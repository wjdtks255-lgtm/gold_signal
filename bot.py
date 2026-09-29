import os
import json
import requests
import yfinance as yf
import pandas as pd
import numpy as np


# ============================================================
# GOLD FUTURES SMART SIGNAL BOT
# Balanced Signal Version
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
# STRATEGY SETTINGS
# ============================================================

# Take Profit
TP1_R = 1.20
TP2_R = 2.00
TP3_R = 3.00

# Stop Loss
MAX_SL_ATR = 2.50

# Entry distance
MAX_ENTRY_DISTANCE_ATR = 1.20

# Signal movement filter
MAX_SIGNAL_MOVE_ATR = 0.70

# ------------------------------------------------------------
# BALANCED FILTERS
# ------------------------------------------------------------

# Previous:
# MIN_ADX_1H = 18
# MIN_ADX_15M = 16

MIN_ADX_1H = 15
MIN_ADX_15M = 13

# RSI
LONG_RSI_MIN = 50
LONG_RSI_MAX = 70

SHORT_RSI_MIN = 30
SHORT_RSI_MAX = 50

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

        response = requests.post(
            url,
            json=payload,
            timeout=20
        )

        if response.status_code == 200:
            print("Telegram message sent.")
            return True

        print("Telegram error:")
        print(response.text)

    except Exception as e:
        print("Telegram exception:", e)

    return False


# ============================================================
# TIME
# ============================================================

def normalize_timestamp(value):

    ts = pd.Timestamp(value)

    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")

    return ts


# ============================================================
# STATE
# ============================================================

def load_state():

    if not os.path.exists(STATE_FILE):
        return {}

    try:

        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)

        if not isinstance(state, dict):
            return {}

        return state

    except Exception as e:

        print("State load error:", e)
        return {}


