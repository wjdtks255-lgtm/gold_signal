import os
import json
from datetime import datetime, timezone, timedelta

import yfinance as yf
import pandas as pd
import numpy as np
import requests


# ============================================================
# GOLD FUTURES SMART SIGNAL BOT V11.2.1
# ============================================================

print("====================================")
print(" 금 선물 스마트 시그널 봇")
print(" BALANCED V11.2.1")
print(" SIGNAL -> PULLBACK -> ACTIVE")
print(" M15 LEAD + 5M SUPPORT")
print(" H1 CONTEXT ONLY")
print(" COMPLETED CANDLE MODE")
print("====================================")


# ============================================================
# SETTINGS
# ============================================================

TICKER = "GC=F"

STATE_FILE = "signal_state.json"
LOG_FILE = "bot_log.json"

STATE_VERSION = "11.2.1"


# ------------------------------------------------------------
# Signal
# ------------------------------------------------------------

MIN_M15_SCORE = 4
STRONG_M15_SCORE = 5

MIN_5M_SCORE = 2

LONG_RSI_MIN = 52
LONG_RSI_MAX = 70

SHORT_RSI_MIN = 30
SHORT_RSI_MAX = 48

MIN_ADX = 13


# ------------------------------------------------------------
# Distance
# ------------------------------------------------------------

MAX_ENTRY_DISTANCE_ATR = 2.0
MAX_SIGNAL_MOVE_ATR = 1.35


# ------------------------------------------------------------
# Pullback
# ------------------------------------------------------------

PULLBACK_ATR = 0.45

MAX_PENDING_DISTANCE_ATR = 1.50

PENDING_TIMEOUT_MINUTES = 90


# ------------------------------------------------------------
# Risk
# ------------------------------------------------------------

RISK_ATR_MULT = 1.35

ATR_RISK_MIN = 0.55
ATR_RISK_MAX = 2.50


# ------------------------------------------------------------
# Targets
# ------------------------------------------------------------

TP1_R = 1.20
TP2_R = 2.00
TP3_R = 3.00


# ============================================================
# TIME
# ============================================================

KST = timezone(timedelta(hours=9))


def now_kst():
    return datetime.now(KST)


def now_iso():
    return now_kst().isoformat()


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_TOKEN = (
    os.getenv("TELEGRAM_TOKEN")
    or os.getenv("TELEGRAM_BOT_TOKEN")
)

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def send_telegram(message):

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing.")
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
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
            print("Telegram sent successfully.")
            return True

        print("Telegram error:", response.text)

    except Exception as e:

        print("Telegram exception:", e)

    return False


# ============================================================
# STATE
# ============================================================

def default_state():

    return {
        "version": STATE_VERSION,

        "status": "NONE",

        "direction": None,

        "signal_price": None,

        "pullback_low": None,
        "pullback_high": None,

        "pullback_touched": False,

        "signal_time": None,
        "pending_time": None,

        "entry": None,
        "sl": None,

        "tp1": None,
        "tp2": None,
        "tp3": None,

        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,

        "pending_alert_sent": False,
        "entry_alert_sent": False,

        "last_update": None
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

        # ----------------------------------------------------
        # Reset old versions ONCE
        # ----------------------------------------------------

        if data.get("version") != STATE_VERSION:

            print("")
            print("====================================")
            print(" OLD STATE RESET")
            print("====================================")
            print(
                "기존 V10/V11/V11.2 상태를 초기화합니다."
            )
            print(
                "현재 시장을 새롭게 분석합니다."
            )
            print("")

            return default_state()

        state = default_state()
        state.update(data)

        return state

    except Exception as e:

        print("State load error:", e)
        return default_state()


def save_state(state):

    state["version"] = STATE_VERSION
    state["last_update"] = now_iso()

    try:

        with open(
            STATE_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                state,
                f,
                indent=2,
                ensure_ascii=False
            )

    except Exception as e:

        print("State save error:", e)


# ============================================================
# LOG
# ============================================================

def save_log(event, data=None):

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

    logs.append({
        "time": now_iso(),
        "event": event,
        "data": data or {}
    })

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
                indent=2,
                ensure_ascii=False
            )

    except Exception as e:

        print("Log save error:", e)


