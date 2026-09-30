import os
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
import yfinance as yf

# ============================================================
# GOLD FUTURES SMART SIGNAL BOT
# V11.2.3
# M15 LEAD + 5M CONFIRMATION
# COMPLETED CANDLE MODE
# SIGNAL -> PULLBACK -> ACTIVE
# ============================================================

VERSION = "11.2.3"
STATE_VERSION = VERSION
TICKER = "GC=F"
TV_LINK = "https://www.tradingview.com/symbols/GC1!/"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

STATE_FILE = "signal_state.json"
LOG_FILE = "bot_log.json"

MIN_M15_SCORE = 4
STRONG_M15_SCORE = 5
MIN_ADX = 13.0
MAX_SIGNAL_MOVE_ATR = 1.00
COOLDOWN_MINUTES = 60

PULLBACK_MIN_ATR = 0.10
PULLBACK_MAX_ATR = 0.45
PULLBACK_TIMEOUT_MINUTES = 45

ENTRY_RISK_ATR = 1.35
MIN_RISK_ATR = 0.55
MAX_RISK_ATR = 2.50

TP1_R = 1.20
TP2_R = 2.00
TP3_R = 3.00


def now_utc():
    return datetime.now(timezone.utc)


def now_text():
    return now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")


def fmt_price(v):
    return f"${float(v):,.2f}"


def load_json(path, default):
    try:
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def log_event(event, data=None):
    logs = load_json(LOG_FILE, [])
    if not isinstance(logs, list):
        logs = []
    logs.append({
        "time": now_text(),
        "event": event,
        "data": data or {}
    })
    save_json(LOG_FILE, logs[-500:])


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.ok:
            print("Telegram sent successfully.")
            return True
        print("Telegram error:", r.status_code, r.text[:500])
    except Exception as e:
        print("Telegram exception:", e)
    return False


def chart_link_html():
    return f'<a href="{TV_LINK}">📈 TradingView 차트 열기</a>'


# ============================================================
# TELEGRAM ALERTS
# ============================================================

def msg_pending(direction, signal_price, zone_low, zone_high,
                m15_score, five_score, rsi_value, adx_value):
    d = "롱" if direction == "LONG" else "숏"
    return f"""🥇 <b>금 선물 스마트 시그널</b>
━━━━━━━━━━━━━━━━━━━━

🟡 <b>{d} 풀백 진입 대기</b>

📊 방향        : <b>{d}</b>
💰 신호 가격   : <b>{fmt_price(signal_price)}</b>

🎯 <b>진입 대기 구간</b>
{fmt_price(zone_low)} ~ {fmt_price(zone_high)}

━━━━━━━━━━━━━━━━━━━━
📈 <b>신호 분석</b>

M15 신호       : {m15_score} / 6
5M 확인        : {five_score} / 3
RSI            : {rsi_value:.2f}
ADX            : {adx_value:.2f}

━━━━━━━━━━━━━━━━━━━━
⚠️ <b>아직 실제 진입이 아닙니다.</b>

먼저 지정된 풀백 구간에 진입한 후
5분봉 방향을 다시 확인합니다.

✅ 풀백 + 5M 방향 확인
→ 실제 진입 확정

⏳ 현재 상태 : <b>풀백 진입 대기</b>

⚙️ 전략 : M15 주도 + 5M 재확인

{chart_link_html()}"""


def msg_pullback(direction, price, zone_low, zone_high):
    d = "롱" if direction == "LONG" else "숏"
    return f"""🥇 <b>금 선물 스마트 시그널</b>
━━━━━━━━━━━━━━━━━━━━

🟠 <b>풀백 구간 도달</b>

📊 방향        : <b>{d}</b>
💰 현재 가격   : <b>{fmt_price(price)}</b>

🎯 <b>진입 대기 구간</b>
{fmt_price(zone_low)} ~ {fmt_price(zone_high)}

━━━━━━━━━━━━━━━━━━━━
✅ <b>풀백 확인 완료</b>

M15 추세       : {d}
5M 상태        : 재확인 중
현재 상태      : 진입 대기

⚠️ 아직 실제 진입이 아닙니다.

5분봉 방향이 다시 확인되면
실제 진입을 확정합니다.

⏳ <b>진입 확인 대기</b>

⚙️ 전략 : M15 주도 + 5M 재확인

{chart_link_html()}"""


