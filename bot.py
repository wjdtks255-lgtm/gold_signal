import os
import json
import requests
import yfinance as yf
import pandas as pd
import numpy as np

from datetime import timezone, timedelta


# ============================================================
# GOLD FUTURES SMART SIGNAL BOT V2
# BALANCED + REVERSAL DETECTION
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

TP1_R = 1.20
TP2_R = 2.00
TP3_R = 3.00

MIN_SL_ATR = 0.55
MAX_SL_ATR = 2.50

MAX_ENTRY_DISTANCE_ATR = 1.50
MAX_SIGNAL_MOVE_ATR = 0.80

MIN_ADX = 18.0

MIN_SIGNAL_SCORE = 4

COOLDOWN_MINUTES = 60

# Reversal settings
REVERSAL_MIN_SCORE = 4

# Prevent chasing an already extended RSI
MAX_LONG_RSI = 68.0
MIN_SHORT_RSI = 32.0


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials are missing.")
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )

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


def format_kst_time(iso_str):

    try:

        dt = pd.Timestamp(iso_str)

        if dt.tzinfo is None:
            dt = dt.tz_localize("UTC")

        dt = dt.tz_convert("Asia/Seoul")

        return dt.strftime(
            "%Y-%m-%d %H:%M:%S (KST)"
        )

    except Exception:

        return str(iso_str)


# ============================================================
# STATE
# ============================================================

def load_state():

    if not os.path.exists(STATE_FILE):
        return {}

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            state = json.load(f)

        return state if isinstance(state, dict) else {}

    except Exception as e:

        print(
            "State load error:",
            e
        )

        return {}


def save_state(state):

    try:

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

    except Exception as e:

        print(
            "State save error:",
            e
        )


# ============================================================
# LOG
# ============================================================

def log_event(event_type, data=None):

    logs = []

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

    if not isinstance(logs, list):
        logs = []

    logs.append(
        {
            "time": pd.Timestamp.now(
                tz="UTC"
            ).isoformat(),

            "event": event_type,

            "data": data or {}
        }
    )

    logs = logs[-500:]

    try:

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

    except Exception as e:

        print(
            "Log save error:",
            e
        )


# ============================================================
# DATA CLEANING
# ============================================================

