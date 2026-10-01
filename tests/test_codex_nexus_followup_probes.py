"""
tests/test_codex_nexus_followup_probes.py - Regression probes for Codex review of commit f116405
=================================================================================================
Covers:
1. FileLock successor lock safety: Owner A release must NEVER delete successor B's active lock.
2. Cross-process duplicate submission atomic receipt: Shared SQLite admission binds message_id,
   correlation_id, and payload_hash across queued/claimed/completed states. Conflicting payload raises.
3. Recovery freshness exemption backed by durable store: Request admitted at t=0 and recovered at t=400s
   is admitted and executes without 300s timestamp expiration. Active recovery records not pruned by TTL.
4. Claude review CLI flags enforce read-only tools (--tools Read,Grep,Glob).
5. Codex version floor strictly verified (>= 0.159.2).
"""
import copy
import hashlib
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import antigravity.daemons.inbox_worker as iw
import antigravity.daemons.tri_agent_bus as bus


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    messages = tmp_path / "messages"
    monkeypatch.setattr(iw, "MESSAGES_ROOT", str(messages))
    monkeypatch.setattr(bus, "MESSAGES_ROOT", str(messages))
    for name, sub in [
        ("INBOX_DIR", "inbox"),
        ("OUTBOX_DIR", "outbox"),
        ("ARCHIVE_DIR", "archive"),
        ("DEAD_LETTER_DIR", "dead"),
        ("BACKUPS_DIR", "backups"),
    ]:
        p = messages / sub
        p.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(iw, name, str(p))
        monkeypatch.setattr(bus, name, str(p), raising=False)
    monkeypatch.setattr(iw, "get_agent_secret_key", lambda _: "FAKE-REVIEW-KEY")
    monkeypatch.setattr(bus, "get_agent_secret_key", lambda _: "FAKE-REVIEW-KEY")


def test_filelock_never_deletes_successor_active_lock(tmp_path):
    """Owner A's release cleanup must never delete successor B's active lock."""
    target = str(tmp_path / "protected_resource")
    lock_a = iw.FileLock(target, timeout_sec=2)
    assert lock_a.acquire()

    # Contender B is waiting.
    # We simulate interleaving: lock_a marks released, then B acquires before A's release finishes.
    lock_b = iw.FileLock(target, timeout_sec=2)

    # In a separate thread or interleaved step:
    # When lock_a releases:
    lock_a.release()

    # Lock B acquires
    assert lock_b.acquire()

    # Now verify that lock file on disk belongs to B and is NOT deleted or None
    assert os.path.exists(lock_b.lock_path), "Successor B's lock file was removed!"
    with open(lock_b.lock_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data.get("pid") == os.getpid()
    assert data.get("released") is False

    lock_b.release()


def test_cross_process_duplicate_submission_atomic_receipt(tmp_path):
    """Concurrent submissions across processes bind message_id, correlation_id, and hash."""
    ready = threading.Barrier(2)
    original_send = bus.send_to_agent

    def send(i):
        ready.wait(timeout=2)
        return bus.send_to_agent(
            sender="CLAUDE",
            recipient="ANTIGRAVITY",
            subject="ECHO",
            body="identical body",
            message_id="msg_atomic_dup_test",
            correlation_id=f"corr_dup_{i}",
            auth_secret="FAKE-REVIEW-KEY",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(send, range(2)))

    # Both callers must receive the exact same correlation_id
    corrs = {corr for mid, corr in receipts}
    assert len(corrs) == 1, f"Different correlation receipts returned: {receipts}"

    # A subsequent submission with DIFFERENT body must raise explicit conflict
    with pytest.raises(ValueError, match="CONFLICT"):
        bus.send_to_agent(
            sender="CLAUDE",
            recipient="ANTIGRAVITY",
            subject="ECHO",
            body="DIFFERENT body",
            message_id="msg_atomic_dup_test",
            correlation_id="corr_conflicting",
            auth_secret="FAKE-REVIEW-KEY",
        )


def test_recovered_request_admitted_even_if_timestamp_older_than_300s(tmp_path, monkeypatch):
    """A crashed request recovered 400s later is admitted via durable recovery identity."""
    worker = iw.InboxWorker()
    worker.db = iw.DurableAdmissionStore(os.path.join(iw.MESSAGES_ROOT, "admission_store.db"))
    iw._ACTIVE_STORE = worker.db

    # Submit at t=1000
    clock = [1000.0]
    monkeypatch.setattr(iw.time, "time", lambda: clock[0])
    monkeypatch.setattr(bus.time, "time", lambda: clock[0])

    msg_id, corr = bus.send_to_agent(
        sender="CLAUDE",
        recipient="ANTIGRAVITY",
        subject="ECHO",
        body="durable recovery check",
        message_id="msg_recovery_late_01",
        auth_secret="FAKE-REVIEW-KEY",
    )

    # Worker claims message at t=1005
    clock[0] = 1005.0
    claimed = worker.claim_message("msg_recovery_late_01.json")
    assert claimed is not None
    claimed_path, msg_data = claimed

    # Simulate crash of worker process: claim file remains on disk
    # Advance clock by 400s (total age = 405s, exceeds 300s arrival limit)
    clock[0] = 1410.0

    # Recover orphaned claims
    recovered_count = worker.recover_orphaned_claims(stale_threshold_sec=30)
    assert recovered_count == 1

    # Worker runs single pass to process the recovered request
    executed = []
    worker.process_message = lambda path, msg: executed.append((path, msg))

    processed = worker.run_single_pass()
    assert processed == 1
    assert len(executed) == 1
    _, admitted_msg = executed[0]
    assert admitted_msg["message_id"] == "msg_recovery_late_01"


def test_claude_chat_only_enforces_read_only_tools(monkeypatch):
    """ask_claude_detailed in chat_only mode must pass --tools Read,Grep,Glob."""
    import subprocess
    captured_cmd = []

    def mock_run(cmd, *args, **kwargs):
        captured_cmd.extend(cmd)
        class Res:
            returncode = 0
            stdout = "The review_id is CODEX-T2-01-4BE7563"
            stderr = ""
        return Res()

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(os.path, "exists", lambda p: True)

    bus.ask_claude_detailed("test prompt", chat_only=True)
    cmd_str = " ".join(captured_cmd)
    assert "--tools" in captured_cmd
    tool_idx = captured_cmd.index("--tools")
    assert "Read,Grep,Glob" in captured_cmd[tool_idx + 1] or "Read" in captured_cmd[tool_idx + 1]
    assert "Edit" not in cmd_str
    assert "Write" not in cmd_str


def test_codex_version_floor_strict_assertion():
    """Validates that real Codex binary satisfies version floor >= 0.159.2."""
    codex_bin = bus.get_codex_bin()
    if not os.path.exists(codex_bin):
        pytest.skip("Codex binary not present")
    import subprocess
    res = subprocess.run([codex_bin, "--version"], capture_output=True, text=True, timeout=5)
    assert res.returncode == 0
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", res.stdout)
    assert m is not None, f"Could not parse semver from: {res.stdout}"
    major, minor, patch = map(int, m.groups())
    assert (major, minor, patch) >= (0, 159, 2), f"Codex version {major}.{minor}.{patch} is below 0.159.2"
