# Adversarial Red-Team Response — Rule 7 & Live Pipeline

**Author:** Claude · **Date:** 2026-09-11 · **Requested format followed.**

---

## 1. Executive Verdict

**The pipeline is good engineering. Rule 7 is directionally right. The system is not yet institutionally sound, and it fails on one thing it has never measured: whether the position can be exited at all.**

Credit first, because it is earned: **you reconciled CHANDRIMA to −₹45 and restated the record as 1 win / 2 losses, net −₹6,745.** Correcting a record *against* your own interest is the single best signal in this document, and it is rarer than anything else the project has produced. The deprecation of circuit-chasing on an explicit 78.9% breakeven is also right, and it is your own arithmetic, not mine.

What blocks "institutionally sound":

1. **There is no liquidity gate.** Every fill assumption in the system is a constant. Nothing anywhere asks whether the position is small relative to the tape. Your own 10-Sep telemetry contains the counterexample and nobody flagged it (§2.1).
2. **Rule 7's exit mechanism is the same event as Rule 7's primary risk.** You cannot sell into a resting upper-circuit queue unless the stock is locked at upper circuit, and that is the state Rule 4 orders you to flee (§2.2).
3. **Zero validated observations.** `QUEUE_MULT`, win rate and the +15–20% target are all n≤1. The framework is well-built scaffolding around an unmeasured core.

None of this is fatal. All three are measurable with the pipeline you just built.

---

## 2. Critical Loopholes

### 2.1 — The position/volume gate does not exist, and your own tape shows why it must

Your 10-Sep telemetry, unremarked in the brief:

> **CHANDRIMA: closed ₹15.22, volume 6,355 shares, 67 trades.**

That is **₹96,723 of turnover for the entire session.** Average trade: 95 shares, ₹1,444.

Against Vishuu's stated position band of ₹25,000–₹1,00,000:

| Position | Shares | % of the whole day's volume |
|---|---|---|
| ₹25,000 | 1,643 | **25.8%** |
| ₹55,035 | 3,616 | **56.9%** |
| ₹1,00,000 | 6,570 | **103.4%** |

His actual CHANDRIMA position, 4,500 shares, was **70.8% of the entire day's volume** — 47× the average trade size.

**At 70% participation you are not filling into the market. You are the market.** Your sell order is the price discovery.

And `QUEUE_MULT = 1.0` — "full position clears in one session" — is currently applied to this name identically to CROPSTER, where 12,560 shares was 0.08% of volume. The constant is defensible at 0.08% and absurd at 70.8%.

| Name | 10-Sep volume | Position | Participation | Verdict |
|---|---|---|---|---|
| CROPSTER | 15,735,454 | 12,560 | 0.08% | fine |
| CCDL | 29,339,415 | 30,000 | 0.10% | fine |
| GATECH | 268,977 | 30,000 | **11.15%** | marginal |
| CHANDRIMA | **6,355** | 4,500 | **70.81%** | **impossible** |

**This is the biggest hole in the system.** Fix in §3.1.

### 2.2 — Rule 7's profit engine and its primary risk are the same event

```
Rule 7 entry : two-sided liquid base, spread <1%, EQ, >= Rs.10
Rule 7 exit  : "sold pre-emptively into the resting UPPER CIRCUIT buyer
                queue on Day 3 or Day 4, sell fill probability ~= 100%"
```

A resting upper-circuit buyer queue only exists when the stock is **locked at upper circuit.** So Rule 7 requires the stock to travel from a liquid two-sided base to a UC lock in three sessions — which is CHANDRIMA's exact path:

| | | |
|---|---|---|
| 20-Aug | 8.00 | two-sided, liquid ← **Rule 7 entry zone** |
| 26-Aug | 11.13 | UC lock, 20% band ← **Rule 7 exit zone** |
| 27-Aug | 12.24 | **band cut to 10%** ← Rule 4: exit immediately |
| 09-Sep | 15.53 | ESM Stage 2, 2% band, PCAS |
| 10-Sep | 15.22 | LC lock. **6,355 shares all day.** |

Rule 4 (exit on band narrowing) and Rule 7 (exit into the UC queue) fire on overlapping states and give opposite instructions. **Which rule wins is undefined**, and the one time it mattered — CHANDRIMA — the answer was: the stock ran to 17, then ended as an instrument where a ₹29,000 position is a two-day exit.

The strategy is not wrong. It is **under-specified at exactly the moment it matters.**

### 2.3 — Short-delivery close-out in a circuit runner is the worst tail in the system

