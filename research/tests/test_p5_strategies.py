"""
P5 tests (plan P5.1-P5.3): RESID_REV on hand-built sessions with hand-computed prices, every rejection
reason, the adapter-level look-ahead check, ORB_PROD against the production desk, and the ORB_SIMPLE rename.
"""
from __future__ import annotations

import copy
import math
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from research.backtest.bars import Bar, CandleStore, round_to_tick
from research.backtest.strategies import StrategyContext
from research.data.session_shape import slot_start
from research.features.calibration import MarketCalibration, SessionCalibration, SymbolCalibration
from research.features.events import NoEventsData, TableEvents
from research.strategies.resid_rev import ResidRevAdapter
from research.studies import prereg_io

IST = timezone(timedelta(hours=5, minutes=30))
DAY = date(2026, 9, 10)
PREV = date(2026, 9, 9)
DRAFT = prereg_io.load(prereg_io.PREREG_DIR / "resid_rev_v1.yaml")
FIXTURE = Path(__file__).resolve().parents[2] / "shared" / "track2_liquid" / "historical_candles_track2.json"   # Kite 32 sessions; hash-pinned in test_p2_regressions


def filled(z_star=2.59, hold=8):
    spec = copy.deepcopy(DRAFT)
    spec["signal"]["z_star"], spec["trade"]["hold_bars"] = z_star, hold
    return spec


def _bars(sym, rets, level=500.0, vol=1000, day=DAY, n=24):
    closes = [level]
    for r in rets:
        closes.append(closes[-1] * math.exp(r))
    closes += [closes[-1]] * (n - len(closes))
    out, prev = [], level
    for k in range(n):
        c = closes[k]
        out.append(Bar(sym, datetime.combine(day, slot_start(k), IST), 15, prev, max(prev, c) * 1.0001,
                       min(prev, c) * 0.9999, c, vol))
        prev = c
    return out


class _View:
    """Minimal point-in-time view: the previous session exists."""

    def sessions(self, symbol):
        return [PREV]


def _calibration(s2=4e-6, s2px=4e-6, n=60, valid=True, zm_pct=1.0):
    shape = np.array([np.nan] + [1.0] * 23)
    sym = SymbolCalibration("AAA", DAY, "IDX:NIFTYMETAL", valid, "OK" if valid else f"INSUFFICIENT_SESSIONS_{n}_OF_40",
                            n_sessions=n, n_pairs=n * 23, beta=1.0, beta_ols=1.0, s2=s2, rel_slot=shape.copy(),
                            s2px=np.array([np.nan] + [s2px] * 23), cumvol_med=np.array([1000.0 * (k + 1) for k in range(25)]),
                            cumvol_reason="OK")
    mkt = MarketCalibration(DAY, True, "OK", 60, s2N=np.array([np.nan] + [1e-6] * 23),
                            zm_pct=np.array([np.nan] + [zm_pct] * 23), vix_ref=14.0, vix_prev_close=13.5, vix_reason="OK")
    return SessionCalibration(DAY, shape, "OK", mkt, {"AAA": sym}, n_sessions=n)


QUIET_EVENTS = TableEvents(covered=(datetime(2026, 1, 1, tzinfo=IST), datetime(2026, 12, 31, tzinfo=IST)))
DOWN = [-0.004, -0.004, -0.004, -0.004, 0.0015]          # E5 = -0.0145, turn at slot 5
UP = [-r for r in DOWN]


def _ctx(stock_rets=DOWN, t=5, vol=1000, nifty_rets=(), cal=None, events=QUIET_EVENTS, extra_after=()):
    bars = _bars("AAA", list(stock_rets) + list(extra_after), vol=vol)
    fac = _bars("IDX:NIFTYMETAL", [], level=20000.0, vol=0, n=25)
    nif = _bars("IDX:NIFTY50", list(nifty_rets), level=25000.0, vol=0, n=25)
    cut = lambda bs: [b for b in bs if b.start <= bars[t].start]
    return StrategyContext(symbol="AAA", decision_time=bars[t].end, current=bars[t], bars=bars[: t + 1],
                           nifty_bars=cut(nif), store=_View(), index_bars={"IDX:NIFTYMETAL": cut(fac), "IDX:NIFTY50": cut(nif)},
                           calibration=cal or _calibration(), events=events, bar_index=t)


