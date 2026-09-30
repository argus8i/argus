"""Pillar 2: atomic capacity reservation (R02, R03, R04, R12, R16) and batch arbitration.

The ledger is a SQLite file. Every admission and transition runs inside
BEGIN IMMEDIATE, which takes the database write lock, so the terminal process,
the Telegram process and the OMS cannot interleave a read-check-write. A
threading.RLock inside one process does not give this guarantee.

Capacity is reserved at intent creation and held through every live state.
It is released only by a terminal state that carries the evidence the state
demands (a cancel acknowledgement, exit fills covering the position, an expiry
time that has passed). Rejections are typed return values, never exceptions.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Tuple

from .marketdata import floor_to_tick, on_grid


class ResState(str, Enum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    PRE_ARMED = "PRE_ARMED"
    APPROVED = "APPROVED"
    ROUTED = "ROUTED"
    WORKING = "WORKING"
    PARTIAL = "PARTIAL"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    OPEN_POSITION = "OPEN_POSITION"
    EXITING = "EXITING"
    CLOSED = "CLOSED"
    CANCELLED_CONFIRMED = "CANCELLED_CONFIRMED"
    EXPIRED = "EXPIRED"
    REJECTED_USER = "REJECTED_USER"
    REJECTED_BROKER = "REJECTED_BROKER"
    REJECTED_STALE_FEED = "REJECTED_STALE_FEED"


S = ResState
PRE_FILL = frozenset({S.PENDING_APPROVAL, S.PRE_ARMED, S.APPROVED, S.ROUTED, S.WORKING})
TERMINAL = frozenset({S.CLOSED, S.CANCELLED_CONFIRMED, S.EXPIRED, S.REJECTED_USER,
                      S.REJECTED_BROKER, S.REJECTED_STALE_FEED})
EXPIRABLE = frozenset({S.PENDING_APPROVAL, S.PRE_ARMED, S.APPROVED})
NEEDS_RECONCILE = frozenset({S.ROUTED, S.WORKING, S.PARTIAL, S.CANCEL_REQUESTED, S.EXITING})
ALLOWED: Dict[ResState, frozenset] = {
    S.PENDING_APPROVAL: frozenset({S.PRE_ARMED, S.APPROVED, S.EXPIRED, S.REJECTED_USER}),
    S.PRE_ARMED: frozenset({S.APPROVED, S.EXPIRED, S.REJECTED_USER}),
    S.APPROVED: frozenset({S.ROUTED, S.EXPIRED, S.REJECTED_STALE_FEED, S.REJECTED_USER}),
    S.ROUTED: frozenset({S.WORKING, S.PARTIAL, S.OPEN_POSITION, S.REJECTED_BROKER, S.CANCEL_REQUESTED}),
    S.WORKING: frozenset({S.PARTIAL, S.OPEN_POSITION, S.CANCEL_REQUESTED}),
    S.PARTIAL: frozenset({S.OPEN_POSITION, S.CANCEL_REQUESTED}),
    S.CANCEL_REQUESTED: frozenset({S.CANCELLED_CONFIRMED, S.OPEN_POSITION}),
    S.OPEN_POSITION: frozenset({S.EXITING, S.CLOSED}),
    S.EXITING: frozenset({S.CLOSED}),
}
_TERMINAL_SQL = ",".join(f"'{t.value}'" for t in sorted(TERMINAL, key=lambda x: x.value))


class LedgerError(Exception):
    pass


class ConfigMismatch(LedgerError):
    pass


class IllegalTransition(LedgerError):
    pass


class StaleVersion(LedgerError):
    pass


class EvidenceRequired(LedgerError):
    pass


# ------------------------------------------------------------------- config
@dataclass(frozen=True)
class CapacityConfig:
    """One allocation configuration for every process (R16). Its fingerprint is
    pinned in the ledger; a process with different numbers cannot open it."""
    capital_rs: float = 250_000.0
    cash_buffer_rs: float = 75_000.0
    slots: int = 3
    risk_per_trade_rs: float = 1_500.0
    max_per_sector: int = 2
    stop_limit_offset: float = 0.005
    max_margin_rate: Optional[float] = 0.30

    def __post_init__(self):
        for name in ("capital_rs", "cash_buffer_rs", "risk_per_trade_rs", "stop_limit_offset"):
            v = getattr(self, name)
            if not (isinstance(v, (int, float)) and math.isfinite(v) and v >= 0):
                raise ValueError(f"{name} must be finite and non-negative")
        if self.cash_buffer_rs >= self.capital_rs or self.risk_per_trade_rs <= 0:
            raise ValueError("buffer must be below capital; risk per trade must be positive")
        if not (isinstance(self.slots, int) and self.slots >= 1 and isinstance(self.max_per_sector, int)
                and self.max_per_sector >= 1):
            raise ValueError("slots and max_per_sector must be positive ints")

    @property
    def deployable_rs(self) -> float:
        return self.capital_rs - self.cash_buffer_rs

    @property
    def slot_notional_rs(self) -> float:
        return self.deployable_rs / self.slots

    @property
    def aggregate_risk_rs(self) -> float:
        return self.risk_per_trade_rs * self.slots

    def fingerprint(self) -> str:
        blob = json.dumps(self.__dict__, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()


@dataclass(frozen=True)
class Sizing:
    qty: int
    stop_limit_px: float
    planned_risk_rs: float     # to the stop's LIMIT price, not its trigger
    notional_rs: float
    binding: str               # "RISK" | "NOTIONAL"


def size_position(limit_px: float, stop_trigger: float, tick: float, cfg: CapacityConfig) -> Sizing:
    """Shares = min(risk budget / risk-to-stop-limit, slot notional / limit).

    When the notional cap binds, planned risk is below Rs 1,500: report it, don't hide it.
    """
    if not (on_grid(limit_px, tick) and on_grid(stop_trigger, tick)) or stop_trigger >= limit_px:
        raise ValueError("prices off grid, or stop not below the entry limit")
    stop_limit = floor_to_tick(stop_trigger * (1.0 - cfg.stop_limit_offset), tick)
    per_share = limit_px - stop_limit
    q_risk = math.floor(cfg.risk_per_trade_rs / per_share + 1e-9)
    q_notional = math.floor(cfg.slot_notional_rs / limit_px + 1e-9)
    qty = min(q_risk, q_notional)
    if qty < 1:
        raise ValueError("position size rounds to zero")
    return Sizing(qty, stop_limit, round(qty * per_share, 2), round(qty * limit_px, 2),
                  "RISK" if q_risk <= q_notional else "NOTIONAL")


# ---------------------------------------------------------------- admission
@dataclass(frozen=True)
class Candidate:
    intent_id: str
    symbol: str
    sector: str
    qty: int
    limit_px: float
    stop_limit_px: float
    expires_at: float
    initial_state: ResState = ResState.PENDING_APPROVAL
    margin_rate: Optional[float] = None
    trigger_ts: float = 0.0
    features: Dict[str, float] = field(default_factory=dict, compare=False, hash=False)

    @property
    def risk(self) -> float:
        return self.qty * (self.limit_px - self.stop_limit_px)

    @property
    def notional(self) -> float:
        return self.qty * self.limit_px


class RejectReason(str, Enum):
    OK = "OK"
    INVALID_CANDIDATE = "INVALID_CANDIDATE"
    NO_SESSION_GATE = "NO_SESSION_GATE"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    LEDGER_BUSY = "LEDGER_BUSY"
    DUPLICATE_INTENT = "DUPLICATE_INTENT"
    DUPLICATE_SYMBOL = "DUPLICATE_SYMBOL"
    PER_TRADE_RISK = "PER_TRADE_RISK"
    PER_SLOT_NOTIONAL = "PER_SLOT_NOTIONAL"
    INVALID_MARGIN_RATE = "INVALID_MARGIN_RATE"
    MARGIN_RATE_CEILING = "MARGIN_RATE_CEILING"
    SECTOR_CAP = "SECTOR_CAP"
    SLOTS_FULL = "SLOTS_FULL"
    AGGREGATE_RISK = "AGGREGATE_RISK"
    AGGREGATE_NOTIONAL = "AGGREGATE_NOTIONAL"
    NOT_SELECTED = "NOT_SELECTED"


@dataclass(frozen=True)
class Usage:
    risk_rs: float
    notional_rs: float
    slots: int
    symbols: Tuple[str, ...]
    sectors: Tuple[Tuple[str, int], ...]

    def sector_count(self, sector: str) -> int:
        return dict(self.sectors).get(sector, 0)

    def plus(self, c: Candidate) -> "Usage":
        sec = dict(self.sectors)
        sec[c.sector] = sec.get(c.sector, 0) + 1
        return Usage(self.risk_rs + c.risk, self.notional_rs + c.notional, self.slots + 1,
                     tuple(sorted(self.symbols + (c.symbol,))), tuple(sorted(sec.items())))


@dataclass(frozen=True)
class Admission:
    ok: bool
    reason: RejectReason
    intent_id: str
    detail: str = ""
    usage_after: Optional[Usage] = None


def exposure(state: ResState, qty: int, filled: int, exit_filled: int, limit_px: float,
             avg_fill_px: float, stop_limit_px: float) -> Tuple[float, float, int]:
    """(risk, notional, slot) a reservation holds in `state`."""
    if state in TERMINAL:
        return 0.0, 0.0, 0
    working = qty - filled if state in PRE_FILL or state in (S.PARTIAL, S.CANCEL_REQUESTED) else 0
    open_qty = filled - exit_filled
    risk = working * max(0.0, limit_px - stop_limit_px) + open_qty * max(0.0, avg_fill_px - stop_limit_px)
    notional = working * limit_px + open_qty * avg_fill_px
    return risk, notional, 1


def check_fit(c: Candidate, usage: Usage, cfg: CapacityConfig, tol: float = 1e-6) -> RejectReason:
    if not (isinstance(c.qty, int) and c.qty > 0 and math.isfinite(c.limit_px) and math.isfinite(c.stop_limit_px)
            and 0 < c.stop_limit_px < c.limit_px and c.symbol and c.sector and c.intent_id):
        return RejectReason.INVALID_CANDIDATE
    if c.symbol in usage.symbols:
        return RejectReason.DUPLICATE_SYMBOL
    if c.risk > cfg.risk_per_trade_rs + tol:
        return RejectReason.PER_TRADE_RISK
    if c.notional > cfg.slot_notional_rs + tol:
        return RejectReason.PER_SLOT_NOTIONAL
    if cfg.max_margin_rate is not None:
        m = c.margin_rate
        if not (isinstance(m, (int, float)) and not isinstance(m, bool) and math.isfinite(m) and 0 < m <= 1):
            return RejectReason.INVALID_MARGIN_RATE          # R11: None/NaN/negative/strings rejected
        if m > cfg.max_margin_rate + tol:
            return RejectReason.MARGIN_RATE_CEILING
    if usage.sector_count(c.sector) >= cfg.max_per_sector:
        return RejectReason.SECTOR_CAP
    if usage.slots + 1 > cfg.slots:
        return RejectReason.SLOTS_FULL
    if usage.risk_rs + c.risk > cfg.aggregate_risk_rs + tol:
        return RejectReason.AGGREGATE_RISK
    if usage.notional_rs + c.notional > cfg.deployable_rs + tol:
        return RejectReason.AGGREGATE_NOTIONAL
    return RejectReason.OK


# -------------------------------------------------------------- arbitration
@dataclass(frozen=True)
class EVModel:
    """Fill-conditional expected R per trade, fitted walk-forward on FILLED trades only."""
    weights: Dict[str, float]
    intercept: float
    means: Dict[str, float]
    stds: Dict[str, float]
    n_train: int
    oos_r2: float
    trained_through: str
    min_ev_r: float = 0.0
    min_train: int = 200

    @property
    def calibrated(self) -> bool:
        return self.n_train >= self.min_train and math.isfinite(self.oos_r2)

    def expected_r(self, feats: Dict[str, float]) -> float:
        total = self.intercept
        for k, w in self.weights.items():
            x = feats.get(k)
            if x is None or not math.isfinite(x):
                raise ValueError(f"missing feature {k}")          # fail closed: no silent zero
            total += w * (x - self.means[k]) / (self.stds[k] or 1.0)
        return total


def uncalibrated_key(c: Candidate) -> Tuple:
    """Pre-calibration ordering. Cheap-to-trade first, slow breakouts before spikes,
    stronger relative strength first. Missing features sort last (fail closed)."""
    f = c.features
    inf = math.inf
    return (f.get("spread_atr", inf), f.get("speed_atr", inf), -f.get("rel_strength", -inf), c.symbol)


def select_batch(cands: Sequence[Candidate], usage: Usage, cfg: CapacityConfig,
                 model: Optional[EVModel] = None) -> Tuple[List[str], str, Dict[str, float]]:
    """Choose which of a simultaneous batch get capacity. Order of arrival inside
    the batch window is ignored on purpose: first-come-first-served selects the
    fastest spikes, which is the winner's curse."""
    eligible = [c for c in cands if check_fit(c, Usage(0, 0, 0, (), ()), cfg) is RejectReason.OK]
    if model is not None and model.calibrated:
        scores = {}
        for c in eligible:
            try:
                scores[c.intent_id] = model.expected_r(c.features)
            except ValueError:
                continue
        pool = [c for c in eligible if c.intent_id in scores and scores[c.intent_id] > model.min_ev_r]
        best: Tuple = ()
        best_key: Tuple = (-math.inf,)
        for m in range(min(len(pool), cfg.slots - usage.slots), 0, -1):
            for combo in itertools.combinations(pool, m):
                u, ok = usage, True
                for c in combo:
                    if check_fit(c, u, cfg) is not RejectReason.OK:
                        ok = False
                        break
                    u = u.plus(c)
                if not ok:
                    continue
                key = (round(sum(scores[c.intent_id] for c in combo), 12), -sum(c.risk for c in combo),
                       tuple(sorted((c.intent_id for c in combo), reverse=True)))
                if key > best_key:
                    best_key, best = key, combo
        chosen = sorted(best, key=lambda c: (-scores[c.intent_id], c.symbol))
        return [c.intent_id for c in chosen], "CALIBRATED_EV", scores
    chosen_ids, u = [], usage
    for c in sorted(eligible, key=uncalibrated_key):
        if check_fit(c, u, cfg) is RejectReason.OK:
            chosen_ids.append(c.intent_id)
            u = u.plus(c)
    return chosen_ids, "UNCALIBRATED_RULE", {}


