"""
Synthetic market generator for P4/P5 tests (plan P4: "use synthetic generators").

Sessions are post-CAS business days from 3 Aug 2026 (outside the plan holdout), 24 bars for stocks and
25 for indices. Factor returns and stock residuals follow a known diurnal variance shape, so tests can
check that calibration recovers beta and the shape.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

import numpy as np

from research.backtest.bars import Bar, CandleStore, DailyBar, round_to_tick
from research.data.session_shape import slot_start

IST = timezone(timedelta(hours=5, minutes=30))

# U-shaped intraday variance profile over slots 1..23 (index 0 unused)
TRUE_SHAPE = np.array([np.nan] + [1.8 - 1.6 * np.sin(np.pi * (b - 1) / 22) + 0.3 * (b > 20) for b in range(1, 24)])
TRUE_SHAPE[1:] /= TRUE_SHAPE[1:].mean()
VOL_PROFILE = np.array([3.0] + [2.0 - 1.4 * np.sin(np.pi * (b - 1) / 23) for b in range(1, 25)])


def business_days(start: date, n: int) -> List[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


@dataclass
class SyntheticSpec:
    n_sessions: int = 70
    stocks: Sequence[str] = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF")
    betas: Sequence[float] = (1.4, 0.8, 1.0, 1.2, 0.6, 1.1)
    factor: str = "IDX:NIFTYMETAL"
    sigma_f: float = 0.0012          # per-slot factor sd (average)
    sigma_e: float = 0.0020          # per-slot residual sd (average)
    sigma_n: float = 0.0009          # per-slot NIFTY sd
    start: date = date(2026, 8, 3)
    seed: int = 7
    base_volume: int = 40_000
    price: float = 500.0


def make_store(spec: SyntheticSpec = SyntheticSpec(), overrides: Optional[Dict] = None) -> CandleStore:
    """overrides: {(symbol, session_index): {slot: close_multiplier}} applied after generation, and
    {("VOL", symbol, session_index): multiplier} for whole-session volume scaling."""
    rng = np.random.default_rng(spec.seed)
    days = business_days(spec.start, spec.n_sessions)
    sd = np.sqrt(TRUE_SHAPE[1:])
    intraday: Dict[str, Dict[date, List[Bar]]] = {}
    daily: Dict[str, List[DailyBar]] = {}
    series_ret: Dict[str, np.ndarray] = {}
    fac = rng.normal(0, spec.sigma_f, (len(days), 23)) * sd
    nif = 0.7 * fac + rng.normal(0, spec.sigma_n, (len(days), 23)) * sd
    series_ret[spec.factor] = fac
    series_ret["IDX:NIFTY50"] = nif
    for s, b in zip(spec.stocks, spec.betas):
        series_ret[s] = b * fac + rng.normal(0, spec.sigma_e, (len(days), 23)) * sd
    overrides = overrides or {}
    for name, rets in series_ret.items():
        is_index = name.startswith("IDX:")
        nbars = 25 if is_index else 24
        level = 20000.0 if is_index else spec.price
        intraday[name], daily[name] = {}, []
        for i, d in enumerate(days):
            gap = rng.normal(0, 0.004)
            closes = [level * np.exp(gap)]
            for k in range(23):
                closes.append(closes[-1] * np.exp(rets[i, k]))
            closes.append(closes[-1] * np.exp(rng.normal(0, 0.0005)))
            mult = overrides.get((name, i), {})
            for slot, m in mult.items():
                for k in range(slot, len(closes)):
                    closes[k] *= m
            bars = []
            prev = closes[0] / np.exp(rng.normal(0, 0.001))
            vmult = overrides.get(("VOL", name, i), 1.0)
            for k in range(nbars):
                c = closes[k]
                o = prev
                hi, lo = max(o, c) * (1 + abs(rng.normal(0, 0.0006))), min(o, c) * (1 - abs(rng.normal(0, 0.0006)))
                if not is_index:
                    o, c, hi, lo = (round_to_tick(x) for x in (o, c, hi, lo))
                    hi, lo = max(hi, o, c), min(lo, o, c)
                vol = 0 if is_index else int(spec.base_volume * VOL_PROFILE[k] * np.exp(rng.normal(0, 0.25)) * vmult)
                bars.append(Bar(name, datetime.combine(d, slot_start(k), IST), 15, float(o), float(hi), float(lo),
                                float(c), vol))
                prev = c
            intraday[name][d] = bars
            daily[name].append(DailyBar(name, d, bars[0].open, max(b.high for b in bars), min(b.low for b in bars),
                                        bars[-1].close, sum(b.volume for b in bars)))
            level = bars[-1].close
    # INDIA VIX: daily only, 300 prior days around 14 plus the session days
    vix_days = business_days(spec.start - timedelta(days=440), 300) + days
    daily["IDX:INDIAVIX"] = [DailyBar("IDX:INDIAVIX", d, 14, 14.5, 13.5, float(13 + (k % 5) * 0.5), 0)
                             for k, d in enumerate(sorted(set(vix_days)))]
    intraday["IDX:INDIAVIX"] = {}
    return CandleStore(intraday, daily)


def sessions(spec: SyntheticSpec = SyntheticSpec()) -> List[date]:
    return business_days(spec.start, spec.n_sessions)
