"""Synthetic paper-only acceptance probes; all state lives in tmp_path."""
import json
import multiprocessing
import os
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pytest

if os.environ.get("ARGUS_A1_REVIEW_ROOT"):
    sys.path.insert(0, os.environ["ARGUS_A1_REVIEW_ROOT"])

from antigravity.models.execution_policy import ExecutionIntent, ExecutionMode, PolicyConfig
from antigravity.daemons.hybrid_execution_oms import HybridExecutionOMS
if os.environ.get("ARGUS_A1_REVIEW_ROOT"):
    import antigravity.daemons.hybrid_execution_oms as _reviewed
    assert Path(_reviewed.__file__).resolve().is_relative_to(Path(os.environ["ARGUS_A1_REVIEW_ROOT"]).resolve())


def candidate(symbol="AAA", **overrides):
    row = dict(symbol=symbol, sector=symbol, entry_price=1000., stop_loss=960.,
               atr14=50., atr_timestamp=datetime.now(timezone.utc).isoformat(),
               var_elm_rate=.2, strategy="TEST")
    row.update(overrides)
    return row


def oms(path):
    return HybridExecutionOMS(output_dir=path, config=PolicyConfig(expiry_seconds=600))


def _race(path, symbol, ready, start, result):
    desk = oms(path)
    ready.put(symbol)
    start.wait(15)
    it, reason = desk.submit_candidate(candidate(symbol))
    result.put((symbol, it is not None, reason))


