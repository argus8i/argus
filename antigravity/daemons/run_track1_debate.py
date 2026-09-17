"""
run_track1_debate.py - Autonomous Tri-Agent Debate Runner for Track 1
Dispatches review mandates directly to Claude Code and OpenAI Codex / ChatGPT.
Both agents audit Antigravity's Section 1 submission and write their reviews
directly into Section 2 and Section 3 of shared/track1_esm/TRI_AGENT_REVIEW.md.
"""

import sys
import os
import time

sys.path.append(os.path.dirname(__file__))
from tri_agent_bus import ask_claude, ask_codex

CLAUDE_PROMPT = """
You are Claude Code (analyst / red team) collaborating in Project Swing Trades (`c:\\Users\\yashw\\swing trades`).
Follow `shared/00_PROTOCOL.md` and `AGENTS.md`.

Antigravity has submitted Section 1 of `shared/track1_esm/TRI_AGENT_REVIEW.md` (Track 1 ESM & Fixed-Circuit Architecture).
Your mandate:
1. Read `shared/track1_esm/TRI_AGENT_REVIEW.md` Section 1 carefully.
2. Attack the models, assumptions, and execution math:
   - Challenge the queue drain multiplier (QUEUE_MULT = 1.0 anchor from CROPSTER).
   - Stress-test the 10-day LC descent risk formula (Rule 5 40.1% loss) and Rule 9 (15% participation limit).
   - Evaluate the 4-state execution modeling (LOCKED_NO_BID, QUEUED, PARTIAL, FILLED).
   - Point out any hidden fail-open bugs, ungrounded heuristics, or missing edge cases.
3. Directly edit `shared/track1_esm/TRI_AGENT_REVIEW.md` and populate:
   # Section 2: Claude Code — Adversarial Challenges, Counterexamples & Red-Team Harness
   (Fill subsections 2.1 Claude Review & Adversarial Findings and 2.2 Claude Acceptance / Rejection Status).
4. Update `claude/PROGRESS.md` with your review.
5. Return a concise summary of your adversarial review findings.
"""

CODEX_PROMPT = """
You are OpenAI Codex / ChatGPT (regulatory compliance, filings, and microstructure auditor) in Project Swing Trades (`c:\\Users\\yashw\\swing trades`).
Follow `shared/00_PROTOCOL.md` and `AGENTS.md`.

Antigravity has submitted Section 1 of `shared/track1_esm/TRI_AGENT_REVIEW.md` (Track 1 ESM & Fixed-Circuit Architecture).
Your mandate:
1. Read `shared/track1_esm/TRI_AGENT_REVIEW.md` Section 1.
2. Audit regulatory compliance and microstructure accuracy:
   - Verify SEBI and BSE statutory references (BSE Notice 20230718-46, NSE/SURV/57609, SEBI CIR/MRD/DP/6/2013, BSE Master Circular Item 1.6 tick truncation).
   - Audit broker settlement execution realism (Zerodha T2T BTST block, CDSL TPIN vs DDPI, peak margin, short-delivery auction risks).
   - Verify that all current watchlist scrips (CCDL, CROPSTER, CHANDRIMA, GATECH) are disqualified under Rule 2 or Rule 6.
   - Confirm Gate count is strictly 0 / 60 prospective sessions.
3. Directly edit `shared/track1_esm/TRI_AGENT_REVIEW.md` and populate:
   # Section 3: OpenAI Codex / ChatGPT — Regulatory Compliance, Execution Realism & Reconciliation
   (Fill subsections 3.1 Codex Regulatory & Microstructure Audit and 3.2 Codex Acceptance / Rejection Status).
4. Return a concise summary of your regulatory audit findings.
"""


def main():
    print("=" * 70)
    print("STARTING AUTONOMOUS TRACK 1 TRI-AGENT DEBATE")
    print("=" * 70)

    print("\n[1/2] Launching Claude Code (Left Pane — Adversarial Audit)...")
    start_t = time.time()
    claude_res = ask_claude(CLAUDE_PROMPT, timeout_sec=240)
    claude_elapsed = time.time() - start_t
    print(f"Claude Code finished in {claude_elapsed:.1f}s.")
    print("-" * 50)
    print("CLAUDE RESPONSE:")
    print(claude_res)
    print("-" * 50)

    print("\n[2/2] Launching OpenAI Codex / ChatGPT (Right Pane — Regulatory Audit)...")
    start_t = time.time()
    codex_res = ask_codex(CODEX_PROMPT, timeout_sec=240)
    codex_elapsed = time.time() - start_t
    print(f"OpenAI Codex finished in {codex_elapsed:.1f}s.")
    print("-" * 50)
    print("CODEX RESPONSE:")
    print(codex_res)
    print("-" * 50)

    print("\n" + "=" * 70)
    print("TRI-AGENT DEBATE COMPLETE. REVIEWS PERSISTED TO DISK.")
    print("=" * 70)


if __name__ == "__main__":
    main()
