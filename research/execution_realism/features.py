"""Pillar 2 inputs: Adverse-Selection Defense features, the winner's-curse test,
and a walk-forward ridge fit for the fill-conditional EV model.

No feature here has a verified predictive weight on NSE. The code computes the
features and tests them; it does not pretend to know their coefficients.
"""
from __future__ import annotations

import math
import random
import statistics
from typing import Dict, List, Optional, Sequence

from .capacity import EVModel
from .marketdata import EPS, Snapshot


# ----------------------------------------------------------- order flow
def ofi(path: Sequence[Snapshot]) -> float:
    """Cont-Kukanov-Stoikov (2014) best-level order-flow imbalance over a snapshot path:
    e_n = 1{b_n>=b_n-1} qb_n - 1{b_n<=b_n-1} qb_n-1 - 1{a_n<=a_n-1} qa_n + 1{a_n>=a_n-1} qa_n-1."""
    total = 0.0
    for p, s in zip(path, path[1:]):
        if not (p.bids and p.asks and s.bids and s.asks):
            raise ValueError("OFI needs two-sided books")
        bp, bn, ap, an = p.bids[0].price, s.bids[0].price, p.asks[0].price, s.asks[0].price
        total += ((s.bids[0].qty if bn >= bp - EPS else 0) - (p.bids[0].qty if bn <= bp + EPS else 0)
                  - (s.asks[0].qty if an <= ap + EPS else 0) + (p.asks[0].qty if an >= ap - EPS else 0))
    return total


def ofi_normalized(path: Sequence[Snapshot]) -> float:
    depth = statistics.fmean((s.bids[0].qty + s.asks[0].qty) / 2 for s in path)
    return ofi(path) / depth if depth > 0 else 0.0


def absorption_at_level(path: Sequence[Snapshot], level: float, tick: float) -> Dict[str, float]:
    """Hidden supply at the breakout price (ask side).

    Counts volume certain to have traded at `level` (LTQ of prints at the level while
    it stayed the best ask) against the displayed depletion of that level. A ratio well
    above 1 means the level kept refilling: an iceberg or a distributor selling into
    the breakout. That is the 'distribution trap' signature, measured rather than guessed.
    """
    half = tick / 2
    certain = depletion = 0.0
    refills = intervals = 0
    for p, s in zip(path, path[1:]):
        if not (p.asks and s.asks) or abs(p.asks[0].price - level) >= half or abs(s.asks[0].price - level) >= half:
            continue
        intervals += 1
        dv = s.cum_volume - p.cum_volume
        traded = dv > 0 and abs(s.ltp - level) < half and s.ltq
        if traded:
            certain += min(s.ltq, dv)
        dq = s.asks[0].qty - p.asks[0].qty
        if dq < 0:
            depletion += -dq
        elif dq > 0 and traded:
            refills += 1
    return {"intervals": float(intervals), "certain_traded": certain, "displayed_depletion": depletion,
            "refills": float(refills), "absorption_ratio": certain / depletion if depletion > 0 else
            (math.inf if certain > 0 else 0.0)}


# ---------------------------------------------------------- price features
def spread_atr(s: Snapshot, atr: float) -> float:
    if not (s.bids and s.asks) or atr <= 0:
        raise ValueError("two-sided book and positive ATR required")
    return (s.asks[0].price - s.bids[0].price) / atr


def speed_atr(path: Sequence[Snapshot], atr: float, window_s: float = 10.0) -> float:
    """Price change over the last `window_s` in ATR units: the 'fills easiest' proxy."""
    now = path[-1]
    ref = None
    for s in path:
        if s.recv_ts <= now.recv_ts - window_s:
            ref = s
    if ref is None:
        raise ValueError("path shorter than the speed window")
    return (now.ltp - ref.ltp) / atr


def vwap_extension_atr(s: Snapshot, atr: float) -> float:
    """Distance above the session VWAP (ATP) in ATR units."""
    if s.atp is None:
        raise ValueError("ATP required")
    return (s.ltp - s.atp) / atr


def book_imbalance(s: Snapshot) -> float:
    b, a = s.displayed_qty("BID"), s.displayed_qty("ASK")
    return (b - a) / (b + a) if (a + b) else 0.0


def rel_strength(ltp: float, open_px: float, idx_ltp: float, idx_open: float) -> float:
    return (ltp / open_px - 1.0) - (idx_ltp / idx_open - 1.0)


