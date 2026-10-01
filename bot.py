import os
import json
import time
import math
import html
import hashlib
from datetime import datetime, timedelta, timezone

import requests
import numpy as np
import pandas as pd
import yfinance as yf


# ============================================================
# GOLD FUTURES SMART SIGNAL BOT V17.2
# Stable Hybrid + Emergency Price Protection
# ============================================================

VERSION = "17.2.0"

TICKER = "GC=F"

STATE_FILE = "signal_state.json"
LOG_FILE = "bot_log.json"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

KST = timezone(timedelta(hours=9))


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

LONG_RSI_MIN = 52
LONG_RSI_MAX = 68

SHORT_RSI_MIN = 32
SHORT_RSI_MAX = 48

MIN_BODY_RATIO = 0.35

EMA_SEPARATION_ATR = 0.10

MAX_ENTRY_DISTANCE_ATR = 1.20

# Risk
RISK_ATR_MULT = 1.80
MIN_RISK_ATR = 1.20
MAX_RISK_ATR = 2.80

# Normal TP
TP1_R = 1.20
TP2_R = 2.00
TP3_R = 3.00

# Strong signal TP
STRONG_TP1_R = 1.30
STRONG_TP2_R = 2.20
STRONG_TP3_R = 3.50

# Cooldowns
SIGNAL_COOLDOWN_MINUTES = 45
SL_COOLDOWN_MINUTES = 120
TP3_COOLDOWN_MINUTES = 15


# ============================================================
# DATA SETTINGS
# ============================================================

DATA_RETRIES = 3

# Normal monitoring freshness
MAX_FRESH_1M_MINUTES = 8

# Emergency mode threshold
EMERGENCY_1M_MINUTES = 8

# Absolute maximum age where emergency price is considered
MAX_EMERGENCY_PRICE_AGE_MINUTES = 30

MAX_5M_FRESH_MINUTES = 15
MAX_15M_FRESH_MINUTES = 30
MAX_1H_FRESH_MINUTES = 90


# ============================================================
# HTTP
# ============================================================

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Gold-Futures-Smart-Signal-Bot/17.2"
    }
)


# ============================================================
# BASIC UTILITIES
# ============================================================

def now_kst():
    return datetime.now(KST)


def iso_now():
    return now_kst().isoformat()


def safe_float(value, default=None):
    try:
        if value is None:
            return default

        x = float(value)

        if not math.isfinite(x):
            return default

        return x
    except Exception:
        return default


def round_price(value):
    value = safe_float(value)

    if value is None:
        return None

    return round(value, 2)


def clamp(value, low, high):
    return max(low, min(high, value))


def escape(text):
    return html.escape(str(text))


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] token/chat id missing")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        r = SESSION.post(
            url,
            json=payload,
            timeout=15
        )

        if r.ok:
            return True

        print(
            "[TELEGRAM ERROR]",
            r.status_code,
            r.text[:500]
        )

    except Exception as e:
        print("[TELEGRAM ERROR]", repr(e))

    return False


# ============================================================
# JSON STATE
# ============================================================

def default_state():
    return {
        "version": VERSION,
        "status": "IDLE",
        "direction": None,

        "entry": None,
        "sl": None,
        "tp1": None,
        "tp2": None,
        "tp3": None,
        "risk": None,

        "stage": "INITIAL",

        "signal_time": None,
        "signal_id": None,
        "setup_id": None,

        "m15_score": 0,
        "five_score": 0,

        "rsi": None,
        "adx": None,
        "atr": None,

        "last_signal_time": None,

        "last_exit_time": None,
        "last_exit_reason": None,
        "last_exit_direction": None,

        "last_sl_time": None,
        "last_sl_direction": None,

        "last_monitor_time": None,
        "last_price": None,

        "migration_done": False,
    }


