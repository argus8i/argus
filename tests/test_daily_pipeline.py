"""
tests/test_daily_pipeline.py
============================
Test suite for JOB 0: Daily after-close pipeline (scripts/daily_pipeline.py).
Tests written FIRST per Rule 13 and Tri-Agent Operational Standards.

Verifies:
1. 403 / 429 stops the run immediately without retry (Rule 6).
2. 404 is recorded as MISSING_404, not an error (Rule 8).
3. Corrupt zip CRC is detected via testzip() (Addition C.4).
4. Fake success (HTML / invalid format) is rejected (Addition C.2).
5. Date mismatch inside file is detected across all 4 datasets (Addition C.3).
6. Resume skips already saved files with matching SHA-256 (Rule 11).
7. Daily request cap is enforced (Rule 5).
8. Write boundary is strictly enforced (Rule 12).
9. Honest User-Agent is recorded in manifest (Rule 7, Addition C.6).
10. End-to-end happy path fetches all 4 daily files (CM, FO, MTO, F&O Ban for next session).
11. Next session date calculation correctly skips weekends (Friday -> Monday).
"""
from __future__ import annotations

import csv
import io
import json
import struct
import tempfile
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
import requests

from scripts.daily_pipeline import (
    DailyPipelineDownloader,
    DailyRequestItem,
    DatasetConfig,
    calculate_next_session_date,
    JOB0_DATASETS,
    MIN_INTERVAL_SECONDS,
    ARCHIVE_DAILY_CAP,
    ARCHIVE_BASE_URL,
    DEFAULT_USER_AGENT,
)


