# ============================================================
# GOLD FUTURES SMART SIGNAL BOT
# V14.0.0
#
# ONE POSITION ONLY
# ACTIVE LOCK + PERSISTENT STATE
# ============================================================

import os
import json
import math
import traceback
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests
import yfinance as yf


# ============================================================
# CONFIG
# ============================================================

VERSION = "14.0.0"
STATE_VERSION = VERSION

TICKER = "GC=F"

TV_LINK = "https://www.tradingview.com/symbols/GC1!/"

STATE_FILE = "signal_state.json"
LOG_FILE = "bot_log.json"

TELEGRAM_TOKEN = (
    os.getenv("TELEGRAM_TOKEN")
    or os.getenv("TELEGRAM_BOT_TOKEN")
)

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ============================================================
# SIGNAL SETTINGS
# ============================================================

MIN_M15_SCORE = 5
STRONG_M15_SCORE = 6

MIN_ADX = 15.0

# 신규 진입 간 기본 쿨다운
COOLDOWN_MINUTES = 45

# SL 발생 후 같은 방향 재진입 방지
SL_COOLDOWN_MINUTES = 120

# 종료 후 동일 조건 재진입 방지
EXIT_COOLDOWN_MINUTES = 15


# ============================================================
# RISK / TARGET SETTINGS
# ============================================================

ENTRY_RISK_ATR = 1.35

MIN_RISK_ATR = 0.55
MAX_RISK_ATR = 2.50

TP1_R = 1.20
TP2_R = 2.00
TP3_R = 3.00


# ============================================================
# DATA SETTINGS
# ============================================================

INTERVAL_1M = "1m"
INTERVAL_5M = "5m"
INTERVAL_15M = "15m"
INTERVAL_1H = "1h"

PERIOD_1M = "7d"
PERIOD_5M = "30d"
PERIOD_15M = "60d"
PERIOD_1H = "2y"


# ============================================================
# TIME
# ============================================================

KST = timezone(timedelta(hours=9))


def now_kst():
    return datetime.now(KST)


def now_iso():
    return now_kst().isoformat()


# ============================================================
# FORMAT
# ============================================================

def fmt_price(value):
    if value is None:
        return "-"
    return f"${float(value):,.2f}"


def fmt_num(value, digits=2):
    if value is None:
        return "-"
    return f"{float(value):.{digits}f}"


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram configuration missing.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=15
        )

        print("Telegram:", response.status_code)

        if response.status_code != 200:
            print(response.text)
            return False

        return True

    except Exception as e:
        print("Telegram error:", e)
        return False


# ============================================================
# STATE
# ============================================================

def default_state():
    return {
        "version": STATE_VERSION,

        # IDLE / ACTIVE
        "status": "IDLE",

        # LONG / SHORT
        "direction": None,

        # Position information
        "entry": None,
        "sl": None,
        "tp1": None,
        "tp2": None,
        "tp3": None,

        "risk": None,

        # Target tracking
        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,

        # Signal information
        "signal_time": None,
        "signal_id": None,

        "m15_score": None,
        "five_score": None,
        "rsi": None,
        "adx": None,

        # Exit information
        "last_exit_time": None,
        "last_exit_reason": None,
        "last_exit_direction": None,

        # Cooldowns
        "last_signal_time": None,
        "last_sl_time": None,
        "last_sl_direction": None,

        # Last processed price/time
        "last_monitor_time": None,
        "last_price": None,
    }


def load_state():
    state = default_state()

    if not os.path.exists(STATE_FILE):
        print("No state file found. Starting IDLE.")
        return state

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)

        # 기존 상태를 최대한 보존
        for key in state:
            if key in saved:
                state[key] = saved[key]

        print("====================================")
        print("STATE LOADED")
        print("Status    :", state.get("status"))
        print("Direction :", state.get("direction"))
        print("Entry     :", state.get("entry"))
        print("SL        :", state.get("sl"))
        print("TP3       :", state.get("tp3"))
        print("====================================")

        return state

    except Exception as e:
        print("State load error:", e)

        # 파일이 깨졌을 경우 안전하게 IDLE
        return default_state()


