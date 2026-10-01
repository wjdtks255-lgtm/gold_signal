# GOLD FUTURES SMART SIGNAL BOT V17.5.0
import os,json,time,hashlib,requests,yfinance as yf
import pandas as pd,numpy as np
from datetime import datetime,timezone,timedelta

V="17.5.0"; TICKER="GC=F"
STATE="signal_state.json"; LOG="bot_log.json"
TOKEN=os.getenv("TELEGRAM_TOKEN",""); CHAT=os.getenv("TELEGRAM_CHAT_ID","")
KST=timezone(timedelta(hours=9))

# =========================
# SETTINGS
# =========================
MIN_M15=7; STRONG_M15=8; MIN_5M=3; MIN_ADX=17
MIN_ATR=.5; MAX_ATR=20; BODY=.35; MAX_DIST=1.2
LONG_RSI=(52,68); SHORT_RSI=(32,48)

RISK_ATR=1.8; MIN_RISK=1.2; MAX_RISK=2.8
TP=(1.2,2.0,3.0); STP=(1.3,2.2,3.5)

SIG_CD=45; SL_CD=120; TP3_CD=15
FRESH_1M=8; FRESH_5M=15; FRESH_15M=30; FRESH_1H=90
EMERGENCY_MAX=30

# =========================
# BASIC
# =========================
def now():
    return datetime.now(KST)

def ts():
    return now().strftime("%Y-%m-%d %H:%M:%S")

def money(x):
    return f"${float(x):,.2f}"

def pct(x):
    return f"{float(x):+.2f}%"

def save_json(path,obj):
    tmp=path+".tmp"
    with open(tmp,"w",encoding="utf-8") as f:
        json.dump(obj,f,ensure_ascii=False,indent=2)
    os.replace(tmp,path)

def load_json(path,default):
    try:
        with open(path,encoding="utf-8") as f:return json.load(f)
    except:return default

def telegram(msg):
    if not TOKEN or not CHAT:return False
    try:
        r=requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id":CHAT,"text":msg,"parse_mode":"HTML"},
            timeout=15)
        ok=r.ok
        print("[TELEGRAM]", "SENT" if ok else f"FAIL {r.status_code}")
        return ok
    except Exception as e:
        print("[TELEGRAM ERROR]",e); return False

# =========================
# STATE
# =========================
def default_state():
    return {
        "version":V,"status":"IDLE","direction":None,
        "entry":None,"sl":None,"tp1":None,"tp2":None,"tp3":None,
        "stage":"INITIAL","signal_time":None,"exit_time":None,
        "exit_price":None,"exit_reason":None,
        "score":0,"strength":"NORMAL",
        "last_signal":None,"last_sl":None,"last_tp3":None
    }

def state_load():
    s=load_json(STATE,default_state())
    d=default_state()
    d.update(s)

    old=s.get("version",V)
    if old!=V:
        print(f"[STATE MIGRATION] {old} -> {V}")

    # legacy stage migration
    if d.get("status")=="ACTIVE":
        if d.get("tp2_hit") and d.get("stage") in ("INITIAL","TP1_TRAIL"):
            d["stage"]="TP2_TRAIL"
        elif d.get("tp1_hit") and d.get("stage")=="INITIAL":
            d["stage"]="TP1_TRAIL"

    d["version"]=V
    save_json(STATE,d)
    return d

def state_print(s):
    print("\n====================================")
    print("STATE")
    print("Status    :",s.get("status"))
    print("Direction :",s.get("direction"))
    print("Entry     :",s.get("entry"))
    print("SL        :",s.get("sl"))
    print("TP1       :",s.get("tp1"))
    print("TP2       :",s.get("tp2"))
    print("TP3       :",s.get("tp3"))
    print("Stage     :",s.get("stage"))
    print("====================================\n")

def log_event(kind,data):
    x=load_json(LOG,[])
    if not isinstance(x,list):x=[]
    x.append({"time":ts(),"type":kind,**data})
    save_json(LOG,x[-500:])

# =========================
# DATA
# =========================
def clean(df):
    if df is None or df.empty:return pd.DataFrame()
    if isinstance(df.columns,pd.MultiIndex):
        df.columns=df.columns.get_level_values(0)
    df=df.copy()
    for c in ["Open","High","Low","Close","Volume"]:
        if c in df:df[c]=pd.to_numeric(df[c],errors="coerce")
    df=df.dropna(subset=["Open","High","Low","Close"])
    if df.index.tz is None:df.index=df.index.tz_localize("UTC")
    return df

