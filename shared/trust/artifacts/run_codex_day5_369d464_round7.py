from pathlib import Path
import subprocess,json,hashlib
ROOT=Path(r'C:\Users\yashw\swing trades'); OUT=ROOT/'shared/trust/artifacts'; PREFIX='CODEX-DAY5-PAPER-DESK-369D464-R7'
def run(label,args):
 args=list(map(str,args)); r=subprocess.run(args,cwd=ROOT,capture_output=True)
 for name,data in [('stdout',r.stdout),('stderr',r.stderr)]: (OUT/f'{PREFIX}-{label}.{name}.log').write_bytes(data)
 (OUT/f'{PREFIX}-{label}.json').write_text(json.dumps(dict(argv=args,cwd=str(ROOT),exit_code=r.returncode),indent=2))
 print(label,r.returncode,r.stdout.decode(errors='replace')[-4500:],flush=True)
run('identity',['git','rev-parse','HEAD'])
run('source-diff',['git','diff','HEAD','--','antigravity/paper','scripts','tests'])
files=['tests/test_day1_data_contracts.py','tests/test_execution_risk_governor.py','tests/test_day3_strategies.py','tests/test_day4_backtest.py']
files += [str(OUT/f'test_codex_day4_{c}_review.py') for c in ['9157a86','ee58cb3','7c23f6c','90255e7','bf510da']]
files += ['tests/test_day5_paper_desk.py','tests/test_runbook_wiring.py']
files += [str(OUT/f'test_codex_day5_{c}.py') for c in ['48cb886_review','eaa38ba_round2','01d3fbc_round3','abce70a_round4','cd0b2bf_round5','ac6f0be_round6']]
py=ROOT/'.venv/Scripts/python.exe'
run('combined',[py,'-m','pytest',*files,'-v','-p','no:cacheprovider','--basetemp',OUT/f'{PREFIX}-combined-tmp'])
run('new-probes',[py,'-m','pytest',OUT/'test_codex_day5_369d464_round7.py','-v','-p','no:cacheprovider','--basetemp',OUT/f'{PREFIX}-new-tmp'])
run('end-identity',['git','rev-parse','HEAD'])
run('end-source-diff',['git','diff','HEAD','--','antigravity/paper','scripts','tests'])
f=OUT/'DAY5-PAPER-DESK-TESTS.log'
(OUT/f'{PREFIX}-submitted-hash.json').write_text(json.dumps(dict(sha256=hashlib.sha256(f.read_bytes()).hexdigest(),bytes=f.stat().st_size)))