# ============================================================
# DOWNLOAD DATA
# ============================================================

def download_data():

    print("")
    print("Downloading market data...")

    data = {}

    settings = {
        "1h": ("1h", "730d"),
        "15m": ("15m", "60d"),
        "5m": ("5m", "60d"),
        "1m": ("1m", "7d")
    }

    for name, values in settings.items():

        interval, period = values

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

            df = df.dropna()

            data[name] = df

            print(
                f"{name}: "
                f"{len(df)} candles"
            )

        except Exception as e:

            print(
                f"{name}: ERROR -> {e}"
            )

    return data


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

    rs = (
        avg_gain
        /
        avg_loss.replace(
            0,
            np.nan
        )
    )

    return 100 - (
        100 /
        (1 + rs)
    )


def atr(df, length=14):

    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    previous_close = close.shift(1)

    tr1 = high - low
    tr2 = (
        high - previous_close
    ).abs()

    tr3 = (
        low - previous_close
    ).abs()

    true_range = pd.concat(
        [
            tr1,
            tr2,
            tr3
        ],
        axis=1
    ).max(axis=1)

    return true_range.ewm(
        alpha=1 / length,
        adjust=False
    ).mean()


def adx(df, length=14):

    high = df["High"]
    low = df["Low"]
    close = df["Close"]

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

    previous_close = close.shift(1)

    true_range = pd.concat(
        [
            high - low,
            (
                high - previous_close
            ).abs(),
            (
                low - previous_close
            ).abs()
        ],
        axis=1
    ).max(axis=1)

    atr_value = true_range.ewm(
        alpha=1 / length,
        adjust=False
    ).mean()

    plus_di = (
        100
        * plus_dm.ewm(
            alpha=1 / length,
            adjust=False
        ).mean()
        / atr_value
    )

    minus_di = (
        100
        * minus_dm.ewm(
            alpha=1 / length,
            adjust=False
        ).mean()
        / atr_value
    )

    denominator = (
        plus_di + minus_di
    ).replace(0, np.nan)

    dx = (
        100
        * (plus_di - minus_di).abs()
        / denominator
    )

    return dx.ewm(
        alpha=1 / length,
        adjust=False
    ).mean()


# ============================================================
# COMPLETED CANDLE
# ============================================================

def completed_df(df):

    if len(df) < 5:
        return df.copy()

    # Last candle may still be forming.
    return df.iloc[:-1].copy()


# ============================================================
# MARKET ANALYSIS
# ============================================================

