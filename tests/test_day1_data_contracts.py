"""
tests/test_day1_data_contracts.py
=================================
Adversarial & contract unit tests for Day 1 deliverables:
- PreOpenEligibilityValidator (Codex Mandate 1: fail-closed pre-open surveillance)
- Input readiness schema classification & provenance audit functions
"""

import gzip
import json
import os
import shutil
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from antigravity.engine.eligibility_contract import (
    EligibilityVerdict,
    PreOpenEligibilityValidator,
    IST,
)
from scripts.audit_input_readiness import (
    audit_gz_csv,
    audit_mto_dat,
    compute_sha256,
)


@pytest.fixture
def temp_env():
    td = tempfile.mkdtemp(prefix="day1_test_")
    surv_dir = Path(td) / "surveillance"
    fno_dir = Path(td) / "fno_master"
    surv_dir.mkdir(parents=True)
    fno_dir.mkdir(parents=True)

    yield {
        "root": Path(td),
        "surveillance": surv_dir,
        "fno_master": fno_dir,
    }

    shutil.rmtree(td, ignore_errors=True)


def test_eligibility_scrip_eligible(temp_env):
    """Eligible F&O scrip not on surveillance passes."""
    session_date = date(2026, 10, 1)

    # Write F&O master with RELIANCE and TCS
    fno_file = temp_env["fno_master"] / "fno_underlyings_2026-10-01.json"
    fno_file.write_text(
        json.dumps({
            "fetched_at": "2026-10-01 08:30:00 IST",
            "fno_underlyings": ["RELIANCE", "TCS", "INFY"],
        }),
        encoding="utf-8",
    )

    # Write surveillance with only INFY under ASM
    surv_file = temp_env["surveillance"] / "surveillance_2026-10-01.json"
    surv_file.write_text(
        json.dumps({
            "fetched_at": "2026-10-01 08:30:00 IST",
            "asm_short_term": ["INFY"],
            "gsm": [],
        }),
        encoding="utf-8",
    )

    validator = PreOpenEligibilityValidator(
        surveillance_dir=temp_env["surveillance"],
        fno_master_dir=temp_env["fno_master"],
    )

    res = validator.validate_scrip("RELIANCE", session_date)
    assert res.is_passed
    assert res.verdict == EligibilityVerdict.ELIGIBLE
    assert res.is_fno is True
    assert res.is_surveillance is False


def test_eligibility_blocks_surveillance_scrip(temp_env):
    """Scrip on ASM list is blocked strictly."""
    session_date = date(2026, 10, 1)

    fno_file = temp_env["fno_master"] / "fno_underlyings_2026-10-01.json"
    fno_file.write_text(
        json.dumps({
            "fetched_at": "2026-10-01 08:30:00 IST",
            "fno_underlyings": ["INFY"],
        }),
        encoding="utf-8",
    )

    surv_file = temp_env["surveillance"] / "surveillance_2026-10-01.json"
    surv_file.write_text(
        json.dumps({
            "fetched_at": "2026-10-01 08:30:00 IST",
            "asm_short_term": ["INFY"],
            "gsm": [],
        }),
        encoding="utf-8",
    )

    validator = PreOpenEligibilityValidator(
        surveillance_dir=temp_env["surveillance"],
        fno_master_dir=temp_env["fno_master"],
    )

    res = validator.validate_scrip("INFY", session_date)
    assert not res.is_passed
    assert res.verdict == EligibilityVerdict.BLOCKED_SURVEILLANCE
    assert res.is_surveillance is True


def test_eligibility_blocks_non_fno_scrip(temp_env):
    """Scrip not in verified F&O list is rejected."""
    session_date = date(2026, 10, 1)

    fno_file = temp_env["fno_master"] / "fno_underlyings_2026-10-01.json"
    fno_file.write_text(
        json.dumps({
            "fetched_at": "2026-10-01 08:30:00 IST",
            "fno_underlyings": ["RELIANCE"],
        }),
        encoding="utf-8",
    )

    surv_file = temp_env["surveillance"] / "surveillance_2026-10-01.json"
    surv_file.write_text(json.dumps({"fetched_at": "2026-10-01 08:30:00 IST", "asm_short_term": []}), encoding="utf-8")

    validator = PreOpenEligibilityValidator(
        surveillance_dir=temp_env["surveillance"],
        fno_master_dir=temp_env["fno_master"],
    )

    res = validator.validate_scrip("PENNYSTOCK", session_date)
    assert not res.is_passed
    assert res.verdict == EligibilityVerdict.BLOCKED_NOT_FNO


