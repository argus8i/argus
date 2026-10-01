"""
scripts/dispatch_day3_codex_review.py
=====================================
Dispatches Sprint Day 3 review request to OpenAI Codex over the Nexus Bus.
"""
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from antigravity.daemons.tri_agent_bus import ask_codex_detailed

PROMPT = """Signed Nexus review request for OpenAI Codex (Senior Systems, Execution-Reality & Reliability Engineer).

Author: Antigravity (Quantitative Modeling & Infrastructure Orchestrator)
Scope:
- shared/track2_liquid/strategies/readiness_audit.md
- shared/track2_liquid/strategies/specs/delivery_accumulation_v1.yaml
- shared/track2_liquid/strategies/specs/high52_momentum_v1.yaml
- shared/track2_liquid/strategies/specs/expiry_relief_v1.yaml
- shared/track2_liquid/strategies/specs/SPEC_MANIFEST.sha256
- antigravity/strategies/base_strategy.py
- antigravity/strategies/delivery_accumulation.py
- antigravity/strategies/high52_momentum.py
- antigravity/strategies/expiry_relief.py
- tests/test_day3_strategies.py
- scripts/run_and_record_day3_suite.py
- shared/trust/artifacts/DAY3-ALPHA-STRATEGIES-TESTS.log
- shared/trust/artifacts/DAY3-ALPHA-STRATEGIES-TESTS.log.sha256

Exact Commit to Review: 42283b6 (on branch feature/day3-quantitative-alpha-strategies)
Prior Review Commit: a3fe27a (CHANGES_REQUIRED - Round 4)
Base Branch Commit: dd558f2 (main tip)

Mandate:
Perform formal Round 5 peer review and acceptance gate evaluation on Sprint Day 3 remediations addressing your Round 4 findings:

1. Blocker 1 Remediation (Deep Trace Immutability, Exact Scalar Conversion, & Mapping Key Validation):
   - In `_deep_freeze()`:
     * Mapping keys are recursively frozen and strictly validated (`type(frozen_k) in (str, int, float, bool, bytes, date, datetime)` or `None`). Non-scalar or mutable keys (e.g. custom objects, tuples) raise `TypeError` fail-closed.
     * All scalar primitives undergo explicit exact conversion (`str(obj)`, `int(obj)`, `float(obj)`, `bool(obj)`, `bytes(obj)`, `datetime(...)`, `date(...)`) to strip subclass mutability and eliminate externally mutable aliases.
   - Verified via `test_codex_round4_blocker1_subclass_and_mapping_key_immutability`.

2. Blocker 2 Remediation (Strict Spec Identity & Track Fail-Closed Binding):
   - In `BaseSwingStrategy._validate_config()`:
     * Mandates valid identity: raises `ValueError` if neither `strategy_name` nor `strategy_id` is declared.
     * Checks EVERY supplied identity field independently against `self.strategy_id`: conflicting second identities (e.g. valid `strategy_name` but conflicting `strategy_id`) raise `ValueError`.
     * Mandates an explicitly approved track: raises `ValueError` if `track` is missing, `False`, or not in `("TRACK_2", "TRACK_2_LIQUID")`.
   - Verified via `test_codex_round4_blocker2_spec_binding_fail_closed`.

3. Test-First Acceptance Gate Probes & Artifacts:
   - Failing pre-fix probe reproduction: recorded in `shared/trust/artifacts/DAY3-ROUND4-FAILING-PROBES.log` (exit code 1, 2 failed) with SHA-256 seal `680FECB97C1FDA18D7B45B4D25392C34CF70042D97F89E58C01AD51A3733C2CB`.
   - Post-fix full test suite: 32 unit tests in `tests/test_day3_strategies.py` + 45 in `tests/test_execution_risk_governor.py` + 12 in `tests/test_day1_data_contracts.py` (total 89 passed in 0.26s, exit code 0).
   - Reproduction command: `.venv\\Scripts\\python.exe -m pytest tests/test_day1_data_contracts.py tests/test_execution_risk_governor.py tests/test_day3_strategies.py -v`
   - Hash-sealed log: `shared/trust/artifacts/DAY3-ALPHA-STRATEGIES-TESTS.log` (8,858 bytes)
   - Log SHA-256: `012B6FE02A696131CFAEB656E48CCA87218A607B5FAFB614405E5786AAD72860` (sealed in `DAY3-ALPHA-STRATEGIES-TESTS.log.sha256`)

Please inspect commit 42283b6 and provide your formal independent review verdict (APPROVED or CHANGES_REQUIRED).
"""

def main():
    print("Dispatching Sprint Day 3 review request to OpenAI Codex (timeout=300s, chat_only=True)...")
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
