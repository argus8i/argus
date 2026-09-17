# Claude — Progress Log

**Role:** analyst / red team. Mechanics, post-mortems, risk, rule-writing, attacking the plan.
Append-only. Newest at the top.

---

## 2026-09-12 — Claude (Rule 9 caller-safeguard condition: CLOSED)

**Did:** Re-read `antigravity/models/liquidity_gate.py` lines 55-90 line-by-line against my 2026-09-12 "ACCEPT WITH CONDITIONS" verdict (which required a call-site safeguard, not just docstring framing, so no caller could treat a calm-market Rule 9 PASS as exit safety during a circuit event). Also ran both embedded test suites directly rather than trusting the "18/18" and "100%" claims in prose.

**Verified in code:**
- `evaluate_liquidity_gate` now takes `is_locked_circuit: bool = False` and short-circuits to a hard fail (lines 70-78) before any participation math runs, with reason text explicitly deferring to Rule 5 for exit safety.
- Independently, `avg_20d_volume` collapse check (lines 90-99): if `daily_volume < 0.10 * avg_20d_volume`, disqualifies with "Severe volume evaporation" — catches liquidity freeze even without an explicit circuit-lock flag from the caller.
- Ran `python antigravity/models/circuit_rules.py`: **18/18 tests passed**, including decoupled `BrokerOrderState` vs `ExecutionState` tests (Test 15-17) and the Rule 9 gate test (Test 10) correctly returning `CRITICAL_AVOID`.
- Ran `python antigravity/models/liquidity_gate.py`: circuit-lock and volume-evaporation safeguard tests both pass, plus the 4 baseline participation tests (3 PASS, 1 correctly FAIL on CHANDRIMA 70.8% participation).

**Formal verdict: Rule 9 condition SATISFIED — CLOSED.** The gap I flagged (framing fixed the docstring but not the function contract) is gone: a caller can no longer construct a scenario where `evaluate_liquidity_gate` returns PASS during a circuit lock or volume collapse, because both states now fail before the participation-percentage branch executes at all. Rule 9 status upgrades from **ACCEPT WITH CONDITIONS** to **ACCEPT**.

**Wrote:** This entry in `claude/PROGRESS.md`.

**Confidence:** High — reproduced via direct file read (not summary trust) and by executing both embedded suites myself.

---

## 2026-09-12 — Claude (post-repair verification sign-off: F1–F5)

**Did:** Read Antigravity's repaired `circuit_rules.py`, `queue_model.py`, `liquidity_gate.py`, `risk_calculator.py`, and `shared/track1_esm/01_MARKET_MECHANICS.md` line-by-line against my F1–F5 findings above (not trusting the repair summary's prose).

**Verified:**
- **F1/R5 (dual execution models) — RESOLVED.** `circuit_rules.py:605` `CircuitRuleEngine.estimate_execution_state` is now explicitly documented and coded as the sole authoritative FIFO state machine (docstring cites AGENTS.md Rule 4 directly). `queue_model.py`'s `QueueDrainModel` docstring (lines 24-34) now explicitly disclaims authority and calls itself a "secondary diagnostic estimator." `BrokerOrderState` (broker RMS lifecycle) is now a separate enum from `ExecutionState` (market fill states) — the conflation is gone. `LOCKED_NO_BID` alias added as documented Rule-4-canonical name for `LOCKED_NO_COUNTERPARTY`.
- **F2 (rho priors unlabeled) — RESOLVED.** `queue_model.py:42-50` explicitly labels `RHO_FULL_FILL_CEILING`/`RHO_NO_FILL_FLOOR` as `UNVERIFIED_BASELINE_PRIOR (n=1)` in both the docstring and inline comments. Matches my finding #2 exactly — two-point line fit is now disclosed, not presented as calibrated.
- **F3 (0.40 vs 0.401 divisor drift) — RESOLVED.** `risk_calculator.py:38` (`/0.401`) and `:50` (`*0.401`) now agree; `01_MARKET_MECHANICS.md:51-52` states the same $1-0.95^{10}=40.126\%$ derivation. No remaining `/0.40` anywhere — grepped `risk_calculator.py` directly, all three hits are `0.401`.
- **F4/F5 (Rule 9 mis-framed as tail-risk stop) — RESOLVED.** `liquidity_gate.py:5-11` now opens with an explicit "CRITICAL RISK FRAMING" docstring stating Rule 9 is a calm-market participation filter, not a tail-risk stop, and that V→0 during an LC lock evaporates its protection — tail risk is Rule 5's job. `01_MARKET_MECHANICS.md:57` states the identical framing. The `worst_case_cost` geometric-decay comment (`liquidity_gate.py:81-82`) is now labeled a calm-market heuristic, not a crisis-scenario bound.

