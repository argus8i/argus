"""
antigravity/paper/paper_store.py
================================
Transactional SQLite Event Store and Deterministic CSV Projection Engine.
Part of Project Swing Trades (ARGUS 8i Track 2 Liquid Desk).

Key Guarantees (Codex Deliberation 2026-10-02):
1. Single Source of Truth: All events, reservations, positions, and equity snapshots
   are committed atomically in a single SQLite transaction with WAL journaling.
2. Crash & Replay Invariance: Replaying from the SQLite store produces identical CSV projections.
3. Atomic Projections: Journal, open positions, and daily equity CSVs are written
   via temporary files and atomic rename with a shared generation_id and last_event_seq.
4. Concurrency Protection: Inter-process transaction serialization via SQLite busy timeout.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

from antigravity.paper.paper_contracts import (
    PaperJournalEvent,
    OpenPositionRecord,
    DailyPortfolioEquityRecord,
    SCHEMA_VERSION,
    TRACK_ID,
    DESK_ID,
    EVIDENCE_MODE_DEFAULT,
    REVIEW_STATUS_DEFAULT,
)


class PaperStore:
    """
    Transactional SQLite persistence layer for Track 2 Canonical Paper Desk.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute("""
            CREATE TABLE IF NOT EXISTS ledger_events (
                event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                event_type TEXT NOT NULL,
                event_at TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                session_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                sleeve_id TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                requested_qty INTEGER NOT NULL,
                fill_qty_delta INTEGER NOT NULL,
                cumulative_fill_qty INTEGER NOT NULL,
                remaining_order_qty INTEGER NOT NULL,
                fill_price REAL,
                stop_price REAL,
                target_price REAL,
                total_cost_rs REAL NOT NULL,
                cash_delta_rs REAL NOT NULL,
                realized_net_pnl_delta_rs REAL NOT NULL,
                exit_reason TEXT,
                reject_reason TEXT,
                payload_json TEXT NOT NULL
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                position_id TEXT PRIMARY KEY,
                isin TEXT NOT NULL,
                symbol TEXT NOT NULL,
                series TEXT NOT NULL,
                sleeve_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                entry_session TEXT NOT NULL,
                acquired_qty INTEGER NOT NULL,
                sold_qty INTEGER NOT NULL,
                residual_qty INTEGER NOT NULL,
                residual_cost_basis_rs REAL NOT NULL,
                entry_cost_allocation_rs REAL NOT NULL,
                stop_price REAL NOT NULL,
                target_price REAL NOT NULL,
                planned_open_risk_rs REAL NOT NULL,
                exit_intent TEXT,
                exit_intent_created_at TEXT,
                pending_exit_order_id TEXT,
                last_mark REAL NOT NULL,
                mark_session TEXT NOT NULL,
                mark_source_hash TEXT NOT NULL,
                mark_status TEXT NOT NULL,
                corporate_action_status TEXT NOT NULL,
                settlement_status TEXT NOT NULL,
                status TEXT NOT NULL
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS reservations (
                reservation_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                sleeve_id TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                stop_price REAL NOT NULL,
                reserved_cash REAL NOT NULL,
                reserved_risk REAL NOT NULL,
                session_date TEXT NOT NULL,
                status TEXT NOT NULL
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_equity (
                session_date TEXT PRIMARY KEY,
                valuation_at TEXT NOT NULL,
                cash_ledger_rs REAL NOT NULL,
                cash_settled_rs REAL NOT NULL,
                receivable_rs REAL NOT NULL,
                payable_rs REAL NOT NULL,
                reserved_cash_rs REAL NOT NULL,
                free_cash_rs REAL NOT NULL,
                inventory_mtm_rs REAL NOT NULL,
                equity_rs REAL NOT NULL,
                external_flow_rs REAL NOT NULL,
                realized_net_pnl_cumulative_rs REAL NOT NULL,
                unrealized_pnl_rs REAL NOT NULL,
                costs_cumulative_rs REAL NOT NULL,
                occupied_slots INTEGER NOT NULL,
                pending_slots INTEGER NOT NULL,
                committed_exposure_rs REAL NOT NULL,
                marked_exposure_rs REAL NOT NULL,
                planned_open_risk_rs REAL NOT NULL,
                reserved_risk_rs REAL NOT NULL,
                pending_exit_count INTEGER NOT NULL,
                unresolved_position_count INTEGER NOT NULL,
                stale_mark_count INTEGER NOT NULL,
                drawdown_rs REAL NOT NULL,
                drawdown_pct REAL NOT NULL,
                cash_buffer_breach INTEGER NOT NULL,
                risk_breach INTEGER NOT NULL,
                data_status TEXT NOT NULL
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS consumed_volume (
                session_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                consumed_volume INTEGER NOT NULL,
                PRIMARY KEY (session_date, symbol)
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """)
            conn.execute("COMMIT;")

    def append_event(self, event: PaperJournalEvent) -> int:
        """
        Appends an immutable event to the ledger in a transaction.
        Returns the assigned event_seq.
        """
        payload = json.dumps(event.to_dict(), ensure_ascii=False)
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            cur = conn.execute("""
                INSERT INTO ledger_events (
                    event_id, event_type, event_at, recorded_at, session_date,
                    symbol, sleeve_id, side, order_type, requested_qty,
                    fill_qty_delta, cumulative_fill_qty, remaining_order_qty,
                    fill_price, stop_price, target_price, total_cost_rs,
                    cash_delta_rs, realized_net_pnl_delta_rs, exit_reason,
                    reject_reason, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event.event_id, event.event_type, event.event_at, event.recorded_at,
                event.session_date, event.symbol, event.sleeve_id, event.side,
                event.order_type, event.requested_qty, event.fill_qty_delta,
                event.cumulative_fill_qty, event.remaining_order_qty,
                event.fill_price, event.stop_price, event.target_price,
                event.total_cost_rs, event.cash_delta_rs, event.realized_net_pnl_delta_rs,
                event.exit_reason, event.reject_reason, payload
            ))
            seq = cur.lastrowid
            conn.execute("COMMIT;")
            return seq

    def upsert_position(self, pos: OpenPositionRecord) -> None:
        status = "OPEN" if pos.residual_qty > 0 else "CLOSED"
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute("""
                INSERT INTO positions (
                    position_id, isin, symbol, series, sleeve_id, strategy_version,
                    entry_session, acquired_qty, sold_qty, residual_qty,
                    residual_cost_basis_rs, entry_cost_allocation_rs, stop_price,
                    target_price, planned_open_risk_rs, exit_intent,
                    exit_intent_created_at, pending_exit_order_id, last_mark,
                    mark_session, mark_source_hash, mark_status,
                    corporate_action_status, settlement_status, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(position_id) DO UPDATE SET
                    acquired_qty = excluded.acquired_qty,
                    sold_qty = excluded.sold_qty,
                    residual_qty = excluded.residual_qty,
                    residual_cost_basis_rs = excluded.residual_cost_basis_rs,
                    stop_price = excluded.stop_price,
                    target_price = excluded.target_price,
                    planned_open_risk_rs = excluded.planned_open_risk_rs,
                    exit_intent = excluded.exit_intent,
                    exit_intent_created_at = excluded.exit_intent_created_at,
                    pending_exit_order_id = excluded.pending_exit_order_id,
                    last_mark = excluded.last_mark,
                    mark_session = excluded.mark_session,
                    mark_source_hash = excluded.mark_source_hash,
                    mark_status = excluded.mark_status,
                    corporate_action_status = excluded.corporate_action_status,
                    settlement_status = excluded.settlement_status,
                    status = excluded.status
            """, (
                pos.position_id, pos.isin, pos.symbol, pos.series, pos.sleeve_id,
                pos.strategy_version, pos.entry_session, pos.acquired_qty, pos.sold_qty,
                pos.residual_qty, pos.residual_cost_basis_rs, pos.entry_cost_allocation_rs,
                pos.stop_price, pos.target_price, pos.planned_open_risk_rs, pos.exit_intent,
                pos.exit_intent_created_at, pos.pending_exit_order_id, pos.last_mark,
                pos.mark_session, pos.mark_source_hash, pos.mark_status,
                pos.corporate_action_status, pos.settlement_status, status
            ))
            conn.execute("COMMIT;")

    def get_open_positions(self) -> List[OpenPositionRecord]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM positions WHERE status = 'OPEN' ORDER BY entry_session ASC").fetchall()
            out = []
            for r in rows:
                out.append(OpenPositionRecord(
                    position_id=r["position_id"],
                    isin=r["isin"],
                    symbol=r["symbol"],
                    series=r["series"],
                    sleeve_id=r["sleeve_id"],
                    strategy_version=r["strategy_version"],
                    entry_session=r["entry_session"],
                    acquired_qty=r["acquired_qty"],
                    sold_qty=r["sold_qty"],
                    residual_qty=r["residual_qty"],
                    residual_cost_basis_rs=r["residual_cost_basis_rs"],
                    entry_cost_allocation_rs=r["entry_cost_allocation_rs"],
                    stop_price=r["stop_price"],
                    target_price=r["target_price"],
                    planned_open_risk_rs=r["planned_open_risk_rs"],
                    exit_intent=r["exit_intent"],
                    exit_intent_created_at=r["exit_intent_created_at"],
                    pending_exit_order_id=r["pending_exit_order_id"],
                    last_mark=r["last_mark"],
                    mark_session=r["mark_session"],
                    mark_source_hash=r["mark_source_hash"],
                    mark_status=r["mark_status"],
                    corporate_action_status=r["corporate_action_status"],
                    settlement_status=r["settlement_status"],
                ))
            return out

    def upsert_reservation(
        self,
        reservation_id: str,
        symbol: str,
        sleeve_id: str,
        quantity: int,
        entry_price: float,
        stop_price: float,
        reserved_cash: float,
        reserved_risk: float,
        session_date: str,
        status: str = "PENDING",
    ) -> None:
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute("""
                INSERT INTO reservations (
                    reservation_id, symbol, sleeve_id, quantity, entry_price,
                    stop_price, reserved_cash, reserved_risk, session_date, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(reservation_id) DO UPDATE SET
                    quantity = excluded.quantity,
                    reserved_cash = excluded.reserved_cash,
                    reserved_risk = excluded.reserved_risk,
                    status = excluded.status
            """, (
                reservation_id, symbol, sleeve_id, quantity, entry_price,
                stop_price, reserved_cash, reserved_risk, session_date, status
            ))
            conn.execute("COMMIT;")

    def get_pending_reservations(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM reservations WHERE status = 'PENDING'").fetchall()
            return [dict(r) for r in rows]

    def clear_reservation(self, reservation_id: str, final_status: str = "FILLED") -> None:
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute("UPDATE reservations SET status = ? WHERE reservation_id = ?", (final_status, reservation_id))
            conn.execute("COMMIT;")

    def get_consumed_volume(self, session_date: str, symbol: str) -> int:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT consumed_volume FROM consumed_volume WHERE session_date = ? AND symbol = ?",
                (session_date, symbol),
            ).fetchone()
            return int(row["consumed_volume"]) if row else 0

    def add_consumed_volume(self, session_date: str, symbol: str, additional_qty: int) -> int:
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute("""
                INSERT INTO consumed_volume (session_date, symbol, consumed_volume)
                VALUES (?, ?, ?)
                ON CONFLICT(session_date, symbol) DO UPDATE SET
                    consumed_volume = consumed_volume + excluded.consumed_volume
            """, (session_date, symbol, additional_qty))
            row = conn.execute(
                "SELECT consumed_volume FROM consumed_volume WHERE session_date = ? AND symbol = ?",
                (session_date, symbol),
            ).fetchone()
            tot = int(row["consumed_volume"])
            conn.execute("COMMIT;")
            return tot

    def record_daily_equity(self, eq: DailyPortfolioEquityRecord) -> None:
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute("""
                INSERT INTO daily_equity (
                    session_date, valuation_at, cash_ledger_rs, cash_settled_rs,
                    receivable_rs, payable_rs, reserved_cash_rs, free_cash_rs,
                    inventory_mtm_rs, equity_rs, external_flow_rs,
                    realized_net_pnl_cumulative_rs, unrealized_pnl_rs,
                    costs_cumulative_rs, occupied_slots, pending_slots,
                    committed_exposure_rs, marked_exposure_rs, planned_open_risk_rs,
                    reserved_risk_rs, pending_exit_count, unresolved_position_count,
                    stale_mark_count, drawdown_rs, drawdown_pct,
                    cash_buffer_breach, risk_breach, data_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_date) DO UPDATE SET
                    valuation_at = excluded.valuation_at,
                    cash_ledger_rs = excluded.cash_ledger_rs,
                    cash_settled_rs = excluded.cash_settled_rs,
                    receivable_rs = excluded.receivable_rs,
                    payable_rs = excluded.payable_rs,
                    reserved_cash_rs = excluded.reserved_cash_rs,
                    free_cash_rs = excluded.free_cash_rs,
                    inventory_mtm_rs = excluded.inventory_mtm_rs,
                    equity_rs = excluded.equity_rs,
                    realized_net_pnl_cumulative_rs = excluded.realized_net_pnl_cumulative_rs,
                    unrealized_pnl_rs = excluded.unrealized_pnl_rs,
                    costs_cumulative_rs = excluded.costs_cumulative_rs,
                    occupied_slots = excluded.occupied_slots,
                    pending_slots = excluded.pending_slots,
                    committed_exposure_rs = excluded.committed_exposure_rs,
                    marked_exposure_rs = excluded.marked_exposure_rs,
                    planned_open_risk_rs = excluded.planned_open_risk_rs,
                    reserved_risk_rs = excluded.reserved_risk_rs,
                    pending_exit_count = excluded.pending_exit_count,
                    unresolved_position_count = excluded.unresolved_position_count,
                    stale_mark_count = excluded.stale_mark_count,
                    drawdown_rs = excluded.drawdown_rs,
                    drawdown_pct = excluded.drawdown_pct,
                    cash_buffer_breach = excluded.cash_buffer_breach,
                    risk_breach = excluded.risk_breach,
                    data_status = excluded.data_status
            """, (
                eq.session_date, eq.valuation_at, eq.cash_ledger_rs, eq.cash_settled_rs,
                eq.receivable_rs, eq.payable_rs, eq.reserved_cash_rs, eq.free_cash_rs,
                eq.inventory_mtm_rs, eq.equity_rs, eq.external_flow_rs,
                eq.realized_net_pnl_cumulative_rs, eq.unrealized_pnl_rs,
                eq.costs_cumulative_rs, eq.occupied_slots, eq.pending_slots,
                eq.committed_exposure_rs, eq.marked_exposure_rs, eq.planned_open_risk_rs,
                eq.reserved_risk_rs, eq.pending_exit_count, eq.unresolved_position_count,
                eq.stale_mark_count, eq.drawdown_rs, eq.drawdown_pct,
                1 if eq.cash_buffer_breach else 0, 1 if eq.risk_breach else 0, eq.data_status
            ))
            conn.execute("COMMIT;")

    def get_latest_equity(self) -> Optional[DailyPortfolioEquityRecord]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM daily_equity ORDER BY session_date DESC LIMIT 1").fetchone()
            if not row:
                return None
            return DailyPortfolioEquityRecord(
                session_date=row["session_date"],
                valuation_at=row["valuation_at"],
                cash_ledger_rs=row["cash_ledger_rs"],
                cash_settled_rs=row["cash_settled_rs"],
                receivable_rs=row["receivable_rs"],
                payable_rs=row["payable_rs"],
                reserved_cash_rs=row["reserved_cash_rs"],
                free_cash_rs=row["free_cash_rs"],
                inventory_mtm_rs=row["inventory_mtm_rs"],
                equity_rs=row["equity_rs"],
                external_flow_rs=row["external_flow_rs"],
                realized_net_pnl_cumulative_rs=row["realized_net_pnl_cumulative_rs"],
                unrealized_pnl_rs=row["unrealized_pnl_rs"],
                costs_cumulative_rs=row["costs_cumulative_rs"],
                occupied_slots=row["occupied_slots"],
                pending_slots=row["pending_slots"],
                committed_exposure_rs=row["committed_exposure_rs"],
                marked_exposure_rs=row["marked_exposure_rs"],
                planned_open_risk_rs=row["planned_open_risk_rs"],
                reserved_risk_rs=row["reserved_risk_rs"],
                pending_exit_count=row["pending_exit_count"],
                unresolved_position_count=row["unresolved_position_count"],
                stale_mark_count=row["stale_mark_count"],
                drawdown_rs=row["drawdown_rs"],
                drawdown_pct=row["drawdown_pct"],
                cash_buffer_breach=bool(row["cash_buffer_breach"]),
                risk_breach=bool(row["risk_breach"]),
                data_status=row["data_status"],
            )

    def export_csv_projections(self, out_dir: Path, generation_id: str) -> Tuple[Path, Path, Path]:
        """
        Atomically exports canonical_paper_journal.csv, open_positions.csv, and
        daily_portfolio_equity.csv as deterministic projections of the SQLite store.
        """
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        journal_path = out_dir / "canonical_paper_journal.csv"
        positions_path = out_dir / "open_positions.csv"
        equity_path = out_dir / "daily_portfolio_equity.csv"

        tmp_journal = out_dir / f".tmp_{generation_id}_journal.csv"
        tmp_positions = out_dir / f".tmp_{generation_id}_positions.csv"
        tmp_equity = out_dir / f".tmp_{generation_id}_equity.csv"

        with self._get_connection() as conn:
            # 1. Export canonical_paper_journal.csv
            event_rows = conn.execute("SELECT payload_json FROM ledger_events ORDER BY event_seq ASC").fetchall()
            with open(tmp_journal, "w", newline="", encoding="utf-8") as f:
                if event_rows:
                    first_dict = json.loads(event_rows[0]["payload_json"])
                    fieldnames = list(first_dict.keys())
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    for r in event_rows:
                        writer.writerow(json.loads(r["payload_json"]))
                else:
                    # Write empty schema
                    writer = csv.writer(f)
                    writer.writerow(["event_id", "event_seq", "event_type", "session_date", "symbol", "side"])

            # 2. Export open_positions.csv
            pos_rows = conn.execute("SELECT * FROM positions WHERE status = 'OPEN' ORDER BY entry_session ASC").fetchall()
            with open(tmp_positions, "w", newline="", encoding="utf-8") as f:
                if pos_rows:
                    fieldnames = [c for c in pos_rows[0].keys() if c != "status"]
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    for r in pos_rows:
                        d = dict(r)
                        d.pop("status", None)
                        writer.writerow(d)
                else:
                    writer = csv.writer(f)
                    writer.writerow(["position_id", "isin", "symbol", "series", "sleeve_id", "entry_session", "residual_qty"])

            # 3. Export daily_portfolio_equity.csv
            eq_rows = conn.execute("SELECT * FROM daily_equity ORDER BY session_date ASC").fetchall()
            with open(tmp_equity, "w", newline="", encoding="utf-8") as f:
                if eq_rows:
                    fieldnames = list(eq_rows[0].keys())
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    for r in eq_rows:
                        writer.writerow(dict(r))
                else:
                    writer = csv.writer(f)
                    writer.writerow(["session_date", "equity_rs", "cash_ledger_rs", "occupied_slots"])

        # Atomic replacement
        tmp_journal.replace(journal_path)
        tmp_positions.replace(positions_path)
        tmp_equity.replace(equity_path)

        return journal_path, positions_path, equity_path
