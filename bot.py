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
#   1️⃣ 기본 설정 및 상수
# --------------------------------------------------------------------------- #
TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID") or "").strip()

BASE_DIR = Path.cwd()
STATE_FILE = BASE_DIR / "signal_state.json"
KST = timezone(timedelta(hours=9))

SYMBOL = "GC=F"
TV_URL = "https://www.tradingview.com/chart/?symbol=COMEX%3AGC1%21"

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
    default_state = {
        "position": None,
        "last_loss_time": 0,  # 손절 발생 시간 기록 (쿨타임용)
        "total_trades": 0,
        "wins": 0,
        "breakevens": 0,
        "losses": 0
    }
    if not STATE_FILE.exists():
        return default_state
    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return default_state
            for k, v in default_state.items():
                data.setdefault(k, v)
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

def send_performance_report(state: Dict[str, Any]) -> None:
    total = state.get("total_trades", 0)
    wins = state.get("wins", 0)
    breakevens = state.get("breakevens", 0)
    losses = state.get("losses", 0)
    
    successful_trades = wins + breakevens
    success_rate = (successful_trades / total * 100) if total > 0 else 0.0
    
    report_text = (
        f"📊 *[골드 선물 보유 현황 & 성과 브리핑]*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  • 총 거래: `{total}회`\n"
        f"  • 내역: 🏆 `{wins}승` / 🛡️ `{breakevens}본전` / 🔴 `{losses}패`\n"
        f"  • 방어 포함 성공률: **`{success_rate:.1f}%`**\n"
        f"⏱️ `{ts()} (KST)`"
    )
    send_telegram(report_text, add_chart_btn=False)

def send_telegram(text: str, add_chart_btn: bool = False) -> bool:
    if not TOKEN or not CHAT_ID:
        return False
    
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    
    if add_chart_btn:
        payload["reply_markup"] = {
            "inline_keyboard": [
                [{"text": "📈 트레이딩뷰 차트 바로가기", "url": TV_URL}]
            ]
        }

    for attempt in range(3):
        try:
            res = requests.post(url, json=payload, timeout=10)
            if res.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False

def fetch_gold_data(interval: str = "15m", period: str = "5d") -> pd.DataFrame:
    try:
        ticker = yf.Ticker(SYMBOL)
        df = ticker.history(period=period, interval=interval)
        if df.empty:
            return pd.DataFrame()
        df.columns = [c.lower() for c in df.columns]
        return df
    except Exception as e:
        print(f"[{ts()}] Data Fetch Error ({interval}): {e}")
        return pd.DataFrame()

def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or len(df) < 50:
        return df
    
    # ATR (변동성 기반 손절/익절폭)
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['atr'] = tr.rolling(window=14).mean()
    
    # 추세 필터용 50 EMA
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
    
    return df

def fmt_money(v: Any) -> str:
    try:
        return f"${float(v):,.2f}"
    except Exception:
        return "$0.00"

# --------------------------------------------------------------------------- #
#   3️⃣ 엄격해진 3대 핵심 전략 분석 엔진 (단순 RSI 완전 제거)
# --------------------------------------------------------------------------- #
def analyze_strict_strategies(df: pd.DataFrame) -> tuple:
    if df.empty or len(df) < 50:
        return None, ""

    latest = df.iloc[-1]
    prev = df.iloc[-2]
    price = float(latest['close'])
    ema50 = float(latest['ema50'])
    
    # 1. 유동성 스윕 (Liquidity Sweep)
    recent_high = df['high'].iloc[-15:-1].max()
    recent_low = df['low'].iloc[-15:-1].min()
    
    if prev['high'] > recent_high and price < recent_high:
        if price < ema50: # 추세 필터: EMA 밑에 있을 때만 숏
            return "SHORT", "유동성 스윕"
    if prev['low'] < recent_low and price > recent_low:
        if price > ema50: # 추세 필터: EMA 위에 있을 때만 롱
            return "LONG", "유동성 스윕"

    # 2. 스마트머니 오더블록 (Order Block)
    body_size = abs(latest['close'] - latest['open'])
    avg_body = (abs(df['close'] - df['open'])).rolling(10).mean().iloc[-1]
    
    if body_size > avg_body * 1.8: # 기준 강화
        if latest['close'] > latest['open'] and price > ema50:
            return "LONG", "스마트머니 오더블록"
        elif latest['close'] < latest['open'] and price < ema50:
            return "SHORT", "스마트머니 오더블록"

    # 3. 라운드 넘버 반등/거부 (Round Number)
    remainder = price % 50
    if remainder < 2 or remainder > 48:
        if latest['close'] > prev['close'] and price > ema50:
            return "LONG", "라운드 넘버"
        elif latest['close'] < prev['close'] and price < ema50:
            return "SHORT", "라운드 넘버"

    return None, ""

