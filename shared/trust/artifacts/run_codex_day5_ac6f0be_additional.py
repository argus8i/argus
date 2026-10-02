from pathlib import Path
import subprocess, json
ROOT=Path(r'C:\Users\yashw\swing trades')
OUT=ROOT/'shared/trust/artifacts'
P='CODEX-DAY5-PAPER-DESK-AC6F0BE-R6-additional'
args=[str(ROOT/'.venv/Scripts/python.exe'),'-m','pytest',str(OUT/'test_codex_day5_ac6f0be_round6.py'),'-v','-p','no:cacheprovider','--basetemp',str(OUT/(P+'-tmp'))]
r=subprocess.run(args,cwd=ROOT,capture_output=True)
(OUT/(P+'.stdout.log')).write_bytes(r.stdout)
(OUT/(P+'.stderr.log')).write_bytes(r.stderr)
(OUT/(P+'.json')).write_text(json.dumps(dict(argv=args,cwd=str(ROOT),exit_code=r.returncode),indent=2))
print(r.stdout.decode(errors='replace'))
print('pytest exit code:',r.returncode)
