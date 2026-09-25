"""
track2_alpha_engine.py - Multi-Timeframe Quantitative Momentum & ORB Alpha Engine
=================================================================================
Part of Project Swing Trades (Track 2 Phase 2B).

Mandate & Mathematical Formulation:
  1. Multi-Timeframe Trend Alignment:
     - Daily Trend Filter: Price > Daily EMA_20 > Daily EMA_50.
  2. 15-Minute Opening Range Breakout (ORB):
     - Window: 09:30 to 14:30 IST.
     - Signal Condition: 15m Candle Close > OR High.
  3. Dynamic Volume Expansion Gating:
     - BULLISH_EXPANSION: Volume >= 2.5x of historical 15m bucket median.
     - NEUTRAL_SELECTIVE: Volume >= 3.5x of historical 15m bucket median.
     - DISTRIBUTION_GATED: All entries strictly blocked.
  4. Volatility Extension Ceiling Guard:
     - Rejects entries where Price > OR High + 0.5 * ATR_14 (prevents buying exhaustion spikes).
  5. Degenerate Stop & Minimum Buffer Guards:
     - Rejects entries where OR Low >= OR High or stop distance < 0.5%.
  6. Rupee Risk Budget Sizing:
     - Fixed at Rs 1,500 rupee risk budget with SL-Limit 0.5% buffer baseline.
"""

from __future__ import annotations

from enum import Enum
from dataclasses import dataclass, asdict
import math
import numbers
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from antigravity.models.market_regime_filter import MarketRegimeFilter, MarketRegimeSnapshot, MarketRegimeState
from antigravity.models.liquid_momentum_screener import LiquidMomentumEngine, SizingResult
from antigravity.models.session_manifest import _canonical_json, _sha256_bytes


class CandidateHealthState(str, Enum):
    LEADER_EXPANDING = "LEADER_EXPANDING"
    IN_POSITION = "IN_POSITION"
    DEGRADED_LOW_VOL = "DEGRADED_LOW_VOL"
    RANGE_BOUND_CHOP = "RANGE_BOUND_CHOP"
    EXTENDED_EXHAUSTED = "EXTENDED_EXHAUSTED"


