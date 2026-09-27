# Project Swing Trades: Autonomous Agent Ground Rules

## Security & Access Authorization — 23 September 2026 (Red-Team Hardened)

Yashu authorizes Claude, Codex and Antigravity to read, edit, build and test the
project under standard execution sandboxing. All permission-bypass flags
(`--dangerously-bypass-approvals-and-sandbox`, `--dangerously-skip-permissions`)
are permanently revoked per Claude Red-Team Audit (Findings F1-F4). Antigravity
remains the primary orchestrator. Browser remote-debugging ports (9333, 9444) and
active browser credential scraping are strictly prohibited. Rule 1 (paper only),
Rule 8 (core-model peer review), and Rule 11 still apply. Do not delete the
repository, .git, checkpoints, or unrelated user work. Do not use destructive
Git resets/clean operations. Report actual edits and tests.

These rules apply unconditionally to all AI agents (**Antigravity**, **Claude**, **ChatGPT**) collaborating in `c:\Users\yashw\swing trades`.

## Working Principle: Results First (Yashu, 26 September 2026)
- **Get the work done.** When something blocks the task, find another way that works and do it; do not stop at
  "blocked" or wait to be asked twice. Example to follow: Antigravity fetching the extra weekend sessions that
  Claude's audit found (2005-06-04, 2005-11-26) the same evening.
- **Take initiative inside the task.** Fix what you find, then report what you did. Ask Yashu only for decisions
  that are really his (money, approvals, new data sources), never for technical choices.
- **Three hard lines, kept for practical reasons, not ethics:**
  1. Never get around a block (VPN, proxy, hotspot, rotating IPs, other machines): it turns a short ban on Yashu's
     home connection into a long one and cuts off the daily data.
  2. Never use browser logins, cookies or remote-debugging ports (see the security section above).
  3. Never overstate results. Every number comes from a file or a command output, and "done" means verified.
     Yashu must be able to trust every report; that is the whole point of the trust system.
- Paper only (Rule 1) remains; Rule 8 below defines when review is required.

## How to amend and apply these rules (Codex draft, 27 September 2026; pending Yashu's approval)
- These rules are versioned operating controls, not permanent strategy assumptions. Any agent may propose a change
  with the conflict it resolves, its effect on research/paper/live activity, and a reproducible check. Antigravity
  integrates approved changes after independent review; unresolved dissent is recorded rather than hidden.
- Research, draft strategies, data collection, exploratory backtests, adversarial probes, and clearly labelled
  non-qualifying paper experiments may proceed without first completing Rule 1's observation milestone. Rule 8
  review applies before a changed core model is promoted as the canonical evidence-generating paper desk, not
  before someone can work on a branch or run a diagnostic. Unreviewed output must not be called qualified evidence.
- Yashu's explicit written approval is required to change the live-capital gate, risk budget, maximum loss,
  qualifying-evidence criteria, Rule 8 review point, track isolation or Track 1 price floor. Other technical
  wording changes require at least one independent agent review. No agent-written rule authorizes real orders;
  the broker-order path remains disabled until a separate written live-trading decision is in force.

---

## 1. Mandatory Paper-Trading Gate (Observation Only)
- **Constraint:** Real capital deployment is strictly prohibited.
- **Milestone for considering live trading, not for building or starting paper observation:** The system must
  complete a minimum of **60 prospective trading sessions** and log at least **20 realistically fillable entries**
  with verified positive net expectancy before any live trading is considered. Track 1 evidence stays in
  `CHATGPT/observation_log.csv` and `shared/track1_esm/`; Track 2 evidence stays in
  `shared/track2_liquid/paper/` and must identify its strategy version. Do not combine tracks or strategy versions
  to manufacture a passing result. A positive point estimate from 20 fills alone is not proof of an edge.