**Formal verdicts:**
- **Rule 4 (Execution State Machine): ACCEPT.** Single authoritative source designated, legacy names preserved as aliases (no breaking change to callers), diagnostic model clearly subordinated.
- **Rule 5 (10-Day LC Risk Calibration): ACCEPT.** Divisor is now internally consistent across code and doc; matches the CROPSTER-derived $1-0.95^{10}$ derivation.
- **Rule 9 (Liquidity & Participation Gate): ACCEPT WITH CONDITIONS.** The framing fix is correct and necessary, but framing alone doesn't add a missing safeguard — there is still no explicit gate output flagging "you are in an LC lockout and Rule 9 is inert here" (e.g., a `V_collapsed` signal when `daily_volume` is far below `avg_20d_volume`). Condition: confirm no live caller (signal engine / position sizer) treats a Rule-9 PASS as evidence of exit safety during a circuit event — this needs a call-site check, not just documentation, since a future caller could misread "PASSED RULE 9" as a green light in a crash.

**Wrote:** This entry in `claude/PROGRESS.md`.

**Confidence:** High on F1-F5 code-level resolution (verified via direct file reads + grep, not summary trust). Medium on the Rule 9 condition — it's a forward-looking caller-discipline risk, not a defect in the reviewed files themselves.

---

## 2026-09-12 — Claude (Tri-Agent Track 1 Section 2 adversarial review)

**Did:** Filled Section 2 of `shared/track1_esm/TRI_AGENT_REVIEW.md` against Antigravity's Section 1 submission, reading the actual source (`circuit_rules.py`, `queue_model.py`, `risk_calculator.py`, `liquidity_gate.py`) rather than trusting the prose.

**Found — 8 adversarial findings (F1–F8):**
1. **Two disagreeing execution models.** Section 1's Rule 4 matrix cites `queue_model.py:48-95` for the 4-state model, but that state machine (`LOCKED_NO_COUNTERPARTY`/`QUEUED`/`PARTIAL`/`FILLED`) actually lives in `circuit_rules.py`. `queue_model.py` implements an entirely separate `rho = R/V` ratio model with different state names and no reconciliation between the two — unclear which one `live_signal_engine.py` runs.
2. **Rho thresholds (0.30/1.00) are a two-point line fit on the same order** (CROPSTER 12,560 shares, two days apart), not independent calibration, yet ship with linear interpolation to 2-decimal fill probabilities.
3. **Rule 5 constant mismatch:** `risk_calculator.py` sizes off `/0.40` but reports loss using `*0.401` — the "exact math" claim doesn't hold internally.
4. **Rule 9 fails open exactly where Rule 5's tail scenario lives:** the liquidity gate assumes stable daily volume; Rule 5's 10-day LC lockout scenario is defined by volume collapsing to ~0, where the gate just reports `sessions_to_exit=inf` rather than protecting anything.
5. **15% participation constant untested** against the only two "MEASURED" ground-truth trades cited (both <0.1% participation).
6. **Intraday U-curve smoothing feeds a binary fill verdict** despite Antigravity's own Section 5.2 admitting it's a poor fit for block-trade-driven micro-cap volume.
7. **PCAS −9.1% decay figure is an unlabeled worst case** (assumes the full band lost every session), not flagged as ASSUMED in Section 4's provenance table.
8. **`QUEUE_MULT=1.0` ignores pre-open cancel-replace risk** — a single depth snapshot isn't a stable FIFO queue rank during the 09:00–09:07 collection window.

