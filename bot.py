# GOLD FUTURES SMART SIGNAL BOT V20.1.3 (Optimized & No-Cooldown)
import os, json, time, requests, yfinance as yf, pandas as pd, numpy as np
from datetime import datetime, timezone, timedelta

V = "20.1.3"; T = "GC=F"; STATE = "signal_state.json"; LOG = "bot_log.json"
TOKEN = os.getenv("TELEGRAM_TOKEN", ""); CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
KST = timezone(timedelta(hours=9))

# ===== STRATEGY PARAMS (최적화) =====
ADX_TREND_MIN = 18
RSI_L = (45, 72); RSI_S = (28, 55)

# ===== RISK (골드 노이즈 회피형 넓은 손절/익절) =====
MIN_RISK = 1.5; MAX_RISK = 4.0
TP = (1.5, 2.5, 4.0); STRONG_TP = (2.0, 3.5, 5.5)
BT_PERIOD = "30d"

class NpEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer): return int(obj)
        if isinstance(obj, np.floating): return float(obj)
        if isinstance(obj, np.ndarray): return obj.tolist()
        if isinstance(obj, (np.bool_, bool)): return bool(obj)
        return super(NpEncoder, self).default(obj)

def now():
    return datetime.now(KST)

def ts():
    return now().strftime("%Y-%m-%d %H:%M:%S")

def m(x):
    try:
        val = float(x)
        if np.isnan(val) or np.isinf(val): return "$0.00"
        return f"${val:,.2f}"
    except:
        return "$0.00"

def save(p, x):
    q = p + ".tmp"
    with open(q, "w", encoding="utf8") as f:
        json.dump(x, f, ensure_ascii=False, indent=2, cls=NpEncoder)
    os.replace(q, p)

def load(p, d):
    try:
        with open(p, encoding="utf8") as f: return json.load(f)
    except: return d

def tg(x):
    if not TOKEN or not CHAT: 
        print("[TG CONFIG ERROR] Token or Chat ID is missing.")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id": CHAT, "text": x, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=15)
        print("[TELEGRAM]", "SENT" if r.ok else f"FAIL: {r.text}")
        return r.ok
    except Exception as e:
        print("[TG ERROR]", e)
        return False

def log(t, **x):
    a = load(LOG, [])
    if not isinstance(a, list): a = []
    a.append({"time": ts(), "type": t, **x})
    save(LOG, a[-500:])

def default():
    return {
        "version": V, "status": "IDLE", "direction": None,
        "entry": None, "sl": None, "tp1": None, "tp2": None, "tp3": None,
        "stage": "INITIAL", "signal_time": None,
        "exit_price": None, "exit_reason": None,
        "score": 0, "strength": "NORMAL", "regime": "UNKNOWN", "indicators": {}, "backtest": {}
    }

def get_state():
    s = load(STATE, default())
    d = default(); d.update(s); d["version"] = V
    save(STATE, d)
    return d

def clean(x):
    if x is None or x.empty: return pd.DataFrame()
    if isinstance(x.columns, pd.MultiIndex):
        x.columns = x.columns.get_level_values(0)
    x = x.copy()
    for c in ["Open", "High", "Low", "Close", "Volume"]:
        if c in x: x[c] = pd.to_numeric(x[c], errors="coerce")
    x = x.dropna(subset=["Open", "High", "Low", "Close"])
    if x.index.tz is None: x.index = x.index.tz_localize("UTC")
    return x

def getdata(tf, period):
    for i in range(3):
        try:
            x = clean(yf.download(T, period=period, interval=tf, progress=False, auto_adjust=False, threads=False))
            if len(x) > 30:
                return x.iloc[:-1]
        except Exception as e:
            print("[DATA ERROR]", tf, e)
        time.sleep(1)
    return pd.DataFrame()