def gap_vs_index(open_px: float, prev_close: float, idx_open: float, idx_prev_close: float) -> float:
    return (open_px / prev_close - 1.0) - (idx_open / idx_prev_close - 1.0)


# ------------------------------------------------------- winner's curse
def winners_curse(records: Sequence[Dict], *, n_boot: int = 2000, seed: int = 7) -> Dict[str, float]:
    """E[R | filled] - E[R | signalled], from a shadow book that follows EVERY signal.

    records: {"disposition": "FILLED" | "COLLAR_ABORT" | "RETRACE_ABORT" | "NOT_SELECTED" | ...,
              "r_signal": outcome in R measured from the signal price at a fixed horizon}
    Identity used as a check: E[R|F] - E[R] = Cov(R, 1_F) / P(F).
    A negative difference whose bootstrap interval excludes zero is the curse.
    """
    rs = [float(r["r_signal"]) for r in records]
    fl = [1.0 if r["disposition"] == "FILLED" else 0.0 for r in records]
    n, nf = len(rs), sum(fl)
    if n == 0 or nf == 0:
        raise ValueError("need signals and at least one fill")

    def diff(idx: List[int]) -> Optional[float]:
        f = [rs[i] for i in idx if fl[i]]
        if not f:
            return None
        return statistics.fmean(f) - statistics.fmean(rs[i] for i in idx)

    base = diff(list(range(n)))
    pf = nf / n
    mr = statistics.fmean(rs)
    cov = statistics.fmean((rs[i] - mr) * (fl[i] - pf) for i in range(n))
    rng = random.Random(seed)
    boots = []
    for _ in range(n_boot):
        d = diff([rng.randrange(n) for _ in range(n)])
        if d is not None:
            boots.append(d)
    boots.sort()
    by = {}
    for r in records:
        by.setdefault(r["disposition"], []).append(float(r["r_signal"]))
    out = {"n_signals": float(n), "n_filled": nf, "mean_all": mr,
           "mean_filled": statistics.fmean(rs[i] for i in range(n) if fl[i]), "curse": base,
           "curse_identity": cov / pf, "ci95_lo": boots[int(0.025 * len(boots))],
           "ci95_hi": boots[int(0.975 * len(boots)) - 1]}
    for k, v in by.items():
        out[f"mean_{k}"] = statistics.fmean(v)
        out[f"n_{k}"] = float(len(v))
    return out


# ------------------------------------------------------------ EV model
def _solve(a: List[List[float]], b: List[float]) -> List[float]:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[piv][c]) < 1e-12:
            raise ValueError("singular system")
        m[c], m[piv] = m[piv], m[c]
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    return [m[i][n] / m[i][i] for i in range(n)]


def fit_ev_model(rows: Sequence[Dict[str, float]], y: Sequence[float], features: Sequence[str], *,
                 lam: float, trained_through: str, oos_rows: Sequence[Dict[str, float]] = (),
                 oos_y: Sequence[float] = ()) -> EVModel:
    """Ridge regression of realised R on standardised features, for FILLED trades only.
    Calibrated (usable for EV arbitration) only with >= 200 training fills and an
    out-of-sample R^2 computed on later trades."""
    if len(rows) != len(y) or len(rows) < len(features) + 2:
        raise ValueError("not enough rows")
    means = {f: statistics.fmean(r[f] for r in rows) for f in features}
    stds = {f: (statistics.pstdev([r[f] for r in rows]) or 1.0) for f in features}
    X = [[(r[f] - means[f]) / stds[f] for f in features] for r in rows]
    ybar = statistics.fmean(y)
    k = len(features)
    xtx = [[sum(x[i] * x[j] for x in X) + (lam if i == j else 0.0) for j in range(k)] for i in range(k)]
    xty = [sum(x[i] * (yy - ybar) for x, yy in zip(X, y)) for i in range(k)]
    w = _solve(xtx, xty)
    model = EVModel(dict(zip(features, w)), ybar, means, stds, len(rows), float("nan"), trained_through)
    if oos_rows:
        pred = [model.expected_r(r) for r in oos_rows]
        mu = statistics.fmean(oos_y)
        ss_res = sum((a - b) ** 2 for a, b in zip(oos_y, pred))
        ss_tot = sum((a - mu) ** 2 for a in oos_y) or 1e-12
        model = EVModel(model.weights, ybar, means, stds, len(rows), 1 - ss_res / ss_tot, trained_through)
    return model
