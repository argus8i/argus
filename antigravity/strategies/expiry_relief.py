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
- Fail-closed timing: strictly requires per-symbol verified expiry date and explicit cycle_start_price.
- Conservative Adverse Selection: Opening stop breaches and ambiguous intrabar touches prioritize STOP_LOSS.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from .base_strategy import BaseSwingStrategy, SignalEvent, ExitSignalEvent


DEFAULT_SPEC_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "shared"
    / "track2_liquid"
    / "strategies"
    / "specs"
    / "expiry_relief_v1.yaml"
)


class ExpiryReliefStrategy(BaseSwingStrategy):
    """
    Sleeve C: Post-Expiry Relief Mean Reversion.
    """

    STRATEGY_ID = "EXPIRY_RELIEF"

    def __init__(
        self,
        spec_path: Optional[Union[str, Path]] = None,
        config: Optional[Dict[str, Any]] = None,
        allow_unreviewed_overrides: bool = False
    ) -> None:
        if spec_path is None and config is None:
            spec_path = DEFAULT_SPEC_PATH
        super().__init__(spec_path=spec_path, config=config, allow_unreviewed_overrides=allow_unreviewed_overrides)

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

        if self.min_cycle_decline_pct < 8.0 and not getattr(self, "allow_unreviewed_overrides", False):
            raise ValueError(
                f"expiry_cycle_min_decline_pct ({self.min_cycle_decline_pct}) cannot be less than 8.0 without explicit "
                "allow_unreviewed_overrides=True (Rule 8 v2 locked definition)"
            )

    def generate_signals(
        self,
        session_date: str,
        market_data: Mapping[str, Any],
        context: Optional[Dict[str, Any]] = None
    ) -> List[SignalEvent]:
        """
        Scans market data for post-expiry relief candidates on `session_date`.
        Signals fire on monthly expiry day close for execution on post-expiry session 1.
        Strictly requires explicit cycle_start_price and per-symbol verified expiry matching session_date.
        """
        next_session = self.validate_timing_context(session_date, context)
        if next_session is None:
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

            # Per-symbol PIT expiry check (Codex Finding 5: never allow loose global override)
            nearest_expiry = metadata.get("nearest_fut_expiry")
            if nearest_expiry != session_date:
                continue

            # Invariant: Explicit cycle_start_price required (never substitute unverified default closes)
            cycle_start_price = metadata.get("cycle_start_price")
            if (
                cycle_start_price is None
                or not isinstance(cycle_start_price, (int, float))
                or isinstance(cycle_start_price, bool)
                or not math.isfinite(cycle_start_price)
                or cycle_start_price <= 0.0
            ):
                continue

            bars = data.get("bars", [])
            # Require at least 25 bars (for RSI 14 + indicators)
            if len(bars) < 25:
                continue

            # Strict historical bar validation (strictly increasing unique dates, no future bars, final date == session_date)
            if not self.validate_historical_bars(bars, session_date):
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
        Evaluates active post-expiry relief positions using conservative adverse-selection execution precedence.
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

            # Precedence 6: Fixed 5-day Time Stop
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
