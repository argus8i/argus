"""
P6 decision engine tests (plan P6.1-P6.7). All data is synthetic; the register and the pre-registration
are copied to tmp_path, so the committed register is never written.
"""
from __future__ import annotations

import json
import math
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from research.backtest.metrics import clustered_t
from research.decision import clusters as cl
from research.decision import promotion as pr
from research.decision import stress as sx
from research.decision.allocator import EXPLOIT, Candidate, Position, allocate
from research.decision.estimator import StrategyEstimate, estimate, expected_net_r, signal_cost
from research.decision.ledger import DuplicateSignalError, Ledger, signal_id
from research.decision.sizing import Multiplier, base_qty, final_qty, vol_target_multiplier
from research.decision.stats import clustered_se, lb95
from research.studies import prereg_io

IST = timezone(timedelta(hours=5, minutes=30))
D0 = date(2026, 9, 1)


# ================================================================== P6.2 stats and estimator
def test_clustered_se_matches_the_locked_gate_t():
    rng = np.random.default_rng(0)
    x = rng.normal(0.1, 0.74, 90)
    g = [k // 3 for k in range(90)]
    assert np.mean(x) / clustered_se(x, g) == pytest.approx(clustered_t(x, g))
    assert math.isnan(clustered_se([1.0], [0])) and math.isnan(clustered_se([1.0, 2.0], [0, 0]))
    assert lb95(0.1, 0.05) == pytest.approx(0.1 - 1.645 * 0.05) and math.isnan(lb95(0.1, math.nan))


def test_estimate_shrinks_gross_toward_zero_and_refuses_mixed_bases():
    rows = [{"net_r": 0.1, "gross_r": 0.2, "session": D0 + timedelta(days=k), "r_basis": "stop_limit"}
            for k in range(15)]
    e = estimate("S", rows, "stop_limit")
    assert e.n == 15 and e.shrunk_gross_r == pytest.approx(15 / (15 + 85) * 0.2)
    with pytest.raises(ValueError):
        estimate("S", rows + [{"net_r": 0, "gross_r": 0, "session": D0, "r_basis": "trigger"}], "stop_limit")
    assert StrategyEstimate.empty("S", "stop_limit").shrunk_gross_r == 0.0


def test_signal_cost_uses_the_signals_own_stop_distance():
    wide = signal_cost(500.0, 490.0, "BUY", 100)          # risk Rs 1,000
    tight = signal_cost(500.0, 497.5, "BUY", 100)         # risk Rs 250, same rupee fees
    assert wide.fees_rs == pytest.approx(tight.fees_rs)
    assert tight.fee_r == pytest.approx(4 * wide.fee_r)
    assert wide.slip_rs == pytest.approx(2 * 0.05 * 100)   # 1 tick per side at Rs 0.05
    est = StrategyEstimate("S", "stop_limit", None, 100, 60, 0.1, 0.05, 0.02, 0.2, 0.1)
    assert expected_net_r(est, wide) == pytest.approx(0.1 - wide.fee_r - wide.slip_r)
    assert math.isnan(expected_net_r(est, signal_cost(500.0, 500.0, "BUY", 100)))


# ================================================================== P6.3 sizing
def _vix_hist(level=14.0, n=250):
    return [level] * n


def test_multiplier_cases():
    now = datetime(2026, 9, 25, 10, 0, tzinfo=IST)
    fresh = now - timedelta(seconds=30)
    assert vol_target_multiplier(28.0, fresh, _vix_hist(), now).m == pytest.approx(0.5)      # jump rule too
    assert vol_target_multiplier(15.0, fresh, _vix_hist(), now).m == pytest.approx(14 / 15)
    assert vol_target_multiplier(10.0, fresh, _vix_hist(), now).m == 1.0                      # never above 1
    jump = vol_target_multiplier(16.2, fresh, _vix_hist(14.0), now)                          # +15.7% overnight
    assert (jump.m, jump.reason) == (0.5, "VIX_OVERNIGHT_JUMP")
    no_jump = vol_target_multiplier(16.0, fresh, _vix_hist(14.0), now)                       # +14.3%
    assert no_jump.reason == "OK" and no_jump.m == pytest.approx(14 / 16)
    assert vol_target_multiplier(14.0, now - timedelta(minutes=6), _vix_hist(), now).m == 0.0
    for bad in (None, float("nan"), -1.0, True, "14"):
        assert vol_target_multiplier(bad, fresh, _vix_hist(), now).m == 0.0
    assert vol_target_multiplier(14.0, fresh, _vix_hist(n=119), now).reason == "VIX_HISTORY_119_OF_120"


def test_base_and_final_qty():
    assert base_qty(500.0, 495.0, 1e6) == 76                       # Adjusted A1 cap 38,000/500 binds (risk: 300)
    assert base_qty(100.0, 90.0, 1e6) == 150                       # risk binds
    assert base_qty(500.0, 495.0, 10_000) == 20                    # free cash binds
    assert base_qty(500.0, 500.0, 1e6) == 0 and base_qty(500.0, 495.0, 0) == 0
    assert final_qty(116, Multiplier(0.5, "x")) == 58 and final_qty(116, Multiplier(1.7, "x")) == 116
    assert final_qty(116, Multiplier(0.0, "VIX_STALE")) == 0


# ================================================================== P6.1 ledger
def _row(**kw):
    base = {"run_id": "r1", "mode": "BACKTEST", "strategy_id": "RESID_REV", "strategy_version": "v1",
            "session": D0, "decision_ts": datetime(2026, 9, 1, 10, 0, tzinfo=IST), "symbol": "AAA", "side": "BUY",
            "r_basis": "stop_limit", "disposition": "ALLOCATED", "allocated": True, "evidence_class": "E1",
            "net_r": 0.25, "features": {"Z": -3.1}}
    base.update(kw)
    return base


def test_ledger_is_append_only_and_keyed(tmp_path):
    import sqlite3

    lg = Ledger(tmp_path / "l.sqlite")
    [sid] = lg.append([_row()])
    assert sid == signal_id("r1", "BACKTEST", "v1", "AAA", "2026-09-01T10:00:00+05:30")
    with pytest.raises(DuplicateSignalError):
        lg.append([_row(net_r=9.9)])
    lg.append([_row(symbol="BBB", net_r=float("nan"), disposition="MAX_SLOTS", allocated=False,
                    evidence_class="E1_CF")])
    rows = lg.rows()
    assert [r["symbol"] for r in rows] == ["AAA", "BBB"] and rows[1]["net_r"] is None
    assert json.loads(rows[0]["features"]) == {"Z": -3.1}
    con = sqlite3.connect(tmp_path / "l.sqlite")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE signals SET net_r = 1.0")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM signals")
    con.close()
    assert lg.ledger_hash() == Ledger(tmp_path / "l.sqlite").ledger_hash()
    assert lg.rows(evidence=["E1_CF"])[0]["symbol"] == "BBB"
    for bad in ({"mode": "LIVE"}, {"evidence_class": "E9"}, {"r_basis": "close"}, {"symbol": ""}, {"oops": 1}):
        with pytest.raises(ValueError):
            lg.append([_row(**{"symbol": "ZZZ", **bad})])


