# ============================================================
# GOLD FUTURES SMART SIGNAL BOT V17.1.0
# ONE POSITION LOCK
#
# GC=F / Gold Futures
#
# FEATURES
# ------------------------------------------------------------
# - H1 / M15 / 5M / 1M Multi-Timeframe
# - M15 strong setup scoring
# - 5M confirmation
# - H1 trend filter
# - Hard ADX filter
# - ATR volatility filter
# - ONE POSITION ONLY
# - TP1 -> SL to ENTRY
# - TP2 -> SL to TP1
# - TP3 -> FINAL EXIT
# - 1M HIGH / LOW TP-SL monitoring
# - Missed 1M candle catch-up
# - Conservative same-candle SL priority
# - Persistent state
# - V13/V14 state migration
# - Atomic JSON writes
# - Telegram HTML escaping
# - Yahoo Finance retry
# - Data freshness protection
#
# GitHub Actions:
#   Run every 5 minutes
# ============================================================

import os
import json
import time
import math
import html
import tempfile
from datetime import datetime, timezone, timedelta

import requests
import numpy as np
import pandas as pd
import yfinance as yf


# ============================================================
# BASIC SETTINGS
# ============================================================

VERSION = "17.1.0"

TICKER = "GC=F"

STATE_FILE = "signal_state.json"
LOG_FILE = "bot_log.json"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

KST = timezone(timedelta(hours=9))

TRADINGVIEW_URL = "https://www.tradingview.com/symbols/GC1!/"


# ============================================================
# SIGNAL SETTINGS
# ============================================================

MIN_M15_SCORE = 7
STRONG_M15_SCORE = 8

MIN_5M_SCORE = 3

MIN_ADX = 17.0

MIN_ATR = 0.50
MAX_ATR = 20.0

REQUIRE_M15_CONFIRMATION = True


# ============================================================
# ENTRY FILTERS
# ============================================================

LONG_RSI_MIN = 52.0
LONG_RSI_MAX = 68.0

SHORT_RSI_MIN = 32.0
SHORT_RSI_MAX = 48.0

MIN_BODY_RATIO = 0.35

MIN_EMA_SEPARATION_ATR = 0.10

MAX_ENTRY_DISTANCE_ATR = 1.20


# ============================================================
# COOLDOWN
# ============================================================

SIGNAL_COOLDOWN_MINUTES = 45
SL_COOLDOWN_MINUTES = 120
EXIT_COOLDOWN_MINUTES = 15


# ============================================================
# RISK / TARGET
# ============================================================

ENTRY_RISK_ATR = 1.80

MIN_RISK_ATR = 1.20
MAX_RISK_ATR = 2.80

TP1_R = 1.20
TP2_R = 2.00
TP3_R = 3.00

STRONG_TP1_R = 1.30
STRONG_TP2_R = 2.20
STRONG_TP3_R = 3.50


# ============================================================
# DATA SETTINGS
# ============================================================

DATA_RETRIES = 3

DATA_RETRY_BASE_SLEEP = 2

MAX_DELAY_1M_MINUTES = 8
MAX_DELAY_5M_MINUTES = 12
MAX_DELAY_15M_MINUTES = 25


# ============================================================
# HELPERS
# ============================================================

def now_kst():
    return datetime.now(KST)


def iso_now():
    return now_kst().isoformat()


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default

        value = float(value)

        if not math.isfinite(value):
            return default

        return value

    except Exception:
        return default


def fmt_price(value):
    return f"{safe_float(value):,.2f}"


def parse_timestamp(value):
    if value is None:
        return None

    try:
        ts = pd.Timestamp(value)

        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")

        ts = ts.tz_convert(KST)

        return ts.to_pydatetime()

    except Exception:
        return None


def timestamp_iso(value):
    dt = parse_timestamp(value)

    if dt is None:
        return ""

    return dt.isoformat()


def escape_html(value):
    return html.escape(str(value), quote=False)


# ============================================================
# ATOMIC JSON
# ============================================================

def atomic_write_json(path, data):
    directory = os.path.dirname(os.path.abspath(path))

    os.makedirs(directory, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(
        prefix=".tmp_",
        suffix=".json",
        dir=directory
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_path, path)

    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


# ============================================================
# DEFAULT STATE
# ============================================================

def default_state():
    return {
        "version": VERSION,

        "status": "FLAT",

        "direction": "",

        "entry": 0.0,
        "sl": 0.0,

        "tp1": 0.0,
        "tp2": 0.0,
        "tp3": 0.0,

        "risk": 0.0,

        "stage": "INITIAL",

        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,

        "signal_time": "",
        "signal_id": "",

        "m15_score": 0,
        "five_score": 0,

        "rsi": 0.0,
        "adx": 0.0,

        "h1_trend": "",

        "last_monitor_time": "",

        "last_price": 0.0,

        "last_exit_time": "",
        "last_exit_reason": "",
        "last_exit_direction": "",

        "last_signal_time": "",
        "last_sl_time": "",
        "last_sl_direction": "",

        "last_setup_id": "",

        "exit_r": 0.0
    }


# ============================================================
# STATE LOAD / MIGRATION
# ============================================================

def load_state():
    state = default_state()

    if not os.path.exists(STATE_FILE):
        return state

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)

        if not isinstance(raw, dict):
            return state

        state.update(raw)

    except Exception as e:
        print(f"[STATE] Load failed: {e}")
        return state

    # --------------------------------------------------------
    # OLD VERSION MIGRATION
    # --------------------------------------------------------

    old_version = str(state.get("version", ""))

    # V13/V14 used tp1_hit / tp2_hit
    if state.get("status") == "ACTIVE":

        tp1_hit = bool(state.get("tp1_hit", False))
        tp2_hit = bool(state.get("tp2_hit", False))

        if tp2_hit:

            state["stage"] = "TP2_TRAIL"

            # V17 management:
            # TP2 hit -> SL moves to TP1
            if safe_float(state.get("tp1")) > 0:
                state["sl"] = safe_float(state["tp1"])

        elif tp1_hit:

            state["stage"] = "TP1_BE"

            # TP1 hit -> SL moves to entry
            if safe_float(state.get("entry")) > 0:
                state["sl"] = safe_float(state["entry"])

        else:

            state["stage"] = "INITIAL"

    else:

        state["stage"] = state.get("stage", "INITIAL")

    state["version"] = VERSION

    # Missing fields from older versions
    for key, value in default_state().items():

        if key not in state:
            state[key] = value

    return state


