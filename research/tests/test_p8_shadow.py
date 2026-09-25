"""P8 ShadowRunner (Yashu's mandate, 26 Sep 2026): rule 1 refusal, engine-identical emission and sizing,
Adjusted A1 caps with pending reservations, VIX fail-closed, zero look-ahead, a flat book at the close."""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest

from research.backtest.bars import Bar
from research.decision.sizing import AGGREGATE_EXPOSURE_CAP_RS, SLOT_CAP_RS
from research.shadow.runner import LiveTradingRefused, RunnerConfig, ShadowRunner
from research.tests.synthetic import SyntheticSpec, make_store, sessions

IST = timezone(timedelta(hours=5, minutes=30))
DAYS = sessions()
DAY = DAYS[66]
SECTORS = {"AAA": "METAL", "BBB": "BANK", "CCC": "IT", "DDD": "AUTO", "EEE": "PHARMA", "FFF": "FMCG"}
CLUSTERS = lambda d: {s: f"C-{s}" for s in SECTORS}          # every stock its own weekly cluster


class _WithVix:
    """Synthetic store plus INDIA VIX 15-minute bars at `level` (the daily history sits around 14)."""

    def __init__(self, store, level=12.0, days=()):
        self._s, self.level, self.days = store, level, set(days)

    def __getattr__(self, k):
        return getattr(self._s, k)

    @property
    def symbols(self):
        return self._s.symbols

    def bars(self, sym, day):
        if sym == "IDX:INDIAVIX" and (not self.days or day in self.days):
            start = datetime.combine(day, time(9, 15), IST)
            return [Bar(sym, start + timedelta(minutes=15 * k), 15, self.level, self.level, self.level, self.level, 0)
                    for k in range(25)]
        return self._s.bars(sym, day)


class _Truncated:
    """The same store with every bar of DAY after `cut` removed (what a live runner would have seen)."""

    def __init__(self, store, cut):
        self._s, self.cut = store, cut

    def __getattr__(self, k):
        return getattr(self._s, k)

    @property
    def symbols(self):
        return self._s.symbols

    def bars(self, sym, day):
        bs = self._s.bars(sym, day)
        return [b for b in bs if b.end <= self.cut] if day == DAY else bs


def _sig(sym, store, slot, side="BUY", stop_pct=0.01, strategy="RESID_REV"):
    bars = store.bars(sym, DAY)
    b = bars[slot]
    sg = 1 if side == "BUY" else -1
    p0 = b.close
    stop = round(p0 * (1 - sg * stop_pct), 2)
    return SimpleNamespace(strategy=strategy, symbol=sym, side=side, signal_time=b.end, entry_ref=p0, stop_loss=stop,
                           targets=((round(p0 * (1 + sg * 0.01), 2), 0.5), (round(p0 * (1 + sg * 0.02), 2), 0.5)),
                           max_bars=8, priority_score=3.0, diagnostics={}, counterfactual_net_r=0.0,
                           counterfactual_gross_r=0.0, counterfactual_fee_r=0.0, counterfactual_slip_r=0.0,
                           counterfactual_exit_reason="X")


def test_allow_live_is_refused():
    with pytest.raises(LiveTradingRefused, match="rule 1"):
        ShadowRunner(make_store(), SECTORS, config=RunnerConfig(allow_live=True))


def test_adjusted_a1_slots_and_caps_with_pending_reservations():
    store = _WithVix(make_store())
    r = ShadowRunner(store, SECTORS, clusters=CLUSTERS)
    sigs = [_sig(s, store, 5) for s in ("AAA", "BBB", "CCC", "DDD", "EEE")]
    rep, rows = r.process_session(DAY, sigs)
    alloc = [x for x in rows if x["disposition"] == "ALLOCATED"]
    assert len(alloc) == 3 and rep.drops["MAX_SLOTS"] == 2           # 3 slots; pending entries hold them
    assert all(x["notional_planned"] <= SLOT_CAP_RS + 1e-6 for x in alloc)
    assert sum(x["notional_planned"] for x in alloc) <= AGGREGATE_EXPOSURE_CAP_RS + 1e-6
    assert rep.peak_exposure_rs <= AGGREGATE_EXPOSURE_CAP_RS + 1e-6 and rep.book_flat


