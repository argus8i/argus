"""
test_track2_v2.py - Comprehensive Unit Test Suite for Track 2 V2.0 Upgrades
Part of Project Swing Trades (Antigravity + Claude Code + OpenAI Codex).

Tests:
1. MarketRegimeFilter: Breadth gating, Nifty 50 15m OR trend, fail-closed handling.
2. TwoTrancheExitModel: Tranche allocation, odd/single share handling, state transitions, trailing stops.
3. Track2UniverseScanner: Momentum scoring arithmetic, dynamic ranking, fail-closed canonical fallback.
4. ExchangeCircularPoller: Bulletin ingestion and surveillance history updates.
5. LiquidMomentumEngine V2.0 Integration: Regime gating on ORB signals, tranche allocation in SizingResult.
"""

import math
import os
import pytest
from datetime import datetime

from antigravity.models.market_regime_filter import (
    MarketRegimeFilter,
    MarketRegimeState,
    MarketRegimeSnapshot
)
from antigravity.models.two_tranche_exit_model import (
    TwoTrancheExitModel,
    TwoTrancheState,
    TrancheAllocation,
    TrancheStatus
)
from antigravity.models.track2_universe_scanner import (
    Track2UniverseScanner,
    CANONICAL_FALLBACK_CANDIDATES,
    EXPANDED_FNO_UNIVERSE
)
from antigravity.daemons.exchange_circular_poller import ExchangeCircularPoller
from antigravity.models.liquid_momentum_screener import (
    LiquidMomentumEngine,
    LiquidScripSnapshot,
    SizingResult
)


# ==============================================================================
# 1. MARKET REGIME FILTER TESTS
# ==============================================================================

def test_market_regime_bullish_expansion():
    """Nifty > OR High and A/D ratio >= 1.20 triggers BULLISH_EXPANSION with standard 2.5x volume."""
    snapshot = MarketRegimeFilter.evaluate_regime(
        nifty_ltp=25500.0,
        nifty_or_high=25400.0,
        nifty_or_low=25300.0,
        advances=350,
        declines=150,
        min_ad_ratio=1.20
    )
    assert snapshot.state == MarketRegimeState.BULLISH_EXPANSION
    assert snapshot.allow_standard_orb is True
    assert snapshot.min_volume_multiple == 2.5
    assert snapshot.ad_ratio == round(350 / 150, 2)
    assert "BULLISH EXPANSION" in snapshot.reason


def test_market_regime_distribution_gated_by_price():
    """Nifty < OR Low triggers DISTRIBUTION_GATED regardless of advances/declines."""
    snapshot = MarketRegimeFilter.evaluate_regime(
        nifty_ltp=25250.0,
        nifty_or_high=25400.0,
        nifty_or_low=25300.0,
        advances=300,
        declines=200
    )
    assert snapshot.state == MarketRegimeState.DISTRIBUTION_GATED
    assert snapshot.allow_standard_orb is False
    assert snapshot.min_volume_multiple == float("inf")
    assert "ABORT_DISTRIBUTION" in snapshot.reason


def test_market_regime_distribution_gated_by_breadth():
    """A/D ratio < 1.0 triggers DISTRIBUTION_GATED even if Nifty is inside opening range."""
    snapshot = MarketRegimeFilter.evaluate_regime(
        nifty_ltp=25350.0,
        nifty_or_high=25400.0,
        nifty_or_low=25300.0,
        advances=180,
        declines=320
    )
    assert snapshot.state == MarketRegimeState.DISTRIBUTION_GATED
    assert snapshot.allow_standard_orb is False
    assert snapshot.ad_ratio == 0.56


def test_market_regime_neutral_selective():
    """Nifty inside range with positive breadth triggers NEUTRAL_SELECTIVE (requires 3.5x volume)."""
    snapshot = MarketRegimeFilter.evaluate_regime(
        nifty_ltp=25350.0,
        nifty_or_high=25400.0,
        nifty_or_low=25300.0,
        advances=280,
        declines=220
    )
    assert snapshot.state == MarketRegimeState.NEUTRAL_SELECTIVE
    assert snapshot.allow_standard_orb is True
    assert snapshot.min_volume_multiple == 3.5


def test_market_regime_fail_closed_on_nan_or_none():
    """Invalid price metrics fail closed to REGIME_DATA_INVALID."""
    snap1 = MarketRegimeFilter.evaluate_regime(None, 25400.0, 25300.0)
    assert snap1.state == MarketRegimeState.REGIME_DATA_INVALID
    assert snap1.allow_standard_orb is False

    snap2 = MarketRegimeFilter.evaluate_regime(float("nan"), 25400.0, 25300.0)
    assert snap2.state == MarketRegimeState.REGIME_DATA_INVALID

    # Degenerate range (High < Low)
    snap3 = MarketRegimeFilter.evaluate_regime(25350.0, 25300.0, 25400.0)
    assert snap3.state == MarketRegimeState.REGIME_DATA_INVALID


