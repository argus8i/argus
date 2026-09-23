# Track 2 Phase 1B3A — Evidence Contract Hardening Mandate

**Owner:** Antigravity  
**Reviewers:** Claude and Codex  
**Scope:** Track 2 paper-observation evidence only

## Why this subphase exists

Claude identified five qualification-boundary defects that a coordinator alone
cannot safely repair: preflight is written only at close, source hashes are not
role-bound, the frozen universe is not enforced, snapshot receipt time is caller
controlled, and failed session attempts are not durably registered. Antigravity's
design review agreed with the intended coordinator but did not resolve all five.

Phase 1B3A repairs the evidence contract before Phase 1B3B adds orchestration.

## Assigned files

Antigravity may modify only:

- `antigravity/models/session_manifest.py`
- `antigravity/daemons/track2_session_recorder.py`
- `tests/test_session_evidence_20260920.py`
- `tests/test_track2_phase1b_integration.py`
- `tests/test_track2_phase1b3a_hardening.py` (new)

Do not modify Phase 1B2 ingestion, legacy radar/bridge, watchlists, logs, broker
configuration, Track 1, qualification counters, or trade logs.

## Required contract changes

### 1. Preflight must be sealed prospectively

- Add immutable `preflight.json` + digest creation before 09:15 IST.
- Append a hash-chained external preflight anchor containing session date, preflight
  digest, registration time, and previous chain hash.
- Verification must require exactly one matching anchor and prove it was written
  after preregistration freeze and before market open.
- `close_and_evaluate()` must verify the already-sealed preflight. It must never
  create, replace, or backdate preflight evidence at close.
- A missing, changed, late, duplicate, or unanchored preflight makes the session VOID.

### 2. Reference hashes must be bound to unique roles

- Every reference manifest entry must carry exactly one allowed role:
  `FNO`, `SURVEILLANCE`, `BAND_POLICY`, or `UNIVERSE`.
- The four roles must resolve to four contained, non-symlink, immutable files.
- `fno_source_sha256`, `surveillance_source_sha256`, and
  `band_source_sha256` must match their exact role, not merely any manifest hash.
- The market stream can never satisfy a reference role.
- Role paths and role hashes must be distinct. Duplicate roles, paths, or hashes
  make the session VOID.
- `universe_sha256` must exactly match the `UNIVERSE` role artifact.

### 3. Frozen universe must govern every event

- Define a canonical universe artifact with session date, deterministic selection
  rule/version, sorted unique symbols, and source bindings.
- Recorder construction/resume must verify its hash against preregistration.
- Reject snapshots containing symbols outside the frozen universe rather than
  silently accepting those events.
- Reject signals whose symbol is outside the frozen universe.
- Final evaluation independently scans the stream and makes the session VOID if any
  quote, depth, or signal symbol is outside the frozen universe.
- A frozen universe with fewer than four symbols is an anchored VOID attempt, never
  a counted zero-signal session.

### 4. Production receipt time must not be supplied per call

- Replace public per-snapshot `wall_clock` authority with one recorder-owned clock.
- Production defaults to an internal aware IST system clock. Tests may inject one
  explicit fake clock at construction; individual calls cannot override it.
- Every event records both exchange/source `timestamp` and coordinator `recorded_at`.
- Freshness, ordering, market window, and capture gaps use `recorded_at` plus source
  time; a caller cannot make an after-close replay prospective by passing
  `wall_clock=timestamp`.
- Add append-only, hash-chained stream checkpoints outside the session directory at
  bounded intervals. Missing/out-of-order checkpoints make qualification VOID.

### 5. Every attempted session must remain visible

- Add a hash-chained attempt registry outside individual session directories.
- The first coordinator action will append one attempt before source ingestion.
- The evidence model must support terminal attempt outcomes at minimum:
  `PENDING`, `VOID`, and `FINALIZED`, without deleting or rewriting prior records.
- A date cannot have more than one initial attempt. Missing terminal outcome or a
  broken chain prevents aggregation from treating the date as counted.
- Phase 1B3A may expose pure append/verify functions; the coordinator will call them
  in Phase 1B3B.

## Compatibility and migration

- Historical and pilot sessions without the new preflight, role, universe,
  checkpoint, and attempt evidence remain readable but must be `PILOT_UNVERIFIED`
  or `VOID`; they cannot count toward 60/20.
- Preserve Rule 1 and Rule 11.
- No E3 evidence may be created from browser snapshots.
- No broker/network/order code in the assigned files.

## Mandatory adversarial tests

At minimum cover:

1. backdated or late preflight;
2. missing/duplicate/tampered preflight anchor;
3. one file/hash used for multiple reference roles;
4. stream hash masquerading as a source hash;
5. missing/wrong role and universe hash mismatch;
6. out-of-universe quote, depth, and signal;
7. universe below four symbols;
8. after-close replay attempt with matching source timestamps;
9. injected per-call clock rejected by API;
10. missing, reordered, or tampered stream checkpoint;
11. duplicate attempt date and broken attempt hash chain;
12. crash/restart with exact immutable evidence versus mismatch;
13. old pilot evidence cannot count;
14. existing Phase 1A, 1B1 and 1B2 tests remain green after intentional fixture
    migration to the hardened contract.

## Acceptance

- Focused hardening tests pass.
- Full first-party `tests/` suite passes.
- `git diff --check` is clean for owned files.
- Claude and Codex independently report no P0/P1.
- Antigravity does not self-approve its implementation.