## 2. Absolute ₹10.00 Price Floor
- **Constraint:** Immediate disqualification of any security trading below **₹10.00**.
- **Rationale:** Sub-₹10 securities suffer from extreme tick-size distortion (at ₹0.82, one tick is 1.22%; at ₹1.32, it is 0.76%), near-permanent surveillance entrapment, and fatal liquidity evaporation.

## 3. Prohibition of Locked-Circuit Chasing
- **Constraint:** Never submit or recommend a buy limit order for a stock locked at Upper Circuit where offer quantity is zero or negligible.
- **Rationale:** Fills on locked upper circuits occur only when the operator is distributing ("buying the exit"). Fill probability and forward returns are negatively correlated by construction.

## 4. Execution Modeling (Track 1 queue states; Track 2 sleeve-specific fills)
- **Constraint:** Never assume deterministic or "guaranteed" fills (e.g. "ensures fill within 15–60 minutes").
- **Track 1 Required Architecture:** Track 1 simulators, backtests, and paper logs must model:
  1. `LOCKED_NO_BID`: Total Bids == 0 (or Total Offers == 0 on UC). Fill probability $\equiv 0\%$.
  2. `QUEUED`: Order accepted, waiting behind $R$ shares in FIFO queue.
  3. `PARTIAL`: Incoming turnover matches a portion of order quantity.
  4. `FILLED`: Broker-confirmed fill, or a conservative paper fill supported by post-arrival
     trade-through/quote-through or price-level queue-depletion evidence. Gross session volume alone does not
     establish that a specific order filled; inferred FIFO rank is an estimate, not a guarantee.
- **Track 2:** Each strategy version specifies its own conservative fill and missed-exit rules before evaluation.
  Daily OHLCV can support a scenario estimate, not an observed FIFO fill. Do not label a bar-based hypothetical
  exit as broker-confirmed or count a mere price touch as a realized fill. Qualifying prospective fills need the
  evidence standard stated in the reviewed strategy version; gaps, halts and price-band locks must be handled
  explicitly, with unresolved exits carried forward rather than silently closed.

## 5. 10-Day Lower-Circuit Risk Calibration
- **Constraint:** Position sizing must include an unbroken exit lockout of **10 consecutive lower-circuit sessions**
  ($-40.1\%$ scenario loss on 5% bands, $-18.3\%$ on 2% bands), calibrated from CROPSTER's verified descent.
  This is a stress scenario, not a maximum possible loss. Stop-losses must never be assumed to execute when
  bid depth is zero.
- **Formula & Scope:**
  $$\text{Divisor} = 1 - (1 - \text{band\_pct}/100)^{10}$$
  $$\text{Max Position Size} = \frac{\text{Rupees Willing to Lose Outright}}{\text{Divisor}}$$
  *(Calibrated strictly to 10 consecutive sessions: 5% band divisor is $0.401$; 2% band divisor is $0.183$).*
- **Track 1 Band Scope (Rule 11 Guardrail):** Applies exclusively to fixed circuit bands ($\{2\%, 5\%\}$). Any
  security with a band $>5\%$ (e.g. 10%, 20%) is refused and sizes strictly to 0 shares per Rule 11.

## 6. Surveillance Pre-Emption & Daily Band Monitoring
- **Constraint:** Run daily pre-open checks comparing today's circuit band against yesterday's using `antigravity/models/band_revision_monitor.py`.
- **Trigger:** Any band tightening ($20\% \to 10\%, 10\% \to 5\%, 5\% \to 2\%$) or classification under ESM Stage 1/2, GSM, ASM, or Trade-to-Trade (`BE`) triggers an immediate entry freeze and mandatory exit review/attempt. Record queued or unfilled exits; do not report the position as closed without fill evidence.

