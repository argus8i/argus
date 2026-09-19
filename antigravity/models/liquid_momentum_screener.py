"""
liquid_momentum_screener.py - Quantitative Screener & Sizing for Liquid Mid/Small-Caps
Part of Project Swing Trades - Track 2 Liquid Momentum Engine.

Collaborative Architecture:
- Originator & Engineering Implementation: Antigravity Hub
- Microstructure & Adversarial Red-Teaming: Claude Code
- Regulatory & Surveillance Audits: ChatGPT / Codex
- Governance: AGENTS.md (Strict Rule 1 Observation Mode)
"""

import math
import os
import sys
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple, Any

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from antigravity.models.two_tranche_exit_model import TwoTrancheExitModel, TrancheAllocation
from antigravity.models.market_regime_filter import MarketRegimeFilter, MarketRegimeSnapshot, MarketRegimeState


@dataclass
class LiquidScripSnapshot:
    symbol: str
    series: str                     # Must be 'EQ'
    ltp: float
    mcap_cr: float                  # Market Cap in Rs Crores
    dtv_med20_cr: float             # Median 20-day Daily Traded Value (Rs Crores)
    beta: float                     # 252-day Beta vs Nifty 50
    atr14_pct: float                # 14-day ATR as % of price
    inst_holding_pct: float         # Mutual Fund + DII + FII holding %
    delivery_pct: float             # Current delivery % from sec_bhavdata_full
    band_pct: float                 # Price band (0.0 for dynamic F&O; fixed bands like 10, 20 are NOT F&O)
    is_surveillance: bool           # True if shortlisted in ASM, GSM, ESM, or T2T
    is_fno_underlying: bool = False # True if security has active F&O contracts with dynamic flexing bands


@dataclass
class SizingResult:
    shares: int
    notional_value: float
    actual_risk_rs: float
    stop_price: float
    target_price: float
    order_type: str                 # 'SL_M_NSE' (direct market stop) or 'SL_LIMIT_BSE'
    limit_exit_price: Optional[float]
    risk_reward_ratio: float
    constrained_by: str
    provenance: str = "DERIVED (Rule 5 fixed risk arithmetic)"
    tranche_allocation: Optional[TrancheAllocation] = None


