"""
The paper-strategy framework (research/framework). Written before the implementation.
A toy strategy (TOY_SHORT: sell every stock that closed above Rs 100, hold 2 sessions) proves the desk is generic:
different side, different hold, a plan every session. Synthetic NSE UDiFF files in a temporary history folder.
"""
from __future__ import annotations

import gzip
import io
import json
import math
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from research.framework import daily, desk, evaluate, rules
from research.framework.market import MarketFiles
from research.framework.strategy import PaperStrategy
from research.shadow.run_day import Journal, ShadowError

IST = timezone(timedelta(hours=5, minutes=30))
COLS = ["TradDt", "Sgmt", "FinInstrmTp", "TckrSymb", "SctySrs", "XpryDt", "OpnPric", "HghPric", "LwPric", "ClsPric",
        "PrvsClsgPric", "TtlTrfVal"]
CLEAN = {"identity": "TEST", "dirty": []}


def _write(path: Path, rows: list) -> None:
    df = pd.DataFrame([{c: r.get(c, "") for c in COLS} for r in rows], columns=COLS)
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    path.write_bytes(gzip.compress(buf.getvalue().encode("utf-8")))


def _ban(h: Path, d: date, syms=()) -> None:
    body = "Securities in Ban For Trade Date " + d.strftime("%d-%b-%Y").upper() + ":\n" + \
           "".join(f"{i + 1},{s}\n" for i, s in enumerate(syms))
    p = h / "raw" / "nse" / "fo_ban" / f"{d.isoformat()}_{d.isoformat()}.csv.gz"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(gzip.compress(body.encode("utf-8")))


def _weekdays(start: date, n: int) -> list:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _market(h: Path, days: list, skip=()) -> None:
    """30 stocks; S00..S29 at 90 + k, each rising 1% a session. PrvsClsgPric always chains to the prior file's close,
    including across a skipped day (the skipped day's move is lost, exactly as a missing file would lose it)."""
    px = {f"S{k:02d}": 90.0 + k for k in range(30)}
    for d in days:
        prev = dict(px)
        for s in px:
            px[s] = prev[s] * 1.01
        if d in skip:
            continue
        rows = [{"TradDt": d.isoformat(), "Sgmt": "CM", "FinInstrmTp": "STK", "TckrSymb": s, "SctySrs": "EQ",
                 "OpnPric": prev[s], "HghPric": px[s], "LwPric": prev[s], "ClsPric": px[s], "PrvsClsgPric": prev[s],
                 "TtlTrfVal": 5e8} for s in px]
        _write(h / "bhavcopy" / "raw" / "cm" / str(d.year) / f"{d.isoformat()}.csv.gz", rows)
        _write(h / "bhavcopy" / "raw" / "fo" / str(d.year) / f"{d.isoformat()}.csv.gz",
               [{"TradDt": d.isoformat(), "Sgmt": "FO", "FinInstrmTp": "STF", "TckrSymb": s,
                 "XpryDt": days[-1].isoformat()} for s in px])
        _ban(h, d)


class ToyShort(PaperStrategy):
    id = "TOY_SHORT_v1"
    hold_sessions = 2
    evaluation = {"review_after": 12, "futility_after": 6, "t_pass": 2.0}

    def __init__(self, prereg: Path, journal_dir: Path) -> None:
        self._prereg, self._journal_dir = prereg, journal_dir

    def prereg_path(self) -> Path:
        return self._prereg

    def journal_path(self, replay: bool = False) -> Path:
        return self._journal_dir / ("replay_journal.jsonl" if replay else "journal.jsonl")

    def is_plan_day(self, md: MarketFiles, day: date) -> bool:
        return day in md.sessions()

    def build_plan(self, md, day, entry):
        cm = md.cm(day)
        sig = [{"symbol": s, "side": "SELL", "close": float(c)} for s, c in zip(cm.TckrSymb, cm.ClsPric) if c > 100]
        return {"status": "OK", "signals": sig, "book": [s["symbol"] for s in sig[:3]], "data_files": {}}

    def score(self, md, plan_row, signal, hold):
        o = float(md.cm(hold[0]).set_index("TckrSymb").OpnPric[signal["symbol"]])
        c = float(md.cm(hold[-1]).set_index("TckrSymb").ClsPric[signal["symbol"]])
        return {"exit_reason": "TIME", "net_r": (o - c) / o * 10}


@pytest.fixture()
def env(tmp_path):
    h = tmp_path / "history"
    days = _weekdays(date(2026, 8, 3), 8)
    _market(h, days)
    pre = tmp_path / "toy.yaml"
    pre.write_text("id: TOY_SHORT_v1\nstatus: LOCKED_PROSPECTIVE\n", encoding="utf-8")
    return {"h": h, "days": days, "md": MarketFiles(h), "strat": ToyShort(pre, tmp_path / "paper" / "toy"),
            "tmp": tmp_path}


