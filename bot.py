import os,json,time,math,hashlib
from datetime import datetime,timedelta,timezone
import requests,numpy as np,pandas as pd,yfinance as yf

VERSION="17.4.0"
TICKER="GC=F"
STATE_FILE="signal_state.json"
LOG_FILE="bot_log.json"
TELEGRAM_TOKEN=os.getenv("TELEGRAM_TOKEN","")
TELEGRAM_CHAT_ID=os.getenv("TELEGRAM_CHAT_ID","")
KST=timezone(timedelta(hours=9))

# SIGNAL
MIN_M15_SCORE=7
STRONG_M15_SCORE=8
MIN_5M_SCORE=3
MIN_ADX=17.0
MIN_ATR=0.50
MAX_ATR=20.0
LONG_RSI_MIN=52
LONG_RSI_MAX=68
SHORT_RSI_MIN=32
SHORT_RSI_MAX=48
MIN_BODY_RATIO=0.35
MAX_ENTRY_DISTANCE_ATR=1.20

# RISK
RISK_ATR_MULT=1.80
MIN_RISK_ATR=1.20
MAX_RISK_ATR=2.80

# TP
TP1_R=1.20
TP2_R=2.00
TP3_R=3.00
STRONG_TP1_R=1.30
STRONG_TP2_R=2.20
STRONG_TP3_R=3.50

# COOLDOWN
SIGNAL_COOLDOWN_MINUTES=45
SL_COOLDOWN_MINUTES=120
TP3_COOLDOWN_MINUTES=15

# DATA
DATA_RETRIES=3
MAX_FRESH_1M_MINUTES=8
MAX_EMERGENCY_PRICE_AGE_MINUTES=30
MAX_5M_FRESH_MINUTES=15
MAX_15M_FRESH_MINUTES=30
MAX_1H_FRESH_MINUTES=90

SESSION=requests.Session()
SESSION.headers.update({
    "User-Agent":"Gold-Futures-Smart-Signal-Bot/17.4"
})


# ============================================================
# BASIC
# ============================================================

def now_kst():
    return datetime.now(KST)

def iso_now():
    return now_kst().isoformat()

def safe_float(v,default=None):
    try:
        if v is None:
            return default
        v=float(v)
        return v if math.isfinite(v) else default
    except:
        return default

def round_price(v):
    v=safe_float(v)
    return round(v,2) if v is not None else None

def clamp(v,lo,hi):
    return max(lo,min(hi,v))


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_TOKEN:
        print("[TELEGRAM] TOKEN MISSING")
        return False

    if not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] CHAT_ID MISSING")
        return False

    url=f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    payload={
        "chat_id":TELEGRAM_CHAT_ID,
        "text":message,
        "parse_mode":"HTML",
        "disable_web_page_preview":True
    }

    try:
        r=SESSION.post(url,json=payload,timeout=15)

        if r.ok:
            print("[TELEGRAM] SENT")
            return True

        print(
            "[TELEGRAM ERROR]",
            r.status_code,
            r.text[:500]
        )

    except Exception as e:
        print("[TELEGRAM EXCEPTION]",repr(e))

    return False


# ============================================================
# STATE
# ============================================================

def default_state():
    return {
        "version":VERSION,
        "status":"IDLE",
        "direction":None,
        "entry":None,
        "sl":None,
        "tp1":None,
        "tp2":None,
        "tp3":None,
        "risk":None,
        "stage":"INITIAL",
        "signal_time":None,
        "signal_id":None,
        "setup_id":None,
        "m15_score":0,
        "five_score":0,
        "rsi":None,
        "adx":None,
        "atr":None,
        "last_signal_time":None,
        "last_exit_time":None,
        "last_exit_reason":None,
        "last_exit_direction":None,
        "last_sl_time":None,
        "last_sl_direction":None,
        "last_monitor_time":None,
        "last_price":None,
        "last_run":None,
        "migration_done":False
    }