# ------------------------------------------------------------------- ledger
_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reservations (
  intent_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, sector TEXT NOT NULL, state TEXT NOT NULL,
  qty INTEGER NOT NULL CHECK (qty > 0),
  filled INTEGER NOT NULL DEFAULT 0 CHECK (filled >= 0 AND filled <= qty),
  exit_filled INTEGER NOT NULL DEFAULT 0 CHECK (exit_filled >= 0 AND exit_filled <= filled),
  limit_px REAL NOT NULL CHECK (limit_px > 0), stop_limit_px REAL NOT NULL CHECK (stop_limit_px > 0),
  avg_fill_px REAL NOT NULL DEFAULT 0, exit_notional REAL NOT NULL DEFAULT 0,
  expires_at REAL NOT NULL, session_gate TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_live_intent_per_symbol ON reservations(symbol)
  WHERE state NOT IN ({_TERMINAL_SQL});
CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, intent_id TEXT, kind TEXT NOT NULL,
  from_state TEXT, to_state TEXT, payload TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS decisions (seq INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT, intent_id TEXT,
  symbol TEXT, decision TEXT, reason TEXT, method TEXT, score REAL, features TEXT, at REAL);
"""


class ReservationLedger:
    def __init__(self, path: str, config: CapacityConfig, *, busy_timeout_s: float = 5.0):
        self.path, self.config, self.busy_timeout_s = str(path), config, busy_timeout_s
        with self._tx() as con:
            for stmt in [s.strip() for s in _SCHEMA.split(";") if s.strip()]:
                con.execute(stmt)
            stored = self._meta(con, "config_fingerprint")
            if stored is None:
                self._set_meta(con, "config_fingerprint", config.fingerprint())
                self._set_meta(con, "config", json.dumps(config.__dict__, sort_keys=True))
            elif stored != config.fingerprint():
                raise ConfigMismatch("ledger was created with a different allocation config")

    # -- plumbing -------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=self.busy_timeout_s, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
        return con

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            try:
                yield con
            except BaseException:
                con.execute("ROLLBACK")
                raise
            con.execute("COMMIT")
        finally:
            con.close()

    @staticmethod
    def _meta(con, key: str) -> Optional[str]:
        row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return None if row is None else row["value"]

    @staticmethod
    def _set_meta(con, key: str, value: str) -> None:
        con.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value))

    @staticmethod
    def _event(con, intent_id, kind, frm, to, payload: dict, at: float) -> None:
        con.execute("INSERT INTO events(intent_id,kind,from_state,to_state,payload,at) VALUES(?,?,?,?,?,?)",
                    (intent_id, kind, frm, to, json.dumps(payload, sort_keys=True, default=str), at))

    def _usage(self, con) -> Usage:
        risk = notional = 0.0
        slots, syms, sectors = 0, [], {}
        for r in con.execute(f"SELECT * FROM reservations WHERE state NOT IN ({_TERMINAL_SQL})"):
            rk, nt, sl = exposure(S(r["state"]), r["qty"], r["filled"], r["exit_filled"], r["limit_px"],
                                  r["avg_fill_px"], r["stop_limit_px"])
            risk += rk
            notional += nt
            slots += sl
            syms.append(r["symbol"])
            sectors[r["sector"]] = sectors.get(r["sector"], 0) + 1
        return Usage(risk, notional, slots, tuple(sorted(syms)), tuple(sorted(sectors.items())))

    def usage(self) -> Usage:
        with self._tx() as con:
            return self._usage(con)

    def row(self, intent_id: str) -> Dict:
        with self._tx() as con:
            r = con.execute("SELECT * FROM reservations WHERE intent_id=?", (intent_id,)).fetchone()
            if r is None:
                raise LedgerError(f"unknown intent {intent_id}")
            return dict(r)

    # -- session gate and OMS ownership ---------------------------------------
    def open_session(self, trading_date: str, surveillance_sha256: str, now: float) -> str:
        """Called only after the surveillance snapshot for `trading_date` validated."""
        if len(surveillance_sha256) != 64:
            raise ValueError("surveillance hash required")
        gate = f"{trading_date}:{surveillance_sha256}"
        with self._tx() as con:
            self._set_meta(con, "session_gate", gate)
            self._event(con, None, "SESSION_OPEN", None, None, {"gate": gate}, now)
        return gate

    def close_session(self, now: float) -> None:
        with self._tx() as con:
            con.execute("DELETE FROM meta WHERE key='session_gate'")
            self._event(con, None, "SESSION_CLOSED", None, None, {}, now)

    def claim_oms(self, owner: str, now: float, stale_after_s: float = 5.0) -> bool:
        """Exactly one routing OMS. On takeover after a crash, admissions stop until
        live orders are reconciled against the broker (R04). Returns reconcile_required."""
        with self._tx() as con:
            cur, hb = self._meta(con, "oms_owner"), self._meta(con, "oms_heartbeat")
            if cur and cur != owner and hb is not None and now - float(hb) < stale_after_s:
                raise LedgerError(f"OMS {cur} is live")
            self._set_meta(con, "oms_owner", owner)
            self._set_meta(con, "oms_heartbeat", repr(now))
            placeholders = ",".join("?" * len(NEEDS_RECONCILE))
            n = con.execute(f"SELECT COUNT(*) FROM reservations WHERE state IN ({placeholders})",
                            [s.value for s in NEEDS_RECONCILE]).fetchone()[0]
            need = n > 0
            self._set_meta(con, "reconcile_required", "1" if need else "0")
            self._event(con, None, "OMS_CLAIM", None, None, {"owner": owner, "live_orders": n}, now)
            return need

    def heartbeat(self, owner: str, now: float) -> None:
        with self._tx() as con:
            if self._meta(con, "oms_owner") != owner:
                raise LedgerError("not the OMS owner")
            self._set_meta(con, "oms_heartbeat", repr(now))

    def mark_reconciled(self, owner: str, now: float, evidence: dict) -> None:
        if not evidence.get("broker_orderbook_sha256"):
            raise EvidenceRequired("reconciliation needs the broker/paper order-book snapshot hash")
        with self._tx() as con:
            if self._meta(con, "oms_owner") != owner:
                raise LedgerError("not the OMS owner")
            self._set_meta(con, "reconcile_required", "0")
            self._event(con, None, "RECONCILED", None, None, evidence, now)

    # -- admission ------------------------------------------------------------
    def admit(self, c: Candidate, *, now: float, session_gate: str) -> Admission:
        return self.admit_batch([c], now=now, session_gate=session_gate, batch_id=f"single:{c.intent_id}",
                                selector=lambda cs, u, cfg: ([x.intent_id for x in cs], "SINGLE", {}))[0]

    def admit_batch(self, cands: Sequence[Candidate], *, now: float, session_gate: str, batch_id: str,
                    selector: Optional[Callable] = None) -> List[Admission]:
        selector = selector or (lambda cs, u, cfg: select_batch(cs, u, cfg, None))
        try:
            with self._tx() as con:
                gate = self._meta(con, "session_gate")
                if gate is None or gate != session_gate:
                    return [Admission(False, RejectReason.NO_SESSION_GATE, c.intent_id) for c in cands]
                if self._meta(con, "reconcile_required") == "1":
                    return [Admission(False, RejectReason.RECONCILIATION_REQUIRED, c.intent_id) for c in cands]
                usage = self._usage(con)
                chosen, method, scores = selector(list(cands), usage, self.config)
                by_id = {c.intent_id: c for c in cands}
                out: Dict[str, Admission] = {}
                for iid in chosen:
                    c = by_id[iid]
                    reason = RejectReason.OK
                    if c.expires_at <= now or c.initial_state not in (S.PENDING_APPROVAL, S.APPROVED, S.PRE_ARMED):
                        reason = RejectReason.INVALID_CANDIDATE
                    elif con.execute("SELECT 1 FROM reservations WHERE intent_id=?", (iid,)).fetchone():
                        reason = RejectReason.DUPLICATE_INTENT
                    else:
                        reason = check_fit(c, usage, self.config)
                    if reason is RejectReason.OK:
                        con.execute(
                            "INSERT INTO reservations(intent_id,symbol,sector,state,qty,limit_px,stop_limit_px,"
                            "expires_at,session_gate,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (iid, c.symbol, c.sector, c.initial_state.value, c.qty, c.limit_px, c.stop_limit_px,
                             c.expires_at, gate, now, now))
                        self._event(con, iid, "ADMIT", None, c.initial_state.value,
                                    {"risk": c.risk, "notional": c.notional, "batch": batch_id}, now)
                        usage = usage.plus(c)
                        out[iid] = Admission(True, RejectReason.OK, iid, usage_after=usage)
                    else:
                        out[iid] = Admission(False, reason, iid, detail=reason.value)
                for c in cands:
                    if c.intent_id not in out:
                        out[c.intent_id] = Admission(False, RejectReason.NOT_SELECTED, c.intent_id,
                                                     detail=f"lost arbitration ({method})")
                    a = out[c.intent_id]
                    con.execute("INSERT INTO decisions(batch_id,intent_id,symbol,decision,reason,method,score,"
                                "features,at) VALUES(?,?,?,?,?,?,?,?,?)",
                                (batch_id, c.intent_id, c.symbol, "ADMITTED" if a.ok else "REJECTED",
                                 a.reason.value, method, scores.get(c.intent_id),
                                 json.dumps(c.features, sort_keys=True), now))
                return [out[c.intent_id] for c in cands]
        except sqlite3.OperationalError as exc:           # lock timeout: fail closed
            return [Admission(False, RejectReason.LEDGER_BUSY, c.intent_id, detail=str(exc)) for c in cands]

    # -- transitions ----------------------------------------------------------
    def _load(self, con, intent_id: str) -> sqlite3.Row:
        r = con.execute("SELECT * FROM reservations WHERE intent_id=?", (intent_id,)).fetchone()
        if r is None:
            raise LedgerError(f"unknown intent {intent_id}")
        return r

    def _move(self, con, r: sqlite3.Row, to: ResState, now: float, payload: dict, **cols) -> int:
        frm = S(r["state"])
        if to is not frm and to not in ALLOWED.get(frm, frozenset()):
            raise IllegalTransition(f"{r['intent_id']}: {frm.value} -> {to.value}")
        sets = ", ".join([f"{k}=?" for k in cols] + ["state=?", "version=version+1", "updated_at=?"])
        con.execute(f"UPDATE reservations SET {sets} WHERE intent_id=? AND version=?",
                    list(cols.values()) + [to.value, now, r["intent_id"], r["version"]])
        self._event(con, r["intent_id"], "TRANSITION", frm.value, to.value, payload, now)
        return r["version"] + 1

    def transition(self, intent_id: str, to: ResState, *, expected_version: int, now: float,
                   evidence: Optional[dict] = None) -> int:
        ev = dict(evidence or {})
        with self._tx() as con:
            r = self._load(con, intent_id)
            if r["version"] != expected_version:
                raise StaleVersion(f"{intent_id}: version {r['version']} != {expected_version}")
            frm = S(r["state"])
            if to not in ALLOWED.get(frm, frozenset()):
                raise IllegalTransition(f"{intent_id}: {frm.value} -> {to.value}")
            if to is S.EXPIRED and not (frm in EXPIRABLE and now >= r["expires_at"]):
                raise EvidenceRequired("EXPIRED needs an expirable state whose expiry has passed")
            if to is S.APPROVED and frm is S.PRE_ARMED and not ev.get("trigger_snapshot_seq"):
                raise EvidenceRequired("a pre-armed intent is approved only by a trigger snapshot")
            if to in (S.APPROVED, S.ROUTED) and now >= r["expires_at"]:
                raise EvidenceRequired("intent expired: it can only move to EXPIRED")   # R12
            if to is S.ROUTED:
                if not ev.get("order_id"):
                    raise EvidenceRequired("ROUTED needs a broker/paper order id")
                if self._meta(con, "session_gate") != r["session_gate"]:
                    raise EvidenceRequired("session gate changed since admission")
            if to is S.CANCELLED_CONFIRMED:
                if not ev.get("cancel_ack") or ev.get("filled_qty_at_ack") != r["filled"] or r["filled"] != 0:
                    raise EvidenceRequired("cancel needs an ack showing zero fills")
            if to is S.OPEN_POSITION:
                if frm is S.CANCEL_REQUESTED:
                    if r["filled"] <= 0 or not ev.get("cancel_ack"):
                        raise EvidenceRequired("partial position needs fills plus an ack for the remainder")
                elif r["filled"] != r["qty"]:
                    raise EvidenceRequired("OPEN_POSITION from a working order needs the full quantity filled")
            if to is S.CLOSED and (r["filled"] == 0 or r["exit_filled"] != r["filled"]):
                raise EvidenceRequired("CLOSED needs exit fills covering the whole position")   # R05
            cols = {}
            if to is S.OPEN_POSITION and frm is S.CANCEL_REQUESTED:
                cols["qty"] = r["filled"]
            return self._move(con, r, to, now, ev, **cols)

    def record_entry_fill(self, intent_id: str, qty: int, px: float, *, now: float, evidence: dict) -> int:
        if not evidence.get("fill_evidence"):
            raise EvidenceRequired("entry fills need fill evidence (PESSIMISTIC envelope)")
        with self._tx() as con:
            r = self._load(con, intent_id)
            frm = S(r["state"])
            if frm not in (S.ROUTED, S.WORKING, S.PARTIAL, S.CANCEL_REQUESTED):
                raise IllegalTransition(f"no working entry order in {frm.value}")
            if not (isinstance(qty, int) and 0 < qty <= r["qty"] - r["filled"]) or not (math.isfinite(px) and px > 0):
                raise ValueError("fill exceeds working quantity or bad price")
            if px > r["limit_px"] + 1e-9:
                raise ValueError("buy fill above its limit price")
            filled = r["filled"] + qty
            avg = (r["avg_fill_px"] * r["filled"] + px * qty) / filled
            to = frm if frm is S.CANCEL_REQUESTED else (S.OPEN_POSITION if filled == r["qty"] else S.PARTIAL)
            return self._move(con, r, to, now, {**evidence, "qty": qty, "px": px}, filled=filled, avg_fill_px=avg)

    def record_exit_fill(self, intent_id: str, qty: int, px: float, *, now: float, evidence: dict) -> int:
        if not evidence.get("fill_evidence"):
            raise EvidenceRequired("exit fills need fill evidence; an exit instruction is not an exit")
        with self._tx() as con:
            r = self._load(con, intent_id)
            frm = S(r["state"])
            if frm not in (S.OPEN_POSITION, S.EXITING):
                raise IllegalTransition(f"no open position in {frm.value}")
            if not (isinstance(qty, int) and 0 < qty <= r["filled"] - r["exit_filled"]) or not (math.isfinite(px) and px > 0):
                raise ValueError("exit fill exceeds open quantity or bad price")
            exit_filled = r["exit_filled"] + qty
            to = S.CLOSED if exit_filled == r["filled"] else S.EXITING
            return self._move(con, r, to, now, {**evidence, "qty": qty, "px": px},
                              exit_filled=exit_filled, exit_notional=r["exit_notional"] + qty * px)

    def update_stop(self, intent_id: str, new_stop_limit_px: float, *, now: float) -> int:
        """Stops may only tighten (move up for a long)."""
        with self._tx() as con:
            r = self._load(con, intent_id)
            if S(r["state"]) not in (S.OPEN_POSITION, S.EXITING):
                raise IllegalTransition("stop updates apply to open positions")
            if not (math.isfinite(new_stop_limit_px) and new_stop_limit_px >= r["stop_limit_px"]):
                raise ValueError("stops can only be tightened")
            return self._move(con, r, S(r["state"]), now, {"stop_limit_px": new_stop_limit_px},
                              stop_limit_px=new_stop_limit_px)

    def sweep_expired(self, now: float) -> List[str]:
        """Expire PENDING_APPROVAL, PRE_ARMED and un-routed APPROVED intents (R12)."""
        done = []
        with self._tx() as con:
            placeholders = ",".join("?" * len(EXPIRABLE))
            rows = con.execute(f"SELECT * FROM reservations WHERE state IN ({placeholders}) AND expires_at<=?",
                               [s.value for s in EXPIRABLE] + [now]).fetchall()
            for r in rows:
                self._move(con, r, S.EXPIRED, now, {"sweeper": True})
                done.append(r["intent_id"])
        return done
