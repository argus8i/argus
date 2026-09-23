"""
test_hybrid_execution_policy.py - Tests for Hardened Hybrid Execution OMS & Policies
=====================================================================================
Part of Project Swing Trades (ARGUS 8i // BEACON Track 2).

Verifies Tri-Agent consensus hardening:
- 30s fail-closed expiry countdown
- Hard Limit Collar (+15 bps max slippage) & Pre-Routing Slippage Guard
- Thread-safe RLock and atomic Compare-And-Swap (CAS) state validation
- Monotonic 300s deduplication cache
- Statutory friction and pegged limit order attributes
"""

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

from antigravity.models.execution_policy import (
    ExecutionEnvironment,
    ExecutionIntent,
    ExecutionMode,
    IntentStatus,
    PolicyConfig,
    SecurityViolationError,
)
from antigravity.daemons.hybrid_execution_oms import HybridExecutionOMS


def test_execution_intent_sizing_and_tranches():
    candidate = {
        "symbol": "RVNL",
        "entry_price": 215.0,
        "stop_loss": 207.5,      # risk per share = 7.50
        "target_price": 226.25,   # +1.5R target = 215 + 1.5*7.5 = 226.25
        "volume_multiplier": 3.8,
        "nifty_breadth_confirmed": False,
        "atr14": 8.0,
    }
    # Expected shares = 1500 / 7.50 = 200 shares
    intent = ExecutionIntent.create_from_candidate(candidate, mode=ExecutionMode.CO_PILOT)

    assert intent.symbol == "RVNL"
    assert intent.shares == 200
    assert intent.tranche1_shares == 100
    assert intent.tranche2_shares == 100
    assert intent.risk_rs == 1500.0
    assert intent.notional_rs == 43000.0
    assert intent.target_tranche1 == 226.25
    assert intent.runner_tranche2 == 237.50  # +3.0R
    assert intent.status == IntentStatus.PENDING_APPROVAL
    assert intent.conviction_tier == 2

    # Limit collar: min(215 * 1.0015, 215 + 0.10 * 8.0) = min(215.32, 215.80) = 215.32
    assert intent.limit_price == 215.32
    assert intent.max_slippage_bps == 15.0


def test_hybrid_mode_tier1_auto_vs_tier2_copilot():
    # Tier 1 setup (volume >= 4.0x and breadth confirmed) -> Should auto-approve
    tier1_candidate = {
        "symbol": "CDSL",
        "entry_price": 1400.0,
        "stop_loss": 1370.0,
        "volume_multiplier": 4.5,
        "nifty_breadth_confirmed": True,
    }
    tier1_intent = ExecutionIntent.create_from_candidate(tier1_candidate, mode=ExecutionMode.HYBRID)
    assert tier1_intent.conviction_tier == 1
    assert tier1_intent.status == IntentStatus.APPROVED
    assert tier1_intent.resolved_by == "HYBRID_TIER1_AUTO"

    # Tier 2 setup (volume < 4.0x or breadth not confirmed) -> Should prompt Co-Pilot
    tier2_candidate = {
        "symbol": "SUZLON",
        "entry_price": 42.0,
        "stop_loss": 40.5,
        "volume_multiplier": 3.6,
        "nifty_breadth_confirmed": False,
    }
    tier2_intent = ExecutionIntent.create_from_candidate(tier2_candidate, mode=ExecutionMode.HYBRID)
    assert tier2_intent.conviction_tier == 2
    assert tier2_intent.status == IntentStatus.PENDING_APPROVAL


def test_30_second_expiry_sweeper(tmp_path):
    oms = HybridExecutionOMS(output_dir=tmp_path)
    oms.set_mode(ExecutionMode.CO_PILOT)

    candidate = {
        "symbol": "ANGELONE",
        "entry_price": 300.0,
        "stop_loss": 290.0,
        "volume_multiplier": 3.7,
    }
    intent, msg = oms.submit_candidate(candidate)
    assert intent is not None
    assert intent.status == IntentStatus.PENDING_APPROVAL
    assert intent.is_expired() is False

    # Verify default expiry is ~30 seconds, not 90s
    created_dt = datetime.fromisoformat(intent.created_at)
    exp_dt = datetime.fromisoformat(intent.expires_at)
    assert round((exp_dt - created_dt).total_seconds()) == 30

    # Simulate clock advancing 35 seconds past expiry
    simulated_future = datetime.now(timezone.utc) + timedelta(seconds=35)
    assert intent.is_expired(simulated_future) is True

    # Manually backdate created_at and expires_at to test sweep_expired_intents
    intent.expires_at = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    oms._save_intents()

    swept = oms.sweep_expired_intents()
    assert swept == 1
    assert intent.status == IntentStatus.EXPIRED
    assert intent.resolved_by == "EXPIRY_SWEEPER"
    assert "30s countdown elapsed" in intent.resolution_notes


