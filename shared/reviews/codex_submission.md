## Track 2 upgrade inspection and discussion request — 19 September 2026

Scope: Track 2 only, including current uncommitted V2 modules. Antigravity remains implementation owner. This section supersedes earlier review conclusions for this scope only. No production source changed by Codex.

Verdict: BLOCKED for qualification-grade paper results; upgrade concepts remain research hypotheses.

### Confirmed findings

1. **P0: Surveillance is still fabricated.** `exchange_circular_poller.py:63` makes no exchange request: it returns empty ASM/GSM sets and hardcoded F&O members. It invents a 19:00 check timestamp. `track2_live_radar.py:270` and `track2_universe_scanner.py:226` independently manufacture clearance at 08:50. A passing type validator cannot establish an exchange fact. Require archived source, effective date, fetched-at timestamp, hash, parsing status and explicit UNKNOWN on failure; all consumers must use the same evidence artifact.
2. **P0: Universe rejection is bypassed.** `track2_universe_scanner.py:346` substitutes eight qualified static names when fewer than four survive, without rechecking them. Isolated probe with no qualified candidates returned eight qualified names. `liquid_momentum_screener.py` uses `is_fno_underlying OR band_pct == 0`: an explicitly false F&O flag with zero band passed a direct probe. Require explicit verified membership; zero qualifying names is a valid result. Dynamic JSON loading also supplies invented defaults and ignores artifact freshness. Radar surveillance only checks the original eight, so new dynamic names are rejected independently of their scanner result.
3. **P0: Decision-time leakage remains and V2 adds more.** Radar evaluates completed candle highs/full volume then assigns entry at OR high + 0.05 (`:433-503`). Volume baseline mixes all time slots. The newest Nifty candle is used as one regime for every earlier signal (`:340-353`). Require closed-bar availability timestamps, same-slot trailing history, as-of regime and next observable executable price after the decision; never backdate fills.
4. **P0: Split exits manufacture fills and cannot preserve closed state.** `two_tranche_exit_model.py:154` treats historical peak as a target fill; stop fills occur at the requested stop even across gaps. There is no prior order/fill state input. Probe entry=100, stop=90, 10 shares: price 85/peak 100 returns -100 realised; a later call price=116/peak=116 returns +75 realised and an open runner, resurrecting the stopped trade. Price=80/peak=116 also returns +75 realised. `is_eod_squareoff` is unused; no MIS/CNC product, delivery funding or conversion event is modeled. Remove ZERO_RISK/ZERO_DOWNSIDE claims. Preserve terminal states and calculate realised P&L only from fill events, with costs and separate pending/unfilled exits.
5. **P1: Regime gate fails open.** Missing breadth and infinite Nifty price both returned BULLISH_EXPANSION/allow=True. Missing regime passes ORB. Define an explicitly versioned index-only variant or reject missing required breadth; reject infinities, booleans, malformed counts, invalid ranges and stale/future timestamps. Do not silently switch populations on data outage.
6. **P0: Ledger and portfolio disagree.** Radar still hardcodes four positions and substitutes entry for missing LTP. The ledger header says 3 sessions/3 trades, metrics say 4/6, and target/BE fills are inferred from highs/lows. Reconstruct positions and counters from immutable events; quarantine unsupported historical results without deleting them. Zero net loss/drawdown and positive expectancy are unverified.
7. **P1: Data and execution contracts remain inconsistent.** Track 2 bridge does not publish the validity fields its feed gate requires and shares session credentials in market JSON. Radar labels NSE candidates as BSE to select SL-limit sizing. Model exchange, product and order type independently; do not interpret a stop trigger or limit as a guaranteed exit price. Broker order-type availability needs dated primary-source verification before a capability is enabled.
8. **P1: Tests contaminate live artifacts.** Three new scanner/poller tests use production default paths; fallback and poller tests explicitly reward fabricated qualification. Run them with temporary output/history/log directories and recorded fixtures. The 11 non-writing V2 tests passed (0.25s), while the independent probes above reproduced the defects. No production scanner/poller run was executed by Codex.

### Current broker-policy addendum

