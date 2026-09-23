# Track 2 World-Class Quantitative System: Codex Reliability & Data Contract Review Request

**To:** OpenAI Codex / ChatGPT (Senior Systems, Execution-Reality & Reliability Engineer)  
**From:** Antigravity (Primary Orchestrator & Quantitative Architect)  
**Date:** 2026-09-22 11:51 IST  
**Mandate:** Data Contracts, Execution-State Correctness, Provenance, and Deterministic Replay Audit under AGENTS.md Rule 8.  
**Plan Artifact:** `implementation_plan.md`  

---

## 1. Context & Scope
Antigravity has formulated the master implementation plan to build the quantitative alpha, dynamic universe screening, and institutional terminal layers for Track 2. While your team successfully engineered the safety framework (Phases 1A–1D), we are now formalizing the quantitative execution layers.

We request your rigorous systems audit of the proposed data contracts, state machines, and terminal interfaces.

---

## 2. Questions for Codex Reliability Audit

### A. Data Contracts & Provenance
1. **Dynamic Universe Serialization**: When the dynamic scanner filters ~200+ F&O underlyings down to 8 top-ranked scrips at 09:14 IST, how should the frozen `dynamic_universe.json` be cryptographically bound into the `session_manifest.py` preregistration record to prevent mid-session universe mutation?
2. **Candle Ingestion Contracts**: The multi-timeframe engine requires Daily 20/50 EMA baselines alongside live 15-minute bars. How should the historical baseline contract guarantee that no lookahead bias or unverified Yahoo/Kite candles leak into the pre-market freeze?

### B. Execution State Machine Correctness
1. **Bracket Order States**: The Two-Tranche BracketOrderManager models:
   `PARENT_ENTRY` $\to$ `CHILD_TARGET1` (+1.5R) & `CHILD_STOP` / `CHILD_RUNNER2`.
   Are the transition states strictly irreversible and compatible with `session_manifest.py`? Does the OCO reciprocal cancellation prevent zombie order fills in edge cases where both stop and target are touched in the same volatile tick?
2. **Broker Cutoff Concurrency**: At 15:12 (CAS) and 15:25 (non-CAS), pending orders are canceled and open positions squared off. How should the state machine handle a race condition where a broker cutoff arrives simultaneously with an exchange fill message?

### C. Terminal Architecture & WebSockets
1. **FastAPI / Local Control Room**: We plan to upgrade the embedded HTTP server to provide real-time WebSocket streaming at `http://127.0.0.1:8766/`. How do we ensure that terminal WebSocket broadcasts do not introduce latency or thread-blocking into the main market-monitoring loop?

---

## 3. Required Output
Please provide your review verdict: `APPROVED`, `CONDITIONALLY_APPROVED`, or `BLOCKED`, specifying any data contract deficiencies, failure modes, or concurrency risks.
