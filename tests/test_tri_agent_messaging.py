"""
tests/test_tri_agent_messaging.py - Comprehensive Unit Tests for Tri-Agent Durable Messaging
=============================================================================================
Tests all core requirements and adversarial red-team probes:
1.  test_successful_roundtrip_message
2.  test_malformed_message_rejection
3.  test_duplicate_message_idempotency
4.  test_timeout_returns_failure
5.  test_exit_code_zero_without_task_completion_fails
6.  test_permission_request_classified_incomplete
7.  test_expected_artifact_missing_fails
8.  test_concurrent_edit_conflict_detection
9.  test_worker_restart_and_recovery_of_claimed
10. test_track_crossing_message_rejection
11. test_path_traversal_rejection
12. test_no_command_execution_from_untrusted_fields
13. test_unauthenticated_sender_rejected
14. test_replay_attack_rejected
15. test_message_id_path_injection_blocked
16. test_completion_fabrication_rejected
17. test_write_submission_directory_escape_blocked
18. test_mandatory_occ_on_existing_file
19. test_antigravity_reasoning_pipeline
20. test_outbox_collision_prevention
"""

import os
import sys
import time
import json
import uuid
import shutil
import pytest

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import antigravity.daemons.inbox_worker as iw
import antigravity.daemons.tri_agent_bus as tab
from antigravity.daemons.inbox_worker import (
    InboxWorker,
    validate_message_schema,
    validate_path_security,
    compute_sha256,
    write_json_atomic,
    compute_message_hmac,
    compute_envelope_hmac,
    canonicalize_envelope,
    load_auth_config,
    get_agent_secret_key,
    get_current_ist,
    CLAIM_TIMEOUT_SEC,
    MAX_ATTEMPTS,
    FileLock,
    DurableReplayStore,
)
from antigravity.daemons.tri_agent_bus import (
    send_to_antigravity,
    get_message_status,
    read_antigravity_response,
    wait_for_antigravity_response,
    verify_task_completion,
    safe_atomic_file_write_occ,
    ask_antigravity_detailed,
)
from typing import Any, Dict, Optional


