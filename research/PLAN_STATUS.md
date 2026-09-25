# Plan status (Track 2 execution plan, `research/notes/PLAN.md`)

| Date | Phase | Task | Status | Commit | Notes |
|---|---|---|---|---|---|
| 2026-09-25 | P0 | Record main checkout state | done | – | HEAD cdc0665, clean apart from the untracked plan file |
| 2026-09-25 | P0 | Worktree `track2/decision-engine` | done | – | Pre-existing (12:46 IST), clean; used as is |
| 2026-09-25 | P0 | Ignore generated data, before any generation | done | 1aa0e31 | Also un-ignores `research/studies/prereg/*.lock` (the global `*.lock` rule would hide the P7.3 lock) |
| 2026-09-25 | P0 | Plan copied to `research/notes/PLAN.md` | done | 0bcd087 | Byte-identical |
| 2026-09-25 | P0 | Test baseline | done | – | 314 passed on Windows at cdc0665 |
| 2026-09-25 | P0 | Baseline note | done | (P0 commit) | `research/notes/p0_baseline.md`; two plan facts outdated (production fee rate; pyarrow missing) |
| 2026-09-25 | P1 | Measured facts, trials registry, erratum, register | done | 9905e88 | F1–F11 re-run; T0001–T0021; register.json single-writer test |
| 2026-09-25 | P2 | D1–D19 engine defects | done | (P2 commit) | 27 defect tests; single simulation in `research/studies/signal_sim.py`; D10 calibration deferred to P4 |
| 2026-09-25 | P2 | Regression 1 (audit ORB first breaks) | done | (P2 commit) | 103 events, 18/9/76, net −0.0839R; T1 9 vs 10 is IREDA 2026-09-18 touch-only (trade-through rule) |
| 2026-09-25 | P2 | Regression 2 (audit production-code ORB) | done | (P2 commit) | n = 41, −0.0026R (audit −0.003R) |
| 2026-09-25 | P2 | Runtime | done | – | NoOp benchmark 0.136 → 0.017 s/session; the 30-min target on real data is unverified until P5 |
| 2026-09-25 | P2 | Full suite | done | – | 347 passed; report `research/notes/p2_report.md`. STOP: awaiting "continue" and a pyarrow decision |
