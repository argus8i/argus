# -*- coding: utf-8 -*-
"""
tests/test_codex_nexus_followup_probes.py
Comprehensive regression probes covering all findings from OpenAI Codex's peer reviews:
1. P1: FileLock Successor Safety under Interleaving & Empty Abandoned Break Token Recovery
2. P1: Cross-Process Publication Race (Producer A pauses after QUEUED, Producer B publishes & claims, Producer A resumes -> aborted)
3. P1: Permanent Admission Retention & Terminal State Protection against Recovery Reversal
4. P2: Pre-Upgrade Recovery Claims & Leftover Claim Reconciled without Resurrection
5. P2: Full Identity Contract Validation (Sender, Recipient, Subject, Payload)
6. P2: Terminal State Rejection & Database Error Fail-Closed in Claim
7. P2: Durable State Updates on Completion & Dead-Lettering with Claim Failure Abort
8. P2: Strict SemVer 2.0.0 Floor Enforcement & Pre-Release Rejection (Fails Closed with RuntimeError)
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from antigravity.daemons.inbox_worker import (
    DurableAdmissionStore,
    FileLock,
    InboxWorker,
    compute_envelope_hmac,
    compute_payload_hash,
    get_current_ist,
    get_process_create_time_nt,
    validate_message_schema,
    verify_message_auth,
    write_json_atomic,
)
from antigravity.daemons.tri_agent_bus import (
    get_codex_bin,
    send_to_agent,
)


# ==============================================================================
# PROBE 1: FileLock Successor Safety & Empty Abandoned Break Token Recovery
# ==============================================================================
def test_filelock_successor_safety_and_empty_break_token_recovery():
    """
    Finding P1 & P2:
    1. An old empty abandoned .break token must be detected, cleaned up, and not block takeover.
    2. When contender B breaks stale lock and acquires, contender C must NOT unlink contender B's live lock.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        target_path = os.path.join(tmpdir, "resource.json")
        lock_path = target_path + ".lock"
        break_path = lock_path + ".break"

        # Step 1: Create a released lock representing Lock A
        stale_data = {
            "pid": 999999,
            "owner": "DEAD_WORKER",
            "created_at": time.time() - 100.0,
            "create_time_nt": 123456789,
            "released": True,
        }
        with open(lock_path, "w", encoding="utf-8") as f:
            json.dump(stale_data, f)

        # Step 2: Create an empty abandoned .break token older than 0.5s
        with open(break_path, "w", encoding="utf-8") as f:
            pass  # 0 bytes
        old_mtime = time.time() - 2.0
        os.utime(break_path, (old_mtime, old_mtime))
        assert os.path.getsize(break_path) == 0

        # Step 3: Contender B attempts to break stale lock.
        # It must clean up the empty abandoned break token and acquire successfully!
        lock_b = FileLock(target_path, timeout_sec=2.0)
        acquired_b = lock_b.acquire()
        assert acquired_b is True, "Contender B was blocked by empty abandoned break token!"

        # Verify B owns the lock
        with open(lock_path, "r", encoding="utf-8") as f:
            b_data = json.load(f)
        assert b_data["pid"] == os.getpid()
        assert b_data["released"] is False

        # Step 4: Contender C attempts _break_stale_lock on B's active lock
        lock_c = FileLock(target_path, timeout_sec=0.1)
        broke_c = lock_c._break_stale_lock()
        assert broke_c is False, "Contender C improperly broke live Contender B's lock!"

        # Verify B's lock file is still intact and held
        assert os.path.exists(lock_path)
        with open(lock_path, "r", encoding="utf-8") as f:
            intact_data = json.load(f)
        assert intact_data["pid"] == os.getpid()
        assert intact_data["released"] is False

        lock_b.release()


# ==============================================================================
# PROBE 2: Cross-Process Publication Race Interleaving
# ==============================================================================
def test_cross_process_publication_race_interleaving():
    """
    Finding P1: Producer A passes admission and pauses.
    Producer B publishes, and worker claims B's message.
    Producer A resumes and attempts to write to inbox.
    Cross-process FileLock and admission state check must cause Producer A to abort
    and NOT recreate the inbox file or overwrite the claimed message.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "admissions.db")
        inbox_dir = os.path.join(tmpdir, "inbox")
        outbox_dir = os.path.join(tmpdir, "outbox")
        dead_dir = os.path.join(tmpdir, "dead")
        archive_dir = os.path.join(tmpdir, "archive")
        os.makedirs(inbox_dir, exist_ok=True)
        os.makedirs(outbox_dir, exist_ok=True)
        os.makedirs(dead_dir, exist_ok=True)
        os.makedirs(archive_dir, exist_ok=True)

        store = DurableAdmissionStore(db_path)
        msg_id = "MSG-RACE-001"
        corr_a = "CORR-A-001"
        corr_b = "CORR-B-001"
        body = {"task": "race_task"}
        p_hash = compute_payload_hash(body)

        with patch("antigravity.daemons.inbox_worker.INBOX_DIR", inbox_dir), \
             patch("antigravity.daemons.inbox_worker.OUTBOX_DIR", outbox_dir), \
             patch("antigravity.daemons.inbox_worker.DEAD_LETTER_DIR", dead_dir), \
             patch("antigravity.daemons.inbox_worker.ARCHIVE_DIR", archive_dir), \
             patch("antigravity.daemons.inbox_worker.get_default_admission_store", return_value=store), \
             patch("antigravity.daemons.tri_agent_bus.INBOX_DIR", inbox_dir), \
             patch("antigravity.daemons.tri_agent_bus.OUTBOX_DIR", outbox_dir), \
             patch("antigravity.daemons.tri_agent_bus.get_default_admission_store", return_value=store), \
             patch("antigravity.daemons.tri_agent_bus.get_agent_secret_key", return_value=b"secret_key_32_bytes_long_123456"), \
             patch("antigravity.daemons.inbox_worker.get_agent_secret_key", return_value=b"secret_key_32_bytes_long_123456"):

            # 1. Producer A admits message (gets QUEUED) and prepares envelope, then pauses
            is_new_a, winning_corr_a, err_a, st_a = store.admit_submission(
                msg_id, corr_a, p_hash, sender="CLAUDE", recipient="ANTIGRAVITY", subject="ECHO", timestamp_ist="2026-10-01T09:00:00+05:30"
            )
            assert is_new_a is True
            assert winning_corr_a == corr_a

            # 2. Producer B arrives concurrently with same message ID
            # Producer B admits (returns winning_corr_a) and publishes
            send_to_agent(
                sender="CLAUDE", recipient="ANTIGRAVITY", subject="ECHO", body=body,
                message_id=msg_id, correlation_id=corr_b, nonce="NONCE-B-001"
            )

            inbox_file = os.path.join(inbox_dir, f"{msg_id}.json")
            assert os.path.exists(inbox_file), "Producer B failed to write inbox file"

            # Worker claims Producer B's file
            worker = InboxWorker()
            worker.db = store
            claim_res = worker.claim_message(f"{msg_id}.json")
            assert claim_res is not None
            claimed_path, claimed_data = claim_res
            assert claimed_data.get("nonce") == "NONCE-B-001"
            assert store.get_message_state(msg_id) == "CLAIMED"
            assert not os.path.exists(inbox_file)

            # 3. Now Producer A resumes and calls write_json_atomic with nonce "NONCE-A-001"
            env_a = {
                "message_id": msg_id, "correlation_id": winning_corr_a, "sender": "CLAUDE",
                "recipient": "ANTIGRAVITY", "subject": "ECHO", "body": body, "nonce": "NONCE-A-001",
                "status": "CREATED"
            }
            write_json_atomic(inbox_file, env_a)

            # Verification: Producer A's write was aborted!
            # inbox_file was NOT recreated with NONCE-A
            assert not os.path.exists(inbox_file), "Producer A improperly recreated inbox file after claim!"
            # claimed file remains owned by B
            with open(claimed_path, "r", encoding="utf-8") as f:
                current_claimed = json.load(f)
            assert current_claimed.get("nonce") == "NONCE-B-001"


# ==============================================================================
# PROBE 3: Permanent Retention & Terminal State Immunity from Recovery Reversal
# ==============================================================================
def test_permanent_retention_and_terminal_immunity_from_recovery():
    """
    Finding P1:
    1. message_admissions are permanent and never deleted after 600s TTL.
    2. mark_message_recovering must strictly refuse to transition COMPLETED or DEAD states to RECOVERING.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "admissions.db")
        store = DurableAdmissionStore(db_path)

        msg_id_comp = "MSG-COMP-001"
        msg_id_dead = "MSG-DEAD-001"
        p_hash = compute_payload_hash("data")

        # Admit and complete msg_id_comp
        store.admit_submission(msg_id_comp, "CORR-01", p_hash, "CODEX", "ANTIGRAVITY", "sub", "2026-10-01T09:00:00+05:30")
        assert store.mark_message_completed(msg_id_comp) is True
        assert store.get_message_state(msg_id_comp) == "COMPLETED"

        # Admit and dead-letter msg_id_dead
        store.admit_submission(msg_id_dead, "CORR-02", p_hash, "CODEX", "ANTIGRAVITY", "sub", "2026-10-01T09:00:00+05:30")
        assert store.mark_message_dead(msg_id_dead, error="SIMULATED") is True
        assert store.get_message_state(msg_id_dead) == "DEAD"

        # Insert expired nonce (700s ago)
        import sqlite3
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "INSERT INTO seen_nonces (nonce, sender, timestamp_ist, recorded_at, message_id, state) VALUES (?, ?, ?, ?, ?, 'COMPLETED')",
                ("OLD-NONCE", "CODEX", "2026-10-01T08:00:00+05:30", time.time() - 700.0, msg_id_comp)
            )
            conn.commit()

        # Trigger cleanup via new nonce record
        store.check_and_record_nonce("NEW-NONCE", "CODEX", "2026-10-01T09:10:00+05:30", message_id="MSG-OTHER")

        # Verify old nonce pruned, but terminal message_admissions strictly retained!
        assert store.get_message_state(msg_id_comp) == "COMPLETED"
        assert store.get_message_state(msg_id_dead) == "DEAD"

        # Attempt to reverse COMPLETED state via mark_message_recovering
        auth_comp = store.mark_message_recovering(msg_id_comp, nonce="RETRY-NONCE-1")
        assert auth_comp is False, "mark_message_recovering reversed COMPLETED state!"
        assert store.get_message_state(msg_id_comp) == "COMPLETED"

        # Attempt to reverse DEAD state via mark_message_recovering
        auth_dead = store.mark_message_recovering(msg_id_dead, nonce="RETRY-NONCE-2")
        assert auth_dead is False, "mark_message_recovering reversed DEAD state!"
        assert store.get_message_state(msg_id_dead) == "DEAD"


