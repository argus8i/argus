"""
P4 tests (plan P4.1-P4.4): known answers, three kinds of look-ahead test, fail-closed inputs.
Synthetic data only (research/tests/synthetic.py).
"""
from __future__ import annotations

import math
from dataclasses import replace
from datetime import date

import numpy as np
import pytest

from research.backtest.bars import CandleStore
from research.features import timeprofile as tp
from research.features.calibration import CalibrationConfig, CalibrationProvider
from research.features.events import NoEventsData, TableEvents, is_monthly_stock_expiry
from research.features.session_cache import market_path, residual_paths, rvol_path, slot_series
from research.studies import prereg_io
from research.tests.synthetic import TRUE_SHAPE, SyntheticSpec, make_store, sessions

SPEC = prereg_io.load(prereg_io.PREREG_DIR / "resid_rev_v1.yaml")
CFG = CalibrationConfig.from_prereg(SPEC)
FACTORS = {s: "IDX:NIFTYMETAL" for s in SyntheticSpec().stocks}


@pytest.fixture(scope="module")
def store():
    return make_store()


@pytest.fixture(scope="module")
def provider(store):
    return CalibrationProvider(store, FACTORS, CFG)


# ------------------------------------------------------------------ config
def test_config_comes_from_the_prereg_without_defaults():
    assert CFG.beta_lookback == 60 and CFG.beta_n0 == 300 and CFG.beta_clip == (0.3, 2.5)
    assert CFG.resid_min_valid == 40 and CFG.rvol_lookback == 20 and CFG.rvol_min_valid == 15
    assert CFG.market_index == "IDX:NIFTY50" and CFG.market_pct == 80
    broken = {k: v for k, v in SPEC.items()}
    broken["calibration"] = {k: v for k, v in SPEC["calibration"].items() if k != "rvol"}
    with pytest.raises(KeyError):
        CalibrationConfig.from_prereg(broken)


# ------------------------------------------------------------------ known answers
def test_beta_recovered_and_shrunk(provider):
    d = sessions()[65]
    c = provider.symbol_calibration("AAA", d)
    assert c.valid and c.n_sessions == 60 and c.n_pairs == 60 * 23
    assert abs(c.beta_ols - 1.4) < 0.1
    w = c.n_pairs / (c.n_pairs + 300)
    assert math.isclose(c.beta, w * c.beta_ols + (1 - w) * 1.0, rel_tol=1e-12)
    low = provider.symbol_calibration("EEE", d)
    assert abs(low.beta_ols - 0.6) < 0.1


def test_shape_normalised_and_close_to_truth(provider):
    cal = provider.session(sessions()[65])
    assert cal.shape is not None
    assert math.isclose(float(np.mean(cal.shape[1:24])), 1.0, rel_tol=1e-12)
    assert np.corrcoef(cal.shape[1:24], TRUE_SHAPE[1:24])[0, 1] > 0.8


def test_rvol_known_answer():
    med = np.arange(1, 26, dtype=float) * 100.0          # median cum volume through slot t = 100*(t+1)
    vols = np.full(25, 100.0)
    vols[0] = 300.0
    v, why = tp.cum_rvol(vols, 3, med)                    # cum = 300+100*3 = 600 vs 400
    assert why == "OK" and math.isclose(v, 1.5)
    path = rvol_path(vols, med)
    assert math.isclose(path[3], 1.5) and math.isclose(path[0], 3.0)


def test_cumvol_medians_known_answer():
    rows = np.vstack([np.full(25, float(k)) for k in range(1, 21)])       # 20 sessions, volume k per slot
    med, why = tp.cumvol_medians(rows, 15)
    assert why == "OK" and math.isclose(med[0], 10.5) and math.isclose(med[4], 52.5)


def test_market_calibration_and_vix(provider, store):
    d = sessions()[65]
    m = provider.market_calibration(d)
    assert m.valid and m.n_sessions == 60
    # percentile recomputed by hand from the same prior sessions
    nh = provider.history("IDX:NIFTY50")
    rows = [i for i in nh.before(d, 60) if nh.complete[i]]
    RN = tp.returns_matrix(nh.closes[rows, :24])
    zm = np.abs(np.cumsum(RN[:, 1:24], axis=1) / np.sqrt(np.cumsum(m.s2N[1:24])))
    assert np.allclose(m.zm_pct[1:24], np.percentile(zm, 80, axis=0))
    vix = [b.close for b in store.daily_before("IDX:INDIAVIX", d)][-250:]
    assert m.vix_ref == float(np.median(vix)) and m.vix_reason == "OK"


