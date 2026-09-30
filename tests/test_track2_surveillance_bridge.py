"""
tests/test_track2_surveillance_bridge.py
========================================
Unit tests for the surveillance bridge that synchronizes official ingestor
snapshots to the research desk layout with cryptographic verification.
"""
import hashlib
import json
from datetime import date
from pathlib import Path
import pytest

from antigravity.daemons.track2_surveillance_bridge import sync_surveillance_to_research

DAY = "2026-09-29"


def _setup_operational_fixtures(op_dir: Path, session_date: str = DAY, corrupt_hash: bool = False,
                                parse_status: str = "SUCCESS", wrong_date: bool = False):
    op_dir.mkdir(parents=True, exist_ok=True)
    asm_content = b'{"longterm": {"data": [{"symbol": "ASM1"}]}, "shortterm": {"data": []}}'
    gsm_content = b'[{"symbol": "GSM1"}]'

    asm_sha = hashlib.sha256(asm_content).hexdigest()
    gsm_sha = hashlib.sha256(gsm_content).hexdigest()

    asm_rel = f"raw_nse_asm_{session_date}_{asm_sha[:8]}.json"
    gsm_rel = f"raw_nse_gsm_{session_date}_{gsm_sha[:8]}.json"

    (op_dir / asm_rel).write_bytes(asm_content)
    (op_dir / gsm_rel).write_bytes(gsm_content)

    if corrupt_hash:
        asm_sha = "0000000000000000000000000000000000000000000000000000000000000000"

    eff_date = "2026-09-25" if wrong_date else session_date

    snap = {
        "snapshot_id": f"NSE_SURV_{session_date.replace('-', '')}_INGESTED",
        "effective_session_date": eff_date,
        "publication_date": session_date,
        "parse_status": parse_status,
        "sources": {
            "asm": {
                "raw_relative_path": asm_rel,
                "sha256": asm_sha,
                "http_status": 200,
                "source_url": "https://www.nseindia.com/api/reportDir/asm.json",
                "fetched_at": f"{session_date}T08:30:00+05:30",
                "byte_length": len(asm_content),
            },
            "gsm": {
                "raw_relative_path": gsm_rel,
                "sha256": gsm_sha,
                "http_status": 200,
                "source_url": "https://www.nseindia.com/api/reportDir/gsm.json",
                "fetched_at": f"{session_date}T08:30:00+05:30",
                "byte_length": len(gsm_content),
            },
        },
    }
    (op_dir / f"nse_surveillance_snapshot_{session_date}.json").write_text(json.dumps(snap), encoding="utf-8")


def test_sync_surveillance_success(tmp_path):
    op_dir = tmp_path / "operational"
    target_hist = tmp_path / "research_hist"
    _setup_operational_fixtures(op_dir)

    res = sync_surveillance_to_research(DAY, operational_dir=op_dir, target_history_dir=target_hist)
    assert res["ok"] is True
    assert res["session_date"] == DAY

    target_surv = target_hist / "raw" / "nse" / "surveillance"
    assert (target_surv / f"{DAY}_asm.json").exists()
    assert (target_surv / f"{DAY}_gsm.json").exists()
    assert (target_surv / f"bridge_receipt_{DAY}.json").exists()

    # Claude T2-01 audit finding: target_surv must also contain the exact raw_relative_path files referenced by the snapshot
    snap = json.loads((target_surv / f"nse_surveillance_snapshot_{DAY}.json").read_text(encoding="utf-8"))
    asm_rel = snap["sources"]["asm"]["raw_relative_path"]
    gsm_rel = snap["sources"]["gsm"]["raw_relative_path"]
    assert (target_surv / asm_rel).exists(), f"Raw ASM file {asm_rel} was not bridged to {target_surv}"
    assert (target_surv / gsm_rel).exists(), f"Raw GSM file {gsm_rel} was not bridged to {target_surv}"

    receipt = json.loads((target_surv / f"bridge_receipt_{DAY}.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "VERIFIED_ATOMIC"
    assert receipt["session_date"] == DAY



def test_sync_surveillance_corrupt_hash_fails_closed(tmp_path):
    op_dir = tmp_path / "operational"
    target_hist = tmp_path / "research_hist"
    _setup_operational_fixtures(op_dir, corrupt_hash=True)

    res = sync_surveillance_to_research(DAY, operational_dir=op_dir, target_history_dir=target_hist)
    assert res["ok"] is False
    assert "HASH_MISMATCH" in res["reason"]


def test_sync_surveillance_failed_parse_status_fails_closed(tmp_path):
    op_dir = tmp_path / "operational"
    target_hist = tmp_path / "research_hist"
    _setup_operational_fixtures(op_dir, parse_status="FAILED")

    res = sync_surveillance_to_research(DAY, operational_dir=op_dir, target_history_dir=target_hist)
    assert res["ok"] is False
    assert "SNAPSHOT_PARSE_STATUS_NOT_SUCCESS" in res["reason"]


def test_sync_surveillance_date_mismatch_fails_closed(tmp_path):
    op_dir = tmp_path / "operational"
    target_hist = tmp_path / "research_hist"
    _setup_operational_fixtures(op_dir, wrong_date=True)

    res = sync_surveillance_to_research(DAY, operational_dir=op_dir, target_history_dir=target_hist)
    assert res["ok"] is False
    assert "SESSION_DATE_MISMATCH" in res["reason"]


def test_bridged_surveillance_can_be_read_by_market_files(tmp_path):
    """Claude T2-01 integration: MarketFiles reader must successfully read and verify bridged files."""
    import sys
    sys.path.insert(0, r"C:\Users\yashw\swing-trades-track2")
    from research.framework.market import MarketFiles

    op_dir = tmp_path / "operational"
    target_hist = tmp_path / "research_hist"
    _setup_operational_fixtures(op_dir)

    res = sync_surveillance_to_research(DAY, operational_dir=op_dir, target_history_dir=target_hist)
    assert res["ok"] is True

    mf = MarketFiles(history=target_hist)
    syms = mf.surveillance(date.fromisoformat(DAY))
    assert syms == {"ASM1", "GSM1"}, f"Expected {{'ASM1', 'GSM1'}}, got {syms}"

