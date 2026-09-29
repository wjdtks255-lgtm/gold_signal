import os
import json
import requests
import yfinance as yf
import pandas as pd
import numpy as np

from datetime import datetime, timedelta, timezone


# =========================================================
# GOLD FUTURES SMART SIGNAL BOT
# BALANCED V5 FINAL
# =========================================================

print("=================================")
print(" GOLD FUTURES SMART SIGNAL BOT")
print(" BALANCED V5 FINAL")
print("=================================")


# =========================================================
# BASIC
# =========================================================

TICKER = "GC=F"

STATE_FILE = "signal_state.json"
LOG_FILE = "bot_log.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

KST = timezone(timedelta(hours=9))


# =========================================================
# TARGET SETTINGS
# =========================================================

TP1_R = 1.20
TP2_R = 2.00
TP3_R = 3.00


# =========================================================
# STOP LOSS SETTINGS
# =========================================================

MIN_SL_ATR = 0.55
MAX_SL_ATR = 2.50


# =========================================================
# ENTRY FILTER
# =========================================================

MAX_ENTRY_DISTANCE_ATR = 1.50
MAX_SIGNAL_MOVE_ATR = 0.80


# =========================================================
# TREND SIGNAL
# =========================================================

MIN_ADX = 18.0
TREND_SCORE = 4


# =========================================================
# EARLY TREND / REVERSAL
# =========================================================

EARLY_SCORE = 4
EARLY_MIN_ADX = 13.0


# =========================================================
# RSI
# =========================================================

LONG_RSI_MIN = 52.0
LONG_RSI_MAX = 68.0

SHORT_RSI_MIN = 32.0
SHORT_RSI_MAX = 48.0


# =========================================================
# COOLDOWN
# =========================================================

COOLDOWN_MINUTES = 60


# =========================================================
# TELEGRAM
# =========================================================

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
        "text": message
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=15
        )

        if response.status_code == 200:

            print("Telegram sent.")

            return True

        print(
            "Telegram error:",
            response.status_code,
            response.text
        )

    except Exception as e:

        print(
            "Telegram exception:",
            e
        )

    return False


# =========================================================
# TIME
# =========================================================

def normalize_timestamp(ts):

    ts = pd.Timestamp(ts)

    if ts.tzinfo is None:

        ts = ts.tz_localize("UTC")

    return ts


def format_kst_time(ts):

    ts = normalize_timestamp(ts)

    return ts.tz_convert(
        KST
    ).strftime(
        "%Y-%m-%d %H:%M"
    )


# =========================================================
# STATE
# =========================================================

def load_state():

    if not os.path.exists(
        STATE_FILE
    ):

        return {
            "status": "idle"
        }

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception:

        return {
            "status": "idle"
        }


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


# =========================================================
# LOG
# =========================================================

def log_event(event):

    logs = []

    if os.path.exists(
        LOG_FILE
    ):

        try:

            with open(
                LOG_FILE,
                "r",
                encoding="utf-8"
            ) as f:

                logs = json.load(f)

        except Exception:

            logs = []

    logs.append(event)

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


# =========================================================
# DATA CLEAN
# =========================================================

def clean_dataframe(df):

    if isinstance(
        df.columns,
        pd.MultiIndex
    ):

        df.columns = (
            df.columns
            .get_level_values(0)
        )

    required = [
        "Open",
        "High",
        "Low",
        "Close",
        "Volume"
    ]

    for col in required:

        if col not in df.columns:

            if col == "Volume":

                df[col] = 0

            else:

                raise ValueError(
                    f"Missing column: {col}"
                )

    df = df[
        [
            "Open",
            "High",
            "Low",
            "Close",
            "Volume"
        ]
    ].copy()

    df.dropna(
        subset=[
            "Open",
            "High",
            "Low",
            "Close"
        ],
        inplace=True
    )

    return df


# =========================================================
# DOWNLOAD MARKET DATA
# =========================================================