# ------------------------------------------------------------------ signals with hand-computed prices
def _expected(sg, E, p0, s2px=4e-6, t=5, h=8):
    t1 = round_to_tick(p0 * math.exp(sg * 0.5 * abs(E)), "down" if sg > 0 else "up")
    t2 = round_to_tick(p0 * math.exp(sg * 1.0 * abs(E)), "down" if sg > 0 else "up")
    h_eff = min(h, 22 - t)
    s = max(0.008, math.sqrt(h_eff * s2px))
    stop = round_to_tick(p0 * (1 - sg * s), "down" if sg > 0 else "up")
    return t1, t2, stop, h_eff


def test_idiosyncratic_drop_with_turn_gives_buy_with_exact_prices():
    a = ResidRevAdapter(filled())
    d = a.evaluate(_ctx())
    assert d.action == "SIGNAL", d.reason
    it = d.intent
    E = sum(DOWN)
    p0 = 500.0 * math.exp(E)
    t1, t2, stop, h_eff = _expected(+1, E, p0)
    assert it.side == "BUY" and it.max_bars == h_eff == 8
    assert it.targets == ((t1, 0.5), (t2, 0.5))
    assert it.stop_loss == stop
    assert math.isclose(it.diagnostics["Z"], E / math.sqrt(5 * 4e-6))
    assert it.diagnostics["Z"] < -3.2 and it.priority_score == abs(it.diagnostics["Z"])
    assert it.r_basis == "stop_limit" and it.is_shadow


def test_mirror_case_gives_sell():
    d = ResidRevAdapter(filled()).evaluate(_ctx(stock_rets=UP))
    assert d.action == "SIGNAL" and d.intent.side == "SELL"
    E = sum(UP)
    p0 = 500.0 * math.exp(E)
    t1, t2, stop, _ = _expected(-1, E, p0)
    assert d.intent.targets == ((t1, 0.5), (t2, 0.5)) and d.intent.stop_loss == stop
    assert t2 < t1 < p0 < stop


def test_eod_hold_uses_22_minus_t():
    d = ResidRevAdapter(filled(hold="EOD")).evaluate(_ctx())
    assert d.intent.max_bars == 17


# ------------------------------------------------------------------ rejections
def _reason(adapter, ctx):
    d = adapter.evaluate(ctx)
    return d.action, d.reason.split(":")[0]


def test_rvol_2_is_volume_abnormal():
    assert _reason(ResidRevAdapter(filled()), _ctx(vol=2000)) == ("NO_PATTERN", "VOLUME_ABNORMAL")


def test_filing_since_previous_close_blocks():
    ev = TableEvents(announcements={"AAA": [datetime(2026, 9, 10, 9, 40, tzinfo=IST)]},
                     covered=QUIET_EVENTS.covered)
    assert _reason(ResidRevAdapter(filled()), _ctx(events=ev)) == ("NO_PATTERN", "NEWS_BLOCKED")


def test_next_trading_day_rule_survives_a_holiday():
    """Result on Mon 14 Sep, decision Thu 10 Sep. Without a calendar the next two weekdays are checked
    (Fri 11 might be a holiday), so it blocks; with a calendar the exact next session decides."""
    ev = TableEvents(scheduled={"AAA": [(datetime(2026, 9, 1, 18, 0, tzinfo=IST), date(2026, 9, 14))]},
                     covered=QUIET_EVENTS.covered)
    assert _reason(ResidRevAdapter(filled()), _ctx(events=ev)) == ("NO_PATTERN", "NEWS_BLOCKED")
    friday_open = [DAY, date(2026, 9, 11), date(2026, 9, 14)]
    assert ResidRevAdapter(filled(), trading_days=friday_open).evaluate(_ctx(events=ev)).action == "SIGNAL"
    friday_holiday = [DAY, date(2026, 9, 14)]
    assert _reason(ResidRevAdapter(filled(), trading_days=friday_holiday), _ctx(events=ev)) == \
        ("NO_PATTERN", "NEWS_BLOCKED")


