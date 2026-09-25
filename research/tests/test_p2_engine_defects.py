"""P2 engine defects D1-D19 (plan research/notes/PLAN.md, section P2). One or more tests per defect.

Prices around Rs 100 use the Rs 0.01 tick. Bars are 15-minute, stamped at bar start (IST).
"""
import math
from datetime import date, datetime, time, timedelta

import pytest

from research.backtest.bars import IST, Bar, CandleStore, is_index_symbol
from research.backtest.cost_model import DhanFeeEngine, OrderSide, ProductType
from research.backtest.engine import BacktestEngine, EngineConfig
from research.backtest.metrics import evaluate_gate
from research.backtest.strategies import (Decision, LookAheadError, SignalIntent, StrategyAdapter,
                                          StrategyContext)
from research.backtest.study import run_study
from research.backtest.universe import PointInTimeUniverse
from research.studies.signal_sim import SimConfig, plan_tranches, simulate_signal

DAY = date(2026, 9, 1)


def _session(sym, day=DAY, n=24, px=100.0, overrides=None, vol=10000, first=time(9, 15)):
    bars = []
    for i in range(n):
        start = datetime.combine(day, first, IST) + timedelta(minutes=15 * i)
        o, h, l, c = px, px + 0.2, px - 0.2, px
        if overrides and i in overrides:
            o, h, l, c = overrides[i]
        bars.append(Bar(sym, start, 15, o, h, l, c, vol))
    return bars


def _store(sessions):
    intraday = {}
    for sess in sessions:
        intraday.setdefault(sess[0].symbol, {})[sess[0].session_date] = sess
    return CandleStore(intraday, {s: [] for s in intraday})


def _universe(symbols):
    return PointInTimeUniverse.assumed_static(symbols, date(2026, 1, 1), date(2026, 12, 31), note="test")


class Scripted(StrategyAdapter):
    def __init__(self, script, name="SCRIPT", is_shadow=False):
        self.name, self.is_shadow, self.script, self.seen = name, is_shadow, script, []

    def evaluate(self, ctx: StrategyContext) -> Decision:
        self.seen.append((ctx.decision_time, ctx.symbol, ctx.current.close, tuple(ctx.index_bars), ctx.bar_index,
                          len(ctx.bars)))
        spec = self.script.get((ctx.symbol, ctx.current.hm))
        if not spec or (spec.get("day") and ctx.decision_time.astimezone(IST).date() != spec["day"]):
            return Decision(self.name, ctx.symbol, "NO_PATTERN")
        intent = SignalIntent.from_ctx(ctx, self.name, spec.get("side", "BUY"), spec["stop"], spec["targets"],
                                       spec.get("max_bars"), self.is_shadow)
        intent.priority_score = spec.get("score", 0.0)
        return Decision(self.name, ctx.symbol, "SIGNAL", intent)


def _engine(sessions, adapters, cfg=None, sectors=None, clusters=None, universe_syms=None):
    syms = universe_syms or sorted({s[0].symbol for s in sessions})
    return BacktestEngine(_store(sessions), _universe(syms), adapters, cfg or EngineConfig(var_elm_rate=0.2),
                          sectors or {}, clusters=clusters)


LONG = {"stop": 99.0, "targets": ((101.5, 0.5), (103.0, 0.5))}
SHORT = {"side": "SELL", "stop": 101.0, "targets": ((98.5, 0.5), (97.0, 0.5))}
CFG = SimConfig()


def _sim(bars, side, stop, targets, qty=100, cfg=CFG, i=2, max_bars=None):
    return simulate_signal(bars, i, side, bars[i].close, stop, targets, max_bars, qty, cfg, DAY)


# ---------------------------------------------------------------- D1 side-aware
def test_d1_long_and_short_intrabar_stop_prices_and_r():
    long = _sim(_session("A", overrides={4: (100.0, 100.1, 98.9, 99.2)}), "BUY", 99.0, LONG["targets"])
    short = _sim(_session("A", overrides={4: (100.0, 101.2, 99.9, 100.8)}), "SELL", 101.0, SHORT["targets"])
    assert (long.entry_price, long.exits[0].price, long.exit_reason) == (100.01, 98.99, "STOP")
    assert (short.entry_price, short.exits[0].price, short.exit_reason) == (99.99, 101.01, "STOP")
    for r in (long, short):
        assert r.gross_r == pytest.approx(-1.0) and r.slip_r == pytest.approx(0.02)
        assert r.exits[0].time.astimezone(IST).strftime("%H:%M") == "10:15"


