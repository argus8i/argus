# Red-Team Verdict — Track 2 Liquid Momentum Engine (code-level)

**Author:** Claude (analyst / red team) · **Date:** 2026-09-11
**Target:** `antigravity/models/liquid_momentum_screener.py` (274 lines)
**Harness:** `claude/models/track2_redteam_harness.py` — reproducible; exit code = number of broken properties
**Result:** the engine's own suite prints *"ALL TRACK 2 LIQUID MOMENTUM TESTS PASSED 100%!"* on 3 happy-path cases. **8 of 8 adversarial properties break.**

> Companion to `claude/2026-09-11_consensus_review_and_self_audit.md`, which covers Track 2 at the strategy level (provenance, gate dilution, gap risk). This document is the code audit only.

---

## Verdict

**Track 2 is not ready to emit a paper signal, let alone a live one.** Not because the thesis is wrong — a liquid, F&O-eligible universe genuinely removes Track 1's zero-bid trap, and that is real progress — but because **every quantitative guard the engine advertises is either fail-open or arithmetically wrong**, and the one structural property that justifies the entire track is enforced nowhere in code.

This is a different failure mode from Track 1. Track 1 failed on **microstructure**: the trade could not be executed. Track 2 fails on **instrumentation**: the trade can be executed, but the engine misreports what it costs and admits candidates it claims to reject.

Nothing here says the ORB hypothesis is unprofitable. It says the current code cannot measure whether it is.

---

## 1. "Fail-closed" is fail-open against real data

`screen_universe` docstring: *"Enforces strict fail-closed data validation."*

```
[BROKEN] A1  NaN-poisoned row must be rejected by fail-closed validator
         dtv=NaN beta=NaN atr=NaN inst_holding=NaN -> survivors=['NAN_STOCK']
```

The validator at line 66 is `if None in [c.series, c.is_surveillance, ...]`. Real feeds — pandas, CSV, bhavcopy, any `read_csv` — emit **`NaN`, not `None`**, for a missing numeric. `None in [...]` does not catch `NaN`. And every downstream gate is a one-sided `<` comparison:

```python
if c.dtv_med20_cr < min_dtv_cr:     return False   # NaN < 30.0  -> False -> passes
if c.beta < min_beta:               return False   # NaN < 1.3   -> False -> passes
if c.atr14_pct < atr_floor:         return False   # NaN < 3.5   -> False -> passes
if c.inst_holding_pct < inst_floor: return False   # NaN < 15.0  -> False -> passes
```

**Every comparison against `NaN` is False, so a row with no liquidity data, no beta, no volatility and no institutional ownership passes every quantitative gate in the screener.**

Market cap escapes only by accident — it is a *range* check (`not (min <= nan <= max)` → `not False` → `True` → reject). The four gates written as one-sided comparisons all fail open.

This is the same class of defect ChatGPT flagged for `is_surveillance` (a bare boolean where missing becomes "clean"), but wider: it affects the four numeric filters that define the universe. A tri-state sentinel on `is_surveillance` alone does not fix it.

**Fix:** validate before filtering — reject any row failing `math.isnan()` on a required numeric, or falling outside a physical range (`beta` ∈ [0, 5], `atr14_pct` ∈ (0, 50], `inst_holding_pct` ∈ [0, 100], `dtv_med20_cr` > 0).

---

## 2. The arithmetic contradicts itself — the same way as yesterday

`SizingResult.risk_reward_ratio` is **hardcoded to `2.0`** at line 213, on both order-type paths. On the BSE path the exit limit sits **0.5% below the stop trigger**, so realized risk per share exceeds the risk the position was sized on:

| | Value |
|---|---|
| Entry | ₹76.20 |
| Stop trigger | ₹74.92 (position sized on ₹1.28/sh) |
| SL-Limit exit | ₹74.55 (**actual risk ₹1.65/sh**) |
| Target | ₹78.75 (reward ₹2.55/sh) |
| **Reported R:R** | **1 : 2.00 → breakeven 33.3%** |
| **True R:R** | **1 : 1.545 → breakeven 39.3%** |

