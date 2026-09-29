import os
import json
import time
from datetime import datetime, timezone, timedelta

import requests
import numpy as np
import pandas as pd
import yfinance as yf


TICKER = "GC=F"

STATE_FILE = "signal_state.json"
LOG_FILE = "bot_log.json"

MIN_M15_SCORE = 4
STRONG_M15_SCORE = 5

LONG_RSI_MIN = 50
LONG_RSI_MAX = 72

SHORT_RSI_MIN = 28
SHORT_RSI_MAX = 50

MIN_ADX = 13

MAX_ENTRY_DISTANCE_ATR = 1.60
MAX_SIGNAL_MOVE_ATR = 1.20

COOLDOWN_MINUTES = 60

SWING_LOOKBACK = 8
SL_ATR_BUFFER = 0.35

MIN_RISK_ATR = 0.55
MAX_RISK_ATR = 2.50

TP1_R = 1.20
TP2_R = 2.00
TP3_R = 3.00


TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def telegram_configured():
    return bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)


def send_telegram(message):
    if not telegram_configured():
        print("Telegram credentials missing.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    for attempt in range(3):
        try:
            response = requests.post(
                url,
                json=payload,
                timeout=15
            )

            if response.ok:
                data = response.json()

                if data.get("ok"):
                    print("Telegram sent successfully.")
                    return True

                print(
                    "Telegram API error: "
                    + str(data.get("description", "unknown error"))
                )

            else:
                print(
                    f"Telegram HTTP error {response.status_code}: "
                    f"{response.text}"
                )

        except Exception as e:
            print(f"Telegram send error: {e}")

        if attempt < 2:
            time.sleep(2)

    print("Telegram send failed after 3 attempts.")
    return False


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            return data

    except Exception as e:
        print(f"State load error: {e}")

    return {}


def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                state,
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:
        print(f"State save error: {e}")


def load_log():
    if not os.path.exists(LOG_FILE):
        return []

    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            return data

    except Exception as e:
        print(f"Log load error: {e}")

    return []


def save_log(log):
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(
                log[-200:],
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:
        print(f"Log save error: {e}")


def add_log(event, data=None):
    log = load_log()

    item = {
        "time": datetime.now(timezone.utc).isoformat(),
        "event": event
    }

    if data is not None:
        item["data"] = data

    log.append(item)
    save_log(log)


def download_data():
    print("Downloading market data...")

    data = {}

    intervals = {
        "1h": "60d",
        "15m": "30d",
        "5m": "15d",
        "1m": "7d",
    }

    for interval, period in intervals.items():
        try:
            df = yf.download(
                TICKER,
                period=period,
                interval=interval,
                progress=False,
                auto_adjust=False
            )

            if df is None or df.empty:
                print(f"{interval}: NO DATA")
                continue

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df = df.dropna()

            data[interval] = df

            print(f"{interval}: {len(df)} candles")

        except Exception as e:
            print(f"{interval}: download error: {e}")

    return data


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
    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    previous_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - previous_close).abs()
    tr3 = (low - previous_close).abs()

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

    plus_dm = pd.Series(
        np.where(
            (up_move > down_move) & (up_move > 0),
            up_move,
            0
        ),
        index=df.index
    )

    minus_dm = pd.Series(
        np.where(
            (down_move > up_move) & (down_move > 0),
            down_move,
            0
        ),
        index=df.index
    )

    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs()
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
        ).mean()
        /
        atr_value.replace(0, np.nan)
    )

    minus_di = (
        100 *
        minus_dm.ewm(
            alpha=1 / length,
            adjust=False
        ).mean()
        /
        atr_value.replace(0, np.nan)
    )

    dx = (
        100 *
        (plus_di - minus_di).abs()
        /
        (plus_di + minus_di).replace(0, np.nan)
    )

    return dx.ewm(
        alpha=1 / length,
        adjust=False
    ).mean()


def prepare_m15(df):
    df = df.copy()

    df["EMA20"] = ema(df["Close"], 20)
    df["EMA50"] = ema(df["Close"], 50)
    df["RSI"] = rsi(df["Close"], 14)
    df["ADX"] = adx(df, 14)
    df["ATR"] = atr(df, 14)

    return df.dropna()


def prepare_5m(df):
    df = df.copy()

    df["EMA20"] = ema(df["Close"], 20)
    df["EMA50"] = ema(df["Close"], 50)

    return df.dropna()