def analyze_market(data):

    m15 = completed_df(
        data["15m"]
    )

    m5 = completed_df(
        data["5m"]
    )

    h1 = completed_df(
        data["1h"]
    )

    # ========================================================
    # M15
    # ========================================================

    m15["EMA20"] = ema(
        m15["Close"],
        20
    )

    m15["EMA50"] = ema(
        m15["Close"],
        50
    )

    m15["RSI"] = rsi(
        m15["Close"]
    )

    m15["ADX"] = adx(m15)

    m15["ATR"] = atr(m15)

    last15 = m15.iloc[-1]
    previous15 = m15.iloc[-2]

    close15 = float(last15["Close"])
    ema20_15 = float(last15["EMA20"])
    ema50_15 = float(last15["EMA50"])
    rsi15 = float(last15["RSI"])
    adx15 = float(last15["ADX"])
    atr15 = float(last15["ATR"])

    previous_close15 = float(
        previous15["Close"]
    )

    # ========================================================
    # 5M
    # ========================================================

    m5["EMA20"] = ema(
        m5["Close"],
        20
    )

    m5["EMA50"] = ema(
        m5["Close"],
        50
    )

    m5["RSI"] = rsi(
        m5["Close"]
    )

    last5 = m5.iloc[-1]

    close5 = float(last5["Close"])
    ema20_5 = float(last5["EMA20"])
    ema50_5 = float(last5["EMA50"])
    rsi5 = float(last5["RSI"])

    # ========================================================
    # H1
    # ========================================================

    h1["EMA20"] = ema(
        h1["Close"],
        20
    )

    h1["EMA50"] = ema(
        h1["Close"],
        50
    )

    last1h = h1.iloc[-1]

    close1h = float(last1h["Close"])
    ema20_1h = float(last1h["EMA20"])
    ema50_1h = float(last1h["EMA50"])

    # ========================================================
    # M15 SCORE
    # ========================================================

    long_score = 0
    short_score = 0

    # Price / EMA20

    if close15 > ema20_15:
        long_score += 1

    if close15 < ema20_15:
        short_score += 1

    # EMA20 / EMA50

    if ema20_15 > ema50_15:
        long_score += 1

    if ema20_15 < ema50_15:
        short_score += 1

    # RSI

    if (
        LONG_RSI_MIN
        <= rsi15
        <= LONG_RSI_MAX
    ):
        long_score += 1

    if (
        SHORT_RSI_MIN
        <= rsi15
        <= SHORT_RSI_MAX
    ):
        short_score += 1

    # ADX

    if adx15 >= MIN_ADX:

        if close15 > ema20_15:
            long_score += 1

        if close15 < ema20_15:
            short_score += 1

    # Candle direction

    if close15 > previous_close15:
        long_score += 1

    if close15 < previous_close15:
        short_score += 1

    # Momentum

    candle_move = abs(
        close15 - previous_close15
    )

    if candle_move <= (
        atr15 * MAX_SIGNAL_MOVE_ATR
    ):

        if close15 > previous_close15:
            long_score += 1

        if close15 < previous_close15:
            short_score += 1

    long_score = min(
        long_score,
        6
    )

    short_score = min(
        short_score,
        6
    )

    # ========================================================
    # 5M SCORE
    # ========================================================

    long_5_score = 0
    short_5_score = 0

    if close5 > ema20_5:
        long_5_score += 1

    if ema20_5 > ema50_5:
        long_5_score += 1

    if rsi5 >= 50:
        long_5_score += 1

    if close5 < ema20_5:
        short_5_score += 1

    if ema20_5 < ema50_5:
        short_5_score += 1

    if rsi5 <= 50:
        short_5_score += 1

    # ========================================================
    # H1
    # ========================================================

    h1_bull = (
        close1h > ema20_1h
        and ema20_1h > ema50_1h
    )

    h1_bear = (
        close1h < ema20_1h
        and ema20_1h < ema50_1h
    )

    # ========================================================
    # PRINT
    # ========================================================

    print("")
    print("====================================")
    print(" CURRENT MARKET CHECK")
    print("====================================")

    print(
        f"M15 Close : ${close15:,.2f}"
    )

    print(
        f"EMA20     : ${ema20_15:,.2f}"
    )

    print(
        f"EMA50     : ${ema50_15:,.2f}"
    )

    print(
        f"RSI       : {rsi15:.2f}"
    )

    print(
        f"ADX       : {adx15:.2f}"
    )

    print(
        f"ATR       : {atr15:.2f}"
    )

    print("")
    print("====================================")
    print(" LONG CHECK")
    print("====================================")

    print(
        f"M15 Score : {long_score}/6"
    )

    print(
        f"5M Score  : {long_5_score}/3"
    )

    print(
        "H1        : "
        + (
            "BULL"
            if h1_bull
            else "NOT BULL"
        )
    )

    print("")
    print("====================================")
    print(" SHORT CHECK")
    print("====================================")

    print(
        f"M15 Score : {short_score}/6"
    )

    print(
        f"5M Score  : {short_5_score}/3"
    )

    print(
        "H1        : "
        + (
            "BEAR"
            if h1_bear
            else "NOT BEAR"
        )
    )

    # ========================================================
    # SIGNAL QUALITY
    #
    # M15 5/6 = strong
    #
    # M15 4/6 requires 5M >= 2/3
    # ========================================================

    long_quality = (
        long_score >= STRONG_M15_SCORE
        or (
            long_score >= MIN_M15_SCORE
            and long_5_score >= MIN_5M_SCORE
        )
    )

    short_quality = (
        short_score >= STRONG_M15_SCORE
        or (
            short_score >= MIN_M15_SCORE
            and short_5_score >= MIN_5M_SCORE
        )
    )

    # RSI filter

    if not (
        LONG_RSI_MIN
        <= rsi15
        <= LONG_RSI_MAX
    ):
        long_quality = False

    if not (
        SHORT_RSI_MIN
        <= rsi15
        <= SHORT_RSI_MAX
    ):
        short_quality = False

    # Strong opposite 5M

    if (
        long_quality
        and short_5_score >= 3
        and long_5_score == 0
    ):
        long_quality = False

    if (
        short_quality
        and long_5_score >= 3
        and short_5_score == 0
    ):
        short_quality = False

    direction = None

    if long_quality and short_quality:

        if long_score > short_score:
            direction = "LONG"

        elif short_score > long_score:
            direction = "SHORT"

    elif long_quality:

        direction = "LONG"

    elif short_quality:

        direction = "SHORT"

    # ========================================================
    # DISTANCE
    # ========================================================

    if direction:

        distance_atr = (
            abs(
                close15 - ema20_15
            )
            / atr15
            if atr15 > 0
            else 999
        )

        print("")
        print("====================================")
        print(" SIGNAL FILTER")
        print("====================================")

        print(
            f"Distance : "
            f"{distance_atr:.2f} ATR"
        )

        if distance_atr > MAX_ENTRY_DISTANCE_ATR:

            print("Distance : FAIL")
            direction = None

        else:

            print("Distance : PASS")

    return {
        "direction": direction,
        "signal_price": close15,
        "atr": atr15,
        "long_score": long_score,
        "short_score": short_score,
        "long_5_score": long_5_score,
        "short_5_score": short_5_score,
        "h1_bull": h1_bull,
        "h1_bear": h1_bear
    }


