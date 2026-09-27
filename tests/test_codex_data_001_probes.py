"""Failing-first, network-free probes for CODEX-DATA-001."""
from datetime import date, datetime
import subprocess
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from scripts.download_nse_archive import NseArchiveDownloader, StopExecutionError


def response(status, content=b""):
    r = MagicMock()
    r.status_code = status
    r.content = content
    return r


def test_retry_must_not_cross_daily_cap(tmp_path):
    dl = NseArchiveDownloader(base_dir=tmp_path, min_interval=0, daily_cap=1)
    with patch.object(dl.session, "get", side_effect=[response(503), response(404)]) as get, \
         patch("scripts.download_nse_archive.time.sleep"):
        with pytest.raises(StopExecutionError, match="cap"):
            dl.download_file("mto", date(2010, 1, 4))
    assert get.call_count == 1


def test_first_three_days_cap_cannot_be_raised_by_caller(tmp_path):
    """
    Adapted per Yashu's 27 Sep instruction:
    Cap is governed by scripts/nse_cap_policy.json schedule (not flag alone).
    1. On initial 3-day schedule (<= 2026-09-25) or after block, cap cannot exceed 500.
    2. From 27 Sep onwards, cap is 1,000 (970 archive). A caller attempting to raise
       it above policy (e.g. daily_cap=2000) is clamped to policy cap (1000).
    """
    dl_initial = NseArchiveDownloader(base_dir=tmp_path, min_interval=0, daily_cap=1000, policy_date=date(2026, 9, 25))
    assert dl_initial.daily_cap <= 500

    dl_current = NseArchiveDownloader(base_dir=tmp_path, min_interval=0, daily_cap=2000, policy_date=date(2026, 9, 27))
    assert dl_current.daily_cap <= 1000


def test_each_retry_has_its_own_manifest_line(tmp_path):
    dl = NseArchiveDownloader(base_dir=tmp_path, min_interval=0, daily_cap=5)
    with patch.object(dl.session, "get", side_effect=[response(503), response(404)]), \
         patch("scripts.download_nse_archive.time.sleep"):
        dl.download_file("mto", date(2010, 1, 4))
    assert len(dl.load_manifest_records()) == 2


def test_existing_unlogged_file_is_never_overwritten(tmp_path):
    dl = NseArchiveDownloader(base_dir=tmp_path, min_interval=0)
    _, filename, folder = dl.build_url_and_filename("mto", date(2010, 1, 4))
    target = tmp_path / folder / "2010" / filename
    target.parent.mkdir(parents=True)
    target.write_bytes(b"valuable original")
    valid = (b"Security Wise Delivery Position\n"
             b"Trade Date <04-JAN-2010>\n")
    with patch.object(dl.session, "get", return_value=response(200, valid)):
        dl.download_file("mto", date(2010, 1, 4))
    assert target.read_bytes() == b"valuable original"


def test_two_instances_share_pacing_clock(tmp_path):
    a = NseArchiveDownloader(base_dir=tmp_path, min_interval=4.15)
    b = NseArchiveDownloader(base_dir=tmp_path, min_interval=4.15)
    start_a = datetime.fromisoformat(a._pace())
    start_b = datetime.fromisoformat(b._pace())
    assert (start_b - start_a).total_seconds() >= 4.0


def test_block_persists_across_instances_same_day(tmp_path):
    a = NseArchiveDownloader(base_dir=tmp_path, min_interval=0)
    with patch.object(a.session, "get", return_value=response(403)):
        with pytest.raises(StopExecutionError):
            a.download_file("mto", date(2010, 1, 4))
    b = NseArchiveDownloader(base_dir=tmp_path, min_interval=0)
    with patch.object(b.session, "get", return_value=response(404)) as get:
        with pytest.raises(StopExecutionError, match="403|block"):
            b.download_file("mto", date(2010, 1, 5))
    get.assert_not_called()


def _daily_branch_module():
    source = subprocess.check_output(["git", "show", "ops/daily-pipeline:scripts/daily_pipeline.py"],
                                     text=True)
    module = types.ModuleType("_codex_daily_branch_probe")
    sys.modules[module.__name__] = module
    exec(compile(source, "ops/daily-pipeline:scripts/daily_pipeline.py", "exec"), module.__dict__)
    return module


def test_daily_branch_writes_to_paper_consumed_layout(tmp_path):
    module = _daily_branch_module()
    dl = module.DailyPipelineDownloader(root_dir=tmp_path, pause_seconds=0)
    item = module.build_request_item("cm_bhavcopy", date(2026, 9, 25), date(2026, 9, 28))
    actual, _ = dl.save_file(item, b"raw exchange payload")
    assert actual == tmp_path / "bhavcopy" / "raw" / "cm" / "2026" / "2026-09-25.csv.gz"


def test_daily_branch_next_session_includes_known_weekend(tmp_path):
    module = _daily_branch_module()
    assert module.calculate_next_session_date(date(2026, 1, 30)) == date(2026, 2, 1)
