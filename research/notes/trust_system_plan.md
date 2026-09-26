# Trust system plan (Track 2), draft v1, 26 Sep 2026

Author: Claude. For Yashu; review by Codex. Status: DRAFT until Codex's review is addressed.

## Why

Agent prose has repeatedly disagreed with the files. On 25–26 Sep:

- **Performance numbers:** 7 of 8 "blueprint" numbers did not reproduce.
  - One conditioned on the future (look-ahead).
  - One had no data behind it.
  - One was produced from bhavcopy approximations rather than a fill simulation.
- **Process and code:** "strictly serial" was a 20-worker parallel harvest; "Dhan 100% confirmed" was unchecked; "95% built" was said with no strategy passed; 23 production files were edited without review.
- **Data quality:** "0 quality errors" data failed the cross-check.

**Principle:** no agent's words change what Yashu believes or does. Only a check a machine can re-run does.

## Roles (who is trusted for what)

| Job | Owner | Trusted output |
|---|---|---|
| Fetching data, scheduled jobs, plumbing | Antigravity | A manifest + coverage audit that passes T4, never prose |
| Whether a strategy works, and how much | The code: locked pre-registration → one holdout run → `promotion.py` → `register.json` | The register and the trials registry |
| Code and data-contract review | Codex | Review records with failing-first tests |
| Research, red-teaming, claim checks | Claude | Reproducible studies in the registry |
| Decisions (sources, holdouts, capital) | Yashu | Written in chat; recorded by the agent that acts |

## Components

### T1. `status`: one command, one truth
`python -m research.trust.status` prints the real state from files only, never from notes or messages:

- **Strategies:** every entry in `register.json`, with its status, evidence class, lock hash and holdout record.
- **Trials:** registry size and the last 10 trials.
- **Data:** each dataset with its source, date coverage, gaps, manifest hash and approval status.
- **Snapshots:** sealed snapshots, verified.
- **Git and code:**
  - branch, HEAD and dirty files of both checkouts;
  - unreviewed branches, meaning commits not on `main` without a review record;
  - which checkout the main folder has checked out.
- **Paper desk:** the last heartbeat of each scheduled job, and journal integrity (the hash chain).
- **Verdict line:** for example "0 strategies passed; 0 in paper shadow; live trading: prohibited (Rule 1)".
- **Output:** a text report and `status.json`. It exits non-zero if any integrity check fails.

### T2. Evidence contract (an AGENTS.md amendment, which Yashu approves)
Any number, "verified", "done", "confirmed", "complete" or "100%" in an agent message must carry an evidence block:

```
EVIDENCE: command=<exact command>; exit=<code>; commit=<sha>; files=<paths>; output_sha256=<hash of raw output>
```

- **Default:** a claim without evidence is UNVERIFIED and cannot be acted on.
- **Labels:** every figure is marked MEASURED (from code on data), ESTIMATED (a model or assumption) or EXTERNAL (a quoted outside source, with a URL).
- **Failures first:** a status report states what failed or is unknown before what succeeded.

### T3. Claim checker
`python -m research.trust.claim_check <message.txt>` parses an agent message and:

- **Paths and commits:** checks that every cited path exists and every cited commit is in the repo.
- **Strategy numbers:** matches each strategy number (net R, t, win rate, n) against the trials registry and the register; mismatches are flagged.
- **Unbacked superlatives:** flags "100%", "fully", "guaranteed", "confirmed", "verified" and "zero errors" when no evidence block is attached.
- **Status words:** flags strategy status words ("surviving", "passed", "validated") that contradict `register.json`.
- **Report:** writes PASS / FLAGGED lines. It runs on every Antigravity reply that comes over the NEXUS bus (a wrapper in `research/trust/`; `antigravity/` is not edited).

### T4. Data provenance and approved sources
- **`research/trust/sources.yaml`:** approved sources, each with Yashu's approval date. A study refuses data from an unapproved source.
- **A manifest for every dataset:** endpoint, fetch-code commit, pacing, concurrency, request and response counts by status, pagination, raw-response hashes and fetch time.
- **`python -m research.trust.data_audit <dataset>`:** checks coverage, meaning expected versus present per symbol and period (for example a result filing per stock per quarter). It also checks duplicates, timezones and manifest consistency, and refuses on gaps above the dataset's threshold.

