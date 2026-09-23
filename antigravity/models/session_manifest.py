"""Fail-closed Track 2 Phase 1A session evidence contracts.

This module records research evidence only.  It cannot place broker orders and it
does not evaluate alpha.  Gate counters are derived from immutable session
artifacts instead of being typed into status documents.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping


# India has no daylight-saving transitions; a fixed offset avoids depending on
# an optional Windows tzdata package merely to validate exchange timestamps.
IST = timezone(timedelta(hours=5, minutes=30), name="IST")
MARKET_OPEN = time(9, 15)
ORB_SIGNAL_START = time(9, 30)
LAST_ENTRY_TIME = time(15, 15)
SESSION_CLOSE = time(15, 30)
MAX_CAPTURE_GAP_SECONDS = 5.0
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_PREREGISTRATION_FIELDS = {
    "track_id",
    "session_date",
    "frozen_at",
    "config_sha256",
    "universe_sha256",
    "order_rules",
    "cost_model",
    "gap_seconds",
    "queue_haircut",
    "latency_ms",
    "gate_stats_version",
    "paper_only",
    "qualification_mode",
}
REQUIRED_PREFLIGHT_FIELDS = {
    "fno_verified",
    "asm_gsm_clear",
    "band_check",
    "feed_health",
    "source_authenticated",
    "passed",
    "checked_at",
    "fno_source_sha256",
    "surveillance_source_sha256",
    "band_source_sha256",
    "universe_sha256",
    "qualification_mode",
}
ALLOWED_QUALIFICATION_MODES = {"PROSPECTIVE_QUALIFYING", "REHEARSAL"}
ALLOWED_REFERENCE_ROLES = {"FNO", "SURVEILLANCE", "BAND_POLICY", "UNIVERSE"}


class SessionStatus(str, Enum):
    COUNTED = "COUNTED"
    VOID = "VOID"
    PILOT_UNVERIFIED = "PILOT_UNVERIFIED"
    REHEARSAL = "REHEARSAL"


class EvidenceClass(str, Enum):
    E0_SIGNAL_ONLY = "E0_SIGNAL_ONLY"
    E1_BAR_POSSIBLE = "E1_BAR_POSSIBLE"
    E2_MARKETABLE_DEPTH = "E2_MARKETABLE_DEPTH"
    E3_TICK_QUEUE = "E3_TICK_QUEUE"


class FillState(str, Enum):
    LOCKED_NO_BID = "LOCKED_NO_BID"
    QUEUED = "QUEUED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"


@dataclass(frozen=True)
class SessionVerdict:
    track_id: str
    session_date: str
    status: SessionStatus
    void_reasons: tuple[str, ...]
    signals: int
    qualifying_fills: int
    counts_toward_60: int
    counts_toward_20: int
    qualifying_order_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id,
            "session_date": self.session_date,
            "status": self.status.value,
            "void_reasons": list(self.void_reasons),
            "signals": self.signals,
            "qualifying_fills": self.qualifying_fills,
            "counts_toward_60": self.counts_toward_60,
            "counts_toward_20": self.counts_toward_20,
            "qualifying_order_ids": list(self.qualifying_order_ids),
        }


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_PATTERN.fullmatch(value) is not None


def _parse_aware_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(IST)


def _is_finite_number(value: Any, *, minimum: float | None = None) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    numeric = float(value)
    return math.isfinite(numeric) and (minimum is None or numeric >= minimum)


def _now_ist() -> datetime:
    return datetime.now(tz=IST)


def _market_datetime(session_date: date, clock_time: time) -> datetime:
    return datetime.combine(session_date, clock_time, tzinfo=IST)


def validate_preregistration(record: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    missing = REQUIRED_PREREGISTRATION_FIELDS.difference(record)
    if missing:
        errors.append(f"missing preregistration fields: {sorted(missing)}")
        return errors

    try:
        session_date = date.fromisoformat(str(record["session_date"]))
    except ValueError:
        errors.append("session_date must be YYYY-MM-DD")
        return errors
    if session_date.isoformat() != record["session_date"]:
        errors.append("session_date must use canonical YYYY-MM-DD form")
        return errors

    if record["track_id"] != "TRACK2":
        errors.append("track_id must be exactly TRACK2")
    frozen_at = _parse_aware_timestamp(record["frozen_at"])
    if frozen_at is None:
        errors.append("frozen_at must be a timezone-aware ISO timestamp")
    elif frozen_at >= _market_datetime(session_date, MARKET_OPEN):
        errors.append("frozen_at must be before the session's 09:15 IST open")

    for field in ("config_sha256", "universe_sha256"):
        if not _is_sha256(record[field]):
            errors.append(f"{field} must be a lowercase SHA-256 digest")
    for field in ("order_rules", "cost_model"):
        if not isinstance(record[field], dict) or not record[field]:
            errors.append(f"{field} must be a non-empty object")
    if (not _is_finite_number(record["gap_seconds"], minimum=0.001)
            or float(record["gap_seconds"]) > MAX_CAPTURE_GAP_SECONDS):
        errors.append(f"gap_seconds must be positive and <= {MAX_CAPTURE_GAP_SECONDS}")
    if not _is_finite_number(record["queue_haircut"], minimum=0.0) or float(record["queue_haircut"]) > 1:
        errors.append("queue_haircut must be between 0 and 1")
    if not _is_finite_number(record["latency_ms"], minimum=0.0):
        errors.append("latency_ms must be a non-negative finite number")
    if not isinstance(record["gate_stats_version"], str) or not record["gate_stats_version"].strip():
        errors.append("gate_stats_version must be a non-empty string")
    if record["paper_only"] is not True:
        errors.append("paper_only must be exactly true")
    if record["qualification_mode"] not in ALLOWED_QUALIFICATION_MODES:
        errors.append("qualification_mode must be PROSPECTIVE_QUALIFYING or REHEARSAL")
    return errors


def _read_anchor_registry(registry_path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    if not registry_path.exists():
        return [], []
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    previous_hash = "0" * 64
    for line_number, line in enumerate(registry_path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"anchor registry line {line_number} is invalid JSON")
            continue
        if not isinstance(record, dict):
            errors.append(f"anchor registry line {line_number} is not an object")
            continue
        chain_hash = record.get("chain_hash")
        payload = {key: value for key, value in record.items() if key != "chain_hash"}
        if payload.get("previous_hash") != previous_hash:
            errors.append(f"anchor registry chain break at line {line_number}")
        expected = _sha256_bytes(previous_hash.encode("ascii") + _canonical_json(payload))
        if chain_hash != expected:
            errors.append(f"anchor registry hash mismatch at line {line_number}")
        if isinstance(chain_hash, str):
            previous_hash = chain_hash
        records.append(record)
    return records, errors


def _append_preregistration_anchor(
    sessions_root: Path,
    session_date: str,
    preregistration_sha256: str,
    registered_at: datetime,
) -> None:
    """Append a hash-chained external anchor outside the individual session."""
    registry_path = sessions_root / "preregistration_registry.jsonl"
    records, errors = _read_anchor_registry(registry_path)
    if errors:
        raise ValueError("; ".join(errors))
    if any(item.get("session_date") == session_date for item in records):
        raise ValueError(f"session {session_date} is already anchored")
    previous_hash = str(records[-1]["chain_hash"]) if records else "0" * 64
    payload = {
        "session_date": session_date,
        "preregistration_sha256": preregistration_sha256,
        "registered_at": registered_at.isoformat(),
        "previous_hash": previous_hash,
    }
    record = {**payload, "chain_hash": _sha256_bytes(previous_hash.encode("ascii") + _canonical_json(payload))}
    with open(registry_path, "a", encoding="utf-8") as registry:
        registry.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        registry.flush()
        os.fsync(registry.fileno())


def _verify_preregistration_anchor(session_dir: Path, session_date: str, digest: str) -> list[str]:
    records, errors = _read_anchor_registry(session_dir.parent / "preregistration_registry.jsonl")
    matching = [item for item in records if item.get("session_date") == session_date]
    if len(matching) != 1:
        errors.append("exactly one preregistration anchor is required for the session")
    elif matching[0].get("preregistration_sha256") != digest:
        errors.append("preregistration anchor digest mismatch")
    else:
        registered_at = _parse_aware_timestamp(matching[0].get("registered_at"))
        try:
            preregistration = json.loads((session_dir / "preregistration.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            preregistration = {}
        frozen_at = _parse_aware_timestamp(preregistration.get("frozen_at"))
        try:
            open_at = _market_datetime(date.fromisoformat(session_date), MARKET_OPEN)
        except ValueError:
            open_at = None
        if registered_at is None or frozen_at is None or open_at is None:
            errors.append("preregistration anchor timestamps are invalid")
        elif registered_at < frozen_at or registered_at >= open_at:
            errors.append("preregistration anchor must be after freeze and before market open")
    return errors


def write_locked_preregistration(
    session_dir: str | os.PathLike[str],
    record: Mapping[str, Any],
) -> str:
    """Create a preregistration exactly once and write its independent digest."""
    errors = validate_preregistration(record)
    if errors:
        raise ValueError("; ".join(errors))
    current_time = _now_ist().astimezone(IST)
    frozen_at = _parse_aware_timestamp(record["frozen_at"])
    session_date = date.fromisoformat(str(record["session_date"]))
    if frozen_at is None or frozen_at > current_time:
        raise ValueError("frozen_at cannot be later than the registration wall clock")
    if current_time >= _market_datetime(session_date, MARKET_OPEN):
        raise ValueError("preregistration must be written before the session opens")
    directory = Path(session_dir)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "preregistration.json"
    lock_path = directory / "preregistration.sha256"
    payload = _canonical_json(record)
    digest = _sha256_bytes(payload)
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as exc:
        raise FileExistsError("preregistration is immutable and already exists") from exc
    with os.fdopen(descriptor, "wb") as target:
        target.write(payload)
        target.flush()
        os.fsync(target.fileno())
    with open(lock_path, "x", encoding="ascii") as lock_file:
        lock_file.write(digest + "\n")
    _append_preregistration_anchor(directory.parent, str(record["session_date"]), digest, current_time)
    return digest


def verify_locked_preregistration(session_dir: str | os.PathLike[str]) -> tuple[dict[str, Any] | None, list[str]]:
    directory = Path(session_dir)
    destination = directory / "preregistration.json"
    lock_path = directory / "preregistration.sha256"
    if not destination.is_file() or not lock_path.is_file():
        return None, ["preregistration file or digest is missing"]
    raw = destination.read_bytes()
    expected = lock_path.read_text(encoding="ascii").strip()
    actual_digest = _sha256_bytes(raw)
    if not _is_sha256(expected) or actual_digest != expected:
        return None, ["preregistration digest mismatch"]
    try:
        record = json.loads(raw)
    except json.JSONDecodeError:
        return None, ["preregistration is not valid JSON"]
    if not isinstance(record, dict):
        return None, ["preregistration must be a JSON object"]
    errors = validate_preregistration(record)
    errors.extend(_verify_preregistration_anchor(directory, str(record.get("session_date", "")), actual_digest))
    return (record if not errors else None), errors


def validate_preflight(record: Mapping[str, Any], session_date: str, frozen_at: datetime) -> list[str]:
    errors: list[str] = []
    missing = REQUIRED_PREFLIGHT_FIELDS.difference(record)
    if missing:
        errors.append(f"missing preflight fields: {sorted(missing)}")
        return errors
    for field in ("fno_verified", "asm_gsm_clear", "band_check", "feed_health", "source_authenticated", "passed"):
        if record[field] is not True:
            errors.append(f"{field} must be exactly true")
    checked_at = _parse_aware_timestamp(record["checked_at"])
    session_day = date.fromisoformat(session_date)
    if (checked_at is None or checked_at.date().isoformat() != session_date
            or checked_at < frozen_at or checked_at >= _market_datetime(session_day, MARKET_OPEN)):
        errors.append("checked_at must be after freeze and before 09:15 IST on the session date")
    for field in (
        "fno_source_sha256",
        "surveillance_source_sha256",
        "band_source_sha256",
        "universe_sha256",
    ):
        if not _is_sha256(record[field]):
            errors.append(f"{field} must be a lowercase SHA-256 digest")
    if record["qualification_mode"] not in ALLOWED_QUALIFICATION_MODES:
        errors.append("qualification_mode must be PROSPECTIVE_QUALIFYING or REHEARSAL")
    return errors


def _append_preflight_anchor(
    sessions_root: Path,
    session_date: str,
    preflight_sha256: str,
    registered_at: datetime,
) -> None:
    """Append a hash-chained external preflight anchor outside the individual session."""
    registry_path = sessions_root / "preflight_registry.jsonl"
    records, errors = _read_anchor_registry(registry_path)
    if errors:
        raise ValueError("; ".join(errors))
    if any(item.get("session_date") == session_date for item in records):
        raise ValueError(f"preflight for session {session_date} is already anchored")
    previous_hash = str(records[-1]["chain_hash"]) if records else "0" * 64
    payload = {
        "session_date": session_date,
        "preflight_sha256": preflight_sha256,
        "registered_at": registered_at.isoformat(),
        "previous_hash": previous_hash,
    }
    record = {**payload, "chain_hash": _sha256_bytes(previous_hash.encode("ascii") + _canonical_json(payload))}
    with open(registry_path, "a", encoding="utf-8") as registry:
        registry.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        registry.flush()
        os.fsync(registry.fileno())


def _verify_preflight_anchor(session_dir: Path, session_date: str, digest: str) -> list[str]:
    records, errors = _read_anchor_registry(session_dir.parent / "preflight_registry.jsonl")
    matching = [item for item in records if item.get("session_date") == session_date]
    if len(matching) != 1:
        errors.append("exactly one preflight anchor is required for the session")
    elif matching[0].get("preflight_sha256") != digest:
        errors.append("preflight anchor digest mismatch")
    else:
        registered_at = _parse_aware_timestamp(matching[0].get("registered_at"))
        try:
            prereg_record = json.loads((session_dir / "preregistration.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prereg_record = {}
        frozen_at = _parse_aware_timestamp(prereg_record.get("frozen_at"))
        try:
            open_at = _market_datetime(date.fromisoformat(session_date), MARKET_OPEN)
        except ValueError:
            open_at = None
        if registered_at is None or frozen_at is None or open_at is None:
            errors.append("preflight anchor timestamps are invalid")
        elif registered_at < frozen_at or registered_at >= open_at:
            errors.append("preflight anchor must be after freeze and before market open")
    return errors


def write_locked_preflight(
    session_dir: str | os.PathLike[str],
    record: Mapping[str, Any],
) -> str:
    """Create an immutable preflight.json before 09:15 IST and append an external anchor."""
    directory = Path(session_dir)
    session_date = directory.name
    # Verify preregistration exists first to get frozen_at
    prereg_path = directory / "preregistration.json"
    if not prereg_path.is_file():
        raise ValueError("preregistration must exist before sealing preflight")
    try:
        prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"preregistration unreadable: {exc}")
    frozen_at = _parse_aware_timestamp(prereg.get("frozen_at"))
    if frozen_at is None:
        raise ValueError("preregistration frozen_at is invalid")
    if record.get("qualification_mode") != prereg.get("qualification_mode"):
        raise ValueError("preflight qualification_mode must match preregistration")

    errors = validate_preflight(record, session_date, frozen_at)
    if errors:
        raise ValueError("; ".join(errors))

    current_time = _now_ist().astimezone(IST)
    session_day = date.fromisoformat(session_date)
    open_at = _market_datetime(session_day, MARKET_OPEN)
    if current_time >= open_at:
        raise ValueError("preflight must be sealed before 09:15 IST")
    checked_at = _parse_aware_timestamp(record["checked_at"])
    if checked_at is None or checked_at > current_time:
        raise ValueError("checked_at cannot be later than current registration clock")

    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "preflight.json"
    lock_path = directory / "preflight.sha256"
    payload = _canonical_json(record)
    digest = _sha256_bytes(payload)

    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as exc:
        raise FileExistsError("preflight is immutable and already exists") from exc
    with os.fdopen(descriptor, "wb") as target:
        target.write(payload)
        target.flush()
        os.fsync(target.fileno())
    with open(lock_path, "x", encoding="ascii") as lock_file:
        lock_file.write(digest + "\n")
    _append_preflight_anchor(directory.parent, session_date, digest, current_time)
    return digest


def verify_locked_preflight(session_dir: str | os.PathLike[str]) -> tuple[dict[str, Any] | None, list[str]]:
    directory = Path(session_dir)
    destination = directory / "preflight.json"
    lock_path = directory / "preflight.sha256"
    if not destination.is_file() or not lock_path.is_file():
        return None, ["preflight file or digest is missing"]
    raw = destination.read_bytes()
    expected = lock_path.read_text(encoding="ascii").strip()
    actual_digest = _sha256_bytes(raw)
    if not _is_sha256(expected) or actual_digest != expected:
        return None, ["preflight digest mismatch"]
    try:
        record = json.loads(raw)
    except json.JSONDecodeError:
        return None, ["preflight is not valid JSON"]
    if not isinstance(record, dict):
        return None, ["preflight must be a JSON object"]
    session_date = directory.name
    prereg_path = directory / "preregistration.json"
    try:
        prereg = json.loads(prereg_path.read_text(encoding="utf-8")) if prereg_path.is_file() else {}
    except Exception:
        prereg = {}
    frozen_at = _parse_aware_timestamp(prereg.get("frozen_at")) or _market_datetime(date.fromisoformat(session_date), time(0, 0))
    errors = validate_preflight(record, session_date, frozen_at)
    errors.extend(_verify_preflight_anchor(directory, session_date, actual_digest))
    return (record if not errors else None), errors


# --- Stream Checkpoints ---

def write_stream_checkpoint(
    sessions_root: Path | str,
    session_date: str,
    *,
    sequence: int,
    stream_sha256: str,
    row_count: int,
    byte_count: int,
    recorded_at: datetime,
) -> str:
    if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
        raise ValueError("checkpoint recorded_at must be timezone-aware")
    if not _is_sha256(stream_sha256):
        raise ValueError("checkpoint stream_sha256 must be a lowercase SHA-256 digest")
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count <= 0:
        raise ValueError("checkpoint row_count must be a positive integer")
    if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count <= 0:
        raise ValueError("checkpoint byte_count must be a positive integer")
    root = Path(sessions_root)
    root.mkdir(parents=True, exist_ok=True)
    cp_path = root / f"stream_checkpoint_{session_date}.jsonl"
    records, errors = verify_stream_checkpoints(root, session_date)
    if errors:
        raise ValueError("; ".join(errors))
    expected_seq = len(records) + 1
    if sequence != expected_seq:
        raise ValueError(f"checkpoint sequence must be {expected_seq}, got {sequence}")
    previous_hash = str(records[-1]["chain_hash"]) if records else "0" * 64
    payload = {
        "session_date": session_date,
        "sequence": sequence,
        "stream_sha256": stream_sha256,
        "row_count": row_count,
        "byte_count": byte_count,
        "recorded_at": recorded_at.astimezone(IST).isoformat(),
        "previous_hash": previous_hash,
    }
    chain_hash = _sha256_bytes(previous_hash.encode("ascii") + _canonical_json(payload))
    record = {**payload, "chain_hash": chain_hash}
    with open(cp_path, "a", encoding="utf-8") as target:
        target.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        target.flush()
        os.fsync(target.fileno())
    return chain_hash


def verify_stream_checkpoints(
    sessions_root: Path | str,
    session_date: str,
    stream_path: Path | str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    cp_path = Path(sessions_root) / f"stream_checkpoint_{session_date}.jsonl"
    if not cp_path.exists():
        return [], []
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    previous_hash = "0" * 64

    stream_bytes: bytes | None = None
    if stream_path is not None:
        st_p = Path(stream_path)
        if st_p.is_file():
            stream_bytes = st_p.read_bytes()
        else:
            errors.append(f"market stream file {st_p.name} not found for checkpoint verification")

    for line_num, line in enumerate(cp_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"stream checkpoint line {line_num} is invalid JSON")
            continue
        if not isinstance(rec, dict):
            errors.append(f"stream checkpoint line {line_num} is not an object")
            continue
        chain_hash = rec.get("chain_hash")
        payload = {k: v for k, v in rec.items() if k != "chain_hash"}
        if payload.get("previous_hash") != previous_hash:
            errors.append(f"stream checkpoint chain break at line {line_num}")
        expected = _sha256_bytes(previous_hash.encode("ascii") + _canonical_json(payload))
        if chain_hash != expected:
            errors.append(f"stream checkpoint hash mismatch at line {line_num}")
        if isinstance(chain_hash, str):
            previous_hash = chain_hash
        if rec.get("session_date") != session_date:
            errors.append(f"stream checkpoint session mismatch at line {line_num}")
        if rec.get("sequence") != len(records) + 1:
            errors.append(f"stream checkpoint sequence mismatch at line {line_num}")
        if not _is_sha256(rec.get("stream_sha256")):
            errors.append(f"stream checkpoint digest invalid at line {line_num}")
        if isinstance(rec.get("row_count"), bool) or not isinstance(rec.get("row_count"), int) or rec.get("row_count", 0) <= 0:
            errors.append(f"stream checkpoint row_count invalid at line {line_num}")
        if isinstance(rec.get("byte_count"), bool) or not isinstance(rec.get("byte_count"), int) or rec.get("byte_count", 0) <= 0:
            errors.append(f"stream checkpoint byte_count invalid at line {line_num}")
        recorded_at = _parse_aware_timestamp(rec.get("recorded_at"))
        if recorded_at is None or recorded_at.date().isoformat() != session_date:
            errors.append(f"stream checkpoint recorded_at invalid at line {line_num}")
        if records:
            prior_at = _parse_aware_timestamp(records[-1].get("recorded_at"))
            if prior_at is not None and recorded_at is not None and recorded_at <= prior_at:
                errors.append(f"stream checkpoint timestamps not increasing at line {line_num}")
            prior_rows = records[-1].get("row_count")
            if isinstance(prior_rows, int) and isinstance(rec.get("row_count"), int) and rec["row_count"] < prior_rows:
                errors.append(f"stream checkpoint row_count decreased at line {line_num}")
            prior_bytes = records[-1].get("byte_count")
            if isinstance(prior_bytes, int) and isinstance(rec.get("byte_count"), int) and rec["byte_count"] < prior_bytes:
                errors.append(f"stream checkpoint byte_count decreased at line {line_num}")

        # Exact prefix verification if stream_bytes is available
        if stream_bytes is not None:
            bc = rec.get("byte_count")
            if isinstance(bc, int) and bc > 0:
                if bc > len(stream_bytes):
                    errors.append(f"stream checkpoint line {line_num} byte_count exceeds stream length")
                else:
                    prefix_slice = stream_bytes[:bc]
                    actual_prefix_hash = _sha256_bytes(prefix_slice)
                    if rec.get("stream_sha256") != actual_prefix_hash:
                        errors.append(f"stream checkpoint line {line_num} stream_sha256 does not match prefix bytes")
                    actual_prefix_rows = sum(1 for ln in prefix_slice.splitlines() if ln.strip())
                    if rec.get("row_count") != actual_prefix_rows:
                        errors.append(f"stream checkpoint line {line_num} row_count does not match prefix rows")

        records.append(rec)
    return records, errors


# --- Session Attempt Registry ---

ALLOWED_ATTEMPT_OUTCOMES = {"PENDING", "VOID", "FINALIZED", "REHEARSAL"}


def write_attempt_record(
    sessions_root: Path | str,
    session_date: str,
    outcome: str,
    *,
    details: Mapping[str, Any] | None = None,
    recorded_at: datetime | None = None,
) -> str:
    try:
        parsed_session_date = date.fromisoformat(session_date)
    except (TypeError, ValueError) as exc:
        raise ValueError("session_date must use canonical YYYY-MM-DD form") from exc
    if parsed_session_date.isoformat() != session_date:
        raise ValueError("session_date must use canonical YYYY-MM-DD form")
    root = Path(sessions_root)
    root.mkdir(parents=True, exist_ok=True)
    reg_path = root / "session_attempt_registry.jsonl"
    if outcome not in ALLOWED_ATTEMPT_OUTCOMES:
        raise ValueError(f"outcome must be one of {sorted(ALLOWED_ATTEMPT_OUTCOMES)}")
    records, errors = verify_attempt_registry(root)
    if errors:
        raise ValueError("; ".join(errors))

    normalized_details = dict(details or {})
    matching_attempts = [r for r in records if r.get("session_date") == session_date]
    if not matching_attempts:
        if outcome != "PENDING":
            raise ValueError(f"first attempt for {session_date} must be PENDING")
        if normalized_details.get("qualification_mode") not in ALLOWED_QUALIFICATION_MODES:
            raise ValueError("PENDING attempt requires an explicit qualification_mode")
    else:
        # A date cannot have more than one initial PENDING attempt
        if outcome == "PENDING":
            raise ValueError(f"initial attempt for session date {session_date} already exists")
        # Terminal outcomes (VOID, FINALIZED) cannot follow another terminal outcome
        if matching_attempts[-1].get("attempt_outcome") in {"VOID", "FINALIZED", "REHEARSAL"}:
            raise ValueError(f"session date {session_date} already has terminal outcome {matching_attempts[-1].get('attempt_outcome')}")
        mode = matching_attempts[0].get("details", {}).get("qualification_mode")
        allowed_terminal = {"VOID", "REHEARSAL"} if mode == "REHEARSAL" else {"VOID", "FINALIZED"}
        if outcome not in allowed_terminal:
            raise ValueError(f"terminal outcome {outcome} is invalid for qualification_mode {mode}")

    at_time = recorded_at or _now_ist()
    if at_time.tzinfo is None or at_time.utcoffset() is None:
        raise ValueError("attempt recorded_at must be timezone-aware")
    at_time = at_time.astimezone(IST)
    previous_hash = str(records[-1]["chain_hash"]) if records else "0" * 64
    payload = {
        "session_date": session_date,
        "attempt_outcome": outcome,
        "recorded_at": at_time.isoformat(),
        "details": normalized_details,
        "previous_hash": previous_hash,
    }
    chain_hash = _sha256_bytes(previous_hash.encode("ascii") + _canonical_json(payload))
    record = {**payload, "chain_hash": chain_hash}
    with open(reg_path, "a", encoding="utf-8") as target:
        target.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        target.flush()
        os.fsync(target.fileno())
    return chain_hash


def verify_attempt_registry(sessions_root: Path | str) -> tuple[list[dict[str, Any]], list[str]]:
    reg_path = Path(sessions_root) / "session_attempt_registry.jsonl"
    if not reg_path.exists():
        return [], []
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    previous_hash = "0" * 64
    seen_dates_first: set[str] = set()
    for line_num, line in enumerate(reg_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"attempt registry line {line_num} is invalid JSON")
            continue
        if not isinstance(rec, dict):
            errors.append(f"attempt registry line {line_num} is not an object")
            continue
        chain_hash = rec.get("chain_hash")
        payload = {k: v for k, v in rec.items() if k != "chain_hash"}
        if payload.get("previous_hash") != previous_hash:
            errors.append(f"attempt registry chain break at line {line_num}")
        expected = _sha256_bytes(previous_hash.encode("ascii") + _canonical_json(payload))
        if chain_hash != expected:
            errors.append(f"attempt registry hash mismatch at line {line_num}")
        if isinstance(chain_hash, str):
            previous_hash = chain_hash
        session_value = rec.get("session_date")
        outcome = rec.get("attempt_outcome")
        recorded_at = _parse_aware_timestamp(rec.get("recorded_at"))
        if not isinstance(session_value, str):
            errors.append(f"attempt registry session_date invalid at line {line_num}")
        else:
            try:
                parsed_session_value = date.fromisoformat(session_value)
            except ValueError:
                parsed_session_value = None
            if parsed_session_value is None or parsed_session_value.isoformat() != session_value:
                errors.append(f"attempt registry session_date is not canonical at line {line_num}")
        if outcome not in ALLOWED_ATTEMPT_OUTCOMES:
            errors.append(f"attempt registry outcome invalid at line {line_num}")
        if recorded_at is None:
            errors.append(f"attempt registry recorded_at invalid at line {line_num}")
        prior_for_date = [item for item in records if item.get("session_date") == session_value]
        if not prior_for_date and outcome != "PENDING":
            errors.append(f"attempt registry first state must be PENDING at line {line_num}")
        elif not prior_for_date:
            details = rec.get("details")
            if not isinstance(details, dict) or details.get("qualification_mode") not in ALLOWED_QUALIFICATION_MODES:
                errors.append(f"attempt registry PENDING mode invalid at line {line_num}")
        elif prior_for_date:
            initial_mode = prior_for_date[0].get("details", {}).get("qualification_mode")
            allowed_terminal = {"VOID", "REHEARSAL"} if initial_mode == "REHEARSAL" else {"VOID", "FINALIZED"}
            if (outcome == "PENDING"
                    or prior_for_date[-1].get("attempt_outcome") in {"VOID", "FINALIZED", "REHEARSAL"}
                    or outcome not in allowed_terminal):
                errors.append(f"attempt registry invalid transition at line {line_num}")
        records.append(rec)
    return records, errors


def _safe_manifest_path(session_dir: Path, relative_path: Any) -> Path | None:
    if not isinstance(relative_path, str) or not relative_path.strip():
        return None
    candidate = session_dir / relative_path
    if candidate.is_symlink():
        return None
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(session_dir.resolve(strict=True))
    except (FileNotFoundError, OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def _inspect_market_stream(path: Path) -> tuple[int, datetime | None, datetime | None, float, EvidenceClass | None, list[str]]:
    timestamps: list[datetime] = []
    evidence_classes: list[EvidenceClass] = []
    errors: list[str] = []
    rows = 0
    with open(path, encoding="utf-8", errors="strict") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            rows += 1
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                errors.append(f"{path.name}:{line_number} is invalid JSON")
                continue
            timestamp = _parse_aware_timestamp(value.get("timestamp")) if isinstance(value, dict) else None
            try:
                evidence_class = EvidenceClass(value.get("evidence_class")) if isinstance(value, dict) else None
            except (TypeError, ValueError):
                evidence_class = None
            if timestamp is None or evidence_class is None:
                errors.append(f"{path.name}:{line_number} lacks a valid timestamp/evidence_class")
                continue
            timestamps.append(timestamp)
            evidence_classes.append(evidence_class)
    if not timestamps:
        return rows, None, None, math.inf, None, errors or [f"{path.name} has no valid market rows"]
    if timestamps != sorted(timestamps):
        errors.append(f"{path.name} timestamps are not monotonic")
    ordered = sorted(timestamps)
    max_gap = max(((right - left).total_seconds() for left, right in zip(ordered, ordered[1:])), default=0.0)
    rank = {item: index for index, item in enumerate(EvidenceClass)}
    return rows, ordered[0], ordered[-1], max_gap, max(evidence_classes, key=rank.get), errors


def inspect_data_manifest(
    record: Mapping[str, Any],
    allowed_gap_seconds: float,
    session_dir: Path,
    session_date: str,
) -> tuple[list[str], set[str], EvidenceClass | None, dict[str, Path], dict[str, str], dict[str, Path]]:
    """Recompute hashes, rows, coverage, gaps, and evidence class from disk."""
    errors: list[str] = []
    required = {"files", "max_gap_seconds", "evidence_class_max", "complete"}
    missing = required.difference(record)
    if missing:
        return [f"missing data manifest fields: {sorted(missing)}"], set(), None, {}, {}, {}
    if record["complete"] is not True:
        errors.append("data manifest complete must be exactly true")
    files = record["files"]
    if not isinstance(files, list) or not files:
        return errors + ["files must be a non-empty list"], set(), None, {}, {}, {}

    manifest_hashes: set[str] = set()
    seen_roles: set[str] = set()
    seen_ref_paths: set[Path] = set()
    seen_ref_hashes: set[str] = set()
    role_to_hash: dict[str, str] = {}
    role_to_path: dict[str, Path] = {}
    stream_classes: list[EvidenceClass] = []
    derived_gaps: list[float] = []
    session_day = date.fromisoformat(session_date)
    open_at = _market_datetime(session_day, MARKET_OPEN)
    close_at = _market_datetime(session_day, SESSION_CLOSE)
    stream_count = 0
    market_sources: dict[str, Path] = {}
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            errors.append(f"files[{index}] must be an object")
            continue
        required_file = {"path", "kind", "sha256", "rows", "first_ts", "last_ts"}
        missing_file = required_file.difference(item)
        if missing_file:
            errors.append(f"files[{index}] missing fields: {sorted(missing_file)}")
            continue
        path = _safe_manifest_path(session_dir, item["path"])
        if path is None:
            errors.append(f"files[{index}].path is missing, unsafe, or outside the session")
            continue
        raw = path.read_bytes()
        actual_hash = _sha256_bytes(raw)
        if item["sha256"] != actual_hash:
            errors.append(f"files[{index}].sha256 does not match file bytes")
        manifest_hashes.add(actual_hash)
        kind = item["kind"]
        if kind == "REFERENCE":
            role = item.get("role")
            if role is not None:
                if role not in ALLOWED_REFERENCE_ROLES:
                    errors.append(f"files[{index}].role {role} is not an allowed reference role")
                elif role in seen_roles:
                    errors.append(f"duplicate reference role {role} in data manifest")
                seen_roles.add(role)
                role_to_hash[role] = actual_hash
                role_to_path[role] = path
            if path in seen_ref_paths:
                errors.append(f"reference path {path.name} used multiple times")
            seen_ref_paths.add(path)
            if actual_hash in seen_ref_hashes:
                errors.append(f"reference hash {actual_hash} used for multiple reference files")
            seen_ref_hashes.add(actual_hash)

            actual_rows = sum(1 for line in raw.splitlines() if line.strip())
            if actual_rows <= 0 or item["rows"] != actual_rows:
                errors.append(f"files[{index}].rows does not match non-empty reference rows")
            if item["first_ts"] is not None or item["last_ts"] is not None:
                errors.append(f"files[{index}] reference timestamps must be null")
            continue
        if kind != "MARKET_STREAM":
            errors.append(f"files[{index}].kind must be REFERENCE or MARKET_STREAM")
            continue
        stream_count += 1
        market_sources[actual_hash] = path
        rows, first_ts, last_ts, max_gap, evidence_class, stream_errors = _inspect_market_stream(path)
        errors.extend(stream_errors)
        if item["rows"] != rows or rows <= 0:
            errors.append(f"files[{index}].rows does not match parsed market rows")
        declared_first = _parse_aware_timestamp(item["first_ts"])
        declared_last = _parse_aware_timestamp(item["last_ts"])
        if first_ts is None or last_ts is None or declared_first != first_ts or declared_last != last_ts:
            errors.append(f"files[{index}] declared timestamps do not match parsed data")
        elif first_ts > open_at or last_ts < close_at:
            errors.append(f"files[{index}] does not cover the full 09:15-15:30 session")
        if max_gap > allowed_gap_seconds or max_gap > MAX_CAPTURE_GAP_SECONDS:
            errors.append(f"files[{index}] derived gap exceeds the allowed limit")
        derived_gaps.append(max_gap)
        if evidence_class is not None:
            stream_classes.append(evidence_class)
    if stream_count == 0:
        errors.append("at least one MARKET_STREAM file is required")
    derived_max_gap = max(derived_gaps, default=math.inf)
    if not _is_finite_number(record["max_gap_seconds"], minimum=0.0) or float(record["max_gap_seconds"]) != derived_max_gap:
        errors.append("max_gap_seconds does not match parsed market data")
    rank = {item: index for index, item in enumerate(EvidenceClass)}
    derived_class = max(stream_classes, key=rank.get) if stream_classes else None
    if derived_class is None or record["evidence_class_max"] != derived_class.value:
        errors.append("evidence_class_max does not match parsed market data")
    return errors, manifest_hashes, derived_class, market_sources, role_to_hash, role_to_path


def validate_data_manifest(
    record: Mapping[str, Any],
    allowed_gap_seconds: float,
    session_dir: str | os.PathLike[str],
    session_date: str,
) -> list[str]:
    return inspect_data_manifest(record, allowed_gap_seconds, Path(session_dir), session_date)[0]


def evidence_counts_toward_20(record: Mapping[str, Any]) -> bool:
    """Perform the context-free portion of strict E3 fill validation."""
    try:
        evidence_class = EvidenceClass(record.get("evidence_class"))
        fill_state = FillState(record.get("fill_state"))
    except (TypeError, ValueError):
        return False
    if evidence_class is not EvidenceClass.E3_TICK_QUEUE or fill_state is not FillState.FILLED:
        return False
    numeric_fields = ("queue_rank", "order_qty", "cum_volume_at_or_better")
    if not all(_is_finite_number(record.get(field), minimum=0.0) for field in numeric_fields):
        return False
    if any(isinstance(record[field], bool) or not isinstance(record[field], int) for field in numeric_fields):
        return False
    order_qty = int(record["order_qty"])
    if order_qty <= 0:
        return False
    return int(record["cum_volume_at_or_better"]) >= int(record["queue_rank"]) + order_qty


def _derive_signals_from_streams(
    market_sources: Mapping[str, Path],
    preregistration: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Derive prospective signals from hashed streams, never caller counters."""
    signals: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    session_day = date.fromisoformat(str(preregistration["session_date"]))
    start = _market_datetime(session_day, ORB_SIGNAL_START)
    end = _market_datetime(session_day, LAST_ENTRY_TIME)
    expected_rules_hash = _sha256_bytes(_canonical_json(preregistration["order_rules"]))
    for path in market_sources.values():
        with open(path, encoding="utf-8", errors="strict") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict) or event.get("event_type") != "SIGNAL":
                    continue
                timestamp = _parse_aware_timestamp(event.get("timestamp"))
                order_spec = event.get("order_spec")
                signal_id = event.get("signal_id")
                recorded_at = _parse_aware_timestamp(event.get("recorded_at"))
                if recorded_at is None or recorded_at.date() != session_day:
                    errors.append(f"{path.name}:{line_number} signal recorded_at is missing or outside session date")
                    continue
                if timestamp is None or not start <= timestamp <= end:
                    errors.append(f"{path.name}:{line_number} signal is outside the entry window")
                    continue
                if abs((recorded_at - timestamp).total_seconds()) > 120.0:
                    errors.append(f"{path.name}:{line_number} signal freshness exceeded maximum allowed age")
                    continue
                if not isinstance(signal_id, str) or not signal_id.strip():
                    errors.append(f"{path.name}:{line_number} signal_id is invalid")
                    continue
                if not isinstance(order_spec, dict) or order_spec.get("strategy_rules_sha256") != expected_rules_hash:
                    errors.append(f"{path.name}:{line_number} signal order_spec is not bound to strategy rules")
                    continue
                if event.get("symbol") != order_spec.get("symbol"):
                    errors.append(f"{path.name}:{line_number} signal event symbol does not match order_spec symbol")
                    continue
                order_hash = _sha256_bytes(_canonical_json(order_spec))
                if event.get("order_hash") != order_hash or order_hash in signals:
                    errors.append(f"{path.name}:{line_number} signal order hash is invalid or duplicated")
                    continue
                signals[order_hash] = {"timestamp": timestamp, "signal_id": signal_id, "order_spec": order_spec}
    return signals, errors


