"""
track2_volatility_squeeze_strategy.py - Multi-Day Volatility Contraction & Squeeze Expansion
=========================================================================================
Part of Project Swing Trades (Track 2 Multi-Strategy Alpha Engine).

Quantitative Thesis:
  1. Volatility Clustering & Cycle Alternation:
     - Volatility is mean-reverting: periods of extreme low volatility are inevitably
       followed by explosive volatility expansion (Mandelbrot, 1963).
     - In Indian F&O mid-caps, when a stock trades within its narrowest daily range in 7 days
       (NR7) or compresses Bollinger Bands inside Keltner Channels, multi-day order flow
       coils like a spring.
  2. Directional Breakout Trigger:
     - When a compressed stock breaks out of its 15-minute Opening Range on Day 0 with
       elevated volume, the follow-through probability is statistically superior to
       random breakout entries.
     - The compression provides clear structural invalidation: if price trades back below
       the compression range midpoint, the squeeze has failed.

Mathematical Formulation:
  - Daily Range: R_d = High_d - Low_d
  - NR7 Condition: R_0 < min(R_{-1}, R_{-2}, ..., R_{-6})
  - Inside Day (ID): High_0 < High_{-1} and Low_0 > Low_{-1}
  - Bollinger Bands (20, 2.0): BB_mid = SMA20, BB_up = SMA20 + 2.0*StdDev, BB_low = SMA20 - 2.0*StdDev
  - Keltner Channels (20, 1.5): KC_mid = EMA20, KC_up = EMA20 + 1.5*ATR20, KC_low = EMA20 - 1.5*ATR20
  - Squeeze Active: BB_up <= KC_up and BB_low >= KC_low (BB inside KC)
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import math
import numbers
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


def _finite_positive(val: Any) -> bool:
    return (
        not isinstance(val, bool)
        and isinstance(val, numbers.Real)
        and math.isfinite(float(val))
        and float(val) > 0
    )


@dataclass(frozen=True)
class DailyCompressionProfile:
    is_nr7: bool
    is_inside_day: bool
    is_bb_kc_squeeze: bool
    compression_score: float  # 0.0 to 1.0
    compression_range_high: float
    compression_range_low: float
    compression_midpoint: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VolatilitySqueezeSignal:
    symbol: str
    decision: str  # SIGNAL_BUY, NO_COMPRESSION, NO_BREAKOUT, VOLUME_INSUFFICIENT, TREND_BEARISH, DATA_INVALID
    passed_all_gates: bool
    rejection_reason: Optional[str]
    entry_price: Optional[float]
    stop_price: Optional[float]
    target_tranche1: Optional[float]
    target_tranche2: Optional[float]
    shares: Optional[int]
    notional_value_rs: Optional[float]
    actual_risk_rs: Optional[float]
    risk_pct: Optional[float]
    compression_profile: Optional[DailyCompressionProfile]
    volume_multiple: Optional[float]
    strategy_name: str = "VOLATILITY_SQUEEZE"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class VolatilitySqueezeStrategy:
    """
    Evaluates multi-day volatility compression (NR7, Inside Day, BB Squeeze)
    followed by an intraday 15-minute expansion breakout.
    """

    def __init__(
        self,
        min_volume_mult: float = 2.0,
        risk_budget_rs: float = 1500.0,
        max_notional_rs: float = 58333.33,
        r_multiple_t1: float = 1.5,
        r_multiple_t2: float = 3.0,
    ):
        self.min_volume_mult = min_volume_mult
        self.risk_budget_rs = risk_budget_rs
        self.max_notional_rs = max_notional_rs
        self.r_multiple_t1 = r_multiple_t1
        self.r_multiple_t2 = r_multiple_t2

    @staticmethod
    def detect_daily_compression(daily_candles: Sequence[Mapping[str, Any]]) -> Optional[DailyCompressionProfile]:
        """
        Inspects the last 7 to 20 daily bars to determine if the stock is coiled in a compression squeeze.
        """
        if len(daily_candles) < 7:
            return None

        # Last completed daily bar is index -1 (or yesterday before today's open)
        ranges = []
        for bar in daily_candles[-7:]:
            h = float(bar.get("high", 0.0))
            l = float(bar.get("low", 0.0))
            if _finite_positive(h) and _finite_positive(l) and h >= l:
                ranges.append((h, l, h - l))

        if len(ranges) < 7:
            return None

        recent_h, recent_l, recent_range = ranges[-1]
        prior_ranges = [r[2] for r in ranges[:-1]]

        # 1. NR7 Condition: Narrowest range of the last 7 sessions
        is_nr7 = recent_range < min(prior_ranges)

        # 2. Inside Day Condition: High < Prior High and Low > Prior Low
        prior_h, prior_l, _ = ranges[-2]
        is_inside_day = recent_h <= prior_h and recent_l >= prior_l

        # 3. Simple BB / KC Squeeze approximation if 20 daily bars available
        is_bb_kc_squeeze = False
        if len(daily_candles) >= 20:
            closes = [float(b.get("close", 0.0)) for b in daily_candles[-20:]]
            if all(_finite_positive(c) for c in closes):
                sma20 = sum(closes) / 20.0
                std_dev = math.sqrt(sum((c - sma20) ** 2 for c in closes) / 20.0)
                # Keltner ATR approx
                atrs = [float(b.get("high", 0.0)) - float(b.get("low", 0.0)) for b in daily_candles[-20:]]
                atr20 = sum(atrs) / 20.0
                bb_width = 2.0 * std_dev
                kc_width = 1.5 * atr20
                is_bb_kc_squeeze = bb_width < kc_width

        # Composite compression score
        score = 0.0
        if is_nr7:
            score += 0.45
        if is_inside_day:
            score += 0.30
        if is_bb_kc_squeeze:
            score += 0.25

        midpoint = (recent_h + recent_l) / 2.0

        return DailyCompressionProfile(
            is_nr7=is_nr7,
            is_inside_day=is_inside_day,
            is_bb_kc_squeeze=is_bb_kc_squeeze,
            compression_score=round(score, 2),
            compression_range_high=round(recent_h, 2),
            compression_range_low=round(recent_l, 2),
            compression_midpoint=round(midpoint, 2),
        )

    def evaluate(
        self,
        symbol: str,
        daily_candles: Sequence[Mapping[str, Any]],
        candles_15m: Sequence[Mapping[str, Any]],
        hist_median_volume_15m: float,
        daily_ema20: float,
        daily_ema50: float,
        microstructure_trap: bool = False,
    ) -> VolatilitySqueezeSignal:
        """
        Evaluates daily compression and intraday breakout.
        """
        # 1. Detect Daily Compression
        profile = self.detect_daily_compression(daily_candles)
        if not profile or profile.compression_score < 0.30:
            return VolatilitySqueezeSignal(
                symbol=symbol,
                decision="NO_COMPRESSION",
                passed_all_gates=False,
                rejection_reason="No multi-day volatility compression detected (NR7/ID/Squeeze score < 0.30)",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                compression_profile=profile, volume_multiple=None
            )

        # 2. Check Intraday Candles
        if not candles_15m:
            return VolatilitySqueezeSignal(
                symbol=symbol,
                decision="DATA_INVALID",
                passed_all_gates=False,
                rejection_reason="Missing intraday 15m candles",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                compression_profile=profile, volume_multiple=None
            )

        current_bar = candles_15m[-1]
        curr_close = float(current_bar.get("close", 0.0))
        curr_vol = int(current_bar.get("volume", 0))

        # 3. Daily Structural Trend Gate
        if not (curr_close > daily_ema20 > daily_ema50):
            return VolatilitySqueezeSignal(
                symbol=symbol,
                decision="TREND_BEARISH",
                passed_all_gates=False,
                rejection_reason=f"Price {curr_close:.2f} failed daily trend filter (EMA20={daily_ema20:.2f}, EMA50={daily_ema50:.2f})",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                compression_profile=profile, volume_multiple=None
            )

        # 4. Microstructure Trap Check
        if microstructure_trap:
            return VolatilitySqueezeSignal(
                symbol=symbol,
                decision="DISTRIBUTION_TRAP",
                passed_all_gates=False,
                rejection_reason="Blocked by microstructure defense (iceberg seller distribution detected)",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                compression_profile=profile, volume_multiple=None
            )

        # 5. Breakout Trigger: Price must close above compression high
        if curr_close <= profile.compression_range_high:
            return VolatilitySqueezeSignal(
                symbol=symbol,
                decision="NO_BREAKOUT",
                passed_all_gates=False,
                rejection_reason=f"Close {curr_close:.2f} has not broken above compression high {profile.compression_range_high:.2f}",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                compression_profile=profile, volume_multiple=None
            )

        # 6. Volume Expansion Confirmation
        vol_multiple = curr_vol / float(hist_median_volume_15m) if hist_median_volume_15m > 0 else 1.0
        if vol_multiple < self.min_volume_mult:
            return VolatilitySqueezeSignal(
                symbol=symbol,
                decision="VOLUME_INSUFFICIENT",
                passed_all_gates=False,
                rejection_reason=f"Breakout volume multiple {vol_multiple:.2f}x below floor {self.min_volume_mult:.2f}x",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                compression_profile=profile, volume_multiple=round(vol_multiple, 2)
            )

        # 7. Sizing & Risk Calculation
        # Stop loss placed at compression midpoint (tight, structural)
        stop_price = round(profile.compression_midpoint, 2)
        risk_per_share = curr_close - stop_price
        risk_pct = (risk_per_share / curr_close) * 100.0

        if risk_per_share <= 0 or risk_pct < 0.40:
            return VolatilitySqueezeSignal(
                symbol=symbol,
                decision="DEGENERATE_STOP",
                passed_all_gates=False,
                rejection_reason=f"Stop distance {risk_pct:.2f}% too tight (<0.40%)",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                compression_profile=profile, volume_multiple=round(vol_multiple, 2)
            )

        shares = int(self.risk_budget_rs // risk_per_share)
        if shares <= 0:
            return VolatilitySqueezeSignal(
                symbol=symbol,
                decision="SIZING_ZERO",
                passed_all_gates=False,
                rejection_reason="Position sizing resulted in 0 shares",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                compression_profile=profile, volume_multiple=round(vol_multiple, 2)
            )

        notional_rs = shares * curr_close
        if notional_rs > self.max_notional_rs:
            shares = int(self.max_notional_rs // curr_close)
            notional_rs = shares * curr_close

        actual_risk = shares * risk_per_share
        t1_price = round(curr_close + (self.r_multiple_t1 * risk_per_share), 2)
        t2_price = round(curr_close + (self.r_multiple_t2 * risk_per_share), 2)

        return VolatilitySqueezeSignal(
            symbol=symbol,
            decision="SIGNAL_BUY",
            passed_all_gates=True,
            rejection_reason=None,
            entry_price=round(curr_close, 2),
            stop_price=stop_price,
            target_tranche1=t1_price,
            target_tranche2=t2_price,
            shares=shares,
            notional_value_rs=round(notional_rs, 2),
            actual_risk_rs=round(actual_risk, 2),
            risk_pct=round(risk_pct, 2),
            compression_profile=profile,
            volume_multiple=round(vol_multiple, 2),
        )
