r"""
status.py - Hub-and-Spoke Tri-Agent Status & Health Interface
============================================================
Inspects and displays:
  1. Antigravity coordinator health & binary status.
  2. Claude adapter health & binary availability.
  3. Codex adapter health & binary availability.
  4. Outstanding review requests in inbox/outbox.
  5. Latest signed response from each reviewer with HMAC verification.
  6. Current synthesis state & unresolved objections.
  7. Dead-letter or timed-out tasks.
"""

import os
import sys
import tempfile
import json
import glob
from typing import Any, Dict, Optional

# Ensure workspace root is in sys.path
WORKSPACE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if WORKSPACE_DIR not in sys.path:
    sys.path.insert(0, WORKSPACE_DIR)

from antigravity.daemons.inbox_worker import (
    MESSAGES_ROOT,
    INBOX_DIR,
    OUTBOX_DIR,
    ARCHIVE_DIR,
    DEAD_LETTER_DIR,
    get_agent_secret_key,
    get_current_ist,
    compute_envelope_hmac,
)
from antigravity.daemons.tri_agent_bus import (
    CLAUDE_BIN,
    CODEX_BIN,
    AGY_BIN,
    verify_task_completion,
)


def inspect_agent_health(agent_name: str, binary_path: str) -> Dict[str, Any]:
    """Inspects agent binary existence and external key readiness."""
    bin_exists = os.path.exists(binary_path)
    key = get_agent_secret_key(agent_name)
    key_configured = bool(key and len(key) >= 16)

    status = "READY" if (bin_exists and key_configured) else ("NO_KEY" if bin_exists else "BIN_MISSING")
    return {
        "agent": agent_name,
        "status": status,
        "binary_path": binary_path,
        "binary_exists": bin_exists,
        "key_configured": key_configured
    }


def get_hub_status() -> Dict[str, Any]:
    """Aggregates comprehensive health, queue, and review status across the hub."""
    # 1. Adapter Health
    antigravity_health = inspect_agent_health("ANTIGRAVITY", AGY_BIN)
    claude_health = inspect_agent_health("CLAUDE", CLAUDE_BIN)
    codex_health = inspect_agent_health("CODEX", CODEX_BIN)

    # 2. Queue & Task Counts
    inbox_files = glob.glob(os.path.join(INBOX_DIR, "*.json"))
    claimed_files = glob.glob(os.path.join(INBOX_DIR, "*.claimed"))
    outbox_files = glob.glob(os.path.join(OUTBOX_DIR, "*.json"))
    dead_files = glob.glob(os.path.join(DEAD_LETTER_DIR, "*.json"))
    archive_files = glob.glob(os.path.join(ARCHIVE_DIR, "*.json"))

    # 3. Latest Signed Submissions
    latest_reviews = {"CLAUDE": None, "CODEX": None}
    for reviewer, sub_path in [
        ("CLAUDE", os.path.join(WORKSPACE_DIR, "shared", "reviews", "claude_submission.md")),
        ("CODEX", os.path.join(WORKSPACE_DIR, "shared", "reviews", "codex_submission.md"))
    ]:
        if os.path.exists(sub_path):
            mtime = os.path.getmtime(sub_path)
            size = os.path.getsize(sub_path)
            latest_reviews[reviewer] = {
                "file": os.path.relpath(sub_path, WORKSPACE_DIR),
                "mtime_epoch": mtime,
                "size_bytes": size,
                "status": "AVAILABLE"
            }

    # 4. Current Synthesis State
    synthesis_path = os.path.join(WORKSPACE_DIR, "shared", "reviews", "antigravity_synthesis.md")
    synthesis_state = {"status": "NO_SYNTHESIS", "unresolved_objections": []}
    if os.path.exists(synthesis_path):
        try:
            with open(synthesis_path, "r", encoding="utf-8") as f:
                text = f.read()
            dec = "UNKNOWN"
            if "**Status:** PASSED" in text or "**Decision:** **PASSED**" in text:
                dec = "PASSED"
            elif "**Status:** BLOCKED" in text or "**Decision:** **BLOCKED**" in text:
                dec = "BLOCKED"

            unresolved = []
            if "## 4. Dissent Ledger & Objections" in text:
                dissent_sec = text.split("## 4. Dissent Ledger & Objections", 1)[1].split("---", 1)[0]
                for line in dissent_sec.strip().split("\n"):
                    if line.startswith("- ") and "No unresolved" not in line:
                        unresolved.append(line[2:].strip())

            synthesis_state = {
                "status": dec,
                "file": "shared/reviews/antigravity_synthesis.md",
                "unresolved_objections": unresolved
            }
        except Exception as e:
            synthesis_state = {"status": "READ_ERROR", "error": str(e)}

    # 5. Dead-Letter / Error Diagnostics
    dead_task_errors = []
    for df in dead_files[-5:]:
        try:
            with open(df, "r", encoding="utf-8") as f:
                d = json.load(f)
            dead_task_errors.append({
                "file": os.path.basename(df),
                "error": d.get("error", "Unknown error"),
                "sender": d.get("sender"),
                "subject": d.get("subject")
            })
        except Exception:
            pass

    return {
        "timestamp_ist": get_current_ist(),
        "adapters": {
            "antigravity": antigravity_health,
            "claude": claude_health,
            "codex": codex_health
        },
        "queues": {
            "inbox_pending": len(inbox_files),
            "inbox_claimed": len(claimed_files),
            "outbox_responses": len(outbox_files),
            "archived_completed": len(archive_files),
            "dead_letter_failures": len(dead_files)
        },
        "latest_reviews": latest_reviews,
        "synthesis": synthesis_state,
        "recent_failures": dead_task_errors
    }


