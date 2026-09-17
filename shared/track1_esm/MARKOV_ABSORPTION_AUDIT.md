# Track 1 Markov Chain State Absorption & Circuit Sensitivity Audit

**Primary Empirical Source:** `antigravity/logs/track1_historical.db` (214,441 records across 120 sessions).  
**Scope:** Strictly Track 1 (ESM & Circuit Micro-Caps). AGENTS.md Rules 1-11 govern unconditionally.  
**Status:** Formal Tri-Agent Quantitative Debate Document for Claude & Codex.  

---

## Section 1: Empirical 5x5 Markov Transition Matrix

| State From | TWO_SIDED_BASE | LOCKED_UC | UNLOCKED_VOLATILE | LOCKED_LC | BAND_TIGHTENED |
|---|---|---|---|---|---|
| **TWO_SIDED_BASE** |     78.19% |      0.83% |     20.28% |      0.62% |      0.08% |
| **LOCKED_UC** |     23.35% |     44.88% |     26.45% |      5.32% |      0.00% |
| **UNLOCKED_VOLATILE** |     48.16% |      1.47% |     48.75% |      1.28% |      0.35% |
| **LOCKED_LC** |     24.15% |      9.26% |     26.28% |     40.32% |      0.00% |
| **BAND_TIGHTENED** |     41.85% |      7.38% |     42.15% |      8.62% |      0.00% |

---

## Section 2: Absorption Probabilities Across 10-Session Horizons

### A. Starting from Two-Sided Base (Pre-Circuit Setup)
- **P(Reaching Target +15% to +20%):** **4.13%**
- **P(Trapped in 10-Day LC Lockout):** **2.91%**
- **P(Aborted by Rule 6 Band Cut):** **1.39%**
- **P(Normal Multi-Day Base Hold):** **91.57%**

### B. Starting from Day 1 Upper Circuit (Momentum Acceleration)
- **P(Consecutive Lock to +15% Target):** **23.07%**
- **P(Trapped in Lower Circuit Lockout):** **5.12%**
- **P(Surveillance Band Tightening):** **1.00%**

---

## Section 3: Kyle's Lambda & Bid-Wall Fragility Calibration

| Case Study Stock | Day Volume | Displayed Bids | Fragility Ratio (Bids/Vol) | Spoof Risk Flag | Critical Dump Vol ($V_{crit}$) |
|---|---|---|---|---|---|
| **CCDL (11-Sep)** | 760,155 | 17,730,606 | 23.32x | 🚨 HIGH SPOOF | 20,390,197.0 shares |
| **CROPSTER (09-Sep)** | 27,385,897 | 19,360,931 | 0.71x | ✅ STRUCTURAL | 22,265,071.0 shares |
| **CHANDRIMA (10-Sep)** | 6,355 | 4,500 | 0.71x | ✅ STRUCTURAL | 5,175.0 shares |
| **MOBIKWIK (11-Sep)** | 892,396 | 125,000 | 0.14x | ✅ STRUCTURAL | 143,750.0 shares |
| **LOVABLE (11-Sep)** | 29,989 | 8,500 | 0.28x | ✅ STRUCTURAL | 9,775.0 shares |

---

## Section 4: Mathematical Directives for Claude & Codex Peer Review
1. **Adverse-Selection Red-Team:** Does taking profit exits into the Day 3/4 Upper Circuit buyer queue expose the trader to front-running by operator block dumps?
2. **State Absorption Invariance:** Does the 5x5 Markov matrix satisfy ergodicity, or do `LOCKED_LC` and `BAND_TIGHTENED` act as absorbing boundaries under AGENTS.md Rule 5 and Rule 6?
3. **Position Sizing Gate:** Prove whether the $0.401$ risk divisor provides sufficient tail-risk margin given the empirical 5.32% direct jump probability from `LOCKED_UC` to `LOCKED_LC`.

---

## Section 5: Claude Code Peer Review & Quantitative Verdict

**Reviewer:** Claude Code (Lead Microstructure & Red-Teaming)  
**Execution Timestamp:** 108.0s turnaround  
**Status:** Peer-Reviewed & Verified  

# Track 1 Markov / Microstructure Audit — Quantitative Peer Review

## Directive 1 — Adverse-Selection Red-Team: YES, exit risk is real and stock-specific

Taking profit into a Day 3/4 UC buyer queue is *not* uniformly risky — it is conditional on the Kyle's-Lambda fragility ratio, and the sample data actually proves both cases side by side:

| Signature | Ratio | Interpretation |
|---|---|---|
| CCDL | 23.32x | Displayed bid depth is **23x** the day's traded volume — the queue is almost certainly non-executable ("ghost") liquidity. Classic spoof/momentum-ignition pattern: operator stacks bids to pull in retail sellers, then cancels ahead of the queue and dumps into the resulting vacuum. A profit-take order queued behind that wall gets a partial or zero fill, and the subsequent gap-down is exactly the mechanism that produces LOCKED_UC→LOCKED_LC (5.32%) transitions. |
| CROPSTER / CHANDRIMA / MOBIKWIK / LOVABLE | 0.14x–0.71x | Bids are backed by real turnover ("structural"). Exiting into these queues carries normal execution risk, not front-running risk. |

**Verdict:** 1 of 5 sampled names (20%) shows an active spoof signature. This is high enough to require a hard **fragility-ratio gate**, not a judgment call: block or throttle profit-taking exits whenever displayed-bid/volume > ~3x, and route via partial scale-out or limit-inside-band instead of full-size market-into-queue. The 5.32% direct UC→LC jump probability in Section 1 is very plausibly *conditional on exactly this fragility regime* — the audit should segment the transition matrix by fragility-ratio bucket rather than pooling all UC states together, since pooling masks the fact that low-fragility UC exits are probably far safer than the blended 5.32%.

