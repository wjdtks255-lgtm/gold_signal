import os,json,time,tempfile,math
from pathlib import Path
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import requests,numpy as np,pandas as pd,yfinance as yf
V='15.0.0'; TZ=ZoneInfo('Asia/Seoul'); SYMBOL='GC=F'; TV='https://www.tradingview.com/symbols/GC1!/'
SF=Path('signal_state.json'); LF=Path('bot_log.json'); TOKEN=os.getenv('TELEGRAM_TOKEN','').strip(); CHAT=(os.getenv('TELEGRAM_CHAT_ID') or os.getenv('CHAT_ID') or '').strip()
def now(): return datetime.now(TZ)
def ts(): return now().isoformat(timespec='seconds')
def safe(x):
 if isinstance(x,dict): return {str(k):safe(v) for k,v in x.items()}
 if isinstance(x,(list,tuple)): return [safe(v) for v in x]
 if isinstance(x,(np.integer,)): return int(x)
 if isinstance(x,(np.floating,)): return None if not np.isfinite(x) else float(x)
 if isinstance(x,(np.bool_,)): return bool(x)
 if isinstance(x,(pd.Timestamp,datetime)): return x.isoformat()
 if isinstance(x,float) and not math.isfinite(x): return None
 return x
def write(path,obj):
 path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); fd,tmp=tempfile.mkstemp(dir=path.parent,prefix=path.name+'.',suffix='.tmp')
 try:
  with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump(safe(obj),f,ensure_ascii=False,indent=2,allow_nan=False); f.flush(); os.fsync(f.fileno())
  os.replace(tmp,path)
 finally:
  if os.path.exists(tmp): os.remove(tmp)
def read(path,default):
 if not Path(path).exists(): return default
 try:
  with open(path,encoding='utf-8') as f: return json.load(f)
 except Exception as e:
  backup=Path(str(path)+'.corrupt-'+now().strftime('%Y%m%d-%H%M%S'))
  try: Path(path).replace(backup)
  except Exception: pass
  if str(path)==str(SF): raise RuntimeError(f'{path} 손상. 백업 {backup} 생성; 상태 복구 전 신규 진입 중단') from e
  print('Log file error:',e); return default
def log(event,**kw):
 a=read(LF,[])
 if not isinstance(a,list): a=[]
 a.append({'time':ts(),'event':event,**safe(kw)}); write(LF,a[-500:])
def send(msg):
 if not TOKEN or not CHAT: print('Telegram 설정 누락: TELEGRAM_TOKEN / TELEGRAM_CHAT_ID'); return False
 for i in range(3):
  try:
   r=requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage',json={'chat_id':CHAT,'text':msg,'parse_mode':'HTML','disable_web_page_preview':True},timeout=15)
   if r.ok and r.json().get('ok'): return True
   print('Telegram error',r.status_code,r.text[:200])
   if 400<=r.status_code<500: return False
  except Exception as e: print('Telegram attempt',i+1,e)
  time.sleep(1+i)
 return False
def blank(): return {'version':V,'status':'IDLE','position':None,'last_signal_bar':None,'cooldown_until':None,'pending_alerts':[],'sent_event_keys':[]}
def valid_state(s):
 if not isinstance(s,dict): raise RuntimeError('signal_state.json must be a JSON object')
 for k,v in blank().items(): s.setdefault(k,v)
 if s['status'] not in ('IDLE','ACTIVE'): raise RuntimeError('Invalid state status')
 if s['status']=='ACTIVE' and not isinstance(s.get('position'),dict): raise RuntimeError('ACTIVE position missing; refusing new entries')
 return s
def queue(s,key,msg,event):
 if key in s['sent_event_keys']: return
 s['sent_event_keys']=(s['sent_event_keys']+[key])[-500:]; s['pending_alerts'].append({'key':key,'message':msg,'event':event}); s['pending_alerts']=s['pending_alerts'][-50:]
 write(SF,s); log('alert_queued',key=key,event=event)
def flush(s):
 left=[]
 for a in s.get('pending_alerts',[]):
  ok=send(a['message']); log('telegram_sent' if ok else 'telegram_failed',key=a['key'],event=a['event'])
  if not ok: left.append(a)
 s['pending_alerts']=left; write(SF,s)
def bars(period,interval):
 d=yf.Ticker(SYMBOL).history(period=period,interval=interval,auto_adjust=False,actions=False)
 if d is None or d.empty: return pd.DataFrame()
 d=d.rename(columns={c:c.lower() for c in d.columns}); d=d[~d.index.duplicated(keep='last')].sort_index()
 if len(d)>2: d=d.iloc[:-1].copy() # ignore possibly incomplete last candle
 return d.dropna(subset=['open','high','low','close'])
