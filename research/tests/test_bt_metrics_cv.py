"""Metrics (Sharpe/Sortino/Calmar/DSR/PBO/clustered t/markouts/gate) and purged CV tests."""
import math
from datetime import date

import numpy as np
import pytest

from research.backtest import metrics as M
from research.backtest.cross_validation import CombinatorialPurgedKFold, PurgedKFold


def test_sharpe_matches_hand_calculation():
    r = np.array([0.01, -0.01, 0.02, 0.0])
    per_period = 0.005 / math.sqrt(0.0005 / 3)
    assert M.sharpe(r, periods_per_year=1) == pytest.approx(per_period, rel=1e-9)
    assert M.sharpe(r) == pytest.approx(per_period * math.sqrt(252), rel=1e-9)


def test_sharpe_is_nan_without_dispersion():
    assert math.isnan(M.sharpe(np.array([0.01, 0.01, 0.01])))


def test_sortino_uses_downside_deviation():
    r = np.array([0.02, -0.01, 0.03, -0.02])
    dd = math.sqrt((0.01 ** 2 + 0.02 ** 2) / 4)
    assert M.sortino(r, periods_per_year=1) == pytest.approx(0.005 / dd, rel=1e-9)


def test_max_drawdown_and_duration():
    eq = np.array([100, 110, 90, 95, 120, 100], dtype=float)
    dd = M.max_drawdown(eq)
    assert dd["max_drawdown"] == pytest.approx(-20 / 110)
    assert dd["peak_index"] == 1 and dd["trough_index"] == 2
    assert dd["longest_underwater_periods"] == 2


def test_calmar_sign_and_nan():
    assert M.calmar(np.array([0.01, -0.02, 0.015, 0.01]), periods_per_year=252) > 0
    assert math.isnan(M.calmar(np.array([0.01, 0.01])))          # no drawdown -> undefined


def test_psr_is_half_at_benchmark_and_rises_with_sample():
    assert M.probabilistic_sharpe(0.0, 100, 0.0, 3.0, 0.0) == pytest.approx(0.5)
    assert M.probabilistic_sharpe(0.1, 400, 0.0, 3.0) > M.probabilistic_sharpe(0.1, 100, 0.0, 3.0)


def test_expected_max_sharpe_grows_with_trials_and_dsr_penalises_search():
    assert M.expected_max_sharpe(100, 1.0) > M.expected_max_sharpe(10, 1.0) > 0
    rng = np.random.default_rng(1)
    r = rng.normal(0.001, 0.01, 250)
    one = M.deflated_sharpe(r, n_trials=1, trials_sharpe_variance=0.0)
    many = M.deflated_sharpe(r, n_trials=50, trials_sharpe_variance=0.01)
    assert many < one


def test_clustered_t_equals_naive_with_singleton_clusters_and_shrinks_with_duplicates():
    x = np.array([0.5, -0.2, 1.1, 0.3, -0.7, 0.9])
    naive = x.mean() / (x.std(ddof=1) / math.sqrt(len(x)))
    assert M.clustered_t(x, list(range(len(x)))) == pytest.approx(naive, rel=1e-9)
    doubled = np.repeat(x, 2)
    clusters = np.repeat(np.arange(len(x)), 2)
    naive_doubled = doubled.mean() / (doubled.std(ddof=1) / math.sqrt(len(doubled)))
    assert abs(M.clustered_t(doubled, clusters)) < abs(naive_doubled)


def test_block_bootstrap_ci_is_seeded_and_brackets_mean():
    x = np.linspace(-1, 2, 40)
    days = np.repeat(np.arange(10), 4)
    lo, hi = M.block_bootstrap_ci(x, days, reps=500, seed=3)
    assert lo < x.mean() < hi
    assert (lo, hi) == M.block_bootstrap_ci(x, days, reps=500, seed=3)


def test_pbo_averages_one_half_for_noise_and_near_zero_for_real_edge():
    # One noise draw can land anywhere in [0.06, 0.96] (a configuration lucky over the whole sample looks
    # consistent in every split), so the property holds on average across draws, not per draw.
    noise_pbo = [M.probability_of_backtest_overfitting(np.random.default_rng(s).normal(0, 0.01, (320, 10)),
                                                       n_splits=8)["pbo"] for s in range(20)]
    assert 0.40 <= float(np.mean(noise_pbo)) <= 0.65
    edge = np.random.default_rng(7).normal(0, 0.01, (320, 10))
    edge[:, 0] += 0.004
    assert M.probability_of_backtest_overfitting(edge, n_splits=8)["pbo"] < 0.10


def test_markout_sign_convention():
    assert M.markout_bps(+1, 100.0, 101.0) == pytest.approx(100.0)
    assert M.markout_bps(-1, 100.0, 101.0) == pytest.approx(-100.0)


def test_gate_fails_without_admissible_evidence():
    trades = [{"evidence_class": "BAR_MODELLED", "net_r": 0.5, "session": date(2026, 9, d % 28 + 1)}
              for d in range(120)]
    g = M.evaluate_gate(trades, sessions_observed=70)
    assert g["passed"] is False and g["admissible_executions"] == 0
    assert "admissible" in " ".join(g["reasons"]).lower()


