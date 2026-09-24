"""Monte Carlo stress audit of the Track 2 six-strategy ensemble (Rule 4 / Rule 8 red-team).

What this measures
    Outcome distributions of the ensemble (COMPASS, TRAPDOOR, ORB, VWAP Reclaim,
    LAST LIGHT, RECOIL) under the audited friction schedule, conditional on stated
    priors. It is a stress test, not evidence of edge: the repo holds no out-of-sample
    trade record for any of these strategies, so every drift ("edge") is an input.

    Three questions it answers regardless of the true edge:
      1. What win rate does each exit model actually need after friction, with
         empirical 15m volatility and a 15:10 hard flat?  (the "44.0%" claim)
      2. How much edge does Rule 4 execution (4-state fills, queue-rank decay,
         distribution traps) remove relative to the "every signal fills" ideal?
      3. What tail risk (VaR99, ES99, drawdown, loss streaks) and what risk-parity
         weights does the ensemble carry, and can the 60-session paper gate tell
         a real edge from none?

Price model
    Each trade is a path in R units (1R = entry - structural stop), 3 sub-steps per
    15m bar, Student-t(5) innovations (fat tails), Brownian-bridge barrier crossing
    between sub-steps, stop checked before target when both cross in one sub-step
    (the pessimistic ordering of fills.resolve_bar_exit). 15m sigma, tail weight and
    market beta are calibrated from shared/track2_liquid/historical_candles_track2.json
    (8 F&O names, 2026-08-10 .. 2026-09-23); see EMPIRICAL below.

Correlation
    All six strategies are long-only. Each session draws one market factor z shared
    by every trade that day (plus a 3% shock-day state), so the three slots are not
    independent bets.

Fill layer (Rule 4)
    Passive entries (ORB and VWAP Reclaim post a BUY limit at the signal price) run a
    queue simulation: displayed quantity ahead of us, cancellation decay, sell flow at
    the level, and trade-through. Breakouts are latent GENUINE or DISTRIBUTION. A
    distribution trap sends sell flow into the bid (we fill); a genuine breakout lifts
    away (we queue). Terminal states use fills.FillState: LOCKED_NO_OFFER (a BUY
    with no offers; LOCKED_NO_BID is the SELL-side analogue and is modelled on the
    stop as STOP_SKIP_PROB), QUEUED, PARTIAL, FILLED. Taker entries (COMPASS,
    TRAPDOOR: "next executable ask") always fill unless locked, and pay the spread
    plus slippage that is larger on genuine breakouts.

Standard library only (package convention). Paper research code (Rule 1); nothing
here changes antigravity/ (Rule 8).

Run:
    python -m research.execution_realism.monte_carlo_ensemble_audit --paths 10000
"""
from __future__ import annotations

import argparse
import json
import math
import random
import time
from array import array
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .fills import FillState

# --------------------------------------------------------------- audited schedule
SLOT_NOTIONAL_RS = 58_333.0          # Rs 1,75,000 deployable / 3 slots
MIS_ROUNDTRIP_FRICTION = 0.00106     # 0.106% round trip -> Rs 61.83 per full slot
STOP_PCT_RANGE = (0.010, 0.015)      # structural stop band under audit
RISK_BUDGET_RS = 1_500.0             # nominal; non-binding below a 2.57% stop (see notes)
T1_R, T2_R = 1.5, 3.0                # two-tranche targets
CLAIMED_BREAKEVEN_WR = 0.440         # the figure under audit
MAX_SLOTS = 3
GATE_SESSIONS = 60                   # Rule 1 prospective sessions
GATE_MIN_ENTRIES = 20                # Rule 1 fillable entries

# ------------------------------------------------ empirical (repo candle corpus)
# Measured from historical_candles_track2.json: intraday 15m close-to-close log
# returns, 8 symbols x 32 sessions (736 returns each). Median sigma 0.266%
# (range 0.208%-0.363%), pooled excess kurtosis 6.55 (t with nu ~ 4.9), corr to
# NIFTY 15m 0.26-0.49, beta 1.17-1.55, NIFTY daily sigma ~0.45%.
EMPIRICAL = {
    "bar_sigma_pct": 0.00266,
    "t_dof": 5.0,
    "market_beta": 1.4,
    "nifty_daily_sigma_pct": 0.0045,
    "source": "shared/track2_liquid/historical_candles_track2.json (2026-08-10..2026-09-23)",
}
BARS_PER_SESSION = 25

# --------------------------------------------------------------- priors (UNCALIBRATED)
SUBSTEPS = 6                         # 3 left a +0.01R stop-gap bias with t(5) jumps
REGIME_DRIFT_PCT_BAR = EMPIRICAL["market_beta"] * EMPIRICAL["nifty_daily_sigma_pct"] / BARS_PER_SESSION
SHOCK_PROB = 0.03                    # NIFTY <= -1% sessions: 1 of 31 in the corpus
SHOCK_Z = -2.7                       # a shock session is a -2.7 sigma market day...
SHOCK_SIGMA_MULT = 1.5               # ...with 1.5x intraday volatility
QUINTILE_Z = (-1.3998, -0.5319, 0.0, 0.5319, 1.3998)   # E[Z | quintile] of N(0,1)
GENUINE_SHARE = 0.5                  # latent share of genuine breakouts
TYPE_DRIFT_PCT_BAR = 0.0005          # genuine +0.05%/bar, distribution -0.05%/bar about base
HALF_SPREAD_PCT = 0.00015            # ~1 tick on a Rs 330 stock
STOP_SLIP_MEAN_PCT = 0.0002          # SL-limit fill below trigger, exponential mean
STOP_SKIP_PROB = 0.01                # stop limit skipped (LOCKED_NO_BID / fast tape)
TAKER_SLIP_MEAN_PCT = {"GENUINE": 0.0003, "DISTRIBUTION": 0.0001}
LOCK_PROB = 0.003                    # dynamic-band freeze with zero offers (F&O: rare)

