import os
import json
import time
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import yfinance as yf
import requests


# ============================================================
# GOLD FUTURES SMART SIGNAL BOT V6
# M15 PRIMARY + 5M CONFIRMATION
# H1 = CONTEXT ONLY
# ============================================================

TICKER = "GC=F"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

STATE_FILE = "signal_state.json"
LOG_FILE = "bot_log.json"

KST = timezone(timedelta(hours=9))


# ============================================================
# SETTINGS
# ============================================================

# Signal
MIN_M15_SCORE = 5
MIN_EARLY_SCORE = 5

# RSI
LONG_RSI_MIN = 52
LONG_RSI_MAX = 68

SHORT_RSI_MIN = 32
SHORT_RSI_MAX = 48

# ADX
MIN_ADX = 13
EARLY_MIN_ADX = 13

# Entry distance
MAX_ENTRY_DISTANCE_ATR = 1.50

# Signal candle
MAX_SIGNAL_MOVE_ATR = 0.80

# Cooldown
COOLDOWN_MINUTES = 60

# Target
TP1_R = 1.20
TP2_R = 2.00
TP3_R = 3.00

# Stop
SWING_LOOKBACK = 8
SL_ATR_BUFFER = 0.35

# Allowed risk
MIN_RISK_ATR = 0.55
MAX_RISK_ATR = 2.50


# ============================================================
# UTILITY
# ============================================================

def now_kst():
    return datetime.now(KST)


def safe_float(v, default=0.0):
    try:
        if pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing.")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }

    try:
        r = requests.post(url, json=payload, timeout=15)

        if r.status_code == 200:
            print("Telegram sent.")
            return True

        print("Telegram error:", r.text)
        return False

    except Exception as e:
        print("Telegram exception:", e)
        return False


# ============================================================
# STATE
# ============================================================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2,
            default=str
        )


def clear_state():
    if os.path.exists(STATE_FILE):
        try:
            os.remove(STATE_FILE)
        except Exception:
            pass


def load_log():
    if not os.path.exists(LOG_FILE):
        return []

    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        return data if isinstance(data, list) else []

    except Exception:
        return []


def save_log(data):
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(
            data[-300:],
            f,
            ensure_ascii=False,
            indent=2,
            default=str
        )


def add_log(event, data=None):
    logs = load_log()

    logs.append({
        "time": now_kst().isoformat(),
        "event": event,
        "data": data or {}
    })

    save_log(logs)


# ============================================================
# DATA DOWNLOAD
# ============================================================

def download_data():

    print("Downloading market data...")

    result = {}

    configs = {
        "1h": {
            "period": "30d",
            "interval": "1h"
        },
        "15m": {
            "period": "10d",
            "interval": "15m"
        },
        "5m": {
            "period": "5d",
            "interval": "5m"
        },
        "1m": {
            "period": "2d",
            "interval": "1m"
        }
    }

    for name, cfg in configs.items():

        try:

            df = yf.download(
                TICKER,
                period=cfg["period"],
                interval=cfg["interval"],
                auto_adjust=False,
                progress=False
            )

            if df is None or df.empty:
                print(f"{name}: NO DATA")
                continue

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            required = [
                "Open",
                "High",
                "Low",
                "Close"
            ]

            for col in required:
                if col not in df.columns:
                    raise ValueError(f"Missing column: {col}")

            df = df.dropna(subset=required).copy()

            print(f"{name}: {len(df)} candles")

            result[name] = df

        except Exception as e:
            print(f"{name} download error:", e)

    return result


# ============================================================
# INDICATORS
# ============================================================

