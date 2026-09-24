import math

import pytest
from conftest import TICK, mk

from execution_realism.marketdata import (COUNTER_RESET, FIRST_OF_SESSION, GAP, FeedInvalid,
                                          FreshnessRegistry, VolumeDeltaTracker, schedule_tick,
                                          split_across_boundary, validate_snapshot)

U = frozenset({"ABC"})


def test_qty_at_distinguishes_empty_from_unobservable():
    s = mk(bids=((99.95, 100, 1), (99.90, 200, 2), (99.80, 300, 3), (99.75, 50, 1), (99.70, 10, 1)))
    assert s.qty_at("BID", 99.90, TICK) == 200
    assert s.qty_at("BID", 99.85, TICK) == 0          # inside displayed range, missing: empty
    assert s.qty_at("BID", 100.00, TICK) == 0         # better than best bid: nobody there
    assert s.qty_at("BID", 99.50, TICK) is None       # beyond 5 displayed levels: unknown
    thin = mk(bids=((99.95, 100, 1), (99.90, 200, 2)))
    assert thin.qty_at("BID", 99.50, TICK) == 0       # book shallower than the feed depth: all shown


@pytest.mark.parametrize("kw,msg", [
    (dict(ltp=0.0), "LTP"),
    (dict(ltp=float("nan")), "LTP"),
    (dict(ltp=100.02), "grid"),
    (dict(bids=((100.0, 10, 1),), asks=((100.0, 10, 1),)), "crossed"),
    (dict(bids=((99.95, 10, 0),)), "without orders"),
    (dict(symbol="XYZ"), "universe"),
])
def test_invalid_snapshots_raise(kw, msg):
    s = mk(validate=False, **kw)
    with pytest.raises(FeedInvalid, match=msg):
        validate_snapshot(s, now=1000.0, tick=TICK, max_age_s=2.0, universe=U)


def test_stale_snapshot_raises():
    s = mk(t=1000.0, validate=False)
    with pytest.raises(FeedInvalid, match="stale"):
        validate_snapshot(s, now=1003.0, tick=TICK, max_age_s=2.0, universe=U)


def test_registry_unknown_packet_cannot_refresh_a_stale_symbol():
    """Codex R09 probe: an unknown packet refreshed an hour-old quote."""
    reg = FreshnessRegistry(["ABC"], {"ABC": TICK}, max_age_s=2.0)
    reg.accept(mk(t=1000.0), now=1000.0)
    with pytest.raises(FeedInvalid):
        reg.accept(mk(symbol="ZZZ", t=4600.0, validate=False), now=4600.0)
    with pytest.raises(FeedInvalid, match="stale"):
        reg.latest("ABC", now=4600.0)
    with pytest.raises(FeedInvalid, match="incomplete"):
        reg.assert_complete(now=4600.0)
    assert reg.rejected == 1


def test_registry_rejects_backwards_statistics():
    reg = FreshnessRegistry(["ABC"], {"ABC": TICK})
    reg.accept(mk(seq=1, t=1000.0, lo=95.0), now=1000.0)
    with pytest.raises(FeedInvalid, match="shrank"):
        reg.accept(mk(seq=2, t=1001.0, lo=96.0), now=1001.0)


def test_volume_deltas_codex_r08_probe():
    """Counters 1000 then 1100: the new volume is 100, not 1100."""
    vt = VolumeDeltaTracker()
    a = vt.update(mk(seq=1, t=1000.0, cum=1000), "2026-09-24")
    b = vt.update(mk(seq=2, t=1001.0, cum=1100), "2026-09-24")
    assert a.delta is None and FIRST_OF_SESSION in a.flags
    assert b.delta == 100 and not b.flags
    c = vt.update(mk(seq=3, t=1002.0, cum=50), "2026-09-24")          # reconnect to a reset counter
    assert c.delta is None and COUNTER_RESET in c.flags
    d = vt.update(mk(seq=4, t=1010.0, cum=80), "2026-09-24")
    assert d.delta == 30 and GAP in d.flags


def test_bucket_split_uses_last_trade_time():
    e, l = split_across_boundary(500, 899.5, 900.5, 900.0, ltt_curr=899.8, ltq_curr=40)
    assert (e.lo, e.hi, l.lo, l.hi) == (500, 500, 0, 0)
    e, l = split_across_boundary(500, 899.5, 900.5, 900.0, ltt_curr=900.2, ltq_curr=40)
    assert (e.lo, e.hi, l.lo, l.hi) == (0, 460, 40, 500)


def test_tick_schedule_cross_check():
    assert schedule_tick(210.0) == 0.01
    assert schedule_tick(250.0) == 0.05
    assert schedule_tick(1000.0) == 0.05
    assert schedule_tick(1000.5) == 0.10
    assert schedule_tick(25000.0) == 5.00
    with pytest.raises(ValueError):
        schedule_tick(math.nan)
