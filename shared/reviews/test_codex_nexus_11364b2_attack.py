"""Exact-commit Nexus boundary probes. Synthetic keys, queues and process identities only."""
import copy
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.environ.get("ARGUS_NEXUS_REVIEW_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))))
from antigravity.daemons import inbox_worker as iw
from antigravity.daemons import supervised_inbox_worker as sw
from antigravity.daemons import tri_agent_bus as bus


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(iw, "MESSAGES_ROOT", str(tmp_path / "messages"))
    monkeypatch.setattr(bus, "MESSAGES_ROOT", str(tmp_path / "messages"))
    for name, sub in [("INBOX_DIR", "inbox"), ("OUTBOX_DIR", "outbox"), ("ARCHIVE_DIR", "archive"),
                      ("DEAD_LETTER_DIR", "dead"), ("BACKUPS_DIR", "backups")]:
        p = tmp_path / "messages" / sub
        p.mkdir(parents=True)
        monkeypatch.setattr(iw, name, str(p))
        monkeypatch.setattr(bus, name, str(p), raising=False)
    monkeypatch.setattr(iw, "get_agent_secret_key", lambda _: "FAKE-REVIEW-KEY")
    monkeypatch.setattr(iw, "load_auth_config", lambda: {"token_validity_sec": 300, "max_future_skew_sec": 60})
    for name in ["SUPERVISOR_PID_FILE", "SUPERVISOR_LOCK_FILE", "SUPERVISOR_LOG_FILE", "SUPERVISOR_STOP_FILE"]:
        monkeypatch.setattr(sw, name, str(tmp_path / name))
    monkeypatch.setattr(sw, "log_supervisor", lambda *a: None)


def envelope(mid="msg_test_0001", recipient="ANTIGRAVITY", created=None):
    msg = dict(message_id=mid, correlation_id="corr_" + mid, sender="CLAUDE", recipient=recipient,
               track="SHARED", subject="ECHO", body="synthetic", status="CREATED", attempt_count=0,
               nonce="nonce_" + mid, created_at_ist=created or iw.get_current_ist())
    msg["auth_signature"] = iw.compute_envelope_hmac(msg, "FAKE-REVIEW-KEY")
    return msg


def enqueue(msg):
    path = Path(iw.INBOX_DIR) / (msg["message_id"] + ".json")
    iw.write_json_atomic(str(path), msg)
    return path


def test_unsigned_verified_marker_cannot_admit_expired_message():
    original = envelope(created="2020-01-01 00:00:00 IST")
    assert iw.validate_message_schema(original)[0] is False
    forged = dict(original, auth_verified_at_ist=iw.get_current_ist())
    ok, error = iw.validate_message_schema(forged)
    assert not ok, {"accepted_expired_signed_copy_with_unsigned_marker": ok, "error": error}


def test_unsigned_verified_marker_cannot_bypass_consumed_nonce():
    original = envelope()
    assert iw.validate_message_schema(original)[0] is True
    replay = dict(original, auth_verified_at_ist=iw.get_current_ist())
    ok, error = iw.validate_message_schema(replay)
    assert not ok, {"accepted_same_consumed_nonce": ok, "error": error}


def test_expired_copy_with_unsigned_marker_is_not_executed():
    forged = dict(envelope(created="2020-01-01 00:00:00 IST"), auth_verified_at_ist=iw.get_current_ist())
    enqueue(forged)
    worker = iw.InboxWorker()
    try:
        worker.run_single_pass()
        response = json.loads((Path(iw.OUTBOX_DIR) / (forged["correlation_id"] + "_resp.json")).read_text())
        assert response["status"] != "COMPLETED", {"expired_copy_was_executed": response}
    finally:
        worker._executor.shutdown(wait=True)


def test_recovered_retry_consumes_durable_permission(tmp_path, monkeypatch):
    msg = envelope()
    enqueue(msg)
    worker = iw.InboxWorker()
    try:
        claimed, admitted = worker.claim_message(msg["message_id"] + ".json")
        admitted["worker_pid"] = 55555
        admitted["worker_create_time_nt"] = 123
        iw.write_json_atomic(claimed, admitted)
        os.utime(claimed, (time.time() - 120, time.time() - 120))
        monkeypatch.setattr(iw, "_pid_is_running", lambda *a, **k: False)
        worker.recover_orphaned_claims()
        assert worker.run_single_pass() == 1
        store = iw.DurableReplayStore(str(Path(iw.MESSAGES_ROOT) / "replay_store.db"))
        ok, error = store.check_and_record_nonce(msg["nonce"], msg["sender"], msg["created_at_ist"],
                                                message_id=msg["message_id"], allow_recovery=True)
        assert not ok, {"recovery_permission_still_available_after_completed_retry": ok, "error": error}
    finally:
        worker._executor.shutdown(wait=True)


def test_fast_new_arrival_cannot_expire_behind_running_dispatch(monkeypatch):
    advanced = [0]
    base = datetime(2026, 10, 1, 12, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = base + timedelta(seconds=advanced[0])
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    monkeypatch.setattr(iw, "datetime", Clock)
    worker = iw.InboxWorker()
    started, release = threading.Event(), threading.Event()
    original = worker.execute_task

    def execute(msg, claimed_path=None):
        if msg["recipient"] == "CODEX":
            started.set()
            assert release.wait(2)
        return original(dict(msg, recipient="ANTIGRAVITY"), claimed_path)

    monkeypatch.setattr(worker, "execute_task", execute)
    enqueue(envelope("msg_001_slow", recipient="CODEX"))
    run = threading.Thread(target=worker.run_single_pass)
    run.start()
    try:
        assert started.wait(2)
        fast = envelope("msg_002_newarrival")
        enqueue(fast)
        advanced[0] = 400
        release.set()
        run.join(2)
        assert not run.is_alive()
        worker.run_single_pass()
        result = json.loads((Path(iw.OUTBOX_DIR) / (fast["correlation_id"] + "_resp.json")).read_text())
        assert result["status"] == "COMPLETED", {"status": result["status"], "error": result.get("error")}
    finally:
        release.set()
        run.join(3)
        worker._executor.shutdown(wait=True)


def test_busy_recipient_cannot_occupy_all_other_lane_threads(monkeypatch):
    worker = iw.InboxWorker()
    started, release, fast_done = threading.Event(), threading.Event(), threading.Event()

    def execute(msg, claimed_path=None):
        if msg["recipient"] == "CODEX":
            started.set()
            assert release.wait(2)
        else:
            fast_done.set()
        return "COMPLETED", {"reply": "synthetic"}, {}, None

    monkeypatch.setattr(worker, "execute_task", execute)
    for i in range(worker._executor._max_workers):
        enqueue(envelope(f"msg_00{i}_slow", recipient="CODEX"))
    enqueue(envelope("msg_999_fast", recipient="ANTIGRAVITY"))
    run = threading.Thread(target=worker.run_single_pass)
    run.start()
    try:
        assert started.wait(2)
        early = fast_done.wait(0.3)
    finally:
        release.set()
        run.join(3)
        worker._executor.shutdown(wait=True)
    assert early, "An idle recipient's job was starved by tasks waiting on another recipient's lock"


def test_expired_breaker_does_not_hide_healthy_restarted_supervisor(monkeypatch):
    breaker = {"status": "CIRCUIT_BREAKER_TRIPPED", "tripped_at_ts": time.time() - 1000,
               "cooldown_sec": 900, "supervisor_pid": 55555, "supervisor_create_time_nt": 123}
    Path(sw._get_breaker_file()).write_text(json.dumps(breaker))
    Path(sw.SUPERVISOR_PID_FILE).write_text(json.dumps({"status": "RUNNING", "supervisor_pid": 66666,
        "supervisor_create_time_nt": 456, "worker_pid": 77777, "worker_create_time_nt": 789,
        "last_heartbeat_ts": time.time()}))
    monkeypatch.setattr(sw, "_pid_is_running", lambda *a, **k: True)
    result = sw.get_status()
    assert result["status"] == "RUNNING" and result["running"], result


def test_unknown_torn_lock_owner_is_not_revoked_by_age(tmp_path):
    lock = iw.FileLock(str(tmp_path / "protected"), timeout_sec=0)
    Path(lock.lock_path).write_text('{"pid":')
    os.utime(lock.lock_path, (time.time() - 120, time.time() - 120))
    removed = lock._break_stale_lock()
    assert not removed and Path(lock.lock_path).exists(), "Unknown lock ownership was revoked by age alone"


def test_control_modified_signed_payload_still_rejected():
    forged = dict(envelope(), body="modified after signing", auth_verified_at_ist=iw.get_current_ist())
    ok, error = iw.validate_message_schema(forged)
    assert not ok and "AUTH_FAILED" in error


def test_concurrent_duplicate_submit_returns_one_original_receipt(monkeypatch):
    ready = threading.Barrier(2)
    original = bus.write_json_atomic

    def race(path, data):
        if Path(path).parent == Path(iw.INBOX_DIR):
            ready.wait(timeout=2)
        return original(path, data)

    monkeypatch.setattr(bus, "write_json_atomic", race)

    def send(i):
        return bus.send_to_agent(sender="CLAUDE", recipient="ANTIGRAVITY", subject="ECHO", body="same payload",
                                 message_id="msg_same_concurrent", correlation_id=f"corr_concurrent_{i}",
                                 auth_secret="FAKE-REVIEW-KEY")

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(send, range(2)))
    saved = json.loads((Path(iw.INBOX_DIR) / "msg_same_concurrent.json").read_text())
    assert len({corr for mid, corr in receipts}) == 1, {
        "returned_correlations": receipts, "only_persisted_correlation": saved["correlation_id"]}


def test_control_sqlite_concurrent_nonce_admission_is_serialized():
    messages = [envelope(f"msg_sql_{i:04d}") for i in range(24)]
    with ThreadPoolExecutor(max_workers=12) as pool:
        accepted = list(pool.map(iw.verify_message_auth, messages))
        rejected = list(pool.map(iw.verify_message_auth, messages))
    assert all(ok for ok, error in accepted), accepted
    assert all(not ok and "REPLAY_ATTACK" in error for ok, error in rejected), rejected


def test_control_filelock_thread_exclusion(tmp_path):
    guard = threading.Lock()
    active, peak = [0], [0]

    def enter(_):
        with iw.FileLock(str(tmp_path / "one_resource"), timeout_sec=3):
            with guard:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.01)
            with guard:
                active[0] -= 1

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(enter, range(8)))
    assert peak[0] == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows file-sharing semantics")
