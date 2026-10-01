"""Pinned owner-approved Adjusted A1 controls. Paper only.

The scenario budget is not a guaranteed maximum loss. Lower local ceilings are
permitted; no caller may widen these ceilings or use corpus to scale them up.
"""
import math
from decimal import Decimal, ROUND_FLOOR

MAX_SLOTS = 3
SLOT_CAP_RS = 38_000.00
AGGREGATE_EXPOSURE_CAP_RS = 114_000.00
RISK_PER_TRADE_RS = 1_500.00
AGGREGATE_RISK_CAP_RS = 4_500.00
CAPITAL_RS = 250_000.00
CASH_BUFFER_RS = 136_000.00
ATR_MAX_AGE_SECONDS = 300.0


def finite_positive(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def affordable(limit_price, stop_price, slot_cap=SLOT_CAP_RS, risk_cap=RISK_PER_TRADE_RS):
    """Integer size without float-boundary rounding up. Long risk to stop COST price."""
    if not all(finite_positive(v) for v in (limit_price, stop_price, slot_cap, risk_cap)) or limit_price <= stop_price:
        return 0
    price, stop = Decimal(str(limit_price)), Decimal(str(stop_price))
    n = Decimal(str(min(slot_cap, SLOT_CAP_RS))) / price
    r = Decimal(str(min(risk_cap, RISK_PER_TRADE_RS))) / (price - stop)
    return int(min(n, r).to_integral_value(rounding=ROUND_FLOOR))


def validate_policy(config, corpus):
    if not finite_positive(corpus) or corpus < AGGREGATE_EXPOSURE_CAP_RS:
        raise ValueError("A1_CONFIG_MISMATCH: insufficient or invalid corpus")
    if (config.max_open_positions != MAX_SLOTS or isinstance(config.max_open_positions, bool)
            or config.risk_budget_rs != RISK_PER_TRADE_RS or config.cash_buffer_rs != CASH_BUFFER_RS
            or config.enforce_rule1_lock is not True):
        raise ValueError("A1_CONFIG_MISMATCH: owner-approved limits are pinned")
    config.validate_for_execution()
