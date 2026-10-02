from pathlib import Path
import subprocess, sys, json, hashlib
ROOT = Path(r'C:\Users\yashw\swing trades')
OUT = ROOT/'shared/trust/artifacts'
PREFIX = 'CODEX-DAY5-PAPER-DESK-ABCE70A-R4'
python = ROOT/'.venv/Scripts/python.exe'
def run(label, args):
    result = subprocess.run(list(map(str,args)), cwd=ROOT, capture_output=True)
    for name, data in [('stdout',result.stdout),('stderr',result.stderr)]:
        (OUT/f'{PREFIX}-{label}.{name}.log').write_bytes(data)
    (OUT/f'{PREFIX}-{label}.json').write_text(json.dumps({'argv':list(map(str,args)), 'cwd':str(ROOT), 'exit_code':result.returncode},indent=2))
    print(label, result.returncode, result.stdout.decode(errors='replace')[-4500:])
run('identity',['git','rev-parse','HEAD'])
run('tracked-diff',['git','diff','--','antigravity/paper','tests','scripts/ingest_daily_regulatory_data.py','shared/docs'])
run('original-probes',[python,'-m','pytest','shared/trust/artifacts/test_codex_day5_48cb886_review.py','-v','--basetemp',OUT/f'{PREFIX}-original-tmp'])
files=['tests/test_day1_data_contracts.py','tests/test_execution_risk_governor.py','tests/test_day3_strategies.py','tests/test_day4_backtest.py']
files += [f'shared/trust/artifacts/test_codex_day4_{c}_review.py' for c in ['9157a86','ee58cb3','7c23f6c','90255e7','bf510da']]
files += ['tests/test_day5_paper_desk.py']
run('suite',[python,'-m','pytest',*files,'-v','--basetemp',OUT/f'{PREFIX}-suite-tmp'])
run('additional-probes',[python,'-m','pytest','shared/trust/artifacts/test_codex_day5_01d3fbc_round3.py','-v','--basetemp',OUT/f'{PREFIX}-additional-tmp'])
run('new-probes',[python,'-m','pytest','shared/trust/artifacts/test_codex_day5_eaa38ba_round2.py','-v','--basetemp',OUT/f'{PREFIX}-new-tmp'])
run('runbook',[python,'-m','pytest','tests/test_runbook_wiring.py','-v','--basetemp',OUT/f'{PREFIX}-runbook-tmp'])
run('submitted-log-hash',['git','show','--stat','--oneline','HEAD'])
hashes={str(x.relative_to(ROOT)):hashlib.sha256(x.read_bytes()).hexdigest() for x in OUT.glob(f'{PREFIX}*') if x.is_file()}
hashes['shared/trust/artifacts/DAY5-PAPER-DESK-TESTS.log']=hashlib.sha256((OUT/'DAY5-PAPER-DESK-TESTS.log').read_bytes()).hexdigest()
(OUT/f'{PREFIX}-hashes.json').write_text(json.dumps(hashes,indent=2))
