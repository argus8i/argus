import math
import random
from dataclasses import replace

from execution_realism import monte_carlo_ensemble_audit as mc
from execution_realism.fills import FillState

ORB = next(s for s in mc.STRATEGIES if s.name == "ORB")
COMPASS = next(s for s in mc.STRATEGIES if s.name == "COMPASS")


def test_friction_schedule_matches_the_audited_slot():
    assert math.isclose(mc.SLOT_NOTIONAL_RS * mc.MIS_ROUNDTRIP_FRICTION, 61.83, abs_tol=0.01)
    assert math.isclose(mc.friction_r(0.010), 0.106)
    # the slot cap binds below a 2.57% stop, so the Rs 1,500 budget never binds in 1.0-1.5%
    assert math.isclose(mc.risk_rs(0.010), 583.33, abs_tol=0.01)
    assert math.isclose(mc.risk_rs(0.015), 875.0, abs_tol=0.01)
    assert mc.risk_rs(0.03) == mc.RISK_BUDGET_RS


def test_two_tranche_breakeven_closed_form():
    f = mc.friction_r(0.010)
    assert math.isclose(mc.two_tranche_breakeven(0.0, f), 1.106 / 1.75)
    assert math.isclose(mc.two_tranche_breakeven(1.0, f), 1.106 / 3.25)
    q = mc.runner_share_for_breakeven(0.44, f)
    assert math.isclose(mc.two_tranche_breakeven(q, f), 0.44)
    assert 0.50 < q < 0.52        # 44% needs about half of T1 winners to reach 3.0R
    # a single 1.5R exit at a 1% stop is what actually produces ~44%
    assert math.isclose(mc.single_target_breakeven(1.5, f), 0.4424, abs_tol=1e-4)


def test_zero_drift_trade_has_no_gross_edge():
    rng = random.Random(7)
    td = mc._TDraw(rng, mc.EMPIRICAL["t_dof"])
    n = 20_000
    gross = sum(mc.simulate_trade(rng, td, ORB, 0.0, mc.EMPIRICAL["bar_sigma_pct"], 0.0125)[0] for _ in range(n)) / n
    assert -0.05 < gross < 0.01   # martingale minus spread and stop slippage, within MC error


def test_stop_is_checked_before_target_and_losses_can_exceed_one_r():
    spec = replace(COMPASS, horizon_bars=200)
    rng = random.Random(3)
    td = mc._TDraw(rng, mc.EMPIRICAL["t_dof"])
    worst = min(mc.simulate_trade(rng, td, spec, 0.0, 0.01, 0.010)[0] for _ in range(3000))
    assert worst < -1.0           # SL-limit slippage and skips, never an exact -1R


def test_two_tranche_runner_moves_stop_to_entry():
    # huge upward drift: T1 always fills, and the runner can only end at >= its breakeven stop
    rng = random.Random(11)
    td = mc._TDraw(rng, 1000.0)
    outs = [mc.simulate_trade(rng, td, ORB, 0.01, 0.0005, 0.010) for _ in range(200)]
    assert all(mc.OUTCOMES[o].startswith("T1") for _, o in outs)
    assert all(g >= 0.5 * mc.T1_R - 0.05 for g, _ in outs)


def test_passive_fill_states_and_adverse_selection():
    rng = random.Random(5)
    gen = [mc.simulate_passive_entry(rng, "GENUINE") for _ in range(3000)]
    dist = [mc.simulate_passive_entry(rng, "DISTRIBUTION") for _ in range(3000)]
    states = {o.state for o in gen + dist}
    assert states <= {FillState.FILLED.value, FillState.PARTIAL.value, FillState.QUEUED.value,
                      FillState.LOCKED_NO_OFFER.value}
    for o in gen + dist:
        if o.state == FillState.FILLED.value:
            assert o.fill_frac == 1.0 and math.isfinite(o.t_fill_s)
        elif o.state == FillState.PARTIAL.value:
            assert 0.0 < o.fill_frac < 1.0
        else:
            assert o.fill_frac == 0.0
    g = sum(o.fill_frac for o in gen) / len(gen)
    d = sum(o.fill_frac for o in dist) / len(dist)
    assert d > g                  # a passive bid fills more often when the breakout fails


def test_queue_rank_decay_is_monotone():
    curve = mc.queue_decay_curve([1, 6, 24], [60, 300, 900], 1500, seed=9)
    for kind in mc.TYPES:
        for series in curve[kind].values():
            assert all(a <= b for a, b in zip(series, series[1:]))
        assert curve[kind]["rank_1Q"][-1] >= curve[kind]["rank_24Q"][-1]


def test_taker_always_fills_unless_locked_and_pays_the_spread():
    rng = random.Random(2)
    for _ in range(2000):
        o = mc.simulate_taker_entry(rng, "GENUINE")
        if o.state == FillState.FILLED.value:
            assert o.entry_cost_pct >= mc.HALF_SPREAD_PCT
        else:
            assert o.state == FillState.LOCKED_NO_OFFER.value and o.fill_frac == 0.0


def test_risk_parity_equalises_risk_contributions():
    cov = [[4.0, 0.6, 0.2], [0.6, 1.0, 0.1], [0.2, 0.1, 0.25]]
    w = mc.risk_parity_weights(cov)
    assert math.isclose(sum(w), 1.0)
    rc = mc.risk_contributions(cov, w)
    assert max(rc) - min(rc) < 1e-6
    assert w[0] < w[1] < w[2]


def test_small_ensemble_is_reproducible_and_respects_slots():
    specs = list(mc.STRATEGIES)
    tab = mc.build_trade_tables(specs, {s.name: 0.0 for s in specs}, 60, seed=1)
    fills = mc.build_fill_tables(300, seed=2)
    a = mc.run_ensemble("A", specs, tab, fills, "SPEC", 40, 30, seed=3)
    b = mc.run_ensemble("A", specs, tab, fills, "SPEC", 40, 30, seed=3)
    assert a.mean_trade_pnl_rs == b.mean_trade_pnl_rs and a.daily_var99_rs == b.daily_var99_rs
    assert a.trades_per_session <= mc.MAX_SLOTS
    assert set(a.fill_states) <= {s.value for s in FillState}
    ideal = mc.run_ensemble("I", specs, tab, fills, "IDEAL", 40, 30, seed=3)
    assert set(ideal.fill_states) == {"FILLED"}
