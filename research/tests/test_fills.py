import random

import pytest
from conftest import TICK, ladder, mk

from execution_realism.fills import (CENT, OPT, PESS, Evidence, FillState, PassiveOrderSim, SelfConsumption,
                                     Side, atp_interval_vwap_error_bound, resolve_bar_exit, taker_fill)
from execution_realism.marketdata import Session, VolumeDeltaTracker

D = "2026-09-24"
T0 = 1_000_000.0


def run(order, snaps):
    vt = VolumeDeltaTracker()
    vt.update(snaps[0], D)
    order.join(snaps[0])
    for s in snaps[1:]:
        order.on_snapshot(s, vt.update(s, D))
    return order


def test_touch_is_not_a_fill_codex_r06():
    """LTP touches the target and falls back: the old engine booked the profit."""
    bids = ladder(100.50, 0.05, 5)
    asks = ((100.55, 500, 3), (100.70, 500, 3), (100.80, 500, 3), (100.90, 500, 3), (101.00, 2000, 9))
    s0 = mk(seq=0, t=T0 + 0.0, ltp=100.50, bids=bids, asks=asks, cum=10_000, hi=100.60)
    s1 = mk(seq=1, t=T0 + 1.0, ltp=101.00, ltq=50, ltt=T0 + 0.9, cum=10_300, hi=101.00,
            bids=ladder(100.95, 0.05, 5), asks=((101.00, 1950, 9), (101.05, 300, 2)))
    s2 = mk(seq=2, t=T0 + 2.0, ltp=100.60, ltq=10, ltt=T0 + 1.9, cum=10_400, hi=101.00,
            bids=ladder(100.60, 0.05, 5), asks=((100.65, 300, 2), (101.00, 1950, 9)))
    o = run(PassiveOrderSim("tp", Side.SELL, 101.00, 100, TICK), [s0, s1, s2])
    assert o.filled[PESS] == 0 and o.state[PESS] is FillState.QUEUED
    assert o.filled[CENT] == 0 and o.filled[OPT] == 0      # 2,000 shares were ahead at join


def test_trade_through_proves_the_fill():
    s0 = mk(seq=0, t=T0 + 0.0, ltp=100.50, bids=ladder(100.50, 0.05, 5),
            asks=((100.55, 500, 3), (101.00, 2000, 9)), hi=100.60)
    s1 = mk(seq=1, t=T0 + 1.0, ltp=101.05, ltq=100, ltt=T0 + 0.9, cum=10_900, hi=101.05,
            bids=ladder(101.00, 0.05, 5), asks=((101.10, 400, 3),))
    o = run(PassiveOrderSim("tp", Side.SELL, 101.00, 100, TICK), [s0, s1])
    assert all(o.filled[e] == 100 for e in (PESS, CENT, OPT))
    assert o.events[0].evidence is Evidence.TRADE_THROUGH and o.events[0].price == 101.00


def test_quote_through_proves_the_fill():
    s0 = mk(seq=0, t=T0 + 0.0, bids=ladder(99.95, 0.05, 5))
    s1 = mk(seq=1, t=T0 + 1.0, ltp=99.95, bids=((99.90, 400, 2),), asks=((99.95, 300, 2),))
    o = run(PassiveOrderSim("b", Side.BUY, 99.95, 50, TICK), [s0, s1])
    assert o.filled[PESS] == 50 and o.events[0].evidence is Evidence.QUOTE_THROUGH


def test_old_day_low_is_not_a_trade_through():
    """Research-report bug: day_low < price was treated as a fill even when that low
    printed before the order existed."""
    s0 = mk(seq=0, t=T0 + 0.0, lo=99.00, bids=ladder(99.95, 0.05, 5))
    s1 = mk(seq=1, t=T0 + 1.0, lo=99.00, bids=ladder(99.95, 0.05, 5))
    o = run(PassiveOrderSim("b", Side.BUY, 99.95, 50, TICK), [s0, s1])
    assert o.filled[PESS] == 0 and o.filled[OPT] == 0


def test_level_beyond_visible_depth_is_frozen_not_front_of_queue():
    """Research-report bug: an invisible level read as quantity 0, i.e. front of queue."""
    s0 = mk(seq=0, t=T0 + 0.0, bids=ladder(99.95, 0.05, 5))                 # 99.95 .. 99.75 visible
    o = PassiveOrderSim("b", Side.BUY, 99.50, 100, TICK)
    vt = VolumeDeltaTracker()
    vt.update(s0, D)
    o.join(s0)
    assert o.rank[PESS] is None
    s1 = mk(seq=1, t=T0 + 1.0, ltp=100.0, ltq=10, ltt=T0 + 0.9, cum=10_500, bids=ladder(99.95, 0.05, 5))
    o.on_snapshot(s1, vt.update(s1, D))
    assert o.rank[PESS] is None and o.frozen_intervals == 1 and o.filled[OPT] == 0
    s2 = mk(seq=2, t=T0 + 2.0, ltp=99.70, ltq=10, ltt=T0 + 1.9, cum=11_000,
            bids=((99.65, 100, 1), (99.60, 100, 1), (99.55, 100, 1), (99.50, 3000, 12), (99.45, 100, 1)),
            asks=((99.70, 500, 3),))
    o.on_snapshot(s2, vt.update(s2, D))
    assert (o.rank[PESS], o.rank[CENT], o.rank[OPT]) == (3000.0, 1500.0, 0.0)