def test_copilot_approve_and_reject_flow(tmp_path):
    oms = HybridExecutionOMS(output_dir=tmp_path)
    oms.set_mode(ExecutionMode.CO_PILOT)

    candidate = {
        "symbol": "BDL",
        "entry_price": 1200.0,
        "stop_loss": 1170.0,
        "volume_multiplier": 3.8,
    }
    intent, _ = oms.submit_candidate(candidate)
    assert intent is not None
    assert intent.status == IntentStatus.PENDING_APPROVAL

    # Test Approval
    res = oms.approve_intent(intent.intent_id, approver="TELEGRAM_YASHU")
    assert res["status"] == "SUCCESS"
    assert intent.status == IntentStatus.ROUTED
    assert intent.resolved_by == "TELEGRAM_YASHU"
    assert len(oms.active_orders) == 1
    assert oms.active_orders[0]["symbol"] == "BDL"
    assert oms.active_orders[0]["order_type"] == "PEGGED_LIMIT"
    assert oms.active_orders[0]["statutory_friction_est_rs"] in (151.61, 167.54)

    # Test Rejection on second candidate
    cand2 = {
        "symbol": "IREDA",
        "entry_price": 110.0,
        "stop_loss": 105.0,
        "volume_multiplier": 3.5,
    }
    intent2, _ = oms.submit_candidate(cand2)
    assert intent2 is not None
    rej_res = oms.reject_intent(intent2.intent_id, reason="REJECTED_BY_TRADER")
    assert rej_res["status"] == "OK"
    assert intent2.status == IntentStatus.REJECTED


def test_adverse_selection_limit_collar_abort(tmp_path):
    """
    Claude Hardening Condition 1:
    If market price has extended beyond the +0.15% limit collar, routing must abort fail-closed.
    """
    oms = HybridExecutionOMS(output_dir=tmp_path)
    oms.set_mode(ExecutionMode.CO_PILOT)

    candidate = {
        "symbol": "RVNL",
        "entry_price": 200.0,
        "stop_loss": 195.0,
        "volume_multiplier": 3.5,
    }
    intent, _ = oms.submit_candidate(candidate)
    assert intent is not None
    # Limit collar is 200.0 * 1.0015 = 200.30
    assert intent.limit_price == 200.30

    # Simulate price extending to 201.20 (+0.60%) during the trader delay
    res = oms.approve_intent(intent.intent_id, approver="USER", current_ltp=201.20)

    assert res["status"] == "ERROR"
    assert res["reason"] == "SLIPPAGE_TOLERANCE_EXCEEDED"
    assert "exceeds limit collar" in res["message"]
    assert intent.status == IntentStatus.REJECTED
    assert intent.resolved_by == "SLIPPAGE_GUARD"
    assert len(oms.active_orders) == 0  # Zero order placed


def test_false_breakout_retracement_abort(tmp_path):
    """
    Claude Hardening Condition 1:
    If breakout failed and market price retraced below entry trigger, routing must abort fail-closed.
    """
    oms = HybridExecutionOMS(output_dir=tmp_path)
    oms.set_mode(ExecutionMode.CO_PILOT)

    candidate = {
        "symbol": "SUZLON",
        "entry_price": 50.0,
        "stop_loss": 48.5,
        "volume_multiplier": 3.5,
    }
    intent, _ = oms.submit_candidate(candidate)
    assert intent is not None

    # Simulate price retracing to 49.60 (below 50.0 trigger) during trader delay
    res = oms.approve_intent(intent.intent_id, approver="USER", current_ltp=49.60)

    assert res["status"] == "ERROR"
    assert res["reason"] == "FALSE_BREAKOUT_RETRACED"
    assert "retraced below entry trigger" in res["message"]
    assert intent.status == IntentStatus.REJECTED
    assert len(oms.active_orders) == 0


def test_concurrent_multi_thread_approval_safety(tmp_path):
    """
    Codex Guarantee G1:
    Wrap state transitions in threading.RLock and CAS.
    Verify that 10 concurrent threads attempting to approve the same intent produce exactly 1 order.
    """
    oms = HybridExecutionOMS(output_dir=tmp_path)
    oms.set_mode(ExecutionMode.CO_PILOT)

    candidate = {
        "symbol": "CDSL",
        "entry_price": 1400.0,
        "stop_loss": 1370.0,
        "volume_multiplier": 3.5,
    }
    intent, _ = oms.submit_candidate(candidate)
    assert intent is not None

    results = []
    # Launch 10 worker threads concurrently
    def worker(worker_id: int):
        return oms.approve_intent(intent.intent_id, approver=f"WORKER_{worker_id}")

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker, i) for i in range(10)]
        for f in as_completed(futures):
            results.append(f.result())

    success_count = sum(1 for r in results if r.get("status") == "SUCCESS")
    stale_or_dedup_count = sum(1 for r in results if r.get("status") in ("REJECTED_STALE_STATE", "IDEMPOTENT_IGNORED"))

    # Exactly 1 approval must succeed; all other 9 must be rejected fail-closed
    assert success_count == 1
    assert stale_or_dedup_count == 9
    assert len(oms.active_orders) == 1
    assert intent.status == IntentStatus.ROUTED


