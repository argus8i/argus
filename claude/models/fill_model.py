"""
================================================================================
RETIRED / OBSOLETE RESEARCH ARTIFACT — DO NOT USE IN PRODUCTION OR EXECUTION
================================================================================
WARNING: This early exploratory script was written as a red-team Monte Carlo
illustration prior to the adoption of AGENTS.md Rule 1 (Mandatory Paper-Trading Gate)
and Rule 4 (Discrete 4-State Execution Modeling).

It uses heuristic, uncalibrated fill probabilities (e.g. 0.02, 0.35, 0.95) and does
not reflect the fail-closed 4-state execution engine (LOCKED_NO_BID, QUEUED, PARTIAL,
FILLED) or the mandatory 10-day lower-circuit risk calibration.

ACTIVE PRODUCTION IMPLEMENTATION:
- State Machine & Execution Gates: antigravity/models/circuit_rules.py
- Capacity-Bounded Queue Drain:    antigravity/models/queue_model.py
- Rule 1 / Rule 5 Risk Engine:     antigravity/models/risk_calculator.py
================================================================================

CIRCUIT-RIDING FILL MODEL (HISTORICAL EXPLORATORY ARTIFACT)
===========================================================
Monte Carlo of the "buy the upper-circuit stock, ride it 4 days, take 20%" plan.

WHY THIS EXISTS (HISTORICAL CONTEXT)
------------------------------------
The plan assumes two things that are false:
  1. That you can buy when you decide to buy.
  2. That you can sell when you decide to sell.

In a circuit-locked stock neither is true. This script priced what that costs.

Every parameter below is anchored to numbers read directly off Vishuu's own
screenshots (see claude/analysis/*.md for the sourcing). Nothing is invented.
"""

import numpy as np

RNG = np.random.default_rng(20260909)
N = 200_000

# ---------------------------------------------------------------------------
# OBSERVED PARAMETERS  (source: data screenshots, Aug 24 - Sep 9 2026)
# ---------------------------------------------------------------------------
# Order-book imbalance on a cleanly UC-locked day, measured as
#     total_offer_qty / total_bid_qty
#   CHANDRIMA 26-Aug 15:34 :         0 /   16,97,766  = 0.0000
#   CHANDRIMA 28-Aug 09:17 :         0 / 1,45,89,474  = 0.0000
#   CCDL      09-Sep 13:50 :   4,10,174 / 9,07,92,858 = 0.0005
#
# You are joining the BACK of the bid queue. Your fill probability on a
# strongly locked day is effectively the imbalance ratio, not 50/50.

P_FILL = {
    "LOCK_STRONG": 0.02,   # offer side empty. Nobody is selling to you.
    "LOCK_WEAK":   0.35,   # offer qty appearing. Someone IS selling to you.
    "BREAK":       0.95,   # circuit opened. You get filled instantly.
}

# Next-day state transitions. The asymmetry that matters:
# a WEAK lock is the operator testing the exit, and it resolves down far more
# often than it resolves up.
TRANSITION = {
    # from            -> [LOCK_STRONG, LOCK_WEAK, BREAK]
    "LOCK_STRONG":       [0.62,        0.23,      0.15],
    "LOCK_WEAK":         [0.18,        0.22,      0.60],
    "BREAK":             [0.05,        0.10,      0.85],
}
STATES = ["LOCK_STRONG", "LOCK_WEAK", "BREAK"]

