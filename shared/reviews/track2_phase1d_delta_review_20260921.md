# Track 2 Phase 1D Hardening Delta Review

Read-only review. Track 2 only. Inspect only this post-review delta:

1. `resume()` now rejects runtime config, candidate eligibility, or source policy
   that differs from the frozen session.
2. The rehearsal runner now requires every live snapshot to contain the frozen
   eligible universe.
3. Runtime feed failures release the writer via `suspend_for_restart()` before
   propagating the error.
4. New adversarial tests cover changed config/candidates/policy and missing
   frozen symbols. The complete first-party suite passes 475/475.

Return any remaining P0/P1 defect and a final `APPROVED` or `BLOCKED` verdict.
Do not modify files.
