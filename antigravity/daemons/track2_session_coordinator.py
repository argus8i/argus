"""Fail-closed Track 2 prospective paper-session coordinator.

This module joins official NSE source ingestion to the hardened session recorder.
It contains no broker routes, credentials, live-order capabilities, P&L updates,
trade-log writes, or qualification-counter mutation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from antigravity.daemons.track2_official_source_ingestor import (
    PARSER_VERSION,
    SYMBOL_REGEX,
    Track2OfficialSourceIngestor,
    replay_and_verify_sources,
)
from antigravity.daemons.track2_session_recorder import Track2SessionRecorder
from antigravity.models.session_manifest import (
    ALLOWED_QUALIFICATION_MODES,
    IST,
    SessionStatus,
    SessionVerdict,
    _canonical_json,
    _now_ist,
    _sha256_bytes,
    verify_attempt_registry,
    verify_locked_preflight,
    verify_locked_preregistration,
    write_attempt_record,
    write_locked_preflight,
    write_locked_preregistration,
)


PREPARATION_CUTOFF = time(9, 0)
REQUIRED_POLICY_CIRCULARS = {"NSE/FAOP/62241", "NSE/FAOP/63405"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CoordinatorState(str, Enum):
    NEW = "NEW"
    PREPARED = "PREPARED"
    RECORDING = "RECORDING"
    FINALIZED = "FINALIZED"
    VOID = "VOID"
    REHEARSAL = "REHEARSAL"


@dataclass(frozen=True)
class PreparationResult:
    state: CoordinatorState
    session_date: str
    reason: str | None
    eligible_symbols: tuple[str, ...] = ()


class SessionWriterLock:
    """Hold an OS-released exclusive byte lock for one session writer."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            handle.close()
            raise ValueError("another coordinator already owns this session") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}:{uuid.uuid4().hex}".encode("ascii"))
        handle.flush()
        os.fsync(handle.fileno())
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            self._handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


