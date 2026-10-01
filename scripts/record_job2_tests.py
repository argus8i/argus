import subprocess
import time
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
claude_dir = Path(r"C:\Users\yashw\swing-trades-claude-004")
py = Path(r"C:\Users\yashw\swing trades\.venv\Scripts\python.exe")
art_dir = Path(r"C:\Users\yashw\swing trades\shared\trust\artifacts")

test_files = [
    "research/tests/test_claude_t2_01_evidence_store_processes.py",
    "research/tests/test_codex_t2_4be7563_store.py",
    "research/tests/test_codex_t2_147c2a9_retention.py",
    "research/tests/test_codex_t2_1580779_pin_race.py",
    "research/tests/test_codex_t2_01_lock_recheck.py",
    "research/tests/test_codex_t2_01_recheck_probes.py",
    "research/tests/test_claude_t2_01_surveillance_session.py",
    "research/tests/test_claude_snapshot_rename_retry.py"
]

joined_files = " ".join(test_files)
cmd = [str(py), "-m", "pytest"] + test_files + ["-v", "-p", "no:cacheprovider", "--basetemp", str(art_dir / "claude_job2_targeted_tmp")]
cmd_str = f"python -m pytest {joined_files} -v -p no:cacheprovider"

start_time = datetime.now(IST).isoformat()
t0 = time.time()
res = subprocess.run(cmd, cwd=str(claude_dir), capture_output=True, text=True, encoding="utf-8", errors="replace")
elapsed = time.time() - t0
end_time = datetime.now(IST).isoformat()

log_content = (
    "================================================================================\n"
    f"COMMAND: {cmd_str}\n"
    f"CWD: {claude_dir}\n"
    f"START TIME: {start_time}\n"
    "================================================================================\n"
    + res.stdout + ("\nSTDERR:\n" + res.stderr if res.stderr else "") +
    "\n================================================================================\n"
    f"END TIME: {end_time}\n"
    f"ELAPSED SECONDS: {elapsed:.2f}\n"
    f"EXIT CODE: {res.returncode}\n"
    "================================================================================\n"
)

log_path = art_dir / "CODEX-T2-01-JOB2-995AE27_tests.log"
log_path.write_text(log_content, encoding="utf-8")
print(f"Done! Returncode: {res.returncode}, elapsed: {elapsed:.2f}s, written to {log_path}")
