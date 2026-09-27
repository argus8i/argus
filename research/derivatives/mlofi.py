"""
research/derivatives/mlofi.py
=============================
Multi-Level Order Flow Imbalance (MLOFI) Engine for NSE Liquid Equities.
Implements the Cont, Kukanov & Stoikov (2014) 5-level formulation with exact
signed price-level transitions per Section 3.3 of the BEACON Institutional Audit.

Governing Equation:
  e_k = I(b_t >= b_{t-1}) * q^b_t - I(b_t <= b_{t-1}) * q^b_{t-1}
      - I(a_t <= a_{t-1}) * q^a_t + I(a_t >= a_{t-1}) * q^a_{t-1}

Normalized Multi-Level OFI:
  MLOFI = sum(w_k * e_k) / sum(w_k * (q^b_t + q^b_{t-1} + q^a_t + q^a_{t-1}))
  Weights: w = (1.0, 0.6, 0.35, 0.2, 0.1) for levels 1 to 5.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class DepthLevel:
    level: int
    bid_price: float
    bid_shares: int
    ask_price: float
    ask_shares: int


@dataclass(frozen=True)
class LevelDepthSnapshot:
    symbol: str
    timestamp: str
    levels: List[DepthLevel]  # 5 depth levels, sorted best to worst

    def __post_init__(self):
        if len(self.levels) != 5:
            raise ValueError(f"Snapshot must contain exactly 5 levels, got {len(self.levels)}.")

    @property
    def best_bid(self) -> float:
        return self.levels[0].bid_price

    @property
    def best_ask(self) -> float:
        return self.levels[0].ask_price

    @property
    def is_crossed(self) -> bool:
        """True if top of book is locked or crossed (bid >= ask)."""
        return self.best_bid >= self.best_ask

    @property
    def weighted_midpoint(self) -> float:
        """Volume-weighted microprice at top of book."""
        l1 = self.levels[0]
        denom = l1.bid_shares + l1.ask_shares
        if denom <= 0:
            return round((l1.bid_price + l1.ask_price) / 2.0, 2)
        # Weighted midpoint: (bid * ask_qty + ask * bid_qty) / total_qty
        microprice = (l1.bid_price * l1.ask_shares + l1.ask_price * l1.bid_shares) / denom
        return round(microprice, 2)


@dataclass(frozen=True)
class OFIResult:
    symbol: str
    timestamp: str
    raw_mlofi: float
    normalized_mlofi: float
    weighted_midpoint: float
    is_valid: bool
    details: dict


class MultiLevelOFI:
    """
    Computes mathematically rigorous Order Flow Imbalance across 5 market depth levels.
    """

    LEVEL_WEIGHTS: Tuple[float, ...] = (1.0, 0.6, 0.35, 0.2, 0.1)

    @classmethod
    def compute_level_delta(cls, prev: DepthLevel, curr: DepthLevel) -> Tuple[float, float]:
        """
        Compute signed order flow delta e_k and total turnover denominator for one level.
        
        Returns:
            (e_k, level_volume_sum)
        """
        # Bid side contribution
        if curr.bid_price > prev.bid_price:
            # Bid improved upward -> new aggressive bid liquidity
            bid_contrib = float(curr.bid_shares)
        elif curr.bid_price == prev.bid_price:
            # Bid price stayed same -> delta in displayed shares
            bid_contrib = float(curr.bid_shares - prev.bid_shares)
        else:
            # Bid dropped downward -> bid liquidity was removed/canceled
            bid_contrib = -float(prev.bid_shares)

        # Ask side contribution
        if curr.ask_price < prev.ask_price:
            # Ask improved downward -> aggressive ask liquidity encroaching
            ask_contrib = -float(curr.ask_shares)
        elif curr.ask_price == prev.ask_price:
            # Ask price stayed same -> delta in ask shares
            ask_contrib = -float(curr.ask_shares - prev.ask_shares)
        else:
            # Ask price moved upward -> ask liquidity was removed/canceled
            ask_contrib = float(prev.ask_shares)

        e_k = bid_contrib + ask_contrib
        level_vol = float(curr.bid_shares + prev.bid_shares + curr.ask_shares + prev.ask_shares)
        return e_k, level_vol

    @classmethod
    def evaluate_snapshots(
        cls, prev_snap: LevelDepthSnapshot, curr_snap: LevelDepthSnapshot
    ) -> OFIResult:
        """
        Evaluate MLOFI between two sequential depth snapshots.
        """
        if curr_snap.symbol != prev_snap.symbol:
            raise ValueError(f"Symbol mismatch: {prev_snap.symbol} vs {curr_snap.symbol}")

        # Fail closed on crossed quotes or zero quotes
        if curr_snap.is_crossed or curr_snap.best_bid <= 0.0 or curr_snap.best_ask <= 0.0:
            return OFIResult(
                symbol=curr_snap.symbol,
                timestamp=curr_snap.timestamp,
                raw_mlofi=0.0,
                normalized_mlofi=0.0,
                weighted_midpoint=curr_snap.weighted_midpoint,
                is_valid=False,
                details={"reason": "CROSSED_OR_ZERO_DEPTH"},
            )

        weighted_numerator = 0.0
        weighted_denominator = 0.0
        level_breakdown = {}

        for k in range(5):
            w_k = cls.LEVEL_WEIGHTS[k]
            prev_level = prev_snap.levels[k]
            curr_level = curr_snap.levels[k]

            e_k, vol_k = cls.compute_level_delta(prev_level, curr_level)
            weighted_numerator += w_k * e_k
            weighted_denominator += w_k * vol_k
            level_breakdown[f"L{k+1}"] = {"e_k": e_k, "vol_k": vol_k, "weight": w_k}

        if weighted_denominator <= 0.0:
            return OFIResult(
                symbol=curr_snap.symbol,
                timestamp=curr_snap.timestamp,
                raw_mlofi=0.0,
                normalized_mlofi=0.0,
                weighted_midpoint=curr_snap.weighted_midpoint,
                is_valid=False,
                details={"reason": "ZERO_DEPTH_DENOMINATOR"},
            )

        normalized = max(-1.0, min(1.0, weighted_numerator / weighted_denominator))

        return OFIResult(
            symbol=curr_snap.symbol,
            timestamp=curr_snap.timestamp,
            raw_mlofi=round(weighted_numerator, 2),
            normalized_mlofi=round(normalized, 4),
            weighted_midpoint=curr_snap.weighted_midpoint,
            is_valid=True,
            details=level_breakdown,
        )