def save_state(state):
    try:
        temp_file = STATE_FILE + ".tmp"

        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(
                state,
                f,
                ensure_ascii=False,
                indent=2
            )

        os.replace(temp_file, STATE_FILE)

        print("State saved.")

    except Exception as e:
        print("State save error:", e)


# ============================================================
# LOG
# ============================================================

def append_log(event, state=None, price=None, extra=None):
    try:
        logs = []

        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    logs = json.load(f)

                if not isinstance(logs, list):
                    logs = []

            except Exception:
                logs = []

        item = {
            "time": now_iso(),
            "version": VERSION,
            "event": event,
            "price": price,
        }

        if state:
            item["status"] = state.get("status")
            item["direction"] = state.get("direction")
            item["entry"] = state.get("entry")
            item["sl"] = state.get("sl")
            item["tp1"] = state.get("tp1")
            item["tp2"] = state.get("tp2")
            item["tp3"] = state.get("tp3")

        if extra:
            item["extra"] = extra

        logs.append(item)

        # 로그가 지나치게 커지지 않도록 최근 500개만 유지
        logs = logs[-500:]

        temp_file = LOG_FILE + ".tmp"

        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(
                logs,
                f,
                ensure_ascii=False,
                indent=2
            )

        os.replace(temp_file, LOG_FILE)

    except Exception as e:
        print("Log error:", e)


# ============================================================
# DATA DOWNLOAD
# ============================================================

def download_data():
    print("====================================")
    print("DOWNLOADING MARKET DATA")
    print("====================================")

    data = {}

    try:
        print("Downloading 1M...")
        data["1m"] = yf.download(
            TICKER,
            period=PERIOD_1M,
            interval=INTERVAL_1M,
            progress=False,
            auto_adjust=False,
            threads=False
        )

        print("Downloading 5M...")
        data["5m"] = yf.download(
            TICKER,
            period=PERIOD_5M,
            interval=INTERVAL_5M,
            progress=False,
            auto_adjust=False,
            threads=False
        )

        print("Downloading 15M...")
        data["15m"] = yf.download(
            TICKER,
            period=PERIOD_15M,
            interval=INTERVAL_15M,
            progress=False,
            auto_adjust=False,
            threads=False
        )

        print("Downloading 1H...")
        data["1h"] = yf.download(
            TICKER,
            period=PERIOD_1H,
            interval=INTERVAL_1H,
            progress=False,
            auto_adjust=False,
            threads=False
        )

        return data

    except Exception as e:
        print("Data download error:", e)
        return data


# ============================================================
# CLEAN DATAFRAME
# ============================================================

def clean_df(df):
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()

    # yfinance MultiIndex 대응
    if isinstance(df.columns, pd.MultiIndex):
        try:
            df.columns = df.columns.get_level_values(0)
        except Exception:
            pass

    required = ["Open", "High", "Low", "Close"]

    for col in required:
        if col not in df.columns:
            return pd.DataFrame()

    df = df.dropna(subset=required)

    return df


# ============================================================
# COMPLETED CANDLES
# ============================================================

def completed(df):
    df = clean_df(df)

    if len(df) < 5:
        return df

    # 가장 최근 캔들은 아직 완성되지 않았을 가능성이 있으므로 제거
    return df.iloc[:-1].copy()


# ============================================================
# INDICATORS
# ============================================================

