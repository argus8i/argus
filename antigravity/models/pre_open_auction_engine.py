"""
pre_open_auction_engine.py - Tactical Pre-Open (09:00:00 - 09:08:00 IST) Auction Sniping Engine
Part of Project Swing Trades (Track 1: ESM & Circuit Micro-Caps).
Enforces queue priority, equilibrium price sanity, and strict anti-chasing gates before continuous market open.
"""

import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
from typing import Dict, Any, Optional, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from antigravity.models.circuit_rules import CircuitRuleEngine, ExecutionState
from antigravity.models.risk_calculator import CircuitRiskCalculator


class PreOpenAction(str, Enum):
    SUBMIT_PRE_OPEN_LIMIT = "SUBMIT_PRE_OPEN_LIMIT"       # Clean pre-open setup, room to UC, time priority 09:00:01
    ABORT_LOCKED_UC = "ABORT_LOCKED_UC"                   # 0 offers at UC ceiling; Rule 3 violation
    ABORT_EXCESSIVE_GAP = "ABORT_EXCESSIVE_GAP"           # Indicative price leaves < 3% room to UC; reward exhausted
    ABORT_SUB_10_FLOOR = "ABORT_SUB_10_FLOOR"             # Rule 2 price floor violation (< Rs 10.00)
    ABORT_HIGH_SPOOF_RISK = "ABORT_HIGH_SPOOF_RISK"       # Indicative buy-to-sell imbalance > 10x; phantom queue trap
    WAIT_FOR_CONTINUOUS = "WAIT_FOR_CONTINUOUS"           # Two-sided book wide spread, defer to 09:15:00
    DATA_INVALID = "DATA_INVALID"                         # Missing or malformed pre-open depth


@dataclass
class PreOpenAuctionResult:
    symbol: str
    scripcode: str
    prev_close: float
    upper_circuit: float
    lower_circuit: float
    band_pct: float
    indicative_price: Optional[float]
    indicative_bids: int
    indicative_offers: int
    imbalance_ratio: float
    headroom_pct: float
    action: PreOpenAction
    recommended_limit_price: Optional[float]
    recommended_shares: int
    queue_priority_window: str
    rationale: str