# passive queue model (per 15m breakout, quantities in units of our order Q)
QUEUE = {
    "horizon_s": 900.0, "dt_s": 10.0,
    "ahead_median_q": 6.0, "ahead_log_sigma": 0.9,
    "cancel_rate_per_s": {"GENUINE": 1 / 150.0, "DISTRIBUTION": 1 / 600.0},
    "sell_flow_q_per_s": {"GENUINE": 0.03, "DISTRIBUTION": 0.02},
    "sell_flow_decay_s": {"GENUINE": 90.0, "DISTRIBUTION": math.inf},
    "print_size_q": 0.25,
    "trade_through_prob": {"GENUINE": 0.08, "DISTRIBUTION": 0.40},
    "trade_through_depth_pct": {"GENUINE": 0.0003, "DISTRIBUTION": 0.0010},
}

TYPES = ("GENUINE", "DISTRIBUTION")
N_BUCKETS = len(QUINTILE_Z) + 1      # 5 quintiles + shock
SHOCK_BUCKET = N_BUCKETS - 1


@dataclass(frozen=True)
class StrategySpec:
    name: str
    implemented: bool
    source: str
    exit_model: str          # TWO_TRANCHE | SINGLE
    target_r: float          # SINGLE target (TWO_TRANCHE uses T1_R/T2_R)
    horizon_bars: int        # time exit / 15:10 hard flat
    signal_rate: float       # P(at least one qualified signal per session) - prior
    entry_mode: str          # PASSIVE | TAKER


STRATEGIES: Tuple[StrategySpec, ...] = (
    StrategySpec("COMPASS", True, "antigravity/models/track2_compass_strategy.py",
                 "SINGLE", 2.0, 6, 0.30, "TAKER"),
    StrategySpec("TRAPDOOR", True, "antigravity/models/track2_trapdoor_strategy.py",
                 "SINGLE", 1.8, 4, 0.20, "TAKER"),
    StrategySpec("ORB", True, "antigravity/models/track2_alpha_engine.py",
                 "TWO_TRANCHE", 0.0, 22, 0.45, "PASSIVE"),
    StrategySpec("VWAP_RECLAIM", True, "antigravity/models/track2_vwap_reclaim_strategy.py",
                 "TWO_TRANCHE", 0.0, 14, 0.35, "PASSIVE"),
    StrategySpec("LAST_LIGHT", False, "NOT IN REPOSITORY - placeholder parameters",
                 "SINGLE", 1.5, 5, 0.25, "TAKER"),
    StrategySpec("RECOIL", False, "NOT IN REPOSITORY - placeholder parameters",
                 "SINGLE", 2.0, 8, 0.25, "TAKER"),
)

OUTCOMES = ("STOP", "STOP_SKIPPED", "TARGET", "TIME", "T1_THEN_BE", "T1_THEN_T2", "T1_THEN_TIME")
_OC = {k: i for i, k in enumerate(OUTCOMES)}


# ------------------------------------------------------------------ analytics
def friction_r(stop_pct: float) -> float:
    """Round-trip MIS friction in R. The slot cap binds below a 2.57% stop, so
    notional = SLOT_NOTIONAL_RS and 1R = SLOT_NOTIONAL_RS * stop_pct."""
    return MIS_ROUNDTRIP_FRICTION / stop_pct


def risk_rs(stop_pct: float) -> float:
    return min(RISK_BUDGET_RS, SLOT_NOTIONAL_RS * stop_pct)


def two_tranche_breakeven(q_runner: float, f_r: float) -> float:
    """Win rate (P(T1)) at zero net expectancy for T1 at 1.5R, runner to 3.0R or a
    breakeven stop, no time exit: p (0.75 + 1.5 q) - (1 - p) - f = 0."""
    return (1.0 + f_r) / (1.0 + 0.5 * T1_R + 0.5 * T2_R * q_runner)


def runner_share_for_breakeven(p_be: float, f_r: float) -> float:
    """Inverse of two_tranche_breakeven: the q that makes p_be the breakeven."""
    return ((1.0 + f_r) / p_be - 1.0 - 0.5 * T1_R) / (0.5 * T2_R)


def single_target_breakeven(target_r: float, f_r: float) -> float:
    return (1.0 + f_r) / (1.0 + target_r)


# ------------------------------------------------------------ path simulation
class _TDraw:
    """Unit-variance Student-t innovations."""

    def __init__(self, rng: random.Random, dof: float):
        self.rng, self.dof = rng, dof
        self.scale = math.sqrt((dof - 2.0) / dof)
        self.half = dof / 2.0

    def __call__(self) -> float:
        g = self.rng.gauss(0.0, 1.0)
        chi = self.rng.gammavariate(self.half, 2.0)
        return g / math.sqrt(chi / self.dof) * self.scale


def _cross_prob(a: float, b: float, var: float) -> float:
    """P(a Brownian bridge from a to b, both > 0 from the barrier, touches it)."""
    e = 2.0 * a * b / var
    return 0.0 if e > 30.0 else math.exp(-e)


