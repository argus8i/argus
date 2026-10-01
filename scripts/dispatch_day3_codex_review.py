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

Exact Commit to Review: 468b89c (on branch feature/day3-quantitative-alpha-strategies)
Prior Review Commit: 42283b6 (CHANGES_REQUIRED - Round 5)
Base Branch Commit: dd558f2 (main tip)

Mandate:
Perform formal Round 6 peer review and acceptance gate evaluation on Sprint Day 3 remediations addressing your Round 5 findings:

1. Overridden Scalar Conversion Remediation:
   - In `_deep_freeze()`:
     * Used descriptor-level C-slot extraction: `str.__str__(obj)`, `int.__int__(obj)`, `float.__float__(obj)`, and `bytes.__bytes__(obj)`.
     * If a subclass overrides `__str__()` to return `self`, `str.__str__(obj)` invokes the built-in C slot directly, bypassing the subclass method and extracting a pure, detached built-in `str` (`type is str`, `res is not obj`, stripped of all subclass attributes). A fallback `"".join([chr(c) for c in bytes(obj.encode("utf-8"))])` ensures bulletproof primitive isolation.
   - Verified via `test_overridden_str_conversion_strips_alias`.

2. Detached Immutable Timezone & Semantics Preservation:
   - In `_deep_freeze()`:
     * When `obj.tzinfo` is present: evaluates `offset = obj.utcoffset()` and builds a standard immutable `timezone(offset, name=tz_name)` into built-in `datetime.timezone`, completely detaching the datetime from any mutable custom timezone instance.
     * Preserves `fold` semantics (`fold=getattr(obj, "fold", 0)`), ensuring `fold=1` is preserved rather than dropped to 0.
   - Verified via `test_datetime_timezone_is_detached`.

3. Test-First Acceptance Gate Probes & Artifacts:
   - Failing pre-fix probe reproduction: recorded in `shared/trust/artifacts/DAY3-ROUND5-FAILING-PROBES.log` (exit code 1, 2 failed) with SHA-256 seal `70B59C12B029F415441E7455F89F6FEDC7D72CBE7AAADDD7F20004529E3194C6`.
   - Post-fix full test suite: 34 unit tests in `tests/test_day3_strategies.py` + 45 in `tests/test_execution_risk_governor.py` + 12 in `tests/test_day1_data_contracts.py` (total 91 passed in 0.29s, exit code 0).
   - Reproduction command: `.venv\\Scripts\\python.exe -m pytest tests/test_day1_data_contracts.py tests/test_execution_risk_governor.py tests/test_day3_strategies.py -v`
   - Hash-sealed log: `shared/trust/artifacts/DAY3-ALPHA-STRATEGIES-TESTS.log` (9,027 bytes)
   - Log SHA-256: `ABA4882289164022C483CB5F8B96AF08B7C22DBC8CCBC9219F91D319D28A1C0F` (sealed in `DAY3-ALPHA-STRATEGIES-TESTS.log.sha256`)

Please inspect commit 468b89c and provide your formal independent review verdict (APPROVED or CHANGES_REQUIRED).
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