# ==============================================================================
# PROBE 4: Pre-Upgrade Recovery, Terminal Immunity & Leftover Claim Reconciled
# ==============================================================================
def test_pre_upgrade_recovery_and_leftover_reconciliation():
    """
    Finding P1 & P2:
    1. Leftover .claimed files from already COMPLETED messages are reconciled (unlinked)
       and NEVER reverted back to .json or marked DEAD, regardless of policy or attempt count.
    2. Terminal states are protected in SQL: mark_message_dead cannot overwrite COMPLETED,
       and mark_message_completed cannot overwrite DEAD.
    3. mark_message_recovering returns False on zero rowcount update without touching nonce.
    4. Recovery persistence failure preserves .claimed envelope.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "admissions.db")
        inbox_dir = os.path.join(tmpdir, "inbox")
        outbox_dir = os.path.join(tmpdir, "outbox")
        dead_dir = os.path.join(tmpdir, "dead")
        archive_dir = os.path.join(tmpdir, "archive")
        os.makedirs(inbox_dir, exist_ok=True)
        os.makedirs(outbox_dir, exist_ok=True)
        os.makedirs(dead_dir, exist_ok=True)
        os.makedirs(archive_dir, exist_ok=True)

        store = DurableAdmissionStore(db_path)

        with patch("antigravity.daemons.inbox_worker.INBOX_DIR", inbox_dir), \
             patch("antigravity.daemons.inbox_worker.OUTBOX_DIR", outbox_dir), \
             patch("antigravity.daemons.inbox_worker.DEAD_LETTER_DIR", dead_dir), \
             patch("antigravity.daemons.inbox_worker.ARCHIVE_DIR", archive_dir):

            worker = InboxWorker()
            worker.db = store

            # Scenario A1: WORKER_RETRY mode with attempts >= MAX_ATTEMPTS on COMPLETED message
            comp_id_1 = "MSG-LEFTOVER-MAX-ATTEMPTS"
            store.admit_submission(comp_id_1, "CORR-L1", "hash", "CODEX", "ANTIGRAVITY", "task", "2026-10-01T09:00:00+05:30")
            store.mark_message_completed(comp_id_1, response_json=json.dumps({"status": "COMPLETED"}))
            claimed_file_1 = os.path.join(inbox_dir, f"{comp_id_1}.claimed")
            with open(claimed_file_1, "w", encoding="utf-8") as f:
                json.dump({"message_id": comp_id_1, "worker_pid": 999999, "status": "CLAIMED", "attempt_count": 5}, f)
            os.utime(claimed_file_1, (time.time() - 200, time.time() - 200))

            worker.orphan_recovery_policy = "WORKER_RETRY"
            worker.recover_orphaned_claims()

            assert not os.path.exists(claimed_file_1), "Leftover claim file was not unlinked"
            assert not os.path.exists(os.path.join(inbox_dir, f"{comp_id_1}.json")), "Resurrected as .json"
            assert not os.path.exists(os.path.join(dead_dir, f"{comp_id_1}.dead.json")), "COMPLETED message was wrongly dead-lettered!"
            assert store.get_message_state(comp_id_1) == "COMPLETED"

            # Scenario A2: RETRY_REQUIRED policy on COMPLETED message
            comp_id_2 = "MSG-LEFTOVER-RETRY-REQ"
            store.admit_submission(comp_id_2, "CORR-L2", "hash", "CODEX", "ANTIGRAVITY", "task", "2026-10-01T09:00:00+05:30")
            store.mark_message_completed(comp_id_2, response_json=json.dumps({"status": "COMPLETED"}))
            claimed_file_2 = os.path.join(inbox_dir, f"{comp_id_2}.claimed")
            with open(claimed_file_2, "w", encoding="utf-8") as f:
                json.dump({"message_id": comp_id_2, "worker_pid": 999999, "status": "CLAIMED", "attempt_count": 0}, f)
            os.utime(claimed_file_2, (time.time() - 200, time.time() - 200))

            worker.orphan_recovery_policy = "RETRY_REQUIRED"
            worker.recover_orphaned_claims()

            assert not os.path.exists(claimed_file_2), "Leftover claim file was not unlinked"
            assert not os.path.exists(os.path.join(dead_dir, f"{comp_id_2}.dead.json")), "COMPLETED message was wrongly dead-lettered under RETRY_REQUIRED!"
            assert store.get_message_state(comp_id_2) == "COMPLETED"

            # Scenario B: SQL terminal state protection
            assert store.mark_message_dead(comp_id_1, error="MUTATION_ATTEMPT") is False
            assert store.get_message_state(comp_id_1) == "COMPLETED"

            dead_id = "MSG-DEAD-TERM"
            store.admit_submission(dead_id, "CORR-D", "hash", "CODEX", "ANTIGRAVITY", "task", "2026-10-01T09:00:00+05:30")
            store.mark_message_dead(dead_id, error="INITIAL_DEAD")
            assert store.mark_message_completed(dead_id) is False
            assert store.get_message_state(dead_id) == "DEAD"

            # Scenario C: Zero-rowcount recovery refusal in mark_message_recovering
            # 1. On terminal message: returns False before update
            store.check_and_record_nonce("TEST-NONCE-TERM", "CODEX", "2026-10-01T09:00:00+05:30", message_id=comp_id_1)
            auth_term = store.mark_message_recovering(comp_id_1, nonce="TEST-NONCE-TERM")
            assert auth_term is False, "mark_message_recovering authorized recovery on COMPLETED message!"

            # 2. On active message: update executes but rowcount is 0 (e.g. concurrent race)
            act_id_zc = "MSG-ACTIVE-ZC"
            store.admit_submission(act_id_zc, "CORR-ZC", "hash", "CODEX", "ANTIGRAVITY", "task", "2026-10-01T09:00:00+05:30")
            store.check_and_record_nonce("TEST-NONCE-ZC", "CODEX", "2026-10-01T09:00:00+05:30", message_id=act_id_zc)

            orig_connect = sqlite3.connect
            class MockConn:
                def __init__(self, real_conn):
                    self._real = real_conn
                def execute(self, sql, params=()):
                    res = self._real.execute(sql, params)
                    if "UPDATE message_admissions SET state = 'RECOVERING'" in sql:
                        mock_cur = MagicMock()
                        mock_cur.rowcount = 0
                        return mock_cur
                    return res
                def commit(self):
                    self._real.commit()
                def __getattr__(self, name):
                    return getattr(self._real, name)
                def __setattr__(self, name, val):
                    if name == "_real":
                        super().__setattr__(name, val)
                    else:
                        setattr(self._real, name, val)
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    return self._real.__exit__(*args)

            with patch("sqlite3.connect", side_effect=lambda *args, **kwargs: MockConn(orig_connect(*args, **kwargs))):
                auth_res = store.mark_message_recovering(act_id_zc, nonce="TEST-NONCE-ZC")
                assert auth_res is False, "mark_message_recovering did not check rowcount == 0!"

            # Verify nonce state was NOT mutated to RECOVERED_RETRY_PENDING
            with sqlite3.connect(store.db_path) as conn:
                conn.row_factory = sqlite3.Row
                nrow = conn.execute("SELECT state FROM seen_nonces WHERE nonce = 'TEST-NONCE-ZC'").fetchone()
                assert nrow["state"] != "RECOVERED_RETRY_PENDING"

            # Scenario D: Recovery persistence failure preserves envelope
            active_id = "MSG-ACTIVE-REC-FAIL"
            store.admit_submission(active_id, "CORR-A", "hash", "CODEX", "ANTIGRAVITY", "task", "2026-10-01T09:00:00+05:30")
            claimed_active = os.path.join(inbox_dir, f"{active_id}.claimed")
            with open(claimed_active, "w", encoding="utf-8") as f:
                json.dump({"message_id": active_id, "worker_pid": 999999, "status": "CLAIMED"}, f)
            os.utime(claimed_active, (time.time() - 200, time.time() - 200))

            worker.orphan_recovery_policy = "WORKER_RETRY"
            with patch.object(store, "mark_message_recovering", return_value=False):
                worker.recover_orphaned_claims()
                # Must preserve .claimed file when recovery authorization fails due to DB error!
                assert os.path.exists(claimed_active), "Recovery persistence error deleted the envelope!"


# ==============================================================================
# PROBE 5: Full Identity Contract Validation
# ==============================================================================
def test_identity_contract_validation():
    """
    Finding P2: admit_submission checks (payload_hash, sender, recipient, subject).
    Divergent attributes raise CONFLICT.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "admissions.db")
        store = DurableAdmissionStore(db_path)

        msg_id = "MSG-ID-001"
        corr_id = "CORR-ID-001"
        p_hash = compute_payload_hash("Original Body")

        is_new, _, err, _ = store.admit_submission(
            message_id=msg_id, correlation_id=corr_id, payload_hash=p_hash,
            sender="ANTIGRAVITY", recipient="CODEX", subject="Review", timestamp_ist="2026-10-01T09:00:00+05:30"
        )
        assert is_new is True and err is None

        # Divergent sender
        is_new, _, err, _ = store.admit_submission(
            message_id=msg_id, correlation_id=corr_id, payload_hash=p_hash,
            sender="CLAUDE", recipient="CODEX", subject="Review", timestamp_ist="2026-10-01T09:00:00+05:30"
        )
        assert is_new is False and "CONFLICT" in err

        # Divergent recipient
        is_new, _, err, _ = store.admit_submission(
            message_id=msg_id, correlation_id=corr_id, payload_hash=p_hash,
            sender="ANTIGRAVITY", recipient="CLAUDE", subject="Review", timestamp_ist="2026-10-01T09:00:00+05:30"
        )
        assert is_new is False and "CONFLICT" in err

        # Divergent subject
        is_new, _, err, _ = store.admit_submission(
            message_id=msg_id, correlation_id=corr_id, payload_hash=p_hash,
            sender="ANTIGRAVITY", recipient="CODEX", subject="Other", timestamp_ist="2026-10-01T09:00:00+05:30"
        )
        assert is_new is False and "CONFLICT" in err

        # Divergent payload
        is_new, _, err, _ = store.admit_submission(
            message_id=msg_id, correlation_id=corr_id, payload_hash=compute_payload_hash("Mutated"),
            sender="ANTIGRAVITY", recipient="CODEX", subject="Review", timestamp_ist="2026-10-01T09:00:00+05:30"
        )
        assert is_new is False and "CONFLICT" in err


# ==============================================================================
# PROBE 6: Terminal Claim Rejection & DB Error Fail-Closed
# ==============================================================================
def test_claim_message_rejects_terminal_and_db_errors():
    """
    Finding P2: claim_message rejects messages in terminal state and fails closed on DB error.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "admissions.db")
        inbox_dir = os.path.join(tmpdir, "inbox")
        outbox_dir = os.path.join(tmpdir, "outbox")
        os.makedirs(inbox_dir, exist_ok=True)
        os.makedirs(outbox_dir, exist_ok=True)

        store = DurableAdmissionStore(db_path)
        msg_id = "MSG-CLAIM-TERM"
        store.admit_submission(msg_id, "CORR-T", "hash", "CODEX", "ANTIGRAVITY", "sub", "2026-10-01T09:00:00+05:30")
        store.mark_message_completed(msg_id)

        inbox_file = os.path.join(inbox_dir, f"{msg_id}.json")
        with open(inbox_file, "w", encoding="utf-8") as f:
            json.dump({"message_id": msg_id, "status": "CREATED"}, f)

        with patch("antigravity.daemons.inbox_worker.INBOX_DIR", inbox_dir), \
             patch("antigravity.daemons.inbox_worker.OUTBOX_DIR", outbox_dir):
            worker = InboxWorker()
            worker.db = store

            # Terminal claim rejected and inbox file unlinked
            res = worker.claim_message(f"{msg_id}.json")
            assert res is None
            assert not os.path.exists(inbox_file)

            # DB Error simulation: fail-closed (return None)
            msg_id_err = "MSG-CLAIM-ERR"
            inbox_file_err = os.path.join(inbox_dir, f"{msg_id_err}.json")
            with open(inbox_file_err, "w", encoding="utf-8") as f:
                json.dump({"message_id": msg_id_err, "status": "CREATED"}, f)

            with patch.object(store, "get_message_state", return_value="ERROR"):
                res_err = worker.claim_message(f"{msg_id_err}.json")
                assert res_err is None, "claim_message failed to fail closed on store ERROR"


# ==============================================================================
# PROBE 7: Durable Completion & Dead-Letter Updates with Zero-Loss Claim Failure
# ==============================================================================
def test_durable_lifecycle_transitions_and_claim_failure_abort():
    """
    Finding P1 & P2:
    1. Task completions and dead-lettering durably update admission store state.
    2. If store.mark_message_claimed returns False on active message, claim_message
       aborts AND restores the envelope in the inbox (zero envelope loss).
    3. If store.mark_message_completed returns False, _process_message_locked
       routes to dead letter with COMPLETION_PERSISTENCE_FAILED.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "admissions.db")
        inbox_dir = os.path.join(tmpdir, "inbox")
        outbox_dir = os.path.join(tmpdir, "outbox")
        dead_dir = os.path.join(tmpdir, "dead")
        archive_dir = os.path.join(tmpdir, "archive")
        os.makedirs(inbox_dir, exist_ok=True)
        os.makedirs(outbox_dir, exist_ok=True)
        os.makedirs(dead_dir, exist_ok=True)
        os.makedirs(archive_dir, exist_ok=True)

        store = DurableAdmissionStore(db_path)

        with patch("antigravity.daemons.inbox_worker.INBOX_DIR", inbox_dir), \
             patch("antigravity.daemons.inbox_worker.OUTBOX_DIR", outbox_dir), \
             patch("antigravity.daemons.inbox_worker.DEAD_LETTER_DIR", dead_dir), \
             patch("antigravity.daemons.inbox_worker.ARCHIVE_DIR", archive_dir):
            worker = InboxWorker()
            worker.db = store

            msg_id = "MSG-LIFE-001"
            store.admit_submission(msg_id, "CORR-01", "hash", "CODEX", "ANTIGRAVITY", "sub", "2026-10-01T09:00:00+05:30")

            # Route to dead letter updates store
            claimed_path = os.path.join(inbox_dir, f"{msg_id}.claimed")
            msg_data = {"message_id": msg_id, "correlation_id": "CORR-01", "status": "CLAIMED"}
            with open(claimed_path, "w", encoding="utf-8") as f:
                json.dump(msg_data, f)

            persisted = worker.route_to_dead_letter(claimed_path, msg_data, "ERROR_TEST")
            assert persisted is True
            assert store.get_message_state(msg_id) == "DEAD"

            # Completion updates store
            msg_id_comp = "MSG-LIFE-COMP"
            store.admit_submission(msg_id_comp, "CORR-02", "hash", "CODEX", "ANTIGRAVITY", "sub", "2026-10-01T09:00:00+05:30")
            assert store.mark_message_completed(msg_id_comp) is True
            assert store.get_message_state(msg_id_comp) == "COMPLETED"

            # Claim failure zero-loss envelope preservation & retry recovery:
            # When mark_message_claimed fails on an active or CLAIMED message, claim_message returns None,
            # RESTORES the .json envelope in inbox with status 'CREATED', and marks nonce for recovery.
            # Then the subsequent claim with REAL validate_message_schema succeeds without REPLAY_ATTACK!
            msg_id_fail = "MSG-CLAIM-FAIL"
            corr_id_fail = "CORR-FAIL"
            nonce_fail = "NONCE-CLAIM-FAIL"
            secret_key = b"secret_key_32_bytes_long_123456"

            body_fail = {"task": "claim_fail_test"}
            store.admit_submission(msg_id_fail, corr_id_fail, compute_payload_hash(body_fail), "CODEX", "ANTIGRAVITY", "sub", get_current_ist())
            inbox_fail = os.path.join(inbox_dir, f"{msg_id_fail}.json")
            env_fail = {
                "message_id": msg_id_fail,
                "correlation_id": corr_id_fail,
                "sender": "CODEX",
                "recipient": "ANTIGRAVITY",
                "subject": "sub",
                "status": "CREATED",
                "attempt_count": 0,
                "created_at_ist": get_current_ist(),
                "body": body_fail,
                "nonce": nonce_fail,
            }
            env_fail["auth_signature"] = compute_envelope_hmac(env_fail, secret_key)
            with open(inbox_fail, "w", encoding="utf-8") as f:
                json.dump(env_fail, f)

            with patch("antigravity.daemons.inbox_worker.get_agent_secret_key", return_value=secret_key):
                # 1. First claim attempt: mark_message_claimed fails, message state is CLAIMED
                with patch.object(store, "mark_message_claimed", return_value=False), \
                     patch.object(store, "get_message_state", return_value="CLAIMED"):
                    claim_res = worker.claim_message(f"{msg_id_fail}.json")
                    assert claim_res is None
                    assert not os.path.exists(os.path.join(inbox_dir, f"{msg_id_fail}.claimed"))
                    # ZERO LOSS: The message envelope must still exist in the inbox!
                    assert os.path.exists(inbox_fail), "Persistence failure deleted the only recoverable envelope!"
                    with open(inbox_fail, "r", encoding="utf-8") as f:
                        restored_data = json.load(f)
                    assert restored_data["status"] == "CREATED"

                # Verify nonce was marked for recovery in durable store
                with sqlite3.connect(store.db_path) as conn:
                    conn.row_factory = sqlite3.Row
                    nrow = conn.execute("SELECT state FROM seen_nonces WHERE nonce = ?", (nonce_fail,)).fetchone()
                    assert nrow["state"] == "RECOVERED_RETRY_PENDING"

                # 2. Second claim attempt: REAL validate_message_schema and REAL mark_message_claimed!
                # Must succeed without REPLAY_ATTACK!
                claim_res2 = worker.claim_message(f"{msg_id_fail}.json")
                assert claim_res2 is not None, "Second claim attempt failed with real schema validation!"
                claimed_p2, claimed_d2 = claim_res2
                assert claimed_d2["status"] == "CLAIMED"
                assert store.get_message_state(msg_id_fail) == "CLAIMED"

            # Completion persistence failure handling:
            # If mark_message_completed returns False, routes to dead letter with COMPLETION_PERSISTENCE_FAILED
            # AND publishes FAILED response with COMPLETION_PERSISTENCE_FAILED to outbox!
            msg_id_comp_fail = "MSG-COMP-PERSIST-FAIL"
            corr_id_cpf = "CORR-CPF"
            store.admit_submission(msg_id_comp_fail, corr_id_cpf, "hash", "CODEX", "ANTIGRAVITY", "sub", get_current_ist())
            claimed_cpf = os.path.join(inbox_dir, f"{msg_id_comp_fail}.claimed")
            msg_cpf = {
                "message_id": msg_id_comp_fail,
                "correlation_id": corr_id_cpf,
                "nonce": "NONCE-CPF",
                "recipient": "ANTIGRAVITY",
                "sender": "CODEX",
                "subject": "sub",
                "body": {"task": "comp_fail"},
                "status": "CLAIMED",
                "attempt_count": 0,
                "created_at_ist": get_current_ist(),
            }
            msg_cpf["auth_signature"] = compute_envelope_hmac(msg_cpf, secret_key)
            with open(claimed_cpf, "w", encoding="utf-8") as f:
                json.dump(msg_cpf, f)

            with patch.object(worker, "execute_task", return_value=("COMPLETED", "output", {}, None)), \
                 patch("antigravity.daemons.inbox_worker.get_agent_secret_key", return_value=secret_key), \
                 patch.object(store, "mark_message_completed", return_value=False):
                worker._process_message_locked(claimed_cpf, msg_cpf)

                # Must be dead-lettered due to completion persistence failure
                dead_cpf = os.path.join(dead_dir, f"{msg_id_comp_fail}.dead.json")
                assert os.path.exists(dead_cpf), "Completion persistence failure was not routed to dead letter!"
                with open(dead_cpf, "r", encoding="utf-8") as f:
                    dead_cpf_data = json.load(f)
                assert dead_cpf_data["error"] == "COMPLETION_PERSISTENCE_FAILED"

                # Must publish FAILED response to outbox
                outbox_cpf = os.path.join(outbox_dir, f"{corr_id_cpf}_resp.json")
                assert os.path.exists(outbox_cpf), "Outbox response file was not written!"
                with open(outbox_cpf, "r", encoding="utf-8") as f:
                    out_cpf_data = json.load(f)
                assert out_cpf_data["status"] == "FAILED"
                assert out_cpf_data["error"] == "COMPLETION_PERSISTENCE_FAILED"


