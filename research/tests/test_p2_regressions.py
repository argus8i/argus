"""P2 regression tests against the 24-Sep audit (plan P2, "Regression test 1/2"; research/evidence/MEASURED_FACTS.md).

Both run the new engine on shared/track2_liquid/historical_candles_track2.json in audit mode:
entry at the signal close, trigger-basis R, no slippage ticks, flat cost 0.106% of entry notional,
two-tranche 1.5R/3R with breakeven, flat at the open of the 15:00 bar.
"""
import math
import statistics
from datetime import date
from pathlib import Path

import pytest

from research.backtest.bars import CandleStore
from research.backtest.engine import BacktestEngine, EngineConfig
from research.backtest.strategies import Decision, SignalIntent, StrategyAdapter
from research.backtest.universe import PointInTimeUniverse
from research.studies.signal_sim import simulate_signal

DATA = Path(__file__).resolve().parents[2] / "shared" / "track2_liquid" / "historical_candles_track2.json"
pytestmark = pytest.mark.skipif(not DATA.exists(), reason="repo 32-session file missing")

AUDIT_CFG = EngineConfig(var_elm_rate=0.20, entry_mode="signal_close", entry_slippage_ticks=0, stop_slippage_ticks=0,
                         exit_slippage_ticks=0, r_basis="trigger", cost_mode="flat_pct_of_entry_notional",
                         flat_cost_pct=0.00106)


@pytest.fixture(scope="module")
def store():
    return CandleStore.from_historical_json(DATA)


def _universe(store):
    syms = [s for s in store.symbols if store.kind(s) == "TRADABLE"]
    return PointInTimeUniverse.assumed_static(syms, date(2026, 1, 1), date(2026, 12, 31), note="audit replay")


def _two_tranche(e, stop):
    r = e - stop
    return ((e + 1.5 * r, 0.5), (e + 3.0 * r, 0.5))


class FirstBreak(StrategyAdapter):
    """First close above the 09:15 bar high, bar start before 14:45, no volume gate (audit probe section D)."""
    name, is_shadow = "FIRST_BREAK", True

    def __init__(self):
        self.fired = set()

    def evaluate(self, ctx):
        key = (ctx.symbol, ctx.current.session_date)
        if ctx.bar_index < 1 or ctx.current.hm >= "14:45" or key in self.fired:
            return Decision(self.name, ctx.symbol, "NO_PATTERN")
        if ctx.current.close <= ctx.bars[0].high:
            return Decision(self.name, ctx.symbol, "NO_PATTERN")
        self.fired.add(key)
        stop = ctx.bars[0].low
        intent = SignalIntent.from_ctx(ctx, self.name, "BUY", stop, _two_tranche(ctx.current.close, stop))
        intent.is_shadow = True
        return Decision(self.name, ctx.symbol, "SIGNAL", intent)


def _replay(store, signals):
    """Re-simulate every signal with the same pure function and return the SimResults."""
    out = []
    for s in signals:
        day = s.signal_time.date()
        bars = store.bars(s.symbol, day)
        i = next(k for k, b in enumerate(bars) if b.end == s.signal_time)
        sim = simulate_signal(bars, i, s.side, s.entry_ref, s.stop_loss, s.targets, s.max_bars, s.qty_planned,
                              AUDIT_CFG.sim_config(), day)
        assert sim.net_r == pytest.approx(s.counterfactual_net_r)       # engine counterfactual = pure function
        out.append(sim)
    return out


