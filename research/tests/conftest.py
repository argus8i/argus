import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from execution_realism.marketdata import Level, Session, Snapshot, validate_snapshot  # noqa: E402

TICK = 0.05


def mk(symbol="ABC", seq=0, t=1000.0, ltp=100.0, bids=((99.95, 1000, 5),), asks=((100.0, 800, 4),),
       cum=10_000, hi=110.0, lo=90.0, ltq=None, ltt=None, session=Session.CONTINUOUS, atp=None,
       depth=5, tick=TICK, validate=True):
    s = Snapshot(symbol=symbol, source="REPLAY", seq=seq, recv_ts=t, session=session, ltp=ltp,
                 cum_volume=cum, day_high=hi, day_low=lo,
                 bids=tuple(Level(p, q, o) for p, q, o in bids),
                 asks=tuple(Level(p, q, o) for p, q, o in asks),
                 depth_levels=depth, ltt=ltt, ltq=ltq, atp=atp)
    if validate:
        validate_snapshot(s, now=t, tick=tick, max_age_s=2.0, universe=frozenset({symbol}))
    return s


def ladder(best, step, n, qty=1000, side="BID"):
    out = []
    for k in range(n):
        p = round(best - k * step, 2) if side == "BID" else round(best + k * step, 2)
        out.append((p, qty, 5))
    return tuple(out)
