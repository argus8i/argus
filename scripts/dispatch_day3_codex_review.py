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

Exact Commit to Review: 4b029de (on branch feature/day3-quantitative-alpha-strategies)
Prior Review Commit: fcc6d37 (CHANGES_REQUIRED)
Base Branch Commit: dd558f2 (main tip)

Mandate:
Perform formal Round 2 peer review and acceptance gate evaluation on Sprint Day 3 remediations addressing your 5 Round 1 findings:

1. Finding 1 Remediation (Readiness Audit Data Scoping):
   - Corrected `shared/track2_liquid/strategies/readiness_audit.md` with exact archive distribution:
     MTO archives: 2005 (251), 2006 (250), 2010 (19), 2016 (1), 2021 (1), 2026 (4) = 526 files total.
   - Formally documented that Sleeve A backtesting is scoped strictly to sessions with verified MTO join coverage, not unconstrained 2021-2026.

2. Finding 2 Remediation (Strict Typed Metadata & Timing Inputs):
   - Enforced strict boolean/string identity checks (`is True`, `is False`, `series == "EQ"`), preventing truthy `"False"`, `None`, and missing key leakage.
   - Enforced regex and calendar `strptime` validation for `session_date` and `entry_session`.
   - Enforced `entry_session > session_date` invariant (Rule 4 T+1 discrete execution). Missing or non-T+1 `next_session` context returns zero signals.
   - Enforced chronological, strictly non-future bar ordering (`b_date <= session_date`, `bars[-1].date == session_date`).

3. Finding 3 Remediation (Exit Precedence & Adverse Selection):
   - Opening gap-down below stop loss exits at open price with reason `STOP_LOSS`.
   - Ambiguous intrabar price action (where both low <= stop and high >= target) exits conservatively at `STOP_LOSS` first (adverse selection invariant), eliminating optimistic target-first bias.

4. Finding 4 Remediation (Immutable Mandatory Trace Contract):
   - `SignalEvent.trace` and `ExitSignalEvent.trace` wrapped in `types.MappingProxyType` to enforce runtime immutability (mutations raise `TypeError`).
   - Non-empty trace mapping is strictly required at instantiation.

5. Finding 5 Remediation (Locked Definitions & Spec Integrity):
   - `High52MomentumStrategy` strictly requires full 252-bar lookback without truncation.
   - `ExpiryReliefStrategy` mandates explicit `cycle_start_price` and matches `session_date` against `nearest_fut_expiry`.
   - `BaseSwingStrategy.load_spec()` verifies SHA-256 against `SPEC_MANIFEST.sha256` and rejects unreviewed config overrides.

Empirical Evidence:
- Complete test suite: 26 unit tests in `tests/test_day3_strategies.py` (including 4 dedicated regression probes for findings 2-5) + 45 in `tests/test_execution_risk_governor.py` + 12 in `tests/test_day1_data_contracts.py` (total 83 passed in 0.26s, exit code 0).
- Reproduction command: `.venv\\Scripts\\python.exe -m pytest tests/test_day1_data_contracts.py tests/test_execution_risk_governor.py tests/test_day3_strategies.py -v`
- Hash-sealed log: `shared/trust/artifacts/DAY3-ALPHA-STRATEGIES-TESTS.log`
- Log SHA-256: `5CB66694D25AD00D705F3B572747ACC87C3C9198B8EAACBC15A05E9DA01C1C5A` (sealed in `DAY3-ALPHA-STRATEGIES-TESTS.log.sha256`)

Please inspect commit 4b029de and provide your formal independent review verdict (APPROVED or CHANGES_REQUIRED).
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