def test_rows_from_engine_covers_every_intent():
    from types import SimpleNamespace

    from research.backtest.strategies import SignalIntent
    from research.decision.ledger import rows_from_engine

    t = datetime(2026, 9, 1, 10, 0, tzinfo=IST)
    s1 = SignalIntent("S", "AAA", "BUY", 99.0, ((101.0, 0.5), (102.0, 0.5)), signal_time=t, entry_ref=100.0,
                      disposition="ALLOCATED", r_basis="stop_limit", trade_id="T1")
    s2 = SignalIntent("S", "BBB", "SELL", 101.0, ((99.0, 1.0),), signal_time=t, entry_ref=100.0,
                      disposition="MAX_SLOTS", r_basis="stop_limit", counterfactual_net_r=-0.4)
    tr = SimpleNamespace(trade_id="T1", stop_limit=98.5, entry_price=100.05, entry_time=t, evidence_class="E1",
                         exit_reason="STOP", exits=[SimpleNamespace(price=98.9, qty=10, reason="STOP", time=t)],
                         gross_pnl=-11.5, charges=5.0, slippage_rs=1.0, rms_fee=0.0, net_pnl=-16.5, gross_r=-0.7,
                         fee_r=0.3, slip_r=0.06, net_r=-1.06, risk_rs=15.5, qty=10)
    rows = rows_from_engine(SimpleNamespace(trades=[tr], signals=[s1, s2]), run_id="r", mode="BACKTEST",
                            strategy_version={"S": "v1"})
    assert [r["evidence_class"] for r in rows] == ["E1", "E1_CF"]
    assert rows[0]["allocated"] and rows[0]["net_r"] == -1.06 and rows[1]["net_r"] == -0.4


