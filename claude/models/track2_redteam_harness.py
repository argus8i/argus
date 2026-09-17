"""
track2_redteam_harness.py - Adversarial test harness for Track 2 Liquid Momentum Engine.

Author: Claude (analyst / red team) | Date: 2026-09-11
Target: antigravity/models/liquid_momentum_screener.py

Purpose
-------
The target file's own suite prints "ALL TRACK 2 LIQUID MOMENTUM TESTS PASSED 100%!"
on three happy-path cases. This harness supplies the negative cases that suite omits.
Every assertion below is a property the engine CLAIMS in its own docstrings:
  - "Enforces strict fail-closed data validation"      (screen_universe)
  - "Fails closed on missing or non-positive metrics"   (evaluate_15m_orb_breakout)
  - "true SL-M on NSE cash" / risk_reward_ratio = 2.0   (calculate_position_size)

Run:  .venv/Scripts/python.exe claude/models/track2_redteam_harness.py
Exit code 0 = engine survived all attacks. Non-zero = count of broken properties.

This file lives in claude/ and does not modify Antigravity's engine.
Per shared/00_PROTOCOL.md: own your folder, never edit another assistant's.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from antigravity.models.liquid_momentum_screener import (
    LiquidMomentumEngine as Engine,
    LiquidScripSnapshot as Snap,
)

NAN = float("nan")
_failures = []


def check(tag, title, holds, detail):
    """holds=True means the engine defended the property."""
    if not holds:
        _failures.append(tag + "  " + title)
    print("[{}] {}  {}".format("SURVIVED" if holds else "**BROKEN**", tag, title))
    print("            " + detail + "\n")


# --------------------------------------------------------------------------
# A1 - NaN passthrough on the "fail-closed" validator
# --------------------------------------------------------------------------
def attack_nan_passthrough():
    """Real feeds (pandas / CSV / bhavcopy) emit NaN, not None, for missing numerics.
    The None-membership test does not catch NaN, and every '<' guard is False
    against NaN, so the row passes dtv, beta, ATR and institutional gates."""
    poisoned = Snap("NAN_STOCK", "EQ", 100.0, 10000.0, NAN, NAN, NAN, NAN, 40.0, 20.0, False)
    survivors, _ = Engine.screen_universe([poisoned], min_surviving_pool=1)
    check("A1", "NaN-poisoned row must be rejected by fail-closed validator",
          len(survivors) == 0,
          "dtv/beta/atr/inst_holding all NaN -> survivors={}. "
          "NaN < x is always False, so dtv, beta, ATR and institutional guards all pass."
          .format([c.symbol for c in survivors]))


# --------------------------------------------------------------------------
# A2 - relaxation cannot distinguish "relaxed and fixed" from "relaxed and still empty"
# --------------------------------------------------------------------------
def attack_silent_relaxation():
    junk = [Snap("JUNK", "EQ", 50.0, 9000.0, 1.0, 0.2, 0.5, 1.0, 20.0, 20.0, False)]
    _, meta = Engine.screen_universe(junk, min_surviving_pool=15)
    signalled = (meta.get("survivors_count", 0) >= 15
                 or meta.get("below_target") is True
                 or "STILL" in str(meta.get("reason", "")).upper())
    check("A2", "Screener must signal that the pool is STILL thin after relaxation",
          signalled,
          "meta={}. Returns an EMPTY pool flagged relaxed=True with a reason implying "
          "the relaxation worked. No field separates success from failure.".format(meta))


# --------------------------------------------------------------------------
# A3 - risk_reward_ratio is hardcoded 2.0 on a path whose exit sits below the trigger
# --------------------------------------------------------------------------
def attack_rr_overstatement():
    r = Engine.calculate_position_size(entry_price=76.20, or_low=74.20, atr14=0.85,
                                       dtv_med20_cr=450.0, exchange="BSE")
    realized = 76.20 - r.limit_exit_price
    reward = r.target_price - 76.20
    true_rr = reward / realized
    check("A3", "Reported risk_reward_ratio must match realizable R:R",
          abs(r.risk_reward_ratio - true_rr) < 0.05,
          "reported {:.2f} (breakeven 33.3%) vs true {:.3f} (breakeven {:.1f}%). "
          "SL-Limit exit {} is {:.3f}/sh below entry, not {:.3f}. Overstated {:.0f}%."
          .format(r.risk_reward_ratio, true_rr, 100.0 / (1.0 + true_rr),
                  r.limit_exit_price, realized, 76.20 - r.stop_price,
                  100.0 * (r.risk_reward_ratio / true_rr - 1.0)))


# --------------------------------------------------------------------------
# A4 / A5 - degenerate stop triggers a silent fallback that misreports risk
# --------------------------------------------------------------------------
def attack_degenerate_stop():
    r = Engine.calculate_position_size(entry_price=100.0, or_low=105.0, atr14=0.5,
                                       dtv_med20_cr=450.0, exchange="NSE")
    check("A4", "Stop price must be strictly below entry price",
          r.stop_price < 100.0,
          "entry=100.00 or_low=105.00 -> stop_price={} (ABOVE entry). Fallback resets "
          "risk_per_share to 1.5% but never recomputes stop_price.".format(r.stop_price))

    implied = r.shares * (100.0 - r.stop_price)
    check("A5", "actual_risk_rs must equal shares * (entry - stop)",
          abs(r.actual_risk_rs - implied) < 1.0,
          "reported Rs {} vs shares*(entry-stop) = Rs {:.2f} -> disagreement of "
          "Rs {:.2f} on a Rs 1,500 budget."
          .format(r.actual_risk_rs, implied, abs(r.actual_risk_rs - implied)))


# --------------------------------------------------------------------------
# A6 - the F&O premise that justifies Track 2 is not enforced anywhere in code
# --------------------------------------------------------------------------
def attack_fno_unenforced():
    """shared/02_WATCHLIST.md states: because all 8 scrips are F&O underlyings, NSE
    applies dynamic flexing bands rather than fixed 5% circuit freezes. That is
    Track 2's entire structural advantage over Track 1 - and no code checks it."""
    fixed_band = Snap("NON_FNO", "EQ", 300.0, 9000.0, 60.0, 1.9, 4.5, 20.0, 40.0, 10.0, False)
    survivors, _ = Engine.screen_universe([fixed_band], min_surviving_pool=1)
    check("A6", "Non-F&O fixed-band stock must not enter a 'dynamic band' universe",
          len(survivors) == 0,
          "band_pct=10.0 (fixed, freezable) -> survivors={}. LiquidScripSnapshot has no "
          "is_fno_underlying field; band_pct in [10, 20] is accepted. The freeze-immunity "
          "premise is carried only by a hand-written table."
          .format([c.symbol for c in survivors]))


