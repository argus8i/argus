"""Independent acceptance probes for the exact 7c23f6c Day 4 submission."""
import pandas as pd
from antigravity.engine.backtest_engine import (
    BacktestTrade, DailyEquityPoint, FrictionPolicy, TradeStatus, compute_backtest_metrics,
)
from scripts.run_walk_forward_simulation import run_fold_simulation
from test_codex_day4_9157a86_review import StubStrategy


def run_case(mode):
    days = pd.bdate_range('2023-01-02', periods=33).strftime('%Y-%m-%d').tolist()
    rows = [dict(day=d, open=100., high=101., low=99., close=100., volume=100000) for d in days]
    if mode == 'entry_stop':
        rows[26].update(low=90., volume=1000)
    else:
        rows[27].update(open=94., high=96., low=93., close=94., volume=1000)
    if mode == 'disqualified':
        rows[27].update(open=100., high=101., low=99., close=100.)
    univ = pd.DataFrame([dict(session=d, symbol='TEST', eligible=not (mode == 'disqualified' and i == 27)) for i,d in enumerate(days)])
    fno = pd.DataFrame([dict(session=d, symbol='TEST', nearest_fut_expiry=d) for d in days])
    strategy = StubStrategy(days[25], days[26])
    return days, run_fold_simulation('PROBE', days[25], days[-1], days, univ, fno,
        {'TEST': pd.DataFrame(rows)}, strategy, strategy, FrictionPolicy.realistic())


def test_blocked_entry_stop_remains_pending_on_recovery():
    days, (trades, _, _) = run_case('entry_stop')
    assert trades[0].exit_session == days[27], 'entry stop forgotten after volume cap blocks liquidation and price recovers'


def test_thin_exit_executes_available_partial_and_retains_residual():
    days, (trades, equity, _) = run_case('thin')
    before = next(p for p in equity if p.session == days[26])
    after = next(p for p in equity if p.session == days[27])
    assert after.cash > before.cash, '150-share sell capacity discarded rather than reducing 295-share holding'


def test_blocked_disqualification_remains_pending_after_eligibility_returns():
    days, (trades, _, _) = run_case('disqualified')
    assert trades[0].exit_session == days[28], 'blocked disqualification exit forgotten when eligibility returns'


def test_hurdle_fails_on_cash_buffer_breach():
    t = BacktestTrade('WIN','TEST','TEST','2023-01-02', shares=1, initial_risk_per_share=100,
        net_pnl=100, realized_r=1., status=TradeStatus.CLOSED)
    m = compute_backtest_metrics([t], [DailyEquityPoint('2023-01-02',250000, cash=100000, holdings_value=150000)])
    assert not m.hurdle_passed, 'positive trade statistics accepted despite cash below Rs 136000'
