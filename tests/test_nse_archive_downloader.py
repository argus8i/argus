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
