import os
import requests

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

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
        # 공개된 무료 금 시세 API 혹은 환율/금속 정보 활용
        url = "https://api.gold-api.com/price/XAU"
        response = requests.get(url, timeout=5)
        data = response.json()
        return float(data.get("price", 0))
    except Exception as e:
        print(f"가격 조회 실패: {e}")
        return None

def check_gold_signal():
    price = get_gold_price()
    
    if not price:
        send_telegram("⚠️ 골드 가격 데이터를 불러오는데 실패했습니다.")
        return

    # 간단한 예시 전략 조건 (원하시는 롱/숏 조건식으로 나중에 변경 가능합니다)
    # 예: 임의의 기준으로 롱/숏 판단 시그널 생성
    signal_type = "LONG 🟢 (매수 우세)" if price % 2 == 0 else "SHORT 🔴 (매도 우세)"
    
    message = (
        f"🔔 **[GOLD] 실시간 시그널 알림**\n\n"
        f"• 현재 골드 가격: **${price:,.2f} USD**\n"
        f"• 추천 포지션: **{signal_type}**\n"
        f"• 상태: 깃허브 자동 실행 완료 ✅"
    )
    
    send_telegram(message)

if __name__ == "__main__":
    check_gold_signal()