def make_signed_response(
    correlation_id: str,
    status: str = "COMPLETED",
    output_payload: Any = "Task completed successfully",
    artifact_hashes: Optional[Dict[str, str]] = None,
    responder: str = "ANTIGRAVITY",
    error: Optional[str] = None,
    message_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Helper to generate a cryptographically valid, HMAC-signed response envelope."""
    env = {
        "message_id": message_id or f"resp_{uuid.uuid4().hex[:12]}",
        "correlation_id": correlation_id,
        "responder": responder,
        "status": status,
        "created_at_ist": get_current_ist(),
        "completed_at_ist": get_current_ist(),
        "output_payload": output_payload,
        "artifact_hashes": artifact_hashes or {},
        "error": error,
        "nonce": uuid.uuid4().hex
    }
    key = get_agent_secret_key(responder)
    if key:
        env["auth_signature"] = compute_envelope_hmac(env, key)
    return env


def make_signed_request(
    sender: str = "CLAUDE",
    subject: str = "PING",
    body: Any = "ping",
    track: str = "SHARED",
    message_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    source_file: Optional[str] = None,
    expected_response_file: Optional[str] = None,
    pre_task_hash: Optional[str] = None,
    status: str = "CREATED",
    attempt_count: int = 0,
    nonce: Optional[str] = None,
) -> Dict[str, Any]:
    """Helper to generate a cryptographically valid, HMAC-signed inbound request envelope."""
    env = {
        "message_id": message_id or f"msg_{uuid.uuid4().hex[:12]}",
        "correlation_id": correlation_id or f"corr_{uuid.uuid4().hex[:12]}",
        "sender": sender,
        "recipient": "ANTIGRAVITY",
        "track": track,
        "created_at_ist": get_current_ist(),
        "subject": subject,
        "body": body,
        "status": status,
        "attempt_count": attempt_count,
        "nonce": nonce or uuid.uuid4().hex,
        "source_file": source_file,
        "expected_response_file": expected_response_file,
        "pre_task_hash": pre_task_hash,
    }
    key = get_agent_secret_key(sender)
    if key:
        env["auth_signature"] = compute_envelope_hmac(env, key)
    return env


@pytest.fixture
def msg_test_env(monkeypatch):
    """
    Sets up an isolated messaging sandbox within the workspace tree:
    1. Tests run without polluting production inbox/outbox.
    2. Paths inside the workspace pass validate_path_security.
    3. Replay nonces are isolated via dedicated SQLite database.
    4. Mock model hook avoids external API latency for fast unit tests.
    5. Clean teardown with Windows handle release retries.
    """
    sandbox_id = f"test_box_{uuid.uuid4().hex[:8]}"
    sandbox_dir = os.path.join(
        PROJECT_ROOT, "antigravity", "messages", "_test_sandboxes", sandbox_id
    )

    inbox_dir = os.path.join(sandbox_dir, "inbox")
    outbox_dir = os.path.join(sandbox_dir, "outbox")
    archive_dir = os.path.join(sandbox_dir, "archive")
    dead_letter_dir = os.path.join(sandbox_dir, "dead_letter")
    backups_dir = os.path.join(sandbox_dir, "backups")
    replay_db = os.path.join(sandbox_dir, "replay_store.db")

    for d in [inbox_dir, outbox_dir, archive_dir, dead_letter_dir, backups_dir]:
        os.makedirs(d, exist_ok=True)

    # Monkeypatch both inbox_worker and tri_agent_bus directories
    for mod in [iw, tab]:
        monkeypatch.setattr(mod, "MESSAGES_ROOT", sandbox_dir)
        monkeypatch.setattr(mod, "INBOX_DIR", inbox_dir)
        monkeypatch.setattr(mod, "OUTBOX_DIR", outbox_dir)
        monkeypatch.setattr(mod, "ARCHIVE_DIR", archive_dir)
        monkeypatch.setattr(mod, "DEAD_LETTER_DIR", dead_letter_dir)
        monkeypatch.setattr(mod, "BACKUPS_DIR", backups_dir)
        if hasattr(mod, "REPLAY_DB_PATH"):
            monkeypatch.setattr(mod, "REPLAY_DB_PATH", replay_db)

    # Ensure external key lookup finds existing keys
    key_file = r"C:\Users\yashw\.gemini\antigravity\agent_keys.json"
    if os.path.exists(key_file):
        monkeypatch.setenv("TRI_AGENT_KEY_FILE", key_file)

    # Configure fast mock reasoning hook
    def mock_model(prompt: str, timeout_sec: int) -> dict:
        return {
            "success": True,
            "output": f"Antigravity Live Model Analysis: verified {prompt[:30]}",
            "returncode": 0,
            "elapsed_sec": 0.01
        }
    monkeypatch.setattr(iw, "MODEL_DISPATCH_HOOK", mock_model)

    yield {
        "sandbox_dir": sandbox_dir,
        "inbox": inbox_dir,
        "outbox": outbox_dir,
        "archive": archive_dir,
        "dead_letter": dead_letter_dir,
        "backups": backups_dir,
        "replay_db": replay_db,
    }

    # Teardown with retry for Windows file handle release
    for _ in range(5):
        try:
            shutil.rmtree(sandbox_dir)
            break
        except Exception:
            time.sleep(0.05)


# ------------------------------------------------------------------------------
# Test 1: Successful Roundtrip Message
# ------------------------------------------------------------------------------
def test_successful_roundtrip_message(msg_test_env):
    """End-to-end authenticated message from CLAUDE to ANTIGRAVITY (PING), processed, verified."""
    msg_id, corr_id = send_to_antigravity(
        sender="CLAUDE",
        subject="PING",
        body="Health check ping from Claude",
        track="SHARED"
    )

    inbox_file = os.path.join(msg_test_env["inbox"], f"{msg_id}.json")
    assert os.path.exists(inbox_file)

    worker = InboxWorker()
    processed_count = worker.run_single_pass()
    assert processed_count == 1

    res = wait_for_antigravity_response(corr_id, timeout_sec=2.0)
    assert res["success"] is True
    assert res["status"] == "COMPLETED"
    assert res["response"]["output_payload"]["reply"] == "PONG"
    assert res["response"]["output_payload"]["agent"] == "ANTIGRAVITY"

    verification = verify_task_completion(res["response"], expected_correlation_id=corr_id)
    assert verification["verified"] is True
    assert verification["status"] == "COMPLETED"

    archive_file = os.path.join(msg_test_env["archive"], f"{msg_id}.json")
    assert os.path.exists(archive_file)
    assert not os.path.exists(inbox_file)

    status_info = get_message_status(msg_id, corr_id)
    assert status_info["status"] == "COMPLETED"


# ------------------------------------------------------------------------------
# Test 2: Malformed Message Rejection
# ------------------------------------------------------------------------------
def test_malformed_message_rejection(msg_test_env):
    """Message missing required envelope fields routed fail-closed to dead_letter/."""
    malformed_id = "msg_malformed_001"
    raw_file = os.path.join(msg_test_env["inbox"], f"{malformed_id}.json")

    # Missing correlation_id, auth_signature, nonce, etc.
    with open(raw_file, "w", encoding="utf-8") as f:
        json.dump({"message_id": malformed_id, "subject": "PING"}, f)

    worker = InboxWorker()
    processed_count = worker.run_single_pass()
    assert processed_count == 1

    assert not os.path.exists(raw_file)
    dead_file = os.path.join(msg_test_env["dead_letter"], f"{malformed_id}.dead.json")
    assert os.path.exists(dead_file)
    with open(dead_file, "r", encoding="utf-8") as f:
        dead_data = json.load(f)
    assert dead_data["status"] == "FAILED"
    assert "SCHEMA_ERROR" in dead_data["error"]


# ------------------------------------------------------------------------------
# Test 3: Duplicate Message Idempotency
# ------------------------------------------------------------------------------
def test_duplicate_message_idempotency(msg_test_env):
    """Submitting same message_id twice deduplicates cleanly."""
    fixed_msg_id = "msg_idempotent_test_99"
    fixed_corr_id = "corr_idempotent_test_99"

    id1, corr1 = send_to_antigravity(
        sender="CODEX",
        subject="ECHO",
        body="First payload",
        message_id=fixed_msg_id,
        correlation_id=fixed_corr_id
    )
    assert id1 == fixed_msg_id

    id2, corr2 = send_to_antigravity(
        sender="CODEX",
        subject="ECHO",
        body="Conflicting second payload",
        message_id=fixed_msg_id,
        correlation_id=fixed_corr_id
    )
    assert id2 == fixed_msg_id

    inbox_files = [f for f in os.listdir(msg_test_env["inbox"]) if f.endswith(".json")]
    assert len(inbox_files) == 1
    with open(os.path.join(msg_test_env["inbox"], inbox_files[0]), "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["body"] == "First payload"


# ------------------------------------------------------------------------------
# Test 4: Timeout Returns Failure
# ------------------------------------------------------------------------------
def test_timeout_returns_failure(msg_test_env):
    """Poller for unhandled message strictly returns TIMED_OUT and success=False."""
    res = wait_for_antigravity_response(
        correlation_id="corr_non_existent_12345",
        timeout_sec=0.4,
        poll_interval_sec=0.1
    )
    assert res["success"] is False
    assert res["status"] == "TIMED_OUT"
    assert res["response"] is None
    assert "TIMED_OUT" in res["error"]


# ------------------------------------------------------------------------------
# Test 5: Exit Code Zero Without Task Completion Fails
# ------------------------------------------------------------------------------
def test_exit_code_zero_without_task_completion_fails(msg_test_env):
    """Verification logic rejects non-COMPLETED or absent artifacts even if claimed success."""
    res1 = verify_task_completion(None)
    assert res1["verified"] is False
    assert res1["status"] == "INCOMPLETE"

    fake_resp = make_signed_response(
        correlation_id="corr_001",
        status="FAILED",
        output_payload=None,
        error="Failed to parse data"
    )
    res2 = verify_task_completion(fake_resp)
    assert res2["verified"] is False
    assert res2["status"] == "FAILED"

    fake_completed = make_signed_response(
        correlation_id="corr_002",
        status="COMPLETED",
        output_payload="I wrote the file"
    )
    res3 = verify_task_completion(
        fake_completed,
        expected_file="shared/non_existent_artifact_xyz.md"
    )
    assert res3["verified"] is False
    assert res3["status"] == "INCOMPLETE"


# ------------------------------------------------------------------------------
# Test 6: Permission Request Classified Incomplete
# ------------------------------------------------------------------------------
def test_permission_request_classified_incomplete(msg_test_env):
    """Conversational hedging is classified as INCOMPLETE and rejected."""
    hedging_resp = make_signed_response(
        correlation_id="corr_hedging_01",
        status="COMPLETED",
        output_payload="I have drafted the plan. Would you like me to proceed with execution?"
    )
    v_res = verify_task_completion(hedging_resp)
    assert v_res["verified"] is False
    assert v_res["status"] == "INCOMPLETE"
    assert "hedging" in v_res["reason"].lower() or "permission" in v_res["reason"].lower()

    msg_id, corr_id = send_to_antigravity(
        sender="CLAUDE",
        subject="ANALYZE",
        body="Here is the observation. Do you want me to proceed to update the rules?",
        track="SHARED"
    )
    worker = InboxWorker()
    worker.run_single_pass()

    resp = read_antigravity_response(corr_id)
    assert resp is not None
    assert resp["status"] == "INCOMPLETE"
    assert "permission-seeking" in resp["error"]

    dead_file = os.path.join(msg_test_env["dead_letter"], f"{msg_id}.dead.json")
    assert os.path.exists(dead_file)


# ------------------------------------------------------------------------------
# Test 7: Expected Artifact Missing Fails
# ------------------------------------------------------------------------------
def test_expected_artifact_missing_fails(msg_test_env):
    """Artifact checks fail if missing, empty, or hash mismatch."""
    test_artifact_rel = "shared/test_artifact_probe.txt"
    test_artifact_abs = os.path.join(PROJECT_ROOT, test_artifact_rel)

    try:
        with open(test_artifact_abs, "w", encoding="utf-8") as f:
            pass

        resp_empty = make_signed_response(
            correlation_id="corr_art_01",
            status="COMPLETED",
            output_payload="Generated artifact",
            artifact_hashes={}
        )
        res_empty = verify_task_completion(resp_empty, expected_file=test_artifact_rel)
        assert res_empty["verified"] is False
        assert "empty" in res_empty["reason"]

        with open(test_artifact_abs, "w", encoding="utf-8") as f:
            f.write("TASK COMPLETED WITH MARKER: [DONE_PROBE]")

        actual_hash = compute_sha256(test_artifact_abs)

        resp_bad_hash = make_signed_response(
            correlation_id="corr_art_01",
            status="COMPLETED",
            output_payload="Generated artifact",
            artifact_hashes={test_artifact_rel: actual_hash}
        )

        res_bad_hash = verify_task_completion(
            resp_bad_hash,
            expected_file=test_artifact_rel,
            expected_hash="0000000000000000000000000000000000000000000000000000000000000000"
        )
        assert res_bad_hash["verified"] is False
        assert "hash mismatch" in res_bad_hash["reason"]

        res_bad_marker = verify_task_completion(
            resp_bad_hash,
            expected_file=test_artifact_rel,
            completion_marker="[MISSING_MARKER]"
        )
        assert res_bad_marker["verified"] is False
        assert "Completion marker" in res_bad_marker["reason"]

        res_valid = verify_task_completion(
            resp_bad_hash,
            expected_file=test_artifact_rel,
            expected_hash=actual_hash,
            completion_marker="[DONE_PROBE]"
        )
        assert res_valid["verified"] is True
        assert res_valid["status"] == "COMPLETED"

    finally:
        if os.path.exists(test_artifact_abs):
            os.remove(test_artifact_abs)


# ------------------------------------------------------------------------------
# Test 8: Concurrent Edit Conflict Detection (OCC)
# ------------------------------------------------------------------------------
def test_concurrent_edit_conflict_detection(msg_test_env):
    """Optimistic Concurrency Control (OCC) detects hash mismatch and aborts overwrite."""
    rel_path = "shared/reviews/test_occ_sample.txt"
    abs_path = os.path.join(PROJECT_ROOT, rel_path)

    try:
        res1 = safe_atomic_file_write_occ(rel_path, "Version 1: Base State")
        assert res1["success"] is True
        hash_v1 = res1["sha256"]

        with open(abs_path, "w", encoding="utf-8") as f:
            f.write("Version 2: Edited by Claude concurrent")
        hash_v2 = compute_sha256(abs_path)
        assert hash_v2 != hash_v1

        res3 = safe_atomic_file_write_occ(
            rel_path,
            "Version 3: Conflicting write from Codex",
            expected_base_hash=hash_v1
        )
        assert res3["success"] is False
        assert "OCC_CONFLICT" in res3["error"]

        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert content == "Version 2: Edited by Claude concurrent"

        res4 = safe_atomic_file_write_occ(
            rel_path,
            "Version 3: Reconciled write",
            expected_base_hash=hash_v2
        )
        assert res4["success"] is True
        assert res4["backup"] is not None
        assert os.path.exists(res4["backup"])

    finally:
        if os.path.exists(abs_path):
            os.remove(abs_path)


# ------------------------------------------------------------------------------
# Test 9: Worker Restart and Recovery of Claimed Files
# ------------------------------------------------------------------------------
def test_worker_restart_and_recovery_of_claimed(msg_test_env):
    """Worker recovers stale .claimed files or routes to dead letter after max attempts."""
    crashed_id = "msg_crashed_worker_01"
    claimed_file = os.path.join(msg_test_env["inbox"], f"{crashed_id}.claimed")

    envelope = make_signed_request(
        sender="CLAUDE",
        subject="PING",
        body="ping",
        message_id=crashed_id,
        correlation_id="corr_crashed_01",
        status="CLAIMED",
        attempt_count=0
    )
    write_json_atomic(claimed_file, envelope)

    old_time = time.time() - (CLAIM_TIMEOUT_SEC + 10)
    os.utime(claimed_file, (old_time, old_time))

    worker = InboxWorker()
    worker.recover_orphaned_claims()

    reverted_file = os.path.join(msg_test_env["inbox"], f"{crashed_id}.json")
    assert os.path.exists(reverted_file)
    assert not os.path.exists(claimed_file)

    with open(reverted_file, "r", encoding="utf-8") as f:
        reverted_data = json.load(f)
    assert reverted_data["status"] == "CREATED"
    assert reverted_data["attempt_count"] == 1

    reverted_data["attempt_count"] = MAX_ATTEMPTS
    claimed_max = os.path.join(msg_test_env["inbox"], f"{crashed_id}.claimed")
    write_json_atomic(claimed_max, reverted_data)
    os.remove(reverted_file)
    os.utime(claimed_max, (old_time, old_time))

    worker.recover_orphaned_claims()

    dead_file = os.path.join(msg_test_env["dead_letter"], f"{crashed_id}.dead.json")
    assert os.path.exists(dead_file)
    assert not os.path.exists(claimed_max)


# ------------------------------------------------------------------------------
# Test 10: Track Crossing Message Rejection
# ------------------------------------------------------------------------------
def test_track_crossing_message_rejection(msg_test_env):
    """AGENTS.md Rule 11 cross-track references are rejected fail-closed."""
    v1, _, err1 = validate_path_security("shared/track2_liquid/reviews/test.md", "TRACK_1")
    assert v1 is False
    assert "TRACK_ISOLATION_VIOLATION" in err1

    v2, _, err2 = validate_path_security("shared/track1_esm/reviews/test.md", "TRACK_2")
    assert v2 is False
    assert "TRACK_ISOLATION_VIOLATION" in err2

    msg_id, corr_id = send_to_antigravity(
        sender="CLAUDE",
        subject="WRITE_SUBMISSION",
        body={"target_file": "shared/track2_liquid/reviews/leak.md", "content": "illegal cross-track"},
        track="TRACK_1",
        expected_response_file="shared/track2_liquid/reviews/leak.md"
    )

    worker = InboxWorker()
    worker.run_single_pass()

    dead_file = os.path.join(msg_test_env["dead_letter"], f"{msg_id}.dead.json")
    assert os.path.exists(dead_file)
    with open(dead_file, "r", encoding="utf-8") as f:
        dead_data = json.load(f)
    assert "TRACK_ISOLATION_VIOLATION" in dead_data["error"]


# ------------------------------------------------------------------------------
# Test 11: Path Traversal Rejection
# ------------------------------------------------------------------------------
def test_path_traversal_rejection(msg_test_env):
    """Attempts to escape workspace root via ../../ or absolute path are rejected."""
    v1, _, err1 = validate_path_security("../../Windows/System32/cmd.exe", "SHARED")
    assert v1 is False
    assert "SECURITY_VIOLATION" in err1

    v2, _, err2 = validate_path_security("C:\\Windows\\System32\\calc.exe", "SHARED")
    assert v2 is False
    assert "SECURITY_VIOLATION" in err2

    msg = make_signed_request(
        sender="CLAUDE",
        subject="INSPECT_FILE",
        body="C:\\Windows\\System32\\drivers\\etc\\hosts",
        source_file="../../boot.ini",
        message_id="msg_hack_01",
        correlation_id="corr_hack_01"
    )
    is_valid, schema_err = validate_message_schema(msg)
    assert is_valid is False
    assert "SECURITY_VIOLATION" in schema_err


# ------------------------------------------------------------------------------
# Test 12: No Command Execution From Untrusted Fields
# ------------------------------------------------------------------------------
def test_no_command_execution_from_untrusted_fields(msg_test_env):
    """Untrusted shell commands in subject/body handled safely without shell execution."""
    dangerous_subject = "& calc.exe | echo pwned &"
    dangerous_body = "; shutdown /s /t 0 ; rm -rf / ;"

    msg = make_signed_request(
        sender="CLAUDE",
        subject=dangerous_subject,
        body=dangerous_body,
        message_id="msg_probe_injection",
        correlation_id="corr_probe_injection"
    )

    worker = InboxWorker()
    status, payload, artifact_hashes, err = worker.execute_task(msg)

    assert status == "COMPLETED"
    assert err is None


# ------------------------------------------------------------------------------
# Test 13: Unauthenticated Sender Rejection (Audit Finding #2)
# ------------------------------------------------------------------------------
def test_unauthenticated_sender_rejected(msg_test_env):
    """Messages with missing or corrupt HMAC signature fail validation and route to dead letter."""
    msg = make_signed_request(
        sender="CLAUDE",
        subject="PING",
        body="Spoofed message without authentic key",
        message_id="msg_spoofed_01",
        correlation_id="corr_spoofed_01"
    )
    msg["auth_signature"] = "0" * 64
    inbox_file = os.path.join(msg_test_env["inbox"], "msg_spoofed_01.json")
    write_json_atomic(inbox_file, msg)

    worker = InboxWorker()
    worker.run_single_pass()

    assert not os.path.exists(inbox_file)
    dead_file = os.path.join(msg_test_env["dead_letter"], "msg_spoofed_01.dead.json")
    assert os.path.exists(dead_file)
    with open(dead_file, "r", encoding="utf-8") as f:
        dead_data = json.load(f)
    assert "AUTH_FAILED" in dead_data["error"]


# ------------------------------------------------------------------------------
# Test 14: Nonce Replay Attack Rejected (Audit Finding #2)
# ------------------------------------------------------------------------------
def test_replay_attack_rejected(msg_test_env):
    """Reusing the exact same nonce is rejected as a replay attack."""
    fixed_nonce = "fixed_nonce_probe_12345"
    id1, corr1 = send_to_antigravity(
        sender="CLAUDE",
        subject="PING",
        body="Original send",
        nonce=fixed_nonce
    )
    worker = InboxWorker()
    worker.run_single_pass()

    # Second send with same nonce but different message_id
    msg2 = make_signed_request(
        sender="CLAUDE",
        subject="PING",
        body="Replay attempt",
        message_id="msg_replay_probe_02",
        correlation_id="corr_replay_probe_02",
        nonce=fixed_nonce
    )
    inbox_file2 = os.path.join(msg_test_env["inbox"], "msg_replay_probe_02.json")
    write_json_atomic(inbox_file2, msg2)

    worker.run_single_pass()

    dead_file2 = os.path.join(msg_test_env["dead_letter"], "msg_replay_probe_02.dead.json")
    assert os.path.exists(dead_file2)
    with open(dead_file2, "r", encoding="utf-8") as f:
        dead_data = json.load(f)
    assert "REPLAY_ATTACK" in dead_data["error"]


# ------------------------------------------------------------------------------
# Test 15: Message ID Path Injection Blocked (Audit Finding #3)
# ------------------------------------------------------------------------------
def test_message_id_path_injection_blocked(msg_test_env):
    """Path injection via message_id is strictly blocked by regex and does not escape."""
    with pytest.raises(ValueError, match="Invalid message_id"):
        send_to_antigravity(
            sender="CLAUDE",
            subject="PING",
            body="probe",
            message_id="../../../shared/evil_file"
        )

    # Even if an attacker drops a file named with path traversal characters directly in inbox:
    raw_bad_name = ".._.._shared_hack.json"
    inbox_bad = os.path.join(msg_test_env["inbox"], raw_bad_name)
    with open(inbox_bad, "w", encoding="utf-8") as f:
        json.dump({"message_id": raw_bad_name, "subject": "PING"}, f)

    worker = InboxWorker()
    claim_res = worker.claim_message(raw_bad_name)
    assert claim_res is None


# ------------------------------------------------------------------------------
# Test 16: Completion Fabrication Rejected (Audit Finding #4)
# ------------------------------------------------------------------------------
def test_completion_fabrication_rejected(msg_test_env):
    """Audit Finding #4 probes: Bare status, wrong responder, correlation mismatch, untouched file."""
    # Probe A: Bare {"status": "COMPLETED"}
    res_bare = verify_task_completion({"status": "COMPLETED"})
    assert res_bare["verified"] is False
    assert "missing required field" in res_bare["reason"]

    # Probe B: Attacker responder
    fake_attacker = make_signed_response(
        correlation_id="corr_test_01",
        responder="ATTACKER",
        status="COMPLETED"
    )
    fake_attacker["auth_signature"] = "attacker_signature_test"
    res_att = verify_task_completion(fake_attacker)
    assert res_att["verified"] is False
    assert "Unauthorized responder" in res_att["reason"]

    # Probe C: Correlation ID mismatch
    valid_env = make_signed_response(correlation_id="corr_test_02")
    res_corr = verify_task_completion(valid_env, expected_correlation_id="corr_expected_different")
    assert res_corr["verified"] is False
    assert "Correlation ID mismatch" in res_corr["reason"]

    # Probe D: Claiming pre-existing AGENTS.md was completed even though never modified
    agents_rel = "AGENTS.md"
    agents_abs = os.path.join(PROJECT_ROOT, agents_rel)
    agents_hash = compute_sha256(agents_abs)

    valid_env_untouched = make_signed_response(
        correlation_id="corr_test_02",
        artifact_hashes={agents_rel: agents_hash}
    )
    res_untouched = verify_task_completion(
        valid_env_untouched,
        expected_file=agents_rel,
        pre_task_hash=agents_hash  # Hash did not change!
    )
    assert res_untouched["verified"] is False
    assert "never modified" in res_untouched["reason"]


# ------------------------------------------------------------------------------
# Test 17: Write Submission Directory Escape Blocked (Audit High Finding)
# ------------------------------------------------------------------------------
def test_write_submission_directory_escape_blocked(msg_test_env):
    """WRITE_SUBMISSION cannot modify files outside designated review directories."""
    msg = make_signed_request(
        sender="CLAUDE",
        subject="WRITE_SUBMISSION",
        body={
            "target_file": "AGENTS.md",
            "content": "# Tampered rules"
        },
        message_id="msg_sub_escape_01",
        correlation_id="corr_sub_escape_01"
    )

    worker = InboxWorker()
    status, payload, hashes, err = worker.execute_task(msg)
    assert status == "FAILED"
    assert "DIRECTORY_SECURITY_VIOLATION" in err


# ------------------------------------------------------------------------------
# Test 18: Mandatory OCC on Existing File (Audit High Finding)
# ------------------------------------------------------------------------------
def test_mandatory_occ_on_existing_file(msg_test_env):
    """Modifying existing file via WRITE_SUBMISSION without expected_base_hash fails OCC_REQUIRED."""
    target_rel = "shared/reviews/test_occ_mandate.md"
    target_abs = os.path.join(PROJECT_ROOT, target_rel)

    try:
        os.makedirs(os.path.dirname(target_abs), exist_ok=True)
        with open(target_abs, "w", encoding="utf-8") as f:
            f.write("Initial state")

        msg = make_signed_request(
            sender="CLAUDE",
            subject="WRITE_SUBMISSION",
            body={
                "target_file": target_rel,
                "content": "Overwrite without base hash"
            },
            message_id="msg_occ_mandate_01",
            correlation_id="corr_occ_mandate_01"
        )

        worker = InboxWorker()
        status, payload, hashes, err = worker.execute_task(msg)
        assert status == "FAILED"
        assert "OCC_REQUIRED" in err

    finally:
        if os.path.exists(target_abs):
            os.remove(target_abs)


# ------------------------------------------------------------------------------
# Test 19: Antigravity Reasoning Pipeline (Audit Finding #1)
# ------------------------------------------------------------------------------
def test_antigravity_reasoning_pipeline(msg_test_env):
    """Analytical queries invoke Antigravity model reasoning rather than static responses."""
    msg_id, corr_id = send_to_antigravity(
        sender="CLAUDE",
        subject="AUDIT_MOBIKWIK_SETUP",
        body="Audit MOBIKWIK Day 3 target and stop calibration.",
        track="TRACK_1"
    )

    worker = InboxWorker()
    worker.run_single_pass()

    res = wait_for_antigravity_response(corr_id, timeout_sec=2.0)
    assert res["success"] is True
    assert res["status"] == "COMPLETED"
    assert "Antigravity Live Model Analysis" in res["response"]["output_payload"]["model_response"]

    # Verify ask_antigravity_detailed direct dispatch
    direct_res = ask_antigravity_detailed("Audit prompt")
    assert direct_res["success"] is True
    assert "Antigravity Live Model Analysis" in direct_res["output"]


# ------------------------------------------------------------------------------
# Test 20: Outbox Collision Prevention (Audit High Finding)
# ------------------------------------------------------------------------------
def test_outbox_collision_prevention(msg_test_env):
    """Worker detects and prevents overwriting existing responses with different correlation."""
    corr_id = "corr_collision_test_01"
    safe_outbox_file = os.path.join(msg_test_env["outbox"], f"{corr_id}_resp.json")

    # Seed prior completed response
    prior_resp = make_signed_response(
        correlation_id=corr_id,
        status="COMPLETED",
        output_payload="Prior completed work"
    )
    write_json_atomic(safe_outbox_file, prior_resp)

    # Attempt to process a new message with identical correlation_id
    msg_id, _ = send_to_antigravity(
        sender="CODEX",
        subject="PING",
        body="Colliding correlation",
        correlation_id=corr_id
    )

    worker = InboxWorker()
    worker.run_single_pass()

    # Must be routed to dead letter due to collision
    dead_file = os.path.join(msg_test_env["dead_letter"], f"{msg_id}.dead.json")
    assert os.path.exists(dead_file)
    with open(dead_file, "r", encoding="utf-8") as f:
        dead_data = json.load(f)
    assert "CORRELATION_ID_COLLISION" in dead_data["error"]


# ------------------------------------------------------------------------------
# Test 21: Full Envelope Tampering Rejection
# ------------------------------------------------------------------------------
def test_full_envelope_tampering_rejected(msg_test_env):
    """Tampering with any envelope field (e.g., track or subject) invalidates signature."""
    msg = make_signed_request(
        sender="CLAUDE",
        subject="PING",
        body="Original body",
        track="SHARED",
        message_id="msg_tamper_env_01",
        correlation_id="corr_tamper_env_01"
    )
    # Attacker alters track or subject after HMAC was calculated
    msg["track"] = "TRACK_1"

    inbox_file = os.path.join(msg_test_env["inbox"], "msg_tamper_env_01.json")
    write_json_atomic(inbox_file, msg)

    worker = InboxWorker()
    worker.run_single_pass()

    dead_file = os.path.join(msg_test_env["dead_letter"], "msg_tamper_env_01.dead.json")
    assert os.path.exists(dead_file)
    with open(dead_file, "r", encoding="utf-8") as f:
        dead_data = json.load(f)
    assert "AUTH_FAILED" in dead_data["error"]


# ------------------------------------------------------------------------------
# Test 22: Response Envelope HMAC Tampering Rejected
# ------------------------------------------------------------------------------
def test_response_envelope_hmac_tampering_rejected(msg_test_env):
    """Tampering with response output payload invalidates HMAC verification in bus."""
    resp = make_signed_response(
        correlation_id="corr_tamp_resp_01",
        output_payload="Legitimate calculation output"
    )
    # Tamper with output payload
    resp["output_payload"] = "Tampered output by attacker"

    v_res = verify_task_completion(resp, expected_correlation_id="corr_tamp_resp_01")
    assert v_res["verified"] is False
    assert v_res["status"] == "AUTH_FAILED"
    assert "HMAC" in v_res["reason"]


# ------------------------------------------------------------------------------
# Test 23: Wait For Antigravity Response Mandatory Verification Failure
# ------------------------------------------------------------------------------
def test_wait_for_antigravity_response_mandatory_verification_failure(msg_test_env):
    """wait_for_antigravity_response strictly returns success=False if response is forged."""
    corr_id = "corr_mand_verif_01"
    outbox_file = os.path.join(msg_test_env["outbox"], f"{corr_id}_resp.json")

    # Forge response with invalid HMAC
    forged_resp = make_signed_response(correlation_id=corr_id)
    forged_resp["auth_signature"] = "bad" * 16
    write_json_atomic(outbox_file, forged_resp)

    res = wait_for_antigravity_response(corr_id, timeout_sec=0.5, poll_interval_sec=0.1)
    assert res["success"] is False
    assert res["status"] == "AUTH_FAILED"
    assert "HMAC" in res["error"] or "signature" in res["error"].lower()


# ------------------------------------------------------------------------------
# Test 24: Editing Task Missing Expected Response File Rejected
# ------------------------------------------------------------------------------
def test_editing_task_missing_expected_response_file_rejected(msg_test_env):
    """WRITE_SUBMISSION without expected_response_file fails EXPECTED_OUTPUT_REQUIRED."""
    msg = make_signed_request(
        sender="CLAUDE",
        subject="WRITE_SUBMISSION",
        body={"target_file": "shared/reviews/test.md", "content": "text"},
        message_id="msg_no_exp_file_01",
        correlation_id="corr_no_exp_file_01",
        expected_response_file=None
    )
    is_valid, err = validate_message_schema(msg)
    assert is_valid is False
    assert "EXPECTED_OUTPUT_REQUIRED" in err


# ------------------------------------------------------------------------------
# Test 25: Editing Task Existing File Missing Pre-Task Hash Rejected
# ------------------------------------------------------------------------------
def test_editing_task_existing_file_missing_pre_task_hash_rejected(msg_test_env):
    """Editing existing file without pre_task_hash fails PRE_TASK_HASH_REQUIRED."""
    probe_rel = "shared/reviews/existing_file_probe.md"
    probe_abs = os.path.join(PROJECT_ROOT, probe_rel)

    try:
        os.makedirs(os.path.dirname(probe_abs), exist_ok=True)
        with open(probe_abs, "w", encoding="utf-8") as f:
            f.write("Base content")

        msg = make_signed_request(
            sender="CLAUDE",
            subject="WRITE_SUBMISSION",
            body={"target_file": probe_rel, "content": "Updated content"},
            expected_response_file=probe_rel,
            pre_task_hash=None,
            message_id="msg_no_pre_hash_01",
            correlation_id="corr_no_pre_hash_01"
        )
        is_valid, err = validate_message_schema(msg)
        assert is_valid is False
        assert "PRE_TASK_HASH_REQUIRED" in err

    finally:
        if os.path.exists(probe_abs):
            os.remove(probe_abs)


# ------------------------------------------------------------------------------
# Test 26: Durable Replay Store SQLite Persistence
# ------------------------------------------------------------------------------
def test_durable_replay_store_sqlite_persistence(msg_test_env):
    """Nonce replay check persists across store restarts via SQLite WAL database."""
    db_file = msg_test_env["replay_db"]
    store1 = DurableReplayStore(db_file)
    test_nonce = "persisted_nonce_probe_abc123"

    ok1, err1 = store1.check_and_record_nonce(test_nonce, "CLAUDE", get_current_ist())
    assert ok1 is True
    assert err1 is None

    # Simulate process crash and restart by creating a new store instance with same db
    store2 = DurableReplayStore(db_file)
    ok2, err2 = store2.check_and_record_nonce(test_nonce, "CLAUDE", get_current_ist())
    assert ok2 is False
    assert "REPLAY_ATTACK" in err2


# ------------------------------------------------------------------------------
# Test 27: FileLock Atomic Mutual Exclusion
# ------------------------------------------------------------------------------
def test_file_lock_atomic_mutual_exclusion(msg_test_env):
    """FileLock enforces strict mutual exclusion and handles stale lock breaking."""
    target = os.path.join(msg_test_env["sandbox_dir"], "test_lock_target.txt")
    lock1 = FileLock(target, timeout_sec=0.2)
    assert lock1.acquire() is True

    # Second lock fails to acquire while lock1 holds it
    lock2 = FileLock(target, timeout_sec=0.2)
    assert lock2.acquire() is False

    lock1.release()

    # After release, lock2 acquires successfully
    assert lock2.acquire() is True
    lock2.release()

    # Stale lock breaking verification
    # Simulate a crashed process holding a stale lock:
    # In an actual crash, the OS closes file descriptors while leaving the lockfile on disk.
    stale_lock = FileLock(target, timeout_sec=0.5, stale_sec=0.1)
    stale_lock.acquire()
    if stale_lock.fd is not None:
        os.close(stale_lock.fd)
        stale_lock.fd = None
    old_time = time.time() - 10
    os.utime(stale_lock.lock_path, (old_time, old_time))

    breaker_lock = FileLock(target, timeout_sec=0.5, stale_sec=0.1)
    assert breaker_lock.acquire() is True
    breaker_lock.release()
