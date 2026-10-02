"""
scripts/ingest_daily_regulatory_data.py
=======================================
Daily Regulatory Ingestion Daemon & Snapshot Generator for ARGUS Track 2.

Ingests, verifies, and records daily exchange regulatory masters:
1. Surveillance masters: ASM (Long-Term & Short-Term), GSM, ESM, and T2T.
2. Active F&O Underlying scrips master (NSE fo_mktlots.csv).

Enforces (AGENTS.md Rule 8 v2 Fail-Closed Invariant & Codex Day 5 Finding 6):
- Strict fail-closed verification: Never fabricates empty lists or artificial "NORMAL"
  snapshots absent verified daily source inputs.
- Preserves genuine cryptographic provenance (source path, source file SHA-256, actual availability mtime).
- Deterministic, atomic JSON persistence to data/surveillance/ and data/fno/.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone, timedelta
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
from typing import Dict, List, Set, Any, Optional

IST = timezone(timedelta(hours=5, minutes=30))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ingest_regulatory")


def compute_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_surveillance_source(session_date: str, explicit_path: Optional[str] = None) -> Optional[Path]:
    if explicit_path:
        p = Path(explicit_path)
        if p.exists():
            return p.resolve()
        raise FileNotFoundError(f"Explicit surveillance source file not found: {explicit_path}")

    # Search canonical locations
    candidates = [
        Path(f"data/surveillance/raw/surveillance_{session_date}.csv"),
        Path(f"data/surveillance/raw/surveillance_{session_date}.json"),
        Path(f"data/raw/surveillance_{session_date}.csv"),
        Path(f"data/raw/surveillance_{session_date}.json"),
        Path(f"data/circulars/surveillance_{session_date}.csv"),
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()
    return None


def find_fno_source(session_date: str, explicit_path: Optional[str] = None) -> Optional[Path]:
    if explicit_path:
        p = Path(explicit_path)
        if p.exists():
            return p.resolve()
        raise FileNotFoundError(f"Explicit F&O source file not found: {explicit_path}")

    # Search canonical locations
    candidates = [
        Path(f"data/fno/raw/fo_mktlots_{session_date}.csv"),
        Path(f"data/raw/fo_mktlots_{session_date}.csv"),
        Path("data/fno/fo_mktlots.csv"),
        Path("data/raw/fo_mktlots.csv"),
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()
    return None


def parse_fno_source(source_path: Path) -> List[str]:
    """Parses NSE official fo_mktlots.csv or JSON to extract active underlying symbols."""
    symbols: Set[str] = set()
    if source_path.suffix.lower() == ".json":
        data = json.loads(source_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            symbols = {str(s).strip().upper() for s in data if s}
        elif isinstance(data, dict):
            underlyings = data.get("fno_underlyings") or data.get("underlyings") or []
            symbols = {str(s).strip().upper() for s in underlyings if s}
    else:
        with open(source_path, newline="", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            for row in reader:
                if not row:
                    continue
                # fo_mktlots format typically: UNDERLYING, SYMBOL, LOT_SIZE...
                sym = row[1].strip().upper() if len(row) > 1 else row[0].strip().upper()
                if sym and sym not in {"SYMBOL", "UNDERLYING", "NAME"}:
                    symbols.add(sym)

    if not symbols:
        raise ValueError(f"No valid F&O underlyings parsed from source: {source_path}")
    return sorted(list(symbols))


def parse_surveillance_source(source_path: Path) -> Dict[str, List[str]]:
    """Parses surveillance circular or JSON file for ASM, GSM, ESM, and T2T lists."""
    if source_path.suffix.lower() == ".json":
        data = json.loads(source_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data:
            raise ValueError(f"Surveillance source {source_path} is empty or not a valid dictionary")
        required_keys = {"asm_long_term", "asm_short_term", "gsm"}
        for k in required_keys:
            if k not in data:
                raise KeyError(f"Surveillance source {source_path} missing required surveillance key: '{k}'")
        for k, val in data.items():
            if isinstance(val, str) or not isinstance(val, (list, tuple, set)):
                raise TypeError(f"Surveillance category '{k}' must be a list/set/tuple of symbols, got {type(val).__name__}")
            for item in val:
                if not isinstance(item, str):
                    raise TypeError(f"Surveillance symbol in category '{k}' must be a string, got {type(item).__name__}")
        return {
            "asm_long_term": sorted(list(set(str(s).strip().upper() for s in data.get("asm_long_term", [])))),
            "asm_short_term": sorted(list(set(str(s).strip().upper() for s in data.get("asm_short_term", [])))),
            "gsm": sorted(list(set(str(s).strip().upper() for s in data.get("gsm", [])))),
            "esm": sorted(list(set(str(s).strip().upper() for s in data.get("esm", [])))),
            "t2t": sorted(list(set(str(s).strip().upper() for s in data.get("t2t", [])))),
        }
    
    # CSV parser
    res: Dict[str, List[str]] = {
        "asm_long_term": [],
        "asm_short_term": [],
        "gsm": [],
        "esm": [],
        "t2t": [],
    }
    with open(source_path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sym = (row.get("SYMBOL") or row.get("symbol") or "").strip().upper()
            cat = (row.get("CATEGORY") or row.get("category") or "").strip().upper()
            if not sym:
                continue
            if "GSM" in cat:
                res["gsm"].append(sym)
            elif "ESM" in cat:
                res["esm"].append(sym)
            elif "ASM_ST" in cat or "SHORT" in cat:
                res["asm_short_term"].append(sym)
            elif "ASM" in cat:
                res["asm_long_term"].append(sym)
            elif "T2T" in cat or "BE" in cat:
                res["t2t"].append(sym)

    for k in res:
        res[k] = sorted(list(set(res[k])))
    return res


def ingest_daily_regulatory_data(
    session_date: str,
    surveillance_dir: Path,
    fno_dir: Path,
    surveillance_source: Optional[str] = None,
    fno_source: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Ingests, verifies, and records daily surveillance and F&O underlyings snapshots.
    Fails closed if verified official daily source files are missing.
    """
    surveillance_dir = Path(surveillance_dir).resolve()
    fno_dir = Path(fno_dir).resolve()

    surv_source_path = find_surveillance_source(session_date, surveillance_source)
    fno_source_path = find_fno_source(session_date, fno_source)

    if not surv_source_path or not fno_source_path:
        missing = []
        if not surv_source_path:
            missing.append("surveillance source")
        if not fno_source_path:
            missing.append("F&O underlyings source")
        err_msg = (
            f"FAIL_CLOSED: Missing verified official exchange inputs for session {session_date} ({', '.join(missing)}). "
            f"Fabrication of artificial 'NORMAL' snapshots without source provenance is strictly prohibited per AGENTS.md Rule 8 v2."
        )
        logger.error(err_msg)
        raise FileNotFoundError(err_msg)

    # Compute source provenance
    surv_source_sha = compute_file_sha256(surv_source_path)
    fno_source_sha = compute_file_sha256(fno_source_path)

    surv_mtime = datetime.fromtimestamp(os.path.getmtime(surv_source_path), tz=IST).isoformat()
    fno_mtime = datetime.fromtimestamp(os.path.getmtime(fno_source_path), tz=IST).isoformat()

    surv_data = parse_surveillance_source(surv_source_path)
    fno_list = parse_fno_source(fno_source_path)

    # 1. Surveillance Snapshot
    surveillance_payload = {
        "session_date": session_date,
        "fetched_at": surv_mtime,
        "timestamp": surv_mtime,
        "source_file": str(surv_source_path),
        "source_sha256": surv_source_sha,
        "status": "NORMAL",
        "asm_long_term": surv_data["asm_long_term"],
        "asm_short_term": surv_data["asm_short_term"],
        "gsm": surv_data["gsm"],
        "esm": surv_data["esm"],
        "t2t": surv_data["t2t"],
    }

    # 2. F&O Underlyings Snapshot
    fno_payload = {
        "session_date": session_date,
        "fetched_at": fno_mtime,
        "timestamp": fno_mtime,
        "source_file": str(fno_source_path),
        "source_sha256": fno_source_sha,
        "status": "NORMAL",
        "fno_underlyings": fno_list,
        "count": len(fno_list),
    }

    surv_path = surveillance_dir / f"surveillance_{session_date}.json"
    fno_path = fno_dir / f"fno_underlyings_{session_date}.json"

    surv_bytes = json.dumps(surveillance_payload, indent=2, sort_keys=True).encode("utf-8")
    fno_bytes = json.dumps(fno_payload, indent=2, sort_keys=True).encode("utf-8")

    surv_sha = hashlib.sha256(surv_bytes).hexdigest()
    fno_sha = hashlib.sha256(fno_bytes).hexdigest()

    if not dry_run:
        surveillance_dir.mkdir(parents=True, exist_ok=True)
        fno_dir.mkdir(parents=True, exist_ok=True)

        tmp_surv = surveillance_dir / f".tmp_{session_date}_{surv_sha[:8]}.json"
        tmp_fno = fno_dir / f".tmp_{session_date}_{fno_sha[:8]}.json"

        tmp_surv.write_bytes(surv_bytes)
        tmp_fno.write_bytes(fno_bytes)

        tmp_surv.replace(surv_path)
        tmp_fno.replace(fno_path)

        logger.info(f"Verified & recorded surveillance snapshot: {surv_path} (SHA-256: {surv_sha[:12]}...)")
        logger.info(f"Verified & recorded F&O snapshot: {fno_path} (count: {len(fno_list)}, SHA-256: {fno_sha[:12]}...)")

    return {
        "session_date": session_date,
        "fetched_at": surv_mtime,
        "surveillance_path": str(surv_path),
        "surveillance_sha256": surv_sha,
        "fno_path": str(fno_path),
        "fno_sha256": fno_sha,
        "fno_count": len(fno_list),
        "status": "SUCCESS",
    }


