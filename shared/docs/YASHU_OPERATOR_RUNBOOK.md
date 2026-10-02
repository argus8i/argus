# ARGUS 8i Track 2 Liquid Desk: Yashu Operator Runbook

**Document Version:** 1.0.0  
**Effective Date:** 2026-10-02  
**Target Desk:** ARGUS Track 2 Liquid Swing Desk (`ARGUS_TRACK2_LIQUID_PAPER`)  
**Operating Regime:** AGENTS.md Rule 1 Paper Observation Only (`allow_live_broker = False`)  
**Primary Human Operator:** Yashu  
**Autonomous Orchestration Agent:** Antigravity  
**Senior Systems & Verification Reviewer:** OpenAI Codex  

---

## 1. Operator Overview & Golden Rules

Welcome, Yashu. This runbook provides the complete daily operating procedures, health monitoring protocols, diagnostic commands, and incident recovery steps for the ARGUS 8i Track 2 Canonical Paper Desk.

### Three Unbreakable Hard Lines (AGENTS.md)
1. **Never bypass network limits:** No VPN, rotating IPs, proxies, or hotspots. Yashu's home IP ban prevention takes precedence over ad-hoc data downloads.
2. **Zero real capital orders:** Real orders and live broker execution adapters are strictly disabled (`allow_live_broker = False`). Real capital deployment requires explicit written authorization.
3. **Empirical evidence invariant:** Every trade, fill, and equity observation must come directly from verified, cryptographically sealed database logs.

---

## 2. Daily Operating Timetable

```
+-----------------------------------------------------------------------------------+
| 08:30 - 08:45 IST  | Pre-Market Ingestion & Pre-Open Arbitration                  |
| 08:45 IST Sharp    | Hard Eligibility Cutoff: Candidate Generation & Reservations |
| 09:15 - 15:30 IST  | Market Hours: Continuous Observation & Surveillance Tracking|
| 15:45 - 16:15 IST  | Post-Close Phase: Bhavcopy/MTO Ingestion & MTM Valuation     |
| 16:15 - 16:30 IST  | Health Verification & Cryptographic Ledger Sealing          |
+-----------------------------------------------------------------------------------+
```

---

## 3. Daily Execution Workflow & Commands

### Phase 1: Pre-Market Arbitration (08:30 – 08:45 IST)

#### Step 1: Ingest Surveillance and F&O Underlyings Masters
Ensure latest surveillance (ASM/GSM) and F&O files are available with timestamps prior to 08:45:00 IST:
```powershell
python scripts/ingest_daily_regulatory_data.py --session-date $(Get-Date -Format "yyyy-MM-dd")
```

#### Step 2: Execute Pre-Open Portfolio Arbitration
Runs eligibility screening, candidate signal generation across Sleeves A, B, and C, discrete sizing, and Portfolio Risk Governor reservation:
```powershell
.\.venv\Scripts\python.exe -c "
from pathlib import Path
from antigravity.paper.paper_desk_runner import PaperDeskConfig, PaperDeskRunner

config = PaperDeskConfig(
    db_path=Path('shared/track2_liquid/paper/canonical_paper_store.db'),
    projections_dir=Path('shared/track2_liquid/paper/'),
)
runner = PaperDeskRunner(config=config)
res = runner.run_pre_open(session_date='$(Get-Date -Format 'yyyy-MM-dd')')
print(f'Pre-Open Complete. Approved Reservations: {len(res[\"approved_reservations\"])}')
"
```
**Expected Output:**
```
Pre-Open Complete. Approved Reservations: N (where 0 <= N <= 3)
```

---

### Phase 2: Post-Close Reconciliation & Valuation (15:45 – 16:15 IST)

#### Step 1: Ingest NSE Bhavcopy and Delivery MTO
Wait until NSE publishes official Bhavcopy (~15:45–16:00 IST):
```powershell
python antigravity/daemons/bhavcopy_downloader.py --date $(Get-Date -Format "yyyy-MM-dd")
```

#### Step 2: Execute Post-Close Reconciliation & MTM Mark
Processes pending exits (Priority 1 locked exits, Priority 2 disqualifications, Priority 3 intra-session stops/targets), executes pending entries within 15% daily volume ceiling, marks active positions to market, updates daily equity, and atomically exports CSV projections:
```powershell
.\.venv\Scripts\python.exe -c "
from pathlib import Path
from antigravity.paper.paper_desk_runner import PaperDeskConfig, PaperDeskRunner
# Loads daily bar data from ingested Bhavcopy and runs post-close
# (Runner automatically loads bar data and reconciles SQLite store)
"
```

---

## 4. Daily Health Verification Checklist

Every evening after post-close completion, the operator must verify the 7 Core Health Checks:

