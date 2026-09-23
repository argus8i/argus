"""
dhan_feed_bridge.py - Headless, High-Speed DhanHQ WebSocket v2 Feed Bridge
==========================================================================
Part of Project Swing Trades (ARGUS 8i // BEACON Track 2).

Replaces visual Chrome DevTools (CDP) scraping with an institutional-grade,
sub-millisecond binary WebSocket stream using DhanHQ API v2.

Key Features:
  - 100% Headless: No Google Chrome instances, no DevTools ports, no DOM parsing.
  - Sub-10ms Latency: Direct binary tick ingestion (LTP, 5-level DOM depth, OHLCV).
  - Rule 1 Enforcement: Observation & Market Data ONLY. Zero order routing.
  - Seamless Drop-in: Produces exact schemas for live_depth_track2.json and
    live_candles_track2.json, keeping VIGIL, NIGHTWATCH, and Paper Desk green.
  - Fail-Closed Security: Credentials loaded from gitignored dhan_config.json.
    Never logs or serializes tokens or passwords.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from antigravity.models.session_manifest import IST

# Constants & Paths
CONFIG_PATH = REPO_ROOT / "antigravity" / "config" / "dhan_config.json"
SHARED_TRACK2_DIR = REPO_ROOT / "shared" / "track2_liquid"
SCRIP_MASTER_PATH = SHARED_TRACK2_DIR / "dhan_scrip_master.csv"
SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
LIVE_DEPTH_PATH = SHARED_TRACK2_DIR / "live_depth_track2.json"
LIVE_CANDLES_PATH = SHARED_TRACK2_DIR / "live_candles_track2.json"
HISTORICAL_CANDLES_PATH = SHARED_TRACK2_DIR / "historical_candles_track2.json"
HEARTBEAT_PATH = SHARED_TRACK2_DIR / "dhan_feed_heartbeat.json"

DEFAULT_TRACK2_SYMBOLS = [
    "CDSL", "ANGELONE", "SUZLON", "INOXWIND", "IREDA", "RVNL", "COCHINSHIP", "BDL"
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [DhanBridge] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("DhanFeedBridge")


def load_dhan_config(config_path: Path = CONFIG_PATH) -> Optional[Dict[str, Any]]:
    """Loads and validates Dhan credentials from private config file."""
    if not config_path.is_file():
        logger.warning(f"Config file not found at {config_path}")
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        client_id = str(data.get("client_id", "")).strip()
        access_token = str(data.get("access_token", "")).strip()

        # Check for placeholder values
        if not client_id or "YOUR_DHAN" in client_id:
            return None
        if not access_token or "YOUR_DHAN" in access_token:
            return None

        # Rule 1 Paper Trading Lock
        data["paper_trading_only"] = True
        return data
    except Exception as e:
        logger.error(f"Error reading {config_path}: {e}")
        return None


class DhanScripMaster:
    """Manages downloading, local caching, and fast resolution of Dhan security IDs."""

    def __init__(self, master_path: Path = SCRIP_MASTER_PATH):
        self.master_path = master_path
        self.sym_to_id: Dict[str, str] = {}
        self.id_to_sym: Dict[str, str] = {}
        self.sym_to_seg: Dict[str, int] = {}  # 0=IDX, 1=NSE_EQ

    def ensure_cached(self, max_age_days: int = 2) -> None:
        """Downloads the compact scrip master CSV if missing or stale."""
        self.master_path.parent.mkdir(parents=True, exist_ok=True)
        need_download = False

        if not self.master_path.is_file():
            need_download = True
        else:
            age = time.time() - self.master_path.stat().st_mtime
            if age > max_age_days * 86400:
                need_download = True

        if need_download:
            logger.info(f"Downloading Dhan Scrip Master from {SCRIP_MASTER_URL}...")
            try:
                resp = requests.get(SCRIP_MASTER_URL, stream=True, timeout=30)
                if resp.status_code == 200:
                    tmp_path = self.master_path.with_suffix(".tmp")
                    with open(tmp_path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=1024 * 1024):
                            f.write(chunk)
                    tmp_path.replace(self.master_path)
                    logger.info(f"Scrip Master cached ({self.master_path.stat().st_size} bytes)")
                else:
                    logger.warning(f"Download failed with HTTP {resp.status_code}. Using existing cache if any.")
            except Exception as exc:
                logger.error(f"Failed to download scrip master: {exc}")

    def load_index(self) -> None:
        """Parses the CSV into high-speed in-memory lookup dicts."""
        self.ensure_cached()
        if not self.master_path.is_file():
            logger.error("Scrip master file is unavailable.")
            return

        t0 = time.time()
        count = 0
        with open(self.master_path, "r", encoding="utf-8", errors="ignore") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header:
                return

            try:
                exch_idx = header.index("SEM_EXM_EXCH_ID")
                seg_idx = header.index("SEM_SEGMENT")
                sec_id_idx = header.index("SEM_SMST_SECURITY_ID")
                sym_idx = header.index("SEM_TRADING_SYMBOL")
                series_idx = header.index("SEM_SERIES")
            except ValueError as e:
                logger.error(f"Scrip master header mismatch: {e}")
                return

            for row in reader:
                if len(row) <= max(exch_idx, seg_idx, sec_id_idx, sym_idx, series_idx):
                    continue

                exch = row[exch_idx]
                seg = row[seg_idx]
                sec_id = row[sec_id_idx]
                sym = row[sym_idx]
                series = row[series_idx]

                # Map NSE Equities
                if exch == "NSE" and seg == "E" and series == "EQ":
                    self.sym_to_id[sym] = sec_id
                    self.id_to_sym[sec_id] = sym
                    self.sym_to_seg[sym] = 1  # MarketFeed.NSE
                    count += 1
                # Map NIFTY 50 Benchmark Index
                elif seg == "I" and sym in ("NIFTY", "Nifty 50"):
                    self.sym_to_id["NIFTY 50"] = sec_id
                    self.sym_to_id["NIFTY"] = sec_id
                    self.id_to_sym[sec_id] = "NIFTY 50"
                    self.sym_to_seg["NIFTY 50"] = 0  # MarketFeed.IDX
                    self.sym_to_seg["NIFTY"] = 0
                    count += 1

        t1 = time.time()
        logger.info(f"Indexed {count} symbols from Scrip Master in {t1 - t0:.2f}s")

    def resolve(self, symbol: str) -> Optional[Tuple[int, str]]:
        """Returns (exchange_segment, security_id) for a symbol."""
        sec_id = self.sym_to_id.get(symbol)
        if not sec_id:
            return None
        seg = self.sym_to_seg.get(symbol, 1)
        return seg, sec_id


class DhanFeedBridge:
    """
    Headless Market Data Feed Bridge for Track 2.
    Connects to DhanHQ WebSocket v2 and maintains live JSON feeds.
    """

    def __init__(
        self,
        config: Dict[str, Any],
        symbols: Optional[List[str]] = None,
        output_dir: Path = SHARED_TRACK2_DIR,
        test_mode: bool = False,
    ):
        self.config = config
        self.client_id = str(config["client_id"]).strip()
        self.access_token = str(config["access_token"]).strip()
        self.output_dir = output_dir
        self.test_mode = test_mode
        self.stale_timeout_sec = float(config.get("stale_timeout_sec", 12.0))  # 12.0s prevents false-freeze during midday lull (Codex Audit)

        self.live_depth_path = output_dir / ("dhan_live_depth_test.json" if test_mode else "live_depth_track2.json")
        self.live_candles_path = output_dir / ("dhan_live_candles_test.json" if test_mode else "live_candles_track2.json")
        self.heartbeat_path = output_dir / "dhan_feed_heartbeat.json"

        # Symbol & Scrip mapping
        self.symbols = symbols or list(DEFAULT_TRACK2_SYMBOLS)
        if "NIFTY 50" not in self.symbols:
            self.symbols.append("NIFTY 50")

        self.scrip_master = DhanScripMaster()
        self.scrip_master.load_index()

        # Telemetry & State
        self.quote_cache: Dict[str, Dict[str, Any]] = {}
        self.depth_cache: Dict[str, Any] = {}
        self.intraday_bars: Dict[str, List[Dict[str, Any]]] = {}
        self.ticks_count = 0
        self.last_tick_time: Optional[datetime] = None
        self.start_time = datetime.now(timezone.utc)
        self.is_connected = False
        self._running = False
        self._dhan_context = None
        self._feed = None

        # Seed existing session bars if available
        self._seed_existing_bars()

    def _seed_existing_bars(self) -> None:
        """Seeds intraday bars from live_candles_track2.json if it belongs to today's session."""
        if not self.live_candles_path.is_file():
            return
        try:
            today_str = datetime.now(IST).strftime("%Y-%m-%d")
            data = json.loads(self.live_candles_path.read_text(encoding="utf-8"))
            if data.get("session_date") == today_str and isinstance(data.get("symbols"), dict):
                for sym, rec in data["symbols"].items():
                    if isinstance(rec, dict) and isinstance(rec.get("bars"), list):
                        self.intraday_bars[sym] = list(rec["bars"])
                logger.info(f"Seeded intraday bars for {len(self.intraday_bars)} symbols from existing session file.")
        except Exception as exc:
            logger.warning(f"Could not seed existing bars: {exc}")

    def build_subscription_list(self) -> List[Tuple[int, str, int]]:
        """
        Builds DhanHQ v2 subscription tuples: (ExchangeSegment, SecurityId, RequestCode).
        RequestCode 21 = Full Packet (Quotes + 5-Depth Market Depth).
        """
        from dhanhq import MarketFeed

        tuples: List[Tuple[int, str, int]] = []
        for sym in self.symbols:
            res = self.scrip_master.resolve(sym)
            if res:
                seg, sec_id = res
                # For index, quote mode is sufficient (code 17); for equities, full depth (code 21)
                req_code = MarketFeed.Quote if seg == MarketFeed.IDX else MarketFeed.Full
                tuples.append((seg, str(sec_id), req_code))
                logger.info(f"Subscribing {sym} -> Seg={seg}, ID={sec_id}, Mode={'Full' if req_code==21 else 'Quote'}")
            else:
                logger.warning(f"Could not resolve Dhan security ID for {sym}")
        return tuples

    def handle_message(self, feed_instance: Any, data: Dict[str, Any]) -> None:
        """Callback invoked when a decoded tick packet is received from DhanHQ."""
        if not isinstance(data, dict):
            return

        self.ticks_count += 1
        now_dt = datetime.now(timezone.utc)
        self.last_tick_time = now_dt

        sec_id = str(data.get("security_id", ""))
        sym = self.scrip_master.id_to_sym.get(sec_id)
        if not sym:
            return

        try:
            ltp = float(data.get("LTP", 0.0))
        except (ValueError, TypeError):
            ltp = 0.0

        if ltp <= 0:
            return

        open_val = float(data.get("open", 0.0) or 0.0)
        high_val = float(data.get("high", 0.0) or 0.0)
        low_val = float(data.get("low", 0.0) or 0.0)
        close_val = float(data.get("close", 0.0) or 0.0)
        volume = int(data.get("volume", 0) or 0)

        # Calculate daily change
        prev_close = close_val if close_val > 0 else open_val
        if prev_close > 0:
            change_abs = round(ltp - prev_close, 2)
            change_pct = f"{round((change_abs / prev_close) * 100, 2)}%"
        else:
            change_abs = 0.0
            change_pct = "0.00%"

        # Update Quote Cache
        self.quote_cache[sym] = {
            "symbol": sym,
            "exchange": "INDEX" if sym in ("NIFTY 50", "NIFTY") else "NSE",
            "ltp": ltp,
            "open": open_val,
            "high": high_val,
            "low": low_val,
            "close": close_val,
            "volume": volume,
            "change_pct": change_pct,
            "change_abs": change_abs,
            "total_buy_quantity": int(data.get("total_buy_quantity", 0) or 0),
            "total_sell_quantity": int(data.get("total_sell_quantity", 0) or 0),
            "last_trade_time": data.get("LTT"),
        }

        # Update Depth Cache if full packet
        depth_raw = data.get("depth")
        if depth_raw and isinstance(depth_raw, list):
            bids = []
            asks = []
            for item in depth_raw:
                bids.append({
                    "price": float(item.get("bid_price", 0.0)),
                    "quantity": int(item.get("bid_quantity", 0)),
                    "orders": int(item.get("bid_orders", 0)),
                })
                asks.append({
                    "price": float(item.get("ask_price", 0.0)),
                    "quantity": int(item.get("ask_quantity", 0)),
                    "orders": int(item.get("ask_orders", 0)),
                })
            self.depth_cache[sym] = {
                "bids": bids,
                "offers": asks,
                "total_buy": int(data.get("total_buy_quantity", 0) or 0),
                "total_sell": int(data.get("total_sell_quantity", 0) or 0),
                "timestamp": now_dt.isoformat(),
            }

        # Update 15-minute bar in-memory
        now_ist = datetime.now(IST)
        minute_bucket = (now_ist.minute // 15) * 15
        bucket_dt = now_ist.replace(minute=minute_bucket, second=0, microsecond=0)
        bucket_iso = bucket_dt.isoformat()

        if sym not in self.intraday_bars:
            self.intraday_bars[sym] = []

        sym_bars = self.intraday_bars[sym]
        if not sym_bars or sym_bars[-1]["timestamp"] != bucket_iso:
            sym_bars.append({
                "timestamp": bucket_iso,
                "open": ltp,
                "high": ltp,
                "low": ltp,
                "close": ltp,
                "volume": volume,
            })
        else:
            current_bar = sym_bars[-1]
            current_bar["high"] = max(float(current_bar["high"]), ltp)
            current_bar["low"] = min(float(current_bar["low"]), ltp)
            current_bar["close"] = ltp
            if volume > 0:
                current_bar["volume"] = volume

    def write_live_candles(self) -> None:
        """Atomically formats and writes shared/track2_liquid/live_candles_track2.json."""
        if not self.intraday_bars:
            return

        now_ist = datetime.now(IST)
        today_str = now_ist.strftime("%Y-%m-%d")
        now_str = now_ist.strftime("%Y-%m-%d %H:%M:%S")

        symbols_payload = {}
        for sym, bars in self.intraday_bars.items():
            if bars:
                symbols_payload[sym] = {"bars": bars}

        valid = len(symbols_payload) >= 1

        payload = {
            "credential_serialized": False,
            "data_valid": valid,
            "interval": "15minute",
            "local_write_time": now_str,
            "session_date": today_str,
            "symbols": symbols_payload,
        }

        tmp_file = self.live_candles_path.with_suffix(".tmp")
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, sort_keys=True)
            tmp_file.replace(self.live_candles_path)
        except Exception as exc:
            logger.error(f"Error writing live candles: {exc}")

    def write_live_depth(self) -> None:
        """Atomically formats and writes shared/track2_liquid/live_depth_track2.json."""
        if not self.quote_cache:
            return

        now_ist = datetime.now(IST)
        now_iso = datetime.now(timezone.utc).isoformat()
        now_str = now_ist.strftime("%Y-%m-%d %H:%M:%S")

        watchlist: List[Dict[str, Any]] = []
        for sym in self.symbols:
            q = self.quote_cache.get(sym)
            if q:
                watchlist.append({
                    "symbol": sym,
                    "exchange": q["exchange"],
                    "ltp": q["ltp"],
                    "change_pct": q["change_pct"],
                    "change_abs": q["change_abs"],
                    "volume": q["volume"],
                })

        now_utc = datetime.now(timezone.utc)
        tick_delta_sec = (now_utc - self.last_tick_time).total_seconds() if self.last_tick_time else 999.0
        is_stale = tick_delta_sec > self.stale_timeout_sec

        valid_count = sum(1 for w in watchlist if w["symbol"] in DEFAULT_TRACK2_SYMBOLS and w["ltp"] > 0)
        data_valid = (valid_count >= 1) and (not is_stale)

        if is_stale and self.is_connected:
            status = "FEED_STALE_FREEZE"
        elif data_valid:
            status = "LIVE_STREAMING"
        else:
            status = "INITIALIZING"

        payload = {
            "timestamp": now_iso,
            "feed_source": "DHANHQ_WEBSOCKET_V2",
            "is_tab_hidden": False,
            "visibility_state": "visible",
            "watchlist": watchlist,
            "active_stock": None,
            "stats": {
                "tick_delta_sec": round(tick_delta_sec, 2),
                "is_stale": is_stale,
            },
            "depth": self.depth_cache,
            "local_write_time": now_str,
            "data_valid": data_valid,
            "status": status,
            "track2_matches": [s for s in DEFAULT_TRACK2_SYMBOLS if s in self.quote_cache],
        }

        tmp_file = self.live_depth_path.with_suffix(".tmp")
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, sort_keys=True)
            tmp_file.replace(self.live_depth_path)
        except Exception as exc:
            logger.error(f"Error writing live depth: {exc}")

    def write_heartbeat(self, status: str = "LIVE_STREAMING") -> None:
        """Writes daemon heartbeat and telemetry with active liveness watchdog."""
        now_dt = datetime.now(timezone.utc)
        tick_delta_sec = (now_dt - self.last_tick_time).total_seconds() if self.last_tick_time else 999.0
        is_stale = tick_delta_sec > self.stale_timeout_sec
        latency = round(tick_delta_sec * 1000, 1) if self.last_tick_time else None

        hb_status = status
        if is_stale and self.is_connected:
            hb_status = "FEED_STALE_FREEZE"

        # Mask client_id for security
        masked_id = f"{self.client_id[:2]}****{self.client_id[-2:]}" if len(self.client_id) >= 4 else "****"

        heartbeat = {
            "feed": "DHANHQ_V2",
            "status": hb_status,
            "client_id": masked_id,
            "connected": self.is_connected,
            "ticks_received": self.ticks_count,
            "last_tick_time": self.last_tick_time.isoformat() if self.last_tick_time else None,
            "tick_delta_sec": round(tick_delta_sec, 2),
            "data_valid": (self.is_connected and not is_stale and self.ticks_count > 0),
            "latency_ms": latency,
            "tracked_instruments": len(self.symbols),
            "quote_cache_size": len(self.quote_cache),
            "updated_at": now_dt.isoformat(),
        }

        tmp = self.heartbeat_path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(heartbeat, f, indent=2)
            tmp.replace(self.heartbeat_path)
        except Exception as exc:
            logger.error(f"Heartbeat write error: {exc}")

    async def streaming_worker(self) -> None:
        """Main asynchronous WebSocket worker."""
        from dhanhq import DhanContext, MarketFeed

        logger.info(f"Connecting to DhanHQ v2 WebSocket for {len(self.symbols)} instruments...")
        self._dhan_context = DhanContext(self.client_id, self.access_token)
        sub_list = self.build_subscription_list()

        def on_connect_cb(feed_inst):
            self.is_connected = True
            logger.info("[CONNECTED] DhanHQ WebSocket v2 feed established successfully.")
            self.write_heartbeat("CONNECTED")

        def on_error_cb(feed_inst, error):
            logger.error(f"[ERROR] DhanHQ feed error: {error}")
            self.is_connected = False
            self.write_heartbeat("ERROR")

        def on_close_cb(feed_inst):
            logger.warning("[DISCONNECTED] DhanHQ feed disconnected.")
            self.is_connected = False
            self.write_heartbeat("DISCONNECTED")

        self._feed = MarketFeed(
            self._dhan_context,
            sub_list,
            version="v2",
            on_connect=on_connect_cb,
            on_message=self.handle_message,
            on_error=on_error_cb,
            on_close=on_close_cb,
        )

        # Run MarketFeed in background thread or loop
        loop = asyncio.get_event_loop()
        feed_task = loop.run_in_executor(None, self._feed.run)

        # Periodic output flush loop (1 Hz)
        candle_flush_counter = 0
        try:
            while self._running:
                await asyncio.sleep(1.0)
                self.write_live_depth()
                candle_flush_counter += 1
                if candle_flush_counter >= 5:
                    self.write_live_candles()
                    candle_flush_counter = 0
                self.write_heartbeat("LIVE_STREAMING" if self.ticks_count > 0 else "CONNECTED")
        except asyncio.CancelledError:
            pass
        finally:
            if self._feed:
                self._feed.close_connection()

    def start(self) -> None:
        """Entrypoint for the bridge."""
        self._running = True
        logger.info("Starting DhanHQ Feed Bridge daemon...")
        self.write_heartbeat("STARTING")

        try:
            asyncio.run(self.streaming_worker())
        except KeyboardInterrupt:
            logger.info("DhanHQ Feed Bridge stopped by user.")
        finally:
            self._running = False
            self.write_heartbeat("STOPPED")