class LiquidMomentumEngine:
    """
    Track 2 Liquid Momentum Engine:
    1. Adaptive Universe Screening (with fail-closed validation and F&O dynamic band enforcement).
    2. 15-Minute Opening Range Breakout (ORB) Confirmation (with max extension ceiling).
    3. Strict Risk-Budget Sizing (rupee budgeted, fail-closed degenerate stops, dynamic R:R).
    """

    @staticmethod
    def screen_universe(
        candidates: List[LiquidScripSnapshot],
        min_mcap_cr: float = 4000.0,
        max_mcap_cr: float = 75000.0,
        min_dtv_cr: float = 30.0,
        min_beta: float = 1.3,
        min_atr_pct: float = 3.5,
        min_inst_holding_pct: float = 15.0,
        min_surviving_pool: Optional[int] = None
    ) -> Tuple[List[LiquidScripSnapshot], Dict[str, any]]:
        """
        Screens candidates with automatic threshold relaxation if intersection is too thin.
        Enforces strict fail-closed data validation (NaN/None rejection and F&O dynamic band check).
        """
        def _is_valid_num(val: Optional[float], min_val: float = 0.0, strict_positive: bool = True) -> bool:
            if val is None or not isinstance(val, (int, float)):
                return False
            if math.isnan(val) or math.isinf(val):
                return False
            return val > min_val if strict_positive else val >= min_val

        def passes_filter(c: LiquidScripSnapshot, atr_floor: float, inst_floor: float) -> bool:
            # 1. Strict Fail-Closed Validation on series and surveillance
            if c.series != "EQ" or c.is_surveillance is not False:
                return False

            # 2. Strict Fail-Closed Validation on all numeric fields (rejects NaN and non-positive numbers)
            if not _is_valid_num(c.ltp):
                return False
            if not _is_valid_num(c.mcap_cr):
                return False
            if not _is_valid_num(c.dtv_med20_cr):
                return False
            if not _is_valid_num(c.beta):
                return False
            if not _is_valid_num(c.atr14_pct):
                return False
            if not _is_valid_num(c.inst_holding_pct, min_val=0.0, strict_positive=False):
                return False
            if not _is_valid_num(c.delivery_pct, min_val=0.0, strict_positive=False):
                return False

            # 3. Enforce F&O Dynamic Flexing Band rule (A6 Defense)
            # Track 2 requires dynamic flexing circuit bands. Fixed-band stocks (e.g. band_pct == 10 or 20 without F&O) are rejected.
            has_fno = getattr(c, "is_fno_underlying", False) or c.band_pct == 0.0
            if not has_fno:
                return False

            # 4. Quantitative Threshold Gates
            if not (min_mcap_cr <= c.mcap_cr <= max_mcap_cr):
                return False
            if c.dtv_med20_cr < min_dtv_cr:
                return False
            if c.beta < min_beta:
                return False
            if c.atr14_pct < atr_floor:
                return False
            if c.inst_holding_pct < inst_floor:
                return False

            return True

        # Phase 1: Strict Screening
        survivors = [c for c in candidates if passes_filter(c, min_atr_pct, min_inst_holding_pct)]
        relaxed = False
        relaxation_reason = "Standard parameters applied"

        # Target pool: if min_surviving_pool is explicitly provided, use it; otherwise cap at min(len(candidates), 15)
        target_pool = min_surviving_pool if min_surviving_pool is not None else min(len(candidates), 15)

        # Phase 2: Adaptive Relaxation if pool < target_pool
        if len(survivors) < target_pool:
            relaxed = True
            survivors = [c for c in candidates if passes_filter(c, 3.0, 10.0)]
            if len(survivors) < target_pool:
                relaxation_reason = f"Relaxed ATR (3.0%) and Inst (10.0%), but pool is STILL below target ({len(survivors)} < {target_pool})"
            else:
                relaxation_reason = f"Relaxed ATR floor to 3.0% and Institutional holding floor to 10.0% due to thin intersection (survivors: {len(survivors)})"

        return survivors, {
            "survivors_count": len(survivors),
            "relaxed": relaxed,
            "below_target": len(survivors) < target_pool,
            "reason": relaxation_reason
        }

    @staticmethod
    def evaluate_15m_orb_breakout(
        symbol: str,
        current_price: float,
        or_high: float,
        or_low: float,
        bucket_volume: float,
        historical_bucket_volume_median: float,
        atr14_intraday: float,
        min_volume_multiple: float = 2.5,
        regime_snapshot: Optional[MarketRegimeSnapshot] = None
    ) -> Dict[str, any]:
        """
        Evaluates 15-minute Opening Range Breakout (09:15 - 09:30 range).
        Fails closed on missing, NaN, or non-positive metrics.
        Enforces maximum breakout extension ceiling (rejects chases > 0.5 * ATR14 above OR high).
        Enforces Market Regime Filter (rejects longs on distribution, adjusts dynamic volume multiple).
        """
        for v in [current_price, or_high, or_low, bucket_volume, historical_bucket_volume_median]:
            if v is None or not isinstance(v, (int, float)) or math.isnan(v) or v <= 0:
                return {
                    "symbol": symbol,
                    "signal": "NO_ENTRY_DATA_INVALID",
                    "reason": "FAIL-CLOSED: Missing or invalid price/volume data",
                    "volume_ratio": 0.0,
                    "or_high": or_high,
                    "or_low": or_low
                }

        volume_ratio = bucket_volume / historical_bucket_volume_median

        # Check Market Regime Gate
        if regime_snapshot is not None:
            if regime_snapshot.state == MarketRegimeState.DISTRIBUTION_GATED:
                return {
                    "symbol": symbol,
                    "signal": "HOLD_REJECT_MARKET_DISTRIBUTION",
                    "reason": f"REJECTED: Broad market in distribution ({regime_snapshot.reason}). All long breakouts gated.",
                    "volume_ratio": round(volume_ratio, 2),
                    "or_high": or_high,
                    "or_low": or_low
                }
            if regime_snapshot.state == MarketRegimeState.REGIME_DATA_INVALID:
                return {
                    "symbol": symbol,
                    "signal": "NO_ENTRY_DATA_INVALID",
                    "reason": f"FAIL-CLOSED: Market regime data invalid ({regime_snapshot.reason}).",
                    "volume_ratio": round(volume_ratio, 2),
                    "or_high": or_high,
                    "or_low": or_low
                }
            if regime_snapshot.min_volume_multiple > min_volume_multiple:
                min_volume_multiple = regime_snapshot.min_volume_multiple

        is_breakout = current_price > or_high
        is_volume_confirmed = volume_ratio >= min_volume_multiple

        # Extension ceiling guard (A7 Defense):
        # Reject breakouts extended beyond 0.5 * ATR14 intraday points above OR high
        atr_pts = atr14_intraday if (atr14_intraday and not math.isnan(atr14_intraday) and atr14_intraday > 0) else (0.01 * or_high)
        max_allowed_entry = or_high + (0.5 * atr_pts)

        if is_breakout and current_price > max_allowed_entry:
            return {
                "symbol": symbol,
                "signal": "HOLD_REJECT_OVEREXTENDED",
                "reason": f"REJECTED: Price Rs {current_price:.2f} extended > 0.5*ATR ({max_allowed_entry:.2f}) above OR High Rs {or_high:.2f}. High chase risk.",
                "volume_ratio": round(volume_ratio, 2),
                "or_high": or_high,
                "or_low": or_low
            }

        if is_breakout and is_volume_confirmed:
            entry_signal = "BUY_ORB_CONFIRMED"
            reason = f"QUALIFIED ORB: Price Rs {current_price:.2f} > OR High Rs {or_high:.2f} with {volume_ratio:.2f}x volume confirmation (>= {min_volume_multiple}x)"
        elif is_breakout and not is_volume_confirmed:
            entry_signal = "HOLD_REJECT_FALSE_BREAKOUT"
            reason = f"REJECTED: Price broke OR High but volume ratio ({volume_ratio:.2f}x) failed threshold ({min_volume_multiple}x). High false breakout risk."
        elif not is_breakout:
            entry_signal = "WAIT_IN_RANGE"
            reason = f"Inside 15-minute Opening Range (High: Rs {or_high:.2f}, Low: Rs {or_low:.2f})"
        else:
            entry_signal = "NO_ENTRY"
            reason = "Criteria not met"

        return {
            "symbol": symbol,
            "signal": entry_signal,
            "reason": reason,
            "volume_ratio": round(volume_ratio, 2),
            "or_high": or_high,
            "or_low": or_low
        }

    @staticmethod
    def calculate_position_size(
        entry_price: float,
        or_low: float,
        atr14: float,
        dtv_med20_cr: float,
        exchange: str = "NSE",
        risk_budget_rs: float = 1500.0,
        max_notional_rs: float = 100000.0,
        limit_offset_pct: float = 0.5
    ) -> SizingResult:
        """
        Calculates position size strictly for a fixed rupee risk budget (e.g. Rs 1,500).
        Fails closed on degenerate stops (or_low >= entry_price).
        Dynamically computes realized risk:reward ratio.
        """
        # Validate inputs
        for val in [entry_price, or_low, atr14, dtv_med20_cr, risk_budget_rs, max_notional_rs]:
            if val is None or not isinstance(val, (int, float)) or math.isnan(val) or val <= 0:
                return SizingResult(
                    shares=0, notional_value=0.0, actual_risk_rs=0.0,
                    stop_price=0.0, target_price=0.0, order_type="REJECTED_INVALID_INPUT",
                    limit_exit_price=None, risk_reward_ratio=0.0,
                    constrained_by="INVALID_OR_NAN_INPUT"
                )

        # Fail-closed on degenerate stops (A4/A5 Defense)
        if entry_price <= or_low:
            return SizingResult(
                shares=0,
                notional_value=0.0,
                actual_risk_rs=0.0,
                stop_price=0.0,
                target_price=0.0,
                order_type="REJECTED_DEGENERATE_STOP",
                limit_exit_price=None,
                risk_reward_ratio=0.0,
                constrained_by="DEGENERATE_STOP_ENTRY_LEQ_OR_LOW"
            )

        stop_price = round(max(or_low, entry_price - (1.5 * atr14)), 2)

        # Exchange execution dynamics
        if exchange.upper() == "NSE":
            order_type = "SL_M_NSE"
            limit_exit_price = None
            effective_exit_price = stop_price
        else:
            order_type = "SL_LIMIT_BSE"
            limit_exit_price = round(stop_price * (1.0 - (limit_offset_pct / 100.0)), 2)
            effective_exit_price = limit_exit_price

        risk_per_share = round(entry_price - effective_exit_price, 3)
        if risk_per_share <= 0:
            return SizingResult(
                shares=0, notional_value=0.0, actual_risk_rs=0.0,
                stop_price=0.0, target_price=0.0, order_type="REJECTED_NON_POSITIVE_RISK",
                limit_exit_price=None, risk_reward_ratio=0.0,
                constrained_by="NON_POSITIVE_RISK_PER_SHARE"
            )

        # Raw size by rupee risk budget - NO MINIMUM NOTIONAL FLOOR
        qty = math.floor(risk_budget_rs / risk_per_share)
        constraint = "RISK_BUDGET"

        # Cap at maximum notional ceiling
        if (qty * entry_price) > max_notional_rs:
            qty = math.floor(max_notional_rs / entry_price)
            constraint = "MAX_NOTIONAL_CEILING"

        # Cap at <= 0.1% of median daily volume
        dtv_rupees = dtv_med20_cr * 10000000.0
        max_shares_dtv = math.floor((0.001 * dtv_rupees) / entry_price)
        if qty > max_shares_dtv:
            qty = max_shares_dtv
            constraint = "DTV_TURNOVER_CAP"

        final_notional = qty * entry_price
        actual_risk = round(qty * risk_per_share, 2)

        # 2R Target relative to structural stop
        target_price = round(entry_price + (2.0 * (entry_price - stop_price)), 2)
        reward_per_share = target_price - entry_price

        # Dynamic Realized Risk:Reward (A3 Defense)
        realized_rr = round(reward_per_share / (entry_price - effective_exit_price), 3) if (entry_price - effective_exit_price) > 0 else 0.0

        # Two-Tranche Allocation
        tranche_alloc = None
        if qty > 0:
            tranche_alloc = TwoTrancheExitModel.allocate_tranches(
                entry_price=entry_price,
                stop_price=effective_exit_price,
                total_shares=qty,
                target_1_rr=1.5
            )

        return SizingResult(
            shares=qty,
            notional_value=round(final_notional, 2),
            actual_risk_rs=actual_risk,
            stop_price=round(stop_price, 2),
            target_price=target_price,
            order_type=order_type,
            limit_exit_price=limit_exit_price,
            risk_reward_ratio=realized_rr,
            constrained_by=constraint,
            tranche_allocation=tranche_alloc
        )