def test_d1_fees_follow_the_legs():
    short = _sim(_session("A", overrides={4: (100.0, 101.2, 99.9, 100.8)}), "SELL", 101.0, SHORT["targets"])
    exp = (DhanFeeEngine.calculate_order(OrderSide.SELL, [(short.entry_price, 100)], ProductType.MIS).total_charges
           + DhanFeeEngine.calculate_order(OrderSide.BUY, [(short.exits[0].price, 100)], ProductType.MIS).total_charges)
    assert short.fees_rs == pytest.approx(exp, abs=0.01)
    entry_leg = DhanFeeEngine.calculate_order(OrderSide.SELL, [(short.entry_price, 100)], ProductType.MIS)
    assert entry_leg.stt > 0 and entry_leg.stamp_duty == 0          # STT on the short's entry leg


def test_d1_gap_through_limit_each_side():
    cfg = SimConfig(stop_limit_offset_pct=0.005)
    long = _sim(_session("A", overrides={4: (98.0, 98.2, 97.8, 98.1)}), "BUY", 99.0, LONG["targets"], cfg=cfg)
    short = _sim(_session("A", overrides={4: (102.0, 102.2, 101.8, 102.1)}), "SELL", 101.0, SHORT["targets"], cfg=cfg)
    assert (long.stop_limit, long.exit_reason, long.exits[0].price) == (98.5, "GAP_THROUGH_LIMIT", 97.99)
    assert (short.stop_limit, short.exit_reason, short.exits[0].price) == (101.51, "GAP_THROUGH_LIMIT", 102.01)
    assert long.gap_through_limit and short.gap_through_limit


def test_d1_stop_wins_when_bar_touches_stop_and_target_each_side():
    long = _sim(_session("A", overrides={4: (100.0, 101.6, 98.9, 100.0)}), "BUY", 99.0, LONG["targets"])
    short = _sim(_session("A", overrides={4: (100.0, 101.1, 98.4, 100.0)}), "SELL", 101.0, SHORT["targets"])
    assert long.exit_reason == "STOP" and short.exit_reason == "STOP"


def test_d1_engine_allocates_shorts_only_when_allowed():
    s = [_session("AAA")]
    res = _engine(s, [Scripted({("AAA", "09:45"): SHORT})]).run()
    assert res.trades == [] and res.signals[0].disposition == "REJECTED_GOVERNOR_INVERTED_STOP"
    res = _engine(s, [Scripted({("AAA", "09:45"): SHORT})], EngineConfig(var_elm_rate=0.2, allow_shorts=True)).run()
    assert res.trades[0].side == "SELL" and res.trades[0].exit_reason == "POLICY_EXIT"
    assert res.trades[0].exits[-1].price == 100.01                    # buy back at open + 1 tick


# ---------------------------------------------------------------- D2
def test_d2_study_requires_margin_rate(tmp_path):
    with pytest.raises(ValueError):
        run_study(_store([_session("AAA")]), _universe(["AAA"]), tmp_path, adapters=[Scripted({})],
                  config=EngineConfig())


# ---------------------------------------------------------------- D3
def test_d3_stop_limit_basis_and_k_sensitivity():
    bars = _session("A", overrides={4: (100.0, 100.1, 98.9, 99.2)})
    r = _sim(bars, "BUY", 99.0, LONG["targets"], qty=583, cfg=SimConfig(stop_limit_offset_pct=0.005, r_basis="stop_limit"))
    assert r.r_basis == "stop_limit" and r.risk_rs == pytest.approx(583 * 1.5)
    assert _sim(bars, "BUY", 99.0, LONG["targets"], cfg=SimConfig(stop_slippage_ticks=0)).exits[0].price == 99.0
    assert _sim(bars, "BUY", 99.0, LONG["targets"], cfg=SimConfig(stop_slippage_ticks=2)).exits[0].price == 98.98
    with pytest.raises(ValueError):
        SimConfig(r_basis="stop_limit")                              # stop_limit basis needs an offset


def test_d3_gap_between_trigger_and_limit_fills_no_worse_than_limit():
    r = _sim(_session("A", overrides={4: (98.7, 98.8, 98.6, 98.7)}), "BUY", 99.0, LONG["targets"],
             cfg=SimConfig(stop_limit_offset_pct=0.005))
    assert r.exit_reason == "STOP" and r.exits[0].price == 98.69


# ---------------------------------------------------------------- D4 + D18
def test_d4_d18_breakeven_checked_in_the_t1_bar_at_the_fill_price():
    r = _sim(_session("A", overrides={4: (100.5, 101.6, 99.9, 100.2)}), "BUY", 99.0, LONG["targets"], qty=583)
    assert [(e.reason, e.price, e.qty) for e in r.exits] == [("TARGET_1", 101.5, 291), ("BREAKEVEN_STOP", 100.0, 292)]
    assert r.be_reference == "entry_fill" and r.exits[1].time == r.exits[0].time