# ============================================================
# CREATE PULLBACK SIGNAL
# ============================================================

def create_pending(
    state,
    analysis
):

    direction = analysis["direction"]
    signal_price = analysis["signal_price"]
    atr_value = analysis["atr"]

    pullback_size = (
        atr_value * PULLBACK_ATR
    )

    # --------------------------------------------------------
    # LONG
    #
    # Price must actually move DOWN from signal.
    # --------------------------------------------------------

    if direction == "LONG":

        pullback_high = (
            signal_price
            - atr_value * 0.10
        )

        pullback_low = (
            signal_price
            - pullback_size
        )

    # --------------------------------------------------------
    # SHORT
    #
    # Price must actually move UP from signal.
    # --------------------------------------------------------

    else:

        pullback_low = (
            signal_price
            + atr_value * 0.10
        )

        pullback_high = (
            signal_price
            + pullback_size
        )

    state["status"] = "PENDING"

    state["direction"] = direction

    state["signal_price"] = signal_price

    state["pullback_low"] = pullback_low
    state["pullback_high"] = pullback_high

    state["pullback_touched"] = False

    state["signal_time"] = now_iso()
    state["pending_time"] = now_iso()

    state["pending_alert_sent"] = False

    save_state(state)

    print("")
    print("====================================")
    print(" NEW PULLBACK SIGNAL")
    print("====================================")

    print(
        f"Direction    : {direction}"
    )

    print(
        f"Signal Price : "
        f"${signal_price:,.2f}"
    )

    print(
        f"Pullback Zone: "
        f"${pullback_low:,.2f}"
        f" ~ "
        f"${pullback_high:,.2f}"
    )

    message = (
        "🥇 금 선물 스마트 시그널\n\n"
        "🟡 풀백 진입 대기\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 방향 : "
        f"{'롱' if direction == 'LONG' else '숏'}\n"
        f"📍 신호 가격 : "
        f"${signal_price:,.2f}\n"
        f"📉 풀백 구간 : "
        f"${pullback_low:,.2f}"
        f" ~ "
        f"${pullback_high:,.2f}\n\n"
        "⚠️ 아직 실제 진입이 아닙니다.\n"
        "먼저 풀백이 발생해야 합니다.\n"
        "풀백 후 방향을 재확인합니다.\n\n"
        "⚙️ 전략 : M15 주도 + 5M 확인"
    )

    if send_telegram(message):

        state["pending_alert_sent"] = True

        save_state(state)

    save_log(
        "PULLBACK_SIGNAL",
        {
            "direction": direction,
            "signal_price": signal_price,
            "pullback_low": pullback_low,
            "pullback_high": pullback_high
        }
    )


