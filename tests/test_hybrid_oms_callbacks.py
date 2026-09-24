"""
test_hybrid_oms_callbacks.py - Tests for Telegram Interactive Callbacks & Commands
==================================================================================
Part of Project Swing Trades (ARGUS 8i // BEACON Track 2).
"""

import json
from unittest.mock import MagicMock, patch
import pytest

from antigravity.models.execution_policy import ExecutionMode, IntentStatus
from antigravity.daemons.hybrid_execution_oms import HybridExecutionOMS
from antigravity.daemons.telegram_alert_bot import (
    build_copilot_intent_card,
    process_telegram_updates,
)


def test_build_copilot_intent_card_contains_inline_buttons():
    intent_data = {
        "intent_id": "INTENT_RVNL_A1B2C3D4",
        "symbol": "RVNL",
        "entry_price": 215.0,
        "stop_loss": 207.5,
        "target_tranche1": 226.25,
        "runner_tranche2": 237.5,
        "shares": 200,
        "tranche1_shares": 100,
        "tranche2_shares": 100,
        "risk_rs": 1500.0,
        "notional_rs": 43000.0,
        "volume_multiplier": 3.8,
        "seconds_remaining": 88,
    }
    card, markup = build_copilot_intent_card(intent_data)

    assert "CO-PILOT AUTHORIZATION REQUIRED" in card
    assert "RVNL" in card
    assert "₹215.00" in card
    assert "₹207.50" in card
    assert "inline_keyboard" in markup

    buttons = markup["inline_keyboard"]
    assert len(buttons) == 2
    # Row 1: Approve and Reject
    assert buttons[0][0]["text"] == "✅ APPROVE & EXECUTE"
    assert buttons[0][0]["callback_data"] == "APPROVE:INTENT_RVNL_A1B2C3D4"
    assert buttons[0][1]["text"] == "❌ REJECT / PASS"
    assert buttons[0][1]["callback_data"] == "REJECT:INTENT_RVNL_A1B2C3D4"
    # Row 2: Emergency Kill
    assert buttons[1][0]["text"] == "🚨 EMERGENCY KILL-SWITCH"
    assert buttons[1][0]["callback_data"] == "KILL_ALL"


@patch("antigravity.daemons.telegram_alert_bot.requests.post")
@patch("antigravity.daemons.telegram_alert_bot.requests.get")
def test_telegram_callback_approve_flow(mock_get, mock_post, tmp_path):
    oms = HybridExecutionOMS(output_dir=tmp_path)
    oms.set_mode(ExecutionMode.CO_PILOT)

    # Provide valid live market depth for RVNL to satisfy fail-closed feed check (Codex R01)
    (tmp_path / "live_depth_track2.json").write_text(
        json.dumps({"watchlist": [{"symbol": "RVNL", "ltp": 215.05}]}),
        encoding="utf-8",
    )

    candidate = {
        "symbol": "RVNL",
        "entry_price": 215.0,
        "stop_loss": 207.5,
        "volume_multiplier": 3.8,
        "var_elm_rate": 0.20,
    }
    intent, _ = oms.submit_candidate(candidate)
    assert intent is not None
    assert intent.status == IntentStatus.PENDING_APPROVAL

    # Mock response for getUpdates
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {
        "result": [
            {
                "update_id": 1001,
                "callback_query": {
                    "id": "cb_999",
                    "from": {"id": 6022010720, "first_name": "Yashu"},
                    "data": f"APPROVE:{intent.intent_id}",
                    "message": {"message_id": 555},
                },
            }
        ]
    }
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {"ok": True}

    last_id = process_telegram_updates(
        bot_token="test_token",
        authorized_chat_id="6022010720",
        oms=oms,
        last_update_id=0,
    )
    assert last_id == 1001
    assert intent.status == IntentStatus.ROUTED
    assert intent.resolved_by == "TELEGRAM_YASHU"


@patch("antigravity.daemons.telegram_alert_bot.requests.post")
@patch("antigravity.daemons.telegram_alert_bot.requests.get")
def test_telegram_mode_switch_commands(mock_get, mock_post, tmp_path):
    oms = HybridExecutionOMS(output_dir=tmp_path)
    oms.set_mode(ExecutionMode.CO_PILOT)
    assert oms.config.mode == ExecutionMode.CO_PILOT

    # Mock response for /mode auto command
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {
        "result": [
            {
                "update_id": 2001,
                "message": {
                    "chat": {"id": 6022010720},
                    "text": "/mode auto",
                },
            }
        ]
    }
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {"ok": True}

    last_id = process_telegram_updates(
        bot_token="test_token",
        authorized_chat_id="6022010720",
        oms=oms,
        last_update_id=0,
    )
    assert last_id == 2001
    assert oms.config.mode == ExecutionMode.AUTONOMOUS
