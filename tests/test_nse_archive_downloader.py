"""
Unit tests for scripts/download_nse_archive.py
Covers mandatory test gates:
- 403 stops run immediately (STOPPED_403)
- 404 recorded as missing (MISSING_404), not error
- Corrupt zip with damaged CRC detected via zipfile.ZipFile.testzip() (CORRUPT_DOWNLOAD)
- Fake success / invalid content detected (FAILED_BAD_CONTENT)
- Wrong date inside file detected (FAILED_WRONG_DATE)
- Resume skips verified files (SKIPPED_ALREADY_SAVED)
- Daily cap (500) enforced
- Boundaries: no writes outside shared/track2_liquid/history/raw/nse_archive/
- User-Agent recorded in manifest lines
"""
import io
import json
import os
import tempfile
import zipfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.download_nse_archive import (
    ARCHIVE_DAILY_CAP,
    DATASETS,
    NseArchiveDownloader,
    StopExecutionError,
    validate_cm_fo_content,
    validate_mto_content,
)


def _create_valid_cm_zip(trade_date: date) -> bytes:
    buf = io.BytesIO()
    date_str = trade_date.strftime("%d-%b-%Y").upper()
    csv_content = (
        f"SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN\n"
        f"SBIN,EQ,200.0,205.0,199.0,204.0,204.0,198.0,10000,2040000,{date_str},500,INE062A01020\n"
    )
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"cm{trade_date.strftime('%d%b%Y').upper()}bhav.csv", csv_content)
    return buf.getvalue()


def _create_valid_mto(trade_date: date) -> bytes:
    date_str = trade_date.strftime("%d-%b-%Y").upper()
    date_num = trade_date.strftime("%d%m%Y")
    content = (
        f"Security Wise Delivery Position - Compulsory Rolling Settlement\n"
        f"10,MTO,{date_num},12345678,0001000\n"
        f"Trade Date <{date_str}>,Settlement Type <N>,Settlement No <2010001>,Settlement Date <06-JAN-2010>\n"
        f"Record Type,Sr No,Name of Security,Quantity Traded,Deliverable Quantity,% of Traded Quantity\n"
        f"20,1,SBIN,10000,5000,50.00\n"
    )
    return content.encode("utf-8")


def _create_corrupt_crc_zip(trade_date: date) -> bytes:
    import struct
    valid = _create_valid_cm_zip(trade_date)
    zf = zipfile.ZipFile(io.BytesIO(valid))
    crc = zf.infolist()[0].CRC
    crc_bytes = struct.pack("<I", crc)
    corrupted_crc = struct.pack("<I", crc ^ 0x12345678)
    # Replace CRC bytes in header so zip is structurally valid but CRC fails
    return valid.replace(crc_bytes, corrupted_crc)


@pytest.fixture
def temp_archive_dir(tmp_path):
    archive_dir = tmp_path / "shared" / "track2_liquid" / "history" / "raw" / "nse_archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    return archive_dir


def test_403_stops_run_immediately(temp_archive_dir):
    downloader = NseArchiveDownloader(base_dir=temp_archive_dir, min_interval=0.0)
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.content = b"Forbidden"

    with patch.object(downloader.session, "get", return_value=mock_resp):
        with pytest.raises(StopExecutionError, match="403"):
            downloader.download_file("cm_bhavcopy", date(2010, 1, 4))

    manifest_records = downloader.load_manifest_records()
    assert len(manifest_records) == 1
    assert manifest_records[0]["outcome"] == "STOPPED_403"
    assert manifest_records[0]["http_status"] == 403


def test_404_recorded_as_missing(temp_archive_dir):
    downloader = NseArchiveDownloader(base_dir=temp_archive_dir, min_interval=0.0)
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.content = b"Not Found"

    with patch.object(downloader.session, "get", return_value=mock_resp):
        rec = downloader.download_file("cm_bhavcopy", date(2010, 1, 1))

    assert rec["outcome"] == "MISSING_404"
    assert rec["http_status"] == 404
    manifest_records = downloader.load_manifest_records()
    assert len(manifest_records) == 1
    assert manifest_records[0]["outcome"] == "MISSING_404"


