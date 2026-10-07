# GOLD FUTURES SMART SIGNAL BOT V20.0.0 (Adaptive Strategy Engine)
import os, json, time, requests, yfinance as yf, pandas as pd, numpy as np
from datetime import datetime, timezone, timedelta

V = "20.0.0"; T = "GC=F"; STATE = "signal_state.json"; LOG = "bot_log.json"
TOKEN = os.getenv("TELEGRAM_TOKEN", ""); CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
KST = timezone(timedelta(hours=9))

# ===== ADAPTIVE STRATEGY PARAMS =====
ADX_TREND_MIN = 20
RSI_L = (48, 70); RSI_S = (30, 52); BODY = 0.30

# ===== RISK =====
MIN_RISK = 1.0; MAX_RISK = 3.0
TP = (1.2, 2.0, 3.0); STRONG_TP = (1.5, 2.5, 4.0)

# ===== BACKTEST (현실적인 통과 기준으로 조정) =====
BT_PERIOD = "30d"
BT_MIN_TRADES = 5
BT_MIN_WINRATE = 18.0
BT_MIN_PF = 0.75
BT_MAX_DD = 45.0

# ===== LIVE =====
PRICE_MAX_AGE = 300

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
        json.dump(x, f, ensure_ascii=False, indent=2)
    os.replace(q, p)

def load(p, d):
    try:
        with open(p, encoding="utf8") as f: return json.load(f)
    except: return d

def tg(x):
    if not TOKEN or not CHAT: return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id": CHAT, "text": x, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=15)
        print("[TELEGRAM]", "SENT" if r.ok else "FAIL")
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
        "score": 0, "strength": "NORMAL", "regime": "UNKNOWN", "backtest": {}
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

    x["body"] = (x.Close - x.Open).abs() / (x.High - x.Low).replace(0, np.nan)
    x["pos"] = (x.Close - x.Low) / (x.High - x.Low).replace(0, np.nan)
    return x.fillna(0)

def detect_regime(x, i=-1):
    r = x.iloc[i]
    adx = float(getattr(r, "adx", 20))
    bb_w = float(getattr(r, "bb_width", 0.02))
    bb_w_ma = float(getattr(r, "bb_width_ma", 0.02))

    if adx >= ADX_TREND_MIN and bb_w >= bb_w_ma * 0.9:
        return "TREND"
    elif bb_w < bb_w_ma * 0.8:
        return "BREAKOUT_CANDIDATE"
    else:
        return "RANGE"

def evaluate_multi_strategy(x15, x5, d, i=-1):
    regime = detect_regime(x15, i)
    r = x15.iloc[i]; p = x15.iloc[i-1]; s = 0

    if d == "LONG":
        s += (r.Close > r.ema20) * 2
        s += (r.ema20 > r.ema50) * 2
        s += (r.Close > p.High)
        s += (RSI_L[0] <= r.rsi <= RSI_L[1])
    else:
        s += (r.Close < r.ema20) * 2
        s += (r.ema20 < r.ema50) * 2
        s += (r.Close < p.Low)
        s += (RSI_S[0] <= r.rsi <= RSI_S[1])

    score_5m = 0
    q = x5.loc[:x15.index[i]]
    if len(q) >= 5:
        r5 = q.iloc[-1]
        if d == "LONG": score_5m += (r5.Close > r5.ema20) + (r5.rsi >= 48)
        else: score_5m += (r5.Close < r5.ema20) + (r5.rsi <= 52)

    return round(s + score_5m, 1), regime

def levels(x, d, i):
    r = x.iloc[i]; e = float(r.Close)
    a = float(r.atr) if not pd.isna(r.atr) and r.atr > 0 else e * 0.01
    sw = x.iloc[max(0, i-7):i+1]

    if d == "LONG":
        low_min = float(sw.Low.min()) if not pd.isna(sw.Low.min()) else e - (a * 2)
        sl0 = low_min - a * 0.2
        risk = max(e - sl0, a * MIN_RISK)
        risk = min(risk, a * MAX_RISK)
        sl = e - risk
    else:
        high_max = float(sw.High.max()) if not pd.isna(sw.High.max()) else e + (a * 2)
        sl0 = high_max + a * 0.2
        risk = max(sl0 - e, a * MIN_RISK)
        risk = min(risk, a * MAX_RISK)
        sl = e + risk

    return float(e), float(sl), float(risk)

def targets(e, risk, strong, d):
    z = STRONG_TP if strong else TP
    if d == "LONG": return [float(e + risk * q) for q in z]
    return [float(e - risk * q) for q in z]

