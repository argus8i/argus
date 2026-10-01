"""
Executes the full Nexus test suite and writes the complete reproduction artifact
including exact command, working directory, start/end timestamps, stdout/stderr,
and exit code per Rule 8 v2 Invariant 3.
"""
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))

ROOT_DIR = Path(__file__).resolve().parent.parent
PYTEST_PATH = ROOT_DIR / ".venv" / "Scripts" / "python.exe"

TEST_FILES = [
    "tests/test_codex_nexus_followup_probes.py",
    "tests/test_codex_nexus_full_2026_09_30.py",
    "tests/test_claude_nexus_review_probes_2026_09_30.py",
    "tests/test_codex_nexus_service_review_2026_09_30.py",
    "tests/test_nexus_resilience.py",
    "tests/test_tri_agent_messaging.py",
    "tests/test_reviewer_dispatch_validation.py"
]

CMD = [str(PYTEST_PATH), "-m", "pytest"] + TEST_FILES + ["-v"]
CMD_STR = f".venv\\Scripts\\python.exe -m pytest {' '.join(TEST_FILES)} -v"

LOG_PATH = ROOT_DIR / "shared" / "trust" / "artifacts" / "CODEX-NEXUS-FULL-SUITE-VERIFIED.log"

def main():
    start_time = datetime.now(IST).isoformat()
    t0 = time.time()
    
    print(f"Running command: {CMD_STR}")
    proc = subprocess.run(
        CMD,
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )
    elapsed = time.time() - t0
    end_time = datetime.now(IST).isoformat()
    
    combined_output = proc.stdout + ("\nSTDERR:\n" + proc.stderr if proc.stderr else "")
    
    header = (
        "================================================================================\n"
        f"COMMAND: {CMD_STR}\n"
        f"CWD: {ROOT_DIR}\n"
        f"START TIME: {start_time}\n"
        "================================================================================\n"
    )
    footer = (
        "\n================================================================================\n"
        f"END TIME: {end_time}\n"
        f"ELAPSED SECONDS: {elapsed:.2f}\n"
        f"EXIT CODE: {proc.returncode}\n"
        "================================================================================\n"
    )
    
    full_content = header + combined_output + footer
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        f.write(full_content)
        
    print(f"Test suite finished with exit code {proc.returncode} in {elapsed:.2f}s.")
    print(f"Complete artifact written to {LOG_PATH}")
    sys.exit(proc.returncode)

if __name__ == "__main__":
    main()
