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

Exact Commit to Review: 31e9deb (on branch feature/day3-quantitative-alpha-strategies)
Prior Review Commit: 4b029de (CHANGES_REQUIRED - Round 2)
Base Branch Commit: dd558f2 (main tip)

Mandate:
Perform formal Round 3 peer review and acceptance gate evaluation on Sprint Day 3 remediations addressing your Round 2 findings:

1. Finding 2 Remediation (Strict Bar Chronology & Timing Validation):
   - Implemented centralized `BaseSwingStrategy.validate_historical_bars()` called by all three strategies:
     * Every single bar requires a non-empty, valid ISO calendar date string (parsed via `strptime`).
     * Bar dates must be strictly increasing and unique: `cur_dt <= prev_dt` immediately rejected (catches duplicate dates, unordered bars, and date reversals).
     * Rejects any bar in the future relative to `session_date` (`raw_date > session_date`).
     * Mandatory final bar date matching `session_date` (`last_date == session_date`).
   - Implemented centralized `BaseSwingStrategy.validate_timing_context()`:
     * Catches malformed `next_session` (e.g. "2026-99-99") gracefully and returns `None` (0 signals, no unhandled ValueError).
     * Enforces `session_date < next_session <= session_date + 10 days`, rejecting far-future entry dates (e.g. "2099-01-01").

2. Finding 4 Remediation (Mandatory Immutable Trace Contract & Exit Validation):
   - Implemented `_deep_freeze()`: recursively converts mappings to `types.MappingProxyType`, sequences to `tuple`, sets to `frozenset`.
     * In-place mutation attempt at any nesting level (e.g. `event.trace["sub"]["val"] = 2`) raises `TypeError: 'mappingproxy' object does not support item assignment`.
     * External mutation of the dictionary passed to `trace` does not affect the event (deep copied and frozen).
   - In `ExitSignalEvent`:
     * Strictly validates calendar dates via `datetime.strptime(self.session_date, "%Y-%m-%d")`, rejecting impossible dates like "2026-02-31".
     * Mandates non-empty mapping trace (`trace={}` raises `ValueError`).
     * Applies `_deep_freeze()` to trace.

3. Finding 5 Remediation (Spec Integrity & Locked Manifest Enforcement):
   - In `BaseSwingStrategy.load_spec()`:
     * Cryptographic manifest `SPEC_MANIFEST.sha256` is strictly REQUIRED (`FileNotFoundError` raised if absent).
     * Hash verification cannot be bypassed on canonical paths.
   - All three strategy classes default to their pre-registered YAML specs in `shared/track2_liquid/strategies/specs/` upon default instantiation `Strategy()`.
   - In `BaseSwingStrategy.__init__()` and subclass `_validate_config()`:
     * Config-only construction without spec requires explicit `allow_unreviewed_overrides=True` (raises `ValueError` otherwise).
     * Unreviewed overrides over pre-registered specs require explicit `allow_unreviewed_overrides=True` (raises `ValueError` otherwise).
     * `lookback_days_52w < 252` strictly rejected fail-closed unless `allow_unreviewed_overrides=True`.

4. Regression Suite Expansion:
   - Expanded `tests/test_day3_strategies.py` with full 260-bar positive control fixtures (producing exactly 1 signal), varying one invalid input at a time:
     * Missing intermediate bar dates -> 0 signals.
     * Duplicate session dates -> 0 signals.
     * Decreasing / non-chronological bar dates -> 0 signals.
     * Future bar dates -> 0 signals.
     * Stale / mismatched final bar date -> 0 signals.
     * Far-future entry session ("2099-01-01") -> 0 signals.
     * Malformed next_session ("2026-99-99") -> 0 signals without exception.
     * Exit event invalid date ("2026-02-31") -> ValueError.
     * Exit event empty trace -> ValueError.
     * Nested trace in-place mutation -> TypeError.
     * External mutation leakage -> prevented.
     * Missing manifest -> FileNotFoundError.
     * Config-only without override -> ValueError.
     * Cross-sleeve bar validation tests on Sleeves A and C.

Empirical Evidence:
- Complete test suite: 27 unit tests in `tests/test_day3_strategies.py` + 45 in `tests/test_execution_risk_governor.py` + 12 in `tests/test_day1_data_contracts.py` (total 84 passed in 0.31s, exit code 0).
- Reproduction command: `.venv\\Scripts\\python.exe -m pytest tests/test_day1_data_contracts.py tests/test_execution_risk_governor.py tests/test_day3_strategies.py -v`
- Hash-sealed log: `shared/trust/artifacts/DAY3-ALPHA-STRATEGIES-TESTS.log`
- Log SHA-256: `D4D7099109F7AF33A0E3C1A21242057AA1B1E6C82CC7E9A6E23349D780241D50` (sealed in `DAY3-ALPHA-STRATEGIES-TESTS.log.sha256`)

Please inspect commit 31e9deb and provide your formal independent review verdict (APPROVED or CHANGES_REQUIRED).
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
