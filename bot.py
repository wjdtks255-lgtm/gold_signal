import os
import json
import math
import time
import html
import traceback
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests
import yfinance as yf


# ============================================================
# GOLD FUTURES SMART SIGNAL BOT
# V17.1 STABLE HYBRID
#
# V15 Stability
# +
# V16 Aegis Fusion
# +
# Retry / Data Freshness / HTML Safety
#
# ONE POSITION ONLY
# MTF: H1 -> M15 -> 5M
# Monitoring: 1M completed candle
#
# TP1 -> BREAK EVEN
# TP2 -> SL = TP1
# TP3 -> FINAL EXIT
# ============================================================

VERSION = "17.1.0"
STATE_VERSION = VERSION

TICKER = "GC=F"

STATE_FILE = "signal_state.json"
LOG_FILE = "bot_log.json"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

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

# Long RSI
LONG_RSI_MIN = 52.0
LONG_RSI_MAX = 68.0

# Short RSI
SHORT_RSI_MIN = 32.0
SHORT_RSI_MAX = 48.0

MIN_BODY_RATIO = 0.35

MIN_EMA_SEPARATION_ATR = 0.10
MAX_ENTRY_DISTANCE_ATR = 1.20


# ============================================================
# COOLDOWN
# ============================================================

SL_COOLDOWN_MINUTES = 120
EXIT_COOLDOWN_MINUTES = 15
SIGNAL_COOLDOWN_MINUTES = 45


# ============================================================
# RISK
# ============================================================

ENTRY_RISK_ATR = 1.80
MIN_RISK_ATR = 1.20
MAX_RISK_ATR = 2.80


# ============================================================
# TAKE PROFIT
# ============================================================

BASE_TP1_R = 1.20
BASE_TP2_R = 2.00
BASE_TP3_R = 3.00

STRONG_TP1_R = 1.30
STRONG_TP2_R = 2.20
STRONG_TP3_R = 3.50


# ============================================================
# DATA
# ============================================================

DATA_RETRY_COUNT = 3
DATA_RETRY_DELAY_SECONDS = 2

# 마지막 완료 봉이 이 시간보다 오래되면
# 새로운 SIGNAL을 만들지 않는다.
MAX_1M_DATA_DELAY_MINUTES = 8
MAX_5M_DATA_DELAY_MINUTES = 12
MAX_15M_DATA_DELAY_MINUTES = 25


# ============================================================
# TIMEZONE
# ============================================================

KST = timezone(timedelta(hours=9))


# ============================================================
# BASIC HELPERS
# ============================================================

def now_kst():
    return datetime.now(KST)


def now_iso():
    return now_kst().isoformat()


def safe_float(value, default=0.0):
    try:
        value = float(value)

        if not math.isfinite(value):
            return default

        return value

    except Exception:
        return default


def fmt_price(value):
    value = safe_float(value)

    if value == 0:
        return "-"

    return f"${value:,.2f}"


def fmt_r(value):
    value = safe_float(value)

    return f"{value:.2f}R"


def html_escape(value):
    return html.escape(str(value), quote=False)


def parse_timestamp(value):
    """
    모든 timestamp를 KST aware Timestamp로 통일.
    """

    if value is None:
        return None

    try:
        ts = pd.Timestamp(value)

        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")

        ts = ts.tz_convert("Asia/Seoul")

        return ts

    except Exception:
        return None


def timestamp_iso(value):
    ts = parse_timestamp(value)

    if ts is None:
        return None

    return ts.isoformat()


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Token or Chat ID missing")
        print(message)
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

        response = requests.post(
            url,
            json=payload,
            timeout=15,
        )

        if response.status_code != 200:

            print(
                "[TELEGRAM ERROR]",
                response.status_code,
                response.text[:500],
            )

            return False

        return True

    except Exception as e:

        print("[TELEGRAM EXCEPTION]", e)

        return False


# ============================================================
# STATE
# ============================================================

def default_state():

    return {
        "version": STATE_VERSION,

        "status": "IDLE",

        "direction": None,

        "entry": 0.0,
        "sl": 0.0,
        "tp1": 0.0,
        "tp2": 0.0,
        "tp3": 0.0,

        "risk": 0.0,

        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,

        "position_stage": "NONE",

        "signal_time": None,
        "signal_id": None,

        "m15_score": 0,
        "five_score": 0,

        "rsi": 0.0,
        "adx": 0.0,

        "last_exit_time": None,
        "last_exit_reason": None,
        "last_exit_direction": None,
        "last_exit_price": 0.0,
        "last_exit_r": 0.0,

        "last_exit_setup_id": None,

        "last_signal_time": None,

        "last_sl_time": None,
        "last_sl_direction": None,

        "last_monitor_time": None,
        "last_candle_time": None,
        "last_price": 0.0,
    }


def atomic_write_json(filename, data):

    temp_file = filename + ".tmp"

    with open(
        temp_file,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )

        f.flush()
        os.fsync(f.fileno())

    os.replace(temp_file, filename)


def save_state(state):

    try:

        state["version"] = STATE_VERSION

        atomic_write_json(
            STATE_FILE,
            state,
        )

        return True

    except Exception as e:

        print("[STATE SAVE ERROR]", e)

        return False


def load_state():

    state = default_state()

    if not os.path.exists(STATE_FILE):

        return state

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8",
        ) as f:

            saved = json.load(f)

        if isinstance(saved, dict):

            for key in state:

                if key in saved:

                    state[key] = saved[key]

    except Exception as e:

        print("[STATE LOAD ERROR]", e)

        return state


    # --------------------------------------------------------
    # STATE MIGRATION
    # --------------------------------------------------------

    if state.get("status") == "ACTIVE":

        tp1_hit = bool(state.get("tp1_hit"))
        tp2_hit = bool(state.get("tp2_hit"))

        if tp2_hit:

            state["position_stage"] = "TP2_TRAIL"

            if safe_float(state.get("tp1")) > 0:

                state["sl"] = safe_float(
                    state.get("tp1")
                )

        elif tp1_hit:

            state["position_stage"] = "TP1_BE"

            if safe_float(state.get("entry")) > 0:

                state["sl"] = safe_float(
                    state.get("entry")
                )

        else:

            state["position_stage"] = "INITIAL"

    else:

        state["position_stage"] = "NONE"


    state["version"] = STATE_VERSION

    return state


# ============================================================
# LOG
# ============================================================

def load_logs():

    if not os.path.exists(LOG_FILE):

        return []

    try:

        with open(
            LOG_FILE,
            "r",
            encoding="utf-8",
        ) as f:

            data = json.load(f)

        if isinstance(data, list):

            return data

    except Exception as e:

        print("[LOG LOAD ERROR]", e)

    return []


def append_log(
    event,
    state,
    price=None,
    extra=None,
):

    try:

        logs = load_logs()

        item = {
            "time": now_iso(),
            "version": VERSION,
            "event": event,

            "status": state.get("status"),
            "direction": state.get("direction"),

            "entry": safe_float(
                state.get("entry")
            ),

            "sl": safe_float(
                state.get("sl")
            ),

            "tp1": safe_float(
                state.get("tp1")
            ),

            "tp2": safe_float(
                state.get("tp2")
            ),

            "tp3": safe_float(
                state.get("tp3")
            ),

            "risk": safe_float(
                state.get("risk")
            ),

            "position_stage":
                state.get("position_stage"),

            "tp1_hit":
                bool(state.get("tp1_hit")),

            "tp2_hit":
                bool(state.get("tp2_hit")),

            "tp3_hit":
                bool(state.get("tp3_hit")),

            "price":
                safe_float(price)
                if price is not None
                else None,

            "m15_score":
                int(state.get("m15_score") or 0),

            "five_score":
                int(state.get("five_score") or 0),

            "rsi":
                safe_float(state.get("rsi")),

            "adx":
                safe_float(state.get("adx")),

            "signal_id":
                state.get("signal_id"),
        }

        if isinstance(extra, dict):

            item.update(extra)

        logs.append(item)

        # 로그가 무한히 커지지 않도록
        logs = logs[-5000:]

        atomic_write_json(
            LOG_FILE,
            logs,
        )

    except Exception as e:

        print("[LOG ERROR]", e)