def download_data():

    print(
        "Downloading market data..."
    )

    data = {}

    periods = {

        "1h": (
            "10d",
            "1h"
        ),

        "15m": (
            "7d",
            "15m"
        ),

        "5m": (
            "5d",
            "5m"
        ),

        "1m": (
            "2d",
            "1m"
        )
    }

    for name, (
        period,
        interval
    ) in periods.items():

        try:

            df = yf.download(
                TICKER,
                period=period,
                interval=interval,
                auto_adjust=False,
                progress=False
            )

            if (
                df is None
                or df.empty
            ):

                print(
                    f"{name}: no data"
                )

                continue

            df = clean_dataframe(
                df
            )

            data[name] = df

            print(
                f"{name}: {len(df)} candles"
            )

        except Exception as e:

            print(
                f"{name} download error:",
                e
            )

    return data


# =========================================================
# INDICATORS
# =========================================================

def add_indicators(df):

    df = df.copy()

    # -----------------------------------------------------
    # EMA 20
    # -----------------------------------------------------

    df["EMA20"] = (
        df["Close"]
        .ewm(
            span=20,
            adjust=False
        )
        .mean()
    )

    # -----------------------------------------------------
    # EMA 50
    # -----------------------------------------------------

    df["EMA50"] = (
        df["Close"]
        .ewm(
            span=50,
            adjust=False
        )
        .mean()
    )

    # -----------------------------------------------------
    # RSI
    # -----------------------------------------------------

    delta = df["Close"].diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = (
        gain
        .ewm(
            alpha=1 / 14,
            adjust=False
        )
        .mean()
    )

    avg_loss = (
        loss
        .ewm(
            alpha=1 / 14,
            adjust=False
        )
        .mean()
    )

    rs = (
        avg_gain
        / avg_loss.replace(
            0,
            np.nan
        )
    )

    df["RSI"] = (
        100
        - (
            100
            / (1 + rs)
        )
    )

    df["RSI"] = (
        df["RSI"]
        .fillna(50)
    )

    # -----------------------------------------------------
    # ATR
    # -----------------------------------------------------

    previous_close = (
        df["Close"].shift(1)
    )

    tr1 = (
        df["High"]
        - df["Low"]
    )

    tr2 = (
        df["High"]
        - previous_close
    ).abs()

    tr3 = (
        df["Low"]
        - previous_close
    ).abs()

    true_range = pd.concat(
        [
            tr1,
            tr2,
            tr3
        ],
        axis=1
    ).max(axis=1)

    df["ATR"] = (
        true_range
        .ewm(
            alpha=1 / 14,
            adjust=False
        )
        .mean()
    )

    # -----------------------------------------------------
    # ADX
    # -----------------------------------------------------

    high_diff = df["High"].diff()

    low_diff = (
        -df["Low"].diff()
    )

    plus_dm = np.where(
        (
            high_diff > low_diff
        )
        & (
            high_diff > 0
        ),
        high_diff,
        0
    )

    minus_dm = np.where(
        (
            low_diff > high_diff
        )
        & (
            low_diff > 0
        ),
        low_diff,
        0
    )

    plus_di = (
        100
        * pd.Series(
            plus_dm,
            index=df.index
        )
        .ewm(
            alpha=1 / 14,
            adjust=False
        )
        .mean()
        / df["ATR"]
    )

    minus_di = (
        100
        * pd.Series(
            minus_dm,
            index=df.index
        )
        .ewm(
            alpha=1 / 14,
            adjust=False
        )
        .mean()
        / df["ATR"]
    )

    dx = (
        100
        * (
            plus_di
            - minus_di
        ).abs()
        / (
            plus_di
            + minus_di
        )
    )

    df["ADX"] = (
        dx
        .ewm(
            alpha=1 / 14,
            adjust=False
        )
        .mean()
    )

    # -----------------------------------------------------
    # CANDLE
    # -----------------------------------------------------

    df["Body"] = (
        df["Close"]
        - df["Open"]
    ).abs()

    df["Range"] = (
        df["High"]
        - df["Low"]
    ).replace(
        0,
        np.nan
    )

    df["BodyRatio"] = (
        df["Body"]
        / df["Range"]
    )

    df["ClosePosition"] = (
        df["Close"]
        - df["Low"]
    ) / df["Range"]

    df = df.replace(
        [
            np.inf,
            -np.inf
        ],
        np.nan
    )

    df = df.ffill().bfill()

    return df