# ---------------------------------------------------------------- D5
def test_d5_index_registry_and_no_trades_on_indices():
    assert is_index_symbol("INDIA VIX") and is_index_symbol("IDX:INDIAVIX") and is_index_symbol("NIFTY50")
    assert not is_index_symbol("NIFTYBEES") and not is_index_symbol("RVNL")
    script = {("IDX:INDIAVIX", "09:45"): LONG, ("AAA", "09:45"): LONG}
    ad = Scripted(script)
    res = _engine([_session("AAA"), _session("IDX:INDIAVIX", px=14.0)], [ad],
                  universe_syms=["AAA", "IDX:INDIAVIX"]).run()
    assert {t.symbol for t in res.trades} == {"AAA"}
    assert all(sym != "IDX:INDIAVIX" for _, sym, *_ in ad.seen)


# ---------------------------------------------------------------- D6
def test_d6_allocation_ignores_alphabetical_order():
    def run(high, low):
        script = {(high, "09:45"): dict(LONG, score=2.0), (low, "09:45"): dict(LONG, score=1.0)}
        res = _engine([_session(high), _session(low)], [Scripted(script)],
                      EngineConfig(var_elm_rate=0.2, max_slots=1)).run()
        return [t.symbol for t in res.trades]
    assert run("AAA", "ZZZ") == ["AAA"]
    assert run("ZZZ", "AAA") == ["ZZZ"]                              # high score wins even when last alphabetically


# ---------------------------------------------------------------- D7
def test_d7_cluster_cap():
    script = {("AAA", "09:45"): LONG, ("BBB", "09:45"): LONG}
    res = _engine([_session("AAA"), _session("BBB")], [Scripted(script)], sectors={"AAA": "S1", "BBB": "S2"},
                  clusters={DAY: {"AAA": "C1", "BBB": "C1"}}).run()
    assert len(res.trades) == 1
    assert sorted(s.disposition for s in res.signals) == ["ALLOCATED", "REJECTED_GOVERNOR_CLUSTER_LIMIT"]


# ---------------------------------------------------------------- D8
def test_d8_evidence_labels_and_gate_never_passes():
    script = {("AAA", "09:45"): LONG, ("BBB", "09:45"): dict(LONG)}
    res = _engine([_session("AAA"), _session("BBB")], [Scripted(script)]).run()
    assert res.trades and all(t.evidence_class == "E1" for t in res.trades)
    assert all(s.counterfactual_evidence_class == "E1_CF" for s in res.signals)
    trades = [{"evidence_class": t.evidence_class, "net_r": 1.0, "session": t.session} for t in res.trades] * 200
    assert evaluate_gate(trades, sessions_observed=500)["passed"] is False


# ---------------------------------------------------------------- D9
def test_d9_zero_qty_counterfactual_is_per_share_and_nan_when_undefined():
    big = _session("BIG", px=60000.0, overrides={i: (60000.0, 60100.0, 59900.0, 60000.0) for i in range(24)})
    script = {("BIG", "09:45"): {"stop": 59000.0, "targets": ((61500.0, 0.5), (63000.0, 0.5))},
              ("AAA", "09:45"): {"stop": 101.0, "targets": ((101.5, 1.0),)}}           # long with stop above entry
    res = _engine([big, _session("AAA")], [Scripted(script)]).run()
    by = {s.symbol: s for s in res.signals}
    assert by["BIG"].disposition == "ZERO_QTY" and by["BIG"].counterfactual_fee_estimated
    assert math.isfinite(by["BIG"].counterfactual_net_r)
    assert math.isnan(by["AAA"].counterfactual_net_r)                                  # undefined R is NaN, not 0.0


# ---------------------------------------------------------------- D10
@pytest.mark.parametrize("n_bars", [24, 25])
def test_d10_policy_exit_in_both_session_shapes(n_bars):
    res = _engine([_session("AAA", n=n_bars), _session("IDX:NIFTY50", n=25, px=24000.0)],
                  [Scripted({("AAA", "13:00"): LONG})], universe_syms=["AAA"]).run()
    t = res.trades[0]
    assert t.exit_reason == "POLICY_EXIT" and t.exits[-1].time.astimezone(IST).strftime("%H:%M") == "15:00"
    assert res.rms_exits == 0


# ---------------------------------------------------------------- D11
def test_d11_per_strategy_series_have_zeros_on_inactive_sessions():
    d2 = DAY + timedelta(days=1)
    res = _engine([_session("AAA"), _session("AAA", day=d2)],
                  [Scripted({("AAA", "09:45"): dict(LONG, day=DAY)})]).run()
    pnl = res.per_strategy_daily_pnl["SCRIPT"]
    assert set(pnl) == {DAY, d2} and pnl[DAY] != 0.0 and pnl[d2] == 0.0
    assert set(res.per_strategy_daily_cf_r["SCRIPT"]) == {DAY, d2}


