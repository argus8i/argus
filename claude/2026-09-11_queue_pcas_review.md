# Queue Ratio & PCAS Engine — Red-Team Review

**Author:** Claude · **Date:** 2026-09-11

---

## Headline

**The engineering is right. The ρ model has one empirical anchor, not two — and I share the blame for that.**

Credit first, properly: the excluded-window baseline, the tick/band-fraction ceiling gate, the fail-closed delivery check, `STALE_TAB_BACKGROUNDED`, and the Bernoulli-per-auction switch are all correct fixes, shipped in a day. The falsification rule being adopted formally is worth more than any of them.

Now the audit.

---

## 1. The ρ calibration is n=1, not n=2

```
25-Aug 10:28   R = 4,646,100    <- OBSERVED (screenshot)
27-Aug         R = 4,646,100    <- SAME NUMBER. Not observed.
                                   Carried forward two sessions.
```

25-Aug and 26-Aug both traded. The resting queue on 27-Aug was never measured and cannot have been identical to the 25-Aug 10:28 snapshot.

So what the brief calls two anchors is:

| | |
|---|---|
| Anchor 1 | `R_obs / V_25aug = 1.04` → no fill. **Real.** |
| Anchor 2 | `R_obs / V_27aug = 0.30` → fill. **Same R, different V.** |

**One measurement of R divided by two different V's.** The 27-Aug "MATCH" reduces to: a larger denominator produces a smaller ratio. That is arithmetic, not validation — the same shape as the "4 basis point convergence" (0.95³) two briefs ago, in a new form: *one datum reused as two*.

**This one is partly mine.** I published that line as *"ρ if queue unchanged"*. The caveat was in the label and nowhere else, and it has since been stripped and the number hard-coded as an empirical anchor. I should have marked it INFERRED in the output, not just in prose.

**Action:** mark ρ as `n=1, UNCALIBRATED`. One anchor tells you the model has the right *sign*. It tells you nothing about the *shape*.

---

## 2. The volume figures were restated without annotation

| Name | 10-Sep brief | Today | Δ |
|---|---|---|---|
| CROPSTER | 1,225,000 | 1,483,000 | **+21.1%** |
| CCDL | 545,000 | 563,000 | +3.3% |

Same session, two different figures, no note. Almost certainly the earlier ones were intraday snapshots — which **confirms the timestamp defect flagged yesterday, live.** Good that it surfaced.

But an unexplained restatement in a data log is indistinguishable from an error, and in three weeks nobody will be able to tell which it was. **Annotate the revision** with both timestamps.

---

## 3. The PCAS divisor is unverified and load-bearing

Every PCAS number divides by **six** auction sessions. Sensitivity:

| Sessions/day | V_auction | Allowed @15% | Days to clear 4,500 | Decay @2%/day |
|---|---|---|---|---|
| 3 | 1,044.7 | 156.7 | 9.6 | **−17.6%** |
| 6 | 522.3 | **78.4** | 9.6 | **−17.6%** |
| 12 | 261.2 | 39.2 | 9.6 | **−17.6%** |

Note what this actually shows: **days-to-clear and the decay are invariant to the divisor**, because a 15% participation cap applied per auction and then summed over more auctions cancels exactly. So the −17.6% headline is robust — good — but it also means **the auction-count refactor changed nothing quantitatively.** The unit change was conceptually right and numerically inert under a participation cap. Worth knowing before more is built on it.

Where the divisor *does* bite is the Bernoulli engine: 78 shares/auction vs 39 changes the lumpiness and the per-auction fill probability entirely.

And the source: *"SEBI CIR/MRD/DP/6/2013 & 38/2013"* is one of the citations attributed to me that **I never produced** (flagged 11-Sep, still standing). A shipped model now depends on it. **ChatGPT verifies the actual BSE PCAS session count and window structure, or the engine is marked provisional.**

---

## 4. Q1 — linear vs exponential: unanswerable on n=1

Both forms are fitted to one real point. Demonstration — every curve below is consistent with `ρ=1.04 → 0`:

| ρ | linear | exp λ=2 | exp λ=4 | exp λ=8 | step |
|---|---|---|---|---|---|
| 0.30 | 100% | 55% | 30% | 9% | 100% |
| 0.50 | 71% | 37% | 14% | 2% | 0% |
| 0.70 | 43% | 25% | 6% | 0% | 0% |
| 0.90 | 14% | 17% | 3% | 0% | 0% |

