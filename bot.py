import os
import json
import time
from datetime import datetime, timezone

import requests
import numpy as np
import pandas as pd
import yfinance as yf


# ============================================================
# 금 선물 스마트 시그널 봇 V10
# M15 LEAD + 5M SUPPORT
# H1 CONTEXT ONLY
# EARLY MOVE BALANCED
# TELEGRAM RECOVERY
# ============================================================

TICKER = "GC=F"

STATE_FILE = "signal_state.json"
LOG_FILE = "bot_log.json"

# ------------------------------------------------------------
# Telegram
# ------------------------------------------------------------

TELEGRAM_TOKEN = (
    os.getenv("TELEGRAM_TOKEN")
    or os.getenv("TELEGRAM_BOT_TOKEN")
)

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ------------------------------------------------------------
# 전략 설정
# ------------------------------------------------------------

MIN_M15_SCORE = 4
STRONG_M15_SCORE = 5

MIN_ADX = 13.0

# 기존보다 완화
MAX_ENTRY_DISTANCE_ATR = 2.00

# 급격한 캔들 추격 방지
MAX_SIGNAL_MOVE_ATR = 1.35

# RSI
LONG_RSI_MIN = 50.0
LONG_RSI_MAX = 72.0

SHORT_RSI_MIN = 28.0
SHORT_RSI_MAX = 50.0

# 신호 쿨다운
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
# 출력
# ============================================================

def now_text():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def money(value):
    return f"${float(value):,.2f}"


# ============================================================
# 상태
# ============================================================

