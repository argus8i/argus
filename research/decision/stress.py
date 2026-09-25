"""
research/decision/stress.py
===========================
Joint stop-out stress for the 3-slot book (plan P6.7, A.17). One 15-minute bar; equicorrelated t-copula:

    X_i = sqrt(nu/W) * ( sqrt(rho)*Z + sqrt(1-rho)*eps_i ),   W ~ chi2(nu),  Z, eps_i ~ N(0,1)
    position i stops  <=>  X_i < q,   with q the t_nu quantile of p0 (so each marginal stops with p0)

The shared W creates tail dependence even at rho = 0, so the right check at rho = 0 is the normal-chi2
MIXTURE formula, not the binomial:
    P(k of n) = C(n,k) * E_W[ F^k (1-F)^(n-k) ],   F = Phi(q * sqrt(W/nu))
The Gaussian copula (nu = inf) at rho = 0 is exactly the binomial.

Inputs
- p0: per-bar stop hazard. Until the ledger has data: 1.25% per bar, DERIVED from A.4 (23.2% of trades stop
  within about 21 bars: 1 - (1 - 0.0125)^21 = 0.232).
- nu in {3, 5, 8}, rho in {0.30, 0.45, 0.60}, 3 positions, >= 10^6 draws, fixed seed.
- Loss severities: pass the empirical stop-loss and GAP_THROUGH_LIMIT loss distributions from the ledger
  (rupees per stopped position). Until they exist the defaults are ASSUMPTIONS: a normal stop loses the full
  Rs 1,500 risk budget (stop_limit basis; conservative), and 5% of stops are gap-throughs losing 2x that.
- Deterministic scenarios (Adjusted A1, Yashu 25 Sep 2026): the whole book at the aggregate exposure cap
  (3 x Rs 38,000 = Rs 1,14,000, absolute notional) moves -10%, -15% or -20% against every position, stops
  unfilled, exit at the moved price, BEFORE COSTS:
      -10%: Rs 11,400 (4.56% of the Rs 2,50,000 corpus) - within the Rs 12,000 "-10% scenario budget"
      -15%: Rs 17,100 (6.84%)   -20%: Rs 22,800 (9.12%)  - both exceed that budget.
  Band flexing does not guarantee an exit, so the move can be larger than the band that was in force.

Outputs: P(k of 3 stopped), tail-loss quantiles (95/99/99.9%) in rupees and as a share of the corpus, the
comparison with the Rs 4,500 risk-to-stop budget and the Rs 12,000 -10% scenario budget, and the scenario
table. These are model quantiles and scenario arithmetic, NOT a guaranteed maximum loss.
No scipy: the t quantile is solved from the mixture by bisection; the chi2 expectation uses a trapezoid rule
on a log grid.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from statistics import NormalDist
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from research.decision.sizing import AGGREGATE_EXPOSURE_CAP_RS, MAX_SLOTS, RISK_BUDGET_RS, SLOT_CAP_RS

P0_DEFAULT = 0.0125                     # DERIVED (A.4), see module docstring
NUS = (3, 5, 8)
RHOS = (0.30, 0.45, 0.60)
N_POS = MAX_SLOTS
DRAWS = 1_000_000
RISK_TO_STOP_BUDGET_RS = N_POS * RISK_BUDGET_RS          # Rs 4,500
SCENARIO_BUDGET_RS = 12_000.0           # the "-10% scenario budget" (Yashu, decision 5 / Adjusted A1)
CORPUS_RS = 250_000.0                   # DERIVED: 12,000 is 4.8% of the corpus (decision 5)
BAND = 0.10
SCENARIO_SHOCKS = (0.10, 0.15, 0.20)
VERDICT = ("Adjusted A1 satisfies the −10% scenario budget before costs. The −15% and −20% "
           "scenarios exceed that budget. ₹12,000 is not a guaranteed maximum loss, and band flexing does "
           "not guarantee an exit.")
GAP_SHARE, GAP_MULT = 0.05, 2.0         # ASSUMPTIONS until the ledger has GAP_THROUGH_LIMIT data
IST = timezone(timedelta(hours=5, minutes=30))
_N = NormalDist()


def _erfc(x: np.ndarray) -> np.ndarray:
    """erfc, vectorised, fractional error < 1.2e-7 everywhere (Chebyshev fit, Numerical Recipes 'erfcc')."""
    z = np.abs(x)
    t = 1.0 / (1.0 + 0.5 * z)
    ans = t * np.exp(-z * z - 1.26551223 + t * (1.00002368 + t * (0.37409196 + t * (0.09678418 + t * (
        -0.18628806 + t * (0.27886807 + t * (-1.13520398 + t * (1.48851587 + t * (-0.82215223 + t * 0.17087277)))))))))
    return np.where(x >= 0, ans, 2.0 - ans)


def _phi(x: np.ndarray) -> np.ndarray:
    """Standard normal CDF, vectorised."""
    return 0.5 * _erfc(-np.asarray(x, dtype=float) / math.sqrt(2.0))


def _chi2_grid(nu: float, n: int = 20_000):
    """Nodes w and weights a with sum(a * f(w)) ~= E[f(W)], W ~ chi2(nu). Trapezoid on x = e^u for the
    Gamma(k = nu/2, scale 2) density, u from -40 to ln(400 + 40 nu)."""
    k = nu / 2.0
    u = np.linspace(-40.0, math.log(400.0 + 40.0 * nu), n)
    x = np.exp(u)
    dens = np.exp(k * u - x - math.lgamma(k))                  # x^k e^-x / Gamma(k) (includes dx = x du)
    wts = dens * np.gradient(u)
    wts /= wts.sum()
    return 2.0 * x, wts


def t_threshold(p0: float, nu: float) -> float:
    """q with P(X < q) = p0 for X = Z * sqrt(nu/W) (Student t with nu d.o.f.); nu = inf gives Phi^-1(p0)."""
    if not 0 < p0 < 1:
        raise ValueError("p0 must be in (0, 1)")
    if math.isinf(nu):
        return _N.inv_cdf(p0)
    w, a = _chi2_grid(nu)
    lo, hi = -1e4, 1e4
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if float(np.sum(a * _phi(mid * np.sqrt(w / nu)))) < p0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def mixture_probs(p0: float, nu: float, n: int = N_POS) -> np.ndarray:
    """rho = 0 exact P(k of n) for the t-copula (the binomial when nu = inf)."""
    if math.isinf(nu):
        return np.array([math.comb(n, k) * p0 ** k * (1 - p0) ** (n - k) for k in range(n + 1)])
    q = t_threshold(p0, nu)
    w, a = _chi2_grid(nu)
    F = _phi(q * np.sqrt(w / nu))
    return np.array([math.comb(n, k) * float(np.sum(a * F ** k * (1 - F) ** (n - k))) for k in range(n + 1)])


def simulate_stops(p0: float, rho: float, nu: float, n_pos: int = N_POS, draws: int = DRAWS,
                   seed: int = 0) -> np.ndarray:
    """Boolean matrix (draws x n_pos): which positions stop in the bar."""
    rng = np.random.default_rng(seed)
    z = rng.standard_normal(draws)
    eps = rng.standard_normal((draws, n_pos))
    x = math.sqrt(rho) * z[:, None] + math.sqrt(1.0 - rho) * eps
    if not math.isinf(nu):
        x *= np.sqrt(nu / rng.chisquare(nu, draws))[:, None]
    return x < t_threshold(p0, nu)


def losses_rs(stopped: np.ndarray, severities_rs: Optional[Sequence[float]] = None, seed: int = 1) -> np.ndarray:
    """Rupee loss per draw: each stopped position draws a severity. Default severities are the module's
    ASSUMPTIONS (Rs 1,500; 5% gap-throughs at 2x)."""
    rng = np.random.default_rng(seed)
    if severities_rs is None:
        sev = np.where(rng.random(stopped.shape) < GAP_SHARE, GAP_MULT * RISK_BUDGET_RS, RISK_BUDGET_RS)
    else:
        pool = np.asarray(list(severities_rs), dtype=float)
        if pool.size == 0 or not np.all(np.isfinite(pool)):
            raise ValueError("severities must be a non-empty finite sample")
        sev = rng.choice(pool, size=stopped.shape)
    return (stopped * sev).sum(axis=1)


def gross_exposure_rs(n_pos: int = N_POS, notional: float = SLOT_CAP_RS,
                      aggregate_cap: float = AGGREGATE_EXPOSURE_CAP_RS) -> float:
    """Largest absolute notional the book can hold: min(n_pos x slot cap, aggregate cap)."""
    return min(n_pos * notional, aggregate_cap)


def band_hit_loss_rs(n_pos: int = N_POS, notional: float = SLOT_CAP_RS, band: float = BAND,
                     aggregate_cap: float = AGGREGATE_EXPOSURE_CAP_RS) -> float:
    """Before-cost loss if the whole book moves `band` against every position and exits at that price."""
    for v in (n_pos, notional, band, aggregate_cap):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)) or v < 0:
            raise ValueError(f"invalid scenario input {v!r}")
    return round(gross_exposure_rs(n_pos, notional, aggregate_cap) * band, 2)


def scenario_table(shocks: Sequence[float] = SCENARIO_SHOCKS, budget_rs: float = SCENARIO_BUDGET_RS,
                   corpus_rs: float = CORPUS_RS) -> List[Dict[str, Any]]:
    """Deterministic scenario rows (before costs) against the -10% scenario budget."""
    rows = []
    for s in shocks:
        loss = band_hit_loss_rs(band=s)
        rows.append({"shock_pct": round(s * 100, 2), "exposure_rs": gross_exposure_rs(), "loss_rs": loss,
                     "pct_corpus": round(loss / corpus_rs * 100, 2), "budget_rs": budget_rs,
                     "within_budget": loss <= budget_rs + 1e-9, "headroom_rs": round(budget_rs - loss, 2)})
    return rows


def run(p0: float = P0_DEFAULT, nus: Sequence[float] = NUS, rhos: Sequence[float] = RHOS, draws: int = DRAWS,
        seed: int = 20260925, severities_rs: Optional[Sequence[float]] = None) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for nu in nus:
        for rho in rhos:
            st = simulate_stops(p0, rho, nu, draws=draws, seed=seed)
            k = st.sum(axis=1)
            loss = losses_rs(st, severities_rs, seed=seed + 1)
            q = {f"q{p * 100:g}": float(np.quantile(loss, p)) for p in (0.95, 0.99, 0.999)}
            rows.append({"nu": nu, "rho": rho,
                         "p_k": [float(np.mean(k == j)) for j in range(N_POS + 1)],
                         "loss_quantiles_rs": q,
                         "loss_quantiles_pct_corpus": {kk: v / CORPUS_RS * 100 for kk, v in q.items()},
                         "p_loss_above_risk_budget": float(np.mean(loss > RISK_TO_STOP_BUDGET_RS)),
                         "p_loss_above_scenario_budget": float(np.mean(loss > SCENARIO_BUDGET_RS))})
    return {"model": "equicorrelated t-copula, one 15-minute bar (plan P6.7, A.17)", "p0": p0, "draws": draws,
            "seed": seed, "severities": "empirical" if severities_rs is not None else
            f"ASSUMPTION: Rs {RISK_BUDGET_RS:.0f} per stop, {GAP_SHARE:.0%} gap-throughs at {GAP_MULT:g}x",
            "limits": {"max_slots": MAX_SLOTS, "slot_cap_rs": SLOT_CAP_RS,
                       "aggregate_exposure_cap_rs": AGGREGATE_EXPOSURE_CAP_RS, "risk_budget_rs": RISK_BUDGET_RS},
            "risk_to_stop_budget_rs": RISK_TO_STOP_BUDGET_RS, "scenario_budget_rs": SCENARIO_BUDGET_RS,
            "corpus_rs": CORPUS_RS, "grid": rows,
            "scenarios_before_costs": scenario_table(),
            "verdict": VERDICT,
            "caveat": "Model quantiles and scenario arithmetic, not a guaranteed maximum loss."}


def main(argv: Optional[List[str]] = None) -> int:
    from research.data import paths

    ap = argparse.ArgumentParser(description="t-copula stop-out stress (plan P6.7)")
    ap.add_argument("--p0", type=float, default=P0_DEFAULT)
    ap.add_argument("--draws", type=int, default=DRAWS)
    ap.add_argument("--seed", type=int, default=20260925)
    args = ap.parse_args(argv)
    res = run(args.p0, draws=args.draws, seed=args.seed)
    out = paths.ensure(paths.outputs_dir() / "stress") / f"stress_{datetime.now(IST):%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
