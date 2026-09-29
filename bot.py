import os
import json
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta


# ============================================================
# GOLD FUTURES SMART SIGNAL BOT
# Scoring & Flexible Condition Version (KST & Korean Localized)
# ============================================================

TICKER = "GC=F"

STATE_FILE = "signal_state.json"
LOG_FILE = "bot_log.json"

# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ============================================================
# STRATEGY SETTINGS (점수제 및 유연한 기준)
# ============================================================

# Take Profit
TP1_R = 1.20
TP2_R = 2.00
TP3_R = 3.00

# Stop Loss
MAX_SL_ATR = 2.50

# Entry distance
MAX_ENTRY_DISTANCE_ATR = 1.50

# Signal movement filter
MAX_SIGNAL_MOVE_ATR = 0.80

# Cooldown after position closes
COOLDOWN_MINUTES = 60


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials are missing.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:
        response = requests.post(url, json=payload, timeout=20)
        if response.status_code == 200:
            print("Telegram message sent.")
            return True
        print("Telegram error:", response.text)
    except Exception as e:
        print("Telegram exception:", e)

    return False


# ============================================================
# TIME & STATE & LOG
# ============================================================

def normalize_timestamp(value):
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts

def format_kst_time(iso_str):
    """UTC ISO 문자열을 받아 KST(한국 시간) 문자열로 변환합니다."""
    try:
        dt_utc = datetime.fromisoformat(iso_str)
        kst_zone = timezone(timedelta(hours=9))
        dt_kst = dt_utc.astimezone(kst_zone)
        return dt_kst.strftime("%Y-%m-%d %H:%M:%S (KST)")
    except Exception:
        return iso_str

def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except Exception as e:
        print("State load error:", e)
        return {}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def log_event(event_type, data=None):
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except Exception:
            logs = []
    else:
        logs = []

    if not isinstance(logs, list):
        logs = []

    logs.append({
        "time": pd.Timestamp.now(tz="UTC").isoformat(),
        "event": event_type,
        "data": data or {}
    })
    logs = logs[-500:]

    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)


# ============================================================
# DATA CLEANING & INDICATORS
# ============================================================

def clean_dataframe(df):
    if df is None or df.empty:
        return None
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]

    required = ["Open", "High", "Low", "Close"]
    for col in required:
        if col not in df.columns:
            return None

    df = df[required].dropna()
    try:
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
    except Exception:
        pass
    return df

def download_data():
    print("Downloading market data...")
    try:
        h1 = clean_dataframe(yf.download(TICKER, period="30d", interval="1h", progress=False, auto_adjust=False))
        m15 = clean_dataframe(yf.download(TICKER, period="10d", interval="15m", progress=False, auto_adjust=False))
        m5 = clean_dataframe(yf.download(TICKER, period="5d", interval="5m", progress=False, auto_adjust=False))
        m1 = clean_dataframe(yf.download(TICKER, period="5d", interval="1m", progress=False, auto_adjust=False))

        if h1 is None or m15 is None or m5 is None or m1 is None:
            return None

        if len(h1) > 2: h1 = h1.iloc[:-1]
        if len(m15) > 2: m15 = m15.iloc[:-1]
        if len(m5) > 2: m5 = m5.iloc[:-1]
        if len(m1) > 2: m1 = m1.iloc[:-1]

        return h1, m15, m5, m1
    except Exception as e:
        print("Download error:", e)
        return None

def add_indicators(df):
    df = df.copy()
    close, high, low = df["Close"], df["High"], df["Low"]

    df["EMA20"] = close.ewm(span=20, adjust=False).mean()
    df["EMA50"] = close.ewm(span=50, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["RSI"] = 100 - (100 / (1 + rs))

    previous_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - previous_close).abs()
    tr3 = (low - previous_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["ATR"] = tr.ewm(alpha=1/14, adjust=False).mean()

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
    atr_for_adx = tr.ewm(alpha=1/14, adjust=False).mean()

    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1/14, adjust=False).mean() / atr_for_adx
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1/14, adjust=False).mean() / atr_for_adx
    denominator = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / denominator
    df["ADX"] = dx.ewm(alpha=1/14, adjust=False).mean()

    candle_range = (high - low).replace(0, np.nan)
    df["BodyRatio"] = (close - df["Open"]).abs() / candle_range
    df["ClosePosition"] = (close - low) / candle_range

    return df


# ============================================================
# FIND SIGNAL (점수제 적용 로직)
# ============================================================

