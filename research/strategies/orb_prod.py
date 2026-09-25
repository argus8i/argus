"""
research/strategies/orb_prod.py
===============================
ORB_PROD (plan P5.1, Appendix B orb_prod_v1): the desk's LIVE ORB path, imported read-only and replayed
bar by bar. Nothing in antigravity/ is modified.

Live path reproduced (antigravity/daemons/track2_daily_paper_desk.py):
1. baseline_from_history(prior intraday bars, prior daily bars, decision_timestamp = the bar's START):
   same-bucket volume median over the prior 20 daily sessions, ATR14 from daily true ranges, DTV20.
2. DTV20 < Rs 30 Cr -> DTV_BELOW_30_CR.
3. MarketRegimeFilter.evaluate_regime(NIFTY last close, NIFTY 09:15 bar high/low), with no breadth,
   exactly as the desk calls it. Without breadth the regime raises the volume multiple (3.5x in practice).
4. track2_orb_signal_adapter.evaluate_symbol(...) with the desk's ORDER_RULES, which calls
   LiquidMomentumEngine.evaluate_15m_orb_breakout and calculate_position_size (SL_LIMIT, 0.5% offset).
Window: the desk evaluates only while 09:45 <= now < 14:45 and only bars starting 09:30-14:30, so a bar
can signal if it starts between 09:30 and 14:15 (it closes before 14:45).
First qualifying bar per symbol-day only (plan P5.1).
Exits (Appendix B): two tranches at 1.5R and 3.0R from the production stop trigger, breakeven after T1,
policy exit 15:05 (engine). The production sizing's own single target is recorded in diagnostics.
Long only, as in production.

manifest() records the git blob hash of every imported production file (plan P5.1).
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

from research.backtest.bars import Bar
from research.backtest.strategies import Decision, SignalIntent, StrategyAdapter, StrategyContext

IST = timezone(timedelta(hours=5, minutes=30))
REPO = Path(__file__).resolve().parents[2]
PRODUCTION_FILES = (
    "antigravity/daemons/track2_daily_paper_desk.py",
    "antigravity/models/track2_orb_signal_adapter.py",
    "antigravity/models/liquid_momentum_screener.py",
    "antigravity/models/market_regime_filter.py",
)
FIRST_SIGNAL_START, LAST_SIGNAL_START = time(9, 30), time(14, 15)
TARGET_R = (1.5, 3.0)


def git_blob_sha1(path: Path) -> str:
    """Same value as `git hash-object <file>` for the bytes on disk (LF-normalised, as git stores them)."""
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _bar_map(b: Bar) -> Dict[str, Any]:
    return {"timestamp": b.start.astimezone(IST).isoformat(), "open": b.open, "high": b.high, "low": b.low,
            "close": b.close, "volume": int(b.volume)}


class OrbProdAdapter(StrategyAdapter):
    name = "ORB_PROD"

    def __init__(self, is_shadow: bool = True) -> None:
        from antigravity.daemons import track2_daily_paper_desk as desk
        from antigravity.models import track2_orb_signal_adapter as live
        from antigravity.models.market_regime_filter import MarketRegimeFilter

        self.is_shadow = is_shadow
        self._desk, self._live, self._regime = desk, live, MarketRegimeFilter
        self.order_rules = dict(desk.ORDER_RULES)
        self._emitted: Set[Tuple[str, date]] = set()
        self.reasons: Dict[str, int] = {}

    def manifest(self) -> Dict[str, str]:
        return {f: git_blob_sha1(REPO / f) for f in PRODUCTION_FILES}

    def _no(self, ctx: StrategyContext, action: str, reason: str) -> Decision:
        self.reasons[reason] = self.reasons.get(reason, 0) + 1
        return Decision(self.name, ctx.symbol, action, reason=reason)

    def evaluate(self, ctx: StrategyContext) -> Decision:
        cur = ctx.current
        start = cur.start.astimezone(IST)
        day = start.date()
        if not (FIRST_SIGNAL_START <= start.time() <= LAST_SIGNAL_START):
            return self._no(ctx, "NO_PATTERN", "OUTSIDE_SIGNAL_WINDOW")
        if (ctx.symbol, day) in self._emitted:
            return self._no(ctx, "NO_PATTERN", "DAY_USED")
        nifty = [b for b in ctx.nifty_bars if b.session_date == day]
        opening = next((b for b in nifty if b.start.astimezone(IST).time() == time(9, 15)), None)
        if opening is None or not nifty:
            return self._no(ctx, "DATA_INVALID", "NIFTY_OPENING_RANGE_UNAVAILABLE")
        regime = self._regime.evaluate_regime(nifty_ltp=float(nifty[-1].close), nifty_or_high=float(opening.high),
                                              nifty_or_low=float(opening.low))
        if ctx.store is None:
            return self._no(ctx, "DATA_INVALID", "NO_POINT_IN_TIME_STORE")
        prior_days = ctx.store.sessions(ctx.symbol)[-25:]
        same_bucket = []
        for d in prior_days:
            for b in ctx.store.bars(ctx.symbol, d):
                if b.start.astimezone(IST).time() == start.time():
                    same_bucket.append(_bar_map(b))
        daily = [{"timestamp": f"{x.day.isoformat()}T00:00:00+05:30", "open": x.open, "high": x.high, "low": x.low,
                  "close": x.close, "volume": int(x.volume)} for x in list(ctx.daily_bars)[-30:]]
        try:
            baseline = self._desk.baseline_from_history(same_bucket, daily_bars=daily,
                                                        decision_timestamp=start.isoformat())
        except (KeyError, TypeError, ValueError) as exc:
            return self._no(ctx, "DATA_INVALID", f"BASELINE:{str(exc)[:60]}")
        if baseline["dtv_med20_cr"] < 30:
            return self._no(ctx, "NO_PATTERN", "DTV_BELOW_30_CR")
        result = self._live.evaluate_symbol(symbol=ctx.symbol, candle_record={"bars": [_bar_map(b) for b in ctx.bars]},
                                            baseline=baseline, regime=regime, order_rules=self.order_rules)
        if result.get("decision") != "PAPER_SIGNAL":
            decision = str(result.get("decision"))
            action = "DATA_INVALID" if "INVALID" in decision else "NO_PATTERN"
            return self._no(ctx, action, decision)
        instr = result["paper_instruction"]
        entry, stop = float(instr["limit_price"]), float(instr["stop_price"])
        risk = entry - stop
        if risk <= 0:
            return self._no(ctx, "DATA_INVALID", "NON_POSITIVE_RISK")
        from research.backtest.bars import round_to_tick

        targets = [(round_to_tick(entry + k * risk, "down"), 0.5) for k in TARGET_R]
        intent = SignalIntent.from_ctx(ctx, self.name, "BUY", stop, targets, is_shadow=self.is_shadow)
        intent.r_basis = "stop_limit"
        intent.priority_score = float(result["evaluation"].get("volume_ratio", 0.0))
        intent.diagnostics = {
            "production_stop_limit": instr.get("stop_limit_price"), "production_target": instr.get("target_price"),
            "production_quantity": instr.get("quantity"), "volume_ratio": result["evaluation"].get("volume_ratio"),
            "regime": getattr(regime.state, "value", str(regime.state)),
            "regime_min_volume_multiple": regime.min_volume_multiple, "baseline": baseline,
        }
        self._emitted.add((ctx.symbol, day))
        self.reasons["SIGNAL"] = self.reasons.get("SIGNAL", 0) + 1
        return Decision(self.name, ctx.symbol, "SIGNAL", intent)
