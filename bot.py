# GOLD FUTURES SMART SIGNAL BOT V18.1
import os,json,time,requests,yfinance as yf,pandas as pd,numpy as np
from datetime import datetime,timezone,timedelta

V="18.1.0"; T="GC=F"; STATE="signal_state.json"; LOG="bot_log.json"
TOKEN=os.getenv("TELEGRAM_TOKEN",""); CHAT=os.getenv("TELEGRAM_CHAT_ID","")
KST=timezone(timedelta(hours=9))

# ===== SIGNAL =====
M15_MIN=7; FIVE_MIN=3; ADX_MIN=17; ATR_MIN=.5; ATR_MAX=20
RSI_L=(52,68); RSI_S=(32,48); BODY=.35; DIST=1.2

# ===== RISK =====
RISK_ATR=1.8; MIN_RISK=1.2; MAX_RISK=2.8
TP=(1.2,2.0,3.0); STRONG_TP=(1.3,2.2,3.5)

# ===== BACKTEST =====
BT_PERIOD="60d"
BT_MIN_TRADES=20
BT_MIN_WINRATE=45.0
BT_MIN_PF=1.05
BT_MAX_DD=25.0

# ===== LIVE =====
PRICE_MAX_AGE=8
COOLDOWN=45
FAIL_ALERT_COOLDOWN = 4 * 3600  # 백테스트 실패 알림 쿨다운 (4시간, 초 단위)


def now():
    return datetime.now(KST)

def ts():
    return now().strftime("%Y-%m-%d %H:%M:%S")

def m(x):
    return f"${float(x):,.2f}"

def save(p,x):
    q=p+".tmp"
    with open(q,"w",encoding="utf8") as f:
        json.dump(x,f,ensure_ascii=False,indent=2)
    os.replace(q,p)

def load(p,d):
    try:
        with open(p,encoding="utf8") as f:return json.load(f)
    except:return d

def tg(x):
    if not TOKEN or not CHAT:return False
    try:
        r=requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id":CHAT,"text":x,"parse_mode":"HTML"},
            timeout=15)
        print("[TELEGRAM]","SENT" if r.ok else "FAIL")
        return r.ok
    except Exception as e:
        print("[TG ERROR]",e)
        return False

def log(t,**x):
    a=load(LOG,[])
    if not isinstance(a,list):a=[]
    a.append({"time":ts(),"type":t,**x})
    save(LOG,a[-500:])


# =========================================================
# STATE
# =========================================================
def default():
    return {
        "version":V,"status":"IDLE","direction":None,
        "entry":None,"sl":None,"tp1":None,"tp2":None,"tp3":None,
        "stage":"INITIAL","signal_time":None,
        "exit_price":None,"exit_reason":None,
        "score":0,"strength":"NORMAL","setup_hash":None,
        "backtest":{}, "last_fail_alert_time": 0
    }

def get_state():
    s=load(STATE,default())
    d=default(); d.update(s); d["version"]=V
    if s.get("version")!=V:
        print(f"[STATE MIGRATION] {s.get('version')} -> {V}")
    save(STATE,d)
    return d

def show(s):
    print("\n===== STATE =====")
    for k in ["status","direction","entry","sl","tp1","tp2","tp3","stage"]:
        print(f"{k:10}: {s.get(k)}")
    print("=================\n")


# =========================================================
# DATA
# =========================================================
def clean(x):
    if x is None or x.empty:return pd.DataFrame()
    if isinstance(x.columns,pd.MultiIndex):
        x.columns=x.columns.get_level_values(0)
    x=x.copy()
    for c in ["Open","High","Low","Close","Volume"]:
        if c in x:x[c]=pd.to_numeric(x[c],errors="coerce")
    x=x.dropna(subset=["Open","High","Low","Close"])
    if x.index.tz is None:x.index=x.index.tz_localize("UTC")
    return x

