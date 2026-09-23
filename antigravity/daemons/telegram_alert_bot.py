"""
telegram_alert_bot.py - Project Swing Trades Interactive Telegram Dispatcher & Co-Pilot
=======================================================================================
Part of Project Swing Trades (ARGUS 8i // BEACON Track 2).

Dispatches real-time alerts, breakout signals, and interactive Co-Pilot trade cards.
Supports two-way interactive callbacks via Telegram inline keyboards:
  - [ APPROVE & EXECUTE ]: 1-click trade authorization from phone.
  - [ REJECT / PASS ]: Discards candidate signal.
  - [ EMERGENCY KILL ]: Triggers immediate portfolio kill-switch.
  - Interactive commands: /status, /mode copilot, /mode auto, /mode hybrid, /kill.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from antigravity.models.execution_policy import ExecutionMode, IntentStatus
from antigravity.daemons.hybrid_execution_oms import HybridExecutionOMS

RADAR_JSON_PATH = REPO_ROOT / "shared" / "multi_stock_radar.json"
TRACK2_STATUS_PATH = REPO_ROOT / "shared" / "track2_liquid" / "paper_desk_status.json"
INTENTS_PATH = REPO_ROOT / "shared" / "track2_liquid" / "execution_intents.json"
CONFIG_PATH = REPO_ROOT / "antigravity" / "config" / "telegram_config.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [TelegramBot] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("TelegramBot")

# State cache to avoid duplicate alert spam
last_notified_state: Dict[str, str] = {}
last_notified_signals: Set[str] = set()
intent_message_ids: Dict[str, int] = {}  # intent_id -> telegram message_id


def load_config() -> Dict[str, Any]:
    """Loads telegram configuration."""
    if CONFIG_PATH.is_file():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def send_telegram_alert(
    bot_token: str,
    chat_id: str,
    message: str,
    reply_markup: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """Dispatches formatted Markdown notification, optionally with inline buttons. Returns message_id."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        resp = requests.post(url, json=payload, timeout=8)
        if resp.status_code == 200:
            res_json = resp.json()
            return res_json.get("result", {}).get("message_id")
        else:
            logger.error(f"Telegram error {resp.status_code}: {resp.text}")
    except Exception as e:
        logger.error(f"Telegram send error: {e}")
    return None


def edit_telegram_message(
    bot_token: str,
    chat_id: str,
    message_id: int,
    new_text: str,
    reply_markup: Optional[Dict[str, Any]] = None,
) -> bool:
    """Edits an existing Telegram message in-place to update status or remove buttons."""
    url = f"https://api.telegram.org/bot{bot_token}/editMessageText"
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": new_text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    try:
        resp = requests.post(url, json=payload, timeout=8)
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Telegram edit error: {e}")
        return False


