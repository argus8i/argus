import json
import math
import subprocess
import sys
import time
from pathlib import Path

import pytest

from execution_realism.capacity import (Candidate, CapacityConfig, ConfigMismatch, EVModel, EvidenceRequired,
                                        IllegalTransition, RejectReason, ReservationLedger, ResState,
                                        select_batch, size_position)

T0 = 1_000_000.0
SHA = "a" * 64
ROOT = Path(__file__).resolve().parents[1]


def cand(iid, symbol=None, sector=None, qty=500, limit=100.0, stop=97.0, **kw):
    kw.setdefault("margin_rate", 0.2)
    kw.setdefault("expires_at", T0 + 30)
    return Candidate(iid, symbol or iid, sector or f"SEC_{iid}", qty, limit, stop, **kw)


@pytest.fixture
def ledger(tmp_path):
    lg = ReservationLedger(tmp_path / "ledger.db", CapacityConfig())
    gate = lg.open_session("2026-09-24", SHA, now=T0)
    return lg, gate


def test_config_derivations():
    c = CapacityConfig()
    assert c.deployable_rs == 175_000 and round(c.slot_notional_rs, 2) == 58_333.33 and c.aggregate_risk_rs == 4_500


def test_codex_r02_four_pending_signals_cannot_breach_caps(ledger):
    """Codex probe: 4 x (500 sh @ 100, stop 97) were all approved: risk 6,000, notional 200,000."""
    lg, gate = ledger
    results = [lg.admit(cand(s), now=T0, session_gate=gate) for s in ("CDSL", "SUZLON", "RVNL", "BDL")]
    assert [r.ok for r in results] == [True, True, True, False]
    assert results[3].reason is RejectReason.SLOTS_FULL
    u = lg.usage()
    assert u.slots == 3 and u.risk_rs <= 4_500 + 1e-6 and u.notional_rs <= 175_000 + 1e-6


def test_codex_r03_rejection_is_a_typed_value(ledger):
    lg, gate = ledger
    a = lg.admit(cand("ANGELONE", qty=100_000), now=T0, session_gate=gate)
    assert a.ok is False and a.reason is RejectReason.PER_TRADE_RISK


@pytest.mark.parametrize("rate", [None, float("nan"), "INVALID", -1, True])
def test_codex_r11_margin_rate_unknowns_rejected(ledger, rate):
    lg, gate = ledger
    a = lg.admit(cand("CDSL", margin_rate=rate), now=T0, session_gate=gate)
    assert a.reason is RejectReason.INVALID_MARGIN_RATE


def test_no_session_gate_no_admission(tmp_path):
    lg = ReservationLedger(tmp_path / "l.db", CapacityConfig())
    assert lg.admit(cand("CDSL"), now=T0, session_gate="x").reason is RejectReason.NO_SESSION_GATE


def test_r16_one_allocation_config(tmp_path):
    ReservationLedger(tmp_path / "l.db", CapacityConfig())
    with pytest.raises(ConfigMismatch):
        ReservationLedger(tmp_path / "l.db", CapacityConfig(cash_buffer_rs=50_000))


def test_reservation_held_until_confirmed_terminal(ledger):
    lg, gate = ledger
    for s in ("A", "B", "C"):
        assert lg.admit(cand(s, initial_state=ResState.APPROVED), now=T0, session_gate=gate).ok
    v = lg.transition("A", ResState.ROUTED, expected_version=1, now=T0 + 1, evidence={"order_id": "P1"})
    v = lg.transition("A", ResState.WORKING, expected_version=v, now=T0 + 1)
    v = lg.transition("A", ResState.CANCEL_REQUESTED, expected_version=v, now=T0 + 2)
    assert lg.admit(cand("D"), now=T0 + 2, session_gate=gate).reason is RejectReason.SLOTS_FULL
    with pytest.raises(EvidenceRequired):
        lg.transition("A", ResState.CANCELLED_CONFIRMED, expected_version=v, now=T0 + 3, evidence={})
    lg.transition("A", ResState.CANCELLED_CONFIRMED, expected_version=v, now=T0 + 3,
                  evidence={"cancel_ack": True, "filled_qty_at_ack": 0})
    assert lg.admit(cand("D"), now=T0 + 3, session_gate=gate).ok


def test_partial_fill_then_cancel_keeps_only_filled_exposure(ledger):
    lg, gate = ledger
    lg.admit(cand("A", initial_state=ResState.APPROVED), now=T0, session_gate=gate)
    v = lg.transition("A", ResState.ROUTED, expected_version=1, now=T0, evidence={"order_id": "P1"})
    v = lg.record_entry_fill("A", 200, 99.9, now=T0 + 1, evidence={"fill_evidence": "QUEUE_CERTAIN"})
    v = lg.transition("A", ResState.CANCEL_REQUESTED, expected_version=v, now=T0 + 2)
    v = lg.transition("A", ResState.OPEN_POSITION, expected_version=v, now=T0 + 3, evidence={"cancel_ack": True})
    u = lg.usage()
    assert u.slots == 1 and math.isclose(u.notional_rs, 200 * 99.9) and math.isclose(u.risk_rs, 200 * (99.9 - 97.0))


