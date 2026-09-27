"""P8.2 shadow runner: live-file validation, history freshness, journal chain, reconciliation, live-day
universe, and the replay test (bar-by-bar ticks must equal a one-shot run of the same day)."""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest

from research.shadow import run_day as rd

IST = timezone(timedelta(hours=5, minutes=30))
D = date(2026, 9, 28)
UPSTOX = "https://api.upstox.com/v2/historical-candle/NSE_EQ|X/15minute/2026-09-28/2026-09-28"
KITE = "https://kite.zerodha.com/oms/instruments/historical/256265/15minute?from=2026-09-28"


def _bar(h, m, o=100.0, hi=101.0, lo=99.0, c=100.5, v=1000, day=D):
    return {"timestamp": datetime.combine(day, time(h, m), IST).isoformat(), "open": o, "high": hi, "low": lo,
            "close": c, "volume": v}


def _file(symbols, session=D):
    return json.dumps({"session_date": session.isoformat(), "interval": "15minute", "data_valid": True,
                       "symbols": symbols}).encode()


# ------------------------------------------------------------------ live file
def test_live_file_drops_forbidden_sources_and_forming_bars():
    raw = _file({"AAA": {"source_url": UPSTOX, "bars": [_bar(9, 15), _bar(9, 30), _bar(9, 45)]},
                 "NIFTY50": {"source_url": UPSTOX, "bars": [_bar(9, 15, v=0)]},
                 "BBB": {"source_url": KITE, "bars": [_bar(9, 15)]}})
    live = rd.load_live(raw, D, datetime.combine(D, time(10, 0, 30), IST))
    # at 10:00:30 the 09:45 bar (ends 10:00) is not yet 60 s old: still forming for the runner
    assert [b.start.time() for b in live.bars["AAA"]] == [time(9, 15), time(9, 30)]
    assert live.issues["FORMING_BAR_NOT_USED"] == 1
    assert "IDX:NIFTY50" in live.bars                                      # index names canonicalised
    assert live.dropped == {"BBB": "SOURCE_NOT_ALLOWED:KITE_WEB_SESSION"}   # plan rule 1.2.11
    assert rd.load_live(raw, D, datetime.combine(D, time(10, 1), IST)).bars["AAA"][-1].start.time() == time(9, 45)


@pytest.mark.parametrize("bars,why", [
    ([_bar(9, 15), _bar(9, 15)], "BAR_DUPLICATE"),
    ([_bar(9, 20)], "BAR_OFF_GRID"),
    ([_bar(9, 15, hi=99.5)], "BAR_INVALID"),       # high below open
    ([_bar(9, 15, day=D - timedelta(days=1))], "BAR_OTHER_SESSION"),
    ([{"timestamp": "2026-09-28T09:15:00", "open": 1, "high": 1, "low": 1, "close": 1}], "BAR_TIMESTAMP_NAIVE"),
    ([_bar(9, 15, lo=-1.0)], "BAR_INVALID"),
])
def test_a_bad_bar_drops_the_symbol_for_the_day(bars, why):
    live = rd.load_live(_file({"AAA": {"source_url": UPSTOX, "bars": bars}}), D,
                        datetime.combine(D, time(15, 40), IST))
    assert live.dropped == {"AAA": why} and "AAA" not in live.bars


def test_live_file_for_another_session_is_refused():
    with pytest.raises(rd.ShadowError, match="LIVE_FILE_WRONG_SESSION"):
        rd.load_live(_file({}, session=D - timedelta(days=3)), D, datetime.combine(D, time(10), IST))


# ------------------------------------------------------------------ history freshness
def test_history_must_reach_the_previous_weekday():
    hist = SimpleNamespace(sessions=lambda s: [date(2026, 9, 24), date(2026, 9, 25)])
    assert rd.check_history_fresh(hist, D) == date(2026, 9, 25)             # Fri -> Mon: weekend only
    with pytest.raises(rd.ShadowError, match="HISTORY_STALE"):
        rd.check_history_fresh(hist, date(2026, 9, 30))                     # Mon 28 and Tue 29 missing
    assert rd.check_history_fresh(hist, date(2026, 9, 30),
                                  holidays=[date(2026, 9, 28), date(2026, 9, 29)]) == date(2026, 9, 25)


