"""
scripts/run_and_record_day5_suite.py
====================================
Executes the comprehensive test suite for Sprint Day 5, records full unedited
stdout/stderr to shared/trust/artifacts/DAY5-PAPER-DESK-TESTS.log,
computes its SHA-256 seal, and verifies zero failures across all tests.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

ARTIFACTS_DIR = ROOT_DIR / "shared" / "trust" / "artifacts"
LOG_FILE = ARTIFACTS_DIR / "DAY5-PAPER-DESK-TESTS.log"
SHA_FILE = ARTIFACTS_DIR / "DAY5-PAPER-DESK-TESTS.log.sha256"


def compute_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().upper()


def main():
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    python_exe = str(ROOT_DIR / ".venv" / "Scripts" / "python.exe")
    if not Path(python_exe).exists():
        python_exe = sys.executable

    test_files = [
        "tests/test_day1_data_contracts.py",
        "tests/test_execution_risk_governor.py",
        "tests/test_day3_strategies.py",
        "tests/test_day4_backtest.py",
        "shared/trust/artifacts/test_codex_day4_9157a86_review.py",
        "shared/trust/artifacts/test_codex_day4_ee58cb3_review.py",
        "shared/trust/artifacts/test_codex_day4_7c23f6c_review.py",
        "shared/trust/artifacts/test_codex_day4_90255e7_review.py",
        "shared/trust/artifacts/test_codex_day4_bf510da_review.py",
        "tests/test_day5_paper_desk.py",
    ]

    cmd = [python_exe, "-m", "pytest", *test_files, "-v"]
    print(f"[{time.strftime('%X')}] Executing test suite: {' '.join(cmd)}")

    t0 = time.time()
    res = subprocess.run(cmd, cwd=ROOT_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace")
    elapsed = time.time() - t0

    print(f"[{time.strftime('%X')}] Test execution finished with exit code {res.returncode} in {elapsed:.2f}s")

    # Write log file
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write(f"COMMAND: {' '.join(cmd)}\n")
        f.write(f"EXIT_CODE: {res.returncode}\n")
        f.write(f"ELAPSED_SECONDS: {elapsed:.2f}\n")
        f.write("=" * 80 + "\n")
        f.write("STDOUT:\n")
        f.write(res.stdout)
        f.write("=" * 80 + "\n")
        f.write("STDERR:\n")
        f.write(res.stderr)

    log_sha = compute_sha256(LOG_FILE)
    with open(SHA_FILE, "w", encoding="utf-8") as f:
        f.write(f"{log_sha}  {LOG_FILE.name}\n")

    print(f"[{time.strftime('%X')}] Recorded log: {LOG_FILE} ({LOG_FILE.stat().st_size} bytes)")
    print(f"[{time.strftime('%X')}] Computed SHA-256: {log_sha}")

    if res.returncode != 0:
        print(f"ERROR: Tests failed with exit code {res.returncode}")
        sys.exit(res.returncode)

    print("All Day 1–5 tests passed cleanly!")


if __name__ == "__main__":
    main()