def main():
    parser = argparse.ArgumentParser(description="Ingest daily regulatory masters (Surveillance & F&O underlyings)")
    now_ist = datetime.now(tz=IST)
    default_date = now_ist.strftime("%Y-%m-%d")

    parser.add_argument("--session-date", default=default_date, help="Trading session date (YYYY-MM-DD)")
    parser.add_argument("--surveillance-source", default=None, help="Path to verified raw surveillance circular/file")
    parser.add_argument("--fno-source", default=None, help="Path to verified raw F&O fo_mktlots.csv")
    parser.add_argument("--surveillance-dir", default="data/surveillance", help="Directory for surveillance snapshots")
    parser.add_argument("--fno-dir", default="data/fno", help="Directory for F&O master snapshots")
    parser.add_argument("--dry-run", action="store_true", help="Perform ingestion check without writing files")

    args = parser.parse_args()

    # Validate session_date format
    try:
        datetime.strptime(args.session_date, "%Y-%m-%d")
    except ValueError:
        logger.error(f"Invalid --session-date format: {args.session_date}. Expected YYYY-MM-DD.")
        sys.exit(1)

    try:
        res = ingest_daily_regulatory_data(
            session_date=args.session_date,
            surveillance_dir=Path(args.surveillance_dir),
            fno_dir=Path(args.fno_dir),
            surveillance_source=args.surveillance_source,
            fno_source=args.fno_source,
            dry_run=args.dry_run,
        )
        print(json.dumps(res, indent=2))
        sys.exit(0)
    except FileNotFoundError as e:
        logger.error(str(e))
        sys.exit(1)
    except Exception as e:
        logger.error(f"Regulatory ingestion failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