# ==============================================================================
# PROBE 8: Strict SemVer 2.0.0 Floor & Decoupled Bus Import
# ==============================================================================
def test_codex_bin_strict_semver_floor_and_rejection():
    """
    Finding P2:
    1. Anchored SemVer rejects:
       - 'codex-cli 0.158.0' (below floor)
       - 'codex-cli 0.159.2-rc.1' (prerelease)
       - 'codex-cli 0.159.2-' (trailing hyphen)
       - 'codex-cli 00.159.2' (leading zeros)
       - 'unverified text 0.159.2garbage' (trailing/leading garbage)
    2. Anchored SemVer accepts:
       - 'codex-cli 0.159.2'
       - 'codex 0.159.2'
       - '0.159.2'
       - 'codex-cli 0.160.0'
    3. get_codex_bin raises RuntimeError if no candidate qualifies.
    4. Bus import succeeds and tri_agent_bus.CODEX_BIN is None even if Codex is absent.
    5. ask_codex_detailed returns error dict without unhandled exception when resolution fails.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        bin_old = os.path.join(tmpdir, "old_codex.exe")
        bin_rc = os.path.join(tmpdir, "rc_codex.exe")
        bin_trail_hyphen = os.path.join(tmpdir, "trail_hyphen_codex.exe")
        bin_leading_zero = os.path.join(tmpdir, "leading_zero_codex.exe")
        bin_garbage = os.path.join(tmpdir, "garbage_codex.exe")
        bin_valid = os.path.join(tmpdir, "valid_codex.exe")

        all_bins = [bin_old, bin_rc, bin_trail_hyphen, bin_leading_zero, bin_garbage, bin_valid]
        for p in all_bins:
            with open(p, "wb") as f:
                f.write(b"MZ" + b"\x00" * 100)

        def mock_subprocess(cmd, *args, **kwargs):
            b = cmd[0]
            res = MagicMock()
            res.returncode = 0
            if b == bin_old:
                res.stdout = "codex-cli 0.158.0\n"
            elif b == bin_rc:
                res.stdout = "codex-cli 0.159.2-rc.1\n"
            elif b == bin_trail_hyphen:
                res.stdout = "codex-cli 0.159.2-\n"
            elif b == bin_leading_zero:
                res.stdout = "codex-cli 00.159.2\n"
            elif b == bin_garbage:
                res.stdout = "unverified text 0.159.2garbage\n"
            elif b == bin_valid:
                res.stdout = "codex-cli 0.159.2\n"
            return res

        # Case 1: Outdated, pre-release, malformed SemVer all REJECTED -> MUST RAISE RuntimeError
        bad_candidates = [bin_old, bin_rc, bin_trail_hyphen, bin_leading_zero, bin_garbage]
        with patch("subprocess.run", side_effect=mock_subprocess), \
             patch("antigravity.daemons.tri_agent_bus.glob.glob", return_value=bad_candidates), \
             patch("os.path.isfile", side_effect=lambda p: p in bad_candidates):

            with pytest.raises(RuntimeError) as exc_info:
                get_codex_bin()
            assert "No compatible Codex binary found" in str(exc_info.value)

        # Case 2: Valid candidate (0.159.2) present -> resolved
        with patch("subprocess.run", side_effect=mock_subprocess), \
             patch("antigravity.daemons.tri_agent_bus.glob.glob", return_value=all_bins), \
             patch("os.path.isfile", side_effect=lambda p: p in all_bins):

            resolved = get_codex_bin()
            assert resolved == bin_valid

        # Case 3: Bus import decoupled from Codex resolution
        import antigravity.daemons.tri_agent_bus as bus
        assert bus.CODEX_BIN is None, "CODEX_BIN must be lazily resolved, not bound at module import!"

        # Case 4: ask_codex_detailed handles missing Codex gracefully
        with patch("antigravity.daemons.tri_agent_bus.get_codex_bin", side_effect=RuntimeError("No candidate")):
            res = bus.ask_codex_detailed("test prompt")
            assert res["success"] is False
            assert "Failed to resolve compatible Codex binary" in res["error"]

        # Case 5: Fresh subprocess import of tri_agent_bus with clean environment
        cmd = [
            sys.executable,
            "-c",
            "import os, sys; "
            "sys.path.insert(0, os.getcwd()); "
            "import antigravity.daemons.tri_agent_bus as bus; "
            "assert bus.CODEX_BIN is None; "
            "print('FRESH_IMPORT_OK')"
        ]
        sub_res = subprocess.run(cmd, capture_output=True, text=True)
        assert sub_res.returncode == 0, f"Fresh subprocess import failed: {sub_res.stderr}"
        assert "FRESH_IMPORT_OK" in sub_res.stdout


def test_strict_semver_prerelease_and_ascii_enforcement():
    """
    Finding P2: SemVer 2.0.0 Parser Strictness
    1. Rejects numeric prerelease identifiers with leading zeros: '0.160.0-01', '0.160.0-rc.01'.
    2. Rejects non-ASCII digits: '০.১৬০.০'.
    3. Accepts valid SemVer 2.0.0 with ASCII digits: '0.160.0-1', '0.160.0-rc.1', '0.160.0-beta.2+build.42'.
    """
    import re
    semver_pattern = (
        r"^(?:(?:codex|codex-cli)\s+)?"
        r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
        r"(?:-((?:0|[1-9][0-9]*|[0-9A-Za-z-]*[a-zA-Z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[a-zA-Z-][0-9A-Za-z-]*))*))?"
        r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
    )

    # Valid SemVer strings
    valid_versions = [
        "0.160.0",
        "codex 0.160.0",
        "codex-cli 0.160.0",
        "0.160.0-1",
        "0.160.0-rc.1",
        "0.160.0-beta.2+build.42",
        "1.0.0-alpha",
        "1.0.0-0.3.7",
        "1.0.0-x.7.z.92",
    ]
    for v in valid_versions:
        assert re.match(semver_pattern, v) is not None, f"Strict SemVer regex failed to match valid version '{v}'"

    # Invalid SemVer strings (must NOT match)
    invalid_versions = [
        "0.160.0-01",        # Numeric prerelease with leading zero
        "0.160.0-rc.01",     # Sub-identifier numeric with leading zero
        "00.160.0",          # Leading zero in major
        "0.0160.0",          # Leading zero in minor
        "0.160.00",          # Leading zero in patch
        "0.160.0-",          # Empty prerelease
        "0.160.0+",          # Empty build
        "০.১৬০.০",           # Non-ASCII digits
        "0.160.0-alpha..1",  # Empty identifier
    ]
    for inv in invalid_versions:
        assert re.match(semver_pattern, inv) is None, f"Strict SemVer regex incorrectly matched invalid version '{inv}'"


# ==============================================================================
# PROBE 9: Nonce Helpers Terminal State Immunity
# ==============================================================================
def test_nonce_helpers_terminal_state_immunity():
    """
    Finding P1: Nonce helpers (mark_nonce_completed, mark_nonce_failed, mark_nonce_for_recovery)
    must strictly preserve terminal states (COMPLETED, DEAD) and never permit:
    - COMPLETED -> DEAD
    - COMPLETED -> RECOVERING
    - DEAD -> COMPLETED
    - DEAD -> RECOVERING
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "admissions.db")
        store = DurableAdmissionStore(db_path)

        comp_msg = "MSG-IMMUNE-COMP"
        dead_msg = "MSG-IMMUNE-DEAD"

        store.admit_submission(comp_msg, "CORR-IC", "h1", "CODEX", "ANTIGRAVITY", "sub", "2026-10-01T09:00:00+05:30")
        assert store.mark_message_completed(comp_msg) is True
        assert store.get_message_state(comp_msg) == "COMPLETED"

        store.admit_submission(dead_msg, "CORR-ID", "h2", "CODEX", "ANTIGRAVITY", "sub", "2026-10-01T09:00:00+05:30")
        assert store.mark_message_dead(dead_msg, error="SIMULATED") is True
        assert store.get_message_state(dead_msg) == "DEAD"

        # 1. Attempt to mutate COMPLETED -> DEAD via mark_nonce_failed
        store.mark_nonce_failed("NONCE-C1", message_id=comp_msg)
        assert store.get_message_state(comp_msg) == "COMPLETED", "mark_nonce_failed mutated COMPLETED to DEAD!"

        # 2. Attempt to mutate COMPLETED -> RECOVERING via mark_nonce_for_recovery
        store.mark_nonce_for_recovery("NONCE-C2", message_id=comp_msg)
        assert store.get_message_state(comp_msg) == "COMPLETED", "mark_nonce_for_recovery mutated COMPLETED to RECOVERING!"

        # 3. Attempt to mutate DEAD -> COMPLETED via mark_nonce_completed
        store.mark_nonce_completed("NONCE-D1", message_id=dead_msg)
        assert store.get_message_state(dead_msg) == "DEAD", "mark_nonce_completed mutated DEAD to COMPLETED!"

        # 4. Attempt to mutate DEAD -> RECOVERING via mark_nonce_for_recovery
        store.mark_nonce_for_recovery("NONCE-D2", message_id=dead_msg)
        assert store.get_message_state(dead_msg) == "DEAD", "mark_nonce_for_recovery mutated DEAD to RECOVERING!"


