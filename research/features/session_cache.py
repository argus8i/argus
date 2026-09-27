"""
research/features/session_cache.py
==================================
Causal intraday paths for one session (plan P4.4, Appendix A.7): r, f, e, E, V, Z, RVOL_cum and Z_M.

Causality is structural: every path value at slot t is a cumulative quantity over slots <= t, so
computing a whole session at once gives the same value at t as computing it from bars up to t. A test
checks this equality on random data. Strategy adapters call these functions on ctx.bars, which the
engine already cuts at the decision time; the SessionCache below is only a speed-up for studies.

A missing or non-positive close at slot k makes every path value from k onward NaN (never zero).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

import numpy as np

from research.data.session_shape import slot_of
from research.features import timeprofile as tp

IST = timezone(timedelta(hours=5, minutes=30))


def slot_series(bars: Sequence[Any], field: str = "close") -> np.ndarray:
    """25-vector by slot from Bar objects (NaN, or -1 for volume, where a slot is absent)."""
    fill = -1.0 if field == "volume" else np.nan
    out = np.full(tp.SLOTS, fill)
    for b in bars:
        s = slot_of(b.start.astimezone(IST).time())
        if s is not None and s < tp.SLOTS:
            out[s] = float(getattr(b, field))
    return out


def _cum_until_nan(x: np.ndarray) -> np.ndarray:
    """Cumulative sum over slots 1..23 that turns NaN at the first NaN and stays NaN."""
    out = np.full(24, np.nan)
    acc = 0.0
    for b in range(1, 24):
        if not np.isfinite(x[b]):
            break
        acc += x[b]
        out[b] = acc
    return out


def residual_paths(stock_closes: np.ndarray, factor_closes: np.ndarray, beta: float, s2: float,
                   shape: np.ndarray) -> Dict[str, np.ndarray]:
    """A.7: r, f, e, E, V, Z as 24-vectors indexed by slot (index 0 is NaN)."""
    r = tp.session_returns(np.asarray(stock_closes, dtype=float)[:24])
    f = tp.session_returns(np.asarray(factor_closes, dtype=float)[:24])
    e = r - beta * f
    v = np.full(24, np.nan)
    v[1:24] = s2 * np.asarray(shape, dtype=float)[1:24]
    E = _cum_until_nan(e)
    V = _cum_until_nan(v)
    with np.errstate(invalid="ignore", divide="ignore"):
        Z = np.where(np.isfinite(E) & np.isfinite(V) & (V > 0), E / np.sqrt(V), np.nan)
    return {"r": r, "f": f, "e": e, "E": E, "V": V, "Z": Z}


def market_path(index_closes: np.ndarray, s2N: np.ndarray) -> np.ndarray:
    """Z_M[t] = ln(N[t]/N[0]) / sqrt(sum_{b<=t} s2N[b]) (A.7), as a 24-vector."""
    fN = tp.session_returns(np.asarray(index_closes, dtype=float)[:24])
    cum = _cum_until_nan(fN)
    scale = _cum_until_nan(np.asarray(s2N, dtype=float))
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(np.isfinite(cum) & np.isfinite(scale) & (scale > 0), cum / np.sqrt(scale), np.nan)


def rvol_path(volumes: np.ndarray, cumvol_med: Optional[np.ndarray]) -> np.ndarray:
    out = np.full(tp.SLOTS, np.nan)
    if cumvol_med is None:
        return out
    cum = tp.cumulative_volume(volumes)
    with np.errstate(invalid="ignore", divide="ignore"):
        ok = np.isfinite(cum) & np.isfinite(cumvol_med) & (cumvol_med > 0)
        out[ok] = cum[ok] / cumvol_med[ok]
    return out


def config_hash(config: Any) -> str:
    payload = json.dumps(config.__dict__ if hasattr(config, "__dict__") else config, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class SessionCache:
    """Whole-session causal paths for every calibrated symbol, optionally persisted as parquet under
    research/outputs/session_cache/<config hash>/<session>.parquet (gitignored)."""
    provider: Any
    store: Any
    root: Optional[Path] = None

    def compute(self, day: date) -> Dict[str, Dict[str, np.ndarray]]:
        cal = self.provider.session(day)
        mkt = cal.market
        n_bars = self.store.bars(self.provider.cfg.market_index, day) if mkt.valid else []
        zm = market_path(slot_series(n_bars), mkt.s2N) if mkt.valid and n_bars else np.full(24, np.nan)
        out: Dict[str, Dict[str, np.ndarray]] = {}
        if cal.shape is None:
            return out
        for sym, c in cal.symbols.items():
            if not c.valid or day not in set(self.store.sessions(sym)):
                continue
            bars = self.store.bars(sym, day)
            fbars = self.store.bars(c.factor, day) if c.factor in self.store.symbols else []
            fcl = slot_series(fbars)
            if not np.all(np.isfinite(fcl[:24])):
                fcl = slot_series(n_bars)
            paths = residual_paths(slot_series(bars), fcl, c.beta, c.s2, cal.shape)
            paths["RVOL"] = rvol_path(slot_series(bars, "volume"), c.cumvol_med)
            paths["ZM"] = zm
            out[sym] = paths
        if self.root is not None:
            self._save(day, out)
        return out

    def _save(self, day: date, out: Mapping[str, Mapping[str, np.ndarray]]) -> None:
        import pandas as pd       # only when persisting (studies may use pandas; features logic does not)

        d = Path(self.root) / config_hash(self.provider.cfg)
        d.mkdir(parents=True, exist_ok=True)
        rows = [{"symbol": s, "slot": t, **{k: float(v[t]) if t < len(v) else float("nan") for k, v in p.items()}}
                for s, p in out.items() for t in range(24)]
        pd.DataFrame(rows).to_parquet(d / f"{day.isoformat()}.parquet", index=False)
