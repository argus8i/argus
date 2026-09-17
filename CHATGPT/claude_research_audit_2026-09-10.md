# ChatGPT audit — Claude micro-cap research submission

**Date:** 10 September 2026  
**Source reviewed:** `C:/Users/yashw/.codex/attachments/4f05c725-7255-415f-a4c9-b1d25ae32c6d/pasted-text.txt`  
**Decision:** **Useful research map; needs revision before model use.** No core model parameters or project rules were changed.

## What is solid enough to retain

1. **ESM circular correction:** BSE notice 20230718-46 and NSE/SURV/57609 are the relevant July 2023 ESM update identifiers. Current exchange material continues to reference them. Stage II uses Trade-to-Trade, a 2% band, 100% margin and Periodic Call Auction on all trading days.
2. **ESM criteria and notice lead time:** The July 2025 NSE FAQ supports the Stage-I/Stage-II criteria quoted in broad outline and says notices are typically issued after market hours one trading day before effectiveness. It also says weekly stage review in its lower-stage/exit section; this still does not establish a universal Friday-only escalation schedule.
3. **Historical surveillance events are reconstructible:** Exchange circulars/notices plus annexures are appropriate event sources. Claude correctly labels the actual ESM lead/lag distribution as a gap rather than claiming a result.
4. **The empirical questions remain open:** India-specific UC continuation, price-specific exit-queue fills, zero-bid incidence and ESM Stage-II outcomes need a purpose-built dataset. Adjacent-market or liquid-index intraday studies are not calibrations for locked Indian micro-caps.
5. **Identifier correction:** BSE 539091 is CCDL/Consecutive Commodities, not Contil India. This should be enforced in future joins.
6. **Legal caution:** The located enforcement examples distinguish ordinary unconnected buyers from alleged promoters, disseminators, volume creators and connected offloaders. This is useful descriptive context, not a legal safe harbour or individualized legal advice.

## Material corrections required

### 1. Zerodha conclusion contradicts its current T2T article — high impact

Claude quotes both “sell ... on the next trading day (T+1)” and an asserted blanket BTST prohibition, then concludes all T2T/GSM/ASM T+1 exits are RMS-blocked until BO-account credit. The current Zerodha article at review time says T2T stocks may be sold on T+1 and explicitly discusses sale of T1 holdings; the located current page does not contain Claude's quoted blanket prohibition.

Supported conclusion: **T+1 day is permitted under the current public policy.** Still unresolved: the precise time on T+1 for BSE XT, whether 09:00 pre-open/AMO routing is accepted, and security/session exceptions. CCDL's reported T+1 sale is consistent with the public article but lacks an exchange timestamp, so it does not settle 09:00.

Required change: strike recommendations 4 and TL;DR finding 4 as written. Keep Q10 open until Zerodha gives a dated, attributable reply. Do not turn an older support-page statement into current policy without an archived URL and effective date.

### 2. `QUEUE_MULT = 1.0` does not follow from CROPSTER — high impact

A 12,560-share order filling within approximately one hour says only that enough matching demand reached that price and the order's position during the reported interval. It does **not** reveal:

- shares ahead at acceptance;
- cumulative executable buy volume after acceptance;
- cancellations ahead;
- partial-fill timestamps;
- whether 15.7M daily volume occurred before or after the fill; or
- how Claude's abstract `QUEUE_MULT` maps to a price-level queue.

The calculation `12,560 / 15.7M = 0.08%` is a size-to-full-day-volume ratio, not “the amount of queue ahead that drained” and not an effective multiplier. Therefore neither `QUEUE_MULT≈1` nor a directional update from 2 toward 1 is identified by this observation. Record it as one successful full exit with censored rank information, not a quantitative prior anchor.

### 3. Bhavcopy cannot measure zero-bid frequency — high impact

Claude recommends counting zero-volume or low-volume scrip-days from bhavcopy to estimate `ZERO_BID_RATE`. Zero traded volume and zero displayed bids are different states; positive daily volume can coexist with a zero-bid snapshot, as the CROPSTER example itself shows. A zero-bid frequency requires time-stamped order-book snapshots or exchange message data. Bhavcopy can measure zero-trade days and OHLC/turnover, not bid-state duration.

