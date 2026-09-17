## Workspace reassessment — 17 September 2026

Author: CODEX. Scope: current working tree at HEAD `befa178`, recent bridge commits, pending feed-consumer and reviewer-prompt changes, coordinator, surveillance gate, and both paper logs. This is a current-state audit, not certification of every vendored integration. Files were being edited concurrently; findings refer to the versions inspected during this run. Previous review is preserved below as historical evidence.

**Verdict: BLOCKED (P0: unsupported consensus approval and unqualified paper-trade evidence).**

### What is happening

The project now has Git history. Recent commits installed real CLI reviewer dispatch, removed tracked session credentials, hardened the depth bridge, and installed Claude's standing prompt. Pending changes install Codex's new prompt and introduce `feed_validity.py` into three consumers. The latter is still untracked. The transport previously returned real responses from all three agents; this audit did not repeat paid model requests. The supervised inbox reports STOPPED: authenticated request capability is not an always-running worker.

Validation: `.venv/Scripts/python.exe -m pytest tests -q -p no:cacheprovider` completed with **111 passed in 4.23 seconds**. Additional independent probes found the failures below. File writes were mocked for coordinator probes; surveillance persistence was mocked. No source or broker configuration was changed.

### P0 — Consensus can approve unauthenticated or incomplete evidence

`antigravity/orchestrator/coordinator.py`, `synthesize_outcome`: a single envelope with `auth_signature="INVALID"`, no Codex review, and `has_p0_objection=False` returns **PASSED / HIGH**. The function does not verify signatures, artifact hashes, task/correlation identity, or required reviewers. It nevertheless prints verified signatures, verified cash state, and enforced track isolation. These are unsupported attestations.

Fix: accept the original review package; verify each envelope and artifact against that package; require the reviewer set dictated by its review type; validate explicit structured verdicts and unresolved findings. Missing/invalid evidence must block approval. Cash state must be reported as unverified unless backed by an authorized account observation. Do not infer it from a review succeeding.

### P0 — Paper qualification and execution evidence are not established

`CHATGPT/observation_log.csv` records ANLON and MOBIKWIK observations at 09:47 but orders at 09:16 and fills at 09:18/09:20. Their source is a mutable daily-band file. The displayed records do not establish that the entry inputs were known before the order or that contra-volume after submission filled the queue. They currently count toward the gate. Require original timestamped signal/quote/fill evidence before qualifying them; preserve the records with unresolved status rather than deleting history.

`shared/track1_esm/02_WATCHLIST.md` admits 20% band candidates while current AGENTS Rule 11 explicitly limits Track 1 to 2%/5% fixed bands. This is a local policy contradiction, regardless of current exchange classification. Resolve the strategy mandate explicitly before qualifying those trades. The Track 1 log header says 0 sessions while its later summary says 3; the watchlist says 1.

`shared/track2_liquid/03_TRADE_LOG.md:29` claims positive net expectancy from two unrealized winners and a gross breakeven trade. That does not establish positive net expectancy after costs. The template CSV even contains a SESSION_SUMMARY marked as counting despite UNSET strategy/source metadata. The actual radar writes a different 21-column CSV, bypassing the 48-column template's provenance fields. Establish one validated ledger per track and derive displayed counters from qualifying records only.

### P1 — New imports break standalone launcher loading

