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
# GOLD FUTURES SMART SIGNAL BOT V17.3
# Stable Hybrid + Emergency Protection
# ============================================================

VERSION = "17.3.0"

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

LONG_RSI_MIN = 52
LONG_RSI_MAX = 68

SHORT_RSI_MIN = 32
SHORT_RSI_MAX = 48

MIN_BODY_RATIO = 0.35

MAX_ENTRY_DISTANCE_ATR = 1.20

# ============================================================
# RISK
# ============================================================

RISK_ATR_MULT = 1.80
MIN_RISK_ATR = 1.20
MAX_RISK_ATR = 2.80

# ============================================================
# TAKE PROFIT
# ============================================================

TP1_R = 1.20
TP2_R = 2.00
TP3_R = 3.00

STRONG_TP1_R = 1.30
STRONG_TP2_R = 2.20
STRONG_TP3_R = 3.50


# ============================================================
# COOLDOWN
# ============================================================

SIGNAL_COOLDOWN_MINUTES = 45
SL_COOLDOWN_MINUTES = 120
TP3_COOLDOWN_MINUTES = 15


# ============================================================
# DATA
# ============================================================

DATA_RETRIES = 3

MAX_FRESH_1M_MINUTES = 8
MAX_EMERGENCY_PRICE_AGE_MINUTES = 30

MAX_5M_FRESH_MINUTES = 15
MAX_15M_FRESH_MINUTES = 30
MAX_1H_FRESH_MINUTES = 90


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": "Gold-Futures-Smart-Signal-Bot/17.3"
    }
)


# ============================================================
# BASIC
# ============================================================

def now_kst():
    return datetime.now(KST)


def iso_now():
    return now_kst().isoformat()


def safe_float(value, default=None):
    try:
        if value is None:
            return default

        value = float(value)

        if not math.isfinite(value):
            return default

        return value

    except Exception:
        return default


def round_price(value):
    value = safe_float(value)

    if value is None:
        return None

    return round(value, 2)


def clamp(value, low, high):
    return max(low, min(high, value))


def escape(value):
    return html.escape(str(value))


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_TOKEN:
        print("[TELEGRAM] TOKEN MISSING")
        return False

    if not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] CHAT_ID MISSING")
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

        response = SESSION.post(
            url,
            json=payload,
            timeout=15
        )

        if response.ok:
            print("[TELEGRAM] SENT")
            return True

        print(
            "[TELEGRAM ERROR]",
            response.status_code,
            response.text[:500]
        )

    except Exception as e:

        print(
            "[TELEGRAM EXCEPTION]",
            repr(e)
        )

    return False


# ============================================================
# STATE
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

        "last_run": None,

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

    os.replace(
        temp_path,
        path
    )


def load_state():

    state = default_state()

    if not os.path.exists(STATE_FILE):

        print("[STATE] No state file")

        return state


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

        print(
            "[STATE LOAD ERROR]",
            repr(e)
        )

        return state


    old_version = str(
        state.get(
            "version",
            ""
        )
    )

    migrated = False


    # ========================================================
    # OLD TP FLAGS -> STAGE
    # ========================================================

    if "stage" not in state:

        if state.get("tp2_hit"):

            state["stage"] = "TP2_TRAIL"

        elif state.get("tp1_hit"):

            state["stage"] = "TP1_BE"

        else:

            state["stage"] = "INITIAL"

        migrated = True


    # ========================================================
    # ACTIVE OLD POSITION MIGRATION
    # ========================================================

    if state.get("status") == "ACTIVE":

        if state.get("tp2_hit") is True:

            state["stage"] = "TP2_TRAIL"

            tp1 = safe_float(
                state.get("tp1")
            )

            if tp1 is not None:
                state["sl"] = tp1

            migrated = True

        elif state.get("tp1_hit") is True:

            state["stage"] = "TP1_BE"

            entry = safe_float(
                state.get("entry")
            )

            if entry is not None:
                state["sl"] = entry

            migrated = True


    if old_version != VERSION:
        migrated = True


    state["version"] = VERSION
    state["migration_done"] = True


    if migrated:

        print(
            f"[STATE MIGRATION] "
            f"{old_version or 'UNKNOWN'} -> {VERSION}"
        )

        save_state(state)

    return state


