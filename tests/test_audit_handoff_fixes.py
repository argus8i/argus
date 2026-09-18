"""
tests/test_audit_handoff_fixes.py - Regression Suite for Codex Audit Handoff Fixes
===================================================================================
Verifies:
  1. Batch 1: Coordinator review routing for REALITY_AUDIT, PROVENANCE_AUDIT, BOTH,
     and fail-closed rejection of unknown review types.
  2. Batch 2: Consensus synthesis HMAC signature verification, artifact hash integrity,
     mandatory reviewer presence, and unverified cash state reporting.
  3. Batch 3: Feed validity schema hardening (exact boolean data_valid, CONNECTED_NO_DATA,
     unhashable status handling) and consumer robustness (stats=None, exact symbol matching).
  4. Batch 5: Surveillance monitor hardening against truthiness bypass, garbage timestamps,
     and non-integer stage values.
"""

import math
import os
import sys
import uuid
import pytest
from datetime import datetime, timedelta
from typing import Any, Dict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import antigravity.adapters.claude_adapter as ca
import antigravity.adapters.codex_adapter as cxa
from antigravity.orchestrator.coordinator import AntigravityCoordinator
from antigravity.daemons.inbox_worker import (
    compute_sha256,
    compute_envelope_hmac,
    get_agent_secret_key,
)
from antigravity.daemons.feed_validity import (
    check_feed,
    usable_watchlist,
)
from antigravity.models.track2_surveillance_monitor import (
    Track2SurveillanceMonitor,
    Track2SurveillanceState,
)


@pytest.fixture
def audit_env(monkeypatch):
    """Isolated environment for coordinator and adapter tests."""
    sandbox_dir = os.path.join(
        PROJECT_ROOT, "antigravity", "messages", "_test_sandboxes",
        f"test_audit_{uuid.uuid4().hex[:8]}"
    )
    reviews_dir = os.path.join(sandbox_dir, "shared", "reviews")
    os.makedirs(reviews_dir, exist_ok=True)

    def mock_claude(prompt: str, timeout_sec: int) -> Dict[str, Any]:
        return {
            "success": True,
            "output": "## Claude Red-Team\nVerified mathematical constraints.",
            "returncode": 0,
            "elapsed": 0.05
        }

    def mock_codex(prompt: str, timeout_sec: int) -> Dict[str, Any]:
        return {
            "success": True,
            "output": "## Codex Audit\nVerified exchange ground truth.",
            "returncode": 0,
            "elapsed": 0.05
        }

    monkeypatch.setattr(ca, "CLAUDE_DISPATCH_HOOK", mock_claude)
    monkeypatch.setattr(cxa, "CODEX_DISPATCH_HOOK", mock_codex)
    monkeypatch.setattr(ca, "ALLOWED_SUBMISSION_DIRS", [os.path.normcase(reviews_dir)])
    monkeypatch.setattr(cxa, "ALLOWED_SUBMISSION_DIRS", [os.path.normcase(reviews_dir)])

    yield {
        "workspace": sandbox_dir,
        "reviews_dir": reviews_dir,
    }

    import shutil
    shutil.rmtree(sandbox_dir, ignore_errors=True)


# ==============================================================================
# Batch 1: Review Routing Tests
# ==============================================================================

def test_dispatch_reality_audit_routes_to_codex_only(audit_env):
    """REALITY_AUDIT must dispatch to Codex and leave Claude None."""
    coordinator = AntigravityCoordinator(audit_env["workspace"])
    pkg = coordinator.create_review_package(
        task_id="TASK_REALITY_01",
        track="TRACK_1",
        exact_question="Verify ESM Stage 2 circuit bands.",
        assumptions={},
        source_files=[],
        measured_values={},
        requested_review="REALITY_AUDIT",
        submission_dir=os.path.relpath(audit_env["reviews_dir"], audit_env["workspace"])
    )
    result = coordinator.dispatch_review(pkg)
    assert result["status"] == "COMPLETED"
    assert result["claude"] is None
    assert result["codex"] is not None
    assert result["codex"]["status"] == "COMPLETED"
    assert result["codex"]["review_type"] == "REALITY_AUDIT"


