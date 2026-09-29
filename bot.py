import os
import json
import time
from datetime import datetime, timezone

import requests
import numpy as np
import pandas as pd
import yfinance as yf


# ============================================================
# 금 선물 스마트 시그널 봇 V11
#
# SIGNAL -> PENDING ENTRY -> ACTIVE POSITION
#
# M15 LEAD
# 5M SUPPORT
# H1 CONTEXT
# PULLBACK ENTRY
# TELEGRAM RECOVERY
# ============================================================

TICKER = "GC=F"

STATE_FILE = "signal_state.json"
LOG_FILE = "bot_log.json"


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_TOKEN = (
    os.getenv("TELEGRAM_TOKEN")
    or os.getenv("TELEGRAM_BOT_TOKEN")
)

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ============================================================
# 전략 설정
# ============================================================

MIN_M15_SCORE = 4
STRONG_M15_SCORE = 5

MIN_ADX = 13.0

LONG_RSI_MIN = 50.0
LONG_RSI_MAX = 72.0

SHORT_RSI_MIN = 28.0
SHORT_RSI_MAX = 50.0

# 신호 이후 진입 가격 영역
ENTRY_ZONE_ATR = 0.45

# 진입 구간의 방향성
LONG_ZONE_HIGH_ATR = 0.10
SHORT_ZONE_LOW_ATR = 0.10

# 신호 발생 후 너무 멀리 움직이면 대기 취소
MAX_PENDING_DISTANCE_ATR = 1.50

# 신호가 너무 오래되면 취소
PENDING_TIMEOUT_MINUTES = 90

# 새 신호 쿨다운
COOLDOWN_MINUTES = 60

# 손절
SWING_LOOKBACK = 8
SL_ATR_BUFFER = 0.35

MIN_RISK_ATR = 0.55
MAX_RISK_ATR = 2.50

# TP
TP1_R = 1.20
TP2_R = 2.00
TP3_R = 3.00


# ============================================================
# 시간 / 출력
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def now_text():
    return now_utc().strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def money(value):
    return f"${float(value):,.2f}"


# ============================================================
# 기본 상태
# ============================================================

def default_state():

    return {
        "status": "NONE",

        "direction": None,

        # PENDING 상태
        "signal_price": None,
        "entry_zone_low": None,
        "entry_zone_high": None,
        "pending_time": None,
        "pending_alert_sent": False,

        # ACTIVE 상태
        "entry": None,
        "sl": None,
        "tp1": None,
        "tp2": None,
        "tp3": None,
        "entry_time": None,

        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,
        "sl_hit": False,

        "entry_alert_sent": False,

        "last_alert": None,

        "last_signal_time": None,
        "last_signal_direction": None,
    }


def load_state():

    if not os.path.exists(STATE_FILE):
        return default_state()

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        state = default_state()

        if isinstance(data, dict):
            state.update(data)

        return state

    except Exception as e:

        print(
            f"State load error: {e}"
        )

        return default_state()


def save_state(state):

    temp_file = STATE_FILE + ".tmp"

    with open(
        temp_file,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(
        temp_file,
        STATE_FILE
    )


def append_log(
    event,
    data=None
):

    logs = []

    if os.path.exists(LOG_FILE):

        try:

            with open(
                LOG_FILE,
                "r",
                encoding="utf-8"
            ) as f:

                logs = json.load(f)

            if not isinstance(
                logs,
                list
            ):
                logs = []

        except Exception:

            logs = []

    logs.append(
        {
            "time": now_text(),
            "event": event,
            "data": data or {}
        }
    )

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
# TELEGRAM
# ============================================================

def send_telegram(
    message,
    retries=3
):

    if (
        not TELEGRAM_TOKEN
        or not TELEGRAM_CHAT_ID
    ):

        print()
        print(
            "===================================="
        )
        print(
            "TELEGRAM ERROR"
        )
        print(
            "===================================="
        )

        print(
            "Telegram credentials missing."
        )

        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    for attempt in range(
        1,
        retries + 1
    ):

        try:

            response = requests.post(
                url,
                data=payload,
                timeout=15
            )

            if response.status_code == 200:

                result = response.json()

                if result.get("ok"):

                    print(
                        "Telegram sent successfully."
                    )

                    return True

                print(
                    "Telegram API error:",
                    result
                )

            else:

                print(
                    "Telegram HTTP error:",
                    response.status_code
                )

                print(
                    response.text[:500]
                )

        except Exception as e:

            print(
                f"Telegram error "
                f"attempt {attempt}: {e}"
            )

        if attempt < retries:
            time.sleep(2)

    return False


# ============================================================
# MARKET DATA
# ============================================================

def download_data():

    print(
        "Downloading market data..."
    )

    result = {}

    intervals = {
        "1h": "180d",
        "15m": "60d",
        "5m": "30d",
        "1m": "7d",
    }

    for interval, period in intervals.items():

        try:

            df = yf.download(
                TICKER,
                period=period,
                interval=interval,
                auto_adjust=False,
                progress=False,
                threads=False,
            )

            if (
                df is None
                or df.empty
            ):

                print(
                    f"{interval}: NO DATA"
                )

                result[interval] = None
                continue

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
                "Volume",
            ]

            df = df[
                required
            ].copy()

            df = df.dropna()

            print(
                f"{interval}: "
                f"{len(df)} candles"
            )

            result[interval] = df

        except Exception as e:

            print(
                f"{interval}: ERROR - {e}"
            )

            result[interval] = None

    return result


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
        100 / (1 + rs)
    )

    return result.fillna(50)


