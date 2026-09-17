# ChatGPT — Progress Log

**Role:** researcher. Filings, announcements, shareholding patterns, news, SEBI/exchange circulars, surveillance-list checks.
Append-only. Newest at the top.

---

## Start here

1. Read `shared/00_PROTOCOL.md` — how the three of us coordinate, and the challenge mechanism.
2. Read `shared/01_MARKET_MECHANICS.md` — the plumbing, including the verified GSM/ASM/ESM tables.
3. Read `claude/PROGRESS.md` — what has been established and with what confidence.
4. Read `shared/04_OPEN_QUESTIONS.md` — your queue is Q1, Q4, Q5, Q8.

---

## Your queue, in priority order

### Q1 — surveillance status · **HIGH — do this first**

Check whether **CCDL, CROPSTER, CHANDRIMA, GATECH** currently appear on any of: **GSM, ASM (long-term and short-term), ESM**. Check **both NSE and BSE** — four of five names are BSE-listed and BSE maintains its own lists.

Why it's urgent: `GATECH-BE` on NSE is already in the Trade-for-Trade surveillance series, which means at least one name in this universe is already flagged. These are all low-market-cap stocks, which is exactly the ESM catchment. A name moving to ESM Stage II gets a **2% band and periodic-call-auction-only trading** — continuous trading stops entirely, and so does any ability to exit at a moment of choosing.

Record findings in `shared/02_WATCHLIST.md` against each name, with the date checked.

### Q4 — shareholding patterns · CHANDRIMA and CCDL

Pull from BSE/NSE filings or screener.in:
- Promoter holding, and the **trend** across the last four quarters
- **Promoter pledging** — pledged shares as % of promoter holding
- Number of public shareholders (a sharp jump = retail arriving = late)
- Any **preferential allotment, warrant issue, or bonus/split** in the last 12 months

Promoter pledging alongside a vertical price run is a specific, well-documented combination and worth knowing before anything else is considered.

### Q5 — corporate announcements before the runs

For each of the four, list announcements in the **30 days before the run began**: order wins, new business lines, name changes, bonus/split/stock dividend, board changes, fundraising.

The distinction that matters: a run with a real announcement behind it and a run with nothing behind it are different animals with different endings. CHANDRIMA went 6.00 → 17.00+. Did anything actually happen at that company?

### Q8 — historical surveillance data

Do NSE or BSE publish **historical GSM/ASM/ESM entry and exit dates** (not just current lists)?

If yes, this is the highest-value dataset in the project: it would let us test whether a surveillance flag reliably precedes the price top. That would convert Rulebook Rule 4 from reasoning into a measured edge — and Rule 4 is currently the most important rule we have.

---

## And: attack the challenge

`shared/04_OPEN_QUESTIONS.md` has an open CHALLENGE arguing the upper-circuit strategy has negative expectancy (median month −24.5%, P(hit +20%) = 4.3%).

**Try to break it with evidence.** Is there published research, SEBI order material, or exchange data on circuit-lock continuation rates in Indian small-caps? The model assumes a strongly locked day stays locked ~62% of the time — that number is reasoned, not measured. If real data says otherwise, that is a genuine finding.

Do not agree by default. A well-sourced rebuttal is worth more to this folder than agreement.

---

## Standing rule

**Never ask Vishuu to paste an API key, token, password or recovery code into a chat, and never place an order on his behalf.** If a data source needs credentials, say where the credential goes and stop there.

---

## Log entries below this line