def test_regression_1_first_orb_breaks(store):
    res = BacktestEngine(store, _universe(store), [FirstBreak()], AUDIT_CFG).run()
    sims = _replay(store, res.signals)
    assert len(sims) == 103
    t1 = sum(1 for s in sims if any(e.reason == "TARGET_1" for e in s.exits))
    stops = sum(1 for s in sims if s.exit_reason == "STOP")
    timed = sum(1 for s in sims if s.exit_reason == "POLICY_EXIT" and not any(e.reason == "TARGET_1" for e in s.exits))
    assert abs(stops - 18) <= 1 and abs(t1 - 10) <= 1 and abs(timed - 75) <= 1
    net = [s.net_r for s in sims]
    assert statistics.mean(net) == pytest.approx(-0.084, abs=0.005)
    assert statistics.mean(s.gross_r for s in sims) == pytest.approx(-0.003, abs=0.005)
    assert statistics.pstdev(net) == pytest.approx(0.741, abs=0.01)                          # MEASURED_FACTS F6
    capped = [58333.33 * (s.entry_price - s.stop_trigger) / s.entry_price for s in sims
              if (s.entry_price - s.stop_trigger) / s.entry_price < 1500 / 58333.33]
    assert len(capped) / len(sims) == pytest.approx(0.961, abs=0.01)                          # F4
    assert statistics.median(capped) == pytest.approx(832, abs=10)                              # F4


class AuditProdOrb(StrategyAdapter):
    """Audit probe section C: MultiTimeframeAlphaEngine.evaluate_15m_orb (production code, read-only import),
    BULLISH regime with a 2.5x volume multiple, trend gates disabled, >= 10 prior sessions for the bucket median,
    daily ATR14 from >= 10 prior true ranges, first signal per stock-day."""
    name, is_shadow = "ORB_AUDIT_C", True

    def __init__(self):
        from antigravity.models.market_regime_filter import MarketRegimeSnapshot, MarketRegimeState
        from antigravity.models.track2_alpha_engine import MultiTimeframeAlphaEngine
        self.engine = MultiTimeframeAlphaEngine
        self.regime = MarketRegimeSnapshot(state=MarketRegimeState.BULLISH_EXPANSION, nifty_ltp=1.0,
                                           nifty_or_high=1.0, nifty_or_low=1.0, ad_ratio=1.5, advances=None,
                                           declines=None, reason="audit probe section C",
                                           allow_standard_orb=True, min_volume_multiple=2.5)
        self.fired, self.cache = set(), {}

    def _baseline(self, ctx):
        key = (ctx.symbol, ctx.current.session_date)
        if key not in self.cache:
            prior = ctx.store.sessions(ctx.symbol)[-20:]
            vols = {}
            for d in prior:
                for b in ctx.store.bars(ctx.symbol, d):
                    vols.setdefault(b.hm, []).append(b.volume)
            daily = ctx.store.daily(ctx.symbol)[-15:]
            trs = [max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)) for p, c in zip(daily, daily[1:])]
            atr = sum(trs) / len(trs) if len(trs) >= 10 else None
            self.cache[key] = (len(prior), vols, atr)
        return self.cache[key]

    def evaluate(self, ctx):
        key = (ctx.symbol, ctx.current.session_date)
        if ctx.bar_index < 1 or key in self.fired:
            return Decision(self.name, ctx.symbol, "NO_PATTERN")
        n_prior, vols, atr = self._baseline(ctx)
        v = vols.get(ctx.current.hm, [])
        if n_prior < 10 or len(v) < 10 or atr is None:
            return Decision(self.name, ctx.symbol, "DATA_INVALID")
        o = self.engine.evaluate_15m_orb(ctx.symbol, ctx.current.close, ctx.bars[0].high, ctx.bars[0].low,
                                         int(ctx.current.volume), int(statistics.median(v)), atr, self.regime)
        if not o.passed_all_gates:
            return Decision(self.name, ctx.symbol, "NO_PATTERN")
        self.fired.add(key)
        intent = SignalIntent.from_ctx(ctx, self.name, "BUY", o.stop_price, _two_tranche(o.entry_price, o.stop_price))
        intent.is_shadow = True
        return Decision(self.name, ctx.symbol, "SIGNAL", intent)


def test_regression_2_audit_production_code_orb(store):
    res = BacktestEngine(store, _universe(store), [AuditProdOrb()], AUDIT_CFG).run()
    sims = _replay(store, res.signals)
    assert abs(len(sims) - 41) <= 3
    mean = statistics.mean(s.net_r for s in sims)
    assert -0.20 <= mean <= 0.22                                   # audit CI for n = 41: [-0.20, +0.22]