def atr(
    df,
    length=14
):

    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    previous_close = (
        close.shift(1)
    )

    tr1 = high - low

    tr2 = (
        high -
        previous_close
    ).abs()

    tr3 = (
        low -
        previous_close
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

    tr1 = high - low

    tr2 = (
        high -
        close.shift()
    ).abs()

    tr3 = (
        low -
        close.shift()
    ).abs()

    tr = pd.concat(
        [
            tr1,
            tr2,
            tr3
        ],
        axis=1
    ).max(axis=1)

    atr_value = tr.ewm(
        alpha=1 / length,
        adjust=False
    ).mean()

    plus_dm = pd.Series(
        plus_dm,
        index=df.index
    )

    minus_dm = pd.Series(
        minus_dm,
        index=df.index
    )

    plus_di = (
        100 *
        plus_dm.ewm(
            alpha=1 / length,
            adjust=False
        ).mean()
        /
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
        ).mean()
        /
        atr_value.replace(
            0,
            np.nan
        )
    )

    denominator = (
        plus_di +
        minus_di
    ).replace(
        0,
        np.nan
    )

    dx = (
        100 *
        (
            plus_di -
            minus_di
        ).abs()
        /
        denominator
    )

    return dx.ewm(
        alpha=1 / length,
        adjust=False
    ).mean().fillna(0)


def prepare(df):

    df = df.copy()

    df["EMA20"] = ema(
        df["Close"],
        20
    )

    df["EMA50"] = ema(
        df["Close"],
        50
    )

    df["RSI"] = rsi(
        df["Close"],
        14
    )

    df["ATR"] = atr(
        df,
        14
    )

    df["ADX"] = adx(
        df,
        14
    )

    return df.dropna()


# ============================================================
# H1 CONTEXT
# ============================================================

def h1_context(df):

    row = df.iloc[-1]

    close = float(
        row["Close"]
    )

    e20 = float(
        row["EMA20"]
    )

    e50 = float(
        row["EMA50"]
    )

    bull = (
        close > e20
        and e20 > e50
    )

    bear = (
        close < e20
        and e20 < e50
    )

    return bull, bear


# ============================================================
# M15 SCORE
# ============================================================

def m15_score(df):

    row = df.iloc[-1]
    prev = df.iloc[-2]

    close = float(
        row["Close"]
    )

    e20 = float(
        row["EMA20"]
    )

    e50 = float(
        row["EMA50"]
    )

    r = float(
        row["RSI"]
    )

    a = float(
        row["ADX"]
    )

    prev_high = float(
        prev["High"]
    )

    prev_low = float(
        prev["Low"]
    )

    bullish_candle = (
        float(row["Close"])
        >
        float(row["Open"])
    )

    bearish_candle = (
        float(row["Close"])
        <
        float(row["Open"])
    )

    long_score = 0
    short_score = 0

    # 1
    if close > e20:
        long_score += 1

    if close < e20:
        short_score += 1

    # 2
    if e20 > e50:
        long_score += 1

    if e20 < e50:
        short_score += 1

    # 3
    if bullish_candle:
        long_score += 1

    if bearish_candle:
        short_score += 1

    # 4
    if close > prev_high:
        long_score += 1

    if close < prev_low:
        short_score += 1

    # 5
    if (
        LONG_RSI_MIN
        <= r
        <= LONG_RSI_MAX
    ):
        long_score += 1

    if (
        SHORT_RSI_MIN
        <= r
        <= SHORT_RSI_MAX
    ):
        short_score += 1

    # 6
    if a >= MIN_ADX:

        if close > e20:
            long_score += 1

        if close < e20:
            short_score += 1

    return (
        long_score,
        short_score
    )


# ============================================================
# 5M SCORE
# ============================================================

def m5_score(df):

    row = df.iloc[-1]
    prev = df.iloc[-2]

    close = float(
        row["Close"]
    )

    e20 = float(
        row["EMA20"]
    )

    e50 = float(
        row["EMA50"]
    )

    prev_close = float(
        prev["Close"]
    )

    long_score = 0
    short_score = 0

    if close > e20:
        long_score += 1

    if close < e20:
        short_score += 1

    if e20 > e50:
        long_score += 1

    if e20 < e50:
        short_score += 1

    if close > prev_close:
        long_score += 1

    if close < prev_close:
        short_score += 1

    return (
        long_score,
        short_score
    )


