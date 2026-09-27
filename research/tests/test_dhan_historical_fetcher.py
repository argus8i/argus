"""
research/tests/test_dhan_historical_fetcher.py
==============================================
Unit tests for DhanHistoricalFetcher, ScripResolver, chunking, rate-limiting,
and CandleStore canonical JSON export.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from research.backtest.bars import CandleStore
from research.data.dhan_historical_fetcher import (
    DhanHistoricalFetcher,
    FetcherConfig,
    ScripInfo,
    ScripResolver,
    load_fno_symbols_from_snapshot,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIP_MASTER_PATH = REPO_ROOT / "shared" / "track2_liquid" / "dhan_scrip_master.csv"
SNAPSHOT_PATH = REPO_ROOT / "shared" / "track2_liquid" / "paper_surveillance" / "raw_nse_fno_2026-09-23_680a5141.json"


def test_scrip_resolver_special_indices():
    resolver = ScripResolver(SCRIP_MASTER_PATH)
    nifty = resolver.resolve("NIFTY")
    assert nifty is not None
    assert nifty.security_id == "13"
    assert nifty.exchange_segment == "IDX_I"

    vix = resolver.resolve("INDIA VIX")
    assert vix is not None
    assert vix.security_id == "21"
    assert vix.exchange_segment == "IDX_I"

    banknifty = resolver.resolve("BANKNIFTY")
    assert banknifty is not None
    assert banknifty.security_id == "25"


def test_scrip_resolver_equities():
    resolver = ScripResolver(SCRIP_MASTER_PATH)
    reliance = resolver.resolve("RELIANCE")
    assert reliance is not None
    assert reliance.security_id == "2885"
    assert reliance.exchange_segment == "NSE_EQ"
    assert reliance.instrument_type == "EQUITY"

    # With -EQ suffix
    rel_eq = resolver.resolve("RELIANCE-EQ")
    assert rel_eq is not None
    assert rel_eq.security_id == "2885"

    # Unknown stock
    unknown = resolver.resolve("NONEXISTENT_XYZ_123")
    assert unknown is None


def test_load_fno_symbols_from_snapshot():
    syms = load_fno_symbols_from_snapshot(SNAPSHOT_PATH)
    assert len(syms) == 210
    assert "RELIANCE" in syms
    assert "TCS" in syms
    assert "HDFCBANK" in syms
    assert "SUZLON" in syms


def test_fetcher_config_fails_closed_on_placeholders(tmp_path: Path):
    bad_cfg = tmp_path / "bad_dhan.json"
    bad_cfg.write_text(json.dumps({"client_id": "YOUR_DHAN_CLIENT_ID", "access_token": "valid_token"}))
    with pytest.raises(ValueError, match="contains placeholder"):
        FetcherConfig.from_json_file(bad_cfg, SCRIP_MASTER_PATH)

    bad_cfg2 = tmp_path / "bad_dhan2.json"
    bad_cfg2.write_text(json.dumps({"client_id": "12345", "access_token": "YOUR_DHAN_TOKEN"}))
    with pytest.raises(ValueError, match="contains placeholder"):
        FetcherConfig.from_json_file(bad_cfg2, SCRIP_MASTER_PATH)


class MockDhanClient:
    def __init__(self) -> None:
        self.call_count = 0

    def intraday_minute_data(self, **kwargs) -> Dict[str, Any]:
        self.call_count += 1
        # Epoch timestamps for 09:15, 09:30, 09:45 on 2026-09-22 IST
        # 2026-09-22 09:15:00 IST = 2026-09-22 03:45:00 UTC = 1790048700
        t0 = 1790048700
        return {
            "status": "success",
            "data": {
                "open": [100.0, 101.0, 102.0],
                "high": [102.0, 103.0, 104.0],
                "low": [99.5, 100.5, 101.5],
                "close": [101.0, 102.0, 103.0],
                "volume": [50000, 45000, 60000],
                "timestamp": [t0, t0 + 900, t0 + 1800],
            }
        }

    def historical_daily_data(self, **kwargs) -> Dict[str, Any]:
        return {
            "status": "success",
            "data": {
                "open": [98.0],
                "high": [105.0],
                "low": [97.5],
                "close": [103.0],
                "volume": [500000],
                "timestamp": [1790048700],
            }
        }


def test_dhan_historical_fetcher_chunking_and_export(tmp_path: Path):
    cfg = FetcherConfig(
        client_id="TEST_CLIENT",
        access_token="TEST_TOKEN",
        scrip_master_path=SCRIP_MASTER_PATH,
        cache_dir=tmp_path / "cache",
        chunk_days=30,
        rate_limit_per_sec=100.0,  # Fast for testing
    )
    mock_client = MockDhanClient()
    fetcher = DhanHistoricalFetcher(cfg, client=mock_client)

    # Request 90 days (should create 3 chunks: 0..30, 31..61, 62..90)
    start = date(2026, 6, 1)
    end = date(2026, 8, 30)

    res = fetcher.download_symbol("RELIANCE", start, end, interval=15, use_cache=False)
    assert res["symbol"] == "RELIANCE"
    assert len(res["bars"]) == 3  # Deduped identical mock bars
    assert len(res["daily_bars"]) == 1
    assert mock_client.call_count >= 3  # Chunking verified!

    # Verify cache written to disk
    cache_file = tmp_path / "cache" / "RELIANCE_15m.json"
    assert cache_file.is_file()

    # Export canonical store
    out_store = tmp_path / "test_store.json"
    fetcher.export_canonical_store({"RELIANCE": res}, out_store, start, end)
    assert out_store.is_file()
    from research.data import provenance

    exported = json.loads(out_store.read_text(encoding="utf-8"))["symbols"]["RELIANCE"]
    assert provenance.classify(exported) == provenance.DHAN      # strategy-eligible after ingest

    # Load with CandleStore to verify complete compatibility with backtester
    store = CandleStore.from_historical_json(out_store)
    assert "RELIANCE" in store.symbols
    assert len(store.sessions("RELIANCE")) >= 1
    bars = store.bars("RELIANCE", date(2026, 9, 22))
    assert len(bars) == 3
    assert bars[0].close == 101.0
    assert bars[0].symbol == "RELIANCE"
