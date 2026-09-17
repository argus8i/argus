"""
generate_monday_watchlist.py - Generates the Prospective Paper Observation Watchlist for Monday
Screens the latest 2026-09-11 Bhavcopy session for the cleanest Rule 7 setups.
Adheres strictly to AGENTS.md Rules 1, 2, 3, 5, 6, 7, 9.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from antigravity.analysis.screen_latest_setups import get_latest_rule7_candidates

REPORT_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "shared", "track1_esm", "MONDAY_PROSPECTIVE_WATCHLIST_20260914.md"))


def generate_watchlist():
    setups = get_latest_rule7_candidates()

    # Filter for realistic micro-cap momentum setups
    # Price: Rs 15 to Rs 350
    # Volume: >= 20,000 shares
    # Volume Expansion: 3.5x to 25x
    # Headroom to Upper Circuit: 5.0% to 22.0%
    filtered = [
        s for s in setups
        if 15.0 <= s["close"] <= 350.0
        and 3.5 <= s["vol_ratio"] <= 25.0
        and 5.0 <= s["dist_to_uc_pct"] <= 22.0
        and s["volume"] >= 20000
    ]

    filtered.sort(key=lambda x: x["vol_ratio"], reverse=True)
    top_candidates = filtered[:5]

    lines = [
        "# Monday Prospective Paper-Trading Watchlist (2026-09-14)",
        "",
        "**Observation Session:** Monday, 14-September-2026  ",
        "**Source Data:** Official BSE Capital Market Bhavcopy (Friday Close: 2026-09-11)  ",
        "**Strategy Rule:** AGENTS.md Rule 7 (Pre-Circuit Accumulation Breakout)  ",
        "**Execution Mode:** STRICT OBSERVATION ONLY (AGENTS.md Rule 1 — Real Capital Prohibited)  ",
        "**Paper Gate Milestone Counter:** **0 / 60 Sessions | 0 / 20 Fills**  ",
        "",
        "---",
        "",
        "## 1. Top 5 Prospective Candidates Meeting All 11 Execution Gates",
        "",
        "| Scrip Code | Symbol | Group | Close (Rs) | Traded Volume | Vol Expansion | Daily Range | Distance to UC | Max Paper Sizing (Rule 5 & 9) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    for c in top_candidates:
        sym = c["symbol"]
        code = c["scripcode"]
        grp = c["group"]
        cl = c["close"]
        vol = c["volume"]
        vr = c["vol_ratio"]
        rng = c["range_pct"]
        dist = c["dist_to_uc_pct"]
        sh = c["max_paper_shares"]
        cb = c["constrained_by"]

        lines.append(f"| **{code}** | {sym} | `{grp}` | ₹{cl:.2f} | {vol:,} sh | **{vr:.1f}x** | {rng:.2f}% | **{dist:.2f}% headroom** | **{sh:,} shares** (`{cb}`) |")

    lines.extend([
        "",
        "---",
        "",
        "## 2. Invariant Execution Gates & Rules for Each Candidate",
        "",
        "### A. Pre-Open Order Entry Rules (09:00:00 – 09:05:00 IST)",
        "- **Discrete 4-State Fill Check:** If the stock opens locked at Upper Circuit with 0 offers, **FILL PROBABILITY = 0% (`LOCKED_NO_BID`)**. Order must be rejected; locked circuits must NEVER be chased (Rule 3).",
        r"- **Two-Sided Liquidity Check:** Order entry allowed only if bid-ask spread is verified $< 1.0\%$ and both bids and offers exist.",
        "",
        "### B. Position Sizing & Downside Risk Budget (Rule 5 & Rule 9)",
        "- **Risk Allocation:** ₹5,000 outright maximum rupee loss willingness.",
        "- **10-Day Lower-Circuit Lockout Calibrated Sizing:**",
        r"  $$\text{Capital Sizing} = \frac{\text{₹5,000}}{0.401} = \text{₹12,468.83}$$",
        r"- **15% Volume Participation Cap:** Position shares cannot exceed $2 \times 0.15 \times \text{Daily Volume}$.",
        "",
        "### C. Day 3 / Day 4 Pre-Emptive Profit Exit Protocol (Rule 7)",
        r"- Target pre-emptive profit exits ($+15\%$ to $+20\%$) taken into the Upper Circuit buyer queue on Day 3 or Day 4.",
        r"- **Rule 6 Surveillance Pre-emption Override:** If the exchange revises the circuit band ($20\% \to 10\%, 10\% \to 5\%, 5\% \to 2\%$) or flags the scrip under ESM/GSM/T2T, **EXIT IMMEDIATELY** into earliest available liquidity. Do NOT wait for Day 3/4 targets.",
        "",
        "---",
        "",
        "## 3. Mandatory Paper-Trading Logging Protocol",
        "- Every prospective order and fill must be logged in `CHATGPT/observation_log.csv` and `shared/03_TRADE_LOG.md`.",
        "- Minimum milestone: **60 prospective sessions and 20 fillable paper trades** before any live capital can be reviewed.",
    ])

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Monday Prospective Watchlist generated at: {REPORT_PATH}")
    return top_candidates


if __name__ == "__main__":
    cands = generate_watchlist()
    for c in cands:
        print(f"  {c['symbol']} ({c['scripcode']}) | Rs {c['close']} | {c['vol_ratio']}x vol | {c['dist_to_uc_pct']}% to UC | Sized: {c['max_paper_shares']} sh")