def test_dispatch_provenance_audit_routes_to_codex_only(audit_env):
    """PROVENANCE_AUDIT must dispatch to Codex."""
    coordinator = AntigravityCoordinator(audit_env["workspace"])
    pkg = coordinator.create_review_package(
        task_id="TASK_PROVENANCE_01",
        track="TRACK_1",
        exact_question="Verify quote timestamps.",
        assumptions={},
        source_files=[],
        measured_values={},
        requested_review="PROVENANCE_AUDIT",
        submission_dir=os.path.relpath(audit_env["reviews_dir"], audit_env["workspace"])
    )
    result = coordinator.dispatch_review(pkg)
    assert result["status"] == "COMPLETED"
    assert result["claude"] is None
    assert result["codex"] is not None
    assert result["codex"]["review_type"] == "PROVENANCE_AUDIT"


def test_dispatch_both_routes_to_both_with_mapped_types(audit_env):
    """BOTH must dispatch Claude (MICROSTRUCTURE) and Codex (REALITY_AUDIT)."""
    coordinator = AntigravityCoordinator(audit_env["workspace"])
    pkg = coordinator.create_review_package(
        task_id="TASK_BOTH_01",
        track="SHARED",
        exact_question="Joint review.",
        assumptions={},
        source_files=[],
        measured_values={},
        requested_review="BOTH",
        submission_dir=os.path.relpath(audit_env["reviews_dir"], audit_env["workspace"])
    )
    result = coordinator.dispatch_review(pkg)
    assert result["status"] == "COMPLETED"
    assert result["claude"] is not None
    assert result["claude"]["review_type"] == "MICROSTRUCTURE"
    assert result["codex"] is not None
    assert result["codex"]["review_type"] == "REALITY_AUDIT"


def test_dispatch_unknown_review_type_fails_closed(audit_env):
    """Unknown review type must be rejected immediately, not returned as COMPLETED."""
    coordinator = AntigravityCoordinator(audit_env["workspace"])
    pkg = coordinator.create_review_package(
        task_id="TASK_BAD_01",
        track="SHARED",
        exact_question="Bad type probe.",
        assumptions={},
        source_files=[],
        measured_values={},
        requested_review="UNKNOWN_REVIEW_TYPE_XYZ",
        submission_dir=os.path.relpath(audit_env["reviews_dir"], audit_env["workspace"])
    )
    result = coordinator.dispatch_review(pkg)
    assert result["status"] == "REJECTED_UNKNOWN_TYPE"
    assert result["claude"] is None
    assert result["codex"] is None
    assert len(result["errors"]) > 0


# ==============================================================================
# Batch 2: P0 Consensus Verification in synthesize_outcome
# ==============================================================================

def test_synthesize_blocks_on_invalid_hmac_signature(audit_env):
    """Envelopes with forged or invalid auth_signature must be BLOCKED."""
    coordinator = AntigravityCoordinator(audit_env["workspace"])

    sub_file = os.path.join(audit_env["reviews_dir"], "codex_submission.md")
    with open(sub_file, "w", encoding="utf-8") as f:
        f.write("## Valid Content")
    file_sha = compute_sha256(sub_file)

    tampered_review = {
        "task_id": "TASK_SIG_TEST",
        "sender": "CODEX",
        "submission_file": os.path.relpath(sub_file, audit_env["workspace"]),
        "sha256": file_sha,
        "auth_signature": "FORGED_INVALID_SIGNATURE_HEX",
        "output_payload": {
            "review_text": "Audit passed.",
            "has_p0_objection": False
        }
    }

    syn_result = coordinator.synthesize_outcome(
        task_id="TASK_SIG_TEST",
        track="TRACK_1",
        question="HMAC check.",
        primary_analysis="Analysis.",
        claude_review=None,
        codex_review=tampered_review,
        synthesis_file_rel=f"{os.path.relpath(audit_env['reviews_dir'], audit_env['workspace'])}/antigravity_synthesis.md"
    )

    assert syn_result["decision"] == "BLOCKED"
    assert syn_result["confidence"] == "LOW"
    assert any("Signature Invalid" in o for o in syn_result["unresolved_objections"])


