r"""
supervised_inbox_worker.py - Supervised Daemon Process for Antigravity Inbox Worker
==================================================================================
Provides continuous process supervision, crash recovery, and health monitoring
for Antigravity's durable bidirectional messaging daemon.

Features:
  1. Mutual exclusion: strictly one supervisor and worker active at a time via FileLock.
  2. Health tracking: records supervisor and worker PIDs and heartbeat timestamps.
  3. Automatic crash recovery: restarts worker with exponential backoff on crash.
  4. Orphan recovery: automatically runs recover_orphaned_claims() after crashes.
  5. Clean signal handling: handles SIGINT / SIGTERM gracefully, terminating worker child.
  6. CLI actions: run, --status, --stop.
"""

import os
import sys
import time
import json
import signal
import subprocess
import argparse
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

# Ensure workspace root is in sys.path
WORKSPACE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if WORKSPACE_DIR not in sys.path:
    sys.path.insert(0, WORKSPACE_DIR)

from antigravity.daemons.inbox_worker import (
    MESSAGES_ROOT,
    FileLock,
    InboxWorker,
    get_current_ist,
    ensure_directories,
)

SUPERVISOR_LOCK_FILE = os.path.join(MESSAGES_ROOT, "supervisor.lock")
SUPERVISOR_PID_FILE = os.path.join(MESSAGES_ROOT, "supervisor.pid")
SUPERVISOR_LOG_FILE = os.path.join(MESSAGES_ROOT, "supervisor.log")

MIN_BACKOFF_SEC = 1.0
MAX_BACKOFF_SEC = 30.0
BACKOFF_FACTOR = 2.0


def log_supervisor(message: str):
    """Appends a timestamped log entry to supervisor.log and stdout."""
    ist_time = get_current_ist()
    formatted = f"[{ist_time}] [SUPERVISOR] {message}"
    print(formatted, flush=True)
    try:
        os.makedirs(os.path.dirname(SUPERVISOR_LOG_FILE), exist_ok=True)
        with open(SUPERVISOR_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(formatted + "\n")
    except Exception:
        pass


class SupervisedInboxWorker:
    def __init__(self):
        ensure_directories()
        self.lock = FileLock(SUPERVISOR_LOCK_FILE, timeout_sec=2.0)
        self.worker_proc: Optional[subprocess.Popen] = None
        self.shutdown_requested = False
        self.backoff_sec = MIN_BACKOFF_SEC

    def _setup_signal_handlers(self):
        def handle_signal(signum, frame):
            log_supervisor(f"Received termination signal ({signum}). Initiating graceful shutdown...")
            self.shutdown_requested = True
            self._terminate_child()

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, handle_signal)

    def _terminate_child(self):
        if self.worker_proc and self.worker_proc.poll() is None:
            log_supervisor(f"Terminating worker process (PID={self.worker_proc.pid})...")
            try:
                self.worker_proc.terminate()
                self.worker_proc.wait(timeout=5.0)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    self.worker_proc.kill()
                except OSError:
                    pass

    def _write_pid_file(self):
        data = {
            "supervisor_pid": os.getpid(),
            "worker_pid": self.worker_proc.pid if self.worker_proc else None,
            "started_at_ist": get_current_ist(),
            "status": "RUNNING"
        }
        with open(SUPERVISOR_PID_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _remove_pid_file(self):
        try:
            if os.path.exists(SUPERVISOR_PID_FILE):
                os.remove(SUPERVISOR_PID_FILE)
        except OSError:
            pass

    def run(self):
        """Main supervision lifecycle loop."""
        log_supervisor("Starting Antigravity Supervised Worker Daemon...")

        if not self.lock.acquire():
            log_supervisor("FATAL: Another supervisor instance is already running. Exiting.")
            sys.exit(1)

        self._setup_signal_handlers()

        try:
            while not self.shutdown_requested:
                worker_script = os.path.join(os.path.dirname(__file__), "inbox_worker.py")
                cmd = [sys.executable, "-u", worker_script]

                log_supervisor(f"Spawning inbox worker: {' '.join(cmd)}")
                try:
                    self.worker_proc = subprocess.Popen(
                        cmd,
                        cwd=WORKSPACE_DIR,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace"
                    )
                except Exception as e:
                    log_supervisor(f"ERROR spawning worker process: {e}")
                    time.sleep(self.backoff_sec)
                    continue

                self._write_pid_file()
                log_supervisor(f"Worker spawned successfully with PID={self.worker_proc.pid}")

                # Stream worker output while process is alive
                crash_output = []
                while self.worker_proc.poll() is None:
                    line = self.worker_proc.stdout.readline()
                    if line:
                        line_str = line.strip()
                        if line_str:
                            crash_output.append(line_str)
                            if len(crash_output) > 50:
                                crash_output.pop(0)
                            print(f"  [WORKER-{self.worker_proc.pid}] {line_str}", flush=True)
                    else:
                        time.sleep(0.1)

                returncode = self.worker_proc.returncode
                log_supervisor(f"Worker process (PID={self.worker_proc.pid}) exited with code {returncode}")

                if self.shutdown_requested:
                    break

                # Unexpected exit / crash recovery
                log_supervisor("CRASH/EXIT DETECTED: Triggering automatic recovery...")

                # 1. Recover any orphaned claims
                try:
                    recovery_worker = InboxWorker()
                    recovered = recovery_worker.recover_orphaned_claims()
                    log_supervisor("Orphan recovery pass completed.")
                except Exception as rec_err:
                    log_supervisor(f"Error during orphan recovery: {rec_err}")

                # 2. Backoff before restarting
                log_supervisor(f"Applying backoff delay of {self.backoff_sec:.1f}s before restart...")
                time.sleep(self.backoff_sec)
                self.backoff_sec = min(self.backoff_sec * BACKOFF_FACTOR, MAX_BACKOFF_SEC)

        finally:
            self._terminate_child()
            self._remove_pid_file()
            self.lock.release()
            log_supervisor("Supervisor shut down cleanly.")


def get_status() -> Dict[str, Any]:
    """Inspects PID and lock files to determine daemon status."""
    if not os.path.exists(SUPERVISOR_PID_FILE):
        return {"status": "STOPPED", "running": False}

    try:
        with open(SUPERVISOR_PID_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        sup_pid = data.get("supervisor_pid")

        # Check if supervisor PID is alive on Windows
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, sup_pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            kernel32.CloseHandle(handle)
            return {"status": "RUNNING", "running": True, "details": data}
        else:
            return {"status": "STALE_PID", "running": False, "details": data}
    except Exception as e:
        return {"status": "ERROR", "running": False, "error": str(e)}


def stop_daemon():
    """Signals the running supervisor to terminate cleanly."""
    status = get_status()
    if not status.get("running"):
        print("[SUPERVISOR] Daemon is not running.")
        return

    sup_pid = status["details"]["supervisor_pid"]
    print(f"[SUPERVISOR] Sending termination signal to supervisor PID={sup_pid}...")
    try:
        os.kill(sup_pid, signal.SIGTERM)
        print("[SUPERVISOR] Termination signal sent.")
    except Exception as e:
        print(f"[SUPERVISOR] Failed to signal process: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Antigravity Supervised Inbox Worker")
    parser.add_argument("--status", action="store_true", help="Check status of the daemon")
    parser.add_argument("--stop", action="store_true", help="Stop the running daemon")
    args = parser.parse_args()

    if args.status:
        st = get_status()
        print(json.dumps(st, indent=2))
    elif args.stop:
        stop_daemon()
    else:
        supervisor = SupervisedInboxWorker()
        supervisor.run()