def _make_dummy_zip(filename: str, content: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(filename, content)
    return buf.getvalue()


def _make_valid_cm_bhav_zip(trade_date: date, udiff: bool = True) -> bytes:
    if udiff:
        # Modern UDiFF format
        date_str = trade_date.isoformat()
        csv_content = f"TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd\n{date_str},{date_str},CM,NSE,STK,1234,INE002A01018,RELIANCE,EQ,2900.0,2950.0,2890.0,2940.0,2940.0,2880.0,1000000,2940000000,50000\n".encode("utf-8")
        filename = f"BhavCopy_NSE_CM_0_0_0_{trade_date.strftime('%Y%m%d')}_F_0000.csv"
    else:
        # Legacy format
        month_abbr = trade_date.strftime("%b").upper()
        date_str = trade_date.strftime("%d-%b-%Y").upper()
        csv_content = f"SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN\nRELIANCE,EQ,1000.0,1050.0,990.0,1040.0,1040.0,995.0,50000,52000000,{date_str},1500,INE002A01018\n".encode("utf-8")
        filename = f"cm{trade_date.strftime('%d%b%Y').upper()}bhav.csv"
    return _make_dummy_zip(filename, csv_content)


def _make_valid_fo_bhav_zip(trade_date: date, udiff: bool = True) -> bytes:
    if udiff:
        # Modern UDiFF format
        date_str = trade_date.isoformat()
        csv_content = f"TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd\n{date_str},{date_str},FO,NSE,FUT,5678,,NIFTY,XX,24000.0,24200.0,23950.0,24150.0,24150.0,23980.0,500000,12000000000,20000\n".encode("utf-8")
        filename = f"BhavCopy_NSE_FO_0_0_0_{trade_date.strftime('%Y%m%d')}_F_0000.csv"
    else:
        # Legacy format
        month_abbr = trade_date.strftime("%b").upper()
        date_str = trade_date.strftime("%d-%b-%Y").upper()
        csv_content = f"INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP\nFUTIDX,NIFTY,28-OCT-2021,0,XX,18000.0,18100.0,17950.0,18050.0,18050.0,10000,90000.0,100000,500,{date_str}\n".encode("utf-8")
        filename = f"fo{trade_date.strftime('%d%b%Y').upper()}bhav.csv"
    return _make_dummy_zip(filename, csv_content)


def _make_valid_mto(trade_date: date) -> bytes:
    date_str = trade_date.strftime("%d-%b-%Y").upper()
    lines = [
        "Security Wise Delivery Position - Compulsory Rolling Settlement",
        "Record Type,Sr No,Name of Security,Segment Type,Quantity Traded,Deliverable Quantity,% of Deliverable Quantity to Traded Quantity",
        f"Trade Date <{date_str}>,Settlement Type <N>,Settlement Number <12345>",
        "20,1,RELIANCE,EQ,1000000,500000,50.00",
    ]
    return "\n".join(lines).encode("utf-8")


def _make_valid_secban(ban_date: date, symbols: Optional[List[str]] = None) -> bytes:
    date_str = ban_date.strftime("%d-%b-%Y").upper()
    lines = [f"Securities in Ban For Trade Date {date_str}:"]
    syms = symbols if symbols is not None else ["KAYNES", "SAIL"]
    for i, sym in enumerate(syms, 1):
        lines.append(f"{i},{sym}")
    return "\n".join(lines).encode("utf-8")


def _corrupt_zip_crc(valid_zip: bytes) -> bytes:
    data = bytearray(valid_zip)
    idx = data.find(b"PK\x03\x04")
    assert idx != -1
    crc_offset = idx + 14
    old_crc = struct.unpack("<I", data[crc_offset : crc_offset + 4])[0]
    new_crc = old_crc ^ 0x12345678
    data[crc_offset : crc_offset + 4] = struct.pack("<I", new_crc)
    cd_idx = data.find(b"PK\x01\x02")
    if cd_idx != -1:
        cd_crc_offset = cd_idx + 16
        data[cd_crc_offset : cd_crc_offset + 4] = struct.pack("<I", new_crc)
    return bytes(data)


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------


def test_job0_weekend_date_calculation():
    """Verify that Friday session calculates next session as Monday, skipping weekend."""
    friday = date(2026, 9, 25)
    monday = calculate_next_session_date(friday)
    assert monday == date(2026, 9, 28)

    thursday = date(2026, 9, 24)
    friday_next = calculate_next_session_date(thursday)
    assert friday_next == date(2026, 9, 25)


def test_job0_403_stops_immediately_no_retry(tmp_path: Path):
    """Rule 6: Any HTTP 403 halts immediately, records STOPPED_403, and does not retry."""
    downloader = DailyPipelineDownloader(root_dir=tmp_path, pause_seconds=0.0)

    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.content = b"Forbidden"

    with patch.object(downloader.session, "get", return_value=mock_resp) as mock_get:
        items = downloader.build_daily_items(trade_date=date(2026, 9, 28))
        stats = downloader.execute_items(items)

        assert stats["STOPPED_403"] == 1
        assert stats["SAVED"] == 0
        assert mock_get.call_count == 1  # Exactly 1 call, halted immediately

    # Check manifest
    manifest_file = tmp_path / "raw" / "nse_archive" / "manifest.jsonl"
    assert manifest_file.exists()
    lines = [json.loads(line) for line in manifest_file.read_text(encoding="utf-8").strip().split("\n")]
    assert len(lines) == 1
    assert lines[0]["http_status"] == 403
    assert lines[0]["outcome"] == "STOPPED_403"
    assert lines[0]["user_agent"] == DEFAULT_USER_AGENT


def test_job0_429_stops_immediately_no_retry(tmp_path: Path):
    """Rule 6: HTTP 429 halts immediately, records STOPPED_429."""
    downloader = DailyPipelineDownloader(root_dir=tmp_path, pause_seconds=0.0)

    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.content = b"Too Many Requests"

    with patch.object(downloader.session, "get", return_value=mock_resp) as mock_get:
        items = downloader.build_daily_items(trade_date=date(2026, 9, 28))
        stats = downloader.execute_items(items)

        assert stats["STOPPED_429"] == 1
        assert mock_get.call_count == 1


def test_job0_404_recorded_as_missing_not_error(tmp_path: Path):
    """Rule 8: 404 is recorded as MISSING_404, not an error; pipeline proceeds to next file."""
    downloader = DailyPipelineDownloader(root_dir=tmp_path, pause_seconds=0.0)

    mock_resp_404 = MagicMock()
    mock_resp_404.status_code = 404
    mock_resp_404.content = b"Not Found"

    with patch.object(downloader.session, "get", return_value=mock_resp_404) as mock_get:
        items = downloader.build_daily_items(trade_date=date(2026, 9, 28))
        stats = downloader.execute_items(items)

        assert stats["MISSING_404"] == 4  # All 4 items returned 404
        assert stats["SAVED"] == 0
        assert mock_get.call_count == 4

    manifest_file = tmp_path / "raw" / "nse_archive" / "manifest.jsonl"
    lines = [json.loads(line) for line in manifest_file.read_text(encoding="utf-8").strip().split("\n")]
    assert len(lines) == 4
    for record in lines:
        assert record["outcome"] == "MISSING_404"
        assert record["http_status"] == 404
        assert record["bytes"] == 0


def test_job0_corrupt_zip_crc_detected(tmp_path: Path):
    """Addition C.4: zipfile testzip() CRC error is detected and logged as CORRUPT_DOWNLOAD."""
    downloader = DailyPipelineDownloader(root_dir=tmp_path, pause_seconds=0.0)
    t_date = date(2026, 9, 28)
    valid_zip = _make_valid_cm_bhav_zip(t_date, udiff=True)
    corrupted_zip = _corrupt_zip_crc(valid_zip)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = corrupted_zip

    with patch.object(downloader.session, "get", return_value=mock_resp):
        items = [downloader.build_daily_items(trade_date=t_date)[0]]  # CM Bhavcopy only
        stats = downloader.execute_items(items)

        assert stats["CORRUPT_DOWNLOAD"] == 1
        assert stats["SAVED"] == 0

    manifest_file = tmp_path / "raw" / "nse_archive" / "manifest.jsonl"
    line = json.loads(manifest_file.read_text(encoding="utf-8").strip())
    assert line["outcome"] == "CORRUPT_DOWNLOAD"
    assert "Corrupt zip" in line.get("error", "")


def test_job0_fake_success_detected(tmp_path: Path):
    """Addition C.2: HTML error page returned with 200 is detected and logged as FAILED_BAD_CONTENT."""
    downloader = DailyPipelineDownloader(root_dir=tmp_path, pause_seconds=0.0)
    t_date = date(2026, 9, 28)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"<html><head><title>Error</title></head><body>File not found</body></html>"

    with patch.object(downloader.session, "get", return_value=mock_resp):
        items = [downloader.build_daily_items(trade_date=t_date)[0]]  # CM Bhavcopy expecting zip
        stats = downloader.execute_items(items)

        assert stats["FAILED_BAD_CONTENT"] == 1
        assert stats["SAVED"] == 0

    manifest_file = tmp_path / "raw" / "nse_archive" / "manifest.jsonl"
    line = json.loads(manifest_file.read_text(encoding="utf-8").strip())
    assert line["outcome"] == "FAILED_BAD_CONTENT"
    assert "bad_content" in line.get("saved_path", "")


def test_job0_date_mismatch_detected(tmp_path: Path):
    """Addition C.3: Date inside file does not match requested date -> FAILED_WRONG_DATE."""
    downloader = DailyPipelineDownloader(root_dir=tmp_path, pause_seconds=0.0)
    t_date = date(2026, 9, 28)
    wrong_date = date(2026, 9, 27)

    # CM bhavcopy with wrong date
    wrong_cm_zip = _make_valid_cm_bhav_zip(wrong_date, udiff=True)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = wrong_cm_zip

    with patch.object(downloader.session, "get", return_value=mock_resp):
        items = [downloader.build_daily_items(trade_date=t_date)[0]]
        stats = downloader.execute_items(items)

        assert stats["FAILED_WRONG_DATE"] == 1
        assert stats["SAVED"] == 0

    # MTO with wrong date
    wrong_mto = _make_valid_mto(wrong_date)
    mock_resp.content = wrong_mto
    with patch.object(downloader.session, "get", return_value=mock_resp):
        items = [downloader.build_daily_items(trade_date=t_date)[2]]  # MTO
        stats = downloader.execute_items(items)
        assert stats["FAILED_WRONG_DATE"] == 1

    # Ban file with wrong date
    wrong_ban = _make_valid_secban(wrong_date)
    mock_resp.content = wrong_ban
    with patch.object(downloader.session, "get", return_value=mock_resp):
        items = [downloader.build_daily_items(trade_date=t_date, next_session_date=date(2026, 9, 29))[3]]  # BAN
        stats = downloader.execute_items(items)
        assert stats["FAILED_WRONG_DATE"] == 1


def test_job0_resume_skips_already_saved(tmp_path: Path):
    """Rule 11: File already in manifest with matching SHA-256 is skipped without network request."""
    downloader = DailyPipelineDownloader(root_dir=tmp_path, pause_seconds=0.0)
    t_date = date(2026, 9, 28)

    # 1. Download valid file
    valid_cm = _make_valid_cm_bhav_zip(t_date, udiff=True)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = valid_cm

    with patch.object(downloader.session, "get", return_value=mock_resp) as mock_get:
        items = [downloader.build_daily_items(trade_date=t_date)[0]]
        stats = downloader.execute_items(items)
        assert stats["SAVED"] == 1
        assert mock_get.call_count == 1

    # 2. Re-run identical item
    with patch.object(downloader.session, "get", return_value=mock_resp) as mock_get_second:
        items = [downloader.build_daily_items(trade_date=t_date)[0]]
        stats2 = downloader.execute_items(items)
        assert stats2["SKIPPED_ALREADY_SAVED"] == 1
        assert mock_get_second.call_count == 0  # No network request made


def test_job0_daily_cap_enforced(tmp_path: Path):
    """Rule 5: Daily cap strictly enforced across attempts."""
    downloader = DailyPipelineDownloader(root_dir=tmp_path, pause_seconds=0.0, daily_cap=2)
    t_date = date(2026, 9, 28)

    mock_resp_404 = MagicMock()
    mock_resp_404.status_code = 404
    mock_resp_404.content = b"Not Found"

    with patch.object(downloader.session, "get", return_value=mock_resp_404) as mock_get:
        items = downloader.build_daily_items(trade_date=t_date)
        stats = downloader.execute_items(items)

        assert mock_get.call_count == 2
        assert stats["MISSING_404"] == 2
        assert stats["STOPPED_CAP_REACHED"] == 1


def test_job0_write_boundary_enforced(tmp_path: Path):
    """Rule 12: Writing outside history_dir is prohibited."""
    downloader = DailyPipelineDownloader(root_dir=tmp_path, pause_seconds=0.0)
    outside_file = tmp_path.parent / "unauthorized.txt"
    with pytest.raises(PermissionError, match="Write boundary violation"):
        downloader.safe_write(outside_file, b"malicious data")


def test_job0_user_agent_recorded_in_manifest(tmp_path: Path):
    """Addition C.6: user_agent recorded in every manifest line."""
    downloader = DailyPipelineDownloader(root_dir=tmp_path, pause_seconds=0.0)
    t_date = date(2026, 9, 28)

    mock_resp_404 = MagicMock()
    mock_resp_404.status_code = 404
    mock_resp_404.content = b"Not Found"

    with patch.object(downloader.session, "get", return_value=mock_resp_404):
        items = [downloader.build_daily_items(trade_date=t_date)[0]]
        downloader.execute_items(items)

    manifest_file = tmp_path / "raw" / "nse_archive" / "manifest.jsonl"
    line = json.loads(manifest_file.read_text(encoding="utf-8").strip())
    assert "user_agent" in line
    assert line["user_agent"] == DEFAULT_USER_AGENT


def test_job0_happy_path_four_files_saved(tmp_path: Path):
    """End-to-end happy path: all 4 files fetched, verified, saved, and manifest recorded."""
    downloader = DailyPipelineDownloader(root_dir=tmp_path, pause_seconds=0.0)
    t_date = date(2026, 9, 28)
    next_date = date(2026, 9, 29)

    cm_bytes = _make_valid_cm_bhav_zip(t_date, udiff=True)
    fo_bytes = _make_valid_fo_bhav_zip(t_date, udiff=True)
    mto_bytes = _make_valid_mto(t_date)
    ban_bytes = _make_valid_secban(next_date, ["KAYNES", "SAIL"])

    def side_effect(url, *args, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        if "BhavCopy_NSE_CM" in url or "cm28" in url.lower():
            resp.content = cm_bytes
        elif "BhavCopy_NSE_FO" in url or "fo28" in url.lower():
            resp.content = fo_bytes
        elif "MTO_" in url:
            resp.content = mto_bytes
        elif "fo_secban" in url:
            resp.content = ban_bytes
        else:
            resp.status_code = 404
            resp.content = b"Not Found"
        return resp

    with patch.object(downloader.session, "get", side_effect=side_effect) as mock_get:
        items = downloader.build_daily_items(trade_date=t_date, next_session_date=next_date)
        assert len(items) == 4
        stats = downloader.execute_items(items)

        assert stats["SAVED"] == 4
        assert stats["FAILED"] == 0
        assert mock_get.call_count == 4

    # Verify manifest lines
    manifest_file = tmp_path / "raw" / "nse_archive" / "manifest.jsonl"
    assert manifest_file.exists()
    records = [json.loads(line) for line in manifest_file.read_text(encoding="utf-8").strip().split("\n")]
    assert len(records) == 4

    datasets = [r["dataset"] for r in records]
    assert datasets == ["cm_bhavcopy", "fo_bhavcopy", "mto", "fo_secban"]

    for r in records:
        assert r["job"] == "JOB0"
        assert r["outcome"] == "SAVED"
        assert r["http_status"] == 200
        assert r["bytes"] > 0
        assert len(r["sha256"]) == 64
        assert r["user_agent"] == DEFAULT_USER_AGENT
        saved_file = tmp_path / r["saved_path"]
        assert saved_file.exists()
        assert len(saved_file.read_bytes()) == r["bytes"]

    # Verify daily summary report generated
    summary_file = tmp_path / "raw" / "nse_archive" / f"daily_report_{t_date.isoformat()}.json"
    assert summary_file.exists()
    summary = json.loads(summary_file.read_text(encoding="utf-8"))
    assert summary["trade_date"] == "2026-09-28"
    assert summary["next_session_date"] == "2026-09-29"
    assert summary["saved_count"] == 4


def test_job0_ban_file_nil_securities_accepted(tmp_path: Path):
    """A ban file that states 'NIL' or has no banned securities is valid if date matches."""
    downloader = DailyPipelineDownloader(root_dir=tmp_path, pause_seconds=0.0)
    t_date = date(2026, 9, 28)
    next_date = date(2026, 9, 29)

    nil_ban_content = f"Securities in Ban For Trade Date {next_date.strftime('%d-%b-%Y').upper()}: NIL\n".encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = nil_ban_content

    with patch.object(downloader.session, "get", return_value=mock_resp):
        items = [downloader.build_daily_items(trade_date=t_date, next_session_date=next_date)[3]]  # Ban item
        stats = downloader.execute_items(items)

        assert stats["SAVED"] == 1
        assert stats["FAILED"] == 0


def test_job0_timeout_retries_and_succeeds(tmp_path: Path):
    """Rule 8: Timeouts are retried up to 2 times and succeed if next attempt returns 200."""
    downloader = DailyPipelineDownloader(root_dir=tmp_path, pause_seconds=0.0)
    t_date = date(2026, 9, 28)

    valid_mto = _make_valid_mto(t_date)
    success_resp = MagicMock()
    success_resp.status_code = 200
    success_resp.content = valid_mto

    with patch("time.sleep", return_value=None):
        with patch.object(downloader.session, "get", side_effect=[requests.Timeout("Mock timeout"), success_resp]) as mock_get:
            items = [downloader.build_daily_items(trade_date=t_date)[2]]  # MTO
            stats = downloader.execute_items(items)

            assert stats["SAVED"] == 1
            assert mock_get.call_count == 2
