# BEACON: execution audit and alpha-expansion specification

Author: Codex | 24 September 2026 | Track 2 only

## Verdict and how to read this report

**BLOCKED for live deployment and for treating unverified simulated exits as qualifying performance. Research expansion is conditionally acceptable.** This is an independent review, not tri-agent consensus. No production model, broker setting, qualification count or trading permission is changed by this report.

The mandate is useful as a research agenda, but several requested “proofs” are false. More strategies will not repair an unreliable fill ledger. Fix execution accounting, data contracts and end-to-end wiring first; evaluate additional alpha as separate, preregistered challengers.

Provenance convention: **MEASURED** = observed code/test output; **DERIVED** = mathematical consequence; **ASSUMED** = proposed research parameter, not an established NSE edge. Regulatory statements link to primary sources accessed 24 September 2026. HEAD during inspection: `93cebcb3a028fcae22682bf4b75d05bb1fb92644`; this is a shared, changing workspace. The unrelated `antigravity/logs/circular_poller.log` modification was left alone. The claimed 815-test baseline was not independently rerun in this review.

### Confirmed local defects and contradictions

| Finding | Evidence in current local code | Consequence / required repair |
|---|---|---|
| P0: quote-only stops invent completed exits | `track2_paper_execution.py`, `BracketOrderManager.update_bracket_quote`, stop branch around lines 619–645. A fresh 100-share bracket, entry 100, stop 95, quote 94, `execution_evidence=False` returns `STOPPED_OUT_FULL`, net P&L -510.28. | Emit a pending exit instruction; only validated incremental execution events may close inventory or realize P&L. |
| P0: time-only exit invents a profitable fill | Same method around lines 570–615. Entry 100, quote 102, 15:12 CAS, no evidence produces `CLOSED_MIS_SQUAREOFF`, net P&L +189.26. | A deadline is not evidence. Reconcile residual quantity and mark missed-flat incidents. |
| P0 before integration: unified signal loses explicit side | `UnifiedTradeSignal` has no side field. RECOIL side survives only in unstructured details; generic targets use long-side arithmetic as fallback. | Mandatory BUY/SELL side through signal, reservation, bracket, fees and ledger; short integration stays disabled until tested. |
| P1: COMPASS not evaluated | Constructor creates `compass_engine`, but `evaluate_symbol()` never calls it. | Cross-sectional point-in-time batch adapter and a reachable end-to-end test. |
| P1: ensemble not wired into the inspected daily desk | `track2_daily_paper_desk.py` imports `track2_orb_signal_adapter.evaluate_symbol`; repository search finds no daemon call to `MultiStrategyEngine`. | Built classes do not establish a seven-strategy operating desk. Prove the selected application launch route separately. |
| P1: four cost hurdles double count | TRAPDOOR line 230 and COMPASS line 263 multiply buy+sell turnover by a declared round-trip rate. LAST_LIGHT line 271 and RECOIL line 280 multiply notional × 0.00106 × 2. | One versioned, order-aware fee calculator, not four constants. Removing ×2 alone is an interim approximation. |
| P1: deadline mismatch | Executor uses 15:12 CAS / 15:25 other; blueprint says 15:10. | Shared exchange/broker/session policy and earlier staged liquidation. |
| P1: COMPASS missing data masquerades as observations | Empty peers become the candidate itself; missing market becomes return zero; beta defaults to 1 and clamps into [0.5,2.5]. | Reject missing peer/benchmark/beta evidence, enforce coverage and synchronized bar times. |
| P1: feature names overstate lookback completeness | Shared features calculate ATR from up to 20 bars including supplied latest bar; ER8 accepts shorter history; RVOL floors the denominator at 1000. | Specify history completeness and inclusion convention; no fabricated baseline for absent data. |
| P1: regime and risk propagation gaps | Ensemble passes `market_regime_allows_orb=True`; constructs ORB without forwarding its own configured risk budget. | Inject a validated regime and reconcile actual sized risk at the portfolio boundary. |
| P1: payout contracts change during wrapping | TRAPDOOR's 1.8R target is used as tranche 1, with 3R invented for tranche 2. RECOIL duplicates a VWAP target. | Preserve each strategy's exit policy explicitly; do not silently impose the global bracket. |
| P1: ranking is not a reservation ledger | `rank_and_allocate` handles batch duplicates/sector counts, but does not itself reserve cash, risk or pending orders atomically. | All accepted signals must pass the existing durable capacity layer; concurrent signals require transaction-level tests. |

These are code observations, not assertions that every branch is reachable in the current deployed process. The first two defects were reproduced directly; seven existing new-strategy/ensemble tests passed despite them.

## 1. Microstructure and execution reality

### 1.1 Bar-close latency, shortfall and limits

Use completed, timestamped candles only. Define decision midpoint m0, arrival midpoint ma, order direction s∈{+1,-1}, fill prices pj and quantities qj. Executed implementation shortfall in basis points is:

