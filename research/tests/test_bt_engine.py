"""Engine batch tests: universe, strategy adapters, event-driven engine, tear sheet, study pipeline.

Exit timestamps: intrabar fills (stops, targets) carry the start of the bar they happened in; fills at a
bar's close (time stops) carry the bar's end; fills at a bar's open (gaps, policy exit) carry its start.
"""
from datetime import date, datetime, time, timedelta

import pytest

from research.backtest.bars import IST, Bar, CandleStore
from research.backtest.cost_model import DhanFeeEngine, OrderSide, ProductType
from research.backtest.engine import BacktestEngine, EngineConfig
from research.backtest.strategies import (Decision, OrbAdapter, SignalIntent, StrategyAdapter, StrategyContext,
                                          default_adapters)
from research.backtest.study import run_study
from research.backtest.tear_sheet import render_tear_sheet
from research.backtest.universe import PointInTimeUniverse

DAY = date(2026, 9, 1)
SECTORS = {"AAA": "S1", "BBB": "S2"}


def _session(sym, day=DAY, n=24, px=100.0, overrides=None, vol=10000):
    bars = []
    for i in range(n):
        start = datetime.combine(day, time(9, 15), IST) + timedelta(minutes=15 * i)
        o, h, l, c = px, px + 0.2, px - 0.2, px
        if overrides and i in overrides:
            o, h, l, c = overrides[i]
        bars.append(Bar(sym, start, 15, o, h, l, c, vol))
    return bars


def _store(sessions_by_symbol):
    intraday = {s: {b[0].session_date: b for b in [sess]} for s, sess in sessions_by_symbol.items()}
    return CandleStore(intraday, {s: [] for s in sessions_by_symbol})


def _universe(symbols=("AAA", "BBB")):
    return PointInTimeUniverse.assumed_static(symbols, date(2026, 1, 1), date(2026, 12, 31), note="test")


class Scripted(StrategyAdapter):
    """Emits a pre-scripted intent when the signal bar (by start time) is reached; records what it saw."""

    def __init__(self, script, name="SCRIPT", is_shadow=False):
        self.name, self.is_shadow, self.script, self.seen = name, is_shadow, script, []

    def evaluate(self, ctx: StrategyContext) -> Decision:
        latest_seen = max([b.end for b in ctx.bars] + [b.end for b in ctx.nifty_bars] +
                          [b.end for bars in ctx.peers.values() for b in bars])
        self.seen.append((ctx.decision_time, latest_seen))
        spec = self.script.get((ctx.symbol, ctx.current.hm))
        if not spec:
            return Decision(self.name, ctx.symbol, "NO_PATTERN")
        intent = SignalIntent.from_ctx(ctx, self.name, spec.get("side", "BUY"), spec["stop"], spec["targets"],
                                       spec.get("max_bars"), self.is_shadow)
        return Decision(self.name, ctx.symbol, "SIGNAL", intent)


def _run(sessions, script, cfg=None, shadow=False, adapters=None, sectors=SECTORS):
    adapters = adapters or [Scripted(script, is_shadow=shadow)]
    eng = BacktestEngine(_store(sessions), _universe(), adapters, cfg or EngineConfig(var_elm_rate=0.20), sectors)
    return eng.run(), adapters


TWO_TRANCHE = {"stop": 99.0, "targets": ((101.5, 0.5), (103.0, 0.5))}


def _hm(ts):
    return ts.astimezone(IST).strftime("%H:%M")


# ---------------------------------------------------------------- universe
def test_universe_fails_closed_and_excludes_surveillance_and_floor():
    u = PointInTimeUniverse([("AAA", date(2026, 9, 1), None)], surveillance=[("AAA", "ASM", date(2026, 9, 5), None)])
    assert u.check("AAA", date(2026, 9, 2), price=100.0) == (True, "ELIGIBLE")
    assert u.check("AAA", date(2026, 9, 6))[1] == "SURVEILLANCE_ASM"
    assert u.check("ZZZ", date(2026, 9, 2))[1] == "NOT_FNO_MEMBER"
    assert u.check("AAA", date(2026, 8, 31))[1] == "NOT_FNO_MEMBER"
    assert u.check("AAA", date(2026, 9, 2), price=9.5)[1] == "PRICE_BELOW_FLOOR"
    assert _universe().assumed is True


# ---------------------------------------------------------------- entries
def test_entry_fills_at_next_open_plus_one_tick_and_sizes_to_slot():
    res, _ = _run({"AAA": _session("AAA")}, {("AAA", "09:45"): TWO_TRANCHE})
    t = res.trades[0]
    assert _hm(t.entry_time) == "10:00"
    assert t.entry_price == 100.01
    # Adjusted A1: min(1500 // 1.00, 38000 // 100.11); 100.11 = worst admissible entry (100 x 1.001 -> 100.10,
    # + 1 tick). Was 583 under the Rs 58,333.33 cap. The fill (379 x 100.01) stays under Rs 38,000.
    assert t.qty == 379 and t.qty * t.entry_price <= 38_000.0


