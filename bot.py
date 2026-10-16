from datetime import datetime, timedelta, timezone
import json
import os
import random
import time
from pathlib import Path
from typing import Any, Dict, List
import requests
import yfinance as yf
import pandas as pd
import numpy as np

# --------------------------------------------------------------------------- #
#   1️⃣ 기본 설정 및 상수
# --------------------------------------------------------------------------- #
TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID") or "").strip()

BASE_DIR = Path.cwd()
STATE_FILE = BASE_DIR / "bot_state.json"
KST = timezone(timedelta(hours=9))

MAX_SLOTS = 8
COOLDOWN_MINUTES = 120
SL_PENALTY_HOURS = 12

# --------------------------------------------------------------------------- #
#   2️⃣ 유틸리티 및 안전한 파일 I/O (Atomic Write & Safe Load)
# --------------------------------------------------------------------------- #
class NpEncoder(json.JSONEncoder):
    """NumPy 객체 JSON 직렬화 지원"""
    def default(self, obj: Any) -> Any:
        if isinstance(obj, (np.integer, np.int64)):
            return int(obj)
        if isinstance(obj, (np.floating, np.float64)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, (np.bool_, bool)):
            return bool(obj)
        return super().default(obj)

def now() -> datetime:
    return datetime.now(KST)

def ts() -> str:
    return now().strftime("%Y-%m-%d %H:%M:%S")

def load_state() -> Dict[str, Any]:
    """상태 파일 손상 방지 및 안전한 로드"""
    default_state = {"positions": {}, "signals": {}, "sl_penalties": {}}
    if not STATE_FILE.exists():
        return default_state
    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return default_state
            data.setdefault("positions", {})
            data.setdefault("signals", {})
            data.setdefault("sl_penalties", {})
            return data
    except Exception as e:
        print(f"[{ts()}] State Load Error (Using default): {e}")
        return default_state

def save_state(state: Dict[str, Any]) -> None:
    """원자적(Atomic) 파일 저장으로 데이터 유실 방지"""
    tmp_file = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
    try:
        with tmp_file.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2, cls=NpEncoder)
        tmp_file.replace(STATE_FILE)
    except Exception as e:
        print(f"[{ts()}] State Save Error: {e}")

# --------------------------------------------------------------------------- #
#   3️⃣ 텔레그램 알림 시스템
# --------------------------------------------------------------------------- #
def send_telegram(text: str) -> bool:
    if not TOKEN or not CHAT_ID:
        print(f"[{ts()}] TG CONFIG ERROR: Token or Chat ID missing.")
        return False
    
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    
    for attempt in range(3):
        try:
            res = requests.post(url, json=payload, timeout=20)
            if res.status_code == 200:
                return True
        except Exception as e:
            print(f"[{ts()}] TG Exception (try {attempt+1}): {e}")
        time.sleep(1.5)
    return False

# --------------------------------------------------------------------------- #
#   4️⃣ 데이터 수집 및 지표 계산
# --------------------------------------------------------------------------- #
def fetch_market_data() -> List[Dict[str, Any]]:
    """Gate.io 선물 시장 데이터를 활용한 시그널 후보 수집"""
    valid_tickers = []
    url = "https://fx-api.gateio.ws/api/v4/futures/usdt/tickers"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict):
                data = data.get("data", [])
            random.shuffle(data)
            for item in data:
                contract = item.get("contract") or item.get("name")
                if not contract or item.get("in_delisting", False):
                    continue
                if "MATIC" in contract:
                    continue
                try:
                    last_price = float(item.get("last", 0))
                    change = float(item.get("change_percentage", 0))
                    if 0.000001 < last_price <= 100000.0:
                        valid_tickers.append({
                            "symbol": contract.replace("_USDT", "").replace("USDT", ""),
                            "contract": contract,
                            "price": last_price,
                            "change": change
                        })
                except Exception:
                    pass
    except Exception as e:
        print(f"[{ts()}] Error fetching market tickers: {e}")
    return valid_tickers

