"""
tests/test_claude_nexus_review_probes_2026_09_30.py
===================================================
Test-first regression probes for Claude's review findings C1-C7 across
the Nexus Bus (inbox_worker.py, tri_agent_bus.py, nexus_cli.py).

Findings:
- C1: Head-of-line expiry & per-recipient execution lanes
- C2: Uncapped timeout_sec in Antigravity model execution path
- C3: Orphan recovery replay attack & crash recovery policies
- C4: Nonce burned before timestamp and HMAC verification
- C5: PERMISSION_SEEKING_REGEX dead-lettering prompt bodies
- C6: Governance - refuse sender USER on the bus
- C7: nexus_cli non-zero exit on failure, recipient naming on timeout,
      realpath link resolution in validate_path_security,
      creation-time identity check in recover_orphaned_claims
"""

import os
import sys
import time
import uuid
import json
import pytest
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from antigravity.daemons import inbox_worker
from antigravity.daemons import tri_agent_bus
from antigravity.daemons import nexus_cli


@pytest.fixture
def isolated_bus_env(tmp_path, monkeypatch):
    """Provides isolated inbox/outbox/archive/dead_letter and replay store."""
    messages_dir = tmp_path / "messages"
    inbox = messages_dir / "inbox"
    outbox = messages_dir / "outbox"
    archive = messages_dir / "archive"
    dead = messages_dir / "dead_letter"
    backups = messages_dir / "backups"
    for d in [inbox, outbox, archive, dead, backups]:
        d.mkdir(parents=True, exist_ok=True)

    db_path = str(messages_dir / "replay_store.db")

    keys_file = tmp_path / "agent_keys.json"
    keys_data = {
        "keys": {
            "ANTIGRAVITY": "secret_key_antigravity_test",
            "CLAUDE": "secret_key_claude_test",
            "CODEX": "secret_key_codex_test",
            "USER": "secret_key_user_test",
        },
        "token_validity_sec": 300,
        "max_future_skew_sec": 60,
    }
    keys_file.write_text(json.dumps(keys_data), encoding="utf-8")

    monkeypatch.setattr(inbox_worker, "MESSAGES_ROOT", str(messages_dir))
    monkeypatch.setattr(inbox_worker, "INBOX_DIR", str(inbox))
    monkeypatch.setattr(inbox_worker, "OUTBOX_DIR", str(outbox))
    monkeypatch.setattr(inbox_worker, "ARCHIVE_DIR", str(archive))
    monkeypatch.setattr(inbox_worker, "DEAD_LETTER_DIR", str(dead))
    monkeypatch.setattr(inbox_worker, "BACKUPS_DIR", str(backups))
    monkeypatch.setattr(inbox_worker, "REPLAY_DB_PATH", db_path)
    monkeypatch.setattr(inbox_worker, "DEFAULT_EXTERNAL_KEY_PATH", str(keys_file))

    monkeypatch.setattr(tri_agent_bus, "MESSAGES_ROOT", str(messages_dir))
    monkeypatch.setattr(tri_agent_bus, "INBOX_DIR", str(inbox))
    monkeypatch.setattr(tri_agent_bus, "OUTBOX_DIR", str(outbox))
    monkeypatch.setattr(tri_agent_bus, "ARCHIVE_DIR", str(archive))
    monkeypatch.setattr(tri_agent_bus, "DEAD_LETTER_DIR", str(dead))

    monkeypatch.setenv("TRI_AGENT_KEY_FILE", str(keys_file))

    return {
        "root": tmp_path,
        "messages": messages_dir,
        "inbox": inbox,
        "outbox": outbox,
        "dead": dead,
        "keys_file": keys_file,
    }


def _make_signed_envelope(sender, recipient, subject, body, nonce=None, created_at_ist=None, auth_secret=None):
    tz_ist = timezone(timedelta(hours=5, minutes=30))
    ts = created_at_ist or datetime.now(tz_ist).strftime("%Y-%m-%d %H:%M:%S IST")
    n = nonce or uuid.uuid4().hex
    envelope = {
        "message_id": f"msg_{uuid.uuid4().hex[:12]}",
        "correlation_id": f"corr_{uuid.uuid4().hex[:12]}",
        "sender": sender,
        "recipient": recipient,
        "created_at_ist": ts,
        "subject": subject,
        "body": body,
        "status": "CREATED",
        "attempt_count": 0,
        "track": "SHARED",
        "nonce": n,
    }
    key = auth_secret or inbox_worker.get_agent_secret_key(sender)
    envelope["auth_signature"] = inbox_worker.compute_envelope_hmac(envelope, key)
    return envelope


