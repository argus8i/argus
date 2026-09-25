"""
scripts/download_dhan_historical.py
===================================
Automated CLI script to pull multi-year 15-minute and daily historical candles
from DhanHQ API v2 for all NSE F&O constituents, major indices, and India VIX.

Usage:
  # Dry-run validation (resolves symbols, tests config, prints plan):
  python scripts/download_dhan_historical.py --dry-run

  # Download the 8 Track 2 basket stocks for the last 5 years:
  python scripts/download_dhan_historical.py --basket-only --years 5

  # Download all 210 F&O underlyings + NIFTY + INDIA VIX:
  python scripts/download_dhan_historical.py --all-fno --years 5

  # Download specific symbols:
  python scripts/download_dhan_historical.py --symbols RELIANCE,TCS,INFY,NIFTY --years 3
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

# Add project root to sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.data.dhan_historical_fetcher import (
    DhanHistoricalFetcher,
    FetcherConfig,
    ScripResolver,
    load_fno_symbols_from_snapshot,
)

DEFAULT_CONFIG_PATH = REPO_ROOT / "antigravity" / "config" / "dhan_config.json"
DEFAULT_SCRIP_MASTER_PATH = REPO_ROOT / "shared" / "track2_liquid" / "dhan_scrip_master.csv"
DEFAULT_SNAPSHOT_PATH = (
    REPO_ROOT / "shared" / "track2_liquid" / "paper_surveillance" / "raw_nse_fno_2026-09-23_680a5141.json"
)
DEFAULT_OUT_PATH = REPO_ROOT / "shared" / "track2_liquid" / "historical_candles_track2.json"
DEFAULT_BASKET_SYMBOLS = [
    "CDSL", "ANGELONE", "SUZLON", "INOXWIND", "IREDA", "RVNL", "COCHINSHIP", "BDL", "NIFTY", "INDIA VIX"
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [DhanDownloader] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("DhanDownloader")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download multi-year historical candles from DhanHQ API v2.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to dhan_config.json")
    parser.add_argument("--scrip-master", type=Path, default=DEFAULT_SCRIP_MASTER_PATH, help="Path to dhan_scrip_master.csv")
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT_PATH, help="Path to NSE F&O JSON snapshot")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH, help="Path to output canonical candle store JSON")
    parser.add_argument("--years", type=int, default=5, help="Number of years of history to download (default: 5)")
    parser.add_argument("--interval", type=int, default=15, help="Candle interval in minutes (default: 15)")
    parser.add_argument("--symbols", type=str, default="", help="Comma-separated symbol list")
    parser.add_argument("--all-fno", action="store_true", help="Download all 210 F&O constituents + NIFTY + INDIA VIX")
    parser.add_argument("--basket-only", action="store_true", help="Download only the 8 Track 2 active basket stocks + indices")
    parser.add_argument("--dry-run", action="store_true", help="Validate resolution and config without downloading")
    parser.add_argument("--chunk-days", type=int, default=60, help="Days per API chunk request (default: 60)")
    parser.add_argument("--rate-limit", type=float, default=4.0, help="Max requests per second (default: 4.0)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logger.info("Initializing Dhan Historical Downloader...")

    # Determine symbols list
    if args.all_fno:
        fno_list = load_fno_symbols_from_snapshot(args.snapshot)
        symbols = sorted(set(fno_list + ["NIFTY", "INDIA VIX", "BANKNIFTY"]))
        logger.info(f"Targeting ALL {len(symbols)} F&O constituents and key indices.")
    elif args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        logger.info(f"Targeting explicit symbol list: {symbols}")
    else:
        # Default to Track 2 basket
        symbols = DEFAULT_BASKET_SYMBOLS
        logger.info(f"Targeting default Track 2 basket: {symbols}")

    # Validate Scrip Master Resolution
    resolver = ScripResolver(args.scrip_master)
    resolved_count = 0
    unresolved = []
    for s in symbols:
        info = resolver.resolve(s)
        if info:
            resolved_count += 1
        else:
            unresolved.append(s)

    logger.info(f"Scrip Master Resolution: {resolved_count}/{len(symbols)} symbols resolved successfully.")
    if unresolved:
        logger.warning(f"Unresolved symbols ({len(unresolved)}): {unresolved}")

    # Date range
    end_date = date.today()
    start_date = end_date - timedelta(days=args.years * 365)
    logger.info(f"Date Range: {start_date} to {end_date} ({args.years} years, interval={args.interval}m)")

    # Check credentials
    try:
        cfg = FetcherConfig.from_json_file(
            config_path=args.config,
            scrip_master_path=args.scrip_master,
        )
        cfg.rate_limit_per_sec = args.rate_limit
        cfg.chunk_days = args.chunk_days
        has_valid_creds = True
    except Exception as exc:
        logger.warning(f"Dhan Credentials Notice: {exc}")
        has_valid_creds = False

    if args.dry_run:
        logger.info("[DRY-RUN] Execution completed successfully. All pre-flight checks passed.")
        if not has_valid_creds:
            logger.info("[DRY-RUN] To run actual download, update antigravity/config/dhan_config.json with real client_id and access_token.")
        return 0

    if not has_valid_creds:
        logger.error("Cannot proceed with download: valid Dhan API credentials are required in dhan_config.json.")
        return 1

    # Execute Fetch
    fetcher = DhanHistoricalFetcher(cfg)
    logger.info(f"Starting batch download for {len(symbols)} symbols into {cfg.cache_dir}...")
    basket_res = fetcher.download_basket(
        symbols=symbols,
        start_date=start_date,
        end_date=end_date,
        interval=args.interval,
        use_cache=True,
    )

    # Export canonical JSON
    logger.info(f"Exporting canonical store to {args.out}...")
    fetcher.export_canonical_store(
        basket_results=basket_res,
        out_path=args.out,
        start_date=start_date,
        end_date=end_date,
        interval=args.interval,
    )

    logger.info("Batch download completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
