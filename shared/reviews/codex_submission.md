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