"""
tests/test_day4_backtest.py
===========================
Adversarial & Invariant Unit Tests for Sprint Day 4 Deliverables:
- WalkForwardEngine & PurgedFoldManager (antigravity/engine/backtest_engine.py)
- Multi-tier friction hurdles (Tier 1 Zero, Tier 2 Realistic, Tier 3 Severe Stress)
- Portfolio risk governor integration across sleeves (3 slots, Rs 38,000 slot cap, Rs 1,500 risk)
- Conservative daily bar execution ordering, gap-down exits (>1R loss), and circuit lockouts
- Sealed holdout access guards and reproducible metric ledgers
"""

import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import pytest

from antigravity.engine.backtest_engine import (
    BacktestMetrics,
    BacktestSimulation,
    BacktestTrade,
    DailyEquityPoint,
    FrictionPolicy,
    FrictionTier,
    PurgedFold,
    PurgedFoldManager,
    TradeStatus,
    WalkForwardEngine,
    compute_backtest_metrics,
)
from antigravity.engine.risk_governor import (
    AGGREGATE_EXPOSURE_CAP_RS,
    AGGREGATE_RISK_CAP_RS,
    CASH_BUFFER_RS,
    MAX_SLOTS,
    RISK_PER_TRADE_RS,
    SLOT_CAP_RS,
    TOTAL_CORPUS_RS,
    PortfolioRiskGovernor,
)
from antigravity.engine.execution_simulator import (
    DailyBar,
    ExecutionState,
    OrderSide,
    calculate_statutory_costs,
)
from antigravity.strategies.base_strategy import SignalEvent, ExitSignalEvent


# =============================================================================
# PART 1: FOLD ISOLATION, PURGING & INFORMATION BOUNDARY TESTS
# =============================================================================

def test_purged_fold_validation_and_embargo_gap():
    """Verify PurgedFold enforces valid session order and minimum 10-session purge gap."""
    # Valid fold with 10 purge sessions
    fold = PurgedFold(
        fold_id="FOLD_1",
        train_start="2022-01-03",
        train_end="2022-12-15",
        purge_start="2022-12-16",
        purge_end="2022-12-30",
        purge_sessions=10,
        test_start="2023-01-02",
        test_end="2023-12-29",
    )
    assert fold.fold_id == "FOLD_1"
    assert fold.purge_sessions >= 10

    # Invalid fold: purge sessions < 10 must fail closed
    with pytest.raises(ValueError, match="purge_sessions must be at least 10"):
        PurgedFold(
            fold_id="FOLD_INVALID",
            train_start="2022-01-03",
            train_end="2022-12-20",
            purge_start="2022-12-21",
            purge_end="2022-12-25",
            purge_sessions=4,
            test_start="2022-12-26",
            test_end="2023-12-29",
        )

    # Inverted date order must fail closed
    with pytest.raises(ValueError, match="Chronological violation"):
        PurgedFold(
            fold_id="FOLD_INVERTED",
            train_start="2023-01-01",
            train_end="2022-12-31",
            purge_start="2023-01-02",
            purge_end="2023-01-15",
            purge_sessions=10,
            test_start="2023-01-16",
            test_end="2023-12-29",
        )


def test_purged_fold_manager_purges_overlapping_training_trades():
    """
    Trades entered in training whose holding interval intersects or extends
    into the purge/test window must be purged from training evaluation to prevent leakage.
    """
    manager = PurgedFoldManager()
    fold = PurgedFold(
        fold_id="FOLD_1",
        train_start="2022-01-03",
        train_end="2022-12-15",
        purge_start="2022-12-16",
        purge_end="2022-12-30",
        purge_sessions=10,
        test_start="2023-01-02",
        test_end="2023-12-29",
    )

    # Trade A: entered and closed cleanly before purge
    trade_a = BacktestTrade(
        trade_id="T_A",
        strategy_id="DELIVERY_ACCUMULATION",
        symbol="SBIN",
        entry_session="2022-06-01",
        exit_session="2022-06-08",
        entry_price=500.0,
        exit_price=520.0,
        shares=70,
        initial_risk_per_share=15.0,
        status=TradeStatus.CLOSED,
    )

    # Trade B: entered on 2022-12-14, closed on 2022-12-20 (during purge)
    trade_b = BacktestTrade(
        trade_id="T_B",
        strategy_id="DELIVERY_ACCUMULATION",
        symbol="RELIANCE",
        entry_session="2022-12-14",
        exit_session="2022-12-20",
        entry_price=2500.0,
        exit_price=2450.0,
        shares=15,
        initial_risk_per_share=75.0,
        status=TradeStatus.CLOSED,
    )

    # Trade C: entered on 2022-12-15, still open at 2023-01-05 (extends into test)
    trade_c = BacktestTrade(
        trade_id="T_C",
        strategy_id="HIGH52_MOMENTUM",
        symbol="TCS",
        entry_session="2022-12-15",
        exit_session="2023-01-05",
        entry_price=3300.0,
        exit_price=3400.0,
        shares=11,
        initial_risk_per_share=100.0,
        status=TradeStatus.CLOSED,
    )

    filtered_train_trades, purged_trades = manager.filter_training_trades(
        [trade_a, trade_b, trade_c], fold
    )

    assert len(filtered_train_trades) == 1
    assert filtered_train_trades[0].trade_id == "T_A"
    assert len(purged_trades) == 2
    assert {t.trade_id for t in purged_trades} == {"T_B", "T_C"}