# ============================================================
# CONFIRM ENTRY
# ============================================================

def confirm_entry(
    state,
    current_price,
    atr_value
):

    direction = state["direction"]

    risk = (
        atr_value * RISK_ATR_MULT
    )

    risk = max(
        risk,
        atr_value * ATR_RISK_MIN
    )

    risk = min(
        risk,
        atr_value * ATR_RISK_MAX
    )

    entry = current_price

    if direction == "LONG":

        sl = entry - risk

        tp1 = entry + (
            risk * TP1_R
        )

        tp2 = entry + (
            risk * TP2_R
        )

        tp3 = entry + (
            risk * TP3_R
        )

    else:

        sl = entry + risk

        tp1 = entry - (
            risk * TP1_R
        )

        tp2 = entry - (
            risk * TP2_R
        )

        tp3 = entry - (
            risk * TP3_R
        )

    state["status"] = "ACTIVE"

    state["entry"] = entry
    state["sl"] = sl

    state["tp1"] = tp1
    state["tp2"] = tp2
    state["tp3"] = tp3

    state["tp1_hit"] = False
    state["tp2_hit"] = False
    state["tp3_hit"] = False

    state["entry_alert_sent"] = False

    save_state(state)

    print("")
    print("====================================")
    print(" ACTUAL ENTRY CONFIRMED")
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

    message = (
        "🥇 금 선물 스마트 시그널\n\n"
        +
        (
            "🟢 롱 진입 확정"
            if direction == "LONG"
            else "🔴 숏 진입 확정"
        )
        +
        "\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 방향 : "
        f"{'롱' if direction == 'LONG' else '숏'}\n"
        f"💰 실제 진입가 : "
        f"${entry:,.2f}\n"
        f"🛑 손절가 : "
        f"${sl:,.2f}\n"
        f"🎯 익절 1 : "
        f"${tp1:,.2f}\n"
        f"🎯 익절 2 : "
        f"${tp2:,.2f}\n"
        f"🎯 익절 3 : "
        f"${tp3:,.2f}\n\n"
        "⚙️ 전략 : M15 주도 + 5M 확인\n"
        "✅ 풀백 발생 + 방향 재확인"
    )

    if send_telegram(message):

        state["entry_alert_sent"] = True

        save_state(state)

    save_log(
        "ENTRY_CONFIRMED",
        {
            "direction": direction,
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3
        }
    )


# ============================================================
# MONITOR PENDING
# ============================================================

