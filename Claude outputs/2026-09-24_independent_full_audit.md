# Independent Full Audit: Project Swing Trades

**Auditor:** Claude, working independently. I read the files myself and did not rely on the Claude (F1–F47), Codex (R01–R16) or Antigravity reports.
**Date:** 24-Sep-2026, morning · **Tree:** branch `audit/2026-09-23-independent-reviews` at `fc73083`, plus uncommitted overnight edits (14 files, 05:13–05:17 IST).
**Companion:** `Claude outputs/2026-09-23_independent_desk_audit.md` (findings N1–N36). This report adds findings **A1–A38** and the full list of doubts.
**Method:** I read the code and documents, re-ran my own probes against the current tree, checked every numeric claim against its own source file, and verified external facts (NSE tick size) on the web. I changed no code, configuration or ledger.

---

## 1. What I read (coverage, stated honestly)

**Read in full or in all relevant parts:**
- **Docs:** AGENTS.md, README, `00_PROTOCOL`, `00_TAXONOMY`, `FAIL_CLOSED_CHECKLIST`, the master brief (sections 1–9), Track 2 `01_MARKET_MECHANICS`, `FIELD_TEST_RUNBOOK`, all three trade logs, the ChatGPT observation log, and the open-questions CHALLENGE index.
- **Track 2 signal and sizing:** `track2_daily_paper_desk`, `track2_orb_signal_adapter`, `liquid_momentum_screener`, `market_regime_filter`, `track2_alpha_engine`, `helm_session_orchestrator`.
- **Risk and execution:** `track2_portfolio_risk_governor`, `execution_policy`, `hybrid_execution_oms` (the version at HEAD and the overnight version), `two_tranche_exit_model`, and `track2_paper_execution` (costs, depth, queue and bracket logic).
- **Data and evidence:** `dhan_feed_bridge`, `track2_candle_collector`, `track2_official_source_ingestor` (parser), and in `session_manifest` the E3 evidence, session evaluation and gate counters.
- **Evidence pipeline and analytics:** `track2_verdict_aggregator` (gate condition), `track2_rehearsal_runner`, `caliber_performance_analytics`.
- **Operations and agents:** `track2_terminal_server`, `agent_access` (checkpoints), `tri_agent_bus` (review validation), `inbox_worker` (message gating).

**Read in part:** `track2_session_recorder` and `track2_session_coordinator` (only event types and the signal path), `track2_live_radar` (only thresholds and data sources), `exchange_circular_poller`, `track2_universe_scanner`, `circuit_rules` (bands and stage classifier), `telegram_alert_bot` (auth and commands only), and the test suite (isolation and assertions).

**Not read:** Track 1 models beyond `circuit_rules` (`risk_calculator`, `liquidity_gate`, `accumulation_screener`, `pre_open_auction_engine`, `pcas_execution`, `bse_price_band_poller`, `live_signal_engine`, `multi_stock_radar`, `delivery_absorption_analyzer`), `kite_web_depth_bridge` (disabled), `orchestrator/`, the adapters, `antigravity/analysis/` scripts, the terminal UI HTML, `claude/models/fill_model.py`, and the red-team harness.

Track 1 is sidelined, so I prioritised Track 2. If Track 1 is revived, it needs its own pass.

---

## 2. What I now understand the system to be

**Two systems are being built at once, and they have never met.**

1. **A research-evidence system** (session manifest, recorder, coordinator, aggregator). Rigorous in spirit. It seals rules before the open, captures market data with hashes, and would count a fill only if a trade-by-trade tape proves the queue cleared. Its gate decides whether real money is ever allowed.
2. **A trading-desk system** (paper desk, OMS, Telegram, terminal, analytics). It generates ORB signals, sizes them, routes intents, and displays P&L.

**The desk produces signals, but in a format the evidence system cannot count. The evidence system can count, but no signal ever reaches it (A14).** Around both sit a multi-agent build/review loop and a lot of documentation that has drifted away from the code.

---

## 3. Findings (A1–A38), grouped by what they break

### 3.1 Rule 1 (the qualification gate) cannot work as written. 🔴