def test_missing_or_stale_vix_means_no_entries():
    store = make_store()                                             # no intraday VIX at all
    rep, rows = ShadowRunner(store, SECTORS, clusters=CLUSTERS).process_session(DAY, [_sig("AAA", store, 5)])
    assert rep.allocated == 0 and rows[0]["disposition"] == "DROPPED:SIZE_ZERO:VIX_MISSING_OR_INVALID"
    stale = _WithVix(make_store(), days=[DAYS[65]])                   # VIX bars only on the previous day
    rep2, rows2 = ShadowRunner(stale, SECTORS, clusters=CLUSTERS).process_session(DAY, [_sig("AAA", stale, 5)])
    assert rep2.allocated == 0 and rows2[0]["disposition"].startswith("DROPPED:SIZE_ZERO:VIX")


def test_high_vix_scales_quantity_down():
    calm, hot = _WithVix(make_store(), 12.0), _WithVix(make_store(), 28.0)
    q = lambda st: ShadowRunner(st, SECTORS, clusters=CLUSTERS).process_session(DAY, [_sig("AAA", st, 5)])[1][0]["qty_planned"]
    assert 0 < q(hot) < q(calm)                                      # m = clip(ref/VIX, 0.5, 1)


def test_zero_lookahead_decisions_at_t_ignore_later_bars():
    store = _WithVix(make_store())
    early = [_sig("AAA", store, 3), _sig("BBB", store, 3), _sig("CCC", store, 4)]
    late = [_sig("DDD", store, 12), _sig("EEE", store, 12)]
    cut = early[-1].signal_time
    full = ShadowRunner(store, SECTORS, clusters=CLUSTERS).process_session(DAY, early + late)[1]
    trunc = ShadowRunner(_Truncated(store, cut), SECTORS, clusters=CLUSTERS).process_session(DAY, early)[1]
    key = lambda rows: sorted((r["symbol"], r["decision_ts"], r["disposition"], r["qty_planned"])
                              for r in rows if r["decision_ts"] <= cut)
    assert key(full) == key(trunc)


def test_runner_matches_engine_emission_and_sizing():
    from research.backtest.engine import BacktestEngine, EngineConfig
    from research.backtest.universe import PointInTimeUniverse
    from research.features.calibration import CalibrationConfig, CalibrationProvider
    from research.features.events import NoEventsData
    from research.strategies.resid_rev import ResidRevAdapter
    from research.tests.test_p5_strategies import DRAFT, filled

    store = _WithVix(make_store(overrides={("AAA", 66): {2: 0.985, 3: 0.985, 4: 0.99}}))
    fmap = {s: "IDX:NIFTYMETAL" for s in SyntheticSpec().stocks}
    uni = PointInTimeUniverse.assumed_static(list("AAA BBB CCC DDD EEE FFF".split()), DAYS[0], DAYS[-1])
    res = BacktestEngine(store, uni, [ResidRevAdapter(filled(z_star=2.0), variant="RESID_REV_NF")],
                         EngineConfig(var_elm_rate=0.20, allow_shorts=True, r_basis="stop_limit",
                                      stop_limit_offset_pct=0.005),
                         calibration_provider=CalibrationProvider(store, fmap, CalibrationConfig.from_prereg(DRAFT)),
                         events_provider=NoEventsData()).run(only_dates=[DAY])
    assert res.signals
    rep, rows = ShadowRunner(store, SECTORS, clusters=CLUSTERS).process_session(DAY, res.signals)
    assert sorted((r["symbol"], r["decision_ts"]) for r in rows) == sorted((s.symbol, s.signal_time) for s in res.signals)
    eng_qty = {(s.symbol, s.signal_time): s.qty_planned for s in res.signals}
    for r in rows:                                                   # m = 1 at VIX 12: identical sizing
        assert r["qty_planned"] == eng_qty[(r["symbol"], r["decision_ts"])] and r["vix_multiplier"] == 1.0
    assert rep.book_flat


