"""
track2_multi_strategy_engine.py - Multi-Strategy Ensemble & Alpha Ranking Engine
================================================================================
Part of Project Swing Trades (Track 2 Phase 2B).

Architecture:
  - Ensembles multiple independent quantitative alpha strategies:
    1. 15-Minute Opening Range Breakout (ORB)
    2. Intraday VWAP Reclaim & Institutional Continuation
    3. Multi-Day Volatility Squeeze Expansion (NR7 / BB Squeeze)
  - Prioritizes and ranks concurrent signals using a Composite Conviction Score:
    Score = w_strat * StrategyConfidence + 0.30 * VolMultiple + 0.25 * RelativeStrength + 0.15 * OFI
  - Allocates portfolio risk slots (max 3 concurrent positions) adhering to
    strict sector diversification (max 2 per sector) and ₹1,500 rupee risk budget.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from enum import Enum
import math
import numbers
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from antigravity.models.track2_alpha_engine import MultiTimeframeAlphaEngine, AlphaEvaluationResult
from antigravity.models.track2_vwap_reclaim_strategy import VWAPReclaimStrategy, VWAPReclaimSignal
from antigravity.models.track2_volatility_squeeze_strategy import VolatilitySqueezeStrategy, VolatilitySqueezeSignal


class StrategyType(str, Enum):
    ORB_MOMENTUM = "ORB_MOMENTUM"
    VWAP_RECLAIM = "VWAP_RECLAIM"
    VOLATILITY_SQUEEZE = "VOLATILITY_SQUEEZE"


@dataclass(frozen=True)
class UnifiedTradeSignal:
    symbol: str
    strategy_type: str
    conviction_score: float
    entry_price: float
    stop_price: float
    target_tranche1: float
    target_tranche2: float
    shares: int
    notional_value_rs: float
    actual_risk_rs: float
    risk_pct: float
    volume_multiple: float
    sector: str
    details: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MultiStrategyEngine:
    """
    Unified multi-strategy alpha evaluator and portfolio slot allocator.
    """

    def __init__(
        self,
        risk_budget_rs: float = 1500.0,
        max_portfolio_slots: int = 3,
        max_per_sector: int = 2,
    ):
        self.risk_budget_rs = risk_budget_rs
        self.max_portfolio_slots = max_portfolio_slots
        self.max_per_sector = max_per_sector

        # Instantiate sub-strategies
        self.orb_engine = MultiTimeframeAlphaEngine()
        self.vwap_engine = VWAPReclaimStrategy(risk_budget_rs=risk_budget_rs)
        self.squeeze_engine = VolatilitySqueezeStrategy(risk_budget_rs=risk_budget_rs)

    def evaluate_symbol(
        self,
        symbol: str,
        sector: str,
        candles_15m: Sequence[Mapping[str, Any]],
        daily_candles: Optional[Sequence[Mapping[str, Any]]],
        hist_median_volume_15m: float,
        daily_ema20: float,
        daily_ema50: float,
        atr14_points: float,
        depth_snapshots: Optional[Sequence[Any]] = None,
        relative_strength: float = 0.0,
    ) -> List[UnifiedTradeSignal]:
        """
        Evaluates all strategies concurrently for a single candidate symbol.
        Returns a list of qualifying UnifiedTradeSignal instances.
        """
        signals: List[UnifiedTradeSignal] = []

        # 1. Microstructure Defense Check (Iceberg Distribution Trap)
        is_trap = False
        norm_ofi = 0.0
        if depth_snapshots and len(candles_15m) > 0:
            breakout_lvl = float(candles_15m[0].get("high", 0.0))
            defense = self.orb_engine.evaluate_microstructure_defense(
                depth_snapshots, breakout_level=breakout_lvl
            )
            is_trap = defense.get("is_distribution_trap", False)
            norm_ofi = float(defense.get("normalized_ofi", 0.0))

        # -------------------------------------------------------------
        # Strategy 1: ORB Momentum
        # -------------------------------------------------------------
        if len(candles_15m) >= 2:
            orb_res = self.orb_engine.evaluate_candidate(
                symbol=symbol,
                candles_15m=candles_15m,
                hist_median_volume_15m=hist_median_volume_15m,
                daily_ema20=daily_ema20,
                daily_ema50=daily_ema50,
                atr14_points=atr14_points,
                market_regime_allows_orb=True,
                depth_snapshots=depth_snapshots,
            )
            if orb_res.passed_all_gates and orb_res.entry_price and orb_res.stop_price and orb_res.shares:
                entry = orb_res.entry_price
                stop = orb_res.stop_price
                shares = orb_res.shares
                risk_per_share = entry - stop
                risk_pct = (risk_per_share / entry) * 100.0 if entry > 0 else 0.0
                vol_mult = orb_res.volume_multiple or 1.0

                # Conviction Score calculation: 30% Strategy Base (0.80) + 30% VolMult + 25% RS + 15% OFI
                conviction = round(
                    (0.30 * 0.80)
                    + (0.30 * min(3.0, vol_mult) / 3.0)
                    + (0.25 * math.tanh(relative_strength / 10.0))
                    + (0.15 * max(0.0, norm_ofi)),
                    3,
                )
                t1 = round(entry + (1.5 * risk_per_share), 2)
                t2 = round(entry + (3.0 * risk_per_share), 2)

                signals.append(
                    UnifiedTradeSignal(
                        symbol=symbol,
                        strategy_type=StrategyType.ORB_MOMENTUM.value,
                        conviction_score=conviction,
                        entry_price=entry,
                        stop_price=stop,
                        target_tranche1=t1,
                        target_tranche2=t2,
                        shares=shares,
                        notional_value_rs=orb_res.notional_value_rs or (shares * entry),
                        actual_risk_rs=orb_res.actual_risk_rs or (shares * risk_per_share),
                        risk_pct=round(risk_pct, 2),
                        volume_multiple=round(vol_mult, 2),
                        sector=sector,
                        details=orb_res.to_dict(),
                    )
                )

        # -------------------------------------------------------------
        # Strategy 2: VWAP Reclaim
        # -------------------------------------------------------------
        vwap_res = self.vwap_engine.evaluate(
            symbol=symbol,
            candles_15m=candles_15m,
            hist_median_volume_15m=hist_median_volume_15m,
            daily_ema20=daily_ema20,
            daily_ema50=daily_ema50,
            atr14_points=atr14_points,
            microstructure_trap=is_trap,
        )
        if vwap_res.passed_all_gates and vwap_res.entry_price and vwap_res.stop_price and vwap_res.shares:
            vol_mult = vwap_res.volume_multiple or 1.0
            conviction = round(
                (0.35 * 0.90)  # VWAP reclaims carry high institutional weight
                + (0.30 * min(3.0, vol_mult) / 3.0)
                + (0.20 * math.tanh(relative_strength / 10.0))
                + (0.15 * max(0.0, norm_ofi)),
                3,
            )
            signals.append(
                UnifiedTradeSignal(
                    symbol=symbol,
                    strategy_type=StrategyType.VWAP_RECLAIM.value,
                    conviction_score=conviction,
                    entry_price=vwap_res.entry_price,
                    stop_price=vwap_res.stop_price,
                    target_tranche1=vwap_res.target_tranche1 or 0.0,
                    target_tranche2=vwap_res.target_tranche2 or 0.0,
                    shares=vwap_res.shares,
                    notional_value_rs=vwap_res.notional_value_rs or 0.0,
                    actual_risk_rs=vwap_res.actual_risk_rs or 0.0,
                    risk_pct=vwap_res.risk_pct or 0.0,
                    volume_multiple=round(vol_mult, 2),
                    sector=sector,
                    details=vwap_res.to_dict(),
                )
            )

        # -------------------------------------------------------------
        # Strategy 3: Volatility Squeeze Expansion
        # -------------------------------------------------------------
        if daily_candles and len(daily_candles) >= 7:
            squeeze_res = self.squeeze_engine.evaluate(
                symbol=symbol,
                daily_candles=daily_candles,
                candles_15m=candles_15m,
                hist_median_volume_15m=hist_median_volume_15m,
                daily_ema20=daily_ema20,
                daily_ema50=daily_ema50,
                microstructure_trap=is_trap,
            )
            if squeeze_res.passed_all_gates and squeeze_res.entry_price and squeeze_res.stop_price and squeeze_res.shares:
                vol_mult = squeeze_res.volume_multiple or 1.0
                comp_score = squeeze_res.compression_profile.compression_score if squeeze_res.compression_profile else 0.5
                conviction = round(
                    (0.35 * (0.75 + 0.25 * comp_score))
                    + (0.30 * min(3.0, vol_mult) / 3.0)
                    + (0.20 * math.tanh(relative_strength / 10.0))
                    + (0.15 * max(0.0, norm_ofi)),
                    3,
                )
                signals.append(
                    UnifiedTradeSignal(
                        symbol=symbol,
                        strategy_type=StrategyType.VOLATILITY_SQUEEZE.value,
                        conviction_score=conviction,
                        entry_price=squeeze_res.entry_price,
                        stop_price=squeeze_res.stop_price,
                        target_tranche1=squeeze_res.target_tranche1 or 0.0,
                        target_tranche2=squeeze_res.target_tranche2 or 0.0,
                        shares=squeeze_res.shares,
                        notional_value_rs=squeeze_res.notional_value_rs or 0.0,
                        actual_risk_rs=squeeze_res.actual_risk_rs or 0.0,
                        risk_pct=squeeze_res.risk_pct or 0.0,
                        volume_multiple=round(vol_mult, 2),
                        sector=sector,
                        details=squeeze_res.to_dict(),
                    )
                )

        return signals

    def rank_and_allocate(
        self,
        candidate_signals: Sequence[UnifiedTradeSignal],
        existing_sector_counts: Optional[Mapping[str, int]] = None,
        available_slots: Optional[int] = None,
    ) -> List[UnifiedTradeSignal]:
        """
        Ranks all incoming signals across all strategies by conviction score,
        enforcing sector concentration limits (max 2 per sector) and slot availability.
        """
        slots = self.max_portfolio_slots if available_slots is None else min(self.max_portfolio_slots, available_slots)
        if slots <= 0 or not candidate_signals:
            return []

        # Sort descending by conviction score
        sorted_signals = sorted(candidate_signals, key=lambda s: s.conviction_score, reverse=True)

        selected: List[UnifiedTradeSignal] = []
        sector_counts: Dict[str, int] = dict(existing_sector_counts or {})
        seen_symbols = set()

        for sig in sorted_signals:
            if len(selected) >= slots:
                break

            if sig.symbol in seen_symbols:
                continue

            current_sec_count = sector_counts.get(sig.sector, 0)
            if current_sec_count >= self.max_per_sector:
                continue

            selected.append(sig)
            seen_symbols.add(sig.symbol)
            sector_counts[sig.sector] = current_sec_count + 1

        return selected