You asked what happens on an ICCL/NCL buy-in. Verified against NSE Clearing and Zerodha:

> Close-out = **the higher of (a) auction-day settlement price **+20%**, or (b) the highest price of the stock from the trade day through the auction date.**
> For Trade-for-Trade scrips the markup stays at 20% over the trade-day settlement, or the highest price across exchanges that day, whichever is higher.

Now apply it to a stock that has been limit-up daily: **"highest price from trade day to auction date" is the top of the run.** In a name that gained 5% a day for four sessions before you short-delivered, the close-out is the peak, and possibly peak + 20%.

The exposure chain is specific and live for you: you sell on T+1 in a T2T/XT name → your counterparty short-delivered to *you* → your shares never arrive → **you** short-deliver to your buyer → close-out at the run's high. This is exactly the Q10 gap. **Q10 is not an operational convenience question. It is a tail-risk question**, and it is currently open while the strategy assumes T+1 selling works.

Related: **ESM Stage 1 / T2T applied mid-trade does not force a sale, but it kills netting.** If you bought intending to square off and the name goes T2T overnight, you must take delivery and can only sell from demat. That converts a 1-day trade into a minimum 2-day trade — and Rule 7's stop is priced as a 1-day event.

### 2.4 — `QUEUE_MULT = 1.0` was adopted on n=1, in the optimistic direction

You flagged the n=1 yourself, which is good practice. The asymmetry is what matters: **you moved the parameter from conservative (2.0) to the most optimistic value available (1.0) on a single observation** — and that observation was CROPSTER Day 3, a 15.7-million-share session at 0.08% participation, i.e. the most favourable conditions in the entire dataset.

I rejected a single-observation parameter change on Q7 when it made my case *stronger*. The same standard applies here: **revert to a range, or better, replace the scalar entirely (§3.1).**

### 2.5 — Citations attributed to me that I did not produce

§3 states *"All primary sources from Claude's investigation"* and then lists **BSE Notice 20230718-46**, **NSE/SURV/57609**, **SEBI CIR/MRD/DP/6/2013 & 38/2013**, and a *"BSE Consolidated Master Circular Equity Segment Item 1.6."*

**I did not source any of those.** What I actually verified and cited was: the NSE GSM FAQ (15-Apr-2025), an exchange-surveillance summary, an ESM overview, and Zerodha's T2T page. I produced **no circular numbers at any point.**

I am not saying they are wrong — they may well be correct. I am saying they entered `shared/01_MARKET_MECHANICS.md` stamped "verified by Claude" without being verified by Claude, and a document whose whole value is that its numbers are sourced cannot launder attributions. **Either ChatGPT re-verifies each against the exchange's own circular archive and takes the byline, or they are marked UNVERIFIED.**

---

## 3. Stress Tests & Code Edits for Antigravity

### 3.1 — Replace the scalar `QUEUE_MULT` with a participation gate *(highest priority)*

Written and tested: `claude/models/liquidity_gate.py`.

```python
MAX_PARTICIPATION = 0.15   # realistic share of a session you can be

def daily_fill_fraction(position, daily_volume, mp=MAX_PARTICIPATION):
    return min(1.0, mp * daily_volume / position)

def max_position_for_exit_in(days, daily_volume, mp=MAX_PARTICIPATION):
    return int(days * mp * daily_volume)      # <-- THE SIZING RULE
```

Applied to the current book:

| Name | Fill/day | Sessions to clear | Gate (≤2 sessions) |
|---|---|---|---|
| CROPSTER | 100% | 1.0 | PASS |
| CCDL | 100% | 1.0 | PASS |
| GATECH | 100% | 1.0 | PASS *(11% participation — marginal, tighten `MAX_PARTICIPATION` and it fails)* |
| CHANDRIMA | **21.2%** | **4.7** | **FAIL** |

Max position exitable in two sessions:

| CROPSTER | CCDL | GATECH | CHANDRIMA |
|---|---|---|---|
| ₹1.50 Cr | ₹1.21 Cr | ₹62,941 | **₹29,009** |

**This becomes a hard pre-trade gate alongside the ₹10 floor.** `MAX_PARTICIPATION` is itself a guess (0.15) — it is now the top calibration target, but it is a guess that fails safe, unlike a constant that assumes full fills.

### 3.2 — Daily volume is not morning volume. This hole sits under every exit assumption

Every fill argument in the project cites **daily** volume. Every exit happens in the **morning.**

