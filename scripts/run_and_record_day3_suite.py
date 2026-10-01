"""
scripts/run_and_record_day3_suite.py
====================================
Runs the complete Sprint Day 3 test suite across data contracts, execution/risk,
and quantitative alpha strategies, recording a normalized LF log and cryptographic SHA-256 seal.
Part of Project Swing Trades (ARGUS 8i Track 2 Liquid Desk).
"""

import hashlib
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON_EXE = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
LOG_DIR = REPO_ROOT / "shared" / "trust" / "artifacts"
LOG_FILE = LOG_DIR / "DAY3-ALPHA-STRATEGIES-TESTS.log"
SHA_FILE = LOG_DIR / "DAY3-ALPHA-STRATEGIES-TESTS.log.sha256"

TEST_FILES = [
    "tests/test_day1_data_contracts.py",
    "tests/test_execution_risk_governor.py",
    "tests/test_day3_strategies.py",
]


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [str(PYTHON_EXE), "-m", "pytest"] + TEST_FILES + ["-v"]
    print("Running command:", " ".join(cmd))

    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    raw_stdout = proc.stdout
    raw_stderr = proc.stderr
    combined = raw_stdout
    if raw_stderr:
        combined += "\n--- STDERR ---\n" + raw_stderr

    # Normalize to strictly LF
    normalized = combined.replace("\r\n", "\n").replace("\r", "\n")
    log_bytes = normalized.encode("utf-8")

    with open(LOG_FILE, "wb") as f:
        f.write(log_bytes)

    digest = hashlib.sha256(log_bytes).hexdigest().upper()
    sha_content = f"{digest}  {LOG_FILE.name}\n"
    with open(SHA_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write(sha_content)

    print(f"Exit code: {proc.returncode}")
    print(f"Log written: {LOG_FILE} ({len(log_bytes)} bytes)")
    print(f"SHA-256: {digest}")
    print(f"Seal written: {SHA_FILE}")

    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
