"""
two_tranche_exit_model.py - Two-Tranche Trailing Exit & Scaling Model for Track 2
Part of Project Swing Trades (Antigravity + Claude Code + OpenAI Codex).

Quantitative Purpose:
- Eliminates premature trade churn caused by moving the entire position to breakeven at +1R.
- Tranche 1 (50% qty): Emits research exit intents; profit requires fill evidence.
- Tranche 2 (50% qty): Research runner intent; no trailing/fill assumption in Phase 1.
- Fail-Closed: Strict validation against negative shares, inverted stops, or NaN metrics.
"""

from dataclasses import dataclass, asdict
from enum import Enum
import math
import numbers
from typing import Optional, Dict, Any, Tuple


class TrancheStatus(Enum):
    PENDING_ENTRY = "PENDING_ENTRY"
    PENDING_TARGET_EXIT = "PENDING_TARGET_EXIT"
    PENDING_STOP_EXIT = "PENDING_STOP_EXIT"
    PENDING_EOD_SQUAREOFF = "PENDING_EOD_SQUAREOFF"
    ACTIVE_INITIAL_STOP = "ACTIVE_INITIAL_STOP"
    TRAILED_BREAKEVEN = "TRAILED_BREAKEVEN"
    TARGET_FILLED = "TARGET_FILLED"
    STOPPED_OUT = "STOPPED_OUT"
    SWING_TRAILING = "SWING_TRAILING"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"
    CANCEL_PENDING = "CANCEL_PENDING"
    UNFILLED_TRIGGERED = "UNFILLED_TRIGGERED"
    CLOSED_MIS_SQUAREOFF = "CLOSED_MIS_SQUAREOFF"


TERMINAL_TRANCHE_STATES = {
    TrancheStatus.TARGET_FILLED,
    TrancheStatus.STOPPED_OUT,
    TrancheStatus.CLOSED_MIS_SQUAREOFF,
    TrancheStatus.REJECTED
}


PENDING_TRANCHE_STATES = {
    TrancheStatus.PENDING_TARGET_EXIT, TrancheStatus.PENDING_STOP_EXIT,
    TrancheStatus.PENDING_EOD_SQUAREOFF, TrancheStatus.UNFILLED_TRIGGERED,
    TrancheStatus.CANCEL_PENDING,
}
RESEARCH_STATES = PENDING_TRANCHE_STATES | {
    TrancheStatus.ACTIVE_INITIAL_STOP, TrancheStatus.PENDING_ENTRY,
}


@dataclass
class TrancheAllocation:
    total_shares: int
    entry_price: float
    initial_stop: float
    risk_per_share: float
    tranche1_shares: int
    tranche2_shares: int
    tranche1_target: float
    tranche1_rr: float
    notional_value: float
    total_rupee_risk: float


@dataclass
class TwoTrancheState:
    symbol: str
    entry_price: float
    ltp: float
    peak_price: float
    
    # Tranche 1
    t1_shares: int
    t1_status: TrancheStatus
    t1_active_sl: float
    t1_target: float
    t1_realized_pnl: float
    t1_unrealized_pnl: float
    
    # Tranche 2
    t2_shares: int
    t2_status: TrancheStatus
    t2_active_sl: float
    t2_realized_pnl: float
    t2_unrealized_pnl: float
    
    # Combined
    combined_mtm_pnl: float
    total_realized_pnl: float
    combined_risk_state: str
    realized_source: str = "NONE_PENDING_FILL_LEDGER"
    qualification_eligible: bool = False
    research_only: bool = True
    requested_exit_order_type: str = "SL_LIMIT"
    research_limit_offset_pct: float = 0.5

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["t1_status"] = self.t1_status.value
        d["t2_status"] = self.t2_status.value
        return d