# ---------------------------------------------------------------- D13
def test_d13_context_and_intent_fields():
    ad = Scripted({("AAA", "09:45"): dict(LONG, score=1.5)})
    res = _engine([_session("AAA"), _session("IDX:NIFTY50", n=25, px=24000.0)], [ad], universe_syms=["AAA"]).run()
    _, _, _, idx_names, bar_index, n_bars = ad.seen[2]
    assert "IDX:NIFTY50" in idx_names and bar_index == n_bars - 1
    assert res.signals[0].priority_score == 1.5 and res.signals[0].r_basis == "trigger"


# ---------------------------------------------------------------- D14
@pytest.mark.parametrize("qty,expected", [(1, [("TARGET_2", 1)]), (2, [("TARGET_1", 1), ("TARGET_2", 1)]),
                                          (3, [("TARGET_1", 1), ("TARGET_2", 2)])])
def test_d14_small_quantities_do_not_crash(qty, expected):
    assert [(n, q) for n, _, q in plan_tranches(qty, LONG["targets"])] == [(int(e[0][-1]), e[1]) for e in expected]
    up = _session("A", overrides={4: (100.6, 101.6, 100.5, 101.5), 5: (101.5, 103.2, 101.4, 103.0)})
    r = _sim(up, "BUY", 99.0, LONG["targets"], qty=qty)
    assert [(e.reason, e.qty) for e in r.exits] == expected and sum(e.qty for e in r.exits) == qty


def test_d14_engine_survives_qty_one():
    px = 30000.0
    s = _session("HI", px=px, overrides={i: (px, px + 50, px - 50, px) for i in range(24)})
    res = _engine([s], [Scripted({("HI", "09:45"): {"stop": 29700.0, "targets": ((30450.0, 0.5), (30900.0, 0.5))}})]).run()
    assert res.trades[0].qty == 1


# ---------------------------------------------------------------- D15
def test_d15_counterfactual_matches_allocated_trade_and_respects_clamp():
    res = _engine([_session("AAA")], [Scripted({("AAA", "09:45"): LONG})]).run()
    assert res.signals[0].counterfactual_net_r == pytest.approx(res.trades[0].net_r)
    gapped = _session("AAA", overrides={3: (100.2, 100.4, 100.1, 100.3)})
    res = _engine([gapped], [Scripted({("AAA", "09:45"): LONG}, is_shadow=True)]).run()
    s = res.signals[0]
    assert s.disposition == "SHADOW_NOT_ALLOCATED" and s.counterfactual_exit_reason == "MISSED_CLAMP"
    assert math.isnan(s.counterfactual_net_r)                    # a clamp miss is not a counterfactual gain


# ---------------------------------------------------------------- D16
def test_d16_same_symbol_same_bar_and_missing_next_bar():
    script = {("AAA", "09:45"): LONG}
    res = _engine([_session("AAA")], [Scripted(script, name="S1"), Scripted(script, name="S2")]).run()
    assert [s.disposition for s in res.signals] == ["ALLOCATED", "BLOCKED_PENDING"]
    res = _engine([_session("AAA", n=3)], [Scripted(script)]).run()
    assert res.signals[0].disposition == "NO_NEXT_BAR" and res.trades == []


# ---------------------------------------------------------------- D17
class Peeker(StrategyAdapter):
    name, is_shadow = "PEEK", False

    def evaluate(self, ctx):
        ctx.store.bars(ctx.symbol, ctx.decision_time.astimezone(IST).date())
        return Decision(self.name, ctx.symbol, "NO_PATTERN")


def test_d17_store_view_refuses_current_and_future_sessions():
    with pytest.raises(LookAheadError):
        _engine([_session("AAA")], [Peeker()]).run()


def test_d17_changing_later_bars_does_not_change_earlier_decisions():
    base = _session("AAA")
    mutated = _session("AAA", overrides={i: (90.0, 90.2, 89.8, 90.0) for i in range(10, 24)})
    seen = []
    for sess in (base, mutated):
        ad = Scripted({})
        _engine([sess], [ad]).run()
        cutoff = sess[10].start
        seen.append([x[:3] for x in ad.seen if x[0] <= cutoff])
    assert seen[0] == seen[1] and seen[0]


# ---------------------------------------------------------------- D19
def test_d19_signal_close_entry_and_flat_cost_mode():
    cfg = SimConfig(entry_mode="signal_close", entry_slippage_ticks=0, cost_mode="flat_pct_of_entry_notional")
    r = _sim(_session("A"), "BUY", 99.0, LONG["targets"], qty=583, cfg=cfg)
    assert r.entry_price == 100.0 and r.entry_time.astimezone(IST).strftime("%H:%M") == "10:00"
    assert r.fees_rs == pytest.approx(0.00106 * 100.0 * 583, abs=0.01)
