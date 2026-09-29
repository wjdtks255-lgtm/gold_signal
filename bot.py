import os
import json
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import yfinance as yf
import requests


# ============================================================
# GOLD FUTURES SMART SIGNAL BOT V7
# M15 LEAD + 5M SUPPORT
# H1 CONTEXT ONLY
# ============================================================

TICKER = "GC=F"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

STATE_FILE = "signal_state.json"
LOG_FILE = "bot_log.json"

KST = timezone(timedelta(hours=9))


# ============================================================
# SETTINGS
# ============================================================

# M15
MIN_M15_SCORE = 4

# Very strong M15 setup
STRONG_M15_SCORE = 5

# RSI
LONG_RSI_MIN = 52
LONG_RSI_MAX = 70

SHORT_RSI_MIN = 30
SHORT_RSI_MAX = 48

# ADX
MIN_ADX = 13

# 5M
# 5M no longer has to PASS.
# Only strong opposite movement blocks the signal.
MIN_5M_SUPPORT = 1

# Entry distance
MAX_ENTRY_DISTANCE_ATR = 1.60

# Candle size
MAX_SIGNAL_MOVE_ATR = 1.00

# Cooldown
COOLDOWN_MINUTES = 60

# Target
TP1_R = 1.20
TP2_R = 2.00
TP3_R = 3.00

# Stop
SWING_LOOKBACK = 8
SL_ATR_BUFFER = 0.35

# Risk
MIN_RISK_ATR = 0.55
MAX_RISK_ATR = 2.50


# ============================================================
# BASIC
# ============================================================

def now_kst():
    return datetime.now(KST)


def safe_float(v, default=0.0):
    try:
        if pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default


# ============================================================
# TELEGRAM
# ============================================================

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
        "text": message,
        "parse_mode": "HTML"
    }

    try:

        r = requests.post(
            url,
            json=payload,
            timeout=15
        )

        if r.status_code == 200:
            print("Telegram sent.")
            return True

        print("Telegram error:", r.text)
        return False

    except Exception as e:
        print("Telegram exception:", e)
        return False


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
            return json.load(f)

    except Exception:
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
            indent=2,
            default=str
        )


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


def save_log(data):

    with open(
        LOG_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data[-300:],
            f,
            ensure_ascii=False,
            indent=2,
            default=str
        )


def add_log(event, data=None):

    logs = load_log()

    logs.append({
        "time": now_kst().isoformat(),
        "event": event,
        "data": data or {}
    })

    save_log(logs)


# ============================================================
# DOWNLOAD
# ============================================================

def download_data():

    print("Downloading market data...")

    result = {}

    configs = {
        "1h": ("30d", "1h"),
        "15m": ("10d", "15m"),
        "5m": ("5d", "5m"),
        "1m": ("2d", "1m")
    }

    for name, (period, interval) in configs.items():

        try:

            df = yf.download(
                TICKER,
                period=period,
                interval=interval,
                auto_adjust=False,
                progress=False
            )

            if df is None or df.empty:
                print(f"{name}: NO DATA")
                continue

            if isinstance(
                df.columns,
                pd.MultiIndex
            ):
                df.columns = (
                    df.columns
                    .get_level_values(0)
                )

            needed = [
                "Open",
                "High",
                "Low",
                "Close"
            ]

            if not all(
                col in df.columns
                for col in needed
            ):
                print(
                    f"{name}: missing columns"
                )
                continue

            df = df.dropna(
                subset=needed
            ).copy()

            print(
                f"{name}: {len(df)} candles"
            )

            result[name] = df

        except Exception as e:

            print(
                f"{name} error:",
                e
            )

    return result


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
        avg_gain /
        avg_loss.replace(
            0,
            np.nan
        )
    )

    df["RSI"] = (
        100 -
        (100 / (1 + rs))
    )

    # ATR
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

    df["ATR"] = tr.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    # ADX
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where(
            (up_move > down_move)
            & (up_move > 0),
            up_move,
            0
        ),
        index=df.index
    )

    minus_dm = pd.Series(
        np.where(
            (down_move > up_move)
            & (down_move > 0),
            down_move,
            0
        ),
        index=df.index
    )

    plus_di = (
        100 *
        plus_dm.ewm(
            alpha=1 / 14,
            adjust=False
        ).mean()
        /
        df["ATR"].replace(
            0,
            np.nan
        )
    )

    minus_di = (
        100 *
        minus_dm.ewm(
            alpha=1 / 14,
            adjust=False
        ).mean()
        /
        df["ATR"].replace(
            0,
            np.nan
        )
    )

    dx = (
        100 *
        (plus_di - minus_di).abs()
        /
        (
            plus_di + minus_di
        ).replace(
            0,
            np.nan
        )
    )

    df["ADX"] = dx.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    # Candle
    df["Body"] = (
        close - df["Open"]
    ).abs()

    df["Range"] = (
        high - low
    ).replace(
        0,
        np.nan
    )

    df["BodyRatio"] = (
        df["Body"] /
        df["Range"]
    )

    df["Bull"] = (
        close > df["Open"]
    )

    df["Bear"] = (
        close < df["Open"]
    )

    return df