# ============================================================
# DISTANCE
# ============================================================

def distance_from_ema(df):

    row = df.iloc[-1]

    close = float(
        row["Close"]
    )

    e20 = float(
        row["EMA20"]
    )

    atr_value = float(
        row["ATR"]
    )

    if atr_value <= 0:
        return 999

    return (
        abs(close - e20)
        / atr_value
    )


# ============================================================
# 최근 움직임
# ============================================================

def recent_move_atr(df):

    row = df.iloc[-1]

    close = float(
        row["Close"]
    )

    atr_value = float(
        row["ATR"]
    )

    if atr_value <= 0:
        return 999

    lookback = min(
        3,
        len(df) - 1
    )

    old_close = float(
        df.iloc[
            -1 - lookback
        ]["Close"]
    )

    return (
        abs(close - old_close)
        / atr_value
    )


# ============================================================
# CANDLE
# ============================================================

def candle_strength(df):

    row = df.iloc[-1]

    body = abs(
        float(row["Close"])
        -
        float(row["Open"])
    )

    candle_range = (
        float(row["High"])
        -
        float(row["Low"])
    )

    if candle_range <= 0:
        return 0

    return body / candle_range


# ============================================================
# COOLDOWN
# ============================================================

def cooldown_ok(state):

    last_time = state.get(
        "last_signal_time"
    )

    if not last_time:
        return True

    try:

        last = datetime.fromisoformat(
            last_time
        )

        if last.tzinfo is None:

            last = last.replace(
                tzinfo=timezone.utc
            )

        elapsed = (
            now_utc() - last
        ).total_seconds() / 60

        return (
            elapsed >=
            COOLDOWN_MINUTES
        )

    except Exception:

        return True


# ============================================================
# V10 ACTIVE -> V11 PENDING 변환
# ============================================================

def migrate_old_active_state(state):

    if state.get("status") == "PENDING":
        return state

    if (
        state.get("status") == "ACTIVE"
    ):
        return state

    # V10은 active=True 형태였음
    if state.get("active"):

        direction = state.get(
            "direction"
        )

        entry = state.get(
            "entry"
        )

        if (
            direction
            and entry
        ):

            entry = float(entry)

            # 기존 진입가를
            # 새로운 "신호 기준가"로 사용
            state["status"] = "PENDING"

            state["signal_price"] = entry

            if direction == "LONG":

                state["entry_zone_low"] = (
                    entry * 0.9990
                )

                state["entry_zone_high"] = (
                    entry * 1.0005
                )

            else:

                state["entry_zone_low"] = (
                    entry * 0.9995
                )

                state["entry_zone_high"] = (
                    entry * 1.0010
                )

            state["pending_time"] = (
                state.get(
                    "entry_time"
                )
                or now_text()
            )

            state["pending_alert_sent"] = True

            state["active"] = False

            print()
            print(
                "===================================="
            )
            print(
                "V10 POSITION -> V11 PENDING"
            )
            print(
                "===================================="
            )

            print(
                "기존 V10 포지션을"
            )

            print(
                "새로운 진입 대기 상태로 변환했습니다."
            )

            save_state(state)

    return state


# ============================================================
# 진입 대기 메시지
# ============================================================

def pending_message(state):

    direction = state[
        "direction"
    ]

    if direction == "LONG":

        title = "🟡 롱 진입 대기"

        direction_text = "롱"

    else:

        title = "🟡 숏 진입 대기"

        direction_text = "숏"

    return f"""
<b>🥇 금 선물 스마트 시그널</b>

{title}
━━━━━━━━━━━━━━━━━━

📌 방향 : <b>{direction_text}</b>

📍 신호 기준가 :
<b>{money(state["signal_price"])}</b>

🎯 진입 구간 :
<b>{money(state["entry_zone_low"])}</b>
~
<b>{money(state["entry_zone_high"])}</b>

━━━━━━━━━━━━━━━━━━
⚠️ 아직 실제 진입 포지션이 아닙니다.
가격이 진입 구간에 들어오면
실제 진입 신호가 발생합니다.

━━━━━━━━━━━━━━━━━━
⚙️ M15 주도 + 5M 확인
━━━━━━━━━━━━━━━━━━
"""


# ============================================================
# 실제 진입 메시지
# ============================================================