# =========================================================
# 5M CONFIRMATION
# =========================================================

def check_5m_confirmation(m5):

    if len(m5) < 4:

        return (
            False,
            False,
            0,
            0
        )

    c = m5.iloc[-2]
    p = m5.iloc[-3]

    long_score = 0
    short_score = 0

    # LONG

    if c["Close"] > c["EMA20"]:

        long_score += 1

    if c["Close"] > p["Close"]:

        long_score += 1

    if c["RSI"] >= 50:

        long_score += 1

    # SHORT

    if c["Close"] < c["EMA20"]:

        short_score += 1

    if c["Close"] < p["Close"]:

        short_score += 1

    if c["RSI"] <= 50:

        short_score += 1

    return (
        long_score >= 2,
        short_score >= 2,
        long_score,
        short_score
    )


# =========================================================
# SIGNAL FINDER
# =========================================================

def find_signal(data):

    h1 = add_indicators(
        data["1h"]
    )

    m15 = add_indicators(
        data["15m"]
    )

    m5 = add_indicators(
        data["5m"]
    )

    if (
        len(h1) < 60
        or len(m15) < 60
        or len(m5) < 50
    ):

        print(
            "Not enough market data."
        )

        return None

    # =====================================================
    # CLOSED CANDLES ONLY
    # =====================================================

    h = h1.iloc[-2]
    hp = h1.iloc[-3]

    m = m15.iloc[-2]
    mp = m15.iloc[-3]

    # =====================================================
    # M15 MARKET VALUES
    # =====================================================

    price = float(
        m["Close"]
    )

    ema20 = float(
        m["EMA20"]
    )

    ema50 = float(
        m["EMA50"]
    )

    rsi = float(
        m["RSI"]
    )

    adx = float(
        m["ADX"]
    )

    atr = float(
        m["ATR"]
    )

    if atr <= 0:

        return None

    # =====================================================
    # H1 FULL TREND
    # =====================================================

    h1_bull = (
        h["Close"] > h["EMA20"]
        and h["EMA20"] > h["EMA50"]
        and h["EMA20"] > hp["EMA20"]
    )

    h1_bear = (
        h["Close"] < h["EMA20"]
        and h["EMA20"] < h["EMA50"]
        and h["EMA20"] < hp["EMA20"]
    )

    # =====================================================
    # H1 EARLY TREND
    # =====================================================
    #
    # IMPORTANT:
    #
    # No EMA slope requirement.
    #
    # LONG:
    # H1 Close > EMA20 > EMA50
    #
    # SHORT:
    # H1 Close < EMA20 < EMA50
    # =====================================================

    h1_early_bull = (
        h["Close"] > h["EMA20"]
        and h["EMA20"] > h["EMA50"]
    )

    h1_early_bear = (
        h["Close"] < h["EMA20"]
        and h["EMA20"] < h["EMA50"]
    )

    # =====================================================
    # 5M CONFIRMATION
    # =====================================================

    (
        long_5m,
        short_5m,
        long_5m_score,
        short_5m_score
    ) = check_5m_confirmation(
        m5
    )

    # =====================================================
    # ENTRY DISTANCE
    # =====================================================

    ema_distance = abs(
        price - ema20
    )

    entry_distance_atr = (
        ema_distance / atr
    )

    entry_distance_ok = (
        entry_distance_atr
        <= MAX_ENTRY_DISTANCE_ATR
    )

    # =====================================================
    # NORMAL LONG SCORE
    # =====================================================

    normal_long_score = 0

    if m["Close"] > m["Open"]:

        normal_long_score += 1

    if m["Close"] > mp["High"]:

        normal_long_score += 1

    if (
        45
        <= m["RSI"]
        <= LONG_RSI_MAX
    ):

        normal_long_score += 1

    if m["ADX"] >= MIN_ADX:

        normal_long_score += 1

    if (
        m["Close"] > m["EMA20"]
        and m["EMA20"] >= m["EMA50"]
    ):

        normal_long_score += 1

    if long_5m:

        normal_long_score += 1

    # =====================================================
    # NORMAL SHORT SCORE
    # =====================================================

    normal_short_score = 0

    if m["Close"] < m["Open"]:

        normal_short_score += 1

    if m["Close"] < mp["Low"]:

        normal_short_score += 1

    if (
        SHORT_RSI_MIN
        <= m["RSI"]
        <= 52
    ):

        normal_short_score += 1

    if m["ADX"] >= MIN_ADX:

        normal_short_score += 1

    if (
        m["Close"] < m["EMA20"]
        and m["EMA20"] <= m["EMA50"]
    ):

        normal_short_score += 1

    if short_5m:

        normal_short_score += 1

    # =====================================================
    # EARLY LONG SCORE
    # =====================================================
    #
    # 1 H1 close > EMA20
    # 2 H1 EMA20 > EMA50
    # 3 M15 bullish candle
    # 4 M15 RSI 52~68
    # 5 M15 close > EMA20
    # 6 5M confirmation
    #
    # 4 / 6 required
    # =====================================================

    early_long_score = 0

    if h["Close"] > h["EMA20"]:

        early_long_score += 1

    if h["EMA20"] > h["EMA50"]:

        early_long_score += 1

    if m["Close"] > m["Open"]:

        early_long_score += 1

    if (
        LONG_RSI_MIN
        <= m["RSI"]
        <= LONG_RSI_MAX
    ):

        early_long_score += 1

    if m["Close"] > m["EMA20"]:

        early_long_score += 1

    if long_5m:

        early_long_score += 1

    # =====================================================
    # EARLY SHORT SCORE
    # =====================================================

    early_short_score = 0

    if h["Close"] < h["EMA20"]:

        early_short_score += 1

    if h["EMA20"] < h["EMA50"]:

        early_short_score += 1

    if m["Close"] < m["Open"]:

        early_short_score += 1

    if (
        SHORT_RSI_MIN
        <= m["RSI"]
        <= SHORT_RSI_MAX
    ):

        early_short_score += 1

    if m["Close"] < m["EMA20"]:

        early_short_score += 1

    if short_5m:

        early_short_score += 1

    # =====================================================
    # DISPLAY
    # =====================================================

    print()
    print(
        "===================================="
    )

    print(
        " CURRENT MARKET CHECK"
    )

    print(
        "===================================="
    )

    print(
        f"Price : ${price:,.2f}"
    )

    print(
        f"EMA20 : ${ema20:,.2f}"
    )

    print(
        f"EMA50 : ${ema50:,.2f}"
    )

    print(
        f"RSI   : {rsi:.2f}"
    )

    print(
        f"ADX   : {adx:.2f}"
    )

    print(
        f"ATR   : {atr:.2f}"
    )

    print()
    print(
        "===================================="
    )

    print(
        " LONG CHECK"
    )

    print(
        "===================================="
    )

    print(
        "1H Trend       : "
        + (
            "PASS"
            if h1_bull
            else "FAIL"
        )
    )

    print(
        "1H Early Trend : "
        + (
            "PASS"
            if h1_early_bull
            else "FAIL"
        )
    )

    print(
        f"Normal Score   : "
        f"{normal_long_score}/6"
    )

    print(
        f"Early Score    : "
        f"{early_long_score}/6"
    )

    print(
        f"5M Score       : "
        f"{long_5m_score}/3"
    )

    print(
        "5M Confirm     : "
        + (
            "PASS"
            if long_5m
            else "FAIL"
        )
    )

    print()
    print(
        "===================================="
    )

    print(
        " SHORT CHECK"
    )

    print(
        "===================================="
    )

    print(
        "1H Trend       : "
        + (
            "PASS"
            if h1_bear
            else "FAIL"
        )
    )

    print(
        "1H Early Trend : "
        + (
            "PASS"
            if h1_early_bear
            else "FAIL"
        )
    )

    print(
        f"Normal Score   : "
        f"{normal_short_score}/6"
    )

    print(
        f"Early Score    : "
        f"{early_short_score}/6"
    )

    print(
        f"5M Score       : "
        f"{short_5m_score}/3"
    )

    print(
        "5M Confirm     : "
        + (
            "PASS"
            if short_5m
            else "FAIL"
        )
    )

    # =====================================================
    # NORMAL SIGNAL
    # =====================================================

    normal_long = (
        h1_bull
        and normal_long_score >= TREND_SCORE
        and long_5m
        and adx >= MIN_ADX
    )

    normal_short = (
        h1_bear
        and normal_short_score >= TREND_SCORE
        and short_5m
        and adx >= MIN_ADX
    )

    # =====================================================
    # EARLY SIGNAL
    # =====================================================

    early_long = (
        h1_early_bull
        and early_long_score >= EARLY_SCORE
        and long_5m
        and adx >= EARLY_MIN_ADX
    )

    early_short = (
        h1_early_bear
        and early_short_score >= EARLY_SCORE
        and short_5m
        and adx >= EARLY_MIN_ADX
    )

    # =====================================================
    # SELECT SIGNAL
    # =====================================================

    direction = None
    signal_mode = None

    if normal_long:

        direction = "LONG"
        signal_mode = "TREND"

    elif normal_short:

        direction = "SHORT"
        signal_mode = "TREND"

    elif early_long:

        direction = "LONG"
        signal_mode = "EARLY"

    elif early_short:

        direction = "SHORT"
        signal_mode = "EARLY"

    else:

        print()
        print(
            "No valid signal found."
        )

        return None

    # =====================================================
    # ENTRY DISTANCE FILTER
    # =====================================================

    if not entry_distance_ok:

        print(
            "Signal rejected: "
            "price too far from EMA20."
        )

        return None

    # =====================================================
    # SIGNAL CANDLE SIZE FILTER
    # =====================================================

    signal_move = abs(
        m["Close"]
        - m["Open"]
    )

    signal_move_atr = (
        signal_move / atr
    )

    if (
        signal_move_atr
        > MAX_SIGNAL_MOVE_ATR
    ):

        print(
            "Signal rejected: "
            "candle moved too far."
        )

        return None

    # =====================================================
    # STOP LOSS
    # =====================================================

    recent_lows = (
        m15["Low"]
        .iloc[-6:-1]
    )

    recent_highs = (
        m15["High"]
        .iloc[-6:-1]
    )

    if direction == "LONG":

        swing_low = float(
            recent_lows.min()
        )

        sl = (
            swing_low
            - atr * 0.35
        )

        risk = (
            price - sl
        )

    else:

        swing_high = float(
            recent_highs.max()
        )

        sl = (
            swing_high
            + atr * 0.35
        )

        risk = (
            sl - price
        )

    risk_atr = (
        risk / atr
    )

    # =====================================================
    # RISK FILTER
    # =====================================================

    if (
        risk_atr < MIN_SL_ATR
        or risk_atr > MAX_SL_ATR
    ):

        print(
            f"Signal rejected: "
            f"SL risk {risk_atr:.2f} ATR"
        )

        return None

    # =====================================================
    # TARGET
    # =====================================================

    if direction == "LONG":

        tp1 = (
            price
            + risk * TP1_R
        )

        tp2 = (
            price
            + risk * TP2_R
        )

        tp3 = (
            price
            + risk * TP3_R
        )

    else:

        tp1 = (
            price
            - risk * TP1_R
        )

        tp2 = (
            price
            - risk * TP2_R
        )

        tp3 = (
            price
            - risk * TP3_R
        )

    # =====================================================
    # TIME
    # =====================================================

    signal_time = (
        normalize_timestamp(
            m15.index[-2]
        )
    )

    entry_time = (
        signal_time
        + pd.Timedelta(
            minutes=15
        )
    )

    # =====================================================
    # SIGNAL OBJECT
    # =====================================================

    signal = {

        "status":
            "active",

        "direction":
            direction,

        "signal_mode":
            signal_mode,

        "entry":
            round(
                price,
                2
            ),

        "sl":
            round(
                sl,
                2
            ),

        "initial_sl":
            round(
                sl,
                2
            ),

        "tp1":
            round(
                tp1,
                2
            ),

        "tp2":
            round(
                tp2,
                2
            ),

        "tp3":
            round(
                tp3,
                2
            ),

        "risk":
            round(
                risk,
                2
            ),

        "risk_atr":
            round(
                risk_atr,
                2
            ),

        "rsi":
            round(
                float(m["RSI"]),
                2
            ),

        "adx":
            round(
                float(m["ADX"]),
                2
            ),

        "atr":
            round(
                float(m["ATR"]),
                2
            ),

        "normal_long_score":
            normal_long_score,

        "normal_short_score":
            normal_short_score,

        "early_long_score":
            early_long_score,

        "early_short_score":
            early_short_score,

        "long_5m_score":
            long_5m_score,

        "short_5m_score":
            short_5m_score,

        "signal_time":
            signal_time.isoformat(),

        "entry_time":
            entry_time.isoformat(),

        "tp1_hit":
            False,

        "tp2_hit":
            False,

        "tp3_hit":
            False,

        "entry_alert_sent":
            False,

        "created_at":
            datetime.now(
                timezone.utc
            ).isoformat()
    }

    # =====================================================
    # SIGNAL OUTPUT
    # =====================================================

    print()
    print(
        "===================================="
    )

    print(
        " SIGNAL FOUND"
    )

    print(
        "===================================="
    )

    print(
        f"Direction : {direction}"
    )

    print(
        f"Mode      : {signal_mode}"
    )

    print(
        f"Entry     : ${price:,.2f}"
    )

    print(
        f"SL        : ${sl:,.2f}"
    )

    print(
        f"TP1       : ${tp1:,.2f}"
    )

    print(
        f"TP2       : ${tp2:,.2f}"
    )

    print(
        f"TP3       : ${tp3:,.2f}"
    )

    return signal