# ==============================================================================
# PROBE 10: Terminal Nonce Rows Immunity (Codex Finding 1)
# ==============================================================================
def test_terminal_nonce_rows_immunity():
    """
    Finding P1: Terminal nonce rows in seen_nonces (COMPLETED, FAILED) must be immutable.
    - mark_nonce_for_recovery on COMPLETED returns False; nonce state remains COMPLETED.
    - mark_nonce_failed on COMPLETED returns False; nonce state remains COMPLETED.
    - mark_nonce_completed on FAILED returns False; nonce state remains FAILED.
    - mark_nonce_for_recovery on FAILED returns False; nonce state remains FAILED.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "admissions.db")
        store = DurableAdmissionStore(db_path)

        nonce_c = "NONCE-TERM-COMP"
        nonce_f = "NONCE-TERM-FAIL"

        # Initialize nonces directly into seen_nonces
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO seen_nonces (nonce, sender, timestamp_ist, recorded_at, state) VALUES (?, ?, ?, ?, ?)",
                (nonce_c, "CODEX", "2026-10-01T09:00:00+05:30", time.time(), "COMPLETED")
            )
            conn.execute(
                "INSERT INTO seen_nonces (nonce, sender, timestamp_ist, recorded_at, state) VALUES (?, ?, ?, ?, ?)",
                (nonce_f, "CODEX", "2026-10-01T09:00:00+05:30", time.time(), "FAILED")
            )

        # 1. Attempt to mutate COMPLETED -> RECOVERED_RETRY_PENDING
        res1 = store.mark_nonce_for_recovery(nonce_c)
        assert res1 is False
        with sqlite3.connect(db_path) as conn:
            row1 = conn.execute("SELECT state FROM seen_nonces WHERE nonce = ?", (nonce_c,)).fetchone()
            assert row1[0] == "COMPLETED"

        # 2. Attempt to mutate COMPLETED -> FAILED
        res2 = store.mark_nonce_failed(nonce_c)
        assert res2 is False
        with sqlite3.connect(db_path) as conn:
            row2 = conn.execute("SELECT state FROM seen_nonces WHERE nonce = ?", (nonce_c,)).fetchone()
            assert row2[0] == "COMPLETED"

        # 3. Attempt to mutate FAILED -> COMPLETED
        res3 = store.mark_nonce_completed(nonce_f)
        assert res3 is False
        with sqlite3.connect(db_path) as conn:
            row3 = conn.execute("SELECT state FROM seen_nonces WHERE nonce = ?", (nonce_f,)).fetchone()
            assert row3[0] == "FAILED"

        # 4. Attempt to mutate FAILED -> RECOVERED_RETRY_PENDING
        res4 = store.mark_nonce_for_recovery(nonce_f)
        assert res4 is False
        with sqlite3.connect(db_path) as conn:
            row4 = conn.execute("SELECT state FROM seen_nonces WHERE nonce = ?", (nonce_f,)).fetchone()
            assert row4[0] == "FAILED"


# ==============================================================================
# PROBE 11: Terminal Admission Rejection On Fresh Nonces (Codex Finding 2)
# ==============================================================================
def test_terminal_admission_rejects_fresh_nonces():
    """
    Finding P1: Fresh nonces for a terminal message_id must be rejected fail-closed.
    check_and_record_nonce must check message_admissions BEFORE seen_nonces.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "admissions.db")
        store = DurableAdmissionStore(db_path)

        comp_id = "MSG-COMP-TERM-CHECK"
        dead_id = "MSG-DEAD-TERM-CHECK"

        store.admit_submission(comp_id, "CORR-C", "hash", "CODEX", "ANTIGRAVITY", "sub", "2026-10-01T09:00:00+05:30")
        store.mark_message_completed(comp_id)

        store.admit_submission(dead_id, "CORR-D", "hash", "CODEX", "ANTIGRAVITY", "sub", "2026-10-01T09:00:00+05:30")
        store.mark_message_dead(dead_id, error="SIMULATED")

        # 1. Fresh nonce for COMPLETED message
        fresh_nonce_1 = "NONCE-FRESH-FOR-COMPLETED"
        ok1, err1 = store.check_and_record_nonce(fresh_nonce_1, "CODEX", "2026-10-01T09:00:00+05:30", message_id=comp_id)
        assert ok1 is False
        assert "TERMINAL_STATE" in err1
        assert "COMPLETED" in err1

        # Verify fresh nonce was NOT inserted into seen_nonces
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT 1 FROM seen_nonces WHERE nonce = ?", (fresh_nonce_1,)).fetchone()
            assert row is None, "Fresh nonce was recorded despite terminal message state!"

        # 2. Fresh nonce for DEAD message
        fresh_nonce_2 = "NONCE-FRESH-FOR-DEAD"
        ok2, err2 = store.check_and_record_nonce(fresh_nonce_2, "CODEX", "2026-10-01T09:00:00+05:30", message_id=dead_id)
        assert ok2 is False
        assert "TERMINAL_STATE" in err2
        assert "DEAD" in err2


# ==============================================================================
# PROBE 12: Untrusted Attempt Count Cannot Bypass Expiry (Codex Finding 3)
# ==============================================================================
def test_untrusted_attempt_count_cannot_bypass_expiry():
    """
    Finding P1: An unverified attempt_count in the envelope must NEVER grant recovery
    exemption or bypass timestamp freshness checks.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "admissions.db")
        store = DurableAdmissionStore(db_path)
        secret_key = b"secret_key_32_bytes_long_123456"

        old_timestamp = "2026-09-30 09:00:00"  # 24 hours old
        msg_id = "MSG-OLD-TAMPERED-ATTEMPT"
        corr_id = "CORR-OLD-01"
        nonce = "NONCE-OLD-01"

        body = {"cmd": "test_expiry"}
        env = {
            "message_id": msg_id,
            "correlation_id": corr_id,
            "sender": "CODEX",
            "recipient": "ANTIGRAVITY",
            "subject": "sub",
            "status": "CREATED",
            "attempt_count": 5,  # Caller supplied attempt_count > 0 without durable authorization!
            "created_at_ist": old_timestamp,
            "body": body,
            "nonce": nonce,
        }
        env["auth_signature"] = compute_envelope_hmac(env, secret_key)

        with patch("antigravity.daemons.inbox_worker.get_agent_secret_key", return_value=secret_key):
            # Auth verification must reject with TIMESTAMP_OUT_OF_BOUNDS
            ok, err = verify_message_auth(env, admission_store=store)
            assert ok is False
            assert "TIMESTAMP_OUT_OF_BOUNDS" in err

            # Schema validation must reject with TIMESTAMP_OUT_OF_BOUNDS
            val_ok, val_err = validate_message_schema(env, admission_store=store)
            assert val_ok is False
            assert "TIMESTAMP_OUT_OF_BOUNDS" in val_err


# ==============================================================================
# PROBE 13: Dead-Letter Persistence Failure Preserves Claim and Suppresses Outbox (Codex Finding 4)
# ==============================================================================
def test_dead_letter_persistence_failure_preserves_claim_and_suppresses_outbox():
    """
    Finding P1: If store.mark_message_dead fails, route_to_dead_letter must preserve
    claimed_path, and _process_message_locked must NOT publish a contradictory FAILED outbox response.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "admissions.db")
        inbox_dir = os.path.join(tmpdir, "inbox")
        outbox_dir = os.path.join(tmpdir, "outbox")
        dead_dir = os.path.join(tmpdir, "dead")
        archive_dir = os.path.join(tmpdir, "archive")
        os.makedirs(inbox_dir, exist_ok=True)
        os.makedirs(outbox_dir, exist_ok=True)
        os.makedirs(dead_dir, exist_ok=True)
        os.makedirs(archive_dir, exist_ok=True)

        store = DurableAdmissionStore(db_path)
        secret_key = b"secret_key_32_bytes_long_123456"

        with patch("antigravity.daemons.inbox_worker.INBOX_DIR", inbox_dir), \
             patch("antigravity.daemons.inbox_worker.OUTBOX_DIR", outbox_dir), \
             patch("antigravity.daemons.inbox_worker.DEAD_LETTER_DIR", dead_dir), \
             patch("antigravity.daemons.inbox_worker.ARCHIVE_DIR", archive_dir):
            worker = InboxWorker()
            worker.db = store

            msg_id = "MSG-FAIL-DEAD-PERSIST"
            corr_id = "CORR-FDP"
            claimed_path = os.path.join(inbox_dir, f"{msg_id}.claimed")
            outbox_path = os.path.join(outbox_dir, f"{corr_id}_resp.json")

            store.admit_submission(msg_id, corr_id, "hash", "CODEX", "ANTIGRAVITY", "sub", get_current_ist())

            msg_data = {
                "message_id": msg_id,
                "correlation_id": corr_id,
                "nonce": "NONCE-FDP",
                "recipient": "ANTIGRAVITY",
                "sender": "CODEX",
                "subject": "sub",
                "body": {"task": "test"},
                "status": "CLAIMED",
                "attempt_count": 0,
                "created_at_ist": get_current_ist(),
            }
            msg_data["auth_signature"] = compute_envelope_hmac(msg_data, secret_key)
            with open(claimed_path, "w", encoding="utf-8") as f:
                json.dump(msg_data, f)

            with patch.object(worker, "execute_task", return_value=("FAILED", None, {}, "TASK_FAILED")), \
                 patch("antigravity.daemons.inbox_worker.get_agent_secret_key", return_value=secret_key), \
                 patch.object(store, "mark_message_dead", return_value=False):

                worker._process_message_locked(claimed_path, msg_data)

                # Claimed envelope MUST be preserved!
                assert os.path.exists(claimed_path), "claimed envelope was unlinked despite DB mark_message_dead failure!"

                # Outbox file MUST NOT be published!
                assert not os.path.exists(outbox_path), "Outbox response was published despite DB mark_message_dead failure!"


# ==============================================================================
# PROBE 14: Reconciled Response Loss Window via Durable Response (Codex Finding 5)
# ==============================================================================
def test_outbox_publication_failure_reconciled_via_durable_response():
    """
    Finding P1: When completion persistence succeeds, response_json is stored durably in SQLite.
    If writing outbox response fails (or crashes), claimed_path is preserved, and
    recover_orphaned_claims reconstructs the exact response from SQLite before unlinking the claim.
    """
    import antigravity.daemons.inbox_worker as ib_module

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "admissions.db")
        inbox_dir = os.path.join(tmpdir, "inbox")
        outbox_dir = os.path.join(tmpdir, "outbox")
        dead_dir = os.path.join(tmpdir, "dead")
        archive_dir = os.path.join(tmpdir, "archive")
        os.makedirs(inbox_dir, exist_ok=True)
        os.makedirs(outbox_dir, exist_ok=True)
        os.makedirs(dead_dir, exist_ok=True)
        os.makedirs(archive_dir, exist_ok=True)

        store = DurableAdmissionStore(db_path)
        secret_key = b"secret_key_32_bytes_long_123456"

        with patch("antigravity.daemons.inbox_worker.INBOX_DIR", inbox_dir), \
             patch("antigravity.daemons.inbox_worker.OUTBOX_DIR", outbox_dir), \
             patch("antigravity.daemons.inbox_worker.DEAD_LETTER_DIR", dead_dir), \
             patch("antigravity.daemons.inbox_worker.ARCHIVE_DIR", archive_dir):
            worker = InboxWorker()
            worker.db = store

            msg_id = "MSG-RECON-OUTBOX"
            corr_id = "CORR-RECON"
            claimed_path = os.path.join(inbox_dir, f"{msg_id}.claimed")
            outbox_path = os.path.join(outbox_dir, f"{corr_id}_resp.json")

            store.admit_submission(msg_id, corr_id, "hash", "CODEX", "ANTIGRAVITY", "sub", get_current_ist())

            msg_data = {
                "message_id": msg_id,
                "correlation_id": corr_id,
                "nonce": "NONCE-RECON",
                "recipient": "ANTIGRAVITY",
                "sender": "CODEX",
                "subject": "sub",
                "body": {"task": "reconcile"},
                "status": "CLAIMED",
                "worker_pid": 999999,
                "attempt_count": 0,
                "created_at_ist": get_current_ist(),
            }
            msg_data["auth_signature"] = compute_envelope_hmac(msg_data, secret_key)
            with open(claimed_path, "w", encoding="utf-8") as f:
                json.dump(msg_data, f)

            # Injected failure on writing outbox file during _process_message_locked
            orig_write = ib_module.write_json_atomic
            def write_fail_on_outbox(path, data, **kwargs):
                if path == outbox_path:
                    raise OSError("Injected disk write error on outbox")
                return orig_write(path, data, **kwargs)

            with patch.object(worker, "execute_task", return_value=("COMPLETED", {"result": "success"}, {}, None)), \
                 patch("antigravity.daemons.inbox_worker.get_agent_secret_key", return_value=secret_key), \
                 patch("antigravity.daemons.inbox_worker.write_json_atomic", side_effect=write_fail_on_outbox):

                worker._process_message_locked(claimed_path, msg_data)

                # Claimed envelope MUST be preserved!
                assert os.path.exists(claimed_path)
                # Outbox file does not exist yet
                assert not os.path.exists(outbox_path)

            # State in DB is COMPLETED, and response_json was durably saved!
            assert store.get_message_state(msg_id) == "COMPLETED"
            saved_resp = store.get_message_response(msg_id)
            assert saved_resp is not None
            resp_dict = json.loads(saved_resp)
            assert resp_dict["status"] == "COMPLETED"
            assert resp_dict["output_payload"] == {"result": "success"}

            # Now run recover_orphaned_claims()
            # Age the claimed file so it qualifies as orphan
            os.utime(claimed_path, (time.time() - 200, time.time() - 200))
            worker.recover_orphaned_claims()

            # The outbox file MUST have been reconstructed and published!
            assert os.path.exists(outbox_path), "recover_orphaned_claims failed to reconstruct outbox response!"
            with open(outbox_path, "r", encoding="utf-8") as f:
                reconstructed_resp = json.load(f)
            assert reconstructed_resp["status"] == "COMPLETED"
            assert reconstructed_resp["output_payload"] == {"result": "success"}

            # Now that outbox file exists, the leftover claim was safely unlinked!
            assert not os.path.exists(claimed_path), "Leftover claim was not unlinked after outbox reconciliation!"


