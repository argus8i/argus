"""
research/decision/stats.py
==========================
Small statistics used by the decision engine (plan P6.2, A.11). The locked gate
(research/backtest/metrics.evaluate_gate) is not modified; `metrics.clustered_t` returns only t, so the
standard error it uses is exposed here with the identical CR1 formula.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Dict, Sequence

import numpy as np

Z_LB95 = 1.645          # one-sided 95% lower bound (A.11)
Z_FUTILITY = 1.2816     # one-sided 90% upper bound used by the futility rule (P6.6)


def clustered_se(values: Sequence[float], clusters: Sequence) -> float:
    """Cluster-robust SE of the mean, CR1 (G/(G-1)), clustered by session. Same formula as
    metrics.clustered_t, so mean / clustered_se == clustered_t. NaN when undefined (n < 2 or G < 2)."""
    x = np.asarray(values, dtype=float)
    if len(x) != len(clusters) or len(x) < 2 or not np.all(np.isfinite(x)):
        return math.nan
    mean = x.mean()
    sums: Dict[object, float] = defaultdict(float)
    for v, g in zip(x - mean, clusters):
        sums[g] += v
    g_count = len(sums)
    if g_count < 2:
        return math.nan
    var = g_count / (g_count - 1) * sum(s * s for s in sums.values()) / (len(x) ** 2)
    return float(math.sqrt(var)) if var > 0 else math.nan


def lb95(mean: float, se: float) -> float:
    """A.11: mean - 1.645 * se (NaN when either input is undefined)."""
    if not (math.isfinite(mean) and math.isfinite(se)):
        return math.nan
    return mean - Z_LB95 * se
