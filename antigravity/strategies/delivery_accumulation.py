"""
antigravity/strategies/delivery_accumulation.py
===============================================
Sleeve A: Institutional Delivery Accumulation Strategy (Track 2 Liquid Swing).
Part of Project Swing Trades (ARGUS 8i Track 2 Liquid Desk).

Economic & Microstructure Rationale:
Institutional float absorption occurs quietly through high delivery percentages
during price consolidation. When followed by range compression and volume expansion,
it signals institutional accumulation breaking out into a multi-day swing trend.

Invariants:
- AGENTS.md Rule 2 Price Floor (Rs 10.00).
- AGENTS.md Rule 11 Track 2 Isolation (F&O underlyings, DTV >= 30 Cr, not in ASM/GSM).
- Pre-registered specification: shared/track2_liquid/strategies/specs/delivery_accumulation_v1.yaml
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from .base_strategy import BaseSwingStrategy, SignalEvent, ExitSignalEvent


class DeliveryAccumulationStrategy(BaseSwingStrategy):
    """
    Sleeve A: Institutional Delivery Accumulation & Float Absorption.
    """

    STRATEGY_ID = "DELIVERY_ACCUMULATION"

    def __init__(
        self,
        spec_path: Optional[Union[str, Path]] = None,
        config: Optional[Dict[str, Any]] = None
    ) -> None:
        super().__init__(spec_path=spec_path, config=config)

    @property
    def strategy_id(self) -> str:
        return self.STRATEGY_ID

    def _validate_config(self) -> None:
        # Default fallbacks matching pre-registered spec
        self.min_price = float(self.config.get("eligibility", {}).get("min_price", 10.00))
        self.min_dtv_rs = float(self.config.get("eligibility", {}).get("min_dtv_rs", 300_000_000.0))
        self.lookback_days = int(self.config.get("setup_rules", {}).get("lookback_days", 20))
        self.compression_days = int(self.config.get("setup_rules", {}).get("compression_days", 5))
        self.delivery_exp_ratio = float(self.config.get("setup_rules", {}).get("delivery_pct_expansion_ratio", 2.0))
        self.range_comp_ratio = float(self.config.get("setup_rules", {}).get("range_compression_ratio", 0.75))
        self.vol_exp_ratio = float(self.config.get("entry_rules", {}).get("volume_expansion_ratio", 1.5))
        self.stop_atr_mult = float(self.config.get("risk_and_exits", {}).get("stop_loss_atr_mult", 1.5))
        self.target_atr_mult = float(self.config.get("risk_and_exits", {}).get("target_profit_atr_mult", 3.0))
        self.max_holding_sessions = int(self.config.get("risk_and_exits", {}).get("max_holding_sessions", 7))

    def generate_signals(
        self,
        session_date: str,
        market_data: Mapping[str, Any],
        context: Optional[Dict[str, Any]] = None
    ) -> List[SignalEvent]:
        """
        Scans market data for institutional delivery accumulation breakout setups on `session_date`.
        """
        signals: List[SignalEvent] = []
        next_session = context.get("next_session", session_date) if context else session_date

        for symbol, data in market_data.items():
            # 1. Eligibility Check (Fail-Closed)
            metadata = data.get("metadata", {})
            if not metadata.get("is_fno_underlying", False):
                continue
            if metadata.get("is_surveillance", True):
                continue
            if metadata.get("series", "EQ") != "EQ":
                continue

            bars = data.get("bars", [])
            # Need at least lookback_days + 1 bars
            if len(bars) < self.lookback_days + 1:
                continue

            # Extract bar series
            closes = [float(b["close"]) for b in bars]
            highs = [float(b["high"]) for b in bars]
            lows = [float(b["low"]) for b in bars]
            volumes = [float(b["volume"]) for b in bars]
            deliveries = [float(b.get("delivery_pct", 0.0)) for b in bars]

            curr_close = closes[-1]
            curr_volume = volumes[-1]
            curr_delivery = deliveries[-1]

            # Price Floor Check (AGENTS.md Rule 2)
            if curr_close < self.min_price:
                continue

            # ATR Calculation (20 periods)
            atr = self.calculate_atr(highs, lows, closes, period=self.lookback_days)
            if atr is None or atr <= 0.0:
                continue

            # Daily Turnover Check (DTV >= Rs 30 Cr)
            vol_sma20 = self.calculate_sma(volumes, period=self.lookback_days)
            if vol_sma20 is None or vol_sma20 <= 0:
                continue
            dtv_rs = curr_close * vol_sma20
            if dtv_rs < self.min_dtv_rs:
                continue

            # Delivery Moving Average Check
            deliv_sma20 = self.calculate_sma(deliveries, period=self.lookback_days)
            if deliv_sma20 is None or deliv_sma20 <= 0:
                continue

            # 2. Setup Conditions:
            # Condition A: Delivery expansion >= 2.0x 20-day delivery MA
            delivery_expansion = curr_delivery / deliv_sma20
            if delivery_expansion < self.delivery_exp_ratio:
                continue

            # Condition B: 5-Day Price Range Compression <= 0.75x ATR(20)
            # Compression is measured over the 5 sessions prior to breakout
            comp_highs = highs[-self.compression_days - 1 : -1]
            comp_lows = lows[-self.compression_days - 1 : -1]
            range_5d = max(comp_highs) - min(comp_lows)
            range_ratio = range_5d / atr
            if range_ratio > self.range_comp_ratio:
                continue

            # 3. Entry Trigger:
            # Volume expansion >= 1.5x 20-day average volume
            vol_expansion = curr_volume / vol_sma20
            if vol_expansion < self.vol_exp_ratio:
                continue

            # Breakout above prior consolidation high
            prior_5d_high = max(comp_highs)
            if curr_close <= prior_5d_high:
                continue

            # 4. Sizing & Levels (Strict Mathematical Ordering)
            reference_price = round(curr_close, 2)
            stop_loss = round(reference_price - (self.stop_atr_mult * atr), 2)
            target_price = round(reference_price + (self.target_atr_mult * atr), 2)

            if stop_loss <= 0.0 or stop_loss >= reference_price or target_price <= reference_price:
                continue

            # Priority score: Volume Expansion * Delivery Expansion
            priority_score = round(vol_expansion * delivery_expansion, 4)

            trace = {
                "close": curr_close,
                "atr": atr,
                "vol_expansion": round(vol_expansion, 2),
                "deliv_expansion": round(delivery_expansion, 2),
                "range_5d": round(range_5d, 2),
                "range_ratio": round(range_ratio, 2),
                "dtv_rs": round(dtv_rs, 2),
            }

            event = SignalEvent(
                strategy_id=self.STRATEGY_ID,
                symbol=symbol,
                session_date=session_date,
                entry_session=next_session,
                signal_type="BUY",
                order_type="BUY_STOP_OR_MARKET_OPEN",
                reference_price=reference_price,
                stop_loss_price=stop_loss,
                target_price=target_price,
                priority_score=priority_score,
                trace=trace,
            )
            signals.append(event)

        # Deterministic sorting by priority score descending
        signals.sort(key=lambda s: s.priority_score, reverse=True)
        return signals

    def evaluate_exits(
        self,
        open_positions: Sequence[Mapping[str, Any]],
        current_bars: Mapping[str, Any],
        session_date: str
    ) -> List[ExitSignalEvent]:
        """
        Evaluates active swing positions for take-profit, stop-loss, trailing stop, and time stops.
        """
        exits: List[ExitSignalEvent] = []

        for pos in open_positions:
            if pos.get("strategy_id") != self.STRATEGY_ID:
                continue

            symbol = pos["symbol"]
            bar = current_bars.get(symbol)
            if not bar:
                continue

            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar["close"])
            shares = int(pos["shares"])
            pos_id = str(pos["position_id"])
            entry_price = float(pos["entry_price"])
            stop_price = float(pos["stop_price"])
            target_price = float(pos["target_price"])
            holding_sessions = int(pos.get("holding_sessions", 1))

            # 1. Target Hit
            if high >= target_price:
                exits.append(
                    ExitSignalEvent(
                        strategy_id=self.STRATEGY_ID,
                        symbol=symbol,
                        session_date=session_date,
                        position_id=pos_id,
                        reason="TARGET_HIT",
                        exit_price=target_price,
                        shares_to_exit=shares,
                        trace={"target_price": target_price, "bar_high": high},
                    )
                )
                continue

            # 2. Stop Loss Hit
            if low <= stop_price:
                exits.append(
                    ExitSignalEvent(
                        strategy_id=self.STRATEGY_ID,
                        symbol=symbol,
                        session_date=session_date,
                        position_id=pos_id,
                        reason="STOP_LOSS",
                        exit_price=min(stop_price, bar.get("open", stop_price)),
                        shares_to_exit=shares,
                        trace={"stop_price": stop_price, "bar_low": low},
                    )
                )
                continue

            # 3. Time-Based Stop
            if holding_sessions >= self.max_holding_sessions:
                exits.append(
                    ExitSignalEvent(
                        strategy_id=self.STRATEGY_ID,
                        symbol=symbol,
                        session_date=session_date,
                        position_id=pos_id,
                        reason="TIME_STOP",
                        exit_price=close,
                        shares_to_exit=shares,
                        trace={"holding_sessions": holding_sessions, "max_holding": self.max_holding_sessions},
                    )
                )
                continue

            # 4. Trailing Exit (Close < 5-day EMA after session 3)
            ema5 = bar.get("ema5")
            if holding_sessions >= 3 and ema5 is not None and close < float(ema5):
                exits.append(
                    ExitSignalEvent(
                        strategy_id=self.STRATEGY_ID,
                        symbol=symbol,
                        session_date=session_date,
                        position_id=pos_id,
                        reason="TRAILING_STOP",
                        exit_price=close,
                        shares_to_exit=shares,
                        trace={"close": close, "ema5": ema5, "holding_sessions": holding_sessions},
                    )
                )

        return exits