def test_residual_paths_known_answer():
    closes = np.array([100.0] + [100.0 * math.exp(0.001 * k) for k in range(1, 24)] + [np.nan])
    fac = np.array([50.0] + [50.0 * math.exp(0.0005 * k) for k in range(1, 24)] + [np.nan])
    shape = np.array([np.nan] + [1.0] * 23)
    p = residual_paths(closes, fac, beta=2.0, s2=1e-6, shape=shape)
    assert np.allclose(p["e"][1:24], 0.0, atol=1e-15)              # r = 0.001 = 2 * 0.0005
    assert np.allclose(p["V"][1:24], 1e-6 * np.arange(1, 24))
    zm = market_path(fac, np.array([np.nan] + [1e-6] * 23))
    assert math.isclose(zm[4], (4 * 0.0005) / math.sqrt(4e-6), rel_tol=1e-9)


# ------------------------------------------------------------------ look-ahead
def test_lookahead_calibration_ignores_session_d_and_later():
    days = sessions()
    d = days[62]
    base = CalibrationProvider(make_store(), FACTORS, CFG).session(d)
    # change every close from session d onward for AAA and the factor
    over = {(s, i): {0: 1.3} for s in ("AAA", "IDX:NIFTYMETAL", "IDX:NIFTY50") for i in range(62, 70)}
    over.update({("VOL", "AAA", i): 5.0 for i in range(62, 70)})
    moved = CalibrationProvider(make_store(overrides=over), FACTORS, CFG).session(d)
    a, b = base.for_symbol("AAA"), moved.for_symbol("AAA")
    assert a.beta == b.beta and a.s2 == b.s2
    assert np.array_equal(a.s2px, b.s2px, equal_nan=True) and np.array_equal(a.cumvol_med, b.cumvol_med, equal_nan=True)
    assert np.array_equal(base.shape, moved.shape, equal_nan=True)
    assert np.array_equal(base.market.zm_pct, moved.market.zm_pct, equal_nan=True)
    # sanity: the change is visible one session later
    later = CalibrationProvider(make_store(overrides=over), FACTORS, CFG).symbol_calibration("AAA", days[64])
    assert later.s2 != CalibrationProvider(make_store(), FACTORS, CFG).symbol_calibration("AAA", days[64]).s2


def test_lookahead_paths_are_causal():
    rng = np.random.default_rng(3)
    for _ in range(25):
        closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, 25)))
        fac = 50 * np.exp(np.cumsum(rng.normal(0, 0.001, 25)))
        vols = rng.integers(1000, 5000, 25).astype(float)
        shape = np.array([np.nan] + list(rng.uniform(0.5, 2, 23)))
        med = np.cumsum(np.full(25, 3000.0))
        full = residual_paths(closes, fac, 1.2, 4e-6, shape)
        rv_full = rvol_path(vols, med)
        for t in range(1, 24):
            cut_c, cut_f, cut_v = closes.copy(), fac.copy(), vols.copy()
            cut_c[t + 1:], cut_f[t + 1:], cut_v[t + 1:] = np.nan, np.nan, -1
            part = residual_paths(cut_c, cut_f, 1.2, 4e-6, shape)
            for k in ("E", "V", "Z"):
                assert part[k][t] == full[k][t]
                if k != "V":                         # V depends on the calibration only
                    assert np.all(np.isnan(part[k][t + 1:]))
            assert rvol_path(cut_v, med)[t] == rv_full[t]


# ------------------------------------------------------------------ fail-closed
@pytest.mark.parametrize("bad", [np.nan, np.inf, -1.0, 0.0])
def test_returns_fail_closed_on_bad_prices(bad):
    c = np.full(25, 100.0)
    c[5] = bad
    r = tp.session_returns(c)
    assert np.isnan(r[5]) and np.isnan(r[6]) and np.isfinite(r[4])
    p = residual_paths(c, np.full(25, 50.0), 1.0, 1e-6, np.array([np.nan] + [1.0] * 23))
    assert np.all(np.isnan(p["Z"][5:])) and np.isfinite(p["Z"][4])


def test_fail_closed_calibration_inputs():
    assert tp.slot_variance(np.full((10, 24), 0.001), min_valid=40)[0] is None
    zero = np.zeros((50, 24))
    zero[:, 0] = np.nan
    assert tp.slot_variance(zero, min_valid=40) == (None, "ZERO_OR_INVALID_VARIANCE")
    assert tp.winsor_mean(np.array([np.nan, np.inf])) is None
    assert tp.cum_rvol(np.full(25, 10.0), 3, None) == (None, "NO_VOLUME_CALIBRATION")
    med = np.full(25, 0.0)
    assert tp.cum_rvol(np.full(25, 10.0), 3, med) == (None, "ZERO_DENOMINATOR")
    v = np.full(25, 10.0)
    v[2] = -1                                                   # missing volume at slot 2
    assert tp.cum_rvol(v, 3, np.full(25, 10.0)) == (None, "MISSING_VOLUME_TODAY")
    assert tp.normalise_shape(np.array([np.nan] + [0.0] * 23))[0] is None
    assert tp.diurnal_shape([None, np.array([np.nan] + [1.0] * 23)], min_symbols=5)[0] is None