# ==============================================================================
# PROBE 15: Orphan Exhaustion Persistence Failure Preserves Claim (Finding 1)
# ==============================================================================
def test_orphan_exhaustion_persistence_failure_preserves_claim():
    """
    Finding P1: In recover_orphaned_claims, if store.mark_message_dead fails on
    RETRY_REQUIRED or maximum-attempts exhaustion, execution must NOT write dead-letter,
    must NOT publish outbox response, and must NOT unlink the claim envelope.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "admissions.db")
        inbox_dir = os.path.join(tmpdir, "inbox")
        outbox_dir = os.path.join(tmpdir, "outbox")
        dead_dir = os.path.join(tmpdir, "dead")
        os.makedirs(inbox_dir, exist_ok=True)
        os.makedirs(outbox_dir, exist_ok=True)
        os.makedirs(dead_dir, exist_ok=True)

        store = DurableAdmissionStore(db_path)
        secret_key = b"secret_key_32_bytes_long_123456"

        with patch("antigravity.daemons.inbox_worker.INBOX_DIR", inbox_dir), \
             patch("antigravity.daemons.inbox_worker.OUTBOX_DIR", outbox_dir), \
             patch("antigravity.daemons.inbox_worker.DEAD_LETTER_DIR", dead_dir):
            worker = InboxWorker()
            worker.db = store
            worker.orphan_recovery_policy = "RETRY_REQUIRED"

            msg_id = "MSG-EXHAUST-FAIL"
            corr_id = "CORR-EXHAUST"
            claimed_path = os.path.join(inbox_dir, f"{msg_id}.claimed")
            dead_path = os.path.join(dead_dir, f"{msg_id}.dead.json")
            outbox_path = os.path.join(outbox_dir, f"{corr_id}_resp.json")

            store.admit_submission(msg_id, corr_id, "hash", "CODEX", "ANTIGRAVITY", "sub", get_current_ist())

            msg_data = {
                "message_id": msg_id,
                "correlation_id": corr_id,
                "nonce": "NONCE-EXHAUST",
                "recipient": "ANTIGRAVITY",
                "sender": "CODEX",
                "subject": "sub",
                "body": {"task": "exhaust"},
                "status": "CLAIMED",
                "worker_pid": 999999,
                "attempt_count": 5,
                "created_at_ist": get_current_ist(),
            }
            with open(claimed_path, "w", encoding="utf-8") as f:
                json.dump(msg_data, f)

            os.utime(claimed_path, (time.time() - 200, time.time() - 200))

            # Simulate database persistence failure for mark_message_dead
            with patch.object(store, "mark_message_dead", return_value=False), \
                 patch("antigravity.daemons.inbox_worker.get_agent_secret_key", return_value=secret_key):

                worker.recover_orphaned_claims()

                # Claim envelope MUST be preserved!
                assert os.path.exists(claimed_path), "Claim envelope was unlinked despite DB mark_message_dead failure!"
                # Dead letter file MUST NOT be written!
                assert not os.path.exists(dead_path), "Dead letter was written despite DB mark_message_dead failure!"
                # Outbox file MUST NOT be published!
                assert not os.path.exists(outbox_path), "Outbox response was published despite DB mark_message_dead failure!"


# ==============================================================================
# PROBE 16: Schema Rejection Persistence Failure Preserves Claim & Suppresses Outbox (Finding 2)
# ==============================================================================
def test_schema_rejection_persistence_failure_preserves_claim_and_suppresses_outbox():
    """
    Finding P1: In _process_message_locked, when validate_message_schema fails,
    if route_to_dead_letter returns False (DB persistence failed), outbox response
    must NOT be published, and the claim envelope must remain preserved.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "admissions.db")
        inbox_dir = os.path.join(tmpdir, "inbox")
        outbox_dir = os.path.join(tmpdir, "outbox")
        dead_dir = os.path.join(tmpdir, "dead")
        os.makedirs(inbox_dir, exist_ok=True)
        os.makedirs(outbox_dir, exist_ok=True)
        os.makedirs(dead_dir, exist_ok=True)

        store = DurableAdmissionStore(db_path)
        secret_key = b"secret_key_32_bytes_long_123456"

        with patch("antigravity.daemons.inbox_worker.INBOX_DIR", inbox_dir), \
             patch("antigravity.daemons.inbox_worker.OUTBOX_DIR", outbox_dir), \
             patch("antigravity.daemons.inbox_worker.DEAD_LETTER_DIR", dead_dir):
            worker = InboxWorker()
            worker.db = store

            msg_id = "MSG-BAD-SCHEMA-PERSIST"
            corr_id = "CORR-BSP"
            claimed_path = os.path.join(inbox_dir, f"{msg_id}.claimed")
            outbox_path = os.path.join(outbox_dir, f"{corr_id}_resp.json")

            store.admit_submission(msg_id, corr_id, "hash", "CODEX", "ANTIGRAVITY", "sub", get_current_ist())

            # Message with missing required field (invalid schema)
            msg_data = {
                "message_id": msg_id,
                "correlation_id": corr_id,
                # Missing recipient, sender, etc.
                "status": "CLAIMED",
            }
            with open(claimed_path, "w", encoding="utf-8") as f:
                json.dump(msg_data, f)

            with patch.object(store, "mark_message_dead", return_value=False), \
                 patch("antigravity.daemons.inbox_worker.get_agent_secret_key", return_value=secret_key):

                worker._process_message_locked(claimed_path, msg_data)

                # Claim envelope MUST be preserved!
                assert os.path.exists(claimed_path), "Claim envelope was unlinked despite DB mark_message_dead failure on schema rejection!"
                # Outbox file MUST NOT be published!
                assert not os.path.exists(outbox_path), "Outbox response was published despite DB mark_message_dead failure on schema rejection!"


# ==============================================================================
# PROBE 17: Completed Recovery Missing Response Preserves Claim (Finding 3)
# ==============================================================================
def test_completed_recovery_missing_response_preserves_claim():
    """
    Finding P1: In recover_orphaned_claims, when an orphan has state COMPLETED in DB,
    if outbox file is missing and get_message_response returns None (or DB error),
    the claim envelope must be PRESERVED and not unlinked.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "admissions.db")
        inbox_dir = os.path.join(tmpdir, "inbox")
        outbox_dir = os.path.join(tmpdir, "outbox")
        os.makedirs(inbox_dir, exist_ok=True)
        os.makedirs(outbox_dir, exist_ok=True)

        store = DurableAdmissionStore(db_path)

        with patch("antigravity.daemons.inbox_worker.INBOX_DIR", inbox_dir), \
             patch("antigravity.daemons.inbox_worker.OUTBOX_DIR", outbox_dir):
            worker = InboxWorker()
            worker.db = store

            msg_id = "MSG-NO-RESP-SAVED"
            corr_id = "CORR-NRS"
            claimed_path = os.path.join(inbox_dir, f"{msg_id}.claimed")
            outbox_path = os.path.join(outbox_dir, f"{corr_id}_resp.json")

            store.admit_submission(msg_id, corr_id, "hash", "CODEX", "ANTIGRAVITY", "sub", get_current_ist())
            # Mark completed WITHOUT response_json
            store.mark_message_completed(msg_id, response_json=None)

            msg_data = {
                "message_id": msg_id,
                "correlation_id": corr_id,
                "status": "CLAIMED",
                "worker_pid": 999999,
                "attempt_count": 0,
                "created_at_ist": get_current_ist(),
            }
            with open(claimed_path, "w", encoding="utf-8") as f:
                json.dump(msg_data, f)

            os.utime(claimed_path, (time.time() - 200, time.time() - 200))

            worker.recover_orphaned_claims()

            # Claim envelope MUST be preserved because response cannot be recovered!
            assert os.path.exists(claimed_path), "Claim envelope was deleted even though response_json was missing!"
            assert not os.path.exists(outbox_path)


# ==============================================================================
# PROBE 18: Partial Transaction Rollback on Failed Paired Transitions (Finding 4)
# ==============================================================================
def test_partial_transaction_rollback_on_failed_paired_transitions():
    """
    Finding P1: When a paired transition (nonce + admission) fails due to admission
    terminal immunity or missing admission row, the partial update to seen_nonces
    must be ROLLED BACK and not committed.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "admissions.db")
        store = DurableAdmissionStore(db_path)

        # 1. Nonce RECORDED, message COMPLETED -> mark_nonce_for_recovery must rollback nonce
        nonce1 = "NONCE-PAIR-REC"
        msg_id1 = "MSG-PAIR-COMP"
        store.admit_submission(msg_id1, "CORR-1", "hash", "CODEX", "ANTIGRAVITY", "sub", get_current_ist())
        store.mark_message_completed(msg_id1)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO seen_nonces (nonce, sender, timestamp_ist, recorded_at, message_id, state) VALUES (?, ?, ?, ?, ?, ?)",
                (nonce1, "CODEX", get_current_ist(), time.time(), msg_id1, "RECORDED")
            )

        res1 = store.mark_nonce_for_recovery(nonce1, msg_id1)
        assert res1 is False
        with sqlite3.connect(db_path) as conn:
            row1 = conn.execute("SELECT state FROM seen_nonces WHERE nonce = ?", (nonce1,)).fetchone()
            assert row1[0] == "RECORDED", f"Partial transaction committed! Nonce state is {row1[0]}, expected RECORDED"

        # 2. Nonce RECORDED, message DEAD -> mark_nonce_completed must rollback nonce
        nonce2 = "NONCE-PAIR-COMP"
        msg_id2 = "MSG-PAIR-DEAD"
        store.admit_submission(msg_id2, "CORR-2", "hash", "CODEX", "ANTIGRAVITY", "sub", get_current_ist())
        store.mark_message_dead(msg_id2, error="SIMULATED")
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO seen_nonces (nonce, sender, timestamp_ist, recorded_at, message_id, state) VALUES (?, ?, ?, ?, ?, ?)",
                (nonce2, "CODEX", get_current_ist(), time.time(), msg_id2, "RECORDED")
            )

        res2 = store.mark_nonce_completed(nonce2, msg_id2)
        assert res2 is False
        with sqlite3.connect(db_path) as conn:
            row2 = conn.execute("SELECT state FROM seen_nonces WHERE nonce = ?", (nonce2,)).fetchone()
            assert row2[0] == "RECORDED", f"Partial transaction committed! Nonce state is {row2[0]}, expected RECORDED"

        # 3. Nonce RECORDED, message COMPLETED -> mark_nonce_failed must rollback nonce
        nonce3 = "NONCE-PAIR-FAIL"
        msg_id3 = "MSG-PAIR-COMP2"
        store.admit_submission(msg_id3, "CORR-3", "hash", "CODEX", "ANTIGRAVITY", "sub", get_current_ist())
        store.mark_message_completed(msg_id3)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO seen_nonces (nonce, sender, timestamp_ist, recorded_at, message_id, state) VALUES (?, ?, ?, ?, ?, ?)",
                (nonce3, "CODEX", get_current_ist(), time.time(), msg_id3, "RECORDED")
            )

        res3 = store.mark_nonce_failed(nonce3, msg_id3)
        assert res3 is False
        with sqlite3.connect(db_path) as conn:
            row3 = conn.execute("SELECT state FROM seen_nonces WHERE nonce = ?", (nonce3,)).fetchone()
            assert row3[0] == "RECORDED", f"Partial transaction committed! Nonce state is {row3[0]}, expected RECORDED"