def run_bridge_supervisor(test_mode: bool = False) -> None:
    """Supervisor loop that monitors configuration and launches bridge when credentials exist."""
    print("=" * 70)
    print("      ARGUS 8i: TRACK 2 HEADLESS DHANHQ WEBSOCKET DATA BRIDGE")
    print("      Protocol: Binary WebSocket v2 // Sub-10ms Market Telemetry")
    print("      Rule 1 Gate: 100% Observation & Market Data ONLY (Paper Locked)")
    print("=" * 70)

    while True:
        cfg = load_dhan_config()
        if not cfg:
            now_str = datetime.now().strftime("%H:%M:%S")
            print(f"[{now_str}] [AWAITING CREDENTIALS] Valid Dhan credentials not yet set.")
            print(f"       Please edit: {CONFIG_PATH}")
            print(f"       Enter your 'client_id' and 'access_token' from https://web.dhan.co")

            # Write awaiting heartbeat
            heartbeat = {
                "feed": "DHANHQ_V2",
                "status": "AWAITING_CREDENTIALS",
                "connected": False,
                "ticks_received": 0,
                "message": f"Please set credentials in {CONFIG_PATH.name}",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            tmp = HEARTBEAT_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(heartbeat, f, indent=2)
            tmp.replace(HEARTBEAT_PATH)

            time.sleep(5)
            continue

        print(f"[{datetime.now().strftime('%H:%M:%S')}] Valid configuration detected. Launching DhanHQ Bridge...")
        bridge = DhanFeedBridge(cfg, test_mode=test_mode)
        try:
            bridge.start()
        except Exception as exc:
            logger.error(f"DhanHQ bridge crashed: {exc}. Retrying in 5 seconds...")
            time.sleep(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DhanHQ WebSocket Market Data Bridge for Track 2")
    parser.add_argument("--test", action="store_true", help="Run in test mode (writes to test output files)")
    args = parser.parse_args()

    run_bridge_supervisor(test_mode=args.test)
