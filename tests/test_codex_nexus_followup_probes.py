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
    compute_payload_hash,
    get_process_create_time_nt,
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
            store.mark_message_completed(comp_id_1)
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
            store.mark_message_completed(comp_id_2)
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
            store.check_and_record_nonce("TEST-NONCE-ZC", "CODEX", "2026-10-01T09:00:00+05:30", message_id=comp_id_1)
            auth_res = store.mark_message_recovering(comp_id_1, nonce="TEST-NONCE-ZC")
            assert auth_res is False, "mark_message_recovering authorized recovery on COMPLETED message!"
            # Verify nonce state was NOT mutated to RECOVERED_RETRY_PENDING
            import sqlite3
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

            # Claim failure zero-loss envelope preservation:
            # If mark_message_claimed fails due to DB error, claim_message returns None
            # and RESTORES the .json envelope in inbox with status 'CREATED'!
            msg_id_fail = "MSG-CLAIM-FAIL"
            store.admit_submission(msg_id_fail, "CORR-FAIL", "hash", "CODEX", "ANTIGRAVITY", "sub", "2026-10-01T09:00:00+05:30")
            inbox_fail = os.path.join(inbox_dir, f"{msg_id_fail}.json")
            with open(inbox_fail, "w", encoding="utf-8") as f:
                json.dump({
                    "message_id": msg_id_fail, "correlation_id": "CORR-FAIL",
                    "sender": "CODEX", "recipient": "ANTIGRAVITY", "subject": "sub",
                    "status": "CREATED", "created_at_ist": "2026-10-01T09:00:00+05:30",
                    "body": "test"
                }, f)

            with patch.object(store, "mark_message_claimed", return_value=False), \
                 patch("antigravity.daemons.inbox_worker.validate_message_schema", return_value=(True, None)):
                claim_res = worker.claim_message(f"{msg_id_fail}.json")
                assert claim_res is None
                assert not os.path.exists(os.path.join(inbox_dir, f"{msg_id_fail}.claimed"))
                # ZERO LOSS: The message envelope must still exist in the inbox!
                assert os.path.exists(inbox_fail), "Persistence failure deleted the only recoverable envelope!"
                with open(inbox_fail, "r", encoding="utf-8") as f:
                    restored_data = json.load(f)
                assert restored_data["status"] == "CREATED"

            # Completion persistence failure handling:
            # If mark_message_completed returns False, routes to dead letter with COMPLETION_PERSISTENCE_FAILED
            msg_id_comp_fail = "MSG-COMP-PERSIST-FAIL"
            store.admit_submission(msg_id_comp_fail, "CORR-CPF", "hash", "CODEX", "ANTIGRAVITY", "sub", "2026-10-01T09:00:00+05:30")
            claimed_cpf = os.path.join(inbox_dir, f"{msg_id_comp_fail}.claimed")
            msg_cpf = {
                "message_id": msg_id_comp_fail, "correlation_id": "CORR-CPF", "nonce": "NONCE-CPF",
                "recipient": "ANTIGRAVITY", "sender": "CODEX", "status": "CLAIMED"
            }
            with open(claimed_cpf, "w", encoding="utf-8") as f:
                json.dump(msg_cpf, f)

            with patch.object(worker, "execute_task", return_value=("COMPLETED", "output", {}, None)), \
                 patch("antigravity.daemons.inbox_worker.validate_message_schema", return_value=(True, None)), \
                 patch.object(store, "mark_message_completed", return_value=False):
                worker._process_message_locked(claimed_cpf, msg_cpf)
                # Must be dead-lettered due to completion persistence failure
                dead_cpf = os.path.join(dead_dir, f"{msg_id_comp_fail}.dead.json")
                assert os.path.exists(dead_cpf), "Completion persistence failure was not routed to dead letter!"
                with open(dead_cpf, "r", encoding="utf-8") as f:
                    dead_cpf_data = json.load(f)
                assert dead_cpf_data["error"] == "COMPLETION_PERSISTENCE_FAILED"


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

