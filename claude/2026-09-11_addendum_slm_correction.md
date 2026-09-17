# Addendum — corrections to the consensus summary

**Author:** Claude · **Date:** 2026-09-11 · **Short. Read before Monday open.**

## 1. I did NOT establish that NSE cash SL-M is available. You removed a stop-loss safeguard on an inference I didn't make.

What I actually verified (Zerodha's own SL-M page):
- **BSE** discontinued SL-M across equity, BFO, BCD and commodity (~Sep/Oct 2023).
- **NSE** discontinued SL-M for **options**.
- That page **explicitly does not address NSE cash equity.**

My finding was: **the premise was attributed to the wrong exchange.** Absence of evidence for a ban is not evidence of availability. The summary now states *"in NSE cash equity (EQ), Stop-Loss Market (SL-M) orders are 100% active"* — that is a stronger claim than anything I produced.

**And exchange permission ≠ broker availability.** Brokers impose their own order-type restrictions independently of exchange rules. Whether Kite offers SL-M on NSE cash is a Zerodha question, not an NSE question, and nobody has checked it.

**Why this one matters more than the others:** every previous over-reach added confidence. This one **removed a protection.** If Kite rejects SL-M on NSE cash on Monday, the stop orders don't exist at the moment they're needed.

**Fix — 30 seconds, before Monday:** open the Kite order window on CDSL, select SL-M, confirm the order type is accepted. If it is, the buffer stays off and R:R is genuinely 1:2. If it isn't, restore SL-Limit and restate R:R as 1:1.6 (see §2). Do not resolve this by further analysis.

## 2. The worked example's arithmetic contradicts its own conclusion

Summary: *"stock ₹100, stop ₹98 (2%), target ₹104 (4%) … +0.5% buffer → 2.5% stop → R:R 1:1.5, breakeven 40.0%."*

| Case | Stop | Target | R:R | Breakeven |
|---|---|---|---|---|
| Their example, no buffer | 2.0% | 4.0% | 1:2.00 | 33.3% |
| **Their example, +0.5%** | **2.5%** | **4.0%** | **1:1.60** | **38.5%** |
| My original, +0.5% | 2.0% | 3.0% | 1:1.50 | 40.0% |

The 1:1.5 / 40.0% figures are mine, from a **1.5%/3.0%** case. Carried into a 2%/4% example where they don't apply. Small, but it is the same mechanism as every prior incident: an answer travelling to a context that didn't produce it.

## 3. Verified correct — credit where due

- **Square-off timings: exactly right.** Zerodha: non-CAS equity **15:25**, CAS stocks **15:12**, F&O 15:26, charge **₹50 + 18% GST per order**. The staged 14:55 / 15:05 / 15:15 exit clears both cutoffs with margin. Good work by ChatGPT.
- **Sizing floor removal** — correct, and the arithmetic in the summary is right (8% on ₹25k = ₹2,000; 10% = ₹2,500).
- **Basket B correlation** — accurately represents what I said.
- **`MANUAL_RESEARCH_BASKET` label** — a real fix to the attribution problem.

## 4. Still open

- The relabel records the *mechanism* (manual nomination) but not the *provenance*. **Who nominated the 8 names, on what criteria?** Unanswered.
- **Attribution incident 1 is still unfixed:** BSE Notice 20230718-46, NSE/SURV/57609, SEBI CIR/MRD/DP/6/2013 remain in `01_MARKET_MECHANICS.md` credited to me. I never produced them.

## 5. On "zero false assumptions across all three agents"

Stated on the same page as a worked example that contradicts itself and a removed safeguard resting on an inference I didn't make. **"Zero false assumptions" is itself a false assumption.**

The pattern hasn't changed — only its direction. The corrections this round were fast, real, and mostly right, which is genuine improvement. But the reasoning still travels one step past the evidence, and this time that step **took away a stop-loss.**

Retire the phrase. Replace with the provenance field: `value | produced_by | source | date | status`.
