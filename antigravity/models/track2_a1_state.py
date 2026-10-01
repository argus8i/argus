"""Cross-process ops admission authority, SQLite write transaction + local RLock.

Every OMS reads the latest projections while holding BEGIN IMMEDIATE. Writes
are fsynced before releasing the transaction. A persistent reconciliation latch
prevents a torn/absent projection from being reinterpreted as an empty account.
This is independent of the research ledger; no research code is modified.
"""
import json
import hashlib
import sqlite3
import threading

from .track2_a1 import MAX_SLOTS, SLOT_CAP_RS, AGGREGATE_EXPOSURE_CAP_RS, RISK_PER_TRADE_RS


class AdmissionLock:
    def __init__(self, owner):
        self.owner = owner
        self.local = threading.RLock()
        self.depth = 0
        self.con = None
        owner.output_dir.mkdir(parents=True, exist_ok=True)
        self.path = owner.output_dir / "a1_authority.sqlite3"

    def __enter__(self):
        self.local.acquire()
        if self.depth:
            self.depth += 1
            return self
        try:
            self.con = sqlite3.connect(self.path, timeout=10, isolation_level=None)
            self.con.execute("PRAGMA synchronous=FULL")
            self.con.execute("BEGIN IMMEDIATE")
            if self.con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("RECONCILIATION_REQUIRED: corrupt SQLite authority")
            self.con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            signature = json.dumps([MAX_SLOTS, SLOT_CAP_RS, AGGREGATE_EXPOSURE_CAP_RS, RISK_PER_TRADE_RS])
            old = self.con.execute("SELECT value FROM meta WHERE key='configuration'").fetchone()
            if old and old[0] != signature:
                raise ValueError("A1_CONFIG_MISMATCH: authority fingerprint")
            self.set("configuration", signature)
            halted = self.con.execute("SELECT value FROM meta WHERE key='halted'").fetchone()
            if halted:
                self.owner._state_error = halted[0]
            for name in ("intents_path", "orders_path"):
                seen = self.con.execute("SELECT value FROM meta WHERE key=?", (name,)).fetchone()
                if seen and not getattr(self.owner, name).is_file():
                    self.owner._state_error = f"RECONCILIATION_REQUIRED: missing {name}"
                if seen and getattr(self.owner, name).is_file():
                    actual = hashlib.sha256(getattr(self.owner, name).read_bytes()).hexdigest()
                    if actual != seen[0]:
                        self.owner._state_error = f"RECONCILIATION_REQUIRED: out-of-authority change to {name}"
            self.owner._load_intents()
            self.owner._load_active_orders()
            self.depth = 1
            return self
        except BaseException:
            if self.con:
                self.con.close()
                self.con = None
            self.local.release()
            raise

    def set(self, key, value):
        self.con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))

    def __exit__(self, kind, value, tb):
        self.depth -= 1
        try:
            if self.depth == 0:
                if self.owner._state_error:
                    self.set("halted", self.owner._state_error)
                if kind is None:
                    for name in ("intents_path", "orders_path"):
                        if getattr(self.owner, name).is_file():
                            self.set(name, hashlib.sha256(getattr(self.owner, name).read_bytes()).hexdigest())
                    self.con.commit()
                else:
                    # Preserve a failure latch even when a caller threw after an IO failure.
                    self.con.rollback()
                    if self.owner._state_error:
                        self.con.execute("BEGIN IMMEDIATE")
                        self.set("halted", self.owner._state_error)
                        self.con.commit()
                self.con.close()
                self.con = None
        finally:
            self.local.release()