# ============================================================
# CLOSED CANDLE
# ============================================================

def closed_candles(df):

    if len(df) < 5:
        return None, None

    return (
        df.iloc[-2],
        df.iloc[-3]
    )


# ============================================================
# M15 LONG SCORE
# ============================================================

def long_score(c, p):

    close = safe_float(c["Close"])
    ema20 = safe_float(c["EMA20"])
    ema50 = safe_float(c["EMA50"])
    rsi = safe_float(c["RSI"])
    adx = safe_float(c["ADX"])

    prev_high = safe_float(
        p["High"]
    )

    score = 0

    # 1
    if close > ema20:
        score += 1

    # 2
    if ema20 > ema50:
        score += 1

    # 3
    if bool(c["Bull"]):
        score += 1

    # 4
    if close > prev_high:
        score += 1

    # 5
    if (
        LONG_RSI_MIN
        <= rsi
        <= LONG_RSI_MAX
    ):
        score += 1

    # 6
    if adx >= MIN_ADX:
        score += 1

    return score


# ============================================================
# M15 SHORT SCORE
# ============================================================

def short_score(c, p):

    close = safe_float(c["Close"])
    ema20 = safe_float(c["EMA20"])
    ema50 = safe_float(c["EMA50"])
    rsi = safe_float(c["RSI"])
    adx = safe_float(c["ADX"])

    prev_low = safe_float(
        p["Low"]
    )

    score = 0

    # 1
    if close < ema20:
        score += 1

    # 2
    if ema20 < ema50:
        score += 1

    # 3
    if bool(c["Bear"]):
        score += 1

    # 4
    if close < prev_low:
        score += 1

    # 5
    if (
        SHORT_RSI_MIN
        <= rsi
        <= SHORT_RSI_MAX
    ):
        score += 1

    # 6
    if adx >= MIN_ADX:
        score += 1

    return score


# ============================================================
# 5M DIRECTION
# ============================================================

def five_min_state(df):

    if len(df) < 10:
        return {
            "long_score": 0,
            "short_score": 0,
            "long": False,
            "short": False
        }

    c = df.iloc[-2]
    p = df.iloc[-3]

    close = safe_float(c["Close"])
    ema20 = safe_float(c["EMA20"])
    ema50 = safe_float(c["EMA50"])

    p_close = safe_float(
        p["Close"]
    )

    long_points = 0
    short_points = 0

    # LONG
    if close > ema20:
        long_points += 1

    if ema20 > ema50:
        long_points += 1

    if close > p_close:
        long_points += 1

    # SHORT
    if close < ema20:
        short_points += 1

    if ema20 < ema50:
        short_points += 1

    if close < p_close:
        short_points += 1

    return {
        "long_score": long_points,
        "short_score": short_points,

        "long": long_points >= 2,

        "short": short_points >= 2
    }