# ================================================================== P6.4 clusters
def _block_corr(sizes, rho_in, rho_out=0.0):
    n = sum(sizes)
    c = np.full((n, n), rho_out)
    k = 0
    for s in sizes:
        c[k:k + s, k:k + s] = rho_in
        k += s
    np.fill_diagonal(c, 1.0)
    return c


def test_correlated_blocks_are_recovered():
    rng = np.random.default_rng(3)
    sizes = [8, 8, 8, 6]
    L = np.linalg.cholesky(_block_corr(sizes, 0.7))
    X = rng.standard_normal((2000, sum(sizes))) @ L.T
    names = [f"S{i:02d}" for i in range(sum(sizes))]
    lab = cl.cluster_from_corr(np.corrcoef(X.T), names, max_frac=0.5)
    blocks, k = [], 0
    for s in sizes:
        blocks.append({lab[names[i]] for i in range(k, k + s)})
        k += s
    assert all(len(b) == 1 for b in blocks) and len({next(iter(b)) for b in blocks}) == len(sizes)


def test_factor_structure_never_yields_a_cluster_above_ten_percent():
    """180 names, one market-like factor with mean pairwise rho ~ 0.3 (the plan's chaining case) plus sector
    blocks. Single linkage chains into one giant cluster here; the capped average-linkage scheme does not."""
    rng = np.random.default_rng(11)
    n, T = 180, 1380
    load = rng.uniform(0.35, 0.72, n)
    sector = np.repeat(np.arange(18), 10)
    f = rng.standard_normal(T)
    s = rng.standard_normal((T, 18))
    idio = np.sqrt(np.clip(1 - load ** 2 - 0.35 ** 2, 0.05, 1))
    X = f[:, None] * load + s[:, sector] * 0.35 + rng.standard_normal((T, n)) * idio
    C = np.corrcoef(X.T)
    assert 0.2 < C[~np.eye(n, dtype=bool)].mean() < 0.4
    sizes = np.bincount(list(cl.cluster_from_corr(C, [f"N{i}" for i in range(n)]).values()))
    assert sizes.max() <= 18


def test_weekly_clusters_use_only_prior_data_and_fail_closed_on_short_history():
    from research.tests.synthetic import SyntheticSpec, make_store, sessions

    spec = SyntheticSpec(n_sessions=60)
    days = sessions(spec)
    m = cl.weekly_clusters(make_store(spec), list(spec.stocks), days, min_sessions=40)
    first, last = days[0], days[-1]
    assert all(v.endswith(":UNCLUSTERED") for v in m[first].values())       # no prior data in week 1
    assert all(":C" in v for v in m[last].values())                          # >= 40 prior sessions by the end
    week = [d for d in days if d.isocalendar()[:2] == last.isocalendar()[:2]]
    assert all(m[d] == m[week[0]] for d in week)
    # changing data on and after the week's first session does not change that week's clusters
    later = {(s, days.index(week[0]) + k): {3: 1.05} for s in spec.stocks for k in range(len(week))}
    m2 = cl.weekly_clusters(make_store(spec, overrides=later), list(spec.stocks), days, min_sessions=40)
    assert m2[week[0]] == m[week[0]]


# ================================================================== P6.5 allocator
def _cand(sym, **kw):
    base = dict(strategy_id="RESID_REV", symbol=sym, side="BUY", session=D0, priority=1, priority_score=3.0,
                expected_net_r=0.1, se=0.05, risk_rs=1500.0, m=1.0, notional=35_000.0, cluster=f"c{sym}",
                sector=f"s{sym}")
    base.update(kw)
    return Candidate(**base)