def test_clamp_skips_entry_when_next_open_gaps_away():
    s = _session("AAA", overrides={3: (100.20, 100.4, 100.1, 100.3)})
    res, _ = _run({"AAA": s}, {("AAA", "09:45"): TWO_TRANCHE})
    assert res.trades == []
    assert res.signals[0].disposition == "MISSED_CLAMP"


# ---------------------------------------------------------------- exits
def test_stop_gap_fills_at_open_minus_slippage():
    s = _session("AAA", overrides={4: (98.5, 98.7, 98.3, 98.6)})
    res, _ = _run({"AAA": s}, {("AAA", "09:45"): TWO_TRANCHE})
    t = res.trades[0]
    assert t.exit_reason == "STOP" and t.exits[0].price == 98.49


def test_target_needs_trade_through_then_breakeven_trail():
    s = _session("AAA", overrides={4: (100.0, 101.50, 99.9, 101.0),     # touches T1 exactly: no fill
                                   5: (101.0, 101.51, 100.9, 101.2),    # trades through T1
                                   6: (101.0, 101.1, 99.95, 100.0)})    # back through breakeven
    res, _ = _run({"AAA": s}, {("AAA", "09:45"): TWO_TRANCHE})
    t = res.trades[0]
    assert [(e.reason, e.price, e.qty) for e in t.exits] == [("TARGET_1", 101.5, 189), ("BREAKEVEN_STOP", 100.0, 190)]
    assert _hm(t.exits[0].time) == "10:30"


def test_time_stop_exits_at_close_of_nth_bar():
    res, _ = _run({"AAA": _session("AAA")}, {("AAA", "09:45"): dict(TWO_TRANCHE, max_bars=2)})
    t = res.trades[0]
    assert t.exit_reason == "TIME_STOP" and t.exits[-1].price == 99.99
    assert _hm(t.exits[-1].time) == "10:30"


def test_policy_exit_at_first_bar_spanning_bounded_exit_time():
    res, _ = _run({"AAA": _session("AAA")}, {("AAA", "13:00"): TWO_TRANCHE})
    t = res.trades[0]
    assert t.exit_reason == "POLICY_EXIT"
    assert _hm(t.exits[-1].time) == "15:00" and t.exits[-1].price == 99.99


def test_rms_squareoff_fee_when_session_data_ends_early():
    res, _ = _run({"AAA": _session("AAA", n=23)}, {("AAA", "13:00"): TWO_TRANCHE})
    t = res.trades[0]
    assert t.exit_reason == "RMS_SQUAREOFF"
    assert t.rms_fee == 23.6 and "RMS" in t.evidence_method


def test_no_decisions_at_or_after_entry_freeze():
    script = {("AAA", "14:45"): TWO_TRANCHE, ("AAA", "14:30"): TWO_TRANCHE}
    res, (adapter,) = _run({"AAA": _session("AAA")}, script)
    assert all(d.astimezone(IST).time() < time(14, 50) for d, _ in adapter.seen)
    assert len(res.trades) == 1 and _hm(res.trades[0].entry_time) == "14:45"


def test_strategies_never_see_future_bars():
    sessions = {"AAA": _session("AAA"), "BBB": _session("BBB"), "NIFTY50": _session("NIFTY50", px=24000.0)}
    adapter = Scripted({})
    BacktestEngine(_store(sessions), _universe(), [adapter], EngineConfig(var_elm_rate=0.2),
                   {"AAA": "S1", "BBB": "S1"}).run()                     # same sector: peers are supplied
    assert adapter.seen and all(seen <= decided for decided, seen in adapter.seen)


# ---------------------------------------------------------------- risk, shadow, re-entry, charges
def test_governor_fails_closed_without_var_elm_rate():
    res, _ = _run({"AAA": _session("AAA")}, {("AAA", "09:45"): TWO_TRANCHE}, cfg=EngineConfig())
    assert res.trades == [] and res.signals[0].disposition.startswith("REJECTED_GOVERNOR")


def test_short_intent_is_rejected_by_governor():
    spec = {"side": "SELL", "stop": 101.0, "targets": ((98.5, 1.0),)}
    res, _ = _run({"AAA": _session("AAA")}, {("AAA", "09:45"): spec})
    assert res.trades == [] and "INVERTED_STOP" in res.signals[0].disposition
    assert res.signals[0].counterfactual_net_r is not None          # counterfactual still recorded