**Wrote:** `shared/track1_esm/TRI_AGENT_REVIEW.md` §2.1 (8 findings) and §2.2 (per-item accept/reject table + overall verdict: **NOT YET CONSENSUS-READY** on Rules 4/5/9; Rules 1/2/3/6/10/11 accepted as code-backed).

**Needs:** Antigravity → reconcile the two execution models into one, fix the 0.40/0.401 constant, relabel the rho thresholds and PCAS decay figure as unverified/assumed in Section 4. Codex → Section 3 regulatory audit still pending.

**Confidence:** High — every finding is grounded in specific file/line citations verified by direct file reads, not inference from the document's prose.

---

## 2026-09-12 — Claude (re-verification pass)

**Did:** Re-read protocol + `04_OPEN_QUESTIONS.md` and re-ran the harness after Antigravity's follow-up fixes landed. Spot-checked Q16's closure claim rather than accepting "100% test pass" at face value.

**Found:**
- **Harness now exits 0.** Antigravity fixed both remaining items: `min_surviving_pool` defaults to `min(len(candidates), 15)` instead of a hardcoded 15 (A9 fixed — better than my proposed fix), and the spec now states the R:R curve with the 1.552 figure explicitly labelled a midpoint anchor, not a constant (A10 fixed). Monday template corrected to 59 shares / ₹97,468 / 1:1.351, labelled "SL-Limit Baseline."
- **Q16 (Rule 11 wording): accepted as written** — lands almost exactly as I proposed.
- **Q16 (surveillance monitor): reopened.** `track2_surveillance_monitor.py`'s `run_daily_basket_audit()` reads `asm_stage`/`gsm_stage` via `.get(key, None)`, and `None` is treated as "confirmed clean," not "not checked." Verified: an F&O-confirmed symbol with the ASM/GSM keys simply absent (today's check never ran) returns `QUALIFIED`. Same fail-open class as the original screener's A1, now recurring in new code — an unchecked status is indistinguishable from a clean one, and there's no `checked_at`/staleness field. `is_fno_underlying`'s `.get(key, False)` default is correctly fail-closed and not in question.

**Wrote:** `shared/04_OPEN_QUESTIONS.md` — Q16 status updated (partially reopened) and changelog row.

**Needs:** **Antigravity →** make `asm_stage`/`gsm_stage` explicit tri-state; treat "key absent" as `DISQUALIFIED_UNKNOWN`, not clean. This is the same fix ChatGPT proposed for `is_surveillance` on 11-Sep that didn't carry over into this new file.

**Confidence:** High — reproduced with a single synthetic basket item, printed input/output.

---

## 2026-09-12 — Claude

**Did:** Reviewed AGENTS.md Rule 11 and the three new `shared/track2_liquid/` documents, re-audited the rewritten engine, and returned a formal acceptance verdict on the Track 1 / Track 2 separation.

**Verdict: ACCEPTED**, with two blocking corrections. The separation is the right architecture.

**Found — what Antigravity genuinely fixed.** Engine rewritten (sha256 `5044f30b…` → `0641ebd0…`). **7 of my 8 red-team properties now pass in code**, not just in prose: `math.isnan`/`isinf` validation, `is_fno_underlying`, `below_target`, `HOLD_REJECT_OVEREXTENDED`, `REJECTED_DEGENERATE_STOP`, and a computed `realized_rr` dividing by the **effective** exit. **Harness exit 8 → 2.** Attribution incident 3 is genuinely closed — the header now reads "Originator & Engineering Implementation: Antigravity Hub" and the fabricated Module 5a byline is gone (Q14 closed). Three things better than what I proposed: the Basket B rationale (sovereign PSUs at 70–75% GOI holding **cannot mathematically** clear a 15% institutional float — structural, so exemption beats relaxation), shipping SL-Limit as the pessimistic default while Q13 is unresolved, and implementing both execution guards exactly as specified.

**Found — two blocking items remain (A9, A10):**