def save_state(state):
    state["version"] = VERSION
    atomic_write_json(STATE_FILE, state)


# ============================================================
# LOG
# ============================================================

def load_logs():
    if not os.path.exists(LOG_FILE):
        return []

    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            return data

    except Exception:
        pass

    return []


def append_log(event, extra=None):
    logs = load_logs()

    item = {
        "time": iso_now(),
        "event": event
    }

    if extra:
        item.update(extra)

    logs.append(item)

    # Keep reasonable size
    logs = logs[-5000:]

    try:
        atomic_write_json(LOG_FILE, logs)
    except Exception as e:
        print(f"[LOG] Save failed: {e}")


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Token/chat ID missing")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_TOKEN}/sendMessage"
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
            timeout=15
        )

        if response.ok:
            return True

        print(
            f"[TELEGRAM] Failed "
            f"{response.status_code}: {response.text[:300]}"
        )

    except Exception as e:
        print(f"[TELEGRAM] Exception: {e}")

    return False


# ============================================================
# DATAFRAME CLEANING
# ============================================================

def clean_dataframe(df):
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()

    # yfinance can return MultiIndex
    if isinstance(df.columns, pd.MultiIndex):

        new_columns = []

        for col in df.columns:

            if isinstance(col, tuple):

                # Usually:
                # ('Close', 'GC=F')
                # ('Open', 'GC=F')
                new_columns.append(str(col[0]))

            else:
                new_columns.append(str(col))

        df.columns = new_columns

    df.columns = [
        str(c).strip().lower()
        for c in df.columns
    ]

    required = [
        "open",
        "high",
        "low",
        "close",
        "volume"
    ]

    for col in required:

        if col not in df.columns:

            if col == "volume":

                df[col] = 0

            else:

                return pd.DataFrame()

    for col in required:

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = df.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close"
        ]
    )

    df = df.sort_index()

    return df


# ============================================================
# DOWNLOAD DATA
# ============================================================

def download_data(interval, period):
    last_error = None

    for attempt in range(1, DATA_RETRIES + 1):

        try:

            print(
                f"[DATA] {interval} "
                f"attempt {attempt}/{DATA_RETRIES}"
            )

            df = yf.download(
                TICKER,
                period=period,
                interval=interval,
                auto_adjust=False,
                progress=False,
                threads=False
            )

            df = clean_dataframe(df)

            if not df.empty:

                print(
                    f"[DATA] {interval} "
                    f"{len(df)} rows"
                )

                return df

        except Exception as e:

            last_error = e

            print(
                f"[DATA] {interval} error: {e}"
            )

        if attempt < DATA_RETRIES:
            time.sleep(
                DATA_RETRY_BASE_SLEEP * attempt
            )

    print(
        f"[DATA] {interval} failed: "
        f"{last_error}"
    )

    return pd.DataFrame()


# ============================================================
# COMPLETED CANDLES
# ============================================================

def completed(df):
    if df.empty:
        return df

    # Remove currently forming candle.
    #
    # yfinance occasionally returns a partial last candle.
    # We intentionally use completed candles only.
    return df.iloc[:-1].copy()


# ============================================================
# DATA FRESHNESS
# ============================================================

def data_delay_minutes(df):
    if df.empty:
        return 9999.0

    try:

        last_ts = parse_timestamp(df.index[-1])

        if last_ts is None:
            return 9999.0

        delay = (
            now_kst() - last_ts
        ).total_seconds() / 60.0

        return max(0.0, delay)

    except Exception:
        return 9999.0


def check_freshness(name, df, max_delay):
    delay = data_delay_minutes(df)

    print(
        f"[DATA FRESHNESS] "
        f"{name}: {delay:.1f} min"
    )

    if delay > max_delay:

        print(
            f"[DATA DELAY] "
            f"{name} is stale"
        )

        return False

    return True


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

    return result.fillna(50)


def atr(df, length=14):

    high = df["high"]
    low = df["low"]
    close = df["close"]

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

    high = df["high"]
    low = df["low"]
    close = df["close"]

    up_move = high.diff()

    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where(
            (up_move > down_move) &
            (up_move > 0),
            up_move,
            0
        ),
        index=df.index
    )

    minus_dm = pd.Series(
        np.where(
            (down_move > up_move) &
            (down_move > 0),
            down_move,
            0
        ),
        index=df.index
    )

    prev_close = close.shift(1)

    tr1 = high - low

    tr2 = (high - prev_close).abs()

    tr3 = (low - prev_close).abs()

    tr = pd.concat(
        [tr1, tr2, tr3],
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
        ).mean() /
        atr_value.replace(0, np.nan)
    )

    minus_di = (
        100 *
        minus_dm.ewm(
            alpha=1 / length,
            adjust=False
        ).mean() /
        atr_value.replace(0, np.nan)
    )

    dx = (
        100 *
        (plus_di - minus_di).abs() /
        (plus_di + minus_di).replace(0, np.nan)
    )

    result = dx.ewm(
        alpha=1 / length,
        adjust=False
    ).mean()

    return result.fillna(0)


def add_indicators(df):

    df = df.copy()

    df["ema20"] = ema(
        df["close"],
        20
    )

    df["ema50"] = ema(
        df["close"],
        50
    )

    ema1 = ema(
        df["close"],
        20
    )

    ema2 = ema(
        ema1,
        20
    )

    # Zero-lag EMA approximation
    df["zlema20"] = (
        2 * ema1 - ema2
    )

    df["rsi"] = rsi(
        df["close"],
        14
    )

    df["atr"] = atr(
        df,
        14
    )

    df["adx"] = adx(
        df,
        14
    )

    # EMA / ZLEMA slope
    df["ema_slope"] = (
        df["ema20"] -
        df["ema20"].shift(3)
    )

    df["zlema_slope"] = (
        df["zlema20"] -
        df["zlema20"].shift(3)
    )

    # Candle body quality
    candle_range = (
        df["high"] -
        df["low"]
    ).replace(0, np.nan)

    body = (
        df["close"] -
        df["open"]
    ).abs()

    df["body_ratio"] = (
        body / candle_range
    ).fillna(0)

    # Close position inside candle
    df["close_pos"] = (
        (df["close"] - df["low"]) /
        candle_range
    ).fillna(0.5)

    # Bollinger Bands
    bb_mid = (
        df["close"]
        .rolling(20)
        .mean()
    )

    bb_std = (
        df["close"]
        .rolling(20)
        .std()
    )

    df["bb_upper"] = (
        bb_mid + 2.0 * bb_std
    )

    df["bb_lower"] = (
        bb_mid - 2.0 * bb_std
    )

    # Keltner Channel
    df["kc_mid"] = df["ema20"]

    df["kc_upper"] = (
        df["ema20"] +
        1.5 * df["atr"]
    )

    df["kc_lower"] = (
        df["ema20"] -
        1.5 * df["atr"]
    )

    # Squeeze
    df["squeeze"] = (
        (df["bb_lower"] > df["kc_lower"]) &
        (df["bb_upper"] < df["kc_upper"])
    )

    return df