def _evening(d: date) -> datetime:
    return datetime.combine(d, time(20, 0), IST)


# ---------------------------------------------------------------------------------------------- market
def test_chain_is_ok_between_adjacent_sessions(env):
    d = env["days"]
    c = env["md"].chain(d[0], d[1])
    assert c["ok"] is True and c["compared"] == 30 and c["matched"] == 30


def test_chain_breaks_when_a_session_file_is_missing(tmp_path):
    h = tmp_path / "h"
    days = _weekdays(date(2026, 8, 3), 5)
    _market(h, days, skip={days[2]})
    md = MarketFiles(h)
    assert days[2] not in md.sessions()
    assert md.chain(days[1], days[3])["ok"] is False


def test_chain_is_unknown_on_too_few_stocks(tmp_path):
    h = tmp_path / "h"
    d0, d1 = date(2026, 8, 3), date(2026, 8, 4)
    for d, pc in ((d0, 100.0), (d1, 100.0)):
        _write(h / "bhavcopy" / "raw" / "cm" / "2026" / f"{d.isoformat()}.csv.gz",
               [{"TckrSymb": "A", "SctySrs": "EQ", "ClsPric": 100.0, "PrvsClsgPric": pc}])
    assert MarketFiles(h).chain(d0, d1)["ok"] is None


def test_entry_session_needs_a_ban_list_and_records_skipped_weekdays(env):
    h, days, md = env["h"], env["days"], env["md"]
    last = days[-1]                                  # a Wednesday; nothing published after it yet
    assert md.entry_session(last) == (None, [])
    nxt = last + timedelta(days=2)                   # Friday; Thursday skipped (no ban list: a holiday or a gap)
    _ban(h, nxt)
    assert MarketFiles(h).entry_session(last) == (nxt, [last + timedelta(days=1)])


# ---------------------------------------------------------------------------------------------- plan
def test_plan_is_prospective_only_before_the_deadline(env):
    d, md, s = env["days"], env["md"], env["strat"]
    rec = desk.plan(s, md, d[2], now=_evening(d[2]), code=CLEAN)
    assert rec["admissible"] is True and rec["evidence"] == "PROSPECTIVE" and rec["entry_session"] == d[3].isoformat()
    late = desk.plan(s, md, d[3], now=datetime.combine(d[4], time(9, 0), IST), code=CLEAN)
    assert late["admissible"] is False and late["evidence"] == "LATE"


def test_second_plan_for_the_same_day_is_a_duplicate(env):
    d, md, s = env["days"], env["md"], env["strat"]
    desk.plan(s, md, d[2], now=_evening(d[2]), code=CLEAN)
    dup = desk.plan(s, md, d[2], now=_evening(d[2]) + timedelta(minutes=5), code=CLEAN)
    assert dup["status"] == "DUPLICATE" and dup["admissible"] is False


def test_dirty_code_is_never_prospective(env):
    d, md, s = env["days"], env["md"], env["strat"]
    rec = desk.plan(s, md, d[2], now=_evening(d[2]), code={"identity": "X", "dirty": [" M research/x.py"]})
    assert rec["admissible"] is False and rec["inadmissible_reason"] == "CODE_DIRTY"


def test_changed_prereg_after_the_first_plan_is_refused(env):
    d, md, s = env["days"], env["md"], env["strat"]
    desk.plan(s, md, d[1], now=_evening(d[1]), code=CLEAN)
    s.prereg_path().write_text("id: TOY_SHORT_v1\nstatus: LOCKED_PROSPECTIVE\nstop: 9\n", encoding="utf-8")
    rec = desk.plan(s, md, d[2], now=_evening(d[2]), code=CLEAN)
    assert rec["status"] == "REFUSED" and "pre-registration changed" in rec["reason"]


def test_missing_lookback_session_blocks_the_plan(tmp_path):
    h = tmp_path / "h"
    days = _weekdays(date(2026, 8, 3), 6)
    _market(h, days, skip={days[3]})
    md = MarketFiles(h)
    assert md.chain_window([days[2], days[4]])["ok"] is False
    assert md.chain_window(days[:3])["ok"] is True


