"""Calibration from your own recorded depth snapshots.

What market-by-price snapshots can identify, and what they cannot:
  identifiable      a LOWER BOUND on level-aggregate cancellation intensity kappa_k (from
                    intervals with no trades, where a fall in displayed quantity is a cancel
                    or modify-out net of additions); depth persistence h_k(lag) (the taker
                    haircut); stop skip rates.
  NOT identifiable  P(cancel | order age) or position-dependent cancellation. Those need
                    order-level data (NSE tick-by-tick). The queue model therefore uses
                    kappa_k with an explicit allocation rule, and brackets the rule with
                    envelopes instead of pretending to know it.

No published estimate exists for NSE mid-cap F&O stocks, so these functions are the
deliverable, not a number. Your live_depth_ticks.csv stores best prices and totals but
not per-level quantities, so it cannot feed them; record all displayed levels.
"""
from __future__ import annotations

import math
from typing import Dict, List, Sequence

from .marketdata import Session, Snapshot


def cancel_intensity(path: Sequence[Snapshot], side: str, tick: float, max_level: int = 5) -> Dict[int, Dict[str, float]]:
    """kappa_k = cancelled quantity / (displayed quantity x seconds), per level index k
    counted from the touch, over consecutive snapshot pairs with zero traded volume.

    Under a constant intensity a unit of displayed depth survives tau seconds with
    probability exp(-kappa_k tau), so P(cancel within tau) = 1 - exp(-kappa_k tau).

    This is a LOWER BOUND on kappa. Snapshots show net flow, and additions inside the
    same interval mask cancellations; the bound is tight only when the two rarely share
    an interval (thin levels, fast snapshots). Quiet intervals are also selected. Both
    biases under-credit cancellations ahead of you, which is the safe direction.
    """
    acc = {k: {"exposure_qty_s": 0.0, "cancelled_qty": 0.0, "intervals": 0.0} for k in range(max_level)}
    for p, s in zip(path, path[1:]):
        if p.symbol != s.symbol or p.session is not Session.CONTINUOUS or s.session is not Session.CONTINUOUS:
            continue
        if s.cum_volume != p.cum_volume:
            continue
        dt = s.recv_ts - p.recv_ts
        if dt <= 0:
            continue
        for k, lv in enumerate(p.side_levels(side)[:max_level]):
            q_now = s.qty_at(side, lv.price, tick)
            if q_now is None:
                continue                                  # moved out of view: unobservable
            a = acc[k]
            a["exposure_qty_s"] += lv.qty * dt
            a["cancelled_qty"] += max(0, lv.qty - q_now)
            a["intervals"] += 1
    for a in acc.values():
        a["kappa_per_s"] = a["cancelled_qty"] / a["exposure_qty_s"] if a["exposure_qty_s"] > 0 else math.nan
    return acc


def p_cancel(kappa_per_s: float, tau_s: float) -> float:
    return 1.0 - math.exp(-kappa_per_s * tau_s)


def depth_persistence(path: Sequence[Snapshot], side: str, tick: float, lag_s: float,
                      max_level: int = 5) -> Dict[int, float]:
    """Taker haircut h_k(lag): mean of min(q(t+lag), q(t)) / q(t) at the same price, for the
    level that was k-th from the touch at t. Use the lag equal to your measured order
    round trip. A price still displayed is counted even if it changed index."""
    sums: Dict[int, List[float]] = {k: [] for k in range(max_level)}
    j = 0
    for i, p in enumerate(path):
        while j < len(path) and path[j].recv_ts < p.recv_ts + lag_s:
            j += 1
        if j >= len(path):
            break
        s = path[j]
        if s.symbol != p.symbol or s.session is not Session.CONTINUOUS or p.session is not Session.CONTINUOUS:
            continue
        for k, lv in enumerate(p.side_levels(side)[:max_level]):
            q = s.qty_at(side, lv.price, tick)
            if q is None:
                continue
            sums[k].append(min(q, lv.qty) / lv.qty)
    return {k: (sum(v) / len(v) if v else math.nan) for k, v in sums.items()}
