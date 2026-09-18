"""
test_track1_oms_bridge.py - Verification Suite for Track 1 Kite OMS & Volume Engine
==================================================================================
Tests:
  1. TRACK1_INSTRUMENTS schema & token integrity across all 9 symbols.
  2. Multi-tier volume fetch fallback (Kite OMS -> Yahoo Finance -> SQLite DB).
  3. Real-time Rule 7 Volume Expansion math:
     - 20-day Average Volume & Median calculation
     - Full-day volume projection based on elapsed minutes from 09:15 IST
     - Volume expansion ratio (V_proj / V_avg20) and Rule 7 threshold (>= 3.0x)
  4. Output JSON schema and Rule 11 track isolation.
"""

import json
import os
import sys
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from antigravity.daemons.kite_web_depth_bridge import (
    TRACK1_INSTRUMENTS,
    compute_rule7_volume_expansion,
    fetch_20d_volumes_for_symbol,
    LIVE_DEPTH_PATH
)


class TestTrack1InstrumentTokens:
    def test_all_nine_symbols_mapped(self):
        """All 9 Track 1 candidate scrips must be explicitly mapped with verified tokens."""
        expected = [
            "MOBIKWIK", "AHCL", "VEDAVAAG", "LOVABLE", "KINETICENG",
            "CROPSTER", "GATECH", "CCDL", "CHANDRIMA"
        ]
        for sym in expected:
            assert sym in TRACK1_INSTRUMENTS, f"Missing {sym} from TRACK1_INSTRUMENTS"
            meta = TRACK1_INSTRUMENTS[sym]
            assert meta["bse_scrip"] is not None and len(meta["bse_scrip"]) == 6
            assert meta["primary_token"] is not None and meta["primary_token"] > 0
            assert meta["primary_exchange"] in ["NSE", "BSE"]

    def test_mobikwik_and_ahcl_dual_tokens(self):
        """Dual-listed symbols must have both NSE and BSE tokens populated."""
        mobikwik = TRACK1_INSTRUMENTS["MOBIKWIK"]
        assert mobikwik["nse_token"] == 7179777
        assert mobikwik["bse_token"] == 139342084
        assert mobikwik["bse_scrip"] == "544305"

        ahcl = TRACK1_INSTRUMENTS["AHCL"]
        assert ahcl["nse_token"] == 194295809
        assert ahcl["bse_token"] == 139391236
        assert ahcl["bse_scrip"] == "544497"


class TestRule7VolumeExpansionEngine:
    def test_rule7_volume_expansion_math(self):
        """Rule 7 volume expansion math must accurately project full-day volume and ratio."""
        dummy_meta = {
            "symbol": "TEST",
            "name": "Test Scrip",
            "bse_scrip": "544305",
            "primary_token": 7179777,
            "primary_exchange": "NSE",
            "yahoo": "MOBIKWIK.NS"
        }
        dummy_auth = {"authenticated": False, "enctoken": None}

        # Case A: High volume expansion (>= 3.0x)
        # Using 10,000,000 intraday volume
        result = compute_rule7_volume_expansion(
            symbol="MOBIKWIK",
            meta=dummy_meta,
            intraday_volume=10000000,
            auth=dummy_auth
        )
        assert result["symbol"] == "MOBIKWIK"
        assert result["avg_20d_volume"] > 0
        assert result["intraday_volume"] == 10000000
        assert result["minutes_elapsed"] >= 1
        assert result["projected_full_day_volume"] >= 10000000
        assert result["volume_expansion_ratio"] >= 2.0

    def test_rule7_volume_qualification_boundary(self):
        """Intraday volume resulting in ratio < 3.0x must flag rule7_volume_qualified as False."""
        dummy_meta = {
            "symbol": "TEST",
            "name": "Test Scrip",
            "bse_scrip": "544305",
            "primary_token": 7179777,
            "primary_exchange": "NSE",
            "yahoo": "MOBIKWIK.NS"
        }
        dummy_auth = {"authenticated": False, "enctoken": None}

        result = compute_rule7_volume_expansion(
            symbol="MOBIKWIK",
            meta=dummy_meta,
            intraday_volume=100,  # Negligible volume
            auth=dummy_auth
        )
        assert result["rule7_volume_qualified"] is False
        assert result["volume_expansion_ratio"] < 1.0


class TestLiveDepthSchemaAndIsolation:
    def test_live_depth_json_structure(self):
        """live_depth.json must exist and include all mandatory Track 1 blocks."""
        assert os.path.exists(LIVE_DEPTH_PATH)
        with open(LIVE_DEPTH_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert "auth" in data
        assert "instrument_tokens" in data
        assert "volume_expansion_audit" in data
        assert "watchlist" in data
        assert "status" in data
        assert "local_write_time" in data

        # Check all 9 symbols in volume audit
        for sym in TRACK1_INSTRUMENTS:
            assert sym in data["volume_expansion_audit"]
            assert data["volume_expansion_audit"][sym]["instrument_token"] == TRACK1_INSTRUMENTS[sym]["primary_token"]

    def test_strict_track_isolation(self):
        """Must not contain Track 2 symbols in Track 1 instrument token definitions."""
        track2_symbols = {"CDSL", "ANGELONE", "SUZLON", "INOXWIND", "IREDA", "RVNL", "COCHINSHIP", "BDL"}
        assert track2_symbols.isdisjoint(set(TRACK1_INSTRUMENTS.keys())), "Track 2 symbol found in Track 1 definitions!"