def clean_dataframe(df):

    if df is None or df.empty:
        return None

    df = df.copy()

    if isinstance(
        df.columns,
        pd.MultiIndex
    ):

        df.columns = [
            col[0]
            if isinstance(col, tuple)
            else col
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

            df.index = (
                df.index.tz_localize("UTC")
            )

        else:

            df.index = (
                df.index.tz_convert("UTC")
            )

    except Exception:
        pass

    return df


# ============================================================
# DOWNLOAD DATA
# ============================================================

def download_data():

    print("Downloading market data...")

    try:

        h1 = clean_dataframe(
            yf.download(
                TICKER,
                period="30d",
                interval="1h",
                progress=False,
                auto_adjust=False,
                threads=False
            )
        )

        m15 = clean_dataframe(
            yf.download(
                TICKER,
                period="10d",
                interval="15m",
                progress=False,
                auto_adjust=False,
                threads=False
            )
        )

        m5 = clean_dataframe(
            yf.download(
                TICKER,
                period="5d",
                interval="5m",
                progress=False,
                auto_adjust=False,
                threads=False
            )
        )

        m1 = clean_dataframe(
            yf.download(
                TICKER,
                period="5d",
                interval="1m",
                progress=False,
                auto_adjust=False,
                threads=False
            )
        )

        if any(
            x is None
            for x in [
                h1,
                m15,
                m5,
                m1
            ]
        ):

            print(
                "One or more datasets unavailable."
            )

            return None

        # Remove unfinished candles
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

        print(
            "Download error:",
            e
        )

        return None


# ============================================================
# INDICATORS
# ============================================================

def add_indicators(df):

    df = df.copy()

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    open_price = df["Open"]

    # --------------------------------------------------------
    # EMA
    # --------------------------------------------------------

    df["EMA20"] = close.ewm(
        span=20,
        adjust=False
    ).mean()

    df["EMA50"] = close.ewm(
        span=50,
        adjust=False
    ).mean()

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    delta = close.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = gain.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    rs = (
        avg_gain
        /
        avg_loss.replace(
            0,
            np.nan
        )
    )

    df["RSI"] = (
        100
        -
        (
            100
            /
            (1 + rs)
        )
    )

    # --------------------------------------------------------
    # ATR
    # --------------------------------------------------------

    previous_close = close.shift(1)

    tr1 = high - low

    tr2 = (
        high
        - previous_close
    ).abs()

    tr3 = (
        low
        - previous_close
    ).abs()

    tr = pd.concat(
        [
            tr1,
            tr2,
            tr3
        ],
        axis=1
    ).max(axis=1)

    df["ATR"] = tr.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    # --------------------------------------------------------
    # ADX
    # --------------------------------------------------------

    up_move = high.diff()

    down_move = -low.diff()

    plus_dm = np.where(
        (
            up_move > down_move
        )
        &
        (
            up_move > 0
        ),
        up_move,
        0
    )

    minus_dm = np.where(
        (
            down_move > up_move
        )
        &
        (
            down_move > 0
        ),
        down_move,
        0
    )

    atr_adx = tr.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    plus_di = (
        100
        *
        pd.Series(
            plus_dm,
            index=df.index
        ).ewm(
            alpha=1 / 14,
            adjust=False
        ).mean()
        /
        atr_adx
    )

    minus_di = (
        100
        *
        pd.Series(
            minus_dm,
            index=df.index
        ).ewm(
            alpha=1 / 14,
            adjust=False
        ).mean()
        /
        atr_adx
    )

    denominator = (
        plus_di
        +
        minus_di
    ).replace(
        0,
        np.nan
    )

    dx = (
        100
        *
        (
            plus_di
            -
            minus_di
        ).abs()
        /
        denominator
    )

    df["ADX"] = dx.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    # --------------------------------------------------------
    # Candle structure
    # --------------------------------------------------------

    candle_range = (
        high - low
    ).replace(
        0,
        np.nan
    )

    df["BodyRatio"] = (
        (
            close
            -
            open_price
        ).abs()
        /
        candle_range
    )

    df["ClosePosition"] = (
        (
            close
            -
            low
        )
        /
        candle_range
    )

    return df


# ============================================================
# FIND SIGNAL
# ============================================================

def find_signal(h1, m15, m5):

    h1 = add_indicators(h1)
    m15 = add_indicators(m15)
    m5 = add_indicators(m5)

    if (
        len(h1) < 60
        or
        len(m15) < 60
        or
        len(m5) < 50
    ):

        print(
            "Not enough indicator data."
        )

        return None

    # ========================================================
    # CURRENT CANDLES
    # ========================================================

    h = h1.iloc[-1]
    h_prev = h1.iloc[-2]
    h_prev2 = h1.iloc[-3]

    p = m15.iloc[-2]
    c = m15.iloc[-1]

    p2 = m15.iloc[-3]

    latest_5m = m5.iloc[-1]
    previous_5m = m5.iloc[-2]

    # ========================================================
    # BASIC VALUES
    # ========================================================

    price = float(c["Close"])

    ema20 = float(c["EMA20"])
    ema50 = float(c["EMA50"])

    rsi = float(c["RSI"])
    adx = float(c["ADX"])
    atr = float(c["ATR"])

    values = [
        price,
        ema20,
        ema50,
        rsi,
        adx,
        atr
    ]

    if not all(
        np.isfinite(v)
        for v in values
    ):

        print(
            "Invalid indicator values."
        )

        return None

    if atr <= 0:
        return None

    # ========================================================
    # H1 TREND
    # ========================================================

    h1_bull = (
        h["Close"] > h["EMA20"]
        and
        h["EMA20"] > h["EMA50"]
        and
        h["EMA20"] >= h_prev["EMA20"]
    )

    h1_bear = (
        h["Close"] < h["EMA20"]
        and
        h["EMA20"] < h["EMA50"]
        and
        h["EMA20"] <= h_prev["EMA20"]
    )

    # ========================================================
    # H1 REVERSAL
    # ========================================================

    # Price has recovered above EMA20,
    # while EMA20 has not yet crossed EMA50.

    bullish_reversal = (
        h["Close"] > h["EMA20"]
        and
        h["EMA20"] >= h_prev["EMA20"]
        and
        h_prev["EMA20"] <= h_prev2["EMA20"]
        and
        h["EMA20"] <= h["EMA50"]
    )

    bearish_reversal = (
        h["Close"] < h["EMA20"]
        and
        h["EMA20"] <= h_prev["EMA20"]
        and
        h_prev["EMA20"] >= h_prev2["EMA20"]
        and
        h["EMA20"] >= h["EMA50"]
    )

    # ========================================================
    # ENTRY DISTANCE
    # ========================================================

    entry_distance = abs(
        price - ema20
    )

    if (
        entry_distance
        >
        atr * MAX_ENTRY_DISTANCE_ATR
    ):

        print(
            "Entry rejected: "
            "too far from EMA20."
        )

        return None

    # ========================================================
    # LONG SCORE
    # ========================================================

    long_score = 0

    # 1. Strong bullish candle
    if (
        c["Close"] > c["Open"]
        and
        c["BodyRatio"] >= 0.30
        and
        c["ClosePosition"] >= 0.55
    ):

        long_score += 1

    # 2. Break previous high
    if (
        c["Close"]
        >
        p["High"]
    ):

        long_score += 1

    # 3. RSI
    if (
        45 <= rsi <= MAX_LONG_RSI
    ):

        long_score += 1

    # 4. ADX
    if adx >= MIN_ADX:

        long_score += 1

    # 5. EMA recovery / structure
    if (
        c["Close"] > ema20
        and
        ema20 >= ema50
    ):

        long_score += 1

    # 6. 5M confirmation
    five_min_long = (
        latest_5m["Close"]
        >
        latest_5m["Open"]
        and
        latest_5m["Close"]
        >=
        latest_5m["EMA20"]
        and
        latest_5m["EMA20"]
        >=
        previous_5m["EMA20"]
    )

    if five_min_long:

        long_score += 1

    # ========================================================
    # SHORT SCORE
    # ========================================================

    short_score = 0

    # 1. Strong bearish candle
    if (
        c["Close"] < c["Open"]
        and
        c["BodyRatio"] >= 0.30
        and
        c["ClosePosition"] <= 0.45
    ):

        short_score += 1

    # 2. Break previous low
    if (
        c["Close"]
        <
        p["Low"]
    ):

        short_score += 1

    # 3. RSI
    if (
        MIN_SHORT_RSI
        <= rsi
        <= 52
    ):

        short_score += 1

    # 4. ADX
    if adx >= MIN_ADX:

        short_score += 1

    # 5. EMA recovery / structure
    if (
        c["Close"] < ema20
        and
        ema20 <= ema50
    ):

        short_score += 1

    # 6. 5M confirmation
    five_min_short = (
        latest_5m["Close"]
        <
        latest_5m["Open"]
        and
        latest_5m["Close"]
        <=
        latest_5m["EMA20"]
        and
        latest_5m["EMA20"]
        <=
        previous_5m["EMA20"]
    )

    if five_min_short:

        short_score += 1

    # ========================================================
    # REVERSAL SCORES
    # ========================================================

    bullish_reversal_score = 0

    if bullish_reversal:
        bullish_reversal_score += 1

    if (
        c["Close"] > c["Open"]
        and
        c["BodyRatio"] >= 0.30
        and
        c["ClosePosition"] >= 0.60
    ):
        bullish_reversal_score += 1

    if (
        c["Close"]
        >
        p["High"]
    ):
        bullish_reversal_score += 1

    if (
        50 <= rsi <= 68
    ):
        bullish_reversal_score += 1

    if adx >= MIN_ADX:
        bullish_reversal_score += 1

    if five_min_long:
        bullish_reversal_score += 1

    bearish_reversal_score = 0

    if bearish_reversal:
        bearish_reversal_score += 1

    if (
        c["Close"] < c["Open"]
        and
        c["BodyRatio"] >= 0.30
        and
        c["ClosePosition"] <= 0.40
    ):
        bearish_reversal_score += 1

    if (
        c["Close"]
        <
        p["Low"]
    ):
        bearish_reversal_score += 1

    if (
        32 <= rsi <= 50
    ):
        bearish_reversal_score += 1

    if adx >= MIN_ADX:
        bearish_reversal_score += 1

    if five_min_short:
        bearish_reversal_score += 1

    # ========================================================
    # MARKET CHECK
    # ========================================================

    print("")
    print("====================================")
    print(" CURRENT MARKET CHECK")
    print("====================================")

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

    print("")
    print("====================================")
    print(" LONG CHECK")
    print("====================================")

    print(
        f"1H Trend      : "
        f"{'PASS' if h1_bull else 'FAIL'}"
    )

    print(
        f"1H Reversal   : "
        f"{'PASS' if bullish_reversal else 'FAIL'}"
    )

    print(
        f"Normal Score  : "
        f"{long_score}/6"
    )

    print(
        f"Reversal Score: "
        f"{bullish_reversal_score}/6"
    )

    print(
        f"5M Confirm    : "
        f"{'PASS' if five_min_long else 'FAIL'}"
    )

    print("")
    print("====================================")
    print(" SHORT CHECK")
    print("====================================")

    print(
        f"1H Trend      : "
        f"{'PASS' if h1_bear else 'FAIL'}"
    )

    print(
        f"1H Reversal   : "
        f"{'PASS' if bearish_reversal else 'FAIL'}"
    )

    print(
        f"Normal Score  : "
        f"{short_score}/6"
    )

    print(
        f"Reversal Score: "
        f"{bearish_reversal_score}/6"
    )

    print(
        f"5M Confirm    : "
        f"{'PASS' if five_min_short else 'FAIL'}"
    )

    # ========================================================
    # FINAL LONG CONDITIONS
    # ========================================================

    normal_long = (
        h1_bull
        and
        long_score >= MIN_SIGNAL_SCORE
        and
        five_min_long
    )

    reversal_long = (
        bullish_reversal
        and
        bullish_reversal_score
        >= REVERSAL_MIN_SCORE
        and
        five_min_long
    )

    # ========================================================
    # FINAL SHORT CONDITIONS
    # ========================================================

    normal_short = (
        h1_bear
        and
        short_score >= MIN_SIGNAL_SCORE
        and
        five_min_short
    )

    reversal_short = (
        bearish_reversal
        and
        bearish_reversal_score
        >= REVERSAL_MIN_SCORE
        and
        five_min_short
    )

    # ========================================================
    # DIRECTION
    # ========================================================

    if normal_long or reversal_long:

        direction = "LONG"

        is_reversal = (
            reversal_long
            and not normal_long
        )

        selected_score = (
            bullish_reversal_score
            if is_reversal
            else long_score
        )

        if is_reversal:

            reason = (
                "1시간 하락 후 반전 + "
                f"반전점수 {selected_score}/6 + "
                "5분 상승 확인"
            )

        else:

            reason = (
                "1시간 상승 추세 + "
                f"점수 {selected_score}/6 + "
                "5분 상승 확인"
            )

    elif normal_short or reversal_short:

        direction = "SHORT"

        is_reversal = (
            reversal_short
            and not normal_short
        )

        selected_score = (
            bearish_reversal_score
            if is_reversal
            else short_score
        )

        if is_reversal:

            reason = (
                "1시간 상승 후 반전 + "
                f"반전점수 {selected_score}/6 + "
                "5분 하락 확인"
            )

        else:

            reason = (
                "1시간 하락 추세 + "
                f"점수 {selected_score}/6 + "
                "5분 하락 확인"
            )

    else:

        print("")
        print(
            "No valid signal found."
        )

        return None

    # ========================================================
    # SL
    # ========================================================

    if direction == "LONG":

        swing_low = float(
            m15["Low"]
            .iloc[-5:-1]
            .min()
        )

        sl = (
            swing_low
            -
            atr * 0.35
        )

    else:

        swing_high = float(
            m15["High"]
            .iloc[-5:-1]
            .max()
        )

        sl = (
            swing_high
            +
            atr * 0.35
        )

    # ========================================================
    # RISK
    # ========================================================

    risk = abs(
        price - sl
    )

    if (
        risk
        <
        atr * MIN_SL_ATR
    ):

        print(
            "Signal rejected: SL too close."
        )

        return None

    if (
        risk
        >
        atr * MAX_SL_ATR
    ):

        print(
            "Signal rejected: SL too far."
        )

        return None

    # ========================================================
    # 5M PRICE DISTANCE
    # ========================================================

    five_min_price = float(
        latest_5m["Close"]
    )

    if (
        abs(
            five_min_price
            -
            price
        )
        >
        atr * MAX_SIGNAL_MOVE_ATR
    ):

        print(
            "Signal rejected: "
            "5M price moved too far."
        )

        return None

    # ========================================================
    # TARGETS
    # ========================================================

    if direction == "LONG":

        tp1 = (
            price
            +
            risk * TP1_R
        )

        tp2 = (
            price
            +
            risk * TP2_R
        )

        tp3 = (
            price
            +
            risk * TP3_R
        )

    else:

        tp1 = (
            price
            -
            risk * TP1_R
        )

        tp2 = (
            price
            -
            risk * TP2_R
        )

        tp3 = (
            price
            -
            risk * TP3_R
        )

    # ========================================================
    # TIME
    # ========================================================

    signal_time = normalize_timestamp(
        m15.index[-1]
    )

    entry_time = (
        signal_time
        +
        pd.Timedelta(
            minutes=15
        )
    )

    # ========================================================
    # SIGNAL
    # ========================================================

    signal = {

        "status": "active",

        "direction": direction,

        "entry": float(price),

        "sl": float(sl),

        "initial_sl": float(sl),

        "tp1": float(tp1),

        "tp2": float(tp2),

        "tp3": float(tp3),

        "risk": float(risk),

        "rsi": float(rsi),

        "adx": float(adx),

        "atr": float(atr),

        "long_score": int(long_score),

        "short_score": int(short_score),

        "reversal_score": int(
            bullish_reversal_score
            if direction == "LONG"
            else bearish_reversal_score
        ),

        "is_reversal": bool(
            is_reversal
        ),

        "reason": reason,

        "signal_time": (
            signal_time.isoformat()
        ),

        "entry_time": (
            entry_time.isoformat()
        ),

        "tp1_hit": False,

        "tp2_hit": False,

        "tp3_hit": False,

        "entry_alert_sent": False,

        "created_at": (
            pd.Timestamp.now(
                tz="UTC"
            ).isoformat()
        )
    }

    # ========================================================
    # RESULT
    # ========================================================

    print("")
    print("====================================")
    print(" SIGNAL CREATED")
    print("====================================")

    print(
        f"Direction : {direction}"
    )

    print(
        f"Type      : "
        f"{'REVERSAL' if is_reversal else 'TREND'}"
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

    print(
        f"Risk      : ${risk:,.2f}"
    )

    return signal


# ============================================================
# ENTRY MESSAGE
# ============================================================

def format_entry_message(signal):

    direction = signal["direction"]

    if direction == "LONG":

        emoji = "🟢"
        title = "LONG BUY SIGNAL"

    else:

        emoji = "🔴"
        title = "SHORT SELL SIGNAL"

    if signal["is_reversal"]:

        signal_type = (
            "🔄 REVERSAL"
        )

    else:

        signal_type = (
            "📈 TREND"
        )

    score = max(
        signal["long_score"],
        signal["short_score"],
        signal["reversal_score"]
    )

    return f"""
👑 <b>GOLD FUTURES SMART SIGNAL</b>

{emoji} <b>{title}</b>
{signal_type}

━━━━━━━━━━━━━━━━━━

💵 <b>ENTRY</b>
<code>${signal['entry']:,.2f}</code>

🛡️ <b>STOP LOSS</b>
<code>${signal['sl']:,.2f}</code>

🎯 <b>TP1</b>
<code>${signal['tp1']:,.2f}</code>

🎯 <b>TP2</b>
<code>${signal['tp2']:,.2f}</code>

🏆 <b>TP3</b>
<code>${signal['tp3']:,.2f}</code>

━━━━━━━━━━━━━━━━━━

📊 <b>MARKET</b>

• RSI : <code>{signal['rsi']:.2f}</code>
• ADX : <code>{signal['adx']:.2f}</code>
• ATR : <code>{signal['atr']:.2f}</code>
• Score : <code>{score}/6</code>
• Risk : <code>${signal['risk']:.2f}</code>

━━━━━━━━━━━━━━━━━━

🧠 <b>REASON</b>

{signal['reason']}

━━━━━━━━━━━━━━━━━━

🕐 <b>TIME</b>

<code>{format_kst_time(signal['signal_time'])}</code>

📊 <a href="https://www.tradingview.com/symbols/GC1!/">TradingView Gold Chart</a>
"""


# ============================================================
# POSITION MONITOR
# ============================================================

def monitor_position(state, m1):

    if state.get("status") != "active":

        return state

    direction = state["direction"]

    entry = float(
        state["entry"]
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

    entry_time = normalize_timestamp(
        state["entry_time"]
    )

    data = m1[
        m1.index >= entry_time
    ].copy()

    if data.empty:

        return state

    for idx, row in data.iterrows():

        high = float(
            row["High"]
        )

        low = float(
            row["Low"]
        )

        candle_time = normalize_timestamp(
            idx
        )

        # ====================================================
        # LONG
        # ====================================================

        if direction == "LONG":

            if low <= sl:

                send_telegram(
                    f"""🛑 <b>LONG STOP LOSS</b>

ENTRY:
<code>${entry:,.2f}</code>

SL:
<code>${sl:,.2f}</code>

TIME:
<code>{format_kst_time(candle_time.isoformat())}</code>"""
                )

                log_event(
                    "LONG_SL",
                    state
                )

                state["status"] = (
                    "cooldown"
                )

                state[
                    "cooldown_until"
                ] = (
                    pd.Timestamp.now(
                        tz="UTC"
                    )
                    +
                    pd.Timedelta(
                        minutes=COOLDOWN_MINUTES
                    )
                ).isoformat()

                save_state(state)

                return state

            if (
                not state["tp1_hit"]
                and
                high >= tp1
            ):

                send_telegram(
                    f"""🎯 <b>LONG TP1 HIT</b>

TP1:
<code>${tp1:,.2f}</code>"""
                )

                state["tp1_hit"] = True

                log_event(
                    "LONG_TP1",
                    state
                )

                save_state(state)

            if (
                not state["tp2_hit"]
                and
                high >= tp2
            ):

                send_telegram(
                    f"""🎯 <b>LONG TP2 HIT</b>

TP2:
<code>${tp2:,.2f}</code>

🔒 STOP moved to ENTRY.

ENTRY:
<code>${entry:,.2f}</code>"""
                )

                state["tp2_hit"] = True

                state["sl"] = entry

                log_event(
                    "LONG_TP2",
                    state
                )

                save_state(state)

            if (
                not state["tp3_hit"]
                and
                high >= tp3
            ):

                send_telegram(
                    f"""🏆 <b>LONG TP3 HIT</b>

TP3:
<code>${tp3:,.2f}</code>

Position completed."""
                )

                state["tp3_hit"] = True

                state["status"] = (
                    "cooldown"
                )

                state[
                    "cooldown_until"
                ] = (
                    candle_time
                    +
                    pd.Timedelta(
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

            if high >= sl:

                send_telegram(
                    f"""🛑 <b>SHORT STOP LOSS</b>

ENTRY:
<code>${entry:,.2f}</code>

SL:
<code>${sl:,.2f}</code>

TIME:
<code>{format_kst_time(candle_time.isoformat())}</code>"""
                )

                log_event(
                    "SHORT_SL",
                    state
                )

                state["status"] = (
                    "cooldown"
                )

                state[
                    "cooldown_until"
                ] = (
                    pd.Timestamp.now(
                        tz="UTC"
                    )
                    +
                    pd.Timedelta(
                        minutes=COOLDOWN_MINUTES
                    )
                ).isoformat()

                save_state(state)

                return state

            if (
                not state["tp1_hit"]
                and
                low <= tp1
            ):

                send_telegram(
                    f"""🎯 <b>SHORT TP1 HIT</b>

TP1:
<code>${tp1:,.2f}</code>"""
                )

                state["tp1_hit"] = True

                log_event(
                    "SHORT_TP1",
                    state
                )

                save_state(state)

            if (
                not state["tp2_hit"]
                and
                low <= tp2
            ):

                send_telegram(
                    f"""🎯 <b>SHORT TP2 HIT</b>

TP2:
<code>${tp2:,.2f}</code>

🔒 STOP moved to ENTRY.

ENTRY:
<code>${entry:,.2f}</code>"""
                )

                state["tp2_hit"] = True

                state["sl"] = entry

                log_event(
                    "SHORT_TP2",
                    state
                )

                save_state(state)

            if (
                not state["tp3_hit"]
                and
                low <= tp3
            ):

                send_telegram(
                    f"""🏆 <b>SHORT TP3 HIT</b>

TP3:
<code>${tp3:,.2f}</code>

Position completed."""
                )

                state["tp3_hit"] = True

                state["status"] = (
                    "cooldown"
                )

                state[
                    "cooldown_until"
                ] = (
                    candle_time
                    +
                    pd.Timedelta(
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

    print("=================================")
    print(" GOLD FUTURES SMART SIGNAL BOT")
    print(" BALANCED REVERSAL V2")
    print("=================================")

    state = load_state()

    data = download_data()

    if data is None:

        return

    h1, m15, m5, m1 = data

    # ========================================================
    # ACTIVE
    # ========================================================

    if state.get("status") == "active":

        print(
            "Active position found."
        )

        # Retry Telegram entry if previous run failed
        if not state.get(
            "entry_alert_sent",
            False
        ):

            message = format_entry_message(
                state
            )

            if send_telegram(message):

                state[
                    "entry_alert_sent"
                ] = True

                save_state(state)

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

            cooldown_time = (
                normalize_timestamp(
                    cooldown_until
                )
            )

            if now < cooldown_time:

                print("")
                print(
                    "Cooldown active."
                )

                print(
                    f"Until: "
                    f"{cooldown_time}"
                )

                return

        print("")
        print(
            "Cooldown finished."
        )

        # Preserve last signal candle
        last_signal_time = state.get(
            "last_signal_time"
        )

        state = {
            "status": "idle",
            "last_signal_time":
                last_signal_time
        }

        save_state(state)

    # ========================================================
    # NEW SIGNAL
    # ========================================================

    signal = find_signal(
        h1,
        m15,
        m5
    )

    if signal is None:

        return

    # ========================================================
    # DUPLICATE PROTECTION
    # ========================================================

    current_state = load_state()

    last_signal_time = (
        current_state.get(
            "last_signal_time"
        )
    )

    if (
        last_signal_time
        ==
        signal["signal_time"]
    ):

        print(
            "Duplicate signal candle blocked."
        )

        return

    signal[
        "last_signal_time"
    ] = signal["signal_time"]

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

    if send_telegram(message):

        signal[
            "entry_alert_sent"
        ] = True

        save_state(signal)

    print("")
    print("=================================")
    print(" SIGNAL SENT")
    print("=================================")


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