# ============================================================
# M15 ANALYSIS
# ============================================================

def analyze_m15(df):

    if df.empty or len(df) < 100:
        return None

    row = df.iloc[-1]

    close = safe_float(row["close"])
    ema20 = safe_float(row["ema20"])
    ema50 = safe_float(row["ema50"])
    zlema20 = safe_float(row["zlema20"])

    rsi_value = safe_float(row["rsi"])
    adx_value = safe_float(row["adx"])
    atr_value = safe_float(row["atr"])

    body_ratio = safe_float(
        row["body_ratio"]
    )

    close_pos = safe_float(
        row["close_pos"]
    )

    ema_slope = safe_float(
        row["ema_slope"]
    )

    zlema_slope = safe_float(
        row["zlema_slope"]
    )

    score_long = 0
    score_short = 0

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if close > ema20:
        score_long += 1

    if close > zlema20:
        score_long += 1

    if ema20 > ema50:
        score_long += 1

    if zlema_slope > 0:
        score_long += 1

    if 52 <= rsi_value <= 68:
        score_long += 1

    if (
        body_ratio >= MIN_BODY_RATIO and
        close_pos >= 0.60
    ):
        score_long += 1

    if adx_value >= MIN_ADX:
        score_long += 1

    if (
        ema20 - ema50
    ) >= (
        atr_value *
        MIN_EMA_SEPARATION_ATR
    ):
        score_long += 1

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if close < ema20:
        score_short += 1

    if close < zlema20:
        score_short += 1

    if ema20 < ema50:
        score_short += 1

    if zlema_slope < 0:
        score_short += 1

    if 32 <= rsi_value <= 48:
        score_short += 1

    if (
        body_ratio >= MIN_BODY_RATIO and
        close_pos <= 0.40
    ):
        score_short += 1

    if adx_value >= MIN_ADX:
        score_short += 1

    if (
        ema50 - ema20
    ) >= (
        atr_value *
        MIN_EMA_SEPARATION_ATR
    ):
        score_short += 1

    return {
        "timestamp": timestamp_iso(df.index[-1]),

        "close": close,

        "ema20": ema20,
        "ema50": ema50,
        "zlema20": zlema20,

        "rsi": rsi_value,
        "adx": adx_value,
        "atr": atr_value,

        "body_ratio": body_ratio,
        "close_pos": close_pos,

        "ema_slope": ema_slope,
        "zlema_slope": zlema_slope,

        "squeeze": bool(row["squeeze"]),

        "long_score": score_long,
        "short_score": score_short
    }


# ============================================================
# M15 CONFIRMATION
# ============================================================

def m15_confirmation(df, direction):

    if len(df) < 3:
        return False

    a = df.iloc[-1]
    b = df.iloc[-2]

    if direction == "LONG":

        return (
            safe_float(a["close"]) >
            safe_float(a["open"]) and

            safe_float(b["close"]) >
            safe_float(b["open"]) and

            safe_float(a["close"]) >=
            safe_float(b["close"])
        )

    if direction == "SHORT":

        return (
            safe_float(a["close"]) <
            safe_float(a["open"]) and

            safe_float(b["close"]) <
            safe_float(b["open"]) and

            safe_float(a["close"]) <=
            safe_float(b["close"])
        )

    return False


# ============================================================
# 5M ANALYSIS
# ============================================================

def analyze_5m(df):

    if df.empty or len(df) < 100:
        return None

    row = df.iloc[-1]

    close = safe_float(row["close"])

    ema20 = safe_float(row["ema20"])

    rsi_value = safe_float(row["rsi"])

    adx_value = safe_float(row["adx"])

    body_ratio = safe_float(
        row["body_ratio"]
    )

    close_pos = safe_float(
        row["close_pos"]
    )

    long_score = 0
    short_score = 0

    # LONG
    if close > ema20:
        long_score += 1

    if (
        safe_float(row["close"]) >
        safe_float(row["open"]) and
        body_ratio >= MIN_BODY_RATIO
    ):
        long_score += 1

    if rsi_value >= 52:
        long_score += 1

    if adx_value >= MIN_ADX:
        long_score += 1

    # SHORT
    if close < ema20:
        short_score += 1

    if (
        safe_float(row["close"]) <
        safe_float(row["open"]) and
        body_ratio >= MIN_BODY_RATIO
    ):
        short_score += 1

    if rsi_value <= 48:
        short_score += 1

    if adx_value >= MIN_ADX:
        short_score += 1

    return {
        "timestamp": timestamp_iso(df.index[-1]),

        "close": close,

        "ema20": ema20,

        "rsi": rsi_value,

        "adx": adx_value,

        "atr": safe_float(row["atr"]),

        "body_ratio": body_ratio,

        "close_pos": close_pos,

        "long_score": long_score,

        "short_score": short_score
    }


# ============================================================
# H1 TREND
# ============================================================

def analyze_h1(df):

    if df.empty or len(df) < 100:
        return None

    row = df.iloc[-1]

    close = safe_float(row["close"])
    ema20 = safe_float(row["ema20"])
    ema50 = safe_float(row["ema50"])

    if (
        close > ema20 and
        ema20 > ema50
    ):
        trend = "BULL"

    elif (
        close < ema20 and
        ema20 < ema50
    ):
        trend = "BEAR"

    else:
        trend = "NEUTRAL"

    return {
        "timestamp": timestamp_iso(df.index[-1]),
        "close": close,
        "ema20": ema20,
        "ema50": ema50,
        "trend": trend
    }


