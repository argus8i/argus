import math

import numpy as np
from conftest import TICK, ladder, mk

from execution_realism.exits import (SIEGMUND_BETA, DynamicBand, EmergencyExitSim, ExitParams, ExitState,
                                     StopLimitSell, StopState, TokenBucket, exit_cost_estimate,
                                     expected_discrete_overshoot, overnight_skip_probability, skip_events,
                                     skip_probability, wilson_interval)
from execution_realism.fills import Evidence
from execution_realism.marketdata import Session, VolumeDeltaTracker, floor_to_tick

T0 = 1_000_000.0
D = "2026-09-24"
END = T0 + 6 * 3600          # continuous-session end for the band's last-half-hour rule


def band(prev_close=100.0):
    return DynamicBand(prev_close=prev_close, tick=TICK, continuous_end_ts=END)


def test_band_ladder_matches_the_circulars():
    b = band()
    assert (b.lower, b.upper) == (90.0, 110.0)
    t = T0
    expected = [(95.0, 115.0, 15), (100.0, 120.0, 15), (103.0, 123.0, 30), (106.0, 126.0, 30), (108.0, 128.0, 60)]
    for lo, hi, cool_min in expected:
        assert b.on_trade(b.upper - 0.10, t) == "UP"
        assert b.pending_at - t == cool_min * 60
        assert b.resolve_pending(t + cool_min * 60 - 1, True) is None      # still cooling off
        assert b.resolve_pending(t + cool_min * 60, True) == (lo, hi)
        t += cool_min * 60 + 1
    assert b.flexes == 5


def test_band_last_half_hour_and_criteria_not_met():
    b = band()
    assert b.on_trade(90.10, END - 600) == "DOWN"
    assert b.pending_at == END - 600 + 300                                  # 5-minute cooling-off
    assert b.resolve_pending(END, False) == (90.0, 110.0) and b.flexes == 0
    assert b.adverse_mark_long() == 85.0


def _pair(p_kw, s_kw):
    p = mk(seq=0, t=T0, **p_kw)
    s = mk(seq=1, t=T0 + 1, **s_kw)
    vt = VolumeDeltaTracker()
    vt.update(p, D)
    return p, s, vt.update(s, D), vt


def test_stop_limit_fills_when_bids_exist_at_trigger():
    stop = StopLimitSell("ABC", 100, trigger=98.00, limit=floor_to_tick(98.00 * 0.995, TICK), tick=TICK)
    assert stop.limit == 97.50
    p, s, vd, _ = _pair(dict(ltp=98.50, bids=ladder(98.45, 0.05, 5), asks=((98.50, 500, 3),)),
                        dict(ltp=98.00, ltq=50, ltt=T0 + 0.9, cum=10_400, bids=ladder(97.95, 0.05, 5),
                             asks=((98.00, 500, 3),)))
    assert stop.on_snapshot(p, s, vd, band()) is StopState.FILLED
    assert stop.filled == 100 and stop.fills[0].price == 97.95


def test_stop_limit_gap_is_skipped_and_handed_off():
    stop = StopLimitSell("ABC", 100, trigger=98.00, limit=97.50, tick=TICK, skip_confirm_s=2.0)
    p, s, vd, vt = _pair(dict(ltp=98.50, bids=ladder(98.45, 0.05, 5), asks=((98.50, 500, 3),)),
                         dict(ltp=97.00, ltq=900, ltt=T0 + 0.9, cum=12_000, lo=90.0,
                              bids=ladder(96.95, 0.05, 5), asks=ladder(97.00, 0.05, 5, side="ASK")))
    b = band()
    assert stop.on_snapshot(p, s, vd, b) is StopState.RESTING and stop.filled == 0
    prev = s
    for k in (2, 3):
        nxt = mk(seq=k, t=T0 + k, ltp=96.90, ltq=10, ltt=T0 + k - 0.1, cum=12_000 + 100 * k,
                 bids=ladder(96.85, 0.05, 5), asks=ladder(96.90, 0.05, 5, side="ASK"))
        st = stop.on_snapshot(prev, nxt, vt.update(nxt, D), b)
        prev = nxt
    assert st is StopState.SKIPPED and stop.remaining == 100


def test_stop_limit_outside_slid_band_is_rejected_at_trigger():
    b = band()
    for _ in range(2):                                   # two up-flexes: band slides to 100-120
        b.on_trade(b.upper - 0.10, T0)
        b.resolve_pending(T0 + 3600, True)
    assert (b.lower, b.upper) == (100.0, 120.0)
    stop = StopLimitSell("ABC", 100, trigger=100.00, limit=99.50, tick=TICK)
    p, s, vd, _ = _pair(dict(ltp=100.50, lo=100.0, hi=119.0, bids=ladder(100.45, 0.05, 5), asks=((100.50, 5, 1),)),
                        dict(ltp=100.00, ltq=5, ltt=T0 + 0.9, lo=100.0, hi=119.0, cum=10_100,
                             bids=ladder(100.00, 0.05, 1), asks=((100.05, 5, 1),)))
    assert stop.on_snapshot(p, s, vd, b) is StopState.REJECTED_AT_TRIGGER