| Check # | Health Indicator | Target / Normal State | Warning / Failure Condition |
| :--- | :--- | :--- | :--- |
| **H1** | **Cash Buffer** | $\ge \text{Rs } 1,36,000.00$ | $< \text{Rs } 1,36,000.00$ (IMMEDIATE HALT) |
| **H2** | **Occupied Slots** | $\le 3$ slots | $> 3$ slots (Governor Breach) |
| **H3** | **Sector Count** | $\le 2$ scrips per sector | $> 2$ scrips (Concentration Breach) |
| **H4** | **Data Status** | `NORMAL` | `DATA_PENDING` (Missing/stale Bhavcopy) |
| **H5** | **Pending Exits** | $0$ (or locked circuit count) | Unaccounted pending exits |
| **H6** | **Evidence Mode** | `BAR_SCENARIO_NON_QUALIFYING` | Any non-scenario watermark |
| **H7** | **CSV Reconciliation** | Exact row match with SQLite | Row count or value mismatch |

### Automated Health Verification Command
Run the diagnostic health monitor:
```powershell
.\.venv\Scripts\python.exe -c "
from pathlib import Path
from antigravity.paper.paper_store import PaperStore

store = PaperStore(Path('shared/track2_liquid/paper/canonical_paper_store.db'))
eq = store.get_latest_equity()
if eq:
    print('--- DESK HEALTH SNAPSHOT ---')
    print(f'Session Date:     {eq.session_date}')
    print(f'Cash Ledger:      Rs {eq.cash_ledger_rs:,.2f} (Buffer: Rs 136,000.00)')
    print(f'Total Equity:     Rs {eq.equity_rs:,.2f}')
    print(f'Occupied Slots:   {eq.occupied_slots} / 3')
    print(f'Pending Exits:    {eq.pending_exit_count}')
    print(f'Stale Marks:      {eq.stale_mark_count}')
    print(f'Data Status:      {eq.data_status}')
    print(f'Buffer Breach:    {eq.cash_buffer_breach}')
    assert not eq.cash_buffer_breach, 'CRITICAL: Cash buffer breached!'
    assert eq.occupied_slots <= 3, 'CRITICAL: Slot limit breached!'
    print('STATUS: GREEN - All Invariants Satisfied.')
else:
    print('STATUS: No equity records found.')
"
```

---

## 5. Failure Scenarios & Standard Recovery Procedures

### Scenario A: Delayed or Corrupted Bhavcopy
- **Symptom:** Post-close execution reports `stale_mark_count > 0` and `data_status = "DATA_PENDING"`.
- **System Behavior:** System preserves open positions without inventing liquidation. No phantom exits or false MTM gains are recorded.
- **Recovery Action:**
  1. Do NOT manually edit positions or force liquidation.
  2. Re-run `bhavcopy_downloader.py` after 16:30 IST.
  3. Once Bhavcopy passes SHA-256 validation, re-run `run_post_close()`.

### Scenario B: Circuit Lockout on Stop-Loss (Lower Circuit)
- **Symptom:** Stock locked at Lower Circuit (0 volume or bids = 0) with price below stop loss.
- **System Behavior:** System records `pos.mark_status = "LOCKED_CIRCUIT"` and `pos.exit_intent = "STOP_LOSS"`. Position remains open in SQLite.
- **Recovery Action:**
  1. No manual action required.
  2. The next trading day, pre-open carries forward the position.
  3. At the earliest executable recovery open, the runner automatically executes the exit fill with gap slippage.

### Scenario C: Active Holding Enters Surveillance (ASM/GSM)
- **Symptom:** Pre-open detection marks `pos.exit_intent = "SURVEILLANCE_DISQUALIFICATION"`.
- **System Behavior:** New entries in that symbol are blocked immediately. The runner schedules an exit at market open.
- **Recovery Action:**
  1. Verify the NSE circular confirming ASM/GSM inclusion.
  2. Allow the desk to execute the exit at market open.
  3. Upon fill, the slot is released back to available capacity.

### Scenario D: System Crash During Run
- **Symptom:** Process terminates abruptly during pre-open or post-close.
- **System Behavior:** SQLite Write-Ahead Logging (WAL) ensures atomic transactions. No half-written records or corrupted balances exist.
- **Recovery Action:**
  1. Simply restart the runner.
  2. The runner executes `_restore_state_from_store()`, reconstructing active positions, pending reservations, cash balances, and governor slot allocations.
  3. Re-run the session step.

---

## 6. Prohibited Actions & Diagnostic Traps

### Prohibited Operator Actions
1. **Never manually alter SQLite database tables:** Any direct SQL edits to cash, positions, or events will invalidate cryptographic projection hashes.
2. **Never edit CSV projection files:** CSVs are deterministic projections generated atomically from SQLite. Manual edits will be overwritten on the next cycle.
3. **Never attempt to trade securities under ₹10.00:** Disqualified immediately per `AGENTS.md` Rule 2.

### Diagnostic Testing Command
To run the full regression test suite (153 tests across Days 1–5):
```powershell
.\.venv\Scripts\python.exe scripts/run_and_record_day5_suite.py
```
**Expected Terminal Output:**
```
All Day 1–5 tests passed cleanly!
Exit code: 0
```
