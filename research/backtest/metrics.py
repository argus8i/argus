"""
research/backtest/metrics.py
============================
Performance statistics for the research backtester.

- Sharpe / Sortino / Calmar / drawdown (depth and longest time under water).
- Probabilistic and Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2012 and 2014). Sharpe ratios in
  these two are PER PERIOD (not annualised) and kurtosis is raw (3 for a normal).
- Probability of Backtest Overfitting via CSCV (Bailey, Borwein, Lopez de Prado & Zhu, 2015).
- Day-clustered t-statistics and day-block bootstrap intervals: trades on the same session are
  correlated (0.32 mean pairwise 15m correlation in the repo's data), so treating them as independent
  overstates evidence.
- Markouts and the locked Track 2 evidence gate.

Every function returns NaN rather than a number it cannot support (no dispersion, too few points).
"""
from __future__ import annotations

import math
from collections import defaultdict
from itertools import combinations
from statistics import NormalDist
from typing import Callable, Dict, Iterable, List, Mapping, Sequence, Tuple

import numpy as np

_N = NormalDist()
EULER_GAMMA = 0.5772156649015329


def _clean(x: Iterable[float]) -> np.ndarray:
    a = np.asarray(list(x) if not isinstance(x, np.ndarray) else x, dtype=float)
    return a[np.isfinite(a)]


def sharpe(returns: Sequence[float], periods_per_year: float = 252.0) -> float:
    r = _clean(returns)
    if len(r) < 2:
        return math.nan
    sd = r.std(ddof=1)
    if not math.isfinite(sd) or sd <= 1e-15:
        return math.nan
    return float(r.mean() / sd * math.sqrt(periods_per_year))


def sortino(returns: Sequence[float], periods_per_year: float = 252.0, target: float = 0.0) -> float:
    r = _clean(returns)
    if len(r) < 2:
        return math.nan
    downside = np.minimum(r - target, 0.0)
    dd = math.sqrt(float(np.mean(downside ** 2)))
    if dd <= 1e-15:
        return math.nan
    return float((r.mean() - target) / dd * math.sqrt(periods_per_year))


def max_drawdown(equity: Sequence[float]) -> Dict[str, float]:
    e = np.asarray(equity, dtype=float)
    if len(e) == 0:
        return {"max_drawdown": math.nan, "peak_index": -1, "trough_index": -1, "longest_underwater_periods": 0}
    peaks = np.maximum.accumulate(e)
    dd = e / peaks - 1.0
    trough = int(np.argmin(dd))
    peak = int(np.argmax(e[: trough + 1]))
    longest = run = 0
    for below in e < peaks - 1e-12 * np.abs(peaks):
        run = run + 1 if below else 0
        longest = max(longest, run)
    return {"max_drawdown": float(dd[trough]), "peak_index": peak, "trough_index": trough,
            "longest_underwater_periods": longest}


def calmar(returns: Sequence[float], periods_per_year: float = 252.0) -> float:
    r = _clean(returns)
    if len(r) < 2:
        return math.nan
    equity = np.concatenate([[1.0], np.cumprod(1.0 + r)])
    mdd = max_drawdown(equity)["max_drawdown"]
    if not mdd < 0:
        return math.nan
    growth = equity[-1]
    if growth <= 0:
        return math.nan
    annual = growth ** (periods_per_year / len(r)) - 1.0
    return float(annual / abs(mdd))


def _moments(r: np.ndarray) -> Tuple[float, float]:
    m = r.mean()
    s = r.std(ddof=0)
    if s <= 1e-15:
        return 0.0, 3.0
    z = (r - m) / s
    return float(np.mean(z ** 3)), float(np.mean(z ** 4))


def probabilistic_sharpe(sr: float, n: int, skew: float, kurtosis: float, sr_benchmark: float = 0.0) -> float:
    """P(true per-period Sharpe > benchmark) given an estimate from n observations."""
    if n < 2 or not math.isfinite(sr):
        return math.nan
    var = 1.0 - skew * sr + (kurtosis - 1.0) / 4.0 * sr * sr
    if var <= 0:
        return math.nan
    return _N.cdf((sr - sr_benchmark) * math.sqrt(n - 1) / math.sqrt(var))


def expected_max_sharpe(n_trials: int, trials_sharpe_variance: float) -> float:
    """Expected maximum per-period Sharpe among n_trials skill-less strategies."""
    if n_trials < 2 or trials_sharpe_variance <= 0:
        return 0.0
    a = _N.inv_cdf(1.0 - 1.0 / n_trials)
    b = _N.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(trials_sharpe_variance) * ((1.0 - EULER_GAMMA) * a + EULER_GAMMA * b)


