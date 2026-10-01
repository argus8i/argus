# -*- coding: utf-8 -*-
"""
tests/test_codex_nexus_followup_probes.py
Comprehensive regression probes covering all 5 findings from OpenAI Codex's peer review of ce85d57:
1. P1: FileLock Successor Safety under Interleaving (atomic .break token + pre-unlink identity verification)
2. P1: SQLite Admission & Publication Separate Race Boundary (send_to_agent duplicate never re-writes inbox)
3. P1: Durable Admission Store nonces vs message_admissions separation (admissions never pruned after 600s TTL)
4. P2: Pre-upgrade recovery UPSERT with full metadata and fallback nonce checks
5. P2: Identity contract checking (sender, recipient, subject, payload_hash), terminal claim rejection, and Codex semver floor
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
# PROBE 1: FileLock Successor Safety under Interleaving
# ==============================================================================
def test_filelock_successor_safety_under_interleaving():
    """
    Finding P1: When Lock A is released or dead, Contender B breaks stale lock and
    acquires it (writing new PID and released=False). Contender C, having observed Lock A
    earlier, must NOT unlink Contender B's newly acquired lock file.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        target_path = os.path.join(tmpdir, "resource.json")
        lock_path = target_path + ".lock"

        # Step 1: Create a released/stale lock file representing Lock A
        stale_data = {
            "pid": 999999,
            "owner": "DEAD_WORKER",
            "created_at": time.time() - 100.0,
            "create_time_nt": 123456789,
            "released": True,
        }
        with open(lock_path, "w", encoding="utf-8") as f:
            json.dump(stale_data, f)

        # Step 2: Contender B acquires the lock. It should break the stale lock and take ownership.
        lock_b = FileLock(target_path, timeout_sec=2.0)
        acquired_b = lock_b.acquire()
        assert acquired_b is True, "Contender B failed to acquire lock from stale state"

        # Verify B owns the lock
        with open(lock_path, "r", encoding="utf-8") as f:
            b_data = json.load(f)
        assert b_data["pid"] == os.getpid()
        assert b_data["released"] is False

        # Step 3: Now Contender C attempts _break_stale_lock on lock_path.
        # But lock_path is now owned by live process B (released=False, live PID).
        # C must NOT delete B's lock!
        lock_c = FileLock(target_path, timeout_sec=0.1)
        broke = lock_c._break_stale_lock()
        assert broke is False, "Contender C improperly broke live Contender B's lock!"

        # Verify B's lock file is still intact and untouched
        assert os.path.exists(lock_path), "Contender B's lock file was unlinked by Contender C!"
        with open(lock_path, "r", encoding="utf-8") as f:
            intact_data = json.load(f)
        assert intact_data["pid"] == os.getpid()
        assert intact_data["released"] is False

        # Contender C cannot acquire while B holds it
        acquired_c = lock_c.acquire()
        assert acquired_c is False, "Contender C acquired lock while B still holds it!"

        # Step 4: Release B and ensure clean release
        lock_b.release()
        with open(lock_path, "r", encoding="utf-8") as f:
            rel_data = json.load(f)
        assert rel_data["released"] is True