def find_signal(h1, m15, m5):
    h1 = add_indicators(h1)
    m15 = add_indicators(m15)
    m5 = add_indicators(m5)

    if len(h1) < 60 or len(m15) < 60 or len(m5) < 30:
        print("Not enough data.")
        return None

    h = h1.iloc[-1]
    p = m15.iloc[-2]
    c = m15.iloc[-1]
    latest_5m = m5.iloc[-1]

    price = float(c["Close"])
    ema20 = float(c["EMA20"])
    rsi = float(c["RSI"])
    adx = float(c["ADX"])
    atr = float(c["ATR"])

    print("")
    print("====================================")
    print(" CURRENT MARKET CHECK (SCORING)")
    print("====================================")
    print(f"Price : ${price:,.2f}")
    print(f"EMA20 : ${ema20:,.2f}")
    print(f"RSI   : {rsi:.2f}")
    print(f"ADX   : {adx:.2f}")
    print(f"ATR   : {atr:.2f}")

    # --------------------------------------------------------
    # LONG 점수 산정 (필수 2개 + 보조 중 3개 이상 만족 시 신호)
    # --------------------------------------------------------
    h1_bull_base = (h["Close"] > h["EMA20"] or h["EMA20"] >= h["EMA50"])
    long_distance = abs(price - ema20) <= atr * MAX_ENTRY_DISTANCE_ATR

    long_score = 0
    long_reversal = (c["Close"] > c["Open"] and c["BodyRatio"] >= 0.30 and c["ClosePosition"] >= 0.55)
    long_breakout = (c["Close"] > p["High"])
    long_rsi = (40 <= rsi <= 75)
    long_adx = (adx >= 12)
    long_ema_hold = (c["Close"] >= ema20 - atr * 0.50)

    if long_reversal: long_score += 1
    if long_breakout: long_score += 1
    if long_rsi: long_score += 1
    if long_adx: long_score += 1
    if long_ema_hold: long_score += 1

    long_condition = h1_bull_base and long_distance and (long_score >= 3)

    # --------------------------------------------------------
    # SHORT 점수 산정 (필수 2개 + 보조 중 3개 이상 만족 시 신호)
    # --------------------------------------------------------
    h1_bear_base = (h["Close"] < h["EMA20"] or h["EMA20"] <= h["EMA50"])
    short_distance = abs(price - ema20) <= atr * MAX_ENTRY_DISTANCE_ATR

    short_score = 0
    short_reversal = (c["Close"] < c["Open"] and c["BodyRatio"] >= 0.30 and c["ClosePosition"] <= 0.45)
    short_breakdown = (c["Close"] < p["Low"])
    short_rsi = (25 <= rsi <= 60)
    short_adx = (adx >= 12)
    short_ema_hold = (c["Close"] <= ema20 + atr * 0.50)

    if short_reversal: short_score += 1
    if short_breakdown: short_score += 1
    if short_rsi: short_score += 1
    if short_adx: short_score += 1
    if short_ema_hold: short_score += 1

    short_condition = h1_bear_base and short_distance and (short_score >= 3)

    print(f"LONG Score  : {long_score} / 5 (Pass: {long_condition})")
    print(f"SHORT Score : {short_score} / 5 (Pass: {short_condition})")

    if not long_condition and not short_condition:
        print("\n====================================")
        print(" NO VALID SIGNAL (Score < 3)")
        print("====================================")
        return None

    # ========================================================
    # ENTRY & RISK
    # ========================================================
    if long_condition and (long_score >= short_score):
        direction = "LONG"
        entry = price
        swing_low = float(m15["Low"].iloc[-4:-1].min())
        sl = swing_low - atr * 0.25
        reason = f"1시간 상승 추세 + 15분봉 점수 {long_score}/5 (RSI: {rsi:.1f}, ADX: {adx:.1f})"
    else:
        direction = "SHORT"
        entry = price
        swing_high = float(m15["High"].iloc[-4:-1].max())
        sl = swing_high + atr * 0.25
        reason = f"1시간 하락 추세 + 15분봉 점수 {short_score}/5 (RSI: {rsi:.1f}, ADX: {adx:.1f})"

    risk = abs(entry - sl)
    if risk <= 0 or risk > atr * MAX_SL_ATR:
        print("Signal rejected: Invalid or too large risk.")
        return None

    current_5m_price = float(latest_5m["Close"])
    if abs(current_5m_price - entry) > atr * MAX_SIGNAL_MOVE_ATR:
        print("Signal rejected: Price moved too far from signal.")
        return None

    if direction == "LONG":
        tp1 = entry + risk * TP1_R
        tp2 = entry + risk * TP2_R
        tp3 = entry + risk * TP3_R
    else:
        tp1 = entry - risk * TP1_R
        tp2 = entry - risk * TP2_R
        tp3 = entry - risk * TP3_R

    signal_time = normalize_timestamp(m15.index[-1])
    entry_time = signal_time + pd.Timedelta(minutes=15)

    signal = {
        "status": "active",
        "direction": direction,
        "entry": float(entry),
        "sl": float(sl),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "tp3": float(tp3),
        "risk": float(risk),
        "rsi": float(rsi),
        "adx": float(adx),
        "atr": float(atr),
        "reason": reason,
        "signal_time": signal_time.isoformat(),
        "entry_time": entry_time.isoformat(),
        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,
        "entry_alert_sent": False,
        "created_at": pd.Timestamp.now(tz="UTC").isoformat()
    }

    return signal


