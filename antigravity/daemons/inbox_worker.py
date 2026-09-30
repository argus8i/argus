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
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple, Callable

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


def _pid_is_running(pid: object) -> bool:
    """Fail closed when process liveness cannot be established."""
    if type(pid) is not int or pid <= 0:
        return True
    if pid == os.getpid():
        return True
    try:
        if os.name == "nt":
            import ctypes
            kernel = ctypes.windll.kernel32
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                # Access denied is not proof that the process exited.
                return ctypes.GetLastError() != 87
            try:
                exit_code = ctypes.c_ulong()
                if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return True
                return exit_code.value == 259  # STILL_ACTIVE
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

VALID_SENDERS = {"CLAUDE", "CODEX", "ANTIGRAVITY", "USER"}
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


def write_json_atomic(filepath: str, data: Dict[str, Any]):
    """Writes a dictionary to JSON atomically using a temporary file and replace."""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    tmp_path = filepath + f".tmp_{uuid.uuid4().hex[:8]}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    for attempt in range(5):
        try:
            os.replace(tmp_path, filepath)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.02)


class FileLock:
    """
    Truly atomic per-file mutual exclusion using OS-level O_CREAT | O_EXCL.
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
                    "acquired_at": time.time(),
                    "target": self.target_path
                }).encode("utf-8")
                os.write(self.fd, lock_data)
                return True
            except FileExistsError:
                if self._break_stale_lock():
                    continue
                if (time.time() - t0) >= self.timeout_sec:
                    return False
                time.sleep(0.05)
            except OSError:
                if (time.time() - t0) >= self.timeout_sec:
                    return False
                time.sleep(0.05)

    def _break_stale_lock(self) -> bool:
        try:
            mtime = os.path.getmtime(self.lock_path)
            with open(self.lock_path, "r", encoding="utf-8") as f:
                owner = json.load(f)
            pid = owner.get("pid")
            if not _pid_is_running(pid):
                try:
                    os.remove(self.lock_path)
                    return True
                except OSError:
                    pass
            elif (time.time() - mtime) > self.stale_sec:
                try:
                    os.remove(self.lock_path)
                    return True
                except OSError:
                    pass
        except OSError:
            pass
        return False

    def release(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
        try:
            if os.path.exists(self.lock_path):
                os.remove(self.lock_path)
        except OSError:
            pass

    def __enter__(self):
        if not self.acquire():
            raise TimeoutError(f"Could not acquire lock for '{self.target_path}' within {self.timeout_sec}s")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


class DurableReplayStore:
    """
    Durable, SQLite-backed nonce replay prevention store with WAL mode.
    Guarantees replay rejection persists across worker crashes and restarts.
    """
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.path.join(MESSAGES_ROOT, "replay_store.db")
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._init_db()

    def _init_db(self):
        try:
            with sqlite3.connect(self.db_path, timeout=10.0) as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS seen_nonces (
                        nonce TEXT PRIMARY KEY,
                        sender TEXT NOT NULL,
                        timestamp_ist TEXT NOT NULL,
                        recorded_at REAL NOT NULL
                    );
                """)
                conn.commit()
        except Exception:
            pass

    def check_and_record_nonce(self, nonce: str, sender: str, timestamp_ist: str, ttl_sec: float = 600.0) -> Tuple[bool, Optional[str]]:
        now = time.time()
        try:
            with sqlite3.connect(self.db_path, timeout=10.0) as conn:
                conn.execute("DELETE FROM seen_nonces WHERE recorded_at < ?", (now - ttl_sec,))
                conn.execute(
                    "INSERT INTO seen_nonces (nonce, sender, timestamp_ist, recorded_at) VALUES (?, ?, ?, ?)",
                    (nonce, sender, timestamp_ist, now)
                )
                conn.commit()
            return True, None
        except sqlite3.IntegrityError:
            return False, f"REPLAY_ATTACK: Nonce '{nonce}' has already been processed."
        except Exception as e:
            return False, f"REPLAY_STORE_ERROR: {e}"


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


RUNTIME_METADATA_FIELDS = {"auth_signature", "claimed_at_ist", "worker_pid", "processing_started_at_ist"}