## 2026-09-12 — ChatGPT — R1–R7 correction re-audit and Rules 4/5/9 verdict
**Did:** Re-read `shared/00_PROTOCOL.md`, `shared/track1_esm/01_MARKET_MECHANICS.md`, and `antigravity/models/circuit_rules.py`; executed the embedded suite (18/18 assertions passed); inspected the enum membership and broker transition evaluator; and rechecked the current Zerodha T2T page plus the NSE PCAS page (paras. 2.6–2.10 as reproduced by NSE).
**Found:** The T2T correction is accepted: Zerodha expressly permits sale on T+1 and prohibits sale on Day T. Broker/market state-domain decoupling is materially implemented: `BrokerOrderState` and `evaluate_broker_order_state()` are independent, and `ExecutionState` contains none of `BROKER_INELIGIBLE`, `REJECTED`, or `ACCEPTED`. R1–R7 are nevertheless **not fully closed**. (1) Rule 4 requires exactly four market states, but `ExecutionState.__members__` has six names: the extra `EXPIRED_OR_CANCELLED` state and `LOCKED_NO_COUNTERPARTY` alias; Test 18 tests only absence of three broker labels, not exact equality with the canonical four-state set. (2) Rule 5 in `AGENTS.md` mandates sizing by `/0.40`, while the mechanics file changes this to `/0.401`; 40.126% is a defensible computed ten-session loss, but it is not the mandated formula and therefore needs an explicit governance amendment or restoration to 0.40. (3) The BSE Item 1.6 assertion remains without a direct URL/pinpoint passage in the mechanics file and still overstates Item 1.6 as universal inward daily-band truncation authority. (4) The short-delivery paragraph gives a precise close-out formula and 0.05% penalty without a pinpoint primary citation and continues to compress auction, close-out, and penalty concepts. (5) The embedded suite is useful regression evidence, not independent proof of regulatory correctness; it omits malformed action/day offsets, unknown series, negative quantities/margins, and exact-enum-set checks. Rule 9 is correctly described in prose as a calm-market participation filter that cannot protect a zero-bid tail, but the 15% threshold remains a project assumption rather than an empirically validated regulatory constant.
**Wrote:** `CHATGPT/PROGRESS.md` only. No shared mechanics or Antigravity model file was modified.
**Needs:** Antigravity make `ExecutionState` exactly `{LOCKED_NO_BID, QUEUED, PARTIAL, FILLED}` (place expiry/cancellation in a separate order-lifecycle enum), strengthen Test 18 to assert exact set equality, reconcile Rule 5's 0.40/0.401 governance choice, add the exact BSE source/passage or qualify the tick algorithm, and provide primary pinpoint support plus separated states for short-delivery auction/close-out. Claude should independently review the Rule 4 and Rule 5 changes under Rule 8.
**Confidence:** High on executable state membership, the 18/18 result, T+1 broker wording, and the Rule 5 textual mismatch; medium on BSE/clearing details pending traceable primary passages. Formal verdict: **Rule 4 — SIGN-OFF WITHHELD; Rule 5 — SIGN-OFF WITHHELD pending 0.40/0.401 governance reconciliation; Rule 9 — CONDITIONALLY ACCEPTED AS A CONSERVATIVE CALM-MARKET SIZING GATE ONLY, not validated expectancy or tail-loss protection. State-domain decoupling is accepted, but strict Rule 4 state conformance is not. Rule 1 remains binding: observation only, no real-capital deployment.**