# ============================================================
# DATA CLEAN
# ============================================================

def clean_dataframe(df):

    if df is None:
        return None

    if df.empty:
        return None

    df = df.copy()

    # yfinance MultiIndex 대응
    if isinstance(df.columns, pd.MultiIndex):

        try:

            df.columns = df.columns.get_level_values(0)

        except Exception:

            pass

    required = [
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
    ]

    missing = [
        c for c in required
        if c not in df.columns
    ]

    if missing:

        print(
            "[DATA ERROR] Missing:",
            missing,
        )

        return None

    df = df[
        required
    ].copy()

    df = df.apply(
        pd.to_numeric,
        errors="coerce",
    )

    df = df.dropna(
        subset=[
            "Open",
            "High",
            "Low",
            "Close",
        ]
    )

    df = df[
        ~df.index.duplicated(
            keep="last"
        )
    ]

    df = df.sort_index()

    return df


# ============================================================
# DOWNLOAD WITH RETRY
# ============================================================

def download_data(
    interval,
    period,
    retry_count=DATA_RETRY_COUNT,
):

    for attempt in range(
        1,
        retry_count + 1,
    ):

        try:

            print(
                f"[DATA] {interval} "
                f"attempt {attempt}/{retry_count}"
            )

            df = yf.download(
                TICKER,
                period=period,
                interval=interval,
                progress=False,
                auto_adjust=False,
                threads=False,
            )

            df = clean_dataframe(df)

            if (
                df is not None
                and len(df) >= 50
            ):

                print(
                    f"[DATA] {interval} "
                    f"{len(df)} rows"
                )

                return df

            print(
                f"[DATA] {interval} "
                "empty/insufficient"
            )

        except Exception as e:

            print(
                f"[DATA ERROR] {interval}:",
                e,
            )

        if attempt < retry_count:

            delay = (
                DATA_RETRY_DELAY_SECONDS
                * attempt
            )

            print(
                f"[DATA] retry after "
                f"{delay}s"
            )

            time.sleep(delay)

    return None


# ============================================================
# COMPLETED CANDLE
# ============================================================

def completed(df):

    if df is None or df.empty:

        return df

    # 마지막 봉은 진행 중일 수 있으므로 제외
    if len(df) >= 5:

        return df.iloc[:-1].copy()

    return df.copy()


# ============================================================
# DATA FRESHNESS
# ============================================================

def latest_completed_timestamp(df):

    cdf = completed(df)

    if cdf is None or cdf.empty:

        return None

    return parse_timestamp(
        cdf.index[-1]
    )


def data_age_minutes(df):

    latest = latest_completed_timestamp(df)

    if latest is None:

        return float("inf")

    current = pd.Timestamp(
        now_kst()
    )

    return max(
        0.0,
        (
            current - latest
        ).total_seconds()
        / 60.0,
    )


def is_data_fresh(
    df,
    max_delay_minutes,
    label,
):

    age = data_age_minutes(df)

    print(
        f"[DATA FRESHNESS] "
        f"{label}: {age:.1f} min"
    )

    if age > max_delay_minutes:

        print(
            f"[DATA DELAY] "
            f"{label} is stale"
        )

        return False

    return True


# ============================================================
# INDICATORS
# ============================================================

def add_indicators(df):

    df = df.copy()

    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    # --------------------------------------------------------
    # EMA
    # --------------------------------------------------------

    df["EMA20"] = (
        close
        .ewm(
            span=20,
            adjust=False,
        )
        .mean()
    )

    df["EMA50"] = (
        close
        .ewm(
            span=50,
            adjust=False,
        )
        .mean()
    )

    # --------------------------------------------------------
    # ZLEMA
    #
    # V16 방식 유지
    # --------------------------------------------------------

    ema1 = (
        close
        .ewm(
            span=20,
            adjust=False,
        )
        .mean()
    )

    ema2 = (
        ema1
        .ewm(
            span=20,
            adjust=False,
        )
        .mean()
    )

    df["ZLEMA20"] = (
        2 * ema1 - ema2
    )

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

    avg_gain = (
        gain
        .ewm(
            alpha=1 / 14,
            adjust=False,
        )
        .mean()
    )

    avg_loss = (
        loss
        .ewm(
            alpha=1 / 14,
            adjust=False,
        )
        .mean()
    )

    rs = avg_gain / avg_loss.replace(
        0,
        np.nan,
    )

    df["RSI"] = (
        100
        - (
            100
            / (1 + rs)
        )
    )

    # --------------------------------------------------------
    # ATR
    # --------------------------------------------------------

    prev_close = close.shift(1)

    tr1 = high - low

    tr2 = (
        high - prev_close
    ).abs()

    tr3 = (
        low - prev_close
    ).abs()

    tr = pd.concat(
        [
            tr1,
            tr2,
            tr3,
        ],
        axis=1,
    ).max(axis=1)

    df["ATR"] = (
        tr
        .ewm(
            span=14,
            adjust=False,
        )
        .mean()
    )

    # --------------------------------------------------------
    # ADX
    # --------------------------------------------------------

    up_move = (
        high.diff()
    )

    down_move = (
        -low.diff()
    )

    plus_dm = pd.Series(
        np.where(
            (up_move > down_move)
            & (up_move > 0),
            up_move,
            0,
        ),
        index=df.index,
    )

    minus_dm = pd.Series(
        np.where(
            (down_move > up_move)
            & (down_move > 0),
            down_move,
            0,
        ),
        index=df.index,
    )

    atr14 = (
        tr
        .ewm(
            alpha=1 / 14,
            adjust=False,
        )
        .mean()
    )

    plus_di = (
        100
        * plus_dm.ewm(
            alpha=1 / 14,
            adjust=False,
        ).mean()
        / atr14.replace(
            0,
            np.nan,
        )
    )

    minus_di = (
        100
        * minus_dm.ewm(
            alpha=1 / 14,
            adjust=False,
        ).mean()
        / atr14.replace(
            0,
            np.nan,
        )
    )

    dx = (
        100
        * (
            (plus_di - minus_di).abs()
            /
            (plus_di + minus_di)
                .replace(
                    0,
                    np.nan,
                )
        )
    )

    df["ADX"] = (
        dx
        .ewm(
            alpha=1 / 14,
            adjust=False,
        )
        .mean()
    )

    df["PLUS_DI"] = plus_di
    df["MINUS_DI"] = minus_di

    # --------------------------------------------------------
    # SQUEEZE
    # BB vs Keltner
    # --------------------------------------------------------

    bb_mid = (
        close
        .rolling(20)
        .mean()
    )

    bb_std = (
        close
        .rolling(20)
        .std()
    )

    df["BB_UPPER"] = (
        bb_mid
        + 2.0 * bb_std
    )

    df["BB_LOWER"] = (
        bb_mid
        - 2.0 * bb_std
    )

    atr20 = (
        tr
        .ewm(
            span=20,
            adjust=False,
        )
        .mean()
    )

    kc_mid = (
        close
        .ewm(
            span=20,
            adjust=False,
        )
        .mean()
    )

    df["KC_UPPER"] = (
        kc_mid
        + 1.5 * atr20
    )

    df["KC_LOWER"] = (
        kc_mid
        - 1.5 * atr20
    )

    df["IS_SQUEEZE"] = (
        (df["BB_UPPER"] < df["KC_UPPER"])
        &
        (df["BB_LOWER"] > df["KC_LOWER"])
    )

    # --------------------------------------------------------
    # EMA SLOPE
    # --------------------------------------------------------

    df["EMA20_SLOPE"] = (
        df["EMA20"]
        - df["EMA20"].shift(3)
    )

    df["ZLEMA_SLOPE"] = (
        df["ZLEMA20"]
        - df["ZLEMA20"].shift(3)
    )

    # --------------------------------------------------------
    # CANDLE QUALITY
    # --------------------------------------------------------

    candle_range = (
        high - low
    ).replace(
        0,
        np.nan,
    )

    body = (
        close - df["Open"]
    ).abs()

    df["BODY_RATIO"] = (
        body
        / candle_range
    )

    df["CLOSE_POS"] = (
        (close - low)
        / candle_range
    )

    return df