def backtest(x15, x5):
    if len(x15) < 50 or len(x5) < 50:
        return {"pass": False, "reason": "INSUFFICIENT_DATA"}

    trades = []
    wins = losses = gross_win = gross_loss = 0
    last_exit = -99

    for i in range(30, len(x15) - 10):
        if i - last_exit < 2: continue
        best_d = None; best_score = 0; best_regime = "UNKNOWN"

        for d in ["LONG", "SHORT"]:
            score, regime = evaluate_multi_strategy(x15, x5, d, i)
            if score > best_score:
                best_score = score; best_d = d; best_regime = regime

        if not best_d or best_score < 3.0: continue

        e, sl, risk = levels(x15, best_d, i)
        t1, t2, t3 = targets(e, risk, best_score >= 6.0, best_d)

        result = None; exit_price = None
        for k in range(i + 1, min(i + 40, len(x15))):
            c = x15.iloc[k]
            hi = float(c.High); lo = float(c.Low)

            if best_d == "LONG":
                if lo <= sl: result = "LOSS"; exit_price = sl; break
                if hi >= t2: result = "WIN"; exit_price = t2; break
            else:
                if hi >= sl: result = "LOSS"; exit_price = sl; break
                if lo <= t2: result = "WIN"; exit_price = t2; break

        if result is None:
            c_last = x15.iloc[min(i + 39, len(x15) - 1)]
            exit_price = float(c_last.Close)
            result = "WIN" if (best_d == "LONG" and exit_price > e) or (best_d == "SHORT" and exit_price < e) else "LOSS"

        ret = (exit_price - e) / risk if best_d == "LONG" else (e - exit_price) / risk
        trades.append(ret)
        last_exit = k
        if ret > 0: wins += 1; gross_win += ret
        elif ret < 0: losses += 1; gross_loss += abs(ret)

    n = len(trades)
    if not n: return {"pass": False, "reason": "NO_TRADES", "trades": 0}

    wr = wins / n * 100
    pf = gross_win / gross_loss if gross_loss else 99
    return {
        "trades": n, "wins": wins, "losses": losses,
        "winrate": round(wr, 1), "profit_factor": round(pf, 2),
        "pass": (n >= BT_MIN_TRADES and wr >= BT_MIN_WINRATE and pf >= BT_MIN_PF)
    }

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

    return f"""⚡ <b>[골드 선물] 신규 스마트 퀀트 시그널 V20</b>
━━━━━━━━━━━━━━━━━━━━━━━
🎯 <b>진입 방향</b> : <b>{icon}</b>
💎 <b>셋업 등급</b> : <b>{s['strength']}</b> (점수: <b>{s['score']}</b>)
━━━━━━━━━━━━━━━━━━━━━━━
📊 <b>시장 국면</b> : <code>{s.get('regime')}</code>
 • 진입 가격　 : <code>{m(e)}</code>

🎯 <b>목표가 래더 (TP)</b>
 ├ <b>TP1</b> : <code>{m(t1)}</code> ({r1:+.2f}%)
 ├ <b>TP2</b> : <code>{m(t2)}</code> ({r2:+.2f}%)
 └ <b>TP3</b> : <code>{m(t3)}</code> ({r3:+.2f}%)

🛡 <b>손절가 (SL)</b> : <code>{m(sl)}</code> ({rs_loss:+.2f}%)
⏱ <code>{now().strftime('%H:%M:%S KST')}</code>"""

