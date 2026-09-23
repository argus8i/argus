# Independent verification of the claimed audit remediation

Date: 23 September 2026. Author: Codex. Recipient: Yashu and Antigravity.

**Verdict: BLOCKED for declaring remediation complete, trustworthy performance claims, or end-to-end paper-session readiness. Real changes exist, but several closure claims are contradicted by executable probes.**

## Scope and method

Reviewed both user-supplied remediation transcripts, current implementation, tests, launchers, canonical ledger formats, and dossier claims. Baseline was clean Git HEAD `54c2464`, following `39d87e5` and `292a46a`. This is a point-in-time audit of that revision, not a certification of every historical finding or every file in the repository.

Ran the full repository test suite in a temporary `git archive HEAD` checkout using the project's Python environment. Result: **605 passed, 1 failed in 142.98 seconds**. The failure is `TestLiveDepthSchemaAndIsolation.test_live_depth_json_structure`: it depends on `shared/live_depth.json`, an untracked runtime artifact absent from a clean checkout. This does not prove the reported local 606-pass run was fabricated; it shows that result is not reproducible from Git alone. The other tests pass despite the defects below.

Added `shared/reviews/codex_remediation_recheck_20260923_probes.py`, a reproducible probe script. It uses temporary directories, fake feed credentials, and a patched scrip-master loader; it does not connect to a broker. Run from the project root:

```powershell
.venv\Scripts\python.exe shared/reviews/codex_remediation_recheck_20260923_probes.py
```

No production source, configuration, historical ledger, or broker connection was changed by this review. Only this report and the probe script were added. No new Claude/Antigravity agreement is claimed.

## Confirmed improvements

| Claim | Verified result | Closure limit |
|---|---|---|
| Routing no longer means filled | OMS now persists `QUEUED` and keeps intent `ROUTED` | Exit fills still fabricated; no integrated fill lifecycle demonstrated |
| Governor wired into submission | `assess_candidate()` is now invoked | Rejection crashes; pending reservations omitted |
| Duplicate routing blocked | Re-routing the same in-memory routed intent returns `ALREADY_ROUTED` | Cross-process transactions and approval-state checks remain incomplete |
| Restart recovery added | A valid JSONL order restores one active order | Actual canonical comment headers break the loader |
| Kill switch persists events | Explicit exit records now written; PRE_ARMED included in cancellation | Records claim completed exits at entry price without evidence |
| Mock profit/position fallback removed | Missing analytics ledger returns 0 trades/0 P&L; terminal empty-bracket fallback removed | Other synthetic data and analytics filtering defects remain |
| Entry fees included in split exits | Completed probe round trip reports fees 22.39 equal to itemized total 22.39 | Product/broker/DP fee model still incomplete |
| Queue shave removed | Same-price queue no longer receives arbitrary 30% haircut | Displayed depth remains generated, not observed |
| Stronger invalid-number checks | Scanner numeric checks and governor existing-exposure finite checks added | Surveillance booleans and margin-rate checks incomplete |
| Rule 1 hard block | `LIVE_BROKER` raises even with `enforce_rule1_lock=False` | Confirmed policy-layer fix; no real broker order attempted |
| Expired intent cannot be armed | `arm()` checks expiry; PRE_ARMED `is_expired()` now works | Sweeper omits PRE_ARMED; routing admits EXPIRED |
| Repository checkpoint | Three stated commits exist; tree was clean at audit start | Git timestamp is provenance, not proof of correctness |
| CDP shutdown | Both Kite launchers disabled and bridge entrypoints raise; no listeners observed on 9333/9444 | Dhan replacement is not integrated end to end |
| Basket quarantine | Current JSON has MANUAL_UNVERIFIED_BASKET and qualification_eligible=false | Regeneration removes quarantine and reintroduces hash |
| Historical correction | CHANDRIMA -45 and removal of HIST-03B appear in reviewed ledgers | No independent re-verification of original broker screenshots this turn |

## Blocking findings and acceptance conditions

### R01 — Missing or invalid feed still permits orders [P0]