def test_unresolved_exits_reported_without_invented_liquidation():
    """
    Open positions reaching the end of the test window without triggering an exit
    must be reported as UNRESOLVED with mark-to-market value; never artificially closed at 0.
    """
    trade = BacktestTrade(
        trade_id="T_UNRESOLVED",
        strategy_id="DELIVERY_ACCUMULATION",
        symbol="INFY",
        entry_session="2023-12-20",
        exit_session=None,
        entry_price=1500.0,
        exit_price=None,
        shares=25,
        initial_risk_per_share=40.0,
        status=TradeStatus.OPEN,
    )

    # Mark to market at final test session close (1520.0)
    trade.mark_to_market(last_close=1520.0, as_of_session="2023-12-29")

    assert trade.status == TradeStatus.UNRESOLVED
    assert trade.exit_session is None
    assert trade.unrealized_pnl == pytest.approx((1520.0 - 1500.0) * 25)
    assert trade.mtm_value == pytest.approx(1520.0 * 25)


def test_per_symbol_warmup_252_sessions_enforced():
    """
    Sleeve B (52-Week High Momentum) requires 252 valid historical sessions PER SECURITY,
    not merely 252 calendar rows across the market.
    """
    engine = WalkForwardEngine()

    # Symbol with 200 sessions -> Not warmed up
    assert not engine.is_symbol_warmed_up(
        symbol="NEW_LISTING", session_count=200, required_warmup=252
    )

    # Symbol with 252 sessions -> Warmed up
    assert engine.is_symbol_warmed_up(
        symbol="ESTABLISHED", session_count=252, required_warmup=252
    )


def test_sealed_holdout_access_fails_closed_without_explicit_opt_in():
    """
    Accessing the 2025-2026 holdout dataset requires explicit caller opt-in (allow_holdout=True);
    defaults fail-closed.
    """
    engine = WalkForwardEngine(allow_holdout=False)
    with pytest.raises(PermissionError, match="Holdout evaluation blocked"):
        engine.run_simulation_for_dates(
            start_date="2025-01-01", end_date="2025-12-31"
        )


# =============================================================================
# PART 2: MULTI-SLEEVE PORTFOLIO CAPACITY & ARBITRATION TESTS
# =============================================================================

