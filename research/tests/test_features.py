import math
import random

from conftest import mk

from execution_realism.features import (absorption_at_level, fit_ev_model, ofi, speed_atr, spread_atr,
                                        vwap_extension_atr, winners_curse)

T0 = 1_000_000.0


def test_ofi_matches_hand_computation():
    p = mk(seq=0, t=T0, bids=((100.00, 500, 3),), asks=((100.05, 400, 2),))
    s = mk(seq=1, t=T0 + 1, bids=((100.00, 700, 4),), asks=((100.05, 400, 2),))
    assert ofi([p, s]) == 700 - 500 - 400 + 400
    u = mk(seq=2, t=T0 + 2, bids=((100.05, 300, 2),), asks=((100.10, 600, 3),))
    assert ofi([s, u]) == 300 + 400


def test_absorption_flags_a_refilling_offer():
    path, cum = [], 10_000
    for k in range(8):
        cum += 400
        path.append(mk(seq=k, t=T0 + k, ltp=100.05, ltq=300, ltt=T0 + k - 0.1, cum=cum,
                       bids=((100.00, 500, 3),), asks=((100.05, 1000, 6),)))
    iceberg = absorption_at_level(path, 100.05, 0.05)
    assert iceberg["certain_traded"] == 7 * 300 and iceberg["displayed_depletion"] == 0
    assert math.isinf(iceberg["absorption_ratio"])
    path, cum, q = [], 10_000, 3000
    for k in range(8):
        cum += 400
        q -= 300
        path.append(mk(seq=k, t=T0 + k, ltp=100.05, ltq=300, ltt=T0 + k - 0.1, cum=cum,
                       bids=((100.00, 500, 3),), asks=((100.05, q, 6),)))
    honest = absorption_at_level(path, 100.05, 0.05)
    assert honest["absorption_ratio"] == 1.0


def test_price_features():
    s = mk(ltp=101.0, atp=100.2, bids=((100.95, 10, 1),), asks=((101.00, 10, 1),))
    assert math.isclose(spread_atr(s, 2.5), 0.02)
    assert math.isclose(vwap_extension_atr(s, 2.0), 0.4)
    path = [mk(seq=k, t=T0 + k, ltp=100.0 + 0.1 * k) for k in range(12)]
    assert math.isclose(speed_atr(path, 2.0, window_s=10), (101.1 - 100.1) / 2.0)


def test_winners_curse_detects_adverse_fill_selection():
    rng = random.Random(3)
    recs = []
    for _ in range(3000):
        r = rng.gauss(0.2, 1.0)
        runaway = r > 1.0 and rng.random() < 0.8        # strong runners leave the collar behind
        recs.append({"disposition": "COLLAR_ABORT" if runaway else "FILLED", "r_signal": r})
    out = winners_curse(recs, n_boot=600)
    assert out["curse"] < 0 and out["ci95_hi"] < 0
    assert math.isclose(out["curse"], out["curse_identity"], rel_tol=1e-9)
    assert out["mean_COLLAR_ABORT"] > out["mean_FILLED"]


def test_ev_fit_calibration_gate():
    rng = random.Random(5)
    rows, y = [], []
    for _ in range(400):
        a, b = rng.gauss(0, 1), rng.gauss(0, 1)
        rows.append({"a": a, "b": b})
        y.append(0.1 + 0.5 * a - 0.3 * b + rng.gauss(0, 0.5))
    m = fit_ev_model(rows[:300], y[:300], ["a", "b"], lam=1.0, trained_through="t",
                     oos_rows=rows[300:], oos_y=y[300:])
    assert m.weights["a"] > 0.4 and m.weights["b"] < -0.2 and m.oos_r2 > 0.3 and m.calibrated
    thin = fit_ev_model(rows[:60], y[:60], ["a", "b"], lam=1.0, trained_through="t")
    assert not thin.calibrated
