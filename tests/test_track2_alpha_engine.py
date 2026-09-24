"""
test_track2_alpha_engine.py - Unit & Invariant Tests for Multi-Timeframe Alpha Engine
====================================================================================
Part of Project Swing Trades (Track 2 Phase 2B).
"""

import pytest
from antigravity.models.market_regime_filter import (
    MarketRegimeFilter,
    MarketRegimeSnapshot,
    MarketRegimeState,
)
from antigravity.models.track2_alpha_engine import (
    MultiTimeframeAlphaEngine,
    AlphaEvaluationResult,
)


@pytest.fixture
def bullish_regime():
    return MarketRegimeSnapshot(
        state=MarketRegimeState.BULLISH_EXPANSION,
        nifty_ltp=25500.0,
        nifty_or_high=25400.0,
        nifty_or_low=25300.0,
        ad_ratio=1.5,
        advances=320,
        declines=180,
        reason="Bullish expansion",
        allow_standard_orb=True,
        min_volume_multiple=2.5,
    )


@pytest.fixture
def neutral_regime():
    return MarketRegimeSnapshot(
        state=MarketRegimeState.NEUTRAL_SELECTIVE,
        nifty_ltp=25350.0,
        nifty_or_high=25400.0,
        nifty_or_low=25300.0,
        ad_ratio=1.1,
        advances=260,
        declines=240,
        reason="Neutral selective",
        allow_standard_orb=True,
        min_volume_multiple=3.5,
    )


@pytest.fixture
def distribution_regime():
    return MarketRegimeSnapshot(
        state=MarketRegimeState.DISTRIBUTION_GATED,
        nifty_ltp=25250.0,
        nifty_or_high=25400.0,
        nifty_or_low=25300.0,
        ad_ratio=0.6,
        advances=150,
        declines=350,
        reason="Distribution gated",
        allow_standard_orb=False,
        min_volume_multiple=float("inf"),
    )


def test_daily_trend_alignment():
    # Price > EMA20 > EMA50 -> True
    ok, _ = MultiTimeframeAlphaEngine.evaluate_daily_trend(100.0, 95.0, 90.0, strict_trend=True)
    assert ok is True

    # Price < EMA20 -> False
    ok, reason = MultiTimeframeAlphaEngine.evaluate_daily_trend(92.0, 95.0, 90.0, strict_trend=True)
    assert ok is False
    assert "Daily trend not aligned" in reason


def test_orb_breakout_bullish_regime_passes(bullish_regime):
    # Entry: 1005.0, OR High: 1000.0, OR Low: 980.0 (Risk = 25 pts)
    # ATR14: 20 pts. Max extension allowed = 0.5 * 20 = 10 pts. Actual = 5 pts <= 10 pts.
    # Volume: 3000, Median: 1000 -> Vol Mult = 3.0x >= 2.5x
    res = MultiTimeframeAlphaEngine.evaluate_15m_orb(
        symbol="CDSL",
        current_price=1005.0,
        or_high=1000.0,
        or_low=980.0,
        bucket_volume=3000,
        historical_bucket_volume_median=1000,
        atr14_points=20.0,
        regime_snapshot=bullish_regime,
        daily_ema20=950.0,
        daily_ema50=920.0,
        risk_budget_rs=1500.0,
    )
    assert res.passed_all_gates is True
    assert res.decision == "PAPER_SIGNAL"
    assert res.entry_price == 1005.0
    assert res.shares > 0
    assert res.actual_risk_rs <= 1500.0
    assert res.paper_instruction is not None


def test_orb_in_range_rejected(bullish_regime):
    # Price 995 <= OR High 1000
    res = MultiTimeframeAlphaEngine.evaluate_15m_orb(
        symbol="CDSL",
        current_price=995.0,
        or_high=1000.0,
        or_low=980.0,
        bucket_volume=3000,
        historical_bucket_volume_median=1000,
        atr14_points=20.0,
        regime_snapshot=bullish_regime,
    )
    assert res.passed_all_gates is False
    assert res.decision == "IN_RANGE"


