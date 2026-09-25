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
    # AGENTS.md Rule 4 name. Side-aware: a BUY is locked when offers are zero, a SELL when bids are zero.
    LOCKED_NO_BID = "LOCKED_NO_LIQUIDITY"
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
    # QUEUE_DEPLETION (prints at our price cleared the queue ahead), TRADE_THROUGH (a print beyond our
    # price: every resting order at our price must have filled), TAKER_DISPLAYED_DEPTH, TAKER_LADDER.
    evidence: str = "QUEUE_DEPLETION"


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
    # False until the order has survived one event unfilled. A crossing contra quote on arrival means
    # we take liquidity (TAKER at their price); after resting, it means a contra order met us (MAKER at ours).
    has_rested: bool = False

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
        last_trade_price: Optional[float] = None,
    ) -> SimulatedOrder:
        """Apply one market event, then mark a still-live order as resting (see SimulatedOrder.has_rested)."""
        L2QueueSimulator._apply_event(order, timestamp, trade_volume, bid_depth, ask_depth, best_bid, best_ask,
                                      cancellations_ahead, last_trade_price)
        if not order.is_terminal:
            order.has_rested = True
        return order

    @staticmethod
    def _apply_event(
        order: SimulatedOrder,
        timestamp: str,
        trade_volume: int,
        bid_depth: int,
        ask_depth: int,
        best_bid: float,
        best_ask: float,
        cancellations_ahead: int = 0,
        last_trade_price: Optional[float] = None,
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

        is_buy = order.side == OrderSide.BUY
        execution_price = order.order_price

        # 3a. Marketable: the order crosses the displayed contra quote, so it takes liquidity now
        #     and never joins the queue. Approximation: all displayed contra depth at the best price.
        crossed = (0 < best_ask <= order.order_price) if is_buy else (best_bid >= order.order_price > 0)
        if crossed:
            take = min(order.remaining_shares, int(contra_depth))
            if take > 0:
                if order.has_rested:
                    L2QueueSimulator._record_fill(order, timestamp, order.order_price, take, "MAKER", "CONTRA_CROSSED")
                else:
                    take_px = best_ask if is_buy else best_bid
                    L2QueueSimulator._record_fill(order, timestamp, take_px, take, "TAKER", "TAKER_DISPLAYED_DEPTH")
            return order

        # 3b. Trade-through: a print strictly beyond our price proves every resting order at our
        #     price was matched first (price-time priority), so the whole remainder filled.
        if last_trade_price is not None and (
            (is_buy and last_trade_price < order.order_price) or (not is_buy and last_trade_price > order.order_price)
        ):
            L2QueueSimulator._record_fill(order, timestamp, order.order_price, order.remaining_shares,
                                          "MAKER", "TRADE_THROUGH")
            return order

        # 3c. The displayed best is now worse than our price with no print through it: the orders that
        #     were ahead of us cancelled or were matched, so we are alone at the top. Only prints at our
        #     price can fill us from here.
        if (is_buy and best_bid < order.order_price) or (not is_buy and best_ask > order.order_price):
            order.queue_rank_ahead = 0
        elif (is_buy and best_bid > order.order_price) or (not is_buy and best_ask < order.order_price):
            return order          # market moved away; our price is behind the best, nothing trades there

        if trade_volume <= 0:
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
                L2QueueSimulator._record_fill(order, timestamp, execution_price, fillable_shares,
                                              "MAKER", "QUEUE_DEPLETION")
        return order

    @staticmethod
    def _record_fill(order: SimulatedOrder, timestamp: str, price: float, shares: int,
                     liquidity_flag: str, evidence: str) -> None:
        order.fills.append(FillRecord(timestamp=timestamp, fill_price=price, fill_shares=shares,
                                      turnover=round(shares * price, 2), liquidity_flag=liquidity_flag,
                                      evidence=evidence))
        order.filled_shares += shares
        order.remaining_shares -= shares
        if order.remaining_shares == 0:
            order.state = ExecutionState.FILLED
            order.is_terminal = True
        else:
            order.state = ExecutionState.PARTIAL

    @staticmethod
    def execute_marketable_limit(
        order: SimulatedOrder,
        contra_levels: List[Tuple[float, int]],
        timestamp: str,
    ) -> SimulatedOrder:
        """
        Exact taker execution: walk the displayed contra ladder (asks for a BUY, bids for a SELL)
        level by level, never beyond the order's limit. Whatever is left unfilled is the caller's to
        cancel (IOC) or rest. An empty contra side is a lock: fill probability is zero.
        """
        if order.is_terminal:
            return order
        if not contra_levels or sum(q for _, q in contra_levels) <= 0:
            order.state = ExecutionState.LOCKED_NO_LIQUIDITY
            return order
        is_buy = order.side == OrderSide.BUY
        ladder = sorted(contra_levels, key=lambda x: x[0], reverse=not is_buy)
        for price, qty in ladder:
            if order.remaining_shares == 0:
                break
            if (is_buy and price > order.order_price + 1e-9) or (not is_buy and price < order.order_price - 1e-9):
                break
            take = min(order.remaining_shares, int(qty))
            if take > 0:
                L2QueueSimulator._record_fill(order, timestamp, price, take, "TAKER", "TAKER_LADDER")
        return order
