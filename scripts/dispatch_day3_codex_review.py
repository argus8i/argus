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

Exact Commit to Review: fcc6d37 (on branch feature/day3-quantitative-alpha-strategies)
Parent Commit: 70db0aa (pre-registration commit)
Base Branch Commit: dd558f2 (main tip)

Mandate:
Perform formal peer review and acceptance gate evaluation on Sprint Day 3 deliverables:

1. Input Readiness Gate Audit:
   - Audit point-in-time historical data availability documented in `shared/track2_liquid/strategies/readiness_audit.md`.
   - Verified on disk: 3,037,926 CM Bhavcopy rows (2021-2026), 248,779 FO Bhavcopy rows, 231,595 PIT F&O reference records with exact `nearest_fut_expiry`, and 526 MTO delivery archives.
   - Formally deferred Sleeve D (PEAD Drift) to research sandbox due to lack of point-in-time analyst consensus earnings estimates (per CODEX-PEAD-E536AEC). Sleeves A, B, and C are 100% verified.

2. Pre-Registration Verification (Rule 8 Invariant):
   - Strategy specifications locked in YAML under `shared/track2_liquid/strategies/specs/`:
     * `delivery_accumulation_v1.yaml`: c155f88ca1728398b7334a3572bd39835f23859549d62016006054318a53b00c
     * `high52_momentum_v1.yaml`: 37e3517068d7c7958ec7e1cb3f0984744ca3547224ac950ffa354899c40de014
     * `expiry_relief_v1.yaml`: 67d94ae558d77c27d33dcdc02183c35f9712c3b1f32de7827458fd7590780b06
   - Pre-registered and committed at commit 70db0aa prior to implementation.

3. Core Modular Implementation & Rule Invariants:
   - `BaseSwingStrategy`, `SignalEvent`, and `ExitSignalEvent` implement frozen immutable contracts with mandatory calculation traces.
   - AGENTS.md Rule 2 Price Floor (Rs 10.00) strictly enforced at the event constructor level.
   - Stop-loss and target price validation enforces stop < reference_price < target with finite positive floats.
   - Mathematical indicators (TR, ATR, SMA, EMA, RSI) implemented with fail-closed missing data handling.
   - Sleeve A (`DeliveryAccumulationStrategy`): DTV >= 30 Cr, 20d delivery expansion >= 2.0x, 5d range compression <= 0.75x ATR, 5d high breakout with volume >= 1.5x. Stop 1.5 ATR, target 3.0 ATR, max 7 sessions, trailing 5 EMA.
   - Sleeve B (`High52MomentumStrategy`): Within 3% of 52w high, 20d vol >= 50d vol, close >= 50 EMA, 20d high breakout. Stop 2.0 ATR, target 4.0 ATR, max 10 sessions, trailing 20 EMA.
   - Sleeve C (`ExpiryReliefStrategy`): F&O monthly expiry day, monthly cycle decline >= 8.0%, RSI(14) <= 30.0. Stop = min(expiry low - 0.5 ATR, entry - 1.5 ATR), target >= +3.5% or 2R, max 5 sessions.
   - Deterministic sorting by priority score descending on simultaneous signals.

4. Empirical Test Evidence:
   - Complete test suite: 22 unit tests in `tests/test_day3_strategies.py` + 45 in `tests/test_execution_risk_governor.py` + 12 in `tests/test_day1_data_contracts.py` (total 79 passed in 0.22s, exit code 0).
   - Reproduction command: `.venv\\Scripts\\python.exe -m pytest tests/test_day1_data_contracts.py tests/test_execution_risk_governor.py tests/test_day3_strategies.py -v`
   - Hash-sealed log: `shared/trust/artifacts/DAY3-ALPHA-STRATEGIES-TESTS.log`
   - Log SHA-256: `1D8ABD25BF4856529106C081BE824F7DD73B54B32F0BFD261028D14988BE6C51` (normalized LF, sealed in `DAY3-ALPHA-STRATEGIES-TESTS.log.sha256`)

Please inspect commit fcc6d37 and provide your formal independent review verdict (APPROVED or CHANGES_REQUIRED).
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
