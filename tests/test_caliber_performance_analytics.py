"""
test_caliber_performance_analytics.py - Unit Tests for CALIBER Performance & Expectancy Engine
==============================================================================================
Part of Project Swing Trades // ARGUS 8i // BEACON.
"""

import pytest
from antigravity.models.caliber_performance_analytics import (
    CaliberPerformanceAnalytics,
    PerformanceSummary,
    Rule1GateStatus,
)


def test_caliber_performance_analytics_zero_trades():
    analytics = CaliberPerformanceAnalytics(capital_base_rs=250000.0, risk_per_trade_rs=1500.0)
    summary = analytics.calculate_metrics(trades=[], completed_sessions=0, verified_e3_fills=0)

    assert summary.total_trades == 0
    assert summary.win_rate_pct == 0.0
    assert summary.net_expectancy_r == 0.0
    assert summary.current_equity_rs == 250000.0
    assert summary.rule_1_gate.is_live_trading_permitted is False
    assert summary.rule_1_gate.verdict == "OBSERVATION_ONLY_GATED"
    assert summary.rule_1_gate.completed_sessions == 0
    assert summary.rule_1_gate.verified_e3_fills == 0


def test_caliber_performance_analytics_with_trades():
    analytics = CaliberPerformanceAnalytics(capital_base_rs=250000.0, risk_per_trade_rs=1500.0)

    trades = [
        # Win 1: +1.5R (+2250 Rs gross, -40 Rs charges = +2210 Rs)
        {"symbol": "CDSL", "net_pnl_rs": 2210.0, "charges_rs": 40.0, "r_multiple": 1.47},
        # Win 2: +1.0R (+1500 Rs gross, -30 Rs charges = +1470 Rs)
        {"symbol": "IREDA", "net_pnl_rs": 1470.0, "charges_rs": 30.0, "r_multiple": 0.98},
        # Loss 1: -1.0R (-1500 Rs gross, -25 Rs charges = -1525 Rs)
        {"symbol": "SUZLON", "net_pnl_rs": -1525.0, "charges_rs": 25.0, "r_multiple": -1.02},
    ]

    summary = analytics.calculate_metrics(
        trades=trades,
        completed_sessions=5,
        verified_e3_fills=2,
    )

    assert summary.total_trades == 3
    assert summary.winning_trades == 2
    assert summary.losing_trades == 1
    assert summary.win_rate_pct == 66.7
    assert summary.loss_rate_pct == 33.3

    assert summary.net_expectancy_r > 0.40
    assert summary.net_expectancy_rs > 600.0
    assert summary.profit_factor > 2.0

    assert summary.net_pnl_rs == 2155.0
    assert summary.current_equity_rs == 250000.0 + 2155.0

    assert summary.rule_1_gate.completed_sessions == 5
    assert summary.rule_1_gate.verified_e3_fills == 2
    assert summary.rule_1_gate.sessions_progress_pct == round((5 / 60.0) * 100, 1)
    assert summary.rule_1_gate.fills_progress_pct == round((2 / 20.0) * 100, 1)
    assert summary.rule_1_gate.is_live_trading_permitted is False
    assert summary.rule_1_gate.verdict == "OBSERVATION_ONLY_GATED"

    assert len(summary.equity_curve) == 3
    assert summary.equity_curve[-1]["portfolio_equity_rs"] == summary.current_equity_rs


def test_caliber_breakeven_win_rate_curve():
    # Modal win where Tranche 1 = +1.5R and Tranche 2 = 0R (avg gross win = +0.75R)
    # Friction Rs 258.10 / Rs 1500 = 0.172R
    be_modal = CaliberPerformanceAnalytics.calculate_breakeven_win_rate(
        avg_gross_win_r=0.75,
        avg_gross_loss_r=-1.0,
        friction_rs=258.10,
        risk_per_trade_rs=1500.0,
    )
    # Net win = 0.75 - 0.172 = 0.578R, Net loss = 1.0 + 0.172 = 1.172R
    # p_BE = 1.172 / (0.578 + 1.172) = 66.97%
    assert 66.0 <= be_modal <= 67.5

    # Trend extension win where Tranche 1 = +1.5R and Tranche 2 trails to +3.0R (avg gross win = +2.25R)
    be_trend = CaliberPerformanceAnalytics.calculate_breakeven_win_rate(
        avg_gross_win_r=2.25,
        avg_gross_loss_r=-1.0,
        friction_rs=258.10,
        risk_per_trade_rs=1500.0,
    )
    # Net win = 2.25 - 0.172 = 2.078R, Net loss = 1.172R
    # p_BE = 1.172 / (2.078 + 1.172) = 36.06%
    assert 35.0 <= be_trend <= 37.0


def test_load_from_orders_log_empty_file_never_injects_fake_trades(tmp_path):
    empty_log = tmp_path / "empty_orders.jsonl"
    empty_log.write_text("# empty file\n", encoding="utf-8")

    analytics = CaliberPerformanceAnalytics(orders_log_path=empty_log)
    summary = analytics.load_from_orders_log(orders_path=empty_log)

    assert summary.total_trades == 0
    assert summary.winning_trades == 0
    assert summary.losing_trades == 0
    assert summary.gross_pnl_rs == 0.0
    assert summary.net_pnl_rs == 0.0
    assert len(summary.equity_curve) == 0