**The engine overstates its edge by 29% and understates the win rate it must clear by 6.0 percentage points.**

I flagged this exact mechanism in `claude/2026-09-11_addendum_slm_correction.md` §2 earlier today — a 1:2 figure carried into a context whose arithmetic yields 1:1.6. The correction was accepted in prose and then **hardcoded as a literal `2.0` in the engine**, where it is now load-bearing for every expectancy number Track 2 will produce.

R:R must be *computed* from the prices actually in the result, never asserted:

```python
effective_exit = limit_exit_price if limit_exit_price is not None else stop_price
risk_reward_ratio = (target_price - entry_price) / (entry_price - effective_exit)
```

— before any slippage assumption is layered on top.

---

## 3. A silent fallback that prints a stop above the entry

Lines 173–174:

```python
if risk_per_share <= 0:
    risk_per_share = entry_price * 0.015   # Fallback 1.5% stop
```

It resets the *risk* but **never recomputes `stop_price`**. Feed it `entry=100.00, or_low=105.00`:

```
[BROKEN] A4  stop_price = 105.0  (ABOVE entry)
[BROKEN] A5  reported risk Rs 1,500.00  vs  shares*(entry-stop) = Rs -5,000.00
             -> the two disagree by Rs 6,500 on a Rs 1,500 budget
```

The function returns a 1,000-share position, a stop that is instantly triggered, a target *below* the stop, and an `actual_risk_rs` of ₹1,500 that is ₹6,500 away from what its own returned prices imply. It raises nothing and logs nothing.

`calculate_position_size` is a free `@staticmethod` with **no coupling to the ORB check** — nothing enforces `entry > or_low`. A stale `or_low`, a transposed argument, or a re-entry after the range shifts all reach this branch. **Fail closed: raise, or return a `REJECTED` result. Never silently substitute a number.**

---

## 4. Track 2's entire safety premise is enforced nowhere in code

`shared/02_WATCHLIST.md` §Notes 1: *"Because all 8 scrips are F&O underlyings, NSE applies dynamic flexing bands rather than fixed 5% circuit freezes."*

That sentence is the whole argument for why Track 2 escapes the CROPSTER/CCDL zero-bid trap. In code:

```
[BROKEN] A6  band_pct=10.0 (fixed, freezable) -> survivors=['NON_FNO']
```

`LiquidScripSnapshot` **has no `is_fno_underlying` field at all.** Line 72 accepts `band_pct in [10.0, 20.0, 0.0]`, so ordinary fixed-band, freezable stocks pass. The freeze-immunity property lives *only* in a hand-maintained Markdown table — while the screener's stated purpose (line 43, "Adaptive Universe Screening") is to run across Nifty Midcap 150 / Smallcap 250: hundreds of names, most not F&O underlyings.

**The moment this screener is pointed at its intended universe, it will admit exactly the stocks Track 2 exists to avoid.**

Add `is_fno_underlying: bool`, require `True`, and reconcile it against the NSE derivatives master with a datestamp — F&O membership changes (the watchlist notes COCHINSHIP is F&O only since 1-Apr-2026, and RVNL exited ST-ASM in Jan 2026).

---

## 5. The relaxation branch can never *not* fire

```
[BROKEN] A8  strict(15%)  survivors 4/8: CDSL, ANGELONE, SUZLON, INOXWIND
             relaxed(10%) survivors 5/8: + BDL
```

Three findings, all from the watchlist's own documented numbers:

1. **`min_surviving_pool` defaults to 15. The curated universe is 8 names.** 8 < 15 unconditionally, so **the relaxation branch fires on every run and the strict ruleset is unreachable code for this basket.** ChatGPT correctly called relaxation "a second population"; the stronger statement is that the second population is the *only* population. The strict parameters are decorative.

2. **IREDA (4.9%), RVNL (9.0%) and COCHINSHIP (9.8%) fail both the 15% and the relaxed 10% institutional floor.** Three of four Basket B names sit on the watchlist as candidates **the screener cannot select at any setting.** The watchlist documents this for IREDA ("Fails 15% institutional rule") and lists it anyway.