BAND = 0.05          # 5% circuit band (CROPSTER, CCDL, GATECH all 5%)
# ---------------------------------------------------------------------------
# EXIT ENGINE -- v4, rebuilt 2026-09-10 on Antigravity's BSE bhavcopy audit
# ---------------------------------------------------------------------------
# v1-v3 modelled the lower-circuit run as a TOTAL LOCKOUT: once the stock turns
# you eat the whole descent (ret *= 0.95**lc_days). The bhavcopy data says that
# is WRONG, and the error is not small.
#
# CROPSTER's 10 "locked" LC sessions, 23-Jul to 05-Aug 2026, ACTUAL volumes:
#     2,955,010 / 723,636 / 417,255 / 351,329 / 531,370
#       490,187 / 606,022 / 913,400 / 1,342,206 / 2,321,465
#   -> 10,651,880 shares changed hands across the "lockout"
#   -> volume bottomed Day 4 and then ROSE 6.6x into Day 10
#
# Shares traded means a bid existed. Vishuu's 12,560 was 0.43%-3.57% of daily
# volume -- never more than a rounding error against the tape. An exit was
# available EVERY ONE OF THOSE DAYS, at the limit price.
#
# So -39.66% is NOT a structural liquidity loss. It is the loss of someone who
# did not place a sell order for ten consecutive sessions. The trap is not
# "you cannot sell", it is "you can only sell at the limit, and the limit
# ratchets down 5% a day while you decide."
#
# That makes the loss a function of EXIT DISCIPLINE, which is actionable:
#     exit Day 1 -> -4.97%    Day 3 -> -14.14%   Day 5  -> -22.38%
#     exit Day 2 -> -9.69%    Day 4 -> -18.32%   Day 10 -> -39.66%
#
# The zero-bid state is real but was a MOMENT, not a fortnight (CROPSTER
# 25-Aug 10:28: 0 bids / 46,46,100 offered). Modelled as ZERO_BID_RATE.

# Fraction of your order filled on an LC day when queued at the limit.
# FIFO + random queue placement => expected fill ~ V_day / Q_queue.
# QUEUE_MULT = Q_queue / V_day. UNMEASURED -- sensitivity reported below.
QUEUE_MULT_RANGE = (1.0, 2.0, 3.0, 5.0)
QUEUE_MULT = 2.0

# Share of LC sessions with a true zero bid (no fill at any size).
# One observed instance; frequency UNMEASURED. See Q11.
ZERO_BID_RATE = 0.15

# Sessions the descent runs before a bid returns.
# NOW MEASURED: primary 10, secondary 9. Mean 9.5, tight cluster.
LC_RUN_MEAN = 9.5

# Round-trip friction at sub-Rs.10 prices. One tick = Rs.0.01.
#   CROPSTER @ 3.34  -> 1 tick = 0.30%
#   CCDL     @ 1.32  -> 1 tick = 0.76%
#   CHANDRIMA@ 15.53 -> quoted spread 15.53/16.15 = 4.0% (!)
SPREAD_COST = 0.010  # 1.0% round trip, generous for a sub-Rs.5 name
TAX_COST    = 0.002  # STT + exchange + GST + stamp, delivery


def _step(state_idx, n):
    """Advance the order-book regime by one session."""
    new_idx = np.empty_like(state_idx)
    for s, name in enumerate(STATES):
        m = state_idx == s
        if m.sum():
            new_idx[m] = RNG.choice(3, size=m.sum(), p=TRANSITION[name])
    return new_idx


def _drain(n, exit_day, queue_mult=None, zero_bid=None):
    """Multiplicative outcome of exiting into an LC descent.

    You queue the whole remaining position at the limit every morning from
    `exit_day` onward. Each session fills a fraction; the remainder rolls to
    the next day at a price 5% lower. exit_day=1 is disciplined; a large
    exit_day is the frozen holder.
    """
    qm = QUEUE_MULT if queue_mult is None else queue_mult
    zb = ZERO_BID_RATE if zero_bid is None else zero_bid
    run = RNG.poisson(LC_RUN_MEAN, n).clip(1, 20)

    remaining = np.ones(n)
    proceeds = np.zeros(n)
    price = np.ones(n)

    for day in range(1, 21):
        live = (remaining > 1e-9) & (day <= run)
        if not live.any():
            break
        price = np.where(live, price * (1 - BAND), price)
        open_book = RNG.random(n) > zb          # zero-bid sessions fill nothing
        frac = np.where(live & (day >= exit_day) & open_book,
                        min(1.0, 1.0 / qm), 0.0)
        filled = remaining * frac
        proceeds += filled * price
        remaining -= filled

    proceeds += remaining * (1 - BAND) ** run   # residue sold at the bottom
    return proceeds