def test_codex_r05_closed_needs_exit_fills(ledger):
    lg, gate = ledger
    lg.admit(cand("A", initial_state=ResState.APPROVED), now=T0, session_gate=gate)
    v = lg.transition("A", ResState.ROUTED, expected_version=1, now=T0, evidence={"order_id": "P1"})
    v = lg.record_entry_fill("A", 500, 100.0, now=T0 + 1, evidence={"fill_evidence": "BOOK_WALK"})
    with pytest.raises(EvidenceRequired):
        lg.transition("A", ResState.CLOSED, expected_version=v, now=T0 + 2)
    with pytest.raises(EvidenceRequired):
        lg.record_exit_fill("A", 500, 100.0, now=T0 + 2, evidence={})      # instruction, not a fill
    v = lg.record_exit_fill("A", 300, 98.5, now=T0 + 2, evidence={"fill_evidence": "BOOK_WALK"})
    assert lg.row("A")["state"] == "EXITING"
    lg.record_exit_fill("A", 200, 98.0, now=T0 + 3, evidence={"fill_evidence": "BOOK_WALK"})
    assert lg.row("A")["state"] == "CLOSED" and lg.usage().slots == 0


def test_codex_r12_prearmed_expiry_and_expired_routing(ledger):
    lg, gate = ledger
    lg.admit(cand("A", expires_at=T0 + 10), now=T0, session_gate=gate)
    v = lg.transition("A", ResState.PRE_ARMED, expected_version=1, now=T0 + 1)
    assert lg.sweep_expired(T0 + 11) == ["A"]
    lg.admit(cand("B", initial_state=ResState.APPROVED, expires_at=T0 + 10), now=T0, session_gate=gate)
    with pytest.raises(EvidenceRequired):
        lg.transition("B", ResState.ROUTED, expected_version=1, now=T0 + 12, evidence={"order_id": "X"})
    with pytest.raises(IllegalTransition):
        lg.transition("A", ResState.ROUTED, expected_version=v + 1, now=T0 + 12, evidence={"order_id": "X"})


def test_codex_r04_restart_requires_reconciliation(tmp_path):
    lg = ReservationLedger(tmp_path / "l.db", CapacityConfig())
    gate = lg.open_session("2026-09-24", SHA, now=T0)
    assert lg.claim_oms("oms-1", now=T0) is False
    lg.admit(cand("A", initial_state=ResState.APPROVED), now=T0, session_gate=gate)
    lg.transition("A", ResState.ROUTED, expected_version=1, now=T0, evidence={"order_id": "P1"})
    # process dies; a new OMS takes over after the heartbeat goes stale
    lg2 = ReservationLedger(tmp_path / "l.db", CapacityConfig())
    assert lg2.claim_oms("oms-2", now=T0 + 60) is True
    assert lg2.admit(cand("B"), now=T0 + 60, session_gate=gate).reason is RejectReason.RECONCILIATION_REQUIRED
    with pytest.raises(EvidenceRequired):
        lg2.mark_reconciled("oms-2", now=T0 + 61, evidence={})
    lg2.mark_reconciled("oms-2", now=T0 + 61, evidence={"broker_orderbook_sha256": SHA})
    assert lg2.admit(cand("B", expires_at=T0 + 90), now=T0 + 62, session_gate=gate).ok


def test_batch_arbitration_ignores_arrival_order(ledger):
    lg, gate = ledger
    feats = {"A": (0.02, 0.9, 0.01), "B": (0.01, 0.2, 0.02), "C": (0.03, 0.1, 0.00),
             "D": (0.01, 0.5, 0.03), "E": (0.05, 0.3, 0.01)}
    cs = [cand(k, features={"spread_atr": a, "speed_atr": b, "rel_strength": c}) for k, (a, b, c) in feats.items()]
    picks = []
    for order in (cs, list(reversed(cs)), cs[2:] + cs[:2]):
        chosen, method, _ = select_batch(order, lg.usage(), lg.config)
        picks.append(chosen)
    assert picks[0] == picks[1] == picks[2] == ["B", "D", "A"] and method == "UNCALIBRATED_RULE"
    res = lg.admit_batch(cs, now=T0, session_gate=gate, batch_id="0930")
    assert sorted(r.intent_id for r in res if r.ok) == ["A", "B", "D"]
    assert {r.reason for r in res if not r.ok} == {RejectReason.NOT_SELECTED}