def get_recent_high_low(contract: str):
    """최근 15분봉 고가/저가 조회"""
    try:
        url = f"https://fx-api.gateio.ws/api/v4/futures/usdt/candlesticks?contract={contract}&interval=15m&limit=10"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            if data and isinstance(data, list):
                highs = [float(item.get("high", item.get("h", 0))) for item in data]
                lows = [float(item.get("low", item.get("l", 0))) for item in data]
                closes = [float(item.get("close", item.get("c", 0))) for item in data]
                return max(highs), min(lows), closes[-1]
    except Exception:
        pass
    return None, None, None

def fmt(x: float) -> str:
    if x >= 1000:
        return f"{x:,.2f}"
    if x >= 1:
        return f"{x:.4f}"
    if x >= 0.01:
        return f"{x:.5f}"
    return f"{x:.8f}".rstrip("0").rstrip(".")

# --------------------------------------------------------------------------- #
#   5️⃣ 포지션 및 래더 추적 엔진 (TP/SL 관리)
# --------------------------------------------------------------------------- #
def get_position_message(p: Dict[str, Any], kind: str) -> str:
    sy = p.get("symbol", "UNKNOWN")
    d = p.get("direction", "LONG")
    if kind == "TP1":
        return f"🎯 *[TP1 1차 익절 & 본전스탑]*\n🪙 종목: `{sy} / USDT` ({d})\n💰 1차 목표가 도달!\n🛡️ 손절가를 진입가로 상향 조정."
    if kind == "TP2":
        return f"🎯 *[TP2 2차 익절 도달]*\n🪙 종목: `{sy} / USDT` ({d})\n💰 2차 목표가 도달!"
    if kind == "TP3":
        return f"🎉 *[TP3 최종 익절 달성 마감!]*\n🪙 종목: `{sy} / USDT` ({d})\n🎯 3차 목표가 달성 완료!"
    return f"🛡️ *[손절(SL) 도달 마감]*\n🪙 종목: `{sy} / USDT` ({d})\n📉 손절가 이탈로 포지션 종료 (12시간 재진입 금지)"

def check_active_positions(state: Dict[str, Any]) -> None:
    for key, p in list(state["positions"].items()):
        if not isinstance(p, dict):
            state["positions"].pop(key, None)
            continue
        p.setdefault("symbol", key)
        contract = f"{key}_USDT"
        
        h, l, curr_p = get_recent_high_low(contract)
        if curr_p is None:
            continue
            
        high_p = h if h else curr_p
        low_p = l if l else curr_p
        long = p["direction"] == "LONG"

        if long:
            if high_p >= p["tp3"]:
                send_telegram(get_position_message(p, "TP3"))
                state["positions"].pop(key, None)
                continue
            if low_p <= p["sl"]:
                send_telegram(get_position_message(p, "SL"))
                state.setdefault("sl_penalties", {})[key] = time.time()
                state["positions"].pop(key, None)
                continue
            if high_p >= p["tp2"] and "TP2" not in p.get("hit", []):
                send_telegram(get_position_message(p, "TP2"))
                p.setdefault("hit", []).append("TP2")
            if not p.get("tp1_hit", False) and high_p >= p["tp1"]:
                p["tp1_hit"] = True
                p["sl"] = p["entry"]
                send_telegram(get_position_message(p, "TP1"))
        else:
            if low_p <= p["tp3"]:
                send_telegram(get_position_message(p, "TP3"))
                state["positions"].pop(key, None)
                continue
            if high_p >= p["sl"]:
                send_telegram(get_position_message(p, "SL"))
                state.setdefault("sl_penalties", {})[key] = time.time()
                state["positions"].pop(key, None)
                continue
            if low_p <= p["tp2"] and "TP2" not in p.get("hit", []):
                send_telegram(get_position_message(p, "TP2"))
                p.setdefault("hit", []).append("TP2")
            if not p.get("tp1_hit", False) and low_p <= p["tp1"]:
                p["tp1_hit"] = True
                p["sl"] = p["entry"]
                send_telegram(get_position_message(p, "TP1"))

