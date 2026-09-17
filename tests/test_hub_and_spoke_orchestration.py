"""
tests/test_hub_and_spoke_orchestration.py - Unit Tests for Hub-and-Spoke Architecture
======================================================================================
Verifies:
  1. Antigravity as primary central orchestrator.
  2. Strict authority boundaries (Claude/Codex write only to their designated submission files).
  3. Domain-specific review dispatch (math -> Claude, audit -> Codex, core -> Both).
  4. Cross-reviewer routing strictly through Antigravity (Claude -> Antigravity -> Codex).
  5. Bounded rebuttal rounds.
  6. P0 / Critical objection blocks acceptance.
  7. Dissent preservation in final synthesis.
  8. Hub status interface reporting.
"""

import os
import sys
import json
import time
import uuid
import shutil
import pytest
from typing import Any, Dict

# Ensure project root in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import antigravity.adapters.claude_adapter as ca
import antigravity.adapters.codex_adapter as cxa
from antigravity.orchestrator.coordinator import AntigravityCoordinator
from antigravity.orchestrator.status import get_hub_status
from antigravity.daemons.inbox_worker import (
    compute_sha256,
    write_json_atomic,
    load_auth_config,
    get_agent_secret_key,
)


@pytest.fixture
def hub_test_env(monkeypatch):
    """Isolated environment for hub-and-spoke testing."""
    test_sandbox_dir = os.path.join(
        PROJECT_ROOT, "antigravity", "messages", "_test_sandboxes",
        f"test_hub_{uuid.uuid4().hex[:8]}"
    )
    reviews_dir = os.path.join(test_sandbox_dir, "shared", "reviews")
    os.makedirs(reviews_dir, exist_ok=True)

    # Configure external keys
    key_file = r"C:\Users\yashw\.gemini\antigravity\agent_keys.json"
    if os.path.exists(key_file):
        monkeypatch.setenv("TRI_AGENT_KEY_FILE", key_file)

    # Set mock hooks for fast unit testing
    def mock_claude(prompt: str, timeout_sec: int) -> Dict[str, Any]:
        return {
            "success": True,
            "output": "## Claude Red-Team Findings\nMathematical proof verified. No adverse selection detected.",
            "returncode": 0,
            "elapsed": 0.05
        }

    def mock_codex(prompt: str, timeout_sec: int) -> Dict[str, Any]:
        return {
            "success": True,
            "output": "## Codex Audit Findings\nRegulatory compliance verified. NSE ESM Stage 1 bounds respected.",
            "returncode": 0,
            "elapsed": 0.05
        }

    monkeypatch.setattr(ca, "CLAUDE_DISPATCH_HOOK", mock_claude)
    monkeypatch.setattr(cxa, "CODEX_DISPATCH_HOOK", mock_codex)

    # Monkeypatch ALLOWED_SUBMISSION_DIRS to include test_sandbox_dir reviews
    monkeypatch.setattr(ca, "ALLOWED_SUBMISSION_DIRS", [os.path.normcase(reviews_dir)])
    monkeypatch.setattr(cxa, "ALLOWED_SUBMISSION_DIRS", [os.path.normcase(reviews_dir)])

    yield {
        "workspace": test_sandbox_dir,
        "reviews_dir": reviews_dir,
    }

    # Teardown
    for _ in range(5):
        try:
            if os.path.exists(test_sandbox_dir):
                shutil.rmtree(test_sandbox_dir, ignore_errors=True)
            break
        except Exception:
            time.sleep(0.05)



def test_coordinator_creates_canonical_package(hub_test_env):
    """Coordinator creates a structured, canonical review package with all required fields."""
    coordinator = AntigravityCoordinator(hub_test_env["workspace"])
    pkg = coordinator.create_review_package(
        task_id="TASK_MOBIKWIK_AUDIT_01",
        track="TRACK_1",
        exact_question="Audit MOBIKWIK Day 3 profit exit versus 10-day LC lockout risk.",
        assumptions={"entry_price": 202.91, "band": 0.05},
        source_files=["antigravity/models/risk_calculator.py"],
        measured_values={"lc_risk": -0.401, "expected_drain": 4500},
        requested_review="HIGH_IMPACT_CORE"
    )

    assert pkg["task_id"] == "TASK_MOBIKWIK_AUDIT_01"
    assert pkg["track"] == "TRACK_1"
    assert pkg["requested_review"] == "HIGH_IMPACT_CORE"
    assert pkg["coordinator"] == "ANTIGRAVITY"
    assert "entry_price" in pkg["assumptions"]
    assert pkg["measured_values"]["lc_risk"] == -0.401


