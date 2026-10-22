import os,json,time,tempfile,math
from pathlib import Path
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import requests,numpy as np,pandas as pd,yfinance as yf

V='15.2.0'
TZ=ZoneInfo('Asia/Seoul')
SYMBOL='GC=F'
TV='https://www.tradingview.com/symbols/GC1!/'
SF=Path('signal_state.json')
LF=Path('bot_log.json')
TOKEN=os.getenv('TELEGRAM_TOKEN','').strip()
CHAT=(os.getenv('TELEGRAM_CHAT_ID') or os.getenv('CHAT_ID') or '').strip()

def now():
    return datetime.now(TZ)

def ts():
    return now().isoformat(timespec='seconds')

def safe(x):
    if isinstance(x,dict):
        return {str(k):safe(v) for k,v in x.items()}
    if isinstance(x,(list,tuple)):
        return [safe(v) for v in x]
    if isinstance(x,np.integer):
        return int(x)
    if isinstance(x,np.floating):
        return None if not np.isfinite(x) else float(x)
    if isinstance(x,np.bool_):
        return bool(x)
    if isinstance(x,(pd.Timestamp,datetime)):
        return x.isoformat()
    if isinstance(x,float) and not math.isfinite(x):
        return None
    return x

def write(path,obj):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix=path.name+'.',suffix='.tmp')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(safe(obj),f,ensure_ascii=False,indent=2,allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

def read(path,default):
    if not Path(path).exists():
        return default
    try:
        with open(path,encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        backup=Path(str(path)+'.corrupt-'+now().strftime('%Y%m%d-%H%M%S'))
        try:
            Path(path).replace(backup)
        except Exception:
            pass
        if str(path)==str(SF):
            raise RuntimeError(
                f'{path} 손상. 백업={backup}; 상태 확인 전 신규 진입 중단'
            ) from e
        print('Log read error:',e)
        return default

def log(event,**kw):
    a=read(LF,[])
    if not isinstance(a,list):
        a=[]
    a.append({'time':ts(),'event':event,**safe(kw)})
    write(LF,a[-500:])

def send(msg):
    if not TOKEN or not CHAT:
        print('Telegram 설정 누락: TELEGRAM_TOKEN / TELEGRAM_CHAT_ID')
        return False
    for i in range(3):
        try:
            r=requests.post(
                f'https://api.telegram.org/bot{TOKEN}/sendMessage',
                json={
                    'chat_id':CHAT,
                    'text':msg,
                    'parse_mode':'HTML',
                    'disable_web_page_preview':True
                },
                timeout=15
            )
            if r.ok and r.json().get('ok'):
                return True
            print('Telegram error:',r.status_code,r.text[:200])
            if 400<=r.status_code<500:
                return False
        except Exception as e:
            print('Telegram attempt',i+1,e)
        time.sleep(1+i)
    return False

def blank():
    return {
        'version':V,
        'status':'IDLE',
        'position':None,
        'last_signal_bar':None,
        'cooldown_until':None,
        'pending_alerts':[],
        'sent_event_keys':[],
        'stats':{
            'tp3':0,
            'stop_after_tp2':0,
            'stop_after_tp1':0,
            'stop_before_tp1':0
        }
    }

def valid_state(s):
    if not isinstance(s,dict):
        raise RuntimeError('signal_state.json은 JSON 객체여야 합니다.')
    defaults=blank()
    for k,v in defaults.items():
        s.setdefault(k,v)
    if s.get('status') not in ('IDLE','ACTIVE'):
        raise RuntimeError('잘못된 상태입니다. 신규 진입을 중단합니다.')
    if s['status']=='ACTIVE' and not isinstance(s.get('position'),dict):
        raise RuntimeError('ACTIVE 상태인데 포지션이 없습니다. 수동 복구가 필요합니다.')
    if not isinstance(s.get('pending_alerts'),list):
        raise RuntimeError('pending_alerts 데이터가 잘못되었습니다.')
    return s

def queue(s,event_key,msg,event):
    if event_key in s['sent_event_keys']:
        return
    s['sent_event_keys']=(s['sent_event_keys']+[event_key])[-500:]
    s['pending_alerts'].append({
        'key':event_key,
        'message':msg,
        'event':event
    })
    s['pending_alerts']=s['pending_alerts'][-50:]
    write(SF,s)
    log('alert_queued',key=event_key,event=event)

def flush(s):
    pending=[]
    for a in s.get('pending_alerts',[]):
        ok=send(a['message'])
        log('telegram_sent' if ok else 'telegram_failed',
            key=a['key'],event=a['event'])
        if not ok:
            pending.append(a)
    s['pending_alerts']=pending
    write(SF,s)

def queue_stats(s):
    st=s.setdefault('stats',{})
    keys=('tp3','stop_after_tp2','stop_after_tp1','stop_before_tp1')
    total=sum(int(st.get(k,0)) for k in keys)
    tp3=int(st.get('tp3',0))
    rate=100*tp3/total if total else 0
    msg=(
        '📊 <b>GOLD SMART SIGNAL V15.2 종료 통계</b>\n'
        '━━━━━━━━━━━━\n'
        f'총 종료 시그널: <code>{total}</code>\n'
        f'TP3 최종 도달: <code>{tp3}</code>\n'
        f'TP2 도달 후 보호 SL: <code>{st.get("stop_after_tp2",0)}</code>\n'
        f'TP1 도달 후 보호 SL: <code>{st.get("stop_after_tp1",0)}</code>\n'
        f'TP1 전 SL: <code>{st.get("stop_before_tp1",0)}</code>\n'
        f'TP3 기준 승률: <b>{rate:.1f}%</b>\n'
        '※ TP1·TP2는 중간 도달이며 최종 종료가 아닙니다.\n'
        f'🕒 {ts()}'
    )
    queue(s,'STATS:'+str(total)+':'+str(st),msg,'STATS')

def bars(period,interval):
    d=yf.Ticker(SYMBOL).history(
        period=period,
        interval=interval,
        auto_adjust=False,
        actions=False
    )
    if d is None or d.empty:
        return pd.DataFrame()
    d=d.rename(columns={c:c.lower() for c in d.columns})
    d=d[~d.index.duplicated(keep='last')].sort_index()
    # 진행 중인 마지막 캔들은 제외
    if len(d)>2:
        d=d.iloc[:-1].copy()
    return d.dropna(subset=['open','high','low','close'])

def indic(d):
    if d.empty:
        return d
    d=d.copy()
    c,h,l=d.close,d.high,d.low
    d['ema20']=c.ewm(span=20,adjust=False).mean()
    d['ema50']=c.ewm(span=50,adjust=False).mean()

    delta=c.diff()
    gain=delta.clip(lower=0).ewm(
        alpha=1/14,adjust=False,min_periods=14
    ).mean()
    loss=(-delta.clip(upper=0)).ewm(
        alpha=1/14,adjust=False,min_periods=14
    ).mean()
    d['rsi']=100-100/(1+gain/loss.replace(0,np.nan))

    prev=c.shift()
    tr=pd.concat([
        h-l,(h-prev).abs(),(l-prev).abs()
    ],axis=1).max(axis=1)
    d['atr']=tr.ewm(
        alpha=1/14,adjust=False,min_periods=14
    ).mean()

    up,down=h.diff(),-l.diff()
    plus=up.where((up>down)&(up>0),0.)
    minus=down.where((down>up)&(down>0),0.)
    a=d.atr.replace(0,np.nan)
    pdi=100*plus.ewm(
        alpha=1/14,adjust=False,min_periods=14
    ).mean()/a
    mdi=100*minus.ewm(
        alpha=1/14,adjust=False,min_periods=14
    ).mean()/a
    dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan)
    d['adx']=dx.ewm(
        alpha=1/14,adjust=False,min_periods=14
    ).mean()
    d['pdi']=pdi
    d['mdi']=mdi

    spread=(h-l).replace(0,np.nan)
    d['body']=(c-d.open).abs()/spread
    d['cpos']=(c-l)/spread
    return d

def last(d):
    if d.empty:
        return None
    r=d.iloc[-1]
    keys=[
        'close','open','high','low','ema20','ema50',
        'rsi','atr','adx','pdi','mdi','body','cpos'
    ]
    if any(k not in r or pd.isna(r[k]) for k in keys):
        return None
    return r

def signal(h,m15,m5):
    # H1 방향 + M15 눌림목 회복 + M5 진입 트리거
    a,b,c=last(h),last(m15),last(m5)
    if any(x is None for x in (a,b,c)):
        return None,{'reason':'indicator_warmup'}
    if min(len(h),len(m15),len(m5))<60:
        return None,{'reason':'insufficient_bars'}

    hp=h.iloc[-2]
    mp=m15.iloc[-2]
    cp=m5.iloc[-2]

    # 상위 시간봉 추세 및 기울기
    h1_up=(
        a.close>a.ema20>a.ema50
        and a.ema20>hp.ema20
    )
    h1_dn=(
        a.close<a.ema20<a.ema50
        and a.ema20<hp.ema20
    )
    m15_up=(
        b.close>b.ema20>b.ema50
        and b.ema20>mp.ema20
    )
    m15_dn=(
        b.close<b.ema20<b.ema50
        and b.ema20<mp.ema20
    )

    # 이전 15분봉이 EMA20 근처까지 눌린 후 추세 방향으로 회복
    pull_l=(
        mp.low<=mp.ema20+0.25*b.atr
        and b.close>b.ema20
        and b.close>=mp.close
    )
    pull_s=(
        mp.high>=mp.ema20-0.25*b.atr
        and b.close<b.ema20
        and b.close<=mp.close
    )

    # 5분봉 직전 고점/저점 돌파 또는 강한 방향성 캔들
    brk_l=c.close>cp.high and c.close>c.open
    brk_s=c.close<cp.low and c.close<c.open
    candle_l=c.cpos>=0.68 and c.body>=0.30 and c.close>c.open
    candle_s=c.cpos<=0.32 and c.body>=0.30 and c.close<c.open

    m5_l=(
        c.close>c.ema20
        and c.rsi>=50
        and c.pdi>c.mdi
        and (brk_l or candle_l)
    )
    m5_s=(
        c.close<c.ema20
        and c.rsi<=50
        and c.mdi>c.pdi
        and (brk_s or candle_s)
    )

    adx_ok=c.adx>=13 and b.adx>=11
    rsi_l=48<=c.rsi<=70
    rsi_s=30<=c.rsi<=52
    dist=abs(float(c.close)-float(c.ema20))/max(float(c.atr),1e-9)
    no_chase=dist<=1.35

    checks={
        'h1L':bool(h1_up),'h1S':bool(h1_dn),
        'm15L':bool(m15_up),'m15S':bool(m15_dn),
        'pullL':bool(pull_l),'pullS':bool(pull_s),
        'm5L':bool(m5_l),'m5S':bool(m5_s),
        'candleL':bool(candle_l),'candleS':bool(candle_s),
        'adx':bool(adx_ok),'rsiL':bool(rsi_l),'rsiS':bool(rsi_s),
        'noChase':bool(no_chase),
        'breakL':bool(brk_l),'breakS':bool(brk_s)
    }

    scores={
        'LONG':(
            2*checks['h1L']+2*checks['m15L']+
            2*checks['pullL']+2*checks['m5L']+
            checks['adx']+checks['rsiL']+checks['noChase']
        ),
        'SHORT':(
            2*checks['h1S']+2*checks['m15S']+
            2*checks['pullS']+2*checks['m5S']+
            checks['adx']+checks['rsiS']+checks['noChase']
        )
    }

    direction=None
    if (
        h1_up and m15_up and pull_l and m5_l
        and adx_ok and rsi_l and no_chase
        and scores['LONG']>=10
    ):
        direction='LONG'
    elif (
        h1_dn and m15_dn and pull_s and m5_s
        and adx_ok and rsi_s and no_chase
        and scores['SHORT']>=10
    ):
        direction='SHORT'

    return direction,{
        'scores':scores,'checks':checks,
        'price':float(c.close),'atr':float(c.atr),
        'rsi':float(c.rsi),'adx':float(c.adx),
        'bar':str(m5.index[-1]),
        'distance_atr':round(dist,2),
        'reason':'trend_pullback_trigger' if direction else 'filters_not_aligned'
    }

def money(x):
    return f'${float(x):,.2f}'

def key(kind,p):
    return f"{kind}:{p['id']}"

def entry_msg(p,info):
    direction=p['direction']
    emoji='🟢' if direction=='LONG' else '🔴'
    return (
        f"🏆 <b>GOLD SMART SIGNAL V{V} | PAPER</b>\n"
        f"{emoji} <b>{direction} 진입 시그널</b>\n"
        f"━━━━━━━━━━━━\n"
        f"📍 기준가 <b>{money(p['entry'])}</b>\n"
        f"🛑 손절 SL: {money(p['sl'])}\n"
        f"🎯 목표 TP1: {money(p['tp1'])}\n"
        f"🎯 목표 TP2: {money(p['tp2'])}\n"
        f"🏁 최종 목표 TP3: {money(p['tp3'])}\n"
        f"━━━━━━━━━━━━\n"
        f"LONG 점수 {info['scores']['LONG']} · "
        f"SHORT 점수 {info['scores']['SHORT']}\n"
        f"RSI {info['rsi']:.1f} | ADX {info['adx']:.1f} | ATR {info['atr']:.2f}\n"
        f"진입 근거: H1 추세 + M15 눌림목 + M5 돌파/반전 확인\n"
        f"🔒 TP3 또는 SL 종료 전 신규 진입 차단\n"
        f"🧪 모의 시그널이며 실제 주문은 실행하지 않습니다.\n"
        f"🕒 {ts()}\n"
        f"<a href='{TV}'>TradingView GC1! 차트</a>"
    )

def monitor(s,price,m5):
    p=s['position']
    d=p['direction']
    sl,tp1,tp2,tp3=[
        float(p[k]) for k in ('sl','tp1','tp2','tp3')
    ]

    # 5분봉의 고가/저가를 함께 확인
    bar=m5.iloc[-1]
    high=float(bar.high)
    low=float(bar.low)
    if d=='LONG':
        sl_hit=low<=sl
        tp1_hit=high>=tp1 and not p['tp1_hit']
        tp2_hit=high>=tp2 and not p['tp2_hit']
        tp3_hit=high>=tp3
    else:
        sl_hit=high>=sl
        tp1_hit=low<=tp1 and not p['tp1_hit']
        tp2_hit=low<=tp2 and not p['tp2_hit']
        tp3_hit=low<=tp3

    # 한 번의 5분봉 안에서 SL과 TP가 모두 걸렸다면 순서를 알 수 없으므로 SL 우선
    if sl_hit:
        st=s.setdefault('stats',{
            'tp3':0,'stop_after_tp2':0,
            'stop_after_tp1':0,'stop_before_tp1':0
        })
        cat=(
            'stop_after_tp2' if p.get('tp2_hit')
            else 'stop_after_tp1' if p.get('tp1_hit')
            else 'stop_before_tp1'
        )
        st[cat]=int(st.get(cat,0))+1
        msg=(
            f"🛑 <b>GOLD V{V} | 모의 종료: SL</b>\n"
            f"{d} · 진입 {money(p['entry'])}\n"
            f"체크 가격 {money(price)} · SL {money(sl)}\n"
            f"최고 도달 목표: "
            f"{'TP2' if p.get('tp2_hit') else 'TP1' if p.get('tp1_hit') else '없음'}\n"
            f"⚠️ 5분봉 안에서 발생 순서를 확정할 수 없어 보수적으로 처리했습니다.\n"
            f"🧪 모의 시그널 · {ts()}\n"
            f"<a href='{TV}'>TradingView GC1! 차트</a>"
        )
        queue(s,key('SL',p),msg,'SL')
        log('exit',reason='SL',price=price,position=p)
        s['status']='IDLE'
        s['position']=None
        s['cooldown_until']=(now()+timedelta(minutes=30)).isoformat()
        write(SF,s)
        queue_stats(s)
        return

    r=m5.iloc[-7:]
    atr=float(r.atr.iloc[-1])
    if not np.isfinite(atr) or atr<=0:
        atr=float(p.get('entry_atr',1.0))

    if tp1_hit:
        p['tp1_hit']=True
        # 보호 SL은 LONG은 위로만, SHORT는 아래로만 이동
        if d=='LONG':
            candidate=min(
                max(float(p['entry'])+0.05*atr,
                    float(r.low.min())-0.15*atr),
                price-0.15*atr
            )
            p['sl']=max(float(p['sl']),candidate)
        else:
            candidate=max(
                min(float(p['entry'])-0.05*atr,
                    float(r.high.max())+0.15*atr),
                price+0.15*atr
            )
            p['sl']=min(float(p['sl']),candidate)

        queue(
            s,key('TP1',p),
            f"✅ <b>GOLD V{V} | TP1 도달</b>\n"
            f"{d} · 현재가 {money(price)} · TP1 {money(tp1)}\n"
            f"보호 SL: {money(p['sl'])}\n"
            f"포지션 유지: TP2/TP3 또는 SL까지 신규 진입 차단.\n"
            f"🧪 모의 시그널 · {ts()}\n"
            f"<a href='{TV}'>TradingView GC1! 차트</a>",
            'TP1'
        )

    if tp2_hit:
        p['tp2_hit']=True
        if d=='LONG':
            candidate=min(float(r.low.min())-0.10*atr,price-0.10*atr)
            p['sl']=max(float(p['sl']),candidate)
        else:
            candidate=max(float(r.high.max())+0.10*atr,price+0.10*atr)
            p['sl']=min(float(p['sl']),candidate)

        queue(
            s,key('TP2',p),
            f"🎯 <b>GOLD V{V} | TP2 도달</b>\n"
            f"{d} · 현재가 {money(price)} · TP2 {money(tp2)}\n"
            f"보호 SL: {money(p['sl'])}\n"
            f"포지션 유지: TP3 또는 SL까지 신규 진입 차단.\n"
            f"🧪 모의 시그널 · {ts()}\n"
            f"<a href='{TV}'>TradingView GC1! 차트</a>",
            'TP2'
        )

    if tp3_hit:
        st=s.setdefault('stats',{
            'tp3':0,'stop_after_tp2':0,
            'stop_after_tp1':0,'stop_before_tp1':0
        })
        st['tp3']=int(st.get('tp3',0))+1
        queue(
            s,key('TP3',p),
            f"🏁 <b>GOLD V{V} | TP3 최종 종료</b>\n"
            f"{d} · 진입 {money(p['entry'])} · TP3 {money(tp3)}\n"
            f"체크 가격 {money(price)}\n"
            f"포지션 잠금 해제. 실제 주문/체결은 아닙니다.\n"
            f"🕒 {ts()}\n"
            f"<a href='{TV}'>TradingView GC1! 차트</a>",
            'TP3'
        )
        log('exit',reason='TP3',price=price,position=p)
        s['status']='IDLE'
        s['position']=None
        s['cooldown_until']=(now()+timedelta(minutes=10)).isoformat()
        queue_stats(s)
    else:
        s['position']=p
    write(SF,s)

def main():
    print(f'GOLD SMART SIGNAL BOT V{V} PAPER ONLY | {ts()}')
    s=valid_state(read(SF,blank()))

    # Telegram 설정이 빠진 경우 시그널만 생성하고 전송 실패하는 상황 방지
    if not TOKEN or not CHAT:
        raise RuntimeError('TELEGRAM_TOKEN / TELEGRAM_CHAT_ID Secrets 설정을 확인하세요.')

    frames=[
        bars('60d','1h'),
        bars('30d','15m'),
        bars('10d','5m')
    ]
    if min(map(len,frames))<60:
        log('data_error',lengths=list(map(len,frames)))
        raise RuntimeError('GC=F 데이터가 부족하거나 Yahoo Finance 요청에 실패했습니다.')

    h,m,n=[indic(x) for x in frames]
    c=last(n)
    if c is None:
        raise RuntimeError('지표 계산에 필요한 데이터가 부족합니다.')

    price=float(c.close)
    print(
        f'Price={price:.2f} RSI={c.rsi:.1f} '
        f'ATR={c.atr:.2f} ADX={c.adx:.1f}'
    )

    # 활성 포지션이 있다면 새 시그널을 절대 생성하지 않음
    if s['status']=='ACTIVE':
        print('ACTIVE position: new entry blocked')
        monitor(s,price,n)
        flush(s)
        return

    cd=s.get('cooldown_until')
    if cd:
        try:
            if now()<datetime.fromisoformat(cd):
                print('Cooldown until',cd)
                flush(s)
                return
        except Exception:
            s['cooldown_until']=None

    direction,info=signal(h,m,n)
    print('Signal:',direction,'scores:',info.get('scores'))

    if not direction:
        log('no_signal',price=price,info=info)
        write(SF,s)
        flush(s)
        return

    if info['bar']==s.get('last_signal_bar'):
        log('duplicate_bar_ignored',bar=info['bar'])
        flush(s)
        return

    atr=float(info['atr'])
    if atr<=0 or atr/price>0.012:
        log('signal_rejected_volatility',price=price,atr=atr)
        flush(s)
        return

    # 손절 폭은 ATR 기준, 목표가는 초기 위험 R의 배수
    risk=max(1.15*atr,1.0)
    if direction=='LONG':
        sl=price-risk
        tp1=price+1.2*risk
        tp2=price+2.0*risk
        tp3=price+3.0*risk
    else:
        sl=price+risk
        tp1=price-1.2*risk
        tp2=price-2.0*risk
        tp3=price-3.0*risk

    p={
        'id':f"{direction}-{now().strftime('%Y%m%d%H%M%S')}",
        'direction':direction,
        'entry':price,
        'sl':sl,
        'initial_sl':sl,
        'tp1':tp1,
        'tp2':tp2,
        'tp3':tp3,
        'risk':risk,
        'entry_atr':atr,
        'entry_time':ts(),
        'entry_bar':info['bar'],
        'tp1_hit':False,
        'tp2_hit':False,
        'score':info['scores'][direction],
        'entry_info':info
    }

    # 먼저 상태를 ACTIVE로 저장해 중복 진입을 막음
    s['status']='ACTIVE'
    s['position']=p
    s['last_signal_bar']=info['bar']
    queue(s,key('ENTRY',p),entry_msg(p,info),'ENTRY')
    write(SF,s)
    log('position_entry',position=p)
    flush(s)
    print('Entry saved; position lock engaged')

if __name__=='__main__':
    main()
