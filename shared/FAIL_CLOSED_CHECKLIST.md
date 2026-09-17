# Tri-Agent Fail-Closed Engineering Checklist

**Scope:** Mandatory pre-commit quality gate for all quantitative models, screeners, monitors, and daemons across **Antigravity**, **Claude**, and **ChatGPT**.  
**Repository:** `c:\Users\yashw\swing trades`  
**Created:** 12 September 2026 (Synthesized from Claude & ChatGPT Red-Team Findings)  

---

## 1. The Core Vulnerability: Happy-Path vs. Missing-Input Bias

Across three consecutive review iterations, the desk identified the identical defect pattern:
1. **Screener `NaN` Fail-Open:** `NaN < threshold` evaluated to `False`, allowing rows with missing DTV, Beta, and ATR to silently pass every gate.
2. **Hardcoded Metric Constancy:** Calculating R:R for a single happy-path stop width ($w=1.67\%$) and compiling it as a global static constant (1:1.55 / 1:2.0).
3. **Surveillance Monitor Key Absence:** Reading `.get(key, None)` and treating `None` as "clean" instead of "unverified" when the daily surveillance check never ran.

---

## 2. Mandatory 6-Point Fail-Closed Verification Checklist

Before any model, daemon, or screener is committed or claimed as passing, the authoring agent MUST test and document behavior across all six conditions:

### Item 1: Explicit Tri-State Status for Surveillance & Flags
- [ ] **Clean State:** Requires an explicit positive confirmation value (e.g. `asm_stage == 0`, `gsm_stage == 0`, `is_surveillance == False`).
- [ ] **Flagged State:** Explicit positive integer/boolean (`stage > 0` or `True`) triggers immediate rejection.
- [ ] **Unchecked / Missing State:** `None` or absent key MUST return `DISQUALIFIED_UNKNOWN` (or `DATA_INVALID`). It must NEVER default to clean.

### Item 2: Temporal Freshness & Staleness Invariant
- [ ] Every external market data snapshot, band record, or surveillance check MUST carry an ISO or `YYYY-MM-DD HH:MM:SS` timestamp (`checked_at`).
- [ ] The engine must verify that `checked_at.startswith(date_str)`. If the date is yesterday or older, the engine MUST return `DISQUALIFIED_STALE` (fail-closed).
- [ ] Stale data from weekends or after-hours must never be evaluated as live trading reality.

### Item 3: Strict IEEE 754 `NaN` & Infinity Rejection
- [ ] All numeric inputs must be explicitly validated using `math.isnan(x)` and `math.isinf(x)`.
- [ ] Note that `None in [...]` does NOT catch `NaN`.
- [ ] Note that `NaN < threshold` and `NaN > threshold` BOTH evaluate to `False`. Any one-sided guard that relies on a comparison fails open on `NaN`.

### Item 4: Boundary & Degenerate Value Rejection
- [ ] Zero values: Check behavior when `volume == 0`, `price == 0`, `atr == 0`, or `dtv == 0`. Must return `ZERO_VOLUME_NO_LIQUIDITY` or `DATA_INVALID`, never divide-by-zero or zero-risk pass.
- [ ] Inverted / Degenerate Stops: Check behavior when `stop_price >= entry_price` or `or_low >= or_high`. Must immediately reject with `REJECTED_DEGENERATE_STOP`.
- [ ] Extreme Overextension: Check behavior when price is extended $> 0.5 \times \text{ATR}$ above trigger. Must return `HOLD_REJECT_OVEREXTENDED`.

### Item 5: Small-Sample & Sub-Target Pool Handling
- [ ] When candidate universe $N$ is smaller than default targets (e.g. 4-stock Basket A vs 15-stock default pool), `min_surviving_pool` MUST dynamically default to $\min(N, 15)$.
- [ ] Relaxation logic must NEVER trigger on an intentionally segregated small basket.
- [ ] An empty surviving pool must NEVER return `relaxed=True` with an ambiguous success message.

### Item 6: Realized Payoff & Effective Exit Math
- [ ] Risk and reward must be computed strictly from **effective exits** (including order limit offsets, slippage buffers, and commission impact).
- [ ] Target prices anchored to structural levels must not be assumed to yield constant R:R ratios across varying stop widths.
- [ ] Sizing caps (e.g. ₹1,00,000 max notional) must bind strictly on $\text{Shares} \times \text{Entry Price}$.

---

## 3. Negative Adversarial Test Requirement

Self-tests that only execute valid, clean, complete inputs are **prohibited** as proof of readiness.  
Every test suite must include synthetic adversary dictionaries:
```python
test_adversary_cases = [
    {"symbol": "MISSING_KEYS_STOCK", "is_fno_underlying": True},  # Missing all surveillance keys
    {"symbol": "NAN_METRICS_STOCK", "dtv_cr": float("nan")},      # NaN poisoned floats
    {"symbol": "STALE_RECORD_STOCK", "checked_at": "2026-09-01"},  # Expired timestamp
    {"symbol": "DEGENERATE_STOP_STOCK", "entry": 100, "stop": 105}, # Inverted risk
]
```
All adversarial cases must be asserted to fail closed with specific error codes before any code is marked complete.

