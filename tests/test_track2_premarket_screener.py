"""
test_track2_premarket_screener.py - Unit & Invariant Tests for Pre-Market Screener
==================================================================================
Part of Project Swing Trades (Track 2 Autonomous Discovery & Screening Engine).
"""

import json
import shutil
import tempfile
from pathlib import Path
import pytest
from antigravity.daemons.track2_premarket_screener import (
    PremarketScreener,
    resolve_sector,
    run_premarket_screener,
    RotationSummary,
)
from antigravity.models.track2_portfolio_risk_governor import COARSE_SECTOR_GROUPS
from antigravity.models.track2_dynamic_universe_scanner import DynamicUniverseScanner


@pytest.fixture
def local_tmp_dir(tmp_path):
    temp_dir = tmp_path / "screener_test_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    surv_dir = temp_dir / "surveillance"
    surv_dir.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "schema_version": "track2.surveillance.v3",
        "generated_at": "2026-09-22T08:00:00",
        "lists": {
            "asm_lt": ["SOME_ASM_SCRIP"],
            "asm_st": [],
            "gsm": [],
            "fo_ban": [],
        },
    }
    with open(surv_dir / "nse_surveillance_snapshot_2026-09-22.json", "w", encoding="utf-8") as f:
        json.dump(snapshot, f)
    return temp_dir


def test_resolve_sector_exact_and_heuristics():
    # Exact mappings
    assert resolve_sector("IREDA") == "PSU_RENEWABLE_FINANCE"
    assert COARSE_SECTOR_GROUPS[resolve_sector("IREDA")] == "FINANCIAL_SERVICES"
    assert resolve_sector("COCHINSHIP") == "DEFENSE_SHIPBUILDING"
    assert COARSE_SECTOR_GROUPS[resolve_sector("COCHINSHIP")] == "DEFENSE_MANUFACTURING"
    assert resolve_sector("CDSL") == "CAPITAL_MARKETS_FINTECH"
    assert COARSE_SECTOR_GROUPS[resolve_sector("CDSL")] == "FINANCIAL_SERVICES"
    assert resolve_sector("TCS") == "IT_SOFTWARE"
    assert resolve_sector("TATASTEEL") == "METALS_MINING"

    # Heuristics (unmapped symbols)
    assert resolve_sector("XYZBANK") == "FINANCIAL_SERVICES"
    assert resolve_sector("XYZMOTORS") == "AUTOMOBILES"
    assert resolve_sector("XYZMINING") == "METALS_MINING"
    assert resolve_sector("XYZPHARMA") == "PHARMA_HEALTHCARE"
    assert resolve_sector("XYZPOWER") == "POWER_ENERGY"
    assert resolve_sector("XYZSOFT") == "IT_SOFTWARE"


def test_load_fno_symbols():
    screener = PremarketScreener()
    symbols = screener.load_fno_symbols()
    assert isinstance(symbols, list)
    assert len(symbols) >= 100, f"Expected at least 100 F&O symbols, got {len(symbols)}"
    assert "RELIANCE" in symbols or "CDSL" in symbols or "IREDA" in symbols


def test_screener_sector_capping_and_ranking(local_tmp_dir):
    universe_file = local_tmp_dir / "dynamic_universe.json"
    rotations_file = local_tmp_dir / "rotations.json"

    screener = PremarketScreener(
        universe_path=universe_file,
        rotations_log=rotations_file,
        surveillance_dir=local_tmp_dir / "surveillance",
        as_of_date="2026-09-22",
    )

    # Run screener in dry-run mode
    summary = screener.run_screener(session_date="2026-09-22", dry_run=True)
    assert isinstance(summary, RotationSummary)
    assert summary.total_fno >= 100
    assert len(summary.top_8) == 8

    # Verify sector cluster invariant: max 2 per sector in top 8
    coarse_sector_counts = {}
    for sym in summary.top_8:
        raw_sec = resolve_sector(sym)
        coarse_sec = COARSE_SECTOR_GROUPS.get(raw_sec, raw_sec)
        coarse_sector_counts[coarse_sec] = coarse_sector_counts.get(coarse_sec, 0) + 1

    for sec, count in coarse_sector_counts.items():
        assert count <= 2, f"Sector {sec} exceeded max 2 allocation with {count} scrips: {summary.top_8}"

    # Verify dry_run did not create files
    assert not universe_file.exists()
    assert not rotations_file.exists()


def test_screener_rotation_detection(local_tmp_dir):
    universe_file = local_tmp_dir / "dynamic_universe.json"
    rotations_file = local_tmp_dir / "rotations.json"

    # Seed an old universe
    old_universe = {
        "generated_at": "2026-09-21 10:00:00",
        "session_date": "2026-09-21",
        "candidates": [
            {"symbol": "CDSL", "sector": "FINANCIAL_SERVICES"},
            {"symbol": "IREDA", "sector": "POWER_ENERGY"},
            {"symbol": "OLD_SCRIP_1", "sector": "IT_SOFTWARE"},
            {"symbol": "OLD_SCRIP_2", "sector": "METALS_MINING"},
        ],
    }
    with open(universe_file, "w", encoding="utf-8") as f:
        json.dump(old_universe, f)

    screener = PremarketScreener(
        universe_path=universe_file,
        rotations_log=rotations_file,
        surveillance_dir=local_tmp_dir / "surveillance",
        as_of_date="2026-09-22",
    )

    # Run screener with persistence
    summary = screener.run_screener(session_date="2026-09-22", dry_run=False)

    # Files must be written
    assert universe_file.exists()
    assert rotations_file.exists()
    assert len(summary.universe_sha256) == 64  # SHA-256 hex string

    # Rotation delta checks
    assert "OLD_SCRIP_1" in summary.dropped
    assert "OLD_SCRIP_2" in summary.dropped
    assert len(summary.added) > 0
    assert len(summary.top_8) == 8

    # Check that rotation log recorded the event
    with open(rotations_file, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["session_date"] == "2026-09-22"
    assert rec["top_8"] == summary.top_8
    assert rec["universe_sha256"] == summary.universe_sha256


def test_screener_rejects_unknown_scrip_synthetic_priors():
    screener = PremarketScreener()
    with pytest.raises(ValueError, match="FAIL-CLOSED: Unknown or uncalibrated scrip"):
        screener.build_candidate("TOTALLY_UNKNOWN_SCRIP", surveillance_set=set())


def test_screener_fails_closed_when_surveillance_missing(monkeypatch, tmp_path):
    empty_dir = tmp_path / "empty_surv"
    empty_dir.mkdir()
    monkeypatch.setattr("antigravity.daemons.track2_premarket_screener.TRACK2_DIR", empty_dir)
    screener = PremarketScreener()
    with pytest.raises(RuntimeError, match="FAIL-CLOSED: Surveillance files missing"):
        screener.load_surveillance_sets()