# --------------------------------------------------------------------------
# A7 - no maximum-extension guard on the breakout
# --------------------------------------------------------------------------
def attack_extended_chase():
    res = Engine.evaluate_15m_orb_breakout("EXT", current_price=110.0, or_high=100.0,
                                           or_low=99.0, bucket_volume=5e6,
                                           historical_bucket_volume_median=1e6,
                                           atr14_intraday=1.0)
    check("A7", "Breakout 10% above the OR high must not be a clean BUY",
          res["signal"] != "BUY_ORB_CONFIRMED",
          "price 110 vs OR high 100 (+10.0% extended) -> signal={}. Any distance above "
          "OR high qualifies; atr14_intraday is accepted and unused."
          .format(res["signal"]))


# --------------------------------------------------------------------------
# Basket A under AGENTS.md Rule 11 track isolation (supersedes the 8-name basket).
# Values from shared/track2_liquid/02_WATCHLIST.md - NOTE: beta / ATR / DTV are
# currently unsourced in that file (see Q15).
# --------------------------------------------------------------------------
BASKET_A = [
    Snap("CDSL",     "EQ", 1650.0, 28000.0, 120.0, 1.45, 4.1, 25.5, 55.0, 0.0, False, is_fno_underlying=True),
    Snap("ANGELONE", "EQ", 2800.0, 27200.0, 180.0, 1.62, 4.8, 34.0, 50.0, 0.0, False, is_fno_underlying=True),
    Snap("SUZLON",   "EQ",   75.0, 62500.0, 550.0, 1.39, 4.8, 26.0, 42.0, 0.0, False, is_fno_underlying=True),
    Snap("INOXWIND", "EQ",  210.0, 13000.0,  95.0, 1.55, 5.2, 24.5, 45.0, 0.0, False, is_fno_underlying=True),
]