def default_state():
    return {
        "active": False,
        "direction": None,
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
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        state = default_state()
        state.update(data)
        return state

    except Exception:
        return default_state()


def save_state(state):
    tmp_file = STATE_FILE + ".tmp"

    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(tmp_file, STATE_FILE)


def append_log(event, data=None):
    logs = []

    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)

            if not isinstance(logs, list):
                logs = []

        except Exception:
            logs = []

    logs.append({
        "time": now_text(),
        "event": event,
        "data": data or {}
    })

    logs = logs[-500:]

    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(
            logs,
            f,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# Telegram
# ============================================================

def send_telegram(message, retries=3):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print()
        print("====================================")
        print("TELEGRAM ERROR")
        print("====================================")
        print("Telegram credentials missing.")
        print("Required:")
        print("TELEGRAM_TOKEN")
        print("TELEGRAM_CHAT_ID")
        print()
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

    for attempt in range(1, retries + 1):

        try:
            response = requests.post(
                url,
                data=payload,
                timeout=15
            )

            if response.status_code == 200:
                result = response.json()

                if result.get("ok"):
                    print("Telegram sent successfully.")
                    return True

                print(
                    "Telegram API error:",
                    result
                )

            else:
                print(
                    f"Telegram HTTP error "
                    f"{response.status_code}: "
                    f"{response.text[:500]}"
                )

        except Exception as e:
            print(
                f"Telegram connection error "
                f"(attempt {attempt}): {e}"
            )

        if attempt < retries:
            time.sleep(2)

    return False


# ============================================================
# 시장 데이터
# ============================================================

def download_data():

    print("Downloading market data...")

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

            if df is None or df.empty:
                print(f"{interval}: NO DATA")
                result[interval] = None
                continue

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df = df[
                [
                    "Open",
                    "High",
                    "Low",
                    "Close",
                    "Volume"
                ]
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
# 지표
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
    close = df["Close"]

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where(
        (up_move > down_move) &
        (up_move > 0),
        up_move,
        0
    )

    minus_dm = np.where(
        (down_move > up_move) &
        (down_move > 0),
        down_move,
        0
    )

    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()

    tr = pd.concat(
        [tr1, tr2, tr3],
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
        (plus_di + minus_di).replace(
            0,
            np.nan
        )
    )

    return dx.ewm(
        alpha=1 / length,
        adjust=False
    ).mean().fillna(0)


# ============================================================
# 데이터 준비
# ============================================================

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
# H1 방향
# ============================================================

def h1_context(df):

    row = df.iloc[-1]

    close = float(row["Close"])
    e20 = float(row["EMA20"])
    e50 = float(row["EMA50"])

    bull = (
        close > e20 and
        e20 > e50
    )

    bear = (
        close < e20 and
        e20 < e50
    )

    return bull, bear


# ============================================================
# M15 점수
# ============================================================

def m15_score(df):

    row = df.iloc[-1]
    prev = df.iloc[-2]

    close = float(row["Close"])
    e20 = float(row["EMA20"])
    e50 = float(row["EMA50"])
    r = float(row["RSI"])
    a = float(row["ADX"])

    prev_high = float(prev["High"])
    prev_low = float(prev["Low"])

    bullish_candle = (
        float(row["Close"]) >
        float(row["Open"])
    )

    bearish_candle = (
        float(row["Close"]) <
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
        LONG_RSI_MIN <= r <=
        LONG_RSI_MAX
    ):
        long_score += 1

    if (
        SHORT_RSI_MIN <= r <=
        SHORT_RSI_MAX
    ):
        short_score += 1

    # 6
    if a >= MIN_ADX:
        if close > e20:
            long_score += 1

        if close < e20:
            short_score += 1

    return long_score, short_score


# ============================================================
# 5M 방향
# ============================================================

def m5_score(df):

    row = df.iloc[-1]
    prev = df.iloc[-2]

    close = float(row["Close"])
    e20 = float(row["EMA20"])
    e50 = float(row["EMA50"])

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

    return long_score, short_score


# ============================================================
# 최근 상승/하락 움직임
# ============================================================

def recent_move_atr(df):

    row = df.iloc[-1]

    close = float(row["Close"])
    atr_value = float(row["ATR"])

    if atr_value <= 0:
        return 0

    lookback = min(3, len(df) - 1)

    old_close = float(
        df.iloc[-1 - lookback]["Close"]
    )

    return abs(
        close - old_close
    ) / atr_value


# ============================================================
# Distance 필터
# ============================================================

def distance_check(df):

    row = df.iloc[-1]

    close = float(row["Close"])
    e20 = float(row["EMA20"])
    atr_value = float(row["ATR"])

    if atr_value <= 0:
        return False, 999

    distance = abs(
        close - e20
    ) / atr_value

    return (
        distance <= MAX_ENTRY_DISTANCE_ATR,
        distance
    )


# ============================================================
# Candle 필터
# ============================================================

def candle_check(
    df,
    direction,
    m15_long,
    m15_short,
    m5_long,
    m5_short
):

    row = df.iloc[-1]

    body = abs(
        float(row["Close"]) -
        float(row["Open"])
    )

    candle_range = (
        float(row["High"]) -
        float(row["Low"])
    )

    if candle_range <= 0:
        return False

    body_ratio = (
        body / candle_range
    )

    # 기본적으로 너무 작은 몸통은 제외
    normal_candle = body_ratio >= 0.18

    # 강한 추세에서는 완화
    strong_context = (
        (
            direction == "LONG" and
            m15_long >= STRONG_M15_SCORE and
            m5_long >= 2
        )
        or
        (
            direction == "SHORT" and
            m15_short >= STRONG_M15_SCORE and
            m5_short >= 2
        )
    )

    if strong_context:
        return body_ratio >= 0.10

    return normal_candle


# ============================================================
# 과열 추격 필터
# ============================================================

def move_check(df):

    move = recent_move_atr(df)

    return (
        move <= MAX_SIGNAL_MOVE_ATR,
        move
    )


# ============================================================
# 쿨다운
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

        now = datetime.now(
            timezone.utc
        )

        elapsed = (
            now - last
        ).total_seconds() / 60

        return elapsed >= COOLDOWN_MINUTES

    except Exception:
        return True


# ============================================================
# 손절/목표 계산
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
            recent_low -
            atr_value * SL_ATR_BUFFER
        )

        risk = entry - sl

    else:

        sl = (
            recent_high +
            atr_value * SL_ATR_BUFFER
        )

        risk = sl - entry

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
            sl = entry - min_risk
        else:
            sl = entry + min_risk

        risk = min_risk

    if risk > max_risk:
        return None

    if direction == "LONG":

        tp1 = entry + risk * TP1_R
        tp2 = entry + risk * TP2_R
        tp3 = entry + risk * TP3_R

    else:

        tp1 = entry - risk * TP1_R
        tp2 = entry - risk * TP2_R
        tp3 = entry - risk * TP3_R

    return {
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "risk": risk,
    }


# ============================================================
# 진입 메시지
# ============================================================

def entry_message(position):

    direction = position["direction"]

    if direction == "LONG":
        title = "🟢 롱 진입 신호"
        direction_text = "롱"
    else:
        title = "🔴 숏 진입 신호"
        direction_text = "숏"

    return f"""
<b>🥇 금 선물 스마트 시그널</b>

{title}
━━━━━━━━━━━━━━━━━━

📌 방향 : <b>{direction_text}</b>

💰 진입가 : <b>{money(position["entry"])}</b>

🛑 손절가 : {money(position["sl"])}

🎯 익절 1 : {money(position["tp1"])}
🎯 익절 2 : {money(position["tp2"])}
🎯 익절 3 : {money(position["tp3"])}

━━━━━━━━━━━━━━━━━━
⚙️ 전략 : M15 주도 + 5M 확인
━━━━━━━━━━━━━━━━━━
"""


# ============================================================
# TP / SL 메시지
# ============================================================

def tp_message(
    direction,
    entry,
    target,
    level,
    current
):

    if direction == "LONG":
        emoji = "🟢"
    else:
        emoji = "🔴"

    return f"""
<b>🥇 금 선물 알림</b>

{emoji} <b>익절 {level} 도달</b>
━━━━━━━━━━━━━━━━━━

방향 : {direction}
진입가 : {money(entry)}
목표가 : {money(target)}
현재가 : {money(current)}

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
진입가 : {money(entry)}
청산가 : {money(current)}

손절가 : {money(sl)}

━━━━━━━━━━━━━━━━━━
🛑 손절 처리 완료
━━━━━━━━━━━━━━━━━━
"""


# ============================================================
# 활성 포지션 텔레그램 복구
# ============================================================

def ensure_entry_alert(state):

    if not state.get("active"):
        return state

    if state.get("entry_alert_sent"):
        return state

    print()
    print("====================================")
    print("ENTRY ALERT RECOVERY")
    print("====================================")

    print(
        "Active position detected."
    )

    success = send_telegram(
        entry_message(state)
    )

    if success:
        state["entry_alert_sent"] = True
        state["last_alert"] = "ENTRY"
        save_state(state)

        print(
            "Entry alert status: SENT"
        )

    else:

        print(
            "Entry alert status: FAILED"
        )

    return state


# ============================================================
# 활성 포지션 감시
# ============================================================

def monitor_position(
    state,
    df1m
):

    if not state.get("active"):
        return state

    if df1m is None or df1m.empty:
        return state

    row = df1m.iloc[-1]

    current = float(
        row["Close"]
    )

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

    print()
    print("====================================")
    print("ACTIVE POSITION")
    print("====================================")

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

        # SL
        if current <= sl:

            success = send_telegram(
                sl_message(
                    direction,
                    entry,
                    sl,
                    current
                )
            )

            if success:

                state["active"] = False
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

        # TP1
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

        # TP2
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

        # TP3
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
                state["active"] = False
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

        # SL
        if current >= sl:

            success = send_telegram(
                sl_message(
                    direction,
                    entry,
                    sl,
                    current
                )
            )

            if success:

                state["active"] = False
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

        # TP1
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

        # TP2
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

        # TP3
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
                state["active"] = False
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
# 새 신호 생성
# ============================================================

def create_signal(
    state,
    direction,
    df15,
    m15_long,
    m15_short,
    m5_long,
    m5_short
):

    row = df15.iloc[-1]

    entry = float(
        row["Close"]
    )

    targets = calculate_targets(
        df15,
        direction,
        entry
    )

    if targets is None:

        print(
            "Risk FAIL"
        )

        return state

    position = {
        "active": True,
        "direction": direction,

        "entry": targets["entry"],
        "sl": targets["sl"],
        "tp1": targets["tp1"],
        "tp2": targets["tp2"],
        "tp3": targets["tp3"],

        "entry_time": now_text(),

        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,
        "sl_hit": False,

        "entry_alert_sent": False,

        "last_alert": "NONE",

        "last_signal_time": datetime.now(
            timezone.utc
        ).isoformat(),

        "last_signal_direction": direction,
    }

    print()
    print("====================================")
    print("NEW SIGNAL")
    print("====================================")

    print(
        f"Direction : {direction}"
    )

    print(
        f"Entry     : {money(position['entry'])}"
    )

    print(
        f"SL        : {money(position['sl'])}"
    )

    print(
        f"TP1       : {money(position['tp1'])}"
    )

    print(
        f"TP2       : {money(position['tp2'])}"
    )

    print(
        f"TP3       : {money(position['tp3'])}"
    )

    message = entry_message(
        position
    )

    sent = send_telegram(
        message
    )

    if sent:
        position[
            "entry_alert_sent"
        ] = True

        print(
            "ENTRY TELEGRAM: SENT"
        )

    else:

        print(
            "ENTRY TELEGRAM: FAILED"
        )

    state.clear()
    state.update(position)

    save_state(state)

    append_log(
        "NEW_SIGNAL",
        {
            "direction": direction,
            "entry": position["entry"],
            "sl": position["sl"],
            "tp1": position["tp1"],
            "tp2": position["tp2"],
            "tp3": position["tp3"],
            "m15_long": m15_long,
            "m15_short": m15_short,
            "m5_long": m5_long,
            "m5_short": m5_short,
        }
    )

    return state


# ============================================================
# 메인
# ============================================================

def main():

    print("=================================")
    print(" 금 선물 스마트 시그널 봇")
    print(" BALANCED V10")
    print(" M15 LEAD + 5M SUPPORT")
    print(" H1 CONTEXT ONLY")
    print(" EARLY MOVE BALANCED")
    print(" TELEGRAM RECOVERY ENABLED")
    print("=================================")

    state = load_state()

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
        len(df15) < 60
        or len(df5) < 60
        or len(df1h) < 60
    ):

        print(
            "Not enough candles."
        )

        return

    # --------------------------------------------------------
    # 기존 포지션이 있으면 먼저 복구
    # --------------------------------------------------------

    if state.get("active"):

        state = ensure_entry_alert(
            state
        )

        state = monitor_position(
            state,
            df1m
        )

        save_state(state)

        # 활성 포지션이 있으면
        # 새로운 포지션 생성하지 않음
        if state.get("active"):

            print()
            print(
                "Active position remains open."
            )

            return

    # --------------------------------------------------------
    # 현재 시장
    # --------------------------------------------------------

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
    print("====================================")
    print(" CURRENT MARKET CHECK")
    print("====================================")

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

    # --------------------------------------------------------
    # 점수
    # --------------------------------------------------------

    m15_long, m15_short = m15_score(
        df15
    )

    m5_long, m5_short = m5_score(
        df5
    )

    h1_bull, h1_bear = h1_context(
        df1h
    )

    print()
    print("====================================")
    print(" LONG CHECK")
    print("====================================")

    print(
        f"M15 Score : {m15_long}/6"
    )

    print(
        f"5M Score  : {m5_long}/3"
    )

    print(
        "H1        : "
        + (
            "BULL"
            if h1_bull
            else "NOT BULL"
        )
    )

    print()
    print("====================================")
    print(" SHORT CHECK")
    print("====================================")

    print(
        f"M15 Score : {m15_short}/6"
    )

    print(
        f"5M Score  : {m5_short}/3"
    )

    print(
        "H1        : "
        + (
            "BEAR"
            if h1_bear
            else "NOT BEAR"
        )
    )

    # --------------------------------------------------------
    # 필터
    # --------------------------------------------------------

    distance_pass, distance = (
        distance_check(df15)
    )

    move_pass, move = move_check(
        df15
    )

    long_candle = candle_check(
        df15,
        "LONG",
        m15_long,
        m15_short,
        m5_long,
        m5_short
    )

    short_candle = candle_check(
        df15,
        "SHORT",
        m15_long,
        m15_short,
        m5_long,
        m5_short
    )

    print()
    print("====================================")
    print(" FILTER")
    print("====================================")

    print(
        f"Distance : "
        f"{'PASS' if distance_pass else 'FAIL'} "
        f"({distance:.2f} ATR)"
    )

    print(
        f"Move     : "
        f"{'PASS' if move_pass else 'FAIL'} "
        f"({move:.2f} ATR)"
    )

    print(
        f"Long Candle  : "
        f"{'PASS' if long_candle else 'FAIL'}"
    )

    print(
        f"Short Candle : "
        f"{'PASS' if short_candle else 'FAIL'}"
    )

    # --------------------------------------------------------
    # 쿨다운
    # --------------------------------------------------------

    if not cooldown_ok(state):

        print()
        print(
            "Cooldown active."
        )

        return

    # --------------------------------------------------------
    # LONG 판단
    # --------------------------------------------------------

    long_valid = False

    if m15_long >= MIN_M15_SCORE:

        # 5M이 완전 반대일 경우 차단
        strong_opposite_5m = (
            m5_short >= 3
        )

        if not strong_opposite_5m:

            # 일반 조건
            normal_condition = (
                distance_pass
                and long_candle
                and move_pass
            )

            # 강한 M15 + 5M 지지
            strong_condition = (
                m15_long >= STRONG_M15_SCORE
                and m5_long >= 2
                and distance <= 2.20
                and move_pass
            )

            # 조기 상승 조건
            early_condition = (
                m15_long >= 4
                and m5_long >= 2
                and a >= 20
                and r >= 55
                and r <= 70
                and distance <= 2.20
                and move_pass
            )

            if (
                normal_condition
                or strong_condition
                or early_condition
            ):
                long_valid = True

    # --------------------------------------------------------
    # SHORT 판단
    # --------------------------------------------------------

    short_valid = False

    if m15_short >= MIN_M15_SCORE:

        strong_opposite_5m = (
            m5_long >= 3
        )

        if not strong_opposite_5m:

            normal_condition = (
                distance_pass
                and short_candle
                and move_pass
            )

            strong_condition = (
                m15_short >= STRONG_M15_SCORE
                and m5_short >= 2
                and distance <= 2.20
                and move_pass
            )

            early_condition = (
                m15_short >= 4
                and m5_short >= 2
                and a >= 20
                and r >= 30
                and r <= 45
                and distance <= 2.20
                and move_pass
            )

            if (
                normal_condition
                or strong_condition
                or early_condition
            ):
                short_valid = True

    # --------------------------------------------------------
    # 둘 다 발생하면 점수 높은 방향
    # --------------------------------------------------------

    if long_valid and short_valid:

        if m15_long > m15_short:
            short_valid = False

        elif m15_short > m15_long:
            long_valid = False

        else:
            print(
                "Both directions equal."
            )

            return

    # --------------------------------------------------------
    # 최종 결과
    # --------------------------------------------------------

    if long_valid:

        print()
        print(
            "===================================="
        )
        print(
            "LONG SIGNAL CONFIRMED"
        )
        print(
            "===================================="
        )

        create_signal(
            state,
            "LONG",
            df15,
            m15_long,
            m15_short,
            m5_long,
            m5_short
        )

        return

    if short_valid:

        print()
        print(
            "===================================="
        )
        print(
            "SHORT SIGNAL CONFIRMED"
        )
        print(
            "===================================="
        )

        create_signal(
            state,
            "SHORT",
            df15,
            m15_long,
            m15_short,
            m5_long,
            m5_short
        )

        return

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
# 실행
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
