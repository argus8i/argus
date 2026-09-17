# Track 1 Tri-Agent Idea Review: Rule 7 Pre-Circuit Accumulation Breakout

**Document Purpose:** Formal single-idea debate and evaluation ledger under AGENTS.md Rule 8 (Tri-Agent Consensus Protocol) and Rule 11 (Absolute Track Isolation).  
**Track:** Track 1 (ESM & Circuit Micro-Caps, Fixed Circuit Bands 2%/5%, Market Cap < ₹500 Cr).  
**Reviewed Strategy Idea:** **Rule 7 Pre-Circuit Accumulation Breakout Strategy**  
**Capital State:** **100% Cash** (Observation Mode under AGENTS.md Rule 1). Real capital deployment strictly prohibited.  
**Gate Status:** **0 / 60 Prospective Sessions | 0 / 20 Realistically Fillable Entries**  

---

## 1. Strategy Specification & Mathematical Architecture (Antigravity Submission)

### A. Core Strategy Premise
The Rule 7 Pre-Circuit Accumulation Breakout strategy identifies micro-cap securities undergoing institutional or operator accumulation in a two-sided continuous market *before* circuit locks occur. By entering during the liquid pre-circuit base and exiting pre-emptively on Day 3 or Day 4 into the resting Upper Circuit buyer queue, the strategy aims to capture +10% to +20% momentum while avoiding locked upper circuit chase traps and locked lower circuit exit freezes.

### B. Input Requirements & Entry Criteria (Rule 7)
A security qualifies for an entry signal (`EntrySignal.BUY_ACCUMULATION_BREAKOUT`) if and only if all following conditions pass simultaneously:
1. **Price Floor (Rule 2):** $\text{Price} \ge \text{₹10.00}$ [`MEASURED`, Exchange Tick Rule]. Sub-₹10 securities suffer extreme tick-size distortion and are immediately disqualified.
2. **Surveillance Cleanliness (Rule 6):** Surveillance status must be strictly `NONE` [`MEASURED`, BSE daily bands]. Any classification under ESM Stage 1/2, GSM 1–4, ASM, or `BE` series triggers `CRITICAL_AVOID`.
3. **Pre-Circuit Proximity (Rule 3 & Rule 7):**
   - $\text{Price} < \text{Upper Circuit} - 3 \times \text{₹0.01}$ ticks [`DERIVED`].
   - Remaining daily price band: $\frac{\text{UC} - \text{Price}}{\text{UC} - \text{LC}} \ge 15.0\%$ [`DERIVED`].
   - Upper circuit offers: $\text{Total Offers} > 0$ [`MEASURED`]. Never chase a stock locked at Upper Circuit with 0 offers.
4. **Order Book Two-Sidedness:** Both $\text{Total Bids} > 0$ and $\text{Total Offers} > 0$ [`MEASURED`].
5. **Bid-Ask Spread:** Spread $\frac{\text{Best Ask} - \text{Best Bid}}{\text{Best Bid}} \times 100 < 1.0\%$ [`DERIVED`, finite non-NaN].
6. **Daily Range Expansion:** Daily range $\frac{\text{High} - \text{Low}}{\text{Low}} \times 100 \ge 3.0\%$ [`DERIVED`, finite non-NaN].
7. **20-Day Volume Expansion:** Current Day Volume $\ge 3.0 \times \overline{V}_{20d}$, with baseline 20-day average volume $\overline{V}_{20d} > 0$ [`MEASURED`].

### C. Position Sizing & Downside Risk Bounds (Rule 5 & Rule 9)
Position sizing is strictly fail-closed and bounded by the minimum of capital tail-risk capacity and calm-market liquidity capacity:
1. **Rule 5 Downside Calibration (10-Day Lower-Circuit Lockout):**
   Calibrated strictly to 10 consecutive 5% lower-circuit sessions without exit liquidity:
   $$1 - 0.95^{10} = 40.126\% \approx 0.401 \quad [\text{DERIVED}]$$
   $$\text{Max Capital Shares } (Q_{capital}) = \left\lfloor \frac{\text{Rupees Willing to Lose Outright}}{0.401 \times \text{Price}} \right\rfloor$$
2. **Rule 9 Liquidity Sizing Gate (Calm-Market 15% Cap):**
   Maximum 15% daily volume participation over a 2-session clearable horizon:
   $$\text{Max Liquidity Shares } (Q_{liquidity}) = \lfloor 2.0 \times 0.15 \times \text{Daily Volume} \rfloor = \lfloor 0.30 \times \text{Daily Volume} \rfloor \quad [\text{DERIVED}]$$
3. **Safe Position Size:**
   $$Q_{order} = \min(Q_{capital}, Q_{liquidity})$$
4. **Mandatory Paper-Trading Gate Invariant (Rule 1):**
   During the observation gate phase:
   $$Q_{live} \equiv 0, \quad Q_{paper} = Q_{order}$$

### D. Position Management & Exit Execution (Rules 4, 6, 7, 10)
- **Pre-Emptive Profit Exit (Day 3/4):** Sell limit order submitted directly into the resting Upper Circuit buyer queue on Day 3 or Day 4 when gross unrealized gain $\ge +10.0\%$ (on Day 3+) or $\ge +15.0\%$ [`ASSUMED`].
- **Rule 6 Surveillance Override (Rule 10):** If an existing position receives a band tightening ($20\% \to 10\%, 10\% \to 5\%, 5\% \to 2\%$) or enters ESM/GSM/ASM, Rule 6 mandates an immediate exit into the earliest available liquidity. Strictly overrides Rule 7 hold targets.
- **Drawdown / Loss Exit Review:** If the position has negative returns ($Gain < 0\%$) on Day 3+, or distribution churn is detected, the engine routes to `EMERGENCY_EXIT_ATTEMPT` / `STOP LOSS REVIEW` (never claiming profit).
- **Lower Circuit Lockout (Discrete 4-State):** If bids collapse to zero ($\text{Total Bids} = 0$), execution state is `LOCKED_NO_BID`. Counterparty fill probability is mathematically $0\%$; stop losses cannot execute. Order queuing is subject to broker T+1 delivery settlement and FIFO priority.