def prepare_h1(df):
    df = df.copy()

    df["EMA20"] = ema(df["Close"], 20)
    df["EMA50"] = ema(df["Close"], 50)

    return df.dropna()


def calculate_m15_score(df):
    row = df.iloc[-1]
    prev = df.iloc[-2]

    close = float(row["Close"])
    ema20_value = float(row["EMA20"])
    ema50_value = float(row["EMA50"])
    rsi_value = float(row["RSI"])
    adx_value = float(row["ADX"])

    bullish = float(row["Close"]) > float(row["Open"])
    bearish = float(row["Close"]) < float(row["Open"])

    long_score = 0
    short_score = 0

    if close > ema20_value:
        long_score += 1

    if close < ema20_value:
        short_score += 1

    if ema20_value > ema50_value:
        long_score += 1

    if ema20_value < ema50_value:
        short_score += 1

    if bullish:
        long_score += 1

    if bearish:
        short_score += 1

    if close > float(prev["High"]):
        long_score += 1

    if close < float(prev["Low"]):
        short_score += 1

    if LONG_RSI_MIN <= rsi_value <= LONG_RSI_MAX:
        long_score += 1

    if SHORT_RSI_MIN <= rsi_value <= SHORT_RSI_MAX:
        short_score += 1

    if adx_value >= MIN_ADX:
        if long_score > short_score:
            long_score += 1
        elif short_score > long_score:
            short_score += 1
        else:
            long_score += 1

    return long_score, short_score


def calculate_5m_score(df):
    row = df.iloc[-1]
    prev = df.iloc[-2]

    close = float(row["Close"])
    ema20_value = float(row["EMA20"])
    ema50_value = float(row["EMA50"])

    long_score = 0
    short_score = 0

    if close > ema20_value:
        long_score += 1

    if close < ema20_value:
        short_score += 1

    if ema20_value > ema50_value:
        long_score += 1

    if ema20_value < ema50_value:
        short_score += 1

    if close > float(prev["Close"]):
        long_score += 1

    if close < float(prev["Close"]):
        short_score += 1

    return long_score, short_score


def h1_context(df):
    row = df.iloc[-1]

    close = float(row["Close"])
    ema20_value = float(row["EMA20"])
    ema50_value = float(row["EMA50"])

    if close > ema20_value and ema20_value > ema50_value:
        return "BULL"

    if close < ema20_value and ema20_value < ema50_value:
        return "BEAR"

    return "NEUTRAL"


def determine_signal(
    m15_long,
    m15_short,
    m5_long,
    m5_short
):

    if m15_long >= STRONG_M15_SCORE:

        if m5_short >= 3:
            return None, "STRONG LONG BLOCKED"

        return "LONG", "M15 STRONG"

    if m15_short >= STRONG_M15_SCORE:

        if m5_long >= 3:
            return None, "STRONG SHORT BLOCKED"

        return "SHORT", "M15 STRONG"

    if m15_long >= MIN_M15_SCORE:

        if m5_short >= 3:
            return None, "LONG BLOCKED"

        return "LONG", "M15 NORMAL"

    if m15_short >= MIN_M15_SCORE:

        if m5_long >= 3:
            return None, "SHORT BLOCKED"

        return "SHORT", "M15 NORMAL"

    return None, "M15 SCORE TOO LOW"


def entry_distance_ok(
    price,
    ema20_value,
    atr_value
):

    if atr_value <= 0:
        return False

    distance = abs(
        price - ema20_value
    )

    return distance <= (
        atr_value *
        MAX_ENTRY_DISTANCE_ATR
    )


def signal_move_ok(df):

    if len(df) < 3:
        return True

    row = df.iloc[-1]
    prev = df.iloc[-2]

    move = abs(
        float(row["Close"])
        -
        float(prev["Close"])
    )

    atr_value = float(row["ATR"])

    if atr_value <= 0:
        return True

    return move <= (
        atr_value *
        MAX_SIGNAL_MOVE_ATR
    )


def candle_filter(df, direction):

    row = df.iloc[-1]

    open_price = float(row["Open"])
    close_price = float(row["Close"])
    high_price = float(row["High"])
    low_price = float(row["Low"])

    candle_range = high_price - low_price

    if candle_range <= 0:
        return False

    body = abs(
        close_price -
        open_price
    )

    body_ratio = (
        body /
        candle_range
    )

    if direction == "LONG":
        return (
            close_price >= open_price
            and
            body_ratio >= 0.18
        )

    return (
        close_price <= open_price
        and
        body_ratio >= 0.18
    )


