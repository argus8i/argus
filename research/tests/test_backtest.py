"""
research/framework/backtest.py: history through the SAME plug-in code the paper desk runs. Written before the
implementation. Reuses the synthetic market and toy strategy of test_framework.py.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, time, timedelta, timezone

import pytest

from research.framework import backtest, desk
from research.framework.market import MarketFiles
from research.shadow.run_day import Journal
from research.tests.test_framework import CLEAN, ToyShort, _market, _weekdays

IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture()
def env(tmp_path):
    h = tmp_path / "history"
    days = _weekdays(date(2022, 3, 1), 12)
    _market(h, days)
    pre = tmp_path / "toy.yaml"
    pre.write_text("id: TOY_SHORT_v1\nstatus: LOCKED_PROSPECTIVE\n", encoding="utf-8")
    return {"h": h, "days": days, "md": MarketFiles(h), "strat": ToyShort(pre, tmp_path / "paper"), "tmp": tmp_path}


def test_backtest_matches_the_paper_desk_trade_for_trade(env):
    d, md, s = env["days"], env["md"], env["strat"]
    bt = backtest.run(s, md, d[0], d[-1])
    desk.plan(s, md, d[3], now=datetime.combine(d[3], time(20, 0), IST), code=CLEAN)
    desk.reconcile(s, md, as_of=d[-1])
    paper = {(r["symbol"], r["net_r"]) for r in Journal(s.journal_path()).verify() if r["kind"] == "RESULT"}
    bt_day = {(t["symbol"], t["net_r"]) for t in bt["trades"] if t["plan_day"] == d[3].isoformat()}
    assert paper and paper == bt_day


def test_backtest_summary_counts_and_statuses(env):
    d, md, s = env["days"], env["md"], env["strat"]
    bt = backtest.run(s, md, d[0], d[-1])
    st = bt["summary"]
    assert st["n"] == len([t for t in bt["trades"] if t["net_r"] is not None])
    assert st["plan_days"] >= 1 and "2022" in st["by_year"]
    assert bt["plans"]["OK"] >= 1
    assert bt["unfinished_plans"] >= 1                                # the last days cannot finish their hold


def test_the_sealed_holdout_is_refused(env):
    with pytest.raises(backtest.WindowRefused):
        backtest.run(env["strat"], env["md"], date(2024, 9, 1), date(2024, 10, 15))
    with pytest.raises(backtest.WindowRefused):
        backtest.run(env["strat"], env["md"], date(2026, 8, 1), date(2026, 9, 1))


def test_a_data_gap_is_counted_not_scored(tmp_path):
    h = tmp_path / "h"
    days = _weekdays(date(2022, 3, 1), 10)
    _market(h, days, skip={days[5]})
    pre = tmp_path / "toy.yaml"
    pre.write_text("id: TOY_SHORT_v1\nstatus: LOCKED_PROSPECTIVE\n", encoding="utf-8")
    bt = backtest.run(ToyShort(pre, tmp_path / "p"), MarketFiles(h), days[0], days[-1])
    assert bt["blocked"].get("DATA_GAP", 0) >= 1
    assert all(t["plan_day"] != days[4].isoformat() for t in bt["trades"])


def test_register_trial_appends_the_next_id(env, tmp_path):
    reg = tmp_path / "trials.csv"
    reg.write_text("trial_id,date_run,strategy_id,variant,data_span,sample_id,n_trades,mean_net_r,r_basis,"
                   "sr_per_trade,source,agent,notes\nT0130,2026-09-26,X,v,a..b,s,1,0.1,none,,src,claude,n\n",
                   encoding="utf-8")
    bt = backtest.run(env["strat"], env["md"], env["days"][0], env["days"][-1])
    tid = backtest.register_trial(bt, variant="toy check", notes="test", registry=reg, source="test")
    rows = list(csv.DictReader(reg.open(encoding="utf-8")))
    assert tid == "T0131" and rows[-1]["trial_id"] == "T0131" and rows[-1]["strategy_id"] == "TOY_SHORT_v1"
    assert int(rows[-1]["n_trades"]) == bt["summary"]["n"]
