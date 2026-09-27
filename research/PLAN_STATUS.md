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
| 2026-09-26 | P7 | z* official (sealed snapshot) | done | 7f63611 | z* = 3.00 (T0028) |
| 2026-09-26 | P7 | ORB_PROD design baseline | KILLED_ON_DESIGN | 7f63611 | n 14,456, net -0.060R (gross -0.003), t -7.40 (T0029) |
| 2026-09-26 | P7 | P7.2c hold study (RESID_REV_NF) | done | 7f63611 | h EOD by Yashu's highest-t rule; all three h negative net (T0030-T0032) |
| 2026-09-26 | P7 | P7.3 lock | done | d549fb7, 35d4311 | pushed before any holdout read; p7_report Part A 7c57316 |
| 2026-09-26 | P7 | P7.4 holdout (once) | **REJECTED** | c835d8c | net -0.124R, t -3.03, gross -0.036R (T0035-T0039) |
| 2026-09-26 | P7 | P7.5 A1 portfolio simulation | done | c83b431 | holdout book -Rs 9,331 (RESID_REV), -Rs 50,326 (ORB_PROD) |
| 2026-09-26 | P7 | Other production strategies on design | KILLED_ON_DESIGN x5 | (final) | COMPASS, LAST_LIGHT, TRAPDOOR, VOL_SQUEEZE, RECOIL: t -6.3 to -15.9 (T0044-T0048) |
| 2026-09-26 | P8 | ShadowRunner, feeds, reconcile | done | 38990db, da82299, bed827b, 882e59a | 590 passed, 6 xfailed; p8_report.md |
| 2026-09-26 | P9 | Production patch spec, security and data findings, overnight summary | done | (final) | p9_report.md; Kite CDP cookie bridge flagged; Upstox history is split/bonus back-adjusted |
| 2026-09-26 | EDGE | Exploratory scan + strategy lab (Antigravity's 8-strategy blueprint, Codex/Claude ideas) | done | 17ffa82 | T0056-T0106, T0118-T0119: EXPIRY_RELIEF reproduced (alpha +1.03% t 4.3); BAN_EXIT +2.00% was look-ahead; GAP_AND_GO false; BULK untestable (no data); others no edge |
| 2026-09-26 | EDGE | BAN_ENTRY_SHORT v1 one-minute simulator (Codex 12 fixes, 13 tests first) | done | 90afd68 | design VWAP SL3 +0.118R t 3.86 (T0107-T0113) |
| 2026-09-26 | EDGE | event_holdout.py runner + Codex pre-lock fixes (10) | done | db8620c | pinned snapshot, spec/runner consistency, markers, conjunctive gates; 626 passed |
| 2026-09-26 | EDGE | Locks BAN_ENTRY_SHORT_v1, EXPIRY_RELIEF_LONG_v1 | done | 7a9f262, 415a2b4 | Yashu approval 12:39 IST (pasted); holdout = sealed p7_holdout_20260926 |
| 2026-09-26 | EDGE | Holdout BAN_ENTRY_SHORT_v1 (once) | **REJECTED** | 43d66d2 | -0.018R t -0.27 (141 events); auction open -0.009R (T0120-T0124) |
| 2026-09-26 | EDGE | Holdout EXPIRY_RELIEF_LONG_v1 (once) | **REJECTED** | 43d66d2 | +0.009R t 0.09; drift alpha +0.47% t 1.66 (gate passed); 3% stop hit 46% (T0125-T0128) |
| 2026-09-26 | EDGE | PEAD_DRIFT_LONG v1 (BSE timestamps) | DRAFT | 6d581b1 | design +2.31%/trade t 2.02, alpha t 3.2, 2022 negative; lock needs Yashu's approval of the BSE source (T0129-T0130) |
| 2026-09-26 | FRAMEWORK | Paper-strategy framework (market, desk, evaluate, rules, daily, RULES.md) + expiry desk as first plug-in | done | (framework commit) | 695 passed, 6 xfailed; replay 2026-08-25 identical to the old desk (24 signals, 23 scored, -0.483R, max diff 0.0); 5 desk bugs fixed test-first (duplicate plans, missing-session window, missing-session hold, dirty code counted, skipped weekday) |
| 2026-09-26 | FRAMEWORK | Legacy NSE format reader + archive audit (research/data/archive_audit.py) + audit in the daily report | done | (audit commit) | 710 passed, 6 xfailed; live history has 5 missing weekend special sessions (12 Nov 2023, 20 Jan 2024, 2 Mar 2024, 18 May 2024, 1 Feb 2026); archive audit PASS on 358 files, then found a missing session between 3 and 6 Jun 2005 (weekend candidates) |
| 2026-09-26 | FRAMEWORK | Backtester (same make_plan/score_plan as the desk) + paper_desks trust check | done | 68cd4f4 | Dry run EXPIRY_RELIEF v2 rules on the design window through the framework: +0.383R, t 3.49 by plan day, 1,102 trades, 27 plan days, 3/3 years positive, book top-3 +0.207R (research code: +0.342R, t 3.14). 5 expiries refused by the missing weekend sessions (DATA_GAP). Not registered (verification run, informs no decision); design data, never evidence for v2 |
| 2026-09-26 | FRAMEWORK | ArchiveMarket (2005-2021 archive behind the framework) + backtest eras | done | (archive commit) | PLAYGROUND 2005-2013 and DESIGN open; 2014-2021 archive exam, holdout, prospective sealed. Smoke run on real 2005 files: 4 plans OK, June 2005 DATA_GAP (missing 4 Jun session), 24 trades (not evidence). 723 passed, 6 xfailed |
| 2026-09-27 | REVIEW | CODEX-FRAMEWORK-001 (CHANGES_REQUIRED): A1 A2 A4 A5 A6 A7 A8 fixed test-first; A3 (Track 2 market-cap / ASM-GSM gates) awaits Yashu | fixed | (fix commit) | 733 passed, 7 xfailed (A3 strict xfail). Real audit PASS_PARTIAL 1,566 files in 30 s without cache; 25 Aug replay identical |
| 2026-09-27 | REVIEW | CODEX-FRAMEWORK-002 (CHANGES_REQUIRED, 5 probes): all 5 fixed test-first | fixed | (fix commit) | 738 passed, 7 xfailed; real audit PASS_PARTIAL with required columns; 25 Aug replay identical |
| 2026-09-27 | REVIEW | CODEX-FRAMEWORK-003 (CHANGES_REQUIRED, 2 audit probes): fixed test-first | fixed | (fix commit) | 741 passed, 7 xfailed; real audit PASS_PARTIAL with value and HTTP-status checks |
| 2026-09-27 | DECISION | Yashu decision A: ASM/GSM exclusion added to EXPIRY_RELIEF_LONG_v2 (before any plan); market-cap band ORB-only (AGENTS.md Rule 11 amended) | done | (decision commit) | 747 passed, 6 xfailed (A3 resolved); lists required from plan day 2026-09-28 in history/raw/nse/surveillance; replay 25 Aug identical |
| 2026-09-27 | GOVERNANCE | Constitution draft v0.1 + approval ledger + trust check (research/trust/constitution.py) | draft | (governance commit) | 755 passed, 6 xfailed; status UNREVIEWED (DRAFT, not in force) until Yashu approves the exact text |
