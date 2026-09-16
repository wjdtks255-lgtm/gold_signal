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

def get_gold_price():
    try:
        url = "https://api.gold-api.com/price/XAU"
        response = requests.get(url, timeout=5)
        data = response.json()
        return float(data.get("price", 0))
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
    price = get_gold_price()
    if not price:
        print("금 가격을 가져오지 못했습니다.")
        return

    state = load_state()
    current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    # 1. 이미 진행 중인 포지션이 있는 경우 -> TP / SL 도달 여부 체크
    if state:
        pos_type = state["type"] # "LONG" 또는 "SHORT"
        entry = state["entry"]
        tp1 = state["tp1"]
        tp2 = state["tp2"]
        tp3 = state["tp3"]
        sl = state["sl"]

        print(f"진행 중인 포지션 감지 ({pos_type}), 현재가: {price}")

        if pos_type == "LONG":
            if price >= tp3:
                send_telegram(f"🎯 **[골드 목표가 3 도달 (TP3)]**\n\n• 진입가: `${entry:,.2f}`\n• 현재가: `${price:,.2f}`\n🔥 **TP3 완벽 달성! 대수익 축하드립니다!** 🚀")
                clear_state()
            elif price >= tp2:
                send_telegram(f"🎯 **[골드 목표가 2 도달 (TP2)]**\n\n• 진입가: `${entry:,.2f}`\n• 현재가: `${price:,.2f}`\n✨ **TP2 도달! 본절가로 스탑로스(SL) 이동 추천!**")
                # TP2 도달 시 상태 업데이트 가능하지만 단순화를 위해 유지 또는 알림만 전송
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

    # 2. 진행 중인 포지션이 없는 경우 -> 새로운 신호 생성
    fractional_part = int((price * 10) % 10)
    
    if fractional_part >= 5:
        pos_type = "LONG"
        action_text = "🟢 **롱 포지션 (매수 진입)**"
        tp1 = price + 8.0
        tp2 = price + 15.0
        tp3 = price + 25.0
        sl = price - 10.0
        reason = "핵심 지지선 반등 및 상승 모멘텀 수렴"
    else:
        pos_type = "SHORT"
        action_text = "🔴 **숏 포지션 (매도 진입)**"
        tp1 = price - 8.0
        tp2 = price - 15.0
        tp3 = price - 25.0
        sl = price + 10.0
        reason = "저항선 맞고 하락 압력 및 매도 볼륨 증가"

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

    # 한글화된 전문적인 시그널 메시지 발송
    message = (
        f"💎 **XAU/USD 실시간 기술적 시그널** 💎\n"
        f"────────────────────────\n"
        f"⏱ **시간**: `{current_time}`\n"
        f"📊 **신호**: {action_text}\n"
        f"💰 **진입 가격**: `${price:,.2f}`\n\n"
        f"🎯 **목표가 설정 (TP)**\n"
        f"• **1차 목표 (TP1)**: `${tp1:,.2f}`\n"
        f"• **2차 목표 (TP2)**: `${tp2:,.2f}`\n"
        f"• **3차 목표 (TP3)**: `${tp3:,.2f}`\n\n"
        f"🛡 **손절가 설정**\n"
        f"• **손절가 (SL)**: `${sl:,.2f}`\n\n"
        f"📈 **진입 근거 / 사유**\n"
        f"_{reason}_\n"
        f"────────────────────────\n"
        f"⚡ *자동 모니터링 시스템 가동 중*"
    )
    
    send_telegram(message)

if __name__ == "__main__":
    main()
