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

def check_gold_signal():
    # 나중에 여기에 골드 가격 분석 로직을 넣을 수 있습니다.
    signal_type = "LONG 🟢"  # 예시: 롱 시그널
    price = "2,350.50"       # 예시 가격
    
    message = (
        f"🔔 **골드(Gold) 자동 시그널 알림**\n\n"
        f"• 포지션: **{signal_type}**\n"
        f"• 기준 가격: **${price}**\n"
        f"• 상태: 깃허브 액션 정상 작동 중!"
    )
    
    send_telegram(message)

if __name__ == "__main__":
    check_gold_signal()