def simulate_trade(rng: random.Random, tdraw: _TDraw, spec: StrategySpec, mu_pct_bar: float,
                   sigma_pct_bar: float, stop_pct: float) -> Tuple[float, int]:
    """One trade in R units. Returns (gross_R, outcome index)."""
    s_bar = sigma_pct_bar / stop_pct
    dt = 1.0 / SUBSTEPS
    m = mu_pct_bar / stop_pct * dt
    sd = s_bar * math.sqrt(dt)
    var = sd * sd
    slip_mean = STOP_SLIP_MEAN_PCT / stop_pct
    exit_cost = HALF_SPREAD_PCT / stop_pct
    rnd = rng.random

    two = spec.exit_model == "TWO_TRANCHE"
    stop, tgt = -1.0, (T1_R if two else spec.target_r)
    frac, realized, runner = 1.0, 0.0, False
    x = 0.0
    for _ in range(spec.horizon_bars * SUBSTEPS):
        xn = x + m + sd * tdraw()
        if xn <= stop or rnd() < _cross_prob(x - stop, xn - stop, var):
            if rnd() < STOP_SKIP_PROB:          # limit skipped: out at the next print
                fill = min(xn, stop) - abs(sd * tdraw())
                return realized + frac * fill, _OC["STOP_SKIPPED"]
            fill = stop - rng.expovariate(1.0 / slip_mean)
            realized += frac * fill
            return realized, (_OC["T1_THEN_BE"] if runner else _OC["STOP"])
        while xn >= tgt or rnd() < _cross_prob(tgt - x, tgt - xn, var):
            if two and not runner:              # tranche 1 banks, runner stop to entry
                realized += 0.5 * T1_R
                frac, runner, stop, tgt = 0.5, True, 0.0, T2_R
                continue
            realized += frac * tgt
            return realized, (_OC["T1_THEN_T2"] if runner else _OC["TARGET"])
        x = xn
    realized += frac * (x - exit_cost)          # 15:10 hard flat / time exit, market out
    return realized, (_OC["T1_THEN_TIME"] if runner else _OC["TIME"])


def _bucket_params(bucket: int) -> Tuple[float, float]:
    """(market drift %/bar, sigma multiplier) for a regime bucket."""
    if bucket == SHOCK_BUCKET:
        return SHOCK_Z * REGIME_DRIFT_PCT_BAR, SHOCK_SIGMA_MULT
    return QUINTILE_Z[bucket] * REGIME_DRIFT_PCT_BAR, 1.0


def _type_drift(kind: str) -> float:
    if kind == "GENUINE":
        return TYPE_DRIFT_PCT_BAR
    return -TYPE_DRIFT_PCT_BAR * GENUINE_SHARE / (1.0 - GENUINE_SHARE)


def _draw_bucket(rng: random.Random) -> int:
    return SHOCK_BUCKET if rng.random() < SHOCK_PROB else rng.randrange(len(QUINTILE_Z))


def trade_stats(spec: StrategySpec, base_mu: float, n: int, seed: int,
                dispersion: bool = True) -> Dict[str, float]:
    """Ideal-execution stats over the regime and breakout-type mixture.
    dispersion=False removes the regime and type drifts (friction-only diagnostic)."""
    rng = random.Random(seed)
    td = _TDraw(rng, EMPIRICAL["t_dof"])
    tot_net = tot_gross = wins = t1 = sq = 0.0
    oc = [0] * len(OUTCOMES)
    for _ in range(n):
        b = _draw_bucket(rng)
        kind = "GENUINE" if rng.random() < GENUINE_SHARE else "DISTRIBUTION"
        drift, smult = _bucket_params(b)
        stop_pct = rng.uniform(*STOP_PCT_RANGE)
        mu = base_mu + drift + _type_drift(kind) if dispersion else base_mu
        g, o = simulate_trade(rng, td, spec, mu, EMPIRICAL["bar_sigma_pct"] * (smult if dispersion else 1.0),
                              stop_pct)
        net = g - friction_r(stop_pct)
        tot_net += net
        sq += net * net
        tot_gross += g
        wins += net > 0
        oc[o] += 1
        t1 += OUTCOMES[o].startswith("T1") or OUTCOMES[o] == "TARGET"
    out = {"mean_net_r": tot_net / n, "mean_gross_r": tot_gross / n,
           "sd_net_r": math.sqrt(max(0.0, sq / n - (tot_net / n) ** 2)),
           "win_rate_net": wins / n, "target1_hit_rate": t1 / n, "n": n}
    out.update({f"p_{k}": oc[i] / n for i, k in enumerate(OUTCOMES)})
    runners = oc[_OC["T1_THEN_BE"]] + oc[_OC["T1_THEN_T2"]] + oc[_OC["T1_THEN_TIME"]]
    out["runner_q"] = oc[_OC["T1_THEN_T2"]] / runners if runners else float("nan")
    out["time_exit_share"] = (oc[_OC["TIME"]] + oc[_OC["T1_THEN_TIME"]]) / n
    return out


def calibrate_base_mu(spec: StrategySpec, target_net_r: float, n: int, seed: int) -> float:
    """Base drift (%/bar) at which ideal-execution expectancy equals target_net_r.
    Secant on common random numbers; expectancy is near-linear in drift."""
    x0, x1 = 0.0, 0.0005
    f0 = trade_stats(spec, x0, n, seed)["mean_net_r"] - target_net_r
    f1 = trade_stats(spec, x1, n, seed)["mean_net_r"] - target_net_r
    for _ in range(8):
        if abs(f1) < 2e-3 or f1 == f0:
            break
        x0, x1, f0 = x1, x1 - f1 * (x1 - x0) / (f1 - f0), f1
        f1 = trade_stats(spec, x1, n, seed)["mean_net_r"] - target_net_r
    return x1


def build_trade_tables(specs: Sequence[StrategySpec], base_mu: Dict[str, float], n: int,
                       seed: int) -> Dict[str, List[List[List[Tuple[float, float]]]]]:
    """tables[strategy][bucket][type] -> [(net_R, stop_pct)] under ideal execution."""
    rng = random.Random(seed)
    td = _TDraw(rng, EMPIRICAL["t_dof"])
    tables = {}
    for spec in specs:
        per_b = []
        for b in range(N_BUCKETS):
            drift, smult = _bucket_params(b)
            per_t = []
            for kind in TYPES:
                rows = []
                mu = base_mu[spec.name] + drift + _type_drift(kind)
                for _ in range(n):
                    stop_pct = rng.uniform(*STOP_PCT_RANGE)
                    g, _o = simulate_trade(rng, td, spec, mu, EMPIRICAL["bar_sigma_pct"] * smult, stop_pct)
                    rows.append((g - friction_r(stop_pct), stop_pct))
                per_t.append(rows)
            per_b.append(per_t)
        tables[spec.name] = per_b
    return tables