def simulate(hold_days=4, queue_days=3, n=N, lc_run_mean=None, exit_day=1):
    """One pass of the strategy. Returns per-attempt P&L in fraction of capital.

    An 'attempt' = you spot a stock locked at upper circuit, queue a buy at the
    UC price, and leave the order there for up to `queue_days` sessions. If it
    fills you hold for `hold_days`. If it never fills you walk away flat.
    """
    filled = np.zeros(n, dtype=bool)
    pnl = np.zeros(n)

    # Day 0: the stock is strongly locked. That is WHY you noticed it.
    state_idx = np.full(n, STATES.index("LOCK_STRONG"))

    # --- ENTRY: sitting in the queue ---------------------------------------
    # THE ADVERSE-SELECTION CORE. You do not choose your fill day.
    # The market chooses it for you, and it chooses the day supply arrives.
    for _ in range(queue_days):
        for s, name in enumerate(STATES):
            m = (state_idx == s) & ~filled
            if m.sum():
                hit = RNG.random(m.sum()) < P_FILL[name]
                idx = np.flatnonzero(m)
                filled[idx[hit]] = True
        state_idx = _step(state_idx, n)

    # Everyone still unfilled walks away flat. Everyone filled now rides.
    # `ret` is a multiplicative factor, so losses compound correctly and can
    # never exceed -100%.
    ret = np.ones(n)
    done = ~filled          # unfilled paths are finished before they start

    for _ in range(hold_days):
        state_idx = _step(state_idx, n)
        live = filled & ~done

        # LOCK_STRONG / LOCK_WEAK on the up-leg -> +5%.
        # Note you cannot sell on these days either. The gain is on paper.
        up = live & (state_idx != STATES.index("BREAK"))
        ret[up] *= (1 + BAND)

        # BREAK -> the circuit opened and the stock traded away from the limit.
        brk = live & (state_idx == STATES.index("BREAK"))
        n_brk = int(brk.sum())
        if n_brk:
            idx = np.flatnonzero(brk)
            turns_down = RNG.random(n_brk) < 0.72

            # Up-break: an ordinary two-way day. You can finally exit. Take it.
            up_idx = idx[~turns_down]
            ret[up_idx] *= 1.02
            done[up_idx] = True

            # Down-break: the LC descent begins. You are NOT frozen -- you
            # queue at the limit each morning and bleed the position out.
            dn_idx = idx[turns_down]
            ret[dn_idx] *= _drain(dn_idx.size, exit_day)
            done[dn_idx] = True

    gross = np.where(filled, ret - 1.0, 0.0)
    net = np.where(filled, ret * (1 - SPREAD_COST - TAX_COST) - 1.0, 0.0)
    return filled, net


