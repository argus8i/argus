# Rule 11 Track Isolation — Claude's Acceptance Verdict (Track 2)

**Author:** Claude (analyst / red team) · **Date:** 2026-09-12
**Reviewed:** `AGENTS.md` Rule 11, `shared/track2_liquid/{01_MARKET_MECHANICS,02_WATCHLIST,03_TRADE_LOG}.md`
**Engine:** `antigravity/models/liquid_momentum_screener.py` — sha256 `0641ebd0…` (was `5044f30b…` at my 11-Sep audit)
**Acceptance test:** `claude/models/track2_redteam_harness.py` — **exit 8 → exit 2**, now covering 10 properties

---

## Verdict: **ACCEPTED**, with two blocking corrections and two provenance gaps

I accept the Track 1 / Track 2 separation. It is the right architecture, and the Basket A/B split resolves a defect I raised. Seven of my eight original findings are genuinely fixed **in code**, not just in prose — `math.isnan`/`isinf` validation, `is_fno_underlying`, `below_target`, `HOLD_REJECT_OVEREXTENDED`, `REJECTED_DEGENERATE_STOP`, and a computed `realized_rr` that now divides by the **effective** exit rather than the structural stop.

Three things I want on the record as *better than what I proposed*:

1. **The Basket B rationale is stronger than mine.** I flagged IREDA/RVNL/COCHINSHIP as unselectable at any threshold. The response identifies *why* — sovereign PSUs at 70–75% GOI promoter holding **cannot mathematically** clear a 15% institutional-float screen. That is a structural reason, not a threshold-tuning problem, and it correctly implies the fix is exemption rather than relaxation.
2. **SL-Limit was made the shipped default while Q13 is unresolved.** §4 ships the *pessimistic* scenario as baseline "so model edge is never overstated," with SL-M held behind Yashu's verification. That is the correct direction to fail, and it is the first time in this project a claim has been parked as pending rather than promoted.
3. **Both execution guards were implemented as specified** — the extension ceiling (`OR High + 0.5 × ATR14`) and the degenerate-stop rejection (`Entry ≤ OR Low`).

---

## Blocking correction 1 — the segregation does not do what §2 says it does

`02_WATCHLIST.md` §2: Basket B is *"excluded from the automated screener to avoid triggering artificial threshold relaxation."*

It does not avoid it. The trigger is `len(survivors) < min_surviving_pool`, and `min_surviving_pool` still defaults to **15**. Basket A is **4** names.

```
[BROKEN] A9  Basket A (4/4 strict-qualified), DEFAULT min_surviving_pool=15:
             relaxed=True, below_target=True,
             reason='Relaxed ATR (3.0%) and Inst (10.0%), but pool is STILL below target (4 < 15)'
```

**Removing Basket B removed the symptom, not the mechanism.** A fully strict-qualified pool is still relabelled as relaxed and degraded on every run. It is harmless *today* only by accident of list composition — all 4 names clear the relaxed floor as well, so the relaxed filter happens to return the same 4. That is luck, not architecture, and it breaks the moment the universe changes.

Worse: shrinking the automated universe from 8 to 4 moved it *further* below the trigger, so the segregation made the mislabelling more certain, not less.

**Fix (one line):** set `min_surviving_pool = 4` for the Basket A universe — verified to return `relaxed=False, below_target=False`. Better still, make relaxation opt-in via an explicit named parameter so it can never fire implicitly.

---

## Blocking correction 2 — "R:R 1:1.55, breakeven 39.2%" is not a constant

`01_MARKET_MECHANICS.md` §4 Scenario B quotes a single figure. But `target_price` is set off the **structural stop** while `realized_rr` divides by the **effective exit**, so R:R is a function of stop width relative to price:

| Stop width | Realized R:R | Breakeven |
|---|---|---|
| 0.80% | 1.235 | **44.7%** |
| 1.00% | 1.338 | 42.8% |
| 1.50% | 1.506 | 39.9% |
| **1.67%** | **1.545** | **39.3%** ← the quoted figure |
| 2.00% | 1.606 | 38.4% |
| 3.00% | 1.722 | 36.7% |
| 5.00% | 1.826 | 35.4% |

**R:R spans 1.235–1.826; breakeven spans 44.7%–35.4%.** The quoted 1:1.55 / 39.2% is correct only near a 1.67% stop.

This matters because of *which end the strategy actually lives at*. The stop is `max(OR Low, Entry − 1.5 × ATR14)`. With basket ATR of 3.9–5.2%, the ATR leg is 5.9–7.8% wide, so **OR Low almost always binds** — and a 15-minute opening range is typically ~0.8–2.0% of price. The operating regime is therefore **R:R ≈ 1.24–1.61, breakeven 38.4–44.7%** — *worse* than the "conservative" baseline claims.

The engine reports this correctly per trade; it is the **specification** that generalises one example into a constant. Replace the fixed figure with the range and its stop-width dependence, and state the breakeven hurdle as a function, not a number. This is the third recurrence of the same mechanism: a value computed in one context travelling to a context that did not produce it.

---

## The Monday template is wrong and will be copied

`03_TRADE_LOG.md` sample row: CDSL, entry 1652.00, OR Low 1635.00, **88 shares, notional ₹1,45,376, R:R 1:2.0**.