def atomic_write_json(path,data):
    tmp=path+".tmp"

    with open(tmp,"w",encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(tmp,path)


def save_state(state):
    state["version"]=VERSION

    try:
        atomic_write_json(
            STATE_FILE,
            state
        )
    except Exception as e:
        print(
            "[STATE SAVE ERROR]",
            repr(e)
        )


def load_state():
    state=default_state()

    if not os.path.exists(STATE_FILE):
        print("[STATE] No state file")
        return state

    try:
        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            old=json.load(f)

        if isinstance(old,dict):
            state.update(old)

    except Exception as e:
        print(
            "[STATE LOAD ERROR]",
            repr(e)
        )
        return state

    old_version=str(
        state.get("version","")
    )

    migrated=False

    if "stage" not in state:

        if state.get("tp2_hit"):
            state["stage"]="TP2_TRAIL"

        elif state.get("tp1_hit"):
            state["stage"]="TP1_BE"

        else:
            state["stage"]="INITIAL"

        migrated=True

    if state.get("status")=="ACTIVE":

        if state.get("tp2_hit") is True:

            state["stage"]="TP2_TRAIL"

            tp1=safe_float(
                state.get("tp1")
            )

            if tp1 is not None:
                state["sl"]=tp1

            migrated=True

        elif state.get("tp1_hit") is True:

            state["stage"]="TP1_BE"

            entry=safe_float(
                state.get("entry")
            )

            if entry is not None:
                state["sl"]=entry

            migrated=True

    if old_version!=VERSION:
        migrated=True

    state["version"]=VERSION
    state["migration_done"]=True

    if migrated:

        print(
            f"[STATE MIGRATION] "
            f"{old_version or 'UNKNOWN'} -> {VERSION}"
        )

        save_state(state)

    return state


# ============================================================
# LOG
# ============================================================

def load_log():

    if not os.path.exists(LOG_FILE):
        return []

    try:
        with open(
            LOG_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            data=json.load(f)

        return data if isinstance(data,list) else []

    except Exception as e:
        print(
            "[LOG LOAD ERROR]",
            repr(e)
        )

        return []


def write_log(event,state=None,extra=None):

    logs=load_log()

    item={
        "time":iso_now(),
        "version":VERSION,
        "event":event
    }

    if state:

        item.update({
            "status":state.get("status"),
            "direction":state.get("direction"),
            "entry":state.get("entry"),
            "sl":state.get("sl"),
            "tp1":state.get("tp1"),
            "tp2":state.get("tp2"),
            "tp3":state.get("tp3"),
            "stage":state.get("stage")
        })

    if extra:
        item["extra"]=extra

    logs.append(item)

    try:
        atomic_write_json(
            LOG_FILE,
            logs[-500:]
        )
    except Exception as e:
        print(
            "[LOG ERROR]",
            repr(e)
        )


# ============================================================
# DATA
# ============================================================

def normalize_index(df):

    result=df.copy()

    idx=pd.to_datetime(
        result.index,
        errors="coerce"
    )

    valid=~idx.isna()

    result=result.loc[valid].copy()
    idx=idx[valid]

    if idx.tz is None:
        idx=idx.tz_localize("UTC")

    result.index=idx.tz_convert(KST)

    result.sort_index(inplace=True)

    result=result[
        ~result.index.duplicated(
            keep="last"
        )
    ]

    return result


def download_data(interval,period):

    last_error=None

    for attempt in range(
        1,
        DATA_RETRIES+1
    ):

        try:

            print(
                f"[DATA] {interval} "
                f"attempt {attempt}/{DATA_RETRIES}"
            )

            df=yf.download(
                TICKER,
                period=period,
                interval=interval,
                auto_adjust=False,
                progress=False,
                threads=False
            )

            if df is None or df.empty:
                raise RuntimeError(
                    "empty dataframe"
                )

            if isinstance(
                df.columns,
                pd.MultiIndex
            ):

                try:

                    if TICKER in df.columns.get_level_values(-1):

                        df=df.xs(
                            TICKER,
                            axis=1,
                            level=-1
                        )

                except:
                    pass

                if isinstance(
                    df.columns,
                    pd.MultiIndex
                ):

                    df.columns=[
                        c[0]
                        if isinstance(c,tuple)
                        else c
                        for c in df.columns
                    ]

            required=[
                "Open",
                "High",
                "Low",
                "Close"
            ]

            for c in required:

                if c not in df.columns:
                    raise RuntimeError(
                        f"missing {c}"
                    )

            df=df[required].copy()

            for c in required:
                df[c]=pd.to_numeric(
                    df[c],
                    errors="coerce"
                )

            df.dropna(
                subset=required,
                inplace=True
            )

            df=normalize_index(df)

            if df.empty:
                raise RuntimeError(
                    "empty after cleanup"
                )

            print(
                f"[DATA] {interval} "
                f"{len(df)} rows"
            )

            return df

        except Exception as e:

            last_error=e

            print(
                f"[DATA ERROR] {interval}: "
                f"{repr(e)}"
            )

            time.sleep(1)

    print(
        f"[DATA FAILED] {interval}: "
        f"{repr(last_error)}"
    )

    return pd.DataFrame()


def completed(df):

    if df is None or df.empty:
        return df

    result=df.copy()

    now=pd.Timestamp.now(
        tz=KST
    )

    result=result[
        result.index<=now
    ]

    if len(result)>1:
        result=result.iloc[:-1]

    return result


def data_age_minutes(df):

    if df is None or df.empty:
        return float("inf")

    age=(
        pd.Timestamp.now(tz=KST)
        -df.index[-1]
    ).total_seconds()/60

    return max(0,age)


def print_freshness(name,df):

    age=data_age_minutes(df)

    print(
        f"[DATA FRESHNESS] "
        f"{name}: {age:.1f} min"
    )

    return age


# ============================================================
# INDICATORS
# ============================================================

def ema(series,length):
    return series.ewm(
        span=length,
        adjust=False
    ).mean()


def rsi(series,length=14):

    delta=series.diff()

    gain=delta.clip(
        lower=0
    )

    loss=-delta.clip(
        upper=0
    )

    avg_gain=gain.ewm(
        alpha=1/length,
        adjust=False
    ).mean()

    avg_loss=loss.ewm(
        alpha=1/length,
        adjust=False
    ).mean()

    rs=avg_gain/avg_loss.replace(
        0,
        np.nan
    )

    result=100-(100/(1+rs))

    return result.fillna(50)


def atr(df,length=14):

    h=df["High"]
    l=df["Low"]
    c=df["Close"]

    prev=c.shift(1)

    tr=pd.concat(
        [
            h-l,
            (h-prev).abs(),
            (l-prev).abs()
        ],
        axis=1
    ).max(axis=1)

    return tr.ewm(
        alpha=1/length,
        adjust=False
    ).mean()


def adx(df,length=14):

    h=df["High"]
    l=df["Low"]
    c=df["Close"]

    up=h.diff()
    down=-l.diff()

    plus_dm=pd.Series(
        np.where(
            (up>down)&(up>0),
            up,
            0
        ),
        index=df.index
    )

    minus_dm=pd.Series(
        np.where(
            (down>up)&(down>0),
            down,
            0
        ),
        index=df.index
    )

    tr=pd.concat(
        [
            h-l,
            (h-c.shift(1)).abs(),
            (l-c.shift(1)).abs()
        ],
        axis=1
    ).max(axis=1)

    atr_v=tr.ewm(
        alpha=1/length,
        adjust=False
    ).mean()

    plus_di=100*plus_dm.ewm(
        alpha=1/length,
        adjust=False
    ).mean()/atr_v.replace(
        0,
        np.nan
    )

    minus_di=100*minus_dm.ewm(
        alpha=1/length,
        adjust=False
    ).mean()/atr_v.replace(
        0,
        np.nan
    )

    dx=100*(plus_di-minus_di).abs()/(
        plus_di+minus_di
    ).replace(
        0,
        np.nan
    )

    return dx.ewm(
        alpha=1/length,
        adjust=False
    ).mean()


def add_indicators(df):

    result=df.copy()

    result["EMA20"]=ema(
        result["Close"],
        20
    )

    result["EMA50"]=ema(
        result["Close"],
        50
    )

    result["RSI"]=rsi(
        result["Close"],
        14
    )

    result["ATR"]=atr(
        result,
        14
    )

    result["ADX"]=adx(
        result,
        14
    )

    result["Body"]=(
        result["Close"]
        -result["Open"]
    ).abs()

    result["Range"]=(
        result["High"]
        -result["Low"]
    ).replace(
        0,
        np.nan
    )

    result["BodyRatio"]=(
        result["Body"]
        /result["Range"]
    ).fillna(0)

    result["ClosePosition"]=(
        (
            result["Close"]
            -result["Low"]
        )
        /result["Range"]
    ).fillna(0.5)

    return result


# ============================================================
# SCORING
# ============================================================

def max_entry_distance(atr_value):
    return (
        atr_value
        *MAX_ENTRY_DISTANCE_ATR
    )


def score_m15(df):

    if df is None or len(df)<60:
        return {
            "long":0,
            "short":0,
            "max":8
        }

    row=df.iloc[-1]
    prev=df.iloc[-2]

    close=safe_float(row["Close"])
    ema20=safe_float(row["EMA20"])
    ema50=safe_float(row["EMA50"])
    prev_ema20=safe_float(prev["EMA20"])
    rsi_v=safe_float(row["RSI"])
    adx_v=safe_float(row["ADX"])
    atr_v=safe_float(row["ATR"])
    body=safe_float(
        row["BodyRatio"],
        0
    )
    pos=safe_float(
        row["ClosePosition"],
        0.5
    )

    if None in (
        close,
        ema20,
        ema50,
        prev_ema20,
        rsi_v,
        adx_v,
        atr_v
    ):
        return {
            "long":0,
            "short":0,
            "max":8
        }

    ls=0
    ss=0

    # LONG
    if close>ema20:
        ls+=1

    if ema20>ema50:
        ls+=1

    if ema20>prev_ema20:
        ls+=1

    if (
        body>=MIN_BODY_RATIO
        and close>row["Open"]
    ):
        ls+=1

    if pos>=0.60:
        ls+=1

    if (
        LONG_RSI_MIN
        <=rsi_v
        <=LONG_RSI_MAX
    ):
        ls+=1

    if adx_v>=MIN_ADX:
        ls+=1

    if (
        close-ema20
        <=max_entry_distance(atr_v)
    ):
        ls+=1

    # SHORT
    if close<ema20:
        ss+=1

    if ema20<ema50:
        ss+=1

    if ema20<prev_ema20:
        ss+=1

    if (
        body>=MIN_BODY_RATIO
        and close<row["Open"]
    ):
        ss+=1

    if pos<=0.40:
        ss+=1

    if (
        SHORT_RSI_MIN
        <=rsi_v
        <=SHORT_RSI_MAX
    ):
        ss+=1

    if adx_v>=MIN_ADX:
        ss+=1

    if (
        ema20-close
        <=max_entry_distance(atr_v)
    ):
        ss+=1

    return {
        "long":ls,
        "short":ss,
        "max":8
    }


def score_5m(df,direction):

    if df is None or len(df)<50:
        return 0

    row=df.iloc[-1]

    close=safe_float(
        row["Close"]
    )

    ema20=safe_float(
        row["EMA20"]
    )

    ema50=safe_float(
        row["EMA50"]
    )

    body=safe_float(
        row["BodyRatio"],
        0
    )

    pos=safe_float(
        row["ClosePosition"],
        0.5
    )

    if None in (
        close,
        ema20,
        ema50
    ):
        return 0

    score=0

    if direction=="LONG":

        if close>ema20:
            score+=1

        if ema20>ema50:
            score+=1

        if (
            body>=MIN_BODY_RATIO
            and close>row["Open"]
        ):
            score+=1

        if pos>=0.55:
            score+=1

    else:

        if close<ema20:
            score+=1

        if ema20<ema50:
            score+=1

        if (
            body>=MIN_BODY_RATIO
            and close<row["Open"]
        ):
            score+=1

        if pos<=0.45:
            score+=1

    return score


def h1_trend(df):

    if df is None or len(df)<60:
        return "NEUTRAL"

    row=df.iloc[-1]

    close=safe_float(
        row["Close"]
    )

    ema20=safe_float(
        row["EMA20"]
    )

    ema50=safe_float(
        row["EMA50"]
    )

    if None in (
        close,
        ema20,
        ema50
    ):
        return "NEUTRAL"

    if close>ema20>ema50:
        return "BULL"

    if close<ema20<ema50:
        return "BEAR"

    return "NEUTRAL"


# ============================================================
# SIGNAL
# ============================================================

def make_setup_id(
    direction,
    m15_close,
    five_close,
    m15_score,
    five_score
):

    raw=(
        f"{direction}|"
        f"{round(m15_close,1)}|"
        f"{round(five_close,1)}|"
        f"{m15_score}|"
        f"{five_score}"
    )

    return hashlib.sha1(
        raw.encode()
    ).hexdigest()[:16]


def find_signal(m15,five,one_h):

    if (
        m15.empty
        or five.empty
        or one_h.empty
    ):
        return None

    m15s=add_indicators(m15)
    fives=add_indicators(five)
    h1s=add_indicators(one_h)

    if (
        len(m15s)<60
        or len(fives)<60
        or len(h1s)<60
    ):
        print(
            "[SIGNAL] "
            "Insufficient data"
        )
        return None

    mrow=m15s.iloc[-1]
    frow=fives.iloc[-1]

    scores=score_m15(m15s)
    h1=h1_trend(h1s)

    candidates=[]

    for direction in (
        "LONG",
        "SHORT"
    ):

        mscore=(
            scores["long"]
            if direction=="LONG"
            else scores["short"]
        )

        if mscore<MIN_M15_SCORE:
            continue

        fscore=score_5m(
            fives,
            direction
        )

        if fscore<MIN_5M_SCORE:
            continue

        close=safe_float(
            mrow["Close"]
        )

        ema20=safe_float(
            mrow["EMA20"]
        )

        rsi_v=safe_float(
            mrow["RSI"]
        )

        adx_v=safe_float(
            mrow["ADX"]
        )

        atr_v=safe_float(
            mrow["ATR"]
        )

        five_close=safe_float(
            frow["Close"]
        )

        if None in (
            close,
            ema20,
            rsi_v,
            adx_v,
            atr_v,
            five_close
        ):
            continue

        if (
            adx_v<MIN_ADX
            or not MIN_ATR
            <=atr_v
            <=MAX_ATR
        ):
            continue

        if direction=="LONG":

            if not (
                LONG_RSI_MIN
                <=rsi_v
                <=LONG_RSI_MAX
            ):
                continue

            if close<=ema20:
                continue

            h1_bonus=(
                1
                if h1=="BULL"
                else 0
            )

        else:

            if not (
                SHORT_RSI_MIN
                <=rsi_v
                <=SHORT_RSI_MAX
            ):
                continue

            if close>=ema20:
                continue

            h1_bonus=(
                1
                if h1=="BEAR"
                else 0
            )

        total=(
            mscore*10
            +fscore*2
            +h1_bonus
        )

        candidates.append({
            "direction":direction,
            "m15_score":mscore,
            "five_score":fscore,
            "rsi":rsi_v,
            "adx":adx_v,
            "atr":atr_v,
            "h1":h1,
            "m15_close":close,
            "five_close":five_close,
            "strong":mscore>=STRONG_M15_SCORE,
            "total_score":total
        })

    if not candidates:

        print(
            "[SIGNAL] "
            "No valid signal"
        )

        return None

    candidates.sort(
        key=lambda x:x["total_score"],
        reverse=True
    )

    signal=candidates[0]

    signal["setup_id"]=make_setup_id(
        signal["direction"],
        signal["m15_close"],
        signal["five_close"],
        signal["m15_score"],
        signal["five_score"]
    )

    print(
        "[SIGNAL FOUND]",
        signal["direction"],
        "M15=",
        signal["m15_score"],
        "5M=",
        signal["five_score"],
        "RSI=",
        round(signal["rsi"],2),
        "ADX=",
        round(signal["adx"],2),
        "H1=",
        signal["h1"]
    )

    return signal


# ============================================================
# POSITION
# ============================================================

def calculate_position(
    signal,
    entry,
    m15
):

    direction=signal["direction"]
    atr_v=safe_float(
        signal["atr"]
    )

    if atr_v is None:
        return None

    recent=m15.tail(12)

    recent_high=safe_float(
        recent["High"].max()
    )

    recent_low=safe_float(
        recent["Low"].min()
    )

    if None in (
        recent_high,
        recent_low
    ):
        return None

    risk=atr_v*RISK_ATR_MULT

    min_risk=(
        atr_v
        *MIN_RISK_ATR
    )

    max_risk=(
        atr_v
        *MAX_RISK_ATR
    )

    if direction=="LONG":

        swing_sl=(
            recent_low
            -atr_v*0.25
        )

        risk=max(
            risk,
            entry-swing_sl
        )

        risk=clamp(
            risk,
            min_risk,
            max_risk
        )

        sl=entry-risk

    else:

        swing_sl=(
            recent_high
            +atr_v*0.25
        )

        risk=max(
            risk,
            swing_sl-entry
        )

        risk=clamp(
            risk,
            min_risk,
            max_risk
        )

        sl=entry+risk

    if signal["strong"]:

        r1=STRONG_TP1_R
        r2=STRONG_TP2_R
        r3=STRONG_TP3_R

    else:

        r1=TP1_R
        r2=TP2_R
        r3=TP3_R

    if direction=="LONG":

        tp1=entry+risk*r1
        tp2=entry+risk*r2
        tp3=entry+risk*r3

    else:

        tp1=entry-risk*r1
        tp2=entry-risk*r2
        tp3=entry-risk*r3

    return {
        "entry":round_price(entry),
        "sl":round_price(sl),
        "tp1":round_price(tp1),
        "tp2":round_price(tp2),
        "tp3":round_price(tp3),
        "risk":round_price(risk)
    }


# ============================================================
# COOLDOWN
# ============================================================

def parse_time(value):

    if not value:
        return None

    try:

        dt=datetime.fromisoformat(
            value
        )

        if dt.tzinfo is None:
            dt=dt.replace(
                tzinfo=KST
            )

        return dt.astimezone(KST)

    except:
        return None


def cooldown_active(
    state,
    direction
):

    now=now_kst()

    last_sl=parse_time(
        state.get("last_sl_time")
    )

    if (
        last_sl
        and state.get(
            "last_sl_direction"
        )==direction
    ):

        minutes=(
            now-last_sl
        ).total_seconds()/60

        if minutes<SL_COOLDOWN_MINUTES:

            print(
                "[COOLDOWN] SL "
                f"{SL_COOLDOWN_MINUTES-minutes:.1f}m"
            )

            return True

    last_exit=parse_time(
        state.get("last_exit_time")
    )

    if last_exit:

        minutes=(
            now-last_exit
        ).total_seconds()/60

        reason=state.get(
            "last_exit_reason"
        )

        limit=(
            TP3_COOLDOWN_MINUTES
            if reason=="TP3"
            else SIGNAL_COOLDOWN_MINUTES
        )

        if minutes<limit:

            print(
                "[COOLDOWN] "
                f"{limit-minutes:.1f}m"
            )

            return True

    return False


# ============================================================
# ALERTS
# ============================================================

def send_entry_alert(
    state,
    signal
):

    direction=signal["direction"]

    emoji=(
        "🟢"
        if direction=="LONG"
        else "🔴"
    )

    message=f"""
🥇 <b>금 선물 스마트 시그널 V17.4</b>

{emoji} <b>{direction} 신규 진입</b>

━━━━━━━━━━━━━━━━━━

💰 Entry
<b>${state["entry"]:.2f}</b>

🛑 SL
<b>${state["sl"]:.2f}</b>

🎯 TP1
<b>${state["tp1"]:.2f}</b>

🎯 TP2
<b>${state["tp2"]:.2f}</b>

🎯 TP3
<b>${state["tp3"]:.2f}</b>

━━━━━━━━━━━━━━━━━━

📊 M15 Score
<b>{signal["m15_score"]}/8</b>

📊 5M Score
<b>{signal["five_score"]}/4</b>

📈 RSI
<b>{signal["rsi"]:.2f}</b>

📐 ADX
<b>{signal["adx"]:.2f}</b>

📏 ATR
<b>{signal["atr"]:.2f}</b>

🕐 H1 Trend
<b>{signal["h1"]}</b>

━━━━━━━━━━━━━━━━━━

🔒 <b>ONE POSITION LOCK</b>

TP1 → SL Entry
TP2 → SL TP1
TP3 → Position Exit
"""

    return send_telegram(
        message.strip()
    )


def send_tp_alert(
    state,
    level,
    price,
    emergency=False
):

    if level=="TP1":

        title="🎯 TP1 도달"

        stop_text=(
            "SL → ENTRY\n"
            f"<b>${state['entry']:.2f}</b>"
        )

    elif level=="TP2":

        title="🎯 TP2 도달"

        stop_text=(
            "SL → TP1\n"
            f"<b>${state['tp1']:.2f}</b>"
        )

    else:

        title="🏆 TP3 최종 도달"

        stop_text="✅ 포지션 종료"

    suffix=(
        "\n⚠️ <b>Emergency Price Check</b>"
        if emergency
        else ""
    )

    message=f"""
🥇 <b>금 선물 스마트 시그널 V17.4</b>

{title}

<b>{state["direction"]}</b>

현재가
<b>${price:.2f}</b>

목표가
<b>${state[level.lower()]:.2f}</b>

{stop_text}
{suffix}
"""

    return send_telegram(
        message.strip()
    )


def send_sl_alert(
    state,
    price,
    emergency=False
):

    suffix=(
        "\n⚠️ <b>Emergency Price Check</b>"
        if emergency
        else ""
    )

    message=f"""
🥇 <b>금 선물 스마트 시그널 V17.4</b>

🛑 <b>{state["direction"]} SL 청산</b>

현재가
<b>${price:.2f}</b>

SL
<b>${state["sl"]:.2f}</b>

❌ <b>포지션 종료</b>
{suffix}
"""

    return send_telegram(
        message.strip()
    )


# ============================================================
# POSITION CLOSE
# ============================================================

def close_position(
    state,
    reason,
    price
):

    direction=state.get(
        "direction"
    )

    old=dict(state)

    state["status"]="IDLE"
    state["direction"]=None
    state["entry"]=None
    state["sl"]=None
    state["tp1"]=None
    state["tp2"]=None
    state["tp3"]=None
    state["risk"]=None
    state["stage"]="INITIAL"

    state["last_exit_time"]=iso_now()
    state["last_exit_reason"]=reason
    state["last_exit_direction"]=direction

    if reason=="SL":

        state["last_sl_time"]=iso_now()
        state["last_sl_direction"]=direction

    save_state(state)

    write_log(
        f"POSITION_EXIT_{reason}",
        state,
        {
            "exit_price":price,
            "old_entry":old.get("entry"),
            "old_sl":old.get("sl"),
            "old_tp1":old.get("tp1"),
            "old_tp2":old.get("tp2"),
            "old_tp3":old.get("tp3")
        }
    )

    print(
        "[EXIT]",
        direction,
        reason,
        "@",
        price
    )


# ============================================================
# 1M MONITOR
# ============================================================

def process_monitor_candle(
    state,
    candle_time,
    candle
):

    if state.get("status")!="ACTIVE":
        return False

    direction=state.get(
        "direction"
    )

    high=safe_float(
        candle["High"]
    )

    low=safe_float(
        candle["Low"]
    )

    close=safe_float(
        candle["Close"]
    )

    if None in (
        high,
        low,
        close
    ):
        return False

    entry=safe_float(
        state.get("entry")
    )

    sl=safe_float(
        state.get("sl")
    )

    tp1=safe_float(
        state.get("tp1")
    )

    tp2=safe_float(
        state.get("tp2")
    )

    tp3=safe_float(
        state.get("tp3")
    )

    if None in (
        entry,
        sl,
        tp1,
        tp2,
        tp3
    ):
        return False

    stage=state.get(
        "stage",
        "INITIAL"
    )

    # LONG
    if direction=="LONG":

        if low<=sl:

            send_sl_alert(
                state,
                sl
            )

            close_position(
                state,
                "SL",
                sl
            )

            return True

        if (
            stage=="INITIAL"
            and high>=tp1
        ):

            state["stage"]="TP1_BE"
            state["sl"]=entry

            send_tp_alert(
                state,
                "TP1",
                tp1
            )

            save_state(state)

            write_log(
                "TP1_HIT",
                state,
                {
                    "price":tp1,
                    "candle_time":str(
                        candle_time
                    )
                }
            )

            stage="TP1_BE"

        if stage=="TP1_BE":

            if low<=entry:

                send_sl_alert(
                    state,
                    entry
                )

                close_position(
                    state,
                    "SL",
                    entry
                )

                return True

            if high>=tp2:

                state["stage"]="TP2_TRAIL"
                state["sl"]=tp1

                send_tp_alert(
                    state,
                    "TP2",
                    tp2
                )

                save_state(state)

                write_log(
                    "TP2_HIT",
                    state,
                    {
                        "price":tp2,
                        "candle_time":str(
                            candle_time
                        )
                    }
                )

                stage="TP2_TRAIL"

        if stage=="TP2_TRAIL":

            if low<=tp1:

                send_sl_alert(
                    state,
                    tp1
                )

                close_position(
                    state,
                    "SL",
                    tp1
                )

                return True

            if high>=tp3:

                send_tp_alert(
                    state,
                    "TP3",
                    tp3
                )

                close_position(
                    state,
                    "TP3",
                    tp3
                )

                return True

    # SHORT
    elif direction=="SHORT":

        if high>=sl:

            send_sl_alert(
                state,
                sl
            )

            close_position(
                state,
                "SL",
                sl
            )

            return True

        if (
            stage=="INITIAL"
            and low<=tp1
        ):

            state["stage"]="TP1_BE"
            state["sl"]=entry

            send_tp_alert(
                state,
                "TP1",
                tp1
            )

            save_state(state)

            write_log(
                "TP1_HIT",
                state,
                {
                    "price":tp1,
                    "candle_time":str(
                        candle_time
                    )
                }
            )

            stage="TP1_BE"

        if stage=="TP1_BE":

            if high>=entry:

                send_sl_alert(
                    state,
                    entry
                )

                close_position(
                    state,
                    "SL",
                    entry
                )

                return True

            if low<=tp2:

                state["stage"]="TP2_TRAIL"
                state["sl"]=tp1

                send_tp_alert(
                    state,
                    "TP2",
                    tp2
                )

                save_state(state)

                write_log(
                    "TP2_HIT",
                    state,
                    {
                        "price":tp2,
                        "candle_time":str(
                            candle_time
                        )
                    }
                )

                stage="TP2_TRAIL"

        if stage=="TP2_TRAIL":

            if high>=tp1:

                send_sl_alert(
                    state,
                    tp1
                )

                close_position(
                    state,
                    "SL",
                    tp1
                )

                return True

            if low<=tp3:

                send_tp_alert(
                    state,
                    "TP3",
                    tp3
                )

                close_position(
                    state,
                    "TP3",
                    tp3
                )

                return True

    return False


def get_new_candles(
    df,
    last_monitor_time
):

    if df is None or df.empty:
        return pd.DataFrame()

    data=completed(df)

    if data.empty:
        return data

    if not last_monitor_time:
        return data.tail(10)

    last_dt=parse_time(
        last_monitor_time
    )

    if last_dt is None:
        return data.tail(10)

    return data[
        data.index
        >pd.Timestamp(last_dt)
    ]


def monitor_precise_1m(
    state,
    one_m
):

    candles=get_new_candles(
        one_m,
        state.get(
            "last_monitor_time"
        )
    )

    if candles.empty:

        print(
            "[MONITOR] "
            "No new completed 1M candles"
        )

        return state

    print(
        "[MONITOR] Processing "
        f"{len(candles)} new 1M candles"
    )

    for timestamp,candle in candles.iterrows():

        if state.get("status")!="ACTIVE":
            break

        process_monitor_candle(
            state,
            timestamp,
            candle
        )

        state["last_monitor_time"]=(
            timestamp.isoformat()
        )

        state["last_price"]=safe_float(
            candle["Close"]
        )

        save_state(state)

    return state


# ============================================================
# EMERGENCY PRICE
# ============================================================

def get_emergency_price():

    print(
        "[EMERGENCY PRICE] "
        "Starting multi-source price lookup"
    )

    # --------------------------------------------------------
    # 1. fast_info
    # --------------------------------------------------------
    try:

        ticker=yf.Ticker(
            TICKER
        )

        fast=ticker.fast_info

        for key in (
            "last_price",
            "regularMarketPrice"
        ):

            try:

                price=safe_float(
                    fast.get(key)
                )

                if (
                    price is not None
                    and price>0
                ):

                    print(
                        "[EMERGENCY PRICE] "
                        f"fast_info.{key} = "
                        f"${price:.2f}"
                    )

                    return (
                        price,
                        now_kst()
                    )

            except Exception as e:

                print(
                    "[EMERGENCY PRICE] "
                    f"fast_info.{key} failed: "
                    f"{repr(e)}"
                )

    except Exception as e:

        print(
            "[EMERGENCY PRICE] "
            f"fast_info failed: {repr(e)}"
        )

    # --------------------------------------------------------
    # 2. yfinance 1m history
    # --------------------------------------------------------
    try:

        print(
            "[EMERGENCY PRICE] "
            "Trying yfinance 1m"
        )

        df=yf.download(
            TICKER,
            period="1d",
            interval="1m",
            auto_adjust=False,
            progress=False,
            threads=False
        )

        if (
            df is not None
            and not df.empty
        ):

            if isinstance(
                df.columns,
                pd.MultiIndex
            ):

                try:

                    if TICKER in df.columns.get_level_values(-1):

                        df=df.xs(
                            TICKER,
                            axis=1,
                            level=-1
                        )

                except:
                    pass

                if isinstance(
                    df.columns,
                    pd.MultiIndex
                ):

                    df.columns=[
                        c[0]
                        if isinstance(c,tuple)
                        else c
                        for c in df.columns
                    ]

            if "Close" in df.columns:

                closes=pd.to_numeric(
                    df["Close"],
                    errors="coerce"
                ).dropna()

                if not closes.empty:

                    price=safe_float(
                        closes.iloc[-1]
                    )

                    if (
                        price is not None
                        and price>0
                    ):

                        print(
                            "[EMERGENCY PRICE] "
                            f"history 1m = "
                            f"${price:.2f}"
                        )

                        return (
                            price,
                            now_kst()
                        )

    except Exception as e:

        print(
            "[EMERGENCY PRICE] "
            f"history 1m failed: "
            f"{repr(e)}"
        )

    # --------------------------------------------------------
    # 3. yfinance 5m history
    # --------------------------------------------------------
    try:

        print(
            "[EMERGENCY PRICE] "
            "Trying yfinance 5m"
        )

        df=yf.download(
            TICKER,
            period="5d",
            interval="5m",
            auto_adjust=False,
            progress=False,
            threads=False
        )

        if (
            df is not None
            and not df.empty
        ):

            if isinstance(
                df.columns,
                pd.MultiIndex
            ):

                try:

                    if TICKER in df.columns.get_level_values(-1):

                        df=df.xs(
                            TICKER,
                            axis=1,
                            level=-1
                        )

                except:
                    pass

                if isinstance(
                    df.columns,
                    pd.MultiIndex
                ):

                    df.columns=[
                        c[0]
                        if isinstance(c,tuple)
                        else c
                        for c in df.columns
                    ]

            if "Close" in df.columns:

                closes=pd.to_numeric(
                    df["Close"],
                    errors="coerce"
                ).dropna()

                if not closes.empty:

                    price=safe_float(
                        closes.iloc[-1]
                    )

                    if (
                        price is not None
                        and price>0
                    ):

                        print(
                            "[EMERGENCY PRICE] "
                            f"history 5m = "
                            f"${price:.2f}"
                        )

                        return (
                            price,
                            now_kst()
                        )

    except Exception as e:

        print(
            "[EMERGENCY PRICE] "
            f"history 5m failed: "
            f"{repr(e)}"
        )

    # --------------------------------------------------------
    # 4. Yahoo Chart API
    # --------------------------------------------------------
    try:

        print(
            "[EMERGENCY PRICE] "
            "Trying Yahoo Chart API"
        )

        url=(
            "https://query1.finance.yahoo.com/"
            "v8/finance/chart/"
            f"{TICKER}"
            "?interval=1m&range=1d"
        )

        headers={
            "User-Agent":
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "Chrome/120 Safari/537.36"
        }

        response=SESSION.get(
            url,
            headers=headers,
            timeout=10
        )

        if response.ok:

            data=response.json()

            result=(
                data
                .get("chart",{})
                .get("result")
            )

            if result:

                result=result[0]

                meta=result.get(
                    "meta",
                    {}
                )

                # 4-1 regularMarketPrice
                market_price=safe_float(
                    meta.get(
                        "regularMarketPrice"
                    )
                )

                if (
                    market_price is not None
                    and market_price>0
                ):

                    print(
                        "[EMERGENCY PRICE] "
                        f"Yahoo meta = "
                        f"${market_price:.2f}"
                    )

                    return (
                        market_price,
                        now_kst()
                    )

                # 4-2 candle close
                indicators=result.get(
                    "indicators",
                    {}
                )

                quote=indicators.get(
                    "quote",
                    []
                )

                if quote:

                    closes=quote[0].get(
                        "close",
                        []
                    )

                    valid=[]

                    for x in closes:

                        value=safe_float(x)

                        if (
                            value is not None
                            and value>0
                        ):
                            valid.append(value)

                    if valid:

                        price=valid[-1]

                        print(
                            "[EMERGENCY PRICE] "
                            f"Yahoo chart = "
                            f"${price:.2f}"
                        )

                        return (
                            price,
                            now_kst()
                        )

        else:

            print(
                "[EMERGENCY PRICE] "
                "Yahoo API HTTP "
                f"{response.status_code}"
            )

    except Exception as e:

        print(
            "[EMERGENCY PRICE] "
            f"Yahoo Chart API failed: "
            f"{repr(e)}"
        )

    print(
        "[EMERGENCY PRICE] "
        "ALL PRICE SOURCES FAILED"
    )

    return None,None


# ============================================================
# EMERGENCY MONITOR
# ============================================================

def emergency_monitor(
    state,
    one_m_age
):

    if state.get("status")!="ACTIVE":
        return state

    if (
        one_m_age
        >MAX_EMERGENCY_PRICE_AGE_MINUTES
    ):

        print(
            "[EMERGENCY WARNING] "
            f"1M age={one_m_age:.1f}m"
        )

        print(
            "[EMERGENCY] "
            "Trying live price anyway..."
        )

    price,price_time=(
        get_emergency_price()
    )

    if price is None:

        print(
            "[EMERGENCY] "
            "Current price unavailable"
        )

        write_log(
            "EMERGENCY_PRICE_FAILED",
            state,
            {
                "one_m_age":one_m_age
            }
        )

        return state

    state["last_price"]=price

    direction=state.get(
        "direction"
    )

    entry=safe_float(
        state.get("entry")
    )

    sl=safe_float(
        state.get("sl")
    )

    tp1=safe_float(
        state.get("tp1")
    )

    tp2=safe_float(
        state.get("tp2")
    )

    tp3=safe_float(
        state.get("tp3")
    )

    if None in (
        entry,
        sl,
        tp1,
        tp2,
        tp3
    ):
        return state

    stage=state.get(
        "stage",
        "INITIAL"
    )

    print(
        "[EMERGENCY CHECK]",
        direction,
        "price=",
        price,
        "SL=",
        sl,
        "TP1=",
        tp1,
        "TP2=",
        tp2,
        "TP3=",
        tp3,
        "stage=",
        stage
    )

    # LONG
    if direction=="LONG":

        if price<=sl:

            send_sl_alert(
                state,
                price,
                True
            )

            close_position(
                state,
                "SL",
                price
            )

            return state

        if (
            stage=="INITIAL"
            and price>=tp1
        ):

            state["stage"]="TP1_BE"
            state["sl"]=entry

            send_tp_alert(
                state,
                "TP1",
                price,
                True
            )

            save_state(state)

            write_log(
                "TP1_HIT_EMERGENCY",
                state,
                {
                    "price":price,
                    "price_time":str(
                        price_time
                    )
                }
            )

            stage="TP1_BE"

        if stage=="TP1_BE":

            if price<=entry:

                send_sl_alert(
                    state,
                    price,
                    True
                )

                close_position(
                    state,
                    "SL",
                    price
                )

                return state

            if price>=tp2:

                state["stage"]="TP2_TRAIL"
                state["sl"]=tp1

                send_tp_alert(
                    state,
                    "TP2",
                    price,
                    True
                )

                save_state(state)

                write_log(
                    "TP2_HIT_EMERGENCY",
                    state,
                    {
                        "price":price,
                        "price_time":str(
                            price_time
                        )
                    }
                )

                stage="TP2_TRAIL"

        if stage=="TP2_TRAIL":

            if price<=tp1:

                send_sl_alert(
                    state,
                    price,
                    True
                )

                close_position(
                    state,
                    "SL",
                    price
                )

                return state

            if price>=tp3:

                send_tp_alert(
                    state,
                    "TP3",
                    price,
                    True
                )

                close_position(
                    state,
                    "TP3",
                    price
                )

                return state

    # SHORT
    elif direction=="SHORT":

        if price>=sl:

            send_sl_alert(
                state,
                price,
                True
            )

            close_position(
                state,
                "SL",
                price
            )

            return state

        if (
            stage=="INITIAL"
            and price<=tp1
        ):

            state["stage"]="TP1_BE"
            state["sl"]=entry

            send_tp_alert(
                state,
                "TP1",
                price,
                True
            )

            save_state(state)

            write_log(
                "TP1_HIT_EMERGENCY",
                state,
                {
                    "price":price,
                    "price_time":str(
                        price_time
                    )
                }
            )

            stage="TP1_BE"

        if stage=="TP1_BE":

            if price>=entry:

                send_sl_alert(
                    state,
                    price,
                    True
                )

                close_position(
                    state,
                    "SL",
                    price
                )

                return state

            if price<=tp2:

                state["stage"]="TP2_TRAIL"
                state["sl"]=tp1

                send_tp_alert(
                    state,
                    "TP2",
                    price,
                    True
                )

                save_state(state)

                write_log(
                    "TP2_HIT_EMERGENCY",
                    state,
                    {
                        "price":price,
                        "price_time":str(
                            price_time
                        )
                    }
                )

                stage="TP2_TRAIL"

        if stage=="TP2_TRAIL":

            if price>=tp1:

                send_sl_alert(
                    state,
                    price,
                    True
                )

                close_position(
                    state,
                    "SL",
                    price
                )

                return state

            if price<=tp3:

                send_tp_alert(
                    state,
                    "TP3",
                    price,
                    True
                )

                close_position(
                    state,
                    "TP3",
                    price
                )

                return state

    save_state(state)

    write_log(
        "EMERGENCY_MONITOR",
        state,
        {
            "price":price,
            "price_time":str(
                price_time
            ),
            "one_m_age":one_m_age
        }
    )

    return state


# ============================================================
# DISPLAY
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
        state.get("status")
    )

    print(
        "Direction :",
        state.get("direction")
    )

    print(
        "Entry     :",
        state.get("entry")
    )

    print(
        "SL        :",
        state.get("sl")
    )

    print(
        "TP1       :",
        state.get("tp1")
    )

    print(
        "TP2       :",
        state.get("tp2")
    )

    print(
        "TP3       :",
        state.get("tp3")
    )

    print(
        "Stage     :",
        state.get("stage")
    )

    print(
        "===================================="
    )

    print()


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
        f" KST: {iso_now()}"
    )

    print(
        "===================================="
    )

    print(
        "[BOOT] Python bot.py started"
    )

    print(
        "[BOOT] TICKER:",
        TICKER
    )

    print(
        "[BOOT] STATE:",
        STATE_FILE
    )

    print(
        "[BOOT] TELEGRAM_TOKEN:",
        "OK"
        if TELEGRAM_TOKEN
        else "MISSING"
    )

    print(
        "[BOOT] TELEGRAM_CHAT_ID:",
        "OK"
        if TELEGRAM_CHAT_ID
        else "MISSING"
    )

    state=load_state()

    state["last_run"]=iso_now()

    save_state(state)

    print_state(state)

    print(
        "[DATA] "
        "Downloading market data..."
    )

    one_m_raw=download_data(
        "1m",
        "7d"
    )

    five_raw=download_data(
        "5m",
        "30d"
    )

    m15_raw=download_data(
        "15m",
        "60d"
    )

    one_h_raw=download_data(
        "1h",
        "60d"
    )

    if one_m_raw.empty:

        print(
            "[WARNING] "
            "1M data unavailable"
        )

        if state.get("status")=="ACTIVE":

            print(
                "[ACTIVE] "
                "Attempting emergency "
                "protection"
            )

            emergency_monitor(
                state,
                float("inf")
            )

        write_log(
            "RUN_1M_DATA_FAILED",
            state
        )

        return

    if (
        five_raw.empty
        or m15_raw.empty
        or one_h_raw.empty
    ):

        print(
            "[WARNING] "
            "Higher timeframe "
            "data unavailable"
        )

        write_log(
            "RUN_HIGHER_TF_DATA_FAILED",
            state
        )

        return

    one_m=completed(
        one_m_raw
    )

    five=completed(
        five_raw
    )

    m15=completed(
        m15_raw
    )

    one_h=completed(
        one_h_raw
    )

    age_1m=print_freshness(
        "1M",
        one_m
    )

    age_5m=print_freshness(
        "5M",
        five
    )

    age_15m=print_freshness(
        "15M",
        m15
    )

    age_1h=print_freshness(
        "1H",
        one_h
    )

    # ========================================================
    # ACTIVE POSITION
    # ========================================================

    if state.get("status")=="ACTIVE":

        print(
            "[POSITION] "
            "ACTIVE position detected"
        )

        print_state(state)

        if (
            age_1m
            <=MAX_FRESH_1M_MINUTES
        ):

            print(
                "[MONITOR MODE] "
                "PRECISE 1M"
            )

            monitor_precise_1m(
                state,
                one_m
            )

        else:

            print(
                "[MONITOR MODE] "
                "EMERGENCY"
            )

            print(
                "[MONITOR] "
                f"1M stale: "
                f"{age_1m:.1f} min"
            )

            emergency_monitor(
                state,
                age_1m
            )

        state["last_run"]=iso_now()

        save_state(state)

        write_log(
            "RUN_ACTIVE_MONITOR",
            state,
            {
                "age_1m":age_1m,
                "age_5m":age_5m,
                "age_15m":age_15m,
                "age_1h":age_1h
            }
        )

        print_state(state)

        print(
            "[DONE] "
            "Active position "
            "monitoring complete"
        )

        return

    # ========================================================
    # IDLE
    # ========================================================

    print(
        "[POSITION] "
        "No active position"
    )

    if (
        age_5m
        >MAX_5M_FRESH_MINUTES
    ):

        print(
            "[SIGNAL BLOCK] "
            f"5M stale: "
            f"{age_5m:.1f} min"
        )

        write_log(
            "SIGNAL_BLOCK_5M_STALE",
            state
        )

        return

    if (
        age_15m
        >MAX_15M_FRESH_MINUTES
    ):

        print(
            "[SIGNAL BLOCK] "
            f"15M stale: "
            f"{age_15m:.1f} min"
        )

        write_log(
            "SIGNAL_BLOCK_15M_STALE",
            state
        )

        return

    if (
        age_1h
        >MAX_1H_FRESH_MINUTES
    ):

        print(
            "[SIGNAL BLOCK] "
            f"1H stale: "
            f"{age_1h:.1f} min"
        )

        write_log(
            "SIGNAL_BLOCK_1H_STALE",
            state
        )

        return

    signal=find_signal(
        m15,
        five,
        one_h
    )

    if signal is None:

        state["last_run"]=iso_now()

        save_state(state)

        write_log(
            "NO_SIGNAL",
            state
        )

        print(
            "[DONE] No signal"
        )

        return

    if cooldown_active(
        state,
        signal["direction"]
    ):

        state["last_run"]=iso_now()

        save_state(state)

        write_log(
            "SIGNAL_BLOCK_COOLDOWN",
            state,
            {
                "direction":
                signal["direction"]
            }
        )

        return

    previous_setup=state.get(
        "setup_id"
    )

    if (
        previous_setup
        and previous_setup
        ==signal["setup_id"]
    ):

        print(
            "[SIGNAL BLOCK] "
            "Same setup already "
            "processed"
        )

        write_log(
            "SIGNAL_BLOCK_DUPLICATE_SETUP",
            state,
            {
                "setup_id":
                signal["setup_id"]
            }
        )

        return

    entry=safe_float(
        one_m["Close"].iloc[-1]
    )

    if entry is None:

        print(
            "[SIGNAL BLOCK] "
            "Invalid entry price"
        )

        return

    print(
        "[ENTRY PRICE]",
        entry
    )

    position=calculate_position(
        signal,
        entry,
        m15
    )

    if position is None:

        print(
            "[SIGNAL BLOCK] "
            "Unable to calculate "
            "position"
        )

        write_log(
            "POSITION_CALC_FAILED",
            state
        )

        return

    # ========================================================
    # NEW POSITION
    # ========================================================

    state["status"]="ACTIVE"

    state["direction"]=(
        signal["direction"]
    )

    state["entry"]=position["entry"]
    state["sl"]=position["sl"]
    state["tp1"]=position["tp1"]
    state["tp2"]=position["tp2"]
    state["tp3"]=position["tp3"]
    state["risk"]=position["risk"]

    state["stage"]="INITIAL"

    state["signal_time"]=iso_now()
    state["last_signal_time"]=(
        state["signal_time"]
    )

    state["signal_id"]=(
        f"{signal['direction']}_"
        f"{position['entry']}_"
        f"{signal['m15_score']}_"
        f"{signal['five_score']}"
    )

    state["setup_id"]=(
        signal["setup_id"]
    )

    state["m15_score"]=(
        signal["m15_score"]
    )

    state["five_score"]=(
        signal["five_score"]
    )

    state["rsi"]=signal["rsi"]
    state["adx"]=signal["adx"]
    state["atr"]=signal["atr"]
    state["last_price"]=entry

    state["tp1_hit"]=False
    state["tp2_hit"]=False
    state["tp3_hit"]=False

    save_state(state)

    write_log(
        "NEW_SIGNAL",
        state,
        {
            "signal":signal
        }
    )

    print_state(state)

    send_entry_alert(
        state,
        signal
    )

    state["last_run"]=iso_now()

    save_state(state)

    write_log(
        "RUN_NEW_POSITION",
        state
    )

    print(
        "[DONE] "
        "New position created"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__=="__main__":

    print(
        "[ENTRYPOINT] "
        "__name__ == '__main__'",
        flush=True
    )

    try:

        main()

    except KeyboardInterrupt:

        print(
            "[STOP] "
            "KeyboardInterrupt"
        )

    except Exception as e:

        print(
            "===================================="
        )

        print(
            "[FATAL BOT ERROR]",
            repr(e)
        )

        print(
            "===================================="
        )

        try:

            write_log(
                "FATAL_ERROR",
                None,
                {
                    "error":repr(e)
                }
            )

        except:
            pass

        raise