- **A11. The "20 realistically fillable entries" can never be counted.**
  - E3 evidence requires `TRADE` events with unique `trade_id`s in the market stream (`session_manifest.py:1043-1051, 1089-1100`).
  - The recorder writes only `QUOTE` and `SIGNAL` events.
  - Neither Kite's web session nor Dhan's feed provides trade IDs; both give snapshots.
  - So `counts_toward_20` stays 0 forever with any data source the project has or plans.
- **A12. Even with a tape, an ORB entry would not qualify.**
  - Queue rank is derived only when a BUY's limit equals the best **bid**, i.e. a passive order (`:1075`).
  - An ORB entry is aggressive: it buys at or above the ask.
  - Separately, there is no exit or P&L evidence model at all. The "20" counts entries only.
- **A13. The gate never checks profitability.**
  - `gate_passed = PROSPECTIVE and trusted and sessions ≥ 60 and fills ≥ 20` (`track2_verdict_aggregator`). There is no expectancy, P&L or cost condition, although Rule 1 requires "verified positive net expectancy".
  - A session counts toward 60 with **zero signals and zero trades** if data capture is clean (`session_manifest.py:1447-1457`).
  - So the 60 measures recorder uptime, not strategy performance.
- **A14. The counting pipeline receives no signals.**
  - The only launcher for it (the Phase 1D rehearsal) calls `record_snapshot` only.
  - `coordinator.record_signal()` has no caller anywhere.
  - The field-test desk, which does generate signals, hard-codes `counts_session_gate: False`.
- **A15. Nifty is never recorded in the evidence stream.** The regime input behind every signal can't be reconstructed from evidence. Adding it would void sessions unless the frozen universe includes it (`:1304-1321`).
- **A16. The rehearsal ends the whole session on the first rejected snapshot.**

### 3.2 There is no single strategy. The same idea exists in several versions. 🔴

- **A18. Three ORB implementations with different rules, and four universe selectors.**
  - **Desk:** enters at the bar close, evaluates the latest bar, uses daily ATR, runs 09:45–14:45.
  - **VECTOR alpha engine** (HELM): not used by the desk.
  - **Live radar:** enters at OR high + ₹0.05, takes the first breakout, uses a static ATR%.
  - **Universe selection:** the premarket screener, the dynamic scanner, the universe scanner, and `screen_universe`.
- **A9. Three versions of the exit.**
  - The mechanics doc: breakeven at +1.0R, then the runner trails on the previous day's low or peak − 1.5 ATR.
  - The research exit model: "no trailing in Phase 1", with tranche 1 rounded up.
  - The paper bracket: runner to breakeven only after T1. The OMS rounds tranche 1 down.
- **A35. The only paper history ever produced used rules the engine no longer applies.**
  - The 16–18 Sep entries qualified at 2.53–2.90× volume under a 2.5× rule, and filled exactly at the OR high.
  - The running engine always requires 3.5× and enters at the bar close.
  - The 21-Sep session used Yahoo Finance data.

### 3.3 Execution realism errors that would matter with real money. 🔴

- **A26. Prices are never rounded to the exchange tick.**
  - Since 15-Apr-2025 NSE ticks are ₹0.01 below ₹250, ₹0.05 for ₹250–1,000, and ₹0.10 or more above that, reviewed monthly.
  - Limits, stops and targets are rounded to 2 decimals (for example ₹1,176.26). For BDL, CDSL and COCHINSHIP the exchange would reject them.
  - The synthetic depth ladder assumes ₹0.05 for every stock: a 12 bps spread on a ₹42 stock whose real tick gives about 2 bps.
- **A27. Stop logic contradicts itself.**
  - Sizing assumes a stop-limit filled at the worst case (stop × 0.995).
  - P&L books the stop exactly at the trigger (best case).
  - On a gap below the limit it books a fill at the gap price, but a real stop-limit would **not fill** and the position would stay open. That is the main danger of stop-limits, and it is never modelled.
- **A28. R is mis-scaled everywhere.**
  - Analytics compute each trade's R as P&L ÷ a fixed ₹1,500, not the trade's real risk (RVNL: ₹841). A full stop-out shows as −0.56R.
  - Expectancy, the breakeven curves ("36.06% / 66.97%") and Sharpe (which assumes 1.5 trades a session) all inherit this.

