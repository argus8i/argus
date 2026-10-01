"""
antigravity/strategies/expiry_relief.py
=======================================
Sleeve C: Post-Expiry Relief & Gamma Pin Mean Reversion (Track 2 Liquid Swing).
Part of Project Swing Trades (ARGUS 8i Track 2 Liquid Desk).

Microstructure & Institutional Rationale:
In the Indian F&O equity market, heavily beaten-down underlyings suffer severe
downside synthetic delta and gamma pin pressure into monthly derivative expiry
(last Thursday of the month) as institutional dealers defend strike prices and
arbitrageurs hedge short exposure. Immediately upon contract expiration, this artificial
selling pressure vanishes, creating a sharp, high-probability 2-to-5 day relief bounce.

Invariants:
- AGENTS.md Rule 2 Price Floor (Rs 10.00).
- AGENTS.md Rule 11 Track 2 Isolation (F&O underlyings, DTV >= 30 Cr, not in ASM/GSM).
- Pre-registered specification: shared/track2_liquid/strategies/specs/expiry_relief_v1.yaml
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from .base_strategy import BaseSwingStrategy, SignalEvent, ExitSignalEvent


class ExpiryReliefStrategy(BaseSwingStrategy):
    """
    Sleeve C: Post-Expiry Relief Mean Reversion.
    """

    STRATEGY_ID = "EXPIRY_RELIEF"

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
        self.min_cycle_decline_pct = float(self.config.get("setup_rules", {}).get("expiry_cycle_min_decline_pct", 8.0))
        self.rsi_period = int(self.config.get("setup_rules", {}).get("rsi_lookback_period", 14))
        self.rsi_max_threshold = float(self.config.get("setup_rules", {}).get("rsi_max_threshold", 30.0))
        self.target_profit_pct = float(self.config.get("risk_and_exits", {}).get("target_profit_pct", 3.5))
        self.max_holding_sessions = int(self.config.get("risk_and_exits", {}).get("max_holding_sessions", 5))
        self.stop_atr_mult = float(self.config.get("risk_and_exits", {}).get("stop_loss_atr_mult", 1.5))

    def generate_signals(
        self,
        session_date: str,
        market_data: Mapping[str, Any],
        context: Optional[Dict[str, Any]] = None
    ) -> List[SignalEvent]:
        """
        Scans market data for post-expiry relief candidates on `session_date`.
        Signals fire on monthly expiry day close for execution on post-expiry session 1.
        """
        # Timing Check: session must be flagged as expiry day or post-expiry session
        is_expiry_day = False
        if context and context.get("is_expiry_session", False):
            is_expiry_day = True
        elif context and context.get("expiry_date") == session_date:
            is_expiry_day = True

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

            # Per-symbol PIT expiry check if not set in general context
            sym_is_expiry = is_expiry_day or (metadata.get("nearest_fut_expiry") == session_date)
            if not sym_is_expiry:
                continue

            bars = data.get("bars", [])
            # Require at least 25 bars (for RSI 14 + cycle lookback ~20 days)
            if len(bars) < 25:
                continue

            closes = [float(b["close"]) for b in bars]
            highs = [float(b["high"]) for b in bars]
            lows = [float(b["low"]) for b in bars]
            volumes = [float(b["volume"]) for b in bars]

            curr_close = closes[-1]
            curr_low = lows[-1]

            # Price Floor Check (Rule 2)
            if curr_close < self.min_price:
                continue

            # ATR Calculation (14 periods)
            atr = self.calculate_atr(highs, lows, closes, period=self.rsi_period)
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
            # Condition A: RSI(14) <= 30.0 (Oversold Pin)
            rsi = self.calculate_rsi(closes, period=self.rsi_period)
            if rsi is None or rsi > self.rsi_max_threshold:
                continue

            # Condition B: Monthly Expiry Cycle Decline >= 8.0%
            # If cycle_start_price is given in metadata/context, use it; otherwise use 20 bars ago
            cycle_start_price = float(metadata.get("cycle_start_price", closes[-21]))
            if cycle_start_price <= 0:
                continue
            cycle_return = (curr_close - cycle_start_price) / cycle_start_price
            cycle_decline_pct = abs(cycle_return) * 100.0

            if cycle_return > -(self.min_cycle_decline_pct / 100.0):
                continue  # Not beaten down enough

            # 3. Sizing & Levels (Strict Mathematical Ordering)
            reference_price = round(curr_close, 2)

            # Stop loss: Expiry day low minus 0.5 ATR (or entry - 1.5 ATR, whichever is lower)
            stop_from_low = curr_low - (0.5 * atr)
            stop_from_entry = reference_price - (self.stop_atr_mult * atr)
            stop_loss = round(min(stop_from_low, stop_from_entry), 2)

            # Target price: at least +3.5% or 2.0R (+3.0 ATR)
            target_pct_price = reference_price * (1.0 + (self.target_profit_pct / 100.0))
            target_atr_price = reference_price + (2.0 * (reference_price - stop_loss))
            target_price = round(max(target_pct_price, target_atr_price), 2)

            if stop_loss <= 0.0 or stop_loss >= reference_price or target_price <= reference_price:
                continue

            # Priority score: (30.0 - RSI) * cycle_decline_pct
            priority_score = round((30.0 - rsi) * cycle_decline_pct, 4)

            trace = {
                "close": curr_close,
                "rsi": rsi,
                "cycle_decline_pct": round(cycle_decline_pct, 2),
                "cycle_start_price": cycle_start_price,
                "atr": atr,
                "expiry_day_low": curr_low,
                "dtv_rs": round(dtv_rs, 2),
            }

            event = SignalEvent(
                strategy_id=self.STRATEGY_ID,
                symbol=symbol,
                session_date=session_date,
                entry_session=next_session,
                signal_type="BUY",
                order_type="BUY_MARKET_OPEN",
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
        Evaluates active post-expiry relief positions for profit targets, stop loss, and 5-day time limits.
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

            # 3. Fixed 5-day Time Stop
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

        return exits
