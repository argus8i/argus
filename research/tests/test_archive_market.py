"""
research/framework/archive.py (ArchiveMarket) and the backtest era rules. Written before the implementation.
Synthetic archive from test_archive_audit.Arch (legacy zips + manifest).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from research.framework import backtest
from research.framework.archive import ArchiveIntegrityError, ArchiveMarket
from research.tests.test_archive_audit import Arch, _weekdays
from research.tests.test_framework import ToyShort


@pytest.fixture()
def arch(tmp_path):
    a = Arch(tmp_path / "history")
    days = _weekdays(date(2005, 3, 1), 8)
    prev = days[0] - timedelta(days=1)
    for d in days:
        a.day(d, prev)
        prev = d
    a.write()
    return a, days


def test_sessions_and_files_come_from_the_manifest(arch):
    a, days = arch
    md = ArchiveMarket(a.h)
    assert md.sessions() == days
    assert md.cm(days[0]).TckrSymb.iloc[0] == "S00" and md.cm(days[0]).TradDt.iloc[0] == days[0].isoformat()
    assert md.chain(days[0], days[1])["ok"] is True


def test_entry_is_the_next_session_and_ban_lists_are_unavailable(arch):
    a, days = arch
    md = ArchiveMarket(a.h)
    assert md.entry_session(days[2]) == (days[3], [])
    assert md.entry_session(days[-1]) == (None, [])
    assert md.ban(days[3]) is None and md.ban_lists is False


def test_a_tampered_file_is_refused(arch):
    a, days = arch
    p = a.h / a.rows[0]["saved_path"]
    p.write_bytes(p.read_bytes() + b"x")
    with pytest.raises(ArchiveIntegrityError):
        ArchiveMarket(a.h).cm(days[0])


def test_only_saved_lines_count(arch):
    a, days = arch
    a.missing("cm_bhavcopy", days[-1] + timedelta(days=1))
    a.write()
    assert ArchiveMarket(a.h).sessions() == days


def test_backtest_eras(arch, tmp_path):
    a, days = arch
    pre = tmp_path / "toy.yaml"
    pre.write_text("id: TOY_SHORT_v1\nstatus: LOCKED_PROSPECTIVE\n", encoding="utf-8")
    s = ToyShort(pre, tmp_path / "p")
    bt = backtest.run(s, ArchiveMarket(a.h), days[0], days[-1])
    assert bt["summary"]["n"] > 0 and bt["era"] == "PLAYGROUND"
    with pytest.raises(backtest.WindowRefused):                  # the sealed archive exam
        backtest.run(s, ArchiveMarket(a.h), date(2013, 12, 1), date(2014, 1, 31))
    with pytest.raises(backtest.WindowRefused):
        backtest.run(s, ArchiveMarket(a.h), date(2016, 1, 1), date(2016, 12, 31))
    with pytest.raises(backtest.WindowRefused):                  # each era has its own source
        backtest.run(s, ArchiveMarket(a.h), date(2022, 3, 1), date(2022, 6, 30))


def test_the_expiry_plugin_assumes_no_ban_only_where_ban_lists_do_not_exist(arch, tmp_path):
    from research.framework.market import MarketFiles
    from research.shadow.expiry_desk import ExpiryReliefV2

    a, days = arch
    s = ExpiryReliefV2()
    assert s.entry_ban(ArchiveMarket(a.h), days[3]) == (set(), ["NO_BAN_LIST_ERA"])
    assert s.entry_ban(MarketFiles(tmp_path / "empty"), days[3]) == (None, [])     # live era: missing blocks


def test_a_backtest_window_the_archive_has_not_downloaded_is_refused(arch, tmp_path):
    a, days = arch
    pre = tmp_path / "toy.yaml"
    pre.write_text("id: TOY_SHORT_v1\nstatus: LOCKED_PROSPECTIVE\n", encoding="utf-8")
    with pytest.raises(backtest.WindowRefused, match="not downloaded"):
        backtest.run(ToyShort(pre, tmp_path / "p"), ArchiveMarket(a.h), days[0], days[-1] + timedelta(days=30))