# ============================================================
# M15 ANALYSIS
# ============================================================

def analyze_m15(df):

    df = completed(df)

    if df is None or len(df) < 60:

        return None

    df = add_indicators(df)

    row = df.iloc[-1]
    prev = df.iloc[-2]
    prev2 = df.iloc[-3]

    close = safe_float(row["Close"])
    ema20 = safe_float(row["EMA20"])
    ema50 = safe_float(row["EMA50"])
    zlema = safe_float(row["ZLEMA20"])

    rsi = safe_float(row["RSI"])
    atr = safe_float(row["ATR"])
    adx = safe_float(row["ADX"])

    body_ratio = safe_float(
        row["BODY_RATIO"]
    )

    close_pos = safe_float(
        row["CLOSE_POS"]
    )

    separation_atr = (
        abs(ema20 - ema50)
        / atr
        if atr > 0
        else 0
    )

    long_score = 0
    short_score = 0

    # --------------------------------------------------------
    # 1. Price vs EMA20
    # --------------------------------------------------------

    if close > ema20:
        long_score += 1

    if close < ema20:
        short_score += 1

    # --------------------------------------------------------
    # 2. Price vs ZLEMA
    # --------------------------------------------------------

    if close > zlema:
        long_score += 1

    if close < zlema:
        short_score += 1

    # --------------------------------------------------------
    # 3. EMA structure
    # --------------------------------------------------------

    if ema20 > ema50:
        long_score += 1

    if ema20 < ema50:
        short_score += 1

    # --------------------------------------------------------
    # 4. ZLEMA slope
    # --------------------------------------------------------

    zlema_slope = safe_float(
        row["ZLEMA_SLOPE"]
    )

    if zlema_slope > 0:
        long_score += 1

    if zlema_slope < 0:
        short_score += 1

    # --------------------------------------------------------
    # 5. RSI
    # --------------------------------------------------------

    if (
        LONG_RSI_MIN
        <= rsi
        <= LONG_RSI_MAX
    ):

        long_score += 1

    if (
        SHORT_RSI_MIN
        <= rsi
        <= SHORT_RSI_MAX
    ):

        short_score += 1

    # --------------------------------------------------------
    # 6. Candle quality
    # --------------------------------------------------------

    previous_close = safe_float(
        prev["Close"]
    )

    if (
        close > previous_close
        and close_pos >= 0.65
        and body_ratio >= MIN_BODY_RATIO
    ):

        long_score += 1

    if (
        close < previous_close
        and close_pos <= 0.35
        and body_ratio >= MIN_BODY_RATIO
    ):

        short_score += 1

    # --------------------------------------------------------
    # 7. ADX + directional DI
    # --------------------------------------------------------

    plus_di = safe_float(
        row["PLUS_DI"]
    )

    minus_di = safe_float(
        row["MINUS_DI"]
    )

    if adx >= MIN_ADX:

        if plus_di > minus_di:

            long_score += 1

        if minus_di > plus_di:

            short_score += 1

    # --------------------------------------------------------
    # 8. EMA separation
    # --------------------------------------------------------

    if (
        separation_atr
        >= MIN_EMA_SEPARATION_ATR
    ):

        if ema20 > ema50:

            long_score += 1

        elif ema20 < ema50:

            short_score += 1

    # --------------------------------------------------------
    # Two candle confirmation
    # --------------------------------------------------------

    prev_ema20 = safe_float(
        prev["EMA20"]
    )

    prev2_ema20 = safe_float(
        prev2["EMA20"]
    )

    prev_close = safe_float(
        prev["Close"]
    )

    prev2_close = safe_float(
        prev2["Close"]
    )

    long_confirmation = (
        prev_close > prev_ema20
        and prev2_close > prev2_ema20
    )

    short_confirmation = (
        prev_close < prev_ema20
        and prev2_close < prev2_ema20
    )

    return {
        "close": close,
        "ema20": ema20,
        "ema50": ema50,
        "zlema20": zlema,

        "rsi": rsi,
        "atr": atr,
        "adx": adx,

        "body_ratio": body_ratio,
        "close_pos": close_pos,

        "separation_atr":
            separation_atr,

        "long_score":
            int(long_score),

        "short_score":
            int(short_score),

        "long_confirmation":
            bool(long_confirmation),

        "short_confirmation":
            bool(short_confirmation),

        "is_squeeze":
            bool(row["IS_SQUEEZE"]),

        "candle_time":
            timestamp_iso(row.name),
    }


# ============================================================
# 5M ANALYSIS
# ============================================================

def analyze_5m(df):

    df = completed(df)

    if df is None or len(df) < 60:

        return None

    df = add_indicators(df)

    row = df.iloc[-1]
    prev = df.iloc[-2]

    close = safe_float(row["Close"])
    ema20 = safe_float(row["EMA20"])

    rsi = safe_float(row["RSI"])
    atr = safe_float(row["ATR"])
    adx = safe_float(row["ADX"])

    body_ratio = safe_float(
        row["BODY_RATIO"]
    )

    close_pos = safe_float(
        row["CLOSE_POS"]
    )

    previous_close = safe_float(
        prev["Close"]
    )

    long_score = 0
    short_score = 0

    # 1. Price / EMA
    if close > ema20:
        long_score += 1

    if close < ema20:
        short_score += 1

    # 2. Candle momentum
    if (
        close > previous_close
        and close_pos >= 0.60
        and body_ratio >= 0.30
    ):

        long_score += 1

    if (
        close < previous_close
        and close_pos <= 0.40
        and body_ratio >= 0.30
    ):

        short_score += 1

    # 3. RSI
    if (
        50 <= rsi <= 70
    ):

        long_score += 1

    if (
        30 <= rsi <= 50
    ):

        short_score += 1

    # 4. ADX
    if adx >= 14:

        if close > ema20:

            long_score += 1

        if close < ema20:

            short_score += 1

    return {
        "close": close,
        "ema20": ema20,

        "rsi": rsi,
        "atr": atr,
        "adx": adx,

        "body_ratio":
            body_ratio,

        "close_pos":
            close_pos,

        "long_score":
            int(long_score),

        "short_score":
            int(short_score),

        "candle_time":
            timestamp_iso(row.name),
    }