# ============================================================
# SETUP ID
# ============================================================

def make_setup_id(
    direction,
    m15,
    five,
    h1
):

    return (
        f"{direction}_"
        f"{m15['timestamp']}_"
        f"{m15['long_score'] if direction == 'LONG' else m15['short_score']}_"
        f"{five['timestamp']}_"
        f"{five['long_score'] if direction == 'LONG' else five['short_score']}_"
        f"{h1['trend']}"
    )


# ============================================================
# COOLDOWN
# ============================================================

def minutes_since(timestamp):

    dt = parse_timestamp(timestamp)

    if dt is None:
        return None

    return (
        now_kst() - dt
    ).total_seconds() / 60.0


def cooldown_remaining(state):

    remaining = 0.0

    # Last SL
    if state.get("last_sl_time"):

        mins = minutes_since(
            state["last_sl_time"]
        )

        if mins is not None:

            remaining = max(
                remaining,
                SL_COOLDOWN_MINUTES - mins
            )

    # Last exit
    if state.get("last_exit_time"):

        mins = minutes_since(
            state["last_exit_time"]
        )

        if mins is not None:

            remaining = max(
                remaining,
                EXIT_COOLDOWN_MINUTES - mins
            )

    # Last signal
    if state.get("last_signal_time"):

        mins = minutes_since(
            state["last_signal_time"]
        )

        if mins is not None:

            remaining = max(
                remaining,
                SIGNAL_COOLDOWN_MINUTES - mins
            )

    return max(
        0.0,
        remaining
    )


# ============================================================
# FIND SIGNAL
# ============================================================

def find_signal(
    m15,
    five,
    h1,
    m15_df
):

    if not m15 or not five or not h1:
        return None

    # --------------------------------------------------------
    # HARD FILTER
    # --------------------------------------------------------

    if m15["adx"] < MIN_ADX:
        print(
            f"[BLOCK] M15 ADX "
            f"{m15['adx']:.2f} < {MIN_ADX}"
        )
        return None

    if m15["atr"] < MIN_ATR:

        print(
            f"[BLOCK] ATR too low: "
            f"{m15['atr']:.2f}"
        )

        return None

    if m15["atr"] > MAX_ATR:

        print(
            f"[BLOCK] ATR too high: "
            f"{m15['atr']:.2f}"
        )

        return None

    # --------------------------------------------------------
    # SQUEEZE BLOCK
    # --------------------------------------------------------

    if m15["squeeze"]:

        print(
            "[BLOCK] M15 volatility squeeze"
        )

        return None

    long_score = m15["long_score"]
    short_score = m15["short_score"]

    five_long = five["long_score"]
    five_short = five["short_score"]

    # --------------------------------------------------------
    # DETERMINE DIRECTION
    # --------------------------------------------------------

    long_ok = (
        long_score >= MIN_M15_SCORE and
        five_long >= MIN_5M_SCORE
    )

    short_ok = (
        short_score >= MIN_M15_SCORE and
        five_short >= MIN_5M_SCORE
    )

    if not long_ok and not short_ok:

        print(
            f"[NO SIGNAL] "
            f"M15 L/S={long_score}/{short_score}, "
            f"5M L/S={five_long}/{five_short}"
        )

        return None

    # --------------------------------------------------------
    # TIE BLOCK
    # --------------------------------------------------------

    if long_ok and short_ok:

        if long_score == short_score:

            print(
                "[BLOCK] Long/Short score tie"
            )

            return None

        if long_score > short_score:

            direction = "LONG"

        else:

            direction = "SHORT"

    elif long_ok:

        direction = "LONG"

    else:

        direction = "SHORT"

    # --------------------------------------------------------
    # H1 FILTER
    # --------------------------------------------------------

    if direction == "LONG":

        if h1["trend"] == "BEAR":

            print(
                "[BLOCK] LONG vs H1 BEAR"
            )

            return None

    if direction == "SHORT":

        if h1["trend"] == "BULL":

            print(
                "[BLOCK] SHORT vs H1 BULL"
            )

            return None

    # Neutral H1 requires stronger M15
    if h1["trend"] == "NEUTRAL":

        score = (
            long_score
            if direction == "LONG"
            else short_score
        )

        if score < STRONG_M15_SCORE:

            print(
                "[BLOCK] H1 NEUTRAL "
                f"requires M15 {STRONG_M15_SCORE}+"
            )

            return None

    # --------------------------------------------------------
    # M15 CONFIRMATION
    # --------------------------------------------------------

    if REQUIRE_M15_CONFIRMATION:

        if not m15_confirmation(
            m15_df,
            direction
        ):

            print(
                "[BLOCK] M15 candle confirmation"
            )

            return None

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    if direction == "LONG":

        if not (
            LONG_RSI_MIN <=
            m15["rsi"] <=
            LONG_RSI_MAX
        ):

            print(
                f"[BLOCK] LONG RSI "
                f"{m15['rsi']:.2f}"
            )

            return None

    else:

        if not (
            SHORT_RSI_MIN <=
            m15["rsi"] <=
            SHORT_RSI_MAX
        ):

            print(
                f"[BLOCK] SHORT RSI "
                f"{m15['rsi']:.2f}"
            )

            return None

    # --------------------------------------------------------
    # BODY FILTER
    # --------------------------------------------------------

    if m15["body_ratio"] < MIN_BODY_RATIO:

        print(
            "[BLOCK] Weak M15 candle body"
        )

        return None

    # --------------------------------------------------------
    # ENTRY DISTANCE
    # --------------------------------------------------------

    distance = abs(
        m15["close"] -
        m15["ema20"]
    )

    max_distance = (
        m15["atr"] *
        MAX_ENTRY_DISTANCE_ATR
    )

    if distance > max_distance:

        print(
            f"[BLOCK] Entry too far "
            f"{distance:.2f} > "
            f"{max_distance:.2f}"
        )

        return None

    # --------------------------------------------------------
    # RESULT
    # --------------------------------------------------------

    return {
        "direction": direction,

        "m15_score": (
            long_score
            if direction == "LONG"
            else short_score
        ),

        "five_score": (
            five_long
            if direction == "LONG"
            else five_short
        ),

        "rsi": m15["rsi"],

        "adx": m15["adx"],

        "atr15": m15["atr"],

        "atr5": five["atr"],

        "h1_trend": h1["trend"],

        "m15_timestamp": m15["timestamp"],

        "five_timestamp": five["timestamp"],

        "m15_close": m15["close"]
    }


