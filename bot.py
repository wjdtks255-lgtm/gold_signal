import os
import json
import time
from datetime import datetime, timedelta, timezone

import requests
import yfinance as yf
import pandas as pd
import numpy as np


# ============================================================
# 기본 설정
# ============================================================

TICKER = "GC=F"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

STATE_FILE = "signal_state.json"
LOG_FILE = "bot_log.json"

# 신호를 너무 자주 발생시키지 않기 위한 쿨다운
COOLDOWN_MINUTES = 60

# 최소 손익비 구조
TP1_R = 1.20
TP2_R = 2.00
TP3_R = 3.00

# ATR 기준 SL 최대 허용 폭
# 너무 멀리 떨어진 SL이 필요한 경우 진입하지 않음
MAX_SL_ATR = 2.50

# EMA에서 너무 멀리 떨어진 가격에서는 추격 진입 금지
MAX_ENTRY_DISTANCE_ATR = 1.00

# 최소 ADX
MIN_ADX_1H = 18
MIN_ADX_15M = 16

# RSI 진입 범위
LONG_RSI_MIN = 52
LONG_RSI_MAX = 68

SHORT_RSI_MIN = 32
SHORT_RSI_MAX = 48


# ============================================================
# Telegram
# ============================================================

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram 환경변수가 없습니다.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

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
            timeout=15
        )

        if response.status_code != 200:
            print("Telegram 전송 실패:", response.text)
            return False

        result = response.json()

        if not result.get("ok"):
            print("Telegram API 오류:", result)
            return False

        return True

    except Exception as e:
        print("Telegram 오류:", e)
        return False


# ============================================================
# 로그
# ============================================================

def log_event(event_type, message, extra=None):

    logs = []

    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)

                if not isinstance(logs, list):
                    logs = []

        except Exception:
            logs = []

    event = {
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        "message": message
    }

    if extra:
        event["data"] = extra

    logs.append(event)

    # 너무 커지는 것을 방지
    logs = logs[-300:]

    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(
            logs,
            f,
            indent=2,
            ensure_ascii=False
        )


# ============================================================
# 상태 파일
# ============================================================

def load_state():

    if not os.path.exists(STATE_FILE):
        return None

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception as e:
        print("상태 파일 읽기 실패:", e)
        return None


def save_state(state):

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            state,
            f,
            indent=2,
            ensure_ascii=False
        )


def clear_state():

    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)


# ============================================================
# 데이터 다운로드
# ============================================================

def get_history(interval, period):

    for attempt in range(3):

        try:

            ticker = yf.Ticker(TICKER)

            df = ticker.history(
                period=period,
                interval=interval,
                auto_adjust=False,
                prepost=False
            )

            if df is None or df.empty:
                raise ValueError("데이터가 비어 있습니다.")

            # MultiIndex 방어
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            required = [
                "Open",
                "High",
                "Low",
                "Close"
            ]

            for column in required:
                if column not in df.columns:
                    raise ValueError(
                        f"{column} 컬럼이 없습니다."
                    )

            df = df.dropna(
                subset=required
            ).copy()

            df = df.sort_index()

            if len(df) < 30:
                raise ValueError(
                    f"{interval} 데이터 부족: {len(df)}개"
                )

            return df

        except Exception as e:

            print(
                f"{interval} 데이터 조회 실패 "
                f"({attempt + 1}/3): {e}"
            )

            time.sleep(2)

    return None


# ============================================================
# 미완성 캔들 제거
# ============================================================

def remove_incomplete_bar(df):

    if df is None or len(df) < 5:
        return None

    # 가장 최근 캔들은 아직 진행 중일 가능성이 있으므로 제거
    return df.iloc[:-1].copy()


# ============================================================
# 기술적 지표
# ============================================================

