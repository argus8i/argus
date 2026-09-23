# Independent Desk Audit: What the Earlier Audits Missed

**Author:** Claude (fresh, independent session) · **Date:** 2026-09-23 (evening) · **Baseline:** HEAD `54c2464` plus working tree
**Scope:** Only problems **not** already in Claude's red team (F1–F47, `Claude outputs/2026-09-23_full_system_redteam.md`) or Codex's recheck (R01–R16, `shared/reviews/codex_remediation_recheck_20260923.md`). Where a finding builds on one of those, it says "extends Fxx/Rxx".
**Method:** I read the code and today's live output files. I ran the project's own sizing, cost and regime functions on today's real RVNL signal and on the 32 days of history on disk. I checked broker and regulatory facts on the web (sources at the end). **No production code, configuration or ledger was changed.** The probe scripts are in `Claude outputs/2026-09-23_desk_audit_probes/`.

---

## Verdict

1. **Tomorrow's session can't run, and no session can ever count.** There is no working data feed: Kite CDP is disabled and the Dhan credentials were never entered. No launcher runs a mode that can increment the 60-session counter. "Session 1 of 60 tomorrow" is not possible as the repo stands.
2. **The strategy, as coded, needs roughly a 70–85% win rate to break even.** The ₹1,00,000 notional cap quietly shrinks R below the ₹1,500 budget. Delivery (CNC) costs then eat 0.2–0.7R per trade, and the runner-to-breakeven rule caps the typical win at +0.75R. No ORB system in any market wins 70–85% of the time.
3. **The rule being tested is not an opening-range breakout.** It fires on any 15-minute bar up to 14:15, even after the breakout has failed and price has closed below the opening-range low. Today's only signal (RVNL, 11:00) was exactly that.
4. **There has never been a backtest.** The only evidence plan is 20 paper fills. That sample can't tell +0.3R from −0.3R. A historical test on all F&O stocks would answer the edge question in days, for ₹500.
5. **What you are told does not match what the engine does.** The chat narration used the wrong volume threshold, called a stock the engine had excluded an "active breakout", and described features that don't exist. Fabricated data also still ships in the terminal, including a fake "CLAUDE_AUDIT" trade verification.

---

## Tier A: the system cannot run or count

**N1 🔴 No working data feed for the next session (extends R07).**
- `antigravity/config/dhan_config.json` is **byte-identical** to `dhan_config.json.example`. The client ID and token are still the template placeholders, so the Dhan bridge has never connected once. Every "sub-10ms, 500-instrument, verified" statement about it is untested.
- The Kite launchers are disabled (`start_track2_kite_feed.bat` now only prints a security notice).
- `start_track2_paper_desk.bat` still starts the disabled Kite launcher at step [2/3].
- Net: the desk has no input tomorrow. Codex R07 showed that Dhan's output can't feed the desk even when connected. This adds that it has never been connected at all.

**N2 🔴 No launcher can ever count a session toward the 60.**
- The field-test desk that actually runs hard-codes `counts_session_gate: False` (`track2_daily_paper_desk.py:517`, `:550`).
- The only other launcher, `start_track2_phase1d_rehearsal.bat`, is explicitly "NON-COUNTING".
- No launcher runs `PROSPECTIVE_QUALIFYING` mode. `track2_gate_status.json` is fed by a different pipeline (session manifests) that nothing launches.
- So "0/60" is not a slow start. The counter is structurally unable to move.

---

## Tier B: the economics as coded

**N3 🔴 The notional cap overrides the ₹1,500 risk budget.**
- `calculate_position_size` caps every trade at ₹1,00,000 notional (`liquid_momentum_screener.py:348`, `:432`). For any stop tighter than about 1.5%, the cap binds before the risk budget does.
- Today's real RVNL candidate: 470 shares, ₹99,913 notional, `constrained_by: MAX_NOTIONAL_CEILING`. Risk to the stop trigger was **₹841**; to the SL-limit, ₹1,335. It was never ₹1,500.

**N4 🔴 Delivery costs are 0.2–0.7R per trade, and breakeven needs a 70–85% win rate.**
- Paper brackets default to `product_type="CNC"` (`track2_paper_execution.py:408`). Delivery charges 0.1% STT on both the buy and the sell.
- Computed with the project's own `calculate_transaction_costs`:

| Case | Round-trip cost | Cost in R |
|---|---|---|
| RVNL today, CNC, statutory only | ₹237 | 0.28R |
| RVNL today, CNC, +5 bps/side slippage | ₹~335 | 0.40R |
| RVNL today, MIS (intraday), +slippage | — | 0.22R |
| 0.5% stop, CNC + slippage | — | 0.68R |
| 1.0% stop | — | 0.34R |
| 2.0% stop | — | 0.17R |

- Breakeven win rate at RVNL's cost level:
  - **47%** if full 2R targets are hit.
  - **80%** for the typical two-tranche win (half at +1.5R, runner stopped at breakeven, +0.75R).
  - **85%** if the stop fills at the SL-limit price.
- Earlier estimates were 45–50% (F21, which assumed intraday costs and a ₹1,500 R) and 59–67% (Codex/CF-03). Both understated this.
- The alpha engine's docstring promises a "stop distance < 0.5%" rejection (`track2_alpha_engine.py:19`). No code implements it. The tightest-range setups get the worst cost ratio.
- The runner still moves to breakeven after T1 (`track2_paper_execution.py:662`). The "2×ATR runner" and "Supertrend/VWAP trailing" described in the dossier and chat do not exist in code.