# ============================================================
# POSITION CALCULATION
# ============================================================

def calculate_position(signal):

    direction = signal["direction"]

    entry = signal["m15_close"]

    atr15 = signal["atr15"]

    atr5 = signal["atr5"]

    # Base volatility
    base_atr = max(
        atr5,
        atr15 * 0.65
    )

    risk_distance = (
        base_atr *
        ENTRY_RISK_ATR
    )

    min_distance = (
        atr5 *
        MIN_RISK_ATR
    )

    max_distance = (
        atr5 *
        MAX_RISK_ATR
    )

    risk_distance = max(
        min_distance,
        min(
            risk_distance,
            max_distance
        )
    )

    if signal["m15_score"] >= STRONG_M15_SCORE and signal["adx"] >= 25:

        tp1_r = STRONG_TP1_R
        tp2_r = STRONG_TP2_R
        tp3_r = STRONG_TP3_R

    else:

        tp1_r = TP1_R
        tp2_r = TP2_R
        tp3_r = TP3_R

    if direction == "LONG":

        sl = entry - risk_distance

        tp1 = entry + (
            risk_distance * tp1_r
        )

        tp2 = entry + (
            risk_distance * tp2_r
        )

        tp3 = entry + (
            risk_distance * tp3_r
        )

    else:

        sl = entry + risk_distance

        tp1 = entry - (
            risk_distance * tp1_r
        )

        tp2 = entry - (
            risk_distance * tp2_r
        )

        tp3 = entry - (
            risk_distance * tp3_r
        )

    return {
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "risk": risk_distance,
        "tp1_r": tp1_r,
        "tp2_r": tp2_r,
        "tp3_r": tp3_r
    }


# ============================================================
# ENTRY MESSAGE
# ============================================================

def build_entry_message(
    signal,
    position
):

    direction = signal["direction"]

    if direction == "LONG":
        icon = "🔴"
        action = "롱 포지션 신규 진입"
    else:
        icon = "🔵"
        action = "숏 포지션 신규 진입"

    reasons = []

    if direction == "LONG":

        reasons.append(
            "가격이 EMA20/ZLEMA20 상단"
        )

        reasons.append(
            "M15 상승 모멘텀 확인"
        )

    else:

        reasons.append(
            "가격이 EMA20/ZLEMA20 하단"
        )

        reasons.append(
            "M15 하락 모멘텀 확인"
        )

    if signal["adx"] >= 25:

        reasons.append(
            "ADX 강한 추세"
        )

    else:

        reasons.append(
            "ADX 추세 조건 충족"
        )

    reasons_text = "\n".join(
        f"• {escape_html(x)}"
        for x in reasons
    )

    return (
        "🥇 <b>금 선물 스마트 시그널</b>\n"
        f"ONE POSITION LOCK V{VERSION}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"

        f"{icon} <b>{action}</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 진입가 : "
        f"<b>${fmt_price(position['entry'])}</b>\n"
        f"🛑 손절가 : "
        f"<b>${fmt_price(position['sl'])}</b>\n\n"

        "🎯 <b>익절 목표</b>\n"
        f"① TP1   ${fmt_price(position['tp1'])}\n"
        f"② TP2   ${fmt_price(position['tp2'])}\n"
        f"③ TP3   ${fmt_price(position['tp3'])}\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "📊 <b>정밀 분석 리포트</b>\n\n"

        f"M15 신호 점수  : "
        f"{signal['m15_score']} / 8\n"

        f"5M 확인 점수   : "
        f"{signal['five_score']} / 4\n"

        f"RSI            : "
        f"{signal['rsi']:.2f}\n"

        f"ADX (추세강도) : "
        f"{signal['adx']:.2f}\n"

        f"H1 추세        : "
        f"{escape_html(signal['h1_trend'])}\n\n"

        "🧠 <b>상승/하락 근거</b>\n"
        f"{reasons_text}\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚙️ 전략 : ONE POSITION LOCK\n"
        "🔒 TP3 또는 SL 종료 전 신규 진입 금지\n"
        "🔴 현재 포지션 실시간 관리 중\n\n"

        f"📈 <a href=\"{TRADINGVIEW_URL}\">"
        "TradingView 차트 열기</a>"
    )


# ============================================================
# ENTER POSITION
# ============================================================

def enter_position(
    state,
    signal,
    position,
    last_1m_timestamp
):

    setup_id = make_setup_id(
        signal["direction"],
        {
            "timestamp": signal["m15_timestamp"],
            "long_score": (
                signal["m15_score"]
                if signal["direction"] == "LONG"
                else 0
            ),
            "short_score": (
                signal["m15_score"]
                if signal["direction"] == "SHORT"
                else 0
            )
        },
        {
            "timestamp": signal["five_timestamp"],
            "long_score": (
                signal["five_score"]
                if signal["direction"] == "LONG"
                else 0
            ),
            "short_score": (
                signal["five_score"]
                if signal["direction"] == "SHORT"
                else 0
            )
        },
        {
            "trend": signal["h1_trend"]
        }
    )

    state["version"] = VERSION

    state["status"] = "ACTIVE"

    state["direction"] = signal["direction"]

    state["entry"] = position["entry"]

    state["sl"] = position["sl"]

    state["tp1"] = position["tp1"]
    state["tp2"] = position["tp2"]
    state["tp3"] = position["tp3"]

    state["risk"] = position["risk"]

    state["stage"] = "INITIAL"

    state["tp1_hit"] = False
    state["tp2_hit"] = False
    state["tp3_hit"] = False

    state["signal_time"] = iso_now()

    state["signal_id"] = setup_id

    state["last_setup_id"] = setup_id

    state["m15_score"] = signal["m15_score"]

    state["five_score"] = signal["five_score"]

    state["rsi"] = signal["rsi"]

    state["adx"] = signal["adx"]

    state["h1_trend"] = signal["h1_trend"]

    state["last_monitor_time"] = (
        timestamp_iso(last_1m_timestamp)
    )

    state["last_price"] = position["entry"]

    state["last_signal_time"] = iso_now()

    save_state(state)

    append_log(
        "ENTRY",
        {
            "direction": signal["direction"],
            "entry": position["entry"],
            "sl": position["sl"],
            "tp1": position["tp1"],
            "tp2": position["tp2"],
            "tp3": position["tp3"],
            "m15_score": signal["m15_score"],
            "five_score": signal["five_score"],
            "rsi": signal["rsi"],
            "adx": signal["adx"],
            "h1_trend": signal["h1_trend"]
        }
    )

    send_telegram(
        build_entry_message(
            signal,
            position
        )
    )


