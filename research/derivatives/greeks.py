"""
research/derivatives/greeks.py
==============================
Option pricing for research: vectorised Black-Scholes-Merton (continuous dividend yield q),
greeks, implied volatility, the bivariate normal CDF, a CRR binomial reference pricer,
Bjerksund-Stensland (2002) American approximation, and a rate curve for MIBOR-style tenors.

NSE stock and index options are EUROPEAN-style, so bs_price/bs_greeks are the correct models for them.
Bjerksund-Stensland is provided for American-style instruments and is checked against the CRR tree.
No rates are embedded: RateCurve must be built from dated, sourced MIBOR (or T-bill) observations.
"""
from __future__ import annotations

import math
from statistics import NormalDist
from typing import Dict, Sequence, Tuple, Union

import numpy as np

_N = NormalDist()
_erf = np.vectorize(math.erf, otypes=[float])
ArrayLike = Union[float, np.ndarray]


def norm_cdf(x: ArrayLike) -> ArrayLike:
    if np.ndim(x) == 0:
        return 0.5 * (1.0 + math.erf(float(x) / math.sqrt(2.0)))
    return 0.5 * (1.0 + _erf(np.asarray(x, dtype=float) / math.sqrt(2.0)))


def norm_pdf(x: ArrayLike) -> ArrayLike:
    a = np.asarray(x, dtype=float)
    out = np.exp(-0.5 * a * a) / math.sqrt(2.0 * math.pi)
    return float(out) if np.ndim(x) == 0 else out


def _prep(S, K, T, r, sigma, q):
    arrs = np.broadcast_arrays(*(np.asarray(v, dtype=float) for v in (S, K, T, r, sigma, q)))
    S_, K_, T_, r_, v_, q_ = arrs
    if np.any(S_ <= 0) or np.any(K_ <= 0) or np.any(T_ <= 0) or np.any(v_ <= 0):
        raise ValueError("S, K, T and sigma must be strictly positive")
    if not all(np.all(np.isfinite(a)) for a in arrs):
        raise ValueError("inputs must be finite")
    sqt = v_ * np.sqrt(T_)
    d1 = (np.log(S_ / K_) + (r_ - q_ + 0.5 * v_ * v_) * T_) / sqt
    return S_, K_, T_, r_, v_, q_, d1, d1 - sqt


def _scalar_if(template, value):
    return float(value) if all(np.ndim(t) == 0 for t in template) else value


def bs_price(S, K, T, r, sigma, q=0.0, kind: str = "C"):
    S_, K_, T_, r_, v_, q_, d1, d2 = _prep(S, K, T, r, sigma, q)
    df_q, df_r = np.exp(-q_ * T_), np.exp(-r_ * T_)
    if kind == "C":
        px = S_ * df_q * norm_cdf(d1) - K_ * df_r * norm_cdf(d2)
    elif kind == "P":
        px = K_ * df_r * norm_cdf(-d2) - S_ * df_q * norm_cdf(-d1)
    else:
        raise ValueError("kind must be 'C' or 'P'")
    return _scalar_if((S, K, T, r, sigma, q), px)


def bs_greeks(S, K, T, r, sigma, q=0.0, kind: str = "C") -> Dict[str, ArrayLike]:
    """delta, gamma (per Rs of spot), vega (per 1.00 of vol), theta (per year), rho (per 1.00 of rate)."""
    S_, K_, T_, r_, v_, q_, d1, d2 = _prep(S, K, T, r, sigma, q)
    df_q, df_r = np.exp(-q_ * T_), np.exp(-r_ * T_)
    pdf = norm_pdf(d1)
    gamma = df_q * pdf / (S_ * v_ * np.sqrt(T_))
    vega = S_ * df_q * pdf * np.sqrt(T_)
    common = -S_ * df_q * pdf * v_ / (2 * np.sqrt(T_))
    if kind == "C":
        delta = df_q * norm_cdf(d1)
        theta = common - r_ * K_ * df_r * norm_cdf(d2) + q_ * S_ * df_q * norm_cdf(d1)
        rho = K_ * T_ * df_r * norm_cdf(d2)
    elif kind == "P":
        delta = -df_q * norm_cdf(-d1)
        theta = common + r_ * K_ * df_r * norm_cdf(-d2) - q_ * S_ * df_q * norm_cdf(-d1)
        rho = -K_ * T_ * df_r * norm_cdf(-d2)
    else:
        raise ValueError("kind must be 'C' or 'P'")
    t = (S, K, T, r, sigma, q)
    return {k: _scalar_if(t, v) for k, v in
            {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta, "rho": rho}.items()}


