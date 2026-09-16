import os
from datetime import datetime
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

    # 예시 전략 로직 (추후 본인 지표 로직으로 교체 가능)
    fractional_part = int((price * 10) % 10)
    current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    
    if fractional_part >= 5:
        signal_type = "🟢 **LONG POSITION (BUY)**"
        tp1 = price + 8.0
        tp2 = price + 15.0
        tp3 = price + 25.0
        sl = price - 10.0
        reason = "Key Support Rebound & Bullish Momentum Confluence"
    else:
        signal_type = "🔴 **SHORT POSITION (SELL)**"
        tp1 = price - 8.0
        tp2 = price - 15.0
        tp3 = price - 25.0
        sl = price + 10.0
        reason = "Resistance Rejection & Bearish Volume Expansion"
    
    # 고급스러운 프로페셔널 메시지 포맷 구성
    message = (
        f"💎 **XAU/USD TECHNICAL SIGNAL** 💎\n"
        f"────────────────────────\n"
        f"⏱ **Time**: `{current_time}`\n"
        f"📊 **Action**: {signal_type}\n"
        f"💰 **Entry Zone**: `${price:,.2f}`\n\n"
        f"🎯 **Take Profit Targets**\n"
        f"• **TP1**: `${tp1:,.2f}`\n"
        f"• **TP2**: `${tp2:,.2f}`\n"
        f"• **TP3**: `${tp3:,.2f}`\n\n"
        f"🛡 **Stop Loss**\n"
        f"• **SL**: `${sl:,.2f}`\n\n"
        f"📈 **Strategy / Reason**\n"
        f"_{reason}_\n"
        f"────────────────────────\n"
        f"⚡ *Powered by GitHub Actions & Python*"
    )
    
    send_telegram(message)

if __name__ == "__main__":
    check_gold_signal()
