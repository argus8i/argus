r"""
claude_adapter.py - Claude Quantitative Red-Team Review Adapter
==============================================================
Part of Project Swing Trades Hub-and-Spoke Architecture.
Antigravity is the primary orchestrator; Claude acts as a secondary quantitative red-team.

Authority Boundaries:
  1. Claude is strictly read-only regarding codebase models and trading rules.
  2. Claude is restricted to writing ONLY to its assigned claude_submission.md file.
  3. Claude cannot edit Codex's submissions or Antigravity's canonical models.
  4. Claude cryptographically signs all review findings using its external secret key.
"""

import os
import sys
import time
import json
import uuid
import re
from typing import Any, Dict, Optional, Tuple, Callable

# Ensure workspace root is in sys.path
WORKSPACE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if WORKSPACE_DIR not in sys.path:
    sys.path.insert(0, WORKSPACE_DIR)

from antigravity.daemons.inbox_worker import (
    compute_sha256,
    write_json_atomic,
    compute_envelope_hmac,
    get_agent_secret_key,
    get_current_ist,
    validate_path_security,
    ALLOWED_SUBMISSION_DIRS,
)
from antigravity.daemons.tri_agent_bus import (
    CLAUDE_BIN,
    ask_claude_detailed,
    log_interaction,
)

# Pluggable dispatch hook for deterministic unit testing
CLAUDE_DISPATCH_HOOK: Optional[Callable[[str, int], Dict[str, Any]]] = None

ALLOWED_CLAUDE_REVIEW_TYPES = {
    "MATHEMATICS",
    "STATISTICS",
    "MICROSTRUCTURE",
    "ADVERSE_SELECTION",
    "FAILURE_MODES",
    "REBUTTAL",
    "HIGH_IMPACT_CORE"
}

# Claude owns exactly these two artifacts. Rebuttals are a separate file:
# writing a rebuttal over the submission would destroy the original review.
ALLOWED_CLAUDE_ARTIFACTS = {
    "claude_submission.md",
    "claude_rebuttal.md",
}


# A dispatch hook means the "reviewer" is a stub, not a reviewer. Writing stub
# text to the canonical review directory is how claude_submission.md,
# codex_submission.md and antigravity_synthesis.md came to contain fabricated
# APPROVED reviews (now quarantined in _SIMULATED/). status.py --demo still
# targeted shared/reviews, so prose in a README is not enough: refuse the write.
CANONICAL_REVIEW_DIRS = (
    os.path.normcase(os.path.abspath(os.path.join(WORKSPACE_DIR, "shared", "reviews"))),
    os.path.normcase(os.path.abspath(os.path.join(WORKSPACE_DIR, "shared", "track1_esm", "reviews"))),
    os.path.normcase(os.path.abspath(os.path.join(WORKSPACE_DIR, "shared", "track2_liquid", "reviews"))),
)


def assert_not_simulating_into_canonical(abs_submission_path: str, hook) -> None:
    """Raise if a stub dispatch hook would write to a canonical review path."""
    if hook is None:
        return
    target_dir = os.path.normcase(os.path.abspath(os.path.dirname(abs_submission_path)))
    if target_dir in CANONICAL_REVIEW_DIRS:
        raise RuntimeError(
            "SIMULATION_TO_CANONICAL_PATH: a dispatch hook is installed, so this "
            "output is simulated, and it was about to be written to the canonical "
            f"review directory {target_dir}. Simulated reviews must go to a "
            "temporary directory. Real reviews come from the reviewer CLI with no "
            "hook installed."
        )


