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
- Conservative Adverse Selection: Opening stop breaches and ambiguous intrabar touches prioritize STOP_LOSS.
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
        config: Optional[Dict[str, Any]] = None,
        allow_unreviewed_overrides: bool = False
    ) -> None:
        super().__init__(spec_path=spec_path, config=config, allow_unreviewed_overrides=allow_unreviewed_overrides)

    @property
    def strategy_id(self) -> str:
        return self.STRATEGY_ID

    def _validate_config(self) -> None:
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
        Strictly point-in-time and fail-closed against unvalidated metadata or timing.
        """
        # Timing Context Check: Explicit valid next trading session is mandatory (Rule 4 / T_PLUS_1)
        if not context or not isinstance(context, Mapping):
            return []
        next_session = context.get("next_session")
        if not next_session or not isinstance(next_session, str) or next_session <= session_date:
            return []

        signals: List[SignalEvent] = []

        for symbol, data in market_data.items():
            # 1. Strict Typed Metadata Eligibility Check (Codex Finding 2)
            metadata = data.get("metadata")
            if not isinstance(metadata, Mapping):
                continue
            if metadata.get("is_fno_underlying") is not True:
                continue
            if metadata.get("is_surveillance") is not False:
                continue
            if metadata.get("series") != "EQ":
                continue

            bars = data.get("bars", [])
            # Need at least lookback_days + 1 bars
            if len(bars) < self.lookback_days + 1:
                continue

            # Chronological bar validation (reject future bars, reject unordered bars)
            valid_bars = True
            for i, b in enumerate(bars):
                b_date = str(b.get("session_date") or b.get("day", ""))
                if b_date and b_date > session_date:
                    valid_bars = False
                    break
                if i > 0 and b_date:
                    prev_date = str(bars[i - 1].get("session_date") or bars[i - 1].get("day", ""))
                    if prev_date and b_date < prev_date:
                        valid_bars = False
                        break
            if not valid_bars:
                continue

            last_bar_date = str(bars[-1].get("session_date") or bars[-1].get("day", ""))
            if last_bar_date and last_bar_date != session_date:
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
            # Compression measured over the 5 sessions prior to the breakout
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
        Evaluates active swing positions using conservative adverse-selection execution precedence.
        """
        exits: List[ExitSignalEvent] = []

        for pos in open_positions:
            if pos.get("strategy_id") != self.STRATEGY_ID:
                continue

            symbol = pos["symbol"]
            bar = current_bars.get(symbol)
            if not bar:
                continue

            open_p = float(bar.get("open", bar.get("close", 0.0)))
            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar["close"])
            shares = int(pos["shares"])
            pos_id = str(pos["position_id"])
            stop_price = float(pos["stop_price"])
            target_price = float(pos["target_price"])
            holding_sessions = int(pos.get("holding_sessions", 1))

            # Precedence 1: Opening Gap-Down below Stop Loss (Codex Finding 3)
            if open_p <= stop_price:
                exits.append(
                    ExitSignalEvent(
                        strategy_id=self.STRATEGY_ID,
                        symbol=symbol,
                        session_date=session_date,
                        position_id=pos_id,
                        reason="STOP_LOSS",
                        exit_price=open_p,
                        shares_to_exit=shares,
                        trace={"stop_price": stop_price, "bar_open": open_p, "gap_down": True},
                    )
                )
                continue

            # Precedence 2: Opening Gap-Up above Target
            if open_p >= target_price:
                exits.append(
                    ExitSignalEvent(
                        strategy_id=self.STRATEGY_ID,
                        symbol=symbol,
                        session_date=session_date,
                        position_id=pos_id,
                        reason="TARGET_HIT",
                        exit_price=open_p,
                        shares_to_exit=shares,
                        trace={"target_price": target_price, "bar_open": open_p, "gap_up": True},
                    )
                )
                continue

            # Precedence 3: Ambiguous Intrabar Range (Both stop and target touched) -> ADVERSE SELECTION: Stop Loss first
            if low <= stop_price and high >= target_price:
                exits.append(
                    ExitSignalEvent(
                        strategy_id=self.STRATEGY_ID,
                        symbol=symbol,
                        session_date=session_date,
                        position_id=pos_id,
                        reason="STOP_LOSS",
                        exit_price=stop_price,
                        shares_to_exit=shares,
                        trace={"stop_price": stop_price, "target_price": target_price, "adverse_selection": True},
                    )
                )
                continue

            # Precedence 4: Normal Stop Loss Hit
            if low <= stop_price:
                exits.append(
                    ExitSignalEvent(
                        strategy_id=self.STRATEGY_ID,
                        symbol=symbol,
                        session_date=session_date,
                        position_id=pos_id,
                        reason="STOP_LOSS",
                        exit_price=stop_price,
                        shares_to_exit=shares,
                        trace={"stop_price": stop_price, "bar_low": low},
                    )
                )
                continue

            # Precedence 5: Normal Target Hit
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

            # Precedence 6: Time-Based Stop (Session 7)
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

            # Precedence 7: Trailing Exit (Close < 5-day EMA after session 3)
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
