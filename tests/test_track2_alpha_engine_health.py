"""
test_track2_alpha_engine_health.py - Tests for Candidate Health State Machine & Degradation
==========================================================================================
Part of Project Swing Trades (Track 2 Autonomous Discovery & Screening Engine).
"""

import pytest
from antigravity.models.track2_alpha_engine import (
    MultiTimeframeAlphaEngine,
    CandidateHealthState,
    AlphaEvaluationResult,
)
from antigravity.models.market_regime_filter import (
    MarketRegimeSnapshot,
    MarketRegimeState,
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


def test_candidate_health_state_enum_values():
    assert CandidateHealthState.LEADER_EXPANDING.value == "LEADER_EXPANDING"
    assert CandidateHealthState.IN_POSITION.value == "IN_POSITION"
    assert CandidateHealthState.DEGRADED_LOW_VOL.value == "DEGRADED_LOW_VOL"
    assert CandidateHealthState.RANGE_BOUND_CHOP.value == "RANGE_BOUND_CHOP"
    assert CandidateHealthState.EXTENDED_EXHAUSTED.value == "EXTENDED_EXHAUSTED"


def test_health_transition_leader_expanding(bullish_regime):
    engine = MultiTimeframeAlphaEngine()

    # Valid breakout with strong volume (3.0x >= 2.5x req)
    res = engine.evaluate_15m_orb(
        symbol="CDSL",
        current_price=1405.0,
        or_high=1400.0,
        or_low=1380.0,
        bucket_volume=300000,
        historical_bucket_volume_median=100000,
        atr14_points=40.0,
        regime_snapshot=bullish_regime,
    )

    assert res.passed_all_gates is True
    assert res.decision == "PAPER_SIGNAL"
    assert res.health_state == CandidateHealthState.LEADER_EXPANDING.value


def test_health_transition_degraded_low_vol(bullish_regime):
    engine = MultiTimeframeAlphaEngine()

    # Breakout attempt with weak volume (1.2x < 2.5x required)
    res = engine.evaluate_15m_orb(
        symbol="SUZLON",
        current_price=44.8,
        or_high=44.5,
        or_low=43.5,
        bucket_volume=120000,
        historical_bucket_volume_median=100000,
        atr14_points=2.0,
        regime_snapshot=bullish_regime,
    )

    assert res.passed_all_gates is False
    assert res.decision == "VOLUME_INSUFFICIENT"
    assert res.health_state == CandidateHealthState.DEGRADED_LOW_VOL.value


def test_health_transition_extended_exhausted(bullish_regime):
    engine = MultiTimeframeAlphaEngine()

    # Price extends beyond 0.5x ATR (allowed: 10 pts, actual: 25 pts)
    res = engine.evaluate_15m_orb(
        symbol="COCHINSHIP",
        current_price=1425.0,
        or_high=1400.0,
        or_low=1380.0,
        bucket_volume=300000,
        historical_bucket_volume_median=100000,
        atr14_points=20.0,
        regime_snapshot=bullish_regime,
    )

    assert res.passed_all_gates is False
    assert res.decision == "OVEREXTENDED_CEILING"
    assert res.health_state == CandidateHealthState.EXTENDED_EXHAUSTED.value


def test_health_transition_range_bound_chop(bullish_regime):
    engine = MultiTimeframeAlphaEngine()

    # Price inside 15m range
    res = engine.evaluate_15m_orb(
        symbol="RVNL",
        current_price=211.0,
        or_high=215.0,
        or_low=208.0,
        bucket_volume=100000,
        historical_bucket_volume_median=100000,
        atr14_points=8.0,
        regime_snapshot=bullish_regime,
    )

    assert res.passed_all_gates is False
    assert res.decision == "IN_RANGE"
    assert res.health_state == CandidateHealthState.RANGE_BOUND_CHOP.value
