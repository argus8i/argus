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


def test_dhan_feed_bridge_candles_stale_watchdog_freeze(tmp_path):
    """
    Regression for the candle-path staleness gap: write_live_candles() must
    invalidate data_valid when the feed has gone dark, exactly like its
    sibling write_live_depth() already does (test_dhan_feed_bridge_stale_
    watchdog_freeze above). Before this fix, a dead/frozen feed kept
    reporting data_valid=True in live_candles_track2.json forever, as long
    as the daemon process stayed alive and kept re-writing the same frozen
    in-memory bars with a fresh local_write_time.
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

    # Initial write: ticks are fresh.
    bridge.write_live_candles()
    candles_path = tmp_path / "dhan_live_candles_test.json"
    payload = json.loads(candles_path.read_text())
    assert payload["data_valid"] is True

    # Simulate the feed going dark for far longer than the 12.0s ceiling,
    # with no new ticks arriving -- the in-memory bars never change, but the
    # daemon process (and its periodic flush loop) is still alive.
    bridge.last_tick_time = datetime.now(timezone.utc) - timedelta(hours=2)

    bridge.write_live_candles()
    stale_payload = json.loads(candles_path.read_text())
    assert stale_payload["data_valid"] is False, (
        "Frozen candle bars from a dead feed must not be reported as valid "
        "just because the write loop is still running."
    )
    assert stale_payload["stats"]["is_stale"] is True
    assert stale_payload["stats"]["tick_delta_sec"] >= 3600.0


def test_dhan_feed_to_daily_paper_desk_session_valid_integration(tmp_path):
    """
    Codex Finding R07:
    Demonstrates session-valid end-to-end integration between DhanFeedBridge output
    and track2_daily_paper_desk.load_current_candles().
    Uses complete Track 2 universe (8 scrips + NIFTY50) during active market hours.
    """
    from antigravity.models.session_manifest import IST
    from antigravity.daemons import track2_daily_paper_desk as desk

    now = datetime(2026, 9, 24, 10, 0, 0, tzinfo=IST)
    now_iso = now.isoformat()
    all_syms = DEFAULT_TRACK2_SYMBOLS + ["NIFTY50"]

    bars = [
        {
            "timestamp": "2026-09-24T09:15:00+05:30",
            "open": 100.0,
            "high": 105.0,
            "low": 99.0,
            "close": 104.0,
            "volume": 10000,
        },
        {
            "timestamp": "2026-09-24T09:30:00+05:30",
            "open": 104.0,
            "high": 108.0,
            "low": 103.0,
            "close": 107.0,
            "volume": 15000,
        },
    ]

    payload = {
        "credential_serialized": False,
        "data_valid": True,
        "interval": "15minute",
        "local_write_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "session_date": "2026-09-24",
        "symbols": {
            s: {
                "bars": bars,
                "requested_at": now_iso,
                "completed_at": now_iso,
            }
            for s in all_syms
        },
    }

    candles_path = tmp_path / "live_candles_track2.json"
    candles_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    loaded = desk.load_current_candles(candles_path, now=now)
    assert loaded["data_valid"] is True
    assert set(loaded["symbols"].keys()) == set(all_syms)
    for sym in all_syms:
        rec = loaded["symbols"][sym]
        assert "bars" in rec
        assert len(rec["bars"]) == 2
        assert rec["bars"][0]["open"] == 100.0
        assert rec["bars"][1]["close"] == 107.0


def test_dhan_feed_bridge_dynamic_universe_loading(tmp_path):
    """Verifies DhanFeedBridge dynamically loads symbols from a fresh, qualified dynamic_universe.json."""
    from antigravity.models.session_manifest import IST
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    dyn_file = tmp_path / "dynamic_universe.json"
    dyn_data = {
        "session_date": today_str,
        "qualification_eligible": True,
        "universe_status": "PROSPECTIVE_QUALIFIED_BASKET",
        "symbols": ["TATAMOTORS", "RELIANCE", "INFY", "NATIONALUM"]
    }
    dyn_file.write_text(json.dumps(dyn_data), encoding="utf-8")

    cfg = {"client_id": "test", "access_token": "test"}
    bridge = DhanFeedBridge(config=cfg, symbols=None, output_dir=tmp_path, test_mode=True)

    assert "TATAMOTORS" in bridge.symbols
    assert "RELIANCE" in bridge.symbols
    assert "INFY" in bridge.symbols
    assert "NATIONALUM" in bridge.symbols
    assert "NIFTY50" in bridge.symbols


def test_dhan_feed_bridge_rejects_stale_or_unqualified_dynamic_universe(tmp_path):
    """
    Regression: a dynamic_universe.json that is stale (wrong session_date) or
    still quarantined (qualification_eligible is not True, e.g. the
    MANUAL_UNVERIFIED_BASKET status track2_dynamic_universe_scanner.
    freeze_universe() stamps under Red-Team Finding F9) must never be trusted
    for live subscription -- the bridge must fall back to the fixed default
    universe instead of silently subscribing to symbols that may since have
    been delisted, exited F&O, or entered surveillance.
    """
    cfg = {"client_id": "test", "access_token": "test"}

    # Case 1: stale session_date (yesterday), otherwise well-formed and "eligible".
    stale_dir = tmp_path / "stale"
    stale_dir.mkdir()
    (stale_dir / "dynamic_universe.json").write_text(json.dumps({
        "session_date": "2020-01-01",
        "qualification_eligible": True,
        "symbols": ["DELISTED_OR_ASM_FLAGGED_SYMBOL", "EXITED_FNO_SYMBOL"],
    }), encoding="utf-8")
    bridge_stale = DhanFeedBridge(config=cfg, symbols=None, output_dir=stale_dir, test_mode=True)
    assert "DELISTED_OR_ASM_FLAGGED_SYMBOL" not in bridge_stale.symbols
    assert set(bridge_stale.symbols) & set(DEFAULT_TRACK2_SYMBOLS)

    # Case 2: today's date, but still quarantined as MANUAL_UNVERIFIED_BASKET.
    from antigravity.models.session_manifest import IST
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    unqualified_dir = tmp_path / "unqualified"
    unqualified_dir.mkdir()
    (unqualified_dir / "dynamic_universe.json").write_text(json.dumps({
        "session_date": today_str,
        "qualification_eligible": False,
        "universe_status": "MANUAL_UNVERIFIED_BASKET",
        "symbols": ["UNVERIFIED_SYMBOL"],
    }), encoding="utf-8")
    bridge_unqualified = DhanFeedBridge(config=cfg, symbols=None, output_dir=unqualified_dir, test_mode=True)
    assert "UNVERIFIED_SYMBOL" not in bridge_unqualified.symbols
    assert set(bridge_unqualified.symbols) & set(DEFAULT_TRACK2_SYMBOLS)

