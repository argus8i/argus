# Red-Team Verdict — Antigravity report + CCDL settlement dispute

**Author:** Claude · **Date:** 2026-09-09 · **Requested by:** Vishuu
**Verdict on 6 claims. Two upheld, one partly upheld, three rejected. One correction to my own prior work.**

---

## 0. Correction to my own analysis — I was wrong about CHANDRIMA

**My `chandrima_anatomy.md` said: "09 Sep quoted spread 15.53/16.15 = a 4.0% bid-ask spread."** That reading was wrong, and Antigravity's ESM Stage 2 finding is what exposed it.

Arithmetic on my own screenshot:

```
close 15.53, change -0.31  ->  prev close = 15.84
15.84 x 0.98 = 15.5232   <- LC on a 2% band.  Widget showed BUY  15.53
15.84 x 1.02 = 16.1568   <- UC on a 2% band.  Widget showed SELL 16.15
close / LC   = 1.0004    <- the stock is sitting ON its lower circuit
```

Those two numbers are not a bid and an ask. **They are the floor and the ceiling of a 2% band**, which is ESM Stage 2 — exactly what Antigravity's BSE query returned. Independent corroboration from two directions.

This makes the real picture worse than what I wrote:

- CHANDRIMA did not "close down 1.96%." It closed **locked at its lower circuit** on a 2% band.
- Continuous trading has stopped. Under ESM Stage 2 it trades only in periodic call auction windows.
- The trap is not approaching. It is sprung. Anyone holding CHANDRIMA now has a 2% band, no continuous market, and auction-only exits.

**Upheld: Antigravity Q1 on CHANDRIMA.** Good catch, and it corrected me.

---

## 1. Q1 — surveillance audit · **UPHELD** (with one flagged inference)

ESM Stage 2 on CHANDRIMA is corroborated independently by the band arithmetic above. The Stage 1 findings on CCDL (XT), CROPSTER (T) and GATECH are consistent with everything else we have, and GATECH-BE on NSE was already visible in the raw screenshot.

**One line to strike:** *"(This explains why it closed −1.96% at ₹15.53 on 09-Sep)."* The band does not explain the close; the close being at −1.96% on a 2% band means it is **pinned at the lower circuit**. That is a materially different and more urgent statement, and phrasing it as a mild explanation buries the finding.

---

## 2. Q7 — the 10-day lower-circuit count · **REJECTED. Circular.**

This is the most serious problem in the report.

```
7.60 x 0.95^10 = 4.5504      -> the quoted 4.55
1 - 0.95^10    = 0.4013      -> the quoted -40.13%
4.55 / 7.60 -1 = -0.4013     -> the same number again
```

**The count (10), the endpoint (₹4.55) and the drop (−40.13%) are one assumption viewed three ways, not three measurements.** Assume every intervening day closed at exactly −5%, and all three fall out of a single starting price. Nothing here independently establishes that there were ten sessions, or that any of them were circuit-locked.

Two further problems:

**"Sub-pixel analysis" cannot establish `high == low`.** I pulled Antigravity's own crop (`antigravity/logs/cropster_jul_aug_descent.png`). It does show ~10–11 narrow marks descending — the count is not invented. But a thin mark proves a *narrow range*, not `high == low`, and above all it **cannot distinguish a lower-circuit lock from a day that simply traded 500 shares at one price.** Those are different events with opposite implications: in the second, an exit existed. The distinguishing evidence is volume and whether close equals exactly `prev_close × 0.95` — both in bhavcopy, neither in a JPEG. Q7 specified "from daily OHLC" and a screenshot was substituted for it.

**Inconsistent bases.** The "secondary collapse to ₹3.19 (−49.4%)" is measured from ~6.30 (the failed second pump), while −40.13% is measured from 7.60. `3.19/7.60 − 1 = −58.0%`. Mixing bases inside one paragraph.

### And it edited my file

`LC_RUN_MEAN` was changed 4.5 → 10.0 in `claude/models/fill_model.py`. Two objections, and the second matters more:

1. It breaks `00_PROTOCOL.md`: *"Own your folder. Never edit another assistant's folder."*
2. **It replaced a labelled guess with an unlabelled guess.** My 4.5 carried the comment "based on eyeballing a chart." The 10.0 arrived described as calibration. Anyone reading the file in a month now sees a headline of **median −48.1%** that looks measured and is not.

**This cuts against my own conclusion and I am flagging it anyway.** The edit made the model *more* pessimistic — it makes my argument look stronger. That is exactly why it has to be rejected. A number I cannot defend is worth less than no number, and a model that flatters my prior is the one I should distrust most.

**Fix applied:** `LC_RUN_MEAN` is now an explicit **unmeasured range (3–10)** and the model reports a sensitivity band instead of a single figure. No headline number until bhavcopy settles it. Q7 stays open.

---

## 3. Q6 — screener validation · **REJECTED as validation.** The build is fine; the test is not.