def msg_entry(direction, entry, sl, tp1, tp2, tp3):
    d = "롱" if direction == "LONG" else "숏"
    return f"""🥇 <b>금 선물 스마트 시그널</b>
━━━━━━━━━━━━━━━━━━━━

🔴 <b>{d} 진입 확정</b>

━━━━━━━━━━━━━━━━━━━━
💰 <b>실제 진입가</b>
{fmt_price(entry)}

🛑 <b>손절가</b>
{fmt_price(sl)}

🎯 <b>익절 목표</b>

① TP1   {fmt_price(tp1)}
② TP2   {fmt_price(tp2)}
③ TP3   {fmt_price(tp3)}

━━━━━━━━━━━━━━━━━━━━
📊 <b>진입 확인</b>

M15 추세       : {d}
5M 방향        : 확인 완료
풀백           : 확인 완료
진입 조건      : 충족

━━━━━━━━━━━━━━━━━━━━
⚙️ 전략 : M15 주도 + 5M 재확인

🔴 현재 포지션 관리 중

{chart_link_html()}"""


def msg_tp(direction, level, price, remaining):
    d = "롱" if direction == "LONG" else "숏"
    return f"""🥇 <b>금 선물 스마트 시그널</b>
━━━━━━━━━━━━━━━━━━━━

🎯 <b>{d} {level} 도달</b>

💰 현재 가격 : <b>{fmt_price(price)}</b>

✅ {level} 익절 조건이 충족되었습니다.

잔여 목표 : {remaining}

━━━━━━━━━━━━━━━━━━━━
⚙️ 포지션 관리 중

{chart_link_html()}"""


def msg_tp3(direction, price):
    d = "롱" if direction == "LONG" else "숏"
    return f"""🥇 <b>금 선물 스마트 시그널</b>
━━━━━━━━━━━━━━━━━━━━

🏆 <b>{d} 최종 익절 완료</b>

💰 청산 가격 : <b>{fmt_price(price)}</b>

🎯 TP3까지 도달했습니다.
현재 포지션 관리가 종료되었습니다.

━━━━━━━━━━━━━━━━━━━━
⚪ 새로운 신호를 탐색합니다.

{chart_link_html()}"""


def msg_sl(direction, entry, price, pnl_pct):
    d = "롱" if direction == "LONG" else "숏"
    return f"""🥇 <b>금 선물 스마트 시그널</b>
━━━━━━━━━━━━━━━━━━━━

🛑 <b>{d} 손절가 도달</b>

━━━━━━━━━━━━━━━━━━━━
💰 진입가   : {fmt_price(entry)}
🔻 청산가   : {fmt_price(price)}
📉 손익률   : <b>{pnl_pct:+.2f}%</b>

━━━━━━━━━━━━━━━━━━━━
🛑 손절 처리 완료

⚪ 현재 포지션을 종료하고
새로운 신호를 탐색합니다.

{chart_link_html()}"""


def msg_cancel(direction, signal_price, zone_low, zone_high, reason):
    d = "롱" if direction == "LONG" else "숏"
    return f"""🥇 <b>금 선물 스마트 시그널</b>
━━━━━━━━━━━━━━━━━━━━

⚪ <b>풀백 진입 대기 취소</b>

📊 방향        : <b>{d}</b>
💰 신호 가격   : {fmt_price(signal_price)}

🎯 <b>목표 풀백 구간</b>
{fmt_price(zone_low)} ~ {fmt_price(zone_high)}

━━━━━━━━━━━━━━━━━━━━
⏱️ <b>대기 결과</b>

{reason}

❌ 이번 신호는 폐기되었습니다.
🔎 다음 유효 신호를 탐색합니다.

⚙️ 전략 : M15 주도 + 5M 재확인

{chart_link_html()}"""


# ============================================================
# MARKET DATA
# ============================================================

def download_data(interval, period):
    try:
        df = yf.download(
            TICKER,
            period=period,
            interval=interval,
            auto_adjust=False,
            progress=False,
            threads=False,
        )
        if df is None or df.empty:
            return None

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        for col in ["Open", "High", "Low", "Close"]:
            if col not in df.columns:
                return None

        if "Volume" not in df.columns:
            df["Volume"] = 0.0

        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        return df.dropna(subset=["Open", "High", "Low", "Close"])
    except Exception as e:
        print(f"Download error {interval}: {e}")
        return None