def getdata(tf,period):
    for i in range(3):
        try:
            x=clean(yf.download(
                T,period=period,interval=tf,
                progress=False,auto_adjust=False,threads=False))
            if len(x)>50:
                return x.iloc[:-1]
        except Exception as e:
            print("[DATA ERROR]",tf,e)
        time.sleep(1)
    return pd.DataFrame()

def age(x):
    if x.empty:return 9999
    t=x.index[-1]
    if t.tzinfo is None:t=t.tz_localize("UTC")
    return max(0,(datetime.now(timezone.utc)-t).total_seconds()/60)


# =========================================================
# INDICATORS
# =========================================================
def addind(x):
    x=x.copy()
    x["ema20"]=x.Close.ewm(span=20,adjust=False).mean()
    x["ema50"]=x.Close.ewm(span=50,adjust=False).mean()

    d=x.Close.diff()
    g=d.clip(lower=0).rolling(14).mean()
    l=(-d.clip(upper=0)).rolling(14).mean()
    rs=g/l.replace(0,np.nan)
    x["rsi"]=100-100/(1+rs)

    tr=pd.concat([
        x.High-x.Low,
        (x.High-x.Close.shift()).abs(),
        (x.Low-x.Close.shift()).abs()],axis=1).max(axis=1)

    x["atr"]=tr.rolling(14).mean()

    up=x.High.diff()
    dn=-x.Low.diff()
    plus=up.where((up>dn)&(up>0),0)
    minus=dn.where((dn>up)&(dn>0),0)
    a=tr.rolling(14).mean()
    p=100*plus.rolling(14).mean()/a
    n=100*minus.rolling(14).mean()/a
    dx=100*(p-n).abs()/(p+n).replace(0,np.nan)
    x["adx"]=dx.rolling(14).mean()

    x["body"]=(x.Close-x.Open).abs()/(x.High-x.Low).replace(0,np.nan)
    x["pos"]=(x.Close-x.Low)/(x.High-x.Low).replace(0,np.nan)
    return x.dropna()


# =========================================================
# SIGNAL SCORE
# =========================================================
def score15(x,d,i=-1):
    r=x.iloc[i]; p=x.iloc[i-1]; s=0

    if d=="LONG":
        s+=r.Close>r.ema20
        s+=r.ema20>r.ema50
        s+=r.Close>p.High
        s+=r.Close>r.Open and r.body>=BODY
        s+=RSI_L[0]<=r.rsi<=RSI_L[1]
        s+=r.adx>=ADX_MIN
        s+=r.pos>=.65
        s+=r.Close<=r.ema20+r.atr*DIST
    else:
        s+=r.Close<r.ema20
        s+=r.ema20<r.ema50
        s+=r.Close<p.Low
        s+=r.Close<r.Open and r.body>=BODY
        s+=RSI_S[0]<=r.rsi<=RSI_S[1]
        s+=r.adx>=ADX_MIN
        s+=r.pos<=.35
        s+=r.Close>=r.ema20-r.atr*DIST

    return int(s)

def score5(x,d,i=-1):
    r=x.iloc[i];s=0
    if d=="LONG":
        s+=r.Close>r.ema20
        s+=r.Close>r.Open and r.body>=BODY
        s+=r.rsi>=50
        s+=r.adx>=ADX_MIN
    else:
        s+=r.Close<r.ema20
        s+=r.Close<r.Open and r.body>=BODY
        s+=r.rsi<=50
        s+=r.adx>=ADX_MIN
    return int(s)

def trend(x,d,i=-1):
    r=x.iloc[i]
    return (r.Close>r.ema20 and r.ema20>r.ema50) if d=="LONG" else \
           (r.Close<r.ema20 and r.ema20<r.ema50)


# =========================================================
# POSITION CALC
# =========================================================
def levels(x,d,i):
    r=x.iloc[i]; e=float(r.Close); a=float(r.atr)
    sw=x.iloc[max(0,i-7):i+1]

    if d=="LONG":
        sl0=float(sw.Low.min())-a*.25
        risk=max(e-sl0,a*MIN_RISK)
        risk=min(risk,a*MAX_RISK)
        sl=e-risk
    else:
        sl0=float(sw.High.max())+a*.25
        risk=max(sl0-e,a*MIN_RISK)
        risk=min(risk,a*MAX_RISK)
        sl=e+risk

    return e,sl,risk

