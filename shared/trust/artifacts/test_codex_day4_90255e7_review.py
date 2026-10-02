"""Independent acceptance probes for 90255e7; no implementation edits."""
import ast
from pathlib import Path
import pandas as pd
import pytest
from antigravity.engine.backtest_engine import BacktestTrade, BacktestSimulation, DailyEquityPoint, FrictionPolicy, TradeStatus, compute_backtest_metrics
from scripts.run_walk_forward_simulation import run_fold_simulation
from test_codex_day4_9157a86_review import StubStrategy
from test_codex_day4_7c23f6c_review import run_case

ROOT = Path(r'C:\Users\yashw\swing trades')

def winner():
    return BacktestTrade('WIN', 'TEST', 'TEST', '2023-01-02', shares=1,
        initial_risk_per_share=100, net_pnl=100, realized_r=1., status=TradeStatus.CLOSED)

def test_missing_cash_observations_fail_closed():
    assert not compute_backtest_metrics([winner()], []).hurdle_passed

def test_pooled_runner_preserves_cash_failure():
    m = compute_backtest_metrics([winner()], [DailyEquityPoint('2023-01-02', 250000, cash=100000)])
    assert not m.hurdle_passed
    tree = ast.parse((ROOT / 'scripts/run_walk_forward_simulation.py').read_text(encoding='utf-8'))
    assignments = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
        and t.value.id == 'pooled_metrics' and t.attr == 'hurdle_passed' for t in n.targets)]
    assert len(assignments) == 1
    expr = ast.Expression(assignments[0].value)
    value = eval(compile(expr, '<exact runner pooled predicate>', 'eval'), {'pooled_metrics': m})
    assert not value, 'runner overwrites cash failure with a cash-free predicate'

def test_partial_ledger_repricing_matches_identical_policy():
    _, (trades, _, _) = run_case('thin')
    t = trades[0]
    assert t.status == TradeStatus.CLOSED and t.total_sold_shares == t.initial_shares
    repriced = BacktestSimulation().compute_ledger_net_pnl([t], FrictionPolicy.realistic())
    assert repriced == pytest.approx(round(t.net_pnl, 2), abs=.01), (repriced, t.net_pnl)

def test_locked_stop_intent_survives_recovery():
    days = pd.bdate_range('2023-01-02', periods=33).strftime('%Y-%m-%d').tolist()
    rows = [dict(day=d, open=100., high=101., low=99., close=100., volume=100000) for d in days]
    rows[27].update(open=90., high=90., low=90., close=90., volume=0)
    univ = pd.DataFrame([dict(session=d, symbol='TEST', eligible=True) for d in days])
    fno = pd.DataFrame([dict(session=d, symbol='TEST', nearest_fut_expiry=d) for d in days])
    strategy = StubStrategy(days[25], days[26])
    trades, _, _ = run_fold_simulation('LOCK', days[25], days[-1], days, univ, fno,
        {'TEST': pd.DataFrame(rows)}, strategy, strategy, FrictionPolicy.realistic())
    assert trades[0].exit_session == days[28], 'stop intent on locked bar forgotten after recovery'