### E. Explicit Provenance Table

| Value / Parameter | Category | Sourced Value | Author & Source Citation | Retrieval Date | Operational Status |
| :--- | :---: | :---: | :--- | :---: | :---: |
| **CROPSTER 10-Day LC Descent** | `MEASURED` | 10 sessions (−39.66%) | Official BSE Bhavcopy (`scripcode=523105`, 23-Jul to 05-Aug 2026) | 2026-09-10 | Calibrating Tail Loss Anchor |
| **Rule 5 Loss Divisor** | `DERIVED` | 0.401 | Compound loss formula: $1 - 0.95^{10} = 0.40126...$ | 2026-09-10 | Code Standardized |
| **CCDL Pre-Emptive Exit** | `MEASURED` | +4.55% (+₹1,800) | User execution (09-Sep @ 1.32 to 10-Sep @ 1.38) | 2026-09-10 | Empirical Proof of Pre-emptive UC Exit |
| **CCDL Post-Exit LC Lock** | `MEASURED` | 0 Bids, 2.05 Cr Offers | Kite Web depth screenshot (`Screenshot_20260911_143535.jpg`) | 2026-09-11 | Tail Risk Demonstration |
| **BSE Inward Tick Truncation** | `MEASURED` | Floor to ₹0.01 tick | BSE Consolidated Master Circular Equity Segment Item 1.6 | 2026-09-10 | Statutory Exchange Regulation |
| **T2T T+1 Settlement Rule** | `MEASURED` | Day T+1 sellable | Zerodha RMS Policy Circular (T2T FAQ / NCL rolling settlement) | 2026-09-12 | Broker Operational Rule |
| **Queue Multiplier Prior** | `ASSUMED` | $\rho = 1.0$ ($n=1$) | CROPSTER Day 3 volume wave (12,560 shares filled in 1h during 1.57 Cr vol) | 2026-09-10 | Prior with Wide Uncertainty |
| **Upper Circuit Continuation Prob** | `ASSUMED` | Unmeasured ($>50\%$) | Hypothesis: Pre-circuit accumulation predicts multi-day continuation | 2026-09-12 | Unmeasured Hypothesis to Paper Test |

### F. Antigravity Formal Verdict
**Verdict:** **ACCEPT WITH CONDITIONS**  
**Conditions for Paper Observation:**
1. Real capital deployment remains strictly prohibited (100% Cash, Rule 1).
2. Strategy is eligible exclusively for prospective paper observation logging in `CHATGPT/observation_log.csv` and `shared/track1_esm/03_TRADE_LOG.md`.
3. Every paper candidate must strictly satisfy the ₹10.00 price floor (Rule 2) and clean surveillance status (Rule 6). Currently 0 watchlist scrips are eligible.
4. Execution must model discrete 4-state execution (`LOCKED_NO_BID`, `QUEUED`, `PARTIAL`, `FILLED`) with capacity caps; no deterministic fills or subjective probabilities.

---

## 2. Independent Quantitative & Adverse-Selection Review (Claude Code Submission)

### A. Scope and Method

This review independently re-derives every formula in Section 1.C–D of the Antigravity submission, stress-tests the sizing math against non-ideal (real-world) volume and gap distributions, and evaluates the Day 3/4 pre-emptive exit as a queue-mechanics claim rather than a strategy assumption. No code was modified. Codex's Section 3 was read prior to writing this section; I do not duplicate its regulatory/broker findings, but I flag where my quantitative conclusions reinforce or diverge from them.

### B. Re-derivation of the Rule 5 loss divisor (0.401)

**Claim under test:** $1 - 0.95^{10} = 0.40126$, used as the tail-loss denominator for capital-based position sizing.

1. Arithmetic check: $0.95^{10} = 0.598737$ [DERIVED; standard compound arithmetic; 2026-09-12]. $1-0.598737 = 0.401263$. The arithmetic is correct — **CONFIRMED**, not merely restated.
2. **This is a geometric decay model applied to a discrete, path-dependent process, and the two are not equivalent.** A 5% lower-circuit band means each day's LC price is $0.95\times$ the *previous close*, not $0.95\times$ the *original entry price*. The compounding is correct for consecutive-day LC-to-LC decay measured from a rolling anchor, but Rule 5's intended use — "rupees an investor is willing to lose outright" — implicitly treats 0.401 as a **worst-case loss fraction of the entry price**. That correspondence only holds if every one of the 10 days actually locks LC at exactly −5% with zero intraday recovery. The CROPSTER reference case (Section 1.E) measured **−39.66% over 10 sessions**, i.e., *not* 10 consecutive locked-limit days — some sessions evidently traded above the prior day's LC floor or the band was not always 5%. This means 0.401 is not calibrated to an observed worst case; it is calibrated to an idealized worst case that happens to sit close to (but on the safe side of) the one real data point available. With n=1 historical exemplar, "close to the ideal case" is not validation — it is coincidence pending more samples. [ASSUMED status for 0.401 as a real-world bound; DERIVED status only for the arithmetic identity itself.]
3. **Circuit-band non-stationarity breaks the fixed-0.95 assumption.** Track 1's own framework (per Codex Section 3.B.3, BSE ESM Stage I/II) tightens bands to 5% → 2% upon surveillance escalation. If a Rule 7 position gets caught by a Rule 6 band-tightening event mid-drawdown (which Rule 6 itself anticipates), the effective daily decay rate *drops* to 0.98, meaning **more days are required to reach a given nominal loss, but exit liquidity also disappears faster in percentage terms per unit time** because 2% bands imply thinner absolute rupee gaps per session but potentially longer full-lockout streaks (BSE Stage II also imposes 100% margin and call-auction-only trading, per Section 3.B.3, which the 0.401 formula does not model at all — it assumes continuous-session compounding throughout). **This is a material gap**: 0.401 has no term for the surveillance-transition scenario that Rule 6 exists specifically to handle. A position sized under Rule 5's continuous-market assumption can be caught in a regime the sizing formula never priced.
4. **Floor/asymmetry check:** $0.95^{10}$ compounding assumes the price never touches the ₹10.00 tick floor (Rule 2) mid-decay, which would change tick granularity and could distort the *realized* percentage move per lower-circuit session (larger relative jumps near round-tick boundaries at low absolute price). For a stock entering near ₹10–15, 10 sessions of 5% LC decay reaches roughly ₹6.0–9.0, which is plausible without hitting exotic sub-tick territory, so this effect is second-order for the price range Rule 2 permits. Not a blocking issue, but worth flagging as unquantified.