def add_indicators(df):

    df = df.copy()

    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    df["EMA20"] = close.ewm(
        span=20,
        adjust=False
    ).mean()

    df["EMA50"] = close.ewm(
        span=50,
        adjust=False
    ).mean()

    # RSI
    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    df["RSI"] = 100 - (
        100 / (1 + rs)
    )

    # True Range
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    df["ATR"] = tr.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    # ADX
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where(
            (up_move > down_move) & (up_move > 0),
            up_move,
            0
        ),
        index=df.index
    )

    minus_dm = pd.Series(
        np.where(
            (down_move > up_move) & (down_move > 0),
            down_move,
            0
        ),
        index=df.index
    )

    atr14 = df["ATR"]

    plus_di = (
        100 *
        plus_dm.ewm(
            alpha=1 / 14,
            adjust=False
        ).mean()
        / atr14.replace(0, np.nan)
    )

    minus_di = (
        100 *
        minus_dm.ewm(
            alpha=1 / 14,
            adjust=False
        ).mean()
        / atr14.replace(0, np.nan)
    )

    dx = (
        100 *
        (plus_di - minus_di).abs()
        /
        (plus_di + minus_di).replace(0, np.nan)
    )

    df["ADX"] = dx.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    # Candle information
    df["Body"] = (close - df["Open"]).abs()

    df["Range"] = (
        df["High"] - df["Low"]
    ).replace(0, np.nan)

    df["BodyRatio"] = (
        df["Body"] / df["Range"]
    )

    df["Bull"] = close > df["Open"]
    df["Bear"] = close < df["Open"]

    return df


# ============================================================
# CLOSED CANDLE
# ============================================================

def get_closed(df):

    if len(df) < 5:
        return None, None

    current = df.iloc[-2]
    previous = df.iloc[-3]

    return current, previous


# ============================================================
# 5 MIN CONFIRMATION
# ============================================================

def check_5m(df):

    if len(df) < 10:
        return False, False, 0, 0

    c = df.iloc[-2]
    p = df.iloc[-3]

    close = safe_float(c["Close"])
    ema20 = safe_float(c["EMA20"])
    ema50 = safe_float(c["EMA50"])

    p_close = safe_float(p["Close"])

    rsi = safe_float(c["RSI"])
    adx = safe_float(c["ADX"])

    bull_score = 0
    bear_score = 0

    # LONG
    if close > ema20:
        bull_score += 1

    if ema20 > ema50:
        bull_score += 1

    if close > p_close:
        bull_score += 1

    long_ok = (
        bull_score >= 2
        and rsi >= 48
        and rsi <= 72
        and adx >= 10
    )

    # SHORT
    if close < ema20:
        bear_score += 1

    if ema20 < ema50:
        bear_score += 1

    if close < p_close:
        bear_score += 1

    short_ok = (
        bear_score >= 2
        and rsi >= 28
        and rsi <= 52
        and adx >= 10
    )

    return (
        long_ok,
        short_ok,
        bull_score,
        bear_score
    )


# ============================================================
# M15 LONG SCORE
# ============================================================

def calculate_long_score(c, p):

    close = safe_float(c["Close"])
    ema20 = safe_float(c["EMA20"])
    ema50 = safe_float(c["EMA50"])
    rsi = safe_float(c["RSI"])
    adx = safe_float(c["ADX"])

    prev_high = safe_float(p["High"])

    score = 0

    # 1. Price above EMA20
    if close > ema20:
        score += 1

    # 2. EMA20 above EMA50
    if ema20 > ema50:
        score += 1

    # 3. Bull candle
    if bool(c["Bull"]):
        score += 1

    # 4. Break previous candle high
    if close > prev_high:
        score += 1

    # 5. RSI healthy bullish zone
    if LONG_RSI_MIN <= rsi <= LONG_RSI_MAX:
        score += 1

    # 6. ADX sufficient
    if adx >= MIN_ADX:
        score += 1

    return score


# ============================================================
# M15 SHORT SCORE
# ============================================================

def calculate_short_score(c, p):

    close = safe_float(c["Close"])
    ema20 = safe_float(c["EMA20"])
    ema50 = safe_float(c["EMA50"])
    rsi = safe_float(c["RSI"])
    adx = safe_float(c["ADX"])

    prev_low = safe_float(p["Low"])

    score = 0

    # 1. Price below EMA20
    if close < ema20:
        score += 1

    # 2. EMA20 below EMA50
    if ema20 < ema50:
        score += 1

    # 3. Bear candle
    if bool(c["Bear"]):
        score += 1

    # 4. Break previous candle low
    if close < prev_low:
        score += 1

    # 5. RSI healthy bearish zone
    if SHORT_RSI_MIN <= rsi <= SHORT_RSI_MAX:
        score += 1

    # 6. ADX sufficient
    if adx >= MIN_ADX:
        score += 1

    return score


