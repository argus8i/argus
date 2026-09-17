"""
bulk_bhavcopy_harvester.py - Automated Historical BSE Bhavcopy Ingestion Engine
Downloads, decompresses, and ingests 120 trading sessions into SQLite.
Strictly adheres to AGENTS.md Rule 1 (Observation Only) and Rule 2 (Rs 10 floor).
"""

import csv
import io
import math
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from antigravity.models.circuit_rules import calculate_bse_circuit_bands

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "logs", "track1_historical.db"))
RAW_CACHE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "logs", "bhavcopy_raw"))

BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.bseindia.com/",
    "Connection": "keep-alive"
}


def get_http_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(total=4, backoff_factor=2.0, status_forcelist=[500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def init_database(db_path: str = DB_PATH):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode = WAL;")
    cur.execute("PRAGMA synchronous = NORMAL;")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS daily_quotes (
        trade_date TEXT NOT NULL,
        scripcode TEXT NOT NULL,
        symbol TEXT NOT NULL,
        security_group TEXT NOT NULL,
        open_price REAL,
        high_price REAL,
        low_price REAL,
        close_price REAL,
        prev_close REAL,
        upper_circuit REAL,
        lower_circuit REAL,
        band_pct REAL,
        volume INTEGER,
        turnover REAL,
        trade_count INTEGER,
        PRIMARY KEY (scripcode, trade_date)
    );
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_quotes_date ON daily_quotes(trade_date);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_quotes_symbol ON daily_quotes(symbol, trade_date);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_quotes_group ON daily_quotes(security_group);")
    conn.commit()
    conn.close()


def get_trading_days(num_days: int = 120) -> List[str]:
    days = []
    curr = datetime.now()
    if curr.weekday() == 5:
        curr -= timedelta(days=1)
    elif curr.weekday() == 6:
        curr -= timedelta(days=2)

    while len(days) < num_days:
        if curr.weekday() < 5:
            days.append(curr.strftime("%Y%m%d"))
        curr -= timedelta(days=1)
    return sorted(days)


def harvest_all(target_days: int = 120, max_concurrent_days: Optional[int] = None) -> int:
    init_database()
    os.makedirs(RAW_CACHE_DIR, exist_ok=True)
    session = get_http_session()
    trading_days = get_trading_days(target_days)
    if max_concurrent_days:
        trading_days = trading_days[-max_concurrent_days:]

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    print(f"=== STARTING BULK BHAVCOPY HARVEST: {len(trading_days)} SESSIONS ===")
    records_inserted = 0

    for d in trading_days:
        date_str = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        cur.execute("SELECT COUNT(*) FROM daily_quotes WHERE trade_date = ?", (date_str,))
        count = cur.fetchone()[0]
        if count > 0:
            print(f"[{date_str}] Already cached ({count} records). Skipping.")
            records_inserted += count
            continue

        url = f"https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{d}_F_0000.CSV"
        try:
            resp = session.get(url, headers=BSE_HEADERS, timeout=15)
            if resp.status_code != 200:
                continue

            lines = resp.text.splitlines()
            reader = csv.DictReader(lines)
            batch = []

            for row in reader:
                cleaned = {k.strip(): v.strip() for k, v in row.items() if k}
                s_group = cleaned.get("SctySrs", "").strip()
                if s_group not in ["T", "XT", "B"]:
                    continue

                try:
                    close_p = float(cleaned.get("ClsPric", 0))
                    prev_p = float(cleaned.get("PrvsClsgPric", 0))
                    open_p = float(cleaned.get("OpnPric", close_p))
                    high_p = float(cleaned.get("HghPric", close_p))
                    low_p = float(cleaned.get("LwPric", close_p))
                    vol = int(float(cleaned.get("TtlTradgVol", 0)))
                    to_rs = float(cleaned.get("TtlTrfVal", 0))
                    trades = int(float(cleaned.get("TtlNbOfTxsExctd", 0)))
                except (ValueError, TypeError):
                    continue

                if close_p < 10.00:  # AGENTS.md Rule 2 Absolute Price Floor
                    continue

                band_pct = 5.0 if s_group in ["T", "XT"] else 20.0
                uc, lc = calculate_bse_circuit_bands(prev_p, band_pct) if prev_p > 0 else (None, None)

                batch.append((
                    date_str,
                    cleaned.get("FinInstrmId") or cleaned.get("SC_CODE"),
                    cleaned.get("FinInstrmNm", "UNKNOWN"),
                    s_group,
                    open_p, high_p, low_p, close_p, prev_p,
                    uc, lc, band_pct,
                    vol, to_rs, trades
                ))

            if batch:
                cur.executemany("""
                INSERT OR REPLACE INTO daily_quotes (
                    trade_date, scripcode, symbol, security_group,
                    open_price, high_price, low_price, close_price, prev_close,
                    upper_circuit, lower_circuit, band_pct,
                    volume, turnover, trade_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, batch)
                conn.commit()
                records_inserted += len(batch)
                print(f"[{date_str}] Ingested {len(batch)} Track 1 scrips.")
            time.sleep(0.3)

        except Exception as e:
            print(f"Failed on {date_str}: {e}")

    conn.close()
    print(f"\nBULK HARVEST COMPLETE: {records_inserted} total records in {DB_PATH}.")
    return records_inserted


if __name__ == "__main__":
    days_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    harvest_all(days_arg)
