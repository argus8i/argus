"""
track2_last_light_strategy.py - LAST LIGHT: Pre-Close Momentum Continuation Strategy
===================================================================================
Part of Project Swing Trades (Track 2 Quantitative Strategy Incubator).
Formulated per ChatGPT/Codex & Claude Tri-Agent Consensus Specification (24 Sep 2026).

Quantitative Thesis:
  - Institutional flow concentration, index rebalancing, and benchmark tracking
    heavily concentrate in the final 90 minutes of the session (14:00–15:10 IST),
    leading into the new NSE 15:15–15:35 Closing Auction Session (CAS).
  - Stocks displaying directional trend persistence throughout the session that break
    out of afternoon consolidation between 14:00 and 14:30 IST experience strong
    continuation into the 15:00 mark.

Regime Filter:
  - ON: Trending regime with Kaufman Efficiency Ratio ER8 >= 0.40.
  - Price must be strictly above intraday VWAP (Close > VWAP).
  - Time Window: 14:00 to 14:30 IST (Bar indices ~19-22).

Pattern Structure:
  1. Afternoon Consolidation: Prior 3-4 bars (12:30–14:00) form a narrow consolidation
     with range <= 1.20 * ATR20.
  2. Breakout Bar: 15m candle between 14:00 and 14:30 closes above the consolidation high.
  3. Volume Confirmation: Relative Volume RVOL >= 1.30 (accelerating institutional participation).

Execution & Invalidation:
  - Entry: Breakout Close (or next executable Ask).
  - Structural Stop: Lowest low of consolidation/breakout bar minus 0.10 * ATR20.
  - Target Tranche 1: Entry + 1.5R (50% position).
  - Target Tranche 2: Entry + 3.0R (50% position).
  - Hard Flat: Mandatory market square-off by 15:10 IST prior to 15:15 CAS.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import time as dtime, datetime
import math
import numbers
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from antigravity.models.track2_shared_features import BarFeatures, SharedFeatureEngine


@dataclass(frozen=True)
class LastLightSignal:
    symbol: str
    decision: str  # SIGNAL_BUY, TIME_WINDOW_BLOCKED, REGIME_BLOCKED, NO_PATTERN, VOLUME_INSUFFICIENT, COST_HURDLE_FAILED, DATA_INVALID
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
    hard_flat_time: str = "15:10:00"
    strategy_name: str = "LAST_LIGHT"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class LastLightStrategy:
    """
    Implements LAST LIGHT pre-close momentum continuation strategy for Track 2.
    """

    def __init__(
        self,
        risk_budget_rs: float = 1500.0,
        max_slot_notional: float = 58333.0,
        min_er8: float = 0.40,
        min_rvol: float = 1.30,
        max_risk_pct: float = 2.50,
        min_risk_pct: float = 0.50,
    ):
        self.risk_budget_rs = risk_budget_rs
        self.max_slot_notional = max_slot_notional
        self.min_er8 = min_er8
        self.min_rvol = min_rvol
        self.max_risk_pct = max_risk_pct
        self.min_risk_pct = min_risk_pct

    def evaluate_setup(
        self,
        symbol: str,
        candles_15m: Sequence[Mapping[str, Any]],
        bucket_median_vol: float,
        current_time_ist: Optional[dtime] = None,
        market_breadth: Optional[float] = None,
    ) -> LastLightSignal:
        """
        Evaluates LAST LIGHT pre-close continuation pattern.
        """
        if not symbol or not candles_15m or len(candles_15m) < 8:
            return LastLightSignal(
                symbol=symbol or "UNKNOWN",
                decision="DATA_INVALID",
                passed_all_gates=False,
                rejection_reason="Insufficient candle history (minimum 8 15m bars required)",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
            )

        latest_bar = candles_15m[-1]
        ts_str = str(latest_bar.get("timestamp", ""))

        # 1. Time Window Check (14:00 to 14:30 IST)
        eval_time = current_time_ist
        if eval_time is None and ts_str:
            try:
                # Parse timestamp e.g. "2026-09-24T14:15:00+05:30" or "14:15:00"
                if "T" in ts_str:
                    time_part = ts_str.split("T")[1][:5]
                else:
                    time_part = ts_str[:5]
                hh, mm = map(int, time_part.split(":"))
                eval_time = dtime(hh, mm)
            except Exception:
                eval_time = None

        if eval_time is not None:
            # Valid execution window is 14:00 to 14:30 IST
            if not (dtime(14, 0) <= eval_time <= dtime(14, 30)):
                return LastLightSignal(
                    symbol=symbol,
                    decision="TIME_WINDOW_BLOCKED",
                    passed_all_gates=False,
                    rejection_reason=f"Current time {eval_time.strftime('%H:%M')} outside 14:00-14:30 IST window",
                    entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                    shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                )

        features = SharedFeatureEngine.extract_features(symbol, candles_15m, bucket_median_vol)
        if not features:
            return LastLightSignal(
                symbol=symbol,
                decision="DATA_INVALID",
                passed_all_gates=False,
                rejection_reason="Failed to extract valid bar features",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
            )

        # 2. Regime Gate: Trend Efficiency & VWAP Positioning
        if features.er8 < self.min_er8:
            return LastLightSignal(
                symbol=symbol,
                decision="REGIME_BLOCKED",
                passed_all_gates=False,
                rejection_reason=f"Trend efficiency ER8={features.er8:.3f} < {self.min_er8} (LAST LIGHT requires directional persistence)",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
            )

        if not features.is_above_vwap:
            return LastLightSignal(
                symbol=symbol,
                decision="REGIME_BLOCKED",
                passed_all_gates=False,
                rejection_reason=f"Price {features.close_p:.2f} <= VWAP {features.session_vwap:.2f} (LAST LIGHT requires long above VWAP)",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
            )

        # 3. Afternoon Consolidation Breakout Check
        # Inspect prior 3 bars for afternoon consolidation
        consolidation_bars = candles_15m[-5:-1]
        if len(consolidation_bars) < 3:
            return LastLightSignal(
                symbol=symbol,
                decision="NO_PATTERN",
                passed_all_gates=False,
                rejection_reason="Insufficient consolidation bars preceding breakout",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
            )

        consol_high = max(float(b.get("high", 0.0)) for b in consolidation_bars)
        consol_low = min(float(b.get("low", 0.0)) for b in consolidation_bars)
        consol_range = consol_high - consol_low

        # Compression check on consolidation: range <= 1.20 * ATR20
        if consol_range > 1.20 * features.atr20:
            return LastLightSignal(
                symbol=symbol,
                decision="NO_PATTERN",
                passed_all_gates=False,
                rejection_reason=f"Consolidation range {consol_range:.2f} > 1.20*ATR20 ({1.20*features.atr20:.2f})",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
            )

        # Breakout: Current bar closes strictly above consolidation high
        breakout_close = features.close_p
        if breakout_close <= consol_high:
            return LastLightSignal(
                symbol=symbol,
                decision="NO_PATTERN",
                passed_all_gates=False,
                rejection_reason=f"Close {breakout_close:.2f} <= consolidation high {consol_high:.2f}",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
            )

        # 4. Volume Confirmation (RVOL >= 1.30)
        if features.rvol < self.min_rvol:
            return LastLightSignal(
                symbol=symbol,
                decision="VOLUME_INSUFFICIENT",
                passed_all_gates=False,
                rejection_reason=f"Breakout RVOL={features.rvol:.2f} < {self.min_rvol:.2f}",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
            )

        # 5. Risk & Structural Stop Formulation
        entry_price = round(breakout_close, 2)
        # Structural stop: lowest low of recent consolidation minus buffer
        raw_stop = consol_low - (0.10 * features.atr20)
        stop_price = round(raw_stop, 2)

        if stop_price >= entry_price:
            return LastLightSignal(
                symbol=symbol,
                decision="NO_PATTERN",
                passed_all_gates=False,
                rejection_reason="Stop price invalidly above or equal to entry",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
            )

        risk_per_sh = round(entry_price - stop_price, 2)
        risk_pct = round((risk_per_sh / entry_price) * 100.0, 2)

        if not (self.min_risk_pct <= risk_pct <= self.max_risk_pct):
            return LastLightSignal(
                symbol=symbol,
                decision="REGIME_BLOCKED",
                passed_all_gates=False,
                rejection_reason=f"Risk {risk_pct:.2f}% outside permissible [{self.min_risk_pct}%, {self.max_risk_pct}%]",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
            )

        # 6. Position Sizing
        shares_by_risk = math.floor(self.risk_budget_rs / risk_per_sh)
        shares_by_notional = math.floor(self.max_slot_notional / entry_price)
        shares = min(shares_by_risk, shares_by_notional)

        if shares <= 0:
            return LastLightSignal(
                symbol=symbol,
                decision="REGIME_BLOCKED",
                passed_all_gates=False,
                rejection_reason="Position sizing resulted in 0 shares",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
            )

        notional_val = round(shares * entry_price, 2)
        actual_risk = round(shares * risk_per_sh, 2)

        # 7. Economic Friction Hurdle (0.106% round trip)
        target1 = round(entry_price + (1.5 * risk_per_sh), 2)
        target2 = round(entry_price + (3.0 * risk_per_sh), 2)
        gross_target_gain = round(shares * 1.5 * risk_per_sh, 2)
        estimated_friction = round(notional_val * 0.00106, 2)  # Entry + exit round trip
        if gross_target_gain < 3.0 * estimated_friction:
            return LastLightSignal(
                symbol=symbol,
                decision="COST_HURDLE_FAILED",
                passed_all_gates=False,
                rejection_reason=f"Gross target gain Rs {gross_target_gain} < 3x round-trip friction Rs {3.0*estimated_friction:.2f}",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
            )

        return LastLightSignal(
            symbol=symbol,
            decision="SIGNAL_BUY",
            passed_all_gates=True,
            rejection_reason=None,
            entry_price=entry_price,
            stop_price=stop_price,
            target_tranche1=target1,
            target_tranche2=target2,
            shares=shares,
            notional_value_rs=notional_val,
            actual_risk_rs=actual_risk,
            risk_pct=risk_pct,
            hard_flat_time="15:10:00",
        )
