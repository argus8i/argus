r"""
inbox_worker.py - Antigravity Inbox Worker Daemon & Durable Messaging Processor
==============================================================================
Processes signed messages addressed to Antigravity, Claude Code, or Codex.
Enforces:
  1. Cryptographic HMAC-SHA256 authentication and nonce replay prevention.
  2. Strict alphanumeric identifier sanitization (^[a-zA-Z0-9_\-]{8,64}$) preventing path injection.
  3. Atomic file-based message claiming (.claimed) and processing state persistence.
  4. AGENTS.md Rule 11 Track Isolation and workspace boundary validation.
  5. Submission directory whitelisting (shared/track1_esm/reviews, shared/track2_liquid/reviews, shared/reviews).
  6. Mandatory Optimistic Concurrency Control (OCC) for existing file modifications.
  7. Genuine Antigravity reasoning model invocation via agy CLI for non-deterministic tasks.
  8. Anti-hedging / permission-seeking rejection and anti-fabrication completion verification.
"""

import os
import sys
import time
import json
import uuid
import hashlib
import hmac
import re
import sqlite3
import subprocess
import threading
import concurrent.futures
import weakref
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple, Callable

logger = logging.getLogger("inbox_worker")

# Workspace Root
WORKSPACE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if WORKSPACE_DIR not in sys.path:
    sys.path.insert(0, WORKSPACE_DIR)

# Messages Directory Tree
MESSAGES_ROOT = os.path.join(WORKSPACE_DIR, "antigravity", "messages")
INBOX_DIR = os.path.join(MESSAGES_ROOT, "inbox")
OUTBOX_DIR = os.path.join(MESSAGES_ROOT, "outbox")
ARCHIVE_DIR = os.path.join(MESSAGES_ROOT, "archive")
DEAD_LETTER_DIR = os.path.join(MESSAGES_ROOT, "dead_letter")
BACKUPS_DIR = os.path.join(MESSAGES_ROOT, "backups")
REPLAY_DB_PATH = os.path.join(MESSAGES_ROOT, "replay_store.db")

# Configuration Paths
AUTH_CONFIG_PATH = os.path.join(WORKSPACE_DIR, "antigravity", "config", "agent_auth.json")
DEFAULT_EXTERNAL_KEY_PATH = r"C:\Users\yashw\.gemini\antigravity\agent_keys.json"

# Security Constraints
CLAIM_TIMEOUT_SEC = 60.0  # Grace period for claims whose worker has exited.


def get_process_create_time_nt(pid: Optional[int]) -> Optional[int]:
    """Returns the 64-bit NT creation timestamp (FILETIME) of the process, or None."""
    if os.name != "nt" or not isinstance(pid, int) or pid <= 0:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            class FILETIME(ctypes.Structure):
                _fields_ = [('dwLowDateTime', wintypes.DWORD), ('dwHighDateTime', wintypes.DWORD)]
            creation = FILETIME()
            exit_t = FILETIME()
            kernel_t = FILETIME()
            user_t = FILETIME()
            if kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_t), ctypes.byref(kernel_t), ctypes.byref(user_t)):
                return (creation.dwHighDateTime << 32) | creation.dwLowDateTime
            return None
        finally:
            kernel.CloseHandle(handle)
    except Exception:
        return None


def _pid_is_running(pid: object, expected_create_time: Optional[int] = None) -> bool:
    """Fail closed when process liveness cannot be established, but detect PID recycling."""
    if type(pid) is not int or pid <= 0:
        return True
    if pid == os.getpid():
        if expected_create_time is not None:
            my_create = get_process_create_time_nt(pid)
            if my_create is not None and my_create != expected_create_time:
                return False
        return True
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.windll.kernel32
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                # ERROR_INVALID_PARAMETER (87) means process does not exist
                return ctypes.GetLastError() != 87
            try:
                exit_code = ctypes.c_ulong()
                if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return True
                if exit_code.value != 259:  # 259 == STILL_ACTIVE
                    return False

                # If process is still active, verify creation time to defeat PID recycling
                if expected_create_time is not None:
                    class FILETIME(ctypes.Structure):
                        _fields_ = [('dwLowDateTime', wintypes.DWORD), ('dwHighDateTime', wintypes.DWORD)]
                    creation = FILETIME()
                    exit_t = FILETIME()
                    kernel_t = FILETIME()
                    user_t = FILETIME()
                    if kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_t), ctypes.byref(kernel_t), ctypes.byref(user_t)):
                        actual_ct = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
                        if actual_ct != expected_create_time:
                            # PID was recycled to a different process!
                            return False

                # Check process image name to ensure it's still a python runtime
                buf = ctypes.create_unicode_buffer(512)
                size = wintypes.DWORD(512)
                if kernel.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    exe_name = buf.value.lower()
                    if "python" not in exe_name:
                        # PID was recycled to non-python process!
                        return False

                return True
            finally:
                kernel.CloseHandle(handle)
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
MAX_ATTEMPTS = 3
IDENTIFIER_REGEX = re.compile(r"^[a-zA-Z0-9_\-]{8,64}$")

VALID_SENDERS = {"CLAUDE", "CODEX", "ANTIGRAVITY"}
VALID_RECIPIENTS = {"ANTIGRAVITY", "CLAUDE", "CODEX"}
VALID_TRACKS = {"TRACK_1", "TRACK_2", "SHARED"}
VALID_STATUSES = {"CREATED", "CLAIMED", "PROCESSING", "COMPLETED", "FAILED", "TIMED_OUT", "INCOMPLETE", "CONFLICT"}

ALLOWED_SUBMISSION_DIRS = [
    os.path.normcase(os.path.abspath(os.path.join(WORKSPACE_DIR, "shared", "track1_esm", "reviews"))),
    os.path.normcase(os.path.abspath(os.path.join(WORKSPACE_DIR, "shared", "track2_liquid", "reviews"))),
    os.path.normcase(os.path.abspath(os.path.join(WORKSPACE_DIR, "shared", "reviews"))),
]

PERMISSION_SEEKING_REGEX = re.compile(
    r"(do you want me to|should i proceed|please confirm|i need your permission|would you like me to|shall i proceed)",
    re.IGNORECASE
)

# Pluggable Dispatch Hook for testing / model simulation
MODEL_DISPATCH_HOOK: Optional[Callable[[str, int], Dict[str, Any]]] = None


def ensure_directories():
    """Initializes all durable messaging directories."""
    for d in [INBOX_DIR, OUTBOX_DIR, ARCHIVE_DIR, DEAD_LETTER_DIR, BACKUPS_DIR]:
        os.makedirs(d, exist_ok=True)


def get_current_ist() -> str:
    """Returns formatted current Indian Standard Time (UTC+5:30)."""
    tz_ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(tz_ist).strftime("%Y-%m-%d %H:%M:%S IST")


def compute_sha256(filepath: str) -> Optional[str]:
    """Computes SHA-256 hexadecimal hash of a file."""
    if not os.path.exists(filepath) or os.path.isdir(filepath):
        return None
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


_ACTIVE_INBOX_WORKERS: weakref.WeakSet = weakref.WeakSet()
_ACTIVE_INBOX_WORKERS_LOCK = threading.Lock()

def _register_active_worker(worker: Any):
    with _ACTIVE_INBOX_WORKERS_LOCK:
        _ACTIVE_INBOX_WORKERS.add(worker)

def _unregister_active_worker(worker: Any):
    with _ACTIVE_INBOX_WORKERS_LOCK:
        _ACTIVE_INBOX_WORKERS.discard(worker)

def _notify_active_inbox_workers():
    with _ACTIVE_INBOX_WORKERS_LOCK:
        workers = list(_ACTIVE_INBOX_WORKERS)
    for w in workers:
        try:
            w.intake_now()
        except Exception as e:
            logger.error("Error during worker intake_now notification: %s", e)


_PROCESS_LOCKS: Dict[str, threading.Lock] = {}
_PROCESS_LOCKS_MUTEX = threading.Lock()

def _get_process_thread_lock(target_path: str) -> threading.Lock:
    norm = os.path.normcase(os.path.abspath(target_path))
    with _PROCESS_LOCKS_MUTEX:
        if norm not in _PROCESS_LOCKS:
            _PROCESS_LOCKS[norm] = threading.Lock()
        return _PROCESS_LOCKS[norm]


