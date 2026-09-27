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

    for kind in ("asm", "gsm"):
        meta = sources[kind]
        if not isinstance(meta, dict):
            return {"ok": False, "reason": f"SOURCE_META_NOT_DICT: {kind}"}
        raw_rel = meta.get("raw_relative_path")
        expected_sha = meta.get("sha256")
        if not raw_rel or not expected_sha:
            return {"ok": False, "reason": f"SOURCE_META_MISSING_PATH_OR_SHA: {kind}"}

        raw_file_path = operational_dir / raw_rel
        if not raw_file_path.exists():
            return {"ok": False, "reason": f"RAW_FILE_NOT_FOUND: {raw_file_path}"}

        b = raw_file_path.read_bytes()
        actual_sha = hashlib.sha256(b).hexdigest().lower()
        if actual_sha != expected_sha.lower():
            return {
                "ok": False,
                "reason": f"HASH_MISMATCH_{kind.upper()}: actual={actual_sha}, expected={expected_sha}",
            }

        verified_bytes[kind] = b
        verified_hashes[kind] = actual_sha

    # Atomically write target files in research desk canonical layout
    files_written: List[str] = []
    try:
        # Write <date>_asm.json
        target_asm = target_surv_dir / f"{d_iso}_asm.json"
        atomic_write_bytes(target_asm, verified_bytes["asm"])
        files_written.append(str(target_asm))

        # Write <date>_gsm.json
        target_gsm = target_surv_dir / f"{d_iso}_gsm.json"
        atomic_write_bytes(target_gsm, verified_bytes["gsm"])
        files_written.append(str(target_gsm))

        # Write copy of snapshot manifest
        target_snap = target_surv_dir / snapshot_filename
        atomic_write_bytes(target_snap, snapshot_path.read_bytes())
        files_written.append(str(target_snap))

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