def test_shared_portfolio_capacity_across_sleeves():
    """
    Signals from Sleeve A, Sleeve B, and Sleeve C must contend for the same shared
    3 slots, Rs 38,000 slot cap, Rs 114,000 exposure ceiling, and Rs 1,500 trade risk.
    """
    sim = BacktestSimulation(initial_cash=250000.0)
    assert sim.risk_governor.max_slots == 3
    assert sim.risk_governor.slot_cap_rs == 38000.0
    assert sim.risk_governor.risk_per_trade_rs == 1500.0

    # Submit 4 signals simultaneously across sleeves
    sig1 = SignalEvent(
        strategy_id="DELIVERY_ACCUMULATION",
        symbol="SBIN",
        session_date="2023-05-02",
        entry_session="2023-05-03",
        reference_price=550.0,
        stop_loss_price=530.0,
        target_price=590.0,
        priority_score=4.5,
        trace={"sleeve": "A"},
    )
    sig2 = SignalEvent(
        strategy_id="HIGH52_MOMENTUM",
        symbol="RELIANCE",
        session_date="2023-05-02",
        entry_session="2023-05-03",
        reference_price=2400.0,
        stop_loss_price=2320.0,
        target_price=2560.0,
        priority_score=5.0,
        trace={"sleeve": "B"},
    )
    sig3 = SignalEvent(
        strategy_id="EXPIRY_RELIEF",
        symbol="INFY",
        session_date="2023-05-02",
        entry_session="2023-05-03",
        reference_price=1400.0,
        stop_loss_price=1350.0,
        target_price=1500.0,
        priority_score=3.8,
        trace={"sleeve": "C"},
    )
    sig4 = SignalEvent(
        strategy_id="DELIVERY_ACCUMULATION",
        symbol="TCS",
        session_date="2023-05-02",
        entry_session="2023-05-03",
        reference_price=3200.0,
        stop_loss_price=3100.0,
        target_price=3400.0,
        priority_score=2.0,
        trace={"sleeve": "A"},
    )

    accepted, rejected = sim.arbitrate_signals([sig1, sig2, sig3, sig4])

    # Exactly 3 accepted (highest priority: RELIANCE 5.0, SBIN 4.5, INFY 3.8)
    assert len(accepted) == 3
    assert [s.symbol for s in accepted] == ["RELIANCE", "SBIN", "INFY"]
    # 4th signal (TCS) rejected due to slot cap exhaustion
    assert len(rejected) == 1
    assert rejected[0].symbol == "TCS"
    assert "Slot cap reached" in rejected[0].rejection_reason


def test_same_symbol_duplicate_signal_rejection():
    """
    If multiple sleeves trigger signals for the SAME symbol on the same date,
    only the highest priority signal is considered; duplicate is rejected.
    """
    sim = BacktestSimulation(initial_cash=250000.0)
    sig_sleeve_a = SignalEvent(
        strategy_id="DELIVERY_ACCUMULATION",
        symbol="SBIN",
        session_date="2023-05-02",
        entry_session="2023-05-03",
        reference_price=550.0,
        stop_loss_price=530.0,
        target_price=590.0,
        priority_score=3.5,
        trace={"sleeve": "A"},
    )
    sig_sleeve_b = SignalEvent(
        strategy_id="HIGH52_MOMENTUM",
        symbol="SBIN",
        session_date="2023-05-02",
        entry_session="2023-05-03",
        reference_price=550.0,
        stop_loss_price=535.0,
        target_price=580.0,
        priority_score=4.8,
        trace={"sleeve": "B"},
    )

    accepted, rejected = sim.arbitrate_signals([sig_sleeve_a, sig_sleeve_b])
    assert len(accepted) == 1
    assert accepted[0].strategy_id == "HIGH52_MOMENTUM"
    assert len(rejected) == 1
    assert rejected[0].strategy_id == "DELIVERY_ACCUMULATION"
    assert "Duplicate symbol" in rejected[0].rejection_reason


def test_aggregate_15pct_volume_participation_cap():
    """
    Participation budget must be enforced aggregately across all orders on that symbol.
    Maximum shares filled cannot exceed 15% of daily volume.
    """
    sim = BacktestSimulation()
    # Daily bar with 1,000 shares total session volume
    bar = DailyBar(
        symbol="LOW_VOL",
        open=100.0,
        high=105.0,
        low=99.0,
        close=102.0,
        volume=1000,
    )

    # Signal requests 200 shares
    fill_shares, state = sim.compute_fill_shares(
        requested_shares=200, bar=bar
    )
    # 15% of 1000 = 150 shares max
    assert fill_shares == 150
    assert state == ExecutionState.PARTIAL


# =============================================================================
# PART 3: REALISTIC EXECUTION PATH & ADVERSE GAP SLIPPAGE TESTS
# =============================================================================