# ==============================================================================
# C4 Probe: verify_message_auth must NOT burn nonces before checking timestamp and HMAC
# ==============================================================================
def test_c4_invalid_or_expired_does_not_burn_nonce(isolated_bus_env):
    """
    C4: verify_message_auth records the nonce BEFORE checking timestamp and HMAC,
    so invalid or expired messages burn nonces.
    Order must be: schema -> timestamp -> HMAC -> record nonce.
    """
    test_nonce = "fixed_nonce_12345678"
    
    # 1. Message with invalid HMAC signature
    bad_msg = _make_signed_envelope("CLAUDE", "ANTIGRAVITY", "PING", "test", nonce=test_nonce, auth_secret="wrong_key")
    ok, err = inbox_worker.verify_message_auth(bad_msg)
    assert not ok
    assert "Cryptographic HMAC signature verification failed" in err or "AUTH_FAILED" in err

    # 2. Subsequent valid message with the SAME nonce should succeed because the first one had bad HMAC!
    good_msg = _make_signed_envelope("CLAUDE", "ANTIGRAVITY", "PING", "test", nonce=test_nonce)
    ok2, err2 = inbox_worker.verify_message_auth(good_msg)
    assert ok2 is True, f"Nonce was prematurely burned by invalid HMAC request: {err2}"

    # 3. Message with expired timestamp
    test_nonce_exp = "expired_nonce_12345678"
    old_time = "2020-01-01 12:00:00 IST"
    exp_msg = _make_signed_envelope("CLAUDE", "ANTIGRAVITY", "PING", "test", nonce=test_nonce_exp, created_at_ist=old_time)
    ok3, err3 = inbox_worker.verify_message_auth(exp_msg)
    assert not ok3
    assert "TIMESTAMP_OUT_OF_BOUNDS" in err3

    # 4. Subsequent valid message with that same nonce should succeed because the expired one should NOT burn it!
    good_msg2 = _make_signed_envelope("CLAUDE", "ANTIGRAVITY", "PING", "test", nonce=test_nonce_exp)
    ok4, err4 = inbox_worker.verify_message_auth(good_msg2)
    assert ok4 is True, f"Nonce was prematurely burned by expired timestamp request: {err4}"


# ==============================================================================
# C2 Probe: Antigravity model path timeout_sec uncapped -> must be clamped to 1-900s
# ==============================================================================
def test_c2_antigravity_model_timeout_clamped(isolated_bus_env, monkeypatch):
    """
    C2: execute_task, Antigravity model path (~line 865): timeout_sec from message is uncapped.
    Must be clamped to 1-900 s, like the peer path.
    """
    recorded_timeouts = []
    def mock_invoke(prompt, timeout_sec=120, chat_only=False):
        recorded_timeouts.append(timeout_sec)
        return {"success": True, "output": "model response", "returncode": 0, "elapsed_sec": 0.1}

    monkeypatch.setattr(inbox_worker, "invoke_antigravity_model", mock_invoke)

    # Test excessive timeout
    msg_large = _make_signed_envelope("CLAUDE", "ANTIGRAVITY", "ANALYZE", "Test body")
    msg_large["timeout_sec"] = 50000
    worker = inbox_worker.InboxWorker()
    worker.execute_task(msg_large)
    assert recorded_timeouts[-1] == 900, f"Expected timeout 50000 to be clamped to 900, got {recorded_timeouts[-1]}"

    # Test sub-minimum timeout
    msg_small = _make_signed_envelope("CLAUDE", "ANTIGRAVITY", "ANALYZE", "Test body 2")
    msg_small["timeout_sec"] = -50
    worker.execute_task(msg_small)
    assert recorded_timeouts[-1] == 1, f"Expected timeout -50 to be clamped to 1, got {recorded_timeouts[-1]}"


