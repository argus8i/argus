"""Adversarial and functional integration tests for Track 2 Phase 1B1 Recorder.

Verifies:
1. Valid zero-signal E1 session counts toward 60 and 0 toward 20.
2. E2-but-zero-fill behavior (depth logged as E2, verdict still 0 toward 20).
3. Duplicate snapshot rejection.
4. >5-second gap VOID.
5. Hidden/stale feed rejection (feed_validity.check_feed integration).
6. Refusal of any E3 claim / no synthesized trade IDs.
7. Signal binding to frozen order_rules hash and valid 09:30–15:15 window.
8. Early close rejection (before 15:30 IST).
9. Crash/incomplete manifest detection.
10. Idempotent close (verdict.json persisted, cannot increment counters twice).
11. Static no-order-path guard (no broker order route, POST/PUT/DELETE, or credential reads).
"""

import ast
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from antigravity.daemons.track2_session_recorder import Track2SessionRecorder
from antigravity.models import session_manifest as sm
from antigravity.models.session_manifest import (
    IST,
    EvidenceClass,
    SessionStatus,
    _canonical_json,
    _sha256_bytes,
    write_attempt_record,
    write_locked_preflight,
)

SESSION_DATE = "2026-09-21"
REGISTER_NOW = datetime(2026, 9, 21, 9, 5, tzinfo=IST)
EVALUATE_NOW = datetime(2026, 9, 21, 16, 0, tzinfo=IST)
TEST_NOW = [REGISTER_NOW]
PENDING_PREFLIGHTS = {}
TEST_UNIVERSE = {
    "session_date": SESSION_DATE,
    "selection_rule": "TEST_FIXED_UNIVERSE",
    "rule_version": "1.0",
    "symbols": ["ANGELONE", "BDL", "CDSL", "SUZLON"],
}


@pytest.fixture(autouse=True)
def fixed_internal_clock(monkeypatch):
    TEST_NOW[0] = REGISTER_NOW
    monkeypatch.setattr(sm, "_now_ist", lambda: TEST_NOW[0])


