import os
import json
import time
from datetime import datetime, timezone

import requests
import yfinance as yf
import pandas as pd
import numpy as np


# ============================================================
# GOLD FUTURES SMART SIGNAL BOT
# ============================================================

TICKER = "GC=F"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

STATE_FILE = "signal_state.json"
LOG_FILE = "bot_log.json"


# ============================================================
# 전략 설정
# ============================================================

# TP
TP1_R = 1.20
TP2_R = 2.00
TP3_R = 3.00

# SL
MAX_SL_ATR = 2.50

# EMA에서 너무 멀리 떨어진 가격 추격 금지
MAX_ENTRY_DISTANCE_ATR = 1.00

# 신호 이후 가격이 너무 움직이면 진입하지 않음
MAX_SIGNAL_MOVE_ATR = 0.50

# ADX
MIN_ADX_1H = 18
MIN_ADX_15M = 16

# RSI
LONG_RSI_MIN = 52
LONG_RSI_MAX = 68

SHORT_RSI_MIN = 32
SHORT_RSI_MAX = 48

# 재진입 대기
COOLDOWN_MINUTES = 60


# ============================================================
# Telegram
# ============================================================

def send_telegram(message):

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:

        print("Telegram 환경변수가 없습니다.")

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
            timeout=15
        )

        if response.status_code != 200:

            print(
                "Telegram HTTP 오류:",
                response.text
            )

            return False

        result = response.json()

        if not result.get("ok"):

            print(
                "Telegram API 오류:",
                result
            )

            return False

        return True

    except Exception as e:

        print(
            "Telegram 전송 오류:",
            e
        )

        return False


# ============================================================
# 시간
# ============================================================

def utc_now():

    return pd.Timestamp.now(
        tz="UTC"
    )


def normalize_timestamp(value):

    try:

        ts = pd.Timestamp(value)

        if ts.tzinfo is None:

            ts = ts.tz_localize(
                "UTC"
            )

        else:

            ts = ts.tz_convert(
                "UTC"
            )

        return ts

    except Exception:

        return None


# ============================================================
# JSON 상태
# ============================================================

def load_state():

    if not os.path.exists(
        STATE_FILE
    ):

        return None

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if not isinstance(
            data,
            dict
        ):

            return None

        return data

    except Exception as e:

        print(
            "상태 파일 읽기 실패:",
            e
        )

        return None


def save_state(state):

    temp_file = (
        STATE_FILE +
        ".tmp"
    )

    try:

        with open(
            temp_file,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                state,
                f,
                indent=2,
                ensure_ascii=False
            )

        os.replace(
            temp_file,
            STATE_FILE
        )

        return True

    except Exception as e:

        print(
            "상태 저장 실패:",
            e
        )

        return False


def clear_state():

    try:

        if os.path.exists(
            STATE_FILE
        ):

            os.remove(
                STATE_FILE
            )

    except Exception as e:

        print(
            "상태 삭제 실패:",
            e
        )


# ============================================================
# 거래 로그
# ============================================================

def log_event(
    event_type,
    message,
    data=None
):

    logs = []

    if os.path.exists(
        LOG_FILE
    ):

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

    event = {
        "time_utc":
            utc_now().isoformat(),

        "event":
            event_type,

        "message":
            message
    }

    if data is not None:

        event["data"] = data

    logs.append(
        event
    )

    # 로그가 무한히 커지는 것을 방지
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

        print(
            "로그 저장 실패:",
            e
        )


# ============================================================
# 구버전 상태 자동 변환
# ============================================================

def migrate_state(state):

    if not state:

        return None

    changed = False

    # --------------------------------------------------------
    # type 확인
    # --------------------------------------------------------

    if "type" not in state:

        print(
            "상태 파일에 type이 없습니다."
        )

        return None

    # --------------------------------------------------------
    # status 추가
    # --------------------------------------------------------

    if "status" not in state:

        state["status"] = "ACTIVE"

        changed = True

    # --------------------------------------------------------
    # TP 상태
    # --------------------------------------------------------

    if "tp1_sent" not in state:

        state["tp1_sent"] = False

        changed = True

    if "tp2_sent" not in state:

        state["tp2_sent"] = False

        changed = True

    if "tp3_sent" not in state:

        state["tp3_sent"] = False

        changed = True

    # --------------------------------------------------------
    # 진입 알림 상태
    # --------------------------------------------------------

    if "entry_alert_sent" not in state:

        # 기존 포지션은 이미 과거 봇에서 알림이 갔을
        # 가능성이 높으므로 True로 설정
        state["entry_alert_sent"] = True

        changed = True

    # --------------------------------------------------------
    # entry_time
    # --------------------------------------------------------

    if not state.get(
        "entry_time"
    ):

        # 구버전 상태에는 진입 시간이 없으므로
        # 최근 15분을 감시 범위로 설정
        fallback_time = (
            utc_now()
            -
            pd.Timedelta(
                minutes=15
            )
        )

        state["entry_time"] = (
            fallback_time.isoformat()
        )

        changed = True

        print(
            "구버전 포지션: "
            "entry_time 자동 생성"
        )

    # --------------------------------------------------------
    # signal_time
    # --------------------------------------------------------

    if not state.get(
        "signal_time"
    ):

        state["signal_time"] = (
            state["entry_time"]
        )

        changed = True

    # --------------------------------------------------------
    # risk
    # --------------------------------------------------------

    if "risk" not in state:

        try:

            entry = float(
                state["entry"]
            )

            sl = float(
                state["sl"]
            )

            state["risk"] = round(
                abs(entry - sl),
                2
            )

            changed = True

        except Exception:

            state["risk"] = 0

    # --------------------------------------------------------
    # ATR / RSI / ADX
    # --------------------------------------------------------

    state.setdefault(
        "atr",
        0
    )

    state.setdefault(
        "rsi",
        0
    )

    state.setdefault(
        "adx",
        0
    )

    state.setdefault(
        "reason",
        "기존 포지션"
    )

    if changed:

        save_state(
            state
        )

        print(
            "구버전 상태를 "
            "새 형식으로 변환했습니다."
        )

    return state