### T5. Agent claims ledger
`research/trust/claims_ledger.csv` records one row per material claim: date, agent, claim, evidence given, verification result, who verified it, and a link. It is seeded with the 25–26 Sep findings, and shows per-agent reliability over time.

### T6. Daily integrity report (after the post-close jobs)
One page:

- data freshness, and hash checks of the new files;
- job heartbeats;
- the paper-desk journal verified;
- new commits on unreviewed branches;
- the status verdict line.

It is written to `shared/track2_liquid/reports/integrity_YYYY-MM-DD.md` and is what Yashu reads instead of agent summaries.

### T7. Change control (enforcing Rule 8)
T1 lists every branch and commit touching `antigravity/models`, `antigravity/daemons` or `research/` that lacks a review record. The report shows "UNREVIEWED CODE PRESENT" in red until Codex reviews it.

## Phases

| Phase | Build | Done when |
|---|---|---|
| **P1** (today) | T1 status; T5 ledger seeded; T2 amendment text for Yashu | `status` runs green/red correctly, with tests; the ledger has today's rows |
| **P2** | T3 claim checker (wired to the bus wrapper); T4 `sources.yaml` + `data_audit` for the existing datasets | Today's Antigravity messages are run through T3, and it flags the known false claims; every current dataset has an audit report |
| **P3** | T6 daily integrity report; the paper desk (expiry-rebound v2, PEAD) writing its journal under `shared/track2_liquid/`, never `CHATGPT/observation_log.csv` (Track 1) | First report on the next session; the paper desk is armed before the 29 Sep expiry |
| **P4** | Long-history data (see "What blocks strategies") through the T4 gates | Audited 2005–2021 daily history available as a fresh, never-read test period |

Every component gets tests written first. Codex reviews each phase before the next starts.

## What blocks strategies (Claude's diagnosis)

**It is not a lack of ideas, and mostly not a lack of data volume.** In order of weight:

### 1. Too little history for anything slower than intraday
We have Oct 2021 – Sep 2026, about 5 years:
- **Monthly strategies:** momentum and 52-week highs get only 22 design months, so t ≈ 1 even when the effect is real. They cannot be judged.
- **Event strategies:** a 33-month design plus a 22-month holdout straddled a regulatory regime change, and both event edges died across it.
- **The holdout is spent:** with every test on it, Oct 2024 – Jul 2026 loses value as a clean test.

**Fix:** NSE's own archives (CM bhavcopy, F&O bhavcopy, delivery files) go back to the early 2000s, free. With 2005–2021 we would have:
- 15+ more years, and a **fresh, never-read test period**;
- several regimes (2008, 2013, 2015–16, 2020);
- real power for monthly strategies.

This is the single most valuable data job.

### 2. The constraints remove most known edges
- **What's allowed:** cash only, long-only overnight, ₹2.5L capital, 3 slots of ₹38k, and liquid F&O stocks (the most efficient part of the market).
- **What that rules out:** no shorting overnight, no options, no pairs, no leverage.
- **Costs:** round trips of 0.12% (MIS) and 0.28% (CNC) kill any edge below about 0.3% per trade.

Most professional money comes from speed, market-making, options premium, leverage or capital scale, none of which this account can use. What remains is slow, low-turnover and event-driven, which needs point 1.

### 3. Missing data types that the remaining edges need
Each of the following would open a real hypothesis. Each is a fetch job for Antigravity, then an audit through T4.

| Data | Source |
|---|---|
| Delivery % | NSE `sec_bhavdata_full` / MTO files |
| Historical F&O open interest and MWPL | NSE F&O bhavcopy; the MWPL files |
| FII/DII daily flows | NSE / NSDL |
| Bulk and block deals | NSE archives |
| Index constituent history | NSE circulars |
| Clean result timestamps after Nov 2024 | a properly re-harvested BSE feed, or a polite NSE fetch |
| Earnings surprise (actual vs expected) | not available free. Consensus estimates cost money, so PEAD can only use the price reaction as the surprise proxy. |

### 4. Idea quality
Ideas came from agent assertion rather than published evidence plus a mechanism. The fix:
- source ideas from peer-reviewed studies of Indian equities, and require a named mechanism;
- run the design test in `strategy_lab`, and only then pre-register.