**N5 🟠 "3 slots" is unreachable at normal sizing.**
- With the OMS/dossier configuration (₹75,000 buffer, ₹1,75,000 deployable), one ₹1L trade fits. The second is rejected with `TOTAL_CAPITAL_EXCEEDED` (`track2_portfolio_risk_governor.py:380`).
- With the terminal/HELM configuration (₹50,000 buffer), two fit.
- The governor rejects rather than downsizing. So the real portfolio is one position (or two, depending on which component you ask).

**N6 🟠 The live volume threshold is always 3.5×, never the 2.5× everyone quotes.**
- `BULLISH_EXPANSION` (2.5×) requires advance/decline breadth (`market_regime_filter.py:171`). The desk never passes breadth (`track2_daily_paper_desk.py:403`).
- So the regime is always either `NEUTRAL_SELECTIVE` (3.5×) or blocked. `ORDER_RULES` `min_volume_multiple: 2.5` is dead configuration.
- Today's events log shows 3.5× on every decision. Every report you received said "Required (2.5×)".

**N7 🔴 The rule is not an ORB. It fires after failed breakouts and at any time until 14:15.**
- `track2_orb_signal_adapter.py:25-28` takes the **latest** bar between 09:30 and 14:30. There is no "first break" condition and no invalidation.
- RVNL today:
  - 09:30 bar closed 212.40, above the OR high of 211.97. That was the breakout.
  - 10:00 bar closed **210.75, below the OR low of 210.79**. The breakout failed and the planned stop was hit.
  - 10:45 bar re-broke on 4.7× volume. That is what the desk signalled at 11:00.
- The signal window is also stated four ways: 09:30–10:30 (HELM and chat), 09:30–14:30 (alpha engine), 09:45–14:45 (desk), and "90% of entries by 10:30" (chat).

**N8 🟠 Replay of the desk's own rule: nothing resolves the same day.**
- I ran the desk's rule (3.5× regime, daily ATR, 20-day same-bucket median, one signal per symbol per day) over the 12 days that have a full 20-day baseline (07–23 Sep, 96 symbol-days).
- Result: **15 signals. Zero reached the stop or T1 the same day.** Every one ended the day open, which means an overnight CNC hold.
- Average same-day move after a signal: **+0.00R gross**, against about **0.13R** of statutory costs.
- n=15 proves nothing about edge. It does show two things:
  - This is a multi-day swing strategy with gap risk, not an intraday ORB.
  - It fires about 1.25 times a day across 8 names, but capacity is 1 position (N5).

**N9 🔴 No backtest exists, and the 20-fill gate can't detect an edge (extends F43 and Codex's "20 fills are not proof").**
- `grep -ri backtest` over the code returns nothing.
- Assume a per-trade SD of about 1.2–1.3R (an assumption, but typical for this payoff shape). Then 20 trades give a standard error of about 0.28R, and a 95% confidence interval of about **±0.55R**.
- To detect a +0.15R edge you need roughly **200 trades**. At current capacity that is more than a year of sessions.
- A backtest on 2–3 years of 15-minute data for all ~210 F&O stocks produces thousands of signals in a day. Kite Connect's ₹500/month plan includes historical data. Paper trading should then measure only execution (slippage, fills, markouts), not edge.

**N10 🟠 No event filter and no loss limit.**
- There is no earnings/results-day, ex-date, F&O-expiry, index-rebalance or block-deal filter. The volume multiple can't tell news or block volume from momentum. A midday 3.5× bar can be a single block deal.
- There is no daily loss limit, weekly drawdown stop, or consecutive-loss stop anywhere in the code.

**N11 🟡 The universe is hard-coded a second time, in the governor (extends F22).**
- `DEFAULT_SECTOR_MAP` lists 12 symbols (`track2_portfolio_risk_governor.py:31-49`). Any other F&O stock is rejected as `UNMAPPED_SECTOR` (`:223`). The "scan all 210 F&O stocks" upgrade can't trade any of them.
- `COARSE_SECTOR_GROUPS` is used only for display and selection, never in the position-time cap. The PSU/defence theme (IREDA, RVNL, COCHINSHIP, BDL, NATIONALUM) is split across four fine-grained "sectors", so the 2-per-sector cap never binds on the factor they actually share.

---

## Tier C: data and labels

**N12 🟠 ANGELONE was wrongly labelled a surveillance rejection all day.**
- ANGELONE is in **no** ASM or GSM list in today's official snapshot.
- The dynamic universe swapped ANGELONE for NATIONALUM, but the candle feed still carried the old 8.
- The desk labels any symbol outside the eligible set as `SURVEILLANCE_REJECTED` (`track2_daily_paper_desk.py:411-413`). So:
  - A stock with data was falsely marked as under surveillance.
  - NATIONALUM was "eligible" but had no data and was never evaluated.
  - The effective universe was 7.
- Meanwhile the chat called ANGELONE an "ACTIVE BREAKOUT".