def add_indicators(df):

    df = df.copy()

    # --------------------------------------------------------
    # EMA
    # --------------------------------------------------------

    df["ema20"] = df["Close"].ewm(
        span=20,
        adjust=False
    ).mean()

    df["ema50"] = df["Close"].ewm(
        span=50,
        adjust=False
    ).mean()

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    delta = df["Close"].diff()

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

    df["rsi"] = 100 - (
        100 / (1 + rs)
    )

    # --------------------------------------------------------
    # ATR
    # --------------------------------------------------------

    prev_close = df["Close"].shift(1)

    tr1 = df["High"] - df["Low"]

    tr2 = (
        df["High"] - prev_close
    ).abs()

    tr3 = (
        df["Low"] - prev_close
    ).abs()

    true_range = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    df["atr"] = true_range.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    # --------------------------------------------------------
    # ADX
    # --------------------------------------------------------

    up_move = df["High"].diff()

    down_move = -df["Low"].diff()

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

    atr = df["atr"].replace(0, np.nan)

    plus_di = (
        100 *
        plus_dm.ewm(
            alpha=1 / 14,
            adjust=False
        ).mean()
        / atr
    )

    minus_di = (
        100 *
        minus_dm.ewm(
            alpha=1 / 14,
            adjust=False
        ).mean()
        / atr
    )

    denominator = (
        plus_di + minus_di
    ).replace(0, np.nan)

    dx = (
        100 *
        (plus_di - minus_di).abs()
        / denominator
    )

    df["adx"] = dx.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    # --------------------------------------------------------
    # 캔들 몸통 / 위치
    # --------------------------------------------------------

    df["body"] = (
        df["Close"] - df["Open"]
    ).abs()

    df["range"] = (
        df["High"] - df["Low"]
    )

    df["body_ratio"] = (
        df["body"] /
        df["range"].replace(0, np.nan)
    )

    df["close_position"] = (
        (df["Close"] - df["Low"]) /
        df["range"].replace(0, np.nan)
    )

    return df


# ============================================================
# 데이터 준비
# ============================================================

def get_market_data():

    df_1h = get_history(
        interval="1h",
        period="30d"
    )

    df_15m = get_history(
        interval="15m",
        period="10d"
    )

    df_5m = get_history(
        interval="5m",
        period="5d"
    )

    df_1m = get_history(
        interval="1m",
        period="5d"
    )

    if (
        df_1h is None or
        df_15m is None or
        df_5m is None or
        df_1m is None
    ):
        return None

    # 현재 진행 중인 캔들 제거
    df_1h = remove_incomplete_bar(df_1h)
    df_15m = remove_incomplete_bar(df_15m)
    df_5m = remove_incomplete_bar(df_5m)
    df_1m = remove_incomplete_bar(df_1m)

    if (
        df_1h is None or
        df_15m is None or
        df_5m is None or
        df_1m is None
    ):
        return None

    df_1h = add_indicators(df_1h)
    df_15m = add_indicators(df_15m)

    return {
        "1h": df_1h,
        "15m": df_15m,
        "5m": df_5m,
        "1m": df_1m
    }


# ============================================================
# 쿨다운
# ============================================================

def is_cooldown(state):

    if not state:
        return False

    if state.get("status") != "COOLDOWN":
        return False

    cooldown_until = state.get(
        "cooldown_until"
    )

    if not cooldown_until:
        return False

    try:

        until = pd.Timestamp(
            cooldown_until
        )

        now = pd.Timestamp.now(
            tz="UTC"
        )

        if until.tzinfo is None:
            until = until.tz_localize("UTC")

        return now < until

    except Exception:
        return False


# ============================================================
# 포지션 종료
# ============================================================

def close_position(
    state,
    reason,
    price,
    event_time
):

    pos_type = state["type"]

    entry = float(
        state["entry"]
    )

    if pos_type == "LONG":

        if reason == "SL":
            pnl = price - entry
        else:
            pnl = price - entry

    else:

        if reason == "SL":
            pnl = entry - price
        else:
            pnl = entry - price

    message = (
        f"🛑 <b>[골드 선물 포지션 종료]</b>\n\n"
        f"📌 방향: <b>{pos_type}</b>\n"
        f"💰 진입가: <code>${entry:,.2f}</code>\n"
        f"📍 종료가: <code>${price:,.2f}</code>\n"
        f"📊 결과: <b>{reason}</b>\n"
        f"💵 가격 변동: <code>{pnl:+.2f}</code>\n"
        f"⏱ 시간: <code>{event_time}</code>\n\n"
        f"🔗 "
        f"<a href='https://www.tradingview.com/symbols/COMEX-GC1!/'"
        f">TradingView 골드 차트</a>"
    )

    sent = send_telegram(message)

    if not sent:
        print("종료 알림 전송 실패. 상태를 유지합니다.")
        return False

    cooldown_until = (
        pd.Timestamp.now(
            tz="UTC"
        ) +
        pd.Timedelta(
            minutes=COOLDOWN_MINUTES
        )
    )

    new_state = {
        "status": "COOLDOWN",
        "last_type": pos_type,
        "exit_reason": reason,
        "exit_price": price,
        "exit_time": event_time,
        "cooldown_until": cooldown_until.isoformat()
    }

    save_state(new_state)

    log_event(
        "POSITION_EXIT",
        reason,
        {
            "type": pos_type,
            "entry": entry,
            "exit": price,
            "pnl_price": pnl
        }
    )

    return True


