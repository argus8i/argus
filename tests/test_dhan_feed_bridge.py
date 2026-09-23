"""
test_dhan_feed_bridge.py - Verification & Schema Tests for DhanHQ Feed Bridge
=============================================================================
Part of Project Swing Trades (ARGUS 8i // BEACON Track 2).
"""

import json
from datetime import datetime, timezone
from pathlib import Path
import pytest

from antigravity.daemons.dhan_feed_bridge import (
    DhanFeedBridge,
    DhanScripMaster,
    load_dhan_config,
    DEFAULT_TRACK2_SYMBOLS,
)
from antigravity.daemons.track2_kite_bridge import validate_extracted_payload


def test_load_dhan_config_rejects_placeholder(tmp_path):
    conf_file = tmp_path / "dhan_config.json"
    conf_file.write_text(json.dumps({
        "client_id": "YOUR_DHAN_CLIENT_ID",
        "access_token": "YOUR_DHAN_ACCESS_TOKEN",
    }))
    cfg = load_dhan_config(conf_file)
    assert cfg is None, "Placeholder credentials must be rejected fail-closed"


def test_load_dhan_config_accepts_valid_tokens(tmp_path):
    conf_file = tmp_path / "dhan_config.json"
    conf_file.write_text(json.dumps({
        "client_id": "1100123456",
        "access_token": "eyJhbGciOiJIUzI1NiIsIn...",
    }))
    cfg = load_dhan_config(conf_file)
    assert cfg is not None
    assert cfg["client_id"] == "1100123456"
    assert cfg["paper_trading_only"] is True


def test_scrip_master_resolves_all_track2_symbols():
    master = DhanScripMaster()
    master.load_index()
    for sym in DEFAULT_TRACK2_SYMBOLS:
        res = master.resolve(sym)
        assert res is not None, f"Failed to resolve Dhan security ID for {sym}"
        seg, sec_id = res
        assert seg == 1, f"Expected NSE segment 1 for equity {sym}"
        assert int(sec_id) > 0, f"Expected positive numeric ID for {sym}"

    # Also check NIFTY 50
    nifty_res = master.resolve("NIFTY 50")
    assert nifty_res is not None
    assert nifty_res[0] == 0, "NIFTY 50 should resolve to segment 0 (IDX)"
    assert nifty_res[1] == "13", "NIFTY 50 should resolve to ID 13"


def test_dhan_feed_bridge_simulated_tick_and_schema(tmp_path):
    dummy_config = {
        "client_id": "1100123456",
        "access_token": "valid_token_string",
    }
    bridge = DhanFeedBridge(dummy_config, output_dir=tmp_path, test_mode=True)

    # Simulate Full Packet tick for SUZLON (ID 12018)
    simulated_packet = {
        "type": "Full Data",
        "exchange_segment": 1,
        "security_id": 12018,
        "LTP": "42.25",
        "LTQ": 100,
        "LTT": "11:30:00",
        "avg_price": "42.10",
        "volume": 2500000,
        "total_sell_quantity": 100000,
        "total_buy_quantity": 150000,
        "open": "42.00",
        "close": "41.50",
        "high": "42.50",
        "low": "41.80",
        "depth": [
            {
                "bid_quantity": 500,
                "ask_quantity": 600,
                "bid_orders": 3,
                "ask_orders": 4,
                "bid_price": "42.20",
                "ask_price": "42.25",
            }
        ],
    }

    bridge.handle_message(None, simulated_packet)

    assert "SUZLON" in bridge.quote_cache
    assert bridge.quote_cache["SUZLON"]["ltp"] == 42.25
    assert bridge.quote_cache["SUZLON"]["volume"] == 2500000
    assert "SUZLON" in bridge.depth_cache

    # Trigger write_live_depth
    bridge.write_live_depth()
    output_path = tmp_path / "dhan_live_depth_test.json"
    assert output_path.is_file()

    payload = json.loads(output_path.read_text())
    assert payload["data_valid"] is True
    assert payload["status"] == "LIVE_STREAMING"
    assert "SUZLON" in payload["track2_matches"]

    # Validate against existing validator
    assert validate_extracted_payload(payload) is True

    # Validate 15-minute bar generation and writing
    assert "SUZLON" in bridge.intraday_bars
    assert len(bridge.intraday_bars["SUZLON"]) == 1
    suzlon_bar = bridge.intraday_bars["SUZLON"][0]
    assert suzlon_bar["open"] == 42.25
    assert suzlon_bar["high"] == 42.25
    assert suzlon_bar["close"] == 42.25

    bridge.write_live_candles()
    candles_path = tmp_path / "dhan_live_candles_test.json"
    assert candles_path.is_file()

    c_payload = json.loads(candles_path.read_text())
    assert c_payload["data_valid"] is True
    assert c_payload["interval"] == "15minute"
    assert "SUZLON" in c_payload["symbols"]
    assert len(c_payload["symbols"]["SUZLON"]["bars"]) == 1


def test_dhan_feed_bridge_stale_watchdog_freeze(tmp_path):
    """
    Codex Guarantee G2 & Claude Condition 4:
    Watchdog detects half-open socket where tick delta > 5.0s,
    freezing feed to FEED_STALE_FREEZE and marking data_valid = False.
    """
    from datetime import timedelta
    dummy_config = {
        "client_id": "1100123456",
        "access_token": "valid_token_string",
    }
    bridge = DhanFeedBridge(dummy_config, output_dir=tmp_path, test_mode=True)
    bridge.is_connected = True

    simulated_packet = {
        "type": "Full Data",
        "exchange_segment": 1,
        "security_id": 12018,
        "LTP": "42.25",
        "volume": 2500000,
        "open": "42.00",
        "close": "41.50",
        "high": "42.50",
        "low": "41.80",
    }
    bridge.handle_message(None, simulated_packet)

    # Initial write: ticks are fresh
    bridge.write_live_depth()
    output_path = tmp_path / "dhan_live_depth_test.json"
    payload = json.loads(output_path.read_text())
    assert payload["status"] == "LIVE_STREAMING"
    assert payload["data_valid"] is True

    # Simulate 15.0s stall (half-open socket exceeding 12.0s ceiling)
    bridge.last_tick_time = datetime.now(timezone.utc) - timedelta(seconds=15.0)

    bridge.write_live_depth()
    stale_payload = json.loads(output_path.read_text())
    assert stale_payload["status"] == "FEED_STALE_FREEZE"
    assert stale_payload["data_valid"] is False
    assert stale_payload["stats"]["is_stale"] is True
    assert stale_payload["stats"]["tick_delta_sec"] >= 15.0

    # Test heartbeat telemetry
    bridge.write_heartbeat()
    assert bridge.heartbeat_path.is_file()
    hb_payload = json.loads(bridge.heartbeat_path.read_text())
    assert hb_payload["status"] == "FEED_STALE_FREEZE"
    assert hb_payload["data_valid"] is False