# ============================================================
# H1 CONTEXT
# ============================================================

def h1_context(df):

    if len(df) < 10:

        return {
            "bull": False,
            "bear": False
        }

    c = df.iloc[-2]

    close = safe_float(
        c["Close"]
    )

    ema20 = safe_float(
        c["EMA20"]
    )

    ema50 = safe_float(
        c["EMA50"]
    )

    return {
        "bull": (
            close > ema20
            and ema20 > ema50
        ),

        "bear": (
            close < ema20
            and ema20 < ema50
        )
    }


# ============================================================
# ENTRY FILTERS
# ============================================================

def distance_ok(
    entry,
    ema20,
    atr
):

    if atr <= 0:
        return False

    return (
        abs(entry - ema20)
        / atr
        <= MAX_ENTRY_DISTANCE_ATR
    )


def candle_ok(c):

    atr = safe_float(
        c["ATR"]
    )

    body = safe_float(
        c["Body"]
    )

    if atr <= 0:
        return False

    return (
        body / atr
        <= MAX_SIGNAL_MOVE_ATR
    )


# ============================================================
# V7 CORE LOGIC
#
# IMPORTANT:
#
# M15 = PRIMARY
# 5M = SUPPORT
#
# 5M does NOT have to PASS.
#
# Only strong 5M opposition blocks.
# ============================================================

def decide_direction(
    m15_long,
    m15_short,
    m5
):

    # Strong M15 LONG
    if m15_long >= STRONG_M15_SCORE:

        # 5M strongly bearish
        if m5["short_score"] >= 3:
            return None, "5M strong bearish block"

        return "LONG", "M15 STRONG"

    # Strong M15 SHORT
    if m15_short >= STRONG_M15_SCORE:

        # 5M strongly bullish
        if m5["long_score"] >= 3:
            return None, "5M strong bullish block"

        return "SHORT", "M15 STRONG"

    # Normal LONG
    if m15_long >= MIN_M15_SCORE:

        if m5["short_score"] >= 3:
            return None, "5M strong bearish block"

        return "LONG", "M15 NORMAL"

    # Normal SHORT
    if m15_short >= MIN_M15_SCORE:

        if m5["long_score"] >= 3:
            return None, "5M strong bullish block"

        return "SHORT", "M15 NORMAL"

    return None, "M15 score insufficient"


# ============================================================
# STOP LOSS
# ============================================================

def long_sl(
    m15,
    entry
):

    recent = m15.iloc[
        -(SWING_LOOKBACK + 2):-2
    ]

    if recent.empty:
        return None

    swing_low = safe_float(
        recent["Low"].min()
    )

    atr = safe_float(
        m15.iloc[-2]["ATR"]
    )

    sl = (
        swing_low
        - atr * SL_ATR_BUFFER
    )

    if sl >= entry:
        return None

    risk = entry - sl

    risk_atr = (
        risk / atr
        if atr > 0
        else 999
    )

    if (
        risk_atr < MIN_RISK_ATR
        or risk_atr > MAX_RISK_ATR
    ):
        return None

    return sl


def short_sl(
    m15,
    entry
):

    recent = m15.iloc[
        -(SWING_LOOKBACK + 2):-2
    ]

    if recent.empty:
        return None

    swing_high = safe_float(
        recent["High"].max()
    )

    atr = safe_float(
        m15.iloc[-2]["ATR"]
    )

    sl = (
        swing_high
        + atr * SL_ATR_BUFFER
    )

    if sl <= entry:
        return None

    risk = sl - entry

    risk_atr = (
        risk / atr
        if atr > 0
        else 999
    )

    if (
        risk_atr < MIN_RISK_ATR
        or risk_atr > MAX_RISK_ATR
    ):
        return None

    return sl


