# Tri-Agent Communication Architecture & Durable Protocol
**Project Swing Trades: Antigravity + Claude Code + OpenAI Codex**  
**Document Revision:** 2.0 (Hardened Production Architecture) · **Date:** 2026-09-17  
**Enforcement:** AGENTS.md Rules 1–11  

---

## 1. System Taxonomy: Clarifying the Four Communication Tiers

To prevent confusion between prompt piping, file modification, and genuine autonomous coordination, Project Swing Trades defines an explicit 4-tier communication taxonomy:

| Tier | Name | Mechanism | Purpose & Guarantees |
| :--- | :--- | :--- | :--- |
| **Tier 1** | **Subprocess Dispatch** | `ask_claude()`, `ask_codex()`, `ask_antigravity()` via local CLI binaries (`claude.exe`, `codex.exe`, `agy.exe`) | Synchronous query dispatch. Captures live stdout/stderr from reasoning models. **Limitation:** Exit code 0 does *not* imply task completion or valid artifacts without Tier 3 verification. |
| **Tier 2** | **Durable Messaging Protocol** | File-backed inbox/outbox state machine (`inbox_worker.py`) with HMAC-SHA256 authentication | Asynchronous, durable, authenticated bidirectional communication. Every message is an immutable JSON file claimed atomically (`.claimed`), authenticated cryptographically, processed by live models or fast-paths, responded to in `outbox/`, and archived. Survives restarts. |
| **Tier 3** | **Task Completion & Cryptographic Verification** | `verify_task_completion()`, SHA-256 artifact hashing, OCC base hashing, completion markers | Validates that a requested change physically exists on disk, was modified after task issuance, matches expected SHA-256 hashes, contains completion markers, and explicitly rejects conversational hedging or arbitrary file claims. |
| **Tier 4** | **Human-in-the-Loop Chat** | IDE Chat UI & Review Artifacts | High-level architectural strategy, approval gates, and multi-agent debate synthesis presented directly to the user. |

---

## 2. Directory Architecture & State Machine

All inter-agent messages and security artifacts are stored under `antigravity/`:

```
antigravity/
├── config/
│   └── agent_auth.json        # Shared HMAC secret key, allowed agents, and freshness windows
├── messages/
│   ├── inbox/                 # Newly arrived messages awaiting worker processing (<msg_id>.json, <msg_id>.claimed)
│   ├── outbox/                # Worker response envelopes (<corr_id>_resp.json)
│   ├── archive/               # Successfully completed and verified messages (<msg_id>.json)
│   ├── dead_letter/           # Malformed, unauthorized, timed out, or rejected messages (<msg_id>.dead.json)
│   └── backups/               # Timestamped OCC pre-modification backups (<filename>.<timestamp>.bak)
└── daemons/
    ├── inbox_worker.py        # Autonomous inbox listener & model dispatch daemon
    └── tri_agent_bus.py       # Client bus library with HMAC signing and cryptographic verification
```

### Lifecycle State Machine

```
[SENDER: Claude/Codex/User]
        │
        ▼ (HMAC signed + atomic write)
  inbox/<id>.json  (Status: CREATED)
        │
        ▼ (worker claim via atomic os.replace)
  inbox/<id>.claimed  (Status: CLAIMED)
        │
        ├──────────────────────┬──────────────────────┬──────────────────────┐
        ▼                      ▼                      ▼                      ▼
  [Auth/Replay Fail]     [Schema/Track Fail]     [Hedging Found]       [Task Execution]
        │                      │                      │                      │
        ▼                      ▼                      ▼                      ├─► Success: outbox/<corr_id>_resp.json (COMPLETED)
 dead_letter/<id>.dead.json (FAILED/INCOMPLETE)                              │            archive/<id>.json
 outbox/<corr_id>_resp.json (FAILED/INCOMPLETE)                              │
                                                                             └─► Fail/Conflict: dead_letter/<id>.dead.json
                                                                                                outbox/<corr_id>_resp.json
```

---

## 3. Cryptographic Authentication & Security Protocol

### A. HMAC-SHA256 Message Signatures
Every message placed into the durable inbox must contain a valid cryptographic signature computed using a pre-shared HMAC secret key configured in `antigravity/config/agent_auth.json`:

$$\text{Canonical String} = \text{message\_id} : \text{correlation\_id} : \text{sender} : \text{recipient} : \text{created\_at\_ist} : \text{nonce} : \text{SHA256}(\text{body})$$