**N13 🟡 Every stock's history is missing the 15:15 bar on all 32 days.** Stocks have 24 bars a day; Nifty has 25. The validator passes it. It doesn't affect entries, but end-of-day and CNC-rollover modelling use a 15:00 close.

**N14 🟡 The terminal shows 21-Sep's regime as today's.** `live_orb_status.json` was last written 21-Sep 15:39. The terminal reads its regime and Nifty OR with no date check.

---

## Tier D: fabrication that survived the "zero synthetic fallbacks" claim (extends R14)

**N15 🔴 A fake audit trail attributed to Claude.** `/api/audit` always starts with seven invented events (`track2_terminal_server.py:547-555`). They include "CDSL triggered ORB @ ₹1,400… Approved" and **"CLAUDE_AUDIT: Verified CDSL Tranche 1 fill @ ₹1,445.00 (+1.5R)"**. That trade never happened, and I never verified it.

**N16 🟠 More constants shown as live state in the terminal:**
- CDSL and IREDA are always shown as `IN_POSITION` (`:273`).
- The sector heatmap always shows CDSL and IREDA held (`:410-415`).
- Radar OR high/low are **LTP × 1.01 / 0.99**, not the real opening range (`:291-292`).
- Open risk is ₹1,500 × bracket count (`:201`).
- The funnel fallback shows "38 qualified, MAZDOCK added" (`:219-229`), and "surveillance passed" is computed as total minus 6.
- The fallback universe hash `e3b0c44298fc1c14…` is the SHA-256 of an **empty string**.

**N17 🟠 `/api/action/enter` writes a filled position into the canonical ledger.**
- Any HTTP body with a symbol, price and quantity becomes an `OPEN` order with `fill_price = entry` in `paper_orders.jsonl` (`:658-769`). It bypasses the signal engine and the E1/E2/E3 evidence rules.
- Its governor check assumes every existing position is exactly ₹1,500 risk and ₹69,000 notional (`:713`).
- It prices costs as intraday while the strategy is CNC (`:727`), understating STT about 4×.

---

## Tier E: security

**N18 🟠 The terminal control API has no authentication and no CSRF protection.**
- The `do_POST` handlers (`:507-523`) accept approve, reject, set_mode, squareoff, pause, enter and re-scan with no token and no Origin check. `_read_json_payload` parses JSON regardless of Content-Type.
- Any local process can call them. So can any web page open in your browser, subject to newer browsers' local-network permission prompts: a `text/plain` POST needs no preflight.
- Today that is a paper-ledger pollution risk. Once live, it becomes "a web page can switch you to AUTONOMOUS or flatten you".
- This is from code reading; I did not exercise it.

**N19 🟠 AGENTS.md says bypass permissions are revoked, but this Claude session is running with bypass permissions on.** The only enforcement is your desktop-app permission setting, not the file. You should choose that setting deliberately.

**N20 🟡 Secrets are in plaintext in the repo directory, readable by every agent.** The Telegram bot token is there now. The Dhan token will be too, and it is a **full trading credential**, the same risk class as F2's enctoken. It is gitignored, which is good, but gitignore is not access control.

---

## Tier F: live-deployment blockers nobody has listed

**N21 🔴 SEBI's retail-algo framework has been mandatory since 1 April 2026.** Every API order must come from a whitelisted **static IP**, with a session created from that IP, tagged under the broker's algo framework. This applies even below 10 orders per second. A home broadband connection with a dynamic IP will have its orders rejected, so live deployment needs a static IP or a cloud VPS. The code has no concept of this.

**N22 🔴 The stop can't live at the broker for this design.**
- Dhan's Forever/OCO orders can't be placed on shares **bought the same day**.
- In CNC, a resting sell-limit target plus a sell-SL stop for the same shares exceeds your holdings, so the second order is rejected.
- The two-tranche split doesn't map onto one Super Order.
- So stops would live only in the Python daemon on your PC. A power, internet or Windows-update failure leaves the position with no stop.
- Selling the day-2 runner from holdings also needs DDPI (or a daily eDIS/TPIN authorisation), which an unattended system can't do.

**N23 🟡 Tax is not modelled.** A T1 exit the same day as entry is speculative business income, taxed at your slab rate (ITR-3). A T2 exit after holding overnight is short-term capital gain at 20%. Expectancy should be measured after tax.

**N24 🟠 The broker facts behind the Dhan switch were wrong.**

| What the chat said | Actual (Sep 2026) |
|---|---|
| Dhan data API: free | ₹499 + GST per month |
| Dhan token: 30 days | 24 hours, renewed daily |
| Dhan limit: 500 instruments | 5,000 per connection |
| Kite Connect: ₹2,000 + ₹2,000 historical per month | ₹500 per month including historical; order API free |

If your real money will sit at Zerodha, Kite Connect at ₹500 keeps data, history and orders on one broker with one daily login.

---

## Tier G: the narration layer and the process

**N25 🔴 The chat narrator is an unverified second source of truth, and it contradicts the engine.** On 23-Sep it told you:
- The volume requirement was 2.5×. The engine used 3.5× (N6).
- ANGELONE was an "active breakout". The engine had excluded it (N12).
- At 09:42 it compared a 12-minute partial bar's volume to the full-bar 20-day median, which is not a like-for-like comparison.
- "Telegram will immediately receive the entry card". No code connects the desk's signals to the OMS or Telegram (R12). Today's RVNL signal never reached your phone.
- The runner "trails on 15m Supertrend/VWAP". That doesn't exist; the runner moves to breakeven.
- The engine "registers the stop at broker RMS". There is no broker code, and N22 shows it can't work that way.