\[
IS_{exec}=10^4s\frac{\sum_jq_jp_j/\sum_jq_j-m_0}{m_0}.
\]

Separately report fees and unexecuted opportunity cost. Decomposition into delay, spread and impact is an attribution model, not three independently observable truths:

\[
IS_{exec}=10^4s(m_a-m_0)/m_0+spread\ crossing+residual\ impact.
\]

Record signal-ready, submission, acknowledgement and fill timestamps. Test empirical latency quantiles, not just a nominal 09:30 timestamp. The first opening-range bar cannot be traded at its closing price before that close is known. Simultaneous signals can encounter adverse selection; ordinary bar-close competition is not evidence of illegal front-running.

**ASSUMED model to fit**, with spread and volatility already in bps:

\[
E[IS\mid x]=a_b S_{bps}+b_b\sigma_{15m,bps}\sqrt{\tau/900}
+Y_b\sigma_{day,bps}\sqrt{Q/ADV}+d_b\,signedOFI.
\]

Here b identifies time bucket/regime, τ is seconds, Q and ADV use identical share units. For small aggressive orders a≈1/2 is a midpoint-to-touch approximation; b,Y,d require fitted data. The volatility term is an adverse-cost proxy, not the signed mean of zero-drift Brownian motion. Estimate median and adverse-tail costs separately and include participation/depth. Never claim an exact expected slippage function without fitted conditional observations.

Square-root impact is an empirical metaorder model, not synonymous with the Almgren–Chriss optimization framework. α=.5 and Y=.5–.8 are scenario assumptions here, not NSE calibration. Published evidence also documents size/participation limits to square-root fit. [Primary impact research](https://arxiv.org/abs/1412.2152).

For buy limit L, with tick h, k∈{1,2}, δ≤.10:

\[
L=h\lfloor\min(ask+kh,entry+\delta ATR)/h\rfloor.
\]

Reject if ask>L, quote stale/crossed, depth insufficient for the policy, or costs erase the edge. Sell analogue: round **up** max(bid−kh,entry−δATR) to tick; reject if bid<L. A collar controls accepted prices, not execution probability. An IOC can partly fill then cancel. Never repeatedly chase beyond a preregistered collar. Read instrument tick size rather than rounding every price to two decimals.

### 1.2 Queue dynamics, cancellation and evidence

The requested cancellation formula R/η is wrong if η denotes the fraction of orders ahead that cancel. Cancellations ahead reduce the queue, not increase it. For an event-ordered simulation, with queue ahead r, our remaining quantity u, eligible aggressive trade quantity v and known cancellations ahead c:

\[
r'=\max(0,r-c),\quad f=\min(u,\max(0,v-r')),\quad
r''=\max(0,r'-v),\quad u'=u-f.
\]

Only cancellations **ahead**, observed before the trade, reduce r. Orders joining behind do not. If η is a cancellation fraction, scenario queue is (1−η)R; if η is instead a conservative usable-volume fraction, require ηV≥R+Q, i.e. V≥(R+Q)/η. Do not combine these interpretations. Current manifest's `queue_haircut` uses usable volume `(1-h)V`; preserve that contract unless versioned and peer-reviewed.

Five-level snapshots do not reveal exact FIFO position, cancellation identity or hidden liquidity. Dhan's full packet documents aggregate depth, cumulative volume and last-trade fields, not individual resting-order IDs. Therefore rank/cancellation are estimates; last-trade quantity must not be treated as an exhaustive tick tape. [Dhan market-feed specification](https://dhanhq.co/docs/v2/live-market-feed/).

Use four market states: QUEUED until matching capacity reaches us; PARTIAL when 0<filled<Q; FILLED only at Q; LOCKED_NO_BID when there is no executable contra-side liquidity at the observed instant. Preserve partial inventory separately during a lock. The lock is not a zero probability of ever filling later. Snapshot zero on an incoming buy's ask side blocks immediate execution; a passive buy can later match incoming selling. Broker rejection/cancel/timeout belongs in a separate order lifecycle.

Adverse selection: measure signed markout M_h=s(mid_{fill+h}−p_fill). Define toxicity before analysis, e.g. M_60s<−one tick, and estimate P(toxic|fill,regime,queue,latency) with sample count and confidence interval. Also report fill-conditioned net expectancy and opportunity cost of nonfills. No universal toxic-fill percentage follows from a fast fill.

**E3 is an evidence contract, not a label a simulator may award itself.** Current `session_manifest.py` demands preregistered order hash, latency, arrival quote, source hashes, continuous capture, eligible turnover and costs. Reference simulator output below deliberately has `qualifies=False`; bind real capture evidence and run the existing verifier before creating qualification records. Synthetic scenarios never count toward prospective sessions.

### 1.3 Correlated shocks and portfolio breaker

