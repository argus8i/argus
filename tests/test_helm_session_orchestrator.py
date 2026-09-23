"""
test_helm_session_orchestrator.py - Tests for HELM Session Orchestrator State Machine
=====================================================================================
Part of Project Swing Trades // ARGUS 8i // BEACON.
"""

from datetime import datetime, timezone, timedelta
import pytest
from antigravity.daemons.helm_session_orchestrator import (
    HelmSessionOrchestrator,
    HelmSessionPhase,
    SessionPhase,
    determine_session_phase,
    IST,
)
from antigravity.models.market_regime_filter import (
    MarketRegimeSnapshot,
    MarketRegimeState,
)


def test_determine_session_phase():
    # 08:30 IST -> MARKET_CLOSED
    t1 = datetime(2026, 9, 22, 8, 30, 0, tzinfo=IST)
    assert determine_session_phase(t1) == HelmSessionPhase.MARKET_CLOSED

    # 09:05 IST -> PRE_MARKET
    t2 = datetime(2026, 9, 22, 9, 5, 0, tzinfo=IST)
    assert determine_session_phase(t2) == HelmSessionPhase.PRE_MARKET

    # 09:20 IST -> OPENING_RANGE
    t3 = datetime(2026, 9, 22, 9, 20, 0, tzinfo=IST)
    assert determine_session_phase(t3) == HelmSessionPhase.OPENING_RANGE

    # 09:45 IST -> PRIME_BREAKOUT
    t4 = datetime(2026, 9, 22, 9, 45, 0, tzinfo=IST)
    assert determine_session_phase(t4) == HelmSessionPhase.PRIME_BREAKOUT

    # 11:00 IST -> INTRADAY_MANAGEMENT
    t5 = datetime(2026, 9, 22, 11, 0, 0, tzinfo=IST)
    assert determine_session_phase(t5) == HelmSessionPhase.INTRADAY_MANAGEMENT

    # 15:20 IST -> PRE_CLOSE
    t6 = datetime(2026, 9, 22, 15, 20, 0, tzinfo=IST)
    assert determine_session_phase(t6) == HelmSessionPhase.PRE_CLOSE

    # 15:45 IST -> POST_MARKET
    t7 = datetime(2026, 9, 22, 15, 45, 0, tzinfo=IST)
    assert determine_session_phase(t7) == HelmSessionPhase.POST_MARKET


def test_breakout_evaluation_capacity_gate():
    orchestrator = HelmSessionOrchestrator(corpus_rs=250000.0, risk_per_trade_rs=1500.0, max_positions=3)

    bullish_regime = MarketRegimeSnapshot(
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

    # When 3 positions already active -> Must fail closed with CAPACITY_EXCEEDED
    active_3 = [
        {"symbol": "CDSL", "open_risk_rs": 1500.0},
        {"symbol": "IREDA", "open_risk_rs": 1500.0},
        {"symbol": "ANGELONE", "open_risk_rs": 1500.0},
    ]

    res = orchestrator.run_breakout_evaluation_step(
        symbol="SUZLON",
        current_price=45.0,
        or_high=44.0,
        or_low=43.0,
        volume_15m=300000,
        median_vol=100000,
        atr14_points=2.0,
        macro_regime=bullish_regime,
        active_positions=active_3,
    )

    assert res.passed_all_gates is False
    assert res.decision == "CAPACITY_EXCEEDED"
    assert "Maximum concurrent positions" in res.rejection_reason


def test_orchestrator_status_serialization():
    orchestrator = HelmSessionOrchestrator(corpus_rs=250000.0)
    status = orchestrator.get_status()

    d = status.to_dict()
    assert isinstance(d, dict)
    assert "phase" in d
    assert "phase_action" in d
    assert "top_8_leaders" in d
    assert "open_risk_rs" in d
    assert d["open_risk_rs"] == 3000.0
