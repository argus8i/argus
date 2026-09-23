"""Read-only probe for Claude outputs/2026-09-23_independent_desk_audit.md. Writes nothing."""
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(REPO))
from antigravity.models.liquid_momentum_screener import LiquidMomentumEngine
from antigravity.models.track2_paper_execution import calculate_transaction_costs
from antigravity.models.track2_portfolio_risk_governor import PortfolioRiskGovernor

DP = 15.93  # the project's own DP figure
def rt_cost(price_in, price_out, qty, intraday):
    b = calculate_transaction_costs(price_in, qty, "BUY", is_intraday=intraday)["total_cost"]
    s = calculate_transaction_costs(price_out, qty, "SELL", is_intraday=intraday)["total_cost"]
    return b + s + (0 if intraday else DP)

print("=== 1. Today's real RVNL candidate (project's own functions) ===")
e, stop, q = 212.58, 210.79, 470
R_trig = q*(e-stop)
R_lim = q*(e-209.74)
c_cnc = rt_cost(e, stop, q, False)
c_mis = rt_cost(e, stop, q, True)
slip = 0.0005*e*q*2   # 5 bps per side slippage/spread, a light assumption
print(f"notional={e*q:,.0f}  R(to trigger)={R_trig:,.0f}  R(to SL-limit)={R_lim:,.0f}  (budget says 1,500)")
print(f"CNC statutory round trip = {c_cnc:,.0f}  = {c_cnc/R_trig:.2f}R ; +5bps/side slippage -> {(c_cnc+slip)/R_trig:.2f}R")
print(f"MIS statutory round trip = {c_mis:,.0f}  = {c_mis/R_trig:.2f}R ; +slippage -> {(c_mis+slip)/R_trig:.2f}R")
c = (c_cnc+slip)/R_trig
def be(win_R, loss_R, c):
    # p*(win-c) = (1-p)*(loss+c)
    return (loss_R + c)/((win_R - c) + (loss_R + c))
print(f"Breakeven win-rate, CNC, full 2R target, stop at trigger: {be(2.0,1.0,c):.0%}")
print(f"Breakeven win-rate, CNC, two-tranche modal (+0.75R, runner at BE): {be(0.75,1.0,c):.0%}")
print(f"Breakeven win-rate, CNC, two-tranche modal, stop fills at SL-limit (-1.59R): {be(0.75,1.59,c):.0%}")

print("\n=== 2. Stop-distance grid (entry=200, daily ATR large, DTV large) ===")
print(" stop%  shares  notional  constrained_by        R_trig   CNC+slip cost in R")
for pct in (0.5,0.75,1.0,1.5,2.0,2.5,3.0):
    entry=200.0; orl=round(entry*(1-pct/100),2)
    s = LiquidMomentumEngine.calculate_position_size(entry_price=entry, or_low=orl, atr14=50.0, dtv_med20_cr=500.0, risk_budget_rs=1500.0, order_execution_type="SL_LIMIT")
    Rt = s.shares*(entry-s.stop_price)
    cc = rt_cost(entry, s.stop_price, s.shares, False) + 0.0005*entry*s.shares*2
    print(f" {pct:4.2f}  {s.shares:6d}  {s.notional_value:9,.0f}  {s.constrained_by:20s} {Rt:7,.0f}   {cc/Rt:.2f}R")

print("\n=== 3. Can the portfolio ever hold 3 positions at typical sizing? ===")
for buf in (75000.0, 50000.0):
    gov = PortfolioRiskGovernor.calibrate_for_corpus(250000.0, cash_buffer_rs=buf)
    held=[]
    names=["RVNL","BDL","CDSL"]
    for n in names:
        r = gov.assess_candidate(n, 212.58, 210.79, 470, active_positions=held)
        print(f" buffer={buf:,.0f} cap={gov.total_capital_allocation_rs:,.0f}: {n} -> {'APPROVED' if r.is_approved else r.rejection_reason[:60]}")
        if r.is_approved: held.append({"symbol":n,"entry_price":212.58,"stop_price":210.79,"quantity":470})