def add_indicators(df):
    df = df.copy()

    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    df["EMA20"] = close.ewm(
        span=20,
        adjust=False
    ).mean()

    df["EMA50"] = close.ewm(
        span=50,
        adjust=False
    ).mean()

    # RSI14
    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    df["RSI"] = 100 - (
        100 / (1 + rs)
    )

    # ATR14
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    df["ATR"] = tr.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    # ADX14
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where(
        (up_move > down_move) & (up_move > 0),
        up_move,
        0
    )

    minus_dm = np.where(
        (down_move > up_move) & (down_move > 0),
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

    atr = df["ATR"].replace(0, np.nan)

    plus_di = (
        100
        * plus_dm.ewm(
            alpha=1 / 14,
            adjust=False
        ).mean()
        / atr
    )

    minus_di = (
        100
        * minus_dm.ewm(
            alpha=1 / 14,
            adjust=False
        ).mean()
        / atr
    )

    dx = (
        100
        * (plus_di - minus_di).abs()
        / (plus_di + minus_di).replace(0, np.nan)
    )

    df["ADX"] = dx.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    return df


# ============================================================
# M15 ANALYSIS
# ============================================================

def analyze_m15(df):
    df = completed(df)

    if len(df) < 60:
        return None

    df = add_indicators(df)

    row = df.iloc[-1]
    prev = df.iloc[-2]

    close = float(row["Close"])
    prev_close = float(prev["Close"])

    ema20 = float(row["EMA20"])
    ema50 = float(row["EMA50"])

    prev_ema20 = float(prev["EMA20"])

    rsi = float(row["RSI"])
    adx = float(row["ADX"])
    atr = float(row["ATR"])

    long_score = 0
    short_score = 0

    # 1. Price vs EMA20
    if close > ema20:
        long_score += 1
    elif close < ema20:
        short_score += 1

    # 2. EMA20 vs EMA50
    if ema20 > ema50:
        long_score += 1
    elif ema20 < ema50:
        short_score += 1

    # 3. EMA20 direction
    if ema20 > prev_ema20:
        long_score += 1
    elif ema20 < prev_ema20:
        short_score += 1

    # 4. RSI
    if rsi >= 50:
        long_score += 1
    elif rsi <= 50:
        short_score += 1

    # 5. Candle direction
    if close > prev_close:
        long_score += 1
    elif close < prev_close:
        short_score += 1

    # 6. ADX trend strength
    if adx >= MIN_ADX:
        if long_score > short_score:
            long_score += 1
        elif short_score > long_score:
            short_score += 1

    long_score = min(long_score, 6)
    short_score = min(short_score, 6)

    return {
        "close": close,
        "ema20": ema20,
        "ema50": ema50,
        "rsi": rsi,
        "adx": adx,
        "atr": atr,
        "long_score": long_score,
        "short_score": short_score,
    }


# ============================================================
# 5M ANALYSIS
# ============================================================

def analyze_5m(df):
    df = completed(df)

    if len(df) < 60:
        return None

    df = add_indicators(df)

    row = df.iloc[-1]
    prev = df.iloc[-2]

    close = float(row["Close"])
    prev_close = float(prev["Close"])

    ema20 = float(row["EMA20"])

    rsi = float(row["RSI"])
    atr = float(row["ATR"])

    long_score = 0
    short_score = 0

    # 1. Price vs EMA20
    if close > ema20:
        long_score += 1
    elif close < ema20:
        short_score += 1

    # 2. Candle direction
    if close > prev_close:
        long_score += 1
    elif close < prev_close:
        short_score += 1

    # 3. RSI
    if rsi >= 50:
        long_score += 1
    elif rsi <= 50:
        short_score += 1

    long_score = min(long_score, 3)
    short_score = min(short_score, 3)

    return {
        "close": close,
        "ema20": ema20,
        "rsi": rsi,
        "atr": atr,
        "long_score": long_score,
        "short_score": short_score,
    }


# ============================================================
# H1 CONTEXT
# ============================================================

def analyze_h1(df):
    df = completed(df)

    if len(df) < 60:
        return None

    df = add_indicators(df)

    row = df.iloc[-1]

    close = float(row["Close"])
    ema20 = float(row["EMA20"])
    ema50 = float(row["EMA50"])

    if close > ema20 and ema20 > ema50:
        trend = "BULL"

    elif close < ema20 and ema20 < ema50:
        trend = "BEAR"

    else:
        trend = "NEUTRAL"

    return {
        "trend": trend,
        "close": close,
        "ema20": ema20,
        "ema50": ema50,
    }


# ============================================================
# CURRENT PRICE
# ============================================================

def get_current_price(df_1m):
    df = clean_df(df_1m)

    if df.empty:
        return None

    try:
        return float(df["Close"].iloc[-1])
    except Exception:
        return None


# ============================================================
# SIGNAL ID
# ============================================================

def make_signal_id(direction, m15, five):
    """
    동일한 시장 상태에서 반복 진입하는 것을 방지하기 위한 ID.
    """

    m15_close = round(m15["close"], 2)
    five_close = round(five["close"], 2)

    return (
        f"{direction}_"
        f"{m15_close}_"
        f"{five_close}_"
        f"{m15['long_score']}_"
        f"{m15['short_score']}_"
        f"{five['long_score']}_"
        f"{five['short_score']}"
    )


# ============================================================
# COOLDOWN CHECK
# ============================================================

def parse_time(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def minutes_since(value):
    dt = parse_time(value)

    if dt is None:
        return None

    return (
        now_kst() - dt
    ).total_seconds() / 60.0


def signal_allowed(state, direction):
    # ========================================================
    # ABSOLUTE ACTIVE LOCK
    # ========================================================

    if state.get("status") == "ACTIVE":
        print("ACTIVE LOCK -> NO NEW SIGNAL")
        return False

    # ========================================================
    # GENERAL COOLDOWN
    # ========================================================

    elapsed = minutes_since(
        state.get("last_signal_time")
    )

    if elapsed is not None:
        if elapsed < COOLDOWN_MINUTES:
            print(
                f"GENERAL COOLDOWN -> "
                f"{elapsed:.1f}/{COOLDOWN_MINUTES} min"
            )
            return False

    # ========================================================
    # EXIT COOLDOWN
    # ========================================================

    exit_elapsed = minutes_since(
        state.get("last_exit_time")
    )

    if exit_elapsed is not None:
        if exit_elapsed < EXIT_COOLDOWN_MINUTES:
            print(
                f"EXIT COOLDOWN -> "
                f"{exit_elapsed:.1f}/{EXIT_COOLDOWN_MINUTES} min"
            )
            return False

    # ========================================================
    # SL SAME DIRECTION COOLDOWN
    # ========================================================

    if (
        state.get("last_sl_direction") == direction
        and state.get("last_sl_time")
    ):
        sl_elapsed = minutes_since(
            state.get("last_sl_time")
        )

        if (
            sl_elapsed is not None
            and sl_elapsed < SL_COOLDOWN_MINUTES
        ):
            print(
                f"SL COOLDOWN -> "
                f"{sl_elapsed:.1f}/{SL_COOLDOWN_MINUTES} min"
            )
            return False

    return True


# ============================================================
# FIND SIGNAL
# ============================================================

def find_signal(m15, five, h1):
    if not m15 or not five:
        return None

    long_m15 = m15["long_score"]
    short_m15 = m15["short_score"]

    long_5m = five["long_score"]
    short_5m = five["short_score"]

    long_valid = (
        long_m15 >= 6
        or (
            long_m15 >= 5
            and long_5m >= 2
        )
    )

    short_valid = (
        short_m15 >= 6
        or (
            short_m15 >= 5
            and short_5m >= 2
        )
    )

    print("====================================")
    print("SIGNAL CHECK")
    print("====================================")

    print(
        f"M15 LONG  : {long_m15}/6"
    )
    print(
        f"M15 SHORT : {short_m15}/6"
    )

    print(
        f"5M LONG   : {long_5m}/3"
    )
    print(
        f"5M SHORT  : {short_5m}/3"
    )

    if h1:
        print(
            f"H1 TREND  : {h1['trend']}"
        )

    # 양쪽 동시에 유효하면 더 강한 M15 쪽
    if long_valid and short_valid:

        if long_m15 > short_m15:
            return "LONG"

        if short_m15 > long_m15:
            return "SHORT"

        print("Both sides equal -> NO SIGNAL")
        return None

    if long_valid:
        return "LONG"

    if short_valid:
        return "SHORT"

    return None


# ============================================================
# CALCULATE POSITION
# ============================================================

def calculate_position(
    direction,
    entry,
    five
):
    atr = float(five["atr"])

    risk = max(
        atr * MIN_RISK_ATR,
        min(
            atr * ENTRY_RISK_ATR,
            atr * MAX_RISK_ATR
        )
    )

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

    return {
        "entry": round(entry, 2),
        "risk": round(risk, 2),
        "sl": round(sl, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "tp3": round(tp3, 2),
    }


# ============================================================
# ENTRY MESSAGE
# ============================================================

def msg_entry(
    direction,
    position,
    m15,
    five,
    h1
):
    if direction == "LONG":
        title = "🔴 롱 포지션 신규 진입"
    else:
        title = "🔵 숏 포지션 신규 진입"

    h1_text = (
        h1["trend"]
        if h1
        else "N/A"
    )

    message = (
        "🥇 <b>금 선물 스마트 시그널</b>\n"
        "<code>ONE POSITION LOCK V14.0</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"

        f"<b>{title}</b>\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 진입가 : <b>{fmt_price(position['entry'])}</b>\n"
        f"🛑 손절가 : <b>{fmt_price(position['sl'])}</b>\n\n"

        "🎯 <b>익절 목표</b>\n"
        f"① TP1   {fmt_price(position['tp1'])}\n"
        f"② TP2   {fmt_price(position['tp2'])}\n"
        f"③ TP3   {fmt_price(position['tp3'])}\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "📊 <b>정밀 분석 리포트</b>\n\n"

        f"M15 신호 점수  : "
        f"{m15['long_score'] if direction == 'LONG' else m15['short_score']} / 6\n"

        f"5M 확인 점수   : "
        f"{five['long_score'] if direction == 'LONG' else five['short_score']} / 3\n"

        f"RSI            : "
        f"{fmt_num(m15['rsi'])}\n"

        f"ADX (추세강도) : "
        f"{fmt_num(m15['adx'])}\n"

        f"H1 추세        : {h1_text}\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚙️ 전략 : <b>ONE POSITION LOCK</b>\n"
        "🔒 TP3 또는 SL 종료 전 신규 진입 금지\n"
        "🔴 현재 포지션 실시간 관리 중\n\n"

        f'📈 <a href="{TV_LINK}">TradingView 차트 열기</a>'
    )

    return message


# ============================================================
# TP MESSAGE
# ============================================================

def msg_tp(state, tp_number, current_price):

    direction = state["direction"]

    emoji = "🟢" if direction == "LONG" else "🔵"

    tp_price = state.get(
        f"tp{tp_number}"
    )

    message = (
        "🥇 <b>금 선물 스마트 시그널</b>\n"
        "<code>POSITION MONITOR</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"

        f"{emoji} <b>{direction} TP{tp_number} 도달</b>\n\n"

        f"현재가 : <b>{fmt_price(current_price)}</b>\n"
        f"TP{tp_number} : <b>{fmt_price(tp_price)}</b>\n\n"

        "⚠️ 포지션은 아직 종료되지 않았습니다.\n"
        "🔒 TP3 또는 SL까지 신규 진입 금지\n\n"

        f'📈 <a href="{TV_LINK}">TradingView 차트 열기</a>'
    )

    return message


# ============================================================
# SL MESSAGE
# ============================================================

def msg_sl(state, current_price):

    direction = state["direction"]

    message = (
        "🥇 <b>금 선물 스마트 시그널</b>\n"
        "<code>POSITION CLOSED</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"

        f"🛑 <b>{direction} 손절(SL) 종료</b>\n\n"

        f"진입가 : {fmt_price(state['entry'])}\n"
        f"손절가 : <b>{fmt_price(state['sl'])}</b>\n"
        f"현재가 : {fmt_price(current_price)}\n\n"

        "🔓 포지션 종료\n"
        "⏳ 신규 신호 대기 중\n\n"

        f'📈 <a href="{TV_LINK}">TradingView 차트 열기</a>'
    )

    return message


# ============================================================
# TP3 MESSAGE
# ============================================================

def msg_tp3(state, current_price):

    direction = state["direction"]

    message = (
        "🥇 <b>금 선물 스마트 시그널</b>\n"
        "<code>POSITION CLOSED</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"

        f"🎯 <b>{direction} TP3 최종 익절</b>\n\n"

        f"진입가 : {fmt_price(state['entry'])}\n"
        f"TP1    : {fmt_price(state['tp1'])}\n"
        f"TP2    : {fmt_price(state['tp2'])}\n"
        f"TP3    : <b>{fmt_price(state['tp3'])}</b>\n\n"

        f"현재가 : {fmt_price(current_price)}\n\n"

        "🔓 포지션 종료\n"
        "⏳ 다음 신규 신호 대기 중\n\n"

        f'📈 <a href="{TV_LINK}">TradingView 차트 열기</a>'
    )

    return message


# ============================================================
# ENTER POSITION
# ============================================================

def enter_position(
    state,
    direction,
    current_price,
    m15,
    five,
    h1
):

    # ========================================================
    # ABSOLUTE SAFETY CHECK
    # ========================================================

    if state.get("status") == "ACTIVE":
        print(
            "!!! ENTRY BLOCKED !!!"
        )
        print(
            "Existing position is ACTIVE."
        )
        return False

    position = calculate_position(
        direction,
        current_price,
        five
    )

    signal_id = make_signal_id(
        direction,
        m15,
        five
    )

    # 상태를 먼저 ACTIVE로 변경
    # Telegram보다 먼저 저장하여 중복 실행 방지
    state["status"] = "ACTIVE"

    state["direction"] = direction

    state["entry"] = position["entry"]
    state["risk"] = position["risk"]

    state["sl"] = position["sl"]

    state["tp1"] = position["tp1"]
    state["tp2"] = position["tp2"]
    state["tp3"] = position["tp3"]

    state["tp1_hit"] = False
    state["tp2_hit"] = False
    state["tp3_hit"] = False

    state["signal_time"] = now_iso()
    state["last_signal_time"] = now_iso()

    state["signal_id"] = signal_id

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

    state["rsi"] = m15["rsi"]
    state["adx"] = m15["adx"]

    state["last_price"] = current_price
    state["last_monitor_time"] = now_iso()

    # ========================================================
    # SAVE ACTIVE STATE FIRST
    # ========================================================

    save_state(state)

    append_log(
        "ENTRY",
        state,
        current_price,
        {
            "signal_id": signal_id,
            "m15": m15,
            "five": five,
            "h1": h1,
        }
    )

    # ========================================================
    # TELEGRAM
    # ========================================================

    send_telegram(
        msg_entry(
            direction,
            position,
            m15,
            five,
            h1
        )
    )

    print("====================================")
    print("POSITION OPENED")
    print("Direction :", direction)
    print("Entry     :", fmt_price(position["entry"]))
    print("SL        :", fmt_price(position["sl"]))
    print("TP1       :", fmt_price(position["tp1"]))
    print("TP2       :", fmt_price(position["tp2"]))
    print("TP3       :", fmt_price(position["tp3"]))
    print("====================================")

    return True


# ============================================================
# RESET AFTER EXIT
# ============================================================

def reset_after_exit(
    state,
    reason,
    current_price
):

    old_direction = state.get(
        "direction"
    )

    old_entry = state.get(
        "entry"
    )

    # 종료 정보 보존
    state["last_exit_time"] = now_iso()
    state["last_exit_reason"] = reason
    state["last_exit_direction"] = old_direction

    # SL 기록
    if reason == "SL":
        state["last_sl_time"] = now_iso()
        state["last_sl_direction"] = old_direction

    # ACTIVE -> IDLE
    state["status"] = "IDLE"

    state["direction"] = None

    state["entry"] = None
    state["sl"] = None

    state["tp1"] = None
    state["tp2"] = None
    state["tp3"] = None

    state["risk"] = None

    state["tp1_hit"] = False
    state["tp2_hit"] = False
    state["tp3_hit"] = False

    state["signal_time"] = None
    state["signal_id"] = None

    state["m15_score"] = None
    state["five_score"] = None
    state["rsi"] = None
    state["adx"] = None

    state["last_price"] = current_price
    state["last_monitor_time"] = now_iso()

    save_state(state)

    append_log(
        "EXIT",
        state,
        current_price,
        {
            "reason": reason,
            "old_direction": old_direction,
            "old_entry": old_entry,
        }
    )


# ============================================================
# MONITOR ACTIVE POSITION
# ============================================================

def monitor_active(
    state,
    current_price
):

    if state.get("status") != "ACTIVE":
        return

    direction = state.get(
        "direction"
    )

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

    print("====================================")
    print("ACTIVE POSITION MONITOR")
    print("====================================")

    print(
        "Direction :",
        direction
    )

    print(
        "Entry     :",
        fmt_price(entry)
    )

    print(
        "Current   :",
        fmt_price(current_price)
    )

    print(
        "SL        :",
        fmt_price(sl)
    )

    print(
        "TP1       :",
        fmt_price(tp1)
    )

    print(
        "TP2       :",
        fmt_price(tp2)
    )

    print(
        "TP3       :",
        fmt_price(tp3)
    )

    # ========================================================
    # LONG
    # ========================================================

    if direction == "LONG":

        # SL 우선
        if current_price <= sl:

            send_telegram(
                msg_sl(
                    state,
                    current_price
                )
            )

            append_log(
                "SL_HIT",
                state,
                current_price
            )

            reset_after_exit(
                state,
                "SL",
                current_price
            )

            return

        # TP3
        if current_price >= tp3:

            state["tp3_hit"] = True

            send_telegram(
                msg_tp3(
                    state,
                    current_price
                )
            )

            append_log(
                "TP3_HIT",
                state,
                current_price
            )

            reset_after_exit(
                state,
                "TP3",
                current_price
            )

            return

        # TP2
        if (
            current_price >= tp2
            and not state.get("tp2_hit")
        ):

            state["tp2_hit"] = True

            send_telegram(
                msg_tp(
                    state,
                    2,
                    current_price
                )
            )

            append_log(
                "TP2_HIT",
                state,
                current_price
            )

            save_state(state)

            return

        # TP1
        if (
            current_price >= tp1
            and not state.get("tp1_hit")
        ):

            state["tp1_hit"] = True

            send_telegram(
                msg_tp(
                    state,
                    1,
                    current_price
                )
            )

            append_log(
                "TP1_HIT",
                state,
                current_price
            )

            save_state(state)

            return

    # ========================================================
    # SHORT
    # ========================================================

    elif direction == "SHORT":

        # SL 우선
        if current_price >= sl:

            send_telegram(
                msg_sl(
                    state,
                    current_price
                )
            )

            append_log(
                "SL_HIT",
                state,
                current_price
            )

            reset_after_exit(
                state,
                "SL",
                current_price
            )

            return

        # TP3
        if current_price <= tp3:

            state["tp3_hit"] = True

            send_telegram(
                msg_tp3(
                    state,
                    current_price
                )
            )

            append_log(
                "TP3_HIT",
                state,
                current_price
            )

            reset_after_exit(
                state,
                "TP3",
                current_price
            )

            return

        # TP2
        if (
            current_price <= tp2
            and not state.get("tp2_hit")
        ):

            state["tp2_hit"] = True

            send_telegram(
                msg_tp(
                    state,
                    2,
                    current_price
                )
            )

            append_log(
                "TP2_HIT",
                state,
                current_price
            )

            save_state(state)

            return

        # TP1
        if (
            current_price <= tp1
            and not state.get("tp1_hit")
        ):

            state["tp1_hit"] = True

            send_telegram(
                msg_tp(
                    state,
                    1,
                    current_price
                )
            )

            append_log(
                "TP1_HIT",
                state,
                current_price
            )

            save_state(state)

            return

    # ========================================================
    # MONITOR STATE SAVE
    # ========================================================

    state["last_price"] = current_price
    state["last_monitor_time"] = now_iso()

    save_state(state)


# ============================================================
# MAIN
# ============================================================

def main():

    print("====================================")
    print(f" GOLD FUTURES SMART SIGNAL BOT V{VERSION}")
    print(" KST:", now_iso())
    print("====================================")

    # ========================================================
    # LOAD STATE FIRST
    # ========================================================

    state = load_state()

    # ========================================================
    # DOWNLOAD 1M FIRST
    # ========================================================

    data = download_data()

    if not data:
        print("No market data.")
        return

    current_price = get_current_price(
        data.get("1m")
    )

    if current_price is None:
        print("Unable to determine current price.")
        return

    print(
        "CURRENT PRICE:",
        fmt_price(current_price)
    )

    # ========================================================
    # CRITICAL ACTIVE LOCK
    #
    # 이 부분이 핵심
    #
    # ACTIVE이면 M15/5M 신규 시그널 계산 자체를 하지 않는다.
    # ========================================================

    if state.get("status") == "ACTIVE":

        print("")
        print("####################################")
        print("# EXISTING POSITION IS ACTIVE")
        print("# NEW ENTRY IS COMPLETELY BLOCKED")
        print("####################################")
        print("")

        monitor_active(
            state,
            current_price
        )

        return

    # ========================================================
    # IDLE ONLY FROM HERE
    # ========================================================

    print("")
    print("STATE: IDLE")
    print("Searching for NEW signal...")
    print("")

    # ========================================================
    # ANALYZE DATA
    # ========================================================

    m15 = analyze_m15(
        data.get("15m")
    )

    five = analyze_5m(
        data.get("5m")
    )

    h1 = analyze_h1(
        data.get("1h")
    )

    if not m15:
        print("M15 analysis unavailable.")
        return

    if not five:
        print("5M analysis unavailable.")
        return

    # ========================================================
    # REPORT
    # ========================================================

    print("====================================")
    print("CURRENT MARKET CHECK")
    print("====================================")

    print(
        "Price :",
        fmt_price(current_price)
    )

    print(
        "M15 Close :",
        fmt_price(m15["close"])
    )

    print(
        "EMA20     :",
        fmt_price(m15["ema20"])
    )

    print(
        "EMA50     :",
        fmt_price(m15["ema50"])
    )

    print(
        "RSI       :",
        fmt_num(m15["rsi"])
    )

    print(
        "ADX       :",
        fmt_num(m15["adx"])
    )

    print(
        "ATR       :",
        fmt_num(m15["atr"])
    )

    print("")

    print("LONG M15 :",
          m15["long_score"], "/ 6")

    print("LONG 5M  :",
          five["long_score"], "/ 3")

    print("")

    print("SHORT M15:",
          m15["short_score"], "/ 6")

    print("SHORT 5M :",
          five["short_score"], "/ 3")

    if h1:
        print("")
        print("H1 TREND :",
              h1["trend"])

    # ========================================================
    # FIND SIGNAL
    # ========================================================

    direction = find_signal(
        m15,
        five,
        h1
    )

    if direction is None:

        print("")
        print("NO VALID SIGNAL")
        return

    print("")
    print("====================================")
    print("VALID SIGNAL FOUND")
    print("Direction:", direction)
    print("====================================")

    # ========================================================
    # COOLDOWN
    # ========================================================

    if not signal_allowed(
        state,
        direction
    ):

        print(
            "SIGNAL BLOCKED BY COOLDOWN"
        )

        return

    # ========================================================
    # FINAL ACTIVE SAFETY CHECK
    # ========================================================

    # 이중 안전장치
    if state.get("status") == "ACTIVE":

        print(
            "FINAL SAFETY LOCK -> BLOCKED"
        )

        return

    # ========================================================
    # IMMEDIATE ENTRY
    # ========================================================

    print(
        f"Executing {direction} "
        f"immediately at "
        f"{fmt_price(current_price)}"
    )

    enter_position(
        state,
        direction,
        current_price,
        m15,
        five,
        h1
    )


# ============================================================
# ERROR HANDLER
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        print("")
        print("====================================")
        print("FATAL ERROR")
        print("====================================")

        print(str(e))

        traceback.print_exc()

        try:
            append_log(
                "ERROR",
                None,
                None,
                {
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                }
            )
        except Exception:
            pass

        raise