## Directive 2 — State Absorption: the audit's framing is backwards

Checked row sums (all ≈100%, confirming a valid stochastic matrix) and diagonals:

| State | P(i→i) |
|---|---|
| TWO_SIDED_BASE | 78.19% |
| LOCKED_UC | 44.88% |
| UNLOCKED_VOLATILE | 48.75% |
| LOCKED_LC | 40.32% |
| BAND_TIGHTENED | **0.00%** |

An absorbing state requires P(i→i) = 1. **No state in this matrix is absorbing** — not LOCKED_LC (40.32% self-loop, 59.68% exits out), and especially not BAND_TIGHTENED, which has a **0% self-loop**: it is the single most transient state in the system, guaranteed to transition away next session. The audit's Directive 2 has the math inverted.

Checking reachability: every state connects to every other state either directly or in ≤2 hops (e.g., LOCKED_UC→BAND_TIGHTENED is 0.00% direct but reachable via TWO_SIDED_BASE at 0.08%/step). Combined with TWO_SIDED_BASE's positive self-loop (period = 1), the raw 5-state chain is **irreducible and aperiodic — i.e., genuinely ergodic**, not absorbing.

**Where Rules 5/6 actually bind:** they don't change the empirical stock-state matrix at all — they impose an *exogenous trader-side constraint* layered on top of it. The stock itself reverts from LOCKED_LC to TWO_SIDED_BASE 24.15% of the time next session, but Rule 5 freezes the *trader* out for 10 sessions regardless. Correct modeling treats this as a 6th synthetic state (`LC_LOCKOUT_TRADER`) with a **deterministic 10-period hold before ejection**, decoupled from the underlying stock's own fast mean-reversion. Same for Rule 6's surveillance cooldown against BAND_TIGHTENED's 0% self-loop — the stock exits the band-tightened state almost immediately, but the *trader's* re-entry is throttled independently.

**Verdict:** Reject "absorbing boundary" framing. The matrix is ergodic; Rules 5/6 are absorbing constraints on the *execution layer*, not properties of the transition matrix. This distinction matters for sizing (Directive 3) — you cannot use stock-state stationary probabilities alone to size for a rule that operates on trader-state, not stock-state.

## Directive 3 — Position Sizing Gate: 0.401 divisor is adequate for the single-jump case, understated for the compounding case

Two-step occupancy check (probability of being in LOCKED_LC at t+2 starting from LOCKED_UC):

P₂(UC→LC) = 0.2335×0.0062 + 0.4488×0.0532 + 0.2645×0.0128 + 0.0532×0.4032 ≈ **5.02%**

This is close to the 1-step 5.32% and to the audit's own 10-session occupancy figure (5.12%), confirming the chain mixes fast — good news: the single-step 5.32% is a reasonable proxy for near-term entry risk, and it does *not* blow up under naive compounding of "ever reaching" LOCKED_LC.

**But the real tail risk is not entry probability — it's persistence probability.** LOCKED_LC's self-loop is 40.32%, meaning *conditional on already being locked*, the position has a large chance of re-locking limit-down again before Rule 5's 10-day freeze even becomes the binding constraint:

- P(2 consecutive LC days | first LC) = 0.4032² ≈ **16.3%**
- P(3 consecutive LC days) = 0.4032³ ≈ **6.6%**

If each circuit band represents a ~10–20% mark-to-market step, 2–3 consecutive LC days compounds to a **~25–35% drawdown before the position can be exited at all** — a materially different risk than the single 5.32% jump probability the audit tests the divisor against.

**Proof sketch:** if 0.401 is calibrated as a safety multiplier against the *single-event* entry probability (implied margin ≈ 1/0.401 ≈ 2.49x), expected loss per trade under that assumption is roughly P(entry)×single-band drawdown ≈ 0.0532×0.15 ≈ 0.8% of capital — comfortably inside a 2.49x buffer. Under the *persistence-chain* scenario, expected tail loss conditional on entry is:

E[drawdown | locked] ≈ Σₖ 0.4032^(k-1)×(1-0.4032)×(k×band_drop) 

which for a ~15% band and geometric persistence gives an expected multi-day drawdown noticeably larger than a single band-width — likely in the 20–25% range once you weight the 16.3% two-day and 6.6% three-day tails. That is not "sufficiently" covered by a 2.49x sizing multiple unless the position sizing formula also caps total notional against the ₹10 floor per Rule 2 independently of the divisor.

**Verdict:** **Not proven sufficient** as stated. The 0.401 divisor appears to have been stress-tested only against the marginal entry probability (5.32%), not against LOCKED_LC's own 40.32% self-persistence. Recommend re-deriving the divisor from the conditional persistence chain (P(k consecutive LC days) weighted by cumulative band drawdown) rather than the single-step transition probability, and confirming the exact 0.401 formula against AGENTS.md Rule 5 text directly — that derivation wasn't in the audit data provided, so this critique flags a gap rather than asserting the number is wrong outright.

---

## Consolidated Recommendations
1. Segment the transition matrix by Kyle's-Lambda fragility bucket before trusting any UC-related exit probability; gate exits at fragility ratio > ~3x.
2. Stop calling LOCKED_LC/BAND_TIGHTENED "absorbing" — model Rule 5/6 as a separate 10-period deterministic trader-state layered on the ergodic 5-state stock chain.
3. Re-derive/stress-test the 0.401 divisor against LOCKED_LC's 40.32% self-persistence (multi-day compounding), not just the 5.32% single-step entry probability, before treating it as validated tail-risk margin.