def completed(df):
    if df is None or len(df) < 3:
        return None
    return df.iloc[:-1].copy()


# ============================================================
# INDICATORS
# ============================================================

def ema(series, length):
    return series.ewm(span=length, adjust=False).mean()


def rsi(series, length=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df, length=14):
    prev_close = df["Close"].shift(1)
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev_close).abs(),
        (df["Low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()


def adx(df, length=14):
    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    up = high.diff()
    down = -low.diff()

    plus_dm = pd.Series(
        np.where((up > down) & (up > 0), up, 0.0), index=df.index
    )
    minus_dm = pd.Series(
        np.where((down > up) & (down > 0), down, 0.0), index=df.index
    )

    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)

    atr_s = tr.ewm(alpha=1 / length, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / length, adjust=False).mean() / atr_s
    minus_di = 100 * minus_dm.ewm(alpha=1 / length, adjust=False).mean() / atr_s

    dx = (
        (plus_di - minus_di).abs()
        / (plus_di + minus_di).replace(0, np.nan)
        * 100
    )
    return dx.ewm(alpha=1 / length, adjust=False).mean()


# ============================================================
# SCORING
# ============================================================

def analyze_market(df):
    df = completed(df)
    if df is None or len(df) < 100:
        return None

    close = df["Close"]
    ema20 = ema(close, 20)
    ema50 = ema(close, 50)
    r = rsi(close, 14)
    a = atr(df, 14)
    d = adx(df, 14)

    c = float(close.iloc[-1])
    prev_c = float(close.iloc[-2])
    e20 = float(ema20.iloc[-1])
    e50 = float(ema50.iloc[-1])
    prev_e20 = float(ema20.iloc[-2])
    rr = float(r.iloc[-1])
    aa = float(a.iloc[-1])
    dd = float(d.iloc[-1])

    if not all(np.isfinite(x) for x in [c, prev_c, e20, e50, prev_e20, rr, aa, dd]):
        return None

    long_score = 0
    short_score = 0

    if c > e20:
        long_score += 1
    if c < e20:
        short_score += 1

    if e20 > e50:
        long_score += 1
    if e20 < e50:
        short_score += 1

    if e20 > prev_e20:
        long_score += 1
    if e20 < prev_e20:
        short_score += 1

    if 52 <= rr <= 70:
        long_score += 1
    if 30 <= rr <= 48:
        short_score += 1

    if c > prev_c:
        long_score += 1
    if c < prev_c:
        short_score += 1

    if dd >= MIN_ADX:
        if long_score > short_score:
            long_score += 1
        elif short_score > long_score:
            short_score += 1

    return {
        "close": c,
        "ema20": e20,
        "ema50": e50,
        "rsi": rr,
        "atr": aa,
        "adx": dd,
        "long_score": min(long_score, 6),
        "short_score": min(short_score, 6),
    }


def analyze_5m(df):
    df = completed(df)
    if df is None or len(df) < 60:
        return None

    close = df["Close"]
    e20 = ema(close, 20)
    r = rsi(close, 14)
    a = atr(df, 14)

    c = float(close.iloc[-1])
    prev = float(close.iloc[-2])
    e = float(e20.iloc[-1])
    rr = float(r.iloc[-1])
    aa = float(a.iloc[-1])

    long_score = 0
    short_score = 0

    if c > e:
        long_score += 1
    if c < e:
        short_score += 1

    if c > prev:
        long_score += 1
    if c < prev:
        short_score += 1

    if rr >= 50:
        long_score += 1
    if rr <= 50:
        short_score += 1

    return {
        "close": c,
        "ema20": e,
        "rsi": rr,
        "atr": aa,
        "long_score": min(long_score, 3),
        "short_score": min(short_score, 3),
    }


def h1_context(df):
    result = analyze_market(df)
    if not result:
        return "UNKNOWN"
    if result["long_score"] >= 4 and result["long_score"] > result["short_score"]:
        return "BULL"
    if result["short_score"] >= 4 and result["short_score"] > result["long_score"]:
        return "BEAR"
    return "NEUTRAL"


# ============================================================
# STATE
# ============================================================

def default_state():
    return {
        "version": STATE_VERSION,
        "status": "FLAT",
        "direction": None,
        "signal_price": None,
        "zone_low": None,
        "zone_high": None,
        "signal_time": None,
        "pullback_notified": False,
        "entry": None,
        "sl": None,
        "tp1": None,
        "tp2": None,
        "tp3": None,
        "tp1_sent": False,
        "tp2_sent": False,
        "tp3_sent": False,
        "sl_sent": False,
        "entry_time": None,
        "last_signal_time": None,
        "last_signal_direction": None,
    }


def load_state():
    state = load_json(STATE_FILE, None)
    if not isinstance(state, dict):
        return default_state()

    if state.get("version") != STATE_VERSION:
        print("OLD STATE RESET")
        print("기존 상태를 새 버전 기준으로 초기화합니다.")
        state = default_state()
        save_json(STATE_FILE, state)

    return state


def reset_state(state):
    last_time = state.get("last_signal_time")
    last_dir = state.get("last_signal_direction")

    new_state = default_state()
    new_state["last_signal_time"] = last_time
    new_state["last_signal_direction"] = last_dir
    save_json(STATE_FILE, new_state)
    return new_state


# ============================================================
# SIGNAL FILTER
# ============================================================

def signal_allowed(state, direction):
    last_time = state.get("last_signal_time")
    if not last_time:
        return True

    try:
        last_dt = datetime.fromisoformat(last_time)
        elapsed = (now_utc() - last_dt).total_seconds() / 60
        return elapsed >= COOLDOWN_MINUTES
    except Exception:
        return True


def find_new_signal(state, m15, five, h1):
    if not m15 or not five:
        return None

    long_score = m15["long_score"]
    short_score = m15["short_score"]

    long_valid = (
        long_score >= STRONG_M15_SCORE
        or (long_score >= MIN_M15_SCORE and five["long_score"] >= 2)
    )

    short_valid = (
        short_score >= STRONG_M15_SCORE
        or (short_score >= MIN_M15_SCORE and five["short_score"] >= 2)
    )

    if long_valid and five["short_score"] >= 3 and long_score < STRONG_M15_SCORE:
        long_valid = False

    if short_valid and five["long_score"] >= 3 and short_score < STRONG_M15_SCORE:
        short_valid = False

    if long_valid and short_valid:
        if long_score > short_score:
            short_valid = False
        elif short_score > long_score:
            long_valid = False
        else:
            return None

    if long_valid:
        return "LONG"
    if short_valid:
        return "SHORT"
    return None


# ============================================================
# PENDING
# ============================================================

def create_pending(state, direction, market, five):
    signal_price = market["close"]
    a = market["atr"]

    if direction == "LONG":
        zone_low = signal_price - PULLBACK_MAX_ATR * a
        zone_high = signal_price - PULLBACK_MIN_ATR * a
        m15_score = market["long_score"]
        five_score = five["long_score"]
    else:
        zone_low = signal_price + PULLBACK_MIN_ATR * a
        zone_high = signal_price + PULLBACK_MAX_ATR * a
        m15_score = market["short_score"]
        five_score = five["short_score"]

    state.update({
        "version": STATE_VERSION,
        "status": "PENDING",
        "direction": direction,
        "signal_price": signal_price,
        "zone_low": zone_low,
        "zone_high": zone_high,
        "signal_time": now_utc().isoformat(),
        "pullback_notified": False,
        "entry": None,
        "sl": None,
        "tp1": None,
        "tp2": None,
        "tp3": None,
        "tp1_sent": False,
        "tp2_sent": False,
        "tp3_sent": False,
        "sl_sent": False,
        "entry_time": None,
        "last_signal_time": now_utc().isoformat(),
        "last_signal_direction": direction,
    })

    save_json(STATE_FILE, state)

    telegram_send(msg_pending(
        direction, signal_price, zone_low, zone_high,
        m15_score, five_score, market["rsi"], market["adx"]
    ))

    log_event("PENDING_CREATED", {
        "direction": direction,
        "signal_price": signal_price,
        "zone_low": zone_low,
        "zone_high": zone_high,
    })


def pending_expired(state):
    try:
        signal_dt = datetime.fromisoformat(state["signal_time"])
        elapsed = (now_utc() - signal_dt).total_seconds() / 60
        return elapsed >= PULLBACK_TIMEOUT_MINUTES
    except Exception:
        return True


def monitor_pending(state, current_price, five):
    direction = state["direction"]
    zone_low = float(state["zone_low"])
    zone_high = float(state["zone_high"])
    signal_price = float(state["signal_price"])

    if pending_expired(state):
        telegram_send(msg_cancel(
            direction,
            signal_price,
            zone_low,
            zone_high,
            "정해진 시간 안에 유효한 풀백이 발생하지 않았습니다."
        ))
        log_event("PENDING_TIMEOUT", {"direction": direction})
        reset_state(state)
        return

    in_zone = zone_low <= current_price <= zone_high

    if not state.get("pullback_notified") and in_zone:
        state["pullback_notified"] = True
        save_json(STATE_FILE, state)

        telegram_send(msg_pullback(
            direction, current_price, zone_low, zone_high
        ))

        log_event("PULLBACK_REACHED", {
            "direction": direction,
            "price": current_price,
        })

    if not state.get("pullback_notified"):
        return

    if direction == "LONG":
        confirmed = current_price > five["ema20"] and five["long_score"] >= 2
    else:
        confirmed = current_price < five["ema20"] and five["short_score"] >= 2

    if not confirmed:
        return

    entry = current_price
    risk = five["atr"] * ENTRY_RISK_ATR
    risk = max(five["atr"] * MIN_RISK_ATR, risk)
    risk = min(five["atr"] * MAX_RISK_ATR, risk)

    if direction == "LONG":
        sl = entry - risk
        tp1 = entry + risk * TP1_R
        tp2 = entry + risk * TP2_R
        tp3 = entry + risk * TP3_R
    else:
        sl = entry + risk
        tp1 = entry - risk * TP1_R
        tp2 = entry - risk * TP2_R
        tp3 = entry - risk * TP3_R

    state.update({
        "status": "ACTIVE",
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "entry_time": now_utc().isoformat(),
        "tp1_sent": False,
        "tp2_sent": False,
        "tp3_sent": False,
        "sl_sent": False,
    })

    save_json(STATE_FILE, state)

    telegram_send(msg_entry(
        direction, entry, sl, tp1, tp2, tp3
    ))

    log_event("ENTRY_CONFIRMED", {
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
    })


# ============================================================
# ACTIVE POSITION
# ============================================================

def pnl_percent(direction, entry, price):
    if direction == "LONG":
        return (price - entry) / entry * 100
    return (entry - price) / entry * 100


def monitor_active(state, current_price):
    direction = state["direction"]
    entry = float(state["entry"])
    sl = float(state["sl"])
    tp1 = float(state["tp1"])
    tp2 = float(state["tp2"])
    tp3 = float(state["tp3"])

    if not state.get("sl_sent"):
        hit_sl = current_price <= sl if direction == "LONG" else current_price >= sl

        if hit_sl:
            pnl = pnl_percent(direction, entry, current_price)
            telegram_send(msg_sl(direction, entry, current_price, pnl))
            state["sl_sent"] = True
            save_json(STATE_FILE, state)

            log_event("STOP_LOSS", {
                "direction": direction,
                "entry": entry,
                "exit": current_price,
                "pnl_pct": pnl,
            })
            reset_state(state)
            return

    if not state.get("tp3_sent"):
        hit_tp3 = current_price >= tp3 if direction == "LONG" else current_price <= tp3

        if hit_tp3:
            telegram_send(msg_tp3(direction, current_price))
            state["tp3_sent"] = True
            save_json(STATE_FILE, state)

            log_event("TP3", {
                "direction": direction,
                "price": current_price,
            })
            reset_state(state)
            return

    if not state.get("tp2_sent"):
        hit_tp2 = current_price >= tp2 if direction == "LONG" else current_price <= tp2

        if hit_tp2:
            state["tp2_sent"] = True
            save_json(STATE_FILE, state)
            telegram_send(msg_tp(direction, "TP2", current_price, "TP3"))

            log_event("TP2", {
                "direction": direction,
                "price": current_price,
            })

    if not state.get("tp1_sent"):
        hit_tp1 = current_price >= tp1 if direction == "LONG" else current_price <= tp1

        if hit_tp1:
            state["tp1_sent"] = True
            save_json(STATE_FILE, state)
            telegram_send(msg_tp(direction, "TP1", current_price, "TP2 / TP3"))

            log_event("TP1", {
                "direction": direction,
                "price": current_price,
            })


# ============================================================
# MAIN
# ============================================================

def main():
    print("====================================")
    print(" GOLD FUTURES SMART SIGNAL BOT")
    print(f" V{VERSION}")
    print(" SIGNAL -> PULLBACK -> ACTIVE")
    print(" M15 LEAD + 5M CONFIRMATION")
    print(" H1 CONTEXT ONLY")
    print(" COMPLETED CANDLE MODE")
    print("====================================\n")

    state = load_state()

    print("Downloading market data...")

    h1_df = download_data("1h", "2y")
    m15_df = download_data("15m", "60d")
    five_df = download_data("5m", "30d")
    one_df = download_data("1m", "7d")

    if any(x is None for x in [h1_df, m15_df, five_df, one_df]):
        print("Market data download failed.")
        return

    print(f"1h: {len(h1_df)} candles")
    print(f"15m: {len(m15_df)} candles")
    print(f"5m: {len(five_df)} candles")
    print(f"1m: {len(one_df)} candles\n")

    m15 = analyze_market(m15_df)
    five = analyze_5m(five_df)
    h1 = h1_context(h1_df)

    if not m15 or not five:
        print("Indicator calculation failed.")
        return

    current_price = float(one_df["Close"].iloc[-1])

    print("====================================")
    print(" CURRENT MARKET CHECK")
    print("====================================")
    print(f"M15 Close : {fmt_price(m15['close'])}")
    print(f"EMA20     : {fmt_price(m15['ema20'])}")
    print(f"EMA50     : {fmt_price(m15['ema50'])}")
    print(f"RSI       : {m15['rsi']:.2f}")
    print(f"ADX       : {m15['adx']:.2f}")
    print(f"ATR       : {m15['atr']:.2f}")
    print(f"Current   : {fmt_price(current_price)}\n")

    print("====================================")
    print(" LONG CHECK")
    print("====================================")
    print(f"M15 Score : {m15['long_score']}/6")
    print(f"5M Score  : {five['long_score']}/3")
    print(f"H1        : {'BULL' if h1 == 'BULL' else 'NOT BULL'}\n")

    print("====================================")
    print(" SHORT CHECK")
    print("====================================")
    print(f"M15 Score : {m15['short_score']}/6")
    print(f"5M Score  : {five['short_score']}/3")
    print(f"H1        : {'BEAR' if h1 == 'BEAR' else 'NOT BEAR'}\n")

    if state.get("status") == "ACTIVE":
        print("ACTIVE POSITION")
        print("Direction :", state.get("direction"))
        print("Entry     :", fmt_price(state["entry"]))
        print("SL        :", fmt_price(state["sl"]))
        print("TP1       :", fmt_price(state["tp1"]))
        print("TP2       :", fmt_price(state["tp2"]))
        print("TP3       :", fmt_price(state["tp3"]))
        print("Current   :", fmt_price(current_price))
        monitor_active(state, current_price)
        return

    if state.get("status") == "PENDING":
        print("PENDING PULLBACK")
        print("Direction :", state.get("direction"))
        print("Signal    :", fmt_price(state["signal_price"]))
        print("Zone      :", fmt_price(state["zone_low"]), "~", fmt_price(state["zone_high"]))
        monitor_pending(state, current_price, five)
        return

    print("====================================")
    print(" SIGNAL FILTER")
    print("====================================")

    distance = abs(m15["close"] - m15["ema20"]) / max(m15["atr"], 0.0001)
    print(f"Distance : {distance:.2f} ATR")

    if distance > MAX_SIGNAL_MOVE_ATR:
        print("Distance : FAIL")
        print("No new signal.")
        return

    print("Distance : PASS\n")

    direction = find_new_signal(state, m15, five, h1)

    if not direction:
        print("No valid new signal.")
        return

    if not signal_allowed(state, direction):
        print("Cooldown active. No new signal.")
        return

    print("====================================")
    print(" FRESH SIGNAL FOUND")
    print("====================================")
    print("Direction :", direction)

    create_pending(state, direction, m15, five)

    print("\nNEW PULLBACK SIGNAL")
    print("Direction    :", direction)
    print("Signal Price :", fmt_price(m15["close"]))
    print("Pullback Zone:", fmt_price(state["zone_low"]), "~", fmt_price(state["zone_high"]))
    print("\nWaiting for real pullback...")


if __name__ == "__main__":
    main()