def test_calibrated_ev_selection_drops_negative_ev(ledger):
    lg, _ = ledger
    model = EVModel({"x": 1.0}, 0.0, {"x": 0.0}, {"x": 1.0}, n_train=250, oos_r2=0.02, trained_through="2026-09-20")
    cs = [cand(k, features={"x": x}) for k, x in (("A", 0.3), ("B", -0.2), ("C", 0.1), ("D", 0.5), ("E", 0.05))]
    chosen, method, scores = select_batch(cs, lg.usage(), lg.config, model)
    assert method == "CALIBRATED_EV" and chosen == ["D", "A", "C"]
    thin = EVModel({"x": 1.0}, 0.0, {"x": 0.0}, {"x": 1.0}, n_train=40, oos_r2=0.1, trained_through="x")
    assert select_batch(cs, lg.usage(), lg.config, thin)[1] == "UNCALIBRATED_RULE"


def test_sector_cap(ledger):
    lg, gate = ledger
    assert lg.admit(cand("A", sector="DEF"), now=T0, session_gate=gate).ok
    assert lg.admit(cand("B", sector="DEF"), now=T0, session_gate=gate).ok
    assert lg.admit(cand("C", sector="DEF"), now=T0, session_gate=gate).reason is RejectReason.SECTOR_CAP


def test_size_position_reports_binding_constraint():
    cfg = CapacityConfig()
    s = size_position(limit_px=210.30, stop_trigger=208.00, tick=0.01, cfg=cfg)
    assert s.binding == "NOTIONAL" and s.qty == 277 and s.planned_risk_rs < 1500
    w = size_position(limit_px=210.30, stop_trigger=203.00, tick=0.01, cfg=cfg)
    assert w.binding == "RISK" and w.planned_risk_rs <= 1500 and w.notional_rs <= cfg.slot_notional_rs


_WORKER = r"""
import json, sys, time
sys.path.insert(0, {root!r})
from execution_realism.capacity import Candidate, CapacityConfig, ReservationLedger
lg = ReservationLedger({db!r}, CapacityConfig(), busy_timeout_s=10.0)
while time.time() < {start}:
    time.sleep(0.001)
c = Candidate("I{i}", "SYM{i}", "SEC{i}", 500, 100.0, 97.0, expires_at={exp}, margin_rate=0.2)
a = lg.admit(c, now={now}, session_gate={gate!r})
print(json.dumps({{"ok": a.ok, "reason": a.reason.value}}))
"""


def test_cross_process_race_cannot_breach_caps(tmp_path):
    """Codex: 'verify across terminal and Telegram processes, not just threads'."""
    db = str(tmp_path / "race.db")
    lg = ReservationLedger(db, CapacityConfig())
    gate = lg.open_session("2026-09-24", SHA, now=T0)
    start = time.time() + 1.5
    procs = [subprocess.Popen([sys.executable, "-c", _WORKER.format(root=str(ROOT), db=db, start=start, i=i,
                                                                   exp=T0 + 30, now=T0, gate=gate)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(8)]
    outs = [json.loads(p.communicate(timeout=60)[0].strip()) for p in procs]
    assert sum(o["ok"] for o in outs) == 3
    assert {o["reason"] for o in outs if not o["ok"]} == {"SLOTS_FULL"}
    u = lg.usage()
    assert u.slots == 3 and u.risk_rs <= 4500 + 1e-6 and u.notional_rs <= 175_000 + 1e-6


_NAIVE_WORKER = r"""
import json, sys, time
sys.path.insert(0, {root!r})
from execution_realism.capacity import Candidate, CapacityConfig, ReservationLedger, check_fit, RejectReason
lg = ReservationLedger({db!r}, CapacityConfig(), busy_timeout_s=10.0)
while time.time() < {start}:
    time.sleep(0.001)
c = Candidate("I{i}", "SYM{i}", "SEC{i}", 500, 100.0, 97.0, expires_at={exp}, margin_rate=0.2)
ok = check_fit(c, lg.usage(), lg.config) is RejectReason.OK      # read in one transaction...
time.sleep(0.05)
if ok:                                                             # ...write in another
    with lg._tx() as con:
        con.execute("INSERT INTO reservations(intent_id,symbol,sector,state,qty,limit_px,stop_limit_px,"
                    "expires_at,session_gate,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (c.intent_id, c.symbol, c.sector, "APPROVED", 500, 100.0, 97.0, {exp}, {gate!r}, {now}, {now}))
print(json.dumps({{"ok": ok}}))
"""


def test_negative_control_read_then_write_breaches(tmp_path):
    """The same race against a read-check-then-write pattern (what a per-process lock plus
    a JSON ledger amounts to) admits more than three: the race test has teeth."""
    db = str(tmp_path / "naive.db")
    lg = ReservationLedger(db, CapacityConfig())
    gate = lg.open_session("2026-09-24", SHA, now=T0)
    start = time.time() + 1.5
    procs = [subprocess.Popen([sys.executable, "-c", _NAIVE_WORKER.format(root=str(ROOT), db=db, start=start, i=i,
                                                                         exp=T0 + 30, now=T0, gate=gate)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(8)]
    outs = [json.loads(p.communicate(timeout=60)[0].strip()) for p in procs]
    assert sum(o["ok"] for o in outs) > 3
    assert lg.usage().risk_rs > 4500