def data(tf,period,retry=3):
    for i in range(retry):
        try:
            print(f"[DATA] {tf} attempt {i+1}/{retry}")
            x=clean(yf.download(TICKER,period=period,interval=tf,
                                progress=False,auto_adjust=False,threads=False))
            if len(x):
                # 마지막 미완성 캔들 제거
                x=x.iloc[:-1] if len(x)>3 else x
                print(f"[DATA] {tf} {len(x)} rows")
                return x
        except Exception as e:print("[DATA ERROR]",tf,e)
        time.sleep(1)
    return pd.DataFrame()

def age(df):
    if df.empty:return 9999
    t=df.index[-1]
    if t.tzinfo is None:t=t.tz_localize("UTC")
    return max(0,(datetime.now(timezone.utc)-t).total_seconds()/60)

def download_all():
    return {
        "1m":data("1m","7d"),
        "5m":data("5m","30d"),
        "15m":data("15m","60d"),
        "1h":data("1h","90d")
    }

# =========================
# INDICATORS
# =========================
def atr(df,n=14):
    h,l,c=df.High,df.Low,df.Close
    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    return tr.rolling(n).mean()

def adx(df,n=14):
    h,l,c=df.High,df.Low,df.Close
    up=h.diff(); dn=-l.diff()
    plus=up.where((up>dn)&(up>0),0)
    minus=dn.where((dn>up)&(dn>0),0)
    tr=pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    a=tr.rolling(n).mean()
    p=100*plus.rolling(n).mean()/a
    m=100*minus.rolling(n).mean()/a
    dx=100*(p-m).abs()/(p+m).replace(0,np.nan)
    return dx.rolling(n).mean()

def ind(df):
    x=df.copy()
    x["ema20"]=x.Close.ewm(span=20,adjust=False).mean()
    x["ema50"]=x.Close.ewm(span=50,adjust=False).mean()
    d=x.Close.diff()
    gain=d.clip(lower=0).rolling(14).mean()
    loss=(-d.clip(upper=0)).rolling(14).mean()
    rs=gain/loss.replace(0,np.nan)
    x["rsi"]=100-(100/(1+rs))
    x["atr"]=atr(x)
    x["adx"]=adx(x)
    x["body"]=(x.Close-x.Open).abs()/(x.High-x.Low).replace(0,np.nan)
    x["pos"]=(x.Close-x.Low)/(x.High-x.Low).replace(0,np.nan)
    return x.dropna()

# =========================
# SCORING
# =========================
def score15(x,d):
    r=x.iloc[-1]; p=x.iloc[-2]; s=0
    if d=="LONG":
        if r.Close>r.ema20:s+=1
        if r.ema20>r.ema50:s+=1
        if r.Close>p.High:s+=1
        if r.Close>r.Open and r.body>=BODY:s+=1
        if 52<=r.rsi<=68:s+=1
        if r.adx>=MIN_ADX:s+=1
        if r.pos>=.65:s+=1
        if r.Close<=r.ema20+r.atr*MAX_DIST:s+=1
    else:
        if r.Close<r.ema20:s+=1
        if r.ema20<r.ema50:s+=1
        if r.Close<p.Low:s+=1
        if r.Close<r.Open and r.body>=BODY:s+=1
        if 32<=r.rsi<=48:s+=1
        if r.adx>=MIN_ADX:s+=1
        if r.pos<=.35:s+=1
        if r.Close>=r.ema20-r.atr*MAX_DIST:s+=1
    return s

def score5(x,d):
    r=x.iloc[-1];s=0
    if d=="LONG":
        if r.Close>r.ema20:s+=1
        if r.Close>r.Open and r.body>=BODY:s+=1
        if r.rsi>=50:s+=1
        if r.adx>=MIN_ADX:s+=1
    else:
        if r.Close<r.ema20:s+=1
        if r.Close<r.Open and r.body>=BODY:s+=1
        if r.rsi<=50:s+=1
        if r.adx>=MIN_ADX:s+=1
    return s

def h1trend(x,d):
    r=x.iloc[-1]
    return (r.Close>r.ema20 and r.ema20>r.ema50) if d=="LONG" else \
           (r.Close<r.ema20 and r.ema20<r.ema50)

