"""
antigravity/daemons/track2_surveillance_bridge.py
=================================================
Cryptographic, fail-closed bridge that synchronizes verified official NSE
ASM/GSM/F&O surveillance snapshots from the operational ingestor directory
(shared/track2_liquid/surveillance/) into the research desk's canonical layout
(history/raw/nse/surveillance/).

Fail-Closed Rules:
1. Verifies that the official snapshot exists and has parse_status == "SUCCESS".
2. Verifies effective_session_date matches the requested session date.
3. Cryptographically hashes the raw source files on disk and asserts exact equality
   with the SHA-256 digests recorded in the official snapshot manifest.
4. Performs atomic writes to destination so incomplete writes are impossible.
5. Emits an immutable bridge receipt with cryptographic hashes of all synced files.

Operational Status:
Kept OFF by default. The research reader (`research.framework.market.MarketFiles`) reads
`shared/track2_liquid/surveillance/` directly via `research.data.paths.surveillance_dir()`.
When executed, this bridge provides cryptographic replication into `history/raw/nse/surveillance/`
with verified bit-for-bit parity against `MarketFiles.surveillance()`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

IST = timezone(timedelta(hours=5, minutes=30))


def atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target.with_suffix(f"{target.suffix}.tmp_{os.getpid()}")
    try:
        temp_target.write_bytes(data)
        os.replace(temp_target, target)
    except Exception:
        if temp_target.exists():
            try:
                temp_target.unlink()
            except OSError:
                pass
        raise


def sync_surveillance_to_research(
    session_date: str,
    operational_dir: Optional[Path | str] = None,
    target_history_dir: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """
    Synchronizes and cryptographically validates surveillance data for session_date.
    Fails closed on any discrepancy, missing file, or hash mismatch.
    """
    try:
        session_day = date.fromisoformat(session_date)
    except ValueError:
        return {"ok": False, "reason": f"INVALID_DATE_FORMAT: {session_date}"}

    d_iso = session_day.isoformat()

    if operational_dir is None:
        operational_dir = Path("shared/track2_liquid/surveillance")
    operational_dir = Path(operational_dir).resolve()

    if target_history_dir is None:
        target_history_dir = Path("history")
    target_history_dir = Path(target_history_dir).resolve()
    target_surv_dir = target_history_dir / "raw" / "nse" / "surveillance"

    snapshot_filename = f"nse_surveillance_snapshot_{d_iso}.json"
    snapshot_path = operational_dir / snapshot_filename
    if not snapshot_path.exists():
        return {
            "ok": False,
            "reason": f"OFFICIAL_SNAPSHOT_NOT_FOUND: {snapshot_path}",
        }

    try:
        snap = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "reason": f"SNAPSHOT_JSON_CORRUPT: {e}"}

    if not isinstance(snap, dict):
        return {"ok": False, "reason": "SNAPSHOT_NOT_DICT"}
    if snap.get("parse_status") != "SUCCESS":
        return {"ok": False, "reason": f"SNAPSHOT_PARSE_STATUS_NOT_SUCCESS: {snap.get('parse_status')}"}
    if snap.get("effective_session_date") != d_iso:
        return {"ok": False, "reason": f"SESSION_DATE_MISMATCH: {snap.get('effective_session_date')} != {d_iso}"}

    sources = snap.get("sources")
    if not isinstance(sources, dict) or "asm" not in sources or "gsm" not in sources:
        return {"ok": False, "reason": "SNAPSHOT_MISSING_ASM_OR_GSM_SOURCES"}

    verified_bytes: Dict[str, bytes] = {}
    verified_hashes: Dict[str, str] = {}
    raw_rel_paths: Dict[str, str] = {}

    for kind in ("asm", "gsm"):
        meta = sources[kind]
        if not isinstance(meta, dict):
            return {"ok": False, "reason": f"SOURCE_META_NOT_DICT: {kind}"}

        # Enforce HTTP 200 status
        http_status = meta.get("http_status")
        if http_status != 200:
            return {"ok": False, "reason": f"INVALID_HTTP_STATUS_{kind.upper()}: {http_status}"}

        # Enforce official NSE source URL allowlist matching research framework
        source_url = meta.get("source_url")
        expected_endpoint = {"asm": "https://www.nseindia.com/api/reportASM", "gsm": "https://www.nseindia.com/api/reportGSM"}.get(kind)
        if source_url != expected_endpoint:
            return {"ok": False, "reason": f"INVALID_SOURCE_ENDPOINT_{kind.upper()}: {source_url}"}

        # Enforce valid JSON content-type
        ctype = meta.get("content_type")
        if not isinstance(ctype, str) or ctype.split(";", 1)[0].strip().lower() not in {"application/json", "text/json"}:
            return {"ok": False, "reason": f"INVALID_CONTENT_TYPE_{kind.upper()}: {ctype}"}

        raw_rel = meta.get("raw_relative_path")
        expected_sha = meta.get("sha256")
        if not raw_rel or not isinstance(raw_rel, str) or not expected_sha:
            return {"ok": False, "reason": f"SOURCE_META_MISSING_PATH_OR_SHA: {kind}"}

        if Path(raw_rel).is_absolute() or ".." in raw_rel:
            return {"ok": False, "reason": f"PATH_ESCAPE_DETECTED: {raw_rel}"}

        raw_file_path = (operational_dir / raw_rel).resolve()
        if raw_file_path.parent != operational_dir or not raw_file_path.is_file():
            return {"ok": False, "reason": f"RAW_FILE_ESCAPE_OR_NOT_FOUND: {raw_rel}"}

        target_raw = (target_surv_dir / raw_rel).resolve()
        if target_raw.parent != target_surv_dir:
            return {"ok": False, "reason": f"DESTINATION_PATH_ESCAPE: {raw_rel}"}

        b = raw_file_path.read_bytes()
        actual_sha = hashlib.sha256(b).hexdigest().lower()
        if actual_sha != expected_sha.lower():
            return {
                "ok": False,
                "reason": f"HASH_MISMATCH_{kind.upper()}: actual={actual_sha}, expected={expected_sha}",
            }

        verified_bytes[kind] = b
        verified_hashes[kind] = actual_sha
        raw_rel_paths[kind] = raw_rel

    # Destination immutability & idempotent resume:
    # Existing files with matching sha256 are accepted without error (resuming after interruption).
    # Existing files with different content trigger immediate conflict error.
    target_asm = target_surv_dir / f"{d_iso}_asm.json"
    target_gsm = target_surv_dir / f"{d_iso}_gsm.json"
    target_snap = target_surv_dir / snapshot_filename
    receipt_path = target_surv_dir / f"bridge_receipt_{d_iso}.json"
    target_raw_asm = target_surv_dir / raw_rel_paths["asm"]
    target_raw_gsm = target_surv_dir / raw_rel_paths["gsm"]

    file_payloads = {
        target_asm: verified_bytes["asm"],
        target_gsm: verified_bytes["gsm"],
        target_raw_asm: verified_bytes["asm"],
        target_raw_gsm: verified_bytes["gsm"],
        target_snap: snapshot_path.read_bytes(),
    }

    for dest_file, expected_bytes in file_payloads.items():
        if dest_file.exists():
            existing_sha = hashlib.sha256(dest_file.read_bytes()).hexdigest().lower()
            exp_sha = hashlib.sha256(expected_bytes).hexdigest().lower()
            if existing_sha != exp_sha:
                return {
                    "ok": False,
                    "reason": f"DESTINATION_FILE_CONFLICT: {dest_file} (existing sha {existing_sha} != {exp_sha})",
                }

    # Atomically write target files in research desk canonical layout
    files_written: List[str] = []
    try:
        for dest_file, expected_bytes in file_payloads.items():
            if not dest_file.exists():
                atomic_write_bytes(dest_file, expected_bytes)
            files_written.append(str(dest_file))


        # Write cryptographic bridge receipt
        receipt = {
            "bridged_at": datetime.now(timezone.utc).astimezone(IST).isoformat(timespec="seconds"),
            "session_date": d_iso,
            "snapshot_id": snap.get("snapshot_id"),
            "source_snapshot": str(snapshot_path),
            "source_snapshot_sha256": hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
            "target_files": {
                "asm": {"path": str(target_asm), "sha256": verified_hashes["asm"]},
                "gsm": {"path": str(target_gsm), "sha256": verified_hashes["gsm"]},
                "raw_asm": {"path": str(target_raw_asm), "sha256": verified_hashes["asm"]},
                "raw_gsm": {"path": str(target_raw_gsm), "sha256": verified_hashes["gsm"]},
            },
            "status": "VERIFIED_ATOMIC",
        }
        receipt_path = target_surv_dir / f"bridge_receipt_{d_iso}.json"
        atomic_write_bytes(receipt_path, json.dumps(receipt, indent=2).encode("utf-8"))
        files_written.append(str(receipt_path))

    except Exception as e:
        return {"ok": False, "reason": f"ATOMIC_WRITE_FAILED: {e}"}

    return {
        "ok": True,
        "session_date": d_iso,
        "snapshot_id": snap.get("snapshot_id"),
        "files_written": files_written,
    }