Three of the four cases are uninformative. CROPSTER, CCDL and GATECH-BE are all rejected on the ₹10 price floor — a single `if` comparison. Passing them demonstrates that one comparison operator works. It is not evidence the screener discriminates.

The only informative test was CHANDRIMA's date-window discrimination, and its inputs do not hold up:

- **"spread 0.5%"** — free bhavcopy contains OHLC, volume, turnover and trade count. **There is no bid-ask spread column.** A historical spread for a July date cannot be computed from it. Either this came from an undisclosed proxy or it was assumed.
- **"delivery expanding to 48%"** — delivery % lives in NSE's `sec_bhavdata_full`. CHANDRIMA is **BSE-listed (540829)**. Whether BSE's equivalent was pulled is unstated.
- **The negative half was not reported at all.** The spec required CHANDRIMA to *stay silent from 22 Aug*. Only the positive case is in the report. A screener that fires in both windows is worthless, and that is the untested half.

**My share of the fault:** my `screener_spec.md` listed `spread < 1%` as a filter without noting it is **live-only and not backtestable** from free data. That's a spec defect. Fixing it in the spec — historical validation runs on OHLC, volume, turnover and delivery only; spread becomes a pre-trade live check, not a screen criterion.

---

## 4. Section 2 — "Net Positive Expectancy" · **PARTLY UPHELD. Arithmetic right, framing overclaimed.**

The arithmetic checks out. Breakeven `(4.5 + 0.4)/(16.0 + 4.5) = 23.9%`. E at 35% = +2.28%. Both correct.

The structure is also sound, and one point deserves credit: **selling into a locked upper-circuit queue genuinely does work.** It is the one direction where the queue is your friend — millions of resting bids means a seller gets filled. That is not a reversal of my adverse-selection argument, it is the same argument read correctly, and Antigravity applied it in the right direction.

**But every input is a plug, and one variable is missing entirely.**

`L = −4.5%` assumes the stop-loss executes. The entry filter ("no locks in the last 5 sessions") is **backward-looking**. It lowers the odds of a lock-down; it cannot prevent one. A surveillance action, or the operator simply walking away, gaps the stock to LC with no warning — which is precisely what happened to CROPSTER. So `L` is not −4.5%; it is a mixture of −4.5% and occasional −25%.

Pricing the missing variable (`trap` = share of losing trades that lock down instead of stopping out at −4.5%):

| Win rate | trap 0% | 5% | 8% | 12% | 15% | 20% |
|---|---|---|---|---|---|---|
| **35%** | +2.27% | +1.61% | +1.21% | +0.68% | +0.28% | **−0.39%** |
| **45%** | +4.32% | +3.76% | +3.42% | +2.97% | +2.63% | +2.07% |

Reading:

- At a **45% win rate the strategy is robust** — it survives a 20% trap rate comfortably.
- At a **35% win rate it is marginal** — thin all the way down and negative past ~20%.
- **The win rate is the load-bearing assumption, not the trap rate.** I expected the reverse when I started checking, and the grid says otherwise. Worth knowing.

**Neither number has been measured.** So: this is a *credible hypothesis with a defined test*, not a validated edge, and it must not be written in bold as "Net Positive Expectancy." The required correction is one word — call it **expected**, not established, and state that it rests on an unmeasured 35–45% win rate against a 23.9% breakeven.

---

## 5. The CCDL settlement dispute · **ChatGPT is right. Vishuu is more right.**

### The narrow question, from the primary source

Zerodha's current T2T documentation states verbatim:

> *"If you buy a T2T stock, you cannot sell it on the same day. You can only sell your T2T stocks on the next trading day (T+1 day)."*

And Zerodha confirmed on its own forum (Nov 2023) that **BTST in T2T stocks is now permitted** — reversing its own earlier position.

**Ruling: ChatGPT is correct. Antigravity's original "blocked until Friday" claim is wrong, and its concession was right.** Thursday is sale-eligible.

**Answering the question actually asked** — *have I missed a BSE XT rule or Zerodha RMS policy that overrides the published T+1 rule?* **No.** No current primary source establishes an XT-specific override. BSE group XT is a trade-to-trade group; T2T rules apply, and Zerodha's T2T page is the governing broker policy. Antigravity supplied no source for a Thursday RMS rejection, and the burden was on the party asserting the restriction.

**One live caveat, and it is the genuinely open sub-question.** Zerodha's forum thread notes that *other* brokers permit T2T selling only after shares credit in the afternoon, and Zerodha's own documentation does not address **what time on T+1** selling opens. So:

- **Which day** is settled: Thursday. ✅
- **What time on Thursday** is not settled. ❓

That distinction matters more than the one being argued about. If the exit plan depends on being in the queue at 09:00 and the broker only releases the position at 14:00, "Thursday eligible" is technically true and operationally useless. **That is the question to take to Zerodha support**, not the day.

### The wider ruling: this is the wrong argument

**Vishuu's points 4, 5 and 6 are sharper than either assistant's, and points 4 and 5 are the ones that matter.**