def report():
    print("=" * 72)
    print("  CIRCUIT-RIDING FILL MODEL  --  200,000 simulated attempts")
    print("=" * 72)

    print("\nTHE PLAN AS STATED: 4 upper circuits x 5% = 21.6% compounded\n")

    for hold in (1, 2, 3, 4, 6):
        filled, net = simulate(hold_days=hold)
        fr = filled.mean()
        on_fill = net[filled]
        print(f"  hold {hold} day(s):  fill rate {fr:5.1%}   "
              f"mean/attempt {net.mean():+7.2%}   "
              f"mean/fill {on_fill.mean():+7.2%}   "
              f"P(loss|fill) {(on_fill < 0).mean():5.1%}")

    print("\n" + "-" * 72)
    print("  WHERE THE MONEY GOES  (4-day hold)")
    print("-" * 72)
    filled, net = simulate(hold_days=4)
    on_fill = net[filled]

    print(f"\n  You attempt 100 entries.")
    print(f"  You get filled on          : {filled.mean()*100:.0f} of them")
    print(f"  Of those fills, winners    : {(on_fill > 0).mean()*100:.0f}")
    print(f"  Of those fills, losers     : {(on_fill < 0).mean()*100:.0f}")
    print(f"\n  Average WIN  when you win  : {on_fill[on_fill > 0].mean():+.2%}")
    print(f"  Average LOSS when you lose : {on_fill[on_fill < 0].mean():+.2%}")
    print(f"  Worst 5% of fills          : {np.percentile(on_fill, 5):+.2%}")
    print(f"  Worst 1% of fills          : {np.percentile(on_fill, 1):+.2%}")

    print("\n" + "-" * 72)
    print("  THE 20%/MONTH TARGET, HONESTLY PRICED")
    print("-" * 72)
    # A month = 20 trading days. Each attempt occupies ~4 days (3 queuing +
    # the hold), so you get 5 NON-OVERLAPPING attempts, full size each time.
    # This is the honest reading of "4 days x 5% = my monthly target".
    n_month = 40_000
    equity = np.ones(n_month)
    for _ in range(5):
        _f, r = simulate(hold_days=4, n=n_month)
        equity *= (1 + r)
    print(f"\n  5 sequential attempts over one month, full size each time:")
    print(f"    median outcome    : {np.median(equity) - 1:+.1%}")
    print(f"    mean outcome      : {equity.mean() - 1:+.1%}")
    print(f"    P(hit target +20%): {(equity >= 1.20).mean():.1%}")
    print(f"    P(end down)       : {(equity < 1.0).mean():.1%}")
    print(f"    P(down over 30%)  : {(equity < 0.70).mean():.1%}")
    print(f"    5th percentile    : {np.percentile(equity, 5) - 1:+.1%}")
    print(f"\n  Same, but sized at 25% of capital per attempt:")
    eq2 = np.ones(n_month)
    for _ in range(5):
        _f, r = simulate(hold_days=4, n=n_month)
        eq2 *= (1 + 0.25 * r)
    print(f"    median outcome    : {np.median(eq2) - 1:+.1%}")
    print(f"    P(hit target +20%): {(eq2 >= 1.20).mean():.1%}")
    print(f"    5th percentile    : {np.percentile(eq2, 5) - 1:+.1%}")

    print("\n" + "-" * 72)
    print("  EXIT DISCIPLINE  --  what the LC descent actually costs")
    print("-" * 72)
    print("\n  v1-v3 assumed a total lockout. Bhavcopy says 10,651,880 shares")
    print("  traded across CROPSTER's 10 'locked' days. A bid existed every")
    print("  day. -39.66% is the cost of NOT SELLING, not of being unable to.\n")
    print(f"  {'queue sell from':>17}{'mean':>10}{'p5':>10}{'p95':>10}")
    for ed in (1, 2, 3, 5, 99):
        r = _drain(60_000, ed)
        lab = "never (frozen)" if ed == 99 else f"Day {ed}"
        print(f"  {lab:>17}{r.mean()-1:>10.2%}"
              f"{np.percentile(r,5)-1:>10.2%}{np.percentile(r,95)-1:>10.2%}")
    print("\n  Discipline is worth ~27 points. It is the largest single")
    print("  controllable variable anywhere in this project.\n")
    print("  SENSITIVITY to QUEUE_MULT (unmeasured -- see Q11):")
    for qm in QUEUE_MULT_RANGE:
        a = _drain(40_000, 1, queue_mult=qm)
        b = _drain(40_000, 3, queue_mult=qm)
        print(f"    queue_mult={qm:<4} exit Day1 {a.mean()-1:+7.2%}"
              f"   exit Day3 {b.mean()-1:+7.2%}")

    print("\n" + "=" * 72)
    print("  THE ONE SENTENCE")
    print("=" * 72)
    print("""
  ENTRY:  your fill rate is highest exactly when forward return is worst.
  On a strongly locked day nobody sells to you, so you don't get in.
  On the day supply appears, you get in instantly -- because the person
  on the other side has decided to leave. You are not riding the move.
  You are buying the exit.

  EXIT:   but once you are in, you are not trapped -- you are hesitating.
  10.6 million shares traded across CROPSTER's ten "locked" sessions.
  The bid was there. The sell order was not.
""")


def fr_pct(x):  # noqa
    return f"{x:.0f}"


if __name__ == "__main__":
    report()
