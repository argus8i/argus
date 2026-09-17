"""Read-only diagnostic probes. Synthetic inputs are tests, not market evidence.

Run from project root: python -B CHATGPT/audit_checks_2026_09_09.py
Models are imported without bytecode writes; monitor persistence is disabled in memory.
Results print to stdout. No orders, data downloads or core-model changes.
"""
import sys
sys.dont_write_bytecode = True
import csv
import hashlib
import importlib.util
import io
import json
import typing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted((ROOT / 'antigravity/models').glob('*.py')) + sorted((ROOT / 'claude/models').glob('*.py'))
before = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in SOURCES}

def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

out = []
def probe(name, fn):
    try:
        out.append({'probe': name, 'observed': fn()})
    except Exception as exc:
        out.append({'probe': name, 'exception': type(exc).__name__, 'message': str(exc)})

c = load('audit_circuit', 'antigravity/models/circuit_rules.py')
s = load('audit_screen', 'antigravity/models/accumulation_screener.py')
r = load('audit_risk', 'antigravity/models/risk_calculator.py')
v = load('audit_volume', 'antigravity/models/volume_climax_detector.py')
b = load('audit_band', 'antigravity/models/band_revision_monitor.py')
f = load('audit_fill', 'claude/models/fill_model.py')

snap = c.MarketDepthSnapshot('SYNTHETIC', 12, 12, 0, 5, 10000, 10000, 1000000, 500000, 0)
probe('circuit_lower_lock', lambda: c.CircuitRuleEngine.evaluate_signal(replace(snap, price=11.4, change_pct=-5, total_bids=0)))
probe('circuit_locked_upper_entry', lambda: c.CircuitRuleEngine.evaluate_signal(replace(snap, price=12.6, change_pct=5, total_offers=0, consecutive_uc_days=1)))
probe('circuit_ESM1', lambda: c.CircuitRuleEngine.evaluate_signal(replace(snap, surveillance_stage='ESM_1')))
probe('circuit_ASM1', lambda: c.CircuitRuleEngine.evaluate_signal(replace(snap, surveillance_stage='ASM_1')))
probe('circuit_sub10', lambda: c.CircuitRuleEngine.evaluate_signal(replace(snap, price=1.32)))
probe('circuit_false_target_no_entry_price', lambda: c.CircuitRuleEngine.evaluate_signal(replace(snap, price=12.6, change_pct=5, consecutive_uc_days=4)))
probe('execution_enum_members', lambda: list(c.ExecutionState.__members__))

history = [s.DailyCandle(f'Day_{i}',10,10.5,9.8,10,500000,.30) for i in range(65)]
history += [s.DailyCandle(f'Day_{i}',10+(i-65)*.125,10+(i-65)*.125+.5,10+(i-65)*.125-.2,10+(i-65)*.125,2000000,.48) for i in range(65,85)]
def screen(hist=history, series='EQ'):
    return s.AccumulationScreener().evaluate_ticker(s.TickerData('SYNTHETIC',series,set(),hist))
probe('screener_control', screen)
probe('screener_20pct_spread', lambda: screen([replace(x,spread_pct=.20) for x in history]))
probe('screener_missing_spread_defaults_to_pass', lambda: history[-1].spread_pct)
probe('screener_nan_delivery', lambda: screen([replace(x,delivery_pct=float('nan')) for x in history]))
probe('screener_nan_volume', lambda: screen([replace(x,volume=float('nan')) for x in history]))
probe('screener_duplicate_dates', lambda: screen([replace(x,date='2026-09-09') for x in history]))
probe('screener_raw_BSE_group_A', lambda: screen(series='A'))
probe('screener_annotation_resolution', lambda: str(typing.get_type_hints(s.AccumulationScreener.evaluate_ticker)))