# ============================================================
# H1 ANALYSIS
# ============================================================

def analyze_h1(df):

    df = completed(df)

    if df is None or len(df) < 80:

        return None

    df = add_indicators(df)

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

    if (
        close > ema20
        and ema20 > ema50
    ):

        trend = "BULL"

    elif (
        close < ema20
        and ema20 < ema50
    ):

        trend = "BEAR"

    else:

        trend = "NEUTRAL"

    return {
        "close": close,
        "ema20": ema20,
        "ema50": ema50,

        "trend": trend,

        "candle_time":
            timestamp_iso(row.name),
    }


# ============================================================
# CURRENT COMPLETED 1M PRICE
# ============================================================

def get_current_1m(df):

    cdf = completed(df)

    if cdf is None or cdf.empty:

        return None, None

    row = cdf.iloc[-1]

    price = safe_float(
        row["Close"]
    )

    timestamp = timestamp_iso(
        row.name
    )

    return price, timestamp


# ============================================================
# SETUP ID
# ============================================================

def make_setup_id(
    direction,
    m15,
    five,
    h1,
):

    return (
        f"{direction}|"
        f"M15:{m15.get('candle_time')}|"
        f"M15S:{m15.get('long_score') if direction == 'LONG' else m15.get('short_score')}|"
        f"5M:{five.get('candle_time')}|"
        f"5MS:{five.get('long_score') if direction == 'LONG' else five.get('short_score')}|"
        f"H1:{h1.get('trend')}"
    )


# ============================================================
# COOLDOWN
# ============================================================

def cooldown_remaining(
    state,
    direction=None,
):

    current = now_kst()

    # --------------------------------------------------------
    # General signal cooldown
    # --------------------------------------------------------

    last_signal = parse_timestamp(
        state.get("last_signal_time")
    )

    if last_signal:

        elapsed = (
            current - last_signal.to_pydatetime()
        ).total_seconds() / 60

        if elapsed < SIGNAL_COOLDOWN_MINUTES:

            return (
                SIGNAL_COOLDOWN_MINUTES
                - elapsed
            )


    # --------------------------------------------------------
    # Exit cooldown
    # --------------------------------------------------------

    last_exit = parse_timestamp(
        state.get("last_exit_time")
    )

    if last_exit:

        elapsed = (
            current - last_exit.to_pydatetime()
        ).total_seconds() / 60

        if elapsed < EXIT_COOLDOWN_MINUTES:

            return (
                EXIT_COOLDOWN_MINUTES
                - elapsed
            )


    # --------------------------------------------------------
    # SL cooldown
    # --------------------------------------------------------

    if direction:

        last_sl_direction = (
            state.get(
                "last_sl_direction"
            )
        )

        last_sl = parse_timestamp(
            state.get("last_sl_time")
        )

        if (
            last_sl
            and last_sl_direction == direction
        ):

            elapsed = (
                current
                - last_sl.to_pydatetime()
            ).total_seconds() / 60

            if elapsed < SL_COOLDOWN_MINUTES:

                return (
                    SL_COOLDOWN_MINUTES
                    - elapsed
                )

    return 0.0


def signal_allowed(
    state,
    direction,
    setup_id,
):

    if state.get("status") == "ACTIVE":

        return False

    remaining = cooldown_remaining(
        state,
        direction,
    )

    if remaining > 0:

        print(
            f"[COOLDOWN] "
            f"{remaining:.1f} min"
        )

        return False

    # 동일 setup 재진입 방지
    if (
        setup_id
        and
        setup_id
        == state.get(
            "last_exit_setup_id"
        )
    ):

        print(
            "[BLOCK] Same setup ID"
        )

        return False

    return True


# ============================================================
# SIGNAL ENGINE
# ============================================================

def find_signal(
    m15,
    five,
    h1,
    current_price,
):

    if not m15 or not five or not h1:

        return None

    print()
    print(
        "===================================="
    )
    print(
        " SIGNAL ENGINE"
    )
    print(
        "===================================="
    )

    print(
        "M15 LONG SCORE :",
        m15["long_score"],
    )

    print(
        "M15 SHORT SCORE:",
        m15["short_score"],
    )

    print(
        "5M LONG SCORE  :",
        five["long_score"],
    )

    print(
        "5M SHORT SCORE :",
        five["short_score"],
    )

    print(
        "M15 ADX        :",
        round(m15["adx"], 2),
    )

    print(
        "H1 TREND       :",
        h1["trend"],
    )


    # --------------------------------------------------------
    # ATR FILTER
    # --------------------------------------------------------

    if (
        m15["atr"] < MIN_ATR
        or
        m15["atr"] > MAX_ATR
    ):

        print(
            "[BLOCK] M15 ATR outside range"
        )

        return None


    # --------------------------------------------------------
    # HARD ADX FILTER
    # --------------------------------------------------------

    if m15["adx"] < MIN_ADX:

        print(
            "[BLOCK] ADX below minimum"
        )

        return None


    # --------------------------------------------------------
    # SQUEEZE FILTER
    # --------------------------------------------------------

    if m15["is_squeeze"]:

        print(
            "[BLOCK] Volatility squeeze"
        )

        return None


    # --------------------------------------------------------
    # M15 SCORE
    # --------------------------------------------------------

    long_ok = (
        m15["long_score"]
        >= MIN_M15_SCORE
    )

    short_ok = (
        m15["short_score"]
        >= MIN_M15_SCORE
    )


    # --------------------------------------------------------
    # 5M SCORE
    # --------------------------------------------------------

    long_ok = (
        long_ok
        and
        five["long_score"]
        >= MIN_5M_SCORE
    )

    short_ok = (
        short_ok
        and
        five["short_score"]
        >= MIN_5M_SCORE
    )


    # --------------------------------------------------------
    # M15 confirmation
    # --------------------------------------------------------

    if REQUIRE_M15_CONFIRMATION:

        long_ok = (
            long_ok
            and
            m15["long_confirmation"]
        )

        short_ok = (
            short_ok
            and
            m15["short_confirmation"]
        )


    # --------------------------------------------------------
    # H1 TREND
    # --------------------------------------------------------

    if h1["trend"] == "BEAR":

        long_ok = False

    elif h1["trend"] == "BULL":

        short_ok = False

    elif h1["trend"] == "NEUTRAL":

        # 중립이면 M15 8점만 허용
        if (
            m15["long_score"]
            < STRONG_M15_SCORE
        ):

            long_ok = False

        if (
            m15["short_score"]
            < STRONG_M15_SCORE
        ):

            short_ok = False


    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    if not (
        LONG_RSI_MIN
        <= m15["rsi"]
        <= LONG_RSI_MAX
    ):

        long_ok = False


    if not (
        SHORT_RSI_MIN
        <= m15["rsi"]
        <= SHORT_RSI_MAX
    ):

        short_ok = False


    # --------------------------------------------------------
    # Candle quality
    # --------------------------------------------------------

    if (
        m15["body_ratio"]
        < MIN_BODY_RATIO
    ):

        long_ok = False
        short_ok = False


    # --------------------------------------------------------
    # EMA distance
    # --------------------------------------------------------

    distance_atr = (
        abs(
            current_price
            - m15["ema20"]
        )
        / m15["atr"]
        if m15["atr"] > 0
        else 999
    )

    print(
        "EMA DISTANCE ATR:",
        round(distance_atr, 3),
    )

    if (
        distance_atr
        > MAX_ENTRY_DISTANCE_ATR
    ):

        long_ok = False
        short_ok = False


    # --------------------------------------------------------
    # Conflict handling
    # --------------------------------------------------------

    if long_ok and short_ok:

        if (
            m15["long_score"]
            > m15["short_score"]
        ):

            short_ok = False

        elif (
            m15["short_score"]
            > m15["long_score"]
        ):

            long_ok = False

        else:

            print(
                "[BLOCK] Exact score tie"
            )

            return None


    # --------------------------------------------------------
    # RESULT
    # --------------------------------------------------------

    if long_ok:

        reasons = [
            "가격 > EMA20",
            "가격 > ZLEMA20",
            "EMA20 > EMA50",
            "ZLEMA 상승",
            f"RSI {m15['rsi']:.1f}",
            f"ADX {m15['adx']:.1f}",
            f"M15 {m15['long_score']}/8",
            f"5M {five['long_score']}/4",
        ]

        return {
            "direction": "LONG",
            "reasons": reasons,
            "setup_id": make_setup_id(
                "LONG",
                m15,
                five,
                h1,
            ),
        }


    if short_ok:

        reasons = [
            "가격 < EMA20",
            "가격 < ZLEMA20",
            "EMA20 < EMA50",
            "ZLEMA 하락",
            f"RSI {m15['rsi']:.1f}",
            f"ADX {m15['adx']:.1f}",
            f"M15 {m15['short_score']}/8",
            f"5M {five['short_score']}/4",
        ]

        return {
            "direction": "SHORT",
            "reasons": reasons,
            "setup_id": make_setup_id(
                "SHORT",
                m15,
                five,
                h1,
            ),
        }


    return None