₹4,500 is planned stop-distance risk, not maximum loss. Gap/slippage/failed exits can exceed it. Correlations .35/.88/.95 are stress scenarios until measured. Correlation is not covariance: Σ=D Corr D with D the volatility diagonal.

For terminal standardized Gaussian returns Zi and barriers −zi:

\[
P(Z_1<-z_1,Z_2<-z_2,Z_3<-z_3)=\Phi_3(-z;Corr).
\]

This is **not** probability of three intraday stops, which is a joint first-passage/path problem. With identical terminal tail probability p=.10, independence yields .001; perfect shared shocks yield .10. These illustrative endpoints do not identify actual stop-out risk. For positive equicorrelation ρ, terminal probability can be integrated as ∫φ(x)∏Φ((−zi−√ρx)/√(1−ρ))dx. Use block-bootstrap/t-copula jump scenarios for paths and executable exit costs, not Gaussian closing returns alone.

Proposed breaker: trigger on the supplied Nifty15m<−.006 OR BankNifty15m<−.009 OR advances/declines<.25. These thresholds are **ASSUMED**, evaluated only on synchronized, fresh data with declared universe coverage. A/D=.25 means 20% advancing among advances+declines, not 25%; D=0 and empty coverage need separate cases. Unknown macro data freezes entries, rather than silently declaring safety.

On trigger: latch freeze; request cancellation of pending entries; retain their risk until acknowledgement; protect any racing fill; submit risk-reducing exits with side-aware collars and reconcile inventory. Do not blindly tighten every stop into spread noise. Resume only after a recorded recovery criterion/cooldown, not one good tick. Test cancel/fill races, short exposures and all three positions gapping together.

### 1.4 CAS, RMS and staged exits

The mandate's CAS 15:15–15:30 and 15:23–15:28 matching schedule is incorrect. NSE currently specifies CAS **15:15–15:35**: transition/reference 15:15–20; market+limit entry 15:20–25; limit-only entry 15:25–30 with random closure in the last two minutes; matching follows closure, scheduled through 15:35. Reference is 15:00–15:15 VWAP; stop-loss/iceberg orders are not allowed. [NSE CAS](https://www.nseindia.com/static/products-services/closing-auction-session).

