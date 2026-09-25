# Erratum: `deep_mathematical_strategy_research_20260925.md` (commit fbc668b)

Author: Claude (Rule 8 red team), 25 Sep 2026. **The original document is not edited.** This note records disagreement, as Rule 8 requires, and does not overwrite it.

## Summary

The document calls itself a "Peer-Audited Mathematical Specification". No peer audit took place.

It reports win rates, expected R, Sharpe ratios, correlations, drawdowns and an "ensemble DSR of 0.96" for strategies that had **zero trades** in any repo dataset. Where the repo does have trades, its numbers contradict the measured results. **Use no number from it.** Treat its strategy descriptions as hypotheses only.

## Claims vs evidence

Measured values are from `research/evidence/MEASURED_FACTS.md` (re-run 25 Sep 2026; 8 stocks, 32 sessions, trigger-basis R). Derived references are to `research/notes/PLAN.md`, Appendix A.

| Claim in the document | Measured or derived |
|---|---|
| ORB is Tier 1; E[R] +0.43R; Sharpe 1.72; "P(T2 given T1) ≈ 0.55" | ORB through production code: −0.003R [−0.20, +0.22], n = 41. First ORB breaks: −0.084R, n = 103; measured q = P(T2 given T1) = 1/10 |
| COMPASS is Tier 1; p ≈ 52%; +0.26R | −0.095R [−0.27, +0.11], n = 17 |
| VWAP_RECLAIM is Tier 2; +0.19R | −0.258R [−0.45, −0.05], n = 89. The whole interval is below zero |
| VOL_SQUEEZE +0.21R | −0.530R [−0.79, −0.27], n = 13 |
| Win rates, Sharpes, correlations and drawdowns for PAIR, GEX_FLIP, MAX_PAIN, PEAD, STATARB and MLOFI | These strategies have never produced a trade in this repo, so none of these numbers is a measurement |
| Ensemble DSR 0.96, "99% confidence" | Not computed from any return series. A DSR needs trades and an honest trial count (Bailey & López de Prado 2014; plan A.14) |
| "Loss_max = 3 × ₹1,500 = ₹4,500 … zero ruin risk" | ₹4,500 is the planned risk to the stop, not a maximum loss. Gap-throughs, unfilled SL-limit orders and price-band freezes can exceed it (plan P6.7) |
| STATARB_PAIRS as a core strategy | Whether a pair beats trading the dislocated leg alone depends on the realised reversion μ and the cost (plan A.12). Several example pairs sit outside the ₹75,000 Cr cap, and TATAMOTORS now trades as TMPV after the Oct 2025 demerger |

## What would change this assessment

Pre-registered holdout evidence (plan P7) for a specific strategy version, followed by E2/E3 shadow evidence evaluated by `research/decision/promotion.py` (plan P6.6). Until then, every strategy in the document is `UNVERIFIED` in `research/decision/register.json`.