class TwoTrancheExitModel:
    """
    Manages the lifecycle and state transitions for two-tranche execution in Track 2.
    """

    @staticmethod
    def allocate_tranches(
        entry_price: float,
        stop_price: float,
        total_shares: int,
        target_1_rr: float = 1.5
    ) -> TrancheAllocation:
        """
        Partitions total shares into Tranche 1 (profit bank) and Tranche 2 (swing runner).
        Fails closed on invalid inputs.
        """
        for val in [entry_price, stop_price, target_1_rr]:
            if (isinstance(val, bool) or not isinstance(val, numbers.Real)
                    or not math.isfinite(float(val)) or float(val) <= 0):
                raise ValueError("FAIL-CLOSED: Invalid entry or stop price.")
        entry_price, stop_price, target_1_rr = map(float, (entry_price, stop_price, target_1_rr))
        entry_price, stop_price = round(entry_price, 2), round(stop_price, 2)
        if stop_price >= entry_price:
            raise ValueError("FAIL-CLOSED: Stop price must be strictly below entry price.")
        if (isinstance(total_shares, bool) or not isinstance(total_shares, numbers.Integral)
                or int(total_shares) <= 0):
            raise ValueError("FAIL-CLOSED: Total shares must be strictly positive.")
        total_shares = int(total_shares)

        risk_per_sh = round(entry_price - stop_price, 3)
        t1_target = round(entry_price + (target_1_rr * risk_per_sh), 2)

        if total_shares == 1:
            t1_shares = 1
            t2_shares = 0
        else:
            t1_shares = math.ceil(total_shares / 2.0)
            t2_shares = math.floor(total_shares / 2.0)

        notional = round(total_shares * entry_price, 2)
        total_risk = round(total_shares * risk_per_sh, 2)

        return TrancheAllocation(
            total_shares=total_shares,
            entry_price=round(entry_price, 2),
            initial_stop=round(stop_price, 2),
            risk_per_share=risk_per_sh,
            tranche1_shares=t1_shares,
            tranche2_shares=t2_shares,
            tranche1_target=t1_target,
            tranche1_rr=target_1_rr,
            notional_value=notional,
            total_rupee_risk=total_risk
        )

    @staticmethod
    def update_state(
        symbol: str, allocation: TrancheAllocation, ltp: float, peak_price: float,
        pdl: Optional[float] = None, daily_atr: Optional[float] = None,
        is_eod_squareoff: bool = False, current_state: Optional[TwoTrancheState] = None,
        order_type: Optional[str] = None, tick_low: Optional[float] = None,
        limit_offset_pct: float = 0.5, slippage_pts: float = 0.0
    ) -> TwoTrancheState:
        """Research intents only. No quote observation is execution evidence.

        No trailing from unordered OHLC extremes; no exchange-ready order prices.
        Fill-ledger integration and durable persistence are required before qualification.
        """
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("INVALID_SYMBOL")
        values = [ltp, peak_price] + [v for v in (pdl, daily_atr, tick_low) if v is not None]
        if any(isinstance(v, bool) or not isinstance(v, numbers.Real)
               or not math.isfinite(float(v)) or float(v) <= 0 for v in values):
            raise ValueError("INVALID_MARKET_INPUT")
        normalized_order_type = (current_state.requested_exit_order_type
                                 if current_state is not None and order_type is None
                                 else "SL_LIMIT" if order_type is None else order_type)
        if (type(is_eod_squareoff) is not bool
                or normalized_order_type not in ("SL_LIMIT", "SL_M")
                or isinstance(limit_offset_pct, bool) or not isinstance(limit_offset_pct, numbers.Real)
                or not math.isfinite(float(limit_offset_pct)) or not 0 <= float(limit_offset_pct) < 100
                or isinstance(slippage_pts, bool) or not isinstance(slippage_pts, numbers.Real)
                or not math.isfinite(float(slippage_pts)) or float(slippage_pts) != 0):
            raise ValueError("INVALID_EXECUTION_INPUT")
        # Validate allocation against its source inputs, including mutated dataclasses.
        expected = TwoTrancheExitModel.allocate_tranches(
            allocation.entry_price, allocation.initial_stop,
            allocation.total_shares, allocation.tranche1_rr)
        if allocation != expected:
            raise ValueError("INVALID_ALLOCATION")
        entry, stop = allocation.entry_price, allocation.initial_stop
        if current_state is not None:
            if (current_state.symbol != symbol or current_state.entry_price != entry
                    or current_state.t1_shares != allocation.tranche1_shares
                    or current_state.t2_shares != allocation.tranche2_shares
                    or current_state.t1_target != allocation.tranche1_target
                    or current_state.t1_active_sl != stop or current_state.t2_active_sl != stop
                    or current_state.t1_status not in RESEARCH_STATES
                    or current_state.t2_status not in RESEARCH_STATES
                    or current_state.t1_realized_pnl != 0
                    or current_state.t2_realized_pnl != 0
                    or current_state.total_realized_pnl != 0
                    or current_state.requested_exit_order_type != normalized_order_type
                    or current_state.research_limit_offset_pct != float(limit_offset_pct)):
                raise ValueError("UNVERIFIED_OR_MISMATCHED_PRIOR_STATE")
        peak = max(peak_price, ltp)
        low = min(ltp, tick_low) if tick_low is not None else ltp
        stop_hit = low <= stop
        target_hit = peak >= allocation.tranche1_target
        ambiguous = current_state is None and stop_hit and target_hit
        def next_status(previous, shares, has_target=False, eod=False):
            if stop_hit:
                return TrancheStatus.PENDING_STOP_EXIT
            if previous in PENDING_TRANCHE_STATES:
                return previous
            if not shares:
                return TrancheStatus.PENDING_ENTRY
            if eod:
                return TrancheStatus.PENDING_EOD_SQUAREOFF
            if has_target and target_hit:
                return TrancheStatus.PENDING_TARGET_EXIT
            return TrancheStatus.ACTIVE_INITIAL_STOP
        t1 = next_status(current_state.t1_status if current_state else None,
                         allocation.tranche1_shares, True, is_eod_squareoff)
        t2 = next_status(current_state.t2_status if current_state else None,
                         allocation.tranche2_shares)
        mtm1 = round(allocation.tranche1_shares * (ltp - entry), 2)
        mtm2 = round(allocation.tranche2_shares * (ltp - entry), 2)
        return TwoTrancheState(
            symbol=symbol, entry_price=entry, ltp=ltp, peak_price=peak,
            t1_shares=allocation.tranche1_shares, t1_status=t1, t1_active_sl=stop,
            t1_target=allocation.tranche1_target, t1_realized_pnl=0.0,
            t1_unrealized_pnl=mtm1, t2_shares=allocation.tranche2_shares,
            t2_status=t2, t2_active_sl=stop, t2_realized_pnl=0.0,
            t2_unrealized_pnl=mtm2, combined_mtm_pnl=round(mtm1 + mtm2, 2),
            total_realized_pnl=0.0,
            combined_risk_state=("AMBIGUOUS_ORDER" if ambiguous else
                                 "OCO_CONFLICT_PENDING_STOP_PRIORITY"
                                 if current_state is not None
                                 and current_state.t1_status == TrancheStatus.PENDING_TARGET_EXIT
                                 and stop_hit else "RESEARCH_PENDING_FILL_LEDGER"),
            requested_exit_order_type=normalized_order_type,
            research_limit_offset_pct=float(limit_offset_pct))


if __name__ == "__main__":
    allocation = TwoTrancheExitModel.allocate_tranches(100.0, 90.0, 10)
    state = TwoTrancheExitModel.update_state("TEST", allocation, 120.0, 120.0)
    assert state.t1_status == TrancheStatus.PENDING_TARGET_EXIT
    assert state.total_realized_pnl == 0.0
    print("Research-intent smoke test passed; no fill evidence or qualification.")