# ==============================================================================
# 2. TWO-TRANCHE EXIT MODEL TESTS
# ==============================================================================

def test_two_tranche_allocation_even_and_odd():
    """Verifies tranche partitioning across even, odd, and single-share positions."""
    # Even shares: 38 shares of CDSL
    a_even = TwoTrancheExitModel.allocate_tranches(entry_price=1332.90, stop_price=1300.00, total_shares=38, target_1_rr=1.5)
    assert a_even.tranche1_shares == 19
    assert a_even.tranche2_shares == 19
    assert a_even.risk_per_share == 32.90
    assert a_even.tranche1_target == round(1332.90 + (1.5 * 32.90), 2)

    # Odd shares: 5 shares
    a_odd = TwoTrancheExitModel.allocate_tranches(entry_price=100.00, stop_price=90.00, total_shares=5, target_1_rr=1.5)
    assert a_odd.tranche1_shares == 3  # math.ceil(5/2)
    assert a_odd.tranche2_shares == 2  # math.floor(5/2)

    # Single share: 1 share
    a_single = TwoTrancheExitModel.allocate_tranches(entry_price=1000.00, stop_price=950.00, total_shares=1, target_1_rr=1.5)
    assert a_single.tranche1_shares == 1
    assert a_single.tranche2_shares == 0


def test_two_tranche_state_progression():
    """Verifies state transitions from initial stop -> breakeven trail -> target 1 hit -> swing trail."""
    alloc = TwoTrancheExitModel.allocate_tranches(entry_price=100.00, stop_price=90.00, total_shares=10, target_1_rr=1.5)
    # Risk per share = 10.0, T1 target = 115.0

    # Phase 1: Inside initial risk (LTP = 105.0)
    s1 = TwoTrancheExitModel.update_state("TEST", alloc, ltp=105.0, peak_price=105.0)
    assert s1.t1_status == TrancheStatus.ACTIVE_INITIAL_STOP
    assert s1.t1_active_sl == 90.00
    assert s1.t2_active_sl == 90.00
    assert s1.combined_risk_state == "INITIAL_RISK_ACTIVE"

    # Phase 2: Gained +1.0R (LTP = 110.0) -> Trailed to breakeven
    s2 = TwoTrancheExitModel.update_state("TEST", alloc, ltp=110.0, peak_price=110.0)
    assert s2.t1_status == TrancheStatus.TRAILED_BREAKEVEN
    assert s2.t1_active_sl == 100.00
    assert s2.t2_active_sl == 100.00
    assert s2.combined_risk_state == "TRAILED_BREAKEVEN_ZERO_DOWNSIDE"

    # Phase 3: Hit Target 1 (LTP = 116.0) -> T1 Banks Profit, T2 trails swing on PDL
    s3 = TwoTrancheExitModel.update_state("TEST", alloc, ltp=116.0, peak_price=116.0, pdl=108.0)
    assert s3.t1_status == TrancheStatus.TARGET_FILLED
    assert s3.t1_realized_pnl == round(5 * (115.0 - 100.0), 2)  # 75.0
    assert s3.t2_status == TrancheStatus.SWING_TRAILING
    assert s3.t2_active_sl == 108.0  # Trailed to PDL
    assert s3.combined_risk_state == "T1_BANKED_T2_RUNNING_ZERO_RISK"

    # Phase 4: Serialization
    d = s3.to_dict()
    assert d["t1_status"] == "TARGET_FILLED"
    assert d["t2_status"] == "SWING_TRAILING"
    assert d["total_realized_pnl"] == 75.0


def test_two_tranche_fail_closed_degenerate():
    """Degenerate stop (stop >= entry) or negative shares raises ValueError."""
    with pytest.raises(ValueError):
        TwoTrancheExitModel.allocate_tranches(entry_price=100.0, stop_price=105.0, total_shares=10)

    with pytest.raises(ValueError):
        TwoTrancheExitModel.allocate_tranches(entry_price=100.0, stop_price=90.0, total_shares=0)


# ==============================================================================
# 3. TRACK 2 UNIVERSE SCANNER TESTS
# ==============================================================================

