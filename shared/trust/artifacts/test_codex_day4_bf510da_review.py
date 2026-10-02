"""Independent bf510da acceptance regressions; implementation remains unchanged."""
from pathlib import Path
from types import SimpleNamespace
import pandas as pd
import pytest
from antigravity.engine.backtest_engine import DailyEquityPoint, FrictionPolicy, compute_backtest_metrics
from scripts.run_walk_forward_simulation import run_fold_simulation
from test_codex_day4_9157a86_review import StubStrategy
from test_codex_day4_90255e7_review import winner

ROOT = Path(r'C:\Users\yashw\swing trades')

def time_lock(recover):
    days = pd.bdate_range('2023-01-02', periods=33).strftime('%Y-%m-%d').tolist()
    rows = [dict(day=d, open=100., high=101., low=99., close=100., volume=100000) for d in days]
    # EXPIRY_RELIEF reaches its fifth holding session at index 31.
    rows[31].update(volume=0)
    rows[32].update(open=103., high=105., low=102., close=104.)
    univ = pd.DataFrame([dict(session=d, symbol='TEST', eligible=True) for d in days])
    fno = pd.DataFrame([dict(session=d, symbol='TEST', nearest_fut_expiry=d) for d in days])
    strategy = StubStrategy(days[25], days[26])
    end = days[32] if recover else days[31]
    trades, _, _ = run_fold_simulation('TIMELOCK', days[25], end, days, univ, fno,
        {'TEST': pd.DataFrame(rows)}, strategy, strategy, FrictionPolicy.realistic())
    return trades[0]

def test_locked_time_stop_persists_at_fold_boundary():
    t = time_lock(False)
    assert t.holding_sessions == 5
    assert t.pending_exit_reason == 'TIME_STOP', 'expired holding limit lacks mandatory exit intent'

def test_locked_time_stop_liquidates_at_recovery_open():
    t = time_lock(True)
    assert t.exit_reason == 'TIME_STOP'
    assert t.exit_fills[0].raw_price == 103., 'expired holding limit waits for recovery close instead of open'

@pytest.mark.parametrize('bad', [DailyEquityPoint('2023-01-03', 250000., cash=float('nan')),
    SimpleNamespace(session='2023-01-03', equity=250000.)], ids=['nan_cash', 'missing_cash'])
def test_incomplete_cash_series_fails_closed(bad):
    curve = [DailyEquityPoint('2023-01-02', 250000., cash=250000.), bad]
    assert not compute_backtest_metrics([winner()], curve).hurdle_passed