def test_renaming_symbols_does_not_change_the_result():
    cands = [_cand(s, priority_score=sc) for s, sc in zip("ABCDE", (3.1, 2.9, 3.5, 2.7, 3.3))]
    a = allocate(cands, free_cash=1e6)
    ren = {"A": "Q", "B": "Z", "C": "M", "D": "B", "E": "A"}
    b = allocate([_cand(ren[c.symbol], priority_score=c.priority_score, cluster=c.cluster, sector=c.sector)
                  for c in cands], free_cash=1e6)
    assert [ren[c.symbol] for c in a.allocated] == [c.symbol for c in b.allocated]
    assert [c.symbol for c in a.allocated] == ["C", "E", "A"]                  # top 3 by |Z|


@pytest.mark.parametrize("kw,open_,cash,reason", [
    ({}, [Position("X1", 10), Position("X2", 10), Position("X3", 10)], 1e6, "MAX_SLOTS"),
    ({"notional": 38_000.01}, [], 1e6, "SLOT_CAP"),
    ({}, [], 30_000.0, "NO_FREE_CASH"),
    ({"cluster": "c1"}, [Position("X1", 10, cluster="c1")], 1e6, "CLUSTER_LIMIT"),
    ({"sector": "bank"}, [Position("X1", 10, sector="bank"), Position("X2", 10, sector="bank")], 1e6, "SECTOR_LIMIT"),
    ({}, [Position("AAA", 10)], 1e6, "ALREADY_HELD"),
])
def test_every_constraint_binds(kw, open_, cash, reason):
    res = allocate([_cand("AAA", **kw)], open_positions=open_, free_cash=cash)
    assert res.allocated == [] and res.dropped[0][1] == reason


def test_unknown_cluster_and_sector_are_capped_together():
    res = allocate([_cand("A", cluster=None), _cand("B", cluster=None, priority_score=2.0)], free_cash=1e6)
    assert [c.symbol for c in res.allocated] == ["A"] and res.dropped[0][1] == "CLUSTER_LIMIT"


def test_conflict_rules():
    same = allocate([_cand("A", strategy_id="S1", expected_net_r=0.05), _cand("A", strategy_id="S2", expected_net_r=0.2)],
                    free_cash=1e6)
    assert [c.strategy_id for c in same.allocated] == ["S2"] and same.dropped[0][1] == "DUPLICATE_SAME_SIDE_LOWER_E"
    both = allocate([_cand("A", side="BUY", expected_net_r=0.10), _cand("A", side="SELL", expected_net_r=0.05)],
                    free_cash=1e6)
    assert both.allocated == [] and {r for _, r in both.dropped} == {"CONFLICT_OPPOSITE_SIDES"}
    clear = allocate([_cand("A", side="BUY", expected_net_r=0.40, se=0.05),
                      _cand("A", side="SELL", expected_net_r=0.05, se=0.05)], free_cash=1e6)
    assert [c.side for c in clear.allocated] == ["BUY"]                        # |dE| 0.35 >= 2 * 0.0707
    unknown = allocate([_cand("A", side="BUY", se=math.nan), _cand("A", side="SELL", se=math.nan)], free_cash=1e6)
    assert unknown.allocated == []


def test_exploit_never_allocates_non_positive_expectation_or_unpromoted():
    cands = [_cand("A", expected_net_r=0.0), _cand("B", expected_net_r=-0.1), _cand("C", expected_net_r=math.nan),
             _cand("D", expected_net_r=0.2, strategy_id="ORB_PROD"), _cand("E", expected_net_r=0.05),
             _cand("F", expected_net_r=0.15, risk_rs=400.0)]
    res = allocate(cands, mode=EXPLOIT, free_cash=1e6, promoted={"RESID_REV"})
    assert [c.symbol for c in res.allocated] == ["E", "F"]                     # 0.05*1500 = 75 > 0.15*400 = 60
    assert all(c.expected_net_r > 0 for c in res.allocated)
    assert {c.symbol: r for c, r in res.dropped} == {"A": "NON_POSITIVE_EXPECTED_R", "B": "NON_POSITIVE_EXPECTED_R",
                                                     "C": "NON_POSITIVE_EXPECTED_R", "D": "NOT_PROMOTED"}
    with pytest.raises(ValueError):
        allocate(cands, mode="LIVE", free_cash=1e6)


# ================================================================== P6.6 promotion: operating characteristics
def _rows(x, sessions):
    return [{"net_r": float(v), "session": s, "evidence_class": "E3"} for v, s in zip(x, sessions)]