# ============================================================
# FORMAT SIGNAL & MONITOR POSITION (KST & 한글화 적용)
# ============================================================

def format_entry_message(signal):
    direction = signal["direction"]
    emoji = "🟢" if direction == "LONG" else "🔴"
    title = "롱 포지션 시그널 (스마트 점수제)" if direction == "LONG" else "숏 포지션 시그널 (스마트 점수제)"
    kst_time_str = format_kst_time(signal['signal_time'])

    return f"""
👑 <b>골드 선물 스마트 시그널</b>

{emoji} <b>{title}</b>

━━━━━━━━━━━━━━━━━━

💰 <b>진입가 (ENTRY)</b>
<code>${signal['entry']:,.2f}</code>

🛑 <b>손절가 (SL)</b>
<code>${signal['sl']:,.2f}</code>

🎯 <b>1차 목표가 (TP1)</b>
<code>${signal['tp1']:,.2f}</code>

🎯 <b>2차 목표가 (TP2)</b>
<code>${signal['tp2']:,.2f}</code>

🎯 <b>3차 목표가 (TP3)</b>
<code>${signal['tp3']:,.2f}</code>

━━━━━━━━━━━━━━━━━━

📊 RSI : <code>{signal['rsi']:.2f}</code>
📈 ADX : <code>{signal['adx']:.2f}</code>
📏 ATR : <code>{signal['atr']:.2f}</code>

⚖️ 리스크 : <code>{signal['risk']:.2f}</code>

🧠 <b>진입 근거</b>
{signal['reason']}

🕐 시그널 발생 시간 (KST)
<code>{kst_time_str}</code>

📊 <a href="https://www.tradingview.com/symbols/GC1!/">트레이딩뷰 골드 차트</a>
"""

