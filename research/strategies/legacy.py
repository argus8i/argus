"""
research/strategies/legacy.py
=============================
Research adapters for the other production Track 2 strategies (Yashu's mandate of 26 Sep 2026: do any of them
show positive gross or net edge on the design set?). Each adapter calls the PRODUCTION class, unmodified and
with its production default parameters (one configuration each: no sweeps, so each is one registered trial):

    TRAPDOOR     antigravity/models/track2_trapdoor_strategy.TrapdoorStrategy.evaluate(sym, bars, bucket_median)
    LAST_LIGHT   ..._last_light_strategy.LastLightStrategy.evaluate_setup(sym, bars, bucket_median, time)
    RECOIL       ..._recoil_strategy.RecoilStrategy.evaluate_setup(sym, bars, bucket_median, time)  (BUY or SELL)
    VOL_SQUEEZE  ..._volatility_squeeze_strategy.VolatilitySqueezeStrategy.evaluate(sym, daily, bars,
                 bucket_median, ema20, ema50)
    COMPASS      ..._compass_strategy.CompassStrategy.evaluate(sym, sector, bars, sector peers' bars, NIFTY bars,
                 bucket_median)   (peers come from the engine, truncated at the decision time)

Inputs are point-in-time: the stock's bars up to the bar close, its daily bars strictly before the session, the
same-slot median volume of the prior 20 sessions (read through the engine's PointInTimeView), and for COMPASS
the engine's same-sector peers and NIFTY bars cut at the decision time.
Everything after the signal is the shared research model (plan D8): entry at the next bar open with the
10 bps clamp and 1 tick of slippage, the production stop as SL-limit, the production targets (two tranches of
50% where the strategy has two, else 100% at its one target), the strategy's max_holding_bars when it has one,
the 15:05 policy exit, MIS fees. The production entry limit price is not used. One signal per stock-day.
"""
from __future__ import annotations

from datetime import date, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

from research.backtest.bars import Bar
from research.backtest.strategies import Decision, SignalIntent, StrategyAdapter, StrategyContext

IST = timezone(timedelta(hours=5, minutes=30))
FIRST_START, LAST_START = time(9, 30), time(14, 30)
BUCKET_SESSIONS, BUCKET_MIN = 20, 15
NAMES = ("TRAPDOOR", "LAST_LIGHT", "RECOIL", "VOL_SQUEEZE", "COMPASS")


def _bar_map(b: Bar) -> Dict[str, Any]:
    return {"timestamp": b.start.astimezone(IST).isoformat(), "open": b.open, "high": b.high, "low": b.low,
            "close": b.close, "volume": int(b.volume)}


def _ema(values: Sequence[float], span: int) -> Optional[float]:
    if len(values) < span:
        return None
    a = 2.0 / (span + 1)
    e = float(np.mean(values[:span]))
    for v in values[span:]:
        e = a * float(v) + (1 - a) * e
    return e