def addind(x):
    x = x.copy()
    x["ema20"] = x.Close.ewm(span=20, adjust=False).mean()
    x["ema50"] = x.Close.ewm(span=50, adjust=False).mean()

    d = x.Close.diff()
    g = d.clip(lower=0).rolling(14).mean()
    l = (-d.clip(upper=0)).rolling(14).mean()
    rs = g / l.replace(0, np.nan)
    x["rsi"] = 100 - 100 / (1 + rs)

    tr = pd.concat([
        x.High - x.Low,
        (x.High - x.Close.shift()).abs(),
        (x.Low - x.Close.shift()).abs()], axis=1).max(axis=1)
    x["atr"] = tr.rolling(14).mean().fillna(x.Close * 0.01)

    up = x.High.diff()
    dn = -x.Low.diff()
    plus = up.where((up > dn) & (up > 0), 0)
    minus = dn.where((dn > up) & (dn > 0), 0)
    a = tr.rolling(14).mean()
    p = 100 * plus.rolling(14).mean() / a
    n = 100 * minus.rolling(14).mean() / a
    dx = 100 * (p - n).abs() / (p + n).replace(0, np.nan)
    x["adx"] = dx.rolling(14).mean().fillna(20)

    x["bb_mid"] = x.Close.rolling(20).mean()
    bb_std = x.Close.rolling(20).std().fillna(x.Close * 0.02)
    x["bb_upper"] = x["bb_mid"] + (bb_std * 2)
    x["bb_lower"] = x["bb_mid"] - (bb_std * 2)
    x["bb_width"] = (x["bb_upper"] - x["bb_lower"]) / x["bb_mid"].replace(0, np.nan)
    x["bb_width_ma"] = x["bb_width"].rolling(20).mean().fillna(0.02)
    return x.fillna(0)

def detect_regime(x, i=-1):
    r = x.iloc[i]
    adx = float(getattr(r, "adx", 20))
    bb_w = float(getattr(r, "bb_width", 0.02))
    bb_w_ma = float(getattr(r, "bb_width_ma", 0.02))

    if adx >= ADX_TREND_MIN:
        return "TREND"
    elif bb_w < bb_w_ma * 0.8:
        return "BREAKOUT_CANDIDATE"
    else:
        return "RANGE"

def evaluate_multi_strategy(x15, x5, d, i=-1):
    regime = detect_regime(x15, i)
    r = x15.iloc[i]; p = x15.iloc[i-1]; s = 0.0

    if d == "LONG":
        s += float(r.Close > r.ema20) * 2.0
        s += float(r.ema20 > r.ema50) * 2.0
        s += float(r.Close > p.High) * 1.5
        s += float(RSI_L[0] <= r.rsi <= RSI_L[1]) * 1.5
    else:
        s += float(r.Close < r.ema20) * 2.0
        s += float(r.ema20 < r.ema50) * 2.0
        s += float(r.Close < p.Low) * 1.5
        s += float(RSI_S[0] <= r.rsi <= RSI_S[1]) * 1.5

    score_5m = 0.0
    q = x5.loc[:x15.index[i]]
    if len(q) >= 5:
        r5 = q.iloc[-1]
        if d == "LONG": 
            score_5m += float(r5.Close > r5.ema20) + float(r5.rsi >= 45)
        else: 
            score_5m += float(r5.Close < r5.ema20) + float(r5.rsi <= 55)

    ind_info = {
        "rsi": float(r.rsi), "adx": float(r.adx), "atr": float(r.atr),
        "ema20": float(r.ema20), "ema50": float(r.ema50), "score_5m": float(score_5m)
    }
    return float(s + score_5m), regime, ind_info

def levels(x, d, i):
    r = x.iloc[i]; e = float(r.Close)
    a = float(r.atr) if not pd.isna(r.atr) and r.atr > 0 else e * 0.015
    sw = x.iloc[max(0, i-10):i+1]

    if d == "LONG":
        low_min = float(sw.Low.min()) if not pd.isna(sw.Low.min()) else e - (a * 2)
        sl0 = low_min - a * 0.3
        risk = max(e - sl0, a * MIN_RISK)
        risk = min(risk, a * MAX_RISK)
        sl = e - risk
    else:
        high_max = float(sw.High.max()) if not pd.isna(sw.High.max()) else e + (a * 2)
        sl0 = high_max + a * 0.3
        risk = max(sl0 - e, a * MIN_RISK)
        risk = min(risk, a * MAX_RISK)
        sl = e + risk

    return float(e), float(sl), float(risk)

def targets(e, risk, strong, d):
    z = STRONG_TP if strong else TP
    if d == "LONG": return [float(e + risk * q) for q in z]
    return [float(e - risk * q) for q in z]

def live():
    try:
        f = yf.Ticker(T).fast_info
        for k in ["last_price", "regularMarketPrice"]:
            if f.get(k): return float(f[k]), 0, "FAST"
    except: pass
    try:
        x = clean(yf.download(T, period="1d", interval="1m", progress=False, auto_adjust=False, threads=False))
        if not x.empty:
            t = x.index[-1]
            if t.tzinfo is None: t = t.tz_localize("UTC")
            return float(x.Close.iloc[-1]), (datetime.now(timezone.utc) - t).total_seconds() / 60, "1M"
    except: pass
    return None, 9999, "NONE"