def test_corrupt_zip_detected_via_testzip(temp_archive_dir):
    corrupt_bytes = _create_corrupt_crc_zip(date(2010, 1, 4))
    downloader = NseArchiveDownloader(base_dir=temp_archive_dir, min_interval=0.0)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = corrupt_bytes

    with patch.object(downloader.session, "get", return_value=mock_resp):
        rec = downloader.download_file("cm_bhavcopy", date(2010, 1, 4))

    assert rec["outcome"] == "CORRUPT_DOWNLOAD"
    assert rec["saved_path"] == ""


def test_fake_success_invalid_content_detected(temp_archive_dir):
    downloader = NseArchiveDownloader(base_dir=temp_archive_dir, min_interval=0.0)

    # CM fake success: HTML error page
    mock_html_resp = MagicMock()
    mock_html_resp.status_code = 200
    mock_html_resp.content = b"<html><body>The resource cannot be found</body></html>"

    with patch.object(downloader.session, "get", return_value=mock_html_resp):
        rec = downloader.download_file("cm_bhavcopy", date(2010, 1, 4))
    assert rec["outcome"] == "FAILED_BAD_CONTENT"
    # Bad content is quarantined under bad_content/ for inspection, not saved as bhavcopy
    assert "bad_content" in rec["saved_path"]

    # MTO fake success: doesn't start with "Security Wise Delivery Position"
    mock_bad_mto = MagicMock()
    mock_bad_mto.status_code = 200
    mock_bad_mto.content = b"Invalid header data\n10,MTO,04012010"

    with patch.object(downloader.session, "get", return_value=mock_bad_mto):
        rec_mto = downloader.download_file("mto", date(2010, 1, 4))
    assert rec_mto["outcome"] == "FAILED_BAD_CONTENT"
    assert "bad_content" in rec_mto["saved_path"]


def test_wrong_date_inside_file_detected(temp_archive_dir):
    downloader = NseArchiveDownloader(base_dir=temp_archive_dir, min_interval=0.0)

    # CM bhavcopy contains date 2010-01-05 when 2010-01-04 was requested
    cm_bytes_wrong_date = _create_valid_cm_zip(date(2010, 1, 5))
    mock_cm_resp = MagicMock()
    mock_cm_resp.status_code = 200
    mock_cm_resp.content = cm_bytes_wrong_date

    with patch.object(downloader.session, "get", return_value=mock_cm_resp):
        rec = downloader.download_file("cm_bhavcopy", date(2010, 1, 4))
    assert rec["outcome"] == "FAILED_WRONG_DATE"

    # MTO file contains date 2010-01-05 when 2010-01-04 was requested
    mto_bytes_wrong_date = _create_valid_mto(date(2010, 1, 5))
    mock_mto_resp = MagicMock()
    mock_mto_resp.status_code = 200
    mock_mto_resp.content = mto_bytes_wrong_date

    with patch.object(downloader.session, "get", return_value=mock_mto_resp):
        rec_mto = downloader.download_file("mto", date(2010, 1, 4))
    assert rec_mto["outcome"] == "FAILED_WRONG_DATE"


def test_resume_skips_verified_files(temp_archive_dir):
    downloader = NseArchiveDownloader(base_dir=temp_archive_dir, min_interval=0.0)
    cm_bytes = _create_valid_cm_zip(date(2010, 1, 4))

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = cm_bytes

    # First fetch: saved
    with patch.object(downloader.session, "get", return_value=mock_resp):
        rec1 = downloader.download_file("cm_bhavcopy", date(2010, 1, 4))
    assert rec1["outcome"] == "SAVED"

    # Second fetch: resume detects saved file and skips download without calling network
    mock_get = MagicMock()
    with patch.object(downloader.session, "get", mock_get):
        rec2 = downloader.download_file("cm_bhavcopy", date(2010, 1, 4))
    assert rec2["outcome"] == "SKIPPED_ALREADY_SAVED"
    assert not mock_get.called