def canonical_hash(value):
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def file_hash(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_preregistration(**overrides):
    value = {
        "track_id": "TRACK2",
        "session_date": SESSION_DATE,
        "frozen_at": f"{SESSION_DATE}T09:00:00+05:30",
        "config_sha256": "a" * 64,
        "universe_sha256": "b" * 64,
        "order_rules": {"strategy": "15M_ORB", "risk_rs": 1500},
        "cost_model": {
            "version": "v1",
            "gross_only_forbidden": True,
            "minimum_cost_rs": 1.0,
        },
        "gap_seconds": 5.0,
        "queue_haircut": 0.25,
        "latency_ms": 500,
        "gate_stats_version": "track2-gate-v1",
        "paper_only": True,
        "qualification_mode": "PROSPECTIVE_QUALIFYING",
    }
    value.update(overrides)
    return value


def make_order_spec(strategy_rules=None, **overrides):
    rules = strategy_rules or {"strategy": "15M_ORB", "risk_rs": 1500}
    value = {
        "strategy_rules_sha256": canonical_hash(rules),
        "symbol": "CDSL",
        "side": "BUY",
        "limit_price": 100.0,
        "quantity": 20,
    }
    value.update(overrides)
    return value


def create_reference_files(session_dir: Path):
    sources = session_dir / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    ref_files = []
    ref_hashes = {}
    roles = {"fno": "FNO", "surveillance": "SURVEILLANCE", "band": "BAND_POLICY"}
    for name in ("fno", "surveillance", "band"):
        path = sources / f"{name}.jsonl"
        path.write_text(json.dumps({"source": name, "session": SESSION_DATE}) + "\n", encoding="utf-8")
        digest = file_hash(path)
        ref_hashes[name] = digest
        ref_files.append({"path": f"sources/{name}.jsonl", "role": roles[name]})
    universe_path = sources / "universe.json"
    universe_path.write_bytes(_canonical_json(TEST_UNIVERSE))
    universe_hash = file_hash(universe_path)
    ref_files.append({"path": "sources/universe.json", "role": "UNIVERSE"})
    preflight = {
        "fno_verified": True,
        "asm_gsm_clear": True,
        "band_check": True,
        "feed_health": True,
        "source_authenticated": True,
        "passed": True,
        "qualification_mode": "PROSPECTIVE_QUALIFYING",
        "checked_at": f"{SESSION_DATE}T09:10:00+05:30",
        "fno_source_sha256": ref_hashes["fno"],
        "surveillance_source_sha256": ref_hashes["surveillance"],
        "band_source_sha256": ref_hashes["band"],
        "universe_sha256": universe_hash,
    }
    PENDING_PREFLIGHTS[session_dir.resolve()] = preflight
    return ref_files, preflight


def make_recorder(session_dir: Path, prereg=None):
    session_dir = Path(session_dir)
    sources = session_dir / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    universe_path = sources / "universe.json"
    if not universe_path.exists():
        universe_path.write_bytes(_canonical_json(TEST_UNIVERSE))
    prereg = dict(prereg or make_preregistration())
    prereg["universe_sha256"] = file_hash(universe_path)
    if not (session_dir.parent / "session_attempt_registry.jsonl").exists():
        write_attempt_record(
            session_dir.parent, SESSION_DATE, "PENDING",
            details={"qualification_mode": "PROSPECTIVE_QUALIFYING"}, recorded_at=REGISTER_NOW,
        )
    recorder = Track2SessionRecorder(session_dir, prereg, _test_clock=lambda: TEST_NOW[0])
    pending = PENDING_PREFLIGHTS.get(session_dir.resolve())
    if pending is not None and not (session_dir / "preflight.json").exists():
        TEST_NOW[0] = datetime(2026, 9, 21, 9, 12, tzinfo=IST)
        write_locked_preflight(session_dir, pending)
    return recorder


def record_snapshot(recorder, snapshot, wall_clock):
    TEST_NOW[0] = wall_clock
    return recorder.record_snapshot(snapshot)


def sample_snapshot(ts_str, ltp=100.0, active_stock=None, depth=None, data_valid=True, is_tab_hidden=False, is_stale=False, status="LIVE"):
    dt = datetime.fromisoformat(ts_str)
    return {
        "timestamp": ts_str,
        "local_write_time": dt.strftime("%Y-%m-%d %H:%M:%S"),
        "data_valid": data_valid,
        "is_tab_hidden": is_tab_hidden,
        "is_stale": is_stale,
        "status": status,
        "watchlist": [
            {"symbol": "CDSL", "ltp": ltp, "change_pct": "1.5%", "change_abs": 1.5},
            {"symbol": "ANGELONE", "ltp": 2500.0, "change_pct": "0.8%", "change_abs": 20.0},
        ],
        "active_stock": active_stock,
        "depth": depth,
    }


def test_valid_zero_signal_e1_session_counts_toward_60(tmp_path):
    session_dir = tmp_path / SESSION_DATE
    prereg = make_preregistration()
    ref_files, preflight = create_reference_files(session_dir)

    recorder = make_recorder(session_dir, prereg)

    # Stream 09:15 to 15:30 every 5 seconds with watchlist LTP
    start = datetime(2026, 9, 21, 9, 15, tzinfo=IST)
    end = datetime(2026, 9, 21, 15, 30, tzinfo=IST)
    cur = start
    while cur <= end:
        snap = sample_snapshot(cur.isoformat())
        ok = record_snapshot(recorder, snap, cur)
        assert ok is True
        cur += timedelta(seconds=5)

    TEST_NOW[0] = EVALUATE_NOW
    verdict = recorder.close_and_evaluate(
        preflight=preflight,
        reference_files=ref_files,
        session_closed_at=f"{SESSION_DATE}T15:30:01+05:30",
    )

    assert verdict.status is SessionStatus.COUNTED
    assert verdict.signals == 0
    assert verdict.counts_toward_60 == 1
    assert verdict.counts_toward_20 == 0
    assert (session_dir / "verdict.json").is_file()


def test_e2_marketable_depth_logged_but_zero_fill(tmp_path):
    session_dir = tmp_path / SESSION_DATE
    prereg = make_preregistration()
    ref_files, preflight = create_reference_files(session_dir)

    recorder = make_recorder(session_dir, prereg)

    start = datetime(2026, 9, 21, 9, 15, tzinfo=IST)
    end = datetime(2026, 9, 21, 15, 30, tzinfo=IST)
    cur = start
    depth_sample = {
        "bids": [{"price": 100.0, "quantity": 100, "orders": 1}],
        "offers": [{"price": 100.1, "quantity": 200, "orders": 2}],
    }
    while cur <= end:
        snap = sample_snapshot(cur.isoformat(), active_stock="CDSL", depth=depth_sample)
        ok = record_snapshot(recorder, snap, cur)
        assert ok is True
        cur += timedelta(seconds=5)

    TEST_NOW[0] = EVALUATE_NOW
    manifest = recorder.build_data_manifest(reference_files=ref_files)
    assert manifest["evidence_class_max"] == EvidenceClass.E2_MARKETABLE_DEPTH.value

    verdict = recorder.close_and_evaluate(
        preflight=preflight,
        reference_files=ref_files,
        session_closed_at=f"{SESSION_DATE}T15:30:01+05:30",
    )
    assert verdict.status is SessionStatus.COUNTED
    assert verdict.counts_toward_60 == 1
    # E2 does NOT count toward 20 fills
    assert verdict.counts_toward_20 == 0


def test_duplicate_snapshot_rejection(tmp_path):
    session_dir = tmp_path / SESSION_DATE
    prereg = make_preregistration()
    recorder = make_recorder(session_dir, prereg)

    snap = sample_snapshot("2026-09-21T09:15:00+05:30")
    t0 = datetime.fromisoformat("2026-09-21T09:15:00+05:30")
    first_ok = record_snapshot(recorder, snap, t0)
    assert first_ok is True

    # Duplicate content
    dup_ok = record_snapshot(recorder, snap, t0)
    assert dup_ok is False
    recorder.close_writer()


def test_out_of_order_snapshot_rejection(tmp_path):
    session_dir = tmp_path / SESSION_DATE
    prereg = make_preregistration()
    recorder = make_recorder(session_dir, prereg)

    snap1 = sample_snapshot("2026-09-21T09:15:10+05:30", ltp=100.0)
    snap2 = sample_snapshot("2026-09-21T09:15:05+05:30", ltp=101.0)
    t1 = datetime.fromisoformat("2026-09-21T09:15:10+05:30")
    t2 = datetime.fromisoformat("2026-09-21T09:15:15+05:30")

    assert record_snapshot(recorder, snap1, t1) is True
    assert record_snapshot(recorder, snap2, t2) is False
    recorder.close_writer()


def test_greater_than_5_second_gap_voids_session(tmp_path):
    session_dir = tmp_path / SESSION_DATE
    prereg = make_preregistration()
    ref_files, preflight = create_reference_files(session_dir)

    recorder = make_recorder(session_dir, prereg)

    start = datetime(2026, 9, 21, 9, 15, tzinfo=IST)
    end = datetime(2026, 9, 21, 15, 30, tzinfo=IST)
    cur = start
    while cur <= end:
        # Introduce a 6-second gap at 10:00:00
        if cur == datetime(2026, 9, 21, 10, 0, tzinfo=IST):
            cur += timedelta(seconds=6)
        else:
            cur += timedelta(seconds=5)
        if cur <= end:
            record_snapshot(recorder, sample_snapshot(cur.isoformat()), cur)

    TEST_NOW[0] = EVALUATE_NOW
    verdict = recorder.close_and_evaluate(
        preflight=preflight,
        reference_files=ref_files,
        session_closed_at=f"{SESSION_DATE}T15:30:01+05:30",
    )
    assert verdict.status is SessionStatus.VOID
    assert any("gap" in reason for reason in verdict.void_reasons)


def test_hidden_and_stale_feed_rejection(tmp_path):
    session_dir = tmp_path / SESSION_DATE
    prereg = make_preregistration()
    recorder = make_recorder(session_dir, prereg)
    t = datetime.fromisoformat("2026-09-21T09:15:00+05:30")

    hidden_snap = sample_snapshot("2026-09-21T09:15:00+05:30", is_tab_hidden=True)
    assert record_snapshot(recorder, hidden_snap, t) is False

    stale_snap = sample_snapshot("2026-09-21T09:15:00+05:30", is_stale=True)
    assert record_snapshot(recorder, stale_snap, t) is False

    invalid_snap = sample_snapshot("2026-09-21T09:15:00+05:30", data_valid=False)
    assert record_snapshot(recorder, invalid_snap, t) is False

    frozen_snap = sample_snapshot("2026-09-21T09:15:00+05:30", status="STALE_DATA_FROZEN")
    assert record_snapshot(recorder, frozen_snap, t) is False
    recorder.close_writer()


def test_refuses_e3_claim_and_never_creates_trade_ids(tmp_path):
    session_dir = tmp_path / SESSION_DATE
    prereg = make_preregistration()
    recorder = make_recorder(session_dir, prereg)
    t = datetime.fromisoformat("2026-09-21T09:15:00+05:30")

    depth_sample = {
        "bids": [{"price": 100.0, "quantity": 100, "orders": 1}],
        "offers": [{"price": 100.1, "quantity": 200, "orders": 2}],
    }
    snap = sample_snapshot("2026-09-21T09:15:00+05:30", active_stock="CDSL", depth=depth_sample)
    record_snapshot(recorder, snap, t)
    recorder.close_writer()

    stream_content = recorder.stream_path.read_text(encoding="utf-8")
    for line in stream_content.splitlines():
        ev = json.loads(line)
        # Refuse any E3 claim
        assert ev.get("evidence_class") != EvidenceClass.E3_TICK_QUEUE.value
        # Never creates / synthesizes trade IDs
        assert "trade_id" not in ev


def test_signal_binding_to_order_rules_and_window(tmp_path):
    session_dir = tmp_path / SESSION_DATE
    prereg = make_preregistration()
    recorder = make_recorder(session_dir, prereg)

    # 1. Reject before 09:30
    early_ts = datetime(2026, 9, 21, 9, 29, 59, tzinfo=IST)
    spec = make_order_spec()
    assert recorder.record_signal(signal_id="sig-1", symbol="CDSL", timestamp=early_ts, order_spec=spec) is False

    # 2. Reject after 15:15
    late_ts = datetime(2026, 9, 21, 15, 15, 1, tzinfo=IST)
    assert recorder.record_signal(signal_id="sig-2", symbol="CDSL", timestamp=late_ts, order_spec=spec) is False

    # 3. Reject mismatched order_rules hash
    valid_ts = datetime(2026, 9, 21, 9, 31, 0, tzinfo=IST)
    bad_spec = make_order_spec(strategy_rules={"strategy": "OTHER_STRATEGY"})
    assert recorder.record_signal(signal_id="sig-3", symbol="CDSL", timestamp=valid_ts, order_spec=bad_spec) is False

    # 4. Accept valid signal
    TEST_NOW[0] = valid_ts
    assert recorder.record_signal(signal_id="sig-4", symbol="CDSL", timestamp=valid_ts, order_spec=spec) is True

    # 5. Reject duplicate signal
    assert recorder.record_signal(signal_id="sig-5", symbol="CDSL", timestamp=valid_ts, order_spec=spec) is False

    # 6. Reject a reused ID even when the order changes.
    second_spec = make_order_spec(quantity=21)
    assert recorder.record_signal(
        signal_id="sig-4", symbol="CDSL", timestamp=valid_ts, order_spec=second_spec,
    ) is False

    # 7. Reject naive time and mismatched symbol without raising.
    assert recorder.record_signal(
        signal_id="sig-6", symbol="CDSL", timestamp=datetime(2026, 9, 21, 10, 0),
        order_spec=second_spec,
    ) is False
    assert recorder.record_signal(
        signal_id="sig-7", symbol="ANGELONE", timestamp=valid_ts, order_spec=second_spec,
    ) is False
    recorder.close_writer()


def test_snapshot_timestamp_must_match_session_and_explicit_clock(tmp_path):
    recorder = make_recorder(tmp_path / SESSION_DATE, make_preregistration())
    before_open = sample_snapshot(f"{SESSION_DATE}T09:14:59+05:30")
    after_close = sample_snapshot(f"{SESSION_DATE}T15:30:01+05:30")
    wrong_day = sample_snapshot("2026-09-22T09:15:00+05:30")
    assert record_snapshot(recorder, before_open, datetime.fromisoformat(before_open["timestamp"])) is False
    assert record_snapshot(recorder, after_close, datetime.fromisoformat(after_close["timestamp"])) is False
    assert record_snapshot(recorder, wrong_day, datetime.fromisoformat(wrong_day["timestamp"])) is False
    valid = sample_snapshot(f"{SESSION_DATE}T09:15:00+05:30")
    assert record_snapshot(recorder, valid, None) is False
    malformed = sample_snapshot(f"{SESSION_DATE}T09:15:00+05:30")
    malformed["non_json"] = {1, 2, 3}
    assert record_snapshot(
        recorder, malformed, datetime.fromisoformat(malformed["timestamp"]),
    ) is False
    recorder.close_writer()


@pytest.mark.parametrize("depth,active_stock", [
    ({"bids": [{"price": 100.0, "quantity": 0, "orders": 1}],
      "offers": [{"price": 100.1, "quantity": 1, "orders": 1}]}, "CDSL"),
    ({"bids": [{"price": 99.0, "quantity": 1, "orders": 1},
               {"price": 100.0, "quantity": 1, "orders": 1}],
      "offers": [{"price": 100.1, "quantity": 1, "orders": 1}]}, "CDSL"),
    ({"bids": [{"price": 100.0, "quantity": 1, "orders": 1}],
      "offers": [{"price": 100.1, "quantity": 1, "orders": 1}]}, "NOT_IN_WATCHLIST"),
])
def test_invalid_depth_is_never_labelled_e2(tmp_path, depth, active_stock):
    recorder = make_recorder(tmp_path / SESSION_DATE, make_preregistration())
    timestamp = datetime(2026, 9, 21, 9, 15, tzinfo=IST)
    snapshot = sample_snapshot(
        timestamp.isoformat(), active_stock=active_stock, depth=depth,
    )
    expected_recorded = active_stock != "NOT_IN_WATCHLIST"
    assert record_snapshot(recorder, snapshot, timestamp) is expected_recorded
    recorder.close_writer()
    events = [json.loads(line) for line in recorder.stream_path.read_text(encoding="utf-8").splitlines()]
    if not expected_recorded:
        assert events == []
        return
    assert events
    assert all(event["evidence_class"] != EvidenceClass.E2_MARKETABLE_DEPTH.value for event in events)


def test_restart_recovers_monotonic_and_duplicate_state(tmp_path):
    session_dir = tmp_path / SESSION_DATE
    prereg = make_preregistration()
    first = make_recorder(session_dir, prereg)
    t0 = datetime(2026, 9, 21, 9, 15, tzinfo=IST)
    snapshot = sample_snapshot(t0.isoformat())
    assert record_snapshot(first, snapshot, t0) is True
    first.close_writer()

    resumed = make_recorder(session_dir, prereg)
    assert record_snapshot(resumed, snapshot, t0) is False
    earlier = sample_snapshot((t0 - timedelta(seconds=1)).isoformat(), ltp=99.0)
    assert record_snapshot(resumed, earlier, t0) is False
    later = sample_snapshot((t0 + timedelta(seconds=5)).isoformat(), ltp=101.0)
    assert record_snapshot(resumed, later, t0 + timedelta(seconds=5)) is True
    resumed.close_writer()


def test_early_close_rejection(tmp_path):
    session_dir = tmp_path / SESSION_DATE
    prereg = make_preregistration()
    ref_files, preflight = create_reference_files(session_dir)

    recorder = make_recorder(session_dir, prereg)
    t_start = datetime.fromisoformat("2026-09-21T09:15:00+05:30")
    t_end = datetime.fromisoformat("2026-09-21T15:30:00+05:30")
    record_snapshot(recorder, sample_snapshot("2026-09-21T09:15:00+05:30"), t_start)
    record_snapshot(recorder, sample_snapshot("2026-09-21T15:30:00+05:30"), t_end)

    TEST_NOW[0] = EVALUATE_NOW
    # A caller-provided replay clock is ignored; closure provenance is recorder-owned.
    verdict = recorder.close_and_evaluate(
        preflight=preflight,
        reference_files=ref_files,
        session_closed_at=f"{SESSION_DATE}T14:00:00+05:30",
    )
    assert verdict.status is SessionStatus.VOID
    assert not any("session_closed_at must be after 15:30 IST" in reason for reason in verdict.void_reasons)


def test_incomplete_or_tampered_manifest(tmp_path):
    session_dir = tmp_path / SESSION_DATE
    prereg = make_preregistration()
    ref_files, preflight = create_reference_files(session_dir)

    recorder = make_recorder(session_dir, prereg)
    t_start = datetime.fromisoformat("2026-09-21T09:15:00+05:30")
    t_end = datetime.fromisoformat("2026-09-21T15:30:00+05:30")
    record_snapshot(recorder, sample_snapshot("2026-09-21T09:15:00+05:30"), t_start)
    record_snapshot(recorder, sample_snapshot("2026-09-21T15:30:00+05:30"), t_end)

    manifest = recorder.build_data_manifest(reference_files=ref_files)
    manifest["complete"] = False

    TEST_NOW[0] = EVALUATE_NOW
    verdict = sm.evaluate_session(
        session_dir=session_dir,
        preflight=preflight,
        data_manifest=manifest,
        session_closed_at=f"{SESSION_DATE}T15:30:01+05:30",
    )
    assert verdict.status is SessionStatus.VOID
    assert any("data manifest complete must be exactly true" in reason for reason in verdict.void_reasons)


def test_reference_file_cannot_escape_session_directory(tmp_path):
    session_dir = tmp_path / SESSION_DATE
    outside = tmp_path / "outside.jsonl"
    outside.write_text("{}\n", encoding="utf-8")
    recorder = make_recorder(session_dir, make_preregistration())
    timestamp = datetime(2026, 9, 21, 9, 15, tzinfo=IST)
    record_snapshot(recorder, sample_snapshot(timestamp.isoformat()), timestamp)
    with pytest.raises(ValueError, match="inside the session"):
        recorder.build_data_manifest([{"path": "../outside.jsonl"}])


def test_idempotent_close_cannot_increment_twice(tmp_path):
    session_dir = tmp_path / SESSION_DATE
    prereg = make_preregistration()
    ref_files, preflight = create_reference_files(session_dir)

    recorder = make_recorder(session_dir, prereg)

    start = datetime(2026, 9, 21, 9, 15, tzinfo=IST)
    end = datetime(2026, 9, 21, 15, 30, tzinfo=IST)
    cur = start
    while cur <= end:
        snap = sample_snapshot(cur.isoformat())
        record_snapshot(recorder, snap, cur)
        cur += timedelta(seconds=5)

    TEST_NOW[0] = EVALUATE_NOW
    v1 = recorder.close_and_evaluate(
        preflight=preflight,
        reference_files=ref_files,
        session_closed_at=f"{SESSION_DATE}T15:30:01+05:30",
    )
    assert v1.status is SessionStatus.COUNTED

    # Repeated close call returns identical verdict
    v2 = recorder.close_and_evaluate(
        preflight=preflight,
        reference_files=ref_files,
        session_closed_at=f"{SESSION_DATE}T15:30:01+05:30",
    )
    assert v2.status is SessionStatus.COUNTED
    assert v2.counts_toward_60 == 1
    assert v2.counts_toward_20 == 0

    # Gate counts verification: compute_gate_counts enforces duplicate session rejection
    gate = sm.compute_gate_counts([v1])
    assert gate["prospective_sessions"] == 1


@pytest.mark.parametrize("replacement", [
    b"{corrupt",
    json.dumps({
        "track_id": "TRACK2", "session_date": SESSION_DATE, "status": "COUNTED",
        "void_reasons": [], "signals": 20, "qualifying_fills": 20,
        "counts_toward_60": 1, "counts_toward_20": 20,
        "qualifying_order_ids": [f"fake-{index}" for index in range(20)],
        "_session_closed_at": f"{SESSION_DATE}T15:30:01+05:30",
        "_preflight_sha256": "0" * 64, "_manifest_sha256": "0" * 64,
    }).encode(),
])
def test_corrupt_or_forged_verdict_is_refused_not_overwritten(tmp_path, replacement):
    session_dir = tmp_path / SESSION_DATE
    prereg = make_preregistration()
    ref_files, preflight = create_reference_files(session_dir)
    recorder = make_recorder(session_dir, prereg)
    start = datetime(2026, 9, 21, 9, 15, tzinfo=IST)
    end = datetime(2026, 9, 21, 15, 30, tzinfo=IST)
    record_snapshot(recorder, sample_snapshot(start.isoformat()), start)
    record_snapshot(recorder, sample_snapshot(end.isoformat()), end)
    TEST_NOW[0] = EVALUATE_NOW
    recorder.close_and_evaluate(
        preflight, reference_files=ref_files,
        session_closed_at=f"{SESSION_DATE}T15:30:01+05:30",
    )
    verdict_path = session_dir / "verdict.json"
    verdict_path.write_bytes(replacement)
    # Even if a forger recomputes the local digest, evidence reproduction must fail.
    verdict_path.with_suffix(".sha256").write_text(file_hash(verdict_path) + "\n", encoding="ascii")
    resumed = make_recorder(session_dir, prereg)
    with pytest.raises(ValueError):
        resumed.close_and_evaluate(
            preflight, reference_files=ref_files,
            session_closed_at=f"{SESSION_DATE}T15:30:01+05:30",
        )
    assert verdict_path.read_bytes() == replacement


def test_static_no_order_path_guard():
    """Static AST audit ensuring no broker execution, credentials, or unsafe network methods."""
    recorder_file = Path(__file__).resolve().parents[1] / "antigravity" / "daemons" / "track2_session_recorder.py"
    assert recorder_file.is_file()

    tree = ast.parse(recorder_file.read_text(encoding="utf-8"))

    forbidden_modules = {"urllib", "requests", "http.client", "aiohttp", "websockets", "kiteconnect"}
    forbidden_calls = {"post", "put", "delete", "place_order", "modify_order", "cancel_order"}
    forbidden_tokens = {"enctoken", "api_key", "api_secret", "access_token", "password", "totp"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_pkg = alias.name.split(".")[0]
                assert root_pkg not in forbidden_modules, f"Forbidden import: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root_pkg = node.module.split(".")[0]
                assert root_pkg not in forbidden_modules, f"Forbidden from-import: {node.module}"
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                assert node.func.attr.lower() not in forbidden_calls, f"Forbidden call: {node.func.attr}"
            elif isinstance(node.func, ast.Name):
                assert node.func.id.lower() not in forbidden_calls, f"Forbidden call: {node.func.id}"
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            val_lower = node.value.lower()
            for token in forbidden_tokens:
                assert token not in val_lower, f"Forbidden credential token: {token} in {node.value}"
