# Tri-Agent Consensus — Peer Review + Self-Audit

**Author:** Claude · **Date:** 2026-09-11

---

## 0. STOP — I did not produce "Module 5a" or the 8-scrip data pack

The brief says:

> *"Integrated **Claude's 11-Sep data pack** into `liquid_momentum_screener.py`"*
> *"Does Antigravity's implementation accurately translate **your Module 5a specification**?"*

**There is no Module 5a. There is no data pack. I have never mentioned CDSL, ANGELONE, SUZLON, INOXWIND, IREDA, RVNL, COCHINSHIP or BDL — not once, in any brief, in any file. I did not propose a liquid momentum track, the basket split, the ≥15% institutional-holding screen, or the ORB parameters.**

This is the **third** attribution incident, and it is escalating:

| # | Date | What was attributed to me | Severity |
|---|---|---|---|
| 1 | 11-Sep | BSE Notice 20230718-46, NSE/SURV/57609, SEBI CIR/MRD/DP/6/2013 | citations |
| 2 | 11-Sep | ρ = 0.30 on 27-Aug as an "empirical anchor" (caveat stripped) | a parameter |
| 3 | **today** | **An entire strategy track with a vetted 8-stock universe** | **a whole track** |

Incident 1 is still standing uncorrected in `01_MARKET_MECHANICS.md`. Incident 2 got hard-coded into a shipped model. Incident 3 is scheduled to be paper-traded on Monday.

**I cannot review my own specification for fidelity when I never wrote one.** Whoever selected those 8 names — Antigravity, or a model summarising a conversation it didn't have — owns that work and should sign it. Until then Track 2 has **no provenance at all**, which is a worse problem than any modelling flaw in it.

**This is the single biggest vulnerability before Monday.** Not the ORB parameters. Not the sizing. The fact that a trading universe entered the repo carrying a fabricated byline and nobody noticed.

---

## 1. Track 2 walks around the validation gate

Four days established: 60 sessions / 20 fills before live capital, and Track 1 is unvalidated at **n=0 paper trades**.

Track 2 arrives before a single session has been logged, with a different universe, different instruments (F&O underlyings), different timeframe (intraday MIS), different mechanics (ORB, SL-Limit), and different risk parameters — and claims the *same* gate. That doesn't extend the gate; it **halves the evidence per strategy.** 60 sessions across two unrelated strategies is 30 each, and 20 fills becomes 10.

And the deeper point: Track 2 is a genuinely different game. Micro-cap circuit trading is structurally broken but **uncrowded**. Liquid F&O-underlying momentum is structurally fine and **ruthlessly competitive** — you are now trading against prop desks and algos whose entire business is the 09:15–09:30 opening range. Nothing from the last four days transfers. The microstructure work, ρ, the drain model, the liquidity gate — all of it was built for thin books and is irrelevant here.

That may still be the right move. It is not an "expansion." It is a restart, and it should be counted as one.

**On "zero circuit-freeze lockout risk":** true and overstated. F&O underlyings have dynamic bands that flex, so yes, you won't be locked out. But the risk didn't vanish — **it changed shape.** In a micro-cap you can't exit; in SUZLON/IREDA/RVNL you can always exit, at a price set by a 12% gap. The loss distribution's left tail is *shorter and fatter*, not absent. Conflating "no lockout" with "lower risk" is the error to watch.

---

## 2. The SL-M premise is factually wrong

> *"Because NSE banned SL-M orders in cash equity, Antigravity has coded an SL-Limit order mechanism with a 0.5% buffer."*

**It was BSE, not NSE.** BSE discontinued SL-M across equity, BFO, BCD and commodity segments (~Sep/Oct 2023, to curb freak trades). NSE discontinued SL-M for **options**. All 8 Track-2 names are NSE F&O underlyings, so they'd be traded on NSE cash — **where SL-M is available.**

The workaround solves a BSE problem on NSE trades, and it is not free:

| Stop | + buffer | Effective | Target | R:R | Breakeven W |
|---|---|---|---|---|---|
| 1.5% | — | 1.5% | 3.0% | **2.00** | **33.3%** |
| 1.5% | 0.5% | 2.0% | 3.0% | **1.50** | **40.0%** |

**The buffer turns the advertised 1:2 into a 1:1.5 and moves breakeven from 33.3% to 40.0%** — 6.7 points harder, paid to solve a non-problem. If SL-M genuinely isn't available on your broker/route, SL-Limit is correct — but then the R:R must be *restated as 1:1.5*, not advertised as 1:2.

---

## 3. The sizing rule's floor clamp breaks the risk rule

Fixed ₹1,500 risk, notional clamped to [₹25,000, ₹1,00,000]:

| Stop | Implied notional | After clamp | **Actual risk** | |
|---|---|---|---|---|
| 1.5% | 1,00,000 | 1,00,000 | ₹1,500 | OK |
| 6.0% | 25,000 | 25,000 | ₹1,500 | OK |
| 10% | 15,000 | **25,000** | **₹2,500** | **1.7× the rule** |
| 15% | 10,000 | **25,000** | **₹3,750** | **2.5×** |
| 20% | 7,500 | **25,000** | **₹5,000** | **3.3×** |

The ceiling clamp is safe — it only ever reduces risk. **The floor clamp is not.** A minimum position size is mathematically incompatible with fixed-rupee risk.

**Drop the floor.** If the stop is so wide that ₹1,500 of risk buys less than ₹25,000 of stock, that is the sizing rule telling you the trade is too volatile — not a reason to override it.

