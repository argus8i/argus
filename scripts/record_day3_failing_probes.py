"""
scripts/record_day3_failing_probes.py
======================================
Runs the failing regression probes for Round 5 blockers to establish the empirical
test-first acceptance artifact before remediations are merged.
Part of Project Swing Trades (ARGUS 8i Track 2 Liquid Desk).
"""

import hashlib
from pathlib import Path
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON_EXE = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
LOG_DIR = REPO_ROOT / "shared" / "trust" / "artifacts"
LOG_FILE = LOG_DIR / "DAY3-ROUND5-FAILING-PROBES.log"
SHA_FILE = LOG_DIR / "DAY3-ROUND5-FAILING-PROBES.log.sha256"


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(PYTHON_EXE),
        "-m",
        "pytest",
        "tests/test_day3_strategies.py",
        "-k",
        "test_overridden_str or test_datetime_timezone",
        "-v",
    ]
    print("Running command:", " ".join(cmd))

    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    combined = proc.stdout
    if proc.stderr:
        combined += "\n--- STDERR ---\n" + proc.stderr

    # Normalize strictly to LF
    normalized = combined.replace("\r\n", "\n").replace("\r", "\n")
    log_bytes = normalized.encode("utf-8")

    with open(LOG_FILE, "wb") as f:
        f.write(log_bytes)

    digest = hashlib.sha256(log_bytes).hexdigest().upper()
    sha_content = f"{digest}  {LOG_FILE.name}\n"
    with open(SHA_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write(sha_content)

    print(f"Exit code: {proc.returncode} (expected != 0 for failing test-first gate)")
    print(f"Recorded log: {LOG_FILE} ({len(log_bytes)} bytes)")
    print(f"SHA-256 seal: {digest}")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