def save_state(state):

    state["version"] = VERSION

    try:

        atomic_write_json(
            STATE_FILE,
            state
        )

    except Exception as e:

        print(
            "[STATE SAVE ERROR]",
            repr(e)
        )


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

    except Exception as e:

        print(
            "[LOG LOAD ERROR]",
            repr(e)
        )

    return []


def write_log(
    event,
    state=None,
    extra=None
):

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

    logs = logs[-500:]

    try:

        atomic_write_json(
            LOG_FILE,
            logs
        )

    except Exception as e:

        print(
            "[LOG ERROR]",
            repr(e)
        )


# ============================================================
# DATA DOWNLOAD
# ============================================================

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


def download_data(
    interval,
    period
):

    last_error = None

    for attempt in range(
        1,
        DATA_RETRIES + 1
    ):

        try:

            print(
                f"[DATA] {interval} "
                f"attempt "
                f"{attempt}/{DATA_RETRIES}"
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


            # =================================================
            # MULTI INDEX
            # =================================================

            if isinstance(
                df.columns,
                pd.MultiIndex
            ):

                try:

                    if (
                        TICKER in
                        df.columns.get_level_values(-1)
                    ):

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
                        c[0]
                        if isinstance(c, tuple)
                        else c
                        for c in df.columns
                    ]


            required = [
                "Open",
                "High",
                "Low",
                "Close",
            ]


            for column in required:

                if column not in df.columns:

                    raise RuntimeError(
                        f"missing {column}"
                    )


            df = df[
                required
            ].copy()


            for column in required:

                df[column] = pd.to_numeric(
                    df[column],
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
                f"[DATA ERROR] "
                f"{interval}: "
                f"{repr(e)}"
            )

            time.sleep(1)


    print(
        f"[DATA FAILED] "
        f"{interval}: "
        f"{repr(last_error)}"
    )

    return pd.DataFrame()


# ============================================================
# COMPLETED CANDLES
# ============================================================

def completed(df):

    if df is None or df.empty:
        return df

    result = df.copy()

    now = pd.Timestamp.now(
        tz=KST
    )

    result = result[
        result.index <= now
    ]

    # Remove current forming candle.
    if len(result) > 1:
        result = result.iloc[:-1]

    return result


# ============================================================
# FRESHNESS
# ============================================================

def data_age_minutes(df):

    if df is None or df.empty:
        return float("inf")

    timestamp = df.index[-1]

    now = pd.Timestamp.now(
        tz=KST
    )

    age = (
        now - timestamp
    ).total_seconds() / 60.0

    return max(
        0.0,
        age
    )


def print_freshness(
    name,
    df
):

    age = data_age_minutes(df)

    print(
        f"[DATA FRESHNESS] "
        f"{name}: "
        f"{age:.1f} min"
    )

    return age


# ============================================================
# INDICATORS
# ============================================================

def ema(
    series,
    length
):

    return series.ewm(
        span=length,
        adjust=False
    ).mean()


def rsi(
    series,
    length=14
):

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

    rs = (
        avg_gain /
        avg_loss.replace(
            0,
            np.nan
        )
    )

    result = (
        100 -
        (
            100 /
            (1 + rs)
        )
    )

    return result.fillna(50)


def atr(
    df,
    length=14
):

    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    previous = close.shift(1)

    tr1 = high - low
    tr2 = (
        high - previous
    ).abs()

    tr3 = (
        low - previous
    ).abs()

    tr = pd.concat(
        [
            tr1,
            tr2,
            tr3
        ],
        axis=1
    ).max(axis=1)

    return tr.ewm(
        alpha=1 / length,
        adjust=False
    ).mean()