def calculate_targets(
    m15,
    direction,
    entry
):

    atr_value = float(
        m15.iloc[-1]["ATR"]
    )

    recent = m15.tail(
        SWING_LOOKBACK
    )

    if direction == "LONG":

        swing_low = float(
            recent["Low"].min()
        )

        sl = (
            swing_low -
            atr_value *
            SL_ATR_BUFFER
        )

        risk = entry - sl

        if risk <= 0:
            return None

        tp1 = entry + risk * TP1_R
        tp2 = entry + risk * TP2_R
        tp3 = entry + risk * TP3_R

    else:

        swing_high = float(
            recent["High"].max()
        )

        sl = (
            swing_high +
            atr_value *
            SL_ATR_BUFFER
        )

        risk = sl - entry

        if risk <= 0:
            return None

        tp1 = entry - risk * TP1_R
        tp2 = entry - risk * TP2_R
        tp3 = entry - risk * TP3_R

    risk_atr = risk / atr_value

    if risk_atr < MIN_RISK_ATR:
        return None

    if risk_atr > MAX_RISK_ATR:
        return None

    return {
        "entry": round(entry, 2),
        "sl": round(sl, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "tp3": round(tp3, 2),
        "risk": round(risk, 2),
        "risk_atr": round(risk_atr, 2)
    }


def format_price(value):
    return f"${float(value):,.2f}"


def make_entry_message(position):

    direction = position["direction"]

    if direction == "LONG":
        title = "🟢 GOLD LONG SIGNAL"
    else:
        title = "🔴 GOLD SHORT SIGNAL"

    return f"""
<b>{title}</b>
━━━━━━━━━━━━━━━━━━

💰 GOLD FUTURES

방향       : <b>{direction}</b>
진입가     : <b>{format_price(position["entry"])}</b>

🛑 손절가   : {format_price(position["sl"])}

🎯 TP1      : {format_price(position["tp1"])}
🎯 TP2      : {format_price(position["tp2"])}
🎯 TP3      : {format_price(position["tp3"])}

━━━━━━━━━━━━━━━━━━

모드       : {position["mode"]}
M15 Score  : {position["m15_score"]}/6
5M Score   : {position["m5_score"]}/3

━━━━━━━━━━━━━━━━━━
🚨 신규 진입 신호
━━━━━━━━━━━━━━━━━━
""".strip()


def make_tp_message(
    position,
    tp_number,
    price
):

    return f"""
<b>🎯 TAKE PROFIT {tp_number}</b>
━━━━━━━━━━━━━━━━━━

💰 GOLD FUTURES

방향       : <b>{position["direction"]}</b>

진입가     : {format_price(position["entry"])}

청산가     : <b>{format_price(price)}</b>

━━━━━━━━━━━━━━━━━━
🎯 TP{tp_number} 도달
━━━━━━━━━━━━━━━━━━
""".strip()


def make_sl_message(
    position,
    price
):

    entry = float(
        position["entry"]
    )

    exit_price = float(price)

    if position["direction"] == "LONG":

        pnl = (
            (exit_price - entry)
            /
            entry
        ) * 100

    else:

        pnl = (
            (entry - exit_price)
            /
            entry
        ) * 100

    return f"""
<b>🔴 STOP LOSS</b>
━━━━━━━━━━━━━━━━━━

💰 GOLD FUTURES

방향       : <b>{position["direction"]}</b>

진입가     : {format_price(entry)}

청산가     : {format_price(exit_price)}

손익률     : {pnl:+.2f}%

━━━━━━━━━━━━━━━━━━
🛑 손절 처리 완료
━━━━━━━━━━━━━━━━━━
""".strip()


def cooldown_active(state):

    value = state.get(
        "cooldown_until"
    )

    if not value:
        return False

    try:

        until = datetime.fromisoformat(
            value
        )

        return (
            datetime.now(timezone.utc)
            <
            until
        )

    except Exception:

        return False


def set_cooldown(state):

    until = (
        datetime.now(timezone.utc)
        +
        timedelta(
            minutes=COOLDOWN_MINUTES
        )
    )

    state[
        "cooldown_until"
    ] = until.isoformat()


def has_active_position(state):

    position = state.get(
        "position"
    )

    if not isinstance(
        position,
        dict
    ):
        return False

    return position.get(
        "active",
        False
    ) is True


def ensure_entry_alert(state):

    position = state.get(
        "position"
    )

    if not isinstance(
        position,
        dict
    ):
        return False

    if not position.get(
        "active",
        False
    ):
        return False

    if position.get(
        "entry_alert_sent",
        False
    ):
        return False

    print("")
    print("ENTRY ALERT RECOVERY")

    success = send_telegram(
        make_entry_message(
            position
        )
    )

    if success:

        position[
            "entry_alert_sent"
        ] = True

        state[
            "position"
        ] = position

        save_state(state)

        add_log(
            "ENTRY_ALERT_SENT",
            {
                "direction":
                    position.get(
                        "direction"
                    ),
                "entry":
                    position.get(
                        "entry"
                    )
            }
        )

        print(
            "Entry alert status: SENT"
        )

        return True

    print(
        "Entry alert status: FAILED"
    )

    print(
        "Will retry next run."
    )

    return False


def monitor_position(
    state,
    df_1m
):

    position = state.get(
        "position"
    )

    if not isinstance(
        position,
        dict
    ):
        return state

    if not position.get(
        "active",
        False
    ):
        return state

    if (
        df_1m is None
        or
        df_1m.empty
    ):
        return state

    current_price = float(
        df_1m.iloc[-1]["Close"]
    )

    direction = position[
        "direction"
    ]

    entry = float(
        position["entry"]
    )

    sl = float(
        position["sl"]
    )

    tp1 = float(
        position["tp1"]
    )

    tp2 = float(
        position["tp2"]
    )

    tp3 = float(
        position["tp3"]
    )

    print("")
    print("====================================")
    print(" ACTIVE POSITION")
    print("====================================")

    print(
        f"Direction : {direction}"
    )

    print(
        f"Entry     : {format_price(entry)}"
    )

    print(
        f"SL        : {format_price(sl)}"
    )

    print(
        f"TP1       : {format_price(tp1)}"
    )

    print(
        f"TP2       : {format_price(tp2)}"
    )

    print(
        f"TP3       : {format_price(tp3)}"
    )

    print(
        f"1M Close  : {format_price(current_price)}"
    )

    ensure_entry_alert(state)

    if direction == "LONG":

        if current_price <= sl:

            if not position.get(
                "sl_alert_sent",
                False
            ):

                success = send_telegram(
                    make_sl_message(
                        position,
                        current_price
                    )
                )

                if success:

                    position[
                        "sl_alert_sent"
                    ] = True

                    position[
                        "active"
                    ] = False

                    set_cooldown(state)

                    add_log(
                        "STOP_LOSS",
                        {
                            "direction":
                                direction,
                            "price":
                                current_price
                        }
                    )

                    state[
                        "position"
                    ] = position

                    save_state(state)

            return state

        if current_price >= tp1:

            if not position.get(
                "tp1_alert_sent",
                False
            ):

                success = send_telegram(
                    make_tp_message(
                        position,
                        1,
                        current_price
                    )
                )

                if success:

                    position[
                        "tp1_alert_sent"
                    ] = True

                    add_log(
                        "TP1",
                        {
                            "direction":
                                direction,
                            "price":
                                current_price
                        }
                    )

        if current_price >= tp2:

            if not position.get(
                "tp2_alert_sent",
                False
            ):

                success = send_telegram(
                    make_tp_message(
                        position,
                        2,
                        current_price
                    )
                )

                if success:

                    position[
                        "tp2_alert_sent"
                    ] = True

                    position[
                        "sl"
                    ] = entry

                    position[
                        "sl_to_be"
                    ] = True

                    add_log(
                        "TP2",
                        {
                            "direction":
                                direction,
                            "price":
                                current_price,
                            "sl_to_be":
                                entry
                        }
                    )

        if current_price >= tp3:

            if not position.get(
                "tp3_alert_sent",
                False
            ):

                success = send_telegram(
                    make_tp_message(
                        position,
                        3,
                        current_price
                    )
                )

                if success:

                    position[
                        "tp3_alert_sent"
                    ] = True

                    position[
                        "active"
                    ] = False

                    set_cooldown(state)

                    add_log(
                        "TP3",
                        {
                            "direction":
                                direction,
                            "price":
                                current_price
                        }
                    )

    else:

        if current_price >= sl:

            if not position.get(
                "sl_alert_sent",
                False
            ):

                success = send_telegram(
                    make_sl_message(
                        position,
                        current_price
                    )
                )

                if success:

                    position[
                        "sl_alert_sent"
                    ] = True

                    position[
                        "active"
                    ] = False

                    set_cooldown(state)

                    add_log(
                        "STOP_LOSS",
                        {
                            "direction":
                                direction,
                            "price":
                                current_price
                        }
                    )

                    state[
                        "position"
                    ] = position

                    save_state(state)

            return state

        if current_price <= tp1:

            if not position.get(
                "tp1_alert_sent",
                False
            ):

                success = send_telegram(
                    make_tp_message(
                        position,
                        1,
                        current_price
                    )
                )

                if success:

                    position[
                        "tp1_alert_sent"
                    ] = True

                    add_log(
                        "TP1",
                        {
                            "direction":
                                direction,
                            "price":
                                current_price
                        }
                    )

        if current_price <= tp2:

            if not position.get(
                "tp2_alert_sent",
                False
            ):

                success = send_telegram(
                    make_tp_message(
                        position,
                        2,
                        current_price
                    )
                )

                if success:

                    position[
                        "tp2_alert_sent"
                    ] = True

                    position[
                        "sl"
                    ] = entry

                    position[
                        "sl_to_be"
                    ] = True

                    add_log(
                        "TP2",
                        {
                            "direction":
                                direction,
                            "price":
                                current_price,
                            "sl_to_be":
                                entry
                        }
                    )

        if current_price <= tp3:

            if not position.get(
                "tp3_alert_sent",
                False
            ):

                success = send_telegram(
                    make_tp_message(
                        position,
                        3,
                        current_price
                    )
                )

                if success:

                    position[
                        "tp3_alert_sent"
                    ] = True

                    position[
                        "active"
                    ] = False

                    set_cooldown(state)

                    add_log(
                        "TP3",
                        {
                            "direction":
                                direction,
                            "price":
                                current_price
                        }
                    )

    state[
        "position"
    ] = position

    save_state(state)

    return state


def create_new_position(
    state,
    direction,
    mode,
    entry,
    targets,
    m15_score,
    m5_score
):

    if has_active_position(
        state
    ):
        return state

    position = {
        "active": True,
        "direction": direction,
        "mode": mode,

        "entry": targets["entry"],
        "sl": targets["sl"],

        "tp1": targets["tp1"],
        "tp2": targets["tp2"],
        "tp3": targets["tp3"],

        "risk": targets["risk"],
        "risk_atr": targets["risk_atr"],

        "m15_score": m15_score,
        "m5_score": m5_score,

        "created_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "entry_alert_sent": False,

        "tp1_alert_sent": False,
        "tp2_alert_sent": False,
        "tp3_alert_sent": False,

        "sl_alert_sent": False,

        "sl_to_be": False
    }

    state[
        "position"
    ] = position

    save_state(state)

    add_log(
        "NEW_SIGNAL",
        {
            "direction":
                direction,
            "entry":
                targets["entry"],
            "sl":
                targets["sl"],
            "tp1":
                targets["tp1"],
            "tp2":
                targets["tp2"],
            "tp3":
                targets["tp3"]
        }
    )

    print("")
    print("====================================")
    print(" NEW SIGNAL")
    print("====================================")

    print(
        f"Direction : {direction}"
    )

    print(
        f"Mode      : {mode}"
    )

    print(
        f"Entry     : {format_price(targets['entry'])}"
    )

    print(
        f"SL        : {format_price(targets['sl'])}"
    )

    print(
        f"TP1       : {format_price(targets['tp1'])}"
    )

    print(
        f"TP2       : {format_price(targets['tp2'])}"
    )

    print(
        f"TP3       : {format_price(targets['tp3'])}"
    )

    success = send_telegram(
        make_entry_message(
            position
        )
    )

    if success:

        position[
            "entry_alert_sent"
        ] = True

        state[
            "position"
        ] = position

        save_state(state)

        add_log(
            "ENTRY_ALERT_SENT",
            {
                "direction":
                    direction,
                "entry":
                    targets["entry"]
            }
        )

        print(
            "ENTRY TELEGRAM: SENT"
        )

    else:

        position[
            "entry_alert_sent"
        ] = False

        state[
            "position"
        ] = position

        save_state(state)

        print(
            "ENTRY TELEGRAM: FAILED"
        )

        print(
            "Will retry automatically."
        )

    return state


def main():

    print("=================================")
    print(" GOLD FUTURES SMART SIGNAL BOT")
    print(" BALANCED V9")
    print(" M15 LEAD + 5M SUPPORT")
    print(" H1 CONTEXT ONLY")
    print(" EARLY MOVE IMPROVED")
    print(" TELEGRAM RECOVERY ENABLED")
    print("=================================")

    state = load_state()

    data = download_data()

    required = [
        "15m",
        "5m",
        "1h",
        "1m"
    ]

    for interval in required:

        if interval not in data:

            print(
                f"{interval} data unavailable."
            )

            return

    m15 = prepare_m15(
        data["15m"]
    )

    m5 = prepare_5m(
        data["5m"]
    )

    h1 = prepare_h1(
        data["1h"]
    )

    if (
        m15.empty
        or
        m5.empty
        or
        h1.empty
    ):

        print(
            "Indicator data unavailable."
        )

        return

    if has_active_position(
        state
    ):

        print(
            "Active position detected."
        )

        state = monitor_position(
            state,
            data["1m"]
        )

        if has_active_position(
            state
        ):
            return

    row = m15.iloc[-1]

    price = float(
        row["Close"]
    )

    ema20_value = float(
        row["EMA20"]
    )

    ema50_value = float(
        row["EMA50"]
    )

    rsi_value = float(
        row["RSI"]
    )

    adx_value = float(
        row["ADX"]
    )

    atr_value = float(
        row["ATR"]
    )

    print("")
    print("====================================")
    print(" CURRENT MARKET CHECK")
    print("====================================")

    print(
        f"M15 Close : {format_price(price)}"
    )

    print(
        f"EMA20     : {format_price(ema20_value)}"
    )

    print(
        f"EMA50     : {format_price(ema50_value)}"
    )

    print(
        f"RSI       : {rsi_value:.2f}"
    )

    print(
        f"ADX       : {adx_value:.2f}"
    )

    print(
        f"ATR       : {atr_value:.2f}"
    )

    m15_long, m15_short = (
        calculate_m15_score(
            m15
        )
    )

    m5_long, m5_short = (
        calculate_5m_score(
            m5
        )
    )

    h1_state = h1_context(
        h1
    )

    print("")
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
        f"H1        : "
        f"{'BULL' if h1_state == 'BULL' else 'NOT BULL'}"
    )

    print("")
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
        f"H1        : "
        f"{'BEAR' if h1_state == 'BEAR' else 'NOT BEAR'}"
    )

    signal, mode = determine_signal(
        m15_long,
        m15_short,
        m5_long,
        m5_short
    )

    print("")
    print("====================================")
    print(" FILTER")
    print("====================================")

    if signal is None:

        print(
            "Distance : PASS"
        )

        print(
            "Candle   : PASS"
        )

        print(
            "Move     : PASS"
        )

        print(
            f"Reason   : {mode}"
        )

        print(
            "No valid signal."
        )

        return

    distance_pass = entry_distance_ok(
        price,
        ema20_value,
        atr_value
    )

    candle_pass = candle_filter(
        m15,
        signal
    )

    move_pass = signal_move_ok(
        m15
    )

    print(
        f"Distance : "
        f"{'PASS' if distance_pass else 'FAIL'}"
    )

    print(
        f"Candle   : "
        f"{'PASS' if candle_pass else 'FAIL'}"
    )

    print(
        f"Move     : "
        f"{'PASS' if move_pass else 'FAIL'}"
    )

    if not distance_pass:

        print(
            "No valid signal."
        )

        return

    if not move_pass:

        print(
            "No valid signal."
        )

        return

    if mode == "M15 STRONG":

        print(
            "Strong M15 signal accepted."
        )

    else:

        if not candle_pass:

            print(
                "Normal M15 candle filter failed."
            )

            print(
                "No valid signal."
            )

            return

    if cooldown_active(
        state
    ):

        print(
            "Cooldown active."
        )

        return

    targets = calculate_targets(
        m15,
        signal,
        price
    )

    if targets is None:

        print(
            "Target calculation rejected."
        )

        print(
            "Risk is outside allowed range."
        )

        return

    score = (
        m15_long
        if signal == "LONG"
        else m15_short
    )

    m5_score = (
        m5_long
        if signal == "LONG"
        else m5_short
    )

    create_new_position(
        state=state,
        direction=signal,
        mode=mode,
        entry=price,
        targets=targets,
        m15_score=score,
        m5_score=m5_score
    )


if __name__ == "__main__":
    main()