# ============================================================
# H1 CONTEXT
# IMPORTANT:
# H1 DOES NOT BLOCK NORMAL M15 SIGNALS
# ============================================================

def get_h1_context(df):

    if len(df) < 10:
        return {
            "bull": False,
            "bear": False,
            "strong_bull": False,
            "strong_bear": False
        }

    c = df.iloc[-2]
    p = df.iloc[-3]

    close = safe_float(c["Close"])
    ema20 = safe_float(c["EMA20"])
    ema50 = safe_float(c["EMA50"])

    prev_ema20 = safe_float(p["EMA20"])

    bull = (
        close > ema20
        and ema20 > ema50
    )

    bear = (
        close < ema20
        and ema20 < ema50
    )

    strong_bull = (
        bull
        and ema20 > prev_ema20
    )

    strong_bear = (
        bear
        and ema20 < prev_ema20
    )

    return {
        "bull": bull,
        "bear": bear,
        "strong_bull": strong_bull,
        "strong_bear": strong_bear
    }


# ============================================================
# ENTRY FILTER
# ============================================================

def entry_distance_ok(close, ema20, atr):

    if atr <= 0:
        return False

    distance = abs(close - ema20)

    return (
        distance / atr
        <= MAX_ENTRY_DISTANCE_ATR
    )


def signal_move_ok(c):

    atr = safe_float(c["ATR"])

    if atr <= 0:
        return False

    move = safe_float(c["Body"])

    return (
        move / atr
        <= MAX_SIGNAL_MOVE_ATR
    )


# ============================================================
# STOP LOSS
# ============================================================

def calculate_long_sl(m15, entry):

    recent = m15.iloc[
        -(SWING_LOOKBACK + 2):-2
    ]

    if recent.empty:
        return None

    swing_low = safe_float(
        recent["Low"].min()
    )

    atr = safe_float(
        m15.iloc[-2]["ATR"]
    )

    sl = swing_low - (
        atr * SL_ATR_BUFFER
    )

    if sl >= entry:
        return None

    risk = entry - sl

    risk_atr = (
        risk / atr
        if atr > 0
        else 999
    )

    if (
        risk_atr < MIN_RISK_ATR
        or risk_atr > MAX_RISK_ATR
    ):
        return None

    return sl


def calculate_short_sl(m15, entry):

    recent = m15.iloc[
        -(SWING_LOOKBACK + 2):-2
    ]

    if recent.empty:
        return None

    swing_high = safe_float(
        recent["High"].max()
    )

    atr = safe_float(
        m15.iloc[-2]["ATR"]
    )

    sl = swing_high + (
        atr * SL_ATR_BUFFER
    )

    if sl <= entry:
        return None

    risk = sl - entry

    risk_atr = (
        risk / atr
        if atr > 0
        else 999
    )

    if (
        risk_atr < MIN_RISK_ATR
        or risk_atr > MAX_RISK_ATR
    ):
        return None

    return sl


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def format_signal(signal):

    direction = signal["direction"]

    if direction == "LONG":
        emoji = "🟢"
        title = "LONG SIGNAL"
    else:
        emoji = "🔴"
        title = "SHORT SIGNAL"

    mode = signal["mode"]

    return (
        f"{emoji} <b>{title}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"🥇 금 선물 (GC=F)\n\n"
        f"진입가     ${signal['entry']:.2f}\n"
        f"손절가     ${signal['sl']:.2f}\n"
        f"TP1        ${signal['tp1']:.2f}\n"
        f"TP2        ${signal['tp2']:.2f}\n"
        f"TP3        ${signal['tp3']:.2f}\n\n"
        f"📊 M15 점수  {signal['m15_score']}/6\n"
        f"📊 5M 확인   PASS\n"
        f"📊 H1 상태   {signal['h1_context']}\n\n"
        f"⚡ 진입 방식  {mode}\n\n"
        f"━━━━━━━━━━━━━━━━━━"
    )


# ============================================================
# BUILD SIGNAL
# ============================================================