# ============================================================
# EXIT R
# ============================================================

def calculate_exit_r(
    direction,
    entry,
    risk,
    exit_price
):

    if risk <= 0:
        return 0.0

    if direction == "LONG":

        return (
            exit_price - entry
        ) / risk

    else:

        return (
            entry - exit_price
        ) / risk


# ============================================================
# MONITOR MESSAGE
# ============================================================

def build_tp_message(
    direction,
    level,
    current_price,
    state
):

    if level == 1:

        text = (
            f"🟢 <b>{direction} TP1 도달</b>\n\n"
            f"현재가 : ${fmt_price(current_price)}\n"
            f"TP1 : ${fmt_price(state['tp1'])}\n\n"
            "🛡️ SL이 진입가로 이동했습니다.\n"
            "⚠️ 포지션은 아직 종료되지 않았습니다.\n"
        )

    elif level == 2:

        text = (
            f"🟢 <b>{direction} TP2 도달</b>\n\n"
            f"현재가 : ${fmt_price(current_price)}\n"
            f"TP2 : ${fmt_price(state['tp2'])}\n\n"
            "🛡️ SL이 TP1 가격으로 이동했습니다.\n"
            "⚠️ 포지션은 아직 종료되지 않았습니다.\n"
        )

    else:

        text = (
            f"🟢 <b>{direction} TP3 도달</b>\n\n"
            f"청산가 : ${fmt_price(current_price)}\n"
            f"TP3 : ${fmt_price(state['tp3'])}\n\n"
            "✅ 포지션 최종 종료\n"
        )

    text += (
        "🔒 TP3 또는 SL까지 신규 진입 금지\n\n"
        f"📈 <a href=\"{TRADINGVIEW_URL}\">"
        "TradingView 차트 열기</a>"
    )

    return text


# ============================================================
# EXIT MESSAGE
# ============================================================

def build_exit_message(
    direction,
    reason,
    exit_price,
    state,
    exit_r
):

    if reason == "SL":

        title = "🔴 손절 / 보호청산"

    elif reason == "BE":

        title = "🟡 BE 청산"

    elif reason == "TRAIL":

        title = "🟠 TP1 트레일 청산"

    else:

        title = "🟢 TP3 최종 익절"

    return (
        "🥇 <b>금 선물 스마트 시그널</b>\n"
        "POSITION CLOSED\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"

        f"{title}\n\n"

        f"방향 : {direction}\n"
        f"진입 : ${fmt_price(state['entry'])}\n"
        f"청산 : ${fmt_price(exit_price)}\n\n"

        f"실현 R : <b>{exit_r:+.2f}R</b>\n\n"

        "🔓 포지션 LOCK 해제\n"
        "새로운 신호 탐색 가능\n\n"

        f"📈 <a href=\"{TRADINGVIEW_URL}\">"
        "TradingView 차트 열기</a>"
    )


# ============================================================
# RESET AFTER EXIT
# ============================================================

def reset_after_exit(
    state,
    reason,
    exit_price,
    candle_time
):

    # --------------------------------------------------------
    # SNAPSHOT BEFORE CLEARING
    # --------------------------------------------------------

    direction = state["direction"]

    entry = safe_float(
        state["entry"]
    )

    risk = safe_float(
        state["risk"]
    )

    exit_r = calculate_exit_r(
        direction,
        entry,
        risk,
        exit_price
    )

    snapshot = dict(state)

    # --------------------------------------------------------
    # LOG
    # --------------------------------------------------------

    append_log(
        "EXIT",
        {
            "direction": direction,
            "reason": reason,
            "entry": entry,
            "exit": exit_price,
            "risk": risk,
            "exit_r": exit_r,
            "tp1": state["tp1"],
            "tp2": state["tp2"],
            "tp3": state["tp3"],
            "signal_time": state["signal_time"]
        }
    )

    # --------------------------------------------------------
    # EXIT MESSAGE BEFORE CLEAR
    # --------------------------------------------------------

    send_telegram(
        build_exit_message(
            direction,
            reason,
            exit_price,
            snapshot,
            exit_r
        )
    )

    # --------------------------------------------------------
    # PRESERVE HISTORY
    # --------------------------------------------------------

    last_setup_id = state.get(
        "last_setup_id",
        state.get("signal_id", "")
    )

    state["status"] = "FLAT"

    state["direction"] = ""

    state["entry"] = 0.0
    state["sl"] = 0.0

    state["tp1"] = 0.0
    state["tp2"] = 0.0
    state["tp3"] = 0.0

    state["risk"] = 0.0

    state["stage"] = "INITIAL"

    state["tp1_hit"] = False
    state["tp2_hit"] = False
    state["tp3_hit"] = False

    state["last_exit_time"] = iso_now()

    state["last_exit_reason"] = reason

    state["last_exit_direction"] = direction

    state["last_price"] = exit_price

    state["exit_r"] = exit_r

    state["last_monitor_time"] = (
        timestamp_iso(candle_time)
    )

    state["last_setup_id"] = last_setup_id

    if reason == "SL":

        state["last_sl_time"] = iso_now()

        state["last_sl_direction"] = direction

    save_state(state)


# ============================================================
# TP1
# ============================================================

def handle_tp1(
    state,
    current_price,
    candle_time
):

    if state["tp1_hit"]:
        return

    state["tp1_hit"] = True

    state["stage"] = "TP1_BE"

    # Move SL to ENTRY
    state["sl"] = state["entry"]

    state["last_price"] = current_price

    state["last_monitor_time"] = (
        timestamp_iso(candle_time)
    )

    append_log(
        "TP1",
        {
            "direction": state["direction"],
            "price": current_price,
            "new_sl": state["sl"]
        }
    )

    save_state(state)

    send_telegram(
        build_tp_message(
            state["direction"],
            1,
            current_price,
            state
        )
    )


