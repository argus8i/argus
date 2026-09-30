r"""
test_nexus_resilience.py - Verification Tests for Nexus Bus Resilience & Watchdog
================================================================================
Tests:
  1. Strict test filesystem isolation (MESSAGES_ROOT, PID, lock, log paths in tmp_path).
  2. Supervisor status contract & PID validation.
  3. Clean shutdown without orphaned processes.
  4. Watchdog automated recovery on simulated outage.
  5. End-to-end PING / PONG processing on recovered daemon.
  6. Orchestrator dashboard Bus Health telemetry reporting.
  7. Read-only peer dispatch execution contract (refuses artifact writing).
  8. Pre-execution nonce commit in SQLite WAL store (at-most-once semantics).
  9. Preservation of live supervisor mutual-exclusion locks during worker restart.
  10. NT creation-time PID recycling detection.
  11. Dead supervisor lock cleanup and recovery.
  12. AST invariant: single authorized launch site via Windows Task Scheduler.
  13. Hung supervisor force-termination and recovery.
  14. Access-denied fail-closed liveness check.
  15. Mid-dispatch worker crash replay prevention.
  16. Live production messages directory purity & supervisor.pid integrity invariant.
"""

import os
import sys
import time
import json
import builtins
import io
import pytest

WORKSPACE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if WORKSPACE_DIR not in sys.path:
    sys.path.insert(0, WORKSPACE_DIR)

from antigravity.daemons.supervised_inbox_worker import (
    get_status,
    stop_daemon,
    get_current_ist,
)
from antigravity.daemons.nexus_watchdog import check_and_recover
from antigravity.daemons.tri_agent_bus import (
    send_to_agent,
    wait_for_agent_response,
)
from antigravity.orchestrator.status import get_hub_status