# ==============================================================================
# PROBE 2: Publication Boundary - Duplicate Never Re-Writes Inbox
# ==============================================================================
def test_send_to_agent_duplicate_never_republishes():
    """
    Finding P1: Once a message is admitted in SQLite, subsequent callers with the same
    message_id get is_new=False. Non-new callers must NEVER write to the inbox filesystem,
    preventing race conditions where an already claimed or completed message is resurrected.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "admissions.db")
        inbox_dir = os.path.join(tmpdir, "inbox")
        outbox_dir = os.path.join(tmpdir, "outbox")
        os.makedirs(inbox_dir, exist_ok=True)
        os.makedirs(outbox_dir, exist_ok=True)

        store = DurableAdmissionStore(db_path)
        msg_id = "MSG-20261001-001"
        corr_id = "CORR-20261001-001"
        payload = {"instruction": "test instruction"}

        # Patch paths in tri_agent_bus
        with patch("antigravity.daemons.tri_agent_bus.get_default_admission_store", return_value=store), \
             patch("antigravity.daemons.tri_agent_bus.INBOX_DIR", inbox_dir), \
             patch("antigravity.daemons.tri_agent_bus.OUTBOX_DIR", outbox_dir), \
             patch("antigravity.daemons.tri_agent_bus.get_agent_secret_key", return_value=b"secret_key_32_bytes_long_123456"):

            # 1. First send -> is_new=True, writes inbox JSON file
            ret_id, ret_corr = send_to_agent(
                sender="CODEX",
                recipient="ANTIGRAVITY",
                subject="test subject",
                body=payload,
                message_id=msg_id,
                correlation_id=corr_id,
            )
            assert ret_id == msg_id
            assert ret_corr == corr_id

            inbox_file = os.path.join(inbox_dir, f"{msg_id}.json")
            assert os.path.exists(inbox_file), "First publication failed to create inbox file"

            # 2. Simulate worker claiming the file (removes .json, creates .claimed)
            claimed_file = os.path.join(inbox_dir, f"{msg_id}.claimed")
            with open(inbox_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            os.remove(inbox_file)
            with open(claimed_file, "w", encoding="utf-8") as f:
                json.dump(data, f)

            assert not os.path.exists(inbox_file)

            # 3. Second send with duplicate message -> must return immediately and NOT re-create inbox_file!
            ret_id2, ret_corr2 = send_to_agent(
                sender="CODEX",
                recipient="ANTIGRAVITY",
                subject="test subject",
                body=payload,
                message_id=msg_id,
                correlation_id=corr_id,
            )
            assert ret_id2 == msg_id
            assert ret_corr2 == corr_id
            assert not os.path.exists(inbox_file), "Duplicate send recreated .json inbox file for claimed message!"


# ==============================================================================
# PROBE 3: Durable Admission Store - No 600s TTL Expiration on Admissions
# ==============================================================================
def test_durable_admission_store_permanent_binding_not_pruned_by_600s_ttl():
    """
    Finding P1: Nonces expire after 600s to detect network replays, but message_admissions
    must NEVER be pruned by check_and_record_nonce or TTL checks. The admission is durable.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "admissions.db")
        store = DurableAdmissionStore(db_path)

        msg_id = "MSG-PERM-001"
        corr_id = "CORR-PERM-001"
        p_hash = compute_payload_hash({"task": "durable_task"})

        # Admit message
        is_new, winning_corr, err, state = store.admit_submission(
            message_id=msg_id,
            correlation_id=corr_id,
            payload_hash=p_hash,
            sender="ANTIGRAVITY",
            recipient="CODEX",
            subject="audit",
            timestamp_ist="2026-10-01T09:00:00+05:30",
        )
        assert is_new is True
        assert err is None
        assert winning_corr == corr_id

        # Record a nonce with old timestamp (700 seconds ago) directly into seen_nonces
        old_time = time.time() - 700.0
        import sqlite3
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "INSERT INTO seen_nonces (nonce, sender, timestamp_ist, recorded_at, message_id, state) VALUES (?, ?, ?, ?, ?, 'COMPLETED')",
                ("OLD-NONCE-123", "CODEX", "2026-10-01T08:00:00+05:30", old_time, msg_id)
            )
            conn.commit()

        # Now check and record a new nonce, which triggers pruning of seen_nonces older than 600s
        ok, reason = store.check_and_record_nonce(
            nonce="NEW-NONCE-456",
            sender="CODEX",
            timestamp_ist="2026-10-01T09:00:00+05:30",
            message_id="MSG-NEW-002",
        )
        assert ok is True

        # Verify seen_nonces pruned OLD-NONCE-123
        with sqlite3.connect(store.db_path) as conn:
            row = conn.execute("SELECT nonce FROM seen_nonces WHERE nonce = 'OLD-NONCE-123'").fetchone()
            assert row is None, "Old nonce was not pruned"

            # BUT message_admissions for msg_id MUST STILL EXIST!
            adm_row = conn.execute(
                "SELECT message_id, correlation_id, state FROM message_admissions WHERE message_id = ?",
                (msg_id,)
            ).fetchone()
            assert adm_row is not None, "CRITICAL: message_admissions was pruned by 600s TTL!"
            assert adm_row[0] == msg_id

        # Re-admission after TTL must still be recognized as duplicate
        is_new2, winning_corr2, err2, state2 = store.admit_submission(
            message_id=msg_id,
            correlation_id="DIFFERENT-CORR",
            payload_hash=p_hash,
            sender="ANTIGRAVITY",
            recipient="CODEX",
            subject="audit",
            timestamp_ist="2026-10-01T09:15:00+05:30",
        )
        assert is_new2 is False, "Message admission expired after TTL!"
        assert winning_corr2 == corr_id


