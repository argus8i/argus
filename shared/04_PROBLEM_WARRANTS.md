# ARGUS 8i: Master Catalog of Problem Warrants
**Document Version**: 2.0.0  
**Platform**: ARGUS 8i (Track 1: TRIPWIRE | Track 2: BEACON)  
**Governance**: `AGENTS.md` (Tri-Agent Consensus: Antigravity + Claude + Codex)  
**Status**: Living Engineering Ledger  

---

## Overview & Methodology
This catalog records every known problem, architectural gap, institutional shortcoming, and ergonomic friction across **ARGUS 8i**, ranging from critical capital safety risks down to the most trivial UI conveniences.

### Severity Tiers
- **P0 (Critical / Mandatory)**: Capital preservation, real kill switches, data integrity, surveillance violations. (Cannot trade without these).
- **P1 (High / Institutional Core)**: Microstructure telemetry, factor risk decomposition, slippage tracking, transaction cost analysis (TCA).
- **P2 (Medium / Systems & Compliance)**: Infrastructure metrics, automated audit bundles, peak margin monitoring, trade amendment modals.
- **P3 (Trivial / Ergonomics & UX)**: Audio chimes, keyboard hotkeys, tooltips, CSV exports, colorblind mode, millisecond toggles.

---

### Summary Dashboard

| Priority | Total Warrants | Resolved | Open | Status |
| :--- | :---: | :---: | :---: | :--- |
| **P0: Critical Safety & Data** | 4 | 4 | 0 | **100% RESOLVED** |
| **P1: Microstructure & Risk** | 7 | 3 | 4 | **43% RESOLVED** |
| **P2: Systems & Compliance** | 7 | 0 | 7 | **OPEN** |
| **P3: Trivial Ergonomics & UX** | 10 | 10 | 0 | **100% RESOLVED** |
| **TOTAL** | **28** | **17** | **11** | **61% COMPLETE** |

---

## Tier 1: P0 Critical Safety & Operational Integrity (Resolved)

### [x] Warrant W1: Real Operational Emergency Kill Switches
- **Problem**: The terminal had mock UI buttons for pause/squareoff that didn't stop order execution or flatten positions in the engine.
- **Resolution**:
  - Implemented `POST /api/action/pause` toggling `is_paused: bool`.
  - Implemented fail-closed check in `POST /api/action/enter` rejecting trades with HTTP 403 Forbidden when paused.
  - Implemented `POST /api/action/squareoff` executing an immediate market flatten writing to `paper_orders.jsonl` and `events.jsonl`.
  - Added interactive confirmation modal in NIGHTWATCH UI.
- **Status**: **RESOLVED** (Commit: 22-Sep-2026)

### [x] Warrant W2: Dynamic Live Data Binding (Zero Mock Numbers)
- **Problem**: The terminal served static hardcoded constants (Nifty 25,480.50, static CDSL/IREDA brackets, static radar).
- **Resolution**:
  - Bound Nifty quote and radar candidates directly to `shared/track2_liquid/live_depth_track2.json`.
  - Bound macro regime and volume requirements directly to `shared/track2_liquid/live_orb_status.json`.
  - Bound execution ledger to `shared/track2_liquid/paper_orders.jsonl`.
- **Status**: **RESOLVED** (Commit: 22-Sep-2026)

### [x] Warrant W3: F&O MWPL Ban Period Tracking
- **Problem**: Securities entering the >95% Market Wide Position Limit (MWPL) ban period are illiquid and dangerous; system lacked automated screening against the official NSE ban list.
- **Resolution**:
  - Added `is_in_fo_ban: bool` and `DISQUALIFIED_MWPL_BAN` to `track2_surveillance_monitor.py`.
  - Ingested official NSE ban endpoint (`https://www.nseindia.com/api/fo-secban`) in `track2_official_source_ingestor.py`.
- **Status**: **RESOLVED** (Commit: 22-Sep-2026)

### [x] Warrant W14: VIGIL 7-Stage Real-Time Health Watchdog
- **Problem**: System health was monitored in a separate script (`track2_live_monitor.py`) and was not visible in the primary trading terminal.
- **Resolution**:
  - Integrated `build_monitor_state()` into `track2_terminal_server.py`.
  - Added 7-stage telemetry strip (`KITE 9444`, `DEPTH`, `15M BARS`, `BASELINES`, `SURVEILLANCE`, `DECISION ENGINE`, `EVIDENCE`) to NIGHTWATCH header.
- **Status**: **RESOLVED** (Commit: 22-Sep-2026)

