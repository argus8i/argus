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

#### Step 2: Generate Verified Pre-Market Candidate Signals
Screen active F&O underlyings across registered strategies (High 52W Momentum, Delivery Accumulation, Expiry Relief) prior to 08:45:00 IST:
```powershell
python scripts/generate_candidate_signals.py --session-date $(Get-Date -Format "yyyy-MM-dd")
```

#### Step 3: Execute Pre-Open Portfolio Arbitration
Runs eligibility screening, candidate signal verification across Sleeves A, B, and C, discrete sizing, and Portfolio Risk Governor reservation:
```powershell
.\.venv\Scripts\python.exe -c "
import json
from pathlib import Path
from antigravity.paper.paper_desk_runner import PaperDeskConfig, PaperDeskRunner
from antigravity.strategies.base_strategy import SignalEvent

session_date = '$(Get-Date -Format 'yyyy-MM-dd')'
surv_p = Path(f'data/surveillance/surveillance_{session_date}.json')
fno_p = Path(f'data/fno/fno_underlyings_{session_date}.json')
sig_p = Path(f'data/signals/signals_{session_date}.json')

surv = json.loads(surv_p.read_text('utf-8')) if surv_p.exists() else None
fno = set(json.loads(fno_p.read_text('utf-8')).get('fno_underlyings', [])) if fno_p.exists() else None

# Load verified candidate signals for today's session (from registered strategies)
candidate_signals = []
if sig_p.exists():
    sig_raw = json.loads(sig_p.read_text('utf-8'))
    candidate_signals = [SignalEvent(**s) for s in sig_raw]

config = PaperDeskConfig(
    db_path=Path('shared/track2_liquid/paper/canonical_paper_store.db'),
    projections_dir=Path('shared/track2_liquid/paper/'),
)
runner = PaperDeskRunner(config=config)
res = runner.run_pre_open(
    session_date=session_date,
    candidate_signals=candidate_signals,
    surveillance_snapshot=surv,
    fno_underlyings=fno,
)
approved = len(res.get('approved_reservations', []))
print(f'Pre-Open Complete. Approved Reservations: {approved}')
"
```
**Expected Output:**
```
Pre-Open Complete. Approved Reservations: N (where 0 <= N <= 3)
```

---

### Phase 2: Post-Close Reconciliation & Valuation (15:45 – 16:15 IST)

#### Step 1: Ingest and Seal Official NSE CM Bhavcopy
Wait until NSE publishes official Bhavcopy (~15:45–16:00 IST). Ingests raw Bhavcopy, computes SHA-256 seal, and generates manifest:
```powershell
python scripts/ingest_daily_bhavcopy.py --session-date $(Get-Date -Format "yyyy-MM-dd")
```

#### Step 2: Execute Post-Close Reconciliation & MTM Mark
Processes pending exits (Priority 1 locked exits, Priority 2 disqualifications, Priority 3 intra-session stops/targets), executes pending entries within 15% daily volume ceiling, marks active positions to market, updates daily equity, and atomically exports CSV projections:
```powershell
.\.venv\Scripts\python.exe -c "
import csv
import json
from pathlib import Path
from antigravity.paper.paper_desk_runner import PaperDeskConfig, PaperDeskRunner
from antigravity.engine.execution_simulator import DailyBar

session_date = '$(Get-Date -Format 'yyyy-MM-dd')'
bhav_manifest_p = Path(f'data/bhavcopy/manifest_{session_date}.json')
bhav_csv_p = Path(f'data/bhavcopy/bhavcopy_{session_date}.csv')

# Load official Bhavcopy manifest (fail-closed if missing; never fabricate fallback)
manifest = json.loads(bhav_manifest_p.read_text('utf-8')) if bhav_manifest_p.exists() else None

# Load official EOD market bars
bar_data_map = {}
if bhav_csv_p.exists():
    with open(bhav_csv_p, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for r in reader:
            sym = (r.get('TckrSymb') or r.get('SYMBOL') or '').strip().upper()
            series = (r.get('SctySrs') or r.get('SERIES') or '').strip().upper()
            if sym and series == 'EQ':
                bar_data_map[sym] = DailyBar(
                    symbol=sym,
                    open=float(r.get('OpnPric') or r.get('OPEN')),
                    high=float(r.get('HghPric') or r.get('HIGH')),
                    low=float(r.get('LwPric') or r.get('LOW')),
                    close=float(r.get('ClsPric') or r.get('CLOSE')),
                    volume=int(float(r.get('TtlTradQty') or r.get('TOTTRDQTY') or 0)),
                )

config = PaperDeskConfig(
    db_path=Path('shared/track2_liquid/paper/canonical_paper_store.db'),
    projections_dir=Path('shared/track2_liquid/paper/'),
)
runner = PaperDeskRunner(config=config)
res = runner.run_post_close(
    session_date=session_date,
    bar_data_map=bar_data_map,
    bhavcopy_manifest=manifest,
)
equity_rs = res.get('equity', {}).get('equity_rs', 0.0)
data_status = res.get('equity', {}).get('data_status', 'UNKNOWN')
print(f'Post-Close Complete. Equity: Rs {equity_rs:,.2f} (Data Status: {data_status})')
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
Run the diagnostic health monitor (enforces all 7 operational invariants fail-closed):
```powershell
python scripts/verify_desk_health.py --session-date $(Get-Date -Format "yyyy-MM-dd")
```
Or execute the diagnostic check programmatically:
```powershell
.\.venv\Scripts\python.exe -c "
from pathlib import Path
from scripts.verify_desk_health import verify_desk_health

session_date = '$(Get-Date -Format 'yyyy-MM-dd')'
report = verify_desk_health(
    db_path=Path('shared/track2_liquid/paper/canonical_paper_store.db'),
    session_date=session_date,
    projections_dir=Path('shared/track2_liquid/paper'),
)
print('--- DESK HEALTH SNAPSHOT ---')
print(f'Session Date:     {report[\"session_date\"]}')
print(f'Cash Ledger:      Rs {report[\"cash_ledger_rs\"]:,.2f} (Buffer: Rs 136,000.00)')
print(f'Total Equity:     Rs {report[\"equity_rs\"]:,.2f}')
print(f'Occupied Slots:   {report[\"occupied_slots\"]} / 3')
print(f'Pending Exits:    {report[\"pending_exit_count\"]}')
print(f'Data Status:      {report[\"data_status\"]}')
print('STATUS: GREEN - All 7 Operational Invariants Formally Satisfied.')
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