def _run_shadow(rng, mu, n_pre, sess_of, sessions_observed):
    x = rng.normal(mu, 0.74, n_pre)
    sess = [sess_of(k) for k in range(n_pre)]
    half = math.ceil(n_pre / 2)
    d = pr.decide_shadow(pr.SHADOW, False, _rows(x[:half], sess[:half]), sessions_observed, n_pre=n_pre)
    if d.state == pr.KILLED:
        return "KILLED_FUTILITY"
    d = pr.decide_shadow(pr.SHADOW, d.futility_checked, _rows(x, sess), sessions_observed, n_pre=n_pre)
    return d.state


def test_false_promotion_rate_at_zero_edge_is_at_most_3_5_percent():
    rng = np.random.default_rng(20260925)
    out = [_run_shadow(rng, 0.0, 85, lambda k: k % 60, 60) for _ in range(10_000)]
    assert out.count(pr.PROMOTE) / len(out) <= 0.035


def test_power_at_plus_0_20r_with_111_independent_trades():
    rng = np.random.default_rng(7)
    out = [_run_shadow(rng, 0.20, 111, lambda k: k, 111) for _ in range(4_000)]
    assert out.count(pr.PROMOTE) / len(out) == pytest.approx(0.80, abs=0.03)


def test_futility_kills_most_losing_strategies_by_half_n_pre():
    rng = np.random.default_rng(3)
    out = [_run_shadow(rng, -0.20, 111, lambda k: k, 111) for _ in range(4_000)]
    assert out.count("KILLED_FUTILITY") / len(out) >= 0.60


def test_e1_rows_can_never_promote():
    rows = [{"net_r": 1.0 + 0.01 * k, "session": k, "evidence_class": "E1"} for k in range(200)]
    d = pr.decide_shadow(pr.SHADOW, False, rows, 200)
    assert d.n == 0 and d.state == pr.SHADOW


# ================================================================== P6.6 promotion: register transitions
@pytest.fixture
def reg(tmp_path):
    p = tmp_path / "register.json"
    shutil.copy(pr.REGISTER_PATH, p)
    return p


def _locked_prereg(tmp_path):
    src = prereg_io.PREREG_DIR / "resid_rev_v1.yaml"
    y = tmp_path / "resid_rev_v1.yaml"
    y.write_bytes(src.read_bytes().replace(b"\r\n", b"\n").replace(b"status: DRAFT", b"status: LOCKED"))
    sha = prereg_io.normalised_sha256(y)
    y.with_suffix(".lock").write_text(json.dumps({"id": "RESID_REV_v1", "yaml_sha256": sha, "commit": "b" * 40,
                                                  "locked_at": "2026-10-01T10:00:00+05:30"}), encoding="utf-8")
    return y, sha


def test_holdout_transition_requires_a_verified_lock(tmp_path, reg):
    y, sha = _locked_prereg(tmp_path)
    rec = {"strategy_id": "RESID_REV", "prereg_sha256": sha, "evidence_class": "E1", "passed": True}
    rd = tmp_path / "rec"
    with pytest.raises(pr.TransitionError):                                   # record cites another prereg
        pr.apply_holdout("RESID_REV", {**rec, "prereg_sha256": "0" * 64}, y, register_path=reg, records_dir=rd)
    with pytest.raises(pr.TransitionError):                                   # E2 is not a holdout record
        pr.apply_holdout("RESID_REV", {**rec, "evidence_class": "E2"}, y, register_path=reg, records_dir=rd)
    draft = tmp_path / "draft.yaml"
    shutil.copy(prereg_io.PREREG_DIR / "resid_rev_v1.yaml", draft)
    with pytest.raises(pr.TransitionError):                                   # no lock
        pr.apply_holdout("RESID_REV", rec, draft, register_path=reg, records_dir=rd)
    out = pr.apply_holdout("RESID_REV", rec, y, register_path=reg, records_dir=rd)
    assert out["to"] == pr.SHADOW and Path(out["record_file"]).exists()
    assert pr.load_register(reg)["strategies"]["RESID_REV"]["status"] == pr.SHADOW
    with pytest.raises(pr.TransitionError):                                   # holdout only from UNVERIFIED
        pr.apply_holdout("RESID_REV", rec, y, register_path=reg, records_dir=rd)
    failed = pr.apply_holdout("ORB_PROD", {**rec, "strategy_id": "ORB_PROD", "passed": False}, y,
                              register_path=reg, records_dir=rd)
    assert failed["to"] == pr.REJECTED