# Standing brief for every dispatched Claude review. The reviewer is a fresh
# Claude Code process with no memory of prior sessions, so the role has to be
# restated on every call or it does not exist.
CLAUDE_STANDING_BRIEF = """You are Claude Code, Lead Quantitative Red-Team Analyst,
Microstructure Specialist and Adversary for Project Swing Trades, working under
AGENTS.md alongside Antigravity (primary orchestrator) and OpenAI Codex
(regulatory auditor).

YOUR MANDATE: ATTACK THE PLAN.
Disagreement is the deliverable. An unchallenged trade idea does not get traded.
You are not here to be agreeable. Find the mathematical flaws, microstructure
traps, liquidity illusions and adverse-selection edge cases before any rupee is
risked.

AUTHORITY BOUNDARY (enforced by the adapter, stated here so you know it):
You are read-only. You write nothing; the adapter records your output as
claude_submission.md. Never attempt to modify source, place trades, or touch
another agent's files.

STANDING STRESS TESTS:
1. Rule 9 - Participation & adverse selection. Position must never exceed 15%
   of daily volume. Sizing UP when fills are scarce is backwards: scarce fills
   are the regime with the worst counterparty composition. Prove whether a fill
   is an adverse-selection trap, i.e. buying the exit from an operator.
2. Rule 5 - 10-day lower-circuit lockout.
   Max Position = (Rupees Willing To Lose) / 0.401, calibrated to ten
   consecutive 5% LCs, since 1 - 0.95^10 = 0.4013. Check the band actually
   applies: at a 2% band the divisor is 1 - 0.98^10 = 0.1829, and the formula
   also assumes an exit is possible on day 11. Never assume a stop executes
   when bid depth is zero.
3. Rule 4 & queue physics - discrete 4-state execution. Reject any assumption
   of continuous fills or guaranteed liquidity. Model the actual book state:
   LOCKED_NO_BID, QUEUED, PARTIAL, FILLED.
4. Rule 11 - absolute track isolation. Track 1 (ESM micro-caps < INR 500 Cr)
   and Track 2 (liquid F&O momentum) must never share assumptions, sizing
   models or execution rules.
5. Cross-examination. When handed a Codex finding, cross-examine it
   mathematically. If Codex argues a regulation makes a trade safe, demand
   proof of how the order book behaves under stress.

REPORTING:
Give concrete formulas, exact numerical proofs and explicit trade mechanics.
No conversational filler.

On uncertainty: do not hedge vaguely, and do not manufacture confidence. If a
claim cannot be checked without tick data, order-book snapshots or a live
session, say so explicitly and label it UNVERIFIABLE (requires X). An
unverifiable claim is a finding, not a gap in your review: it means the trade
is unvalidated. Stating this is required, not optional.

Declare P0 / CRITICAL OBJECTION for any flaw that threatens capital; this
blocks the trade in Antigravity's consensus engine.

End every review with exactly one verdict line:
APPROVED | CONDITIONALLY_APPROVED | BLOCKED (P0: <reason>)
"""


def build_claude_prompt(package: Dict[str, Any]) -> str:
    """Formats a structured quantitative red-team review prompt for Claude."""
    task_id = package.get("task_id", "UNKNOWN")
    question = package.get("exact_question", "")
    assumptions = package.get("assumptions", {})
    metrics = package.get("measured_values", {})
    source_files = package.get("source_files", [])
    review_type = package.get("review_type", "MATHEMATICS")
    instructions = package.get("instructions", "")

    prompt = f"""{CLAUDE_STANDING_BRIEF}

[ANTIGRAVITY REVIEW MANDATE FOR CLAUDE CODE]
Task ID: {task_id}
Review Type: {review_type} (Quantitative Red-Team)
Target: Project Swing Trades (AGENTS.md Rules 1-11 strictly apply)

MANDATE / QUESTION:
{question}

ASSUMPTIONS PRESENTED BY ANTIGRAVITY:
{json.dumps(assumptions, indent=2)}

MEASURED & DERIVED QUANTITATIVE VALUES:
{json.dumps(metrics, indent=2)}

RELEVANT SOURCE FILES:
{', '.join(source_files) if source_files else 'None'}

INSTRUCTIONS & ADVERSARIAL CRITERIA:
{instructions or 'Provide rigorous mathematical, statistical, and market microstructure criticism. Identify adverse-selection risks, edge cases, and failure modes. State all unresolved P0 objections explicitly.'}

You are acting as the independent quantitative red-team. Provide your rigorous review. Your output will be recorded as claude_submission.md.
"""
    return prompt.strip()


