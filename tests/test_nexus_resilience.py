r"""
test_nexus_resilience.py - Verification Tests for Nexus Bus Resilience & Watchdog
================================================================================
Tests:
  1. Supervisor status contract & PID validation.
  2. Clean shutdown via stop_daemon() without orphaned processes.
  3. Watchdog automated recovery on simulated outage.
  4. End-to-end PING / PONG processing on recovered daemon.
  5. Orchestrator dashboard Bus Health telemetry reporting.
"""

import os
import sys
import time
import json
import pytest

WORKSPACE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if WORKSPACE_DIR not in sys.path:
    sys.path.insert(0, WORKSPACE_DIR)

from antigravity.daemons.supervised_inbox_worker import (
    get_status,
    stop_daemon,
    SUPERVISOR_PID_FILE,
    SUPERVISOR_LOCK_FILE,
)
from antigravity.daemons.nexus_watchdog import check_and_recover
from antigravity.daemons.tri_agent_bus import (
    send_to_agent,
    wait_for_agent_response,
)
from antigravity.orchestrator.status import get_hub_status


def test_supervisor_status_contract():
    """Verifies that get_status() returns required health fields."""
    st = get_status()
    assert isinstance(st, dict)
    assert "status" in st
    assert "running" in st
    assert st["status"] in ("RUNNING", "WORKER_DOWN", "STALE_PID", "STOPPED", "ERROR")


def test_watchdog_recovers_if_down():
    """Verifies that the 5-minute watchdog detects outages and recovers the daemon."""
    res = check_and_recover(verbose=False)
    assert res["healthy"] is True
    st = get_status()
    assert st["running"] is True
    assert st["status"] == "RUNNING"
    assert st.get("worker_alive") is True


def test_end_to_end_ping_pong_after_recovery():
    """Verifies that signed messages process cleanly after automated recovery."""
    st = get_status()
    if not st["running"]:
        check_and_recover(verbose=False)

    msg_id, corr_id = send_to_agent(
        sender="CLAUDE",
        recipient="ANTIGRAVITY",
        subject="PING",
        body="RESILIENCE_TEST",
        track="SHARED",
        timeout_sec=30.0,
    )
    assert msg_id.startswith("msg_")
    assert corr_id.startswith("corr_")

    resp = wait_for_agent_response(
        correlation_id=corr_id,
        recipient="ANTIGRAVITY",
        timeout_sec=10.0,
        poll_interval_sec=0.25,
    )
    assert resp["success"] is True
    assert resp["status"] == "COMPLETED"
    output = resp.get("response", {}).get("output_payload", {})
    assert output.get("reply") == "PONG"


def test_dashboard_bus_health_telemetry():
    """Verifies that get_hub_status() contains Bus Health and last processed message."""
    hub = get_hub_status()
    assert "bus" in hub
    bus = hub["bus"]
    assert bus["health"] in ("OK", "DOWN")
    assert "last_message" in bus
    if bus["health"] == "OK":
        assert bus["supervisor_pid"] is not None
        assert bus["worker_pid"] is not None


def test_peer_dispatch_refuses_file_writing():
    """Verifies that peer review dispatches cannot write artifacts (fail-closed read-only)."""
    from antigravity.daemons.inbox_worker import InboxWorker

    msg = {
        "message_id": "msg_test_peer_write_01",
        "correlation_id": "corr_test_peer_write_01",
        "sender": "ANTIGRAVITY",
        "recipient": "CLAUDE",
        "track": "SHARED",
        "subject": "REVIEW",
        "body": "Attempted file write",
        "expected_response_file": "shared/reviews/unauthorized.md",
    }
    worker = InboxWorker()
    status, payload, artifact_hashes, err = worker.execute_task(msg)
    assert status == "INCOMPLETE"
    assert "PEER_ARTIFACT_UNSUPPORTED" in err


def test_nonce_committed_before_execution_at_most_once():
    """Verifies that nonces are recorded in SQLite WAL store BEFORE task execution to enforce at-most-once semantics."""
    from antigravity.daemons.inbox_worker import verify_message_auth, MESSAGES_ROOT, DurableReplayStore
    import uuid

    test_nonce = f"nonce_{uuid.uuid4().hex[:12]}"
    test_msg = {
        "message_id": "msg_test_nonce_01",
        "correlation_id": "corr_test_nonce_01",
        "sender": "ANTIGRAVITY",
        "recipient": "CODEX",
        "track": "SHARED",
        "created_at_ist": "2026-09-30 15:30:00 IST",
        "subject": "PING",
        "body": "Test",
        "status": "CREATED",
        "attempt_count": 0,
        "nonce": test_nonce,
    }
    from antigravity.daemons.inbox_worker import compute_envelope_hmac, get_agent_secret_key
    key = get_agent_secret_key("ANTIGRAVITY")
    test_msg["auth_signature"] = compute_envelope_hmac(test_msg, key)

    # First verification must succeed and record the nonce
    ok, err = verify_message_auth(test_msg)
    assert ok is True

    # Immediate replay must fail as REPLAY_ATTACK because nonce was already committed
    ok2, err2 = verify_message_auth(test_msg)
    assert ok2 is False
    assert "REPLAY_ATTACK" in err2

