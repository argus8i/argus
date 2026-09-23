"""Read-only probe of the current (uncommitted) OMS. Runs in a temp dir; touches no canonical file."""
import json, sys, tempfile, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from antigravity.daemons.hybrid_execution_oms import HybridExecutionOMS
from antigravity.models.execution_policy import PolicyConfig, ExecutionMode

def depth(d, ltps):
    (d / "live_depth_track2.json").write_text(json.dumps({"data_valid": True, "watchlist": [{"symbol": s, "ltp": p} for s, p in ltps.items()]}))

def cand(sym, **kw):
    c = {"symbol": sym, "entry_price": 100.0, "stop_loss": 97.0, "shares": 500, "atr14": 5.0, "sector": None}
    c.update(kw); c.pop("sector") if c.get("sector") is None else None
    return c

print("== A. Candidate with NO var_elm_rate under AUTONOMOUS mode ==")
with tempfile.TemporaryDirectory() as t:
    d = Path(t); depth(d, {"RVNL": 100.05})
    oms = HybridExecutionOMS(config=PolicyConfig(mode=ExecutionMode.AUTONOMOUS), corpus_rs=250000.0, output_dir=d)
    intent, msg = oms.submit_candidate(cand("RVNL"))
    print("   result:", msg[:110])

print("== B. Four pending co-pilot signals (R02 re-test) ==")
with tempfile.TemporaryDirectory() as t:
    d = Path(t); depth(d, {s: 100.05 for s in ("RVNL", "BDL", "CDSL", "IREDA")})
    oms = HybridExecutionOMS(config=PolicyConfig(mode=ExecutionMode.CO_PILOT), corpus_rs=250000.0, output_dir=d)
    for s in ("RVNL", "BDL", "CDSL", "IREDA"):
        i, m = oms.submit_candidate(cand(s))
        print(f"   {s}: {m[:95]}")

print("== C. Emergency flatten of a FILLED/OPEN position that is losing ==")
with tempfile.TemporaryDirectory() as t:
    d = Path(t); depth(d, {"BDL": 1100.0})   # market has fallen 8.3% below entry
    (d / "paper_orders.jsonl").write_text(json.dumps({"order_id": "ORD_X", "symbol": "BDL", "shares": 50, "entry_price": 1200.0, "status": "OPEN", "risk_rs": 1500, "notional": 60000}) + "\n")
    oms = HybridExecutionOMS(config=PolicyConfig(mode=ExecutionMode.CO_PILOT), corpus_rs=250000.0, output_dir=d)
    oms.emergency_flatten_all(reason="PROBE")
    last = [json.loads(l) for l in (d / "paper_orders.jsonl").read_text().splitlines()][-1]
    print(f"   market LTP 1100, entry 1200 -> recorded exit_price={last.get('exit_price')} status={last.get('status')} (loss erased: {last.get('exit_price') == 1200.0})")

print("== D. Pre-armed intent lifetime ==")
with tempfile.TemporaryDirectory() as t:
    d = Path(t); depth(d, {"CDSL": 100.05})
    oms = HybridExecutionOMS(config=PolicyConfig(mode=ExecutionMode.CO_PILOT), corpus_rs=250000.0, output_dir=d)
    i, m = oms.submit_candidate(cand("CDSL", pre_armed=True))
    if i:
        life = (datetime.fromisoformat(i.expires_at) - datetime.fromisoformat(i.created_at)).total_seconds()
        print(f"   status={i.status.value}; armed lifetime = {life:.0f}s (an 'armed before the breakout' order dies after {life:.0f}s)")
    else:
        print("   ", m)

print("== E. Per-symbol staleness: depth file fresh, but symbol quote could be hours old ==")
with tempfile.TemporaryDirectory() as t:
    d = Path(t)
    (d / "live_depth_track2.json").write_text(json.dumps({"data_valid": True, "watchlist": [{"symbol": "IREDA", "ltp": 100.05, "last_trade_time": "2026-09-23T09:20:00+05:30"}]}))
    oms = HybridExecutionOMS(config=PolicyConfig(mode=ExecutionMode.CO_PILOT), corpus_rs=250000.0, output_dir=d)
    print("   sampled LTP despite 09:20 last-trade stamp:", oms._sample_live_ltp("IREDA"))