# ---------------------------------------------------------- 4-state fill layer
@dataclass(frozen=True)
class FillOutcome:
    state: str             # FillState value
    fill_frac: float       # share of the order filled
    entry_cost_pct: float  # adverse cost vs signal price (spread, slippage, trade-through)
    t_fill_s: float        # time to full fill (inf if not filled)


def simulate_passive_entry(rng: random.Random, kind: str, ahead_q: Optional[float] = None,
                           params: Optional[dict] = None) -> FillOutcome:
    """BUY limit at the breakout level, joined at the back of the displayed queue.

    Queue-ahead decays by cancellation (exp(-kappa dt)); sell prints at the level
    (compound Poisson, gamma-approximated) consume the front of the queue first;
    V_cum >= R + Q fills us (Rule 4). A trade-through at a uniform time fills us at
    once (fills.Evidence.TRADE_THROUGH) and marks us below the level."""
    if rng.random() < LOCK_PROB:
        return FillOutcome(FillState.LOCKED_NO_OFFER.value, 0.0, 0.0, math.inf)
    q = QUEUE if params is None else params
    ahead = ahead_q if ahead_q is not None else q["ahead_median_q"] * math.exp(q["ahead_log_sigma"] * rng.gauss(0, 1))
    kappa = q["cancel_rate_per_s"][kind]
    flow0, tau = q["sell_flow_q_per_s"][kind], q["sell_flow_decay_s"][kind]
    t_through = rng.uniform(0, q["horizon_s"]) if rng.random() < q["trade_through_prob"][kind] else math.inf
    dt, filled, t = q["dt_s"], 0.0, 0.0
    decay = math.exp(-kappa * dt)
    while t < q["horizon_s"]:
        t += dt
        if t >= t_through:
            return FillOutcome(FillState.FILLED.value, 1.0, q["trade_through_depth_pct"][kind], t)
        ahead *= decay
        flow = flow0 * (math.exp(-t / tau) if math.isfinite(tau) else 1.0)
        shape = flow * dt / q["print_size_q"]
        vol = rng.gammavariate(shape, q["print_size_q"]) if shape > 1e-9 else 0.0
        take = min(ahead, vol)
        ahead -= take
        filled = min(1.0, filled + vol - take)
        if filled >= 1.0:
            return FillOutcome(FillState.FILLED.value, 1.0, 0.0, t)
    if filled > 0.0:
        return FillOutcome(FillState.PARTIAL.value, filled, 0.0, math.inf)
    return FillOutcome(FillState.QUEUED.value, 0.0, 0.0, math.inf)


def simulate_taker_entry(rng: random.Random, kind: str) -> FillOutcome:
    if rng.random() < LOCK_PROB:
        return FillOutcome(FillState.LOCKED_NO_OFFER.value, 0.0, 0.0, math.inf)
    cost = HALF_SPREAD_PCT + rng.expovariate(1.0 / TAKER_SLIP_MEAN_PCT[kind])
    return FillOutcome(FillState.FILLED.value, 1.0, cost, 0.0)


def build_fill_tables(n: int, seed: int) -> Dict[str, Dict[str, List[FillOutcome]]]:
    rng = random.Random(seed)
    return {
        "PASSIVE": {k: [simulate_passive_entry(rng, k) for _ in range(n)] for k in TYPES},
        "TAKER": {k: [simulate_taker_entry(rng, k) for _ in range(n)] for k in TYPES},
    }


def fill_state_summary(tables: Dict[str, Dict[str, List[FillOutcome]]]) -> Dict[str, dict]:
    out = {}
    for mode, per in tables.items():
        res = {}
        for kind, rows in per.items():
            cnt: Dict[str, int] = {}
            for r in rows:
                cnt[r.state] = cnt.get(r.state, 0) + 1
            res[kind] = {
                "state_share": {k: v / len(rows) for k, v in sorted(cnt.items())},
                "mean_fill_frac": sum(r.fill_frac for r in rows) / len(rows),
                "mean_entry_cost_pct": sum(r.entry_cost_pct * r.fill_frac for r in rows)
                / max(1e-12, sum(r.fill_frac for r in rows)),
            }
        g, d = (sum(r.fill_frac for r in per[k]) / len(per[k]) for k in TYPES)
        # Bayes: share of filled quantity that came from genuine breakouts
        res["genuine_share_of_filled_qty"] = GENUINE_SHARE * g / (GENUINE_SHARE * g + (1 - GENUINE_SHARE) * d)
        res["adverse_selection_ratio"] = d / g if g > 0 else math.inf
        out[mode] = res
    return out


def queue_decay_curve(ranks: Sequence[float], checkpoints_s: Sequence[float], n: int,
                      seed: int) -> Dict[str, Dict[str, List[float]]]:
    """P(FILLED by t | initial queue rank ahead, breakout type)."""
    rng = random.Random(seed)
    out: Dict[str, Dict[str, List[float]]] = {}
    for kind in TYPES:
        per = {}
        for r in ranks:
            times = [simulate_passive_entry(rng, kind, ahead_q=r).t_fill_s for _ in range(n)]
            per[f"rank_{r:g}Q"] = [sum(1 for x in times if x <= c) / n for c in checkpoints_s]
        out[kind] = per
    return out