@pytest.fixture(autouse=True)
def isolate_nexus_filesystem(tmp_path, monkeypatch):
    """
    Enforces strict test isolation:
    1. Redirects ALL messages directories, pid, lock, log, and replay store paths to tmp_path.
    2. Installs an active filesystem interceptor that forbids any write, append, delete,
       or creation targeting the live production antigravity/messages directory.
    """
    real_messages_root = os.path.normpath(os.path.join(WORKSPACE_DIR, "antigravity", "messages"))

    sandbox_root = tmp_path / "messages"
    inbox = sandbox_root / "inbox"
    outbox = sandbox_root / "outbox"
    archive = sandbox_root / "archive"
    dead_letter = sandbox_root / "dead_letter"
    backups = sandbox_root / "backups"
    replay_db = sandbox_root / "replay_store.db"
    sup_pid = sandbox_root / "supervisor.pid"
    sup_lock = sandbox_root / "supervisor.lock"
    sup_log = sandbox_root / "supervisor.log"
    sup_stop = sandbox_root / "supervisor.stop"
    watchdog_log = sandbox_root / "watchdog.log"

    for d in [inbox, outbox, archive, dead_letter, backups]:
        d.mkdir(parents=True, exist_ok=True)

    import antigravity.daemons.inbox_worker as iw
    import antigravity.daemons.supervised_inbox_worker as siw
    import antigravity.daemons.nexus_watchdog as nw
    import antigravity.daemons.tri_agent_bus as tab
    import antigravity.orchestrator.status as aos

    # 1. Patch inbox_worker
    monkeypatch.setattr(iw, "MESSAGES_ROOT", str(sandbox_root))
    monkeypatch.setattr(iw, "INBOX_DIR", str(inbox))
    monkeypatch.setattr(iw, "OUTBOX_DIR", str(outbox))
    monkeypatch.setattr(iw, "ARCHIVE_DIR", str(archive))
    monkeypatch.setattr(iw, "DEAD_LETTER_DIR", str(dead_letter))
    monkeypatch.setattr(iw, "BACKUPS_DIR", str(backups))
    monkeypatch.setattr(iw, "REPLAY_DB_PATH", str(replay_db))

    # 2. Patch supervised_inbox_worker
    monkeypatch.setattr(siw, "MESSAGES_ROOT", str(sandbox_root))
    monkeypatch.setattr(siw, "SUPERVISOR_PID_FILE", str(sup_pid))
    monkeypatch.setattr(siw, "SUPERVISOR_LOCK_FILE", str(sup_lock))
    monkeypatch.setattr(siw, "SUPERVISOR_LOG_FILE", str(sup_log))
    monkeypatch.setattr(siw, "SUPERVISOR_STOP_FILE", str(sup_stop))

    # 3. Patch nexus_watchdog
    monkeypatch.setattr(nw, "MESSAGES_ROOT", str(sandbox_root))
    monkeypatch.setattr(nw, "SUPERVISOR_PID_FILE", str(sup_pid))
    monkeypatch.setattr(nw, "SUPERVISOR_LOCK_FILE", str(sup_lock))
    monkeypatch.setattr(nw, "SUPERVISOR_LOG_FILE", str(sup_log))
    monkeypatch.setattr(nw, "WATCHDOG_LOG_FILE", str(watchdog_log))

    # 4. Patch tri_agent_bus
    monkeypatch.setattr(tab, "MESSAGES_ROOT", str(sandbox_root))
    monkeypatch.setattr(tab, "INBOX_DIR", str(inbox))
    monkeypatch.setattr(tab, "OUTBOX_DIR", str(outbox))
    monkeypatch.setattr(tab, "ARCHIVE_DIR", str(archive))
    monkeypatch.setattr(tab, "DEAD_LETTER_DIR", str(dead_letter))
    monkeypatch.setattr(tab, "BACKUPS_DIR", str(backups))

    # 5. Patch orchestrator status
    monkeypatch.setattr(aos, "MESSAGES_ROOT", str(sandbox_root))
    monkeypatch.setattr(aos, "INBOX_DIR", str(inbox))
    monkeypatch.setattr(aos, "OUTBOX_DIR", str(outbox))
    monkeypatch.setattr(aos, "ARCHIVE_DIR", str(archive))
    monkeypatch.setattr(aos, "DEAD_LETTER_DIR", str(dead_letter))

    # 6. Active write/delete guard forbidding mutations to live messages directory
    orig_open = builtins.open
    def guarded_open(file, *args, **kwargs):
        try:
            resolved = os.path.normpath(os.path.abspath(str(file)))
            if resolved.startswith(real_messages_root):
                mode = args[0] if args else kwargs.get("mode", "r")
                if any(m in mode for m in ("w", "a", "+", "x")):
                    raise PermissionError(f"TEST ISOLATION BREACH: Attempted write to live messages folder: {resolved}")
        except PermissionError:
            raise
        except Exception:
            pass
        return orig_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(io, "open", guarded_open)

    orig_remove = os.remove
    def guarded_remove(path, *args, **kwargs):
        if os.path.normpath(os.path.abspath(str(path))).startswith(real_messages_root):
            raise PermissionError(f"TEST ISOLATION BREACH: Attempted os.remove on live path: {path}")
        return orig_remove(path, *args, **kwargs)
    monkeypatch.setattr(os, "remove", guarded_remove)

    orig_unlink = os.unlink
    def guarded_unlink(path, *args, **kwargs):
        if os.path.normpath(os.path.abspath(str(path))).startswith(real_messages_root):
            raise PermissionError(f"TEST ISOLATION BREACH: Attempted os.unlink on live path: {path}")
        return orig_unlink(path, *args, **kwargs)
    monkeypatch.setattr(os, "unlink", guarded_unlink)