$$\text{auth\_signature} = \text{HMAC-SHA256}(\text{secret\_key}, \text{Canonical String})$$

Messages with missing, mismatched, or corrupt signatures fail closed immediately to `dead_letter/` with error `AUTH_FAILED`.

### B. Nonce Replay Protection & Timestamp Freshness
1. **Cryptographic Nonce:** Each message includes a unique random nonce (`uuid.uuid4().hex`). The worker maintains an in-memory cache of seen nonces (`SEEN_NONCES`). Replayed nonces are rejected with `REPLAY_ATTACK`.
2. **Timestamp Freshness Window:** Inbound message timestamps (`created_at_ist`) must be within **300 seconds** of current time and have no more than **60 seconds** of future clock skew. Expired or future-skewed messages are rejected with `TIMESTAMP_OUT_OF_BOUNDS`.

### C. Alphanumeric Identifier Regex & Path Sanitization
To prevent directory traversal and injection attacks:
1. `message_id` and `correlation_id` are strictly verified against `^[a-zA-Z0-9_\-]{8,64}$`.
2. Any disk access creates filenames using `get_safe_filename()`, stripping illegal characters and capping length.
3. Attempts to submit malicious identifiers (e.g. `../../../shared/evil`) are rejected with `INVALID_IDENTIFIER`.

### D. Whitelisted Submission Directories & Mandatory OCC
The `WRITE_SUBMISSION` subject allows automated agents to write review reports, but enforces rigid boundaries:
1. **Directory Whitelist:** Writes are restricted strictly to:
   - `shared/track1_esm/reviews/`
   - `shared/track2_liquid/reviews/`
   - `shared/reviews/`
   Attempts to write outside these directories (e.g. modifying `AGENTS.md` or `inbox_worker.py`) fail closed with `DIRECTORY_SECURITY_VIOLATION`.
2. **Mandatory OCC (`expected_base_hash`):** When modifying an existing file, the caller must supply `expected_base_hash`. If the on-disk hash does not match, the worker rejects the edit with `OCC_CONFLICT` and preserves previous file state. A timestamped backup is automatically generated in `antigravity/messages/backups/`.

---

## 4. Live Reasoning Model Integration (`agy.exe`)

Deterministic handlers (`PING`, `ECHO`, `INSPECT_FILE`, `WRITE_SUBMISSION`) execute synchronously on fast paths.

All non-fast-path subjects (e.g. `AUDIT_MOBIKWIK_SETUP`, `ANALYZE_CIRCUIT_RISK`, `PROPOSE_ALGO_REVISION`) automatically invoke the **live Antigravity reasoning model** via the official CLI:

```powershell
C:\Users\yashw\.gemini\bin\agy.exe --sandbox --disable-slash-commands --model gemini-3.8-flash-low -p "<prompt>"
```

### Pluggable Architecture & Security Guardrails
- In production, `inbox_worker.py` dispatches to `agy.exe` with terminal sandbox enforcement (`--sandbox --disable-slash-commands`) and zero permission-bypass flags (`--dangerously-skip-permissions` strictly prohibited).
- Per-agent external secret keys are stored outside the repository at `C:\Users\yashw\.gemini\antigravity\agent_keys.json`.
- Nonce replay protection is durably persisted across worker crashes via SQLite WAL database (`replay_store.db`).
- File access and edits are guarded by OS-level mutual exclusion locks (`FileLock` using `os.O_CREAT | os.O_EXCL`).
- In automated test harnesses, `MODEL_DISPATCH_HOOK` enables sub-second mocking of reasoning responses while fully exercising envelope formatting, outbox delivery, and verification logic.

---

## 5. Verification Protocol (`verify_task_completion`)

The verification engine in `tri_agent_bus.py` provides defense against completion fabrication:

1. **Envelope Validation:** Verifies that response is a valid dictionary containing `status == "COMPLETED"`, matching `responder == "ANTIGRAVITY"` (or expected responder), and matching `correlation_id`.
2. **Mandatory HMAC Signature Verification:** Cryptographically validates `auth_signature` across all canonical response envelope fields using the responder's external secret key.
3. **Pre-Task Hash Modification Enforcement:** When modifying an existing file, the caller passes `pre_task_hash`. If the file was not modified after task dispatch, the verification rejects the claim with `UNMODIFIED_ARTIFACT` (preventing an agent from claiming pre-existing files as new work).
4. **Claimed vs. Disk Hash Match:** Verifies that the SHA-256 hash reported in `artifact_hashes` matches the actual physical hash of the file on disk.
5. **Completion Marker Detection:** Verifies that required text markers are present in the final artifact.
6. **Anti-Hedging Filter:** Explicitly rejects conversational questions or permission-seeking strings.