def build_signal(data):

    h1 = data["1h"]
    m15 = data["15m"]
    m5 = data["5m"]

    h1 = add_indicators(h1)
    m15 = add_indicators(m15)
    m5 = add_indicators(m5)

    if (
        len(h1) < 10
        or len(m15) < 20
        or len(m5) < 20
    ):
        print("Not enough indicator data.")
        return None

    h1_c, h1_p = get_closed(h1)
    c, p = get_closed(m15)

    if c is None or p is None:
        return None

    entry = safe_float(c["Close"])
    ema20 = safe_float(c["EMA20"])
    ema50 = safe_float(c["EMA50"])
    rsi = safe_float(c["RSI"])
    adx = safe_float(c["ADX"])
    atr = safe_float(c["ATR"])

    long_score = calculate_long_score(
        c,
        p
    )

    short_score = calculate_short_score(
        c,
        p
    )

    (
        long_5m,
        short_5m,
        long_5m_score,
        short_5m_score
    ) = check_5m(m5)

    h1_ctx = get_h1_context(h1)

    distance_ok = entry_distance_ok(
        entry,
        ema20,
        atr
    )

    move_ok = signal_move_ok(c)

    print()
    print("====================================")
    print(" CURRENT MARKET CHECK")
    print("====================================")
    print(f"M15 Close : ${entry:,.2f}")
    print(f"EMA20     : ${ema20:,.2f}")
    print(f"EMA50     : ${ema50:,.2f}")
    print(f"RSI       : {rsi:.2f}")
    print(f"ADX       : {adx:.2f}")
    print(f"ATR       : {atr:.2f}")

    print()
    print("====================================")
    print(" LONG CHECK")
    print("====================================")
    print(f"M15 Score : {long_score}/6")
    print(f"5M Score  : {long_5m_score}/3")
    print(
        f"5M Confirm: "
        f"{'PASS' if long_5m else 'FAIL'}"
    )
    print(
        f"H1       : "
        f"{'BULL' if h1_ctx['bull'] else 'NOT BULL'}"
    )

    print()
    print("====================================")
    print(" SHORT CHECK")
    print("====================================")
    print(f"M15 Score : {short_score}/6")
    print(f"5M Score  : {short_5m_score}/3")
    print(
        f"5M Confirm: "
        f"{'PASS' if short_5m else 'FAIL'}"
    )
    print(
        f"H1       : "
        f"{'BEAR' if h1_ctx['bear'] else 'NOT BEAR'}"
    )

    print()
    print("====================================")
    print(" ENTRY FILTER")
    print("====================================")
    print(
        f"Distance : "
        f"{'PASS' if distance_ok else 'FAIL'}"
    )
    print(
        f"Candle   : "
        f"{'PASS' if move_ok else 'FAIL'}"
    )

    # ========================================================
    # LONG
    #
    # IMPORTANT:
    # H1 IS NOT A REQUIRED CONDITION.
    #
    # M15 + 5M are the primary engines.
    # ========================================================

    long_base = (
        long_score >= MIN_M15_SCORE
        and long_5m
        and adx >= EARLY_MIN_ADX
        and distance_ok
        and move_ok
        and rsi >= LONG_RSI_MIN
        and rsi <= LONG_RSI_MAX
    )

    # ========================================================
    # SHORT
    # ========================================================

    short_base = (
        short_score >= MIN_M15_SCORE
        and short_5m
        and adx >= EARLY_MIN_ADX
        and distance_ok
        and move_ok
        and rsi >= SHORT_RSI_MIN
        and rsi <= SHORT_RSI_MAX
    )

    # Both should never normally be true.
    # If both occur, do nothing.
    if long_base and short_base:
        print()
        print("Both LONG and SHORT conditions detected.")
        print("No signal.")
        return None

    # ========================================================
    # LONG SIGNAL
    # ========================================================

    if long_base:

        sl = calculate_long_sl(
            m15,
            entry
        )

        if sl is None:
            print("LONG rejected: invalid SL/risk.")
            return None

        risk = entry - sl

        tp1 = entry + (
            risk * TP1_R
        )

        tp2 = entry + (
            risk * TP2_R
        )

        tp3 = entry + (
            risk * TP3_R
        )

        if h1_ctx["bull"]:
            mode = "M15 TREND"
            h1_text = "BULL"
        else:
            mode = "M15 EARLY / REVERSAL"
            h1_text = "NOT BULL"

        return {
            "direction": "LONG",
            "mode": mode,
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "risk": risk,
            "m15_score": long_score,
            "m5_score": long_5m_score,
            "h1_context": h1_text,
            "signal_time": (
                m15.index[-2].isoformat()
            )
        }

    # ========================================================
    # SHORT SIGNAL
    # ========================================================

    if short_base:

        sl = calculate_short_sl(
            m15,
            entry
        )

        if sl is None:
            print("SHORT rejected: invalid SL/risk.")
            return None

        risk = sl - entry

        tp1 = entry - (
            risk * TP1_R
        )

        tp2 = entry - (
            risk * TP2_R
        )

        tp3 = entry - (
            risk * TP3_R
        )

        if h1_ctx["bear"]:
            mode = "M15 TREND"
            h1_text = "BEAR"
        else:
            mode = "M15 EARLY / REVERSAL"
            h1_text = "NOT BEAR"

        return {
            "direction": "SHORT",
            "mode": mode,
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "risk": risk,
            "m15_score": short_score,
            "m5_score": short_5m_score,
            "h1_context": h1_text,
            "signal_time": (
                m15.index[-2].isoformat()
            )
        }

    print()
    print("No valid signal found.")

    return None


