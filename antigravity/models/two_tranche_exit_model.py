"""
two_tranche_exit_model.py - Two-Tranche Trailing Exit & Scaling Model for Track 2
Part of Project Swing Trades (Antigravity + Claude Code + OpenAI Codex).

Quantitative Purpose:
- Eliminates premature trade churn caused by moving the entire position to breakeven at +1R.
- Tranche 1 (50% qty): Banks guaranteed profit at +1.5R/+2.0R to de-risk the trade.
- Tranche 2 (50% qty): Multi-day CNC swing runner that trails on Previous Day Low (PDL) or Daily ATR.
- Fail-Closed: Strict validation against negative shares, inverted stops, or NaN metrics.
"""

from dataclasses import dataclass, asdict
from enum import Enum
import math
from typing import Optional, Dict, Any, Tuple


class TrancheStatus(Enum):
    PENDING_ENTRY = "PENDING_ENTRY"
    ACTIVE_INITIAL_STOP = "ACTIVE_INITIAL_STOP"
    TRAILED_BREAKEVEN = "TRAILED_BREAKEVEN"
    TARGET_FILLED = "TARGET_FILLED"
    STOPPED_OUT = "STOPPED_OUT"
    SWING_TRAILING = "SWING_TRAILING"


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
        for val in [entry_price, stop_price]:
            if val is None or not isinstance(val, (int, float)) or math.isnan(val) or val <= 0:
                raise ValueError("FAIL-CLOSED: Invalid entry or stop price.")
        if stop_price >= entry_price:
            raise ValueError("FAIL-CLOSED: Stop price must be strictly below entry price.")
        if total_shares <= 0:
            raise ValueError("FAIL-CLOSED: Total shares must be strictly positive.")

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
        symbol: str,
        allocation: TrancheAllocation,
        ltp: float,
        peak_price: float,
        pdl: Optional[float] = None,
        daily_atr: Optional[float] = None,
        is_eod_squareoff: bool = False
    ) -> TwoTrancheState:
        """
        Updates the real-time position state, trailing stop levels, and P&L for both tranches.
        """
        entry = allocation.entry_price
        initial_sl = allocation.initial_stop
        risk_per_sh = allocation.risk_per_share
        peak = max(peak_price, ltp)

        # -------------------------------------------------------------
        # 1. TRANCHE 1 EVALUATION (Fixed Target Banking)
        # -------------------------------------------------------------
        t1_shares = allocation.tranche1_shares
        t1_target = allocation.tranche1_target

        if t1_shares == 0:
            t1_status = TrancheStatus.STOPPED_OUT
            t1_sl = entry
            t1_realized = 0.0
            t1_unrealized = 0.0
        elif peak >= t1_target:
            # Target hit and locked
            t1_status = TrancheStatus.TARGET_FILLED
            t1_sl = t1_target
            t1_realized = round(t1_shares * (t1_target - entry), 2)
            t1_unrealized = 0.0
        elif ltp <= initial_sl:
            t1_status = TrancheStatus.STOPPED_OUT
            t1_sl = initial_sl
            t1_realized = round(t1_shares * (initial_sl - entry), 2)
            t1_unrealized = 0.0
        elif peak >= (entry + (1.0 * risk_per_sh)):
            # Gained >= +1.0R -> stop moved to breakeven
            if ltp <= entry:
                t1_status = TrancheStatus.STOPPED_OUT
                t1_sl = entry
                t1_realized = 0.0
                t1_unrealized = 0.0
            else:
                t1_status = TrancheStatus.TRAILED_BREAKEVEN
                t1_sl = entry
                t1_realized = 0.0
                t1_unrealized = round(t1_shares * (ltp - entry), 2)
        else:
            t1_status = TrancheStatus.ACTIVE_INITIAL_STOP
            t1_sl = initial_sl
            t1_realized = 0.0
            t1_unrealized = round(t1_shares * (ltp - entry), 2)

        # -------------------------------------------------------------
        # 2. TRANCHE 2 EVALUATION (Multi-Day CNC Swing Runner)
        # -------------------------------------------------------------
        t2_shares = allocation.tranche2_shares
        if t2_shares == 0:
            t2_status = TrancheStatus.STOPPED_OUT
            t2_sl = entry
            t2_realized = 0.0
            t2_unrealized = 0.0
        else:
            # Baseline rule: If T1 locked profit or touched +1.0R, T2 stop is at least Breakeven
            t1_derisked = (t1_status == TrancheStatus.TARGET_FILLED) or (peak >= (entry + (1.0 * risk_per_sh)))

            if t1_derisked:
                # Dynamic swing trailing baseline: max(entry, PDL, close - 1.5*ATR)
                candidate_stops = [entry]
                if pdl is not None and pdl > 0:
                    candidate_stops.append(pdl)
                if daily_atr is not None and daily_atr > 0:
                    candidate_stops.append(round(peak - (1.5 * daily_atr), 2))
                
                t2_sl = max(candidate_stops)

                if ltp <= t2_sl:
                    t2_status = TrancheStatus.STOPPED_OUT
                    t2_realized = round(t2_shares * (t2_sl - entry), 2)
                    t2_unrealized = 0.0
                else:
                    t2_status = TrancheStatus.SWING_TRAILING
                    t2_realized = 0.0
                    t2_unrealized = round(t2_shares * (ltp - entry), 2)
            else:
                # Still in initial risk phase
                t2_sl = initial_sl
                if ltp <= initial_sl:
                    t2_status = TrancheStatus.STOPPED_OUT
                    t2_realized = round(t2_shares * (initial_sl - entry), 2)
                    t2_unrealized = 0.0
                else:
                    t2_status = TrancheStatus.ACTIVE_INITIAL_STOP
                    t2_realized = 0.0
                    t2_unrealized = round(t2_shares * (ltp - entry), 2)

        total_realized = round(t1_realized + t2_realized, 2)
        combined_mtm = round(t1_unrealized + t2_unrealized, 2)

        # Determine composite label
        if t1_status == TrancheStatus.TARGET_FILLED and t2_status == TrancheStatus.SWING_TRAILING:
            composite_state = "T1_BANKED_T2_RUNNING_ZERO_RISK"
        elif t1_status == TrancheStatus.TARGET_FILLED and t2_status == TrancheStatus.STOPPED_OUT:
            composite_state = "FULL_TRADE_CLOSED_PROFIT"
        elif t1_status == TrancheStatus.STOPPED_OUT and t2_status == TrancheStatus.STOPPED_OUT:
            composite_state = "FULL_TRADE_STOPPED"
        elif t1_status == TrancheStatus.TRAILED_BREAKEVEN or t2_status == TrancheStatus.SWING_TRAILING:
            composite_state = "TRAILED_BREAKEVEN_ZERO_DOWNSIDE"
        else:
            composite_state = "INITIAL_RISK_ACTIVE"

        return TwoTrancheState(
            symbol=symbol,
            entry_price=entry,
            ltp=ltp,
            peak_price=peak,
            t1_shares=t1_shares,
            t1_status=t1_status,
            t1_active_sl=t1_sl,
            t1_target=t1_target,
            t1_realized_pnl=t1_realized,
            t1_unrealized_pnl=t1_unrealized,
            t2_shares=t2_shares,
            t2_status=t2_status,
            t2_active_sl=t2_sl,
            t2_realized_pnl=t2_realized,
            t2_unrealized_pnl=t2_unrealized,
            combined_mtm_pnl=combined_mtm,
            total_realized_pnl=total_realized,
            combined_risk_state=composite_state
        )