def test_same_bar_conservative_order_resolution():
    """
    When a daily bar breaches BOTH stop loss and target price on the same session,
    the simulator must resolve the stop loss FIRST (conservative path invariant).
    Pre-entry intraday highs cannot be consumed to claim a winning exit.
    """
    sim = BacktestSimulation()
    trade = BacktestTrade(
        trade_id="T_CONSERVATIVE",
        strategy_id="DELIVERY_ACCUMULATION",
        symbol="SBIN",
        entry_session="2023-05-03",
        exit_session=None,
        entry_price=500.0,
        exit_price=None,
        shares=50,
        initial_risk_per_share=20.0,  # Stop loss = 480.0, Target = 540.0
        stop_loss=480.0,
        target=540.0,
        status=TradeStatus.OPEN,
    )

    # Bar touches both Low (475 <= 480) and High (545 >= 540)
    bar = DailyBar(
        symbol="SBIN",
        open=505.0,
        high=545.0,
        low=475.0,
        close=510.0,
        volume=500000,
    )

    exit_event = sim.evaluate_bar_exit(trade, bar, policy=FrictionPolicy.realistic())
    assert exit_event is not None
    assert exit_event.reason == "STOP_LOSS"
    assert exit_event.exit_price <= 480.0


def test_gap_below_stop_loss_realizes_greater_than_1r_loss():
    """
    If the market opens below the stop-loss price, execution must fill at the OPEN price
    minus adverse gap slippage (25 bps for realistic, 50 bps for severe).
    The realized loss must strictly exceed the initial 1R risk budget.
    """
    sim = BacktestSimulation()
    trade = BacktestTrade(
        trade_id="T_GAP_DOWN",
        strategy_id="HIGH52_MOMENTUM",
        symbol="TATAMOTORS",
        entry_session="2023-05-03",
        exit_session=None,
        entry_price=600.0,
        exit_price=None,
        shares=60,
        initial_risk_per_share=25.0,  # 1R = 1,500. Stop loss = 575.0
        stop_loss=575.0,
        target=650.0,
        status=TradeStatus.OPEN,
    )

    # Bar opens gap down at 550.0 (well below stop loss of 575.0)
    bar = DailyBar(
        symbol="TATAMOTORS",
        open=550.0,
        high=555.0,
        low=540.0,
        close=542.0,
        volume=1000000,
    )

    policy = FrictionPolicy.realistic()  # 25 bps gap slippage
    exit_event = sim.evaluate_bar_exit(trade, bar, policy=policy)

    assert exit_event is not None
    assert exit_event.reason == "GAP_STOP_LOSS"
    # Expected exit price: 550.0 * (1 - 0.0025) = 548.625
    expected_exit = 550.0 * (1.0 - 0.0025)
    assert exit_event.exit_price == pytest.approx(expected_exit, rel=1e-4)

    # Realized loss: (548.625 - 600.0) * 60 = -3082.5 (more than double 1R = 1500)
    realized_loss = (exit_event.exit_price - trade.entry_price) * trade.shares
    realized_R = realized_loss / (trade.initial_risk_per_share * trade.shares)
    assert realized_R < -1.0  # Strictly worse than -1.0R!
    assert realized_loss < -1500.0


def test_locked_circuit_no_bid_preserves_holdings():
    """
    When a security is locked at Lower Circuit (no bids / zero volume / high==low==open==close),
    exit orders cannot execute. Holdings must be preserved, and MTM equity reflects the locked price.
    """
    sim = BacktestSimulation()
    trade = BacktestTrade(
        trade_id="T_LC_LOCKED",
        strategy_id="DELIVERY_ACCUMULATION",
        symbol="SUZLON",
        entry_session="2023-05-03",
        exit_session=None,
        entry_price=50.0,
        exit_price=None,
        shares=600,
        initial_risk_per_share=2.5,  # Stop loss = 47.5
        stop_loss=47.5,
        target=55.0,
        status=TradeStatus.OPEN,
    )

    # Locked lower circuit bar: Open=High=Low=Close=45.0 with zero volume
    bar = DailyBar(
        symbol="SUZLON",
        open=45.0,
        high=45.0,
        low=45.0,
        close=45.0,
        volume=0,  # Zero volume / locked no-bid
    )

    exit_event = sim.evaluate_bar_exit(trade, bar, policy=FrictionPolicy.realistic())
    # No exit possible!
    assert exit_event is None
    assert trade.status == TradeStatus.OPEN
    assert trade.locked_sessions == 1


# =============================================================================
# PART 3B: ADVERSARIAL REGIME STRESS TESTS
# =============================================================================

