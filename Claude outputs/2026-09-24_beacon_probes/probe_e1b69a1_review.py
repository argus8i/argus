"""Rule 8 review probe for commit e1b69a1 (audit rectifications). Read-only.

Checks each claimed fix against the code as committed, with synthetic inputs and the
repo's historical candles. Writes nothing.
"""
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import time as dtime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from antigravity.models.track2_multi_strategy_engine import MultiStrategyEngine, UnifiedTradeSignal  # noqa: E402
from antigravity.models.track2_alpha_engine import MultiTimeframeAlphaEngine  # noqa: E402
from antigravity.models.market_regime_filter import MarketRegimeSnapshot, MarketRegimeState  # noqa: E402
from antigravity.models.track2_portfolio_risk_governor import PortfolioRiskGovernor as G  # noqa: E402
from antigravity.models.track2_paper_execution import BracketOrderManager as B  # noqa: E402


def section(title):
    print("\n=== " + title)


def session(n, px=100.0, vol=20000):
    out = []
    for i in range(n):
        hh, mm = divmod(9 * 60 + 15 + 15 * i, 60)
        out.append({"timestamp": f"2026-09-24T{hh:02d}:{mm:02d}:00+05:30", "open": px, "high": px + 1.0,
                    "low": px - 0.8, "close": px + 0.4, "volume": vol})
        px += 0.4
    return out


eng = MultiStrategyEngine()
base = dict(symbol="TEST", sector="X", daily_candles=None, hist_median_volume_15m=10000.0,
            daily_ema20=90.0, daily_ema50=85.0, atr14_points=3.0)

section("R1 COMPASS branch when its inputs are actually supplied")
bars = session(8)
try:
    out = eng.evaluate_symbol(candles_15m=bars, sector_candle_now=bars[-1], sector_candle_4_bars_ago=bars[-5],
                              market_candle_now=bars[-1], market_candle_4_bars_ago=bars[-5], **base)
    print(f"  returned {len(out)} signals: {[s.strategy_type for s in out]}")
except Exception as exc:
    print(f"  {type(exc).__name__}: {exc}")

section("R2 ORB volume gate with a missing baseline: old entry point vs new wrapper")
reg = MarketRegimeSnapshot(state=MarketRegimeState.BULLISH_EXPANSION, nifty_ltp=1.0, nifty_or_high=1.0,
                           nifty_or_low=1.0, ad_ratio=1.5, advances=None, declines=None, reason="probe",
                           allow_standard_orb=True, min_volume_multiple=2.5)
b = session(3, vol=20000)
b[-1]["close"] = b[0]["high"] + 0.5
old = MultiTimeframeAlphaEngine.evaluate_15m_orb("TEST", b[-1]["close"], b[0]["high"], b[0]["low"], 20000, 0, 3.0, reg)
new = MultiTimeframeAlphaEngine.evaluate_candidate("TEST", b, 0.0, 90.0, 85.0, 3.0)
print(f"  evaluate_15m_orb(median=0)      -> {old.decision} (volume multiple {old.volume_multiple})")
print(f"  evaluate_candidate(median=0.0)  -> {new.decision} (volume multiple {new.volume_multiple})")
print("  regime inside the wrapper: a BULLISH_EXPANSION snapshot with ad_ratio 1.5, 35/15 advances/declines is"
      " fabricated unless market_regime_allows_orb=False; the engine always passes True")

section("R3 shadow isolation default in rank_and_allocate")
mk = dict(entry_price=100.0, stop_price=99.0, target_tranche1=101.5, target_tranche2=103.0, shares=50,
          notional_value_rs=5000.0, actual_risk_rs=50.0, risk_pct=1.0, volume_multiple=2.0, details={})
orb = UnifiedTradeSignal(symbol="A", strategy_type="ORB_MOMENTUM", conviction_score=0.5, sector="S1", is_shadow=False, **mk)
vw = UnifiedTradeSignal(symbol="B", strategy_type="VWAP_RECLAIM", conviction_score=0.9, sector="S2", is_shadow=True, **mk)
sel = eng.rank_and_allocate([orb, vw])
print(f"  rank_and_allocate([ORB active, VWAP shadow]) with default args -> "
      f"{[(s.strategy_type, s.is_shadow) for s in sel]}")

section("R4 per-slot cap as the daemons build the governor")
for flag in (None, True):
    g = G.calibrate_for_corpus() if flag is None else G.calibrate_for_corpus(enforce_slot_cap=True)
    r = g.assess_candidate("RVNL", 400.0, 396.0, 375, active_positions=[], var_elm_rate=0.20)
    print(f"  calibrate_for_corpus({'' if flag is None else 'enforce_slot_cap=True'}): Rs 1,50,000 position -> "
          f"approved={r.is_approved} {r.reason or ''}")
r = G.calibrate_for_corpus().assess_candidate("RVNL", 400.0, 404.0, 100, active_positions=[], var_elm_rate=0.20)
print(f"  short (stop above entry): {r.reason}")

section("R5 bracket cutoff with the defaults every caller would use")
for t, flag in ((dtime(15, 9), False), (dtime(15, 9), True), (dtime(15, 11), False)):
    m = B.create_bracket("r5", "TEST", 100.0, 95.0, 100)
    m = B.update_bracket_quote(m, ltp=101.0, current_time_ist=t, is_cas_eligible=flag)
    print(f"  product {m.product_type}, {t.strftime('%H:%M')}, is_cas_eligible={flag} -> {m.terminal_state} "
          f"(net {m.realized_pnl_net}, execution_evidence=False)")
m = B.create_bracket("r5b", "TEST", 100.0, 95.0, 100)
m = B.update_bracket_quote(m, ltp=94.0, current_time_ist=dtime(11, 0))
print(f"  one quote through the stop, no evidence -> {m.terminal_state}, net {m.realized_pnl_net}")

section("R6 engine over every real session (8 stocks x 32 days, bar by bar)")
raw = json.loads((REPO / "shared/track2_liquid/historical_candles_track2.json").read_text(encoding="utf-8"))["symbols"]
days = defaultdict(lambda: defaultdict(list))
for s, v in raw.items():
    for bar in v["bars"]:
        days[s][bar["timestamp"][:10]].append(bar)
stocks = [s for s in raw if s != "NIFTY50"]
dates = sorted(days[stocks[0]])
errors, sigs, calls = Counter(), Counter(), 0
for di, d in enumerate(dates):
    for s in stocks:
        bars = days[s][d]
        for k in range(len(bars)):
            prior = [x["volume"] for dd in dates[max(0, di - 20):di] for x in days[s][dd]
                     if x["timestamp"][11:16] == bars[k]["timestamp"][11:16]]
            med = statistics.median(prior) if prior else 0.0
            calls += 1
            try:
                out = eng.evaluate_symbol(symbol=s, sector="X", candles_15m=bars[:k + 1], daily_candles=None,
                                          hist_median_volume_15m=med, daily_ema20=0.02, daily_ema50=0.01,
                                          atr14_points=bars[0]["close"] * 0.03)
                for sig in out:
                    sigs[(sig.strategy_type, sig.is_shadow, "no-baseline" if med == 0 else "baseline")] += 1
            except Exception as exc:
                errors[f"{type(exc).__name__}: {exc}"] += 1
print(f"  evaluate_symbol calls {calls}, exceptions {sum(errors.values())}")
for e, n in errors.most_common(3):
    print(f"    {n} x {e}")
for key, n in sorted(sigs.items()):
    print(f"    signals {key}: {n}")