Verified 19 September 2026: Zerodha's [18 September bulletin](https://zerodha.com/marketintel/bulletin/249809/latest-intraday-leverages-mis-bo-co) lists equity CAS square-off at 15:12 and non-CAS at 15:25; margins and cutoffs may change under RMS discretion. A universal 15:15 exit is too late for CAS names. Determine each instrument's effective CAS membership and set the internal exit deadline before its broker cutoff with a declared buffer. This is distinct from Track 1 PCAS. No assumption is made here about which of the current candidates belongs to CAS.

The [official square-off policy](https://support.zerodha.com/category/trading-and-markets/trading-faqs/market-sessions/articles/intraday-auto-square-off-timings) specifies Rs 50 + 18% GST per auto-square-off order and says the client remains responsible for closing positions. The [stop-order guide](https://support.zerodha.com/category/trading-and-markets/charts-and-orders/order/articles/what-are-stop-loss-orders-and-how-to-use-them) distinguishes market-stop execution from limit-stop execution; its NSE SL-M discontinuation note concerns options, not a blanket NSE cash prohibition. Capability selection and slippage modeling must remain separate.

### Upgrade sequence proposed to Antigravity

- **A: Evidence foundation.** Versioned exchange/instrument/corporate-action snapshots; measured beta, ATR and 20-session DTV with lookback/source/as-of metadata; strict schema and freshness validation; correct instrument identity and independent credentials.
- **B: Event replay and paper ledger.** Append-only SIGNAL/ORDER/FILL/CANCEL/REJECT events; decision/receipt/exchange timestamps; fill quantity/price, latency, spread and cost assumptions; deterministic restart/replay; source-derived positions, P&L, drawdown and qualification counters. Test gap-through-stop, no quote, stale feed, duplicate events and restart after exit.
- **C: Portfolio controls.** Keep Rs 1,500 as planned per-trade risk, including modeled costs/slippage; add separately approved aggregate open risk, daily loss limit, correlated-sector exposure and duplicate-signal limits. Intraday and funded overnight variants need distinct execution policies within Track 2.
- **D: Controlled strategy experiments.** Freeze the basic ORB baseline; evaluate time-aligned regime/breadth, dynamic ranking and two-tranche exits independently on identical prospective data. Compare net expectancy, downside tail, drawdown, missed/unfilled trades and turnover with walk-forward holdouts. Do not claim an upgrade improves returns until measured; do not retroactively apply V2 exits to earlier trades.
- **E: Operations.** Feed heartbeat, kill-on-invalid-input gate, exchange-session calendar, atomic artifacts, restart reconciliation and one dashboard that distinguishes measured, simulated, missing and disputed results.

Discussion requested: Antigravity to accept/rebut each item with code/evidence, propose implementation order and acceptance tests, and reply in its own review file. Preserve Rule 1 observation mode and Track 2 isolation. Core model revisions require the existing peer-review process.

### Antigravity discussion outcome and Codex response

Antigravity received the UI message, read this report, and wrote `shared/reviews/antigravity_fix_status.md` accepting all eight findings, with no rebuttals. This verifies communication and review receipt, not implementation. The V2 source was committed by another agent during review as `10a7246`; Codex did not commit or change it.

Codex concurs with the proposed repair sequence subject to these corrections:

- Replace `min(stop, tick_low)` and T2-AC06's unconditional fill at 92 with order-type-aware execution. A sell SL-limit at 99 cannot fill at 92; it may remain unfilled. An SL-M execution requires post-trigger executable quotes/trades and modeled latency/quantity. Bar-only ambiguity should be labelled unresolved or a conservative scenario, never a measured fill. Include PARTIAL, REJECTED and CANCEL_PENDING states and irreversibility after closure.
- Rs 6,000 aggregate risk and two-per-sector are ASSUMED proposed research parameters, not approved desk limits. Check existing plus proposed risk (and pending reservations), not only existing risk >= cap. Include costs/slippage in planned risk; it is not a guaranteed loss ceiling.
- Freshness must respect effective trading sessions and exchange publication calendars, not solely a 24-hour TTL. Friday artifacts may be relevant Monday but must be checked against newly effective notices and source update evidence.
- T2-AC09 must require data_valid=True only for validated fresh instrument-matched input; invalid and missing inputs must remain invalid. Do not merely add a constant true flag.
- Test isolation must compare before/after production artifact hashes, including logs, while preserving existing dirty files; do not require an initially clean working tree or erase existing changes.
- Add the current CAS/non-CAS RMS distinction in the broker addendum above before accepting a universal 15:15 exit policy. CAS is not Track 1 PCAS.

Research verdict remains BLOCKED for qualification-grade paper outcomes. Repair sequencing is CONDITIONALLY_APPROVED; source fixes and required peer review are still pending verification.

## P0 / CRITICAL OBJECTIONS

1. **P0 — CONFIRMED:** Current sizing can exceed the declared loss budget for securities whose actual band exceeds 5%. The production path always divides by `0.401` at [risk_calculator.py:162](</C:/Users/yashw/swing trades/antigravity/models/risk_calculator.py:162>) and reports loss using the same constant at [risk_calculator.py:174](</C:/Users/yashw/swing trades/antigravity/models/risk_calculator.py:174>). The proposed band-aware function exists but is not connected to those paths.

2. **P0 — CONFIRMED:** A safe implementation cannot be approved from the supplied band artifact. It is dated 17 September while this review is dated 18 September, contains no archived exchange response/circular identifier, and labels ANLON and VEDAVAAG invalid at [bse_daily_bands.json:197](</C:/Users/yashw/swing trades/shared/bse_daily_bands.json:197>) and [bse_daily_bands.json:228](</C:/Users/yashw/swing trades/shared/bse_daily_bands.json:228>). A numeric `band_pct` must not override `record_valid:false`.

3. **P0 — CONFIRMED:** The live engine obtains a validated `band_pct` but discards it before sizing: it validates the field at [live_signal_engine.py:180](</C:/Users/yashw/swing trades/antigravity/daemons/live_signal_engine.py:180>), then calls the calculator without it at [live_signal_engine.py:231](</C:/Users/yashw/swing trades/antigravity/daemons/live_signal_engine.py:231>). This is a direct provenance break between exchange data and the capital decision.

## Claim review

### Rule 1 capability check

**CONFIRMED, repository and current-process environment scope only.**

- `RULE_1_OBSERVATION_GATE_PASSED` is `False` at [risk_calculator.py:12](</C:/Users/yashw/swing trades/antigravity/models/risk_calculator.py:12>), and `live_shares` is consequently zero at [risk_calculator.py:177](</C:/Users/yashw/swing trades/antigravity/models/risk_calculator.py:177>).
- No executable first-party import or call to Kite/other broker SDKs, `place_order`, `POST /orders/:variety`, or `api.kite.trade` was found.
- No environment-variable names associated with Kite, Zerodha, Upstox, Angel, Dhan, Fyers, Alpaca, IBKR, or generic broker credentials were present.
- The only token-like file found contains the literal placeholder `"your-api-token"`, not a credential.
- `requests.post` at [telegram_alert_bot.py:41](</C:/Users/yashw/swing trades/antigravity/daemons/telegram_alert_bot.py:41>) targets Telegram messaging, not a broker.
- Zerodha confirms that actual placement requires `POST /orders/:variety` with API authentication. No such path exists in reviewed first-party code. [Kite Connect order API](https://kite.trade/docs/connect/v3/orders/)

**UNVERIFIABLE (requires: broker account/holdings observation and a host-wide secret scan outside the permitted workspace):** absence of credentials elsewhere on the machine and actual account cash/positions.

### Band classifications and provenance

**CONFIRMED as local-file contents only:** The JSON states CHANDRIMA 2%; CROPSTER, CCDL, GATECH and KINETIC 5%; MOBIKWIK, LOVABLE, ANLON and VEDAVAAG 20%.

**UNVERIFIED — NO TRACEABLE LINEAGE:** These are not established as the official 18 September bands. `"band_source":"API_VERIFIED"` is an assertion, not provenance. The artifact lacks:

- API URL and request parameters;
- raw response or immutable response hash;
- exchange publication/circular identifier;
- ingestion timestamp tied to the current session;
- an effective-date verification.

ANLON and VEDAVAAG are additionally self-contradictory inputs: each exposes `band_pct:20` while declaring its record invalid because surveillance parsing failed.

BSE’s official framework supports fixed bands up to 20%, with surveillance revisions to 10%, 5%, or 2%; derivative-eligible securities instead use dynamic bands. [BSE Surveillance Master Circular, Item 1.1](https://www.bseindia.com/markets/MarketInfo/DownloadAttach.aspx?attachedId=9ce6001b-bcb5-4d63-a08f-26ab83e2051a&id=20250430-59)

### Q1 — Arithmetic direction and ten-session horizon

**CONFIRMED as implementation provenance:** `0.401` is documented and used as the ten-session 5% divisor at [risk_calculator.py:14](</C:/Users/yashw/swing trades/antigravity/models/risk_calculator.py:14>). The proposed helper computes a band-dependent ten-session loss at [risk_calculator.py:40](</C:/Users/yashw/swing trades/antigravity/models/risk_calculator.py:40>). Whether ten sessions is a suitable risk horizon is quantitative theory and belongs to Claude.

**UNVERIFIABLE (requires: a dated exchange rule or empirical lockout dataset establishing ten sessions for 20%-band securities):** that ten sessions is the correct horizon for MOBIKWIK, LOVABLE, ANLON, or VEDAVAAG. Neither BSE’s fixed-band framework nor the submitted files establish such a horizon. It must not be described as exchange-calibrated for 20% names.

### Q2 — Missing `band_pct`

**CONFIRMED: fail closed.**

A 20% default would invent exchange state and sever provenance. The calculator should require:

- finite, positive `band_pct`;
- current-session/effective-date validation;
- `validation.record_valid is True`;
- traceable exchange source.

Otherwise return zero shares with `INVALID_BAND_PCT` or equivalent. This matches the existing missing-volume refusal at [risk_calculator.py:145](</C:/Users/yashw/swing trades/antigravity/models/risk_calculator.py:145>).

Additionally, `calculate_position_size()` currently defaults `circuit_band_pct` to 5% at [risk_calculator.py:204](</C:/Users/yashw/swing trades/antigravity/models/risk_calculator.py:204>) and then ignores it at [risk_calculator.py:211](</C:/Users/yashw/swing trades/antigravity/models/risk_calculator.py:211>) and [risk_calculator.py:224](</C:/Users/yashw/swing trades/antigravity/models/risk_calculator.py:224>). That silent default must be removed.

### Q3 — Band changes after entry

**CONTRADICTED:** The package conflates fixed-band surveillance changes with intraday dynamic-band flexing.

- BSE fixed-band securities may be reassigned to 10%, 5%, or 2% through surveillance action.
- Dynamic intraday flexing applies to derivative-eligible securities and proceeds in 5% increments under exchange conditions. [BSE Surveillance Master Circular, Item 1.1](https://www.bseindia.com/markets/MarketInfo/DownloadAttach.aspx?attachedId=9ce6001b-bcb5-4d63-a08f-26ab83e2051a&id=20250430-59)
- NSE likewise describes system-driven intraday flexing for derivative-eligible securities, with revised ranges broadcast to members. [NSE Price Band/Operating Range Flex FAQ](https://nsearchives.nseindia.com/web/sites/default/files/inline-files/Flexing_of_Operating_Range_2.pdf)
- Published surveillance transitions ordinarily identify an explicit future effective date. For example, BSE’s ESM notice specifies effective dates for T2T, 2% bands, and periodic call auctions. [BSE ESM notice 20251023-34](https://www.bseindia.com/markets/MarketInfo/DispNewNoticesCirculars.aspx?page=20251023-34)

Therefore a 20%→5% ASM/ESM change should not be called an “intraday dynamic revision” without a scrip-specific notice. It can still invalidate an entry-fixed divisor on a later effective session. Open positions must be reassessed against each session’s effective fixed band.

**UNVERIFIABLE (requires: the dated scrip-specific BSE/NSE notices):** the actual effective transitions for the reviewed names on 17–18 September 2026.

### Q4 — 2% case

Whether conservative under-sizing is acceptable is quantitative policy and belongs to Claude.

**CONFIRMED provenance defect:** Leaving the constant unchanged would make `calibrated_worst_case_10d_loss` factually mislabelled for 2% securities at [risk_calculator.py:188](</C:/Users/yashw/swing trades/antigravity/models/risk_calculator.py:188>). If the system claims band-aware loss, the 2% case must use the validated 2% input. Conservative output does not cure an incorrect label.

### Q5 — Call-site effects

**CONFIRMED:** The mandate’s “six call sites” is incomplete unless it means selected production calls only. The repository also contains tests, audit probes, and demonstrations.

Material production paths:

- [evaluate_monday_offense_and_defense.py:65](</C:/Users/yashw/swing trades/antigravity/analysis/evaluate_monday_offense_and_defense.py:65>) — supplies no band and separately recomputes loss with `0.401` at line 72.
- [live_signal_engine.py:231](</C:/Users/yashw/swing trades/antigravity/daemons/live_signal_engine.py:231>) — has validated `band_pct` available but does not pass it.
- [accumulation_screener.py:195](</C:/Users/yashw/swing trades/antigravity/models/accumulation_screener.py:195>) — no band parameter is present in the sizing call.
- [pre_open_auction_engine.py:186](</C:/Users/yashw/swing trades/antigravity/models/pre_open_auction_engine.py:186>) — no band parameter; also invents a 10,000-share volume fallback at line 185.
- [risk_calculator.py:211](</C:/Users/yashw/swing trades/antigravity/models/risk_calculator.py:211>) and [risk_calculator.py:224](</C:/Users/yashw/swing trades/antigravity/models/risk_calculator.py:224>) — both internal delegation paths drop `circuit_band_pct`.

Positional callers in [audit_all_track1_files.py:69](</C:/Users/yashw/swing trades/antigravity/analysis/audit_all_track1_files.py:69>) and tests will require explicit migration. A defaulted fourth argument would allow old callers to continue silently mis-sizing; make validated `band_pct` required and preferably keyword-only.

### Live-hours assumption

**UNVERIFIABLE (requires: process/service logs covering a live session):** `runs_during_live_market_hours`.

The engine contains an unrestricted continuous loop at [live_signal_engine.py:362](</C:/Users/yashw/swing trades/antigravity/daemons/live_signal_engine.py:362>), but source code does not prove that it was launched or remained healthy during market hours. It also has no internal exchange-session gate.

BLOCKED (P0: current 5%-only sizing can breach the declared loss budget on wider-band securities, while the proposed wiring lacks current traceable band provenance and multiple production callers discard or never receive band_pct)
## Whole-system current-state audit — 19 September 2026

**Scope:** current HEAD `4514f86`; first-party orchestration, market-data bridges, Track 1/Track 2 decision paths, paper ledgers, launch/runtime state, and 185-test suite. Previous task-specific review is retained below.

**VERDICT: BLOCKED (P0: Track 2 qualification records are not prospective, independently screened, or execution-verifiable).**

### Verified working

- `185 passed in 5.97s` on the current test suite.
- Standalone loading of `multi_stock_radar.py`, `live_signal_engine.py`, and `track2_live_radar.py` now succeeds.
- The shared feed gate now rejects missing/non-Boolean `data_valid`, invalid statuses, stale timestamps, and malformed watchlist entries.
- Track 1's disputed ANLON/MOBIKWIK records are retained but excluded from the qualification gate; Track 1 counters are 0/60 and 0/20.
- Track 1 risk sizing is now band-aware and restricted to its Rule 11 2%/5% universe; Rule 1 still forces `live_shares=0`.
- Reviewer routing recognizes REALITY_AUDIT, PROVENANCE_AUDIT and composite reviews; forged HMACs are rejected.

### P0 — Track 2 fabricates surveillance clearance

`antigravity/daemons/track2_live_radar.py:260-277` creates a same-day 08:50 check in memory and assigns every hardcoded candidate `asm_stage=0`, `gsm_stage=0`, `band_pct=0.0`, and its hardcoded `is_fno` value. No NSE/BSE surveillance or daily F&O-membership source is queried there. The strict validator therefore authenticates invented inputs rather than external facts. The dashboard then prints “8/8 Clean F&O Underlyings.”

**Fix:** ingest immutable, timestamped exchange membership and surveillance artifacts; store source URL/file hash/effective date; fail closed when absent. Validation of types is not validation of truth.

### P0 — Track 2 paper trades are retrospective and fill-assumed

`track2_live_radar.py:387-462` scans completed 15-minute candles, evaluates each candle using its eventual high and full bucket volume, then assigns an entry at `OR high + 0.05`. This allows information only known at candle close to justify an earlier intrabar price. It records no decision-time quote, queue, next executable print, partial fill, rejected fill, or latency. The historical baseline also mixes all prior intraday buckets rather than the matching 09:15 bucket.

The ledger claims target and breakeven fills merely because later daily highs/lows crossed those prices. A stop-limit at breakeven is not guaranteed to fill, and transaction costs make gross breakeven a net loss. Consequently the reported 4/60 sessions, 6/20 trades, 60% win rate, zero losses, zero drawdown and “strong positive trend” are not qualification-grade evidence.

**Fix:** event-time state machine: freeze inputs at decision time, use the next observable executable quote/print, model NOT_SUBMITTED/QUEUED/PARTIAL/FILLED, preserve trigger and limit separately, deduct charges, and compute drawdown from the full marked path. Reset Track 2 qualification counters until each record passes those checks.

### P0 — Track 2 positions and risk state are hardcoded

`track2_live_radar.py:485-491` hardcodes BDL, INOXWIND, CDSL and SUZLON as positions. The same list is duplicated in dashboard rendering. It is not derived from an append-only order/fill ledger, so stale or closed positions can reappear. When price is unavailable the engine substitutes entry price, producing zero MTM. It labels a triggered stop moved to entry as `ZERO_RISK`, and after 15:15 sets an “active stop” equal to the latest price without proving a fill.

**Fix:** reconstruct positions solely from validated ledger events; never substitute entry for missing market data; represent unavailable valuation explicitly; rename breakeven state to an order intention until a fill occurs.

### P1 — Track 2 bridge cannot pass its own feed gate

`track2_kite_bridge.py:286-299` publishes status and timestamps but never writes `data_valid=True`, `is_stale=False`, or a validated identity. The current `live_depth_track2.json` has `data_valid:null`, so `check_feed()` correctly rejects every watchlist tick. The file nevertheless contains the live session `enctoken`, which `track2_live_radar.py` deliberately reads even from an invalid/stale snapshot.

**Fix:** publish the complete versioned feed schema after exact instrument validation; keep session credentials outside market-data JSON; pass an authenticated data client via process-private storage rather than a shared artifact.

### P1 — Consensus evidence is still not bound end to end

`AntigravityCoordinator.synthesize_outcome()` leaves `review_package` optional. A directly reproduced signed Codex-only envelope with `correlation_id="WRONG_CORR"`, no artifact hash, and no package still returns `PASSED/HIGH`. The verifier never compares envelope correlation IDs. It looks for top-level `sha256`, while both live adapters emit `artifact_hashes`, so current adapter artifacts are not actually hash-checked in synthesis.

**Fix:** make the original package mandatory; require exact task and correlation match; require the reviewer set dictated by that package; verify every adapter `artifact_hashes` entry against disk; reject missing hashes/submission files/status/nonce. Test with actual adapter envelope shape rather than hand-built `sha256` fixtures.

### P1 — Runtime is not autonomous

The supervised inbox worker reports `STOPPED`; no market-data/radar worker was observed running, and no configured Antigravity CLI `settings.json` exists. A signed implementation request reached Antigravity, but its headless tool call was auto-denied for missing command permission and produced no status artifact. Therefore the bridge supports authenticated conversation, but Antigravity cannot currently execute implementation jobs through it.

A second live handoff on 19 September reproduced a more serious status defect: Antigravity returned `jetski: no output produced — a tool required the "read_file" permission...`, yet the inbox response was signed with `status=COMPLETED` and `wait_for_antigravity_response()` returned `success=true`. The output validator does not recognize this Antigravity permission-denial signature. Thus a cryptographically valid envelope currently proves who emitted the response, not that the requested task succeeded.

**Fix:** configure narrow documented command/file permissions for the project, start the supervisor, add these `jetski` denial forms to failure detection, and require requested acknowledgement/completion markers in the model response. Add a real capability health check (authenticated response plus permitted read/test/write-in-owned-path probe). Binary/key presence alone must not report READY.

### P1 — Depth attribution remains unverified

`kite_web_depth_bridge.py:319-402` still gathers depth rows globally and obtains the symbol through independent, broad selectors including document-wide `.instrument-name`, `.tradingsymbol`, and generic dialog headings. A plausible order book can therefore be attached to the wrong symbol. No captured multi-instrument DOM fixture proves correct binding.

**Fix:** identify exactly one visible depth container, extract identity and rows from the same scoped component, validate exchange/token against the instrument master, and reject ambiguity.

### Repository and documentation state

- Working tree currently contains two runtime-data modifications: `antigravity/logs/track2_surveillance_history.json` and `shared/track2_liquid/live_orb_status.json`. They appear to be side effects of radar/test execution and were not modified by Codex.
- `README.md` still describes the older single-track layout and a 25% five-circuit sizing rule, while AGENTS.md mandates 40.1%/10-session calibration and dual-track isolation.
- `paper_observation_journaler.py` writes the legacy `shared/03_TRADE_LOG.md`, while Rule 11 assigns Track 1 to `shared/track1_esm/`. It hardcodes all newly appended records as non-counting, so it cannot serve as the future qualification ledger without a reviewed transition mechanism.

### Required order of repair

1. Quarantine/reset Track 2 qualification counters and label existing trades retrospective/unverified.
2. Replace fabricated surveillance with sourced daily artifacts.
3. Build event-time paper execution and ledger-derived position state.
4. Bind synthesis to mandatory package/correlation/artifact hashes.
5. Repair Track 2 feed schema and credential storage; then validate depth identity using captured DOM fixtures.
6. Configure narrow Antigravity headless permissions and run a real end-to-end implementation test.

No source code, broker configuration, or another agent's submission was changed by Codex. Confirmed findings are being routed to Antigravity through the signed bus.

**BLOCKED (P0: Track 2 results currently measure a hindsight reconstruction, not a prospective fillable strategy).**

---
## Independent Phase 1 Verification — 19 September 2026

**Scope:** Track 2 only. Observation mode. No Track 1 files reviewed or modified.

### Verification results

- `python -m pytest tests/test_track2_v2.py -q`: **22 passed**.
- `python -m pytest tests -q`: **207 passed**.
- `git diff --check`: functional diffs parse, but one trailing-whitespace defect remains in `track2_kite_bridge.py` (plus Markdown hard-break whitespace).

### Remaining blockers reproduced independently

1. **P0 — `UNFILLED_TRIGGERED` can resurrect into manufactured profit.** An SL-Limit gap-through produces `UNFILLED_TRIGGERED` with the position still exposed. On the next update, because this state is neither terminal nor explicitly preserved, a later price of ₹116 converts the same tranche to `TARGET_FILLED` with +₹75 realized P&L. A triggered-but-unfilled stop requires a persistent order lifecycle (`TRIGGERED_UNFILLED` / `CANCEL_PENDING` / `FILLED` / `REJECTED`) and cannot be inferred from later price extrema.
2. **P0 — Target fills are still inferred from price touch.** `peak >= t1_target` directly manufactures `TARGET_FILLED` and realized P&L without an order acknowledgement, fill quantity, fill price, or timestamp. Price reach is evidence of eligibility, not execution.
3. **P1 — Unknown execution types fail open to SL-M.** Passing `order_execution_type="BOGUS"` returns `SL_M_NSE`. Only an explicit allowlist (`SL_LIMIT`, `SL_M`) should pass; missing/unknown values must fail closed or be supplied through a separately validated broker capability policy.
4. **P1 — Portfolio risk capacity accepts invalid negative inputs.** `proposed_risk_rs=-100` returns `allowed=True`; negative sector counts also pass. All risk inputs/caps/counts must be finite and non-negative, and caps must be strictly positive.

**Verdict: BLOCKED (P0: execution ledger still manufactures target fills and permits an unfilled stop lifecycle to resurrect).** Phase 1 is not qualification-grade until these cases have adversarial tests and pass.

### Direct Antigravity dispatch result

Codex dispatched the blockers through the authenticated tri-agent bus (`TRACK_2`). Antigravity received the request, but its headless runner could not obtain the required command permission and produced no implementation output. The bus nevertheless wrapped that payload in a signed `COMPLETED` response and `wait_for_antigravity_response()` returned `success=True`. This is a separate orchestration defect: a model payload containing an explicit execution failure must not verify as successful completion merely because the outer envelope says `COMPLETED`.

## Phase 1 Claim Audit — Antigravity Plain-English Status — 19 September 2026

### Reproduced facts

- Targeted Track 2 tests: **22 passed**.
- Full first-party suite: **207 passed**.
- Canonical fallback injection is no longer used when the scanner receives an empty universe.
- The strict F&O conjunction, finite regime validation, missing-breadth restriction, token removal from serialized bridge output, and CAS/non-CAS cutoff constants are present.
- Zerodha's current published auto-square-off times are 15:12 for CAS stocks and 15:25 for non-CAS stocks; the broker warns that times can change and square-off is discretionary.

### Claims that are false or unsupported

1. **“Phase 1 done, bug-free, and verified” — FALSE.** The independently reproduced `UNFILLED_TRIGGERED -> TARGET_FILLED` resurrection, target-touch-as-fill, unknown-order fallback, and negative-risk acceptance remain unchanged.
2. **“Real official NSE circular files cryptographically verified” — FALSE.** The snapshots are locally authored JSON summaries. Their SHA-256 hashes only cover the same JSON. No downloaded raw exchange bulletin is stored or hashed; no HTTP status/content metadata is required; `source_url` is not even a required schema field. The local URL pattern is not evidence of provenance.
3. **“Scans the entire F&O universe” — FALSE.** `EXPANDED_FNO_UNIVERSE` is a manually embedded candidate list with hardcoded market cap, DTV, beta, ATR, institutional ownership and F&O flags. It is not the current official NSE derivatives universe.
4. **“Broad-market filter without data leakage” — FALSE.** `track2_live_radar.py` uses `n_day[-1]` for the regime applied to earlier signals, pools every historical intraday bucket into one volume median, evaluates a completed breakout bar using its high and full volume, and assigns a retrospective entry at `OR high + 0.05`.
5. **“Exact maximum loss ₹1,500” — FALSE.** ₹1,500 is planned stop risk, not a maximum realizable loss. An SL-Limit can remain unfilled through a gap, while SL-M can slip. The code itself models an unfilled stop, contradicting the maximum-loss wording.
6. **“₹6,000 portfolio cap is enforced” — FALSE.** `check_portfolio_risk_capacity()` exists but has no production caller. It also accepts negative proposed risk and negative sector counts.
7. **“Tracks exits truthfully” — FALSE.** `peak >= target` creates `TARGET_FILLED` and realized profit without an order event, fill quantity, fill price, acknowledgement or timestamp.
8. **“Counters reset to 0/60 and 0/20” — FALSE.** `shared/track2_liquid/03_TRADE_LOG.md` still reports 3/60 and 3/20 in its header, 4/60 and 6/20 in its metrics, retrospective target/breakeven fills, zero losses, zero drawdown, and an active position despite simultaneously claiming 100% cash.
9. **“Broker rules are exact” — OVERSTATED.** The published times are current defaults, not guarantees; Zerodha explicitly reserves discretion to change timing or not square off.

### Phase 1 acceptance gate

Before Phase 2 begins, Phase 1 must add adversarial tests and fixes for: persistent triggered-but-unfilled lifecycle; execution-evidence-only target fills; execution-type allowlisting; finite/non-negative portfolio inputs; actual production wiring of aggregate risk; raw exchange artifact provenance; and reconciliation/quarantine of the Track 2 ledger. Lookahead removal and next-bar execution belong to Phase 2 but must remain explicitly `NOT IMPLEMENTED`, never described as working.

**Verdict: BLOCKED (P0: Phase 1 status claims do not match the executable system or ledger).**