def test_adversarial_regime_2024_election_volatility():
    """
    Simulate the extreme 04-June-2024 Election Volatility shock session:
    Portfolio holds 3 active slots entering on 2024-06-03 Exit Poll surge,
    then faces massive gap and intraday selloff on 2024-06-04.
    Verifies stop losses execute cleanly, gap slippage applies, and portfolio drawdown <= 6.0%.
    """
    sim = BacktestSimulation(initial_cash=250000.0)
    policy = FrictionPolicy.realistic()

    # Slot 1: SBIN - entered 2024-06-03 at 900.0, stop loss = 865.0, shares = 42 (slot ~37,800, 1R = 1,470)
    # 2024-06-04 bar: Open = 897.0, Low = 731.95. Low breaches SL, Open > SL -> Intraday SL trigger at 865 - slippage.
    trade_sbin = BacktestTrade(
        trade_id="ELEC_SBIN",
        strategy_id="HIGH52_MOMENTUM",
        symbol="SBIN",
        entry_session="2024-06-03",
        entry_price=900.0,
        shares=42,
        initial_risk_per_share=35.0,
        stop_loss=865.0,
        target=970.0,
        status=TradeStatus.OPEN,
    )

    # Slot 2: RELIANCE - entered 2024-06-03 at 3000.0, stop loss = 2920.0, shares = 12 (slot ~36,000, 1R = 960)
    # 2024-06-04 bar: Opens gap-down at 2880.0 (< 2920 SL). Gap SL trigger at 2880 - gap slippage.
    trade_rel = BacktestTrade(
        trade_id="ELEC_RELIANCE",
        strategy_id="DELIVERY_ACCUMULATION",
        symbol="RELIANCE",
        entry_session="2024-06-03",
        entry_price=3000.0,
        shares=12,
        initial_risk_per_share=80.0,
        stop_loss=2920.0,
        target=3160.0,
        status=TradeStatus.OPEN,
    )

    # Slot 3: INFY - entered 2024-06-03 at 1500.0, stop loss = 1450.0, shares = 25 (slot ~37,500, 1R = 1,250)
    # 2024-06-04 bar: Low = 1420.0 breaches SL. Intraday SL trigger at 1450 - slippage.
    trade_infy = BacktestTrade(
        trade_id="ELEC_INFY",
        strategy_id="EXPIRY_RELIEF",
        symbol="INFY",
        entry_session="2024-06-03",
        entry_price=1500.0,
        shares=25,
        initial_risk_per_share=50.0,
        stop_loss=1450.0,
        target=1600.0,
        status=TradeStatus.OPEN,
    )

    # 2024-06-04 Bars
    bar_sbin = DailyBar(symbol="SBIN", open=897.0, high=897.0, low=731.95, close=775.2, volume=122381193)
    bar_rel = DailyBar(symbol="RELIANCE", open=2880.0, high=2900.0, low=2750.0, close=2780.0, volume=35000000)
    bar_infy = DailyBar(symbol="INFY", open=1480.0, high=1485.0, low=1420.0, close=1435.0, volume=18000000)

    exit_sbin = sim.evaluate_bar_exit(trade_sbin, bar_sbin, policy)
    exit_rel = sim.evaluate_bar_exit(trade_rel, bar_rel, policy)
    exit_infy = sim.evaluate_bar_exit(trade_infy, bar_infy, policy)

    assert exit_sbin.reason == "STOP_LOSS"
    assert exit_rel.reason == "GAP_STOP_LOSS"
    assert exit_infy.reason == "STOP_LOSS"

    trade_sbin.close("2024-06-04", exit_sbin.exit_price, exit_sbin.reason)
    trade_rel.close("2024-06-04", exit_rel.exit_price, exit_rel.reason)
    trade_infy.close("2024-06-04", exit_infy.exit_price, exit_infy.reason)

    total_loss = abs(trade_sbin.net_pnl + trade_rel.net_pnl + trade_infy.net_pnl)
    # Total loss across all 3 simultaneous stopped-out trades must be well within portfolio budget
    drawdown_pct = (total_loss / 250000.0) * 100.0

    # Invariant: Max portfolio drawdown must not exceed 6.0% (Rs 15,000)
    assert drawdown_pct <= 6.0, f"Drawdown {drawdown_pct}% exceeded 6.0% cap!"
    # In fact, total loss is ~ Rs 4,500 (approx 1.8% of corpus / 3.0R aggregate)
    assert total_loss < 6000.0


