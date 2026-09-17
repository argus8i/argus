"""
tri_agent_bus.py - Direct Local Inter-Agent Communication Bus
Connects Antigravity, Claude Code, and OpenAI Codex / ChatGPT directly on Yashu's machine.
Eliminates manual copy-pasting between chat windows.
"""

import subprocess
import os
import sys
import time
import json
import glob
import uuid
import hashlib
import hmac
import re
from datetime import datetime
from typing import Any, Dict, Optional, Tuple, List

# Ensure antigravity package is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from antigravity.daemons.inbox_worker import (
    MESSAGES_ROOT,
    INBOX_DIR,
    OUTBOX_DIR,
    ARCHIVE_DIR,
    DEAD_LETTER_DIR,
    BACKUPS_DIR,
    ensure_directories,
    write_json_atomic,
    compute_sha256,
    get_current_ist,
    validate_path_security,
    PERMISSION_SEEKING_REGEX,
    IDENTIFIER_REGEX,
    compute_message_hmac,
    compute_envelope_hmac,
    canonicalize_envelope,
    load_auth_config,
    get_agent_secret_key,
    get_safe_filename,
    FileLock,
    DurableReplayStore,
    InboxWorker
)

# Force UTF-8 encoding on Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

def get_claude_bin() -> str:
    """Dynamically resolves the latest installed Claude Code executable."""
    pattern = os.path.expanduser(r"~/.antigravity-ide/extensions/anthropic.claude-code-*/resources/native-binary/claude.exe")
    matches = sorted(glob.glob(pattern), reverse=True)
    if matches and os.path.exists(matches[0]):
        return matches[0]
    return r"c:\Users\yashw\.antigravity-ide\extensions\anthropic.claude-code-2.1.269-win32-x64\resources\native-binary\claude.exe"


def get_codex_bin() -> str:
    """Dynamically resolves the latest installed OpenAI Codex executable."""
    pattern = os.path.expanduser(r"~/.antigravity-ide/extensions/openai.chatgpt-*/bin/windows-x86_64/codex.exe")
    matches = sorted(glob.glob(pattern), reverse=True)
    if matches and os.path.exists(matches[0]):
        return matches[0]
    return r"c:\Users\yashw\.antigravity-ide\extensions\openai.chatgpt-26.721.30844-win32-x64\bin\windows-x86_64\codex.exe"