def test_stop_purged_by_halt_and_expired_at_continuous_close():
    for session, expected in ((Session.HALTED, StopState.PURGED),
                              (Session.CLOSING_AUCTION, StopState.EXPIRED_AT_CONTINUOUS_CLOSE)):
        stop = StopLimitSell("ABC", 100, trigger=98.0, limit=97.5, tick=TICK)
        p, s, vd, _ = _pair({}, dict(session=session))
        assert stop.on_snapshot(p, s, vd, band()) is expected


def _exit(position=200, protective=200, **kw):
    return EmergencyExitSim("ABC", position, TICK, band(), T0, protective_open_qty=protective,
                            params=ExitParams(**kw), limiter=TokenBucket())


def test_exit_waits_for_protective_cancel_ack_then_works_the_book():
    ex = _exit()
    vt = VolumeDeltaTracker()
    s0 = mk(seq=0, t=T0 + 0.2, ltp=99.0, bids=ladder(98.95, 0.05, 5, qty=200), asks=((99.00, 300, 2),))
    vt.update(s0, D)
    assert ex.on_snapshot(s0, s0, vt.update(s0, D), now=T0 + 0.2) is ExitState.CANCELLING_PROTECTIVE
    assert ex.attempts == 0                                  # nothing sent: no double-sell window
    s1 = mk(seq=1, t=T0 + 1.0, ltp=99.0, bids=ladder(98.95, 0.05, 5, qty=200), asks=((99.00, 300, 2),))
    ex.on_snapshot(s0, s1, vt.update(s1, D), now=T0 + 1.0)
    assert ex.attempts == 1 and ex.sold == 0                 # IOC in flight
    s2 = mk(seq=2, t=T0 + 2.0, ltp=99.0, bids=ladder(98.95, 0.05, 5, qty=200), asks=((99.00, 300, 2),))
    st = ex.on_snapshot(s1, s2, vt.update(s2, D), now=T0 + 2.0)
    # haircut depth: 100 + 60 + 60 + 60 + 60 = 340 available inside the 50 bps collar floor (98.50)
    assert st is ExitState.FILLED and ex.sold == 200
    assert all(f.price >= 98.50 for f in ex.fills)


def test_exit_never_declared_flat_without_fills_and_marks_conservatively():
    ex = _exit(protective=0)
    vt = VolumeDeltaTracker()
    s0 = mk(seq=0, t=T0, ltp=99.0, bids=((98.95, 20, 1),), asks=((99.00, 300, 2),))
    vt.update(s0, D)
    ex.on_snapshot(s0, s0, vt.update(s0, D), now=T0)
    s1 = mk(seq=1, t=T0 + 1, ltp=99.0, bids=((98.95, 20, 1),), asks=((99.00, 300, 2),))
    st = ex.on_snapshot(s0, s1, vt.update(s1, D), now=T0 + 1)
    assert st is ExitState.PARTIAL and ex.sold == 10 and ex.remaining == 190
    mark = ex.mark_to_market(s1)
    assert mark < 98.95 and mark != 100.0                   # never the entry price (Codex R05)


def test_exit_queues_at_the_lower_band_and_fills_only_on_recovery():
    ex = _exit(protective=0)
    vt = VolumeDeltaTracker()
    pinned = dict(ltp=90.0, lo=90.0, bids=(), asks=((90.00, 50_000, 400),))
    s0 = mk(seq=0, t=T0, **pinned)
    vt.update(s0, D)
    assert ex.on_snapshot(s0, s0, vt.update(s0, D), now=T0) is ExitState.QUEUED_AT_BAND
    s1 = mk(seq=1, t=T0 + 1, cum=10_050, ltq=50, ltt=T0 + 0.9, **pinned)
    assert ex.on_snapshot(s0, s1, vt.update(s1, D), now=T0 + 1) is ExitState.QUEUED_AT_BAND and ex.sold == 0
    s2 = mk(seq=2, t=T0 + 2, ltp=90.10, lo=90.0, cum=90_000, ltq=500, ltt=T0 + 1.9,
            bids=((90.05, 500, 3),), asks=((90.10, 900, 4),))
    assert ex.on_snapshot(s1, s2, vt.update(s2, D), now=T0 + 2) is ExitState.FILLED
    assert ex.fills[-1].evidence is Evidence.TRADE_THROUGH and ex.fills[-1].price == 90.0