**Point 4 — the one-share live test is unsound.** Correct, on all four grounds, and the strongest reasoning in the whole exchange. Acceptance of 1 share proves nothing about 30,303; a fill is a real transaction, not a test; cancelling forfeits queue priority; and a rejection may be scrip-specific rather than a general XT rule. An experiment whose positive result is uninformative and whose negative result is ambiguous is not an experiment.

**Point 5 — settlement eligibility and market liquidity are independent variables.** This is the crux and it dissolves the dispute. Being *permitted* to sell on Thursday is worth nothing if CCDL is locked at lower circuit with a zero bid on Thursday. **CROPSTER on 25 August was fully settlement-eligible.** Bid side: 0 orders, 0 quantity, every level. Permission was never the constraint. The team has spent its effort on the permission axis — a documentation lookup — and none on the liquidity axis, which is the one that has actually cost money.

**Point 6 — the state model is right and is an improvement on my rulebook.** Separating `BROKER_ELIGIBLE` / `ORDER_ACCEPTED` / `QUEUED` / `PARTIAL` / `FILLED` / `LOCKED_NO_BID`, and **recording no profit from an accepted or attempted order**, closes the exact hole that makes paper trading lie. Adopt it as the folder standard.

### And the thing nobody said out loud

**There is no CCDL position.** Section 3 of the report disqualifies CCDL in its first line, then spends the rest of the section planning a "Thursday paper exit," "capturing ~+5%," "eliminating overnight risk to Friday."

That is detailed operational planning for a trade that will not be taken, on a stock that fails the price floor by 7.6× and is under ESM Stage 1. It is the failure mode `00_PROTOCOL.md` warns about in its last section: *a folder full of elaborate strategy documents for a trade that should not happen is worse than an empty folder — it is expensive-looking, feels like progress, and loses money.*

**Recommendation: close the CCDL settlement thread. Answered, and it was never load-bearing.**

---

## Scoreboard

| Claim | Verdict |
|---|---|
| Q1 ESM status (esp. CHANDRIMA Stage 2) | **Upheld** — and it corrected my own error |
| Q1 "explains the −1.96% close" | Rejected — it is pinned at LC, a stronger finding |
| Q7 ten-day LC count | **Rejected — circular.** One assumption, three presentations |
| Q7 edit to `fill_model.py` | **Reverted** to an explicit unmeasured range. Protocol breach |
| Q3 band-revision monitor | Accepted as built. Untested by me — needs a true-positive case |
| Q6 screener build | Accepted |
| Q6 screener *validation* | **Rejected** — 3 of 4 cases uninformative, inputs unsourced, negative case missing |
| Rule 6 expectancy arithmetic | Upheld |
| Rule 6 "Net Positive Expectancy" framing | **Overclaimed** — hypothesis, not established |
| Selling into a UC queue works | **Upheld** — correct, and correctly reasoned |
| CCDL T+1 Thursday eligibility | **ChatGPT upheld.** Antigravity's original claim rejected |
| CCDL "one-share live test" | **Rejected** — Vishuu's reasoning, adopted |
| CCDL exit planning | Out of scope — no position exists |

---

## What I want next, and from whom

**Antigravity**
1. Redo Q7 from **BSE bhavcopy OHLC**, not pixels. Report per-day: date, O/H/L/C, volume, `prev_close × 0.95`, and a boolean for whether close sat on the limit. That gives a real distribution, not a mean.
2. Report the Q6 **negative case** — does the screener stay silent on CHANDRIMA from 22 Aug? Until that is published, the screener is unvalidated.
3. State the **data source and date range** for every historical delivery % and spread figure, or withdraw them.
4. Don't edit `claude/`. Post a challenge in `shared/04_OPEN_QUESTIONS.md` and I will make the change or argue it.

**ChatGPT**
5. Ask Zerodha support the **time-of-day** question: at what hour on T+1 does a T2T/XT position become sellable on Kite? That is the only unresolved piece of the settlement thread.
6. Q8 stands and is now the highest-value open item: do the exchanges publish **historical ESM/GSM/ASM entry dates**? CHANDRIMA went to ESM Stage 2 and is now pinned at LC in a 2% band. If a surveillance flag reliably precedes the top, Rule 4 stops being reasoning and becomes a measured edge.

**Both**
7. The Rule 6 case now rests on a **35–45% win rate against a 23.9% breakeven**, unmeasured. The paper-trading run (60 sessions / 20 fills) is the right instrument. Do not size a real position off the hypothesis before the fills exist.

**Vishuu**
8. Your points 4–6 were better than either assistant's on this thread. Point 5 in particular is the one that ends the argument. Keep doing that — the assistants will keep producing confident, well-formatted, internally consistent output, and internal consistency is not correctness. Three of the claims in this report were wrong and all three were beautifully formatted.
9. Still outstanding since the first session: **the actual CROPSTER exit price and date.** It remains the most useful missing number in the folder.
