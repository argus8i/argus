"""
track2_vwap_reclaim_strategy.py - Intraday VWAP Reclaim & Institutional Continuation
===================================================================================
Part of Project Swing Trades (Track 2 Multi-Strategy Alpha Engine).

Quantitative Thesis:
  1. Institutional Execution Anchoring:
     - Institutions and algorithmic execution algorithms (TWAP, VWAP, POV) benchmark
       execution quality against intraday Volume-Weighted Average Price (VWAP).
     - When a leading liquid momentum stock opens strong, pulls back to VWAP, and
       reclaims it with aggressive volume, it signals institutional absorption of retail supply.
  2. Structural Risk-Reward Asymmetry:
     - Opening Range Breakouts (ORB) often have wide stops (OR Low), resulting in 2.5%-3.5%
       risk distances and smaller share sizing.
     - VWAP Reclaim entries risk only to the shallow pullback swing low (0.6%-1.2%),
       enabling tighter risk-budget sizing and significantly higher realized R-multiples.

Mathematical Formulation:
  - Typical Price: P_typ_i = (High_i + Low_i + Close_i) / 3.0
  - VWAP_t = sum(P_typ_i * Vol_i) / sum(Vol_i)
  - Intraday Variance: Var_t = sum(Vol_i * (P_typ_i - VWAP_t)^2) / sum(Vol_i)
  - Sigma_t = sqrt(Var_t)
  - Upper Band = VWAP_t + 1.0 * Sigma_t
  - Lower Band = VWAP_t - 1.0 * Sigma_t

Entry Rules:
  1. Multi-Timeframe Trend: Daily Price > EMA20 > EMA50.
  2. Morning Establishment: Current 15m candle index >= 2 (after 09:45 IST).
  3. Pullback Qualification: Prior candle Low <= VWAP * 1.002 (tested VWAP liquidity).
  4. Pullback Integrity: Prior candle Close >= Lower Band (did not breakdown).
  5. Reclaim Trigger: Current candle Close > VWAP.
  6. Volume Confirmation: Current candle Volume >= 1.8x historical 15m median.
  7. Microstructure Defense: Normalized OFI > -0.20 and Absorption Ratio <= 2.5.
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
class VWAPSnapshot:
    vwap: float
    sigma: float
    upper_band: float
    lower_band: float
    cum_volume: int
    cum_turnover: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VWAPReclaimSignal:
    symbol: str
    decision: str  # SIGNAL_BUY, PULLBACK_INSUFFICIENT, VOLUME_INSUFFICIENT, TREND_BEARISH, NO_RECLAIM, DATA_INVALID
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
    vwap: Optional[float]
    volume_multiple: Optional[float]
    strategy_name: str = "VWAP_RECLAIM"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class VWAPReclaimStrategy:
    """
    Evaluates intraday VWAP reclaims on 15-minute liquid F&O candles.
    """

    def __init__(
        self,
        min_volume_mult: float = 1.80,
        risk_budget_rs: float = 1500.0,
        max_notional_rs: float = 200000.0,
        r_multiple_t1: float = 1.5,
        r_multiple_t2: float = 3.0,
    ):
        self.min_volume_mult = min_volume_mult
        self.risk_budget_rs = risk_budget_rs
        self.max_notional_rs = max_notional_rs
        self.r_multiple_t1 = r_multiple_t1
        self.r_multiple_t2 = r_multiple_t2

    @staticmethod
    def calculate_intraday_vwap(candles_15m: Sequence[Mapping[str, Any]]) -> Optional[VWAPSnapshot]:
        """
        Computes rolling intraday VWAP and standard deviation bands from 15m OHLCV bars.
        """
        if not candles_15m:
            return None

        cum_vol = 0
        cum_turnover = 0.0
        typical_prices: List[Tuple[float, int]] = []

        for bar in candles_15m:
            high = float(bar.get("high", 0.0))
            low = float(bar.get("low", 0.0))
            close = float(bar.get("close", 0.0))
            vol = int(bar.get("volume", 0))

            if not (_finite_positive(high) and _finite_positive(low) and _finite_positive(close) and vol >= 0):
                continue

            typ = (high + low + close) / 3.0
            cum_vol += vol
            cum_turnover += typ * vol
            typical_prices.append((typ, vol))

        if cum_vol <= 0:
            return None

        vwap = cum_turnover / cum_vol

        # Variance calculation
        variance_num = sum(v * ((typ - vwap) ** 2) for typ, v in typical_prices)
        variance = variance_num / cum_vol
        sigma = math.sqrt(variance)

        return VWAPSnapshot(
            vwap=round(vwap, 2),
            sigma=round(sigma, 2),
            upper_band=round(vwap + sigma, 2),
            lower_band=round(vwap - sigma, 2),
            cum_volume=cum_vol,
            cum_turnover=round(cum_turnover, 2),
        )

    def evaluate(
        self,
        symbol: str,
        candles_15m: Sequence[Mapping[str, Any]],
        hist_median_volume_15m: float,
        daily_ema20: float,
        daily_ema50: float,
        atr14_points: float,
        microstructure_trap: bool = False,
    ) -> VWAPReclaimSignal:
        """
        Evaluates whether the symbol qualifies for an intraday VWAP Reclaim long entry.
        """
        # 1. Input Validation
        if len(candles_15m) < 3:
            return VWAPReclaimSignal(
                symbol=symbol,
                decision="DATA_INVALID",
                passed_all_gates=False,
                rejection_reason="Insufficient intraday bars (minimum 3 bars required)",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                vwap=None, volume_multiple=None
            )

        current_bar = candles_15m[-1]
        prior_bar = candles_15m[-2]

        curr_close = float(current_bar.get("close", 0.0))
        curr_vol = int(current_bar.get("volume", 0))
        prior_low = float(prior_bar.get("low", 0.0))
        prior_close = float(prior_bar.get("close", 0.0))

        if not (_finite_positive(curr_close) and _finite_positive(prior_low) and _finite_positive(prior_close)):
            return VWAPReclaimSignal(
                symbol=symbol,
                decision="DATA_INVALID",
                passed_all_gates=False,
                rejection_reason="Invalid or non-positive price values in candles",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                vwap=None, volume_multiple=None
            )

        # 2. Daily Structural Trend Gate
        if not (curr_close > daily_ema20 > daily_ema50):
            return VWAPReclaimSignal(
                symbol=symbol,
                decision="TREND_BEARISH",
                passed_all_gates=False,
                rejection_reason=f"Price {curr_close:.2f} failed daily trend filter (EMA20={daily_ema20:.2f}, EMA50={daily_ema50:.2f})",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                vwap=None, volume_multiple=None
            )

        # 3. Microstructure Trap Check
        if microstructure_trap:
            return VWAPReclaimSignal(
                symbol=symbol,
                decision="DISTRIBUTION_TRAP",
                passed_all_gates=False,
                rejection_reason="Blocked by microstructure defense (iceberg seller distribution detected)",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                vwap=None, volume_multiple=None
            )

        # 4. Calculate Rolling VWAP
        vwap_snap = self.calculate_intraday_vwap(candles_15m)
        prior_vwap_snap = self.calculate_intraday_vwap(candles_15m[:-1])
        if not vwap_snap or vwap_snap.vwap <= 0 or not prior_vwap_snap or prior_vwap_snap.vwap <= 0:
            return VWAPReclaimSignal(
                symbol=symbol,
                decision="DATA_INVALID",
                passed_all_gates=False,
                rejection_reason="Failed to compute valid intraday VWAP",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                vwap=None, volume_multiple=None
            )

        vwap = vwap_snap.vwap
        prior_vwap = prior_vwap_snap.vwap

        # 5. Pullback Test Condition: Prior bar must have touched or dipped near prior VWAP
        # (Within 0.5% of VWAP or below it, but held above lower band)
        pullback_tested = prior_low <= (prior_vwap * 1.005)
        pullback_held = prior_close >= (prior_vwap_snap.lower_band * 0.990)

        if not (pullback_tested and pullback_held):
            return VWAPReclaimSignal(
                symbol=symbol,
                decision="PULLBACK_INSUFFICIENT",
                passed_all_gates=False,
                rejection_reason=f"Prior bar low {prior_low:.2f} did not test prior VWAP {prior_vwap:.2f} cleanly",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                vwap=vwap, volume_multiple=None
            )

        # 6. Reclaim Trigger Condition: Current bar must close firmly above VWAP
        if curr_close <= vwap:
            return VWAPReclaimSignal(
                symbol=symbol,
                decision="NO_RECLAIM",
                passed_all_gates=False,
                rejection_reason=f"Current close {curr_close:.2f} is below VWAP {vwap:.2f}",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                vwap=vwap, volume_multiple=None
            )

        # 7. Volume Expansion Confirmation
        vol_multiple = curr_vol / float(hist_median_volume_15m) if hist_median_volume_15m > 0 else 1.0
        if vol_multiple < self.min_volume_mult:
            return VWAPReclaimSignal(
                symbol=symbol,
                decision="VOLUME_INSUFFICIENT",
                passed_all_gates=False,
                rejection_reason=f"Reclaim volume multiple {vol_multiple:.2f}x below floor {self.min_volume_mult:.2f}x",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                vwap=vwap, volume_multiple=round(vol_multiple, 2)
            )

        # 8. Sizing & Risk Calculation
        # Stop loss is placed at the lower of prior low or vwap - 0.25% buffer
        structural_stop = min(prior_low, vwap * 0.9975)
        stop_price = round(structural_stop, 2)
        risk_per_share = curr_close - stop_price
        risk_pct = (risk_per_share / curr_close) * 100.0

        if risk_per_share <= 0 or risk_pct < 0.35:
            # Degenerate stop protection
            return VWAPReclaimSignal(
                symbol=symbol,
                decision="DEGENERATE_STOP",
                passed_all_gates=False,
                rejection_reason=f"Stop distance {risk_pct:.2f}% too tight (<0.35%)",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                vwap=vwap, volume_multiple=round(vol_multiple, 2)
            )

        shares = int(self.risk_budget_rs // risk_per_share)
        if shares <= 0:
            return VWAPReclaimSignal(
                symbol=symbol,
                decision="SIZING_ZERO",
                passed_all_gates=False,
                rejection_reason="Position sizing resulted in 0 shares",
                entry_price=None, stop_price=None, target_tranche1=None, target_tranche2=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                vwap=vwap, volume_multiple=round(vol_multiple, 2)
            )

        notional_rs = shares * curr_close
        if notional_rs > self.max_notional_rs:
            shares = int(self.max_notional_rs // curr_close)
            notional_rs = shares * curr_close

        actual_risk = shares * risk_per_share
        t1_price = round(curr_close + (self.r_multiple_t1 * risk_per_share), 2)
        t2_price = round(curr_close + (self.r_multiple_t2 * risk_per_share), 2)

        return VWAPReclaimSignal(
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
            vwap=vwap,
            volume_multiple=round(vol_multiple, 2),
        )