# ============================================================
# POSITION CALCULATION
# ============================================================

def calculate_position(
    direction,
    entry,
    m15,
    five,
):

    atr5 = safe_float(
        five["atr"]
    )

    atr15 = safe_float(
        m15["atr"]
    )

    base_atr = max(
        atr5,
        atr15 * 0.65,
    )

    raw_risk = (
        base_atr
        * ENTRY_RISK_ATR
    )

    min_risk = (
        atr5
        * MIN_RISK_ATR
    )

    max_risk = (
        atr5
        * MAX_RISK_ATR
    )

    risk = max(
        min_risk,
        min(
            raw_risk,
            max_risk,
        ),
    )

    risk = max(
        risk,
        0.01,
    )


    # --------------------------------------------------------
    # Dynamic TP
    # --------------------------------------------------------

    score = (
        m15["long_score"]
        if direction == "LONG"
        else m15["short_score"]
    )

    if (
        score >= STRONG_M15_SCORE
        and
        m15["adx"] >= 25
    ):

        tp1_r = STRONG_TP1_R
        tp2_r = STRONG_TP2_R
        tp3_r = STRONG_TP3_R

    else:

        tp1_r = BASE_TP1_R
        tp2_r = BASE_TP2_R
        tp3_r = BASE_TP3_R


    if direction == "LONG":

        sl = entry - risk

        tp1 = (
            entry
            + risk * tp1_r
        )

        tp2 = (
            entry
            + risk * tp2_r
        )

        tp3 = (
            entry
            + risk * tp3_r
        )

    else:

        sl = entry + risk

        tp1 = (
            entry
            - risk * tp1_r
        )

        tp2 = (
            entry
            - risk * tp2_r
        )

        tp3 = (
            entry
            - risk * tp3_r
        )


    return {
        "entry": float(entry),
        "sl": float(sl),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "tp3": float(tp3),
        "risk": float(risk),

        "tp1_r": float(tp1_r),
        "tp2_r": float(tp2_r),
        "tp3_r": float(tp3_r),
    }


# ============================================================
# ENTRY MESSAGE
# ============================================================

def build_entry_message(
    state,
    signal,
    m15,
    five,
    h1,
):

    direction = state["direction"]

    title = (
        "🟢 GOLD LONG SIGNAL"
        if direction == "LONG"
        else
        "🔴 GOLD SHORT SIGNAL"
    )

    direction_text = (
        "LONG ▲"
        if direction == "LONG"
        else
        "SHORT ▼"
    )

    safe_reasons = [
        html_escape(x)
        for x in signal["reasons"]
    ]

    reason_text = "\n".join(
        f"• {x}"
        for x in safe_reasons
    )

    score = (
        m15["long_score"]
        if direction == "LONG"
        else m15["short_score"]
    )

    tp_mode = (
        "STRONG TREND"
        if (
            score >= 8
            and m15["adx"] >= 25
        )
        else
        "BASE"
    )

    return f"""
<b>{title}</b>

━━━━━━━━━━━━━━━━━━
<b>GOLD FUTURES V17.1</b>
━━━━━━━━━━━━━━━━━━

📌 방향: <b>{direction_text}</b>

💰 Entry
<b>{fmt_price(state["entry"])}</b>

🛑 SL
<b>{fmt_price(state["sl"])}</b>

🎯 TP1
<b>{fmt_price(state["tp1"])}</b>

🎯 TP2
<b>{fmt_price(state["tp2"])}</b>

🎯 TP3
<b>{fmt_price(state["tp3"])}</b>

━━━━━━━━━━━━━━━━━━

📊 M15 Score
<b>{score}/8</b>

📊 5M Score
<b>{state["five_score"]}/4</b>

📈 RSI
<b>{m15["rsi"]:.2f}</b>

📈 ADX
<b>{m15["adx"]:.2f}</b>

🕐 H1 Trend
<b>{html_escape(h1["trend"])}</b>

📦 TP Mode
<b>{tp_mode}</b>

━━━━━━━━━━━━━━━━━━

<b>상승/하락 근거</b>

{reason_text}

━━━━━━━━━━━━━━━━━━

<b>포지션 관리</b>

TP1 도달
→ SL = Entry

TP2 도달
→ SL = TP1

TP3 도달
→ FINAL EXIT

⚠️ 동일 1분봉에서
TP와 SL이 동시에 터치되면
보수적으로 SL 우선 처리

━━━━━━━━━━━━━━━━━━

<a href="{TRADINGVIEW_URL}">TradingView GC1!</a>
"""


# ============================================================
# ENTER POSITION
# ============================================================