def indic(d):
 if d.empty:return d
 d=d.copy(); c,h,l=d.close,d.high,d.low; d['ema20']=c.ewm(span=20,adjust=False).mean(); d['ema50']=c.ewm(span=50,adjust=False).mean()
 delta=c.diff(); gain=delta.clip(lower=0).ewm(alpha=1/14,adjust=False,min_periods=14).mean(); loss=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False,min_periods=14).mean(); d['rsi']=100-100/(1+gain/loss.replace(0,np.nan))
 prev=c.shift(); tr=pd.concat([h-l,(h-prev).abs(),(l-prev).abs()],axis=1).max(axis=1); d['atr']=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
 up,down=h.diff(),-l.diff(); plus=up.where((up>down)&(up>0),0.); minus=down.where((down>up)&(down>0),0.); a=d.atr.replace(0,np.nan)
 pdi=100*plus.ewm(alpha=1/14,adjust=False,min_periods=14).mean()/a; mdi=100*minus.ewm(alpha=1/14,adjust=False,min_periods=14).mean()/a
 dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan); d['adx']=dx.ewm(alpha=1/14,adjust=False,min_periods=14).mean(); d['pdi']=pdi; d['mdi']=mdi
 spread=(h-l).replace(0,np.nan); d['body']=(c-d.open).abs()/spread; d['cpos']=(c-l)/spread; return d
def last(d):
 if d.empty:return None
 r=d.iloc[-1]; keys=['close','ema20','ema50','rsi','atr','adx','pdi','mdi','body','cpos']
 return None if any(k not in r or pd.isna(r[k]) for k in keys) else r
def signal(h,m15,m5):
 a,b,c=last(h),last(m15),last(m5)
 if any(x is None for x in (a,b,c)): return None,{'reason':'indicator_warmup'}
 q={'h1L':a.close>a.ema20>a.ema50,'h1S':a.close<a.ema20<a.ema50,'m15L':b.close>b.ema20>b.ema50,'m15S':b.close<b.ema20<b.ema50,
 'm5L':c.close>c.ema20 and c.rsi>=50 and c.pdi>c.mdi,'m5S':c.close<c.ema20 and c.rsi<=50 and c.mdi>c.pdi,
 'candleL':c.cpos>=.60 and c.body>=.25,'candleS':c.cpos<=.40 and c.body>=.25,'adx':c.adx>=14 and b.adx>=12,
 'rsiL':48<=c.rsi<=72,'rsiS':28<=c.rsi<=52}
 scores={'LONG':2*q['h1L']+2*q['m15L']+2*q['m5L']+q['candleL']+q['adx']+q['rsiL'],'SHORT':2*q['h1S']+2*q['m15S']+2*q['m5S']+q['candleS']+q['adx']+q['rsiS']}
 direction='LONG' if q['h1L'] and q['m15L'] and scores['LONG']>=7 else 'SHORT' if q['h1S'] and q['m15S'] and scores['SHORT']>=7 else None
 return direction,{'scores':scores,'checks':q,'price':float(c.close),'atr':float(c.atr),'rsi':float(c.rsi),'adx':float(c.adx),'bar':str(m5.index[-1])}
def money(x): return f'${float(x):,.2f}'
def key(kind,p): return f"{kind}:{p['id']}"
def entry_msg(p,info):
 return (f"🏆 <b>GOLD SMART SIGNAL V15 | PAPER</b>\n{'🟢' if p['direction']=='LONG' else '🔴'} <b>{p['direction']} 진입 신호</b>\n━━━━━━━━━━━━\n📍 기준가 <b>{money(p['entry'])}</b>\n🛑 SL {money(p['sl'])}\n🎯 TP1 {money(p['tp1'])}\n🎯 TP2 {money(p['tp2'])}\n🏁 TP3 {money(p['tp3'])}\n━━━━━━━━━━━━\n점수 LONG {info['scores']['LONG']}/9 · SHORT {info['scores']['SHORT']}/9\nRSI {info['rsi']:.1f} | ADX {info['adx']:.1f} | ATR {info['atr']:.2f}\n근거: H1·M15 추세 정렬 + M5 모멘텀 확인\n🔒 TP3 또는 SL까지 신규 진입 차단\n🧪 모의 신호(실제 주문 아님) · {ts()}\n<a href='{TV}'>TradingView GC1! 차트</a>")
