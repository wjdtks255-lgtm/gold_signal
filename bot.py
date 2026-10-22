from datetime import datetime, timedelta, timezone
import json, os, time, tempfile
from pathlib import Path
from zoneinfo import ZoneInfo
import requests, numpy as np, pandas as pd, yfinance as yf

VERSION = "15.3.1"
TZ = ZoneInfo("Asia/Seoul")
SYMBOL = "GC=F"
TV = "https://www.tradingview.com/symbols/GC1!/"
STATE = Path("signal_state.json")
LOG = Path("bot_log.json")
TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT = (os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID") or "").strip()
TP1_R, TP2_R, TP3_R = 1.2, 2.0, 3.0
COOLDOWN_MIN, SL_COOLDOWN_MIN = 60, 120
MAX_LOGS = 500

def now():
    return datetime.now(TZ)

def stamp():
    return now().isoformat(timespec="seconds")

def write(path, data):
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

def read(path, default):
    if not path.exists():
        return default
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise RuntimeError(f"{path} 읽기 실패: {e}")

def log(event, **kwargs):
    try:
        rows = read(LOG, [])
        if not isinstance(rows, list):
            rows = []
        rows.append({"time": stamp(), "event": event, **kwargs})
        write(LOG, rows[-MAX_LOGS:])
    except Exception as e:
        print(f"LOG WARNING: {e}", flush=True)

def blank():
    return {
        "version": VERSION, "status": "IDLE", "position": None,
        "last_signal_bar": None, "cooldown_until": None,
        "pending_alerts": [], "sent_event_keys": [],
        "stats": {"tp3": 0, "stop_after_tp2": 0, "stop_after_tp1": 0, "stop_before_tp1": 0}
    }

def load_state():
    if not STATE.exists():
        return blank()
    s = read(STATE, None)
    if not isinstance(s, dict) or "status" not in s or "stats" not in s:
        raise RuntimeError("상태 파일 구조 오류. 자동 초기화하지 않습니다.")
    s.setdefault("pending_alerts", [])
    s.setdefault("sent_event_keys", [])
    s.setdefault("position", None)
    s.setdefault("cooldown_until", None)
    s.setdefault("last_signal_bar", None)
    for k in ("tp3", "stop_after_tp2", "stop_after_tp1", "stop_before_tp1"):
        s["stats"].setdefault(k, 0)
    return s

def save(s):
    s["version"] = VERSION
    write(STATE, s)

def telegram(message):
    if not TOKEN or not CHAT:
        raise RuntimeError("TELEGRAM_TOKEN 또는 TELEGRAM_CHAT_ID가 없습니다.")
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT, "text": message, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=20
    )
    r.raise_for_status()
    result = r.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram 오류: {result}")
    
    # 💡 연속 알림 폭탄 및 텔레그램 플러드 방지를 위한 부드러운 전송 딜레이
    time.sleep(1.2)
    return True

def queue(s, event_key, message, event_type):
    if event_key in s["sent_event_keys"]:
        return
    if any(x.get("key") == event_key for x in s["pending_alerts"]):
        return
    s["pending_alerts"].append({
        "key": event_key, "message": message, "type": event_type
    })
    log("alert_queued", key=event_key, alert_type=event_type)
    save(s)

def flush(s):
    while s["pending_alerts"]:
        item = s["pending_alerts"][0]
        try:
            telegram(item["message"])
        except Exception as e:
            log("telegram_error", error=str(e), key=item.get("key"))
            print(f"TELEGRAM ERROR: {e}", flush=True)
            save(s)
            return
        s["sent_event_keys"].append(item["key"])
        s["sent_event_keys"] = s["sent_event_keys"][-1000:]
        s["pending_alerts"].pop(0)
        log("alert_sent", key=item["key"], alert_type=item.get("type"))
        save(s)

def stats_message(s):
    st = s["stats"]
    total = sum(st.get(k, 0) for k in (
        "tp3", "stop_after_tp2", "stop_after_tp1", "stop_before_tp1"
    ))
    rate = 100 * st.get("tp3", 0) / total if total else 0
    return (
        "📊 <b>GOLD 시그널 종료 통계</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"총 종료 시그널: <b>{total}</b>\n"
        f"TP3 최종 도달: {st.get('tp3', 0)}\n"
        f"TP2 도달 후 스탑 종료: {st.get('stop_after_tp2', 0)}\n"
        f"TP1 도달 후 스탑 종료: {st.get('stop_after_tp1', 0)}\n"
        f"TP1 전 손절: {st.get('stop_before_tp1', 0)}\n"
        f"TP3 기준 승률: <b>{rate:.1f}%</b>\n"
        "━━━━━━━━━━━━━━\n"
        "<i>승률 = TP3 종료 ÷ 전체 종료 시그널</i>"
    )

