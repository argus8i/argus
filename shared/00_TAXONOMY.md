# ARGUS 8i: Platform Architecture & Taxonomy Reference
**Document Version**: 2.0.0  
**Status**: Authoritative Reference  
**Governance**: `AGENTS.md` (Tri-Agent Consensus: Antigravity + Claude + Codex)  

---

## 1. High-Level Taxonomy Hierarchy

```
ARGUS 8i (Parent Quantitative Platform)
│
├── TRIPWIRE       Track 1: Circuit & Micro-Cap Research Engine (10-day LC lockout)
│
├── BEACON         Track 2: Liquid Momentum System (Cash EQ, ₹2,50,000 corpus)
│   ├── SENTINEL   Universe discovery, F&O master ingestion, and pre-market screening
│   ├── VECTOR     Signal generation engine & 15m Opening Range Breakout (ORB)
│   ├── BASTION    Portfolio risk governor, ₹50,000 SEBI T+1 cash buffer, slot allocation
│   ├── SPLITLOCK  Two-tranche execution state machine (+1.5R split exit, breakeven runner)
│   ├── HELM       Clock-driven market session lifecycle orchestrator
│   └── CALIBER    Expectancy analytics, Sharpe validation, and Rule 1 qualification gate
│
├── NIGHTWATCH     Live operations terminal (dark-mode web console on port 8767)
├── BLACKBOX       Immutable evidence, audit logs, and SHA-256 state manifests
├── NEXUS BUS      Tri-agent consensus communication protocol (Antigravity ⇄ Claude ⇄ Codex)
└── VIGIL          System health, latency, and data-freshness watchdog (7 stages)
```

---

## 2. Component Directory & File Mapping

| Component | Role | Canonical File / Location | System Designation |
| :--- | :--- | :--- | :--- |
| **ARGUS 8i** | Entire Platform | `c:\Users\yashw\swing trades` | Sovereign Quantitative Platform |
| **TRIPWIRE** | Track 1 Micro-Cap Engine | `shared/track1_esm/`, `antigravity/models/circuit_execution_model.py` | Track 1 Research |
| **BEACON** | Track 2 Liquid Momentum | `shared/track2_liquid/` | Track 2 Execution |
| **SENTINEL** | Universe Discovery & Screening | `antigravity/daemons/track2_premarket_screener.py`, `track2_dynamic_universe_scanner.py`, `track2_surveillance_monitor.py` | Screening & Surveillance |
| **VECTOR** | 15m ORB Signal Engine | `antigravity/models/track2_alpha_engine.py` | `VectorAlphaEngine` |
| **BASTION** | Portfolio Risk & Sizing | `antigravity/models/track2_portfolio_risk_governor.py` | `BastionRiskGovernor` |
| **SPLITLOCK** | Two-Tranche Split Exit | `antigravity/models/track2_execution_model.py`, `track2_daily_paper_desk.py` | Split Execution Machine |
| **HELM** | Session Lifecycle Orchestrator | `antigravity/daemons/helm_session_orchestrator.py` | Market Session Engine |
| **CALIBER** | Expectancy & Rule 1 Analytics | `antigravity/models/caliber_performance_analytics.py` | Expectancy Analytics Engine |
| **NIGHTWATCH** | Live Operations Terminal | `antigravity/ui/terminal/index.html`, `antigravity/daemons/track2_terminal_server.py` | Web Terminal (:8767) |
| **BLACKBOX** | Audit & Evidence Ledger | `shared/track2_liquid/paper_orders.jsonl`, `events.jsonl`, `03_TRADE_LOG.md` | Immutable Audit Ledger |
| **NEXUS BUS** | Tri-Agent Protocol | `antigravity/logs/tri_agent_dialogue.*`, `launch_claude_bus.bat`, `launch_codex_bus.bat` | Tri-Agent Consensus Bus |
| **VIGIL** | 7-Stage Health Watchdog | `antigravity/daemons/vigil_watchdog.py`, `track2_live_monitor.py` | Real-Time Telemetry Watchdog |

---

## 3. Tri-Agent Responsibility Matrix

- **Antigravity**: Primary orchestrator, quantitative mathematical modeling, and execution automation.
- **Claude**: Microstructure analyst, adverse-selection testing, and quantitative red-teamer.
- **Codex / ChatGPT**: Senior Systems, Execution-Reality & Reliability Engineer: integration, data contracts, execution-state correctness, adversarial tests, reproducible verification, and regulatory provenance.

---

## 4. Track Isolation Mandate (Rule 11)
- **TRIPWIRE (Track 1)** and **BEACON (Track 2)** are completely decoupled.
- Cross-track contamination of rules, watchlists, order logs, or sizing formulas is strictly prohibited.