def active_entry_message(state):

    direction = state[
        "direction"
    ]

    if direction == "LONG":

        title = "🟢 롱 실제 진입"

        direction_text = "롱"

    else:

        title = "🔴 숏 실제 진입"

        direction_text = "숏"

    return f"""
<b>🥇 금 선물 스마트 시그널</b>

{title}
━━━━━━━━━━━━━━━━━━

📌 방향 : <b>{direction_text}</b>

💰 실제 진입가 :
<b>{money(state["entry"])}</b>

🛑 손절가 :
{money(state["sl"])}

🎯 익절 1 :
{money(state["tp1"])}

🎯 익절 2 :
{money(state["tp2"])}

🎯 익절 3 :
{money(state["tp3"])}

━━━━━━━━━━━━━━━━━━
⚙️ M15 주도 + 5M 확인
━━━━━━━━━━━━━━━━━━
"""


# ============================================================
# PENDING 만료 메시지
# ============================================================

def pending_cancel_message(state):

    direction = state[
        "direction"
    ]

    if direction == "LONG":
        emoji = "🟢"
    else:
        emoji = "🔴"

    return f"""
<b>🥇 금 선물 스마트 시그널</b>

{emoji} <b>진입 대기 취소</b>
━━━━━━━━━━━━━━━━━━

방향 : {direction}

신호 기준가 :
{money(state["signal_price"])}

━━━━━━━━━━━━━━━━━━
가격이 진입 구간으로 돌아오지 않아
이번 신호를 취소했습니다.
━━━━━━━━━━━━━━━━━━
"""


# ============================================================
# PENDING 생성
# ============================================================

def create_pending(
    state,
    direction,
    df15,
    m15_long,
    m15_short,
    m5_long,
    m5_short
):

    row = df15.iloc[-1]

    signal_price = float(
        row["Close"]
    )

    atr_value = float(
        row["ATR"]
    )

    if direction == "LONG":

        zone_low = (
            signal_price
            -
            atr_value *
            ENTRY_ZONE_ATR
        )

        zone_high = (
            signal_price
            +
            atr_value *
            LONG_ZONE_HIGH_ATR
        )

    else:

        zone_low = (
            signal_price
            -
            atr_value *
            SHORT_ZONE_LOW_ATR
        )

        zone_high = (
            signal_price
            +
            atr_value *
            ENTRY_ZONE_ATR
        )

    new_state = default_state()

    new_state["status"] = "PENDING"

    new_state["direction"] = (
        direction
    )

    new_state["signal_price"] = (
        signal_price
    )

    new_state["entry_zone_low"] = (
        zone_low
    )

    new_state["entry_zone_high"] = (
        zone_high
    )

    new_state["pending_time"] = (
        now_text()
    )

    new_state["pending_alert_sent"] = (
        False
    )

    new_state["last_signal_time"] = (
        now_utc().isoformat()
    )

    new_state["last_signal_direction"] = (
        direction
    )

    print()
    print(
        "===================================="
    )
    print(
        "PENDING ENTRY CREATED"
    )
    print(
        "===================================="
    )

    print(
        f"Direction     : {direction}"
    )

    print(
        f"Signal Price  : "
        f"{money(signal_price)}"
    )

    print(
        f"Entry Zone    : "
        f"{money(zone_low)}"
        f" ~ "
        f"{money(zone_high)}"
    )

    # 텔레그램
    sent = send_telegram(
        pending_message(
            new_state
        )
    )

    if sent:

        new_state[
            "pending_alert_sent"
        ] = True

        new_state[
            "last_alert"
        ] = "PENDING"

    state.clear()

    state.update(
        new_state
    )

    save_state(state)

    append_log(
        "PENDING_ENTRY",
        {
            "direction": direction,
            "signal_price": signal_price,
            "entry_zone_low": zone_low,
            "entry_zone_high": zone_high,
            "m15_long": m15_long,
            "m15_short": m15_short,
            "m5_long": m5_long,
            "m5_short": m5_short,
        }
    )

    return state


# ============================================================
# PENDING 복구
# ============================================================

def recover_pending_alert(
    state
):

    if (
        state.get("status")
        != "PENDING"
    ):

        return state

    if state.get(
        "pending_alert_sent"
    ):

        return state

    print()
    print(
        "===================================="
    )
    print(
        "PENDING ALERT RECOVERY"
    )
    print(
        "===================================="
    )

    if send_telegram(
        pending_message(state)
    ):

        state[
            "pending_alert_sent"
        ] = True

        state[
            "last_alert"
        ] = "PENDING"

        save_state(state)

        print(
            "Pending alert: SENT"
        )

    else:

        print(
            "Pending alert: FAILED"
        )

    return state


# ============================================================
# PENDING -> ACTIVE
# ============================================================

