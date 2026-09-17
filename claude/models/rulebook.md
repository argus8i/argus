# The Rulebook

**Author:** Claude · **Version:** 1.0 · **Date:** 2026-09-09
**Read `shared/01_MARKET_MECHANICS.md` first. Read `claude/analysis/*.md` for the evidence behind every rule here.**

---

## Rule 0 — the line that does not move

Watching a price pattern and trading it independently is ordinary trading. What is not ordinary, and what SEBI actually prosecutes, is *participating in the scheme*: acting on tips from the people running it, joining a Telegram/WhatsApp pump group and trading on its calls, being one of a coordinated set of accounts, or forwarding the message to bring in more buyers.

Section 12A of the SEBI Act and the PFUTP Regulations catch participants, not just organisers, and "I only made a little" is not a defence. Penalties run to disgorgement plus fines, and market bans.

**So: no groups, no tips, no coordination, no forwarding. Independent observation only.** Everything below assumes that.

---

## Rule 1 — the price floor: ₹10

**No position in any stock priced below ₹10. No exceptions.**

Why this one rule does more work than the rest combined:

| Stock | Price | 1 tick | Outcome |
|---|---|---|---|
| CCDL | 1.32 | **0.76%** | untradeable grid |
| GATECH | 0.82 | **1.22%** | untradeable grid |
| CROPSTER | 3.34 | **0.30%** | −30% |
| CHANDRIMA | 15.53 | 0.06% | +180% |

Every loss in the record so far is under ₹5. The only stock that ran cleanly is the only one over ₹10. Below ₹10 the tick is a tax you pay on every entry and every exit, and it scales inversely with price.

---

## Rule 2 — never buy a stock that is locked at upper circuit today

This is the rule that contradicts the original plan, so here is the arithmetic rather than an assertion.

Fill probability when the offer side is empty is not "low" — it is approximately `offer_qty / bid_qty`:

- CHANDRIMA 26 Aug: 0 / 16,97,766 = **0**
- CHANDRIMA 28 Aug: 0 / 1,45,89,474 = **0**
- CCDL 09 Sep 13:50: 4,10,174 / 9,07,92,858 = **0.05%**

You cannot buy a locked stock. What actually happens is your order sits in the queue for days and fills on the day supply arrives — and supply arriving is distribution. See `fill_model.py`:

```
  You attempt 100 entries.
  You get filled on          : 49
  Of those fills, winners    : 35
  Of those fills, losers     : 65

  Average WIN  when you win  : +6.77%
  Average LOSS when you lose : -19.95%
```

A 1:3 payoff ratio with a 35% hit rate. That is not a strategy with a sizing problem; it is a strategy with a sign problem.

**Buy only on a day the circuit OPENED** — the stock traded away from the limit price and closed strong. That day proves two-way liquidity exists, which is the thing you are actually buying.

---

## Rule 3 — size for a five-day trap, not for a stop-loss

**A stop-loss in a circuit stock does not exist.** CROPSTER, 25 Aug: bid side 0 orders, 0 quantity, every level. There was nothing for a stop to execute against.

So size the position by this question instead:

> *If I cannot sell a single share for five consecutive sessions and the stock locks down each day, is the resulting loss acceptable?*

Five lower circuits at 5% = **−22.6%**. On ₹1,00,000 that is **−₹22,600**.

**Sizing rule:** maximum position = (rupees you are willing to lose outright) ÷ 0.25.

Willing to lose ₹10,000 → maximum position ₹40,000.
Willing to lose ₹5,000 → maximum position ₹20,000.

Never more than **one** such position open at a time. They correlate — the same operators, the same surveillance actions, the same exit door.

---

## Rule 4 — exit triggers, in priority order

Exit on the **first** of these to occur. Not on a price target. Not on a feeling.

| # | Trigger | Action |
|---|---|---|
| 1 | **Circuit band narrows** (20%→10%, 10%→5%) | Sell everything, next open, any price. The exchange has noticed. Non-negotiable. |
| 2 | **Stock moves to T2T / BE series, or appears on GSM or ASM** | Sell into any bid immediately. Do not wait for a better price. GATECH-BE is already here. |
| 3 | **Fails to lock UC by 11:00 AM** on a day it has been locking | Sell that day. The operator has stopped bidding. |
| 4 | **Offer-side quantity appears and grows while the bid queue shrinks** | Distribution into the queue. Sell same session. |
| 5 | Target hit (see Rule 5) | Sell. |

Trigger 1 is the one that gets ignored because it is invisible in the app — it only shows up as a changed UC/LC number. **Check UC and LC every single morning.** CHANDRIMA's band halved on 27 Aug and nothing announced it.

---

## Rule 5 — take the money early and stop

The original instinct here was right and should be kept: *don't be greedy, hit the number, shut for the day.*

- **Target: +8% to +10% on the position, then out.** Not 20%. Not "four days of 5%".
- **Maximum hold: 3 sessions.** The model shows expected value degrading with every additional day held (−9.88% at 1 day → −10.85% at 6). Time in the position is not your friend; it is the operator's.
- **One trade at a time.** Close it before opening another.

---

## Rule 6 — the trade that is actually available

This is the constructive half, and it comes out of CHANDRIMA's own chart.

CHANDRIMA's tradeable phase was **mid-July to 20 August: 6.50 → 8.00, +23% over four weeks**, with normal candles, real high-low ranges, a two-sided book, and a working stop-loss the entire time.

The vertical phase, 9.28 → 17.00 (+83%), was unreachable — no entry, no chosen exit.

**Trade the accumulation and early markup. Skip the circuit phase entirely.**

Entry conditions, all required:

- [ ] Price **≥ ₹10**
- [ ] 15–40% above the 60-day base (not 100%+ — that phase is over)
- [ ] 20-day average volume rising **3×+** versus the prior 60-day average
- [ ] **Zero circuit locks in the last 5 sessions** (no day where high == low)
- [ ] Daily high−low range **> 3%** of price on at least 4 of the last 5 sessions
- [ ] Quoted bid-ask spread **< 1%** of price
- [ ] **Not** in T2T/BE, **not** on GSM, **not** on ASM
- [ ] Delivery percentage rising over the last 10 sessions

Any single box unticked = no trade.

In this phase a stop-loss works, because there is a bid to sell into. That is the entire difference, and it is worth more than the extra 80%.

---

## Rule 7 — write down every trade before placing it

Into `shared/03_TRADE_LOG.md`, **before** the order goes in:

```
Date | Stock | Price | Qty | Rupees | Why (one sentence)
Band today: UC ___ LC ___ (and what it was yesterday)
Bid qty / Offer qty at entry: ___ / ___
Which of the 8 entry boxes are unticked: ___
Max acceptable loss on this position: ₹___
Exit trigger I expect to hit first: ___
```

Filling this in takes ninety seconds and will stop more bad trades than any indicator. Most bad trades die at the line "which boxes are unticked".

---

## The single sentence

> **In an illiquid circuit stock, getting filled is bad news** — it means someone with a better view of the stock decided your price was a good price to leave at. The strategy is not "ride the manipulation"; the strategy is "be the last buyer", and the last buyer is the one who funds everyone else's exit.
