# CHANDRIMA — Anatomy of a complete cycle

**Author:** Claude · **Date:** 2026-09-09 · **Status:** final
**Source:** `Screenshot_20260826_153400`, `_20260827_090054`, `_20260827_090307`, `_20260827_090430`, `_20260827_091131`, `_20260827_132336`, `_20260827_132512`, `_20260828_090622`, `_20260828_091739`, `_20260909_135053`

---

## Why this one is the most valuable file in the folder

CHANDRIMA went **6.00 → 17.00+ in about three weeks. Roughly +180%.**

The trade in it made **−₹45.**

Being right about the stock produced nothing. That gap — between a correct call and a zero result — is the entire problem with the strategy, and CHANDRIMA documents it end to end.

---

## The timeline, reconstructed from the screenshots

| Date | Price | Band | Order book | Note |
|---|---|---|---|---|
| Jun–mid Jul | 6.00–6.50 | 20% | normal two-way | **Accumulation.** Boring. Low volume. Fully liquid. |
| mid Jul–20 Aug | 6.50 → 8.00 | 20% | normal two-way | **Markup.** Rising volume, real candle ranges, wicks. *Still tradeable both ways.* |
| ~21 Aug | wick to 9.00 | 20% | — | First test. Rejected. |
| 26 Aug 15:34 | **11.13** +19.93% | 20% | bid 16,97,766 / **offer 0** | Locked UC. Volume 1.53 crore. |
| 27 Aug 09:00 (pre-open) | 12.23 | **10%** | bid 40,00,000 in **4 orders** / offer 5,18,144 | Band halved. See below. |
| 27 Aug 09:11 | 12.23 | 10% | bid 52,07,252 (55 orders) / offer 7,15,676 | Offer side appears → fills available |
| 27 Aug 13:23–13:25 | 12.23→12.22 | 10% | — | **Bought 4,500 @ 12.23. Sold. −₹45.** |
| 28 Aug 09:17 | **13.46** +9.96% | 10% | bid 1,45,89,474 / **offer 0** | Locked UC. LC 11.02, UC 13.46. |
| → early Sep | → **17.00+** | 10% | — | The run continues without you |
| 09 Sep 13:50 | 15.53 −1.96% | 10% | **buy 15.53 / sell 16.15** | Distribution. 4% quoted spread. |

---

## Three things in this data that are worth more than the price move

### 1. Four orders for forty lakh shares

27 Aug pre-open, 09:00:

```
   Bid   Orders          Qty
 12.23        4   40,00,000
 12.22        1    9,99,999
 12.21        1    8,56,362
 12.20        1    6,56,366
```

Four orders totalling exactly 40,00,000 shares. One order of 9,99,999. These are not retail quantities and they are not retail *numbers* — 9,99,999 is a number a person types deliberately, one share under ten lakh.

Watched across three minutes: 40,00,000 → 40,00,505 → 40,01,515, while the 12.22 level went 9,99,999 → 10,00,010 → **20,00,010**.

This is a bid wall being *maintained*, not formed by natural demand. Its function is to make the book look like a queue nobody can get through, which discourages selling and locks the circuit.

**Diagnostic value:** a bid queue that is enormous but made of very few orders is a manufactured queue. A bid queue made of hundreds of small orders is retail piling in. The first tells you an operator is present; the second tells you the crowd has already arrived, which is late.

### 2. The band halved from 20% to 10% mid-run

26 Aug: prev close 9.28, UC 11.13 → **20% band.**
27 Aug: prev close 11.13, UC 12.24 → **10% band.**
28 Aug: prev close 12.24, UC 13.46, LC 11.02 → **10% band.**

Nobody announced this in the app. The exchange narrowed the band because the stock triggered a surveillance threshold.

**A band revision is not a technical detail. It is the exchange telling you it has noticed.** What follows a band revision, in order: tighter band → possible ASM/GSM listing → 100% margin → trade-to-trade (compulsory delivery, no intraday) → at higher GSM stages, **trading permitted only once a week** with a 5% band.

If a position is caught by that last step, the exit is a weekly auction. Not a bad price — *no* price, for six days at a time.

**Rule that falls straight out of this: never hold through a band narrowing.** It is the earliest, clearest, and most ignored exit signal available, and it is free to observe.

### 3. The only fill available was on the flat day

Look at when a fill was possible:

- 26 Aug: offer side **0**. No fill possible.
- 27 Aug 09:11: offer 7,15,676 appears. **Fill possible.** Bought here.
- 28 Aug: offer side **0**. No fill possible.

The stock rose +10% on 26 Aug, ~0% on 27 Aug, +9.96% on 28 Aug. The one day it was possible to get in was the one day it did not move.

That is not bad luck. It is mechanical. Fills require sellers; sellers appear on days the lock is weak; days the lock is weak are days the stock does not run. **The fill probability and the forward return are negatively correlated by construction.**

The exit was correct, incidentally — flat out of a name that had gone vertical is a fine outcome. The problem is not the exit. The problem is that the entry was only ever available on a day worth nothing.

---

## And the spread today

09 Sep 13:50: **buy 15.53 / sell 16.15.** A **4.0%** quoted spread.

On a stock with a 10% daily band, a round trip costs 4%. Two-fifths of a perfect day. Anyone entering CHANDRIMA at this moment starts −4% and needs most of a limit-up day just to reach breakeven.

---

## The honest conclusion

The phase where CHANDRIMA was *actually tradeable* — where a position could be entered at a chosen price and exited at a chosen price — was **mid-July to 20 August, 6.50 → 8.00.** Normal candles. Real ranges. Two-sided book. Rising volume. No locks.

That's +23% over four weeks, with a working stop-loss the entire time.

The vertical part, 9.28 → 17.00, is +83% and is almost entirely **unreachable**: you cannot buy into it, and if you do get in, you cannot choose when to leave.

> The money in this pattern is in the boring part. The exciting part is where the liquidity dies.


---

## CORRECTION — 2026-09-09, Claude

**I got the 09 Sep reading wrong, and Antigravity's ESM Stage 2 finding is what caught it.**

Above I wrote that *"09 Sep quoted spread 15.53/16.15 = a 4.0% spread."* That is not a spread.

```
close 15.53, change -0.31   ->  prev close = 15.84
15.84 × 0.98 = 15.5232   <- LC on a 2% band.   Widget showed BUY  15.53
15.84 × 1.02 = 16.1568   <- UC on a 2% band.   Widget showed SELL 16.15
close ÷ LC   = 1.0004
```

Those two numbers are **the floor and the ceiling of a 2% band** — i.e. ESM Stage 2, matching Antigravity's BSE query independently. Note also that the widget showed BUY *below* SELL with a negative delta, inverted relative to CROPSTER (3.34/3.33) and CCDL (1.32/1.31) in the same screenshot set. That inversion is itself a sign the instrument is not trading continuously.

**What this changes:**

- CHANDRIMA did not "close down 1.96% and roll over." It closed **locked at its lower circuit**.
- Under ESM Stage 2 continuous trading has stopped; it trades in **periodic call auction windows only**, on a **2% band**.
- The section above describes the trap closing. It has closed. Anyone holding now faces auction-only exits inside a 2% band.

The rest of the file stands — the timeline, the 4-order bid wall, the 20%→10% band revision, and the −₹45 lesson are unaffected.