## 7. Pre-Circuit Accumulation Only (Rule 6 Setup)
- **Constraint:** Buy only during two-sided accumulation bases where 20-day volume is expanding $\ge 3\times$, spread is $< 1\%$, daily range is $> 3\%$, and a valid stop-loss can be placed. Placing a stop is not proof that it will execute after a circuit lock.
- **Exit Strategy:** Target pre-emptive profit exits ($+15\%$ to $+20\%$) taken into the Upper Circuit buyer queue on Day 3 or Day 4.

## 8. Tri-Agent Consensus Protocol
- **Constraint:** Cross-agent peer review is mandatory before promoting core-model changes into the canonical
  evidence-generating paper desk or counting their paper trades toward qualification. Exploratory coding,
  backtests, and non-qualifying paper diagnostics are allowed while review is pending, with their status explicit:
  - **Claude:** Microstructure, adverse-selection testing, and red-teaming.
  - **ChatGPT / Codex:** Senior Systems, Execution-Reality & Reliability Engineer: integration, data contracts, execution-state correctness, adversarial tests, reproducible verification, and regulatory provenance. May implement explicitly assigned changes; must not independently approve its own core-model changes.
  - **Antigravity:** Quantitative modeling, execution automation, and Bhavcopy pipelines.
  - Antigravity remains primary orchestrator and integration owner. Claude leads quantitative red-teaming. Specialization is responsibility, not a prohibition on finding defects outside one's specialty. Review-only dispatches remain read-only tasks; implementation assignments must specify file ownership to prevent concurrent overwrites. Report evidence and unresolved dissent, not approval by majority vote.
  - **Tri-Agent Operational Standards (Rule 8 v2 Acceptance Gates):**
    1. **Test-First Acceptance Gate:** Reviewer adversarial probes must be written or formalized as failing regression tests before fixes are merged or claimed complete.
    2. **Fail-Closed Default Invariant:** Every safety switch, risk cap, and shadow isolation flag must default to ON / active / fail-closed (`enforce_slot_cap=True`, `allow_shadow=False`, `is_surveillance=False`, `is_fno_underlying=True`). Explicit caller opt-in must never be required for baseline safety.
    3. **Empirical Evidence Invariant:** Verification claims require exact reproduction artifacts (terminal command, exit code, and raw unedited stdout/stderr). Generic assertions like "all issues resolved" without command output are invalid per se.
    4. **Branch Isolation & Merge Gate:** Core model edits (`antigravity/models/`) require peer review on dedicated feature/audit branches before being fast-forwarded to `main`.

## 9. Liquidity & Market Participation Sizing Gate (Claude Specification)
- **Constraint:** Position size must never exceed **15% maximum participation** of realistic daily volume over a 2-session clearable horizon.
- **Formulas:**
  $$\text{Daily Fill Fraction} = \min\left(1.0, \frac{0.15 \times \text{Daily Volume}}{\text{Position Shares}}\right)$$
  $$\text{Sessions to Exit} = \frac{\text{Position Shares}}{0.15 \times \text{Daily Volume}} \le 2.0 \text{ sessions}$$
  $$\text{Max Combined Position Size} = \min\left(\frac{\text{Rupees Willing to Lose Outright}}{0.40}, 2 \times 0.15 \times \text{Daily Volume} \times \text{Price}\right)$$
- **Rationale:** At CHANDRIMA's 10-Sep volume of 6,355 shares, a 4,500-share position represented **70.8% of the entire day's turnover**. At that participation, you are not trading into the market; you ARE the market, creating your own adverse-selection liquidity trap.
- Daily-volume participation is a planning cap, not evidence that two sessions will actually contain enough
  contra-side liquidity to exit. Rule 4 controls the fill claim.

