r"""
codex_adapter.py - OpenAI Codex / ChatGPT Regulatory & Code Audit Adapter
========================================================================
Part of Project Swing Trades Hub-and-Spoke Architecture.
Antigravity is the primary orchestrator; Codex acts as a secondary engineering & regulatory auditor.

Authority Boundaries:
  1. Codex is strictly read-only regarding codebase models and trading rules.
  2. Codex is restricted to writing ONLY to its assigned codex_submission.md file.
  3. Codex cannot edit Claude's submissions or Antigravity's canonical models.
  4. Codex cryptographically signs all review findings using its external secret key.
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
    CODEX_BIN,
    ask_codex_detailed,
    log_interaction,
)

# Pluggable dispatch hook for deterministic unit testing
CODEX_DISPATCH_HOOK: Optional[Callable[[str, int], Dict[str, Any]]] = None

ALLOWED_CODEX_REVIEW_TYPES = {
    "REGULATORY",
    "CODE_AUDIT",
    "BROKER_RULES",
    "SURVEILLANCE",
    "ENGINEERING",
    "REBUTTAL",
    "HIGH_IMPACT_CORE",
    "REALITY_AUDIT",
    "PROVENANCE_AUDIT",
}

# Codex owns exactly these two artifacts. Rebuttals are a separate file:
# writing a rebuttal over the submission would destroy the original review.
ALLOWED_CODEX_ARTIFACTS = {
    "codex_submission.md",
    "codex_rebuttal.md",
}


def build_codex_prompt(package: Dict[str, Any]) -> str:
    """Formats a structured reality/provenance audit prompt for Codex."""
    task_id = package.get("task_id", "UNKNOWN")
    question = package.get("exact_question", "")
    assumptions = package.get("assumptions", {})
    metrics = package.get("measured_values", {})
    source_files = package.get("source_files", [])
    review_type = package.get("review_type", "REGULATORY")
    instructions = package.get("instructions", "")

    prompt = f"""[ANTIGRAVITY REVIEW MANDATE FOR OPENAI CODEX / CHATGPT]
Task ID: {task_id}
Review Type: {review_type} (Reality & Provenance Audit)
Target: Project Swing Trades (AGENTS.md Rules 1-11 strictly apply)

ROLE:
You are the Reality and Provenance Auditor. You do NOT evaluate quantitative
theory, model design, or strategy edge — that is Claude's domain, and your
mandate is to remain asymmetric to Claude's review. Your sole domain is
ground truth: does this claim, value, or assumption match what NSE, BSE, or
Zerodha actually say and do, and can its lineage be traced to a real source.

MANDATE / QUESTION:
{question}

ASSUMPTIONS PRESENTED BY ANTIGRAVITY:
{json.dumps(assumptions, indent=2)}

MEASURED & DERIVED VALUES:
{json.dumps(metrics, indent=2)}

RELEVANT SOURCE FILES:
{', '.join(source_files) if source_files else 'None'}

AUDIT CRITERIA (mandatory, in order):

1. RULE 1 CHECK (Capital Preservation / Ground Truth Primacy):
   Verify that no claim in this package overrides or contradicts documented
   broker or exchange behavior. If Antigravity's assumption conflicts with
   how NSE, BSE, or Zerodha actually operate (margin rules, T2T/ASM/GSM/ESM
   framework, settlement, auction mechanics, circuit limits, order/margin
   API behavior), this is a P0 finding regardless of how the number was
   derived.

2. CITATION REQUIREMENT:
   Every factual claim about exchange or broker behavior (margin %, circuit
   band, settlement cycle, surveillance stage, API constraint, fee/charge,
   holiday/session timing, etc.) MUST be traceable to a specific NSE
   circular, BSE circular, SEBI circular, or Zerodha
   documentation/Kite Connect API reference. Cite the source explicitly
   (document name/circular number/URL/page or the exact Zerodha doc
   section). If a claim cannot be traced to one of these primary sources,
   you MUST mark it "UNVERIFIED" — do not silently accept it, do not infer
   it from general market knowledge, and do not accept Antigravity's or
   Claude's restatement of the claim as its own source.

3. PROVENANCE / DATA LINEAGE CHECK:
   For every measured or derived value in MEASURED & DERIVED VALUES, trace
   it back to its origin: which source file, which broker/exchange feed,
   and which transformation produced it. Flag any value whose lineage
   cannot be reconstructed from the given source files as "UNVERIFIED —
   NO TRACEABLE LINEAGE."

4. CONFLICT-OF-INTEREST / ASSUMPTION-VERIFICATION CLAUSE:
   Antigravity is the orchestrator and has an interest in its own
   assumptions being accepted. Treat every assumption in ASSUMPTIONS
   PRESENTED BY ANTIGRAVITY as unproven until you have independently
   checked it against the RELEVANT SOURCE FILES and, where applicable,
   primary NSE/BSE/Zerodha documentation. Do not defer to Antigravity's
   framing of a fact as if it were already established. If a source file
   does not actually support the assumption attributed to it, state this
   explicitly as a finding.