# ------------------------------------------------------------------ journal
def test_journal_is_hash_chained_and_tamper_evident(tmp_path):
    j = rd.Journal(tmp_path / "j.jsonl")
    j.append([{"kind": "TICK", "n": 1}, {"kind": "SIGNAL", "symbol": "AAA"}])
    j.append([{"kind": "TICK", "n": 2}])
    assert [e["seq"] for e in j.verify()] == [0, 1, 2]
    lines = j.path.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1].replace('"AAA"', '"BBB"')
    j.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(rd.ShadowError, match="JOURNAL_BROKEN at line 2"):
        j.verify()


# ------------------------------------------------------------------ reconciliation
def _sig(sym, hh, mm, side="BUY", entry=100.0):
    return SimpleNamespace(strategy="S", symbol=sym, signal_time=datetime.combine(D, time(hh, mm), IST), side=side,
                           entry_ref=entry, stop_loss=99.0, targets=[(101.0, 0.5), (102.0, 0.5)], max_bars=8)


def test_reconcile_labels_every_case():
    ok, late, changed, unj, gone = _sig("A", 10, 0), _sig("B", 10, 0), _sig("C", 10, 0), _sig("D", 10, 0), _sig("E", 10, 0)
    j = []
    for s, delay in ((ok, 60), (late, 600), (changed, 60), (gone, 60)):
        e = {"kind": "SIGNAL", **rd.signal_record(s),
             "written_at": (s.signal_time + timedelta(seconds=delay)).isoformat()}
        if s is changed:
            e["stop"] = 98.5
        j.append(e)
    status, withdrawn = rd.reconcile([ok, late, changed, unj], j)
    assert status == {rd.signal_key(ok): "PROSPECTIVE", rd.signal_key(late): "SHADOW_LATE",
                      rd.signal_key(changed): "SHADOW_CHANGED", rd.signal_key(unj): "SHADOW_UNJOURNALED"}
    assert [w["symbol"] for w in withdrawn] == ["E"]


# ------------------------------------------------------------------ live-day universe
class _Rich:
    """A history wrapper with 100x daily volume, so DTV20 clears the Rs 30 Cr floor."""

    def __init__(self, h):
        self._h = h

    def __getattr__(self, k):
        return getattr(self._h, k)

    def daily(self, s):
        from research.backtest.bars import DailyBar

        return [DailyBar(x.symbol, x.day, x.open, x.high, x.low, x.close, x.volume * 100) for x in self._h.daily(s)]


def test_live_universe_uses_previous_session_membership_ban_list_and_exclusions():
    from research.tests.synthetic import make_store, sessions

    days = sessions()
    hist = _Rich(make_store())
    day = days[-1]
    raw = rd.live_file_from_history(hist, day, ["AAA", "BBB", "CCC", "DDD"], UPSTOX)
    store = rd.LiveDayStore(hist, rd.load_live(raw, day, datetime.combine(day, time(23), IST)))
    prev = days[-2]
    membership = {"AAA": {prev}, "BBB": {prev}, "CCC": {prev}, "DDD": {days[-10]}}
    u = rd.live_universe(store, membership, banned={"BBB": {day}}, ban_known={day},
                         excluded={"CCC": "wrong company"})
    got = {s: u.check(s, day) for s in ("AAA", "BBB", "CCC", "DDD")}
    assert got == {"AAA": (True, "ELIGIBLE"), "BBB": (False, "FO_BAN"), "CCC": (False, "WRONG_COMPANY_SERIES"),
                   "DDD": (False, "NOT_FNO_MEMBER")}
    assert "FNO_MEMBERSHIP_PREV_SESSION" in u.flags
    unknown = rd.live_universe(store, membership, banned={}, ban_known=set(), excluded={})
    assert unknown.check("AAA", day) == (False, "FO_BAN_UNKNOWN")         # no ban list for D: fail closed