def monitor(s,price,m5):
 p=s['position']; d=p['direction']; sl,tp1,tp2,tp3=[float(p[k]) for k in ('sl','tp1','tp2','tp3')]
 hits=[]
 if d=='LONG':
  if price<=sl:hits.append('SL')
  if price>=tp1 and not p['tp1_hit']:hits.append('TP1')
  if price>=tp2 and not p['tp2_hit']:hits.append('TP2')
  if price>=tp3:hits.append('TP3')
 else:
  if price>=sl:hits.append('SL')
  if price<=tp1 and not p['tp1_hit']:hits.append('TP1')
  if price<=tp2 and not p['tp2_hit']:hits.append('TP2')
  if price<=tp3:hits.append('TP3')
 # If a sampled close is beyond SL and target simultaneously, assume SL conservatively.
 if 'SL' in hits:
  msg=f"🛑 <b>GOLD V15 | 모의 손절 종료</b>\n{d} · 기준가 {money(p['entry'])}\n체크 가격 {money(price)} · SL {money(sl)}\n5분 체크 간격으로 실제 도달 순서를 알 수 없어 보수적으로 처리했습니다.\n🧪 모의 신호 · {ts()}\n<a href='{TV}'>TradingView GC1! 차트</a>"
  queue(s,key('SL',p),msg,'SL'); log('exit',reason='SL',price=price,position=p); s['status']='IDLE'; s['position']=None; s['cooldown_until']=(now()+timedelta(minutes=30)).isoformat(); write(SF,s); return
 for target in ('TP1','TP2'):
  if target not in hits: continue
  r=m5.iloc[-7:]; atr=float(r.atr.iloc[-1])
  if target=='TP1':
   p['tp1_hit']=True
   if d=='LONG': p['sl']=max(sl,min(max(p['entry']+.05*atr,float(r.low.min())-.15*atr),price-.15*atr))
   else: p['sl']=min(sl,max(min(p['entry']-.05*atr,float(r.high.max())+.15*atr),price+.15*atr))
   text=f"✅ <b>GOLD V15 | TP1 도달</b>\n{d} · 현재가 {money(price)} · TP1 {money(tp1)}\n구조·ATR 보호 손절선 {money(p['sl'])}\n포지션 유지: TP2/TP3 또는 SL까지 신규 진입 차단.\n🧪 모의 신호 · {ts()}\n<a href='{TV}'>TradingView GC1! 차트</a>"
  else:
   p['tp2_hit']=True
   if d=='LONG': p['sl']=max(float(p['sl']),min(float(r.low.min())-.10*atr,price-.10*atr))
   else: p['sl']=min(float(p['sl']),max(float(r.high.max())+.10*atr,price+.10*atr))
   text=f"🎯 <b>GOLD V15 | TP2 도달</b>\n{d} · 현재가 {money(price)} · TP2 {money(tp2)}\n보호 손절선 {money(p['sl'])}\n포지션 유지: TP3 또는 SL까지 신규 진입 차단.\n🧪 모의 신호 · {ts()}\n<a href='{TV}'>TradingView GC1! 차트</a>"
  queue(s,key(target,p),text,target)
 if 'TP3' in hits:
  text=f"🏁 <b>GOLD V15 | TP3 모의 종료</b>\n{d} · 진입 {money(p['entry'])} · TP3 {money(tp3)}\n체크 가격 {money(price)}\n포지션 잠금 해제. 실제 체결 아님.\n🕒 {ts()}\n<a href='{TV}'>TradingView GC1! 차트</a>"
  queue(s,key('TP3',p),text,'TP3'); log('exit',reason='TP3',price=price,position=p); s['status']='IDLE'; s['position']=None; s['cooldown_until']=(now()+timedelta(minutes=10)).isoformat()
 else:s['position']=p
 write(SF,s)
def main():
 print(f'GOLD SMART SIGNAL BOT V{V} PAPER ONLY | {ts()}')
 s=valid_state(read(SF,blank()))
 h,m,n=[bars(*x) for x in [('60d','1h'),('30d','15m'),('10d','5m')]]
 if min(len(h),len(m),len(n))<60: log('data_error',h=len(h),m15=len(m),m5=len(n)); raise RuntimeError('Insufficient GC=F data')
 h,m,n=indic(h),indic(m),indic(n); c=last(n)
 if c is None: raise RuntimeError('Indicators not ready')
 price=float(c.close); print(f'Price={price:.2f} RSI={c.rsi:.1f} ATR={c.atr:.2f} ADX={c.adx:.1f}')
 if s['status']=='ACTIVE': print('Active position: entry blocked'); monitor(s,price,n); flush(s); return
 cd=s.get('cooldown_until')
 if cd:
  try:
   if now()<datetime.fromisoformat(cd): print('Cooldown until',cd); flush(s); return
  except Exception:s['cooldown_until']=None
 direction,info=signal(h,m,n); print('Signal:',direction,'scores:',info.get('scores'))
 if not direction: log('no_signal',price=price,info=info); write(SF,s); flush(s); return
 if info['bar']==s.get('last_signal_bar'): log('duplicate_bar_ignored',bar=info['bar']); flush(s); return
 atr=info['atr']
 if atr<=0 or atr/price>.012: log('signal_rejected_volatility',price=price,atr=atr); flush(s); return
 risk=max(1.15*atr,1.0); sl,tp1,tp2,tp3=(price-risk,price+1.2*risk,price+2*risk,price+3*risk) if direction=='LONG' else (price+risk,price-1.2*risk,price-2*risk,price-3*risk)
 p={'id':f"{direction}-{now().strftime('%Y%m%d%H%M%S')}",'direction':direction,'entry':price,'sl':sl,'initial_sl':sl,'tp1':tp1,'tp2':tp2,'tp3':tp3,'risk':risk,'entry_time':ts(),'entry_bar':info['bar'],'tp1_hit':False,'tp2_hit':False,'score':info['scores'][direction],'entry_info':info}
 s['status']='ACTIVE'; s['position']=p; s['last_signal_bar']=info['bar']; queue(s,key('ENTRY',p),entry_msg(p,info),'ENTRY'); write(SF,s); log('position_entry',position=p); flush(s); print('Entry state saved; position lock engaged')
if __name__=='__main__': main()