def monitor(s):
    p, a, src = live()
    if p is None: return

    d = s["direction"]; sl = s["sl"]; st = s["stage"]
    if d == "LONG":
        if p <= sl:
            close(s, p, "SL")
            tg(f"🛡 <b>골드 선물 · 손절(SL) 도달</b>\n━━━━━━━━━━━━━━━━━━━━━━━\n📊 포지션: <b>LONG</b>\n💰 청산가: <code>{m(p)}</code>")
            return
        if st == "INITIAL" and p >= s["tp1"]:
            s["stage"] = "TP1_TRAIL"; s["sl"] = s["entry"]; save(STATE, s)
            tg(f"🎯 <b>골드 선물 · TP1 도달 성공!</b>\n━━━━━━━━━━━━━━━━━━━━━━━\n📊 포지션: <b>LONG</b>\n💰 진입가: <code>{m(s['entry'])}</code>\n🎯 TP1 목표가: <code>{m(s['tp1'])}</code>\n🛡 <b>본전(Entry)으로 손절가(SL) 상향 조정됨</b>")
            return
        if st == "TP1_TRAIL" and p >= s["tp2"]:
            s["stage"] = "TP2_TRAIL"; s["sl"] = s["tp1"]; save(STATE, s)
            tg(f"🎯 <b>골드 선물 · TP2 도달 성공!</b>\n━━━━━━━━━━━━━━━━━━━━━━━\n📊 포지션: <b>LONG</b>\n🎯 TP2 목표가: <code>{m(s['tp2'])}</code>\n🛡 <b>손절가(SL)가 TP1으로 상향 조정됨</b>")
            return
        if st == "TP2_TRAIL" and p >= s["tp3"]:
            close(s, p, "TP3_WIN")
            tg(f"🏆 <b>골드 선물 · TP3 최종 목표가 도달 성공! (WIN)</b>\n━━━━━━━━━━━━━━━━━━━━━━━\n📊 포지션: <b>LONG</b>\n💰 최종 청산가: <code>{m(p)}</code>")
            return
    else:
        if p >= sl:
            close(s, p, "SL")
            tg(f"🛡 <b>골드 선물 · 손절(SL) 도달</b>\n━━━━━━━━━━━━━━━━━━━━━━━\n📊 포지션: <b>SHORT</b>\n💰 청산가: <code>{m(p)}</code>")
            return
        if st == "INITIAL" and p <= s["tp1"]:
            s["stage"] = "TP1_TRAIL"; s["sl"] = s["entry"]; save(STATE, s)
            tg(f"🎯 <b>골드 선물 · TP1 도달 성공!</b>\n━━━━━━━━━━━━━━━━━━━━━━━\n📊 포지션: <b>SHORT</b>\n💰 진입가: <code>{m(s['entry'])}</code>\n🎯 TP1 목표가: <code>{m(s['tp1'])}</code>\n🛡 <b>본전(Entry)으로 손절가(SL) 상향 조정됨</b>")
            return
        if st == "TP1_TRAIL" and p <= s["tp2"]:
            s["stage"] = "TP2_TRAIL"; s["sl"] = s["tp1"]; save(STATE, s)
            tg(f"🎯 <b>골드 선물 · TP2 도달 성공!</b>\n━━━━━━━━━━━━━━━━━━━━━━━\n📊 포지션: <b>SHORT</b>\n🎯 TP2 목표가: <code>{m(s['tp2'])}</code>\n🛡 <b>손절가(SL)가 TP1으로 상향 조정됨</b>")
            return
        if st == "TP2_TRAIL" and p <= s["tp3"]:
            close(s, p, "TP3_WIN")
            tg(f"🏆 <b>골드 선물 · TP3 최종 목표가 도달 성공! (WIN)</b>\n━━━━━━━━━━━━━━━━━━━━━━━\n📊 포지션: <b>SHORT</b>\n💰 최종 청산가: <code>{m(p)}</code>")
            return

def close(s, p, reason):
    s.update({"status": "IDLE", "direction": None, "entry": None, "sl": None, "stage": "INITIAL", "exit_price": p, "exit_reason": reason})
    save(STATE, s)
    log("EXIT", price=p, reason=reason)

def main():
    print("====================================")
    print(f" GOLD FUTURES SMART SIGNAL BOT V{V}")
    print("====================================")

    s = get_state()

    if s.get("status") == "ACTIVE" and s.get("direction"):
        print("[MONITOR] Active position found. Checking live price...")
        monitor(s)
        return

    print("[SCAN] Searching for new trading setups...")
    x15 = addind(getdata("15m", BT_PERIOD))
    x5 = addind(getdata("5m", BT_PERIOD))
    bt = backtest(x15, x5)
    s["backtest"] = bt
    save(STATE, s)

    best_d = None; best_score = 0; best_regime = "UNKNOWN"
    for d in ["LONG", "SHORT"]:
        score, regime = evaluate_multi_strategy(x15, x5, d, -1)
        if score > best_score:
            best_score = score; best_d = d; best_regime = regime

    if not best_d or best_score < 3.0:
        print("[SIGNAL] No qualified setup right now.")
        return

    p, a, src = live()
    if p is None: return

    e, sl, risk = levels(x15, best_d, -1)
    strong = best_score >= 6.0
    t1, t2, t3 = targets(e, risk, strong, best_d)

    sig = {
        "status": "ACTIVE", "direction": best_d,
        "entry": e, "sl": sl, "tp1": t1, "tp2": t2, "tp3": t3,
        "stage": "INITIAL", "score": best_score,
        "strength": "STRONG" if strong else "NORMAL",
        "regime": best_regime, "signal_time": ts()
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
