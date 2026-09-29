import os
import json
import time
from datetime import datetime, timezone, timedelta

import yfinance as yf
import pandas as pd
import numpy as np
import requests


# ============================================================
# GOLD FUTURES SMART SIGNAL BOT V11.1
# SIGNAL -> PENDING -> ACTIVE
# OLD V10/V11 STATE RESET
# M15 LEAD + 5M SUPPORT
# H1 CONTEXT ONLY
# ============================================================

print("====================================")
print(" 금 선물 스마트 시그널 봇")
print(" BALANCED V11.1")
print(" SIGNAL -> PENDING -> ACTIVE")
print(" M15 LEAD + 5M SUPPORT")
print(" H1 CONTEXT ONLY")
print(" FRESH MARKET RE-EVALUATION")
print(" OLD STATE RESET ENABLED")
print("====================================")


# ============================================================
# SETTINGS
# ============================================================

TICKER = "GC=F"

STATE_FILE = "signal_state.json"
LOG_FILE = "bot_log.json"

STATE_VERSION = "11.1"

M15_INTERVAL = "15m"
M5_INTERVAL = "5m"
H1_INTERVAL = "1h"
M1_INTERVAL = "1m"

# Signal score
MIN_M15_SCORE = 4
STRONG_M15_SCORE = 5

# RSI
LONG_RSI_MIN = 52
LONG_RSI_MAX = 70

SHORT_RSI_MIN = 30
SHORT_RSI_MAX = 48

# ADX
MIN_ADX = 13

# Entry filters
MAX_ENTRY_DISTANCE_ATR = 2.00
MAX_SIGNAL_MOVE_ATR = 1.35

# Pending entry
ENTRY_ZONE_ATR = 0.45
MAX_PENDING_DISTANCE_ATR = 1.50
PENDING_TIMEOUT_MINUTES = 90

# Cooldown
COOLDOWN_MINUTES = 60

# ATR / risk
ATR_RISK_MIN = 0.55
ATR_RISK_MAX = 2.50

# Targets
TP1_R = 1.20
TP2_R = 2.00
TP3_R = 3.00

# Swing
SWING_LOOKBACK = 8
SL_ATR_BUFFER = 0.35


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_TOKEN = (
    os.getenv("TELEGRAM_TOKEN")
    or os.getenv("TELEGRAM_BOT_TOKEN")
)

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=15
        )

        if response.status_code == 200:
            print("Telegram sent successfully.")
            return True

        print("Telegram error:", response.text)

    except Exception as e:
        print("Telegram exception:", e)

    return False


# ============================================================
# TIME
# ============================================================

KST = timezone(timedelta(hours=9))


def now_kst():
    return datetime.now(KST)


def now_iso():
    return now_kst().isoformat()


# ============================================================
# DEFAULT STATE
# ============================================================

def default_state():
    return {
        "version": STATE_VERSION,

        "status": "NONE",

        "direction": None,

        "signal_price": None,

        "entry_zone_low": None,
        "entry_zone_high": None,

        "pending_time": None,
        "pending_alert_sent": False,

        "entry": None,
        "sl": None,

        "tp1": None,
        "tp2": None,
        "tp3": None,

        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,

        "entry_alert_sent": False,

        "signal_time": None,
        "last_update": None,

        "cooldown_until": None
    }


# ============================================================
# STATE LOAD
# ============================================================

def load_state():

    if not os.path.exists(STATE_FILE):
        print("No existing state found.")
        return default_state()

    try:

        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        # ----------------------------------------------------
        # IMPORTANT
        # V10/V11 old state has no V11.1 version.
        # Reset it ONCE.
        # ----------------------------------------------------

        if data.get("version") != STATE_VERSION:

            print("")
            print("====================================")
            print(" OLD STATE RESET")
            print("====================================")
            print("기존 V10/V11 상태를 초기화합니다.")
            print("현재 시장을 새롭게 분석합니다.")
            print("")

            return default_state()

        base = default_state()
        base.update(data)

        return base

    except Exception as e:

        print("State load error:", e)

        return default_state()


# ============================================================
# STATE SAVE
# ============================================================

def save_state(state):

    state["version"] = STATE_VERSION
    state["last_update"] = now_iso()

    try:

        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                state,
                f,
                indent=2,
                ensure_ascii=False
            )

    except Exception as e:

        print("State save error:", e)