def test_universe_scanner_ranking():
    """Verifies that candidates are ranked by pre-market volume multiple * Beta."""
    scanner = Track2UniverseScanner(top_n=8)
    mock_pre_open = {
        "IREDA": {"pre_open_volume": 400000, "median_pre_open_volume": 100000, "gap_pct": 2.0},   # Vol multiple = 4.0, Beta = 2.1
        "CDSL": {"pre_open_volume": 80000, "median_pre_open_volume": 40000, "gap_pct": 1.0},      # Vol multiple = 2.0, Beta = 1.45
    }
    ranked = scanner.rank_candidates(pre_open_data=mock_pre_open)
    assert len(ranked) >= 2
    # IREDA should rank higher than CDSL due to 4.0x volume and 2.1 Beta
    symbols = [c.symbol for c in ranked]
    assert symbols.index("IREDA") < symbols.index("CDSL")


def test_universe_scanner_canonical_fallback():
    """If candidate pool is thin (< 4 scrips), canonical fallback activates."""
    scanner = Track2UniverseScanner(top_n=8)
    # Provide empty universe
    res = scanner.scan_and_save(universe=[])
    assert res["is_canonical_fallback"] is True
    assert res["total_qualified"] == len(CANONICAL_FALLBACK_CANDIDATES)
    canonical_syms = {c["symbol"] for c in CANONICAL_FALLBACK_CANDIDATES}
    result_syms = {c["symbol"] for c in res["candidates"]}
    assert canonical_syms == result_syms


# ==============================================================================
# 4. EXCHANGE CIRCULAR POLLER TESTS
# ==============================================================================

def test_exchange_circular_poller_audit():
    """Verifies daily circular poller runs basket audit and updates surveillance history."""
    poller = ExchangeCircularPoller()
    report = poller.poll_and_update()
    assert report["total_evaluated"] >= 8
    assert report["qualified_count"] >= 8
    assert report["disqualified_count"] == 0


# ==============================================================================
# 5. LIQUID MOMENTUM ENGINE V2.0 INTEGRATION TESTS
# ==============================================================================

def test_orb_breakout_gated_by_regime():
    """evaluate_15m_orb_breakout rejects breakout when regime_snapshot is in DISTRIBUTION_GATED."""
    regime_dist = MarketRegimeFilter.evaluate_regime(
        nifty_ltp=25200.0,
        nifty_or_high=25400.0,
        nifty_or_low=25300.0,
        advances=120,
        declines=380
    )
    res = LiquidMomentumEngine.evaluate_15m_orb_breakout(
        symbol="CDSL",
        current_price=1350.00,
        or_high=1330.00,
        or_low=1300.00,
        bucket_volume=200000,
        historical_bucket_volume_median=50000,
        atr14_intraday=20.0,
        min_volume_multiple=2.5,
        regime_snapshot=regime_dist
    )
    assert res["signal"] == "HOLD_REJECT_MARKET_DISTRIBUTION"
    assert "distribution" in res["reason"].lower()


def test_orb_breakout_selective_regime_volume_elevation():
    """In NEUTRAL_SELECTIVE regime, minimum volume requirement elevates from 2.5x to 3.5x."""
    regime_neutral = MarketRegimeFilter.evaluate_regime(
        nifty_ltp=25350.0,
        nifty_or_high=25400.0,
        nifty_or_low=25300.0,
        advances=260,
        declines=240
    )
    assert regime_neutral.min_volume_multiple == 3.5

    # 3.0x volume passes standard 2.5x but fails 3.5x selective threshold
    res = LiquidMomentumEngine.evaluate_15m_orb_breakout(
        symbol="CDSL",
        current_price=1335.00,
        or_high=1330.00,
        or_low=1300.00,
        bucket_volume=150000,
        historical_bucket_volume_median=50000,  # 3.0x
        atr14_intraday=20.0,
        min_volume_multiple=2.5,
        regime_snapshot=regime_neutral
    )
    assert res["signal"] == "HOLD_REJECT_FALSE_BREAKOUT"


def test_calculate_position_size_includes_tranche_allocation():
    """SizingResult includes non-None tranche_allocation when shares > 0."""
    res = LiquidMomentumEngine.calculate_position_size(
        entry_price=1332.90,
        or_low=1300.00,
        atr14=25.0,
        dtv_med20_cr=120.0,
        exchange="NSE",
        risk_budget_rs=1500.0
    )
    assert res.shares > 0
    assert res.tranche_allocation is not None
    assert res.tranche_allocation.total_shares == res.shares
    assert res.tranche_allocation.tranche1_shares + res.tranche_allocation.tranche2_shares == res.shares
    assert res.tranche_allocation.tranche1_target > res.tranche_allocation.entry_price
