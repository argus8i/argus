"""
research/decision/clusters.py
=============================
Weekly correlation clusters for the one-position-per-cluster cap (plan P6.4).

- Input: correlations of MARKET-RESIDUAL 15-minute returns (returns 1..23) over the prior 60 sessions,
  all strictly before the week's first session (as_of precedes the week).
  residual = r - beta * r_NIFTY, beta = OLS through the origin per symbol over the same sessions.
- Method: average linkage, merging while the average correlation between clusters is > 0.4. A cluster
  larger than 10% of the eligible universe is split by complete linkage at the same threshold, with merges
  that would exceed the cap refused, so no cluster ever holds more than 10% of the names.
- Why not single linkage: it chains. A factor simulation with 180 names and mean rho 0.29 produced one
  cluster of 139 names, which would collapse a 3-slot book under the cap (plan P6.4).
- Symbols with too little history get the shared id "UNCLUSTERED" (fail closed: their correlation is
  unknown, so at most one of them can be held at a time under max_per_cluster = 1).

No scipy: linkage is implemented directly (N ~ 210, O(N^3) with numpy).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

THRESHOLD_RHO = 0.4
MAX_FRAC = 0.10
LOOKBACK = 60
MIN_SESSIONS = 40
UNCLUSTERED = "UNCLUSTERED"
IST = timezone(timedelta(hours=5, minutes=30))


def linkage_labels(dist: np.ndarray, threshold: float, method: str,
                   max_size: Optional[int] = None) -> np.ndarray:
    """Agglomerative clustering on a symmetric distance matrix. Merge the closest pair while its linkage
    distance is < threshold (and, if max_size is set, the merged size would not exceed it).
    method: 'average' or 'complete' (Lance-Williams updates). Returns labels 0..k-1."""
    n = len(dist)
    D = np.array(dist, dtype=float, copy=True)
    np.fill_diagonal(D, np.inf)
    D[~np.isfinite(D)] = np.inf
    size = np.ones(n)
    active = np.ones(n, dtype=bool)
    members: Dict[int, List[int]] = {i: [i] for i in range(n)}
    while True:
        M = D.copy()
        M[~active, :] = np.inf
        M[:, ~active] = np.inf
        if max_size is not None:
            M[(size[:, None] + size[None, :]) > max_size] = np.inf
        k = int(np.argmin(M))
        i, j = divmod(k, n)
        if not np.isfinite(M[i, j]) or M[i, j] >= threshold:
            break
        if method == "average":
            new = (size[i] * D[i] + size[j] * D[j]) / (size[i] + size[j])
        elif method == "complete":
            new = np.maximum(D[i], D[j])
        else:
            raise ValueError(method)
        D[i, :], D[:, i] = new, new
        D[i, i] = np.inf
        D[j, :], D[:, j] = np.inf, np.inf
        size[i] += size[j]
        active[j] = False
        members[i].extend(members.pop(j))
    labels = np.empty(n, dtype=int)
    for lab, (_, mem) in enumerate(sorted(members.items(), key=lambda kv: min(kv[1]))):
        labels[mem] = lab
    return labels


def cluster_from_corr(corr: np.ndarray, names: Sequence[str], threshold_rho: float = THRESHOLD_RHO,
                      max_frac: float = MAX_FRAC) -> Dict[str, int]:
    """Average linkage at rho > threshold, then split any cluster above max_frac * N by size-capped
    complete linkage. Returns name -> cluster number."""
    n = len(names)
    if n == 0:
        return {}
    cap = max(1, int(np.floor(max_frac * n)))
    dist = 1.0 - np.asarray(corr, dtype=float)
    thr = 1.0 - threshold_rho
    labels = linkage_labels(dist, thr, "average")
    out = np.empty(n, dtype=int)
    next_id = 0
    for lab in np.unique(labels):
        idx = np.where(labels == lab)[0]
        if len(idx) <= cap:
            out[idx] = next_id
            next_id += 1
            continue
        sub = linkage_labels(dist[np.ix_(idx, idx)], thr, "complete", max_size=cap)
        for s in np.unique(sub):
            out[idx[sub == s]] = next_id
            next_id += 1
    return {names[i]: int(out[i]) for i in range(n)}


def _session_returns(store: Any, symbol: str, day: date) -> Optional[np.ndarray]:
    """Log returns 1..23 of one session, or None when any slot 0..23 is missing or non-positive."""
    from research.data.session_shape import slot_of

    closes = np.full(24, np.nan)
    for b in store.bars(symbol, day):
        s = slot_of(b.start.astimezone(IST).time())
        if s is not None and s < 24:
            closes[s] = b.close
    if not np.all(np.isfinite(closes)) or np.any(closes <= 0):
        return None
    return np.diff(np.log(closes))


def residual_corr(store: Any, symbols: Sequence[str], as_of: date, market: str = "IDX:NIFTY50",
                  lookback: int = LOOKBACK, min_sessions: int = MIN_SESSIONS,
                  cache: Optional[Dict[Tuple[str, date], Optional[np.ndarray]]] = None
                  ) -> Tuple[List[str], np.ndarray, List[str]]:
    """Correlation matrix of market-residual 15m returns over the last `lookback` market sessions strictly
    before as_of. Returns (clustered symbols, corr, symbols with fewer than min_sessions usable sessions)."""
    import pandas as pd

    cache = {} if cache is None else cache           # (symbol, day) -> returns; shared across weeks by the caller

    def ret(sym: str, d: date) -> Optional[np.ndarray]:
        if (sym, d) not in cache:
            cache[(sym, d)] = _session_returns(store, sym, d)
        return cache[(sym, d)]

    msess = [d for d in store.sessions(market) if d < as_of][-lookback:]
    mret = {d: r for d in msess if (r := ret(market, d)) is not None}
    days = sorted(mret)
    pos = {d: i for i, d in enumerate(days)}
    cols, names, short = [], [], []
    for s in symbols:
        have = set(store.sessions(s))
        rets = {d: r for d in days if d in have and (r := ret(s, d)) is not None}
        if len(rets) < min_sessions:
            short.append(s)
            continue
        R = np.vstack([rets[d] for d in sorted(rets)])
        F = np.vstack([mret[d] for d in sorted(rets)])
        den = float(np.sum(F * F))
        beta = float(np.sum(R * F) / den) if den > 0 else 0.0
        full = np.full((len(days), 23), np.nan)
        for d, r in rets.items():
            full[pos[d]] = r - beta * mret[d]
        cols.append(full.ravel())
        names.append(s)
    if not names:
        return [], np.zeros((0, 0)), short
    corr = pd.DataFrame(np.column_stack(cols), columns=names).corr(min_periods=min_sessions * 23 // 2).to_numpy()
    corr = np.where(np.isfinite(corr), corr, 0.0)          # pairs without enough overlap: uncorrelated
    np.fill_diagonal(corr, 1.0)
    return names, corr, short


def weekly_clusters(store: Any, symbols: Sequence[str], sessions: Sequence[date],
                    **kw: Any) -> Dict[date, Dict[str, str]]:
    """Cluster ids for every session, recomputed once per ISO week from data strictly before the week's
    first session. Ids look like '2026-W38:C3'; short-history symbols share '<week>:UNCLUSTERED'."""
    by_week: Dict[Tuple[int, int], List[date]] = defaultdict(list)
    for d in sorted(sessions):
        by_week[tuple(d.isocalendar())[:2]].append(d)
    out: Dict[date, Dict[str, str]] = {}
    cache: Dict[Tuple[str, date], Optional[np.ndarray]] = {}
    for (y, w), days in sorted(by_week.items()):
        names, corr, short = residual_corr(store, symbols, days[0], cache=cache, **kw)
        labels = cluster_from_corr(corr, names) if names else {}
        tag = f"{y}-W{w:02d}"
        mapping = {s: f"{tag}:C{c}" for s, c in labels.items()}
        mapping.update({s: f"{tag}:{UNCLUSTERED}" for s in short})
        for d in days:
            out[d] = mapping
    return out