def entry_alert(s):
    d = s["direction"]
    icon = "🟢 롱 포지션 (매수)" if d == "LONG" else "🔴 숏 포지션 (매도)"
    e = s['entry']; t1, t2, t3, sl = s['tp1'], s['tp2'], s['tp3'], s['sl']
    r1 = (t1/e-1)*100 if d=="LONG" else (e/t1-1)*100
    r2 = (t2/e-1)*100 if d=="LONG" else (e/t2-1)*100
    r3 = (t3/e-1)*100 if d=="LONG" else (e/t3-1)*100
    rs_loss = (sl/e-1)*100 if d=="LONG" else (e/sl-1)*100

    ind = s.get("indicators", {})
    tv_chart_link = "https://www.tradingview.com/chart/?symbol=COMEX%3AGC1!"

    return f"""⚡ <b>[골드 선물] 스마트 퀀트 시그널 V20.1.3</b>
━━━━━━━━━━━━━━━━━━━━━━━
🎯 <b>진입 방향</b> : <b>{icon}</b>
💎 <b>셋업 등급</b> : <b>{s['strength']}</b> (총점: <b>{s['score']}</b>)
📊 <b>시장 국면</b> : <code>{s.get('regime')}</code>
━━━━━━━━━━━━━━━━━━━━━━━
💰 <b>진입 가격</b> : <code>{m(e)}</code>

⚙️ <b>[ 기술적 분석 지표 (15M / 5M) ]</b>
 • <b>RSI (14)</b> : <code>{ind.get('rsi', 0):.1f}</code>
 • <b>ADX (14)</b> : <code>{ind.get('adx', 0):.1f}</code>
 • <b>ATR (14)</b> : <code>{m(ind.get('atr', 0))}</code>
 • <b>5분봉 시너지</b> : <code>{ind.get('score_5m', 0)} 점</code>

🎯 <b>목표가 래더 (TP)</b>
 ├ <b>TP1</b> : <code>{m(t1)}</code> ({r1:+.2f}%)
 ├ <b>TP2</b> : <code>{m(t2)}</code> ({r2:+.2f}%)
 └ <b>TP3</b> : <code>{m(t3)}</code> ({r3:+.2f}%)

🛡 <b>손절가 (SL)</b> : <code>{m(sl)}</code> ({rs_loss:+.2f}%)
━━━━━━━━━━━━━━━━━━━━━━━
📈 <a href="{tv_chart_link}"><b>[트레이딩뷰 골드 선물 실시간 차트 열기]</b></a>
⏱ <code>{now().strftime('%Y-%m-%d %H:%M:%S KST')}</code>"""