def deflated_sharpe(returns: Sequence[float], n_trials: int, trials_sharpe_variance: float) -> float:
    """PSR against the Sharpe that the best of n_trials skill-less strategies would show by luck."""
    r = _clean(returns)
    if len(r) < 3:
        return math.nan
    sd = r.std(ddof=1)
    if sd <= 1e-15:
        return math.nan
    sr = r.mean() / sd
    skew, kurt = _moments(r)
    return probabilistic_sharpe(sr, len(r), skew, kurt, expected_max_sharpe(n_trials, trials_sharpe_variance))


def clustered_t(values: Sequence[float], clusters: Sequence) -> float:
    """Mean / cluster-robust standard error (CR1: G/(G-1) small-sample factor)."""
    x = np.asarray(values, dtype=float)
    if len(x) != len(clusters) or len(x) < 2:
        return math.nan
    mean = x.mean()
    sums: Dict[object, float] = defaultdict(float)
    for v, g in zip(x - mean, clusters):
        sums[g] += v
    g_count = len(sums)
    if g_count < 2:
        return math.nan
    var = g_count / (g_count - 1) * sum(s * s for s in sums.values()) / (len(x) ** 2)
    if var <= 0:
        return math.nan
    return float(mean / math.sqrt(var))


def block_bootstrap_ci(values: Sequence[float], clusters: Sequence, stat: Callable = np.mean, reps: int = 2000,
                       alpha: float = 0.05, seed: int = 0) -> Tuple[float, float]:
    """Resample whole clusters (sessions) with replacement."""
    x = np.asarray(values, dtype=float)
    groups: Dict[object, List[float]] = defaultdict(list)
    for v, g in zip(x, clusters):
        groups[g].append(v)
    keys = list(groups)
    if len(keys) < 2:
        return (math.nan, math.nan)
    rng = np.random.default_rng(seed)
    stats = np.empty(reps)
    for i in range(reps):
        pick = rng.integers(0, len(keys), len(keys))
        stats[i] = stat(np.concatenate([groups[keys[j]] for j in pick]))
    return (float(np.quantile(stats, alpha / 2)), float(np.quantile(stats, 1 - alpha / 2)))