# ==============================================================================
# PROBE 19: Caller Recovery Bypass & Attempt Count Without Durable Authorization (Finding 5)
# ==============================================================================
def test_caller_recovery_bypass_and_attempt_count_without_durable_authorization():
    """
    Finding P2:
    1. Passing allow_recovery=True directly to verify_message_auth on an expired envelope
       without a durable recovery row in SQLite must fail with TIMESTAMP_OUT_OF_BOUNDS.
    2. Setting attempt_count > 0 when check_freshness=False must NOT grant recovery authorization
       to check_and_record_nonce (second request with same nonce must fail as REPLAY_ATTACK).
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "admissions.db")
        store = DurableAdmissionStore(db_path)
        secret_key = b"secret_key_32_bytes_long_123456"

        old_timestamp = "2026-09-30 09:00:00"  # 24 hours old
        msg_id = "MSG-EXP-EXPLICIT-ALLOW"
        corr_id = "CORR-EEA"
        nonce = "NONCE-EEA"

        body = {"cmd": "test_bypass"}
        env = {
            "message_id": msg_id,
            "correlation_id": corr_id,
            "sender": "CODEX",
            "recipient": "ANTIGRAVITY",
            "subject": "sub",
            "status": "CREATED",
            "attempt_count": 0,
            "created_at_ist": old_timestamp,
            "body": body,
            "nonce": nonce,
        }
        env["auth_signature"] = compute_envelope_hmac(env, secret_key)

        with patch("antigravity.daemons.inbox_worker.get_agent_secret_key", return_value=secret_key):
            # 1. Caller passes allow_recovery=True, but store has NO recovery row
            ok, err = verify_message_auth(env, allow_recovery=True, admission_store=store)
            assert ok is False, "allow_recovery=True bypassed timestamp expiration without durable store row!"
            assert "TIMESTAMP_OUT_OF_BOUNDS" in err

            # 2. check_freshness=False with attempt_count > 0 must NOT grant recovery to check_and_record_nonce
            fresh_ts = get_current_ist()
            nonce_reuse = "NONCE-REUSE-ATTEMPT"
            env_reuse = {
                "message_id": "MSG-REUSE-1",
                "correlation_id": "CORR-R1",
                "sender": "CODEX",
                "recipient": "ANTIGRAVITY",
                "subject": "sub",
                "status": "CREATED",
                "attempt_count": 5,  # attempt_count > 0
                "created_at_ist": fresh_ts,
                "body": {"cmd": "reuse"},
                "nonce": nonce_reuse,
            }
            env_reuse["auth_signature"] = compute_envelope_hmac(env_reuse, secret_key)

            # First recording succeeds
            ok1, err1 = verify_message_auth(env_reuse, check_freshness=False, admission_store=store)
            assert ok1 is True

            # Second submission with same nonce and attempt_count=5 must be BLOCKED as REPLAY_ATTACK!
            ok2, err2 = verify_message_auth(env_reuse, check_freshness=False, admission_store=store)
            assert ok2 is False, "attempt_count > 0 granted recovery authorization to check_and_record_nonce without durable row!"
            assert "REPLAY_ATTACK" in err2


# ==============================================================================
# PROBE 20: Exhaustion Failure Response & DEAD Reconciliation Across Passes (Finding 1)
# ==============================================================================
def test_exhaustion_failure_response_and_dead_reconciliation_across_passes():
    """
    Finding P1:
    1. Pass 1: When orphan claim exhausts max attempts, DEAD state is committed with durable response_json.
       If outbox response publication fails, the claimed envelope is PRESERVED on disk.
    2. Pass 2: Next recovery pass finds message in DEAD state with missing outbox response.
       It must reconstruct the response from durable store (get_message_response) and dead-letter file,
       and only unlink the claim after both artifacts exist.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        inbox_dir = os.path.join(tmpdir, "inbox")
        outbox_dir = os.path.join(tmpdir, "outbox")
        dead_dir = os.path.join(tmpdir, "dead_letter")
        archive_dir = os.path.join(tmpdir, "archive")
        os.makedirs(inbox_dir, exist_ok=True)
        os.makedirs(outbox_dir, exist_ok=True)
        os.makedirs(dead_dir, exist_ok=True)
        os.makedirs(archive_dir, exist_ok=True)

        db_path = os.path.join(tmpdir, "admissions.db")
        store = DurableAdmissionStore(db_path)
        secret_key = b"secret_key_32_bytes_long_123456"

        msg_id = "MSG-EXHAUST-RECONCILE"
        corr_id = "CORR-EXHAUST"
        claimed_path = os.path.join(inbox_dir, f"{msg_id}.claimed")
        outbox_path = os.path.join(outbox_dir, f"{corr_id}_resp.json")
        dead_path = os.path.join(dead_dir, f"{msg_id}.dead.json")

        store.admit_submission(msg_id, corr_id, "hash", "CODEX", "ANTIGRAVITY", "sub", get_current_ist())

        msg_data = {
            "message_id": msg_id,
            "correlation_id": corr_id,
            "sender": "CODEX",
            "recipient": "ANTIGRAVITY",
            "subject": "sub",
            "body": {"task": "test"},
            "status": "CLAIMED",
            "worker_pid": 999999,
            "attempt_count": 0,
            "created_at_ist": get_current_ist(),
            "nonce": "NONCE-EXHAUST",
        }
        with open(claimed_path, "w", encoding="utf-8") as f:
            json.dump(msg_data, f)
        os.utime(claimed_path, (time.time() - 200, time.time() - 200))

        with patch("antigravity.daemons.inbox_worker.INBOX_DIR", inbox_dir), \
             patch("antigravity.daemons.inbox_worker.OUTBOX_DIR", outbox_dir), \
             patch("antigravity.daemons.inbox_worker.DEAD_LETTER_DIR", dead_dir), \
             patch("antigravity.daemons.inbox_worker.ARCHIVE_DIR", archive_dir), \
             patch("antigravity.daemons.inbox_worker.get_agent_secret_key", return_value=secret_key):

            worker = InboxWorker()
            worker.db = store
            worker.orphan_recovery_policy = "RETRY_REQUIRED"

            # In Pass 1: Simulate write failure when publishing outbox response
            original_write_json = write_json_atomic
            def write_fail_on_outbox(path, data, **kwargs):
                if str(path).endswith("_resp.json"):
                    raise OSError("Simulated disk error writing outbox response")
                return original_write_json(path, data, **kwargs)

            with patch("antigravity.daemons.inbox_worker.write_json_atomic", side_effect=write_fail_on_outbox):
                worker.recover_orphaned_claims()

            # Pass 1 Verification:
            # - Store is marked DEAD
            assert store.get_message_state(msg_id) == "DEAD"
            # - Durable response is saved in SQLite
            saved_resp = store.get_message_response(msg_id)
            assert saved_resp is not None, "Durable failure response was not saved in store!"
            resp_parsed = json.loads(saved_resp)
            assert resp_parsed["status"] == "FAILED"
            assert "RETRY_REQUIRED" in resp_parsed["error"]
            # - Outbox response is absent (simulated write failure)
            assert not os.path.exists(outbox_path)
            # - Claim envelope MUST BE PRESERVED on disk!
            assert os.path.exists(claimed_path), "Claim envelope was prematurely deleted on publication failure!"

            # Pass 2: Run recover_orphaned_claims normally (no simulated disk error)
            worker.recover_orphaned_claims()

            # Pass 2 Verification:
            # - Missing outbox response was reconciled from durable store!
            assert os.path.exists(outbox_path), "Outbox response was not reconciled on second pass!"
            with open(outbox_path, "r", encoding="utf-8") as f:
                outbox_data = json.load(f)
            assert outbox_data["status"] == "FAILED"
            assert outbox_data["correlation_id"] == corr_id
            # - Dead letter file exists
            assert os.path.exists(dead_path)
            # - Claim envelope is now cleanly unlinked
            assert not os.path.exists(claimed_path), "Claim envelope was not unlinked after successful reconciliation!"


# ==============================================================================
# PROBE 21: mark_message_recovering Paired Transition Rollback on Incompatible Nonce (Finding 2)
# ==============================================================================
def test_mark_message_recovering_paired_rollback_on_incompatible_nonce():
    """
    Finding P1: When mark_message_recovering encounters an incompatible nonce state
    (FAILED, COMPLETED, RECOVERED_RETRY_CONSUMED), the admission change MUST BE ROLLED BACK
    and not committed, returning False. Legitimate missing nonce is inserted cleanly.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "admissions.db")
        store = DurableAdmissionStore(db_path)

        # 1. Admission QUEUED, Nonce FAILED -> Rollback admission, return False
        msg_id1 = "MSG-PAIR-REV-FAIL"
        nonce1 = "NONCE-PAIR-REV-FAIL"
        store.admit_submission(msg_id1, "CORR-1", "hash", "CODEX", "ANTIGRAVITY", "sub", get_current_ist())
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO seen_nonces (nonce, sender, timestamp_ist, recorded_at, message_id, state) VALUES (?, ?, ?, ?, ?, 'FAILED')",
                (nonce1, "CODEX", get_current_ist(), time.time(), msg_id1)
            )

        res1 = store.mark_message_recovering(msg_id1, nonce=nonce1)
        assert res1 is False, "mark_message_recovering succeeded despite FAILED nonce!"
        with sqlite3.connect(db_path) as conn:
            row_adm1 = conn.execute("SELECT state, attempt_count FROM message_admissions WHERE message_id = ?", (msg_id1,)).fetchone()
            assert row_adm1[0] == "QUEUED", f"Admission was not rolled back! State is {row_adm1[0]}"
            assert row_adm1[1] == 0, f"Attempt count was incremented! Count is {row_adm1[1]}"
            row_nonce1 = conn.execute("SELECT state FROM seen_nonces WHERE nonce = ?", (nonce1,)).fetchone()
            assert row_nonce1[0] == "FAILED"

        # 2. Admission QUEUED, Nonce COMPLETED -> Rollback admission, return False
        msg_id2 = "MSG-PAIR-REV-COMP"
        nonce2 = "NONCE-PAIR-REV-COMP"
        store.admit_submission(msg_id2, "CORR-2", "hash", "CODEX", "ANTIGRAVITY", "sub", get_current_ist())
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO seen_nonces (nonce, sender, timestamp_ist, recorded_at, message_id, state) VALUES (?, ?, ?, ?, ?, 'COMPLETED')",
                (nonce2, "CODEX", get_current_ist(), time.time(), msg_id2)
            )

        res2 = store.mark_message_recovering(msg_id2, nonce=nonce2)
        assert res2 is False, "mark_message_recovering succeeded despite COMPLETED nonce!"
        with sqlite3.connect(db_path) as conn:
            row_adm2 = conn.execute("SELECT state FROM message_admissions WHERE message_id = ?", (msg_id2,)).fetchone()
            assert row_adm2[0] == "QUEUED", f"Admission was not rolled back! State is {row_adm2[0]}"

        # 3. Admission QUEUED, Nonce RECOVERED_RETRY_CONSUMED -> Rollback admission, return False
        msg_id3 = "MSG-PAIR-REV-CONSUMED"
        nonce3 = "NONCE-PAIR-REV-CONSUMED"
        store.admit_submission(msg_id3, "CORR-3", "hash", "CODEX", "ANTIGRAVITY", "sub", get_current_ist())
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO seen_nonces (nonce, sender, timestamp_ist, recorded_at, message_id, state) VALUES (?, ?, ?, ?, ?, 'RECOVERED_RETRY_CONSUMED')",
                (nonce3, "CODEX", get_current_ist(), time.time(), msg_id3)
            )

        res3 = store.mark_message_recovering(msg_id3, nonce=nonce3)
        assert res3 is False, "mark_message_recovering succeeded despite RECOVERED_RETRY_CONSUMED nonce!"
        with sqlite3.connect(db_path) as conn:
            row_adm3 = conn.execute("SELECT state, attempt_count FROM message_admissions WHERE message_id = ?", (msg_id3,)).fetchone()
            assert row_adm3[0] == "QUEUED", f"Admission was not rolled back! State is {row_adm3[0]}"
            assert row_adm3[1] == 0, f"Attempt count was incremented! Count is {row_adm3[1]}"

        # 4. Legitimate missing nonce (e.g. pre-upgrade claim) -> Inserts nonce as RECOVERED_RETRY_PENDING, returns True
        msg_id4 = "MSG-PRE-UPGRADE-LEGIT"
        nonce4 = "NONCE-PRE-UPGRADE-LEGIT"
        store.admit_submission(msg_id4, "CORR-4", "hash", "CODEX", "ANTIGRAVITY", "sub", get_current_ist())

        res4 = store.mark_message_recovering(msg_id4, nonce=nonce4)
        assert res4 is True, "mark_message_recovering failed for legitimate missing nonce!"
        with sqlite3.connect(db_path) as conn:
            row_adm4 = conn.execute("SELECT state FROM message_admissions WHERE message_id = ?", (msg_id4,)).fetchone()
            assert row_adm4[0] == "RECOVERING"
            row_nonce4 = conn.execute("SELECT state FROM seen_nonces WHERE nonce = ?", (nonce4,)).fetchone()
            assert row_nonce4 is not None
            assert row_nonce4[0] == "RECOVERED_RETRY_PENDING"


# ==============================================================================
# PROBE 22: Orphan Exhaustion Missing Key Preserves Claim & Completes On Key Restoration
# ==============================================================================
def test_orphan_exhaustion_missing_key_preserves_claim_and_delivers_on_restore():
    """
    Finding P1: When get_agent_secret_key("ANTIGRAVITY") is None during orphan exhaustion:
    1. Pass 1: Claim envelope MUST NOT be unlinked, mark_message_dead MUST NOT be called,
       dead letter file MUST NOT be written, and outbox MUST NOT receive a response.
    2. Pass 2: Once the key is restored, recover_orphaned_claims completes normally:
       DEAD state is marked in DB, dead letter file is written, signed failure response is
       published to outbox, and the claim envelope is safely unlinked.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        inbox_dir = os.path.join(tmpdir, "inbox")
        outbox_dir = os.path.join(tmpdir, "outbox")
        dead_dir = os.path.join(tmpdir, "dead_letter")
        archive_dir = os.path.join(tmpdir, "archive")
        os.makedirs(inbox_dir, exist_ok=True)
        os.makedirs(outbox_dir, exist_ok=True)
        os.makedirs(dead_dir, exist_ok=True)
        os.makedirs(archive_dir, exist_ok=True)

        db_path = os.path.join(tmpdir, "admissions.db")
        store = DurableAdmissionStore(db_path)
        secret_key = b"secret_key_32_bytes_long_123456"

        msg_id = "MSG-EXHAUST-NO-KEY"
        corr_id = "CORR-NO-KEY"
        claimed_path = os.path.join(inbox_dir, f"{msg_id}.claimed")
        outbox_path = os.path.join(outbox_dir, f"{corr_id}_resp.json")
        dead_path = os.path.join(dead_dir, f"{msg_id}.dead.json")

        store.admit_submission(msg_id, corr_id, "hash", "CODEX", "ANTIGRAVITY", "sub", get_current_ist())

        msg_data = {
            "message_id": msg_id,
            "correlation_id": corr_id,
            "sender": "CODEX",
            "recipient": "ANTIGRAVITY",
            "subject": "sub",
            "body": {"task": "key_unavailable_test"},
            "status": "CLAIMED",
            "worker_pid": 999999,
            "attempt_count": 5,
            "created_at_ist": get_current_ist(),
            "nonce": "NONCE-NO-KEY",
        }
        with open(claimed_path, "w", encoding="utf-8") as f:
            json.dump(msg_data, f)
        os.utime(claimed_path, (time.time() - 200, time.time() - 200))

        with patch("antigravity.daemons.inbox_worker.INBOX_DIR", inbox_dir), \
             patch("antigravity.daemons.inbox_worker.OUTBOX_DIR", outbox_dir), \
             patch("antigravity.daemons.inbox_worker.DEAD_LETTER_DIR", dead_dir), \
             patch("antigravity.daemons.inbox_worker.ARCHIVE_DIR", archive_dir):

            worker = InboxWorker()
            worker.db = store
            worker.orphan_recovery_policy = "RETRY_REQUIRED"

            # Pass 1: get_agent_secret_key returns None (key missing / unreadable)
            with patch("antigravity.daemons.inbox_worker.get_agent_secret_key", return_value=None), \
                 patch.object(store, "mark_message_dead", wraps=store.mark_message_dead) as mock_mark_dead:

                worker.recover_orphaned_claims()

                # Verify nothing was finalized without the signing key
                mock_mark_dead.assert_not_called()
                assert store.get_message_state(msg_id) == "QUEUED"
                assert not os.path.exists(dead_path), "Dead letter file was written without signing key!"
                assert not os.path.exists(outbox_path), "Outbox response was published without signing key!"
                assert os.path.exists(claimed_path), "Claim envelope was unlinked when signing key was missing!"

            # Pass 2: Key is restored!
            with patch("antigravity.daemons.inbox_worker.get_agent_secret_key", return_value=secret_key):
                worker.recover_orphaned_claims()

                # Verify clean finalization
                assert store.get_message_state(msg_id) == "DEAD"
                assert os.path.exists(dead_path), "Dead letter file was not written after key restoration!"
                assert os.path.exists(outbox_path), "Outbox response was not published after key restoration!"
                with open(outbox_path, "r", encoding="utf-8") as f:
                    resp_data = json.load(f)
                assert resp_data["status"] == "FAILED"
                assert resp_data["correlation_id"] == corr_id
                assert "auth_signature" in resp_data
                assert not os.path.exists(claimed_path), "Claim envelope was not unlinked after successful exhaustion recovery!"


