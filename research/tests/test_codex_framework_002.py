"""
CODEX-FRAMEWORK-002 (Codex, 27 Sep 2026, CHANGES_REQUIRED): the five failing-first probes against c816589, copied
unchanged from shared/trust/artifacts/test_codex_framework_002_probes.py. Committed before the fixes (Rule 8 v2).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from datetime import date, datetime, time as clock_time, timedelta, timezone
from types import SimpleNamespace

from research.data.archive_audit import audit
from research.framework import daily, desk


def test_live_lock_cannot_be_stolen_only_because_mtime_is_old(tmp_path):
    journal = tmp_path / "journal.jsonl"
    entered = threading.Event()
    with desk.journal_lock(journal):
        lock = tmp_path / "journal.jsonl.lock"
        old = time.time() - desk.LOCK_STALE_S - 1
        os.utime(lock, (old, old))

        def contender():
            with desk.journal_lock(journal):
                entered.set()

        t = threading.Thread(target=contender)
        t.start()
        t.join(timeout=0.3)
        assert not entered.is_set(), "second desk entered while the first still owns the lock"
    t.join(timeout=2)
    assert not t.is_alive()


def test_incomplete_three_dataset_archive_cannot_pass(tmp_path):
    day = date(2010, 1, 4)
    rel = "raw/nse_archive/cm_bhavcopy/2010/cm.csv"
    p = tmp_path / rel
    p.parent.mkdir(parents=True)
    raw = b"SYMBOL,SERIES,TIMESTAMP,CLOSE,PREVCLOSE\nABC,EQ,04-JAN-2010,1,1\n"
    p.write_bytes(raw)
    manifest = tmp_path / "raw/nse_archive/manifest.jsonl"
    manifest.write_text(json.dumps({"dataset": "cm_bhavcopy", "trade_date": day.isoformat(),
                                   "outcome": "SAVED", "saved_path": rel,
                                   "sha256": hashlib.sha256(raw).hexdigest()}) + "\n", encoding="utf-8")
    result = audit(tmp_path, expected=(day, day))
    assert result["verdict"] != "PASS", "F&O and MTO were never downloaded"


def test_date_only_bhavcopy_cannot_pass_as_usable_market_data(tmp_path):
    day = date(2010, 1, 4)
    rows = []
    for dataset, raw in (
        ("cm_bhavcopy", b"TckrSymb,TradDt\nABC,2010-01-04\n"),
        ("fo_bhavcopy", b"TckrSymb,TradDt\nABC,2010-01-04\n"),
        ("mto", b"10,MTO,04012010\n"),
    ):
        rel = f"raw/nse_archive/{dataset}/2010/bad.csv"
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        rows.append({"dataset": dataset, "trade_date": day.isoformat(), "outcome": "SAVED",
                     "saved_path": rel, "sha256": hashlib.sha256(raw).hexdigest()})
    manifest = tmp_path / "raw/nse_archive/manifest.jsonl"
    manifest.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    result = audit(tmp_path, expected=(day, day))
    assert result["verdict"] != "PASS", "date-only files lack every price and series field the desk needs"


def test_injected_clock_switch_cannot_turn_historical_plan_prospective(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, "ALLOW_INJECTED", True)
    prereg = tmp_path / "prereg.yaml"
    prereg.write_text("id: TEST\n", encoding="utf-8")
    entry = date(2020, 1, 2)

    class Market:
        def entry_session(self, day):
            return entry, []

        def cm_path(self, d):
            return tmp_path / "absent"

    class Strategy:
        id = "TEST"
        entry_deadline = clock_time(9, 0)

        def prereg_path(self):
            return prereg

        def build_plan(self, md, day, day_after):
            return {"status": "OK", "signals": [{"symbol": "ABC"}], "book": ["ABC"]}

    fake = datetime(2020, 1, 1, 17, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    row = desk.plan(Strategy(), Market(), date(2020, 1, 1), tmp_path / "journal.jsonl",
                    now=fake, code={"identity": "CLEAN", "dirty": []})
    assert row["evidence"] != "PROSPECTIVE", "mutable global switch accepted a forged clock and code state"


def test_loaded_broker_sdk_stops_daily_planning(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "dhanhq", SimpleNamespace())
    monkeypatch.setattr(daily, "market_status", lambda md, day: {"problems": [], "notes": []})
    monkeypatch.setattr(daily, "download_status", lambda h, day: {"warnings": []})
    monkeypatch.setattr(daily, "archive_audit_status", lambda h, cache=None: {"verdict": "PASS"})
    monkeypatch.setattr(daily.rules, "strategy_problems", lambda *a, **kw: [])
    calls = []
    monkeypatch.setattr(daily.desk, "plan", lambda *a, **kw: calls.append("plan") or {"status": "OK"})
    monkeypatch.setattr(daily.desk, "reconcile", lambda *a, **kw: {"blocked": [], "overdue": []})
    monkeypatch.setattr(daily.desk, "summary", lambda *a, **kw: {"integrity": []})
    strategy = SimpleNamespace(id="TEST", is_plan_day=lambda *a: True)
    daily.run(date(2026, 9, 28), history=tmp_path, strategies=[strategy], write=False,
              code={"identity": "CLEAN", "dirty": []})
    assert not calls, "daily.run continued to plan after detecting a loaded broker SDK"