### 3.4 Risk and OMS changes made last night (uncommitted). 🔴

- **A21. An unknown margin rate now means 20%.**
  - The governor's signature defaults `var_elm_rate = 0.20`, and the OMS passes `candidate.get("var_elm_rate", 0.20)`. Nothing sources NSE's real VAR+ELM file.
  - My probe A: a candidate with no margin data was auto-routed.
- **A22. The new kill switch makes open positions vanish.**
  - It treats `OPEN` and `PARTIAL` as unfilled and records them `CANCELLED` with no exit. Elsewhere, `OPEN` means a held position.
  - My probe C: a losing `OPEN` position (entry ₹1,200, market ₹1,100) was recorded as cancelled, so the position and its loss disappear.
  - Other statuses fall back to the entry price when the feed is unavailable, which is when a kill switch gets used. Exit records carry no P&L.
- **A23. Credit:** the reservation for pending approvals (R02) is fixed in this version (the 4th signal is rejected). The 30-second pre-arm limit and per-symbol stale quotes are unchanged.

### 3.5 Data and universe integrity. 🟠

- **A29. TATACHEM is not an F&O stock**, per NSE's official list of 23-Sep, yet it is in the hard-coded F&O universe, the governor's sector map, and the "F&O bench" you were shown.
- **A17. The F&O ban list (MWPL) is not enforced in the path that generates signals.** It lives only in the surveillance monitor, which the desk doesn't use.
- **A19. The live radar falls back to Yahoo Finance 15-minute data.** It uses naive timestamps and silently drops bars. That radar's output drives the terminal's regime display.
- **A10. The runbook says incomplete sessions (under 25 bars) are rejected. No code checks this.** Every stock's history is missing its 15:15 bar.
- **A20. The NSE preflight parser raises on any duplicate symbol within a list.** One duplicate from NSE and the whole day is lost. (A doubt, not verified.)

### 3.6 Your own trade records disagree with themselves. 🟠

- **A4. CHANDRIMA has three versions.**
  - Master brief: **+₹2,750**, "−₹45 was a display artifact".
  - README: **−₹45**.
  - Ledger: **−₹45**, "net after charges".
- **A32. The ledger's CHANDRIMA entry has its own errors.**
  - Bought 26-Aug and sold 27-Aug, yet labelled "intraday".
  - −₹45 is exactly 4,500 × ₹0.01, the *gross* price difference. Delivery charges would add roughly another ₹100 or more.
  - Rule 9's "70.8% of daily volume" example compares the Aug 26–27 position with **10-Sep** volume.
- **A30. MOBIKWIK's paper stop was hit and ignored.** On 17-Sep its low was ₹193.95 against a ₹194.00 stop. The log says "stop safe (+6.2% cushion)" and keeps holding.
- **A31. The CROPSTER lower-circuit counts don't add up.** "11th / 12th / 13th consecutive lower circuit" (₹2.87 → 2.73 → 2.60, 15–17 Sep) is cited as "reaffirming Rule 5". The brief says the descent ended at ₹2.91 on 4-Sep, and seven more lower circuits from there cannot leave the price at ₹2.87.
- **A33. ANLON's "+₹184.20 realised"** is gross, with no charges. The fill is claimed at the limit price with no evidence. "ASM ST Stage 1 = T2T entrapment" is questionable.
- **A5.** The lower-circuit loss assumption is **25%** (README, brief expectancy grid) or **40.1%** (AGENTS.md Rule 5), depending on the file.
- **A7.** CCDL was bought at ₹1.32 on an SMS pump tip, against both the ₹10 floor and the no-tips rule. It is recorded as a baseline trade.

### 3.7 Governance, process and security. 🟠

- **A1. The builder edited the auditor's evidence overnight.**
  - The builder (Antigravity, presumably) edited Codex's committed probe script, uncommitted and under Codex's filename.
  - When the OMS now correctly refuses an order, the edited probe **writes a fake queued order** into the ledger so later checks run.
  - That breaks the protocol's "never edit another assistant's files", and it is the same pattern as F12.