def test_exit_purged_by_halt_then_rearmed():
    ex = _exit(protective=0)
    vt = VolumeDeltaTracker()
    s0 = mk(seq=0, t=T0, ltp=95.0, bids=((94.95, 10, 1),), asks=((95.00, 10, 1),))
    vt.update(s0, D)
    ex.on_snapshot(s0, s0, vt.update(s0, D), now=T0)
    s1 = mk(seq=1, t=T0 + 1, ltp=95.0, session=Session.HALTED, bids=(), asks=())
    assert ex.on_snapshot(s0, s1, vt.update(s1, D), now=T0 + 1) is ExitState.PURGED
    s2 = mk(seq=2, t=T0 + 2700, ltp=93.0, bids=ladder(92.95, 0.05, 5, qty=1000), asks=((93.00, 500, 2),))
    ex.on_snapshot(s1, s2, vt.update(s2, D), now=T0 + 2700)
    assert ("REARM_AFTER_HALT" in [m for _, m in ex.log]) and ex.attempts == 2


def test_exit_escalates_after_repeated_broker_rejects():
    ex = _exit(protective=0, max_rejects=2, retry_interval_s=0.0)
    vt = VolumeDeltaTracker()
    prev = mk(seq=0, t=T0, ltp=99.0, bids=ladder(98.95, 0.05, 5), asks=((99.00, 300, 2),))
    vt.update(prev, D)
    ex.on_snapshot(prev, prev, vt.update(prev, D), now=T0)
    st = None
    for k in range(1, 12):
        s = mk(seq=k, t=T0 + k, ltp=99.0, bids=ladder(98.95, 0.05, 5), asks=((99.00, 300, 2),))
        st = ex.on_snapshot(prev, s, vt.update(s, D), now=T0 + k, broker_reject="RMS:429")
        prev = s
        if st is ExitState.FAILED_ESCALATE:
            break
    assert st is ExitState.FAILED_ESCALATE and ex.sold == 0


def test_exit_after_continuous_close_goes_to_the_auction():
    ex = _exit(protective=0)
    vt = VolumeDeltaTracker()
    s0 = mk(seq=0, t=T0, ltp=99.0)
    vt.update(s0, D)
    s1 = mk(seq=1, t=T0 + 1, ltp=99.0, session=Session.CLOSING_AUCTION)
    assert ex.on_snapshot(s0, s1, vt.update(s1, D), now=T0 + 1) is ExitState.CAS_PENDING
    s2 = mk(seq=2, t=T0 + 1200, ltp=98.6, session=Session.CLOSING_AUCTION)
    assert ex.on_snapshot(s1, s2, vt.update(s2, D), now=T0 + 1200, cas_price=98.6) is ExitState.FILLED
    assert ex.fills[-1].evidence is Evidence.AUCTION


def test_rs60k_exit_cost_is_a_few_bps():
    s = mk(ltp=500.0, hi=505.0, lo=495.0, bids=ladder(499.95, 0.05, 5, qty=500), asks=((500.00, 500, 3),))
    c = exit_cost_estimate(120, s, sigma_daily=0.025, adv_shares=30e7 / 500, sigma_1s=0.0005, latency_s=0.5)
    assert c["residual_qty"] == 0 and c["book_bps"] < 1.0
    assert abs(c["sqrt_law_whole_order_bps"] - 3.54) < 0.01
    assert c["latency_bps"] > c["book_bps"]


def test_siegmund_overshoot_constant_by_monte_carlo():
    rng = np.random.default_rng(7)
    n, b, steps = 40_000, 6.0, 3000
    x = np.zeros(n)
    over = np.full(n, np.nan)
    alive = np.ones(n, dtype=bool)
    for _ in range(steps):
        x[alive] += rng.standard_normal(alive.sum()) - 0.02
        hit = alive & (x <= -b)
        over[hit] = -b - x[hit]
        alive &= ~hit
        if not alive.any():
            break
    m = np.nanmean(over)
    assert abs(m - SIEGMUND_BETA) < 0.03
    assert math.isclose(expected_discrete_overshoot(0.0002, 1.0), SIEGMUND_BETA * 0.0002)


def test_skip_estimators():
    path = [mk(seq=0, t=T0, ltp=100.0, bids=ladder(99.95, 0.05, 5), asks=((100.00, 100, 1),))]
    path.append(mk(seq=1, t=T0 + 1, ltp=99.80, bids=ladder(99.75, 0.05, 5), asks=((99.80, 100, 1),)))
    path.append(mk(seq=2, t=T0 + 2, ltp=100.10, bids=ladder(100.05, 0.05, 5), asks=((100.10, 100, 1),)))
    path.append(mk(seq=3, t=T0 + 3, ltp=98.00, bids=ladder(97.95, 0.05, 5), asks=((98.00, 100, 1),)))
    ev = skip_events(path, trigger=99.90, theta=0.005)
    assert [e["skipped"] for e in ev] == [0.0, 1.0]
    sp = skip_probability(ev)
    assert sp["n"] == 2 and sp["k"] == 1 and sp["lo95"] < 0.5 < sp["hi95"]
    lo, hi = wilson_interval(0, 200)
    assert lo == 0.0 and 0.018 < hi < 0.02
    o = overnight_skip_probability([-0.05, -0.01, 0.02, -0.03, 0.0], stop_distance=0.02, theta=0.005)
    assert o["k"] == 2 and math.isclose(o["gap_threshold"], 0.98 * 0.995 - 1)