def test_eligibility_fails_closed_when_surveillance_missing(temp_env):
    """Missing surveillance file fails closed (blocks all scrips)."""
    session_date = date(2026, 10, 1)

    fno_file = temp_env["fno_master"] / "fno_underlyings_2026-10-01.json"
    fno_file.write_text(
        json.dumps({
            "fetched_at": "2026-10-01 08:30:00 IST",
            "fno_underlyings": ["RELIANCE"],
        }),
        encoding="utf-8",
    )

    # surveillance directory is completely empty
    validator = PreOpenEligibilityValidator(
        surveillance_dir=temp_env["surveillance"],
        fno_master_dir=temp_env["fno_master"],
    )

    res = validator.validate_scrip("RELIANCE", session_date)
    assert not res.is_passed
    assert res.verdict == EligibilityVerdict.BLOCKED_MISSING_EVIDENCE
    assert "No surveillance snapshots" in str(res.rejection_reason)


def test_eligibility_fails_closed_on_lookahead_timestamp(temp_env):
    """Surveillance timestamped AFTER 09:08:00 is rejected as lookahead."""
    session_date = date(2026, 10, 1)

    fno_file = temp_env["fno_master"] / "fno_underlyings_2026-10-01.json"
    fno_file.write_text(
        json.dumps({
            "fetched_at": "2026-10-01 08:30:00 IST",
            "fno_underlyings": ["RELIANCE"],
        }),
        encoding="utf-8",
    )

    surv_file = temp_env["surveillance"] / "surveillance_2026-10-01.json"
    surv_file.write_text(
        json.dumps({
            "fetched_at": "2026-10-01 09:45:00 IST",  # AFTER 09:08:00!
            "asm_short_term": [],
        }),
        encoding="utf-8",
    )

    validator = PreOpenEligibilityValidator(
        surveillance_dir=temp_env["surveillance"],
        fno_master_dir=temp_env["fno_master"],
    )

    res = validator.validate_scrip("RELIANCE", session_date)
    assert not res.is_passed
    assert res.verdict == EligibilityVerdict.BLOCKED_MISSING_EVIDENCE
    assert "after cutoff" in str(res.rejection_reason)


def test_audit_gz_csv_udiff_cm(temp_env):
    """Tests audit classification of UDiFF CM file."""
    p = temp_env["root"] / "sample_udiff_cm.csv.gz"
    header = "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol\n"
    row = "2026-10-01,2026-10-01,CM,NSE,EQ,1234,INE002A01018,RELIANCE,EQ,2900.0,2950.0,2890.0,2940.0,1000000\n"

    with gzip.open(p, "wt", encoding="utf-8") as f:
        f.write(header)
        f.write(row)

    res = audit_gz_csv(p, "UDIFF_CM")
    assert res["status"] == "VALID"
    assert res["schema_type"] == "UDIFF_CM"
    assert res["row_count"] == 1
    assert res["sample_date"] == "2026-10-01"
    assert len(res["sha256"]) == 64


def test_audit_mto_dat(temp_env):
    """Tests audit classification of MTO DAT file."""
    p = temp_env["root"] / "MTO_01102026.DAT"
    content = (
        "Security Wise Delivery Position\n"
        "10,MTO,01102026,12345,100\n"
        "Trade Date <01-OCT-2026>,Settlement Type <N>\n"
        "Record Type,Sr No,Name of Security,Quantity Traded,Deliverable Quantity,% Deliverable\n"
        "20,1,RELIANCE,EQ,10000,5000,50.00\n"
        "20,2,TCS,EQ,20000,12000,60.00\n"
    )
    p.write_text(content, encoding="utf-8")

    res = audit_mto_dat(p)
    assert res["status"] == "VALID"
    assert res["schema_type"] == "MTO_RECORD_20"
    assert res["row_count"] == 2
    assert res["trade_date"] == "01-OCT-2026"
    assert len(res["sha256"]) == 64