def test_shadow_signals_are_not_allocated_unless_allowed():
    res, _ = _run({"AAA": _session("AAA")}, {("AAA", "09:45"): TWO_TRANCHE}, shadow=True)
    assert res.trades == [] and res.signals[0].disposition == "SHADOW_NOT_ALLOCATED"
    assert res.signals[0].counterfactual_net_r is not None
    res2, _ = _run({"AAA": _session("AAA")}, {("AAA", "09:45"): TWO_TRANCHE}, shadow=True,
                   cfg=EngineConfig(var_elm_rate=0.2, allow_shadow=True))
    assert len(res2.trades) == 1


def test_no_same_day_reentry():
    s = _session("AAA", overrides={4: (98.5, 98.7, 98.3, 98.6)})
    script = {("AAA", "09:45"): TWO_TRANCHE, ("AAA", "11:00"): TWO_TRANCHE}
    res, _ = _run({"AAA": s}, script)
    assert len(res.trades) == 1
    assert res.signals[1].disposition == "BLOCKED_REENTRY"


def test_charges_are_per_order():
    s = _session("AAA", overrides={5: (101.0, 101.51, 100.9, 101.2), 6: (101.0, 101.1, 99.95, 100.0)})
    res, _ = _run({"AAA": s}, {("AAA", "09:45"): TWO_TRANCHE})
    t = res.trades[0]
    expected = DhanFeeEngine.calculate_order(OrderSide.BUY, [(t.entry_price, t.qty)], ProductType.MIS).total_charges
    for e in t.exits:
        expected += DhanFeeEngine.calculate_order(OrderSide.SELL, [(e.price, e.qty)], ProductType.MIS).total_charges
    assert t.charges == pytest.approx(expected, abs=0.02)
    assert t.net_pnl == pytest.approx(t.gross_pnl - t.charges, abs=0.01)


# ---------------------------------------------------------------- real adapters fail closed
def test_orb_adapter_fails_closed_without_baseline_or_trend_history():
    sessions = {"AAA": _session("AAA", overrides={1: (100.0, 101.0, 99.9, 100.9)}),
                "NIFTY50": _session("NIFTY50", px=24000.0)}
    res = BacktestEngine(_store(sessions), _universe(), [OrbAdapter()], EngineConfig(var_elm_rate=0.2), SECTORS).run()
    assert res.trades == []
    assert res.decision_counts["ORB_SIMPLE"]["DATA_INVALID"] > 0


def test_default_adapters_are_orb_active_and_rest_shadow():
    ads = default_adapters()
    assert {a.name: a.is_shadow for a in ads} == {"ORB_SIMPLE": False, "VWAP_RECLAIM": True, "VOL_SQUEEZE": True,
                                                  "TRAPDOOR": True, "LAST_LIGHT": True, "RECOIL": True, "COMPASS": True}


def test_incubated_slate_adapters_are_all_shadow():
    from research.backtest.strategies import incubated_slate_adapters
    ads = incubated_slate_adapters()
    assert {a.name: a.is_shadow for a in ads} == {
        "PEAD_DRIFT": True,
        "SWEEP_RECLAIM": True,
        "LATE_MOMENTUM": True,
        "CAS_REVERSAL": True,
    }



# ---------------------------------------------------------------- tear sheet & study
def test_tear_sheet_is_self_contained(tmp_path):
    days = [date(2026, 9, d) for d in range(1, 11)]
    out = render_tear_sheet(
        tmp_path / "t.html", title="Test <script>",
        portfolio={"dates": days, "daily_returns": [0.001 * ((-1) ** i) for i in range(10)]},
        strategies={"A": {"daily_pnl": {d: 10.0 * i for i, d in enumerate(days)}, "net_r": [0.5, -1.0, 0.2],
                          "summary": {"n": 3, "mean": -0.1}}},
        notes=["note & caveat"], gate={"passed": False, "reasons": ["no admissible evidence"]}, extra_tables={})
    html = out.read_text(encoding="utf-8")
    assert html.count("<svg") >= 3 and "&lt;script&gt;" in html and "note &amp; caveat" in html
    assert "http://" not in html and "https://" not in html and "<script" not in html.replace("&lt;script", "")


def test_study_pipeline_runs_and_gate_fails_on_bar_data(tmp_path):
    intraday = {}
    for s, px in (("AAA", 100.0), ("BBB", 200.0), ("NIFTY50", 24000.0)):
        days = [_session(s, day=date(2026, 8, 3) + timedelta(days=i), px=px) for i in range(3)]
        intraday[s] = {b[0].session_date: b for b in days}
    store = CandleStore(intraday, {s: [] for s in intraday})
    report = run_study(store, _universe(), tmp_path, sectors=SECTORS, config=EngineConfig(var_elm_rate=0.2),
                       min_baseline_sessions=1)
    assert report["gate"]["passed"] is False
    assert (tmp_path / "summary.json").exists() and (tmp_path / "tear_sheet.html").exists()
    assert (tmp_path / "signals.csv").exists() and (tmp_path / "trades.csv").exists()