def queue_prior_sensitivity(n: int, seed: int) -> List[dict]:
    """Genuine share of passively filled quantity across the queue priors that are
    least constrained by data: displayed depth ahead and distribution sell flow."""
    rng = random.Random(seed)
    out = []
    for ahead in (1.0, 3.0, 6.0, 12.0):
        for dist_flow in (0.005, 0.01, 0.02):
            for gen_flow in (0.03, 0.10):
                q = dict(QUEUE)
                q["ahead_median_q"] = ahead
                q["sell_flow_q_per_s"] = {"GENUINE": gen_flow, "DISTRIBUTION": dist_flow}
                ff = {k: sum(simulate_passive_entry(rng, k, params=q).fill_frac for _ in range(n)) / n for k in TYPES}
                g, d = ff["GENUINE"], ff["DISTRIBUTION"]
                out.append({"ahead_median_q": ahead, "dist_flow_q_per_s": dist_flow, "gen_flow_q_per_s": gen_flow,
                            "fill_frac_genuine": g, "fill_frac_distribution": d,
                            "genuine_share_of_filled_qty": GENUINE_SHARE * g / (GENUINE_SHARE * g + (1 - GENUINE_SHARE) * d)})
    return out


# ------------------------------------------------------------ ensemble engine
@dataclass
class EnsembleResult:
    label: str
    n_paths: int
    n_sessions: int
    execution: str
    strategies: List[str]
    trades: int
    trades_per_session: float
    mean_trade_net_r: float
    mean_trade_pnl_rs: float
    sd_trade_net_r: float
    sd_trade_pnl_rs: float
    win_rate_net: float
    per_strategy: Dict[str, dict]
    fill_states: Dict[str, int]
    daily_var99_rs: float
    daily_es99_rs: float
    trade_var99_rs: float
    horizon_pnl_rs: Dict[str, float]
    p_horizon_loss: float
    max_drawdown_rs: Dict[str, float]
    max_drawdown_sessions: Dict[str, float]
    max_losing_streak: Dict[str, float]
    gate60: Dict[str, float]
    daily_cov: List[List[float]]
    slot_multipliers: Dict[str, float]
    runtime_s: float


def _q(xs: List[float], p: float) -> float:
    if not xs:
        return float("nan")
    k = min(len(xs) - 1, max(0, int(round(p * (len(xs) - 1)))))
    return xs[k]