def adx(
    df,
    length=14
):

    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where(
            (
                (up_move > down_move) &
                (up_move > 0)
            ),
            up_move,
            0
        ),
        index=df.index
    )

    minus_dm = pd.Series(
        np.where(
            (
                (down_move > up_move) &
                (down_move > 0)
            ),
            down_move,
            0
        ),
        index=df.index
    )

    tr = pd.concat(
        [
            high - low,
            (
                high -
                close.shift(1)
            ).abs(),
            (
                low -
                close.shift(1)
            ).abs()
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
        (
            plus_di -
            minus_di
        ).abs() /
        (
            plus_di +
            minus_di
        ).replace(
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
        (
            result["Close"] -
            result["Low"]
        ) /
        result["Range"]
    ).fillna(0.5)

    return result


# ============================================================
# SCORING
# ============================================================

def max_entry_distance(
    atr_value
):

    return (
        atr_value *
        MAX_ENTRY_DISTANCE_ATR
    )


def score_m15(df):

    if df is None or len(df) < 60:

        return {
            "long": 0,
            "short": 0,
            "max": 8
        }

    row = df.iloc[-1]
    previous = df.iloc[-2]

    long_score = 0
    short_score = 0

    close = safe_float(
        row["Close"]
    )

    ema20 = safe_float(
        row["EMA20"]
    )

    ema50 = safe_float(
        row["EMA50"]
    )

    previous_ema20 = safe_float(
        previous["EMA20"]
    )

    rsi_value = safe_float(
        row["RSI"]
    )

    adx_value = safe_float(
        row["ADX"]
    )

    atr_value = safe_float(
        row["ATR"]
    )

    body_ratio = safe_float(
        row["BodyRatio"],
        0
    )

    close_position = safe_float(
        row["ClosePosition"],
        0.5
    )

    if None in (
        close,
        ema20,
        ema50,
        previous_ema20,
        rsi_value,
        adx_value,
        atr_value
    ):

        return {
            "long": 0,
            "short": 0,
            "max": 8
        }


    # LONG

    if close > ema20:
        long_score += 1

    if ema20 > ema50:
        long_score += 1

    if ema20 > previous_ema20:
        long_score += 1

    if (
        body_ratio >= MIN_BODY_RATIO and
        close > row["Open"]
    ):
        long_score += 1

    if close_position >= 0.60:
        long_score += 1

    if LONG_RSI_MIN <= rsi_value <= LONG_RSI_MAX:
        long_score += 1

    if adx_value >= MIN_ADX:
        long_score += 1

    if (
        close - ema20
    ) <= max_entry_distance(
        atr_value
    ):
        long_score += 1


    # SHORT

    if close < ema20:
        short_score += 1

    if ema20 < ema50:
        short_score += 1

    if ema20 < previous_ema20:
        short_score += 1

    if (
        body_ratio >= MIN_BODY_RATIO and
        close < row["Open"]
    ):
        short_score += 1

    if close_position <= 0.40:
        short_score += 1

    if SHORT_RSI_MIN <= rsi_value <= SHORT_RSI_MAX:
        short_score += 1

    if adx_value >= MIN_ADX:
        short_score += 1

    if (
        ema20 - close
    ) <= max_entry_distance(
        atr_value
    ):
        short_score += 1


    return {
        "long": long_score,
        "short": short_score,
        "max": 8
    }


def score_5m(
    df,
    direction
):

    if df is None or len(df) < 50:
        return 0

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

    body_ratio = safe_float(
        row["BodyRatio"],
        0
    )

    close_position = safe_float(
        row["ClosePosition"],
        0.5
    )

    if None in (
        close,
        ema20,
        ema50
    ):
        return 0

    score = 0

    if direction == "LONG":

        if close > ema20:
            score += 1

        if ema20 > ema50:
            score += 1

        if (
            body_ratio >= MIN_BODY_RATIO and
            close > row["Open"]
        ):
            score += 1

        if close_position >= 0.55:
            score += 1

    else:

        if close < ema20:
            score += 1

        if ema20 < ema50:
            score += 1

        if (
            body_ratio >= MIN_BODY_RATIO and
            close < row["Open"]
        ):
            score += 1

        if close_position <= 0.45:
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
# SIGNAL
# ============================================================

def find_signal(
    m15,
    five,
    one_h
):

    if (
        m15.empty or
        five.empty or
        one_h.empty
    ):
        return None


    m15s = add_indicators(
        m15
    )

    fives = add_indicators(
        five
    )

    h1s = add_indicators(
        one_h
    )


    if (
        len(m15s) < 60 or
        len(fives) < 60 or
        len(h1s) < 60
    ):

        print(
            "[SIGNAL] "
            "Insufficient data"
        )

        return None


    m15_row = m15s.iloc[-1]
    five_row = fives.iloc[-1]

    scores = score_m15(
        m15s
    )

    h1 = h1_trend(
        h1s
    )

    candidates = []


    for direction in (
        "LONG",
        "SHORT"
    ):

        m15_score = (
            scores["long"]
            if direction == "LONG"
            else scores["short"]
        )

        if m15_score < MIN_M15_SCORE:
            continue


        five_score = score_5m(
            fives,
            direction
        )

        if five_score < MIN_5M_SCORE:
            continue


        close = safe_float(
            m15_row["Close"]
        )

        ema20 = safe_float(
            m15_row["EMA20"]
        )

        rsi_value = safe_float(
            m15_row["RSI"]
        )

        adx_value = safe_float(
            m15_row["ADX"]
        )

        atr_value = safe_float(
            m15_row["ATR"]
        )

        five_close = safe_float(
            five_row["Close"]
        )


        if None in (
            close,
            ema20,
            rsi_value,
            adx_value,
            atr_value,
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

            if close <= ema20:
                continue

            h1_bonus = (
                1 if h1 == "BULL"
                else 0
            )

        else:

            if not (
                SHORT_RSI_MIN <=
                rsi_value <=
                SHORT_RSI_MAX
            ):
                continue

            if close >= ema20:
                continue

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
                "rsi": rsi_value,
                "adx": adx_value,
                "atr": atr_value,
                "h1": h1,
                "m15_close": close,
                "five_close": five_close,
                "strong": (
                    m15_score >=
                    STRONG_M15_SCORE
                ),
                "total_score": total_score,
            }
        )


    if not candidates:

        print(
            "[SIGNAL] "
            "No valid signal"
        )

        return None


    candidates.sort(
        key=lambda x:
        x["total_score"],
        reverse=True
    )

    signal = candidates[0]


    signal["setup_id"] = make_setup_id(
        signal["direction"],
        signal["m15_close"],
        signal["five_close"],
        signal["m15_score"],
        signal["five_score"]
    )


    print(
        "[SIGNAL FOUND]",
        signal["direction"],
        "M15=",
        signal["m15_score"],
        "5M=",
        signal["five_score"],
        "RSI=",
        round(signal["rsi"], 2),
        "ADX=",
        round(signal["adx"], 2),
        "H1=",
        signal["h1"]
    )


    return signal


# ============================================================
# POSITION CALCULATION
# ============================================================

def calculate_position(
    signal,
    entry,
    m15
):

    direction = signal["direction"]

    atr_value = safe_float(
        signal["atr"]
    )

    if atr_value is None:
        return None


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


    risk = (
        atr_value *
        RISK_ATR_MULT
    )


    min_risk = (
        atr_value *
        MIN_RISK_ATR
    )

    max_risk = (
        atr_value *
        MAX_RISK_ATR
    )


    if direction == "LONG":

        swing_sl = (
            recent_low -
            atr_value * 0.25
        )

        swing_distance = (
            entry -
            swing_sl
        )

        risk = max(
            risk,
            swing_distance
        )

        risk = clamp(
            risk,
            min_risk,
            max_risk
        )

        sl = entry - risk


    else:

        swing_sl = (
            recent_high +
            atr_value * 0.25
        )

        swing_distance = (
            swing_sl -
            entry
        )

        risk = max(
            risk,
            swing_distance
        )

        risk = clamp(
            risk,
            min_risk,
            max_risk
        )

        sl = entry + risk


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
        "risk": round_price(risk),
    }