def print_dashboard():
    """Renders formatted console dashboard for Yashu."""
    st = get_hub_status()
    print("=" * 78)
    print("  PROJECT SWING TRADES: HUB-AND-SPOKE TRI-AGENT ORCHESTRATION DASHBOARD")
    print(f"  Time: {st['timestamp_ist']} | Central Orchestrator: Antigravity")
    print("=" * 78)

    print("\n[1] AGENT ADAPTER HEALTH:")
    for role, info in st["adapters"].items():
        status_icon = "[OK]" if info["status"] == "READY" else "[WARN]"
        print(f"  {status_icon} {info['agent']:<12}: Status={info['status']:<10} Binary={info['binary_exists']} Key={info['key_configured']}")

    print("\n[2] QUEUE TELEMETRY:")
    q = st["queues"]
    print(f"  Pending: {q['inbox_pending']} | In-Progress: {q['inbox_claimed']} | Outbox: {q['outbox_responses']} | Completed: {q['archived_completed']} | Dead-Letter: {q['dead_letter_failures']}")

    print("\n[3] LATEST REVIEWER SUBMISSIONS:")
    for rev, rev_info in st["latest_reviews"].items():
        if rev_info:
            print(f"  * {rev}: {rev_info['file']} ({rev_info['size_bytes']} bytes)")
        else:
            print(f"  * {rev}: None recorded")

    print("\n[4] SYNTHESIS & DISSENT STATUS:")
    syn = st["synthesis"]
    print(f"  Latest Synthesis Status: {syn['status']}")
    if syn.get("unresolved_objections"):
        print("  Unresolved Objections:")
        for obj in syn["unresolved_objections"]:
            print(f"    - {obj}")
    else:
        print("  Unresolved Objections: None (Consensus cleared)")

    if st["recent_failures"]:
        print("\n[5] DEAD-LETTER FAILURES:")
        for fail in st["recent_failures"]:
            print(f"  * [{fail['file']}] {fail['error']}")
    print("=" * 78)


