r"""
nexus_watchdog.py - 5-Minute Heartbeat Watchdog & Outage Recovery for Nexus Bus
=============================================================================
Enforces continuous availability of the Antigravity Nexus Supervised Inbox Worker:
  1. Checks daemon health via get_status().
  2. If status is NOT 'RUNNING' or worker is dead, initiates immediate detached restart.
  3. Cleans up stale PID and lock files before restart.
  4. Appends timestamped audit receipts to both supervisor.log and watchdog.log.
  5. Can run as a 5-minute scheduled check (--check) or background daemon (--daemon).
"""

import os
import sys
import time
import json
import signal
import subprocess
import argparse
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

# Workspace root
WORKSPACE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if WORKSPACE_DIR not in sys.path:
    sys.path.insert(0, WORKSPACE_DIR)

from antigravity.daemons.supervised_inbox_worker import (
    MESSAGES_ROOT,
    SUPERVISOR_PID_FILE,
    SUPERVISOR_LOCK_FILE,
    SUPERVISOR_LOG_FILE,
    get_status,
    get_current_ist,
    _pid_is_running,
)

WATCHDOG_LOG_FILE = os.path.join(MESSAGES_ROOT, "watchdog.log")


def _redirect_streams_for_windowless_execution():
    """Ensures stdout, stderr, and uncaught exceptions are written to watchdog.log under pythonw.exe."""
    class LogStream:
        def __init__(self, filepath, stream_name):
            self.filepath = filepath
            self.stream_name = stream_name
        def write(self, s):
            if not s:
                return
            try:
                os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
                with open(self.filepath, "a", encoding="utf-8") as f:
                    f.write(s)
            except Exception:
                pass
        def flush(self):
            pass

    if sys.stdout is None or not hasattr(sys.stdout, "write"):
        sys.stdout = LogStream(WATCHDOG_LOG_FILE, "STDOUT")
    if sys.stderr is None or not hasattr(sys.stderr, "write"):
        sys.stderr = LogStream(WATCHDOG_LOG_FILE, "STDERR")

    def _unhandled_exception_hook(exc_type, exc_val, exc_tb):
        import traceback
        tb_lines = "".join(traceback.format_exception(exc_type, exc_val, exc_tb))
        log_watchdog(f"CRITICAL UNHANDLED EXCEPTION:\n{tb_lines}")

    sys.excepthook = _unhandled_exception_hook


_redirect_streams_for_windowless_execution()


def log_watchdog(message: str):
    """Logs timestamped entry to stdout, supervisor.log, and watchdog.log."""
    ist_time = get_current_ist()
    formatted = f"[{ist_time}] [WATCHDOG] {message}"
    print(formatted, flush=True)
    for log_path in [SUPERVISOR_LOG_FILE, WATCHDOG_LOG_FILE]:
        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(formatted + "\n")
        except Exception:
            pass


TASK_NAME = "ARGUS_Nexus_Supervisor"


def start_supervisor_task() -> bool:
    """Starts the supervisor process exclusively through Windows Task Scheduler.
    This guarantees the supervisor runs detached in its own session and never belongs
    to any caller's process tree."""
    log_watchdog(f"Triggering supervisor via scheduled task: schtasks /Run /TN {TASK_NAME}")
    try:
        res = subprocess.run(
            ["schtasks", "/Run", "/TN", TASK_NAME],
            capture_output=True,
            text=True,
            check=True,
        )
        log_watchdog(f"schtasks output: {res.stdout.strip()}")
        return True
    except subprocess.CalledProcessError as e:
        err = e.stderr.strip() if e.stderr else str(e)
        log_watchdog(f"ERROR: schtasks /Run failed: {err}")
        return False
    except Exception as e:
        log_watchdog(f"ERROR triggering scheduled task: {e}")
        return False


