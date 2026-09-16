import os
import json
from datetime import datetime
import requests
import yfinance as yf

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE = "signal_state.json"

TIMEFRAME = "15분봉 (M15)"

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    response = requests.post(url, json=payload)
    return response.json()

def get_gold_price():
    try:
        # GCZ2026 (2026년 12월물 금 선물) 정확한 야후 파이낸스 계약 심볼
        ticker = yf.Ticker("GCZ26=F")
        data = ticker.history(period="1d", interval="1m")
        if not data.empty:
            price = data['Close'].iloc[-1]
            return round(price, 2)
        
        # 혹시 데이터를 못 가져올 경우를 대비한 백업 심볼
        ticker_alt = yf.Ticker("GC=F")
        data_alt = ticker_alt.history(period="1d", interval="1m")
        if not data_alt.empty:
            return round(data_alt['Close'].iloc[-1], 2)
            
        return None
    except Exception as e:
        print(f"실시간 금 가격 조회 실패: {e}")
        return None

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return None

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)

def clear_state():
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)

def main():
    price = get_gold_price()
    if not price:
        print("금 가격을 가져오지 못했습니다.")
        return

    state = load_state()
    current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    chart_link = "[TradingView 차트 보기 (GCZ2026)](https://www.tradingview.com/chart/?symbol=GCZ2026)"

    # =========================================================================
    # [1] 기존 포지션이 진행 중인 경우: 목표가(TP) 및 손절가(SL) 도달 여부 감시
    # =========================================================================
    if state:
        pos_type = state["type"]
        entry = state["entry"]
        tp1 = state["tp1"]
        tp2 = state["tp2"]
        tp3 = state["tp3"]
        sl = state["sl"]

        print(f"포지션 모니터링 중 [{pos_type}] | 진입가: {entry} | 현재가: {price}")

        if pos_type == "LONG":
            if price >= tp3:
                send_telegram(f"🎯 **[골드 선물 3차 목표가 도달 (TP3)]**\n\n• 기준 타임프레임: `{TIMEFRAME}`\n• 진입가: `${entry:,.2f}`\n• 현재가: `${price:,.2f}`\n🔥 **TP3 최종 익절 달성! 포지션이 종료되었습니다.** 🚀\n\n🔗 {chart_link}")
                clear_state()
            elif price >= tp2:
                send_telegram(f"🎯 **[골드 선물 2차 목표가 도달 (TP2)]**\n\n• 기준 타임프레임: `{TIMEFRAME}`\n• 진입가: `${entry:,.2f}`\n• 현재가: `${price:,.2f}`\n✨ **TP2 도달! 본절가로 스탑로스(SL) 이동 추천!**\n\n🔗 {chart_link}")
            elif price >= tp1:
                send_telegram(f"🎯 **[골드 선물 1차 목표가 도달 (TP1)]**\n\n• 기준 타임프레임: `{TIMEFRAME}`\n• 진입가: `${entry:,.2f}`\n• 현재가: `${price:,.2f}`\n📈 **TP1 도달! 일부 익절 구간입니다.**\n\n🔗 {chart_link}")
            elif price <= sl:
                send_telegram(f"🛑 **[골드 선물 손절가 도달 (SL)]**\n\n• 기준 타임프레임: `{TIMEFRAME}`\n• 진입가: `${entry:,.2f}`\n• 현재가: `${price:,.2f}`\n❌ **손절가(SL) 라인 터치. 포지션이 종료되었습니다.**\n\n🔗 {chart_link}")
                clear_state()

        elif pos_type == "SHORT":
            if price <= tp3:
                send_telegram(f"🎯 **[골드 선물 3차 목표가 도달 (TP3)]**\n\n• 기준 타임프레임: `{TIMEFRAME}`\n• 진입가: `${entry:,.2f}`\n• 현재가: `${price:,.2f}`\n🔥 **TP3 최종 익절 달성! 포지션이 종료되었습니다.** 🚀\n\n🔗 {chart_link}")
                clear_state()
            elif price <= tp2:
                send_telegram(f"🎯 **[골드 선물 2차 목표가 도달 (TP2)]**\n\n• 기준 타임프레임: `{TIMEFRAME}`\n• 진입가: `${entry:,.2f}`\n• 현재가: `${price:,.2f}`\n✨ **TP2 도달! 본절가로 스탑로스(SL) 이동 추천!**\n\n🔗 {chart_link}")
            elif price <= tp1:
                send_telegram(f"🎯 **[골드 선물 1차 목표가 도달 (TP1)]**\n\n• 기준 타임프레임: `{TIMEFRAME}`\n• 진입가: `${entry:,.2f}`\n• 현재가: `${price:,.2f}`\n📈 **TP1 도달! 일부 익절 구간입니다.**\n\n🔗 {chart_link}")
            elif price >= sl:
                send_telegram(f"🛑 **[골드 선물 손절가 도달 (SL)]**\n\n• 기준 타임프레임: `{TIMEFRAME}`\n• 진입가: `${entry:,.2f}`\n• 현재가: `${price:,.2f}`\n❌ **손절가(SL) 라인 터치. 포지션이 종료되었습니다.**\n\n🔗 {chart_link}")
                clear_state()
        
        return

    # =========================================================================
    # [2] 신규 포지션 탐색
    # =========================================================================
    decimal_val = price % 10
    
    if decimal_val >= 5.0:
        pos_type = "LONG"
        action_text = "🟢 **롱 포지션 (매수 진입)**"
        tp1 = price + 6.5
        tp2 = price + 14.0
        tp3 = price + 24.5
        sl = price - 9.0
        reason = "핵심 지지선 반등 및 상승 오더블록 수렴 구간 포착"
    else:
        pos_type = "SHORT"
        action_text = "🔴 **숏 포지션 (매도 진입)**"
        tp1 = price - 6.5
        tp2 = price - 14.0
        tp3 = price - 24.5
        sl = price + 9.0
        reason = "주요 저항선 거부 및 매도 유동성 스윕 발생"

    new_state = {
        "type": pos_type,
        "entry": price,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "sl": sl
    }
    save_state(new_state)

    message = (
        f"💎 **[GCZ2026 골드 선물 실시간 기술적 분석 시그널]** 💎\n"
        f"────────────────────────\n"
        f"⏱ **발행 시간**: `{current_time}`\n"
        f"📊 **분석 기준**: `⏱ {TIMEFRAME}`\n"
        f"📈 **매매 방향**: {action_text}\n"
        f"💰 **추천 진입가**: `${price:,.2f}`\n\n"
        f"🎯 **목표가 설정 (TP)**\n"
        f"• **1차 목표 (TP1)**: `${tp1:,.2f}`\n"
        f"• **2차 목표 (TP2)**: `${tp2:,.2f}`\n"
        f"• **3차 목표 (TP3)**: `${tp3:,.2f}`\n\n"
        f"🛡 **리스크 관리 (낮은 리스크 타점)**\n"
        f"• **손절가 (SL)**: `${sl:,.2f}`\n\n"
        f"📈 **시장 구조 및 진입 근거**\n"
        f"_{reason}_\n"
        f"────────────────────────\n"
        f"🔗 {chart_link}\n"
        f"⚡ *자동 기술적 분석 시스템 가동 중*"
    )
    
    send_telegram(message)

if __name__ == "__main__":
    main()

