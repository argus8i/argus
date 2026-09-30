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

    # Outage detected!
    log_watchdog(f"OUTAGE DETECTED: Status={status_label}. Initiating automated recovery restart...")

    # 1. Clean up stale lock/pid files if process is dead
    if os.path.exists(SUPERVISOR_LOCK_FILE):
        lock_path = SUPERVISOR_LOCK_FILE + ".lock"
        if os.path.exists(lock_path):
            try:
                os.remove(lock_path)
                log_watchdog("Cleaned up stale supervisor lock file.")
            except OSError:
                pass
        try:
            os.remove(SUPERVISOR_LOCK_FILE)
        except OSError:
            pass

    if status_label in ("STALE_PID", "STOPPED", "ERROR"):
        if os.path.exists(SUPERVISOR_PID_FILE):
            try:
                os.remove(SUPERVISOR_PID_FILE)
                log_watchdog("Cleaned up stale supervisor PID file.")
            except OSError:
                pass

    # 2. Spawn supervisor detached from caller / console group
    supervisor_script = os.path.join(os.path.dirname(__file__), "supervised_inbox_worker.py")
    venv_python = os.path.join(WORKSPACE_DIR, ".venv", "Scripts", "python.exe")
    python_bin = venv_python if os.path.exists(venv_python) else sys.executable
    cmd = [python_bin, "-u", supervisor_script]

    creationflags = 0
    if os.name == "nt":
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | 0x08000000  # CREATE_NO_WINDOW
        )

    log_watchdog(f"Spawning detached supervisor process: {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=WORKSPACE_DIR,
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True
        )
        log_watchdog(f"Detached supervisor process spawned with initial PID={proc.pid}")
    except Exception as e:
        err_msg = f"FATAL: Failed to spawn supervisor process: {e}"
        log_watchdog(err_msg)
        return {"healthy": False, "action": "FAILED", "error": err_msg}

    # 3. Wait for supervisor to initialize and report RUNNING
    t0 = time.time()
    recovered = False
    new_status = {}
    while time.time() - t0 < 5.0:
        time.sleep(0.5)
        new_status = get_status()
        if new_status.get("running") and new_status.get("status") == "RUNNING":
            recovered = True
            break

    if recovered:
        sup_pid = new_status.get("details", {}).get("supervisor_pid")
        w_pid = new_status.get("details", {}).get("worker_pid")
        log_watchdog(f"RECOVERY SUCCESS: Supervisor active with PID={sup_pid}, Worker PID={w_pid}")
        return {"healthy": True, "action": "RECOVERED", "status": new_status}
    else:
        log_watchdog(f"RECOVERY WARNING: Supervisor not confirmed RUNNING within 5.0s (current: {new_status})")
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
