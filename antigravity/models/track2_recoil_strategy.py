"""
track2_recoil_strategy.py - RECOIL: Volume-Climax Exhaustion Fade Strategy
==========================================================================
Part of Project Swing Trades (Track 2 Quantitative Strategy Incubator).
Formulated per ChatGPT/Codex & Claude Tri-Agent Consensus Specification (24 Sep 2026).

Quantitative Thesis:
  - Rapid, vertical price extensions accompanied by volume climax spikes represent
    emotional capitulation or retail FOMO exhaustion.
  - When price stretches > 1.4 * ATR20 away from session VWAP on RVOL >= 2.20 with
    a distinct rejection wick (>= 35% of total bar range), institutional absorption
    overwhelms aggressive market orders, triggering immediate mean reversion back to VWAP.

Regime Filter:
  - ON: Rotational / choppy market where trend efficiency ER8 < 0.45.
  - OFF: Strong runaway market trends (ER8 >= 0.55), where climax moves can continue trending.

Pattern Structure:
  1. VWAP Stretch:
     |Price - Session VWAP| >= 1.40 * ATR20.
  2. Volume Climax:
     RVOL >= 2.20 (abnormal volume surge).
  3. Rejection Wick Signature:
     - Long Reversal (Panic Capitulation Buy):
       Lower wick >= 35% of candle range: (min(Open, Close) - Low) / (High - Low) >= 0.35.
       Price is below VWAP, bouncing back up.
     - Short Reversal (Buying Climax Fade):
       Upper wick >= 35% of candle range: (High - max(Open, Close)) / (High - Low) >= 0.35.
       Price is above VWAP, rejecting highs.

Execution & Invalidation:
  - Entry: Close of rejection candle (or next executable price).
  - Structural Stop: Extreme price of rejection candle plus/minus 0.10 * ATR20.
  - Target: Snapback to Session VWAP (or 1.8R limit).
  - Time Stop: 4 bars (1 hour max holding).
  - Hard Flat: 15:10 IST prior to Closing Auction Session.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import time as dtime
import math
import numbers
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from antigravity.models.track2_shared_features import BarFeatures, SharedFeatureEngine


@dataclass(frozen=True)
class RecoilSignal:
    symbol: str
    decision: str  # SIGNAL_BUY, SIGNAL_SELL, REGIME_BLOCKED, NO_PATTERN, VOLUME_INSUFFICIENT, COST_HURDLE_FAILED, DATA_INVALID
    side: str  # "BUY" or "SELL"
    passed_all_gates: bool
    rejection_reason: Optional[str]
    entry_price: Optional[float]
    stop_price: Optional[float]
    target_price: Optional[float]
    shares: Optional[int]
    notional_value_rs: Optional[float]
    actual_risk_rs: Optional[float]
    risk_pct: Optional[float]
    vwap_distance_atr: Optional[float] = None
    strategy_name: str = "RECOIL"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RecoilStrategy:
    """
    Implements RECOIL volume-climax exhaustion fade strategy for Track 2.
    """

    def __init__(
        self,
        risk_budget_rs: float = 1500.0,
        max_slot_notional: float = 58333.0,
        max_er8: float = 0.45,
        min_rvol: float = 2.20,
        min_stretch_atr: float = 1.40,
        min_wick_ratio: float = 0.35,
        max_risk_pct: float = 2.50,
        min_risk_pct: float = 0.50,
    ):
        self.risk_budget_rs = risk_budget_rs
        self.max_slot_notional = max_slot_notional
        self.max_er8 = max_er8
        self.min_rvol = min_rvol
        self.min_stretch_atr = min_stretch_atr
        self.min_wick_ratio = min_wick_ratio
        self.max_risk_pct = max_risk_pct
        self.min_risk_pct = min_risk_pct

    def evaluate_setup(
        self,
        symbol: str,
        candles_15m: Sequence[Mapping[str, Any]],
        bucket_median_vol: float,
        current_time_ist: Optional[dtime] = None,
    ) -> RecoilSignal:
        """
        Evaluates RECOIL volume-climax mean-reversion pattern.
        """
        if not symbol or not candles_15m or len(candles_15m) < 8:
            return RecoilSignal(
                symbol=symbol or "UNKNOWN",
                decision="DATA_INVALID",
                side="NONE",
                passed_all_gates=False,
                rejection_reason="Insufficient candle history (minimum 8 15m bars required)",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
            )

        features = SharedFeatureEngine.extract_features(symbol, candles_15m, bucket_median_vol)
        if not features or features.atr20 <= 0:
            return RecoilSignal(
                symbol=symbol,
                decision="DATA_INVALID",
                side="NONE",
                passed_all_gates=False,
                rejection_reason="Failed to extract valid bar features or zero ATR",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
            )

        # 1. Regime Filter: Mean-reverting / choppy market (pre-climax ER8 < 0.45)
        # Avoid fading strong secular trend days
        pre_er8 = SharedFeatureEngine.calculate_er8(candles_15m[:-1])
        if pre_er8 >= self.max_er8:
            return RecoilSignal(
                symbol=symbol,
                decision="REGIME_BLOCKED",
                side="NONE",
                passed_all_gates=False,
                rejection_reason=f"Pre-climax trend efficiency ER8={pre_er8:.3f} >= {self.max_er8} (RECOIL blocks fading strong trends)",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
            )

        # 2. VWAP Stretch Check
        dist_to_vwap = features.close_p - features.session_vwap
        # 2. VWAP Stretch Check from candle extreme
        high_stretch = (features.high_p - features.session_vwap) / features.atr20
        low_stretch = (features.session_vwap - features.low_p) / features.atr20
        stretch_atr = max(high_stretch, low_stretch)

        if stretch_atr < self.min_stretch_atr:
            return RecoilSignal(
                symbol=symbol,
                decision="NO_PATTERN",
                side="NONE",
                passed_all_gates=False,
                rejection_reason=f"Extreme distance to VWAP {stretch_atr:.2f} ATR < {self.min_stretch_atr} ATR",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                vwap_distance_atr=round(stretch_atr, 2),
            )

        # 3. Volume Climax Confirmation (RVOL >= 2.20)
        if features.rvol < self.min_rvol:
            return RecoilSignal(
                symbol=symbol,
                decision="VOLUME_INSUFFICIENT",
                side="NONE",
                passed_all_gates=False,
                rejection_reason=f"Climax RVOL={features.rvol:.2f} < {self.min_rvol:.2f}",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                vwap_distance_atr=round(stretch_atr, 2),
            )

        # 4. Rejection Wick Analysis
        bar_range = features.high_p - features.low_p
        if bar_range <= 0:
            return RecoilSignal(
                symbol=symbol,
                decision="NO_PATTERN",
                side="NONE",
                passed_all_gates=False,
                rejection_reason="Bar range is zero or invalid",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
            )

        body_top = max(features.open_p, features.close_p)
        body_bottom = min(features.open_p, features.close_p)
        upper_wick = features.high_p - body_top
        lower_wick = body_bottom - features.low_p
        upper_wick_ratio = upper_wick / bar_range
        lower_wick_ratio = lower_wick / bar_range

        side: Optional[str] = None
        entry_price = round(features.close_p, 2)
        stop_price = 0.0
        target_price = round(features.session_vwap, 2)

        # Case A: Panic Capitulation Below VWAP -> BUY Reversal
        if low_stretch >= self.min_stretch_atr and lower_wick_ratio >= self.min_wick_ratio:
            side = "BUY"
            raw_stop = features.low_p - (0.10 * features.atr20)
            stop_price = round(raw_stop, 2)
            if stop_price >= entry_price or target_price <= entry_price:
                return RecoilSignal(
                    symbol=symbol,
                    decision="NO_PATTERN",
                    side="NONE",
                    passed_all_gates=False,
                    rejection_reason="Invalid geometry: stop >= entry or target <= entry for BUY",
                    entry_price=None, stop_price=None, target_price=None,
                    shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                )

        # Case B: Buying Climax Above VWAP -> SELL Reversal (MIS intraday short fade)
        elif high_stretch >= self.min_stretch_atr and upper_wick_ratio >= self.min_wick_ratio:
            side = "SELL"
            raw_stop = features.high_p + (0.10 * features.atr20)
            stop_price = round(raw_stop, 2)
            if stop_price <= entry_price or target_price >= entry_price:
                return RecoilSignal(
                    symbol=symbol,
                    decision="NO_PATTERN",
                    side="NONE",
                    passed_all_gates=False,
                    rejection_reason="Invalid geometry: stop <= entry or target >= entry for SELL",
                    entry_price=None, stop_price=None, target_price=None,
                    shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                )

        else:
            return RecoilSignal(
                symbol=symbol,
                decision="NO_PATTERN",
                side="NONE",
                passed_all_gates=False,
                rejection_reason=f"Insufficient rejection wick (upper={upper_wick_ratio:.2f}, lower={lower_wick_ratio:.2f} < {self.min_wick_ratio})",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                vwap_distance_atr=round(stretch_atr, 2),
            )

        # 5. Risk & Sizing Validation
        risk_per_sh = round(abs(entry_price - stop_price), 2)
        risk_pct = round((risk_per_sh / entry_price) * 100.0, 2)

        if not (self.min_risk_pct <= risk_pct <= self.max_risk_pct):
            return RecoilSignal(
                symbol=symbol,
                decision="REGIME_BLOCKED",
                side=side,
                passed_all_gates=False,
                rejection_reason=f"Risk {risk_pct:.2f}% outside permissible [{self.min_risk_pct}%, {self.max_risk_pct}%]",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
            )

        shares_by_risk = math.floor(self.risk_budget_rs / risk_per_sh)
        shares_by_notional = math.floor(self.max_slot_notional / entry_price)
        shares = min(shares_by_risk, shares_by_notional)

        if shares <= 0:
            return RecoilSignal(
                symbol=symbol,
                decision="REGIME_BLOCKED",
                side=side,
                passed_all_gates=False,
                rejection_reason="Position sizing resulted in 0 shares",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
            )

        notional_val = round(shares * entry_price, 2)
        actual_risk = round(shares * risk_per_sh, 2)

        # 6. Economic Friction Hurdle (0.106% round trip)
        target_dist = abs(target_price - entry_price)
        gross_target_gain = round(shares * target_dist, 2)
        estimated_friction = round(notional_val * 0.00106, 2)
        if gross_target_gain < 3.0 * estimated_friction:
            return RecoilSignal(
                symbol=symbol,
                decision="COST_HURDLE_FAILED",
                side=side,
                passed_all_gates=False,
                rejection_reason=f"Gross target gain Rs {gross_target_gain} < 3x round-trip friction Rs {3.0*estimated_friction:.2f}",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
            )

        decision = "SIGNAL_BUY" if side == "BUY" else "SIGNAL_SELL"

        return RecoilSignal(
            symbol=symbol,
            decision=decision,
            side=side,
            passed_all_gates=True,
            rejection_reason=None,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            shares=shares,
            notional_value_rs=notional_val,
            actual_risk_rs=actual_risk,
            risk_pct=risk_pct,
            vwap_distance_atr=round(stretch_atr, 2),
        )