@dataclass(frozen=True)
class AlphaEvaluationResult:
    symbol: str
    decision: str  # PAPER_SIGNAL, IN_RANGE, VOLUME_INSUFFICIENT, OVEREXTENDED_CEILING, DAILY_TREND_BEARISH, REGIME_BLOCKED, DATA_INVALID
    passed_all_gates: bool
    rejection_reason: Optional[str]
    entry_price: Optional[float]
    stop_price: Optional[float]
    target_price: Optional[float]
    shares: Optional[int]
    notional_value_rs: Optional[float]
    actual_risk_rs: Optional[float]
    volume_multiple: Optional[float]
    min_volume_required: Optional[float]
    extension_points: Optional[float]
    max_extension_allowed: Optional[float]
    paper_instruction: Optional[Dict[str, Any]]
    health_state: str = CandidateHealthState.LEADER_EXPANDING.value

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MultiTimeframeAlphaEngine:
    """
    Evaluates multi-timeframe trend alignment, 15m ORB breakouts, volume expansion,
    and extension ceiling invariants to generate rule-bound paper signals.
    """

    @staticmethod
    def evaluate_daily_trend(
        current_price: float,
        daily_ema20: float,
        daily_ema50: float,
        strict_trend: bool = True,
    ) -> Tuple[bool, Optional[str]]:
        """
        Evaluates daily structural trend alignment.
        Strict: Price > EMA20 > EMA50.
        Relaxed: Price > EMA20.
        """
        for val in [current_price, daily_ema20, daily_ema50]:
            if (
                isinstance(val, bool)
                or not isinstance(val, numbers.Real)
                or not math.isfinite(float(val))
                or float(val) <= 0
            ):
                return False, "Invalid daily price or EMA metrics."

        if strict_trend:
            if not (current_price > daily_ema20 > daily_ema50):
                return False, f"Daily trend not aligned: Price ({current_price:.2f}) > EMA20 ({daily_ema20:.2f}) > EMA50 ({daily_ema50:.2f}) failed."
        else:
            if current_price <= daily_ema20:
                return False, f"Price ({current_price:.2f}) below Daily EMA20 ({daily_ema20:.2f})."

        return True, None

    @staticmethod
    def evaluate_15m_orb(
        symbol: str,
        current_price: float,
        or_high: float,
        or_low: float,
        bucket_volume: int,
        historical_bucket_volume_median: int,
        atr14_points: float,
        regime_snapshot: MarketRegimeSnapshot,
        daily_ema20: Optional[float] = None,
        daily_ema50: Optional[float] = None,
        risk_budget_rs: float = 1500.0,
        max_extension_atr_mult: float = 0.5,
        order_rules: Optional[Mapping[str, Any]] = None,
        dtv_med20_cr: float = 30.0,
    ) -> AlphaEvaluationResult:
        """
        Full quantitative evaluation pipeline for 15-minute Opening Range Breakouts.
        """
        sym = str(symbol).strip().upper()

        # 1. Macro Regime Gate
        if not regime_snapshot.allow_standard_orb and regime_snapshot.state == MarketRegimeState.DISTRIBUTION_GATED:
            return AlphaEvaluationResult(
                symbol=sym,
                decision="REGIME_BLOCKED",
                passed_all_gates=False,
                rejection_reason=f"Regime Gated: {regime_snapshot.reason}",
                entry_price=None,
                stop_price=None,
                target_price=None,
                shares=None,
                notional_value_rs=None,
                actual_risk_rs=None,
                volume_multiple=None,
                min_volume_required=None,
                extension_points=None,
                max_extension_allowed=None,
                paper_instruction=None,
                health_state=CandidateHealthState.RANGE_BOUND_CHOP.value,
            )

        # 2. Daily Structural Trend Gate (if EMAs provided)
        if daily_ema20 is not None and daily_ema50 is not None:
            trend_ok, trend_reason = MultiTimeframeAlphaEngine.evaluate_daily_trend(
                current_price, daily_ema20, daily_ema50, strict_trend=True
            )
            if not trend_ok:
                return AlphaEvaluationResult(
                    symbol=sym,
                    decision="DAILY_TREND_BEARISH",
                    passed_all_gates=False,
                    rejection_reason=trend_reason,
                    entry_price=None,
                    stop_price=None,
                    target_price=None,
                    shares=None,
                    notional_value_rs=None,
                    actual_risk_rs=None,
                    volume_multiple=None,
                    min_volume_required=None,
                    extension_points=None,
                    max_extension_allowed=None,
                    paper_instruction=None,
                    health_state=CandidateHealthState.DEGRADED_LOW_VOL.value,
                )

        # 3. Degenerate Stop & Range Guards
        if or_low >= or_high:
            return AlphaEvaluationResult(
                symbol=sym,
                decision="DEGENERATE_STOP",
                passed_all_gates=False,
                rejection_reason=f"Degenerate range: OR Low ({or_low:.2f}) >= OR High ({or_high:.2f}).",
                entry_price=None,
                stop_price=None,
                target_price=None,
                shares=None,
                notional_value_rs=None,
                actual_risk_rs=None,
                volume_multiple=None,
                min_volume_required=None,
                extension_points=None,
                max_extension_allowed=None,
                paper_instruction=None,
                health_state=CandidateHealthState.RANGE_BOUND_CHOP.value,
            )

        # 4. Breakout Condition Check (Price > OR High)
        if current_price <= or_high:
            return AlphaEvaluationResult(
                symbol=sym,
                decision="IN_RANGE",
                passed_all_gates=False,
                rejection_reason=f"Price ({current_price:.2f}) inside or below OR High ({or_high:.2f}).",
                entry_price=None,
                stop_price=None,
                target_price=None,
                shares=None,
                notional_value_rs=None,
                actual_risk_rs=None,
                volume_multiple=None,
                min_volume_required=None,
                extension_points=None,
                max_extension_allowed=None,
                paper_instruction=None,
                health_state=CandidateHealthState.RANGE_BOUND_CHOP.value,
            )

        # 5. Volatility Extension Ceiling Guard
        max_allowed_extension = round(max_extension_atr_mult * atr14_points, 2)
        actual_extension = round(current_price - or_high, 2)
        if actual_extension > max_allowed_extension:
            return AlphaEvaluationResult(
                symbol=sym,
                decision="OVEREXTENDED_CEILING",
                passed_all_gates=False,
                rejection_reason=(
                    f"Overextended: Price ({current_price:.2f}) exceeds OR High ({or_high:.2f}) "
                    f"by {actual_extension:.2f} pts (max allowed: {max_allowed_extension:.2f} pts = {max_extension_atr_mult}x ATR)."
                ),
                entry_price=current_price,
                stop_price=or_low,
                target_price=None,
                shares=None,
                notional_value_rs=None,
                actual_risk_rs=None,
                volume_multiple=None,
                min_volume_required=None,
                extension_points=actual_extension,
                max_extension_allowed=max_allowed_extension,
                paper_instruction=None,
                health_state=CandidateHealthState.EXTENDED_EXHAUSTED.value,
            )

        # 6. Dynamic Volume Expansion Multiple Check
        min_vol_multiple = regime_snapshot.min_volume_multiple
        vol_multiple = round(bucket_volume / float(historical_bucket_volume_median), 2) if historical_bucket_volume_median > 0 else 0.0

        if vol_multiple < min_vol_multiple:
            return AlphaEvaluationResult(
                symbol=sym,
                decision="VOLUME_INSUFFICIENT",
                passed_all_gates=False,
                rejection_reason=(
                    f"Volume multiple {vol_multiple:.2f}x below regime requirement {min_vol_multiple:.2f}x."
                ),
                entry_price=current_price,
                stop_price=or_low,
                target_price=None,
                shares=None,
                notional_value_rs=None,
                actual_risk_rs=None,
                volume_multiple=vol_multiple,
                min_volume_required=min_vol_multiple,
                extension_points=actual_extension,
                max_extension_allowed=max_allowed_extension,
                paper_instruction=None,
                health_state=CandidateHealthState.DEGRADED_LOW_VOL.value,
            )

        # 7. Position Sizing (Strict Rupee Risk Budget)
        sizing = LiquidMomentumEngine.calculate_position_size(
            entry_price=current_price,
            or_low=or_low,
            atr14=atr14_points,
            dtv_med20_cr=dtv_med20_cr,
            risk_budget_rs=risk_budget_rs,
            order_execution_type="SL_LIMIT",
        )

        if sizing.shares <= 0:
            return AlphaEvaluationResult(
                symbol=sym,
                decision="SIZING_REJECTED",
                passed_all_gates=False,
                rejection_reason="Position sizing produced zero shares.",
                entry_price=current_price,
                stop_price=or_low,
                target_price=None,
                shares=0,
                notional_value_rs=0.0,
                actual_risk_rs=0.0,
                volume_multiple=vol_multiple,
                min_volume_required=min_vol_multiple,
                extension_points=actual_extension,
                max_extension_allowed=max_allowed_extension,
                paper_instruction=None,
                health_state=CandidateHealthState.RANGE_BOUND_CHOP.value,
            )

        # 8. Construct Rule-Bound Paper Instruction Envelope
        rules_dict = dict(order_rules or {"risk_rs": risk_budget_rs, "strategy": "15M_ORB_MOMENTUM"})
        instruction = {
            "symbol": sym,
            "side": "BUY",
            "limit_price": round(current_price, 2),
            "quantity": sizing.shares,
            "stop_price": sizing.stop_price,
            "stop_limit_price": sizing.limit_exit_price,
            "target_price": sizing.target_price,
            "risk_reward_ratio": sizing.risk_reward_ratio,
            "strategy_rules_sha256": _sha256_bytes(_canonical_json(rules_dict)),
            "evidence_class": "E1_BAR_POSSIBLE",
            "fill_claimed": False,
        }

        return AlphaEvaluationResult(
            symbol=sym,
            decision="PAPER_SIGNAL",
            passed_all_gates=True,
            rejection_reason=None,
            entry_price=round(current_price, 2),
            stop_price=sizing.stop_price,
            target_price=sizing.target_price,
            shares=sizing.shares,
            notional_value_rs=sizing.notional_value,
            actual_risk_rs=sizing.actual_risk_rs,
            volume_multiple=vol_multiple,
            min_volume_required=min_vol_multiple,
            extension_points=actual_extension,
            max_extension_allowed=max_allowed_extension,
            paper_instruction=instruction,
            health_state=CandidateHealthState.LEADER_EXPANDING.value,
        )

    @staticmethod
    def evaluate_microstructure_defense(
        depth_snapshots: Sequence[Any],
        breakout_level: float,
        tick: float = 0.05,
    ) -> Dict[str, Any]:
        """
        Claude Pillar 2 Adverse-Selection Defense:
        Evaluates Order Flow Imbalance (OFI) and hidden supply absorption at breakout level.
        Detects iceberg distribution traps (distributors dumping into the breakout).
        """
        try:
            from research.execution_realism.features import ofi_normalized, absorption_at_level
            norm_ofi = ofi_normalized(depth_snapshots)
            abs_info = absorption_at_level(depth_snapshots, breakout_level, tick)
            raw_ratio = abs_info.get("absorption_ratio", abs_info.get("ratio", 0.0))
            if math.isinf(raw_ratio):
                ratio = 999.0
            else:
                ratio = float(raw_ratio) if math.isfinite(raw_ratio) else 0.0
            is_trap = ratio > 2.5
            return {
                "normalized_ofi": round(norm_ofi, 4),
                "absorption_ratio": round(ratio, 2),
                "is_distribution_trap": is_trap,
                "details": abs_info,
            }
        except Exception as exc:
            # Rule 8 v2 Fail-Closed Invariant: error in depth evaluation must fail closed
            return {
                "normalized_ofi": 0.0,
                "absorption_ratio": 0.0,
                "is_distribution_trap": True,
                "data_valid": False,
                "error": str(exc),
            }

    @classmethod
    def evaluate_candidate(
        cls,
        symbol: str,
        candles_15m: Sequence[Mapping[str, Any]],
        hist_median_volume_15m: float,
        daily_ema20: float,
        daily_ema50: float,
        atr14_points: float,
        market_regime_allows_orb: bool = True,
        depth_snapshots: Optional[Sequence[Any]] = None,
        risk_budget_rs: float = 1500.0,
        regime_snapshot: Optional[MarketRegimeSnapshot] = None,
    ) -> AlphaEvaluationResult:
        """
        Adapts sequential 15m candle stream to evaluate_15m_orb.
        Requires at least 2 completed bars:
          - Bar 0 (09:15-09:30): Establishes OR High / OR Low
          - Bar 1..N: Evaluates breakout on the latest completed bar
        Fails closed if historical volume baseline <= 0.
        """
        sym = str(symbol).strip().upper()
        if not candles_15m or len(candles_15m) < 2:
            return AlphaEvaluationResult(
                symbol=sym,
                decision="DATA_INVALID",
                passed_all_gates=False,
                rejection_reason="Insufficient candles (minimum 2 15m bars required for ORB)",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None,
                volume_multiple=None, min_volume_required=None,
                extension_points=None, max_extension_allowed=None,
                paper_instruction=None,
                health_state=CandidateHealthState.RANGE_BOUND_CHOP.value,
            )

        if hist_median_volume_15m is None or hist_median_volume_15m <= 0:
            return AlphaEvaluationResult(
                symbol=sym,
                decision="VOLUME_INSUFFICIENT",
                passed_all_gates=False,
                rejection_reason="Missing historical volume baseline (median <= 0)",
                entry_price=None, stop_price=None, target_price=None,
                shares=None, notional_value_rs=None, actual_risk_rs=None,
                volume_multiple=0.0, min_volume_required=2.5,
                extension_points=None, max_extension_allowed=None,
                paper_instruction=None,
                health_state=CandidateHealthState.RANGE_BOUND_CHOP.value,
            )

        opening_bar = candles_15m[0]
        latest_bar = candles_15m[-1]

        or_high = float(opening_bar.get("high", 0.0))
        or_low = float(opening_bar.get("low", 0.0))
        current_price = float(latest_bar.get("close", 0.0))
        bucket_vol = int(latest_bar.get("volume", 0))

        if regime_snapshot is None:
            regime_snapshot = MarketRegimeSnapshot(
                state=MarketRegimeState.BULLISH_EXPANSION if market_regime_allows_orb else MarketRegimeState.DISTRIBUTION_GATED,
                nifty_ltp=current_price,
                nifty_or_high=or_high,
                nifty_or_low=or_low,
                ad_ratio=1.0,
                advances=None,
                declines=None,
                allow_standard_orb=market_regime_allows_orb,
                min_volume_multiple=2.5,
                reason="Adaptive candidate evaluation (unconfirmed breadth)",
            )

        return cls.evaluate_15m_orb(
            symbol=sym,
            current_price=current_price,
            or_high=or_high,
            or_low=or_low,
            bucket_volume=bucket_vol,
            historical_bucket_volume_median=int(hist_median_volume_15m) if hist_median_volume_15m > 0 else 0,
            atr14_points=atr14_points,
            regime_snapshot=regime_snapshot,
            daily_ema20=daily_ema20,
            daily_ema50=daily_ema50,
            risk_budget_rs=risk_budget_rs,
        )


# Canonical VECTOR Alias for ARGUS 8i // BEACON
VectorAlphaEngine = MultiTimeframeAlphaEngine