def calculate_targets(
    df,
    direction,
    entry
):

    row = df.iloc[-1]

    atr_value = float(
        row["ATR"]
    )

    recent = df.iloc[
        -SWING_LOOKBACK:
    ]

    recent_low = float(
        recent["Low"].min()
    )

    recent_high = float(
        recent["High"].max()
    )

    if direction == "LONG":

        sl = (
            recent_low
            -
            atr_value *
            SL_ATR_BUFFER
        )

        risk = (
            entry - sl
        )

    else:

        sl = (
            recent_high
            +
            atr_value *
            SL_ATR_BUFFER
        )

        risk = (
            sl - entry
        )

    min_risk = (
        atr_value *
        MIN_RISK_ATR
    )

    max_risk = (
        atr_value *
        MAX_RISK_ATR
    )

    if risk < min_risk:

        if direction == "LONG":

            sl = (
                entry -
                min_risk
            )

        else:

            sl = (
                entry +
                min_risk
            )

        risk = min_risk

    if risk > max_risk:

        return None

    if direction == "LONG":

        tp1 = (
            entry +
            risk * TP1_R
        )

        tp2 = (
            entry +
            risk * TP2_R
        )

        tp3 = (
            entry +
            risk * TP3_R
        )

    else:

        tp1 = (
            entry -
            risk * TP1_R
        )

        tp2 = (
            entry -
            risk * TP2_R
        )

        tp3 = (
            entry -
            risk * TP3_R
        )

    return {
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "risk": risk,
    }


def activate_pending(
    state,
    df15,
    current_price
):

    direction = state[
        "direction"
    ]

    targets = calculate_targets(
        df15,
        direction,
        current_price
    )

    if targets is None:

        print(
            "Risk too large. "
            "Pending entry rejected."
        )

        return state

    state["status"] = "ACTIVE"

    state["entry"] = (
        targets["entry"]
    )

    state["sl"] = (
        targets["sl"]
    )

    state["tp1"] = (
        targets["tp1"]
    )

    state["tp2"] = (
        targets["tp2"]
    )

    state["tp3"] = (
        targets["tp3"]
    )

    state["entry_time"] = (
        now_text()
    )

    state["tp1_hit"] = False
    state["tp2_hit"] = False
    state["tp3_hit"] = False
    state["sl_hit"] = False

    state["entry_alert_sent"] = False

    print()
    print(
        "===================================="
    )
    print(
        "ACTUAL ENTRY CONFIRMED"
    )
    print(
        "===================================="
    )

    print(
        f"Direction : {direction}"
    )

    print(
        f"Entry     : "
        f"{money(current_price)}"
    )

    print(
        f"SL        : "
        f"{money(state['sl'])}"
    )

    print(
        f"TP1       : "
        f"{money(state['tp1'])}"
    )

    print(
        f"TP2       : "
        f"{money(state['tp2'])}"
    )

    print(
        f"TP3       : "
        f"{money(state['tp3'])}"
    )

    sent = send_telegram(
        active_entry_message(
            state
        )
    )

    if sent:

        state[
            "entry_alert_sent"
        ] = True

        state[
            "last_alert"
        ] = "ENTRY"

    save_state(state)

    append_log(
        "ACTUAL_ENTRY",
        {
            "direction": direction,
            "entry": current_price,
            "sl": state["sl"],
            "tp1": state["tp1"],
            "tp2": state["tp2"],
            "tp3": state["tp3"],
        }
    )

    return state


# ============================================================
# PENDING 관리
# ============================================================