# ============================================================
# Yahoo Finance 데이터
# ============================================================

def get_history(
    interval,
    period
):

    for attempt in range(3):

        try:

            ticker = yf.Ticker(
                TICKER
            )

            df = ticker.history(
                period=period,
                interval=interval,
                auto_adjust=False,
                prepost=False
            )

            if df is None or df.empty:

                raise ValueError(
                    "데이터가 비어 있습니다."
                )

            # MultiIndex 처리
            if isinstance(
                df.columns,
                pd.MultiIndex
            ):

                df.columns = (
                    df.columns
                    .get_level_values(0)
                )

            required_columns = [
                "Open",
                "High",
                "Low",
                "Close"
            ]

            for column in required_columns:

                if column not in df.columns:

                    raise ValueError(
                        f"{column} 데이터 없음"
                    )

            df = df.dropna(
                subset=required_columns
            ).copy()

            df = df.sort_index()

            if len(df) < 30:

                raise ValueError(
                    f"{interval} 데이터 부족: "
                    f"{len(df)}개"
                )

            # =================================================
            # 시간대 UTC 통일
            # =================================================

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

        except Exception as e:

            print(
                f"{interval} 데이터 오류 "
                f"({attempt + 1}/3):",
                e
            )

            time.sleep(2)

    return None


# ============================================================
# 미완성 캔들 제거
# ============================================================

def remove_incomplete_bar(
    df
):

    if df is None:

        return None

    if len(df) < 5:

        return None

    # Yahoo의 마지막 캔들은 현재 진행 중일 수 있으므로 제거
    return df.iloc[:-1].copy()


# ============================================================
# 기술지표
# ============================================================