### 4. Generic pre-open carryover is not proof of eligibility — medium/high impact

Even if unmatched pre-open limit orders retain time priority when carried into continuous trading, that does not show that a particular BSE XT/ESM security participates in that pre-open mechanism or that Zerodha accepts the holding-based sell at that time. Eligibility, session architecture, order acceptance, price-time rank and fill are separate variables. Do not translate generic pre-open documentation into a 09:00:01 exit advantage for all target securities.

### 5. “All scrips ... except derivatives” is overbroad — medium impact

The wide-band discussion collapses ordinary fixed bands, dynamic F&O bands, surveillance bands, T2T classification and call-auction eligibility into one sentence. These are overlapping regimes, not a clean complement. Build a daily security-state table from authoritative exchange fields; unknown or stale classification must block eligibility.

### 6. Secondary and mixed-source claims need traceable citations — medium impact

The pasted paper names studies, legal orders, a 35.8M-share CROPSTER sale, historical prices, GSM/ASM tables and penalty mechanics but does not supply a numbered bibliography or direct source link for every figure. Several GSM/ASM tables are expressly composites, and the ASM numeric criteria were not retrieved in primary form. These belong in an evidence queue, not the executable strategy policy, until the exact source, publication date and rule version are stored.

### 7. Delivery percentage is a feature hypothesis, not a rule — medium impact

The suggested delivery-collapse threshold near 20% is not established for these securities. A low delivery ratio may signal churn, but using it as a discriminator requires prospective or held-out testing with a predeclared threshold. Do not import the 0.37% ownership-change observation from an enforcement case as calibration for unrelated names.

## Model-ready disposition

| Submission item | Disposition |
|---|---|
| Correct ESM circular IDs and Stage-II mechanics | Accept with current-version checks |
| Historical notice/annexure source map | Accept; extraction/testing outstanding |
| BSE sub-₹10 tick/band rule | Accept only after storing the exact current master-circular page and applying exchange-specific/date-specific logic |
| T2T T+1 blocked until demat credit | Reject as current blanket claim; Q10 remains open |
| `QUEUE_MULT=1.0` prior from CROPSTER | Reject; observation does not identify the parameter |
| Bhavcopy-derived `ZERO_BID_RATE` | Reject; wrong data grain |
| 09:00 early order maximizes all target priority | Unverified; needs broker and security-session evidence |
| UC continuation / ESM trajectory statistics | Correctly labelled missing; build dataset |
| Delivery <20% warning | Research hypothesis only |
| Enforcement treatment of ordinary buyers | Context only; not legal assurance |

## Next research specification

For each prospective paper event, store: exchange security ID/ISIN; series; all surveillance states; band; trading session type; broker eligibility state/time; authorization state; order acceptance/exchange acknowledgement time; limit price; displayed quantity ahead at that exact price; subsequent executable contra volume by timestamp; cancellation inference where observable; partial fills; final fill; remaining quantity; and charges. Preserve `LOCKED_NO_BID`, `QUEUED`, `PARTIAL`, and `FILLED` as outcomes rather than assuming one from daily volume.

For surveillance lead/lag, extract every inclusion, upstage, downstage and exit event from annexures with publication and effective dates. Join adjusted daily prices without look-ahead, define “top” before testing, use comparable controls and report distributions/out-of-sample results. Until then, monitoring is a safety control, not a measured predictive edge.

## Reconciliation with current project state

- Portfolio remains user-reported 100% cash.
- CCDL remains closed: 30,000 at ₹1.38, ₹1,800 gross, exact fill time and charges unknown.
- Three reported live trades remain retrospective and contribute zero paper-gate observations.
- The proposed 1–2 day quick-momentum strategy remains a hypothesis. Claude's submission does not establish its continuation probability, queue-fill distribution, net expectancy or maximum loss.
- Existing AGENTS.md gates remain controlling. Any proposed core-model parameter change requires tri-agent peer review.
