# ARGUS System Reference Handbook: Master Index & System Map

**Document Version:** 1.0.0  
**Effective Date:** 2026-09-30 15:30 IST  
**System Status:** PAPER TRADING ONLY (Rule 1 Mandatory Gate) | Strategies Passed: 0 | Real Capital: ₹0.00  
**Repository Workspaces:**  
- Operational Desk & Pipeline Root: `c:\Users\yashw\swing trades` (Branch: `docs/handbook`)  
- Quantitative Research Worktree: `C:\Users\yashw\swing-trades-track2` (Branch: `track2/decision-engine` frozen at `39a8661` / `0020e8c`)  

---

## 1. Executive Purpose

The ARGUS System Reference Handbook serves as the definitive, single-source operational map for Project Owner **Yashu** and collaborating autonomous agents (**Antigravity**, **Claude**, **Codex**). Every functional module, quantitative strategy, risk constraint, data pipeline, and open defect across the codebase is documented here with plain-English rationales, exact file-and-line citations, and verified evidence links.

### The Three Standing Invariants
1. **Rule 1 Paper Gate:** Real capital deployment is strictly prohibited. The system requires $\ge 60$ prospective trading sessions and $\ge 20$ realistically fillable entries with verified positive net expectancy before live capital is ever considered ([AGENTS.md:52-59](file:///c:/Users/yashw/swing%20trades/AGENTS.md#L52-L59)).
2. **Review Provenance:** A code modification, strategy, or pipeline fix is classified as "approved" or "fixed" **only** if explicitly recorded as `APPROVED` in [shared/trust/reviews.jsonl](file:///c:/Users/yashw/swing%20trades/shared/trust/reviews.jsonl). Commit messages or PR claims without an independent review record are classified strictly as `UNREVIEWED`.
3. **Track Isolation (Rule 11):** Track 1 (micro-caps / ESM) is permanently shelved. Track 2 (Liquid F&O underlyings) operates under strict universe guardrails: active F&O membership, `EQ` series, Daily Traded Value (DTV) $\ge$ ₹30 Cr, and zero ASM/GSM surveillance ([AGENTS.md:155-182](file:///c:/Users/yashw/swing%20trades/AGENTS.md#L155-L182)).

---

## 2. Master Table of Contents & Status Matrix

| Section | Handbook Document | Subsections & Key Topics | Owner | Current Status |
|---|---|---|---|---|
| **00** | [00_INDEX.md](file:///c:/Users/yashw/swing%20trades/shared/handbook/00_INDEX.md) | Master Table of Contents, System Map, Standing Invariants | Antigravity | **OK** |
| **01** | [01_FOUNDATION_messages_and_scheduling.md](file:///c:/Users/yashw/swing%20trades/shared/handbook/01_FOUNDATION_messages_and_scheduling.md) | Nexus inter-agent bus, HMAC authentication, process supervisor, Task Scheduler watchdog, daily status dashboard | Antigravity | **OK** |
| **02** | [02_DATA.md](file:///c:/Users/yashw/swing%20trades/shared/handbook/02_DATA.md) | Daily downloads (Job 0), surveillance ingestion (ASM/GSM), announcements backfill, 2005–2021 archive, BSE results filings, data integrity audits | Antigravity | **IN PROGRESS** |
| **03** | [03_SAFETY_AND_RULES.md](file:///c:/Users/yashw/swing%20trades/shared/handbook/03_SAFETY_AND_RULES.md) | AGENTS.md Rules 1–11, Rule 8 review ledger, security boundaries, 38-finding reconciliation table, branch discipline, Constitution status | Codex / Antigravity | **IN PROGRESS** |
| **04** | [04_STRATEGIES.md](file:///c:/Users/yashw/swing%20trades/shared/handbook/04_STRATEGIES.md) | Intraday failure analysis, Expiry Relief v2, PEAD v2, ORB-in-play, Short-Term Mean Reversion, rejected strategy register | Claude / Antigravity | **BLOCKED** |
| **05** | [05_MONEY_AND_RISK.md](file:///c:/Users/yashw/swing%20trades/shared/handbook/05_MONEY_AND_RISK.md) | Yashu's Adjusted A1 sizing parameters, code enforcement map, legacy ₹58,333 / ₹1,75,000 deprecation audit | Antigravity / Yashu | **IN PROGRESS** |
| **06** | [06_DECISIONS.md](file:///c:/Users/yashw/swing%20trades/shared/handbook/06_DECISIONS.md) | Chronological log of Yashu's sovereign decisions, verbatim transcripts vs agent interpretations | Yashu / Antigravity | **OK** |
| **07** | [07_OPEN_PROBLEMS.md](file:///c:/Users/yashw/swing%20trades/shared/handbook/07_OPEN_PROBLEMS.md) | Unified numbered defect register, severity, owners, reproduction tests, exact file:line citations | All Agents | **BROKEN** |
| **08** | [08_WHO_DOES_WHAT.md](file:///c:/Users/yashw/swing%20trades/shared/handbook/08_WHO_DOES_WHAT.md) | Tri-agent responsibility matrix (RACI), operational boundaries, peer review rules, conflict resolution | All Agents | **OK** |

---

## 3. Status Definitions

- **`OK`**: The subsystem or document is fully operational, verified against reproducible tests, and aligned with confirmed owner decisions.
- **`IN PROGRESS`**: Active development, downloading, or refactoring is underway on a dedicated branch with pending review.
- **`BLOCKED`**: Progress cannot proceed until external dependencies, upstream reviews, or data gaps are resolved.
- **`BROKEN`**: The component contains active defects, failing tests, or unhandled exceptions that prevent safe execution.
- **`UNREVIEWED`**: Code or configuration exists on a branch or commit but has not received an independent `APPROVED` verdict in [shared/trust/reviews.jsonl](file:///c:/Users/yashw/swing%20trades/shared/trust/reviews.jsonl).
