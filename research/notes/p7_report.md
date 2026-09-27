# P7 report: design set, freeze, holdout

**Branch:** `track2/decision-engine`
**Author:** Claude (quant lead, red team)
**Dates:** 25–26 Sep 2026, overnight under Yashu's autonomous mandate of 26 Sep
**Scope:** paper/research only. Every R figure uses `r_basis = stop_limit` unless stated otherwise.

Part A (design set and freeze) was committed **before any holdout result was read**, as Codex requested on 26 Sep. Part B (holdout) is appended after the one holdout run.

---

## Part A: design set 2022-01-03 → 2024-09-30 and the P7.3 freeze

### A.1 Data and its limits (Codex points 1–3)

| Item | Status |
|---|---|
| **Price source** | Upstox V2 1-minute candles, resampled to 15 minutes by `upstox_history.resample_15m`. Promoted to strategy-eligible under Yashu's rule: ≥ 99.5% agreement per field with Kite at max(2 ticks, 0.20%); volume reported, not gated (`p3_upstox_report.md` §7). |
| **Why Upstox and Kite differ** | Upstox 15-minute ranges are wider on 36% of bars (+1.1 bps mean), and volume differs by more than 1% on about 12% of bars. The likely cause is vendor-side minute aggregation (odd-lot and auction prints). **Sensitivity:** with every high pulled down and every low pulled up by 2 ticks, the hold-study net R moves by at most 0.003R and the selected h does not change (A.5). z* uses closes only, so it cannot change. |
| **Frozen inputs** | Sealed snapshot `p7_design_20260926`: 655 files, content SHA-256 `72a444d7…`. Every design run verified the seal before and after running. |
| **Universe** | Point-in-time F&O membership from the NSE F&O bhavcopy (stock futures listed that day), re-derived from raw files on 30 of 30 sampled days. EQ series, prev close ≥ ₹10, DTV20 ≥ ₹30 Cr, F&O ban excluded. Result: 122,309 eligible stock-days, median 170 per session. |
| **Historical members missing (Codex 1)** | Of 206 design-window members, 205 have a price series. **PEL** has none (0.53% of member-days). **HDFC, IDFC, MINDTREE, GSPL** are excluded as `WRONG_COMPANY_SERIES`: the only Upstox series for these extinguished merger ISINs belongs to the acquirer (1.52% of member-days). Coverage is about **98% of design member-days**. The excluded names were all later merged into larger companies. Their absence slightly understates any large-cap merger-arbitrage-like moves; it does not create survivorship bias toward winners. |
| **F&O ban (Codex 2)** | **Present.** NSE archive files cover all 1,232 trading days (2021-10 to 2026-09); 2,523 design stock-days are excluded. |
| **ASM/GSM (Codex 2)** | **Missing** (`SURVEILLANCE_HISTORY_MISSING`). A few stock-days under surveillance may be included. |
| **Corporate actions (Codex 2)** | **Missing.** NSE has refused this machine (HTTP 403) since 25 Sep. |
| **Announcements (Codex 2)** | Present to 2024-11-13 only. |
| **Market-cap band** | Not applied (decision 6). |
| **Consequence** | The pre-registered **RESID_REV (with news filter) cannot run.** Its rule blocks whenever corporate-action coverage is unknown. Every RESID_REV figure below is the registered fallback **RESID_REV_NF** and is labelled as such (Codex 2). |
| **VIX sizing** | INDIA VIX daily history starts 2021-11-02. The multiplier needs 120 prior closes, so it returns m = 0 (no entry) until about May 2022. This affects the portfolio simulation only; the per-signal statistics are unaffected. |

### A.2 z\* (P7.2b), trial T0028

