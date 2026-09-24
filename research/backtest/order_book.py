"""
research/backtest/order_book.py
===============================
Discrete 4-State L2 Order Book & FIFO Queue Matching Simulator.
Implements non-deterministic, queue-priority execution reality per Section 1.2
of the BEACON Institutional Specification (Codex & Claude).

States:
  1. QUEUED: Order accepted, waiting behind R shares in the FIFO order book queue.
  2. PARTIAL: Eligible trade turnover matches a portion of order quantity.
  3. FILLED: Cumulative turnover clears queue rank + order size: V_cum >= (R + Q) / eta.
  4. LOCKED_NO_BID / LOCKED_NO_ASK: Circuit lockout. Zero contra-side liquidity; fill probability = 0%.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple


class ExecutionState(str, Enum):
    QUEUED = "QUEUED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    LOCKED_NO_LIQUIDITY = "LOCKED_NO_LIQUIDITY"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class FillRecord:
    timestamp: str
    fill_price: float
    fill_shares: int
    turnover: float
    liquidity_flag: str  # "MAKER" or "TAKER"


@dataclass
class SimulatedOrder:
    order_id: str
    symbol: str
    side: OrderSide
    order_price: float
    total_shares: int
    arrival_midpoint: float
    arrival_timestamp: str
    queue_rank_ahead: int
    usable_volume_fraction: float = 0.85
    tick_size: float = 0.05
    
    # Dynamic lifecycle tracking
    state: ExecutionState = ExecutionState.QUEUED
    filled_shares: int = 0
    remaining_shares: int = 0
    fills: List[FillRecord] = field(default_factory=list)
    cumulative_volume_processed: int = 0
    is_terminal: bool = False

    def __post_init__(self):
        self.remaining_shares = self.total_shares

    @property
    def weighted_average_fill_price(self) -> float:
        if not self.fills or self.filled_shares == 0:
            return 0.0
        total_turnover = sum(f.turnover for f in self.fills)
        return total_turnover / self.filled_shares

    @property
    def implementation_shortfall_bps(self) -> float:
        """
        Implementation shortfall in basis points relative to arrival midpoint:
        IS_exec = 10^4 * s * (avg_fill_price - m0) / m0
        """
        if self.filled_shares == 0 or self.arrival_midpoint <= 0.0:
            return 0.0
        s = 1.0 if self.side == OrderSide.BUY else -1.0
        avg_px = self.weighted_average_fill_price
        return round(10000.0 * s * (avg_px - self.arrival_midpoint) / self.arrival_midpoint, 2)


class L2QueueSimulator:
    """
    Simulates FIFO queue progression and execution for resting and aggressive orders
    using 5-level market depth snapshots and incoming bar/trade turnover.
    """

    @staticmethod
    def align_to_tick(price: float, tick_size: float = 0.05, round_up: bool = False) -> float:
        """Align price to instrument tick grid."""
        steps = price / tick_size
        rounded = int(steps + 0.99999) if round_up else int(steps + 0.00001)
        return round(rounded * tick_size, 2)

    @classmethod
    def calculate_collar_limit(
        cls,
        side: OrderSide,
        current_quote: float,
        entry_reference: float,
        atr14: float,
        tick_size: float = 0.05,
        k_ticks: int = 2,
        delta_atr: float = 0.08,
    ) -> float:
        """
        Compute strict price collar limit:
        Buy:  L = h * floor(min(ask + k*h, entry + delta*ATR) / h)
        Sell: L = h * ceil(max(bid - k*h, entry - delta*ATR) / h)
        """
        if side == OrderSide.BUY:
            raw_collar = min(current_quote + (k_ticks * tick_size), entry_reference + (delta_atr * atr14))
            return cls.align_to_tick(raw_collar, tick_size=tick_size, round_up=False)
        else:
            raw_collar = max(current_quote - (k_ticks * tick_size), entry_reference - (delta_atr * atr14))
            return cls.align_to_tick(raw_collar, tick_size=tick_size, round_up=True)

    @staticmethod
    def process_order_event(
        order: SimulatedOrder,
        timestamp: str,
        trade_volume: int,
        bid_depth: int,
        ask_depth: int,
        best_bid: float,
        best_ask: float,
        cancellations_ahead: int = 0,
    ) -> SimulatedOrder:
        """
        Process an incoming market tick/event through the FIFO queue state machine.
        
        Args:
            order: The active SimulatedOrder.
            timestamp: Event timestamp.
            trade_volume: Volume traded at or through order price during this interval.
            bid_depth: Aggregate displayed bid quantity across 5 levels.
            ask_depth: Aggregate displayed ask quantity across 5 levels.
            best_bid: Best bid price.
            best_ask: Best ask price.
            cancellations_ahead: Verified cancellations ahead in queue.
        """
        if order.is_terminal:
            return order

        # 1. Check for locked circuit (zero liquidity on contra side)
        contra_depth = ask_depth if order.side == OrderSide.BUY else bid_depth
        if contra_depth <= 0:
            order.state = ExecutionState.LOCKED_NO_LIQUIDITY
            # Order remains active but cannot match during lock
            return order

        # 2. Account for cancellations ahead (reduces queue rank directly)
        if cancellations_ahead > 0:
            order.queue_rank_ahead = max(0, order.queue_rank_ahead - cancellations_ahead)

        # 3. Check price executability
        # Passive limit buy matches if market trades at or below order price
        is_executable_price = False
        execution_price = order.order_price

        if order.side == OrderSide.BUY:
            if best_ask <= order.order_price:
                # Immediate cross / aggressive fill
                is_executable_price = True
                execution_price = best_ask
            elif best_bid == order.order_price:
                # Passive resting order at best bid
                is_executable_price = True
        else:
            if best_bid >= order.order_price:
                is_executable_price = True
                execution_price = best_bid
            elif best_ask == order.order_price:
                is_executable_price = True

        if not is_executable_price or trade_volume <= 0:
            return order

        # 4. Usable volume turnover application
        usable_vol = int(trade_volume * order.usable_volume_fraction)
        order.cumulative_volume_processed += usable_vol

        # 5. FIFO Queue matching logic
        if order.queue_rank_ahead > 0:
            if usable_vol >= order.queue_rank_ahead:
                excess_vol = usable_vol - order.queue_rank_ahead
                order.queue_rank_ahead = 0
            else:
                order.queue_rank_ahead -= usable_vol
                excess_vol = 0
        else:
            excess_vol = usable_vol

        # 6. Fill calculation on excess volume
        if excess_vol > 0:
            fillable_shares = min(order.remaining_shares, excess_vol)
            if fillable_shares > 0:
                fill_record = FillRecord(
                    timestamp=timestamp,
                    fill_price=execution_price,
                    fill_shares=fillable_shares,
                    turnover=round(fillable_shares * execution_price, 2),
                    liquidity_flag="TAKER" if execution_price != order.order_price else "MAKER",
                )
                order.fills.append(fill_record)
                order.filled_shares += fillable_shares
                order.remaining_shares -= fillable_shares

                if order.remaining_shares == 0:
                    order.state = ExecutionState.FILLED
                    order.is_terminal = True
                else:
                    order.state = ExecutionState.PARTIAL

        return order