def test_unknown_events_block_but_nf_variant_ignores_news():
    assert _reason(ResidRevAdapter(filled()), _ctx(events=NoEventsData())) == ("NO_PATTERN", "NEWS_BLOCKED")
    assert _reason(ResidRevAdapter(filled()), _ctx(events=None)) == ("NO_PATTERN", "NEWS_BLOCKED")
    nf = ResidRevAdapter(filled(), variant="RESID_REV_NF").evaluate(_ctx(events=NoEventsData()))
    assert nf.action == "SIGNAL" and nf.intent.diagnostics["news_filter"] is False


def test_calibration_with_too_few_sessions_is_data_invalid():
    assert _reason(ResidRevAdapter(filled()), _ctx(cal=_calibration(n=39, valid=False))) == ("DATA_INVALID", "CALIBRATION")


def test_decision_at_1345_is_window_closed():
    rets = DOWN + [0.0] * 12
    assert _reason(ResidRevAdapter(filled()), _ctx(stock_rets=rets, t=17)) == ("NO_PATTERN", "WINDOW_CLOSED")
    assert _reason(ResidRevAdapter(filled()), _ctx(stock_rets=[-0.01, 0.002], t=1)) == ("NO_PATTERN", "WINDOW_CLOSED")


def test_small_move_is_no_setup():
    assert _reason(ResidRevAdapter(filled()), _ctx(stock_rets=[-0.001] * 4 + [0.0005])) == ("NO_PATTERN", "NO_SETUP")


def test_extending_bar_is_no_turn_and_a_later_turn_may_signal():
    a = ResidRevAdapter(filled())
    rets = [-0.004] * 5 + [0.002]
    assert _reason(a, _ctx(stock_rets=rets, t=5)) == ("NO_PATTERN", "NO_TURN")
    d = a.evaluate(_ctx(stock_rets=rets, t=6))
    assert d.action == "SIGNAL" and d.intent.side == "BUY"


def test_wide_stop_is_skipped():
    assert _reason(ResidRevAdapter(filled()), _ctx(cal=_calibration(s2px=1e-4))) == ("NO_PATTERN", "STOP_TOO_WIDE_SKIP")


def test_t1_closer_than_040pct_is_cost_hurdle():
    small = [-0.0017] * 4 + [0.0005]                    # E = -0.0063 -> T1 at 0.315%
    cal = _calibration(s2=1e-6)                          # Z = -0.0063/sqrt(5e-6) = -2.8
    assert _reason(ResidRevAdapter(filled()), _ctx(stock_rets=small, cal=cal)) == ("NO_PATTERN", "COST_HURDLE")


def test_nifty_shock_is_market_filter():
    assert _reason(ResidRevAdapter(filled()), _ctx(nifty_rets=[-0.004] * 5)) == ("NO_PATTERN", "MARKET_FILTER")


def test_second_emitted_signal_same_day_is_blocked():
    a = ResidRevAdapter(filled())
    assert a.evaluate(_ctx()).action == "SIGNAL"
    rets = DOWN + [-0.004, 0.002]
    assert _reason(a, _ctx(stock_rets=rets, t=7)) == ("NO_PATTERN", "DAY_USED")


def test_draft_spec_only_runs_in_scan_mode_and_scan_logs_max_z():
    with pytest.raises(ValueError):
        ResidRevAdapter(DRAFT)
    scan = ResidRevAdapter(DRAFT, mode="SCAN")
    for t in range(2, 6):
        assert scan.evaluate(_ctx(t=t)).action == "NO_PATTERN"
    zs = [abs(sum(DOWN[:t])) / math.sqrt(t * 4e-6) for t in range(2, 6)]
    assert math.isclose(scan.scan_max[("AAA", DAY)], max(zs))
    with pytest.raises(ValueError):
        ResidRevAdapter(filled(hold=6))