# ============================================================
# TIME
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


    last_sl = parse_time(
        state.get(
            "last_sl_time"
        )
    )

    if (
        last_sl and
        state.get(
            "last_sl_direction"
        ) == direction
    ):

        minutes = (
            now - last_sl
        ).total_seconds() / 60

        if minutes < SL_COOLDOWN_MINUTES:

            print(
                "[COOLDOWN] "
                f"SL "
                f"{SL_COOLDOWN_MINUTES - minutes:.1f}m"
            )

            return True


    last_exit = parse_time(
        state.get(
            "last_exit_time"
        )
    )

    if last_exit:

        minutes = (
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

        if minutes < limit:

            print(
                "[COOLDOWN] "
                f"{limit - minutes:.1f}m"
            )

            return True


    return False


# ============================================================
# ALERTS
# ============================================================

def send_entry_alert(
    state,
    signal
):

    direction = signal["direction"]

    emoji = (
        "🟢"
        if direction == "LONG"
        else "🔴"
    )

    message = f"""
🥇 <b>금 선물 스마트 시그널 V17.3</b>

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

    return send_telegram(
        message.strip()
    )


def send_tp_alert(
    state,
    level,
    price,
    emergency=False
):

    if level == "TP1":

        title = "🎯 TP1 도달"
        stop_text = (
            f"SL → ENTRY\n"
            f"<b>${state['entry']:.2f}</b>"
        )

    elif level == "TP2":

        title = "🎯 TP2 도달"
        stop_text = (
            f"SL → TP1\n"
            f"<b>${state['tp1']:.2f}</b>"
        )

    else:

        title = "🏆 TP3 최종 도달"
        stop_text = "✅ 포지션 종료"


    suffix = (
        "\n⚠️ <b>Emergency Price Check</b>"
        if emergency
        else ""
    )


    message = f"""
🥇 <b>금 선물 스마트 시그널 V17.3</b>

{title}

<b>{escape(state["direction"])}</b>

현재가
<b>${price:.2f}</b>

목표가
<b>${state[level.lower()]:.2f}</b>

{stop_text}
{suffix}
"""

    return send_telegram(
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
🥇 <b>금 선물 스마트 시그널 V17.3</b>

🛑 <b>{escape(state["direction"])} SL 청산</b>

현재가
<b>${price:.2f}</b>

SL
<b>${price:.2f}</b>

❌ <b>포지션 종료</b>
{suffix}
"""

    return send_telegram(
        message.strip()
    )


# ============================================================
# CLOSE POSITION
# ============================================================

def close_position(
    state,
    reason,
    price
):

    direction = state.get(
        "direction"
    )

    old = dict(state)

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
            "old_entry": old.get("entry"),
            "old_sl": old.get("sl"),
            "old_tp1": old.get("tp1"),
            "old_tp2": old.get("tp2"),
            "old_tp3": old.get("tp3"),
        }
    )


    print(
        "[EXIT]",
        direction,
        reason,
        "@",
        price
    )


