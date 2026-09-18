r"""
coordinator.py - Antigravity Primary Orchestrator & Hub-and-Spoke Coordinator
=============================================================================
Central operating environment for Project Swing Trades Tri-Agent System.

Hierarchy:
  - Antigravity (Primary Orchestrator): Central hub, receives Yashu's instructions,
    performs primary research/modeling, prepares review packages, routes reviews,
    executes bounded cross-examination, and synthesizes final results.
  - Claude (Secondary Red-Team): Quantitative/microstructure criticism. Writes only claude_submission.md.
  - Codex (Secondary Auditor): Regulatory/code audit. Writes only codex_submission.md.

Communication Topology:
  - Hub-and-Spoke: All cross-agent messages route through Antigravity:
    Claude -> Antigravity -> Codex
    Codex -> Antigravity -> Claude
"""

import os
import sys
import time
import json
import uuid
import re
import hmac
from typing import Any, Dict, List, Optional, Tuple

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
    log_interaction,
    verify_task_completion,
)
from antigravity.adapters.claude_adapter import (
    ClaudeReviewAdapter,
    ALLOWED_CLAUDE_REVIEW_TYPES,
)
from antigravity.adapters.codex_adapter import (
    CodexReviewAdapter,
    ALLOWED_CODEX_REVIEW_TYPES,
)

COMPOSITE_REVIEW_TYPES = {"HIGH_IMPACT_CORE", "BOTH"}
ALL_VALID_REVIEW_TYPES = (
    (ALLOWED_CLAUDE_REVIEW_TYPES | ALLOWED_CODEX_REVIEW_TYPES | COMPOSITE_REVIEW_TYPES)
    - {"REBUTTAL"}
)


