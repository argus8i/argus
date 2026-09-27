"""
CODEX-FRAMEWORK-001 (Codex, 27 Sep 2026, CHANGES_REQUIRED): the eight failing-first probes, copied from
shared/trust/artifacts/test_codex_framework_001_probes.py. Committed before the fixes (Rule 8 v2).

Adapted, with the reason:
- A2 (concurrent reconcile): Codex's probe makes both threads meet at a Barrier inside score_plan, so ANY fix that
  serialises reconciliation (the fix it asks for) deadlocks the barrier instead of passing. Same intent here: two
  concurrent reconcilers, a slow scorer, exactly one RESULT.
- A3 (Track 2 market-cap and ASM/GSM gates): kept as a strict xfail until Yashu decides the rule (no market-cap
  source exists; the research evidence never applied either gate). It fails today, as Codex showed.
"""
from __future__ import annotations

import hashlib
import json
import time as _time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest

from research.data.archive_audit import audit
from research.framework import daily, desk, rules
from research.framework.archive import ArchiveIntegrityError, ArchiveMarket
from research.shadow import expiry_desk
from research.shadow.run_day import Journal
from research.tests.test_expiry_desk import _ban
from research.tests.test_expiry_desk import hist as make_history


def test_empty_archive_manifest_cannot_pass_audit(tmp_path):                       # A1
    m = tmp_path / "raw" / "nse_archive" / "manifest.jsonl"
    m.parent.mkdir(parents=True)
    m.write_text("", encoding="utf-8")
    assert audit(tmp_path)["verdict"] == "FAIL"


def test_cached_content_verdict_cannot_certify_invalid_file(tmp_path):             # A5
    raw = b"this is not a bhavcopy"
    rel = "raw/nse_archive/cm_bhavcopy/2010/bad.zip"
    p = tmp_path / rel
    p.parent.mkdir(parents=True)
    p.write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    m = tmp_path / "raw" / "nse_archive" / "manifest.jsonl"
    m.write_text(json.dumps({"dataset": "cm_bhavcopy", "trade_date": "2010-01-04",
                             "outcome": "SAVED", "saved_path": rel, "sha256": sha}) + "\n", encoding="utf-8")
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({f"{rel}|{sha}": {"dates": ["2010-01-04"], "rows": 1}}), encoding="utf-8")
    assert audit(tmp_path, cache=cache)["verdict"] == "FAIL"


def test_archive_market_rechecks_file_after_first_read(tmp_path):                  # A6
    rel = "raw/nse_archive/cm_bhavcopy/2010/test.zip"
    p = tmp_path / rel
    p.parent.mkdir(parents=True)
    p.write_bytes(b"original")
    m = tmp_path / "raw" / "nse_archive" / "manifest.jsonl"
    m.write_text(json.dumps({"dataset": "cm_bhavcopy", "trade_date": "2010-01-04",
                             "outcome": "SAVED", "saved_path": rel,
                             "sha256": hashlib.sha256(b"original").hexdigest()}) + "\n", encoding="utf-8")
    md = ArchiveMarket(tmp_path)
    assert md.cm_path(date(2010, 1, 4)) == p
    p.write_bytes(b"changed")
    with pytest.raises(ArchiveIntegrityError):
        md.cm_path(date(2010, 1, 4))


def test_concurrent_reconcile_cannot_score_same_signal_twice(tmp_path, monkeypatch):  # A2 (adapted, see top)
    jpath = tmp_path / "paper.jsonl"
    Journal(jpath).append([{"kind": "PLAN", "status": "OK", "strategy": "TEST", "plan_day": "2026-09-25",
                            "entry_session": "2026-09-28", "evidence": "PROSPECTIVE",
                            "signals": [{"symbol": "ABC", "side": "BUY"}], "book": ["ABC"]}])

    def slow_score(*args, **kwargs):
        _time.sleep(0.3)
        return "SCORED", ([date(2026, 9, 28)], [{"exit_reason": "TIME", "net_r": 0.1}])

    monkeypatch.setattr(desk, "score_plan", slow_score)
    strategy = SimpleNamespace(id="TEST", hold_sessions=1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(desk.reconcile, strategy, object(), jpath, as_of=date(2026, 9, 28)) for _ in range(2)]
        outcomes = [job.result(timeout=30) for job in jobs]
    assert sum(o["scored"] for o in outcomes) == 1
    assert len([r for r in Journal(jpath).verify() if r.get("kind") == "RESULT"]) == 1


def test_paper_only_scan_rejects_dynamic_broker_order(tmp_path):                   # A7
    p = tmp_path / "plugin.py"
    p.write_text('import importlib\nbroker = importlib.import_module("dhan" + "hq")\n'
                 'getattr(broker, "place" + "_order")({"symbol": "ABC"})\n', encoding="utf-8")
    assert rules.paper_only_scan([p])


def test_backdated_now_cannot_create_prospective_plan(tmp_path):                   # A4
    entry = date(2020, 1, 2)
    prereg = tmp_path / "prereg.yaml"
    prereg.write_text("id: TEST\n", encoding="utf-8")

    class Market:
        def entry_session(self, day):
            return entry, []

        def cm_path(self, d):
            return tmp_path / "absent"

    class Strategy:
        id = "TEST"
        entry_deadline = time(9, 0)

        def prereg_path(self):
            return prereg

        def journal_path(self, replay=False):
            return tmp_path / "journal.jsonl"

        def build_plan(self, md, day, day_after):
            return {"status": "OK", "signals": [{"symbol": "ABC"}], "book": ["ABC"]}

    fake_now = datetime(2020, 1, 1, 17, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    row = desk.plan(Strategy(), Market(), date(2020, 1, 1), tmp_path / "journal.jsonl",
                    now=fake_now, code={"identity": "TEST", "dirty": []})
    assert row["evidence"] != "PROSPECTIVE"


def test_track2_plan_blocks_without_asm_gsm_and_market_cap(tmp_path, monkeypatch):  # A3, resolved by decision A
    """Superseded by Yashu's decision A (27 Sep 2026): AGENTS.md Rule 11 now applies the market-cap band to the ORB
    family only, and the expiry pre-registration adds the ASM/GSM exclusion. So the probe's intent now reads: a
    live-era plan WITHOUT the ASM/GSM lists must block (the market-cap part no longer applies to this strategy)."""
    from research.framework import market

    monkeypatch.setattr(market, "SURVEILLANCE_FROM", date(2026, 1, 1))
    fixture = make_history.__wrapped__(tmp_path)
    entry = fixture["days"][21]
    _ban(fixture["history"], entry, [])
    plan = expiry_desk.build_plan(fixture["history"], fixture["expiry"], entry)
    assert plan["status"] == "BLOCKED"


def test_daily_missing_archive_audit_is_not_all_clean(tmp_path):                  # A8
    report = daily.run(date(2026, 9, 27), history=tmp_path, strategies=[], write=False,
                       code={"identity": "TEST", "dirty": []})
    assert report["archive_audit"]["verdict"] is None
    assert report["exit_code"] != daily.EXIT_OK