CROPSTER Day 3 traded 15,735,454 shares. **Nobody knows how many traded by 10:30.** If a limit-down session does most of its volume in the closing half-hour, a 09:00 queue sees a near-empty book for hours — which is precisely what Vishuu saw at **10:28 on 25-Aug: zero bids, on a day that later traded millions.**

That single observation reconciles the "zero-bid lockout" narrative with the "10.65M shares traded" finding. **Both are true.** The bid arrives late.

> **The highest-value thing your new pipeline can capture is the intraday volume curve on circuit days.** Bucket `shared/live_depth.json` into 15-minute bins and log cumulative volume by bin. Nothing else you could build comes close.

### 3.3 — Answering "where does the 09:00:01 pre-open exit fail?"

Four concrete failure modes:

1. **Pre-open is 09:00–09:08 entry, 09:08–09:12 matching.** Only the quantity that matches at the discovered equilibrium fills. A locked stock often discovers **no** equilibrium, and orders then carry into the continuous session — your "09:00:01 placement" bought you nothing.
2. **Your own sell can be the print that breaks the lock.** At meaningful participation you are supplying the queue, not joining it. Post-break the price leaves your limit and the remainder is stranded.
3. **Spoof-cancel partial fill — yes, exactly as you suspected.** You fill against the genuine portion of the bid wall, the operator pulls the rest, and you hold the remainder in a book with no bid. CCDL's bid wall was **240× the day's actual traded volume** (12+ crore resting vs. 2.93 crore traded). A wall that size is not the same population as the tape.
4. **T2T holdings not released at 09:00** — Q10, still open.

### 3.4 — "Is volume ≥3× and spread <1% enough to screen wash trading?" — **No.**

Both filters are *satisfied* by a competent wash operation:

- Wash trades **inflate** volume → passes the 3× filter.
- The operator quotes both sides → **tightens** the spread → passes the <1% filter.

They are necessary, not sufficient. Add the metrics that are expensive to fake — and your new bhavcopy ingestor already captures two of them:

| Metric | Why it resists faking | Current reading |
|---|---|---|
| Delivery % | shares must actually settle | not yet captured — **add it** |
| Trade count + avg trade size | wash prints cluster in size | CROPSTER 1,785 sh/trade; CCDL 9,332; CHANDRIMA **95** |
| Resting bid depth ÷ traded volume | manufactured walls are orders of magnitude off | **CCDL = 240×** |

That last ratio is new, cheap, and probably your best single operator-detector. Add it to the screener.

### 3.5 — Pipeline blindspots (Kite DOM via CDP)

1. **Silent staleness is the dangerous failure.** Chrome throttles timers and rendering in backgrounded or minimised windows. Depth stops updating while your daemon keeps writing `live_depth.json` with **stale values and fresh timestamps.** Fix: carry the exchange/feed timestamp separately from local write time, and emit `status: STALE` if LTP and cumulative volume are unchanged for N seconds during market hours. Never serve a last-known value as live.
2. **Depth cannot show your queue position.** It shows aggregate quantity and order count per level — never where *you* sit in the FIFO. **The pipeline therefore cannot directly measure `QUEUE_MULT`, the parameter it was built to calibrate.** Only your own order timestamps and fill timestamps can. Log both on every order.
3. **It is a rendered view of a throttled feed.** Kite Web's depth is already snapshotted, then DOM-rendered, then polled by you at 2s. You are three hops from the tape. Fine for regime detection, wrong for execution triggers.
4. **Five levels only**, and in a locked stock everything is at one price anyway — the queue *behind* the top level is invisible, which is the part that determines your fill.

---

## 4. Consensus Stance

**Endorsed, without reservation: maintain the 100% cash gate. 60 prospective paper sessions, 20 logged fills, before any live capital.**

Two conditions I would add before the gate is even considered satisfied:

1. **Every paper entry must pass the §3.1 liquidity gate.** A logged "fill" in a name where the position is 70% of daily volume is not an observation, it is fiction — and fiction in the log is worse than an empty log, because it calibrates the model.
2. **Q10 must close before any T2T/XT name is traded live.** §2.3 makes it a tail-risk question, not a convenience one.

One honest caution about this exercise. You asked us to attack the framework, and attacking it produces long, satisfying documents — but the project's three real failures so far were **a live position nobody connected to the pump SMS three sections above it, a −₹45 that became +₹2,750, and a headline conclusion the supporting data contradicted.** None were found by adversarial stress-testing. All three were found by **checking a number against its source.**

The infrastructure is now good enough that the binding constraint is no longer analysis. It is 60 sessions of patiently logging what actually happens. That is boring, and it is the whole job.
