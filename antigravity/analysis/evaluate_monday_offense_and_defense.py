"""
evaluate_monday_offense_and_defense.py - Full Tactical Offense + Defense Playbook for Monday (2026-09-14)
Combines Defense (Risk Gates, Rule 5 Sizing, Spoof Gates) with Offense (Pre-Open Auction Sniping, Delivery Absorption).
Evaluates the Top 5 Prospective Candidates.
"""

import os
import sqlite3
import sys

# Ensure UTF-8 console output
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from antigravity.models.circuit_rules import CircuitRuleEngine
from antigravity.models.risk_calculator import CircuitRiskCalculator
from antigravity.models.pre_open_auction_engine import PreOpenAuctionEngine, PreOpenAction
from antigravity.models.delivery_absorption_analyzer import DeliveryAbsorptionAnalyzer, DeliveryVerdict
from antigravity.analysis.circuit_break_sensitivity import calculate_critical_dump_volume

PLAYBOOK_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "shared", "track1_esm", "MONDAY_OFFENSIVE_DEFENSIVE_PLAYBOOK_20260914.md"))
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "logs", "track1_historical.db"))

CANDIDATES = [
    {"scripcode": "544305", "symbol": "One Mobikwik Systems Ltd.", "group": "B", "close": 209.55, "volume": 892396, "prev_close": 188.45, "band_pct": 20.0, "trades": 14210},
    {"scripcode": "533343", "symbol": "Lovable Lingerie Ltd.", "group": "B", "close": 72.16, "volume": 29989, "prev_close": 60.15, "band_pct": 20.0, "trades": 745},
    {"scripcode": "544497", "symbol": "Anlon Healthcare Ltd.", "group": "B", "close": 19.52, "volume": 6881225, "prev_close": 18.08, "band_pct": 10.0, "trades": 35420},
    {"scripcode": "533056", "symbol": "Vedavaag Systems Ltd.", "group": "B", "close": 23.36, "volume": 224892, "prev_close": 20.38, "band_pct": 20.0, "trades": 2150},
    {"scripcode": "500240", "symbol": "Kinetic Engineering Ltd.", "group": "XT", "close": 229.35, "volume": 267625, "prev_close": 215.25, "band_pct": 10.0, "trades": 3120}
]