class LegacyAdapter(StrategyAdapter):
    def __init__(self, name: str, is_shadow: bool = True) -> None:
        if name not in NAMES:
            raise ValueError(f"unknown legacy strategy {name!r}")
        self.name, self.is_shadow = name, is_shadow
        if name == "TRAPDOOR":
            from antigravity.models.track2_trapdoor_strategy import TrapdoorStrategy as C
        elif name == "LAST_LIGHT":
            from antigravity.models.track2_last_light_strategy import LastLightStrategy as C
        elif name == "RECOIL":
            from antigravity.models.track2_recoil_strategy import RecoilStrategy as C
        elif name == "VOL_SQUEEZE":
            from antigravity.models.track2_volatility_squeeze_strategy import VolatilitySqueezeStrategy as C
        else:
            from antigravity.models.track2_compass_strategy import CompassStrategy as C
        self.impl = C()
        self._emitted: Set[Tuple[str, date]] = set()
        self.reasons: Dict[str, int] = {}

    def _no(self, ctx: StrategyContext, action: str, reason: str) -> Decision:
        key = reason.split(":")[0]
        self.reasons[key] = self.reasons.get(key, 0) + 1
        return Decision(self.name, ctx.symbol, action, reason=reason)

    def _bucket_median(self, ctx: StrategyContext) -> Optional[float]:
        t = ctx.current.start.astimezone(IST).time()
        vols: List[float] = []
        for d in (ctx.store.sessions(ctx.symbol) if ctx.store is not None else [])[-BUCKET_SESSIONS:]:
            for b in ctx.store.bars(ctx.symbol, d):
                if b.start.astimezone(IST).time() == t:
                    vols.append(float(b.volume))
        return float(np.median(vols)) if len(vols) >= BUCKET_MIN else None

    def _call(self, ctx: StrategyContext, bars: List[Dict[str, Any]], bm: float) -> Any:
        t_end = ctx.decision_time.astimezone(IST).time()
        if self.name == "TRAPDOOR":
            return self.impl.evaluate(ctx.symbol, bars, bm)
        if self.name in ("LAST_LIGHT", "RECOIL"):
            return self.impl.evaluate_setup(ctx.symbol, bars, bm, current_time_ist=t_end)
        if self.name == "VOL_SQUEEZE":
            daily = [{"timestamp": f"{x.day.isoformat()}T00:00:00+05:30", "open": x.open, "high": x.high,
                      "low": x.low, "close": x.close, "volume": int(x.volume)} for x in list(ctx.daily_bars)[-120:]]
            closes = [x["close"] for x in daily]
            e20, e50 = _ema(closes, 20), _ema(closes, 50)
            if e20 is None or e50 is None:
                return None
            return self.impl.evaluate(ctx.symbol, daily, bars, bm, e20, e50)
        peers = {p: [_bar_map(b) for b in bs] for p, bs in ctx.peers.items() if bs}
        market = [_bar_map(b) for b in ctx.nifty_bars]
        if not peers or not market:
            return None
        return self.impl.evaluate(ctx.symbol, ctx.sector, bars, peers, market, bm)

    def evaluate(self, ctx: StrategyContext) -> Decision:
        start = ctx.current.start.astimezone(IST)
        day = start.date()
        if not (FIRST_START <= start.time() <= LAST_START):
            return self._no(ctx, "NO_PATTERN", "OUTSIDE_SIGNAL_WINDOW")
        if (ctx.symbol, day) in self._emitted:
            return self._no(ctx, "NO_PATTERN", "DAY_USED")
        bm = self._bucket_median(ctx)
        if bm is None or bm <= 0:
            return self._no(ctx, "DATA_INVALID", "BUCKET_MEDIAN_UNAVAILABLE")
        try:
            x = self._call(ctx, [_bar_map(b) for b in ctx.bars], bm)
        except (KeyError, TypeError, ValueError, ZeroDivisionError, IndexError) as exc:
            return self._no(ctx, "DATA_INVALID", f"PRODUCTION_ERROR:{type(exc).__name__}")
        if x is None:
            return self._no(ctx, "DATA_INVALID", "INPUTS_UNAVAILABLE")
        if not getattr(x, "passed_all_gates", False):
            return self._no(ctx, "NO_PATTERN", str(getattr(x, "decision", "NO_SIGNAL")))
        side = str(getattr(x, "side", "BUY") or "BUY").upper()
        stop = getattr(x, "stop_price", None)
        sg = 1 if side == "BUY" else -1
        p0 = ctx.current.close
        if stop is None or not np.isfinite(stop) or sg * (p0 - float(stop)) <= 0:
            return self._no(ctx, "DATA_INVALID", "STOP_ON_WRONG_SIDE")
        t1, t2 = getattr(x, "target_tranche1", None), getattr(x, "target_tranche2", None)
        if t1 is not None and t2 is not None:
            targets = [(float(t1), 0.5), (float(t2), 0.5)]
        else:
            tp = getattr(x, "target_price", None)
            targets = [(float(tp), 1.0)] if tp is not None else []
        targets = [(p, f) for p, f in targets if sg * (p - p0) > 0]
        max_bars = getattr(x, "max_holding_bars", None)
        intent = SignalIntent.from_ctx(ctx, self.name, side, float(stop), targets,
                                       max_bars=int(max_bars) if max_bars else None, is_shadow=self.is_shadow)
        intent.r_basis = "stop_limit"
        intent.diagnostics = {"production_decision": str(getattr(x, "decision", "")),
                              "production_entry": getattr(x, "entry_price", None), "bucket_median_vol": bm}
        self._emitted.add((ctx.symbol, day))
        self.reasons["SIGNAL"] = self.reasons.get("SIGNAL", 0) + 1
        return Decision(self.name, ctx.symbol, "SIGNAL", intent)