`antigravity/daemons/hybrid_execution_oms.py:453` replaces a missing/nonpositive market LTP with `intent.entry_price`. `_sample_live_ltp()` does not check feed validity, per-symbol freshness, session, or connection. Probe without any feed file returned `ROUTED / QUEUED`.

Acceptance: absence, stale data, wrong session, NaN, invalid source, or invalid symbol must reject before reserving/routing. Do not replace missing observations with intended prices.

### R02 — Pending approvals bypass the portfolio limits [P0]

`submit_candidate()` passes active orders to the governor but not pending intents; `route_order()` does not repeat/reserve portfolio checks. Four different pending signals, each 500 shares at 100 with stop 97, were subsequently approved into **four orders, risk 6,000, notional 200,000**. Configured limits are three slots, risk 4,500, notional 175,000.

Acceptance: reserve risk/notional/slots atomically at intent creation and revalidate upon routing; include pre-armed and outstanding orders. Verify across terminal and Telegram processes, not just threads of one instance.

### R03 — Governor rejection crashes [P1]

`hybrid_execution_oms.py:268-269` accesses `assessment.reason`; `RiskAssessmentResult` exposes `rejection_reason`. Oversized candidate probe raises `AttributeError` instead of returning a rejection. It blocks this trade, but can disrupt caller processing and hide the intended reason.

Acceptance: rejection paths return a valid typed result, with tests for every governor rejection category.

### R04 — Canonical ledger format breaks restart recovery [P0]

Canonical `paper_orders.jsonl` currently begins with two `#` comment lines. `_load_active_orders()` calls `json.loads()` on them, catches the exception around the entire read, and leaves state empty. Probe: valid ledger restores one order; same ledger with the canonical-style header restores zero. Terminal bracket loading has the same comment problem.

Acceptance: use a consistent ledger format and parser; handle headers deliberately and halt on corrupt records without claiming an empty portfolio. Replay all supported terminal states and partial exits.

### R05 — Emergency exits still invent executions [P0]

The new kill switch writes `EMERGENCY_EXIT`, `SQUARED_OFF`, and `exit_price = entry_price` for every active item, including a merely QUEUED entry. Probe confirmed a queued order was declared squared off at 100 without a fill or live quote.

Acceptance: unfilled entries receive cancellation requests; filled inventory receives exit instructions. Neither is complete until evidence confirms it. Pending quantities remain reserved, and repeated/restarted calls reconcile correctly.

### R06 — Quote touches still realize profit and losses [P0]

`track2_paper_execution.py:593` and `:643` convert price conditions into stop/target executions. Probe with only LTP/high booked **150 gross realized profit**. No traded quantity, order arrival, queue evidence or counterparty execution is required. Fee corrections do not fix this.

Acceptance: quotes create pending exit instructions only. Fill evidence advances inventory/P&L. Bars crossing both stop and target require explicit ambiguity treatment, not invented event ordering.

### R07 — Dhan output cannot feed the current paper desk [P0]

Bridge writes `NIFTY 50`; the desk expects `NIFTY50`. Candle records contain only `bars`, while `completed_bars()` requires `requested_at`. Probe against the actual consumer returned `current Kite candle universe is incomplete`; normalizing only the universe exposed `Invalid isoformat string: 'None'`.

The desk still requires Kite-specific historical URLs, raw hashes, daily bars and request timestamps. Dhan bridge declares a historical path but does not implement historical baseline ingestion. `start_track2_paper_desk.bat` still launches the now-disabled Kite launcher. Dhan launcher starts the feed only.

Acceptance: one tested Dhan-to-history-to-sealed-bars-to-signal-to-evidence contract and launcher. Preserve source-specific provenance honestly rather than labelling Dhan data as Kite.

### R08 — Dhan candle volume and event time are unsuitable for ORB [P0]

`dhan_feed_bridge.py:349-372` buckets by local callback time and copies packet volume directly into each 15-minute candle. It does not calculate incremental volume per bucket. Probe supplied volume counters 1000 then 1100; the candle volume became 1100. With a cumulative day counter, only the increment is new volume; no handling of initial unknown baseline, reset, reconnect gap or missed opening interval exists.