def _validate_fill_against_market_stream(
    record: Mapping[str, Any],
    stream_path: Path,
    preregistration: Mapping[str, Any],
) -> list[str]:
    """Derive quote, queue and turnover claims from the hashed JSONL stream."""
    errors: list[str] = []
    signal_ts = _parse_aware_timestamp(record.get("signal_timestamp"))
    arrival_ts = _parse_aware_timestamp(record.get("order_arrival_timestamp"))
    window_start = _parse_aware_timestamp(record.get("volume_window_start_timestamp"))
    claimed_fill_ts = _parse_aware_timestamp(record.get("fill_timestamp"))
    if None in (signal_ts, arrival_ts, window_start, claimed_fill_ts):
        return ["fill timestamps cannot be derived from the market stream"]
    order_spec = record.get("order_spec")
    if not isinstance(order_spec, dict):
        return ["order_spec is unavailable for market-stream derivation"]
    symbol = order_spec.get("symbol")
    side = record.get("order_side")
    limit_price_value = record.get("limit_price")
    order_qty_value = record.get("order_qty")
    if not _is_finite_number(limit_price_value, minimum=0.001):
        return ["limit price is unavailable for market-stream derivation"]
    if (isinstance(order_qty_value, bool) or not isinstance(order_qty_value, int)
            or order_qty_value <= 0):
        return ["order quantity is unavailable for market-stream derivation"]
    limit_price = float(limit_price_value)
    quote_events: list[tuple[datetime, dict[str, Any]]] = []
    trade_events: list[tuple[datetime, float, int]] = []
    symbol_timestamps: list[datetime] = []
    seen_trade_ids: set[str] = set()
    with open(stream_path, encoding="utf-8", errors="strict") as source:
        for line in source:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("symbol") != symbol:
                continue
            timestamp = _parse_aware_timestamp(event.get("timestamp"))
            if timestamp is None:
                continue
            symbol_timestamps.append(timestamp)
            if event.get("event_type") == "QUOTE":
                quote_events.append((timestamp, event))
            elif event.get("event_type") == "TRADE":
                price, quantity = event.get("price"), event.get("quantity")
                trade_id = event.get("trade_id")
                if not isinstance(trade_id, str) or not trade_id.strip() or trade_id in seen_trade_ids:
                    errors.append("market stream contains a missing or duplicate trade_id")
                    continue
                seen_trade_ids.add(trade_id)
                if _is_finite_number(price, minimum=0.001) and isinstance(quantity, int) and not isinstance(quantity, bool) and quantity > 0:
                    trade_events.append((timestamp, float(price), quantity))
    session_day = date.fromisoformat(str(preregistration["session_date"]))
    open_at = _market_datetime(session_day, MARKET_OPEN)
    close_at = _market_datetime(session_day, SESSION_CLOSE)
    ordered_symbol_ts = sorted(symbol_timestamps)
    symbol_max_gap = max(
        ((right - left).total_seconds() for left, right in zip(ordered_symbol_ts, ordered_symbol_ts[1:])),
        default=math.inf,
    )
    if (not ordered_symbol_ts or ordered_symbol_ts[0] > open_at or ordered_symbol_ts[-1] < close_at
            or symbol_max_gap > float(preregistration["gap_seconds"])):
        errors.append("per-symbol stream coverage or continuity is insufficient")
    eligible_quotes = [event for timestamp, event in quote_events if signal_ts <= timestamp <= arrival_ts]
    if not eligible_quotes:
        return ["no manifested arrival quote exists between signal and order arrival"]
    arrival_event = eligible_quotes[-1]
    claimed_quote = record.get("arrival_quote")
    for field in ("best_bid", "best_ask", "bid_qty", "ask_qty"):
        if not isinstance(claimed_quote, dict) or claimed_quote.get(field) != arrival_event.get(field):
            errors.append(f"arrival_quote.{field} does not match manifested quote")

    derived_queue_rank: int | None = None
    best_bid = arrival_event.get("best_bid")
    best_ask = arrival_event.get("best_ask")
    if side == "BUY" and _is_finite_number(best_bid) and limit_price == float(best_bid):
        derived_queue_rank = arrival_event.get("bid_qty")
    elif side == "SELL" and _is_finite_number(best_ask) and limit_price == float(best_ask):
        derived_queue_rank = arrival_event.get("ask_qty")
    if not isinstance(derived_queue_rank, int) or isinstance(derived_queue_rank, bool) or derived_queue_rank < 0:
        errors.append("queue rank cannot be derived from top-of-book at the order price")
        return errors
    if record.get("queue_rank") != derived_queue_rank:
        errors.append("queue_rank does not match manifested top-of-book quantity")

    cumulative = 0
    derived_fill_ts: datetime | None = None
    haircut = 1.0 - float(preregistration["queue_haircut"])
    required = derived_queue_rank + order_qty_value
    for timestamp, price, quantity in sorted(trade_events):
        if timestamp < window_start or timestamp > claimed_fill_ts:
            continue
        is_at_or_better = price <= limit_price if side == "BUY" else price >= limit_price
        if is_at_or_better:
            cumulative += quantity
            if cumulative * haircut >= required and derived_fill_ts is None:
                derived_fill_ts = timestamp
    if record.get("cum_volume_at_or_better") != cumulative:
        errors.append("cum_volume_at_or_better does not match manifested trade prints")
    if derived_fill_ts is None or derived_fill_ts != claimed_fill_ts:
        errors.append("fill_timestamp is not the manifested queue-clear timestamp")
    return errors