# ==============================================================================
# C5 Probe: PERMISSION_SEEKING_REGEX dead-letters prompt body
# ==============================================================================
def test_c5_permission_seeking_body_not_dead_lettered(isolated_bus_env, monkeypatch):
    """
    C5: PERMISSION_SEEKING_REGEX dead-letters any body containing 'please confirm' /
    'should I proceed' etc. Remove it, or log a warning without rejecting.
    """
    def mock_invoke(prompt, timeout_sec=120, chat_only=False):
        return {"success": True, "output": "Acknowledged analysis", "returncode": 0, "elapsed_sec": 0.1}

    monkeypatch.setattr(inbox_worker, "invoke_antigravity_model", mock_invoke)

    # Prompt with "please confirm"
    msg = _make_signed_envelope(
        "CLAUDE", "ANTIGRAVITY", "CHAT",
        "Please confirm the findings in the report and let me know if they look accurate."
    )
    msg_path = str(isolated_bus_env["inbox"] / f"{msg['message_id']}.json")
    inbox_worker.write_json_atomic(msg_path, msg)

    worker = inbox_worker.InboxWorker()
    processed = worker.run_single_pass()
    assert processed == 1

    # Ensure it was NOT routed to dead letter
    dead_files = os.listdir(str(isolated_bus_env["dead"]))
    assert len(dead_files) == 0, f"Message was incorrectly dead-lettered: {dead_files}"

    # Ensure response was written with COMPLETED or non-INCOMPLETE status
    corr_id = msg["correlation_id"]
    out_file = isolated_bus_env["outbox"] / f"{corr_id}_resp.json"
    assert out_file.exists()
    resp = json.loads(out_file.read_text(encoding="utf-8"))
    assert resp["status"] != "INCOMPLETE"
    assert resp["status"] == "COMPLETED"


# ==============================================================================
# C6 Probe: Governance: refuse sender USER on the bus
# ==============================================================================
def test_c6_refuse_sender_user_on_bus(isolated_bus_env):
    """
    C6: Governance: every key is in one file readable by all local processes, so any agent
    can sign as USER. Refuse sender USER on the bus. Owner decisions are taken strictly
    from shared/governance/owner_decisions.jsonl.
    """
    msg = _make_signed_envelope("USER", "ANTIGRAVITY", "PING", "Ping as user")
    is_valid, err = inbox_worker.validate_message_schema(msg)
    assert not is_valid, "Sender USER must be refused on the bus"
    assert "USER" in err and ("UNAUTHORIZED" in err or "REFUSED" in err or "refused" in err.lower())


# ==============================================================================
# C7 Probes:
# - nexus_cli exits non-zero on failure
# - timeout error names actual recipient
# - validate_path_security uses realpath
# - recover_orphaned_claims uses creation-time identity
# ==============================================================================
def test_c7_nexus_cli_nonzero_exit_when_success_false(isolated_bus_env, monkeypatch, capsys):
    """
    C7.1: nexus_cli must exit non-zero when success is false.
    """
    # Polling a nonexistent correlation_id returns success=False
    exit_code = nexus_cli.main(["--recipient", "ANTIGRAVITY", "--poll", "corr_nonexistent_12345"])
    assert exit_code != 0, f"Expected non-zero exit code when poll fails, got {exit_code}"


def test_c7_timeout_error_names_actual_recipient(isolated_bus_env):
    """
    C7.2: The timeout error must name the actual recipient, not always 'Antigravity'.
    """
    res = tri_agent_bus.wait_for_agent_response("corr_test_codex_123", "CODEX", timeout_sec=0.01, poll_interval_sec=0.005)
    assert res["status"] == "TIMED_OUT"
    assert "CODEX" in res["error"], f"Timeout error did not name CODEX: {res['error']}"
    assert "Antigravity did not respond" not in res["error"]

    res_claude = tri_agent_bus.wait_for_agent_response("corr_test_claude_123", "CLAUDE", timeout_sec=0.01, poll_interval_sec=0.005)
    assert res_claude["status"] == "TIMED_OUT"
    assert "CLAUDE" in res_claude["error"], f"Timeout error did not name CLAUDE: {res_claude['error']}"


def test_c7_validate_path_security_realpath_links(tmp_path, monkeypatch):
    """
    C7.3: validate_path_security should use realpath (links/junctions).
    A symlink or junction inside workspace pointing outside must be caught as traversal.
    """
    ws = tmp_path / "workspace"
    ws.mkdir()
    outside = tmp_path / "outside_secret"
    outside.mkdir()
    outside_file = outside / "secret.txt"
    outside_file.write_text("classified")

    link_target = ws / "link_to_outside"
    try:
        os.symlink(str(outside), str(link_target), target_is_directory=True)
    except (OSError, NotImplementedError):
        # On Windows without Developer Mode, junction or skip if symlink cannot be created
        import subprocess
        res = subprocess.run(["cmd", "/c", "mklink", "/J", str(link_target), str(outside)], capture_output=True)
        if res.returncode != 0:
            pytest.skip("Symlink/Junction creation not permitted in this environment")

    # Path through link points to outside file
    sneaky_path = str(link_target / "secret.txt")
    valid, resolved, err = inbox_worker.validate_path_security(sneaky_path, "SHARED", workspace_dir=str(ws))
    assert valid is False, f"Expected validate_path_security to catch symlink escape, but it passed: {resolved}"
    assert "Path traversal" in err or "SECURITY_VIOLATION" in err


