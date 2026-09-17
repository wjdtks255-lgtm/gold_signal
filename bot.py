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
        data = ticker.history(period="3d", interval="15m")
        if len(data) >= 25:
            latest = data.iloc[-1]
            current_price = latest['Close']
            low_price = latest['Low']
            high_price = latest['High']
            
            # 20일 이동평균선 및 이평선 기울기(방향) 계산용 데이터
            sma20_series = data['Close'].rolling(window=20).mean()
            sma20 = sma20_series.iloc[-1]
            sma20_prev = sma20_series.iloc[-3]  # 3봉 전 이평선 (기울기 판단용)
            
            return round(current_price, 2), round(low_price, 2), round(high_price, 2), round(sma20, 2), round(sma20_prev, 2)
        return None, None, None, None, None
    except Exception as e:
        print(f"실시간 금 가격 조회 실패: {e}")
        return None, None, None, None, None

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return None

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=4)

def clear_state():
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)

def main():
    price, low_price, high_price, sma20, sma20_prev = get_market_data()
    if not price:
        print("금 가격을 가져오지 못했습니다.")
        return

    state = load_state()
    current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    chart_link = "[TradingView 차트 보기 (GCZ2026)](https://www.tradingview.com/chart/?symbol=GCZ2026)"

    # =========================================================================
    # [1] 기존 포지션 모니터링
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

        if pos_type == "LONG":
            if low_price <= sl and not sl_sent:
                send_telegram(f"🛑 **[골드 선물 손절가 도달 (SL)]**\n\n• 진입가: `${entry:,.2f}`\n• 도달 저가: `${low_price:,.2f}`\n❌ **손절가 터치로 포지션 종료.**\n\n🔗 {chart_link}")
                clear_state()
                return
            if high_price >= tp3 and not tp3_sent:
                send_telegram(f"🎯 **[골드 선물 3차 목표가 도달 (TP3)]**\n\n• 진입가: `${entry:,.2f}`\n🔥 **TP3 최종 익절 달성!** 🚀\n\n🔗 {chart_link}")
                clear_state()
                return
            if high_price >= tp2 and not tp2_sent:
                send_telegram(f"🎯 **[골드 선물 2차 목표가 도달 (TP2)]**\n\n• 진입가: `${entry:,.2f}`\n✨ **본절가로 스탑로스(SL) 이동 추천!**\n\n🔗 {chart_link}")
                state["tp2_sent"] = True
                save_state(state)
            if high_price >= tp1 and not tp1_sent:
                send_telegram(f"🎯 **[골드 선물 1차 목표가 도달 (TP1)]**\n\n• 진입가: `${entry:,.2f}`\n📈 **TP1 도달 (일부 익절)**\n\n🔗 {chart_link}")
                state["tp1_sent"] = True
                save_state(state)

        elif pos_type == "SHORT":
            if high_price >= sl and not sl_sent:
                send_telegram(f"🛑 **[골드 선물 손절가 도달 (SL)]**\n\n• 진입가: `${entry:,.2f}`\n• 도달 고가: `${high_price:,.2f}`\n❌ **손절가 터치로 포지션 종료.**\n\n🔗 {chart_link}")
                clear_state()
                return
            if low_price <= tp3 and not tp3_sent:
                send_telegram(f"🎯 **[골드 선물 3차 목표가 도달 (TP3)]**\n\n• 진입가: `${entry:,.2f}`\n🔥 **TP3 최종 익절 달성!** 🚀\n\n🔗 {chart_link}")
                clear_state()
                return
            if low_price <= tp2 and not tp2_sent:
                send_telegram(f"🎯 **[골드 선물 2차 목표가 도달 (TP2)]**\n\n• 진입가: `${entry:,.2f}`\n✨ **본절가로 스탑로스(SL) 이동 추천!**\n\n🔗 {chart_link}")
                state["tp2_sent"] = True
                save_state(state)
            if low_price <= tp1 and not tp1_sent:
                send_telegram(f"🎯 **[골드 선물 1차 목표가 도달 (TP1)]**\n\n• 진입가: `${entry:,.2f}`\n📈 **TP1 도달 (일부 익절)**\n\n🔗 {chart_link}")
                state["tp1_sent"] = True
                save_state(state)
        return

    # =========================================================================
    # [2] 신규 포지션 탐색 (이평선 '방향성'까지 완벽히 일치할 때만 진입)
    # =========================================================================
    is_sma_rising = sma20 > sma20_prev   # 이평선이 위로 향하고 있는가?
    is_sma_falling = sma20 < sma20_prev # 이평선이 아래로 향하고 있는가?

    # 롱 조건: 가격이 이평선 위이고, 이평선 자체도 우상향 중일 때만!
    if price >= sma20 and is_sma_rising:
        pos_type = "LONG"
        action_text = "🟢 **롱 포지션 (매수 진입)**"
        tp1 = price + 10.0
        tp2 = price + 20.0
        tp3 = price + 35.0
        sl = price - 8.0  # 노이즈에 안 털리도록 손절 폭 소폭 확대
        reason = "20 이평선 상단 안착 및 명확한 우상향 상승 추세 확인"
    
    # 숏 조건: 가격이 이평선 아래이고, 이평선 자체도 우하향 중일 때만!
    elif price < sma20 and is_sma_falling:
        pos_type = "SHORT"
        action_text = "🔴 **숏 포지션 (매도 진입)**"
        tp1 = price - 10.0
        tp2 = price - 20.0
        tp3 = price - 35.0
        sl = price + 8.0  # 노이즈에 안 털리도록 손절 폭 소폭 확대
        reason = "20 이평선 하단 이탈 및 명확한 우하향 하락 추세 확인"
    else:
        print("현재 시장은 추세가 모호하거나 횡보/역방향 구간입니다. 진입을 보류합니다.")
        return

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
        f"🛡 **리스크 관리 (안전 타점)**\n"
        f"• **손절가 (SL)**: `${sl:,.2f}`\n\n"
        f"📈 **시장 구조 및 진입 근거**\n"
        f"_{reason}_\n"
        f"────────────────────────\n"
        f"🔗 {chart_link}\n"
        f"⚡ *강화된 추세 필터 시스템 가동 중*"
    )
    
    send_telegram(message)

if __name__ == "__main__":
    main()
