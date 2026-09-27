"""
research/features/calibration.py
================================
Point-in-time calibration (plan P4.2, Appendix A.6-A.7). For a session d, everything here is computed
from sessions strictly before d; nothing reads session d or later.

Per symbol (SymbolCalibration):
- beta: OLS through the origin of r[b] on the factor return f[b], pooled over slots 1..23 of the prior
  `lookback` sessions, shrunk to the prior: w = n/(n + n0) with n = number of (session, slot) pairs,
  beta = clip(w*beta_ols + (1-w)*prior, lo, hi);
- s2: winsorised mean of e^2 = (r - beta*f)^2 pooled over the same pairs (the residual variance level);
- rel_slot[b]: per-slot winsorised mean of e^2 divided by s2 (the symbol's input to the universe shape);
- s2px[b]: per-slot winsorised mean of r^2 (price variance; stop sizing, A.9);
- cumvol_med[t]: median over the prior `rvol lookback` sessions of cumulative volume through slot t.
Per session (SessionCalibration): the universe shape (A.6), normalised to mean 1 over slots 1..23.
Market (MarketCalibration): NIFTY per-slot variance s2N, the per-slot percentile of |Z_M| over prior
sessions (market filter), VIX_ref = median of the prior 250 daily INDIA VIX closes (>= 120 required).

A prior session is usable for a symbol only if slots 0..23 all have positive prices (and, for stocks,
positive volume) and the factor has complete slots 0..23 that day. When the mapped sectoral index has no
bars on a date, NIFTY 50 is used for that date and counted in factor_fallback_sessions.
Every object carries n_sessions, valid and a reason; invalid objects never carry partial numbers.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from research.features import timeprofile as tp

NIFTY = "IDX:NIFTY50"
VIX = "IDX:INDIAVIX"
VIX_LOOKBACK, VIX_MIN = 250, 120          # plan P6.3 (A.10); used here only as a diagnostic


@dataclass(frozen=True)
class CalibrationConfig:
    beta_lookback: int
    beta_n0: float
    beta_prior: float
    beta_clip: Tuple[float, float]
    resid_lookback: int
    resid_winsor: float
    resid_min_valid: int
    shape_lookback: int
    px_lookback: int
    px_min_valid: int
    px_winsor: float
    rvol_lookback: int
    rvol_min_valid: int
    market_index: str
    market_pct: float
    market_lookback: int
    shape_min_symbols: int = 5

    @classmethod
    def from_prereg(cls, spec: Mapping[str, Any]) -> "CalibrationConfig":
        """Every value comes from the pre-registration's `calibration` block (no hidden defaults)."""
        c = spec["calibration"]
        b, rv, sh, px, vo, mk = (c["beta"], c["resid_var"], c["shape"], c["price_var_by_slot"], c["rvol"],
                                 c["market_filter"])
        if str(b["bars"]) != "1..23" or str(sh["bars"]) != "1..23":
            raise ValueError("calibration bars must be 1..23 (plan A.6)")
        idx = str(mk["index"]).upper()
        return cls(beta_lookback=int(b["lookback_sessions"]), beta_n0=float(b["shrink_n0_pairs"]),
                   beta_prior=float(b["prior"]), beta_clip=(float(b["clip"][0]), float(b["clip"][1])),
                   resid_lookback=int(rv["lookback_sessions"]), resid_winsor=float(rv["winsor_pct"]),
                   resid_min_valid=int(rv["min_valid"]), shape_lookback=int(sh["lookback_sessions"]),
                   px_lookback=int(px["lookback_sessions"]), px_min_valid=int(px["min_valid"]),
                   px_winsor=float(px["winsor_pct"]), rvol_lookback=int(vo["lookback_sessions"]),
                   rvol_min_valid=int(vo["min_valid"]),
                   market_index="IDX:NIFTY50" if idx in ("NIFTY50", "IDX:NIFTY50") else idx,
                   market_pct=float(mk["percentile"]), market_lookback=int(mk["lookback_sessions"]))


@dataclass(frozen=True)
class SymbolCalibration:
    symbol: str
    session: date
    factor: str
    valid: bool
    reason: str
    n_sessions: int = 0
    n_pairs: int = 0
    beta: float = float("nan")
    beta_ols: float = float("nan")
    s2: float = float("nan")
    rel_slot: Optional[np.ndarray] = None
    s2px: Optional[np.ndarray] = None
    cumvol_med: Optional[np.ndarray] = None
    cumvol_reason: str = ""
    factor_fallback_sessions: int = 0


@dataclass(frozen=True)
class MarketCalibration:
    session: date
    valid: bool
    reason: str
    n_sessions: int = 0
    s2N: Optional[np.ndarray] = None
    zm_pct: Optional[np.ndarray] = None
    vix_ref: Optional[float] = None
    vix_prev_close: Optional[float] = None
    vix_reason: str = ""