# --------------------------------------------------------------------------
# A8 - is Basket A selectable by the strict screen? (Rule 11 claim)
# --------------------------------------------------------------------------
def attack_basket_self_consistency():
    survivors, _ = Engine.screen_universe(BASKET_A, min_surviving_pool=4)
    names = [c.symbol for c in survivors]
    check("A8", "Basket A must be fully selectable by the STRICT screen",
          len(survivors) == 4,
          "strict survivors {}/4: {}. Rule 11 segregates sovereign PSUs (70-75% GOI "
          "holding cannot meet a 15% institutional float) into a manual satellite, so "
          "the automated universe should now be self-consistent.".format(len(names), names))


# --------------------------------------------------------------------------
# A9 - does the segregation actually DISABLE artificial relaxation?
# --------------------------------------------------------------------------
def attack_relaxation_disabled():
    """shared/track2_liquid/02_WATCHLIST.md sec.2 states Basket B is 'excluded from the
    automated screener to avoid triggering artificial threshold relaxation'. The trigger
    is len(survivors) < min_surviving_pool, which still defaults to 15 against a 4-name
    universe - so removing Basket B removes the symptom, not the mechanism."""
    _, meta = Engine.screen_universe(BASKET_A)   # library default
    check("A9", "Segregated Basket A must not trigger artificial relaxation",
          meta.get("relaxed") is False and meta.get("below_target") is False,
          "Basket A (4/4 strict-qualified) at DEFAULT min_surviving_pool=15 returns "
          "relaxed={}, below_target={}, reason='{}'. A clean strict-qualified pool is "
          "mislabelled as degraded. Harmless today only because the relaxed filter "
          "returns the same 4 names; set min_surviving_pool<=4 to make it structural."
          .format(meta.get("relaxed"), meta.get("below_target"), meta.get("reason")))


# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# A10 - Does engine's dynamic SL-Limit R:R match the analytical curve?
# --------------------------------------------------------------------------
def attack_rr_matches_dynamic_curve():
    """Validates that realized R:R conforms to the analytical dynamic formula
    R(w) = 2*w / (w + 0.005*(1-w)) across operating stop widths w in [0.8%, 5.0%]."""
    all_match = True
    err_msg = ""
    for w in (0.008, 0.010, 0.015, 0.020, 0.030, 0.050):
        entry = 1000.0
        r = Engine.calculate_position_size(entry_price=entry, or_low=entry * (1.0 - w),
                                           atr14=999.0, dtv_med20_cr=500.0, exchange="BSE")
        expected_rr = round((2.0 * w) / (w + (0.005 * (1.0 - w))), 3)
        diff = abs(r.risk_reward_ratio - expected_rr)
        print("            stop {:>5.2f}%  ->  Engine R:R {:.3f} vs Analytical {:.3f} (diff {:.3f})".format(
            w * 100, r.risk_reward_ratio, expected_rr, diff))
        if diff > 0.01:
            all_match = False
            err_msg = f"Discrepancy at stop {w*100}%: engine {r.risk_reward_ratio} vs analytical {expected_rr}"
            break

    check("A10", "Engine dynamic SL-Limit R:R must strictly match analytical curve",
          all_match,
          err_msg or "Engine dynamic SL-Limit R:R deviated from analytical curve")


def main():
    print("=" * 78)
    print("RED-TEAM ATTACK SUITE - TRACK 2 LIQUID MOMENTUM ENGINE")
    print("Claude (analyst / red team) - 2026-09-11")
    print("=" * 78 + "\n")

    for attack in (attack_nan_passthrough, attack_silent_relaxation,
                   attack_rr_overstatement, attack_degenerate_stop,
                   attack_fno_unenforced, attack_extended_chase,
                   attack_basket_self_consistency, attack_relaxation_disabled,
                   attack_rr_matches_dynamic_curve):
        attack()

    print("=" * 78)
    if _failures:
        print("RESULT: {} properties BROKEN:".format(len(_failures)))
        for f in _failures:
            print("   - " + f)
    else:
        print("RESULT: engine survived all attacks.")
    print("=" * 78)
    return len(_failures)


if __name__ == "__main__":
    sys.exit(main())
