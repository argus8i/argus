"""
run_claude_review.py - Invokes Claude Code to print Section 2 directly to stdout without tool calls.
"""

import os
import sys
import time

sys.path.append(os.path.dirname(__file__))
from tri_agent_bus import ask_claude_detailed

CLAUDE_PROMPT = """You are Claude Code (analyst / red team) in Project Swing Trades (`c:\\Users\\yashw\\swing trades`).
Scope: Strictly Track 1 (ESM & Circuit Micro-Caps). Do not inspect or modify Track 2.
Debate document: `shared/track1_esm/IDEA_REVIEW.md`.

We are conducting a formal single-idea tri-agent review of:
"Rule 7 Pre-Circuit Accumulation Breakout: Two-sided base with 20D volume expansion >= 3x, spread under 1%, daily range over 3%, Rule 5 10-day LC sizing, Rule 9 volume cap, and Day 3/4 pre-emptive UC exits."

Read Section 1 and Section 3 of `shared/track1_esm/IDEA_REVIEW.md`.
Independently test and review the mathematics, queue mechanics, adverse selection, sizing formulas (Rule 5 divisor 0.401, Rule 9 15% participation cap), pre-emptive exit feasibility, and provide counterexamples or stress scenarios.
Every numerical claim you make must include source, date, and MEASURED/DERIVED/ASSUMED status.
State your formal verdict: ACCEPT, REJECT, or ACCEPT WITH CONDITIONS.

OUTPUT INSTRUCTION:
Do NOT invoke any tools. Output your COMPLETE, exhaustive Section 2 markdown review directly to stdout.
Start your output directly with:
## 2. Independent Quantitative & Adverse-Selection Review (Claude Code Submission)
"""


def main():
    print(">>> Dispatching mandate to Claude Code (stdout mode)...")
    t0 = time.time()
    res = ask_claude_detailed(CLAUDE_PROMPT, timeout_sec=240)
    elapsed = time.time() - t0
    print(f"Claude Code finished in {elapsed:.1f}s | Success: {res['success']} | Output len: {len(res['output'])}")
    out_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "shared", "track1_esm", "claude_section2.md"))
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(res["output"])
    print(f"Written to {out_file}")


if __name__ == "__main__":
    main()

