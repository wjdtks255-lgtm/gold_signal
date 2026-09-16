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
        # 국제 금(XAU) 또는 골드 선물 가격 API
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

    # TODO: 본인만의 매매 로직(이동평균선, RSI 등)을 여기에 구현하시면 됩니다.
    # 예시: 가격의 소수점 첫째 자리나 변동폭을 이용한 임시 로직
    # 여기서는 예시로 가격의 소수점 첫째 자리가 5 이상이면 LONG, 미만이면 SHORT으로 지정해 봅니다.
    fractional_part = int((price * 10) % 10)
    signal_type = "LONG 🟢 (매수 우세)" if fractional_part >= 5 else "SHORT 🔴 (매도 우세)"
    
    message = (
        f"🥇 **[골드 선물 시그널 알림]**\n\n"
        f"• 현재 골드 가격: **${price:,.2f} USD**\n"
        f"• 추천 포지션: **{signal_type}**\n"
        f"• 상태: 깃허브 액션 정상 작동 중 ✅"
    )
    
    send_telegram(message)

if __name__ == "__main__":
    check_gold_signal()