def answer_callback_query(bot_token: str, callback_query_id: str, text: Optional[str] = None) -> bool:
    """Acknowledges an inline button tap to dismiss Telegram's loading spinner."""
    url = f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery"
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    try:
        resp = requests.post(url, json=payload, timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def build_copilot_intent_card(intent: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Builds interactive Co-Pilot card with Approve / Reject buttons."""
    now_str = datetime.now().strftime("%H:%M:%S")
    sym = intent.get("symbol", "UNKNOWN")
    entry = float(intent.get("entry_price", 0.0))
    stop = float(intent.get("stop_loss", 0.0))
    t1 = float(intent.get("target_tranche1", 0.0))
    t2 = float(intent.get("runner_tranche2", 0.0))
    shares = int(intent.get("shares", 0))
    t1_shares = int(intent.get("tranche1_shares", shares // 2))
    t2_shares = int(intent.get("tranche2_shares", shares - t1_shares))
    risk_rs = float(intent.get("risk_rs", 1500.0))
    notional = float(intent.get("notional_rs", shares * entry))
    vol_x = float(intent.get("volume_multiplier", 1.0))
    intent_id = intent.get("intent_id", "")
    rem_sec = int(intent.get("seconds_remaining", 90))

    card = (
        f"🎯 *[ARGUS 8i // CO-PILOT AUTHORIZATION REQUIRED]* `{sym}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🟢 *Signal:* BUY BREAKOUT TRIGGERED\n"
        f"💰 *Entry Price:* ₹{entry:.2f}\n"
        f"🛑 *Stop Loss:* ₹{stop:.2f} (Risk: *₹{risk_rs:,.0f}* / 1.0R)\n"
        f"🎯 *Tranche 1 (+1.5R):* ₹{t1:.2f} ({t1_shares:,} shares)\n"
        f"🏃 *Tranche 2 (Runner):* ₹{t2:.2f} ({t2_shares:,} shares, Trailing BE)\n"
        f"📦 *Position Size:* *{shares:,} shares* (~₹{notional:,.0f})\n"
        f"📊 *Volume Pace:* *{vol_x:.1f}x* normal\n"
        f"⏰ *Window:* *{rem_sec}s remaining* (Expires fail-closed)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 *Tap an action below to authorize:* "
    )

    markup = {
        "inline_keyboard": [
            [
                {"text": "✅ APPROVE & EXECUTE", "callback_data": f"APPROVE:{intent_id}"},
                {"text": "❌ REJECT / PASS", "callback_data": f"REJECT:{intent_id}"},
            ],
            [
                {"text": "🚨 EMERGENCY KILL-SWITCH", "callback_data": "KILL_ALL"},
            ],
        ]
    }
    return card, markup


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

    return (
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


def build_track2_signal_card(signal: dict) -> str:
    now_str = datetime.now().strftime("%H:%M:%S")
    sym = signal.get("symbol", "UNKNOWN")
    entry = signal.get("entry_price", 0.0)
    stop = signal.get("stop_loss", 0.0)
    target = signal.get("target_price", 0.0)
    shares = signal.get("shares", 0)
    risk_rs = signal.get("actual_risk_rs", 1500.0)
    notional = signal.get("notional_value_rs", shares * entry)
    vol_mult = signal.get("volume_multiple", 0.0)

    return (
        f"🎯 *[ARGUS 8i // BEACON ORB SIGNAL]* `{sym}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🟢 *Action:* BUY BREAKOUT CONFIRMED\n"
        f"💰 *Entry Price:* ₹{entry:.2f}\n"
        f"🛑 *Stop Loss:* ₹{stop:.2f} (Risk: ₹{risk_rs:,.0f})\n"
        f"🎯 *Target (2R):* ₹{target:.2f}\n"
        f"📊 *Volume Multiple:* *{vol_mult:.2f}x* (Threshold: ≥2.5x)\n"
        f"📦 *Position Size:* *{shares:,} shares* (~₹{notional:,.0f})\n"
        f"⏰ *Time:* `{now_str} IST`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔒 Managed by SPLITLOCK (+1.5R Tranche 1 / BE Runner)"
    )


def process_telegram_updates(
    bot_token: str,
    authorized_chat_id: str,
    oms: HybridExecutionOMS,
    last_update_id: int,
) -> int:
    """Polls and executes Telegram callback queries and text commands."""
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    params = {"offset": last_update_id + 1, "timeout": 2}

    try:
        resp = requests.get(url, params=params, timeout=5)
        if resp.status_code != 200:
            return last_update_id

        updates = resp.json().get("result", [])
        for upd in updates:
            upd_id = upd.get("update_id", last_update_id)
            last_update_id = max(last_update_id, upd_id)

            # 1. Handle Callback Query (Button clicks)
            cb = upd.get("callback_query")
            if cb:
                cb_id = cb.get("id")
                from_user = cb.get("from", {})
                chat_id_from = str(from_user.get("id", ""))
                sender_name = from_user.get("first_name", "Trader")
                data = str(cb.get("data", ""))
                msg = cb.get("message", {})
                msg_id = msg.get("message_id")

                # Verify sender is authorized chat ID
                if chat_id_from != authorized_chat_id:
                    answer_callback_query(bot_token, cb_id, text="Unauthorized.")
                    continue

                if data.startswith("APPROVE:"):
                    intent_id = data.split(":", 1)[1]
                    res = oms.approve_intent(intent_id, approver=f"TELEGRAM_{sender_name.upper()}")
                    if res.get("status") == "SUCCESS":
                        answer_callback_query(bot_token, cb_id, text="Trade Approved & Routed!")
                        now_str = datetime.now().strftime("%H:%M:%S")
                        updated_text = (
                            f"✅ *[TRADE APPROVED & ROUTED]*\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"👤 *Authorized by:* {sender_name}\n"
                            f"🎫 *Order ID:* `{res.get('order_id')}`\n"
                            f"💰 *Entry:* {res.get('shares')} shares @ ₹{res.get('entry_price')}\n"
                            f"🛑 *Stop-Loss:* ₹{res.get('stop_loss')} | *Target 1:* ₹{res.get('target_tranche1')}\n"
                            f"⏰ *Executed:* `{now_str} IST`\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"🌐 Managed live by Hybrid OMS"
                        )
                        if msg_id:
                            edit_telegram_message(bot_token, authorized_chat_id, msg_id, updated_text, reply_markup={"inline_keyboard": []})
                    else:
                        answer_callback_query(bot_token, cb_id, text=f"Error: {res.get('message')}")

                elif data.startswith("REJECT:"):
                    intent_id = data.split(":", 1)[1]
                    res = oms.reject_intent(intent_id, reason=f"REJECTED_BY_{sender_name.upper()}")
                    answer_callback_query(bot_token, cb_id, text="Trade Rejected.")
                    now_str = datetime.now().strftime("%H:%M:%S")
                    updated_text = (
                        f"❌ *[TRADE REJECTED BY TRADER]*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"👤 *Rejected by:* {sender_name}\n"
                        f"⏰ *Time:* `{now_str} IST`\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"Candidate discarded. Capital preserved."
                    )
                    if msg_id:
                        edit_telegram_message(bot_token, authorized_chat_id, msg_id, updated_text, reply_markup={"inline_keyboard": []})

                elif data == "KILL_ALL":
                    kill_res = oms.emergency_flatten_all(reason=f"TELEGRAM_KILL_SWITCH_BY_{sender_name.upper()}")
                    answer_callback_query(bot_token, cb_id, text="🚨 KILL SWITCH ACTIVATED!")
                    send_telegram_alert(
                        bot_token,
                        authorized_chat_id,
                        f"🚨 *[EMERGENCY KILL-SWITCH EXECUTED]*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"Cancelled {kill_res.get('cancelled_intents')} intents.\n"
                        f"Squared off {kill_res.get('squared_off_orders')} active orders.\n"
                        f"Risk reduced to ₹0.00."
                    )

            # 2. Handle Text Commands
            msg_obj = upd.get("message")
            if msg_obj:
                chat_id_from = str(msg_obj.get("chat", {}).get("id", ""))
                text = str(msg_obj.get("text", "")).strip().lower()

                if chat_id_from == authorized_chat_id:
                    if text == "/status":
                        summary = oms.get_status_summary()
                        send_telegram_alert(
                            bot_token,
                            authorized_chat_id,
                            f"📊 *[ARGUS 8i // DESK STATUS]*\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"⚙️ *Mode:* `{summary['mode']}`\n"
                            f"🛡️ *Environment:* `{summary['environment']}`\n"
                            f"⏳ *Pending Co-Pilot:* *{summary['pending_co_pilot_count']}*\n"
                            f"💼 *Open Positions:* *{summary['active_positions_count']} / {summary['max_positions']}*\n"
                            f"🔥 *Open Risk:* ₹{summary['open_risk_rs']:,.0f}\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"🌐 Terminal: http://127.0.0.1:8767/"
                        )
                    elif text in ("/mode copilot", "/mode co-pilot"):
                        oms.set_mode(ExecutionMode.CO_PILOT)
                        send_telegram_alert(bot_token, authorized_chat_id, "⚙️ Operational mode set to: *CO_PILOT* (1-Click Approval Required)")
                    elif text in ("/mode auto", "/mode autonomous"):
                        oms.set_mode(ExecutionMode.AUTONOMOUS)
                        send_telegram_alert(bot_token, authorized_chat_id, "⚙️ Operational mode set to: *AUTONOMOUS* (Machine Speed <15ms)")
                    elif text in ("/mode hybrid", "/mode hyb"):
                        oms.set_mode(ExecutionMode.HYBRID)
                        send_telegram_alert(bot_token, authorized_chat_id, "⚙️ Operational mode set to: *HYBRID* (Tier 1 Auto / Tier 2 Co-Pilot)")
                    elif text == "/kill":
                        kill_res = oms.emergency_flatten_all(reason="TELEGRAM_COMMAND_KILL")
                        send_telegram_alert(
                            bot_token,
                            authorized_chat_id,
                            f"🚨 *[EMERGENCY KILL EXECUTED]*\n"
                            f"Cancelled: {kill_res.get('cancelled_intents')}, Squared off: {kill_res.get('squared_off_orders')}"
                        )

    except Exception as exc:
        logger.error(f"Telegram polling error: {exc}")

    return last_update_id


def run_telegram_bot_daemon():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting Interactive Telegram Co-Pilot Bot...")
    config = load_config()
    token = config.get("bot_token")
    chat_id = str(config.get("chat_id", ""))

    if not token or not chat_id:
        print("[WARNING] Missing bot_token or chat_id in telegram_config.json")
        return

    print(f"  Connected to Telegram Chat ID: {chat_id}")
    oms = HybridExecutionOMS()
    last_update_id = 0

    while True:
        try:
            # 1. Process incoming Telegram button taps & text commands
            last_update_id = process_telegram_updates(token, chat_id, oms, last_update_id)

            # 2. Sweep expired intents past 90s
            oms.sweep_expired_intents()

            # 3. Check for new PENDING_APPROVAL intents to send Co-Pilot cards
            if INTENTS_PATH.is_file():
                try:
                    intents_data = json.loads(INTENTS_PATH.read_text(encoding="utf-8"))
                    for intent in intents_data.get("intents", []):
                        i_id = intent.get("intent_id")
                        status = intent.get("status")

                        # If pending and not yet notified
                        if status == "PENDING_APPROVAL" and i_id not in intent_message_ids:
                            card, markup = build_copilot_intent_card(intent)
                            msg_id = send_telegram_alert(token, chat_id, card, reply_markup=markup)
                            if msg_id:
                                intent_message_ids[i_id] = msg_id
                                logger.info(f"Dispatched Co-Pilot card for {intent.get('symbol')} (msg_id={msg_id})")

                        # If intent was expired by sweeper, update message to remove buttons
                        elif status == "EXPIRED" and i_id in intent_message_ids:
                            msg_id = intent_message_ids.pop(i_id)
                            sym = intent.get("symbol")
                            expired_text = (
                                f"⏱️ *[INTENT EXPIRED FAIL-CLOSED]* `{sym}`\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"90-second decision countdown elapsed without action.\n"
                                f"Order cancelled to prevent late entry / chasing.\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"Capital preserved."
                            )
                            edit_telegram_message(token, chat_id, msg_id, expired_text, reply_markup={"inline_keyboard": []})
                except Exception as e:
                    logger.debug(f"Error checking intents: {e}")

            # 4. Track 1 Radar Monitoring
            if RADAR_JSON_PATH.is_file():
                with open(RADAR_JSON_PATH, "r", encoding="utf-8") as f:
                    radar_data = json.load(f)

                stocks = radar_data.get("stocks", {})
                for sym, info in stocks.items():
                    status = info.get("status")
                    prev_status = last_notified_state.get(sym)

                    if status in ["BREAKOUT_ARMED", "CIRCUIT_SURGE", "UC_LOCKED_NO_CHASE", "SURV_FREEZE"] and status != prev_status:
                        card = build_alert_card(sym, info, status)
                        send_telegram_alert(token, chat_id, card)
                        last_notified_state[sym] = status

            # 5. Track 2 BEACON Signal Monitoring
            if TRACK2_STATUS_PATH.is_file():
                with open(TRACK2_STATUS_PATH, "r", encoding="utf-8") as f:
                    t2_data = json.load(f)

                signals = t2_data.get("paper_signals", [])
                for sig in signals:
                    sig_id = f"{sig.get('symbol')}_{sig.get('signal_timestamp', '')[:16]}"
                    if sig_id not in last_notified_signals:
                        card = build_track2_signal_card(sig)
                        send_telegram_alert(token, chat_id, card)
                        last_notified_signals.add(sig_id)

            time.sleep(1.0)
        except Exception as e:
            time.sleep(2.0)


if __name__ == "__main__":
    run_telegram_bot_daemon()