# ============================================================
# COOLDOWN
# ============================================================

def cooldown_active(state):

    last_exit = state.get("last_exit_time")

    if not last_exit:
        return False

    try:

        dt = datetime.fromisoformat(
            last_exit
        )

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=KST
            )

        elapsed = (
            datetime.now(KST) - dt
        ).total_seconds() / 60

        if elapsed < COOLDOWN_MINUTES:

            remaining = (
                COOLDOWN_MINUTES - elapsed
            )

            print(
                f"Cooldown active: "
                f"{remaining:.1f} min remaining"
            )

            return True

    except Exception:
        return False

    return False


# ============================================================
# DUPLICATE CHECK
# ============================================================

def duplicate_signal(state, signal):

    last_signal = state.get(
        "last_signal_time"
    )

    if not last_signal:
        return False

    return (
        last_signal
        == signal["signal_time"]
    )


# ============================================================
# OPEN POSITION
# ============================================================

def save_position(signal):

    state = {
        "active": True,
        "direction": signal["direction"],
        "mode": signal["mode"],

        "entry": signal["entry"],
        "sl": signal["sl"],

        "tp1": signal["tp1"],
        "tp2": signal["tp2"],
        "tp3": signal["tp3"],

        "original_sl": signal["sl"],

        "risk": signal["risk"],

        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,

        "entry_alert_sent": False,

        "entry_time": now_kst().isoformat(),
        "signal_time": signal["signal_time"],

        "last_signal_time": signal["signal_time"]
    }

    save_state(state)

    return state


# ============================================================
# POSITION MESSAGE
# ============================================================

def format_entry_retry(state):

    direction = state["direction"]

    if direction == "LONG":
        emoji = "🟢"
    else:
        emoji = "🔴"

    return (
        f"{emoji} <b>{direction} SIGNAL</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"🥇 금 선물 (GC=F)\n\n"
        f"진입가     ${state['entry']:.2f}\n"
        f"손절가     ${state['sl']:.2f}\n"
        f"TP1        ${state['tp1']:.2f}\n"
        f"TP2        ${state['tp2']:.2f}\n"
        f"TP3        ${state['tp3']:.2f}\n\n"
        f"⚡ {state['mode']}\n\n"
        f"━━━━━━━━━━━━━━━━━━"
    )


# ============================================================
# EXIT MESSAGE
# ============================================================

