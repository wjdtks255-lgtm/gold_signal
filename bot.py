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

def get_market_data():
    try:
        ticker = yf.Ticker("GC=F")
        data = ticker.history(period="1d", interval="15m")
        if not data.empty:
            latest = data.iloc[-1]
            prev_price = data['Close'].iloc[-2] if len(data) >= 2 else latest['Close']
            current_price = latest['Close']
            low_price = latest['Low']
            high_price = latest['High']
            return round(prev_price, 2), round(current_price, 2), round(low_price, 2), round(high_price, 2)
        return None, None, None, None
    except Exception as e:
        print(f"실시간 금 가격 조회 실패: {e}")
        return None, None, None, None

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
    prev_price, price, low_price, high_price = get_market_data()
    if not price:
        print("금 가격을 가져오지 못했습니다.")
        return

    state = load_state()
    current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    chart_link = "[TradingView 차트 보기 (GCZ2026)](https://www.tradingview.com/chart/?symbol=GCZ2026)"

    # =========================================================================
    # [1] 기존 포지션 모니터링 (High / Low 범위 기준 검사)
    # =========================================================================
    if state:
        pos_type = state["type"]
        entry = state["entry"]
        tp1 = state["tp1"]
        tp2 = state["tp2"]
        tp3 = state["tp3"]
        sl = state["sl"]

        tp1_sent = state.get("tp1_sent", False)
        tp2_sent = state.get("tp2_sent", False)
        tp3_sent = state.get("tp3_sent", False)
        sl_sent = state.get("sl_sent", False)

        print(f"포지션 모니터링 중 [{pos_type}] | 진입가: {entry} | 현재가: {price} (Low: {low_price}, High: {high_price})")

        if pos_type == "LONG":
            # 1. 손절가 (SL) 체크
            if low_price <= sl and not sl_sent:
                send_telegram(f"🛑 **[골드 선물 손절가 도달 (SL)]**\n\n• 기준 타임프레임: `{TIMEFRAME}`\n• 진입가: `${entry:,.2f}`\n• 도달 저가: `${low_price:,.2f}`\n❌ **손절가(SL) 라인 터치. 포지션이 종료되었습니다.**\n\n🔗 {chart_link}")
                clear_state()
                return

            # 2. 3차 목표가 (TP3) 체크
            if high_price >= tp3 and not tp3_sent:
                send_telegram(f"🎯 **[골드 선물 3차 목표가 도달 (TP3)]**\n\n• 기준 타임프레임: `{TIMEFRAME}`\n• 진입가: `${entry:,.2f}`\n• 도달 고가: `${high_price:,.2f}`\n🔥 **TP3 최종 익절 달성! 포지션이 종료되었습니다.** 🚀\n\n🔗 {chart_link}")
                clear_state()
                return

            # 3. 2차 목표가 (TP2) 체크
            if high_price >= tp2 and not tp2_sent:
                send_telegram(f"🎯 **[골드 선물 2차 목표가 도달 (TP2)]**\n\n• 기준 타임프레임: `{TIMEFRAME}`\n• 진입가: `${entry:,.2f}`\n• 도달 고가: `${high_price:,.2f}`\n✨ **TP2 도달! 본절가로 스탑로스(SL) 이동 추천!**\n\n🔗 {chart_link}")
                state["tp2_sent"] = True
                save_state(state)

            # 4. 1차 목표가 (TP1) 체크
            if high_price >= tp1 and not tp1_sent:
                send_telegram(f"🎯 **[골드 선물 1차 목표가 도달 (TP1)]**\n\n• 기준 타임프레임: `{TIMEFRAME}`\n• 진입가: `${entry:,.2f}`\n• 도달 고가: `${high_price:,.2f}`\n📈 **TP1 도달! 일부 익절 구간입니다.**\n\n🔗 {chart_link}")
                state["tp1_sent"] = True
                save_state(state)

        elif pos_type == "SHORT":
            # 1. 손절가 (SL) 체크 (숏은 가격이 올라가야 손절)
            if high_price >= sl and not sl_sent:
                send_telegram(f"🛑 **[골드 선물 손절가 도달 (SL)]**\n\n• 기준 타임프레임: `{TIMEFRAME}`\n• 진입가: `${entry:,.2f}`\n• 도달 고가: `${high_price:,.2f}`\n❌ **손절가(SL) 라인 터치. 포지션이 종료되었습니다.**\n\n🔗 {chart_link}")
                clear_state()
                return

            # 2. 3차 목표가 (TP3) 체크 (숏은 가격이 내려가야 익절)
            if low_price <= tp3 and not tp3_sent:
                send_telegram(f"🎯 **[골드 선물 3차 목표가 도달 (TP3)]**\n\n• 기준 타임프레임: `{TIMEFRAME}`\n• 진입가: `${entry:,.2f}`\n• 도달 저가: `${low_price:,.2f}`\n🔥 **TP3 최종 익절 달성! 포지션이 종료되었습니다.** 🚀\n\n🔗 {chart_link}")
                clear_state()
                return

            # 3. 2차 목표가 (TP2) 체크
            if low_price <= tp2 and not tp2_sent:
                send_telegram(f"🎯 **[골드 선물 2차 목표가 도달 (TP2)]**\n\n• 기준 타임프레임: `{TIMEFRAME}`\n• 진입가: `${entry:,.2f}`\n• 도달 저가: `${low_price:,.2f}`\n✨ **TP2 도달! 본절가로 스탑로스(SL) 이동 추천!**\n\n🔗 {chart_link}")
                state["tp2_sent"] = True
                save_state(state)

            # 4. 1차 목표가 (TP1) 체크
            if low_price <= tp1 and not tp1_sent:
                send_telegram(f"🎯 **[골드 선물 1차 목표가 도달 (TP1)]**\n\n• 기준 타임프레임: `{TIMEFRAME}`\n• 진입가: `${entry:,.2f}`\n• 도달 저가: `${low_price:,.2f}`\n📈 **TP1 도달! 일부 익절 구간입니다.**\n\n🔗 {chart_link}")
                state["tp1_sent"] = True
                save_state(state)
        
        return

    # =========================================================================
    # [2] 신규 포지션 탐색 (롱/숏 방향에 따른 정확한 TP/SL 가격 산출)
    # =========================================================================
    if price >= prev_price:
        pos_type = "LONG"
        action_text = "🟢 **롱 포지션 (매수 진입)**"
        tp1 = price + 6.5
        tp2 = price + 14.0
        tp3 = price + 24.5
        sl = price - 9.0
        reason = "단기 상승 추세 및 지지선 반등 오더블록 포착"
    else:
        pos_type = "SHORT"
        action_text = "🔴 **숏 포지션 (매도 진입)**"
        tp1 = price - 6.5
        tp2 = price - 14.0
        tp3 = price - 24.5
        sl = price + 9.0
        reason = "단기 하락 추세 및 주요 저항선 매도 압력 포착"

    new_state = {
        "type": pos_type,
        "entry": price,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "sl": sl,
        "tp1_sent": False,
        "tp2_sent": False,
        "tp3_sent": False,
        "sl_sent": False
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
