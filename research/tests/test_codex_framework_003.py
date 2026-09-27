"""
CODEX-FRAMEWORK-003 (Codex, 27 Sep 2026, CHANGES_REQUIRED): three probes against dc82ca0, copied unchanged from
shared/trust/artifacts/test_codex_framework_003_probes.py (the first confirms the OS lock across processes and passes;
the other two fail before the fix). Committed before the fixes (Rule 8 v2).
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import time
from datetime import date

from research.data.archive_audit import audit
from research.tests.test_archive_audit import Arch


def test_os_journal_lock_excludes_another_process(tmp_path):
    journal = tmp_path / "journal.jsonl"
    ready = tmp_path / "ready"
    child = ("from pathlib import Path\nimport sys,time\n"
             "from research.framework.desk import journal_lock\n"
             "j,r=map(Path,sys.argv[1:])\n"
             "with journal_lock(j):\n    r.write_text('ready')\n    time.sleep(0.65)\n")
    proc = subprocess.Popen([sys.executable, "-P", "-c", child, str(journal), str(ready)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        until = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < until and proc.poll() is None:
            time.sleep(0.01)
        assert ready.exists(), proc.communicate(timeout=2)
        began = time.monotonic()
        from research.framework.desk import journal_lock

        with journal_lock(journal):
            waited = time.monotonic() - began
        assert waited >= 0.35, f"another process entered after only {waited:.3f}s"
        assert proc.wait(timeout=2) == 0, proc.communicate(timeout=2)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)


def test_required_column_names_with_unusable_prices_cannot_pass(tmp_path):
    a = Arch(tmp_path / "history")
    day = date(2005, 1, 3)
    a.day(day, date(2004, 12, 31))
    cm = next(r for r in a.rows if r["dataset"] == "cm_bhavcopy")
    raw = (b"SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,PREVCLOSE,TOTTRDVAL,TIMESTAMP\n"
           b"ABC,EQ,banana,banana,banana,banana,banana,banana,03-JAN-2005\n")
    (a.h / cm["saved_path"]).write_bytes(raw)
    cm["sha256"] = hashlib.sha256(raw).hexdigest()
    cm["bytes"] = len(raw)
    a.write()
    result = audit(a.h, expected=(day, day))
    assert result["verdict"] != "PASS", "the required price columns are all NaN after parsing"


def test_manifest_404_without_http_404_cannot_certify_a_holiday(tmp_path):
    a = Arch(tmp_path / "history")
    mon, tue = date(2005, 1, 3), date(2005, 1, 4)
    a.day(mon, date(2004, 12, 31))
    for dataset in ("cm_bhavcopy", "fo_bhavcopy", "mto"):
        a.missing(dataset, tue)
    for row in a.rows:
        if row["trade_date"] == tue.isoformat():
            row["http_status"] = 200
    a.write()
    result = audit(a.h, expected=(mon, tue))
    assert result["verdict"] != "PASS", "HTTP 200 entries were accepted as a proven 404 holiday"