def atomic_write_json(path, data):
    temp_path = path + ".tmp"

    with open(
        temp_path,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(temp_path, path)


def load_state():
    state = default_state()
    migrated = False

    if not os.path.exists(STATE_FILE):
        return state, False

    try:
        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            old = json.load(f)

        if isinstance(old, dict):
            state.update(old)

    except Exception as e:
        print("[STATE LOAD ERROR]", repr(e))
        return state, False

    old_version = str(
        state.get("version", "")
    )

    # ========================================================
    # MIGRATION
    # ========================================================

    if old_version != VERSION:
        migrated = True

    if "stage" not in state:
        if state.get("tp2_hit"):
            state["stage"] = "TP2_TRAIL"

        elif state.get("tp1_hit"):
            state["stage"] = "TP1_BE"

        else:
            state["stage"] = "INITIAL"

        migrated = True

    # Existing V13/V14/V15/V16 state
    if state.get("status") == "ACTIVE":

        # TP2 already reached
        if state.get("tp2_hit") is True:

            state["stage"] = "TP2_TRAIL"

            tp1 = safe_float(state.get("tp1"))

            if tp1 is not None:
                state["sl"] = tp1

            migrated = True

        # TP1 already reached
        elif state.get("tp1_hit") is True:

            state["stage"] = "TP1_BE"

            entry = safe_float(state.get("entry"))

            if entry is not None:
                state["sl"] = entry

            migrated = True

    state["version"] = VERSION
    state["migration_done"] = True

    if migrated:
        print(
            f"[STATE MIGRATION] "
            f"{old_version or 'unknown'} -> {VERSION}"
        )

        # Important:
        # save immediately so stale-data runs do not
        # leave old state on disk.
        try:
            atomic_write_json(
                STATE_FILE,
                state
            )
            print("[STATE] migrated state saved")
        except Exception as e:
            print(
                "[STATE SAVE ERROR]",
                repr(e)
            )

    return state, migrated


def save_state(state):
    state["version"] = VERSION

    try:
        atomic_write_json(
            STATE_FILE,
            state
        )
    except Exception as e:
        print("[STATE SAVE ERROR]", repr(e))


# ============================================================
# LOG
# ============================================================

def load_log():
    if not os.path.exists(LOG_FILE):
        return []

    try:
        with open(
            LOG_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            data = json.load(f)

        if isinstance(data, list):
            return data

    except Exception:
        pass

    return []


def write_log(event, state=None, extra=None):
    logs = load_log()

    item = {
        "time": iso_now(),
        "version": VERSION,
        "event": event,
    }

    if state:
        item.update(
            {
                "status": state.get("status"),
                "direction": state.get("direction"),
                "entry": state.get("entry"),
                "sl": state.get("sl"),
                "tp1": state.get("tp1"),
                "tp2": state.get("tp2"),
                "tp3": state.get("tp3"),
                "stage": state.get("stage"),
            }
        )

    if extra:
        item["extra"] = extra

    logs.append(item)

    # Keep reasonable size
    logs = logs[-500:]

    try:
        atomic_write_json(
            LOG_FILE,
            logs
        )
    except Exception as e:
        print("[LOG ERROR]", repr(e))


# ============================================================
# DATA DOWNLOAD
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
                threads=False,
            )

            if df is None or df.empty:
                raise RuntimeError(
                    "empty dataframe"
                )

            # yfinance may return MultiIndex
            if isinstance(
                df.columns,
                pd.MultiIndex
            ):
                if TICKER in df.columns.get_level_values(-1):
                    try:
                        df = df.xs(
                            TICKER,
                            axis=1,
                            level=-1
                        )
                    except Exception:
                        pass

                if isinstance(
                    df.columns,
                    pd.MultiIndex
                ):
                    df.columns = [
                        c[0] if isinstance(c, tuple)
                        else c
                        for c in df.columns
                    ]

            required = [
                "Open",
                "High",
                "Low",
                "Close",
            ]

            for col in required:
                if col not in df.columns:
                    raise RuntimeError(
                        f"missing {col}"
                    )

            df = df[required].copy()

            for col in required:
                df[col] = pd.to_numeric(
                    df[col],
                    errors="coerce"
                )

            df.dropna(
                subset=required,
                inplace=True
            )

            df = normalize_index(df)

            if df.empty:
                raise RuntimeError(
                    "empty after cleanup"
                )

            print(
                f"[DATA] {interval} "
                f"{len(df)} rows"
            )

            return df

        except Exception as e:
            last_error = e

            print(
                f"[DATA ERROR] {interval}: "
                f"{repr(e)}"
            )

            time.sleep(1)

    print(
        f"[DATA FAILED] {interval}: "
        f"{repr(last_error)}"
    )

    return pd.DataFrame()


def normalize_index(df):
    result = df.copy()

    idx = pd.to_datetime(
        result.index,
        errors="coerce"
    )

    valid = ~idx.isna()

    result = result.loc[valid].copy()
    idx = idx[valid]

    if idx.tz is None:
        idx = idx.tz_localize(
            "UTC"
        )

    idx = idx.tz_convert(
        KST
    )

    result.index = idx

    result.sort_index(
        inplace=True
    )

    result = result[
        ~result.index.duplicated(
            keep="last"
        )
    ]

    return result


def completed(df):
    """
    Remove the currently forming candle.
    """
    if df is None or df.empty:
        return df

    result = df.copy()

    now = pd.Timestamp.now(
        tz=KST
    )

    # Conservative: remove latest candle because
    # it may still be forming.
    if len(result) > 1:
        result = result.iloc[:-1]

    # Also remove future timestamps.
    result = result[
        result.index <= now
    ]

    return result


# ============================================================
# FRESHNESS
# ============================================================

def data_age_minutes(df):
    if df is None or df.empty:
        return float("inf")

    last_ts = df.index[-1]

    now = pd.Timestamp.now(
        tz=KST
    )

    age = (
        now - last_ts
    ).total_seconds() / 60.0

    return max(0.0, age)


def print_freshness(name, df):
    age = data_age_minutes(df)

    print(
        f"[DATA FRESHNESS] "
        f"{name}: {age:.1f} min"
    )

    return age


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

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = gain.ewm(
        alpha=1 / length,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / length,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(
        0,
        np.nan
    )

    result = 100 - (
        100 / (1 + rs)
    )

    return result.fillna(50)


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

    tr = pd.concat(
        [
            high - low,
            (high - df["Close"].shift()).abs(),
            (low - df["Close"].shift()).abs()
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
        ).mean() /
        atr_value.replace(
            0,
            np.nan
        )
    )

    minus_di = (
        100 *
        minus_dm.ewm(
            alpha=1 / length,
            adjust=False
        ).mean() /
        atr_value.replace(
            0,
            np.nan
        )
    )

    dx = (
        100 *
        (plus_di - minus_di).abs() /
        (plus_di + minus_di).replace(
            0,
            np.nan
        )
    )

    return dx.ewm(
        alpha=1 / length,
        adjust=False
    ).mean()


def add_indicators(df):
    result = df.copy()

    result["EMA20"] = ema(
        result["Close"],
        20
    )

    result["EMA50"] = ema(
        result["Close"],
        50
    )

    result["RSI"] = rsi(
        result["Close"],
        14
    )

    result["ATR"] = atr(
        result,
        14
    )

    result["ADX"] = adx(
        result,
        14
    )

    result["Body"] = (
        result["Close"] -
        result["Open"]
    ).abs()

    result["Range"] = (
        result["High"] -
        result["Low"]
    ).replace(
        0,
        np.nan
    )

    result["BodyRatio"] = (
        result["Body"] /
        result["Range"]
    ).fillna(0)

    result["ClosePosition"] = (
        result["Close"] -
        result["Low"]
    ) / result["Range"]

    result["ClosePosition"] = (
        result["ClosePosition"]
        .fillna(0.5)
    )

    return result


# ============================================================
# TREND / SCORE
# ============================================================

def score_m15(df):
    if df is None or len(df) < 60:
        return {
            "long": 0,
            "short": 0,
            "max": 8
        }

    row = df.iloc[-1]
    prev = df.iloc[-2]

    long_score = 0
    short_score = 0

    close = safe_float(row["Close"])
    ema20 = safe_float(row["EMA20"])
    ema50 = safe_float(row["EMA50"])
    adx_value = safe_float(row["ADX"])
    rsi_value = safe_float(row["RSI"])
    atr_value = safe_float(row["ATR"])
    body_ratio = safe_float(
        row["BodyRatio"],
        0
    )
    close_pos = safe_float(
        row["ClosePosition"],
        0.5
    )

    prev_ema20 = safe_float(
        prev["EMA20"]
    )

    if None in (
        close,
        ema20,
        ema50,
        adx_value,
        rsi_value,
        atr_value
    ):
        return {
            "long": 0,
            "short": 0,
            "max": 8
        }

    # LONG 1
    if close > ema20:
        long_score += 1

    # LONG 2
    if ema20 > ema50:
        long_score += 1

    # LONG 3
    if ema20 > prev_ema20:
        long_score += 1

    # LONG 4
    if body_ratio >= MIN_BODY_RATIO:
        if close > row["Open"]:
            long_score += 1

    # LONG 5
    if close_pos >= 0.60:
        long_score += 1

    # LONG 6
    if rsi_value >= LONG_RSI_MIN:
        long_score += 1

    # LONG 7
    if adx_value >= MIN_ADX:
        long_score += 1

    # LONG 8
    if (
        close - ema20
    ) <= (
        MAX_ENTRY_DISTANCE_FROM_ATR(
            atr_value
        )
    ):
        long_score += 1

    # SHORT 1
    if close < ema20:
        short_score += 1

    # SHORT 2
    if ema20 < ema50:
        short_score += 1

    # SHORT 3
    if ema20 < prev_ema20:
        short_score += 1

    # SHORT 4
    if body_ratio >= MIN_BODY_RATIO:
        if close < row["Open"]:
            short_score += 1

    # SHORT 5
    if close_pos <= 0.40:
        short_score += 1

    # SHORT 6
    if rsi_value <= SHORT_RSI_MAX:
        short_score += 1

    # SHORT 7
    if adx_value >= MIN_ADX:
        short_score += 1

    # SHORT 8
    if (
        ema20 - close
    ) <= (
        MAX_ENTRY_DISTANCE_FROM_ATR(
            atr_value
        )
    ):
        short_score += 1

    return {
        "long": long_score,
        "short": short_score,
        "max": 8
    }


def MAX_ENTRY_DISTANCE_FROM_ATR(atr_value):
    return (
        atr_value *
        MAX_ENTRY_DISTANCE_ATR
    )


def score_5m(df, direction):
    if df is None or len(df) < 50:
        return 0

    row = df.iloc[-1]

    close = safe_float(row["Close"])
    ema20 = safe_float(row["EMA20"])
    ema50 = safe_float(row["EMA50"])
    rsi_value = safe_float(row["RSI"])
    body_ratio = safe_float(
        row["BodyRatio"],
        0
    )
    close_pos = safe_float(
        row["ClosePosition"],
        0.5
    )

    if None in (
        close,
        ema20,
        ema50,
        rsi_value
    ):
        return 0

    score = 0

    if direction == "LONG":

        if close > ema20:
            score += 1

        if ema20 > ema50:
            score += 1

        if body_ratio >= MIN_BODY_RATIO:
            if close > row["Open"]:
                score += 1

        if close_pos >= 0.55:
            score += 1

    else:

        if close < ema20:
            score += 1

        if ema20 < ema50:
            score += 1

        if body_ratio >= MIN_BODY_RATIO:
            if close < row["Open"]:
                score += 1

        if close_pos <= 0.45:
            score += 1

    return score


# ============================================================
# H1 TREND
# ============================================================

def h1_trend(df):
    if df is None or len(df) < 60:
        return "NEUTRAL"

    row = df.iloc[-1]

    close = safe_float(
        row["Close"]
    )

    ema20 = safe_float(
        row["EMA20"]
    )

    ema50 = safe_float(
        row["EMA50"]
    )

    if None in (
        close,
        ema20,
        ema50
    ):
        return "NEUTRAL"

    if (
        close > ema20 >
        ema50
    ):
        return "BULL"

    if (
        close < ema20 <
        ema50
    ):
        return "BEAR"

    return "NEUTRAL"


# ============================================================
# SETUP ID
# ============================================================

def make_setup_id(
    direction,
    m15_close,
    five_close,
    m15_score,
    five_score
):
    raw = (
        f"{direction}|"
        f"{round(m15_close, 1)}|"
        f"{round(five_close, 1)}|"
        f"{m15_score}|"
        f"{five_score}"
    )

    return hashlib.sha1(
        raw.encode()
    ).hexdigest()[:16]


# ============================================================
# SIGNAL SEARCH
# ============================================================

def find_signal(
    m15,
    five,
    one_h,
    latest_price
):
    if (
        m15.empty or
        five.empty or
        one_h.empty
    ):
        return None

    m15s = add_indicators(
        completed(m15)
    )

    fives = add_indicators(
        completed(five)
    )

    h1s = add_indicators(
        completed(one_h)
    )

    if (
        len(m15s) < 60 or
        len(fives) < 60 or
        len(h1s) < 60
    ):
        print("[SIGNAL] insufficient data")
        return None

    m15_row = m15s.iloc[-1]
    five_row = fives.iloc[-1]

    m15_scores = score_m15(
        m15s
    )

    candidates = []

    for direction in [
        "LONG",
        "SHORT"
    ]:

        if direction == "LONG":
            m15_score = m15_scores["long"]
        else:
            m15_score = m15_scores["short"]

        five_score = score_5m(
            fives,
            direction
        )

        if m15_score < MIN_M15_SCORE:
            continue

        if five_score < MIN_5M_SCORE:
            continue

        adx_value = safe_float(
            m15_row["ADX"]
        )

        atr_value = safe_float(
            m15_row["ATR"]
        )

        rsi_value = safe_float(
            m15_row["RSI"]
        )

        m15_close = safe_float(
            m15_row["Close"]
        )

        five_close = safe_float(
            five_row["Close"]
        )

        if None in (
            adx_value,
            atr_value,
            rsi_value,
            m15_close,
            five_close
        ):
            continue

        if adx_value < MIN_ADX:
            continue

        if not (
            MIN_ATR <=
            atr_value <=
            MAX_ATR
        ):
            continue

        if direction == "LONG":

            if not (
                LONG_RSI_MIN <=
                rsi_value <=
                LONG_RSI_MAX
            ):
                continue

        else:

            if not (
                SHORT_RSI_MIN <=
                rsi_value <=
                SHORT_RSI_MAX
            ):
                continue

        h1 = h1_trend(h1s)

        # Stronger directional confirmation.
        if REQUIRE_M15_CONFIRMATION:

            if direction == "LONG":

                if m15_close <= safe_float(
                    m15_row["EMA20"]
                ):
                    continue

            else:

                if m15_close >= safe_float(
                    m15_row["EMA20"]
                ):
                    continue

        if direction == "LONG":
            h1_bonus = (
                1 if h1 == "BULL"
                else 0
            )
        else:
            h1_bonus = (
                1 if h1 == "BEAR"
                else 0
            )

        total_score = (
            m15_score * 10 +
            five_score * 2 +
            h1_bonus
        )

        candidates.append(
            {
                "direction": direction,
                "m15_score": m15_score,
                "five_score": five_score,
                "adx": adx_value,
                "atr": atr_value,
                "rsi": rsi_value,
                "h1": h1,
                "m15_close": m15_close,
                "five_close": five_close,
                "strong": (
                    m15_score >=
                    STRONG_M15_SCORE
                ),
                "total_score": total_score,
            }
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x["total_score"],
        reverse=True
    )

    signal = candidates[0]

    setup_id = make_setup_id(
        signal["direction"],
        signal["m15_close"],
        signal["five_close"],
        signal["m15_score"],
        signal["five_score"]
    )

    signal["setup_id"] = setup_id

    return signal


# ============================================================
# POSITION CALCULATION
# ============================================================

def calculate_position(
    signal,
    entry,
    m15,
    five
):
    direction = signal["direction"]

    atr_value = safe_float(
        signal["atr"]
    )

    if atr_value is None:
        return None

    # --------------------------------------------------------
    # Find recent swing
    # --------------------------------------------------------

    recent = m15.tail(12)

    recent_high = safe_float(
        recent["High"].max()
    )

    recent_low = safe_float(
        recent["Low"].min()
    )

    if None in (
        recent_high,
        recent_low
    ):
        return None

    # Initial risk based on ATR
    risk = atr_value * RISK_ATR_MULT

    risk = clamp(
        risk,
        atr_value * MIN_RISK_ATR,
        atr_value * MAX_RISK_ATR
    )

    if direction == "LONG":

        swing_sl = (
            recent_low -
            atr_value * 0.25
        )

        sl_distance = (
            entry -
            swing_sl
        )

        # Prevent excessive stop
        max_risk = (
            atr_value *
            MAX_RISK_ATR
        )

        min_risk = (
            atr_value *
            MIN_RISK_ATR
        )

        risk = max(
            risk,
            sl_distance
        )

        risk = clamp(
            risk,
            min_risk,
            max_risk
        )

        sl = entry - risk

        if sl >= entry:
            return None

    else:

        swing_sl = (
            recent_high +
            atr_value * 0.25
        )

        sl_distance = (
            swing_sl -
            entry
        )

        max_risk = (
            atr_value *
            MAX_RISK_ATR
        )

        min_risk = (
            atr_value *
            MIN_RISK_ATR
        )

        risk = max(
            risk,
            sl_distance
        )

        risk = clamp(
            risk,
            min_risk,
            max_risk
        )

        sl = entry + risk

        if sl <= entry:
            return None

    if signal["strong"]:

        tp1_r = STRONG_TP1_R
        tp2_r = STRONG_TP2_R
        tp3_r = STRONG_TP3_R

    else:

        tp1_r = TP1_R
        tp2_r = TP2_R
        tp3_r = TP3_R

    if direction == "LONG":

        tp1 = entry + risk * tp1_r
        tp2 = entry + risk * tp2_r
        tp3 = entry + risk * tp3_r

    else:

        tp1 = entry - risk * tp1_r
        tp2 = entry - risk * tp2_r
        tp3 = entry - risk * tp3_r

    return {
        "entry": round_price(entry),
        "sl": round_price(sl),
        "tp1": round_price(tp1),
        "tp2": round_price(tp2),
        "tp3": round_price(tp3),
        "risk": round_price(
            abs(entry - sl)
        ),
    }


# ============================================================
# COOLDOWN
# ============================================================

def parse_time(value):
    if not value:
        return None

    try:
        dt = datetime.fromisoformat(
            value
        )

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=KST
            )

        return dt.astimezone(KST)

    except Exception:
        return None


def cooldown_active(
    state,
    direction
):
    now = now_kst()

    # Last SL
    last_sl = parse_time(
        state.get("last_sl_time")
    )

    if (
        last_sl and
        state.get("last_sl_direction") ==
        direction
    ):
        elapsed = (
            now - last_sl
        ).total_seconds() / 60

        if elapsed < SL_COOLDOWN_MINUTES:
            print(
                f"[COOLDOWN] SL "
                f"{SL_COOLDOWN_MINUTES - elapsed:.1f}m"
            )
            return True

    # Last exit
    last_exit = parse_time(
        state.get("last_exit_time")
    )

    if last_exit:

        elapsed = (
            now - last_exit
        ).total_seconds() / 60

        reason = state.get(
            "last_exit_reason"
        )

        limit = (
            TP3_COOLDOWN_MINUTES
            if reason == "TP3"
            else SIGNAL_COOLDOWN_MINUTES
        )

        if elapsed < limit:
            print(
                f"[COOLDOWN] "
                f"{limit - elapsed:.1f}m"
            )
            return True

    return False


# ============================================================
# ENTRY ALERT
# ============================================================

def send_entry_alert(
    state,
    signal
):
    direction = signal["direction"]

    emoji = "🟢" if direction == "LONG" else "🔴"

    message = f"""
🥇 <b>금 선물 스마트 시그널 V17.2</b>

{emoji} <b>{escape(direction)} 신규 진입</b>

━━━━━━━━━━━━━━━━━━

💰 Entry
<b>${state["entry"]:.2f}</b>

🛑 SL
<b>${state["sl"]:.2f}</b>

🎯 TP1
<b>${state["tp1"]:.2f}</b>

🎯 TP2
<b>${state["tp2"]:.2f}</b>

🎯 TP3
<b>${state["tp3"]:.2f}</b>

━━━━━━━━━━━━━━━━━━

📊 M15 Score
<b>{signal["m15_score"]}/8</b>

📊 5M Score
<b>{signal["five_score"]}/4</b>

📈 RSI
<b>{signal["rsi"]:.2f}</b>

📐 ADX
<b>{signal["adx"]:.2f}</b>

📏 ATR
<b>{signal["atr"]:.2f}</b>

🕐 H1 Trend
<b>{escape(signal["h1"])}</b>

━━━━━━━━━━━━━━━━━━

🔒 <b>ONE POSITION LOCK</b>

TP1 → SL Entry
TP2 → SL TP1
TP3 → Position Exit
"""

    send_telegram(
        message.strip()
    )


# ============================================================
# TP / SL ALERT
# ============================================================

def send_tp1_alert(
    state,
    price,
    emergency=False
):
    suffix = (
        "\n⚠️ <b>Emergency Price Check</b>"
        if emergency
        else ""
    )

    message = f"""
🥇 <b>금 선물 스마트 시그널 V17.2</b>

🎯 <b>{escape(state["direction"])} TP1 도달</b>

현재가
<b>${price:.2f}</b>

TP1
<b>${state["tp1"]:.2f}</b>

🔐 SL → ENTRY
<b>${state["entry"]:.2f}</b>
{suffix}
"""

    send_telegram(
        message.strip()
    )


def send_tp2_alert(
    state,
    price,
    emergency=False
):
    suffix = (
        "\n⚠️ <b>Emergency Price Check</b>"
        if emergency
        else ""
    )

    message = f"""
🥇 <b>금 선물 스마트 시그널 V17.2</b>

🎯 <b>{escape(state["direction"])} TP2 도달</b>

현재가
<b>${price:.2f}</b>

TP2
<b>${state["tp2"]:.2f}</b>

🔐 SL → TP1
<b>${state["tp1"]:.2f}</b>
{suffix}
"""

    send_telegram(
        message.strip()
    )


def send_tp3_alert(
    state,
    price,
    emergency=False
):
    suffix = (
        "\n⚠️ <b>Emergency Price Check</b>"
        if emergency
        else ""
    )

    message = f"""
🥇 <b>금 선물 스마트 시그널 V17.2</b>

🏆 <b>{escape(state["direction"])} TP3 최종 도달</b>

현재가
<b>${price:.2f}</b>

TP3
<b>${state["tp3"]:.2f}</b>

✅ <b>포지션 종료</b>
{suffix}
"""

    send_telegram(
        message.strip()
    )


def send_sl_alert(
    state,
    price,
    emergency=False
):
    suffix = (
        "\n⚠️ <b>Emergency Price Check</b>"
        if emergency
        else ""
    )

    message = f"""
🥇 <b>금 선물 스마트 시그널 V17.2</b>

🛑 <b>{escape(state["direction"])} SL 청산</b>

현재가
<b>${price:.2f}</b>

SL
<b>${state["sl"]:.2f}</b>

❌ <b>포지션 종료</b>
{suffix}
"""

    send_telegram(
        message.strip()
    )


# ============================================================
# EXIT
# ============================================================

def close_position(
    state,
    reason,
    price
):
    direction = state.get(
        "direction"
    )

    old_state = dict(state)

    state["status"] = "IDLE"
    state["direction"] = None

    state["entry"] = None
    state["sl"] = None
    state["tp1"] = None
    state["tp2"] = None
    state["tp3"] = None
    state["risk"] = None

    state["stage"] = "INITIAL"

    state["last_exit_time"] = iso_now()
    state["last_exit_reason"] = reason
    state["last_exit_direction"] = direction

    if reason == "SL":
        state["last_sl_time"] = iso_now()
        state["last_sl_direction"] = direction

    save_state(state)

    write_log(
        f"POSITION_EXIT_{reason}",
        state,
        {
            "exit_price": price,
            "old_entry": old_state.get("entry"),
            "old_sl": old_state.get("sl"),
            "old_tp1": old_state.get("tp1"),
            "old_tp2": old_state.get("tp2"),
            "old_tp3": old_state.get("tp3"),
        }
    )

    print(
        f"[EXIT] {direction} "
        f"{reason} @ {price}"
    )


# ============================================================
# PRECISE 1M MONITOR
# ============================================================

def process_monitor_candle(
    state,
    candle_time,
    candle,
):
    if state.get("status") != "ACTIVE":
        return False

    direction = state.get(
        "direction"
    )

    high = safe_float(
        candle["High"]
    )

    low = safe_float(
        candle["Low"]
    )

    close = safe_float(
        candle["Close"]
    )

    if None in (
        high,
        low,
        close
    ):
        return False

    sl = safe_float(
        state.get("sl")
    )

    tp1 = safe_float(
        state.get("tp1")
    )

    tp2 = safe_float(
        state.get("tp2")
    )

    tp3 = safe_float(
        state.get("tp3")
    )

    entry = safe_float(
        state.get("entry")
    )

    if None in (
        sl,
        tp1,
        tp2,
        tp3,
        entry
    ):
        return False

    stage = state.get(
        "stage",
        "INITIAL"
    )

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        # Conservative ambiguity:
        # SL is checked first.
        if low <= sl:

            send_sl_alert(
                state,
                sl
            )

            close_position(
                state,
                "SL",
                sl
            )

            return True

        if stage == "INITIAL":

            if high >= tp1:

                state["stage"] = "TP1_BE"
                state["sl"] = entry

                send_tp1_alert(
                    state,
                    tp1
                )

                save_state(state)

                write_log(
                    "TP1_HIT",
                    state,
                    {
                        "price": tp1,
                        "candle_time":
                            str(candle_time)
                    }
                )

                stage = "TP1_BE"

        if stage == "TP1_BE":

            # After TP1, SL is entry.
            if low <= entry:

                send_sl_alert(
                    state,
                    entry
                )

                close_position(
                    state,
                    "SL",
                    entry
                )

                return True

            if high >= tp2:

                state["stage"] = "TP2_TRAIL"
                state["sl"] = tp1

                send_tp2_alert(
                    state,
                    tp2
                )

                save_state(state)

                write_log(
                    "TP2_HIT",
                    state,
                    {
                        "price": tp2,
                        "candle_time":
                            str(candle_time)
                    }
                )

                stage = "TP2_TRAIL"

        if stage == "TP2_TRAIL":

            # TP2 stage SL = TP1
            if low <= tp1:

                send_sl_alert(
                    state,
                    tp1
                )

                close_position(
                    state,
                    "SL",
                    tp1
                )

                return True

            if high >= tp3:

                send_tp3_alert(
                    state,
                    tp3
                )

                close_position(
                    state,
                    "TP3",
                    tp3
                )

                return True

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    elif direction == "SHORT":

        # Conservative ambiguity:
        # SL is checked first.
        if high >= sl:

            send_sl_alert(
                state,
                sl
            )

            close_position(
                state,
                "SL",
                sl
            )

            return True

        if stage == "INITIAL":

            if low <= tp1:

                state["stage"] = "TP1_BE"
                state["sl"] = entry

                send_tp1_alert(
                    state,
                    tp1
                )

                save_state(state)

                write_log(
                    "TP1_HIT",
                    state,
                    {
                        "price": tp1,
                        "candle_time":
                            str(candle_time)
                    }
                )

                stage = "TP1_BE"

        if stage == "TP1_BE":

            if high >= entry:

                send_sl_alert(
                    state,
                    entry
                )

                close_position(
                    state,
                    "SL",
                    entry
                )

                return True

            if low <= tp2:

                state["stage"] = "TP2_TRAIL"
                state["sl"] = tp1

                send_tp2_alert(
                    state,
                    tp2
                )

                save_state(state)

                write_log(
                    "TP2_HIT",
                    state,
                    {
                        "price": tp2,
                        "candle_time":
                            str(candle_time)
                    }
                )

                stage = "TP2_TRAIL"

        if stage == "TP2_TRAIL":

            if high >= tp1:

                send_sl_alert(
                    state,
                    tp1
                )

                close_position(
                    state,
                    "SL",
                    tp1
                )

                return True

            if low <= tp3:

                send_tp3_alert(
                    state,
                    tp3
                )

                close_position(
                    state,
                    "TP3",
                    tp3
                )

                return True

    return False


def get_new_candles(
    df,
    last_monitor_time
):
    if df is None or df.empty:
        return pd.DataFrame()

    data = completed(df)

    if data.empty:
        return data

    if not last_monitor_time:
        # First run:
        # only process recent candles,
        # preventing an old SL/TP event storm.
        return data.tail(10)

    last_dt = parse_time(
        last_monitor_time
    )

    if last_dt is None:
        return data.tail(10)

    result = data[
        data.index >
        pd.Timestamp(last_dt)
    ]

    return result


def monitor_precise_1m(
    state,
    one_m
):
    candles = get_new_candles(
        one_m,
        state.get(
            "last_monitor_time"
        )
    )

    if candles.empty:
        print(
            "[MONITOR] "
            "No new completed 1M candles"
        )

        return state

    print(
        f"[MONITOR] "
        f"Processing {len(candles)} "
        f"new 1M candles"
    )

    for timestamp, candle in candles.iterrows():

        if state.get("status") != "ACTIVE":
            break

        process_monitor_candle(
            state,
            timestamp,
            candle
        )

        state["last_monitor_time"] = (
            timestamp.isoformat()
        )

        state["last_price"] = safe_float(
            candle["Close"]
        )

        save_state(state)

    return state


# ============================================================
# EMERGENCY CURRENT PRICE
# ============================================================

def get_emergency_price():
    """
    Try Yahoo fast_info.

    This is NOT guaranteed tick-by-tick realtime.
    It is only a fallback when 1M candles are stale.
    """

    try:
        ticker = yf.Ticker(
            TICKER
        )

        fast = ticker.fast_info

        price = None

        for key in [
            "last_price",
            "regularMarketPrice",
        ]:
            try:
                price = safe_float(
                    fast.get(key)
                )
            except Exception:
                pass

            if price is not None:
                break

        if price is None:
            return None, None

        print(
            f"[EMERGENCY PRICE] "
            f"${price:.2f}"
        )

        return price, now_kst()

    except Exception as e:
        print(
            "[EMERGENCY PRICE ERROR]",
            repr(e)
        )

        return None, None


def emergency_monitor(
    state,
    one_m_age
):
    """
    Fallback protection when 1M is stale.

    Priority:
    1. Current price below/above SL
    2. TP3
    3. TP2
    4. TP1
    """

    if state.get("status") != "ACTIVE":
        return state

    if one_m_age > MAX_EMERGENCY_PRICE_AGE_MINUTES:
        print(
            "[EMERGENCY BLOCK] "
            f"1M age {one_m_age:.1f}m > "
            f"{MAX_EMERGENCY_PRICE_AGE_MINUTES}m"
        )
        return state

    price, price_time = (
        get_emergency_price()
    )

    if price is None:
        print(
            "[EMERGENCY] "
            "Unable to obtain current price"
        )
        return state

    state["last_price"] = price

    direction = state.get(
        "direction"
    )

    sl = safe_float(
        state.get("sl")
    )

    tp1 = safe_float(
        state.get("tp1")
    )

    tp2 = safe_float(
        state.get("tp2")
    )

    tp3 = safe_float(
        state.get("tp3")
    )

    entry = safe_float(
        state.get("entry")
    )

    if None in (
        sl,
        tp1,
        tp2,
        tp3,
        entry
    ):
        return state

    stage = state.get(
        "stage",
        "INITIAL"
    )

    print(
        "[EMERGENCY CHECK]",
        direction,
        "price=",
        price,
        "sl=",
        sl,
        "stage=",
        stage
    )

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        # SL FIRST
        if price <= sl:

            send_sl_alert(
                state,
                price,
                emergency=True
            )

            close_position(
                state,
                "SL",
                price
            )

            return state

        if stage == "INITIAL":

            if price >= tp1:

                state["stage"] = "TP1_BE"
                state["sl"] = entry

                send_tp1_alert(
                    state,
                    price,
                    emergency=True
                )

                save_state(state)

                write_log(
                    "TP1_HIT_EMERGENCY",
                    state,
                    {
                        "price": price,
                        "price_time":
                            str(price_time)
                    }
                )

                stage = "TP1_BE"

        if stage == "TP1_BE":

            if price <= entry:

                send_sl_alert(
                    state,
                    price,
                    emergency=True
                )

                close_position(
                    state,
                    "SL",
                    price
                )

                return state

            if price >= tp2:

                state["stage"] = "TP2_TRAIL"
                state["sl"] = tp1

                send_tp2_alert(
                    state,
                    price,
                    emergency=True
                )

                save_state(state)

                write_log(
                    "TP2_HIT_EMERGENCY",
                    state,
                    {
                        "price": price,
                        "price_time":
                            str(price_time)
                    }
                )

                stage = "TP2_TRAIL"

        if stage == "TP2_TRAIL":

            if price <= tp1:

                send_sl_alert(
                    state,
                    price,
                    emergency=True
                )

                close_position(
                    state,
                    "SL",
                    price
                )

                return state

            if price >= tp3:

                send_tp3_alert(
                    state,
                    price,
                    emergency=True
                )

                close_position(
                    state,
                    "TP3",
                    price
                )

                return state

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    elif direction == "SHORT":

        # SL FIRST
        if price >= sl:

            send_sl_alert(
                state,
                price,
                emergency=True
            )

            close_position(
                state,
                "SL",
                price
            )

            return state

        if stage == "INITIAL":

            if price <= tp1:

                state["stage"] = "TP1_BE"
                state["sl"] = entry

                send_tp1_alert(
                    state,
                    price,
                    emergency=True
                )

                save_state(state)

                write_log(
                    "TP1_HIT_EMERGENCY",
                    state,
                    {
                        "price": price,
                        "price_time":
                            str(price_time)
                    }
                )

                stage = "TP1_BE"

        if stage == "TP1_BE":

            if price >= entry:

                send_sl_alert(
                    state,
                    price,
                    emergency=True
                )

                close_position(
                    state,
                    "SL",
                    price
                )

                return state

            if price <= tp2:

                state["stage"] = "TP2_TRAIL"
                state["sl"] = tp1

                send_tp2_alert(
                    state,
                    price,
                    emergency=True
                )

                save_state(state)

                write_log(
                    "TP2_HIT_EMERGENCY",
                    state,
                    {
                        "price": price,
                        "price_time":
                            str(price_time)
                    }
                )

                stage = "TP2_TRAIL"

        if stage == "TP2_TRAIL":

            if price >= tp1:

                send_sl_alert(
                    state,
                    price,
                    emergency=True
                )

                close_position(
                    state,
                    "SL",
                    price
                )

                return state

            if price <= tp3:

                send_tp3_alert(
                    state,
                    price,
                    emergency=True
                )

                close_position(
                    state,
                    "TP3",
                    price
                )

                return state

    save_state(state)

    return state


# ============================================================
# POSITION STATE DISPLAY
# ============================================================

def print_state(state):
    print()
    print("====================================")
    print("STATE")
    print(
        f"Status    : "
        f"{state.get('status')}"
    )
    print(
        f"Direction : "
        f"{state.get('direction')}"
    )
    print(
        f"Entry     : "
        f"{state.get('entry')}"
    )
    print(
        f"SL        : "
        f"{state.get('sl')}"
    )
    print(
        f"TP1       : "
        f"{state.get('tp1')}"
    )
    print(
        f"TP2       : "
        f"{state.get('tp2')}"
    )
    print(
        f"TP3       : "
        f"{state.get('tp3')}"
    )
    print(
        f"Stage     : "
        f"{state.get('stage')}"
    )
    print("====================================")
    print()


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
        f" KST: {iso_now()}"
    )
    print("====================================")

    state, migrated = load_state()

    print_state(state)

    # ========================================================
    # DOWNLOAD
    # ========================================================

    print(
        "Downloading market data..."
    )

    one_m_raw = download_data(
        "1m",
        "7d"
    )

    five_raw = download_data(
        "5m",
        "30d"
    )

    m15_raw = download_data(
        "15m",
        "60d"
    )

    one_h_raw = download_data(
        "1h",
        "60d"
    )

    if one_m_raw.empty:
        print(
            "[FATAL] 1M data unavailable"
        )

        # If already active, attempt emergency
        if state.get("status") == "ACTIVE":
            emergency_monitor(
                state,
                float("inf")
            )

        return

    if five_raw.empty or m15_raw.empty:
        print(
            "[FATAL] Higher timeframe data unavailable"
        )

        return

    # ========================================================
    # FRESHNESS
    # ========================================================

    one_m = completed(
        one_m_raw
    )

    five = completed(
        five_raw
    )

    m15 = completed(
        m15_raw
    )

    one_h = completed(
        one_h_raw
    )

    age_1m = print_freshness(
        "1M",
        one_m
    )

    age_5m = print_freshness(
        "5M",
        five
    )

    age_15m = print_freshness(
        "15M",
        m15
    )

    age_1h = print_freshness(
        "1H",
        one_h
    )

    # ========================================================
    # ACTIVE POSITION
    # ========================================================

    if state.get("status") == "ACTIVE":

        print(
            "[POSITION] "
            "Existing ACTIVE position"
        )

        # ----------------------------------------------------
        # First try