def get_antigravity_bin() -> str:
    """Resolves official Antigravity CLI executable."""
    candidates = [
        r"C:\Users\yashw\.gemini\bin\agy.exe",
        os.path.expanduser(r"~/.gemini/bin/agy.exe"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return r"C:\Users\yashw\.gemini\bin\agy.exe"


CLAUDE_BIN = get_claude_bin()
CODEX_BIN = get_codex_bin()
AGY_BIN = get_antigravity_bin()
WORKSPACE = r"c:\Users\yashw\swing trades"
LOGS_DIR = os.path.join(WORKSPACE, "antigravity", "logs")
DIALOGUE_MD = os.path.join(LOGS_DIR, "tri_agent_dialogue.md")
DIALOGUE_JSONL = os.path.join(LOGS_DIR, "tri_agent_dialogue.jsonl")


def log_interaction(recipient: str, prompt: str, response: str, elapsed_sec: float, exit_code: int = 0):
    """Permanently logs all inter-agent communications for Yashu to inspect in real time."""
    os.makedirs(LOGS_DIR, exist_ok=True)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")
    
    # 1. Structured JSONL Log
    record = {
        "timestamp": now_str,
        "sender": "Antigravity",
        "recipient": recipient,
        "elapsed_seconds": round(elapsed_sec, 2),
        "exit_code": exit_code,
        "prompt": prompt,
        "response": response
    }
    try:
        with open(DIALOGUE_JSONL, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"Warning: Failed to write to {DIALOGUE_JSONL}: {e}")

    # 2. Human-Readable Markdown Log
    md_entry = f"""
## [{now_str}] Antigravity ➔ {recipient} ({elapsed_sec:.1f}s)

**Prompt / Mandate:**
```text
{prompt.strip()}
```

**{recipient} Output & Audit Verdict:**
```markdown
{response.strip()}
```

---
"""
    try:
        if not os.path.exists(DIALOGUE_MD):
            header = "# Tri-Agent Communications & Audit Log\n\nTransparent real-time audit ledger of all messages, queries, and peer reviews exchanged between Antigravity, Claude Code, and OpenAI Codex.\n\n---\n"
            with open(DIALOGUE_MD, "w", encoding="utf-8") as f:
                f.write(header)
        with open(DIALOGUE_MD, "a", encoding="utf-8") as f:
            f.write(md_entry)
    except Exception as e:
        print(f"Warning: Failed to write to {DIALOGUE_MD}: {e}")


# Reviewer CLIs report some hard failures on stdout while exiting 0. Claude Code
# prints "Failed to authenticate: OAuth session expired..." and exits 0; treating
# returncode as the success signal turns that string into a signed review.
REVIEWER_FAILURE_SIGNATURES = (
    "failed to authenticate",
    "oauth session expired",
    "not logged in",
    "please run /login",
    "authentication required",
    "invalid api key",
    "credit balance is too low",
    "usage limit reached",
    "rate limit",
    "stream error",
)

# Anything shorter than this is not a review, whatever the exit code said.
MIN_REVIEW_CHARS = 40


def validate_reviewer_output(
    agent: str,
    output: str,
    returncode: int,
    min_chars: int = MIN_REVIEW_CHARS,
) -> Optional[str]:
    """Return an error string if this output must not be accepted as a review.

    A reviewer's answer is only usable when the process succeeded AND said
    something. Exit code alone is not evidence of either.
    """
    if returncode != 0:
        return f"{agent}_NONZERO_EXIT: exited {returncode}"

    text = (output or "").strip()
    if not text:
        return f"{agent}_EMPTY_OUTPUT: exited 0 but produced nothing"

    low = text.lower()
    for sig in REVIEWER_FAILURE_SIGNATURES:
        if sig in low:
            return f"{agent}_DISPATCH_FAILED: {text[:200]}"

    if len(text) < min_chars:
        return f"{agent}_OUTPUT_TOO_SHORT: {len(text)} chars, need >= {min_chars}"

    return None


def ask_claude_detailed(prompt: str, timeout_sec: int = 300, min_chars: int = MIN_REVIEW_CHARS) -> Dict[str, Any]:
    """Invokes Claude Code non-interactively in the workspace with structured returncode tracking."""
    if not os.path.exists(CLAUDE_BIN):
        err = f"ERROR: Claude binary not found at {CLAUDE_BIN}"
        log_interaction("Claude Code", prompt, err, 0.0, 1)
        return {"success": False, "output": err, "returncode": 1, "elapsed": 0.0, "error": err}
    t0 = time.time()
    try:
        proc = subprocess.run(
            [CLAUDE_BIN, "-p", prompt,
             # Reviewers are read-only. The adapter writes the submission file.
             "--allowedTools", "Read,Grep,Glob",
             "--disallowedTools", "Write,Edit,NotebookEdit,Bash"],
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            input="",
            timeout=timeout_sec,
            encoding="utf-8",
            errors="replace"
        )
        elapsed = time.time() - t0
        res = proc.stdout.strip() or proc.stderr.strip()
        log_interaction("Claude Code", prompt, res, elapsed, proc.returncode)
        failure = validate_reviewer_output("CLAUDE", res, proc.returncode, min_chars)
        return {
            "success": failure is None,
            "output": res,
            "returncode": proc.returncode,
            "elapsed": elapsed,
            "error": failure
        }
    except subprocess.TimeoutExpired:
        elapsed = time.time() - t0
        err = f"ERROR: Claude timed out after {timeout_sec}s"
        log_interaction("Claude Code", prompt, err, elapsed, 124)
        return {"success": False, "output": err, "returncode": 124, "elapsed": elapsed, "error": err}
    except Exception as e:
        elapsed = time.time() - t0
        err = f"ERROR invoking Claude: {e}"
        log_interaction("Claude Code", prompt, err, elapsed, 1)
        return {"success": False, "output": err, "returncode": 1, "elapsed": elapsed, "error": err}


def ask_claude(prompt: str, timeout_sec: int = 180) -> str:
    """Invokes Claude Code and returns raw output string."""
    return ask_claude_detailed(prompt, timeout_sec)["output"]


def ask_codex_detailed(prompt: str, timeout_sec: int = 300, min_chars: int = MIN_REVIEW_CHARS) -> Dict[str, Any]:
    """Invokes OpenAI Codex / ChatGPT non-interactively with structured returncode tracking."""
    if not os.path.exists(CODEX_BIN):
        err = f"ERROR: Codex binary not found at {CODEX_BIN}"
        log_interaction("OpenAI Codex", prompt, err, 0.0, 1)
        return {"success": False, "output": err, "returncode": 1, "elapsed": 0.0, "error": err}
    t0 = time.time()
    try:
        proc = subprocess.run(
            [CODEX_BIN, "exec", "--skip-git-repo-check", "--ephemeral",
             # Reviewers are read-only. The adapter writes the submission file.
             "--sandbox", "read-only", "-"],
            input=prompt,
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            encoding="utf-8",
            errors="replace"
        )
        elapsed = time.time() - t0
        out = proc.stdout.strip()
        # Cleanly extract the agent's message between "codex" and "tokens used" if present
        cleaned_res = out
        if "\ncodex\n" in out:
            parts = out.split("\ncodex\n", 1)[1]
            if "\ntokens used\n" in parts:
                cleaned_res = parts.split("\ntokens used\n", 1)[0].strip()
            else:
                cleaned_res = parts.strip()
        final_res = cleaned_res or proc.stderr.strip()
        log_interaction("OpenAI Codex", prompt, final_res, elapsed, proc.returncode)
        failure = validate_reviewer_output("CODEX", final_res, proc.returncode, min_chars)
        return {
            "success": failure is None,
            "output": final_res,
            "returncode": proc.returncode,
            "elapsed": elapsed,
            "error": failure
        }
    except subprocess.TimeoutExpired:
        elapsed = time.time() - t0
        err = f"ERROR: Codex timed out after {timeout_sec}s"
        log_interaction("OpenAI Codex", prompt, err, elapsed, 124)
        return {"success": False, "output": err, "returncode": 124, "elapsed": elapsed, "error": err}
    except Exception as e:
        elapsed = time.time() - t0
        err = f"ERROR invoking Codex: {e}"
        log_interaction("OpenAI Codex", prompt, err, elapsed, 1)
        return {"success": False, "output": err, "returncode": 1, "elapsed": elapsed, "error": err}


def ask_codex(prompt: str, timeout_sec: int = 180) -> str:
    """Invokes OpenAI Codex / ChatGPT and returns cleaned output string."""
    return ask_codex_detailed(prompt, timeout_sec)["output"]


def ask_antigravity_detailed(prompt: str, timeout_sec: int = 120) -> Dict[str, Any]:
    """Invokes Antigravity reasoning model non-interactively with structured returncode tracking."""
    import antigravity.daemons.inbox_worker as iw
    if iw.MODEL_DISPATCH_HOOK:
        res = iw.MODEL_DISPATCH_HOOK(prompt, timeout_sec)
        log_interaction("Antigravity Model", prompt, res.get("output", ""), res.get("elapsed_sec", 0.0), res.get("returncode", 0))
        return res

    if not os.path.exists(AGY_BIN):
        err = f"ERROR: Antigravity binary not found at {AGY_BIN}"
        log_interaction("Antigravity Model", prompt, err, 0.0, 1)
        return {"success": False, "output": err, "returncode": 1, "elapsed": 0.0, "error": err}

    t0 = time.time()
    try:
        proc = subprocess.run(
            [
                AGY_BIN,
                "--sandbox",
                "--disable-slash-commands",
                "--model", "gemini-3.8-flash-low",
                "-p", prompt
            ],
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            encoding="utf-8",
            errors="replace"
        )
        elapsed = time.time() - t0
        out = proc.stdout.strip() or proc.stderr.strip()
        log_interaction("Antigravity Model", prompt, out, elapsed, proc.returncode)
        success = (proc.returncode == 0)
        return {
            "success": success,
            "output": out,
            "returncode": proc.returncode,
            "elapsed": elapsed,
            "error": None if success else f"Antigravity CLI exited with code {proc.returncode}: {proc.stderr.strip()}"
        }
    except subprocess.TimeoutExpired:
        elapsed = time.time() - t0
        err = f"ERROR: Antigravity timed out after {timeout_sec}s"
        log_interaction("Antigravity Model", prompt, err, elapsed, 124)
        return {"success": False, "output": err, "returncode": 124, "elapsed": elapsed, "error": err}
    except Exception as e:
        elapsed = time.time() - t0
        err = f"ERROR invoking Antigravity: {e}"
        log_interaction("Antigravity Model", prompt, err, elapsed, 1)
        return {"success": False, "output": err, "returncode": 1, "elapsed": elapsed, "error": err}


def ask_antigravity(prompt: str, timeout_sec: int = 120) -> str:
    """Invokes Antigravity reasoning model and returns output string."""
    return ask_antigravity_detailed(prompt, timeout_sec)["output"]


def broadcast(prompt: str) -> Dict[str, str]:
    """Sends a prompt to both Claude and Codex simultaneously and collects responses."""
    print(">>> Dispatching prompt to Claude Code...")
    claude_reply = ask_claude(prompt)
    print(">>> Dispatching prompt to OpenAI Codex / ChatGPT...")
    codex_reply = ask_codex(prompt)
    return {
        "claude": claude_reply,
        "codex": codex_reply
    }
# ==============================================================================
# Durable Bidirectional Messaging API (Antigravity Inbox / Outbox Protocol)
# ==============================================================================

def send_to_antigravity(
    sender: str,
    subject: str,
    body: Any,
    track: str = "SHARED",
    correlation_id: Optional[str] = None,
    source_file: Optional[str] = None,
    expected_response_file: Optional[str] = None,
    expected_artifact_hash: Optional[str] = None,
    pre_task_hash: Optional[str] = None,
    completion_marker: Optional[str] = None,
    timeout_sec: float = 120.0,
    message_id: Optional[str] = None,
    auth_secret: Optional[str] = None,
    nonce: Optional[str] = None
) -> Tuple[str, str]:
    """
    Enqueues an authenticated, immutable message in Antigravity's inbox.
    Returns: (message_id, correlation_id)
    """
    ensure_directories()
    msg_id = message_id or f"msg_{int(time.time())}_{uuid.uuid4().hex[:12]}"
    corr_id = correlation_id or f"corr_{int(time.time())}_{uuid.uuid4().hex[:12]}"

    # Validate Identifier format strictly
    if not IDENTIFIER_REGEX.match(msg_id):
        raise ValueError(f"Invalid message_id '{msg_id}'; must match ^[a-zA-Z0-9_\\-]{{8,64}}$")
    if not IDENTIFIER_REGEX.match(corr_id):
        raise ValueError(f"Invalid correlation_id '{corr_id}'; must match ^[a-zA-Z0-9_\\-]{{8,64}}$")

    # Check for duplicate message_id in inbox, outbox, archive, dead_letter (Idempotency)
    safe_msg_file = get_safe_filename(msg_id, ".json")
    safe_dead_file = get_safe_filename(msg_id, ".dead.json")
    for check_dir in [INBOX_DIR, ARCHIVE_DIR, DEAD_LETTER_DIR]:
        if os.path.exists(os.path.join(check_dir, safe_msg_file)) or os.path.exists(os.path.join(check_dir, safe_dead_file)):
            return msg_id, corr_id

    # Load sender secret key from external storage
    secret_key = auth_secret or get_agent_secret_key(sender) or load_auth_config().get("secret_key", "")
    msg_nonce = nonce or uuid.uuid4().hex

    envelope = {
        "message_id": msg_id,
        "correlation_id": corr_id,
        "sender": sender,
        "recipient": "ANTIGRAVITY",
        "track": track,
        "created_at_ist": get_current_ist(),
        "subject": subject,
        "body": body,
        "status": "CREATED",
        "attempt_count": 0,
        "nonce": msg_nonce,
        "source_file": source_file,
        "expected_response_file": expected_response_file,
        "expected_artifact_hash": expected_artifact_hash,
        "pre_task_hash": pre_task_hash,
        "completion_marker": completion_marker,
        "timeout_sec": timeout_sec
    }

    # Cryptographic HMAC-SHA256 signature across all envelope fields
    envelope["auth_signature"] = compute_envelope_hmac(envelope, secret_key)

    inbox_path = os.path.join(INBOX_DIR, safe_msg_file)
    write_json_atomic(inbox_path, envelope)
    return msg_id, corr_id


def get_message_status(message_id: str, correlation_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Inspects inbox, outbox, archive, and dead_letter to return explicit status:
    CREATED, CLAIMED, PROCESSING, COMPLETED, FAILED, TIMED_OUT, INCOMPLETE, CONFLICT, UNKNOWN.
    """
    ensure_directories()
    # 1. Check outbox for response first (if correlation_id provided)
    if correlation_id:
        safe_corr = get_safe_filename(correlation_id, "_resp.json")
        outbox_path = os.path.join(OUTBOX_DIR, safe_corr)
        if os.path.exists(outbox_path):
            try:
                with open(outbox_path, "r", encoding="utf-8") as f:
                    resp = json.load(f)
                return {
                    "status": resp.get("status", "COMPLETED"),
                    "location": "outbox",
                    "response": resp,
                    "error": resp.get("error")
                }
            except Exception:
                pass

    safe_msg = get_safe_filename(message_id, ".json")
    safe_claimed = get_safe_filename(message_id, ".claimed")
    safe_dead = get_safe_filename(message_id, ".dead.json")

    # 2. Check inbox for active/claimed
    inbox_json = os.path.join(INBOX_DIR, safe_msg)
    if os.path.exists(inbox_json):
        return {"status": "CREATED", "location": "inbox", "error": None}

    inbox_claimed = os.path.join(INBOX_DIR, safe_claimed)
    if os.path.exists(inbox_claimed):
        # Check if processing
        try:
            with open(inbox_claimed, "r", encoding="utf-8") as f:
                d = json.load(f)
            return {"status": d.get("status", "CLAIMED"), "location": "inbox", "error": None}
        except Exception:
            return {"status": "CLAIMED", "location": "inbox", "error": None}

    # 3. Check archive
    archive_json = os.path.join(ARCHIVE_DIR, safe_msg)
    if os.path.exists(archive_json):
        return {"status": "COMPLETED", "location": "archive", "error": None}

    # 4. Check dead letter
    dead_json = os.path.join(DEAD_LETTER_DIR, safe_dead)
    if os.path.exists(dead_json):
        err_detail = None
        try:
            with open(dead_json, "r", encoding="utf-8") as f:
                d = json.load(f)
            err_detail = d.get("error")
        except Exception:
            pass
        return {"status": "FAILED", "location": "dead_letter", "error": err_detail}

    return {"status": "UNKNOWN", "location": "none", "error": "Message ID not found."}


def read_antigravity_response(correlation_id: str) -> Optional[Dict[str, Any]]:
    """Reads response envelope from outbox/<correlation_id>_resp.json if present."""
    ensure_directories()
    safe_corr = get_safe_filename(correlation_id, "_resp.json")
    resp_path = os.path.join(OUTBOX_DIR, safe_corr)
    if os.path.exists(resp_path):
        try:
            with open(resp_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def wait_for_antigravity_response(
    correlation_id: str,
    timeout_sec: float = 30.0,
    poll_interval_sec: float = 0.25,
    auto_process_worker: bool = False,
    expected_file: Optional[str] = None,
    completion_marker: Optional[str] = None,
    expected_hash: Optional[str] = None,
    pre_task_hash: Optional[str] = None,
    expected_responder: Optional[str] = "ANTIGRAVITY",
    track: str = "SHARED"
) -> Dict[str, Any]:
    """
    Polls until response arrives or timeout.
    Performs MANDATORY verification on the response envelope:
    - Verifies cryptographic signature using responder's key.
    - Verifies responder identity and correlation ID.
    - Verifies task completion, output artifacts, hash modifications, and zero hedging.
    Returns success=False if verification fails!
    """
    ensure_directories()
    t0 = time.time()
    worker = InboxWorker() if auto_process_worker else None

    while (time.time() - t0) < timeout_sec:
        if auto_process_worker and worker:
            worker.run_single_pass()

        resp = read_antigravity_response(correlation_id)
        if resp:
            ver = verify_task_completion(
                resp,
                expected_file=expected_file,
                completion_marker=completion_marker,
                expected_hash=expected_hash,
                pre_task_hash=pre_task_hash,
                expected_correlation_id=correlation_id,
                expected_responder=expected_responder,
                track=track
            )
            elapsed = round(time.time() - t0, 3)
            if not ver.get("verified"):
                return {
                    "success": False,
                    "status": ver.get("status", "VERIFICATION_FAILED"),
                    "response": resp,
                    "verification": ver,
                    "elapsed_sec": elapsed,
                    "error": ver.get("reason")
                }
            return {
                "success": True,
                "status": "COMPLETED",
                "response": resp,
                "verification": ver,
                "elapsed_sec": elapsed,
                "error": None
            }
        time.sleep(poll_interval_sec)

    return {
        "success": False,
        "status": "TIMED_OUT",
        "response": None,
        "verification": None,
        "elapsed_sec": round(time.time() - t0, 3),
        "error": f"TIMED_OUT: Antigravity did not respond within {timeout_sec}s."
    }


def verify_task_completion(
    response: Optional[Dict[str, Any]],
    expected_file: Optional[str] = None,
    completion_marker: Optional[str] = None,
    expected_hash: Optional[str] = None,
    pre_task_hash: Optional[str] = None,
    expected_correlation_id: Optional[str] = None,
    expected_responder: Optional[str] = "ANTIGRAVITY",
    track: str = "SHARED"
) -> Dict[str, Any]:
    """
    Strict, cryptographically grounded verification of task completion:
    - Fails if response envelope is missing or lacks required fields (including auth_signature, nonce).
    - Fails if cryptographic HMAC signature verification fails on response.
    - Fails if responder is unauthorized or unexpected.
    - Fails if correlation_id mismatches.
    - Fails if status != 'COMPLETED'.
    - Fails if response body contains conversational hedging / permission seeking.
    - Fails if expected_file does not exist, is empty, or is missing completion_marker.
    - Fails if response artifact_hashes does not claim expected_file.
    - Fails if on-disk hash does not match claimed hash or expected_hash.
    - Fails if pre_task_hash matches current hash (artifact was never changed).
    Returns: {"verified": bool, "status": str, "reason": str, "artifact_hashes": dict}
    """
    if not response or not isinstance(response, dict):
        return {"verified": False, "status": "INCOMPLETE", "reason": "No response envelope provided.", "artifact_hashes": {}}

    # 1. Full Envelope Schema Enforcement
    required_response_fields = [
        "message_id", "correlation_id", "responder", "status",
        "completed_at_ist", "output_payload", "artifact_hashes",
        "auth_signature", "nonce"
    ]
    for rf in required_response_fields:
        if rf not in response:
            return {
                "verified": False,
                "status": "INCOMPLETE",
                "reason": f"Response envelope missing required field '{rf}'.",
                "artifact_hashes": {}
            }

    # 2. Responder Identity & Cryptographic Response Signature Verification
    actual_responder = response.get("responder")
    target_responder = expected_responder or "ANTIGRAVITY"
    if actual_responder != target_responder:
        return {
            "verified": False,
            "status": "FAILED",
            "reason": f"Unauthorized responder '{actual_responder}'; expected '{target_responder}'.",
            "artifact_hashes": response.get("artifact_hashes", {})
        }

    resp_key = get_agent_secret_key(actual_responder)
    if resp_key:
        expected_sig = compute_envelope_hmac(response, resp_key)
        if not hmac.compare_digest(response.get("auth_signature", ""), expected_sig):
            return {
                "verified": False,
                "status": "AUTH_FAILED",
                "reason": "Response cryptographic HMAC signature verification failed.",
                "artifact_hashes": response.get("artifact_hashes", {})
            }

    # 3. Correlation ID Verification
    if expected_correlation_id and response.get("correlation_id") != expected_correlation_id:
        return {
            "verified": False,
            "status": "FAILED",
            "reason": f"Correlation ID mismatch: expected '{expected_correlation_id}', got '{response.get('correlation_id')}'.",
            "artifact_hashes": response.get("artifact_hashes", {})
        }

    # 4. Status Verification
    status = response.get("status")
    if status != "COMPLETED":
        return {
            "verified": False,
            "status": status or "FAILED",
            "reason": response.get("error") or f"Response status is '{status}', not COMPLETED.",
            "artifact_hashes": response.get("artifact_hashes", {})
        }

    # 5. Conversational Hedging / Permission Seeking Check
    payload_str = str(response.get("output_payload", ""))
    if PERMISSION_SEEKING_REGEX.search(payload_str):
        return {
            "verified": False,
            "status": "INCOMPLETE",
            "reason": "Hedging detected in output: conversational permission seeking instead of work completion.",
            "artifact_hashes": response.get("artifact_hashes", {})
        }

    # 6. Physical Artifact and Hash Verification
    artifact_hashes = response.get("artifact_hashes", {})
    if expected_file:
        valid, abs_path, err = validate_path_security(expected_file, track)
        if not valid or not abs_path:
            return {"verified": False, "status": "FAILED", "reason": err or "Invalid artifact path.", "artifact_hashes": artifact_hashes}

        if not os.path.exists(abs_path):
            return {
                "verified": False,
                "status": "INCOMPLETE",
                "reason": f"Expected artifact physically absent on disk: '{expected_file}'.",
                "artifact_hashes": artifact_hashes
            }

        # Check empty file
        if os.path.getsize(abs_path) == 0:
            return {
                "verified": False,
                "status": "INCOMPLETE",
                "reason": f"Artifact file '{expected_file}' is empty (0 bytes).",
                "artifact_hashes": artifact_hashes
            }

        file_hash = compute_sha256(abs_path)

        # Artifact must be claimed in response envelope
        if expected_file not in artifact_hashes:
            return {
                "verified": False,
                "status": "INCOMPLETE",
                "reason": f"Response artifact_hashes did not contain expected file '{expected_file}'.",
                "artifact_hashes": artifact_hashes
            }

        # Declared hash must match actual on-disk hash
        if artifact_hashes[expected_file] != file_hash:
            return {
                "verified": False,
                "status": "INCOMPLETE",
                "reason": f"Response claimed hash '{artifact_hashes[expected_file]}' does not match disk hash '{file_hash}' for '{expected_file}'.",
                "artifact_hashes": artifact_hashes
            }

        # Pre-task Hash Check: Prevents falsely claiming an untouched pre-existing file was generated
        if pre_task_hash and file_hash == pre_task_hash:
            return {
                "verified": False,
                "status": "INCOMPLETE",
                "reason": f"Artifact '{expected_file}' was never modified (current hash matches pre_task_hash '{pre_task_hash}').",
                "artifact_hashes": artifact_hashes
            }

        # Expected hash check
        if expected_hash and file_hash != expected_hash:
            return {
                "verified": False,
                "status": "INCOMPLETE",
                "reason": f"Artifact hash mismatch on '{expected_file}': expected {expected_hash}, got {file_hash}.",
                "artifact_hashes": artifact_hashes
            }

        # Completion marker check
        if completion_marker:
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                if completion_marker not in content:
                    return {
                        "verified": False,
                        "status": "INCOMPLETE",
                        "reason": f"Completion marker '{completion_marker}' not found in '{expected_file}'.",
                        "artifact_hashes": artifact_hashes
                    }
            except Exception as e:
                return {"verified": False, "status": "FAILED", "reason": f"Error reading artifact: {e}", "artifact_hashes": artifact_hashes}

    return {"verified": True, "status": "COMPLETED", "reason": "Task successfully verified with all required envelopes and artifacts.", "artifact_hashes": artifact_hashes}


def safe_atomic_file_write_occ(
    target_file: str,
    content: str,
    expected_base_hash: Optional[str] = None,
    track: str = "SHARED"
) -> Dict[str, Any]:
    """
    Optimistic Concurrency Control file writer preventing lost updates.
    1. Validates path security and Rule 11 track isolation.
    2. Truly atomic per-file locking via FileLock.
    3. Mandates and compares target file's current SHA-256 against expected_base_hash.
    4. Preserves timestamped backup in antigravity/messages/backups/.
    5. Writes content atomically.
    Returns: {"success": bool, "sha256": str, "backup": str, "error": Optional[str]}
    """
    ensure_directories()
    valid, abs_path, err = validate_path_security(target_file, track)
    if not valid or not abs_path:
        return {"success": False, "sha256": None, "backup": None, "error": err}

    with FileLock(abs_path, timeout_sec=5.0):
        # Mandatory OCC Hash Verification on existing files
        if os.path.exists(abs_path):
            if not expected_base_hash:
                return {
                    "success": False,
                    "sha256": None,
                    "backup": None,
                    "error": "OCC_REQUIRED: expected_base_hash is mandatory when modifying existing file."
                }
            curr_hash = compute_sha256(abs_path)
            if curr_hash != expected_base_hash:
                return {
                    "success": False,
                    "sha256": curr_hash,
                    "backup": None,
                    "error": f"OCC_CONFLICT: Base hash '{expected_base_hash}' does not match current disk hash '{curr_hash}'."
                }

        # Create timestamped backup if file exists
        backup_path = None
        if os.path.exists(abs_path):
            backup_name = f"{os.path.basename(abs_path)}.{int(time.time())}.bak"
            backup_path = os.path.join(BACKUPS_DIR, backup_name)
            with open(abs_path, "rb") as sf, open(backup_path, "wb") as df:
                df.write(sf.read())

        # Write atomically
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        tmp_path = abs_path + f".tmp_{uuid.uuid4().hex[:8]}"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, abs_path)

        new_hash = compute_sha256(abs_path)
        return {
            "success": True,
            "sha256": new_hash,
            "backup": backup_path,
            "error": None
        }


if __name__ == "__main__":
    if len(sys.argv) > 1:
        test_prompt = " ".join(sys.argv[1:])
    else:
        test_prompt = "State in 1 sentence your current status on Project Swing Trades and whether you confirm AGENTS.md Rule 11 (Absolute Track Isolation)."
    
    print(f"=== BROADCASTING TO TRI-AGENT BUS ===")
    print(f"Prompt: {test_prompt}\n")
    results = broadcast(test_prompt)
    
    print("\n" + "="*50)
    print("CLAUDE CODE RESPONSE:")
    print("="*50)
    print(results["claude"])
    
    print("\n" + "="*50)
    print("OPENAI CODEX RESPONSE:")
    print("="*50)
    print(results["codex"])