def add_indicators(df):

    df = df.copy()

    # --------------------------------------------------------
    # EMA
    # --------------------------------------------------------

    df["ema20"] = (
        df["Close"]
        .ewm(
            span=20,
            adjust=False
        )
        .mean()
    )

    df["ema50"] = (
        df["Close"]
        .ewm(
            span=50,
            adjust=False
        )
        .mean()
    )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    delta = (
        df["Close"]
        .diff()
    )

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

    df["rsi"] = (
        100 -
        (
            100 /
            (1 + rs)
        )
    )

    # --------------------------------------------------------
    # ATR
    # --------------------------------------------------------

    previous_close = (
        df["Close"]
        .shift(1)
    )

    tr1 = (
        df["High"] -
        df["Low"]
    )

    tr2 = (
        df["High"] -
        previous_close
    ).abs()

    tr3 = (
        df["Low"] -
        previous_close
    ).abs()

    true_range = pd.concat(
        [
            tr1,
            tr2,
            tr3
        ],
        axis=1
    ).max(
        axis=1
    )

    df["atr"] = (
        true_range
        .ewm(
            alpha=1 / 14,
            adjust=False
        )
        .mean()
    )

    # --------------------------------------------------------
    # ADX
    # --------------------------------------------------------

    up_move = (
        df["High"]
        .diff()
    )

    down_move = -(
        df["Low"]
        .diff()
    )

    plus_dm = pd.Series(
        np.where(
            (
                up_move >
                down_move
            )
            &
            (
                up_move > 0
            ),
            up_move,
            0
        ),
        index=df.index
    )

    minus_dm = pd.Series(
        np.where(
            (
                down_move >
                up_move
            )
            &
            (
                down_move > 0
            ),
            down_move,
            0
        ),
        index=df.index
    )

    atr_safe = (
        df["atr"]
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

    di_sum = (
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
        di_sum
    )

    df["adx"] = (
        dx
        .ewm(
            alpha=1 / 14,
            adjust=False
        )
        .mean()
    )

    # --------------------------------------------------------
    # 캔들 정보
    # --------------------------------------------------------

    df["body"] = (
        df["Close"] -
        df["Open"]
    ).abs()

    df["range"] = (
        df["High"] -
        df["Low"]
    )

    df["body_ratio"] = (
        df["body"] /
        df["range"].replace(
            0,
            np.nan
        )
    )

    df["close_position"] = (
        (
            df["Close"] -
            df["Low"]
        )
        /
        df["range"].replace(
            0,
            np.nan
        )
    )

    return df


# ============================================================
# 시장 데이터
# ============================================================

def get_market_data():

    df_1h = get_history(
        "1h",
        "30d"
    )

    df_15m = get_history(
        "15m",
        "10d"
    )

    df_5m = get_history(
        "5m",
        "5d"
    )

    df_1m = get_history(
        "1m",
        "5d"
    )

    if (
        df_1h is None
        or
        df_15m is None
        or
        df_5m is None
        or
        df_1m is None
    ):

        return None

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

    if (
        df_1h is None
        or
        df_15m is None
        or
        df_5m is None
        or
        df_1m is None
    ):

        return None

    df_1h = add_indicators(
        df_1h
    )

    df_15m = add_indicators(
        df_15m
    )

    return {
        "1h": df_1h,
        "15m": df_15m,
        "5m": df_5m,
        "1m": df_1m
    }


# ============================================================
# 쿨다운 확인
# ============================================================

def is_in_cooldown(
    state
):

    if not state:

        return False

    if state.get(
        "status"
    ) != "COOLDOWN":

        return False

    cooldown_until = normalize_timestamp(
        state.get(
            "cooldown_until"
        )
    )

    if cooldown_until is None:

        return False

    return (
        utc_now() <
        cooldown_until
    )


# ============================================================
# 포지션 종료
# ============================================================

def close_position(
    state,
    reason,
    price,
    event_time
):

    direction = state[
        "type"
    ]

    entry = float(
        state["entry"]
    )

    price = float(
        price
    )

    if direction == "LONG":

        pnl = (
            price -
            entry
        )

    else:

        pnl = (
            entry -
            price
        )

    # --------------------------------------------------------
    # 메시지
    # --------------------------------------------------------

    if reason == "SL":

        title = (
            "🛑 손절가 도달"
        )

    elif reason == "TP3":

        title = (
            "🔥 TP3 최종 익절"
        )

    elif reason == "BE":

        title = (
            "🛡 본절 종료"
        )

    else:

        title = (
            "📌 포지션 종료"
        )

    message = (
        f"<b>[골드 선물 포지션 종료]</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{title}\n\n"

        f"📌 방향: "
        f"<b>{direction}</b>\n"

        f"💰 진입가: "
        f"<code>${entry:,.2f}</code>\n"

        f"📍 종료가: "
        f"<code>${price:,.2f}</code>\n"

        f"📊 결과: "
        f"<b>{reason}</b>\n"

        f"💵 가격 변동: "
        f"<code>{pnl:+.2f}</code>\n"

        f"⏱ 시간: "
        f"<code>{event_time}</code>\n\n"

        f"🔗 "
        f"<a href='https://www.tradingview.com/symbols/COMEX-GC1!/'>"
        f"TradingView 골드 차트"
        f"</a>"
    )

    if not send_telegram(
        message
    ):

        print(
            "종료 알림 실패."
            " 상태를 유지합니다."
        )

        return False

    cooldown_until = (
        utc_now()
        +
        pd.Timedelta(
            minutes=COOLDOWN_MINUTES
        )
    )

    new_state = {

        "status":
            "COOLDOWN",

        "last_type":
            direction,

        "exit_reason":
            reason,

        "exit_price":
            price,

        "exit_time":
            str(event_time),

        "cooldown_until":
            cooldown_until.isoformat()
    }

    save_state(
        new_state
    )

    log_event(
        "POSITION_EXIT",
        reason,
        {
            "type":
                direction,

            "entry":
                entry,

            "exit":
                price,

            "pnl":
                pnl
        }
    )

    return True


# ============================================================
# TP 알림
# ============================================================

def send_tp_alert(
    state,
    number,
    price,
    event_time
):

    direction = state[
        "type"
    ]

    entry = float(
        state["entry"]
    )

    price = float(
        price
    )

    if direction == "LONG":

        profit = (
            price -
            entry
        )

    else:

        profit = (
            entry -
            price
        )

    message = (
        f"🎯 <b>[골드 선물 TP{number} 도달]</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"

        f"📌 방향: "
        f"<b>{direction}</b>\n"

        f"💰 진입가: "
        f"<code>${entry:,.2f}</code>\n"

        f"🎯 TP{number}: "
        f"<code>${price:,.2f}</code>\n"

        f"📈 가격 변동: "
        f"<code>+{profit:.2f}</code>\n"

        f"⏱ 시간: "
        f"<code>{event_time}</code>\n"
    )

    if number == 1:

        message += (
            "\n📌 <b>TP1 도달</b>\n"
            "일부 익절을 고려할 수 있습니다."
        )

    elif number == 2:

        message += (
            "\n🛡 <b>TP2 도달</b>\n"
            "SL을 진입가로 이동합니다."
        )

    elif number == 3:

        message += (
            "\n🔥 <b>TP3 도달</b>\n"
            "최종 목표에 도달했습니다."
        )

    message += (
        "\n\n🔗 "
        "<a href='https://www.tradingview.com/symbols/COMEX-GC1!/'>"
        "TradingView 골드 차트"
        "</a>"
    )

    return send_telegram(
        message
    )


# ============================================================
# 기존 포지션 감시
# ============================================================

def monitor_position(
    state,
    df_1m
):

    direction = state[
        "type"
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

    # --------------------------------------------------------
    # entry_time 안전 처리
    # --------------------------------------------------------

    entry_time = normalize_timestamp(
        state.get(
            "entry_time"
        )
    )

    if entry_time is None:

        print(
            "entry_time이 없습니다."
        )

        entry_time = (
            utc_now()
            -
            pd.Timedelta(
                minutes=15
            )
        )

        state[
            "entry_time"
        ] = entry_time.isoformat()

        save_state(
            state
        )

    # --------------------------------------------------------
    # 1분봉 시간대 UTC 통일
    # --------------------------------------------------------

    try:

        if df_1m.index.tz is None:

            df_1m.index = (
                df_1m.index
                .tz_localize(
                    "UTC"
                )
            )

        else:

            df_1m.index = (
                df_1m.index
                .tz_convert(
                    "UTC"
                )
            )

    except Exception as e:

        print(
            "1분봉 timezone 오류:",
            e
        )

        return False

    # --------------------------------------------------------
    # 진입 이후 1분봉
    # --------------------------------------------------------

    bars = df_1m[
        df_1m.index >= entry_time
    ].copy()

    if bars.empty:

        print(
            "진입 이후 1분봉 데이터 없음"
        )

        return False

    # --------------------------------------------------------
    # 각 1분봉 순서대로 검사
    # --------------------------------------------------------

    for index, candle in bars.iterrows():

        high = float(
            candle["High"]
        )

        low = float(
            candle["Low"]
        )

        event_time = str(
            index
        )

        # ====================================================
        # LONG
        # ====================================================

        if direction == "LONG":

            sl_hit = (
                low <= sl
            )

            tp1_hit = (
                high >= tp1
            )

            tp2_hit = (
                high >= tp2
            )

            tp3_hit = (
                high >= tp3
            )

            # ------------------------------------------------
            # 같은 1분봉에서 SL + TP 동시 발생
            # 실제 순서를 알 수 없으므로 SL 우선
            # ------------------------------------------------

            if sl_hit:

                return close_position(
                    state,
                    "SL",
                    sl,
                    event_time
                )

            # ------------------------------------------------
            # TP3
            # ------------------------------------------------

            if (
                tp3_hit
                and
                not tp3_sent
            ):

                # TP1 누락 방지
                if not tp1_sent:

                    if send_tp_alert(
                        state,
                        1,
                        tp1,
                        event_time
                    ):

                        state[
                            "tp1_sent"
                        ] = True

                        tp1_sent = True

                # TP2 누락 방지
                if not tp2_sent:

                    if send_tp_alert(
                        state,
                        2,
                        tp2,
                        event_time
                    ):

                        state[
                            "tp2_sent"
                        ] = True

                        tp2_sent = True

                        # 본절 이동
                        state[
                            "sl"
                        ] = entry

                        sl = entry

                        save_state(
                            state
                        )

                if send_tp_alert(
                    state,
                    3,
                    tp3,
                    event_time
                ):

                    state[
                        "tp3_sent"
                    ] = True

                    save_state(
                        state
                    )

                    return close_position(
                        state,
                        "TP3",
                        tp3,
                        event_time
                    )

            # ------------------------------------------------
            # TP2
            # ------------------------------------------------

            elif (
                tp2_hit
                and
                not tp2_sent
            ):

                if not tp1_sent:

                    if send_tp_alert(
                        state,
                        1,
                        tp1,
                        event_time
                    ):

                        state[
                            "tp1_sent"
                        ] = True

                        tp1_sent = True

                if send_tp_alert(
                    state,
                    2,
                    tp2,
                    event_time
                ):

                    state[
                        "tp2_sent"
                    ] = True

                    # TP2 이후 본절
                    state[
                        "sl"
                    ] = entry

                    sl = entry

                    save_state(
                        state
                    )

            # ------------------------------------------------
            # TP1
            # ------------------------------------------------

            elif (
                tp1_hit
                and
                not tp1_sent
            ):

                if send_tp_alert(
                    state,
                    1,
                    tp1,
                    event_time
                ):

                    state[
                        "tp1_sent"
                    ] = True

                    save_state(
                        state
                    )

        # ====================================================
        # SHORT
        # ====================================================

        elif direction == "SHORT":

            sl_hit = (
                high >= sl
            )

            tp1_hit = (
                low <= tp1
            )

            tp2_hit = (
                low <= tp2
            )

            tp3_hit = (
                low <= tp3
            )

            # ------------------------------------------------
            # SL
            # ------------------------------------------------

            if sl_hit:

                return close_position(
                    state,
                    "SL",
                    sl,
                    event_time
                )

            # ------------------------------------------------
            # TP3
            # ------------------------------------------------

            if (
                tp3_hit
                and
                not tp3_sent
            ):

                if not tp1_sent:

                    if send_tp_alert(
                        state,
                        1,
                        tp1,
                        event_time
                    ):

                        state[
                            "tp1_sent"
                        ] = True

                        tp1_sent = True

                if not tp2_sent:

                    if send_tp_alert(
                        state,
                        2,
                        tp2,
                        event_time
                    ):

                        state[
                            "tp2_sent"
                        ] = True

                        tp2_sent = True

                        # 본절 이동
                        state[
                            "sl"
                        ] = entry

                        sl = entry

                        save_state(
                            state
                        )

                if send_tp_alert(
                    state,
                    3,
                    tp3,
                    event_time
                ):

                    state[
                        "tp3_sent"
                    ] = True

                    save_state(
                        state
                    )

                    return close_position(
                        state,
                        "TP3",
                        tp3,
                        event_time
                    )

            # ------------------------------------------------
            # TP2
            # ------------------------------------------------

            elif (
                tp2_hit
                and
                not tp2_sent
            ):

                if not tp1_sent:

                    if send_tp_alert(
                        state,
                        1,
                        tp1,
                        event_time
                    ):

                        state[
                            "tp1_sent"
                        ] = True

                        tp1_sent = True

                if send_tp_alert(
                    state,
                    2,
                    tp2,
                    event_time
                ):

                    state[
                        "tp2_sent"
                    ] = True

                    # 본절 이동
                    state[
                        "sl"
                    ] = entry

                    sl = entry

                    save_state(
                        state
                    )

            # ------------------------------------------------
            # TP1
            # ------------------------------------------------

            elif (
                tp1_hit
                and
                not tp1_sent
            ):

                if send_tp_alert(
                    state,
                    1,
                    tp1,
                    event_time
                ):

                    state[
                        "tp1_sent"
                    ] = True

                    save_state(
                        state
                    )

    return False


# ============================================================
# 핵심 타점 시스템
# ============================================================

def find_entry_signal(
    df_1h,
    df_15m,
    df_5m
):

    if (
        len(df_1h) < 60
        or
        len(df_15m) < 60
        or
        len(df_5m) < 20
    ):

        return None

    # --------------------------------------------------------
    # 최근 완성봉
    # --------------------------------------------------------

    h = df_1h.iloc[-1]

    h_prev = df_1h.iloc[-4]

    p = df_15m.iloc[-1]

    p_prev = df_15m.iloc[-2]

    p_prev2 = df_15m.iloc[-3]

    latest_5m = df_5m.iloc[-1]

    live_price = float(
        latest_5m["Close"]
    )

    # --------------------------------------------------------
    # 1H 추세
    # --------------------------------------------------------

    bullish_1h = (

        h["Close"]
        >
        h["ema20"]

        and

        h["ema20"]
        >
        h["ema50"]

        and

        h["ema20"]
        >
        h_prev["ema20"]

        and

        h["adx"]
        >=
        MIN_ADX_1H
    )

    bearish_1h = (

        h["Close"]
        <
        h["ema20"]

        and

        h["ema20"]
        <
        h["ema50"]

        and

        h["ema20"]
        <
        h_prev["ema20"]

        and

        h["adx"]
        >=
        MIN_ADX_1H
    )

    # --------------------------------------------------------
    # 15분 지표
    # --------------------------------------------------------

    atr = float(
        p["atr"]
    )

    ema20 = float(
        p["ema20"]
    )

    rsi = float(
        p["rsi"]
    )

    adx = float(
        p["adx"]
    )

    if not np.isfinite(
        atr
    ):

        return None

    if atr <= 0:

        return None

    if not np.isfinite(
        rsi
    ):

        return None

    if not np.isfinite(
        adx
    ):

        return None

    # --------------------------------------------------------
    # 추격 진입 방지
    # --------------------------------------------------------

    distance_from_ema = abs(
        float(p["Close"])
        -
        ema20
    )

    if (
        distance_from_ema
        >
        atr *
        MAX_ENTRY_DISTANCE_ATR
    ):

        print(
            "EMA20에서 너무 멀어 "
            "추격 진입 방지"
        )

        return None

    # --------------------------------------------------------
    # 캔들 특성
    # --------------------------------------------------------

    body_ratio = float(
        p["body_ratio"]
    )

    close_position = float(
        p["close_position"]
    )

    if not np.isfinite(
        body_ratio
    ):

        return None

    if not np.isfinite(
        close_position
    ):

        return None

    # ========================================================
    # LONG
    # ========================================================

    # 1. 눌림
    long_pullback = (

        p_prev["Low"]
        <=
        (
            p_prev["ema20"]
            +
            p_prev["atr"] *
            0.35
        )
    )

    # 2. 눌림 후 EMA20 방어
    long_hold = (

        p_prev["Close"]
        >=
        (
            p_prev["ema20"]
            -
            p_prev["atr"] *
            0.35
        )
    )

    # 3. 현재 양봉
    long_candle = (
        p["Close"]
        >
        p["Open"]
    )

    # 4. 몸통 강도
    long_body = (
        body_ratio
        >=
        0.45
    )

    # 5. 캔들 상단 마감
    long_close_strength = (
        close_position
        >=
        0.65
    )

    # 6. 직전 고점 돌파
    long_breakout = (
        p["Close"]
        >
        p_prev["High"]
    )

    # 7. RSI
    long_rsi = (
        LONG_RSI_MIN
        <=
        rsi
        <=
        LONG_RSI_MAX
    )

    # 8. ADX
    long_adx = (
        adx
        >=
        MIN_ADX_15M
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
    # SHORT
    # ========================================================

    # 1. 반등
    short_pullback = (

        p_prev["High"]
        >=
        (
            p_prev["ema20"]
            -
            p_prev["atr"] *
            0.35
        )
    )

    # 2. EMA20 아래에서 저항
    short_hold = (

        p_prev["Close"]
        <=
        (
            p_prev["ema20"]
            +
            p_prev["atr"] *
            0.35
        )
    )

    # 3. 현재 음봉
    short_candle = (
        p["Close"]
        <
        p["Open"]
    )

    # 4. 몸통 강도
    short_body = (
        body_ratio
        >=
        0.45
    )

    # 5. 캔들 하단 마감
    short_close_strength = (
        close_position
        <=
        0.35
    )

    # 6. 직전 저점 돌파
    short_breakout = (
        p["Close"]
        <
        p_prev["Low"]
    )

    # 7. RSI
    short_rsi = (
        SHORT_RSI_MIN
        <=
        rsi
        <=
        SHORT_RSI_MAX
    )

    # 8. ADX
    short_adx = (
        adx
        >=
        MIN_ADX_15M
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
    # LONG 생성
    # ========================================================

    if long_signal:

        entry = float(
            p["Close"]
        )

        # 최근 구조적 저점
        structural_sl = min(
            float(
                p_prev["Low"]
            ),

            float(
                p_prev2["Low"]
            ),

            float(
                p["Low"]
            )
        )

        # 구조적 저점 아래 ATR 버퍼
        sl = (
            structural_sl
            -
            atr *
            0.25
        )

        risk = (
            entry -
            sl
        )

        # 위험폭이 너무 크면 거래하지 않음
        if (
            risk <= 0
            or
            risk >
            atr *
            MAX_SL_ATR
        ):

            print(
                "LONG SL 위험폭 과다"
            )

            return None

        # TP
        tp1 = (
            entry +
            risk *
            TP1_R
        )

        tp2 = (
            entry +
            risk *
            TP2_R
        )

        tp3 = (
            entry +
            risk *
            TP3_R
        )

        # 신호 발생 후 가격 추격 방지
        if (
            abs(
                live_price -
                entry
            )
            >
            atr *
            MAX_SIGNAL_MOVE_ATR
        ):

            print(
                "LONG 신호 후 "
                "가격 추격 방지"
            )

            return None

        signal_time = p.name

        entry_time = (
            pd.Timestamp(
                signal_time
            )
            +
            pd.Timedelta(
                minutes=15
            )
        )

        return {

            "type":
                "LONG",

            "entry":
                round(
                    entry,
                    2
                ),

            "sl":
                round(
                    sl,
                    2
                ),

            "tp1":
                round(
                    tp1,
                    2
                ),

            "tp2":
                round(
                    tp2,
                    2
                ),

            "tp3":
                round(
                    tp3,
                    2
                ),

            "risk":
                round(
                    risk,
                    2
                ),

            "atr":
                round(
                    atr,
                    2
                ),

            "rsi":
                round(
                    rsi,
                    1
                ),

            "adx":
                round(
                    adx,
                    1
                ),

            "signal_time":
                str(
                    signal_time
                ),

            "entry_time":
                entry_time.isoformat(),

            "reason":
                (
                    "1H 상승추세 + "
                    "15M EMA20 눌림 + "
                    "지지 확인 + "
                    "양봉 반전 + "
                    "직전 고점 돌파"
                )
        }

    # ========================================================
    # SHORT 생성
    # ========================================================

    if short_signal:

        entry = float(
            p["Close"]
        )

        # 최근 구조적 고점
        structural_sl = max(
            float(
                p_prev["High"]
            ),

            float(
                p_prev2["High"]
            ),

            float(
                p["High"]
            )
        )

        # 구조적 고점 위 ATR 버퍼
        sl = (
            structural_sl
            +
            atr *
            0.25
        )

        risk = (
            sl -
            entry
        )

        if (
            risk <= 0
            or
            risk >
            atr *
            MAX_SL_ATR
        ):

            print(
                "SHORT SL 위험폭 과다"
            )

            return None

        # TP
        tp1 = (
            entry -
            risk *
            TP1_R
        )

        tp2 = (
            entry -
            risk *
            TP2_R
        )

        tp3 = (
            entry -
            risk *
            TP3_R
        )

        # 추격 진입 방지
        if (
            abs(
                live_price -
                entry
            )
            >
            atr *
            MAX_SIGNAL_MOVE_ATR
        ):

            print(
                "SHORT 신호 후 "
                "가격 추격 방지"
            )

            return None

        signal_time = p.name

        entry_time = (
            pd.Timestamp(
                signal_time
            )
            +
            pd.Timedelta(
                minutes=15
            )
        )

        return {

            "type":
                "SHORT",

            "entry":
                round(
                    entry,
                    2
                ),

            "sl":
                round(
                    sl,
                    2
                ),

            "tp1":
                round(
                    tp1,
                    2
                ),

            "tp2":
                round(
                    tp2,
                    2
                ),

            "tp3":
                round(
                    tp3,
                    2
                ),

            "risk":
                round(
                    risk,
                    2
                ),

            "atr":
                round(
                    atr,
                    2
                ),

            "rsi":
                round(
                    rsi,
                    1
                ),

            "adx":
                round(
                    adx,
                    1
                ),

            "signal_time":
                str(
                    signal_time
                ),

            "entry_time":
                entry_time.isoformat(),

            "reason":
                (
                    "1H 하락추세 + "
                    "15M EMA20 반등 + "
                    "저항 확인 + "
                    "음봉 반전 + "
                    "직전 저점 돌파"
                )
        }

    return None


# ============================================================
# 진입 알림
# ============================================================

def send_entry_alert(
    signal
):

    direction = signal[
        "type"
    ]

    if direction == "LONG":

        title = (
            "🟢 LONG"
        )

    else:

        title = (
            "🔴 SHORT"
        )

    message = (

        f"👑 "
        f"<b>[골드 선물 스마트 타점]</b>\n"

        f"━━━━━━━━━━━━━━━━━━\n"

        f"📊 방향: "
        f"<b>{title}</b>\n"

        f"💰 진입가: "
        f"<code>${signal['entry']:,.2f}</code>\n\n"

        f"🛡 손절가: "
        f"<code>${signal['sl']:,.2f}</code>\n\n"

        f"🎯 목표가\n"

        f"TP1: "
        f"<code>${signal['tp1']:,.2f}</code> "
        f"(1.2R)\n"

        f"TP2: "
        f"<code>${signal['tp2']:,.2f}</code> "
        f"(2.0R)\n"

        f"TP3: "
        f"<code>${signal['tp3']:,.2f}</code> "
        f"(3.0R)\n\n"

        f"📐 타점 근거\n"
        f"{signal['reason']}\n\n"

        f"📊 RSI: "
        f"<code>{signal['rsi']:.1f}</code>\n"

        f"📊 ADX: "
        f"<code>{signal['adx']:.1f}</code>\n"

        f"📏 ATR: "
        f"<code>{signal['atr']:.2f}</code>\n"

        f"⚠️ 위험폭: "
        f"<code>{signal['risk']:.2f}</code>\n\n"

        f"⏱ 신호봉: "
        f"<code>{signal['signal_time']}</code>\n\n"

        f"🔗 "
        f"<a href='https://www.tradingview.com/symbols/COMEX-GC1!/'>"
        f"TradingView 골드 차트"
        f"</a>\n\n"

        f"⚡ "
        f"<i>추세 → 눌림 → 반전 → 돌파 확인</i>"
    )

    return send_telegram(
        message
    )


# ============================================================
# 메인
# ============================================================

def main():

    print("=" * 60)
    print(
        "골드 선물 스마트 타점 봇 시작"
    )
    print("=" * 60)

    # --------------------------------------------------------
    # 데이터
    # --------------------------------------------------------

    data = get_market_data()

    if not data:

        print(
            "시장 데이터를 가져오지 못했습니다."
        )

        log_event(
            "DATA_ERROR",
            "시장 데이터 조회 실패"
        )

        return

    df_1h = data[
        "1h"
    ]

    df_15m = data[
        "15m"
    ]

    df_5m = data[
        "5m"
    ]

    df_1m = data[
        "1m"
    ]

    print(
        f"1H: {len(df_1h)} / "
        f"15M: {len(df_15m)} / "
        f"5M: {len(df_5m)} / "
        f"1M: {len(df_1m)}"
    )

    # --------------------------------------------------------
    # 기존 상태
    # --------------------------------------------------------

    state = load_state()

    if state:

        state = migrate_state(
            state
        )

    # --------------------------------------------------------
    # 기존 포지션
    # --------------------------------------------------------

    if state:

        status = state.get(
            "status",
            "ACTIVE"
        )

        # ====================================================
        # COOLDOWN
        # ====================================================

        if status == "COOLDOWN":

            if is_in_cooldown(
                state
            ):

                print(
                    "현재 쿨다운 중입니다."
                )

                print(
                    "재진입 가능:",
                    state.get(
                        "cooldown_until"
                    )
                )

                return

            print(
                "쿨다운 종료."
            )

            clear_state()

            state = None

        # ====================================================
        # ACTIVE
        # ====================================================

        elif status == "ACTIVE":

            print(
                "기존 포지션 감시:",
                state.get(
                    "type"
                )
            )

            # ------------------------------------------------
            # 기존 상태의 필수값 검사
            # ------------------------------------------------

            required = [
                "entry",
                "sl",
                "tp1",
                "tp2",
                "tp3"
            ]

            missing = [
                key
                for key in required
                if key not in state
            ]

            if missing:

                print(
                    "상태 파일 필수값 누락:",
                    missing
                )

                log_event(
                    "STATE_ERROR",
                    "필수 상태값 누락",
                    {
                        "missing":
                            missing
                    }
                )

                return

            # ------------------------------------------------
            # 진입 알림 재전송
            # ------------------------------------------------

            if not state.get(
                "entry_alert_sent",
                False
            ):

                signal_copy = {

                    "type":
                        state["type"],

                    "entry":
                        state["entry"],

                    "sl":
                        state["sl"],

                    "tp1":
                        state["tp1"],

                    "tp2":
                        state["tp2"],

                    "tp3":
                        state["tp3"],

                    "risk":
                        state.get(
                            "risk",
                            0
                        ),

                    "atr":
                        state.get(
                            "atr",
                            0
                        ),

                    "rsi":
                        state.get(
                            "rsi",
                            0
                        ),

                    "adx":
                        state.get(
                            "adx",
                            0
                        ),

                    "signal_time":
                        state.get(
                            "signal_time",
                            ""
                        ),

                    "reason":
                        state.get(
                            "reason",
                            "기존 포지션"
                        )
                }

                if send_entry_alert(
                    signal_copy
                ):

                    state[
                        "entry_alert_sent"
                    ] = True

                    save_state(
                        state
                    )

            # ------------------------------------------------
            # TP / SL 감시
            # ------------------------------------------------

            monitor_position(
                state,
                df_1m
            )

            return

    # ========================================================
    # 신규 타점 탐색
    # ========================================================

    print(
        "새로운 타점 검색 중..."
    )

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

        log_event(
            "NO_SIGNAL",
            "조건 충족 타점 없음"
        )

        return

    # --------------------------------------------------------
    # 신호 출력
    # --------------------------------------------------------

    print(
        "=========================================="
    )

    print(
        "신규 신호:",
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

    print(
        "RSI:",
        signal["rsi"]
    )

    print(
        "ADX:",
        signal["adx"]
    )

    print(
        "=========================================="
    )

    # ========================================================
    # 상태 저장
    # ========================================================

    new_state = {

        "status":
            "ACTIVE",

        "type":
            signal["type"],

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

        "risk":
            signal["risk"],

        "atr":
            signal["atr"],

        "rsi":
            signal["rsi"],

        "adx":
            signal["adx"],

        "signal_time":
            signal["signal_time"],

        "entry_time":
            signal["entry_time"],

        "reason":
            signal["reason"],

        "tp1_sent":
            False,

        "tp2_sent":
            False,

        "tp3_sent":
            False,

        "entry_alert_sent":
            False
    }

    if not save_state(
        new_state
    ):

        print(
            "상태 저장 실패."
            " 신호를 보내지 않습니다."
        )

        return

    # ========================================================
    # Telegram
    # ========================================================

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
            "Telegram 진입 알림 실패."
        )

        log_event(
            "TELEGRAM_ERROR",
            "진입 알림 전송 실패"
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        print(
            "치명적 오류:",
            repr(e)
        )

        log_event(
            "FATAL_ERROR",
            repr(e)
        )

        raise