def targets(e,risk,strong,d):
    z=STRONG_TP if strong else TP
    if d=="LONG":return [e+risk*q for q in z]
    return [e-risk*q for q in z]


# =========================================================
# BACKTEST ENGINE
# =========================================================
def backtest(x15,x5):
    print("\n====================================")
    print(" HISTORICAL BACKTEST")
    print("====================================")

    if len(x15)<300 or len(x5)<300:
        return {"pass":False,"reason":"INSUFFICIENT_DATA"}

    trades=[]
    equity=0; peak=0; dd=0
    wins=losses=0
    gross_win=gross_loss=0

    start=100
    last_exit=-99

    for i in range(start,len(x15)-20):
        if i-last_exit<2:continue

        d=None; a=score15(x15,"LONG",i); b=score15(x15,"SHORT",i)

        if a>=M15_MIN and a>b:
            d="LONG"
        elif b>=M15_MIN and b>a:
            d="SHORT"
        else:
            continue

        r=x15.iloc[i]
        if r.atr<ATR_MIN or r.atr>ATR_MAX or r.adx<ADX_MIN:
            continue

        t=x15.index[i]
        q=x5.loc[:t]
        if len(q)<30:continue

        j=len(q)-1
        b5=score5(q,d,j)
        if b5<FIVE_MIN:continue

        strong=a>=8 and b5>=4
        e,sl,risk=levels(x15,d,i)
        t1,t2,t3=targets(e,risk,strong,d)

        result=None; exit_price=None

        for k in range(i+1,min(i+80,len(x15))):
            c=x15.iloc[k]
            hi=float(c.High);lo=float(c.Low)

            if d=="LONG":
                if lo<=sl:
                    result="LOSS";exit_price=sl;break
                if hi>=t1:
                    sl2=e
                    if lo<=sl2:
                        result="BE";exit_price=e;break
                    if hi>=t2:
                        sl3=t1
                        if lo<=sl3:
                            result="TP1";exit_price=t1;break
                        if hi>=t3:
                            result="WIN";exit_price=t3;break
            else:
                if hi>=sl:
                    result="LOSS";exit_price=sl;break
                if lo<=t1:
                    sl2=e
                    if hi>=sl2:
                        result="BE";exit_price=e;break
                    if lo<=t2:
                        sl3=t1
                        if hi>=sl3:
                            result="TP1";exit_price=t1;break
                        if lo<=t3:
                            result="WIN";exit_price=t3;break

        if result is None:continue

        if d=="LONG": ret=(exit_price-e)/risk
        else: ret=(e-exit_price)/risk

        trades.append(ret)
        equity+=ret
        peak=max(peak,equity)
        dd=max(dd,peak-equity)
        last_exit=k

        if ret>0:
            wins+=1;gross_win+=ret
        elif ret<0:
            losses+=1;gross_loss+=abs(ret)

    n=len(trades)
    if not n:
        return {"pass":False,"reason":"NO_TRADES","trades":0}

    wr=wins/n*100
    pf=gross_win/gross_loss if gross_loss else 99
    result={
        "trades":n,
        "wins":wins,
        "losses":losses,
        "winrate":round(wr,1),
        "profit_factor":round(pf,2),
        "max_drawdown":round(dd,2),
        "net_r":round(sum(trades),2)
    }

    result["pass"]=(
        n>=BT_MIN_TRADES and
        wr>=BT_MIN_WINRATE and
        pf>=BT_MIN_PF and
        dd<=BT_MAX_DD)

    print(f"Trades       : {n}")
    print(f"Win Rate     : {wr:.1f}%")
    print(f"Profit Factor: {pf:.2f}")
    print(f"Max DD       : {dd:.2f}R")
    print(f"Net Result   : {sum(trades):+.2f}R")
    print("RESULT       :", "PASS" if result["pass"] else "FAIL")

    return result