def test_daily_cap_enforced(temp_archive_dir):
    # Set cap to 3 requests
    downloader = NseArchiveDownloader(base_dir=temp_archive_dir, min_interval=0.0, daily_cap=3)
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.content = b"Not Found"

    with patch.object(downloader.session, "get", return_value=mock_resp):
        downloader.download_file("cm_bhavcopy", date(2010, 1, 1))
        downloader.download_file("cm_bhavcopy", date(2010, 1, 2))
        downloader.download_file("cm_bhavcopy", date(2010, 1, 3))
        with pytest.raises(StopExecutionError, match="Daily request cap reached"):
            downloader.download_file("cm_bhavcopy", date(2010, 1, 4))

    assert downloader.requests_today == 3


def test_boundaries_no_writes_outside_allowed_folder(temp_archive_dir, tmp_path):
    downloader = NseArchiveDownloader(base_dir=temp_archive_dir, min_interval=0.0)
    outside_path = tmp_path / "other_dir" / "hacked.txt"

    with pytest.raises(PermissionError, match="Write boundary violation"):
        downloader.safe_write(outside_path, b"malicious")


def test_manifest_schema_and_user_agent(temp_archive_dir):
    downloader = NseArchiveDownloader(
        base_dir=temp_archive_dir,
        min_interval=0.0,
        user_agent="CustomTestUA/1.0",
    )
    cm_bytes = _create_valid_cm_zip(date(2010, 1, 4))
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = cm_bytes

    with patch.object(downloader.session, "get", return_value=mock_resp):
        rec = downloader.download_file("cm_bhavcopy", date(2010, 1, 4))

    required_keys = {
        "job",
        "dataset",
        "trade_date",
        "url",
        "attempt",
        "http_status",
        "outcome",
        "bytes",
        "sha256",
        "saved_path",
        "fetched_at",
        "code_commit",
        "requests_today",
        "user_agent",
    }
    assert required_keys.issubset(set(rec.keys()))
    assert rec["user_agent"] == "CustomTestUA/1.0"
    assert rec["job"] == "JOB1"
    assert rec["dataset"] == "cm_bhavcopy"


def test_429_stops_run_immediately(temp_archive_dir):
    """Rule 6: HTTP 429 must stop all requests immediately without retry."""
    downloader = NseArchiveDownloader(base_dir=temp_archive_dir, min_interval=0.0)
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.content = b"Too Many Requests"

    with patch.object(downloader.session, "get", return_value=mock_resp):
        with pytest.raises(StopExecutionError, match="429"):
            downloader.download_file("cm_bhavcopy", date(2010, 1, 4))

    manifest_records = downloader.load_manifest_records()
    assert len(manifest_records) == 1
    assert manifest_records[0]["outcome"] == "STOPPED_429"
    assert manifest_records[0]["http_status"] == 429


def test_timeout_and_5xx_retries(temp_archive_dir):
    """Rule 8: Retries at most 2 times for timeouts and 5xx, then succeeds or fails."""
    downloader = NseArchiveDownloader(base_dir=temp_archive_dir, min_interval=0.0)
    cm_bytes = _create_valid_cm_zip(date(2010, 1, 4))

    resp_500 = MagicMock()
    resp_500.status_code = 500
    resp_500.content = b"Internal Server Error"

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.content = cm_bytes

    # Fails first attempt with 500, succeeds on 2nd attempt with 200
    with patch.object(downloader.session, "get", side_effect=[resp_500, resp_200]):
        with patch("time.sleep", return_value=None):
            rec = downloader.download_file("cm_bhavcopy", date(2010, 1, 4))

    assert rec["outcome"] == "SAVED"
    assert rec["attempt"] == 2
    assert rec["http_status"] == 200