def exit_message(
    state,
    exit_type,
    price,
    pnl_pct
):

    direction = state["direction"]

    if exit_type == "STOP LOSS":
        emoji = "🔴"
    else:
        emoji = "🔵"

    return (
        f"{emoji} <b>{exit_type}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"🥇 금 선물 (GC=F)\n\n"
        f"진입가     ${state['entry']:.2f}\n"
        f"청산가     ${price:.2f}\n"
        f"손익률     {pnl_pct:+.2f}%\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{'🛑 손절 처리 완료' if exit_type == 'STOP LOSS' else '🎯 익절 처리 완료'}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )


def tp_message(
    state,
    tp_name,
    price
):

    return (
        f"🎯 <b>{tp_name} HIT</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"🥇 금 선물 (GC=F)\n\n"
        f"진입가     ${state['entry']:.2f}\n"
        f"현재가     ${price:.2f}\n\n"
        f"📌 {tp_name} 도달\n"
        f"━━━━━━━━━━━━━━━━━━"
    )


# ============================================================
# PNL
# ============================================================

def calculate_pnl_pct(
    direction,
    entry,
    exit_price
):

    if entry <= 0:
        return 0

    if direction == "LONG":

        return (
            (exit_price - entry)
            / entry
        ) * 100

    return (
        (entry - exit_price)
        / entry
    ) * 100


# ============================================================
# MONITOR POSITION
# ============================================================

def monitor_position(data, state):

    if not state.get("active"):
        return False

    df = data.get("1m")

    if df is None or len(df) < 5:
        print("1m data unavailable.")
        return True

    df = add_indicators(df)

    direction = state["direction"]

    entry = safe_float(
        state["entry"]
    )

    sl = safe_float(
        state["sl"]
    )

    tp1 = safe_float(
        state["tp1"]
    )

    tp2 = safe_float(
        state["tp2"]
    )

    tp3 = safe_float(
        state["tp3"]
    )

    tp1_hit = bool(
        state.get("tp1_hit", False)
    )

    tp2_hit = bool(
        state.get("tp2_hit", False)
    )

    tp3_hit = bool(
        state.get("tp3_hit", False)
    )

    # Process closed 1m candles only.
    candle = df.iloc[-2]

    high = safe_float(candle["High"])
    low = safe_float(candle["Low"])
    close = safe_float(candle["Close"])

    print()
    print("====================================")
    print(" ACTIVE POSITION")
    print("====================================")
    print(f"Direction : {direction}")
    print(f"Entry     : ${entry:.2f}")
    print(f"SL        : ${sl:.2f}")
    print(f"TP1       : ${tp1:.2f}")
    print(f"TP2       : ${tp2:.2f}")
    print(f"TP3       : ${tp3:.2f}")
    print(f"1M Close  : ${close:.2f}")

    # ========================================================
    # STOP LOSS FIRST
    # ========================================================

    if direction == "LONG":

        if low <= sl:

            pnl = calculate_pnl_pct(
                direction,
                entry,
                sl
            )

            msg = exit_message(
                state,
                "STOP LOSS",
                sl,
                pnl
            )

            send_telegram(msg)

            state["active"] = False
            state["last_exit_time"] = (
                now_kst().isoformat()
            )

            save_state(state)

            add_log(
                "STOP_LOSS",
                {
                    "direction": direction,
                    "entry": entry,
                    "exit": sl,
                    "pnl_pct": pnl
                }
            )

            return False

    else:

        if high >= sl:

            pnl = calculate_pnl_pct(
                direction,
                entry,
                sl
            )

            msg = exit_message(
                state,
                "STOP LOSS",
                sl,
                pnl
            )

            send_telegram(msg)

            state["active"] = False
            state["last_exit_time"] = (
                now_kst().isoformat()
            )

            save_state(state)

            add_log(
                "STOP_LOSS",
                {
                    "direction": direction,
                    "entry": entry,
                    "exit": sl,
                    "pnl_pct": pnl
                }
            )

            return False

    # ========================================================
    # TP1
    # ========================================================

    if not tp1_hit:

        hit = (
            high >= tp1
            if direction == "LONG"
            else low <= tp1
        )

        if hit:

            send_telegram(
                tp_message(
                    state,
                    "TP1",
                    tp1
                )
            )

            state["tp1_hit"] = True
            tp1_hit = True

            save_state(state)

            add_log(
                "TP1",
                {
                    "direction": direction,
                    "price": tp1
                }
            )

    # ========================================================
    # TP2
    # ========================================================

    if tp1_hit and not tp2_hit:

        hit = (
            high >= tp2
            if direction == "LONG"
            else low <= tp2
        )

        if hit:

            send_telegram(
                tp_message(
                    state,
                    "TP2",
                    tp2
                )
            )

            state["tp2_hit"] = True

            # Move SL to ENTRY
            state["sl"] = entry

            tp2_hit = True

            save_state(state)

            add_log(
                "TP2",
                {
                    "direction": direction,
                    "price": tp2,
                    "new_sl": entry
                }
            )

    # ========================================================
    # TP3
    # ========================================================

    if tp2_hit and not tp3_hit:

        hit = (
            high >= tp3
            if direction == "LONG"
            else low <= tp3
        )

        if hit:

            send_telegram(
                tp_message(
                    state,
                    "TP3",
                    tp3
                )
            )

            pnl = calculate_pnl_pct(
                direction,
                entry,
                tp3
            )

            send_telegram(
                exit_message(
                    state,
                    "TAKE PROFIT",
                    tp3,
                    pnl
                )
            )

            state["tp3_hit"] = True
            state["active"] = False
            state["last_exit_time"] = (
                now_kst().isoformat()
            )

            save_state(state)

            add_log(
                "TP3",
                {
                    "direction": direction,
                    "entry": entry,
                    "exit": tp3,
                    "pnl_pct": pnl
                }
            )

            return False

    # ========================================================
    # UPDATED STATE
    # ========================================================

    save_state(state)

    return True


# ============================================================
# MAIN
# ============================================================

def main():

    print("=================================")
    print(" GOLD FUTURES SMART SIGNAL BOT")
    print(" BALANCED V6")
    print(" M15 PRIMARY + 5M CONFIRM")
    print(" H1 CONTEXT ONLY")
    print("=================================")

    data = download_data()

    required = [
        "1h",
        "15m",
        "5m",
        "1m"
    ]

    missing = [
        x for x in required
        if x not in data
    ]

    if missing:

        print(
            "Missing data:",
            missing
        )

        return

    state = load_state()

    # ========================================================
    # ACTIVE POSITION
    # ========================================================

    if state.get("active"):

        print()
        print(
            "Active position detected."
        )

        still_active = monitor_position(
            data,
            state
        )

        if still_active:

            # Entry alert retry
            if not state.get(
                "entry_alert_sent",
                False
            ):

                sent = send_telegram(
                    format_entry_retry(
                        state
                    )
                )

                if sent:

                    state[
                        "entry_alert_sent"
                    ] = True

                    save_state(state)

            print(
                "Monitoring active position."
            )

        else:

            print(
                "Position closed."
            )

        return

    # ========================================================
    # COOLDOWN
    # ========================================================

    if cooldown_active(state):

        print(
            "No new signal during cooldown."
        )

        return

    # ========================================================
    # FIND NEW SIGNAL
    # ========================================================

    signal = build_signal(data)

    if signal is None:
        return

    # ========================================================
    # DUPLICATE
    # ========================================================

    if duplicate_signal(
        state,
        signal
    ):

        print(
            "Duplicate signal blocked."
        )

        return

    # ========================================================
    # SAVE POSITION
    # ========================================================

    new_state = save_position(
        signal
    )

    # ========================================================
    # TELEGRAM
    # ========================================================

    message = format_signal(
        signal
    )

    sent = send_telegram(
        message
    )

    if sent:

        new_state[
            "entry_alert_sent"
        ] = True

        save_state(
            new_state
        )

    add_log(
        "NEW_SIGNAL",
        {
            "direction": signal[
                "direction"
            ],
            "mode": signal[
                "mode"
            ],
            "entry": signal[
                "entry"
            ],
            "sl": signal[
                "sl"
            ],
            "tp1": signal[
                "tp1"
            ],
            "tp2": signal[
                "tp2"
            ],
            "tp3": signal[
                "tp3"
            ],
            "m15_score": signal[
                "m15_score"
            ],
            "m5_score": signal[
                "m5_score"
            ]
        }
    )

    print()
    print(
        "===================================="
    )
    print(" NEW SIGNAL")
    print(
        "===================================="
    )

    print(
        f"Direction : "
        f"{signal['direction']}"
    )

    print(
        f"Mode      : "
        f"{signal['mode']}"
    )

    print(
        f"Entry     : "
        f"${signal['entry']:.2f}"
    )

    print(
        f"SL        : "
        f"${signal['sl']:.2f}"
    )

    print(
        f"TP1       : "
        f"${signal['tp1']:.2f}"
    )

    print(
        f"TP2       : "
        f"${signal['tp2']:.2f}"
    )

    print(
        f"TP3       : "
        f"${signal['tp3']:.2f}"
    )

    print(
        "===================================="
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