def _iv_scalar(price, S, K, T, r, q, kind, lo, hi, tol, max_iter) -> float:
    df_q, df_r = math.exp(-q * T), math.exp(-r * T)
    if kind == "C":
        lower, upper = max(0.0, S * df_q - K * df_r), S * df_q
    else:
        lower, upper = max(0.0, K * df_r - S * df_q), K * df_r
    if not (lower + 1e-12 < price < upper - 1e-12):
        return math.nan                                   # outside no-arbitrage bounds: fail closed
    a, b = lo, hi
    if not bs_price(S, K, T, r, a, q, kind) <= price <= bs_price(S, K, T, r, b, q, kind):
        return math.nan
    for _ in range(max_iter):
        mid = 0.5 * (a + b)
        if bs_price(S, K, T, r, mid, q, kind) < price:
            a = mid
        else:
            b = mid
        if b - a < tol:
            break
    return 0.5 * (a + b)


def implied_vol(price, S, K, T, r, q=0.0, kind: str = "C", lo: float = 1e-4, hi: float = 5.0,
                tol: float = 1e-10, max_iter: int = 200):
    """Bisection on the monotone price-vol map; NaN when the price violates no-arbitrage bounds."""
    f = np.vectorize(lambda p, s, k, t, rr, qq: _iv_scalar(p, s, k, t, rr, qq, kind, lo, hi, tol, max_iter),
                     otypes=[float])
    out = f(price, S, K, T, r, q)
    return float(out) if np.ndim(out) == 0 else out


def bivariate_normal_cdf(a: float, b: float, rho: float) -> float:
    """P(X <= a, Y <= b) for standard normals with correlation rho, by Gauss-Legendre quadrature of
    int_{-inf}^{a} phi(x) Phi((b - rho x)/sqrt(1 - rho^2)) dx, split at the kink x = b/rho."""
    if rho >= 1.0 - 1e-12:
        return norm_cdf(min(a, b))
    if rho <= -1.0 + 1e-12:
        return max(0.0, norm_cdf(a) + norm_cdf(b) - 1.0)
    if rho == 0.0:
        return norm_cdf(a) * norm_cdf(b)
    lower = -12.0
    if a <= lower:
        return 0.0
    s = math.sqrt(1.0 - rho * rho)
    cuts = [lower, a]
    kink = b / rho
    if lower < kink < a:
        cuts = [lower, kink, a]
    nodes, weights = np.polynomial.legendre.leggauss(160)
    total = 0.0
    for lo, hi in zip(cuts, cuts[1:]):
        x = 0.5 * (hi - lo) * nodes + 0.5 * (hi + lo)
        f = norm_pdf(x) * norm_cdf((b - rho * x) / s)
        total += 0.5 * (hi - lo) * float(np.dot(weights, f))
    return min(1.0, max(0.0, total))