def test_nf_variant_must_be_registered():
    spec = filled()
    spec["variants_registered"] = []
    with pytest.raises(ValueError):
        ResidRevAdapter(spec, variant="RESID_REV_NF")


def test_missing_factor_bars_fall_back_to_nifty():
    ctx = _ctx()
    ctx = StrategyContext(**{**ctx.__dict__, "index_bars": {"IDX:NIFTY50": ctx.index_bars["IDX:NIFTY50"]}})
    d = ResidRevAdapter(filled()).evaluate(ctx)
    assert d.action == "SIGNAL" and d.intent.diagnostics["factor_used"] == "IDX:NIFTY50"


# ------------------------------------------------------------------ engine integration and look-ahead (D17)
def _engine_run(store, adapter, day_index, provider):
    from research.backtest.engine import BacktestEngine, EngineConfig
    from research.backtest.universe import PointInTimeUniverse
    from research.tests.synthetic import sessions

    days = sessions()
    uni = PointInTimeUniverse.assumed_static(list("AAA BBB CCC DDD EEE FFF".split()), days[0], days[-1])
    cfg = EngineConfig(var_elm_rate=0.2, allow_shorts=True)
    eng = BacktestEngine(store, uni, [adapter], cfg, calibration_provider=provider, events_provider=NoEventsData())
    return eng.run()


def test_adapter_level_lookahead_and_engine_wiring():
    from research.features.calibration import CalibrationConfig, CalibrationProvider
    from research.tests.synthetic import SyntheticSpec, make_store, sessions

    cfg = CalibrationConfig.from_prereg(DRAFT)
    fmap = {s: "IDX:NIFTYMETAL" for s in SyntheticSpec().stocks}
    # a sharp idiosyncratic drop in AAA on session 66 (slots 2-5), then later bars
    over = {("AAA", 66): {2: 0.985, 3: 0.985, 4: 0.99}}
    base = make_store(overrides=over)
    later = make_store(overrides={**over, ("AAA", 66): {2: 0.985, 3: 0.985, 4: 0.99, 12: 1.05}})
    a1 = ResidRevAdapter(filled(z_star=2.0), variant="RESID_REV_NF", keep_log=True)
    a2 = ResidRevAdapter(filled(z_star=2.0), variant="RESID_REV_NF", keep_log=True)
    r1 = _engine_run(base, a1, 66, CalibrationProvider(base, fmap, cfg))
    r2 = _engine_run(later, a2, 66, CalibrationProvider(later, fmap, cfg))
    day = sessions()[66]
    cutoff = datetime.combine(day, slot_start(12), IST)
    before = lambda log: [x for x in log if datetime.fromisoformat(x[1]) <= cutoff and x[0] == "AAA"
                          and datetime.fromisoformat(x[1]).date() == day]
    assert before(a1.log) == before(a2.log)
    assert any(x[2] == "SIGNAL" for x in before(a1.log)), "the injected drop should produce a signal"
    assert a1.reasons.get("CALIBRATION", 0) > 0          # early sessions have < 40 prior sessions
    sig = [s for s in r1.signals if s.symbol == "AAA" and s.signal_time.date() == day]
    assert sig and sig[0].side == "BUY" and sig[0].counterfactual_net_r is not None


