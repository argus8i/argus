"""
antigravity/strategies/high52_momentum.py
=========================================
Sleeve B: 52-Week High Anchoring Momentum Strategy (Track 2 Liquid Swing).
Part of Project Swing Trades (ARGUS 8i Track 2 Liquid Desk).

Academic & Quantitative Rationale:
George & Hwang (2004) "The 52-Week High and Momentum Investing", Jegadeesh & Titman (1993).
Neoclassical behavioral anchor: investors underreact to positive fundamental news
when a stock is near its 52-week high due to anchoring bias. When price breaks out
of a 20-day base within 3% of the 52-week high with volume confirmation,
the anchoring barrier breaks, producing persistent multi-day upward continuation.

Invariants:
- AGENTS.md Rule 2 Price Floor (Rs 10.00).
- AGENTS.md Rule 11 Track 2 Isolation (F&O underlyings, DTV >= 30 Cr, not in ASM/GSM).
- Pre-registered specification: shared/track2_liquid/strategies/specs/high52_momentum_v1.yaml
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from .base_strategy import BaseSwingStrategy, SignalEvent, ExitSignalEvent


class High52MomentumStrategy(BaseSwingStrategy):
    """
    Sleeve B: 52-Week High Anchoring Momentum.
    """

    STRATEGY_ID = "HIGH52_MOMENTUM"

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
        self.min_price = float(self.config.get("eligibility", {}).get("min_price", 10.00))
        self.min_dtv_rs = float(self.config.get("eligibility", {}).get("min_dtv_rs", 300_000_000.0))
        self.lookback_52w = int(self.config.get("setup_rules", {}).get("lookback_days_52w", 252))
        self.consolidation_days = int(self.config.get("setup_rules", {}).get("consolidation_days", 20))
        self.proximity_pct = float(self.config.get("setup_rules", {}).get("proximity_pct_to_52w_high", 3.0))
        self.stop_atr_mult = float(self.config.get("risk_and_exits", {}).get("stop_loss_atr_mult", 2.0))
        self.target_atr_mult = float(self.config.get("risk_and_exits", {}).get("target_profit_atr_mult", 4.0))
        self.max_holding_sessions = int(self.config.get("risk_and_exits", {}).get("max_holding_sessions", 10))

    def generate_signals(
        self,
        session_date: str,
        market_data: Mapping[str, Any],
        context: Optional[Dict[str, Any]] = None
    ) -> List[SignalEvent]:
        """
        Scans market data for 52-week high breakout swing momentum candidates on `session_date`.
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
            # Require at least 50 bars for indicator calculation (and up to 252 for full 52w high)
            if len(bars) < 50:
                continue

            closes = [float(b["close"]) for b in bars]
            highs = [float(b["high"]) for b in bars]
            lows = [float(b["low"]) for b in bars]
            volumes = [float(b["volume"]) for b in bars]

            curr_close = closes[-1]
            curr_volume = volumes[-1]

            # Price Floor Check (Rule 2)
            if curr_close < self.min_price:
                continue

            # ATR Calculation (20 periods)
            atr = self.calculate_atr(highs, lows, closes, period=20)
            if atr is None or atr <= 0.0:
                continue

            # DTV Check (20-day average)
            vol_sma20 = self.calculate_sma(volumes, period=20)
            if vol_sma20 is None or vol_sma20 <= 0:
                continue
            dtv_rs = curr_close * vol_sma20
            if dtv_rs < self.min_dtv_rs:
                continue

            # 2. Setup Rules:
            # Lookback for 52-week high (use min(len(highs), lookback_52w))
            lookback_len = min(len(highs), self.lookback_52w)
            high_52w = max(highs[-lookback_len:])
            if high_52w <= 0.0:
                continue

            # Proximity condition: Close >= (1 - proximity_pct/100) * 52w High
            proximity_threshold = (1.0 - (self.proximity_pct / 100.0)) * high_52w
            if curr_close < proximity_threshold:
                continue

            # Volume expansion: 20-day avg volume >= 50-day avg volume
            vol_sma50 = self.calculate_sma(volumes, period=50)
            if vol_sma50 is not None and vol_sma20 < vol_sma50:
                continue

            # Regime / Trend filter: Close >= 50-day EMA
            ema50 = self.calculate_ema(closes, period=50)
            if ema50 is not None and curr_close < ema50:
                continue

            # 3. Entry Trigger:
            # Daily close breaking above 20-day consolidation high
            if len(highs) < self.consolidation_days + 1:
                continue
            prior_20d_high = max(highs[-self.consolidation_days - 1 : -1])
            if curr_close <= prior_20d_high:
                continue

            # 4. Sizing & Levels
            reference_price = round(curr_close, 2)
            stop_loss = round(reference_price - (self.stop_atr_mult * atr), 2)
            target_price = round(reference_price + (self.target_atr_mult * atr), 2)

            if stop_loss <= 0.0 or stop_loss >= reference_price or target_price <= reference_price:
                continue

            # Priority score: Proximity ratio (close / high_52w)
            priority_score = round(curr_close / high_52w, 4)

            trace = {
                "close": curr_close,
                "high_52w": high_52w,
                "prior_20d_high": prior_20d_high,
                "atr": atr,
                "ema50": ema50,
                "proximity_pct": round(((high_52w - curr_close) / high_52w) * 100.0, 2),
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
        Evaluates active 52-week high momentum positions for profit targets, trailing stops, and time limits.
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

            # 3. Time Stop (10 sessions)
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

            # 4. Trailing Exit (Close < 20-day EMA after session 3)
            ema20 = bar.get("ema20")
            if holding_sessions >= 3 and ema20 is not None and close < float(ema20):
                exits.append(
                    ExitSignalEvent(
                        strategy_id=self.STRATEGY_ID,
                        symbol=symbol,
                        session_date=session_date,
                        position_id=pos_id,
                        reason="TRAILING_STOP",
                        exit_price=close,
                        shares_to_exit=shares,
                        trace={"close": close, "ema20": ema20, "holding_sessions": holding_sessions},
                    )
                )

        return exits