You are making decisions from the narration, not from the engine's files.

**N26 🟡 Claimed remediations don't match the files.**
- AGENTS.md contains **no** Track 1 quarantine and no feature-freeze text, although step 6/9 of the remediation said "AGENTS.md updated / ratified". Rule 11 still describes Track 1 as live.
- Code was edited after the "freeze": the terminal server, screener and tests are newer than the brief.
- The capital base is undefined: the brief says ₹5,00,000, the code uses ₹2,50,000, the governor defaults to ₹1,00,000, and F20 used ₹1,00,000.

**N27 🟠 Core risk code was being edited during this audit, uncommitted and unreviewed.**
- The tree was clean when I started. At 20:43–20:44 IST another agent modified `track2_portfolio_risk_governor.py` and `hybrid_execution_oms.py`. This bypasses Rule 8 (peer review before core-model changes) and the claimed feature freeze, and it moves the baseline under any audit.
- The edit partly fixes R03 and R11: a missing margin rate from the OMS is now rejected. It also leaves problems:
  - `effective_margin_rate` is computed and never used.
  - Callers that omit the argument (terminal `/enter`, HELM) still pass silently, now with an assumed 20%.
  - Nothing produces a VAR+ELM value (NSE publishes it daily; no ingestor reads it). Once the OMS is wired to real signals, every candidate will be rejected as `INVALID_MARGIN_RATE`.
  - The OMS restart loader now **skips** corrupt ledger lines with a warning and carries on. Codex's R04 acceptance condition was to halt rather than show a smaller portfolio.
- My probe results are identical before and after this edit.

---

## Trivial but real

- `create_from_candidate` forces at least 1 share even when the risk per share exceeds the budget. The governor catches it later.
- The 14:30 bar can never be evaluated (the window closes at 14:45, when that bar seals).
- The desk shuts down for the whole day if fewer than 4 symbols are eligible.
- The surveillance fetch must finish before 09:00. A PC that boots late loses the whole session.
- There is no clock-sync (NTP) check, but decisions use 5-second and 90-second windows on the local clock.
- Synthetic depth uses Python `hash()`, which is randomised per process, so it differs between the terminal and any other reader.
- The terminal docstring says port 8766; the chat says 8767.
- GST is not applied to the SEBI turnover fee. The amount is negligible.

---

## What I would do, in order

1. **Answer the questions below first.** Several fixes depend on intraday vs swing, Zerodha vs Dhan, and the real capital.
2. **Backtest before any more paper sessions.**
   - Pre-register one rule set. Pull 2–3 years of 15-minute data for all F&O stocks (Kite Connect, ₹500).
   - Measure net expectancy after full costs, split by time of day and by first break vs re-break.
   - If that isn't clearly positive, stop Track 2 here.
3. **Fix the economics.**
   - Pick MIS (intraday, cheaper, exits the same day) or a real swing design.
   - Make the notional cap and the risk budget agree.
   - Enforce a minimum stop distance.
   - Either drop the runner-to-breakeven rule or model its payoff honestly.
4. **Make the rule an actual ORB.** Take the first break only, invalidate the day on a close below the OR low, and use one window, written once.
5. **Build one counting pipeline:** feed → desk → evidence → gate aggregator, with a prospective launcher. Until that exists, the paper phase can't produce evidence.
6. **Delete every terminal fabrication (N15–N17), put a token on the control API (N18), and make reports quote engine files only (N25).**
7. **Before live:** static IP or VPS, DDPI, a stop design that survives PC failure, a tax treatment, and a daily loss limit.

---

## Questions for Yashu

1. **What is the real capital:** ₹1L, ₹2.5L or ₹5L? And what total loss would make you stop the project?
2. **Where will real money sit, Zerodha or Dhan?**
3. **Intraday (MIS, closed by 15:15) or overnight swing (CNC)?** The code does CNC; the chat describes intraday plus 2 sessions. This one choice changes costs, tax and stop mechanics.
4. **Can you get a static IP, or would you run the live engine on a cloud VPS?**
5. **Is DDPI activated on your demat?**
6. **Can you watch the screen from 09:15 to 10:30 on market days?** Co-pilot mode depends on it.
7. **Will you accept a historical backtest as the first gate**, before counting paper sessions?

---

# Part 2: re-audit at 23:30 IST, design questions, institutional gaps and improvement plan

## 2.0 What happened to the code while I was auditing

- **20:43–20:59 IST:** another agent edited 16 files to remediate Codex R01–R15. The changes were uncommitted and unreviewed (N27).
- **23:36:35 IST:** 15 of those files were reverted to HEAD in a single operation, within 0.3 seconds. There is no `reset` in the reflog and no stash, so this was a file-level restore. **It wasn't me**; I ran no git command that changes files. About 400 lines of remediation are gone unless the agent that wrote them kept a copy.
- **The checkpoint safety net didn't catch it.** `agent_access.create_checkpoint()` writes to `..\swing-trades-checkpoints\`. The last three checkpoints (23-Sep 09:26, 09:27, 16:40) are `.partial`, meaning creation failed. The last complete backup is **21-Sep 23:27**. Each checkpoint is about 455 MB.
- **Consequence:** the tree is back at HEAD `54c2464`, plus one change: the test suite rewrote `execution_config.json` (N35). Every Part 1 finding (N1–N27) and every Codex finding (R01–R16) applies to the current tree. My second OMS probe run happened after the revert, so its results below are HEAD results.
- I reviewed the discarded patch before it disappeared. Section 2.2 lists what must change if it is re-applied.