def test_kite_file_gives_data_invalid_for_resid_rev():
    from research.backtest.engine import BacktestEngine, EngineConfig
    from research.backtest.universe import PointInTimeUniverse
    from research.features.calibration import CalibrationConfig, CalibrationProvider

    kite = CandleStore.from_historical_json(FIXTURE)
    syms = [s for s in kite.symbols if kite.kind(s) == "TRADABLE"]
    uni = PointInTimeUniverse.assumed_static(syms, date(2026, 1, 1), date(2026, 12, 31))
    a = ResidRevAdapter(DRAFT, mode="SCAN")
    prov = CalibrationProvider(kite, {}, CalibrationConfig.from_prereg(DRAFT))
    res = BacktestEngine(kite, uni, [a], EngineConfig(var_elm_rate=0.2), calibration_provider=prov).run()
    counts = res.decision_counts[a.name]
    assert counts.get("SIGNAL", 0) == 0 and counts.get("DATA_INVALID", 0) > 0
    assert set(a.reasons) <= {"CALIBRATION", "WINDOW_CLOSED"}


# ------------------------------------------------------------------ ORB_PROD vs the production desk
def _desk_decision(desk, store, sym, day, t_index, kite_nifty):
    bars = store.bars(sym, day)[: t_index + 1]
    m = lambda b: {"timestamp": b.start.astimezone(IST).isoformat(), "open": b.open, "high": b.high, "low": b.low,
                   "close": b.close, "volume": int(b.volume)}
    prior = [m(b) for d in store.sessions(sym) if d < day for b in store.bars(sym, d)]
    daily = [{"timestamp": f"{x.day.isoformat()}T00:00:00+05:30", "open": x.open, "high": x.high, "low": x.low,
              "close": x.close, "volume": int(x.volume)} for x in store.daily_before(sym, day)]
    nifty = [m(b) for b in store.bars(kite_nifty, day) if b.start <= bars[-1].start]
    now = bars[-1].end + timedelta(seconds=30)
    out = desk.evaluate_feed({"session_date": day.isoformat(), "symbols": {sym: {"bars": [m(b) for b in bars]}}},
                             historical={sym: prior}, historical_daily={sym: daily}, order_rules=desk.ORDER_RULES,
                             nifty_bars=nifty, eligible_symbols={sym}, now=now)
    return out.get("decisions", [{}])[0].get("decision", out.get("state")) if out.get("decisions") else out.get("state")


def test_orb_prod_matches_the_production_desk_on_sampled_symbol_days():
    pytest.importorskip("antigravity.daemons.track2_daily_paper_desk")
    from antigravity.daemons import track2_daily_paper_desk as desk
    from research.backtest.strategies import PointInTimeView
    from research.strategies.orb_prod import OrbProdAdapter

    store = CandleStore.from_historical_json(FIXTURE)
    nifty = next(s for s in store.symbols if store.kind(s) == "INDEX")
    syms = [s for s in store.symbols if store.kind(s) == "TRADABLE"]
    days = store.sessions(syms[0])[21:]                  # sessions with 20 prior daily bars
    # the plan asks for 10 sampled symbol-days; every symbol-day with a baseline is checked instead
    sample = [(s, d) for s in syms for d in days]
    compared = signals = 0
    for sym, day in sample:
        adapter = OrbProdAdapter()
        bars = store.bars(sym, day)
        nb = store.bars(nifty, day)
        for i, b in enumerate(bars):
            st = b.start.astimezone(IST).time()
            if not (time(9, 30) <= st <= time(14, 15)):
                continue
            ctx = StrategyContext(symbol=sym, decision_time=b.end, current=b, bars=bars[: i + 1],
                                  daily_bars=store.daily_before(sym, day), nifty_bars=[x for x in nb if x.start <= b.start],
                                  store=PointInTimeView(store, day, b.end), bar_index=i)
            mine = adapter.evaluate(ctx)
            theirs = _desk_decision(desk, store, sym, day, i, nifty)
            compared += 1
            assert (mine.action == "SIGNAL") == (theirs == "PAPER_SIGNAL"), (sym, day, st, mine.reason, theirs)
            if mine.action == "SIGNAL":
                signals += 1
                break                                       # ORB_PROD takes the first qualifying bar only
            if theirs not in ("PAPER_SIGNAL",):
                assert mine.reason == theirs or mine.reason.startswith("BASELINE") and theirs == "DATA_INVALID", \
                    (sym, day, st, mine.reason, theirs)
    assert len(sample) >= 10 and compared >= 1000 and signals >= 10


