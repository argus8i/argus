"""
test_track2_dynamic_universe_scanner.py - Unit & Invariant Tests for Dynamic Universe Scanner
=============================================================================================
Part of Project Swing Trades (Track 2 Phase 2A).
"""

import pytest
import os
import json
from antigravity.models.track2_dynamic_universe_scanner import (
    DynamicUniverseScanner,
    ScripCandidate,
    RankedCandidate,
)


def _make_candidate(
    symbol: str,
    sector: str = "FINANCIAL_SERVICES",
    mcap_cr: float = 25000.0,
    dtv_cr: float = 100.0,
    beta: float = 1.5,
    atr_pct: float = 4.0,
    vol_exp: float = 2.0,
    gap_pct: float = 1.0,
    rel_str: float = 5.0,
    is_fno: bool = True,
    is_surveillance: bool = False,
    band_pct: float = 0.0,
    series: str = "EQ",
) -> ScripCandidate:
    open_p = 100.0 * (1.0 + gap_pct / 100.0)
    atr_pts = round(100.0 * (atr_pct / 100.0), 2)
    return ScripCandidate(
        symbol=symbol,
        series=series,
        mcap_cr=mcap_cr,
        dtv_med20_cr=dtv_cr,
        beta=beta,
        atr14_pct=atr_pct,
        atr14_points=atr_pts,
        pre_open_vol=int(10000 * vol_exp),
        median_pre_open_vol_10d=10000,
        open_price=round(open_p, 2),
        prev_close=100.0,
        return_20d_pct=rel_str,
        nifty_return_20d_pct=0.0,
        band_pct=band_pct,
        is_fno_underlying=is_fno,
        is_surveillance=is_surveillance,
        sector=sector,
    )


def test_scanner_filters_non_fno_or_surveillance():
    scanner = DynamicUniverseScanner()
    # Non-F&O
    c1 = _make_candidate("TEST1", is_fno=False)
    passed, reason = scanner.filter_candidate(c1)
    assert passed is False
    assert "active F&O" in reason

    # Surveillance
    c2 = _make_candidate("TEST2", is_surveillance=True)
    passed, reason = scanner.filter_candidate(c2)
    assert passed is False
    assert "surveillance" in reason

    # Fixed price band (e.g. 5% or 20%)
    c3 = _make_candidate("TEST3", band_pct=10.0)
    passed, reason = scanner.filter_candidate(c3)
    assert passed is False
    assert "dynamic flexing" in reason


def test_scanner_filters_mcap_dtv_beta_atr():
    scanner = DynamicUniverseScanner()
    # Mcap below 4k Cr
    c1 = _make_candidate("TEST1", mcap_cr=3500.0)
    assert scanner.filter_candidate(c1)[0] is False

    # DTV below 30 Cr
    c2 = _make_candidate("TEST2", dtv_cr=25.0)
    assert scanner.filter_candidate(c2)[0] is False

    # Beta below 1.3
    c3 = _make_candidate("TEST3", beta=1.1)
    assert scanner.filter_candidate(c3)[0] is False

    # ATR below 3.5%
    c4 = _make_candidate("TEST4", atr_pct=3.0)
    assert scanner.filter_candidate(c4)[0] is False


def test_scanner_ranks_by_composite_score():
    scanner = DynamicUniverseScanner(min_surviving_pool=2)
    # High volume expansion & momentum
    c1 = _make_candidate("LEADER", vol_exp=4.0, gap_pct=2.0, rel_str=15.0, beta=2.0)
    # Moderate metrics
    c2 = _make_candidate("RUNNER", vol_exp=1.5, gap_pct=0.5, rel_str=2.0, beta=1.4)

    ranked = scanner.scan_and_rank([c2, c1])
    assert len(ranked) == 2
    assert ranked[0].symbol == "LEADER"
    assert ranked[1].symbol == "RUNNER"
    assert ranked[0].composite_score > ranked[1].composite_score


def test_scanner_enforces_sector_concentration_max_two():
    scanner = DynamicUniverseScanner(target_universe_size=8, min_surviving_pool=4, max_per_sector=2)
    candidates = [
        # 4 from FINANCIAL_SERVICES
        _make_candidate("FIN1", sector="FINANCIAL_SERVICES", vol_exp=4.0),
        _make_candidate("FIN2", sector="FINANCIAL_SERVICES", vol_exp=3.5),
        _make_candidate("FIN3", sector="FINANCIAL_SERVICES", vol_exp=3.0),
        _make_candidate("FIN4", sector="FINANCIAL_SERVICES", vol_exp=2.5),
        # 2 from POWER_ENERGY
        _make_candidate("PWR1", sector="POWER_ENERGY", vol_exp=3.2),
        _make_candidate("PWR2", sector="POWER_ENERGY", vol_exp=2.8),
        # 2 from DEFENSE
        _make_candidate("DEF1", sector="DEFENSE", vol_exp=2.9),
        _make_candidate("DEF2", sector="DEFENSE", vol_exp=2.4),
    ]

    ranked = scanner.scan_and_rank(candidates)
    symbols = [r.symbol for r in ranked]
    # Exactly FIN1 and FIN2 should make it from FINANCIAL_SERVICES; FIN3 and FIN4 capped
    assert "FIN1" in symbols
    assert "FIN2" in symbols
    assert "FIN3" not in symbols
    assert "FIN4" not in symbols
    assert "PWR1" in symbols
    assert "PWR2" in symbols
    assert "DEF1" in symbols
    assert "DEF2" in symbols


def test_scanner_fails_closed_under_min_surviving_pool():
    scanner = DynamicUniverseScanner(min_surviving_pool=4)
    candidates = [
        _make_candidate("OK1"),
        _make_candidate("OK2"),
        _make_candidate("BAD1", is_fno=False),
    ]
    with pytest.raises(ValueError, match="FAIL-CLOSED"):
        scanner.scan_and_rank(candidates)


def test_scanner_freeze_universe_sha256(tmp_path):
    scanner = DynamicUniverseScanner(min_surviving_pool=2)
    candidates = [_make_candidate("S1"), _make_candidate("S2", sector="ENERGY")]
    ranked = scanner.scan_and_rank(candidates)

    out_file = str(tmp_path / "test_universe.json")
    payload = scanner.freeze_universe(ranked, output_path=out_file, session_date="2026-09-22")

    assert os.path.exists(out_file)
    assert payload["total_qualified"] == 2
    assert "universe_sha256" in payload
    assert len(payload["universe_sha256"]) == 64


def test_scanner_rejects_nan_and_inf_metrics():
    scanner = DynamicUniverseScanner()
    for bad_val in [float("nan"), float("inf"), float("-inf")]:
        c_beta = _make_candidate("NAN_BETA", beta=bad_val)
        passed, reason = scanner.filter_candidate(c_beta)
        assert passed is False
        assert "beta" in reason.lower()

        c_dtv = _make_candidate("NAN_DTV", dtv_cr=bad_val)
        passed, reason = scanner.filter_candidate(c_dtv)
        assert passed is False
        assert "dtv_med20_cr" in reason.lower()

        c_atr = _make_candidate("NAN_ATR", atr_pct=bad_val)
        passed, reason = scanner.filter_candidate(c_atr)
        assert passed is False
        assert "atr14_pct" in reason.lower()