---

## Tier 2: P1 Microstructure, Execution & Risk Telemetry (In Progress: 3/7 Resolved)

### [x] Warrant W4: Implementation Shortfall & Slippage vs. Arrival Price
- **Problem**: System previously logged order price and fill price without decomposing the difference into market impact, spread crossing, and delay slippage.
- **Resolution**:
  - Implemented `calculate_implementation_shortfall(signal_price, limit_price, fill_price, side)` in `track2_paper_execution.py`.
  - Decomposed shortfall into Spread Crossing Cost (`spread_cost_bps`), Delay/Impact Slippage (`delay_impact_bps`), and Total Slippage (`slippage_bps`).
  - Stored and tracked shortfall telemetry on `BracketOrderState` and `paper_orders.jsonl`.
  - Added color-coded `SLIP: +X.X bps` badge on NIGHTWATCH bracket cards with detailed breakdown tooltip on hover.
- **Status**: **RESOLVED** (Commit: 22-Sep-2026)

### [x] Warrant W5: Order Book Visual Depth Ladder & Queue Rank
- **Problem**: Trader could not see the live bid/ask depth or estimate queue position for limit orders.
- **Resolution**:
  - Implemented `generate_depth_ladder(symbol, ltp, spread_ticks, depth_levels)` producing 5-level bids/asks with depth imbalance ratio and spread in bps.
  - Implemented `calculate_queue_rank(order_qty, order_price, side, depth)` calculating volume ahead, queue ratio ($V_{\text{ahead}} / Q_{\text{order}}$), and required turnover for fill per Rule 4.
  - Implemented `GET /api/depth?symbol=<SYM>` endpoint in `track2_terminal_server.py`.
  - Built institutional `#depth-modal` in NIGHTWATCH with split 5-level green/red depth ladder, imbalance split bar, and queue position estimator. Added `[📊 DEPTH]` button on brackets and `[📊]` icon on VECTOR Radar rows.
- **Status**: **RESOLVED** (Commit: 22-Sep-2026)

### [ ] Warrant W6: Factor Risk Decomposition
- **Problem**: Portfolio risk is tracked purely by nominal rupee risk (₹1,500/trade, ₹4,500 aggregate), ignoring hidden factor tilts (e.g. all 3 active positions being high-beta energy stocks).
- **Required Solution**: Decompose open portfolio into Momentum, Volatility, Beta, and Size factor exposures with visual warning dials.

### [ ] Warrant W7: Dynamic Covariance & Cross-Asset Correlation Matrix
- **Problem**: System allows 2 scrips in the same sector cluster, but doesn't check if two different sectors have a 0.90 rolling correlation (e.g., PSU Banks and Power).
- **Required Solution**: Display a $3 \times 3$ rolling correlation matrix for active positions and warn if pairwise correlation $> 0.70$.

### [ ] Warrant W8: Historical Value-at-Risk (VaR) & Expected Shortfall (CVaR)
- **Problem**: System measures risk in static R-multiples rather than statistical probability distributions under extreme tail events.
- **Required Solution**: Calculate 1-day 99% VaR and CVaR (Expected Shortfall) using 252-day historical returns for active positions.

### [ ] Warrant W9: Intraday P&L Attribution (Alpha vs Sector vs Market)
- **Problem**: When a trade makes +₹1,800, we don't know whether it was pure stock alpha, sector tailwind, or Nifty market drift.
- **Required Solution**: Implement Brinson-style intraday attribution:
  $$\text{Return} = \text{Market Drift} + \text{Sector Allocation} + \text{Idiosyncratic Alpha}$$

### [x] Warrant W10: Explicit Transaction Cost Decomposition
- **Problem**: Bracket ledger displayed an estimated transaction charge without breaking down statutory taxes.
- **Resolution**:
  - Implemented `calculate_transaction_costs` and `aggregate_transaction_costs` in `track2_paper_execution.py` itemizing Brokerage, STT, Exchange Turnover, GST, SEBI Turnover Fee, and Stamp Duty.
  - Attached `cost_breakdown` object to all brackets and `GET /api/state`.
  - Built interactive `#tca-modal` in NIGHTWATCH and added `[ℹ TCA]` button on each bracket card displaying the exact rupee breakdown table, gross turnover, and statutory drag in bps.
- **Status**: **RESOLVED** (Commit: 22-Sep-2026)

---

## Tier 3: P2 Systems, Infrastructure & Compliance (Open)