def test_eligibility_fails_closed_when_fno_master_missing(temp_env):
    """Missing F&O master directory/files fails closed."""
    session_date = date(2026, 10, 1)
    # fno_master directory is empty
    surv_file = temp_env["surveillance"] / "surveillance_2026-10-01.json"
    surv_file.write_text(json.dumps({"fetched_at": "2026-10-01 08:30:00 IST", "asm_short_term": []}), encoding="utf-8")

    validator = PreOpenEligibilityValidator(
        surveillance_dir=temp_env["surveillance"],
        fno_master_dir=temp_env["fno_master"],
    )

    res = validator.validate_scrip("RELIANCE", session_date)
    assert not res.is_passed
    assert res.verdict == EligibilityVerdict.BLOCKED_MISSING_EVIDENCE
    assert "No F&O master files" in str(res.rejection_reason)


def test_eligibility_fails_closed_when_surveillance_corrupted(temp_env):
    """Corrupted/invalid JSON surveillance snapshot fails closed."""
    session_date = date(2026, 10, 1)

    fno_file = temp_env["fno_master"] / "fno_underlyings_2026-10-01.json"
    fno_file.write_text(json.dumps({"fetched_at": "2026-10-01 08:30:00 IST", "fno_underlyings": ["RELIANCE"]}), encoding="utf-8")

    surv_file = temp_env["surveillance"] / "surveillance_2026-10-01.json"
    surv_file.write_text("{CORRUPTED_JSON_TRUNCATED", encoding="utf-8")

    validator = PreOpenEligibilityValidator(
        surveillance_dir=temp_env["surveillance"],
        fno_master_dir=temp_env["fno_master"],
    )

    res = validator.validate_scrip("RELIANCE", session_date)
    assert not res.is_passed
    assert res.verdict == EligibilityVerdict.BLOCKED_MISSING_EVIDENCE
    assert "Failed to parse surveillance snapshot" in str(res.rejection_reason)


def test_eligibility_stale_surveillance_exceeding_max_age_blocked(temp_env):
    """Surveillance older than max_evidence_age_days fails closed."""
    session_date = date(2026, 10, 10)  # 10 days later

    fno_file = temp_env["fno_master"] / "fno_underlyings_2026-10-10.json"
    fno_file.write_text(json.dumps({"fetched_at": "2026-10-10 08:30:00 IST", "fno_underlyings": ["RELIANCE"]}), encoding="utf-8")

    surv_file = temp_env["surveillance"] / "surveillance_2026-10-01.json"
    surv_file.write_text(json.dumps({"fetched_at": "2026-10-01 08:30:00 IST", "asm_short_term": []}), encoding="utf-8")

    validator = PreOpenEligibilityValidator(
        surveillance_dir=temp_env["surveillance"],
        fno_master_dir=temp_env["fno_master"],
        max_evidence_age_days=4,
    )

    res = validator.validate_scrip("RELIANCE", session_date)
    assert not res.is_passed
    assert res.verdict == EligibilityVerdict.BLOCKED_MISSING_EVIDENCE
    assert "days old" in str(res.rejection_reason)


def test_audit_gz_csv_udiff_fo(temp_env):
    """Tests audit classification of UDiFF FO file."""
    p = temp_env["root"] / "sample_udiff_fo.csv.gz"
    header = "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol\n"
    row = "2026-10-01,2026-10-01,FO,NSE,FUT,1234,INE002A01018,RELIANCE,2910.0,2960.0,2900.0,2950.0,500000\n"

    with gzip.open(p, "wt", encoding="utf-8") as f:
        f.write(header)
        f.write(row)

    res = audit_gz_csv(p, "UDIFF_FO")
    assert res["status"] == "VALID"
    assert res["schema_type"] == "UDIFF_FO"
    assert res["row_count"] == 1


def test_audit_gz_csv_corrupt_rejected(temp_env):
    """Tests audit handling of corrupted non-gzip file."""
    p = temp_env["root"] / "corrupt.csv.gz"
    p.write_bytes(b"NOT_A_VALID_GZIP_STREAM")

    res = audit_gz_csv(p, "UDIFF_CM")
    assert res["status"] == "CORRUPT"
    assert "error" in res