# ============================================================
# LOG
# ============================================================

def save_log(event, data=None):

    logs = []

    if os.path.exists(LOG_FILE):

        try:

            with open(LOG_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)

        except Exception:
            logs = []

    logs.append({
        "time": now_iso(),
        "event": event,
        "data": data or {}
    })

    logs = logs[-500:]

    try:

        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(
                logs,
                f,
                indent=2,
                ensure_ascii=False
            )

    except Exception as e:

        print("Log save error:", e)


# ============================================================
# DATA DOWNLOAD
# ============================================================

def download_data():

    print("")
    print("Downloading market data...")

    data = {}

    periods = {
        "1h": ("1h", "730d"),
        "15m": ("15m", "60d"),
        "5m": ("5m", "60d"),
        "1m": ("1m", "7d")
    }

    for key, (interval, period) in periods.items():

        try:

            df = yf.download(
                TICKER,
                period=period,
                interval=interval,
                auto_adjust=False,
                progress=False
            )

            if df is None or df.empty:
                print(f"{key}: NO DATA")
                continue

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df = df.dropna()

            data[key] = df

            print(f"{key}: {len(df)} candles")

        except Exception as e:

            print(f"{key}: ERROR -> {e}")

    return data


# ============================================================
# INDICATORS
# ============================================================

def ema(series, length):

    return series.ewm(
        span=length,
        adjust=False
    ).mean()


def rsi(series, length=14):

    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / length,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / length,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    result = 100 - (100 / (1 + rs))

    return result


def atr(df, length=14):

    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    return tr.ewm(
        alpha=1 / length,
        adjust=False
    ).mean()


def adx(df, length=14):

    high = df["High"]
    low = df["Low"]
    close = df["Close"]

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

    prev_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs()
        ],
        axis=1
    ).max(axis=1)

    atr_value = tr.ewm(
        alpha=1 / length,
        adjust=False
    ).mean()

    plus_di = (
        100 *
        plus_dm.ewm(
            alpha=1 / length,
            adjust=False
        ).mean()
        / atr_value
    )

    minus_di = (
        100 *
        minus_dm.ewm(
            alpha=1 / length,
            adjust=False
        ).mean()
        / atr_value
    )

    dx = (
        100 *
        (plus_di - minus_di).abs()
        /
        (plus_di + minus_di).replace(0, np.nan)
    )

    return dx.ewm(
        alpha=1 / length,
        adjust=False
    ).mean()


# ============================================================
# MARKET ANALYSIS
# ============================================================