def test_lock_release_survives_reader_handle(tmp_path):
    target = str(tmp_path / "release_resource")
    owner = iw.FileLock(target, timeout_sec=0)
    assert owner.acquire()
    # Contending FileLock._break_stale_lock opens this same path to read owner metadata.
    with open(owner.lock_path, encoding="utf-8") as reader:
        assert reader.read()
        owner.release()
    next_owner = iw.FileLock(target, timeout_sec=0)
    acquired = next_owner.acquire()
    try:
        assert acquired, "Release silently left a lock owned by the still-live process after a reader blocked unlink"
    finally:
        if acquired:
            next_owner.release()


def test_control_quiet_stdout_does_not_block_heartbeat(monkeypatch):
    from unittest.mock import Mock
    reading, release, heartbeat = threading.Event(), threading.Event(), threading.Event()
    clock = [1000.0]
    monkeypatch.setattr(sw.time, "time", lambda: clock[0])
    monkeypatch.setattr(sw, "ensure_directories", lambda: None)
    supervisor = sw.SupervisedInboxWorker()
    supervisor.lock = Mock(acquire=lambda: True, release=lambda: None)
    monkeypatch.setattr(supervisor, "_setup_signal_handlers", lambda: None)
    monkeypatch.setattr(sw, "get_process_create_time_nt", lambda _: 123)
    proc = Mock(pid=55555, returncode=0)
    proc.poll.return_value = None

    def read():
        reading.set()
        release.wait(2)
        return ""

    proc.stdout.readline = read
    monkeypatch.setattr(sw.subprocess, "Popen", lambda *a, **k: proc)
    update = supervisor._update_heartbeat

    def observe_update():
        update()
        heartbeat.set()

    monkeypatch.setattr(supervisor, "_update_heartbeat", observe_update)
    run = threading.Thread(target=supervisor.run)
    run.start()
    try:
        assert reading.wait(2)
        # Main loop initializes its timer after starting the reader; allow that initialization.
        time.sleep(0.15)
        clock[0] += 6
        assert heartbeat.wait(1), "Quiet stdout prevented the independent heartbeat update"
        data = json.loads(Path(sw.SUPERVISOR_PID_FILE).read_text())
        assert data["last_heartbeat_ts"] == clock[0]
    finally:
        supervisor.shutdown_requested = True
        proc.poll.return_value = 0
        release.set()
        run.join(3)
    assert not run.is_alive()
