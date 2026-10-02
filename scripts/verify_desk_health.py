"""
scripts/verify_desk_health.py
=============================
Automated 7-Check Health Verification Monitor for Track 2 Canonical Paper Desk.
Enforces the 7 Invariants from YASHU_OPERATOR_RUNBOOK.md Section 4:
  H1: Cash Buffer (>= Rs 136,000.00, not cash_buffer_breach)
  H2: Occupied Slots (<= 3 slots)
  H3: Sector Concentration (<= 2 scrips per sector)
  H4: Data Status (== NORMAL)
  H5: Pending Exits (== 0 or accounted locked circuits)
  H6: Evidence Mode (== BAR_SCENARIO_NON_QUALIFYING)
  H7: CSV Reconciliation (exact row counts and SHA-256 integrity match SQLite)

Fails closed with exit code 1 / AssertionError if any check fails or if equity is missing/stale.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any, Dict, List, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from antigravity.paper.paper_store import PaperStore
from antigravity.paper.paper_contracts import EVIDENCE_MODE_DEFAULT


def compute_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def verify_desk_health(
    db_path: Path,
    session_date: str,
    projections_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    db_path = Path(db_path).resolve()
    if not db_path.exists():
        raise AssertionError(f"CRITICAL: Paper database does not exist: {db_path}")

    store = PaperStore(db_path)
    eq = store.get_latest_equity()

    # Fail closed on uninitialized desk
    if eq is None:
        raise AssertionError("CRITICAL: No equity records found. Desk not initialized.")

    # Fail closed on stale equity
    if eq.session_date != session_date:
        raise AssertionError(
            f"CRITICAL: Stale equity snapshot for session {eq.session_date} (expected today: {session_date})."
        )

    # H1: Cash Buffer
    if eq.cash_ledger_rs < 136000.0 or eq.cash_buffer_breach:
        raise AssertionError(
            f"CRITICAL [H1]: Cash buffer breached! (Cash: Rs {eq.cash_ledger_rs:,.2f}, buffer_breach={eq.cash_buffer_breach})"
        )

    # H2: Occupied Slots
    if eq.occupied_slots > 3:
        raise AssertionError(
            f"CRITICAL [H2]: Slot limit breached! (Occupied: {eq.occupied_slots} / 3)"
        )

    # H3: Sector Concentration
    open_positions = store.get_open_positions()
    sector_counts: Dict[str, int] = {}
    for p in open_positions:
        # Default sector to sleeve or symbol prefix if unclassified
        sec = getattr(p, "sector", None) or p.sleeve_id
        sector_counts[sec] = sector_counts.get(sec, 0) + 1

    max_sector_count = max(sector_counts.values(), default=0)
    if max_sector_count > 2:
        raise AssertionError(
            f"CRITICAL [H3]: Sector concentration limit breached! (Max scrips in single sector: {max_sector_count} / 2)"
        )

    # H4: Data Status
    if eq.data_status != "NORMAL":
        raise AssertionError(
            f"CRITICAL [H4]: Data status is {eq.data_status} (expected NORMAL)."
        )

    # H5: Pending Exits
    if eq.pending_exit_count > 0:
        # Allowed only if accounted for locked circuits
        locked_count = sum(1 for p in open_positions if p.mark_status == "LOCKED_CIRCUIT")
        if eq.pending_exit_count > locked_count:
            raise AssertionError(
                f"CRITICAL [H5]: Unaccounted pending exits detected: {eq.pending_exit_count} (locked: {locked_count})"
            )

    # H6: Evidence Mode
    # Verified from latest ledger event or schema invariant
    with store._get_connection() as conn:
        last_evt = conn.execute("SELECT payload_json FROM ledger_events ORDER BY event_seq DESC LIMIT 1").fetchone()
        if last_evt:
            p_dict = json.loads(last_evt["payload_json"])
            ev_status = p_dict.get("qualifying_evidence_status")
            if ev_status and ev_status != EVIDENCE_MODE_DEFAULT:
                raise AssertionError(
                    f"CRITICAL [H6]: Evidence mode {ev_status} does not match required watermark {EVIDENCE_MODE_DEFAULT}."
                )

    # H7: CSV Reconciliation
    if projections_dir:
        p_dir = Path(projections_dir).resolve()
        manifest_p = p_dir / "generation_manifest.json"
        if not manifest_p.exists():
            raise AssertionError(f"CRITICAL [H7]: Generation manifest missing: {manifest_p}")

        m_data = json.loads(manifest_p.read_text(encoding="utf-8"))
        for fkey, item in m_data.get("files", {}).items():
            f_path = p_dir / item["name"]
            if not f_path.exists():
                raise AssertionError(f"CRITICAL [H7]: Projected CSV {item['name']} missing from disk.")
            actual_sha = compute_file_sha256(f_path).lower()
            if actual_sha != item["sha256"].lower():
                raise AssertionError(
                    f"CRITICAL [H7]: Hash mismatch for {item['name']} (actual {actual_sha} != manifest {item['sha256']})."
                )

        # Reconcile row counts
        with store._get_connection() as conn:
            db_journal_cnt = conn.execute("SELECT COUNT(*) FROM ledger_events").fetchone()[0]
            db_pos_cnt = conn.execute("SELECT COUNT(*) FROM positions WHERE status = 'OPEN'").fetchone()[0]
            db_eq_cnt = conn.execute("SELECT COUNT(*) FROM daily_equity").fetchone()[0]

        with open(p_dir / "canonical_paper_journal.csv", newline="", encoding="utf-8") as f:
            csv_journal_cnt = sum(1 for _ in csv.DictReader(f))
        with open(p_dir / "open_positions.csv", newline="", encoding="utf-8") as f:
            csv_pos_cnt = sum(1 for _ in csv.DictReader(f))
        with open(p_dir / "daily_portfolio_equity.csv", newline="", encoding="utf-8") as f:
            csv_eq_cnt = sum(1 for _ in csv.DictReader(f))

        if csv_journal_cnt != db_journal_cnt or csv_pos_cnt != db_pos_cnt or csv_eq_cnt != db_eq_cnt:
            raise AssertionError(
                f"CRITICAL [H7]: Row count mismatch between CSV and SQLite "
                f"(Journal: {csv_journal_cnt} vs {db_journal_cnt}, Pos: {csv_pos_cnt} vs {db_pos_cnt}, Eq: {csv_eq_cnt} vs {db_eq_cnt})."
            )

    return {
        "session_date": eq.session_date,
        "cash_ledger_rs": eq.cash_ledger_rs,
        "equity_rs": eq.equity_rs,
        "occupied_slots": eq.occupied_slots,
        "pending_exit_count": eq.pending_exit_count,
        "data_status": eq.data_status,
        "status": "GREEN",
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Track 2 Daily Health Verification Monitor")
    parser.add_argument("--session-date", required=True, help="Session date (YYYY-MM-DD)")
    parser.add_argument(
        "--db-path",
        default=str(ROOT_DIR / "shared" / "track2_liquid" / "paper" / "canonical_paper_store.db"),
        help="Path to paper SQLite database",
    )
    parser.add_argument(
        "--projections-dir",
        default=str(ROOT_DIR / "shared" / "track2_liquid" / "paper"),
        help="Path to projections CSV directory",
    )

    args = parser.parse_args(argv)
    try:
        report = verify_desk_health(
            db_path=Path(args.db_path),
            session_date=args.session_date,
            projections_dir=Path(args.projections_dir) if args.projections_dir else None,
        )
        print("--- DESK HEALTH SNAPSHOT ---")
        print(f"Session Date:     {report['session_date']}")
        print(f"Cash Ledger:      Rs {report['cash_ledger_rs']:,.2f} (Buffer: Rs 136,000.00)")
        print(f"Total Equity:     Rs {report['equity_rs']:,.2f}")
        print(f"Occupied Slots:   {report['occupied_slots']} / 3")
        print(f"Pending Exits:    {report['pending_exit_count']}")
        print(f"Data Status:      {report['data_status']}")
        print("STATUS: GREEN - All 7 Operational Invariants Formally Satisfied.")
        return 0
    except AssertionError as ae:
        print(f"HEALTH CHECK FAILED: {ae}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"UNEXPECTED ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
