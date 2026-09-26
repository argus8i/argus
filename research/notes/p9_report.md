# P9 report: production patch specification and overnight handover

**Branch:** `track2/decision-engine` (pushed to origin)
**Author:** Claude
**Date:** 26 Sep 2026
**Scope:** paper/research only.

Nothing under `antigravity/` was edited tonight. Every production change below is a specification for **Codex to implement** on its own branch off `main`, with Rule 8 sign-off before Antigravity integrates. The executable contract is `research/tests/test_p8_production_contract.py`: 2 guards and 6 strict xfails.

---

## 0. Security finding (read first)

`antigravity/daemons/kite_web_depth_bridge.py` (present in the main checkout; last touched in `39d87e5`):
- connects to Chrome's remote-debugging port **9333** (`CDP_HTTP_URL = "http://127.0.0.1:9333/json"`, line 33);
- reads the Kite **`enctoken`, `user_id` and `public_token`** from the browser session with `Network.getCookies` and `localStorage` (lines 507–545).

AGENTS.md (23 Sep) says: "Browser remote-debugging ports (9333, 9444) and active browser credential scraping are strictly prohibited."

`bring_to_front.py`, `inspect_tab.py` and `track2_candle_collector.py` (lines 133, 143, 193: `kite.zerodha.com/oms/instruments/historical/...`) rely on the same web session. Plan rule 1.2.11 forbids that source for research.

- **Checked 26 Sep 04:00:** nothing was listening on 9333 or 9444, and none of these daemons was running. Port 9222 belonged to Microsoft Edge.
- **Request:** remove these files, or disable them behind a fail-closed flag, in the Codex branch. Replace their data with `research/shadow/feed.UpstoxIntradayFeed` (below).

## 1. Adjusted A1 in production (9 files)

Create **one** constants module, e.g. `antigravity/models/track2_limits.py`, mirroring `research/decision/sizing.py`:

```python
MAX_SLOTS = 3
SLOT_CAP_RS = 38_000.00                 # checked at the worst admissible entry price
AGGREGATE_EXPOSURE_CAP_RS = 114_000.00  # absolute notional, filled + pending reservations
RISK_BUDGET_RS = 1_500.00
ENTRY_CLAMP_PCT = 0.0010                # worst admissible entry = round_up(entry * 1.001) + 1 tick
```

| File:line (26 Sep) | Now | Change to |
|---|---|---|
| `execution_policy.py:124` | `candidate.get("max_slot_notional_rs", 58333.33)` | `candidate.get("max_slot_notional_rs", SLOT_CAP_RS)` |
| `liquid_momentum_screener.py:348` | `max_notional_rs: float = 58333.33` | `= SLOT_CAP_RS` |
| `track2_compass_strategy.py:81` | `max_notional_rs: float = 58333.0` | `= SLOT_CAP_RS` |
| `track2_last_light_strategy.py:74` | `max_slot_notional: float = 58333.0` | `= SLOT_CAP_RS` |
| `track2_recoil_strategy.py:79` | `max_slot_notional: float = 58333.0` | `= SLOT_CAP_RS` |
| `track2_trapdoor_strategy.py:70` | `max_notional_rs: float = 58333.0` | `= SLOT_CAP_RS` |
| `track2_volatility_squeeze_strategy.py:92` | `max_notional_rs: float = 58333.33` | `= SLOT_CAP_RS` |
| `track2_vwap_reclaim_strategy.py:97` | `max_notional_rs: float = 58333.33` | `= SLOT_CAP_RS` |
| `track2_portfolio_risk_governor.py:138–179` (`calibrate_for_corpus`) | slot = (corpus − buffer)/3 = 58,333.33; total 1,75,000 | `max_single_slot_notional_rs=SLOT_CAP_RS`, `total_capital_allocation_rs=AGGREGATE_EXPOSURE_CAP_RS`; update the docstring at lines 156–158 and 203 |
| `track2_portfolio_risk_governor.py:250` | `proposed_notional = quantity * entry_price` | `quantity * worst_admissible_entry(entry_price)` |
| `track2_portfolio_risk_governor.py:297–302` | pending counted at `notional_rs` or price × qty | use `reserved_notional_rs` (qty × worst admissible entry) when present; otherwise compute it the same way; never use the bare limit price |
| `track2_portfolio_risk_governor.py:~230` | `if stop_price >= entry_price: INVERTED_STOP` (**every short rejected**) | add a required `side`: BUY needs stop < entry, SELL needs stop > entry; risk per share = abs(entry − stop); MIS only for SELL |