# ============================================================
# TP 알림
# ============================================================

def send_tp_alert(
    state,
    target_number,
    target_price,
    event_time
):

    pos_type = state["type"]
    entry = float(
        state["entry"]
    )

    if pos_type == "LONG":
        profit = target_price - entry
    else:
        profit = entry - target_price

    message = (
        f"🎯 <b>[골드 선물 TP{target_number} 도달]</b>\n\n"
        f"📌 방향: <b>{pos_type}</b>\n"
        f"💰 진입가: <code>${entry:,.2f}</code>\n"
        f"🎯 TP{target_number}: "
        f"<code>${target_price:,.2f}</code>\n"
        f"📈 가격 변동: <code>+{profit:.2f}</code>\n"
        f"⏱ 시간: <code>{event_time}</code>\n"
    )

    if target_number == 1:

        message += (
            "\n📌 <b>TP1 도달</b>\n"
            "일부 익절을 고려할 수 있습니다."
        )

    elif target_number == 2:

        message += (
            "\n🛡 <b>TP2 도달</b>\n"
            "보호를 위해 SL을 진입가로 이동합니다."
        )

    else:

        message += (
            "\n🔥 <b>TP3 최종 목표 도달</b>\n"
            "포지션을 종료합니다."
        )

    message += (
        "\n\n🔗 "
        "<a href='https://www.tradingview.com/symbols/COMEX-GC1!/'"
        ">TradingView 골드 차트</a>"
    )

    return send_telegram(message)


# ============================================================
# 기존 포지션 감시
# ============================================================