Broker policies are not interchangeable. Zerodha publishes CAS MIS square-off **15:12**, ₹50+GST per squared-off order. [Zerodha RMS](https://support.zerodha.com/category/trading-and-markets/trading-faqs/market-sessions/articles/intraday-auto-square-off-timings). Dhan publishes cash intraday square-off **15:10**, derivatives 15:25; its pricing lists auto-square-off ₹20+GST. [Dhan timing](https://dhan.co/support/general/market-session-status-and-timing/what-are-the-dhan-intraday-auto-square-off-timings/), [Dhan charges](https://dhan.co/pricing/).

Recommended policy: 14:50 no new signals/orders; 15:00 cancel remaining entries; 15:05 start bounded exits; **15:08 escalate** for Dhan, aiming to reconcile flat before its 15:10 action. More generally escalation deadline=min(desk deadline,broker RMS−tested safety margin). 15:10 is a breach/alarm deadline, not the first aggressive order. Two minutes is an initial operational assumption, to be stress-tested. A request cannot ensure flatness: record residual inventory, attempts and failures explicitly. All entry evaluators, manifest windows, simulators, daemon timers and UI must consume one versioned policy. Use Asia/Kolkata-aware time and exchange holiday/early-close calendars.

### 1.5 Margin and cash: reject the requested guarantee

0.30×175000=52500 and 75000−52500=22500 are correct arithmetic **conditional on those liabilities being exhaustive and unchanged**. They do not imply P(shortfall)=0. Margin rates, MTM losses, pending reservations, unsettled obligations, collateral haircuts and broker requirements can change. NSE Clearing describes VaR, ELM, MTM and crystallized intraday losses; client risk collection is also broker-specific. [NSE Clearing margins](https://www.nseclearing.in/risk-management/capital-market/margins).

Cash stock delivery requires funding the purchase, not merely retaining a 30% margin forever. Define whether ₹175k means full position notional or margin; leverage must never silently multiply it. For fully cash-funded longs totaling ₹175k within ₹250k, reserve full purchase commitments plus costs and other liabilities; do not double-count cash already blocked.

Admission requires available settled cash − commitments − stressed losses − costs ≥ minimum operational buffer, and independently sufficient broker margin. Obtain instrument/product-specific fresh requirements; reject stale/unknown values. Overnight cash shorts cannot be treated as ordinary delivery longs. The blanket zero-shortfall proof is rejected.

## 2. Seven strategy reviews and arbitration

### Common experimental discipline

All numbers below are **candidate parameters**, not fitted optima. Freeze a small parameter neighborhood, train on earlier dates, purge overlapping holding windows, test on later dates, and report all attempted variants. Use date-block bootstrap, net returns, fill rate, drawdown, turnover, cost sensitivity and confidence intervals. Same-day multiple stocks are not independent observations. Include delisted/excluded names and point-in-time F&O/surveillance membership. No full-day volume, revised corporate events or hindsight sector constituents in historical decisions.

Risk size should solve for the largest integer Q satisfying Q|entry−stop| + estimated fees(Q) + adverse exit allowance(Q) ≤1500, Q×entry≤slot limit, and available portfolio/pending-order capacity. A stop is an instruction, not a loss guarantee. Current simpler sizing budgets price risk alone. With ₹58,333 notional, 1% stops mean only about ₹583 price risk; even 2.5% means ₹1,458. The blueprint's constant ₹1,500 loss/₹3,375 win does not follow from this cap.

### 2.1 ORB_MOMENTUM

Failure: exhausted overnight gaps, narrow opening range repeatedly swept, gap-and-reverse news days, false confirmation after costs, late arrival at the breakout wick. Daily EMA20>EMA50 describes trend, not a guarantee of continuation. Exclude scheduled results under the chosen event policy; evaluate gap size/ATR as a feature rather than banning every Monday.

RVOL for bar bucket b is V(d,b)/median{V(d−j,b):j=1..20}, using only valid prior sessions and a declared minimum count. Opening and midday buckets must not share a denominator. At later ORB signals use that later bar's bucket. Reject zero/unknown baselines. Test thresholds 1.3/1.5/1.8 and range/ATR bands on held-out dates, not the winning grid point alone.

OFI≥.20 and absorption≤2.50 are hypotheses whose normalization/window must be identical in training and deployment. Snapshot imbalance is not event OFI; insufficient depth must be UNKNOWN, not “no iceberg.” Require next executable quote plus collar, then resize from that entry. The ensemble's hardcoded regime=True must be replaced before gating has any meaning.

### 2.2 VWAP_RECLAIM

Failure: a reclaim within broad distribution becomes another lower high; falling VWAP repeatedly attracts losing longs. Bar typical-price VWAP is an approximation to trade VWAP, not a measurement of institutional average cost.

Define session-only VWAP=Σtypical_price×volume/Σvolume and weighted σ=√[Σvolume×(typical−VWAP)²/Σvolume]. Never feed yesterday's bars into session VWAP while warming ATR. Compare σ multipliers1/1.5, touch buffers .2%/.5%, RVOL1.5/1.8/2.1. Trend/breadth filters should use contemporaneous completed data. Structural long stop=min(pullback low,VWAP×.9975)−tick buffer is a testable rule, not stop-hunt immunity. Reject excessive distance; current strategy checks a lower risk bound without the requested common upper bound.

### 2.3 VOLATILITY_SQUEEZE

Failure: compression persists, illiquid narrow prints resemble a squeeze, and repeated boundary attempts accumulate fees. NR7 is the latest completed daily range being narrowest of seven, **not seven consecutive contracting days**. Exclude today's unfinished daily candle.

Candidate score .45I(NR7)+.30I(inside day)+.25I(BBwidth<KCwidth) is ordinal, not success probability. Define BB using 20 completed daily closes and KC using EMA20±1.5 daily ATR; specify ATR estimator. Seven bars suffice for NR7 but not 20-bar bands. Do not substitute missing indicators. Test BB multiplier1.8/2/2.2 and KC1.3/1.5/1.7 with anti-overfitting controls.

Midpoint stop can be inside normal noise. Enforce unrounded risk ratio .004≤(entry−stop)/entry≤.025 after tick alignment, with a breakout-failure stop alternative tested separately. Current code has lower-bound checking but not the stated upper-bound guarantee. Use cooldown/max attempts per symbol.

### 2.4 TRAPDOOR

Failure: the apparent failed breakdown is a pause within a real downtrend; long wicks contain no executable reversal. ER8=|Ct−Ct−8|/Σ|ΔC| requires nine closes; zero denominator is a flat path, not strong evidence of profitable chop. Shorter-history ER is a different feature. ER alone cannot distinguish a noisy downtrend from a safe range.

Use separately completed mother/inside/probe/confirmation bars, no overlapping-role candle. Test ER cap .25/.35/.45, mother ATR1/1.2/1.4, RVOL1.1/1.3/1.5. Four-bar time stop starts at actual entry fill, not signal time, and emits an exit request. Correct doubled cost; retain the 1.8R strategy exit rather than silently replacing it with a half-at-1.8/half-at-3 payout. Re-evaluate viability after actual spread/slippage.

### 2.5 LAST_LIGHT

Failure: remaining time is too short, rebalance flows reverse, scheduled overseas releases cause correlated jumps. “Mutual fund NAV momentum” does not identify a measured mechanism from price/volume alone. European open changes with daylight saving; use an event calendar, not a permanent IST anecdote.

14:00–14:30 entry gives roughly 38–68 minutes to a 15:08 escalation, not unlimited time for 3R. Estimate probability of target before deadline from empirical MFE paths. Proposed 60%@1.2R plus40%@2R yields max1.52R, not2.25R; if second tranche stops at entry, gross=.72R. Recompute expectancy before adopting. Test entry cutoff14:15/14:30 and 2–4bar timeouts; do not tighten stops mechanically solely to manufacture more nominal R. Correct doubled friction; instrument-specific exit availability still governs.

### 2.6 RECOIL

Failure: fading information-driven repricing, short squeeze, persistent one-sided demand. High RVOL is both the trigger and a possible warning; “RVOL≥3 means news” is not a valid news detector. Require verified event exclusion, fresh broad-market regime and reversal confirmation. A stock-only pre-climax ER does not meet the proposed broad-market ER filter.

Test stretch1.2/1.4/1.8ATR and rejection wick.25/.35/.45, conditioning on regime. Do not enable RECOIL merely because VIX is in crisis. Shorts need explicit side, stop above entry, bid-based entry, ask-based cover, cash-short intraday constraints and borrow/auction failure handling. VWAP target must be on the profitable side and large enough to cover fees; reject near-zero target distance. Correct doubled friction and preserve the single VWAP target unless a separate bracket is validated.

### 2.7 COMPASS

The documented theory is viable as a hypothesis, but current fallbacks invalidate the comparison. Use a synchronized cross-sectional batch with sector peers excluding candidate self-influence; require minimum peer count and coverage of a point-in-time sector universe. For beta estimate Cov(ri,rs)/Var(rs) on past20 daily returns, ideally shrink toward1; reject zero variance/insufficient observations. A 20-day beta is noisy; compare60days rather than proclaiming20 optimal.

Use 1hour log residual ri,4−βrs,4, sector return above market, declared sector breadth≥.50, completed four-bar breakout and bucketed RVOL≥1.5. The module's prose says60% while code says50%; pick and version one rule. Cross-sectional z-scores require enough observed peers. Missing Nifty cannot become zero return, missing peer list cannot become the stock itself. Pass actual quote and beta to COMPASS. After conversion to the unified signal, enforce side, exit policy, risk and cost through the common allocator. Bridge reference is supplied separately; deployment still requires strict input adapters.

### Cost correction and strategy conflicts

0.106% of entry notional is only a rough round-trip estimate, not a statutory invariant. Dhan currently lists brokerage min(₹20,.03% per executed order), NSE transaction .0030699%, intraday sell STT .025%, buy stamp .003%, SEBI .0001%, GST18% on applicable charges; rounding and IPFT also apply. Split orders and prices affect the bill. [Dhan pricing](https://dhan.co/pricing/). Implement total fees as sum of per-order charges, distinguish partial fills of the same order from new orders, and reconcile against sample contract notes. Slippage is additional. Do not deduce profitability from target distance exceeding three times a constant fee: target probability and losses matter.

| Concurrent signals | Proposed handling |
|---|---|
| ORB + SQUEEZE, same symbol/direction | One position and reservation. Preserve both labels. +.05 may be an ASSUMED rank adjustment but is not a five-percentage-point probability increase; test incremental value. |
| ORB long + RECOIL short | Default conflict/no trade until a preregistered regime selector is validated. RVOL≥2.5 override is a research hypothesis, not an execution law. |
| Three correlated long signals | Rank by incremental portfolio utility, stress loss and sector capacity, not merely three largest scores. |
| Existing/pending same symbol | Reconcile/cancel/modify explicitly; never open a duplicate because it came from another strategy. |
| Too few positive net-value candidates | Trade fewer than three, including zero. Three concurrent slots is not three mandatory trades/day. |

## 3. Derivatives and alternative alpha

### 3.1 Options: measurable data versus inferred stories

Compute walls within a single underlying/expiry: Kc=argmax call OI, Kp=argmax put OI, with deterministic tie handling and lot-unit normalization. “Massive” needs a preregistered concentration/percentile definition. Long near overhead call wall 0≤Kc−entry≤.35ATR with rising OI can be a **research warning**, not proof of call writing: every newly opened contract has a buyer and seller. Dealer inventory is not observed from public aggregate OI.

\[
MaxPain=\arg\min_s\sum_i lot_i\{OI^c_i(s-K_i)^++OI^p_i(K_i-s)^+\}.
\]

Report expiry, strike range, completeness and ties. This payout statistic does not prove pinning, manipulation, or a tradable gravitational force. Test expiry effects against matched non-expiry controls after costs. PCRoi=ΣputOI/ΣcallOI and PCRvol=ΣputVolume/ΣcallVolume; denominator zero is UNKNOWN. 1.40/.65 are uncalibrated boundaries, sensitive to expiry mix and strike coverage; use own-history percentiles instead of universal bullish/bearish labels.

Price/OI sign matrix (+,+), (+,−), (−,+), (−,−) may be labeled conventional long buildup/short covering/short buildup/long unwinding. These are descriptive classifications, not identification of aggressive institutional ownership. Add a neutral/unknown state, aligned timestamps, material-change tolerances and roll controls. Mandatory “long buildup” across four strategies would change their populations and may exclude short-covering momentum; test overlay on/off separately.

For cash-delta-notional change per1% spot move, if gamma is per₹ and OI is contracts:

\[
GEX_{1\%}=.01S^2\sum_i sign^{dealer}_i\Gamma_i OI_i lot_i.
\]

The mandate's S×(callOI−putOI)×100 is not this quantity. Both long calls and long puts have positive gamma; dealer sign must be independently known or explicitly assumed. A call-positive/put-negative proxy is not net dealer GEX. Unknown sign must remain unknown. Hedging interpretations are conditional hypotheses, not permission to route cash orders.

### 3.2 Futures basis and roll

Raw B=F−S; annualized simple carry=100(F/S−1)365/DTE for DTE>0. Near expiry this annualization becomes unstable. Use synchronized quotes and executable spreads. Fair futures with cash dividends is Ffair=(S−PV(dividends))exp(rT); examine residual F−Ffair, not raw discount alone. Dividends and funding can explain negative basis without bearish aggression.

Basis z=(residual−past mean)/past σ; define20 observations and bucket explicitly, never mix20days with20ticks. A1.5σ filter is ASSUMED. Roll ratio=(OInext+OIfar)/(OInear+OInext+OIfar), denominator positive, consistent lots. Its change is rollover velocity; ratio alone is not velocity. Positive calendar spread can simply reflect carry.

The mandate's Tuesday-to-Thursday expiry convention is stale. NSE individual-security futures currently expire on the last Tuesday (previous trading day when a holiday), with monthly contracts. Read the daily contract master; do not apply index weekly expiry assumptions to stock options. [NSE individual securities](https://www.nseindia.com/static/products-services/equity-derivatives-individual-securities).

Dhan option-chain API supplies OI, Greeks, volume and quotes and an expiry-list endpoint; the documentation specifies a per-unique-request three-second cadence. This is not proof every symbol's OI updates every three seconds. Store exchange/receipt timestamps, session, expiry, lot, units, source version and coverage; honor rate limits, cache, and reject stale overlays. [Dhan option-chain API](https://dhanhq.co/docs/v2/option-chain/).

### 3.3 OFI and depth

Correct signed contribution at level k:

\[
e_k=I(b_t\ge b_{t-1})q^b_t-I(b_t\le b_{t-1})q^b_{t-1}
-I(a_t\le a_{t-1})q^a_t+I(a_t\ge a_{t-1})q^a_{t-1}.
\]

Bid improves/adds → positive; ask improves downward/adds → negative; ask withdraws → positive. The supplied ask cases followed by subtraction reverse the signs when ask price moves. Weighted MLOFI=Σwke_k with w=(1,.6,.35,.2,.1). One bounded normalization is Σwke_k / Σwk(qbt+qbt−1+qat+qat−1), rejecting zero denominator. Accumulate numerator and denominator over a declared window. Fixed rank levels can shift when prices move; snapshots are an approximation to event OFI, not perfect event reconstruction. [Cont, Kukanov and Stoikov](https://arxiv.org/abs/1011.6402).

Weighted midpoint=(bid×askQty+ask×bidQty)/(bidQty+askQty). It lies within an uncrossed spread; it is not the complete calibrated conditional microprice model. A large bid can be canceled. Absorption proxy=eligible aggressive-buy volume / declared initial displayed ask depth, coupled with limited price progress and replenishment observations. Hidden orders cannot be established from aggregate refreshes alone. Without signed, complete trade events, set iceberg state UNKNOWN, not false. Test snapshot loss, duplicate/out-of-order timestamps, crossed quotes and zero depth.

### 3.4 VIX

India VIX is annualized Nifty-option-implied expected volatility over30calendar days, not a calibrated15minute stock forecast. [NSE VIX methodology](https://www.nseindia.com/static/products-services/indices-indiavix-index).

Treat supplied boundaries11.5/16.5/22 as experimental regime bins. Candidate multipliers: below11.5 ORB.5, other approved models at most1;11.5–16.5 at most1;16.5–22 at most.5;above22 all0 pending crisis-specific validation. These are risk-reduction hypotheses, not empirical superiority claims. Do not simultaneously loosen stops and preserve share count. Apply multiplier to risk budget before all caps. Missing/stale VIX blocks a VIX-dependent strategy. The claimed65% false-breakout rate has no supplied sample and is rejected. Add hysteresis to avoid rapid threshold switching; never auto-enable crisis fades.

### 3.5 PEAD

Promising separate challenger, not a reason to bypass the event blackout. Maintain scheduled and actual announcement timestamps from exchange filings, plus timestamped pre-announcement expectations. Surprise=(reported EPS−pre-event consensus)/historical forecast-error scale, with comparable accounting bases. Gap>2.5% and RVOL≥3 are candidate confirmation filters, not proof of an earnings surprise.

For ordinary strategies block entries within24hours of known scheduled results and after an actual event until the exclusion expires. A verified PEAD variant can separately require favorable surprise, completed opening-range hold, valid costs and fresh data; futuresOI remains an optional challenger feature. Correct for other news and survivorship. Multi-day PEAD evidence does not establish a same-day MIS edge. No hindsight use of revised earnings times or consensus.

### 3.6 Pairs research

Fit logPi=α+βlogPj+ε using training data only; test residual stationarity with Engle–Granger appropriate critical values, not ordinary residual ADF thresholds. Correct selection bias from searching many pairs; require stability out of sample. A Kalman βt must be one-step filtered/predicted, not smoothed using future observations; it can otherwise absorb genuine divergence.

For stationary AR(1) εt=a+φεt−1+ut,0<φ<1, θ=−lnφ/Δt and half-life=ln2/θ. φ≥1 is not mean reversion. Z uses lagged estimated mean/scale; enter|Z|≥2, exit near0, risk exit|Z|≥3.2 as hypotheses, with time-stop before liquidation deadline. Convergence before15:00 cannot be required of the market; forcibly attempt risk reduction if convergence has not happened.

Size both legs jointly so full pair stress loss+two-leg fees≤₹1500; reserve two slots and both notionals. Same-sector pair uses the two-per-sector allowance. Handle partial/failed second-leg execution and naked exposure. Several example mega-cap pairs may violate the mandated market-cap ceiling: verify membership rather than silently expanding the universe. Pair models merit a separate Track2 sub-ledger/version, not blending their P&L into ORB.

## 4. Formal implementation, optimization and roadmap

### 4.1 Reference contracts and code

Companion `beacon_reference_20260924.py` supplies frozen JSON-serializable snapshots, correct-sign five-level OFI, an explicitly research-only derivatives overlay, a COMPASS call adapter, and a pure discrete queue simulator. It is executable reference code, **not production-certified or installed in the trading path**. The coding-standards skill informed immutability, explicit validation and small isolated components. Unknown derivatives interpretations are not fabricated. Source hashes bind records; they do not certify that source data is truthful.

Integration requires explicit side/expiry/source/time/units/schema version, validation at every external boundary, and immutable nested collections. Future timestamps, NaN/Inf, booleans-as-numbers, zero denominators, missing units and stale contracts must reject. Use decimal/integer ticks and paise at money/order boundaries; reference analytical floats are not a final accounting implementation.

### 4.2 Scores, expectancy and Kelly

\[
C=\sum_{j=1}^6w_jS_j,\qquad w_j\ge0,\ \sum w_j=1,\ S_j\in[0,1].
\]

Example features: out-of-fold strategy expectancy rank, bucket-RVOL percentile, sector residual percentile, normalized signed OFI, validated derivative feature rank, regime suitability. Fit weights on prior dates and freeze them. Missing mandatory features reject; an optional-feature variant needs a separately registered score definition. Hand-scored C is not P(win). For ranking use lower-confidence expected net gain minus marginal covariance/tail/liquidity penalties, with hard cash, risk, pending orders, sector and symbol constraints. Retain every rejected candidate and reason.

With half exits at1.5R/3R, p1=P(T1 before initial stop), p2=P(T2 before remaining stop|T1), no gaps/timeouts for this illustrative tree:

\[
E[X_R]=-(1-p_1)+p_1(1-p_2).75+p_1p_2(2.25)-E[c_R]
=-1+p_1(1.75+1.5p_2)-E[c_R].
\]

With p1=.44,p2=.50 and average costs.08R, expectancy=.02R; with p2=.25 it is−.145R. Thus44% alone says almost nothing. Actual model needs branches for partial target, timeout, gap, partial fills and canceled remainder. Costs are on actual orders/notionals; T1 “breakeven” stop on remainder does not reimburse its fees. The quoted100-trade arithmetic is correct only for its assumed binary payouts; it is not an estimated return or a44% break-even hurdle. Binary net payouts3251/1562 have break-even1562/(3251+1562)≈32.45%.

For binary gross win bR, loss R, costs cR on either outcome, net win W=b−c>0 and loss L=1+c:

\[
f^*=\frac{pW-(1-p)L}{WL}.
\]

The requested `(pb−q−c)/b` omits cost changes to the denominator. For multiple payouts maximize Σpk log(1+f xk), subject to1+fxk>0; derivative Σpk xk/(1+fxk)=0. Use conservative posterior/bootstrapped probabilities, fractional Kelly only after validation, and cap by ₹1500/capital, portfolio risk and operational constraints. Current deployment allocation remains zero under Rule1. Kelly is not a substitute for an observed edge or jump-risk bounds.

### 4.3 Required tests before integration

Test each strategy with absent/None/NaN/Inf/boolean values; incomplete history; wrong-day or mixed-timezone bars; crossed/stale quotes; negative/zero volume; OI resets; expiry change; denominator zero; duplicate/replayed trade IDs; cancellation races; partial T1; odd quantities; T1/stop touched in same bar; unconfirmed break-even stop amendment; partial IOC; restart after fill-before-write; fee schedule changes; CAS boundary times; source mismatch; pending risk reservation contention. Bar OHLC cannot establish whether target preceded stop: resolve from evidence or choose explicitly adverse research ordering, never favorable hindsight.

### 4.4 Five implementation phases

1. **Execution truth first.** Ownership: Codex implementation assignment with Antigravity integration and Claude red-team review. Make every exit instruction-only until incremental fill evidence; one session/broker policy; side-aware brackets; durable reservation/cancel/fill reconciliation. Acceptance: quote-only/time-only regression probes cannot create realized P&L; restart and race tests preserve inventory. Adopt earlier Dhan escalation. No new alpha in production yet.
2. **Wire and standardize existing strategies.** Antigravity owns ensemble/COMPASS adapter; reviewers check data contracts and quantitative assumptions. Fix four fee hurdles through a shared actual-order cost service. Preserve strategy-specific targets/timeouts and propagate risk config. Acceptance: real launch route reaches approved evaluator; missing peers/market fail closed; short side is preserved; no duplicate reservations. Existing ORB remains a separately identified control.
3. **Derivative telemetry in shadow mode.** Add point-in-time expiry/lot mapping, chain/basis/corporate-event capture, staleness/coverage/rate-limit tests. No public-OI “dealer truth.” Acceptance: deterministic replay and no lookahead; overlays recorded but not silently made mandatory. No options/futures orders: these are inputs to cash trading research.
4. **OFI/VIX challenger experiments.** Correct ask signs; sequence/freshness handling; explicitly approximate snapshot OFI; calibration datasets and missing-data gates. Compare base versus overlay net performance and fill-conditioned markouts. Acceptance: predeclared holdout and confidence intervals, not best in-sample threshold. Leave unsupported iceberg state unknown.
5. **10,000-path stress plus prospective qualification.** Simulate correlated intraday paths with jumps, spread expansion, latency tails, cancellations, feed outages, fee changes, RMS failure, pending orders and recovery. Calibrate from data; independently stress parameters. Report max drawdown, expected shortfall, deadline misses, residual inventory, margin failures and confidence bounds. Zero failures in10000 independent modeled paths implies approximately<.03% at95% confidence only under that model, not zero real-world risk. Continue60 prospective sessions and20 verified fillable entries with positive net expectancy; synthetic paths do not count. Rule8 review and unresolved dissent accompany any promotion.

**Final decision:** repair demonstrated execution and integration defects before introducing mandatory derivatives filters or adding pairs/PEAD to the operating desk. The roadmap is approved as a research proposal only. Production and performance certification remain **BLOCKED (P0: unverified stop/time exits still create completed positions and realized P&L)**.

## Verification record and handoff

- Created this report, `beacon_reference_20260924.py`, and `test_beacon_reference_20260924.py` in `shared/reviews/` only. No production edits, Git pushes, live orders, credentials or observation counts changed.
- Ran `.venv/Scripts/python.exe -m pytest shared/reviews/test_beacon_reference_20260924.py tests/test_track2_new_strategies.py tests/test_track2_multi_strategy_engine.py -q`: **27 passed in 0.15s** (20 reference cases plus seven existing cases).
- Independently reproduced quote-only stop and time-exit P&L defects with fresh in-memory brackets. These are not fixed by the reference module and are not covered by the 27 passing cases above.
- Reference COMPASS adapter deliberately requires an external batch-validation callback. Its unit test verifies wiring only, not the production sector feed, timestamps, beta estimator or the existing strategy's correctness. Full production integration remains Phase2 work.
- Reference derivative overlay provides research warnings rather than an invented statistically calibrated approval rule. No portfolio allocation or broker submission is authorized by its output.
- Neither a 10,000-path simulation nor a new profitable-strategy backtest was run. No fitted coefficients, test-period returns or peer approvals are claimed. The report specifies how to conduct those experiments.
- Antigravity integration request: prioritize the two quote-only closure paths, centralize broker-aware cutoffs, then reconcile side/exit-policy contracts and the actual daemon routing. Claude review request: independently reproduce these cases and challenge payout-tree, cancellation and derivative-sign assumptions before promotion. These are handoff instructions, **not a claim that either agent has received or accepted them**.
