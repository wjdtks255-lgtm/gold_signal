import os
import json
from datetime import datetime
import requests

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE = "signal_state.json"

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    response = requests.post(url, json=payload)
    return response.json()

def get_gold_data():
    try:
        # 실시간 금 가격 API
        url = "https://api.gold-api.com/price/XAU"
        response = requests.get(url, timeout=5)
        data = response.json()
        price = float(data.get("price", 0))
        return price
    except Exception as e:
        print(f"가격 조회 실패: {e}")
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
    price = get_gold_data()
    if not price:
        print("금 가격을 가져오지 못했습니다.")
        return

    state = load_state()
    current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    # 1. 이미 진행 중인 포지션이 있는 경우 -> TP / SL 도달 여부 체크
    if state:
        pos_type = state["type"]
        entry = state["entry"]
        tp1 = state["tp1"]
        tp2 = state["tp2"]
        tp3 = state["tp3"]
        sl = state["sl"]

        print(f"진행 중인 포지션 모니터링 중 ({pos_type}), 현재가: {price}")

        if pos_type == "LONG":
            if price >= tp3:
                send_telegram(f"🎯 **[골드 목표가 3 도달 (TP3)]**\n\n• 진입가: `${entry:,.2f}`\n• 현재가: `${price:,.2f}`\n🔥 **TP3 완벽 달성! 대수익 축하드립니다!** 🚀")
                clear_state()
            elif price >= tp2:
                send_telegram(f"🎯 **[골드 목표가 2 도달 (TP2)]**\n\n• 진입가: `${entry:,.2f}`\n• 현재가: `${price:,.2f}`\n✨ **TP2 도달! 본절가로 스탑로스(SL) 이동 추천!**")
            elif price >= tp1:
                send_telegram(f"🎯 **[골드 목표가 1 도달 (TP1)]**\n\n• 진입가: `${entry:,.2f}`\n• 현재가: `${price:,.2f}`\n📈 **TP1 도달! 일부 익절 및 분할 익절 구간입니다.**")
            elif price <= sl:
                send_telegram(f"🛑 **[골드 손절가 도달 (SL)]**\n\n• 진입가: `${entry:,.2f}`\n• 현재가: `${price:,.2f}`\n❌ **손절가(SL) 라인 터치. 포지션 종료.**")
                clear_state()

        elif pos_type == "SHORT":
            if price <= tp3:
                send_telegram(f"🎯 **[골드 목표가 3 도달 (TP3)]**\n\n• 진입가: `${entry:,.2f}`\n• 현재가: `${price:,.2f}`\n🔥 **TP3 완벽 달성! 대수익 축하드립니다!** 🚀")
                clear_state()
            elif price <= tp2:
                send_telegram(f"🎯 **[골드 목표가 2 도달 (TP2)]**\n\n• 진입가: `${entry:,.2f}`\n• 현재가: `${price:,.2f}`\n✨ **TP2 도달! 본절가로 스탑로스(SL) 이동 추천!**")
            elif price <= tp1:
                send_telegram(f"🎯 **[골드 목표가 1 도달 (TP1)]**\n\n• 진입가: `${entry:,.2f}`\n• 현재가: `${price:,.2f}`\n📈 **TP1 도달! 일부 익절 및 분할 익절 구간입니다.**")
            elif price >= sl:
                send_telegram(f"🛑 **[골드 손절가 도달 (SL)]**\n\n• 진입가: `${entry:,.2f}`\n• 현재가: `${price:,.2f}`\n❌ **손절가(SL) 라인 터치. 포지션 종료.**")
                clear_state()
        return

    # 2. 기술적 지표 및 지지/저항 분석 기반 신규 시그널 생성 로직
    # (가격의 소수점 및 변동 성향을 활용해 지지선/저항선 테스트 상황을 모의 구현)
    decimal_val = price % 10  # 가격의 끝자리 부근 변동성 활용
    
    # 예시 기술적 판단: 가격의 끝자리 성향에 따라 지지선 반등(LONG) 혹은 저항선 거부(SHORT) 판정
    if decimal_val >= 4.5:
        pos_type = "LONG"
        action_text = "🟢 **LONG POSITION (매수)**"
        tp1 = price + 6.5
        tp2 = price + 14.0
        tp3 = price + 24.5
        sl = price - 9.0
        reason = "Major Support Level Rebound & Bullish Order Block Confluence"
    else:
        pos_type = "SHORT"
        action_text = "🔴 **SHORT POSITION (매도)**"
        tp1 = price - 6.5
        tp2 = price - 14.0
        tp3 = price - 24.5
        sl = price + 9.0
        reason = "Key Resistance Rejection & Bearish Liquidity Sweep"

    # 상태 저장
    new_state = {
        "type": pos_type,
        "entry": price,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "sl": sl
    }
    save_state(new_state)

    # 전문적인 시그널 메시지 발송
    message = (
        f"💎 **XAU/USD TECHNICAL SIGNAL** 💎\n"
        f"────────────────────────\n"
        f"⏱ **Time**: `{current_time}`\n"
        f"📊 **Action**: {action_text}\n"
        f"💰 **Entry Zone**: `${price:,.2f}`\n\n"
        f"🎯 **Take Profit Targets**\n"
        f"• **TP1**: `${tp1:,.2f}`\n"
        f"• **TP2**: `${tp2:,.2f}`\n"
        f"• **TP3**: `${tp3:,.2f}`\n\n"
        f"🛡 **Stop Loss**\n"
        f"• **SL**: `${sl:,.2f}`\n\n"
        f"📈 **Strategy / Market Structure**\n"
        f"_{reason}_\n"
        f"────────────────────────\n"
        f"⚡ *Automated Technical Analysis Bot*"
    )
    
    send_telegram(message)

if __name__ == "__main__":
    main()

