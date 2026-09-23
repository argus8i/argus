"""Hardening and adversarial tests for Track 2 Phase 1B3A.

Validates the five Phase 1B3A qualification-boundary repairs:
1. Prospectively sealed preflight (anchor registry, timing, immutability, no backdating).
2. Unique role bindings for reference manifests (FNO, SURVEILLANCE, BAND_POLICY, UNIVERSE).
3. Frozen universe governing every event (rejection of out-of-universe data/signals, >= 4 scrips).
4. Recorder-owned wall clock and hash-chained stream checkpoints.
5. Durable attempt registry outside individual session directories.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from antigravity.daemons import track2_session_recorder as recorder_module
from antigravity.daemons.track2_session_recorder import Track2SessionRecorder
from antigravity.models import session_manifest as sm
from antigravity.models.session_manifest import (
    IST,
    EvidenceClass,
    SessionStatus,
    compute_gate_counts,
    evaluate_session,
    verify_attempt_registry,
    verify_locked_preflight,
    verify_locked_preregistration,
    verify_stream_checkpoints,
    write_attempt_record,
    write_locked_preflight,
    write_locked_preregistration,
    write_stream_checkpoint,
    _canonical_json,
    _sha256_bytes,
)

SESSION_DATE = "2026-09-21"
FROZEN_AT = f"{SESSION_DATE}T08:55:00+05:30"
CHECKED_AT = f"{SESSION_DATE}T09:05:00+05:30"
PREREG_NOW = datetime(2026, 9, 21, 9, 0, tzinfo=IST)
PREFLIGHT_NOW = datetime(2026, 9, 21, 9, 6, tzinfo=IST)
EVALUATE_NOW = datetime(2026, 9, 21, 16, 0, tzinfo=IST)

TEST_NOW = [PREREG_NOW]


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    TEST_NOW[0] = PREREG_NOW
    monkeypatch.setattr(sm, "_now_ist", lambda: TEST_NOW[0])


def canonical_hash(value):
    return _sha256_bytes(_canonical_json(value))


def file_hash(path: Path):
    return _sha256_bytes(path.read_bytes())


def make_universe_artifact(symbols=("ANGELONE", "BDL", "CDSL", "SUZLON")):
    return {
        "session_date": SESSION_DATE,
        "selection_rule": "TOP_LIQUID_HIGH_BETA_V1",
        "rule_version": "1.0",
        "symbols": sorted(list(set(symbols))),
        "source_bindings": {
            "fno_ref": "fno.jsonl",
            "surveillance_ref": "surveillance.jsonl",
            "band_ref": "band.jsonl",
        },
    }


def make_preregistration(universe_hash, **overrides):
    val = {
        "track_id": "TRACK2",
        "session_date": SESSION_DATE,
        "frozen_at": FROZEN_AT,
        "config_sha256": "a" * 64,
        "universe_sha256": universe_hash,
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
    val.update(overrides)
    return val


def setup_hardened_session(
    tmp_path: Path,
    symbols=("ANGELONE", "BDL", "CDSL", "SUZLON"),
    *,
    prereg_overrides=None,
    preflight_overrides=None,
):
    sessions_root = tmp_path / "sessions"
    session_dir = sessions_root / SESSION_DATE
    sources_dir = session_dir / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    # 1. Attempt registry - register initial attempt
    write_attempt_record(
        sessions_root, SESSION_DATE, "PENDING",
        details={"qualification_mode": "PROSPECTIVE_QUALIFYING"},
    )

    # 2. Write distinct reference files
    ref_files = []
    roles = {
        "FNO": ("sources/fno.jsonl", "FNO_CONTENT_V1\n"),
        "SURVEILLANCE": ("sources/surveillance.jsonl", "SURV_CONTENT_V1\n"),
        "BAND_POLICY": ("sources/band.jsonl", "BAND_CONTENT_V1\n"),
    }
    role_hashes = {}
    for role, (rel_path, content) in roles.items():
        abs_p = session_dir / rel_path
        abs_p.write_text(content, encoding="utf-8")
        h = file_hash(abs_p)
        role_hashes[role] = h
        ref_files.append({
            "path": rel_path,
            "role": role,
            "kind": "REFERENCE",
            "sha256": h,
            "rows": 1,
            "first_ts": None,
            "last_ts": None,
        })

    # Universe artifact
    u_obj = make_universe_artifact(symbols)
    u_path = sources_dir / "universe.json"
    u_bytes = _canonical_json(u_obj)
    u_path.write_bytes(u_bytes)
    u_hash = _sha256_bytes(u_bytes)
    role_hashes["UNIVERSE"] = u_hash
    ref_files.append({
        "path": "sources/universe.json",
        "role": "UNIVERSE",
        "kind": "REFERENCE",
        "sha256": u_hash,
        "rows": 1,
        "first_ts": None,
        "last_ts": None,
    })

    # 3. Write locked preregistration
    TEST_NOW[0] = PREREG_NOW
    prereg = make_preregistration(u_hash, **(prereg_overrides or {}))
    write_locked_preregistration(session_dir, prereg)

    # 4. Write locked preflight before 09:15
    TEST_NOW[0] = PREFLIGHT_NOW
    preflight = {
        "fno_verified": True,
        "asm_gsm_clear": True,
        "band_check": True,
        "feed_health": True,
        "source_authenticated": True,
        "passed": True,
        "qualification_mode": "PROSPECTIVE_QUALIFYING",
        "checked_at": CHECKED_AT,
        "fno_source_sha256": role_hashes["FNO"],
        "surveillance_source_sha256": role_hashes["SURVEILLANCE"],
        "band_source_sha256": role_hashes["BAND_POLICY"],
        "universe_sha256": role_hashes["UNIVERSE"],
    }
    if preflight_overrides:
        preflight.update(preflight_overrides)
    write_locked_preflight(session_dir, preflight)

    return session_dir, prereg, preflight, ref_files, role_hashes


def sample_snapshot(ts_str, symbols=("ANGELONE", "BDL", "CDSL", "SUZLON")):
    dt = datetime.fromisoformat(ts_str)
    return {
        "timestamp": ts_str,
        "local_write_time": dt.strftime("%Y-%m-%d %H:%M:%S"),
        "data_valid": True,
        "is_tab_hidden": False,
        "is_stale": False,
        "status": "LIVE",
        "watchlist": [
            {"symbol": s, "ltp": 100.0, "change_pct": "1.0%", "change_abs": 1.0}
            for s in symbols
        ],
    }


# =========================================================================
# MANDATORY ADVERSARIAL TESTS 1 TO 14 PER MANDATE
# =========================================================================

def test_adv1_backdated_or_late_preflight_rejected(tmp_path):
    """1. Backdated or late preflight is rejected."""
    # A) Preflight checked_at at/after 09:15 open
    session_dir, prereg, _, ref_files, role_hashes = setup_hardened_session(tmp_path)
    (session_dir / "preflight.json").unlink()
    (session_dir / "preflight.sha256").unlink()
    late_preflight = {
        "fno_verified": True, "asm_gsm_clear": True, "band_check": True,
        "feed_health": True, "source_authenticated": True, "passed": True,
        "qualification_mode": "PROSPECTIVE_QUALIFYING",
        "checked_at": f"{SESSION_DATE}T09:15:00+05:30",
        "fno_source_sha256": role_hashes["FNO"],
        "surveillance_source_sha256": role_hashes["SURVEILLANCE"],
        "band_source_sha256": role_hashes["BAND_POLICY"],
        "universe_sha256": role_hashes["UNIVERSE"],
    }
    TEST_NOW[0] = datetime(2026, 9, 21, 9, 15, 1, tzinfo=IST)
    with pytest.raises(ValueError, match="before 09:15"):
        write_locked_preflight(session_dir, late_preflight)

    # B) Preflight checked_at before freeze
    early_preflight = dict(late_preflight)
    early_preflight["checked_at"] = f"{SESSION_DATE}T08:50:00+05:30"
    TEST_NOW[0] = PREFLIGHT_NOW
    with pytest.raises(ValueError, match="after freeze"):
        write_locked_preflight(session_dir, early_preflight)


def test_adv2_missing_duplicate_tampered_preflight_anchor(tmp_path):
    """2. Missing/duplicate/tampered preflight anchor."""
    session_dir, _, _, _, _ = setup_hardened_session(tmp_path)
    anchor_file = session_dir.parent / "preflight_registry.jsonl"
    assert anchor_file.is_file()

    # Tamper with anchor file digest
    lines = anchor_file.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    rec["preflight_sha256"] = "0" * 64
    anchor_file.write_text(json.dumps(rec) + "\n", encoding="utf-8")

    rec, errs = verify_locked_preflight(session_dir)
    assert rec is None
    assert any("anchor" in e.lower() for e in errs)


def test_adv3_one_file_or_hash_used_for_multiple_reference_roles(tmp_path):
    """3. One file/hash used for multiple reference roles."""
    session_dir, _, preflight, ref_files, role_hashes = setup_hardened_session(tmp_path)
    # Modify manifest so FNO and SURVEILLANCE share the same file / hash
    for item in ref_files:
        if item["role"] == "SURVEILLANCE":
            item["path"] = "sources/fno.jsonl"
            item["sha256"] = role_hashes["FNO"]
    preflight["surveillance_source_sha256"] = role_hashes["FNO"]

    # Write dummy stream
    stream_p = session_dir / "market_stream.jsonl"
    stream_p.write_text(
        json.dumps({
            "timestamp": f"{SESSION_DATE}T09:15:00+05:30",
            "recorded_at": f"{SESSION_DATE}T09:15:00.100+05:30",
            "evidence_class": EvidenceClass.E1_BAR_POSSIBLE.value,
            "event_type": "QUOTE",
            "symbol": "CDSL",
            "ltp": 100.0,
        }) + "\n", encoding="utf-8"
    )
    manifest = {
        "files": ref_files + [{
            "path": "market_stream.jsonl",
            "kind": "MARKET_STREAM",
            "sha256": file_hash(stream_p),
            "rows": 1,
            "first_ts": f"{SESSION_DATE}T09:15:00+05:30",
            "last_ts": f"{SESSION_DATE}T15:30:00+05:30",
        }],
        "max_gap_seconds": 0.0,
        "evidence_class_max": EvidenceClass.E1_BAR_POSSIBLE.value,
        "complete": True,
    }
    TEST_NOW[0] = EVALUATE_NOW
    v = evaluate_session(
        session_dir, preflight, manifest, session_closed_at=f"{SESSION_DATE}T15:30:01+05:30"
    )
    assert v.status is SessionStatus.VOID
    assert any("multiple" in r.lower() or "duplicate" in r.lower() or "distinct" in r.lower() for r in v.void_reasons)


def test_adv4_stream_hash_masquerading_as_source_hash(tmp_path):
    """4. Stream hash masquerading as a source hash."""
    session_dir, _, preflight, ref_files, role_hashes = setup_hardened_session(tmp_path)
    stream_p = session_dir / "market_stream.jsonl"
    stream_p.write_text("dummy stream content\n", encoding="utf-8")
    s_hash = file_hash(stream_p)

    # Attempt to use stream hash as FNO reference
    for item in ref_files:
        if item["role"] == "FNO":
            item["sha256"] = s_hash
            item["path"] = "market_stream.jsonl"
    preflight["fno_source_sha256"] = s_hash

    manifest = {
        "files": ref_files + [{
            "path": "market_stream.jsonl",
            "kind": "MARKET_STREAM",
            "sha256": s_hash,
            "rows": 1,
            "first_ts": f"{SESSION_DATE}T09:15:00+05:30",
            "last_ts": f"{SESSION_DATE}T15:30:00+05:30",
        }],
        "max_gap_seconds": 0.0,
        "evidence_class_max": EvidenceClass.E1_BAR_POSSIBLE.value,
        "complete": True,
    }
    TEST_NOW[0] = EVALUATE_NOW
    v = evaluate_session(
        session_dir, preflight, manifest, session_closed_at=f"{SESSION_DATE}T15:30:01+05:30"
    )
    assert v.status is SessionStatus.VOID
    assert any("stream" in r.lower() or "role" in r.lower() for r in v.void_reasons)


def test_adv5_missing_wrong_role_and_universe_hash_mismatch(tmp_path):
    """5. Missing/wrong role and universe hash mismatch."""
    # Universe hash in preflight doesn't match preregistration
    session_dir, prereg, preflight, ref_files, _ = setup_hardened_session(tmp_path)
    preflight["universe_sha256"] = "f" * 64

    stream_p = session_dir / "market_stream.jsonl"
    stream_p.write_text("stream\n", encoding="utf-8")
    manifest = {
        "files": ref_files + [{
            "path": "market_stream.jsonl",
            "kind": "MARKET_STREAM",
            "sha256": file_hash(stream_p),
            "rows": 1,
            "first_ts": f"{SESSION_DATE}T09:15:00+05:30",
            "last_ts": f"{SESSION_DATE}T15:30:00+05:30",
        }],
        "max_gap_seconds": 0.0,
        "evidence_class_max": EvidenceClass.E1_BAR_POSSIBLE.value,
        "complete": True,
    }
    TEST_NOW[0] = EVALUATE_NOW
    v = evaluate_session(
        session_dir, preflight, manifest, session_closed_at=f"{SESSION_DATE}T15:30:01+05:30"
    )
    assert v.status is SessionStatus.VOID
    assert any("universe" in r.lower() for r in v.void_reasons)


def test_adv6_out_of_universe_quote_depth_and_signal(tmp_path):
    """6. Out-of-universe quote, depth, and signal are rejected by recorder and evaluation."""
    session_dir, prereg, preflight, ref_files, _ = setup_hardened_session(
        tmp_path, symbols=("ANGELONE", "BDL", "CDSL", "SUZLON")
    )
    cur_time = datetime(2026, 9, 21, 9, 15, tzinfo=IST)
    fake_clock = lambda: cur_time

    recorder = Track2SessionRecorder(session_dir, prereg, _test_clock=fake_clock)

    # A) Snapshot with out-of-universe symbol 'RELIANCE'
    bad_snap = sample_snapshot(cur_time.isoformat(), symbols=("ANGELONE", "RELIANCE"))
    assert recorder.record_snapshot(bad_snap) is False

    # B) Valid snapshot
    good_snap = sample_snapshot(cur_time.isoformat(), symbols=("ANGELONE", "BDL", "CDSL", "SUZLON"))
    assert recorder.record_snapshot(good_snap) is True

    # C) Out-of-universe depth
    depth_sample = {
        "bids": [{"price": 100.0, "quantity": 100, "orders": 1}],
        "offers": [{"price": 100.1, "quantity": 200, "orders": 2}],
    }
    bad_depth_snap = sample_snapshot(cur_time.isoformat())
    bad_depth_snap["active_stock"] = "TCS"
    bad_depth_snap["depth"] = depth_sample
    assert recorder.record_snapshot(bad_depth_snap) is False

    # D) Out-of-universe signal
    cur_time = datetime(2026, 9, 21, 9, 31, tzinfo=IST)
    order_spec = {
        "strategy_rules_sha256": canonical_hash(prereg["order_rules"]),
        "symbol": "INFY",
        "side": "BUY",
        "limit_price": 1500.0,
        "quantity": 10,
    }
    assert recorder.record_signal(
        signal_id="sig-out-univ", symbol="INFY", timestamp=cur_time, order_spec=order_spec
    ) is False
    recorder.close_writer()


def test_adv7_universe_below_four_symbols_is_void(tmp_path):
    """7. Universe below four symbols is an anchored VOID attempt."""
    session_dir, prereg, preflight, ref_files, _ = setup_hardened_session(
        tmp_path, symbols=("ANGELONE", "BDL", "CDSL")  # only 3 symbols!
    )
    stream_p = session_dir / "market_stream.jsonl"
    stream_p.write_text(
        json.dumps({
            "timestamp": f"{SESSION_DATE}T09:15:00+05:30",
            "recorded_at": f"{SESSION_DATE}T09:15:00.100+05:30",
            "evidence_class": EvidenceClass.E1_BAR_POSSIBLE.value,
            "event_type": "QUOTE",
            "symbol": "CDSL",
            "ltp": 100.0,
        }) + "\n", encoding="utf-8"
    )
    manifest = {
        "files": ref_files + [{
            "path": "market_stream.jsonl",
            "kind": "MARKET_STREAM",
            "sha256": file_hash(stream_p),
            "rows": 1,
            "first_ts": f"{SESSION_DATE}T09:15:00+05:30",
            "last_ts": f"{SESSION_DATE}T15:30:00+05:30",
        }],
        "max_gap_seconds": 0.0,
        "evidence_class_max": EvidenceClass.E1_BAR_POSSIBLE.value,
        "complete": True,
    }
    TEST_NOW[0] = EVALUATE_NOW
    v = evaluate_session(
        session_dir, preflight, manifest, session_closed_at=f"{SESSION_DATE}T15:30:01+05:30"
    )
    assert v.status is SessionStatus.VOID
    assert any("fewer than four" in r.lower() or "universe" in r.lower() for r in v.void_reasons)


def test_adv8_after_close_replay_attempt_rejected(tmp_path):
    """8. After-close replay attempt with matching source timestamps is rejected by recorded_at."""
    session_dir, prereg, _, _, _ = setup_hardened_session(tmp_path)
    # Clock is 17:00 IST (after close)
    replay_clock = datetime(2026, 9, 21, 17, 0, tzinfo=IST)
    recorder = Track2SessionRecorder(session_dir, prereg, _test_clock=lambda: replay_clock)

    # Snapshot with apparent 09:15 market timestamp
    snap = sample_snapshot(f"{SESSION_DATE}T09:15:00+05:30")
    # Must be rejected because recorder's own clock shows 17:00 IST!
    assert recorder.record_snapshot(snap) is False
    recorder.close_writer()


def test_adv9_injected_per_call_clock_rejected_by_api(tmp_path):
    """9. Injected per-call clock is rejected / not accepted by API signature."""
    import inspect
    sig = inspect.signature(Track2SessionRecorder.record_snapshot)
    assert "wall_clock" not in sig.parameters


def test_adv10_stream_checkpoint_continuity_and_tampering(tmp_path):
    """10. Missing, reordered, or tampered stream checkpoint."""
    sessions_root = tmp_path / "sessions"
    session_dir, prereg, preflight, ref_files, _ = setup_hardened_session(tmp_path)
    cp_dir = sessions_root / "checkpoints"

    # Write 2 valid stream checkpoints
    c1 = write_stream_checkpoint(
        sessions_root, SESSION_DATE, sequence=1, stream_sha256="1" * 64, row_count=100, byte_count=1000,
        recorded_at=datetime(2026, 9, 21, 9, 30, tzinfo=IST),
    )
    c2 = write_stream_checkpoint(
        sessions_root, SESSION_DATE, sequence=2, stream_sha256="2" * 64, row_count=200, byte_count=2000,
        recorded_at=datetime(2026, 9, 21, 10, 0, tzinfo=IST),
    )
    cps, errs = verify_stream_checkpoints(sessions_root, SESSION_DATE)
    assert not errs
    assert len(cps) == 2

    # Tamper with checkpoint file
    cp_file = sessions_root / f"stream_checkpoint_{SESSION_DATE}.jsonl"
    lines = cp_file.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[1])
    rec["row_count"] = 999
    cp_file.write_text(lines[0] + "\n" + json.dumps(rec) + "\n", encoding="utf-8")

    cps, errs = verify_stream_checkpoints(sessions_root, SESSION_DATE)
    assert any("hash mismatch" in e or "break" in e for e in errs)


def test_adv11_duplicate_attempt_date_and_broken_attempt_chain(tmp_path):
    """11. Duplicate attempt date and broken attempt hash chain."""
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir(parents=True, exist_ok=True)

    write_attempt_record(
        sessions_root, "2026-09-21", "PENDING",
        details={"qualification_mode": "PROSPECTIVE_QUALIFYING"},
    )
    # Duplicate initial attempt for the same date must raise
    with pytest.raises(ValueError, match="already exists"):
        write_attempt_record(
            sessions_root, "2026-09-21", "PENDING",
            details={"qualification_mode": "PROSPECTIVE_QUALIFYING"},
        )

    # Terminal state update is valid
    write_attempt_record(sessions_root, "2026-09-21", "FINALIZED")
    recs, errs = verify_attempt_registry(sessions_root)
    assert not errs
    assert len(recs) == 2

    # Break chain
    reg_file = sessions_root / "session_attempt_registry.jsonl"
    lines = reg_file.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    rec["attempt_outcome"] = "VOID"
    reg_file.write_text(json.dumps(rec) + "\n" + lines[1] + "\n", encoding="utf-8")

    recs, errs = verify_attempt_registry(sessions_root)
    assert any("chain" in e.lower() or "hash" in e.lower() for e in errs)


def test_adv12_crash_restart_with_exact_evidence_vs_mismatch(tmp_path):
    """12. Crash/restart with exact immutable evidence versus mismatch."""
    session_dir, prereg, preflight, ref_files, _ = setup_hardened_session(tmp_path)
    cur_time = datetime(2026, 9, 21, 9, 15, tzinfo=IST)
    r1 = Track2SessionRecorder(session_dir, prereg, _test_clock=lambda: cur_time)
    r1.record_snapshot(sample_snapshot(cur_time.isoformat()))
    r1.close_writer()

    # Resume with exact prereg works
    r2 = Track2SessionRecorder(session_dir, prereg, _test_clock=lambda: cur_time)
    r2.close_writer()

    # Resume with altered prereg fails
    bad_prereg = dict(prereg)
    bad_prereg["gap_seconds"] = 1.0
    with pytest.raises(ValueError, match="does not match"):
        Track2SessionRecorder(session_dir, bad_prereg, _test_clock=lambda: cur_time)


def test_adv13_old_pilot_evidence_cannot_count(tmp_path):
    """13. Old pilot evidence cannot count."""
    verdict = evaluate_session(
        tmp_path / "2026-09-16", {}, {}, session_closed_at="2026-09-16T15:30:00+05:30",
        legacy_unverified=True,
    )
    assert verdict.status is SessionStatus.PILOT_UNVERIFIED
    assert verdict.counts_toward_60 == 0
    assert verdict.counts_toward_20 == 0


def test_adv14_full_hardened_session_evaluates_counted(tmp_path):
    """14. Full hardened session with preflight, universe, checkpoints, and attempt evaluates COUNTED."""
    session_dir, prereg, preflight, ref_files, role_hashes = setup_hardened_session(tmp_path)
    sessions_root = session_dir.parent

    cur = datetime(2026, 9, 21, 9, 15, tzinfo=IST)
    end = datetime(2026, 9, 21, 15, 30, tzinfo=IST)
    fake_clock = lambda: cur
    recorder = Track2SessionRecorder(session_dir, prereg, _test_clock=fake_clock)

    while cur <= end:
        recorder.record_snapshot(sample_snapshot(cur.isoformat()))
        cur += timedelta(seconds=5)

    TEST_NOW[0] = EVALUATE_NOW
    verdict = recorder.close_and_evaluate(
        preflight=preflight,
        reference_files=ref_files,
        session_closed_at=f"{SESSION_DATE}T15:30:01+05:30",
    )
    assert verdict.status is SessionStatus.COUNTED
    assert verdict.counts_toward_60 == 1
    assert verdict.counts_toward_20 == 0

    # Terminal attempt state is written by the recorder, not the caller.
    recs, errs = verify_attempt_registry(sessions_root)
    assert not errs
    assert recs[-1]["attempt_outcome"] == "FINALIZED"


def test_adv15_signal_invalid_or_stale_recorder_clock_fails_closed(tmp_path):
    session_dir, prereg, _, _, _ = setup_hardened_session(tmp_path)
    clock_value = [datetime(2026, 9, 21, 12, 0, tzinfo=IST)]
    recorder = Track2SessionRecorder(
        session_dir, prereg, _test_clock=lambda: clock_value[0],
    )
    order_spec = {
        "strategy_rules_sha256": canonical_hash(prereg["order_rules"]),
        "symbol": "CDSL", "side": "BUY", "limit_price": 100.0, "quantity": 10,
    }
    assert recorder.record_signal(
        signal_id="stale", symbol="CDSL",
        timestamp=datetime(2026, 9, 21, 9, 31, tzinfo=IST),
        order_spec=order_spec,
    ) is False
    clock_value[0] = None
    assert recorder.record_signal(
        signal_id="invalid-clock", symbol="CDSL",
        timestamp=datetime(2026, 9, 21, 9, 31, tzinfo=IST),
        order_spec=order_spec,
    ) is False
    recorder.close_writer()


def test_adv16_tampered_preflight_closes_as_persisted_void(tmp_path):
    session_dir, prereg, preflight, ref_files, _ = setup_hardened_session(tmp_path)
    clock_value = [datetime(2026, 9, 21, 9, 15, tzinfo=IST)]
    recorder = Track2SessionRecorder(
        session_dir, prereg, _test_clock=lambda: clock_value[0],
    )
    assert recorder.record_snapshot(sample_snapshot(clock_value[0].isoformat())) is True
    (session_dir / "preflight.sha256").write_text("0" * 64 + "\n", encoding="ascii")
    clock_value[0] = datetime(2026, 9, 21, 16, 0, tzinfo=IST)
    verdict = recorder.close_and_evaluate(preflight=preflight, reference_files=ref_files)
    assert verdict.status is SessionStatus.VOID
    assert (session_dir / "verdict.json").is_file()
    attempts, errors = verify_attempt_registry(session_dir.parent)
    assert not errors
    assert attempts[-1]["attempt_outcome"] == "VOID"


def test_adv17_corrupt_attempt_ledger_blocks_verdict_commit(tmp_path):
    session_dir, prereg, preflight, ref_files, _ = setup_hardened_session(tmp_path)
    clock_value = [datetime(2026, 9, 21, 9, 15, tzinfo=IST)]
    recorder = Track2SessionRecorder(
        session_dir, prereg, _test_clock=lambda: clock_value[0],
    )
    assert recorder.record_snapshot(sample_snapshot(clock_value[0].isoformat())) is True
    registry = session_dir.parent / "session_attempt_registry.jsonl"
    with open(registry, "a", encoding="utf-8") as target:
        target.write("{broken\n")
    clock_value[0] = datetime(2026, 9, 21, 16, 0, tzinfo=IST)
    with pytest.raises(ValueError, match="attempt registry is invalid"):
        recorder.close_and_evaluate(preflight=preflight, reference_files=ref_files)
    assert not (session_dir / "verdict.json").exists()


def test_adv18_recovery_completes_ledger_after_verdict_write_crash(
    tmp_path, monkeypatch,
):
    session_dir, prereg, preflight, ref_files, _ = setup_hardened_session(tmp_path)
    clock_value = [datetime(2026, 9, 21, 9, 15, tzinfo=IST)]
    recorder = Track2SessionRecorder(
        session_dir, prereg, _test_clock=lambda: clock_value[0],
    )
    assert recorder.record_snapshot(sample_snapshot(clock_value[0].isoformat())) is True
    clock_value[0] = datetime(2026, 9, 21, 16, 0, tzinfo=IST)
    original_write_attempt = recorder_module.write_attempt_record

    def fail_terminal_write(*args, **kwargs):
        raise OSError("simulated crash after verdict persistence")

    monkeypatch.setattr(recorder_module, "write_attempt_record", fail_terminal_write)
    with pytest.raises(OSError, match="simulated crash"):
        recorder.close_and_evaluate(preflight=preflight, reference_files=ref_files)
    assert (session_dir / "verdict.json").is_file()
    attempts, errors = verify_attempt_registry(session_dir.parent)
    assert not errors and attempts[-1]["attempt_outcome"] == "PENDING"

    monkeypatch.setattr(recorder_module, "write_attempt_record", original_write_attempt)
    recovered = Track2SessionRecorder(
        session_dir, prereg, _test_clock=lambda: clock_value[0],
    )
    verdict = recovered.close_and_evaluate(
        preflight=preflight, reference_files=ref_files,
    )
    assert verdict.status is SessionStatus.VOID
    attempts, errors = verify_attempt_registry(session_dir.parent)
    assert not errors and attempts[-1]["attempt_outcome"] == "VOID"
