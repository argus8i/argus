"""Fail-closed Track 2 Phase 1B1 Session Recorder & Coordinator.

Records prospective paper trading research evidence for Track 2.
Strictly paper-only: contains NO broker order routes, credential reads,
POST/PUT/DELETE network endpoints, or order execution capabilities.
Complies with AGENTS.md Rule 1 (paper-only) and Rule 11 (track isolation).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from antigravity.daemons.feed_validity import check_feed
from antigravity.models.session_manifest import (
    IST,
    MARKET_OPEN,
    ORB_SIGNAL_START,
    LAST_ENTRY_TIME,
    SESSION_CLOSE,
    MAX_CAPTURE_GAP_SECONDS,
    EvidenceClass,
    SessionStatus,
    SessionVerdict,
    evaluate_session,
    verify_locked_preregistration,
    write_locked_preregistration,
    verify_locked_preflight,
    verify_stream_checkpoints,
    write_stream_checkpoint,
    verify_attempt_registry,
    write_attempt_record,
    _canonical_json,
    _is_finite_number,
    _now_ist,
    _parse_aware_timestamp,
    _sha256_bytes,
)


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _write_hashed_json(path: Path, value: Mapping[str, Any]) -> str:
    payload = _canonical_json(value)
    digest = _sha256_bytes(payload)
    _atomic_write_bytes(path, payload)
    _atomic_write_bytes(path.with_suffix(".sha256"), (digest + "\n").encode("ascii"))
    return digest


def _read_hashed_json(path: Path) -> dict[str, Any]:
    digest_path = path.with_suffix(".sha256")
    if not path.is_file() or not digest_path.is_file():
        raise ValueError(f"{path.name} or its digest is missing")
    raw = path.read_bytes()
    expected = digest_path.read_text(encoding="ascii").strip()
    if expected != _sha256_bytes(raw):
        raise ValueError(f"{path.name} digest mismatch")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


class Track2SessionRecorder:
    """Manages the lifecycle of a prospective Track 2 paper session recording.

    Appends canonical JSONL events under caller-supplied session directory
    using a single append-only writer, monotonic source timestamps, explicit
    flush/fsync, and snapshot deduplication.
    """

    def __init__(
        self,
        session_dir: str | os.PathLike[str],
        preregistration_record: Mapping[str, Any],
        stream_filename: str = "market_stream.jsonl",
        *,
        _test_clock: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        if "clock" in kwargs:
            raise TypeError("Track2SessionRecorder does not accept caller-injectable 'clock'; clock is recorder-owned in production")
        if kwargs:
            raise TypeError(f"Track2SessionRecorder.__init__() got unexpected keyword argument(s): {sorted(kwargs)}")
        self.session_dir = Path(session_dir)
        self.stream_filename = stream_filename
        self.stream_path = self.session_dir / self.stream_filename
        self._clock = _test_clock if _test_clock is not None else _now_ist

        # A fresh session is preregistered once. A restarted process may resume
        # only the exact already-verified record; it cannot replace or backfill it.
        preregistration_path = self.session_dir / "preregistration.json"
        if preregistration_path.exists():
            verified, errors = verify_locked_preregistration(self.session_dir)
            if verified is None:
                raise ValueError("cannot resume invalid preregistration: " + "; ".join(errors))
            if _canonical_json(verified) != _canonical_json(preregistration_record):
                raise ValueError("resume preregistration does not match the locked record")
            self.preregistration = verified
            self.preregistration_sha256 = _sha256_bytes(_canonical_json(verified))
        else:
            self.preregistration_sha256 = write_locked_preregistration(
                self.session_dir, preregistration_record
            )
            self.preregistration = dict(preregistration_record)
        self.session_date_str = str(self.preregistration["session_date"])
        self.qualification_mode = str(self.preregistration["qualification_mode"])
        self.session_day = date.fromisoformat(self.session_date_str)
        self.order_rules = self.preregistration["order_rules"]
        self.expected_rules_hash = _sha256_bytes(_canonical_json(self.order_rules))
        self.allowed_gap_seconds = float(self.preregistration.get("gap_seconds", MAX_CAPTURE_GAP_SECONDS))

        # Check for universe artifact under sources/ or session_dir
        self.frozen_symbols: Optional[set[str]] = None
        universe_path = self.session_dir / "sources" / "universe.json"
        if not universe_path.is_file():
            universe_path = self.session_dir / "universe.json"
        if not universe_path.is_file():
            raise ValueError("frozen universe artifact is required before recorder construction")
        raw_u = universe_path.read_bytes()
        if _sha256_bytes(raw_u) != self.preregistration.get("universe_sha256"):
            raise ValueError("universe file hash does not match preregistration universe_sha256")
        try:
            u_obj = json.loads(raw_u)
            if not isinstance(u_obj, dict) or not isinstance(u_obj.get("symbols"), list):
                raise ValueError("symbols must be a list")
            self.frozen_symbols = {
                str(symbol).strip().upper() for symbol in u_obj["symbols"] if str(symbol).strip()
            }
            if len(self.frozen_symbols) < 4:
                raise ValueError("frozen universe must contain at least four distinct symbols")
        except Exception as exc:
            raise ValueError(f"malformed universe file: {exc}") from exc

        self.start_window = datetime.combine(self.session_day, ORB_SIGNAL_START, tzinfo=IST)
        self.end_window = datetime.combine(self.session_day, LAST_ENTRY_TIME, tzinfo=IST)
        self.open_time = datetime.combine(self.session_day, MARKET_OPEN, tzinfo=IST)
        self.close_time = datetime.combine(self.session_day, SESSION_CLOSE, tzinfo=IST)

        # Reconstruct state before opening append mode. A malformed prior stream
        # makes recovery fail closed rather than guessing where capture stopped.
        self._last_snapshot_hash: Optional[str] = None
        self._last_timestamp: Optional[datetime] = None
        self._seen_signals: set[str] = set()
        self._seen_signal_ids: set[str] = set()
        self._is_finalized = (self.session_dir / "verdict.json").exists()
        self._restore_stream_state()

        checkpoints, checkpoint_errors = verify_stream_checkpoints(
            self.session_dir.parent, self.session_date_str
        )
        if checkpoint_errors:
            raise ValueError("cannot resume invalid checkpoint chain: " + "; ".join(checkpoint_errors))
        self._checkpoint_sequence = len(checkpoints)
        self._last_checkpoint_at = (
            _parse_aware_timestamp(checkpoints[-1].get("recorded_at")) if checkpoints else None
        )

        # Single append-only file handle for canonical market stream events.
        self._writer = open(self.stream_path, "a", encoding="utf-8")

    def _restore_stream_state(self) -> None:
        if not self.stream_path.exists():
            return
        previous_timestamp: Optional[datetime] = None
        with open(self.stream_path, encoding="utf-8", errors="strict") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"cannot resume malformed stream line {line_number}") from exc
                if not isinstance(event, dict):
                    raise ValueError(f"cannot resume non-object stream line {line_number}")
                timestamp = _parse_aware_timestamp(event.get("timestamp"))
                if timestamp is None or (previous_timestamp is not None and timestamp < previous_timestamp):
                    raise ValueError(f"cannot resume invalid timestamp at stream line {line_number}")
                previous_timestamp = timestamp
                snapshot_hash = event.get("snapshot_sha256")
                if snapshot_hash is not None:
                    if not isinstance(snapshot_hash, str) or len(snapshot_hash) != 64:
                        raise ValueError(f"cannot resume invalid snapshot hash at stream line {line_number}")
                    self._last_snapshot_hash = snapshot_hash
                if event.get("event_type") == "SIGNAL":
                    order_hash = event.get("order_hash")
                    signal_id = event.get("signal_id")
                    if not isinstance(order_hash, str) or not isinstance(signal_id, str):
                        raise ValueError(f"cannot resume malformed signal at stream line {line_number}")
                    if order_hash in self._seen_signals or signal_id in self._seen_signal_ids:
                        raise ValueError(f"cannot resume duplicate signal at stream line {line_number}")
                    self._seen_signals.add(order_hash)
                    self._seen_signal_ids.add(signal_id)
        self._last_timestamp = previous_timestamp

    def __enter__(self) -> Track2SessionRecorder:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close_writer()

    def close_writer(self) -> None:
        """Close the underlying market stream file writer safely."""
        if self._writer and not self._writer.closed:
            self._writer.flush()
            os.fsync(self._writer.fileno())
            self._writer.close()

    def _write_event(self, event: Mapping[str, Any]) -> None:
        if self._is_finalized:
            raise RuntimeError("Cannot append to a finalized Track 2 session")
        if self._writer.closed:
            raise RuntimeError("Cannot write to closed Track2SessionRecorder")
        line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        self._writer.write(line)
        self._writer.flush()
        os.fsync(self._writer.fileno())

    def _write_checkpoint(self, recorded_at: datetime, *, force: bool = False) -> None:
        """Bind the current durable stream bytes into the external hash chain."""
        if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
            raise ValueError("checkpoint clock must be timezone-aware")
        recorded_at = recorded_at.astimezone(IST)
        if (not force and self._last_checkpoint_at is not None
                and (recorded_at - self._last_checkpoint_at).total_seconds() < 300):
            return
        if self._writer and not self._writer.closed:
            self._writer.flush()
            os.fsync(self._writer.fileno())
        if not self.stream_path.is_file():
            return
        raw = self.stream_path.read_bytes()
        rows = sum(1 for line in raw.splitlines() if line.strip())
        if rows <= 0:
            return
        self._checkpoint_sequence += 1
        write_stream_checkpoint(
            self.session_dir.parent,
            self.session_date_str,
            sequence=self._checkpoint_sequence,
            stream_sha256=_sha256_bytes(raw),
            row_count=rows,
            byte_count=len(raw),
            recorded_at=recorded_at,
        )
        self._last_checkpoint_at = recorded_at

    def record_snapshot(
        self,
        snapshot: Mapping[str, Any],
        *,
        max_age_sec: float = 120.0,
    ) -> bool:
        """Processes and appends a local Track 2 browser snapshot.

        1. Uses recorder-owned clock for coordinator recorded_at and freshness checks.
        2. Passes snapshot through feed_validity.check_feed.
        3. Deduplicates snapshots based on content hash.
        4. Enforces monotonic source timestamps.
        5. Rejects any snapshot containing symbols outside the frozen universe.
        6. Labels ordinary watchlist observations as E1_BAR_POSSIBLE.
        7. Labels selected-stock displayed depth as E2_MARKETABLE_DEPTH only
           when active_symbol, both sides, finite prices, and nonnegative quantities validate.
        8. Strictly refuses any E3 claims and never creates trade IDs.

        Returns True if recorded, False if rejected or duplicate.
        """
        if self._is_finalized:
            return False
        current_wall = self._clock()
        if current_wall is None or current_wall.tzinfo is None:
            return False
        wall_clock_ist = current_wall.astimezone(IST)
        check_now = wall_clock_ist.replace(tzinfo=None)

        usable, reason = check_feed(snapshot, now=check_now, max_age_sec=max_age_sec)
        if not usable:
            return False

        # Snapshot deduplication
        try:
            canonical_bytes = _canonical_json(snapshot)
        except (TypeError, ValueError):
            return False
        snap_hash = _sha256_bytes(canonical_bytes)
        if snap_hash == self._last_snapshot_hash:
            return False

        # Parse source timestamp
        raw_ts = snapshot.get("timestamp")
        ts = _parse_aware_timestamp(raw_ts)
        if ts is None:
            # Try parsing local_write_time if timestamp is missing or naive
            raw_local = snapshot.get("local_write_time")
            if raw_local and isinstance(raw_local, str):
                try:
                    dt = datetime.strptime(raw_local.strip(), "%Y-%m-%d %H:%M:%S")
                    ts = dt.replace(tzinfo=IST)
                except (ValueError, TypeError):
                    pass

        if ts is None:
            return False

        # Record real gaps and out-of-order input rather than smoothing/backfilling
        if self._last_timestamp is not None and ts < self._last_timestamp:
            return False

        if ts.date() != self.session_day or not self.open_time <= ts <= self.close_time:
            return False
        if abs((wall_clock_ist - ts).total_seconds()) > max_age_sec:
            return False

        # Frozen universe validation: reject snapshot if any watchlist item or active_stock is out-of-universe
        watchlist = snapshot.get("watchlist")
        if self.frozen_symbols is not None:
            if isinstance(watchlist, list):
                for item in watchlist:
                    if isinstance(item, dict):
                        s = str(item.get("symbol", "")).strip().upper()
                        if s and s not in self.frozen_symbols:
                            return False
            active_candidate = snapshot.get("active_stock")
            if active_candidate and isinstance(active_candidate, str) and active_candidate.strip().upper() not in self.frozen_symbols:
                return False

        events: list[dict[str, Any]] = []

        # 1. Watchlist items -> labeled no higher than E1_BAR_POSSIBLE
        if isinstance(watchlist, list):
            for item in watchlist:
                if not isinstance(item, dict):
                    continue
                sym = item.get("symbol")
                ltp = item.get("ltp")
                if not sym or not isinstance(sym, str) or not sym.strip():
                    continue
                try:
                    ltp_float = float(ltp)
                    if not math.isfinite(ltp_float) or ltp_float <= 0:
                        continue
                except (ValueError, TypeError):
                    continue

                event = {
                    "timestamp": ts.isoformat(),
                    "recorded_at": wall_clock_ist.isoformat(),
                    "evidence_class": EvidenceClass.E1_BAR_POSSIBLE.value,
                    "event_type": "QUOTE",
                    "symbol": sym.strip(),
                    "ltp": ltp_float,
                    "change_pct": item.get("change_pct"),
                    "change_abs": item.get("change_abs"),
                    "source": "WATCHLIST",
                    "snapshot_sha256": snap_hash,
                }
                events.append(event)

        # 2. Selected-stock displayed depth -> labeled E2_MARKETABLE_DEPTH if valid
        active_stock = snapshot.get("active_stock")
        depth = snapshot.get("depth")
        if active_stock and isinstance(active_stock, str) and active_stock.strip() and isinstance(depth, dict):
            bids = depth.get("bids")
            offers = depth.get("offers")
            if isinstance(bids, list) and isinstance(offers, list) and bids and offers:
                bids_valid = True
                offers_valid = True
                parsed_bids = []
                parsed_offers = []

                for b in bids:
                    if not isinstance(b, dict):
                        bids_valid = False
                        break
                    p = b.get("price")
                    q = b.get("quantity")
                    orders = b.get("orders")
                    if (not _is_finite_number(p, minimum=0.001)
                            or isinstance(q, bool) or not isinstance(q, int) or q <= 0
                            or isinstance(orders, bool) or not isinstance(orders, int) or orders < 0):
                        bids_valid = False
                        break
                    parsed_bids.append({"price": float(p), "quantity": q, "orders": orders})

                for o in offers:
                    if not isinstance(o, dict):
                        offers_valid = False
                        break
                    p = o.get("price")
                    q = o.get("quantity")
                    orders = o.get("orders")
                    if (not _is_finite_number(p, minimum=0.001)
                            or isinstance(q, bool) or not isinstance(q, int) or q <= 0
                            or isinstance(orders, bool) or not isinstance(orders, int) or orders < 0):
                        offers_valid = False
                        break
                    parsed_offers.append({"price": float(p), "quantity": q, "orders": orders})

                if bids_valid and offers_valid and parsed_bids and parsed_offers:
                    watchlist_symbols = {
                        str(item.get("symbol", "")).strip()
                        for item in watchlist
                        if isinstance(item, dict)
                    } if isinstance(watchlist, list) else set()
                    bids_sorted = all(
                        left["price"] >= right["price"]
                        for left, right in zip(parsed_bids, parsed_bids[1:])
                    )
                    offers_sorted = all(
                        left["price"] <= right["price"]
                        for left, right in zip(parsed_offers, parsed_offers[1:])
                    )
                    best_bid = parsed_bids[0]["price"]
                    best_ask = parsed_offers[0]["price"]
                    bid_qty = parsed_bids[0]["quantity"]
                    ask_qty = parsed_offers[0]["quantity"]

                    # Best bid and ask must be positive and non-crossed
                    if (active_stock.strip() in watchlist_symbols
                            and bids_sorted and offers_sorted and best_bid < best_ask):
                        depth_event = {
                            "timestamp": ts.isoformat(),
                            "recorded_at": wall_clock_ist.isoformat(),
                            "evidence_class": EvidenceClass.E2_MARKETABLE_DEPTH.value,
                            "event_type": "QUOTE",
                            "symbol": active_stock.strip(),
                            "best_bid": best_bid,
                            "best_ask": best_ask,
                            "bid_qty": bid_qty,
                            "ask_qty": ask_qty,
                            "depth": {
                                "bids": parsed_bids,
                                "offers": parsed_offers,
                            },
                            "source": "ACTIVE_DEPTH",
                            "snapshot_sha256": snap_hash,
                        }
                        events.append(depth_event)

        if not events:
            return False
        for event in events:
            self._write_event(event)
        self._last_snapshot_hash = snap_hash
        self._last_timestamp = ts
        self._write_checkpoint(wall_clock_ist)
        return True

    def record_signal(
        self,
        *,
        signal_id: str,
        symbol: str,
        timestamp: datetime | str,
        order_spec: Mapping[str, Any],
    ) -> bool:
        """Appends a canonical SIGNAL event bound to order_rules.

        Must be within 09:30–15:15 IST and bound to the frozen order_rules hash.
        Refuses any claim of E3 and refuses duplicates.
        """
        if self._is_finalized:
            return False
        if isinstance(timestamp, str):
            ts = _parse_aware_timestamp(timestamp)
        elif isinstance(timestamp, datetime) and timestamp.tzinfo is not None:
            ts = timestamp.astimezone(IST)
        else:
            ts = None
        if ts is None:
            return False

        # Monotonicity check
        if self._last_timestamp is not None and ts < self._last_timestamp:
            return False

        # Valid 09:30–15:15 IST time window
        if not (self.start_window <= ts <= self.end_window):
            return False

        if not isinstance(signal_id, str) or not signal_id.strip():
            return False
        normalized_signal_id = signal_id.strip()
        if normalized_signal_id in self._seen_signal_ids:
            return False
        if not isinstance(symbol, str) or not symbol.strip():
            return False

        # Reject signals whose symbol is outside the frozen universe
        if self.frozen_symbols is not None and symbol.strip().upper() not in self.frozen_symbols:
            return False

        # Bound to frozen order_rules hash
        if not isinstance(order_spec, dict):
            return False
        if order_spec.get("strategy_rules_sha256") != self.expected_rules_hash:
            return False
        if order_spec.get("symbol") != symbol.strip():
            return False

        try:
            order_hash = _sha256_bytes(_canonical_json(order_spec))
        except (TypeError, ValueError):
            return False
        if order_hash in self._seen_signals:
            return False

        current_clock = self._clock()
        if (not isinstance(current_clock, datetime)
                or current_clock.tzinfo is None
                or current_clock.utcoffset() is None):
            return False
        current_wall = current_clock.astimezone(IST)
        if current_wall.date() != self.session_day:
            return False
        if abs((current_wall - ts).total_seconds()) > 120.0:
            return False

        self._seen_signals.add(order_hash)
        self._seen_signal_ids.add(normalized_signal_id)
        self._last_timestamp = ts

        signal_event = {
            "timestamp": ts.isoformat(),
            "recorded_at": current_wall.isoformat(),
            "evidence_class": EvidenceClass.E1_BAR_POSSIBLE.value,
            "event_type": "SIGNAL",
            "symbol": symbol.strip(),
            "signal_id": normalized_signal_id,
            "order_spec": dict(order_spec),
            "order_hash": order_hash,
        }
        self._write_event(signal_event)
        self._write_checkpoint(current_wall)
        return True

    def build_data_manifest(
        self,
        reference_files: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        """Builds a data manifest dict from actual files on disk."""
        self.close_writer()

        manifest_files: list[dict[str, Any]] = []

        # Add caller-supplied reference records (e.g. fno, surveillance, band)
        for ref in reference_files:
            if not isinstance(ref, Mapping) or not isinstance(ref.get("path"), str):
                raise ValueError("reference file entry requires a relative path")
            rel_path = ref["path"]
            candidate_path = self.session_dir / rel_path
            if candidate_path.is_symlink():
                raise ValueError("reference file is missing or unsafe")
            abs_path = candidate_path.resolve()
            try:
                abs_path.relative_to(self.session_dir.resolve())
            except ValueError as exc:
                raise ValueError("reference file must remain inside the session directory") from exc
            if not abs_path.is_file():
                raise ValueError("reference file is missing or unsafe")
            raw = abs_path.read_bytes()
            digest = _sha256_bytes(raw)
            rows = sum(1 for line in raw.splitlines() if line.strip())
            ref_entry = {
                "path": rel_path,
                "kind": "REFERENCE",
                "sha256": digest,
                "rows": rows,
                "first_ts": None,
                "last_ts": None,
            }
            if "role" in ref:
                ref_entry["role"] = ref["role"]
            manifest_files.append(ref_entry)

        # Inspect market stream
        raw_stream = self.stream_path.read_bytes()
        stream_sha256 = _sha256_bytes(raw_stream)
        stream_lines = [line for line in raw_stream.decode("utf-8").splitlines() if line.strip()]
        rows = len(stream_lines)

        timestamps: list[datetime] = []
        classes: list[EvidenceClass] = []
        for line in stream_lines:
            ev = json.loads(line)
            ts = _parse_aware_timestamp(ev.get("timestamp"))
            if ts:
                timestamps.append(ts)
            e_cls = ev.get("evidence_class")
            if e_cls:
                try:
                    classes.append(EvidenceClass(e_cls))
                except ValueError:
                    pass

        first_ts_str = timestamps[0].isoformat() if timestamps else None
        last_ts_str = timestamps[-1].isoformat() if timestamps else None

        # Determine max gap and max evidence class
        max_gap = 0.0
        if len(timestamps) > 1:
            max_gap = max(
                (right - left).total_seconds()
                for left, right in zip(timestamps, timestamps[1:])
            )

        rank = {item: index for index, item in enumerate(EvidenceClass)}
        derived_class = max(classes, key=rank.get) if classes else EvidenceClass.E1_BAR_POSSIBLE

        manifest_files.append({
            "path": self.stream_filename,
            "kind": "MARKET_STREAM",
            "sha256": stream_sha256,
            "rows": rows,
            "first_ts": first_ts_str,
            "last_ts": last_ts_str,
        })

        manifest = {
            "files": manifest_files,
            "max_gap_seconds": max_gap,
            "evidence_class_max": derived_class.value,
            "complete": True,
        }
        return manifest

    def _settle_attempt_ledger(
        self,
        *,
        expected_outcome: str,
        close_clock: datetime,
        verdict_sha256: str,
        write_terminal: bool,
    ) -> None:
        """Validate or complete the terminal attempt transition.

        A persisted verdict and its attempt-ledger state are one logical closure.
        On recovery, a valid verdict beside a still-PENDING attempt completes the
        missing transition; corrupt or contradictory ledgers always fail closed.
        """
        attempt_records, attempt_errors = verify_attempt_registry(self.session_dir.parent)
        if attempt_errors:
            raise ValueError("attempt registry is invalid: " + "; ".join(attempt_errors))
        matching = [
            item for item in attempt_records
            if item.get("session_date") == self.session_date_str
        ]
        if not matching:
            raise ValueError("session has no registered PENDING attempt")
        current_outcome = matching[-1].get("attempt_outcome")
        if current_outcome == "PENDING":
            if write_terminal:
                write_attempt_record(
                    self.session_dir.parent,
                    self.session_date_str,
                    expected_outcome,
                    details={"verdict_sha256": verdict_sha256},
                    recorded_at=close_clock,
                )
            return
        if current_outcome != expected_outcome:
            raise ValueError(
                f"attempt ledger outcome {current_outcome} contradicts verdict outcome {expected_outcome}"
            )

    def close_and_evaluate(
        self,
        preflight: Optional[Mapping[str, Any]] = None,
        *,
        reference_files: Sequence[Mapping[str, Any]] = (),
        session_closed_at: Optional[str] = None,
    ) -> SessionVerdict:
        """Evaluates and atomically persists verdict after 15:30 IST.

        Idempotent only after the stored verdict and its supporting artifacts pass
        digest verification and reproduce the same Phase 1A evaluation.
        """
        verdict_path = self.session_dir / "verdict.json"
        verdict_digest_path = verdict_path.with_suffix(".sha256")
        preflight_path = self.session_dir / "preflight.json"
        manifest_path = self.session_dir / "data_manifest.json"
        close_clock = self._clock()
        if close_clock is None or close_clock.tzinfo is None:
            raise ValueError("recorder close clock must be timezone-aware")
        close_clock = close_clock.astimezone(IST)
        if not (verdict_path.exists() or verdict_digest_path.exists()):
            self._write_checkpoint(close_clock, force=True)
        self.close_writer()

        # Phase 1B3A Mandate Requirement 1:
        # Preflight must already be prospectively sealed.
        # If preflight.json already exists, verify it.
        # If caller supplies preflight, it must match the sealed preflight.
        if preflight_path.is_file():
            verified_preflight, preflight_errors = verify_locked_preflight(self.session_dir)
            if verified_preflight is None:
                # Invalid prospective evidence must produce a persisted VOID,
                # not abort closure before the attempt ledger is finalized.
                sealed_preflight = {}
            else:
                sealed_preflight = verified_preflight
        else:
            # Never create or backdate preflight evidence during close.
            sealed_preflight = {}

        if verdict_path.exists() or verdict_digest_path.exists():
            saved = _read_hashed_json(verdict_path)
            stored_preflight = _read_hashed_json(preflight_path) if preflight_path.exists() else sealed_preflight
            stored_manifest = _read_hashed_json(manifest_path)
            if preflight is not None and _canonical_json(stored_preflight) != _canonical_json(preflight):
                raise ValueError("supplied preflight does not match the persisted preflight")
            closed_at = saved.get("_session_closed_at")
            if not isinstance(closed_at, str):
                raise ValueError("persisted verdict lacks session closure provenance")
            reproduced = evaluate_session(
                session_dir=self.session_dir,
                preflight=stored_preflight,
                data_manifest=stored_manifest,
                session_closed_at=closed_at,
                fill_evidence=(),
            )
            if reproduced.to_dict() != {
                key: saved.get(key) for key in reproduced.to_dict()
            }:
                raise ValueError("persisted verdict does not reproduce from stored evidence")
            if saved.get("_preflight_sha256") != _sha256_bytes(_canonical_json(stored_preflight)):
                raise ValueError("persisted verdict preflight binding mismatch")
            if saved.get("_manifest_sha256") != _sha256_bytes(_canonical_json(stored_manifest)):
                raise ValueError("persisted verdict manifest binding mismatch")
            if saved.get("_fill_evidence_sha256") != _sha256_bytes(_canonical_json([])):
                raise ValueError("persisted verdict fill-evidence binding mismatch")
            saved_digest = _sha256_bytes(_canonical_json(saved))
            self._settle_attempt_ledger(
                expected_outcome=(
                    "FINALIZED" if reproduced.status is SessionStatus.COUNTED
                    else "REHEARSAL" if reproduced.status is SessionStatus.REHEARSAL
                    else "VOID"
                ),
                close_clock=close_clock,
                verdict_sha256=saved_digest,
                write_terminal=True,
            )
            self._is_finalized = True
            return reproduced

        # Build data manifest from disk
        manifest = self.build_data_manifest(reference_files=reference_files)

        def persist_or_match(path: Path, value: Mapping[str, Any]) -> str:
            if path.exists() or path.with_suffix(".sha256").exists():
                existing = _read_hashed_json(path)
                if _canonical_json(existing) != _canonical_json(value):
                    raise ValueError(f"{path.name} conflicts with recovered session evidence")
                return _sha256_bytes(_canonical_json(existing))
            return _write_hashed_json(path, value)

        preflight_digest = (
            preflight_path.with_suffix(".sha256").read_text(encoding="ascii").strip()
            if preflight_path.is_file() and preflight_path.with_suffix(".sha256").is_file()
            else ""
        )
        manifest_digest = persist_or_match(manifest_path, manifest)

        # Session closed at timestamp
        # Closure provenance is recorder-owned. A caller cannot inject a replay clock.
        session_closed_at = close_clock.isoformat()

        # Phase 1B1: Evaluates research evidence. Never passes fabricated E3 fill evidence.
        verdict = evaluate_session(
            session_dir=self.session_dir,
            preflight=sealed_preflight,
            data_manifest=manifest,
            session_closed_at=session_closed_at,
            fill_evidence=(),
        )

        persisted_verdict = {
            **verdict.to_dict(),
            "_session_closed_at": session_closed_at,
            "_preflight_sha256": preflight_digest,
            "_manifest_sha256": manifest_digest,
            # Phase 1 records no E3 fill artifacts. Binding the canonical empty
            # set prevents a later file from being added and treated as original.
            "_fill_evidence_sha256": _sha256_bytes(_canonical_json([])),
        }
        expected_outcome = (
            "FINALIZED" if verdict.status is SessionStatus.COUNTED
            else "REHEARSAL" if verdict.status is SessionStatus.REHEARSAL
            else "VOID"
        )
        persisted_verdict_digest = _sha256_bytes(_canonical_json(persisted_verdict))
        # Validate the ledger before committing the verdict so known corruption
        # cannot create a new split-brain closure.
        self._settle_attempt_ledger(
            expected_outcome=expected_outcome,
            close_clock=close_clock,
            verdict_sha256=persisted_verdict_digest,
            write_terminal=False,
        )
        _write_hashed_json(verdict_path, persisted_verdict)
        self._settle_attempt_ledger(
            expected_outcome=expected_outcome,
            close_clock=close_clock,
            verdict_sha256=persisted_verdict_digest,
            write_terminal=True,
        )
        self._is_finalized = True

        return verdict
