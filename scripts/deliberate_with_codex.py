"""
Deliberates with OpenAI Codex over the Nexus Bus regarding:
1. Technical opinion on Claude's T2-01 Job 2 (commit 995ae27).
2. Architecture & sequencing of Job 3 (Input Readiness Table) and Job 4A (Risk Limit Audit).
3. Strategic alignment on provider quota backoff and multi-day quantitative strategies.
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from antigravity.daemons.tri_agent_bus import ask_codex_detailed, log_interaction

PROMPT = """To: OpenAI Codex (Senior Systems, Execution-Reality & Reliability Engineer)
From: Antigravity (Quantitative Modeling & Infrastructure Orchestrator)
Subject: Nexus Deliberation & Build Plan Consultation (per Yashu Directive & AGENTS.md Rule 8)

Codex, Yashu has directed that we must proactively deliberate our build plans, reviews, and decisions over the Nexus Bus to ensure thorough alignment and eliminate conflicting assumptions.

I want to consult you on three critical items right now:

1. CLAUDE'S T2-01 JOB 2 SURVEILLANCE EVIDENCE-STORE REPAIR:
   - Claude finished implementation on branch `claude/t2-01-surveillance-session` in worktree `C:\\Users\\yashw\\swing-trades-claude-004` at commit `995ae27d561e24410d3fb5403381f77225f1cc0a` (parent `4be7563`).
   - Claude's report is at `shared/reviews/claude_t2_01_job2_report_2026-10-01.md`.
   - The changes address your prior finding CODEX-T2-01-4BE7563:
     - `research/framework/market.py`: replaces `os.replace` with `os.link(tmp, target)` (publish-if-absent).
     - Existing files validated against expected hash; mismatch raises `EVIDENCE_STORE_CORRUPT` -> plan `BLOCKED`.
     - Unique temp names (`.<sha>.<pid>.<random>.tmp`) cleaned up in `finally`.
     - 8-process barrier and junk-writer race tests in `research/tests/test_claude_t2_01_evidence_store_processes.py`.
     - Snapshot directory rename retry with exponential backoff (~3s) in `research/data/snapshot.py` to handle transient Windows antivirus PermissionError.
   - What is your independent review opinion and verdict (APPROVED or CHANGES_REQUIRED) on commit `995ae27`?

2. NEXT-PHASE SEQUENCING (JOBS 3 & 4A) DURING CLAUDE'S PROVIDER COOLDOWN:
   - Claude hit its provider session limit (`RESOURCE_EXHAUSTED / 429`) and is in cooldown until 16:10 IST. Per your 12:28 PM finding, we are standing down from all live dispatches to Claude until then.
   - We propose running two tasks in parallel right now:
     a) Job 3: Input Readiness & Provenance Table (100% read-only audit of all historical/daily data files under `shared/track2_liquid/history/` verifying dates, headers, SHA-256s, and consumer contracts).
     b) Job 4A: Risk Governor & Allocation Limit Audit (verifying Adjusted A1 limits: ₹38k slot cap, ₹114k aggregate exposure at limit price, ₹1,500 risk budget, fail-closed on missing ATR).
   - What is your technical advice on the design and failure boundaries for these two tasks?

3. QUANTITATIVE STRATEGY SHIFT (JOB 4B):
   - Intraday scalps failed due to round-trip friction (0.12% eating 0.06R-0.13R on tight 1% stops).
   - We are shifting focus to multi-day swing holding periods (2 to 10 days) on liquid F&O underlyings (Institutional Delivery Accumulation, 52-Week High Momentum, EOD PEAD) where target moves are 2.5% to 6.0%.
   - Do you see any execution-reality traps or data-contract flaws with this swing horizon?

Please provide your candid, unsparing technical analysis.
"""

def main():
    print("Dispatching deliberation query to OpenAI Codex via Nexus CLI bridge...")
    t0 = time.time()
    res = ask_codex_detailed(PROMPT, timeout_sec=300, chat_only=True)
    elapsed = time.time() - t0
    print(f"Deliberation completed in {elapsed:.2f}s.")
    print(f"Success: {res.get('success')}")
    print(f"Return code: {res.get('returncode')}")
    print(f"Error: {res.get('error')}")
    print("\n================== CODEX DELIBERATION RESPONSE ==================")
    print(res.get("output", ""))
    print("=================================================================\n")

if __name__ == "__main__":
    main()
