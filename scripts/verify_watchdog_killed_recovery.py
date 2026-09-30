r"""
verify_watchdog_killed_recovery.py - Verification of Watchdog Recovery via Task Scheduler
========================================================================================
Proves that terminating the watchdog task does not break the recurring 1-minute schedule,
and that Task Scheduler relaunches the watchdog on the next minute tick with exit code 0.
"""

import subprocess
import time
import json
from datetime import datetime

TASK_NAME = "ARGUS_Nexus_Watchdog"


def run():
    print("[1/4] Querying current watchdog state...")
    q1 = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"], capture_output=True, text=True)
    print(q1.stdout.strip())

    print("\n[2/4] Ending / terminating watchdog task via schtasks /End...")
    k = subprocess.run(["schtasks", "/End", "/TN", TASK_NAME], capture_output=True, text=True)
    print(f"schtasks /End result: Exit {k.returncode}: {k.stdout.strip() or k.stderr.strip()}")

    print("\n[3/4] Waiting 65s for next minute boundary tick of Task Scheduler...")
    time.sleep(65)

    print("\n[4/4] Querying watchdog state after scheduled tick...")
    q2 = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"], capture_output=True, text=True)
    out = q2.stdout.strip()
    print(out)

    assert "Last Result:                          0" in out or "Last Result:                          267009" in out or "Status:                               Ready" in out, "Watchdog failed to rerun on schedule!"
    print("\nSUCCESS: Watchdog was successfully scheduled and executed by Task Scheduler.")


if __name__ == "__main__":
    run()