def test_c7_recover_orphaned_claims_checks_creation_time(isolated_bus_env, monkeypatch):
    """
    C7.4: recover_orphaned_claims should use the PID creation-time identity.
    If a worker PID matches a currently running process, but creation time does NOT match,
    the claim must be recovered (not skipped).
    """
    # Create an orphaned .claimed file with our own PID, but a bogus older creation time
    my_pid = os.getpid()
    bogus_ct = 1234567890123456  # Differing creation time
    claimed_file = isolated_bus_env["inbox"] / "msg_orphan_c7_test.claimed"
    msg_data = {
        "message_id": "msg_orphan_c7_test",
        "correlation_id": "corr_orphan_c7_test",
        "sender": "CLAUDE",
        "recipient": "ANTIGRAVITY",
        "status": "CLAIMED",
        "attempt_count": 0,
        "worker_pid": my_pid,
        "worker_create_time_nt": bogus_ct,
        "created_at_ist": inbox_worker.get_current_ist(),
    }
    claimed_file.write_text(json.dumps(msg_data), encoding="utf-8")

    # Set mtime back by CLAIM_TIMEOUT_SEC + 10s
    old_time = time.time() - (inbox_worker.CLAIM_TIMEOUT_SEC + 10.0)
    os.utime(str(claimed_file), (old_time, old_time))

    worker = inbox_worker.InboxWorker()
    worker.recover_orphaned_claims()

    # If creation time was checked, worker sees PID does not match creation time and recovers it!
    # In current code without creation time check, it sees _pid_is_running(my_pid) == True and skips it!
    assert not claimed_file.exists(), "Orphaned claim with recycled PID was not recovered!"


# ==============================================================================
# C3 Probes: Orphan recovery & replay attacks
# ==============================================================================
def test_c3_orphan_recovery_explicit_retry_required_policy(isolated_bus_env, monkeypatch):
    """
    C3 Mode 1: 'crash = message lost' explicit policy (RETRY_REQUIRED).
    When an orphan is recovered, outbox emits RETRY_REQUIRED failure and dead-letters
    the claim, so caller resends with a new nonce without risking silent replay attack.
    """
    msg = _make_signed_envelope("CLAUDE", "ANTIGRAVITY", "PING", "crash test 1")
    claimed_file = isolated_bus_env["inbox"] / f"{msg['message_id']}.claimed"
    msg["status"] = "CLAIMED"
    msg["worker_pid"] = 99999999  # Dead pid
    msg["worker_create_time_nt"] = 11111111
    claimed_file.write_text(json.dumps(msg), encoding="utf-8")

    old_time = time.time() - (inbox_worker.CLAIM_TIMEOUT_SEC + 10.0)
    os.utime(str(claimed_file), (old_time, old_time))

    worker = inbox_worker.InboxWorker(orphan_recovery_policy="RETRY_REQUIRED")
    worker.recover_orphaned_claims()

    assert not claimed_file.exists()
    # Check outbox response says RETRY_REQUIRED
    out_file = isolated_bus_env["outbox"] / f"{msg['correlation_id']}_resp.json"
    assert out_file.exists(), "Expected outbox response for RETRY_REQUIRED policy"
    resp = json.loads(out_file.read_text(encoding="utf-8"))
    assert resp["status"] == "FAILED"
    assert "RETRY_REQUIRED" in resp["error"]


def test_c3_orphan_recovery_worker_retry_policy(isolated_bus_env, monkeypatch):
    """
    C3 Mode 2: Worker-recovered retry policy (WORKER_RETRY).
    Replay store tracks message state and allows the recovered message with the same nonce
    to be re-executed exactly once without triggering REPLAY_ATTACK.
    """
    msg = _make_signed_envelope("CLAUDE", "ANTIGRAVITY", "PING", "crash test 2")
    msg_path = str(isolated_bus_env["inbox"] / f"{msg['message_id']}.json")
    inbox_worker.write_json_atomic(msg_path, msg)

    # 1. Claim message and simulate crash
    worker = inbox_worker.InboxWorker(orphan_recovery_policy="WORKER_RETRY")
    claim_res = worker.claim_message(f"{msg['message_id']}.json")
    assert claim_res is not None
    claimed_path, msg_data = claim_res

    # Record nonce as claimed
    replay_store = inbox_worker.DurableReplayStore(isolated_bus_env["messages"] / "replay_store.db")
    replay_store.check_and_record_nonce(msg["nonce"], msg["sender"], msg["created_at_ist"])

    # Worker crashes: set dead worker_pid and old mtime
    msg_data["worker_pid"] = 99999999
    msg_data["worker_create_time_nt"] = 11111111
    inbox_worker.write_json_atomic(claimed_path, msg_data)
    old_time = time.time() - (inbox_worker.CLAIM_TIMEOUT_SEC + 10.0)
    os.utime(claimed_path, (old_time, old_time))

    # 2. Recover orphaned claim
    worker.recover_orphaned_claims()

    # Message is back in inbox as .json with attempt_count=1
    assert not os.path.exists(claimed_path)
    assert os.path.exists(msg_path)

    # 3. Worker re-claims and re-processes recovered message: it must NOT fail as REPLAY_ATTACK!
    processed = worker.run_single_pass()
    assert processed == 1

    out_file = isolated_bus_env["outbox"] / f"{msg['correlation_id']}_resp.json"
    assert out_file.exists()
    resp = json.loads(out_file.read_text(encoding="utf-8"))
    assert resp["status"] == "COMPLETED", f"Expected COMPLETED but got {resp}"
    assert resp.get("output_payload", {}).get("reply") == "PONG"