def analyze_market(data):

    m15 = data["15m"].copy()
    m5 = data["5m"].copy()
    h1 = data["1h"].copy()

    # --------------------------------------------------------
    # M15
    # --------------------------------------------------------

    m15["EMA20"] = ema(m15["Close"], 20)
    m15["EMA50"] = ema(m15["Close"], 50)
    m15["RSI"] = rsi(m15["Close"])
    m15["ADX"] = adx(m15)
    m15["ATR"] = atr(m15)

    last15 = m15.iloc[-1]
    prev15 = m15.iloc[-2]

    close15 = float(last15["Close"])
    ema20_15 = float(last15["EMA20"])
    ema50_15 = float(last15["EMA50"])
    rsi15 = float(last15["RSI"])
    adx15 = float(last15["ADX"])
    atr15 = float(last15["ATR"])

    # --------------------------------------------------------
    # 5M
    # --------------------------------------------------------

    m5["EMA20"] = ema(m5["Close"], 20)
    m5["EMA50"] = ema(m5["Close"], 50)
    m5["RSI"] = rsi(m5["Close"])

    last5 = m5.iloc[-1]

    close5 = float(last5["Close"])
    ema20_5 = float(last5["EMA20"])
    ema50_5 = float(last5["EMA50"])
    rsi5 = float(last5["RSI"])

    # --------------------------------------------------------
    # H1
    # --------------------------------------------------------

    h1["EMA20"] = ema(h1["Close"], 20)
    h1["EMA50"] = ema(h1["Close"], 50)

    last1h = h1.iloc[-1]

    close1h = float(last1h["Close"])
    ema20_1h = float(last1h["EMA20"])
    ema50_1h = float(last1h["EMA50"])

    # --------------------------------------------------------
    # M15 SCORE
    # --------------------------------------------------------

    long_score = 0
    short_score = 0

    # Price vs EMA20
    if close15 > ema20_15:
        long_score += 1

    if close15 < ema20_15:
        short_score += 1

    # EMA20 vs EMA50
    if ema20_15 > ema50_15:
        long_score += 1

    if ema20_15 < ema50_15:
        short_score += 1

    # RSI
    if LONG_RSI_MIN <= rsi15 <= LONG_RSI_MAX:
        long_score += 1

    if SHORT_RSI_MIN <= rsi15 <= SHORT_RSI_MAX:
        short_score += 1

    # ADX
    if adx15 >= MIN_ADX:
        if close15 > ema20_15:
            long_score += 1

        if close15 < ema20_15:
            short_score += 1

    # Candle direction
    if close15 > float(prev15["Close"]):
        long_score += 1

    if close15 < float(prev15["Close"]):
        short_score += 1

    # Momentum
    m15_change = abs(
        close15 - float(prev15["Close"])
    )

    if m15_change <= atr15 * MAX_SIGNAL_MOVE_ATR:

        if close15 > float(prev15["Close"]):
            long_score += 1

        if close15 < float(prev15["Close"]):
            short_score += 1

    # Cap score to 6
    long_score = min(long_score, 6)
    short_score = min(short_score, 6)

    # --------------------------------------------------------
    # 5M SCORE
    # --------------------------------------------------------

    long_5_score = 0
    short_5_score = 0

    if close5 > ema20_5:
        long_5_score += 1

    if ema20_5 > ema50_5:
        long_5_score += 1

    if rsi5 >= 50:
        long_5_score += 1

    if close5 < ema20_5:
        short_5_score += 1

    if ema20_5 < ema50_5:
        short_5_score += 1

    if rsi5 <= 50:
        short_5_score += 1

    # --------------------------------------------------------
    # H1 CONTEXT
    # --------------------------------------------------------

    h1_bull = (
        close1h > ema20_1h
        and ema20_1h > ema50_1h
    )

    h1_bear = (
        close1h < ema20_1h
        and ema20_1h < ema50_1h
    )

    # --------------------------------------------------------
    # OUTPUT
    # --------------------------------------------------------

    print("")
    print("====================================")
    print(" CURRENT MARKET CHECK")
    print("====================================")

    print(f"M15 Close : ${close15:,.2f}")
    print(f"EMA20     : ${ema20_15:,.2f}")
    print(f"EMA50     : ${ema50_15:,.2f}")
    print(f"RSI       : {rsi15:.2f}")
    print(f"ADX       : {adx15:.2f}")
    print(f"ATR       : {atr15:.2f}")

    print("")
    print("====================================")
    print(" LONG CHECK")
    print("====================================")

    print(f"M15 Score : {long_score}/6")
    print(f"5M Score  : {long_5_score}/3")
    print(
        f"H1        : "
        f"{'BULL' if h1_bull else 'NOT BULL'}"
    )

    print("")
    print("====================================")
    print(" SHORT CHECK")
    print("====================================")

    print(f"M15 Score : {short_score}/6")
    print(f"5M Score  : {short_5_score}/3")
    print(
        f"H1        : "
        f"{'BEAR' if h1_bear else 'NOT BEAR'}"
    )

    # --------------------------------------------------------
    # Signal selection
    # --------------------------------------------------------

    long_valid = (
        long_score >= MIN_M15_SCORE
        and rsi15 >= LONG_RSI_MIN
        and rsi15 <= LONG_RSI_MAX
    )

    short_valid = (
        short_score >= MIN_M15_SCORE
        and rsi15 >= SHORT_RSI_MIN
        and rsi15 <= SHORT_RSI_MAX
    )

    # Strong opposite 5M direction can block
    if long_valid and short_5_score >= 3 and long_5_score == 0:
        long_valid = False

    if short_valid and long_5_score >= 3 and short_5_score == 0:
        short_valid = False

    # --------------------------------------------------------
    # If both are valid, use stronger M15 score
    # --------------------------------------------------------

    direction = None

    if long_valid and short_valid:

        if long_score > short_score:
            direction = "LONG"

        elif short_score > long_score:
            direction = "SHORT"

    elif long_valid:

        direction = "LONG"

    elif short_valid:

        direction = "SHORT"

    # --------------------------------------------------------
    # Entry distance
    # --------------------------------------------------------

    if direction:

        distance_atr = 0.0

        if atr15 > 0:
            distance_atr = abs(close15 - ema20_15) / atr15

        print("")
        print("====================================")
        print(" SIGNAL FILTER")
        print("====================================")

        print(f"Distance : {distance_atr:.2f} ATR")

        if distance_atr > MAX_ENTRY_DISTANCE_ATR:

            print("Distance : FAIL")
            direction = None

        else:

            print("Distance : PASS")

    return {
        "direction": direction,
        "signal_price": close15,
        "atr": atr15,
        "long_score": long_score,
        "short_score": short_score,
        "long_5_score": long_5_score,
        "short_5_score": short_5_score,
        "h1_bull": h1_bull,
        "h1_bear": h1_bear
    }