**Verdict on 0.401:** Mathematically self-consistent as a stylized worst-case decay constant. **Not validated as a market-realistic maximum-loss bound**, and — critically — it does not model the surveillance-band-tightening interaction with Rule 6 that the strategy explicitly acknowledges can occur. Treat 0.401 as a *floor* on required capital reserve, not a *ceiling* on possible loss. This matches Codex's Section 3.D.5 finding independently arrived at from the code/data-provenance angle; I arrive at it from the compounding-model-mismatch angle.

### C. Re-derivation of the Rule 9 liquidity cap (15% × 2 sessions = 0.30)

**Claim under test:** $Q_{liquidity} = \lfloor 2.0 \times 0.15 \times \text{Daily Volume} \rfloor = \lfloor 0.30 \times \text{Daily Volume}\rfloor$.

1. Arithmetic is trivially correct: $2 \times 0.15 = 0.30$ [DERIVED]. The issue is not the multiplication; it is what "Daily Volume" denotes as an input.
2. **Which day's volume?** The formula does not specify, in Section 1, whether "Daily Volume" is the entry-day volume (which by Entry Criterion 7 is *already* $\ge 3\times \overline{V}_{20d}$ — an elevated, breakout-day print) or the trailing 20-day average. This is a load-bearing ambiguity:
   - If sizing uses the **entry-day elevated volume** (the 3x print), then $Q_{liquidity}$ is calibrated to a volume level that is *by construction* abnormal and has no guarantee of recurring on the two subsequent sessions used for exit (Day 3/4). A single volume spike does not imply two more days of similar liquidity — mean reversion in volume is the norm, not the exception, after a breakout print. This directly threatens the exit side: **Rule 9 sizes the entry against a volume level the exit cannot assume still exists two days later.**
   - If sizing instead uses $\overline{V}_{20d}$ (the calmer baseline, which the "calm-market" label in Section 1.C.2 implies is intended), then $Q_{liquidity}$ is conservative for entry (good) but creates a mismatch with the *actual* fillable quantity on the breakout day itself, which is 3x larger — the position may be needlessly small relative to what the entry-day tape could actually absorb, but that is a conservative direction, not a risk.
   - The Section 1 text's own label "calm-market 15% cap" suggests the baseline average is intended, but the LaTeX formula literally says "Daily Volume" undifferentiated. **This ambiguity must be resolved in code as $\overline{V}_{20d}$, explicitly, or the cap is not calm-market at all — it is spike-market, which inverts the stated intent.**
3. **The 15%/2-session participation rate is a rate of average consolidated tape volume, not a rate of resting displayed depth at the ask/bid the order would actually walk.** Sections 1 and 3 both note (Codex 3.D.1 independently) that whole-session volume is not contra-volume at a specific price level. My independent addition: even granting a perfectly measured $\overline{V}_{20d}$, 15% of *that* volume assumes the participant can be uniformly distributed across the session without moving the price — a standard implicit assumption in participation-rate execution models (e.g., VWAP/POV algos), but one that requires deep, continuous two-sided liquidity. Micro-caps with spread <1% and daily range >3% (the entry filter itself) are, by definition, **not** deep continuous markets — a >3% range on a stock this size implies visible price impact from ordinary retail-size orders. The 15% POV heuristic is imported from institutional-equity execution literature and is unvalidated for Indian micro-cap breadth this thin. This is an [ASSUMED, uncalibrated] parameter, consistent with Codex 3.D.6's finding that all thresholds are policy-assumed, not measured.

**Verdict on Rule 9:** The 0.30 multiplier arithmetic is correct, but the input variable is ambiguously specified between two economically different quantities (spike-day vs. baseline volume) with opposite risk implications. **This is a specification defect, not just a calibration gap**, and should block "ACCEPT" until the code and the doc agree on which volume figure is used — I could not confirm from Section 1 alone which the implementation uses, and Codex's audit of `risk_calculator.py` (Section 3) did not resolve this ambiguity either.

### D. Interaction between Q_capital and Q_liquidity — a counterexample