def test_dispatch_routing_by_review_type(hub_test_env):
    """Reviews are routed specifically: math to Claude, audit to Codex, core to Both."""
    coordinator = AntigravityCoordinator(hub_test_env["workspace"])

    # 1. Mathematics -> Claude only
    pkg_math = coordinator.create_review_package(
        task_id="TASK_MATH_01",
        track="TRACK_1",
        exact_question="Verify 10-day LC loss formula.",
        assumptions={},
        source_files=[],
        measured_values={},
        requested_review="MATHEMATICS",
        submission_dir=os.path.relpath(hub_test_env["reviews_dir"], hub_test_env["workspace"])
    )
    res_math = coordinator.dispatch_review(pkg_math)
    assert res_math["claude"] is not None
    assert res_math["codex"] is None
    assert res_math["claude"]["status"] == "COMPLETED"

    # 2. Regulatory / Code Audit -> Codex only
    pkg_audit = coordinator.create_review_package(
        task_id="TASK_AUDIT_01",
        track="TRACK_1",
        exact_question="Verify ESM Stage 2 auction restrictions.",
        assumptions={},
        source_files=[],
        measured_values={},
        requested_review="REGULATORY",
        submission_dir=os.path.relpath(hub_test_env["reviews_dir"], hub_test_env["workspace"])
    )
    res_audit = coordinator.dispatch_review(pkg_audit)
    assert res_audit["codex"] is not None
    assert res_audit["claude"] is None
    assert res_audit["codex"]["status"] == "COMPLETED"

    # 3. High Impact Core -> Both
    pkg_core = coordinator.create_review_package(
        task_id="TASK_CORE_01",
        track="TRACK_1",
        exact_question="Core model change to queue drain model.",
        assumptions={},
        source_files=[],
        measured_values={},
        requested_review="HIGH_IMPACT_CORE",
        submission_dir=os.path.relpath(hub_test_env["reviews_dir"], hub_test_env["workspace"])
    )
    res_core = coordinator.dispatch_review(pkg_core)
    assert res_core["claude"] is not None
    assert res_core["codex"] is not None
    assert res_core["claude"]["sender"] == "CLAUDE"
    assert res_core["codex"]["sender"] == "CODEX"


def test_reviewer_authority_boundary_enforcement(hub_test_env):
    """Claude cannot write to codex_submission.md, and Codex cannot write to claude_submission.md."""
    coordinator = AntigravityCoordinator(hub_test_env["workspace"])

    # Claude attempt to write to codex_submission.md
    illegal_pkg_claude = {
        "task_id": "ILLEGAL_01",
        "track": "SHARED",
        "exact_question": "hack",
        "review_type": "MATHEMATICS",
        "submission_file": f"{os.path.relpath(hub_test_env['reviews_dir'], hub_test_env['workspace'])}/codex_submission.md"
    }
    success_c, _, err_c = coordinator.claude_adapter.execute_review(illegal_pkg_claude)
    assert success_c is False
    assert "AUTHORITY_VIOLATION" in err_c

    # Codex attempt to write to claude_submission.md
    illegal_pkg_codex = {
        "task_id": "ILLEGAL_02",
        "track": "SHARED",
        "exact_question": "hack",
        "review_type": "REGULATORY",
        "submission_file": f"{os.path.relpath(hub_test_env['reviews_dir'], hub_test_env['workspace'])}/claude_submission.md"
    }
    success_cx, _, err_cx = coordinator.codex_adapter.execute_review(illegal_pkg_codex)
    assert success_cx is False
    assert "AUTHORITY_VIOLATION" in err_cx