# ---------------------------------------------------------------------------------------------- reconcile
def test_reconcile_scores_once_after_the_hold(env):
    d, md, s = env["days"], env["md"], env["strat"]
    desk.plan(s, md, d[2], now=_evening(d[2]), code=CLEAN)
    assert desk.reconcile(s, md, as_of=d[3])["scored"] == 0                 # hold is d3, d4
    out = desk.reconcile(s, md, as_of=d[4])
    assert out["scored"] > 0 and out["blocked"] == []
    assert desk.reconcile(s, md, as_of=d[5])["scored"] == 0
    res = [r for r in Journal(s.journal_path()).verify() if r["kind"] == "RESULT"]
    assert {r["exit_session"] for r in res} == {d[4].isoformat()} and all(r["evidence"] == "PROSPECTIVE" for r in res)
    assert all(r["net_r"] < 0 for r in res)                                  # shorting a rising market loses


def test_a_gap_inside_the_hold_blocks_scoring(tmp_path):
    h = tmp_path / "h"
    days = _weekdays(date(2026, 8, 3), 7)
    _market(h, days, skip={days[4]})
    pre = tmp_path / "toy.yaml"
    pre.write_text("id: TOY_SHORT_v1\nstatus: LOCKED_PROSPECTIVE\n", encoding="utf-8")
    s, md = ToyShort(pre, tmp_path / "paper"), MarketFiles(h)
    desk.plan(s, md, days[2], now=_evening(days[2]), code=CLEAN)
    out = desk.reconcile(s, md, as_of=days[6])
    assert out["scored"] == 0 and out["blocked"] and out["blocked"][0]["reason"] == "DATA_GAP"


def test_only_the_first_ok_plan_of_a_day_is_scored(env):
    d, md, s = env["days"], env["md"], env["strat"]
    first = desk.plan(s, md, d[2], now=_evening(d[2]), code=CLEAN)
    # a second OK row for the same day written by an older desk version (bypasses the duplicate guard)
    Journal(s.journal_path()).append([{**{k: v for k, v in first.items() if k not in ("seq", "prev_hash", "hash")}}])
    desk.reconcile(s, md, as_of=d[5])
    res = [r for r in Journal(s.journal_path()).verify() if r["kind"] == "RESULT"]
    assert {r["plan_seq"] for r in res} == {first["seq"]}


# ---------------------------------------------------------------------------------------------- evaluate
def test_decide_continue_futile_pass_fail():
    assert evaluate.decide([], [], review_after=12, futility_after=6)["decision"] == "NO_DATA"
    few = evaluate.decide([0.1, -0.2, 0.3], ["a", "b", "c"], review_after=12, futility_after=6)
    assert few["decision"] == "CONTINUE"
    bad = [-0.5, -0.6, -0.4, -0.55, -0.45, -0.5, -0.52]
    fut = evaluate.decide(bad, list("abcdefg"), review_after=12, futility_after=6)
    assert fut["decision"] == "STOP_FUTILE"
    good = [0.5, 0.6, 0.4, 0.55, 0.45, 0.5, 0.52, 0.48, 0.6, 0.41, 0.5, 0.49]
    assert evaluate.decide(good, list("abcdefghijkl"), review_after=12, futility_after=6)["decision"] == "PASS"
    noisy = [1, -1] * 6
    assert evaluate.decide(noisy, list("abcdefghijkl"), review_after=12, futility_after=6)["decision"] == "FAIL"


def test_decide_is_fail_closed_on_non_finite_values():
    d = evaluate.decide([0.5] * 11 + [math.nan], list("abcdefghijkl"), review_after=12, futility_after=6)
    assert d["decision"] == "INVALID"


def test_summary_counts_only_prospective_evidence(env):
    d, md, s = env["days"], env["md"], env["strat"]
    desk.plan(s, md, d[1], now=_evening(d[1]), code=CLEAN)
    desk.plan(s, md, d[2], now=datetime.combine(d[3], time(10, 0), IST), code=CLEAN)       # LATE
    desk.reconcile(s, md, as_of=d[6])
    out = desk.summary(s)
    assert out["PROSPECTIVE"]["trades"] > 0 and out["LATE"]["trades"] > 0
    assert out["decision"]["stats"]["n"] == out["PROSPECTIVE"]["trades"]
    assert out["integrity"] == []