@dataclass
class SessionCalibration:
    session: date
    shape: Optional[np.ndarray]
    shape_reason: str
    market: MarketCalibration
    symbols: Dict[str, SymbolCalibration] = field(default_factory=dict)
    n_sessions: int = 0

    @property
    def valid(self) -> bool:
        return self.shape is not None and self.market.valid

    def for_symbol(self, symbol: str) -> Optional[SymbolCalibration]:
        return self.symbols.get(symbol)


class _History:
    """Session x slot matrices for one series, built once from the store (which already applies the
    holdout guard). Rows are ordered by date; calibration slices rows strictly before the session."""

    def __init__(self, store: Any, symbol: str) -> None:
        self.symbol = symbol
        self.dates: List[date] = list(store.sessions(symbol))
        n = len(self.dates)
        self.closes = np.full((n, tp.SLOTS), np.nan)
        self.vols = np.full((n, tp.SLOTS), -1.0)
        from research.data.session_shape import slot_of
        from datetime import timedelta, timezone

        ist = timezone(timedelta(hours=5, minutes=30))
        for i, d in enumerate(self.dates):
            arr = store.session_arrays(symbol, d) if hasattr(store, "session_arrays") else None
            if arr is not None:
                from datetime import datetime

                for k in range(len(arr["close"])):
                    s = slot_of(datetime.fromtimestamp(int(arr["start_epoch"][k]), ist).time())
                    if s is not None and s < tp.SLOTS:
                        self.closes[i, s] = arr["close"][k]
                        self.vols[i, s] = arr["volume"][k]
            else:
                for b in store.bars(symbol, d):
                    s = slot_of(b.start.astimezone(ist).time())
                    if s is not None and s < tp.SLOTS:
                        self.closes[i, s] = b.close
                        self.vols[i, s] = b.volume
        c = self.closes[:, :24]
        self.complete = np.all(np.isfinite(c) & (c > 0), axis=1)
        self.vol_ok = np.all(self.vols[:, :24] > 0, axis=1)
        self._pos = {d: i for i, d in enumerate(self.dates)}

    def before(self, day: date, lookback: int) -> np.ndarray:
        """Row indices of the last `lookback` sessions strictly before `day`."""
        k = bisect.bisect_left(self.dates, day)
        return np.arange(max(0, k - lookback), k)

    def row(self, d: date) -> Optional[int]:
        return self._pos.get(d)


