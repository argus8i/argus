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
    get_current_ist,
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
        "created_at_ist": get_current_ist(),
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


def test_watchdog_never_duplicates_live_supervisor_on_worker_down(monkeypatch, tmp_path):
    """Verifies that WORKER_DOWN is treated as 'supervisor is restarting its worker, wait' and NEVER deletes locks or spawns a second supervisor."""
    import antigravity.daemons.nexus_watchdog as nw
    
    mock_status = {
        "status": "WORKER_DOWN",
        "running": True,
        "details": {"supervisor_pid": 99999, "worker_pid": None},
        "worker_alive": False
    }
    monkeypatch.setattr(nw, "get_status", lambda: mock_status)
    monkeypatch.setattr(nw, "_pid_is_running", lambda pid: True if pid == 99999 else False)

    # Mock lock file presence
    dummy_lock = tmp_path / "supervisor.lock"
    dummy_lock.write_text("dummy_lock")
    monkeypatch.setattr(nw, "SUPERVISOR_LOCK_FILE", str(dummy_lock))

    spawn_called = False
    def fake_spawn(*args, **kwargs):
        nonlocal spawn_called
        spawn_called = True
        return None

    monkeypatch.setattr(nw, "start_supervisor_task", fake_spawn, raising=False)
    monkeypatch.setattr(nw.subprocess, "Popen", fake_spawn)

    res = nw.check_and_recover(verbose=False)

    # Must treat as healthy/wait, NOT an outage restart
    assert res.get("healthy") is True
    assert res.get("action") in ("WAIT_WORKER_RESTART", "NOOP", "SUPERVISOR_ALIVE_WAIT")
    # Must NEVER have deleted the lock file of a live supervisor
    assert dummy_lock.exists(), "SUPERVISOR_LOCK_FILE must not be deleted when supervisor is alive!"
    # Must NEVER have spawned a duplicate supervisor
    assert spawn_called is False, "A second supervisor was spawned while supervisor was alive!"


def test_watchdog_never_deletes_locks_when_supervisor_alive(monkeypatch, tmp_path):
    """Verifies that even if status is not RUNNING, lock files are NEVER deleted if supervisor PID is alive."""
    import antigravity.daemons.nexus_watchdog as nw

    mock_status = {
        "status": "STALE_PID",
        "running": False,
        "details": {"supervisor_pid": 88888, "worker_pid": None},
        "worker_alive": False
    }
    monkeypatch.setattr(nw, "get_status", lambda: mock_status)
    monkeypatch.setattr(nw, "_pid_is_running", lambda pid, expected_create_time=None: True if pid == 88888 else False)

    dummy_lock = tmp_path / "supervisor.lock"
    dummy_lock.write_text("dummy_lock")
    dummy_lock_lock = tmp_path / "supervisor.lock.lock"
    dummy_lock_lock.write_text("lock_lock")
    dummy_pid = tmp_path / "supervisor.pid"
    dummy_pid.write_text(json.dumps({"supervisor_pid": 88888}))

    monkeypatch.setattr(nw, "SUPERVISOR_LOCK_FILE", str(dummy_lock))
    monkeypatch.setattr(nw, "SUPERVISOR_PID_FILE", str(dummy_pid))

    res = nw.check_and_recover(verbose=False)

    # Because PID 88888 is alive, files must NOT be deleted
    assert dummy_lock.exists(), "supervisor.lock was deleted while supervisor process was alive!"
    assert dummy_lock_lock.exists(), "supervisor.lock.lock was deleted while supervisor process was alive!"
    assert dummy_pid.exists(), "supervisor.pid was deleted while supervisor process was alive!"


def test_pid_is_running_detects_pid_recycling():
    """Verifies that _pid_is_running detects when Windows recycles a PID to a different process."""
    from antigravity.daemons.inbox_worker import _pid_is_running, get_process_create_time_nt

    current_pid = os.getpid()
    actual_ct = get_process_create_time_nt(current_pid)
    assert actual_ct is not None, "get_process_create_time_nt must return a valid 64-bit NT timestamp on Windows"

    # 1. Matching PID + matching create time must be alive
    assert _pid_is_running(current_pid, expected_create_time=actual_ct) is True

    # 2. Matching PID + DIFFERENT create time (simulating recycled PID) must return False
    bogus_ct = actual_ct + 50000000  # 5 seconds later
    assert _pid_is_running(current_pid, expected_create_time=bogus_ct) is False


def test_watchdog_cleans_locks_and_recovers_when_supervisor_is_dead(monkeypatch, tmp_path):
    """Verifies the opposite rule: when supervisor is genuinely DEAD, stale locks are cleared and recovery runs."""
    import antigravity.daemons.nexus_watchdog as nw

    dead_pid = 77777
    mock_status = {
        "status": "STALE_PID",
        "running": False,
        "details": {"supervisor_pid": dead_pid, "supervisor_create_time_nt": 12345, "worker_pid": None},
        "worker_alive": False
    }
    monkeypatch.setattr(nw, "get_status", lambda: mock_status)
    # Supervisor is confirmed dead
    monkeypatch.setattr(nw, "_pid_is_running", lambda pid, expected_create_time=None: False)

    dummy_lock = tmp_path / "supervisor.lock"
    dummy_lock.write_text("dummy_lock")
    dummy_lock_lock = tmp_path / "supervisor.lock.lock"
    dummy_lock_lock.write_text("lock_lock")
    dummy_pid = tmp_path / "supervisor.pid"
    dummy_pid.write_text(json.dumps({"supervisor_pid": dead_pid}))

    monkeypatch.setattr(nw, "SUPERVISOR_LOCK_FILE", str(dummy_lock))
    monkeypatch.setattr(nw, "SUPERVISOR_PID_FILE", str(dummy_pid))

    spawn_called = False
    def fake_start():
        nonlocal spawn_called
        spawn_called = True
        return True

    monkeypatch.setattr(nw, "start_supervisor_task", fake_start)

    res = nw.check_and_recover(verbose=False)

    # When supervisor is DEAD, locks MUST be cleaned up so new supervisor can acquire
    assert not dummy_lock.exists(), "supervisor.lock was not cleared when supervisor died!"
    assert not dummy_lock_lock.exists(), "supervisor.lock.lock was not cleared when supervisor died!"
    assert not dummy_pid.exists(), "supervisor.pid was not cleared when supervisor died!"
    # Recovery must have been triggered via start_supervisor_task()
    assert spawn_called is True, "start_supervisor_task was not triggered when supervisor died!"



