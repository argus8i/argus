# Strategy framework: roles, stages and rules

Written 26 Sep 2026 by Claude for Yashu. Applies to every Track 2 strategy. AGENTS.md comes first where they overlap.
"(machine)" marks a rule the code enforces; the rest are enforced by review.

## 1. Roles: who does what

| Role | Who | Does | Never does |
|---|---|---|---|
| Owner | Yashu | Approves locks, data sources, new strategies, anything touching money | Is asked the same question twice; the agents decide the technical questions |
| Quant lead and red team | Claude | Strategy ideas, pre-registrations, design tests, the framework and desks, audits of all data before use | Approves its own code (Codex reviews it) |
| Systems reviewer | ChatGPT / Codex | Reviews every framework, desk or data-code change at an exact commit; writes failing tests for what it finds | Approves its own changes |
| Data engineer | Antigravity | Downloads raw files under the data program rules, runs the daily pipeline, reports with evidence | Cleans, analyses or backtests data; says "done" before Claude's audit |
| Daily runner | `python -m research.framework.daily` | Plans, scores, evaluates, writes the daily report | Places orders (there is no broker code) |
| Auditor | `python -m research.trust.status` | Rebuilds the state from the files; lists unreviewed code | Trusts any agent's summary |

## 2. Stages of a strategy

| Stage | Entry condition | What happens | Exit |
|---|---|---|---|
| S0 Idea | Anyone proposes it | A written hypothesis with a reason it should work after costs | S1, or dropped |
| S1 Draft | A `research/studies/prereg/<id>.yaml` with status DRAFT | Rules, costs, data, sizing and evaluation written down | S2 |
| S2 Design test | The design window only (never the sealed holdout) | A trials-registry row for every variant tried, including failures | KILLED_ON_DESIGN, or S3 |
| S3 Lock | Yashu approves; lock hash committed and pushed | Nothing in the file changes after this | S4 or S5 |
| S4 Holdout (once) | `research/studies/event_holdout.py`, pinned sealed snapshot | Read once; the conjunctive pass rule decides | REJECTED, or S5 |
| S5 Paper | Status LOCKED_PROSPECTIVE, registered in `research/framework/strategy.py` | Daily plans before 09:00, scored after the hold | STOP_FUTILE, FAIL, or PASS |
| S6 Review | PASS after the pre-registered number of plan days | Codex review of the desk and journal; Claude red-team report | Candidate, or stays paper |
| S7 Live candidate | AGENTS.md Rule 1 met (60 prospective sessions, 20 fillable entries, positive net expectancy) | Yashu's written decision | Never automatic |

A strategy changed after S3 is a NEW version (v2, v3) with its own file, lock and journal. Its history cannot count as
evidence for it, because it was seen before the change (this is why EXPIRY_RELIEF v2 is prospective-only).

## 3. The rules

**Money**
- F1. Paper only. The framework and desks contain no broker code (machine: `rules.paper_only_scan`, run in the tests).
- F2. A PASS never means "trade real money". Live money needs S7: AGENTS.md Rule 1, a Codex review, and Yashu in writing.
- F3. Sizing in any multi-strategy book follows Adjusted A1: 3 slots, Rs 38,000 per position, Rs 1,14,000 total
  including pending orders, Rs 1,500 risk per trade (`research/decision/allocator.py`).

**Pre-registration**
- F4. The strategy's pre-registration must be LOCKED_PROSPECTIVE, committed, unchanged, with an id matching the code
  (machine: `rules.strategy_problems`; the daily run skips a strategy with any problem and reports it).
- F5. If the pre-registration changes after the first plan, planning is REFUSED (machine).
- F6. Evaluation numbers (plan days to review, futility, t) come from the pre-registration file, never from the caller
  (machine: the plug-in reads them from the file).

**Evidence**
- F7. Evidence is PROSPECTIVE only: status OK, research code committed, written before 09:00 IST on the entry session.
  Anything else is LATE or REPLAY and never counts (machine).