# =========================================================
# LIVE PRICE
# =========================================================
def live():
    try:
        f=yf.Ticker(T).fast_info
        for k in ["last_price","regularMarketPrice"]:
            if f.get(k):
                return float(f[k]),0,"FAST"
    except:pass

    try:
        x=clean(yf.download(
            T,period="1d",interval="1m",
            progress=False,auto_adjust=False,threads=False))
        if not x.empty:
            t=x.index[-1]
            if t.tzinfo is None:t=t.tz_localize("UTC")
            a=(datetime.now(timezone.utc)-t).total_seconds()/60
            return float(x.Close.iloc[-1]),a,"1M"
    except:pass

    return None,9999,"NONE"


# =========================================================
# ALERTS
# =========================================================
def entry_alert(s,bt):
    d=s["direction"]
    icon="🟢" if d=="LONG" else "🔴"
    return f"""<b>🟡 GOLD FUTURES · 신규 매매 신호</b>
━━━━━━━━━━━━━━━━━━━━

{icon} <b>{d} · {'매수' if d=='LONG' else '매도'}</b>
💰 진입가　<b>{m(s['entry'])}</b>

<b>🎯 목표가</b>
├ TP1　{m(s['tp1'])}
├ TP2　{m(s['tp2'])}
└ TP3　{m(s['tp3'])}

<b>🛡 보호 손절</b>
└ SL　 <b>{m(s['sl'])}</b>

<b>📊 사전 백테스트</b>
├ 거래 횟수　{bt['trades']}회
├ 승률　　　{bt['winrate']:.1f}%
├ Profit Factor　{bt['profit_factor']:.2f}
└ 누적 결과　{bt['net_r']:+.2f}R

🔥 신호 강도　<b>{s['strength']}</b>

━━━━━━━━━━━━━━━━━━━━
⏱ {now().strftime('%H:%M KST')}
📌 자동 포지션 추적 시작
━━━━━━━━━━━━━━━━━━━━"""


def tp_alert(s,n,p):
    e=s["entry"]
    gain=(p/e-1)*100 if s["direction"]=="LONG" else (e/p-1)*100
    sl=s["entry"] if n==1 else s["tp1"]
    nxt="TP2" if n==1 else "TP3"
    return f"""<b>🟢 GOLD FUTURES · TP{n} 달성</b>
━━━━━━━━━━━━━━━━━━━━

📈 {s['direction']} 포지션
💰 진입가　{m(e)}
🎯 TP{n}　　<b>{m(p)}</b>
📈 누적 수익　<b>{gain:+.2f}%</b>

🛡 보호 SL　{m(sl)}
🎯 다음 목표　{nxt}

━━━━━━━━━━━━━━━━━━━━
🔐 수익 보호 모드 유지
━━━━━━━━━━━━━━━━━━━━"""


def sl_alert(s,p,age,src):
    e=s["entry"]
    loss=(p/e-1)*100 if s["direction"]=="LONG" else (e/p-1)*100
    return f"""<b>🔴 GOLD FUTURES · 리스크 종료</b>
━━━━━━━━━━━━━━━━━━━━

📉 {s['direction']} 포지션

💰 진입가　{m(e)}
🛑 청산가　<b>{m(p)}</b>
📉 손익률　<b>{loss:+.2f}%</b>

📡 가격 출처　{src}
⏱ 데이터 지연　{age:.1f}분

━━━━━━━━━━━━━━━━━━━━
⚠️ 보호 손절 기준에 따른 자동 종료
━━━━━━━━━━━━━━━━━━━━"""