# ============================================================
# CREATE PENDING
# ============================================================

def create_pending(state, analysis):

    direction = analysis["direction"]
    signal_price = analysis["signal_price"]
    atr_value = analysis["atr"]

    zone_size = atr_value * ENTRY_ZONE_ATR

    # --------------------------------------------------------
    # LONG
    #
    # Current signal price becomes upper side of pullback zone.
    # --------------------------------------------------------

    if direction == "LONG":

        entry_zone_high = signal_price
        entry_zone_low = signal_price - zone_size

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    else:

        entry_zone_low = signal_price
        entry_zone_high = signal_price + zone_size

    state["version"] = STATE_VERSION

    state["status"] = "PENDING"

    state["direction"] = direction

    state["signal_price"] = signal_price

    state["entry_zone_low"] = entry_zone_low
    state["entry_zone_high"] = entry_zone_high

    state["pending_time"] = now_iso()

    state["pending_alert_sent"] = False

    state["entry"] = None
    state["sl"] = None

    state["tp1"] = None
    state["tp2"] = None
    state["tp3"] = None

    state["tp1_hit"] = False
    state["tp2_hit"] = False
    state["tp3_hit"] = False

    state["entry_alert_sent"] = False

    state["signal_time"] = now_iso()

    save_state(state)

    print("")
    print("====================================")
    print(" NEW PENDING SIGNAL")
    print("====================================")

    print(f"Direction    : {direction}")
    print(f"Signal Price : ${signal_price:,.2f}")
    print(
        f"Entry Zone   : "
        f"${entry_zone_low:,.2f} ~ "
        f"${entry_zone_high:,.2f}"
    )

    message = (
        "🥇 금 선물 스마트 시그널\n\n"
        "🟡 진입 대기 신호\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 방향 : {'롱' if direction == 'LONG' else '숏'}\n"
        f"📍 신호 가격 : ${signal_price:,.2f}\n"
        f"🎯 진입 대기 구간 : "
        f"${entry_zone_low:,.2f} ~ ${entry_zone_high:,.2f}\n\n"
        "⚠️ 아직 실제 진입 상태가 아닙니다.\n"
        "가격이 진입 구간에 들어오면 실제 진입을 확정합니다.\n\n"
        "⚙️ 전략 : M15 주도 + 5M 확인"
    )

    sent = send_telegram(message)

    if sent:

        state["pending_alert_sent"] = True
        save_state(state)

    save_log(
        "PENDING_CREATED",
        {
            "direction": direction,
            "signal_price": signal_price,
            "zone_low": entry_zone_low,
            "zone_high": entry_zone_high
        }
    )


# ============================================================
# ACTIVATE PENDING
# ============================================================

