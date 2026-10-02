import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(r'C:\Users\yashw\swing trades')
ART = ROOT / 'shared/trust/artifacts'
for name, files in [
    ('suite', ['tests/test_day1_data_contracts.py','tests/test_execution_risk_governor.py','tests/test_day3_strategies.py','tests/test_day4_backtest.py','shared/trust/artifacts/test_codex_day4_9157a86_review.py']),
    ('probes', ['shared/trust/artifacts/test_codex_day4_ee58cb3_review.py']),
]:
    cmd = [str(ROOT / '.venv/Scripts/python.exe'), '-m', 'pytest', *files, '-v', '-p', 'no:cacheprovider', '--basetemp', str(ART / ('codex_ee58cb3_' + name + '_tmp'))]
    res = subprocess.run(cmd, cwd=ROOT, capture_output=True)
    path = ART / ('CODEX-DAY4-EE58CB3-' + name + '.log')
    path.write_bytes(('COMMAND_ARGV: '+json.dumps(cmd)+'\nEXIT_CODE: '+str(res.returncode)+'\nSTDOUT:\n').encode() + res.stdout + b'\nSTDERR:\n' + res.stderr)
    digest = hashlib.sha256(path.read_bytes()).hexdigest().upper()
    path.with_suffix('.log.sha256').write_text(digest+'  '+path.name+'\n')
    print(name, 'exit',res.returncode, digest)
    print(res.stdout.decode(errors='replace'))
print('submitted log SHA256', hashlib.sha256((ART / 'DAY4-BACKTEST-STRESS-TESTS.log').read_bytes()).hexdigest().upper())
