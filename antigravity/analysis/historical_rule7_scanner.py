"""
historical_rule7_scanner.py - Identifies AGENTS.md Rule 7 Setups in track1_historical.db
Enforces 20-day volume expansion >= 3.0x, range >= 3.0%, and non-circuit close.
"""

import math
import os
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "logs", "track1_historical.db"))


@dataclass
class Rule7Candidate:
    setup_date: str
    scripcode: str
    symbol: str
    entry_price: float
    volume_ratio: float
    range_pct: float
    group: str


def scan_rule7_candidates(db_path: str = DB_PATH) -> List[Rule7Candidate]:
    if not os.path.exists(db_path):
        return []

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT scripcode FROM daily_quotes WHERE security_group IN ('T', 'XT', 'B');")
    scrips = [r[0] for r in cur.fetchall()]

    candidates = []
    for code in scrips:
        cur.execute("""
        SELECT trade_date, symbol, open_price, high_price, low_price, close_price, volume, upper_circuit, lower_circuit, security_group
        FROM daily_quotes
        WHERE scripcode = ?
        ORDER BY trade_date ASC;
        """, (code,))
        rows = cur.fetchall()
        if len(rows) < 25:
            continue

        for i in range(20, len(rows)):
            d_date, sym, op, hi, lo, cl, vol, uc, lc, grp = rows[i]

            # 20-day volume baseline check
            past_vols = [r[6] for r in rows[i-20:i] if r[6] > 0]
            if len(past_vols) < 15:
                continue
            avg_20d = sum(past_vols) / len(past_vols)
            if avg_20d <= 0:
                continue

            vol_ratio = vol / avg_20d
            if vol_ratio < 3.0:
                continue

            if lo <= 0:
                continue
            range_pct = ((hi - lo) / lo) * 100
            if range_pct < 3.0:
                continue

            # Anti-chasing Rule 3: Must be below Upper Circuit
            if uc and (uc - cl) < 0.05 * cl:
                continue

            candidates.append(Rule7Candidate(
                setup_date=d_date,
                scripcode=code,
                symbol=sym,
                entry_price=cl,
                volume_ratio=round(vol_ratio, 2),
                range_pct=round(range_pct, 2),
                group=grp
            ))

    conn.close()
    return candidates


if __name__ == "__main__":
    cands = scan_rule7_candidates()
    print(f"Total Rule 7 Candidates Found: {len(cands)}")
    for c in cands[:10]:
        print(f"[{c.setup_date}] {c.symbol:<12} (BSE: {c.scripcode}) | Price: Rs {c.entry_price} | Vol Ratio: {c.volume_ratio}x | Range: {c.range_pct}%")