# ============================================================
# BUILD SIGNAL
# ============================================================

def build_signal(data):

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
        len(h1) < 20
        or len(m15) < 30
        or len(m5) < 20
    ):
        print("Not enough data.")
        return None

    c, p = closed_candles(m15)

    if c is None:
        return None

    entry = safe_float(
        c["Close"]
    )

    ema20 = safe_float(
        c["EMA20"]
    )

    ema50 = safe_float(
        c["EMA50"]
    )

    rsi = safe_float(
        c["RSI"]
    )

    adx = safe_float(
        c["ADX"]
    )

    atr = safe_float(
        c["ATR"]
    )

    ls = long_score(
        c,
        p
    )

    ss = short_score(
        c,
        p
    )

    m5_state = five_min_state(
        m5
    )

    h1_state = h1_context(
        h1
    )

    d_ok = distance_ok(
        entry,
        ema20,
        atr
    )

    c_ok = candle_ok(c)

    print()
    print("====================================")
    print(" CURRENT MARKET CHECK")
    print("====================================")
    print(f"M15 Close : ${entry:,.2f}")
    print(f"EMA20     : ${ema20:,.2f}")
    print(f"EMA50     : ${ema50:,.2f}")
    print(f"RSI       : {rsi:.2f}")
    print(f"ADX       : {adx:.2f}")
    print(f"ATR       : {atr:.2f}")

    print()
    print("====================================")
    print(" LONG CHECK")
    print("====================================")
    print(f"M15 Score : {ls}/6")
    print(
        f"5M Score  : "
        f"{m5_state['long_score']}/3"
    )
    print(
        f"H1       : "
        f"{'BULL' if h1_state['bull'] else 'NOT BULL'}"
    )

    print()
    print("====================================")
    print(" SHORT CHECK")
    print("====================================")
    print(f"M15 Score : {ss}/6")
    print(
        f"5M Score  : "
        f"{m5_state['short_score']}/3"
    )
    print(
        f"H1       : "
        f"{'BEAR' if h1_state['bear'] else 'NOT BEAR'}"
    )

    print()
    print("====================================")
    print(" FILTER")
    print("====================================")
    print(
        f"Distance : "
        f"{'PASS' if d_ok else 'FAIL'}"
    )
    print(
        f"Candle   : "
        f"{'PASS' if c_ok else 'FAIL'}"
    )

    # --------------------------------------------------------
    # Direction
    # --------------------------------------------------------

    direction, mode = decide_direction(
        ls,
        ss,
        m5_state
    )

    if direction is None:

        print()
        print(
            f"No valid signal: {mode}"
        )

        return None

    # --------------------------------------------------------
    # Common filters
    # --------------------------------------------------------

    if not d_ok:

        print(
            "Signal rejected: "
            "price too far from EMA20."
        )

        return None

    if not c_ok:

        print(
            "Signal rejected: "
            "candle movement too large."
        )

        return None

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        if not (
            LONG_RSI_MIN
            <= rsi
            <= LONG_RSI_MAX
        ):

            print(
                "LONG rejected: RSI."
            )

            return None

        if adx < MIN_ADX:

            print(
                "LONG rejected: ADX."
            )

            return None

        sl = long_sl(
            m15,
            entry
        )

        if sl is None:

            print(
                "LONG rejected: SL risk."
            )

            return None

        risk = entry - sl

        tp1 = (
            entry
            + risk * TP1_R
        )

        tp2 = (
            entry
            + risk * TP2_R
        )

        tp3 = (
            entry
            + risk * TP3_R
        )

        if h1_state["bull"]:
            h1_text = "BULL"
        else:
            h1_text = "NOT BULL"

        return {
            "direction": "LONG",
            "mode": mode,
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "risk": risk,
            "m15_score": ls,
            "m5_score": m5_state[
                "long_score"
            ],
            "h1_context": h1_text,
            "signal_time":
                m15.index[-2].isoformat()
        }

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if direction == "SHORT":

        if not (
            SHORT_RSI_MIN
            <= rsi
            <= SHORT_RSI_MAX
        ):

            print(
                "SHORT rejected: RSI."
            )

            return None

        if adx < MIN_ADX:

            print(
                "SHORT rejected: ADX."
            )

            return None

        sl = short_sl(
            m15,
            entry
        )

        if sl is None:

            print(
                "SHORT rejected: SL risk."
            )

            return None

        risk = sl - entry

        tp1 = (
            entry
            - risk * TP1_R
        )

        tp2 = (
            entry
            - risk * TP2_R
        )

        tp3 = (
            entry
            - risk * TP3_R
        )

        if h1_state["bear"]:
            h1_text = "BEAR"
        else:
            h1_text = "NOT BEAR"

        return {
            "direction": "SHORT",
            "mode": mode,
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "risk": risk,
            "m15_score": ss,
            "m5_score": m5_state[
                "short_score"
            ],
            "h1_context": h1_text,
            "signal_time":
                m15.index[-2].isoformat()
        }

    return None


