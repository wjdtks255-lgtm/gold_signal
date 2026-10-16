from datetime import datetime, timedelta, timezone
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List
import requests
import yfinance as yf
import pandas as pd
import numpy as np

# --------------------------------------------------------------------------- #
#   1️⃣ 기본 설정 및 상수 (금 선물 전용)
# --------------------------------------------------------------------------- #
TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID") or "").strip()

BASE_DIR = Path.cwd()
STATE_FILE = BASE_DIR / "signal_state.json"
KST = timezone(timedelta(hours=9))

VERSION = "20.2.0"
SYMBOL = "GC=F"  # 금 선물 티커
TV_URL = "https://www.tradingview.com/chart/?symbol=GC%3DF"  # 트레이딩뷰 골드 선물 차트 링크

# --------------------------------------------------------------------------- #
#   2️⃣ 유틸리티 및 안전한 파일 I/O
# --------------------------------------------------------------------------- #
class NpEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, (np.integer, np.int64)):
            return int(obj)
        if isinstance(obj, (np.floating, np.float64)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, (np.bool_, bool)):
            return bool(obj)
        return super().default(obj)

def now() -> datetime:
    return datetime.now(KST)

def ts() -> str:
    return now().strftime("%Y-%m-%d %H:%M:%S")

def load_state() -> Dict[str, Any]:
    default_state = {"position": None, "last_signal_time": 0}
    if not STATE_FILE.exists():
        return default_state
    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return default_state
            return data
    except Exception as e:
        print(f"[{ts()}] State Load Error: {e}")
        return default_state

def save_state(state: Dict[str, Any]) -> None:
    tmp_file = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
    try:
        with tmp_file.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2, cls=NpEncoder)
        tmp_file.replace(STATE_FILE)
    except Exception as e:
        print(f"[{ts()}] State Save Error: {e}")

# --------------------------------------------------------------------------- #
#   3️⃣ 텔레그램 알림 시스템 (인라인 버튼 링크 지원)
# --------------------------------------------------------------------------- #
def send_telegram(text: str, add_chart_btn: bool = False) -> bool:
    if not TOKEN or not CHAT_ID:
        print(f"[{ts()}] TG CONFIG ERROR: Token or Chat ID missing.")
        return False
    
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    
    # 차트 바로가기 버튼 추가
    if add_chart_btn:
        payload["reply_markup"] = {
            "inline_keyboard": [
                [{"text": "📈 트레이딩뷰 차트 보기 (GC=F)", "url": TV_URL}]
            ]
        }

    for attempt in range(3):
        try:
            res = requests.post(url, json=payload, timeout=20)
            if res.status_code == 200:
                return True
        except Exception as e:
            print(f"[{ts()}] TG Exception (try {attempt+1}): {e}")
        time.sleep(1.5)
    return False

# --------------------------------------------------------------------------- #
#   4️⃣ 금 선물 데이터 수집 및 지표 계산
# --------------------------------------------------------------------------- #
def fetch_gold_data() -> pd.DataFrame:
    try:
        ticker = yf.Ticker(SYMBOL)
        df = ticker.history(period="5d", interval="15m")
        if df.empty:
            return pd.DataFrame()
        df.columns = [c.lower() for c in df.columns]
        return df
    except Exception as e:
        print(f"[{ts()}] Data Fetch Error: {e}")
        return pd.DataFrame()

def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or len(df) < 20:
        return df
    
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / (loss + 1e-9)
    df['rsi'] = 100 - (100 / (1 + rs))
    
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['atr'] = tr.rolling(window=14).mean()
    
    return df

def fmt_money(v: Any) -> str:
    try:
        val = float(v)
        return f"${val:,.2f}"
    except Exception:
        return "$0.00"