def _exclusive_write(path: Path, payload: bytes) -> str:
    """Write immutable evidence without replacing an existing artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as exc:
        if path.is_file() and path.read_bytes() == payload:
            return hashlib.sha256(payload).hexdigest()
        raise FileExistsError(f"immutable evidence already exists with different bytes: {path.name}") from exc
    with os.fdopen(descriptor, "wb") as target:
        target.write(payload)
        target.flush()
        os.fsync(target.fileno())
    return hashlib.sha256(payload).hexdigest()


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} artifact is missing or unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} artifact is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} artifact must be a JSON object")
    return value


def validate_band_policy(path: Path) -> tuple[dict[str, Any], str]:
    """Validate the versioned static NSE dynamic-range policy artifact."""
    policy = _load_json_object(path, "band policy")
    required = {"policy_id", "effective_from", "market", "applies_to", "circulars"}
    missing = required.difference(policy)
    if missing:
        raise ValueError(f"band policy missing fields: {sorted(missing)}")
    if policy["market"] != "NSE_CASH" or policy["applies_to"] != "ACTIVE_FNO_UNDERLYINGS":
        raise ValueError("band policy scope must be NSE cash active F&O underlyings")
    if not isinstance(policy["policy_id"], str) or not policy["policy_id"].strip():
        raise ValueError("band policy_id is invalid")
    try:
        date.fromisoformat(str(policy["effective_from"]))
    except ValueError as exc:
        raise ValueError("band policy effective_from must be YYYY-MM-DD") from exc
    circulars = policy["circulars"]
    if not isinstance(circulars, list) or len(circulars) < 2:
        raise ValueError("band policy must contain both governing circulars")
    identifiers: set[str] = set()
    for item in circulars:
        if not isinstance(item, dict):
            raise ValueError("band policy circular entries must be objects")
        identifier = item.get("id")
        url = item.get("url")
        digest = item.get("sha256")
        relative_path = item.get("path")
        if not isinstance(identifier, str) or not isinstance(url, str) or not url.startswith("https://nsearchives.nseindia.com/"):
            raise ValueError("band policy circular identity or URL is invalid")
        if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
            raise ValueError("band policy circular sha256 is invalid")
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise ValueError("band policy circular local path is invalid")
        circular_path = path.parent / relative_path
        if circular_path.is_symlink():
            raise ValueError("band policy circular cannot be a symlink")
        try:
            resolved_circular = circular_path.resolve(strict=True)
            resolved_circular.relative_to(path.parent.resolve(strict=True))
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise ValueError("band policy circular file is missing or outside policy directory") from exc
        if hashlib.sha256(resolved_circular.read_bytes()).hexdigest() != digest:
            raise ValueError("band policy circular file hash mismatch")
        identifiers.add(identifier)
    if not REQUIRED_POLICY_CIRCULARS.issubset(identifiers):
        raise ValueError("band policy omits NSE/FAOP/62241 or NSE/FAOP/63405")
    raw = path.read_bytes()
    return policy, hashlib.sha256(raw).hexdigest()


class Track2PaperSessionCoordinator:
    """Own one prospective Track 2 paper session from preparation to verdict."""

    def __init__(
        self,
        *,
        sessions_root: str | os.PathLike[str],
        surveillance_dir: str | os.PathLike[str],
        band_policy_path: str | os.PathLike[str],
        candidates: Sequence[str],
        config: Mapping[str, Any],
        ingestor: Track2OfficialSourceIngestor,
        feed_health_check: Callable[[], bool],
        qualification_mode: str,
        _test_clock: Callable[[], datetime] | None = None,
        **kwargs: Any,
    ) -> None:
        if "now_fn" in kwargs:
            raise TypeError("Track2PaperSessionCoordinator does not accept public now_fn")
        if kwargs:
            raise TypeError(f"unexpected coordinator arguments: {sorted(kwargs)}")
        self.sessions_root = Path(sessions_root).resolve()
        self.surveillance_dir = Path(surveillance_dir).resolve()
        self.band_policy_path = Path(band_policy_path).resolve()
        self.candidates = tuple(candidates)
        self.config = dict(config)
        self.ingestor = ingestor
        self.feed_health_check = feed_health_check
        if qualification_mode not in ALLOWED_QUALIFICATION_MODES:
            raise ValueError("qualification_mode must be explicit and valid")
        self.qualification_mode = qualification_mode
        self._now = _test_clock or _now_ist
        self.state = CoordinatorState.NEW
        self.session_date: str | None = None
        self.session_dir: Path | None = None
        self.recorder: Track2SessionRecorder | None = None
        self.reference_files: tuple[dict[str, Any], ...] = ()
        self.preflight: dict[str, Any] | None = None
        self._attempt_registered = False
        self._writer_lock: SessionWriterLock | None = None

    def _clock(self) -> datetime:
        value = self._now()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("coordinator clock must be timezone-aware")
        return value.astimezone(IST)

    def _terminal_void(self, reason: str) -> PreparationResult:
        self.state = CoordinatorState.VOID
        if self.session_date is not None and self._attempt_registered:
            try:
                write_attempt_record(
                    self.sessions_root,
                    self.session_date,
                    "VOID",
                    details={"reason": reason},
                    recorded_at=self._clock(),
                )
            except (OSError, ValueError):
                pass
        self._release_writer_lock()
        return PreparationResult(CoordinatorState.VOID, self.session_date or "UNKNOWN", reason)

    def _acquire_writer_lock(self) -> None:
        if self.session_dir is None:
            raise ValueError("session directory is not set")
        writer_lock = SessionWriterLock(self.session_dir / "session_writer.lock")
        writer_lock.acquire()
        self._writer_lock = writer_lock

    def _release_writer_lock(self) -> None:
        if self._writer_lock is not None:
            self._writer_lock.release()
            self._writer_lock = None

    def prepare(self, session_date: str) -> PreparationResult:
        """Create all prospective evidence and open the paper recorder."""
        if self.state is not CoordinatorState.NEW:
            return PreparationResult(CoordinatorState.VOID, session_date, "coordinator prepare is single-use")
        self.session_date = session_date
        try:
            session_day = date.fromisoformat(session_date)
            if session_day.isoformat() != session_date:
                raise ValueError("session_date must use canonical YYYY-MM-DD form")
            now = self._clock()
            if now.date() != session_day or now.time() >= PREPARATION_CUTOFF:
                raise ValueError("coordinator preparation must complete on session date before 09:00 IST")
            self.sessions_root.mkdir(parents=True, exist_ok=True)
            self.session_dir = self.sessions_root / session_date
            self._acquire_writer_lock()
            write_attempt_record(
                self.sessions_root, session_date, "PENDING",
                details={"qualification_mode": self.qualification_mode},
                recorded_at=now,
            )
            self._attempt_registered = True

            ingestion = self.ingestor.ingest_session(session_date)
            if ingestion.get("verified") is not True:
                raise ValueError(f"official source ingestion failed: {ingestion.get('reason', 'UNKNOWN')}")
            snapshot_path = Path(str(ingestion.get("snapshot_path", ""))).resolve()
            try:
                snapshot_path.relative_to(self.surveillance_dir)
            except ValueError as exc:
                raise ValueError("official snapshot escaped surveillance directory") from exc
            snapshot = _load_json_object(snapshot_path, "official surveillance snapshot")
            if snapshot.get("effective_session_date") != session_date or snapshot.get("parse_status") != "SUCCESS":
                raise ValueError("official snapshot session or parse status mismatch")
            snapshot_sources = snapshot.get("sources")
            if not isinstance(snapshot_sources, dict):
                raise ValueError("official snapshot sources must be an object")
            trusted_after_ingestion = self._clock()
            provenance_times = [snapshot.get("fetched_at")]
            provenance_times.extend(
                source.get("fetched_at")
                for source in snapshot_sources.values()
                if isinstance(source, dict)
            )
            for raw_timestamp in provenance_times:
                try:
                    provenance_time = datetime.fromisoformat(str(raw_timestamp)).astimezone(IST)
                except (TypeError, ValueError) as exc:
                    raise ValueError("official source provenance timestamp is invalid") from exc
                if abs((trusted_after_ingestion - provenance_time).total_seconds()) > 120:
                    raise ValueError("official source provenance clock differs from coordinator clock")
                if provenance_time.time() >= PREPARATION_CUTOFF:
                    raise ValueError("official source provenance was recorded after 09:00 IST")
            expected_lists = {
                key: snapshot.get(key, ())
                for key in ("asm_short_term", "asm_long_term", "gsm", "fno_underlyings")
            }
            replay_ok, replay_error = replay_and_verify_sources(
                snapshot_sources,
                self.surveillance_dir,
                expected_lists,
                parser_version=PARSER_VERSION,
                expected_session_date=session_date,
            )
            if not replay_ok:
                raise ValueError(f"official source replay failed: {replay_error}")

            normalized_candidates = []
            for symbol in self.candidates:
                if not isinstance(symbol, str):
                    raise ValueError("candidate symbols must be strings")
                cleaned = symbol.strip().upper()
                if not SYMBOL_REGEX.fullmatch(cleaned):
                    raise ValueError(f"invalid candidate symbol: {symbol}")
                normalized_candidates.append(cleaned)
            if len(normalized_candidates) != len(set(normalized_candidates)):
                raise ValueError("candidate symbols must be unique")
            blocked = set(expected_lists["asm_short_term"]) | set(expected_lists["asm_long_term"]) | set(expected_lists["gsm"])
            fno = set(expected_lists["fno_underlyings"])
            eligible = tuple(sorted(symbol for symbol in normalized_candidates if symbol in fno and symbol not in blocked))
            if len(eligible) < 4:
                raise ValueError("eligible frozen universe has fewer than four symbols")

            policy, policy_file_hash = validate_band_policy(self.band_policy_path)
            if date.fromisoformat(str(policy["effective_from"])) > session_day:
                raise ValueError("band policy is not yet effective for the session")
            sources_dir = self.session_dir / "sources"
            source_snapshot_hash = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
            fno_artifact = {
                "session_date": session_date,
                "source_snapshot_sha256": source_snapshot_hash,
                "source_key": "fno",
                "symbols": sorted(fno),
            }
            surveillance_artifact = {
                "session_date": session_date,
                "source_snapshot_sha256": source_snapshot_hash,
                "asm_short_term": list(expected_lists["asm_short_term"]),
                "asm_long_term": list(expected_lists["asm_long_term"]),
                "gsm": list(expected_lists["gsm"]),
            }
            policy_artifact = {
                "session_date": session_date,
                "source_policy_sha256": policy_file_hash,
                "policy": policy,
            }
            fno_hash = _exclusive_write(sources_dir / "fno.json", _canonical_json(fno_artifact))
            surveillance_hash = _exclusive_write(
                sources_dir / "surveillance.json", _canonical_json(surveillance_artifact)
            )
            band_hash = _exclusive_write(
                sources_dir / "band_policy.json", _canonical_json(policy_artifact)
            )
            universe_artifact = {
                "session_date": session_date,
                "selection_rule": "CONFIGURED_CANDIDATES_INTERSECT_FNO_EXCLUDE_ASM_GSM",
                "rule_version": "1.0.0",
                "symbols": list(eligible),
                "source_bindings": {
                    "fno_sha256": fno_hash,
                    "surveillance_sha256": surveillance_hash,
                    "band_policy_sha256": band_hash,
                },
            }
            universe_hash = _exclusive_write(
                sources_dir / "universe.json", _canonical_json(universe_artifact)
            )
            role_hashes = {fno_hash, surveillance_hash, band_hash, universe_hash}
            if len(role_hashes) != 4:
                raise ValueError("reference role hashes must be distinct")

            required_config = {
                "order_rules", "cost_model", "gap_seconds", "queue_haircut",
                "latency_ms", "gate_stats_version",
            }
            missing_config = required_config.difference(self.config)
            if missing_config:
                raise ValueError(f"coordinator config missing fields: {sorted(missing_config)}")
            preregistration = {
                "track_id": "TRACK2",
                "session_date": session_date,
                "frozen_at": now.isoformat(),
                "config_sha256": _sha256_bytes(_canonical_json(self.config)),
                "universe_sha256": universe_hash,
                "order_rules": self.config["order_rules"],
                "cost_model": self.config["cost_model"],
                "gap_seconds": self.config["gap_seconds"],
                "queue_haircut": self.config["queue_haircut"],
                "latency_ms": self.config["latency_ms"],
                "gate_stats_version": self.config["gate_stats_version"],
                "paper_only": True,
                "qualification_mode": self.qualification_mode,
            }
            write_locked_preregistration(self.session_dir, preregistration)
            if self.feed_health_check() is not True:
                raise ValueError("prospective browser feed health check failed")
            preflight = {
                "fno_verified": True,
                "asm_gsm_clear": True,
                "band_check": True,
                "feed_health": True,
                "source_authenticated": True,
                "passed": True,
                "checked_at": self._clock().isoformat(),
                "fno_source_sha256": fno_hash,
                "surveillance_source_sha256": surveillance_hash,
                "band_source_sha256": band_hash,
                "universe_sha256": universe_hash,
                "qualification_mode": self.qualification_mode,
            }
            write_locked_preflight(self.session_dir, preflight)
            self.reference_files = (
                {"path": "sources/fno.json", "role": "FNO"},
                {"path": "sources/surveillance.json", "role": "SURVEILLANCE"},
                {"path": "sources/band_policy.json", "role": "BAND_POLICY"},
                {"path": "sources/universe.json", "role": "UNIVERSE"},
            )
            self.preflight = preflight
            self.recorder = Track2SessionRecorder(self.session_dir, preregistration)
            self.state = CoordinatorState.RECORDING
            return PreparationResult(self.state, session_date, None, eligible)
        except (OSError, TypeError, ValueError) as exc:
            return self._terminal_void(str(exc))

    def record_snapshot(self, snapshot: Mapping[str, Any]) -> bool:
        if self.state is not CoordinatorState.RECORDING or self.recorder is None:
            return False
        return self.recorder.record_snapshot(snapshot)

    def record_signal(
        self,
        *,
        signal_id: str,
        symbol: str,
        timestamp: datetime | str,
        order_spec: Mapping[str, Any],
    ) -> bool:
        if self.state is not CoordinatorState.RECORDING or self.recorder is None:
            return False
        return self.recorder.record_signal(
            signal_id=signal_id,
            symbol=symbol,
            timestamp=timestamp,
            order_spec=order_spec,
        )

    def resume(self, session_date: str) -> PreparationResult:
        """Resume only an exact, non-terminal prospectively prepared session."""
        if self.state is not CoordinatorState.NEW:
            return PreparationResult(CoordinatorState.VOID, session_date, "coordinator resume is single-use")
        self.session_date = session_date
        self.session_dir = self.sessions_root / session_date
        try:
            parsed_session_day = date.fromisoformat(session_date)
            if parsed_session_day.isoformat() != session_date:
                raise ValueError("session_date must use canonical YYYY-MM-DD form")
            self._acquire_writer_lock()
            attempts, attempt_errors = verify_attempt_registry(self.sessions_root)
            if attempt_errors:
                raise ValueError("attempt registry is invalid: " + "; ".join(attempt_errors))
            matching = [item for item in attempts if item.get("session_date") == session_date]
            if not matching or matching[-1].get("attempt_outcome") != "PENDING":
                raise ValueError("session has no resumable PENDING attempt")
            ledger_mode = matching[0].get("details", {}).get("qualification_mode")
            if ledger_mode != self.qualification_mode:
                raise ValueError("runtime qualification_mode differs from frozen attempt ledger")
            self._attempt_registered = True
            preregistration, prereg_errors = verify_locked_preregistration(self.session_dir)
            if preregistration is None:
                raise ValueError("locked preregistration is invalid: " + "; ".join(prereg_errors))
            if preregistration.get("qualification_mode") != ledger_mode:
                raise ValueError("preregistration qualification_mode differs from attempt ledger")
            if preregistration.get("config_sha256") != _sha256_bytes(_canonical_json(self.config)):
                raise ValueError("runtime config differs from frozen preregistration")
            preflight, preflight_errors = verify_locked_preflight(self.session_dir)
            if preflight is None:
                raise ValueError("locked preflight is invalid: " + "; ".join(preflight_errors))
            reference_files = (
                {"path": "sources/fno.json", "role": "FNO"},
                {"path": "sources/surveillance.json", "role": "SURVEILLANCE"},
                {"path": "sources/band_policy.json", "role": "BAND_POLICY"},
                {"path": "sources/universe.json", "role": "UNIVERSE"},
            )
            role_digests: dict[str, str] = {}
            for item in reference_files:
                path = self.session_dir / item["path"]
                if path.is_symlink() or not path.is_file():
                    raise ValueError(f"resumption reference is missing or unsafe: {item['role']}")
                role_digests[item["role"]] = hashlib.sha256(path.read_bytes()).hexdigest()
            expected_role_digests = {
                "FNO": preflight.get("fno_source_sha256"),
                "SURVEILLANCE": preflight.get("surveillance_source_sha256"),
                "BAND_POLICY": preflight.get("band_source_sha256"),
                "UNIVERSE": preflight.get("universe_sha256"),
            }
            if role_digests != expected_role_digests:
                raise ValueError("resumption reference digest differs from locked preflight")
            universe = _load_json_object(self.session_dir / "sources" / "universe.json", "universe")
            if preregistration.get("universe_sha256") != role_digests["UNIVERSE"]:
                raise ValueError("resumption universe differs from locked preregistration")
            source_bindings = universe.get("source_bindings")
            if source_bindings != {
                "fno_sha256": role_digests["FNO"],
                "surveillance_sha256": role_digests["SURVEILLANCE"],
                "band_policy_sha256": role_digests["BAND_POLICY"],
            }:
                raise ValueError("resumption universe source bindings are invalid")
            symbols = universe.get("symbols")
            if not isinstance(symbols, list) or len(set(symbols)) < 4:
                raise ValueError("resumption universe is malformed or too small")
            normalized_candidates = []
            for symbol in self.candidates:
                if not isinstance(symbol, str):
                    raise ValueError("runtime candidate symbols must be strings")
                cleaned = symbol.strip().upper()
                if not SYMBOL_REGEX.fullmatch(cleaned):
                    raise ValueError(f"invalid runtime candidate symbol: {symbol}")
                normalized_candidates.append(cleaned)
            fno = _load_json_object(self.session_dir / "sources" / "fno.json", "fno source")
            surveillance = _load_json_object(
                self.session_dir / "sources" / "surveillance.json", "surveillance source",
            )
            blocked = set(surveillance.get("asm_short_term", ()))
            blocked.update(surveillance.get("asm_long_term", ()))
            blocked.update(surveillance.get("gsm", ()))
            frozen_fno = set(fno.get("symbols", ()))
            runtime_eligible = sorted(
                symbol for symbol in normalized_candidates
                if symbol in frozen_fno and symbol not in blocked
            )
            if runtime_eligible != sorted(str(symbol) for symbol in symbols):
                raise ValueError("runtime candidates differ from frozen eligible universe")
            band_source = _load_json_object(
                self.session_dir / "sources" / "band_policy.json", "band policy source",
            )
            if self.band_policy_path.is_symlink() or not self.band_policy_path.is_file():
                raise ValueError("runtime band policy is missing or unsafe")
            runtime_policy_hash = hashlib.sha256(self.band_policy_path.read_bytes()).hexdigest()
            if runtime_policy_hash != band_source.get("source_policy_sha256"):
                raise ValueError("runtime band policy differs from frozen policy")
            self.reference_files = reference_files
            self.preflight = preflight
            self.recorder = Track2SessionRecorder(self.session_dir, preregistration)
            self.state = CoordinatorState.RECORDING
            return PreparationResult(
                self.state, session_date, None, tuple(sorted(str(symbol) for symbol in symbols)),
            )
        except (OSError, TypeError, ValueError) as exc:
            return self._terminal_void(str(exc))

    def finalize(self) -> SessionVerdict:
        if self.state is not CoordinatorState.RECORDING or self.recorder is None or self.preflight is None:
            raise RuntimeError("coordinator is not recording")
        current_time = self._clock()
        if current_time.date().isoformat() != self.session_date:
            raise RuntimeError("session must be finalized on its own session date")
        if current_time.time() < time(15, 30):
            raise RuntimeError("session cannot be finalized before 15:30 IST")
        try:
            verdict = self.recorder.close_and_evaluate(
                preflight=self.preflight,
                reference_files=self.reference_files,
            )
        except (OSError, TypeError, ValueError) as exc:
            reason = f"session finalization failed: {exc}"
            self._terminal_void(reason)
            return SessionVerdict(
                "TRACK2", self.session_date or "UNKNOWN", SessionStatus.VOID,
                (reason,), 0, 0, 0, 0,
            )
        self.state = (
            CoordinatorState.FINALIZED
            if verdict.status is SessionStatus.COUNTED
            else CoordinatorState.REHEARSAL
            if verdict.status is SessionStatus.REHEARSAL
            else CoordinatorState.VOID
        )
        self._release_writer_lock()
        return verdict

    def suspend_for_restart(self) -> None:
        """Close local handles while preserving the PENDING attempt for resume."""
        if self.state is not CoordinatorState.RECORDING or self.recorder is None:
            raise RuntimeError("coordinator is not recording")
        self.recorder.close_writer()
        self._release_writer_lock()
        self.state = CoordinatorState.NEW
