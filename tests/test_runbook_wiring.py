"""
tests/test_runbook_wiring.py
============================
Formal offline tests for runbook producer/consumer wiring and health verification:
- scripts/ingest_daily_bhavcopy.py (NSE CM Bhavcopy producer/consumer)
- scripts/generate_candidate_signals.py (Pre-market candidate signal producer)
- scripts/verify_desk_health.py (All 7 operational health invariants)
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
import pytest
import sqlite3

from antigravity.engine.execution_simulator import DailyBar
from antigravity.engine.risk_governor import TOTAL_CORPUS_RS
from antigravity.paper.paper_desk_runner import PaperDeskConfig, PaperDeskRunner
from antigravity.paper.paper_store import PaperStore
from scripts.ingest_daily_bhavcopy import ingest_daily_bhavcopy
from scripts.generate_candidate_signals import generate_candidate_signals
from scripts.verify_desk_health import verify_desk_health


def test_bhavcopy_producer_generates_verified_csv_and_manifest(tmp_path: Path):
    source_p = tmp_path / "raw_bhavcopy.csv"
    fieldnames = ["TckrSymb", "SctySrs", "OpnPric", "HghPric", "LwPric", "ClsPric", "TtlTradQty"]
    rows = [
        {"TckrSymb": "CDSL", "SctySrs": "EQ", "OpnPric": "100.0", "HghPric": "105.0", "LwPric": "99.0", "ClsPric": "104.0", "TtlTradQty": "50000"},
        {"TckrSymb": "INFY", "SctySrs": "EQ", "OpnPric": "1500.0", "HghPric": "1520.0", "LwPric": "1490.0", "ClsPric": "1510.0", "TtlTradQty": "80000"},
    ]
    with open(source_p, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    out_dir = tmp_path / "bhavcopy"
    manifest = ingest_daily_bhavcopy(
        session_date="2024-05-15",
        out_dir=out_dir,
        bhavcopy_source=str(source_p),
    )

    assert manifest["status"] == "NORMAL"
    assert manifest["session_date"] == "2024-05-15"
    assert manifest["record_count"] == 2
    assert (out_dir / "bhavcopy_2024-05-15.csv").exists()
    assert (out_dir / "manifest_2024-05-15.json").exists()


def test_bhavcopy_producer_fails_closed_on_missing_source(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="FAIL_CLOSED: Missing verified official NSE CM Bhavcopy"):
        ingest_daily_bhavcopy(
            session_date="2099-01-01",
            out_dir=tmp_path / "bhavcopy",
            bhavcopy_source=str(tmp_path / "nonexistent.csv"),
        )


def test_candidate_signals_producer(tmp_path: Path):
    out_dir = tmp_path / "signals"
    signals = generate_candidate_signals(
        session_date="2024-05-15",
        out_dir=out_dir,
    )
    sig_file = out_dir / "signals_2024-05-15.json"
    assert sig_file.exists()
    assert isinstance(signals, list)


def test_health_checker_fails_on_uninitialized_desk(tmp_path: Path):
    db_path = tmp_path / "empty_store.db"
    store = PaperStore(db_path)
    with pytest.raises(AssertionError, match="No equity records found"):
        verify_desk_health(db_path=db_path, session_date="2024-05-15")


def test_health_checker_fails_on_stale_equity(tmp_path: Path):
    db_path = tmp_path / "store.db"
    proj_dir = tmp_path / "projections"
    config = PaperDeskConfig(db_path=db_path, projections_dir=proj_dir)
    runner = PaperDeskRunner(config)
    runner.run_post_close(
        "2024-05-14",
        {"CDSL": DailyBar(symbol="CDSL", open=100.0, high=105.0, low=99.0, close=103.0, volume=50000)},
        bhavcopy_manifest={"status": "NORMAL", "session_date": "2024-05-14"},
    )
    with pytest.raises(AssertionError, match="Stale equity snapshot for session 2024-05-14"):
        verify_desk_health(db_path=db_path, session_date="2024-05-15")


def test_health_checker_fails_on_cash_buffer_breach(tmp_path: Path):
    db_path = tmp_path / "store.db"
    proj_dir = tmp_path / "projections"
    config = PaperDeskConfig(db_path=db_path, projections_dir=proj_dir)
    runner = PaperDeskRunner(config)
    runner.run_post_close(
        "2024-05-15",
        {"CDSL": DailyBar(symbol="CDSL", open=100.0, high=105.0, low=99.0, close=103.0, volume=50000)},
        bhavcopy_manifest={"status": "NORMAL", "session_date": "2024-05-15"},
    )
    # Tamper cash to simulate breach
    with runner.store._get_connection() as conn:
        conn.execute("UPDATE daily_equity SET cash_ledger_rs = 100000.0, cash_buffer_breach = 1 WHERE session_date = '2024-05-15'")

    with pytest.raises(AssertionError, match="Cash buffer breached"):
        verify_desk_health(db_path=db_path, session_date="2024-05-15", projections_dir=proj_dir)


def test_health_checker_fails_on_slot_breach(tmp_path: Path):
    db_path = tmp_path / "store.db"
    proj_dir = tmp_path / "projections"
    config = PaperDeskConfig(db_path=db_path, projections_dir=proj_dir)
    runner = PaperDeskRunner(config)
    runner.run_post_close(
        "2024-05-15",
        {"CDSL": DailyBar(symbol="CDSL", open=100.0, high=105.0, low=99.0, close=103.0, volume=50000)},
        bhavcopy_manifest={"status": "NORMAL", "session_date": "2024-05-15"},
    )
    with runner.store._get_connection() as conn:
        conn.execute("UPDATE daily_equity SET occupied_slots = 4 WHERE session_date = '2024-05-15'")

    with pytest.raises(AssertionError, match="Slot limit breached"):
        verify_desk_health(db_path=db_path, session_date="2024-05-15", projections_dir=proj_dir)


def test_health_checker_passes_on_healthy_session(tmp_path: Path):
    db_path = tmp_path / "store.db"
    proj_dir = tmp_path / "projections"
    config = PaperDeskConfig(db_path=db_path, projections_dir=proj_dir)
    runner = PaperDeskRunner(config)
    res = runner.run_post_close(
        "2024-05-15",
        {"CDSL": DailyBar(symbol="CDSL", open=100.0, high=105.0, low=99.0, close=103.0, volume=50000)},
        bhavcopy_manifest={"status": "NORMAL", "session_date": "2024-05-15"},
    )
    report = verify_desk_health(db_path=db_path, session_date="2024-05-15", projections_dir=proj_dir)
    assert report["status"] == "GREEN"
    assert report["occupied_slots"] == 0
    assert report["cash_ledger_rs"] == TOTAL_CORPUS_RS