At ρ=0.5 the candidates disagree by **69 percentage points** and the data cannot separate them. You need roughly **20+ fills spread across 0.3 < ρ < 1.0** before the shape is identifiable. Choosing a functional form now is fitting noise and then trusting it.

**Recommendation: keep the step function with explicit uncertainty bands.** `ρ≤0.3 → expect fill · 0.3<ρ<1.0 → UNKNOWN · ρ≥1.0 → expect none.` "UNKNOWN" is a legitimate model output and it is the honest one. Log every fill with its ρ; revisit at n=20.

**Separate defect:** ρ discards **Q**, your order size. Your own formula `V_cum ≥ R + Q` keeps it. `P(fill) = f(ρ)` is fine when Q ≪ V and wrong at Vishuu's sizes in thin names — CHANDRIMA is Q=4,500 against V=3,134. Make it `f(ρ, Q/V)`.

---

## 5. Q2 — the PCAS random close is unmodellable *by design*, and that is the answer

The randomised close in the 44th–45th minute exists **specifically to defeat end-of-window order timing.** Modelling it means trying to out-predict a randomiser built to stop exactly that. Don't.

**Correct response: don't model placement timing at all. Place early in the entry window and leave the order.**

Your fill randomness does not come from *when* you place inside the window — it comes from whether that batch has contra-side depth at the equilibrium price. So estimate the Bernoulli parameter from **trade count**, not from time:

```
CHANDRIMA   V_day 3,134 · trades/day 67 · auctions 6
  trades per auction              11.2
  shares per auction                522
  avg shares per trade               47
  counterparty trades needed
    to clear 4,500 shares            96
  ... against 11.2 available per auction
```

And the equilibrium rule works **for** you: a sell limit at the LC fills *at the equilibrium* whenever equilibrium ≥ your limit. Place at the LC and take the clearing price. There is no cleverer placement available, which is the useful finding — it closes the question rather than opening a modelling project.

---

## 6. Q3 — T-1 delivery % is the correct design, not a compromise

**Yes, and it is better than same-day would be:**

1. Delivery % is a **regime** indicator measured over weeks, not a timing signal. A one-session lag is immaterial to a multi-week accumulation read.
2. **T-1 is what you would actually have at decision time.** Same-day delivery in a backtest is look-ahead bias. Using T-1 eliminates it for free — you are not conceding to the data lag, you are being forced into correct practice by it.
3. Rule 7 entries sit in a base. Nothing is being raced.

**The real risk is not the lag — it is the contamination bug you just fixed for volume, recurring.** If the 30-day delivery baseline includes the pump days, "expanding delivery" becomes *harder* to trigger exactly when it matters. Apply the identical excluded-window construction.

**Caveat:** delivery % is expensive to fake, not impossible. Genuine inter-account transfers between operator accounts settle as delivery. Best available filter; not proof of authenticity.

---

## 7. 65/65 green is not validation

`queue_model.py` passes 3/3. It is calibrated on n=1 with a reused R. **Those tests would pass identically if ρ were meaningless** — they verify the code does what the author intended, not that the intent is right.

This project's failure mode, four briefs running, has been confidence outrunning evidence. A green suite is a new and very convincing way to manufacture it.

**Concrete suggestion:** add a test that **fails** while `n_observations < 20` for any parameter tagged CALIBRATED. Make the suite carry evidence status, not just arithmetic. Right now nothing in the repo distinguishes a parameter measured 50 times from one measured once.

---

## Priority actions

1. Re-tag ρ as `n=1, UNCALIBRATED`. Step function with an explicit UNKNOWN band. *(§1, §4)*
2. Log `(ρ, Q/V, fill_fraction, timestamp)` on every paper exit. This is the only path to n=20.
3. ChatGPT verifies the BSE PCAS session count/structure — a shipped engine depends on an unverified citation. *(§3)*
4. Annotate the CROPSTER/CCDL volume restatements with timestamps. *(§2)*
5. Extend ρ to `f(ρ, Q/V)`. *(§4)*
6. Apply the excluded-window construction to the delivery baseline too. *(§6)*
7. Add the evidence-status test gate. *(§7)*

**Consensus: 100% cash, unchanged.** Zero qualifying trades remains correct.

One closing note. Yesterday's fixes were fast and right, and that is the thing to protect. The risk now is the opposite of the old one: the repo is becoming sophisticated faster than it is becoming *evidenced*. Seven models, sixty-five tests, two real observations. **The binding constraint is still sixty sessions of logging what actually happens** — and no amount of model quality substitutes for it.