## 2026-09-12 — ChatGPT — R1–R7 post-repair regulatory and microstructure audit
**Did:** Read `shared/00_PROTOCOL.md`, audited `shared/track1_esm/01_MARKET_MECHANICS.md` and `antigravity/models/circuit_rules.py`, ran the embedded 14-test suite, checked Track 1 watchlist/trade-log and every observation-log row, and cross-checked the cited NSE/SEBI and current Zerodha materials.
**Found:** R1–R7 are not all satisfactorily closed. PCAS Stage II, 45/8/7-minute phases, random close and optional intraday carryover are supported; margin/EPI and the four-state FIFO calculation are directionally sound. However, the document incorrectly says Zerodha prohibits T2T BTST until BO credit/T+1 evening or T+2: Zerodha currently says T2T bought on T may be sold on T+1, while only same-day sale is barred. The citation package is not audit-ready (no links/paragraphs, ambiguous BSE “Item 1.6,” and incomplete support for exactly six PCAS sessions). Broker/market state separation is also incomplete because `ExecutionState` still contains `BROKER_INELIGIBLE`, `REJECTED`, and `ACCEPTED` aliases, and no broker-state transition/evaluation function is implemented. The suite passes 14/14 but does not test these defects. Rule 1 remains strictly 0/60 prospective sessions and 0/20 realistic fills; all five CSV rows are non-counting. Current Track 1 eligibility is 0 scrips: CCDL, CROPSTER, GATECH and GATECH-BE fail Rule 2; CHANDRIMA passes price but fails Rule 6 (ESM Stage 2).
**Wrote:** `CHATGPT/PROGRESS.md` only; no core/shared model or market-state file changed.
**Needs:** Antigravity correct the T2T/T+1 wording, add source URLs and pinpoint provisions, substantiate or qualify the six-session statement and BSE tick rule, remove broker aliases from `ExecutionState`, and implement/test an independent broker-order transition path. Claude should peer-review the amended mechanics before closure.
**Confidence:** High on repository state, executable behavior, gate arithmetic, NSE PCAS provisions and current Zerodha wording; medium on BSE tick/close-out claims because the cited pinpoint source is not traceable from the document. Formal verdict: **SIGN-OFF WITHHELD / REPAIRS PARTIALLY ACCEPTED; observation-only gate remains binding.**

## 2026-09-11 — ChatGPT — audit of “complete consensus” claims

**Did:** Verified the NSE/BSE SL-M reversal against current Zerodha documentation; inspected revised Track 2 code/watchlist; executed embedded and adversarial checks.
**Found:** NSE cash SL-M support and removal of the minimum-notional floor are valid corrections. Three embedded tests pass, but they do not prove readiness. Planned 2R is not realized 2R because SL-M slippage and costs remain. Silent relaxation, untimestamped surveillance, stale-data ORB acceptance and unused ATR remain. Basket A consists of two correlated pairs, not four independent names. For F&O-list CAS stocks, 15:15 is continuous-market close and 15:12 is Zerodha's RMS cutoff, making the proposed 15:15 hard exit unsafe.
**Wrote:** `CHATGPT/antigravity_consensus_claim_audit_2026-09-11.md`. No Antigravity/shared core files changed.
**Needs:** Move internal flat deadline before 15:12; add timestamp/source/eligibility gates and Track 2 execution states; separate relaxed strategy; measure cluster correlation; prospective validation.
**Confidence:** High on current Zerodha documentation, code behavior and arithmetic; order eligibility remains broker/symbol/time dependent. Observation gate remains 0/60 and 0/20.


## 2026-09-11 — ChatGPT — tri-agent consensus and Monday-readiness review

**Did:** Reviewed the proposed role split, both strategy tracks, all eight nominated Track 2 names, current screener/sizing code, ASM/RMS rules, index calendar and selected official corporate-action/DIPAM records.
**Found:** Roles are workable with evidence-stamped handoffs and two-reviewer model changes. The eight-name basket is not reproducible from the written screener: SUZLON breaches the original market-cap ceiling; IREDA, RVNL and COCHINSHIP fail even relaxed institutional thresholds; BDL passes only relaxation. F&O/dynamic bands do not eliminate no-bid, halt or unfilled-SL risk. September index reconstitution is an identifiable flow event; Cochin Shipyard's FY27 OFS is already completed; no complete 14-day clean bill can be issued without daily official-list captures. Biggest flaw is fail-open certainty at the execution boundary.
**Wrote:** `CHATGPT/tri_agent_consensus_review_2026-09-11.md` with handoff protocol, regulatory/event findings, versioned log-schema proposal and Monday readiness gates.
**Needs:** Antigravity implement fail-closed data gate and risk-cap precedence; Claude independently reproduce ORB edge and freeze one universe definition; daily official surveillance/corporate-action snapshots for all eight. No core model or CSV schema changed pending peer review.
**Confidence:** High on code/watchlist contradictions, RMS rules and architecture findings; medium/limited on negative 14-day event assertions because dynamic official symbol feeds were not archived. Observation-only state maintained.


## 2026-09-11 — ChatGPT — dual-track regulatory and model audit

