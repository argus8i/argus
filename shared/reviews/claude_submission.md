I have enough to write a rigorous review. I have the source file, the band data, and confirmed `calculate_max_safe_position_by_10day_lc` takes no `band_pct` parameter at all (line 74-78) — it hardcodes `RULE_5_TEN_DAY_LC_DIVISOR` — while `calculate_position_size` accepts `circuit_band_pct` but silently drops it (never passed to the delegate call at lines 211-215 or 224-228).

# CLAUDE RED-TEAM SUBMISSION — TASK_RULE5_BAND_AWARE_DIVISOR

## Q1 — Arithmetic direction and horizon validity

Arithmetic confirmed correct: `1-(1-p)^10` is monotonically increasing in `p`, so a wider band produces a larger cumulative-loss fraction, meaning the constant 0.401 (band=5%) **understates** loss and **oversizes** position for every band >5%. Table reproduced and verified:

| band | 1-(1-p)^10 | ratio to 0.401 |
|---|---|---|
| 2% | 0.18293 | 0.456x |
| 5% | 0.40126 | 1.000x |
| 10% | 0.65132 | 1.624x |
| 20% | 0.89263 | 2.226x |

Rs 5,000 tolerance example: `5000/0.401 = 12,469` rupees permitted; loss at 20% band on that exposure = `12,469 × 0.8926 = 11,130`. Confirmed exactly as claimed.

**Is 10 sessions still the right horizon at 20%?** No — and this is the deeper flaw, not a footnote. At `p=0.20`, `1-(1-p)^10 = 0.8926`: the position is modeled as losing 89.26% of value and the formula still treats the remaining 10.74% as recoverable capital. This is a **first-order model breakdown**, not just a sizing error:

- The formula assumes the position *survives* to session 11 with `(1-p)^10` of its value intact and can then be exited. At 20% band, 10 consecutive LCs is not a tail event to size against — it is a near-total-wipe scenario where the exit itself is the unverified assumption (see Q3/Rule 5 mechanism below).
- Compounding decay is convex in the wrong direction for risk budgeting: going from 9 to 10 consecutive session at 20% band moves loss from 86.6% → 89.3%, a shrinking marginal increment on an already-destroyed base. The choice of exactly 10 sessions as horizon is calibration for the 5% case (BSE's own periodic-band-review cadence for T-group ESM names is loosely time-boxed around two weeks), not a physically justified cutoff at 20%. **UNVERIFIABLE (requires the actual BSE/NSE circuit-filter review cadence for 20%-band, non-ESM Group B names like MOBIKWIK/LOVABLE/ANLON/VEDAVAAG)** — Group B stocks with `surveillance: NONE` or `UNKNOWN` are not necessarily on the same periodic review track as ESM Stage-1/2 T-group names, so "10 sessions" may not even be the right stopping rule for those four names.

**P0 OBJECTION**: proposing `ten_day_lc_divisor(20%) = 0.8926` as *the* correct band-aware fix, without separately flagging that any single-name position sized against an 89.26% single-scenario tail is arguably un-tradeable regardless of divisor correctness, is incomplete. Fixing the divisor makes the sizing *arithmetically honest*, not *safe*. A budget of Rs 5,000 still implies willingly holding a position worth Rs 5,601 (`5000/0.8926`) into a scenario that, if realized, leaves Rs 601 of exit value — assuming an exit exists at all.

## Q2 — Missing band_pct at sizing time: fail-closed or default-to-widest?

**Fail closed. Mirror Rule 9's existing pattern exactly** (`risk_calculator.py:145-160`, `INVALID_DAILY_VOLUME`).

Reasoning, mathematically: defaulting to 20% is *not* conservative in the direction that matters for capital preservation in the way it looks. Two failure modes:

1. If the true band is 2% (CHANDRIMA) and the code defaults to 20%, `max_position_rupees = tolerance/0.8926` **under-sizes** by 4.88x relative to the correct `tolerance/0.1829`. That looks "safe" but is a silent Rule-9-style distortion — capital is misallocated conservatively for the wrong reason, which corrupts portfolio-level capital-allocation math elsewhere (e.g., `portfolio_allocation_pct` in `calculate_position_size` line 219/231) and produces a systematically wrong `worst_case_loss` figure fed into any downstream aggregation.
2. If the true band is unknown due to a stale/failed feed (band data comes from `shared/bse_daily_bands.json`, a snapshot file with a `timestamp` field — line 3 shows `"timestamp": "2026-09-17 10:20:09"`), defaulting to *any* fixed value silently proceeds on a **stale-feed assumption never validated at call time**. This is structurally identical to the "one authoritative feed-validity gate" work already done for `live_depth` consumers per the recent commit `4e05cb3`. Band data deserves the same treatment: no band_pct in hand ⇒ refuse to size, full stop, `constrained_by: "INVALID_BAND_PCT"`.

Defaulting to widest band is only defensible if the caller has *no* band information source at all and the position must ship regardless — that is not this codebase's situation; `shared/bse_daily_bands.json` exists precisely to prevent that default from ever being needed.

## Q3 — Do intraday/dynamic band changes break a divisor fixed at entry?

Yes, and this is a **live P0**, not a hypothetical:

- ANLON and VEDAVAAG in the current snapshot carry `"surveillance": "UNKNOWN"`, `"raw_surveillance": "ASM ST : Stage 1"`, and `"validation": {"record_valid": false, "anomalies": ["SURVEILLANCE_UNKNOWN"]}`. These are **flagged-invalid band records already in the shared file**, yet they still carry a numeric `band_pct: 20.0`. A band-aware divisor computed from this record produces a false sense of precision on data the file itself has marked unreliable. Any wiring of `ten_day_lc_divisor()` MUST reject records where `validation.record_valid == false`, not just missing `band_pct`.
- NSE/BSE mechanism, to the extent checkable from public exchange circular practice: ASM (Additional Surveillance Measure) and ESM (Enhanced Surveillance Measure) stage transitions can change applicable price bands intraday-to-next-session as a scrip is moved into/out of a surveillance stage, and periodic band review (typically bi-weekly for shortlisted scrips) can also revise it. **UNVERIFIABLE (requires live NSE/BSE circular text and confirmation of the exact review cadence in effect 2026-09-17, plus same-day confirmation whether a mid-session band change is possible or only effective from next session)** — I can state the general exchange mechanism exists from known market structure, but cannot certify the exact current-session applicability without a live circular fetch, which is out of scope for a static code review.
- Consequence for a divisor "fixed at entry": if a position is sized at entry using `band_pct=5%` (divisor 0.401) and the scrip is later moved to a wider ASM band mid-hold, the position's true worst-case tail is now larger than what was underwritten at entry — the position was correctly sized for a regime that no longer applies. **The fix must re-evaluate `band_pct` and re-check against the live `max_safe_position_rupees` on every session mark, not only at entry**, or explicitly document that Rule 5 sizing is an entry-only snapshot with no obligation to defend against post-entry band widening (a materially weaker guarantee that must be stated to whoever consumes `calculated_worst_case_10d_loss`).

## Q4 — Is the 2%-band conservatism (CHANDRIMA) acceptable to leave?

No — accepting it "because it's conservative" is a category error. Under-sizing at 2% by `0.401/0.1829 = 2.19x` is not free:

- It caps upside capital deployment on the *only* name in the Track 1 universe with a tight band (i.e., arguably the *safer* name from a tail-loss perspective), while the same flawed constant *oversizes* the 20%-band names by up to 2.23x. The net effect of leaving 0.401 as a universal constant is a portfolio that is **systematically overweight the riskiest names and underweight the safest name in the same universe** — this is a diversification/allocation defect, not merely an efficiency loss.
- Practically, CHANDRIMA is already liquidity-gated in the codebase's own demonstration (`risk_calculator.py:324-334`, `chandrima_size['constrained_by'] == "LIQUIDITY_GATE_RULE_9"`) — Rule 9's `daily_volume=6355` binds before Rule 5 capital sizing does. So the 2%-under-sizing defect is currently masked by the liquidity constraint for CHANDRIMA specifically, but that is incidental, not structural: any other 2%-band name added to the universe without a comparably thin float would expose the under-sizing directly. **Fix it as a first-class defect, not a rounding note.**

## Q5 — Call-site breakage risk from making the divisor band-dependent

Two concrete defects found by reading the call sites directly, not the six-site count claimed in the mandate:

1. **`calculate_max_safe_position_by_10day_lc` (line 74-78) has no `band_pct` parameter at all.** Wiring `ten_day_lc_divisor()` into this function requires a **signature change**, which breaks every positional-argument caller. Grep shows callers in `antigravity/analysis/audit_all_track1_files.py` invoking it positionally: `calculate_max_safe_position_by_10day_lc(5000, 9.99, 50000)` (3-arg positional). Adding a required 4th positional `band_pct` argument shifts nothing (kwarg-safe) *only if* added as a keyword-only param with an explicit non-silent default-refusal (per Q2) — but if added positionally, any call site currently passing exactly 3 positional args continues to compile and silently gets whatever default is chosen. **This is exactly the silent-misdefault failure mode from Q2, now at the API-contract level**: a careless wiring makes old call sites keep running with a phantom default band instead of erroring.
2. **`calculate_position_size` (line 194-232) already accepts `circuit_band_pct: Optional[float] = 5.0` as a parameter (line 204) but never uses it.** It is dead-on-arrival: neither delegate call (`cls.calculate_max_safe_position_by_10day_lc(...)` at lines 211-215 and 224-228) passes `circuit_band_pct` through. This means **any caller today who believes they are already supplying a band-aware call is being silently ignored and defaulted to the flat 0.401** — this is not a future risk, it is a **present, live defect** independent of the proposed change. It must be fixed as part of this same patch or explicitly called out as a separate immediately-actionable bug, since it means the "six call sites" may already include callers under the false impression that band-awareness is live.

Recommend: rename/version the function (e.g. add `calculate_max_safe_position_by_10day_lc_v2(..., band_pct)` required keyword-only) rather than silently overloading the existing 3-arg signature, and audit every call site (not just grep for the function name — also grep for `circuit_band_pct=` usage) to confirm none is currently passing a band value into the dead parameter under the belief it's honored.

## Rule 11 cross-cut check

Track 1 (ESM micro-caps) and Track 2 (liquid F&O) sizing must never share assumptions. This divisor fix is scoped to Track 1's LC-lockout scenario (Rule 5 exists because Track 1 names can freeze at zero bid depth for consecutive sessions — a Track 2 liquid F&O name under circuit filters behaves completely differently, with continuous two-sided quotes resuming same-day in the overwhelming majority of cases). Confirm the wiring change touches only Track 1 consumers (`accumulation_screener.py`, Track 1 daemons) and does **not** get imported into `liquid_momentum_screener.py` / `track2_live_radar.py` sizing paths, where a 10-consecutive-LC assumption is not the relevant tail risk at all. **UNVERIFIABLE from static read alone whether any Track 2 file imports `CircuitRiskCalculator`** — grep showed `liquid_momentum_screener.py` matched the search pattern; this must be checked line-by-line before merge, since importing Track 1's LC-lockout model into Track 2 sizing would itself be a Rule 11 violation regardless of divisor correctness.

## Verdict

**BLOCKED (P0: `calculate_max_safe_position_by_10day_lc` has no `band_pct` parameter to wire the fix into — requires a signature change with fail-closed missing-band handling per Q2, not a drop-in constant swap; additionally `circuit_band_pct` is already dead/unused in `calculate_position_size`, a live defect independent of this proposal; and ASM/ESM `record_valid: false` band records in `shared/bse_daily_bands.json` must be rejected, not sized against, before this wiring ships)**