# =========================
# PRICE
# =========================
def live_price():
    print("[EMERGENCY PRICE] Starting multi-source price lookup")

    # 1. fast_info
    try:
        fi=yf.Ticker(TICKER).fast_info
        for k in ("last_price","regularMarketPrice"):
            v=fi.get(k)
            if v and float(v)>0:
                print("[LIVE] fast_info",v)
                return float(v),0,"fast_info"
    except Exception as e:print("[LIVE ERROR]",e)

    # 2. yfinance 1m
    try:
        x=clean(yf.download(TICKER,period="1d",interval="1m",
                            progress=False,auto_adjust=False,threads=False))
        if not x.empty:
            t=x.index[-1]
            if t.tzinfo is None:t=t.tz_localize("UTC")
            a=(datetime.now(timezone.utc)-t).total_seconds()/60
            v=float(x.Close.iloc[-1])
            print(f"[EMERGENCY PRICE] history 1m = {money(v)} age={a:.1f}m")
            return v,a,"yfinance_1m"
    except Exception as e:print("[1M PRICE ERROR]",e)

    # 3. Yahoo chart
    try:
        u=f"https://query1.finance.yahoo.com/v8/finance/chart/{TICKER}?interval=1m&range=1d"
        z=requests.get(u,timeout=10,headers={"User-Agent":"Mozilla/5.0"}).json()["chart"]["result"][0]
        q=z.get("indicators",{}).get("quote",[{}])[0].get("close",[])
        tm=z.get("timestamp",[])
        for i in range(len(q)-1,-1,-1):
            if q[i] is not None:
                t=datetime.fromtimestamp(tm[i],timezone.utc)
                a=(datetime.now(timezone.utc)-t).total_seconds()/60
                v=float(q[i])
                print(f"[YAHOO] {money(v)} age={a:.1f}m")
                return v,a,"yahoo_chart"
    except Exception as e:print("[YAHOO ERROR]",e)

    return None,9999,"none"

# =========================
# ALERTS
# =========================
def entry_msg(s,m15,five,h1):
    d=s["direction"]; arrow="🟢 LONG · 매수" if d=="LONG" else "🔴 SHORT · 매도"
    e=s["entry"]; sl=s["sl"]; t1=s["tp1"];t2=s["tp2"];t3=s["tp3"]
    risk=abs(e-sl)
    rr=abs(t3-e)/risk if risk else 0
    r=m15.iloc[-1]
    strength="🔥 STRONG" if s["strength"]=="STRONG" else "NORMAL"
    return f"""<b>🟡 GOLD FUTURES · 신규 매매 신호</b>
━━━━━━━━━━━━━━━━━━━━

<b>📌 {arrow}</b>
💰 진입가　<b>{money(e)}</b>

<b>🎯 목표가</b>
├ TP1　{money(t1)} ({pct((t1/e-1)*100) if d=="LONG" else pct((e/t1-1)*100)})
├ TP2　{money(t2)}
└ TP3　{money(t3)}

<b>🛡 리스크 관리</b>
└ 손절가　<b>{money(sl)}</b>

<b>📊 시장 분석</b>
├ 15분 점수　{m15.attrs.get("score","-")}/8
├ 5분 점수　 {five.attrs.get("score","-")}/4
├ 1시간 추세　{"상승 정렬" if h1 and d=="LONG" else "하락 정렬" if h1 else "중립"}
├ RSI　　　　{r.rsi:.1f}
├ ADX　　　　{r.adx:.1f}
└ ATR　　　　{r.atr:.2f}

🔥 신호 강도　<b>{strength}</b>
📐 TP3 기준 R/R　1 : {rr:.2f}

━━━━━━━━━━━━━━━━━━━━
⏱ {now().strftime("%H:%M KST")}
📌 포지션 추적 시작
━━━━━━━━━━━━━━━━━━━━"""

def tp_msg(s,n,price):
    d=s["direction"]; p=s["entry"]
    gain=(price/p-1)*100 if d=="LONG" else (p/price-1)*100
    if n==1:
        extra=f"🛡 손절가 → 진입가 {money(s['entry'])}"
        stage="TP1 → TP2 추적"
    elif n==2:
        extra=f"🛡 손절가 → TP1 {money(s['tp1'])}"
        stage="TP2 → TP3 추적"
    else:
        extra="🔓 포지션 추적 종료"
        stage="거래 종료"
    return f"""<b>🟢 GOLD FUTURES · TP{n} 달성</b>
━━━━━━━━━━━━━━━━━━━━

📈 {d} 포지션
💰 진입가　{money(p)}
🎯 TP{n}　　<b>{money(price)}</b>
📈 누적 수익　<b>{gain:+.2f}%</b>

{extra}

📌 현재 단계　{stage}
⏱ {now().strftime("%H:%M KST")}

━━━━━━━━━━━━━━━━━━━━"""