# ==============================================================================
# C1 Probe: Head-of-line expiry & per-recipient execution lanes
# ==============================================================================
def test_c1_head_of_line_expiry_and_recipient_lanes(isolated_bus_env, monkeypatch):
    """
    C1: A fast PING queued behind a 400s CODEX dispatch must complete without
    being blocked or expiring with TIMESTAMP_OUT_OF_BOUNDS.
    """
    # Simulate a slow CODEX dispatch (e.g. sleeps 0.5s in test, representing 400s)
    codex_started = False
    codex_finished = False

    def mock_codex_detailed(prompt, timeout_sec=300, min_chars=1, chat_only=True):
        nonlocal codex_started, codex_finished
        codex_started = True
        time.sleep(0.4)
        codex_finished = True
        return {"success": True, "output": "Codex completed review", "returncode": 0, "elapsed": 0.4}

    monkeypatch.setattr(tri_agent_bus, "ask_codex_detailed", mock_codex_detailed)

    # 1. Message 1: CODEX dispatch (ordered first in inbox)
    msg_codex = _make_signed_envelope("ANTIGRAVITY", "CODEX", "REVIEW", "Long review task")
    msg_codex["message_id"] = "msg_001_codex_task"
    msg_codex["auth_signature"] = inbox_worker.compute_envelope_hmac(msg_codex, inbox_worker.get_agent_secret_key("ANTIGRAVITY"))
    msg_codex_path = str(isolated_bus_env["inbox"] / f"{msg_codex['message_id']}.json")
    inbox_worker.write_json_atomic(msg_codex_path, msg_codex)

    # 2. Message 2: PING to ANTIGRAVITY (ordered second in inbox)
    msg_ping = _make_signed_envelope("CLAUDE", "ANTIGRAVITY", "PING", "Quick ping")
    msg_ping["message_id"] = "msg_002_ping_task"
    msg_ping["auth_signature"] = inbox_worker.compute_envelope_hmac(msg_ping, inbox_worker.get_agent_secret_key("CLAUDE"))
    msg_ping_path = str(isolated_bus_env["inbox"] / f"{msg_ping['message_id']}.json")
    inbox_worker.write_json_atomic(msg_ping_path, msg_ping)

    # In current serial worker: run_single_pass blocks on CODEX, and if CODEX took 400s,
    # PING timestamp would be expired (> 300s).
    # With per-recipient lanes, ANTIGRAVITY lane processes PING concurrently!
    worker = inbox_worker.InboxWorker()
    
    t0 = time.time()
    # Start worker pass in background or via lane dispatch
    import threading
    worker_thread = threading.Thread(target=worker.run_single_pass, daemon=True)
    worker_thread.start()

    # The PING should complete quickly (well before CODEX finishes at 0.4s)
    ping_resp_file = isolated_bus_env["outbox"] / f"{msg_ping['correlation_id']}_resp.json"
    t_limit = time.time() + 2.0
    ping_completed_early = False
    while time.time() < t_limit:
        if ping_resp_file.exists():
            # Check if ping completed while codex was still running (or early)
            ping_completed_early = not codex_finished
            break
        time.sleep(0.02)

    worker_thread.join(timeout=3.0)

    assert ping_resp_file.exists(), "PING response was not written!"
    ping_resp = json.loads(ping_resp_file.read_text(encoding="utf-8"))
    assert ping_resp["status"] == "COMPLETED"
    assert ping_resp["output_payload"]["reply"] == "PONG"
    assert ping_completed_early is True, "PING was blocked behind CODEX dispatch!"