class ClaudeReviewAdapter:
    """Adapter executing and authenticating Claude red-team reviews under Antigravity orchestration."""

    def __init__(self, workspace_dir: Optional[str] = None):
        self.workspace_dir = workspace_dir or WORKSPACE_DIR

    def execute_review(
        self,
        package: Dict[str, Any],
        timeout_sec: int = 180
    ) -> Tuple[bool, Dict[str, Any], Optional[str]]:
        """
        Executes Claude quantitative red-team review:
        1. Validates review package and target submission path.
        2. Dispatches prompt to Claude Code CLI (or hook).
        3. Writes output strictly to designated claude_submission.md.
        4. Cryptographically signs the response envelope using Claude's secret key.
        Returns: (success, response_envelope, error_message)
        """
        task_id = package.get("task_id", f"task_{uuid.uuid4().hex[:8]}")
        corr_id = package.get("correlation_id", f"corr_{uuid.uuid4().hex[:8]}")
        track = package.get("track", "SHARED")
        review_type = package.get("review_type", "MATHEMATICS").upper()

        if review_type not in ALLOWED_CLAUDE_REVIEW_TYPES:
            return False, {}, f"INVALID_REVIEW_TYPE: '{review_type}' not in {ALLOWED_CLAUDE_REVIEW_TYPES}"

        submission_rel = package.get("submission_file", "shared/reviews/claude_submission.md")
        
        # Enforce Authority Boundary: Claude may write ONLY to its own two
        # artifacts. A rebuttal goes to a separate file so that answering a
        # cross-examination can never destroy the original submission.
        norm_sub = os.path.normpath(submission_rel).replace("\\", "/")
        if os.path.basename(norm_sub) not in ALLOWED_CLAUDE_ARTIFACTS:
            return False, {}, (
                f"AUTHORITY_VIOLATION: Claude is restricted to "
                f"{sorted(ALLOWED_CLAUDE_ARTIFACTS)}, got '{submission_rel}'"
            )

        # Validate path security and track isolation
        valid_p, abs_sub, err = validate_path_security(submission_rel, track, self.workspace_dir)
        if not valid_p or not abs_sub:
            return False, {}, err or "INVALID_PATH"

        # Refuse to write simulated output into a canonical review path.
        assert_not_simulating_into_canonical(abs_sub, CLAUDE_DISPATCH_HOOK)

        # Ensure submission path is within an allowed reviews directory
        norm_abs = os.path.normcase(abs_sub)
        allowed_dirs = list(ALLOWED_SUBMISSION_DIRS)
        if self.workspace_dir != WORKSPACE_DIR:
            allowed_dirs.append(os.path.normcase(os.path.abspath(os.path.join(self.workspace_dir, "shared", "reviews"))))
        in_allowed = any(norm_abs == d or norm_abs.startswith(d + os.path.sep) for d in allowed_dirs)
        if not in_allowed:
            return False, {}, f"DIRECTORY_SECURITY_VIOLATION: '{submission_rel}' outside allowed reviews directories."

        prompt = build_claude_prompt(package)

        # Dispatch execution
        t0 = time.time()
        if CLAUDE_DISPATCH_HOOK:
            res = CLAUDE_DISPATCH_HOOK(prompt, timeout_sec)
        else:
            res = ask_claude_detailed(prompt, timeout_sec)
        elapsed = time.time() - t0

        if not res.get("success"):
            return False, {}, res.get("error") or "Claude execution failed."

        output_text = res.get("output", "").strip()

        # Atomic write to claude_submission.md
        os.makedirs(os.path.dirname(abs_sub), exist_ok=True)
        tmp_write = abs_sub + f".tmp_{uuid.uuid4().hex[:8]}"
        with open(tmp_write, "w", encoding="utf-8") as f:
            f.write(output_text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_write, abs_sub)

        file_hash = compute_sha256(abs_sub)

        # Build authenticated envelope from CLAUDE to ANTIGRAVITY
        claude_key = get_agent_secret_key("CLAUDE")
        if not claude_key:
            return False, {}, "AUTH_CONFIG_ERROR: Missing external cryptographic key for CLAUDE."

        response_envelope = {
            "message_id": f"resp_claude_{uuid.uuid4().hex[:12]}",
            "correlation_id": corr_id,
            "task_id": task_id,
            "sender": "CLAUDE",
            "recipient": "ANTIGRAVITY",
            "review_type": review_type,
            "track": track,
            "status": "COMPLETED",
            "created_at_ist": get_current_ist(),
            "completed_at_ist": get_current_ist(),
            "submission_file": submission_rel,
            "artifact_hashes": {submission_rel: file_hash},
            "output_payload": {
                "summary": output_text[:300] + "..." if len(output_text) > 300 else output_text,
                "review_text": output_text,
                "elapsed_sec": round(elapsed, 2),
                "has_p0_objection": bool(re.search(r"\b(p0\s*(?:objection|risk|blocker|violation)?|critical\s+(?:objection|risk|blocker|violation|flaw))\b", output_text, re.IGNORECASE) and "no p0" not in output_text.lower() and "no critical" not in output_text.lower())
            },
            "nonce": uuid.uuid4().hex,
            "error": None
        }

        # Cryptographically sign response envelope
        response_envelope["auth_signature"] = compute_envelope_hmac(response_envelope, claude_key)

        return True, response_envelope, None
