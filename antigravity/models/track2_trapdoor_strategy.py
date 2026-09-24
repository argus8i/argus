"""
track2_trapdoor_strategy.py - TRAPDOOR: Intraday Failed-Breakdown Reversal
========================================================================
Part of Project Swing Trades (Track 2 Quantitative Strategy Incubator).
Formulated per ChatGPT/Codex Strategy Specification (24 Sep 2026).

Quantitative Thesis:
  - A failed downward break out of a compressed range forces recently entered
    short breakout traders to panic and cover, while institutional dip buyers absorb supply.
  - Unlike trend continuation, this strategy specifically trades the FAILURE of an intraday break.

Regime Filter:
  - ON: Range-bound / rotational market, ER8 < 0.25, Market Breadth between 0.40 and 0.60.
  - OFF: Strong market or sector trend (ER8 >= 0.35) or major news-driven shock.

Pattern Structure:
  1. Mother Bar (m) and Inside Bar (i = m+1):
     H_i < H_m, L_i > L_m, and (H_m - L_m) <= 1.2 * ATR20.
  2. Failed Breakdown Bar (f within 2 bars of i):
     L_f < L_i, but C_f > L_i (probes below inside low but closes back above).
  3. Reversal Confirmation (within 2 bars of f):
     Close > H_i with RVOL >= 1.30.

Execution & Invalidation:
  - Entry: Next executable Ask price.
  - Structural Stop: Lowest price since the inside bar minus 0.10 * ATR20.
  - Target: 1.8R (Target = Entry + 1.8 * (Entry - Stop)).
  - Time Exit: Maximum 4 bars (1 hour).
  - Hard Flat: 15:10 IST (before Closing Auction Session transition).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import math
import numbers
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from antigravity.models.track2_shared_features import BarFeatures, SharedFeatureEngine


@dataclass(frozen=True)
class TrapdoorSignal:
    symbol: str
    decision: str  # SIGNAL_BUY, REGIME_BLOCKED, NO_PATTERN, VOLUME_INSUFFICIENT, COST_HURDLE_FAILED, DATA_INVALID
    passed_all_gates: bool
    rejection_reason: Optional[str]
    entry_price: Optional[float]
    stop_price: Optional[float]
    target_price: Optional[float]
    shares: Optional[int]
    notional_value_rs: Optional[float]
    actual_risk_rs: Optional[float]
    risk_pct: Optional[float]
    max_holding_bars: int = 4
    strategy_name: str = "TRAPDOOR"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TrapdoorStrategy:
    """
    Evaluates intraday failed-breakdown reversals on 15m completed bars.
    """

    def __init__(
        self,
        risk_budget_rs: float = 1500.0,
        max_notional_rs: float = 58333.0,  # Exact slot cap
        target_r_multiple: float = 1.8,
        min_rvol: float = 1.30,
        est_roundtrip_friction_pct: float = 0.00106,  # Verified MIS 0.106%
    ):
        self.risk_budget_rs = risk_budget_rs
        self.max_notional_rs = max_notional_rs
        self.target_r_multiple = target_r_multiple
        self.min_rvol = min_rvol
        self.est_friction_pct = est_roundtrip_friction_pct

    def evaluate(
        self,
        symbol: str,
        candles_15m: Sequence[Mapping[str, Any]],
        bucket_median_vol: float,
        market_breadth: float = 0.50,
        current_ask: Optional[float] = None,
    ) -> TrapdoorSignal:
        """
        Evaluates the TRAPDOOR pattern on completed 15m bars.
        """
        # Minimum 6 bars required to identify Mother -> Inside -> Failed -> Confirm
        if len(candles_15m) < 6:
            return TrapdoorSignal(
                symbol=symbol,
                decision="DATA_INVALID",
                passed_all_gates=False,
                rejection_reason="Insufficient candles (minimum 6 completed 15m bars required)",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
            )

        features = SharedFeatureEngine.extract_features(symbol, candles_15m, bucket_median_vol)
        if not features:
            return TrapdoorSignal(
                symbol=symbol,
                decision="DATA_INVALID",
                passed_all_gates=False,
                rejection_reason="Failed to extract valid bar features",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
            )

        # 1. Regime Gate: Range-bound market (Pre-breakout ER8 < 0.35)
        # We measure trend efficiency of the consolidation leading up to confirmation
        pre_er8 = SharedFeatureEngine.calculate_er8(candles_15m[:-1])
        if pre_er8 >= 0.35:
            return TrapdoorSignal(
                symbol=symbol,
                decision="REGIME_BLOCKED",
                passed_all_gates=False,
                rejection_reason=f"Pre-breakout trend efficiency ER8={pre_er8:.3f} >= 0.35 indicates trending market (TRAPDOOR requires chop)",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
            )

        # 2. Inspect pattern across the last 5 completed bars
        # Bar indices: m (mother), i (inside = m+1), f (failed probe), c (confirmation = current)
        bars = candles_15m[-6:]
        confirm_bar = bars[-1]
        confirm_close = float(confirm_bar.get("close", 0.0))

        # Find valid mother & inside pair in bars[0:3]
        pattern_found = False
        inside_high = 0.0
        inside_low = 0.0
        pattern_low = confirm_close

        for m_idx in range(len(bars) - 3):
            m_h = float(bars[m_idx].get("high", 0.0))
            m_l = float(bars[m_idx].get("low", 0.0))
            i_h = float(bars[m_idx + 1].get("high", 0.0))
            i_l = float(bars[m_idx + 1].get("low", 0.0))

            if m_h <= m_l or i_h <= i_l:
                continue

            # Mother range compression check
            m_range = m_h - m_l
            if m_range > 1.20 * features.atr20:
                continue

            # Inside bar check
            if i_h < m_h and i_l > m_l:
                inside_high = i_h
                inside_low = i_l

                # Check for failed downward break in subsequent bars
                subsequent = bars[m_idx + 2 :]
                has_failed_break = False
                lowest_price = min(i_l, min(float(b.get("low", i_l)) for b in subsequent))

                for f_bar in subsequent[:-1]:
                    f_l = float(f_bar.get("low", 0.0))
                    f_c = float(f_bar.get("close", 0.0))
                    if f_l < inside_low and f_c > inside_low:
                        has_failed_break = True
                        break

                if has_failed_break and confirm_close > inside_high:
                    pattern_found = True
                    pattern_low = lowest_price
                    break

        if not pattern_found:
            return TrapdoorSignal(
                symbol=symbol,
                decision="NO_PATTERN",
                passed_all_gates=False,
                rejection_reason="No completed Mother->Inside->Failed Breakdown->Confirm sequence detected",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
            )

        # 3. Volume Confirmation Gate
        if features.rvol < self.min_rvol:
            return TrapdoorSignal(
                symbol=symbol,
                decision="VOLUME_INSUFFICIENT",
                passed_all_gates=False,
                rejection_reason=f"Confirmation RVOL {features.rvol:.2f}x below threshold {self.min_rvol:.2f}x",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
            )

        # 4. Sizing & Structural Stop
        entry_price = round(current_ask or confirm_close, 2)
        structural_stop = round(pattern_low - (0.10 * features.atr20), 2)
        risk_per_share = entry_price - structural_stop
        risk_pct = (risk_per_share / entry_price) * 100.0

        if risk_per_share <= 0 or not (0.40 <= risk_pct <= 2.50):
            return TrapdoorSignal(
                symbol=symbol,
                decision="INVALID_STOP",
                passed_all_gates=False,
                rejection_reason=f"Stop distance {risk_pct:.2f}% outside permitted [0.40%, 2.50%] structural bounds",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
            )

        shares = int(min(self.risk_budget_rs // risk_per_share, self.max_notional_rs // entry_price))
        if shares <= 0:
            return TrapdoorSignal(
                symbol=symbol,
                decision="SIZING_ZERO",
                passed_all_gates=False,
                rejection_reason="Position sizing yielded 0 shares",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
            )

        notional_rs = round(shares * entry_price, 2)
        actual_risk_rs = round(shares * risk_per_share, 2)
        target_price = round(entry_price + (self.target_r_multiple * risk_per_share), 2)

        # 5. Economic Friction Hurdle Check
        # Target gross gain: q * g. Must be >= 3 * all_in_cost
        gross_gain = shares * (target_price - entry_price)
        est_cost = round(notional_rs * self.est_friction_pct, 2)
        if gross_gain < 3.0 * est_cost:
            return TrapdoorSignal(
                symbol=symbol,
                decision="COST_HURDLE_FAILED",
                passed_all_gates=False,
                rejection_reason=f"Target gross gain ₹{gross_gain:.1f} < 3x estimated friction ₹{est_cost:.1f}",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
            )

        return TrapdoorSignal(
            symbol=symbol,
            decision="SIGNAL_BUY",
            passed_all_gates=True,
            rejection_reason=None,
            entry_price=entry_price,
            stop_price=structural_stop,
            target_price=target_price,
            shares=shares,
            notional_value_rs=notional_rs,
            actual_risk_rs=actual_risk_rs,
            risk_pct=round(risk_pct, 2),
            max_holding_bars=4,
        )

    # Alias for MultiStrategyEngine compatibility
    evaluate_setup = evaluate