def canonicalize_envelope(envelope: Dict[str, Any]) -> str:
    """
    Deterministically serializes envelope fields for HMAC signing and verification:
    - Excludes 'auth_signature' and worker runtime tracking fields.
    - Ignores None values so omitted vs null fields are canonicalized identically.
    - Normalizes request status so worker claim transitions do not break sender signatures.
    - Keys are strictly sorted with normalized separators (',', ':').
    """
    d = {}
    for k, v in envelope.items():
        if k in RUNTIME_METADATA_FIELDS or v is None:
            continue
        if k == "status" and envelope.get("recipient") in VALID_RECIPIENTS and v in ["CLAIMED", "PROCESSING"]:
            d[k] = "CREATED"
        else:
            d[k] = v
    return json.dumps(d, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def compute_envelope_hmac(envelope: Dict[str, Any], secret_key: str) -> str:
    """Computes HMAC-SHA256 over all canonical envelope fields."""
    canonical = canonicalize_envelope(envelope)
    return hmac.new(secret_key.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def compute_message_hmac(msg: Dict[str, Any], secret_key: str) -> str:
    """Computes HMAC over all envelope fields (calls compute_envelope_hmac)."""
    return compute_envelope_hmac(msg, secret_key)


def verify_message_auth(msg: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Validates message HMAC-SHA256 signature, durable nonce uniqueness, and timestamp freshness."""
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

    # Durable SQLite Nonce Replay Check
    replay_store = DurableReplayStore(os.path.join(MESSAGES_ROOT, "replay_store.db"))
    nonce_ok, nonce_err = replay_store.check_and_record_nonce(nonce, sender, msg.get("created_at_ist", ""))
    if not nonce_ok:
        return False, nonce_err

    # Timestamp Freshness Check
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
            return False, f"TIMESTAMP_OUT_OF_BOUNDS: Message expired ({delta_sec:.1f}s old > {val_sec}s limit)."
        if delta_sec < -skew_sec:
            return False, f"TIMESTAMP_OUT_OF_BOUNDS: Message timestamp is in the future by {-delta_sec:.1f}s (> {skew_sec}s limit)."
    except Exception as e:
        return False, f"TIMESTAMP_FORMAT_ERROR: Unable to parse created_at_ist '{ts_str}': {e}"

    # HMAC Verification across all envelope fields
    expected_sig = compute_envelope_hmac(msg, secret_key)
    if not hmac.compare_digest(sig, expected_sig):
        return False, "AUTH_FAILED: Cryptographic HMAC signature verification failed."

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

    base_ws = workspace_dir or WORKSPACE_DIR

    # Resolve absolute path
    if os.path.isabs(path_str):
        resolved = os.path.abspath(path_str)
    else:
        resolved = os.path.abspath(os.path.join(base_ws, path_str))

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



def validate_message_schema(msg: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
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

    # Cryptographic Authentication & Nonce Verification
    auth_valid, auth_err = verify_message_auth(msg)
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
    """Worker daemon that safely monitors and processes the Antigravity inbox."""

    def __init__(self, poll_interval_sec: float = 1.0):
        self.poll_interval_sec = poll_interval_sec
        ensure_directories()

    def recover_orphaned_claims(self):
        """Scans inbox for stale .claimed files from crashed workers and recovers them."""
        ensure_directories()
        now = time.time()
        for filename in os.listdir(INBOX_DIR):
            if filename.endswith(".claimed"):
                claimed_path = os.path.join(INBOX_DIR, filename)
                mtime = os.path.getmtime(claimed_path)
                if (now - mtime) > CLAIM_TIMEOUT_SEC:
                    try:
                        with open(claimed_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        if _pid_is_running(data.get("worker_pid")):
                            continue
                        attempts = data.get("attempt_count", 0) + 1
                        data["attempt_count"] = attempts
                        if attempts >= MAX_ATTEMPTS:
                            # Too many failures, move to dead letter using safe name
                            safe_dead = get_safe_filename(data.get("message_id", filename), ".dead.json")
                            dead_path = os.path.join(DEAD_LETTER_DIR, safe_dead)
                            data["status"] = "FAILED"
                            data["error"] = f"MAX_ATTEMPTS_EXCEEDED: Claim timed out {attempts} times."
                            write_json_atomic(dead_path, data)
                            os.remove(claimed_path)
                        else:
                            # Revert back to .json to allow retry
                            data["status"] = "CREATED"
                            safe_revert = get_safe_filename(data.get("message_id", filename), ".json")
                            revert_path = os.path.join(INBOX_DIR, safe_revert)
                            write_json_atomic(revert_path, data)
                            os.remove(claimed_path)
                    except Exception:
                        pass

    def claim_message(self, json_filename: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        Atomically claims a message by renaming <id>.json to <id>.claimed.
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
                    if data.get("status") != "CREATED":
                        return claimed_path, data
                    data["status"] = "CLAIMED"
                    data["claimed_at_ist"] = get_current_ist()
                    data["worker_pid"] = os.getpid()
                    write_json_atomic(claimed_path, data)
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
                    try:
                        os.remove(claimed_path)
                    except OSError:
                        pass
                    return None
        except TimeoutError:
            return None

    def route_to_dead_letter(self, claimed_path: str, msg: Dict[str, Any], error_msg: str):
        """Moves a rejected or failed message to dead_letter with full diagnostics using sanitized path."""
        msg_id = msg.get("message_id")
        safe_name = get_safe_filename(msg_id, ".dead.json")
        dead_path = os.path.join(DEAD_LETTER_DIR, safe_name)
        msg["status"] = "FAILED"
        msg["error"] = error_msg
        msg["failed_at_ist"] = get_current_ist()
        write_json_atomic(dead_path, msg)
        try:
            os.remove(claimed_path)
        except OSError:
            pass

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
            return "COMPLETED", {"reply": "PONG", "agent": recipient, "check": "GATEWAY_ROUTE_ONLY", "track": track, "time": get_current_ist()}, {}, None

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
        timeout_sec = int(msg.get("timeout_sec", 300))
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
            self.route_to_dead_letter(claimed_path, msg, f"CORRELATION_ID_COLLISION: Response already exists for '{corr_id}'")
            return

        # 1. Schema, Identifier, and Authentication Validation
        is_valid, schema_err = validate_message_schema(msg)
        if not is_valid:
            self.route_to_dead_letter(claimed_path, msg, schema_err or "SCHEMA_VALIDATION_FAILED")
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
            write_json_atomic(outbox_file, err_resp)
            return

        # 2. Check for Permission Seeking / Hedging in Body
        body_text = str(msg.get("body", ""))
        if PERMISSION_SEEKING_REGEX.search(body_text):
            err_resp = {
                "message_id": f"resp_{uuid.uuid4().hex[:12]}",
                "correlation_id": corr_id,
                "responder": "ANTIGRAVITY",
                "route_agent": route_agent,
                "status": "INCOMPLETE",
                "created_at_ist": get_current_ist(),
                "completed_at_ist": get_current_ist(),
                "output_payload": body_text,
                "artifact_hashes": {},
                "nonce": uuid.uuid4().hex,
                "error": "INCOMPLETE_REQUEST: Prompt contains conversational permission-seeking instead of executable task mandate."
            }
            err_resp["auth_signature"] = compute_envelope_hmac(err_resp, antigravity_key)
            write_json_atomic(outbox_file, err_resp)
            self.route_to_dead_letter(claimed_path, msg, err_resp["error"])
            return

        # 3. Execute Task
        status, payload, artifact_hashes, error_msg = self.execute_task(msg, claimed_path)

        # 4. Write Outbox Response Envelope with Full HMAC Signature
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
        write_json_atomic(outbox_file, resp_envelope)

        # 5. Archive or Dead-Letter
        msg_id = msg.get("message_id")
        if status == "COMPLETED":
            msg["status"] = "COMPLETED"
            msg["completed_at_ist"] = resp_envelope["completed_at_ist"]
            safe_archive_name = get_safe_filename(msg_id, ".json")
            archive_path = os.path.join(ARCHIVE_DIR, safe_archive_name)
            write_json_atomic(archive_path, msg)
            try:
                os.remove(claimed_path)
            except OSError:
                pass
        else:
            self.route_to_dead_letter(claimed_path, msg, error_msg or f"TASK_{status}")

    def process_message(self, claimed_path: str, msg: Dict[str, Any]):
        """Serialize requests sharing a correlation ID across worker processes."""
        corr_id = msg.get("correlation_id", "")
        outbox_file = os.path.join(OUTBOX_DIR, get_safe_filename(corr_id, "_resp.json"))
        try:
            with FileLock(outbox_file, timeout_sec=5.0, stale_sec=3600.0):
                self._process_message_locked(claimed_path, msg)
        except TimeoutError:
            self.route_to_dead_letter(claimed_path, msg, "CORRELATION_LOCK_BUSY")

    def run_single_pass(self) -> int:
        """Processes all currently pending messages in the inbox once. Returns count processed."""
        self.recover_orphaned_claims()
        processed = 0
        for filename in sorted(os.listdir(INBOX_DIR)):
            if filename.endswith(".json") and not filename.endswith(".tmp"):
                claim_res = self.claim_message(filename)
                if claim_res:
                    claimed_path, msg_data = claim_res
                    self.process_message(claimed_path, msg_data)
                    processed += 1
        return processed

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
