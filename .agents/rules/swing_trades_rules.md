---
name: swing_trades_rules
description: Permanent trading, risk, and microstructure rules for Project Swing Trades
always_on: true
---

# Swing Trades Workspace Rules

1. **Mandatory Paper-Trading Gate:** Minimum 60 prospective sessions and 20 fillable paper trades logged in `CHATGPT/observation_log.csv` and `shared/03_TRADE_LOG.md` before any real capital deployment.
2. **Absolute ₹10.00 Price Floor:** Reject all stocks priced under ₹10.00 due to tick tax and extreme illiquidity.
3. **No Locked-Circuit Chasing:** Never buy an upper-circuit-locked stock. Fills on locked circuits are adverse-selection exits.
4. **4-State Execution Architecture:** Model `LOCKED_NO_BID`, `QUEUED`, `PARTIAL`, and `FILLED`. No deterministic fill guarantees.
5. **10-Day Lower-Circuit Risk Calibration:** Max position size = (Rupees willing to lose) / 0.40.
6. **Surveillance Pre-Emption:** Monitor daily circuit bands via `band_revision_monitor.py`. Narrowing bands or ESM/GSM/BE triggers an immediate exit.
7. **Pre-Circuit Accumulation Only:** Buy only in liquid consolidation bases (volume ratio $\ge 3\times$, spread $< 1\%$, daily range $> 3\%$). Exit into UC buyer depth on Day 3/4.
8. **Tri-Agent Protocol:** Cross-verify work across Antigravity, Claude, and ChatGPT.