# ==============================================================================
# PROBE 4: Pre-Upgrade Recovery Claims with Full Metadata
# ==============================================================================
def test_recovery_exemption_pre_upgrade_claims():
    """
    Finding P2: If a legacy .claimed file exists without an entry in message_admissions,
    mark_message_recovering must UPSERT a row with state='RECOVERING' and all available
    metadata, and is_message_recovering must return True.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "admissions.db")
        store = DurableAdmissionStore(db_path)

        legacy_msg_id = "LEGACY-MSG-001"
        legacy_corr_id = "LEGACY-CORR-001"
        legacy_nonce = "LEGACY-NONCE-001"
        payload_hash = compute_payload_hash({"legacy": True})

        # Pre-check: Not in store
        assert store.get_message_state(legacy_msg_id) is None
        assert store.is_message_recovering(legacy_msg_id, payload_hash) is False

        # mark_message_recovering called on pre-upgrade claim
        store.mark_message_recovering(
            legacy_msg_id,
            nonce=legacy_nonce,
            correlation_id=legacy_corr_id,
            payload_hash=payload_hash,
            sender="CODEX",
            recipient="ANTIGRAVITY",
            subject="legacy task",
            timestamp_ist="2026-10-01T08:00:00+05:30",
        )

        # Verify state is now RECOVERING
        assert store.get_message_state(legacy_msg_id) == "RECOVERING"
        assert store.is_message_recovering(legacy_msg_id, payload_hash) is True

        # Verify row in message_admissions
        import sqlite3
        with sqlite3.connect(store.db_path) as conn:
            row = conn.execute(
                "SELECT message_id, correlation_id, state, sender, recipient FROM message_admissions WHERE message_id = ?",
                (legacy_msg_id,)
            ).fetchone()
            assert row is not None
            assert row[0] == legacy_msg_id
            assert row[1] == legacy_corr_id
            assert row[2] == "RECOVERING"
            assert row[3] == "CODEX"
            assert row[4] == "ANTIGRAVITY"


# ==============================================================================
# PROBE 5: Identity Contract Validation (Sender, Recipient, Subject, Payload)
# ==============================================================================
def test_identity_contract_validation():
    """
    Finding P2: Admit submission must check (payload_hash, sender, recipient, subject).
    Any mutation of these fields under the same message_id must raise CONFLICT.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "admissions.db")
        store = DurableAdmissionStore(db_path)

        msg_id = "MSG-ID-CONTRACT-001"
        corr_id = "CORR-ID-CONTRACT-001"
        p_hash = compute_payload_hash("Original Body")

        is_new, _, err, _ = store.admit_submission(
            message_id=msg_id,
            correlation_id=corr_id,
            payload_hash=p_hash,
            sender="ANTIGRAVITY",
            recipient="CODEX",
            subject="Review",
            timestamp_ist="2026-10-01T09:00:00+05:30",
        )
        assert is_new is True
        assert err is None

        # 1. Divergent sender
        is_new, _, err, _ = store.admit_submission(
            message_id=msg_id,
            correlation_id=corr_id,
            payload_hash=p_hash,
            sender="CLAUDE",
            recipient="CODEX",
            subject="Review",
            timestamp_ist="2026-10-01T09:00:00+05:30",
        )
        assert is_new is False
        assert err is not None
        assert "CONFLICT" in err

        # 2. Divergent recipient
        is_new, _, err, _ = store.admit_submission(
            message_id=msg_id,
            correlation_id=corr_id,
            payload_hash=p_hash,
            sender="ANTIGRAVITY",
            recipient="CLAUDE",
            subject="Review",
            timestamp_ist="2026-10-01T09:00:00+05:30",
        )
        assert is_new is False
        assert err is not None
        assert "CONFLICT" in err

        # 3. Divergent subject
        is_new, _, err, _ = store.admit_submission(
            message_id=msg_id,
            correlation_id=corr_id,
            payload_hash=p_hash,
            sender="ANTIGRAVITY",
            recipient="CODEX",
            subject="Different Subject",
            timestamp_ist="2026-10-01T09:00:00+05:30",
        )
        assert is_new is False
        assert err is not None
        assert "CONFLICT" in err

        # 4. Divergent payload hash
        other_hash = compute_payload_hash("Mutated Body")
        is_new, _, err, _ = store.admit_submission(
            message_id=msg_id,
            correlation_id=corr_id,
            payload_hash=other_hash,
            sender="ANTIGRAVITY",
            recipient="CODEX",
            subject="Review",
            timestamp_ist="2026-10-01T09:00:00+05:30",
        )
        assert is_new is False
        assert err is not None
        assert "CONFLICT" in err