def stale_alert(s,p,a):
    return f"""<b>⚠️ GOLD FUTURES · 가격 확인 지연</b>
━━━━━━━━━━━━━━━━━━━━

📌 포지션　{s['direction']}
💰 최근 가격　{m(p) if p else '조회 실패'}
🛡 보호 SL　{m(s['sl'])}
⏱ 지연　　　{a:.1f}분

⏳ 실시간 가격 확보 후
SL/TP 판정을 재개합니다.

━━━━━━━━━━━━━━━━━━━━
🔒 포지션 상태 유지
━━━━━━━━━━━━━━━━━━━━"""


# =========================================================
# ACTIVE MONITOR
# =========================================================
def close(s,p,reason):
    s.update({
        "status":"IDLE","exit_price":p,
        "exit_reason":reason,"direction":None,
        "entry":None,"sl":None,"tp1":None,
        "tp2":None,"tp3":None,"stage":"INITIAL"})
    save(STATE,s)
    log("EXIT",price=p,reason=reason)


def monitor(s):
    p,a,src=live()

    if p is None or a>PRICE_MAX_AGE:
        tg(stale_alert(s,p,a))
        print(f"[MONITOR] STALE {a:.1f}m")
        return

    d=s["direction"]; sl=s["sl"]; st=s["stage"]

    print(f"[LIVE] {m(p)} age={a:.1f}m stage={st}")

    if d=="LONG":
        if p<=sl:
            tg(sl_alert(s,p,a,src));close(s,p,"SL");return
        if st=="INITIAL" and p>=s["tp1"]:
            s["stage"]="TP1_TRAIL";s["sl"]=s["entry"]
            save(STATE,s);tg(tp_alert(s,1,p));return
        if st=="TP1_TRAIL" and p>=s["tp2"]:
            s["stage"]="TP2_TRAIL";s["sl"]=s["tp1"]
            save(STATE,s);tg(tp_alert(s,2,p));return
        if st=="TP2_TRAIL" and p>=s["tp3"]:
            tg(tp_alert(s,3,p));close(s,p,"TP3");return

    else:
        if p>=sl:
            tg(sl_alert(s,p,a,src));close(s,p,"SL");return
        if st=="INITIAL" and p<=s["tp1"]:
            s["stage"]="TP1_TRAIL";s["sl"]=s["entry"]
            save(STATE,s);tg(tp_alert(s,1,p));return
        if st=="TP1_TRAIL" and p<=s["tp2"]:
            s["stage"]="TP2_TRAIL";s["sl"]=s["tp1"]
            save(STATE,s);tg(tp_alert(s,2,p));return
        if st=="TP2_TRAIL" and p<=s["tp3"]:
            tg(tp_alert(s,3,p));close(s,p,"TP3");return


# =========================================================
# LIVE SIGNAL
# =========================================================
def signal(x15,x5):
    if len(x15)<100 or len(x5)<100:return None

    best=None

    for d in ["LONG","SHORT"]:
        a=score15(x15,d)
        if a<M15_MIN:continue

        q=x5.loc[:x15.index[-1]]
        if len(q)<30:continue
        b=score5(q,d)

        if b<FIVE_MIN:continue

        r=x15.iloc[-1]
        if r.atr<ATR_MIN or r.atr>ATR_MAX or r.adx<ADX_MIN:continue

        if d=="LONG" and not RSI_L[0]<=r.rsi<=RSI_L[1]:continue
        if d=="SHORT" and not RSI_S[0]<=r.rsi<=RSI_S[1]:continue

        total=a*10+b*2+(1 if trend(x15,d) else 0)

        if best is None or total>best[0]:
            best=(total,d,a,b)

    if not best:return None

    total,d,a,b=best
    e,sl,risk=levels(x15,d,-1)
    strong=a>=8 and b>=4
    t1,t2,t3=targets(e,risk,strong,d)

    return {
        "status":"ACTIVE","direction":d,
        "entry":e,"sl":sl,"tp1":t1,"tp2":t2,"tp3":t3,
        "stage":"INITIAL","score":total,
        "strength":"STRONG" if strong else "NORMAL",
        "signal_time":ts()
    }