def enter_position(
    state,
    signal,
    m15,
    five,
    h1,
    entry,
    entry_candle_time,
):

    direction = signal["direction"]

    setup_id = signal["setup_id"]

    position = calculate_position(
        direction,
        entry,
        m15,
        five,
    )

    state["status"] = "ACTIVE"

    state["direction"] = direction

    state["entry"] = position["entry"]
    state["sl"] = position["sl"]

    state["tp1"] = position["tp1"]
    state["tp2"] = position["tp2"]
    state["tp3"] = position["tp3"]

    state["risk"] = position["risk"]

    state["tp1_hit"] = False
    state["tp2_hit"] = False
    state["tp3_hit"] = False

    state["position_stage"] = "INITIAL"

    state["signal_time"] = now_iso()
    state["last_signal_time"] = now_iso()

    state["signal_id"] = setup_id

    state["m15_score"] = (
        m15["long_score"]
        if direction == "LONG"
        else m15["short_score"]
    )

    state["five_score"] = (
        five["long_score"]
        if direction == "LONG"
        else five["short_score"]
    )

    state["rsi"] = safe_float(
        m15["rsi"]
    )

    state["adx"] = safe_float(
        m15["adx"]
    )

    # Entry 이후 이전 1분봉을
    # 다시 검사하지 않는다.
    state["last_candle_time"] = (
        entry_candle_time
    )

    state["last_monitor_time"] = now_iso()

    state["last_price"] = entry

    save_state(state)

    append_log(
        "ENTRY",
        state,
        entry,
        {
            "setup_id": setup_id,
            "m15_candle_time":
                m15["candle_time"],
            "five_candle_time":
                five["candle_time"],
            "h1_candle_time":
                h1["candle_time"],
            "tp1_r":
                position["tp1_r"],
            "tp2_r":
                position["tp2_r"],
            "tp3_r":
                position["tp3_r"],
        },
    )

    message = build_entry_message(
        state,
        signal,
        m15,
        five,
        h1,
    )

    send_telegram(message)

    print()
    print(
        "===================================="
    )
    print(
        " POSITION ENTERED"
    )
    print(
        "===================================="
    )

    print(
        "Direction:",
        direction,
    )

    print(
        "Entry:",
        fmt_price(
            state["entry"]
        ),
    )

    print(
        "SL:",
        fmt_price(
            state["sl"]
        ),
    )

    print(
        "TP1:",
        fmt_price(
            state["tp1"]
        ),
    )

    print(
        "TP2:",
        fmt_price(
            state["tp2"]
        ),
    )

    print(
        "TP3:",
        fmt_price(
            state["tp3"]
        ),
    )


# ============================================================
# EXIT R
# ============================================================

def calculate_exit_r(
    state,
    exit_price,
):

    entry = safe_float(
        state.get("entry")
    )

    risk = safe_float(
        state.get("risk")
    )

    if risk <= 0:

        return 0.0

    if state.get("direction") == "LONG":

        return (
            exit_price - entry
        ) / risk

    return (
        entry - exit_price
    ) / risk


# ============================================================
# TP MESSAGE
# ============================================================

def build_tp_message(
    state,
    level,
):

    direction = state["direction"]

    if level == "TP1":

        return f"""
🟡 <b>GOLD {direction} — TP1 HIT</b>

━━━━━━━━━━━━━━━━━━

🎯 TP1
<b>{fmt_price(state["tp1"])}</b>

📌 Entry
<b>{fmt_price(state["entry"])}</b>

🛡 현재 SL
<b>{fmt_price(state["sl"])}</b>

📊 Stage
<b>TP1 → BREAK EVEN</b>

━━━━━━━━━━━━━━━━━━

이제 초기 손실 구간은 제거되었습니다.
"""

    return f"""
🟢 <b>GOLD {direction} — TP2 HIT</b>

━━━━━━━━━━━━━━━━━━

🎯 TP2
<b>{fmt_price(state["tp2"])}</b>

📌 Entry
<b>{fmt_price(state["entry"])}</b>

🛡 현재 SL
<b>{fmt_price(state["sl"])}</b>

📊 Stage
<b>TP2 → TP1 TRAIL</b>

━━━━━━━━━━━━━━━━━━

수익 보호 구간으로 이동했습니다.
"""


# ============================================================
# TP1
# ============================================================

def hit_tp1(
    state,
    trigger_price,
):

    if state.get("tp1_hit"):

        return

    state["tp1_hit"] = True

    state["position_stage"] = (
        "TP1_BE"
    )

    # TP1 → Entry
    state["sl"] = safe_float(
        state["entry"]
    )

    save_state(state)

    append_log(
        "TP1_HIT",
        state,
        trigger_price,
    )

    send_telegram(
        build_tp_message(
            state,
            "TP1",
        )
    )

    print(
        "[TP1 HIT]",
        fmt_price(trigger_price),
        "SL -> ENTRY",
    )


# ============================================================
# TP2
# ============================================================

def hit_tp2(
    state,
    trigger_price,
):

    if state.get("tp2_hit"):

        return

    # TP2는 TP1보다 먼저 처리되어야 한다.
    if not state.get("tp1_hit"):

        hit_tp1(
            state,
            state["tp1"],
        )

    state["tp2_hit"] = True

    state["position_stage"] = (
        "TP2_TRAIL"
    )

    # TP2 → TP1
    state["sl"] = safe_float(
        state["tp1"]
    )

    save_state(state)

    append_log(
        "TP2_HIT",
        state,
        trigger_price,
    )

    send_telegram(
        build_tp_message(
            state,
            "TP2",
        )
    )

    print(
        "[TP2 HIT]",
        fmt_price(trigger_price),
        "SL -> TP1",
    )


# ============================================================
# EXIT MESSAGE
# ============================================================

def build_exit_message(
    state,
    reason,
    exit_price,
):

    direction = state["direction"]

    exit_r = calculate_exit_r(
        state,
        exit_price,
    )

    if reason == "SL":

        title = (
            "🔴 GOLD "
            f"{direction} — STOP LOSS"
        )

        stage_text = (
            "초기 손절"
        )

    elif reason == "BE":

        title = (
            "🟡 GOLD "
            f"{direction} — BREAK EVEN"
        )

        stage_text = (
            "TP1 이후 본절 보호"
        )

    else:

        title = (
            "🔵 GOLD "
            f"{direction} — TRAIL EXIT"
        )

        stage_text = (
            "TP2 이후 수익 보호 종료"
        )

    return f"""
<b>{title}</b>

━━━━━━━━━━━━━━━━━━

📌 Entry
<b>{fmt_price(state["entry"])}</b>

🚪 Exit
<b>{fmt_price(exit_price)}</b>

📊 결과
<b>{fmt_r(exit_r)}</b>

🛡 Stage
<b>{html_escape(stage_text)}</b>

━━━━━━━━━━━━━━━━━━

🎯 TP1
{fmt_price(state["tp1"])}

🎯 TP2
{fmt_price(state["tp2"])}

🎯 TP3
{fmt_price(state["tp3"])}

━━━━━━━━━━━━━━━━━━
"""


# ============================================================
# RESET AFTER EXIT
# ============================================================

def reset_after_exit(
    state,
    reason,
    exit_price,
):

    # 반드시 초기화 전에 snapshot
    snapshot = dict(state)

    exit_r = calculate_exit_r(
        snapshot,
        exit_price,
    )

    current_time = now_iso()

    # --------------------------------------------------------
    # EXIT 기록
    # --------------------------------------------------------

    state["last_exit_time"] = (
        current_time
    )

    state["last_exit_reason"] = (
        reason
    )

    state["last_exit_direction"] = (
        snapshot.get("direction")
    )

    state["last_exit_price"] = (
        float(exit_price)
    )

    state["last_exit_r"] = (
        float(exit_r)
    )

    state["last_exit_setup_id"] = (
        snapshot.get("signal_id")
    )

    # 초기 SL만 SL cooldown
    if reason == "SL":

        state["last_sl_time"] = (
            current_time
        )

        state["last_sl_direction"] = (
            snapshot.get("direction")
        )


    # --------------------------------------------------------
    # 먼저 EXIT log
    # --------------------------------------------------------

    append_log(
        "EXIT",
        snapshot,
        exit_price,
        {
            "reason": reason,
            "exit_r": exit_r,
            "setup_id":
                snapshot.get("signal_id"),
        },
    )


    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    send_telegram(
        build_exit_message(
            snapshot,
            reason,
            exit_price,
        )
    )


    # --------------------------------------------------------
    # ACTIVE STATE CLEAR
    # --------------------------------------------------------

    state["status"] = "IDLE"

    state["direction"] = None

    state["entry"] = 0.0
    state["sl"] = 0.0
    state["tp1"] = 0.0
    state["tp2"] = 0.0
    state["tp3"] = 0.0

    state["risk"] = 0.0

    state["tp1_hit"] = False
    state["tp2_hit"] = False
    state["tp3_hit"] = False

    state["position_stage"] = "NONE"

    state["signal_time"] = None
    state["signal_id"] = None

    state["m15_score"] = 0
    state["five_score"] = 0

    state["rsi"] = 0.0
    state["adx"] = 0.0

    state["last_candle_time"] = None

    state["last_monitor_time"] = (
        current_time
    )

    state["last_price"] = (
        float(exit_price)
    )

    save_state(state)

    print(
        "[POSITION CLOSED]",
        reason,
        fmt_price(exit_price),
        fmt_r(exit_r),
    )