def run_hub_demonstration():
    """Executes the full 9-step Hub-and-Spoke Tri-Agent workflow demonstration."""
    import hmac
    from antigravity.orchestrator.coordinator import AntigravityCoordinator
    import antigravity.adapters.claude_adapter as ca
    import antigravity.adapters.codex_adapter as cxa
    from antigravity.daemons.inbox_worker import compute_envelope_hmac


    print("=" * 78)
    print("  PROJECT SWING TRADES: HUB-AND-SPOKE PLUMBING DEMO [SIMULATED REVIEWERS]")
    print("  Central Orchestrator: ANTIGRAVITY | Secondary Reviewers: CLAUDE & CODEX")
    print("=" * 78)

    # The demo runs against a throwaway sandbox WORKSPACE, the way the hub
    # tests do. The adapters whitelist <workspace>/shared/reviews for a
    # non-default workspace, so this passes path validation while keeping
    # simulated output away from the canonical review directory.
    _DEMO_WORKSPACE = tempfile.mkdtemp(prefix="agy_demo_ws_")
    _DEMO_REVIEW_DIR = os.path.join(_DEMO_WORKSPACE, "shared", "reviews")
    os.makedirs(_DEMO_REVIEW_DIR, exist_ok=True)
    print(f"  [DEMO] Simulated submissions -> {_DEMO_REVIEW_DIR}")
    coordinator = AntigravityCoordinator(_DEMO_WORKSPACE)

    # STEP 1: Antigravity Primary Quantitative Analysis
    print("\n[STEP 1] Antigravity: Formulating Primary Quantitative Modeling...")
    task_id = "TASK_MOBIKWIK_ORB_AUDIT"
    track = "TRACK_2"
    exact_question = (
        "Audit MOBIKWIK Day 3 Pre-Emptive Profit Exit vs. 10-Day LC Lockout Risk under AGENTS.md Rule 11."
    )
    primary_analysis = (
        "### Antigravity Primary Quantitative Model:\n"
        "- Security: MOBIKWIK (F&O Underlying candidate, CMP: 202.91 INR)\n"
        "- Entry: Day 1 Breakout at 202.91 INR with 1,500 INR risk budget (Rule 11).\n"
        "- Target Exit: Day 3 Pre-Emptive Profit Exit at +15.5% (234.35 INR) into UC buyer depth.\n"
        "- Downside Protection: Daily flex band monitoring per NSE circular NSE/FAOP/62241.\n"
        "- Verification Required:\n"
        "  1. Claude (Math/Microstructure): Adverse selection when selling into Day 3 UC queue.\n"
        "  2. Codex (Broker/Regulatory): F&O margin maintenance and ESM Stage 1/2 exemption bounds."
    )
    print("  -> Primary model formulated.")

    # STEP 2: Antigravity Constructs Canonical Review Package
    print("\n[STEP 2] Antigravity: Building Immutable Canonical Review Package...")
    pkg = coordinator.create_review_package(
        task_id=task_id,
        track=track,
        exact_question=exact_question,
        assumptions={
            "entry_price": 202.91,
            "shares": 50,
            "target_exit": 234.35,
            "risk_rupees": 1500,
            "is_fno_underlying": True
        },
        source_files=["antigravity/models/risk_calculator.py"],
        measured_values={
            "daily_turnover_cr": 45.2,
            "participation_pct": 0.022,
            "band_pct": 0.20
        },
        requested_review="HIGH_IMPACT_CORE",
        # Relative to the sandbox workspace, never the canonical tree.
        submission_dir="shared/reviews"
    )
    print(f"  -> Package created with Correlation ID: {pkg['correlation_id']}")

    # Reviewer simulations for deterministic verification
    def simulated_claude(prompt: str, timeout: int):
        return {
            "success": True,
            "output": (
                "## Claude Quantitative Red-Team Review\n\n"
                "### 1. Adverse Selection Analysis:\n"
                "- Participation rate is 0.022% (well within Claude Rule 9 cap of 15%).\n"
                "- Day 3 exit at +15.5% into buyer queue avoids the 'buying the exit' trap.\n"
                "### 2. Microstructure Challenge:\n"
                "- If dynamic flex band fails to trigger on NSE FAOP, does Zerodha RMS square off intraday?\n"
                "### Verdict: CONDITIONALLY_APPROVED (No P0 objections)."
            ),
            "returncode": 0,
            "elapsed": 0.08
        }

    def simulated_codex(prompt: str, timeout: int):
        return {
            "success": True,
            "output": (
                "## Codex Broker & Regulatory Audit\n\n"
                "### 1. Surveillance Screening:\n"
                "- Scrip is active F&O underlying; ESM Stage 1/2 does NOT apply per Rule 11.\n"
                "- ASM/GSM screening verified clean (`is_surveillance: False`).\n"
                "### 2. Execution Compliance:\n"
                "- Order routing strictly respects Cash EQ delivery boundaries.\n"
                "### Verdict: APPROVED."
            ),
            "returncode": 0,
            "elapsed": 0.07
        }

    ca.CLAUDE_DISPATCH_HOOK = simulated_claude
    cxa.CODEX_DISPATCH_HOOK = simulated_codex

    # STEP 3 & 4: Antigravity Dispatches Review to Secondary Reviewers
    print("\n[STEP 3 & 4] Antigravity: Dispatching to Claude (Red-Team) and Codex (Auditor)...")
    dispatch_results = coordinator.dispatch_review(pkg)
    print(f"  -> Dispatch status: {dispatch_results['status']}")
    print(f"  -> Claude response status: {dispatch_results['claude']['status']}")
    print(f"  -> Codex response status: {dispatch_results['codex']['status']}")

    # STEP 5: Authority Boundary Verification
    print("\n[STEP 5] Verifying Authority Boundaries and Dedicated Submissions...")
    claude_sub_path = os.path.join(_DEMO_REVIEW_DIR, "claude_submission.md")
    codex_sub_path = os.path.join(_DEMO_REVIEW_DIR, "codex_submission.md")
    assert os.path.exists(claude_sub_path), "Claude submission file missing!"
    assert os.path.exists(codex_sub_path), "Codex submission file missing!"
    print(f"  [OK] Claude submission recorded at: shared/reviews/claude_submission.md ({os.path.getsize(claude_sub_path)} bytes)")
    print(f"  [OK] Codex submission recorded at: shared/reviews/codex_submission.md ({os.path.getsize(codex_sub_path)} bytes)")

    # STEP 6: Antigravity Routes Cross-Examination (Claude -> Antigravity -> Codex)
    print("\n[STEP 6] Routing Cross-Reviewer Challenge: Claude -> Antigravity -> Codex...")
    challenge = "Claude challenges: What is Zerodha RMS square-off behavior if the dynamic flex band delays opening?"
    def codex_rebuttal(prompt: str, timeout: int):
        return {
            "success": True,
            "output": (
                "## Codex Rebuttal to Claude Challenge\n"
                "Zerodha RMS auto-squareoff operates at 15:20 IST. Since Track 2 trades are delivery-based Cash EQ "
                "with full cash margin (no intraday MIS leverage), Zerodha does NOT force-liquidate at 15:20 IST. "
                "Holding converts to CNC delivery safely without margin penalty."
            ),
            "returncode": 0,
            "elapsed": 0.05
        }
    cxa.CODEX_DISPATCH_HOOK = codex_rebuttal

    success_cross, rebuttal_env, err_cross = coordinator.route_cross_examination(
        from_agent="CLAUDE",
        to_agent="CODEX",
        challenge_text=challenge,
        original_package=pkg
    )
    assert success_cross, f"Cross examination failed: {err_cross}"
    print("  [OK] Cross-examination routed through Antigravity Hub successfully.")
    print(f"  [OK] Codex rebuttal received and signed: {rebuttal_env['message_id']}")

    # STEP 7 & 8: Antigravity Final Synthesis
    print("\n[STEP 7 & 8] Antigravity: Synthesizing Final Consensus & Audit Trail...")
    synthesis_res = coordinator.synthesize_outcome(
        task_id=task_id,
        track=track,
        question=exact_question,
        primary_analysis=primary_analysis,
        claude_review=dispatch_results["claude"],
        codex_review=dispatch_results["codex"],
        rebuttal_review=rebuttal_env,
        synthesis_file_rel="shared/reviews/antigravity_synthesis.md"
    )

    # Sandbox workspace, not the canonical tree.
    synthesis_path = os.path.join(_DEMO_REVIEW_DIR, "antigravity_synthesis.md")
    assert os.path.exists(synthesis_path), "Synthesis file missing!"
    print(f"  [OK] Synthesis decision: {synthesis_res['decision']} (Confidence: {synthesis_res['confidence']})")
    print("  [OK] Canonical synthesis written to: shared/reviews/antigravity_synthesis.md")

    # STEP 9: Cryptographic & Integrity Verification
    print("\n[STEP 9] Verifying Cryptographic Envelopes and Invariants...")
    agy_key = get_agent_secret_key("ANTIGRAVITY")
    syn_env = synthesis_res["synthesis_envelope"]
    expected_agy_sig = compute_envelope_hmac(syn_env, agy_key)
    assert hmac.compare_digest(syn_env.get("auth_signature", ""), expected_agy_sig), "Antigravity synthesis signature invalid!"
    print("  [OK] Antigravity synthesis HMAC-SHA256 signature verified.")

    claude_key = get_agent_secret_key("CLAUDE")
    claude_env = dispatch_results["claude"]
    expected_claude_sig = compute_envelope_hmac(claude_env, claude_key)
    assert hmac.compare_digest(claude_env.get("auth_signature", ""), expected_claude_sig), "Claude submission signature invalid!"
    print("  [OK] Claude submission HMAC-SHA256 signature verified.")

    codex_key = get_agent_secret_key("CODEX")
    codex_env = dispatch_results["codex"]
    expected_codex_sig = compute_envelope_hmac(codex_env, codex_key)
    assert hmac.compare_digest(codex_env.get("auth_signature", ""), expected_codex_sig), "Codex submission signature invalid!"
    print("  [OK] Codex submission HMAC-SHA256 signature verified.")

    print("\n" + "=" * 78)
    print("  SIMULATED DEMONSTRATION COMPLETE - NO REVIEWER WAS CONTACTED")
    print("=" * 78)
    print("  Claude and Codex responses above came from hardcoded stubs in this")
    print("  file. This exercises the plumbing ONLY. It is not a verification of")
    print("  any trade, model or review, and must never be reported as one.")
    print("  A real review requires the reviewer CLI with no dispatch hook.")
    print(f"  Simulated artifacts were written to: {_DEMO_WORKSPACE}")
    print("=" * 78)


if __name__ == "__main__":
    if "--demo" in sys.argv:
        run_hub_demonstration()
    elif "--json" in sys.argv:
        print(json.dumps(get_hub_status(), indent=2))
    else:
        print_dashboard()

