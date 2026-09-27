"""
research/features/timeprofile.py
================================
Time-of-day normalisation primitives (plan P4.1, Appendix A.6). Pure numpy; no data access.

Conventions
- A session is a row of 25 slots (0..24); slot k starts 09:15 + 15k minutes. Missing values are NaN
  (prices) or -1 (volume).
- Returns r[b] = ln(C[b] / C[b-1]) for b = 1..23 only: slot 0's return carries the overnight gap, and slot
  24 does not exist for post-CAS stocks. Index 0 of every returned 24-vector is NaN by construction.
- Every function returns (value, reason). value is None when the input cannot support the estimate,
  and reason says why ("OK" otherwise). Nothing silently returns 0.
"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

import numpy as np

SLOTS = 25
RET_SLOTS = np.arange(1, 24)          # b = 1..23


def _finite_positive(a: np.ndarray) -> np.ndarray:
    return np.isfinite(a) & (a > 0)


def session_returns(closes: np.ndarray) -> np.ndarray:
    """24-vector r[0..23] with r[0] = NaN and r[b] = ln(C[b]/C[b-1]) for b = 1..23 (NaN if either is bad)."""
    c = np.asarray(closes, dtype=float)
    if c.shape[0] < 24:
        c = np.concatenate([c, np.full(24 - c.shape[0], np.nan)])
    out = np.full(24, np.nan)
    prev, cur = c[0:23], c[1:24]
    ok = _finite_positive(prev) & _finite_positive(cur)
    out[1:24][ok] = np.log(cur[ok] / prev[ok])
    return out


def returns_matrix(closes: np.ndarray) -> np.ndarray:
    """Rows of session_returns for a (sessions x >=24) close matrix."""
    closes = np.atleast_2d(np.asarray(closes, dtype=float))
    out = np.full((closes.shape[0], 24), np.nan)
    prev, cur = closes[:, 0:23], closes[:, 1:24]
    ok = _finite_positive(prev) & _finite_positive(cur)
    tmp = np.full(prev.shape, np.nan)
    tmp[ok] = np.log(cur[ok] / prev[ok])
    out[:, 1:24] = tmp
    return out


def winsor_mean(values: np.ndarray, pct: float = 99.0) -> Optional[float]:
    """Mean after clipping above the pct-th percentile. Intended for non-negative values (squares)."""
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    if x.size == 0:
        return None
    cap = np.percentile(x, pct)
    return float(np.minimum(x, cap).mean())


def slot_variance(ret_rows: np.ndarray, min_valid: int = 40, winsor_pct: float = 99.0
                  ) -> Tuple[Optional[np.ndarray], str]:
    """Per-slot winsorised mean of squared returns over the given prior sessions (rows).
    Returns a 24-vector (index 0 NaN) or None when fewer than min_valid rows are complete."""
    rows = np.atleast_2d(np.asarray(ret_rows, dtype=float))
    complete = np.all(np.isfinite(rows[:, 1:24]), axis=1) if rows.size else np.zeros(0, bool)
    n = int(complete.sum())
    if n < min_valid:
        return None, f"INSUFFICIENT_SESSIONS_{n}_OF_{min_valid}"
    sq = rows[complete][:, 1:24] ** 2
    out = np.full(24, np.nan)
    for j in range(23):
        m = winsor_mean(sq[:, j], winsor_pct)
        out[j + 1] = m if m is not None else np.nan
    if not np.all(np.isfinite(out[1:24])) or np.any(out[1:24] <= 0):
        return None, "ZERO_OR_INVALID_VARIANCE"
    return out, "OK"


def normalise_shape(per_slot: np.ndarray) -> Tuple[Optional[np.ndarray], str]:
    """Scale a 24-vector so its mean over slots 1..23 is exactly 1."""
    v = np.asarray(per_slot, dtype=float)
    core = v[1:24]
    if not np.all(np.isfinite(core)) or np.any(core <= 0):
        return None, "INVALID_SHAPE_INPUT"
    out = np.full(24, np.nan)
    out[1:24] = core / core.mean()
    return out, "OK"


def diurnal_shape(relative_slot_vars: Sequence[np.ndarray], min_symbols: int = 5
                  ) -> Tuple[Optional[np.ndarray], str]:
    """Universe shape (A.6): mean over symbols of (per-slot mean e^2 / s2_i), normalised to mean 1.
    Each input is one symbol's 24-vector of mean e^2[b] / s2_i."""
    good = [np.asarray(v, dtype=float) for v in relative_slot_vars
            if v is not None and np.all(np.isfinite(np.asarray(v, dtype=float)[1:24]))]
    if len(good) < min_symbols:
        return None, f"TOO_FEW_SYMBOLS_{len(good)}_OF_{min_symbols}"
    return normalise_shape(np.mean(np.vstack(good), axis=0))


def cumulative_volume(volumes: np.ndarray) -> np.ndarray:
    """25-vector of cumulative volume through each slot; NaN from the first missing (negative/NaN) slot."""
    v = np.asarray(volumes, dtype=float)
    if v.shape[0] < SLOTS:
        v = np.concatenate([v, np.full(SLOTS - v.shape[0], np.nan)])
    bad = ~np.isfinite(v) | (v < 0)
    cum = np.cumsum(np.where(bad, 0.0, v))
    first_bad = np.argmax(bad) if bad.any() else SLOTS
    cum[first_bad:] = np.nan
    return cum


def cumvol_medians(prior_volume_rows: np.ndarray, min_valid: int = 15) -> Tuple[Optional[np.ndarray], str]:
    """Per-slot median over prior sessions of cumulative volume through that slot (A.6 RVOL denominator)."""
    rows = np.atleast_2d(np.asarray(prior_volume_rows, dtype=float))
    cums = np.vstack([cumulative_volume(r) for r in rows]) if rows.size else np.zeros((0, SLOTS))
    out = np.full(SLOTS, np.nan)
    for t in range(SLOTS):
        col = cums[:, t] if cums.size else np.zeros(0)
        col = col[np.isfinite(col)]
        if col.size >= min_valid:
            out[t] = float(np.median(col))
    if not np.all(np.isfinite(out[:24])) or np.any(out[:24] <= 0):
        n = int(np.isfinite(cums[:, 0]).sum()) if cums.size else 0
        return None, f"INSUFFICIENT_VOLUME_HISTORY_{n}_OF_{min_valid}"
    return out, "OK"


def cum_rvol(today_volumes: np.ndarray, t: int, medians: Optional[np.ndarray]) -> Tuple[Optional[float], str]:
    """RVOL[t] = cumulative volume through slot t today / median of the same quantity (A.6)."""
    if medians is None:
        return None, "NO_VOLUME_CALIBRATION"
    cum = cumulative_volume(today_volumes)
    if not (0 <= t < SLOTS) or not np.isfinite(cum[t]):
        return None, "MISSING_VOLUME_TODAY"
    den = medians[t]
    if not np.isfinite(den) or den <= 0:
        return None, "ZERO_DENOMINATOR"
    return float(cum[t] / den), "OK"