# ============================================================
# TP2
# ============================================================

def handle_tp2(
    state,
    current_price,
    candle_time
):

    if state["tp2_hit"]:
        return

    state["tp2_hit"] = True

    state["stage"] = "TP2_TRAIL"

    # Move SL to TP1
    state["sl"] = state["tp1"]

    state["last_price"] = current_price

    state["last_monitor_time"] = (
        timestamp_iso(candle_time)
    )

    append_log(
        "TP2",
        {
            "direction": state["direction"],
            "price": current_price,
            "new_sl": state["sl"]
        }
    )

    save_state(state)

    send_telegram(
        build_tp_message(
            state["direction"],
            2,
            current_price,
            state
        )
    )


# ============================================================
# MONITOR SINGLE 1M CANDLE
# ============================================================

def process_monitor_candle(
    state,
    candle
):

    if state["status"] != "ACTIVE":
        return "STOP"

    direction = state["direction"]

    candle_time = candle.name

    open_price = safe_float(
        candle["open"]
    )

    high_price = safe_float(
        candle["high"]
    )

    low_price = safe_float(
        candle["low"]
    )

    close_price = safe_float(
        candle["close"]
    )

    state["last_price"] = close_price

    # ========================================================
    # LONG
    # ========================================================

    if direction == "LONG":

        # ----------------------------------------------------
        # IMPORTANT:
        # Same candle ambiguity:
        # LOW / SL is processed BEFORE HIGH / TP.
        # Conservative protection.
        # ----------------------------------------------------

        sl = safe_float(
            state["sl"]
        )

        if low_price <= sl:

            reset_after_exit(
                state,
                "SL",
                sl,
                candle_time
            )

            return "EXIT"

        # TP1
        if (
            not state["tp1_hit"] and
            high_price >= state["tp1"]
        ):

            handle_tp1(
                state,
                state["tp1"],
                candle_time
            )

        # TP2
        if (
            state["status"] == "ACTIVE" and
            not state["tp2_hit"] and
            high_price >= state["tp2"]
        ):

            handle_tp2(
                state,
                state["tp2"],
                candle_time
            )

        # TP3
        if (
            state["status"] == "ACTIVE" and
            not state["tp3_hit"] and
            high_price >= state["tp3"]
        ):

            state["tp3_hit"] = True

            save_state(state)

            reset_after_exit(
                state,
                "TP3",
                state["tp3"],
                candle_time
            )

            return "EXIT"

    # ========================================================
    # SHORT
    # ========================================================

    elif direction == "SHORT":

        # ----------------------------------------------------
        # Same candle ambiguity:
        # HIGH / SL first.
        # ----------------------------------------------------

        sl = safe_float(
            state["sl"]
        )

        if high_price >= sl:

            reset_after_exit(
                state,
                "SL",
                sl,
                candle_time
            )

            return "EXIT"

        # TP1
        if (
            not state["tp1_hit"] and
            low_price <= state["tp1"]
        ):

            handle_tp1(
                state,
                state["tp1"],
                candle_time
            )

        # TP2
        if (
            state["status"] == "ACTIVE" and
            not state["tp2_hit"] and
            low_price <= state["tp2"]
        ):

            handle_tp2(
                state,
                state["tp2"],
                candle_time
            )

        # TP3
        if (
            state["status"] == "ACTIVE" and
            not state["tp3_hit"] and
            low_price <= state["tp3"]
        ):

            state["tp3_hit"] = True

            save_state(state)

            reset_after_exit(
                state,
                "TP3",
                state["tp3"],
                candle_time
            )

            return "EXIT"

    state["last_monitor_time"] = (
        timestamp_iso(candle_time)
    )

    state["last_price"] = close_price

    save_state(state)

    return "CONTINUE"


# ============================================================
# GET NEW 1M CANDLES
# ============================================================

def get_new_candles(
    df,
    last_monitor_time
):

    if df.empty:
        return pd.DataFrame()

    if not last_monitor_time:

        # First monitor:
        # only latest completed candle
        return df.iloc[-1:].copy()

    last_dt = parse_timestamp(
        last_monitor_time
    )

    if last_dt is None:

        return df.iloc[-1:].copy()

    result = []

    for idx in df.index:

        dt = parse_timestamp(idx)

        if dt is None:
            continue

        if dt > last_dt:

            result.append(idx)

    if not result:
        return pd.DataFrame()

    return df.loc[result].copy()


# ============================================================
# ACTIVE POSITION MONITOR
# ============================================================

def monitor_active(
    state,
    df1m
):

    print(
        "===================================="
    )

    print("ACTIVE POSITION MONITOR")

    print(
        f"Direction : {state['direction']}"
    )

    print(
        f"Entry     : {state['entry']}"
    )

    print(
        f"SL        : {state['sl']}"
    )

    print(
        f"TP1       : {state['tp1']}"
    )

    print(
        f"TP2       : {state['tp2']}"
    )

    print(
        f"TP3       : {state['tp3']}"
    )

    print(
        f"Stage     : {state['stage']}"
    )

    new_candles = get_new_candles(
        df1m,
        state.get("last_monitor_time", "")
    )

    if new_candles.empty:

        print(
            "[MONITOR] No new completed 1M candles"
        )

        return

    print(
        f"[MONITOR] "
        f"{len(new_candles)} new 1M candles"
    )

    for idx, candle in new_candles.iterrows():

        if state["status"] != "ACTIVE":
            break

        print(
            f"[MONITOR] "
            f"{timestamp_iso(idx)} "
            f"O={candle['open']:.2f} "
            f"H={candle['high']:.2f} "
            f"L={candle['low']:.2f} "
            f"C={candle['close']:.2f}"
        )

        result = process_monitor_candle(
            state,
            candle
        )

        if result == "EXIT":
            break


# ============================================================
# MAIN
# ============================================================

