"""
telegram_alert_bot.py - Project Swing Trades Telegram Dispatcher
Continuously monitors shared/multi_stock_radar.json and dispatches
real-time breakout, circuit surge, and risk alerts to your phone.
"""
import json
import os
import sys
import time
import requests
from datetime import datetime

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RADAR_JSON_PATH = os.path.join(REPO_ROOT, "shared", "multi_stock_radar.json")
CONFIG_PATH = os.path.join(REPO_ROOT, "antigravity", "config", "telegram_config.json")

# State cache to avoid duplicate alert spam
last_notified_state = {}


def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def send_telegram_alert(bot_token: str, chat_id: str, message: str):
    """Dispatches formatted Markdown notification via Telegram Bot API."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        resp = requests.post(url, json=payload, timeout=5)
        return resp.status_code == 200
    except Exception as e:
        print(f"Telegram error: {e}", file=sys.stderr)
        return False


def build_alert_card(symbol: str, data: dict, alert_type: str) -> str:
    now_str = datetime.now().strftime("%H:%M:%S")
    ltp = data.get("ltp", 0.0)
    chg = data.get("day_change_pct", 0.0)
    uc = data.get("upper_circuit", 0.0)
    headroom = data.get("uc_headroom_pct", 0.0)
    vol_x = data.get("volume_multiplier", 0.0)
    vol_today = data.get("volume_today", 0)
    shares = data.get("paper_shares", 0)
    capital_allocated = int(shares * ltp) if ltp else 0

    if alert_type == "BREAKOUT_ARMED":
        header = f"🚀 *[BREAKOUT ARMED]* `{symbol}`"
        action = "🟢 *ACTION:* Pre-circuit accumulation base armed! High-momentum volume surge."
    elif alert_type == "CIRCUIT_SURGE":
        header = f"⚡ *[CIRCUIT SURGE]* `{symbol}`"
        action = "🔥 *ACTION:* Headroom burning rapidly! Approaching Upper Circuit ceiling."
    elif alert_type == "UC_LOCKED_NO_CHASE":
        header = f"🛑 *[RULE 3 LOCKOUT]* `{symbol}`"
        action = "⛔ *ACTION:* Stock locked at Upper Circuit (Offers = 0). *DO NOT CHASE.*"
    elif alert_type == "SURV_FREEZE":
        header = f"⚠️ *[RULE 6 SURVEILLANCE FREEZE]* `{symbol}`"
        action = "❄️ *ACTION:* Surveillance alert or circuit band tightening. Immediate freeze."
    else:
        header = f"📢 *[RADAR ALERT]* `{symbol}`"
        action = "ℹ️ Status update."

    card = (
        f"{header}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 *LTP:* ₹{ltp:.2f} (`{chg:+.2f}%`)\n"
        f"🎯 *Upper Circuit:* ₹{uc:.2f} (Headroom: *{headroom:+.1f}%*)\n"
        f"📊 *Volume Pace:* *{vol_x:.1f}x* normal ({vol_today:,} shares)\n"
        f"💼 *Sizing (₹40k Pool):* *{shares:,} shares* (~₹{capital_allocated:,})\n"
        f"⏰ *Time:* `{now_str} IST`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{action}"
    )
    return card


def run_telegram_bot_daemon():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting Telegram Alert Bot...")
    config = load_config()
    token = config.get("bot_token")
    chat_id = config.get("chat_id")

    if not token or not chat_id:
        print("[WARNING] Missing bot_token or chat_id in telegram_config.json")
        return

    print(f"  Connected to Telegram Chat ID: {chat_id}")

    while True:
        try:
            if os.path.exists(RADAR_JSON_PATH):
                with open(RADAR_JSON_PATH, "r", encoding="utf-8") as f:
                    radar_data = json.load(f)

                stocks = radar_data.get("stocks", {})
                for sym, info in stocks.items():
                    status = info.get("status")
                    prev_status = last_notified_state.get(sym)

                    # Trigger alert on critical status transitions
                    if status in ["BREAKOUT_ARMED", "CIRCUIT_SURGE", "UC_LOCKED_NO_CHASE", "SURV_FREEZE"] and status != prev_status:
                        card = build_alert_card(sym, info, status)
                        send_telegram_alert(token, chat_id, card)
                        last_notified_state[sym] = status
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] Telegram alert sent for {sym} [{status}]")

            time.sleep(1.0)
        except Exception as e:
            time.sleep(2.0)


if __name__ == "__main__":
    run_telegram_bot_daemon()
