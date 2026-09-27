"""
research/derivatives/pairs.py
=============================
Pairs research tools: Engle-Granger cointegration, a Kalman-filter hedge ratio, Ornstein-Uhlenbeck
half-life, rolling z-scores and two-leg sizing under the Track 2 risk budget.

A pair trade needs one short leg. The Track 2 governor rejects any order whose stop is above entry
(INVERTED_STOP), and the unified signal carries no side, so pairs are research-only until short-side
plumbing exists end to end. size_pair() marks this with requires_short_leg=True.

Engle-Granger: OLS y = a + b x, then an ADF test (no constant, since residuals are mean-zero) on the
residuals, compared with MacKinnon (2010) response-surface critical values for two variables with a
constant in the cointegrating regression.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional, Sequence

import numpy as np

# MacKinnon (2010), Table 2, N = 2, constant: tau(T) = b_inf + b1/T + b2/T^2
_EG_CRIT = {0.01: (-3.89644, -10.9519, -22.527), 0.05: (-3.33613, -6.1101, -6.823),
            0.10: (-3.04445, -4.2412, -2.720)}


def ols(y: Sequence[float], x: Sequence[float]):
    y_ = np.asarray(y, dtype=float)
    x_ = np.asarray(x, dtype=float)
    if len(y_) != len(x_) or len(y_) < 3:
        raise ValueError("y and x need equal length >= 3")
    X = np.column_stack([np.ones_like(x_), x_])
    coef, *_ = np.linalg.lstsq(X, y_, rcond=None)
    return float(coef[0]), float(coef[1]), y_ - X @ coef


def adf_tstat(series: Sequence[float], lags: int = 1, constant: bool = False) -> float:
    e = np.asarray(series, dtype=float)
    de = np.diff(e)
    rows = len(de) - lags
    if rows < 10:
        raise ValueError("series too short for the ADF regression")
    cols = [e[lags:-1]]
    for i in range(1, lags + 1):
        cols.append(de[lags - i:len(de) - i])
    if constant:
        cols.append(np.ones(rows))
    X = np.column_stack(cols)
    yv = de[lags:]
    coef, *_ = np.linalg.lstsq(X, yv, rcond=None)
    resid = yv - X @ coef
    dof = rows - X.shape[1]
    s2 = float(resid @ resid) / dof
    cov = s2 * np.linalg.inv(X.T @ X)
    se = math.sqrt(cov[0, 0])
    return float(coef[0] / se) if se > 0 else math.nan


def eg_critical_values(n_obs: int) -> Dict[float, float]:
    return {lvl: b0 + b1 / n_obs + b2 / n_obs ** 2 for lvl, (b0, b1, b2) in _EG_CRIT.items()}


@dataclass(frozen=True)
class EGResult:
    alpha: float
    beta: float
    adf_t: float
    critical_values: Dict[float, float]
    n_obs: int

    @property
    def cointegrated_5pct(self) -> bool:
        return math.isfinite(self.adf_t) and self.adf_t < self.critical_values[0.05]


def engle_granger(y: Sequence[float], x: Sequence[float], lags: int = 1) -> EGResult:
    a, b, resid = ols(y, x)
    return EGResult(alpha=a, beta=b, adf_t=adf_tstat(resid, lags=lags, constant=False),
                    critical_values=eg_critical_values(len(resid)), n_obs=len(resid))


def kalman_hedge_ratio(y: Sequence[float], x: Sequence[float], delta: float = 1e-4,
                       obs_var: Optional[float] = None) -> Dict[str, np.ndarray]:
    """Random-walk state [alpha, beta]; observation y_t = alpha_t + beta_t x_t + e_t."""
    y_ = np.asarray(y, dtype=float)
    x_ = np.asarray(x, dtype=float)
    n = len(y_)
    if obs_var is None:
        obs_var = float(np.var(ols(y_, x_)[2]))
    w = delta / (1.0 - delta) * np.eye(2)
    theta = np.zeros(2)
    P = np.eye(2) * 1e4                                   # diffuse prior
    betas, alphas, innov, innov_var = (np.empty(n) for _ in range(4))
    for t in range(n):
        H = np.array([1.0, x_[t]])
        P = P + w
        pred = H @ theta
        S = float(H @ P @ H + obs_var)
        e = y_[t] - pred
        K = P @ H / S
        theta = theta + K * e
        P = P - np.outer(K, H @ P)
        alphas[t], betas[t], innov[t], innov_var[t] = theta[0], theta[1], e, S
    return {"alpha": alphas, "beta": betas, "spread": innov, "spread_var": innov_var}


def ou_half_life(spread: Sequence[float]) -> Optional[float]:
    """AR(1) s_t = a + b s_{t-1} + e; theta = -ln b per bar; half-life = ln 2 / theta. None without reversion."""
    s = np.asarray(spread, dtype=float)
    if len(s) < 10:
        return None
    _, b, _ = ols(s[1:], s[:-1])
    if not 0.0 < b < 1.0 - 1e-6:
        return None
    return math.log(2.0) / -math.log(b)


def zscore(spread: Sequence[float], window: int) -> np.ndarray:
    s = np.asarray(spread, dtype=float)
    out = np.full(len(s), np.nan)
    for i in range(window, len(s)):
        prior = s[i - window:i]
        sd = prior.std(ddof=1)
        if sd > 0:
            out[i] = (s[i] - prior.mean()) / sd
    return out


@dataclass(frozen=True)
class PairSize:
    n_y: int
    n_x: int
    gross_notional_rs: float
    risk_rs: float
    binding_limit: str
    requires_short_leg: bool = True


def size_pair(price_y: float, price_x: float, beta: float, spread_sigma: float, z_entry: float = 2.0,
              z_stop: float = 3.2, risk_budget_rs: float = 1500.0, slot_cap_rs: float = 38000.00,
              cost_rate: float = 0.00106) -> PairSize:
    """One unit = 1 share of y against |beta| shares of x. Loss to the stop per unit is
    (z_stop - z_entry) * sigma plus round-trip costs on both legs. Both legs share one slot."""
    if min(price_y, price_x, spread_sigma) <= 0 or z_stop <= z_entry or beta == 0:
        raise ValueError("positive prices and spread sigma, z_stop > z_entry and a non-zero beta required")
    gross_unit = price_y + abs(beta) * price_x
    loss_unit = (z_stop - z_entry) * spread_sigma + cost_rate * gross_unit
    by_risk = int(risk_budget_rs // loss_unit)
    by_cap = int(slot_cap_rs // gross_unit)
    n = min(by_risk, by_cap)
    binding = "RISK_BUDGET" if by_risk <= by_cap else "SLOT_CAP"

    def legs(k: int):
        nx = int(round(abs(beta) * k))
        return nx, k * price_y + nx * price_x, k * (z_stop - z_entry) * spread_sigma + cost_rate * (k * price_y + nx * price_x)

    while n > 0:
        nx, gross, risk = legs(n)
        if gross <= slot_cap_rs + 1e-6 and risk <= risk_budget_rs + 1e-6:
            return PairSize(n, nx, round(gross, 2), round(risk, 2), binding)
        n -= 1
    return PairSize(0, 0, 0.0, 0.0, "ZERO_SIZE")