**Did:** Verified both 14:35 CCDL screenshots; reconciled the observation log; checked current NSE ESM/ST-ASM and Zerodha RMS/charge sources; inspected the liquid-momentum screener.
**Found:** CCDL showed zero bids, 17,730,606 shares offered at ₹1.32 and 20,538,961 total offers against 760,155 day volume. The 23.325 ratio is a queue-stress observation, not a calibrated probability. Track 2's market-cap floor—not EQ/index membership—keeps new candidates outside current ESM scope. ST-ASM can still apply. Non-CAS equity MIS square-off is currently 15:25; margin is at least 20% or VaR+ELM, whichever is higher; RMS/call-and-trade is ₹50 + GST per order. The screener is not deployment-ready: SL-Limit fills are not guaranteed, adaptive relaxation is a separate strategy, surveillance fails open, and minimum notional can breach the risk budget.
**Wrote:** `CHATGPT/dual_track_regulatory_review_2026-09-11.md`; appended the ineligible 11-Sep CCDL observation to `CHATGPT/observation_log.csv`.
**Needs:** Antigravity implement fail-closed surveillance/data lineage/risk sizing and discrete stop outcomes; Claude reproduce the ORB study and separately validate fixed versus relaxed populations; prospective observation collection must begin.
**Confidence:** High on screenshot transcription, arithmetic, current public NSE/Zerodha wording and code defects. Zero qualifying sessions/trades maintained; no core model changed.


## 2026-09-11 — ChatGPT — queue-ratio and PCAS calibration review

**Did:** Inspected and executed all seven named suites; recomputed ratios/PCAS arithmetic; answered queue curve, random-close and T−1 delivery questions.
**Found:** 27-Aug queue rank was copied from 25-Aug and is not observed; full-day volume is not price-specific post-order contra-volume; rho thresholds are fitted, not calibrated. PCAS divides volume equally, mislabels days as sessions, calls deterministic subtraction Bernoulli, treats project 15% cap as available liquidity and prices all shares at fractional-day decay. Audit checker exits successfully while reporting TypeErrors/unresolved defects, so “27/27 pass” is false. T−1 delivery is valid only as timestamped lagged feature.
**Wrote:** `CHATGPT/queue_pcas_calibration_review_2026-09-11.md`; posted shared challenge. No core models changed.
**Needs:** Antigravity replace probabilities with deterministic accounting plus unestimated forecast bounds; build per-auction replay; fix audit assertions; preserve exact source/rank/time fields. Claude independently review PCAS priority/carryover.
**Confidence:** High on code/arithmetic/identification defects; medium on the precise applicable auction carryover behavior pending versioned exchange source. Cash/zero-count stance maintained.


## 2026-09-11 — ChatGPT — revised volume/depth architecture audit

**Did:** Inspected and executed the revised signal/screener code, current BSE/live snapshots and claimed tick logger; answered the volume, liquidity and disclosure questions.
**Found:** Prior false-BUY cases are fixed and 16 revised unit assertions pass. Current live depth artifact remains stale/empty and no tick CSV exists; bridge can attach another symbol's depth to first watchlist symbol, treats one-sided depth as live and converts missing to zero. Band arithmetic improved but record validity ignores unknown surveillance/group and missing-band default. Intraday TTQ/full-day baseline ratios are clock-biased. Screener hard-codes 5% band and treats empty surveillance/default spread as clean. At-LC with positive traded volume is not proof of continuous zero-bid lock.
**Wrote:** `CHATGPT/volume_depth_architecture_review_2026-09-11.md`; posted shared challenge. No core models changed.
**Needs:** Antigravity implement the ten acceptance tests; Claude review position-specific queue/auction model; retrieve time-windowed SAST/bulk/PIT/encumbrance filings; Q10 broker-time answer.
**Confidence:** High on executable/static code findings and snapshot semantics; medium on BSE field-unit interpretations until raw API responses/schema are preserved. Cash gate and zero qualifying count endorsed.


## 2026-09-11 — ChatGPT — infrastructure and Rule 7 red-team audit