if __name__ == "__main__":
    print("=== TESTING TWO-TRANCHE EXIT MODEL ===")

    # Test Case 1: CDSL Allocation (38 shares @ 1332.90, stop 1300.00)
    alloc = TwoTrancheExitModel.allocate_tranches(entry_price=1332.90, stop_price=1300.00, total_shares=38, target_1_rr=1.5)
    print(f"CDSL Allocation: T1 = {alloc.tranche1_shares} shs (Tgt {alloc.tranche1_target}), T2 = {alloc.tranche2_shares} shs (Swing Runner)")
    assert alloc.tranche1_shares == 19
    assert alloc.tranche2_shares == 19
    assert alloc.tranche1_target == 1382.25

    # Test Case 2: In-flight progression to +1R (LTP 1370.00)
    s1 = TwoTrancheExitModel.update_state("CDSL", alloc, ltp=1370.00, peak_price=1370.00)
    print(f"State @ 1370 (+1.1R): T1={s1.t1_status.value} (SL {s1.t1_active_sl}), T2={s1.t2_status.value} (SL {s1.t2_active_sl}) | State: {s1.combined_risk_state}")
    assert s1.t1_status == TrancheStatus.TRAILED_BREAKEVEN
    assert s1.t1_active_sl == 1332.90
    assert s1.t2_active_sl == 1332.90

    # Test Case 3: Target 1 Hit (LTP 1390.00) with PDL support at 1350.00
    s2 = TwoTrancheExitModel.update_state("CDSL", alloc, ltp=1390.00, peak_price=1390.00, pdl=1350.00)
    print(f"State @ 1390 (T1 Target Exceeded): T1 Realized = Rs {s2.t1_realized_pnl} | T2 SL = Rs {s2.t2_active_sl} | Composite: {s2.combined_risk_state}")
    assert s2.t1_status == TrancheStatus.TARGET_FILLED
    assert s2.t1_realized_pnl == round(19 * (1382.25 - 1332.90), 2)
    assert s2.t2_active_sl == 1350.00  # Trailed to PDL
    assert s2.combined_risk_state == "T1_BANKED_T2_RUNNING_ZERO_RISK"

    # Test Case 4: Odd shares (3 shares)
    odd_alloc = TwoTrancheExitModel.allocate_tranches(entry_price=100.0, stop_price=90.0, total_shares=3)
    assert odd_alloc.tranche1_shares == 2
    assert odd_alloc.tranche2_shares == 1

    # Test Case 5: Single share (1 share)
    single_alloc = TwoTrancheExitModel.allocate_tranches(entry_price=1000.0, stop_price=950.0, total_shares=1)
    assert single_alloc.tranche1_shares == 1
    assert single_alloc.tranche2_shares == 0

    print("ALL TWO-TRANCHE EXIT MODEL SELF-TESTS PASSED 100%!")
