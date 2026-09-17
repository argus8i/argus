I read the actual file (not just the diff) to verify claims against real code around lines 700–960. Verified findings below — ranked by severity.

## P0 — Fail-closed gate doesn't actually null the data that reaches the CSV or the JSON volume block

**File:** `antigravity/daemons/kite_web_depth_bridge.py`, lines 884–953

The fail-closed block only mutates dict keys on `val`:
```python
val["depth"] = None
val["ltp"] = None
val["stats"] = None
ltp_to_log = None
```
It does **not** touch the local variables `depth` (line 802) and `stats` (line 804) — those still hold the pre-nulled, possibly-stale extraction. Two consequences, both verified by reading the code that runs after the gate:

1. **Line 928–932**: `best_bid`/`best_ask`/`spread_pct` are recomputed from the *local* `depth` variable — not `val["depth"]`, which is already `None`. When `data_valid=False`, `live_depth.json` correctly shows `depth: null`, but `live_depth_ticks.csv` still gets `best_bid`, `best_ask`, and `spread_pct` from the stale/backgrounded-tab depth snapshot.
2. **Line 939–940**: `append_tick_log(... total_buy=stats.get("total_buy"), total_sell=stats.get("total_sell") ...)` reads the local `stats` dict, which was never nulled. Same leak.
3. `vol_to_log` (line 813) is computed before the gate and is never reset in the invalid branch, so stale volume also reaches the CSV.
4. `val["volume_expansion_audit"] = vol_audit` (line ~919, after the gate but using `vol_audit` computed at lines 861–873, *before* the gate) is written unconditionally into `live_depth.json` itself — so a downstream consumer reading only the JSON still gets Rule-7 volume-expansion numbers derived from a backgrounded/frozen-DOM read, with no `data_valid` guard on that key at all.

**Why it matters:** this is exactly the scenario the fail-closed design claims to prevent — a backgrounded tab or frozen DOM publishing stale quotes that downstream logic can't distinguish from live ones. The JSON's top-level price fields are protected; the CSV tick log and the volume-expansion audit are not, and Rule-7 volume qualification is presumably used for entry decisions.

**Fix:** null the locals, not just `val`, before anything downstream reads them:
```python
if not data_valid:
    depth = None
    stats = {}
    val["depth"] = None
    val["ltp"] = None
    val["stats"] = None
    ltp_to_log = None
    vol_to_log = None
    vol_audit = {sym: None for sym in TRACK1_INSTRUMENTS}  # or skip the write entirely
    ...
```
And compute `vol_audit`/`val["volume_expansion_audit"]` after the gate, or explicitly guard it with `data_valid`.

## P0 — `enforce_tab_focus`'s second CDP id always collides with the next extraction's id

**File:** same file, lines 472–496 and 761–798

`enforce_tab_focus(ws, req_id)` sends `bringToFront` with `req_id` and `Emulation.setFocusEmulationEnabled` with `req_id + 1`, but never reports that `+1` back to the caller's counter. The caller only does `msg_id += 2` before the call, so the global counter is left sitting at `req_id`, one less than the highest id actually put on the wire.

Trace it: on connect, `msg_id += 2` → say 1002; `enforce_tab_focus` consumes ids 1002 and 1003. `msg_id` is still 1002. First loop iteration: `msg_id += 1` → 1003 — identical to the id just used for `Emulation.setFocusEmulationEnabled`. This isn't a rare race, it's deterministic on every single focus-enforcement cycle (initial connect and every 30s thereafter).

**Why it matters:** `send_cdp_cmd` matches purely on `id`. If the focus-emulation response is slow and arrives after `enforce_tab_focus`'s own 3s timeout gives up, it sits in the websocket's read queue. The following extraction call, reusing that same id, will match against this stale `Emulation.setFocusEmulationEnabled` response instead of its own `Runtime.evaluate` response. `cmd_res.get("result", {}).get("result", {}).get("value")` on that shape yields `None`, so `val` is falsy and the tick is silently skipped — no crash, but a silent data gap with no log line explaining why, and the *actual* extraction response is later orphaned in the socket buffer, discarded by whatever future call happens to read it and wasting part of that call's timeout window. Under any WS latency, this steadily degrades the effective sample rate.

