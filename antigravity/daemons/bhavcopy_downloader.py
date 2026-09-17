"""
BSE Official Capital Market Bhavcopy Automated Downloader
Downloads daily official CSV prints at 18:00 IST from BSE India,
filters watchlist symbols, and appends to antigravity/logs/bhavcopy_history.csv.
"""

import csv
import os
import sys
from datetime import datetime, timedelta
import requests

from urllib3.util import Retry
from requests.adapters import HTTPAdapter

WATCHLIST = {
    "523105": "CROPSTER",
    "540829": "CHANDRIMA",
    "539091": "CCDL",
    "531723": "GATECH",
    "544305": "MOBIKWIK",
    "533343": "LOVABLE",
    "544497": "ANLON",
    "533056": "VEDAVAAG",
    "500240": "KINETIC"
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.bseindia.com/",
    "Connection": "keep-alive"
}

LOGS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "logs"))
HISTORY_CSV = os.path.join(LOGS_DIR, "bhavcopy_history.csv")

def get_session():
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1.5, status_forcelist=[500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session

def download_bhavcopy(target_date: str = None) -> list:
    """
    Downloads Bhavcopy for target_date (format: YYYYMMDD).
    Defaults to today, or previous trading day if weekend.
    """
    if not target_date:
        now = datetime.now()
        if now.weekday() == 5:
            now -= timedelta(days=1)
        elif now.weekday() == 6:
            now -= timedelta(days=2)
        target_date = now.strftime("%Y%m%d")

    url = f"https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{target_date}_F_0000.CSV"
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Downloading Bhavcopy from: {url}")

    session = get_session()
    try:
        resp = session.get(url, headers=HEADERS, timeout=20)
        if resp.status_code == 200:
            lines = resp.text.splitlines()
            reader = csv.DictReader(lines)
            
            matched_rows = []
            for row in reader:
                cleaned = {k.strip(): v.strip() for k, v in row.items() if k}
                code = cleaned.get("FinInstrmId") or cleaned.get("SC_CODE")
                
                if code in WATCHLIST:
                    symbol = WATCHLIST[code]
                    matched_rows.append({
                        "date": f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]}",
                        "scripcode": code,
                        "symbol": symbol,
                        "group": cleaned.get("SctySrs", ""),
                        "open": cleaned.get("OpnPric", ""),
                        "high": cleaned.get("HghPric", ""),
                        "low": cleaned.get("LwPric", ""),
                        "close": cleaned.get("ClsPric", ""),
                        "prev_close": cleaned.get("PrvsClsgPric", ""),
                        "volume": cleaned.get("TtlTradgVol", ""),
                        "turnover": cleaned.get("TtlTrfVal", ""),
                        "trades": cleaned.get("TtlNbOfTxsExctd", "")
                    })
            return matched_rows
        else:
            print(f"Bhavcopy download returned status code {resp.status_code}", file=sys.stderr)
    except Exception as e:
        print(f"Error downloading Bhavcopy: {e}", file=sys.stderr)
    return []

def append_to_history(rows: list):
    if not rows:
        print("No matching rows to save.")
        return

    os.makedirs(LOGS_DIR, exist_ok=True)
    file_exists = os.path.exists(HISTORY_CSV)

    fieldnames = ["date", "scripcode", "symbol", "group", "open", "high", "low", "close", "prev_close", "volume", "turnover", "trades"]
    
    # Read existing entries to prevent duplicates
    existing_keys = set()
    if file_exists:
        with open(HISTORY_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                existing_keys.add((r.get("date"), r.get("scripcode")))

    new_rows = [r for r in rows if (r["date"], r["scripcode"]) not in existing_keys]

    with open(HISTORY_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        for r in new_rows:
            writer.writerow(r)

    print(f"Appended {len(new_rows)} new records to {HISTORY_CSV}:")
    for r in new_rows:
        vol = int(float(r['volume'])) if r['volume'] else 0
        print(f"  {r['date']} | {r['symbol']:<10} | Group: {r['group']:<3} | Close: Rs.{r['close']:<6} | Vol: {vol:>10,} | Trades: {r['trades']}")

def main():
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    rows = download_bhavcopy(date_arg)
    append_to_history(rows)

if __name__ == "__main__":
    main()
