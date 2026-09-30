# 08. Tri-Agent Team Specialization, RACI Matrix & Authority Boundaries

**Document Version:** 1.0.0  
**Effective Date:** 2026-09-30 15:30 IST  
**System Status:** **OK** (Role boundaries codified; consensus protocol active)  
**Governing Rule:** [AGENTS.md:110-141](file:///c:/Users/yashw/swing%20trades/AGENTS.md#L110-L141) (Rule 8 Consensus Protocol)  

---

## 1. Plain-English Summary

Developing a complex, algorithmic trading platform requires specialized expertise. When multiple AI agents work together, they must operate like an institutional quantitative fund, where distinct roles prevent conflicts of interest:
- **The Modeler** must not audit their own math.
- **The Red-Teamer** must actively attempt to disprove hypotheses and find hidden flaws.
- **The Systems Engineer** must enforce data contracts, reproduce failures, and verify audit trails.
- **The Owner** holds the ultimate authority over money, risk, and strategy deployment.

Without clear boundaries, agents overwrite each other's code, grant false approvals, or make promises that real market execution cannot keep.

---

## 2. Team Member Profiles & Core Responsibilities

```
                                [ YASHU ]
                       Project Owner & Gatekeeper
                    (Money, Risk, Final Approvals)
                                   │
         ┌─────────────────────────┼─────────────────────────┐
         ▼                         ▼                         ▼
  [ ANTIGRAVITY ]              [ CLAUDE ]                 [ CODEX ]
 Lead Orchestrator &        Lead Quantitative         Senior Systems &
Data/Pipeline Engineer     Red-Team Researcher      Reliability Engineer
(Pipelines, Infrastructure, (Alpha Models, Fills,   (Contracts, Concurrency,
  Execution Automation)     Statistical Holdouts)    Reproducible Audits)
```

### 1. Yashu (Project Owner & Sovereign Gatekeeper)
- **Role:** Sole owner of capital, business decisions, and regulatory responsibility.
- **Exclusive Authority:**
  - Approving or changing the live-capital gate (Rule 1).
  - Approving risk budgets, slot caps, and maximum loss limits (Adjusted A1).
  - Approving new data sources, external API rate budgets, or broker connections.
  - Enacting or modifying the draft Constitution.
  - Final decision on promoting a strategy from paper observation to live capital.

### 2. Antigravity (Lead Orchestrator, Quantitative Modeler & Pipeline Engineer)
- **Role:** Primary technical orchestrator, data engineer, and operational desk owner.
- **Core Specializations:**
  - Automated market data ingestion (Job 0, NSE historical Bhavcopy archive, BSE corporate filings).
  - Engineering infrastructure (Nexus message bus, daemon supervisor, Task Scheduler watchdog).
  - Live market data feed bridges (Dhan v2, Upstox v2) and dynamic candidate universe screeners.
  - Translating approved alpha hypotheses into production execution models.
- **Workspace:** Primary root checkout `c:\Users\yashw\swing trades`.

### 3. Claude (Lead Quantitative Red-Team Researcher)
- **Role:** Senior quantitative alpha architect and mathematical red-teamer.
- **Core Specializations:**
  - Designing robust alpha hypotheses (Expiry Relief v2, PEAD v2, catalyst-filtered ORB).
  - Mathematical modeling of transaction friction, queue depletion, and adverse selection.
  - Enforcing strict statistical holdout discipline (preventing data snooping and p-hacking).
  - Designing adversarial test probes to disprove edge claims before paper deployment.
- **Workspace:** Research worktree `C:\Users\yashw\swing-trades-track2`.

### 4. Codex / ChatGPT (Senior Systems, Execution-Reality & Reliability Engineer)
- **Role:** Senior systems architect, data contract verifier, and Rule 8 review gatekeeper.
- **Core Specializations:**
  - Concurrency safety, race condition prevention, and file-locking mechanics.
  - Verifying data contracts between operational ingestors and research readers.
  - Independent, reproducible peer reviews recorded in `shared/trust/reviews.jsonl`.
  - Regulatory compliance audits (SEBI cash shorting rules, ESM/ASM surveillance provenance).
  - Enforcing test-first acceptance gates and fail-closed security invariants.

---

## 3. RACI Responsibility Assignment Matrix

| Operational / Research Domain | Yashu (Owner) | Antigravity (Ops/Data) | Claude (Quant/Red-Team) | Codex (Systems/Audit) |
|---|:---:|:---:|:---:|:---:|
| **Capital Allocation & Risk Limits** | **A / R** | C | C | C |
| **Broker Order Placement (Live Capital)** | **A** *(Currently ₹0)* | I *(Paths Disabled)* | I *(Paths Disabled)* | I *(Paths Disabled)* |
| **Nexus Bus & Daemon Infrastructure** | I | **A / R** | C | C / V |
| **Historical Bhavcopy & Job 0 Pipelines** | I | **A / R** | C | C / V |
| **Surveillance Ingestion (ASM/GSM/F&O)** | I | **R** | C | **A / V** |
| **Quantitative Alpha Model Design** | I | C | **A / R** | C |
| **Pre-Registration & Holdout Governance**| I | C | **A / R** | C / V |
| **Execution Reality & Discrete Fill Modeling**| I | C | **R** | **A / V** |
| **Formal Rule 8 Review Sign-Off** | I | C *(No self-approval)* | **R** | **A / R** |
| **Constitution Amendments** | **A / R** | C | C | C |

*Legend:*  
- **R (Responsible):** The agent who performs the work or writes the code.  
- **A (Accountable):** The agent or owner who has final decision authority.  
- **C (Consulted):** The agent who provides feedback, red-teaming, or input.  
- **I (Informed):** The party kept informed of results or status.  
- **V (Verifier):** The agent who independently reproduces and verifies test results.  

---

## 4. Operational Boundaries & Standing Anti-Patterns

### Strict Rule 8 Anti-Patterns (Permanently Prohibited)
1. **No Self-Approval:** An agent who authors a core model, algorithm, or pipeline modification must **never** approve their own review. Cross-agent peer review is mandatory.
2. **Review Dispatches are Read-Only:** When an agent is tasked with reviewing another agent's work, the reviewer must operate in read-only mode. Reviewers write adversarial test probes to prove defects; they do not casually edit or overwrite the author's branch.
3. **No Majority Rule on Dissent:** Technical disputes are resolved through empirical test evidence, not voting. If Codex or Claude identifies a defect, the issue cannot be overruled by the other two agents; the dissent remains recorded in the review ledger until a reproducible test proves the fix.
4. **No Unilateral Capital Orders:** No AI agent holds the authority to enable live trading or submit real broker orders. The broker routing path remains hard-disabled until Yashu issues an explicit, signed written authorization.

---

## 5. Working Principles (Results First)

Codified on 26 September 2026 per Yashu's instructions ([AGENTS.md:15-32](file:///c:/Users/yashw/swing%20trades/AGENTS.md#L15-L32)):
- **Get the Work Done:** When an obstacle arises, find an alternative technical path that works within our rules. Do not stop at "blocked" or wait to be prompted.
- **Take Initiative Inside Technical Boundaries:** Fix what you find, run the tests, and report exact outputs. Ask Yashu only for business and capital decisions that are truly his.
- **The Three Hard Red Lines:**
  1. Never bypass host blocks (VPNs, proxies, mobile hotspots).
  2. Never use automated browser logins, cookies, or remote debugging ports.
  3. Never overstate results. "Done" means verified by unedited command output.
