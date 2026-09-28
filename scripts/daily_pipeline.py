"""
scripts/daily_pipeline.py
=========================
NSE Daily After-Close Pipeline (JOB 0).
Complies strictly with:
- 20 Rules in shared/reviews/antigravity_data_program_2026-09-26.md
- Manifest schema in Part 3
- Additions C.1 to C.6 from Yashu (approved by Claude)

Fetches per trading day (after 18:30 IST):
1. That day's CM Bhavcopy (JOB0)
2. That day's F&O Bhavcopy (JOB0)
3. That day's security-wise delivery position (MTO) file (JOB0)
4. Next session's F&O ban list (fo_secban) (JOB0)

Key constraints:
- ONE process, ONE request at a time (Rule 3)
- >= 4.0s pause between requests (Rule 4)
- Daily cap 500 requests enforced (Rule 5)
- Stop immediately on HTTP 403 or 429 without retry (Rule 6)
- Honest User-Agent recorded in every manifest entry (Rule 7, C.6)
- 404 recorded as MISSING_404, not error (Rule 8)
- Retries: at most 2 retries, 30s apart, only for timeouts/5xx (Rule 8)
- Content validation: Zip CRC testzip(), fake success detection, date validation inside file (C.2, C.3, C.4)
- Resume-safe: skip already saved files matching SHA-256 (Rule 11)
- Strict write boundary: history/raw/ only (Rule 12)
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from dataclasses import dataclass
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple
import zipfile
from datetime import date, datetime, timedelta, timezone

import requests

try:
    import msvcrt
except ImportError:
    msvcrt = None

try:
    import fcntl
except ImportError:
    fcntl = None

IST = timezone(timedelta(hours=5, minutes=30))
MONTH_ABBR = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")

ARCHIVE_BASE_URL = "https://nsearchives.nseindia.com"
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
MIN_INTERVAL_SECONDS = 4.0
ARCHIVE_DAILY_CAP = 500
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 30.0

# Cutoff date where NSE transitioned from legacy format to UDiFF format
UDIFF_TRANSITION_DATE = date(2024, 7, 8)


def get_git_commit() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()
    except Exception:
        return "unknown"


KNOWN_WEEKEND_SESSIONS = {
    date(2005, 6, 4),
    date(2005, 11, 26),
    date(2006, 4, 29),
    date(2006, 6, 25),
    date(2006, 10, 21),
    date(2015, 2, 28),
    date(2020, 2, 1),
    date(2023, 11, 12),
    date(2024, 1, 20),
    date(2024, 3, 2),
    date(2024, 5, 18),
    date(2026, 2, 1),
}


def calculate_next_session_date(current_date: date) -> date:
    """
    Calculate the next scheduled session date.
    Checks known weekend special trading sessions first, then skips regular weekends.
    """
    for delta in (1, 2):
        cand = current_date + timedelta(days=delta)
        if cand in KNOWN_WEEKEND_SESSIONS:
            return cand

    weekday = current_date.weekday()
    if weekday == 4:  # Friday -> Monday
        return current_date + timedelta(days=3)
    elif weekday == 5:  # Saturday -> Monday
        return current_date + timedelta(days=2)
    elif weekday == 6:  # Sunday -> Monday
        return current_date + timedelta(days=1)
    else:
        return current_date + timedelta(days=1)


@dataclass
class DatasetConfig:
    job: str
    dataset_name: str
    folder: str
    target_date_type: str  # "trade_date" or "next_session_date"


JOB0_DATASETS: Dict[str, DatasetConfig] = {
    "cm_bhavcopy": DatasetConfig(
        job="JOB0",
        dataset_name="cm_bhavcopy",
        folder="cm_bhavcopy",
        target_date_type="trade_date",
    ),
    "fo_bhavcopy": DatasetConfig(
        job="JOB0",
        dataset_name="fo_bhavcopy",
        folder="fo_bhavcopy",
        target_date_type="trade_date",
    ),
    "mto": DatasetConfig(
        job="JOB0",
        dataset_name="mto",
        folder="mto",
        target_date_type="trade_date",
    ),
    "fo_secban": DatasetConfig(
        job="JOB0",
        dataset_name="fo_secban",
        folder="fo_secban",
        target_date_type="next_session_date",
    ),
}


@dataclass
class DailyRequestItem:
    dataset: str
    trade_date: date
    effective_date: date
    url: str
    expected_filename: str
    folder: str
    is_zip: bool


def build_request_item(dataset: str, trade_date: date, next_session_date: date) -> DailyRequestItem:
    cfg = JOB0_DATASETS[dataset]
    eff_date = next_session_date if cfg.target_date_type == "next_session_date" else trade_date
    yyyy = eff_date.strftime("%Y")
    mmm = MONTH_ABBR[eff_date.month - 1]
    dd = f"{eff_date.day:02d}"
    mm = f"{eff_date.month:02d}"
    yyyymmdd = eff_date.strftime("%Y%m%d")

    if dataset == "cm_bhavcopy":
        if eff_date >= UDIFF_TRANSITION_DATE:
            url = f"{ARCHIVE_BASE_URL}/content/cm/BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip"
            filename = f"BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip"
        else:
            url = f"{ARCHIVE_BASE_URL}/content/historical/EQUITIES/{yyyy}/{mmm}/cm{dd}{mmm}{yyyy}bhav.csv.zip"
            filename = f"cm{dd}{mmm}{yyyy}bhav.csv.zip"
        is_zip = True
    elif dataset == "fo_bhavcopy":
        if eff_date >= UDIFF_TRANSITION_DATE:
            url = f"{ARCHIVE_BASE_URL}/content/fo/BhavCopy_NSE_FO_0_0_0_{yyyymmdd}_F_0000.csv.zip"
            filename = f"BhavCopy_NSE_FO_0_0_0_{yyyymmdd}_F_0000.csv.zip"
        else:
            url = f"{ARCHIVE_BASE_URL}/content/historical/DERIVATIVES/{yyyy}/{mmm}/fo{dd}{mmm}{yyyy}bhav.csv.zip"
            filename = f"fo{dd}{mmm}{yyyy}bhav.csv.zip"
        is_zip = True
    elif dataset == "mto":
        url = f"{ARCHIVE_BASE_URL}/archives/equities/mto/MTO_{dd}{mm}{yyyy}.DAT"
        filename = f"MTO_{dd}{mm}{yyyy}.DAT"
        is_zip = False
    elif dataset == "fo_secban":
        url = f"{ARCHIVE_BASE_URL}/archives/fo/sec_ban/fo_secban_{dd}{mm}{yyyy}.csv"
        filename = f"fo_secban_{dd}{mm}{yyyy}.csv"
        is_zip = False
    else:
        raise ValueError(f"Unknown dataset: {dataset}")

    return DailyRequestItem(
        dataset=dataset,
        trade_date=trade_date,
        effective_date=eff_date,
        url=url,
        expected_filename=filename,
        folder=cfg.folder,
        is_zip=is_zip,
    )


def parse_date_str(val: str) -> Optional[date]:
    """Parse date from ISO (YYYY-MM-DD), DD-MMM-YYYY, or DD-MM-YYYY."""
    val = val.strip()
    # Try ISO YYYY-MM-DD
    try:
        return date.fromisoformat(val)
    except Exception:
        pass
    # Try DD-MMM-YYYY
    try:
        return datetime.strptime(val, "%d-%b-%Y").date()
    except Exception:
        pass
    # Try DD-MM-YYYY
    try:
        return datetime.strptime(val, "%d-%m-%Y").date()
    except Exception:
        pass
    return None


class DailyPipelineDownloader:
    def __init__(
        self,
        root_dir: Optional[Path] = None,
        pause_seconds: float = MIN_INTERVAL_SECONDS,
        daily_cap: int = ARCHIVE_DAILY_CAP,
        user_agent: str = DEFAULT_USER_AGENT,
    ):
        if root_dir is None:
            project_root = Path(__file__).resolve().parent.parent
            self.history_dir = (project_root / "shared" / "track2_liquid" / "history").resolve()
        else:
            self.history_dir = Path(root_dir).resolve()

        self.raw_dir = self.history_dir / "raw" / "nse_archive"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.raw_dir / "manifest.jsonl"

        self.pause_seconds = max(0.0, pause_seconds)
        self.daily_cap = daily_cap
        self.user_agent = user_agent
        self.code_commit = get_git_commit()

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        })

        self.requests_today = self._count_requests_today()
        self.saved_registry: Dict[str, str] = self._load_saved_registry()

    def safe_write(self, target_path: Path, data: bytes) -> None:
        """Enforces Rule 12: No writes outside history_dir."""
        target_path = Path(target_path).resolve()
        try:
            target_path.relative_to(self.history_dir)
        except ValueError:
            raise PermissionError(f"Write boundary violation: cannot write outside {self.history_dir}: {target_path}")

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(data)

    def _count_requests_today(self) -> int:
        if not self.manifest_path.exists():
            return 0
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        count = 0
        try:
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        ts = str(record.get("requested_at") or record.get("fetched_at") or "")
                        if ts.startswith(today_str):
                            if record.get("outcome") != "SKIPPED_ALREADY_SAVED":
                                count += 1
                    except Exception:
                        continue
        except Exception:
            return 0
        return count

    def _load_saved_registry(self) -> Dict[str, str]:
        registry: Dict[str, str] = {}
        if not self.manifest_path.exists():
            return registry
        try:
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        if record.get("outcome") == "SAVED" and record.get("saved_path") and record.get("sha256"):
                            registry[record["saved_path"]] = record["sha256"]
                    except Exception:
                        continue
        except Exception:
            pass
        return registry

    def _is_blocked_today(self) -> bool:
        today_iso = datetime.now(IST).date().isoformat()
        marker_file = self.raw_dir / f".blocked_{today_iso}"
        if marker_file.exists():
            return True
        if not self.manifest_path.exists():
            return False
        try:
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        ts = str(rec.get("requested_at") or rec.get("fetched_at") or "")
                        if ts.startswith(today_iso):
                            if str(rec.get("outcome", "")).startswith("STOPPED") or rec.get("http_status") in (403, 429):
                                return True
                    except Exception:
                        continue
        except Exception:
            pass
        return False

    def _record_block_today(self, status: int, url: str) -> None:
        today_iso = datetime.now(IST).date().isoformat()
        marker_file = self.raw_dir / f".blocked_{today_iso}"
        try:
            marker_file.write_text(
                json.dumps({
                    "date": today_iso,
                    "status": status,
                    "url": url,
                    "timestamp": datetime.now(IST).isoformat(timespec="seconds"),
                }),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _pace(self) -> str:
        """
        Enforces monotonic pacing >= pause_seconds between consecutive requests sent to the network.
        Persists a cross-process clock and file lock so multiple instances share the same pacing.
        Returns the ISO timestamp (+05:30) of the exact moment the request is sent (requested_at).
        """
        clock_file = self.raw_dir / ".pacing_clock"
        lock_file = self.raw_dir / ".pacing.lock"

        with open(lock_file, "a+", encoding="utf-8") as lf:
            if msvcrt is not None and hasattr(msvcrt, "locking"):
                try:
                    lf.seek(0)
                    msvcrt.locking(lf.fileno(), msvcrt.LK_LOCK, 1)
                except Exception:
                    pass
            elif fcntl is not None:
                try:
                    fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
                except Exception:
                    pass

            try:
                if clock_file.exists():
                    try:
                        content = clock_file.read_text(encoding="utf-8").strip()
                        if content:
                            last_epoch = float(content.split(",")[0])
                            while True:
                                elapsed = time.time() - last_epoch
                                if elapsed >= self.pause_seconds:
                                    break
                                time.sleep(max(0.002, self.pause_seconds - elapsed))
                    except Exception:
                        pass
                if getattr(self, "_last_request_time", None) is not None:
                    while True:
                        elapsed = time.monotonic() - self._last_request_time
                        if elapsed >= self.pause_seconds:
                            break
                        time.sleep(max(0.002, self.pause_seconds - elapsed))

                now_epoch = time.time()
                now_dt = datetime.now(IST)
                iso_str = now_dt.isoformat(timespec="seconds")
                try:
                    clock_file.write_text(f"{now_epoch},{iso_str}", encoding="utf-8")
                except Exception:
                    pass
                self._last_request_time = time.monotonic()
                return iso_str
            finally:
                if msvcrt is not None and hasattr(msvcrt, "locking"):
                    try:
                        lf.seek(0)
                        msvcrt.locking(lf.fileno(), msvcrt.LK_UNLCK, 1)
                    except Exception:
                        pass
                elif fcntl is not None:
                    try:
                        fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
                    except Exception:
                        pass

    def append_manifest_record(self, record: Dict[str, Any]) -> None:
        serialized = json.dumps(record) + "\n"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        with open(self.manifest_path, "a", encoding="utf-8") as f:
            f.write(serialized)

    def validate_content(self, item: DailyRequestItem, content: bytes) -> Tuple[bool, str, str]:
        """
        Validates content and returns (is_valid, outcome_code, failure_reason).
        Outcome codes: SAVED, CORRUPT_DOWNLOAD, FAILED_BAD_CONTENT, FAILED_WRONG_DATE.
        """
        if not content:
            return False, "FAILED_BAD_CONTENT", "Empty content received"

        if content.lstrip().startswith(b"<") or b"<html" in content[:200].lower() or b"<!doctype html" in content[:200].lower():
            return False, "FAILED_BAD_CONTENT", "HTML response received instead of data file (fake success)"

        eff_date = item.effective_date

        if item.is_zip:
            if not content.startswith(b"PK\x03\x04"):
                return False, "FAILED_BAD_CONTENT", "Invalid zip magic bytes (not a real zip)"
            try:
                buf = io.BytesIO(content)
                with zipfile.ZipFile(buf, "r") as zf:
                    corrupt_member = zf.testzip()
                    if corrupt_member is not None:
                        return False, "CORRUPT_DOWNLOAD", f"Corrupt zip CRC error in member: {corrupt_member}"

                    namelist = zf.namelist()
                    if not namelist:
                        return False, "FAILED_BAD_CONTENT", "Zip archive contains no files"

                    csv_name = namelist[0]
                    with zf.open(csv_name, "r") as csv_f:
                        first_bytes = csv_f.read(8192)
                        text = first_bytes.decode("utf-8", errors="replace")

                        # Parse CSV lines
                        reader = csv.reader(io.StringIO(text))
                        header = next(reader, None)
                        if not header:
                            return False, "FAILED_BAD_CONTENT", f"Empty CSV in zip {csv_name}"

                        # Locate date column
                        date_idx = None
                        if "TradDt" in header:
                            date_idx = header.index("TradDt")
                        elif "TIMESTAMP" in header:
                            date_idx = header.index("TIMESTAMP")

                        if date_idx is None:
                            return False, "FAILED_BAD_CONTENT", f"Neither TradDt nor TIMESTAMP in CSV header: {header[:6]}"

                        first_row = next(reader, None)
                        if not first_row or date_idx >= len(first_row):
                            return False, "FAILED_BAD_CONTENT", f"No data row found in CSV {csv_name}"

                        val_str = first_row[date_idx].strip()
                        parsed_d = parse_date_str(val_str)
                        if parsed_d is None:
                            return False, "FAILED_BAD_CONTENT", f"Unparseable date in row: '{val_str}'"

                        if parsed_d != eff_date:
                            return False, "FAILED_WRONG_DATE", f"Date mismatch inside zip member {csv_name}: found {parsed_d} != expected {eff_date}"
            except Exception as e:
                return False, "FAILED_BAD_CONTENT", f"Zip inspection failed: {e}"
            return True, "SAVED", ""

        if item.dataset == "mto":
            try:
                text = content[:2048].decode("utf-8", errors="replace")
                if not text.startswith("Security Wise Delivery Position"):
                    return False, "FAILED_BAD_CONTENT", "MTO header missing 'Security Wise Delivery Position'"
                m = re.search(r"Trade Date\s*<([^>]+)>", text, re.IGNORECASE)
                if not m:
                    return False, "FAILED_BAD_CONTENT", "Missing 'Trade Date <...>' in header"
                header_date_str = m.group(1).strip()
                parsed_d = parse_date_str(header_date_str)
                if parsed_d is None:
                    return False, "FAILED_BAD_CONTENT", f"Unparseable Trade Date header: '{header_date_str}'"
                if parsed_d != eff_date:
                    return False, "FAILED_WRONG_DATE", f"MTO header trade date mismatch: found {parsed_d} != expected {eff_date}"
            except Exception as e:
                return False, "FAILED_BAD_CONTENT", f"MTO inspection failed: {e}"
            return True, "SAVED", ""

        if item.dataset == "fo_secban":
            try:
                text = content[:2048].decode("utf-8", errors="replace")
                if not text.lower().startswith("securities in ban"):
                    return False, "FAILED_BAD_CONTENT", "F&O ban header missing 'Securities in Ban'"
                m = re.search(r"(\d{2}-[A-Za-z]{3}-\d{4})", text)
                if not m:
                    return False, "FAILED_BAD_CONTENT", "Missing date in F&O ban header"
                header_date_str = m.group(1).strip()
                parsed_d = parse_date_str(header_date_str)
                if parsed_d is None:
                    return False, "FAILED_BAD_CONTENT", f"Unparseable F&O ban date: '{header_date_str}'"
                if parsed_d != eff_date:
                    return False, "FAILED_WRONG_DATE", f"F&O ban trade date mismatch: found {parsed_d} != expected {eff_date}"
            except Exception as e:
                return False, "FAILED_BAD_CONTENT", f"F&O ban inspection failed: {e}"
            return True, "SAVED", ""

        return True, "SAVED", ""

    def get_target_path(self, item: DailyRequestItem) -> Path:
        yyyy = item.effective_date.strftime("%Y")
        eff_iso = item.effective_date.isoformat()
        if item.dataset == "cm_bhavcopy":
            return self.history_dir / "bhavcopy" / "raw" / "cm" / yyyy / f"{eff_iso}.csv.gz"
        elif item.dataset == "fo_bhavcopy":
            return self.history_dir / "bhavcopy" / "raw" / "fo" / yyyy / f"{eff_iso}.csv.gz"
        elif item.dataset == "fo_secban":
            return self.history_dir / "raw" / "nse" / "fo_ban" / f"{eff_iso}_{eff_iso}.csv.gz"
        elif item.dataset == "mto":
            return self.history_dir / "raw" / "nse_archive" / "mto" / yyyy / item.expected_filename
        else:
            return self.raw_dir / item.folder / yyyy / item.expected_filename

    def get_rel_saved_path(self, item: DailyRequestItem) -> str:
        target = self.get_target_path(item)
        return str(target.relative_to(self.history_dir)).replace("\\", "/")

    def save_file(self, item: DailyRequestItem, content: bytes) -> Tuple[Path, str]:
        target_path = self.get_target_path(item)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        final_bytes = content
        if item.dataset in ("cm_bhavcopy", "fo_bhavcopy"):
            if content.startswith(b"PK\x03\x04"):
                with zipfile.ZipFile(io.BytesIO(content)) as zf:
                    csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
                    if csv_names:
                        csv_data = zf.read(csv_names[0])
                        final_bytes = gzip.compress(csv_data)
                    else:
                        final_bytes = gzip.compress(content)
            elif not content.startswith(b"\x1f\x8b"):
                final_bytes = gzip.compress(content)
        elif item.dataset == "fo_secban":
            if not content.startswith(b"\x1f\x8b"):
                final_bytes = gzip.compress(content)

        sha256_hash = hashlib.sha256(final_bytes).hexdigest()

        if target_path.exists():
            existing_sha = hashlib.sha256(target_path.read_bytes()).hexdigest()
            if existing_sha == sha256_hash:
                return target_path, sha256_hash
            else:
                timestamp_str = datetime.now(IST).strftime("%Y%m%d_%H%M%S")
                target_path = target_path.parent / f"{target_path.stem}_diff_{timestamp_str}{target_path.suffix}"

        self.safe_write(target_path, final_bytes)
        return target_path, sha256_hash

    def fetch_item(self, item: DailyRequestItem) -> Dict[str, Any]:
        yyyy = item.effective_date.strftime("%Y")
        rel_saved_path = self.get_rel_saved_path(item)

        # Resume check
        if rel_saved_path in self.saved_registry:
            full_disk_path = self.history_dir / rel_saved_path
            if full_disk_path.exists():
                disk_sha = hashlib.sha256(full_disk_path.read_bytes()).hexdigest()
                if disk_sha == self.saved_registry[rel_saved_path]:
                    now_ts = datetime.now(IST).isoformat(timespec="seconds")
                    record = {
                        "job": "JOB0",
                        "dataset": item.dataset,
                        "trade_date": item.trade_date.isoformat(),
                        "url": item.url,
                        "attempt": 1,
                        "http_status": 200,
                        "outcome": "SKIPPED_ALREADY_SAVED",
                        "bytes": full_disk_path.stat().st_size,
                        "sha256": disk_sha,
                        "saved_path": rel_saved_path,
                        "requested_at": now_ts,
                        "fetched_at": now_ts,
                        "code_commit": self.code_commit,
                        "requests_today": self.requests_today,
                        "user_agent": self.user_agent,
                    }
                    return record

        for attempt in range(1, MAX_RETRIES + 2):
            now_ts = datetime.now(IST).isoformat(timespec="seconds")
            if self._is_blocked_today():
                record = {
                    "job": "JOB0",
                    "dataset": item.dataset,
                    "trade_date": item.trade_date.isoformat(),
                    "url": item.url,
                    "attempt": attempt,
                    "http_status": 403,
                    "outcome": "STOPPED_403",
                    "bytes": 0,
                    "sha256": "",
                    "saved_path": "",
                    "requested_at": now_ts,
                    "fetched_at": now_ts,
                    "code_commit": self.code_commit,
                    "requests_today": self.requests_today,
                    "user_agent": self.user_agent,
                    "error": "Host is blocked today per Rule 6 (HTTP 403/429 recorded)",
                }
                return record

            if self.requests_today >= self.daily_cap:
                record = {
                    "job": "JOB0",
                    "dataset": item.dataset,
                    "trade_date": item.trade_date.isoformat(),
                    "url": item.url,
                    "attempt": attempt,
                    "http_status": None,
                    "outcome": "STOPPED_CAP_REACHED",
                    "bytes": 0,
                    "sha256": "",
                    "saved_path": "",
                    "requested_at": now_ts,
                    "fetched_at": now_ts,
                    "code_commit": self.code_commit,
                    "requests_today": self.requests_today,
                    "user_agent": self.user_agent,
                    "error": f"Daily cap of {self.daily_cap} reached",
                }
                return record

            requested_at = self._pace()
            self.requests_today += 1

            try:
                resp = self.session.get(item.url, timeout=20.0)
                status = resp.status_code
                fetched_at = datetime.now(IST).isoformat(timespec="seconds")

                if status == 403:
                    self._record_block_today(403, item.url)
                    record = {
                        "job": "JOB0",
                        "dataset": item.dataset,
                        "trade_date": item.trade_date.isoformat(),
                        "url": item.url,
                        "attempt": attempt,
                        "http_status": 403,
                        "outcome": "STOPPED_403",
                        "bytes": 0,
                        "sha256": "",
                        "saved_path": "",
                        "requested_at": requested_at,
                        "fetched_at": fetched_at,
                        "code_commit": self.code_commit,
                        "requests_today": self.requests_today,
                        "user_agent": self.user_agent,
                    }
                    self.append_manifest_record(record)
                    return record

                if status == 429:
                    self._record_block_today(429, item.url)
                    record = {
                        "job": "JOB0",
                        "dataset": item.dataset,
                        "trade_date": item.trade_date.isoformat(),
                        "url": item.url,
                        "attempt": attempt,
                        "http_status": 429,
                        "outcome": "STOPPED_429",
                        "bytes": 0,
                        "sha256": "",
                        "saved_path": "",
                        "requested_at": requested_at,
                        "fetched_at": fetched_at,
                        "code_commit": self.code_commit,
                        "requests_today": self.requests_today,
                        "user_agent": self.user_agent,
                    }
                    self.append_manifest_record(record)
                    return record

                if status == 404:
                    record = {
                        "job": "JOB0",
                        "dataset": item.dataset,
                        "trade_date": item.trade_date.isoformat(),
                        "url": item.url,
                        "attempt": attempt,
                        "http_status": 404,
                        "outcome": "MISSING_404",
                        "bytes": 0,
                        "sha256": "",
                        "saved_path": "",
                        "requested_at": requested_at,
                        "fetched_at": fetched_at,
                        "code_commit": self.code_commit,
                        "requests_today": self.requests_today,
                        "user_agent": self.user_agent,
                    }
                    self.append_manifest_record(record)
                    return record

                if status == 200:
                    content = resp.content
                    is_valid, out_code, err_msg = self.validate_content(item, content)
                    if not is_valid:
                        saved_path = ""
                        saved_bytes = 0
                        saved_sha = ""
                        if out_code == "FAILED_BAD_CONTENT":
                            # Addition C.2: Quarantine bad content under bad_content/ for inspection
                            bad_file = self.raw_dir / "bad_content" / item.folder / yyyy / f"{item.expected_filename}.bad"
                            self.safe_write(bad_file, content)
                            saved_path = f"raw/nse_archive/bad_content/{item.folder}/{yyyy}/{item.expected_filename}.bad"
                            saved_bytes = len(content)
                            saved_sha = hashlib.sha256(content).hexdigest()

                        record = {
                            "job": "JOB0",
                            "dataset": item.dataset,
                            "trade_date": item.trade_date.isoformat(),
                            "url": item.url,
                            "attempt": attempt,
                            "http_status": 200,
                            "outcome": out_code,
                            "bytes": saved_bytes,
                            "sha256": saved_sha,
                            "saved_path": saved_path,
                            "requested_at": requested_at,
                            "fetched_at": fetched_at,
                            "code_commit": self.code_commit,
                            "requests_today": self.requests_today,
                            "user_agent": self.user_agent,
                            "error": err_msg,
                        }
                        self.append_manifest_record(record)
                        return record

                    target_file, sha256_hash = self.save_file(item, content)
                    file_size = target_file.stat().st_size
                    saved_rel = str(target_file.relative_to(self.history_dir)).replace("\\", "/")
                    self.saved_registry[saved_rel] = sha256_hash

                    record = {
                        "job": "JOB0",
                        "dataset": item.dataset,
                        "trade_date": item.trade_date.isoformat(),
                        "url": item.url,
                        "attempt": attempt,
                        "http_status": 200,
                        "outcome": "SAVED",
                        "bytes": file_size,
                        "sha256": sha256_hash,
                        "saved_path": saved_rel,
                        "requested_at": requested_at,
                        "fetched_at": fetched_at,
                        "code_commit": self.code_commit,
                        "requests_today": self.requests_today,
                        "user_agent": self.user_agent,
                    }
                    self.append_manifest_record(record)
                    return record

                if attempt <= MAX_RETRIES and status >= 500:
                    retry_rec = {
                        "job": "JOB0",
                        "dataset": item.dataset,
                        "trade_date": item.trade_date.isoformat(),
                        "url": item.url,
                        "attempt": attempt,
                        "http_status": status,
                        "outcome": f"RETRY_{status}",
                        "bytes": 0,
                        "sha256": "",
                        "saved_path": "",
                        "requested_at": requested_at,
                        "fetched_at": fetched_at,
                        "code_commit": self.code_commit,
                        "requests_today": self.requests_today,
                        "user_agent": self.user_agent,
                        "error": f"HTTP status {status}, retrying",
                    }
                    self.append_manifest_record(retry_rec)
                    time.sleep(RETRY_BACKOFF_SECONDS)
                    continue

                record = {
                    "job": "JOB0",
                    "dataset": item.dataset,
                    "trade_date": item.trade_date.isoformat(),
                    "url": item.url,
                    "attempt": attempt,
                    "http_status": status,
                    "outcome": "FAILED",
                    "bytes": 0,
                    "sha256": "",
                    "saved_path": "",
                    "requested_at": requested_at,
                    "fetched_at": fetched_at,
                    "code_commit": self.code_commit,
                    "requests_today": self.requests_today,
                    "user_agent": self.user_agent,
                    "error": f"HTTP status {status}",
                }
                self.append_manifest_record(record)
                return record

            except (requests.Timeout, requests.ConnectionError) as req_err:
                err_outcome = "RETRY_TIMEOUT" if isinstance(req_err, requests.Timeout) else "RETRY_CONN"
                fetched_at = datetime.now(IST).isoformat(timespec="seconds")
                if attempt <= MAX_RETRIES:
                    retry_rec = {
                        "job": "JOB0",
                        "dataset": item.dataset,
                        "trade_date": item.trade_date.isoformat(),
                        "url": item.url,
                        "attempt": attempt,
                        "http_status": None,
                        "outcome": err_outcome,
                        "bytes": 0,
                        "sha256": "",
                        "saved_path": "",
                        "requested_at": requested_at,
                        "fetched_at": fetched_at,
                        "code_commit": self.code_commit,
                        "requests_today": self.requests_today,
                        "user_agent": self.user_agent,
                        "error": str(req_err),
                    }
                    self.append_manifest_record(retry_rec)
                    time.sleep(RETRY_BACKOFF_SECONDS)
                    continue
                record = {
                    "job": "JOB0",
                    "dataset": item.dataset,
                    "trade_date": item.trade_date.isoformat(),
                    "url": item.url,
                    "attempt": attempt,
                    "http_status": None,
                    "outcome": "FAILED",
                    "bytes": 0,
                    "sha256": "",
                    "saved_path": "",
                    "requested_at": requested_at,
                    "fetched_at": fetched_at,
                    "code_commit": self.code_commit,
                    "requests_today": self.requests_today,
                    "user_agent": self.user_agent,
                    "error": str(req_err),
                }
                self.append_manifest_record(record)
                return record

        return {}

    def build_daily_items(
        self,
        trade_date: date,
        next_session_date: Optional[date] = None,
    ) -> List[DailyRequestItem]:
        if next_session_date is None:
            next_session_date = calculate_next_session_date(trade_date)

        items = []
        for dset in ("cm_bhavcopy", "fo_bhavcopy", "mto", "fo_secban"):
            items.append(build_request_item(dset, trade_date, next_session_date))
        return items

    def execute_items(self, items: List[DailyRequestItem]) -> Counter:
        stats: Counter = Counter()
        results: List[Dict[str, Any]] = []

        for item in items:
            record = self.fetch_item(item)
            results.append(record)
            outcome = record.get("outcome", "UNKNOWN")
            stats[outcome] += 1

            if outcome in ("STOPPED_403", "STOPPED_429", "STOPPED_CAP_REACHED"):
                break

        # Generate daily report file
        if items:
            t_date = items[0].trade_date
            next_date = calculate_next_session_date(t_date)
            for itm in items:
                if itm.dataset == "fo_secban":
                    next_date = itm.effective_date

            report_data = {
                "job": "JOB0",
                "trade_date": t_date.isoformat(),
                "next_session_date": next_date.isoformat(),
                "generated_at": datetime.now(IST).isoformat(timespec="seconds"),
                "requests_today": self.requests_today,
                "saved_count": stats.get("SAVED", 0) + stats.get("SKIPPED_ALREADY_SAVED", 0),
                "missing_count": stats.get("MISSING_404", 0),
                "failed_count": stats.get("FAILED", 0),
                "manifest_lines": results,
            }
            report_path = self.raw_dir / f"daily_report_{t_date.isoformat()}.json"
            report_path.write_text(json.dumps(report_data, indent=2), encoding="utf-8")

        return stats


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="NSE Daily After-Close Pipeline (JOB 0)")
    parser.add_argument("--trade-date", help="Trade date in YYYY-MM-DD format (default: today)")
    parser.add_argument("--next-ban-date", help="Next session date for F&O ban file in YYYY-MM-DD format (default: auto next weekday)")
    parser.add_argument("--root-dir", help="Root storage directory (default: shared/track2_liquid/history)")
    parser.add_argument("--pause", type=float, default=MIN_INTERVAL_SECONDS, help="Pause in seconds between requests (min 4.0)")
    parser.add_argument("--daily-cap", type=int, default=ARCHIVE_DAILY_CAP, help="Daily request cap (default: 500)")
    args = parser.parse_args(argv)

    if args.trade_date:
        t_date = date.fromisoformat(args.trade_date)
    else:
        now_ist = datetime.now(IST)
        t_date = now_ist.date()

    next_ban_date = date.fromisoformat(args.next_ban_date) if args.next_ban_date else calculate_next_session_date(t_date)

    root_dir = Path(args.root_dir) if args.root_dir else None
    pause = max(MIN_INTERVAL_SECONDS, args.pause)

    print(f"=== JOB 0 Daily After-Close Pipeline ===")
    print(f"Trade Date: {t_date.isoformat()} ({t_date.strftime('%A')})")
    print(f"Next Session Ban Date: {next_ban_date.isoformat()} ({next_ban_date.strftime('%A')})")
    print(f"Pacing: {pause:.1f}s | Daily Cap: {args.daily_cap}")

    downloader = DailyPipelineDownloader(
        root_dir=root_dir,
        pause_seconds=pause,
        daily_cap=args.daily_cap,
    )

    items = downloader.build_daily_items(t_date, next_ban_date)
    print(f"Prepared {len(items)} requests to fetch:")
    for itm in items:
        print(f"  - [{itm.dataset}] eff_date={itm.effective_date} -> {itm.url}")

    stats = downloader.execute_items(items)
    print("\nExecution complete. Stats:")
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")

    if stats.get("STOPPED_403", 0) > 0 or stats.get("STOPPED_429", 0) > 0 or stats.get("FAILED", 0) > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