def save_state(state):

    with open(
        STATE_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# LOG
# ============================================================

def log_event(event_type, data=None):

    if os.path.exists(LOG_FILE):

        try:

            with open(
                LOG_FILE,
                "r",
                encoding="utf-8"
            ) as f:

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

    with open(
        LOG_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            logs,
            f,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# DATA CLEANING
# ============================================================

def clean_dataframe(df):

    if df is None or df.empty:
        return None

    df = df.copy()

    # Yahoo sometimes returns MultiIndex columns
    if isinstance(df.columns, pd.MultiIndex):

        df.columns = [
            col[0] if isinstance(col, tuple) else col
            for col in df.columns
        ]

    required = [
        "Open",
        "High",
        "Low",
        "Close"
    ]

    for col in required:

        if col not in df.columns:
            return None

    df = df[required].copy()

    df = df.dropna()

    try:

        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

    except Exception as e:

        print("Index timezone error:", e)

    return df


# ============================================================
# DOWNLOAD
# ============================================================

def download_data():

    print("Downloading market data...")

    try:

        h1 = yf.download(
            TICKER,
            period="30d",
            interval="1h",
            progress=False,
            auto_adjust=False
        )

        m15 = yf.download(
            TICKER,
            period="10d",
            interval="15m",
            progress=False,
            auto_adjust=False
        )

        m5 = yf.download(
            TICKER,
            period="5d",
            interval="5m",
            progress=False,
            auto_adjust=False
        )

        m1 = yf.download(
            TICKER,
            period="5d",
            interval="1m",
            progress=False,
            auto_adjust=False
        )

        h1 = clean_dataframe(h1)
        m15 = clean_dataframe(m15)
        m5 = clean_dataframe(m5)
        m1 = clean_dataframe(m1)

        if (
            h1 is None
            or m15 is None
            or m5 is None
            or m1 is None
        ):
            return None

        # Remove latest potentially incomplete candle
        if len(h1) > 2:
            h1 = h1.iloc[:-1]

        if len(m15) > 2:
            m15 = m15.iloc[:-1]

        if len(m5) > 2:
            m5 = m5.iloc[:-1]

        if len(m1) > 2:
            m1 = m1.iloc[:-1]

        return h1, m15, m5, m1

    except Exception as e:

        print("Download error:", e)
        return None


# ============================================================
# INDICATORS
# ============================================================

def add_indicators(df):

    df = df.copy()

    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    # EMA
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
    previous_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - previous_close).abs()
    tr3 = (low - previous_close).abs()

    tr = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    # ATR
    df["ATR"] = tr.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    # ADX
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where(
        (up_move > down_move) & (up_move > 0),
        up_move,
        0
    )

    minus_dm = np.where(
        (down_move > up_move) & (down_move > 0),
        down_move,
        0
    )

    atr_for_adx = tr.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    plus_di = (
        100
        * pd.Series(
            plus_dm,
            index=df.index
        ).ewm(
            alpha=1 / 14,
            adjust=False
        ).mean()
        / atr_for_adx
    )

    minus_di = (
        100
        * pd.Series(
            minus_dm,
            index=df.index
        ).ewm(
            alpha=1 / 14,
            adjust=False
        ).mean()
        / atr_for_adx
    )

    denominator = (
        plus_di + minus_di
    ).replace(0, np.nan)

    dx = (
        100
        * (plus_di - minus_di).abs()
        / denominator
    )

    df["ADX"] = dx.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    # Candle body
    candle_range = (
        high - low
    ).replace(0, np.nan)

    df["BodyRatio"] = (
        (close - df["Open"]).abs()
        / candle_range
    )

    # Close location
    df["ClosePosition"] = (
        (close - low)
        / candle_range
    )

    return df


# ============================================================
# SIGNAL DIAGNOSTICS
# ============================================================

def print_check(name, value):

    print(
        f"{name:<20}: "
        f"{'PASS' if value else 'FAIL'}"
    )


# ============================================================
# FIND SIGNAL
# ============================================================

def find_signal(h1, m15, m5):

    h1 = add_indicators(h1)
    m15 = add_indicators(m15)
    m5 = add_indicators(m5)

    if (
        len(h1) < 60
        or len(m15) < 60
        or len(m5) < 30
    ):
        print("Not enough data.")
        return None

    h = h1.iloc[-1]

    p = m15.iloc[-2]
    c = m15.iloc[-1]

    latest_5m = m5.iloc[-1]

    # ========================================================
    # MARKET VALUES
    # ========================================================

    price = float(c["Close"])

    ema20 = float(c["EMA20"])

    rsi = float(c["RSI"])

    adx = float(c["ADX"])

    atr = float(c["ATR"])

    print("")
    print("====================================")
    print(" CURRENT MARKET CHECK")
    print("====================================")

    print(f"Price : ${price:,.2f}")
    print(f"EMA20 : ${ema20:,.2f}")
    print(f"RSI   : {rsi:.2f}")
    print(f"ADX   : {adx:.2f}")
    print(f"ATR   : {atr:.2f}")

    # ========================================================
    # 1H TREND
    # ========================================================

    h1_bull = (
        h["Close"] > h["EMA20"]
        and h["EMA20"] > h["EMA50"]
        and h["EMA20"] > h1["EMA20"].iloc[-2]
        and h["ADX"] >= MIN_ADX_1H
    )

    h1_bear = (
        h["Close"] < h["EMA20"]
        and h["EMA20"] < h["EMA50"]
        and h["EMA20"] < h1["EMA20"].iloc[-2]
        and h["ADX"] >= MIN_ADX_1H
    )

    # ========================================================
    # LONG CONDITIONS
    # ========================================================

    long_pullback = (
        p["Low"]
        <= p["EMA20"] + p["ATR"] * 0.50
    )

    long_hold = (
        p["Close"]
        >= p["EMA20"] - p["ATR"] * 0.35
    )

    long_reversal = (
        c["Close"] > c["Open"]
        and c["BodyRatio"] >= 0.35
        and c["ClosePosition"] >= 0.60
    )

    long_breakout = (
        c["Close"] > p["High"]
    )

    long_rsi = (
        LONG_RSI_MIN
        <= rsi
        <= LONG_RSI_MAX
    )

    long_adx = (
        adx >= MIN_ADX_15M
    )

    long_distance = (
        abs(price - ema20)
        <= atr * MAX_ENTRY_DISTANCE_ATR
    )

    # ========================================================
    # SHORT CONDITIONS
    # ========================================================

    short_pullback = (
        p["High"]
        >= p["EMA20"] - p["ATR"] * 0.50
    )

    short_hold = (
        p["Close"]
        <= p["EMA20"] + p["ATR"] * 0.35
    )

    short_reversal = (
        c["Close"] < c["Open"]
        and c["BodyRatio"] >= 0.35
        and c["ClosePosition"] <= 0.40
    )

    short_breakdown = (
        c["Close"] < p["Low"]
    )

    short_rsi = (
        SHORT_RSI_MIN
        <= rsi
        <= SHORT_RSI_MAX
    )

    short_adx = (
        adx >= MIN_ADX_15M
    )

    short_distance = (
        abs(price - ema20)
        <= atr * MAX_ENTRY_DISTANCE_ATR
    )

    # ========================================================
    # DIAGNOSTICS
    # ========================================================

    print("")
    print("====================================")
    print(" LONG CHECK")
    print("====================================")

    print_check(
        "1H Trend",
        h1_bull
    )

    print_check(
        "Pullback",
        long_pullback
    )

    print_check(
        "EMA Hold",
        long_hold
    )

    print_check(
        "Reversal",
        long_reversal
    )

    print_check(
        "Breakout",
        long_breakout
    )

    print_check(
        "RSI",
        long_rsi
    )

    print_check(
        "ADX",
        long_adx
    )

    print_check(
        "EMA Distance",
        long_distance
    )

    print("")
    print("====================================")
    print(" SHORT CHECK")
    print("====================================")

    print_check(
        "1H Trend",
        h1_bear
    )

    print_check(
        "Pullback",
        short_pullback
    )

    print_check(
        "EMA Hold",
        short_hold
    )

    print_check(
        "Reversal",
        short_reversal
    )

    print_check(
        "Breakdown",
        short_breakdown
    )

    print_check(
        "RSI",
        short_rsi
    )

    print_check(
        "ADX",
        short_adx
    )

    print_check(
        "EMA Distance",
        short_distance
    )

    # ========================================================
    # FINAL CONDITIONS
    # ========================================================

    long_condition = (
        h1_bull
        and long_pullback
        and long_hold
        and long_reversal
        and long_breakout
        and long_rsi
        and long_adx
        and long_distance
    )

    short_condition = (
        h1_bear
        and short_pullback
        and short_hold
        and short_reversal
        and short_breakdown
        and short_rsi
        and short_adx
        and short_distance
    )

    if not long_condition and not short_condition:

        print("")
        print("====================================")
        print(" NO VALID SIGNAL")
        print("====================================")
        print("Waiting for next setup...")

        return None

    # ========================================================
    # ENTRY
    # ========================================================

    if long_condition:

        direction = "LONG"

        entry = price

        recent_lows = m15["Low"].iloc[-4:-1]

        swing_low = float(
            recent_lows.min()
        )

        sl = (
            swing_low
            - atr * 0.25
        )

        reason = (
            "1H bullish trend + "
            "15M EMA20 pullback + "
            "bullish reversal + "
            "breakout"
        )

    else:

        direction = "SHORT"

        entry = price

        recent_highs = m15["High"].iloc[-4:-1]

        swing_high = float(
            recent_highs.max()
        )

        sl = (
            swing_high
            + atr * 0.25
        )

        reason = (
            "1H bearish trend + "
            "15M EMA20 pullback + "
            "bearish reversal + "
            "breakdown"
        )

    # ========================================================
    # RISK
    # ========================================================

    risk = abs(entry - sl)

    if risk <= 0:

        print("Invalid risk.")
        return None

    if risk > atr * MAX_SL_ATR:

        print(
            "Signal rejected: "
            "SL distance too large."
        )

        return None

    # ========================================================
    # 5M ENTRY DISTANCE FILTER
    # ========================================================

    current_5m_price = float(
        latest_5m["Close"]
    )

    if (
        abs(current_5m_price - entry)
        > atr * MAX_SIGNAL_MOVE_ATR
    ):

        print(
            "Signal rejected: "
            "price moved too far "
            "from signal."
        )

        return None

    # ========================================================
    # TARGETS
    # ========================================================

    if direction == "LONG":

        tp1 = entry + risk * TP1_R
        tp2 = entry + risk * TP2_R
        tp3 = entry + risk * TP3_R

    else:

        tp1 = entry - risk * TP1_R
        tp2 = entry - risk * TP2_R
        tp3 = entry - risk * TP3_R

    # ========================================================
    # TIME
    # ========================================================

    signal_time = normalize_timestamp(
        m15.index[-1]
    )

    entry_time = (
        signal_time
        + pd.Timedelta(minutes=15)
    )

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

        "created_at": pd.Timestamp.now(
            tz="UTC"
        ).isoformat()
    }

    return signal


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_entry_message(signal):

    direction = signal["direction"]

    if direction == "LONG":

        emoji = "🟢"
        title = "LONG SIGNAL"

    else:

        emoji = "🔴"
        title = "SHORT SIGNAL"

    entry = signal["entry"]
    sl = signal["sl"]
    tp1 = signal["tp1"]
    tp2 = signal["tp2"]
    tp3 = signal["tp3"]

    rsi = signal["rsi"]
    adx = signal["adx"]
    atr = signal["atr"]
    risk = signal["risk"]

    signal_time = signal["signal_time"]

    return f"""
👑 <b>GOLD FUTURES SMART SIGNAL</b>

{emoji} <b>{title}</b>

━━━━━━━━━━━━━━━━━━

💰 <b>ENTRY</b>
<code>${entry:,.2f}</code>

🛑 <b>SL</b>
<code>${sl:,.2f}</code>

🎯 <b>TP1</b>
<code>${tp1:,.2f}</code>

🎯 <b>TP2</b>
<code>${tp2:,.2f}</code>

🎯 <b>TP3</b>
<code>${tp3:,.2f}</code>

━━━━━━━━━━━━━━━━━━

📊 RSI : <code>{rsi:.2f}</code>
📈 ADX : <code>{adx:.2f}</code>
📏 ATR : <code>{atr:.2f}</code>

⚖️ Risk : <code>{risk:.2f}</code>

🧠 <b>Reason</b>
{signal["reason"]}

🕐 Signal Time
<code>{signal_time}</code>

📊 <a href="https://www.tradingview.com/symbols/GC1!/">TradingView Gold</a>
"""


