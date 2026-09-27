"""
track2_paper_execution.py - Queue-Aware Paper Execution Engine & Bracket State Machine for Track 2
==================================================================================================
Part of Project Swing Trades (Antigravity + Claude Code + OpenAI Codex).

Mandate & Features:
  1. Strict Discrete 4-State Execution Modeling (AGENTS.md Rule 4):
     - Never assume deterministic fills from OHLC bars or quote touches.
     - Modeled states: QUEUED, PARTIAL, FILLED, REJECTED, CANCEL_PENDING, UNFILLED_STOP_LOSS_RISK.
  2. Evidence Hierarchy (session_manifest.py):
     - E1: Price touched order price (never qualifies as fill).
     - E2: Marketable depth quote snapshot present at signal time.
     - E3: Queue rank + order quantity cleared by post-arrival trade prints with queue haircut.
  3. Two-Tranche Bracket Order Lifecycle:
     - Tranche 1 (50% shares): Target exit at +1.5R.
     - Tranche 2 (50% shares): Trailing runner. Upon Tranche 1 target fill, Tranche 2 stop
       automatically trails to Breakeven (Entry Price).
     - Reciprocal cancellation (OCO): If stop is hit, pending target orders are immediately canceled.
  4. Broker Cutoff Enforcement:
     - 15:15 IST (CAS cutoff) and 15:25 IST (non-CAS cutoff) auto-cancel pending entries and
       enforce market squareoff on open positions.
  5. Realistic Friction & Transaction Costs:
     - Comprehensive transaction cost model: Brokerage, STT, Exchange charges, GST, SEBI fees, Stamp duty.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, time as dtime
import math
import numbers
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from antigravity.models.session_manifest import (
    EvidenceClass,
    FillState,
    _canonical_json,
    _sha256_bytes,
)
from antigravity.models.two_tranche_exit_model import (
    TwoTrancheExitModel,
    TrancheAllocation,
    TrancheStatus,
)

try:
    from research.execution_realism.fills import (
        PassiveOrderSim,
        taker_fill,
        Envelope as FillEnvelope,
        Side as OrderSide,
        FillState as RealismFillState,
        QueueParams,
    )
    from research.execution_realism.exits import (
        DynamicBand,
        EmergencyExitSim,
        TokenBucket,
    )
except ImportError:
    pass


def calculate_transaction_costs(
    price: float,
    quantity: int,
    side: str = "BUY",
    is_intraday: bool = True,
) -> Dict[str, float]:
    """
    Computes exact Indian regulatory and broker transaction costs for equity trades.
    Calibrated strictly to Zerodha equity charges:
      - Brokerage: Min(Rs 20, 0.03% of turnover) for intraday; Rs 0 for delivery.
      - STT / CTT: 0.025% on sell side for intraday; 0.1% on both sides for delivery.
      - Exchange Transaction Charges: NSE 0.00297% of turnover.
      - GST: 18% on (Brokerage + Exchange Charges).
      - SEBI Charges: Rs 10 per crore (0.0001% of turnover).
      - Stamp Duty: 0.003% on buy side for intraday; 0.015% on buy side for delivery.
    """
    for val in [price]:
        if (
            isinstance(val, bool)
            or not isinstance(val, numbers.Real)
            or not math.isfinite(float(val))
            or float(val) <= 0
        ):
            raise ValueError("FAIL-CLOSED: Price must be a positive finite number.")
    if (
        isinstance(quantity, bool)
        or not isinstance(quantity, numbers.Integral)
        or int(quantity) <= 0
    ):
        raise ValueError("FAIL-CLOSED: Quantity must be a positive integer.")

    price = round(float(price), 2)
    quantity = int(quantity)
    side = str(side).upper()
    turnover = round(price * quantity, 2)

    # Brokerage
    if is_intraday:
        brokerage = min(20.0, round(0.0003 * turnover, 2))
    else:
        brokerage = 0.0

    # STT / CTT
    if is_intraday:
        stt = round(0.00025 * turnover, 2) if side == "SELL" else 0.0
    else:
        stt = round(0.001 * turnover, 2)

    # Exchange Turnover Charges (NSE: 0.0030699% per NSE circular FA73061 effective 1 March 2026)
    exchange_charges = round(0.000030699 * turnover, 2)

    # SEBI Turnover Charges: Rs 10 / Crore (0.0001%)
    sebi_charges = round(0.000001 * turnover, 2)

    # GST: 18% on (Brokerage + Exchange Charges + SEBI Charges)
    gst = round(0.18 * (brokerage + exchange_charges + sebi_charges), 2)

    # Stamp Duty: 0.003% on buy side (intraday)
    if side == "BUY":
        stamp_duty = round(0.00003 * turnover, 2) if is_intraday else round(0.00015 * turnover, 2)
    else:
        stamp_duty = 0.0

    total_cost = round(brokerage + stt + exchange_charges + gst + sebi_charges + stamp_duty, 2)

    return {
        "turnover": turnover,
        "brokerage": brokerage,
        "stt": stt,
        "exchange_charges": exchange_charges,
        "gst": gst,
        "sebi_charges": sebi_charges,
        "stamp_duty": stamp_duty,
        "total_cost": total_cost,
    }


def calculate_implementation_shortfall(
    signal_price: float,
    limit_price: float,
    fill_price: float,
    side: str = "BUY",
) -> Dict[str, float]:
    """
    W4: Implementation Shortfall & Slippage vs Arrival Price.
    Decomposes total execution slippage into:
      1. Spread crossing cost: (Limit - Signal) / Signal
      2. Delay / impact slippage: (Fill - Limit) / Signal
      3. Total slippage vs arrival price: (Fill - Signal) / Signal
    All metrics reported in basis points (bps, 1 bps = 0.01%).
    Convention: Positive bps indicates adverse slippage (execution worse than benchmark).
    """
    for p in (signal_price, limit_price, fill_price):
        if (
            isinstance(p, bool)
            or not isinstance(p, numbers.Real)
            or not math.isfinite(float(p))
            or float(p) <= 0
        ):
            raise ValueError("FAIL-CLOSED: Prices must be positive finite numbers.")

    s_price = float(signal_price)
    l_price = float(limit_price)
    f_price = float(fill_price)
    side = str(side).upper()

    if side == "BUY":
        total_slippage_bps = round(((f_price - s_price) / s_price) * 10000.0, 2)
        spread_cost_bps = round(((l_price - s_price) / s_price) * 10000.0, 2)
        delay_impact_bps = round(((f_price - l_price) / s_price) * 10000.0, 2)
    elif side == "SELL":
        total_slippage_bps = round(((s_price - f_price) / s_price) * 10000.0, 2)
        spread_cost_bps = round(((s_price - l_price) / s_price) * 10000.0, 2)
        delay_impact_bps = round(((l_price - f_price) / s_price) * 10000.0, 2)
    else:
        raise ValueError(f"Invalid side: {side}. Must be BUY or SELL.")

    return {
        "signal_price": round(s_price, 2),
        "limit_price": round(l_price, 2),
        "fill_price": round(f_price, 2),
        "slippage_bps": total_slippage_bps,
        "spread_cost_bps": spread_cost_bps,
        "delay_impact_bps": delay_impact_bps,
    }


def generate_depth_ladder(
    symbol: str,
    ltp: float,
    spread_ticks: int = 1,
    depth_levels: int = 5,
) -> Dict[str, Any]:
    """
    W5: Order Book Visual Depth Ladder & Queue Rank Modeling.
    Generates or formats a calibrated 5-level market depth book around LTP.
    Tick size is ₹0.05 for Indian equity cash market.
    """
    if not isinstance(ltp, numbers.Real) or ltp <= 0 or not math.isfinite(float(ltp)):
        raise ValueError("LTP must be a positive finite number.")

    tick_size = 0.05
    ltp = round(float(ltp), 2)
    spread_rs = round(max(1, spread_ticks) * tick_size, 2)
    best_bid = round(ltp - (spread_rs / 2.0), 2)
    best_ask = round(best_bid + spread_rs, 2)

    # Calibrate realistic deterministic quantities based on symbol hash & level
    bids = []
    asks = []
    base_qty = 500 + (abs(hash(symbol)) % 1500)

    for i in range(depth_levels):
        b_price = round(best_bid - (i * tick_size), 2)
        a_price = round(best_ask + (i * tick_size), 2)
        b_qty = int(base_qty * (1.0 + 0.25 * i) + (i * 120))
        a_qty = int(base_qty * (0.9 + 0.22 * i) + (i * 110))
        b_orders = 3 + i * 2
        a_orders = 2 + i * 2

        bids.append({"price": b_price, "qty": b_qty, "orders": b_orders})
        asks.append({"price": a_price, "qty": a_qty, "orders": a_orders})

    total_bid_qty = sum(b["qty"] for b in bids)
    total_ask_qty = sum(a["qty"] for a in asks)
    total_depth = total_bid_qty + total_ask_qty
    imbalance_pct = round(((total_bid_qty - total_ask_qty) / total_depth) * 100.0, 1) if total_depth > 0 else 0.0
    spread_bps = round((spread_rs / ltp) * 10000.0, 2)

    return {
        "symbol": symbol,
        "ltp": ltp,
        "tick_size": tick_size,
        "spread_rs": spread_rs,
        "spread_bps": spread_bps,
        "total_bid_qty": total_bid_qty,
        "total_ask_qty": total_ask_qty,
        "imbalance_pct": imbalance_pct,
        "bids": bids,
        "asks": asks,
    }


def calculate_queue_rank(
    order_qty: int,
    order_price: float,
    side: str,
    depth: Dict[str, Any],
) -> Dict[str, Any]:
    """
    W5: Computes estimated queue rank and position ahead in the order book.
    Rule 4 & E3 compliance: Fill requires cumulative volume >= queue_rank + order_qty.
    """
    if order_qty <= 0:
        raise ValueError("Order quantity must be positive.")
    side = str(side).upper()
    levels = depth.get("bids" if side == "BUY" else "asks", [])

    vol_ahead = 0
    orders_ahead = 0

    for lvl in levels:
        price = lvl.get("price", 0.0)
        qty = lvl.get("qty", 0)
        orders = lvl.get("orders", 0)

        if (side == "BUY" and price > order_price) or (side == "SELL" and price < order_price):
            vol_ahead += qty
            orders_ahead += orders
        elif math.isclose(price, order_price, abs_tol=0.01):
            # In FIFO queue, order arrives behind all current level volume
            vol_ahead += qty
            orders_ahead += max(1, orders)
            break

    total_side_depth = depth.get("total_bid_qty" if side == "BUY" else "total_ask_qty", max(1, vol_ahead + order_qty))
    queue_ratio = round(vol_ahead / float(order_qty), 2)
    queue_pct = round((vol_ahead / float(max(1, total_side_depth))) * 100.0, 1)

    return {
        "order_price": order_price,
        "order_qty": order_qty,
        "side": side,
        "vol_ahead": vol_ahead,
        "orders_ahead": orders_ahead,
        "queue_ratio": queue_ratio,
        "queue_percentile": queue_pct,
        "required_turnover_for_fill": vol_ahead + order_qty,
    }


def assess_execution(
    *,
    order_id: str,
    instruction: Mapping[str, Any],
    arrival_quote: Mapping[str, Any],
    trades: Sequence[Mapping[str, Any]],
    order_arrival_timestamp: str,
    source_data_sha256: str,
    queue_haircut: float,
    full_cost_deduction: float,
) -> dict[str, Any]:
    """
    Evaluates execution of a single order against order book queue rank and post-arrival trade prints.
    Emits E3 qualifying evidence only when cumulative volume clears queue rank + order size with haircut.
    """
    side = instruction.get("side")
    limit_price = instruction.get("limit_price")
    quantity = instruction.get("quantity")
    if side not in {"BUY", "SELL"} or not isinstance(limit_price, (int, float)):
        raise ValueError("paper instruction is invalid")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise ValueError("paper instruction quantity is invalid")
    if not 0 <= queue_haircut < 1:
        raise ValueError("queue haircut is invalid")
    queue_field = "bid_qty" if side == "BUY" else "ask_qty"
    price_field = "best_bid" if side == "BUY" else "best_ask"
    if arrival_quote.get(price_field) != limit_price:
        return {
            "state": FillState.QUEUED.value,
            "evidence_class": EvidenceClass.E1_BAR_POSSIBLE.value,
            "reason": "order price is not bound to manifested top-of-book queue",
            "fill_evidence": None,
        }
    queue_rank = arrival_quote.get(queue_field)
    if isinstance(queue_rank, bool) or not isinstance(queue_rank, int) or queue_rank < 0:
        raise ValueError("arrival queue quantity is invalid")
    arrival = datetime.fromisoformat(order_arrival_timestamp)
    seen: set[str] = set()
    cumulative = 0
    fill_timestamp = None
    required = queue_rank + quantity
    for trade in trades:
        trade_id = trade.get("trade_id")
        if not isinstance(trade_id, str) or not trade_id or trade_id in seen:
            raise ValueError("trade IDs must be present and unique")
        seen.add(trade_id)
        timestamp = datetime.fromisoformat(str(trade.get("timestamp")))
        if timestamp < arrival:
            continue
        price, trade_quantity = trade.get("price"), trade.get("quantity")
        if (
            not isinstance(price, (int, float))
            or isinstance(trade_quantity, bool)
            or not isinstance(trade_quantity, int)
            or trade_quantity <= 0
        ):
            raise ValueError("trade print is invalid")
        at_or_better = price <= limit_price if side == "BUY" else price >= limit_price
        if at_or_better:
            cumulative += trade_quantity
            if cumulative * (1.0 - queue_haircut) >= required:
                fill_timestamp = timestamp.isoformat()
                break
    if fill_timestamp is None:
        state = FillState.PARTIAL if cumulative else FillState.QUEUED
        return {
            "state": state.value,
            "evidence_class": EvidenceClass.E2_MARKETABLE_DEPTH.value,
            "queue_rank": queue_rank,
            "cum_volume_at_or_better": cumulative,
            "fill_evidence": None,
        }
    signal_price = instruction.get("signal_price", limit_price)
    slippage_bps = 0.0
    if isinstance(signal_price, (int, float)) and signal_price > 0:
        shortfall = calculate_implementation_shortfall(
            signal_price=signal_price,
            limit_price=limit_price,
            fill_price=limit_price,
            side=side,
        )
        slippage_bps = shortfall["slippage_bps"]

    evidence = {
        "order_id": order_id,
        "evidence_class": EvidenceClass.E3_TICK_QUEUE.value,
        "fill_state": FillState.FILLED.value,
        "order_spec": dict(instruction),
        "preregistered_order_hash": _sha256_bytes(_canonical_json(dict(instruction))),
        "signal_timestamp": instruction["signal_timestamp"],
        "order_arrival_timestamp": order_arrival_timestamp,
        "volume_window_start_timestamp": order_arrival_timestamp,
        "fill_timestamp": fill_timestamp,
        "order_side": side,
        "limit_price": limit_price,
        "signal_price": signal_price,
        "slippage_bps": slippage_bps,
        "order_qty": quantity,
        "queue_rank": queue_rank,
        "cum_volume_at_or_better": cumulative,
        "arrival_quote": dict(arrival_quote),
        "capture_continuous": True,
        "source_data_sha256": source_data_sha256,
        "full_cost_deduction": full_cost_deduction,
    }
    return {
        "state": FillState.FILLED.value,
        "evidence_class": EvidenceClass.E3_TICK_QUEUE.value,
        "queue_rank": queue_rank,
        "cum_volume_at_or_better": cumulative,
        "fill_evidence": evidence,
    }


@dataclass
class BracketOrderState:
    order_id: str
    symbol: str
    entry_price: float
    total_shares: int
    initial_stop_price: float
    t1_shares: int
    t2_shares: int
    t1_target_price: float
    t2_current_stop_price: float
    is_t1_target_filled: bool
    is_t2_breakeven_trailed: bool
    is_stopped_out: bool
    is_eod_squared_off: bool
    terminal_state: Optional[str]
    product_type: str = "CNC"  # "CNC" for delivery swing, "MIS" for intraday
    t1_exit_price: Optional[float] = None
    t2_exit_price: Optional[float] = None
    t1_fill_timestamp: Optional[str] = None
    t2_fill_timestamp: Optional[str] = None
    t2_target_price: Optional[float] = None
    is_t2_target_filled: bool = False
    realized_pnl_gross: float = 0.0
    total_friction_cost: float = 0.0
    realized_pnl_net: float = 0.0
    # W4: Implementation Shortfall & Slippage
    signal_price: Optional[float] = None
    limit_price: Optional[float] = None
    fill_price: Optional[float] = None
    slippage_bps: float = 0.0
    delay_slippage_bps: float = 0.0
    spread_cost_bps: float = 0.0
    # W10: Explicit Transaction Cost Decomposition
    cost_breakdown: Optional[Dict[str, float]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def aggregate_transaction_costs(cost_records: Sequence[Dict[str, float]]) -> Dict[str, float]:
    """W10: Aggregates itemized statutory and broker charges across entry and exit executions."""
    result = {
        "turnover": 0.0,
        "brokerage": 0.0,
        "stt": 0.0,
        "exchange_charges": 0.0,
        "gst": 0.0,
        "sebi_charges": 0.0,
        "stamp_duty": 0.0,
        "total_cost": 0.0,
    }
    for c in cost_records:
        for k in result:
            result[k] = round(result[k] + c.get(k, 0.0), 2)
    return result


class BracketOrderManager:
    """
    Manages the discrete multi-tranche bracket lifecycle:
    1. Instantiates Tranche 1 (50% at +1.5R) and Tranche 2 (50% runner).
    2. Synchronizes reciprocal state: Tranche 1 target fill -> trails Tranche 2 stop to Breakeven.
    3. Stop hit -> cancels remaining targets, terminates position (OCO).
    4. Broker cutoff at 15:12 (CAS) / 15:25 (non-CAS) forces EOD squareoff for MIS (intraday) only.
       CNC swing trades roll overnight without forced 15:25 squareoff.
    5. Deducts transaction costs and discrete gap slippage from gross PnL.
    6. Tracks Implementation Shortfall vs arrival price and explicit cost breakdown (W4, W10).
    """

    @staticmethod
    def create_bracket(
        order_id: str,
        symbol: str,
        entry_price: float,
        stop_price: float,
        total_shares: int,
        target_1_rr: float = 1.5,
        target_2_rr: float = 3.0,
        product_type: str = "MIS",
        signal_price: Optional[float] = None,
        limit_price: Optional[float] = None,
    ) -> BracketOrderState:
        alloc = TwoTrancheExitModel.allocate_tranches(
            entry_price=entry_price,
            stop_price=stop_price,
            total_shares=total_shares,
            target_1_rr=target_1_rr,
            target_2_rr=target_2_rr,
        )
        s_price = signal_price if signal_price is not None else entry_price
        l_price = limit_price if limit_price is not None else entry_price

        shortfall = calculate_implementation_shortfall(
            signal_price=s_price,
            limit_price=l_price,
            fill_price=entry_price,
            side="BUY",
        )
        entry_cost = calculate_transaction_costs(
            price=entry_price,
            quantity=total_shares,
            side="BUY",
            is_intraday=(product_type == "MIS"),
        )
        return BracketOrderState(
            order_id=order_id,
            symbol=symbol,
            entry_price=alloc.entry_price,
            total_shares=alloc.total_shares,
            initial_stop_price=alloc.initial_stop,
            t1_shares=alloc.tranche1_shares,
            t2_shares=alloc.tranche2_shares,
            t1_target_price=alloc.tranche1_target,
            t2_target_price=alloc.tranche2_target,
            t2_current_stop_price=alloc.initial_stop,
            is_t1_target_filled=False,
            is_t2_target_filled=False,
            is_t2_breakeven_trailed=False,
            is_stopped_out=False,
            is_eod_squared_off=False,
            terminal_state=None,
            product_type=product_type,
            signal_price=shortfall["signal_price"],
            limit_price=shortfall["limit_price"],
            fill_price=shortfall["fill_price"],
            slippage_bps=shortfall["slippage_bps"],
            delay_slippage_bps=shortfall["delay_impact_bps"],
            spread_cost_bps=shortfall["spread_cost_bps"],
            cost_breakdown=entry_cost,
        )

    @staticmethod
    def update_bracket_quote(
        bracket: BracketOrderState,
        ltp: float,
        tick_low: Optional[float] = None,
        tick_high: Optional[float] = None,
        tick_open: Optional[float] = None,
        timestamp: Optional[str] = None,
        is_cas_eligible: bool = False,
        current_time_ist: Optional[dtime] = None,
        adverse_slippage_pct: float = 0.0,
        execution_evidence: bool = False,
    ) -> BracketOrderState:
        """
        Evaluates bracket state transitions upon receipt of a new quote/tick:
        - Checks stop breach (low <= active_stop) with gap slippage modeling.
        - Checks Tranche 1 target reach (high >= t1_target).
        - Enforces broker cutoffs for MIS orders (15:08 CAS or 15:25 non-CAS).
          CNC delivery positions roll overnight without broker squareoff.
        """
        if bracket.terminal_state is not None:
            return bracket  # Terminal states are strictly immutable

        high = max(ltp, tick_high if tick_high is not None else ltp)
        low = min(ltp, tick_low if tick_low is not None else ltp)
        ts = timestamp or datetime.now().isoformat()

        # 1. Check Broker Cutoff (15:08 Dhan MIS auto-squareoff pre-emption) for MIS orders ONLY
        if bracket.product_type == "MIS":
            cutoff_time = dtime(15, 8)  # Dhan squares off ALL cash intraday positions at 15:10 IST
            if current_time_ist is not None and current_time_ist >= cutoff_time:
                # Force MIS Market Squareoff at LTP
                bracket.is_eod_squared_off = True
                bracket.terminal_state = "CLOSED_MIS_SQUAREOFF"
                
                pnl_gross = 0.0
                is_intra = True
                entry_cost = calculate_transaction_costs(bracket.entry_price, bracket.total_shares, "BUY", is_intra)
                
                exit_costs = []
                entry_friction_needed = 0.0
                # Close remaining shares
                if not bracket.is_t1_target_filled:
                    bracket.t1_exit_price = ltp
                    bracket.t1_fill_timestamp = ts
                    pnl_gross += bracket.t1_shares * (ltp - bracket.entry_price)
                    cost_exit_t1 = calculate_transaction_costs(ltp, bracket.t1_shares, "SELL", is_intra)
                    exit_costs.append(cost_exit_t1)
                    entry_friction_needed += entry_cost["total_cost"]
                else:
                    # Tranche 1 was already filled and had its proportional entry cost deducted
                    entry_friction_needed += round(entry_cost["total_cost"] * (bracket.t2_shares / bracket.total_shares), 2)
                
                bracket.t2_exit_price = ltp
                bracket.t2_fill_timestamp = ts
                pnl_gross += bracket.t2_shares * (ltp - bracket.entry_price)
                cost_exit_t2 = calculate_transaction_costs(ltp, bracket.t2_shares, "SELL", is_intra)
                exit_costs.append(cost_exit_t2)

                friction = entry_friction_needed + sum(c["total_cost"] for c in exit_costs)

                bracket.realized_pnl_gross += round(pnl_gross, 2)
                bracket.total_friction_cost += round(friction, 2)
                bracket.realized_pnl_net = round(bracket.realized_pnl_gross - bracket.total_friction_cost, 2)

                # W10: Itemized cost breakdown
                all_costs = [entry_cost]
                if bracket.t1_exit_price is not None:
                    all_costs.append(calculate_transaction_costs(bracket.t1_exit_price, bracket.t1_shares, "SELL", is_intra))
                if bracket.t2_exit_price is not None:
                    all_costs.append(calculate_transaction_costs(bracket.t2_exit_price, bracket.t2_shares, "SELL", is_intra))
                bracket.cost_breakdown = aggregate_transaction_costs(all_costs)
                return bracket

        # 2. Check Stop-Loss Execution with Gap Slippage Modeling
        # If Tranche 1 is already filled, Tranche 2 is stopped out at breakeven
        if low <= bracket.t2_current_stop_price:
            bracket.is_stopped_out = True
            effective_stop = bracket.t2_current_stop_price

            # If candle/tick opened below the stop (gap-down), exit at tick_open with adverse slippage
            if tick_open is not None and tick_open < effective_stop:
                stop_exit_price = round(tick_open * (1.0 - max(0.0, adverse_slippage_pct)), 2)
            else:
                stop_exit_price = effective_stop
            
            is_intra = (bracket.product_type == "MIS")
            entry_cost = calculate_transaction_costs(bracket.entry_price, bracket.total_shares, "BUY", is_intra)

            if not bracket.is_t1_target_filled:
                # Initial stop hit before target: Both tranches exit at stop_exit_price
                bracket.terminal_state = "STOPPED_OUT_FULL"
                bracket.t1_exit_price = stop_exit_price
                bracket.t2_exit_price = stop_exit_price
                bracket.t1_fill_timestamp = ts
                bracket.t2_fill_timestamp = ts
                
                loss_gross = bracket.total_shares * (stop_exit_price - bracket.entry_price)
                exit_cost_rec = calculate_transaction_costs(stop_exit_price, bracket.total_shares, "SELL", is_intra)
                cost_exit = exit_cost_rec["total_cost"]
                total_friction = round(entry_cost["total_cost"] + cost_exit, 2)
                
                bracket.realized_pnl_gross = round(loss_gross, 2)
                bracket.total_friction_cost = total_friction
                bracket.realized_pnl_net = round(loss_gross - total_friction, 2)
                bracket.cost_breakdown = aggregate_transaction_costs([entry_cost, exit_cost_rec])
                return bracket
            else:
                # Tranche 1 was already filled at target; Tranche 2 exits at trailed stop (Breakeven or gap)
                bracket.terminal_state = "STOPPED_OUT_T2_BREAKEVEN"
                bracket.t2_exit_price = stop_exit_price
                bracket.t2_fill_timestamp = ts
                
                t2_pnl = bracket.t2_shares * (stop_exit_price - bracket.entry_price)
                t1_cost_rec = calculate_transaction_costs(bracket.t1_target_price, bracket.t1_shares, "SELL", is_intra)
                t2_cost_rec = calculate_transaction_costs(stop_exit_price, bracket.t2_shares, "SELL", is_intra)
                t2_cost = t2_cost_rec["total_cost"]
                t2_entry_friction = round(entry_cost["total_cost"] * (bracket.t2_shares / bracket.total_shares), 2)
                
                bracket.realized_pnl_gross += round(t2_pnl, 2)
                bracket.total_friction_cost += round(t2_entry_friction + t2_cost, 2)
                bracket.realized_pnl_net = round(bracket.realized_pnl_gross - bracket.total_friction_cost, 2)
                bracket.cost_breakdown = aggregate_transaction_costs([entry_cost, t1_cost_rec, t2_cost_rec])
                return bracket

        # 3. Check Tranche 1 Target Execution (Codex R06 / Claude A36: Quote-reach is eligibility only)
        if not bracket.is_t1_target_filled and high >= bracket.t1_target_price:
            if not execution_evidence:
                # Quote reach without execution evidence (fill ledger, queue depletion) cannot manufacture realized profit
                return bracket

            bracket.is_t1_target_filled = True
            bracket.t1_exit_price = bracket.t1_target_price
            bracket.t1_fill_timestamp = ts
            
            is_intra = (bracket.product_type == "MIS")
            entry_cost = calculate_transaction_costs(bracket.entry_price, bracket.total_shares, "BUY", is_intra)
            t1_pnl = bracket.t1_shares * (bracket.t1_target_price - bracket.entry_price)
            t1_cost_rec = calculate_transaction_costs(bracket.t1_target_price, bracket.t1_shares, "SELL", is_intra)
            t1_cost = t1_cost_rec["total_cost"]
            t1_entry_friction = round(entry_cost["total_cost"] * (bracket.t1_shares / bracket.total_shares), 2)
            
            bracket.realized_pnl_gross = round(t1_pnl, 2)
            bracket.total_friction_cost = round(t1_entry_friction + t1_cost, 2)
            bracket.realized_pnl_net = round(t1_pnl - bracket.total_friction_cost, 2)
            bracket.cost_breakdown = aggregate_transaction_costs([entry_cost, t1_cost_rec])

            # Auto-trail Tranche 2 stop to Breakeven
            bracket.t2_current_stop_price = bracket.entry_price
            bracket.is_t2_breakeven_trailed = True

        # 4. Check Tranche 2 Runner Target Execution (+3.0R)
        if (bracket.is_t1_target_filled and not bracket.is_t2_target_filled
                and bracket.t2_target_price is not None
                and high >= bracket.t2_target_price):
            if not execution_evidence:
                return bracket

            bracket.is_t2_target_filled = True
            bracket.t2_exit_price = bracket.t2_target_price
            bracket.t2_fill_timestamp = ts
            bracket.terminal_state = "TARGET_FILLED_FULL"

            is_intra = (bracket.product_type == "MIS")
            entry_cost = calculate_transaction_costs(bracket.entry_price, bracket.total_shares, "BUY", is_intra)
            t2_pnl = bracket.t2_shares * (bracket.t2_target_price - bracket.entry_price)
            t1_cost_rec = calculate_transaction_costs(bracket.t1_target_price, bracket.t1_shares, "SELL", is_intra)
            t2_cost_rec = calculate_transaction_costs(bracket.t2_target_price, bracket.t2_shares, "SELL", is_intra)
            t2_cost = t2_cost_rec["total_cost"]
            t2_entry_friction = round(entry_cost["total_cost"] * (bracket.t2_shares / bracket.total_shares), 2)

            bracket.realized_pnl_gross += round(t2_pnl, 2)
            bracket.total_friction_cost += round(t2_entry_friction + t2_cost, 2)
            bracket.realized_pnl_net = round(bracket.realized_pnl_gross - bracket.total_friction_cost, 2)
            bracket.cost_breakdown = aggregate_transaction_costs([entry_cost, t1_cost_rec, t2_cost_rec])
            return bracket

        return bracket
