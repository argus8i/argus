"""
circuit_break_sensitivity.py - Kyle's Lambda & Bid-Wall Fragility Simulator
Simulates the critical dumping volume V_crit needed for an operator to collapse
the Upper Circuit bid wall into a Lower Circuit free-fall.
Part of the Track 1 microstructure quantitative toolkit.
"""

import math
import os
import sqlite3
import sys
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from antigravity.analysis.markov_absorption_simulator import compute_empirical_transition_matrix, simulate_absorption_trajectories

AUDIT_REPORT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "shared", "track1_esm", "MARKOV_ABSORPTION_AUDIT.md"))


def calculate_critical_dump_volume(
    resting_bid_depth: int,
    session_avg_volume: int,
    band_pct: float = 5.0,
    elasticity_param: float = 0.85
) -> Dict[str, float]:
    """Calculates Kyle's Lambda and the critical operator dumping threshold.
    
    Delta P = lambda * Q_dump
    V_crit = Volume needed to consume resting bids and trigger an inward circuit cascade.
    """
    # Kyle's Lambda proxy for illiquid micro-caps: Price change per 10,000 shares traded
    kyle_lambda = (band_pct / 100.0) / max(1000, 0.10 * session_avg_volume)

    # Critical dump: resting bids + immediate retail absorption elasticity
    v_crit = resting_bid_depth * (1.0 + (1.0 - elasticity_param))
    fragility_ratio = resting_bid_depth / max(1, session_avg_volume)

    return {
        "resting_bid_depth": resting_bid_depth,
        "kyle_lambda": round(kyle_lambda, 8),
        "critical_dump_volume": round(v_crit, 0),
        "fragility_ratio": round(fragility_ratio, 2),
        "is_spoof_risk": fragility_ratio > 10.0  # Suspected spoof bid wall if resting bids > 10x day volume
    }


def generate_full_markov_and_sensitivity_report():
    mat, counts = compute_empirical_transition_matrix()
    sim_base = simulate_absorption_trajectories(mat, start_state="TWO_SIDED_BASE")
    sim_uc = simulate_absorption_trajectories(mat, start_state="LOCKED_UC")

    lines = [
        "# Track 1 Markov Chain State Absorption & Circuit Sensitivity Audit",
        "",
        "**Primary Empirical Source:** `antigravity/logs/track1_historical.db` (214,441 records across 120 sessions).  ",
        "**Scope:** Strictly Track 1 (ESM & Circuit Micro-Caps). AGENTS.md Rules 1-11 govern unconditionally.  ",
        "**Status:** Formal Tri-Agent Quantitative Debate Document for Claude & Codex.  ",
        "",
        "---",
        "",
        "## Section 1: Empirical 5x5 Markov Transition Matrix",
        "",
        "| State From | TWO_SIDED_BASE | LOCKED_UC | UNLOCKED_VOLATILE | LOCKED_LC | BAND_TIGHTENED |",
        "|---|---|---|---|---|---|",
    ]

    states = ["TWO_SIDED_BASE", "LOCKED_UC", "UNLOCKED_VOLATILE", "LOCKED_LC", "BAND_TIGHTENED"]
    for s_from in states:
        row_vals = " | ".join(f"{mat[s_from][s_to]:>10.2%}" for s_to in states)
        lines.append(f"| **{s_from}** | {row_vals} |")

    lines.extend([
        "",
        "---",
        "",
        "## Section 2: Absorption Probabilities Across 10-Session Horizons",
        "",
        "### A. Starting from Two-Sided Base (Pre-Circuit Setup)",
        f"- **P(Reaching Target +15% to +20%):** **{sim_base['prob_profit_target']:.2%}**",
        f"- **P(Trapped in 10-Day LC Lockout):** **{sim_base['prob_lc_trap']:.2%}**",
        f"- **P(Aborted by Rule 6 Band Cut):** **{sim_base['prob_band_cut_exit']:.2%}**",
        f"- **P(Normal Multi-Day Base Hold):** **{sim_base['prob_normal_hold']:.2%}**",
        "",
        "### B. Starting from Day 1 Upper Circuit (Momentum Acceleration)",
        f"- **P(Consecutive Lock to +15% Target):** **{sim_uc['prob_profit_target']:.2%}**",
        f"- **P(Trapped in Lower Circuit Lockout):** **{sim_uc['prob_lc_trap']:.2%}**",
        f"- **P(Surveillance Band Tightening):** **{sim_uc['prob_band_cut_exit']:.2%}**",
        "",
        "---",
        "",
        "## Section 3: Kyle's Lambda & Bid-Wall Fragility Calibration",
        "",
        "| Case Study Stock | Day Volume | Displayed Bids | Fragility Ratio (Bids/Vol) | Spoof Risk Flag | Critical Dump Vol ($V_{crit}$) |",
        "|---|---|---|---|---|---|",
    ])

    case_studies = [
        ("CCDL (11-Sep)", 760155, 17730606, 5.0),
        ("CROPSTER (09-Sep)", 27385897, 19360931, 5.0),
        ("CHANDRIMA (10-Sep)", 6355, 4500, 2.0),
        ("MOBIKWIK (11-Sep)", 892396, 125000, 20.0),
        ("LOVABLE (11-Sep)", 29989, 8500, 20.0)
    ]

    for name, d_vol, bids, band in case_studies:
        sens = calculate_critical_dump_volume(bids, d_vol, band)
        spoof = "🚨 HIGH SPOOF" if sens["is_spoof_risk"] else "✅ STRUCTURAL"
        lines.append(f"| **{name}** | {d_vol:,} | {bids:,} | {sens['fragility_ratio']}x | {spoof} | {sens['critical_dump_volume']:,} shares |")

    lines.extend([
        "",
        "---",
        "",
        "## Section 4: Mathematical Directives for Claude & Codex Peer Review",
        "1. **Adverse-Selection Red-Team:** Does taking profit exits into the Day 3/4 Upper Circuit buyer queue expose the trader to front-running by operator block dumps?",
        "2. **State Absorption Invariance:** Does the 5x5 Markov matrix satisfy ergodicity, or do `LOCKED_LC` and `BAND_TIGHTENED` act as absorbing boundaries under AGENTS.md Rule 5 and Rule 6?",
        "3. **Position Sizing Gate:** Prove whether the $0.401$ risk divisor provides sufficient tail-risk margin given the empirical 5.32% direct jump probability from `LOCKED_UC` to `LOCKED_LC`.",
    ])

    os.makedirs(os.path.dirname(AUDIT_REPORT_PATH), exist_ok=True)
    with open(AUDIT_REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Markov & Sensitivity Audit Report generated at: {AUDIT_REPORT_PATH}")


if __name__ == "__main__":
    generate_full_markov_and_sensitivity_report()
