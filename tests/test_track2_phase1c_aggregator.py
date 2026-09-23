"""Adversarial tests for the read-only Track 2 Phase 1C accountant."""

from __future__ import annotations

import json
import hashlib
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from antigravity.daemons import track2_verdict_aggregator as aggregator
from antigravity.daemons import track2_session_recorder as recorder_module
from antigravity.daemons.track2_official_source_ingestor import (
    EXPECTED_ENDPOINTS,
    Track2OfficialSourceIngestor,
)
from antigravity.daemons.track2_session_coordinator import (
    CoordinatorState,
    Track2PaperSessionCoordinator,
)
from antigravity.models import session_manifest as manifest_module
from antigravity.models.session_manifest import (
    IST,
    SessionStatus,
    SessionVerdict,
    _canonical_json,
    _sha256_bytes,
    write_attempt_record,
)


REAL_SESSION_DATE = "2026-09-21"
REAL_SYMBOLS = ("ANGELONE", "BDL", "CDSL", "SUZLON")


def _verdict(session_date: str, *, order_ids=()) -> SessionVerdict:
    return SessionVerdict(
        track_id="TRACK2",
        session_date=session_date,
        status=SessionStatus.COUNTED,
        void_reasons=(),
        signals=max(1, len(order_ids)),
        qualifying_fills=len(order_ids),
        counts_toward_60=1,
        counts_toward_20=len(order_ids),
        qualifying_order_ids=tuple(order_ids),
    )


def _write_hashed(path: Path, value) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical_json(value)
    digest = _sha256_bytes(payload)
    path.write_bytes(payload)
    path.with_suffix(".sha256").write_text(digest + "\n", encoding="ascii")
    return digest


def _finalized_fixture(root: Path, verdict: SessionVerdict, *, bound_digest=None):
    write_attempt_record(
        root, verdict.session_date, "PENDING",
        details={"qualification_mode": "PROSPECTIVE_QUALIFYING"},
    )
    saved = {
        **verdict.to_dict(),
        "_session_closed_at": f"{verdict.session_date}T15:30:00+05:30",
        "_preflight_sha256": "a" * 64,
        "_manifest_sha256": "b" * 64,
        "_fill_evidence_sha256": _sha256_bytes(_canonical_json([])),
    }
    digest = _write_hashed(root / verdict.session_date / "verdict.json", saved)
    write_attempt_record(
        root,
        verdict.session_date,
        "FINALIZED",
        details={"verdict_sha256": bound_digest or digest},
    )
    return saved