def test_too_few_sessions_zero_volume_and_missing_slots_are_excluded():
    store = make_store(SyntheticSpec(n_sessions=45))
    prov = CalibrationProvider(store, FACTORS, CFG)
    last = sessions(SyntheticSpec(n_sessions=45))[-1]
    assert prov.symbol_calibration("AAA", last).valid                  # 44 prior sessions >= 40
    # make 5 prior sessions unusable: zero volume in one, a missing slot in the others
    days = sessions(SyntheticSpec(n_sessions=45))
    intr = {s: {d: store.bars(s, d) for d in store.sessions(s)} for s in store.symbols}
    daily = {s: store.daily(s) for s in store.symbols}
    from research.backtest.bars import Bar

    b0 = intr["AAA"][days[3]]
    intr["AAA"][days[3]] = [Bar(b.symbol, b.start, 15, b.open, b.high, b.low, b.close, 0 if k == 7 else b.volume)
                            for k, b in enumerate(b0)]
    for k in (5, 8, 11, 14):
        intr["AAA"][days[k]] = [b for j, b in enumerate(intr["AAA"][days[k]]) if j != 10]
    c = CalibrationProvider(CandleStore(intr, daily), FACTORS, CFG).symbol_calibration("AAA", last)
    assert not c.valid and c.reason == "INSUFFICIENT_SESSIONS_39_OF_40"


def test_factor_fallback_to_nifty_is_counted():
    store = make_store()
    intr = {s: {d: store.bars(s, d) for d in store.sessions(s)} for s in store.symbols}
    daily = {s: store.daily(s) for s in store.symbols}
    days = sessions()
    for k in range(10, 20):
        intr["IDX:NIFTYMETAL"].pop(days[k])
    c = CalibrationProvider(CandleStore(intr, daily), FACTORS, CFG).symbol_calibration("AAA", days[65])
    assert c.valid and c.factor_fallback_sessions == 10 - sum(1 for k in range(10, 20) if k < 5)


def test_kite_file_cannot_calibrate():
    from pathlib import Path

    kite = CandleStore.from_historical_json(Path(__file__).resolve().parents[2] / "shared" / "track2_liquid" / "historical_candles_track2.json")  # hash-pinned in test_p2_regressions
    prov = CalibrationProvider(kite, {}, CFG)
    last = kite.sessions("ANGELONE")[-1]
    c = prov.symbol_calibration("ANGELONE", last)
    assert not c.valid and c.reason.startswith("INSUFFICIENT_SESSIONS")


# ------------------------------------------------------------------ events
def test_events_unknown_is_none_and_table_is_point_in_time():
    from datetime import datetime, timedelta, timezone

    ist = timezone(timedelta(hours=5, minutes=30))
    t = lambda s: datetime.fromisoformat(s).replace(tzinfo=ist)
    none = NoEventsData()
    assert none.news_since("X", t("2026-09-01T15:30"), t("2026-09-02T10:00")) is None
    ev = TableEvents(announcements={"X": [t("2026-09-02T09:50")]},
                     scheduled={"X": [(t("2026-09-02T12:00"), date(2026, 9, 3))]},
                     covered=(t("2026-08-01T00:00"), t("2026-09-30T00:00")))
    assert ev.news_since("X", t("2026-09-01T15:30"), t("2026-09-02T10:00")) is True
    assert ev.news_since("X", t("2026-09-01T15:30"), t("2026-09-02T09:45")) is False     # not yet disseminated
    assert ev.scheduled_event("X", date(2026, 9, 3), t("2026-09-02T11:00")) is False      # not yet intimated
    assert ev.scheduled_event("X", date(2026, 9, 3), t("2026-09-02T13:00")) is True
    assert ev.news_since("X", t("2026-10-05T09:00"), t("2026-10-05T10:00")) is None       # outside coverage


def test_expiry_flag_weekday_switch():
    assert is_monthly_stock_expiry(date(2026, 9, 29))          # last Tuesday
    assert not is_monthly_stock_expiry(date(2026, 9, 24))
    assert is_monthly_stock_expiry(date(2025, 8, 28))          # last Thursday, before the switch