def crr_price(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0, kind: str = "C",
              american: bool = True, steps: int = 500) -> float:
    """Cox-Ross-Rubinstein binomial tree; the reference used to check Bjerksund-Stensland."""
    dt = T / steps
    u = math.exp(sigma * math.sqrt(dt))
    d = 1.0 / u
    p = (math.exp((r - q) * dt) - d) / (u - d)
    if not 0.0 < p < 1.0:
        raise ValueError("binomial probability outside (0, 1); increase steps")
    disc = math.exp(-r * dt)
    j = np.arange(steps + 1)
    prices = S * u ** j * d ** (steps - j)
    sign = 1.0 if kind == "C" else -1.0
    values = np.maximum(sign * (prices - K), 0.0)
    for n in range(steps - 1, -1, -1):
        prices = prices[:-1] * u
        values = disc * (p * values[1:] + (1 - p) * values[:-1])
        if american:
            values = np.maximum(values, sign * (prices - K))
    return float(values[0])


def _gbs_call(S, K, T, r, b, v):
    """Generalised Black-Scholes call with cost of carry b."""
    d1 = (math.log(S / K) + (b + 0.5 * v * v) * T) / (v * math.sqrt(T))
    d2 = d1 - v * math.sqrt(T)
    return S * math.exp((b - r) * T) * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)


def _phi(S, T, gamma, H, I, r, b, v):
    lam = (-r + gamma * b + 0.5 * gamma * (gamma - 1) * v * v) * T
    d = -(math.log(S / H) + (b + (gamma - 0.5) * v * v) * T) / (v * math.sqrt(T))
    kappa = 2 * b / (v * v) + (2 * gamma - 1)
    return math.exp(lam) * S ** gamma * (norm_cdf(d) - (I / S) ** kappa *
                                         norm_cdf(d - 2 * math.log(I / S) / (v * math.sqrt(T))))


def _psi(S, T2, gamma, H, I2, I1, t1, r, b, v):
    m = (b + (gamma - 0.5) * v * v)
    s1, s2 = v * math.sqrt(t1), v * math.sqrt(T2)
    e1 = (math.log(S / I1) + m * t1) / s1
    e2 = (math.log(I2 ** 2 / (S * I1)) + m * t1) / s1
    e3 = (math.log(S / I1) - m * t1) / s1
    e4 = (math.log(I2 ** 2 / (S * I1)) - m * t1) / s1
    f1 = (math.log(S / H) + m * T2) / s2
    f2 = (math.log(I2 ** 2 / (S * H)) + m * T2) / s2
    f3 = (math.log(I1 ** 2 / (S * H)) + m * T2) / s2
    f4 = (math.log(S * I1 ** 2 / (H * I2 ** 2)) + m * T2) / s2
    rho = math.sqrt(t1 / T2)
    lam = -r + gamma * b + 0.5 * gamma * (gamma - 1) * v * v
    kappa = 2 * b / (v * v) + (2 * gamma - 1)
    M = bivariate_normal_cdf
    return math.exp(lam * T2) * S ** gamma * (
        M(-e1, -f1, rho) - (I2 / S) ** kappa * M(-e2, -f2, rho)
        - (I1 / S) ** kappa * M(-e3, -f3, -rho) + (I1 / I2) ** kappa * M(-e4, -f4, -rho))


def _bs2002_call(S, K, T, r, b, v):
    if b >= r:                                   # early exercise never optimal
        return _gbs_call(S, K, T, r, b, v)
    v2 = v * v
    beta = (0.5 - b / v2) + math.sqrt((b / v2 - 0.5) ** 2 + 2 * r / v2)
    b_inf = beta / (beta - 1) * K
    b0 = max(K, r / (r - b) * K)
    t1 = 0.5 * (math.sqrt(5) - 1) * T
    h1 = -(b * t1 + 2 * v * math.sqrt(t1)) * K * K / ((b_inf - b0) * b0)
    h2 = -(b * T + 2 * v * math.sqrt(T)) * K * K / ((b_inf - b0) * b0)
    i1 = b0 + (b_inf - b0) * (1 - math.exp(h1))
    i2 = b0 + (b_inf - b0) * (1 - math.exp(h2))
    a1 = (i1 - K) * i1 ** (-beta)
    a2 = (i2 - K) * i2 ** (-beta)
    if S >= i2:
        return S - K
    return (a2 * S ** beta - a2 * _phi(S, t1, beta, i2, i2, r, b, v)
            + _phi(S, t1, 1, i2, i2, r, b, v) - _phi(S, t1, 1, i1, i2, r, b, v)
            - K * _phi(S, t1, 0, i2, i2, r, b, v) + K * _phi(S, t1, 0, i1, i2, r, b, v)
            + a1 * _phi(S, t1, beta, i1, i2, r, b, v) - a1 * _psi(S, T, beta, i1, i2, i1, t1, r, b, v)
            + _psi(S, T, 1, i1, i2, i1, t1, r, b, v) - _psi(S, T, 1, K, i2, i1, t1, r, b, v)
            - K * _psi(S, T, 0, i1, i2, i1, t1, r, b, v) + K * _psi(S, T, 0, K, i2, i1, t1, r, b, v))