def monitor_pending(
    state,
    data
):

    direction = state["direction"]

    m1 = data["1m"]

    m5 = completed_df(
        data["5m"]
    )

    m15 = completed_df(
        data["15m"]
    )

    # Current price
    current_price = float(
        m1["Close"].iloc[-1]
    )

    # ATR
    atr_value = float(
        atr(m15).iloc[-1]
    )

    signal_price = float(
        state["signal_price"]
    )

    pullback_low = float(
        state["pullback_low"]
    )

    pullback_high = float(
        state["pullback_high"]
    )

    # ========================================================
    # TIMEOUT
    # ========================================================

    pending_time = state.get(
        "pending_time"
    )

    if pending_time:

        try:

            created = datetime.fromisoformat(
                pending_time
            )

            age_minutes = (
                now_kst() - created
            ).total_seconds() / 60

            if (
                age_minutes
                > PENDING_TIMEOUT_MINUTES
            ):

                print("")
                print("====================================")
                print(" PENDING TIMEOUT")
                print("====================================")

                print(
                    "풀백 대기 시간이 만료되었습니다."
                )

                send_telegram(
                    "🥇 금 선물 스마트 시그널\n\n"
                    "⚪ 풀백 진입 대기 취소\n"
                    "━━━━━━━━━━━━━━━━━━\n\n"
                    "정해진 시간 안에 유효한 "
                    "풀백이 발생하지 않았습니다."
                )

                save_log(
                    "PENDING_TIMEOUT"
                )

                state.clear()
                state.update(
                    default_state()
                )

                save_state(state)

                return

        except Exception:
            pass

    # ========================================================
    # 5M INDICATORS
    # ========================================================

    m5["EMA20"] = ema(
        m5["Close"],
        20
    )

    m5["EMA50"] = ema(
        m5["Close"],
        50
    )

    m5["RSI"] = rsi(
        m5["Close"]
    )

    last5 = m5.iloc[-1]

    close5 = float(
        last5["Close"]
    )

    ema20_5 = float(
        last5["EMA20"]
    )

    ema50_5 = float(
        last5["EMA50"]
    )

    rsi5 = float(
        last5["RSI"]
    )

    long_5_score = 0
    short_5_score = 0

    if close5 > ema20_5:
        long_5_score += 1

    if ema20_5 > ema50_5:
        long_5_score += 1

    if rsi5 >= 50:
        long_5_score += 1

    if close5 < ema20_5:
        short_5_score += 1

    if ema20_5 < ema50_5:
        short_5_score += 1

    if rsi5 <= 50:
        short_5_score += 1

    # ========================================================
    # PRINT
    # ========================================================

    print("")
    print("====================================")
    print(" PULLBACK PENDING")
    print("====================================")

    print(
        f"Direction    : {direction}"
    )

    print(
        f"Signal Price : "
        f"${signal_price:,.2f}"
    )

    print(
        f"Pullback Zone: "
        f"${pullback_low:,.2f}"
        f" ~ "
        f"${pullback_high:,.2f}"
    )

    print(
        f"Current      : "
        f"${current_price:,.2f}"
    )

    print(
        f"5M Long      : "
        f"{long_5_score}/3"
    )

    print(
        f"5M Short     : "
        f"{short_5_score}/3"
    )

    # ========================================================
    # DISTANCE
    # ========================================================

    if atr_value > 0:

        distance_atr = (
            abs(
                current_price
                -
                signal_price
            )
            /
            atr_value
        )

    else:

        distance_atr = 0

    if (
        distance_atr
        >
        MAX_PENDING_DISTANCE_ATR
    ):

        print("")
        print(
            "PENDING CANCELLED"
        )

        print(
            "가격이 신호에서 너무 멀어졌습니다."
        )

        send_telegram(
            "🥇 금 선물 스마트 시그널\n\n"
            "⚪ 풀백 진입 대기 취소\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "가격이 신호에서 너무 멀어져\n"
            "기존 대기 신호를 취소했습니다."
        )

        save_log(
            "PENDING_CANCELLED_DISTANCE"
        )

        state.clear()
        state.update(
            default_state()
        )

        save_state(state)

        return

    # ========================================================
    # STEP 1
    # WAIT FOR REAL PULLBACK
    # ========================================================

    if not state["pullback_touched"]:

        if direction == "LONG":

            touched = (
                current_price
                <= pullback_high
                and
                current_price
                >= pullback_low
            )

        else:

            touched = (
                current_price
                >= pullback_low
                and
                current_price
                <= pullback_high
            )

        if touched:

            state["pullback_touched"] = True

            save_state(state)

            print("")
            print("====================================")
            print(" PULLBACK TOUCHED")
            print("====================================")

            print(
                "실제 풀백이 발생했습니다."
            )

            print(
                "5M 방향 재확인을 기다립니다."
            )

            send_telegram(
                "🥇 금 선물 스마트 시그널\n\n"
                "🟠 풀백 구간 도달\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                f"📌 방향 : "
                f"{'롱' if direction == 'LONG' else '숏'}\n"
                f"💰 현재가 : "
                f"${current_price:,.2f}\n\n"
                "풀백이 확인되었습니다.\n"
                "이제 5M 방향 재확인을 기다립니다."
            )

            return

        print("")
        print(
            "Waiting for real pullback..."
        )

        return

    # ========================================================
    # STEP 2
    # ENTRY CONFIRMATION
    # ========================================================

    print("")
    print("====================================")
    print(" WAITING FOR ENTRY CONFIRM")
    print("====================================")

    if direction == "LONG":

        confirm = (
            current_price > ema20_5
            and
            long_5_score >= MIN_5M_SCORE
        )

        print(
            f"5M EMA20 : "
            f"${ema20_5:,.2f}"
        )

        print(
            f"5M Long  : "
            f"{long_5_score}/3"
        )

    else:

        confirm = (
            current_price < ema20_5
            and
            short_5_score >= MIN_5M_SCORE
        )

        print(
            f"5M EMA20 : "
            f"${ema20_5:,.2f}"
        )

        print(
            f"5M Short : "
            f"{short_5_score}/3"
        )

    if confirm:

        print("")
        print(
            "ENTRY CONFIRMATION PASS"
        )

        confirm_entry(
            state,
            current_price,
            atr_value
        )

    else:

        print("")
        print(
            "ENTRY CONFIRMATION WAIT"
        )


