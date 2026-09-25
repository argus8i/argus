"""Foundation tests: bars and NSE ticks, session policy, order-level Dhan fees, queue fixes."""
from datetime import date, datetime, time, timedelta

import pytest

from research.backtest.bars import IST, Bar, CandleStore, on_tick_grid, resample, round_to_tick, tick_size
from research.backtest.cost_model import DhanFeeEngine, OrderSide, ProductType
from research.backtest.order_book import ExecutionState, L2QueueSimulator, OrderSide as QSide, SimulatedOrder
from research.backtest.policy import SessionPhase, SessionPolicy


def _bar(minute_offset=0, o=100.0, h=101.0, l=99.5, c=100.5, v=20000, minutes=15, sym="ABC", day=date(2026, 9, 24)):
    start = datetime.combine(day, time(9, 15), IST) + timedelta(minutes=minute_offset)
    return Bar(sym, start, minutes, o, h, l, c, v)


# ---------------------------------------------------------------- ticks
@pytest.mark.parametrize("price,tick", [(49.37, 0.01), (250.0, 0.01), (250.05, 0.05), (1000.0, 0.05),
                                        (1000.1, 0.10), (5000.0, 0.10), (7500.0, 0.50), (15000.0, 1.00),
                                        (25000.0, 5.00)])
def test_tick_table(price, tick):
    assert tick_size(price) == tick


def test_round_to_tick_directions():
    assert round_to_tick(300.07, "down") == 300.05
    assert round_to_tick(300.01, "up") == 300.05
    assert round_to_tick(1234.56, "nearest") == 1234.60
    assert on_tick_grid(1095.10) and not on_tick_grid(1095.15)


def test_tick_size_rejects_non_positive():
    with pytest.raises(ValueError):
        tick_size(0.0)


# ---------------------------------------------------------------- bars
def test_bar_rejects_inconsistent_ohlc():
    with pytest.raises(ValueError):
        _bar(h=99.0)            # high below open/close
    with pytest.raises(ValueError):
        _bar(v=-1)


def test_bar_mapping_and_end_time():
    b = _bar()
    assert b.end == b.start + timedelta(minutes=15)
    assert b.hm == "09:15"
    m = b.as_mapping()
    assert m["timestamp"].startswith("2026-09-24T09:15:00") and m["close"] == 100.5


def test_resample_one_minute_to_fifteen():
    ones = [_bar(i, o=100 + i, h=101 + i, l=99 + i, c=100.5 + i, v=10, minutes=1) for i in range(30)]
    fifteen = resample(ones, 15)
    assert len(fifteen) == 2
    first = fifteen[0]
    assert first.open == 100 and first.close == 114.5 and first.high == 115 and first.low == 99
    assert first.volume == 150 and first.minutes == 15


def test_candle_store_from_historical_file_shape(tmp_path):
    payload = {"symbols": {"ABC": {
        "bars": [_bar(15 * i).as_mapping() for i in range(3)],
        "daily_bars": [{"timestamp": "2026-09-23T00:00:00+05:30", "open": 99.0, "high": 101.0,
                        "low": 98.0, "close": 100.0, "volume": 1000000}]}}}
    p = tmp_path / "hist.json"
    import json
    p.write_text(json.dumps(payload), encoding="utf-8")
    store = CandleStore.from_historical_json(p)
    assert store.symbols == ["ABC"]
    assert store.sessions("ABC") == [date(2026, 9, 24)]
    assert [b.hm for b in store.bars("ABC", date(2026, 9, 24))] == ["09:15", "09:30", "09:45"]
    assert store.daily("ABC")[0].close == 100.0
    report = store.quality_report()
    assert report["invalid_bars"] == 0 and report["off_tick_prices"] == 0


# ---------------------------------------------------------------- policy
def test_policy_default_is_dhan_ladder():
    p = SessionPolicy()
    assert p.freeze_entries == time(14, 50) and p.hard_escalation == time(15, 8) and p.broker_rms == time(15, 10)
    assert p.entries_allowed(time(14, 45)) and not p.entries_allowed(time(14, 50))
    assert not p.entries_allowed(time(9, 20))
    assert p.phase_at(time(15, 6)) == SessionPhase.BOUNDED_EXIT
    assert p.phase_at(time(15, 9)) == SessionPhase.HARD_EXIT
    assert p.phase_at(time(15, 11)) == SessionPhase.BROKER_RMS


def test_policy_rejects_out_of_order_times():
    with pytest.raises(ValueError):
        SessionPolicy(hard_escalation=time(15, 11))      # after broker RMS