# ============================================================
# MONITOR REASON
# ============================================================

def current_stop_reason(state):

    stage = (
        state.get(
            "position_stage"
        )
    )

    if stage == "INITIAL":

        return "SL"

    if stage == "TP1_BE":

        return "BE"

    if stage == "TP2_TRAIL":

        return "TRAIL"

    return "SL"


# ============================================================
# PROCESS SINGLE COMPLETED 1M CANDLE
# ============================================================

def process_monitor_candle(
    state,
    row,
):

    if state.get("status") != "ACTIVE":

        return False

    direction = state.get(
        "direction"
    )

    high = safe_float(
        row["High"]
    )

    low = safe_float(
        row["Low"]
    )

    close = safe_float(
        row["Close"]
    )

    state["last_price"] = close


    # ========================================================
    # LONG
    # ========================================================

    if direction == "LONG":

        current_sl = safe_float(
            state["sl"]
        )

        # ----------------------------------------------------
        # STOP FIRST
        #
        # 같은 1분봉에서
        # SL + TP가 동시에 발생하면
        # 보수적으로 SL 우선
        # ----------------------------------------------------

        if low <= current_sl:

            reason = current_stop_reason(
                state
            )

            reset_after_exit(
                state,
                reason,
                current_sl,
            )

            return True


        # ----------------------------------------------------
        # TP1
        # ----------------------------------------------------

        if (
            not state["tp1_hit"]
            and
            high >= state["tp1"]
        ):

            hit_tp1(
                state,
                state["tp1"],
            )


        # ----------------------------------------------------
        # TP2
        # ----------------------------------------------------

        if (
            state.get("status") == "ACTIVE"
            and
            not state["tp2_hit"]
            and
            high >= state["tp2"]
        ):

            hit_tp2(
                state,
                state["tp2"],
            )


        # ----------------------------------------------------
        # TP3
        # ----------------------------------------------------

        if (
            state.get("status") == "ACTIVE"
            and
            not state["tp3_hit"]
            and
            high >= state["tp3"]
        ):

            state["tp3_hit"] = True

            tp3_price = safe_float(
                state["tp3"]
            )

            append_log(
                "TP3_HIT",
                state,
                tp3_price,
            )

            send_telegram(
                f"""
🏆 <b>GOLD LONG — TP3 HIT</b>

━━━━━━━━━━━━━━━━━━

🎯 TP3
<b>{fmt_price(tp3_price)}</b>

📊 Result
<b>{fmt_r(calculate_exit_r(state, tp3_price))}</b>

🚪 FINAL EXIT

━━━━━━━━━━━━━━━━━━
"""
            )

            reset_after_exit(
                state,
                "TP3",
                tp3_price,
            )

            return True


        return False


    # ========================================================
    # SHORT
    # ========================================================

    if direction == "SHORT":

        current_sl = safe_float(
            state["sl"]
        )

        # ----------------------------------------------------
        # STOP FIRST
        # ----------------------------------------------------

        if high >= current_sl:

            reason = current_stop_reason(
                state
            )

            reset_after_exit(
                state,
                reason,
                current_sl,
            )

            return True


        # ----------------------------------------------------
        # TP1
        # ----------------------------------------------------

        if (
            not state["tp1_hit"]
            and
            low <= state["tp1"]
        ):

            hit_tp1(
                state,
                state["tp1"],
            )


        # ----------------------------------------------------
        # TP2
        # ----------------------------------------------------

        if (
            state.get("status") == "ACTIVE"
            and
            not state["tp2_hit"]
            and
            low <= state["tp2"]
        ):

            hit_tp2(
                state,
                state["tp2"],
            )


        # ----------------------------------------------------
        # TP3
        # ----------------------------------------------------

        if (
            state.get("status") == "ACTIVE"
            and
            not state["tp3_hit"]
            and
            low <= state["tp3"]
        ):

            state["tp3_hit"] = True

            tp3_price = safe_float(
                state["tp3"]
            )

            append_log(
                "TP3_HIT",
                state,
                tp3_price,
            )

            send_telegram(
                f"""
🏆 <b>GOLD SHORT — TP3 HIT</b>

━━━━━━━━━━━━━━━━━━

🎯 TP3
<b>{fmt_price(tp3_price)}</b>

📊 Result
<b>{fmt_r(calculate_exit_r(state, tp3_price))}</b>

🚪 FINAL EXIT

━━━━━━━━━━━━━━━━━━
"""
            )

            reset_after_exit(
                state,
                "TP3",
                tp3_price,
            )

            return True


        return False


    return False


# ============================================================
# GET NEW 1M CANDLES
# ============================================================

def get_new_candles(
    df,
    last_candle_time,
):

    cdf = completed(df)

    if cdf is None or cdf.empty:

        return None

    # 최초 실행 / 기존 상태에 시간 없음
    #
    # 과거 1분봉 전체를 재생하지 않고
    # 최신 완료 봉 하나만 검사한다.
    if not last_candle_time:

        return cdf.tail(1)


    last_ts = parse_timestamp(
        last_candle_time
    )

    if last_ts is None:

        return cdf.tail(1)


    selected = []

    for idx in cdf.index:

        current_ts = parse_timestamp(
            idx
        )

        if (
            current_ts
            and
            current_ts > last_ts
        ):

            selected.append(idx)

    if not selected:

        return None

    return cdf.loc[selected]


# ============================================================
# ACTIVE POSITION MONITOR
# ============================================================

def monitor_active(
    state,
    df_1m,
):

    if state.get("status") != "ACTIVE":

        return

    if df_1m is None or df_1m.empty:

        return

    candles = get_new_candles(
        df_1m,
        state.get(
            "last_candle_time"
        ),
    )

    # --------------------------------------------------------
    # 새 봉이 없더라도 현재 완료봉 가격은 저장
    # --------------------------------------------------------

    latest_price, latest_time = (
        get_current_1m(df_1m)
    )

    if latest_price is not None:

        state["last_price"] = (
            latest_price
        )

        state["last_monitor_time"] = (
            now_iso()
        )

        save_state(state)


    if candles is None or candles.empty:

        print(
            "[MONITOR] No new completed 1M candle"
        )

        return


    print()
    print(
        "===================================="
    )
    print(
        " ACTIVE POSITION MONITOR"
    )
    print(
        "===================================="
    )

    print(
        "Direction:",
        state.get("direction"),
    )

    print(
        "Entry:",
        fmt_price(
            state.get("entry")
        ),
    )

    print(
        "SL:",
        fmt_price(
            state.get("sl")
        ),
    )

    print(
        "Stage:",
        state.get(
            "position_stage"
        ),
    )


    # --------------------------------------------------------
    # 누락된 1분봉을 시간순으로 모두 처리
    # --------------------------------------------------------

    for idx, row in candles.iterrows():

        if state.get("status") != "ACTIVE":

            return

        candle_time = timestamp_iso(
            idx
        )

        candle_close = safe_float(
            row["Close"]
        )

        # 먼저 checkpoint 저장
        state["last_candle_time"] = (
            candle_time
        )

        state["last_monitor_time"] = (
            now_iso()
        )

        state["last_price"] = (
            candle_close
        )

        print()
        print(
            "[1M]",
            candle_time,
            "O:",
            fmt_price(row["Open"]),
            "H:",
            fmt_price(row["High"]),
            "L:",
            fmt_price(row["Low"]),
            "C:",
            fmt_price(row["Close"]),
        )

        closed = process_monitor_candle(
            state,
            row,
        )

        # process에서 EXIT가 발생하면
        # reset_after_exit가 상태를 정리했으므로
        # 추가 저장으로 덮어쓰지 않는다.
        if closed:

            return

        save_state(state)