The new `from antigravity.daemons.feed_validity ...` occurs before repository-root setup in `live_signal_engine.py:16` and `track2_live_radar.py:40`; `multi_stock_radar.py:34` has no preceding path setup. Isolated script-load probes for all three raise `ModuleNotFoundError: ... 'antigravity' is not a package` (Python's stdlib antigravity module can win resolution). `launch_radar.bat:13` and `launch_radar_and_alerts.bat:15` invoke the file directly. Pytest masks this because conftest injects the root.

Fix: bootstrap the root before project imports or standardize launchers on package execution from the root. Test the actual supported launcher entrypoints, without a preloaded project package. Include the untracked helper in the deployment change.

### P1 — New reviewer types silently dispatch nobody

The Codex adapter permits REALITY_AUDIT and PROVENANCE_AUDIT, but coordinator routing omits them. Reproduction: dispatch REALITY_AUDIT -> `COMPLETED`, Claude=None, Codex=None. BOTH is routed unchanged to adapters whose allowed-type sets omit BOTH.

Fix: one validated routing registry shared with adapters; map composite requests into supported reviewer types; reject unknown or zero-recipient dispatches. Test these through the coordinator, not only the adapter.

### P1 — Feed gate still accepts invalid shapes and can crash

`feed_validity.py:57` accepts a fresh timestamp with missing `data_valid`, `data_valid=None`, or the string `"false"`. It also accepts CONNECTED_NO_DATA. A list-valued status raises TypeError rather than returning unusable. These were directly reproduced. Its docstring promises all market fields can be trusted, but it validates neither identity nor numeric content.

`multi_stock_radar.py:165,202` still dereferences `stats=None`; a fresh snapshot with active_stock=MOBIKWIK reproduces AttributeError. Lines 187 and 197 also retain substring symbol matching and invented 10,000-share baseline defaults.

Fix: validate a versioned producer schema, exact Boolean flags, status type, timestamp, instrument identity, and finite numeric values. If legacy Track 2 payloads need support, explicitly normalize that known schema; do not accept arbitrary missing validity fields. Normalize or reject null containers. Match exchange/token identity exactly and reject missing volume baselines. Regression tests must exercise downstream consumers with malformed snapshots.

### P1 — Symbol/depth attribution remains unproven

`kite_web_depth_bridge.py:319-402` still collects depth rows globally and independently obtains symbol names through document-wide `.instrument-name` / `.tradingsymbol` fallbacks and generic dialog headings. The earlier finding remains open: these can bind one instrument's book to another label. No recorded real DOM fixture was inspected establishing correct binding. The gate marks data invalid for hidden/frozen state, not ambiguous identity.

Fix: identify exactly one visible depth container and extract exchange, symbol/token, quotes and rows from its uniquely linked scope; reject ambiguity. Verify with recorded multi-instrument DOM fixtures.

### P1 — Track 2 replay is presented as prospective execution

`track2_live_radar.py:389-458` takes historical median volume across all prior intraday buckets rather than matching clock buckets. It evaluates a candle's high with its full bucket volume and then assigns entry at OR high + 0.05. This does not prove a fill at that price after volume confirmation became observable. The sizing call labels NSE candidates `exchange="BSE"` to select an order policy; venue and execution policy must be separate fields.

Lines 485 onward hardcode three portfolio positions and recompute state from day highs/latest prices; this is not an event-based order/fill ledger. `TRAILED_TO_BREAKEVEN_ZERO_RISK` is not a valid execution guarantee. Replace retrospective entry assumptions with decision-time events, next executable quotes, explicit partial/unfilled states and cost accounting; isolate historical exploration from prospective qualification.

Zerodha's official stop-loss documentation confirms a stop-limit order has a price boundary and may remain unexecuted beyond it: https://support.zerodha.com/category/trading-and-markets/charts-and-orders/order/articles/what-are-stop-loss-orders-and-how-to-use-them . A trigger moved to entry therefore does not eliminate downside or transaction costs.

### P1 — Surveillance types are insufficiently validated

`track2_surveillance_monitor.py`, evaluate_scrip: `is_fno_underlying="false"`, ASM=0, GSM=0, band=0 and checked_at="2026-09-17garbage" returns QUALIFIED. Truthiness and startswith are not Boolean/date validation. Fix exact types, allowed stage values, parsed timestamps and independently sourced daily membership. A separate all-NaN probe rejected on the band test; this does not invalidate the reproduced string/timestamp bypass.

### Verified improvements and boundaries

- The bridge now clears local depth/stats/watchlist and derived volume audit on its hidden/frozen path; the old CSV-local stale-data defect is addressed in that path.
- The focus request counter advances past both consumed IDs; the prior immediate collision is addressed.
- Current CSV widths are consistent: Track 1 46 columns/7 records; Track 2 template 48/2; radar CSV 21/142. Correct widths do not establish provenance or valid fills.
- Risk calculator's observation flag is False and reviewed sizing returns zero live shares. No place_order match was found in the inspected first-party daemon/model paths. This is not proof of account holdings or absence of credentials across disk.
- Track 2 producer explicitly serializes an enctoken to its JSON snapshot. Credential untracking does not imply credential absence on disk; no secret value was exposed in this audit.

### Ordered implementation handoff to Antigravity

1. Repair standalone imports and dispatch routing; verify real entrypoints and every supported review type.
2. Enforce consensus verification and structured verdicts; test invalid/missing/wrong-task evidence and preserve dissent.
3. Complete feed schema and identity validation with downstream behavioral tests.
4. Correct prospective logging and execution sequencing; reconcile counters without inventing history or promoting retrospective observations.
5. Replace surveillance truthiness/date-prefix checks; reconcile the explicit Track 1 universe with its rulebook.

Source changes are reserved to Antigravity by the user's standing auditor mandate. This submission supplies reproducible failures and concrete remedies; it does not claim they have been implemented. No unsigned outbound dispatch was sent.

**Final verdict: BLOCKED (P0: consensus and paper qualification can pass without verified evidence).**

---

## Earlier depth-bridge audit — retained historical record

## Audit verdict

**REJECT for live/downstream trading use.** There are two P0 defects: invalid data still escapes the fail-closed boundary, and the new selector fallbacks can bind one instrument’s depth to another symbol.

Rule 1 remains controlling: paper observation only; zero real-capital deployment.

## P0 — Deployment blockers

### 1. Fail-closed path still publishes stale prices, volume and depth to consumers

**File:** `antigravity/daemons/kite_web_depth_bridge.py:884-945`

When `data_valid == false`, the code nulls fields inside `val`, but continues using pre-nulling local variables:

- `depth`
- `stats`
- `vol_to_log`
- `best_bid`
- `best_ask`
- `total_buy`
- `total_sell`

Consequently, the tick CSV receives stale depth-derived values and volume at lines 927–945. Only `ltp_to_log` is cleared.

The JSON also retains:

- `watchlist`, including watchlist LTPs
- `volume_expansion_audit`, computed from stale `stats["volume"]`
- `active_stock`

This is exploitable by a real downstream consumer: `multi_stock_radar.py:142-180` reads watchlist LTPs without checking `data_valid`, `is_stale`, or timestamp freshness. It can label those values `KITE_LIVE` even after the bridge declared the snapshot invalid.

**Why it matters:** Stale prices can drive Rule 2 price checks, circuit headroom, sizing, and paper-trade decisions as if live. Stale volume can falsely qualify Rule 7’s ≥3× expansion and Rule 9 liquidity gates.

**Concrete fix:**

- Build a sanitized publication object before both JSON and CSV output.
- On invalid data, clear `watchlist`, `depth`, `stats`, and any audit derived from current DOM data.
- Pass `None` for every CSV market-data column, or skip the market-data tick and write a separate invalid-feed event.
- Add `data_valid` and `invalid_reason` columns to the CSV.
- Require every consumer to explicitly test `data_valid is True` and snapshot age before reading any market field.
- Fail closed for extraction timeout/error too, instead of leaving the previous valid JSON on disk.

### 2. New global selectors can attribute depth to the wrong instrument

**File:** `antigravity/daemons/kite_web_depth_bridge.py`, `EXTRACT_JS`, new fallback block around the diff’s lines 371–404

The following fallback is not scoped to the depth component:

```js
'.instrument-name', '.tradingsymbol'
```

`document.querySelector(paneSel)` returns the first matching node anywhere in the document. That can be a watchlist row, chart header, order dialog, or another retained component.

The visible-dialog fallback is also unsafe. Within any visible modal it accepts:

```js
'.symbol, .name, .title, h1, h2, h3'
```

Those selectors can return a dialog title such as an order-window heading rather than the depth instrument. Visibility is tested only for the outer dialog, not the selected symbol node.

Depth rows themselves are globally collected:

```js
document.querySelectorAll('table.buy ...')
document.querySelectorAll('table.sell ...')
```

Thus the depth and symbol are independently selected from different DOM regions. The patch increases the chance of publishing plausible depth under the wrong `active_stock`, which is worse than `UNATTRIBUTED`.

**Why it matters:** Downstream logic can calculate liquidity, locked-circuit state, volume qualification, and position sizing for security A using security B’s order book.

**Concrete fix:**

- First identify exactly one visible depth container.
- Extract bids, offers, totals, exchange, and symbol strictly from that same container or a uniquely linked parent.
- Do not use document-wide `.instrument-name`, `.tradingsymbol`, `.name`, `.title`, or heading fallbacks.
- Require an exact normalized `(exchange, tradingsymbol)` match against the instrument master/universe.
- Reject ambiguous or multiple visible depth containers.
- Prefer an instrument token carried by the component if Kite exposes one.
- Publish `UNATTRIBUTED` and `data_valid=false` whenever identity cannot be proven.

The exchange-suffix regex itself is syntactically valid. Its correctness against Kite’s current rendered labels cannot be verified without a live DOM capture.

## P1 — High severity

### 3. CDP request IDs collide after every focus cycle

**File:** `antigravity/daemons/kite_web_depth_bridge.py:765-796`

Initial sequence:

```text
msg_id = 1000
msg_id += 2
focus IDs = 1002, 1003
msg_id += 1
extract ID = 1003
```

The same reuse occurs after every periodic focus enforcement: the second focus ID becomes the next extraction ID.

Normally the focus response is consumed before extraction begins. But if the second focus request times out and its response arrives late, the extraction’s `send_cdp_cmd(..., req_id=1003)` can accept that late focus response as the extraction response. `val` will then be absent and that capture cycle is lost. Other timing patterns can cause nonmatching responses to be permanently discarded.

The fixed authentication IDs `901` and `902` do not presently collide with the dynamic counter because it starts at 1000. Reusing them serially is still fragile if an auth command times out and a late response survives until the next refresh.

**Concrete fix:**

Use a single monotonic allocator for every CDP command:

```python
def next_id():
    nonlocal msg_id
    msg_id += 1
    return msg_id
```

Call it separately for both focus commands, authentication commands, and extraction. Never use fixed IDs and never reuse an ID during a connection.

For robust handling, use one dedicated WebSocket reader task that dispatches responses into pending futures keyed by ID and routes events separately. Timed-out IDs should be retired so late responses cannot satisfy a later request.

### 4. `stats = None` crashes an existing downstream consumer

**Files:**

- `antigravity/daemons/kite_web_depth_bridge.py:887`
- `antigravity/daemons/multi_stock_radar.py:156,192-195`

`dict.get("stats", {})` returns `None` when the key exists with a null value. It does not return the default. The radar then executes:

```python
active_stats.get("volume")
```

If the invalid snapshot retains a matching `active_stock`, this raises `AttributeError`.

`live_signal_engine.py` currently returns at its stale gate before dereferencing `stats`, so this particular path is safe there. It still should explicitly gate on `data_valid`.

**Concrete fix:**

Maintain schema stability:

```python
val["stats"] = {}
```

and harden consumers:

```python
active_stats = live_depth.get("stats") or {}
```

The same convention should apply to `depth`, `watchlist`, and derived-audit objects.

### 5. Extraction failure leaves the last valid JSON looking current to weak consumers

**File:** `antigravity/daemons/kite_web_depth_bridge.py:800-801`

If CDP times out, returns an error, or receives the collided response described above, `val` is falsy and the daemon writes nothing. The old `live_depth.json` remains in place.

`live_signal_engine.py` checks timestamp age, but `multi_stock_radar.py` does not. It can therefore continue using the last successful snapshot indefinitely.

**Concrete fix:**

On every failed extraction, atomically publish an invalid heartbeat containing:

- current `local_write_time`
- `data_valid=false`
- `invalid_reason="CDP_EXTRACTION_FAILED"`
- empty market-data containers
- no carried-forward watchlist or derived metrics

All consumers must reject stale timestamps independently.

### 6. `data_valid` is too permissive

**File:** `antigravity/daemons/kite_web_depth_bridge.py:881-896`

The value is false only for hidden or frozen data. It remains true for:

- `CONNECTED_NO_DATA`
- `CONNECTED_NO_DEPTH`
- `PARTIAL_DEPTH`
- unattributed depth
- a potentially unrecognized `active_stock`

For a depth feed, “connected” is not equivalent to validated live market data. In particular, `PARTIAL_DEPTH` may represent legitimate locked-circuit states, so it must not simply be discarded; it must be classified under the required four-state execution model. But it cannot be marked generically valid without identity and freshness validation.

**Concrete fix:**

Separate validity dimensions:

```text
identity_valid
quote_valid
depth_valid
freshness_valid
execution_state
```

Set overall `data_valid` only after exact symbol binding and freshness validation. Represent legitimate one-sided books as `LOCKED_NO_BID`, `QUEUED`, `PARTIAL`, or `FILLED` inputs rather than conflating them with missing/unmounted DOM.

## Concurrency finding

`enforce_tab_focus()` does **not currently race a separate main-loop `ws.recv()`**. All calls are awaited serially in one coroutine, and the main loop reads the socket only through `send_cdp_cmd()`.

Therefore, it cannot presently consume a message that another concurrently active receiver is waiting for.

However, `send_cdp_cmd()` discards every nonmatching response or event it receives. Combined with timeouts and ID reuse, this loses messages and enables the late-response mis-correlation described above. A single reader/dispatcher is the correct architecture if any concurrency is introduced.

## Broker and regulatory finding

No order-placement, margin, auction, or T2T behavior is changed in this diff, so there is no substantiated Zerodha margin/T2T defect to report here.

There is nevertheless an unresolved production-governance issue: this daemon extracts browser session credentials and scrapes Kite’s rendered DOM instead of using the documented market-data interface. Zerodha documents its WebSocket feed as the supported efficient source for LTP, volume and five-level depth, identified by instrument token. Its terms say API access must use documented means and restrict market-data usage and redistribution. Written broker/compliance confirmation would be needed before treating this browser bridge as an approved production data source. [Zerodha WebSocket documentation](https://kite.trade/docs/connect/v3/websocket/), [Kite Connect terms](https://kite.trade/terms/)

SEBI’s retail-algo framework becomes relevant if this data eventually drives automated order submission, but this patch contains no order path and Rule 1 forbids live deployment. No further regulatory violation can be established from this diff alone.

## Unresolved live-session verification

The following cannot be established without a live Kite session and captured DOM fixtures:

- Whether Chrome actually unmounts the relevant depth component under each minimized/occluded state.
- Whether `Emulation.setFocusEmulationEnabled` persists or is dropped under the claimed conditions.
- Kite’s current drawer/modal hierarchy and exact symbol label format.
- Whether hidden duplicate depth tables remain mounted.
- Whether the displayed buy/sell column order still matches the parser.

These uncertainties support failing closed; they do not justify guessing selectors.

**Final disposition:** P0 objections remain unresolved. Do not approve this diff for downstream trading decisions, even paper-entry logging, until stale-field publication and symbol/depth binding are corrected and tested with recorded DOM fixtures.