5. EXPLICIT EXCLUSION — NO QUANTITATIVE THEORY:
   Do NOT evaluate statistical methodology, backtest design, model
   assumptions, indicator logic, position sizing math, or any other
   quantitative/strategy theory. That review belongs to Claude. Straying
   into it collapses the two-sided asymmetry this review process depends
   on. If a claim mixes a quantitative assertion with a factual/provenance
   one, review only the factual/provenance component and explicitly state
   that the quantitative component is out of scope for this audit.

6. UNCERTAINTY RULE:
   Silence or ambiguity is not a pass. If you cannot verify a claim with
   the evidence given, you MUST report it as a finding in the form:
   "UNVERIFIABLE (requires: <exactly what evidence, document, or file
   would resolve this>)". An UNVERIFIABLE finding is a first-class
   objection — it is not weaker than a CONFIRMED violation and must not be
   omitted or downgraded to a passing remark.

{instructions or ''}

OUTPUT FORMAT:
For each claim reviewed, state one of: CONFIRMED (with citation/source),
CONTRADICTED (with citation/source and the conflicting fact), or
UNVERIFIED / UNVERIFIABLE (requires: ...). State all unresolved P0
objections explicitly at the top of your output. Your output will be
recorded as codex_submission.md.
"""
    return prompt.strip()


class CodexReviewAdapter:
    """Adapter executing and authenticating Codex audit reviews under Antigravity orchestration."""

    def __init__(self, workspace_dir: Optional[str] = None):
        self.workspace_dir = workspace_dir or WORKSPACE_DIR

    def execute_review(
        self,
        package: Dict[str, Any],
        timeout_sec: int = 180
    ) -> Tuple[bool, Dict[str, Any], Optional[str]]:
        """
        Executes Codex regulatory and code audit review:
        1. Validates review package and target submission path.
        2. Dispatches prompt to Codex CLI (or hook).
        3. Writes output strictly to designated codex_submission.md.
        4. Cryptographically signs the response envelope using Codex's secret key.
        Returns: (success, response_envelope, error_message)
        """
        task_id = package.get("task_id", f"task_{uuid.uuid4().hex[:8]}")
        corr_id = package.get("correlation_id", f"corr_{uuid.uuid4().hex[:8]}")
        track = package.get("track", "SHARED")
        review_type = package.get("review_type", "REGULATORY").upper()

        if review_type not in ALLOWED_CODEX_REVIEW_TYPES:
            return False, {}, f"INVALID_REVIEW_TYPE: '{review_type}' not in {ALLOWED_CODEX_REVIEW_TYPES}"

        submission_rel = package.get("submission_file", "shared/reviews/codex_submission.md")

        # Enforce Authority Boundary: Codex may write ONLY to its own two
        # artifacts. A rebuttal goes to a separate file so that answering a
        # cross-examination can never destroy the original submission.
        norm_sub = os.path.normpath(submission_rel).replace("\\", "/")
        if os.path.basename(norm_sub) not in ALLOWED_CODEX_ARTIFACTS:
            return False, {}, (
                f"AUTHORITY_VIOLATION: Codex is restricted to "
                f"{sorted(ALLOWED_CODEX_ARTIFACTS)}, got '{submission_rel}'"
            )

        # Validate path security and track isolation
        valid_p, abs_sub, err = validate_path_security(submission_rel, track, self.workspace_dir)
        if not valid_p or not abs_sub:
            return False, {}, err or "INVALID_PATH"

        # Ensure submission path is within an allowed reviews directory
        norm_abs = os.path.normcase(abs_sub)
        allowed_dirs = list(ALLOWED_SUBMISSION_DIRS)
        if self.workspace_dir != WORKSPACE_DIR:
            allowed_dirs.append(os.path.normcase(os.path.abspath(os.path.join(self.workspace_dir, "shared", "reviews"))))
        in_allowed = any(norm_abs == d or norm_abs.startswith(d + os.path.sep) for d in allowed_dirs)
        if not in_allowed:
            return False, {}, f"DIRECTORY_SECURITY_VIOLATION: '{submission_rel}' outside allowed reviews directories."

        prompt = build_codex_prompt(package)

        # Dispatch execution
        t0 = time.time()
        if CODEX_DISPATCH_HOOK:
            res = CODEX_DISPATCH_HOOK(prompt, timeout_sec)
        else:
            res = ask_codex_detailed(prompt, timeout_sec)
        elapsed = time.time() - t0

        if not res.get("success"):
            return False, {}, res.get("error") or "Codex execution failed."

        output_text = res.get("output", "").strip()

        # Atomic write to codex_submission.md
        os.makedirs(os.path.dirname(abs_sub), exist_ok=True)
        tmp_write = abs_sub + f".tmp_{uuid.uuid4().hex[:8]}"
        with open(tmp_write, "w", encoding="utf-8") as f:
            f.write(output_text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_write, abs_sub)

        file_hash = compute_sha256(abs_sub)

        # Build authenticated envelope from CODEX to ANTIGRAVITY
        codex_key = get_agent_secret_key("CODEX")
        if not codex_key:
            return False, {}, "AUTH_CONFIG_ERROR: Missing external cryptographic key for CODEX."

        response_envelope = {
            "message_id": f"resp_codex_{uuid.uuid4().hex[:12]}",
            "correlation_id": corr_id,
            "task_id": task_id,
            "sender": "CODEX",
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
        response_envelope["auth_signature"] = compute_envelope_hmac(response_envelope, codex_key)

        return True, response_envelope, None
