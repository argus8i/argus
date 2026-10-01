"""
scripts/auto_daily_collector.py
===============================
Automated intelligent scheduler for daily NSE data ingestion (Job 0).
Supports:
- Morning run (06:00 - 09:00 IST): verifies previous completed session EOD data,
  fetches any missing Bhavcopies/MTO, and verifies today's F&O ban list.
- Evening run (18:30 - 21:30 IST): fetches today's EOD Bhavcopy, FO Bhavcopy,
  MTO delivery positions, and tomorrow's F&O ban list.
- On-demand / Catch-up: automatically determines the latest trading sessions,
  detects gaps, and backfills missing files immediately.

Strictly adheres to:
- Rule 3: Single process, single request at a time
- Rule 4: Pacing >= 4.0s
- Rule 5: Daily cap 500 requests
- Rule 6: Stop on 403/429
- Rule 11: Idempotent resume (skips already saved SHA-verified files)
- Rule 12: Write strictly within history/
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
from typing import List, Optional

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.daily_pipeline import (
    DailyPipelineDownloader,
    calculate_next_session_date,
    KNOWN_EXCHANGE_HOLIDAYS,
    KNOWN_WEEKEND_SESSIONS,
    IST,
    MIN_INTERVAL_SECONDS,
    ARCHIVE_DAILY_CAP,
)


def get_previous_trading_day(ref_date: date) -> date:
    """Find the most recent trading session strictly before ref_date."""
    cand = ref_date - timedelta(days=1)
    for _ in range(15):
        if cand in KNOWN_WEEKEND_SESSIONS:
            return cand
        if cand.weekday() < 5 and cand not in KNOWN_EXCHANGE_HOLIDAYS:
            return cand
        cand -= timedelta(days=1)
    return ref_date - timedelta(days=1)


def is_trading_day(d: date) -> bool:
    """Check if a date is a scheduled trading day."""
    if d in KNOWN_WEEKEND_SESSIONS:
        return True
    if d.weekday() >= 5 or d in KNOWN_EXCHANGE_HOLIDAYS:
        return False
    return True


def run_collection(
    force_trade_date: Optional[str] = None,
    dry_run: bool = False,
    pause_seconds: float = MIN_INTERVAL_SECONDS,
) -> int:
    now_ist = datetime.now(IST)
    today = now_ist.date()
    current_hour = now_ist.hour
    current_minute = now_ist.minute

    print(f"[{now_ist.strftime('%Y-%m-%d %H:%M:%S IST')}] Starting Automated Daily Data Collection")

    downloader = DailyPipelineDownloader(
        pause_seconds=pause_seconds,
        daily_cap=ARCHIVE_DAILY_CAP,
    )

    sessions_to_check: List[date] = []

    if force_trade_date:
        sessions_to_check.append(date.fromisoformat(force_trade_date))
    else:
        # Determine candidate sessions based on time of day
        prev_session = get_previous_trading_day(today)
        sessions_to_check.append(prev_session)

        # If today is a trading day and time is after 18:15 IST, also check today
        if is_trading_day(today) and (current_hour > 18 or (current_hour == 18 and current_minute >= 15)):
            sessions_to_check.append(today)

    total_saved = 0
    total_skipped = 0
    total_failed = 0

    for t_date in sessions_to_check:
        next_session = calculate_next_session_date(t_date)
        print(f"\n--- Checking Session: {t_date.isoformat()} ({t_date.strftime('%A')}) | Next: {next_session.isoformat()} ---")

        items = downloader.build_daily_items(t_date, next_session)
        if dry_run:
            print(f"[DRY RUN] Would process {len(items)} items for {t_date.isoformat()}:")
            for itm in items:
                print(f"  - [{itm.dataset}] -> {itm.url}")
            continue

        stats = downloader.execute_items(items)
        saved = stats.get("SAVED", 0)
        skipped = stats.get("SKIPPED_ALREADY_SAVED", 0)
        failed = sum(v for k, v in stats.items() if "FAIL" in k or "STOP" in k)

        total_saved += saved
        total_skipped += skipped
        total_failed += failed

        print(f"Session {t_date.isoformat()} Summary: SAVED={saved}, SKIPPED={skipped}, FAILED={failed}")

    print(f"\n[{datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}] All Sessions Complete.")
    print(f"Total: SAVED={total_saved}, SKIPPED_ALREADY_SAVED={total_skipped}, FAILED={total_failed}")

    return 1 if total_failed > 0 else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Automated Daily NSE Data Ingestion")
    parser.add_argument("--trade-date", help="Override trade date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Print items without fetching")
    parser.add_argument("--pause", type=float, default=MIN_INTERVAL_SECONDS, help="Pause in seconds between requests")
    args = parser.parse_args()

    return run_collection(
        force_trade_date=args.trade_date,
        dry_run=args.dry_run,
        pause_seconds=args.pause,
    )


if __name__ == "__main__":
    sys.exit(main())
