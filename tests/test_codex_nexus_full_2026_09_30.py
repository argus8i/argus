"""Broader Nexus adversarial probes at 8db69a1. Temporary files and fake keys only."""
import copy
import json
import os
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = os.environ.get("ARGUS_NEXUS_REVIEW_ROOT", str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, PROJECT_ROOT)
from antigravity.daemons import inbox_worker as iw, tri_agent_bus as bus, supervised_inbox_worker as sw


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    for module in (iw, bus):
        monkeypatch.setattr(module, "MESSAGES_ROOT", str(tmp_path), raising=False)
        for attr, name in (("INBOX_DIR", "inbox"), ("OUTBOX_DIR", "outbox"),
                           ("ARCHIVE_DIR", "archive"), ("DEAD_LETTER_DIR", "dead")):
            path = tmp_path / name
            path.mkdir(exist_ok=True)
            monkeypatch.setattr(module, attr, str(path))
        monkeypatch.setattr(module, "get_agent_secret_key", lambda _: "synthetic-key")
    monkeypatch.setattr(iw, "load_auth_config", lambda: {"token_validity_sec": 300})
    monkeypatch.setattr(iw, "_pid_is_running", lambda *a, **k: False)
    monkeypatch.setattr(sw, "SUPERVISOR_PID_FILE", str(tmp_path / "supervisor.pid"))
    monkeypatch.setattr(sw, "SUPERVISOR_LOCK_FILE", str(tmp_path / "supervisor"))


def request(**kwargs):
    mid, corr = bus.send_to_agent(sender="CODEX", recipient="ANTIGRAVITY", subject="ECHO", body="test", **kwargs)
    path = Path(iw.INBOX_DIR) / (mid + ".json")
    return mid, corr, json.loads(path.read_text()), path


def test_healthy_supervisor_lock_cannot_be_stolen_after_sixty_seconds(tmp_path, monkeypatch):
    lock = iw.FileLock(str(tmp_path / "supervisor"), timeout_sec=0)
    Path(lock.lock_path).write_text(json.dumps({"pid": 123, "create_time_nt": 456}))
    os.utime(lock.lock_path, (time.time() - 121, time.time() - 121))
    monkeypatch.setattr(iw, "_pid_is_running", lambda *a, **k: True)
    assert lock._break_stale_lock() is False
    assert Path(lock.lock_path).exists()


def test_bad_signature_cannot_consume_valid_requests_nonce():
    _, _, valid, _ = request()
    invalid = dict(valid, auth_signature="0" * 64)
    assert iw.verify_message_auth(invalid)[0] is False
    ok, error = iw.verify_message_auth(valid)
    assert ok, error


def test_orphan_recovery_preserves_sender_signature(monkeypatch):
    mid, _, msg, path = request()
    claimed = path.with_suffix(".claimed")
    path.rename(claimed)
    msg.update(status="CLAIMED", worker_pid=55555)
    iw.write_json_atomic(str(claimed), msg)
    os.utime(claimed, (time.time() - 2000, time.time() - 2000))
    iw.InboxWorker().recover_orphaned_claims()
    recovered = json.loads((Path(iw.INBOX_DIR) / (mid + ".json")).read_text())
    assert recovered["auth_signature"] == iw.compute_envelope_hmac(recovered, "synthetic-key")


def test_authenticated_crashed_request_is_retryable(monkeypatch):
    mid, _, msg, path = request()
    assert iw.verify_message_auth(msg)[0]
    claimed = path.with_suffix(".claimed")
    path.rename(claimed)
    msg.update(status="CLAIMED", worker_pid=55555)
    iw.write_json_atomic(str(claimed), msg)
    os.utime(claimed, (time.time() - 2000, time.time() - 2000))
    iw.InboxWorker().recover_orphaned_claims()
    recovered = json.loads((Path(iw.INBOX_DIR) / (mid + ".json")).read_text())
    ok, error = iw.verify_message_auth(recovered)
    assert ok, error


def test_duplicate_message_returns_original_correlation():
    mid, corr, _, _ = request(message_id="duplicate_123", correlation_id="original_123")
    _, second = bus.send_to_agent(sender="CODEX", recipient="ANTIGRAVITY", subject="ECHO", body="different",
                                  message_id=mid, correlation_id="different_123")
    assert second == corr, second


def test_duplicate_claimed_message_is_not_reenqueued():
    mid, _, _, _ = request(message_id="duplicate_123", correlation_id="original_123")
    assert iw.InboxWorker().claim_message(mid + ".json")
    bus.send_to_agent(sender="CODEX", recipient="ANTIGRAVITY", subject="ECHO", body="different",
                      message_id=mid, correlation_id="different_123")
    assert not (Path(iw.INBOX_DIR) / (mid + ".json")).exists()


def test_breaker_cooldown_can_expire_without_manual_reset(tmp_path):
    payload = {"status": "CIRCUIT_BREAKER_TRIPPED", "tripped_at_ts": time.time() - 1000, "cooldown_sec": 900}
    Path(sw._get_breaker_file()).write_text(json.dumps(payload))
    Path(sw.SUPERVISOR_PID_FILE).write_text(json.dumps(payload))
    assert sw.get_status()["status"] != "CIRCUIT_BREAKER_TRIPPED"


def test_empty_signed_reply_does_not_count_as_completed():
    response = {"message_id": "response_123", "correlation_id": "original_123", "responder": "ANTIGRAVITY",
                "status": "COMPLETED", "completed_at_ist": iw.get_current_ist(), "output_payload": None,
                "artifact_hashes": {}, "nonce": "synthetic_nonce_123"}
    response["auth_signature"] = iw.compute_envelope_hmac(response, "synthetic-key")
    assert bus.verify_task_completion(response)["verified"] is False


def test_malformed_lock_record_does_not_crash_acquisition(tmp_path):
    lock = iw.FileLock(str(tmp_path / "badlock"), timeout_sec=0)
    Path(lock.lock_path).write_text("{")
    assert lock.acquire() is False


def test_stop_without_creation_time_preserves_unverified_live_owner(tmp_path, monkeypatch):
    payload = {"supervisor_pid": 123, "status": "RUNNING"}
    pidfile = Path(sw.SUPERVISOR_PID_FILE)
    lockfile = Path(sw.SUPERVISOR_LOCK_FILE + ".lock")
    pidfile.write_text(json.dumps(payload))
    lockfile.write_text("owner")
    monkeypatch.setattr(sw, "get_status", lambda: {"status": "RUNNING", "details": payload})
    monkeypatch.setattr(sw, "_pid_is_running", lambda *a, **k: True)
    sw.stop_daemon()
    assert lockfile.exists() and pidfile.exists()


def test_quoted_permission_phrase_is_not_transport_failure():
    response = {"message_id": "response_123", "correlation_id": "original_123", "responder": "ANTIGRAVITY",
                "status": "COMPLETED", "completed_at_ist": iw.get_current_ist(),
                "output_payload": 'Bug confirmed: the old prompt contains "should I proceed". Remove that phrase.',
                "artifact_hashes": {}, "nonce": "synthetic_nonce_123"}
    response["auth_signature"] = iw.compute_envelope_hmac(response, "synthetic-key")
    assert bus.verify_task_completion(response)["verified"] is True
