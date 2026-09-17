"""
screen_latest_setups.py - Scans track1_historical.db for Active Rule 7 Breakout Candidates
Identifies prospective candidates from the latest trading session for paper observation.
Strictly adheres to AGENTS.md Rules 1, 2, 3, 6, 7, 9.
"""

import math
import os
import sqlite3
from typing import Dict, List

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "logs", "track1_historical.db"))


def get_latest_rule7_candidates(db_path: str = DB_PATH) -> List[Dict]:
    if not os.path.exists(db_path):
        return []

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT MAX(trade_date) FROM daily_quotes;")
    latest_date = cur.fetchone()[0]

    cur.execute("""
    SELECT scripcode, symbol, security_group, open_price, high_price, low_price, close_price, volume, upper_circuit, lower_circuit
    FROM daily_quotes
    WHERE trade_date = ? AND close_price >= 10.0 AND volume > 1000;
    """, (latest_date,))
    candidates = cur.fetchall()

    qualified_setups = []

    for row in candidates:
        code, sym, grp, op, hi, lo, cl, vol, uc, lc = row
        if not cl or cl < 10.0 or not lo or lo <= 0:
            continue

        cur.execute("""
        SELECT volume FROM daily_quotes
        WHERE scripcode = ? AND trade_date < ? AND volume > 0
        ORDER BY trade_date DESC LIMIT 20;
        """, (code, latest_date))
        past_vols = [r[0] for r in cur.fetchall()]
        if len(past_vols) < 15:
            continue

        avg_20d = sum(past_vols) / len(past_vols)
        if avg_20d <= 0:
            continue

        vol_ratio = vol / avg_20d
        if vol_ratio < 3.0:
            continue

        range_pct = ((hi - lo) / lo) * 100
        if range_pct < 3.0:
            continue

        # Rule 3 anti-chasing check: Price must not be locked at UC
        if uc and cl >= (uc - 0.02):
            continue

        dist_to_uc_pct = ((uc - cl) / cl) * 100 if uc and cl > 0 else 0.0

        # Rule 5 / Rule 9 Max Position Sizing
        # Max Risk Budget: Rs 5,000 outright loss
        # Rule 5 Divisor: 0.401
        capital_risk_max_shares = int((5000.0 / 0.401) // cl)
        # Rule 9 Liquidity Cap: 2 sessions * 15% of baseline 20-day volume (Claude & Codex condition)
        liquidity_max_shares = int(2.0 * 0.15 * avg_20d)
        final_shares = min(capital_risk_max_shares, liquidity_max_shares)
        constrained_by = "CAPITAL_RISK_RULE_5" if capital_risk_max_shares <= liquidity_max_shares else "LIQUIDITY_GATE_RULE_9"

        if final_shares <= 0:
            continue

        qualified_setups.append({
            "scripcode": code,
            "symbol": sym,
            "group": grp,
            "close": cl,
            "volume": vol,
            "avg_20d": int(avg_20d),
            "vol_ratio": round(vol_ratio, 2),
            "range_pct": round(range_pct, 2),
            "upper_circuit": uc,
            "dist_to_uc_pct": round(dist_to_uc_pct, 2),
            "max_paper_shares": final_shares,
            "constrained_by": constrained_by,
            "date": latest_date
        })

    conn.close()
    return qualified_setups


if __name__ == "__main__":
    setups = get_latest_rule7_candidates()
    print(f"Total Qualified Setups from Latest Session: {len(setups)}")
    print("-" * 90)
    for s in setups[:15]:
        sym = s["symbol"][:25]
        code = s["scripcode"]
        grp = s["group"]
        cl = s["close"]
        vol = s["volume"]
        vr = s["vol_ratio"]
        rng = s["range_pct"]
        uc = s["upper_circuit"]
        dist = s["dist_to_uc_pct"]
        sh = s["max_paper_shares"]
        cb = s["constrained_by"]
        print(f"[{s['date']}] {sym:<25} (BSE: {code}) Grp:{grp} | Rs {cl:<6.2f} | Vol: {vol:>8,} ({vr}x) | Rng: {rng}% | UC: {uc} ({dist}% head) | Size: {sh} sh ({cb})")