**Did:** Inspected Antigravity's synchronization note, browser launcher/bridge, live and band JSON, bhavcopy ingestor, signal/queue engine, screener and their executable tests. Ran two adversarial signal cases.
**Found:** System is an observation prototype, not institutionally sound. `LIVE_STREAMING` coexists with null depth/stats; band JSON is internally inconsistent/null on surveillance; `ESM_STAGE_1` and an at-UC-with-offers case both returned BUY; queue test uses invented rank and full-day volume; spread gate is absent; screener validation is synthetic; browser debugging profile introduces privileged-session risk. CHANDRIMA outcome remains unresolved rather than proven as either result.
**Wrote:** `CHATGPT/red_team_sync_response_2026-09-11.md`; posted shared challenge. No Antigravity/Claude file or core model changed.
**Needs:** Antigravity P0 fail-closed/data-quality/security repair; Claude payoff-tail challenge; broker Q10 response; CHANDRIMA contract note; independent replay and prospective testing.
**Confidence:** High on code/data counterexamples and arithmetic classification; medium on environment/network exposure until the active listener/firewall state is independently checked. Observation gate endorsed.


## 2026-09-10 — ChatGPT — audit of Claude external research submission

**Did:** Audited Claude's 247-line microstructure/surveillance research paper against current public exchange/broker sources and the model's required data grain.
**Found:** ESM circular correction and historical-source map are useful. Current Zerodha T2T article supports T+1 sale and does not support Claude's blanket T1 prohibition. CROPSTER's one-hour fill cannot identify `QUEUE_MULT≈1`; bhavcopy cannot estimate zero-bid duration; generic pre-open carryover does not establish security/broker eligibility. Several composite/secondary claims need traceable citations.
**Wrote:** `CHATGPT/claude_research_audit_2026-09-10.md` with claim-level dispositions and a model-ready data specification.
**Needs:** Claude correction; Zerodha written exact-time answer; order-book/event dataset; tri-agent review before any model parameter/rule changes.
**Confidence:** High on internal logical/data-grain defects and current Zerodha-page conflict; medium on claims whose underlying primary sources were not embedded in the submission. No core model changed.


## 2026-09-10 — ChatGPT — master alignment reconciliation and Q8/Q10

**Did:** Reconciled Yashu's three historical live trades; recorded proposed 1–2 day momentum hypothesis without altering core models or AGENTS.md.
**Found:** Two reported wins / one loss, aggregate −₹3,950 on mixed cost bases; CCDL fully closed at ₹1.38, +₹1,800 gross. Approximate CHANDRIMA/CROPSTER prices need reconciliation. Historical ESM/GSM notices exist; Friday-only immunity is unsupported by current linked NSE FAQ. Zerodha supports T+1 sale, but precise 09:00 XT eligibility remains unconfirmed. The −14.14% claimed model validation is a price-path comparison, not the cited simulation output.
**Wrote:** `CHATGPT/observation_log.csv` (schema v2, three retrospective trades plus repaired example, all paper-ineligible); archived original; added schema, `alignment_response_2026-09-10.md`, `zerodha_Q10_support_request.md`; updated shared trade log/questions and added CCDL confirmation to prior review.
**Needs:** Claude/Antigravity peer review of exit probabilities and tail assumptions; historical surveillance event extraction; user/broker written Q10 reply and optional tradebook/charges reconciliation. Support draft not sent. No new trade or core-model modification.
**Confidence:** High in arithmetic and cited policy distinctions; historical executions accepted as user-reported; exact fill timings, net P&L, queue calibration and strategy expectancy unverified. Validation separates these rather than silently resolving them.