def test_gate_passes_only_with_sessions_count_and_t():
    rng = np.random.default_rng(2)
    trades = [{"evidence_class": "E3", "net_r": float(v), "session": date(2026, 1 + i // 28, i % 28 + 1)}
              for i, v in enumerate(rng.normal(0.4, 0.7, 120))]
    assert M.evaluate_gate(trades, sessions_observed=70)["passed"] is True
    assert M.evaluate_gate(trades, sessions_observed=59)["passed"] is False


def test_purged_kfold_never_overlaps_and_embargoes():
    n = 100
    t0 = np.arange(n, dtype=float)
    t1 = t0 + 3                                    # each sample lives 3 periods
    embargo = int(math.ceil(0.02 * n))
    cv = PurgedKFold(n_splits=5, embargo_pct=0.02)
    seen = []
    for train, test in cv.split(t0, t1):
        lo, hi = t0[test].min(), t1[test].max()
        assert not np.any((t0[train] <= hi) & (t1[train] >= lo))          # purged
        after = train[train > test.max()]
        assert np.all(t0[after] > hi + embargo)                          # embargoed
        seen.extend(test.tolist())
    assert sorted(seen) == list(range(n))


def test_cpcv_split_and_path_counts():
    cv = CombinatorialPurgedKFold(n_groups=6, n_test_groups=2, embargo_pct=0.0)
    n = 60
    t0 = np.arange(n, dtype=float)
    splits = list(cv.split(t0, t0 + 1))
    assert len(splits) == 15 and cv.n_paths == 5
    counts = {g: 0 for g in range(6)}
    for _, test_groups, _ in splits:
        for g in test_groups:
            counts[g] += 1
    assert set(counts.values()) == {5}
    paths = cv.assemble_paths({i: {g: f"s{i}g{g}" for g in s[1]} for i, s in enumerate(splits)})
    assert len(paths) == 5 and all(len(p) == 6 for p in paths)
    assert all(len({p[g] for p in paths}) == 5 for g in range(6))      # each path uses a different split per group


def test_two_tranche_breakeven_hurdles_match_analytical_table():
    # q = 0.5 (random walk runner)
    assert M.two_tranche_breakeven_hurdle(0.10, q_runner=0.5) == pytest.approx(1.10 / 2.50, rel=1e-6)  # 0.440
    assert M.two_tranche_breakeven_hurdle(0.15, q_runner=0.5) == pytest.approx(1.15 / 2.50, rel=1e-6)  # 0.460
    assert M.two_tranche_breakeven_hurdle(0.25, q_runner=0.5) == pytest.approx(1.25 / 2.50, rel=1e-6)  # 0.500
    assert M.two_tranche_breakeven_hurdle(0.40, q_runner=0.5) == pytest.approx(1.40 / 2.50, rel=1e-6)  # 0.560

    # q = 0.0 (runner never hits +3R, always breakeven)
    assert M.two_tranche_breakeven_hurdle(0.10, q_runner=0.0) == pytest.approx(1.10 / 1.75, rel=1e-4)  # 0.6286
    assert M.two_tranche_breakeven_hurdle(0.25, q_runner=0.0) == pytest.approx(1.25 / 1.75, rel=1e-4)  # 0.7143

    # q = 1.0 (runner always hits +3R)
    assert M.two_tranche_breakeven_hurdle(0.10, q_runner=1.0) == pytest.approx(1.10 / 3.25, rel=1e-4)  # 0.3385


def test_slot_cap_binding_threshold_and_friction_scaling():
    # Rs 1500 / Rs 58,333.33 = 2.5714%
    s_star = M.slot_cap_binding_stop(1500.0, 58333.33)
    assert s_star == pytest.approx(0.025714, rel=1e-4)

    # At 0.5% stop: 1R = 58,333.33 * 0.005 = Rs 291.67
    # MIS (Rs 61.86): 61.86 / 291.67 = 0.212R
    c_mis_05 = M.friction_in_r(0.005, notional_rs=58333.33, friction_rs=61.86)
    assert c_mis_05 == pytest.approx(0.212, abs=0.005)

    # CNC (Rs 144.40): 144.40 / 291.67 = 0.495R
    c_cnc_05 = M.friction_in_r(0.005, notional_rs=58333.33, friction_rs=144.40)
    assert c_cnc_05 == pytest.approx(0.495, abs=0.005)

    # At 1.0% stop: 1R = 58,333.33 * 0.01 = Rs 583.33
    c_mis_10 = M.friction_in_r(0.010, notional_rs=58333.33, friction_rs=61.86)
    assert c_mis_10 == pytest.approx(0.106, abs=0.005)

    # Beyond binding stop (e.g. 3.0% stop): 1R is capped at Rs 1500
    c_mis_30 = M.friction_in_r(0.030, notional_rs=58333.33, friction_rs=61.86, risk_budget_rs=1500.0)
    assert c_mis_30 == pytest.approx(61.86 / 1500.0, rel=1e-5)