def test_queue_depletion_orders_the_envelopes():
    base_ask = ((100.00, 800, 4),)
    path = [mk(seq=0, t=T0 + 0.0, ltp=99.95, bids=((99.95, 1000, 5),), asks=base_ask, cum=10_000)]
    levels = [700, 300, 50, 20, 20]
    cum, t = 10_000, T0
    for i, q in enumerate(levels, start=1):
        t += 1.0
        cum += 300
        path.append(mk(seq=i, t=t, ltp=99.95, ltq=100, ltt=t - 0.1, cum=cum,
                       bids=((99.95, q, 3), (99.90, 500, 2)), asks=base_ask))
    o = PassiveOrderSim("b", Side.BUY, 99.95, 100, TICK)
    vt = VolumeDeltaTracker()
    vt.update(path[0], D)
    o.join(path[0])
    first_fill = {}
    for s in path[1:]:
        o.on_snapshot(s, vt.update(s, D))
        for e in (PESS, CENT, OPT):
            if o.filled[e] and e not in first_fill:
                first_fill[e] = s.seq
        assert o.filled[PESS] <= o.filled[CENT] <= o.filled[OPT]
    assert first_fill[OPT] <= first_fill[CENT] <= first_fill[PESS]
    assert o.filled[PESS] == 100


def test_halt_purges_orders():
    s0 = mk(seq=0, t=T0 + 0.0)
    s1 = mk(seq=1, t=T0 + 1.0, session=Session.HALTED)
    o = run(PassiveOrderSim("b", Side.BUY, 99.95, 10, TICK), [s0, s1])
    assert o.terminal is FillState.PURGED and o.state[PESS] is FillState.PURGED


def test_cancel_race_honours_fills_evidenced_before_the_ack():
    s0 = mk(seq=0, t=T0 + 0.0, bids=ladder(99.95, 0.05, 5))
    s1 = mk(seq=1, t=T0 + 1.0, ltp=99.90, ltq=500, ltt=T0 + 0.95, cum=10_600, lo=89.0, bids=ladder(99.85, 0.05, 5),
            asks=((99.90, 100, 1),))
    o = PassiveOrderSim("b", Side.BUY, 99.95, 50, TICK)
    vt = VolumeDeltaTracker()
    vt.update(s0, D)
    o.join(s0)
    o.request_cancel(t_request=T0 + 0.2, ack_latency_s=0.5)
    o.on_snapshot(s1, vt.update(s1, D))
    assert o.filled[PESS] == 50 and o.state[PESS] is FillState.FILLED and o.terminal is FillState.CANCELLED


def _random_path(rng, n=60):
    """Random but valid snapshot path around a resting buy at 99.95."""
    path, cum, t, lo, q = [], 50_000, T0, 99.50, 2000
    path.append(mk(seq=0, t=t, ltp=100.0, bids=((99.95, q, 8), (99.90, 900, 4)), asks=((100.00, 900, 4),),
                   cum=cum, lo=lo))
    for i in range(1, n):
        t += 1.0
        dv = rng.choice([0, 0, rng.randint(1, 800)])
        cum += dv
        q = max(1, q + rng.randint(-600, 300))
        ltp = rng.choice([99.95, 100.00]) if dv else path[-1].ltp
        ltq = rng.randint(1, max(1, dv)) if dv else None
        path.append(mk(seq=i, t=t, ltp=ltp, ltq=ltq, ltt=(t - 0.1) if dv else path[-1].ltt, cum=cum,
                       lo=lo, bids=((99.95, q, 5), (99.90, 900, 4)), asks=((100.00, rng.randint(100, 2000), 4),)))
    return path


@pytest.mark.parametrize("seed", range(100))
def test_envelope_ordering_property(seed):
    rng = random.Random(seed)
    path = _random_path(rng)
    o = PassiveOrderSim("b", Side.BUY, 99.95, rng.randint(50, 1500), TICK)
    vt = VolumeDeltaTracker()
    vt.update(path[0], D)
    o.join(path[0])
    for s in path[1:]:
        o.on_snapshot(s, vt.update(s, D))
        assert o.filled[PESS] <= o.filled[CENT] <= o.filled[OPT]
        ranks = [o.rank[e] for e in (PESS, CENT, OPT)]
        assert ranks[0] + 1e-9 >= ranks[1] >= ranks[2] - 1e-9
        assert all(0 <= r <= s.qty_at("BID", 99.95, TICK) + 1e-9 for r in ranks)


