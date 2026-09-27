"""P8.1 production contract (plan P8.1; Rule 8 v2 gate 1: reviewer probes are failing tests before the fix).

These tests read production code and never modify it. A test marked xfail(strict=True) states a change the
review request asks Codex to make (shared/reviews/track2_decision_engine_review_request_2026-09-26.md);
when the fix lands the test passes, strict xfail turns that into a failure, and the marker is removed in the
same change. Unmarked tests guard items that are already correct.
"""
from __future__ import annotations

import dataclasses
import inspect
import re

import pytest

from antigravity.models import track2_multi_strategy_engine as mse
from antigravity.models.track2_paper_execution import BracketOrderState, calculate_transaction_costs
from antigravity.models.track2_portfolio_risk_governor import PortfolioRiskGovernor
from antigravity.models.track2_shared_features import SharedFeatureEngine
from research.backtest.cost_model import DhanFeeEngine
from research.decision.sizing import AGGREGATE_EXPOSURE_CAP_RS, SLOT_CAP_RS

SECTORS = {"AAA": "METAL", "BBB": "BANK", "CCC": "IT"}
REQ = "P8.1 review request 2026-09-26"


def _gov() -> PortfolioRiskGovernor:
    return PortfolioRiskGovernor.calibrate_for_corpus(sector_mapping=SECTORS)


# ------------------------------------------------------------------ already correct (guards)
def test_one_fee_function_production_equals_research():
    """Plan C.1 / known answer: Rs 583.33 x 100 MIS round trip = Rs 61.99 at 0.0030699% (NSE FA73061)."""
    prod = sum(calculate_transaction_costs(583.33, 100, side, True)["total_cost"] for side in ("BUY", "SELL"))
    assert round(prod, 2) == DhanFeeEngine.calculate_round_trip(583.33, 583.33, 100).total_charges == 61.99


def test_multi_strategy_engine_calls_methods_that_exist():
    engine_cls = {"orb_engine": mse.MultiTimeframeAlphaEngine, "vwap_engine": mse.VWAPReclaimStrategy,
                  "squeeze_engine": mse.VolatilitySqueezeStrategy, "trapdoor_engine": mse.TrapdoorStrategy,
                  "compass_engine": mse.CompassStrategy, "last_light_engine": mse.LastLightStrategy,
                  "recoil_engine": mse.RecoilStrategy}
    calls = re.findall(r"self\.(\w+_engine)\.(\w+)\(", inspect.getsource(mse.MultiStrategyEngine))
    assert calls
    for attr, meth in calls:
        assert hasattr(engine_cls[attr], meth), f"{attr}.{meth} does not exist"


# ------------------------------------------------------------------ requested changes (strict xfail)
@pytest.mark.xfail(strict=True, reason=f"{REQ} items 1-2: the governor has no side and rejects every short "
                                       "(INVERTED_STOP); intraday shorts in F&O stocks are allowed")
def test_governor_accepts_a_valid_intraday_short():
    r = _gov().assess_candidate("AAA", 100.0, 101.0, 300, [], var_elm_rate=0.2, side="SELL")
    assert r.is_approved and r.proposed_risk_rs == 300.0


@pytest.mark.xfail(strict=True, reason=f"{REQ} items 1-2: a short's stop must be ABOVE its entry")
def test_governor_rejects_a_short_with_its_stop_below_entry():
    r = _gov().assess_candidate("AAA", 100.0, 99.0, 300, [], var_elm_rate=0.2, side="SELL")
    assert not r.is_approved and r.rejection_reason == "INVERTED_STOP"


@pytest.mark.xfail(strict=True, reason=f"{REQ} item 3: Adjusted A1 (Yashu 25 Sep 2026) replaces Rs 58,333.33 / 1,75,000")
def test_governor_uses_adjusted_a1_caps():
    g = _gov()
    assert g.max_single_slot_notional_rs == SLOT_CAP_RS == 38000.0
    assert g.total_capital_allocation_rs == AGGREGATE_EXPOSURE_CAP_RS == 114000.0


@pytest.mark.xfail(strict=True, reason=f"{REQ} item 3: a pending entry counts at its reservation (qty x worst "
                                       "admissible entry), not at its limit price")
def test_governor_counts_pending_entries_at_their_reservation():
    active = [{"symbol": "CCC", "quantity": 100, "entry_price": 380.0, "stop_price": 375.0, "notional_rs": 38_000.0}]
    # BBB is 37,000 at its limit price but reserves 38,200: 38,000 + 38,200 + 38,000 = 1,14,200 > 1,14,000
    pending = [{"symbol": "BBB", "quantity": 100, "entry_price": 370.0, "stop_price": 360.0,
                "reserved_notional_rs": 38_200.0}]
    r = _gov().assess_candidate("AAA", 380.0, 375.0, 100, active, pending_orders=pending, var_elm_rate=0.2)
    assert not r.is_approved and r.rejection_reason == "TOTAL_CAPITAL_EXCEEDED"


@pytest.mark.xfail(strict=True, reason=f"{REQ} item 4: a bracket built without a product must be MIS (fail closed: "
                                       "CNC skips the 15:08 square-off)")
def test_bracket_product_defaults_to_mis():
    field = {f.name: f for f in dataclasses.fields(BracketOrderState)}["product_type"]
    assert field.default == "MIS"


@pytest.mark.xfail(strict=True, reason=f"{REQ} item 7: the 1,000-share floor on the RVOL denominator understates "
                                       "RVOL for high-priced liquid F&O stocks")
def test_rvol_has_no_share_floor():
    assert SharedFeatureEngine.calculate_rvol(500, 400.0) == 1.25