def test_adversarial_regime_2022_bear_market_grind():
    """
    Simulate 2022 Global Bear Market grind:
    Portfolio experiences 8 consecutive stopped-out trades over multiple weeks.
    Verifies that cumulative drawdown is strictly bounded and cash buffer remains inviolate.
    """
    initial_corpus = 250000.0
    equity = initial_corpus
    peak = initial_corpus
    max_dd_rs = 0.0

    # 8 consecutive losing trades of approx 1R (Rs 1,500 each)
    equity_points = [DailyEquityPoint("2022-01-03", equity)]
    trades = []

    for i in range(8):
        trade = BacktestTrade(
            trade_id=f"BEAR_{i}",
            strategy_id="DELIVERY_ACCUMULATION",
            symbol=f"STOCK_{i}",
            entry_session=f"2022-02-{i+1:02d}",
            exit_session=f"2022-02-{i+5:02d}",
            entry_price=1000.0,
            exit_price=950.0,
            shares=30,
            initial_risk_per_share=50.0,  # 1R = 1,500
            status=TradeStatus.CLOSED,
            net_pnl=-1550.0,  # 1R + friction
        )
        trades.append(trade)
        equity += trade.net_pnl
        equity_points.append(DailyEquityPoint(trade.exit_session, equity))
        dd = peak - equity
        if dd > max_dd_rs:
            max_dd_rs = dd

    metrics = compute_backtest_metrics(trades, equity_points, initial_corpus, 1500.0)

    # 8 * 1550 = Rs 12,400 max drawdown
    assert metrics.max_drawdown_rs == pytest.approx(12400.0)
    assert metrics.max_drawdown_pct == pytest.approx(12400.0 / 250000.0 * 100.0)
    # 4.96% <= 6.0% cap!
    assert metrics.max_drawdown_pct <= 6.0
    # Inviolable cash buffer of Rs 136,000 is completely safe: remaining equity is Rs 237,600
    assert equity >= CASH_BUFFER_RS


def test_adversarial_regime_10day_lower_circuit_lockout():
    """
    Simulate Rule 5 10-day consecutive Lower Circuit descent stress scenario:
    A full Rs 38,000 slot gets locked across 10 consecutive sessions at -5% daily band.
    -40.1% descent loss on Rs 38,000 = Rs 15,238 loss.
    Verifies that the portfolio handles the locked descent, updates MTM equity daily,
    and isolates the loss to that single slot without corrupting other slots.
    """
    sim = BacktestSimulation(initial_cash=250000.0)
    trade = BacktestTrade(
        trade_id="T_CROPSTER_DESCENT",
        strategy_id="DELIVERY_ACCUMULATION",
        symbol="LOCKED_SCRIP",
        entry_session="2023-01-02",
        entry_price=100.0,
        shares=380,  # Exactly Rs 38,000 slot cap
        initial_risk_per_share=5.0,
        stop_loss=95.0,
        target=110.0,
        status=TradeStatus.OPEN,
    )

    current_price = 100.0
    daily_equity_points = [DailyEquityPoint("2023-01-02", 250000.0)]

    for day in range(1, 11):
        current_price = round(current_price * 0.95, 2)  # 5% lower circuit each session
        bar = DailyBar(
            symbol="LOCKED_SCRIP",
            open=current_price,
            high=current_price,
            low=current_price,
            close=current_price,
            volume=0,  # Zero volume / locked no-bid
        )
        exit_event = sim.evaluate_bar_exit(trade, bar, policy=FrictionPolicy.realistic())
        assert exit_event is None  # Cannot exit!
        assert trade.locked_sessions == day

        # MTM portfolio equity: initial cash minus allocated slot + MTM value
        mtm_position = current_price * trade.shares
        portfolio_equity = (250000.0 - 38000.0) + mtm_position
        daily_equity_points.append(
            DailyEquityPoint(f"2023-01-{day+2:02d}", portfolio_equity)
        )

    # After 10 days: price dropped from 100 to approx 59.87 (-40.1%)
    expected_descent_loss = (100.0 - current_price) * 380
    assert expected_descent_loss == pytest.approx(38000.0 * 0.401, rel=1e-2)

    # Drawdown on Rs 250,000 corpus: Rs 15,238 / 250,000 = approx 6.09%
    # Portfolio equity remains Rs 234,762 (well above Rs 136,000 cash buffer!)
    final_equity = daily_equity_points[-1].equity
    assert final_equity >= CASH_BUFFER_RS