3. **SUZLON (₹62,500 Cr)** is annotated "Exceeds 50k Cr cap (manual inclusion)", but the code default is `max_mcap_cr = 75000.0` — it passes silently, with no manual-inclusion flag. Spec and code disagree about the cap.

And the relaxation reports success when it has failed:

```
[BROKEN] A2  meta = {'survivors_count': 0, 'relaxed': True,
                     'reason': 'Relaxed ATR floor ... due to thin intersection'}
```

An **empty** pool returns flagged `relaxed=True` with a reason implying the relaxation worked. No field distinguishes "relaxed and now sufficient" from "relaxed and still empty."

**The deeper objection:** relaxation is triggered by *pool size*, not by data quality. The system lowers its standards precisely when the market offers fewest opportunities — it takes worse trades in worse conditions. That is an adverse-selection mechanism structurally identical to the one that killed Track 1, relocated from the order book into the screener. Freeze one ruleset; make any relaxed variant a separately named, separately logged experiment.

---

## 6. Attribution incident 3 is still standing in the shipped file

```
line   3: Part of Project Swing Trades (Antigravity + Claude + ChatGPT) - Track 2 Engine.
line   4: Grounded directly in Claude's primary-source quantitative specification (11-Sep-2026).
line  43:     Implements Module 5a & 5b from quantitative research:
```

**There is no Module 5a. There is no Claude quantitative specification for Track 2.** I did not propose the liquid momentum track, the 8-name basket, the ≥15% institutional screen, or the ORB parameters. I recorded this in `claude/2026-09-11_consensus_review_and_self_audit.md` this morning; the lines are unchanged.

Line 167 (*"Minimum notional floor removed per Claude/ChatGPT red-team audit"*) **is** accurate — I did call for that, and credit is correctly placed. So the file demonstrates that correct attribution is achievable here; lines 4 and 43 are simply wrong and should be replaced with whoever actually selected the universe and parameters.

This matters beyond bookkeeping: a reviewer who believes Track 2 was specified by the red-team agent will not red-team it. **A fabricated byline disables the exact check that would have caught the eight defects above.**

---

## 7. Smaller, still real

- **A7 — no maximum-extension guard.** `current_price=110` vs `or_high=100` (+10% extended) returns `BUY_ORB_CONFIRMED`. Any distance above the OR high qualifies, so the engine is most confident exactly when the move is most extended. Add a ceiling (e.g. reject beyond `or_high + 0.5 × ATR`).
- **`atr14_intraday` is accepted and never used** in `evaluate_15m_orb_breakout` (ChatGPT flagged this; confirmed).
- **`volume_ratio` compares a partial bucket against a full-bucket median** unless the caller guarantees the 09:15–09:30 window has closed. Nothing in the signature enforces it. An 09:22 call measures 7 minutes against a 15-minute median and *under*-reports the ratio — biasing toward rejection, so it is safe-direction, but it makes the 2.5× threshold uncalibrated.
- **The DTV cap is 0.1% of traded value** (line 197), far stricter than AGENTS.md Rule 9's 15%. Conservative, so not a defect — but the two should be reconciled in writing so nobody later "fixes" 0.1% up to 15%.

---

## 8. The standing SL-M problem, restated because it got worse

`shared/02_WATCHLIST.md` line 47, now labelled **"Tri-Agent Verified"**:

> *"SL-M (Stop-Loss Market) **IS AVAILABLE** on NSE cash equity … preserving the true 1:2 Risk-to-Reward ratio without limit-offset distortion."*

The 11-Sep master kickoff briefing repeats it: *"Direct Stop-Loss Market (SL-M) on NSE Cash (preserving true 1:2 Risk-to-Reward with 33.3% breakeven hurdle)."* The engine encodes it at lines 46, 168 and 180.

**I did not establish this, and my 11-Sep addendum said so explicitly.** What I verified is narrower: Zerodha's SL-M page documents BSE discontinuing SL-M across segments and NSE discontinuing it for *options*, and **is silent on NSE cash equity**. Silence is not permission. Separately, **exchange permission ≠ broker availability** — brokers restrict order types independently, and whether Kite accepts SL-M on NSE cash has still not been checked by anyone.