def monitor_position(state, df_1m):

    pos_type = state["type"]

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

    tp1_sent = state.get(
        "tp1_sent",
        False
    )

    tp2_sent = state.get(
        "tp2_sent",
        False
    )

    tp3_sent = state.get(
        "tp3_sent",
        False
    )

    entry_time = pd.Timestamp(
        state["entry_time"]
    )

    if entry_time.tzinfo is None:
        entry_time = entry_time.tz_localize(
            "UTC"
        )

    # 진입 이후 1분봉만 검사
    bars = df_1m[
        df_1m.index >= entry_time
    ].copy()

    if bars.empty:
        return False

    for index, candle in bars.iterrows():

        high = float(
            candle["High"]
        )

        low = float(
            candle["Low"]
        )

        event_time = str(index)

        # ====================================================
        # LONG
        # ====================================================

        if pos_type == "LONG":

            sl_hit = low <= sl
            tp1_hit = high >= tp1
            tp2_hit = high >= tp2
            tp3_hit = high >= tp3

            # 같은 1분봉에서 SL과 TP가 동시에 발생하면
            # 실제 순서를 알 수 없으므로 보수적으로 SL 우선
            if sl_hit:

                return close_position(
                    state,
                    "SL",
                    sl,
                    event_time
                )

            # TP3
            if tp3_hit and not tp3_sent:

                if not tp1_sent:
                    if send_tp_alert(
                        state,
                        1,
                        tp1,
                        event_time
                    ):
                        state["tp1_sent"] = True
                        tp1_sent = True

                if not tp2_sent:
                    if send_tp_alert(
                        state,
                        2,
                        tp2,
                        event_time
                    ):
                        state["tp2_sent"] = True
                        tp2_sent = True

                        # TP2 이후 본절 이동
                        sl = entry
                        state["sl"] = entry
                        save_state(state)

                if send_tp_alert(
                    state,
                    3,
                    tp3,
                    event_time
                ):
                    state["tp3_sent"] = True
                    save_state(state)

                    return close_position(
                        state,
                        "TP3",
                        tp3,
                        event_time
                    )

            # TP2
            elif tp2_hit and not tp2_sent:

                if not tp1_sent:

                    if send_tp_alert(
                        state,
                        1,
                        tp1,
                        event_time
                    ):
                        state["tp1_sent"] = True
                        tp1_sent = True

                if send_tp_alert(
                    state,
                    2,
                    tp2,
                    event_time
                ):

                    state["tp2_sent"] = True

                    # 본절 이동
                    state["sl"] = entry

                    save_state(state)

                    sl = entry

            # TP1
            elif tp1_hit and not tp1_sent:

                if send_tp_alert(
                    state,
                    1,
                    tp1,
                    event_time
                ):

                    state["tp1_sent"] = True

                    save_state(state)

        # ====================================================
        # SHORT
        # ====================================================

        elif pos_type == "SHORT":

            sl_hit = high >= sl
            tp1_hit = low <= tp1
            tp2_hit = low <= tp2
            tp3_hit = low <= tp3

            # SL 우선
            if sl_hit:

                return close_position(
                    state,
                    "SL",
                    sl,
                    event_time
                )

            # TP3
            if tp3_hit and not tp3_sent:

                if not tp1_sent:

                    if send_tp_alert(
                        state,
                        1,
                        tp1,
                        event_time
                    ):
                        state["tp1_sent"] = True
                        tp1_sent = True

                if not tp2_sent:

                    if send_tp_alert(
                        state,
                        2,
                        tp2,
                        event_time
                    ):
                        state["tp2_sent"] = True
                        tp2_sent = True

                        sl = entry
                        state["sl"] = entry
                        save_state(state)

                if send_tp_alert(
                    state,
                    3,
                    tp3,
                    event_time
                ):

                    state["tp3_sent"] = True
                    save_state(state)

                    return close_position(
                        state,
                        "TP3",
                        tp3,
                        event_time
                    )

            # TP2
            elif tp2_hit and not tp2_sent:

                if not tp1_sent:

                    if send_tp_alert(
                        state,
                        1,
                        tp1,
                        event_time
                    ):
                        state["tp1_sent"] = True
                        tp1_sent = True

                if send_tp_alert(
                    state,
                    2,
                    tp2,
                    event_time
                ):

                    state["tp2_sent"] = True

                    # 본절 이동
                    state["sl"] = entry

                    save_state(state)

                    sl = entry

            # TP1
            elif tp1_hit and not tp1_sent:

                if send_tp_alert(
                    state,
                    1,
                    tp1,
                    event_time
                ):

                    state["tp1_sent"] = True

                    save_state(state)

    return False


# ============================================================
# 핵심 타점 계산
# ============================================================

