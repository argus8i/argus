"""
Dispatch script to invoke OpenAI Codex for exact-commit review of 67c2d12a097e7bd15fb4980d6d3abaf495f6d219.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import time
from antigravity.daemons.tri_agent_bus import ask_codex_detailed

PROMPT = """Signed Nexus review request for OpenAI Codex (Senior Systems, Execution-Reality & Reliability Engineer).

Author: Antigravity (Quantitative Modeling & Infrastructure Orchestrator)
Scope:
- antigravity/daemons/inbox_worker.py
- antigravity/daemons/tri_agent_bus.py
- antigravity/orchestrator/status.py
- antigravity/daemons/supervised_inbox_worker.py
- antigravity/daemons/nexus_watchdog.py
- antigravity/daemons/nexus_cli.py
- scripts/*nexus*.ps1
- tests/*nexus*.py
- tests/test_tri_agent*.py

Exact commit to review: 67c2d12a097e7bd15fb4980d6d3abaf495f6d219
Parent commit: 8e42cdb05a87dbabe3d9adb09381d87c274d3e2a
Branch: fix/nexus-and-bridge-repair

Mandate:
Perform final review and acceptance gate evaluation on commit 67c2d12 (building directly upon verified commit 8e42cdb) on the ARGUS Nexus Bus & Supervisor reliability repair:

1. Verification of Code Repairs (Confirmed in 8e42cdb):
   - You previously verified by independent execution that:
     a) Final P1 is resolved (claim_message validation failure acquires FileLock and rechecks existence before outbox write).
     b) Probe 23 Parts A-D assert exact equality with durable SQLite store and Parts A-C verify recovered HMAC signatures.
     c) All Python files parsed cleanly and read-only verification exited 0.

2. Resolution of the Acceptance Gate Prerequisite:
   - Your prior review noted: "the committed pytest log contains neither the invocation nor the process exit code... APPROVED is therefore withheld for the full acceptance gate."
   - In commit 67c2d12:
     - `scripts/run_and_record_nexus_suite.py` was implemented and committed to systematically execute the suite and record the complete reproduction artifact per Rule 8 v2 Invariant 3.
     - `shared/trust/artifacts/CODEX-NEXUS-FULL-SUITE-VERIFIED.log` now contains:
       - Header: Exact invocation command, CWD, and START TIME (2026-10-01T12:02:35.249679+05:30).
       - Body: Raw unedited pytest stdout & stderr covering all 136 tests across 7 files (136 passed in 41.02s).
       - Footer: END TIME (2026-10-01T12:03:16.724876+05:30), ELAPSED SECONDS (41.48), and EXIT CODE: 0.

Please inspect commit 67c2d12 and parent 8e42cdb, verify that all acceptance criteria are satisfied, and provide your formal independent review verdict (APPROVED or CHANGES_REQUIRED) with findings and test evidence.
"""

def main():
    print("Dispatching review request to OpenAI Codex (timeout=300s, chat_only=True)...")
    t0 = time.time()
    res = ask_codex_detailed(PROMPT, timeout_sec=300, chat_only=True)
    elapsed = time.time() - t0
    print(f"Elapsed: {elapsed:.2f}s")
    print(f"Success: {res.get('success')}")
    print(f"Return code: {res.get('returncode')}")
    print(f"Error: {res.get('error')}")
    print("\n--- CODEX OUTPUT ---")
    print(res.get("output", ""))
    print("--- END OUTPUT ---\n")

if __name__ == "__main__":
    main()
