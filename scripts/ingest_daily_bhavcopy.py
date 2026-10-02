"""
scripts/ingest_daily_bhavcopy.py
================================
Official NSE Capital Market (CM) Bhavcopy Ingestion and Provenance Sealing.
Ingests raw official EOD Bhavcopy prints, normalizes columns, computes cryptographic
SHA-256 hash, and generates data/bhavcopy/manifest_{session_date}.json.

Fail-Closed Invariant:
Never fabricates synthetic or unverified Bhavcopy data. Missing official inputs
raise FileNotFoundError per AGENTS.md Rule 8 v2.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone, timedelta
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple
import zipfile

IST = timezone(timedelta(hours=5, minutes=30))
ROOT_DIR = Path(__file__).resolve().parent.parent


def compute_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def find_bhavcopy_source(session_date: str, custom_source: Optional[str] = None) -> Optional[Path]:
    if custom_source:
        p = Path(custom_source).resolve()
        if p.exists() and p.is_file():
            return p
        return None

    dt_str = session_date.replace("-", "")
    candidates = [
        ROOT_DIR / "data" / "bhavcopy" / "raw" / f"bhavcopy_{session_date}.csv",
        ROOT_DIR / "data" / "bhavcopy" / "raw" / f"bhavcopy_{dt_str}.csv",
        ROOT_DIR / "data" / "bhavcopy" / f"bhavcopy_{session_date}.csv",
        ROOT_DIR / "history" / "raw" / "nse_archive" / "cm_bhavcopy" / session_date[:4] / f"cm{dt_str}bhav.csv.gz",
        ROOT_DIR / "history" / "raw" / "nse_archive" / "cm_bhavcopy" / session_date[:4] / f"cm{dt_str}bhav.csv",
        ROOT_DIR / "shared" / "track2_liquid" / "bhavcopy" / f"bhavcopy_{session_date}.csv",
    ]
    for c in candidates:
        if c.exists() and c.is_file():
            return c
    return None


def extract_and_validate_bhavcopy(source_path: Path, session_date: str) -> Tuple[List[Dict[str, str]], List[str]]:
    """Reads raw CSV or ZIP, validates official OHLCV schema, dates, and extracts rows."""
    content: bytes
    if source_path.suffix.lower() == ".gz":
        content = gzip.decompress(source_path.read_bytes())
    elif source_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(source_path) as zf:
            csv_members = [m for m in zf.namelist() if m.lower().endswith(".csv")]
            if not csv_members:
                raise ValueError(f"No CSV member found in zip archive {source_path}")
            content = zf.read(csv_members[0])
    else:
        content = source_path.read_bytes()

    text = content.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError(f"Bhavcopy source {source_path} has empty headers")

    # Required column groups
    field_map = {f.strip(): f for f in reader.fieldnames}
    
    def find_field(candidates: List[str]) -> Optional[str]:
        for c in candidates:
            if c in field_map:
                return field_map[c]
            for f in field_map:
                if f.upper() == c.upper():
                    return field_map[f]
        return None

    sym_col = find_field(["TckrSymb", "SYMBOL", "TkrSymb"])
    srs_col = find_field(["SctySrs", "SERIES"])
    open_col = find_field(["OpnPric", "OPEN", "OPEN_PRICE"])
    high_col = find_field(["HghPric", "HIGH", "HIGH_PRICE"])
    low_col = find_field(["LwPric", "LOW", "LOW_PRICE"])
    close_col = find_field(["ClsPric", "CLOSE", "CLOSE_PRICE"])
    vol_col = find_field(["TtlTradgVol", "TtlTradQty", "TOTTRDQTY", "VOLUME"])
    date_col = find_field(["TradDt", "BizDt", "TIMESTAMP", "DATE"])

    required_missing = []
    if not sym_col: required_missing.append("SYMBOL")
    if not srs_col: required_missing.append("SERIES")
    if not open_col: required_missing.append("OPEN")
    if not high_col: required_missing.append("HIGH")
    if not low_col: required_missing.append("LOW")
    if not close_col: required_missing.append("CLOSE")
    if not vol_col: required_missing.append("VOLUME")

    if required_missing:
        raise KeyError(f"Bhavcopy source {source_path} missing required column groups: {required_missing}")

    # Possible representations of session_date (YYYY-MM-DD)
    date_targets = {session_date, session_date.replace("-", "")}
    try:
        dt_obj = datetime.strptime(session_date, "%Y-%m-%d")
        date_targets.add(dt_obj.strftime("%d-%b-%Y").upper())
        date_targets.add(dt_obj.strftime("%d-%B-%Y").upper())
        date_targets.add(dt_obj.strftime("%d-%m-%Y"))
    except ValueError:
        pass

    rows = []
    date_matched = False
    for r in reader:
        cleaned = {k.strip(): (v.strip() if v else "") for k, v in r.items() if k}
        sym = cleaned.get(sym_col, "").strip().upper()
        if not sym or sym in {"SYMBOL", "TCKRSYMB"}:
            continue

        if date_col:
            raw_d = cleaned.get(date_col, "").strip().upper()
            if raw_d not in date_targets:
                continue
            date_matched = True

        # Numeric OHLCV validation
        try:
            o = float(cleaned[open_col])
            h = float(cleaned[high_col])
            l = float(cleaned[low_col])
            c = float(cleaned[close_col])
            v = float(cleaned[vol_col])
            if o <= 0 or l <= 0 or c <= 0 or h < l or v < 0:
                raise ValueError(f"Invalid price/volume values in row: {cleaned}")
        except (ValueError, TypeError, KeyError) as e:
            raise ValueError(f"Bhavcopy non-numeric or invalid OHLCV for {sym}: {e}")

        rows.append(cleaned)

    if date_col and not date_matched:
        raise ValueError(f"Bhavcopy source {source_path} contains no trading records for requested session {session_date}")

    if not rows:
        raise ValueError(f"No valid trading records found for session {session_date} in Bhavcopy source {source_path}")

    fieldnames = list(reader.fieldnames)
    return rows, fieldnames


def ingest_daily_bhavcopy(
    session_date: str,
    out_dir: Path,
    bhavcopy_source: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Ingests and normalizes official NSE CM Bhavcopy, generates canonical CSV,
    and seals output with manifest_{session_date}.json.
    """
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    source_path = find_bhavcopy_source(session_date, bhavcopy_source)
    if not source_path:
        err_msg = (
            f"FAIL_CLOSED: Missing verified official NSE CM Bhavcopy for session {session_date}. "
            f"Never fabricate synthetic or unverified Bhavcopy data per AGENTS.md Rule 8 v2."
        )
        raise FileNotFoundError(err_msg)

    source_sha = compute_file_sha256(source_path)
    rows, fieldnames = extract_and_validate_bhavcopy(source_path, session_date)

    # Write normalized canonical CSV
    csv_target = out_dir / f"bhavcopy_{session_date}.csv"
    with open(csv_target, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    csv_sha = compute_file_sha256(csv_target)
    now_ist = datetime.now(tz=IST).isoformat()

    # Write cryptographic manifest
    manifest_target = out_dir / f"manifest_{session_date}.json"
    manifest_payload = {
        "status": "NORMAL",
        "session_date": session_date,
        "source_file": str(source_path),
        "source_sha256": source_sha,
        "bhavcopy_file": csv_target.name,
        "bhavcopy_sha256": csv_sha,
        "record_count": len(rows),
        "generated_at": now_ist,
    }
    manifest_target.write_text(json.dumps(manifest_payload, indent=2, sort_keys=True), encoding="utf-8")

    return manifest_payload


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Official NSE CM Bhavcopy Ingestion and Manifest Sealer")
    parser.add_argument("--session-date", required=True, help="Session date (YYYY-MM-DD)")
    parser.add_argument("--bhavcopy-source", default=None, help="Path to raw Bhavcopy CSV/ZIP file")
    parser.add_argument("--out-dir", default=str(ROOT_DIR / "data" / "bhavcopy"), help="Output directory")

    args = parser.parse_args(argv)
    try:
        manifest = ingest_daily_bhavcopy(
            session_date=args.session_date,
            out_dir=Path(args.out_dir),
            bhavcopy_source=args.bhavcopy_source,
        )
        print(f"SUCCESS: Bhavcopy ingested for {args.session_date}. Records: {manifest['record_count']}, SHA: {manifest['bhavcopy_sha256'][:16]}...")
        return 0
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
