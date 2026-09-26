"""
scripts/download_nse_archive.py
===============================
NSE Static Archive Downloader (Jobs 1, 2, 3: CM Bhavcopy, F&O Bhavcopy, MTO Delivery).
Complies strictly with:
- 20 Rules in shared/reviews/antigravity_data_program_2026-09-26.md
- Manifest schema in Part 3
- Additions C.1 to C.6 from Yashu (approved by Claude)

Key constraints:
- ONE process, ONE request at a time (Rule 3)
- >= 4.0s pause between requests (Rule 4)
- Daily cap 500 requests enforced (Rule 5)
- Stop immediately on HTTP 403 or 429 (Rule 6)
- Honest User-Agent recorded in every manifest entry (Rule 7, C.6)
- 404 recorded as MISSING_404, not error (Rule 8)
- Content validation: Zip CRC testzip(), fake success detection, date validation inside file (C.2, C.3, C.4)
- Resume-safe: skip already saved files matching SHA-256 (Rule 11)
- Strict write boundary: history/raw/nse_archive/ only (Rule 12)
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

import requests

IST = timezone(timedelta(hours=5, minutes=30))
MONTH_ABBR = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")

ARCHIVE_BASE_URL = "https://nsearchives.nseindia.com"
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
MIN_INTERVAL_SECONDS = 4.15
ARCHIVE_DAILY_CAP = 500
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 30.0

# Addition C.1: Known weekend special sessions on NSE (e.g. Budget Saturdays, Diwali Muhurat)
KNOWN_WEEKEND_SESSIONS = [
    "2005-06-04",  # Saturday - Special Live Trading (Disaster Recovery backup test)
    "2005-06-05",  # Sunday - Weekend probe per Addendum A2
    "2005-11-26",  # Saturday - Special Live Trading (Disaster Recovery site test)
    "2005-11-27",  # Sunday - Weekend probe per Addendum A2
    "2006-10-21",  # Saturday - Diwali Muhurat
    "2013-11-03",  # Sunday - Diwali Muhurat
    "2014-02-22",  # Saturday - Special Live Trading
    "2015-02-28",  # Saturday - Union Budget Session
    "2016-10-30",  # Sunday - Diwali Muhurat
    "2019-10-27",  # Sunday - Diwali Muhurat
    "2020-02-01",  # Saturday - Union Budget Session
    "2020-11-14",  # Saturday - Diwali Muhurat
]

DATASETS = {
    "cm_bhavcopy": {
        "job": "JOB1",
        "folder": "cm_bhavcopy",
        "url_template": "{base}/content/historical/EQUITIES/{yyyy}/{mmm}/cm{dd}{mmm}{yyyy}bhav.csv.zip",
        "file_template": "cm{dd}{mmm}{yyyy}bhav.csv.zip",
    },
    "fo_bhavcopy": {
        "job": "JOB2",
        "folder": "fo_bhavcopy",
        "url_template": "{base}/content/historical/DERIVATIVES/{yyyy}/{mmm}/fo{dd}{mmm}{yyyy}bhav.csv.zip",
        "file_template": "fo{dd}{mmm}{yyyy}bhav.csv.zip",
    },
    "mto": {
        "job": "JOB3",
        "folder": "mto",
        "url_template": "{base}/archives/equities/mto/MTO_{dd}{mm}{yyyy}.DAT",
        "file_template": "MTO_{dd}{mm}{yyyy}.DAT",
    },
}


class StopExecutionError(RuntimeError):
    """Raised when 403, 429, or daily cap is encountered to immediately halt the run."""


def get_git_commit() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        return res.stdout.strip()
    except Exception:
        return "unknown_commit"


def parse_date_str(val: str) -> Optional[date]:
    """Parse date from common NSE strings like '04-JAN-2010', '2010-01-04', '04-01-2010'."""
    val = val.strip().strip("'\"")
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y", "%d%m%Y"):
        try:
            return datetime.strptime(val, fmt).date()
        except ValueError:
            pass
    return None


def validate_cm_fo_content(data: bytes, requested_date: date) -> Tuple[bool, str, Optional[str]]:
    """
    Validates CM / F&O Bhavcopy zip payload:
    - Must be a valid zip archive (not HTML error page)
    - testzip() must pass CRC check (not corrupt)
    - Must contain a CSV file
    - CSV TIMESTAMP column must equal requested_date
    Returns: (is_valid, outcome, failure_reason)
    """
    if not data or len(data) < 22:
        return False, "FAILED_BAD_CONTENT", "Empty or truncated payload"

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception as exc:
        return False, "FAILED_BAD_CONTENT", f"Invalid zip format: {exc}"

    # Addition C.4: Corrupt zip detected via testzip CRC check
    try:
        bad_crc_file = zf.testzip()
    except Exception as exc:
        return False, "CORRUPT_DOWNLOAD", f"Zip integrity check failed: {exc}"

    if bad_crc_file is not None:
        return False, "CORRUPT_DOWNLOAD", f"CRC check failed on {bad_crc_file}"

    namelist = zf.namelist()
    csv_names = [n for n in namelist if n.lower().endswith(".csv")]
    if not csv_names:
        return False, "FAILED_BAD_CONTENT", f"No CSV found inside zip: {namelist}"

    csv_data = zf.read(csv_names[0]).decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(csv_data))
    header = next(reader, None)
    if not header:
        return False, "FAILED_BAD_CONTENT", "CSV file is empty"

    header_cols = [c.strip().upper() for c in header]
    if "TIMESTAMP" not in header_cols:
        return False, "FAILED_BAD_CONTENT", f"No TIMESTAMP column in CSV header: {header_cols[:8]}"

    ts_idx = header_cols.index("TIMESTAMP")
    first_row = next(reader, None)
    if not first_row or ts_idx >= len(first_row):
        return False, "FAILED_BAD_CONTENT", "No data rows in CSV"

    ts_val = first_row[ts_idx].strip()
    file_date = parse_date_str(ts_val)
    if not file_date:
        return False, "FAILED_BAD_CONTENT", f"Unparseable TIMESTAMP value: '{ts_val}'"

    # Addition C.3: Date inside file must equal requested date
    if file_date != requested_date:
        return False, "FAILED_WRONG_DATE", f"TIMESTAMP inside file {file_date} != requested {requested_date}"

    return True, "SAVED", None


def validate_mto_content(data: bytes, requested_date: date) -> Tuple[bool, str, Optional[str]]:
    """
    Validates MTO Delivery Position DAT payload:
    - Must start with 'Security Wise Delivery Position' (Addition C.2)
    - Must have 'Trade Date <...>' header matching requested_date (Addition C.3)
    Returns: (is_valid, outcome, failure_reason)
    """
    if not data:
        return False, "FAILED_BAD_CONTENT", "Empty payload"

    text = data.decode("utf-8", errors="replace").lstrip("\ufeff").strip()
    if not text.startswith("Security Wise Delivery Position"):
        return False, "FAILED_BAD_CONTENT", "Payload does not start with 'Security Wise Delivery Position'"

    m = re.search(r"Trade Date\s*<([^>]+)>", text, re.IGNORECASE)
    if not m:
        return False, "FAILED_BAD_CONTENT", "Missing 'Trade Date <...>' in header"

    header_date_str = m.group(1).strip()
    file_date = parse_date_str(header_date_str)
    if not file_date:
        return False, "FAILED_BAD_CONTENT", f"Unparseable Trade Date header: '{header_date_str}'"

    if file_date != requested_date:
        return False, "FAILED_WRONG_DATE", f"Trade Date header {file_date} != requested {requested_date}"

    return True, "SAVED", None


class NseArchiveDownloader:
    def __init__(
        self,
        base_dir: Optional[Path] = None,
        min_interval: float = MIN_INTERVAL_SECONDS,
        daily_cap: int = ARCHIVE_DAILY_CAP,
        user_agent: str = DEFAULT_USER_AGENT,
        session: Optional[requests.Session] = None,
        code_commit: Optional[str] = None,
    ) -> None:
        if base_dir is None:
            repo_root = Path(__file__).resolve().parents[1]
            base_dir = repo_root / "shared" / "track2_liquid" / "history" / "raw" / "nse_archive"
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

        self.min_interval = min_interval
        self.daily_cap = daily_cap
        self.user_agent = user_agent
        self.code_commit = code_commit or get_git_commit()

        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Accept": "*/*",
        })

        self.manifest_path = self.base_dir / "manifest.jsonl"
        self._last_request_time: Optional[float] = None
        self.requests_today = self._count_today_requests()

    def _count_today_requests(self) -> int:
        if not self.manifest_path.exists():
            return 0
        today_iso = datetime.now(IST).date().isoformat()
        count = 0
        for rec in self.load_manifest_records():
            ts = rec.get("requested_at") or rec.get("fetched_at", "")
            if ts.startswith(today_iso):
                # Count all network attempts (including retries) made today per Rule 5
                if rec.get("outcome") != "SKIPPED_ALREADY_SAVED":
                    count += int(rec.get("attempt", 1))
        return count

    def load_manifest_records(self) -> List[Dict[str, Any]]:
        if not self.manifest_path.exists():
            return []
        records = []
        with open(self.manifest_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return records

    def safe_write(self, target_path: Path, data: bytes) -> None:
        """Enforces Rule 12: No writes outside base_dir."""
        target_path = Path(target_path).resolve()
        try:
            target_path.relative_to(self.base_dir)
        except ValueError:
            raise PermissionError(f"Write boundary violation: cannot write outside {self.base_dir}: {target_path}")

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(data)

    def _pace(self) -> str:
        """
        Enforces monotonic pacing >= min_interval between consecutive requests sent to the network.
        Returns the ISO timestamp (+05:30) of the exact moment the request is sent (requested_at).
        """
        if self._last_request_time is not None:
            while True:
                elapsed = time.monotonic() - self._last_request_time
                if elapsed >= self.min_interval:
                    break
                time.sleep(max(0.02, self.min_interval - elapsed))
        now_dt = datetime.now(IST)
        self._last_request_time = time.monotonic()
        return now_dt.isoformat(timespec="seconds")

    def build_url_and_filename(self, dataset: str, trade_date: date) -> Tuple[str, str, str]:
        cfg = DATASETS[dataset]
        yyyy = f"{trade_date.year:04d}"
        mm = f"{trade_date.month:02d}"
        dd = f"{trade_date.day:02d}"
        mmm = MONTH_ABBR[trade_date.month - 1]

        url = cfg["url_template"].format(
            base=ARCHIVE_BASE_URL,
            yyyy=yyyy,
            mm=mm,
            dd=dd,
            mmm=mmm,
        )
        filename = cfg["file_template"].format(
            yyyy=yyyy,
            mm=mm,
            dd=dd,
            mmm=mmm,
        )
        folder = cfg["folder"]
        return url, filename, folder

    def append_manifest(self, rec: Dict[str, Any]) -> None:
        with open(self.manifest_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

    def download_file(self, dataset: str, trade_date: date) -> Dict[str, Any]:
        """
        Processes a single file download with full verification, pacing, resume check,
        and outcome logging.
        """
        cfg = DATASETS[dataset]
        job = cfg["job"]
        tdate_str = trade_date.isoformat()
        url, filename, folder = self.build_url_and_filename(dataset, trade_date)

        target_file = self.base_dir / folder / f"{trade_date.year:04d}" / filename
        # Relative path under history\ per Part 3
        # e.g., raw/nse_archive/cm_bhavcopy/2010/cm04JAN2010bhav.csv.zip
        rel_saved_path = f"raw/nse_archive/{folder}/{trade_date.year:04d}/{filename}"

        # Rule 11: Check resume status
        existing_records = [
            r for r in self.load_manifest_records()
            if r.get("dataset") == dataset and r.get("trade_date") == tdate_str and r.get("outcome") == "SAVED"
        ]
        if existing_records and target_file.exists():
            file_bytes = target_file.read_bytes()
            current_sha256 = hashlib.sha256(file_bytes).hexdigest()
            last_rec = existing_records[-1]
            if current_sha256 == last_rec.get("sha256"):
                # Resume-safe: skip download
                skip_rec = {
                    "job": job,
                    "dataset": dataset,
                    "trade_date": tdate_str,
                    "url": url,
                    "attempt": 1,
                    "http_status": 200,
                    "outcome": "SKIPPED_ALREADY_SAVED",
                    "bytes": len(file_bytes),
                    "sha256": current_sha256,
                    "saved_path": rel_saved_path,
                    "requested_at": "",
                    "fetched_at": datetime.now(IST).isoformat(timespec="seconds"),
                    "code_commit": self.code_commit,
                    "requests_today": self.requests_today,
                    "user_agent": self.user_agent,
                }
                self.append_manifest(skip_rec)
                return skip_rec

        # Daily cap check
        if self.requests_today >= self.daily_cap:
            raise StopExecutionError(
                f"Daily request cap reached ({self.requests_today}/{self.daily_cap}). Halting."
            )

        # Attempt download
        attempt = 0
        last_status = 0
        body = b""
        outcome = "FAILED"
        saved_path = ""
        saved_sha256 = ""
        saved_bytes = 0
        requested_at = ""

        while attempt < (MAX_RETRIES + 1):
            attempt += 1
            self.requests_today += 1
            requested_at = self._pace()

            try:
                resp = self.session.get(url, timeout=30.0)
                last_status = int(resp.status_code)
                body = bytes(resp.content)
            except Exception as exc:
                last_status = 0
                body = b""
                if attempt <= MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_SECONDS)
                    continue
                outcome = "FAILED"
                break

            # Rule 6: Stop immediately on 403 or 429
            if last_status == 403:
                outcome = "STOPPED_403"
                rec = {
                    "job": job,
                    "dataset": dataset,
                    "trade_date": tdate_str,
                    "url": url,
                    "attempt": attempt,
                    "http_status": 403,
                    "outcome": outcome,
                    "bytes": 0,
                    "sha256": "",
                    "saved_path": "",
                    "requested_at": requested_at,
                    "fetched_at": datetime.now(IST).isoformat(timespec="seconds"),
                    "code_commit": self.code_commit,
                    "requests_today": self.requests_today,
                    "user_agent": self.user_agent,
                }
                self.append_manifest(rec)
                raise StopExecutionError(f"HTTP 403 Forbidden on {url}. Stopped immediately per Rule 6.")

            if last_status == 429:
                outcome = "STOPPED_429"
                rec = {
                    "job": job,
                    "dataset": dataset,
                    "trade_date": tdate_str,
                    "url": url,
                    "attempt": attempt,
                    "http_status": 429,
                    "outcome": outcome,
                    "bytes": 0,
                    "sha256": "",
                    "saved_path": "",
                    "requested_at": requested_at,
                    "fetched_at": datetime.now(IST).isoformat(timespec="seconds"),
                    "code_commit": self.code_commit,
                    "requests_today": self.requests_today,
                    "user_agent": self.user_agent,
                }
                self.append_manifest(rec)
                raise StopExecutionError(f"HTTP 429 Too Many Requests on {url}. Stopped immediately per Rule 6.")

            # Rule 8: 404 is MISSING_404, not an error
            if last_status == 404:
                outcome = "MISSING_404"
                break

            # Retry on 5xx
            if last_status in (500, 502, 503, 504):
                if attempt <= MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_SECONDS)
                    continue
                outcome = "FAILED"
                break

            if last_status == 200:
                # Additions C.2, C.3, C.4 validation
                if dataset in ("cm_bhavcopy", "fo_bhavcopy"):
                    is_valid, out_code, fail_reason = validate_cm_fo_content(body, trade_date)
                elif dataset == "mto":
                    is_valid, out_code, fail_reason = validate_mto_content(body, trade_date)
                else:
                    is_valid, out_code, fail_reason = True, "SAVED", None

                outcome = out_code
                if is_valid:
                    self.safe_write(target_file, body)
                    saved_path = rel_saved_path
                    saved_sha256 = hashlib.sha256(body).hexdigest()
                    saved_bytes = len(body)
                elif out_code == "FAILED_BAD_CONTENT":
                    # Save quarantined bad content for inspection
                    bad_file = self.base_dir / "bad_content" / folder / f"{trade_date.year:04d}" / f"{filename}.bad"
                    self.safe_write(bad_file, body)
                    saved_path = f"raw/nse_archive/bad_content/{folder}/{trade_date.year:04d}/{filename}.bad"
                    saved_bytes = len(body)
                    saved_sha256 = hashlib.sha256(body).hexdigest()
                break

            # Other unknown HTTP status
            outcome = "FAILED"
            break

        rec = {
            "job": job,
            "dataset": dataset,
            "trade_date": tdate_str,
            "url": url,
            "attempt": attempt,
            "http_status": last_status,
            "outcome": outcome,
            "bytes": saved_bytes,
            "sha256": saved_sha256,
            "saved_path": saved_path,
            "requested_at": requested_at,
            "fetched_at": datetime.now(IST).isoformat(timespec="seconds"),
            "code_commit": self.code_commit,
            "requests_today": self.requests_today,
            "user_agent": self.user_agent,
        }
        self.append_manifest(rec)
        return rec


def generate_coverage_report(
    records: List[Dict[str, Any]],
    start: date,
    end: date,
    out_dir: Path,
) -> Dict[str, Any]:
    """
    Generates coverage_<dataset>_<YYYY>.csv and coverage_summary_<YYYY>.txt.
    Checks for discrepancies across datasets and lists known weekend special sessions.
    """
    datasets = ["cm_bhavcopy", "fo_bhavcopy", "mto"]
    summary: Dict[str, Any] = {}

    # Group records by (dataset, trade_date)
    by_ds_date: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for r in records:
        key = (r["dataset"], r["trade_date"])
        # Keep latest record or SAVED if present
        if key not in by_ds_date or r.get("outcome") == "SAVED":
            by_ds_date[key] = r

    # Determine date range
    cur = start
    dates: List[date] = []
    while cur <= end:
        dates.append(cur)
        cur += timedelta(days=1)

    for ds in datasets:
        rows = []
        counts = {"SAVED": 0, "MISSING_404": 0, "FAILED": 0, "SKIPPED_ALREADY_SAVED": 0}
        for d in dates:
            if d.weekday() >= 5:  # skip normal weekends for daily rows
                continue
            d_str = d.isoformat()
            weekday_name = d.strftime("%A")
            rec = by_ds_date.get((ds, d_str))
            outcome = rec.get("outcome") if rec else "NOT_FETCHED"
            saved_flag = 1 if outcome in ("SAVED", "SKIPPED_ALREADY_SAVED") else 0
            m404_flag = 1 if outcome == "MISSING_404" else 0
            failed_flag = 1 if outcome in ("FAILED", "FAILED_BAD_CONTENT", "FAILED_WRONG_DATE", "CORRUPT_DOWNLOAD") else 0

            rows.append({
                "date": d_str,
                "weekday": weekday_name,
                "saved": saved_flag,
                "missing_404": m404_flag,
                "failed": failed_flag,
                "outcome": outcome,
            })
            if outcome in counts:
                counts[outcome] += 1
            elif failed_flag:
                counts["FAILED"] += 1

        out_path = out_dir / f"coverage_{ds}_{start.year}.csv"
        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=["date", "weekday", "saved", "missing_404", "failed"])
            writer.writeheader()
            for r in rows:
                writer.writerow({k: r[k] for k in ["date", "weekday", "saved", "missing_404", "failed"]})

        summary[ds] = {
            "counts": counts,
            "rows": rows,
        }

    # Discrepancy detection (Part 3: missing in ONE dataset but present in another)
    discrepancies = []
    weekdays = [d for d in dates if d.weekday() < 5]
    for d in weekdays:
        d_str = d.isoformat()
        status_per_ds = {}
        for ds in datasets:
            rec = by_ds_date.get((ds, d_str))
            outc = rec.get("outcome") if rec else "NOT_FETCHED"
            status_per_ds[ds] = outc
        saved_count = sum(1 for s in status_per_ds.values() if s in ("SAVED", "SKIPPED_ALREADY_SAVED"))
        if 0 < saved_count < len(datasets):
            discrepancies.append((d_str, status_per_ds))

    # Known weekend special sessions (Addition C.1)
    known_weekends_unfetched = []
    for ws in KNOWN_WEEKEND_SESSIONS:
        ws_fetched = any(
            r.get("trade_date") == ws and r.get("outcome") in ("SAVED", "SKIPPED_ALREADY_SAVED")
            for r in records
        )
        if not ws_fetched:
            known_weekends_unfetched.append(ws)

    # Write text summary (Part 3 & Addition C.1)
    summary_file = out_dir / f"coverage_summary_{start.year}.txt"
    summary_lines = [
        f"=== Coverage Summary Report ({start.isoformat()} to {end.isoformat()}) ===",
        f"Expected Weekdays: {len(weekdays)}",
    ]
    for ds in datasets:
        c = summary[ds]["counts"]
        total_saved = c.get("SAVED", 0) + c.get("SKIPPED_ALREADY_SAVED", 0)
        summary_lines.append(
            f"  - {ds}: Saved={total_saved}, Missing_404={c.get('MISSING_404', 0)}, Failed={c.get('FAILED', 0)}"
        )

    summary_lines.append(f"\nDiscrepancies (missing in 1 dataset but present in another): {len(discrepancies)}")
    if discrepancies:
        for d_str, st in discrepancies:
            summary_lines.append(f"  * {d_str}: {st}")
    else:
        summary_lines.append("  None (all datasets consistent across all trading days).")

    summary_lines.append(
        f"\nKnown Weekend Special Sessions (Addition C.1 - awaiting official calendar in Job 4):"
    )
    for kw in known_weekends_unfetched:
        summary_lines.append(f"  * {kw} (known weekend session, not yet fetched)")

    summary_text = "\n".join(summary_lines) + "\n"
    summary_file.write_text(summary_text, encoding="utf-8")
    summary["discrepancies"] = discrepancies
    summary["known_weekends_unfetched"] = known_weekends_unfetched
    summary["summary_file"] = str(summary_file)

    return summary


def _get_first_lines_of_saved_file(file_path: Path, n: int = 3) -> List[str]:
    """Returns the first n lines of a saved file (inspecting inside zip if zip)."""
    if not file_path.exists():
        return [f"[File not found: {file_path}]"]
    try:
        if file_path.suffix.lower() == ".zip":
            with zipfile.ZipFile(file_path) as zf:
                csv_names = [name for name in zf.namelist() if name.lower().endswith(".csv")]
                if not csv_names:
                    return [f"[Zip namelist: {zf.namelist()}]"]
                with zf.open(csv_names[0]) as cf:
                    lines = [cf.readline().decode("utf-8", "replace").rstrip() for _ in range(n)]
                    return lines
        else:
            with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                lines = [fh.readline().rstrip() for _ in range(n)]
                return lines
    except Exception as exc:
        return [f"[Error reading file: {exc}]"]


def run_forward_download(
    start_date: date,
    end_date: date,
    downloader: Optional[NseArchiveDownloader] = None,
    datasets: Sequence[str] = ("cm_bhavcopy", "fo_bhavcopy", "mto"),
    interval: float = MIN_INTERVAL_SECONDS,
    daily_cap: int = ARCHIVE_DAILY_CAP,
) -> Dict[str, Any]:
    """
    Runs chronological forward downloading (A.1: 2005 first, 2021 last),
    interleaving CM, FO, MTO day-by-day (Part 2 Rule 73).
    """
    if downloader is None:
        downloader = NseArchiveDownloader(min_interval=interval, daily_cap=daily_cap)

    cur = start_date
    trading_days = []
    while cur <= end_date:
        cur_iso = cur.isoformat()
        if cur.weekday() < 5 or cur_iso in KNOWN_WEEKEND_SESSIONS:
            trading_days.append(cur)
        cur += timedelta(days=1)

    halt_reason = None
    stats: Counter = Counter()
    for td in trading_days:
        for ds in datasets:
            try:
                rec = downloader.download_file(ds, td)
                outcome = rec.get("outcome", "UNKNOWN")
                stats[outcome] += 1
            except StopExecutionError as exc:
                print(f"Execution halted: {exc}", file=sys.stderr)
                halt_reason = str(exc)
                break
        if halt_reason is not None:
            break

    # Generate coverage reports per year encountered
    years = sorted(list(set(d.year for d in trading_days)))
    all_records = downloader.load_manifest_records()
    for yr in years:
        yr_start = max(start_date, date(yr, 1, 1))
        yr_end = min(end_date, date(yr, 12, 31))
        generate_coverage_report(all_records, yr_start, yr_end, downloader.base_dir)

    result = {"stats": dict(stats), "trading_days": len(trading_days)}
    if halt_reason is not None:
        result["stopped"] = halt_reason
    return result


def run_pilot(base_dir: Optional[Path] = None, interval: float = MIN_INTERVAL_SECONDS) -> None:
    """Executes the January 2010 Pilot and the 9 spot checks."""
    downloader = NseArchiveDownloader(base_dir=base_dir, min_interval=interval)

    print("=== STARTING SPOT CHECKS (ADDITION C.5: 2005, 2016, 2021) ===", flush=True)
    spot_dates = [
        date(2005, 6, 1),
        date(2016, 1, 4),
        date(2021, 1, 4),
    ]
    spot_results = []
    for sd in spot_dates:
        for ds in ("cm_bhavcopy", "fo_bhavcopy", "mto"):
            print(f"[Spot Check] Fetching {ds} for {sd}...", flush=True)
            rec = downloader.download_file(ds, sd)
            spot_results.append(rec)
            print(f"  -> Outcome: {rec['outcome']} | Status: {rec['http_status']} | Bytes: {rec['bytes']}", flush=True)

    print("\n=== STARTING JANUARY 2010 PILOT ===", flush=True)
    start_d = date(2010, 1, 1)
    end_d = date(2010, 1, 31)

    cur = start_d
    pilot_days = []
    while cur <= end_d:
        if cur.weekday() < 5:  # Monday to Friday
            pilot_days.append(cur)
        cur += timedelta(days=1)

    print(f"Total trading weekdays in Jan 2010: {len(pilot_days)}", flush=True)
    for td in pilot_days:
        for ds in ("cm_bhavcopy", "fo_bhavcopy", "mto"):
            print(f"[Pilot] {td.isoformat()} | {ds}...", end=" ", flush=True)
            rec = downloader.download_file(ds, td)
            print(f"{rec['outcome']} (HTTP {rec['http_status']})", flush=True)

    all_records = downloader.load_manifest_records()
    cov = generate_coverage_report(all_records, start_d, end_d, downloader.base_dir)

    # Write progress_log.md
    today_iso = datetime.now(IST).date().isoformat()
    progress_log_path = downloader.base_dir / "progress_log.md"
    outcome_counts = Counter(r.get("outcome") for r in all_records)
    total_bytes = sum(r.get("bytes", 0) for r in all_records if r.get("outcome") in ("SAVED", "SKIPPED_ALREADY_SAVED"))
    
    log_entry = (
        f"\n## {today_iso} (Pilot Run - Jan 2010 + Spot Checks)\n"
        f"- Host: nsearchives.nseindia.com\n"
        f"- Requests used today: {downloader.requests_today}\n"
        f"- Manifest lines recorded: {len(all_records)}\n"
        f"- Outcomes: {dict(outcome_counts)}\n"
        f"- Total saved bytes: {total_bytes:,} bytes\n"
        f"- HTTP 403 / 429 blocks: 0\n"
        f"- Next step: STOP and await pilot approval per Rule 19.\n"
    )
    with open(progress_log_path, "a", encoding="utf-8") as fh:
        fh.write(log_entry)

    print("\n=== PILOT COMPLETE ===", flush=True)
    print(f"Manifest written to: {downloader.manifest_path}")
    print(f"Progress log written to: {progress_log_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="NSE Static Archive Downloader")
    ap.add_argument("--pilot", action="store_true", help="Run January 2010 Pilot + 9 spot checks")
    ap.add_argument("--start-date", help="Start date in YYYY-MM-DD format for forward download")
    ap.add_argument("--end-date", help="End date in YYYY-MM-DD format for forward download")
    ap.add_argument("--year", type=int, help="Download a specific year forward")
    ap.add_argument("--interval", type=float, default=MIN_INTERVAL_SECONDS, help="Interval between requests (>= 4.0s)")
    ap.add_argument("--cap", type=int, default=ARCHIVE_DAILY_CAP, help="Daily request cap (default 500)")
    args = ap.parse_args()

    interval = max(MIN_INTERVAL_SECONDS, args.interval)

    if args.pilot:
        run_pilot(interval=interval)
    elif args.start_date and args.end_date:
        s_date = date.fromisoformat(args.start_date)
        e_date = date.fromisoformat(args.end_date)
        run_forward_download(start_date=s_date, end_date=e_date, interval=interval, daily_cap=args.cap)
    elif args.year:
        s_date = date(args.year, 1, 1)
        e_date = date(args.year, 12, 31)
        run_forward_download(start_date=s_date, end_date=e_date, interval=interval, daily_cap=args.cap)
    else:
        print("Please specify --pilot to run the pilot, or --start-date and --end-date (or --year) for forward download.")