# =========================================================
# TELEGRAM ENTRY MESSAGE
# =========================================================

def format_entry_message(signal):

    if signal["direction"] == "LONG":

        emoji = "🟢"

    else:

        emoji = "🔴"

    return f"""
{emoji} GOLD FUTURES SIGNAL

▪ Direction: {signal["direction"]}
▪ Mode: {signal["signal_mode"]}

▪ ENTRY: ${signal["entry"]:,.2f}
▪ SL: ${signal["sl"]:,.2f}

▪ TP1: ${signal["tp1"]:,.2f}
▪ TP2: ${signal["tp2"]:,.2f}
▪ TP3: ${signal["tp3"]:,.2f}

━━━━━━━━━━━━━━━━━━

▪ RSI: {signal["rsi"]:.2f}
▪ ADX: {signal["adx"]:.2f}
▪ ATR: {signal["atr"]:.2f}

▪ Risk: {signal["risk_atr"]:.2f} ATR

▪ Signal time:
{format_kst_time(signal["signal_time"])}
""".strip()


# =========================================================
# POSITION MONITOR
# =========================================================

def monitor_position(
    state,
    data
):

    if (
        state.get("status")
        != "active"
    ):

        return state

    if "1m" not in data:

        return state

    entry_time = (
        normalize_timestamp(
            state["entry_time"]
        )
    )

    m1 = clean_dataframe(
        data["1m"]
    )

    m1 = m1[
        m1.index >= entry_time
    ]

    if m1.empty:

        return state

    direction = (
        state["direction"]
    )

    sl = float(
        state["sl"]
    )

    tp1 = float(
        state["tp1"]
    )

    tp2 = float(
        state["tp2"]
    )

    tp3 = float(
        state["tp3"]
    )

    # =====================================================
    # M1 MONITOR
    # =====================================================

    for timestamp, candle in m1.iterrows():

        high = float(
            candle["High"]
        )

        low = float(
            candle["Low"]
        )

        # =================================================
        # LONG
        # =================================================

        if direction == "LONG":

            # SL
            if low <= sl:

                send_telegram(
                    "🛑 GOLD FUTURES\n\n"
                    "LONG STOP LOSS HIT\n"
                    f"Price: ${sl:,.2f}"
                )

                state[
                    "status"
                ] = "cooldown"

                state[
                    "cooldown_until"
                ] = (
                    datetime.now(
                        timezone.utc
                    )
                    + timedelta(
                        minutes=
                        COOLDOWN_MINUTES
                    )
                ).isoformat()

                log_event({

                    "event":
                        "SL",

                    "direction":
                        direction,

                    "price":
                        sl,

                    "time":
                        timestamp.isoformat()
                })

                return state

            # TP1
            if (
                not state["tp1_hit"]
                and high >= tp1
            ):

                send_telegram(
                    "🎯 GOLD FUTURES\n\n"
                    "LONG TP1 HIT\n"
                    f"Price: ${tp1:,.2f}"
                )

                state[
                    "tp1_hit"
                ] = True

                log_event({

                    "event":
                        "TP1",

                    "direction":
                        direction,

                    "price":
                        tp1,

                    "time":
                        timestamp.isoformat()
                })

            # TP2
            if (
                not state["tp2_hit"]
                and high >= tp2
            ):

                send_telegram(
                    "🎯 GOLD FUTURES\n\n"
                    "LONG TP2 HIT\n"
                    f"Price: ${tp2:,.2f}"
                )

                state[
                    "tp2_hit"
                ] = True

                state[
                    "sl"
                ] = state[
                    "entry"
                ]

                log_event({

                    "event":
                        "TP2",

                    "direction":
                        direction,

                    "price":
                        tp2,

                    "time":
                        timestamp.isoformat()
                })

            # TP3
            if (
                not state["tp3_hit"]
                and high >= tp3
            ):

                send_telegram(
                    "🏆 GOLD FUTURES\n\n"
                    "LONG TP3 HIT\n"
                    f"Price: ${tp3:,.2f}"
                )

                state[
                    "tp3_hit"
                ] = True

                state[
                    "status"
                ] = "cooldown"

                state[
                    "cooldown_until"
                ] = (
                    datetime.now(
                        timezone.utc
                    )
                    + timedelta(
                        minutes=
                        COOLDOWN_MINUTES
                    )
                ).isoformat()

                log_event({

                    "event":
                        "TP3",

                    "direction":
                        direction,

                    "price":
                        tp3,

                    "time":
                        timestamp.isoformat()
                })

                return state

        # =================================================
        # SHORT
        # =================================================

        else:

            # SL
            if high >= sl:

                send_telegram(
                    "🛑 GOLD FUTURES\n\n"
                    "SHORT STOP LOSS HIT\n"
                    f"Price: ${sl:,.2f}"
                )

                state[
                    "status"
                ] = "cooldown"

                state[
                    "cooldown_until"
                ] = (
                    datetime.now(
                        timezone.utc
                    )
                    + timedelta(
                        minutes=
                        COOLDOWN_MINUTES
                    )
                ).isoformat()

                log_event({

                    "event":
                        "SL",

                    "direction":
                        direction,

                    "price":
                        sl,

                    "time":
                        timestamp.isoformat()
                })

                return state

            # TP1
            if (
                not state["tp1_hit"]
                and low <= tp1
            ):

                send_telegram(
                    "🎯 GOLD FUTURES\n\n"
                    "SHORT TP1 HIT\n"
                    f"Price: ${tp1:,.2f}"
                )

                state[
                    "tp1_hit"
                ] = True

                log_event({

                    "event":
                        "TP1",

                    "direction":
                        direction,

                    "price":
                        tp1,

                    "time":
                        timestamp.isoformat()
                })

            # TP2
            if (
                not state["tp2_hit"]
                and low <= tp2
            ):

                send_telegram(
                    "🎯 GOLD FUTURES\n\n"
                    "SHORT TP2 HIT\n"
                    f"Price: ${tp2:,.2f}"
                )

                state[
                    "tp2_hit"
                ] = True

                state[
                    "sl"
                ] = state[
                    "entry"
                ]

                log_event({

                    "event":
                        "TP2",

                    "direction":
                        direction,

                    "price":
                        tp2,

                    "time":
                        timestamp.isoformat()
                })

            # TP3
            if (
                not state["tp3_hit"]
                and low <= tp3
            ):

                send_telegram(
                    "🏆 GOLD FUTURES\n\n"
                    "SHORT TP3 HIT\n"
                    f"Price: ${tp3:,.2f}"
                )

                state[
                    "tp3_hit"
                ] = True

                state[
                    "status"
                ] = "cooldown"

                state[
                    "cooldown_until"
                ] = (
                    datetime.now(
                        timezone.utc
                    )
                    + timedelta(
                        minutes=
                        COOLDOWN_MINUTES
                    )
                ).isoformat()

                log_event({

                    "event":
                        "TP3",

                    "direction":
                        direction,

                    "price":
                        tp3,

                    "time":
                        timestamp.isoformat()
                })

                return state

    return state


