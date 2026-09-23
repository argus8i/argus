"""
test_track2_portfolio_risk_governor.py - Unit & Invariant Tests for Portfolio Risk Governor
===========================================================================================
Part of Project Swing Trades (Antigravity + Claude Code + OpenAI Codex).
"""

import pytest
from antigravity.models.track2_portfolio_risk_governor import (
    PortfolioRiskGovernor,
    RiskAssessmentResult,
    DEFAULT_SECTOR_MAP,
)


@pytest.fixture
def governor():
    return PortfolioRiskGovernor(
        max_single_trade_risk_rs=1500.0,
        max_aggregate_risk_rs=6000.0,
        max_positions_per_sector=2,
        total_capital_allocation_rs=100000.0,
    )


def test_governor_valid_candidate_passes(governor):
    # CDSL: Entry 1000, Stop 980 (Risk = 20/sh * 50 sh = 1000 Rs <= 1500 Rs)
    res = governor.assess_candidate(
        symbol="CDSL",
        entry_price=1000.0,
        stop_price=980.0,
        quantity=50,
        active_positions=[],
    )
    assert res.is_approved is True
    assert res.rejection_reason is None
    assert res.proposed_risk_rs == 1000.0
    assert res.current_open_risk_rs == 0.0
    assert res.new_total_risk_rs == 1000.0
    assert res.sector_position_count == 1


def test_governor_single_trade_risk_exceeded(governor):
    # Risk = 50/sh * 40 sh = 2000 Rs > 1500 Rs
    res = governor.assess_candidate(
        symbol="CDSL",
        entry_price=1000.0,
        stop_price=950.0,
        quantity=40,
        active_positions=[],
    )
    assert res.is_approved is False
    assert "SINGLE_TRADE_RISK_EXCEEDED" in res.rejection_reason
    assert res.proposed_risk_rs == 2000.0


def test_governor_aggregate_portfolio_risk_cap(governor):
    # Existing active positions with total open risk of Rs 5000
    active = [
        {"symbol": "SUZLON", "sector": "GREEN_ENERGY_POWER", "open_risk_rs": 2500.0, "notional_rs": 20000.0},
        {"symbol": "RVNL", "sector": "PSU_RAILWAYS_INFRA", "open_risk_rs": 2500.0, "notional_rs": 25000.0},
    ]
    # New proposed candidate with risk Rs 1200 -> Total = 6200 > 6000
    res = governor.assess_candidate(
        symbol="CDSL",
        entry_price=1000.0,
        stop_price=970.0,
        quantity=40,  # 30 * 40 = 1200 Rs
        active_positions=active,
    )
    assert res.is_approved is False
    assert "AGGREGATE_PORTFOLIO_RISK_EXCEEDED" in res.rejection_reason
    assert res.current_open_risk_rs == 5000.0
    assert res.new_total_risk_rs == 6200.0


def test_governor_sector_concentration_limit(governor):
    # Already 2 active positions in CAPITAL_MARKETS_FINTECH (CDSL and ANGELONE)
    active = [
        {"symbol": "CDSL", "sector": "CAPITAL_MARKETS_FINTECH", "open_risk_rs": 1000.0, "notional_rs": 20000.0},
        {"symbol": "ANGELONE", "sector": "CAPITAL_MARKETS_FINTECH", "open_risk_rs": 1000.0, "notional_rs": 20000.0},
    ]
    # Attempting to add a 3rd stock in CAPITAL_MARKETS_FINTECH (POLICYBZR)
    res = governor.assess_candidate(
        symbol="POLICYBZR",
        entry_price=1200.0,
        stop_price=1180.0,
        quantity=25,  # 20 * 25 = 500 Rs
        active_positions=active,
    )
    assert res.is_approved is False
    assert "SECTOR_CONCENTRATION_EXCEEDED" in res.rejection_reason
    assert res.sector_position_count == 2


def test_governor_total_capital_ceiling(governor):
    # Active positions with notional of Rs 90,000
    active = [
        {"symbol": "CDSL", "open_risk_rs": 1000.0, "entry_price": 1000.0, "stop_price": 980.0, "quantity": 90}
    ]
    # Proposed trade notional: Rs 20,000 -> Total 1,10,000 > 1,00,000
    res = governor.assess_candidate(
        symbol="SUZLON",
        entry_price=50.0,
        stop_price=48.0,
        quantity=400,  # Notional = 20,000 Rs, Risk = 800 Rs
        active_positions=active,
    )
    assert res.is_approved is False
    assert "TOTAL_CAPITAL_EXCEEDED" in res.rejection_reason


def test_governor_duplicate_symbol_rejection(governor):
    active = [
        {"symbol": "CDSL", "open_risk_rs": 1000.0, "notional_rs": 20000.0}
    ]
    res = governor.assess_candidate(
        symbol="CDSL",
        entry_price=1000.0,
        stop_price=980.0,
        quantity=30,
        active_positions=active,
    )
    assert res.is_approved is False
    assert "DUPLICATE_SYMBOL_POSITION" in res.rejection_reason