def sl_msg(s,price,age,source):
    d=s["direction"]; e=s["entry"]
    loss=(price/e-1)*100 if d=="LONG" else (e/price-1)*100
    return f"""<b>🔴 GOLD FUTURES · 리스크 종료</b>
━━━━━━━━━━━━━━━━━━━━

📉 {d} 포지션 · 보호 손절

💰 진입가　　{money(e)}
🛑 청산가　　<b>{money(price)}</b>
📉 손익률　　<b>{loss:+.2f}%</b>

📊 청산 정보
├ 사유　　　보호 손절
├ 가격来源　{source}
└ 데이터 지연　{age:.1f}분

📌 포지션 상태　CLOSED
⏱ {now().strftime("%H:%M KST")}

━━━━━━━━━━━━━━━━━━━━
⚠️ 리스크 관리 기준에 따른 자동 종료"""

def stale_msg(s,price,age,source):
    return f"""<b>⚠️ GOLD FUTURES · 가격 데이터 지연</b>
━━━━━━━━━━━━━━━━━━━━

현재 실시간 가격 데이터가 지연되어
포지션 판정을 일시 보류합니다.

📌 포지션　{s['direction']}
💰 마지막 확인가　{money(price) if price else '조회 실패'}
🛡 보호 SL　{money(s['sl'])}
⏱ 데이터 지연　<b>{age:.1f}분</b>
📡 출처　{source}

━━━━━━━━━━━━━━━━━━━━
⏳ 실시간 가격 재확인 중
━━━━━━━━━━━━━━━━━━━━"""

# =========================
# POSITION
# =========================
def close(s,price,reason):
    s["status"]="IDLE";s["exit_price"]=price;s["exit_reason"]=reason
    s["exit_time"]=ts();s["direction"]=None
    s["entry"]=s["sl"]=s["tp1"]=s["tp2"]=s["tp3"]=None
    s["stage"]="INITIAL"
    save_json(STATE,s)
    log_event("EXIT",{"price":price,"reason":reason})

def hit_tp(s,n,price):
    s["stage"]=f"TP{n}_TRAIL"
    if n==1:s["sl"]=s["entry"]
    elif n==2:s["sl"]=s["tp1"]
    save_json(STATE,s)
    telegram(tp_msg(s,n,price))
    log_event("TP",{"level":n,"price":price})

def monitor(s,df1):
    d=s["direction"]
    p,age,src=live_price()

    if p is None:
        telegram(stale_msg(s,None,9999,"조회 실패"))
        print("[MONITOR] Price unavailable")
        return

    # 가격이 8분 초과 지연이면 SL/TP 판정 금지
    if age>FRESH_1M:
        print(f"[MONITOR] PRICE STALE {age:.1f}m")
        telegram(stale_msg(s,p,age,src))
        return

    print(f"[MONITOR] LIVE {money(p)} age={age:.1f}m")
    sl=s["sl"];t1=s["tp1"];t2=s["tp2"];t3=s["tp3"]
    st=s["stage"]

    if d=="LONG":
        if p<=sl:
            telegram(sl_msg(s,p,age,src))
            close(s,p,"SL")
            return
        if st=="INITIAL" and p>=t1:
            hit_tp(s,1,p);return
        if st=="TP1_TRAIL" and p>=t2:
            hit_tp(s,2,p);return
        if st=="TP2_TRAIL" and p>=t3:
            telegram(tp_msg(s,3,p))
            close(s,p,"TP3")
            return
    else:
        if p>=sl:
            telegram(sl_msg(s,p,age,src))
            close(s,p,"SL")
            return
        if st=="INITIAL" and p<=t1:
            hit_tp(s,1,p);return
        if st=="TP1_TRAIL" and p<=t2:
            hit_tp(s,2,p);return
        if st=="TP2_TRAIL" and p<=t3:
            telegram(tp_msg(s,3,p))
            close(s,p,"TP3")
            return