# =========================================================
# MAIN
# =========================================================

def main():

    data = download_data()

    if not data:

        print(
            "No market data."
        )

        return

    state = load_state()

    # =====================================================
    # ACTIVE POSITION
    # =====================================================

    if (
        state.get("status")
        == "active"
    ):

        if not state.get(
            "entry_alert_sent",
            False
        ):

            message = (
                format_entry_message(
                    state
                )
            )

            sent = send_telegram(
                message
            )

            if sent:

                state[
                    "entry_alert_sent"
                ] = True

                save_state(
                    state
                )

        state = monitor_position(
            state,
            data
        )

        save_state(
            state
        )

        return

    # =====================================================
    # COOLDOWN
    # =====================================================

    if (
        state.get("status")
        == "cooldown"
    ):

        cooldown_until = (
            state.get(
                "cooldown_until"
            )
        )

        if cooldown_until:

            cooldown_time = (
                normalize_timestamp(
                    cooldown_until
                )
            )

            now = datetime.now(
                timezone.utc
            )

            if now < cooldown_time:

                remaining = (
                    cooldown_time
                    - now
                )

                print()
                print(
                    "Cooldown active."
                )

                print(
                    "Remaining:",
                    str(remaining)
                )

                return

        last_signal_time = (
            state.get(
                "signal_time"
            )
        )

        state = {

            "status":
                "idle",

            "last_signal_time":
                last_signal_time
        }

        save_state(
            state
        )

        print()
        print(
            "Cooldown finished."
        )

    # =====================================================
    # FIND NEW SIGNAL
    # =====================================================

    signal = find_signal(
        data
    )

    if signal is None:

        return

    # =====================================================
    # DUPLICATE PROTECTION
    # =====================================================

    last_signal_time = (
        state.get(
            "last_signal_time"
        )
    )

    if last_signal_time:

        if (
            signal["signal_time"]
            == last_signal_time
        ):

            print(
                "Duplicate signal blocked."
            )

            return

    # =====================================================
    # SAVE SIGNAL
    # =====================================================

    signal[
        "last_signal_time"
    ] = signal[
        "signal_time"
    ]

    save_state(
        signal
    )

    log_event({

        "event":
            "SIGNAL",

        "direction":
            signal["direction"],

        "mode":
            signal["signal_mode"],

        "entry":
            signal["entry"],

        "sl":
            signal["sl"],

        "tp1":
            signal["tp1"],

        "tp2":
            signal["tp2"],

        "tp3":
            signal["tp3"],

        "time":
            signal["created_at"]
    })

    # =====================================================
    # TELEGRAM
    # =====================================================

    message = (
        format_entry_message(
            signal
        )
    )

    sent = send_telegram(
        message
    )

    if sent:

        signal[
            "entry_alert_sent"
        ] = True

        save_state(
            signal
        )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    main()