# ============================================================
# ACTIVE POSITION
# ============================================================

def monitor_active(
    state,
    data
):

    current_price = float(
        data["1m"]["Close"].iloc[-1]
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

    print("")
    print("====================================")
    print(" ACTIVE POSITION")
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
        f"1M Close  : ${current_price:,.2f}"
    )

    # ========================================================
    # LONG
    # ========================================================

    if direction == "LONG":

        # ----------------------------------------------------
        # STOP LOSS
        # ----------------------------------------------------

        if current_price <= sl:

            pnl = (
                (current_price - entry)
                / entry
                * 100
            )

            send_telegram(
                "🥇 금 선물 스마트 시그널\n\n"
                "🔴 손절 처리\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 진입가 : "
                f"${entry:,.2f}\n"
                f"🛑 청산가 : "
                f"${current_price:,.2f}\n"
                f"📉 손익률 : "
                f"{pnl:.2f}%\n\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "🛑 손절 처리 완료"
            )

            save_log(
                "STOP_LOSS",
                {
                    "direction": direction,
                    "entry": entry,
                    "exit": current_price,
                    "pnl_percent": pnl
                }
            )

            state.clear()
            state.update(
                default_state()
            )

            save_state(state)

            return

        # ----------------------------------------------------
        # TP1
        # ----------------------------------------------------

        if (
            not state["tp1_hit"]
            and current_price >= tp1
        ):

            send_telegram(
                "🥇 금 선물 스마트 시그널\n\n"
                "🔵 익절 1 도달\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 진입가 : "
                f"${entry:,.2f}\n"
                f"🎯 익절가 : "
                f"${current_price:,.2f}"
            )

            state["tp1_hit"] = True

            save_state(state)

        # ----------------------------------------------------
        # TP2
        # ----------------------------------------------------

        if (
            not state["tp2_hit"]
            and current_price >= tp2
        ):

            send_telegram(
                "🥇 금 선물 스마트 시그널\n\n"
                "🔵 익절 2 도달\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 진입가 : "
                f"${entry:,.2f}\n"
                f"🎯 익절가 : "
                f"${current_price:,.2f}"
            )

            state["tp2_hit"] = True

            save_state(state)

        # ----------------------------------------------------
        # TP3
        # ----------------------------------------------------

        if (
            not state["tp3_hit"]
            and current_price >= tp3
        ):

            send_telegram(
                "🥇 금 선물 스마트 시그널\n\n"
                "🔵 익절 3 도달\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 진입가 : "
                f"${entry:,.2f}\n"
                f"🎯 최종 청산가 : "
                f"${current_price:,.2f}\n\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "✅ 거래 종료"
            )

            save_log(
                "TP3_COMPLETE",
                {
                    "direction": direction,
                    "entry": entry,
                    "exit": current_price
                }
            )

            state.clear()
            state.update(
                default_state()
            )

            save_state(state)

            return

    # ========================================================
    # SHORT
    # ========================================================

    else:

        # ----------------------------------------------------
        # STOP LOSS
        # ----------------------------------------------------

        if current_price >= sl:

            pnl = (
                (entry - current_price)
                / entry
                * 100
            )

            send_telegram(
                "🥇 금 선물 스마트 시그널\n\n"
                "🔴 손절 처리\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 진입가 : "
                f"${entry:,.2f}\n"
                f"🛑 청산가 : "
                f"${current_price:,.2f}\n"
                f"📉 손익률 : "
                f"{pnl:.2f}%\n\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "🛑 손절 처리 완료"
            )

            save_log(
                "STOP_LOSS",
                {
                    "direction": direction,
                    "entry": entry,
                    "exit": current_price,
                    "pnl_percent": pnl
                }
            )

            state.clear()
            state.update(
                default_state()
            )

            save_state(state)

            return

        # ----------------------------------------------------
        # TP1
        # ----------------------------------------------------

        if (
            not state["tp1_hit"]
            and current_price <= tp1
        ):

            send_telegram(
                "🥇 금 선물 스마트 시그널\n\n"
                "🔵 익절 1 도달\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 진입가 : "
                f"${entry:,.2f}\n"
                f"🎯 익절가 : "
                f"${current_price:,.2f}"
            )

            state["tp1_hit"] = True

            save_state(state)

        # ----------------------------------------------------
        # TP2
        # ----------------------------------------------------

        if (
            not state["tp2_hit"]
            and current_price <= tp2
        ):

            send_telegram(
                "🥇 금 선물 스마트 시그널\n\n"
                "🔵 익절 2 도달\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 진입가 : "
                f"${entry:,.2f}\n"
                f"🎯 익절가 : "
                f"${current_price:,.2f}"
            )

            state["tp2_hit"] = True

            save_state(state)

        # ----------------------------------------------------
        # TP3
        # ----------------------------------------------------

        if (
            not state["tp3_hit"]
            and current_price <= tp3
        ):

            send_telegram(
                "🥇 금 선물 스마트 시그널\n\n"
                "🔵 익절 3 도달\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 진입가 : "
                f"${entry:,.2f}\n"
                f"🎯 최종 청산가 : "
                f"${current_price:,.2f}\n\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "✅ 거래 종료"
            )

            save_log(
                "TP3_COMPLETE",
                {
                    "direction": direction,
                    "entry": entry,
                    "exit": current_price
                }
            )

            state.clear()
            state.update(
                default_state()
            )

            save_state(state)

            return

    print("")
    print(
        "Active position remains open."
    )