def activate_pending(state, current_price, atr_value):

    direction = state["direction"]

    signal_price = float(
        state["signal_price"]
    )

    # --------------------------------------------------------
    # Entry
    # --------------------------------------------------------

    entry = current_price

    # --------------------------------------------------------
    # Swing reference
    # --------------------------------------------------------

    # Since current pending state doesn't retain the original
    # dataframe, use ATR-based risk here.
    # This keeps the entry logic stable across GitHub runs.
    # --------------------------------------------------------

    risk = atr_value * 1.35

    risk = max(
        risk,
        atr_value * ATR_RISK_MIN
    )

    risk = min(
        risk,
        atr_value * ATR_RISK_MAX
    )

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        sl = entry - risk

        tp1 = entry + risk * TP1_R
        tp2 = entry + risk * TP2_R
        tp3 = entry + risk * TP3_R

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    else:

        sl = entry + risk

        tp1 = entry - risk * TP1_R
        tp2 = entry - risk * TP2_R
        tp3 = entry - risk * TP3_R

    state["status"] = "ACTIVE"

    state["entry"] = entry
    state["sl"] = sl

    state["tp1"] = tp1
    state["tp2"] = tp2
    state["tp3"] = tp3

    state["tp1_hit"] = False
    state["tp2_hit"] = False
    state["tp3_hit"] = False

    state["entry_alert_sent"] = False

    save_state(state)

    print("")
    print("====================================")
    print(" ACTUAL ENTRY CONFIRMED")
    print("====================================")

    print(f"Direction : {direction}")
    print(f"Entry     : ${entry:,.2f}")
    print(f"SL        : ${sl:,.2f}")
    print(f"TP1       : ${tp1:,.2f}")
    print(f"TP2       : ${tp2:,.2f}")
    print(f"TP3       : ${tp3:,.2f}")

    message = (
        "🥇 금 선물 스마트 시그널\n\n"
        f"{'🟢 롱 진입 확정' if direction == 'LONG' else '🔴 숏 진입 확정'}\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 방향 : {'롱' if direction == 'LONG' else '숏'}\n"
        f"💰 실제 진입가 : ${entry:,.2f}\n"
        f"🛑 손절가 : ${sl:,.2f}\n"
        f"🎯 익절 1 : ${tp1:,.2f}\n"
        f"🎯 익절 2 : ${tp2:,.2f}\n"
        f"🎯 익절 3 : ${tp3:,.2f}\n\n"
        "⚙️ 전략 : M15 주도 + 5M 확인\n"
        "✅ 진입 대기 구간 도달로 실제 진입 확정"
    )

    sent = send_telegram(message)

    if sent:

        state["entry_alert_sent"] = True
        save_state(state)

    save_log(
        "ENTRY_CONFIRMED",
        {
            "direction": direction,
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3
        }
    )


# ============================================================
# MONITOR PENDING
# ============================================================

def monitor_pending(state, data):

    direction = state["direction"]

    m1 = data["1m"]
    m15 = data["15m"]

    current_price = float(
        m1["Close"].iloc[-1]
    )

    atr_value = float(
        atr(m15).iloc[-1]
    )

    pending_time = state.get("pending_time")

    # --------------------------------------------------------
    # Timeout
    # --------------------------------------------------------

    if pending_time:

        try:

            created = datetime.fromisoformat(
                pending_time
            )

            age_minutes = (
                now_kst() - created
            ).total_seconds() / 60

            if age_minutes > PENDING_TIMEOUT_MINUTES:

                print("")
                print("====================================")
                print(" PENDING TIMEOUT")
                print("====================================")

                print("진입 대기 시간이 만료되었습니다.")

                save_log(
                    "PENDING_TIMEOUT",
                    {
                        "direction": direction,
                        "current_price": current_price
                    }
                )

                state.clear()
                state.update(default_state())

                save_state(state)

                return

        except Exception:
            pass

    zone_low = float(
        state["entry_zone_low"]
    )

    zone_high = float(
        state["entry_zone_high"]
    )

    # --------------------------------------------------------
    # Distance from signal
    # --------------------------------------------------------

    signal_price = float(
        state["signal_price"]
    )

    distance_atr = 0

    if atr_value > 0:

        distance_atr = (
            abs(current_price - signal_price)
            / atr_value
        )

    print("")
    print("====================================")
    print(" PENDING ENTRY")
    print("====================================")

    print(f"Direction    : {direction}")
    print(f"Signal Price : ${signal_price:,.2f}")

    print(
        f"Entry Zone   : "
        f"${zone_low:,.2f} ~ "
        f"${zone_high:,.2f}"
    )

    print(f"Current      : ${current_price:,.2f}")
    print(f"Distance     : {distance_atr:.2f} ATR")

    # --------------------------------------------------------
    # Cancel if price runs too far
    # --------------------------------------------------------

    if distance_atr > MAX_PENDING_DISTANCE_ATR:

        print("")
        print("Pending signal cancelled.")
        print("가격이 진입 구간에서 너무 멀어졌습니다.")

        message = (
            "🥇 금 선물 스마트 시그널\n\n"
            "⚪ 진입 대기 취소\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"📌 방향 : {'롱' if direction == 'LONG' else '숏'}\n"
            f"💰 현재가 : ${current_price:,.2f}\n\n"
            "가격이 진입 구간에서 너무 멀어져\n"
            "기존 대기 신호를 취소했습니다."
        )

        send_telegram(message)

        save_log(
            "PENDING_CANCELLED_DISTANCE",
            {
                "direction": direction,
                "current_price": current_price
            }
        )

        state.clear()
        state.update(default_state())

        save_state(state)

        return

    # --------------------------------------------------------
    # Check entry zone
    # --------------------------------------------------------

    in_zone = (
        zone_low <= current_price <= zone_high
    )

    if in_zone:

        print("")
        print("ENTRY ZONE REACHED")
        print("실제 진입을 확정합니다.")

        activate_pending(
            state,
            current_price,
            atr_value
        )

        return

    print("Waiting for entry zone...")


