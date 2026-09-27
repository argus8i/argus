"""
research/tests/test_order_book_queue.py
=======================================
Adversarial unit tests for discrete 4-state L2 queue matching simulator.
"""

import pytest
from research.backtest.order_book import (
    ExecutionState,
    L2QueueSimulator,
    OrderSide,
    SimulatedOrder,
)


def test_order_remains_queued_when_volume_insufficient_for_rank():
    """Order must stay QUEUED if volume does not clear queue rank ahead."""
    order = SimulatedOrder(
        order_id="ORD1",
        symbol="INFY",
        side=OrderSide.BUY,
        order_price=1500.0,
        total_shares=100,
        arrival_midpoint=1500.0,
        arrival_timestamp="2026-09-24T09:30:00+05:30",
        queue_rank_ahead=1000,
        usable_volume_fraction=0.85,
    )
    # 500 shares traded -> usable volume = 500 * 0.85 = 425
    # Rank drops from 1000 to 575; 0 shares filled
    L2QueueSimulator.process_order_event(
        order=order,
        timestamp="2026-09-24T09:31:00+05:30",
        trade_volume=500,
        bid_depth=5000,
        ask_depth=5000,
        best_bid=1500.0,
        best_ask=1500.5,
    )
    assert order.state == ExecutionState.QUEUED
    assert order.queue_rank_ahead == 575
    assert order.filled_shares == 0
    assert order.remaining_shares == 100


def test_order_transitions_to_partial_fill():
    """Order must transition to PARTIAL when volume clears queue and partly fills order."""
    order = SimulatedOrder(
        order_id="ORD2",
        symbol="TCS",
        side=OrderSide.BUY,
        order_price=3500.0,
        total_shares=100,
        arrival_midpoint=3500.0,
        arrival_timestamp="2026-09-24T09:30:00+05:30",
        queue_rank_ahead=200,
        usable_volume_fraction=1.0,  # 100% usable for clean math
    )
    # 250 shares traded: 200 clears queue, 50 fills order
    L2QueueSimulator.process_order_event(
        order=order,
        timestamp="2026-09-24T09:31:00+05:30",
        trade_volume=250,
        bid_depth=2000,
        ask_depth=2000,
        best_bid=3500.0,
        best_ask=3500.5,
    )
    assert order.state == ExecutionState.PARTIAL
    assert order.queue_rank_ahead == 0
    assert order.filled_shares == 50
    assert order.remaining_shares == 50
    assert len(order.fills) == 1


def test_order_transitions_to_filled():
    """Order must transition to FILLED when cumulative volume clears queue + full order size."""
    order = SimulatedOrder(
        order_id="ORD3",
        symbol="RELIANCE",
        side=OrderSide.BUY,
        order_price=2800.0,
        total_shares=50,
        arrival_midpoint=2800.0,
        arrival_timestamp="2026-09-24T09:30:00+05:30",
        queue_rank_ahead=100,
        usable_volume_fraction=1.0,
    )
    # Trade 150 shares: 100 clears queue, 50 clears entire order
    L2QueueSimulator.process_order_event(
        order=order,
        timestamp="2026-09-24T09:31:00+05:30",
        trade_volume=150,
        bid_depth=5000,
        ask_depth=5000,
        best_bid=2800.0,
        best_ask=2800.5,
    )
    assert order.state == ExecutionState.FILLED
    assert order.is_terminal is True
    assert order.filled_shares == 50
    assert order.remaining_shares == 0


def test_locked_circuit_prevents_execution():
    """Zero contra-side depth must set LOCKED_NO_LIQUIDITY with zero fills."""
    order = SimulatedOrder(
        order_id="ORD4",
        symbol="CIRCUIT_TEST",
        side=OrderSide.BUY,
        order_price=100.0,
        total_shares=100,
        arrival_midpoint=100.0,
        arrival_timestamp="2026-09-24T09:30:00+05:30",
        queue_rank_ahead=0,
    )
    # Upper circuit: Ask depth is 0
    L2QueueSimulator.process_order_event(
        order=order,
        timestamp="2026-09-24T09:31:00+05:30",
        trade_volume=5000,  # Print occurs but depth is 0
        bid_depth=100000,
        ask_depth=0,
        best_bid=100.0,
        best_ask=0.0,
    )
    assert order.state == ExecutionState.LOCKED_NO_LIQUIDITY
    assert order.filled_shares == 0
    assert order.remaining_shares == 100


def test_collar_limit_calculation():
    """Verify collar calculation respects tick size alignment and directional bounds."""
    # Buy side: floor min(ask + 2 ticks, entry + 0.08 * ATR)
    # ask = 100.0, k=2 ticks (0.10) -> 100.10
    # entry = 100.0, atr = 2.0, delta = 0.08 -> 100.0 + 0.16 = 100.16
    # min is 100.10, aligned to 0.05 is 100.10
    buy_collar = L2QueueSimulator.calculate_collar_limit(
        side=OrderSide.BUY,
        current_quote=100.0,
        entry_reference=100.0,
        atr14=2.0,
        tick_size=0.05,
        k_ticks=2,
        delta_atr=0.08,
    )
    assert buy_collar == 100.10

    # Sell side: ceil max(bid - 2 ticks, entry - 0.08 * ATR)
    # bid = 100.0, k=2 ticks (0.10) -> 99.90
    # entry = 100.0, atr = 2.0 -> 99.84
    # max is 99.90, aligned to 0.05 is 99.90
    sell_collar = L2QueueSimulator.calculate_collar_limit(
        side=OrderSide.SELL,
        current_quote=100.0,
        entry_reference=100.0,
        atr14=2.0,
        tick_size=0.05,
        k_ticks=2,
        delta_atr=0.08,
    )
    assert sell_collar == 99.90