## 2.1 New bugs in the current tree (HEAD)

**N28 🔴 Paper trading does not exist end to end (extends R12).** The pieces are four disconnected islands:
- The signal desk writes `candidate_<SYM>.json` and stops there. `append_unique_paper_orders()` has no caller.
- `HybridExecutionOMS.submit_candidate()` has **no production caller**, so no real signal ever becomes an intent, a Telegram card or an order.
- The bracket and exit engine `update_bracket_quote()`, which handles stops, targets, P&L and costs, has **no production caller**.
- Analytics read `paper_orders.jsonl`. The only production writers of that file are the terminal's manual `/enter` (writes `OPEN`, never closes it) and the OMS kill switch (writes exits with no P&L). No component can produce a closed trade with P&L.
- So the 60-session / 20-fill paper phase cannot generate a single paper trade result, even with a perfect feed.

**N29 🔴 From 24-Sep the desk fails every cycle, even with a valid Dhan token (extends R07 and N1).**
- `historical_candles_track2.json` has exactly one writer: the disabled Kite bridge (`track2_kite_bridge.py:102-105`). The Dhan bridge defines the path but never writes it.
- The desk requires that file's `end_date` to equal today, and its URLs to be Kite URLs (`track2_daily_paper_desk.py:186-213`).
- So every cycle from tomorrow raises "historical Kite candle provenance is invalid".

**N30 🔴 The Dhan bridge has no reconnect, and it certifies stale candles as valid.**
- When the socket closes (network blip, or the daily 24-hour token expiry), `MarketFeed.run` returns. The 1 Hz write loop keeps running while nothing reconnects. The feed stays dead until a human restarts it.
- Meanwhile `write_live_candles()` writes `data_valid: True` whenever any symbol has any bar (`dhan_feed_bridge.py:388`), with a fresh `local_write_time`. So the desk's 90-second freshness check passes on frozen data.
- A bar that was cut off mid-interval by the disconnect later looks "sealed". It is then evaluated with truncated volume and a stale close.

**N31 🟠 Dhan bars are not comparable to the Kite history they are measured against.**
- Bar high and low come from sampled LTP snapshots, not exchange highs and lows, so the live OR high is understated relative to the Kite-built baseline. The packet's day high/low fields are ignored.
- Bars are bucketed by local receive time (R08 still open).
- Staleness is not tracked per symbol. Probe E: the OMS accepted a quote whose last trade was 09:20.
- If the bridge starts after 09:30 (a late PC boot), there is no 09:15 bar, so every symbol is `DATA_INVALID` for the whole day.

**N32 🟠 "Watch all 210 F&O stocks", the reason for switching to Dhan, is not implemented.** The bridge subscribes to a hard-coded list of the original 8 (`DEFAULT_TRACK2_SYMBOLS`: still ANGELONE, no NATIONALUM), with `stream_all_fno: false`. It has no historical ingestion.

**N33 🔴 The kill switch erases losses, and killed trades then vanish from the statistics.**
- `emergency_flatten_all()` records every active order as `SQUARED_OFF` at `exit_price = entry_price` (`hybrid_execution_oms.py:576`).
- Probe C: a position entered at ₹1,200 with the market at ₹1,100 was recorded as exited at ₹1,200. **A ₹5,000 loss became ₹0.**
- These exit records carry no `net_pnl_rs`, so any P&L-based analytics skip them. The worst trades, the ones you killed, disappear from performance.

**N34 🟡 A "pre-armed" order dies 30 seconds after it is created** (`execution_policy.py:75`, `:193`). Probe D confirms it. "Arm it before the breakout" can't work unless the breakout arrives within 30 seconds.

**N35 🟠 Running the test suite rewrites the live execution mode.**
- `tests/test_hybrid_execution_policy.py` and `tests/test_hybrid_oms_callbacks.py` call `oms.set_mode()` 9 times without redirecting `CONFIG_PATH`. `save_config()` writes the canonical `antigravity/config/execution_config.json`.
- Its `updated_at` changed at 20:48:41 IST, the same second the tests ran. The live mode is whatever the last test left: currently **AUTONOMOUS**.
- This is F13 again, now for configuration. **For this reason I did not run the full test suite.**

**N36 🟡 Smaller issues in the current tree:**
- The OMS governor call passes `var_elm_rate=None` when the candidate lacks it, so the margin gate is skipped (R11 still open; probe A was auto-routed).
- R02 is still open at HEAD: probe B accepted all four pending signals.
- HELM's `get_status()` reports constant positions and risk (R14).
- `start_track2_paper_desk.bat` still launches the disabled Kite feed.

## 2.2 Review of the discarded 20:43–20:59 patch (if anyone re-applies it)