# ============================================================
# MONITOR ACTIVE
# ============================================================

def monitor_active(state, data):

    m1 = data["1m"]

    current_price = float(
        m1["Close"].iloc[-1]
    )

    direction = state["direction"]

    entry = float(state["entry"])
    sl = float(state["sl"])

    tp1 = float(state["tp1"])
    tp2 = float(state["tp2"])
    tp3 = float(state["tp3"])

    print("")
    print("====================================")
    print(" ACTIVE POSITION")
    print("====================================")

    print(f"Direction : {direction}")
    print(f"Entry     : ${entry:,.2f}")
    print(f"SL        : ${sl:,.2f}")
    print(f"TP1       : ${tp1:,.2f}")
    print(f"TP2       : ${tp2:,.2f}")
    print(f"TP3       : ${tp3:,.2f}")
    print(f"1M Close  : ${current_price:,.2f}")

    # ========================================================
    # LONG
    # ========================================================

    if direction == "LONG":

        # ----------------------------------------------------
        # Stop Loss
        # ----------------------------------------------------

        if current_price <= sl:

            message = (
                "🥇 금 선물 스마트 시그널\n\n"
                "🔴 손절 처리\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 진입가 : ${entry:,.2f}\n"
                f"🛑 청산가 : ${current_price:,.2f}\n"
                f"📉 손익률 : "
                f"{((current_price - entry) / entry) * 100:.2f}%\n\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "🛑 손절 처리 완료"
            )

            send_telegram(message)

            save_log(
                "STOP_LOSS",
                {
                    "direction": direction,
                    "entry": entry,
                    "exit": current_price
                }
            )

            state.clear()
            state.update(default_state())

            save_state(state)

            return

        # ----------------------------------------------------
        # TP1
        # ----------------------------------------------------

        if not state["tp1_hit"] and current_price >= tp1:

            message = (
                "🥇 금 선물 스마트 시그널\n\n"
                "🔵 익절 1 도달\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 진입가 : ${entry:,.2f}\n"
                f"🎯 익절가 : ${current_price:,.2f}\n\n"
                "익절 1 도달"
            )

            send_telegram(message)

            state["tp1_hit"] = True

            save_state(state)

        # ----------------------------------------------------
        # TP2
        # ----------------------------------------------------

        if not state["tp2_hit"] and current_price >= tp2:

            message = (
                "🥇 금 선물 스마트 시그널\n\n"
                "🔵 익절 2 도달\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 진입가 : ${entry:,.2f}\n"
                f"🎯 익절가 : ${current_price:,.2f}\n\n"
                "익절 2 도달"
            )

            send_telegram(message)

            state["tp2_hit"] = True

            save_state(state)

        # ----------------------------------------------------
        # TP3
        # ----------------------------------------------------

        if not state["tp3_hit"] and current_price >= tp3:

            message = (
                "🥇 금 선물 스마트 시그널\n\n"
                "🔵 익절 3 도달\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 진입가 : ${entry:,.2f}\n"
                f"🎯 최종 청산가 : ${current_price:,.2f}\n\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "✅ 거래 종료"
            )

            send_telegram(message)

            save_log(
                "TP3_COMPLETE",
                {
                    "direction": direction,
                    "entry": entry,
                    "exit": current_price
                }
            )

            state.clear()
            state.update(default_state())

            save_state(state)

            return

    # ========================================================
    # SHORT
    # ========================================================

    else:

        # ----------------------------------------------------
        # Stop Loss
        # ----------------------------------------------------

        if current_price >= sl:

            message = (
                "🥇 금 선물 스마트 시그널\n\n"
                "🔴 손절 처리\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 진입가 : ${entry:,.2f}\n"
                f"🛑 청산가 : ${current_price:,.2f}\n"
                f"📉 손익률 : "
                f"{((entry - current_price) / entry) * 100:.2f}%\n\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "🛑 손절 처리 완료"
            )

            send_telegram(message)

            save_log(
                "STOP_LOSS",
                {
                    "direction": direction,
                    "entry": entry,
                    "exit": current_price
                }
            )

            state.clear()
            state.update(default_state())

            save_state(state)

            return

        # ----------------------------------------------------
        # TP1
        # ----------------------------------------------------

        if not state["tp1_hit"] and current_price <= tp1:

            message = (
                "🥇 금 선물 스마트 시그널\n\n"
                "🔵 익절 1 도달\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 진입가 : ${entry:,.2f}\n"
                f"🎯 익절가 : ${current_price:,.2f}\n\n"
                "익절 1 도달"
            )

            send_telegram(message)

            state["tp1_hit"] = True

            save_state(state)

        # ----------------------------------------------------
        # TP2
        # ----------------------------------------------------

        if not state["tp2_hit"] and current_price <= tp2:

            message = (
                "🥇 금 선물 스마트 시그널\n\n"
                "🔵 익절 2 도달\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 진입가 : ${entry:,.2f}\n"
                f"🎯 익절가 : ${current_price:,.2f}\n\n"
                "익절 2 도달"
            )

            send_telegram(message)

            state["tp2_hit"] = True

            save_state(state)

        # ----------------------------------------------------
        # TP3
        # ----------------------------------------------------

        if not state["tp3_hit"] and current_price <= tp3:

            message = (
                "🥇 금 선물 스마트 시그널\n\n"
                "🔵 익절 3 도달\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 진입가 : ${entry:,.2f}\n"
                f"🎯 최종 청산가 : ${current_price:,.2f}\n\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "✅ 거래 종료"
            )

            send_telegram(message)

            save_log(
                "TP3_COMPLETE",
                {
                    "direction": direction,
                    "entry": entry,
                    "exit": current_price
                }
            )

            state.clear()
            state.update(default_state())

            save_state(state)

            return

    print("")
    print("Active position remains open.")