class FileLock:
    """
    Truly atomic per-file mutual exclusion using OS-level O_CREAT | O_EXCL
    and explicit release markers.
    Guarantees that exactly one process can hold the lock at any given time.
    """
    def __init__(self, target_path: str, timeout_sec: float = 10.0, stale_sec: float = 60.0):
        self.target_path = os.path.abspath(target_path)
        self.lock_path = self.target_path + ".lock"
        self.timeout_sec = timeout_sec
        self.stale_sec = stale_sec
        self.fd: Optional[int] = None

    def acquire(self) -> bool:
        t0 = time.time()
        while True:
            try:
                os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
                self.fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                lock_data = json.dumps({
                    "pid": os.getpid(),
                    "create_time_nt": get_process_create_time_nt(os.getpid()),
                    "acquired_at": time.time(),
                    "target": self.target_path,
                    "released": False
                }).encode("utf-8")
                os.write(self.fd, lock_data)
                return True
            except FileExistsError:
                if self._break_stale_lock():
                    continue
                if (time.time() - t0) >= self.timeout_sec:
                    return False
                time.sleep(0.02)
            except OSError:
                if (time.time() - t0) >= self.timeout_sec:
                    return False
                time.sleep(0.02)

    def _break_stale_lock(self) -> bool:
        break_token = self.lock_path + ".break"
        b_fd = None
        for _break_attempt in range(2):
            try:
                b_fd = os.open(break_token, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.write(b_fd, json.dumps({"pid": os.getpid(), "ts": time.time()}).encode("utf-8"))
                break
            except FileExistsError:
                # Another contender is in the process of inspecting/breaking the lock.
                # Clean up if break_token itself is abandoned (empty, malformed, or holder dead)
                try:
                    t_mtime = os.path.getmtime(break_token)
                    t_size = os.path.getsize(break_token)
                    age = time.time() - t_mtime
                    should_remove = False
                    if t_size == 0 and age > 0.1:
                        should_remove = True
                    elif age > 1.0:
                        try:
                            with open(break_token, "r", encoding="utf-8") as tf:
                                t_info = json.load(tf)
                            t_pid = t_info.get("pid")
                            if not t_pid or not _pid_is_running(t_pid):
                                should_remove = True
                        except (json.JSONDecodeError, ValueError):
                            should_remove = True
                    if should_remove:
                        try:
                            os.remove(break_token)
                            continue
                        except OSError:
                            pass
                except OSError:
                    pass
                return False
            except OSError:
                return False

        if b_fd is None:
            return False

        try:
            if not os.path.exists(self.lock_path):
                return True

            try:
                with open(self.lock_path, "r", encoding="utf-8") as f:
                    owner = json.load(f)
            except (json.JSONDecodeError, ValueError):
                # Malformed/torn lock: unknown ownership - fail-closed (R6)!
                return False
            except OSError:
                return False

            # If explicitly marked as released by previous owner:
            if owner.get("released"):
                try:
                    # Re-verify lock file has not been replaced by an active successor
                    with open(self.lock_path, "r", encoding="utf-8") as f_chk:
                        chk_owner = json.load(f_chk)
                    if not chk_owner.get("released"):
                        return False
                    os.remove(self.lock_path)
                    return True
                except OSError:
                    return False

            pid = owner.get("pid")
            ct = owner.get("create_time_nt")
            # If owner process is verifiably running, lock is active - never break it!
            if pid and _pid_is_running(pid, expected_create_time=ct):
                return False

            # Owner is dead - safe to remove lock after re-checking identity
            if pid and not _pid_is_running(pid, expected_create_time=ct):
                try:
                    with open(self.lock_path, "r", encoding="utf-8") as f_chk:
                        chk_owner = json.load(f_chk)
                    chk_pid = chk_owner.get("pid")
                    chk_ct = chk_owner.get("create_time_nt")
                    if chk_pid != pid or chk_ct != ct:
                        # Owner changed to another process!
                        return False
                    if _pid_is_running(chk_pid, expected_create_time=chk_ct):
                        return False
                    os.remove(self.lock_path)
                    return True
                except OSError:
                    return False

            # Unknown owner (pid missing) - fail closed (R6)
            return False
        finally:
            if b_fd is not None:
                try:
                    os.close(b_fd)
                except OSError:
                    pass
                try:
                    os.remove(break_token)
                except OSError:
                    pass

    def release(self):
        if self.fd is not None:
            try:
                # Mark as released before closing handle
                os.lseek(self.fd, 0, os.SEEK_SET)
                rel_bytes = b'{"released": true}'
                os.write(self.fd, rel_bytes)
                os.ftruncate(self.fd, len(rel_bytes))
            except OSError:
                pass
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
        # Note: Do NOT unlink self.lock_path here. Unlinking after closing fd creates an
        # ownership race where owner A unlinks successor B's newly acquired lock!
        # Successors safely clear released markers when acquiring via _break_stale_lock().

    def __enter__(self):
        if not self.acquire():
            raise TimeoutError(f"Could not acquire lock for '{self.target_path}' within {self.timeout_sec}s")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


def write_json_atomic(filepath: str, data: Dict[str, Any], allow_overwrite_claimed: bool = False):
    """Writes a dictionary to JSON atomically using a temporary file and replace."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    tmp_path = filepath + f".tmp_{uuid.uuid4().hex[:8]}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    # For inbox messages, serialize write attempts to avoid racing overwrites of identical message_ids
    norm_dir = os.path.normpath(os.path.dirname(filepath))
    is_inbox = os.path.basename(norm_dir) == "inbox" and filepath.endswith(".json")
    if is_inbox:
        claimed_path = filepath.replace(".json", ".claimed")
        dead_path = os.path.join(DEAD_LETTER_DIR, os.path.basename(filepath).replace(".json", ".dead.json"))
        archive_path = os.path.join(ARCHIVE_DIR, os.path.basename(filepath))
        msg_id = data.get("message_id") or os.path.basename(filepath).replace(".json", "")
        written = False
        try:
            with FileLock(filepath, timeout_sec=5.0):
                # Cross-process check: if already present in inbox, dead-letter, archive, or claimed
                if (os.path.exists(filepath) or os.path.exists(dead_path) or os.path.exists(archive_path) or
                    (not allow_overwrite_claimed and os.path.exists(claimed_path))):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
                    return
                # Also check durable admission store state inside cross-process lock
                if not allow_overwrite_claimed:
                    store = get_default_admission_store()
                    if store:
                        st = store.get_message_state(msg_id)
                        if st and st in ("CLAIMED", "COMPLETED", "DEAD"):
                            try:
                                os.remove(tmp_path)
                            except OSError:
                                pass
                            return
                for attempt in range(5):
                    try:
                        os.replace(tmp_path, filepath)
                        written = True
                        break
                    except PermissionError:
                        if attempt == 4:
                            raise
                        time.sleep(0.02)
        except TimeoutError:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            return
        if written:
            _notify_active_inbox_workers()
        return
    else:
        for attempt in range(5):
            try:
                os.replace(tmp_path, filepath)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.02)


class DurableAdmissionStore:
    """
    Durable, SQLite-backed cross-process admission and replay prevention store with WAL mode.
    Coordinates message admission, receipt binding, lifecycle tracking, and orphan recovery
    across all processes and life-cycle states (QUEUED, CLAIMED, RECOVERING, RECOVERED_ACTIVE, COMPLETED, DEAD).
    """
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.path.join(MESSAGES_ROOT, "admission_store.db")
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._init_db()

    def _init_db(self):
        try:
            with sqlite3.connect(self.db_path, timeout=15.0) as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS seen_nonces (
                        nonce TEXT PRIMARY KEY,
                        sender TEXT NOT NULL,
                        timestamp_ist TEXT NOT NULL,
                        recorded_at REAL NOT NULL,
                        message_id TEXT,
                        state TEXT NOT NULL DEFAULT 'RECORDED'
                    );
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS message_admissions (
                        message_id TEXT PRIMARY KEY,
                        correlation_id TEXT NOT NULL,
                        payload_hash TEXT NOT NULL,
                        sender TEXT NOT NULL,
                        recipient TEXT NOT NULL,
                        subject TEXT NOT NULL,
                        admitted_at REAL NOT NULL,
                        original_timestamp_ist TEXT NOT NULL,
                        state TEXT NOT NULL DEFAULT 'QUEUED',
                        attempt_count INTEGER NOT NULL DEFAULT 0
                    );
                """)
                cursor = conn.execute("PRAGMA table_info(seen_nonces);")
                cols = {row[1] for row in cursor.fetchall()}
                if "message_id" not in cols:
                    conn.execute("ALTER TABLE seen_nonces ADD COLUMN message_id TEXT;")
                if "state" not in cols:
                    conn.execute("ALTER TABLE seen_nonces ADD COLUMN state TEXT NOT NULL DEFAULT 'RECORDED';")
                cursor_adm = conn.execute("PRAGMA table_info(message_admissions);")
                cols_adm = {row[1] for row in cursor_adm.fetchall()}
                if "completed_at" not in cols_adm:
                    conn.execute("ALTER TABLE message_admissions ADD COLUMN completed_at REAL;")
                if "response_json" not in cols_adm:
                    conn.execute("ALTER TABLE message_admissions ADD COLUMN response_json TEXT;")
                conn.commit()
        except Exception:
            pass

    def admit_submission(
        self,
        message_id: str,
        correlation_id: str,
        payload_hash: str,
        sender: str,
        recipient: str,
        subject: str,
        timestamp_ist: str,
    ) -> Tuple[bool, str, Optional[str], str]:
        """
        Atomically coordinates submission across multiple processes.
        Returns: (is_new_admission, winning_correlation_id, error_or_conflict, state)
        """
        now = time.time()
        try:
            with sqlite3.connect(self.db_path, timeout=15.0) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL;")
                cur = conn.execute(
                    "SELECT correlation_id, payload_hash, sender, recipient, subject, state FROM message_admissions WHERE message_id = ?",
                    (message_id,)
                )
                row = cur.fetchone()
                if row is not None:
                    # Check full identity contract: sender, recipient, subject, payload_hash
                    if (row["payload_hash"] != payload_hash or
                        row["sender"] != sender or
                        row["recipient"] != recipient or
                        row["subject"] != subject):
                        return False, row["correlation_id"], (
                            f"CONFLICT: message_id '{message_id}' already admitted with different identity attributes"
                        ), row["state"]
                    return False, row["correlation_id"], None, row["state"]

                try:
                    conn.execute(
                        """
                        INSERT INTO message_admissions (
                            message_id, correlation_id, payload_hash, sender, recipient, subject,
                            admitted_at, original_timestamp_ist, state, attempt_count
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'QUEUED', 0)
                        """,
                        (message_id, correlation_id, payload_hash, sender, recipient, subject, now, timestamp_ist)
                    )
                    conn.commit()
                    return True, correlation_id, None, "QUEUED"
                except sqlite3.IntegrityError:
                    cur = conn.execute(
                        "SELECT correlation_id, payload_hash, sender, recipient, subject, state FROM message_admissions WHERE message_id = ?",
                        (message_id,)
                    )
                    row = cur.fetchone()
                    if row and (row["payload_hash"] != payload_hash or
                                row["sender"] != sender or
                                row["recipient"] != recipient or
                                row["subject"] != subject):
                        return False, row["correlation_id"], (
                            f"CONFLICT: message_id '{message_id}' already admitted with different identity attributes"
                        ), row["state"]
                    return False, row["correlation_id"] if row else correlation_id, None, row["state"] if row else "QUEUED"
        except Exception as e:
            return False, correlation_id, f"ADMISSION_STORE_ERROR: {e}", "ERROR"

    def get_message_state(self, message_id: str) -> Optional[str]:
        """Returns the current lifecycle state of a message from the admission store."""
        try:
            with sqlite3.connect(self.db_path, timeout=10.0) as conn:
                cur = conn.execute("SELECT state FROM message_admissions WHERE message_id = ?", (message_id,))
                row = cur.fetchone()
                return row[0] if row else None
        except Exception as e:
            logger.error("Error reading message state for %s: %s", message_id, e)
            return "ERROR"

    def is_message_recovering(self, message_id: str, payload_hash: Optional[str] = None) -> bool:
        """Checks if a message has been authorized for recovery retry in the durable store."""
        try:
            with sqlite3.connect(self.db_path, timeout=10.0) as conn:
                cur = conn.execute(
                    "SELECT state, payload_hash FROM message_admissions WHERE message_id = ?",
                    (message_id,)
                )
                row = cur.fetchone()
                if row and row[0] in ("RECOVERING", "RECOVERED_ACTIVE"):
                    if payload_hash is None or not row[1] or row[1] == payload_hash:
                        return True
                # Fallback for pre-upgrade nonces without an admission row
                cur2 = conn.execute(
                    "SELECT state FROM seen_nonces WHERE message_id = ? AND state = 'RECOVERED_RETRY_PENDING'",
                    (message_id,)
                )
                if cur2.fetchone() is not None:
                    return True
        except Exception:
            pass
        return False

    def mark_message_claimed(
        self,
        message_id: str,
        correlation_id: Optional[str] = None,
        payload_hash: Optional[str] = None,
        sender: Optional[str] = None,
        recipient: Optional[str] = None,
        subject: Optional[str] = None,
        timestamp_ist: Optional[str] = None,
    ) -> bool:
        """Atomically marks message as CLAIMED in admission store."""
        try:
            with sqlite3.connect(self.db_path, timeout=10.0) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.execute("SELECT state FROM message_admissions WHERE message_id = ?", (message_id,))
                row = cur.fetchone()
                if row is None:
                    # Message enqueued directly without prior admission row: create row as CLAIMED
                    conn.execute(
                        """
                        INSERT INTO message_admissions
                        (message_id, correlation_id, payload_hash, sender, recipient, subject, admitted_at, original_timestamp_ist, state, attempt_count)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CLAIMED', 0)
                        """,
                        (
                            message_id,
                            correlation_id or f"corr_{message_id}",
                            payload_hash or "",
                            sender or "UNKNOWN",
                            recipient or "UNKNOWN",
                            subject or "UNKNOWN",
                            time.time(),
                            timestamp_ist or get_current_ist(),
                        )
                    )
                    conn.commit()
                    return True
                if row["state"] in ("COMPLETED", "DEAD"):
                    # Terminal state: cannot claim!
                    return False
                if row["state"] in ("QUEUED", "RECOVERING", "RECOVERED_ACTIVE"):
                    cur_update = conn.execute(
                        "UPDATE message_admissions SET state = 'CLAIMED' WHERE message_id = ? AND state IN ('QUEUED', 'RECOVERING', 'RECOVERED_ACTIVE')",
                        (message_id,)
                    )
                    conn.commit()
                    return cur_update.rowcount > 0
                return False
        except Exception as e:
            logger.error("Error in mark_message_claimed for %s: %s", message_id, e)
            return False

    def mark_message_completed(self, message_id: str, response_json: Optional[str] = None) -> bool:
        """Persists COMPLETED terminal state with completion timestamp and optional response JSON in admission store."""
        try:
            with sqlite3.connect(self.db_path, timeout=10.0) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.execute("SELECT state FROM message_admissions WHERE message_id = ?", (message_id,))
                row = cur.fetchone()
                if row is None:
                    # Pre-upgrade or unrecorded envelope: record directly as COMPLETED
                    conn.execute(
                        """
                        INSERT INTO message_admissions (
                            message_id, correlation_id, payload_hash, sender, recipient, subject,
                            admitted_at, original_timestamp_ist, state, completed_at, response_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'COMPLETED', ?, ?)
                        """,
                        (message_id, f"corr_{message_id}", "", "", "", "", time.time(), get_current_ist(), time.time(), response_json)
                    )
                    conn.commit()
                    return True
                elif row["state"] == "DEAD":
                    # Cannot overwrite DEAD terminal state
                    return False
                else:
                    cur_up = conn.execute(
                        "UPDATE message_admissions SET state = 'COMPLETED', completed_at = ?, response_json = COALESCE(?, response_json) WHERE message_id = ? AND state != 'DEAD'",
                        (time.time(), response_json, message_id)
                    )
                    conn.commit()
                    return cur_up.rowcount > 0
        except Exception as e:
            logger.error("Failed to mark message %s completed in store: %s", message_id, e)
            return False

    def get_message_response(self, message_id: str) -> Optional[str]:
        """Retrieves durably persisted response JSON for a completed message."""
        try:
            with sqlite3.connect(self.db_path, timeout=10.0) as conn:
                cur = conn.execute("SELECT response_json FROM message_admissions WHERE message_id = ?", (message_id,))
                row = cur.fetchone()
                return row[0] if row and row[0] else None
        except Exception:
            return None

    def is_nonce_recovering(self, nonce: str, message_id: Optional[str] = None) -> bool:
        """Checks if a nonce is in RECOVERED_RETRY_PENDING state for the given message."""
        try:
            with sqlite3.connect(self.db_path, timeout=10.0) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.execute("SELECT state, message_id FROM seen_nonces WHERE nonce = ?", (nonce,))
                row = cur.fetchone()
                if row and row["state"] == "RECOVERED_RETRY_PENDING":
                    if message_id is None or not row["message_id"] or row["message_id"] == message_id:
                        return True
                return False
        except Exception:
            return False

    def mark_message_dead(self, message_id: str, error: Optional[str] = None, response_json: Optional[str] = None) -> bool:
        """Persists DEAD terminal state with completion timestamp and optional response_json in admission store."""
        try:
            with sqlite3.connect(self.db_path, timeout=10.0) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.execute("SELECT state FROM message_admissions WHERE message_id = ?", (message_id,))
                row = cur.fetchone()
                if row is None:
                    # Pre-upgrade or unrecorded envelope: record directly as DEAD
                    conn.execute(
                        """
                        INSERT INTO message_admissions (
                            message_id, correlation_id, payload_hash, sender, recipient, subject,
                            admitted_at, original_timestamp_ist, state, completed_at, response_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'DEAD', ?, ?)
                        """,
                        (message_id, f"corr_{message_id}", "", "", "", "", time.time(), get_current_ist(), time.time(), response_json)
                    )
                    conn.commit()
                    return True
                elif row["state"] == "COMPLETED":
                    # Cannot overwrite COMPLETED terminal state
                    return False
                else:
                    cur_up = conn.execute(
                        """
                        UPDATE message_admissions
                        SET state = 'DEAD', completed_at = ?, response_json = COALESCE(?, response_json)
                        WHERE message_id = ? AND state != 'COMPLETED'
                        """,
                        (time.time(), response_json, message_id)
                    )
                    conn.commit()
                    return cur_up.rowcount > 0
        except Exception as e:
            logger.error("Failed to mark message %s dead in store: %s", message_id, e)
            return False

    def mark_message_recovering(
        self,
        message_id: str,
        nonce: Optional[str] = None,
        correlation_id: Optional[str] = None,
        payload_hash: Optional[str] = None,
        sender: Optional[str] = None,
        recipient: Optional[str] = None,
        subject: Optional[str] = None,
        timestamp_ist: Optional[str] = None,
    ) -> bool:
        """Marks message for recovery if not terminal. Returns True if authorized for recovery, False if terminal or error."""
        try:
            with sqlite3.connect(self.db_path, timeout=10.0) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.execute("SELECT state FROM message_admissions WHERE message_id = ?", (message_id,))
                row = cur.fetchone()
                if row is None:
                    # Pre-upgrade claim without an existing admission row: create row in RECOVERING state
                    conn.execute(
                        """
                        INSERT INTO message_admissions (
                            message_id, correlation_id, payload_hash, sender, recipient, subject,
                            admitted_at, original_timestamp_ist, state, attempt_count
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'RECOVERING', 1)
                        """,
                        (
                            message_id,
                            correlation_id or f"corr_{message_id}",
                            payload_hash or "",
                            sender or "",
                            recipient or "",
                            subject or "",
                            time.time(),
                            timestamp_ist or get_current_ist(),
                        )
                    )
                else:
                    if row["state"] in ("COMPLETED", "DEAD"):
                        # Terminal state: NEVER reverse back to RECOVERING!
                        return False
                    cur_up = conn.execute(
                        "UPDATE message_admissions SET state = 'RECOVERING', attempt_count = attempt_count + 1 WHERE message_id = ? AND state NOT IN ('COMPLETED', 'DEAD')",
                        (message_id,)
                    )
                    if cur_up.rowcount == 0:
                        # Row transition did not occur (e.g. concurrent transition to terminal state)
                        conn.rollback()
                        return False
                if nonce:
                    cur_n = conn.execute("SELECT state FROM seen_nonces WHERE nonce = ?", (nonce,))
                    nrow = cur_n.fetchone()
                    if nrow is not None:
                        if nrow["state"] in ("COMPLETED", "FAILED", "RECOVERED_RETRY_CONSUMED"):
                            # Incompatible nonce state: cannot authorize recovery! Roll back entire paired transition.
                            conn.rollback()
                            return False
                        cur_n_up = conn.execute(
                            "UPDATE seen_nonces SET state = 'RECOVERED_RETRY_PENDING' WHERE nonce = ? AND state NOT IN ('COMPLETED', 'FAILED', 'RECOVERED_RETRY_CONSUMED')",
                            (nonce,)
                        )
                        if cur_n_up.rowcount == 0:
                            conn.rollback()
                            return False
                    else:
                        # Legitimate missing-nonce compatibility (e.g. pre-upgrade claim): insert nonce row as RECOVERED_RETRY_PENDING
                        conn.execute(
                            "INSERT INTO seen_nonces (nonce, sender, timestamp_ist, recorded_at, message_id, state) VALUES (?, ?, ?, ?, ?, 'RECOVERED_RETRY_PENDING')",
                            (nonce, sender or "", timestamp_ist or get_current_ist(), time.time(), message_id)
                        )
                conn.commit()
                return True
        except Exception as e:
            logger.error("Error in mark_message_recovering for %s: %s", message_id, e)
            return False

    def check_and_record_nonce(
        self,
        nonce: str,
        sender: str,
        timestamp_ist: str,
        ttl_sec: float = 600.0,
        message_id: Optional[str] = None,
        allow_recovery: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        now = time.time()
        try:
            with sqlite3.connect(self.db_path, timeout=10.0) as conn:
                conn.row_factory = sqlite3.Row
                # Prune only completed/failed nonces older than TTL; NEVER prune message_admissions!
                conn.execute("DELETE FROM seen_nonces WHERE recorded_at < ? AND state IN ('COMPLETED', 'FAILED')", (now - ttl_sec,))

                # Terminal message admissions can NEVER be re-admitted or recovered, EVEN WITH A FRESH NONCE!
                if message_id:
                    cur_m = conn.execute("SELECT state FROM message_admissions WHERE message_id = ?", (message_id,))
                    m_row = cur_m.fetchone()
                    if m_row and m_row["state"] in ("COMPLETED", "DEAD"):
                        return False, f"TERMINAL_STATE: Message '{message_id}' is already in terminal state '{m_row['state']}'."

                cur = conn.execute("SELECT * FROM seen_nonces WHERE nonce = ?", (nonce,))
                row = cur.fetchone()
                if row is not None:
                    # Consumed, completed, or failed nonces can NEVER be reused or recovered
                    if row["state"] in ("COMPLETED", "FAILED", "RECOVERED_RETRY_CONSUMED"):
                        return False, f"REPLAY_ATTACK: Nonce '{nonce}' has already been completed or consumed (state='{row['state']}')."
                    if (allow_recovery or row["state"] == "RECOVERED_RETRY_PENDING") and (row["message_id"] == message_id or not row["message_id"]):
                        conn.execute("UPDATE seen_nonces SET state = 'RECOVERED_RETRY_CONSUMED', recorded_at = ? WHERE nonce = ?", (now, nonce))
                        if message_id:
                            conn.execute("UPDATE message_admissions SET state = 'RECOVERED_ACTIVE' WHERE message_id = ? AND state NOT IN ('COMPLETED', 'DEAD')", (message_id,))
                        conn.commit()
                        return True, None
                    return False, f"REPLAY_ATTACK: Nonce '{nonce}' has already been processed."
                conn.execute(
                    "INSERT INTO seen_nonces (nonce, sender, timestamp_ist, recorded_at, message_id, state) VALUES (?, ?, ?, ?, ?, ?)",
                    (nonce, sender, timestamp_ist, now, message_id, "RECORDED")
                )
                conn.commit()
            return True, None
        except sqlite3.IntegrityError:
            return False, f"REPLAY_ATTACK: Nonce '{nonce}' has already been processed."
        except Exception as e:
            return False, f"REPLAY_STORE_ERROR: {e}"

    def mark_nonce_for_recovery(self, nonce: str, message_id: Optional[str] = None) -> bool:
        """Marks a nonce as eligible for exactly one recovered retry with terminal immunity."""
        try:
            with sqlite3.connect(self.db_path, timeout=10.0) as conn:
                cur_n = conn.execute(
                    "UPDATE seen_nonces SET state = 'RECOVERED_RETRY_PENDING' WHERE nonce = ? AND state NOT IN ('COMPLETED', 'FAILED')",
                    (nonce,)
                )
                if cur_n.rowcount == 0:
                    conn.rollback()
                    return False
                if message_id:
                    cur_m = conn.execute(
                        "UPDATE message_admissions SET state = 'RECOVERING', attempt_count = attempt_count + 1 WHERE message_id = ? AND state NOT IN ('COMPLETED', 'DEAD')",
                        (message_id,)
                    )
                    if cur_m.rowcount == 0:
                        conn.rollback()
                        return False
                conn.commit()
                return True
        except Exception:
            return False

    def mark_nonce_completed(self, nonce: str, message_id: Optional[str] = None) -> bool:
        """Marks a nonce and admission as completed with terminal immunity."""
        try:
            with sqlite3.connect(self.db_path, timeout=10.0) as conn:
                cur_n = conn.execute(
                    "UPDATE seen_nonces SET state = 'COMPLETED', recorded_at = ? WHERE nonce = ? AND state != 'FAILED'",
                    (time.time(), nonce)
                )
                if cur_n.rowcount == 0:
                    conn.rollback()
                    return False
                if message_id:
                    cur_m = conn.execute(
                        "UPDATE message_admissions SET state = 'COMPLETED', completed_at = ? WHERE message_id = ? AND state != 'DEAD'",
                        (time.time(), message_id)
                    )
                    if cur_m.rowcount == 0:
                        conn.rollback()
                        return False
                conn.commit()
                return True
        except Exception:
            return False

    def mark_nonce_failed(self, nonce: str, message_id: Optional[str] = None) -> bool:
        """Marks a nonce and admission as failed with terminal immunity."""
        try:
            with sqlite3.connect(self.db_path, timeout=10.0) as conn:
                cur_n = conn.execute(
                    "UPDATE seen_nonces SET state = 'FAILED', recorded_at = ? WHERE nonce = ? AND state != 'COMPLETED'",
                    (time.time(), nonce)
                )
                if cur_n.rowcount == 0:
                    conn.rollback()
                    return False
                if message_id:
                    cur_m = conn.execute(
                        "UPDATE message_admissions SET state = 'DEAD', completed_at = ? WHERE message_id = ? AND state != 'COMPLETED'",
                        (time.time(), message_id)
                    )
                    if cur_m.rowcount == 0:
                        conn.rollback()
                        return False
                conn.commit()
                return True
        except Exception:
            return False


DurableReplayStore = DurableAdmissionStore


def compute_payload_hash(body: Any) -> str:
    """Computes a deterministic SHA-256 hash of the request body/payload."""
    if isinstance(body, str):
        payload_bytes = body.encode("utf-8")
    else:
        try:
            payload_bytes = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode("utf-8")
        except Exception:
            payload_bytes = str(body).encode("utf-8")
    return hashlib.sha256(payload_bytes).hexdigest()


_ACTIVE_STORE: Optional[DurableAdmissionStore] = None
_DEFAULT_ADMISSION_STORE: Optional[DurableAdmissionStore] = None
_DEFAULT_ADMISSION_STORE_LOCK = threading.Lock()

def get_default_admission_store() -> DurableAdmissionStore:
    global _DEFAULT_ADMISSION_STORE, _ACTIVE_STORE
    if _ACTIVE_STORE is not None:
        try:
            if os.path.dirname(os.path.abspath(_ACTIVE_STORE.db_path)) == os.path.abspath(MESSAGES_ROOT):
                return _ACTIVE_STORE
        except Exception:
            pass
    with _DEFAULT_ADMISSION_STORE_LOCK:
        replay_db = os.path.join(MESSAGES_ROOT, "replay_store.db")
        admission_db = os.path.join(MESSAGES_ROOT, "admission_store.db")
        target_db = admission_db if (os.path.exists(admission_db) and not os.path.exists(replay_db)) else replay_db
        if _DEFAULT_ADMISSION_STORE is None or _DEFAULT_ADMISSION_STORE.db_path != target_db:
            _DEFAULT_ADMISSION_STORE = DurableAdmissionStore(target_db)
        return _DEFAULT_ADMISSION_STORE


def load_auth_config() -> Dict[str, Any]:
    """
    Loads authentication configuration and per-agent cryptographic keys.
    Keys are strictly loaded from external storage outside the repository:
    1. Path in TRI_AGENT_KEY_FILE environment variable.
    2. Path in agent_auth.json ("key_storage_path").
    3. Default: C:\\Users\\yashw\\.gemini\\antigravity\\agent_keys.json
    """
    external_path = os.environ.get("TRI_AGENT_KEY_FILE")
    if not external_path and os.path.exists(AUTH_CONFIG_PATH):
        try:
            with open(AUTH_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                external_path = cfg.get("key_storage_path")
        except Exception:
            pass
    if not external_path:
        external_path = DEFAULT_EXTERNAL_KEY_PATH

    if os.path.exists(external_path):
        try:
            with open(external_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    raise RuntimeError(
        f"External authentication configuration not found at {external_path}. "
        "Hardcoded fallback signing keys are strictly prohibited per Constitution Article 5."
    )


def get_agent_secret_key(agent_name: str) -> Optional[str]:
    """Returns the external secret key for a specific agent."""
    cfg = load_auth_config()
    keys = cfg.get("keys", {})
    key = keys.get(agent_name) if isinstance(keys, dict) else None
    return key if isinstance(key, str) and key else None


RUNTIME_METADATA_FIELDS = {
    "auth_signature", "claimed_at_ist", "claimed_at_ts", "worker_pid", "worker_create_time_nt",
    "processing_started_at_ist", "auth_verified_at_ist"
}


def canonicalize_envelope(envelope: Dict[str, Any]) -> str:
    """
    Deterministically serializes envelope fields for HMAC signing and verification:
    - Excludes 'auth_signature' and worker runtime tracking fields.
    - Ignores None values so omitted vs null fields are canonicalized identically.
    - Normalizes request status and attempt_count so worker claim/retry transitions do not break sender signatures.
    - Keys are strictly sorted with normalized separators (',', ':').
    """
    d = {}
    for k, v in envelope.items():
        if k in RUNTIME_METADATA_FIELDS or v is None:
            continue
        if k == "status" and envelope.get("recipient") in VALID_RECIPIENTS and v in ["CLAIMED", "PROCESSING"]:
            d[k] = "CREATED"
        elif k == "attempt_count":
            d[k] = 0
        else:
            d[k] = v
    return json.dumps(d, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def compute_envelope_hmac(envelope: Dict[str, Any], secret_key: Union[str, bytes]) -> str:
    """Computes HMAC-SHA256 over all canonical envelope fields."""
    canonical = canonicalize_envelope(envelope)
    key_bytes = secret_key.encode("utf-8") if isinstance(secret_key, str) else secret_key
    return hmac.new(key_bytes, canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def compute_message_hmac(msg: Dict[str, Any], secret_key: str) -> str:
    """Computes HMAC over all envelope fields (calls compute_envelope_hmac)."""
    return compute_envelope_hmac(msg, secret_key)


def verify_message_auth(
    msg: Dict[str, Any],
    record_nonce: bool = True,
    allow_recovery: bool = False,
    check_freshness: bool = True,
    admission_store: Optional[DurableAdmissionStore] = None,
) -> Tuple[bool, Optional[str]]:
    """Validates message HMAC-SHA256 signature, durable nonce uniqueness, and timestamp freshness.
    Strict evaluation order: schema/fields -> timestamp freshness -> HMAC verification -> record nonce.
    """
    auth_cfg = load_auth_config()
    sender = msg.get("sender", "")
    secret_key = get_agent_secret_key(sender)
    if not secret_key:
        return False, f"AUTH_FAILED: No cryptographic secret key found for sender '{sender}'."

    sig = msg.get("auth_signature")
    if not sig:
        return False, "AUTH_FAILED: Missing 'auth_signature' in message envelope."

    nonce = msg.get("nonce")
    if not nonce or not isinstance(nonce, str) or len(nonce) < 8:
        return False, "AUTH_FAILED: Missing or invalid 'nonce' in message envelope."

    # Determine recovery status STRICTLY from durable store (never trust caller flags or attempt_count!)
    store = admission_store or get_default_admission_store()
    msg_id = msg.get("message_id")
    is_recovered = False
    if store and msg_id:
        p_hash = compute_payload_hash(msg.get("body"))
        if store.is_message_recovering(msg_id, p_hash):
            is_recovered = True
    if not is_recovered and store and nonce:
        if hasattr(store, "is_nonce_recovering") and store.is_nonce_recovering(nonce, msg_id):
            is_recovered = True
        else:
            try:
                with sqlite3.connect(store.db_path, timeout=5.0) as conn:
                    conn.row_factory = sqlite3.Row
                    cur = conn.execute("SELECT state, message_id FROM seen_nonces WHERE nonce = ?", (nonce,))
                    nrow = cur.fetchone()
                    if nrow and nrow["state"] == "RECOVERED_RETRY_PENDING":
                        if not nrow["message_id"] or nrow["message_id"] == msg_id:
                            is_recovered = True
            except Exception:
                pass

    # 1. Timestamp Freshness Check (checked BEFORE burning nonce)
    if check_freshness:
        ts_str = msg.get("created_at_ist", "")
        try:
            ts_clean = ts_str.replace(" IST", "")
            msg_dt = datetime.strptime(ts_clean, "%Y-%m-%d %H:%M:%S")
            tz_ist = timezone(timedelta(hours=5, minutes=30))
            now_dt = datetime.now(tz_ist).replace(tzinfo=None)
            delta_sec = (now_dt - msg_dt).total_seconds()
            val_sec = auth_cfg.get("token_validity_sec", 300)
            skew_sec = auth_cfg.get("max_future_skew_sec", 60)

            if delta_sec > val_sec:
                if not is_recovered:
                    return False, f"TIMESTAMP_OUT_OF_BOUNDS: Message expired ({delta_sec:.1f}s old > {val_sec}s limit)."
            if delta_sec < -skew_sec:
                return False, f"TIMESTAMP_OUT_OF_BOUNDS: Message timestamp is in the future by {-delta_sec:.1f}s (> {skew_sec}s limit)."
        except Exception as e:
            return False, f"TIMESTAMP_FORMAT_ERROR: Unable to parse created_at_ist '{ts_str}': {e}"

    # 2. HMAC Verification across all envelope fields (checked BEFORE burning nonce)
    expected_sig = compute_envelope_hmac(msg, secret_key)
    if not hmac.compare_digest(sig, expected_sig):
        return False, "AUTH_FAILED: Cryptographic HMAC signature verification failed."

    # 3. Durable SQLite Nonce Replay Check (recorded ONLY after timestamp and HMAC pass)
    if record_nonce:
        nonce_ok, nonce_err = store.check_and_record_nonce(
            nonce, sender, msg.get("created_at_ist", ""),
            message_id=msg_id, allow_recovery=is_recovered
        )
        if not nonce_ok:
            return False, nonce_err

    return True, None


def get_safe_filename(raw_id: Any, suffix: str) -> str:
    """Sanitizes raw identifier to prevent any path traversal when creating files."""
    clean = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(raw_id))[:64]
    if not clean:
        clean = uuid.uuid4().hex[:12]
    return f"{clean}{suffix}"


def validate_path_security(
    path_str: Optional[str],
    track: str,
    workspace_dir: Optional[str] = None
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Validates that a relative or absolute path is within base_ws (WORKSPACE_DIR) and respects Rule 11.
    Returns: (is_valid, resolved_absolute_path, error_message)
    """
    if not path_str:
        return True, None, None

    base_ws = os.path.realpath(workspace_dir or WORKSPACE_DIR)

    # Resolve real path (resolving symbolic links, directory junctions, etc.)
    if os.path.isabs(path_str):
        resolved = os.path.realpath(path_str)
    else:
        resolved = os.path.realpath(os.path.join(base_ws, path_str))

    # Path traversal check: must start with base_ws
    norm_workspace = os.path.normcase(base_ws)
    norm_resolved = os.path.normcase(resolved)
    if not (norm_resolved == norm_workspace or norm_resolved.startswith(norm_workspace + os.path.sep)):
        return False, None, f"SECURITY_VIOLATION: Path traversal outside workspace '{path_str}'"

    # AGENTS.md Rule 11: Absolute Track Isolation
    rel_path = os.path.relpath(resolved, base_ws).replace("\\", "/")
    if track == "TRACK_1":
        if rel_path.startswith("shared/track2_liquid") or "track2" in rel_path.lower():
            return False, None, f"TRACK_ISOLATION_VIOLATION: Track 1 message targeting Track 2 path '{rel_path}'"
    elif track == "TRACK_2":
        if rel_path.startswith("shared/track1_esm") or "track1" in rel_path.lower():
            return False, None, f"TRACK_ISOLATION_VIOLATION: Track 2 message targeting Track 1 path '{rel_path}'"

    return True, resolved, None



def validate_message_schema(
    msg: Dict[str, Any],
    record_nonce: Optional[bool] = None,
    allow_recovery: bool = False,
    check_freshness: bool = True,
    admission_store: Optional[DurableAdmissionStore] = None,
) -> Tuple[bool, Optional[str]]:
    """Validates incoming message envelope schema and security constraints strictly."""
    required_fields = [
        "message_id", "correlation_id", "sender", "recipient",
        "created_at_ist", "subject", "body", "status", "attempt_count",
        "auth_signature", "nonce"
    ]
    for rf in required_fields:
        if rf not in msg:
            return False, f"SCHEMA_ERROR: Missing required field '{rf}'"

    # Strict Alphanumeric Identifier Verification
    msg_id = msg.get("message_id", "")
    corr_id = msg.get("correlation_id", "")
    if not IDENTIFIER_REGEX.match(str(msg_id)):
        return False, f"INVALID_IDENTIFIER: message_id '{msg_id}' violates regex ^[a-zA-Z0-9_\\-]{{8,64}}$"
    if not IDENTIFIER_REGEX.match(str(corr_id)):
        return False, f"INVALID_IDENTIFIER: correlation_id '{corr_id}' violates regex ^[a-zA-Z0-9_\\-]{{8,64}}$"

    if msg["sender"] == "USER":
        return False, "UNAUTHORIZED_SENDER: Sender 'USER' is refused on the bus. Owner decisions must be recorded strictly via shared/governance/owner_decisions.jsonl."

    if msg["sender"] not in VALID_SENDERS:
        return False, f"UNAUTHORIZED_SENDER: Sender '{msg['sender']}' not in {VALID_SENDERS}"

    if msg["recipient"] not in VALID_RECIPIENTS:
        return False, f"INVALID_RECIPIENT: Recipient '{msg['recipient']}' not in {VALID_RECIPIENTS}"

    track = msg.get("track", "SHARED")
    if track not in VALID_TRACKS:
        return False, f"INVALID_TRACK: Track '{track}' not in {VALID_TRACKS}"

    # Inbound status must be CREATED (or CLAIMED after being claimed by worker)
    if msg["status"] not in ["CREATED", "CLAIMED"]:
        return False, f"INVALID_STATUS: Inbound message must have status 'CREATED', got '{msg['status']}'"

    # Attempt count check
    if not isinstance(msg["attempt_count"], int) or msg["attempt_count"] < 0:
        return False, f"INVALID_ATTEMPT_COUNT: attempt_count must be non-negative integer, got '{msg['attempt_count']}'"

    # Validate paths if present
    for path_key in ["source_file", "expected_response_file"]:
        val = msg.get(path_key)
        if val:
            valid, _, err = validate_path_security(val, track)
            if not valid:
                return False, err

    # Mandatory Pre-task Hashes & Expected Output for Editing Tasks
    subject = str(msg.get("subject", "")).upper()
    body = msg.get("body")
    if subject == "WRITE_SUBMISSION" or (isinstance(body, dict) and body.get("target_file")):
        sub_file = body.get("target_file") if isinstance(body, dict) else None
        exp_file = msg.get("expected_response_file")
        if not exp_file:
            return False, "EXPECTED_OUTPUT_REQUIRED: Editing task requires 'expected_response_file' in message envelope."

        target_path_to_check = sub_file or exp_file
        valid_p, abs_target, _ = validate_path_security(target_path_to_check, track)
        if valid_p and abs_target and os.path.exists(abs_target):
            pre_hash = msg.get("pre_task_hash") or (body.get("expected_base_hash") if isinstance(body, dict) else None)
            if not pre_hash:
                return False, f"PRE_TASK_HASH_REQUIRED: Modifying existing file '{target_path_to_check}' requires 'pre_task_hash' (or 'expected_base_hash')."

    # Check durable store for active recovery status (never trust caller flags!)
    msg_id = msg.get("message_id")
    nonce = msg.get("nonce")
    store = admission_store or get_default_admission_store()
    is_recovering = False
    if msg_id and store:
        p_hash = compute_payload_hash(msg.get("body"))
        if store.is_message_recovering(msg_id, p_hash):
            is_recovering = True
    if not is_recovering and nonce and store:
        if hasattr(store, "is_nonce_recovering") and store.is_nonce_recovering(nonce, msg_id):
            is_recovering = True
        else:
            try:
                with sqlite3.connect(store.db_path, timeout=5.0) as conn:
                    conn.row_factory = sqlite3.Row
                    cur = conn.execute("SELECT state, message_id FROM seen_nonces WHERE nonce = ?", (nonce,))
                    nrow = cur.fetchone()
                    if nrow and nrow["state"] == "RECOVERED_RETRY_PENDING":
                        if not nrow["message_id"] or nrow["message_id"] == msg_id:
                            is_recovered = True
            except Exception:
                pass

    # Cryptographic Authentication & Nonce Verification
    should_record = record_nonce if record_nonce is not None else True
    auth_valid, auth_err = verify_message_auth(
        msg,
        record_nonce=should_record,
        allow_recovery=is_recovering,
        check_freshness=check_freshness,
        admission_store=store,
    )
    if not auth_valid:
        return False, auth_err

    return True, None


def invoke_antigravity_model(prompt: str, timeout_sec: int = 120, chat_only: bool = False) -> Dict[str, Any]:
    """
    Invokes Antigravity with user-authorized access after a verified checkpoint,
    or sandboxed read-only access for chat discussions.
    """
    if MODEL_DISPATCH_HOOK:
        return MODEL_DISPATCH_HOOK(prompt, timeout_sec)

    agy_bin = r"C:\Users\yashw\.gemini\bin\agy.exe"
    if not os.path.exists(agy_bin):
        return {
            "success": False,
            "output": "AGY_BIN_NOT_FOUND",
            "returncode": 1,
            "error": f"Antigravity CLI binary not found at {agy_bin}"
        }

    t0 = time.time()
    try:
        from antigravity.daemons.agent_access import prepare_dispatch, TASK_BOUNDARIES
        from antigravity.daemons.tri_agent_bus import CHAT_BOUNDARIES
        cli_flags = ["--project", "3ccee98c-0ec8-497b-a076-f86d4ef452ae", "--sandbox"] if chat_only else prepare_dispatch("ANTIGRAVITY")
        boundary = CHAT_BOUNDARIES if chat_only else TASK_BOUNDARIES
        proc = subprocess.run(
            [
                agy_bin,
                *cli_flags,
                "--disable-slash-commands",
                "--model", "gemini-3.8-flash-low",
                "-p", boundary + prompt
            ],
            cwd=WORKSPACE_DIR,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            encoding="utf-8",
            errors="replace"
        )
        elapsed = time.time() - t0
        out = proc.stdout.strip() or proc.stderr.strip()
        normalized_out = out.lower()
        permission_denial_signatures = (
            "jetski: no output produced",
            'tool required the "command" permission',
            'tool required the "read_file" permission',
            "headless mode cannot prompt",
            "so it was auto-denied",
        )
        denial = next((sig for sig in permission_denial_signatures if sig in normalized_out), None)
        success = (proc.returncode == 0) and denial is None and bool(out.strip())
        if denial:
            error = f"ANTIGRAVITY_PERMISSION_DENIED: detected '{denial}'"
        elif not out.strip():
            error = "ANTIGRAVITY_EMPTY_OUTPUT"
        elif proc.returncode != 0:
            error = f"Antigravity CLI exited with {proc.returncode}: {proc.stderr.strip()}"
        else:
            error = None
        return {
            "success": success,
            "output": out,
            "returncode": proc.returncode,
            "elapsed_sec": round(elapsed, 2),
            "error": error
        }
    except subprocess.TimeoutExpired:
        elapsed = time.time() - t0
        err = f"ERROR: Antigravity CLI timed out after {timeout_sec}s"
        return {"success": False, "output": err, "returncode": 124, "elapsed_sec": round(elapsed, 2), "error": err}
    except Exception as e:
        elapsed = time.time() - t0
        err = f"ERROR invoking Antigravity CLI: {e}"
        return {"success": False, "output": err, "returncode": 1, "elapsed_sec": round(elapsed, 2), "error": err}


class InboxWorker:
    """Worker daemon that safely monitors and processes the Antigravity inbox with per-recipient lanes."""

    def __init__(self, poll_interval_sec: float = 1.0, orphan_recovery_policy: str = "WORKER_RETRY"):
        self.poll_interval_sec = poll_interval_sec
        self.orphan_recovery_policy = orphan_recovery_policy
        self._recipient_lanes: Dict[str, threading.Lock] = {r: threading.Lock() for r in VALID_RECIPIENTS}
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=max(len(VALID_RECIPIENTS), 4))
        self._active_recipient_futures: Dict[str, concurrent.futures.Future] = {}
        self._pending_by_recipient: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
        self._intake_lock = threading.Lock()
        self._dead_letter_count_in_pass = 0
        ensure_directories()

    def intake_now(self):
        """Scans the inbox directory and admits/claims pending messages immediately upon arrival."""
        with self._intake_lock:
            try:
                inbox_entries = sorted(os.listdir(INBOX_DIR))
            except OSError:
                inbox_entries = []

            for filename in inbox_entries:
                if filename.endswith(".json") and not filename.endswith(".tmp"):
                    claim_res = self.claim_message(filename)
                    if claim_res:
                        claimed_path, msg_data = claim_res
                        recipient = msg_data.get("recipient", "ANTIGRAVITY")
                        self._pending_by_recipient.setdefault(recipient, []).append((claimed_path, msg_data))

    def recover_orphaned_claims(self, stale_threshold_sec: Optional[float] = None) -> int:
        """Scans inbox for stale .claimed files from crashed workers and recovers them."""
        ensure_directories()
        now = time.time()
        threshold = stale_threshold_sec if stale_threshold_sec is not None else CLAIM_TIMEOUT_SEC
        recovered_count = 0
        for filename in os.listdir(INBOX_DIR):
            if filename.endswith(".claimed"):
                claimed_path = os.path.join(INBOX_DIR, filename)
                try:
                    mtime = os.path.getmtime(claimed_path)
                except OSError:
                    continue

                data = None
                claim_ts = mtime
                try:
                    with open(claimed_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if "claimed_at_ts" in data and isinstance(data["claimed_at_ts"], (int, float)):
                        claim_ts = data["claimed_at_ts"]
                except Exception:
                    pass

                if (now - claim_ts) > threshold or (now - mtime) > threshold:
                    try:
                        if data is None:
                            with open(claimed_path, "r", encoding="utf-8") as f:
                                data = json.load(f)
                        worker_pid = data.get("worker_pid")
                        worker_ct = data.get("worker_create_time_nt")
                        if _pid_is_running(worker_pid, expected_create_time=worker_ct):
                            if stale_threshold_sec is None or worker_pid != os.getpid():
                                continue
                        msg_id = data.get("message_id", filename.replace(".claimed", ""))
                        corr_id = data.get("correlation_id", uuid.uuid4().hex)

                        # Reconcile terminal state first across ALL policy and attempt branches (Finding 1)
                        store = getattr(self, "db", None) or get_default_admission_store()
                        if store:
                            cur_st = store.get_message_state(msg_id)
                            if cur_st == "COMPLETED":
                                # Leftover claim from completed message: reconcile outbox response before unlinking (Finding 5)
                                outbox_file = os.path.join(OUTBOX_DIR, get_safe_filename(corr_id, "_resp.json"))
                                if not os.path.exists(outbox_file):
                                    try:
                                        with FileLock(outbox_file, timeout_sec=5.0, stale_sec=3600.0):
                                            if not os.path.exists(outbox_file):
                                                saved_resp = store.get_message_response(msg_id)
                                                if saved_resp:
                                                    try:
                                                        resp_data = json.loads(saved_resp)
                                                        write_json_atomic(outbox_file, resp_data)
                                                        logger.info("Reconciled missing outbox response for completed message %s from durable store", msg_id)
                                                    except Exception as pub_err:
                                                        logger.error("Failed to reconcile outbox response for %s: %s; preserving claim", msg_id, pub_err)
                                                        continue  # Do NOT unlink claim if publication reconciliation fails!
                                                else:
                                                    # Response cannot be retrieved from store (missing or DB error); PRESERVE claim!
                                                    logger.error("Failed to retrieve durable response for completed message %s; preserving claim", msg_id)
                                                    continue
                                    except TimeoutError:
                                        logger.warning("Outbox lock busy while reconciling completed message %s; preserving claim", msg_id)
                                        continue
                                try:
                                    os.remove(claimed_path)
                                except OSError:
                                    pass
                                continue
                            elif cur_st == "DEAD":
                                outbox_file = os.path.join(OUTBOX_DIR, get_safe_filename(corr_id, "_resp.json"))
                                if not os.path.exists(outbox_file):
                                    try:
                                        with FileLock(outbox_file, timeout_sec=5.0, stale_sec=3600.0):
                                            if not os.path.exists(outbox_file):
                                                saved_resp = store.get_message_response(msg_id)
                                                if saved_resp:
                                                    try:
                                                        resp_data = json.loads(saved_resp)
                                                        write_json_atomic(outbox_file, resp_data)
                                                        logger.info("Reconciled missing outbox response for DEAD message %s from durable store", msg_id)
                                                    except Exception as pub_err:
                                                        logger.error("Failed to reconcile outbox response for DEAD message %s: %s; preserving claim", msg_id, pub_err)
                                                        continue
                                                else:
                                                    # Reconstruct fallback failure response if key available
                                                    antigravity_key = get_agent_secret_key("ANTIGRAVITY")
                                                    if antigravity_key:
                                                        err_resp = {
                                                            "message_id": f"resp_{uuid.uuid4().hex[:12]}",
                                                            "correlation_id": corr_id,
                                                            "responder": "ANTIGRAVITY",
                                                            "route_agent": data.get("recipient"),
                                                            "status": "FAILED",
                                                            "created_at_ist": get_current_ist(),
                                                            "completed_at_ist": get_current_ist(),
                                                            "output_payload": None,
                                                            "artifact_hashes": {},
                                                            "nonce": uuid.uuid4().hex,
                                                            "error": data.get("error") or "Message marked DEAD without saved response",
                                                        }
                                                        err_resp["auth_signature"] = compute_envelope_hmac(err_resp, antigravity_key)
                                                        try:
                                                            write_json_atomic(outbox_file, err_resp)
                                                            store.mark_message_dead(msg_id, error=err_resp["error"], response_json=json.dumps(err_resp))
                                                        except Exception as rec_err:
                                                            logger.error("Failed to write reconstructed outbox response for DEAD message %s: %s; preserving claim", msg_id, rec_err)
                                                            continue
                                                    else:
                                                        logger.error("No ANTIGRAVITY key to reconcile DEAD message %s; preserving claim", msg_id)
                                                        continue
                                    except TimeoutError:
                                        logger.warning("Outbox lock busy while reconciling DEAD message %s; preserving claim", msg_id)
                                        continue

                                safe_dead = get_safe_filename(msg_id, ".dead.json")
                                dead_path = os.path.join(DEAD_LETTER_DIR, safe_dead)
                                if not os.path.exists(dead_path):
                                    data["status"] = "FAILED"
                                    if "error" not in data:
                                        data["error"] = "Message marked DEAD"
                                    try:
                                        write_json_atomic(dead_path, data)
                                    except Exception as dl_err:
                                        logger.error("Failed to write dead letter file for %s: %s; preserving claim", msg_id, dl_err)
                                        continue

                                try:
                                    os.remove(claimed_path)
                                except OSError:
                                    pass
                                continue

                        attempts = data.get("attempt_count", 0) + 1
                        data["attempt_count"] = attempts

                        if self.orphan_recovery_policy == "RETRY_REQUIRED" or attempts >= MAX_ATTEMPTS:
                            err_desc = (
                                f"RETRY_REQUIRED: Worker crashed during processing. Resend with new nonce."
                                if self.orphan_recovery_policy == "RETRY_REQUIRED"
                                else f"MAX_ATTEMPTS_EXCEEDED: Claim timed out {attempts} times."
                            )
                            # 1. Pre-construct failure response envelope
                            antigravity_key = get_agent_secret_key("ANTIGRAVITY")
                            if not antigravity_key:
                                logger.error("Antigravity gateway key unavailable for exhaustion response on %s; preserving claim envelope", msg_id)
                                continue

                            err_resp = {
                                "message_id": f"resp_{uuid.uuid4().hex[:12]}",
                                "correlation_id": corr_id,
                                "responder": "ANTIGRAVITY",
                                "route_agent": data.get("recipient"),
                                "status": "FAILED",
                                "created_at_ist": get_current_ist(),
                                "completed_at_ist": get_current_ist(),
                                "output_payload": None,
                                "artifact_hashes": {},
                                "nonce": uuid.uuid4().hex,
                                "error": err_desc,
                            }
                            err_resp["auth_signature"] = compute_envelope_hmac(err_resp, antigravity_key)
                            resp_json_str = json.dumps(err_resp)

                            # 2. Persist DEAD state with durable response_json
                            dead_persisted = True
                            if store and msg_id:
                                dead_persisted = store.mark_message_dead(msg_id, error=err_desc, response_json=resp_json_str)
                            if not dead_persisted:
                                logger.error("Failed to mark message %s dead during orphan exhaustion; preserving claim envelope", msg_id)
                                continue

                            # 3. Explicit RETRY_REQUIRED or dead-letter when max attempts reached
                            safe_dead = get_safe_filename(msg_id, ".dead.json")
                            dead_path = os.path.join(DEAD_LETTER_DIR, safe_dead)
                            data["status"] = "FAILED"
                            data["error"] = err_desc
                            try:
                                write_json_atomic(dead_path, data)
                            except Exception as dl_err:
                                logger.error("Failed to write dead letter file for %s during exhaustion: %s; preserving claim", msg_id, dl_err)
                                continue

                            # 4. Reconcile outbox response before unlinking claim
                            outbox_file = os.path.join(OUTBOX_DIR, get_safe_filename(corr_id, "_resp.json"))
                            if not os.path.exists(outbox_file):
                                try:
                                    with FileLock(outbox_file, timeout_sec=5.0, stale_sec=3600.0):
                                        if not os.path.exists(outbox_file):
                                            write_json_atomic(outbox_file, err_resp)
                                except TimeoutError:
                                    logger.warning("Outbox lock busy while writing exhaustion response for %s; preserving claim", msg_id)
                                    continue
                                except Exception as pub_err:
                                    logger.error("Failed to write outbox response for exhausted claim %s: %s; preserving claim", msg_id, pub_err)
                                    continue

                            try:
                                os.remove(claimed_path)
                            except OSError:
                                pass
                        else:
                            # WORKER_RETRY mode: Mark nonce and admission for recovery
                            nonce = data.get("nonce")
                            if store:
                                authorized = store.mark_message_recovering(
                                    msg_id,
                                    nonce=nonce,
                                    correlation_id=corr_id,
                                    payload_hash=compute_payload_hash(data.get("body")),
                                    sender=data.get("sender"),
                                    recipient=data.get("recipient"),
                                    subject=data.get("subject"),
                                    timestamp_ist=data.get("created_at_ist"),
                                )
                                if not authorized:
                                    logger.error("Failed to authorize recovery for %s (paired transition failed or incompatible nonce); preserving envelope", msg_id)
                                    continue
                            if nonce:
                                replay_store = get_default_admission_store()
                                replay_store.mark_nonce_for_recovery(nonce, msg_id)

                            # Revert back to .json to allow worker retry
                            data["status"] = "CREATED"
                            data["attempt_count"] = attempts
                            for rm_field in ["claimed_at_ist", "claimed_at_ts", "worker_pid", "worker_create_time_nt", "processing_started_at_ist", "auth_verified_at_ist"]:
                                data.pop(rm_field, None)
                            safe_revert = get_safe_filename(msg_id, ".json")
                            revert_path = os.path.join(INBOX_DIR, safe_revert)
                            write_json_atomic(revert_path, data, allow_overwrite_claimed=True)
                            try:
                                os.remove(claimed_path)
                            except OSError:
                                pass
                            recovered_count += 1
                    except Exception:
                        pass
        # Independent durable reconciliation for any dead letters with missing outbox responses
        if os.path.exists(DEAD_LETTER_DIR):
            for dl_file in os.listdir(DEAD_LETTER_DIR):
                if not dl_file.endswith(".dead.json"):
                    continue
                dl_path = os.path.join(DEAD_LETTER_DIR, dl_file)
                try:
                    with open(dl_path, "r", encoding="utf-8") as f:
                        dl_data = json.load(f)
                    dl_msg_id = dl_data.get("message_id")
                    dl_corr_id = dl_data.get("correlation_id")
                    if dl_corr_id and dl_msg_id:
                        dl_outbox = os.path.join(OUTBOX_DIR, get_safe_filename(dl_corr_id, "_resp.json"))
                        if not os.path.exists(dl_outbox):
                            try:
                                with FileLock(dl_outbox, timeout_sec=5.0, stale_sec=3600.0):
                                    if not os.path.exists(dl_outbox):
                                        store = getattr(self, "db", None) or get_default_admission_store()
                                        if store:
                                            dl_resp = store.get_message_response(dl_msg_id)
                                            if dl_resp:
                                                try:
                                                    write_json_atomic(dl_outbox, json.loads(dl_resp))
                                                    logger.info("Reconciled missing outbox response for dead letter %s from durable store", dl_msg_id)
                                                except Exception:
                                                    pass
                            except TimeoutError:
                                pass
                except Exception:
                    pass
        return recovered_count

    def claim_message(self, json_filename: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        Atomically claims a message by renaming <id>.json to <id>.claimed.
        Validates schema, timestamp freshness, and HMAC at ARRIVAL (claim time).
        Returns (claimed_filepath, message_data) or None if already claimed.
        """
        # Strictly validate filename regex before accessing filesystem
        if not re.match(r"^[a-zA-Z0-9_\-]{8,64}\.json$", json_filename):
            return None

        base_path = os.path.join(INBOX_DIR, json_filename)
        claimed_path = os.path.join(INBOX_DIR, json_filename.replace(".json", ".claimed"))

        try:
            with FileLock(base_path, timeout_sec=2.0):
                if not os.path.exists(base_path):
                    return None
                msg_id_from_file = json_filename.replace(".json", "")
                store = getattr(self, "db", None) or get_default_admission_store()
                if store:
                    cur_state = store.get_message_state(msg_id_from_file)
                    if cur_state in ("COMPLETED", "DEAD"):
                        # Terminal state: reject claim and clear redundant inbox file
                        try:
                            os.remove(base_path)
                        except OSError:
                            pass
                        return None
                    if cur_state == "ERROR":
                        # Database failure: fail closed, do not claim
                        return None
                replaced = False
                for _attempt in range(5):
                    try:
                        os.replace(base_path, claimed_path)
                        replaced = True
                        break
                    except PermissionError:
                        time.sleep(0.02)
                    except OSError:
                        return None
                if not replaced:
                    return None

                try:
                    with open(claimed_path, "r", encoding="utf-8") as f:
                        data = json.load(f)

                    # Validate schema, timestamp freshness, and HMAC at ARRIVAL (claim time)
                    is_valid, auth_err = validate_message_schema(data, admission_store=getattr(self, "db", None))
                    if not is_valid:
                        safe_dead = get_safe_filename(data.get("message_id", json_filename.replace(".json", "")), ".dead.json")
                        dead_path = os.path.join(DEAD_LETTER_DIR, safe_dead)
                        data["status"] = "FAILED"
                        data["error"] = auth_err or "CLAIM_VALIDATION_FAILED"
                        data["failed_at_ist"] = get_current_ist()
                        write_json_atomic(dead_path, data)
                        if store:
                            store.mark_message_dead(data.get("message_id", json_filename.replace(".json", "")), error=data["error"])
                        corr_id = data.get("correlation_id")
                        if corr_id:
                            outbox_file = os.path.join(OUTBOX_DIR, get_safe_filename(corr_id, "_resp.json"))
                            if not os.path.exists(outbox_file):
                                antigravity_key = get_agent_secret_key("ANTIGRAVITY")
                                if antigravity_key:
                                    err_resp = {
                                        "message_id": f"resp_{uuid.uuid4().hex[:12]}",
                                        "correlation_id": corr_id,
                                        "responder": "ANTIGRAVITY",
                                        "route_agent": data.get("recipient"),
                                        "status": "FAILED",
                                        "created_at_ist": get_current_ist(),
                                        "completed_at_ist": get_current_ist(),
                                        "output_payload": None,
                                        "artifact_hashes": {},
                                        "nonce": uuid.uuid4().hex,
                                        "error": data["error"]
                                    }
                                    err_resp["auth_signature"] = compute_envelope_hmac(err_resp, antigravity_key)
                                    write_json_atomic(outbox_file, err_resp)
                        try:
                            os.remove(claimed_path)
                        except OSError:
                            pass
                        self._dead_letter_count_in_pass += 1
                        return None

                    if data.get("status") != "CREATED":
                        safe_dead = get_safe_filename(data.get("message_id", json_filename.replace(".json", "")), ".dead.json")
                        dead_path = os.path.join(DEAD_LETTER_DIR, safe_dead)
                        data["status"] = "FAILED"
                        data["error"] = f"INVALID_STATUS: Inbound message must have status 'CREATED', got '{data.get('status')}'"
                        data["failed_at_ist"] = get_current_ist()
                        write_json_atomic(dead_path, data)
                        if store:
                            store.mark_message_dead(data.get("message_id", json_filename.replace(".json", "")), error=data["error"])
                        try:
                            os.remove(claimed_path)
                        except OSError:
                            pass
                        self._dead_letter_count_in_pass += 1
                        return None

                    data["status"] = "CLAIMED"
                    data["claimed_at_ist"] = get_current_ist()
                    data["claimed_at_ts"] = time.time()
                    data["worker_pid"] = os.getpid()
                    data["worker_create_time_nt"] = get_process_create_time_nt(os.getpid())
                    write_json_atomic(claimed_path, data)
                    if store:
                        claimed_ok = store.mark_message_claimed(
                            data.get("message_id", json_filename.replace(".json", "")),
                            correlation_id=data.get("correlation_id"),
                            payload_hash=compute_payload_hash(data.get("body")),
                            sender=data.get("sender"),
                            recipient=data.get("recipient"),
                            subject=data.get("subject"),
                            timestamp_ist=data.get("created_at_ist"),
                        )
                        if not claimed_ok:
                            # State transition failed: distinguish terminal refusal from persistence failure (Finding 2)
                            msg_id = data.get("message_id", json_filename.replace(".json", ""))
                            cur_st = store.get_message_state(msg_id)
                            if cur_st in ("COMPLETED", "DEAD"):
                                try:
                                    os.remove(claimed_path)
                                except OSError:
                                    pass
                            else:
                                # Persistence failure or active state (QUEUED, CLAIMED, RECOVERING, ERROR, None):
                                # Revert envelope to base_path with CREATED status so message is preserved in inbox!
                                data["status"] = "CREATED"
                                for rm_k in ("worker_pid", "worker_create_time_nt", "claimed_at_ist", "claimed_at_ts"):
                                    data.pop(rm_k, None)
                                write_json_atomic(claimed_path, data)
                                try:
                                    os.replace(claimed_path, base_path)
                                except OSError as rep_err:
                                    logger.error("Failed to replace claimed envelope %s back to %s: %s", claimed_path, base_path, rep_err)
                                # Restore retryability for this nonce (Finding 2)
                                nonce = data.get("nonce")
                                if nonce and store:
                                    rec_ok = store.mark_nonce_for_recovery(nonce, msg_id)
                                    if not rec_ok:
                                        logger.warning("Could not mark nonce %s for recovery on message %s", nonce, msg_id)
                                logger.error("Failed to persist CLAIMED state for message %s (cur_state=%s); preserved envelope in inbox", msg_id, cur_st)
                            return None
                    try:
                        os.utime(claimed_path, (time.time(), time.time()))
                    except OSError:
                        pass
                    return claimed_path, data
                except Exception as e:
                    # Malformed JSON in inbox
                    safe_dead = get_safe_filename(json_filename.replace(".json", ""), ".dead.json")
                    dead_path = os.path.join(DEAD_LETTER_DIR, safe_dead)
                    dead_record = {
                        "raw_file": json_filename,
                        "error": f"JSON_DECODE_ERROR: {e}",
                        "timestamp_ist": get_current_ist()
                    }
                    write_json_atomic(dead_path, dead_record)
                    if store:
                        store.mark_message_dead(json_filename.replace(".json", ""), error=f"JSON_DECODE_ERROR: {e}")
                    try:
                        os.remove(claimed_path)
                    except OSError:
                        pass
                    self._dead_letter_count_in_pass += 1
                    return None
        except TimeoutError:
            return None

    def route_to_dead_letter(
        self,
        claimed_path: str,
        msg: Dict[str, Any],
        error_msg: str,
        response_json: Optional[str] = None,
        unlink_claim: bool = False,
    ) -> bool:
        """Moves a rejected or failed message to dead_letter with full diagnostics using sanitized path.
        If unlink_claim is False, preserves claimed_path so caller can publish outbox response
        before unlinking, preventing claim loss on outbox write failure or crash.
        """
        msg_id = msg.get("message_id")
        store = getattr(self, "db", None) or get_default_admission_store()
        persisted = True
        if store and msg_id:
            persisted = store.mark_message_dead(msg_id, error=error_msg, response_json=response_json)
            if not persisted:
                logger.error("Failed to persist DEAD state for message %s in durable store; preserving envelope", msg_id)
                return False

        safe_name = get_safe_filename(msg_id, ".dead.json")
        dead_path = os.path.join(DEAD_LETTER_DIR, safe_name)
        msg["status"] = "FAILED"
        msg["error"] = error_msg
        msg["failed_at_ist"] = get_current_ist()
        try:
            write_json_atomic(dead_path, msg)
        except Exception as dl_err:
            logger.error("Failed to write dead letter file for %s: %s; preserving claimed envelope", msg_id, dl_err)
            return False

        if unlink_claim:
            try:
                os.remove(claimed_path)
            except OSError:
                pass
        return True

    def execute_task(self, msg: Dict[str, Any], claimed_path: Optional[str] = None) -> Tuple[str, Any, Dict[str, str], Optional[str]]:
        """
        Executes the message mandate with persisted PROCESSING state, directory whitelisting,
        mandatory OCC, and live model reasoning for non-deterministic tasks.
        Returns: (status, output_payload, artifact_hashes, error_message)
        """
        # Persist PROCESSING state in claimed envelope
        if claimed_path and os.path.exists(claimed_path):
            try:
                msg["status"] = "PROCESSING"
                msg["processing_started_at_ist"] = get_current_ist()
                write_json_atomic(claimed_path, msg)
            except Exception:
                pass

        subject = msg.get("subject", "").upper()
        recipient = msg.get("recipient", "ANTIGRAVITY")
        body = msg.get("body", "")
        track = msg.get("track", "SHARED")
        expected_file = msg.get("expected_response_file")
        completion_marker = msg.get("completion_marker")

        # 1. Fast-Path Deterministic Handlers
        if subject in ["PING", "HEALTH_CHECK"]:
            return "COMPLETED", {"reply": "PONG", "agent": recipient, "check": "GATEWAY_ROUTE_ONLY (Route-only ping; does not verify agent execution)", "track": track, "time": get_current_ist()}, {}, None

        if subject == "HEALTH_DEEP":
            from antigravity.daemons import tri_agent_bus as bus
            prompt = "read shared/trust/reviews.jsonl and return the review_id of the last line"
            timeout_sec = max(1, min(int(msg.get("timeout_sec", 180)), 900))
            if recipient == "CLAUDE":
                res = bus.ask_claude_detailed(prompt, timeout_sec=timeout_sec, min_chars=1, chat_only=True)
            elif recipient == "CODEX":
                res = bus.ask_codex_detailed(prompt, timeout_sec=timeout_sec, min_chars=1, chat_only=True)
            elif recipient == "ANTIGRAVITY":
                res = bus.ask_antigravity_detailed(prompt, timeout_sec=timeout_sec, min_chars=1, chat_only=True)
            else:
                return "FAILED", None, {}, f"UNKNOWN_RECIPIENT_FOR_DEEP_HEALTH: {recipient}"

            verification = bus.verify_deep_health_output(res.get("output", ""))
            if not res.get("success") or not verification.get("verified"):
                err_msg = verification.get("error") or res.get("error") or "DEEP_HEALTH_VERIFICATION_FAILED"
                return "FAILED", {
                    "agent": recipient,
                    "model_response": res.get("output"),
                    "verified": False,
                    "error": err_msg,
                    "elapsed_sec": res.get("elapsed", 0.0),
                }, {}, err_msg

            # Persist latest deep health status
            try:
                dh_path = os.path.join(MESSAGES_ROOT, "deep_health_latest.json")
                cur_dh = {}
                if os.path.exists(dh_path):
                    with open(dh_path, "r", encoding="utf-8") as f:
                        cur_dh = json.load(f)
                agents = cur_dh.get("agents", {})
                agents[recipient] = {
                    "status": "PASS",
                    "verified": True,
                    "review_id": verification.get("review_id"),
                    "elapsed_sec": round(res.get("elapsed", 0.0), 2),
                    "timestamp_ist": get_current_ist(),
                }
                all_pass = all(agents.get(a, {}).get("status") == "PASS" for a in ["CODEX", "CLAUDE", "ANTIGRAVITY"])
                cur_dh["status"] = "PASS" if all_pass else "PARTIAL"
                cur_dh["timestamp_ist"] = get_current_ist()
                cur_dh["agents"] = agents
                write_json_atomic(dh_path, cur_dh)
            except Exception:
                pass

            return "COMPLETED", {
                "agent": recipient,
                "model_response": res.get("output"),
                "verified": True,
                "review_id": verification.get("review_id"),
                "check": "HEALTH_DEEP_VERIFIED",
                "elapsed_sec": res.get("elapsed", 0.0),
            }, {}, None

        if subject == "ECHO":
            return "COMPLETED", {"echo": body}, {}, None

        if recipient in {"CLAUDE", "CODEX"}:
            # The gateway attests the CLI output; it does not impersonate the peer's
            # private signing key or claim that its interactive IDE pane was reached.
            if expected_file:
                return "INCOMPLETE", None, {}, "PEER_ARTIFACT_UNSUPPORTED: Review messages are read-only."
            from antigravity.daemons import tri_agent_bus as bus
            dispatch = bus.ask_claude_detailed if recipient == "CLAUDE" else bus.ask_codex_detailed
            prompt = (
                f"Signed Nexus message from {msg.get('sender')} to {recipient}. "
                "This dispatch is for discussion/review only: do not edit files, "
                "place orders, or dispatch other agents.\n"
                f"Subject: {msg.get('subject')}\nBody: {body}\nTrack: {track}"
            )
            timeout_sec = max(1, min(int(msg.get("timeout_sec", 300)), 900))
            dispatch_kwargs = {"timeout_sec": timeout_sec, "min_chars": 1}
            import inspect
            if "chat_only" in inspect.signature(dispatch).parameters:
                dispatch_kwargs["chat_only"] = True
            result = dispatch(prompt, **dispatch_kwargs)
            if not result.get("success"):
                return "FAILED", result.get("output"), {}, result.get("error") or "Peer CLI failed."
            return "COMPLETED", {
                "agent": recipient,
                "model_response": result.get("output"),
                "transport": "HEADLESS_CLI",
                "elapsed_sec": result.get("elapsed", 0.0),
            }, {}, None

        if subject == "INSPECT_FILE":
            valid, abs_path, err = validate_path_security(body if isinstance(body, str) else None, track)
            if not valid or not abs_path:
                return "FAILED", None, {}, err
            if not os.path.exists(abs_path):
                return "FAILED", None, {}, f"FILE_NOT_FOUND: '{abs_path}'"
            file_hash = compute_sha256(abs_path)
            return "COMPLETED", {"file": body, "sha256": file_hash, "size": os.path.getsize(abs_path)}, {body: file_hash}, None

        if subject == "WRITE_SUBMISSION":
            if not isinstance(body, dict):
                return "FAILED", None, {}, "INVALID_PAYLOAD: WRITE_SUBMISSION requires JSON dict payload."
            sub_file = body.get("target_file")
            content = body.get("content", "")
            valid, abs_path, err = validate_path_security(sub_file, track)
            if not valid or not abs_path:
                return "FAILED", None, {}, err

            # Whitelisted Submission Directory Enforcement
            norm_sub = os.path.normcase(abs_path)
            in_allowed = any(norm_sub == d or norm_sub.startswith(d + os.path.sep) for d in ALLOWED_SUBMISSION_DIRS)
            if not in_allowed:
                return "FAILED", None, {}, f"DIRECTORY_SECURITY_VIOLATION: WRITE_SUBMISSION target '{sub_file}' is not inside an allowed reviews directory."

            # Truly atomic per-file locking for edits
            with FileLock(abs_path, timeout_sec=5.0):
                # Mandatory Optimistic Concurrency Control (OCC)
                if os.path.exists(abs_path):
                    base_hash = body.get("expected_base_hash") or msg.get("pre_task_hash")
                    if not base_hash:
                        return "FAILED", None, {}, f"OCC_REQUIRED: expected_base_hash (pre_task_hash) is mandatory when modifying existing file '{sub_file}'."
                    curr_hash = compute_sha256(abs_path)
                    if curr_hash != base_hash:
                        return "CONFLICT", None, {}, f"OCC_CONFLICT: Base hash '{base_hash}' does not match disk hash '{curr_hash}'."

                    # Save timestamped backup
                    backup_name = f"{os.path.basename(abs_path)}.{int(time.time())}.bak"
                    backup_path = os.path.join(BACKUPS_DIR, backup_name)
                    with open(abs_path, "rb") as sf, open(backup_path, "wb") as df:
                        df.write(sf.read())

                # Atomic file write using temporary file + rename
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                tmp_write = abs_path + f".tmp_{uuid.uuid4().hex[:8]}"
                with open(tmp_write, "w", encoding="utf-8") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_write, abs_path)

                new_hash = compute_sha256(abs_path)
                return "COMPLETED", {"written_file": sub_file, "sha256": new_hash}, {sub_file: new_hash}, None

        # 2. Live Antigravity Model Reasoning Handler
        # Any non-fast-path subject invokes the live model
        is_chat = (subject == "CHAT" or not expected_file)
        prompt_text = f"Subject: {msg.get('subject')}\nBody: {body}\nTrack: {track}"
        timeout_sec = max(1, min(int(msg.get("timeout_sec", 300)), 900))
        model_result = invoke_antigravity_model(prompt_text, timeout_sec, chat_only=is_chat)

        if not model_result.get("success"):
            return "FAILED", model_result.get("output"), {}, model_result.get("error") or "Model invocation failed."

        resp_payload = {
            "agent": "ANTIGRAVITY",
            "model_response": model_result.get("output"),
            "elapsed_sec": model_result.get("elapsed_sec", 0.0)
        }
        artifact_hashes = {}

        # Completion Verification: Check if expected_response_file was fulfilled
        if expected_file:
            valid, abs_exp, err = validate_path_security(expected_file, track)
            if not valid or not abs_exp or not os.path.exists(abs_exp):
                return "INCOMPLETE", resp_payload, {}, f"EXPECTED_ARTIFACT_MISSING: '{expected_file}' not found."
            file_hash = compute_sha256(abs_exp)
            if not file_hash or os.path.getsize(abs_exp) == 0:
                return "INCOMPLETE", resp_payload, {}, f"EXPECTED_ARTIFACT_EMPTY: '{expected_file}' is empty."
            artifact_hashes[expected_file] = file_hash

            if completion_marker:
                try:
                    with open(abs_exp, "r", encoding="utf-8", errors="replace") as f:
                        file_text = f.read()
                    if completion_marker not in file_text:
                        return "INCOMPLETE", resp_payload, artifact_hashes, f"COMPLETION_MARKER_MISSING: '{completion_marker}' not in {expected_file}."
                except Exception as e:
                    return "INCOMPLETE", resp_payload, artifact_hashes, f"FILE_READ_ERROR: {e}"

        return "COMPLETED", resp_payload, artifact_hashes, None

    # Backward compatibility / testing alias
    _execute_task_payload = execute_task

    def _process_message_locked(self, claimed_path: str, msg: Dict[str, Any]):
        """Executes message, writes outbox response, and archives message safely."""
        corr_id = msg.get("correlation_id", uuid.uuid4().hex)
        safe_corr_name = get_safe_filename(corr_id, "_resp.json")
        outbox_file = os.path.join(OUTBOX_DIR, safe_corr_name)
        antigravity_key = get_agent_secret_key("ANTIGRAVITY")
        if not antigravity_key:
            raise RuntimeError("Antigravity gateway signing key is unavailable")
        route_agent = msg.get("recipient")

        # An invalid second request must not overwrite an already signed reply.
        # Check before schema validation, whose failure also emits a response.
        if os.path.exists(outbox_file):
            self.route_to_dead_letter(claimed_path, msg, f"CORRELATION_ID_COLLISION: Response already exists for '{corr_id}'", unlink_claim=True)
            return

        # 1. Schema, Identifier, and Authentication Validation (already admitted; check HMAC & schema)
        is_valid, schema_err = validate_message_schema(msg, record_nonce=False, check_freshness=False)
        if not is_valid:
            err_resp = {
                "message_id": f"resp_{uuid.uuid4().hex[:12]}",
                "correlation_id": corr_id,
                "responder": "ANTIGRAVITY",
                "route_agent": route_agent,
                "status": "FAILED",
                "created_at_ist": get_current_ist(),
                "completed_at_ist": get_current_ist(),
                "output_payload": None,
                "artifact_hashes": {},
                "nonce": uuid.uuid4().hex,
                "error": schema_err
            }
            err_resp["auth_signature"] = compute_envelope_hmac(err_resp, antigravity_key)
            resp_json_str = json.dumps(err_resp)

            persisted_dead = self.route_to_dead_letter(claimed_path, msg, schema_err or "SCHEMA_VALIDATION_FAILED", response_json=resp_json_str, unlink_claim=False)
            if persisted_dead:
                try:
                    write_json_atomic(outbox_file, err_resp)
                except Exception as out_err:
                    logger.error("Failed to write outbox response for invalid schema message %s: %s; claim preserved for recovery", msg.get("message_id"), out_err)
                    return
                try:
                    os.remove(claimed_path)
                except OSError:
                    pass
            else:
                logger.error("Failed to persist DEAD state for invalid schema message %s; preserving envelope and suppressing outbox reply", msg.get("message_id"))
            return

        # 2. Check for Permission Seeking / Hedging in Body (Warning only; do not dead-letter)
        body_text = str(msg.get("body", ""))
        if PERMISSION_SEEKING_REGEX.search(body_text):
            logger.warning(
                "Message %s contains conversational permission-seeking phrasing instead of executable task mandate.",
                msg.get("message_id")
            )

        # 3. Execute Task
        status, payload, artifact_hashes, error_msg = self.execute_task(msg, claimed_path)

        # 4. Persist Lifecycle State Before Publishing Response (Finding 3)
        msg_id = msg.get("message_id")
        nonce = msg.get("nonce")
        store = getattr(self, "db", None) or get_default_admission_store()

        if status == "COMPLETED":
            resp_envelope = {
                "message_id": f"resp_{uuid.uuid4().hex[:12]}",
                "correlation_id": corr_id,
                "responder": "ANTIGRAVITY",
                "route_agent": route_agent,
                "status": "COMPLETED",
                "created_at_ist": get_current_ist(),
                "completed_at_ist": get_current_ist(),
                "output_payload": payload,
                "artifact_hashes": artifact_hashes,
                "nonce": uuid.uuid4().hex,
                "error": None
            }
            resp_envelope["auth_signature"] = compute_envelope_hmac(resp_envelope, antigravity_key)
            resp_json_str = json.dumps(resp_envelope)

            comp_ok = True
            if store and msg_id:
                comp_ok = store.mark_message_completed(msg_id, response_json=resp_json_str)

            if not comp_ok:
                logger.error("Failed to persist COMPLETED state for message %s in durable store; routing to dead-letter", msg_id)
                if nonce and store:
                    store.mark_nonce_failed(nonce, msg_id)
                err_resp = {
                    "message_id": f"resp_{uuid.uuid4().hex[:12]}",
                    "correlation_id": corr_id,
                    "responder": "ANTIGRAVITY",
                    "route_agent": route_agent,
                    "status": "FAILED",
                    "created_at_ist": get_current_ist(),
                    "completed_at_ist": get_current_ist(),
                    "output_payload": None,
                    "artifact_hashes": {},
                    "nonce": uuid.uuid4().hex,
                    "error": "COMPLETION_PERSISTENCE_FAILED"
                }
                err_resp["auth_signature"] = compute_envelope_hmac(err_resp, antigravity_key)
                resp_json_str = json.dumps(err_resp)

                persisted_dead = self.route_to_dead_letter(claimed_path, msg, "COMPLETION_PERSISTENCE_FAILED", response_json=resp_json_str, unlink_claim=False)
                if persisted_dead:
                    try:
                        write_json_atomic(outbox_file, err_resp)
                    except Exception as out_err:
                        logger.error("Failed to write outbox response for completion failure %s: %s; claim preserved for recovery", msg_id, out_err)
                        return
                    try:
                        os.remove(claimed_path)
                    except OSError:
                        pass
                else:
                    logger.error("Failed to persist DEAD state for message %s after completion persistence failure; leaving claimed envelope intact and suppressing outbox reply", msg_id)
                return

            if nonce and store:
                store.mark_nonce_completed(nonce, msg_id)

            try:
                write_json_atomic(outbox_file, resp_envelope)
            except Exception as out_err:
                logger.error("Failed to write outbox response for %s: %s; leaving claimed envelope intact for recovery reconciliation", msg_id, out_err)
                return

            msg["status"] = "COMPLETED"
            msg["completed_at_ist"] = resp_envelope["completed_at_ist"]
            safe_archive_name = get_safe_filename(msg_id, ".json")
            archive_path = os.path.join(ARCHIVE_DIR, safe_archive_name)
            try:
                write_json_atomic(archive_path, msg)
            except Exception:
                pass
            try:
                os.remove(claimed_path)
            except OSError:
                pass
        else:
            if nonce and store:
                store.mark_nonce_failed(nonce, msg_id)
            resp_envelope = {
                "message_id": f"resp_{uuid.uuid4().hex[:12]}",
                "correlation_id": corr_id,
                "responder": "ANTIGRAVITY",
                "route_agent": route_agent,
                "status": status,
                "created_at_ist": get_current_ist(),
                "completed_at_ist": get_current_ist(),
                "output_payload": payload,
                "artifact_hashes": artifact_hashes,
                "nonce": uuid.uuid4().hex,
                "error": error_msg
            }
            resp_envelope["auth_signature"] = compute_envelope_hmac(resp_envelope, antigravity_key)
            resp_json_str = json.dumps(resp_envelope)

            persisted_dead = self.route_to_dead_letter(claimed_path, msg, error_msg or f"TASK_{status}", response_json=resp_json_str, unlink_claim=False)
            if persisted_dead:
                try:
                    write_json_atomic(outbox_file, resp_envelope)
                except Exception as out_err:
                    logger.error("Failed to write outbox response for failed task %s: %s; claim preserved for recovery", msg_id, out_err)
                    return
                try:
                    os.remove(claimed_path)
                except OSError:
                    pass
            else:
                logger.error("Failed to persist DEAD state for message %s in task failure; leaving claimed envelope intact and suppressing outbox reply", msg_id)

    def process_message(self, claimed_path: str, msg: Dict[str, Any]):
        """Serialize requests sharing a correlation ID across worker processes."""
        corr_id = msg.get("correlation_id", "")
        outbox_file = os.path.join(OUTBOX_DIR, get_safe_filename(corr_id, "_resp.json"))
        try:
            with FileLock(outbox_file, timeout_sec=5.0, stale_sec=3600.0):
                self._process_message_locked(claimed_path, msg)
        except TimeoutError:
            self.route_to_dead_letter(claimed_path, msg, "CORRELATION_LOCK_BUSY", unlink_claim=True)

    def _execute_in_lane(self, claimed_path: str, msg_data: Dict[str, Any]):
        recipient = msg_data.get("recipient", "ANTIGRAVITY")
        lane_lock = self._recipient_lanes.setdefault(recipient, threading.Lock())
        with lane_lock:
            self.process_message(claimed_path, msg_data)

    def run_single_pass(self) -> int:
        """Processes all pending messages in inbox with continuous intake and isolated recipient queues."""
        _register_active_worker(self)
        try:
            self.recover_orphaned_claims()
            processed_count = 0
            self._dead_letter_count_in_pass = 0

            while True:
                # 1. Continuous Intake: claim all pending inbox files immediately upon arrival
                self.intake_now()

                # 2. Dispatch next pending message for any idle recipient lane
                with self._intake_lock:
                    for recipient in list(self._pending_by_recipient.keys()):
                        queue = self._pending_by_recipient.get(recipient, [])
                        if queue and recipient not in self._active_recipient_futures:
                            claimed_path, msg_data = queue.pop(0)
                            fut = self._executor.submit(self._execute_in_lane, claimed_path, msg_data)
                            self._active_recipient_futures[recipient] = fut
                            processed_count += 1

                    # Prune empty queues
                    self._pending_by_recipient = {r: q for r, q in self._pending_by_recipient.items() if q}

                    has_active = bool(self._active_recipient_futures)
                    has_pending = bool(self._pending_by_recipient)

                # 3. If no active work is running and no work is queued, check if any new file landed in inbox
                if not has_active and not has_pending:
                    try:
                        remaining_inbox = [f for f in os.listdir(INBOX_DIR) if f.endswith(".json") and not f.endswith(".tmp")]
                    except OSError:
                        remaining_inbox = []
                    if not remaining_inbox:
                        break
                    continue

                # 4. Wait for active tasks with short timeout (0.02s) so new arrivals are continuously admitted
                active_futs = list(self._active_recipient_futures.values())
                if active_futs:
                    done, _ = concurrent.futures.wait(
                        active_futs,
                        timeout=0.02,
                        return_when=concurrent.futures.FIRST_COMPLETED
                    )
                    for fut in done:
                        for r, f in list(self._active_recipient_futures.items()):
                            if f == fut:
                                del self._active_recipient_futures[r]
                                try:
                                    fut.result()
                                except Exception as e:
                                    logger.error("Error executing task in lane %s: %s", r, e)
                                break
                else:
                    time.sleep(0.01)

            return processed_count + self._dead_letter_count_in_pass
        finally:
            _unregister_active_worker(self)

    def run_daemon(self):
        """Continuously polls the inbox directory."""
        print(f"[{get_current_ist()}] Antigravity Inbox Worker started. Monitoring: {INBOX_DIR}")
        while True:
            try:
                self.run_single_pass()
            except Exception as e:
                print(f"Error in inbox worker loop: {e}", file=sys.stderr)
            time.sleep(self.poll_interval_sec)


if __name__ == "__main__":
    worker = InboxWorker()
    if "--single-pass" in sys.argv:
        count = worker.run_single_pass()
        print(f"Processed {count} messages in single pass.")
    else:
        worker.run_daemon()
