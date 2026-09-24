"""
test_caliber_performance_analytics.py - Unit Tests for CALIBER Performance & Expectancy Engine
==============================================================================================
Part of Project Swing Trades // ARGUS 8i // BEACON.
"""

import json
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


def test_load_from_orders_log_lifecycle_replay_queued_then_closed(tmp_path):
    # Codex R13: QUEUED event followed by CLOSED event with same order_id must record the trade
    log = tmp_path / "orders.jsonl"
    events = [
        {"order_id": "ORD_001", "symbol": "CDSL", "status": "QUEUED", "shares": 100, "entry_price": 1000.0},
        {"order_id": "ORD_001", "symbol": "CDSL", "status": "CLOSED", "shares": 100, "entry_price": 1000.0, "exit_price": 1010.0, "net_pnl_rs": 1000.0},
    ]
    log.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

    analytics = CaliberPerformanceAnalytics(orders_log_path=log)
    summary = analytics.load_from_orders_log(orders_path=log)

    assert summary.total_trades == 1
    assert summary.winning_trades == 1
    assert summary.net_pnl_rs == 1000.0


def test_load_from_orders_log_rejects_unsupported_profit_without_status(tmp_path):
    # Codex R13: Record with only order_id and net_pnl_rs but no valid closed status must be rejected
    log = tmp_path / "orders.jsonl"
    events = [
        {"order_id": "ORD_BAD_1", "net_pnl_rs": 500.0},  # No status
        {"order_id": "ORD_BAD_2", "status": "QUEUED", "net_pnl_rs": 300.0},  # Queued status with fake profit
        {"order_id": "ORD_BAD_3", "status": "CANCELLED", "net_pnl_rs": 200.0},  # Cancelled status with fake profit
    ]
    log.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

    analytics = CaliberPerformanceAnalytics(orders_log_path=log)
    summary = analytics.load_from_orders_log(orders_path=log)

    assert summary.total_trades == 0
    assert summary.net_pnl_rs == 0.0


def test_trade_risk_specific_r_multiple_scaling():
    # Claude A28: R-multiple must scale by trade's actual initial risk, not arbitrary fixed Rs 1500
    analytics = CaliberPerformanceAnalytics(capital_base_rs=250000.0, risk_per_trade_rs=1500.0)
    
    # Trade with entry=100, stop=98 (risk=2/sh), 100 shares -> total risk = Rs 200
    # Net profit = Rs 400 -> Should be +2.0R (not 400 / 1500 = 0.27R)
    trade = {
        "symbol": "CDSL",
        "entry_price": 100.0,
        "stop_loss": 98.0,
        "shares": 100,
        "net_pnl_rs": 400.0,
        "charges_rs": 20.0,
    }
    summary = analytics.calculate_metrics(trades=[trade])
    assert summary.total_trades == 1
    assert summary.equity_curve[0]["r_multiple"] == 2.0
    assert summary.avg_win_r == 2.0



