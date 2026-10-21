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

SYMBOL = "GC=F"  # 금 선물 티커
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
        "last_signal_time": 0,
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
        f"📊 *[골드 선물 정기 성적표 & 누적 통계]*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  • 총 청산 거래: `{total}회`\n"
        f"  • 상세 내역: 🏆 `{wins}승` / 🛡️ `{breakevens}본전방어` / 🔴 `{losses}패`\n"
        f"  • 실효 방어 성공률: **`{success_rate:.1f}%`**\n"
        f"⏱️ 기준 시간: `{ts()} (KST)`"
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
                [{"text": "📈 트레이딩뷰 실시간 차트 보기", "url": TV_URL}]
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
        return f"${float(v):,.2f}"
    except Exception:
        return "$0.00"

# --------------------------------------------------------------------------- #
#   3️⃣ 상위 트레이더 4대 전략 분석 엔진
# --------------------------------------------------------------------------- #
def analyze_top_trader_strategies(df: pd.DataFrame) -> tuple:
    if df.empty or len(df) < 20:
        return None, ""

    latest = df.iloc[-1]
    prev = df.iloc[-2]
    price = float(latest['close'])
    
    # 1. 유동성 스윕 (Liquidity Sweep)
    recent_high = df['high'].iloc[-15:-1].max()
    recent_low = df['low'].iloc[-15:-1].min()
    
    if prev['high'] > recent_high and price < recent_high:
        return "SHORT", "유동성 스윕 (Liquidity Sweep)"
    if prev['low'] < recent_low and price > recent_low:
        return "LONG", "유동성 스윕 (Liquidity Sweep)"

    # 2. 오더블록 / 매물대 리테스트
    body_size = abs(latest['close'] - latest['open'])
    avg_body = (abs(df['close'] - df['open'])).rolling(10).mean().iloc[-1]
    
    if body_size > avg_body * 1.5:
        if latest['close'] > latest['open']:
            return "LONG", "스마트머니 오더블록 (Order Block)"
        else:
            return "SHORT", "스마트머니 오더블록 (Order Block)"

    # 3. 라운드 넘버 근접성 검사
    remainder = price % 50
    if remainder < 3 or remainder > 47:
        if latest['close'] > prev['close']:
            return "LONG", "라운드 넘버 지지 (Round Number)"
        else:
            return "SHORT", "라운드 넘버 저항 (Round Number)"

    # 4. 모멘텀 & 볼륨 트렌드
    rsi = float(latest['rsi']) if not np.isnan(latest['rsi']) else 50.0
    if rsi < 38:
        return "LONG", "모멘텀 반전 (RSI Momentum)"
    elif rsi > 62:
        return "SHORT", "모멘텀 반전 (RSI Momentum)"

    return None, ""