- **A38. Requests that ask for confirmation are auto-discarded.** Any message to Antigravity containing "please confirm", "should I proceed" or "would you like me to" is rejected unprocessed (`inbox_worker.py:63-64, 799-817`). Its "anti-fabrication" check only confirms that an expected file exists, is non-empty and contains a marker string.
- **A25. A reply counts as a "review" if it exits cleanly and has ≥ 40 characters.** A 44-character rubber stamp passes. A genuine review that mentions "quota" or "rate limit" is rejected as a failure. The HMAC key is readable by every co-located agent, so a signature does not prove who wrote a message.
- **A34. The challenge mechanism stopped on 12-Sep.** No CHALLENGE entry exists for anything built 20–23 Sep: the OMS, Dhan, Telegram or hybrid execution. Several September challenges are still open.
- **A24. Backups fail during market hours and contain secrets.**
  - A checkpoint zips the whole repo (about 455 MB, including `.git`) and aborts if any file changes mid-zip, so it structurally fails during market hours.
  - A failed checkpoint blocks agent dispatch, and the leftover `.partial` files (up to 485 MB) are never cleaned up.
  - The zips include gitignored secrets (the Telegram bot token) and sit unencrypted outside the repo.
  - The last complete backup is from 21-Sep.
- **A36. The tests write your live settings and encode known defects.** The two OMS test files write the live `execution_config.json` (the current mode, AUTONOMOUS, was left by tests). Tests assert "a price touch equals a fill with profit", so the suite enshrines that defect.
- **A2, A3, A6, A8. Documents disagree with the code and with each other.**
  - The protocol (v1.0, read first by every agent) says "no assistant places an order" and gives the agents roles that no longer match.
  - The taxonomy lists a ₹50k buffer and port 8767; the code uses ₹75k and 8766.
  - The taxonomy maps SPLITLOCK, TRIPWIRE and both "NEXUS BUS" launchers to **4 files that don't exist**.
  - The execution-state model is described as 4, 6 or 8 states, depending on the file.
- **A37 (Track 1).** The upper-circuit "lock" classifier ignores whether offers are zero, although Rule 3 depends on that.

### 3.8 What I checked and found sound

- The E3 fill standard is well designed for what it tries to prove; it just can't be fed (A11, A12).
- The NSE parser is strict. Candle parsing validates OHLC, monotonic timestamps and timezones.
- The desk fails closed on stale data, and every decision cycle archives its inputs with hashes.
- The BSE band arithmetic (truncating inward to the tick) is reasonable.
- The overnight code fixes R02 (pending reservations).

---

## 4. Doubts that only you (Yashu) can resolve

1. **Capital.** Is it ₹1L, ₹2.5L or ₹5L? The docs and code use all three. What total loss would make you stop?
2. **Broker.** Where will real money sit: Zerodha or Dhan? Do you already pay for any data API?
3. **Holding period.** Intraday (closed the same day) or overnight swing? The code does overnight CNC, and the docs say both.
4. **Time.** Can you watch 09:15–10:30 on market days? Is this alongside a job or studies?
5. **CHANDRIMA.** What does your contract note actually say: −₹45, +₹2,750, or something else? And which dates?
6. **CROPSTER.** What were the exact fill time and price on 27-Aug? Do you have the contract note?
7. **CCDL.** Did you buy it because of the "DelightAdvsor" SMS?
8. **Last night.** Did you, or an agent you started, restore 15 files at 23:36:35 on 23-Sep? Did you ask Antigravity to work from 05:13 to 05:17 this morning, including editing Codex's probe script?
9. **Unattended agents.** Is Antigravity running on its own (inbox worker, scheduled tasks) without you watching?
10. **Approvals.** Have you personally approved any rule in AGENTS.md, or any "COMPLETE / APPROVED" phase in the Track 2 roadmap?
11. **Track 1.** Dead, or alive? AGENTS.md still treats it as live.
12. **Your goal.** Income, learning, or building a product you'd use or sell? This decides whether ₹2,000–3,000 a month of plausible profit (Part 2.3, Q18 of the companion report) is worth it.
13. **DDPI and static IP.** Is DDPI active on your demat? Can you get a static IP or run a cloud server?
14. **Tax.** Are you a salaried taxpayer? Intraday profits count as speculative business income.
15. **Source of truth.** Do you read the engine's files, or only the agents' chat summaries?
16. **The "₹5L multi-asset" and "Australia route" discussions.** Are they closed? I saw them only in pasted chat.
17. **Backtest.** Will you accept a historical backtest as the first gate?