if __name__ == "__main__":
    print("=== TESTING LIQUID MOMENTUM ENGINE (TRACK 2) ===")

    # Test 1: Universe Screening & Adaptive Relaxation
    test_pool = [
        LiquidScripSnapshot("SUZLON", "EQ", 75.0, 10500.0, 450.0, 1.85, 4.8, 22.0, 42.0, 0.0, False, is_fno_underlying=True),
        LiquidScripSnapshot("IREDA", "EQ", 230.0, 35000.0, 800.0, 2.10, 5.2, 18.0, 38.0, 0.0, False, is_fno_underlying=True),
        LiquidScripSnapshot("RVNL", "EQ", 580.0, 42000.0, 650.0, 1.70, 4.2, 28.0, 45.0, 0.0, False, is_fno_underlying=True),
        LiquidScripSnapshot("LOW_VOL", "EQ", 150.0, 8000.0, 40.0, 0.85, 1.8, 35.0, 60.0, 0.0, False, is_fno_underlying=True),
        LiquidScripSnapshot("ASM_NAME", "EQ", 95.0, 5000.0, 50.0, 1.60, 4.5, 16.0, 30.0, 5.0, True, is_fno_underlying=False),
    ]

    survivors, meta = LiquidMomentumEngine.screen_universe(test_pool, min_surviving_pool=2)
    print(f"Test 1 (Screening): {len(survivors)} passed | Meta: {meta}")
    assert len(survivors) >= 2, "Screening failed"

    # Test 2A: 15-Minute ORB Breakout with Volume Confirmation (Valid Breakout)
    orb_res = LiquidMomentumEngine.evaluate_15m_orb_breakout(
        symbol="SUZLON",
        current_price=75.80,
        or_high=75.50,
        or_low=74.20,
        bucket_volume=2500000,
        historical_bucket_volume_median=800000,
        atr14_intraday=0.85
    )
    print(f"Test 2A (Valid ORB Breakout): Signal = {orb_res['signal']} | Ratio = {orb_res['volume_ratio']}x")
    assert orb_res["signal"] == "BUY_ORB_CONFIRMED", "ORB signal failed"

    # Test 2B: Overextended Breakout Guard (> 0.5 * ATR above OR High)
    orb_over = LiquidMomentumEngine.evaluate_15m_orb_breakout(
        symbol="SUZLON",
        current_price=76.20,
        or_high=75.50,
        or_low=74.20,
        bucket_volume=2500000,
        historical_bucket_volume_median=800000,
        atr14_intraday=0.85
    )
    print(f"Test 2B (Overextended ORB): Signal = {orb_over['signal']}")
    assert orb_over["signal"] == "HOLD_REJECT_OVEREXTENDED", "Overextension guard failed"

    # Test 3: Sizing for NSE (SL-M) and BSE (SL-Limit)
    size_nse = LiquidMomentumEngine.calculate_position_size(
        entry_price=76.20,
        or_low=74.20,
        atr14=0.85,
        dtv_med20_cr=450.0,
        exchange="NSE"
    )
    print(f"Test 3 (NSE Sizing): Shares = {size_nse.shares} | Risk = Rs {size_nse.actual_risk_rs} | R:R = {size_nse.risk_reward_ratio}")
    assert size_nse.shares > 0, "NSE sizing failed"

    size_bse = LiquidMomentumEngine.calculate_position_size(
        entry_price=76.20,
        or_low=74.20,
        atr14=0.85,
        dtv_med20_cr=450.0,
        exchange="BSE"
    )
    print(f"Test 3 (BSE Sizing): Shares = {size_bse.shares} | Risk = Rs {size_bse.actual_risk_rs} | Limit Exit = {size_bse.limit_exit_price} | R:R = {size_bse.risk_reward_ratio}")
    assert size_bse.shares > 0, "BSE sizing failed"
    assert size_bse.limit_exit_price is not None, "BSE limit exit price missing"

    print("\nALL TRACK 2 LIQUID MOMENTUM SELF-TESTS PASSED 100%!")