def validate_qualifying_fill(
    record: Mapping[str, Any],
    *,
    session_date: str,
    manifest_hashes: set[str],
    market_sources: Mapping[str, Path],
    preregistration: Mapping[str, Any],
    market_first_ts: datetime,
    market_last_ts: datetime,
    derived_signals: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    """Validate provenance fields required before an E3 fill may count."""
    errors: list[str] = []
    required = {
        "order_id",
        "evidence_class",
        "fill_state",
        "preregistered_order_hash",
        "order_spec",
        "signal_timestamp",
        "order_arrival_timestamp",
        "volume_window_start_timestamp",
        "fill_timestamp",
        "order_side",
        "limit_price",
        "order_qty",
        "queue_rank",
        "cum_volume_at_or_better",
        "arrival_quote",
        "capture_continuous",
        "source_data_sha256",
        "full_cost_deduction",
    }
    missing = required.difference(record)
    if missing:
        return [f"missing fill evidence fields: {sorted(missing)}"]
    if not isinstance(record["order_id"], str) or not record["order_id"].strip():
        errors.append("order_id must be a non-empty string")
    order_spec = record["order_spec"]
    if not isinstance(order_spec, dict) or not order_spec:
        errors.append("order_spec must be a non-empty object")
    else:
        expected_rules_hash = _sha256_bytes(_canonical_json(preregistration["order_rules"]))
        if order_spec.get("strategy_rules_sha256") != expected_rules_hash:
            errors.append("order_spec is not bound to preregistered order_rules")
        expected_order_hash = _sha256_bytes(_canonical_json(order_spec))
        if record["preregistered_order_hash"] != expected_order_hash:
            errors.append("preregistered_order_hash does not match order_spec")
        if (order_spec.get("side") != record["order_side"]
                or order_spec.get("limit_price") != record["limit_price"]
                or order_spec.get("quantity") != record["order_qty"]):
            errors.append("order_spec does not match the execution record")
        signal_record = derived_signals.get(expected_order_hash)
        if signal_record is None:
            errors.append("order_spec has no matching signal in a hashed market stream")
        elif _parse_aware_timestamp(record["signal_timestamp"]) != signal_record["timestamp"]:
            errors.append("signal_timestamp does not match the manifested signal")
    signal_timestamp = _parse_aware_timestamp(record["signal_timestamp"])
    arrival_timestamp = _parse_aware_timestamp(record["order_arrival_timestamp"])
    window_start = _parse_aware_timestamp(record["volume_window_start_timestamp"])
    fill_timestamp = _parse_aware_timestamp(record["fill_timestamp"])
    session_day = date.fromisoformat(session_date)
    signal_start = _market_datetime(session_day, ORB_SIGNAL_START)
    last_entry = _market_datetime(session_day, LAST_ENTRY_TIME)
    if signal_timestamp is None or not signal_start <= signal_timestamp <= last_entry:
        errors.append("signal_timestamp must be within the 09:30-15:15 IST entry window")
    latency = timedelta(milliseconds=float(preregistration["latency_ms"]))
    if arrival_timestamp is None or signal_timestamp is None or arrival_timestamp < signal_timestamp + latency:
        errors.append("order_arrival_timestamp must include preregistered signal latency")
    if window_start is None or arrival_timestamp is None or window_start < arrival_timestamp:
        errors.append("volume window must start at or after order arrival")
    if fill_timestamp is None or window_start is None or fill_timestamp < window_start:
        errors.append("fill_timestamp must be at or after the volume window starts")
    if (signal_timestamp and signal_timestamp < market_first_ts) or (fill_timestamp and fill_timestamp > market_last_ts):
        errors.append("fill timing falls outside manifested market coverage")
    if record["order_side"] not in {"BUY", "SELL"}:
        errors.append("order_side must be BUY or SELL")
    if not _is_finite_number(record["limit_price"], minimum=0.001):
        errors.append("limit_price must be positive and finite")
    quote = record["arrival_quote"]
    if not isinstance(quote, dict):
        errors.append("arrival_quote must be an object")
    else:
        quote_errors = False
        for field in ("best_bid", "best_ask", "bid_qty", "ask_qty"):
            if not _is_finite_number(quote.get(field), minimum=0.0):
                errors.append(f"arrival_quote.{field} must be non-negative and finite")
                quote_errors = True
        if not quote_errors and float(quote["best_bid"]) > float(quote["best_ask"]):
            errors.append("arrival_quote is crossed")
    if record["capture_continuous"] is not True:
        errors.append("capture_continuous must be exactly true")
    source_hash = record["source_data_sha256"]
    if not _is_sha256(source_hash) or source_hash not in manifest_hashes or source_hash not in market_sources:
        errors.append("source_data_sha256 is not bound to a manifested source file")
    minimum_cost = preregistration["cost_model"].get("minimum_cost_rs")
    if (not _is_finite_number(minimum_cost, minimum=0.001)
            or not _is_finite_number(record["full_cost_deduction"], minimum=float(minimum_cost or math.inf))):
        errors.append("full_cost_deduction must satisfy the preregistered minimum")
    if not evidence_counts_toward_20(record):
        errors.append("E3 queue-plus-size fill condition is not satisfied")
    elif _is_finite_number(preregistration["queue_haircut"], minimum=0.0):
        effective_volume = int(record["cum_volume_at_or_better"]) * (1.0 - float(preregistration["queue_haircut"]))
        required_volume = int(record["queue_rank"]) + int(record["order_qty"])
        if effective_volume < required_volume:
            errors.append("haircut-adjusted turnover does not clear queue rank plus order quantity")
    if source_hash in market_sources:
        errors.extend(_validate_fill_against_market_stream(record, market_sources[source_hash], preregistration))
    return errors


def evaluate_session(
    session_dir: str | os.PathLike[str],
    preflight: Mapping[str, Any],
    data_manifest: Mapping[str, Any],
    *,
    session_closed_at: str,
    fill_evidence: Iterable[Mapping[str, Any]] = (),
    legacy_unverified: bool = False,
) -> SessionVerdict:
    directory = Path(session_dir)
    date_hint = directory.name
    if legacy_unverified:
        return SessionVerdict("TRACK2", date_hint, SessionStatus.PILOT_UNVERIFIED, ("legacy session lacks prospective evidence",), 0, 0, 0, 0)

    preregistration, errors = verify_locked_preregistration(directory)
    if preregistration is None:
        return SessionVerdict("TRACK2", date_hint, SessionStatus.VOID, tuple(errors), 0, 0, 0, 0)
    session_date = str(preregistration["session_date"])
    if directory.name != session_date:
        errors.append("session directory name must match preregistration session_date")
    frozen_at = _parse_aware_timestamp(preregistration["frozen_at"])
    if frozen_at is None:
        errors.append("preregistration frozen_at is invalid")
        frozen_at = _market_datetime(date.fromisoformat(session_date), time(0, 0))
    sealed_preflight, preflight_errors = verify_locked_preflight(directory)
    errors.extend(preflight_errors)
    if sealed_preflight is None:
        effective_preflight: Mapping[str, Any] = {}
    else:
        effective_preflight = sealed_preflight
        try:
            if _canonical_json(preflight) != _canonical_json(sealed_preflight):
                errors.append("supplied preflight does not match immutable sealed preflight")
                if preflight.get("universe_sha256") != sealed_preflight.get("universe_sha256"):
                    errors.append("supplied preflight universe_sha256 differs from sealed preflight")
        except (TypeError, ValueError):
            errors.append("supplied preflight is not canonical JSON")
    errors.extend(validate_preflight(effective_preflight, session_date, frozen_at))
    manifest_errors, manifest_hashes, derived_evidence_class, market_sources, role_to_hash, role_to_path = inspect_data_manifest(
        data_manifest,
        float(preregistration["gap_seconds"]),
        directory,
        session_date,
    )
    errors.extend(manifest_errors)

    # Role bindings verification:
    # 1. Every reference file must match its exact role hash
    role_field_mapping = {
        "FNO": "fno_source_sha256",
        "SURVEILLANCE": "surveillance_source_sha256",
        "BAND_POLICY": "band_source_sha256",
        "UNIVERSE": "universe_sha256",
    }
    for role, field_name in role_field_mapping.items():
        claimed_hash = effective_preflight.get(field_name)
        actual_role_hash = role_to_hash.get(role)
        if actual_role_hash is None:
            errors.append(f"required reference role {role} is missing from data manifest")
        elif claimed_hash != actual_role_hash:
            errors.append(f"{field_name} does not match {role} role file hash")

    # 2. Market stream can never satisfy a reference role
    for role, r_hash in role_to_hash.items():
        if r_hash in market_sources:
            errors.append(f"market stream hash {r_hash} cannot satisfy reference role {role}")

    # 3. preregistration universe_sha256 must match preflight universe_sha256 and UNIVERSE role
    if effective_preflight.get("universe_sha256") and preregistration.get("universe_sha256"):
        if effective_preflight["universe_sha256"] != preregistration["universe_sha256"]:
            errors.append("preflight universe_sha256 does not match preregistration universe_sha256")
        if "UNIVERSE" in role_to_hash and role_to_hash["UNIVERSE"] != preregistration["universe_sha256"]:
            errors.append("UNIVERSE role file hash does not match preregistration universe_sha256")

    # 4. Load canonical frozen universe symbols if UNIVERSE role is present
    frozen_symbols: set[str] | None = None
    if "UNIVERSE" in role_to_path:
        u_path = role_to_path["UNIVERSE"]
        try:
            u_data = json.loads(u_path.read_text(encoding="utf-8"))
            if isinstance(u_data, dict) and isinstance(u_data.get("symbols"), list):
                frozen_symbols = set(str(s).strip().upper() for s in u_data["symbols"])
                if len(frozen_symbols) < 4:
                    errors.append("frozen universe has fewer than four symbols")
            else:
                errors.append("UNIVERSE role file does not contain valid symbols list")
        except Exception as exc:
            errors.append(f"failed to read UNIVERSE role file: {exc}")

    # 5. Scan all market streams to independently verify no out-of-universe quotes, depth, or signals
    if frozen_symbols is not None:
        for m_path in market_sources.values():
            try:
                with open(m_path, encoding="utf-8", errors="strict") as m_source:
                    for line_num, m_line in enumerate(m_source, start=1):
                        if not m_line.strip():
                            continue
                        try:
                            m_ev = json.loads(m_line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(m_ev, dict):
                            continue
                        s = m_ev.get("symbol")
                        if s and str(s).strip().upper() not in frozen_symbols:
                            errors.append(f"{m_path.name}:{line_num} event symbol {s} is outside frozen universe")
                            break
            except Exception as exc:
                errors.append(f"failed to scan market stream {m_path.name}: {exc}")

    # A session attempt must be registered before evidence collection. Failed
    # days therefore remain visible instead of disappearing from the sample.
    attempt_records, attempt_errors = verify_attempt_registry(directory.parent)
    errors.extend(attempt_errors)
    matching_attempts = [item for item in attempt_records if item.get("session_date") == session_date]
    if not matching_attempts or matching_attempts[0].get("attempt_outcome") != "PENDING":
        errors.append("session attempt registry lacks an initial PENDING record")
    else:
        pending_at = _parse_aware_timestamp(matching_attempts[0].get("recorded_at"))
        attempt_deadline = _market_datetime(date.fromisoformat(session_date), MARKET_OPEN)
        if pending_at is None or pending_at >= attempt_deadline:
            errors.append("session PENDING attempt must be registered before 09:15 IST")

    # Checkpoints bind the append-only stream while the session is in progress.
    stream_items = [item for item in data_manifest.get("files", ()) if isinstance(item, dict) and item.get("kind") == "MARKET_STREAM"]
    stream_file: Path | None = None
    if len(stream_items) == 1:
        st_hash = stream_items[0].get("sha256")
        stream_file = market_sources.get(st_hash)

    checkpoints, checkpoint_errors = verify_stream_checkpoints(directory.parent, session_date, stream_path=stream_file)
    errors.extend(checkpoint_errors)
    if not checkpoints:
        errors.append("stream checkpoint evidence is missing")
    else:
        first_checkpoint_at = _parse_aware_timestamp(checkpoints[0].get("recorded_at"))
        final_checkpoint_at = _parse_aware_timestamp(checkpoints[-1].get("recorded_at"))
        checkpoint_session_day = date.fromisoformat(session_date)
        first_deadline = _market_datetime(checkpoint_session_day, MARKET_OPEN) + timedelta(minutes=6)
        if first_checkpoint_at is None or first_checkpoint_at > first_deadline:
            errors.append("first stream checkpoint must be recorded by 09:21 IST")
        if final_checkpoint_at is None or final_checkpoint_at < _market_datetime(checkpoint_session_day, SESSION_CLOSE):
            errors.append("final stream checkpoint must be recorded at or after 15:30 IST")
        if len(stream_items) != 1:
            errors.append("exactly one MARKET_STREAM is required for checkpoint binding")
        elif stream_file is not None:
            final_checkpoint = checkpoints[-1]
            stream_item = stream_items[0]
            stream_bytes = stream_file.read_bytes()
            if final_checkpoint.get("stream_sha256") != stream_item.get("sha256"):
                errors.append("final stream checkpoint digest does not match market stream")
            if final_checkpoint.get("row_count") != stream_item.get("rows"):
                errors.append("final stream checkpoint row_count does not match market stream")
            if final_checkpoint.get("byte_count") != len(stream_bytes):
                errors.append("final stream checkpoint byte_count does not match market stream length")
            if final_checkpoint.get("stream_sha256") != _sha256_bytes(stream_bytes):
                errors.append("final stream checkpoint hash does not match market stream bytes")

            # Verify cadence between checkpoints (max 300s between active periodic checkpoints)
            for idx in range(len(checkpoints) - 1):
                cur_cp_ts = _parse_aware_timestamp(checkpoints[idx].get("recorded_at"))
                next_cp_ts = _parse_aware_timestamp(checkpoints[idx + 1].get("recorded_at"))
                # If there's a gap between checkpoints, check cadence:
                # Active recording must not have undocumented multi-hour gaps in checkpoints if stream grew
                if cur_cp_ts and next_cp_ts:
                    gap = (next_cp_ts - cur_cp_ts).total_seconds()
                    # Allow up to 3600s gap only if no bytes were written, but if bytes grew, cadence must be bounded
                    if checkpoints[idx + 1].get("byte_count", 0) > checkpoints[idx].get("byte_count", 0):
                        # Gaps between checkpoints when stream is actively growing should not exceed 300s (with grace to 360s)
                        if gap > 360.0:
                            errors.append(f"stream checkpoint cadence gap ({gap}s) exceeded bounded interval between seq {checkpoints[idx].get('sequence')} and {checkpoints[idx + 1].get('sequence')}")

    closed_at = _parse_aware_timestamp(session_closed_at)
    current_time = _now_ist().astimezone(IST)
    session_day = date.fromisoformat(session_date)
    close_at = _market_datetime(session_day, SESSION_CLOSE)
    if closed_at is None or closed_at < close_at or closed_at > current_time:
        errors.append("session_closed_at must be after 15:30 IST and not in the future")
    derived_signals, signal_errors = _derive_signals_from_streams(market_sources, preregistration)
    errors.extend(signal_errors)
    if errors:
        return SessionVerdict("TRACK2", session_date, SessionStatus.VOID, tuple(errors), 0, 0, 0, 0)

    signals = len(derived_signals)

    stream_items = [item for item in data_manifest["files"] if item.get("kind") == "MARKET_STREAM"]
    market_first_ts = min(_parse_aware_timestamp(item["first_ts"]) for item in stream_items)
    market_last_ts = max(_parse_aware_timestamp(item["last_ts"]) for item in stream_items)
    qualifying_fills = 0
    seen_order_ids: set[str] = set()
    consumed_signal_hashes: set[str] = set()
    manifest_supports_e3 = derived_evidence_class is EvidenceClass.E3_TICK_QUEUE
    for record in fill_evidence:
        order_id = str(record.get("order_id", "")).strip().casefold()
        fill_errors = validate_qualifying_fill(
            record,
            session_date=session_date,
            manifest_hashes=manifest_hashes,
            market_sources=market_sources,
            preregistration=preregistration,
            market_first_ts=market_first_ts,
            market_last_ts=market_last_ts,
            derived_signals=derived_signals,
        )
        if not manifest_supports_e3:
            fill_errors.append("data manifest does not support E3 evidence")
        if order_id in seen_order_ids:
            fill_errors.append("duplicate order_id")
        signal_hash = str(record.get("preregistered_order_hash", ""))
        if signal_hash in consumed_signal_hashes:
            fill_errors.append("signal already consumed by another qualifying fill")
        if not fill_errors:
            qualifying_fills += 1
            seen_order_ids.add(order_id)
            consumed_signal_hashes.add(signal_hash)
    if qualifying_fills > signals:
        return SessionVerdict(
            "TRACK2", session_date, SessionStatus.VOID,
            ("qualifying fills cannot exceed prospective signals",), 0, 0, 0, 0,
        )
    if preregistration["qualification_mode"] == "REHEARSAL":
        return SessionVerdict(
            track_id="TRACK2",
            session_date=session_date,
            status=SessionStatus.REHEARSAL,
            void_reasons=("explicitly non-counting live-market rehearsal",),
            signals=signals,
            qualifying_fills=0,
            counts_toward_60=0,
            counts_toward_20=0,
            qualifying_order_ids=(),
        )
    return SessionVerdict(
        track_id="TRACK2",
        session_date=session_date,
        status=SessionStatus.COUNTED,
        void_reasons=(),
        signals=signals,
        qualifying_fills=qualifying_fills,
        counts_toward_60=1,
        counts_toward_20=qualifying_fills,
        qualifying_order_ids=tuple(sorted(seen_order_ids)),
    )


def compute_gate_counts(verdicts: Iterable[SessionVerdict]) -> dict[str, int]:
    values = list(verdicts)
    dates = [item.session_date for item in values if item.counts_toward_60]
    if len(dates) != len(set(dates)):
        raise ValueError("duplicate counted session_date")
    order_ids = [order_id for item in values for order_id in item.qualifying_order_ids]
    if len(order_ids) != len(set(order_ids)):
        raise ValueError("duplicate qualifying order_id across sessions")
    for item in values:
        if item.track_id != "TRACK2":
            raise ValueError("non-TRACK2 verdict cannot enter Track 2 counters")
        if item.counts_toward_60 not in (0, 1) or item.counts_toward_20 != len(item.qualifying_order_ids):
            raise ValueError("verdict counters are internally inconsistent")
        if item.status is SessionStatus.REHEARSAL and (
            item.counts_toward_60 != 0 or item.counts_toward_20 != 0
        ):
            raise ValueError("rehearsal verdict cannot contribute to qualification gates")
    return {
        "prospective_sessions": sum(item.counts_toward_60 for item in values),
        "realistically_fillable_entries": sum(item.counts_toward_20 for item in values),
        "void_sessions": sum(item.status is SessionStatus.VOID for item in values),
        "legacy_unverified_sessions": sum(item.status is SessionStatus.PILOT_UNVERIFIED for item in values),
        "rehearsal_sessions": sum(item.status is SessionStatus.REHEARSAL for item in values),
    }