Tests to turn from xfail to pass: `test_governor_uses_adjusted_a1_caps`, `test_governor_counts_pending_entries_at_their_reservation`, `test_governor_accepts_a_valid_intraday_short`, `test_governor_rejects_a_short_with_its_stop_below_entry`.

## 2. Other production items (P8.1)

| Item | File:line | Change | Test |
|---|---|---|---|
| MIS default | `track2_paper_execution.py:425` (`product_type: str = "CNC"`) | `"MIS"` | `test_bracket_product_defaults_to_mis` |
| RVOL floor | `track2_shared_features.py:129` (`floor_median=1000.0`) | no floor; a zero or missing median gives "unknown", which the strategy rejects | `test_rvol_has_no_share_floor` |
| Fee docstring | `track2_paper_execution.py:75` says 0.00297% | 0.0030699% (the code is already correct; ₹61.99 matches research) | guard passes |
| 15:05 policy exit | `track2_daily_paper_desk.py` (none found) | MARKET exit at 15:05 plus the existing 15:08 broker-safety flat, MIS only, both logged | Codex to add |
| Side end to end | governor, `track2_multi_strategy_engine.py`, desk, `execution_policy.py` | carry `side` from signal to bracket | covered by the governor tests |

## 3. Replace the 9-symbol Kite bridge with a 210-stock feed

- **Research side, done** (`research/shadow/feed.py`): `UpstoxIntradayFeed(keys_from_manifest()).poll(day)` fetches Upstox V2 intraday 1-minute candles for every universe stock, NIFTY 50, INDIA VIX and the sector indices. It resamples them with the same rule that built the history and writes `shared/track2_liquid/live_candles_track2_upstox.json` atomically, with `source_url` on `api.upstox.com` (provenance `UPSTOX_API_V2`).
- **Production side, to do:** schedule `poll` once per bar close, and point `run_day tick --live-file …live_candles_track2_upstox.json` at it. Stop writing `live_candles_track2.json` from Kite.
- At 1 request/s a poll takes about 3 minutes, which is inside the 5-minute journal window. Faster pacing needs Upstox's published limits verified first.

## 4. Data-layer items found tonight

1. **Upstox history is back-adjusted for splits and bonuses; the bhavcopy is not.**
   - 83 of 298 stocks disagree with the bhavcopy close on more than 1% of days, and the ratios are exact split/bonus factors (ANGELONE 0.10, MCX/KOTAKBANK/CAMS 0.20, HDFCAMC/PIDILITIND 0.50).
   - 21.7% of design and 5.4% of holdout eligible stock-days are on adjusted prices.
   - Returns and R are unaffected. The ₹10 floor, tick sizes and share counts before an adjustment are slightly off.
   - **Fix before any v2 study:** rescale each Upstox day by (bhavcopy close ÷ Upstox close) to get actual traded price levels.
   - Antigravity's statement that the historical names were "verified against CM Bhavcopy" holds for 73 of 88; 15 are below 99% agreement.
2. **`trading_calendar.json`** lists 5 exchange holidays of 2026 as trading days. It is not used by research code.
3. **NSE access:** HTTP 403 to this machine since the 16-worker run on 25 Sep. Board meetings, corporate actions and announcements after Nov 2024 are therefore missing, so RESID_REV can only run as RESID_REV_NF.
4. **`LiveDayStore.daily()`** (research, mine) applied the post-CAS cutoff to daily bars. A live shadow run would therefore have had about 40 VIX closes (it needs 120) and m = 0 on every day. Fixed in `882e59a` after the holdout finalized, with a regression test.

## 5. Overnight executive summary (for Yashu)

**Bottom line: none of the seven Track 2 strategies has a positive net edge.**
- **RESID_REV v1** was run once on the holdout and **REJECTED**: net −0.124R, t −3.03, and gross −0.036R, so it was negative even before costs.
- **ORB_PROD and the five other production strategies** lose clearly on the design set (t −6 to −16) and are **KILLED_ON_DESIGN**.

The cause is structural. A ₹38,000 MIS round trip costs about 0.11–0.13% of price (0.06–0.13R at 1–2% stops), and no signal tested earns more than +0.05R gross. The paper-trading gate in AGENTS.md rule 1 is unaffected; nothing here trades.