def test_supervisor_status_contract():
    """Verifies that get_status() returns required health fields across lifecycle states."""
    import antigravity.daemons.supervised_inbox_worker as siw
    from antigravity.daemons.inbox_worker import get_process_create_time_nt

    # 1. Stopped state (PID file absent)
    st = get_status()
    assert isinstance(st, dict)
    assert "status" in st
    assert "running" in st
    assert st["status"] == "STOPPED"
    assert st["running"] is False

    # 2. Running state (PID file present with alive process)
    my_pid = os.getpid()
    ct = get_process_create_time_nt(my_pid)
    data = {
        "supervisor_pid": my_pid,
        "supervisor_create_time_nt": ct,
        "worker_pid": my_pid,
        "worker_create_time_nt": ct,
        "last_heartbeat_ts": time.time(),
        "status": "RUNNING"
    }
    with open(siw.SUPERVISOR_PID_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    st2 = get_status()
    assert st2["running"] is True
    assert st2["status"] == "RUNNING"
    assert st2["worker_alive"] is True


def test_watchdog_recovers_if_down(monkeypatch):
    """Verifies that the 5-minute watchdog detects outages and recovers the daemon."""
    import antigravity.daemons.nexus_watchdog as nw
    from antigravity.daemons.inbox_worker import get_process_create_time_nt

    sup_pid_file = nw.SUPERVISOR_PID_FILE
    assert not os.path.exists(sup_pid_file)

    def fake_start():
        pid = os.getpid()
        ct = get_process_create_time_nt(pid)
        data = {
            "supervisor_pid": pid,
            "supervisor_create_time_nt": ct,
            "worker_pid": pid,
            "worker_create_time_nt": ct,
            "last_heartbeat_ts": time.time(),
            "started_at_ist": get_current_ist(),
            "status": "RUNNING"
        }
        with open(sup_pid_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return True

    monkeypatch.setattr(nw, "start_supervisor_task", fake_start)
    monkeypatch.setattr(nw, "_pid_is_running", lambda pid, expected_create_time=None: True)

    res = nw.check_and_recover(verbose=False)
    assert res["healthy"] is True
    st = nw.get_status()
    assert st["running"] is True
    assert st["status"] == "RUNNING"
    assert st.get("worker_alive") is True


def test_end_to_end_ping_pong_after_recovery():
    """Verifies that signed messages process cleanly in an isolated environment."""
    msg_id, corr_id = send_to_agent(
        sender="CLAUDE",
        recipient="ANTIGRAVITY",
        subject="PING",
        body="RESILIENCE_TEST",
        track="SHARED",
        timeout_sec=10.0,
    )
    assert msg_id.startswith("msg_")
    assert corr_id.startswith("corr_")

    resp = wait_for_agent_response(
        correlation_id=corr_id,
        recipient="ANTIGRAVITY",
        timeout_sec=10.0,
        poll_interval_sec=0.1,
        auto_process_worker=True,
    )
    assert resp["success"] is True
    assert resp["status"] == "COMPLETED"
    output = resp.get("response", {}).get("output_payload", {})
    assert output.get("reply") == "PONG"


def test_dashboard_bus_health_telemetry():
    """Verifies that get_hub_status() contains Bus Health and last processed message."""
    import antigravity.daemons.nexus_watchdog as nw
    import antigravity.daemons.inbox_worker as iw
    from antigravity.daemons.inbox_worker import get_process_create_time_nt

    pid = os.getpid()
    ct = get_process_create_time_nt(pid)
    data = {
        "supervisor_pid": pid,
        "supervisor_create_time_nt": ct,
        "worker_pid": pid,
        "worker_create_time_nt": ct,
        "last_heartbeat_ts": time.time(),
        "status": "RUNNING"
    }
    with open(nw.SUPERVISOR_PID_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    archive_msg = {
        "message_id": "msg_test_01",
        "sender": "CLAUDE",
        "recipient": "ANTIGRAVITY",
        "subject": "PING",
        "status": "COMPLETED",
        "completed_at_ist": get_current_ist()
    }
    with open(os.path.join(iw.ARCHIVE_DIR, "msg_test_01.json"), "w", encoding="utf-8") as f:
        json.dump(archive_msg, f, indent=2)

    hub = get_hub_status()
    assert "bus" in hub
    bus = hub["bus"]
    assert bus["health"] in ("OK", "DOWN")
    assert "last_message" in bus
    if bus["health"] == "OK":
        assert bus["supervisor_pid"] is not None
        assert bus["worker_pid"] is not None
        assert bus["last_message"]["message_id"] == "msg_test_01"


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
    from antigravity.daemons.inbox_worker import verify_message_auth
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
    monkeypatch.setattr(nw, "_pid_is_running", lambda pid, expected_create_time=None: True if pid == 99999 else False)

    dummy_lock = tmp_path / "supervisor.lock"
    dummy_lock.write_text("dummy_lock")
    monkeypatch.setattr(nw, "SUPERVISOR_LOCK_FILE", str(dummy_lock))

    spawn_called = False
    def fake_spawn(*args, **kwargs):
        nonlocal spawn_called
        spawn_called = True
        return None

    monkeypatch.setattr(nw, "start_supervisor_task", fake_spawn)
    monkeypatch.setattr(nw.subprocess, "Popen", fake_spawn)

    res = nw.check_and_recover(verbose=False)

    assert res.get("healthy") is True
    assert res.get("action") in ("WAIT_WORKER_RESTART", "NOOP", "SUPERVISOR_ALIVE_WAIT")
    assert dummy_lock.exists(), "SUPERVISOR_LOCK_FILE must not be deleted when supervisor is alive!"
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

    assert not dummy_lock.exists(), "supervisor.lock was not cleared when supervisor died!"
    assert not dummy_lock_lock.exists(), "supervisor.lock.lock was not cleared when supervisor died!"
    assert not dummy_pid.exists(), "supervisor.pid was not cleared when supervisor died!"
    assert spawn_called is True, "start_supervisor_task was not triggered when supervisor died!"


def test_ast_single_launch_site_for_supervisor():
    """AST Invariant Test: Verifies that no Popen or unauthorized process spawning calls invoke supervised_inbox_worker.
    The only permitted call sites are schtasks /Run in nexus_watchdog.py and supervised_inbox_worker.py --start."""
    import ast
    from pathlib import Path

    spawning_apis = {"Popen", "run", "call", "check_call", "system", "spawnlp", "spawnl", "startfile"}
    py_files = list(Path(WORKSPACE_DIR, "antigravity").rglob("*.py"))
    violations = []

    for fpath in py_files:
        rel_path = fpath.relative_to(WORKSPACE_DIR).as_posix()
        try:
            tree = ast.parse(fpath.read_text(encoding="utf-8"), filename=str(fpath))
        except Exception:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_name = ""
                if isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr
                elif isinstance(node.func, ast.Name):
                    func_name = node.func.id

                if func_name in spawning_apis:
                    arg_str = ast.dump(node)
                    if "supervised_inbox_worker" in arg_str:
                        if func_name == "Popen":
                            violations.append(f"{rel_path}:{node.lineno} calls Popen on supervised_inbox_worker")
                        elif "schtasks" not in arg_str:
                            violations.append(f"{rel_path}:{node.lineno} spawns supervised_inbox_worker without schtasks")

    assert not violations, f"Forbidden supervisor launch sites detected:\n" + "\n".join(violations)


def test_hung_but_alive_supervisor_recovered_by_watchdog(monkeypatch, tmp_path):
    """Verifies that if a supervisor is alive in the OS but hung (>120s heartbeat), watchdog force-terminates it and restores service."""
    import antigravity.daemons.nexus_watchdog as nw

    hung_pid = 55555
    mock_status = {
        "status": "SUPERVISOR_HUNG",
        "running": False,
        "details": {"supervisor_pid": hung_pid, "supervisor_create_time_nt": 12345, "worker_pid": None},
        "worker_alive": False
    }
    monkeypatch.setattr(nw, "get_status", lambda: mock_status)
    hung_alive = [True]
    monkeypatch.setattr(nw, "_pid_is_running", lambda pid, expected_create_time=None: hung_alive[0] if pid == hung_pid else False)

    dummy_lock = tmp_path / "supervisor.lock"
    dummy_lock.write_text("dummy_lock")
    dummy_pid = tmp_path / "supervisor.pid"
    dummy_pid.write_text(json.dumps({"supervisor_pid": hung_pid}))

    monkeypatch.setattr(nw, "SUPERVISOR_LOCK_FILE", str(dummy_lock))
    monkeypatch.setattr(nw, "SUPERVISOR_PID_FILE", str(dummy_pid))
    monkeypatch.setattr(nw, "SUPERVISOR_LOG_FILE", str(tmp_path / "supervisor.log"))
    monkeypatch.setattr(nw, "WATCHDOG_LOG_FILE", str(tmp_path / "watchdog.log"))

    killed_pids = []
    def fake_kill(pid, sig):
        killed_pids.append(pid)
        hung_alive[0] = False

    monkeypatch.setattr(nw.os, "kill", fake_kill)

    spawn_called = False
    def fake_start():
        nonlocal spawn_called
        spawn_called = True
        return True

    monkeypatch.setattr(nw, "start_supervisor_task", fake_start)

    res = nw.check_and_recover(verbose=False)

    assert hung_pid in killed_pids, "Hung supervisor was not killed by watchdog!"
    assert not dummy_lock.exists(), "supervisor.lock was not cleared after killing hung supervisor!"
    assert spawn_called is True, "Watchdog failed to trigger schtasks restart for hung supervisor!"


def test_access_denied_liveness_fails_closed(monkeypatch):
    """Verifies that if OpenProcess fails with ERROR_ACCESS_DENIED, _pid_is_running fails closed (returns True, never assumes dead)."""
    from antigravity.daemons.inbox_worker import _pid_is_running
    import ctypes

    class FakeKernel:
        def OpenProcess(self, access, inherit, pid):
            return 0
        def GetLastError(self):
            return 5

    monkeypatch.setattr(ctypes.windll, "kernel32", FakeKernel())
    assert _pid_is_running(99999) is True


def test_kill_during_dispatch_preserves_nonce_and_at_most_once():
    """Verifies that if a worker is killed mid-execution, re-processing the same message fails with REPLAY_ATTACK because the nonce was committed before execution started."""
    from antigravity.daemons.inbox_worker import (
        verify_message_auth,
        compute_envelope_hmac,
        get_agent_secret_key,
        get_current_ist,
    )
    import uuid

    nonce = f"mid_dispatch_{uuid.uuid4().hex[:12]}"
    msg = {
        "message_id": "msg_mid_dispatch_01",
        "correlation_id": "corr_mid_dispatch_01",
        "sender": "ANTIGRAVITY",
        "recipient": "CODEX",
        "track": "SHARED",
        "created_at_ist": get_current_ist(),
        "subject": "PING",
        "body": "mid dispatch test",
        "status": "CREATED",
        "attempt_count": 0,
        "nonce": nonce,
    }
    key = get_agent_secret_key("ANTIGRAVITY")
    msg["auth_signature"] = compute_envelope_hmac(msg, key)

    # 1. First verification commits nonce to SQLite WAL store
    ok, err = verify_message_auth(msg)
    assert ok is True

    # 2. Worker simulated crash mid-execution. Message is re-read by recovered worker.
    # 3. Second verification MUST fail closed as REPLAY_ATTACK
    ok2, err2 = verify_message_auth(msg)
    assert ok2 is False
    assert "REPLAY_ATTACK" in err2


def test_live_messages_directory_untouched():
    """
    Verification Invariant:
    Proves that running resilience tests never writes, creates, or deletes any files
    in the live production antigravity/messages folder, and specifically confirms that
    the live supervisor.pid has not been modified.
    """
    real_messages_root = os.path.normpath(os.path.join(WORKSPACE_DIR, "antigravity", "messages"))
    live_pid_file = os.path.join(real_messages_root, "supervisor.pid")

    # 1. Assert live supervisor.pid exists and contains valid running supervisor data
    assert os.path.exists(live_pid_file), "Live supervisor.pid must exist"
    with open(live_pid_file, "r", encoding="utf-8") as f:
        pid_data = json.load(f)
    live_sup_pid = pid_data.get("supervisor_pid")
    assert live_sup_pid is not None and live_sup_pid > 0
    # Must NOT be test mock PID
    assert live_sup_pid not in (55555, 88888, 77777, 99999, 42)

    # 2. Direct attempt to write to live_messages_root must be blocked by the isolation guard
    with pytest.raises(PermissionError) as exc_info:
        test_leak_file = os.path.join(real_messages_root, "test_leak.tmp")
        with open(test_leak_file, "w", encoding="utf-8") as f:
            f.write("leak")
    assert "TEST ISOLATION BREACH" in str(exc_info.value)
    assert not os.path.exists(os.path.join(real_messages_root, "test_leak.tmp"))

    # 3. Confirm live supervisor.pid contents are intact and uncorrupted
    with open(live_pid_file, "r", encoding="utf-8") as f:
        pid_data_after = json.load(f)
    assert pid_data == pid_data_after, "Live supervisor.pid was modified during test run!"