def test_real_processes_share_pending_and_open_capacity(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    ready, results, start = ctx.Queue(), ctx.Queue(), ctx.Event()
    workers = [ctx.Process(target=_race, args=(tmp_path, f"S{i}", ready, start, results)) for i in range(6)]
    for p in workers:
        p.start()
    try:
        for _ in workers:
            ready.get(timeout=30)
        start.set()
        rows = [results.get(timeout=30) for _ in workers]
        assert sum(r[1] for r in rows) == 3, rows
        desk = oms(tmp_path)
        assert len(desk.intents) == 3
        a, pending = desk._get_active_and_pending_exposures()
        assert sum(r["notional_rs"] for r in a + pending) <= 114000
    finally:
        start.set()
        for p in workers:
            p.join(10)
            if p.is_alive():
                p.terminate()
                p.join()


def test_limit_sizing_and_risk_ignore_forged_metrics():
    it = ExecutionIntent.create_from_candidate(candidate(shares=38, stop_loss=980, risk_rs=1e9, max_slot_notional_rs=1e9))
    assert it is not None
    assert it.shares * it.limit_price <= 38000
    assert it.risk_rs == round(it.shares * (it.limit_price - it.stop_loss), 2)
    assert it.risk_rs <= 1500


@pytest.mark.parametrize("atr", [None, 0, -1, float("nan"), float("inf")])
def test_invalid_required_atr_rejected(atr):
    assert ExecutionIntent.create_from_candidate(candidate(atr14=atr)) is None


def test_stale_or_missing_atr_timestamp_rejected():
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert ExecutionIntent.create_from_candidate(candidate(atr_timestamp=old)) is None
    row = candidate()
    del row["atr_timestamp"]
    assert ExecutionIntent.create_from_candidate(row) is None


def routed(path):
    desk = oms(path)
    it, reason = desk.submit_candidate(candidate())
    assert it is not None, reason
    assert desk.approve_intent(it.intent_id, current_ltp=1000)["status"] == "SUCCESS"
    return desk, it


def test_cancel_race_and_exit_release_only_with_evidence(tmp_path):
    desk, it = routed(tmp_path)
    desk.emergency_flatten_all()
    assert desk.record_entry_fill(it.order_id, 5, 1000, evidence={"event_id": "f1", "kind": "PAPER_TRADE_THROUGH"})["status"] == "SUCCESS"
    assert desk.confirm_cancel(it.order_id, evidence={"event_id": "c1", "kind": "PAPER_CANCEL_ACK", "filled_qty_at_ack": 5})["status"] == "SUCCESS"
    a, pending = oms(tmp_path)._get_active_and_pending_exposures()
    assert len(a) == 1 and not pending
    assert a[0]["notional_rs"] == 5000
    desk.emergency_flatten_all()
    assert desk.record_exit_fill(it.order_id, 5, 990, evidence={})["status"] == "ERROR"
    assert oms(tmp_path).active_orders
    assert desk.record_exit_fill(it.order_id, 5, 990, evidence={"event_id": "e1", "kind": "PAPER_TRADE_THROUGH"})["status"] == "SUCCESS"
    assert not oms(tmp_path).active_orders


def test_evidence_replay_is_idempotent(tmp_path):
    desk, it = routed(tmp_path)
    ev = {"event_id": "f1", "kind": "PAPER_TRADE_THROUGH"}
    desk.record_entry_fill(it.order_id, 5, 1000, evidence=ev)
    desk.record_entry_fill(it.order_id, 5, 1000, evidence=ev)
    assert oms(tmp_path).active_orders[0]["filled_shares"] == 5


def test_truncated_state_blocks_new_admission(tmp_path):
    desk, _ = routed(tmp_path)
    with desk.orders_path.open("a", encoding="utf-8") as f:
        f.write('{"order_id":')
    it, reason = oms(tmp_path).submit_candidate(candidate("BBB"))
    assert it is None and "RECONCILIATION" in reason


def test_unaffordable_and_mutated_policy_fail_closed(tmp_path):
    assert ExecutionIntent.create_from_candidate(candidate(entry_price=39000, stop_loss=38000)) is None
    desk = oms(tmp_path)
    desk.config.max_open_positions = 20
    assert desk.submit_candidate(candidate())[0] is None


def test_partial_exit_keeps_remaining_risk_and_slot(tmp_path):
    desk, it = routed(tmp_path)
    desk.record_entry_fill(it.order_id, it.shares, 1000, evidence={"event_id": "f1", "kind": "PAPER_QUOTE_THROUGH"})
    desk.emergency_flatten_all()
    desk.record_exit_fill(it.order_id, 5, 999, evidence={"event_id": "e1", "kind": "PAPER_TRADE_THROUGH"})
    a, pending = oms(tmp_path)._get_active_and_pending_exposures()
    assert len(a) == 1 and not pending
    assert a[0]["notional_rs"] == (it.shares - 5) * 1000
    assert a[0]["open_risk_rs"] == (it.shares - 5) * 40


def test_no_fill_or_exit_from_ltp_only(tmp_path):
    desk, it = routed(tmp_path)
    (tmp_path / "live_depth_track2.json").write_text(json.dumps({"watchlist": [{"symbol": "AAA", "ltp": 990}]}))
    desk.emergency_flatten_all()
    rows = [json.loads(x) for x in desk.orders_path.read_text().splitlines()]
    assert rows[-1]["status"] == "CANCEL_REQUESTED"
    assert "gross_pnl_rs" not in rows[-1] and "realized_gross_pnl_rs" not in rows[-1]
    assert desk.record_entry_fill(it.order_id, 1, 1000, evidence={"event_id": "bad", "kind": "LTP_TOUCH"})["status"] == "ERROR"


def test_changed_valid_json_and_missing_file_latch_reconciliation(tmp_path):
    desk = oms(tmp_path)
    assert desk.submit_candidate(candidate())[0]
    original = desk.intents_path.read_text()
    desk.intents_path.write_text(json.dumps({"intents": []}))
    assert oms(tmp_path).submit_candidate(candidate("BBB"))[0] is None
    desk.intents_path.write_text(original)
    assert oms(tmp_path).submit_candidate(candidate("BBB"))[0] is None  # latch persists


def test_missing_state_file_never_means_empty_book(tmp_path):
    desk = oms(tmp_path)
    assert desk.submit_candidate(candidate())[0]
    desk.intents_path.unlink()
    assert oms(tmp_path).submit_candidate(candidate("BBB"))[0] is None


def test_conflicting_event_replay_and_cancel_counter_rejected(tmp_path):
    desk, it = routed(tmp_path)
    ev = {"event_id": "f1", "kind": "PAPER_TRADE_THROUGH"}
    assert desk.record_entry_fill(it.order_id, 5, 1000, evidence=ev)["status"] == "SUCCESS"
    assert desk.record_entry_fill(it.order_id, 6, 1000, evidence=ev)["status"] == "ERROR"
    desk.emergency_flatten_all()
    assert desk.confirm_cancel(it.order_id, evidence={"event_id": "c1", "kind": "PAPER_CANCEL_ACK", "filled_qty_at_ack": 0})["status"] == "ERROR"
    assert oms(tmp_path).active_orders[0]["filled_shares"] == 5


def test_two_sleeves_same_symbol_cannot_double_reserve(tmp_path):
    a, b = oms(tmp_path), oms(tmp_path)
    assert a.submit_candidate(candidate(strategy="ORB"))[0]
    assert b.submit_candidate(candidate(strategy="PEAD"))[0] is None


def test_database_corruption_never_falls_back_to_empty(tmp_path):
    desk = oms(tmp_path)
    assert desk.submit_candidate(candidate())[0]
    desk._lock.path.write_bytes(b"not a SQLite database")
    with pytest.raises(Exception):
        oms(tmp_path)


def test_nested_transactions_do_not_deadlock(tmp_path):
    desk = oms(tmp_path)
    with desk._lock:
        it, reason = desk.submit_candidate(candidate())
        assert it is not None, reason
        with desk._lock:
            assert desk.approve_intent(it.intent_id, current_ltp=1000)["status"] == "SUCCESS"


def test_exit_before_cancel_ack_is_not_allowed(tmp_path):
    desk, it = routed(tmp_path)
    desk.record_entry_fill(it.order_id, 5, 1000, evidence={"event_id": "f1", "kind": "PAPER_TRADE_THROUGH"})
    desk.emergency_flatten_all()
    assert desk.record_exit_fill(it.order_id, 5, 990, evidence={"event_id": "e1", "kind": "PAPER_TRADE_THROUGH"})["status"] == "ERROR"