### [ ] Warrant W11: Tick-to-Decision & Feed Latency Histograms
- **Problem**: We track clock drift (+0.02s), but lack end-to-end latency distribution (tick arrival to order decision).
- **Required Solution**: Measure round-trip execution latency in milliseconds and display a rolling P50, P90, and P99 latency histogram.

### [ ] Warrant W12: Process Memory & CPU Leak Watchdog
- **Problem**: If the terminal server or paper desk runs for 8 hours, undetected memory leaks could crash the daemon during market hours.
- **Required Solution**: Add memory/CPU profiling to VIGIL, triggering an alert if process RAM exceeds 300 MB.

### [ ] Warrant W13: Peak Margin & SEBI T+1 Intraday Cash Verification
- **Problem**: The system reserves a ₹50,000 cash buffer, but doesn't verify against broker peak margin snapshot rules.
- **Required Solution**: Enforce automated checks confirming that 100% of required margin is available in unencumbered cash with zero reliance on pledged collateral.

### [ ] Warrant W15: Automated Audit Evidence Bundle Exporter
- **Problem**: Generating audit evidence requires manually finding JSON files and computing SHA-256 hashes.
- **Required Solution**: Add an `[EXPORT AUDIT BUNDLE]` button in NIGHTWATCH that packages the day's `paper_orders.jsonl`, `events.jsonl`, `live_depth.json`, and SHA-256 manifest into a single `.zip` file.

### [ ] Warrant W16: Multi-Monitor Layout Presets & Workspace Persistence
- **Problem**: The UI is a single responsive layout; traders with multi-monitor setups cannot detach the Radar table or Ledger.
- **Required Solution**: Add layout toggle presets: Single Screen, Dual-Monitor (Radar on Screen 1, Risk & Brackets on Screen 2), and Compact Laptop mode.

### [ ] Warrant W17: Manual Trade Amendment & Partial Exit Modal
- **Problem**: In paper trading, if a trader wants to manually trail a stop early or take a partial fill, there is no UI modal to do so.
- **Required Solution**: Add an `[AMEND]` button on each active bracket card to adjust trailing stop or trigger partial exit.

### [ ] Warrant W18: Session Replay & Simulator Backplay Mode
- **Problem**: Testing new rules requires waiting for live market hours or running CLI scripts.
- **Required Solution**: Port historical tick recorder to NIGHTWATCH allowing 10x replay of historical sessions (e.g. 2026-09-21) inside the UI.

---

## Tier 4: P3 Trivial Ergonomics, Sensory & Visual Frictions (Resolved)

### [x] Warrant W19: Audio / Sound Alerts
- **Problem**: Trader must stare at the screen continuously to notice a 15m breakout trigger or bracket fill.
- **Resolution**:
  - Implemented client-side Web Audio API synthesizer in `index.html` (zero external audio files or CDN latency).
  - Designed distinct tones:
    - High-frequency rising arpeggio (587Hz -> 880Hz -> 1174Hz) for ORB Breakouts.
    - Soft double-click (987Hz -> 1318Hz) for Tranche 1 fills.
    - Low-frequency warning drop (220Hz sawtooth) for Emergency Flatten / risk events.
    - Clean toggle click (440Hz sine) for Pause/Resume.
  - Added header toggle button (`🔊 SOUND` / `🔇 MUTED`) with `localStorage` persistence.
- **Status**: **RESOLVED** (Commit: 22-Sep-2026)

### [x] Warrant W20: Keyboard Shortcuts (Hotkeys)
- **Problem**: All actions require mouse clicks.
- **Resolution**:
  - Implemented global `keydown` event listeners:
    - `Space`: Toggles operational pause (fail-closed entry gate).
    - `Shift + Esc`: Opens Emergency Flatten confirmation modal.
    - `R`: Triggers pre-market universe re-scan.
    - `1` - `8`: Highlights and scrolls to corresponding candidate in VECTOR Radar matrix.
    - `?`: Toggles Hotkeys Cheat-Sheet modal.
    - `M`: Toggles audio sound/mute.
    - `C`: Toggles colorblind mode.
    - `Esc`: Closes any active modal.
  - Added interactive `#hotkeys-modal` cheat-sheet and header button `[⌨ KEYS [?]]`.
- **Status**: **RESOLVED** (Commit: 22-Sep-2026)

### [x] Warrant W21: Plain-English Tooltips on Hover
- **Problem**: Non-technical or junior operators may not remember what "15M VOL MULT", "E3 FILL", "ATR14 %", or "A/D RATIO" mean.
- **Resolution**:
  - Added plain-English `title="..."` tooltips across all table headers, macro cards, risk dials, and funnel metrics.
  - Explains formulas, thresholds, and regulatory constraints (e.g. Rule 1 60-session/20-fill requirements, 1R ₹1,500 sizing formula).