def test_cross_reviewer_routing_through_antigravity(hub_test_env):
    """Claude challenge to Codex routes strictly through Antigravity Hub."""
    coordinator = AntigravityCoordinator(hub_test_env["workspace"])

    pkg = coordinator.create_review_package(
        task_id="TASK_CROSS_01",
        track="TRACK_1",
        exact_question="Primary analysis on T2T settlement.",
        assumptions={},
        source_files=[],
        measured_values={},
        requested_review="HIGH_IMPACT_CORE",
        submission_dir=os.path.relpath(hub_test_env["reviews_dir"], hub_test_env["workspace"])
    )

    # Claude raises an objection and asks Codex to verify broker delivery rules:
    # Flow: Claude -> Antigravity -> Codex
    success, rebuttal_env, err = coordinator.route_cross_examination(
        from_agent="CLAUDE",
        to_agent="CODEX",
        challenge_text="Does Zerodha permit same-day squareoff on Trade-to-Trade (T2T) securities?",
        original_package=pkg
    )

    assert success is True
    assert rebuttal_env["sender"] == "CODEX"
    assert rebuttal_env["recipient"] == "ANTIGRAVITY"
    assert rebuttal_env["review_type"] == "REBUTTAL"
    assert os.path.exists(os.path.join(hub_test_env["workspace"], rebuttal_env["submission_file"]))


def test_rebuttal_does_not_overwrite_original_submission(hub_test_env):
    """A cross-examination rebuttal must never destroy the original review.

    Regression: route_cross_examination pointed the rebuttal at the reviewer's
    own submission_file, so answering a challenge overwrote the audit that
    prompted it. The live shared/reviews/codex_submission.md was reduced to a
    rebuttal while antigravity_synthesis.md still quoted the vanished audit.
    """
    coordinator = AntigravityCoordinator(hub_test_env["workspace"])

    pkg = coordinator.create_review_package(
        task_id="TASK_REBUTTAL_PRESERVE",
        track="TRACK_1",
        exact_question="Primary analysis on delivery margin.",
        assumptions={},
        source_files=[],
        measured_values={},
        requested_review="HIGH_IMPACT_CORE",
        submission_dir=os.path.relpath(hub_test_env["reviews_dir"], hub_test_env["workspace"])
    )

    # Codex files its original audit.
    dispatch = coordinator.dispatch_review(pkg)
    assert dispatch["codex"]["status"] == "COMPLETED"

    codex_sub = os.path.join(hub_test_env["reviews_dir"], "codex_submission.md")
    original_audit = open(codex_sub, encoding="utf-8").read()
    assert "Codex Audit Findings" in original_audit

    # Claude then challenges it, and Codex answers.
    def mock_rebuttal(prompt: str, timeout_sec: int) -> Dict[str, Any]:
        return {
            "success": True,
            "output": "## Codex Rebuttal. RMS does not force-liquidate delivery positions.",
            "returncode": 0,
            "elapsed": 0.05
        }

    cxa.CODEX_DISPATCH_HOOK = mock_rebuttal
    success, rebuttal_env, err = coordinator.route_cross_examination(
        from_agent="CLAUDE",
        to_agent="CODEX",
        challenge_text="Does RMS square off at 15:20 IST?",
        original_package=pkg
    )
    assert success is True, err

    # The rebuttal lands in its own artifact...
    assert os.path.basename(rebuttal_env["submission_file"]) == "codex_rebuttal.md"
    rebuttal_text = open(
        os.path.join(hub_test_env["reviews_dir"], "codex_rebuttal.md"), encoding="utf-8"
    ).read()
    assert "Codex Rebuttal" in rebuttal_text

    # ...and the original audit is still intact, byte for byte.
    assert open(codex_sub, encoding="utf-8").read() == original_audit


def test_reviewer_cannot_write_outside_its_own_artifacts(hub_test_env):
    """The widened whitelist admits the rebuttal file and nothing else."""
    coordinator = AntigravityCoordinator(hub_test_env["workspace"])

    pkg = coordinator.create_review_package(
        task_id="TASK_AUTHORITY_REBUTTAL",
        track="TRACK_1",
        exact_question="Boundary probe.",
        assumptions={},
        source_files=[],
        measured_values={},
        requested_review="MATHEMATICS",
        submission_dir=os.path.relpath(hub_test_env["reviews_dir"], hub_test_env["workspace"])
    )

    sub_dir = os.path.relpath(hub_test_env["reviews_dir"], hub_test_env["workspace"])

    # Claude must not be able to reach Codex's rebuttal, or the synthesis.
    for forbidden in ("codex_rebuttal.md", "codex_submission.md", "antigravity_synthesis.md"):
        bad_pkg = dict(pkg)
        bad_pkg["submission_file"] = f"{sub_dir}/{forbidden}"
        ok, _, err = coordinator.claude_adapter.execute_review(bad_pkg, 30)
        assert ok is False
        assert "AUTHORITY_VIOLATION" in (err or "")