def monitor_position(state, m1):
    if not state or state.get("status") != "active":
        return state

    direction = state["direction"]
    entry, sl = float(state["entry"]), float(state["sl"])
    tp1, tp2, tp3 = float(state["tp1"]), float(state["tp2"]), float(state["tp3"])
    entry_time = normalize_timestamp(state["entry_time"])

    data = m1[m1.index >= entry_time].copy()
    if data.empty:
        return state

    for idx, row in data.iterrows():
        high, low = float(row["High"]), float(row["Low"])
        candle_time = normalize_timestamp(idx)
        kst_candle_time = format_kst_time(candle_time.isoformat())

        if direction == "LONG":
            if low <= sl:
                send_telegram(f"🛑 <b>롱 포지션 손절가 도달 (SL)</b>\n\n진입가: <code>${entry:,.2f}</code>\n손절가: <code>${sl:,.2f}</code>\n시간: <code>{kst_candle_time}</code>")
                log_event("LONG_SL", state)
                state["status"] = "cooldown"
                state["cooldown_until"] = (pd.Timestamp.now(tz="UTC") + pd.Timedelta(minutes=COOLDOWN_MINUTES)).isoformat()
                save_state(state)
                return state

            if not state["tp1_hit"] and high >= tp1:
                send_telegram(f"🎯 <b>1차 목표가 도달 (TP1)</b>\n\n1차 TP: <code>${tp1:,.2f}</code>\n진입가: <code>${entry:,.2f}</code>")
                state["tp1_hit"] = True
                log_event("LONG_TP1", state)
                save_state(state)

            if not state["tp2_hit"] and high >= tp2:
                send_telegram(f"🎯 <b>2차 목표가 도달 (TP2)</b>\n\n2차 TP: <code>${tp2:,.2f}</code>\n🔒 스탑로스가 본절가로 이동되었습니다 (<code>${entry:,.2f}</code>)")
                state["tp2_hit"] = True
                state["sl"] = entry
                log_event("LONG_TP2", state)
                save_state(state)

            if not state["tp3_hit"] and high >= tp3:
                send_telegram(f"🏆 <b>3차 목표가 도달 (TP3)</b>\n\n3차 TP: <code>${tp3:,.2f}</code>\n포지션이 성공적으로 완료되었습니다. 🔥")
                state["tp3_hit"] = True
                state["status"] = "cooldown"
                state["cooldown_until"] = (candle_time + pd.Timedelta(minutes=COOLDOWN_MINUTES)).isoformat()
                log_event("LONG_TP3", state)
                save_state(state)
                return state
        else:
            if high >= sl:
                send_telegram(f"🛑 <b>숏 포지션 손절가 도달 (SL)</b>\n\n진입가: <code>${entry:,.2f}</code>\n손절가: <code>${sl:,.2f}</code>\n시간: <code>{kst_candle_time}</code>")
                log_event("SHORT_SL", state)
                state["status"] = "cooldown"
                state["cooldown_until"] = (pd.Timestamp.now(tz="UTC") + pd.Timedelta(minutes=COOLDOWN_MINUTES)).isoformat()
                save_state(state)
                return state

            if not state["tp1_hit"] and low <= tp1:
                send_telegram(f"🎯 <b>1차 목표가 도달 (TP1)</b>\n\n1차 TP: <code>${tp1:,.2f}</code>\n진입가: <code>${entry:,.2f}</code>")
                state["tp1_hit"] = True
                log_event("SHORT_TP1", state)
                save_state(state)

            if not state["tp2_hit"] and low <= tp2:
                send_telegram(f"🎯 <b>2차 목표가 도달 (TP2)</b>\n\n2차 TP: <code>${tp2:,.2f}</code>\n🔒 스탑로스가 본절가로 이동되었습니다 (<code>${entry:,.2f}</code>)")
                state["tp2_hit"] = True
                state["sl"] = entry
                log_event("SHORT_TP2", state)
                save_state(state)

            if not state["tp3_hit"] and low <= tp3:
                send_telegram(f"🏆 <b>3차 목표가 도달 (TP3)</b>\n\n3차 TP: <code>${tp3:,.2f}</code>\n포지션이 성공적으로 완료되었습니다. 🔥")
                state["tp3_hit"] = True
                state["status"] = "cooldown"
                state["cooldown_until"] = (candle_time + pd.Timedelta(minutes=COOLDOWN_MINUTES)).isoformat()
                log_event("SHORT_TP3", state)
                save_state(state)
                return state

    save_state(state)
    return state


# ============================================================
# MAIN
# ============================================================

def main():
    print("====================================")
    print(" GOLD FUTURES SMART SIGNAL BOT")
    print("====================================")

    state = load_state()
    data = download_data()
    if data is None:
        print("Market data unavailable.")
        return

    h1, m15, m5, m1 = data

    if state.get("status") == "active":
        print("\nActive position found. Monitoring...")
        monitor_position(state, m1)
        return

    if state.get("status") == "cooldown":
        cooldown_until = state.get("cooldown_until")
        if cooldown_until:
            now = pd.Timestamp.now(tz="UTC")
            cooldown_time = normalize_timestamp(cooldown_until)
            if now < cooldown_time:
                print(f"\nCooldown active until: {cooldown_time}")
                return
        save_state({})

    signal = find_signal(h1, m15, m5)
    if signal is None:
        return

    save_state(signal)
    log_event("NEW_SIGNAL", signal)

    message = format_entry_message(signal)
    sent = send_telegram(message)
    if sent:
        signal["entry_alert_sent"] = True
        save_state(signal)

    print("\n====================================")
    print(" SIGNAL CREATED")
    print("====================================")
    print(f"Direction : {signal['direction']}")
    print(f"Entry     : ${signal['entry']:,.2f}")
    print(f"SL        : ${signal['sl']:,.2f}")
    print(f"TP1       : ${signal['tp1']:,.2f}")
    print(f"TP2       : ${signal['tp2']:,.2f}")
    print(f"TP3       : ${signal['tp3']:,.2f}")


if __name__ == "__main__":
    main()