def find_entry_signal(
    df_1h,
    df_15m,
    df_5m
):

    if (
        len(df_1h) < 60 or
        len(df_15m) < 60 or
        len(df_5m) < 20
    ):
        return None

    h = df_1h.iloc[-1]
    h_prev = df_1h.iloc[-4]

    p = df_15m.iloc[-1]
    p_prev = df_15m.iloc[-2]
    p_prev2 = df_15m.iloc[-3]

    # --------------------------------------------------------
    # 최신 완료 5분봉 가격
    # --------------------------------------------------------

    latest_5m = df_5m.iloc[-1]

    live_price = float(
        latest_5m["Close"]
    )

    # --------------------------------------------------------
    # 1시간 추세
    # --------------------------------------------------------

    bullish_1h = (
        h["Close"] > h["ema20"]
        and
        h["ema20"] > h["ema50"]
        and
        h["ema20"] > h_prev["ema20"]
        and
        h["adx"] >= MIN_ADX_1H
    )

    bearish_1h = (
        h["Close"] < h["ema20"]
        and
        h["ema20"] < h["ema50"]
        and
        h["ema20"] < h_prev["ema20"]
        and
        h["adx"] >= MIN_ADX_1H
    )

    # --------------------------------------------------------
    # 공통 데이터
    # --------------------------------------------------------

    atr = float(
        p["atr"]
    )

    if not np.isfinite(atr) or atr <= 0:
        return None

    ema20 = float(
        p["ema20"]
    )

    rsi = float(
        p["rsi"]
    )

    adx = float(
        p["adx"]
    )

    if not np.isfinite(rsi):
        return None

    if not np.isfinite(adx):
        return None

    # --------------------------------------------------------
    # 너무 멀리 올라간/내려간 가격 추격 방지
    # --------------------------------------------------------

    distance_from_ema = abs(
        float(p["Close"]) - ema20
    )

    if (
        distance_from_ema >
        atr * MAX_ENTRY_DISTANCE_ATR
    ):
        print(
            "가격이 EMA20에서 너무 멀어 "
            "추격 진입을 막았습니다."
        )
        return None

    # --------------------------------------------------------
    # 15분봉 강도
    # --------------------------------------------------------

    current_range = float(
        p["High"] - p["Low"]
    )

    if current_range <= 0:
        return None

    body_ratio = float(
        p["body_ratio"]
    )

    close_position = float(
        p["close_position"]
    )

    # ========================================================
    # LONG 타점
    # ========================================================

    # 직전 봉이 EMA20 근처까지 눌림
    long_pullback = (
        p_prev["Low"]
        <=
        p_prev["ema20"]
        +
        p_prev["atr"] * 0.35
    )

    # 직전 봉이 EMA20을 크게 깨지 않음
    long_hold = (
        p_prev["Close"]
        >=
        p_prev["ema20"]
        -
        p_prev["atr"] * 0.35
    )

    # 현재 봉이 양봉
    long_candle = (
        p["Close"] > p["Open"]
    )

    # 몸통이 어느 정도 있는 캔들
    long_body = (
        body_ratio >= 0.45
    )

    # 종가가 캔들 상단에 위치
    long_close_strength = (
        close_position >= 0.65
    )

    # 이전 봉 고점 돌파
    long_breakout = (
        p["Close"] > p_prev["High"]
    )

    long_rsi = (
        LONG_RSI_MIN
        <=
        rsi
        <=
        LONG_RSI_MAX
    )

    long_adx = (
        adx >= MIN_ADX_15M
    )

    long_signal = (
        bullish_1h
        and
        long_pullback
        and
        long_hold
        and
        long_candle
        and
        long_body
        and
        long_close_strength
        and
        long_breakout
        and
        long_rsi
        and
        long_adx
    )

    # ========================================================
    # SHORT 타점
    # ========================================================

    short_pullback = (
        p_prev["High"]
        >=
        p_prev["ema20"]
        -
        p_prev["atr"] * 0.35
    )

    short_hold = (
        p_prev["Close"]
        <=
        p_prev["ema20"]
        +
        p_prev["atr"] * 0.35
    )

    short_candle = (
        p["Close"] < p["Open"]
    )

    short_body = (
        body_ratio >= 0.45
    )

    short_close_strength = (
        close_position <= 0.35
    )

    # 이전 봉 저점 돌파
    short_breakout = (
        p["Close"] < p_prev["Low"]
    )

    short_rsi = (
        SHORT_RSI_MIN
        <=
        rsi
        <=
        SHORT_RSI_MAX
    )

    short_adx = (
        adx >= MIN_ADX_15M
    )

    short_signal = (
        bearish_1h
        and
        short_pullback
        and
        short_hold
        and
        short_candle
        and
        short_body
        and
        short_close_strength
        and
        short_breakout
        and
        short_rsi
        and
        short_adx
    )

    # ========================================================
    # LONG 확정
    # ========================================================

    if long_signal:

        entry = float(
            p["Close"]
        )

        # 구조적 SL
        structural_sl = min(
            float(p_prev["Low"]),
            float(p_prev2["Low"]),
            float(p["Low"])
        )

        sl = (
            structural_sl
            -
            atr * 0.25
        )

        risk = entry - sl

        # SL이 지나치게 멀면 진입하지 않음
        if risk > atr * MAX_SL_ATR:

            print(
                "LONG SL이 너무 멀어 "
                "진입하지 않습니다."
            )

            return None

        if risk <= 0:
            return None

        tp1 = entry + (
            risk * TP1_R
        )

        tp2 = entry + (
            risk * TP2_R
        )

        tp3 = entry + (
            risk * TP3_R
        )

        # 신호 발생 후 가격이 너무 멀리 움직였는지 확인
        if abs(live_price - entry) > atr * 0.50:

            print(
                "LONG 신호 이후 가격이 너무 이동하여 "
                "추격 진입을 막았습니다."
            )

            return None

        signal_time = p.name

        entry_time = (
            pd.Timestamp(signal_time)
            +
            pd.Timedelta(minutes=15)
        )

        return {
            "type": "LONG",
            "entry": round(entry, 2),
            "sl": round(sl, 2),
            "tp1": round(tp1, 2),
            "tp2": round(tp2, 2),
            "tp3": round(tp3, 2),
            "risk": round(risk, 2),
            "atr": round(atr, 2),
            "rsi": round(rsi, 1),
            "adx": round(adx, 1),
            "signal_time": str(signal_time),
            "entry_time": entry_time.isoformat(),
            "reason": (
                "1시간 상승 추세 + "
                "15분 EMA20 눌림 + "
                "양봉 반전 + "
                "직전 고점 돌파"
            )
        }

    # ========================================================
    # SHORT 확정
    # ========================================================

    if short_signal:

        entry = float(
            p["Close"]
        )

        structural_sl = max(
            float(p_prev["High"]),
            float(p_prev2["High"]),
            float(p["High"])
        )

        sl = (
            structural_sl
            +
            atr * 0.25
        )

        risk = sl - entry

        if risk > atr * MAX_SL_ATR:

            print(
                "SHORT SL이 너무 멀어 "
                "진입하지 않습니다."
            )

            return None

        if risk <= 0:
            return None

        tp1 = entry - (
            risk * TP1_R
        )

        tp2 = entry - (
            risk * TP2_R
        )

        tp3 = entry - (
            risk * TP3_R
        )

        if abs(live_price - entry) > atr * 0.50:

            print(
                "SHORT 신호 이후 가격이 너무 이동하여 "
                "추격 진입을 막았습니다."
            )

            return None

        signal_time = p.name

        entry_time = (
            pd.Timestamp(signal_time)
            +
            pd.Timedelta(minutes=15)
        )

        return {
            "type": "SHORT",
            "entry": round(entry, 2),
            "sl": round(sl, 2),
            "tp1": round(tp1, 2),
            "tp2": round(tp2, 2),
            "tp3": round(tp3, 2),
            "risk": round(risk, 2),
            "atr": round(atr, 2),
            "rsi": round(rsi, 1),
            "adx": round(adx, 1),
            "signal_time": str(signal_time),
            "entry_time": entry_time.isoformat(),
            "reason": (
                "1시간 하락 추세 + "
                "15분 EMA20 눌림 + "
                "음봉 반전 + "
                "직전 저점 돌파"
            )
        }

    return None