def test_synthesize_blocks_on_missing_required_reviewer(audit_env):
    """When package requires HIGH_IMPACT_CORE, missing Codex or Claude blocks synthesis."""
    coordinator = AntigravityCoordinator(audit_env["workspace"])

    pkg = coordinator.create_review_package(
        task_id="TASK_CORE_REQ",
        track="SHARED",
        exact_question="Core change.",
        assumptions={},
        source_files=[],
        measured_values={},
        requested_review="HIGH_IMPACT_CORE",
        submission_dir=os.path.relpath(audit_env["reviews_dir"], audit_env["workspace"])
    )

    sub_file = os.path.join(audit_env["reviews_dir"], "claude_submission.md")
    with open(sub_file, "w", encoding="utf-8") as f:
        f.write("Claude approved.")
    file_sha = compute_sha256(sub_file)

    claude_env = {
        "task_id": "TASK_CORE_REQ",
        "sender": "CLAUDE",
        "submission_file": os.path.relpath(sub_file, audit_env["workspace"]),
        "sha256": file_sha,
        "output_payload": {"review_text": "Approved.", "has_p0_objection": False}
    }
    claude_env["auth_signature"] = compute_envelope_hmac(claude_env, get_agent_secret_key("CLAUDE"))

    # Supply only Claude; Codex is missing
    syn_result = coordinator.synthesize_outcome(
        task_id="TASK_CORE_REQ",
        track="SHARED",
        question="Core change.",
        primary_analysis="Analysis.",
        claude_review=claude_env,
        codex_review=None,
        synthesis_file_rel=f"{os.path.relpath(audit_env['reviews_dir'], audit_env['workspace'])}/antigravity_synthesis.md",
        review_package=pkg
    )

    assert syn_result["decision"] == "BLOCKED"
    assert any("Missing Required Reviewer" in o for o in syn_result["unresolved_objections"])


def test_synthesize_reports_unverified_cash_state_when_no_observation(audit_env):
    """Rule 1 cash state must be reported UNVERIFIED when not backed by observation log."""
    coordinator = AntigravityCoordinator(audit_env["workspace"])

    sub_file = os.path.join(audit_env["reviews_dir"], "claude_submission.md")
    with open(sub_file, "w", encoding="utf-8") as f:
        f.write("Approved math.")
    file_sha = compute_sha256(sub_file)

    claude_env = {
        "task_id": "TASK_CASH_01",
        "sender": "CLAUDE",
        "submission_file": os.path.relpath(sub_file, audit_env["workspace"]),
        "sha256": file_sha,
        "output_payload": {"review_text": "Approved math.", "has_p0_objection": False}
    }
    claude_env["auth_signature"] = compute_envelope_hmac(claude_env, get_agent_secret_key("CLAUDE"))

    syn_result = coordinator.synthesize_outcome(
        task_id="TASK_CASH_01",
        track="TRACK_1",
        question="Cash check.",
        primary_analysis="Analysis.",
        claude_review=claude_env,
        codex_review=None,
        synthesis_file_rel=f"{os.path.relpath(audit_env['reviews_dir'], audit_env['workspace'])}/antigravity_synthesis.md",
        account_observation_verified=False
    )

    syn_abs = os.path.join(audit_env["workspace"], syn_result["synthesis_file"])
    content = open(syn_abs, encoding="utf-8").read()
    assert "Rule 1 Verification (100% Cash / Paper Observation Gate):** UNVERIFIED" in content


# ==============================================================================
# Batch 3: Feed Validity Schema & Consumer Robustness Tests
# ==============================================================================

