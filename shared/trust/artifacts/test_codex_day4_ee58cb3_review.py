import pandas as pd
from antigravity.engine.backtest_engine import BacktestTrade, DailyEquityPoint, FrictionPolicy, TradeStatus, compute_backtest_metrics
from scripts.run_walk_forward_simulation import run_fold_simulation
from test_codex_day4_9157a86_review import StubStrategy


def case(mode):
    days = pd.bdate_range('2023-01-02', periods=33).strftime('%Y-%m-%d').tolist()
    rows = [dict(day=d, open=100., high=101., low=99., close=100., volume=100000) for d in days]
    if mode == 'roundtrip':
        rows[26].update(low=90., volume=1000)
    if mode == 'gap_entry':
        rows[26].update(open=90., high=101., low=89., close=100.)
    univ = pd.DataFrame([dict(session=d, symbol='TEST', eligible=(mode != 'held_ineligible' or i <= 26)) for i,d in enumerate(days)])
    fno = pd.DataFrame([dict(session=d, symbol='TEST', nearest_fut_expiry=d) for d in days])
    strategy = StubStrategy(days[25], days[26])
    return run_fold_simulation('PROBE', days[25], days[-1], days, univ, fno,
        {'TEST': pd.DataFrame(rows)}, strategy, strategy, FrictionPolicy.realistic())


def test_roundtrip_uses_aggregate_session_participation():
    trades, _, _ = case('roundtrip')
    used = sum(t.shares * (1 + (t.exit_session == t.entry_session)) for t in trades)
    assert used <= 150, f'1000-volume session cap=150 but total buy/sell shares={used}'


def test_held_position_disqualified_on_current_session():
    trades, _, _ = case('held_ineligible')
    assert trades[0].exit_session == '2023-02-08', 'held ineligible security not exited on first liquid disqualified session'


def test_expectancy_threshold_is_strict():
    t = BacktestTrade('T','TEST','TEST','2023-01-02', shares=1, initial_risk_per_share=100,
        net_pnl=25, realized_r=.25, status=TradeStatus.CLOSED)
    assert not compute_backtest_metrics([t], [DailyEquityPoint('2023-01-02',250000)]).hurdle_passed