class CalibrationProvider:
    def __init__(self, store: Any, factor_map: Mapping[str, str], config: CalibrationConfig,
                 symbols: Optional[Sequence[str]] = None, cache_sessions: int = 4) -> None:
        self.store = store
        self.factor_map = dict(factor_map)
        self.cfg = config
        self.symbols = sorted(symbols) if symbols is not None else [
            s for s in store.symbols if store.kind(s) == "TRADABLE"]
        self._hist: Dict[str, _History] = {}
        self._cache: Dict[date, SessionCalibration] = {}
        self._cache_order: List[date] = []
        self._cache_max = cache_sessions

    # ------------------------------------------------------------------ helpers
    def history(self, symbol: str) -> Optional[_History]:
        if symbol not in self._hist:
            if symbol not in self.store.symbols:
                return None
            self._hist[symbol] = _History(self.store, symbol)
        return self._hist[symbol]

    def factor_for(self, symbol: str) -> str:
        return self.factor_map.get(symbol, self.cfg.market_index)

    # ------------------------------------------------------------------ per symbol
    def symbol_calibration(self, symbol: str, day: date) -> SymbolCalibration:
        cfg = self.cfg
        factor = self.factor_for(symbol)
        h = self.history(symbol)
        if h is None:
            return SymbolCalibration(symbol, day, factor, False, "NO_HISTORY")
        lookback = max(cfg.beta_lookback, cfg.resid_lookback, cfg.px_lookback)
        rows = h.before(day, lookback)
        fh, nh = self.history(factor), self.history(cfg.market_index)
        R, F, used, fallbacks = [], [], 0, 0
        for i in rows:
            if not (h.complete[i] and h.vol_ok[i]):
                continue
            d = h.dates[i]
            fr = None
            for src, is_fallback in ((fh, False), (nh, True)):
                if src is None:
                    continue
                j = src.row(d)
                if j is not None and src.complete[j]:
                    fr = src.closes[j, :24]
                    fallbacks += int(is_fallback and factor != cfg.market_index)
                    break
            if fr is None:
                continue
            R.append(tp.session_returns(h.closes[i, :24]))
            F.append(tp.session_returns(fr))
            used += 1
        if used < cfg.resid_min_valid:
            return SymbolCalibration(symbol, day, factor, False, f"INSUFFICIENT_SESSIONS_{used}_OF_{cfg.resid_min_valid}",
                                     n_sessions=used, factor_fallback_sessions=fallbacks)
        R_, F_ = np.vstack(R)[:, 1:24], np.vstack(F)[:, 1:24]
        den = float(np.sum(F_ ** 2))
        if not np.isfinite(den) or den <= 0:
            return SymbolCalibration(symbol, day, factor, False, "ZERO_FACTOR_VARIANCE", n_sessions=used)
        n_pairs = int(R_.size)
        beta_ols = float(np.sum(R_ * F_) / den)
        w = n_pairs / (n_pairs + cfg.beta_n0)
        beta = float(np.clip(w * beta_ols + (1 - w) * cfg.beta_prior, *cfg.beta_clip))
        E = R_ - beta * F_
        s2 = tp.winsor_mean((E ** 2).ravel(), cfg.resid_winsor)
        if s2 is None or s2 <= 0:
            return SymbolCalibration(symbol, day, factor, False, "ZERO_RESIDUAL_VARIANCE", n_sessions=used)
        rel = np.full(24, np.nan)
        for j in range(23):
            m = tp.winsor_mean(E[:, j] ** 2, cfg.resid_winsor)
            rel[j + 1] = (m / s2) if m is not None else np.nan
        full_R = np.full((R_.shape[0], 24), np.nan)
        full_R[:, 1:24] = R_
        s2px, px_reason = tp.slot_variance(full_R[-cfg.px_lookback:], cfg.px_min_valid, cfg.px_winsor)
        if s2px is None:
            return SymbolCalibration(symbol, day, factor, False, f"PRICE_VAR_{px_reason}", n_sessions=used)
        vrows = [h.vols[i] for i in h.before(day, cfg.rvol_lookback) if h.vol_ok[i]]
        cum_med, cum_reason = tp.cumvol_medians(np.vstack(vrows) if vrows else np.zeros((0, tp.SLOTS)),
                                                cfg.rvol_min_valid)
        return SymbolCalibration(symbol, day, factor, True, "OK", n_sessions=used, n_pairs=n_pairs, beta=beta,
                                 beta_ols=beta_ols, s2=float(s2), rel_slot=rel, s2px=s2px, cumvol_med=cum_med,
                                 cumvol_reason=cum_reason, factor_fallback_sessions=fallbacks)

    # ------------------------------------------------------------------ market
    def market_calibration(self, day: date) -> MarketCalibration:
        cfg = self.cfg
        nh = self.history(cfg.market_index)
        if nh is None:
            return MarketCalibration(day, False, "NO_MARKET_HISTORY")
        rows = [i for i in nh.before(day, cfg.market_lookback) if nh.complete[i]]
        if len(rows) < cfg.px_min_valid:
            return MarketCalibration(day, False, f"INSUFFICIENT_SESSIONS_{len(rows)}_OF_{cfg.px_min_valid}",
                                     n_sessions=len(rows))
        RN = tp.returns_matrix(nh.closes[rows, :24])
        s2N, reason = tp.slot_variance(RN, cfg.px_min_valid, cfg.px_winsor)
        if s2N is None:
            return MarketCalibration(day, False, f"MARKET_VAR_{reason}", n_sessions=len(rows))
        cum = np.cumsum(RN[:, 1:24], axis=1)
        scale = np.sqrt(np.cumsum(s2N[1:24]))
        zm = np.abs(cum / scale)
        zm_pct = np.full(24, np.nan)
        zm_pct[1:24] = np.percentile(zm, cfg.market_pct, axis=0)
        vix_ref, vix_prev, vix_reason = None, None, "NO_VIX_HISTORY"
        if VIX in self.store.symbols:
            closes = [b.close for b in self.store.daily_before(VIX, day)][-VIX_LOOKBACK:]
            closes = [c for c in closes if c and c > 0]
            if closes:
                vix_prev = float(closes[-1])
            if len(closes) >= VIX_MIN:
                vix_ref, vix_reason = float(np.median(closes)), "OK"
            else:
                vix_reason = f"INSUFFICIENT_VIX_{len(closes)}_OF_{VIX_MIN}"
        return MarketCalibration(day, True, "OK", n_sessions=len(rows), s2N=s2N, zm_pct=zm_pct, vix_ref=vix_ref,
                                 vix_prev_close=vix_prev, vix_reason=vix_reason)

    # ------------------------------------------------------------------ session
    def session(self, day: date) -> SessionCalibration:
        if day in self._cache:
            return self._cache[day]
        syms = {s: self.symbol_calibration(s, day) for s in self.symbols}
        shape, shape_reason = tp.diurnal_shape([c.rel_slot for c in syms.values() if c.valid],
                                               self.cfg.shape_min_symbols)
        out = SessionCalibration(day, shape, shape_reason, self.market_calibration(day), syms,
                                 n_sessions=max((c.n_sessions for c in syms.values()), default=0))
        self._cache[day] = out
        self._cache_order.append(day)
        if len(self._cache_order) > self._cache_max:
            self._cache.pop(self._cache_order.pop(0), None)
        return out