Consider a stock priced at ₹12, trader risk budget ₹40,100 (chosen so $Q_{capital}$ is a round number):
- $Q_{capital} = \lfloor 40100 / (0.401 \times 12) \rfloor = \lfloor 40100/4.812 \rfloor = 8332$ shares [DERIVED].
- Suppose $\overline{V}_{20d} = 40{,}000$ shares/day (a plausible ESM-adjacent micro-cap baseline) and entry-day volume (3x trigger) $= 120{,}000$ shares.
- If $Q_{liquidity}$ is computed off $\overline{V}_{20d}$: $\lfloor 0.30 \times 40000 \rfloor = 12{,}000$ shares → **binding constraint is $Q_{capital}=8332$.**
- If computed off entry-day volume: $\lfloor 0.30\times120000\rfloor=36{,}000$ → still $Q_{capital}$ binds.
- In this example the ambiguity in Section C doesn't change the *order size* because capital happens to bind either way — **but it changes the claimed liquidity safety margin**. At 8,332 shares against a $\overline{V}_{20d}$ of 40,000, the order is 20.8% of the *baseline* day's volume, not 15%, once the elevated entry-day volume mean-reverts on Day 3/4 exit — i.e., the participation rate implicitly *rises* on the exit day precisely when the strategy needs the queue to still be liquid. **This is the core adverse-selection mechanism the strategy must confront: sizing is calibrated to Day-0 (peak) liquidity, but exit execution happens on Day 3/4 (post-peak, likely mean-reverted) liquidity.** Neither Section 1 nor Section 3 quantifies this decay. I flag it here as the single largest unmodeled risk in the sizing chain.

### E. Pre-emptive Day 3/4 exit feasibility — queue mechanics

**Claim under test:** Selling into the resting Upper Circuit buyer queue on Day 3/4 at +10–15% gain is executable as a "pre-emptive" (non-locked) exit.