# --------------------------------------------------------------------------- #
#   4️⃣ 메인 프로세스 (포지션 락 + 1시간 손절 쿨타임 적용)
# --------------------------------------------------------------------------- #
def process_gold_signal():
    state = load_state()
    pos = state.get("position")
    current_time = time.time()

    # 🚨 1. 포지션 락: 기존 포지션 관리
    if pos:
        print(f"[{ts()}] [POSITION LOCK] 기존 포지션 관리 중.")
        
        df_1m = fetch_gold_data(interval="1m", period="1d")
        if df_1m.empty:
            return
        
        latest_1m = df_1m.iloc[-1]
        recent_high = float(latest_1m['high'])
        recent_low = float(latest_1m['low'])
        
        long = pos["direction"] == "LONG"
        hit_list = pos.setdefault("hit", [])

        if long:
            if "TP1" not in hit_list and recent_high >= pos["tp1"]:
                hit_list.append("TP1")
                pos["tp1_hit"] = True
                pos["sl"] = pos["entry"]
                profit = abs(pos["tp1"] - pos["entry"])
                send_telegram(
                    f"🎯 *[골드 선물 LONG - 1차 TP1 도달]*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🪙 종목: `Gold Futures (GC=F)`\n"
                    f"💰 도달가: `{fmt_money(recent_high)}` (+{fmt_money(profit)})\n"
                    f"🛡️ 손절가를 진입가(`{fmt_money(pos['entry'])}`)로 수정 (본전스탑)\n"
                    f"⏱️ `{ts()} (KST)`", add_chart_btn=True
                )
                save_state(state)

            if "TP2" not in hit_list and recent_high >= pos["tp2"]:
                hit_list.append("TP2")
                profit = abs(pos["tp2"] - pos["entry"])
                send_telegram(
                    f"🎯 *[골드 선물 LONG - 2차 TP2 도달]*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🪙 종목: `Gold Futures (GC=F)`\n"
                    f"💰 도달가: `{fmt_money(recent_high)}` (+{fmt_money(profit)})\n"
                    f"⏱️ `{ts()} (KST)`", add_chart_btn=True
                )
                save_state(state)

            if "TP3" not in hit_list and recent_high >= pos["tp3"]:
                hit_list.append("TP3")
                profit = abs(pos["tp3"] - pos["entry"])
                state["total_trades"] = state.get("total_trades", 0) + 1
                state["wins"] = state.get("wins", 0) + 1
                send_telegram(
                    f"🎉 *[골드 선물 LONG - 3차 TP3 익절 달성!]*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🪙 종목: `Gold Futures (GC=F)`\n"
                    f"🏆 최종가: `{fmt_money(recent_high)}` (+{fmt_money(profit)})\n"
                    f"⏱️ `{ts()} (KST)`", add_chart_btn=True
                )
                send_performance_report(state)
                state["position"] = None
                save_state(state)
                return

            if recent_low <= pos["sl"]:
                if pos.get("tp1_hit", False):
                    state["total_trades"] = state.get("total_trades", 0) + 1
                    state["breakevens"] = state.get("breakevens", 0) + 1
                    send_telegram(
                        f"🛡️ *[골드 선물 LONG - 본전 마감]*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🪙 종목: `Gold Futures (GC=F)`\n"
                        f"💰 방어가격: `{fmt_money(recent_low)}`\n"
                        f"⏱️ `{ts()} (KST)`", add_chart_btn=True
                    )
                    send_performance_report(state)
                else:
                    state["total_trades"] = state.get("total_trades", 0) + 1
                    state["losses"] = state.get("losses", 0) + 1
                    state["last_loss_time"] = current_time # 손절 쿨타임 시각 기록
                    loss_amt = abs(pos["entry"] - pos["sl"])
                    send_telegram(
                        f"🔴 *[골드 선물 LONG - 손절 마감]*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🪙 종목: `Gold Futures (GC=F)`\n"
                        f"📉 청산가: `{fmt_money(recent_low)}` (-{fmt_money(loss_amt)})\n"
                        f"⏳ 조치: 추세 안정을 위해 1시간 동안 신규 스캔을 중단합니다.\n"
                        f"⏱️ `{ts()} (KST)`", add_chart_btn=True
                    )
                    send_performance_report(state)
                
                state["position"] = None
                save_state(state)
                return

        else: # SHORT 관리
            if "TP1" not in hit_list and recent_low <= pos["tp1"]:
                hit_list.append("TP1")
                pos["tp1_hit"] = True
                pos["sl"] = pos["entry"]
                profit = abs(pos["entry"] - pos["tp1"])
                send_telegram(
                    f"🎯 *[골드 선물 SHORT - 1차 TP1 도달]*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🪙 종목: `Gold Futures (GC=F)`\n"
                    f"💰 도달가: `{fmt_money(recent_low)}` (+{fmt_money(profit)})\n"
                    f"🛡️ 손절가를 진입가(`{fmt_money(pos['entry'])}`)로 수정 (본전스탑)\n"
                    f"⏱️ `{ts()} (KST)`", add_chart_btn=True
                )
                save_state(state)

            if "TP2" not in hit_list and recent_low <= pos["tp2"]:
                hit_list.append("TP2")
                profit = abs(pos["entry"] - pos["tp2"])
                send_telegram(
                    f"🎯 *[골드 선물 SHORT - 2차 TP2 도달]*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🪙 종목: `Gold Futures (GC=F)`\n"
                    f"💰 도달가: `{fmt_money(recent_low)}` (+{fmt_money(profit)})\n"
                    f"⏱️ `{ts()} (KST)`", add_chart_btn=True
                )
                save_state(state)

            if "TP3" not in hit_list and recent_low <= pos["tp3"]:
                hit_list.append("TP3")
                profit = abs(pos["entry"] - pos["tp3"])
                state["total_trades"] = state.get("total_trades", 0) + 1
                state["wins"] = state.get("wins", 0) + 1
                send_telegram(
                    f"🎉 *[골드 선물 SHORT - 3차 TP3 익절 달성!]*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🪙 종목: `Gold Futures (GC=F)`\n"
                    f"🏆 최종가: `{fmt_money(recent_low)}` (+{fmt_money(profit)})\n"
                    f"⏱️ `{ts()} (KST)`", add_chart_btn=True
                )
                send_performance_report(state)
                state["position"] = None
                save_state(state)
                return

            if recent_high >= pos["sl"]:
                if pos.get("tp1_hit", False):
                    state["total_trades"] = state.get("total_trades", 0) + 1
                    state["breakevens"] = state.get("breakevens", 0) + 1
                    send_telegram(
                        f"🛡️ *[골드 선물 SHORT - 본전 마감]*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🪙 종목: `Gold Futures (GC=F)`\n"
                        f"💰 방어가격: `{fmt_money(recent_high)}`\n"
                        f"⏱️ `{ts()} (KST)`", add_chart_btn=True
                    )
                    send_performance_report(state)
                else:
                    state["total_trades"] = state.get("total_trades", 0) + 1
                    state["losses"] = state.get("losses", 0) + 1
                    state["last_loss_time"] = current_time # 손절 쿨타임 시각 기록
                    loss_amt = abs(pos["sl"] - pos["entry"])
                    send_telegram(
                        f"🔴 *[골드 선물 SHORT - 손절 마감]*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🪙 종목: `Gold Futures (GC=F)`\n"
                        f"📉 청산가: `{fmt_money(recent_high)}` (-{fmt_money(loss_amt)})\n"
                        f"⏳ 조치: 추세 안정을 위해 1시간 동안 신규 스캔을 중단합니다.\n"
                        f"⏱️ `{ts()} (KST)`", add_chart_btn=True
                    )
                    send_performance_report(state)
                
                state["position"] = None
                save_state(state)
                return

        save_state(state)
        return

    # 🚨 2. 손절 후 1시간 쿨타임 검사
    last_loss = state.get("last_loss_time", 0)
    if current_time - last_loss < 3600: # 3600초 = 1시간
        remaining_min = int((3600 - (current_time - last_loss)) // 60)
        print(f"[{ts()}] [COOL-DOWN ACTIVE] 최근 손절 발생. 잔여 쿨타임: {remaining_min}분")
        return

    # ----------------신규 타점 스캔---------------- #
    df = fetch_gold_data(interval="15m", period="5d")
    if df.empty:
        return

    df = calculate_indicators(df)
    latest = df.iloc[-1]
    price = float(latest['close'])
    atr = float(latest['atr']) if not np.isnan(latest['atr']) else 15.0
    if atr < 8.0:
        atr = 12.0

    direction, strategy_name = analyze_strict_strategies(df)
    print(f"[{ts()}] Gold Price: {fmt_money(price)} | Strategy: {direction} ({strategy_name})")

    if direction:
        risk = max(atr * 1.0, 10.0)
        if direction == "LONG":
            sl = price - risk
            tp1 = price + risk * 1.5
            tp2 = price + risk * 3.0
            tp3 = price + risk * 5.0
            pos_text = "롱 (LONG) 🟢"
        else:
            sl = price + risk
            tp1 = price - risk * 1.5
            tp2 = price - risk * 3.0
            tp3 = price - risk * 5.0
            pos_text = "숏 (SHORT) 🔴"

        tp1_profit = abs(tp1 - price)
        tp2_profit = abs(tp2 - price)
        tp3_profit = abs(tp3 - price)
        sl_risk = abs(price - sl)

        msg = (
            f"🪙 *[골드 선물 실전 타점]* 🪙\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 종목: `Gold Futures (GC=F)`\n"
            f"📊 포지션: **{pos_text}**\n"
            f"💡 근거: `{strategy_name}`\n"
            f"💰 진입가: `{fmt_money(price)}`\n\n"
            f"🎯 목표가 (Take Profit)\n"
            f"  • 1차 (TP1): `{fmt_money(tp1)}` (+`{fmt_money(tp1_profit)}` / 본전스탑)\n"
            f"  • 2차 (TP2): `{fmt_money(tp2)}` (+`{fmt_money(tp2_profit)}`)\n"
            f"  • 3차 (TP3): `{fmt_money(tp3)}` (+`{fmt_money(tp3_profit)}`)\n\n"
            f"🛡️ 손절가 (Stop Loss): `{fmt_money(sl)}` (-`{fmt_money(sl_risk)}`)\n"
            f"⚖️ 손익비: **1 : 5.0**\n"
            f"⏱️ `{ts()} (KST)`"
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
            print(f"[{ts()}] Strict Signal Sent successfully.")

def main():
    print(f"===================================")
    print(f" GOLD FUTURES STRICT BOT RUNNING")
    print(f"===================================")
    process_gold_signal()

if __name__ == "__main__":
    main()