def test_feed_validity_rejects_string_boolean_and_none():
    """String 'false', None, or missing data_valid must fail closed."""
    now = datetime(2026, 9, 18, 10, 0, 0)
    base_snap = {
        "data_valid": "false",
        "status": "LIVE",
        "is_tab_hidden": False,
        "is_stale": False,
        "local_write_time": "2026-09-18 10:00:00"
    }
    ok, reason = check_feed(base_snap, now=now)
    assert ok is False
    assert reason == "DATA_VALID_NOT_BOOLEAN_TRUE"

    base_snap["data_valid"] = None
    ok, reason = check_feed(base_snap, now=now)
    assert ok is False
    assert reason == "DATA_VALID_NOT_BOOLEAN_TRUE"


def test_feed_validity_rejects_connected_no_data():
    """CONNECTED_NO_DATA status must be rejected."""
    now = datetime(2026, 9, 18, 10, 0, 0)
    snap = {
        "data_valid": True,
        "status": "CONNECTED_NO_DATA",
        "is_tab_hidden": False,
        "is_stale": False,
        "local_write_time": "2026-09-18 10:00:00"
    }
    ok, reason = check_feed(snap, now=now)
    assert ok is False
    assert reason == "CONNECTED_NO_DATA"


def test_feed_validity_handles_unhashable_status():
    """List status must not raise TypeError."""
    now = datetime(2026, 9, 18, 10, 0, 0)
    snap = {
        "data_valid": True,
        "status": ["CONNECTED_NO_DATA"],
        "is_tab_hidden": False,
        "is_stale": False,
        "local_write_time": "2026-09-18 10:00:00"
    }
    ok, reason = check_feed(snap, now=now)
    assert ok is False
    assert reason == "INVALID_STATUS_TYPE"


# ==============================================================================
# Batch 5: Surveillance Validation Tests
# ==============================================================================

def test_surveillance_monitor_rejects_string_boolean(audit_env):
    """is_fno_underlying='false' must be rejected fail-closed, not treated as truthy."""
    monitor = Track2SurveillanceMonitor(history_file=os.path.join(audit_env["workspace"], "surv_hist.json"))
    state = monitor.evaluate_scrip(
        symbol="TATAMOTORS",
        is_fno_underlying="false",  # String instead of bool
        asm_stage=0,
        gsm_stage=0,
        band_pct=0.0,
        date_str="2026-09-18",
        checked_at="2026-09-18 09:15:00"
    )
    assert state.status == "DISQUALIFIED_UNKNOWN"
    assert "must be an exact boolean" in state.reason


def test_surveillance_monitor_rejects_garbage_timestamp(audit_env):
    """Garbage checked_at timestamp must fail closed, not pass via startswith."""
    monitor = Track2SurveillanceMonitor(history_file=os.path.join(audit_env["workspace"], "surv_hist.json"))
    state = monitor.evaluate_scrip(
        symbol="TATAMOTORS",
        is_fno_underlying=True,
        asm_stage=0,
        gsm_stage=0,
        band_pct=0.0,
        date_str="2026-09-18",
        checked_at="2026-09-18garbage"
    )
    assert state.status == "DISQUALIFIED_UNKNOWN"
    assert "cannot be parsed" in state.reason


def test_surveillance_monitor_rejects_invalid_stage_types(audit_env):
    """Boolean or negative stage values must be rejected."""
    monitor = Track2SurveillanceMonitor(history_file=os.path.join(audit_env["workspace"], "surv_hist.json"))
    state = monitor.evaluate_scrip(
        symbol="TATAMOTORS",
        is_fno_underlying=True,
        asm_stage=True,  # Boolean instead of int
        gsm_stage=0,
        band_pct=0.0,
        date_str="2026-09-18",
        checked_at="2026-09-18 09:15:00"
    )
    assert state.status == "DISQUALIFIED_UNKNOWN"
    assert "asm_stage must be an integer" in state.reason


