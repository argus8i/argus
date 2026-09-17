# Swing Trades — working folder

Shared workspace for three assistants — **Antigravity**, **Claude**, **ChatGPT** — working on the same problem: the small-cap circuit-lock pattern on BSE/NSE.

**Started:** 2026-09-09 · **Owner:** Yashu

---

## Layout

```
swing trades/
├─ README.md                    ← you are here
├─ shared/                      ← single source of truth. Anyone may edit.
│  ├─ 00_PROTOCOL.md            ← how the three assistants coordinate. READ FIRST.
│  ├─ 01_MARKET_MECHANICS.md    ← circuits, order books, GSM/ASM/ESM. Ground truth.
│  ├─ 02_WATCHLIST.md           ← every tracked name and its current state
│  ├─ 03_TRADE_LOG.md           ← every trade, with a pre-trade checklist
│  └─ 04_OPEN_QUESTIONS.md      ← unclaimed work + live disagreements
├─ claude/                      ← analyst / red team
│  ├─ PROGRESS.md
│  ├─ analysis/                 ← CROPSTER post-mortem, CHANDRIMA anatomy, CCDL live
│  └─ models/                   ← rulebook.md, screener_spec.md, fill_model.py
├─ antigravity/PROGRESS.md      ← builder. Queue: Q3, Q6, Q7, Q9
├─ chatgpt/PROGRESS.md          ← researcher. Queue: Q1, Q4, Q5, Q8
└─ data screenshots/            ← 20 screenshots, 24 Aug – 9 Sep 2026. The raw evidence.
```

Each assistant owns its own folder and never edits another's. Cross-assistant communication goes through `shared/`.

---

## Where the analysis landed

The original idea was: find stocks hitting the 5% upper circuit daily, ride four of them, take ~20% a month.

Reading the 20 screenshots, that plan fails on **liquidity**, not on stock-picking:

- **You cannot buy at a locked upper circuit.** Fill probability is roughly `offer_qty ÷ bid_qty`, measured at **0%–0.05%** across three observations here.
- **The fills you do get are the bad ones.** A fill requires a seller; in a locked-up stock the only sellers are people leaving. Fill probability and forward return are negatively correlated *by construction*.
- **You cannot sell at a locked lower circuit.** CROPSTER on 25 Aug: **zero bid, zero orders, zero quantity at every price level.** No stop-loss can execute against an empty book.
- **CHANDRIMA is the proof.** The call was right — it went on to rise 39% in a week. The trade made **−₹45**, because the only day a fill was available was the one flat day.

The constructive half is **Rulebook Rule 6**: trade the *accumulation and early markup* phase instead. CHANDRIMA's liquid window was 6.50 → 8.00, +23% over four weeks, with a working stop-loss throughout. The +83% vertical that followed was unreachable in both directions.

> The money in this pattern is in the boring part. The exciting part is where the liquidity dies.

**Start with:** `claude/models/rulebook.md`

---

## The two numbers that matter most

**₹10** — the price floor. Every losing name here is under ₹5; the only one that ran cleanly is the only one over ₹10. At ₹1.32 a single tick is 0.76% and the whole 5% band is twelve ticks wide.

**25%** — five consecutive lower circuits. Size every position so that losing 25% of it outright is acceptable, because for several days the exit will not exist.

---

## Ground rules

- No groups, no tips, no coordination, no forwarding. Independent observation only — see `01_MARKET_MECHANICS.md` §7.
- No assistant ever asks for an API key, token or password, and no assistant places an order.
- Every number cites its source. Unsourced numbers get deleted.
- Disagreement between assistants is the deliverable, not a problem. An unchallenged trade idea does not get traded.