def run_ensemble(label: str, specs: Sequence[StrategySpec], trade_tables, fill_tables, execution: str,
                 n_paths: int, n_sessions: int, seed: int,
                 slot_multipliers: Optional[Dict[str, float]] = None) -> EnsembleResult:
    """execution: IDEAL (every signal fills at the signal price), SPEC (each strategy's
    own entry mode through the 4-state layer) or ALL_TAKER."""
    t0 = time.time()
    rng = random.Random(seed)
    rnd, rr = rng.random, rng.randrange
    names = [s.name for s in specs]
    k = len(specs)
    mult = [1.0 if slot_multipliers is None else slot_multipliers[n] for n in names]
    mode_of = [("TAKER" if execution == "ALL_TAKER" else s.entry_mode) for s in specs]
    tabs = [trade_tables[n] for n in names]

    daily = array("d")
    trade_pnls = array("d")
    s1 = [0.0] * k
    s2 = [[0.0] * k for _ in range(k)]
    st_n = [0] * k
    st_r = [0.0] * k
    st_w = [0] * k
    st_att = [0] * k
    fill_states: Dict[str, int] = {}
    horizon, dd, dd_len, streaks = [], [], [], []
    gate_pass = gate_entries_ok = gate_sig = 0
    gate_pnl = []
    total_trades = wins = 0
    total_r = total_rs = total_r2 = total_rs2 = 0.0

    for _ in range(n_paths):
        cum = peak = 0.0
        mdd, cur_len, mdd_len = 0.0, 0, 0
        streak = best_streak = 0
        g_pnl, g_entries, g_ss = 0.0, 0, 0.0
        for sess in range(n_sessions):
            b = _draw_bucket(rng)
            cands = [i for i in range(k) if rnd() < specs[i].signal_rate]
            rng.shuffle(cands)                       # conviction ranking: no ordering edge assumed
            day = 0.0
            per = [0.0] * k
            used = 0
            for i in cands:
                if used >= MAX_SLOTS:
                    break
                ti = 0 if rnd() < GENUINE_SHARE else 1
                st_att[i] += 1
                if execution == "IDEAL":
                    frac, cost = 1.0, 0.0
                    fill_states["FILLED"] = fill_states.get("FILLED", 0) + 1
                else:
                    rows = fill_tables[mode_of[i]][TYPES[ti]]
                    fo = rows[rr(len(rows))]
                    fill_states[fo.state] = fill_states.get(fo.state, 0) + 1
                    if fo.fill_frac <= 0.0:
                        continue                     # QUEUED / LOCKED: slot stays free
                    frac, cost = fo.fill_frac, fo.entry_cost_pct
                used += 1
                row = tabs[i][b][ti]
                net_r, stop_pct = row[rr(len(row))]
                net_r -= cost / stop_pct
                pnl = net_r * frac * mult[i] * risk_rs(stop_pct)
                day += pnl
                per[i] += pnl
                trade_pnls.append(pnl)
                st_n[i] += 1
                st_r[i] += net_r * frac * mult[i]
                st_w[i] += pnl > 0
                total_trades += 1
                wins += pnl > 0
                total_r += net_r * frac * mult[i]
                total_r2 += (net_r * frac * mult[i]) ** 2
                total_rs += pnl
                total_rs2 += pnl * pnl
                if pnl < 0:
                    streak += 1
                    best_streak = max(best_streak, streak)
                else:
                    streak = 0
                if sess < GATE_SESSIONS:
                    g_entries += 1
                    g_ss += pnl * pnl
            daily.append(day)
            for a in range(k):
                pa = per[a]
                if pa:
                    s1[a] += pa
                    row2 = s2[a]
                    for c in range(k):
                        if per[c]:
                            row2[c] += pa * per[c]
            cum += day
            if sess < GATE_SESSIONS:
                g_pnl += day
            if cum >= peak:
                peak, cur_len = cum, 0
            else:
                cur_len += 1
                mdd = max(mdd, peak - cum)
                mdd_len = max(mdd_len, cur_len)
        horizon.append(cum)
        dd.append(mdd)
        dd_len.append(mdd_len)
        streaks.append(best_streak)
        gate_pnl.append(g_pnl)
        enough = g_entries >= GATE_MIN_ENTRIES
        gate_entries_ok += enough
        gate_pass += enough and g_pnl > 0
        if enough:                                   # one-sided t-test of mean trade P&L > 0 at 5%
            mu_t = g_pnl / g_entries
            var_t = max(1e-12, (g_ss - g_entries * mu_t * mu_t) / (g_entries - 1))
            gate_sig += mu_t / math.sqrt(var_t / g_entries) > 1.645

    n_days = n_paths * n_sessions
    mean = [x / n_days for x in s1]
    cov = [[s2[a][c] / n_days - mean[a] * mean[c] for c in range(k)] for a in range(k)]
    d_sorted = sorted(daily)
    t_sorted = sorted(trade_pnls)
    var_idx = int(0.01 * len(d_sorted))
    for xs in (horizon, dd, dd_len, streaks, gate_pnl):
        xs.sort()

    def dist(xs):
        return {"mean": sum(xs) / len(xs), "p01": _q(xs, 0.01), "p05": _q(xs, 0.05), "median": _q(xs, 0.5),
                "p95": _q(xs, 0.95), "p99": _q(xs, 0.99)}

    per_strategy = {}
    for i, nme in enumerate(names):
        per_strategy[nme] = {
            "attempts": st_att[i], "trades": st_n[i],
            "fill_rate": st_n[i] / st_att[i] if st_att[i] else float("nan"),
            "mean_net_r": st_r[i] / st_n[i] if st_n[i] else float("nan"),
            "win_rate_net": st_w[i] / st_n[i] if st_n[i] else float("nan"),
            "daily_pnl_sd_rs": math.sqrt(max(0.0, cov[i][i])),
        }
    return EnsembleResult(
        label=label, n_paths=n_paths, n_sessions=n_sessions, execution=execution, strategies=names,
        trades=total_trades, trades_per_session=total_trades / n_days,
        mean_trade_net_r=total_r / max(1, total_trades), mean_trade_pnl_rs=total_rs / max(1, total_trades),
        sd_trade_net_r=math.sqrt(max(0.0, total_r2 / max(1, total_trades) - (total_r / max(1, total_trades)) ** 2)),
        sd_trade_pnl_rs=math.sqrt(max(0.0, total_rs2 / max(1, total_trades) - (total_rs / max(1, total_trades)) ** 2)),
        win_rate_net=wins / max(1, total_trades), per_strategy=per_strategy, fill_states=fill_states,
        daily_var99_rs=-d_sorted[var_idx], daily_es99_rs=-sum(d_sorted[:var_idx + 1]) / (var_idx + 1),
        trade_var99_rs=-t_sorted[int(0.01 * len(t_sorted))] if t_sorted else float("nan"),
        horizon_pnl_rs=dist(horizon), p_horizon_loss=sum(1 for x in horizon if x <= 0) / n_paths,
        max_drawdown_rs=dist(dd), max_drawdown_sessions=dist(dd_len), max_losing_streak=dist(streaks),
        gate60={"p_min_entries": gate_entries_ok / n_paths, "p_pass": gate_pass / n_paths,
                "p_pass_t_test_5pct": gate_sig / n_paths,
                "pnl_median_rs": _q(gate_pnl, 0.5), "pnl_p05_rs": _q(gate_pnl, 0.05)},
        daily_cov=cov, slot_multipliers=dict(zip(names, mult)), runtime_s=time.time() - t0,
    )


# -------------------------------------------------------------- risk parity
def risk_parity_weights(cov: Sequence[Sequence[float]], iters: int = 500, tol: float = 1e-12) -> List[float]:
    """Equal-risk-contribution weights by cyclical coordinate descent
    (Griveau-Billion, Richard & Roncalli 2013): each w_i solves
    S_ii w_i^2 + w_i sum_{j!=i} S_ij w_j - 1/n = 0, then normalise."""
    n = len(cov)
    w = [1.0 / math.sqrt(cov[i][i]) if cov[i][i] > 0 else 0.0 for i in range(n)]
    s = sum(w)
    w = [x / s for x in w]
    b = 1.0 / n
    for _ in range(iters):
        prev = list(w)
        for i in range(n):
            if cov[i][i] <= 0:
                continue
            c = sum(cov[i][j] * w[j] for j in range(n) if j != i)
            w[i] = (-c + math.sqrt(c * c + 4.0 * cov[i][i] * b)) / (2.0 * cov[i][i])
        if max(abs(a - p) for a, p in zip(w, prev)) < tol:
            break
    s = sum(w)
    return [x / s for x in w]


def risk_contributions(cov: Sequence[Sequence[float]], w: Sequence[float]) -> List[float]:
    sw = [sum(cov[i][j] * w[j] for j in range(len(w))) for i in range(len(w))]
    tot = sum(w[i] * sw[i] for i in range(len(w)))
    return [w[i] * sw[i] / tot for i in range(len(w))]


def correlation(cov: Sequence[Sequence[float]]) -> List[List[float]]:
    n = len(cov)
    return [[cov[i][j] / math.sqrt(cov[i][i] * cov[j][j]) if cov[i][i] > 0 and cov[j][j] > 0 else 0.0
             for j in range(n)] for i in range(n)]


