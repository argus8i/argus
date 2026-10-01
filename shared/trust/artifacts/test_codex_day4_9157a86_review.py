import pandas as pd
import pytest
from scripts.run_walk_forward_simulation import run_fold_simulation
from antigravity.engine.backtest_engine import (
    BacktestTrade, DailyEquityPoint, FrictionPolicy, TradeStatus, compute_backtest_metrics,
)
from antigravity.strategies.base_strategy import SignalEvent


class StubStrategy:
    def __init__(self, signal_day, entry_day):
        self.signal_day, self.entry_day = signal_day, entry_day

    def generate_signals(self, session, market_data, context):
        if session != self.signal_day:
            return []
        return [SignalEvent(strategy_id="EXPIRY_RELIEF", symbol="TEST",
            session_date=session, entry_session=self.entry_day, reference_price=100,
            stop_loss_price=95, target_price=110, priority_score=1, trace={"synthetic_probe": True})]


def run_case(mode, year=2023):
    days = pd.bdate_range(f"{year}-01-02", periods=33).strftime("%Y-%m-%d").tolist()
    rows = [dict(day=d, open=100., high=101., low=99., close=100., volume=100000) for d in days]
    entry = 26
    if mode == "not_triggered":
        rows[entry].update(open=98., high=99., low=97., close=98.)
    if mode == "entry_stop":
        rows[entry].update(low=90., close=91.)
    if mode == "locked":
        for i in range(entry+1, len(days)):
            p = 100 * .95 ** (i-entry)
            rows[i].update(open=p, high=p, low=p, close=p, volume=0)
    if mode == "thin_exit":
        rows[entry+1].update(open=94., high=96., low=93., close=94., volume=10)
    univ = pd.DataFrame([dict(session=d, symbol="TEST", eligible=(mode != "ineligible" or i <= 25)) for i,d in enumerate(days)])
    fno = pd.DataFrame([dict(session=d, symbol="TEST", nearest_fut_expiry=d) for d in days])
    strategy = StubStrategy(days[25], days[26])
    return run_fold_simulation("PROBE", days[25], days[-1], days, univ, fno,
        {"TEST": pd.DataFrame(rows)}, strategy, strategy, FrictionPolicy.realistic())


def test_buy_stop_requires_trigger():
    trades, _, _ = run_case("not_triggered")
    assert not trades, "BUY_STOP filled despite high below trigger"


def test_entry_session_stop_is_not_ignored():
    trades, _, _ = run_case("entry_stop")
    assert trades[0].exit_session == trades[0].entry_session


def test_time_stop_cannot_liquidate_zero_volume_lock():
    trades, _, _ = run_case("locked")
    assert trades[0].status == TradeStatus.UNRESOLVED


def test_exit_respects_volume_cap():
    trades, _, _ = run_case("thin_exit")
    assert trades[0].exit_session != "2023-02-08", "Entire holding sold on 10-share session"


def test_entry_revalidates_current_eligibility():
    trades, _, _ = run_case("ineligible")
    assert not trades


def test_actual_runner_blocks_holdout():
    with pytest.raises(PermissionError):
        run_case("normal", year=2025)


def test_drawdown_gate_fails_above_six_percent():
    trade = BacktestTrade("WIN", "TEST", "TEST", "2023-01-02", shares=10,
        initial_risk_per_share=10, net_pnl=100, realized_r=1, status=TradeStatus.CLOSED)
    metrics = compute_backtest_metrics([trade], [DailyEquityPoint("2023-01-02",250000),
        DailyEquityPoint("2023-01-03",230000)])
    assert not metrics.hurdle_passed


def test_drawdown_includes_initial_corpus():
    metrics = compute_backtest_metrics([], [DailyEquityPoint("2023-01-02",230000)])
    assert metrics.max_drawdown_rs == 20000
