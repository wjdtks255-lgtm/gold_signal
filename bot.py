import os
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
import yfinance as yf

# ============================================================
# GOLD FUTURES SMART SIGNAL BOT (ULTIMATE ROBUST VERSION)
# V13.0 - REALTIME PRICE ANCHORING & EDGE-CASE HARDENING
# ============================================================

VERSION = "13.0.0"
STATE_VERSION = VERSION
TICKER = "GC=F"
TV_LINK = "https://www.tradingview.com/symbols/GC1!/"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

STATE_FILE = "signal_state.json"
LOG_FILE = "bot_log.json"

MIN_M15_SCORE = 5        
STRONG_M15_SCORE = 6     
MIN_ADX = 15.0           
COOLDOWN_MINUTES = 45    
SL_COOLDOWN_MINUTES = 120  # 손절 후 동일 방향 재진입 차단 (2시간)

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
    try:
        return f"${float(v):,.2f}"
    except Exception:
        return "$0.00"


def load_json(path, default):
    try:
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, type(default)) else default
    except Exception:
        return default


def save_json(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        print(f"Save JSON error: {e}")


def log_event(event, data=None):
    try:
        logs = load_json(LOG_FILE, [])
        if not isinstance(logs, list):
            logs = []
        logs.append({
            "time": now_text(),
            "event": event,
            "data": data or {}
        })
        save_json(LOG_FILE, logs[-500:])
    except Exception as e:
        print(f"Log event error: {e}")


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


def msg_entry(direction, entry, sl, tp1, tp2, tp3, m15_score, five_score, rsi_value, adx_value):
    d = "롱" if direction == "LONG" else "숏"
    return f"""🥇 <b>금 선물 스마트 시그널 (실시간 앵커링 V13)</b>
━━━━━━━━━━━━━━━━━━━━

🔴 <b>{d} 포지션 즉시 실행</b>

━━━━━━━━━━━━━━━━━━━━
💰 <b>진입가 (실시간현재가)</b> : <b>{fmt_price(entry)}</b>
🛑 <b>손절가 (SL)</b>          : <b>{fmt_price(sl)}</b>

🎯 <b>익절 목표 (TP)</b>
① TP1   {fmt_price(tp1)}
② TP2   {fmt_price(tp2)}
③ TP3   {fmt_price(tp3)}

━━━━━━━━━━━━━━━━━━━━
📊 <b>정밀 분석 리포트</b>

M15 신호 점수  : {m15_score} / 6
5M 확인 점수   : {five_score} / 3
RSI            : {rsi_value:.2f}
ADX (추세강도) : {adx_value:.2f}

━━━━━━━━━━━━━━━━━━━━
⚙️ 전략 : 실시간 앵커링 + 휩소 방어 가드
🔴 현재 포지션 실시간 관리 중

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

🏆 <b>{d} 최종 익절 완료 (TP3)</b>

💰 청산 가격 : <b>{fmt_price(price)}</b>

🎯 목표가 완주! 포지션 관리가 종료되었습니다.
━━━━━━━━━━━━━━━━━━━━
⚪ 새로운 정밀 신호를 탐색합니다.

{chart_link_html()}"""


def msg_sl(direction, entry, price, pnl_pct):
    d = "롱" if direction == "LONG" else "숏"
    return f"""🥇 <b>금 선물 스마트 시그널</b>
━━━━━━━━━━━━━━━━━━━━

🛑 <b>{d} 손절가 도달 (SL)</b>

━━━━━━━━━━━━━━━━━━━━
💰 진입가   : {fmt_price(entry)}
🔻 청산가   : {fmt_price(price)}
📉 손익률   : <b>{pnl_pct:+.2f}%</b>

━━━━━━━━━━━━━━━━━━━━
🛑 손절 처리 완료. 동일 방향 휩소 방지를 위해 2시간 동안 재진입이 제한됩니다.

{chart_link_html()}"""


# ============================================================
# MARKET DATA & INDICATORS
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

        return df[["Open", "High", "Low", "Close", "Volume"]].copy().dropna()
    except Exception as e:
        print(f"Download error {interval}: {e}")
        return None


def completed(df):
    if df is None or len(df) < 3:
        return None
    return df.iloc[:-1].copy()


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

    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)

    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)

    atr_s = tr.ewm(alpha=1 / length, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / length, adjust=False).mean() / atr_s
    minus_di = 100 * minus_dm.ewm(alpha=1 / length, adjust=False).mean() / atr_s

    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100
    return dx.ewm(alpha=1 / length, adjust=False).mean()