- **z\* = 3.00.** It is the 95th percentile (raw 2.998) of the per-stock-day maximum |Z| over bar closes 10:00–13:30, taken before the volume, news and market filters.
- Sample: 107,323 stock-days, 196 symbols, 636 sessions.
- Day-block 95% CI: 2.94–3.06. By year: 2022 2.91, 2023 3.04, 2024 3.03. The Gaussian reference is 2.59, so the real scan tail is fatter than iid.
- No returns were used.
- Superseded runs: T0024–T0026 (live history; no ban history) gave 3.10 on 90 sessions and 3.00 on the full window.

### A.3 ORB_PROD (P7.2a), trial T0029: **KILLED_ON_DESIGN**

| n | Signal days | Gross R | Fee R | Slippage R | **Net R** | SE (by day) | t |
|---|---|---|---|---|---|---|---|
| 14,456 | 543 | −0.0033 | 0.0491 | 0.0079 | **−0.0603** | 0.0082 | **−7.40** |

- **The strategy has no gross edge.** The whole loss is costs: 81% fees, 13% slippage.
- 87% of trades end at the 15:05 policy exit, never touching the stop or a target.
- A rerun with the diagnostic table is bit-identical (determinism check).

**Diagnostics** (`research/outputs/p7/diagnostics.json`; descriptive only, no choice made from them):

| Cut | Bucket | n | Gross R | Fee R | Net R | t |
|---|---|---|---|---|---|---|
| Stop distance | < 0.5% | 31 | −0.004 | 0.113 | −0.135 | −1.76 |
| | 0.5–1.0% | 1,386 | −0.008 | 0.081 | −0.101 | −6.26 |
| | > 1.0% | 13,039 | −0.003 | 0.046 | −0.056 | −6.66 |
| RVOL tercile | ≤ 4.26 | 4,846 | +0.001 | 0.051 | −0.058 | −5.68 |
| | 4.26–5.89 | 4,806 | +0.001 | 0.049 | −0.056 | −5.81 |
| | > 5.89 | 4,804 | −0.012 | 0.047 | −0.067 | −6.88 |
| VIX tercile (prev close) | ≤ 12.62 | 4,895 | +0.011 | 0.053 | −0.051 | −4.11 |
| | 12.62–16.10 | 4,790 | +0.003 | 0.050 | −0.055 | −3.96 |
| | > 16.10 | 4,771 | −0.024 | 0.044 | −0.075 | −4.72 |
| Entry slot | 09:30 | 1,732 | −0.036 | 0.044 | −0.087 | −4.90 |
| | 09:45 | 1,362 | −0.025 | 0.047 | −0.079 | −4.38 |
| | 10:00+ | 11,362 | +0.004 | 0.050 | −0.054 | −6.66 |

**Reading:**
- The losses are **not** mainly friction on tight stops. The median stop is 1.76% and 90% of stops exceed 1%.
- Gross drift is about zero everywhere and negative in the opening slots and at high VIX.
- No cut makes ORB_PROD positive even before costs by more than 0.011R, so there is nothing to tune.
- Per Yashu, it is **not curve-fitted.** `promotion.kill_on_design` moved it to `KILLED_ON_DESIGN` (mean ≤ 0 and t ≤ −2; DecisionRecord in `research/decision/records/`). EXPLOIT mode gives it no slots.

### A.4 Cost versus edge for a 15-minute MIS trade

For a ₹38,000 position (the A1 slot), DhanFeeEngine gives a round trip of ₹37–40, which is **0.106% of notional** at any price. Adding 1 tick per side (0.004–0.02%) makes the **break-even favourable move 0.11–0.13%**.

When the slot cap binds (risk = notional × stop %), the gross edge a strategy needs just to pay costs is:

| Stop distance | 0.5% | 1.0% | 2.0% |
|---|---|---|---|
| Cost in R | 0.22–0.25R | 0.11–0.13R | 0.055–0.063R |

With 2 ticks per side, add 0.004–0.02% to the move.

Neither strategy tested here produces the 0.05–0.13R gross edge its stops require:
- ORB_PROD: −0.003R gross.
- RESID_REV_NF: +0.035 to +0.054R gross.

### A.5 RESID_REV_NF holding period (P7.2c), trials T0030–T0032 (one sample_id)