# ==============================================================================
# PROBE 23: Schema / Task Failure Outbox Write Failure Reconciled on Second Pass
# ==============================================================================
def test_schema_or_task_failure_outbox_failure_reconciled_on_second_pass():
    """
    Finding P1: When a message encounters a schema or task failure:
    1. Pass 1: route_to_dead_letter runs with unlink_claim=False, persisting DEAD in DB
       and writing the dead letter file. If write_json_atomic to outbox fails,
       the claim envelope MUST REMAIN on disk so recovery can trigger.
    2. Pass 2: recover_orphaned_claims sees the leftover claim and missing outbox response,
       reconstructs the exact signed error response from store.get_message_response,
       writes the outbox response file, and only then safely unlinks the claim.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        inbox_dir = os.path.join(tmpdir, "inbox")
        outbox_dir = os.path.join(tmpdir, "outbox")
        dead_dir = os.path.join(tmpdir, "dead_letter")
        archive_dir = os.path.join(tmpdir, "archive")
        os.makedirs(inbox_dir, exist_ok=True)
        os.makedirs(outbox_dir, exist_ok=True)
        os.makedirs(dead_dir, exist_ok=True)
        os.makedirs(archive_dir, exist_ok=True)

        db_path = os.path.join(tmpdir, "admissions.db")
        store = DurableAdmissionStore(db_path)
        secret_key = b"secret_key_32_bytes_long_123456"

        msg_id = "MSG-TASK-FAIL-OUTBOX-FAIL"
        corr_id = "CORR-TASK-FAIL"
        claimed_path = os.path.join(inbox_dir, f"{msg_id}.claimed")
        outbox_path = os.path.join(outbox_dir, f"{corr_id}_resp.json")
        dead_path = os.path.join(dead_dir, f"{msg_id}.dead.json")

        store.admit_submission(msg_id, corr_id, "hash", "CODEX", "ANTIGRAVITY", "sub", get_current_ist())

        msg_data = {
            "message_id": msg_id,
            "correlation_id": corr_id,
            "sender": "CODEX",
            "recipient": "ANTIGRAVITY",
            "subject": "sub",
            "body": {"task": "doomed_task"},
            "status": "CLAIMED",
            "worker_pid": 999999,
            "attempt_count": 0,
            "created_at_ist": get_current_ist(),
            "nonce": "NONCE-TASK-FAIL",
        }
        msg_data["auth_signature"] = compute_envelope_hmac(msg_data, secret_key)
        with open(claimed_path, "w", encoding="utf-8") as f:
            json.dump(msg_data, f)

        with patch("antigravity.daemons.inbox_worker.INBOX_DIR", inbox_dir), \
             patch("antigravity.daemons.inbox_worker.OUTBOX_DIR", outbox_dir), \
             patch("antigravity.daemons.inbox_worker.DEAD_LETTER_DIR", dead_dir), \
             patch("antigravity.daemons.inbox_worker.ARCHIVE_DIR", archive_dir), \
             patch("antigravity.daemons.inbox_worker.get_agent_secret_key", return_value=secret_key):

            worker = InboxWorker()
            worker.db = store

            # Pass 1: Task fails AND write_json_atomic on outbox raises OSError
            orig_write = write_json_atomic
            def write_fail_on_outbox(path, data, **kwargs):
                if str(path).endswith("_resp.json"):
                    raise OSError("Injected disk full error on outbox")
                return orig_write(path, data, **kwargs)

            with patch.object(worker, "execute_task", return_value=("FAILED", None, {}, "APPLICATION_TASK_ERROR")), \
                 patch("antigravity.daemons.inbox_worker.write_json_atomic", side_effect=write_fail_on_outbox):

                worker._process_message_locked(claimed_path, msg_data)

            # Pass 1 Verification:
            # - DB state is DEAD
            assert store.get_message_state(msg_id) == "DEAD"
            # - Dead letter file is present
            assert os.path.exists(dead_path), "Dead letter file was not written!"
            # - Outbox response was NOT written (failed)
            assert not os.path.exists(outbox_path)
            # - CRUCIAL INVARIANT: Claim envelope MUST STILL EXIST ON DISK!
            assert os.path.exists(claimed_path), "Claim envelope was unlinked before outbox write succeeded!"

            # Pass 2: Age the claimed file and run recover_orphaned_claims
            os.utime(claimed_path, (time.time() - 200, time.time() - 200))
            worker.recover_orphaned_claims()

            # Pass 2 Verification:
            # - Missing outbox response was reconciled from SQLite response_json
            assert os.path.exists(outbox_path), "Outbox response was not reconciled on recovery pass!"
            with open(outbox_path, "r", encoding="utf-8") as f:
                resp_data = json.load(f)
            assert resp_data["status"] == "FAILED"
            assert resp_data["error"] == "APPLICATION_TASK_ERROR"
            assert resp_data["correlation_id"] == corr_id
            # Assert exact equality with durable response in SQLite
            durable_raw = store.get_message_response(msg_id)
            assert durable_raw is not None
            assert resp_data == json.loads(durable_raw), "Reconciled outbox does not match durable SQLite response!"
            # Assert recovered HMAC signature is cryptographically valid
            sig = resp_data.get("auth_signature")
            assert sig == compute_envelope_hmac(resp_data, secret_key), "Recovered outbox HMAC is invalid!"
            # - Claim envelope is now cleanly unlinked
            assert not os.path.exists(claimed_path), "Claim envelope was not unlinked after recovery pass reconciled outbox!"

        # ----------------------------------------------------------------------
        # Part B: Schema validation failure with outbox write failure
        # ----------------------------------------------------------------------
        msg_id_sch = "MSG-SCHEMA-FAIL-OUTBOX-FAIL"
        corr_id_sch = "CORR-SCHEMA-FAIL"
        claimed_sch = os.path.join(inbox_dir, f"{msg_id_sch}.claimed")
        outbox_sch = os.path.join(outbox_dir, f"{corr_id_sch}_resp.json")
        dead_sch = os.path.join(dead_dir, f"{msg_id_sch}.dead.json")

        store.admit_submission(msg_id_sch, corr_id_sch, "hash", "CODEX", "ANTIGRAVITY", "sub", get_current_ist())
        # Malformed envelope missing required fields
        msg_sch_data = {
            "message_id": msg_id_sch,
            "correlation_id": corr_id_sch,
            "status": "CLAIMED",
            "worker_pid": 999999,
        }
        with open(claimed_sch, "w", encoding="utf-8") as f:
            json.dump(msg_sch_data, f)

        with patch("antigravity.daemons.inbox_worker.INBOX_DIR", inbox_dir), \
             patch("antigravity.daemons.inbox_worker.OUTBOX_DIR", outbox_dir), \
             patch("antigravity.daemons.inbox_worker.DEAD_LETTER_DIR", dead_dir), \
             patch("antigravity.daemons.inbox_worker.get_agent_secret_key", return_value=secret_key):

            orig_write = write_json_atomic
            def write_fail_on_outbox_sch(path, data, **kwargs):
                if str(path).endswith("_resp.json"):
                    raise OSError("Injected disk error writing schema outbox")
                return orig_write(path, data, **kwargs)

            with patch("antigravity.daemons.inbox_worker.write_json_atomic", side_effect=write_fail_on_outbox_sch):
                worker._process_message_locked(claimed_sch, msg_sch_data)

            # Verification Pass 1:
            assert store.get_message_state(msg_id_sch) == "DEAD"
            assert os.path.exists(dead_sch)
            assert not os.path.exists(outbox_sch)
            assert os.path.exists(claimed_sch), "Claim envelope unlinked prematurely on schema outbox failure!"

            # Pass 2: Reconciles outbox and unlinks claim
            os.utime(claimed_sch, (time.time() - 200, time.time() - 200))
            worker.recover_orphaned_claims()

            assert os.path.exists(outbox_sch)
            with open(outbox_sch, "r", encoding="utf-8") as f:
                sch_resp = json.load(f)
            assert sch_resp["status"] == "FAILED"
            assert "SCHEMA" in sch_resp["error"]
            # Assert exact equality with durable response in SQLite
            durable_sch = store.get_message_response(msg_id_sch)
            assert durable_sch is not None
            assert sch_resp == json.loads(durable_sch), "Reconciled schema outbox does not match durable SQLite response!"
            # Assert recovered HMAC signature is cryptographically valid
            sig_sch = sch_resp.get("auth_signature")
            assert sig_sch == compute_envelope_hmac(sch_resp, secret_key), "Recovered schema outbox HMAC is invalid!"
            assert not os.path.exists(claimed_sch), "Claim envelope not unlinked after schema outbox reconciliation!"

        # ----------------------------------------------------------------------
        # Part C: Completion persistence failure with outbox write failure
        # ----------------------------------------------------------------------
        msg_id_cpf = "MSG-CPF-OUTBOX-FAIL"
        corr_id_cpf = "CORR-CPF-FAIL"
        claimed_cpf = os.path.join(inbox_dir, f"{msg_id_cpf}.claimed")
        outbox_cpf = os.path.join(outbox_dir, f"{corr_id_cpf}_resp.json")
        dead_cpf = os.path.join(dead_dir, f"{msg_id_cpf}.dead.json")

        store.admit_submission(msg_id_cpf, corr_id_cpf, "hash", "CODEX", "ANTIGRAVITY", "sub", get_current_ist())
        msg_cpf_data = {
            "message_id": msg_id_cpf,
            "correlation_id": corr_id_cpf,
            "sender": "CODEX",
            "recipient": "ANTIGRAVITY",
            "subject": "sub",
            "body": {"task": "cpf_test"},
            "status": "CLAIMED",
            "worker_pid": 999999,
            "attempt_count": 0,
            "created_at_ist": get_current_ist(),
            "nonce": "NONCE-CPF-FAIL",
        }
        msg_cpf_data["auth_signature"] = compute_envelope_hmac(msg_cpf_data, secret_key)
        with open(claimed_cpf, "w", encoding="utf-8") as f:
            json.dump(msg_cpf_data, f)

        with patch("antigravity.daemons.inbox_worker.INBOX_DIR", inbox_dir), \
             patch("antigravity.daemons.inbox_worker.OUTBOX_DIR", outbox_dir), \
             patch("antigravity.daemons.inbox_worker.DEAD_LETTER_DIR", dead_dir), \
             patch("antigravity.daemons.inbox_worker.get_agent_secret_key", return_value=secret_key):

            def write_fail_on_outbox_cpf(path, data, **kwargs):
                if str(path).endswith("_resp.json"):
                    raise OSError("Injected disk error writing completion outbox")
                return orig_write(path, data, **kwargs)

            # Injected failure: task completes but mark_message_completed returns False
            with patch.object(worker, "execute_task", return_value=("COMPLETED", "success_out", {}, None)), \
                 patch.object(store, "mark_message_completed", return_value=False), \
                 patch("antigravity.daemons.inbox_worker.write_json_atomic", side_effect=write_fail_on_outbox_cpf):

                worker._process_message_locked(claimed_cpf, msg_cpf_data)

            # Verification Pass 1:
            assert store.get_message_state(msg_id_cpf) == "DEAD"
            assert os.path.exists(dead_cpf)
            assert not os.path.exists(outbox_cpf)
            assert os.path.exists(claimed_cpf), "Claim envelope unlinked prematurely on completion outbox failure!"

            # Pass 2: Reconciles outbox and unlinks claim
            os.utime(claimed_cpf, (time.time() - 200, time.time() - 200))
            worker.recover_orphaned_claims()

            assert os.path.exists(outbox_cpf)
            with open(outbox_cpf, "r", encoding="utf-8") as f:
                cpf_resp = json.load(f)
            assert cpf_resp["status"] == "FAILED"
            assert cpf_resp["error"] == "COMPLETION_PERSISTENCE_FAILED"
            # Assert exact equality with durable response in SQLite
            durable_cpf = store.get_message_response(msg_id_cpf)
            assert durable_cpf is not None
            assert cpf_resp == json.loads(durable_cpf), "Reconciled completion outbox does not match durable SQLite response!"
            # Assert recovered HMAC signature is cryptographically valid
            sig_cpf = cpf_resp.get("auth_signature")
            assert sig_cpf == compute_envelope_hmac(cpf_resp, secret_key), "Recovered completion outbox HMAC is invalid!"
            assert not os.path.exists(claimed_cpf), "Claim envelope not unlinked after completion outbox reconciliation!"

        # ----------------------------------------------------------------------
        # Part D: Independent dead-letter reconciliation with claim absent
        # ----------------------------------------------------------------------
        msg_id_indep = "MSG-INDEP-NO-CLAIM"
        corr_id_indep = "CORR-INDEP-NO-CLAIM"
        outbox_indep = os.path.join(outbox_dir, f"{corr_id_indep}_resp.json")
        dead_indep = os.path.join(dead_dir, f"{msg_id_indep}.dead.json")

        store.admit_submission(msg_id_indep, corr_id_indep, "hash", "CODEX", "ANTIGRAVITY", "sub", get_current_ist())
        indep_resp = {
            "message_id": "resp_indep_123",
            "correlation_id": corr_id_indep,
            "status": "FAILED",
            "error": "INDEPENDENT_DEAD_RECONCILED",
        }
        store.mark_message_dead(msg_id_indep, error="INDEPENDENT_DEAD_RECONCILED", response_json=json.dumps(indep_resp))

        # Write dead letter file, NO .claimed file exists
        with open(dead_indep, "w", encoding="utf-8") as f:
            json.dump({
                "message_id": msg_id_indep,
                "correlation_id": corr_id_indep,
                "status": "FAILED",
                "error": "INDEPENDENT_DEAD_RECONCILED",
            }, f)

        assert not os.path.exists(outbox_indep)
        assert not os.path.exists(os.path.join(inbox_dir, f"{msg_id_indep}.claimed"))

        with patch("antigravity.daemons.inbox_worker.INBOX_DIR", inbox_dir), \
             patch("antigravity.daemons.inbox_worker.OUTBOX_DIR", outbox_dir), \
             patch("antigravity.daemons.inbox_worker.DEAD_LETTER_DIR", dead_dir):

            worker.recover_orphaned_claims()

            assert os.path.exists(outbox_indep), "Independent dead letter sweep failed to publish missing outbox file!"
            with open(outbox_indep, "r", encoding="utf-8") as f:
                reconciled_indep = json.load(f)
            assert reconciled_indep["status"] == "FAILED"
            assert reconciled_indep["error"] == "INDEPENDENT_DEAD_RECONCILED"
            # Assert exact equality with durable response in SQLite
            durable_indep = store.get_message_response(msg_id_indep)
            assert durable_indep is not None
            assert reconciled_indep == json.loads(durable_indep), "Reconciled independent outbox does not match durable SQLite response!"


# ==============================================================================
# PROBE 24: Dead-Letter Sweep FileLock Prevents Overwriting Concurrent Publisher
# ==============================================================================
def test_dead_letter_sweep_filelock_prevents_overwriting_concurrent_publisher():
    """
    Finding P1 (Codex 2026-10-01):
    Dead-letter recovery sweep must acquire the correlation FileLock and recheck existence
    before publishing to outbox, preventing any race condition where an older/failed dead-letter
    response overwrites a concurrently published winning response installed by a live worker.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        inbox_dir = os.path.join(tmpdir, "inbox")
        outbox_dir = os.path.join(tmpdir, "outbox")
        dead_dir = os.path.join(tmpdir, "dead_letter")
        os.makedirs(inbox_dir, exist_ok=True)
        os.makedirs(outbox_dir, exist_ok=True)
        os.makedirs(dead_dir, exist_ok=True)

        db_path = os.path.join(tmpdir, "admissions.db")
        store = DurableAdmissionStore(db_path)

        corr_id = "CORR-RACE-C"
        msg_id = "MSG-OLD-DEAD"
        outbox_file = os.path.join(outbox_dir, f"{corr_id}_resp.json")
        dead_file = os.path.join(dead_dir, f"{msg_id}.dead.json")

        store.admit_submission(msg_id, corr_id, "hash", "CODEX", "ANTIGRAVITY", "sub", get_current_ist())
        old_reply = {"message_id": "old_reply", "correlation_id": corr_id, "status": "FAILED"}
        store.mark_message_dead(msg_id, error="OLD_FAILURE", response_json=json.dumps(old_reply))

        # Write dead-letter file
        dead_data = {
            "message_id": msg_id,
            "correlation_id": corr_id,
            "status": "FAILED",
            "error": "OLD_FAILURE",
        }
        with open(dead_file, "w", encoding="utf-8") as f:
            json.dump(dead_data, f)

        # Injected interleaving: When recovery sweep enters FileLock on outbox_file,
        # simulate a concurrent publisher installing winning_reply BEFORE the recovery
        # code re-checks existence and writes.
        real_file_lock_enter = FileLock.__enter__
        winning_reply = {"message_id": "winning_reply", "correlation_id": corr_id, "status": "COMPLETED"}

        def hooked_lock_enter(lock_self):
            res = real_file_lock_enter(lock_self)
            # If this is the outbox lock for our correlation ID and file does not exist yet,
            # simulate concurrent publisher installing winning_reply right after lock is acquired!
            if lock_self.lock_path.startswith(outbox_file) and not os.path.exists(outbox_file):
                write_json_atomic(outbox_file, winning_reply)
            return res

        with patch("antigravity.daemons.inbox_worker.INBOX_DIR", inbox_dir), \
             patch("antigravity.daemons.inbox_worker.OUTBOX_DIR", outbox_dir), \
             patch("antigravity.daemons.inbox_worker.DEAD_LETTER_DIR", dead_dir), \
             patch.object(FileLock, "__enter__", side_effect=hooked_lock_enter, autospec=True):

            worker = InboxWorker()
            worker.db = store
            worker.recover_orphaned_claims()

            # Verify that winning_reply was NOT overwritten by old_reply!
            assert os.path.exists(outbox_file)
            with open(outbox_file, "r", encoding="utf-8") as f:
                final_outbox = json.load(f)
            assert final_outbox["message_id"] == "winning_reply", f"Winning reply was overwritten! Got: {final_outbox}"
            assert final_outbox["status"] == "COMPLETED"


