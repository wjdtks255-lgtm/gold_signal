import os
import requests

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def check_gold_signal():
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": "🔍 디버깅 테스트 메시지입니다.",
        "parse_mode": "Markdown"
    }
    
    response = requests.post(url, json=payload)
    
    # 텔레그램 서버가 반환한 응답 결과를 깃허브 로그에 강제로 출력합니다.
    print("--- 텔레그램 응답 결과 ---")
    print("Status Code:", response.status_code)
    print("Response Text:", response.text)

if __name__ == "__main__":
    check_gold_signal()