def test_orb_prod_manifest_hashes_production_files():
    from research.strategies.orb_prod import PRODUCTION_FILES, OrbProdAdapter

    man = OrbProdAdapter().manifest()
    assert set(man) == set(PRODUCTION_FILES) and all(len(v) == 40 for v in man.values())


def test_orb_simple_rename():
    from research.backtest.strategies import OrbAdapter, OrbSimpleAdapter, default_adapters

    assert OrbAdapter.name == "ORB_SIMPLE" and OrbSimpleAdapter is OrbAdapter
    assert "ORB_MOMENTUM" not in {a.name for a in default_adapters()}


def test_candidate_recorded_once_per_stock_day_before_the_stop_rules():
    """P7.2c event study: the first evaluation that passes every h-independent filter is recorded, whatever
    the stop rules then decide, and later evaluations of the same stock-day do not replace it."""
    a = ResidRevAdapter(filled())
    d = a.evaluate(_ctx(stock_rets=UP))
    assert d.action == "SIGNAL"
    (key, cd), = a.candidates.items()
    assert cd["sg"] == -1 and cd["E"] > 0 and math.isclose(cd["Z"], d.intent.diagnostics["Z"])
    assert cd["factor_used"] == d.intent.diagnostics["factor_used"] and cd["slot"] == d.intent.diagnostics["slot"]
    first = dict(cd)
    a.evaluate(_ctx(stock_rets=UP))
    assert a.candidates[key] == first


def test_one_day_engine_run_matches_the_full_run_for_that_day():
    """The shadow runner runs the engine for one live day. That day's decisions must equal the full
    run's: sessions are independent and every input is read point-in-time from the history."""
    from research.backtest.engine import BacktestEngine, EngineConfig
    from research.backtest.universe import PointInTimeUniverse
    from research.features.calibration import CalibrationConfig, CalibrationProvider
    from research.features.events import NoEventsData
    from research.tests.synthetic import SyntheticSpec, make_store, sessions

    cfg = CalibrationConfig.from_prereg(DRAFT)
    fmap = {s: "IDX:NIFTYMETAL" for s in SyntheticSpec().stocks}
    store = make_store(overrides={("AAA", 66): {2: 0.985, 3: 0.985, 4: 0.99}})
    days = sessions()
    uni = PointInTimeUniverse.assumed_static(list("AAA BBB CCC DDD EEE FFF".split()), days[0], days[-1])

    def run(only=None):
        a = ResidRevAdapter(filled(z_star=2.0), variant="RESID_REV_NF")
        eng = BacktestEngine(store, uni, [a], EngineConfig(var_elm_rate=0.2, allow_shorts=True),
                             calibration_provider=CalibrationProvider(store, fmap, cfg), events_provider=NoEventsData())
        return eng.run(only_dates=only)

    day = days[66]
    key = lambda s: (s.symbol, s.signal_time, s.side, s.entry_ref, s.stop_loss, tuple(s.targets),
                     s.max_bars, s.disposition, s.counterfactual_net_r)
    full = sorted(key(s) for s in run().signals if s.signal_time.date() == day)
    one = run([day])
    assert one.dates == [day]
    assert full and sorted(key(s) for s in one.signals) == full


def test_legacy_adapters_wrap_production_classes_and_refuse_unknown_names():
    from research.strategies.legacy import NAMES, LegacyAdapter

    assert set(NAMES) == {"TRAPDOOR", "LAST_LIGHT", "RECOIL", "VOL_SQUEEZE", "COMPASS"}
    for n in NAMES:
        a = LegacyAdapter(n)
        assert a.name == n and type(a.impl).__module__.startswith("antigravity.models.")
    with pytest.raises(ValueError):
        LegacyAdapter("VWAP_RECLAIM")