# ============================================================
# MONITOR SINGLE 1M CANDLE
# ============================================================

def process_monitor_candle(
    state,
    candle_time,
    candle
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


    entry = safe_float(
        state.get("entry")
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

    if None in (
        entry,
        sl,
        tp1,
        tp2,
        tp3
    ):
        return False


    stage = state.get(
        "stage",
        "INITIAL"
    )


    # ========================================================
    # LONG
    # ========================================================

    if direction == "LONG":

        # SL first for conservative same-candle handling.
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

                send_tp_alert(
                    state,
                    "TP1",
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

                send_tp_alert(
                    state,
                    "TP2",
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

                send_tp_alert(
                    state,
                    "TP3",
                    tp3
                )

                close_position(
                    state,
                    "TP3",
                    tp3
                )

                return True


    # ========================================================
    # SHORT
    # ========================================================

    elif direction == "SHORT":

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

                send_tp_alert(
                    state,
                    "TP1",
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

                send_tp_alert(
                    state,
                    "TP2",
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

                send_tp_alert(
                    state,
                    "TP3",
                    tp3
                )

                close_position(
                    state,
                    "TP3",
                    tp3
                )

                return True


    return False


# ============================================================
# NEW 1M CANDLES
# ============================================================

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

        # Only recent candles on first monitoring run.
        return data.tail(10)


    last_dt = parse_time(
        last_monitor_time
    )

    if last_dt is None:

        return data.tail(10)


    timestamp = pd.Timestamp(
        last_dt
    )


    return data[
        data.index > timestamp
    ]


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
        "[MONITOR]",
        f"Processing {len(candles)} "
        "new 1M candles"
    )


   