def test_deduplication_cache_rejects_duplicate_request(tmp_path):
    """
    Codex Guarantee G4:
    OMS implements a 300s monotonic deduplication cache to reject duplicate callbacks.
    """
    oms = HybridExecutionOMS(output_dir=tmp_path)
    oms.set_mode(ExecutionMode.CO_PILOT)

    candidate = {
        "symbol": "COCHINSHIP",
        "entry_price": 1350.0,
        "stop_loss": 1310.0,
        "volume_multiplier": 3.5,
    }
    intent, _ = oms.submit_candidate(candidate)
    assert intent is not None

    # First request
    res1 = oms.approve_intent(intent.intent_id, approver="USER", request_id="REQ_TOKEN_ABC123")
    assert res1["status"] == "SUCCESS"

    # Second request with identical token
    res2 = oms.approve_intent(intent.intent_id, approver="USER", request_id="REQ_TOKEN_ABC123")
    assert res2["status"] == "IDEMPOTENT_IGNORED"
    assert "Duplicate request" in res2["message"]


def test_rule1_security_gate_blocks_live_capital(tmp_path):
    # Attempting to set LIVE_BROKER with enforce_rule1_lock=True must fail closed
    cfg = PolicyConfig(
        mode=ExecutionMode.AUTONOMOUS,
        environment=ExecutionEnvironment.LIVE_BROKER,
        enforce_rule1_lock=True,
    )
    with pytest.raises(SecurityViolationError, match="RULE 1 VIOLATION"):
        cfg.validate_for_execution()

    oms = HybridExecutionOMS(config=cfg, output_dir=tmp_path)
    candidate = {
        "symbol": "RVNL",
        "entry_price": 215.0,
        "stop_loss": 207.5,
        "volume_multiplier": 4.5,
        "nifty_breadth_confirmed": True,
    }
    # Submission should fail-closed because routing cannot proceed
    with pytest.raises(SecurityViolationError, match="RULE 1 VIOLATION"):
        oms.submit_candidate(candidate)


def test_emergency_kill_switch(tmp_path):
    oms = HybridExecutionOMS(output_dir=tmp_path)
    candidate = {
        "symbol": "COCHINSHIP",
        "entry_price": 1380.0,
        "stop_loss": 1340.0,
        "volume_multiplier": 3.5,
    }
    intent, _ = oms.submit_candidate(candidate)
    assert intent is not None
    assert intent.status == IntentStatus.PENDING_APPROVAL

    kill_res = oms.emergency_flatten_all(reason="MANUAL_KILL_COMMAND")
    assert kill_res["status"] == "KILL_SWITCH_EXECUTED"
    assert kill_res["cancelled_intents"] == 1
    assert intent.status == IntentStatus.CANCELLED
    assert len(oms.active_orders) == 0


def test_pre_armed_conditional_intent_and_dedup_hydration(tmp_path):
    """
    Claude & Codex Audit Hardening:
    1. Pre-armed intent allows zero-latency machine-speed fill on trigger crossing.
    2. Dedup cache hydrates from disk on reboot, preventing duplicate orders post-crash.
    """
    oms = HybridExecutionOMS(output_dir=tmp_path)
    oms.set_mode(ExecutionMode.CO_PILOT)

    cand = {
        "symbol": "CDSL",
        "entry_price": 1000.0,
        "stop_loss": 980.0,
        "volume_multiplier": 3.2,
    }
    intent, msg = oms.submit_candidate(cand)
    assert intent is not None
    assert intent.status == IntentStatus.PENDING_APPROVAL

    # Pre-Arm the intent
    arm_res = oms.pre_arm_intent(intent.intent_id, armed_by="PRE_MARKET_YASHU")
    assert arm_res["status"] == "PRE_ARMED"
    assert intent.status == IntentStatus.PRE_ARMED

    # On trigger crossing, pre-armed intent approves and routes immediately
    app_res = oms.approve_intent(intent.intent_id, approver="MACHINE_TRIGGER")
    assert app_res["status"] == "SUCCESS"
    assert intent.status == IntentStatus.ROUTED
    assert len(oms.active_orders) == 1
    assert oms.active_orders[0]["dp_charges_count"] == 2
    assert oms.active_orders[0]["statutory_friction_est_rs"] == 167.54

    # Simulate daemon crash and reboot: new OMS instance with same output_dir
    oms_reboot = HybridExecutionOMS(output_dir=tmp_path)
    assert f"approve_{intent.intent_id}_USER" in oms_reboot._dedup_cache
    assert f"approve_{intent.intent_id}_AUTONOMOUS" in oms_reboot._dedup_cache