# ============================================================
# STATUS PRINT
# ============================================================

def print_state(state):

    print()
    print(
        "===================================="
    )

    print(
        "STATE"
    )

    print(
        "Status    :",
        state.get("status"),
    )

    print(
        "Direction :",
        state.get("direction"),
    )

    if state.get("status") == "ACTIVE":

        print(
            "Entry     :",
            state.get("entry"),
        )

        print(
            "SL        :",
            state.get("sl"),
        )

        print(
            "TP1       :",
            state.get("tp1"),
        )

        print(
            "TP2       :",
            state.get("tp2"),
        )

        print(
            "TP3       :",
            state.get("tp3"),
        )

        print(
            "Stage     :",
            state.get(
                "position_stage"
            ),
        )

    print(
        "===================================="
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "===================================="
    )

    print(
        f" GOLD FUTURES SMART SIGNAL BOT "
        f"V{VERSION}"
    )

    print(
        " KST:",
        now_kst().isoformat(),
    )

    print(
        "===================================="
    )


    # ========================================================
    # LOAD STATE
    # ========================================================

    state = load_state()

    print_state(state)


    # ========================================================
    # DOWNLOAD
    # ========================================================

    print()
    print(
        "Downloading market data..."
    )


    df_1m = download_data(
        "1m",
        "7d",
    )

    df_5m = download_data(
        "5m",
        "30d",
    )

    df_15m = download_data(
        "15m",
        "60d",
    )

    df_1h = download_data(
        "1h",
        "2y",
    )


    # ========================================================
    # DATA FAILURE
    # ========================================================

    if (
        df_1m is None
        or df_5m is None
        or df_15m is None
        or df_1h is None
    ):

        print(
            "[FATAL] Market data unavailable"
        )

        append_log(
            "DATA_ERROR",
            state,
            None,
            {
                "1m":
                    df_1m is not None,
                "5m":
                    df_5m is not None,
                "15m":
                    df_15m is not None,
                "1h":
                    df_1h is not None,
            },
        )

        return


    # ========================================================
    # FRESHNESS
    # ========================================================

    fresh_1m = is_data_fresh(
        df_1m,
        MAX_1M_DATA_DELAY_MINUTES,
        "1M",
    )

    fresh_5m = is_data_fresh(
        df_5m,
        MAX_5M_DATA_DELAY_MINUTES,
        "5M",
    )

    fresh_15m = is_data_fresh(
        df_15m,
        MAX_15M_DATA_DELAY_MINUTES,
        "15M",
    )


    # ========================================================
    # ACTIVE POSITION
    #
    # ACTIVE면 SIGNAL 탐색하지 않는다.
    # ========================================================

    if state.get("status") == "ACTIVE":

        # 1M 데이터가 stale이면
        # 현재 포지션을 억지로 판단하지 않는다.
        if not fresh_1m:

            print(
                "[MONITOR BLOCK] "
                "1M data is stale"
            )

            append_log(
                "DATA_STALE_MONITOR",
                state,
                None,
                {
                    "1m_age_minutes":
                        data_age_minutes(
                            df_1m
                        )
                },
            )

            return

        monitor_active(
            state,
            df_1m,
        )

        print_state(state)

        return


    # ========================================================
    # SIGNAL MODE
    # ========================================================

    if not (
        fresh_1m
        and fresh_5m
        and fresh_15m
    ):

        print(
            "[SIGNAL BLOCK] "
            "One or more MTF datasets are stale"
        )

        append_log(
            "DATA_STALE_SIGNAL",
            state,
            None,
            {
                "1m_age_minutes":
                    data_age_minutes(
                        df_1m
                    ),
                "5m_age_minutes":
                    data_age_minutes(
                        df_5m
                    ),
                "15m_age_minutes":
                    data_age_minutes(
                        df_15m
                    ),
            },
        )

        return


    # ========================================================
    # ANALYZE
    # ========================================================

    m15 = analyze_m15(
        df_15m
    )

    five = analyze_5m(
        df_5m
    )

    h1 = analyze_h1(
        df_1h
    )

    entry_price, entry_candle_time = (
        get_current_1m(
            df_1m
        )
    )


    if (
        m15 is None
        or five is None
        or h1 is None
        or entry_price is None
        or entry_candle_time is None
    ):

        print(
            "[BLOCK] Indicator analysis failed"
        )

        return


    # ========================================================
    # PRINT MARKET CHECK
    # ========================================================

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
        "Price :",
        fmt_price(entry_price),
    )

    print(
        "M15 EMA20:",
        fmt_price(m15["ema20"]),
    )

    print(
        "M15 EMA50:",
        fmt_price(m15["ema50"]),
    )

    print(
        "M15 RSI:",
        f"{m15['rsi']:.2f}",
    )

    print(
        "M15 ADX:",
        f"{m15['adx']:.2f}",
    )

    print(
        "M15 ATR:",
        fmt_price(m15["atr"]),
    )

    print(
        "M15 LONG:",
        m15["long_score"],
    )

    print(
        "M15 SHORT:",
        m15["short_score"],
    )

    print(
        "5M LONG:",
        five["long_score"],
    )

    print(
        "5M SHORT:",
        five["short_score"],
    )

    print(
        "H1 TREND:",
        h1["trend"],
    )

    print(
        "SQUEEZE:",
        m15["is_squeeze"],
    )


    # ========================================================
    # FIND SIGNAL
    # ========================================================

    signal = find_signal(
        m15,
        five,
        h1,
        entry_price,
    )


    if not signal:

        print()
        print(
            "[RESULT] No valid signal"
        )

        return


    direction = signal[
        "direction"
    ]

    setup_id = signal[
        "setup_id"
    ]


    # ========================================================
    # COOLDOWN / DUPLICATE
    # ========================================================

    if not signal_allowed(
        state,
        direction,
        setup_id,
    ):

        print(
            "[RESULT] Signal blocked"
        )

        return


    # ========================================================
    # ENTER
    # ========================================================

    enter_position(
        state,
        signal,
        m15,
        five,
        h1,
        entry_price,
        entry_candle_time,
    )


    print_state(state)


# ============================================================
# EXECUTION
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        print()
        print(
            "===================================="
        )

        print(
            "[FATAL ERROR]"
        )

        print(
            str(e)
        )

        print(
            "===================================="
        )

        traceback.print_exc()

        try:

            state = load_state()

            append_log(
                "FATAL_ERROR",
                state,
                None,
                {
                    "error": str(e),
                    "traceback":
                        traceback.format_exc()[
                            -5000:
                        ],
                },
            )

        except Exception:

            pass