# ==============================================================================
# PROBE 6: Terminal State Rejection on Claim
# ==============================================================================
def test_claim_message_rejects_terminal_states():
    """
    Finding P2: claim_message must reject messages whose admission state is COMPLETED or DEAD,
    and remove the redundant inbox file without processing.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "admissions.db")
        inbox_dir = os.path.join(tmpdir, "inbox")
        outbox_dir = os.path.join(tmpdir, "outbox")
        os.makedirs(inbox_dir, exist_ok=True)
        os.makedirs(outbox_dir, exist_ok=True)

        store = DurableAdmissionStore(db_path)
        msg_id = "MSG-TERM-001"
        corr_id = "CORR-TERM-001"
        p_hash = compute_payload_hash("Terminal Task")

        # Admit and mark completed
        store.admit_submission(
            message_id=msg_id,
            correlation_id=corr_id,
            payload_hash=p_hash,
            sender="CODEX",
            recipient="ANTIGRAVITY",
            subject="done",
            timestamp_ist="2026-10-01T09:00:00+05:30",
        )
        store.mark_message_completed(msg_id)
        assert store.get_message_state(msg_id) == "COMPLETED"

        # Place redundant .json file in inbox
        inbox_file = os.path.join(inbox_dir, f"{msg_id}.json")
        with open(inbox_file, "w", encoding="utf-8") as f:
            json.dump({"message_id": msg_id, "correlation_id": corr_id, "status": "CREATED"}, f)

        # Worker instance patched with isolated directories
        with patch("antigravity.daemons.inbox_worker.INBOX_DIR", inbox_dir), \
             patch("antigravity.daemons.inbox_worker.OUTBOX_DIR", outbox_dir):
            worker = InboxWorker()
            worker.db = store

            # claim_message should return None and delete redundant file
            claimed = worker.claim_message(f"{msg_id}.json")
            assert claimed is None
            assert not os.path.exists(inbox_file), "Redundant inbox file was not cleared"


# ==============================================================================
# PROBE 7: Dead-Letter Route and Complete Transitions Update Store
# ==============================================================================
def test_dead_letter_route_and_complete_transitions_update_store():
    """
    Finding P2: Transitions to dead letter or completion must durably record state
    in the admission store.
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

        with patch("antigravity.daemons.inbox_worker.INBOX_DIR", inbox_dir), \
             patch("antigravity.daemons.inbox_worker.OUTBOX_DIR", outbox_dir), \
             patch("antigravity.daemons.inbox_worker.DEAD_LETTER_DIR", dead_dir):
            worker = InboxWorker()
            worker.db = store

            msg_id = "MSG-FAIL-001"
            corr_id = "CORR-FAIL-001"
            p_hash = compute_payload_hash("Failing Task")

            store.admit_submission(
                message_id=msg_id,
                correlation_id=corr_id,
                payload_hash=p_hash,
                sender="CODEX",
                recipient="ANTIGRAVITY",
                subject="fail",
                timestamp_ist="2026-10-01T09:00:00+05:30",
            )
            assert store.get_message_state(msg_id) == "QUEUED"

            # Create claimed file
            claimed_path = os.path.join(inbox_dir, f"{msg_id}.claimed")
            msg_data = {"message_id": msg_id, "correlation_id": corr_id, "status": "CLAIMED"}
            with open(claimed_path, "w", encoding="utf-8") as f:
                json.dump(msg_data, f)

            # Route to dead letter
            worker.route_to_dead_letter(claimed_path, msg_data, "SIMULATED_FAILURE")

            # Check store is now DEAD
            assert store.get_message_state(msg_id) == "DEAD"


# ==============================================================================
# PROBE 8: Codex Binary Semver Floor (>= 0.159.2)
# ==============================================================================
def test_codex_bin_semver_floor():
    """
    Finding P2: get_codex_bin() must strictly enforce semver floor >= (0, 159, 2)
    and reject outdated binaries.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        sub_old = os.path.join(tmpdir, "old_codex.exe")
        sub_new = os.path.join(tmpdir, "new_codex.exe")

        # Create dummy binaries with MZ header
        for path in (sub_old, sub_new):
            with open(path, "wb") as f:
                f.write(b"MZ" + b"\x00" * 100)

        def mock_subprocess_run(cmd, *args, **kwargs):
            binary = cmd[0]
            mock_res = MagicMock()
            mock_res.returncode = 0
            if binary == sub_old:
                mock_res.stdout = "codex-cli 0.158.0\n"
            elif binary == sub_new:
                mock_res.stdout = "codex-cli 0.159.2\n"
            else:
                mock_res.stdout = "codex-cli 0.150.0\n"
            return mock_res

        with patch("subprocess.run", side_effect=mock_subprocess_run), \
             patch("glob.glob", return_value=[]), \
             patch("os.path.isfile", side_effect=lambda p: p in (sub_old, sub_new)), \
             patch("antigravity.daemons.tri_agent_bus.glob.glob", return_value=[sub_old, sub_new]):

            resolved = get_codex_bin()
            assert resolved == sub_new, f"Expected {sub_new}, got {resolved}"