class PreOpenAuctionEngine:
    """
    Evaluates BSE/NSE Pre-Open session metrics (09:00:00 - 09:08:00 IST)
    to generate optimal limit prices and high-priority queue orders.
    """

    TICK_SIZE = 0.01
    MIN_HEADROOM_PCT = 3.0       # Must have at least 3.0% upside remaining to Upper Circuit
    MAX_IMBALANCE_RATIO = 10.0   # Imbalance > 10x indicates operator spoofing / non-executable bids

    @classmethod
    def evaluate_auction_snapshot(
        cls,
        symbol: str,
        scripcode: str,
        prev_close: float,
        circuit_band_pct: float,
        indicative_price: Optional[float],
        indicative_bids: int,
        indicative_offers: int,
        avg_20d_volume: int,
        risk_budget_rupees: float = 5000.0,
        current_time: Optional[time] = None
    ) -> PreOpenAuctionResult:
        """
        Evaluates pre-open snapshot and calculates optimal queue-priority limit order.
        """
        # Guard: Sanity checks
        if prev_close is None or not math.isfinite(prev_close) or prev_close <= 0:
            return cls._invalid_result(symbol, scripcode, "Invalid previous close.")

        if circuit_band_pct is None or not math.isfinite(circuit_band_pct) or circuit_band_pct <= 0:
            return cls._invalid_result(symbol, scripcode, "Invalid circuit band percentage.")

        # Rule 2: Absolute Rs 10 Floor
        if prev_close < CircuitRuleEngine.PRICE_FLOOR_INR:
            uc, lc = CircuitRuleEngine.calculate_bse_bands(prev_close, circuit_band_pct)
            return PreOpenAuctionResult(
                symbol=symbol,
                scripcode=scripcode,
                prev_close=prev_close,
                upper_circuit=uc,
                lower_circuit=lc,
                band_pct=circuit_band_pct,
                indicative_price=indicative_price,
                indicative_bids=indicative_bids,
                indicative_offers=indicative_offers,
                imbalance_ratio=0.0,
                headroom_pct=0.0,
                action=PreOpenAction.ABORT_SUB_10_FLOOR,
                recommended_limit_price=None,
                recommended_shares=0,
                queue_priority_window="NONE",
                rationale=f"Rule 2 Disqualification: Stock price (Rs {prev_close:.2f}) is below Rs 10.00 floor."
            )

        uc, lc = CircuitRuleEngine.calculate_bse_bands(prev_close, circuit_band_pct)

        # If no indicative price discovery yet (early in session, e.g. 09:00:10)
        iep = indicative_price if (indicative_price and indicative_price > 0 and math.isfinite(indicative_price)) else prev_close
        bids = max(0, indicative_bids or 0)
        offers = max(0, indicative_offers or 0)

        # Rule 3: Zero offers at UC ceiling -> Do NOT chase
        if offers == 0 and iep >= (uc - 0.02):
            return PreOpenAuctionResult(
                symbol=symbol,
                scripcode=scripcode,
                prev_close=prev_close,
                upper_circuit=uc,
                lower_circuit=lc,
                band_pct=circuit_band_pct,
                indicative_price=iep,
                indicative_bids=bids,
                indicative_offers=0,
                imbalance_ratio=float("inf"),
                headroom_pct=0.0,
                action=PreOpenAction.ABORT_LOCKED_UC,
                recommended_limit_price=None,
                recommended_shares=0,
                queue_priority_window="NONE",
                rationale="Rule 3 Prohibition: Stock locked at Upper Circuit with 0 offers in pre-open. Fill probability = 0%."
            )

        imbalance_ratio = round(bids / max(1, offers), 2)
        headroom_pct = round(((uc - iep) / iep) * 100, 2) if iep > 0 else 0.0

        # Excessive gap-up check: reward-to-risk exhausted
        if headroom_pct < cls.MIN_HEADROOM_PCT:
            return PreOpenAuctionResult(
                symbol=symbol,
                scripcode=scripcode,
                prev_close=prev_close,
                upper_circuit=uc,
                lower_circuit=lc,
                band_pct=circuit_band_pct,
                indicative_price=iep,
                indicative_bids=bids,
                indicative_offers=offers,
                imbalance_ratio=imbalance_ratio,
                headroom_pct=headroom_pct,
                action=PreOpenAction.ABORT_EXCESSIVE_GAP,
                recommended_limit_price=None,
                recommended_shares=0,
                queue_priority_window="NONE",
                rationale=f"Excessive Gap-Up: Indicative price Rs {iep:.2f} leaves only {headroom_pct:.1f}% headroom to UC (Rs {uc:.2f}). Minimum required: {cls.MIN_HEADROOM_PCT}%."
            )

        # Spoofing check: Huge bid wall with negligible offers
        if imbalance_ratio > cls.MAX_IMBALANCE_RATIO:
            return PreOpenAuctionResult(
                symbol=symbol,
                scripcode=scripcode,
                prev_close=prev_close,
                upper_circuit=uc,
                lower_circuit=lc,
                band_pct=circuit_band_pct,
                indicative_price=iep,
                indicative_bids=bids,
                indicative_offers=offers,
                imbalance_ratio=imbalance_ratio,
                headroom_pct=headroom_pct,
                action=PreOpenAction.ABORT_HIGH_SPOOF_RISK,
                recommended_limit_price=None,
                recommended_shares=0,
                queue_priority_window="NONE",
                rationale=f"Pre-Open Spoof Warning: Bid-to-offer imbalance ({imbalance_ratio:.1f}x) exceeds {cls.MAX_IMBALANCE_RATIO}x. High risk of phantom bid cancellation at 09:07 IST."
            )

        # Optimal pre-open limit price calculation:
        # Give 2 ticks above indicative price to secure execution at equilibrium, capped at 3 ticks below UC
        recommended_price = min(round(iep + 2 * cls.TICK_SIZE, 2), round(uc - 3 * cls.TICK_SIZE, 2))
        recommended_price = max(recommended_price, iep)

        # Position Sizing under Rule 5 (band-aware divisor) & Rule 9 (avg_20d
        # baseline). The band is passed through: this engine already knew it and
        # previously sized every scrip at the flat 5% divisor of 0.401.
        effective_vol = avg_20d_volume if avg_20d_volume and avg_20d_volume > 0 else 10000
        sizing = CircuitRiskCalculator.calculate_max_safe_position_by_10day_lc(
            rupees_willing_to_lose=risk_budget_rupees,
            stock_price=recommended_price,
            daily_volume=effective_vol,
            band_pct=circuit_band_pct,
        )
        shares = sizing.get("max_shares", 0)

        return PreOpenAuctionResult(
            symbol=symbol,
            scripcode=scripcode,
            prev_close=prev_close,
            upper_circuit=uc,
            lower_circuit=lc,
            band_pct=circuit_band_pct,
            indicative_price=iep,
            indicative_bids=bids,
            indicative_offers=offers,
            imbalance_ratio=imbalance_ratio,
            headroom_pct=headroom_pct,
            action=PreOpenAction.SUBMIT_PRE_OPEN_LIMIT,
            recommended_limit_price=recommended_price,
            recommended_shares=shares,
            queue_priority_window="09:00:01 - 09:04:59 IST",
            rationale=(
                f"Qualified Pre-Open Breakout: Headroom {headroom_pct:.1f}% to UC Rs {uc:.2f}. "
                f"Imbalance {imbalance_ratio:.1f}x healthy. Sized to {shares} shares under Rule 5 ({sizing.get('constrained_by')}). "
                "Submit limit order at 09:00:01 IST to establish Queue Rank R <= 10."
            )
        )

    @classmethod
    def _invalid_result(cls, symbol: str, scripcode: str, reason: str) -> PreOpenAuctionResult:
        return PreOpenAuctionResult(
            symbol=symbol,
            scripcode=scripcode,
            prev_close=0.0,
            upper_circuit=0.0,
            lower_circuit=0.0,
            band_pct=0.0,
            indicative_price=None,
            indicative_bids=0,
            indicative_offers=0,
            imbalance_ratio=0.0,
            headroom_pct=0.0,
            action=PreOpenAction.DATA_INVALID,
            recommended_limit_price=None,
            recommended_shares=0,
            queue_priority_window="NONE",
            rationale=f"DATA_INVALID / FAIL-CLOSED: {reason}"
        )


if __name__ == "__main__":
    print("=== PRE-OPEN AUCTION ENGINE SELF-TEST ===")
    res = PreOpenAuctionEngine.evaluate_auction_snapshot(
        symbol="MOBIKWIK",
        scripcode="544305",
        prev_close=188.45,
        circuit_band_pct=20.0,
        indicative_price=195.0,
        indicative_bids=50000,
        indicative_offers=30000,
        avg_20d_volume=45000,
        risk_budget_rupees=5000.0
    )
    print(f"Action: {res.action.value}")
    print(f"Recommended Limit Price: Rs {res.recommended_limit_price} | Shares: {res.recommended_shares}")
    print(f"Rationale: {res.rationale}")