# --------------------------------------------------------------------------- #
#   4️⃣ 메인 프로세스 (포지션 락 + 고품질 알림 연동)
# --------------------------------------------------------------------------- #
def process_gold_signal():
    state = load_state()
    pos = state.get("position")

    # 🚨 포지션 락: 이미 진입한 포지션이 있을 경우 스캔 중단
    if pos:
        print(f"[{ts()}] [POSITION LOCK ACTIVE] 기존 포지션 존속 중.")
        
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
                    f"🎯 *[골드 선물 LONG - 1차 목표(TP1) 도달 & 본전스탑]*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🪙 종목: `Gold Futures (GC=F)`\n"
                    f"💰 1차 달성가: `{fmt_money(recent_high)}` (+{fmt_money(profit)})\n"
                    f"🛡️ 안전 조치: 손절가를 진입가(`{fmt_money(pos['entry'])}`)로 수정하여 원금 방어를 시작합니다.\n"
                    f"⏱️ 시간: `{ts()} (KST)`", add_chart_btn=True
                )
                save_state(state)

            if "TP2" not in hit_list and recent_high >= pos["tp2"]:
                hit_list.append("TP2")
                profit = abs(pos["tp2"] - pos["entry"])
                send_telegram(
                    f"🎯 *[골드 선물 LONG - 2차 목표(TP2) 도달]*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🪙 종목: `Gold Futures (GC=F)`\n"
                    f"💰 2차 달성가: `{fmt_money(recent_high)}` (+{fmt_money(profit)})\n"
                    f"⏱️ 시간: `{ts()} (KST)`", add_chart_btn=True
                )
                save_state(state)

            if "TP3" not in hit_list and recent_high >= pos["tp3"]:
                hit_list.append("TP3")
                profit = abs(pos["tp3"] - pos["entry"])
                state["total_trades"] = state.get("total_trades", 0) + 1
                state["wins"] = state.get("wins", 0) + 1
                send_telegram(
                    f"🎉 *[골드 선물 LONG - 3차 최종 목표(TP3) 익절 달성!]*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🪙 종목: `Gold Futures (GC=F)`\n"
                    f"🏆 최종 익절가: `{fmt_money(recent_high)}` (최종 수익: +{fmt_money(profit)})\n"
                    f"⏱️ 시간: `{ts()} (KST)`", add_chart_btn=True
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
                        f"🛡️ *[골드 선물 LONG - 본전 마감 (Breakeven 방어 성공)]*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🪙 종목: `Gold Futures (GC=F)`\n"
                        f"💰 방어가격: `{fmt_money(recent_low)}` (진입가 청산)\n"
                        f"⏱️ 시간: `{ts()} (KST)`", add_chart_btn=True
                    )
                    send_performance_report(state)
                else:
                    state["total_trades"] = state.get("total_trades", 0) + 1
                    state["losses"] = state.get("losses", 0) + 1
                    loss_amt = abs(pos["entry"] - pos["sl"])
                    send_telegram(
                        f"🔴 *[골드 선물 LONG - 손절(SL) 마감]*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🪙 종목: `Gold Futures (GC=F)`\n"
                        f"📉 최종 도달가: `{fmt_money(recent_low)}` (-{fmt_money(loss_amt)})\n"
                        f"⏱️ 시간: `{ts()} (KST)`", add_chart_btn=True
                    )
                    send_performance_report(state)
                
                state["position"] = None
                save_state(state)
                return

        else: # SHORT 포지션 관리
            if "TP1" not in hit_list and recent_low <= pos["tp1"]:
                hit_list.append("TP1")
                pos["tp1_hit"] = True
                pos["sl"] = pos["entry"]
                profit = abs(pos["entry"] - pos["tp1"])
                send_telegram(
                    f"🎯 *[골드 선물 SHORT - 1차 목표(TP1) 도달 & 본전스탑]*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🪙 종목: `Gold Futures (GC=F)`\n"
                    f"💰 1차 달성가: `{fmt_money(recent_low)}` (+{fmt_money(profit)})\n"
                    f"🛡️ 안전 조치: 손절가를 진입가(`{fmt_money(pos['entry'])}`)로 수정하여 원금 방어를 시작합니다.\n"
                    f"⏱️ 시간: `{ts()} (KST)`", add_chart_btn=True
                )
                save_state(state)

            if "TP2" not in hit_list and recent_low <= pos["tp2"]:
                hit_list.append("TP2")
                profit = abs(pos["entry"] - pos["tp2"])
                send_telegram(
                    f"🎯 *[골드 선물 SHORT - 2차 목표(TP2) 도달]*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🪙 종목: `Gold Futures (GC=F)`\n"
                    f"💰 2차 달성가: `{fmt_money(recent_low)}` (+{fmt_money(profit)})\n"
                    f"⏱️ 시간: `{ts()} (KST)`", add_chart_btn=True
                )
                save_state(state)

            if "TP3" not in hit_list and recent_low <= pos["tp3"]:
                hit_list.append("TP3")
                profit = abs(pos["entry"] - pos["tp3"])
                state["total_trades"] = state.get("total_trades", 0) + 1
                state["wins"] = state.get("wins", 0) + 1
                send_telegram(
                    f"🎉 *[골드 선물 SHORT - 3차 최종 목표(TP3) 익절 달성!]*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🪙 종목: `Gold Futures (GC=F)`\n"
                    f"🏆 최종 익절가: `{fmt_money(recent_low)}` (최종 수익: +{fmt_money(profit)})\n"
                    f"⏱️ 시간: `{ts()} (KST)`", add_chart_btn=True
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
                        f"🛡️ *[골드 선물 SHORT - 본전 마감 (Breakeven 방어 성공)]*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🪙 종목: `Gold Futures (GC=F)`\n"
                        f"💰 방어가격: `{fmt_money(recent_high)}` (진입가 청산)\n"
                        f"⏱️ 시간: `{ts()} (KST)`", add_chart_btn=True
                    )
                    send_performance_report(state)
                else:
                    state["total_trades"] = state.get("total_trades", 0) + 1
                    state["losses"] = state.get("losses", 0) + 1
                    loss_amt = abs(pos["sl"] - pos["entry"])
                    send_telegram(
                        f"🔴 *[골드 선물 SHORT - 손절(SL) 마감]*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🪙 종목: `Gold Futures (GC=F)`\n"
                        f"📉 최종 도달가: `{fmt_money(recent_high)}` (-{fmt_money(loss_amt)})\n"
                        f"⏱️ 시간: `{ts()} (KST)`", add_chart_btn=True
                    )
                    send_performance_report(state)
                
                state["position"] = None
                save_state(state)
                return

        save_state(state)
        return

    # ----------------포지션 미보유 시 신규 타점 스캔---------------- #
    df = fetch_gold_data(interval="15m", period="5d")
    if df.empty:
        return

    df = calculate_indicators(df)
    latest = df.iloc[-1]
    price = float(latest['close'])
    atr = float(latest['atr']) if not np.isnan(latest['atr']) else 15.0
    if atr < 8.0:
        atr = 12.0

    direction, strategy_name = analyze_top_trader_strategies(df)
    print(f"[{ts()}] Gold Price: {fmt_money(price)} | Strategy Signal: {direction} ({strategy_name})")

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
            f"🏛️ *[기관급 앙상블 신규 타점 포착]* 🏛️\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 종목: `Gold Futures (GC=F)`\n"
            f"📊 포지션: **{pos_text}**\n"
            f"💡 진입 근거: `{strategy_name}`\n"
            f"💰 진입가: `{fmt_money(price)}`\n\n"
            f"🎯 목표가 (Take Profit & 기대 수익)\n"
            f"  • 1차 (TP1): `{fmt_money(tp1)}` (기대: +{fmt_money(tp1_profit)} / 본전스탑 전환)\n"
            f"  • 2차 (TP2): `{fmt_money(tp2)}` (기대: +{fmt_money(tp2_profit)})\n"
            f"  • 3차 (TP3): `{fmt_money(tp3)}` (기대: +{fmt_money(tp3_profit)} / 대세 추세)\n\n"
            f"🛡️ 손절가 (Stop Loss): `{fmt_money(sl)}` (-{fmt_money(sl_risk)})\n"
            f"⚖️ 최고 기대 손익비 (RRR): **1 : 5.0**\n"
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
            print(f"[{ts()}] Enhanced Signal Sent successfully.")

def main():
    print(f"===================================")
    print(f" MASTER GOLD ENSEMBLE BOT RUNNING")
    print(f"===================================")
    process_gold_signal()

if __name__ == "__main__":
    main()