# ------------------------------------------------------------------ replay (the P8.2 acceptance test)
def _replay_inputs(store):
    from research.backtest.universe import PointInTimeUniverse
    from research.features.calibration import CalibrationConfig, CalibrationProvider
    from research.features.events import NoEventsData
    from research.strategies.resid_rev import ResidRevAdapter
    from research.tests.synthetic import SyntheticSpec, sessions
    from research.tests.test_p5_strategies import DRAFT, filled

    days = sessions()
    cfg = CalibrationConfig.from_prereg(DRAFT)
    fmap = {s: "IDX:NIFTYMETAL" for s in SyntheticSpec().stocks}
    uni = PointInTimeUniverse.assumed_static(list("AAA BBB CCC DDD EEE FFF".split()), days[0], days[-1])
    return rd.DayInputs(history=store, universe_inputs={},
                        adapters=lambda: [ResidRevAdapter(filled(z_star=2.0), variant="RESID_REV_NF")],
                        calibration=lambda st: CalibrationProvider(st, fmap, cfg), events=NoEventsData(),
                        universe=uni, history_from=days[0])


def test_replay_bar_by_bar_equals_one_shot_and_the_backtest(tmp_path):
    from research.backtest.engine import BacktestEngine, EngineConfig
    from research.tests.synthetic import make_store, sessions

    store = make_store(overrides={("AAA", 66): {2: 0.985, 3: 0.985, 4: 0.99}})
    day = sessions()[66]
    inp = _replay_inputs(store)
    raw = rd.live_file_from_history(store, day, store.symbols, UPSTOX)
    out = rd.replay(inp, raw, day, rd.Journal(tmp_path / "replay.jsonl"))
    assert out["identical"], out
    assert out["signals"] >= 1 and out["ticks"] >= 23
    # and the journaled decisions are the backtest's decisions for that day
    bt = BacktestEngine(store, inp.universe, inp.adapters(),
                        EngineConfig(var_elm_rate=0.20, allow_shorts=True, r_basis="stop_limit",
                                     stop_limit_offset_pct=0.005),
                        calibration_provider=inp.calibration(store), events_provider=inp.events).run(only_dates=[day])
    journaled = sorted((e["symbol"], e["decision_ts"], e["side"], e["stop"])
                       for e in rd.Journal(tmp_path / "replay.jsonl").verify() if e["kind"] == "SIGNAL")
    assert journaled == sorted((s.symbol, s.signal_time.astimezone(IST).isoformat(), s.side, s.stop_loss)
                               for s in bt.signals)


def test_close_writes_shadow_ledger_rows_once(tmp_path):
    from research.decision.ledger import Ledger
    from research.tests.synthetic import make_store, sessions

    store = make_store(overrides={("AAA", 66): {2: 0.985, 3: 0.985, 4: 0.99}})
    day = sessions()[66]
    inp = _replay_inputs(store)
    raw = rd.live_file_from_history(store, day, store.symbols, UPSTOX)
    j = rd.Journal(tmp_path / "j.jsonl")
    rd.replay(inp, raw, day, j)
    live = rd.load_live(raw, day, datetime.combine(day, time(15, 45), IST))
    summ = rd.close_day(inp, live, j, tmp_path / "ledger.db", {"RESID_REV_NF": "test"},
                        clock=lambda: datetime.combine(day, time(15, 45), IST))
    assert summ["status"] == {"PROSPECTIVE": summ["signals"]} and summ["signals"] >= 1
    rows = Ledger(tmp_path / "ledger.db").rows(mode="SHADOW")
    assert len(rows) == summ["signals"] and all(r["evidence_class"] in ("E1", "E1_CF") for r in rows)
    assert j.verify()[-1]["kind"] == "CLOSE"
    with pytest.raises(rd.ShadowError, match="ALREADY_CLOSED"):
        rd.close_day(inp, live, j, tmp_path / "ledger.db", {"RESID_REV_NF": "test"})
    rep = rd.write_report(summ, tmp_path / "report.md").read_text(encoding="utf-8")
    assert "Paper/research only" in rep and "| RESID_REV_NF | AAA |" in rep


