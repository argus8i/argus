from pathlib import Path
import sys, json, hashlib
ROOT = Path(r'C:\Users\yashw\swing trades')
sys.path.insert(0,str(ROOT))
from antigravity.daemons.tri_agent_bus import send_to_agent
PREFIX = 'CODEX-DAY5-PAPER-DESK-369D464-R7'
OUT = ROOT/'shared/trust/artifacts'
report = ROOT/'shared/trust/codex_day5_369d464_round7_review.md'
results=[]
for recipient in ['ANTIGRAVITY','CLAUDE']:
    try:
        mid,cid=send_to_agent(sender='CODEX',recipient=recipient,subject=PREFIX+' CHANGES_REQUIRED independent review',body='Formal Round 7 review evidence. Review-only dispatch; no recursive dispatch. Antigravity retains remediation ownership. Please reconcile the reproduced blockers and record dissent before promotion.\n\n'+report.read_text(encoding='utf-8'),track='TRACK_2',source_file=str(report),timeout_sec=20)
        results.append(dict(recipient=recipient,message_id=mid,correlation_id=cid,status='ENQUEUED'))
    except Exception as e:
        results.append(dict(recipient=recipient,status='FAILED',error_type=type(e).__name__))
(OUT/f'{PREFIX}-dispatch.json').write_text(json.dumps(results,indent=2))
paths=list(OUT.glob(PREFIX+'*'))+[report,Path(__file__),OUT/'test_codex_day5_369d464_round7.py',OUT/'run_codex_day5_369d464_round7.py']
(OUT/f'{PREFIX}-hashes.json').write_text(json.dumps({x.relative_to(ROOT).as_posix():hashlib.sha256(x.read_bytes()).hexdigest() for x in paths if x.is_file() and not x.name.endswith('-hashes.json')},indent=2))
print(json.dumps(results,indent=2))


