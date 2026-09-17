"""
dispatch_idea_review.py - Dispatches Track 1 Idea Review Mandates to Claude Code and OpenAI Codex.
Strictly adheres to AGENTS.md controls:
- Observation mode only
- No permission-bypass flags
- No modification of core model code
- Captures independent audits in shared/track1_esm/IDEA_REVIEW.md
"""

import os
import sys
import time

sys.path.append(os.path.dirname(__file__))
from tri_agent_bus import ask_claude_detailed, ask_codex_detailed

IDEA_REVIEW_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "shared", "track1_esm", "IDEA_REVIEW.md"))

CLAUDE_MANDATE = """You are Claude Code (analyst / red team) in Project Swing Trades (`c:\\Users\\yashw\\swing trades`).
Scope: Strictly Track 1 (ESM & Circuit Micro-Caps). Do not inspect or modify Track 2.
Debate document: `shared/track1_esm/IDEA_REVIEW.md`.

We are conducting a formal single-idea tri-agent review of:
"Rule 7 Pre-Circuit Accumulation Breakout: Two-sided base with 20D volume expansion >= 3x, spread under 1%, daily range over 3%, Rule 5 10-day LC sizing, Rule 9 volume cap, and Day 3/4 pre-emptive UC exits."

Antigravity has written Section 1 of `shared/track1_esm/IDEA_REVIEW.md`.

Your task:
1. Read `shared/track1_esm/IDEA_REVIEW.md` Section 1.
2. Independently test and review the mathematics, queue mechanics, adverse selection, sizing formulas (Rule 5 divisor 0.401, Rule 9 15% participation cap), pre-emptive exit feasibility, and provide counterexamples or stress scenarios.
3. Every numerical claim you make must include source, date, and MEASURED/DERIVED/ASSUMED status.
4. State your formal verdict: ACCEPT, REJECT, or ACCEPT WITH CONDITIONS.
5. Provide your review for Section 2 ("2. Independent Quantitative & Adverse-Selection Review (Claude Code Submission)").
Do NOT modify core model code.
Write your complete review directly replacing the placeholder in Section 2 of `shared/track1_esm/IDEA_REVIEW.md`, preserving all other sections untouched.
"""

CODEX_MANDATE = """You are OpenAI Codex / ChatGPT (regulatory compliance & microstructure auditor) in Project Swing Trades (`c:\\Users\\yashw\\swing trades`).
Scope: Strictly Track 1 (ESM & Circuit Micro-Caps). Do not inspect or modify Track 2.
Debate document: `shared/track1_esm/IDEA_REVIEW.md`.

We are conducting a formal single-idea tri-agent review of:
"Rule 7 Pre-Circuit Accumulation Breakout: Two-sided base with 20D volume expansion >= 3x, spread under 1%, daily range over 3%, Rule 5 10-day LC sizing, Rule 9 volume cap, and Day 3/4 pre-emptive UC exits."

Antigravity and Claude Code have written Sections 1 and 2 of `shared/track1_esm/IDEA_REVIEW.md`.

Your task:
1. Read `shared/track1_esm/IDEA_REVIEW.md`.
2. Independently audit code behavior, malformed inputs, exchange/broker rules (BSE/NSE circulars, Zerodha T2T T+1 settlement), data provenance table, and execution assumptions.
3. Every numerical claim you make must include source, date, and MEASURED/DERIVED/ASSUMED status.
4. State your formal verdict: ACCEPT, REJECT, or ACCEPT WITH CONDITIONS.
5. Provide your review for Section 3 ("3. Independent Regulatory, Broker RMS & Execution Review (OpenAI Codex Submission)").
Do NOT modify core model code.
Write your complete review directly replacing the placeholder in Section 3 of `shared/track1_esm/IDEA_REVIEW.md`, preserving all other sections untouched.
"""


def main():
    print("=" * 70, flush=True)
    print("TRACK 1 TRI-AGENT IDEA REVIEW DISPATCHER", flush=True)
    print(f"Review Document: {IDEA_REVIEW_PATH}", flush=True)
    print("=" * 70, flush=True)

    # 1. Dispatch to Claude Code
    print("\n[1/2] Dispatching mandate to Claude Code...", flush=True)
    t0 = time.time()
    claude_res = ask_claude_detailed(CLAUDE_MANDATE, timeout_sec=300)
    t_claude = time.time() - t0
    print(f"Claude Code completed in {t_claude:.1f}s | Success: {claude_res['success']} | Exit Code: {claude_res['returncode']}", flush=True)
    print("-" * 50, flush=True)
    print("Claude Output Summary:", flush=True)
    print(claude_res['output'][:1000], flush=True)
    print("-" * 50, flush=True)

    # 2. Dispatch to OpenAI Codex
    print("\n[2/2] Dispatching mandate to OpenAI Codex / ChatGPT...", flush=True)
    t0 = time.time()
    codex_res = ask_codex_detailed(CODEX_MANDATE, timeout_sec=300)
    t_codex = time.time() - t0
    print(f"OpenAI Codex completed in {t_codex:.1f}s | Success: {codex_res['success']} | Exit Code: {codex_res['returncode']}", flush=True)
    print("-" * 50, flush=True)
    print("Codex Output Summary:", flush=True)
    print(codex_res['output'][:1000], flush=True)
    print("-" * 50, flush=True)

    print("\n" + "=" * 70, flush=True)
    print("DISPATCH COMPLETE. Inspect shared/track1_esm/IDEA_REVIEW.md for results.", flush=True)
    print("=" * 70, flush=True)


if __name__ == "__main__":
    main()

