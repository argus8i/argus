"""Read-only probes for the BEACON mandate red-team audit (24-Sep-2026).

Imports production modules and calls them with synthetic inputs. Writes nothing.
Run from anywhere:  .venv/Scripts/python.exe "Claude outputs/2026-09-24_beacon_probes/probe_code_claims.py"
"""
import inspect
import sys
from datetime import time as dtime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from antigravity.models.track2_paper_execution import (  # noqa: E402
    BracketOrderManager as B,
    calculate_transaction_costs as tc,
)
from antigravity.models.track2_multi_strategy_engine import MultiStrategyEngine  # noqa: E402
from antigravity.models.track2_portfolio_risk_governor import PortfolioRiskGovernor as G  # noqa: E402
from antigravity.models.track2_last_light_strategy import LastLightStrategy  # noqa: E402
from antigravity.models.track2_shared_features import SharedFeatureEngine as F  # noqa: E402


def section(title):
    print("\n=== " + title)


# P1. What does a round trip actually cost under the repo's own fee function?
section("P1 round-trip cost from calculate_transaction_costs (slot-sized orders)")
for price in (50.0, 300.0, 1000.0, 3000.0):
    qty = int(58333 // price)
    notional = price * qty
    for intraday in (True, False):
        buy = tc(price, qty, "BUY", intraday)["total_cost"]
        sell = tc(price, qty, "SELL", intraday)["total_cost"]
        print(f"  price {price:7.1f} qty {qty:5d} {'MIS' if intraday else 'CNC'}: "
              f"Rs {buy + sell:7.2f} = {100 * (buy + sell) / notional:.4f}% of one-side notional")
qty = 20
print(f"  small order (Rs 6,000, MIS): "
      f"{100 * (tc(300.0, qty, 'BUY')['total_cost'] + tc(300.0, qty, 'SELL')['total_cost']) / 6000:.4f}%")

# P1b. Where does each strategy's cost filter bind, as coded vs single-counted?
section("P1b cost-filter binding threshold on risk% (f = 0.00106)")
f = 0.00106
rows = [
    ("TRAPDOOR  (entry+exit notional) x f, k=1.8R", 6 * f / (1.8 * (1 - 3 * f)), 3 * f / 1.8, 0.40),
    ("COMPASS   (entry+exit notional) x f, k=2.0R", 6 * f / (2.0 * (1 - 3 * f)), 3 * f / 2.0, 0.40),
    ("LAST_LIGHT notional x f x 2, T1=1.5R", 4 * f, 2 * f, 0.50),
]
for name, coded, single, floor in rows:
    print(f"  {name}: coded rejects risk% < {100 * coded:.3f}; single-count < {100 * single:.3f}; "
          f"strategy risk floor {floor:.2f}% -> coded filter {'CAN' if 100 * coded > floor else 'NEVER'} bind")
print(f"  RECOIL notional x f x 2, target=VWAP: coded rejects target distance < {100 * 6 * f:.3f}% of price; "
      f"single-count < {100 * 3 * f:.3f}%  (no floor on target distance -> binds)")

# P2. Does MultiStrategyEngine.evaluate_symbol run on a normal session?
section("P2 MultiStrategyEngine.evaluate_symbol on real-shaped bars")
bars = []
px = 100.0
for i in range(8):
    hh, mm = divmod(9 * 60 + 15 + 15 * i, 60)
    bars.append({"timestamp": f"2026-09-24T{hh:02d}:{mm:02d}:00+05:30", "open": px, "high": px + 1.0,
                 "low": px - 0.8, "close": px + 0.4, "volume": 20000})
    px += 0.4
eng = MultiStrategyEngine()
for n in (1, 2, 6, 8):
    try:
        out = eng.evaluate_symbol(symbol="TEST", sector="X", candles_15m=bars[:n], daily_candles=None,
                                  hist_median_volume_15m=10000.0, daily_ema20=90.0, daily_ema50=85.0,
                                  atr14_points=3.0)
        print(f"  {n} bars -> returned {len(out)} signals")
    except Exception as exc:  # the point of the probe
        print(f"  {n} bars -> {type(exc).__name__}: {exc}")
src = inspect.getsource(MultiStrategyEngine.evaluate_symbol)
print("  'compass_engine' referenced inside evaluate_symbol:", "compass_engine" in src)
print("  orb_engine has evaluate_candidate:", hasattr(eng.orb_engine, "evaluate_candidate"))
print("  trapdoor_engine has evaluate_setup:", hasattr(eng.trapdoor_engine, "evaluate_setup"))

# P3. Bracket cutoff, product default and evidence asymmetry
section("P3 BracketOrderManager cutoff / evidence behaviour")
b = B.create_bracket("p3a", "TEST", 100.0, 95.0, 100)
print("  default product_type:", b.product_type)
b = B.update_bracket_quote(b, ltp=101.0, current_time_ist=dtime(15, 20))
print("  default (CNC) bracket at 15:20 ->", b.terminal_state)
m = B.create_bracket("p3b", "TEST", 100.0, 95.0, 100, product_type="MIS")
m = B.update_bracket_quote(m, ltp=101.0, current_time_ist=dtime(15, 13))
print("  MIS, is_cas_eligible left at default, 15:13 ->", m.terminal_state)
m = B.update_bracket_quote(m, ltp=101.0, current_time_ist=dtime(15, 13), is_cas_eligible=True)
print(f"  MIS, CAS flag, 15:13, no execution evidence -> {m.terminal_state}, net {m.realized_pnl_net}")
m = B.create_bracket("p3c", "TEST", 100.0, 95.0, 100, product_type="MIS")
m = B.update_bracket_quote(m, ltp=101.0, timestamp="2026-09-24T15:40:00+05:30")
print("  MIS, clock not passed (current_time_ist=None) at 15:40 ->", m.terminal_state)
m = B.create_bracket("p3d", "TEST", 100.0, 95.0, 100, product_type="MIS")
m = B.update_bracket_quote(m, ltp=108.0, current_time_ist=dtime(11, 0))
print("  T1 (107.5) touched without evidence -> t1 filled:", m.is_t1_target_filled, "| state:", m.terminal_state)
m = B.update_bracket_quote(m, ltp=94.0, current_time_ist=dtime(11, 15))
print(f"  then one quote at 94 without evidence -> {m.terminal_state}, net {m.realized_pnl_net}")
print("  non-test callers of create_bracket/update_bracket_quote: see grep in report (none found)")

# P4. Governor limits as deployed (calibrate_for_corpus) vs mandate claims
section("P4 PortfolioRiskGovernor.calibrate_for_corpus()")
g = G.calibrate_for_corpus()
print(f"  single risk {g.max_single_trade_risk_rs}, aggregate {g.max_aggregate_risk_rs}, "
      f"total notional {g.total_capital_allocation_rs}, var gate {g.enforce_var_elm_gate}")
r = g.assess_candidate("RVNL", 400.0, 396.0, 375, active_positions=[], var_elm_rate=0.20)
print(f"  one Rs 1,50,000 position (2.6 slots): approved={r.is_approved} "
      f"notional={r.proposed_notional_rs} reason={r.reason}")
r = g.assess_candidate("RVNL", 400.0, 404.0, 100, active_positions=[], var_elm_rate=0.20)
print(f"  short (stop above entry, as RECOIL SELL): reason={r.reason}")
d = G()
print(f"  bare PortfolioRiskGovernor() defaults: aggregate {d.max_aggregate_risk_rs}, "
      f"total {d.total_capital_allocation_rs}, var gate {d.enforce_var_elm_gate}")

# P5. LAST_LIGHT time window when the timestamp has no 'T'
section("P5 LAST_LIGHT time-window parse")
ll = LastLightStrategy()
for fmt in ("2026-09-24T11:00:00+05:30", "2026-09-24 11:00:00"):
    bs = [dict(x) for x in bars]
    bs[-1]["timestamp"] = fmt
    res = ll.evaluate_setup("TEST", bs, bucket_median_vol=10000.0)
    print(f"  last bar stamped {fmt!r} (11:00, outside 14:00-14:30) -> {res.decision}")

# P6. RVOL when the volume baseline is missing
section("P6 SharedFeatureEngine RVOL with a missing baseline")
print("  calculate_rvol(50000, bucket_median=0) ->", F.calculate_rvol(50000, 0.0))
print("  VWAP/SQUEEZE with hist median 0 -> vol multiple forced to 1.0 (fails 1.8x/2.0x floor)")
