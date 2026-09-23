import hashlib
import inspect
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from antigravity.models import session_manifest as sm
from antigravity.models.session_manifest import (
    EvidenceClass, FillState, IST, SessionStatus, compute_gate_counts,
    evaluate_session, evidence_counts_toward_20,
    verify_locked_preregistration, write_locked_preregistration,
    write_locked_preflight, write_attempt_record, write_stream_checkpoint,
)

SESSION_DATE = "2026-09-21"
REGISTER_NOW = datetime(2026, 9, 21, 9, 5, tzinfo=IST)
EVALUATE_NOW = datetime(2026, 9, 21, 16, 0, tzinfo=IST)
TEST_NOW = [REGISTER_NOW]


@pytest.fixture(autouse=True)
def fixed_internal_clock(monkeypatch):
    TEST_NOW[0] = REGISTER_NOW
    monkeypatch.setattr(sm, "_now_ist", lambda: TEST_NOW[0])


def canonical_hash(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(payload).hexdigest()


def file_hash(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


UNIVERSE = {
    "session_date": SESSION_DATE,
    "selection_rule": "TEST_FIXED_UNIVERSE",
    "rule_version": "1.0",
    "symbols": ["ANGELONE", "BDL", "CDSL", "SUZLON"],
}


def preregistration(**overrides):
    value = {
        "track_id": "TRACK2", "session_date": SESSION_DATE,
        "frozen_at": f"{SESSION_DATE}T09:00:00+05:30",
        "config_sha256": "a" * 64, "universe_sha256": "b" * 64,
        "order_rules": {"strategy": "15M_ORB", "risk_rs": 1500},
        "cost_model": {
            "version": "v1", "gross_only_forbidden": True, "minimum_cost_rs": 1.0,
        },
        "gap_seconds": 5, "queue_haircut": 0.25, "latency_ms": 500,
        "gate_stats_version": "track2-gate-v1", "paper_only": True,
        "qualification_mode": "PROSPECTIVE_QUALIFYING",
    }
    value.update(overrides)
    return value


def create_session(tmp_path: Path, **registration_overrides) -> Path:
    TEST_NOW[0] = REGISTER_NOW
    session_dir = tmp_path / SESSION_DATE
    sources = session_dir / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    universe_path = sources / "universe.json"
    universe_path.write_bytes(sm._canonical_json(UNIVERSE))
    registration_overrides.setdefault("universe_sha256", file_hash(universe_path))
    write_attempt_record(
        session_dir.parent, SESSION_DATE, "PENDING",
        details={"qualification_mode": "PROSPECTIVE_QUALIFYING"}, recorded_at=REGISTER_NOW,
    )
    write_locked_preregistration(session_dir, preregistration(**registration_overrides))
    return session_dir


def make_order_spec():
    return {
        "strategy_rules_sha256": canonical_hash({"strategy": "15M_ORB", "risk_rs": 1500}),
        "symbol": "CDSL", "side": "BUY", "limit_price": 100.0, "quantity": 20,
    }


def build_evidence_bundle(
    session_dir: Path,
    evidence_class=EvidenceClass.E3_TICK_QUEUE,
    *,
    include_signal=False,
    include_second_signal=False,
):
    sources, ticks = session_dir / "sources", session_dir / "ticks"
    sources.mkdir(parents=True, exist_ok=True)
    ticks.mkdir(parents=True, exist_ok=True)
    reference_items, reference_hashes = [], {}
    roles = {"fno": "FNO", "surveillance": "SURVEILLANCE", "band": "BAND_POLICY"}
    for name in ("fno", "surveillance", "band"):
        path = sources / f"{name}.jsonl"
        path.write_text(json.dumps({"source": name, "session": SESSION_DATE}) + "\n", encoding="utf-8")
        digest = file_hash(path)
        reference_hashes[name] = digest
        reference_items.append({
            "path": f"sources/{name}.jsonl", "role": roles[name], "kind": "REFERENCE",
            "sha256": digest, "rows": 1, "first_ts": None, "last_ts": None,
        })
    universe_path = sources / "universe.json"
    universe_hash = file_hash(universe_path)
    reference_items.append({
        "path": "sources/universe.json", "role": "UNIVERSE", "kind": "REFERENCE",
        "sha256": universe_hash, "rows": 1, "first_ts": None, "last_ts": None,
    })
    tick_path = ticks / "CDSL.jsonl"
    start = datetime(2026, 9, 21, 9, 15, tzinfo=IST)
    end = datetime(2026, 9, 21, 15, 30, tzinfo=IST)
    current = start
    extra_rows = 0
    with open(tick_path, "w", encoding="utf-8") as stream:
        while current <= end:
            stream.write(json.dumps({
                "timestamp": current.isoformat(), "evidence_class": evidence_class.value,
                "event_type": "QUOTE", "symbol": "CDSL",
                "best_bid": 100.0, "best_ask": 100.05, "bid_qty": 100, "ask_qty": 400,
            }) + "\n")
            if include_signal and current == datetime(2026, 9, 21, 9, 31, tzinfo=IST):
                order_spec = make_order_spec()
                stream.write(json.dumps({
                    "timestamp": current.isoformat(), "evidence_class": evidence_class.value,
                    "event_type": "SIGNAL", "symbol": "CDSL", "signal_id": "signal-0001",
                    "recorded_at": current.isoformat(),
                    "order_spec": order_spec, "order_hash": canonical_hash(order_spec),
                }) + "\n")
                extra_rows += 1
            if include_second_signal and current == datetime(2026, 9, 21, 9, 32, tzinfo=IST):
                second_spec = {**make_order_spec(), "quantity": 21}
                stream.write(json.dumps({
                    "timestamp": current.isoformat(),
                    "recorded_at": current.isoformat(),
                    "evidence_class": evidence_class.value,
                    "event_type": "SIGNAL", "symbol": "CDSL", "signal_id": "signal-0002",
                    "order_spec": second_spec, "order_hash": canonical_hash(second_spec),
                }) + "\n")
                extra_rows += 1
            if current == datetime(2026, 9, 21, 9, 31, 5, tzinfo=IST):
                stream.write(json.dumps({
                    "timestamp": current.isoformat(), "evidence_class": evidence_class.value,
                    "event_type": "TRADE", "symbol": "CDSL", "trade_id": "trade-0001",
                    "price": 100.0, "quantity": 160,
                }) + "\n")
                extra_rows += 1
            current += timedelta(seconds=5)
    tick_digest = file_hash(tick_path)
    manifest = {
        "files": reference_items + [{
            "path": "ticks/CDSL.jsonl", "kind": "MARKET_STREAM",
            "sha256": tick_digest,
            "rows": int((end - start).total_seconds() / 5) + 1 + extra_rows,
            "first_ts": start.isoformat(), "last_ts": end.isoformat(),
        }],
        "max_gap_seconds": 5, "evidence_class_max": evidence_class.value, "complete": True,
    }
    preflight = {
        "fno_verified": True, "asm_gsm_clear": True, "band_check": True,
        "feed_health": True, "source_authenticated": True, "passed": True,
        "qualification_mode": "PROSPECTIVE_QUALIFYING",
        "checked_at": f"{SESSION_DATE}T09:10:00+05:30",
        "fno_source_sha256": reference_hashes["fno"],
        "surveillance_source_sha256": reference_hashes["surveillance"],
        "band_source_sha256": reference_hashes["band"],
        "universe_sha256": universe_hash,
    }
    TEST_NOW[0] = datetime(2026, 9, 21, 9, 12, tzinfo=IST)
    write_locked_preflight(session_dir, preflight)
    stream_bytes = tick_path.read_bytes()
    raw_lines = stream_bytes.splitlines(keepends=True)
    checkpoint_time = datetime(2026, 9, 21, 9, 20, tzinfo=IST)
    final_checkpoint_time = datetime(2026, 9, 21, 15, 30, tzinfo=IST)
    sequence = 1
    while checkpoint_time <= final_checkpoint_time:
        prefix_lines = [
            raw_line for raw_line in raw_lines
            if datetime.fromisoformat(json.loads(raw_line)["timestamp"]) <= checkpoint_time
        ]
        prefix_bytes = b"".join(prefix_lines)
        write_stream_checkpoint(
            session_dir.parent,
            SESSION_DATE,
            sequence=sequence,
            stream_sha256=hashlib.sha256(prefix_bytes).hexdigest(),
            row_count=len(prefix_lines),
            byte_count=len(prefix_bytes),
            recorded_at=checkpoint_time + timedelta(seconds=1),
        )
        sequence += 1
        checkpoint_time += timedelta(minutes=5)
    return preflight, manifest, tick_digest


def e3_fill(source_hash: str, **overrides):
    order_spec = make_order_spec()
    value = {
        "order_id": "paper-order-0001", "evidence_class": EvidenceClass.E3_TICK_QUEUE.value,
        "fill_state": FillState.FILLED.value, "order_spec": order_spec,
        "preregistered_order_hash": canonical_hash(order_spec),
        "signal_timestamp": f"{SESSION_DATE}T09:31:00+05:30",
        "order_arrival_timestamp": f"{SESSION_DATE}T09:31:00.500000+05:30",
        "volume_window_start_timestamp": f"{SESSION_DATE}T09:31:00.500000+05:30",
        "fill_timestamp": f"{SESSION_DATE}T09:31:05+05:30",
        "order_side": "BUY", "limit_price": 100.0, "order_qty": 20,
        "queue_rank": 100, "cum_volume_at_or_better": 160,
        "arrival_quote": {"best_bid": 100.0, "best_ask": 100.05, "bid_qty": 100, "ask_qty": 400},
        "capture_continuous": True, "source_data_sha256": source_hash,
        "full_cost_deduction": 12.5,
    }
    value.update(overrides)
    return value


def evaluate_valid(session_dir: Path, *, include_signal=False, **overrides):
    preflight, manifest, tick_digest = build_evidence_bundle(
        session_dir, include_signal=include_signal,
    )
    values = {
        "preflight": preflight, "data_manifest": manifest,
        "session_closed_at": f"{SESSION_DATE}T15:30:01+05:30",
    }
    values.update(overrides)
    TEST_NOW[0] = EVALUATE_NOW
    return evaluate_session(session_dir, **values), preflight, manifest, tick_digest


def test_zero_signal_valid_session_counts_toward_60_not_20(tmp_path):
    verdict, _, _, _ = evaluate_valid(create_session(tmp_path))
    assert verdict.status is SessionStatus.COUNTED
    assert (verdict.counts_toward_60, verdict.counts_toward_20) == (1, 0)


def test_after_open_preregistration_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="before the session's 09:15"):
        create_session(tmp_path, frozen_at=f"{SESSION_DATE}T09:15:00+05:30")


def test_backfilled_and_future_freezes_are_rejected(tmp_path):
    TEST_NOW[0] = datetime(2026, 9, 21, 10, 0, tzinfo=IST)
    with pytest.raises(ValueError, match="before the session opens"):
        write_locked_preregistration(tmp_path / SESSION_DATE, preregistration())
    TEST_NOW[0] = datetime(2026, 9, 21, 8, 0, tzinfo=IST)
    with pytest.raises(ValueError, match="later than the registration wall clock"):
        write_locked_preregistration(tmp_path / "future" / SESSION_DATE, preregistration())


def test_track_isolation_is_fail_closed(tmp_path):
    with pytest.raises(ValueError, match="TRACK2"):
        create_session(tmp_path, track_id="TRACK1")


def test_preregistration_is_write_once(tmp_path):
    session_dir = create_session(tmp_path)
    with pytest.raises(FileExistsError, match="immutable"):
        write_locked_preregistration(session_dir, preregistration())


def test_preregistration_tampering_is_detected(tmp_path):
    session_dir = create_session(tmp_path)
    path = session_dir / "preregistration.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["latency_ms"] = 0
    path.write_text(json.dumps(value), encoding="utf-8")
    record, errors = verify_locked_preregistration(session_dir)
    assert record is None
    assert errors == ["preregistration digest mismatch"]


def test_anchor_registry_tampering_is_detected(tmp_path):
    session_dir = create_session(tmp_path)
    registry = session_dir.parent / "preregistration_registry.jsonl"
    record = json.loads(registry.read_text(encoding="utf-8"))
    record["registered_at"] = "2026-09-21T09:14:59+05:30"
    registry.write_text(json.dumps(record) + "\n", encoding="utf-8")
    verified, errors = verify_locked_preregistration(session_dir)
    assert verified is None
    assert any("anchor registry hash mismatch" in error for error in errors)


def test_rechained_anchor_still_cannot_predate_freeze(tmp_path):
    session_dir = create_session(tmp_path)
    registry = session_dir.parent / "preregistration_registry.jsonl"
    record = json.loads(registry.read_text(encoding="utf-8"))
    record["registered_at"] = f"{SESSION_DATE}T08:59:59+05:30"
    previous_hash = record["previous_hash"]
    payload = {key: value for key, value in record.items() if key != "chain_hash"}
    record["chain_hash"] = hashlib.sha256(
        previous_hash.encode("ascii")
        + json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    registry.write_text(json.dumps(record) + "\n", encoding="utf-8")
    verified, errors = verify_locked_preregistration(session_dir)
    assert verified is None
    assert any("after freeze and before market open" in error for error in errors)


def test_public_clock_and_signal_count_injection_are_not_supported():
    evaluate_parameters = inspect.signature(evaluate_session).parameters
    registration_parameters = inspect.signature(write_locked_preregistration).parameters
    assert "signals" not in evaluate_parameters
    assert "now_ist" not in evaluate_parameters
    assert "now_ist" not in registration_parameters


@pytest.mark.parametrize("field", [
    "fno_verified", "asm_gsm_clear", "band_check", "feed_health", "source_authenticated", "passed"
])
def test_preflight_fields_fail_closed(field, tmp_path):
    session_dir = create_session(tmp_path)
    _, valid_preflight, manifest, _ = evaluate_valid(session_dir)
    valid_preflight[field] = False
    verdict = evaluate_session(
        session_dir, valid_preflight, manifest,
        session_closed_at=f"{SESSION_DATE}T15:30:01+05:30",
    )
    assert verdict.status is SessionStatus.VOID


def test_manifest_hash_is_recomputed_from_file(tmp_path):
    session_dir = create_session(tmp_path)
    _, preflight, manifest, _ = evaluate_valid(session_dir)
    (session_dir / "ticks" / "CDSL.jsonl").write_text("tampered\n", encoding="utf-8")
    verdict = evaluate_session(
        session_dir, preflight, manifest,
        session_closed_at=f"{SESSION_DATE}T15:30:01+05:30",
    )
    assert verdict.status is SessionStatus.VOID
    assert any("does not match file bytes" in reason for reason in verdict.void_reasons)


def test_preflight_source_hash_must_be_in_manifest(tmp_path):
    session_dir = create_session(tmp_path)
    _, preflight, manifest, _ = evaluate_valid(session_dir)
    preflight["fno_source_sha256"] = "9" * 64
    verdict = evaluate_session(
        session_dir, preflight, manifest,
        session_closed_at=f"{SESSION_DATE}T15:30:01+05:30",
    )
    assert verdict.status is SessionStatus.VOID


def test_missing_manifest_and_early_close_void_session(tmp_path):
    session_dir = create_session(tmp_path)
    _, preflight, manifest, _ = evaluate_valid(session_dir)
    bad_manifest = {**manifest, "files": []}
    verdict = evaluate_session(
        session_dir, preflight, bad_manifest,
        session_closed_at=f"{SESSION_DATE}T09:31:00+05:30",
    )
    assert verdict.status is SessionStatus.VOID


@pytest.mark.parametrize("evidence_class", [
    EvidenceClass.E0_SIGNAL_ONLY, EvidenceClass.E1_BAR_POSSIBLE, EvidenceClass.E2_MARKETABLE_DEPTH,
])
def test_non_e3_evidence_never_counts_as_fill(evidence_class):
    assert evidence_counts_toward_20({
        "evidence_class": evidence_class.value, "fill_state": FillState.FILLED.value,
        "queue_rank": 0, "order_qty": 10, "cum_volume_at_or_better": 1000,
    }) is False


def test_e3_fill_requires_queue_rank_plus_order_quantity():
    evidence = {
        "evidence_class": EvidenceClass.E3_TICK_QUEUE.value, "fill_state": FillState.FILLED.value,
        "queue_rank": 100, "order_qty": 20, "cum_volume_at_or_better": 119,
    }
    assert evidence_counts_toward_20(evidence) is False
    evidence["cum_volume_at_or_better"] = 120
    assert evidence_counts_toward_20(evidence) is True


def evaluate_with_fill(tmp_path, fill_changes=None, *, include_signal=True):
    session_dir = create_session(tmp_path)
    preflight, manifest, tick_hash = build_evidence_bundle(
        session_dir, include_signal=include_signal,
    )
    fill = e3_fill(tick_hash, **(fill_changes or {}))
    TEST_NOW[0] = EVALUATE_NOW
    return evaluate_session(
        session_dir, preflight, manifest,
        session_closed_at=f"{SESSION_DATE}T15:30:01+05:30", fill_evidence=[fill],
    )


def test_valid_e3_fill_advances_fill_counter(tmp_path):
    verdict = evaluate_with_fill(tmp_path)
    assert (verdict.counts_toward_60, verdict.counts_toward_20) == (1, 1)


@pytest.mark.parametrize("changes", [
    {"cum_volume_at_or_better": 159}, {"full_cost_deduction": 0},
    {"limit_price": "not-a-number"},
    {"cum_volume_at_or_better": 9999},
    {"arrival_quote": {"best_bid": 99.0, "best_ask": 100.05, "bid_qty": 100, "ask_qty": 400}},
    {"preregistered_order_hash": "9" * 64},
    {"signal_timestamp": f"{SESSION_DATE}T09:31:01+05:30"},
    {"order_arrival_timestamp": f"{SESSION_DATE}T09:31:00.499000+05:30"},
    {"volume_window_start_timestamp": f"{SESSION_DATE}T09:31:00.499000+05:30"},
    {"fill_timestamp": f"{SESSION_DATE}T09:30:59+05:30"},
])
def test_e3_fill_fails_closed_on_execution_proof_defects(changes, tmp_path):
    assert evaluate_with_fill(tmp_path, changes).counts_toward_20 == 0


def test_duplicate_normalized_order_id_cannot_be_double_counted(tmp_path):
    session_dir = create_session(tmp_path)
    preflight, manifest, tick_hash = build_evidence_bundle(session_dir, include_signal=True)
    first = e3_fill(tick_hash)
    second = e3_fill(tick_hash, order_id=" PAPER-ORDER-0001 ")
    TEST_NOW[0] = EVALUATE_NOW
    verdict = evaluate_session(
        session_dir, preflight, manifest,
        session_closed_at=f"{SESSION_DATE}T15:30:01+05:30", fill_evidence=[first, second],
    )
    assert verdict.counts_toward_20 == 1


def test_one_manifested_signal_cannot_back_multiple_fills(tmp_path):
    session_dir = create_session(tmp_path)
    preflight, manifest, tick_hash = build_evidence_bundle(
        session_dir, include_signal=True, include_second_signal=True,
    )
    first = e3_fill(tick_hash, order_id="paper-order-0001")
    second = e3_fill(tick_hash, order_id="paper-order-0002")
    TEST_NOW[0] = EVALUATE_NOW
    verdict = evaluate_session(
        session_dir, preflight, manifest,
        session_closed_at=f"{SESSION_DATE}T15:30:01+05:30", fill_evidence=[first, second],
    )
    assert verdict.signals == 2
    assert verdict.counts_toward_20 == 1


def test_fill_cannot_exist_without_a_signal(tmp_path):
    assert evaluate_with_fill(tmp_path, include_signal=False).counts_toward_20 == 0


def test_e3_fill_must_be_bound_to_manifested_source(tmp_path):
    assert evaluate_with_fill(tmp_path, {"source_data_sha256": "9" * 64}).counts_toward_20 == 0


def test_per_symbol_gap_cannot_be_hidden_by_other_symbol_events(tmp_path):
    session_dir = create_session(tmp_path)
    preflight, manifest, _ = build_evidence_bundle(session_dir, include_signal=True)
    tick_path = session_dir / "ticks" / "CDSL.jsonl"
    rows = [json.loads(line) for line in tick_path.read_text(encoding="utf-8").splitlines()]
    gap_timestamp = f"{SESSION_DATE}T10:00:00+05:30"
    for row in rows:
        if row["timestamp"] == gap_timestamp and row["event_type"] == "QUOTE":
            row["symbol"] = "FILLER"
            break
    tick_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    tick_hash = file_hash(tick_path)
    manifest["files"][-1]["sha256"] = tick_hash
    fill = e3_fill(tick_hash)
    TEST_NOW[0] = EVALUATE_NOW
    verdict = evaluate_session(
        session_dir, preflight, manifest,
        session_closed_at=f"{SESSION_DATE}T15:30:01+05:30", fill_evidence=[fill],
    )
    assert verdict.counts_toward_20 == 0


def test_missing_trade_id_cannot_inflate_turnover(tmp_path):
    session_dir = create_session(tmp_path)
    preflight, manifest, _ = build_evidence_bundle(session_dir, include_signal=True)
    tick_path = session_dir / "ticks" / "CDSL.jsonl"
    rows = [json.loads(line) for line in tick_path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        if row["event_type"] == "TRADE":
            row.pop("trade_id")
    tick_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    tick_hash = file_hash(tick_path)
    manifest["files"][-1]["sha256"] = tick_hash
    fill = e3_fill(tick_hash)
    TEST_NOW[0] = EVALUATE_NOW
    verdict = evaluate_session(
        session_dir, preflight, manifest,
        session_closed_at=f"{SESSION_DATE}T15:30:01+05:30", fill_evidence=[fill],
    )
    assert verdict.counts_toward_20 == 0


def test_locked_no_bid_never_counts_even_with_volume():
    assert evidence_counts_toward_20({
        "evidence_class": EvidenceClass.E3_TICK_QUEUE.value,
        "fill_state": FillState.LOCKED_NO_BID.value,
        "queue_rank": 0, "order_qty": 10, "cum_volume_at_or_better": 1000,
    }) is False


def test_legacy_session_is_never_counted(tmp_path):
    verdict = evaluate_session(
        tmp_path / "2026-09-16", {}, {}, session_closed_at="2026-09-16T15:30:00+05:30",
        legacy_unverified=True,
    )
    assert verdict.status is SessionStatus.PILOT_UNVERIFIED
    assert verdict.counts_toward_60 == verdict.counts_toward_20 == 0


def test_gate_counts_reject_duplicate_session_date(tmp_path):
    counted, _, _, _ = evaluate_valid(create_session(tmp_path))
    with pytest.raises(ValueError, match="duplicate counted session_date"):
        compute_gate_counts([counted, counted])


def test_gate_counts_are_computed_from_verdicts(tmp_path):
    counted, _, _, _ = evaluate_valid(create_session(tmp_path / "valid"))
    legacy = evaluate_session(
        tmp_path / "2026-09-16", {}, {}, session_closed_at="2026-09-16T15:30:00+05:30",
        legacy_unverified=True,
    )
    assert compute_gate_counts([counted, legacy]) == {
        "prospective_sessions": 1, "realistically_fillable_entries": 0,
        "void_sessions": 0, "legacy_unverified_sessions": 1,
        "rehearsal_sessions": 0,
    }