- F8. One OK plan per plan day; a second is a DUPLICATE and is never scored (machine).
- F9. Each signal is scored once; results are never edited. The journal is hash-chained; a broken chain stops
  that strategy until it is explained (machine).
- F10. A VOID (no data, corporate action, zero quantity) is recorded with its reason and counted. A void rate above
  20% is flagged for review (machine), because VOIDs can hide losses.
- F11. The decision is binding: STOP_FUTILE (after the futility point) and FAIL (at review) end the strategy version.
  A PASS moves it to S6, nothing more (machine: `evaluate.decide`).

**Data**
- F12. Plan and score only on files that chain: each day's previous close must match the prior file's close
  (machine: `market.chain`; a missing session is a DATA_GAP, never a guessed holiday).
- F13. The entry session needs its published F&O ban list; without it the plan is BLOCKED (machine).
- F14. No dataset feeds a strategy before Claude's audit says PASS (hashes, dates inside files, coverage).
- F15. The sealed holdout is read once per locked pre-registration, only through the holdout runner. The backtester
  refuses any window outside the design window 2022-01-03..2024-09-30, and never reads a session past the window
  end (machine: `backtest.WindowRefused`).
- F16. Survivor-biased data (Kaggle TradingView) is for ideas only, up to 2013, never evidence.

**Process**
- F17. Research code must be committed before planning: the daily command REFUSES on uncommitted code, so no
  plan is wasted as LATE (machine).
- F18. Every framework, desk or data-code change is reviewed by Codex at an exact commit before it is trusted (Rule 8;
  the trust status lists unreviewed commits).
- F19. Every number relayed between agents is recomputed from the files before anyone relies on it.
- F20. Track 2 journals live only under `shared/track2_liquid/paper/`, never in Track 1 files (machine: Rule 11).

## 4. Adding a new strategy (checklist)

1. Pre-registration DRAFT: hypothesis, signal, eligibility, trade rules, costs, sizing, evaluation block
   (review after N plan days, futility after M, t threshold).
2. Design test on the design window; register every variant in the trials registry.
3. Lock (Yashu approves), then holdout once, or declare prospective-only with the reason.
4. A plug-in class (see `research/shadow/expiry_desk.py`, `ExpiryReliefV2`): `prereg_path`, `journal_path`,
   `is_plan_day`, `build_plan`, `score`, `hold_sessions`, `evaluation`. Only the strategy's own rules go here.
5. Tests first: plan selection, exclusions, scoring on synthetic files, plus the framework tests stay green.
6. Register it in `research/framework/strategy.py` `registered()`; Codex reviews the commit.
7. From the next plan day, the daily run handles it.

## 5. The tools

| Command | What it does |
|---|---|
| `python -m research.framework.daily` | Evening run: plan, score, evaluate, report (also shows the download and its audit) |
| `python -m research.framework.backtest --strategy ID --from D --to D --dry-run` | The same plug-in code over the design window; `--register "variant"` records the trial (needs committed code) |
| `python -m research.data.archive_audit` | Checks every downloaded archive file: fingerprint, readable, date inside, missing sessions, pacing, blocks |
| `python -m research.trust.status` | Rebuilds the whole state from files, including a `paper_desks` check of every registered strategy |
| `python -m research.shadow.expiry_desk summary` | One strategy's journal summary |

A backtest and the paper desk call the same two functions (`desk.make_plan`, `desk.score_plan`), so they cannot
disagree about what a rule means (machine: `test_backtest_matches_the_paper_desk_trade_for_trade`).

## 6. Every trading evening

    python -m research.framework.daily

Read `shared/track2_liquid/paper/daily/<date>.md`. Exit 0 is all clean. Exit 1 means read the PROBLEM lines tonight.
Exit 2 means commit the code first, then run it again. Exit 4 is an internal error: send the report to Claude.
On an expiry evening it must run after the F&O ban list for the next session is published, and before 09:00.