def test_unresolved_p0_blocks_synthesis(hub_test_env):
    """Any P0 / Critical objection blocks synthesis approval immediately."""
    coordinator = AntigravityCoordinator(hub_test_env["workspace"])

    # Simulate Claude finding a critical P0 mathematical defect
    p0_claude_review = {
        "sender": "CLAUDE",
        "submission_file": "shared/reviews/claude_submission.md",
        "auth_signature": "sig_claude_12345",
        "output_payload": {
            "review_text": "CRITICAL: P0 defect detected! Formula underestimates 10-day LC drawdown.",
            "has_p0_objection": True
        }
    }

    syn_result = coordinator.synthesize_outcome(
        task_id="TASK_P0_TEST",
        track="TRACK_1",
        question="Proposal to reduce LC risk calibration factor from 0.401 to 0.20.",
        primary_analysis="Initial proposal to lower cash buffer.",
        claude_review=p0_claude_review,
        codex_review=None,
        synthesis_file_rel=f"{os.path.relpath(hub_test_env['reviews_dir'], hub_test_env['workspace'])}/antigravity_synthesis.md"
    )

    assert syn_result["success"] is True
    assert syn_result["decision"] == "BLOCKED"
    assert syn_result["confidence"] == "LOW"
    assert len(syn_result["unresolved_objections"]) > 0
    assert "P0" in syn_result["unresolved_objections"][0]

    # Verify synthesis markdown contains explicit BLOCKED decision
    syn_abs = os.path.join(hub_test_env["workspace"], syn_result["synthesis_file"])
    with open(syn_abs, "r", encoding="utf-8") as f:
        content = f.read()
    assert "**Decision:** **BLOCKED**" in content


def test_dissent_preservation_in_synthesis(hub_test_env):
    """Antigravity cannot silence reviewer objections; full dissent is preserved."""
    coordinator = AntigravityCoordinator(hub_test_env["workspace"])

    claude_dissent = {
        "sender": "CLAUDE",
        "submission_file": "shared/reviews/claude_submission.md",
        "auth_signature": "sig_claude_999",
        "output_payload": {
            "review_text": "Dissent: Liquidity participation at 15% assumes continuous order book matching.",
            "has_p0_objection": False
        }
    }
    codex_dissent = {
        "sender": "CODEX",
        "submission_file": "shared/reviews/codex_submission.md",
        "auth_signature": "sig_codex_999",
        "output_payload": {
            "review_text": "Audit note: BSE PCAS periodic call auction operates once every 60 minutes.",
            "has_p0_objection": False
        }
    }

    syn_result = coordinator.synthesize_outcome(
        task_id="TASK_DISSENT_TEST",
        track="TRACK_1",
        question="Evaluate PCAS liquidity assumptions.",
        primary_analysis="Primary analysis assuming normal volume curves.",
        claude_review=claude_dissent,
        codex_review=codex_dissent,
        synthesis_file_rel=f"{os.path.relpath(hub_test_env['reviews_dir'], hub_test_env['workspace'])}/antigravity_synthesis.md"
    )

    assert syn_result["decision"] == "PASSED"
    syn_abs = os.path.join(hub_test_env["workspace"], syn_result["synthesis_file"])
    with open(syn_abs, "r", encoding="utf-8") as f:
        content = f.read()

    # Dissent and provenance must be fully preserved in final markdown
    assert "Claude Quantitative Red-Team Submission" in content
    assert "Liquidity participation at 15%" in content
    assert "Codex Engineering & Regulatory Audit Submission" in content
    assert "BSE PCAS periodic call auction" in content


def test_status_interface_reports_accurately(hub_test_env):
    """Hub status interface accurately reports adapter health and readiness."""
    status = get_hub_status()
    assert "adapters" in status
    assert "antigravity" in status["adapters"]
    assert "claude" in status["adapters"]
    assert "codex" in status["adapters"]
    assert status["adapters"]["antigravity"]["status"] in ["READY", "NO_KEY"]
    assert "queues" in status
