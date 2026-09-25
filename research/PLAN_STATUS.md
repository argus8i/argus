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
| 2026-09-25 | P3 | Data layer code (P3.1, P3.5–P3.9) | done | fd288e2 | Adopted from an unidentified session; provenance QA_ONLY enforcement added |
| 2026-09-25 | P3 | P3.2 instruments, P3.3 dhan_history | deferred | – | Data-source decision pending (Antigravity proposes an HF Upstox mirror; not yet approved) |
| 2026-09-25 | P3 | P3.4 nse_events | not started | – | Decision 7 approved; RESID_REV_NF only until built |
| 2026-09-25 | P3 | Acceptance | **FAIL** | – | Yahoo fails the P3.9 cross-check; 0 design-set sessions. Report `research/notes/p3_data_report.md` |
| 2026-09-25 | P4 | Features and point-in-time calibration | done | 823aaa2 | Synthetic known-answer and look-ahead tests |
| 2026-09-25 | P5 | ORB_PROD, RESID_REV v1, ORB_SIMPLE rename | done | (P5 commit) | Synthetic tests; ORB_PROD matches the desk on sampled days; next-trading-day rule made fail-closed |
| 2026-09-25 | P5 | Descriptive runs registered | done | (P5 commit) | T0022/T0023 (Yahoo post-CAS, not evidence) |
| 2026-09-25 | P3 | P3.4 NSE events + F&O ban fetcher | done | b0c1e0a, 91a5707 | Keys verified on recorded responses; ex-dates use a labelled assumption (no broadcast time from the API); bulk 2021-10..2026-09 download running |
| 2026-09-25 | P6 | Decision engine P6.1–P6.7 | done | (P6 commit) | 32 tests; false promotion 2.59%, power 0.801, futility 75.7%; band-hit scenario Rs 17,500 > X Rs 12,000 (decision needed); erratum E-P6.7 |
| 2026-09-25 | P6 | Adjusted A1 (3 slots, Rs 38,000/position, Rs 1,14,000 aggregate incl. pending, Rs 1,500 risk) | done | (A1 commit) | research/decision only; engine, draft prereg and production still at Rs 58,333.33 (listed in p6_report.md); -10% scenario Rs 11,400 within the Rs 12,000 budget before costs |
| 2026-09-25 | P3 | P3.4 bulk NSE download | stopped | – | 3 consecutive ReadTimeouts after 713 announcement days (Oct 2021 - Sep 2023); stop rule fired as designed; resumable |
| 2026-09-25 | P7-prep | Adjusted A1 in engine, prereg (DRAFT), metrics, pairs | done | dff8c1c | Engine sizes the slot cap at the worst admissible entry; aggregate check over filled + pending; audit replays pin pre-A1 sizing |
| 2026-09-25 | P3 | Upstox V2 intraday source (upstox_history.py) | built; source **FAILS** P3.9 | (Upstox commit) | Starts identical; OHLC >1 tick on 12-28% of bars, volume >1% on 12%; Yahoo sides with Kite 3:1; stays QA_ONLY; universe NOT ingested (research/notes/p3_upstox_report.md) |
| 2026-09-25 | P3 | P3.4 NSE download resumed (--interval 4) | running | – | Through 2024-04-24 at 20:24 IST |
| 2026-09-25 | P3 | Cross-source tolerance max(2 ticks, 0.20%) (Yashu) | done | (tolerance commit) | Upstox re-run still FAILS: 18 price fields, 837 volume bars > 1%; stays QA_ONLY pending volume/outlier decision |
| 2026-09-25 | P7-prep | Design window 2022-01-03..2024-09-30 (Yashu); holdout unchanged | done | (tolerance commit) | prereg still DRAFT |
| 2026-09-25 | P3 | P3.4 NSE download | stopped | – | 3rd stop (ConnectionError) at 2024-11-13; 1,144 announcement days on disk; awaiting Yashu's choice |
| 2026-09-25 | P3 | Cross-check gate: >= 99.5% agreement per field, volume reported (Yashu) | done | (promotion commit) | Upstox PASSES (exit 0); UPSTOX_API_V2 promoted to STRATEGY_SOURCES |
| 2026-09-25 | P3 | 228-series Upstox history (Antigravity fetch, upstox_history.build) | verified | – | 13,251 raw files hash-verified; 99.67% valid series-sessions; 209/210 stocks >= 95% valid; acceptance line 1 passes |
| 2026-09-25 | P3 | NSE events download | reassigned to Antigravity | – | once a day, --interval 4 --max-requests 150, single process |
| 2026-09-25 | P3 | PIT F&O membership, wrong-company and duplicate-series guards | done | 1ca84a8, c3b058e | Membership from the F&O bhavcopy (stock futures); 5 merger-successor series excluded |
| 2026-09-26 | P3 | F&O ban lists 2021-10-01..2026-09-25 (Antigravity fetch) | verified | 1a18cf7 | 1,237 files, hashes match, every trading day covered; 3,368 banned stock-days all F&O members; ban-file NIL-header parse fixed. NSE now 403s this machine after the 16-worker run |
| 2026-09-26 | P7 | Sealed history snapshot `p7_design_20260926` | done | 1a18cf7 | 655 files, content 72a444d7...; universe rebuilt with ban history (122,309 eligible stock-days, median 170/session); design runs refuse unsealed history |
| 2026-09-26 | P7 | P7.2c hold-study runner + event study | built | 5a92373 | Waits for the snapshot z*; runs as RESID_REV_NF until board meetings and corporate actions cover the design window |
| 2026-09-26 | P8 | P8.2 shadow runner `research/shadow/run_day.py` | done | 6861bf6 | Replay of 2026-09-24 on real history: 19/19 ORB_PROD signals identical tick-by-tick vs one-shot. Live run blocked: feed is kite.zerodha.com/oms (rule 1.2.11), 9 symbols |
| 2026-09-26 | P8 | P8.1 production contract + review request | filed | baa32a1 | 2 guards pass (fees Rs 61.99 both; method names), 6 strict xfails (side/shorts, A1 caps, pending reservation, MIS default, RVOL floor); shared/reviews/track2_decision_engine_review_request_2026-09-26.md |