# ============================================================
# COOLDOWN
# ============================================================

def cooldown_active(state):

    last_exit = state.get(
        "last_exit_time"
    )

    if not last_exit:
        return False

    try:

        dt = datetime.fromisoformat(
            last_exit
        )

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=KST
            )

        elapsed = (
            datetime.now(KST) - dt
        ).total_seconds() / 60

        if elapsed < COOLDOWN_MINUTES:

            remaining = (
                COOLDOWN_MINUTES
                - elapsed
            )

            print(
                f"Cooldown active: "
                f"{remaining:.1f} min"
            )

            return True

    except Exception:
        pass

    return False


# ============================================================
# DUPLICATE
# ============================================================

def duplicate_signal(
    state,
    signal
):

    previous = state.get(
        "last_signal_time"
    )

    if not previous:
        return False

    return (
        previous
        == signal["signal_time"]
    )


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def signal_message(signal):

    if signal["direction"] == "LONG":
        emoji = "🟢"
        title = "LONG SIGNAL"
    else:
        emoji = "🔴"
        title = "SHORT SIGNAL"

    return (
        f"{emoji} <b>{title}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"🥇 금 선물 (GC=F)\n\n"
        f"진입가     ${signal['entry']:.2f}\n"
        f"손절가     ${signal['sl']:.2f}\n"
        f"TP1        ${signal['tp1']:.2f}\n"
        f"TP2        ${signal['tp2']:.2f}\n"
        f"TP3        ${signal['tp3']:.2f}\n\n"
        f"📊 M15 점수  "
        f"{signal['m15_score']}/6\n"
        f"📊 5M 점수   "
        f"{signal['m5_score']}/3\n"
        f"📊 H1        "
        f"{signal['h1_context']}\n\n"
        f"⚡ {signal['mode']}\n\n"
        f"━━━━━━━━━━━━━━━━━━"
    )


# ============================================================
# SAVE POSITION
# ============================================================

def save_position(signal):

    state = {
        "active": True,

        "direction":
            signal["direction"],

        "mode":
            signal["mode"],

        "entry":
            signal["entry"],

        "sl":
            signal["sl"],

        "original_sl":
            signal["sl"],

        "tp1":
            signal["tp1"],

        "tp2":
            signal["tp2"],

        "tp3":
            signal["tp3"],

        "risk":
            signal["risk"],

        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,

        "entry_alert_sent":
            False,

        "entry_time":
            now_kst().isoformat(),

        "signal_time":
            signal["signal_time"],

        "last_signal_time":
            signal["signal_time"]
    }

    save_state(state)

    return state


# ============================================================
# TP / EXIT
# ============================================================