**Fix:** don't let `enforce_tab_focus` hand out an id the caller doesn't know it used. Either return the highest id consumed and have the caller advance `msg_id` to it, or have the caller pass and own two ids explicitly:
```python
msg_id += 1
focus_res = await enforce_tab_focus(ws, msg_id)  # uses msg_id and msg_id+1
msg_id += 1  # advance past the second id enforce_tab_focus actually used
```

## P1 — Depth-drawer symbol fallback can attribute depth to the wrong symbol

**File:** same file, lines ~371–410 (EXTRACT_JS)

The dialog fallback:
```js
const dialogs = document.querySelectorAll('[role="dialog"], .modal, .modal-content, .su-modal, dialog[open]');
for (const dlg of dialogs) {
    ... if (nm && nm.innerText.trim()) { activeStock = ...; break; }
}
```
takes the **first visible dialog in DOM order**, not necessarily the depth-pane's own dialog, and pulls a name from a broad selector (`h1, h2, h3, .title` included) that isn't depth-specific. If Kite has more than one dialog-like element mounted and visible at once — an order confirmation, a GTT dialog, an alert/toast implemented as `[role="dialog"]` — while the depth drawer is also open, this can attribute the currently-captured depth to whatever symbol that *other* dialog names, which the review brief correctly identifies as worse than `UNATTRIBUTED`. I can't confirm this happens on the live Kite DOM without a running session — flagging as a real risk given the selector breadth, not a confirmed reproduction.

**Fix:** scope the fallback to a depth-specific ancestor (e.g. require the dialog to also contain a `.depth-pane`/bid-ask table before trusting its name), rather than any visible dialog.

## P2 — `--auto-open-devtools-for-tabs` added to `start_kite_feed.bat`

Opening DevTools for every tab works against the very fix being shipped alongside it: `Page.bringToFront`/`Emulation.setFocusEmulationEnabled` exist to keep Kite's tab believed-foregrounded, and an auto-opened DevTools panel is itself a competing window that can take real OS focus/occlusion. It also has nothing to do with the stated purpose of this diff (focus enforcement + fail-closed) and looks like a leftover debug flag rather than an intentional change. Flagging for a "was this deliberate?" check, not asserting the mechanics without a live run.

## Correct / no issue

- `send_cdp_cmd`'s id-matching filter itself (discard non-matching ids until timeout) is fine in isolation — the bug is in id issuance, not this function.
- `ltp` nulling is done correctly both in `val["ltp"]` and the `ltp_to_log` local — this path alone doesn't leak.
- The `\s+(NSE|BSE|...)\b` exchange-badge strip is correctly anchored on a preceding whitespace + word boundary; ordinary tickers won't false-positive.
- `enforce_tab_focus` awaiting inside the same `ws` as the main loop is not a true concurrency race — both awaits are sequential (`await enforce_tab_focus(...)` fully resolves, including its internal timeout, before the loop proceeds to the extraction `send_cdp_cmd`), so no message is consumed out of turn by two coroutines truly running in parallel. The actual failure mode is the id-collision above, not a race.

## Unresolved P0 objections
1. Tick CSV (`live_depth_ticks.csv`) can carry stale best_bid/best_ask/spread/volume/total_buy/total_sell even when `data_valid=False` and the JSON output is correctly nulled — any consumer reading the CSV instead of/alongside the JSON is unprotected.
2. `live_depth.json`'s `volume_expansion_audit` key is written unconditionally, bypassing the fail-closed gate entirely, and is presumably an input to Rule-7 volume-qualification entry logic.
3. The focus-enforcement id scheme deterministically collides with the following extraction's id on every cycle, causing silent, unlogged data-gap ticks whose frequency scales with CDP round-trip latency — not verified against a live session, but verified against the code's own id arithmetic.