# ==============================================================================
# PROBE 25: Claim Validation Failure FileLock Prevents Overwriting Winning Response
# ==============================================================================
def test_claim_validation_failure_filelock_prevents_overwriting_winning_response():
    """
    Finding P1 (Codex 2026-10-01):
    In claim_message, when inbound message validation fails, outbox failure response
    publication must acquire the correlation FileLock and recheck existence before writing,
    preventing any race condition where a failed claim response overwrites a winning
    COMPLETED response concurrently installed by a live worker.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        inbox_dir = os.path.join(tmpdir, "inbox")
        outbox_dir = os.path.join(tmpdir, "outbox")
        dead_dir = os.path.join(tmpdir, "dead_letter")
        os.makedirs(inbox_dir, exist_ok=True)
        os.makedirs(outbox_dir, exist_ok=True)
        os.makedirs(dead_dir, exist_ok=True)

        db_path = os.path.join(tmpdir, "admissions.db")
        store = DurableAdmissionStore(db_path)
        secret_key = b"secret_key_32_bytes_long_123456"

        corr_id = "CORR-CLAIM-WINNER"
        msg_id = "MSG-BAD-CLAIM"
        inbox_file = os.path.join(inbox_dir, f"{msg_id}.json")
        outbox_file = os.path.join(outbox_dir, f"{corr_id}_resp.json")

        store.admit_submission(msg_id, corr_id, "hash", "CODEX", "ANTIGRAVITY", "sub", get_current_ist())

        # Invalid envelope (missing fields, invalid schema)
        bad_msg = {
            "message_id": msg_id,
            "correlation_id": corr_id,
            "status": "CREATED",
            # missing recipient, sender, body, nonce, signature
        }
        with open(inbox_file, "w", encoding="utf-8") as f:
            json.dump(bad_msg, f)

        real_file_lock_enter = FileLock.__enter__
        winning_reply = {"message_id": "winner_reply_001", "correlation_id": corr_id, "status": "COMPLETED"}

        def hooked_lock_enter(lock_self):
            res = real_file_lock_enter(lock_self)
            # If this is the outbox lock for our correlation ID and file does not exist yet,
            # simulate concurrent publisher installing winning_reply right after lock is acquired!
            if lock_self.lock_path.startswith(outbox_file) and not os.path.exists(outbox_file):
                write_json_atomic(outbox_file, winning_reply)
            return res

        with patch("antigravity.daemons.inbox_worker.INBOX_DIR", inbox_dir), \
             patch("antigravity.daemons.inbox_worker.OUTBOX_DIR", outbox_dir), \
             patch("antigravity.daemons.inbox_worker.DEAD_LETTER_DIR", dead_dir), \
             patch("antigravity.daemons.inbox_worker.get_agent_secret_key", return_value=secret_key), \
             patch.object(FileLock, "__enter__", side_effect=hooked_lock_enter, autospec=True):

            worker = InboxWorker()
            worker.db = store
            res = worker.claim_message(f"{msg_id}.json")
            assert res is None  # Validation failed

            # Verification: winning_reply must NOT have been overwritten by FAILED claim response!
            assert os.path.exists(outbox_file)
            with open(outbox_file, "r", encoding="utf-8") as f:
                final_out = json.load(f)
            assert final_out["message_id"] == "winner_reply_001", f"Winning reply was overwritten by claim validation failure! Got: {final_out}"
            assert final_out["status"] == "COMPLETED"