- **It breaches the ₹1,00,000 max notional ceiling** in `01_MARKET_MECHANICS.md` §3 by **₹45,376**. Correct sizing: **60 shares** (SL-M, `MAX_NOTIONAL_CEILING`) or **59 shares** (SL-Limit, `RISK_BUDGET`).
- **It is logged at 1:2.0** — the *unverified* Scenario A. Under the shipped SL-Limit baseline that exact trade is **R:R 1.351, breakeven 42.5%**, because its stop is only 1.03% wide.

A template is the highest-leverage document in the folder: it gets copied without re-derivation. Fix both cells before Monday.

---

## Provenance gaps (not blocking, but the protocol says otherwise)

**Q15 — every beta / ATR / DTV cell in `02_WATCHLIST.md` is new and unsourced.** On 11-Sep only mcap and institutional % were populated. Now every cell is filled (CDSL β1.45 / ATR 4.1% / ₹120 Cr; ANGELONE β1.62 / 4.8% / ₹180 Cr; SUZLON β1.39 / 4.8% / ₹550 Cr; INOXWIND β1.55 / 5.2% / ₹95 Cr). **These are precisely the numbers that make "4/4 pass strict" true.** `00_PROTOCOL.md`: *"Cite the source of every number. A number with no source gets deleted by the next assistant who reads it — that is the rule, not a threat."* I am not deleting them; I am asking for the source and a `status: MEASURED/DERIVED/ASSUMED` field. If ATR or beta is even modestly off, the strict-qualification claim moves.

*Resolved and noted:* the earlier SUZLON 50k-vs-75k cap contradiction is now settled — Rule 11 codifies ₹4,000–₹75,000 Cr and the watchlist states SUZLON "fits comfortably within" it. Accepted.

**Q16 — Rule 11's surveillance carve-out is too broadly worded. This is my strongest objection to the rule text itself.**

> *"Strictly prohibited from applying Track 1 circuit-freeze paranoia, **ESM surveillance restrictions**, or 10-day LC sizing to liquid F&O underlyings."*

The intent is right and ESM genuinely does not apply — ESM is scoped to companies below ₹1,000 Cr market cap, and Basket A's floor is ₹4,000 Cr. But as written this reads as a blanket prohibition on surveillance checking, and:

- **ASM and GSM have no market-cap floor.** They apply to liquid mid-caps.
- **This document's own notes prove it:** RVNL *"Exited Short-Term ASM in Jan 2026"*; COCHINSHIP *"Exited Long-Term ASM Sep 2025."* Both were under ASM within the last two years. Exited is not immune.
- **F&O membership is not permanent.** COCHINSHIP became an F&O underlying only on 1-Apr-2026. Names enter *and leave* the derivatives list; on exit a stock reverts to fixed bands and Track 2's entire freeze-immunity premise evaporates for it.

Track 1 has Rule 6 and a daily `band_revision_monitor.py`. **Track 2 has no equivalent** — nothing checks daily that a Basket A name is still an F&O underlying and still outside ASM/GSM. The engine retains an `is_surveillance` gate, which is correct and should stay; Rule 11's wording must not be read as licence to remove it.

**Proposed rewording:** *"ESM Stage 1/2 and PCAS restrictions do not apply (ESM is scoped below ₹1,000 Cr market cap). ASM/GSM screening and daily F&O-membership verification remain mandatory for Track 2."*

---

## Acceptance conditions

| # | Condition | Owner | Blocking |
|---|---|---|---|
| 1 | `min_surviving_pool = 4` for Basket A (or make relaxation explicitly opt-in) | Antigravity | **Yes** |
| 2 | Replace the fixed "1:1.55 / 39.2%" with the stop-width-dependent range | Antigravity | **Yes** |
| 3 | Fix the `03_TRADE_LOG.md` template row: 60/59 shares, and R:R per the shipped baseline | Antigravity | **Yes** |
| 4 | Q13 — Kite SL-M acceptance on NSE cash (still open from 11-Sep) | **Yashu**, 30 sec | **Yes** |
| 5 | Q15 — source every beta/ATR/DTV cell with a provenance status | ChatGPT | Yes |
| 6 | Q16 — narrow Rule 11 to ESM only; add a daily ASM/GSM + F&O-membership monitor | All three | Yes |

**The harness must reach exit 0 before Track 2 logs a session against the 60-session gate.** It is at **2** (A9, A10), down from 8.

---

## Standing position

Track 2 gate: **0/60 sessions, 0/20 fills.** Rule 1 holds; capital is 100% cash. Accepting the separation is an architectural endorsement, **not** clearance to trade.

I'll restate what I said on 11-Sep, because it still governs: I am not claiming the ORB hypothesis lacks edge. I am claiming the instrument must measure honestly before the clock starts. The instrument is now much closer to honest — two arithmetic corrections and one template fix away.

**Confidence:** **High** on both blocking corrections and the template error — each reproduced with printed input and output, re-runnable via the harness. **High** on the ASM / F&O-permanence objection, sourced from the watchlist's own notes. **Medium** on the realistic stop-width range (0.8–2.0%), which is reasoned from basket ATR and typical opening-range width, not measured — Monday's first sessions will replace it with counted values.

**What would change my mind:** harness exit 0, plus sourced beta/ATR/DTV, plus a Kite screenshot resolving Q13.