def analyze_market(df):
    df_comp = completed(df)
    if df_comp is None or len(df_comp) < 100:
        return None

    close = df_comp["Close"]
    ema20 = ema(close, 20)
    ema50 = ema(close, 50)
    r = rsi(close, 14)
    a = atr(df_comp, 14)
    d = adx(df_comp, 14)

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

    if c > e20: long_score += 1
    if c < e20: short_score += 1
    if e20 > e50: long_score += 1
    if e20 < e50: short_score += 1
    if e20 > prev_e20: long_score += 1
    if e20 < prev_e20: short_score += 1
    if rr >= 50: long_score += 1
    if rr <= 50: short_score += 1
    if c > prev_c: long_score += 1
    if c < prev_c: short_score += 1

    if dd >= MIN_ADX:
        if long_score > short_score: long_score += 1
        elif short_score > long_score: short_score += 1

    return {
        "close": c, "ema20": e20, "ema50": e50,
        "rsi": rr, "atr": aa, "adx": dd,
        "long_score": min(long_score, 6),
        "short_score": min(short_score, 6),
    }


def analyze_5m(df):
    df_comp = completed(df)
    if df_comp is None or len(df_comp) < 60:
        return None

    close = df_comp["Close"]
    e20 = ema(close, 20)
    r = rsi(close, 14)
    a = atr(df_comp, 14)

    c = float(close.iloc[-1])
    prev = float(close.iloc[-2])
    e = float(e20.iloc[-1])
    rr = float(r.iloc[-1])
    aa = float(a.iloc[-1])

    long_score = 0
    short_score = 0

    if c > e: long_score += 1
    if c < e: short_score += 1
    if c > prev: long_score += 1
    if c < prev: short_score += 1
    if rr >= 50: long_score += 1
    if rr <= 50: short_score += 1

    return {
        "close": c, "ema20": e, "rsi": rr, "atr": aa,
        "long_score": min(long_score, 3),
        "short_score": min(short_score, 3),
    }


# ============================================================
# STATE MANAGEMENT
# ============================================================

def default_state():
    return {
        "version": STATE_VERSION,
        "status": "FLAT",
        "direction": None,
        "entry": None,
        "sl": None,
        "tp1": None,
        "tp2": None,
        "tp3": None,
        "tp1_sent": False,
        "tp2_sent": False,
        "tp3_sent": False,
        "sl_sent": False,
        "last_signal_time": None,
        "last_signal_direction": None,
        "last_sl_time": None,
        "last_sl_direction": None,
    }


def load_state():
    state = load_json(STATE_FILE, None)
    if not isinstance(state, dict) or state.get("version") != STATE_VERSION:
        state = default_state()
        save_json(STATE_FILE, state)
    return state


def reset_state_on_sl(state, direction):
    new_state = default_state()
    new_state["last_signal_time"] = state.get("last_signal_time")
    new_state["last_signal_direction"] = state.get("last_signal_direction")
    new_state["last_sl_time"] = now_utc().isoformat()
    new_state["last_sl_direction"] = direction
    save_json(STATE_FILE, new_state)
    return new_state


def reset_state_normal(state):
    new_state = default_state()
    new_state["last_signal_time"] = state.get("last_signal_time")
    new_state["last_signal_direction"] = state.get("last_signal_direction")
    new_state["last_sl_time"] = state.get("last_sl_time")
    new_state["last_sl_direction"] = state.get("last_sl_direction")
    save_json(STATE_FILE, new_state)
    return new_state


def signal_allowed(state, direction):
    last_time = state.get("last_signal_time")
    if last_time:
        try:
            last_dt = datetime.fromisoformat(last_time)
            elapsed = (now_utc() - last_dt).total_seconds() / 60
            if elapsed < COOLDOWN_MINUTES:
                return False
        except Exception:
            pass

    last_sl_time = state.get("last_sl_time")
    last_sl_dir = state.get("last_sl_direction")
    if last_sl_time and last_sl_dir == direction:
        try:
            sl_dt = datetime.fromisoformat(last_sl_time)
            sl_elapsed = (now_utc() - sl_dt).total_seconds() / 60
            if sl_elapsed < SL_COOLDOWN_MINUTES:
                print(f"SL Lock active for {direction}. Elapsed: {sl_elapsed:.1f}m / {SL_COOLDOWN_MINUTES}m")
                return False
        except Exception:
            pass

    return True


def find_new_signal(m15, five):
    if not m15 or not five: return None
    long_score = m15["long_score"]
    short_score = m15["short_score"]

    long_valid = long_score >= STRONG_M15_SCORE or (long_score >= MIN_M15_SCORE and five["long_score"] >= 2)
    short_valid = short_score >= STRONG_M15_SCORE or (short_score >= MIN_M15_SCORE and five["short_score"] >= 2)

    if long_valid and short_valid:
        if long_score > short_score: short_valid = False
        elif short_score > long_score: long_valid = False
        else: return None

    if long_valid: return "LONG"
    if short_valid: return "SHORT"
    return None


