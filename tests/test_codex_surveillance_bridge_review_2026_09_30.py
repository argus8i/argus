"""Bridge review at 4739d2f against the exact T2-01 research reader. No network."""
import importlib.util
import json
import os
import sys
from datetime import date
from pathlib import Path

OPS = Path(os.environ["ARGUS_REVIEW_OPS_ROOT"])
RESEARCH = Path(os.environ["ARGUS_REVIEW_RESEARCH_ROOT"])
sys.path[:0] = [str(OPS), str(RESEARCH)]
from antigravity.daemons import track2_surveillance_bridge as bridge
from research.framework.market import MarketFiles
from research.tests.test_claude_t2_01_surveillance_session import _ingest, _at

DAY = date(2026, 9, 30)


def test_real_ingestor_bridge_reader_roundtrip(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_SURVEILLANCE_DIR", raising=False)
    op, history = tmp_path / "op", tmp_path / "history"
    receipt = _ingest(op, DAY, _at(date(2026, 9, 29), 19, 5))
    assert receipt["verified"] is True
    result = bridge.sync_surveillance_to_research(str(DAY), op, history)
    assert result["ok"] is True, result
    assert MarketFiles(history, surveillance_dir=tmp_path / "empty").surveillance(DAY) == {"FALL", "NOTAFUTURE"}


def test_bridge_rejects_author_fixture_reader_cannot_verify(tmp_path):
    spec = importlib.util.spec_from_file_location("bridge_author_tests", OPS / "tests/test_track2_surveillance_bridge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    op, history = tmp_path / "op", tmp_path / "history"
    module._setup_operational_fixtures(op, str(DAY))
    result = bridge.sync_surveillance_to_research(str(DAY), op, history)
    assert result["ok"] is False, "Bridge reports success for wrong endpoints and missing content_type"


def test_raw_relative_path_cannot_escape_destination(tmp_path):
    op, history = tmp_path / "op", tmp_path / "history"
    _ingest(op, DAY, _at(date(2026, 9, 29), 19, 5))
    snap_path = op / f"nse_surveillance_snapshot_{DAY}.json"
    snapshot = json.loads(snap_path.read_text())
    source = op / snapshot["sources"]["asm"]["raw_relative_path"]
    (op.parent / "escape.json").write_bytes(source.read_bytes())
    snapshot["sources"]["asm"]["raw_relative_path"] = "../escape.json"
    snap_path.write_text(json.dumps(snapshot))
    result = bridge.sync_surveillance_to_research(str(DAY), op, history)
    assert not (history / "raw/nse/escape.json").exists(), result
    assert result["ok"] is False, result


def test_partial_copy_can_resume_without_overwriting(tmp_path, monkeypatch):
    op, history = tmp_path / "op", tmp_path / "history"
    _ingest(op, DAY, _at(date(2026, 9, 29), 19, 5))
    original = bridge.atomic_write_bytes
    count = [0]
    def interrupt(path, data):
        count[0] += 1
        if count[0] == 3:
            raise OSError("simulated interruption")
        return original(path, data)
    monkeypatch.setattr(bridge, "atomic_write_bytes", interrupt)
    first = bridge.sync_surveillance_to_research(str(DAY), op, history)
    assert first["ok"] is False
    monkeypatch.setattr(bridge, "atomic_write_bytes", original)
    retry = bridge.sync_surveillance_to_research(str(DAY), op, history)
    assert retry["ok"] is True, retry
