"""Capture raw reproduction bytes without overwriting submitted artifacts."""
import hashlib
import json
import subprocess
import uuid
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
ROOT = Path(r'C:\Users\yashw\swing trades')
ART = ROOT / 'shared/trust/artifacts'
suite = ['tests/test_day1_data_contracts.py', 'tests/test_execution_risk_governor.py',
    'tests/test_day3_strategies.py', 'tests/test_day4_backtest.py',
    'shared/trust/artifacts/test_codex_day4_9157a86_review.py',
    'shared/trust/artifacts/test_codex_day4_ee58cb3_review.py',
    'shared/trust/artifacts/test_codex_day4_7c23f6c_review.py']
runs = [] if '--inspect' in sys.argv else [('suite', suite), ('probes', ['shared/trust/artifacts/test_codex_day4_90255e7_review.py'])]
for name, paths in runs:
    argv = [str(ROOT / '.venv/Scripts/python.exe'), '-m', 'pytest', *paths, '-v',
        '-p', 'no:cacheprovider', '--basetemp', str(ART / ('codex_90255e7_' + uuid.uuid4().hex))]
    res = subprocess.run(argv, cwd=ROOT, capture_output=True)
    log = ART / f'CODEX-DAY4-90255E7-{name}.log'
    log.write_bytes((f'ARGV: {json.dumps(argv)}\nCWD: {ROOT}\nEXIT_CODE: {res.returncode}\nSTDOUT:\n').encode() + res.stdout + b'\nSTDERR:\n' + res.stderr)
    digest = hashlib.sha256(log.read_bytes()).hexdigest().upper()
    log.with_suffix('.log.sha256').write_text(f'{digest}  {log.name}\n', encoding='utf-8')
    print(f'{name}: exit={res.returncode}; sha256={digest}')
    print(res.stdout.decode('utf-8', errors='replace'))
    print(res.stderr.decode('utf-8', errors='replace'))
if '--inspect' in sys.argv:
    code = """import pandas as pd, hashlib, subprocess
from pathlib import Path
from scripts.run_walk_forward_simulation import run_adversarial_stress_scenarios
print('HEAD',subprocess.check_output(['git','rev-parse','HEAD'],cwd=Path.cwd()).decode().strip())
print('stress',run_adversarial_stress_scenarios())
t=pd.read_csv('shared/track2_liquid/backtests/trades.csv')
e=pd.read_csv('shared/track2_liquid/backtests/daily_equity.csv')
print('status_counts',t.status.value_counts().to_dict())
print('closed_share_counts',t.loc[t.status=='CLOSED','shares'].value_counts().to_dict())
print('columns',t.columns.tolist())
print('closed_net_pnl',t.loc[t.status=='CLOSED','net_pnl'].sum())
print('minimum_cash',e.cash.min())
p=Path('shared/trust/artifacts/DAY4-BACKTEST-STRESS-TESTS.log')
print('submitted_bytes',p.stat().st_size,'SHA256',hashlib.sha256(p.read_bytes()).hexdigest().upper())
print('submitted_sidecar',p.with_suffix('.log.sha256').read_text())
"""
    argv = [str(ROOT / '.venv/Scripts/python.exe'), '-c', code]
    res = subprocess.run(argv, cwd=ROOT, capture_output=True)
    log = ART / 'CODEX-DAY4-90255E7-inspection.log'
    log.write_bytes((f'ARGV: {json.dumps(argv)}\nCWD: {ROOT}\nEXIT_CODE: {res.returncode}\nSTDOUT:\n').encode() + res.stdout + b'\nSTDERR:\n' + res.stderr)
    digest = hashlib.sha256(log.read_bytes()).hexdigest().upper()
    log.with_suffix('.log.sha256').write_text(f'{digest}  {log.name}\n', encoding='utf-8')
    print(f'inspection: exit={res.returncode}; sha256={digest}')
    print(res.stdout.decode('utf-8', errors='replace'))