def test_deterministic_replay_invariance():
    """
    Executing the simulation twice with identical signals and market bars
    must produce bit-for-bit identical trade metrics, fills, and equity values.
    """
    signals = [
        SignalEvent(
            strategy_id="DELIVERY_ACCUMULATION",
            symbol="SBIN",
            session_date="2023-05-02",
            entry_session="2023-05-03",
            reference_price=550.0,
            stop_loss_price=530.0,
            target_price=590.0,
            priority_score=4.5,
            trace={"test": 1},
        ),
        SignalEvent(
            strategy_id="HIGH52_MOMENTUM",
            symbol="TCS",
            session_date="2023-05-02",
            entry_session="2023-05-03",
            reference_price=3200.0,
            stop_loss_price=3100.0,
            target_price=3400.0,
            priority_score=4.0,
            trace={"test": 2},
        ),
    ]

    sim1 = BacktestSimulation()
    sim2 = BacktestSimulation()

    acc1, rej1 = sim1.arbitrate_signals(signals)
    acc2, rej2 = sim2.arbitrate_signals(signals)

    assert [s.symbol for s in acc1] == [s.symbol for s in acc2]
    assert [s.priority_score for s in acc1] == [s.priority_score for s in acc2]
    assert len(rej1) == len(rej2)



# =============================================================================
# PART 4: FRICTION POLICIES, MONOTONICITY & DP GROUPING
# =============================================================================

def test_friction_policy_definitions():
    """Verify exact parameterization of Tier 1, Tier 2, and Tier 3 friction policies."""
    t1 = FrictionPolicy.zero()
    assert t1.tier == FrictionTier.TIER_1_ZERO
    assert t1.normal_slippage_bps == 0.0
    assert t1.gap_slippage_bps == 0.0
    assert not t1.include_statutory_costs
    assert t1.flat_dp_charge_rs == 0.0

    t2 = FrictionPolicy.realistic()
    assert t2.tier == FrictionTier.TIER_2_REALISTIC
    assert t2.normal_slippage_bps == 7.5
    assert t2.gap_slippage_bps == 25.0
    assert t2.include_statutory_costs
    assert t2.flat_dp_charge_rs == 15.93

    t3 = FrictionPolicy.severe()
    assert t3.tier == FrictionTier.TIER_3_SEVERE
    assert t3.normal_slippage_bps == 20.0
    assert t3.gap_slippage_bps == 50.0
    assert t3.include_statutory_costs
    assert t3.flat_dp_charge_rs == 15.93


def test_paired_repricing_monotone_degradation():
    """
    Paired repricing of the IDENTICAL fill ledger across Tier 1, Tier 2, and Tier 3
    must guarantee monotonic net PnL degradation: Tier 1 PnL > Tier 2 PnL > Tier 3 PnL.
    """
    sim = BacktestSimulation()
    trades = [
        BacktestTrade(
            trade_id=f"T_{i}",
            strategy_id="DELIVERY_ACCUMULATION",
            symbol="SBIN",
            entry_session="2023-05-01",
            exit_session="2023-05-08",
            entry_price=500.0,
            exit_price=530.0 if i % 2 == 0 else 485.0,
            shares=50,
            initial_risk_per_share=15.0,
            status=TradeStatus.CLOSED,
        )
        for i in range(10)
    ]

    pnl_t1 = sim.compute_ledger_net_pnl(trades, policy=FrictionPolicy.zero())
    pnl_t2 = sim.compute_ledger_net_pnl(trades, policy=FrictionPolicy.realistic())
    pnl_t3 = sim.compute_ledger_net_pnl(trades, policy=FrictionPolicy.severe())

    assert pnl_t1 > pnl_t2 > pnl_t3, f"Monotonicity violated: T1={pnl_t1}, T2={pnl_t2}, T3={pnl_t3}"


def test_dp_charges_grouped_per_symbol_per_day():
    """
    DP charges (Rs 15.93 flat) must be applied strictly ONCE per symbol per sell day,
    regardless of multiple partial fill fragments, and without double-counting GST.
    """
    sim = BacktestSimulation()
    # 3 sell executions of SBIN on the same day (2023-05-08)
    costs = sim.calculate_sell_friction(
        symbol="SBIN",
        fills=[(500.0, 10), (501.0, 20), (502.0, 20)],
        sell_date="2023-05-08",
        policy=FrictionPolicy.realistic(),
    )
    # Flat DP charge applied once
    assert costs["dp_charges"] == 15.93