Setup: z\* = 3.00, unconstrained per-signal simulation, identical fill rules, 1 tick per side.

| h | n | Days | Gross R | Fee R | Slippage R | **Net R** | SE | **t** | With highs/lows pulled in 2 ticks |
|---|---|---|---|---|---|---|---|---|---|
| 4 | 220 | 131 | +0.035 | 0.081 | 0.013 | −0.059 | 0.032 | −1.83 | −0.059 (t −1.83) |
| 8 | 220 | 131 | +0.054 | 0.079 | 0.013 | −0.038 | 0.038 | −1.00 | −0.041 (t −1.07) |
| **EOD** | 219 | 130 | +0.047 | 0.074 | 0.011 | −0.038 | 0.043 | **−0.89** | −0.040 (t −0.93) |

**Event study** (first h-independent candidate per stock-day, n = 223): the residual reversion after the signal is statistically nil at every horizon.

| h (bars) | 1 | 2 | 4 | 8 | EOD |
|---|---|---|---|---|---|
| Residual reversion | +0.6 bps | −0.4 bps | −0.4 bps | +1.3 bps | +4.3 bps (t 0.58) |
| Share of the move undone | 0.4% | 0.5% | 0.7% | 1.1% | 3.3% |
| Gross from next open | +1.9 bps | +2.1 bps | +3.7 bps | +5.5 bps | +7.6 bps (t 0.83) |

**Selection:**
- **Yashu's rule** (highest day-clustered t) gives **EOD**.
- The plan's rule (highest mean net R) would give h = 8. The means differ by 0.0001R, so the choice is immaterial.
- The 2-tick wick sensitivity gives EOD as well.
- No h is clearly negative (t ≤ −2), so RESID_REV is **not** killed on design. The holdout decides, as authorized.
- Honest prior: design gives no reason to expect a holdout pass.

**Smoke and superseded runs, registered and not used for any choice:** T0033 (80 sessions), T0034 (200 sessions, portfolio code test), T0022/T0023 (Yahoo, earlier).

### A.6 Freeze (P7.3)

- `research/studies/prereg/resid_rev_v1.yaml`: status **LOCKED**; `z_star: 3.0`; `hold_bars: EOD`; `holdout_variant: RESID_REV_NF`; `data.snapshot` = the design snapshot hash. Secondaries: `slippage_2_ticks` (must stay > 0); `long_only`, `short_only`, `r_basis_trigger` and `wick_pulled_in_2_ticks` are report-only. Committed as `d549fb7`.
- `resid_rev_v1.lock`: `yaml_sha256` `eeec1a75…`, commit `d549fb7`, locked 2026-09-26 03:45 IST. Committed as `35d4311` and **pushed to origin before any holdout read** (the holdout claim, Codex point 4).
- **Holdout protocol** (`research/studies/run_holdout.py`). It refuses without a valid lock, after the done marker, on an unsealed history, on a snapshot sealed before the lock, or on uncommitted research code. It also refuses when the holdout snapshot's data files differ from the design snapshot's.
- **Holdout snapshot:** `p7_holdout_20260926` (content `6040e0cf…`, sealed 03:48, after the lock). Its bars, daily, events, bhavcopy and manifests are **byte-identical** to the design snapshot's; only the reference tables were rebuilt with the holdout visible.
- **Primary test:** day-clustered t ≥ 2.0 **and** mean net R > 0 on 2024-10-01 → 2026-07-31.

### A.7 Process defects found and fixed tonight (all mine; none reached a reported number)

1. Three runs started in the same second shared one output folder. They were stopped before writing anything; folders are now unique and never reused (`09b8a71`).
2. The run manifest recorded the code commit at the end of the run; it now records it at the start (`cf1ea65`).
3. The holdout runner would have refused its own later parts: its marker made `research/` "dirty", and it pinned HEAD. It now identifies code by the blob hashes of `research/**/*.py` (`5127d32`).
4. A `.py` commit made during the holdout run would have changed that identity. It was reverted (`c729ec7`), and the diagnostics script is re-added after finalize.