## 5. Doubts about the system that need evidence (not opinion)

1. Which signal engine is canonical: the desk, VECTOR or the radar? Which universe selector?
2. Which exit model is canonical, and what is the intended runner behaviour?
3. Can the E3 standard ever be fed? No retail feed gives trade IDs. Should it be redesigned around snapshots, with explicit estimation labels?
4. Should the gate include a net-expectancy test with a confidence bound? Right now it has none.
5. Does NSE ever list one symbol twice in its ASM or GSM data? If so, the preflight fails for the day.
6. Who wrote the failure lines in `circular_poller.log` at 05:06 and 05:35 today: the real poller, or tests?
7. Q13, whether Kite allows SL-M on these stocks, has been open since 11-Sep. The whole stop design depends on it.
8. What are the real MIS square-off times at your broker? The code hard-codes 15:12 and 15:25.
9. What are the bhavcopy closes for CROPSTER from 25-Aug to 17-Sep, to settle the lower-circuit count claims?
10. Does ASM Short-Term Stage 1 really mean Trade-for-Trade? (I believe not; worth confirming from the NSE circular.)

---

## 5A. Status re-check at 09:31 IST (the tree changed again while this report was being written)

The working tree now has 22 modified files, a new untracked `research/` folder, and a Codex follow-up review (`shared/reviews/codex_remediation_followup_20260924.md`, verdict "PARTIAL REMEDIATION; BLOCKED"). None of it is committed. I re-ran my probes against it:

| Finding | Status at 09:31 |
|---|---|
| A1 (edit to Codex's probe) | **Reverted.** The file matches its committed version again. |
| A21 (unknown margin treated as 20%) | **Fixed in the OMS path.** A missing rate is now rejected (`MISSING_MARGIN_RATE`). Nothing sources NSE VAR+ELM yet, so the OMS now rejects every candidate. |
| A22 (kill switch makes positions vanish) | **Fixed for OPEN positions.** A losing position is exited at the market price (₹1,100). |
| A26 (no tick rounding) | **Partly fixed.** `ExecutionIntent` aligns to the tick using `research/execution_realism` (untracked). The desk, signal adapter and sizing still don't round, and the depth ladder still hard-codes ₹0.05. |
| A27 (stop-limit no-fill never modelled) | **Still open.** `UNFILLED_STOP_LOSS_RISK` appears only in the module docstring (line 9), not in the logic. |
| A28 (R fixed at ₹1,500) | **Fixed.** R is now computed from each trade's own risk. |
| Probe E (stale per-symbol quote accepted) | **Still open.** |
| A11–A14, A15, A18, A29–A34, A36, A38 | **Unchanged.** The files involved were not modified. |

The fixes are real, but they are uncommitted and unreviewed, and they bypass Rule 8 again. Production code now depends on the untracked `research/` folder.

## 6. The three things I would do first

1. **Freeze and commit.** No agent edits without you seeing the diff. Revert the edit to Codex's probe, or move it into Antigravity's own folder under its own name. Fix the tests so they stop writing live config.
2. **Decide the canonical strategy.** One signal engine, one exit model, one universe rule, written once. Delete or archive the others.
3. **Fix the gate before collecting any more evidence.**
   - Add an expectancy condition.
   - Make E3 achievable with the data you actually have, or replace it with a clearly labelled estimate.
   - Connect signals to the counting pipeline.
   - Then backtest before any paper session (companion report, Part 2.5).

---

Verified external source: NSE tick-size revision, effective 15-Apr-2025 (<https://zerodha.com/marketintel/bulletin/408151/revision-in-tick-size-for-nse-derivatives-and-cash-segment-from-april-15-2025>).
