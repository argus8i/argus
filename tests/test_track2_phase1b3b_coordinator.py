"""Adversarial integration tests for the Track 2 Phase 1B3B coordinator."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

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
from antigravity.models.session_manifest import IST, SessionStatus, verify_attempt_registry


SESSION_DATE = "2026-09-22"
SYMBOLS = ("ANGELONE", "BDL", "CDSL", "SUZLON", "IREDA")


def _config():
    return {
        "order_rules": {"strategy": "15M_ORB", "risk_rs": 1500},
        "cost_model": {"version": "v1", "gross_only_forbidden": True, "minimum_cost_rs": 1.0},
        "gap_seconds": 5.0,
        "queue_haircut": 0.25,
        "latency_ms": 500,
        "gate_stats_version": "track2-gate-v1",
    }


def _raw_payloads(*, blocked=()):
    blocked = list(blocked)
    return {
        "asm": json.dumps({
            "shortterm": {"data": [{"symbol": symbol} for symbol in blocked] or [{"symbol": "SBIN"}]},
            "longterm": {"data": [{"symbol": "TCS"}]},
        }, sort_keys=True).encode(),
        "gsm": json.dumps([{"symbol": "INFY"}], sort_keys=True).encode(),
        "fno": json.dumps({
            "data": {"UnderlyingList": [{"symbol": symbol} for symbol in SYMBOLS]}
        }, sort_keys=True).encode(),
    }


def _make_ingestor(surveillance_dir: Path, clock, *, blocked=(), fail=False, source_clock=None):
    payloads = _raw_payloads(blocked=blocked)

    def fetcher(url):
        if fail:
            raise OSError("simulated source outage")
        key = next(name for name, endpoint in EXPECTED_ENDPOINTS.items() if endpoint == url)
        return 200, payloads[key], {"content-type": "application/json"}

    return Track2OfficialSourceIngestor(
        surveillance_dir=str(surveillance_dir),
        fetcher=fetcher,
        now_fn=lambda: source_clock[0] if source_clock is not None else clock[0],
    )


def _make_policy(tmp_path: Path, *, corrupt=False) -> Path:
    policy_dir = tmp_path / "policy"
    policy_dir.mkdir(parents=True, exist_ok=True)
    circulars = []
    for identifier, filename in (
        ("NSE/FAOP/62241", "FAOP62241.pdf"),
        ("NSE/FAOP/63405", "FAOP63405.pdf"),
    ):
        payload = f"fixture bytes for {identifier}".encode()
        circular_path = policy_dir / filename
        circular_path.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        if corrupt and identifier.endswith("63405"):
            digest = "0" * 64
        circulars.append({
            "id": identifier,
            "url": f"https://nsearchives.nseindia.com/content/circulars/{filename}",
            "path": filename,
            "sha256": digest,
        })
    policy = {
        "policy_id": "NSE_DYNAMIC_OPERATING_RANGE_V2",
        "effective_from": "2024-06-10",
        "market": "NSE_CASH",
        "applies_to": "ACTIVE_FNO_UNDERLYINGS",
        "circulars": circulars,
    }
    policy_path = policy_dir / "band_policy.json"
    policy_path.write_text(json.dumps(policy, sort_keys=True), encoding="utf-8")
    return policy_path


@pytest.fixture
def coordinator_env(tmp_path, monkeypatch):
    clock = [datetime(2026, 9, 22, 8, 30, tzinfo=IST)]
    monkeypatch.setattr(manifest_module, "_now_ist", lambda: clock[0])
    monkeypatch.setattr(recorder_module, "_now_ist", lambda: clock[0])
    sessions_root = tmp_path / "sessions"
    surveillance_dir = tmp_path / "surveillance"
    policy_path = _make_policy(tmp_path)

    def build(
        *, candidates=SYMBOLS, blocked=(), fail=False, feed_ok=True,
        policy=policy_path, source_clock=None,
        qualification_mode="PROSPECTIVE_QUALIFYING", config=None, **extra,
    ):
        return Track2PaperSessionCoordinator(
            sessions_root=sessions_root,
            surveillance_dir=surveillance_dir,
            band_policy_path=policy,
            candidates=candidates,
            config=_config() if config is None else config,
            ingestor=_make_ingestor(
                surveillance_dir, clock, blocked=blocked, fail=fail, source_clock=source_clock,
            ),
            feed_health_check=lambda: feed_ok,
            qualification_mode=qualification_mode,
            _test_clock=lambda: clock[0],
            **extra,
        )

    return clock, sessions_root, build


def _snapshot(at: datetime, symbols):
    return {
        "timestamp": at.isoformat(),
        "local_write_time": at.strftime("%Y-%m-%d %H:%M:%S"),
        "data_valid": True,
        "is_tab_hidden": False,
        "is_stale": False,
        "status": "LIVE",
        "watchlist": [
            {"symbol": symbol, "ltp": 100.0, "change_pct": "1.0%", "change_abs": 1.0}
            for symbol in symbols
        ],
    }


def test_prepare_builds_four_role_locked_paper_session(coordinator_env):
    _, sessions_root, build = coordinator_env
    coordinator = build()
    result = coordinator.prepare(SESSION_DATE)
    assert result.state is CoordinatorState.RECORDING
    assert result.eligible_symbols == tuple(sorted(SYMBOLS))
    session_dir = sessions_root / SESSION_DATE
    for name in ("fno.json", "surveillance.json", "band_policy.json", "universe.json"):
        assert (session_dir / "sources" / name).is_file()
    assert (session_dir / "preregistration.json").is_file()
    assert (session_dir / "preflight.json").is_file()


@pytest.mark.parametrize("failure_kind", ["source", "feed", "policy"])
def test_prepare_failure_is_terminal_visible_void(coordinator_env, tmp_path, failure_kind):
    _, sessions_root, build = coordinator_env
    kwargs = {}
    if failure_kind == "source":
        kwargs["fail"] = True
    elif failure_kind == "feed":
        kwargs["feed_ok"] = False
    else:
        kwargs["policy"] = _make_policy(tmp_path / "bad", corrupt=True)
    result = build(**kwargs).prepare(SESSION_DATE)
    assert result.state is CoordinatorState.VOID
    attempts, errors = verify_attempt_registry(sessions_root)
    assert not errors
    assert [record["attempt_outcome"] for record in attempts] == ["PENDING", "VOID"]


def test_surveillance_filter_below_four_symbols_is_void(coordinator_env):
    _, sessions_root, build = coordinator_env
    result = build(blocked=("CDSL", "SUZLON")).prepare(SESSION_DATE)
    assert result.state is CoordinatorState.VOID
    assert "fewer than four" in str(result.reason)
    attempts, errors = verify_attempt_registry(sessions_root)
    assert not errors and attempts[-1]["attempt_outcome"] == "VOID"


def test_duplicate_daily_prepare_cannot_create_second_attempt(coordinator_env):
    _, sessions_root, build = coordinator_env
    first = build().prepare(SESSION_DATE)
    assert first.state is CoordinatorState.RECORDING
    second = build().prepare(SESSION_DATE)
    assert second.state is CoordinatorState.VOID
    attempts, errors = verify_attempt_registry(sessions_root)
    assert not errors
    assert sum(record["attempt_outcome"] == "PENDING" for record in attempts) == 1
    assert [record["attempt_outcome"] for record in attempts] == ["PENDING"]


@pytest.mark.parametrize("alias", ["20260922", "2026-W39-2"])
def test_noncanonical_date_alias_cannot_create_attempt(coordinator_env, alias):
    _, sessions_root, build = coordinator_env
    result = build().prepare(alias)
    assert result.state is CoordinatorState.VOID
    attempts, errors = verify_attempt_registry(sessions_root)
    assert not errors and attempts == []


def test_public_clock_override_is_rejected(coordinator_env):
    _, _, build = coordinator_env
    with pytest.raises(TypeError, match="public now_fn"):
        build(now_fn=lambda: datetime(2026, 9, 22, 8, 0, tzinfo=IST))


def test_source_clock_forgery_is_terminal_void(coordinator_env):
    clock, sessions_root, build = coordinator_env
    forged_clock = [clock[0] - timedelta(hours=1)]
    result = build(source_clock=forged_clock).prepare(SESSION_DATE)
    assert result.state is CoordinatorState.VOID
    assert "clock differs" in str(result.reason)
    attempts, errors = verify_attempt_registry(sessions_root)
    assert not errors and attempts[-1]["attempt_outcome"] == "VOID"


def test_malformed_snapshot_sources_is_terminal_void(coordinator_env):
    _, sessions_root, build = coordinator_env
    coordinator = build()
    original_ingest = coordinator.ingestor.ingest_session

    def malformed_ingest(session_date):
        result = original_ingest(session_date)
        snapshot_path = Path(result["snapshot_path"])
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        snapshot["sources"] = []
        snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
        return result

    coordinator.ingestor.ingest_session = malformed_ingest
    result = coordinator.prepare(SESSION_DATE)
    assert result.state is CoordinatorState.VOID
    assert "sources must be an object" in str(result.reason)
    attempts, errors = verify_attempt_registry(sessions_root)
    assert not errors and attempts[-1]["attempt_outcome"] == "VOID"


def test_early_finalize_is_refused_without_terminalizing(coordinator_env):
    clock, sessions_root, build = coordinator_env
    coordinator = build(candidates=SYMBOLS[:4])
    assert coordinator.prepare(SESSION_DATE).state is CoordinatorState.RECORDING
    clock[0] = datetime(2026, 9, 22, 15, 29, 59, tzinfo=IST)
    with pytest.raises(RuntimeError, match="before 15:30"):
        coordinator.finalize()
    attempts, errors = verify_attempt_registry(sessions_root)
    assert not errors and [record["attempt_outcome"] for record in attempts] == ["PENDING"]


def test_cross_date_finalize_is_refused_without_terminalizing(coordinator_env):
    clock, sessions_root, build = coordinator_env
    coordinator = build(candidates=SYMBOLS[:4])
    assert coordinator.prepare(SESSION_DATE).state is CoordinatorState.RECORDING
    clock[0] = datetime(2026, 9, 23, 16, 0, tzinfo=IST)
    with pytest.raises(RuntimeError, match="own session date"):
        coordinator.finalize()
    attempts, errors = verify_attempt_registry(sessions_root)
    assert not errors and [record["attempt_outcome"] for record in attempts] == ["PENDING"]


def test_exclusive_writer_lock_blocks_concurrent_resume(coordinator_env):
    _, sessions_root, build = coordinator_env
    first = build(candidates=SYMBOLS[:4])
    assert first.prepare(SESSION_DATE).state is CoordinatorState.RECORDING
    second = build(candidates=SYMBOLS[:4])
    result = second.resume(SESSION_DATE)
    assert result.state is CoordinatorState.VOID
    assert "already owns" in str(result.reason)
    attempts, errors = verify_attempt_registry(sessions_root)
    assert not errors and [record["attempt_outcome"] for record in attempts] == ["PENDING"]


def test_full_zero_signal_session_finalizes_counted(coordinator_env):
    clock, _, build = coordinator_env
    coordinator = build(candidates=SYMBOLS[:4])
    prepared = coordinator.prepare(SESSION_DATE)
    assert prepared.state is CoordinatorState.RECORDING
    current = datetime(2026, 9, 22, 9, 15, tzinfo=IST)
    end = datetime(2026, 9, 22, 15, 30, tzinfo=IST)
    while current <= end:
        clock[0] = current
        assert coordinator.record_snapshot(_snapshot(current, prepared.eligible_symbols)) is True
        current += timedelta(seconds=5)
    clock[0] = datetime(2026, 9, 22, 16, 0, tzinfo=IST)
    verdict = coordinator.finalize()
    assert verdict.status is SessionStatus.COUNTED
    assert verdict.counts_toward_60 == 1
    assert verdict.counts_toward_20 == 0
    attempts, errors = verify_attempt_registry(coordinator.sessions_root)
    assert not errors and attempts[-1]["attempt_outcome"] == "FINALIZED"


def test_crash_resume_requires_exact_locked_evidence(coordinator_env):
    clock, sessions_root, build = coordinator_env
    first = build(candidates=SYMBOLS[:4])
    prepared = first.prepare(SESSION_DATE)
    assert prepared.state is CoordinatorState.RECORDING
    clock[0] = datetime(2026, 9, 22, 9, 15, tzinfo=IST)
    assert first.record_snapshot(_snapshot(clock[0], prepared.eligible_symbols)) is True
    first.suspend_for_restart()

    clock[0] = datetime(2026, 9, 22, 9, 15, 5, tzinfo=IST)
    resumed = build(candidates=SYMBOLS[:4])
    result = resumed.resume(SESSION_DATE)
    assert result.state is CoordinatorState.RECORDING
    assert resumed.record_snapshot(_snapshot(clock[0], result.eligible_symbols)) is True
    resumed.recorder.close_writer()
    attempts, errors = verify_attempt_registry(sessions_root)
    assert not errors
    assert [record["attempt_outcome"] for record in attempts] == ["PENDING"]


def test_resume_rejects_tampered_reference_and_voids_attempt(coordinator_env):
    _, sessions_root, build = coordinator_env
    first = build(candidates=SYMBOLS[:4])
    assert first.prepare(SESSION_DATE).state is CoordinatorState.RECORDING
    first.suspend_for_restart()
    fno_path = sessions_root / SESSION_DATE / "sources" / "fno.json"
    fno_path.write_bytes(fno_path.read_bytes() + b" ")
    resumed = build(candidates=SYMBOLS[:4])
    result = resumed.resume(SESSION_DATE)
    assert result.state is CoordinatorState.VOID
    assert "digest differs" in str(result.reason)
    attempts, errors = verify_attempt_registry(sessions_root)
    assert not errors and attempts[-1]["attempt_outcome"] == "VOID"


def test_finalize_exception_is_visible_terminal_void(coordinator_env):
    clock, sessions_root, build = coordinator_env
    coordinator = build(candidates=SYMBOLS[:4])
    prepared = coordinator.prepare(SESSION_DATE)
    clock[0] = datetime(2026, 9, 22, 9, 15, tzinfo=IST)
    assert coordinator.record_snapshot(_snapshot(clock[0], prepared.eligible_symbols)) is True
    coordinator.recorder.close_writer()
    with open(coordinator.recorder.stream_path, "ab") as target:
        target.write(b"{malformed\n")
    clock[0] = datetime(2026, 9, 22, 16, 0, tzinfo=IST)
    verdict = coordinator.finalize()
    assert verdict.status is SessionStatus.VOID
    attempts, errors = verify_attempt_registry(sessions_root)
    assert not errors and attempts[-1]["attempt_outcome"] == "VOID"


def test_module_has_no_broker_or_counter_capability():
    source = Path("antigravity/daemons/track2_session_coordinator.py").read_text(encoding="utf-8").lower()
    forbidden = ("place_order", "enctoken", "api.kite.trade", "requests.post", "track2_live_radar")
    assert not any(token in source for token in forbidden)


def test_rehearsal_full_session_is_valid_but_never_counts(coordinator_env):
    from antigravity.daemons.track2_verdict_aggregator import build_gate_report

    clock, sessions_root, build = coordinator_env
    coordinator = build(
        candidates=SYMBOLS[:4], qualification_mode="REHEARSAL",
    )
    prepared = coordinator.prepare(SESSION_DATE)
    assert prepared.state is CoordinatorState.RECORDING
    current = datetime(2026, 9, 22, 9, 15, tzinfo=IST)
    end = datetime(2026, 9, 22, 15, 30, tzinfo=IST)
    while current <= end:
        clock[0] = current
        assert coordinator.record_snapshot(
            _snapshot(current, prepared.eligible_symbols)
        ) is True
        current += timedelta(seconds=5)
    clock[0] = datetime(2026, 9, 22, 16, 0, tzinfo=IST)
    verdict = coordinator.finalize()
    assert coordinator.state is CoordinatorState.REHEARSAL
    assert verdict.status is SessionStatus.REHEARSAL
    assert verdict.counts_toward_60 == verdict.counts_toward_20 == 0
    attempts, errors = verify_attempt_registry(sessions_root)
    assert not errors and attempts[-1]["attempt_outcome"] == "REHEARSAL"

    rehearsal_report = build_gate_report(
        sessions_root, expected_qualification_mode="REHEARSAL",
    )
    assert rehearsal_report.trusted is True
    assert rehearsal_report.rehearsal_attempts == 1
    assert rehearsal_report.prospective_sessions == 0
    assert rehearsal_report.realistically_fillable_entries == 0
    assert rehearsal_report.gate_passed is False

    qualifying_report = build_gate_report(sessions_root)
    assert qualifying_report.trusted is False
    assert qualifying_report.prospective_sessions == 0


def test_rehearsal_resume_cannot_be_promoted_to_qualifying(coordinator_env):
    clock, sessions_root, build = coordinator_env
    rehearsal = build(candidates=SYMBOLS[:4], qualification_mode="REHEARSAL")
    assert rehearsal.prepare(SESSION_DATE).state is CoordinatorState.RECORDING
    rehearsal.suspend_for_restart()
    clock[0] = datetime(2026, 9, 22, 10, 0, tzinfo=IST)
    attempted_promotion = build(
        candidates=SYMBOLS[:4], qualification_mode="PROSPECTIVE_QUALIFYING",
    )
    result = attempted_promotion.resume(SESSION_DATE)
    assert result.state is CoordinatorState.VOID
    assert "differs from frozen attempt ledger" in str(result.reason)
    attempts, errors = verify_attempt_registry(sessions_root)
    assert not errors and attempts[-1]["attempt_outcome"] == "PENDING"


@pytest.mark.parametrize("changed_input", ["config", "candidates", "policy"])
def test_resume_rejects_runtime_inputs_that_differ_from_frozen_session(
    coordinator_env, tmp_path, changed_input,
):
    clock, sessions_root, build = coordinator_env
    rehearsal = build(candidates=SYMBOLS[:4], qualification_mode="REHEARSAL")
    assert rehearsal.prepare(SESSION_DATE).state is CoordinatorState.RECORDING
    rehearsal.suspend_for_restart()
    clock[0] = datetime(2026, 9, 22, 10, 0, tzinfo=IST)
    kwargs = {"candidates": SYMBOLS[:4], "qualification_mode": "REHEARSAL"}
    if changed_input == "config":
        changed = _config()
        changed["latency_ms"] = 999
        kwargs["config"] = changed
    elif changed_input == "candidates":
        kwargs["candidates"] = SYMBOLS[1:5]
    else:
        kwargs["policy"] = _make_policy(tmp_path / "different")
        policy_data = json.loads(Path(kwargs["policy"]).read_text(encoding="utf-8"))
        policy_data["policy_id"] = "DIFFERENT_POLICY"
        Path(kwargs["policy"]).write_text(json.dumps(policy_data), encoding="utf-8")
    result = build(**kwargs).resume(SESSION_DATE)
    assert result.state is CoordinatorState.VOID
    assert "frozen" in str(result.reason)


def test_coordinator_requires_explicit_valid_qualification_mode(coordinator_env):
    _, _, build = coordinator_env
    with pytest.raises(ValueError, match="qualification_mode"):
        build(qualification_mode="UNKNOWN")