**And the gap case isn't covered at all.** A −1.5% stop loses ₹1,500 *only if it executes at the stop price*. On ₹1,00,000: a 5% gap is ₹5,000 (3.3×), a 12% gap is ₹12,000 (8×). Track 2b holds CNC overnight, so this is live, and these are exactly the names that gap.

---

## 4. SELF-AUDIT — my own work, same standard

Asked to review my own contributions before this ships. Four things I'd fix, and two errors I caught while writing this section.

**(a) The ₹10 price floor's empirical justification is refuted by my own example.**
I justified it as *"every loser is under ₹5; the only runner is over ₹10"* — n=4, and I repeated it four times. **CHANDRIMA at ₹15.22 is now the worst outcome in the entire book**: ESM Stage 2, 2% band, 6,355 shares/day, a position at 144% of daily volume. My headline counter-example became my headline counter-example.

The floor is probably still right, but on **tick-size mechanics** — at ₹1.32 one tick is 0.76% of price — which is a structural argument, not a statistical one. **Drop the empirical framing. Keep the mechanical one.**

**(b) `MAX_PARTICIPATION = 0.15` is a number I invented, and it decides whether Vishuu's normal size is legal.**

| MAX_PART | CROPSTER | CCDL | GATECH | CHANDRIMA |
|---|---|---|---|---|
| 0.05 | ₹4,47,866 | ₹74,316 | ₹20,173 | **₹4,676** |
| 0.15 | ₹13,43,598 | ₹2,22,948 | ₹60,520 | **₹14,028** |
| 0.20 | ₹17,91,464 | ₹2,97,264 | ₹80,693 | **₹18,704** |

CHANDRIMA's max position swings **4×** on my arbitrary constant, and lands inside his ₹25,000–₹1,00,000 band. My made-up number is doing real gatekeeping work. Tag it `UNCALIBRATED` in the code, not just in prose.

**(c) "Exit discipline is worth ~27 points, the largest controllable variable in the project."** That rests on `QUEUE_MULT = 2.0` and `ZERO_BID_RATE = 0.15`, both invented by me. The *direction* is robust across the range; **the "27 points" is not a measured quantity and I stated it as one.**

**(d) I oversold ρ.** I called it *"worth more than the entire 2-week-average apparatus"* when it had one anchor. That framing is what licensed hard-coding it. My fault as much as Antigravity's.

**Two errors caught inside this self-audit, while writing it:**

1. I drafted *"GATECH's PASS is an artifact of my arbitrary 0.15."* I re-ran it: GATECH passes at 0.05→0.20 and only flips below MP=5.6%. **The claim was false.** Cut.
2. I then wrote the CHANDRIMA swing as *"₹9,352 → ₹37,410."* The table says **₹4,676 → ₹18,704.** Right multiple, wrong endpoints. Fixed.

Both were caught by re-running rather than re-reading. That is the whole method, and it is the answer to the question of how to make handoffs better (§5).

---

## 5. Division of labour — agreed, with one addition

The split is sensible and I'll take the quant/red-team mandate. One correction to my own job description: *"deep empirical literature research (academic journals)"* is where I am **least** reliable — I can't access most paywalled Indian market-microstructure literature, and asking me for it invites plausible-sounding citations. **That line should move to ChatGPT or be struck.** Everything that has actually worked in four days came from arithmetic on your own data, not from literature.

**The handoff fix is one field, not a process.** Every number in every shared file carries:

```
value | produced_by | source | date | status: MEASURED (n=) | DERIVED | ASSUMED
```

Three attribution incidents in one day are not a discipline problem — they're a **missing schema**. A number with no `produced_by` cannot be laundered, because it never gets a byline in the first place.

---

## 6. Which basket first — neither yet; if forced, A

**Basket B is not four positions. It is one.** IREDA, RVNL, COCHINSHIP and BDL are all government-linked PSU theme trades driven by the same policy flow, order-book announcements and divestment news. They co-move. Four names in Basket B gives you 4× the notional of a single correlated bet with none of the diversification you think you're buying — and in a drawdown they gap together.

**Basket A has genuine idiosyncratic drivers**: CDSL and ANGELONE are levered to market *activity* (demat accounts, retail volumes) — a real, distinct, measurable factor. SUZLON and INOXWIND share a renewables theme, so treat those two as one.

So Basket A is effectively 3 independent bets; Basket B is 1. **If you paper-trade anything Monday, trade A** — and log correlations from session one, because that single number will tell you more about Basket B's real risk than 10 sessions of P&L.

But the honest answer to "which first": **neither, until the provenance of that list exists.** You're about to allocate ten sessions of scarce evidence to eight stocks nobody can account for choosing.

---

## 7. Before Monday 09:15

1. **Establish who chose the 8 scrips and on what criteria.** Nothing else matters until this is answered. *(§0)*
2. Correct the SL-M premise; restate R:R as 1:1.5 if the buffer stays. *(§2)*
3. Remove the ₹25,000 floor from the sizing rule. *(§3)*
4. Decide explicitly whether Track 2 restarts the 60-session count or splits it. It cannot silently do the latter. *(§1)*
5. Add the provenance schema. *(§5)*
6. Fix incident 1 — the circulars are still uncorrected. *(§0)*

**Consensus: 100% cash.** Nothing here changes that, and Track 2 makes the gate *more* necessary, not less.

One last thing, and it's the reason for §4. I've been the red team for four days, and my own output carries the same defect I keep flagging: numbers stated with more confidence than their provenance supports. The difference is that nobody has been red-teaming me. **The provenance schema should apply to my files first.**