# ============================================================
# MONITOR POSITION
# ============================================================

def monitor_position(state, m1):

    if not state:
        return state

    if state.get("status") != "active":
        return state

    direction = state["direction"]

    entry = float(state["entry"])
    sl = float(state["sl"])

    tp1 = float(state["tp1"])
    tp2 = float(state["tp2"])
    tp3 = float(state["tp3"])

    entry_time = normalize_timestamp(
        state["entry_time"]
    )

    data = m1[
        m1.index >= entry_time
    ].copy()

    if data.empty:
        return state

    for idx, row in data.iterrows():

        high = float(row["High"])
        low = float(row["Low"])

        candle_time = normalize_timestamp(idx)

        # ====================================================
        # LONG
        # ====================================================

        if direction == "LONG":

            # Stop first
            if low <= sl:

                message = f"""
🛑 <b>LONG STOP LOSS</b>

Entry:
<code>${entry:,.2f}</code>

SL:
<code>${sl:,.2f}</code>

Time:
<code>{candle_time}</code>
"""

                send_telegram(message)

                log_event(
                    "LONG_SL",
                    state
                )

                state["status"] = "cooldown"

                state["cooldown_until"] = (
                    pd.Timestamp.now(
                        tz="UTC"
                    )
                    + pd.Timedelta(
                        minutes=COOLDOWN_MINUTES
                    )
                ).isoformat()

                save_state(state)

                return state

            # TP1
            if (
                not state["tp1_hit"]
                and high >= tp1
            ):

                message = f"""
🎯 <b>LONG TP1 HIT</b>

TP1:
<code>${tp1:,.2f}</code>

Entry:
<code>${entry:,.2f}</code>
"""

                send_telegram(message)

                state["tp1_hit"] = True

                log_event(
                    "LONG_TP1",
                    state
                )

                save_state(state)

            # TP2
            if (
                not state["tp2_hit"]
                and high >= tp2
            ):

                message = f"""
🎯 <b>LONG TP2 HIT</b>

TP2:
<code>${tp2:,.2f}</code>

🔒 SL moved to BREAK EVEN

New SL:
<code>${entry:,.2f}</code>
"""

                send_telegram(message)

                state["tp2_hit"] = True

                state["sl"] = entry

                log_event(
                    "LONG_TP2",
                    state
                )

                save_state(state)

            # TP3
            if (
                not state["tp3_hit"]
                and high >= tp3
            ):

                message = f"""
🏆 <b>LONG TP3 HIT</b>

TP3:
<code>${tp3:,.2f}</code>

Position completed.
"""

                send_telegram(message)

                state["tp3_hit"] = True

                state["status"] = "cooldown"

                state["cooldown_until"] = (
                    candle_time
                    + pd.Timedelta(
                        minutes=COOLDOWN_MINUTES
                    )
                ).isoformat()

                log_event(
                    "LONG_TP3",
                    state
                )

                save_state(state)

                return state

        # ====================================================
        # SHORT
        # ====================================================

        else:

            # Stop first
            if high >= sl:

                message = f"""
🛑 <b>SHORT STOP LOSS</b>

Entry:
<code>${entry:,.2f}</code>

SL:
<code>${sl:,.2f}</code>

Time:
<code>{candle_time}</code>
"""

                send_telegram(message)

                log_event(
                    "SHORT_SL",
                    state
                )

                state["status"] = "cooldown"

                state["cooldown_until"] = (
                    pd.Timestamp.now(
                        tz="UTC"
                    )
                    + pd.Timedelta(
                        minutes=COOLDOWN_MINUTES
                    )
                ).isoformat()

                save_state(state)

                return state

            # TP1
            if (
                not state["tp1_hit"]
                and low <= tp1
            ):

                message = f"""
🎯 <b>SHORT TP1 HIT</b>

TP1:
<code>${tp1:,.2f}</code>

Entry:
<code>${entry:,.2f}</code>
"""

                send_telegram(message)

                state["tp1_hit"] = True

                log_event(
                    "SHORT_TP1",
                    state
                )

                save_state(state)

            # TP2
            if (
                not state["tp2_hit"]
                and low <= tp2
            ):

                message = f"""
🎯 <b>SHORT TP2 HIT</b>

TP2:
<code>${tp2:,.2f}</code>

🔒 SL moved to BREAK EVEN

New SL:
<code>${entry:,.2f}</code>
"""

                send_telegram(message)

                state["tp2_hit"] = True

                state["sl"] = entry

                log_event(
                    "SHORT_TP2",
                    state
                )

                save_state(state)

            # TP3
            if (
                not state["tp3_hit"]
                and low <= tp3
            ):

                message = f"""
🏆 <b>SHORT TP3 HIT</b>

TP3:
<code>${tp3:,.2f}</code>

Position completed.
"""

                send_telegram(message)

                state["tp3_hit"] = True

                state["status"] = "cooldown"

                state["cooldown_until"] = (
                    candle_time
                    + pd.Timedelta(
                        minutes=COOLDOWN_MINUTES
                    )
                ).isoformat()

                log_event(
                    "SHORT_TP3",
                    state
                )

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

    # ========================================================
    # ACTIVE POSITION
    # ========================================================

    if state.get("status") == "active":

        print("")
        print("Active position found.")
        print("Monitoring position...")

        monitor_position(
            state,
            m1
        )

        return

    # ========================================================
    # COOLDOWN
    # ========================================================

    if state.get("status") == "cooldown":

        cooldown_until = state.get(
            "cooldown_until"
        )

        if cooldown_until:

            now = pd.Timestamp.now(
                tz="UTC"
            )

            cooldown_time = normalize_timestamp(
                cooldown_until
            )

            if now < cooldown_time:

                print("")
                print(
                    "Cooldown active until:",
                    cooldown_time
                )

                return

        # Cooldown finished
        state = {}

        save_state(state)

    # ========================================================
    # FIND SIGNAL
    # ========================================================

    signal = find_signal(
        h1,
        m15,
        m5
    )

    if signal is None:
        return

    # ========================================================
    # SAVE
    # ========================================================

    save_state(signal)

    log_event(
        "NEW_SIGNAL",
        signal
    )

    # ========================================================
    # TELEGRAM
    # ========================================================

    message = format_entry_message(
        signal
    )

    sent = send_telegram(
        message
    )

    if sent:

        signal["entry_alert_sent"] = True

        save_state(signal)

    print("")
    print("====================================")
    print(" SIGNAL CREATED")
    print("====================================")

    print(
        f"Direction : {signal['direction']}"
    )

    print(
        f"Entry     : ${signal['entry']:,.2f}"
    )

    print(
        f"SL        : ${signal['sl']:,.2f}"
    )

    print(
        f"TP1       : ${signal['tp1']:,.2f}"
    )

    print(
        f"TP2       : ${signal['tp2']:,.2f}"
    )

    print(
        f"TP3       : ${signal['tp3']:,.2f}"
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
