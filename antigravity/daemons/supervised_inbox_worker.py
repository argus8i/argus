r"""
supervised_inbox_worker.py - Supervised Daemon Process for Antigravity Inbox Worker
==================================================================================
Provides continuous process supervision, crash recovery, and health monitoring
for Antigravity's durable bidirectional messaging daemon.

CRITICAL INCEPTION & LIFECYCLE INVARIANT:
Nobody starts the supervisor by Popen or by hand from an interactive agent session.
The watchdog and every agent start it ONLY via `schtasks /Run /TN ARGUS_Nexus_Supervisor`,
so it never belongs to anyone's process tree and survives console/terminal closure.

Features:
  1. Mutual exclusion: strictly one supervisor and worker active at a time via FileLock.
  2. Health tracking: records supervisor and worker PIDs and heartbeat timestamps.
  3. Automatic crash recovery: restarts worker with exponential backoff on crash.
  4. Orphan recovery: automatically runs recover_orphaned_claims() after crashes.
  5. Clean signal handling: handles SIGINT / SIGTERM gracefully, terminating worker child.
  6. CLI actions: --status, --start (via Task Scheduler), --stop.
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
    _pid_is_running,
    get_process_create_time_nt,
)

SUPERVISOR_LOCK_FILE = os.path.join(MESSAGES_ROOT, "supervisor.lock")
SUPERVISOR_PID_FILE = os.path.join(MESSAGES_ROOT, "supervisor.pid")
SUPERVISOR_LOG_FILE = os.path.join(MESSAGES_ROOT, "supervisor.log")
SUPERVISOR_STOP_FILE = os.path.join(MESSAGES_ROOT, "supervisor.stop")

MIN_BACKOFF_SEC = 1.0
MAX_BACKOFF_SEC = 30.0
BACKOFF_FACTOR = 2.0
MAX_CONSECUTIVE_CRASHES = 5
CRASH_WINDOW_SEC = 120.0
MAX_HEARTBEAT_STALE_SEC = 120.0


def _redirect_streams_for_windowless_execution():
    """Ensures stdout, stderr, and uncaught exceptions are written to supervisor.log under pythonw.exe."""
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
        sys.stdout = LogStream(SUPERVISOR_LOG_FILE, "STDOUT")
    if sys.stderr is None or not hasattr(sys.stderr, "write"):
        sys.stderr = LogStream(SUPERVISOR_LOG_FILE, "STDERR")

    def _unhandled_exception_hook(exc_type, exc_val, exc_tb):
        import traceback
        tb_lines = "".join(traceback.format_exception(exc_type, exc_val, exc_tb))
        log_supervisor(f"CRITICAL UNHANDLED EXCEPTION:\n{tb_lines}")

    sys.excepthook = _unhandled_exception_hook


_redirect_streams_for_windowless_execution()


def log_supervisor(message: str):
    """Appends a timestamped log entry to supervisor.log and stdout."""
    ist_time = get_current_ist()
    formatted = f"[{ist_time}] [SUPERVISOR] {message}"
    try:
        print(formatted, flush=True)
    except (OSError, ValueError):
        pass
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
        self.crash_timestamps: list = []

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

    def _write_pid_file(self, status: str = "RUNNING"):
        sup_pid = os.getpid()
        sup_ct = get_process_create_time_nt(sup_pid)
        worker_pid = self.worker_proc.pid if self.worker_proc else None
        worker_ct = get_process_create_time_nt(worker_pid) if worker_pid else None
        now_ts = time.time()
        data = {
            "supervisor_pid": sup_pid,
            "supervisor_create_time_nt": sup_ct,
            "worker_pid": worker_pid,
            "worker_create_time_nt": worker_ct,
            "started_at_ist": get_current_ist(),
            "last_heartbeat_ts": now_ts,
            "last_heartbeat_ist": get_current_ist(),
            "status": status
        }
        with open(SUPERVISOR_PID_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _update_heartbeat(self):
        try:
            if not os.path.exists(SUPERVISOR_PID_FILE):
                return
            with open(SUPERVISOR_PID_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["last_heartbeat_ts"] = time.time()
            data["last_heartbeat_ist"] = get_current_ist()
            with open(SUPERVISOR_PID_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

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
            # Clean up any leftover stop file from previous runs
            if os.path.exists(SUPERVISOR_STOP_FILE):
                try:
                    os.remove(SUPERVISOR_STOP_FILE)
                except OSError:
                    pass

            while not self.shutdown_requested:
                if os.path.exists(SUPERVISOR_STOP_FILE):
                    log_supervisor("Stop request detected (supervisor.stop). Initiating clean shutdown...")
                    self.shutdown_requested = True
                    break

                worker_script = os.path.join(os.path.dirname(__file__), "inbox_worker.py")
                cmd = [sys.executable, "-u", worker_script]

                log_supervisor(f"Spawning inbox worker: {' '.join(cmd)}")
                creationflags = 0
                if os.name == "nt":
                    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
                try:
                    self.worker_proc = subprocess.Popen(
                        cmd,
                        cwd=WORKSPACE_DIR,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        creationflags=creationflags
                    )
                except Exception as e:
                    log_supervisor(f"ERROR spawning worker process: {e}")
                    time.sleep(self.backoff_sec)
                    continue

                spawn_time = time.time()
                self._write_pid_file()
                log_supervisor(f"Worker spawned successfully with PID={self.worker_proc.pid}")

                # Stream worker output while process is alive
                crash_output = []
                last_hb_ts = time.time()
                while self.worker_proc.poll() is None:
                    now = time.time()
                    if (now - last_hb_ts) >= 5.0:
                        self._update_heartbeat()
                        last_hb_ts = now
                    if os.path.exists(SUPERVISOR_STOP_FILE):
                        log_supervisor("Stop request detected during execution. Terminating worker child...")
                        self.shutdown_requested = True
                        self._terminate_child()
                        break

                    line = self.worker_proc.stdout.readline()
                    if line:
                        line_str = line.strip()
                        if line_str:
                            crash_output.append(line_str)
                            if len(crash_output) > 50:
                                crash_output.pop(0)
                            try:
                                print(f"  [WORKER-{self.worker_proc.pid}] {line_str}", flush=True)
                            except (OSError, ValueError):
                                pass
                    else:
                        time.sleep(0.1)

                returncode = self.worker_proc.returncode
                log_supervisor(f"Worker process (PID={self.worker_proc.pid}) exited with code {returncode}")

                if self.shutdown_requested:
                    break

                # Circuit breaker check: detect rapid repeated crashes
                now = time.time()
                run_duration = now - spawn_time
                if run_duration >= 60.0:
                    self.backoff_sec = MIN_BACKOFF_SEC
                    self.crash_timestamps = []
                else:
                    self.crash_timestamps.append(now)
                    self.crash_timestamps = [t for t in self.crash_timestamps if (now - t) <= CRASH_WINDOW_SEC]
                    if len(self.crash_timestamps) >= MAX_CONSECUTIVE_CRASHES:
                        log_supervisor(f"CIRCUIT BREAKER TRIPPED: {len(self.crash_timestamps)} crashes within {CRASH_WINDOW_SEC}s. Halting supervisor to prevent crash loop.")
                        self.circuit_breaker_tripped = True
                        self._write_pid_file(status="CIRCUIT_BREAKER_TRIPPED")
                        self.shutdown_requested = True
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
            if not getattr(self, "circuit_breaker_tripped", False):
                self._remove_pid_file()
            if os.path.exists(SUPERVISOR_STOP_FILE):
                try:
                    os.remove(SUPERVISOR_STOP_FILE)
                except OSError:
                    pass
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
        sup_ct = data.get("supervisor_create_time_nt")
        worker_pid = data.get("worker_pid")
        worker_ct = data.get("worker_create_time_nt")
        last_hb = data.get("last_heartbeat_ts")

        sup_alive = _pid_is_running(sup_pid, expected_create_time=sup_ct)
        worker_alive = _pid_is_running(worker_pid, expected_create_time=worker_ct) if worker_pid else False

        # If supervisor is running a living worker, it is RUNNING (worker may be quiet during long model calls)
        if sup_alive and worker_alive:
            return {"status": "RUNNING", "running": True, "details": data, "worker_alive": True}
        elif sup_alive and not worker_alive:
            # Detect hung supervisor only when worker is dead and supervisor fails to recover/heartbeat
            if last_hb and (time.time() - last_hb) > MAX_HEARTBEAT_STALE_SEC:
                return {"status": "SUPERVISOR_HUNG", "running": False, "details": data, "worker_alive": False}
            return {"status": "WORKER_DOWN", "running": True, "details": data, "worker_alive": False}
        else:
            return {"status": "STALE_PID", "running": False, "details": data, "worker_alive": worker_alive}
    except Exception as e:
        return {"status": "ERROR", "running": False, "error": str(e)}


def stop_daemon():
    """Signals the running supervisor to terminate cleanly."""
    status = get_status()
    if status.get("status") == "STOPPED":
        print("[SUPERVISOR] Daemon is already stopped.")
        for fpath in [SUPERVISOR_STOP_FILE, SUPERVISOR_LOCK_FILE + ".lock"]:
            if os.path.exists(fpath):
                try:
                    os.remove(fpath)
                except OSError:
                    pass
        return

    sup_pid = status.get("details", {}).get("supervisor_pid")
    sup_ct = status.get("details", {}).get("supervisor_create_time_nt")
    worker_pid = status.get("details", {}).get("worker_pid")
    worker_ct = status.get("details", {}).get("worker_create_time_nt")

    if sup_pid and _pid_is_running(sup_pid, expected_create_time=sup_ct):
        print(f"[SUPERVISOR] Sending termination signal to supervisor PID={sup_pid}...")
        # 1. Create stop file indicator
        try:
            with open(SUPERVISOR_STOP_FILE, "w", encoding="utf-8") as f:
                f.write(f"STOP requested at {get_current_ist()} for PID={sup_pid}\n")
        except Exception:
            pass

        # 2. Send signal
        try:
            if os.name == "nt" and hasattr(signal, "CTRL_BREAK_EVENT"):
                os.kill(sup_pid, signal.CTRL_BREAK_EVENT)
            else:
                os.kill(sup_pid, signal.SIGTERM)
            print("[SUPERVISOR] Termination signal sent.")
        except Exception as e:
            print(f"[SUPERVISOR] Note on process signal: {e}")

        # 3. Wait up to 5 seconds for supervisor to clean up and exit
        t0 = time.time()
        while time.time() - t0 < 5.0:
            if not _pid_is_running(sup_pid, expected_create_time=sup_ct):
                break
            time.sleep(0.2)

        # 4. If still running, force terminate ONLY this PID
        if _pid_is_running(sup_pid, expected_create_time=sup_ct):
            print(f"[SUPERVISOR] Force-terminating supervisor PID={sup_pid}...")
            try:
                os.kill(sup_pid, signal.SIGTERM)
            except Exception:
                pass

    # 5. Also terminate child worker if still alive
    if worker_pid and _pid_is_running(worker_pid, expected_create_time=worker_ct):
        print(f"[SUPERVISOR] Terminating worker child process PID={worker_pid}...")
        try:
            os.kill(worker_pid, signal.SIGTERM)
        except Exception:
            pass


    # 6. Clean up files so status transitions cleanly to STOPPED
    for fpath in [SUPERVISOR_PID_FILE, SUPERVISOR_STOP_FILE, SUPERVISOR_LOCK_FILE + ".lock"]:
        if os.path.exists(fpath):
            try:
                os.remove(fpath)
            except OSError:
                pass

    print("[SUPERVISOR] Stop operation completed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Antigravity Supervised Inbox Worker")
    parser.add_argument("--status", action="store_true", help="Check status of the daemon")
    parser.add_argument("--start", action="store_true", help="Start daemon via Windows Task Scheduler (ARGUS_Nexus_Supervisor)")
    parser.add_argument("--stop", action="store_true", help="Stop the running daemon")
    args = parser.parse_args()

    if args.status:
        st = get_status()
        print(json.dumps(st, indent=2))
    elif args.start:
        print("[SUPERVISOR] Starting daemon via Windows Task Scheduler (ARGUS_Nexus_Supervisor)...")
        res = subprocess.run(["schtasks", "/Run", "/TN", "ARGUS_Nexus_Supervisor"], capture_output=True, text=True)
        if res.returncode == 0:
            print(f"[SUPERVISOR] SUCCESS: {res.stdout.strip()}")
        else:
            print(f"[SUPERVISOR] ERROR: {res.stderr.strip() if res.stderr else res.stdout.strip()}")
    elif args.stop:
        stop_daemon()
    else:
        supervisor = SupervisedInboxWorker()
        supervisor.run()