# ============================================================
# 진입 알림
# ============================================================

def send_entry_alert(signal):

    direction = signal["type"]

    if direction == "LONG":

        title = "🟢 LONG"

    else:

        title = "🔴 SHORT"

    message = (
        f"👑 <b>[골드 선물 스마트 타점]</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 방향: <b>{title}</b>\n"
        f"💰 진입가: "
        f"<code>${signal['entry']:,.2f}</code>\n\n"

        f"🛡 <b>손절가</b>\n"
        f"<code>${signal['sl']:,.2f}</code>\n\n"

        f"🎯 <b>목표가</b>\n"
        f"TP1: <code>${signal['tp1']:,.2f}</code> "
        f"(1.2R)\n"
        f"TP2: <code>${signal['tp2']:,.2f}</code> "
        f"(2.0R)\n"
        f"TP3: <code>${signal['tp3']:,.2f}</code> "
        f"(3.0R)\n\n"

        f"📐 <b>타점 근거</b>\n"
        f"{signal['reason']}\n\n"

        f"📊 RSI: <code>{signal['rsi']:.1f}</code>\n"
        f"📊 ADX: <code>{signal['adx']:.1f}</code>\n"
        f"📏 ATR: <code>{signal['atr']:.2f}</code>\n"
        f"⚠️ 위험폭: <code>{signal['risk']:.2f}</code>\n\n"

        f"⏱ 신호봉: "
        f"<code>{signal['signal_time']}</code>\n\n"

        f"🔗 "
        f"<a href='https://www.tradingview.com/symbols/COMEX-GC1!/'"
        f">TradingView 골드 차트</a>\n\n"

        f"⚡ <i>추세 → 눌림 → 반전 → 돌파 확인 방식</i>"
    )

    return send_telegram(message)


# ============================================================
# 메인
# ============================================================