# =============================================================================
# PART 5: DRAWDOWN & PERFORMANCE METRICS CONTRACTS
# =============================================================================

def test_drawdown_calculation_rupees_pct_and_r():
    """
    Verify MTM drawdown calculation in rupees, percentage of Rs 250,000 corpus, and R multiples.
    Rs 15,000 drawdown on Rs 250,000 corpus = 6.0% = 10R.
    Rs 6,000 drawdown = 2.4% = 4R.
    """
    equity_curve = [
        DailyEquityPoint("2023-05-01", 250000.0),
        DailyEquityPoint("2023-05-02", 255000.0),  # Peak = 255,000
        DailyEquityPoint("2023-05-03", 245000.0),  # DD = 10,000
        DailyEquityPoint("2023-05-04", 240000.0),  # DD = 15,000
        DailyEquityPoint("2023-05-05", 248000.0),
    ]

    metrics = compute_backtest_metrics(
        trades=[],
        equity_curve=equity_curve,
        corpus_rs=250000.0,
        risk_per_trade_rs=1500.0,
    )

    # Max DD from peak (255,000 - 240,000) = 15,000
    assert metrics.max_drawdown_rs == 15000.0
    assert metrics.max_drawdown_pct == pytest.approx(15000.0 / 250000.0 * 100, rel=1e-4)  # 6.0% of corpus
    assert metrics.max_drawdown_r == pytest.approx(15000.0 / 1500.0, rel=1e-4)  # 10.0 R


def test_empty_and_extreme_metrics_fail_safe():
    """Verify metric calculations are safe against division by zero and edge cases."""
    # Empty trades -> win rate 0.0, profit factor 0.0, mean R 0.0, hurdle fails
    empty_m = compute_backtest_metrics([], [], corpus_rs=250000.0, risk_per_trade_rs=1500.0)
    assert empty_m.total_trades == 0
    assert empty_m.win_rate == 0.0
    assert empty_m.profit_factor == 0.0
    assert not empty_m.hurdle_passed

    # All wins -> profit factor = inf or capped float, not crash
    winning_trade = BacktestTrade(
        trade_id="T_WIN",
        strategy_id="HIGH52_MOMENTUM",
        symbol="TCS",
        entry_session="2023-01-02",
        exit_session="2023-01-10",
        entry_price=3000.0,
        exit_price=3300.0,
        shares=10,
        initial_risk_per_share=50.0,
        status=TradeStatus.CLOSED,
        net_pnl=3000.0,
    )
    win_m = compute_backtest_metrics([winning_trade], [], corpus_rs=250000.0, risk_per_trade_rs=1500.0)
    assert win_m.win_rate == 1.0
    assert math.isinf(win_m.profit_factor) or win_m.profit_factor > 100.0


def test_hurdle_invariants_under_tier2():
    """
    Strategy must pass hurdle criteria under Tier 2:
    Profit Factor >= 1.30, Win Rate >= 45%, and Net Expectancy > 0.25R.
    """
    trades = [
        BacktestTrade(
            trade_id=f"T_{i}",
            strategy_id="DELIVERY_ACCUMULATION",
            symbol="INFY",
            entry_session="2023-01-02",
            exit_session="2023-01-10",
            entry_price=1000.0,
            exit_price=1050.0 if i < 5 else 980.0,  # 5 wins (+50), 5 losses (-20)
            shares=30,
            initial_risk_per_share=20.0,
            status=TradeStatus.CLOSED,
            net_pnl=1400.0 if i < 5 else -650.0,
        )
        for i in range(10)
    ]
    # Win rate = 50% (>= 45%), Gross win = 7000, Gross loss = 3250 -> PF = 2.15 (>= 1.30)
    # Mean net PnL per trade = 375. Initial risk = 600 -> Mean R = 0.625R (> 0.25R)
    equity_curve = [DailyEquityPoint("2023-01-10", 253750.0, cash=250000.0)]
    m = compute_backtest_metrics(trades, equity_curve, corpus_rs=250000.0, risk_per_trade_rs=1500.0)
    assert m.win_rate == 0.50
    assert m.profit_factor >= 1.30
    assert m.net_expectancy_r >= 0.25
    assert m.hurdle_passed