1. **This is not queue-jumping into a locked circuit — it is aggressive marketable selling against a resting bid stack that happens to sit at the UC price.** By definition, if the stock is *not* locked (two-sided, Section 1.B.4 requires bids AND offers), a bid resting at the UC price is just the best bid, not a "circuit queue" in the FIFO-lockout sense that applies once the stock actually locks. Section 1.D's framing ("into the resting Upper Circuit buyer queue") conflates two distinct market states:
   - **State A (pre-lock, two-sided):** UC price = current best bid, but offers still exist above/at it. A limit-sell at that bid executes as ordinary price-time-priority matching. This is what Day 3/4 exit actually is, per the entry criteria's own two-sidedness requirement.
   - **State B (locked):** Zero offers, UC bid stack has queued FIFO buy orders and no matching sell flow — this is the CCDL post-exit scenario in Section 1.E (0 bids... wait, note Section 1.E's CCDL post-exit shows **0 BIDS, 2.05 Cr OFFERS** — that is a *locked lower circuit* pattern (sellers stuck), not an upper-circuit lockout. This is worth flagging: the CCDL exemplar cited as "Tail Risk Demonstration" is actually evidence of the *opposite* circuit direction (LC lockout after exit) rather than a UC entry-queue mechanic. It's relevant as evidence for Rule 5's downside case, not for the Day 3/4 UC-exit mechanic it's proximate to in the table.
   
   Given State A is what Section 1.D actually describes ("Sell limit order submitted... when gross unrealized gain ≥ +10%"), the exit is **ordinary limit-order execution in a still-liquid two-sided market**, not a special "pre-emptive queue" mechanism. The strategic insight — exit before the stock actually locks up and becomes illiquid — is sound as a *timing* heuristic, but Section 1's language overstates the mechanism as something structurally different from a normal sell, when mechanically it is not. Codex's Section 3.D.3 makes the adjacent but distinct point about price-time priority vs. displayed aggregate depth; my point is about the conceptual mislabeling of a normal limit sell as a "queue" exploit.

2. **Feasibility stress scenario:** if Day 3 gain is driven by continued one-sided demand (which is the accumulation-breakout thesis itself — institutional buying pushing price toward UC), then by the time gain reaches +10%, the two-sidedness condition from entry (Section 1.B.4) is exactly what is *eroding* — offers thin out as the stock approaches its own UC on day 3/4 rally, which is the same dynamic that produces circuit locks in the first place. **The exit mechanism is most fragile exactly when the entry thesis is most successful**: a strong accumulation move that justifies the +10-15% target is also the move most likely to exhaust offers and produce a same-day lock before the limit sell fills. This is a structural tension the strategy doc does not resolve: success and exit-liquidity are anti-correlated for this specific setup. This is a novel finding not covered in Sections 1 or 3.

3. **Counterexample using the CCDL evidence itself:** the cited "empirical proof" (Section 1.E, +4.55% same-day exit ₹1.32→₹1.38) is a **44 paisa move on a sub-₹10 stock disqualified by Rule 2** (also flagged by Codex 3.D.4). Independently: 4.55% is also well below the +10–15% target band the strategy requires for the Day 3/4 rule to trigger. The one cited "proof" data point does not actually instantiate the rule being tested (wrong price tier, wrong gain threshold, wrong day-count — executed same day, not Day 3/4). **n=0 valid confirming observations exist in the document for the Day 3/4 mechanism as specified.**

### F. Entry-criteria numerical stress test

Running the stated thresholds together against a synthetic order book to check for vacuous or self-contradictory qualification:
- Spread <1% AND daily range ≥3%: not contradictory — a stock can have a tight quoted spread while still ranging >3% intraday (spread is a snapshot, range is peak-to-trough). Compatible. [DERIVED, no issue found]
- Remaining band ≥15% of (UC−LC) AND price within 3 ticks of UC (Section 1.B.3, bullet 1: "Price < UC − 3×₹0.01"): **these two sub-conditions are in tension but not contradictory.** For a 5% band stock at ₹100 (UC=₹105, LC=₹95, band width ₹10), 15% of band = ₹1.50 remaining, meaning qualifying price range is ₹103.50–₹104.97 (105 − 0.03). That is a **147-paise-wide qualifying window** (₹103.50 to ₹104.97) at ₹100 price level — a narrow but non-empty band. At lower prices (say ₹15, 5% band, UC=₹15.75, LC=₹14.25, width ₹1.50), 15% of band = ₹0.225, qualifying window is ₹15.525–₹15.72 — only **19.5 paise wide**, i.e., roughly 20 ticks at ₹0.01 granularity. **This means the entry window narrows sharply in absolute rupee terms as price decreases toward the ₹10 floor**, making the signal timing-critical and likely to be skipped between polling intervals for low-price names — an operational/latency risk not discussed in either Section 1 or Section 3. [DERIVED from stated formulas; 2026-09-12]

### G. Summary of counterexamples and stress findings

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | 0.401 divisor doesn't model Rule 6 surveillance-band-tightening interaction (5%→2% mid-drawdown) | High | New (not in Sec 1 or 3) |
| 2 | Rule 9 "Daily Volume" input is ambiguous between spike-day and $\overline{V}_{20d}$, with opposite risk direction | High — spec defect | New |
| 3 | Sizing calibrated to Day-0 peak volume; exit occurs Day 3/4 on likely mean-reverted (lower) volume — participation rate rises exactly when liquidity risk is highest | High | New |
| 4 | Day 3/4 "queue" exit is conceptually a normal limit sell in a still-two-sided market, not a distinct queue mechanism; mislabeling risks overconfidence in execution certainty | Medium | New framing on top of Codex 3.D.3 |
| 5 | Exit-liquidity and entry-thesis-success are anti-correlated (successful accumulation erodes the same two-sidedness the exit depends on) | High | New |
| 6 | CCDL "proof" data point fails Rule 2 price floor, fails the +10-15% gain threshold, and was same-day not Day-3/4 — zero valid confirming observations for the mechanism it's cited to support | High | Reinforces Codex 3.D.4 |
| 7 | Entry qualifying price window narrows to ~20 ticks at low price tiers near the ₹10 floor — latency/polling risk | Medium | New |
| 8 | ₹0.01 tick / inward truncation and 0.401 arithmetic are internally correct | — | Confirmed, no defect |

### H. Formal Verdict

**VERDICT: ACCEPT WITH CONDITIONS — paper observation only, and only after the Rule 9 volume-input ambiguity (Finding #2) is resolved in code and documentation.**

This independently corroborates the ACCEPT WITH CONDITIONS verdicts already reached by Antigravity (Section 1.F) and Codex (Section 3.F), reached via distinct quantitative and mechanics-based reasoning rather than by deference. My additional conditions, beyond those already stated in Sections 1 and 3:

1. **Resolve and hard-code which volume figure feeds $Q_{liquidity}$** (entry-day vs. $\overline{V}_{20d}$) before any paper trade counts; document the choice with an explicit code citation and provenance entry.
2. **Add an explicit Rule 5 × Rule 6 interaction model**: 0.401 must be revised or supplemented with a scenario where surveillance escalates mid-drawdown, since the current formula assumes continuous 5%-band compounding for all 10 sessions.
3. **Re-label the Day 3/4 exit mechanism** in documentation as a standard limit sell executed early while two-sidedness persists, not a "queue" exploit, to avoid overstating execution certainty to future paper-log reviewers.
4. **Track exit-day (Day 3/4) volume against entry-day volume as a required paper-log field** to empirically test Finding #3/#5 (the liquidity-decay-vs-thesis-success tension) — this is the single most important unmeasured relationship in the whole strategy and is fully absent from the current provenance table (Section 1.E).
5. **No live capital deployment** — concur fully with the Rule 1 gate as stated by both prior submissions.

Every threshold examined (3x volume, 1% spread, 3% range, 15% participation, 2-session horizon, 0.401 divisor, +10–15% target) remains ASSUMED/policy-level pending prospective, session-logged calibration; none should be treated as validated even though the arithmetic underlying each is independently confirmed correct in this review.

## 3. Independent Regulatory, Broker RMS & Execution Review (OpenAI Codex Submission)

### A. Scope and Method
This review independently audits the Rule 7 Pre-Circuit Accumulation Breakout proposal in Section 1 against statutory regulations (SEBI circulars, BSE/NSE notices), broker operational realities (Zerodha RMS, T2T settlement, CDSL TPIN/DDPI), and code behavior in `antigravity/models/circuit_rules.py`, `accumulation_screener.py`, `risk_calculator.py`, and `live_signal_engine.py`. No code was modified during this audit. The adversarial findings of Claude Code (Section 2) were reviewed and integrated into this assessment.

### B. Exchange and Broker Regulatory Plumbing

1. **T2T T+1 Settlement Realism & Demat Credit Dependency:**
   - Under Zerodha's Trade-to-Trade policy ([Zerodha T2T Support](https://support.zerodha.com/category/trading-and-markets/trading-faqs/general/articles/what-are-trade-to-trade-stocks)), shares bought in the Trade-to-Trade segment (`BE`, `T`, `XT`) **can be sold on T+1 day** into delivery settlement. Same-day selling (intraday square-off on Day T) is strictly prohibited.
   - However, execution in practice requires that the shares are credited to the client's Beneficiary Owner (BO) demat account or cleared by the clearing corporation (ICCL/NCL) before RMS releases the holding for sale. If a delivery shortage occurs from the original seller, the T+1 sell order will be rejected or forced into an auction deficit.
   - Therefore, Day T+1 sellability cannot be modeled as a deterministic certainty. In paper-trading logs, T+1 sell orders must record explicit `demat_credit_state` and broker RMS acceptance.

2. **CDSL TPIN vs. DDPI Authorisation:**
   - Non-DDPI client accounts require daily CDSL TPIN + OTP authorisation (open after 07:00 AM IST) before sell orders can route to the exchange ([Zerodha TPIN Support](https://support.zerodha.com/category/trading-and-markets/trading-faqs/general/articles/tpin-preauthorisation)). Accounts with active Demat Debit and Pledge Instruction (DDPI) bypass this step ([Zerodha DDPI Support](https://support.zerodha.com/category/your-zerodha-account/your-profile/ddpi/articles/activate-ddpi)).
   - Neither TPIN nor DDPI creates market liquidity, preserves queue priority, or guarantees fill execution. Paper logs must track `authorisation_state` (`DDPI_ACTIVE` vs. `TPIN_VALID`) independently of market order states.

3. **Surveillance Escalation & Periodic Call Auction (PCAS):**
   - Under BSE Notice 20230718-46 / NSE Circular NSE/SURV/57609 (and 2024 amendments NSE/SURV/63361, 64066, 64400), securities entering ESM Stage 2 are placed in a **±2% price band with 100% margin and trade exclusively via 1-hour Periodic Call Auction Sessions (PCAS)** across 6 sessions per day (SEBI CIR/MRD/DP/6/2013).
   - Once a stock transitions into ESM Stage 2, continuous-market limit order assumptions fail completely. Unmatched orders purge or roll into subsequent auctions on single equilibrium price discovery.

4. **Auction Close-Out & Delivery Shortages:**
   - If the counterparty from Day T defaults on delivery, the clearing corporation conducts an auction on T+1.
   - In micro-caps where auction offers are unavailable, clearing corporations enforce mandatory cash close-out at the highest price from trade day to auction day or 20% over settlement price ([Zerodha Short Delivery Support](https://support.zerodha.com/category/trading-and-markets/trading-faqs/general/articles/what-is-short-delivery-and-what-are-its-consequences)).

### C. Code Architecture & Input Provenance Audit

1. **Live Pipeline Missing Provenance & Synthesis Risk:**
   - In `live_signal_engine.py`, missing values must never be artificially synthesized. For example, falling back to LTP when `prev_close` is missing, defaulting to a 5% band when circuit limits are absent, or assuming 1.0x volume when volume history is empty are fail-open defects.
   - The engine must strictly fail-closed to `DATA_INVALID` whenever any upstream market or band input is absent, stale, or malformed.

2. **Numeric and Schema Validation:**
   - `accumulation_screener.py` and `circuit_rules.py` must enforce finite, non-null checks on spread, daily range, and 20-day baseline average volume ($\overline{V}_{20d} > 0$).
   - A zero volume baseline ($\overline{V}_{20d} = 0$) previously produced a spurious 1,000,000x volume expansion. This defect has been patched and verified in the adversarial test suite.

3. **Track 1 Adversarial Test Suite Conformance:**
   - All 22 tests in `tests/test_track1_adversarial.py` pass 100%, verifying that:
     - Sub-₹10 securities fail closed (`SUB_RS_10_PRICE_FLOOR_VIOLATION`).
     - Missing or zero-depth books return `DATA_INVALID`.
     - Surveillance scrips return `CRITICAL_AVOID`.
     - Locked upper circuits block buy entries.

### D. Microstructure & Execution Realism

1. **Whole-Session Volume vs. Queue Advancement:**
   - Whole-session turnover does not measure or establish FIFO queue advancement at a specific price level. Volume executed across continuous trading occurs at fluctuating price levels and cannot be assumed to drain an order's queue rank $R$.
   - A discrete 4-state execution model (`LOCKED_NO_BID`, `QUEUED`, `PARTIAL`, `FILLED`) is mandatory.

2. **Continuous Market vs. PCAS Call Auction Separation:**
   - Continuous two-sided order books and PCAS call auctions are structurally distinct execution regimes. Continuous queue models must not be applied to PCAS auction sessions.

3. **Day 3/4 Exit Framing:**
   - Placing a limit sell at or near the Upper Circuit when two-sided liquidity exists is an ordinary limit sell matching against resting bids on price-time priority. It is not an exploit of a "circuit queue".

4. **Historical Trades Do Not Validate Rule 7 Expectancy:**
   - **CCDL (+4.55% on 10-Sep-2026):** Disqualified by Rule 2 (Price ₹1.32 < ₹10.00), executed below the +10–15% profit target, and exited on Day 1/2 rather than Day 3/4.
   - **CROPSTER (−39.66%):** Validates the 10-day LC tail loss disaster anchor, but provides zero empirical proof of positive forward expectancy for accumulation breakouts.
   - Therefore, valid confirming empirical observations for the Rule 7 setup stand strictly at $n = 0$.

5. **Rule 5 Loss Divisor (0.401):**
   - The formula $1 - 0.95^{10} = 0.40126... \approx 0.401$ is mathematically exact for 10 sessions of unbroken 5% LC descent.
   - However, 0.401 operates as a capital reserve floor, not an empirical guarantee that losses cannot exceed 40.1% if surveillance freezes or tick-size distortion intervenes.

6. **Policy Parameter Classification:**
   - Every numerical strategy parameter (3.0x volume expansion, 1.0% spread ceiling, 3.0% daily range floor, 15% participation rate, 2-session clearable horizon, +10–15% profit target) is classified as an **`ASSUMED` policy prior**, not a measured empirical constant.

### E. Independent Evaluation of Claude Code Findings (F1–F8)

| Claude Finding | Codex Evaluation | Required Action / Disposition |
| :--- | :---: | :--- |
| **F1: Rule 5 $\times$ Rule 6 Escalation** | **CONCUR** | Sizing under 0.401 does not model band tightening (5% $\to$ 2% PCAS) mid-drawdown. Must be treated as capital reserve floor. |
| **F2: Rule 9 Volume Input Ambiguity** | **CONCUR** | Sizing against entry-day spike volume inverts calm-market intent. Must strictly specify $\overline{V}_{20d}$. |
| **F3: Post-Peak Exit Liquidity Decay** | **CONCUR** | Participation rate increases on Day 3/4 if volume mean-reverts after breakout. Sizing must be conservative. |
| **F4: Day 3/4 Exit Mechanism Framing** | **CONCUR** | Ordinary limit sell into two-sided liquidity, not a special "queue exploit". |
| **F5: Thesis-Liquidity Anti-Correlation** | **CONCUR** | Strong accumulation pushes price to UC, eroding the very offers needed for two-sided continuous exit. |
| **F6: CCDL Non-Validation** | **CONCUR** | CCDL fails Rule 2, fails target %, and fails day count. $n=0$ confirming evidence for Rule 7. |
| **F7: Narrowing Entry Window Near ₹10** | **CONCUR** | At ₹10–15 price tier, qualifying window narrows to ~20 ticks, creating polling latency risk. |
| **F8: Tick Arithmetic & 0.401 Math** | **CONFIRMED** | Compound arithmetic is verified correct. |

### F. OpenAI Codex Formal Verdict

**VERDICT: ACCEPT WITH CONDITIONS — Paper Observation Only.**

**Mandatory Conditions for Prospective Observation:**
1. **Rule 1 Absolute Invariant:** Real capital deployment is strictly prohibited (100% Cash). Gate count remains 0/60 prospective sessions and 0/20 fills.
2. **Demat Credit & Broker RMS Validation:** Paper logs must record explicit broker delivery credit and RMS acceptance states before logging any T+1 exit fill.
3. **Fail-Closed Pipeline:** `live_signal_engine.py` must never synthesize missing inputs; missing or unverified data must output `DATA_INVALID`.
4. **Volume Baseline Hardcoding:** Rule 9 sizing must explicitly take baseline 20-day median/average volume ($\overline{V}_{20d}$), never entry-day spike volume.
5. **No Watchlist Scrips Eligible Today:** CCDL, CROPSTER, and GATECH fail Rule 2; CHANDRIMA fails Rule 6. Zero scrips are currently eligible.

---

## 4. Tri-Agent Synthesis & Consensus Adjudication

### A. Final Tri-Agent Consensus Ledger

| Strategy Item / Dimension | Antigravity Status | Claude Code Status | OpenAI Codex Status | Unanimous Consensus Adjudication |
| :--- | :---: | :---: | :---: | :---: |
| **Rule 1: Observation Only (100% Cash)** | `ACCEPT` | `ACCEPT` | `ACCEPT` | **UNANIMOUS CONSENSUS (100% Cash)** |
| **Rule 2: Absolute ₹10.00 Floor** | `ACCEPT` | `ACCEPT` | `ACCEPT` | **UNANIMOUS CONSENSUS (All <₹10 Disqualified)** |
| **Rule 3: Prohibition of UC Chasing** | `ACCEPT` | `ACCEPT` | `ACCEPT` | **UNANIMOUS CONSENSUS (No Locked UC Buy Orders)** |
| **Rule 4: Discrete 4-State Execution** | `ACCEPT` | `ACCEPT` | `ACCEPT` | **UNANIMOUS CONSENSUS (Discrete States Mandatory)** |
| **Rule 5: 10-Day LC Sizing Divisor (0.401)** | `ACCEPT` | `ACCEPT WITH CONDITIONS` | `ACCEPT WITH CONDITIONS` | **UNANIMOUS CONSENSUS (Arithmetic Confirmed; Capital Floor Only)** |
| **Rule 6: Surveillance Pre-Emption & Exit** | `ACCEPT` | `ACCEPT` | `ACCEPT` | **UNANIMOUS CONSENSUS (Strict Rule 10 Override)** |
| **Rule 7: Pre-Circuit Accumulation Base** | `ACCEPT WITH CONDITIONS` | `ACCEPT WITH CONDITIONS` | `ACCEPT WITH CONDITIONS` | **UNANIMOUS CONSENSUS (Unmeasured Empirical Hypothesis)** |
| **Rule 9: Liquidity Participation Gate** | `ACCEPT` | `ACCEPT WITH CONDITIONS` | `ACCEPT WITH CONDITIONS` | **UNANIMOUS CONSENSUS (Must Use Baseline $\overline{V}_{20d}$)** |
| **Rule 10: Strict Precedence Hierarchy** | `ACCEPT` | `ACCEPT` | `ACCEPT` | **UNANIMOUS CONSENSUS (Rule 6 Overrides Rule 7)** |
| **Rule 11: Absolute Track Isolation** | `ACCEPT` | `ACCEPT` | `ACCEPT` | **UNANIMOUS CONSENSUS (Track 1 & Track 2 Strictly Decoupled)** |

**Overall Tri-Agent Verdict:** **ACCEPT WITH CONDITIONS — Prospective Paper Observation Only.**

---

### B. Summary of Consensus Agreements (6 Core Pillars)

1. **Rule 1 Invariant (100% Cash):** All three agents agree that zero real capital may be deployed. The strategy is approved solely as a formal hypothesis for prospective paper-trading observation in `CHATGPT/observation_log.csv` and `shared/track1_esm/03_TRADE_LOG.md`. The gate remains strictly **0 / 60 prospective sessions and 0 / 20 realistically fillable entries**.
2. **Rule 2 Price Floor Disqualification:** All three agents agree that securities trading below ₹10.00 suffer fatal tick-size distortion and are immediately disqualified. CCDL (₹1.32), CROPSTER (₹3.02), and GATECH (₹0.75) are permanently ineligible. Currently, **0 watchlist scrips qualify for entry**.
3. **Rule 5 Arithmetic Identity:** The compound calculation $1 - (1 - 0.05)^{10} = 0.401263... \approx 0.401$ is algebraically verified. All three agents agree it represents the stylized loss of 10 consecutive 5% lower-circuit sessions.
4. **Adversarial Robustness:** The 22-test adversarial suite in `tests/test_track1_adversarial.py` passes 100%, proving that the code fails closed on missing depth, zero volume, negative numbers, and boundary violations.
5. **Policy Parameter Classification:** All strategy thresholds (3.0x volume expansion, 1.0% spread, 3.0% daily range, 15% participation rate, 2-session horizon, +10–15% target) are agreed to be **`ASSUMED` policy priors**, not validated empirical facts.
6. **Track Isolation (Rule 11):** Track 1 (ESM & Circuit Micro-Caps) is completely decoupled from Track 2 (Liquid F&O Momentum).

---

### C. Open Objections & Conditions Requiring Resolution

1. **Rule 9 Volume Input Ambiguity (Claude Finding #2 & Codex Finding #4):**
   - *Objection:* Section 1.C.2 stated "Daily Volume" without distinguishing entry-day volume from baseline volume. Sizing off the entry-day 3x volume spike sizes a position against peak liquidity that mean-reverts before the Day 3/4 exit, raising the realized exit participation rate above 15%.
   - *Condition:* Code and documentation must strictly hardcode the baseline 20-day average volume ($\overline{V}_{20d}$) as the input to $Q_{liquidity}$.
2. **Rule 5 $\times$ Rule 6 Interaction & Surveillance Escalation (Claude Finding #1 & Codex Section 3.B.3):**
   - *Objection:* Sizing via 0.401 assumes continuous-market 5% LC decay and does not model surveillance band cuts (5% $\to$ 2% PCAS) mid-drawdown. Under ESM Stage 2, daily loss drops to 2%, but liquidity completely evaporates into 1-hour call auctions.
   - *Condition:* 0.401 must be treated strictly as a **capital reserve floor**, never an empirical guarantee against losses exceeding 40.1%.
3. **T2T Broker Settlement Realism (Codex Section 3.B.1):**
   - *Objection:* Zerodha allows T+1 selling in T2T stocks, but execution is contingent upon demat credit to the BO account and broker RMS release.
   - *Condition:* Paper execution logs must record `demat_credit_state` and broker RMS acceptance; T+1 exits must never be assumed automatic.
4. **Thesis-Liquidity Anti-Correlation (Claude Finding #5):**
   - *Objection:* A strong accumulation breakout pushes price toward the Upper Circuit, which by definition exhausts offers and erodes the two-sided liquidity needed for an exit. Success and exit liquidity are structurally anti-correlated.
   - *Condition:* Paper logs must track exit-day volume against entry-day volume to measure the rate of liquidity decay.
5. **Day 3/4 Exit Framing (Claude Finding #4 & Codex Section 3.D.3):**
   - *Objection:* Framing the exit as "selling into the UC buyer queue" implies a special queue exploit. Mechanically, it is an ordinary limit sell into remaining two-sided liquidity.
   - *Condition:* Documentation and logs must label it as a standard pre-emptive limit sell in a two-sided book.

---

### D. Evidence Needed to Close Open Conditions

1. **Bhavcopy Circuit Continuation Dataset:** Historical multi-year BSE/NSE bhavcopy data measuring the actual conditional probability $P(\text{UC Lock at } T+1 \mid \text{UC Lock at } T)$ for non-surveillance micro-caps priced ₹10–₹50.
2. **Post-Breakout Volume Decay Series:** Empirical measurement of volume turnover on Day T+2, T+3, and T+4 following a 3x volume expansion print to quantify volume mean-reversion.
3. **Broker RMS Execution Log:** Empirical capture of Zerodha order rejection/acceptance codes for T2T scrips placed at 09:00:01 AM on Day T+1.
4. **Surveillance Transition Frequency:** Event study measuring the probability that an accumulation breakout stock triggers ESM Stage 1 or a price band cut within 5 trading sessions.

---

### E. Exact Tests Required Before Paper Logging

1. **Volume Baseline Sizing Test:** Verify that `risk_calculator.py` uses `avg_20d_volume` exclusively and rejects entry-day spike volume for $Q_{liquidity}$ sizing.
2. **Fail-Closed Synthesis Test:** Verify that `live_signal_engine.py` emits `DATA_INVALID` if `prev_close`, `circuit_limit_pct`, or `avg_20d_volume` is null or zero.
3. **Order State Conformance Test:** Verify that paper execution logs strictly conform to the 4 canonical market states (`LOCKED_NO_BID`, `QUEUED`, `PARTIAL`, `FILLED`) decoupled from broker order states (`SUBMITTED`, `ACCEPTED`, `REJECTED`).
4. **Entry Window Latency Test:** Measure polling latency against the 20-tick qualifying price window for stocks priced near ₹10.00.

---

### F. Final Determination & Eligibility for Prospective Paper Observation

- **Strategy Eligibility:** **ELIGIBLE FOR PROSPECTIVE PAPER OBSERVATION ONLY.**
- **Active Watchlist Eligibility:** **0 Scrips Eligible.** (CCDL, CROPSTER, GATECH fail Rule 2; CHANDRIMA fails Rule 6).
- **Capital Deployment:** **STRICTLY PROHIBITED (100% Cash).**
- **Mandatory Paper Gate:** **0 / 60 Prospective Sessions | 0 / 20 Realistically Fillable Entries.**
- **Next Action:** Monitor BSE/NSE bhavcopy daily for new candidates meeting all Rule 1–11 criteria. No paper log entry may be recorded without satisfying all consensus conditions.


