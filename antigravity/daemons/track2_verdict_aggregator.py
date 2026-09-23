"""Read-only, fail-closed Track 2 qualification-gate accountant.

The aggregator never edits session evidence and has no broker capability. It
reproduces terminal verdicts from sealed artifacts before deriving counters.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from antigravity.models.session_manifest import (
    ALLOWED_QUALIFICATION_MODES,
    SessionStatus,
    SessionVerdict,
    _canonical_json,
    _sha256_bytes,
    compute_gate_counts,
    evaluate_session,
    verify_attempt_registry,
    verify_locked_preflight,
)


@dataclass(frozen=True)
class SessionAudit:
    session_date: str
    outcome: str
    trusted: bool
    counted_session: int
    counted_fills: int
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class GateReport:
    track_id: str
    expected_qualification_mode: str
    trusted: bool
    prospective_sessions: int
    realistically_fillable_entries: int
    required_sessions: int
    required_entries: int
    gate_passed: bool
    pending_attempts: int
    void_attempts: int
    rehearsal_attempts: int
    orphan_directories: tuple[str, ...]
    unexpected_root_entries: tuple[str, ...]
    integrity_errors: tuple[str, ...]
    sessions: tuple[SessionAudit, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["sessions"] = [asdict(item) for item in self.sessions]
        return value


def _read_hashed_json(path: Path, *, expected_type: type) -> Any:
    digest_path = path.with_suffix(".sha256")
    if path.is_symlink() or digest_path.is_symlink():
        raise ValueError(f"{path.name} or its digest is a symlink")
    if not path.is_file() or not digest_path.is_file():
        raise ValueError(f"{path.name} or its digest is missing")
    raw = path.read_bytes()
    expected_digest = digest_path.read_text(encoding="ascii").strip()
    if expected_digest != hashlib.sha256(raw).hexdigest():
        raise ValueError(f"{path.name} digest mismatch")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path.name} is not valid UTF-8 JSON") from exc
    if not isinstance(value, expected_type):
        raise ValueError(f"{path.name} must contain a {expected_type.__name__}")
    return value


def _verdict_from_mapping(value: Mapping[str, Any]) -> SessionVerdict:
    required = {
        "track_id", "session_date", "status", "void_reasons", "signals",
        "qualifying_fills", "counts_toward_60", "counts_toward_20",
        "qualifying_order_ids",
    }
    missing = required.difference(value)
    if missing:
        raise ValueError(f"verdict missing fields: {sorted(missing)}")
    allowed = required | {
        "_session_closed_at", "_preflight_sha256", "_manifest_sha256",
        "_fill_evidence_sha256",
    }
    unexpected = set(value).difference(allowed)
    if unexpected:
        raise ValueError(f"verdict contains unexpected fields: {sorted(unexpected)}")
    try:
        status = SessionStatus(value["status"])
    except (TypeError, ValueError) as exc:
        raise ValueError("verdict status is invalid") from exc
    if not isinstance(value["void_reasons"], list) or not isinstance(value["qualifying_order_ids"], list):
        raise ValueError("verdict list fields are invalid")
    integer_fields = ("signals", "qualifying_fills", "counts_toward_60", "counts_toward_20")
    if any(isinstance(value[field], bool) or not isinstance(value[field], int) for field in integer_fields):
        raise ValueError("verdict counters must be integers")
    return SessionVerdict(
        track_id=str(value["track_id"]),
        session_date=str(value["session_date"]),
        status=status,
        void_reasons=tuple(str(item) for item in value["void_reasons"]),
        signals=value["signals"],
        qualifying_fills=value["qualifying_fills"],
        counts_toward_60=value["counts_toward_60"],
        counts_toward_20=value["counts_toward_20"],
        qualifying_order_ids=tuple(str(item) for item in value["qualifying_order_ids"]),
    )


def _reproduce_verdict(session_dir: Path, saved: Mapping[str, Any]) -> SessionVerdict:
    preregistration = _read_hashed_json(
        session_dir / "preregistration.json", expected_type=dict,
    )
    if preregistration.get("track_id") != "TRACK2":
        raise ValueError("preregistration is not Track 2")
    preflight, preflight_errors = verify_locked_preflight(session_dir)
    if preflight is None:
        raise ValueError("preflight verification failed: " + "; ".join(preflight_errors))
    manifest = _read_hashed_json(session_dir / "data_manifest.json", expected_type=dict)
    fill_path = session_dir / "fill_evidence.json"
    fill_digest_path = fill_path.with_suffix(".sha256")
    if fill_path.exists() or fill_digest_path.exists():
        fill_evidence = _read_hashed_json(fill_path, expected_type=list)
    else:
        fill_evidence = []
    closed_at = saved.get("_session_closed_at")
    if not isinstance(closed_at, str):
        raise ValueError("verdict lacks closure provenance")
    if saved.get("_preflight_sha256") != _sha256_bytes(_canonical_json(preflight)):
        raise ValueError("verdict preflight binding mismatch")
    if saved.get("_manifest_sha256") != _sha256_bytes(_canonical_json(manifest)):
        raise ValueError("verdict manifest binding mismatch")
    if saved.get("_fill_evidence_sha256") != _sha256_bytes(_canonical_json(fill_evidence)):
        raise ValueError("verdict fill-evidence binding mismatch")
    reproduced = evaluate_session(
        session_dir=session_dir,
        preflight=preflight,
        data_manifest=manifest,
        session_closed_at=closed_at,
        fill_evidence=fill_evidence,
    )
    if reproduced.to_dict() != {key: saved.get(key) for key in reproduced.to_dict()}:
        raise ValueError("stored verdict does not reproduce from sealed evidence")
    return reproduced


def _audit_terminal_session(
    sessions_root: Path,
    session_date: str,
    terminal_attempt: Mapping[str, Any],
    *,
    expected_status: SessionStatus,
    outcome: str,
) -> tuple[SessionAudit, SessionVerdict | None]:
    session_dir = sessions_root / session_date
    try:
        if session_dir.is_symlink() or not session_dir.is_dir():
            raise ValueError("finalized session directory is missing or unsafe")
        saved = _read_hashed_json(session_dir / "verdict.json", expected_type=dict)
        stored_verdict = _verdict_from_mapping(saved)
        if stored_verdict.session_date != session_date:
            raise ValueError("verdict session date differs from attempt ledger")
        saved_digest = _sha256_bytes(_canonical_json(saved))
        details = terminal_attempt.get("details")
        if not isinstance(details, dict) or details.get("verdict_sha256") != saved_digest:
            raise ValueError("terminal attempt does not bind the exact verdict")
        reproduced = _reproduce_verdict(session_dir, saved)
        if reproduced != stored_verdict:
            raise ValueError("stored verdict fields differ from reproduced verdict")
        if reproduced.status is not expected_status:
            raise ValueError(f"{outcome} attempt does not contain a {expected_status.value} verdict")
        if expected_status is SessionStatus.REHEARSAL and (
            reproduced.counts_toward_60 != 0 or reproduced.counts_toward_20 != 0
        ):
            raise ValueError("REHEARSAL verdict contributes to qualification counters")
        return (
            SessionAudit(
                session_date, outcome, True,
                reproduced.counts_toward_60, reproduced.counts_toward_20,
            ),
            reproduced,
        )
    except (OSError, TypeError, ValueError) as exc:
        return SessionAudit(session_date, outcome, False, 0, 0, (str(exc),)), None


def build_gate_report(
    sessions_root: str | os.PathLike[str],
    *,
    expected_qualification_mode: str = "PROSPECTIVE_QUALIFYING",
) -> GateReport:
    """Scan sealed session evidence without modifying it."""
    if expected_qualification_mode not in ALLOWED_QUALIFICATION_MODES:
        raise ValueError("expected_qualification_mode is invalid")
    root = Path(sessions_root).resolve()
    attempt_records, registry_errors = verify_attempt_registry(root)
    integrity_errors = list(registry_errors)
    terminal_by_date: dict[str, Mapping[str, Any]] = {}
    initial_mode_by_date: dict[str, Any] = {}
    for record in attempt_records:
        record_date = str(record.get("session_date"))
        terminal_by_date[record_date] = record
        initial_mode_by_date.setdefault(
            record_date, record.get("details", {}).get("qualification_mode")
        )

    session_audits: list[SessionAudit] = []
    trusted_verdicts: list[SessionVerdict] = []
    pending_attempts = 0
    void_attempts = 0
    rehearsal_attempts = 0
    for session_date in sorted(terminal_by_date):
        terminal = terminal_by_date[session_date]
        outcome = str(terminal.get("attempt_outcome"))
        try:
            parsed_session_date = date.fromisoformat(session_date)
        except ValueError:
            parsed_session_date = None
        if parsed_session_date is None or parsed_session_date.isoformat() != session_date:
            integrity_errors.append(f"attempt ledger session date is unsafe: {session_date}")
            session_audits.append(
                SessionAudit(session_date, outcome, False, 0, 0, ("unsafe session date",))
            )
            continue
        frozen_mode = initial_mode_by_date.get(session_date)
        if frozen_mode != expected_qualification_mode:
            reason = (
                f"frozen qualification_mode {frozen_mode} does not match evidence root mode "
                f"{expected_qualification_mode}"
            )
            integrity_errors.append(f"{session_date}: {reason}")
            session_audits.append(
                SessionAudit(session_date, outcome, False, 0, 0, (reason,))
            )
            continue
        if outcome == "PENDING":
            pending_attempts += 1
            unexpected_verdict = root / session_date / "verdict.json"
            reasons = ["attempt is still PENDING"]
            if unexpected_verdict.exists() or unexpected_verdict.with_suffix(".sha256").exists():
                reason = "PENDING attempt unexpectedly contains verdict evidence"
                reasons.append(reason)
                integrity_errors.append(f"{session_date}: {reason}")
            session_audits.append(
                SessionAudit(session_date, outcome, False, 0, 0, tuple(reasons))
            )
        elif outcome == "VOID":
            void_attempts += 1
            unexpected_verdict = root / session_date / "verdict.json"
            reasons: tuple[str, ...] = ()
            trusted_void = True
            if unexpected_verdict.exists() or unexpected_verdict.with_suffix(".sha256").exists():
                reason = "VOID attempt unexpectedly contains verdict evidence"
                integrity_errors.append(f"{session_date}: {reason}")
                reasons = (reason,)
                trusted_void = False
            session_audits.append(
                SessionAudit(session_date, outcome, trusted_void, 0, 0, reasons)
            )
        elif outcome == "FINALIZED":
            audit, verdict = _audit_terminal_session(
                root, session_date, terminal,
                expected_status=SessionStatus.COUNTED,
                outcome="FINALIZED",
            )
            session_audits.append(audit)
            if verdict is not None:
                trusted_verdicts.append(verdict)
            else:
                integrity_errors.extend(
                    f"{session_date}: {reason}" for reason in audit.reasons
                )
        elif outcome == "REHEARSAL":
            rehearsal_attempts += 1
            audit, verdict = _audit_terminal_session(
                root, session_date, terminal,
                expected_status=SessionStatus.REHEARSAL,
                outcome="REHEARSAL",
            )
            session_audits.append(audit)
            if verdict is not None:
                trusted_verdicts.append(verdict)
            else:
                integrity_errors.extend(
                    f"{session_date}: {reason}" for reason in audit.reasons
                )
        else:
            reason = f"unsupported attempt outcome: {outcome}"
            integrity_errors.append(f"{session_date}: {reason}")
            session_audits.append(
                SessionAudit(session_date, outcome, False, 0, 0, (reason,))
            )

    ledger_dates = set(terminal_by_date)
    orphan_directories: list[str] = []
    unexpected_root_entries: list[str] = []
    if root.is_dir():
        for child in root.iterdir():
            if child.is_symlink():
                unexpected_root_entries.append(child.name)
            elif child.is_dir():
                if child.name not in ledger_dates:
                    orphan_directories.append(child.name)
            elif child.is_file():
                allowed_registries = {
                    "session_attempt_registry.jsonl",
                    "preregistration_registry.jsonl",
                    "preflight_registry.jsonl",
                }
                checkpoint_match = re.fullmatch(
                    r"stream_checkpoint_(\d{4}-\d{2}-\d{2})\.jsonl", child.name,
                )
                if child.name in allowed_registries:
                    continue
                if checkpoint_match and checkpoint_match.group(1) in ledger_dates:
                    continue
                unexpected_root_entries.append(child.name)
    if orphan_directories:
        integrity_errors.extend(
            f"orphan session directory without attempt ledger entry: {name}"
            for name in sorted(orphan_directories)
        )
    if unexpected_root_entries:
        integrity_errors.extend(
            f"unexpected entry in session evidence root: {name}"
            for name in sorted(unexpected_root_entries)
        )

    counts = {
        "prospective_sessions": 0,
        "realistically_fillable_entries": 0,
    }
    if not integrity_errors:
        try:
            counts.update(compute_gate_counts(trusted_verdicts))
        except ValueError as exc:
            integrity_errors.append(str(exc))
    trusted = not integrity_errors
    if not trusted:
        counts["prospective_sessions"] = 0
        counts["realistically_fillable_entries"] = 0
    prospective_sessions = counts["prospective_sessions"]
    fillable_entries = counts["realistically_fillable_entries"]
    return GateReport(
        track_id="TRACK2",
        expected_qualification_mode=expected_qualification_mode,
        trusted=trusted,
        prospective_sessions=prospective_sessions,
        realistically_fillable_entries=fillable_entries,
        required_sessions=60,
        required_entries=20,
        gate_passed=(
            expected_qualification_mode == "PROSPECTIVE_QUALIFYING"
            and trusted and prospective_sessions >= 60 and fillable_entries >= 20
        ),
        pending_attempts=pending_attempts,
        void_attempts=void_attempts,
        rehearsal_attempts=rehearsal_attempts,
        orphan_directories=tuple(sorted(orphan_directories)),
        unexpected_root_entries=tuple(sorted(unexpected_root_entries)),
        integrity_errors=tuple(integrity_errors),
        sessions=tuple(session_audits),
    )


def write_derived_report(
    report: GateReport,
    output_path: str | os.PathLike[str],
    *,
    sessions_root: str | os.PathLike[str],
) -> None:
    """Atomically write a derived report outside the immutable evidence tree."""
    output = Path(output_path).resolve()
    root = Path(sessions_root).resolve()
    try:
        output.relative_to(root)
    except ValueError:
        pass
    else:
        raise ValueError("derived report cannot be written inside session evidence")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output.parent, prefix=f".{output.name}.", suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit Track 2 paper qualification counters")
    parser.add_argument("--sessions-root", required=True)
    parser.add_argument("--output")
    parser.add_argument(
        "--mode", choices=sorted(ALLOWED_QUALIFICATION_MODES),
        default="PROSPECTIVE_QUALIFYING",
    )
    arguments = parser.parse_args(argv)
    report = build_gate_report(
        arguments.sessions_root, expected_qualification_mode=arguments.mode,
    )
    if arguments.output:
        write_derived_report(report, arguments.output, sessions_root=arguments.sessions_root)
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 0 if report.trusted else 2


if __name__ == "__main__":
    raise SystemExit(main())