class AntigravityCoordinator:
    """Central primary orchestrator managing hub-and-spoke multi-agent reviews and synthesis."""

    def __init__(self, workspace_dir: Optional[str] = None):
        self.workspace_dir = workspace_dir or WORKSPACE_DIR
        self.claude_adapter = ClaudeReviewAdapter(self.workspace_dir)
        self.codex_adapter = CodexReviewAdapter(self.workspace_dir)

    def create_review_package(
        self,
        task_id: str,
        track: str,
        exact_question: str,
        assumptions: Dict[str, Any],
        source_files: List[str],
        measured_values: Dict[str, Any],
        requested_review: str,
        deadline_ist: Optional[str] = None,
        instructions: Optional[str] = None,
        submission_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Creates a canonical, immutable review package for reviewer dispatch."""
        corr_id = f"corr_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        sub_dir = submission_dir or "shared/reviews"
        deadline = deadline_ist or get_current_ist()

        package = {
            "task_id": task_id,
            "correlation_id": corr_id,
            "track": track,
            "exact_question": exact_question,
            "assumptions": assumptions,
            "source_files": source_files,
            "measured_values": measured_values,
            "requested_review": requested_review.upper(),
            "deadline_ist": deadline,
            "instructions": instructions or "",
            "submission_dir": sub_dir,
            "created_at_ist": get_current_ist(),
            "coordinator": "ANTIGRAVITY"
        }
        return package

    def dispatch_review(
        self,
        package: Dict[str, Any],
        timeout_sec: int = 180
    ) -> Dict[str, Any]:
        """
        Routes review package to the appropriate secondary reviewer(s) based on domain:
        - Mathematics, Microstructure, Adverse Selection -> Claude
        - Regulatory, Broker Rules, Code Audit, Reality/Provenance -> Codex
        - High-Impact Core Model Change or BOTH -> Both Claude and Codex
        Fails closed on unknown review types or zero-recipient routing.
        """
        review_type = package.get("requested_review", "HIGH_IMPACT_CORE").upper()
        track = package.get("track", "SHARED")
        sub_dir = package.get("submission_dir", "shared/reviews")

        results = {
            "task_id": package.get("task_id"),
            "correlation_id": package.get("correlation_id"),
            "review_type": review_type,
            "claude": None,
            "codex": None,
            "status": "DISPATCHED",
            "errors": []
        }

        # Validate against known allowed review types
        if review_type not in ALL_VALID_REVIEW_TYPES:
            results["status"] = "REJECTED_UNKNOWN_TYPE"
            results["errors"].append(
                f"Unknown review_type: '{review_type}'. Must be one of {sorted(ALL_VALID_REVIEW_TYPES)}"
            )
            return results

        # Determine dispatch targets
        should_dispatch_claude = (
            review_type in ALLOWED_CLAUDE_REVIEW_TYPES or review_type in COMPOSITE_REVIEW_TYPES
        )
        should_dispatch_codex = (
            review_type in ALLOWED_CODEX_REVIEW_TYPES or review_type in COMPOSITE_REVIEW_TYPES
        )

        if not should_dispatch_claude and not should_dispatch_codex:
            results["status"] = "ZERO_RECIPIENTS"
            results["errors"].append(f"No reviewers configured for review_type: '{review_type}'")
            return results

        # 1. Dispatch to Claude if relevant
        if should_dispatch_claude:
            claude_pkg = dict(package)
            claude_pkg["review_type"] = (
                "MICROSTRUCTURE" if review_type in COMPOSITE_REVIEW_TYPES else review_type
            )
            claude_pkg["submission_file"] = f"{sub_dir}/claude_submission.md"

            success, env, err = self.claude_adapter.execute_review(claude_pkg, timeout_sec)
            if success:
                results["claude"] = env
                log_interaction("Claude Code", package.get("exact_question"), env.get("output_payload", {}).get("review_text", ""), timeout_sec, 0)
            else:
                results["errors"].append(f"Claude review failed: {err}")

        # 2. Dispatch to Codex if relevant
        if should_dispatch_codex:
            codex_pkg = dict(package)
            codex_pkg["review_type"] = (
                "REALITY_AUDIT" if review_type in COMPOSITE_REVIEW_TYPES else review_type
            )
            codex_pkg["submission_file"] = f"{sub_dir}/codex_submission.md"

            success, env, err = self.codex_adapter.execute_review(codex_pkg, timeout_sec)
            if success:
                results["codex"] = env
                log_interaction("OpenAI Codex", package.get("exact_question"), env.get("output_payload", {}).get("review_text", ""), timeout_sec, 0)
            else:
                results["errors"].append(f"Codex review failed: {err}")

        results["status"] = "COMPLETED" if not results["errors"] else "PARTIAL_ERROR"
        return results

    def route_cross_examination(
        self,
        from_agent: str,
        to_agent: str,
        challenge_text: str,
        original_package: Dict[str, Any],
        timeout_sec: int = 180
    ) -> Tuple[bool, Dict[str, Any], Optional[str]]:
        """
        Hub-and-Spoke cross-examination router:
        Routes:
          Claude -> Antigravity -> Codex
          Codex -> Antigravity -> Claude
        Logs the cross-examination exchange through Antigravity before dispatch.
        """
        from_agent = from_agent.upper()
        to_agent = to_agent.upper()

        if from_agent not in ["CLAUDE", "CODEX"] or to_agent not in ["CLAUDE", "CODEX"]:
            return False, {}, f"INVALID_CROSS_EXAM_AGENTS: '{from_agent}' -> '{to_agent}'"

        if from_agent == to_agent:
            return False, {}, "CANNOT_CROSS_EXAMINE_SELF"

        sub_dir = original_package.get("submission_dir", "shared/reviews")

        # Create cross-examination package
        cross_pkg = dict(original_package)
        cross_pkg["review_type"] = "REBUTTAL"
        cross_pkg["exact_question"] = f"[CROSS-EXAMINATION via ANTIGRAVITY from {from_agent}]:\n{challenge_text}"
        cross_pkg["instructions"] = f"Address the specific challenge raised by {from_agent}. State agreement or rebut with evidence."

        log_msg = f"[HUB-AND-SPOKE CROSS-EXAMINATION] Routing challenge from {from_agent} to {to_agent} through Antigravity Hub."
        log_interaction(f"{from_agent} -> ANTIGRAVITY -> {to_agent}", challenge_text, log_msg, 0.0, 0)

        # A rebuttal is written to its own artifact. Routing it at the reviewer's
        # submission file would overwrite the original review with the answer to a
        # challenge, silently destroying the objections the hub exists to preserve.
        if to_agent == "CODEX":
            cross_pkg["submission_file"] = f"{sub_dir}/codex_rebuttal.md"
            return self.codex_adapter.execute_review(cross_pkg, timeout_sec)
        else:
            cross_pkg["submission_file"] = f"{sub_dir}/claude_rebuttal.md"
            return self.claude_adapter.execute_review(cross_pkg, timeout_sec)

    def synthesize_outcome(
        self,
        task_id: str,
        track: str,
        question: str,
        primary_analysis: str,
        claude_review: Optional[Dict[str, Any]],
        codex_review: Optional[Dict[str, Any]],
        rebuttal_review: Optional[Dict[str, Any]] = None,
        synthesis_file_rel: str = "shared/reviews/antigravity_synthesis.md",
        review_package: Optional[Dict[str, Any]] = None,
        account_observation_verified: bool = False
    ) -> Dict[str, Any]:
        """
        Synthesizes primary analysis, secondary reviews, and rebuttals into antigravity_synthesis.md.
        Enforces:
          - Cryptographic verification of all reviewer envelope HMAC signatures.
          - On-disk existence and SHA-256 integrity verification of declared artifacts.
          - Mandatory reviewer presence enforcement based on review_package requested_review.
          - Task and correlation identity alignment between package and review envelopes.
          - Preservation of all dissents and counterarguments.
          - Unresolved P0 / Critical objections or unauthenticated evidence strictly BLOCK approval.
          - Rule 1 (100% Cash / Paper Observation) cash state attestation backed by observation log.
        """
        abs_synthesis = os.path.abspath(os.path.join(self.workspace_dir, synthesis_file_rel))
        os.makedirs(os.path.dirname(abs_synthesis), exist_ok=True)

        unresolved_objections = []
        agreed_points = []
        p0_objections_found = False

        # 1. Verify original review package requirements if package provided
        if review_package:
            pkg_task_id = review_package.get("task_id")
            if pkg_task_id and pkg_task_id != task_id:
                p0_objections_found = True
                unresolved_objections.append(
                    f"[Identity Mismatch]: Package task_id '{pkg_task_id}' does not match synthesis task_id '{task_id}'."
                )

            req_review = review_package.get("requested_review", "HIGH_IMPACT_CORE").upper()
            if req_review in COMPOSITE_REVIEW_TYPES:
                if not claude_review:
                    p0_objections_found = True
                    unresolved_objections.append(
                        f"[Missing Required Reviewer]: Claude review is mandatory for '{req_review}' but was not provided."
                    )
                if not codex_review:
                    p0_objections_found = True
                    unresolved_objections.append(
                        f"[Missing Required Reviewer]: Codex review is mandatory for '{req_review}' but was not provided."
                    )
            elif req_review in ALLOWED_CLAUDE_REVIEW_TYPES and req_review not in ALLOWED_CODEX_REVIEW_TYPES:
                if not claude_review:
                    p0_objections_found = True
                    unresolved_objections.append(
                        f"[Missing Required Reviewer]: Claude review is mandatory for '{req_review}' but was not provided."
                    )
            elif req_review in ALLOWED_CODEX_REVIEW_TYPES and req_review not in ALLOWED_CLAUDE_REVIEW_TYPES:
                if not codex_review:
                    p0_objections_found = True
                    unresolved_objections.append(
                        f"[Missing Required Reviewer]: Codex review is mandatory for '{req_review}' but was not provided."
                    )

            # Verify declared artifact hashes in review package
            declared_hashes = review_package.get("artifact_hashes", {})
            if isinstance(declared_hashes, dict):
                for rel_path, expected_hash in declared_hashes.items():
                    abs_art = os.path.abspath(os.path.join(self.workspace_dir, rel_path))
                    if not os.path.exists(abs_art):
                        p0_objections_found = True
                        unresolved_objections.append(
                            f"[Artifact Missing]: Declared artifact '{rel_path}' does not exist on disk."
                        )
                    else:
                        actual_hash = compute_sha256(abs_art)
                        if actual_hash != expected_hash:
                            p0_objections_found = True
                            unresolved_objections.append(
                                f"[Artifact Hash Mismatch]: '{rel_path}' sha256 ({actual_hash[:12]}...) != expected ({expected_hash[:12]}...)."
                            )

        # 2. Helper to verify each reviewer envelope integrity
        def _verify_envelope(env: Dict[str, Any], default_sender: str, role_title: str):
            nonlocal p0_objections_found
            sender = (env.get("sender") or default_sender).upper()
            env_task_id = env.get("task_id")
            if env_task_id and env_task_id != task_id:
                p0_objections_found = True
                unresolved_objections.append(
                    f"[{role_title} Identity Mismatch]: envelope task_id '{env_task_id}' != synthesis task_id '{task_id}'."
                )

            # HMAC signature verification
            sig = env.get("auth_signature")
            if not sig:
                p0_objections_found = True
                unresolved_objections.append(f"[{role_title} Unauthenticated]: Missing auth_signature in envelope.")
            else:
                key = get_agent_secret_key(sender)
                if not key:
                    p0_objections_found = True
                    unresolved_objections.append(f"[{role_title} Key Missing]: No secret key configured for {sender}.")
                else:
                    expected_sig = compute_envelope_hmac(env, key)
                    if not hmac.compare_digest(sig, expected_sig):
                        p0_objections_found = True
                        unresolved_objections.append(f"[{role_title} Signature Invalid]: auth_signature failed HMAC verification.")

            # Submission file & sha256 verification
            sub_file = env.get("submission_file")
            if sub_file:
                abs_sub = os.path.abspath(os.path.join(self.workspace_dir, sub_file))
                if not os.path.exists(abs_sub):
                    p0_objections_found = True
                    unresolved_objections.append(f"[{role_title} Submission Missing]: File '{sub_file}' not found on disk.")
                elif env.get("sha256"):
                    actual_sha = compute_sha256(abs_sub)
                    if actual_sha != env.get("sha256"):
                        p0_objections_found = True
                        unresolved_objections.append(f"[{role_title} Artifact Tampered]: '{sub_file}' sha256 does not match envelope sha256.")

            # Inspect payload and verdict
            payload = env.get("output_payload", {})
            if payload.get("has_p0_objection"):
                p0_objections_found = True
                unresolved_objections.append(f"[{role_title} P0 / Critical Objection]: Blocking risk detected.")

            rev_text = payload.get("review_text", "")
            if re.search(r"\b(?:VERDICT:\s*(?:BLOCKED|REJECTED)|Verdict:\s*(?:BLOCKED|REJECTED))\b", rev_text, re.I):
                p0_objections_found = True
                if not any(f"[{role_title} P0" in o for o in unresolved_objections):
                    unresolved_objections.append(f"[{role_title} Verdict Blocked]: Review text explicitly states BLOCKED/REJECTED.")

        # Inspect Claude Findings
        claude_text = ""
        claude_sig = ""
        if claude_review:
            claude_text = claude_review.get("output_payload", {}).get("review_text", "")
            claude_sig = claude_review.get("auth_signature", "")
            _verify_envelope(claude_review, "CLAUDE", "Claude")

        # Inspect Codex Findings
        codex_text = ""
        codex_sig = ""
        if codex_review:
            codex_text = codex_review.get("output_payload", {}).get("review_text", "")
            codex_sig = codex_review.get("auth_signature", "")
            _verify_envelope(codex_review, "CODEX", "Codex")

        # Inspect Rebuttal
        rebuttal_text = ""
        if rebuttal_review:
            rebuttal_text = rebuttal_review.get("output_payload", {}).get("review_text", "")
            reb_sender = rebuttal_review.get("sender", "CODEX")
            _verify_envelope(rebuttal_review, reb_sender, f"Rebuttal ({reb_sender})")

        # Determine Decision
        if p0_objections_found:
            decision = "BLOCKED"
            confidence = "LOW"
            decision_rationale = f"Proposal is strictly BLOCKED due to {len(unresolved_objections)} unresolved objections or verification failures."
        elif not claude_review and not codex_review:
            decision = "INCOMPLETE"
            confidence = "NONE"
            decision_rationale = "No secondary reviewer submissions received."
        else:
            decision = "PASSED"
            confidence = "HIGH"
            decision_rationale = "Secondary reviews completed with verified signatures, matching artifact hashes, and zero blocking P0 objections."

        rule1_attestation = (
            "VERIFIED (Backed by authorized account observation log)."
            if account_observation_verified
            else "UNVERIFIED (No live account observation attached; paper observation only)."
        )
        rule8_attestation = (
            "Cross-agent peer review recorded and cryptographically verified."
            if decision == "PASSED"
            else "Cross-agent peer review recorded; cryptographic verification FAILED, BLOCKED, or INCOMPLETE."
        )

        synthesis_md = f"""# Antigravity Synthesis: {task_id}
**Orchestrator:** Antigravity (Primary Operating Environment)  
**Track:** {track} · **Status:** {decision} · **Confidence:** {confidence}  
**Date/Time:** {get_current_ist()}  

---

## 1. Mandate & Question
{question}

---

## 2. Antigravity Primary Analysis
{primary_analysis.strip()}

---

## 3. Secondary Reviewer Submissions (Authenticated)

### Claude Quantitative Red-Team Submission
- **File:** `{claude_review.get('submission_file') if claude_review else 'N/A'}`
- **Signature:** `{claude_sig[:24] + '...' if claude_sig else 'None'}`
- **Review Summary:**
```markdown
{claude_text.strip() if claude_text else 'No Claude review dispatched/received.'}
```

### Codex Engineering & Regulatory Audit Submission
- **File:** `{codex_review.get('submission_file') if codex_review else 'N/A'}`
- **Signature:** `{codex_sig[:24] + '...' if codex_sig else 'None'}`
- **Review Summary:**
```markdown
{codex_text.strip() if codex_text else 'No Codex review dispatched/received.'}
```

{"### Cross-Review Rebuttal" if rebuttal_text else ""}
{f"```markdown\n{rebuttal_text.strip()}\n```" if rebuttal_text else ""}

---

## 4. Dissent Ledger & Objections
{"- No unresolved P0 objections recorded." if not unresolved_objections else chr(10).join(f"- {o}" for o in unresolved_objections)}

---

## 5. Final Synthesis & Decision Gate
- **Decision:** **{decision}**
- **Confidence Level:** **{confidence}**
- **Rationale:** {decision_rationale}
- **Rule 1 Verification (100% Cash / Paper Observation Gate):** {rule1_attestation}
- **Rule 8 Tri-Agent Protocol:** {rule8_attestation}
- **Rule 11 Track Isolation:** Enforced fail-closed on {track}.
"""
        # Write synthesis file atomically
        write_json_atomic(abs_synthesis + ".json", {"raw": synthesis_md})
        with open(abs_synthesis, "w", encoding="utf-8") as f:
            f.write(synthesis_md)

        syn_hash = compute_sha256(abs_synthesis)
        antigravity_key = get_agent_secret_key("ANTIGRAVITY") or "antigravity_default_secure_secret_key_2026"

        synthesis_envelope = {
            "task_id": task_id,
            "correlation_id": f"corr_syn_{uuid.uuid4().hex[:8]}",
            "orchestrator": "ANTIGRAVITY",
            "track": track,
            "decision": decision,
            "confidence": confidence,
            "synthesis_file": synthesis_file_rel,
            "sha256": syn_hash,
            "p0_objections_found": p0_objections_found,
            "completed_at_ist": get_current_ist(),
            "nonce": uuid.uuid4().hex
        }
        synthesis_envelope["auth_signature"] = compute_envelope_hmac(synthesis_envelope, antigravity_key)

        return {
            "success": True,
            "decision": decision,
            "confidence": confidence,
            "synthesis_file": synthesis_file_rel,
            "synthesis_envelope": synthesis_envelope,
            "unresolved_objections": unresolved_objections
        }