def _real_policy(tmp_path: Path) -> Path:
    policy_dir = tmp_path / "policy"
    policy_dir.mkdir(parents=True)
    circulars = []
    for identifier, filename in (
        ("NSE/FAOP/62241", "FAOP62241.pdf"),
        ("NSE/FAOP/63405", "FAOP63405.pdf"),
    ):
        payload = f"test fixture {identifier}".encode()
        (policy_dir / filename).write_bytes(payload)
        circulars.append({
            "id": identifier,
            "url": f"https://nsearchives.nseindia.com/content/circulars/{filename}",
            "path": filename,
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
    policy_path = policy_dir / "band_policy.json"
    policy_path.write_text(json.dumps({
        "policy_id": "NSE_DYNAMIC_OPERATING_RANGE_V2",
        "effective_from": "2024-06-10",
        "market": "NSE_CASH",
        "applies_to": "ACTIVE_FNO_UNDERLYINGS",
        "circulars": circulars,
    }), encoding="utf-8")
    return policy_path


def _real_counted_session(tmp_path: Path, monkeypatch):
    clock = [datetime(2026, 9, 21, 8, 30, tzinfo=IST)]
    monkeypatch.setattr(manifest_module, "_now_ist", lambda: clock[0])
    monkeypatch.setattr(recorder_module, "_now_ist", lambda: clock[0])
    sessions_root = tmp_path / "real_sessions"
    surveillance_dir = tmp_path / "real_surveillance"
    payloads = {
        "asm": json.dumps({
            "shortterm": {"data": [{"symbol": "SBIN"}]},
            "longterm": {"data": [{"symbol": "TCS"}]},
        }).encode(),
        "gsm": json.dumps([{"symbol": "INFY"}]).encode(),
        "fno": json.dumps({
            "data": {"UnderlyingList": [{"symbol": symbol} for symbol in REAL_SYMBOLS]}
        }).encode(),
    }

    def fetcher(url):
        key = next(name for name, endpoint in EXPECTED_ENDPOINTS.items() if endpoint == url)
        return 200, payloads[key], {"content-type": "application/json"}

    ingestor = Track2OfficialSourceIngestor(
        surveillance_dir=str(surveillance_dir),
        fetcher=fetcher,
        now_fn=lambda: clock[0],
    )
    coordinator = Track2PaperSessionCoordinator(
        sessions_root=sessions_root,
        surveillance_dir=surveillance_dir,
        band_policy_path=_real_policy(tmp_path),
        candidates=REAL_SYMBOLS,
        config={
            "order_rules": {"strategy": "15M_ORB", "risk_rs": 1500},
            "cost_model": {"version": "v1", "gross_only_forbidden": True, "minimum_cost_rs": 1.0},
            "gap_seconds": 5.0,
            "queue_haircut": 0.25,
            "latency_ms": 500,
            "gate_stats_version": "track2-gate-v1",
        },
        ingestor=ingestor,
        feed_health_check=lambda: True,
        qualification_mode="PROSPECTIVE_QUALIFYING",
        _test_clock=lambda: clock[0],
    )
    prepared = coordinator.prepare(REAL_SESSION_DATE)
    assert prepared.state is CoordinatorState.RECORDING
    current = datetime(2026, 9, 21, 9, 15, tzinfo=IST)
    end = datetime(2026, 9, 21, 15, 30, tzinfo=IST)
    while current <= end:
        clock[0] = current
        assert coordinator.record_snapshot({
            "timestamp": current.isoformat(),
            "local_write_time": current.strftime("%Y-%m-%d %H:%M:%S"),
            "data_valid": True,
            "is_tab_hidden": False,
            "is_stale": False,
            "status": "LIVE",
            "watchlist": [
                {"symbol": symbol, "ltp": 100.0, "change_pct": "1.0%", "change_abs": 1.0}
                for symbol in REAL_SYMBOLS
            ],
        }) is True
        current += timedelta(seconds=5)
    clock[0] = datetime(2026, 9, 21, 16, 0, tzinfo=IST)
    verdict = coordinator.finalize()
    assert verdict.status is SessionStatus.COUNTED
    return sessions_root, clock


def _rewrite_verdict_and_ledger(root: Path, saved: dict, *, pending_at: datetime, terminal_at: datetime):
    verdict_path = root / REAL_SESSION_DATE / "verdict.json"
    digest = _write_hashed(verdict_path, saved)
    registry = root / "session_attempt_registry.jsonl"
    registry.unlink()
    write_attempt_record(
        root, REAL_SESSION_DATE, "PENDING",
        details={"qualification_mode": "PROSPECTIVE_QUALIFYING"}, recorded_at=pending_at,
    )
    write_attempt_record(
        root, REAL_SESSION_DATE, "FINALIZED",
        details={"verdict_sha256": digest}, recorded_at=terminal_at,
    )


def test_empty_root_reports_trusted_zero_gate(tmp_path):
    report = aggregator.build_gate_report(tmp_path / "sessions")
    assert report.trusted is True
    assert report.prospective_sessions == 0
    assert report.realistically_fillable_entries == 0
    assert report.gate_passed is False


def test_verified_finalized_session_counts_from_reproduced_verdict(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    verdict = _verdict("2026-09-21", order_ids=("ORDER-1",))
    _finalized_fixture(root, verdict)
    monkeypatch.setattr(aggregator, "_reproduce_verdict", lambda *_: verdict)
    report = aggregator.build_gate_report(root)
    assert report.trusted is True
    assert (report.prospective_sessions, report.realistically_fillable_entries) == (1, 1)
    assert report.sessions[0].trusted is True


def test_real_reproduction_and_binding_tamper_cases(tmp_path, monkeypatch):
    root, clock = _real_counted_session(tmp_path, monkeypatch)
    valid_report = aggregator.build_gate_report(root)
    assert valid_report.trusted is True
    assert valid_report.prospective_sessions == 1
    verdict_path = root / REAL_SESSION_DATE / "verdict.json"
    original_saved = json.loads(verdict_path.read_text(encoding="utf-8"))
    pending_at = datetime(2026, 9, 21, 8, 30, tzinfo=IST)
    terminal_at = datetime(2026, 9, 21, 16, 0, tzinfo=IST)

    wrong_preflight = dict(original_saved)
    wrong_preflight["_preflight_sha256"] = "0" * 64
    _rewrite_verdict_and_ledger(
        root, wrong_preflight, pending_at=pending_at, terminal_at=terminal_at,
    )
    report = aggregator.build_gate_report(root)
    assert report.trusted is False
    assert any("preflight binding mismatch" in item for item in report.integrity_errors)

    wrong_manifest = dict(original_saved)
    wrong_manifest["_manifest_sha256"] = "0" * 64
    _rewrite_verdict_and_ledger(
        root, wrong_manifest, pending_at=pending_at, terminal_at=terminal_at,
    )
    report = aggregator.build_gate_report(root)
    assert report.trusted is False
    assert any("manifest binding mismatch" in item for item in report.integrity_errors)

    missing_close = dict(original_saved)
    missing_close.pop("_session_closed_at")
    _rewrite_verdict_and_ledger(
        root, missing_close, pending_at=pending_at, terminal_at=terminal_at,
    )
    report = aggregator.build_gate_report(root)
    assert report.trusted is False
    assert any("closure provenance" in item for item in report.integrity_errors)

    _rewrite_verdict_and_ledger(
        root, original_saved, pending_at=pending_at, terminal_at=terminal_at,
    )
    _write_hashed(root / REAL_SESSION_DATE / "fill_evidence.json", [{}])
    report = aggregator.build_gate_report(root)
    assert report.trusted is False
    assert any("fill-evidence binding mismatch" in item for item in report.integrity_errors)
    assert clock[0] == terminal_at


def test_pending_and_void_attempts_are_visible_but_never_counted(tmp_path):
    root = tmp_path / "sessions"
    write_attempt_record(
        root, "2026-09-20", "PENDING",
        details={"qualification_mode": "PROSPECTIVE_QUALIFYING"},
    )
    write_attempt_record(root, "2026-09-20", "VOID", details={"reason": "source outage"})
    write_attempt_record(
        root, "2026-09-21", "PENDING",
        details={"qualification_mode": "PROSPECTIVE_QUALIFYING"},
    )
    report = aggregator.build_gate_report(root)
    assert report.trusted is True
    assert report.pending_attempts == 1
    assert report.void_attempts == 1
    assert report.prospective_sessions == report.realistically_fillable_entries == 0


def test_tampered_verdict_digest_forces_global_zero(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    verdict = _verdict("2026-09-21")
    _finalized_fixture(root, verdict)
    (root / verdict.session_date / "verdict.json").write_bytes(b"{}")
    monkeypatch.setattr(aggregator, "_reproduce_verdict", lambda *_: verdict)
    report = aggregator.build_gate_report(root)
    assert report.trusted is False
    assert report.prospective_sessions == 0
    assert "digest mismatch" in report.integrity_errors[0]


def test_terminal_record_must_bind_exact_verdict_hash(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    verdict = _verdict("2026-09-21")
    _finalized_fixture(root, verdict, bound_digest="0" * 64)
    monkeypatch.setattr(aggregator, "_reproduce_verdict", lambda *_: verdict)
    report = aggregator.build_gate_report(root)
    assert report.trusted is False
    assert "exact verdict" in report.integrity_errors[0]


def test_unexpected_verdict_field_forces_global_zero(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    verdict = _verdict("2026-09-21")
    saved = _finalized_fixture(root, verdict)
    saved["manual_profit_rs"] = 999999
    pending_at = datetime(2026, 9, 21, 8, 30, tzinfo=IST)
    terminal_at = datetime(2026, 9, 21, 16, 0, tzinfo=IST)
    _rewrite_verdict_and_ledger(
        root, saved, pending_at=pending_at, terminal_at=terminal_at,
    )
    monkeypatch.setattr(aggregator, "_reproduce_verdict", lambda *_: verdict)
    report = aggregator.build_gate_report(root)
    assert report.trusted is False
    assert report.prospective_sessions == 0
    assert "unexpected fields" in report.integrity_errors[0]


def test_corrupt_global_attempt_registry_forces_zero(tmp_path):
    root = tmp_path / "sessions"
    write_attempt_record(
        root, "2026-09-21", "PENDING",
        details={"qualification_mode": "PROSPECTIVE_QUALIFYING"},
    )
    registry = root / "session_attempt_registry.jsonl"
    with open(registry, "a", encoding="utf-8") as target:
        target.write("{broken\n")
    report = aggregator.build_gate_report(root)
    assert report.trusted is False
    assert report.prospective_sessions == report.realistically_fillable_entries == 0
    assert any("invalid JSON" in reason for reason in report.integrity_errors)


def test_duplicate_order_id_across_sessions_forces_global_zero(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    verdicts = {
        date: _verdict(date, order_ids=("SAME-ORDER",))
        for date in ("2026-09-20", "2026-09-21")
    }
    for verdict in verdicts.values():
        _finalized_fixture(root, verdict)

    def reproduce(session_dir, _saved):
        return verdicts[session_dir.name]

    monkeypatch.setattr(aggregator, "_reproduce_verdict", reproduce)
    report = aggregator.build_gate_report(root)
    assert report.trusted is False
    assert report.prospective_sessions == report.realistically_fillable_entries == 0
    assert "duplicate qualifying order_id" in report.integrity_errors[-1]


def test_orphan_session_directory_is_visible_and_forces_zero(tmp_path):
    root = tmp_path / "sessions"
    (root / "2026-09-21").mkdir(parents=True)
    report = aggregator.build_gate_report(root)
    assert report.trusted is False
    assert report.orphan_directories == ("2026-09-21",)
    assert report.prospective_sessions == 0


def test_unexpected_root_file_is_visible_and_forces_zero(tmp_path):
    root = tmp_path / "sessions"
    root.mkdir()
    (root / "rogue.json").write_text("{}", encoding="utf-8")
    report = aggregator.build_gate_report(root)
    assert report.trusted is False
    assert report.unexpected_root_entries == ("rogue.json",)
    assert report.prospective_sessions == 0


def test_derived_report_is_atomic_and_cannot_enter_evidence_tree(tmp_path):
    root = tmp_path / "sessions"
    report = aggregator.build_gate_report(root)
    output = tmp_path / "status" / "track2_gate_status.json"
    aggregator.write_derived_report(report, output, sessions_root=root)
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["track_id"] == "TRACK2"
    assert saved["prospective_sessions"] == 0
    with pytest.raises(ValueError, match="inside session evidence"):
        aggregator.write_derived_report(
            report, root / "status.json", sessions_root=root,
        )


def test_module_has_no_broker_or_trade_log_capability():
    source = Path("antigravity/daemons/track2_verdict_aggregator.py").read_text(
        encoding="utf-8",
    ).lower()
    forbidden = (
        "place_order", "enctoken", "api.kite.trade", "requests.post",
        "03_trade_log", "track1_esm",
    )
    assert not any(token in source for token in forbidden)