# ---------------------------------------------------------------------- main
def run_audit(n_paths: int = 10_000, n_sessions: int = 250, seed: int = 20260924,
              calib_n: int = 12_000, table_n: int = 2_500, fill_n: int = 20_000,
              verbose: bool = True) -> dict:
    t_start = time.time()
    log = print if verbose else (lambda *a, **k: None)
    specs = list(STRATEGIES)
    implemented = [s for s in specs if s.implemented]

    # 1. Analytic hurdles
    analytic = {}
    for sp in (STOP_PCT_RANGE[0], 0.0125, STOP_PCT_RANGE[1]):
        f = friction_r(sp)
        analytic[f"stop_{sp*100:.2f}pct"] = {
            "friction_r": f, "friction_rs_full_slot": SLOT_NOTIONAL_RS * MIS_ROUNDTRIP_FRICTION,
            "risk_rs": risk_rs(sp),
            "two_tranche_be_q0": two_tranche_breakeven(0.0, f),
            "two_tranche_be_q050": two_tranche_breakeven(0.5, f),
            "two_tranche_be_q1": two_tranche_breakeven(1.0, f),
            "runner_q_needed_for_44pct": runner_share_for_breakeven(CLAIMED_BREAKEVEN_WR, f),
            "single_1p5r_be": single_target_breakeven(1.5, f),
            "single_1p8r_be": single_target_breakeven(1.8, f),
            "single_2p0r_be": single_target_breakeven(2.0, f),
        }
    log("[1/6] analytic hurdles done")

    # 2. Scenarios are defined by ideal-execution expectancy, not by drift: with
    #    genuine/distribution dispersion the stop/target/time-exit payoff is convex,
    #    so zero base drift is not zero edge. BREAKEVEN: E_ideal[net R] = 0 (no edge
    #    after friction). EDGE: E_ideal[net R] = +0.10R.
    hurdle, be_mu, edge_mu, cost_only, edge_stats = {}, {}, {}, {}, {}
    for i, spec in enumerate(specs):
        be_mu[spec.name] = calibrate_base_mu(spec, 0.0, calib_n, seed + 11 * i)
        hurdle[spec.name] = {"base_mu_pct_bar": be_mu[spec.name] * 100,
                             **trade_stats(spec, be_mu[spec.name], calib_n, seed + 11 * i + 1)}
        cost_only[spec.name] = trade_stats(spec, 0.0, calib_n, seed + 11 * i + 2, dispersion=False)
        edge_mu[spec.name] = calibrate_base_mu(spec, 0.10, calib_n, seed + 11 * i + 3)
        edge_stats[spec.name] = {"base_mu_pct_bar": edge_mu[spec.name] * 100,
                                 **trade_stats(spec, edge_mu[spec.name], calib_n, seed + 11 * i + 4)}
    log(f"[2/6] hurdle calibration done ({time.time()-t_start:.0f}s)")

    # 3. Fill layer
    fills = build_fill_tables(fill_n, seed + 101)
    fill_summary = fill_state_summary(fills)
    decay = queue_decay_curve([1, 3, 6, 12, 24], [60, 180, 300, 600, 900], 4000, seed + 102)
    sensitivity = queue_prior_sensitivity(3000, seed + 103)
    log(f"[3/6] fill layer done ({time.time()-t_start:.0f}s)")

    # 4. Trade tables
    be_tab = build_trade_tables(specs, be_mu, table_n, seed + 201)
    edge_tab = build_trade_tables(specs, edge_mu, table_n, seed + 202)
    log(f"[4/6] trade tables done ({time.time()-t_start:.0f}s)")

    # 5. Ensembles
    runs: Dict[str, EnsembleResult] = {}
    plan = [
        ("BREAKEVEN_IDEAL", specs, be_tab, "IDEAL", None),
        ("BREAKEVEN_SPEC", specs, be_tab, "SPEC", None),
        ("EDGE_IDEAL", specs, edge_tab, "IDEAL", None),
        ("EDGE_SPEC", specs, edge_tab, "SPEC", None),
        ("EDGE_ALL_TAKER", specs, edge_tab, "ALL_TAKER", None),
        ("EDGE_SPEC_IMPLEMENTED_ONLY", implemented, edge_tab, "SPEC", None),
    ]
    for j, (lbl, sp, tab, ex, m) in enumerate(plan):
        runs[lbl] = run_ensemble(lbl, sp, tab, fills, ex, n_paths, n_sessions, seed + 301 + j, m)
        log(f"      {lbl}: {runs[lbl].runtime_s:.0f}s")

    # 6. Risk parity. Computed on the ALL_TAKER ensemble: under SPEC execution the
    #    passive strategies carry negative expectancy and should get no capital at all,
    #    so their covariance is not a basis for sizing. The slot cap binds, so weights
    #    can only scale a strategy's slot notional down (multiplier = w_i / max w).
    def _rp(run: EnsembleResult) -> dict:
        w = risk_parity_weights(run.daily_cov)
        wmax = max(w)
        mults = {n: x / wmax for n, x in zip(run.strategies, w)}
        return {"basis": run.label, "strategies": run.strategies, "weights": w, "slot_multipliers": mults,
                "slot_notional_rs": {n: m * SLOT_NOTIONAL_RS for n, m in mults.items()},
                "risk_contrib_equal_weight": risk_contributions(run.daily_cov, [1.0 / len(w)] * len(w)),
                "risk_contrib_risk_parity": risk_contributions(run.daily_cov, w),
                "daily_pnl_correlation": correlation(run.daily_cov)}
    rp = _rp(runs["EDGE_ALL_TAKER"])
    rp_spec = _rp(runs["EDGE_SPEC"])
    runs["EDGE_ALL_TAKER_RISK_PARITY"] = run_ensemble("EDGE_ALL_TAKER_RISK_PARITY", specs, edge_tab, fills,
                                                      "ALL_TAKER", n_paths, n_sessions, seed + 399,
                                                      rp["slot_multipliers"])
    log(f"[6/6] risk parity done ({time.time()-t_start:.0f}s)")

    return {
        "meta": {
            "generated_by": "research/execution_realism/monte_carlo_ensemble_audit.py",
            "seed": seed, "paths": n_paths, "sessions_per_path": n_sessions,
            "runtime_s": time.time() - t_start,
            "schedule": {"slot_notional_rs": SLOT_NOTIONAL_RS, "mis_roundtrip_friction": MIS_ROUNDTRIP_FRICTION,
                         "stop_pct_range": STOP_PCT_RANGE, "t1_r": T1_R, "t2_r": T2_R,
                         "claimed_breakeven_wr": CLAIMED_BREAKEVEN_WR, "max_slots": MAX_SLOTS},
            "empirical": EMPIRICAL,
            "priors": {"substeps": SUBSTEPS, "regime_drift_pct_bar": REGIME_DRIFT_PCT_BAR,
                       "shock_prob": SHOCK_PROB, "shock_z": SHOCK_Z, "shock_sigma_mult": SHOCK_SIGMA_MULT,
                       "genuine_share": GENUINE_SHARE, "type_drift_pct_bar": TYPE_DRIFT_PCT_BAR,
                       "half_spread_pct": HALF_SPREAD_PCT, "stop_slip_mean_pct": STOP_SLIP_MEAN_PCT,
                       "stop_skip_prob": STOP_SKIP_PROB, "taker_slip_mean_pct": TAKER_SLIP_MEAN_PCT,
                       "lock_prob": LOCK_PROB,
                       "queue": {k: (v if not isinstance(v, dict) else
                                     {kk: (vv if math.isfinite(vv) else "inf") for kk, vv in v.items()})
                                 for k, v in QUEUE.items()}},
            "strategies": [asdict(s) for s in specs],
        },
        "analytic_hurdles": analytic,
        "simulated_hurdles": hurdle,
        "cost_only_trade_stats": cost_only,
        "edge_trade_stats": edge_stats,
        "fill_layer": {"summary": fill_summary, "queue_decay_p_filled_by_s": decay,
                       "queue_prior_sensitivity": sensitivity,
                       "checkpoints_s": [60, 180, 300, 600, 900]},
        "ensembles": {k: asdict(v) for k, v in runs.items()},
        "risk_parity": rp,
        "risk_parity_spec_basis": rp_spec,
    }