def monitor(s):
    p, a, src = live()
    if p is None: return

    d = s["direction"]; sl = s["sl"]; st = s["stage"]
    tv_chart_link = "https://www.tradingview.com/chart/?symbol=COMEX%3AGC1!"

    if d == "LONG":
        if p <= sl:
            close(s, p, "SL")
            tg(f"🛡 <b>골드 선물 · 손절가 도달</b>\n━━━━━━━━━━━━━━━━━━━━━━━\n📊 포지션: <b>LONG</b>\n💰 청산가: <code>{m(p)}</code>\n📈 <a href=\"{tv_chart_link}\">차트 확인</a>")
            return
        if p >= s["tp3"]:
            close(s, p, "TP3_WIN")
            tg(f"🏆 <b>골드 선물 · 3차 목표가 도달 (완료)</b>\n━━━━━━━━━━━━━━━━━━━━━━━\n📊 포지션: <b>LONG</b>\n💰 최종 청산가: <code>{m(p)}</code>\n📈 <a href=\"{tv_chart_link}\">차트 확인</a>")
            return
        if p >= s["tp2"] and st in ["INITIAL", "TP1_TRAIL"]:
            if st == "INITIAL":
                s["stage"] = "TP1_TRAIL"; s["sl"] = s["entry"]; save(STATE, s)
                tg(f"🎯 <b>골드 선물 · 1차 목표가 도달</b>\n🛡 <b>본전(Entry)으로 손절가 상향 조정됨</b>\n📈 <a href=\"{tv_chart_link}\">차트 확인</a>")
                time.sleep(1)
            s["stage"] = "TP2_TRAIL"; s["sl"] = s["tp1"]; save(STATE, s)
            tg(f"🎯 <b>골드 선물 · 2차 목표가 도달</b>\n🛡 <b>손절가가 TP1으로 상향 조정됨</b>\n📈 <a href=\"{tv_chart_link}\">차트 확인</a>")
            return
        if p >= s["tp1"] and st == "INITIAL":
            s["stage"] = "TP1_TRAIL"; s["sl"] = s["entry"]; save(STATE, s)
            tg(f"🎯 <b>골드 선물 · 1차 목표가 도달</b>\n🛡 <b>본전(Entry)으로 손절가 상향 조정됨</b>\n📈 <a href=\"{tv_chart_link}\">차트 확인</a>")
            return
    else: # SHORT
        if p >= sl:
            close(s, p, "SL")
            tg(f"🛡 <b>골드 선물 · 손절가 도달</b>\n━━━━━━━━━━━━━━━━━━━━━━━\n📊 포지션: <b>SHORT</b>\n💰 청산가: <code>{m(p)}</code>\n📈 <a href=\"{tv_chart_link}\">차트 확인</a>")
            return
        if p <= s["tp3"]:
            close(s, p, "TP3_WIN")
            tg(f"🏆 <b>골드 선물 · 3차 목표가 도달 (완료)</b>\n━━━━━━━━━━━━━━━━━━━━━━━\n📊 포지션: <b>SHORT</b>\n💰 최종 청산가: <code>{m(p)}</code>\n📈 <a href=\"{tv_chart_link}\">차트 확인</a>")
            return
        if p <= s["tp2"] and st in ["INITIAL", "TP1_TRAIL"]:
            if st == "INITIAL":
                s["stage"] = "TP1_TRAIL"; s["sl"] = s["entry"]; save(STATE, s)
                tg(f"🎯 <b>골드 선물 · 1차 목표가 도달</b>\n🛡 <b>본전(Entry)으로 손절가 상향 조정됨</b>\n📈 <a href=\"{tv_chart_link}\">차트 확인</a>")
                time.sleep(1)
            s["stage"] = "TP2_TRAIL"; s["sl"] = s["tp1"]; save(STATE, s)
            tg(f"🎯 <b>골드 선물 · 2차 목표가 도달</b>\n🛡 <b>손절가가 TP1으로 상향 조정됨</b>\n📈 <a href=\"{tv_chart_link}\">차트 확인</a>")
            return
        if p <= s["tp1"] and st == "INITIAL":
            s["stage"] = "TP1_TRAIL"; s["sl"] = s["entry"]; save(STATE, s)
            tg(f"🎯 <b>골드 선물 · 1차 목표가 도달</b>\n🛡 <b>본전(Entry)으로 손절가 상향 조정됨</b>\n📈 <a href=\"{tv_chart_link}\">차트 확인</a>")
            return

def close(s, p, reason):
    s.update({"status": "IDLE", "direction": None, "entry": None, "sl": None, "stage": "INITIAL", "exit_price": float(p), "exit_reason": reason})
    save(STATE, s)
    log("EXIT", price=float(p), reason=reason)

def main():
    print("====================================")
    print(f" GOLD FUTURES SMART SIGNAL BOT V{V}")
    print("====================================")

    s = get_state()

    # 활성 포지션이 있으면 모니터링 수행
    if s.get("status") == "ACTIVE" and s.get("direction"):
        print("[MONITOR] Active position found. Checking live price...")
        monitor(s)

    print("[SCAN] Searching for new trading setups...")
    x15 = addind(getdata("15m", BT_PERIOD))
    x5 = addind(getdata("5m", BT_PERIOD))

    if x15.empty or x5.empty:
        print("[DATA ERROR] Failed to fetch market data from yfinance.")
        return

    best_d = None; best_score = 0.0; best_regime = "UNKNOWN"; best_ind = {}
    for d in ["LONG", "SHORT"]:
        score, regime, ind = evaluate_multi_strategy(x15, x5, d, -1)
        if score > best_score:
            best_score = float(score); best_d = d; best_regime = regime; best_ind = ind

    # 점수 기준 완화 (2.0점 이상이면 포착)
    if not best_d or best_score < 2.0:
        print("[SIGNAL] No qualified setup right now.")
        return

    p, a, src = live()
    if p is None: return

    e, sl, risk = levels(x15, best_d, -1)
    strong = best_score >= 5.0
    t1, t2, t3 = targets(e, risk, strong, best_d)

    # 쿨다운 없이 언제든 새로운 고품질 세팅이 잡히면 덮어쓰고 알림 발송 가능하도록 수정
    sig = {
        "status": "ACTIVE", "direction": best_d,
        "entry": float(e), "sl": float(sl), "tp1": float(t1), "tp2": float(t2), "tp3": float(t3),
        "stage": "INITIAL", "score": float(best_score),
        "strength": "STRONG" if strong else "NORMAL",
        "regime": best_regime, "indicators": best_ind, "signal_time": ts()
    }

    s.update(sig)
    save(STATE, s)
    tg(entry_alert(s))
    print("[ENTRY] New position created and notified!")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[FATAL]", repr(e))
        raise