**Real improvements to keep:**
- Pending intents now reserve risk and capacity. R02 was verified fixed in probe B's first run: the 4th signal was rejected.
- `assessment.reason` crash fixed (R03).
- Routing only from `APPROVED`/`PRE_ARMED`, with an expiry check (R12).
- The OMS refuses a missing, stale (>12 s) or invalid feed instead of substituting the intended price (R01, partly).
- Codex `--sandbox workspace-write` (R15).
- Terminal macro values become `UNAVAILABLE` instead of fake Nifty/VIX.
- HELM counts come from the ledger.
- No universe hash for quarantined baskets (R10, partly).
- Analytics filter terminal states and dedupe.
- Dhan `NIFTY50` naming and a `requested_at` field; volume is computed as deltas (R08, partly).

**Bugs the patch introduced or left behind. Fix these before re-applying:**
1. `candidate_rate = 0.20` when missing, plus a hard-coded `var_elm_rate=0.20` at routing. This **undoes** the governor's new fail-closed check: an unknown margin is treated as 20%. `effective_margin_rate` is computed and never used. Nothing sources the real VAR+ELM file from NSE.
2. The kill switch still books **filled** positions as exited at the entry price (N33).
3. Dhan volume deltas start at 0 on the first tick after **any** start or restart, so the bar spanning a restart loses its volume. Candle validity becomes "any symbol configured", which is looser than HEAD.
4. The terminal still matches `"NIFTY 50"` while Dhan now writes `"NIFTY50"`, so the terminal's Nifty stays blank forever.
5. Terminal `/enter` blocks only on `DISTRIBUTION_GATED`. With regime `UNAVAILABLE`, entries are **allowed**, so an unknown regime means "go".
6. The surveillance loader accepts files up to **7 days old**, and a file with all-empty lists passes as "no surveillance". ASM/GSM change daily.
7. Analytics dedupe keeps the **first** terminal record per order. If tranche exits are separate records, the runner's P&L is dropped. The outer `except` still wipes all trades on any error.
8. The OMS restart loader **skips** corrupt lines and continues, which contradicts R04's "halt, don't shrink the portfolio".
9. HELM "trades today" counts every record in the file regardless of date.
10. Process: core risk models were edited with no review, no commit and no test run that you saw, and then lost.

## 2.3 Questions I would put to this design

These are the questions a risk committee at a real desk asks before it funds a strategy. Each one needs a written, evidenced answer.

**The edge**
1. Who is on the other side of this trade, and why do they keep losing to a 15-minute retail breakout rule in stocks that prop desks and HFT firms trade all day? If there is no answer, the default assumption is that there is no edge.
2. Where is the out-of-sample evidence across different regimes (a falling year, a rising year, a flat year)? Right now there is none (N9).
3. What result would make you **stop**? Write the kill criterion down before seeing any data.
4. Every parameter (15 minutes, 3.5×, 0.5 ATR, 1.5R, 2R, 3R, 50/50 split, ₹1,500, ₹1L cap): was it calibrated, or chosen? How many free parameters are there against how many independent trades?
5. Does the rule beat simple baselines? Examples: buying every liquid F&O stock at 09:45; buying the same stocks at random times with the same stops; holding Nifty beta.
6. Why these 8 stocks? Would the rule work on a random draw of F&O stocks chosen **before** looking at results (F23)?

**The design**
7. Is this an intraday strategy or a swing strategy? The signal is intraday, the product is CNC, and nothing resolves the same day (N8). The answer changes costs, tax, stops and gap risk.
8. Why take profit on half at +1.5R and move the rest to breakeven? Test that against a single exit. It is the biggest driver of the 80% breakeven requirement (N4).
9. Why is a re-break after a failed breakout (RVNL today) treated the same as a clean first break? Measure them separately.

**Risk**
10. What is the worst plausible day? For example: three PSU/defence names held overnight all gap down 8% on a policy headline. What is the loss in rupees, and is it survivable?
11. What is the daily loss limit, the weekly limit and the max drawdown, and who enforces them when the PC is off?
12. How much of the P&L is just Nifty beta? If most of it is, buying Nifty does the same job more cheaply.

**Execution**
13. What is the measured slippage, not the assumed one? What happens on the 5% of days you most need to exit (gap-down, cooling-off, no bids)?
14. Where does the stop live when the PC dies at 10:05 with a position open (N22)?

**Operations and governance**
15. Can every number on every screen be traced to a source file with a timestamp? Today it can't (N15, N16, N25).
16. Who approves a rule change? How is each rule version recorded so that results are never pooled across versions?
17. What stops the builder AI from writing, testing and approving its own work (F12, F41, N27)? Who reverts code, and why was 400 lines of work lost tonight with no backup?

**Is it worth it at this size?**
18. Suppose the strategy works at a plausible +0.15R net per trade, with R about ₹1,000 and about 15 trades a month at one-position capacity. That is about **₹2,250 a month, or 0.9%/month (about 11%/year) on ₹2.5L, before tax.** That is roughly what a Nifty index fund has returned long-run, with none of the concentration, gap risk or screen time. Scaling up, not the strategy logic, is what would make this worthwhile, and the capacity limits (N5) cap that. What is the plan?

## 2.4 What an institutional quant desk has that this setup lacks