def main():

    print("====================================")
    print(
        f" GOLD FUTURES SMART SIGNAL BOT "
        f"V{VERSION}"
    )
    print(
        f" KST: {now_kst().isoformat()}"
    )
    print("====================================")

    state = load_state()

    print("")
    print("====================================")
    print("STATE")
    print(
        f"Status    : {state['status']}"
    )
    print(
        f"Direction : {state['direction']}"
    )
    print(
        f"Entry     : {state['entry']}"
    )
    print(
        f"SL        : {state['sl']}"
    )
    print(
        f"TP1       : {state['tp1']}"
    )
    print(
        f"TP2       : {state['tp2']}"
    )
    print(
        f"TP3       : {state['tp3']}"
    )
    print(
        f"Stage     : {state['stage']}"
    )
    print("====================================")

    # ========================================================
    # DOWNLOAD
    # ========================================================

    print("")
    print("Downloading market data...")

    df1m_raw = download_data(
        "1m",
        "7d"
    )

    df5_raw = download_data(
        "5m",
        "30d"
    )

    df15_raw = download_data(
        "15m",
        "60d"
    )

    df1h_raw = download_data(
        "1h",
        "2y"
    )

    if (
        df1m_raw.empty or
        df5_raw.empty or
        df15_raw.empty or
        df1h_raw.empty
    ):

        print(
            "[FATAL] Missing market data"
        )

        append_log(
            "DATA_ERROR",
            {}
        )

        return

    # ========================================================
    # FRESHNESS
    # ========================================================

    fresh_1m = check_freshness(
        "1M",
        df1m_raw,
        MAX_DELAY_1M_MINUTES
    )

    fresh_5m = check_freshness(
        "5M",
        df5_raw,
        MAX_DELAY_5M_MINUTES
    )

    fresh_15m = check_freshness(
        "15M",
        df15_raw,
        MAX_DELAY_15M_MINUTES
    )

    # ========================================================
    # COMPLETED CANDLES
    # ========================================================

    df1m = completed(
        df1m_raw
    )

    df5 = completed(
        df5_raw
    )

    df15 = completed(
        df15_raw
    )

    df1h = completed(
        df1h_raw
    )

    # ========================================================
    # ACTIVE POSITION
    # ========================================================

    if state["status"] == "ACTIVE":

        # Monitoring must use fresh 1M data.
        if not fresh_1m:

            print(
                "[MONITOR BLOCK] "
                "1M data is stale"
            )

            append_log(
                "MONITOR_BLOCK_STALE_1M",
                {
                    "delay_minutes":
                        data_delay_minutes(df1m_raw)
                }
            )

            return

        monitor_active(
            state,
            df1m
        )

        return

    # ========================================================
    # NEW SIGNAL
    # ========================================================

    # New signal requires all MTF data reasonably fresh.
    if not fresh_5m or not fresh_15m:

        print(
            "[SIGNAL BLOCK] "
            "5M/15M data is stale"
        )

        append_log(
            "SIGNAL_BLOCK_STALE_DATA",
            {
                "fresh_5m": fresh_5m,
                "fresh_15m": fresh_15m
            }
        )

        return

    # ========================================================
    # COOLDOWN
    # ========================================================

    cooldown = cooldown_remaining(
        state
    )

    if cooldown > 0:

        print(
            f"[COOLDOWN] "
            f"{cooldown:.1f} minutes remaining"
        )

        return

    # ========================================================
    # INDICATORS
    # ========================================================

    df5 = add_indicators(
        df5
    )

    df15 = add_indicators(
        df15
    )

    df1h = add_indicators(
        df1h
    )

    # ========================================================
    # ANALYSIS
    # ========================================================

    m15 = analyze_m15(
        df15
    )

    five = analyze_5m(
        df5
    )

    h1 = analyze_h1(
        df1h
    )

    if not m15 or not five or not h1:

        print(
            "[ERROR] Indicator analysis failed"
        )

        return

    print("")
    print("====================================")
    print("MARKET ANALYSIS")
    print("====================================")

    print(
        f"M15 LONG/SHORT : "
        f"{m15['long_score']}/"
        f"{m15['short_score']}"
    )

    print(
        f"5M LONG/SHORT  : "
        f"{five['long_score']}/"
        f"{five['short_score']}"
    )

    print(
        f"RSI            : "
        f"{m15['rsi']:.2f}"
    )

    print(
        f"ADX            : "
        f"{m15['adx']:.2f}"
    )

    print(
        f"ATR            : "
        f"{m15['atr']:.2f}"
    )

    print(
        f"H1 TREND       : "
        f"{h1['trend']}"
    )

    print(
        f"M15 SQUEEZE    : "
        f"{m15['squeeze']}"
    )

    # ========================================================
    # SIGNAL
    # ========================================================

    signal = find_signal(
        m15,
        five,
        h1,
        df15
    )

    if not signal:

        print("")
        print(
            "[RESULT] No valid signal"
        )

        return

    # ========================================================
    # SETUP ID
    # ========================================================

    setup_id = make_setup_id(
        signal["direction"],
        m15,
        five,
        h1
    )

    if (
        state.get("last_setup_id") ==
        setup_id
    ):

        print(
            "[BLOCK] Same setup already used"
        )

        return

    # ========================================================
    # POSITION
    # ========================================================

    position = calculate_position(
        signal
    )

    # ========================================================
    # USE COMPLETED 1M CLOSE FOR ENTRY
    # ========================================================

    if not df1m.empty:

        latest_1m = df1m.iloc[-1]

        entry_price = safe_float(
            latest_1m["close"]
        )

        if entry_price > 0:

            # Recalculate using actual
            # latest completed 1M close.
            signal["m15_close"] = entry_price

            position = calculate_position(
                signal
            )

    last_1m_timestamp = (
        df1m.index[-1]
        if not df1m.empty
        else df15.index[-1]
    )

    print("")
    print("====================================")
    print("NEW SIGNAL")
    print("====================================")

    print(
        f"Direction : {signal['direction']}"
    )

    print(
        f"Entry     : {position['entry']:.2f}"
    )

    print(
        f"SL        : {position['sl']:.2f}"
    )

    print(
        f"TP1       : {position['tp1']:.2f}"
    )

    print(
        f"TP2       : {position['tp2']:.2f}"
    )

    print(
        f"TP3       : {position['tp3']:.2f}"
    )

    # ========================================================
    # ENTER
    # ========================================================

    enter_position(
        state,
        signal,
        position,
        last_1m_timestamp
    )

    print(
        "[RESULT] Position entered"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        print("")
        print("====================================")
        print("FATAL ERROR")
        print("====================================")

        print(
            repr(e)
        )

        append_log(
            "FATAL_ERROR",
            {
                "error": repr(e)
            }
        )

        raise