def monitor_pending(
    state,
    df15,
    df1m
):

    if (
        state.get("status")
        != "PENDING"
    ):

        return state

    state = recover_pending_alert(
        state
    )

    direction = state[
        "direction"
    ]

    signal_price = float(
        state["signal_price"]
    )

    zone_low = float(
        state["entry_zone_low"]
    )

    zone_high = float(
        state["entry_zone_high"]
    )

    # --------------------------------------------------------
    # 최신 가격
    # --------------------------------------------------------

    if (
        df1m is not None
        and not df1m.empty
    ):

        current_price = float(
            df1m.iloc[-1]["Close"]
        )

    else:

        current_price = float(
            df15.iloc[-1]["Close"]
        )

    # --------------------------------------------------------
    # 대기 시간 확인
    # --------------------------------------------------------

    pending_time = state.get(
        "pending_time"
    )

    if pending_time:

        try:

            started = datetime.fromisoformat(
                pending_time
            )

            if started.tzinfo is None:

                started = started.replace(
                    tzinfo=timezone.utc
                )

            elapsed = (
                now_utc() - started
            ).total_seconds() / 60

            if (
                elapsed >
                PENDING_TIMEOUT_MINUTES
            ):

                print()
                print(
                    "Pending entry expired."
                )

                send_telegram(
                    pending_cancel_message(
                        state
                    )
                )

                append_log(
                    "PENDING_EXPIRED",
                    {
                        "direction": direction,
                        "signal_price": signal_price,
                    }
                )

                state.clear()

                state.update(
                    default_state()
                )

                save_state(state)

                return state

        except Exception:
            pass

    # --------------------------------------------------------
    # 너무 멀리 움직였는지 확인
    # --------------------------------------------------------

    atr_value = float(
        df15.iloc[-1]["ATR"]
    )

    distance = (
        abs(
            current_price -
            signal_price
        )
        /
        atr_value
        if atr_value > 0
        else 999
    )

    print()
    print(
        "===================================="
    )
    print(
        "PENDING ENTRY"
    )
    print(
        "===================================="
    )

    print(
        f"Direction    : {direction}"
    )

    print(
        f"Signal Price : "
        f"{money(signal_price)}"
    )

    print(
        f"Entry Zone   : "
        f"{money(zone_low)}"
        f" ~ "
        f"{money(zone_high)}"
    )

    print(
        f"Current      : "
        f"{money(current_price)}"
    )

    print(
        f"Distance     : "
        f"{distance:.2f} ATR"
    )

    # --------------------------------------------------------
    # 너무 멀리 가면 취소
    # --------------------------------------------------------

    if (
        distance >
        MAX_PENDING_DISTANCE_ATR
    ):

        print(
            "Pending entry cancelled:"
        )

        print(
            "price moved too far."
        )

        send_telegram(
            pending_cancel_message(
                state
            )
        )

        append_log(
            "PENDING_DISTANCE_CANCEL",
            {
                "direction": direction,
                "signal_price": signal_price,
                "current_price": current_price,
                "distance_atr": distance,
            }
        )

        state.clear()

        state.update(
            default_state()
        )

        save_state(state)

        return state

    # --------------------------------------------------------
    # 진입 구간 도달
    # --------------------------------------------------------

    in_zone = (
        zone_low
        <= current_price
        <= zone_high
    )

    if in_zone:

        print()
        print(
            "ENTRY ZONE REACHED"
        )

        print(
            "Actual entry confirmed."
        )

        state = activate_pending(
            state,
            df15,
            current_price
        )

        return state

    print(
        "Waiting for entry zone..."
    )

    return state


# ============================================================
# TP / SL
# ============================================================

def tp_message(
    direction,
    entry,
    target,
    level,
    current
):

    emoji = (
        "🟢"
        if direction == "LONG"
        else "🔴"
    )

    return f"""
<b>🥇 금 선물 알림</b>

{emoji} <b>{level} 도달</b>
━━━━━━━━━━━━━━━━━━

방향 : {direction}

진입가 :
{money(entry)}

목표가 :
{money(target)}

현재가 :
{money(current)}

━━━━━━━━━━━━━━━━━━
"""


def sl_message(
    direction,
    entry,
    sl,
    current
):

    return f"""
<b>🥇 금 선물 알림</b>

🔴 <b>손절 처리</b>
━━━━━━━━━━━━━━━━━━

방향 : {direction}

진입가 :
{money(entry)}

손절가 :
{money(sl)}

청산가 :
{money(current)}

━━━━━━━━━━━━━━━━━━
🛑 손절 처리 완료
━━━━━━━━━━━━━━━━━━
"""


# ============================================================
# ACTIVE POSITION 관리
# ============================================================