| Layer | What a professional desk has | What this setup has | Gap |
|---|---|---|---|
| **Research** | Point-in-time, survivorship-free, corporate-action-adjusted data. A backtest engine with realistic costs. Walk-forward and out-of-sample tests. Parameter sensitivity. Multiple-testing correction. A research log of every test run, including failures | No backtest at all. 32 days of 15-minute data for 9 symbols | 🔴 Missing |
| **Market data** | Primary and backup feeds, with tick capture stored for replay. Exchange timestamps. Clock synced by NTP/PTP. Data-quality monitors (gaps, stale symbols, outliers) | One feed, never connected, no reconnect, local-time bucketing, snapshot OHLC, no NTP check | 🔴 |
| **Reference data** | Trading calendar and holidays, corporate actions, lot sizes, F&O ban list, price bands, daily VAR+ELM margins, earnings calendar, index rebalances | ASM/GSM/F&O list only (good). No calendar, corporate actions, margins or events | 🟠 |
| **Signal** | One canonical, versioned, deterministic implementation, replayable from stored inputs | Three implementations with different windows and parameters. Inputs archived with hashes (good) | 🟠 |
| **Pre-trade risk** | Hard limits in a separate process **and** at the broker: max order value and quantity, price-band and fat-finger checks, daily loss limit, position and exposure limits. Tested independently | An in-process governor with a 12-symbol sector map. No loss limits. Bypassable by `/enter` | 🔴 |
| **Order management** | An order state machine reconciled against the broker's order book and trade book (drop copy). Unique client order IDs. Retry and idempotency. Broker-side protective orders. Exchange algo tags | Paper only. No broker adapter, no reconciliation design, no protective-order design (N22) | 🔴 for live |
| **Post-trade** | Daily reconciliation of positions, cash and fees against the contract note. Transaction-cost analysis (implementation shortfall, markouts). P&L attribution (beta, sector, alpha, costs). Tax lots | None; no trade can even be produced (N28) | 🔴 |
| **Portfolio risk** | Factor and theme exposure, stress and gap scenarios, correlation limits, liquidity-adjusted exits. VaR/ES as a secondary measure | A 2-per-sector cap on a 12-name map that splits the PSU theme four ways | 🟠 |
| **Operations** | Always-on hosting (VPS/co-lo), UPS, backup internet, static IP. Monitoring derived from the same state as execution. Alerting with escalation. Runbooks. Incident post-mortems | One home PC. Dashboards show fabricated state. No runbooks for disconnect, token expiry or reboot. Backups failing | 🔴 |
| **Governance** | Model inventory. Change control (every rule change is a new version plus review plus backtest). Segregation of duties (builder ≠ validator ≠ approver). Independent validation. Human sign-off. Immutable audit trail | AIs build, test and approve their own work. Uncommitted edits to core models. Narrator contradicts engine. You never sign off | 🔴 |
| **Compliance** | SEBI algo framework (static IP, algo ID, kill switch), broker API terms, record retention, tax reporting | None of it modelled (N21, N23) | 🔴 for live |
| **Mandate** | Defined capital. Risk budget as a % of capital. Drawdown limits. A benchmark (usually the index) that the strategy must beat after costs and tax | Capital undefined (₹1L / ₹2.5L / ₹5L). No benchmark. No drawdown limit | 🟠 |

**Where effort went instead:** low-latency engineering (<15 ms routing, lock/CAS concurrency, "sub-10 ms binary feeds") for a strategy that decides every 15 minutes, plus dashboards, bots and a message bus. A desk would build these last, after research shows an edge, not first.

## 2.5 How to improve: a concrete plan with exit criteria

**Principles**
- A professional desk proves the edge on history first, then proves execution on paper, then proves operations with tiny real size. This project has been doing step 3's plumbing before step 1.
- Every number shown to you must be read from an engine file with a timestamp. Anything unknown shows as "UNAVAILABLE". Nothing is ever defaulted.
- One source of truth per concept: one signal implementation, one config, one ledger, one launcher.
- Every rule change gets a version number, a backtest re-run and a commit before it touches paper trading.

**Phase 0: stabilise (1–2 days)**
- Answer the 7 questions in Part 1. Commit the current tree. Fix the checkpoint failures, or rely on git commits instead.
- Stop the test suite writing canonical files (N35), and restore `execution_config.json` to `CO_PILOT`.
- Delete every terminal fabrication (N15, N16) and put a token on the control API (N18).
- Make the chat narration quote engine files only (N25).
- **Exit criterion:** a clean commit, `grep` finds no hard-coded market values, and the tests pass without touching `shared/` or `antigravity/config/`.

**Phase 1: research (1–2 weeks). The only phase that can tell you whether to continue.**
- **Data:** Kite Connect (₹500/month) gives 2–3 years of 15-minute bars for all F&O stocks and Nifty. Use point-in-time F&O membership, adjust for corporate actions, and store everything locally.
- **Backtest:** one simple engine using the full cost model (MIS and CNC variants), with slippage of 5–10 bps per side. When a bar hits both stop and target, assume the stop hit first.
- **Pre-register the hypotheses before running anything:**
  - (a) First-break 15-minute ORB, intraday MIS, exit by 15:10.
  - (b) The same rule with CNC and an overnight runner.
  - (c) First break vs re-break.
  - (d) Two-tranche vs single exit.
  - (e) Volume at 2.5× vs 3.5×.
  - (f) Baselines: buy at 09:45 with the same stop, and random entry times.