# --------------------------------------------------------------------------- #
#   5️⃣ 포지션 및 래더 추적 엔진 (금 전용)
# --------------------------------------------------------------------------- #
def process_gold_signal():
    state = load_state()
    df = fetch_gold_data()
    if df.empty:
        print(f"[{ts()}] Failed to fetch gold data.")
        return

    df = calculate_indicators(df)
    latest = df.iloc[-1]
    
    price = float(latest['close'])
    rsi = float(latest['rsi'])
    atr = float(latest['atr']) if not np.isnan(latest['atr']) else price * 0.005
    
    print(f"[{ts()}] Gold Price: {fmt_money(price)} | RSI: {rsi:.2f} | ATR: {atr:.2f}")

    pos = state.get("position")

    if pos:
        long = pos["direction"] == "LONG"
        high_p = float(latest['high'])
        low_p = float(latest['low'])

        if long:
            if high_p >= pos["tp3"]:
                send_telegram(f"🎉 *[골드 선물 TP3 최종 익절 달성!]*\n💰 가격: `{fmt_money(high_p)}`", add_chart_btn=True)
                state["position"] = None
            elif low_p <= pos["sl"]:
                send_telegram(f"🛡️ *[골드 선물 손절(SL) 도달]*\n📉 가격: `{fmt_money(low_p)}`", add_chart_btn=True)
                state["position"] = None
            elif high_p >= pos["tp2"] and "TP2" not in pos.get("hit", []):
                send_telegram(f"🎯 *[골드 선물 TP2 2차 익절 도달]*\n💰 가격: `{fmt_money(high_p)}`", add_chart_btn=True)
                pos.setdefault("hit", []).append("TP2")
            elif not pos.get("tp1_hit", False) and high_p >= pos["tp1"]:
                pos["tp1_hit"] = True
                pos["sl"] = pos["entry"]
                send_telegram(f"🎯 *[골드 선물 TP1 1차 익절 & 본전스탑]*\n🛡️ 손절가를 진입가로 상향합니다.", add_chart_btn=True)
        else:
            if low_p <= pos["tp3"]:
                send_telegram(f"🎉 *[골드 선물 숏 TP3 최종 익절 달성!]*", add_chart_btn=True)
                state["position"] = None
            elif high_p >= pos["sl"]:
                send_telegram(f"🛡️ *[골드 선물 숏 손절(SL) 도달]*", add_chart_btn=True)
                state["position"] = None
            elif low_p <= pos["tp2"] and "TP2" not in pos.get("hit", []):
                send_telegram(f"🎯 *[골드 선물 숏 TP2 도달]*", add_chart_btn=True)
                pos.setdefault("hit", []).append("TP2")
            elif not pos.get("tp1_hit", False) and low_p <= pos["tp1"]:
                pos["tp1_hit"] = True
                pos["sl"] = pos["entry"]
                send_telegram(f"🎯 *[골드 선물 숏 TP1 1차 익절 & 본전스탑]*", add_chart_btn=True)

        save_state(state)
        return

    direction = None
    if rsi <= 38:
        direction = "LONG"
    elif rsi >= 62:
        direction = "SHORT"

    if direction:
        risk = atr * 1.5
        if direction == "LONG":
            sl = price - risk
            tp1 = price + risk * 1.5
            tp2 = price + risk * 2.5
            tp3 = price + risk * 4.0
            pos_text = "롱 (LONG) 🟢"
        else:
            sl = price + risk
            tp1 = price - risk * 1.5
            tp2 = price - risk * 2.5
            tp3 = price - risk * 4.0
            pos_text = "숏 (SHORT) 🔴"

        msg = (
            f"🌟 *골드 선물 스마트 시그널 v{VERSION}* 🌟\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 종목: `Gold Futures (GC=F)`\n"
            f"📊 포지션: **{pos_text}**\n"
            f"💰 진입가: `{fmt_money(price)}`\n"
            f"📊 RSI: `{rsi:.2f}` | ATR: `{atr:.2f}`\n\n"
            f"🎯 목표가 (Take Profit)\n"
            f"  • 1차 (TP1): `{fmt_money(tp1)}` (본전스탑)\n"
            f"  • 2차 (TP2): `{fmt_money(tp2)}`\n"
            f"  • 3차 (TP3): `{fmt_money(tp3)}`\n\n"
            f"🛡️ 손절가 (Stop Loss): `{fmt_money(sl)}`\n"
            f"⏱️ 시간: `{ts()} (KST)`"
        )

        if send_telegram(msg, add_chart_btn=True):
            state["position"] = {
                "direction": direction,
                "entry": price,
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "tp3": tp3,
                "tp1_hit": False,
                "hit": []
            }
            save_state(state)
            print(f"[{ts()}] Gold Signal Sent successfully with chart link.")

def main():
    print(f"===================================")
    print(f" GOLD FUTURES BOT v{VERSION} RUNNING")
    print(f"===================================")
    process_gold_signal()

if __name__ == "__main__":
    main()
