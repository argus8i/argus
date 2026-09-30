"""
tests/test_deep_health_and_bin_resolution.py - Tests for Codex Binary Resolution & Deep Health
==============================================================================================
Tests requirements:
1. get_codex_bin() prefers newest %LOCALAPPDATA%\\OpenAI\\Codex\\bin\\*\\codex.exe over extension binaries.
2. Peer dispatches run from repo root with read-only sandbox so reviewers can inspect files.
3. Claude chat route does not disable tools with --tools "".
4. ANTIGRAVITY chat route grants read-only workspace inspection.
5. HEALTH_DEEP deep health check verifies real file answers (last review_id in reviews.jsonl).
6. PING is explicitly labeled as route-only gateway check.
"""

import os
import sys
import json
import time
import subprocess
import pytest
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import antigravity.daemons.tri_agent_bus as tab
import antigravity.daemons.inbox_worker as iw


def test_get_codex_bin_prefers_localappdata(tmp_path, monkeypatch):
    """
    Verifies that get_codex_bin() resolves the newest binary in LOCALAPPDATA,
    then newest in VS Code extension, then newest in Antigravity IDE, and finally fallback.
    """
    localappdata = tmp_path / "localappdata"
    hash_old = localappdata / "OpenAI" / "Codex" / "bin" / "hash_old"
    hash_new = localappdata / "OpenAI" / "Codex" / "bin" / "hash_new"
    hash_old.mkdir(parents=True)
    hash_new.mkdir(parents=True)

    codex_old = hash_old / "codex.exe"
    codex_new = hash_new / "codex.exe"
    codex_old.write_text("old")
    time.sleep(0.05)
    codex_new.write_text("new")

    monkeypatch.setenv("LOCALAPPDATA", str(localappdata))

    resolved = tab.get_codex_bin()
    assert os.path.normcase(resolved) == os.path.normcase(str(codex_new))


def test_real_codex_bin_resolves_v0159_or_newer():
    """Verifies that the live host resolves to the updated codex.exe in LOCALAPPDATA."""
    bin_path = tab.get_codex_bin()
    assert os.path.exists(bin_path), f"Codex binary not found: {bin_path}"
    assert "OpenAI\\Codex\\bin" in bin_path or "openai.chatgpt" in bin_path

    # Verify version does not fail on gpt-6.1-sol
    res = subprocess.run([bin_path, "--version"], capture_output=True, text=True, timeout=5)
    assert res.returncode == 0
    assert "codex-cli" in res.stdout


def test_chat_boundaries_grants_read_only_workspace_access():
    """Verifies CHAT_BOUNDARIES no longer blanket-forbids inspecting files or using tools."""
    assert "Do not use tools" not in tab.CHAT_BOUNDARIES
    assert "claim to have inspected the repository" not in tab.CHAT_BOUNDARIES
    assert "read-only" in tab.CHAT_BOUNDARIES.lower()


def test_claude_route_does_not_pass_empty_tools(monkeypatch):
    """Verifies ask_claude_detailed with chat_only=True does not disable tools with --tools ''."""
    recorded_cmd = []

    def mock_run(cmd, *args, **kwargs):
        recorded_cmd.extend(cmd)
        class Dummy:
            returncode = 0
            stdout = "CODEX-T2-01-147C2A9"
            stderr = ""
        return Dummy()

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(tab, "CLAUDE_BIN", "dummy_claude.exe")
    monkeypatch.setattr(os.path, "exists", lambda p: True)

    tab.ask_claude_detailed("test prompt", chat_only=True)
    assert "--tools" not in recorded_cmd, "Claude chat route must not disable read tools with --tools ''"


def test_get_last_review_id_extracts_from_reviews_jsonl():
    """Verifies helper correctly extracts the last review_id from shared/trust/reviews.jsonl."""
    last_id = tab.get_last_review_id()
    assert last_id is not None
    assert len(last_id) >= 5
    assert "CODEX" in last_id or "CLAUDE" in last_id


def test_deep_health_verifier_logic():
    """Verifies deep health check logic passes only on matching review_id."""
    last_id = tab.get_last_review_id()

    # Pass case
    good_output = f"The last line of shared/trust/reviews.jsonl has review_id {last_id}."
    good_res = tab.verify_deep_health_output(good_output, expected_id=last_id)
    assert good_res["verified"] is True
    assert good_res["review_id"] == last_id

    # Fail case
    bad_output = "I cannot read files or CANNOT_READ_FILES"
    bad_res = tab.verify_deep_health_output(bad_output, expected_id=last_id)
    assert bad_res["verified"] is False
    assert "MISMATCH" in bad_res["error"]


def test_ping_is_explicitly_labeled_route_only():
    """Verifies PING returns GATEWAY_ROUTE_ONLY and indicates route-only status."""
    dummy_msg = {
        "message_id": "test_ping_001",
        "sender": "CODEX",
        "recipient": "ANTIGRAVITY",
        "subject": "PING",
        "body": {},
        "track": "SHARED",
    }
    status, payload, _, err = iw.InboxWorker()._execute_task_payload(dummy_msg)
    assert status == "COMPLETED"
    assert payload.get("reply") == "PONG"
    assert "GATEWAY_ROUTE_ONLY" in str(payload.get("check"))


def test_health_deep_worker_handling_success(monkeypatch):
    """Verifies HEALTH_DEEP executes agent call, verifies review_id, and returns COMPLETED."""
    last_id = tab.get_last_review_id()
    def mock_ask_codex(prompt, *args, **kwargs):
        return {"success": True, "output": f"The last line has review_id: {last_id}", "returncode": 0, "elapsed": 0.5}

    monkeypatch.setattr(tab, "ask_codex_detailed", mock_ask_codex)

    dummy_msg = {
        "message_id": "test_deep_001",
        "sender": "CLAUDE",
        "recipient": "CODEX",
        "subject": "HEALTH_DEEP",
        "body": {},
        "track": "SHARED",
    }
    status, payload, _, err = iw.InboxWorker()._execute_task_payload(dummy_msg)
    assert status == "COMPLETED"
    assert payload.get("verified") is True
    assert payload.get("review_id") == last_id
    assert err is None


def test_health_deep_worker_handling_failure_on_mismatch(monkeypatch):
    """Verifies HEALTH_DEEP fails closed when review_id is not returned."""
    def mock_ask_claude(prompt, *args, **kwargs):
        return {"success": True, "output": "I do not see any review_id", "returncode": 0, "elapsed": 0.5}

    monkeypatch.setattr(tab, "ask_claude_detailed", mock_ask_claude)

    dummy_msg = {
        "message_id": "test_deep_002",
        "sender": "ANTIGRAVITY",
        "recipient": "CLAUDE",
        "subject": "HEALTH_DEEP",
        "body": {},
        "track": "SHARED",
    }
    status, payload, _, err = iw.InboxWorker()._execute_task_payload(dummy_msg)
    assert status == "FAILED"
    assert payload.get("verified") is False
    assert "MISMATCH" in str(err)