# ============================================================
# MAIN
# ============================================================

def main():

    state = load_state()

    data = download_data()

    required = [
        "1h",
        "15m",
        "5m",
        "1m"
    ]

    for key in required:

        if key not in data:

            print("")
            print(
                f"Missing market data: {key}"
            )

            return

    # ========================================================
    # ACTIVE
    # ========================================================

    if state["status"] == "ACTIVE":

        monitor_active(
            state,
            data
        )

        return

    # ========================================================
    # PENDING
    # ========================================================

    if state["status"] == "PENDING":

        monitor_pending(
            state,
            data
        )

        return

    # ========================================================
    # NEW SIGNAL
    # ========================================================

    analysis = analyze_market(
        data
    )

    direction = analysis["direction"]

    if direction is None:

        print("")
        print("====================================")
        print(" NO VALID SIGNAL")
        print("====================================")

        save_log(
            "NO_SIGNAL",
            {
                "long_score":
                    analysis["long_score"],

                "short_score":
                    analysis["short_score"],

                "long_5_score":
                    analysis["long_5_score"],

                "short_5_score":
                    analysis["short_5_score"]
            }
        )

        return

    # ========================================================
    # FRESH SIGNAL
    # ========================================================

    print("")
    print("====================================")
    print(" FRESH SIGNAL FOUND")
    print("====================================")

    print(
        f"Direction : {direction}"
    )

    create_pending(
        state,
        analysis
    )

    print("")
    print(
        "Waiting for real pullback..."
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        print("")
        print("====================================")
        print(" BOT ERROR")
        print("====================================")

        print(
            f"{type(e).__name__}: {e}"
        )

        save_log(
            "BOT_ERROR",
            {
                "error": str(e),
                "type": type(e).__name__
            }
        )
