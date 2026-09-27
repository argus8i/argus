"""
research/decision/ledger.py
===========================
Append-only signal ledger (plan P6.1). SQLite in WAL mode; UPDATE and DELETE are refused by triggers, so a
row, once written, is permanent. Every intent is written whatever its disposition.

Unique key (run_id, mode, strategy_version, symbol, decision_ts); signal_id = sha1 of those five fields.
Storage is gitignored (plan rule 1.2.6): pass a path under TRACK2_HISTORY_DIR or research/outputs/.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

MODES = ("BACKTEST", "SHADOW", "PAPER")
EVIDENCE = ("E1", "E1_CF", "E2", "E3")

# (name, sqlite type); order is the table order (plan P6.1 groups)
COLUMNS: Sequence[tuple] = (
    # identity
    ("signal_id", "TEXT PRIMARY KEY"), ("run_id", "TEXT NOT NULL"), ("mode", "TEXT NOT NULL"),
    ("strategy_id", "TEXT NOT NULL"), ("strategy_version", "TEXT NOT NULL"), ("prereg_sha", "TEXT"),
    # decision
    ("session", "TEXT NOT NULL"), ("decision_ts", "TEXT NOT NULL"), ("symbol", "TEXT NOT NULL"),
    ("side", "TEXT NOT NULL"), ("decision_price", "REAL"), ("entry_ref", "REAL"), ("stop_trigger", "REAL"),
    ("stop_limit", "REAL"), ("t1", "REAL"), ("t2", "REAL"), ("h_eff", "INTEGER"), ("time_exit_ts", "TEXT"),
    # sizing
    ("qty_planned", "INTEGER"), ("notional_planned", "REAL"), ("risk_rs_planned", "REAL"),
    ("r_basis", "TEXT NOT NULL"), ("vix_multiplier", "REAL"),
    # allocation
    ("disposition", "TEXT NOT NULL"), ("allocated", "INTEGER NOT NULL"),
    # fill
    ("entry_fill_price", "REAL"), ("entry_fill_ts", "TEXT"), ("evidence_class", "TEXT NOT NULL"),
    # exit
    ("exit_reason", "TEXT"), ("exit_fills", "TEXT"),
    # rupee P&L
    ("gross_pnl_rs", "REAL"), ("fees_rs", "REAL"), ("slippage_rs", "REAL"), ("rms_fee_rs", "REAL"),
    ("net_pnl_rs", "REAL"),
    # R outcomes
    ("gross_r", "REAL"), ("fee_r", "REAL"), ("slip_r", "REAL"), ("net_r", "REAL"), ("mfe_r", "REAL"),
    ("mae_r", "REAL"),
    # provenance
    ("features", "TEXT"), ("data_hash", "TEXT"), ("config_hash", "TEXT"), ("code_commit", "TEXT"),
)
NAMES = [c[0] for c in COLUMNS]
REQUIRED = ("run_id", "mode", "strategy_id", "strategy_version", "session", "decision_ts", "symbol", "side",
            "r_basis", "disposition", "evidence_class")


class DuplicateSignalError(ValueError):
    pass


def signal_id(run_id: str, mode: str, strategy_version: str, symbol: str, decision_ts: str) -> str:
    return hashlib.sha1("|".join((run_id, mode, strategy_version, symbol, decision_ts)).encode()).hexdigest()


def _iso(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def _clean(v: Any) -> Any:
    if isinstance(v, float) and not math.isfinite(v):
        return None                                  # NaN/inf stored as NULL (never as a number)
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(v, sort_keys=True, default=str)
    if isinstance(v, bool):
        return int(v)
    return _iso(v)


class Ledger:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as con:
            con.execute("PRAGMA journal_mode=WAL")
            cols = ", ".join(f"{n} {t}" for n, t in COLUMNS)
            con.execute(f"CREATE TABLE IF NOT EXISTS signals ({cols}, "
                        "UNIQUE (run_id, mode, strategy_version, symbol, decision_ts))")
            con.execute("CREATE TRIGGER IF NOT EXISTS signals_no_update BEFORE UPDATE ON signals "
                        "BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END")
            con.execute("CREATE TRIGGER IF NOT EXISTS signals_no_delete BEFORE DELETE ON signals "
                        "BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END")
            con.commit()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def append(self, rows: Iterable[Mapping[str, Any]]) -> List[str]:
        """Insert rows atomically; returns their signal_ids. A duplicate key aborts the whole batch."""
        prepared = []
        for r in rows:
            missing = [k for k in REQUIRED if r.get(k) in (None, "")]
            if missing:
                raise ValueError(f"ledger row missing {missing}")
            if r["mode"] not in MODES or r["evidence_class"] not in EVIDENCE:
                raise ValueError(f"bad mode/evidence_class: {r['mode']}/{r['evidence_class']}")
            if r["r_basis"] not in ("trigger", "stop_limit"):
                raise ValueError(f"bad r_basis {r['r_basis']!r}")
            unknown = set(r) - set(NAMES)
            if unknown:
                raise ValueError(f"unknown ledger fields {sorted(unknown)}")
            row = {k: _clean(r.get(k)) for k in NAMES}
            row["signal_id"] = signal_id(str(row["run_id"]), str(row["mode"]), str(row["strategy_version"]),
                                         str(row["symbol"]), str(row["decision_ts"]))
            row["allocated"] = int(bool(r.get("allocated")))
            prepared.append(row)
        with closing(self._connect()) as con:
            try:
                with con:
                    con.executemany(f"INSERT INTO signals ({', '.join(NAMES)}) VALUES "
                                    f"({', '.join('?' for _ in NAMES)})", [[p[k] for k in NAMES] for p in prepared])
            except sqlite3.IntegrityError as exc:
                raise DuplicateSignalError(str(exc)) from exc
        return [p["signal_id"] for p in prepared]

    def rows(self, strategy_id: Optional[str] = None, evidence: Optional[Sequence[str]] = None,
             mode: Optional[str] = None) -> List[Dict[str, Any]]:
        q, args = "SELECT * FROM signals WHERE 1=1", []
        if strategy_id is not None:
            q += " AND strategy_id = ?"
            args.append(strategy_id)
        if mode is not None:
            q += " AND mode = ?"
            args.append(mode)
        if evidence is not None:
            q += f" AND evidence_class IN ({', '.join('?' for _ in evidence)})"
            args.extend(evidence)
        q += " ORDER BY decision_ts, signal_id"
        with closing(self._connect()) as con:
            return [dict(r) for r in con.execute(q, args)]

    def ledger_hash(self, strategy_id: Optional[str] = None) -> str:
        """SHA-256 over the canonical JSON of the (filtered) rows in key order: what a DecisionRecord cites."""
        h = hashlib.sha256()
        for r in self.rows(strategy_id):
            h.update(json.dumps(r, sort_keys=True, default=str).encode())
        return h.hexdigest()


def rows_from_engine(result: Any, *, run_id: str, mode: str, strategy_version: Mapping[str, str],
                     prereg_sha: Optional[str] = None, config_hash: Optional[str] = None,
                     data_hash: Optional[str] = None, code_commit: Optional[str] = None) -> List[Dict[str, Any]]:
    """Ledger rows for every intent of a BacktestEngine run (allocated or not). Allocated intents carry the
    E1 trade; the rest carry their E1_CF counterfactual R. strategy_version maps strategy name -> version."""
    trades = {t.trade_id: t for t in result.trades if getattr(t, "trade_id", None)}
    out: List[Dict[str, Any]] = []
    for s in result.signals:
        t = trades.get(s.trade_id) if s.trade_id else None
        tg = list(s.targets)
        row: Dict[str, Any] = {
            "run_id": run_id, "mode": mode, "strategy_id": s.strategy,
            "strategy_version": strategy_version.get(s.strategy, "unversioned"), "prereg_sha": prereg_sha,
            "session": s.signal_time.date() if s.signal_time else None, "decision_ts": s.signal_time,
            "symbol": s.symbol, "side": s.side, "decision_price": s.entry_ref, "entry_ref": s.entry_ref,
            "stop_trigger": s.stop_loss, "t1": tg[0][0] if tg else None, "t2": tg[1][0] if len(tg) > 1 else None,
            "h_eff": s.max_bars, "qty_planned": s.qty_planned, "r_basis": s.r_basis or "trigger",
            "disposition": s.disposition, "allocated": t is not None,
            "features": s.diagnostics or None, "config_hash": config_hash, "data_hash": data_hash,
            "code_commit": code_commit,
        }
        if t is not None:
            row.update({"stop_limit": t.stop_limit, "entry_fill_price": t.entry_price, "entry_fill_ts": t.entry_time,
                        "evidence_class": t.evidence_class, "exit_reason": t.exit_reason,
                        "exit_fills": [{"price": e.price, "qty": e.qty, "reason": e.reason,
                                        "time": _iso(e.time)} for e in t.exits],
                        "gross_pnl_rs": t.gross_pnl, "fees_rs": t.charges, "slippage_rs": t.slippage_rs,
                        "rms_fee_rs": t.rms_fee, "net_pnl_rs": t.net_pnl, "gross_r": t.gross_r, "fee_r": t.fee_r,
                        "slip_r": t.slip_r, "net_r": t.net_r, "risk_rs_planned": t.risk_rs,
                        "notional_planned": t.entry_price * t.qty})
        else:
            row.update({"evidence_class": s.counterfactual_evidence_class or "E1_CF",
                        "exit_reason": s.counterfactual_exit_reason or None, "net_r": s.counterfactual_net_r})
        out.append(row)
    return out