probe('risk_allocation_vs_2pct_loss_budget', lambda: r.CircuitRiskCalculator.calculate_position_size(100000,100,1000000))
probe('ten_day_loss_on_10000', lambda: 10000*(1-.95**10))
probe('risk_default_lock_days', lambda: r.CircuitRiskCalculator.simulate_lower_circuit_lock_trap(100,100))
probe('risk_rounding_fixture_1_26', lambda: r.CircuitRiskCalculator.simulate_circuit_run(1.26,1))
probe('risk_zero_price', lambda: r.CircuitRiskCalculator.calculate_position_size(100000,0,1000000))

probe('volume_target_zero_bids', lambda: v.VolumeClimaxDetector.evaluate_session_progression([
    v.DailySession(1,10,100000,False,False,10000,10000),
    v.DailySession(4,12,100000,False,False,0,10000)]))
probe('volume_LC_with_buyers', lambda: v.VolumeClimaxDetector.evaluate_session_progression([
    v.DailySession(1,10,100000,False,False,10000,10000),
    v.DailySession(2,9.5,100000,False,True,10000,10000)]))

class MemoryBand(b.BandRevisionMonitor):
    def __init__(self): self.history = {}
    def _save_history(self): pass

def positive_band():
    m=MemoryBand()
    a=m.check_ticker('TEST',100,120,80,'2026-09-08')
    z=m.check_ticker('TEST',100,110,90,'2026-09-09')
    return [a,z]
probe('band_true_narrowing_control',positive_band)
def duplicate_band():
    m=MemoryBand()
    m.check_ticker('TEST',100,120,80,'2026-09-08')
    first=m.check_ticker('TEST',100,110,90,'2026-09-09')
    second=m.check_ticker('TEST',100,110,90,'2026-09-09')
    return {'first':first,'same_day_repeat':second,'records':len(m.history['TEST'])}
probe('band_repeat_loses_alert',duplicate_band)
probe('band_missing_upper_price', lambda: MemoryBand().check_ticker('TEST',100,0,90,'2026-09-09'))
probe('band_asymmetric_lower_change_ignored', lambda: (lambda m: [m.check_ticker('TEST',100,105,95,'2026-09-08'),m.check_ticker('TEST',100,105,98,'2026-09-09')])(MemoryBand()))
def corrupt_band():
    with patch.object(b.os.path,'exists',return_value=True), patch('builtins.open',return_value=io.StringIO('{broken')):
        return b.BandRevisionMonitor('unused').history
probe('band_corrupt_history_silently_empty',corrupt_band)

def fake_no_selling_profit():
    """Sensitivity probe, explicitly overriding assumptions in memory only."""
    with patch.dict(f.P_FILL,{'LOCK_STRONG':1.,'LOCK_WEAK':1.,'BREAK':1.}), patch.dict(f.TRANSITION,{'LOCK_STRONG':[1.,0.,0.],'LOCK_WEAK':[1.,0.,0.],'BREAK':[1.,0.,0.]}):
        filled,net=f.simulate(n=1,hold_days=4)
        return {'filled':bool(filled[0]),'reported_net':float(net[0]),'exit_orders_or_bid_checks':'none in simulator'}
probe('fill_reports_profit_without_exit_event',fake_no_selling_profit)
probe('fill_probability_constants_unestimated', lambda: f.P_FILL)
probe('ccdl_offer_bid_ratio_actual_pct', lambda: 410174/90792858*100)

def log_audit():
    with (ROOT/'CHATGPT/observation_log.csv').open(encoding='utf-8-sig',newline='') as stream:
        rows=list(csv.reader(stream))
    return {'header_columns':len(rows[0]),'row_lengths':[len(x) for x in rows[1:]],'non_example_rows':sum(bool(x) and not x[0].startswith('EXAMPLE') for x in rows[1:]),'first_row_mapping':dict(zip(rows[0],rows[1])) if len(rows)>1 else {}}
probe('paper_log_integrity',log_audit)
after={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in SOURCES}
print(json.dumps({'python':sys.version,'core_hashes':before,'core_files_unchanged':before==after,'diagnostics':out},indent=2,default=str))
