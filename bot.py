import os
import json
import requests
import yfinance as yf
import pandas as pd
import numpy as np


# ============================================================
# CONFIG
# ============================================================

TICKER = "GC=F"

STATE_FILE = "signal_state.json"
LOG_FILE = "bot_log.json"

TP1_R = 1.20
TP2_R = 2.00
TP3_R = 3.00

MAX_SL_ATR = 2.50
MAX_ENTRY_DISTANCE_ATR = 1.00
MAX_SIGNAL_MOVE_ATR = 0.50

MIN_ADX_1H = 18
MIN_ADX_15M = 16

LONG_RSI_MIN = 52
LONG_RSI_MAX = 68

SHORT_RSI_MIN = 32
SHORT_RSI_MAX = 48

COOLDOWN_MINUTES = 60


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def send_telegram(message):

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("ERROR: Telegram secrets are missing.")
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

        if response.status_code != 200:
            print("Telegram error:")
            print(response.text)
            return False

        print("Telegram message sent.")
        return True

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


def format_signal_time(value):

    try:

        ts = normalize_timestamp(value)

        return ts.strftime(
            "%Y-%m-%d %H:%M UTC"
        )

    except Exception:

        return ""


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

            data = json.load(f)

        if isinstance(data, dict):
            return data

        return {}

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

def log_event(event, data=None):

    logs = []

    if os.path.exists(LOG_FILE):

        try:

            with open(
                LOG_FILE,
                "r",
                encoding="utf-8"
            ) as f:

                logs = json.load(f)

            if not isinstance(logs, list):
                logs = []

        except Exception:

            logs = []

    item = {
        "time": pd.Timestamp.utcnow().isoformat(),
        "event": event
    }

    if isinstance(data, dict):
        item.update(data)

    logs.append(item)

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
# MARKET DATA
# ============================================================

def get_history():

    print("Downloading market data...")

    data_1h = yf.download(
        TICKER,
        period="30d",
        interval="1h",
        auto_adjust=False,
        progress=False
    )

    data_15m = yf.download(
        TICKER,
        period="10d",
        interval="15m",
        auto_adjust=False,
        progress=False
    )

    data_5m = yf.download(
        TICKER,
        period="5d",
        interval="5m",
        auto_adjust=False,
        progress=False
    )

    data_1m = yf.download(
        TICKER,
        period="5d",
        interval="1m",
        auto_adjust=False,
        progress=False
    )

    def clean(df):

        if df is None or df.empty:
            return pd.DataFrame()

        if isinstance(
            df.columns,
            pd.MultiIndex
        ):

            df.columns = (
                df.columns
                .get_level_values(0)
            )

        df.index = pd.to_datetime(
            df.index
        )

        if df.index.tz is None:

            df.index = (
                df.index
                .tz_localize("UTC")
            )

        else:

            df.index = (
                df.index
                .tz_convert("UTC")
            )

        return df

    return (
        clean(data_1h),
        clean(data_15m),
        clean(data_5m),
        clean(data_1m)
    )


# ============================================================
# REMOVE INCOMPLETE CANDLE
# ============================================================

def remove_incomplete_bar(df):

    if df is None or df.empty:
        return df

    if len(df) <= 2:
        return df

    return df.iloc[:-1].copy()


# ============================================================
# INDICATORS
# ============================================================

