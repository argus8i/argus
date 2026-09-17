# CROPSTER — Post-mortem

**Author:** Claude · **Date:** 2026-09-09 · **Status:** final
**Source:** `data screenshots/Screenshot_20260824_224509.jpg`, `_20260824_225243`, `_20260825_102256`, `_20260825_102830`, `_20260909_135024`, `_20260909_135034`

---

## The trade

| | |
|---|---|
| Position | 12,560 shares @ avg **₹4.77** (CNC, BSE) |
| Capital | ~**₹59,900** |
| P&L shown 24 Aug 22:52 | **−₹2,763.20** (LTP 4.55) |
| Price 25 Aug 10:22 | **₹4.33**, −4.84% — locked at lower circuit |
| Price 09 Sep 13:50 | **₹3.34** |
| Loss if held to today | ≈ **−₹17,960 (−30%)** |

Confirmed exited. Good. But *how* the exit was possible matters more than that it happened, and that is the whole lesson.

---

## The screenshot that explains everything

`Screenshot_20260825_102830.jpg` — 25 Aug, 10:28 AM. Market depth for CROPSTER:

```
   Bid   Orders        Qty  |  Offer  Orders          Qty
  0.00        0          0  |   4.33     687   28,46,239
  0.00        0          0  |   4.34      13        2,240
  0.00        0          0  |   4.35      12        2,002
  0.00        0          0  |   4.36      12        6,675
  0.00        0          0  |   4.37      12        2,182
 Total                   0  |  Total           46,46,100
```

Read the left column again. **Zero bid. Zero orders. Zero quantity. At every price level.**

There was no buyer for this stock at any price. Not a low price — *any* price. On the right, 46,46,100 shares queued to sell, 687 separate orders stacked at 4.33 alone.

Holding 12,560 shares in that book:

- A stop-loss at −5% would not have executed. There was nothing to execute against.
- A market sell order would not have executed. A market order needs a bid; there wasn't one.
- Position in queue: somewhere among 687 orders and 2.8 million shares at the first price level.
- To be filled you needed a buyer for ~2.85 million shares to appear *ahead of you*.

**The stop-loss did not fail. It did not exist.** This is the single fact that invalidates every "I'll just exit at −5%" plan in a circuit stock.

---

## Why the fill at ₹4.77 happened at all

This is the part that stings, and it is the transferable lesson.

Trace the daily chart (`_20260909_135034`):

| Phase | Dates | What happened |
|---|---|---|
| Base | Jun | 5.90 drifting to 5.20, low volume |
| **Pump 1** | ~10–20 Jul | 5.20 → **7.60**, largest volume bars on the chart |
| **Lock-down** | late Jul → Aug | Descending staircase of *tiny marks* — days where the stock opened at lower circuit and never traded a range. Near-zero volume. 7.60 → ~5.00 |
| Pump 2 (failed) | mid-Aug | 5.20 → 6.50, rejected |
| Break | 22–25 Aug | → 4.33, locked down, **zero bid** |
| Bleed | Sep | → 3.19, small bounce to 3.34 |

Entry at 4.77 sits on the **descending staircase after Pump 2 failed**. Not in the accumulation. Not in the markup. In the distribution.

Now the question that matters: on a stock where the bid side routinely goes to zero, **why did a buy order at 4.77 get filled?**

Because somebody sold. That is the only way a buy gets filled.

On the days CROPSTER was locked *up*, nobody would sell — offer side empty, no fill available at any size. The one day a fill was available was the day supply arrived. Supply arriving is the definition of distribution.

> **The fill was not the strategy working. The fill was the warning.**

In an illiquid circuit stock, getting filled is information — and the information is bad. A fill means someone with a better view of the stock than you decided that your price was a good price to leave at.

---

## The cost of the tick size

CROPSTER at ₹3.34. Tick size ₹0.01. One tick = **0.30%**.
CCDL at ₹1.32. One tick = **0.76%**.

At sub-₹5 prices, the minimum price increment is a meaningful fraction of the daily band. Crossing the spread once costs 0.3–0.8%. Round trip: 0.6–1.5%. Against a 5% band, friction alone eats 12–30% of a perfect day's move — before brokerage, STT, GST and stamp duty.

**Every stock in the watchlist that lost money is priced under ₹5.** CROPSTER 3.34, CCDL 1.32, GATECH 0.82. The one that ran (CHANDRIMA) is the only one above ₹10.

That correlation is not a coincidence, and it is the cheapest filter available: **a price floor.**

---

## What to carry forward

1. In a circuit stock, a stop-loss is a wish, not an order. Size the position as if the exit does not exist, because for several days it will not.
2. Getting filled in an illiquid name is adverse information, not confirmation.
3. Under ₹10, the tick tax is structural and cannot be traded around.
4. The lock-down staircase — a run of tiny daily marks on near-zero volume — is the visual signature of holders being unable to sell. Learn to see it on a chart in one second.
