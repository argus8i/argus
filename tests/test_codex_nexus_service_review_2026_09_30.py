"""Reviewer probes. All filesystem and process actions are isolated or intercepted."""
import importlib.util
import json
import os
import sys
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest

OPS = Path(os.environ["ARGUS_REVIEW_OPS_ROOT"])
sys.path.insert(0, str(OPS))
from antigravity.daemons import nexus_watchdog as nw
from antigravity.daemons import supervised_inbox_worker as sw


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for module in (nw, sw):
        for name in ("SUPERVISOR_PID_FILE", "SUPERVISOR_LOCK_FILE", "SUPERVISOR_LOG_FILE", "SUPERVISOR_STOP_FILE", "WATCHDOG_LOG_FILE"):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, str(tmp_path / name))
    monkeypatch.setattr(sw, "ensure_directories", lambda: None)
    monkeypatch.setattr(sw, "log_supervisor", lambda *a: None)
    monkeypatch.setattr(nw, "log_watchdog", lambda *a: None)
    monkeypatch.setattr(nw, "start_supervisor_task", lambda: False)


def _supervisor(monkeypatch):
    supervisor = sw.SupervisedInboxWorker()
    supervisor.lock = Mock(acquire=lambda: True, release=lambda: None)
    monkeypatch.setattr(supervisor, "_setup_signal_handlers", lambda: None)
    monkeypatch.setattr(sw, "get_process_create_time_nt", lambda pid: 123)
    return supervisor


def test_quiet_healthy_worker_does_not_make_supervisor_hung(monkeypatch):
    supervisor = _supervisor(monkeypatch)
    reading, release = threading.Event(), threading.Event()
    clock = [1000.0]
    monkeypatch.setattr(sw.time, "time", lambda: clock[0])
    proc = Mock(pid=55555, returncode=0)
    proc.poll.return_value = None

    def readline():
        reading.set()
        release.wait(4)
        return ""

    proc.stdout.readline = readline
    proc.terminate.side_effect = lambda: setattr(proc.poll, "return_value", 0)
    monkeypatch.setattr(sw.subprocess, "Popen", lambda *a, **kw: proc)
    monkeypatch.setattr(sw, "_pid_is_running", lambda *a, **kw: True)
    thread = threading.Thread(target=supervisor.run, daemon=True)
    thread.start()
    assert reading.wait(2)
    clock[0] += 130  # A healthy worker can be silent throughout a long model request.
    status = sw.get_status()
    supervisor.shutdown_requested = True
    proc.poll.return_value = 0
    release.set()
    thread.join(4)
    assert not thread.is_alive()
    assert status["status"] == "RUNNING", status


def test_hung_path_rechecks_creation_time_before_killing(monkeypatch):
    monkeypatch.setattr(nw, "get_status", lambda: {"status": "SUPERVISOR_HUNG", "running": False,
        "details": {"supervisor_pid": 55555, "supervisor_create_time_nt": 123}})
    monkeypatch.setattr(nw, "_pid_is_running", lambda *a, **kw: False)
    killed = []
    monkeypatch.setattr(nw.os, "kill", lambda pid, sig: killed.append(pid))
    nw.check_and_recover(verbose=False)
    assert killed == [], f"Recycled/dead identity was killed: {killed}"


def test_failed_hung_termination_preserves_live_lock(monkeypatch):
    Path(nw.SUPERVISOR_LOCK_FILE).write_text("owned")
    Path(nw.SUPERVISOR_PID_FILE).write_text("owned")
    monkeypatch.setattr(nw, "get_status", lambda: {"status": "SUPERVISOR_HUNG", "running": False,
        "details": {"supervisor_pid": 55555, "supervisor_create_time_nt": 123}})
    monkeypatch.setattr(nw, "_pid_is_running", lambda *a, **kw: True)
    def denied(*a):
        raise PermissionError("cannot terminate")
    monkeypatch.setattr(nw.os, "kill", denied)
    nw.check_and_recover(verbose=False)
    assert Path(nw.SUPERVISOR_LOCK_FILE).exists(), "Lock deleted while owner remains alive"


def test_circuit_breaker_leaves_durable_tripped_state(monkeypatch):
    supervisor = _supervisor(monkeypatch)
    proc = Mock(pid=55555, returncode=1)
    proc.poll.return_value = 1
    monkeypatch.setattr(sw.subprocess, "Popen", lambda *a, **kw: proc)
    supervisor.crash_timestamps = [sw.time.time()] * 4
    supervisor.run()
    assert Path(sw.SUPERVISOR_PID_FILE).exists(), "Circuit-breaker state erased during finally cleanup"
    assert json.loads(Path(sw.SUPERVISOR_PID_FILE).read_text())["status"] == "CIRCUIT_BREAKER_TRIPPED"


def test_stop_checks_recorded_process_identity(monkeypatch):
    monkeypatch.setattr(sw, "get_status", lambda: {"status": "STALE_PID", "running": False,
        "details": {"supervisor_pid": 55555, "supervisor_create_time_nt": 123}})
    monkeypatch.setattr(sw, "_pid_is_running", lambda pid, expected_create_time=None: expected_create_time is None)
    killed = []
    monkeypatch.setattr(sw.os, "kill", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(sw.time, "sleep", lambda *a: None)
    ticks = iter(range(100, 130))
    monkeypatch.setattr(sw.time, "time", lambda: next(ticks))
    sw.stop_daemon()
    assert killed == [], f"Stop signalled recycled PID without creation-time check: {killed}"


def test_author_mocked_watchdog_test_isolates_log_paths(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("reviewed_author_tests", OPS / "tests/test_nexus_resilience.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Restore the paths that importing the exact committed test suite would use.
    monkeypatch.setattr(nw, "WATCHDOG_LOG_FILE", str(OPS / "antigravity/messages/watchdog.log"))
    monkeypatch.setattr(nw, "SUPERVISOR_LOG_FILE", str(OPS / "antigravity/messages/supervisor.log"))
    attempted = []
    monkeypatch.setattr(nw, "log_watchdog", lambda msg: attempted.extend([nw.WATCHDOG_LOG_FILE, nw.SUPERVISOR_LOG_FILE]))
    module.test_hung_but_alive_supervisor_recovered_by_watchdog(monkeypatch, tmp_path)
    assert attempted and all(str(tmp_path) in p for p in attempted), attempted