def add_indicators(df):

    df = df.copy()

    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    # EMA 20
    df["EMA20"] = (
        close
        .ewm(
            span=20,
            adjust=False
        )
        .mean()
    )

    # EMA 50
    df["EMA50"] = (
        close
        .ewm(
            span=50,
            adjust=False
        )
        .mean()
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
        avg_gain /
        avg_loss.replace(
            0,
            np.nan
        )
    )

    df["RSI"] = (
        100 -
        (
            100 /
            (1 + rs)
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
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    df["ATR"] = (
        tr
        .ewm(
            alpha=1 / 14,
            adjust=False
        )
        .mean()
    )

    # --------------------------------------------------------
    # ADX
    # --------------------------------------------------------

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

    plus_dm = pd.Series(
        plus_dm,
        index=df.index
    )

    minus_dm = pd.Series(
        minus_dm,
        index=df.index
    )

    atr_safe = (
        df["ATR"]
        .replace(
            0,
            np.nan
        )
    )

    plus_di = (
        100 *
        plus_dm
        .ewm(
            alpha=1 / 14,
            adjust=False
        )
        .mean()
        /
        atr_safe
    )

    minus_di = (
        100 *
        minus_dm
        .ewm(
            alpha=1 / 14,
            adjust=False
        )
        .mean()
        /
        atr_safe
    )

    dx = (
        100 *
        (plus_di - minus_di).abs()
        /
        (
            plus_di +
            minus_di
        ).replace(
            0,
            np.nan
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

    # --------------------------------------------------------
    # Candle body
    # --------------------------------------------------------

    candle_range = (
        high - low
    ).replace(
        0,
        np.nan
    )

    df["BODY_RATIO"] = (
        (
            close -
            df["Open"]
        ).abs()
        /
        candle_range
    )

    # --------------------------------------------------------
    # Close position
    # --------------------------------------------------------

    df["CLOSE_POSITION"] = (
        (
            close -
            low
        )
        /
        candle_range
    )

    return df


# ============================================================
# CONDITION PRINT
# ============================================================

def print_condition(
    name,
    value
):

    if value:
        status = "PASS"
    else:
        status = "FAIL"

    print(
        f"{name:<18}: {status}"
    )


# ============================================================
# FIND ENTRY SIGNAL
# ============================================================

def find_entry_signal(
    df_1h,
    df_15m,
    df_5m
):

    if (
        df_1h.empty or
        df_15m.empty or
        df_5m.empty
    ):

        print(
            "ERROR: insufficient market data."
        )

        return None

    df_1h = add_indicators(
        df_1h
    )

    df_15m = add_indicators(
        df_15m
    )

    df_5m = add_indicators(
        df_5m
    )

    if len(df_1h) < 60:

        print(
            "ERROR: not enough 1H data."
        )

        return None

    if len(df_15m) < 60:

        print(
            "ERROR: not enough 15M data."
        )

        return None

    if len(df_5m) < 30:

        print(
            "ERROR: not enough 5M data."
        )

        return None

    # --------------------------------------------------------
    # Latest candles
    # --------------------------------------------------------

    h1 = df_1h.iloc[-1]

    p = df_15m.iloc[-2]

    c = df_15m.iloc[-1]

    # --------------------------------------------------------
    # Current values
    # --------------------------------------------------------

    price = float(
        c["Close"]
    )

    ema20 = float(
        c["EMA20"]
    )

    rsi = float(
        c["RSI"]
    )

    adx = float(
        c["ADX"]
    )

    atr = float(
        c["ATR"]
    )

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
        f"RSI   : {rsi:.2f}"
    )

    print(
        f"ADX   : {adx:.2f}"
    )

    print(
        f"ATR   : {atr:.2f}"
    )

    # --------------------------------------------------------
    # 1H trend
    # --------------------------------------------------------

    h1_bull = (
        h1["Close"] >
        h1["EMA20"]
        and
        h1["EMA20"] >
        h1["EMA50"]
        and
        h1["EMA20"] >
        df_1h["EMA20"].iloc[-2]
        and
        h1["ADX"] >=
        MIN_ADX_1H
    )

    h1_bear = (
        h1["Close"] <
        h1["EMA20"]
        and
        h1["EMA20"] <
        h1["EMA50"]
        and
        h1["EMA20"] <
        df_1h["EMA20"].iloc[-2]
        and
        h1["ADX"] >=
        MIN_ADX_1H
    )

    # ========================================================
    # LONG
    # ========================================================

    long_pullback = (
        p["Low"] <=
        p["EMA20"] +
        p["ATR"] * 0.35
    )

    long_hold = (
        p["Close"] >=
        p["EMA20"] -
        p["ATR"] * 0.25
    )

    long_reversal = (
        c["Close"] >
        c["Open"]
        and
        c["BODY_RATIO"] >=
        0.45
        and
        c["CLOSE_POSITION"] >=
        0.65
    )

    long_breakout = (
        c["Close"] >
        p["High"]
    )

    long_rsi = (
        LONG_RSI_MIN <=
        rsi <=
        LONG_RSI_MAX
    )

    long_adx = (
        adx >=
        MIN_ADX_15M
    )

    long_distance = (
        abs(
            price - ema20
        )
        <=
        atr *
        MAX_ENTRY_DISTANCE_ATR
    )

    # ========================================================
    # SHORT
    # ========================================================

    short_pullback = (
        p["High"] >=
        p["EMA20"] -
        p["ATR"] * 0.35
    )

    short_hold = (
        p["Close"] <=
        p["EMA20"] +
        p["ATR"] * 0.25
    )

    short_reversal = (
        c["Close"] <
        c["Open"]
        and
        c["BODY_RATIO"] >=
        0.45
        and
        c["CLOSE_POSITION"] <=
        0.35
    )

    short_breakout = (
        c["Close"] <
        p["Low"]
    )

    short_rsi = (
        SHORT_RSI_MIN <=
        rsi <=
        SHORT_RSI_MAX
    )

    short_adx = (
        adx >=
        MIN_ADX_15M
    )

    short_distance = (
        abs(
            price - ema20
        )
        <=
        atr *
        MAX_ENTRY_DISTANCE_ATR
    )

    # ========================================================
    # LONG DIAGNOSTIC
    # ========================================================

    print("")
    print("====================================")
    print(" LONG CHECK")
    print("====================================")

    print_condition(
        "1H Trend",
        h1_bull
    )

    print_condition(
        "Pullback",
        long_pullback
    )

    print_condition(
        "EMA Hold",
        long_hold
    )

    print_condition(
        "Reversal",
        long_reversal
    )

    print_condition(
        "Breakout",
        long_breakout
    )

    print_condition(
        "RSI",
        long_rsi
    )

    print_condition(
        "ADX",
        long_adx
    )

    print_condition(
        "EMA Distance",
        long_distance
    )

    # ========================================================
    # SHORT DIAGNOSTIC
    # ========================================================

    print("")
    print("====================================")
    print(" SHORT CHECK")
    print("====================================")

    print_condition(
        "1H Trend",
        h1_bear
    )

    print_condition(
        "Pullback",
        short_pullback
    )

    print_condition(
        "EMA Hold",
        short_hold
    )

    print_condition(
        "Reversal",
        short_reversal
    )

    print_condition(
        "Breakdown",
        short_breakout
    )

    print_condition(
        "RSI",
        short_rsi
    )

    print_condition(
        "ADX",
        short_adx
    )

    print_condition(
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
        and short_breakout
        and short_rsi
        and short_adx
        and short_distance
    )

    # ========================================================
    # NO SIGNAL
    # ========================================================

    if (
        not long_condition
        and
        not short_condition
    ):

        print("")
        print("====================================")
        print(" NO VALID SIGNAL")
        print("====================================")
        print(
            "Waiting for next setup..."
        )

        return None

    # ========================================================
    # DIRECTION
    # ========================================================

    if long_condition:

        direction = "LONG"

        entry = price

        recent_low = float(
            df_15m["Low"]
            .iloc[-4:-1]
            .min()
        )

        sl = (
            recent_low -
            atr * 0.25
        )

        reason = (
            "1H bullish trend → "
            "15M EMA20 pullback → "
            "bullish reversal → "
            "previous high breakout"
        )

    else:

        direction = "SHORT"

        entry = price

        recent_high = float(
            df_15m["High"]
            .iloc[-4:-1]
            .max()
        )

        sl = (
            recent_high +
            atr * 0.25
        )

        reason = (
            "1H bearish trend → "
            "15M EMA20 pullback → "
            "bearish reversal → "
            "previous low breakdown"
        )

    # ========================================================
    # RISK
    # ========================================================

    risk = abs(
        entry - sl
    )

    if risk <= 0:

        print(
            "Signal rejected: invalid risk."
        )

        return None

    if risk > (
        atr *
        MAX_SL_ATR
    ):

        print(
            "Signal rejected: "
            "SL distance too wide."
        )

        return None

    # ========================================================
    # TARGETS
    # ========================================================

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

    # ========================================================
    # 5M MOVEMENT FILTER
    # ========================================================

    recent_5m = float(
        df_5m["Close"].iloc[-1]
    )

    if abs(
        recent_5m - entry
    ) > (
        atr *
        MAX_SIGNAL_MOVE_ATR
    ):

        print(
            "Signal rejected: "
            "price moved too far."
        )

        return None

    # ========================================================
    # SIGNAL TIME
    # ========================================================

    signal_time = normalize_timestamp(
        df_15m.index[-1]
    )

    entry_time = (
        signal_time +
        pd.Timedelta(
            minutes=15
        )
    )

    # ========================================================
    # SIGNAL OBJECT
    # ========================================================

    signal = {

        "status": "active",

        "direction": direction,

        "entry": float(entry),

        "sl": float(sl),

        "tp1": float(tp1),

        "tp2": float(tp2),

        "tp3": float(tp3),

        "risk": round(
            float(risk),
            2
        ),

        "rsi": round(
            float(rsi),
            2
        ),

        "adx": round(
            float(adx),
            2
        ),

        "atr": round(
            float(atr),
            2
        ),

        "reason": reason,

        "signal_time":
            signal_time.isoformat(),

        "entry_time":
            entry_time.isoformat(),

        "tp1_hit": False,

        "tp2_hit": False,

        "tp3_hit": False,

        "entry_alert_sent": False,

        "created_at":
            pd.Timestamp.utcnow()
            .isoformat()
    }

    # ========================================================
    # SIGNAL FOUND
    # ========================================================

    print("")
    print("====================================")
    print(" SIGNAL FOUND")
    print("====================================")

    print(
        f"Direction : {direction}"
    )

    print(
        f"Entry     : ${entry:,.2f}"
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
        f"RSI       : {rsi:.2f}"
    )

    print(
        f"ADX       : {adx:.2f}"
    )

    print(
        f"ATR       : {atr:.2f}"
    )

    print(
        f"Risk      : {risk:.2f}"
    )

    return signal


# ============================================================
# ENTRY TELEGRAM
# ============================================================

def send_entry_alert(state):

    direction = state["direction"]

    if direction == "LONG":

        direction_text = "🟢 LONG"

    else:

        direction_text = "🔴 SHORT"

    message = f"""
<b>👑 [골드 선물 스마트 타점]</b>
━━━━━━━━━━━━━━━━━━

📊 방향: {direction_text}

💰 진입가: <b>${state["entry"]:,.2f}</b>

🛡 <b>손절가</b>
${state["sl"]:,.2f}

🎯 <b>목표가</b>
TP1: ${state["tp1"]:,.2f} (1.2R)
TP2: ${state["tp2"]:,.2f} (2.0R)
TP3: ${state["tp3"]:,.2f} (3.0R)

📐 <b>타점 근거</b>
{state["reason"]}

📊 RSI: {state["rsi"]:.2f}
📊 ADX: {state["adx"]:.2f}
📏 ATR: {state["atr"]:.2f}
⚠️ 위험폭: {state["risk"]:.2f}

⏱ 신호봉:
{format_signal_time(state["signal_time"])}

🔗 <a href="https://www.tradingview.com/symbols/COMEX-GC1!/">TradingView 골드 차트</a>

⚡ 추세 → 눌림 → 반전 → 돌파 확인 방식
"""

    return send_telegram(
        message
    )


# ============================================================
# TP TELEGRAM
# ============================================================

def send_tp_alert(
    state,
    number
):

    price = state[
        f"tp{number}"
    ]

    message = f"""
<b>🎯 골드 선물 TP{number} 도달</b>
━━━━━━━━━━━━━━━━━━

📊 방향: {state["direction"]}

💰 진입가:
${state["entry"]:,.2f}

🎯 TP{number}:
<b>${price:,.2f}</b>
"""

    if number == 2:

        message += """
        
🛡 손절가 → 진입가 이동
⚡ BE 적용
"""

    if number == 3:

        message += """
        
🏁 최종 목표 TP3 도달
"""

    return send_telegram(
        message
    )


# ============================================================
# SL TELEGRAM
# ============================================================

def send_sl_alert(state):

    message = f"""
<b>🛑 골드 선물 손절</b>
━━━━━━━━━━━━━━━━━━

📊 방향: {state["direction"]}

💰 진입가:
${state["entry"]:,.2f}

🛑 SL:
<b>${state["sl"]:,.2f}</b>

📉 포지션 종료
"""

    return send_telegram(
        message
    )


# ============================================================
# CLOSE POSITION
# ============================================================

def close_position(
    state,
    reason
):

    message = f"""
<b>🔒 골드 선물 포지션 종료</b>
━━━━━━━━━━━━━━━━━━

📊 방향: {state["direction"]}

💰 진입가:
${state["entry"]:,.2f}

📌 종료 사유:
{reason}

⏳ {COOLDOWN_MINUTES}분 쿨다운
"""

    send_telegram(
        message
    )

    cooldown_until = (
        pd.Timestamp.utcnow()
        +
        pd.Timedelta(
            minutes=COOLDOWN_MINUTES
        )
    )

    save_state(
        {
            "status": "cooldown",
            "cooldown_until":
                cooldown_until.isoformat()
        }
    )


# ============================================================
# MONITOR POSITION
# ============================================================

def monitor_position(
    state,
    df_1m
):

    if df_1m.empty:
        return state

    entry_time = normalize_timestamp(
        state["entry_time"]
    )

    bars = df_1m[
        df_1m.index >= entry_time
    ]

    if bars.empty:
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

    for timestamp, row in bars.iterrows():

        high = float(
            row["High"]
        )

        low = float(
            row["Low"]
        )

        # ----------------------------------------------------
        # LONG
        # ----------------------------------------------------

        if direction == "LONG":

            if low <= sl:

                send_sl_alert(
                    state
                )

                close_position(
                    state,
                    "Stop Loss"
                )

                return None

            if (
                not state["tp1_hit"]
                and
                high >= tp1
            ):

                send_tp_alert(
                    state,
                    1
                )

                state["tp1_hit"] = True

                save_state(
                    state
                )

            if (
                not state["tp2_hit"]
                and
                high >= tp2
            ):

                send_tp_alert(
                    state,
                    2
                )

                state["tp2_hit"] = True

                state["sl"] = entry

                save_state(
                    state
                )

            if (
                not state["tp3_hit"]
                and
                high >= tp3
            ):

                send_tp_alert(
                    state,
                    3
                )

                state["tp3_hit"] = True

                save_state(
                    state
                )

                close_position(
                    state,
                    "TP3 reached"
                )

                return None

        # ----------------------------------------------------
        # SHORT
        # ----------------------------------------------------

        else:

            if high >= sl:

                send_sl_alert(
                    state
                )

                close_position(
                    state,
                    "Stop Loss"
                )

                return None

            if (
                not state["tp1_hit"]
                and
                low <= tp1
            ):

                send_tp_alert(
                    state,
                    1
                )

                state["tp1_hit"] = True

                save_state(
                    state
                )

            if (
                not state["tp2_hit"]
                and
                low <= tp2
            ):

                send_tp_alert(
                    state,
                    2
                )

                state["tp2_hit"] = True

                state["sl"] = entry

                save_state(
                    state
                )

            if (
                not state["tp3_hit"]
                and
                low <= tp3
            ):

                send_tp_alert(
                    state,
                    3
                )

                state["tp3_hit"] = True

                save_state(
                    state
                )

                close_position(
                    state,
                    "TP3 reached"
                )

                return None

    return state


# ============================================================
# COOLDOWN
# ============================================================

def is_cooldown_active(state):

    if state.get(
        "status"
    ) != "cooldown":

        return False

    value = state.get(
        "cooldown_until"
    )

    if not value:
        return False

    try:

        now = pd.Timestamp.utcnow()

        until = normalize_timestamp(
            value
        )

        return now < until

    except Exception:

        return False


# ============================================================
# MAIN
# ============================================================

def main():

    print("====================================")
    print(" GOLD FUTURES SMART SIGNAL BOT")
    print("====================================")

    state = load_state()

    (
        df_1h,
        df_15m,
        df_5m,
        df_1m
    ) = get_history()

    if (
        df_1h.empty or
        df_15m.empty or
        df_5m.empty or
        df_1m.empty
    ):

        print(
            "ERROR: Market data unavailable."
        )

        return

    # Remove current incomplete candles

    df_1h = remove_incomplete_bar(
        df_1h
    )

    df_15m = remove_incomplete_bar(
        df_15m
    )

    df_5m = remove_incomplete_bar(
        df_5m
    )

    df_1m = remove_incomplete_bar(
        df_1m
    )

    # ========================================================
    # ACTIVE POSITION
    # ========================================================

    if state.get(
        "status"
    ) == "active":

        print(
            "Active position detected."
        )

        result = monitor_position(
            state,
            df_1m
        )

        if result is not None:

            save_state(
                result
            )

        return

    # ========================================================
    # COOLDOWN
    # ========================================================

    if is_cooldown_active(
        state
    ):

        print(
            "Cooldown active."
        )

        return

    # ========================================================
    # FIND NEW SIGNAL
    # ========================================================

    signal = find_entry_signal(
        df_1h,
        df_15m,
        df_5m
    )

    if signal is None:

        return

    # ========================================================
    # SAVE SIGNAL
    # ========================================================

    save_state(
        signal
    )

    log_event(
        "ENTRY_SIGNAL",
        signal
    )

    # ========================================================
    # TELEGRAM
    # ========================================================

    success = send_entry_alert(
        signal
    )

    if success:

        signal[
            "entry_alert_sent"
        ] = True

        save_state(
            signal
        )

        print(
            "Entry alert sent successfully."
        )

    else:

        print(
            "Entry alert failed."
        )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
