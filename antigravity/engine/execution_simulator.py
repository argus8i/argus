"""
antigravity/engine/execution_simulator.py
=========================================
Multi-Day Swing Execution Reality Engine & Statutory Friction Simulator for Track 2.
Part of Project Swing Trades (ARGUS 8i Track 2 Liquid Desk).

Core Capabilities:
1. Itemized Statutory Friction Model (Zerodha Delivery Equity Calibrated):
   - Brokerage: Rs 0.00 for delivery equity trades.
   - Securities Transaction Tax (STT): 0.1% on delivery buy and sell turnover.
   - Exchange Transaction Charges (NSE): 0.00297% of turnover.
   - SEBI Turnover Fees: 0.0001% (Rs 10 per crore).
   - Stamp Duty: 0.015% on buy turnover only (Rs 0 on sell).
   - Depository Participant (DP) Charges: flat Rs 15.93 on delivery scrip sale (Codex Mandate 2).
   - Goods & Services Tax (GST): 18% on (Brokerage + Exchange Charges + SEBI Fees).
2. Realistic Slippage Modeling:
   - Base slippage: 7.5 bps per side on liquid F&O underlyings.
   - Gap stress slippage: 25.0 bps on gap-openings and circuit stress.
   - Adverse execution direction: Buy executed above benchmark; Sell executed below benchmark.
   - Buy limit orders cannot execute above their declared limit price (limit is ceiling).
3. Discrete Execution State Representation (AGENTS.md Rule 4):
   - Modeled states: QUEUED, FILLED, PARTIAL, LOCKED_NO_OFFER (UC), LOCKED_NO_BID (LC), REJECTED, EXPIRED.
4. Circuit Lock Handling (AGENTS.md Rules 3, 4, 5):
   - Locked Upper Circuit on buy attempt -> LOCKED_NO_OFFER (fill probability 0%). Never chase locked UC!
   - Locked Lower Circuit on exit attempt -> LOCKED_NO_BID (fill probability 0%). Position remains open and carried forward!
5. Gap Opening Mechanics:
   - Gap-up open on entry: execution at Open price + slippage (bounded by limit for limit orders).
   - Gap-down open past stop-loss: execution at Open price - slippage, actual loss exceeds 1R planned budget.
6. Liquidity & Market Participation Sizing Gate (Claude Rule 9):
   - Maximum 15% participation of session volume applied to BOTH entries and exits.
   - Zero-volume sessions result in 0 filled shares. Excess quantity results in PARTIAL fill.
7. Complete Trade Economics:
   - Realized net loss and R-multiple account for both entry and exit transaction friction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
import math
import numbers
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
import uuid


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET_ON_OPEN = "MARKET_ON_OPEN"
    PRE_OPEN = "PRE_OPEN"


class ExecutionState(str, Enum):
    QUEUED = "QUEUED"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    LOCKED_NO_OFFER = "LOCKED_NO_OFFER"
    LOCKED_NO_BID = "LOCKED_NO_BID"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


# Regulatory constants
BROKERAGE_DELIVERY = 0.00
STT_DELIVERY_RATE = 0.001          # 0.1% on delivery buy & sell
NSE_TURNOVER_RATE = 0.0000297      # 0.00297%
SEBI_TURNOVER_RATE = 0.000001      # 0.0001% (Rs 10 / Crore)
STAMP_DUTY_BUY_RATE = 0.00015      # 0.015% on buy turnover only
DP_CHARGE_FLAT_RS = 15.93          # Rs 15.93 flat per delivery scrip sale
GST_RATE = 0.18                    # 18% on taxable services


def calculate_statutory_costs(
    price: float,
    quantity: int,
    side: str | OrderSide = OrderSide.BUY,
    is_delivery: bool = True,
) -> Dict[str, float]:
    """
    Computes exact itemized regulatory and depository transaction costs for Indian cash delivery equity.
    Calibrated strictly to Zerodha delivery schedule:
      - Brokerage: Rs 0.00 (delivery equity).
      - STT: 0.1% on buy and sell turnover.
      - Exchange Turnover Charges: NSE 0.00297% of turnover.
      - SEBI Charges: Rs 10 / crore (0.0001% of turnover).
      - Stamp Duty: 0.015% on buy side only (0 on sell).
      - DP Charges: flat Rs 15.93 per delivery scrip sale (0 on buy).
      - GST: 18% on (Brokerage + Exchange Charges + SEBI Charges).
    """
    if (
        isinstance(price, bool)
        or not isinstance(price, numbers.Real)
        or not math.isfinite(float(price))
        or float(price) <= 0
    ):
        raise ValueError("FAIL-CLOSED: Price must be a finite positive number.")

    if (
        isinstance(quantity, bool)
        or not isinstance(quantity, numbers.Integral)
        or int(quantity) <= 0
    ):
        raise ValueError("FAIL-CLOSED: Quantity must be a positive integer.")

    price_f = round(float(price), 2)
    qty_i = int(quantity)
    side_str = str(side.value if isinstance(side, OrderSide) else side).upper()

    if side_str not in ("BUY", "SELL"):
        raise ValueError(f"FAIL-CLOSED: Invalid order side '{side_str}'. Must be BUY or SELL.")

    turnover = round(price_f * qty_i, 2)
    brokerage = BROKERAGE_DELIVERY

    # STT: 0.1% on delivery buy and sell
    stt = round(STT_DELIVERY_RATE * turnover, 2)

    # Exchange turnover charges (NSE: 0.00297%)
    exchange_charges = round(NSE_TURNOVER_RATE * turnover, 2)

    # SEBI turnover fees (Rs 10 / Crore = 0.0001%)
    sebi_charges = round(SEBI_TURNOVER_RATE * turnover, 2)

    # Stamp duty: 0.015% on buy side only
    stamp_duty = round(STAMP_DUTY_BUY_RATE * turnover, 2) if side_str == "BUY" else 0.00

    # DP charges: flat Rs 15.93 on delivery sell only
    dp_charges = DP_CHARGE_FLAT_RS if (side_str == "SELL" and is_delivery) else 0.00

    # GST: 18% on (Brokerage + Exchange Charges + SEBI Fees)
    taxable_services = round(brokerage + exchange_charges + sebi_charges, 2)
    gst = round(GST_RATE * taxable_services, 2)

    total_cost = round(
        brokerage + stt + exchange_charges + sebi_charges + stamp_duty + dp_charges + gst, 2
    )

    return {
        "turnover": turnover,
        "brokerage": brokerage,
        "stt": stt,
        "exchange_charges": exchange_charges,
        "sebi_charges": sebi_charges,
        "stamp_duty": stamp_duty,
        "dp_charges": dp_charges,
        "gst": gst,
        "total_cost": total_cost,
    }


# Backwards compatibility alias
calculate_transaction_costs = calculate_statutory_costs


@dataclass
class DailyBar:
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    upper_circuit: Optional[float] = None
    lower_circuit: Optional[float] = None
    is_upper_circuit_locked: bool = False
    is_lower_circuit_locked: bool = False


@dataclass
class EntryOrder:
    symbol: str
    side: OrderSide
    order_type: OrderType
    limit_price: float
    stop_price: float
    quantity: int
    order_id: Optional[str] = None
    timestamp: Optional[str] = None

    def __post_init__(self):
        if self.order_id is None:
            self.order_id = f"ORD_{uuid.uuid4().hex[:8].upper()}"


@dataclass
class SwingPosition:
    symbol: str
    shares: int
    entry_price: float
    stop_price: float
    sector: str
    entry_time: Optional[str] = None
    highest_price: Optional[float] = None
    entry_transaction_costs: float = 0.0

    def __post_init__(self):
        if self.highest_price is None:
            self.highest_price = self.entry_price


@dataclass
class ExecutionReport:
    order_id: str
    symbol: str
    side: OrderSide
    state: ExecutionState
    requested_quantity: int
    filled_quantity: int
    unfilled_quantity: int
    actual_fill_price: float
    fill_probability: float
    slippage_bps: float
    slippage_amount: float
    turnover: float
    transaction_costs: Dict[str, float]
    total_cost: float
    exit_transaction_costs: float = 0.0
    rejection_reason: Optional[str] = None
    is_gap_exit: bool = False
    position_remains_open: bool = False
    gross_realized_loss: float = 0.0
    net_realized_loss: float = 0.0
    realized_r_multiple: float = 0.0


class ExecutionFrictionEngine:
    """
    Applies itemized statutory transaction friction and adverse slippage to order fills.
    """

    def __init__(
        self,
        base_slippage_bps: float = 7.5,
        gap_stress_slippage_bps: float = 25.0,
        max_participation_rate: float = 0.15,
    ):
        self.base_slippage_bps = float(base_slippage_bps)
        self.gap_stress_slippage_bps = float(gap_stress_slippage_bps)
        self.max_participation_rate = float(max_participation_rate)

    def apply_slippage(
        self,
        price: float,
        side: OrderSide | str,
        is_gap: bool = False,
    ) -> float:
        """
        Calculates execution fill price after adverse slippage:
          - BUY: fill_price = price * (1 + slippage_rate)
          - SELL: fill_price = price * (1 - slippage_rate)
        """
        p = float(price)
        side_enum = OrderSide(side) if isinstance(side, str) else side
        slippage_bps = self.gap_stress_slippage_bps if is_gap else self.base_slippage_bps
        slippage_rate = slippage_bps / 10000.0

        if side_enum == OrderSide.BUY:
            return round(p * (1.0 + slippage_rate), 2)
        elif side_enum == OrderSide.SELL:
            return round(p * (1.0 - slippage_rate), 2)
        else:
            raise ValueError(f"Invalid order side: {side}")


class ExecutionSimulator(ExecutionFrictionEngine):
    """
    Multi-Day Swing Execution Reality Engine.
    Simulates realistic market order execution with circuit locks, gap openings,
    volume participation caps, and statutory friction.
    """

    def simulate_entry(
        self,
        order: EntryOrder,
        bar: DailyBar,
    ) -> ExecutionReport:
        """
        Simulates entry order execution against session bar:
        - Symbol and Side consistency validation.
        - Rule 2 Floor (< Rs 10.00 rejected immediately).
        - Inverted Stop check.
        - Rule 3 Locked UC check (fill probability 0%).
        - Gap-up open fills at Open price + slippage.
        - Buy limit orders cannot execute above limit price (ceiling).
        - Rule 9: 15% volume participation cap.
        """
        # Validate symbol consistency (Codex Finding 7)
        if order.symbol.strip().upper() != bar.symbol.strip().upper():
            return ExecutionReport(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                state=ExecutionState.REJECTED,
                requested_quantity=order.quantity,
                filled_quantity=0,
                unfilled_quantity=order.quantity,
                actual_fill_price=0.0,
                fill_probability=0.0,
                slippage_bps=0.0,
                slippage_amount=0.0,
                turnover=0.0,
                transaction_costs={},
                total_cost=0.0,
                rejection_reason=f"SYMBOL_MISMATCH: Order symbol '{order.symbol}' does not match bar symbol '{bar.symbol}'.",
            )

        # Validate order side (Codex Finding 7)
        if order.side != OrderSide.BUY:
            return ExecutionReport(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                state=ExecutionState.REJECTED,
                requested_quantity=order.quantity,
                filled_quantity=0,
                unfilled_quantity=order.quantity,
                actual_fill_price=0.0,
                fill_probability=0.0,
                slippage_bps=0.0,
                slippage_amount=0.0,
                turnover=0.0,
                transaction_costs={},
                total_cost=0.0,
                rejection_reason="INVALID_ENTRY_SIDE: Entry orders must be BUY.",
            )

        # Rule 2: Absolute Rs 10.00 price floor (exact, no rounding)
        if bar.open < 10.0 or order.limit_price < 10.0:
            return ExecutionReport(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                state=ExecutionState.REJECTED,
                requested_quantity=order.quantity,
                filled_quantity=0,
                unfilled_quantity=order.quantity,
                actual_fill_price=0.0,
                fill_probability=0.0,
                slippage_bps=0.0,
                slippage_amount=0.0,
                turnover=0.0,
                transaction_costs={},
                total_cost=0.0,
                rejection_reason="RULE_2_PRICE_FLOOR_VIOLATION: Sub-Rs 10 securities strictly disqualified.",
            )

        # Inverted Stop check
        if order.stop_price >= order.limit_price:
            return ExecutionReport(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                state=ExecutionState.REJECTED,
                requested_quantity=order.quantity,
                filled_quantity=0,
                unfilled_quantity=order.quantity,
                actual_fill_price=0.0,
                fill_probability=0.0,
                slippage_bps=0.0,
                slippage_amount=0.0,
                turnover=0.0,
                transaction_costs={},
                total_cost=0.0,
                rejection_reason="INVERTED_STOP: Stop price must be strictly below limit price.",
            )

        # Rule 3: Locked Upper Circuit check (No Offer)
        is_uc_locked = bar.is_upper_circuit_locked
        if not is_uc_locked and bar.upper_circuit is not None:
            if math.isclose(bar.open, bar.upper_circuit, abs_tol=1e-2) and math.isclose(bar.low, bar.upper_circuit, abs_tol=1e-2):
                is_uc_locked = True

        if is_uc_locked:
            return ExecutionReport(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                state=ExecutionState.LOCKED_NO_OFFER,
                requested_quantity=order.quantity,
                filled_quantity=0,
                unfilled_quantity=order.quantity,
                actual_fill_price=0.0,
                fill_probability=0.0,
                slippage_bps=0.0,
                slippage_amount=0.0,
                turnover=0.0,
                transaction_costs={},
                total_cost=0.0,
                rejection_reason="RULE_3_LOCKED_UPPER_CIRCUIT: Locked at Upper Circuit. Fill probability = 0%.",
            )

        # Price determination & Gap Openings
        is_gap = False
        if bar.open > order.limit_price:
            if order.order_type in (OrderType.MARKET_ON_OPEN, OrderType.PRE_OPEN):
                is_gap = True
                base_price = bar.open
            else:
                # Limit order cannot fill if opened above limit
                return ExecutionReport(
                    order_id=order.order_id,
                    symbol=order.symbol,
                    side=order.side,
                    state=ExecutionState.EXPIRED,
                    requested_quantity=order.quantity,
                    filled_quantity=0,
                    unfilled_quantity=order.quantity,
                    actual_fill_price=0.0,
                    fill_probability=0.0,
                    slippage_bps=0.0,
                    slippage_amount=0.0,
                    turnover=0.0,
                    transaction_costs={},
                    total_cost=0.0,
                    rejection_reason="LIMIT_PRICE_EXCEEDED: Open price opened above limit price.",
                )
        else:
            base_price = bar.open

        # Apply slippage
        actual_fill_price = self.apply_slippage(base_price, OrderSide.BUY, is_gap=is_gap)
        # Limit price is an inviolable ceiling for LIMIT orders (Codex Finding 7)
        if order.order_type == OrderType.LIMIT and actual_fill_price > order.limit_price:
            actual_fill_price = order.limit_price

        slippage_bps = self.gap_stress_slippage_bps if is_gap else self.base_slippage_bps
        slippage_amount = round(actual_fill_price - base_price, 2)

        # Claude Rule 9: Volume participation cap (15% max of daily volume)
        max_fillable = int(math.floor(self.max_participation_rate * bar.volume)) if bar.volume > 0 else 0
        if order.quantity > max_fillable:
            filled_qty = max_fillable
            unfilled_qty = order.quantity - filled_qty
            state = ExecutionState.PARTIAL if filled_qty > 0 else ExecutionState.REJECTED
        else:
            filled_qty = order.quantity
            unfilled_qty = 0
            state = ExecutionState.FILLED

        if filled_qty > 0:
            costs = calculate_statutory_costs(actual_fill_price, filled_qty, side="BUY", is_delivery=True)
            turnover = costs["turnover"]
            total_cost = costs["total_cost"]
            prob = 1.0 if state == ExecutionState.FILLED else (filled_qty / order.quantity)
        else:
            costs = {}
            turnover = 0.0
            total_cost = 0.0
            prob = 0.0

        return ExecutionReport(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            state=state,
            requested_quantity=order.quantity,
            filled_quantity=filled_qty,
            unfilled_quantity=unfilled_qty,
            actual_fill_price=actual_fill_price,
            fill_probability=prob,
            slippage_bps=slippage_bps,
            slippage_amount=slippage_amount,
            turnover=turnover,
            transaction_costs=costs,
            total_cost=total_cost,
        )

    def simulate_exit(
        self,
        position: SwingPosition,
        bar: DailyBar,
        trigger_reason: str = "STOP_LOSS",
        target_price: Optional[float] = None,
        entry_transaction_costs: float = 0.0,
    ) -> ExecutionReport:
        """
        Simulates exit order execution against session bar (Codex Findings 1, 8, Round 2):
        - Validates symbol consistency.
        - Verifies exit price touch (stop-loss touched only if bar.low <= stop_price or gap open;
          take-profit touched only if bar.high >= target_price or gap open).
        - Enforces session volume availability and Rule 9 participation cap on exits.
        - Rule 4 & 5 Lower Circuit check (LOCKED_NO_BID -> position carried forward).
        - Benchmarks execution against target_price (for TAKE_PROFIT) or stop_price (for STOP_LOSS).
        - Gap-down past stop price fills at Open price - slippage, actual loss exceeds 1R.
        - Calculates itemized friction including flat Rs 15.93 DP charge.
        - Calculates complete net PnL accounting for both entry and exit transaction friction.
        """
        # Validate symbol consistency
        if position.symbol.strip().upper() != bar.symbol.strip().upper():
            return ExecutionReport(
                order_id=f"EXIT_{uuid.uuid4().hex[:8].upper()}",
                symbol=position.symbol,
                side=OrderSide.SELL,
                state=ExecutionState.REJECTED,
                requested_quantity=position.shares,
                filled_quantity=0,
                unfilled_quantity=position.shares,
                actual_fill_price=0.0,
                fill_probability=0.0,
                slippage_bps=0.0,
                slippage_amount=0.0,
                turnover=0.0,
                transaction_costs={},
                total_cost=0.0,
                exit_transaction_costs=0.0,
                position_remains_open=True,
                rejection_reason=f"SYMBOL_MISMATCH: Position symbol '{position.symbol}' does not match bar symbol '{bar.symbol}'.",
            )

        # Exit trigger touch verification (Codex Findings 1, Round 2)
        if trigger_reason in ("STOP_LOSS", "STOP"):
            if bar.low > position.stop_price and bar.open >= position.stop_price:
                # Stop price was NEVER touched during the session!
                return ExecutionReport(
                    order_id=f"EXIT_{uuid.uuid4().hex[:8].upper()}",
                    symbol=position.symbol,
                    side=OrderSide.SELL,
                    state=ExecutionState.QUEUED,
                    requested_quantity=position.shares,
                    filled_quantity=0,
                    unfilled_quantity=position.shares,
                    actual_fill_price=0.0,
                    fill_probability=0.0,
                    slippage_bps=0.0,
                    slippage_amount=0.0,
                    turnover=0.0,
                    transaction_costs={},
                    total_cost=0.0,
                    exit_transaction_costs=0.0,
                    position_remains_open=True,
                    rejection_reason=f"STOP_NOT_TOUCHED: Session low ({bar.low}) stayed above stop price ({position.stop_price}).",
                )
        elif trigger_reason in ("TAKE_PROFIT", "TARGET"):
            if target_price is None:
                raise ValueError("FAIL-CLOSED: target_price must be provided when trigger_reason is TAKE_PROFIT or TARGET.")
            if bar.high < target_price and bar.open <= target_price:
                # Target price was NEVER reached during the session!
                return ExecutionReport(
                    order_id=f"EXIT_{uuid.uuid4().hex[:8].upper()}",
                    symbol=position.symbol,
                    side=OrderSide.SELL,
                    state=ExecutionState.QUEUED,
                    requested_quantity=position.shares,
                    filled_quantity=0,
                    unfilled_quantity=position.shares,
                    actual_fill_price=0.0,
                    fill_probability=0.0,
                    slippage_bps=0.0,
                    slippage_amount=0.0,
                    turnover=0.0,
                    transaction_costs={},
                    total_cost=0.0,
                    exit_transaction_costs=0.0,
                    position_remains_open=True,
                    rejection_reason=f"TARGET_NOT_TOUCHED: Session high ({bar.high}) stayed below target price ({target_price}).",
                )

        # Check zero-volume session (Codex Finding 1)
        if bar.volume <= 0:
            return ExecutionReport(
                order_id=f"EXIT_{uuid.uuid4().hex[:8].upper()}",
                symbol=position.symbol,
                side=OrderSide.SELL,
                state=ExecutionState.LOCKED_NO_BID,
                requested_quantity=position.shares,
                filled_quantity=0,
                unfilled_quantity=position.shares,
                actual_fill_price=0.0,
                fill_probability=0.0,
                slippage_bps=0.0,
                slippage_amount=0.0,
                turnover=0.0,
                transaction_costs={},
                total_cost=0.0,
                position_remains_open=True,
                rejection_reason="ZERO_SESSION_VOLUME: Session volume is zero. Liquidity unavailable. Position carried forward.",
            )

        # Rule 4 & 5: Locked Lower Circuit check (No Bid)
        is_lc_locked = bar.is_lower_circuit_locked
        if not is_lc_locked and bar.lower_circuit is not None:
            if math.isclose(bar.open, bar.lower_circuit, abs_tol=1e-2) and math.isclose(bar.high, bar.lower_circuit, abs_tol=1e-2):
                is_lc_locked = True

        if is_lc_locked:
            return ExecutionReport(
                order_id=f"EXIT_{uuid.uuid4().hex[:8].upper()}",
                symbol=position.symbol,
                side=OrderSide.SELL,
                state=ExecutionState.LOCKED_NO_BID,
                requested_quantity=position.shares,
                filled_quantity=0,
                unfilled_quantity=position.shares,
                actual_fill_price=0.0,
                fill_probability=0.0,
                slippage_bps=0.0,
                slippage_amount=0.0,
                turnover=0.0,
                transaction_costs={},
                total_cost=0.0,
                position_remains_open=True,
                rejection_reason="RULE_5_LOCKED_LOWER_CIRCUIT: Locked at Lower Circuit. Bid depth = 0. Position carried forward.",
            )

        # Claude Rule 9: Volume participation cap on exit (Codex Finding 1)
        max_fillable = int(math.floor(self.max_participation_rate * bar.volume))
        if position.shares > max_fillable:
            filled_qty = max_fillable
            unfilled_qty = position.shares - filled_qty
            state = ExecutionState.PARTIAL if filled_qty > 0 else ExecutionState.LOCKED_NO_BID
            remains_open = True
        else:
            filled_qty = position.shares
            unfilled_qty = 0
            state = ExecutionState.FILLED
            remains_open = False

        if filled_qty == 0:
            return ExecutionReport(
                order_id=f"EXIT_{uuid.uuid4().hex[:8].upper()}",
                symbol=position.symbol,
                side=OrderSide.SELL,
                state=ExecutionState.LOCKED_NO_BID,
                requested_quantity=position.shares,
                filled_quantity=0,
                unfilled_quantity=position.shares,
                actual_fill_price=0.0,
                fill_probability=0.0,
                slippage_bps=0.0,
                slippage_amount=0.0,
                turnover=0.0,
                transaction_costs={},
                total_cost=0.0,
                position_remains_open=True,
                rejection_reason="PARTICIPATION_LIMIT_ZERO_FILL: Session liquidity insufficient for executable participation.",
            )

        # Price determination & Gap exit check (Codex Finding 1, Round 2)
        is_gap = False
        if trigger_reason in ("TAKE_PROFIT", "TARGET") and target_price is not None:
            if bar.open > target_price:
                is_gap = True
                base_price = bar.open
            else:
                base_price = target_price
        elif trigger_reason in ("STOP_LOSS", "STOP"):
            if bar.open < position.stop_price:
                is_gap = True
                base_price = bar.open
            else:
                base_price = position.stop_price
        else:
            base_price = bar.close

        # Apply adverse slippage
        actual_fill_price = self.apply_slippage(base_price, OrderSide.SELL, is_gap=is_gap)
        slippage_bps = self.gap_stress_slippage_bps if is_gap else self.base_slippage_bps
        slippage_amount = round(base_price - actual_fill_price, 2)

        # Calculate costs on sale (includes flat Rs 15.93 DP charges)
        costs = calculate_statutory_costs(
            actual_fill_price, filled_qty, side="SELL", is_delivery=True
        )
        turnover = costs["turnover"]
        exit_total_cost = costs["total_cost"]

        # Calculate realized metrics including BOTH entry and exit friction (Codex Finding 8)
        entry_turnover = round(filled_qty * position.entry_price, 2)
        gross_realized_pnl = round(turnover - entry_turnover, 2)
        gross_loss = abs(gross_realized_pnl) if gross_realized_pnl < 0 else 0.0

        prop_entry_costs = round(
            float(entry_transaction_costs) * (filled_qty / position.shares), 2
        ) if position.shares > 0 else 0.0
        combined_total_cost = round(exit_total_cost + prop_entry_costs, 2)

        net_realized_pnl = round(gross_realized_pnl - combined_total_cost, 2)
        net_loss = abs(net_realized_pnl) if net_realized_pnl < 0 else 0.0

        planned_1r = round(filled_qty * (position.entry_price - position.stop_price), 2)
        r_multiple = round(net_realized_pnl / planned_1r, 3) if planned_1r > 0 else 0.0
        prob = 1.0 if state == ExecutionState.FILLED else (filled_qty / position.shares)

        return ExecutionReport(
            order_id=f"EXIT_{uuid.uuid4().hex[:8].upper()}",
            symbol=position.symbol,
            side=OrderSide.SELL,
            state=state,
            requested_quantity=position.shares,
            filled_quantity=filled_qty,
            unfilled_quantity=unfilled_qty,
            actual_fill_price=actual_fill_price,
            fill_probability=prob,
            slippage_bps=slippage_bps,
            slippage_amount=slippage_amount,
            turnover=turnover,
            transaction_costs=costs,
            total_cost=combined_total_cost,
            exit_transaction_costs=exit_total_cost,
            is_gap_exit=is_gap,
            position_remains_open=remains_open,
            gross_realized_loss=gross_loss,
            net_realized_loss=net_loss,
            realized_r_multiple=r_multiple,
        )