def test_count_today_requests_accumulates_all_attempts(temp_archive_dir):
    """Rule 5: Cap must count every attempt including retries when loading from manifest."""
    manifest_path = temp_archive_dir / "manifest.jsonl"
    rec1 = {
        "job": "JOB1",
        "dataset": "cm_bhavcopy",
        "trade_date": "2010-01-04",
        "attempt": 3,
        "outcome": "SAVED",
        "fetched_at": "2026-09-26T10:00:00+05:30",
    }
    rec2 = {
        "job": "JOB1",
        "dataset": "fo_bhavcopy",
        "trade_date": "2010-01-04",
        "attempt": 2,
        "outcome": "MISSING_404",
        "fetched_at": "2026-09-26T10:05:00+05:30",
    }
    manifest_path.write_text(json.dumps(rec1) + "\n" + json.dumps(rec2) + "\n", encoding="utf-8")

    downloader = NseArchiveDownloader(base_dir=temp_archive_dir, min_interval=0.0)
    # rec1 took 3 attempts, rec2 took 2 attempts -> total 5 attempts today
    assert downloader.requests_today == 5


def test_coverage_report_generates_summary_and_tracks_weekends(temp_archive_dir):
    """Part 3 & Addition C.1: Generates summary file, checks discrepancies, lists untracked weekends."""
    from scripts.download_nse_archive import generate_coverage_report, KNOWN_WEEKEND_SESSIONS

    assert len(KNOWN_WEEKEND_SESSIONS) >= 2
    assert "2015-02-28" in KNOWN_WEEKEND_SESSIONS
    assert "2020-02-01" in KNOWN_WEEKEND_SESSIONS

    # Create dummy records: date 2010-01-04 saved for CM and FO, but missing for MTO (discrepancy)
    records = [
        {"dataset": "cm_bhavcopy", "trade_date": "2010-01-04", "outcome": "SAVED"},
        {"dataset": "fo_bhavcopy", "trade_date": "2010-01-04", "outcome": "SAVED"},
        {"dataset": "mto", "trade_date": "2010-01-04", "outcome": "MISSING_404"},
    ]

    summary = generate_coverage_report(
        records=records,
        start=date(2010, 1, 1),
        end=date(2010, 1, 5),
        out_dir=temp_archive_dir,
    )

    summary_file = temp_archive_dir / "coverage_summary_2010.txt"
    assert summary_file.exists()
    summary_text = summary_file.read_text(encoding="utf-8")
    assert "Discrepancies" in summary_text
    assert "2010-01-04" in summary_text
    assert "Known Weekend Special Sessions" in summary_text


def test_forward_download_interleaved_chronological(temp_archive_dir):
    """Item A.1: Forward download iterates chronologically from start to end date, interleaving CM, FO, MTO."""
    from scripts.download_nse_archive import run_forward_download

    downloader = NseArchiveDownloader(base_dir=temp_archive_dir, min_interval=0.0)
    called_sequence = []

    def mock_download(dataset, trade_date):
        called_sequence.append((trade_date.isoformat(), dataset))
        return {"outcome": "SAVED", "trade_date": trade_date.isoformat(), "dataset": dataset}

    with patch.object(downloader, "download_file", side_effect=mock_download):
        run_forward_download(
            start_date=date(2005, 1, 3),
            end_date=date(2005, 1, 4),
            downloader=downloader,
        )

    expected = [
        ("2005-01-03", "cm_bhavcopy"),
        ("2005-01-03", "fo_bhavcopy"),
        ("2005-01-03", "mto"),
        ("2005-01-04", "cm_bhavcopy"),
        ("2005-01-04", "fo_bhavcopy"),
        ("2005-01-04", "mto"),
    ]
    assert called_sequence == expected