- **Status**: **RESOLVED** (Commit: 22-Sep-2026)

### [x] Warrant W22: One-Click CSV / Excel Data Download
- **Problem**: Copying table data to Excel requires manual copy-pasting.
- **Resolution**:
  - Implemented client-side CSV generator via `Blob` and dynamic `<a download>` links.
  - Added `[📥 CSV]` export buttons to:
    - VECTOR Radar Matrix (`ARGUS8i_RADAR_<TIMESTAMP>.csv`).
    - SPLITLOCK Bracket Ledger (`ARGUS8i_BRACKETS_<TIMESTAMP>.csv`).
    - BLACKBOX Audit Stream (`ARGUS8i_AUDIT_<TIMESTAMP>.csv`).
- **Status**: **RESOLVED** (Commit: 22-Sep-2026)

### [x] Warrant W23: Colorblind Accessibility Mode
- **Problem**: Green/red color coding is difficult for colorblind operators to distinguish during high-stress market moves.
- **Resolution**:
  - Added `.colorblind-mode` CSS palette:
    - Gains/Positive: High-contrast Cobalt Blue (`#38bdf8` / Sky Blue).
    - Losses/Negative: High-contrast Vermilion Orange (`#fb923c` / Amber-Orange).
  - Added header toggle button (`👁 CB: ON/OFF`) with `localStorage` persistence.
- **Status**: **RESOLVED** (Commit: 22-Sep-2026)

### [x] Warrant W24: Browser Tab Title Flash & Favicon Alert
- **Problem**: If the trader switches to another browser tab, they miss breakout alerts.
- **Resolution**:
  - Implemented `flashTabAlert(message)`:
    - Alternates `document.title` between `[!] <ALERT>` and original title every 700ms for 10 cycles.
    - Dynamically flashes SVG favicon from green (`#10b981`) to amber (`#f59e0b`) during alert state.
- **Status**: **RESOLVED** (Commit: 22-Sep-2026)

### [x] Warrant W25: Theme Contrast Switcher (Pitch Black vs. Navy Slate)
- **Problem**: Different monitors and ambient lighting conditions cause eye strain.
- **Resolution**:
  - Added `.theme-oled` CSS styles providing true OLED `#000000` pitch black background and dark panels.
  - Added header toggle button (`🎨 OLED` / `🎨 NAVY`) with `localStorage` persistence.
- **Status**: **RESOLVED** (Commit: 22-Sep-2026)

### [x] Warrant W26: Microsecond Timestamp Precision Toggle
- **Problem**: Timestamps display in seconds (`09:34:12 IST`), which doesn't show execution sequence order when two events occur in the same second.
- **Resolution**:
  - Added header toggle button (`⏱ MS: ON/OFF`) with `localStorage` persistence.
  - Dynamically switches live clock and timestamp formats to millisecond precision (`HH:MM:SS.mmm IST`).
- **Status**: **RESOLVED** (Commit: 22-Sep-2026)

### [x] Warrant W27: Sticky Table Headers on Scroll
- **Problem**: Scrolling down through long lists scrolls table column titles out of view.
- **Resolution**:
  - Added `sticky top-0 bg-slate-900/95 backdrop-blur z-10` and `.sticky-header` to table headers.
  - Wrapped VECTOR Radar table in `overflow-y-auto max-h-[380px]` container so column titles lock into place during scroll.
- **Status**: **RESOLVED** (Commit: 22-Sep-2026)

### [x] Warrant W28: Live Ping / Heartbeat Indicator
- **Problem**: The trader cannot tell if the local server has lagged or died without looking at the terminal window.
- **Resolution**:
  - Implemented round-trip latency measurement using `performance.now()` on every `/api/state` poll cycle.
  - Added live ping badge in header:
    - `< 60ms`: `● <RTT>ms` (emerald).
    - `60ms - 150ms`: `● <RTT>ms` (amber).
    - `> 150ms`: `● <RTT>ms` (rose).
    - Disconnected / server dead: `● OFFLINE` (rose border).
- **Status**: **RESOLVED** (Commit: 22-Sep-2026)

---

## Execution Governance
All warrants must be resolved following the Tri-Agent Protocol:
1. **Antigravity**: Implementation and mathematical modeling.
2. **Claude**: Microstructural red-teaming and adverse selection review.
3. **Codex**: System reliability, contracts, and regression verification.