def test_a_signal_journaled_late_is_never_admissible(tmp_path):
    from research.decision.ledger import Ledger
    from research.tests.synthetic import make_store, sessions

    store = make_store(overrides={("AAA", 66): {2: 0.985, 3: 0.985, 4: 0.99}})
    day = sessions()[66]
    inp = _replay_inputs(store)
    raw = rd.live_file_from_history(store, day, store.symbols, UPSTOX)
    j = rd.Journal(tmp_path / "j.jsonl")
    after_close = datetime.combine(day, time(15, 40), IST)
    rd.tick(inp, rd.load_live(raw, day, after_close), j, clock=lambda: after_close)   # one tick, after the fact
    summ = rd.close_day(inp, rd.load_live(raw, day, after_close), j, tmp_path / "ledger.db", {"RESID_REV_NF": "t"},
                        clock=lambda: after_close)
    assert set(summ["status"]) == {"SHADOW_LATE"}
    assert all(r["evidence_class"] == "E1_CF" and r["disposition"].startswith("SHADOW_LATE:")
               for r in Ledger(tmp_path / "ledger.db").rows(mode="SHADOW"))


def test_close_allocates_through_the_shadow_runner(tmp_path):
    import dataclasses

    from research.decision.ledger import Ledger
    from research.shadow.runner import RunnerConfig
    from research.tests.synthetic import make_store, sessions

    store = make_store(overrides={("AAA", 66): {2: 0.985, 3: 0.985, 4: 0.99}})
    day = sessions()[66]
    inp = dataclasses.replace(_replay_inputs(store), runner_config=RunnerConfig(use_vix=False),
                              sectors={"AAA": "METAL"}, clusters=lambda st, d: {"AAA": "C1"})
    raw = rd.live_file_from_history(store, day, store.symbols, UPSTOX)
    j = rd.Journal(tmp_path / "j.jsonl")
    rd.replay(inp, raw, day, j)
    summ = rd.close_day(inp, rd.load_live(raw, day, datetime.combine(day, time(15, 45), IST)), j,
                        tmp_path / "ledger.db", {"RESID_REV_NF": "t"}, clock=lambda: datetime.combine(day, time(15, 45), IST))
    rows = Ledger(tmp_path / "ledger.db").rows(mode="SHADOW")
    assert summ["allocation"]["book_flat"] and summ["allocation"]["signals"] == len(rows) >= 1
    assert all(r["disposition"] == "ALLOCATED" or r["disposition"].startswith("DROPPED:") for r in rows)
    assert any(r["allocated"] and r["evidence_class"] == "E1" and r["vix_multiplier"] == 1.0 for r in rows)


def test_live_store_keeps_the_full_daily_history_for_vix():
    """Regression: the post-CAS cutoff is for intraday sessions only; VIX sizing needs 120 daily closes."""
    from research.backtest.bars import DailyBar

    day = date(2026, 9, 28)
    days = [day - timedelta(days=k) for k in range(400, 0, -1)]
    hist = SimpleNamespace(symbols=["IDX:INDIAVIX"], sessions=lambda s: [],
                           daily=lambda s: [DailyBar(s, d, 14, 15, 13, 14, 0) for d in days])
    live = rd.LiveDay(day, datetime.combine(day, time(10), IST), {}, "x", {}, {})
    st = rd.LiveDayStore(hist, live)
    assert len(st.daily("IDX:INDIAVIX")) == 400 and all(x.day < day for x in st.daily("IDX:INDIAVIX"))
    assert len(st.daily_before("IDX:INDIAVIX", day)) >= 120