def check_and_recover(verbose: bool = True) -> Dict[str, Any]:
    """Inspects daemon liveness and performs automated detached recovery if down."""
    st = get_status()
    is_running = st.get("running", False)
    status_label = st.get("status", "UNKNOWN")

    if is_running and status_label == "RUNNING":
        if verbose:
            sup_pid = st.get("details", {}).get("supervisor_pid")
            w_pid = st.get("details", {}).get("worker_pid")
            log_watchdog(f"Health check OK: Status=RUNNING (Supervisor PID={sup_pid}, Worker PID={w_pid})")
        return {"healthy": True, "action": "NOOP", "status": st}

    # If worker is down, supervisor is alive and actively restarting it with backoff
    if status_label == "WORKER_DOWN":
        sup_pid = st.get("details", {}).get("supervisor_pid")
        if verbose:
            log_watchdog(f"Health check: Status=WORKER_DOWN (Supervisor PID={sup_pid} is alive and actively recovering child worker). Waiting...")
        return {"healthy": True, "action": "WAIT_WORKER_RESTART", "status": st}

    # If supervisor is hung (event loop deadlocked for >120s), force-terminate to allow recovery
    if status_label == "SUPERVISOR_HUNG":
        sup_pid = st.get("details", {}).get("supervisor_pid")
        log_watchdog(f"HUNG SUPERVISOR DETECTED: Supervisor PID={sup_pid} event loop frozen (>120s). Force-terminating hung process...")
        if sup_pid:
            try:
                os.kill(sup_pid, signal.SIGTERM)
            except Exception:
                pass

    sup_pid = st.get("details", {}).get("supervisor_pid")
    sup_ct = st.get("details", {}).get("supervisor_create_time_nt")
    if status_label != "SUPERVISOR_HUNG" and sup_pid and _pid_is_running(sup_pid, expected_create_time=sup_ct):
        if verbose:
            log_watchdog(f"Health check: Status={status_label}, but Supervisor PID={sup_pid} is STILL ALIVE. Waiting without deleting lock files or spawning duplicate.")
        return {"healthy": True, "action": "SUPERVISOR_ALIVE_WAIT", "status": st}

    # Outage confirmed: supervisor is truly dead.
    log_watchdog(f"OUTAGE DETECTED: Status={status_label}. Initiating automated recovery restart via Task Scheduler...")

    # 1. Clean up stale lock/pid files ONLY because supervisor is confirmed DEAD
    if os.path.exists(SUPERVISOR_LOCK_FILE):
        lock_path = SUPERVISOR_LOCK_FILE + ".lock"
        if os.path.exists(lock_path):
            try:
                os.remove(lock_path)
                log_watchdog("Cleaned up stale supervisor lock file (.lock).")
            except OSError:
                pass
        try:
            os.remove(SUPERVISOR_LOCK_FILE)
            log_watchdog("Cleaned up stale supervisor lock file.")
        except OSError:
            pass

    if os.path.exists(SUPERVISOR_PID_FILE):
        try:
            os.remove(SUPERVISOR_PID_FILE)
            log_watchdog("Cleaned up stale supervisor PID file.")
        except OSError:
            pass

    # Clean up orphaned child worker from the dead supervisor if still running
    old_worker_pid = st.get("details", {}).get("worker_pid")
    old_worker_ct = st.get("details", {}).get("worker_create_time_nt")
    if old_worker_pid and _pid_is_running(old_worker_pid, expected_create_time=old_worker_ct):
        try:
            os.kill(old_worker_pid, signal.SIGTERM)
            log_watchdog(f"Cleaned up orphaned worker PID={old_worker_pid}.")
        except Exception:
            pass

    # 2. Trigger supervisor exclusively via Windows Task Scheduler
    success = start_supervisor_task()
    if not success:
        return {"healthy": False, "action": "FAILED", "error": "schtasks failed to launch supervisor"}

    # 3. Wait for supervisor to initialize and report RUNNING or WORKER_DOWN
    t0 = time.time()
    recovered = False
    new_status = {}
    while time.time() - t0 < 8.0:
        time.sleep(0.5)
        new_status = get_status()
        if new_status.get("running") and new_status.get("status") in ("RUNNING", "WORKER_DOWN"):
            recovered = True
            break

    if recovered:
        sup_pid = new_status.get("details", {}).get("supervisor_pid")
        w_pid = new_status.get("details", {}).get("worker_pid")
        log_watchdog(f"RECOVERY SUCCESS: Supervisor active with PID={sup_pid}, Worker PID={w_pid}")
        return {"healthy": True, "action": "RECOVERED", "status": new_status}
    else:
        log_watchdog(f"RECOVERY WARNING: Supervisor not confirmed RUNNING within 8.0s (current: {new_status})")
        return {"healthy": False, "action": "TIMEOUT", "status": new_status}


def run_daemon(interval_sec: float = 300.0):
    """Runs continuous watchdog loop with specified polling interval (default: 5 min)."""
    log_watchdog(f"Nexus Watchdog Daemon started. Monitoring interval: {interval_sec:.1f}s")
    while True:
        try:
            check_and_recover(verbose=False)
        except Exception as e:
            log_watchdog(f"Error in watchdog loop: {e}")
        time.sleep(interval_sec)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Antigravity Nexus Watchdog & Recovery Daemon")
    parser.add_argument("--check", action="store_true", help="Perform single health check and recover if down")
    parser.add_argument("--daemon", action="store_true", help="Run as continuous monitoring daemon")
    parser.add_argument("--interval", type=float, default=300.0, help="Monitoring interval in seconds (default: 300)")
    parser.add_argument("--status", action="store_true", help="Display current health status")
    args = parser.parse_args()

    if args.status:
        st = get_status()
        print(json.dumps(st, indent=2))
        sys.exit(0 if st.get("running") else 1)
    elif args.daemon:
        run_daemon(interval_sec=args.interval)
    else:
        # Default or --check runs single pass
        res = check_and_recover(verbose=True)
        sys.exit(0 if res.get("healthy") else 1)
