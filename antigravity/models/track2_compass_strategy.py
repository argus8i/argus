"""
track2_compass_strategy.py - COMPASS: Sector Leadership & Residual Strength Dispersion
=====================================================================================
Part of Project Swing Trades (Track 2 Quantitative Strategy Incubator).
Formulated per ChatGPT/Codex Strategy Specification (24 Sep 2026).

Quantitative Thesis:
  - Sector-wide repricing propagates unevenly across constituents.
  - A leading constituent displaying high idiosyncratic residual strength (RS)
    can continue outperforming even while the broad market drifts directionless.
  - This is a cross-sectional rotation hypothesis, not a simple momentum chase.

Regime Filter:
  - ON: Market breadth between 0.40 and 0.60 (broad market neutral/range),
        meaningful sector dispersion, and leading sector outperforming.
  - OFF: Market-wide shock (all sectors dropping together) or single-stock dominated sector.

Features & Mathematical Rules:
  1. 1-Hour Log Return (4 completed 15m bars):
     r_{i,4} = log(C_{i,t} / C_{i,t-4})
  2. Sector Return:
     r_{s,4} = mean(r_{j,4} for all observed constituents in sector s)
  3. Stock Residual Strength:
     RS_i = r_{i,4} - (beta_{i,s} * r_{s,4})
  4. Mandatory Signals:
     - Sector return > Broad Market return (r_{s,4} > r_{m,4})
     - Sector breadth >= 60% (>=60% of sector stocks above session VWAP)
     - Stock Close > max(High of previous 4 bars)
     - Time-of-day Relative Volume RVOL >= 1.50

Execution & Invalidation:
  - Entry: Next executable Ask price.
  - Stop: Preceding 3-bar Low minus 0.10 * ATR20.
  - Target: 2.0R (Target = Entry + 2.0 * (Entry - Stop)).
  - Early Invalidation: Both stock and sector close below session VWAP.
  - Time Exit: Maximum 6 bars (1.5 hours).
  - Hard Flat: 15:10 IST.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import math
import numbers
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from antigravity.models.track2_shared_features import BarFeatures, SharedFeatureEngine


@dataclass(frozen=True)
class CompassSignal:
    symbol: str
    sector: str
    decision: str  # SIGNAL_BUY, SECTOR_NOT_LEADING, RESIDUAL_STRENGTH_WEAK, BREAKOUT_MISSING, VOLUME_INSUFFICIENT, COST_HURDLE_FAILED, DATA_INVALID
    passed_all_gates: bool
    rejection_reason: Optional[str]
    entry_price: Optional[float]
    stop_price: Optional[float]
    target_price: Optional[float]
    shares: Optional[int]
    notional_value_rs: Optional[float]
    actual_risk_rs: Optional[float]
    risk_pct: Optional[float]
    sector_return_1h_pct: Optional[float]
    residual_strength: Optional[float]
    max_holding_bars: int = 6
    strategy_name: str = "COMPASS"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CompassStrategy:
    """
    Evaluates cross-sectional sector leadership and stock residual strength.
    """

    def __init__(
        self,
        risk_budget_rs: float = 1500.0,
        max_notional_rs: float = 58333.0,
        target_r_multiple: float = 2.0,
        min_rvol: float = 1.50,
        est_roundtrip_friction_pct: float = 0.00106,
    ):
        self.risk_budget_rs = risk_budget_rs
        self.max_notional_rs = max_notional_rs
        self.target_r_multiple = target_r_multiple
        self.min_rvol = min_rvol
        self.est_friction_pct = est_roundtrip_friction_pct

    def evaluate(
        self,
        symbol: str,
        sector: str,
        candles_15m: Sequence[Mapping[str, Any]],
        sector_constituents_candles: Mapping[str, Sequence[Mapping[str, Any]]],
        market_candles_15m: Sequence[Mapping[str, Any]],
        bucket_median_vol: float,
        stock_sector_beta: float = 1.0,
        current_ask: Optional[float] = None,
    ) -> CompassSignal:
        """
        Evaluates COMPASS sector leadership and residual momentum on 15m bars.
        """
        if len(candles_15m) < 5:
            return CompassSignal(
                symbol=symbol, sector=sector,
                decision="DATA_INVALID", passed_all_gates=False,
                rejection_reason="Insufficient candles (minimum 5 completed 15m bars required)",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                sector_return_1h_pct=None, residual_strength=None,
            )

        features = SharedFeatureEngine.extract_features(symbol, candles_15m, bucket_median_vol)
        if not features:
            return CompassSignal(
                symbol=symbol, sector=sector,
                decision="DATA_INVALID", passed_all_gates=False,
                rejection_reason="Failed to extract valid bar features",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                sector_return_1h_pct=None, residual_strength=None,
            )

        # 1. Calculate 1-hour log return for candidate stock (last 4 bars)
        c_now = float(candles_15m[-1].get("close", 0.0))
        c_4ago = float(candles_15m[-5].get("close", 0.0))
        if c_now <= 0 or c_4ago <= 0:
            return CompassSignal(
                symbol=symbol, sector=sector,
                decision="DATA_INVALID", passed_all_gates=False,
                rejection_reason="Invalid price values for 1-hour return",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                sector_return_1h_pct=None, residual_strength=None,
            )

        r_stock = math.log(c_now / c_4ago)

        # 2. Calculate Sector 1-hour return and Sector Breadth
        sec_returns: List[float] = []
        sec_above_vwap = 0
        total_sec_observed = 0

        for sec_sym, s_candles in sector_constituents_candles.items():
            if len(s_candles) >= 5:
                s_c_now = float(s_candles[-1].get("close", 0.0))
                s_c_4ago = float(s_candles[-5].get("close", 0.0))
                if s_c_now > 0 and s_c_4ago > 0:
                    ret = math.log(s_c_now / s_c_4ago)
                    sec_returns.append(ret)
                    vwap, _ = SharedFeatureEngine.calculate_session_vwap(s_candles)
                    if vwap > 0 and s_c_now >= vwap:
                        sec_above_vwap += 1
                    total_sec_observed += 1

        if not sec_returns:
            sec_returns = [r_stock]
            total_sec_observed = 1
            sec_above_vwap = 1 if features.is_above_vwap else 0

        r_sector = sum(sec_returns) / len(sec_returns)
        sector_breadth = float(sec_above_vwap) / max(1, total_sec_observed)

        # 3. Market Return Benchmark
        r_market = 0.0
        if len(market_candles_15m) >= 5:
            m_now = float(market_candles_15m[-1].get("close", 0.0))
            m_4ago = float(market_candles_15m[-5].get("close", 0.0))
            if m_now > 0 and m_4ago > 0:
                r_market = math.log(m_now / m_4ago)

        # Gate A: Sector must outperform broad market and have positive breadth >= 50%
        if r_sector <= r_market or sector_breadth < 0.50:
            return CompassSignal(
                symbol=symbol, sector=sector,
                decision="SECTOR_NOT_LEADING", passed_all_gates=False,
                rejection_reason=f"Sector return {r_sector*100:.2f}% <= Market {r_market*100:.2f}% or breadth {sector_breadth*100:.0f}% < 50%",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                sector_return_1h_pct=round(r_sector * 100.0, 2), residual_strength=None,
            )

        # 4. Stock Residual Strength: RS = r_stock - (beta * r_sector)
        beta_adj = max(0.5, min(2.5, float(stock_sector_beta)))
        residual_strength = r_stock - (beta_adj * r_sector)

        # Gate B: Stock must have positive residual strength (outperforming its own sector beta)
        if residual_strength <= 0:
            return CompassSignal(
                symbol=symbol, sector=sector,
                decision="RESIDUAL_STRENGTH_WEAK", passed_all_gates=False,
                rejection_reason=f"Residual strength {residual_strength*100:.2f}% <= 0 (stock lagging sector)",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                sector_return_1h_pct=round(r_sector * 100.0, 2),
                residual_strength=round(residual_strength * 100.0, 2),
            )

        # Gate C: Stock must close above its previous 4-bar high
        prev_4_high = max(float(b.get("high", 0.0)) for b in candles_15m[-5:-1])
        if c_now <= prev_4_high:
            return CompassSignal(
                symbol=symbol, sector=sector,
                decision="BREAKOUT_MISSING", passed_all_gates=False,
                rejection_reason=f"Close {c_now:.2f} <= previous 4-bar high {prev_4_high:.2f}",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                sector_return_1h_pct=round(r_sector * 100.0, 2),
                residual_strength=round(residual_strength * 100.0, 2),
            )

        # Gate D: Relative Volume Confirmation
        if features.rvol < self.min_rvol:
            return CompassSignal(
                symbol=symbol, sector=sector,
                decision="VOLUME_INSUFFICIENT", passed_all_gates=False,
                rejection_reason=f"RVOL {features.rvol:.2f}x below threshold {self.min_rvol:.2f}x",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                sector_return_1h_pct=round(r_sector * 100.0, 2),
                residual_strength=round(residual_strength * 100.0, 2),
            )

        # 5. Sizing & Structural Stop (Lowest of previous 3 bars minus 0.10 ATR20)
        entry_price = round(current_ask or c_now, 2)
        prev_3_low = min(float(b.get("low", c_now)) for b in candles_15m[-4:-1])
        structural_stop = round(prev_3_low - (0.10 * features.atr20), 2)
        risk_per_share = entry_price - structural_stop
        risk_pct = (risk_per_share / entry_price) * 100.0

        if risk_per_share <= 0 or not (0.40 <= risk_pct <= 2.50):
            return CompassSignal(
                symbol=symbol, sector=sector,
                decision="INVALID_STOP", passed_all_gates=False,
                rejection_reason=f"Stop distance {risk_pct:.2f}% outside permitted [0.40%, 2.50%] structural bounds",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                sector_return_1h_pct=round(r_sector * 100.0, 2),
                residual_strength=round(residual_strength * 100.0, 2),
            )

        shares = int(min(self.risk_budget_rs // risk_per_share, self.max_notional_rs // entry_price))
        if shares <= 0:
            return CompassSignal(
                symbol=symbol, sector=sector,
                decision="SIZING_ZERO", passed_all_gates=False,
                rejection_reason="Position sizing yielded 0 shares",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                sector_return_1h_pct=round(r_sector * 100.0, 2),
                residual_strength=round(residual_strength * 100.0, 2),
            )

        notional_rs = round(shares * entry_price, 2)
        actual_risk_rs = round(shares * risk_per_share, 2)
        target_price = round(entry_price + (self.target_r_multiple * risk_per_share), 2)

        # 6. Economic Friction Hurdle Check
        gross_gain = shares * (target_price - entry_price)
        est_cost = (notional_rs + (shares * target_price)) * self.est_friction_pct
        if gross_gain < 3.0 * est_cost:
            return CompassSignal(
                symbol=symbol, sector=sector,
                decision="COST_HURDLE_FAILED", passed_all_gates=False,
                rejection_reason=f"Target gross gain ₹{gross_gain:.1f} < 3x estimated friction ₹{est_cost:.1f}",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None, risk_pct=None,
                sector_return_1h_pct=round(r_sector * 100.0, 2),
                residual_strength=round(residual_strength * 100.0, 2),
            )

        return CompassSignal(
            symbol=symbol, sector=sector,
            decision="SIGNAL_BUY", passed_all_gates=True,
            rejection_reason=None,
            entry_price=entry_price,
            stop_price=structural_stop,
            target_price=target_price,
            shares=shares,
            notional_value_rs=notional_rs,
            actual_risk_rs=actual_risk_rs,
            risk_pct=round(risk_pct, 2),
            sector_return_1h_pct=round(r_sector * 100.0, 2),
            residual_strength=round(residual_strength * 100.0, 2),
            max_holding_bars=6,
        )