# ============================================================
# MAIN
# ============================================================

def main():

    state = load_state()

    data = download_data()

    required = [
        "1h",
        "15m",
        "5m",
        "1m"
    ]

    for key in required:

        if key not in data:

            print("")
            print(f"Missing market data: {key}")
            return

    # ========================================================
    # ACTIVE
    # ========================================================

    if state["status"] == "ACTIVE":

        monitor_active(
            state,
            data
        )

        return

    # ========================================================
    # PENDING
    # ========================================================

    if state["status"] == "PENDING":

        monitor_pending(
            state,
            data
        )

        return

    # ========================================================
    # NEW MARKET ANALYSIS
    # ========================================================

    analysis = analyze_market(data)

    direction = analysis["direction"]

    if direction is None:

        print("")
        print("====================================")
        print(" NO VALID SIGNAL")
        print("====================================")

        save_log(
            "NO_SIGNAL",
            {
                "long_score": analysis["long_score"],
                "short_score": analysis["short_score"]
            }
        )

        return

    # ========================================================
    # CREATE NEW PENDING
    # ========================================================

    print("")
    print("====================================")
    print(" FRESH SIGNAL FOUND")
    print("====================================")

    print(f"Direction : {direction}")

    create_pending(
        state,
        analysis
    )

    # ========================================================
    # IMPORTANT
    #
    # After creating a fresh pending signal, check whether
    # current price is already inside the entry zone.
    #
    # This prevents unnecessary waiting when the signal
    # happens at the current market price.
    # ========================================================

    monitor_pending(
        state,
        data
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        print("")
        print("====================================")
        print(" BOT ERROR")
        print("====================================")

        print(str(e))

        save_log(
            "BOT_ERROR",
            {
                "error": str(e)
            }
)
