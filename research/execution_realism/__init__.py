"""Execution-realism reference implementation for the Track 2 NSE 15-minute ORB desk.

Modules map to the four pillars of the specification:
  marketdata    snapshot contract, per-symbol freshness, volume deltas   (R01, R07, R08, R09)
  fills         touch != fill; trade-through, quote-through, queue model (R06)
  capacity      cross-process reservation ledger, batch arbitration      (R02, R03, R04, R12, R16)
  features      adverse-selection features, winner's-curse test, EV fit  (Pillar 2)
  exits         dynamic band, SL-limit, emergency exit, costs, skip risk (R05)
  surveillance  fail-closed snapshot contract and pre-market gate        (R11)
  calibration   cancel-intensity bound, depth persistence (haircuts)     (Pillar 1)

Standard library only. Paper-trading reference code (Rule 1): it has no broker
connectivity and must pass the tri-agent review (Rule 8) before it replaces
anything in antigravity/.
"""
__version__ = "0.1.0"
