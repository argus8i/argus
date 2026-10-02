from pathlib import Path
import subprocess, json, hashlib
ROOT = Path(r'C:\Users\yashw\swing trades')
OUT = ROOT/'shared/trust/artifacts'
PREFIX = 'CODEX-DAY5-PAPER-DESK-AC6F0BE-R6'
python = ROOT/'.venv/Scripts/python.exe'
def run(label, args):
    args = list(map(str,args))
    result = subprocess.run(args, cwd=ROOT, capture_output=True)
    for name, data in [('stdout',result.stdout),('stderr',result.stderr)]:
        (OUT/f'{PREFIX}-{label}.{name}.log').write_bytes(data)
    (OUT/f'{PREFIX}-{label}.json').write_text(json.dumps(dict(argv=args,cwd=str(ROOT),exit_code=result.returncode),indent=2))
    print(label,result.returncode,result.stdout.decode(errors='replace')[-2500:],flush=True)
run('identity',['git','rev-parse','HEAD'])
run('source-diff',['git','diff','HEAD','--','antigravity/paper','antigravity/engine','antigravity/strategies','scripts','tests'])
files=['tests/test_day1_data_contracts.py','tests/test_execution_risk_governor.py','tests/test_day3_strategies.py','tests/test_day4_backtest.py']
files += [f'shared/trust/artifacts/test_codex_day4_{c}_review.py' for c in ['9157a86','ee58cb3','7c23f6c','90255e7','bf510da']]
files += ['tests/test_day5_paper_desk.py','tests/test_runbook_wiring.py']
files += [f'shared/trust/artifacts/test_codex_day5_{c}.py' for c in ['48cb886_review','eaa38ba_round2','01d3fbc_round3','abce70a_round4']]
run('combined',[python,'-m','pytest',*files,'-v','-p','no:cacheprovider','--basetemp',OUT/f'{PREFIX}-combined-tmp'])
run('new-probes',[python,'-m','pytest',OUT/'test_codex_day5_cd0b2bf_round5.py','-v','-p','no:cacheprovider','--basetemp',OUT/f'{PREFIX}-new-tmp'])
run('end-identity',['git','rev-parse','HEAD'])
run('end-source-diff',['git','diff','HEAD','--','antigravity/paper','antigravity/engine','antigravity/strategies','scripts','tests'])
submitted=OUT/'DAY5-PAPER-DESK-TESTS.log'
(OUT/f'{PREFIX}-submitted-hash.json').write_text(json.dumps(dict(path=str(submitted),sha256=hashlib.sha256(submitted.read_bytes()).hexdigest()),indent=2))
paths=list(OUT.glob(PREFIX+'*'))+[Path(__file__),OUT/'test_codex_day5_cd0b2bf_round5.py']
(OUT/f'{PREFIX}-hashes.json').write_text(json.dumps({x.relative_to(ROOT).as_posix():hashlib.sha256(x.read_bytes()).hexdigest() for x in paths if x.is_file() and not x.name.endswith('-hashes.json')},indent=2))