---

## 6. Comprehensive Verification Suite (27 Tests, 74 Total)

The protocol is validated by an adversarial test suite in `tests/test_tri_agent_messaging.py`:

```powershell
.venv\Scripts\pytest.exe tests/test_tri_agent_messaging.py -v
```

### Test Scenarios
1. `test_successful_roundtrip_message` — End-to-end authenticated roundtrip (PING $\to$ PONG) with full archival.
2. `test_malformed_message_rejection` — Incomplete message envelopes fail closed to dead letter.
3. `test_duplicate_message_idempotency` — Duplicate message IDs deduplicate cleanly.
4. `test_timeout_returns_failure` — Unanswered correlation IDs strictly timeout without fabricating success.
5. `test_exit_code_zero_without_task_completion_fails` — Non-completed statuses fail verification.
6. `test_permission_request_classified_incomplete` — Conversational hedging rejected fail-closed.
7. `test_expected_artifact_missing_fails` — Missing or empty output files fail verification.
8. `test_concurrent_edit_conflict_detection` — OCC conflict detection and backup generation.
9. `test_worker_restart_and_recovery_of_claimed` — Orphaned `.claimed` recovery on worker restart.
10. `test_track_crossing_message_rejection` — AGENTS.md Rule 11 track isolation enforcement.
11. `test_path_traversal_rejection` — Path traversal attempts outside workspace blocked.
12. `test_no_command_execution_from_untrusted_fields` — Protection against shell injection.
13. `test_unauthenticated_sender_rejected` — Missing or corrupt HMAC signature rejected fail-closed.
14. `test_replay_attack_rejected` — Replay of identical nonces blocked by durable SQLite store.
15. `test_message_id_path_injection_blocked` — Traversal in message/correlation IDs blocked by regex.
16. `test_completion_fabrication_rejected` — Fabrication probes (unmodified pre-existing files, responder spoofing) rejected.
17. `test_write_submission_directory_escape_blocked` — Attempts to write outside review directories blocked.
18. `test_mandatory_occ_on_existing_file` — Overwriting existing files without `expected_base_hash` blocked.
19. `test_antigravity_reasoning_pipeline` — Complex prompts invoke live model reasoning rather than static responses.
20. `test_outbox_collision_prevention` — Prevents collisions with prior responses for the same correlation ID.
21. `test_full_envelope_tampering_rejected` — Tampering with any envelope field invalidates signature.
22. `test_response_envelope_hmac_tampering_rejected` — Tampering with response output payload invalidates HMAC verification.
23. `test_wait_for_antigravity_response_mandatory_verification_failure` — Mandatory verification returns failure on forged response.
24. `test_editing_task_missing_expected_response_file_rejected` — Editing task without expected output rejected.
25. `test_editing_task_existing_file_missing_pre_task_hash_rejected` — Modifying existing file without pre-task hash rejected.
26. `test_durable_replay_store_sqlite_persistence` — Nonce replay protection persists across worker restarts via SQLite WAL.
27. `test_file_lock_atomic_mutual_exclusion` — OS-level file lock mutual exclusion and stale lock breaking.

**Full Test Suite Status:**
- `tests/test_tri_agent_messaging.py`: **27 passed in 3.18s**
- Entire Project (`tests/`): **74 passed in 6.56s**

---

## 7. Supervised Daemon Operations

To run the inbox worker continuously with supervisor monitoring, mutual exclusion, and crash recovery:

```cmd
:: Launch using the workspace root batch file:
launch_inbox_worker.bat
```

Or via Python CLI:
```powershell
.venv\Scripts\python.exe antigravity\daemons\supervised_inbox_worker.py
```

Check status:
```powershell
.venv\Scripts\python.exe antigravity\daemons\supervised_inbox_worker.py --status
```

Stop daemon:
```powershell
.venv\Scripts\python.exe antigravity\daemons\supervised_inbox_worker.py --stop
```