## 10. Strict Precedence Hierarchy of Track 1 Execution Gates
- **Constraint:** When market events cause rules to fire on overlapping states with conflicting instructions, the following strict hierarchy governs:
  1. **Rule 1 (Observation Only):** 100% Cash; zero real capital.
  2. **Rule 6 (Surveillance Pre-emption & Band Cut):** Immediate entry freeze and mandatory exit attempt. **Strictly overrides Rule 7.** If a stock enters via Rule 7 on Day 1, but receives a band cut ($20\% \to 10\%, 10\% \to 5\%$) or surveillance flag on Day 2/3, Rule 6 mandates attempting an exit into the earliest available liquidity. Holding to wait for Day 3/4 targets is prohibited; an unfilled attempt remains an open position under Rule 4.
  3. **Rule 2 (Absolute ₹10.00 Floor):** Immediate disqualification.
  4. **Rule 9 (Liquidity Participation Gate):** Rejection of any trade requiring $>2$ sessions to exit at $\le 15\%$ participation.
  5. **Rule 3 & 4 (No Locked UC Chasing & Discrete Execution):** Never chase locked circuits; model non-deterministic fills.
  6. **Rule 7 (Pre-Circuit Accumulation Breakout):** Entry allowed only when Rules 1–6 and Rule 9 pass.

## 11. Absolute Track Isolation (ESM Micro-Caps vs. Liquid Short-Term)
- **Constraint:** All agents must unconditionally treat **Track 1 (ESM / Circuit Micro-Caps)** and **Track 2 (Liquid Multi-Strategy Research and Paper Desk)** as two independent, decoupled quantitative systems. Cross-track contamination of rules, watchlists, logs, or sizing is strictly prohibited.
- **Track 1 Guardrails (ESM & Circuit Micro-Caps):**
  - Governed by Rules 2, 3, 4, 5, 6, 7, 9, 10.
  - Applies exclusively to micro-caps ($\text{Mcap} < \text{₹500 Cr}$) under fixed circuit bands (2%, 5%) and surveillance (ESM Stage 1/2, PCAS, T2T).
  - Never assume continuous liquidity or deterministic stop-loss execution. Sizing is governed by Rule 5 (10-day LC lockout) and Rule 9 (15% volume cap).
  - Dedicated storage: `shared/track1_esm/` and `CHATGPT/observation_log.csv`.
- **Track 2 Guardrails (Liquid Multi-Strategy Research and Paper Desk):**
  - ORB is one strategy sleeve, not the definition of Track 2. Each sleeve must have its own versioned,
    pre-registered entry, exit, eligibility and cost rules. Shared controls include cash-equity market mechanics,
    executable-exit modeling, surveillance checks and a strict ₹1,500 planned risk budget per trade.
  - Applies exclusively to active F&O underlyings (`EQ` series, DTV $\ge$ ₹30 Cr, not under ASM/GSM).
  - The Mcap ₹4,000–₹75,000 Cr band is provisionally scoped to the ORB / intraday-momentum family only;
    the claimed Yashu decision A provenance still needs owner confirmation. Every other Track 2 strategy states
    its universe in its locked pre-registration; the F&O, EQ, DTV and ASM/GSM conditions above apply to all of them.
  - **Surveillance Boundaries & Safeguards:** ESM Stage 1/2 and PCAS auction restrictions do not apply to Track 2 (ESM is bounded to Mcap < ₹1,000 Cr). However, general ASM/GSM screening and daily F&O-membership verification remain mandatory (`is_surveillance: False` and `is_fno_underlying: True` enforced fail-closed in engine). If a scrip exits F&O or enters ASM/GSM, it is immediately disqualified from Track 2.
  - Do not mechanically apply Track 1 ESM rules or 10-day LC sizing to F&O underlyings. Each sleeve's fill model
    states how broker rejects, halts, dynamic-band locks and missing counterparties are treated; liquidity is
    observed, never guaranteed.
  - Strictly prohibited from applying Track 2 continuous stop-loss assumptions to Track 1 micro-caps.
  - Dedicated storage: `shared/track2_liquid/`; qualifying paper journals live under
    `shared/track2_liquid/paper/`. `CHATGPT/monday_orb_paper_template.csv` is a legacy ORB template, not the
    qualification ledger for every Track 2 strategy.