- **Report** net expectancy in R and rupees, with a confidence interval, by year, by time of day and by stock. Include max drawdown and the longest losing streak.
- **Exit criterion (decide the thresholds now):** for example, the lower 95% bound of net expectancy is above 0 in at least 2 of 3 years, it beats the baselines, and results stay stable when each parameter is moved ±20%. **If it fails, stop Track 2 or redesign it. Do not paper-trade a rule that loses on history.**

**Phase 2: build one live-grade paper loop (1–2 weeks), only if Phase 1 passes**
- **Pipeline:** feed (Kite Connect WebSocket, the same broker as your money) → bars (exchange time, true high/low, gap flags, reconnect with resync from the historical API) → the single signal implementation → hard pre-trade limits (including daily loss) → paper broker adapter (partial fills, no-fills, rejections, stop slippage, queue as an explicit estimate) → position manager → ledger → end-of-day reconciliation → analytics.
- Every stage reads and writes versioned files and can be replayed from archived inputs.
- **Exit criterion:** replaying a recorded day reproduces the same decisions and P&L byte-for-byte. Killing the process mid-session and restarting loses nothing and duplicates nothing.

**Phase 3: prospective paper (4–8 weeks). Measures execution, not edge.**
- Compare each paper day against what the backtest engine says for the same day. This "tracking error" is the thing you are measuring: slippage, missed fills, markouts at +1, +5 and +15 minutes.
- **Exit criterion:** live-vs-backtest slippage is within the cost assumptions used in Phase 1, and no fabricated or unexplained number appears in any report.

**Phase 4: tiny live pilot (3 months)**
- Static IP or VPS, the broker's algo registration, DDPI, and a protective-order design that survives a PC failure.
- Hard daily loss limit and a manual kill switch that actually cancels at the broker.
- The smallest size that still clears fixed fees.
- Scale only by pre-written rules, and only after real-money expectancy matches paper.

**Strategy design changes worth testing in Phase 1** (they are hypotheses, not recommendations to adopt blindly):
- Intraday MIS for an intraday signal: about 4× cheaper STT, no overnight gap, and stops resolve the same day.
- An entry order resting at the broker at OR high + buffer, placed once the OR bar seals. This removes human latency and the adverse selection of the co-pilot window.
- First break only. The day is invalid after a close below the OR low. One window, written once.
- Make sizing consistent: a risk budget that the notional cap can actually deliver, a minimum stop distance, and costs included in R.
- A single exit, or a trailing rule tested against it, instead of the untested 50% at +1.5R with breakeven.
- Event filters (results day, ex-date, expiry, index rebalance) and a daily loss limit.
- A rule-based universe chosen each morning from all F&O stocks using only prior-day data.

## 2.6 What is genuinely good and worth keeping

- The fail-closed desk refused stale data instead of trading on it. That is the most valuable behaviour in the repo.
- Every decision cycle archives its inputs with hashes, so decisions can be replayed.
- The surveillance preflight uses official NSE sources, with replay verification.
- The governor's structure (single-trade, aggregate and sector checks, with NaN rejection) is a sound skeleton that needs the right inputs.
- Rule 1 is a hard block at the policy layer.
- The adversarial review culture itself. It is working: it just needs a human approver and a backtest to anchor it.

---

## Reproduction

```
.venv\Scripts\python.exe "Claude outputs\2026-09-23_desk_audit_probes\probe_costs.py"
.venv\Scripts\python.exe "Claude outputs\2026-09-23_desk_audit_probes\probe_replay.py"
.venv\Scripts\python.exe "Claude outputs\2026-09-23_desk_audit_probes\probe_oms.py"
```

`probe_oms.py` runs the OMS in a temporary folder with an explicit config. It never touches canonical files. Results at HEAD:
- A: auto-routed with no margin check.
- B: 4th pending signal accepted (R02 open).
- C: the kill switch erased a ₹5,000 loss.
- D: pre-armed lifetime is 30 seconds.
- E: accepted a quote whose last trade was 09:20.

Both scripts only read repo files and write nothing. The 23-Sep regime, volume ratio and ANGELONE labels come from `shared/track2_liquid/field_tests/2026-09-23/events.jsonl` (event generated 11:00:11).

## Sources (fetched 23-Sep-2026)

- Dhan Data API pricing: https://dhan.co/support/platforms/dhanhq-api/how-does-the-dhanhq-data-api-subscription-work/
- Dhan token validity: https://dhan.co/support/platforms/dhanhq-api/what-is-the-maximum-validity-of-an-api-access-token-in-dhan-apis/
- Dhan live feed limits: https://dhanhq.co/docs/v2/live-market-feed/
- Dhan Forever/OCO (not on same-day buys): https://dhan.co/support/orders-and-positions/order-types/what-is-oco-can-we-place-stop-loss-and-target-together-for-positional-trades/
- Kite Connect ₹500 and free personal order API: https://zerodha.com/z-connect/updates/free-personal-apis-from-kite-connect and https://kite.trade/forum/discussion/15015/revising-kite-connect-fees-from-2000-to-500-per-month
- SEBI algo framework, static IP from 1-Apr-2026: https://inthemoneybyzerodha.substack.com/p/sebi-algo-trading-changes-april-2026