def test_taker_fill_haircuts_bracketing_and_memory():
    before = mk(seq=0, t=T0 + 0.0, ltp=100.0, asks=((100.00, 1000, 5), (100.05, 1000, 5), (100.10, 1000, 5)))
    after = mk(seq=1, t=T0 + 1.0, ltp=100.0, asks=((100.05, 1000, 5), (100.10, 1000, 5)))
    mem = {e: SelfConsumption() for e in (PESS, CENT, OPT)}
    r = taker_fill(Side.BUY, 900, 100.10, TICK, before, after, T0 + 0.5, memory=mem)
    # PESS: 100.00 absent after -> 0; 100.05: min(0.5*1000 after-level0, 0.3*1000 before-level1)=300; 100.10: 300
    assert r.filled[PESS] == 600 and r.status[PESS] == "PARTIAL"
    assert r.filled[CENT] == 800 and r.filled[OPT] == 900
    r2 = taker_fill(Side.BUY, 900, 100.10, TICK, before, after, T0 + 0.6, memory=mem)
    assert r2.filled[PESS] == 0          # the same displayed depth cannot be consumed twice


def test_taker_collar_breach_and_lock():
    before = mk(seq=0, t=T0 + 0.0, ltp=100.0)
    runaway = mk(seq=1, t=T0 + 1.0, ltp=100.40, hi=100.40, bids=((100.35, 500, 3),), asks=((100.40, 500, 3),))
    r = taker_fill(Side.BUY, 100, 100.15, TICK, before, runaway, T0 + 0.5)
    assert r.filled[PESS] == 0 and r.status[PESS] == "NO_LIQUIDITY_WITHIN_LIMIT"
    locked = mk(seq=1, t=T0 + 1.0, ltp=100.0, asks=())
    assert taker_fill(Side.BUY, 100, 100.15, TICK, before, locked, T0 + 0.5).status[CENT] == "LOCKED"


def test_bar_replay_never_invents_event_order():
    r = resolve_bar_exit(high=103.0, low=97.0, stop_trigger=98.0, stop_limit=97.5, target=102.0, tick=TICK)
    assert r["flag"] == "AMBIGUOUS_ORDER" and r["pessimistic"] == "STOP_SKIP_POSSIBLE"
    r = resolve_bar_exit(high=102.0, low=99.0, stop_trigger=98.0, stop_limit=97.5, target=102.0, tick=TICK)
    assert r == {"pessimistic": "NO_FILL", "optimistic": "TARGET", "flag": "TOUCH_ONLY"}


def test_atp_decomposition_is_numerically_useless_after_the_open():
    """The research report called p_bar = (ATP_t V_t - ATP_t-1 V_t-1)/dV 'exactly recoverable'.
    With ATP quantised to the paisa the error bound is 0.01 (V_t + V_t-1) / dV."""
    early = atp_interval_vwap_error_bound(20_000, 21_000)          # 09:15:05-ish
    later = atp_interval_vwap_error_bound(300_000, 301_000)        # 09:31
    assert early > 0.40 and later > 6.0                           # 8 ticks and 120 ticks at a 0.05 tick
    rng = random.Random(1)
    v, notional, errs = 0, 0.0, []
    prev_atp_q = prev_v = None
    for _ in range(400):
        q = rng.randint(200, 1500)
        px = rng.choice([99.95, 100.00])
        v += q
        notional += q * px
        atp_q = round(notional / v, 2)                             # what the feed disseminates
        if prev_v and v > 100_000:
            pbar = (atp_q * v - prev_atp_q * prev_v) / (v - prev_v)
            errs.append(abs(pbar - px))
        prev_atp_q, prev_v = atp_q, v
    assert max(errs) > 1.0                                         # far wider than the 0.05 spread


def test_fill_probability_matches_simulation():
    import numpy as np
    from execution_realism.fills import fill_probability
    rng = np.random.default_rng(3)
    ahead, qty, H, lam, kappa = 5000, 500, 120.0, 0.5, 0.004
    sizes = lambda n: rng.geometric(1 / 60.0, size=n)            # mean 60 shares per print
    m, m2 = 60.0, 2 * 60.0 ** 2 - 60.0                            # geometric: E[s^2] = (2-p)/p^2
    hits, n = 0, 20_000
    for _ in range(n):
        k = rng.poisson(lam * H)
        v = sizes(k).sum() if k else 0
        hits += v >= ahead * np.exp(-kappa * H) + qty
    p_mc = hits / n
    p = fill_probability(ahead, qty, H, trade_rate_per_s=lam, mean_trade_size=m, second_moment_size=m2,
                         cancel_rate_per_s=kappa)
    assert abs(p - p_mc) < 0.03 and 0.05 < p < 0.95
