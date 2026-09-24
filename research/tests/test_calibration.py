import math
import random

from conftest import mk

from execution_realism.calibration import cancel_intensity, depth_persistence, p_cancel

T0 = 1_000_000.0


def _path(kappa, steps, adds, seed=11):
    rng = random.Random(seed)
    q, path = 5000, []
    for k in range(steps):
        path.append(mk(seq=k, t=T0 + k, bids=((99.95, max(q, 1), 10), (99.90, 3000, 8)), asks=((100.00, 800, 4),)))
        cancelled = sum(1 for _ in range(q) if rng.random() < 1 - math.exp(-kappa))
        q = max(1, q - cancelled + (rng.randint(0, 2 * int(q * kappa) + 1) if adds else 0))
    return path


def test_cancel_intensity_is_exact_without_same_interval_adds():
    est = cancel_intensity(_path(0.001, 2000, adds=False), "BID", 0.05)
    assert abs(est[0]["kappa_per_s"] - 0.001) < 0.0002
    assert est[1]["kappa_per_s"] == 0.0
    assert math.isclose(p_cancel(0.02, 10), 1 - math.exp(-0.2))


def test_cancel_intensity_is_only_a_lower_bound_when_adds_mask_cancels():
    """Snapshots show net flow: adds and cancels in the same interval cancel out.
    MBP data bounds kappa from below; only order-level data identifies it."""
    est = cancel_intensity(_path(0.02, 2000, adds=True), "BID", 0.05)
    assert 0 < est[0]["kappa_per_s"] < 0.5 * 0.02


def test_intervals_with_trades_are_excluded():
    a = mk(seq=0, t=T0, cum=10_000, bids=((99.95, 1000, 5),))
    b = mk(seq=1, t=T0 + 1, cum=10_500, bids=((99.95, 400, 3),))
    est = cancel_intensity([a, b], "BID", 0.05)
    assert est[0]["intervals"] == 0 and math.isnan(est[0]["kappa_per_s"])


def test_depth_persistence():
    path = [mk(seq=k, t=T0 + k, bids=((99.95, 1000 if k % 2 == 0 else 400, 5),)) for k in range(10)]
    h = depth_persistence(path, "BID", 0.05, lag_s=1.0)
    assert math.isclose(h[0], (5 * 0.4 + 4 * 1.0) / 9)