# ============================================================
# ACTIVE EXECUTION (REALTIME PRICE ANCHORING)
# ============================================================

def execute_immediate_entry(state, direction, market, five, current_price):
    # 🎯 실시간 현재가(Current Price)를 진입가로 직접 앵커링
    entry = float(current_price)
    risk = max(five["atr"] * MIN_RISK_ATR, min(five["atr"] * ENTRY_RISK_ATR, five["atr"] * MAX_RISK_ATR))

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
        "version": STATE_VERSION,
        "status": "ACTIVE",
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "tp1_sent": False,
        "tp2_sent": False,
        "tp3_sent": False,
        "sl_sent": False,
        "last_signal_time": now_utc().isoformat(),
        "last_signal_direction": direction,
    })

    save_json(STATE_FILE, state)

    telegram_send(msg_entry(
        direction, entry, sl, tp1, tp2, tp3,
        market["long_score"] if direction == "LONG" else market["short_score"],
        five["long_score"] if direction == "LONG" else five["short_score"],
        market["rsi"], market["adx"]
    ))

    log_event("IMMEDIATE_ENTRY", {"direction": direction, "entry": entry, "sl": sl})


def monitor_active(state, current_price):
    direction = state["direction"]
    entry = float(state["entry"])
    sl = float(state["sl"])
    tp1 = float(state["tp1"])
    tp2 = float(state["tp2"])
    tp3 = float(state["tp3"])

    # 🛑 1. 손절(SL) 여부 최우선 검증 (슬리피지 및 갭 하락 대비)
    if not state.get("sl_sent"):
        hit_sl = current_price <= sl if direction == "LONG" else current_price >= sl
        if hit_sl:
            pnl = (current_price - entry) / entry * 100 if direction == "LONG" else (entry - current_price) / entry * 100
            telegram_send(msg_sl(direction, entry, current_price, pnl))
            log_event("STOP_LOSS", {"exit": current_price, "pnl_pct": pnl})
            reset_state_on_sl(state, direction)
            return

    # 🏆 2. 최종 익절(TP3) 검증
    if not state.get("tp3_sent"):
        hit_tp3 = current_price >= tp3 if direction == "LONG" else current_price <= tp3
        if hit_tp3:
            telegram_send(msg_tp3(direction, current_price))
            log_event("TP3", {"price": current_price})
            reset_state_normal(state)
            return

    # 🎯 3. 중간 익절(TP2) 검증
    if not state.get("tp2_sent"):
        hit_tp2 = current_price >= tp2 if direction == "LONG" else current_price <= tp2
        if hit_tp2:
            state["tp2_sent"] = True
            save_json(STATE_FILE, state)
            telegram_send(msg_tp(direction, "TP2", current_price, "TP3"))
            log_event("TP2", {"price": current_price})

    # 🎯 4. 1차 익절(TP1) 검증
    if not state.get("tp1_sent"):
        hit_tp1 = current_price >= tp1 if direction == "LONG" else current_price <= tp1
        if hit_tp1:
            state["tp1_sent"] = True
            save_json(STATE_FILE, state)
            telegram_send(msg_tp(direction, "TP1", current_price, "TP2 / TP3"))
            log_event("TP1", {"price": current_price})


# ============================================================
# MAIN
# ============================================================

def main():
    print("====================================")
    print(" GOLD SMART SIGNAL (ROBUST V13)")
    print(f" V{VERSION}")
    print("====================================\n")

    state = load_state()

    h1_df = download_data("1h", "2y")
    m15_df = download_data("15m", "60d")
    five_df = download_data("5m", "30d")
    one_df = download_data("1m", "7d")

    if any(x is None for x in [h1_df, m15_df, five_df, one_df]):
        print("Market data download failed.")
        return

    m15 = analyze_market(m15_df)
    five = analyze_5m(five_df)

    if not m15 or not five:
        print("Indicator calculation failed.")
        return

    current_price = float(one_df["Close"].iloc[-1])

    print(f"Current Price : {fmt_price(current_price)}")
    print(f"Current Status: {state.get('status')}\n")

    if state.get("status") == "ACTIVE":
        print("Monitoring active position with realtime price...")
        monitor_active(state, current_price)
        return

    direction = find_new_signal(m15, five)

    if not direction:
        print("No valid signal found (Filtered out).")
        return

    if not signal_allowed(state, direction):
        print("Signal blocked by Cooldown or SL Lock.")
        return

    print(f"VALID SIGNAL FOUND: {direction} -> Executing Immediately at {fmt_price(current_price)}!")
    execute_immediate_entry(state, direction, m15, five, current_price)


if __name__ == "__main__":
    main()