def _bs1993_call(S, K, T, r, b, v):
    if b >= r:
        return _gbs_call(S, K, T, r, b, v)
    v2 = v * v
    beta = (0.5 - b / v2) + math.sqrt((b / v2 - 0.5) ** 2 + 2 * r / v2)
    b_inf = beta / (beta - 1) * K
    b0 = max(K, r / (r - b) * K)
    h = -(b * T + 2 * v * math.sqrt(T)) * b0 / (b_inf - b0)
    i = b0 + (b_inf - b0) * (1 - math.exp(h))
    a = (i - K) * i ** (-beta)
    if S >= i:
        return S - K
    return (a * S ** beta - a * _phi(S, T, beta, i, i, r, b, v) + _phi(S, T, 1, i, i, r, b, v)
            - _phi(S, T, 1, K, i, r, b, v) - K * _phi(S, T, 0, i, i, r, b, v) + K * _phi(S, T, 0, K, i, r, b, v))


def bjerksund_stensland_1993(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0,
                             kind: str = "C") -> float:
    """Single flat-boundary version. A looser lower bound than 2002: 1993 <= 2002 <= true American.
    Reproduces the textbook value 5.2704 for S=42, K=40, T=0.75, r=0.04, b=-0.04, sigma=0.35."""
    if min(S, K, T, sigma) <= 0:
        raise ValueError("S, K, T and sigma must be strictly positive")
    b = r - q
    if kind == "C":
        return _bs1993_call(S, K, T, r, b, sigma)
    if kind == "P":
        return _bs1993_call(K, S, T, r - b, -b, sigma)
    raise ValueError("kind must be 'C' or 'P'")


def bjerksund_stensland_2002(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0,
                             kind: str = "C") -> float:
    """American option approximation (Bjerksund & Stensland, 2002). Puts via the put-call transformation
    P(S, K, T, r, b) = C(K, S, T, r - b, -b)."""
    if min(S, K, T, sigma) <= 0:
        raise ValueError("S, K, T and sigma must be strictly positive")
    b = r - q
    if kind == "C":
        return _bs2002_call(S, K, T, r, b, sigma)
    if kind == "P":
        return _bs2002_call(K, S, T, r - b, -b, sigma)
    raise ValueError("kind must be 'C' or 'P'")


class RateCurve:
    """Zero-rate curve from (tenor_years, rate) points: linear inside, flat outside. No default data."""

    def __init__(self, points: Sequence[Tuple[float, float]]):
        pts = sorted((float(t), float(r)) for t, r in points)
        if not pts:
            raise ValueError("RateCurve needs at least one sourced (tenor, rate) point")
        if any(t <= 0 or not math.isfinite(r) for t, r in pts):
            raise ValueError("tenors must be positive and rates finite")
        self._t = np.array([p[0] for p in pts])
        self._r = np.array([p[1] for p in pts])

    def rate(self, t_years: float) -> float:
        return float(np.interp(t_years, self._t, self._r))