## 2026-09-10 — ChatGPT — CCDL reported exit and new screenshots
**Did:** Inspected all three new 10-Sep screenshots and checked them against the user's confirmed entry (30,000 @ ₹1.32 on 9-Sep) and reported sale today.
**Found:** Actual displayed UC ₹1.38, LC ₹1.26, open ₹1.37. At 11:24 and 13:14, best bids remained ₹1.37 despite LTP later reaching ₹1.38. The 16:40 red −30,000 overlay is not a completed-order record. Full exit is user-reported; actual filled quantity/average price/time/charges need tradebook confirmation. If all sold at ₹1.38, gross profit ₹1,800 (+4.55%); not yet recorded as verified realised P&L.
**Wrote:** `CHATGPT/ccdl_exit_review_2026-09-10.md`, with source table, execution distinctions and conditional profit reconciliation.
**Needs:** Completed sell-order details and charges. Shared ACTIVE label is stale against the user's report; reconcile without assuming fill from the chart. Exclude this live sub-₹10 case from compliant prospective paper-validation counts. No core models or orders changed.
**Confidence:** High for screenshot transcription/math; sale completion user-reported, execution specifics pending.

## 2026-09-09 — ChatGPT — Whole-model audit
**Did:** Inspected all six core Python modules, project rules, shared and agent research/specifications, saved watchlist, paper log and screenshot evidence. Ran 31 isolated diagnostic probes, including valid controls; core-file SHA-256 hashes remained unchanged.
**Found:** Not ready for decision-making or scored signal-driven paper validation. Circuit branches crash; spread/invalid-data gates fail; risk budget is disconnected; exit detector promises liquidity with zero bids; simulator probabilities are unestimated and its timeline/exit accounting is invalid; band monitor loses repeated-day alerts; CSV has 35 header columns but 34 example fields and no non-example rows. Earlier Chandrima realised-P&L interpretation and CCDL arithmetic/execution claims require correction, including my own prior claims.
**Wrote:** `CHATGPT/model_audit_2026-09-09.html` (reader-facing audit, issue register, evidence corrections, repair order); `CHATGPT/audit_checks_2026_09_09.py` (reproducible synthetic diagnostics, not paper trades).
**Needs:** Tri-agent review of a single policy version and deterministic controls, then an event-time execution/cash ledger, source-backed data and prospective validation. No core fixes or orders were performed. Raw observations may continue; current simulated returns must not be treated as validated expectancy.
**Confidence:** High for reproduced code defects and recomputed arithmetic; historical trade profitability and predictive edge remain unverified.

*(none yet)*

## 2026-09-11 — ChatGPT — pre-Monday regulatory, RMS and logging audit
**Did:** Queried the official NSE corporate-action calendar for 12–26 Sep; reviewed the official 30-Sep NSE Indices reconstitution, DIPAM current receipts, and current Zerodha margin/RMS/CAS pages; inspected the live observation CSV.
**Found:** No requested symbol has an active NSE corporate action in the interval as of the query. None changes membership in Midcap 150/Smallcap 250, but SUZLON enters Next 100, RVNL exits Housing/PSE, and COCHINSHIP enters Nifty500 Multicap Infrastructure 50:30:20. COCHINSHIP’s 4.58% OFS is completed; no active PSU OFS found, while promoter lock-in expiry remains UNKNOWN. Zerodha shows 20%/5× for all eight as of 10-Sep, subject to dynamic RMS. CAS MIS square-off begins 15:12, so the internal exit is 15:10. CSV remains v2 with 0/60 sessions and 0/20 fills.
**Wrote:** `CHATGPT/pre_monday_regulatory_rms_audit_2026-09-11.md`, `CHATGPT/observation_log_schema_v3_proposal.md`, and `CHATGPT/monday_orb_paper_template.csv`. Preserved the production CSV pending Rule 8 peer review.
**Needs:** Claude/Antigravity approve the additive v3 schema; pre-open refresh Monday; replace every `UNSET`/`UNKNOWN` prospectively; never send a broker order under Rule 1.
**Confidence:** High for point-in-time exchange/broker results and repository count; medium on absence of promoter lock-in events because no consolidated official calendar was located. Zero capital and zero qualifying counts maintained.

```markdown
## YYYY-MM-DD — ChatGPT
**Did:**
**Found:**
**Wrote:**
**Needs:**
**Confidence:**
```
