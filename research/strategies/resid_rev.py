"""
research/strategies/resid_rev.py
================================
RESID_REV v1 (plan P5.2, Appendix A.7-A.9, pre-registration research/studies/prereg/resid_rev_v1.yaml):
fade a large, idiosyncratic, no-news intraday move of a stock relative to its sector factor once it
starts to turn.

Every parameter comes from the pre-registration spec; the adapter has no defaults of its own. While the
spec is DRAFT (z_star and hold_bars null), the adapter can only run in mode="SCAN", which evaluates and
logs everything (including the per stock-day maximum |Z| that P7.2b needs for z*) but never emits.

Evaluation order at the close of bar t (each rejection is counted by reason):
 WINDOW_CLOSED       bar close outside the decision window (10:00-13:30, t = 2..16)
 DAY_USED            a signal was already EMITTED for this symbol today (failed evaluations do not count)
 DATA_INVALID        calibration invalid / fewer than min sessions / missing slot / no factor or market bars
 SCAN_ONLY           mode SCAN: stop here after logging |Z|
 NO_SETUP            |Z[t]| < z*
 NO_TURN             e[t] * E[t] >= 0 (the bar still extends the move)
 VOLUME_ABNORMAL     RVOL_cum[t] outside the band
 NEWS_BLOCKED        a filing since the previous session's 15:30, a scheduled result/board meeting today or
                     next trading day, an ex-date today, or any of these UNKNOWN (variant RESID_REV only)
 MARKET_FILTER       |Z_M[t]| above the prior-60-session 80th percentile at slot t
 STOP_TOO_WIDE_SKIP  sigma_hold above the skip threshold
 COST_HURDLE         |T1 - P0| / P0 below the minimum
Prices (A.9): P0 = C[t]; T_k = P0*exp(sg*retrace_k*|E[t]|) rounded toward P0; stop = P0*(1 - sg*s) rounded
away from P0 with s = max(floor, multiple*sigma_hold); sigma_hold = sqrt(sum s2px[t+1..t+h_eff]);
h_eff = min(h, 22 - t) (EOD: 22 - t). Side: SELL if E[t] > 0 else BUY.
"""
from __future__ import annotations

import bisect
import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import numpy as np

from research.backtest.bars import round_to_tick
from research.backtest.strategies import Decision, SignalIntent, StrategyAdapter, StrategyContext
from research.data.session_shape import slot_of
from research.features.events import is_monthly_stock_expiry
from research.features.session_cache import market_path, residual_paths, rvol_path, slot_series

IST = timezone(timedelta(hours=5, minutes=30))
LAST_HELD_SLOT = 22          # 14:30-14:45 is the last bar fully held; the 15:05 policy exit falls in bar 23


def _hm(s: str) -> time:
    return datetime.strptime(str(s), "%H:%M").time()


