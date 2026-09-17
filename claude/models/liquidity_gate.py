"""
LIQUIDITY GATE  --  proposed replacement for the scalar QUEUE_MULT
Claude, 2026-09-11. For antigravity/models/ and claude/models/fill_model.py.

QUEUE_MULT=1.0 was calibrated on CROPSTER Day 3: 12,560 shares against
15,735,454 traded = 0.08% participation. At 0.08% you are invisible and a
full same-session fill is reasonable.

The same constant is currently applied to CHANDRIMA, where a 4,500-share
position was 70.8% of the 10-Sep volume (6,355 shares, 67 trades, Rs.96,723
of turnover for the entire day). At 70% participation you are not filling
into the market -- you ARE the market.

Fix: make fill capacity a function of participation, not a constant.
"""
MAX_PARTICIPATION = 0.15   # realistic share of a session's volume you can be
                           # without being the print that moves the price.
                           # Conservative for micro-caps. Calibrate, don't guess.


def daily_fill_fraction(position_shares, daily_volume, max_participation=MAX_PARTICIPATION):
    """Fraction of the position realistically clearable in ONE session."""
    if daily_volume <= 0:
        return 0.0
    return min(1.0, max_participation * daily_volume / position_shares)


def sessions_to_exit(position_shares, daily_volume, max_participation=MAX_PARTICIPATION):
    f = daily_fill_fraction(position_shares, daily_volume, max_participation)
    if f <= 0:
        return float("inf")
    return 1.0 / f


def max_position_for_exit_in(days, daily_volume, max_participation=MAX_PARTICIPATION):
    """Largest position exitable within `days` sessions. THE SIZING RULE."""
    return int(days * max_participation * daily_volume)


def gate(position_shares, daily_volume, band=0.05, max_days=2):
    """Pre-trade gate. Returns (pass, reason, worst-case exit cost)."""
    s = sessions_to_exit(position_shares, daily_volume)
    cost = 1 - (1 - band) ** s if s != float("inf") else 1.0
    ok = s <= max_days
    return ok, f"{s:.1f} sessions to clear", cost


if __name__ == "__main__":
    print(f"{'name':<11}{'volume':>13}{'position':>10}{'partic':>9}"
          f"{'fill/day':>10}{'sessions':>10}{'exit cost':>11}  gate")
    cases = [("CROPSTER", 15_735_454, 12_560, 0.05),
             ("CCDL",     29_339_415, 30_000, 0.05),
             ("GATECH",      268_977, 30_000, 0.05),
             ("CHANDRIMA",     6_355,  4_500, 0.02)]
    for n, v, p, b in cases:
        f = daily_fill_fraction(p, v)
        s = sessions_to_exit(p, v)
        ok, _, c = gate(p, v, band=b)
        print(f"{n:<11}{v:>13,}{p:>10,}{p/v:>8.2%}{f:>10.1%}{s:>10.1f}"
              f"{c:>10.1%}  {'PASS' if ok else 'FAIL'}")

    print("\nSIZING RULE  --  max position exitable in 2 sessions "
          f"(at {MAX_PARTICIPATION:.0%} participation):")
    for n, v, px in [("CROPSTER", 15_735_454, 3.17), ("CCDL", 29_339_415, 1.38),
                     ("GATECH", 268_977, 0.78), ("CHANDRIMA", 6_355, 15.22)]:
        q = max_position_for_exit_in(2, v)
        print(f"  {n:<11}{q:>12,} shares = Rs.{q*px:>12,.0f}")
