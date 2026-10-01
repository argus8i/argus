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

Exact Commit to Review: a3fe27a (on branch feature/day3-quantitative-alpha-strategies)
Prior Review Commit: 31e9deb (CHANGES_REQUIRED - Round 3)
Base Branch Commit: dd558f2 (main tip)

Mandate:
Perform formal Round 4 peer review and acceptance gate evaluation on Sprint Day 3 remediations addressing your Round 3 findings:

1. Gap 1 Remediation (Canonical Manifest Unconditional Verification):
   - In `BaseSwingStrategy.load_spec()`: Cryptographic manifest verification is mandatory and cannot be bypassed. If `SPEC_MANIFEST.sha256` is absent, `FileNotFoundError` is unconditionally raised.
   - `BaseSwingStrategy.__init__()` always invokes manifest verification when loading specification files.

2. Gap 2 Remediation (Deep Trace Leaf Immutability & Isolation):
   - In `_deep_freeze()`:
     * `bytearray` leaves are isolated and converted to immutable `bytes(obj)` copies.
     * Only whitelisted immutable scalar primitives (`int, float, str, bool, bytes, None, date, datetime`) and deeply frozen containers (`MappingProxyType, tuple, frozenset`) are accepted.
     * Any other mutable or unverified leaf type raises `TypeError` fail-closed, eliminating mutable aliasing.

3. Gap 3 Remediation (Strict Cross-Sleeve Spec Binding):
   - In `BaseSwingStrategy._validate_config()`:
     * Validates that declared `strategy_name` / `strategy_id` in spec matches the executing strategy class's `strategy_id` (`ValueError` raised on mismatch). Cross-sleeve spec loading (e.g. delivery spec into High52) is strictly rejected.
     * Enforces Track 2 isolation (`track in ("TRACK_2", "TRACK_2_LIQUID")`).
     * Subclasses call `super()._validate_config()` first.

4. Readiness Audit Boundary Corrections:
   - In `shared/track2_liquid/strategies/readiness_audit.md`:
     * Documented PIT F&O reference membership boundary: coverage begins on **2022-01-03**.
     * Documented Sleeve B (52-Week High Momentum) warm-up requirement: requires full **252 trading sessions** of historical daily bars from 2022-01-03, establishing canonical backtest signal generation starting in **January 2023** (2023-01-09).
     * Documented Sleeve C (Expiry Relief) window (2022-01-03 to 2026-09-25).

5. Test-First Regression Probes:
   - Verified failing probes prior to fixes, now passing:
     * `test_codex_round3_canonical_manifest_cannot_be_bypassed`: verifies manifest cannot be bypassed.
     * `test_codex_round3_trace_rejects_or_isolates_mutable_leaf`: verifies bytearray isolation and custom mutable rejection.
     * `test_codex_round3_cross_sleeve_spec_rejection`: verifies cross-sleeve spec mismatch rejection.

Empirical Evidence:
- Complete test suite: 30 unit tests in `tests/test_day3_strategies.py` + 45 in `tests/test_execution_risk_governor.py` + 12 in `tests/test_day1_data_contracts.py` (total 87 passed in 0.39s, exit code 0).
- Reproduction command: `.venv\\Scripts\\python.exe -m pytest tests/test_day1_data_contracts.py tests/test_execution_risk_governor.py tests/test_day3_strategies.py -v`
- Hash-sealed log: `shared/trust/artifacts/DAY3-ALPHA-STRATEGIES-TESTS.log`
- Log SHA-256: `0659401F092BEEE479F0955128B76167670834CFA048F0295FE6E8595FA72293` (sealed in `DAY3-ALPHA-STRATEGIES-TESTS.log.sha256`)

Please inspect commit a3fe27a and provide your formal independent review verdict (APPROVED or CHANGES_REQUIRED).
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