# ---------------------------------------------------------------- fees
def test_partial_fills_of_one_order_share_one_brokerage_cap():
    one_order = DhanFeeEngine.calculate_order(OrderSide.BUY, [(500.0, 100), (500.0, 100)], ProductType.MIS)
    two_orders = [DhanFeeEngine.calculate_leg(OrderSide.BUY, 500.0, 100, ProductType.MIS) for _ in range(2)]
    assert one_order.brokerage == 20.0                      # min(20, 0.03% x 1,00,000 = 30)
    assert sum(x.brokerage for x in two_orders) == 30.0     # 2 x min(20, 15)
    assert one_order.shares == 200 and one_order.turnover == 100000.0


def test_order_fee_components_match_schedule():
    f = DhanFeeEngine.calculate_order(OrderSide.SELL, [(250.0, 200)], ProductType.MIS)
    assert f.turnover == 50000.0
    assert f.brokerage == 15.0 and f.stt == 12.5 and f.stamp_duty == 0.0
    assert f.gst == round(0.18 * (f.brokerage + f.exchange_charges + f.sebi_charges), 2)


def test_order_requires_fills():
    with pytest.raises(ValueError):
        DhanFeeEngine.calculate_order(OrderSide.BUY, [], ProductType.MIS)


def test_auto_squareoff_fee_is_twenty_plus_gst():
    f = DhanFeeEngine.calculate_order(OrderSide.SELL, [(100.0, 10)], ProductType.MIS, is_auto_squareoff=True)
    assert f.auto_squareoff_penalty == 23.6


# ---------------------------------------------------------------- queue fixes
def _order(side=QSide.BUY, price=100.0, qty=100, rank=10000, mid=100.025):
    return SimulatedOrder(order_id="o1", symbol="ABC", side=side, order_price=price, total_shares=qty,
                          arrival_midpoint=mid, arrival_timestamp="t0", queue_rank_ahead=rank)


def test_trade_through_fills_resting_buy_completely():
    o = _order()
    L2QueueSimulator.process_order_event(o, "t1", trade_volume=100, bid_depth=5000, ask_depth=5000,
                                         best_bid=99.95, best_ask=100.05, last_trade_price=99.95)
    assert o.state == ExecutionState.FILLED and o.filled_shares == 100
    assert o.fills[-1].fill_price == 100.0 and o.fills[-1].evidence == "TRADE_THROUGH"


def test_bid_falling_below_order_without_trade_only_clears_queue():
    o = _order()
    L2QueueSimulator.process_order_event(o, "t1", trade_volume=0, bid_depth=5000, ask_depth=5000,
                                         best_bid=99.95, best_ask=100.05)
    assert o.state == ExecutionState.QUEUED and o.filled_shares == 0 and o.queue_rank_ahead == 0


def test_marketable_buy_takes_displayed_liquidity_without_queueing():
    o = _order(price=100.05, rank=50000)
    L2QueueSimulator.process_order_event(o, "t1", trade_volume=0, bid_depth=5000, ask_depth=60,
                                         best_bid=99.95, best_ask=100.0)
    assert o.state == ExecutionState.PARTIAL and o.filled_shares == 60
    assert o.fills[-1].liquidity_flag == "TAKER" and o.fills[-1].fill_price == 100.0


def test_marketable_limit_walks_ladder_up_to_limit():
    o = _order(price=100.05, qty=100, rank=0, mid=99.975)       # book 99.95 / 100.00
    L2QueueSimulator.execute_marketable_limit(o, [(100.00, 50), (100.05, 30), (100.10, 100)], "t1")
    assert o.filled_shares == 80 and o.state == ExecutionState.PARTIAL
    assert [(f.fill_price, f.fill_shares) for f in o.fills] == [(100.00, 50), (100.05, 30)]
    assert o.implementation_shortfall_bps > 0


def test_rule4_locked_no_bid_name_exists():
    assert ExecutionState.LOCKED_NO_BID == ExecutionState.LOCKED_NO_LIQUIDITY


def test_sell_trade_through_is_symmetric():
    o = _order(side=QSide.SELL, price=100.0)
    L2QueueSimulator.process_order_event(o, "t1", trade_volume=10, bid_depth=5000, ask_depth=5000,
                                         best_bid=99.95, best_ask=100.0, last_trade_price=100.05)
    assert o.state == ExecutionState.FILLED and o.fills[-1].evidence == "TRADE_THROUGH"


def test_resting_order_met_by_crossing_contra_fills_as_maker_at_own_price():
    o = _order(price=100.0, rank=500)
    L2QueueSimulator.process_order_event(o, "t1", trade_volume=0, bid_depth=5000, ask_depth=5000,
                                         best_bid=100.0, best_ask=100.05)          # rests behind 500
    assert o.state == ExecutionState.QUEUED and o.has_rested
    L2QueueSimulator.process_order_event(o, "t2", trade_volume=0, bid_depth=5000, ask_depth=40,
                                         best_bid=99.95, best_ask=99.95)           # contra now at/through us
    assert o.filled_shares == 40 and o.fills[-1].fill_price == 100.0
    assert o.fills[-1].liquidity_flag == "MAKER" and o.fills[-1].evidence == "CONTRA_CROSSED"
