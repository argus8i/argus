# Tri-Agent Consultation Brief: Hybrid Execution, Broker Infrastructure & Terminal Evolution

**Date:** 2026-09-23  
**Orchestrator:** Antigravity  
**Reviewers:** 
- **Claude** (Lead Microstructure Analyst, Risk Auditor & Quantitative Red-Teamer)
- **ChatGPT / Codex** (Senior Systems, Execution-Reality & Reliability Engineer)  
**Governing Documents:** [`AGENTS.md`](file:///c:/Users/yashw/swing%20trades/AGENTS.md) (Rules 1, 4, 8, 10, 11), [`shared/00_PROTOCOL.md`](file:///c:/Users/yashw/swing%20trades/shared/00_PROTOCOL.md)

---

## 1. Executive Context & User Mandate
The user (Yashu) has delivered a firm, explicit directive:
> *"I am having a feeling that you are hastening up things. Don't do that. Take your time, consult the other two, and then build."*

This is an essential course correction. Hasty execution in quantitative systems creates hidden microstructure bugs, unhandled edge cases, and catastrophic operational risks. Per **Rule 8 (Tri-Agent Consensus Protocol)**, all architectural upgrades must undergo formal cross-agent peer review, adversarial stress-testing, and consensus reconciliation before any production code or trading rules are finalized.

We submit four fundamental architectural proposals for formal review and debate.

---

## 2. Topic 1: Execution Architecture — Co-Pilot vs. Autonomous vs. Hybrid

### A. The Core Proposal
We proposed a **Hybrid Execution Model** operating under strict Rule 1 (Paper Only):
1. **Co-Pilot Mode (Tier 2 / Medium Conviction):**
   - Signal triggers (15m ORB Breakout, Volume $\ge 2.5\times$ to $4.0\times$).
   - System sizes the trade strictly to ₹1,500 risk (1.0R) and checks Portfolio Risk Governor (max 3 slots, ₹50k cash buffer).
   - Generates an `ExecutionIntent` and dispatches an actionable card to **Telegram** (inline buttons `[ ✅ APPROVE & EXECUTE ]` and `[ ❌ REJECT ]`) and the **NIGHTWATCH Web Terminal** with a **90-second countdown timer**.
   - If the user taps approve within 90 seconds $\to$ routes order in < 15ms.
   - If 90 seconds elapse without action $\to$ intent expires **fail-closed** (signal discarded, zero capital risked).
2. **Autonomous Mode (Tier 1 / Highest Conviction):**
   - Requires exceptional alignment: Volume $\ge 4.0\times$, Nifty breadth confirmation ($A/D > 1.5$), and non-extended price ($< 0.5 \times \text{ATR}_{14}$ beyond OR High).
   - Machine routes the paper order in < 15ms without waiting for human tap, then immediately notifies user.
3. **Emergency Safeguards:**
   - Single-tap Emergency Flatten (`Shift + Esc` on terminal, or `/kill` on Telegram) cancels all pending intents and squares off all open positions immediately.

### B. Specific Questions for Reviewers:
* **To Claude (Microstructure & Trader Psychology):**
  1. Does a 90-second approval window introduce severe adverse selection? If the price runs up during those 90 seconds, will the user approve a stale order and buy the top of the 15-minute bar?
  2. Should Co-Pilot limit orders be pegged strictly to the breakout price (with an explicit maximum slippage tolerance of $+0.15\%$), automatically aborting if the market moves past the limit before approval?
  3. Does having an autonomous tier tempt the system into over-trading, or does it correctly eliminate human hesitation during the best momentum setups?
* **To ChatGPT / Codex (Systems, State Machine & Failure Modes):**
  1. How does the state machine handle network latency or dropped packets between Telegram webhook/polling and the local OMS?
  2. What happens if the user taps "Approve" at second 89.9, but the OMS receives the message at second 90.5? How is the race condition strictly resolved?
  3. How should intent replay and idempotency be enforced so a double-tap on Telegram never routes duplicate orders?

---

## 3. Topic 2: Data & Broker Infrastructure — DhanHQ WebSocket v2 vs. Zerodha Scraping

### A. The Core Proposal
Currently, Track 2 relies on an open Chrome browser running on debug port 9444 with JavaScript scraping the Kite DOM. 
The user has an active **Dhan** account. We proposed transitioning to **DhanHQ WebSocket v2 API**:
1. Zero cost (official API is free, unlike Zerodha's ₹4,000/month fee).
2. Headless binary WebSocket protocol (no browser tabs to keep open, no CDP disconnections, sub-15ms tick delivery).
3. Provides full 5-depth Level 2 order books and 15-minute bar aggregation.

### B. Specific Questions for Reviewers:
* **To Claude (Microstructure & Fill Reality):**
  1. Does Dhan's market depth stream provide true exchange-matching timestamps, or are ticks throttled?
  2. In liquid F&O underlyings in Cash EQ, does Dhan's order routing infrastructure exhibit noticeable execution drag compared to Zerodha or institutional DMA?
* **To ChatGPT / Codex (Reliability & Fail-Closed Mechanics):**
  1. If the Dhan WebSocket disconnects during market hours, what is the exact fail-closed watchdog protocol? (e.g., how many seconds of missed heartbeats trigger a `FEED_DOWN_FREEZE`?)
  2. How do we ensure strict **Track Isolation (Rule 11)** so that Track 1 (Micro-Caps on Port 9333 / BSE) and Track 2 (Liquid Momentum on Dhan) never contaminate each other's state files or processes?

---

## 4. Topic 3: Portfolio Sizing & Turnover Realities on ₹2,50,000 Capital Base

### A. The Numbers
* **Total Dedicated Capital:** ₹2,50,000 INR (100% Cash EQ, zero margin debt).
* **Risk Per Trade (1.0R):** ₹1,500 (0.60% of total capital).
* **Max Open Positions:** 3 concurrent trades (Total open risk cap = ₹4,500, or 1.80%).
* **Max Notional Allocation:** ₹2,00,000 (~₹66,000 max position size per stock).
* **Unencumbered Cash Buffer:** ₹50,000 (kept liquid to absorb SEBI T+1 80/20 delivery sale margin retention).
* **Expected Frequency:** 0 to 2 trade setups per day, 2 to 6 orders per day.

### B. Specific Questions for Reviewers:
* **To Claude (Friction & Implementation Shortfall):**
  1. With an average position size of ₹60,000, what is the exact friction hurdle (STT, exchange charges, SEBI turnover, GST, stamp duty, and half-spread bid-ask crossing) in basis points?
  2. Does a +1.5R target (₹2,250 gross gain on ₹1,500 risk) yield sufficient net expectancy after deducting round-trip frictional costs?
* **To ChatGPT / Codex (Settlement & Capital Recycling):**
  1. If Position 1 is closed at 10:30 AM, can the 80% credited delivery proceeds legally and technically fund Position 3 at 13:00 PM without triggering a peak margin penalty from the clearing corporation?
  2. What is the mathematical proof that the ₹50,000 cash buffer guarantees immunity from SEBI intraday margin shortfalls?

---

## 5. Topic 4: Institutional Quant Dashboard — Essential vs. Bloat

### A. The Benchmark
A deep research study compared VAJRA 8i against Citadel, Two Sigma, Jane Street, and Bloomberg PORT terminals, identifying:
- L2/L3 order book DOM ladders & queue drain.
- Real-time rolling factor covariance / correlation matrices.
- Post-trade Implementation Shortfall & Adverse Selection curves.
- Detachable multi-monitor panels (GoldenLayout).

### B. Specific Questions for Reviewers:
* **To Claude & ChatGPT / Codex:**
  1. Which of these institutional features genuinely protect capital and improve expectancy for a ₹2.5L retail momentum trader?
  2. Which features are merely visual distractions ("institutional cosplay") that add unnecessary CPU load and latency to the terminal?

---

## 6. Review Deliverables
Both **Claude** and **ChatGPT / Codex** are requested to provide comprehensive, independent, unvarnished written review memos to:
- `claude/PROGRESS.md` & `shared/reviews/claude_hybrid_execution_verdict_20260923.md`
- `chatgpt/PROGRESS.md` & `shared/reviews/chatgpt_hybrid_execution_verdict_20260923.md`

No core model or production execution code shall be updated until both reviews are synthesized, open questions are resolved, and Yashu approves the final design.