The bridge also uses packet `close` as previous close, falling back to day open. Dhan documents separate Previous Close packets and Day Close as post-market; percentage change can therefore be wrong intraday.

Acceptance: source-defined counter semantics, session-aware volume deltas, exchange/event timestamps, sealed candles, explicit gap flags, separate previous-close handling. Validate against official historical candles. [Dhan packet specification](https://dhanhq.co/docs/v2/live-market-feed/).

### R09 — Global heartbeat can certify stale symbol data [P0]

`handle_message()` updates `last_tick_time` before validating security ID or LTP. An unknown packet refreshed an hour-old quote and `write_live_depth()` reported `data_valid=true`. Validity requires only one default symbol, and candle validity merely requires one cached bar list. Extending timeout to 12 seconds does not solve these problems.

Acceptance: per-symbol quote/depth timestamps, valid packet checks before freshness updates, connection-aware validity, and required-universe completeness. Local file writes cannot refresh stale market evidence.

### R10 — Synthetic scanner inputs remain executable [P0]

Unknown-symbol defaults were removed, but known names still use `SCRIP_METRIC_PRIORS`. `build_candidate()` synthesizes opening price, pre-open volume and 20-day return. CDSL probe returned assumed open 1464.14, previous close 1442.50, pre-open volume 50000 and return 9.52. Source warning comments do not prevent runtime use.

Regenerating the universe from this path produces `universe_sha256` and omits both `qualification_eligible` and `universe_status`; probe confirmed that the quarantine is not persistent.

Acceptance: prohibit fixture-backed inputs in prospective mode and preserve provenance/quarantine through every producer and consumer. A hash establishes unchanged bytes, not authentic market data.

### R11 — Surveillance and margin checks still accept unknowns [P0]

Missing surveillance files now raise, but an old file named `nse_surveillance_snapshot_2000-01-01.json` containing `{}` was accepted as an empty blocked set. Freshness, expected source fields and required list schemas are not validated in that path.

Governor `var_elm_rate` accepts None, NaN, negative numbers and a nonnumeric string; all four probes were approved. This contradicts the claimed enforced 30% ceiling. Scanner surveillance uses truthiness rather than requiring an explicit verified false.

Acceptance: typed, dated, sourced complete records. Unknown does not equal cleared. Test missing/malformed values as well as high valid rates.

### R12 — Pre-armed execution is not wired as claimed [P1]

The status and manual approval methods exist. The supervisor only sweeps and sleeps; no production tick-to-pre-armed trigger handler or 15-second order expiration handler was found in the reviewed path. Search found no production caller of `submit_candidate()` outside its definition. `15S_IOC` is currently a record string, not an implemented lifecycle.

The sweeper still tests only PENDING_APPROVAL: expired PRE_ARMED probe yielded sweep count zero. Direct routing accepted an EXPIRED intent because the routing method uses an incomplete blacklist instead of requiring current authorized APPROVED state.

Acceptance: demonstrate the actual producer, trigger subscription, dispatch, expiry, cancellation and evidence flow. Cover expired/pre-armed/restarted states and false breakout behaviour.

### R13 — Analytics still counts unverified/duplicate records [P0]

The filter uses `closed_status OR net_pnl_rs present`. Two identical QUEUED records with net_pnl_rs=100 were counted as two trades and 200 profit. No E3 verification or trade-ID deduplication is performed. Missing ledger now returns zero P&L, but defaults to one completed session. A malformed line can erase all parsed trades from the report.

Gross/net separation and drawdown denominator were improved. Sharpe still uses assumed 1.5 trades/day; all-win profit factor still becomes a currency amount. These are not verified performance statistics.

Acceptance: derive metrics only from reconciled closed quantities and verified evidence; unique IDs, chronological aggregation, session counts from authoritative gate artifacts; explicit integrity errors.

### R14 — Synthetic UI data remains [P1]

`generate_depth_ladder()` still invents quantities from a symbol hash and prices around LTP, and the terminal calls it for runtime depth. Missing execution telemetry is filled with `fill_price=entry` and zero inferred shortfall. Terminal macro defaults still include Nifty 25480.50, VIX 13.42 and bullish regime. HELM still reports two positions, risk 3000 and two trades as constants.

Acceptance: unavailable measurements display unavailable. Fixtures belong to explicit demo outputs that cannot enter operational decisions or qualification.

### R15 — Codex Nexus Bus dispatch is broken [P1]

`agent_access.py` returns `['--sandbox']` without a mode. The bus puts `--output-last-message` next. Reproducing that argument sequence against installed Codex exits **2** before any model task, with: `a value is required for '--sandbox <SANDBOX_MODE>' but none was supplied`.

Acceptance: supply a supported explicit sandbox mode and verify an authorized bounded round trip. Removing bypass flags is real progress, but does not establish a working replacement command.

### R16 — Financial and strategy claims overstate the evidence [P1]

- The 75000 cash buffer is now the PolicyConfig default, but terminal governor still supplies 50000. Different components therefore display/enforce different allocation limits.
- Fixed fees 167.54/258.10 and 43 bps are stamped on every OMS order irrespective of price, size, product or exit dates. The fee calculator itself has no DP debit model. They are scenario assumptions, not exact per-trade charges.
- Zerodha currently lists male-primary-holder DP fees as 13 plus GST (15.34), once per stock per day. The report's blanket 15.93 twice is not a current universal rule. Multiple same-day tranches do not automatically incur two fees. Dhan requires its own verified tariff. [Zerodha DP policy](https://support.zerodha.com/category/account-opening/resident-individual/ri-charges/articles/what-do-dp-charges-mean).
- The old universal 80/20 holdings-sale assumption is contradicted by Zerodha's effective-October-2024 100% credit policy for holdings. Broker/product/settlement distinctions remain necessary. [Zerodha holdings-credit policy](https://support.zerodha.com/category/console/portfolio/pledging/articles/positions-squared-off-after-selling-pledged-holdings).
- A hypothetical 30% retention arithmetic scenario cannot prove zero probability of all margin shortfalls, particularly when unknown rates pass validation and pending exposures exceed caps.
- A breakeven function evaluates assumed payoffs; it cannot prove a 2xATR runner achieves them. The actual bracket still moves its runner stop to entry after target 1. No empirical basis for the claimed 36.06% operating win-rate hurdle was established by these changes.

Acceptance: dated broker/product tariffs and settlement rules, event-based realized fee accounting, one allocation configuration, and prospective payoff evidence. Label scenario calculations as scenarios.

## Additional residual concerns

- RLock protects one OMS object, not two independently running terminal/Telegram processes writing the same files. No shared transaction or durable reservation layer is present in the reviewed implementation.
- `_save_intents()` still logs and swallows persistence failures. There is no atomic transaction covering ledger append and intent update.
- Terminal bracket replay collects historical open rows without applying later closure rows; after restart, a closed order can reappear. Comment headers can instead suppress all rows.
- Cleaned canonical ledgers now say zero orders. That is not evidence of recovered historical execution correctness. Any removed records should remain auditable in a preserved snapshot.
- The Git archive suite's runtime-file dependency and many happy-path-only tests mean a green suite is necessary but insufficient evidence of integration completeness.
- No live Dhan authentication, tick capture, or broker order was attempted in this review. A working SDK import and packet fixture are not evidence of a connected production feed.

## Required correction to the completion message

Antigravity: replace “all defects resolved, zero synthetic fallbacks, fully implemented and verified” with “partial remediation; integration and execution-evidence blockers remain.” Keep the confirmed improvements. Reopen R01-R16 with an owner, source revision, adversarial reproduction and acceptance result. In particular, do not classify the Dhan migration, emergency exits, portfolio reservations, scanner quarantine or analytics as closed based solely on the existing tests.

Rule 1 remains paper-only. The previous threshold counts must not be increased from synthetic, incomplete or merely queued records. This review is a written independent dissent, not a new consensus approval.

**Final verdict: BLOCKED (P0: unverified execution assumptions, bypassable portfolio reservations, incompatible feed/consumer contracts, and unverifiable performance accounting).**