def monitor_active(
    state,
    df1m
):

    if (
        state.get("status")
        != "ACTIVE"
    ):

        return state

    if (
        df1m is None
        or df1m.empty
    ):

        return state

    current = float(
        df1m.iloc[-1]["Close"]
    )

    direction = state[
        "direction"
    ]

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

    print()
    print(
        "===================================="
    )
    print(
        "ACTIVE POSITION"
    )
    print(
        "===================================="
    )

    print(
        f"Direction : {direction}"
    )

    print(
        f"Entry     : {money(entry)}"
    )

    print(
        f"SL        : {money(sl)}"
    )

    print(
        f"TP1       : {money(tp1)}"
    )

    print(
        f"TP2       : {money(tp2)}"
    )

    print(
        f"TP3       : {money(tp3)}"
    )

    print(
        f"1M Close  : {money(current)}"
    )

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        if current <= sl:

            if send_telegram(
                sl_message(
                    direction,
                    entry,
                    sl,
                    current
                )
            ):

                state["status"] = "NONE"
                state["sl_hit"] = True
                state["last_alert"] = "SL"

                save_state(state)

                append_log(
                    "STOP_LOSS",
                    {
                        "direction": direction,
                        "entry": entry,
                        "exit": current,
                    }
                )

            return state

        if (
            not state.get("tp1_hit")
            and current >= tp1
        ):

            if send_telegram(
                tp_message(
                    direction,
                    entry,
                    tp1,
                    "TP1",
                    current
                )
            ):

                state["tp1_hit"] = True
                state["last_alert"] = "TP1"

                save_state(state)

        if (
            not state.get("tp2_hit")
            and current >= tp2
        ):

            if send_telegram(
                tp_message(
                    direction,
                    entry,
                    tp2,
                    "TP2",
                    current
                )
            ):

                state["tp2_hit"] = True
                state["last_alert"] = "TP2"

                save_state(state)

        if (
            not state.get("tp3_hit")
            and current >= tp3
        ):

            if send_telegram(
                tp_message(
                    direction,
                    entry,
                    tp3,
                    "TP3",
                    current
                )
            ):

                state["tp3_hit"] = True
                state["status"] = "NONE"
                state["last_alert"] = "TP3"

                save_state(state)

                append_log(
                    "TP3_COMPLETE",
                    {
                        "direction": direction,
                        "entry": entry,
                        "exit": current,
                    }
                )

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    else:

        if current >= sl:

            if send_telegram(
                sl_message(
                    direction,
                    entry,
                    sl,
                    current
                )
            ):

                state["status"] = "NONE"
                state["sl_hit"] = True
                state["last_alert"] = "SL"

                save_state(state)

                append_log(
                    "STOP_LOSS",
                    {
                        "direction": direction,
                        "entry": entry,
                        "exit": current,
                    }
                )

            return state

        if (
            not state.get("tp1_hit")
            and current <= tp1
        ):

            if send_telegram(
                tp_message(
                    direction,
                    entry,
                    tp1,
                    "TP1",
                    current
                )
            ):

                state["tp1_hit"] = True
                state["last_alert"] = "TP1"

                save_state(state)

        if (
            not state.get("tp2_hit")
            and current <= tp2
        ):

            if send_telegram(
                tp_message(
                    direction,
                    entry,
                    tp2,
                    "TP2",
                    current
                )
            ):

                state["tp2_hit"] = True
                state["last_alert"] = "TP2"

                save_state(state)

        if (
            not state.get("tp3_hit")
            and current <= tp3
        ):

            if send_telegram(
                tp_message(
                    direction,
                    entry,
                    tp3,
                    "TP3",
                    current
                )
            ):

                state["tp3_hit"] = True
                state["status"] = "NONE"
                state["last_alert"] = "TP3"

                save_state(state)

                append_log(
                    "TP3_COMPLETE",
                    {
                        "direction": direction,
                        "entry": entry,
                        "exit": current,
                    }
                )

    return state


# ============================================================
# 메인
# ============================================================