def test_orb_extension_ceiling_rejected(bullish_regime):
    # Price 1015.0 > OR High 1000.0 + (0.5 * 20) = 1010.0
    res = MultiTimeframeAlphaEngine.evaluate_15m_orb(
        symbol="CDSL",
        current_price=1015.0,
        or_high=1000.0,
        or_low=980.0,
        bucket_volume=3000,
        historical_bucket_volume_median=1000,
        atr14_points=20.0,
        regime_snapshot=bullish_regime,
    )
    assert res.passed_all_gates is False
    assert res.decision == "OVEREXTENDED_CEILING"
    assert res.extension_points == 15.0
    assert res.max_extension_allowed == 10.0


def test_orb_volume_multiple_selective_regime(neutral_regime):
    # Vol multiple 2.8x is >= 2.5x, but Neutral Regime requires 3.5x -> FAILS
    res1 = MultiTimeframeAlphaEngine.evaluate_15m_orb(
        symbol="CDSL",
        current_price=1005.0,
        or_high=1000.0,
        or_low=980.0,
        bucket_volume=2800,
        historical_bucket_volume_median=1000,
        atr14_points=20.0,
        regime_snapshot=neutral_regime,
    )
    assert res1.passed_all_gates is False
    assert res1.decision == "VOLUME_INSUFFICIENT"
    assert res1.volume_multiple == 2.8
    assert res1.min_volume_required == 3.5

    # Vol multiple 3.8x >= 3.5x -> PASSES
    res2 = MultiTimeframeAlphaEngine.evaluate_15m_orb(
        symbol="CDSL",
        current_price=1005.0,
        or_high=1000.0,
        or_low=980.0,
        bucket_volume=3800,
        historical_bucket_volume_median=1000,
        atr14_points=20.0,
        regime_snapshot=neutral_regime,
    )
    assert res2.passed_all_gates is True
    assert res2.decision == "PAPER_SIGNAL"


def test_orb_distribution_regime_blocked(distribution_regime):
    res = MultiTimeframeAlphaEngine.evaluate_15m_orb(
        symbol="CDSL",
        current_price=1005.0,
        or_high=1000.0,
        or_low=980.0,
        bucket_volume=5000,
        historical_bucket_volume_median=1000,
        atr14_points=20.0,
        regime_snapshot=distribution_regime,
    )
    assert res.passed_all_gates is False
    assert res.decision == "REGIME_BLOCKED"


def test_orb_degenerate_stop_rejected(bullish_regime):
    # OR Low 1005 >= OR High 1000
    res = MultiTimeframeAlphaEngine.evaluate_15m_orb(
        symbol="CDSL",
        current_price=1010.0,
        or_high=1000.0,
        or_low=1005.0,
        bucket_volume=3000,
        historical_bucket_volume_median=1000,
        atr14_points=20.0,
        regime_snapshot=bullish_regime,
    )
    assert res.passed_all_gates is False
    assert res.decision == "DEGENERATE_STOP"


def test_evaluate_microstructure_defense():
    """Verifies OFI and iceberg absorption calculation with execution_realism features."""
    import math
    from research.execution_realism.marketdata import Snapshot, Level, Session

    # Create synthetic snapshots around level 100.0
    s1 = Snapshot(
        symbol="CDSL",
        source="REPLAY",
        seq=1,
        recv_ts=1000.0,
        session=Session.CONTINUOUS,
        ltp=100.00,
        cum_volume=10000,
        day_high=105.00,
        day_low=95.00,
        bids=(Level(price=99.95, qty=1000, orders=5),),
        asks=(Level(price=100.00, qty=1000, orders=5),),
        ltq=200,
    )
    s2 = Snapshot(
        symbol="CDSL",
        source="REPLAY",
        seq=2,
        recv_ts=1001.0,
        session=Session.CONTINUOUS,
        ltp=100.00,
        cum_volume=10500,
        day_high=105.00,
        day_low=95.00,
        bids=(Level(price=99.95, qty=1000, orders=5),),
        asks=(Level(price=100.00, qty=1500, orders=6),),  # Refilled by 500
        ltq=300,
    )
    res = MultiTimeframeAlphaEngine.evaluate_microstructure_defense([s1, s2], breakout_level=100.00)
    assert "normalized_ofi" in res
    assert "absorption_ratio" in res
    assert "is_distribution_trap" in res
    assert isinstance(res["absorption_ratio"], float)
    assert not math.isinf(res["absorption_ratio"])