def pnl_pct(
    direction,
    entry,
    exit_price
):

    if entry <= 0:
        return 0

    if direction == "LONG":

        return (
            (exit_price - entry)
            / entry
        ) * 100

    return (
        (entry - exit_price)
        / entry
    ) * 100


def exit_message(
    state,
    title,
    price
):

    pnl = pnl_pct(
        state["direction"],
        state["entry"],
        price
    )

    emoji = (
        "🔴"
        if title == "STOP LOSS"
        else "🔵"
    )

    final_text = (
        "🛑 손절 처리 완료"
        if title == "STOP LOSS"
        else "🎯 익절 처리 완료"
    )

    return (
        f"{emoji} <b>{title}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"🥇 금 선물 (GC=F)\n\n"
        f"진입가     ${state['entry']:.2f}\n"
        f"청산가     ${price:.2f}\n"
        f"손익률     {pnl:+.2f}%\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{final_text}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )


def tp_message(
    state,
    tp_name,
    price
):

    return (
        f"🎯 <b>{tp_name} HIT</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"🥇 금 선물 (GC=F)\n\n"
        f"진입가     ${state['entry']:.2f}\n"
        f"현재가     ${price:.2f}\n\n"
        f"📌 {tp_name} 도달\n"
        f"━━━━━━━━━━━━━━━━━━"
    )


# ============================================================
# MONITOR
# ============================================================

def monitor_position(
    data,
    state
):

    if not state.get("active"):
        return False

    df = data.get("1m")

    if df is None or len(df) < 5:

        print(
            "1m data unavailable."
        )

        return True

    df = add_indicators(df)

    c = df.iloc[-2]

    high = safe_float(
        c["High"]
    )

    low = safe_float(
        c["Low"]
    )

    close = safe_float(
        c["Close"]
    )

    direction = state["direction"]

    entry = safe_float(
        state["entry"]
    )

    sl = safe_float(
        state["sl"]
    )

    tp1 = safe_float(
        state["tp1"]
    )

    tp2 = safe_float(
        state["tp2"]
    )

    tp3 = safe_float(
        state["tp3"]
    )

    print()
    print("====================================")
    print(" ACTIVE POSITION")
    print("====================================")
    print(f"Direction : {direction}")
    print(f"Entry     : ${entry:.2f}")
    print(f"SL        : ${sl:.2f}")
    print(f"TP1       : ${tp1:.2f}")
    print(f"TP2       : ${tp2:.2f}")
    print(f"TP3       : ${tp3:.2f}")
    print(f"1M Close  : ${close:.2f}")

    # --------------------------------------------------------
    # STOP
    # --------------------------------------------------------

    if direction == "LONG":

        if low <= sl:

            send_telegram(
                exit_message(
                    state,
                    "STOP LOSS",
                    sl
                )
            )

            state["active"] = False
            state["last_exit_time"] = (
                now_kst().isoformat()
            )

            save_state(state)

            add_log(
                "STOP_LOSS",
                {
                    "direction":
                        direction,
                    "entry":
                        entry,
                    "exit":
                        sl
                }
            )

            return False

    else:

        if high >= sl:

            send_telegram(
                exit_message(
                    state,
                    "STOP LOSS",
                    sl
                )
            )

            state["active"] = False
            state["last_exit_time"] = (
                now_kst().isoformat()
            )

            save_state(state)

            add_log(
                "STOP_LOSS",
                {
                    "direction":
                        direction,
                    "entry":
                        entry,
                    "exit":
                        sl
                }
            )

            return False

    # --------------------------------------------------------
    # TP1
    # --------------------------------------------------------

    if not state["tp1_hit"]:

        hit = (
            high >= tp1
            if direction == "LONG"
            else low <= tp1
        )

        if hit:

            send_telegram(
                tp_message(
                    state,
                    "TP1",
                    tp1
                )
            )

            state["tp1_hit"] = True

            save_state(state)

    # --------------------------------------------------------
    # TP2
    # --------------------------------------------------------

    if (
        state["tp1_hit"]
        and not state["tp2_hit"]
    ):

        hit = (
            high >= tp2
            if direction == "LONG"
            else low <= tp2
        )

        if hit:

            send_telegram(
                tp_message(
                    state,
                    "TP2",
                    tp2
                )
            )

            state["tp2_hit"] = True

            # Move SL to entry
            state["sl"] = entry

            save_state(state)

    # --------------------------------------------------------
    # TP3
    # --------------------------------------------------------

    if (
        state["tp2_hit"]
        and not state["tp3_hit"]
    ):

        hit = (
            high >= tp3
            if direction == "LONG"
            else low <= tp3
        )

        if hit:

            send_telegram(
                tp_message(
                    state,
                    "TP3",
                    tp3
                )
            )

            send_telegram(
                exit_message(
                    state,
                    "TAKE PROFIT",
                    tp3
                )
            )

            state["tp3_hit"] = True
            state["active"] = False

            state["last_exit_time"] = (
                now_kst().isoformat()
            )

            save_state(state)

            add_log(
                "TP3",
                {
                    "direction":
                        direction,
                    "entry":
                        entry,
                    "exit":
                        tp3
                }
            )

            return False

    save_state(state)

    return True


# ============================================================
# MAIN
# ============================================================

def main():

    print("=================================")
    print(" GOLD FUTURES SMART SIGNAL BOT")
    print(" BALANCED V7")
    print(" M15 LEAD + 5M SUPPORT")
    print(" H1 CONTEXT ONLY")
    print("=================================")

    data = download_data()

    required = [
        "1h",
        "15m",
        "5m",
        "1m"
    ]

    missing = [
        x
        for x in required
        if x not in data
    ]

    if missing:

        print(
            "Missing data:",
            missing
        )

        return

    state = load_state()

    # ========================================================
    # ACTIVE POSITION
    # ========================================================

    if state.get("active"):

        print(
            "Active position detected."
        )

        monitor_position(
            data,
            state
        )

        return

    # ========================================================
    # COOLDOWN
    # ========================================================

    if cooldown_active(state):

        print(
            "No new signal during cooldown."
        )

        return

    # ========================================================
    # NEW SIGNAL
    # ========================================================

    signal = build_signal(data)

    if signal is None:
        return

    # ========================================================
    # DUPLICATE
    # ========================================================

    if duplicate_signal(
        state,
        signal
    ):

        print(
            "Duplicate signal blocked."
        )

        return

    # ========================================================
    # SAVE
    # ========================================================

    new_state = save_position(
        signal
    )

    # ========================================================
    # TELEGRAM
    # ========================================================

    sent = send_telegram(
        signal_message(signal)
    )

    if sent:

        new_state[
            "entry_alert_sent"
        ] = True

        save_state(
            new_state
        )

    add_log(
        "NEW_SIGNAL",
        {
            "direction":
                signal["direction"],
            "mode":
                signal["mode"],
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
            "m15_score":
                signal["m15_score"],
            "m5_score":
                signal["m5_score"]
        }
    )

    print()
    print("====================================")
    print(" NEW SIGNAL")
    print("====================================")
    print(
        f"Direction : "
        f"{signal['direction']}"
    )
    print(
        f"Mode      : "
        f"{signal['mode']}"
    )
    print(
        f"Entry     : "
        f"${signal['entry']:.2f}"
    )
    print(
        f"SL        : "
        f"${signal['sl']:.2f}"
    )
    print(
        f"TP1       : "
        f"${signal['tp1']:.2f}"
    )
    print(
        f"TP2       : "
        f"${signal['tp2']:.2f}"
    )
    print(
        f"TP3       : "
        f"${signal['tp3']:.2f}"
    )
    print("====================================")


if __name__ == "__main__":
    main()