def generate_playbook():
    print("=== EVALUATING MONDAY CANDIDATES: OFFENSE + DEFENSE ===")
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    evaluated = []
    
    for c in CANDIDATES:
        code = c["scripcode"]
        sym = c["symbol"]
        grp = c["group"]
        cl = c["close"]
        prev = c["prev_close"]
        vol = c["volume"]
        band = c["band_pct"]
        trades = c["trades"]
        
        # 20-day baseline volume
        cur.execute("""
        SELECT volume FROM daily_quotes
        WHERE scripcode = ? AND trade_date < '2026-09-11' AND volume > 0
        ORDER BY trade_date DESC LIMIT 20;
        """, (code,))
        past_vols = [r[0] for r in cur.fetchall()]
        avg_20d = int(sum(past_vols) / len(past_vols)) if past_vols else int(vol / 5)
        
        # 1. DEFENSE SIZING & AIRBAG (Rule 5 & Rule 9)
        sizing = CircuitRiskCalculator.calculate_max_safe_position_by_10day_lc(
            rupees_willing_to_lose=5000.0,
            stock_price=cl,
            daily_volume=avg_20d,  # Using baseline 20-day volume
            band_pct=band,
        )
        shares = sizing.get("max_shares", 0)
        deployed_rs = round(shares * cl, 2)
        worst_loss_rs = sizing.get("calibrated_worst_case_10d_loss", 0.0)
        lc_divisor = sizing.get("lc_divisor")
        
        # 2. OFFENSE MODULE 1: Pre-Open Auction Sniping Plan
        # Calculate simulated indicative auction response at equilibrium
        iep_sim = round(cl * 1.015, 2)  # 1.5% indicative markup in pre-open
        auction_eval = PreOpenAuctionEngine.evaluate_auction_snapshot(
            symbol=sym,
            scripcode=code,
            prev_close=cl, # On Monday morning, Friday's close is the new prev_close
            circuit_band_pct=band,
            indicative_price=iep_sim,
            indicative_bids=int(vol * 0.15),
            indicative_offers=int(vol * 0.10),
            avg_20d_volume=avg_20d,
            risk_budget_rupees=5000.0
        )
        
        # 3. OFFENSE MODULE 2: Delivery & Float Absorption
        deliv_eval = DeliveryAbsorptionAnalyzer.evaluate_delivery_metrics(
            symbol=sym,
            scripcode=code,
            trade_date="2026-09-11",
            security_group=grp,
            total_volume=vol,
            deliverable_volume=None,  # Group XT gets 100%, B gets standard 70% baseline
            avg_20d_volume=avg_20d,
            trade_count=trades
        )
        
        evaluated.append({
            "code": code,
            "symbol": sym,
            "group": grp,
            "close": cl,
            "volume": vol,
            "avg_20d": avg_20d,
            "shares": shares,
            "deployed_rs": deployed_rs,
            "worst_loss_rs": worst_loss_rs,
            "lc_divisor": lc_divisor,
            "band_pct": band,
            "auction": auction_eval,
            "delivery": deliv_eval
        })
        
    conn.close()
    
    # Write Playbook Markdown
    lines = [
        "# Monday Tactical Offense + Defense Execution Playbook (2026-09-14)",
        "",
        "**Execution Phase:** Pre-Open Observation & Auction Matching (09:00:00 - 09:15:00 IST)  ",
        "**Operating Mode:** STRICT OBSERVATION ONLY (AGENTS.md Rule 1 — Real Capital Prohibited)  ",
        "**Desk Milestone Counter:** **0 / 60 Sessions | 0 / 20 Realistically Fillable Entries**  ",
        "",
        "---",
        "",
        "## 1. Dual-Core Evaluation Matrix (Defense + Offense)",
        "",
        "| Scrip Code | Symbol | Group | Friday Close | Sizing (Rule 5 & 9) | Max Loss Airbag | Offense 1: Pre-Open Auction Sniping | Offense 2: Delivery Absorption Footprint |",
        "|---|---|---|---|---|---|---|---|",
    ]
    
    for e in evaluated:
        cd = e["code"]
        sy = e["symbol"]
        gp = e["group"]
        cl = e["close"]
        sh = e["shares"]
        dep = e["deployed_rs"]
        wl = e["worst_loss_rs"]
        div = e.get("lc_divisor")
        b_pct = e.get("band_pct")
        auc = e["auction"]
        deliv = e["delivery"]
        
        div_str = f"{div:.4f} divisor" if div else "ineligible band"
        auc_plan = f"Limit: ₹{auc.recommended_limit_price:.2f} ({auc.queue_priority_window})" if auc.recommended_limit_price else auc.action.value
        deliv_plan = f"{deliv.verdict.value} ({deliv.delivery_pct:.0f}% deliv, {deliv.vol_expansion_ratio:.1f}x vol)"
        
        lines.append(f"| **{cd}** | {sy} | `{gp}` | ₹{cl:.2f} | **{sh} shares** (₹{dep:,}) | **₹{wl:,}** ({div_str}) | **{auc_plan}** | **{deliv_plan}** |")
        
    lines.extend([
        "",
        "---",
        "",
        "## 2. Action Plan: Sub-Second Pre-Open Execution (09:00:00 – 09:08:00 IST)",
        "",
        "### A. Pre-Open Order Entry Rules (Module 1)",
        "1. **09:00:01 IST Queue Placement:** Place limit buy order within the first 5 seconds to secure Queue Rank $R \\le 10$.",
        "2. **Equilibrium Tick Protection:** Never place market orders in pre-open. Limit price must be set 2 ticks above Indicative Equilibrium Price (IEP) and at least 3 ticks below Upper Circuit ceiling.",
        "3. **Rule 3 Prohibition:** If Indicative Offers = 0 at the Upper Circuit at 09:07 IST, cancel order immediately. Zero contra liquidity means adverse selection trap.",
        "",
        "### B. Delivery & Float Lockup Confirmation (Module 2)",
        "1. **XT Series (Kinetic Engineering):** Statutory 100% gross delivery settled. 100% of volume locks into depository demat accounts; zero intraday short-selling or day-trading permitted.",
        "2. **B Series (Mobikwik, Lovable, Anlon, Vedavaag):** Minimum 70%-85% delivery threshold enforced. Any candidate exhibiting volume expansion with < 40% delivery is flagged as **DISTRIBUTION CHURN** and disqualified.",
        "",
        "### C. 3-Stage Laddered Exit Protocol (Offense)",
        r"- **Leg 1 (De-Risking):** Sell **33%** at $+10.0\%$ into two-sided continuous book on Day 2/3.",
        r"- **Leg 2 (Core Profit Target):** Sell **33%** at $+15.0\%$ pre-emptively on Day 3/4.",
        r"- **Leg 3 (Moonshot/Queue Runner):** Place resting limit sell at Upper Circuit price for final **34%** on Day 4. If displayed bids exceed $3.0\times$ volume, abort and exit at market bid to avoid spoof dump.",
        "",
        "---",
        "",
        "## 3. Mandatory Paper-Trading Logging Protocol",
        "- All simulated orders, queue positions, and theoretical fills must be recorded in `CHATGPT/observation_log.csv` and `shared/03_TRADE_LOG.md`.",
        "- Invariant: `counts_toward_paper_gate = false` until 60 full sessions and 20 fillable entries pass.",
    ])
    
    with open(PLAYBOOK_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        
    print(f"\nSuccessfully generated Dual-Core Playbook at: {PLAYBOOK_PATH}")

if __name__ == "__main__":
    generate_playbook()
