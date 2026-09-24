import pytest

from antigravity.models.track2_paper_execution import assess_execution


ORDER = {"symbol": "CDSL", "side": "BUY", "limit_price": 100.0, "quantity": 20,
         "signal_timestamp": "2026-09-22T09:31:00+05:30", "strategy_rules_sha256": "a" * 64}
QUOTE = {"best_bid": 100.0, "best_ask": 100.05, "bid_qty": 100, "ask_qty": 400}


def _trade(identifier, quantity, price=100.0, second=2):
    return {"trade_id": identifier, "timestamp": f"2026-09-22T09:31:0{second}+05:30",
            "price": price, "quantity": quantity}


def test_price_touch_or_insufficient_turnover_never_claims_fill():
    result = assess_execution(order_id="p1", instruction=ORDER, arrival_quote=QUOTE,
                              trades=[_trade("t1", 50)], order_arrival_timestamp="2026-09-22T09:31:01+05:30",
                              source_data_sha256="b" * 64, queue_haircut=.25, full_cost_deduction=12)
    assert result["state"] == "PARTIAL" and result["fill_evidence"] is None


def test_queue_plus_order_and_haircut_are_required_for_e3():
    result = assess_execution(order_id="p1", instruction=ORDER, arrival_quote=QUOTE,
                              trades=[_trade("t1", 160)], order_arrival_timestamp="2026-09-22T09:31:01+05:30",
                              source_data_sha256="b" * 64, queue_haircut=.25, full_cost_deduction=12)
    assert result["state"] == "FILLED"
    assert result["fill_evidence"]["evidence_class"] == "E3_TICK_QUEUE"


def test_duplicate_trade_ids_fail_closed():
    with pytest.raises(ValueError, match="unique"):
        assess_execution(order_id="p1", instruction=ORDER, arrival_quote=QUOTE,
                         trades=[_trade("t1", 50), _trade("t1", 150, second=3)],
                         order_arrival_timestamp="2026-09-22T09:31:01+05:30",
                         source_data_sha256="b" * 64, queue_haircut=.25, full_cost_deduction=12)


def test_unbound_top_of_book_never_creates_e3():
    quote = dict(QUOTE, best_bid=99.95)
    result = assess_execution(order_id="p1", instruction=ORDER, arrival_quote=quote,
                              trades=[_trade("t1", 1000)], order_arrival_timestamp="2026-09-22T09:31:01+05:30",
                              source_data_sha256="b" * 64, queue_haircut=.25, full_cost_deduction=12)
    assert result["fill_evidence"] is None


def test_calculate_queue_rank_no_volume_shave():
    from antigravity.models.track2_paper_execution import calculate_queue_rank
    depth = {
        "bids": [
            {"price": 100.0, "qty": 500, "orders": 5},
            {"price": 99.95, "qty": 800, "orders": 8},
        ],
        "total_bid_qty": 1300,
    }
    # An arriving BUY order at 100.0 must be placed behind all 500 shares already resting at 100.0
    rank = calculate_queue_rank(order_qty=50, order_price=100.0, side="BUY", depth=depth)
    assert rank["vol_ahead"] == 500
    assert rank["required_turnover_for_fill"] == 550


def test_bracket_exit_deducts_proportional_entry_and_exit_friction():
    from antigravity.models.track2_paper_execution import BracketOrderManager
    bracket = BracketOrderManager.create_bracket(
        order_id="TEST_B1",
        symbol="CDSL",
        entry_price=100.0,
        stop_price=95.0,
        total_shares=100,
        target_1_rr=1.5,
        product_type="MIS",
    )
    # Target 1 is at 107.50
    # Simulate high reaching target 1 (108.0) with verified execution evidence
    b_updated = BracketOrderManager.update_bracket_quote(
        bracket=bracket,
        ltp=107.50,
        tick_high=108.0,
        tick_low=99.0,
        timestamp="2026-09-23T10:00:00+05:30",
        execution_evidence=True,
    )
    assert b_updated.is_t1_target_filled is True
    # Realized net P&L must be strictly less than gross P&L due to friction
    assert b_updated.realized_pnl_gross > 0
    assert b_updated.total_friction_cost > 0
    assert b_updated.realized_pnl_net == round(b_updated.realized_pnl_gross - b_updated.total_friction_cost, 2)


def test_quote_only_target_reach_does_not_fill_without_evidence():
    # Codex R06: Quote reach without execution evidence must not claim filled target or manufacture profit
    from antigravity.models.track2_paper_execution import BracketOrderManager
    bracket = BracketOrderManager.create_bracket(
        order_id="TEST_B2",
        symbol="CDSL",
        entry_price=1000.0,
        stop_price=980.0,
        total_shares=10,
        target_1_rr=1.5,
    )
    # Target 1 is at 1030.0. Quote reaches 1035.0 without execution evidence
    b_quote = BracketOrderManager.update_bracket_quote(
        bracket=bracket,
        ltp=1032.0,
        tick_high=1035.0,
        execution_evidence=False,
    )
    assert b_quote.is_t1_target_filled is False
    assert b_quote.realized_pnl_gross == 0.0
    assert b_quote.total_friction_cost == 0.0
    assert b_quote.is_t2_breakeven_trailed is False