class ResidRevAdapter(StrategyAdapter):
    def __init__(self, spec: Mapping[str, Any], *, variant: str = "RESID_REV", mode: str = "EMIT",
                 is_shadow: bool = True, keep_log: bool = False,
                 trading_days: Optional[Sequence[date]] = None) -> None:
        self.trading_days: List[date] = sorted(trading_days) if trading_days else []
        if variant not in ("RESID_REV", "RESID_REV_NF"):
            raise ValueError("variant must be RESID_REV or RESID_REV_NF")
        if variant == "RESID_REV_NF" and "RESID_REV_NF" not in (spec.get("variants_registered") or []):
            raise ValueError("RESID_REV_NF is not a registered variant in this spec")
        if mode not in ("EMIT", "SCAN"):
            raise ValueError("mode must be EMIT or SCAN")
        sig, trade = spec["signal"], spec["trade"]
        self.name = variant
        self.variant, self.mode, self.is_shadow = variant, mode, is_shadow
        self.window = (_hm(sig["decision_window_bar_close"][0]), _hm(sig["decision_window_bar_close"][1]))
        self.z_star = sig["z_star"]
        self.hold = trade["hold_bars"]
        if mode == "EMIT" and (self.z_star is None or self.hold is None):
            raise ValueError("pre-registration is DRAFT: z_star and hold_bars are set only by P7.2 (use mode='SCAN')")
        if self.hold is not None and self.hold != "EOD" and self.hold not in (4, 8):
            raise ValueError("hold_bars must be 4, 8 or EOD (plan A.9)")
        if not sig["turn_confirmation"]:
            raise ValueError("v1 requires turn confirmation")
        self.rvol_band = (float(sig["rvol_band"][0]), float(sig["rvol_band"][1]))
        self.one_per_day = bool(sig["one_emitted_signal_per_symbol_day"])
        st = trade["stop"]
        self.stop_mult, self.stop_floor = float(st["sigma_hold_multiple"]), float(st["floor_pct"]) / 100
        self.stop_skip = float(st["skip_if_above_pct"]) / 100
        self.targets = [(float(t["retrace_of_E"]), float(t["fraction"])) for t in trade["targets"]]
        if abs(sum(f for _, f in self.targets) - 1.0) > 1e-9:
            raise ValueError("target fractions must sum to 1")
        self.min_t1 = float(trade["min_t1_distance_pct"]) / 100
        self.news_rules = sig["news"]
        self._emitted: Set[Tuple[str, date]] = set()
        self.reasons: Dict[str, int] = {}
        self.scan_max: Dict[Tuple[str, date], float] = {}
        self.keep_log = keep_log
        self.log: List[Tuple[str, str, str, float]] = []
        # P7.2c event study: the first evaluation per stock-day that passes every filter that does not depend
        # on the holding period (all filters up to MARKET_FILTER), so every h run shares one sample
        self.candidates: Dict[Tuple[str, date], Dict[str, Any]] = {}

    # ------------------------------------------------------------------ helpers
    def _no(self, ctx: StrategyContext, action: str, reason: str, z: float = float("nan")) -> Decision:
        key = reason.split(":")[0]
        self.reasons[key] = self.reasons.get(key, 0) + 1
        if self.keep_log:
            self.log.append((ctx.symbol, ctx.decision_time.isoformat(), reason, z))
        return Decision(self.name, ctx.symbol, action, reason=reason)

    def _next_sessions(self, day: date) -> List[date]:
        """The next trading day, for the 'scheduled today or next trading day' rule. With a calendar it is
        exact. Without one, the next TWO weekdays are checked, so a one-day holiday cannot hide a scheduled
        result (over-blocking is the fail-closed direction)."""
        if self.trading_days:
            k = bisect.bisect_right(self.trading_days, day)
            if k < len(self.trading_days):
                return [self.trading_days[k]]
        out, d = [], day
        while len(out) < 2:
            d += timedelta(days=1)
            if d.weekday() < 5:
                out.append(d)
        return out

    def _news_blocked(self, ctx: StrategyContext, day: date) -> Optional[str]:
        ev = ctx.events
        if ev is None:
            return "EVENTS_UNKNOWN"
        prior = ctx.store.sessions(ctx.symbol) if ctx.store is not None else []
        if not prior:
            return "PREV_SESSION_UNKNOWN"
        since = datetime.combine(prior[-1], time(15, 30), IST)
        answers = [ev.news_since(ctx.symbol, since, ctx.decision_time)]
        for d in [day] + self._next_sessions(day):
            answers.append(ev.scheduled_event(ctx.symbol, d, ctx.decision_time))
        answers.append(ev.ex_date(ctx.symbol, day, ctx.decision_time))
        if any(a is None for a in answers):
            return "EVENTS_UNKNOWN"
        if any(answers):
            return "EVENT_PRESENT"
        return None

    # ------------------------------------------------------------------ evaluate
    def evaluate(self, ctx: StrategyContext) -> Decision:
        cur = ctx.current
        start = cur.start.astimezone(IST)
        close_t = cur.end.astimezone(IST).time()
        day = start.date()
        t = slot_of(start.time())
        if t is None:
            return self._no(ctx, "DATA_INVALID", "MISALIGNED_BAR")
        if not (self.window[0] <= close_t <= self.window[1]):
            return self._no(ctx, "NO_PATTERN", "WINDOW_CLOSED")
        if self.one_per_day and (ctx.symbol, day) in self._emitted:
            return self._no(ctx, "NO_PATTERN", "DAY_USED")
        cal = ctx.calibration
        c = cal.for_symbol(ctx.symbol) if cal is not None else None
        if cal is None or c is None:
            return self._no(ctx, "DATA_INVALID", "NO_CALIBRATION")
        if not c.valid:
            return self._no(ctx, "DATA_INVALID", f"CALIBRATION:{c.reason}")
        if cal.shape is None:
            return self._no(ctx, "DATA_INVALID", f"SHAPE:{cal.shape_reason}")
        if not cal.market.valid:
            return self._no(ctx, "DATA_INVALID", f"MARKET:{cal.market.reason}")
        if c.cumvol_med is None:
            return self._no(ctx, "DATA_INVALID", f"VOLUME:{c.cumvol_reason}")
        closes = slot_series(ctx.bars)
        if not np.all(np.isfinite(closes[: t + 1])):
            return self._no(ctx, "DATA_INVALID", "MISSING_SLOT")
        nifty = ctx.index_bars.get("IDX:NIFTY50") or ctx.nifty_bars
        n_cl = slot_series([b for b in nifty if b.session_date == day])
        if not np.all(np.isfinite(n_cl[: t + 1])):
            return self._no(ctx, "DATA_INVALID", "MISSING_MARKET_SLOT")
        factor_used = c.factor
        f_cl = slot_series([b for b in ctx.index_bars.get(c.factor, []) if b.session_date == day])
        if not np.all(np.isfinite(f_cl[: t + 1])):
            factor_used, f_cl = "IDX:NIFTY50", n_cl
        p = residual_paths(closes, f_cl, c.beta, c.s2, cal.shape)
        Z, E, e, V = p["Z"][t], p["E"][t], p["e"][t], p["V"][t]
        if not (np.isfinite(Z) and np.isfinite(E) and np.isfinite(e)):
            return self._no(ctx, "DATA_INVALID", "PATH_NOT_FINITE")
        key = (ctx.symbol, day)
        self.scan_max[key] = max(self.scan_max.get(key, 0.0), abs(float(Z)))
        if self.mode == "SCAN":
            return self._no(ctx, "NO_PATTERN", "SCAN_ONLY", float(Z))
        if abs(Z) < self.z_star:
            return self._no(ctx, "NO_PATTERN", "NO_SETUP", float(Z))
        if not e * E < 0:
            return self._no(ctx, "NO_PATTERN", "NO_TURN", float(Z))
        rv = rvol_path(slot_series(ctx.bars, "volume"), c.cumvol_med)[t]
        if not np.isfinite(rv) or not (self.rvol_band[0] <= rv <= self.rvol_band[1]):
            return self._no(ctx, "NO_PATTERN", "VOLUME_ABNORMAL", float(Z))
        if self.variant == "RESID_REV":
            why = self._news_blocked(ctx, day)
            if why:
                return self._no(ctx, "NO_PATTERN", f"NEWS_BLOCKED:{why}", float(Z))
        zm = market_path(n_cl, cal.market.s2N)[t]
        if not np.isfinite(zm) or abs(zm) > cal.market.zm_pct[t]:
            return self._no(ctx, "NO_PATTERN", "MARKET_FILTER", float(Z))
        sg = -1 if E > 0 else 1
        side = "BUY" if sg > 0 else "SELL"
        p0 = float(closes[t])
        if key not in self.candidates:
            self.candidates[key] = {"symbol": ctx.symbol, "day": day, "slot": t, "sg": sg, "E": float(E),
                                    "Z": float(Z), "beta": float(c.beta), "factor_used": factor_used, "p0": p0}
        toward, away = ("down", "down") if sg > 0 else ("up", "up")   # BUY: targets above -> round down; stop below -> down
        targets = [(round_to_tick(p0 * math.exp(sg * k * abs(E)), toward), frac) for k, frac in self.targets]
        h_eff = (LAST_HELD_SLOT - t) if self.hold == "EOD" else min(int(self.hold), LAST_HELD_SLOT - t)
        sigma_hold = float(np.sqrt(np.sum(c.s2px[t + 1:t + 1 + h_eff])))
        if self.stop_mult * sigma_hold > self.stop_skip:
            return self._no(ctx, "NO_PATTERN", "STOP_TOO_WIDE_SKIP", float(Z))
        s = max(self.stop_floor, self.stop_mult * sigma_hold)
        stop = round_to_tick(p0 * (1 - sg * s), away)
        if abs(targets[0][0] - p0) / p0 < self.min_t1:
            return self._no(ctx, "NO_PATTERN", "COST_HURDLE", float(Z))
        ee = p["e"][1:t + 1]
        phi = float(np.sum(ee[1:] * ee[:-1]) / np.sum(ee[:-1] ** 2)) if t >= 3 and np.sum(ee[:-1] ** 2) > 0 else None
        intent = SignalIntent.from_ctx(ctx, self.name, side, stop, targets, max_bars=h_eff, is_shadow=self.is_shadow)
        intent.r_basis = "stop_limit"
        intent.priority_score = abs(float(Z))
        vix_bars = ctx.index_bars.get("IDX:INDIAVIX") or []
        intent.diagnostics = {
            "Z": float(Z), "E": float(E), "sigma_E": float(math.sqrt(V)), "beta": c.beta, "RVOL": float(rv),
            "Z_M": float(zm), "factor": c.factor, "factor_used": factor_used, "factor_weight": None,
            "phi_ar1_intraday": phi, "is_expiry_day": is_monthly_stock_expiry(day), "slot": t, "h_eff": h_eff,
            "sigma_hold": sigma_hold, "stop_pct": s,
            "vix": float(vix_bars[-1].close) if vix_bars else cal.market.vix_prev_close,
            "vix_source": "intraday" if vix_bars else "prev_daily_close", "variant": self.variant,
            "news_filter": self.variant == "RESID_REV", "events_coverage": getattr(ctx.events, "coverage", "NONE"),
        }
        self._emitted.add(key)
        self.reasons["SIGNAL"] = self.reasons.get("SIGNAL", 0) + 1
        if self.keep_log:
            self.log.append((ctx.symbol, ctx.decision_time.isoformat(), "SIGNAL", float(Z)))
        return Decision(self.name, ctx.symbol, "SIGNAL", intent)