---

## Part B: the one holdout run (P7.4), 2024-10-01 → 2026-07-31 (added after finalize)

The run happened exactly once, from lock `eeec1a75…` / commit `d549fb7`, on snapshot `6040e0cf…`, with code identity `d3f0d60e…`.
- 452 sessions; 93,282 eligible stock-days.
- Special session 2025-10-21 (Muhurat) excluded by the design-set rule, plus 333 invalid stock-sessions.
- Finalized 2026-09-26 04:48 IST. The done marker is committed, and a rerun is refused (checked).
- Result files: `research/evidence/holdout_resid_rev_v1/`.

### B.1 Primary test: **FAIL → RESID_REV v1 REJECTED**

| Variant | n | Days | Gross R | Fee R | Slippage R | **Net R** | SE | **t** | Pre-registered requirement |
|---|---|---|---|---|---|---|---|---|---|
| **Primary** (1 tick, stop_limit) | 219 | 130 | **−0.036** | 0.075 | 0.012 | **−0.124** | 0.041 | **−3.03** | t ≥ 2.0 and net > 0: **FAIL** |
| 2 ticks per side | 219 | 130 | −0.036 | 0.075 | 0.024 | −0.135 | | −3.30 | net > 0: fail |
| r_basis trigger | 219 | 130 | −0.059 | 0.118 | 0.019 | −0.196 | | −3.12 | report only |
| Highs/lows pulled in 2 ticks | 219 | 130 | −0.030 | 0.075 | 0.012 | −0.118 | | −2.86 | report only |
| Long only | 93 | | | | | −0.071 | 0.069 | −1.03 | report only |
| Short only | 126 | | | | | −0.162 | 0.051 | −3.17 | report only |

- The holdout signals are genuine holdout dates: 2024-10-01 → 2026-07-20, 136 symbols, and **none** coincide with the 219 design signals. The equal counts (219 on 130 days) are a coincidence, checked.
- Out of sample, RESID_REV_NF is **negative before costs**: gross −0.036R, against +0.047R on design. The design gross edge did not survive.
- `promotion.apply_holdout` moved the register from UNVERIFIED to **REJECTED** (DecisionRecord `RESID_REV_*_HOLDOUT_REJECTED.json`). Version 1 is finished; any new idea needs a new version, a new pre-registration and new data.

### B.2 ORB_PROD on the holdout (comparison only; already KILLED_ON_DESIGN)

| n | Days | Gross R | Fee R | Slippage R | Net R | t |
|---|---|---|---|---|---|---|
| 12,331 | 375 | +0.013 | 0.048 | 0.008 | −0.042 | −4.61 |

The same picture as on design: gross edge is about zero, and costs make it a steady loser.

### B.3 P7.5 portfolio simulation (Adjusted A1 book)

Setup: `ShadowRunner` with 3 slots, ₹38,000 per slot, ₹1,14,000 aggregate including pending reservations, one position per weekly cluster, two per sector, VIX multiplier.

| Run | Signals | Allocated | Filled | **Net P&L** | Mean trade R | Win rate | Max drawdown | Peak exposure | Flat at close | Cap breaches |
|---|---|---|---|---|---|---|---|---|---|---|
| RESID_REV_NF, design (EOD) | 221 | 181 | 179 | **−₹3,437** | −0.038 | 48.6% | ₹5,743 | ₹1,13,090 | 131/131 | 0 |
| RESID_REV_NF, holdout (primary) | 220 | 205 | 205 | **−₹9,331** | −0.101 | 44.9% | ₹10,402 | ₹1,13,660 | 130/130 | 0 |
| ORB_PROD, holdout | 12,355 | 1,248 | 1,246 | **−₹50,326** | −0.048 | 44.7% | ₹57,426 | ₹1,13,898 | 375/375 | 0 |