def get_bars(interval, period):
    d = yf.download(
        SYMBOL, interval=interval, period=period,
        auto_adjust=False, progress=False, threads=False
    )
    if d is None or d.empty:
        raise RuntimeError(f"Yahoo Finance 데이터 없음: {interval}")
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d.columns = [str(c).lower() for c in d.columns]
    cols = ["open", "high", "low", "close", "volume"]
    if any(c not in d.columns for c in cols):
        raise RuntimeError(f"{interval} 데이터 열 부족")
    d = d[cols].dropna()
    if len(d) < 60:
        raise RuntimeError(f"{interval} 데이터 부족: {len(d)}")
    return d.iloc[:-1].copy()

def indicators(d):
    d = d.copy()
    c, h, l = d.close, d.high, d.low
    d["ema20"] = c.ewm(span=20, adjust=False).mean()
    d["ema50"] = c.ewm(span=50, adjust=False).mean()
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    d["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1/14, adjust=False).mean()
    up, down = h.diff(), -l.diff()
    plus = pd.Series(np.where((up > down) & (up > 0), up, 0), index=d.index)
    minus = pd.Series(np.where((down > up) & (down > 0), down, 0), index=d.index)
    atr = d["atr"].replace(0, np.nan)
    pdi = 100 * plus.ewm(alpha=1/14, adjust=False).mean() / atr
    mdi = 100 * minus.ewm(alpha=1/14, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    d["pdi"], d["mdi"] = pdi, mdi
    d["adx"] = dx.ewm(alpha=1/14, adjust=False).mean()
    d["body"] = ((c - d.open).abs() / (h - l).replace(0, np.nan)).fillna(0)
    d["closepos"] = ((c - l) / (h - l).replace(0, np.nan)).fillna(.5)
    return d.replace([np.inf, -np.inf], np.nan).dropna()

def get_signal():
    h1 = indicators(get_bars("1h", "60d"))
    m15 = indicators(get_bars("15m", "30d"))
    m5 = indicators(get_bars("5m", "10d"))
    h, m, b = h1.iloc[-1], m15.iloc[-1], m5.iloc[-1]
    prev = m15.iloc[-2]
    price = float(b.close)
    atr = float(b.atr)
    if not np.isfinite(atr) or atr <= 0:
        raise RuntimeError("ATR 값 오류")

    long_score = short_score = 0
    long_score += 2 if h.ema20 > h.ema50 else 0
    short_score += 2 if h.ema20 < h.ema50 else 0
    long_score += 2 if m.ema20 > m.ema50 else 0
    short_score += 2 if m.ema20 < m.ema50 else 0
    long_score += 1 if m.close > m.ema20 and prev.close <= prev.ema20 * 1.003 else 0
    short_score += 1 if m.close < m.ema20 and prev.close >= prev.ema20 * .997 else 0
    long_score += 1 if b.close > b.open and b.closepos > .65 else 0
    short_score += 1 if b.close < b.open and b.closepos < .35 else 0
    long_score += 1 if b.pdi > b.mdi else 0
    short_score += 1 if b.mdi > b.pdi else 0
    long_score += 1 if 38 <= b.rsi <= 68 else 0
    short_score += 1 if 30 <= b.rsi <= 62 else 0

    direction = None
    if long_score >= 6 and long_score > short_score and h.ema20 > h.ema50:
        direction = "LONG"
    elif short_score >= 6 and short_score > long_score and h.ema20 < h.ema50:
        direction = "SHORT"
    info = {
        "price": price, "atr": atr, "rsi": float(b.rsi),
        "adx": float(b.adx), "long_score": int(long_score),
        "short_score": int(short_score),
        "score": int(max(long_score, short_score)),
        "bar": str(m5.index[-1])
    }
    if direction and (b.adx < 14 or abs(price - float(b.ema20)) > 1.5 * atr):
        direction = None
    return direction, info

def entry_message(p, info):
    side = "🟢 LONG 매수 관점" if p["side"] == "LONG" else "🔴 SHORT 매도 관점"
    return (
        "🥇 <b>GOLD FUTURES SIGNAL V15.3.1</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"{side}\n"
        f"진입 기준가: <b>{p['entry']:.2f}</b>\n"
        f"손절가: <b>{p['sl']:.2f}</b>\n"
        f"TP1: <b>{p['tp1']:.2f}</b>\n"
        f"TP2: <b>{p['tp2']:.2f}</b>\n"
        f"TP3: <b>{p['tp3']:.2f}</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"신호 점수: {info['score']} | RSI: {info['rsi']:.1f}\n"
        f"ADX: {info['adx']:.1f} | ATR: {info['atr']:.2f}\n"
        f"발생 시각: {stamp()}\n"
        f'차트: <a href="{TV}">TradingView Gold</a>\n'
        "⚠️ 가격은 데이터 제공처와 체결 가격에 따라 다를 수 있습니다."
    )

def close_position(s, p, reason, price):
    st = s["stats"]
    if reason == "TP3":
        st["tp3"] += 1
        label = "🎯 TP3 최종 목표 도달"
    elif p["tp2_hit"]:
        st["stop_after_tp2"] += 1
        label = "🛡 TP2 도달 후 보호 스탑 종료"
    elif p["tp1_hit"]:
        st["stop_after_tp1"] += 1
        label = "🛡 TP1 도달 후 보호 스탑 종료"
    else:
        st["stop_before_tp1"] += 1
        label = "🛑 TP1 도달 전 손절"
    msg = (
        "🏁 <b>GOLD SIGNAL 종료</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"{label}\n방향: {p['side']}\n"
        f"기준 진입가: {p['entry']:.2f}\n"
        f"종료 기준가: {price:.2f}\n"
        f"종료 사유: {reason}\n시각: {stamp()}"
    )
    s["status"] = "IDLE"
    s["position"] = None
    mins = SL_COOLDOWN_MIN if reason == "SL" else COOLDOWN_MIN
    s["cooldown_until"] = (now() + timedelta(minutes=mins)).isoformat()
    queue(s, f"EXIT-{p['id']}", msg, "EXIT")
    queue(s, f"STATS-{p['id']}", stats_message(s), "STATS")
    log("position_closed", reason=reason, side=p["side"], stats=st.copy())
    save(s)

def monitor(s):
    p = s.get("position")
    if not p:
        return
    d = get_bars("5m", "10d")
    bar = d.iloc[-1]
    hi, lo = float(bar.high), float(bar.low)
    side = p["side"]
    sl_hit = lo <= p["sl"] if side == "LONG" else hi >= p["sl"]
    if sl_hit:
        close_position(s, p, "SL", p["sl"])
        return

    for n in (1, 2, 3):
        flag = f"tp{n}_hit"
        target = p[f"tp{n}"]
        hit = hi >= target if side == "LONG" else lo <= target
        if not p[flag] and hit:
            p[flag] = True
            queue(s, f"TP{n}-{p['id']}",
                f"🎯 <b>GOLD TP{n} 도달</b>\n방향: {side}\n목표가: <b>{target:.2f}</b>\n"
                "시그널은 아직 종료되지 않았습니다.", f"TP{n}")
            if n in (1, 2):
                p["sl"] = max(p["sl"], p["entry"]) if side == "LONG" else min(p["sl"], p["entry"])
            if n == 3:
                save(s)
                close_position(s, p, "TP3", target)
                return
    save(s)

def main():
    print(f"GOLD SMART SIGNAL BOT V{VERSION} | {stamp()}", flush=True)
    s = load_state()
    flush(s)
    if s["pending_alerts"]:
        print("알림 대기 중: Telegram 설정/네트워크 확인 필요")
        return
    if s["status"] == "ACTIVE" and s.get("position"):
        monitor(s)
        flush(s)
        return
    if s.get("cooldown_until"):
        try:
            if now() < datetime.fromisoformat(s["cooldown_until"]):
                print("Cooldown active", flush=True)
                return
        except ValueError:
            log("invalid_cooldown", value=s["cooldown_until"])

    direction, info = get_signal()
    print(
        f"Price={info['price']:.2f} RSI={info['rsi']:.1f} "
        f"ATR={info['atr']:.2f} ADX={info['adx']:.1f}", flush=True
    )
    print(f"Signal: {direction} scores: LONG={info['long_score']} SHORT={info['short_score']}", flush=True)
    if not direction:
        log("no_signal", **info)
        save(s)
        return
    if s.get("last_signal_bar") == info["bar"]:
        print("Duplicate signal bar ignored", flush=True)
        return

    entry = info["price"]
    risk = info["atr"] * 1.35
    sign = 1 if direction == "LONG" else -1
    p = {
        "id": str(int(time.time())), "side": direction, "entry": entry,
        "sl": entry - sign * risk,
        "tp1": entry + sign * risk * TP1_R,
        "tp2": entry + sign * risk * TP2_R,
        "tp3": entry + sign * risk * TP3_R,
        "tp1_hit": False, "tp2_hit": False, "tp3_hit": False,
        "entry_time": stamp(), "entry_bar": info["bar"]
    }
    s["status"] = "ACTIVE"
    s["position"] = p
    s["last_signal_bar"] = info["bar"]
    save(s)
    queue(s, f"ENTRY-{p['id']}", entry_message(p, info), "ENTRY")
    flush(s)
    print("Entry state saved; alert queue processed.", flush=True)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL ERROR: {type(e).__name__}: {e}", flush=True)
        log("fatal_error", error=f"{type(e).__name__}: {e}")
        raise