def main():

    print("=" * 60)
    print("골드 선물 스마트 타점 봇 시작")
    print("=" * 60)

    data = get_market_data()

    if not data:

        print("시장 데이터를 가져오지 못했습니다.")

        log_event(
            "DATA_ERROR",
            "시장 데이터 조회 실패"
        )

        return

    df_1h = data["1h"]
    df_15m = data["15m"]
    df_5m = data["5m"]
    df_1m = data["1m"]

    print(
        f"1H: {len(df_1h)} / "
        f"15M: {len(df_15m)} / "
        f"5M: {len(df_5m)} / "
        f"1M: {len(df_1m)}"
    )

    # --------------------------------------------------------
    # 기존 포지션 확인
    # --------------------------------------------------------

    state = load_state()

    if state:

        status = state.get(
            "status",
            "ACTIVE"
        )

        # ----------------------------------------------------
        # 쿨다운
        # ----------------------------------------------------

        if status == "COOLDOWN":

            if is_cooldown(state):

                print(
                    "현재 쿨다운 중입니다."
                )

                print(
                    "다음 진입 가능:",
                    state.get(
                        "cooldown_until"
                    )
                )

                return

            else:

                print(
                    "쿨다운 종료. "
                    "새로운 타점을 검색합니다."
                )

                clear_state()

                state = None

        # ----------------------------------------------------
        # ACTIVE 포지션
        # ----------------------------------------------------

        elif status == "ACTIVE":

            print(
                "기존 포지션 감시:",
                state.get("type")
            )

            # 진입 알림이 실패했으면 재전송
            if not state.get(
                "entry_alert_sent",
                False
            ):

                signal_copy = {
                    "type": state["type"],
                    "entry": state["entry"],
                    "sl": state["sl"],
                    "tp1": state["tp1"],
                    "tp2": state["tp2"],
                    "tp3": state["tp3"],
                    "risk": state.get(
                        "risk",
                        0
                    ),
                    "atr": state.get(
                        "atr",
                        0
                    ),
                    "rsi": state.get(
                        "rsi",
                        0
                    ),
                    "adx": state.get(
                        "adx",
                        0
                    ),
                    "signal_time": state.get(
                        "signal_time",
                        ""
                    ),
                    "reason": state.get(
                        "reason",
                        ""
                    )
                }

                if send_entry_alert(
                    signal_copy
                ):

                    state[
                        "entry_alert_sent"
                    ] = True

                    save_state(state)

            monitor_position(
                state,
                df_1m
            )

            return

    # --------------------------------------------------------
    # 새로운 타점 검색
    # --------------------------------------------------------

    print("새로운 타점 검색 중...")

    signal = find_entry_signal(
        df_1h,
        df_15m,
        df_5m
    )

    if not signal:

        print(
            "현재 조건을 모두 만족하는 "
            "고품질 타점이 없습니다."
        )

        return

    print(
        "신호 발생:",
        signal["type"]
    )

    print(
        "Entry:",
        signal["entry"]
    )

    print(
        "SL:",
        signal["sl"]
    )

    print(
        "TP1:",
        signal["tp1"]
    )

    print(
        "TP2:",
        signal["tp2"]
    )

    print(
        "TP3:",
        signal["tp3"]
    )

    # --------------------------------------------------------
    # 상태 저장
    # --------------------------------------------------------

    new_state = {
        "status": "ACTIVE",

        "type": signal["type"],

        "entry": signal["entry"],

        "sl": signal["sl"],

        "tp1": signal["tp1"],

        "tp2": signal["tp2"],

        "tp3": signal["tp3"],

        "risk": signal["risk"],

        "atr": signal["atr"],

        "rsi": signal["rsi"],

        "adx": signal["adx"],

        "signal_time": signal[
            "signal_time"
        ],

        "entry_time": signal[
            "entry_time"
        ],

        "reason": signal[
            "reason"
        ],

        "tp1_sent": False,

        "tp2_sent": False,

        "tp3_sent": False,

        "entry_alert_sent": False
    }

    save_state(
        new_state
    )

    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    if send_entry_alert(
        signal
    ):

        new_state[
            "entry_alert_sent"
        ] = True

        save_state(
            new_state
        )

        log_event(
            "NEW_SIGNAL",
            signal["type"],
            signal
        )

        print(
            "Telegram 진입 알림 전송 완료"
        )

    else:

        print(
            "Telegram 전송 실패. "
            "다음 실행에서 재전송합니다."
        )

        log_event(
            "TELEGRAM_ERROR",
            "진입 알림 전송 실패"
        )


# ============================================================
# 실행
# ============================================================

if __name__ == "__main__":
    main()