# =========================
# SIGNAL
# =========================
def setup(dfs):
    m15=ind(dfs["15m"]); f5=ind(dfs["5m"]); h1=ind(dfs["1h"])
    if min(map(len,[m15,f5,h1]))<60:return None

    best=None
    for d in ("LONG","SHORT"):
        a=score15(m15,d);b=score5(f5,d);ht=h1trend(h1,d)
        if a<MIN_M15 or b<MIN_5M:continue
        if a<MIN_M15 or a>8:continue
        r=m15.iloc[-1]
        if r.atr<MIN_ATR or r.atr>MAX_ATR or r.adx<MIN_ADX:continue
        if d=="LONG" and not(LONG_RSI[0]<=r.rsi<=LONG_RSI[1]):continue
        if d=="SHORT" and not(SHORT_RSI[0]<=r.rsi<=SHORT_RSI[1]):continue
        total=a*10+b*2+(1 if ht else 0)
        if best is None or total>best[0]:
            best=(total,d,a,b,ht,m15,f5,h1)

    if not best:return None
    total,d,a,b,ht,m15,f5,h1=best
    r=m15.iloc[-1];entry=float(r.Close)
    atrv=float(r.atr)

    # 최근 스윙 + ATR 기반 SL
    if d=="LONG":
        swing=float(m15.Low.tail(8).min())
        raw=entry-(entry-swing+atrv*.25)
        risk=entry-raw
        risk=max(risk,atrv*MIN_RISK)
        risk=min(risk,atrv*MAX_RISK)
        sl=entry-risk
        mult=STP if a>=STRONG_M15 and b>=4 else TP
        t1,t2,t3=[entry+risk*x for x in mult]
    else:
        swing=float(m15.High.tail(8).max())
        raw=entry+(swing-entry+atrv*.25)
        risk=raw-entry
        risk=max(risk,atrv*MIN_RISK)
        risk=min(risk,atrv*MAX_RISK)
        sl=entry+risk
        mult=STP if a>=STRONG_M15 and b>=4 else TP
        t1,t2,t3=[entry-risk*x for x in mult]

    strength="STRONG" if a>=STRONG_M15 and b>=4 else "NORMAL"
    key=f"{d}-{round(entry,1)}-{a}-{b}-{round(r.rsi,1)}"
    return {
        "direction":d,"entry":entry,"sl":sl,
        "tp1":t1,"tp2":t2,"tp3":t3,
        "stage":"INITIAL","score":total,
        "strength":strength,"signal_time":ts(),
        "setup_hash":hashlib.md5(key.encode()).hexdigest(),
        "_m15":m15,"_5m":f5,"_h1":h1
    }

def send_entry(s):
    m15=s.pop("_m15");f5=s.pop("_5m");h1=s.pop("_h1")
    m15.attrs["score"]=int(score15(m15,s["direction"]))
    f5.attrs["score"]=int(score5(f5,s["direction"]))
    telegram(entry_msg(s,m15,m15.attrs["score"],h1trend(h1,s["direction"])))
    save_json(STATE,s)
    log_event("ENTRY",{"direction":s["direction"],"entry":s["entry"]})

# =========================
# MAIN
# =========================
def main():
    print("====================================")
    print(f" GOLD FUTURES SMART SIGNAL BOT V{V}")
    print(" KST:",now().isoformat())
    print("====================================")

    s=state_load()
    state_print(s)

    dfs=download_all()
    ages={k:age(v) for k,v in dfs.items()}
    for k,v in ages.items():print(f"[DATA FRESHNESS] {k.upper()}: {v:.1f} min")

    if s["status"]=="ACTIVE":
        print("[POSITION] ACTIVE position detected")
        if ages["1m"]>FRESH_1M:
            print(f"[MONITOR MODE] EMERGENCY / 1M stale {ages['1m']:.1f}m")
        else:
            print("[MONITOR MODE] PRECISE")
        monitor(s,dfs["1m"])
        state_print(state_load())
        print("[DONE] Active position monitoring complete")
        return

    # 신규 진입에는 모든 핵심 데이터가 너무 오래되면 진입 금지
    if ages["15m"]>FRESH_15M or ages["5m"]>FRESH_5M:
        print("[ENTRY BLOCK] Market data stale")
        return

    sig=setup(dfs)
    if not sig:
        print("[SIGNAL] No qualified setup")
        return

    # 중복 신호 방지
    if s.get("last_signal")==sig["setup_hash"]:
        print("[SIGNAL] Duplicate setup")
        return

    sig["status"]="ACTIVE"
    sig["last_signal"]=sig["setup_hash"]
    s.update(sig)
    send_entry(s)

if __name__=="__main__":
    try:
        main()
    except Exception as e:
        print("[FATAL]",repr(e))
        try:
            log_event("FATAL",{"error":repr(e)})
            telegram(f"""<b>⚠️ GOLD FUTURES · 시스템 오류</b>
━━━━━━━━━━━━━━━━━━━━

자동매매 신호 시스템에서
예외가 발생했습니다.

⏱ {ts()}
🔧 오류: <code>{str(e)[:500]}</code>

📌 기존 포지션 상태는 보존됩니다.
━━━━━━━━━━━━━━━━━━━━""")
        except:pass
        raise
