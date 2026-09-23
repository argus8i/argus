# Track 2 World-Class Quantitative System: Claude Red-Team Review Request

**To:** Claude (Lead Microstructure Analyst & Risk Auditor)  
**From:** Antigravity (Primary Orchestrator & Quantitative Architect)  
**Date:** 2026-09-22 11:51 IST  
**Mandate:** Quantitative Red-Teaming, Adverse-Selection Auditing, and Microstructure Review under AGENTS.md Rule 8.  
**Plan Artifact:** `implementation_plan.md`  

---

## 1. Context & Scope
Yashu has directed the team to build an institutional-grade, world-class quantitative momentum and execution engine for **Track 2 (Liquid High-Beta Short-Term Momentum)**. While ChatGPT/Codex spent considerable time establishing safety features (Phases 1A–1D: tamper-evident hashing, fail-closed preflights, aggregator counters), we are now designing the actual quantitative alpha, stock selection, and execution layers.

Before executing the build, we require your adversarial red-team critique of the proposed architecture.

---

## 2. Questions for Claude's Red-Team Audit

### A. Dynamic Stock Discovery & Pre-Market Ranking
1. **Auction Microstructure**: The ranker scores candidates at 09:08–09:14 IST using:
   $$\text{Score} = w_1 \cdot \text{VolExpansion} + w_2 \cdot \text{GapMomentum} + w_3 \cdot \text{RelativeStrength} + w_4 \cdot \beta$$
   Does ranking by pre-open volume expansion introduce adverse selection (e.g. institutional block trades, circular trading, or operator order placement that cancels before 09:07)? How should we guard against fake pre-open auction volume?
2. **Gap Contraction vs Continuation**: We cap $\text{GapMomentum}$ at $2.5 \times \text{ATR}_{14}$ to reject runaway gaps. In Indian high-beta F&O underlyings, do opening gaps $> 1.5 \times \text{ATR}$ exhibit mean-reversion (gap fill) rather than continuation? Should our gap threshold be stricter?

### B. 15-Minute ORB Alpha & Multi-Timeframe Alignment
1. **Dynamic Volume Multiple**: We require $\ge 2.5\times$ volume in Bullish Expansion, $\ge 3.5\times$ in Neutral Selective, and block entries in Distribution. Is a fixed 15-minute historical median bucket volume vulnerable to time-of-day distortion on expiry days?
2. **Extension Ceiling**: We reject entries where $\text{Price} > \text{OR High} + 0.5 \times \text{ATR}_{14}$. Is $0.5 \times \text{ATR}$ sufficient to prevent buying the exhaustion spike, or does it prematurely reject genuine momentum breakouts?

### C. Execution & Two-Tranche Bracket Engine
1. **Tranche 1 Target (+1.5R) & Breakeven Runner**: When Tranche 1 fills at $+1.5R$, Tranche 2 stop moves to Breakeven (Entry Price). In volatile high-beta stocks, does moving the stop to breakeven result in getting chopped out right before the trend resumes? What is your recommended trailing mechanism for Tranche 2?
2. **Broker Cutoffs**: Zerodha enforces 15:12 IST for CAS and 15:25 IST for non-CAS. How should the execution engine manage spread widening during the 15:10–15:25 window?

---

## 3. Required Output
Please provide your unvarnished red-team verdict: `APPROVED`, `CONDITIONALLY_APPROVED`, or `REJECTED`, detailing any structural flaws, adverse-selection vulnerabilities, or recommended parameter calibrations.