| Phase | Result |
|---|---|
| **P7.1** snapshot | Sealed `p7_design_20260926` (`72a444d7…`) with ban history. Universe: 122,309 eligible stock-days (~98% of point-in-time F&O member-days). |
| **P7.2a** ORB_PROD | n 14,456; gross −0.003R; fee 0.049R; **net −0.060R, t −7.40 → KILLED_ON_DESIGN**. Negative in every stop, RVOL, VIX and entry-slot bucket (p7_report A.3). |
| **P7.2b** z\* | **3.00** (107,323 stock-days; CI 2.94–3.06). |
| **P7.2c** hold study (RESID_REV_NF) | h4 −0.059R (t −1.83), h8 −0.038R (t −1.00), **EOD −0.038R (t −0.89) selected** by Yashu's rule. The event study shows no reversion (EOD +4 bps, t 0.58). The 2-tick wick sensitivity does not change the choice. |
| **P7.3** lock | YAML LOCKED (`d549fb7`); lock `eeec1a75…` (`35d4311`), **pushed before any holdout read**. |
| **P7.4** holdout, once | **REJECTED**: net −0.124R, t −3.03. Secondaries also negative: 2 ticks −0.135R, trigger basis −0.196R, wick −0.118R, long −0.071R, short −0.162R. ORB_PROD on the holdout: −0.042R (t −4.61). |
| **P7.5** A1 book | RESID_REV holdout **−₹9,331** on 205 trades (max drawdown ₹10,402). ORB_PROD holdout **−₹50,326** on 1,246 trades. Every session ended flat; 0 cap breaches. |
| **Other strategies** | COMPASS −0.067R, LAST_LIGHT −0.076R, TRAPDOOR −0.090R, VOL_SQUEEZE −0.077R, RECOIL −0.147R, all with \|t\| ≥ 6.3 → KILLED_ON_DESIGN. |
| **P8** shadow runner | `run_day` (tick / close / replay), `ShadowRunner` (A1, VIX, clusters, sectors, ledger), `UpstoxIntradayFeed` (replaces the Kite bridge), `reconcile`. The real-data replay of 24 Sep is identical bar by bar (19 of 19). Live use waits on the feed schedule, the daily data refresh and the P8.1 patches. |
| **P9** | Production patch specification (§1–3); security finding (§0); data findings (§4). |
| **Tests** | `python -m pytest -q research/tests` → **590 passed, 6 xfailed** (the xfails are the P8.1 production changes requested from Codex). |

**Commits on `track2/decision-engine` tonight** (all pushed to origin):

| Commit | What |
|---|---|
| `1a18cf7` | Sealed snapshots; ban-file date fix |
| `04ce29b` | Gross / fee / slippage R kept on every counterfactual |
| `5a92373` | Hold-study runner and event study |
| `cf1ea65` | Code commit recorded at run start |
| `6861bf6` | Shadow runner `run_day.py`, replay-tested |
| `baa32a1` | P8.1 contract tests and review request |
| `26db617` | Superseded trials T0024–T0027 |
| `0d1c3f1` | Sensitivities, portfolio summary, diagnostics |
| `015c36c` | `KILLED_ON_DESIGN` transition |
| `a15a5d5` | `run_holdout.py` |
| `09b8a71` | Unique run folders (bug fix) |
| `38990db` | `ShadowRunner` |
| `da82299` | Feeds |
| `bed827b` | Close allocates through the runner; `reconcile.py` |
| `c83b431` | P7.5 portfolio simulation |
| `5c9b298` | Legacy adapters |
| `7f63611` | ORB_PROD killed; official trials registered |
| `5127d32` | Code identity for the holdout (bug fix) |
| `d549fb7` | **LOCK** |
| `35d4311` | **Lock file** |
| `b633b45` / `c729ec7` | Diagnostics script added, then reverted to protect the holdout code identity |
| `7c57316` | P7 report, Part A (before the holdout read) |
| `efc2576` | P8 report |
| `c835d8c` | **Holdout: REJECTED** |
| `882e59a` | Post-holdout fixes (live VIX history, tests pinned to explicit guards, diagnostics re-added) |
| Final commit | Reports, register kills, trials T0035–T0053 |

**What remains:**
1. **Codex:** implement the P8.1/P9 production patches (§0–2) on its own branch and remove the CDP/cookie Kite bridge.
2. **Antigravity:**
   - daily after-close refresh of Upstox data, bhavcopy and ban lists;
   - NSE news fetch (board meetings, corporate actions, announcements after Nov 2024) once NSE stops returning 403: one process, the `nse_events` command, 150 requests a day.
3. **Data:** rescale Upstox bars to bhavcopy price levels before any new study (§4.1).
4. **Research (new pre-registrations only, plan P9):** strategies whose expected move per trade is well above 0.13%, for example catalyst- or event-driven, or longer holds. When the NSE news history completes, a RESID_REV **v2** with the full news filter could be pre-registered, on **new** data (post-CAS shadow). The v1 holdout is spent.
5. The shadow runner can gather prospective evidence in SHADOW mode for any new pre-registered strategy once the feed is scheduled. EXPLOIT mode will allocate nothing, because nothing is promoted.