Notes:
- **Design drops:** 27 blocked by the short early VIX history (m = 0), 9 by max slots, 2 by cluster limit.
- **Holdout drops, RESID_REV:** 10 by max slots, 4 by cluster limit, 1 by size.
- **Holdout drops, ORB_PROD:** 10,883 by max slots, 162 by size, 42 by cluster limit, 20 by sector limit.
- **Defect (mine):** in the `slippage_2_ticks` and `r_basis_trigger` holdout parts, the portfolio simulation ran with the default 1-tick / stop-limit costs, so their rupee P&L repeats the primary's. The per-signal secondaries above use the right settings. The portfolio is not part of the decision. It is noted here, not re-run.

### B.4 The other production strategies on the design set (production defaults, one configuration each)

| Strategy | n | Days | Gross R | Fee R | Slippage R | Net R | t | Register |
|---|---|---|---|---|---|---|---|---|
| COMPASS | 19,943 | 658 | +0.016 | 0.071 | 0.012 | −0.067 | −12.09 | KILLED_ON_DESIGN |
| LAST_LIGHT | 2,695 | 566 | +0.016 | 0.080 | 0.013 | −0.076 | −6.28 | KILLED_ON_DESIGN |
| TRAPDOOR | 10,182 | 661 | +0.006 | 0.083 | 0.013 | −0.090 | −15.90 | KILLED_ON_DESIGN |
| VOL_SQUEEZE | 4,668 | 616 | −0.011 | 0.057 | 0.009 | −0.077 | −7.70 | KILLED_ON_DESIGN |
| RECOIL | 4,589 | 647 | −0.045 | 0.088 | 0.014 | −0.147 | −13.86 | KILLED_ON_DESIGN |

- The adapters are `research/strategies/legacy.py`: the production classes, unmodified, with point-in-time inputs.
- The shared research fill model applies: entry at the next bar open with the clamp, the production stop used as an SL-limit, the production targets, the 15:05 policy exit, MIS fees.
- The production entry-limit prices are not used. That favours fills, so real results would be no better.
- **No strategy has a positive net edge.** The best gross edge (+0.016R) is about a fifth of the ~0.08R that costs take.
- All five meet the kill rule Yashu set for ORB_PROD (mean ≤ 0 and t ≤ −2 on the sealed design set); `promotion.kill_on_design` recorded each one.
- VWAP_RECLAIM has no adapter here. Its earlier probe was negative at every threshold (T0004–T0006); it stays UNVERIFIED.

### B.5 Late data finding: Upstox history is back-adjusted for splits and bonuses

- Checked against the NSE CM bhavcopy (raw traded closes), 83 of 298 stocks agree on under 99% of days, and the ratios are exact split/bonus factors (median 0.5).
- **21.7% of design** and **5.4% of holdout** eligible stock-days sit on adjusted price levels.
- Intraday returns and R multiples are scale-free within a day, so the results above stand. Price-level details before an adjustment (₹10 floor, tick grid, share counts behind fees) are slightly off.
- The 57 design RESID_REV signals on adjusted days averaged −0.093R against −0.019R for the rest. That difference is not significant (SE about 0.08).
- **Fix before any v2 study:** rescale Upstox bars to bhavcopy price levels (P9 §4).

### B.6 Conclusion

**None of the seven Track 2 strategies tested has a positive net edge as a 15-minute cash MIS strategy:**
- RESID_REV v1 was **REJECTED** on the holdout (t −3.03).
- ORB_PROD and the five legacy strategies were **KILLED_ON_DESIGN** (t −6 to −16).

The common cause is structural. A ₹38,000 MIS round trip costs about 0.11–0.13% of price. With stops of 1–2%, that is 0.06–0.13R per trade, and no signal tested earns a gross edge that large.

What could change this:
- a signal with much larger per-trade expected moves (catalyst or event driven);
- cheaper execution (maker fills, lower-fee instruments);
- holding periods long enough for the edge to exceed the fixed round-trip cost.

Each is a new pre-registration on new data (plan P9), not a re-fit of these.