# --------------------------------------------------------------------------- #
#   6️⃣ 메인 실행 함수
# --------------------------------------------------------------------------- #
def main():
    print(f"===================================")
    print(f" SMART SIGNAL BOT v20.2.0 RUNNING")
    print(f"===================================")
    
    state = load_state()
    check_active_positions(state)
    save_state(state)

    tickers = fetch_market_data()
    print(f"[{ts()}] Fetched Tickers:", len(tickers))
    if not tickers:
        print(f"[{ts()}] No tickers fetched.")
        return

    candidates = []
    for item in tickers:
        sym = item["symbol"]
        p = item["price"]
        ch = item["change"]
        
        direction = "LONG" if ch >= 0 else "SHORT"
        risk = max(p * 0.015, p * 0.005)

        abs_ch = abs(ch)
        lev = 3 if abs_ch > 5.0 else (5 if abs_ch > 2.5 else (7 if abs_ch > 1.0 else 10))

        if direction == "LONG":
            sl = p - risk
            tp1 = p + risk * 1.5
            tp2 = p + risk * 2.5
            tp3 = p + risk * 4.0
            pos_text = "롱 (LONG) 🟢"
        else:
            sl = p + risk
            tp1 = p - risk * 1.5
            tp2 = p - risk * 2.5
            tp3 = p - risk * 4.0
            pos_text = "숏 (SHORT) 🔴"

        candidates.append({
            "symbol": sym,
            "direction": direction,
            "price": p,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "lev": lev,
            "pos_text": pos_text,
        })

    random.shuffle(candidates)

    current_active_count = len(state["positions"])
    available_slots = MAX_SLOTS - current_active_count
    print(f"[{ts()}] ACTIVE SLOTS: {current_active_count}/{MAX_SLOTS} (Available: {available_slots})")

    sent = 0
    t = time.time()
    sl_penalties = state.get("sl_penalties", {})

    if available_slots > 0:
        for s in candidates:
            if sent >= available_slots:
                break
            key = s["symbol"]

            if key in state["positions"] or key in sl_penalties:
                if key in sl_penalties and t - float(sl_penalties[key]) < SL_PENALTY_HOURS * 3600:
                    continue

            sid = f"{key}:{s['direction']}"
            if t - float(state["signals"].get(sid, 0) or 0) < COOLDOWN_MINUTES * 60:
                continue

            current_slot_number = current_active_count + sent + 1
            msg_text = (
                f"🚨 *프로페셔널 트레이딩 시그널* 🚨\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 슬롯 번호: `[ {current_slot_number} / {MAX_SLOTS} ]`\n"
                f"🪙 종목명: `{s['symbol']} / USDT`\n"
                f"📊 포지션: **{s['pos_text']}**\n"
                f"💰 실시간 진입가: `{fmt(s['price'])}` USDT\n\n"
                f"⚙️ 운용 가이드\n"
                f"  • 권장 레버리지: `{s['lev']}x` (격리 마진)\n\n"
                f"🎯 목표가 (Take Profit)\n"
                f"  • 1차 (TP1): `{fmt(s['tp1'])}` (본전 스탑)\n"
                f"  • 2차 (TP2): `{fmt(s['tp2'])}`\n"
                f"  • 3차 (TP3): `{fmt(s['tp3'])}` (최종 익절)\n\n"
                f"🛡️ 손절가 (Stop Loss): `{fmt(s['sl'])}`\n"
                f"⏱️ 발생 시간: `{now().strftime('%Y-%m-%d %H:%M:%S')} (KST)`"
            )

            if not send_telegram(msg_text):
                continue

            state["signals"][sid] = t
            state["positions"][key] = {
                "symbol": key,
                "direction": s["direction"],
                "entry": s["price"],
                "sl": s["sl"],
                "tp1": s["tp1"],
                "tp2": s["tp2"],
                "tp3": s["tp3"],
                "tp1_hit": False,
                "hit": [],
            }
            save_state(state)
            sent += 1
            print(f"[{ts()}] SIGNAL SENT:", key)

    save_state(state)
    print(f"[{ts()}] DONE. Sent:", sent)

if __name__ == "__main__":
    main()