def probability_of_backtest_overfitting(perf: np.ndarray, n_splits: int = 16,
                                        metric: Callable[[np.ndarray], float] | None = None) -> Dict[str, object]:
    """
    CSCV. `perf` is T x N (rows = time, columns = configurations). Rows are cut into n_splits blocks;
    every half of the blocks is used once as in-sample. PBO = share of splits where the in-sample
    winner ranks at or below the out-of-sample median (logit <= 0).
    """
    m = np.asarray(perf, dtype=float)
    if m.ndim != 2 or m.shape[1] < 2 or n_splits < 2 or n_splits % 2:
        raise ValueError("perf must be T x N with N >= 2 and an even n_splits >= 2")
    t = (m.shape[0] // n_splits) * n_splits
    if t < n_splits * 2:
        raise ValueError("not enough rows for the requested splits")
    blocks = np.array_split(np.arange(t), n_splits)

    def default_metric(block: np.ndarray) -> np.ndarray:
        sd = block.std(axis=0, ddof=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            out = np.where(sd > 1e-15, block.mean(axis=0) / sd, -np.inf)
        return out

    score = metric or default_metric
    logits: List[float] = []
    n_cols = m.shape[1]
    for combo in combinations(range(n_splits), n_splits // 2):
        is_rows = np.concatenate([blocks[i] for i in combo])
        oos_rows = np.concatenate([blocks[i] for i in range(n_splits) if i not in combo])
        best = int(np.argmax(score(m[is_rows])))
        oos = score(m[oos_rows])
        rank = 1 + int(np.sum(oos < oos[best]))          # 1 = worst, N = best
        omega = rank / (n_cols + 1.0)
        logits.append(math.log(omega / (1.0 - omega)))
    arr = np.array(logits)
    return {"pbo": float(np.mean(arr <= 0.0)), "logits": arr.tolist(), "n_combinations": len(arr)}


def markout_bps(side: int, fill_price: float, future_mid: float) -> float:
    """M_h = s * (mid_{t+h} - p_fill) / p_fill in basis points (s = +1 buy, -1 sell)."""
    if fill_price <= 0 or not math.isfinite(future_mid):
        return math.nan
    return 1e4 * side * (future_mid - fill_price) / fill_price


def r_multiple_summary(net_r: Sequence[float], sessions: Sequence, seed: int = 0) -> Dict[str, float]:
    r = np.asarray(net_r, dtype=float)
    if len(r) == 0:
        return {"n": 0}
    lo, hi = block_bootstrap_ci(r, sessions, seed=seed) if len(set(sessions)) >= 2 else (math.nan, math.nan)
    return {"n": int(len(r)), "mean": float(r.mean()), "median": float(np.median(r)),
            "win_rate": float(np.mean(r > 0)), "sd": float(r.std(ddof=1)) if len(r) > 1 else math.nan,
            "t_clustered": clustered_t(r, sessions), "ci95_low": lo, "ci95_high": hi}


def evaluate_gate(trades: Sequence[Mapping[str, object]], sessions_observed: int, min_sessions: int = 60,
                  min_executions: int = 85, min_t: float = 2.0,
                  admissible: Tuple[str, ...] = ("E2", "E3")) -> Dict[str, object]:
    """
    Locked Track 2 gate (25-Sep decision): >= 60 sessions, >= 85 verified E2/E3 executions and a
    day-clustered t-statistic >= 2.0 on net R. Bar-modelled fills are never admissible. E3-only
    counts are reported next to the gate because E2 depends on a queue model's estimate.
    """
    good = [t for t in trades if t.get("evidence_class") in admissible]
    e3 = [t for t in good if t.get("evidence_class") == "E3"]
    reasons: List[str] = []
    if sessions_observed < min_sessions:
        reasons.append(f"sessions {sessions_observed} < {min_sessions}")
    if len(good) < min_executions:
        reasons.append(f"admissible ({'/'.join(admissible)}) executions {len(good)} < {min_executions}")
    t_stat = clustered_t([float(t["net_r"]) for t in good], [t["session"] for t in good]) if good else math.nan
    if not (math.isfinite(t_stat) and t_stat >= min_t):
        reasons.append(f"day-clustered t {t_stat:.2f} < {min_t}" if math.isfinite(t_stat)
                       else "day-clustered t undefined")
    return {"passed": not reasons, "reasons": reasons, "sessions_observed": sessions_observed,
            "admissible_executions": len(good), "e3_only_executions": len(e3), "total_trades": len(trades),
            "t_stat": t_stat, "mean_net_r": float(np.mean([float(t["net_r"]) for t in good])) if good else math.nan}


def two_tranche_breakeven_hurdle(
    c_friction_r: float,
    q_runner: float = 0.5,
    t1_r: float = 1.5,
    t2_r: float = 3.0,
) -> float:
    """
    Closed-form breakeven win rate hurdle for a 2-tranche bracket:
        p* = (1 + c) / (1 + 0.5 * T1_R + 0.5 * T2_R * q)
    For T1 = 1.5R, T2 = 3.0R:
        p* = (1 + c) / (1.75 + 1.5 * q)
    Under driftless diffusion (random walk):
        P(T1 before -1R) = 1 / (1 + 1.5) = 40.0%
        P(runner reaches +3R before breakeven | T1 hit) = 50.0% (q = 0.5).
    """
    denominator = 1.0 + 0.5 * t1_r + 0.5 * t2_r * q_runner
    if denominator <= 0:
        return math.nan
    return float((1.0 + c_friction_r) / denominator)


def slot_cap_binding_stop(
    risk_budget_rs: float = 1500.0,
    slot_cap_rs: float = 58333.33,
) -> float:
    """
    Calculates the exact stop percentage threshold below which the slot notional cap binds
    before the rupee risk budget binds:
        S* = risk_budget_rs / slot_cap_rs = 1500 / 58333.33 = 2.5714%
    For any stop tighter than S*, 1R < risk_budget_rs.
    """
    if slot_cap_rs <= 0:
        return math.nan
    return float(risk_budget_rs / slot_cap_rs)


def friction_in_r(
    stop_pct: float,
    notional_rs: float = 58333.33,
    friction_rs: float = 61.86,
    risk_budget_rs: float = 1500.0,
) -> float:
    """
    Calculates round-trip friction expressed as a fraction of 1R:
        1R = min(risk_budget_rs, notional_rs * stop_pct)
        c = friction_rs / 1R
    """
    if stop_pct <= 0 or notional_rs <= 0:
        return math.nan
    one_r = min(risk_budget_rs, notional_rs * stop_pct)
    if one_r <= 0:
        return math.nan
    return float(friction_rs / one_r)

