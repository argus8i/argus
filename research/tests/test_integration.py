"""09:30 batch, end to end: surveillance gate -> session -> batch admission -> IOC entries
-> an emergency exit that closes only on evidenced fills."""
import json

from conftest import ladder, mk
from test_surveillance import NOW, TD, build

from execution_realism.capacity import Candidate, CapacityConfig, ReservationLedger, ResState, size_position
from execution_realism.exits import DynamicBand, EmergencyExitSim, ExitParams, ExitState, TokenBucket
from execution_realism.fills import PESS, Side, taker_fill
from execution_realism.marketdata import FreshnessRegistry, VolumeDeltaTracker
from execution_realism.surveillance import premarket_gate, verdict

T0 = 1_000_000.0
TICK = 0.05


def test_0930_batch_end_to_end(tmp_path):
    snap = build()
    path = tmp_path / "nse_surveillance_snapshot_2026-09-24.json"
    path.write_text(json.dumps(snap))
    snap = premarket_gate(path, TD, NOW)
    lg = ReservationLedger(tmp_path / "ledger.db", CapacityConfig())
    gate = lg.open_session(TD.isoformat(), snap["sha256"], now=T0)

    names = ["FNOSTOCK001", "FNOSTOCK002", "FNOSTOCK003", "FNOSTOCK004", "FNOSTOCK009"]
    eligible = [s for s in names if verdict(s, snap).eligible]
    assert "FNOSTOCK009" not in eligible                       # in LT-ASM: never reaches arbitration

    cands = []
    for k, sym in enumerate(eligible):
        sz = size_position(limit_px=100.15, stop_trigger=98.00, tick=TICK, cfg=lg.config)
        cands.append(Candidate(f"I-{sym}", sym, f"SEC{k}", sz.qty, 100.15, sz.stop_limit_px, T0 + 30,
                               initial_state=ResState.APPROVED, margin_rate=0.2,
                               features={"spread_atr": 0.02 + 0.01 * k, "speed_atr": 0.3, "rel_strength": 0.01}))
    res = lg.admit_batch(cands, now=T0, session_gate=gate, batch_id="0930")
    admitted = [r.intent_id for r in res if r.ok]
    assert len(admitted) == 3 and lg.usage().slots == 3

    reg = FreshnessRegistry(eligible, {s: TICK for s in eligible})
    books = {  # arrival books 0.3 s after routing
        "FNOSTOCK001": dict(ltp=100.05, bids=((100.00, 900, 4),), asks=((100.05, 2000, 9), (100.10, 2000, 9))),
        "FNOSTOCK002": dict(ltp=100.60, hi=100.60, bids=((100.55, 900, 4),), asks=((100.60, 900, 4),)),
        "FNOSTOCK003": dict(ltp=100.10, bids=((100.05, 900, 4),), asks=((100.10, 300, 2), (100.40, 900, 4))),
    }
    before = {s: mk(symbol=s, seq=1, t=T0, ltp=100.0, bids=((99.95, 900, 4),),
                    asks=((100.00, 3000, 9), (100.05, 3000, 9), (100.10, 3000, 9))) for s in books}
    outcome = {}
    for iid in admitted:
        sym = iid[2:]
        row = lg.row(iid)
        v = lg.transition(iid, ResState.ROUTED, expected_version=row["version"], now=T0, evidence={"order_id": f"P-{sym}"})
        after = reg.accept(mk(symbol=sym, seq=2, t=T0 + 1.0, **books[sym]), now=T0 + 1.0)
        r = taker_fill(Side.BUY, row["qty"], 100.15, TICK, before[sym], after, T0 + 0.3)
        got = r.filled[PESS]
        outcome[sym] = got
        if got:
            v = lg.record_entry_fill(iid, got, round(r.vwap[PESS], 2), now=T0 + 1,
                                     evidence={"fill_evidence": "BOOK_WALK", "envelope": "PESSIMISTIC"})
        if got < row["qty"]:                                    # IOC remainder cancelled by the exchange
            v = lg.transition(iid, ResState.CANCEL_REQUESTED, expected_version=v, now=T0 + 1)
            ack = {"cancel_ack": True, "filled_qty_at_ack": got, "reason": "IOC_REMAINDER"}
            lg.transition(iid, ResState.OPEN_POSITION if got else ResState.CANCELLED_CONFIRMED,
                          expected_version=v, now=T0 + 1, evidence=ack)
    assert outcome["FNOSTOCK002"] == 0                          # breakout ran past the collar: no fill
    assert lg.row("I-FNOSTOCK002")["state"] == "CANCELLED_CONFIRMED"
    assert 0 < outcome["FNOSTOCK003"] < lg.row("I-FNOSTOCK003")["qty"] or lg.row("I-FNOSTOCK003")["state"] == "OPEN_POSITION"
    assert lg.usage().slots == 2                                # the unfilled slot came back

    # emergency exit of the full position in FNOSTOCK001 into a falling book
    iid = "I-FNOSTOCK001"
    pos = lg.row(iid)["filled"]
    ex = EmergencyExitSim("FNOSTOCK001", pos, TICK, DynamicBand(100.0, TICK, T0 + 20_000), T0 + 5,
                          protective_open_qty=pos, params=ExitParams(), limiter=TokenBucket())
    vt = VolumeDeltaTracker()
    prev = mk(symbol="FNOSTOCK001", seq=3, t=T0 + 5, ltp=99.50, bids=ladder(99.45, 0.05, 5, qty=150),
              asks=((99.50, 500, 3),))
    vt.update(prev, TD.isoformat())
    t, seq, recorded = T0 + 5, 3, 0
    while ex.state is not ExitState.FILLED and seq < 40:
        t += 1.0
        seq += 1
        s = mk(symbol="FNOSTOCK001", seq=seq, t=t, ltp=99.50 - 0.05 * seq, cum=10_000 + 50 * seq,
               bids=ladder(99.45 - 0.05 * seq, 0.05, 5, qty=150), asks=((99.50 - 0.05 * seq, 500, 3),))
        ex.on_snapshot(prev, s, vt.update(s, TD.isoformat()), now=t)
        for f in ex.fills[recorded:]:
            lg.record_exit_fill(iid, f.qty, f.price, now=t, evidence={"fill_evidence": f.evidence.value})
        recorded = len(ex.fills)
        state = lg.row(iid)["state"]
        assert state in ("OPEN_POSITION", "EXITING", "CLOSED")
        assert (state == "CLOSED") == (ex.sold == pos)          # flat only on evidenced fills
        prev = s
    assert ex.state is ExitState.FILLED and lg.row(iid)["state"] == "CLOSED"
    assert ex.attempts >= 2 and max(f.price for f in ex.fills) < 100.0