def main():

    print(
        "================================="
    )

    print(
        " 금 선물 스마트 시그널 봇"
    )

    print(
        " BALANCED V11"
    )

    print(
        " SIGNAL -> PENDING -> ACTIVE"
    )

    print(
        " M15 LEAD + 5M SUPPORT"
    )

    print(
        " H1 CONTEXT ONLY"
    )

    print(
        " PULLBACK ENTRY"
    )

    print(
        " TELEGRAM RECOVERY ENABLED"
    )

    print(
        "================================="
    )

    state = load_state()

    # --------------------------------------------------------
    # V10 상태 변환
    # --------------------------------------------------------

    state = migrate_old_active_state(
        state
    )

    # --------------------------------------------------------
    # 데이터
    # --------------------------------------------------------

    data = download_data()

    df1h = data.get("1h")
    df15 = data.get("15m")
    df5 = data.get("5m")
    df1m = data.get("1m")

    if (
        df1h is None
        or df15 is None
        or df5 is None
        or df1m is None
    ):

        print(
            "Required market data unavailable."
        )

        return

    df1h = prepare(df1h)
    df15 = prepare(df15)
    df5 = prepare(df5)
    df1m = prepare(df1m)

    if (
        len(df1h) < 60
        or len(df15) < 60
        or len(df5) < 60
    ):

        print(
            "Not enough candles."
        )

        return

    # ========================================================
    # PENDING 상태
    # ========================================================

    if (
        state.get("status")
        == "PENDING"
    ):

        state = monitor_pending(
            state,
            df15,
            df1m
        )

        save_state(state)

        if (
            state.get("status")
            == "ACTIVE"
        ):

            print(
                "Actual position is now active."
            )

        else:

            print(
                "Still waiting for entry."
            )

        return

    # ========================================================
    # ACTIVE 상태
    # ========================================================

    if (
        state.get("status")
        == "ACTIVE"
    ):

        state = monitor_active(
            state,
            df1m
        )

        save_state(state)

        if (
            state.get("status")
            == "ACTIVE"
        ):

            print(
                "Active position remains open."
            )

        return

    # ========================================================
    # CURRENT MARKET
    # ========================================================

    row = df15.iloc[-1]

    close = float(
        row["Close"]
    )

    e20 = float(
        row["EMA20"]
    )

    e50 = float(
        row["EMA50"]
    )

    r = float(
        row["RSI"]
    )

    a = float(
        row["ADX"]
    )

    atr_value = float(
        row["ATR"]
    )

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
        f"M15 Close : {money(close)}"
    )

    print(
        f"EMA20     : {money(e20)}"
    )

    print(
        f"EMA50     : {money(e50)}"
    )

    print(
        f"RSI       : {r:.2f}"
    )

    print(
        f"ADX       : {a:.2f}"
    )

    print(
        f"ATR       : {atr_value:.2f}"
    )

    # ========================================================
    # SCORE
    # ========================================================

    m15_long, m15_short = (
        m15_score(df15)
    )

    m5_long, m5_short = (
        m5_score(df5)
    )

    h1_bull, h1_bear = (
        h1_context(df1h)
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
        f"M15 Score : {m15_long}/6"
    )

    print(
        f"5M Score  : {m5_long}/3"
    )

    print(
        "H1        : "
        +
        (
            "BULL"
            if h1_bull
            else "NOT BULL"
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
        f"M15 Score : {m15_short}/6"
    )

    print(
        f"5M Score  : {m5_short}/3"
    )

    print(
        "H1        : "
        +
        (
            "BEAR"
            if h1_bear
            else "NOT BEAR"
        )
    )

    # ========================================================
    # FILTER
    # ========================================================

    distance = distance_from_ema(
        df15
    )

    move = recent_move_atr(
        df15
    )

    candle = candle_strength(
        df15
    )

    print()
    print(
        "===================================="
    )

    print(
        " FILTER"
    )

    print(
        "===================================="
    )

    print(
        f"Distance : "
        f"{distance:.2f} ATR"
    )

    print(
        f"Move     : "
        f"{move:.2f} ATR"
    )

    print(
        f"Candle   : "
        f"{candle:.2f}"
    )

    # ========================================================
    # COOLDOWN
    # ========================================================

    if not cooldown_ok(state):

        print()
        print(
            "Cooldown active."
        )

        return

    # ========================================================
    # LONG 후보
    # ========================================================

    long_valid = False

    if m15_long >= MIN_M15_SCORE:

        # 5M이 완전히 반대면 제외
        if m5_short < 3:

            normal_long = (
                distance <= 2.00
                and move <= 1.35
                and candle >= 0.18
                and m5_long >= 1
            )

            strong_long = (
                m15_long >= 5
                and m5_long >= 2
                and distance <= 2.20
                and move <= 1.35
            )

            early_long = (
                m15_long >= 4
                and m5_long >= 2
                and a >= 20
                and r >= 55
                and r <= 70
                and distance <= 2.20
                and move <= 1.35
            )

            if (
                normal_long
                or strong_long
                or early_long
            ):

                long_valid = True

    # ========================================================
    # SHORT 후보
    # ========================================================

    short_valid = False

    if m15_short >= MIN_M15_SCORE:

        if m5_long < 3:

            normal_short = (
                distance <= 2.00
                and move <= 1.35
                and candle >= 0.18
                and m5_short >= 1
            )

            strong_short = (
                m15_short >= 5
                and m5_short >= 2
                and distance <= 2.20
                and move <= 1.35
            )

            early_short = (
                m15_short >= 4
                and m5_short >= 2
                and a >= 20
                and r >= 30
                and r <= 45
                and distance <= 2.20
                and move <= 1.35
            )

            if (
                normal_short
                or strong_short
                or early_short
            ):

                short_valid = True

    # ========================================================
    # 양쪽 동시에 발생
    # ========================================================

    if (
        long_valid
        and short_valid
    ):

        if m15_long > m15_short:

            short_valid = False

        elif m15_short > m15_long:

            long_valid = False

        else:

            print(
                "Both directions equal."
            )

            return

    # ========================================================
    # LONG PENDING
    # ========================================================

    if long_valid:

        print()
        print(
            "===================================="
        )

        print(
            "LONG SIGNAL"
        )

        print(
            "===================================="
        )

        print(
            "신호 발생."
        )

        print(
            "바로 ACTIVE가 되지 않습니다."
        )

        print(
            "진입 구간을 기다립니다."
        )

        create_pending(
            state,
            "LONG",
            df15,
            m15_long,
            m15_short,
            m5_long,
            m5_short
        )

        return

    # ========================================================
    # SHORT PENDING
    # ========================================================

    if short_valid:

        print()
        print(
            "===================================="
        )

        print(
            "SHORT SIGNAL"
        )

        print(
            "===================================="
        )

        print(
            "신호 발생."
        )

        print(
            "바로 ACTIVE가 되지 않습니다."
        )

        print(
            "진입 구간을 기다립니다."
        )

        create_pending(
            state,
            "SHORT",
            df15,
            m15_long,
            m15_short,
            m5_long,
            m5_short
        )

        return

    # ========================================================
    # NO SIGNAL
    # ========================================================

    print()
    print(
        "===================================="
    )

    print(
        "NO VALID SIGNAL"
    )

    print(
        "===================================="
    )


# ============================================================
# RUN
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
            "BOT ERROR"
        )

        print(
            "===================================="
        )

        print(
            str(e)
        )

        append_log(
            "BOT_ERROR",
            {
                "error": str(e)
            }
)