def test_governor_fail_closed_on_invalid_inputs(governor):
    # Inverted stop
    res1 = governor.assess_candidate("CDSL", 1000.0, 1020.0, 10, [])
    assert res1.is_approved is False
    assert "INVERTED_STOP" in res1.rejection_reason

    # Non-positive quantity
    res2 = governor.assess_candidate("CDSL", 1000.0, 980.0, 0, [])
    assert res2.is_approved is False
    assert "INVALID_QUANTITY" in res2.rejection_reason

    # Unmapped sector
    res3 = governor.assess_candidate("UNKNOWN_STOCK_XYZ", 100.0, 95.0, 10, [])
    assert res3.is_approved is False
    assert "UNMAPPED_SECTOR" in res3.rejection_reason


def test_governor_calibrate_for_corpus_2_5_lakhs():
    gov_25 = PortfolioRiskGovernor.calibrate_for_corpus(
        corpus_rs=250000.0,
        risk_per_trade_rs=1500.0,
        max_concurrent_positions=3,
        cash_buffer_rs=50000.0,
    )
    assert gov_25.max_single_trade_risk_rs == 1500.0
    assert gov_25.max_aggregate_risk_rs == 4500.0
    assert gov_25.total_capital_allocation_rs == 200000.0
    assert gov_25.max_positions_per_sector == 2

    # Active 2 positions with Rs 3000 risk
    active = [
        {"symbol": "CDSL", "sector": "CAPITAL_MARKETS_FINTECH", "open_risk_rs": 1500.0, "notional_rs": 50000.0},
        {"symbol": "SUZLON", "sector": "GREEN_ENERGY_POWER", "open_risk_rs": 1500.0, "notional_rs": 50000.0},
    ]

    # 3rd position with Rs 1500 risk passes exactly (Total risk = Rs 4500, Notional = Rs 150000 <= Rs 200000)
    res = gov_25.assess_candidate(
        symbol="RVNL",
        entry_price=200.0,
        stop_price=190.0,
        quantity=150,  # Risk = 10 * 150 = 1500 Rs, Notional = 30000 Rs
        active_positions=active,
    )
    assert res.is_approved is True
    assert res.new_total_risk_rs == 4500.0
    assert res.new_total_notional_rs == 130000.0

    # 4th position would exceed aggregate risk cap of Rs 4500
    res_4th = gov_25.assess_candidate(
        symbol="BDL",
        entry_price=1000.0,
        stop_price=980.0,
        quantity=50,  # Risk = 1000 Rs -> Total = 5500 > 4500
        active_positions=active + [{"symbol": "RVNL", "open_risk_rs": 1500.0, "notional_rs": 30000.0}],
    )
    assert res_4th.is_approved is False
    assert "AGGREGATE_PORTFOLIO_RISK_EXCEEDED" in res_4th.rejection_reason


def test_governor_calibrate_default_75k_buffer_and_var_elm_gate():
    # Tests the hardened default calibration (Codex Audit 2026-09-23)
    gov_hardened = PortfolioRiskGovernor.calibrate_for_corpus()
    assert gov_hardened.max_single_trade_risk_rs == 1500.0
    assert gov_hardened.max_aggregate_risk_rs == 4500.0
    assert gov_hardened.total_capital_allocation_rs == 175000.0  # Rs 2,50,000 - Rs 75,000 cash buffer

    # Candidate with VAR+ELM <= 30% passes
    res_pass = gov_hardened.assess_candidate(
        symbol="CDSL",
        entry_price=1000.0,
        stop_price=985.0,
        quantity=100,  # Risk Rs 1500, Notional Rs 100k
        active_positions=[],
        var_elm_rate=0.25,  # 25% VAR+ELM
    )
    assert res_pass.is_approved is True

    # Candidate with VAR+ELM > 30% is rejected fail-closed to protect against SEBI margin shortfall
    res_fail = gov_hardened.assess_candidate(
        symbol="CDSL",
        entry_price=1000.0,
        stop_price=985.0,
        quantity=100,
        active_positions=[],
        var_elm_rate=0.35,  # 35% VAR+ELM exceeds 30% ceiling
    )
    assert res_fail.is_approved is False
    assert "VAR_ELM_EXCEEDS_MARGIN_CEILING" in res_fail.rejection_reason


def test_governor_rejects_nan_in_existing_position():
    gov = PortfolioRiskGovernor.calibrate_for_corpus(250000.0)
    # Existing position with NaN risk must cause immediate fail-closed rejection
    res = gov.assess_candidate(
        symbol="IREDA",
        entry_price=100.0,
        stop_price=98.5,
        quantity=1000,
        active_positions=[{"symbol": "CDSL", "open_risk_rs": float("nan")}],
    )
    assert res.is_approved is False
    assert "CORRUPTED_PORTFOLIO_EXPOSURE" in res.rejection_reason