def test_shadow_evaluation_single_futility_look_and_terminal_test(tmp_path, reg):
    y, sha = _locked_prereg(tmp_path)
    rd = tmp_path / "rec"
    pr.apply_holdout("RESID_REV", {"strategy_id": "RESID_REV", "prereg_sha256": sha, "evidence_class": "E1",
                                   "passed": True}, y, register_path=reg, records_dir=rd)
    rows = _rows(np.random.default_rng(5).normal(0.35, 0.74, 111), range(111))
    cf = [{"net_r": -5.0, "session": 0, "evidence_class": "E1_CF"}] * 30          # never counted
    r1 = pr.evaluate_shadow("RESID_REV", rows[:40] + cf, 40, register_path=reg, records_dir=rd)
    assert r1["action"] == "NONE" and r1["inputs"]["n_admissible"] == 40
    r2 = pr.evaluate_shadow("RESID_REV", rows[:60], 60, register_path=reg, records_dir=rd)
    assert r2["action"] == "FUTILITY_PASSED"
    assert pr.load_register(reg)["strategies"]["RESID_REV"]["futility_checked"] is True
    r3 = pr.evaluate_shadow("RESID_REV", rows[:80], 80, register_path=reg, records_dir=rd)
    assert r3["action"] == "NONE"                                                  # no second futility look
    r4 = pr.evaluate_shadow("RESID_REV", rows, 111, register_path=reg, records_dir=rd)
    assert r4["action"] in ("TERMINAL_PROMOTE", "TERMINAL_KILL") and r4["gate"] is not None
    assert r4["psr"] is not None and len(r4["ledger_hash"]) == 64
    with pytest.raises(pr.TransitionError):                                        # terminal: no further looks
        pr.evaluate_shadow("RESID_REV", rows, 111, register_path=reg, records_dir=rd)


# ================================================================== P6.7 stress
def test_t_quantiles_match_tables():
    for nu, q in ((3, -4.5407), (5, -3.3649), (8, -2.8965)):                   # t_nu^{-1}(0.01), standard tables
        assert sx.t_threshold(0.01, nu) == pytest.approx(q, abs=2e-4)
    assert sx.t_threshold(0.01, math.inf) == pytest.approx(-2.3263, abs=1e-4)


def test_gaussian_copula_at_rho_zero_is_binomial():
    k = sx.simulate_stops(0.05, 0.0, math.inf, draws=400_000, seed=1).sum(axis=1)
    mc = np.array([np.mean(k == j) for j in range(4)])
    assert np.allclose(mc, sx.mixture_probs(0.05, math.inf), atol=2e-3)


@pytest.mark.parametrize("nu", [3, 5, 8])
def test_t_copula_at_rho_zero_matches_the_mixture_not_the_binomial(nu):
    st = sx.simulate_stops(0.01, 0.0, nu, draws=1_000_000, seed=nu)
    k = st.sum(axis=1)
    mix = sx.mixture_probs(0.01, nu)
    assert np.mean(st) == pytest.approx(0.01, rel=0.03)                     # marginal hazard is p0
    assert np.mean(k == 2) == pytest.approx(mix[2], rel=0.1)
    # 3 of 3 vs the binomial (MEASURED 25 Sep: 262x, 102x, 40x; the plan's "49-249x" is erratum E-P6.7)
    assert mix[3] / 0.01 ** 3 == pytest.approx({3: 262.2, 5: 101.7, 8: 40.1}[nu], rel=0.02)


def test_stress_run_reports_quantiles_and_the_band_scenario():
    res = sx.run(draws=100_000, nus=(3,), rhos=(0.45,))
    g = res["grid"][0]
    assert abs(sum(g["p_k"]) - 1.0) < 1e-9
    assert set(g["loss_quantiles_rs"]) == {"q95", "q99", "q99.9"}
    minus10 = res["scenarios_before_costs"][0]                                 # Adjusted A1 (was Rs 17,500)
    assert (minus10["loss_rs"], minus10["within_budget"]) == (11_400.0, True)
    assert "p_loss_above_scenario_budget" in g
    assert "not a guaranteed maximum loss" in res["caveat"]
