"""Failing-first probes for the owner-approved Track 2 Adjusted A1 limits.

These are review tests, not a production implementation or a trading approval.
"""

from antigravity.models.execution_policy import ExecutionIntent
from antigravity.models.track2_multi_strategy_engine import MultiStrategyEngine, UnifiedTradeSignal
from antigravity.models.track2_portfolio_risk_governor import PortfolioRiskGovernor
from research.execution_realism.capacity import CapacityConfig


def test_corpus_calibration_uses_adjusted_a1_caps():
    governor = PortfolioRiskGovernor.calibrate_for_corpus()
    assert governor.max_concurrent_positions == 3
    assert governor.max_single_trade_risk_rs == 1500
    assert governor.max_aggregate_risk_rs == 4500
    assert governor.max_single_slot_notional_rs == 38000
    assert governor.total_capital_allocation_rs == 114000


def test_reference_ledger_config_matches_adjusted_a1():
    config = CapacityConfig()
    assert config.slots == 3
    assert config.risk_per_trade_rs == 1500
    assert config.slot_notional_rs == 38000
    assert config.deployable_rs == 114000


def test_explicit_candidate_shares_cannot_bypass_slot_cap():
    candidate = {
        "symbol": "RVNL", "entry_price": 100.0, "stop_loss": 99.0,
        "shares": 500, "risk_rs": 500.0, "atr14": 2.0,
    }
    try:
        intent = ExecutionIntent.create_from_candidate(candidate)
    except ValueError:
        return  # Fail-closed rejection is valid.
    if intent is None:
        return  # Clean fail-closed rejection when caller risk cap is violated
    assert intent.notional_rs <= 38000


def test_pending_orders_reserve_aggregate_exposure():
    governor = PortfolioRiskGovernor.calibrate_for_corpus()
    pending = [
        {"symbol": "RVNL", "sector": "RAIL", "entry_price": 100.0,
         "stop_price": 99.0, "quantity": 380, "notional_rs": 38000.0,
         "open_risk_rs": 380.0},
        {"symbol": "BDL", "sector": "DEFENSE", "entry_price": 100.0,
         "stop_price": 99.0, "quantity": 380, "notional_rs": 38000.0,
         "open_risk_rs": 380.0},
    ]
    result = governor.assess_candidate(
        symbol="CDSL", entry_price=100.0, stop_price=99.0,
        quantity=390, active_positions=[], pending_orders=pending,
        custom_sector="DEPOSITORY", var_elm_rate=0.2,
    )
    assert not result.is_approved


def test_pending_buy_uses_its_limit_not_the_lower_reference_price():
    governor = PortfolioRiskGovernor(
        max_single_trade_risk_rs=1500, max_aggregate_risk_rs=4500,
        total_capital_allocation_rs=114000, max_single_slot_notional_rs=38000,
        enforce_var_elm_gate=True,
    )
    pending = [
        {"symbol": symbol, "sector": sector, "entry_price": 100.0,
         "limit_price": 101.0, "stop_price": 99.0, "quantity": 380}
        for symbol, sector in (("RVNL", "RAIL"), ("BDL", "DEFENSE"))
    ]
    result = governor.assess_candidate(
        symbol="CDSL", entry_price=100.0, stop_price=99.0,
        quantity=380, active_positions=[], pending_orders=pending,
        custom_sector="DEPOSITORY", var_elm_rate=0.2,
    )
    assert not result.is_approved


def test_simultaneous_strategies_cannot_allocate_more_than_cap():
    engine = MultiStrategyEngine()
    signals = [
        UnifiedTradeSignal(
            symbol=symbol, strategy_type=strategy, conviction_score=score,
            entry_price=100.0, stop_price=99.0,
            target_tranche1=101.5, target_tranche2=103.0,
            shares=390, notional_value_rs=39000.0, actual_risk_rs=390.0,
            risk_pct=0.01, volume_multiple=2.0, sector=sector, details={},
        )
        for symbol, strategy, score, sector in [
            ("RVNL", "ORB", 0.9, "RAIL"),
            ("BDL", "RECOIL", 0.8, "DEFENSE"),
            ("CDSL", "PEAD", 0.7, "DEPOSITORY"),
        ]
    ]
    selected = engine.rank_and_allocate(signals)
    assert sum(s.notional_value_rs for s in selected) <= 114000
    assert all(s.notional_value_rs <= 38000 for s in selected)
