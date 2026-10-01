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

Exact Commit to Review: 815a18c (on branch feature/day3-quantitative-alpha-strategies)
Prior Review Commit: 468b89c (CHANGES_REQUIRED - Round 6)
Base Branch Commit: dd558f2 (main tip)

Mandate:
Perform formal Round 7 peer review and acceptance gate evaluation on Sprint Day 3 remediations addressing your Round 6 findings:

1. Unicode Trace Text Preservation:
   - In `_deep_freeze()`:
     * Exact built-in strings (`type(obj) is str`) are preserved directly without transformation (`return obj`), completely avoiding byte code point corruption and guaranteeing identical preservation of Unicode characters (e.g. `\u20b9`, accented characters).
     * String subclasses (`isinstance(obj, str)` where `type(obj) is not str`) are converted via `str.encode(obj, "utf-8").decode("utf-8")`, which extracts a pure built-in `str` copy preserving exact unicode code points and stripping any subclass mutability or overridden `__str__`.
     * Exact built-in primitives (`int, float, bytes`) also preserve exact identity when `type(obj) in (...)`.
   - Verified via `test_codex_round6_unicode_trace_preservation`.

2. Timezone Name Normalization & Detachment:
   - In `_deep_freeze()`:
     * When `obj.tzinfo` is present and `raw_tz_name = obj.tzname()` is not None, the name is strictly normalized to an exact built-in string (`type(raw_tz_name) is str` or UTF-8 decode).
     * The constructed `timezone(offset, name=clean_name)` contains a pure built-in `str` name, eliminating any mutable subclass aliases reachable through `frozen.tzname()`.
   - Verified via `test_codex_round6_timezone_name_normalization`.

3. Test-First Acceptance Gate Probes & Artifacts:
   - Failing pre-fix probe reproduction: recorded in `shared/trust/artifacts/DAY3-ROUND6-FAILING-PROBES.log` (exit code 1, 2 failed) with SHA-256 seal `A4E18511FC66EBCD969779A5BC2F6E36C9E8280D843FE24F25DF1F3B83908B1E`.
   - Post-fix full test suite: 36 unit tests in `tests/test_day3_strategies.py` + 45 in `tests/test_execution_risk_governor.py` + 12 in `tests/test_day1_data_contracts.py` (total 93 passed in 0.56s, exit code 0).
   - Reproduction command: `.venv\\Scripts\\python.exe -m pytest tests/test_day1_data_contracts.py tests/test_execution_risk_governor.py tests/test_day3_strategies.py -v`
   - Hash-sealed log: `shared/trust/artifacts/DAY3-ALPHA-STRATEGIES-TESTS.log` (9,208 bytes)
   - Log SHA-256: `FBE52446A3B5A3B521614F685BDC9AD5E84F0D3797119148E77430F5B2299027` (sealed in `DAY3-ALPHA-STRATEGIES-TESTS.log.sha256`)

Please inspect commit 815a18c and provide your formal independent review verdict (APPROVED or CHANGES_REQUIRED).
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
