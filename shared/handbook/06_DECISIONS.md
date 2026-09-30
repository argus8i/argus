# 06. Sovereign Owner Decisions & Provenance Chronicle

**Document Version:** 1.0.0  
**Effective Date:** 2026-09-30 15:30 IST  
**System Status:** **OK** (All 9 decisions documented with exact provenance and citations)  
**Primary Register:** [shared/governance/owner_decisions.jsonl](file:///c:/Users/yashw/swing%20trades/shared/governance/owner_decisions.jsonl)  

---

## 1. Plain-English Summary

In Project ARGUS, AI agents (Antigravity, Claude, Codex) design, build, test, and audit software, but **Project Owner Yashu makes all sovereign decisions**.

Agents are strictly forbidden from guessing, inventing, or assuming approvals regarding:
- Real capital deployment or risk limits
- Live broker connectivity or order execution
- Approval of external data sources or scraping policies
- Changes to constitutional governance

To prevent ambiguity, every decision in this chronicle is classified into one of two provenance tiers:
1. **Owner's Verbatim Words:** Exactly what Yashu typed or dictated, preserved without alteration.
2. **Agent Interpretation / Claim:** The derived engineering implementation of what the owner intended. Where documents or agents disagree, the verbatim quote always takes precedence.

---

## 2. Chronological Record of Yashu's Decisions

### Decision 1: Security Authorization & Permission Revocation (23 September 2026)
- **Context:** Claude's Red-Team Security Audit identified risks associated with automated browser debugging and permission-bypass flags.
- **Provenance:** **Owner's Verbatim Words** (Direct chat directive; codified into [AGENTS.md:3-13](file:///c:/Users/yashw/swing%20trades/AGENTS.md#L3-L13)).
- **What Was Decided:**
  - Revoked all permission-bypass flags (`--dangerously-bypass-approvals-and-sandbox`, `--dangerously-skip-permissions`).
  - Permanently prohibited Chrome remote debugging ports (`9333`, `9444`) and active browser credential scraping.
  - Mandated that all operations proceed under standard sandboxed execution.

### Decision 2: Adjusted A1 Portfolio Sizing Selection (25 September 2026)
- **Context:** Selecting between competing capital allocation proposals (A1, A2, B1) for Track 2 liquid trading.
- **Provenance:** **Owner's Verbatim Words / Decision Record** ([`shared/CLAUDE_ACCOUNT_HANDOFF_2026-09-23_TO_27.md:35`](file:///c:/Users/yashw/swing%20trades/shared/CLAUDE_ACCOUNT_HANDOFF_2026-09-23_TO_27.md#L35)).
- **What Was Decided:**
  - Selected **Adjusted A1**: ₹2,50,000 corpus, 3 concurrent slots, ₹38,000 slot cap, ₹1,14,000 aggregate exposure cap (active positions + pending orders), ₹1,36,000 mandatory cash buffer, and ₹1,500 planned risk budget per trade.
  - Specified that ₹12,000 represents a −10% stress scenario loss budget, not a guaranteed stop loss.

### Decision 3: Results First Principle & CNC Swing Approval (26 September 2026)
- **Context:** Intraday strategies failed after costs. Research proposed testing multi-day swing holds (1–5 days) in cash delivery (CNC).
- **Provenance:** **Owner's Verbatim Words** (Codified into [AGENTS.md:15-32](file:///c:/Users/yashw/swing%20trades/AGENTS.md#L15-L32)).
- **What Was Decided:**
  - Approved CNC cash equity swing holds of 1–5 days for research and paper trading.
  - Established the **Results First Principle**: Take initiative inside technical boundaries, never stop at "blocked", and observe the Three Hard Red Lines (no block evasion, no credential scraping, never overstate results).

### Decision 4: Decision A — Track 2 Universe Scoping (27 September 2026)
- **Context:** Ambiguity over whether the ₹4,000–₹75,000 Cr market capitalization band applied to all Track 2 strategies or only ORB.
- **Provenance:** **Owner's Verbatim Words to Claude** ([`shared/CLAUDE_ACCOUNT_HANDOFF_2026-09-23_TO_27.md:36`](file:///c:/Users/yashw/swing%20trades/shared/CLAUDE_ACCOUNT_HANDOFF_2026-09-23_TO_27.md#L36), [AGENTS.md:173-176](file:///c:/Users/yashw/swing%20trades/AGENTS.md#L173-L176)).
- **What Was Decided:**
  - The ₹4,000–₹75,000 Cr market-cap band applies **exclusively to the ORB / intraday momentum family**.
  - All other Track 2 strategies are governed by active F&O underlying membership, `EQ` series, DTV $\ge$ ₹30 Cr, and zero ASM/GSM surveillance.

### Decision 5: Quality Precedence Over Speed (28 September 2026)
- **Context:** Discussion regarding an aggressive 7–10 day research timeline.
- **Provenance:** **Owner's Verbatim Words in Chat** ([`shared/TRACK2_COMPRESSED_BUILD_FREEZE_PLAN_2026-09-28.md:3`](file:///c:/Users/yashw/swing%20trades/shared/TRACK2_COMPRESSED_BUILD_FREEZE_PLAN_2026-09-28.md#L3)).
- **What Was Decided:**
  - Clarified that **strategy quality, robust statistical evidence, and cost survival matter far more than an arbitrary 7–10 day finish date**.
  - October dates represent review checkpoints, not a rushed commitment to deploy capital.

### Decision 6: BSE Results Source & Trading Session Definition (29 September 2026 06:30 IST)
- **Decision ID:** `OWNER-2026-09-29-01` ([`shared/governance/owner_decisions.jsonl:1`](file:///c:/Users/yashw/swing%20trades/shared/governance/owner_decisions.jsonl#L1))
- **Channel:** Direct chat input from Yashu to Claude.
- **Owner's Verbatim Words:**
  > *"Okay, approved the PSP result thing. Approved. No, one to five days are normal days, not trading days. Trading sessions, I mean."*
- **What Was Decided:**
  - Approved BSE official corporate results filings as the primary earnings announcement timestamp source for PEAD.
  - Clarified that "1–5 days" means **1 to 5 exchange trading sessions**.

### Decision 7: PEAD 5-Session Holding Period Cap (29 September 2026 06:40 IST)
- **Decision ID:** `OWNER-2026-09-29-02` ([`shared/governance/owner_decisions.jsonl:2`](file:///c:/Users/yashw/swing%20trades/shared/governance/owner_decisions.jsonl#L2))
- **Channel:** Direct chat input from Yashu to Claude.
- **Owner's Verbatim Words:**
  > *"sessions, and 5 for PEAD"*
- **What Was Decided:**
  - Post-Earnings Announcement Drift (PEAD) holding period is strictly capped at **5 trading sessions**. The draft 20-session hold is rejected and cannot be locked.

### Decision 8: NSE Announcements Backfill Approval (29 September 2026 21:30 IST)
- **Decision ID:** `OWNER-2026-09-29-03` ([`shared/governance/owner_decisions.jsonl:3`](file:///c:/Users/yashw/swing%20trades/shared/governance/owner_decisions.jsonl#L3))
- **Channel:** Direct chat input from Yashu to Claude.
- **Owner's Verbatim Words:**
  > *"approve backfill with Claude's 4 conditions"*
- **What Was Decided:**
  - Approved the NSE corporate announcements backfill subject to Claude's 4 conditions:
    1. Stop immediately on the FIRST 401/403/429 and latch the host for the day.
    2. Total NSE requests capped at $\le 150$/day; run 21:00–07:00 IST only, $\ge 4.0\text{s}$ apart.
    3. First night pre-flight proof: fetch `2024-11-13` and match row counts against `events/announcements.parquet`.
    4. Correct proposal: acknowledged that only 118 holdout stock-quarters lack results (not 1,134).

### Decision 9: Windows Task Scheduler Registration (30 September 2026 14:55 IST)
- **Decision ID:** `OWNER-2026-09-30-01` ([`shared/governance/owner_decisions.jsonl:4`](file:///c:/Users/yashw/swing%20trades/shared/governance/owner_decisions.jsonl#L4))
- **Channel:** Direct chat input from Yashu to Claude.
- **Owner's Verbatim Words:**
  > *"approved, tell antigravity to set up the scheduled task"*
- **What Was Decided:**
  - Approved registering the Nexus supervisor and 5-minute watchdog as Windows Task Scheduler tasks under user `yashw`.
  - Required reporting exact task definitions, commands, and empirical recovery proofs.

---

## 3. Provenance & Verification Classification Summary

| Date & Time | Topic | Provenance Category | Primary Source Citation |
|---|---|---|---|
| **2026-09-23** | Security & CDP Ban | Owner's Verbatim Words | [AGENTS.md:3-13](file:///c:/Users/yashw/swing%20trades/AGENTS.md#L3-L13) |
| **2026-09-25** | Adjusted A1 Sizing | Owner's Verbatim Words | [`shared/CLAUDE_ACCOUNT_HANDOFF_2026-09-23_TO_27.md:35`](file:///c:/Users/yashw/swing%20trades/shared/CLAUDE_ACCOUNT_HANDOFF_2026-09-23_TO_27.md#L35) |
| **2026-09-26** | CNC 1–5 Sessions | Owner's Verbatim Words | [AGENTS.md:15-32](file:///c:/Users/yashw/swing%20trades/AGENTS.md#L15-L32) |
| **2026-09-27** | Decision A (ORB Scope) | Direct Confirmation | [`shared/CLAUDE_ACCOUNT_HANDOFF_2026-09-23_TO_27.md:36`](file:///c:/Users/yashw/swing%20trades/shared/CLAUDE_ACCOUNT_HANDOFF_2026-09-23_TO_27.md#L36) |
| **2026-09-28** | Quality Over Speed | Owner's Verbatim Words | [`shared/TRACK2_COMPRESSED_BUILD_FREEZE_PLAN_2026-09-28.md:3`](file:///c:/Users/yashw/swing%20trades/shared/TRACK2_COMPRESSED_BUILD_FREEZE_PLAN_2026-09-28.md#L3) |
| **2026-09-29 06:30** | BSE Source & Hold Unit | Owner's Verbatim Words | `OWNER-2026-09-29-01` ([`owner_decisions.jsonl:1`](file:///c:/Users/yashw/swing%20trades/shared/governance/owner_decisions.jsonl#L1)) |
| **2026-09-29 06:40** | PEAD 5-Session Cap | Owner's Verbatim Words | `OWNER-2026-09-29-02` ([`owner_decisions.jsonl:2`](file:///c:/Users/yashw/swing%20trades/shared/governance/owner_decisions.jsonl#L2)) |
| **2026-09-29 21:30** | NSE Announcements Backfill | Owner's Verbatim Words | `OWNER-2026-09-29-03` ([`owner_decisions.jsonl:3`](file:///c:/Users/yashw/swing%20trades/shared/governance/owner_decisions.jsonl#L3)) |
| **2026-09-30 14:55** | Task Scheduler Registration | Owner's Verbatim Words | `OWNER-2026-09-30-01` ([`owner_decisions.jsonl:4`](file:///c:/Users/yashw/swing%20trades/shared/governance/owner_decisions.jsonl#L4)) |
