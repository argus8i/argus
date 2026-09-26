"""
research/framework/evaluate.py
==============================
The only place a paper strategy's evidence is judged. Inputs are PROSPECTIVE results only (the desk never passes
LATE or REPLAY rows); the numbers come from the strategy's pre-registration, never from a caller's choice.

  clusters < futility_after                       CONTINUE
  futility_after <= clusters < review_after       STOP_FUTILE if mean + 1.2816 x SE < 0, else CONTINUE
  clusters >= review_after                        PASS if mean > 0 and t >= t_pass, else FAIL (final)
  any non-finite value                            INVALID (fail closed)
SE is cluster-robust CR1 (research/decision/stats.clustered_se), clustered by plan day.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Sequence

import numpy as np

from research.decision.stats import Z_FUTILITY, clustered_se


def cluster_stats(values: Sequence[float], clusters: Sequence) -> Dict[str, Any]:
    x = np.asarray(values, dtype=float)
    mean = float(x.mean()) if len(x) else math.nan
    se = clustered_se(x, list(clusters)) if len(x) else math.nan
    t = mean / se if math.isfinite(se) and se > 0 else math.nan
    return {"n": int(len(x)), "clusters": len(set(clusters)), "mean": mean, "se": se, "t": t}


def decide(values: Sequence[float], clusters: Sequence, *, review_after: int, futility_after: int,
           t_pass: float = 2.0, z_futility: float = Z_FUTILITY) -> Dict[str, Any]:
    if len(values) != len(clusters):
        raise ValueError("values and clusters differ in length")
    if not len(values):
        return {"decision": "NO_DATA", "reason": "no prospective result yet", "stats": cluster_stats([], [])}
    if not all(isinstance(v, (int, float)) and math.isfinite(float(v)) for v in values):
        return {"decision": "INVALID", "reason": "a non-finite result (fail closed)", "stats": {"n": len(values)}}
    st = cluster_stats(values, clusters)
    g = st["clusters"]
    if g >= review_after:
        ok = st["mean"] > 0 and math.isfinite(st["t"]) and st["t"] >= t_pass
        return {"decision": "PASS" if ok else "FAIL", "stats": st,
                "reason": f"{g} clusters >= {review_after}: mean {st['mean']:.4f}, t {st['t']:.2f} (needs > 0 and "
                          f">= {t_pass})"}
    if g >= futility_after and math.isfinite(st["se"]) and st["mean"] + z_futility * st["se"] < 0:
        return {"decision": "STOP_FUTILE", "stats": st,
                "reason": f"mean + {z_futility} x SE = {st['mean'] + z_futility * st['se']:.4f} < 0 after {g} clusters"}
    return {"decision": "CONTINUE", "stats": st, "reason": f"{g} of {review_after} clusters"}