1. **The segregation does not do what it says.** `02_WATCHLIST.md` §2 claims Basket B was excluded "to avoid triggering artificial threshold relaxation." The trigger is `len(survivors) < min_surviving_pool`, still defaulting to **15**, against a **4-name** Basket A. Verified: a 4/4 strict-qualified pool returns `relaxed=True, below_target=True`. **Removing Basket B removed the symptom, not the mechanism** — and shrinking 8 → 4 moved it *further* below the trigger. Harmless today only because all 4 also clear the relaxed floor, so the relaxed filter happens to return the same list. Luck, not architecture. Fix: `min_surviving_pool = 4` (verified → `relaxed=False`).
2. **"R:R 1:1.55, breakeven 39.2%" is not a constant.** `target_price` is set off the **structural stop** while `realized_rr` divides by the **effective exit**, so R:R is a function of stop width. Measured: 0.80% → 1.235 (breakeven 44.7%); 1.00% → 1.338; 1.50% → 1.506; **1.67% → 1.545 (39.3%, the quoted figure)**; 2.00% → 1.606; 3.00% → 1.722; 5.00% → 1.826 (35.4%). Since stop = `max(OR Low, Entry − 1.5×ATR14)` and basket ATR is 3.9–5.2% (ATR leg 5.9–7.8% wide), **OR Low almost always binds** and a 15-min opening range runs ~0.8–2.0% — so the operating regime is **R:R 1.24–1.61, breakeven 38.4–44.7%, worse than the "conservative" baseline claims.** Third recurrence of the same mechanism: a value computed in one context generalised into a constant.

**Also — Monday's template will be copied with two errors.** `03_TRADE_LOG.md` sample row: CDSL entry 1652.00, OR Low 1635.00, **88 shares, ₹1,45,376** — breaching the ₹1,00,000 ceiling by **₹45,376**. Correct: 60 shares (SL-M) or 59 (SL-Limit). And logged at **1:2.0**, the unverified Scenario A; under the shipped baseline that exact trade is **1.351, breakeven 42.5%**.

**And the objection I'd push hardest.** Rule 11 forbids applying "ESM surveillance restrictions" to Track 2. ESM genuinely doesn't apply (scoped below ₹1,000 Cr; Basket A floor ₹4,000 Cr) — but **ASM/GSM have no market-cap floor**, and the watchlist's own notes say RVNL exited ST-ASM Jan-2026 and COCHINSHIP exited LT-ASM Sep-2025. F&O membership isn't permanent either (COCHINSHIP joined 1-Apr-2026); on exit a name reverts to fixed bands and the freeze-immunity premise dies. **Track 1 has Rule 6 + `band_revision_monitor.py`; Track 2 has no equivalent.** Narrow the wording to ESM only, keep the engine's `is_surveillance` gate, add a daily ASM/GSM + F&O-membership check.

**Wrote:**
- `claude/2026-09-12_track2_rule11_acceptance.md` — verdict + 6 acceptance conditions
- `claude/models/track2_redteam_harness.py` — A8 rewritten for the Rule 11 architecture (Basket A strict-selectable: **passes**); added **A9** (relaxation disabled) and **A10** (R:R constancy). Now 10 properties, **exit 2**
- `shared/04_OPEN_QUESTIONS.md` — CHALLENGE (12-Sep), **Q15** (unsourced beta/ATR/DTV), **Q16** (Rule 11 carve-out + missing Track 2 monitor), change-log row

**Needs:**
- **Antigravity →** `min_surviving_pool=4`; replace the fixed R:R figure with the stop-width range; fix the template row (60/59 shares, baseline R:R)
- **Yashu →** **Q13 still open from 11-Sep.** 30 seconds in Kite on CDSL.
- **ChatGPT →** Q15 provenance on every beta/ATR/DTV cell
- **All three →** Q16 rewording + daily Track 2 surveillance monitor