# =========================================================
# MAIN
# =========================================================
def main():
    print("====================================")
    print(f" GOLD FUTURES SMART SIGNAL BOT V{V}")
    print(" KST:",now().isoformat())
    print("====================================")

    s=get_state()
    show(s)

    if s["status"]=="ACTIVE":
        print("[POSITION] ACTIVE")
        monitor(s)
        show(get_state())
        return

    print("[DATA] Loading historical market data...")
    x15=addind(getdata("15m",BT_PERIOD))
    x5=addind(getdata("5m",BT_PERIOD))

    print(f"[DATA] 15M={len(x15)} 5M={len(x5)}")
    print(f"[FRESHNESS] 15M={age(x15):.1f}m 5M={age(x5):.1f}m")

    # ==========================================
    # 1. 반드시 백테스트 먼저
    # ==========================================
    bt=backtest(x15,x5)
    s["backtest"]=bt
    save(STATE,s)

    if not bt.get("pass"):
        print("[ENTRY] BACKTEST FAILED")
        current_time_epoch = time.time()
        last_fail_time = s.get("last_fail_alert_time", 0)

        # 마지막 실패 알림 후 4시간이 지난 경우에만 텔레그램 알림 발송
        if current_time_epoch - last_fail_time > FAIL_ALERT_COOLDOWN:
            tg(f"""<b>🟠 GOLD FUTURES · 신규 진입 보류</b>
━━━━━━━━━━━━━━━━━━━━

📊 사전 백테스트 결과

├ 거래 횟수　{bt.get('trades',0)}회
├ 승률　　　{bt.get('winrate',0):.1f}%
├ Profit Factor　{bt.get('profit_factor',0):.2f}
├ 최대 DD　 {bt.get('max_drawdown',0):.2f}R
└ 결과　　　<b>조건 미충족</b>

📌 백테스트 검증은 주기적으로 계속 수행됩니다.
━━━━━━━━━━━━━━━━━━━━""")
            s["last_fail_alert_time"] = current_time_epoch
            save(STATE, s)
        else:
            print("[ENTRY] Fail alert in cooldown period. Skipping telegram message.")
        return

    # 백테스트 통과 시 실패 알림 타이머 리셋
    s["last_fail_alert_time"] = 0
    save(STATE, s)

    print("[ENTRY] BACKTEST PASSED")

    # ==========================================
    # 2. 실시간 가격 확인
    # ==========================================
    p,a,src=live()

    if p is None:
        print("[ENTRY] LIVE PRICE UNAVAILABLE")
        return

    print(f"[LIVE PRICE] {m(p)} age={a:.1f}m source={src}")

    if a>PRICE_MAX_AGE and src!="FAST":
        print("[ENTRY] LIVE PRICE STALE")
        return

    # ==========================================
    # 3. 현재 신호 분석
    # ==========================================
    sig=signal(x15,x5)

    if not sig:
        print("[SIGNAL] No qualified setup")
        return

    dist=abs(p-sig["entry"])/sig["entry"]*100

    if dist>0.35:
        print(f"[ENTRY BLOCK] Price deviation {dist:.2f}%")
        return

    s.update(sig)
    save(STATE,s)

    tg(entry_alert(s,bt))
    log("ENTRY",
        direction=s["direction"],
        entry=s["entry"],
        score=s["score"],
        strength=s["strength"],
        backtest=bt)

    print("[ENTRY] POSITION CREATED")
    show(s)


if __name__=="__main__":
    try:
        main()
    except Exception as e:
        print("[FATAL]",repr(e))
        log("FATAL",error=repr(e))
        try:
            tg(f"""<b>⚠️ GOLD FUTURES · 시스템 오류</b>
━━━━━━━━━━━━━━━━━━━━

자동 분석 과정에서 오류가 발생했습니다.

⏱ {ts()}
🔧 <code>{str(e)[:400]}</code>

📌 기존 포지션 상태는 보존됩니다.
━━━━━━━━━━━━━━━━━━━━""")
        except:pass
        raise
