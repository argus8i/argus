"""Record exact argv, cwd, exit code, and unedited output bytes for Day 4 review."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(r'C:\Users\yashw\swing trades')
ART = ROOT / 'shared/trust/artifacts'
PYTHON = str(ROOT / '.venv/Scripts/python.exe')
suite = ['tests/test_day1_data_contracts.py', 'tests/test_execution_risk_governor.py',
         'tests/test_day3_strategies.py', 'tests/test_day4_backtest.py',
         'shared/trust/artifacts/test_codex_day4_9157a86_review.py',
         'shared/trust/artifacts/test_codex_day4_ee58cb3_review.py']
retry = '--retry' in sys.argv
runs = [] if '--inspect' in sys.argv else ([('suite-retry', suite)] if retry else [('suite', suite), ('probes', ['shared/trust/artifacts/test_codex_day4_7c23f6c_review.py'])])
for name, paths in runs:
    argv = [PYTHON, '-m', 'pytest', *paths, '-v', '-p', 'no:cacheprovider']
    if retry:
        argv += ['--basetemp', str(ART / ('codex_7c23f6c_tmp_' + uuid.uuid4().hex))]
    result = subprocess.run(argv, cwd=ROOT, capture_output=True)
    log = ART / f'CODEX-DAY4-7C23F6C-{name}.log'
    header = (f'ARGV: {json.dumps(argv)}\nCWD: {ROOT}\nEXIT_CODE: {result.returncode}\nSTDOUT:\n').encode()
    log.write_bytes(header + result.stdout + b'\nSTDERR:\n' + result.stderr)
    digest = hashlib.sha256(log.read_bytes()).hexdigest().upper()
    log.with_suffix(log.suffix + '.sha256').write_text(f'{digest}  {log.name}\n', encoding='utf-8')
    print(f'{name}: exit={result.returncode}; sha256={digest}')
    print(result.stdout.decode('utf-8', errors='replace'))
    print(result.stderr.decode('utf-8', errors='replace'))
if '--inspect' in sys.argv:
    code = """import pandas as pd
from scripts.run_walk_forward_simulation import run_adversarial_stress_scenarios
print(run_adversarial_stress_scenarios())
t=pd.read_csv('shared/track2_liquid/backtests/trades.csv')
e=pd.read_csv('shared/track2_liquid/backtests/daily_equity.csv')
print('trade_status_counts', t.status.value_counts().to_dict())
print('closed_net_pnl', t.loc[t.status=='CLOSED','net_pnl'].sum())
print('minimum_cash', e.cash.min())
for k,g in e.groupby('fold_id'):
    values=[250000.]+g.equity.tolist()
    peak=250000.; dd=0.
    for v in values:
        peak=max(peak,v); dd=max(dd,peak-v)
    print('independent_fold_drawdown',k,dd)
"""
    argv = [PYTHON, '-c', code]
    result = subprocess.run(argv, cwd=ROOT, capture_output=True)
    log = ART / 'CODEX-DAY4-7C23F6C-inspection.log'
    log.write_bytes((f'ARGV: {json.dumps(argv)}\nCWD: {ROOT}\nEXIT_CODE: {result.returncode}\nSTDOUT:\n').encode() + result.stdout + b'\nSTDERR:\n' + result.stderr)
    digest = hashlib.sha256(log.read_bytes()).hexdigest().upper()
    log.with_suffix(log.suffix + '.sha256').write_text(f'{digest}  {log.name}\n', encoding='utf-8')
    print(f'inspection: exit={result.returncode}; sha256={digest}')
    print(result.stdout.decode('utf-8', errors='replace'))
