"""Read-only probe for Claude outputs/2026-09-23_independent_desk_audit.md. Writes nothing."""
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
import sys, json, statistics
from collections import Counter, defaultdict
sys.path.insert(0, str(REPO))
from antigravity.models.liquid_momentum_screener import LiquidMomentumEngine
from antigravity.models.market_regime_filter import MarketRegimeFilter
from antigravity.models.track2_paper_execution import calculate_transaction_costs

h = json.load(open(REPO / "shared" / "track2_liquid" / "historical_candles_track2.json", encoding="utf-8"))
def rows(r, key):
    out=[]
    for b in r[key]:
        if isinstance(b, dict): out.append(b)
        else: out.append(dict(zip(("timestamp","open","high","low","close","volume"), b)))
    return out
data = {s: {"m": rows(r,"bars"), "d": rows(r,"daily_bars")} for s, r in h["symbols"].items()}

# 1. which slot is missing for stocks?
slots = Counter(str(b["timestamp"])[11:16] for b in data["RVNL"]["m"])
nslots = Counter(str(b["timestamp"])[11:16] for b in data["NIFTY50"]["m"])
print("slots in NIFTY but not RVNL:", sorted(set(nslots)-set(slots)), "| RVNL first/last slot:", min(slots), max(slots))

# 2. replay the paper-desk rule
days = sorted({str(b["timestamp"])[:10] for b in data["RVNL"]["m"]})
by = {s: defaultdict(list) for s in data}
for s in data:
    for b in data[s]["m"]: by[s][str(b["timestamp"])[:10]].append(b)
daily = {s: {str(b["timestamp"])[:10]: b for b in data[s]["d"]} for s in data}
signals=[]
for di, day in enumerate(days):
    if di < 20: continue
    prior = days[di-20:di]
    nb = by["NIFTY50"][day]
    nor = next((b for b in nb if str(b["timestamp"])[11:16]=="09:15"), None)
    for s in data:
        if s=="NIFTY50": continue
        bars = by[s][day]
        op = next((b for b in bars if str(b["timestamp"])[11:16]=="09:15"), None)
        if not op: continue
        # daily ATR14 over prior 20 days
        dd=[daily[s][d] for d in prior if d in daily[s]]
        tr=[]; pc=None
        for x in dd:
            r=[x["high"]-x["low"]]
            if pc is not None: r += [abs(x["high"]-pc), abs(x["low"]-pc)]
            tr.append(max(r)); pc=x["close"]
        atr=sum(tr[-14:])/14
        fired=False
        for b in bars:
            t=str(b["timestamp"])[11:16]
            if not ("09:30"<=t<="14:15") or fired: continue
            med = statistics.median([x["volume"] for d in prior for x in by[s][d] if str(x["timestamp"])[11:16]==t] or [0])
            if med<=0: continue
            # nifty regime at this bar close
            nlast = [x for x in nb if str(x["timestamp"])[11:16]<=t]
            reg = MarketRegimeFilter.evaluate_regime(float(nlast[-1]["close"]), float(nor["high"]), float(nor["low"]))
            res = LiquidMomentumEngine.evaluate_15m_orb_breakout(s, b["close"], op["high"], op["low"], b["volume"], med, atr, 2.5, reg)
            if res["signal"]=="RESEARCH_ORB_HYPOTHESIS":
                fired=True
                sz = LiquidMomentumEngine.calculate_position_size(entry_price=b["close"], or_low=op["low"], atr14=atr, dtv_med20_cr=500, order_execution_type="SL_LIMIT")
                # crude outcome on same-day bars after signal: stop (trigger) vs T1 (1.5R) vs close; ambiguous bars flagged
                e=b["close"]; st=sz.stop_price; R=e-st; t1=e+1.5*R
                after=[x for x in bars if str(x["timestamp"])[11:16]>t]
                out="EOD"; ex=after[-1]["close"] if after else e
                for x in after:
                    hs, ht = x["low"]<=st, x["high"]>=t1
                    if hs and ht: out="AMBIGUOUS"; ex=st; break
                    if hs: out="STOP"; ex=st; break
                    if ht: out="T1"; ex=t1; break
                q=sz.shares
                cost = calculate_transaction_costs(e,q,"BUY",is_intraday=False)["total_cost"]+calculate_transaction_costs(ex,q,"SELL",is_intraday=False)["total_cost"]+15.93
                signals.append((day,t,s,round(e,2),round(R/e*100,2),sz.constrained_by,out,round((ex-e)/R,2),round(cost/(q*R),2), reg.min_volume_multiple))
print(f"\nEvaluable days: {len(days)-20} ({days[20]}..{days[-1]}), symbols: 8, symbol-days: {8*(len(days)-20)}")
print("day        bar   sym         entry  stop%  sized_by             outcome  gross_R  cost_R  vol_req")
for x in signals: print(f"{x[0]} {x[1]} {x[2]:10s} {x[3]:8.2f} {x[4]:5.2f}  {x[5]:20s} {x[6]:8s} {x[7]:6.2f}  {x[8]:5.2f}   {x[9]}")
print("signals:", len(signals), "| per symbol-day:", round(len(signals)/(8*(len(days)-20)),3))