def test_runner_rows_append_to_the_ledger(tmp_path):
    from research.decision.ledger import Ledger
    from research.shadow.runner import ledger_rows

    store = _WithVix(make_store())
    rep, rows = ShadowRunner(store, SECTORS, clusters=CLUSTERS).process_session(DAY, [_sig(s, store, 5) for s in ("AAA", "BBB", "CCC", "DDD")])
    led = Ledger(tmp_path / "l.db")
    led.append(ledger_rows(rows, run_id="t", mode="SHADOW", strategy_version={"RESID_REV": "v"}))
    got = led.rows(mode="SHADOW")
    assert len(got) == 4 and sum(r["allocated"] for r in got) == 3
    assert {r["evidence_class"] for r in got if r["allocated"]} == {"E1"}


def test_unknown_clusters_share_one_bucket_fail_closed():
    store = _WithVix(make_store())
    rep, _ = ShadowRunner(store, SECTORS).process_session(DAY, [_sig(s, store, 5) for s in ("AAA", "BBB", "CCC")])
    assert rep.allocated == 1 and rep.drops["CLUSTER_LIMIT"] == 2


# ------------------------------------------------------------------ feeds
def test_snapshot_replay_feed_is_point_in_time():
    from research.shadow.feed import SnapshotReplayFeed

    store = make_store()
    live = SnapshotReplayFeed(store).live_day(DAY, datetime.combine(DAY, time(10, 0, 30), IST))
    assert all(b.end <= datetime.combine(DAY, time(9, 59, 30), IST) for bs in live.bars.values() for b in bs)
    assert not live.dropped and "AAA" in live.bars


def test_upstox_intraday_feed_writes_a_valid_live_file(tmp_path):
    import json

    from research.shadow import run_day as rd
    from research.shadow.feed import UpstoxIntradayFeed

    day = date(2026, 9, 28)
    mins = [[(datetime.combine(day, time(9, 15), IST) + timedelta(minutes=k)).isoformat(),
             100 + k * 0.1, 100.5 + k * 0.1, 99.8 + k * 0.1, 100.2 + k * 0.1, 1000, 0] for k in range(45)]

    class FakeClient:
        requests_made = 0

        def get(self, url):
            self.requests_made += 1
            if "BAD" in url:
                return 403, b"denied"
            return 200, json.dumps({"status": "success", "data": {"candles": list(reversed(mins))}}).encode()

    out = tmp_path / "live.json"
    summ = UpstoxIntradayFeed({"AAA": "NSE_EQ|INE000A01001", "ZZZ": "NSE_EQ|BAD"}, client=FakeClient(),
                              out_path=out).poll(day)
    assert summ["symbols"] == 1 and summ["errors"] == {"HTTP_403": 1}
    live = rd.load_live(out.read_bytes(), day, datetime.combine(day, time(10, 1), IST))
    assert not live.dropped and live.sources["AAA"] == "UPSTOX_API_V2"
    assert [b.start.time() for b in live.bars["AAA"]] == [time(9, 15), time(9, 30), time(9, 45)]
    b0 = live.bars["AAA"][0]
    assert b0.open == 100.0 and b0.volume == 15000 and abs(b0.close - (100.2 + 14 * 0.1)) < 1e-9


def test_dhan_feed_refuses():
    from research.shadow.feed import DhanIntradayFeed

    with pytest.raises(NotImplementedError, match="paid Dhan Data API"):
        DhanIntradayFeed()