An unverified inference has been promoted to "Tri-Agent Verified", written into shared state, repeated in the master briefing, and compiled into an execution engine — while the thing it asserts remains a 30-second check nobody has run. **This is the project's characteristic failure: not a wrong number, but a number that gains confidence with each re-transmission and is never traced back to a source.**

It also matters more than the rest because it is the only one that **removes a protection** rather than adding false comfort. If Kite rejects SL-M on Monday, the stops do not exist at the moment they are needed.

**Not resolvable by analysis.** Open Kite on CDSL, select SL-M, confirm acceptance. If accepted, the 1:2 claim stands for the NSE path. If not, restore SL-Limit and restate every Track 2 R:R figure at ~1:1.55.

---

## 9. What I am *not* claiming

- I have **not** shown the ORB hypothesis has negative expectancy. I have shown the engine cannot currently measure its expectancy honestly.
- I have **not** verified F&O membership, market caps, betas or institutional percentages for the 8 names — those come from `shared/02_WATCHLIST.md` and are unsourced there. §5 is *conditional on the watchlist's own numbers*; if they are wrong, the contradiction lies in the table rather than the code — a different bug with the same urgency.
- The gates that **do** work, work. `evaluate_15m_orb_breakout` correctly rejects non-positive inputs; the series, surveillance-boolean and DTV caps behave as written. The fail-closed *intent* is real and partly implemented. This is a code-quality problem, not a design fraud.

---

## 10. Required before Track 2 emits a single paper signal

| # | Fix | Owner | Blocking |
|---|---|---|---|
| 1 | `math.isnan` + physical-range validation on all numerics *before* filtering | Antigravity | **Yes** |
| 2 | Compute `risk_reward_ratio` from returned prices; never hardcode | Antigravity | **Yes** |
| 3 | Degenerate-stop branch must reject, not silently substitute | Antigravity | **Yes** |
| 4 | Add `is_fno_underlying` (datestamped, reconciled to NSE master); require `True` | Antigravity | **Yes** |
| 5 | Confirm SL-M acceptance in Kite on NSE cash; correct all R:R figures to match | **Yashu** (30 sec) | **Yes** |
| 6 | Freeze one ruleset; relaxed variant becomes a named separate experiment with its own log | Antigravity | **Yes** |
| 7 | Correct lines 4 and 43 to the actual author of the Track 2 universe and parameters | Antigravity | **Yes** |
| 8 | `below_target` flag in screener meta; an empty pool must never read as success | Antigravity | Yes |
| 9 | Reconcile watchlist vs code: 50k vs 75k mcap cap; Basket B institutional floor | All three | Yes |
| 10 | Max-extension guard; use or remove `atr14_intraday`; enforce a closed 09:15–09:30 bucket | Antigravity | No |
| 11 | Reconcile the 0.1% DTV cap against Rule 9's 15%, documented either way | Antigravity | No |

`claude/models/track2_redteam_harness.py` is the acceptance test. **It currently exits 8. It must exit 0 before Track 2 produces a signal that counts toward the 60-session gate.**

---

## 11. Standing position

Observation gate is **0/60 sessions, 0/20 fillable trades**. AGENTS.md Rule 1 holds. Nothing here changes that, and nothing here should be read as clearing Track 2 for paper trading.

The engine must first be able to report its own risk correctly, because **a paper log generated by an engine that overstates R:R by 29% and can print a stop above its entry will produce 60 sessions of unusable data.** Fixing the instrument before starting the clock is far cheaper than discovering at session 60 that the log has to be thrown away.

**Confidence:** **High** on §§1–5 and §7 — each is a reproduced failure with printed input and output, re-runnable via the harness. **High** on §6 (quoted directly from the file). **Medium** on §5's watchlist contradictions, which inherit the watchlist's own unsourced numbers. **High** on §8 as a *provenance* failure; **unknown**, by construction, on whether NSE cash SL-M is actually available — that is precisely the point.

**What would change my mind:** a harness run that exits 0, plus a Kite screenshot showing SL-M accepted on an NSE cash symbol.
