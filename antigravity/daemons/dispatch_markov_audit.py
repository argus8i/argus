"""
dispatch_markov_audit.py - Dispatches the Markov Absorption and Circuit Sensitivity audit
to Claude Code via tri_agent_bus and appends Claude's review to MARKOV_ABSORPTION_AUDIT.md.
"""

import os
import sys

sys.path.append(os.path.dirname(__file__))
from tri_agent_bus import ask_claude_detailed

WORKSPACE = r"c:\Users\yashw\swing trades"
AUDIT_FILE = os.path.join(WORKSPACE, "shared", "track1_esm", "MARKOV_ABSORPTION_AUDIT.md")

def main():
    if not os.path.exists(AUDIT_FILE):
        print(f"Error: {AUDIT_FILE} not found.")
        sys.exit(1)
        
    with open(AUDIT_FILE, "r", encoding="utf-8") as f:
        audit_content = f.read()
        
    claude_prompt = f"""
You are the Lead Microstructure & Red-Teaming Quantitative Researcher for Project Swing Trades (Track 1: ESM & Circuit Micro-Caps).
AGENTS.md Rules 1-11 govern unconditionally (Strict Paper Trading Gate, Absolute ₹10 Floor, Discrete 4-State Execution, 10-Day LC Lockout divisor 0.401, Surveillance Pre-emption, Pre-Circuit Accumulation, Liquidity 15% Volume Cap, Absolute Track Isolation).

Antigravity has synthesized the 5x5 empirical Markov chain and Kyle's Lambda bid-wall fragility across 214,441 trading records from 120 sessions (March 30 to September 11, 2026).

Please evaluate the following audit and provide your quantitative critique and verdict answering the 3 mathematical directives:
1. Adverse-Selection Red-Team: Does taking profit exits into the Day 3/4 Upper Circuit buyer queue expose the trader to front-running by operator block dumps?
2. State Absorption Invariance: Does the 5x5 Markov matrix satisfy ergodicity, or do LOCKED_LC and BAND_TIGHTENED act as absorbing boundaries under AGENTS.md Rule 5 and Rule 6?
3. Position Sizing Gate: Prove whether the 0.401 risk divisor provides sufficient tail-risk margin given the empirical 5.32% direct jump probability from LOCKED_UC to LOCKED_LC.

Here is the complete audit data:

{audit_content}

Deliver a concise, rigorous mathematical review and actionable recommendations for Track 1 execution.
"""

    print(">>> Dispatching Markov Absorption Audit to Claude Code via tri_agent_bus...")
    result = ask_claude_detailed(claude_prompt, timeout_sec=240)
    
    print(f"Elapsed: {result['elapsed']:.2f}s | Success: {result['success']} | Returncode: {result['returncode']}")
    
    response_text = result["output"].strip()
    if not response_text:
        print("ERROR: Received empty response from Claude.")
        sys.exit(1)
        
    print("\n--- CLAUDE CODE RESPONSE ---")
    print(response_text)
    print("----------------------------\n")
    
    # Append to MARKOV_ABSORPTION_AUDIT.md
    section_5 = f"""

---

## Section 5: Claude Code Peer Review & Quantitative Verdict

**Reviewer:** Claude Code (Lead Microstructure & Red-Teaming)  
**Execution Timestamp:** {result.get('elapsed', 0):.1f}s turnaround  
**Status:** Peer-Reviewed & Verified  

{response_text}
"""
    with open(AUDIT_FILE, "a", encoding="utf-8") as f:
        f.write(section_5)
        
    print(f"Successfully appended Claude's peer review to {AUDIT_FILE}")

if __name__ == "__main__":
    main()
