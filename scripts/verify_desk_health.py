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
import math
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
from antigravity.models.track2_portfolio_risk_governor import DEFAULT_SECTOR_MAP


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
        sec = getattr(p, "sector", None) or DEFAULT_SECTOR_MAP.get(str(p.symbol).strip().upper(), "UNKNOWN_SECTOR")
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
    with store._get_connection() as conn:
        all_events = conn.execute("SELECT event_id, payload_json FROM ledger_events").fetchall()
        for evt_row in all_events:
            p_dict = json.loads(evt_row["payload_json"]) if evt_row["payload_json"] else {}
            ev_status = p_dict.get("qualifying_evidence_status")
            if not ev_status or ev_status != EVIDENCE_MODE_DEFAULT:
                raise AssertionError(
                    f"CRITICAL [H6]: Event {evt_row['event_id']} has invalid or missing qualifying_evidence_status "
                    f"'{ev_status}' (expected '{EVIDENCE_MODE_DEFAULT}')."
                )

    # H7: CSV Value Reconciliation
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

        with store._get_connection() as conn:
            db_journal_events = conn.execute("SELECT * FROM ledger_events ORDER BY event_seq ASC").fetchall()
            db_journal_cnt = len(db_journal_events)
            db_pos_cnt = conn.execute("SELECT COUNT(*) FROM positions WHERE status = 'OPEN'").fetchone()[0]
            db_eq_cnt = conn.execute("SELECT COUNT(*) FROM daily_equity").fetchone()[0]
            db_equity_rows = {row["session_date"]: dict(row) for row in conn.execute("SELECT * FROM daily_equity").fetchall()}
            db_pos_rows = {row["position_id"]: dict(row) for row in conn.execute("SELECT * FROM positions WHERE status = 'OPEN'").fetchall()}

        # 1. Equity Value Reconciliation
        with open(p_dir / "daily_portfolio_equity.csv", newline="", encoding="utf-8") as f:
            csv_eq_rows = list(csv.DictReader(f))
        if len(csv_eq_rows) != db_eq_cnt:
            raise AssertionError(f"CRITICAL [H7]: Row count mismatch on daily_portfolio_equity.csv: {len(csv_eq_rows)} != {db_eq_cnt}")
        for r_eq in csv_eq_rows:
            s_date = r_eq["session_date"]
            if s_date not in db_equity_rows:
                raise AssertionError(f"CRITICAL [H7]: Equity CSV contains session {s_date} missing in SQLite.")
            db_r = db_equity_rows[s_date]

            try:
                csv_cash = float(r_eq["cash_ledger_rs"])
            except (ValueError, TypeError):
                raise AssertionError(f"CRITICAL [H7]: Non-numeric cash value for {s_date}: {r_eq.get('cash_ledger_rs')}")
            if not math.isfinite(csv_cash) or abs(csv_cash - float(db_r["cash_ledger_rs"])) > 0.01:
                raise AssertionError(
                    f"CRITICAL [H7]: Cash mismatch/non-finite for {s_date}: CSV has {r_eq['cash_ledger_rs']} != SQLite has {db_r['cash_ledger_rs']}"
                )

            try:
                csv_eq_val = float(r_eq["equity_rs"])
            except (ValueError, TypeError):
                raise AssertionError(f"CRITICAL [H7]: Non-numeric equity value for {s_date}: {r_eq.get('equity_rs')}")
            if not math.isfinite(csv_eq_val) or abs(csv_eq_val - float(db_r["equity_rs"])) > 0.01:
                raise AssertionError(
                    f"CRITICAL [H7]: Equity mismatch/non-finite for {s_date}: CSV has {r_eq['equity_rs']} != SQLite has {db_r['equity_rs']}"
                )

            if "occupied_slots" in r_eq and int(r_eq["occupied_slots"]) != int(db_r["occupied_slots"]):
                raise AssertionError(
                    f"CRITICAL [H7]: Occupied slots mismatch for {s_date}: CSV has {r_eq['occupied_slots']} != SQLite has {db_r['occupied_slots']}"
                )

            if r_eq.get("data_status") != db_r["data_status"]:
                raise AssertionError(
                    f"CRITICAL [H7]: Data status mismatch for {s_date}: CSV has {r_eq.get('data_status')} != SQLite has {db_r['data_status']}"
                )

        # 2. Positions Value Reconciliation
        with open(p_dir / "open_positions.csv", newline="", encoding="utf-8") as f:
            csv_pos_rows = list(csv.DictReader(f))
        if len(csv_pos_rows) != db_pos_cnt:
            raise AssertionError(f"CRITICAL [H7]: Row count mismatch on open_positions.csv: {len(csv_pos_rows)} != {db_pos_cnt}")
        for r_pos in csv_pos_rows:
            p_id = r_pos["position_id"]
            if p_id not in db_pos_rows:
                raise AssertionError(f"CRITICAL [H7]: Position {p_id} in open_positions.csv missing or not OPEN in SQLite.")
            db_p = db_pos_rows[p_id]
            if int(r_pos["residual_qty"]) != int(db_p["residual_qty"]):
                raise AssertionError(f"CRITICAL [H7]: Position {p_id} residual_qty mismatch: CSV {r_pos['residual_qty']} != SQLite {db_p['residual_qty']}")

        # 3. Journal Reconciliation
        with open(p_dir / "canonical_paper_journal.csv", newline="", encoding="utf-8") as f:
            csv_journal_rows = list(csv.DictReader(f))
        if len(csv_journal_rows) != db_journal_cnt:
            raise AssertionError(f"CRITICAL [H7]: Row count mismatch on canonical_paper_journal.csv: {len(csv_journal_rows)} != {db_journal_cnt}")
        for r_csv, r_db in zip(csv_journal_rows, db_journal_events):
            if int(r_csv["event_seq"]) != int(r_db["event_seq"]):
                raise AssertionError(
                    f"CRITICAL [H7]: Event sequence mismatch: CSV has {r_csv['event_seq']} != SQLite has {r_db['event_seq']}"
                )
            if r_csv.get("event_id") != r_db["event_id"]:
                raise AssertionError(
                    f"CRITICAL [H7]: Event ID mismatch: CSV has {r_csv.get('event_id')} != SQLite has {r_db['event_id']}"
                )
            if r_csv.get("event_type") != r_db["event_type"]:
                raise AssertionError(
                    f"CRITICAL [H7]: Event type mismatch: CSV has {r_csv.get('event_type')} != SQLite has {r_db['event_type']}"
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