def _fmt_report(res: dict) -> str:
    L = []
    L.append("== Simulated breakeven (ideal execution, zero net expectancy) ==")
    for n, h in res["simulated_hurdles"].items():
        L.append(f"  {n:13s} win_rate_net={h['win_rate_net']*100:5.1f}%  P(T1/target)={h['target1_hit_rate']*100:5.1f}%"
                 f"  runner_q={h['runner_q']:.3f}  drift={h['base_mu_pct_bar']:+.4f}%/bar")
    L.append("== Cost-only expectancy (no drift, no dispersion) ==")
    for n, h in res["cost_only_trade_stats"].items():
        L.append(f"  {n:13s} mean_net_R={h['mean_net_r']:+.3f}  gross={h['mean_gross_r']:+.3f}  win={h['win_rate_net']*100:.1f}%")
    L.append("== Fill layer ==")
    for mode, per in res["fill_layer"]["summary"].items():
        L.append(f"  {mode}: genuine share of filled qty={per['genuine_share_of_filled_qty']:.3f}"
                 f"  adverse-selection ratio={per['adverse_selection_ratio']:.2f}")
        for kind in TYPES:
            L.append(f"    {kind:12s} {per[kind]['state_share']}  fill_frac={per[kind]['mean_fill_frac']:.3f}")
    L.append("== Ensembles ==")
    for k, e in res["ensembles"].items():
        L.append(f"  {k:28s} trades/sess={e['trades_per_session']:.2f} R/trade={e['mean_trade_net_r']:+.3f}"
                 f" Rs/trade={e['mean_trade_pnl_rs']:+.1f} VaR99d=Rs{e['daily_var99_rs']:.0f} ES99d=Rs{e['daily_es99_rs']:.0f}"
                 f" 250d median=Rs{e['horizon_pnl_rs']['median']:.0f} P(loss)={e['p_horizon_loss']*100:.1f}%"
                 f" MDD p95=Rs{e['max_drawdown_rs']['p95']:.0f} streak p95={e['max_losing_streak']['p95']:.0f}"
                 f" gate60 pass={e['gate60']['p_pass']*100:.1f}% (t-test {e['gate60']['p_pass_t_test_5pct']*100:.1f}%)")
    rp = res["risk_parity"]
    L.append(f"== Risk parity ({rp['basis']} daily P&L covariance) ==")
    for n, w, m, a, b in zip(rp["strategies"], rp["weights"], rp["slot_multipliers"].values(),
                             rp["risk_contrib_equal_weight"], rp["risk_contrib_risk_parity"]):
        L.append(f"  {n:13s} w={w:.3f} slot_mult={m:.3f} RC_eq={a:.3f} RC_rp={b:.3f}")
    return "\n".join(L)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--paths", type=int, default=10_000)
    ap.add_argument("--sessions", type=int, default=250)
    ap.add_argument("--seed", type=int, default=20260924)
    ap.add_argument("--out", type=Path, default=Path("shared/track2_liquid/monte_carlo_ensemble_audit_results.json"))
    a = ap.parse_args(argv)
    res = run_audit(a.paths, a.sessions, a.seed)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=2, sort_keys=False, default=str), encoding="utf-8")
    print(_fmt_report(res))
    print(f"\nwrote {a.out}  ({res['meta']['runtime_s']:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