# ---------------------------------------------------------------------------------------------- rules
def test_paper_only_scan_flags_broker_code(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("from dhanhq import dhanhq\nx = client.place_order(1)\n", encoding="utf-8")
    good = tmp_path / "good.py"
    good.write_text("# place an order? never. paper only\nx = 1\n", encoding="utf-8")
    hits = rules.paper_only_scan([bad, good])
    assert {h["line"] for h in hits} == {1, 2} and all(h["file"] == str(bad) for h in hits)


def test_framework_and_desks_contain_no_broker_code():
    root = Path(__file__).resolve().parents[1]
    files = sorted((root / "framework").glob("*.py")) + [root / "shadow" / "expiry_desk.py"]
    assert rules.paper_only_scan(files) == []


def test_journal_must_live_in_the_track2_paper_folder(tmp_path):
    assert rules.journal_location_problem(Path("C:/x/shared/track2_liquid/paper/a/journal.jsonl")) is None
    assert rules.journal_location_problem(Path("C:/x/CHATGPT/observation_log.csv")) is not None
    assert rules.journal_location_problem(Path("C:/x/shared/track1_esm/paper/j.jsonl")) is not None


def test_strategy_problems_list_an_unlocked_prereg(env):
    s = env["strat"]
    s.prereg_path().write_text("id: TOY_SHORT_v1\nstatus: DRAFT\n", encoding="utf-8")
    probs = rules.strategy_problems(s, check_git=False, check_location=False)
    assert any("LOCKED_PROSPECTIVE" in p for p in probs)


# ---------------------------------------------------------------------------------------------- daily
def test_daily_run_plans_reconciles_and_reports(env):
    d, s = env["days"], env["strat"]
    rep = None
    for k in range(1, 6):
        rep = daily.run(d[k], history=env["h"], strategies=[s], report_dir=env["tmp"] / "daily",
                        now=_evening(d[k]), code=CLEAN, check_git=False, check_location=False)
    assert rep["exit_code"] == 0, rep
    assert (env["tmp"] / "daily" / f"{d[5].isoformat()}.json").exists()
    st = rep["strategies"][s.id]
    assert st["plan"]["status"] == "OK" and st["reconcile"]["scored"] > 0
    assert json.loads((env["tmp"] / "daily" / f"{d[5].isoformat()}.json").read_text(encoding="utf-8"))["day"] == \
        d[5].isoformat()


def test_daily_run_stops_on_a_broken_journal(env):
    d, s = env["days"], env["strat"]
    daily.run(d[1], history=env["h"], strategies=[s], report_dir=env["tmp"] / "daily", now=_evening(d[1]),
              code=CLEAN, check_git=False, check_location=False)
    p = s.journal_path()
    p.write_text(p.read_text(encoding="utf-8").replace('"OK"', '"0K"', 1), encoding="utf-8")
    with pytest.raises(ShadowError):
        Journal(p).verify()
    rep = daily.run(d[2], history=env["h"], strategies=[s], report_dir=env["tmp"] / "daily", now=_evening(d[2]),
                    code=CLEAN, check_git=False, check_location=False)
    assert rep["exit_code"] == 1 and "JOURNAL_BROKEN" in " ".join(rep["strategies"][s.id]["problems"])
    assert rep["strategies"][s.id].get("plan") is None


def test_daily_run_flags_missing_market_files(env):
    d, s = env["days"], env["strat"]
    later = d[-1] + timedelta(days=1)                # a weekday with no files at all
    rep = daily.run(later, history=env["h"], strategies=[s], report_dir=env["tmp"] / "daily", now=_evening(later),
                    code=CLEAN, check_git=False, check_location=False)
    assert rep["exit_code"] == 1 and any("CM file" in p for p in rep["market"]["problems"])


def test_download_pacing_uses_request_start_times_and_ignores_skips(tmp_path):
    m = tmp_path / "raw" / "nse_archive" / "manifest.jsonl"
    m.parent.mkdir(parents=True)
    rows = [{"outcome": "SAVED", "fetched_at": "2026-09-26T18:22:0%d+05:30" % k, "trade_date": "2010-01-04",
             "dataset": "cm_bhavcopy"} for k in range(3)]                        # old pilot rows: response times only
    rows += [{"outcome": "SKIPPED_ALREADY_SAVED", "requested_at": "", "fetched_at": "2026-09-26T19:14:30+05:30",
              "trade_date": "2005-01-03", "dataset": "mto"}]
    rows += [{"outcome": "SAVED", "requested_at": "2026-09-26T19:14:%02d+05:30" % (35 + 4 * k),
              "fetched_at": "2026-09-26T19:14:%02d+05:30" % (36 + 4 * k), "trade_date": "2005-01-0%d" % (3 + k),
              "dataset": "cm_bhavcopy"} for k in range(4)]
    m.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    d = daily.download_status(tmp_path, date(2026, 9, 26))
    assert d["min_request_gap_s"] == 4.0 and d["warnings"] == []
    assert d["requests_on_day"] == 7 and d["rows_without_request_time"] == 3
    assert d["position"] == "2005-01-06"
    rows.append({"outcome": "SAVED", "requested_at": "2026-09-26T19:14:49+05:30", "fetched_at": "x",
                 "trade_date": "2005-01-07", "dataset": "cm_bhavcopy"})
    m.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    assert "under 4 s" in " ".join(daily.download_status(tmp_path, date(2026, 9, 26))["warnings"])