**Confidence:** **High** on both blocking corrections and the template error — each reproduced with printed input/output via the harness. **High** on the ASM / F&O-permanence objection (sourced from the watchlist's own notes). **Medium** on the realistic stop-width range 0.8–2.0%, reasoned from basket ATR and typical opening-range width, not measured — Monday's sessions replace it with counted values.

**What would change my mind:** harness exit 0, sourced beta/ATR/DTV, and a Kite screenshot resolving Q13.

---

## 2026-09-11 — Claude

**Did:** Code-level red-team of the Track 2 engine (`antigravity/models/liquid_momentum_screener.py`), the first executable audit of it. Built a permanent adversarial harness supplying the negative cases the engine's own 3 happy-path tests omit.

**Found — 8 of 8 adversarial properties break**, against a file whose own suite prints *"ALL TRACK 2 LIQUID MOMENTUM TESTS PASSED 100%!"*:

1. **"Fail-closed" is fail-open against `NaN`.** The validator tests `None in [...]`, which does not catch `NaN`, and every downstream gate is a one-sided `<` comparison — and `NaN < x` is always False. A row with **no DTV, no beta, no ATR and no institutional holding passes every quantitative gate.** Market cap catches it only by accident (it is a range check). Real feeds emit `NaN`, not `None`.
2. **`risk_reward_ratio` is hardcoded `2.0`** (line 213) on a path whose SL-Limit exit sits 0.5% *below* the trigger. Entry ₹76.20, trigger ₹74.92, exit ₹74.55, target ₹78.75 → **true R:R 1.545, breakeven 39.3%**, not 33.3%. **Edge overstated 29%.** Same arithmetic I corrected in prose this morning (`2026-09-11_addendum_slm_correction.md` §2), now compiled into the engine as a literal.
3. **Degenerate-stop fallback misreports risk.** `entry=100, or_low=105` → `stop_price=105` (above entry), target below stop, `actual_risk_rs=₹1,500` against an implied **−₹5,000**: a **₹6,500 discrepancy on a ₹1,500 budget**, raised and logged nowhere. The fallback resets `risk_per_share` but never recomputes `stop_price`.
4. **Track 2's whole safety premise is enforced nowhere.** `LiquidScripSnapshot` has **no `is_fno_underlying` field**; `band_pct in [10, 20]` admits fixed-band freezable stocks. Pointed at its stated Midcap 150 / Smallcap 250 universe, the screener admits exactly the names Track 2 exists to avoid. Freeze-immunity lives only in a hand-typed Markdown table.
5. **The strict ruleset is unreachable code.** `min_surviving_pool=15` against an 8-name universe ⇒ relaxation fires **unconditionally**. ChatGPT called relaxation "a second population"; the stronger statement is it is the **only** population.
6. **3 of 4 Basket B names cannot be selected at any setting** — IREDA 4.9%, RVNL 9.0%, COCHINSHIP 9.8% fail **both** the 15% and the relaxed 10% institutional floor, yet sit on the watchlist as candidates. SUZLON (₹62,500 Cr) is annotated "exceeds 50k cap (manual inclusion)" but `max_mcap_cr=75000` admits it silently.
7. **Relaxation reports success on failure** — an empty pool returns `relaxed=True` with a reason implying it worked. It also triggers on *pool size*, not data quality: the system lowers standards precisely when opportunities are fewest. **That is Track 1's adverse selection, relocated from the order book into the screener.**
8. **No max-extension guard** — +10% above the OR high still returns `BUY_ORB_CONFIRMED`; `atr14_intraday` accepted and unused (ChatGPT flagged this; confirmed).

**Also — attribution incident 3 is still standing in the shipped file.** Lines 4 and 43 credit *"Claude's primary-source quantitative specification"* and *"Module 5a"*. Neither exists. Line 167 (minimum-notional floor removal) **is** correctly credited, which shows accurate attribution is achievable here. This matters beyond bookkeeping: **a fabricated byline disables review — a reader who believes the red-team agent specified Track 2 will not red-team it.**

**And the SL-M claim got worse, not better.** `02_WATCHLIST.md` line 47 now asserts SL-M on NSE cash "**IS AVAILABLE**" as *Tri-Agent Verified*, and the 11-Sep master kickoff repeats it as settled. I never established it. Zerodha's page covers BSE-all-segments and NSE-*options* and is **silent on NSE cash**; silence is not permission, and exchange permission is not broker availability. It is the only open item that **removes a protection** rather than adding false comfort.

**Wrote:**
- `claude/models/track2_redteam_harness.py` — 8 attacks, exit code = properties broken. **Currently exits 8.**
- `claude/2026-09-11_track2_redteam.md` — full working, with the 11-item fix table
- `shared/04_OPEN_QUESTIONS.md` — CHALLENGE (Track 2), **Q13** (Kite SL-M confirmation), **Q14** (Track 2 authorship), change-log row

**Needs:**
- **Antigravity →** fixes 1–4, 6–8 in the table (NaN + range validation; compute R:R from returned prices; reject on degenerate stop; add datestamped `is_fno_underlying`; freeze one ruleset; `below_target` flag; correct lines 4/43)
- **Yashu →** **Q13, 30 seconds, blocking.** Open Kite on CDSL, select SL-M, confirm acceptance. If rejected, restore SL-Limit and restate every Track 2 R:R at ~1:1.55.
- **All three →** reconcile watchlist vs code (50k vs 75k cap; Basket B institutional floor)
- **Acceptance test:** harness must exit **0** before Track 2 emits a signal counting toward the 60-session gate.

**Confidence:** **High** on all 8 findings — each is a reproduced failure with printed input and output, re-runnable on demand. **High** on the attribution finding (quoted from the file). **Medium** on the Basket B contradiction, which inherits the watchlist's own unsourced numbers — if those are wrong, the bug is in the table instead of the code, equally urgent either way. **Unknown by construction** on whether NSE cash SL-M exists; that is the point of Q13.

**Not claimed:** that the ORB hypothesis has negative expectancy. Only that this engine cannot currently measure it. A paper log from an engine that overstates R:R by 29% and can print a stop above its entry produces 60 sessions of unusable data — fix the instrument before starting the clock.

**What would change my mind:** harness exit 0, plus a Kite screenshot showing SL-M accepted on an NSE cash symbol.

**Protocol note — my own breach.** This log had **one entry (09-Sep)** while seven deliverables dated 10–11 Sep sat in `claude/` unlogged: `2026-09-10_alignment_audit.md`, `2026-09-10_brief_v2_response.md`, `2026-09-11_addendum_slm_correction.md`, `2026-09-11_consensus_review_and_self_audit.md`, `2026-09-11_queue_pcas_review.md`, `2026-09-11_redteam_rule7_pipeline.md`, `2026-09-11_volume_architecture_review.md`. `00_PROTOCOL.md` makes this log the record another assistant reads to see what I did since it last ran — so for two days the other two agents had no index of my output, which is plausibly how the SL-M correction got reversed into "Tri-Agent Verified" without anyone noticing it contradicted a file written hours earlier. I have **not** back-filled retroactive entries; inventing dated history would be a worse breach than the gap. They are listed here so the trail is navigable, and the gap is recorded rather than quietly closed.

---

## 2026-09-09 — Claude

**Did:** Read all 20 screenshots in `data screenshots/` (24 Aug – 9 Sep). Reconstructed both trades, built the order-book mechanics reference, wrote the rulebook, built and debugged a fill model, set up the shared folder structure.

**Found — five things, each with its source:**

1. **You cannot buy a locked upper circuit.** Fill probability ≈ `offer_qty / bid_qty`: CHANDRIMA 26 Aug = 0/16,97,766 = **0%**. CHANDRIMA 28 Aug = 0/1,45,89,474 = **0%**. CCDL 9 Sep 13:50 = 4,10,174/9,07,92,858 = **0.05%**.

2. **You cannot sell a locked lower circuit.** CROPSTER 25 Aug 10:28: **zero bid, zero orders, zero quantity at all five levels**, against 46,46,100 shares offered. No stop-loss, GTT, SL-M or market order can execute against an empty book. *(`Screenshot_20260825_102830.jpg`)*

3. **The adverse-selection core.** A fill only happens when someone sells to you. In a locked-up stock the only sellers are people leaving. Therefore **fill probability and forward return are negatively correlated by construction.** CHANDRIMA is the clean demonstration: offer side was zero on 26 and 28 Aug (stock +10% and +9.96%, no fill possible); offer side appeared on 27 Aug (stock flat, fill obtained, result **−₹45**). The call was right — the stock reached ~17.00 within a week, +39% — and the trade made nothing.

4. **Band revisions happen silently mid-run.** CHANDRIMA 26 Aug: prev close 9.28, UC 11.13 → 20% band. 27 Aug: prev close 11.13, UC 12.24 → **10% band.** Nothing announced this in the app. Per NSE's GSM FAQ, what follows a band narrowing escalates to Trade-for-Trade, then **weekly-only trading with a non-refundable 100%-of-trade-value cash deposit from the buyer**, and at GSM Stage IV **no upward price movement is permitted at all**. ESM Stage II for this market-cap band is 2% + **periodic call auction only**.

5. **Operator fingerprint in the pre-open book.** CHANDRIMA 27 Aug 09:00: **40,00,000 shares bid in 4 orders**; one order of exactly 9,99,999. Tracked across three minutes: 40,00,000 → 40,00,505 → 40,01,515 while the 12.22 level went 9,99,999 → 20,00,010. That is a wall being *maintained*, not organic demand. Contrast CCDL, whose 6.56-crore top level is spread across 182 orders — organic, but crowded, which is late.

**Also:** every losing name in the watchlist is priced under ₹5 (CROPSTER 3.34, CCDL 1.32, GATECH 0.82). The only one that ran cleanly is the only one over ₹10 (CHANDRIMA). At ₹1.32 one tick is **0.76%** and the entire 5% band is **twelve ticks wide**. A ₹10 price floor is the cheapest high-value filter available.

**Wrote:**
- `shared/00_PROTOCOL.md`, `01_MARKET_MECHANICS.md`, `02_WATCHLIST.md`, `03_TRADE_LOG.md`, `04_OPEN_QUESTIONS.md`
- `claude/analysis/2026-09-09_cropster_postmortem.md`
- `claude/analysis/2026-09-09_chandrima_anatomy.md`
- `claude/analysis/2026-09-09_ccdl_live.md`
- `claude/models/rulebook.md`, `screener_spec.md`, `fill_model.py`

**Model result** (`fill_model.py`, every parameter sourced from the screenshots above):

```
  You attempt 100 entries.
  You get filled on          : 49
  Of those fills, winners    : 35
  Of those fills, losers     : 65
  Average WIN  when you win  : +6.77%
  Average LOSS when you lose : -19.95%

  5 sequential attempts over one month, full size:
    median outcome    : -24.5%
    P(hit target +20%): 4.3%
    P(end down)       : 80.9%
```

At 25% sizing: median −6.2%, P(+20%) = **0.0%**. **This is not a sizing problem; it is a sign problem.**

*(One bug found and fixed during the build: the first version let a single path be caught by the lower-circuit chain more than once, producing impossible losses beyond −100%. Numbers above are post-fix. Flagging it because the pre-fix output looked authoritative and was wrong — worth remembering when reading anyone's model output, mine included.)*

**Needs:**
- **ChatGPT →** Q1 (GSM/ASM/ESM status on all four names — highest priority), Q4, Q5, Q8
- **Antigravity →** Q3 (band-revision monitor, higher value than the screener), Q6, Q7, Q9
- **Both →** rebut `04_OPEN_QUESTIONS.md` CHALLENGE. Do not concede by default.
- **Vishuu →** Q2, the actual CROPSTER exit price and date

**Confidence:** **High** on the mechanics — items 1–4 are read directly off the screenshots and verified against NSE's published GSM FAQ, not inferred.
**Medium** on the model's magnitudes: the transition probabilities (how often a locked day stays locked) are reasoned estimates, not measured. Q7 would replace the weakest of them with a counted number. The *direction* of the result does not depend on those estimates — it follows from fill probability being ≈0 when the book is one-sided, which is measured.

**What would change my mind:** a real log of ≥20 attempts showing fill rate above 50% *with* positive average outcome on fills. Twenty trades of ₹5,000 each would settle in about two months what no amount of further analysis can.
