"""Independent acceptance probes; failures are required remediation, not fixes."""
import csv
import pytest
from antigravity.paper.paper_desk_runner import PaperDeskConfig, PaperDeskRunner
from antigravity.engine.execution_simulator import DailyBar
from antigravity.strategies.base_strategy import SignalEvent

def runner(tmp_path):
    return PaperDeskRunner(PaperDeskConfig(db_path=tmp_path/'store.db', projections_dir=tmp_path/'csv'))

def signal(symbol='CDSL', **kwargs):
    values=dict(strategy_id='HIGH52_MOMENTUM',symbol=symbol,session_date='2024-05-14',entry_session='2024-05-15',reference_price=100.,stop_loss_price=90.,target_price=120.,priority_score=10.,trace={'atr':5.},created_at='2024-05-14T16:00:00+05:30')
    values.update(kwargs)
    return SignalEvent(**values)

def reserve(r, **kwargs):
    return r.run_pre_open('2024-05-15',candidate_signals=[signal()],surveillance_snapshot={'fetched_at':'2024-05-15T08:30:00+05:30','asm_long_term':[],'asm_short_term':[],'gsm':[]},fno_underlyings={'CDSL'},**kwargs)

def bars(volume=50000, low=98., high=105., close=103.):
    return {'CDSL':DailyBar(symbol='CDSL',open=100.,high=high,low=low,close=close,volume=volume)}

def test_missing_evidence_freezes_entries(tmp_path):
    r=runner(tmp_path)
    result=r.run_pre_open('2024-05-15',candidate_signals=[signal()])
    assert not result['approved_reservations'], result

@pytest.mark.parametrize('snapshot',[{}, {'fetched_at':'garbage'}, {'fetched_at':'2024-05-15T09:00:00+05:30','gsm':[]}])
def test_malformed_or_future_evidence_freezes_entries(tmp_path,snapshot):
    r=runner(tmp_path)
    result=r.run_pre_open('2024-05-15',candidate_signals=[signal()],surveillance_snapshot=snapshot,fno_underlyings={'CDSL'})
    assert not result['approved_reservations'], result

def test_future_signal_not_backfilled_at_open(tmp_path):
    r=runner(tmp_path)
    result=r.run_pre_open('2024-05-15',candidate_signals=[signal(created_at='2024-05-15T16:00:00+05:30',session_date='2024-05-15')],surveillance_snapshot={'fetched_at':'2024-05-15T08:30:00+05:30','gsm':[]},fno_underlyings={'CDSL'})
    assert not result['approved_reservations'], result

def test_partial_entry_reservation_survives_restart(tmp_path):
    r=runner(tmp_path); reserve(r)
    r.run_post_close('2024-05-15',bars(volume=100))
    before=r.governor.pending_reservations['CDSL']['quantity']
    restored=PaperDeskRunner(r.config)
    assert restored.governor.pending_reservations.get('CDSL',{}).get('quantity') == before

def test_crash_before_fill_event_rolls_back_economics(tmp_path,monkeypatch):
    r=runner(tmp_path); reserve(r)
    original=r.store.append_event
    def crash(event):
        if event.fill_qty_delta: raise RuntimeError('INJECTED_CRASH_BEFORE_FILL_EVENT')
        return original(event)
    monkeypatch.setattr(r.store,'append_event',crash)
    with pytest.raises(RuntimeError,match='INJECTED_CRASH'):
        r.run_post_close('2024-05-15',bars())
    restored=PaperDeskRunner(r.config)
    assert not restored.store.get_open_positions(), 'position committed without fill event or cash debit'
    assert restored.store.get_consumed_volume('2024-05-15','CDSL') == 0

def test_postclose_rerun_does_not_add_economics(tmp_path):
    r=runner(tmp_path); reserve(r)
    b=bars(low=80.)
    first=r.run_post_close('2024-05-15',b)
    cash=r.governor.cash_rs
    second=r.run_post_close('2024-05-15',b)
    assert r.governor.cash_rs == cash, (first,second)
    assert not second['executed_exits']

def test_split_preserves_mtm_and_is_idempotent(tmp_path):
    r=runner(tmp_path); reserve(r); r.run_post_close('2024-05-15',bars())
    old=r.store.get_open_positions()[0]
    r.apply_corporate_action('CDSL','SPLIT',2.,'2024-05-16')
    new=r.store.get_open_positions()[0]
    assert new.residual_qty*new.last_mark == old.residual_qty*old.last_mark
    r.apply_corporate_action('CDSL','SPLIT',2.,'2024-05-16')
    assert r.store.get_open_positions()[0].residual_qty == new.residual_qty

def test_exports_carry_generation_and_actual_sequence(tmp_path):
    r=runner(tmp_path); reserve(r)
    result=r.run_post_close('2024-05-15',bars())
    for path in result['projections'].values():
        with open(path,newline='',encoding='utf-8') as f:
            reader=csv.DictReader(f)
            assert {'generation_id','last_event_seq','schema_version','track','code_commit'} <= set(reader.fieldnames)
            rows=list(reader)
            assert all(row['generation_id']==result['generation_id'] for row in rows)
    with open(result['projections']['journal'],newline='',encoding='utf-8') as f:
        seq=[int(x['event_seq']) for x in csv.DictReader(f)]
    assert seq==list(range(1,len(seq)+1))

def test_missing_manifest_does_not_commit_eod(tmp_path):
    r=runner(tmp_path); reserve(r)
    r.run_post_close('2024-05-15',bars(),bhavcopy_manifest=None)
    assert r.store.get_latest_equity() is None

def test_two_stale_runners_share_slot_gate(tmp_path):
    a=runner(tmp_path); b=PaperDeskRunner(a.config)
    surv={'fetched_at':'2024-05-15T08:30:00+05:30','gsm':[],'asm_long_term':[],'asm_short_term':[]}
    fno={'CDSL','SUZLON','RELIANCE','INFY'}
    first=a.run_pre_open('2024-05-15',candidate_signals=[signal('CDSL'),signal('SUZLON')],surveillance_snapshot=surv,fno_underlyings=fno)
    second=b.run_pre_open('2024-05-15',candidate_signals=[signal('RELIANCE'),signal('INFY')],surveillance_snapshot=surv,fno_underlyings=fno)
    assert len(first['approved_reservations']) == 2
    assert len(second['approved_reservations']) <= 1
    assert len(a.store.get_pending_reservations()) <= 3

def test_rule1_live_rejected(tmp_path):
    with pytest.raises(ValueError):
        PaperDeskRunner(PaperDeskConfig(db_path=tmp_path/'live.db',projections_dir=tmp_path/'live',allow_live_broker=True))

def test_entry_session_stop_is_processed_once(tmp_path):
    r=runner(tmp_path); reserve(r)
    result=r.run_post_close('2024-05-15',bars(low=80.))
    assert not r.store.get_open_positions(), result

def test_unknown_action_reports_unresolved_health(tmp_path):
    r=runner(tmp_path); reserve(r); r.run_post_close('2024-05-15',bars())
    r.apply_corporate_action('CDSL','MERGER',1.,'2024-05-16')
    result=r.run_post_close('2024-05-16',bars())
    assert result['equity']['unresolved_position_count'] == 1, result
    assert result['equity']['data_status'] != 'NORMAL'

def test_partial_exit_keeps_intent_at_recovery_open(tmp_path):
    r=runner(tmp_path); reserve(r); r.run_post_close('2024-05-15',bars())
    r.run_post_close('2024-05-16',bars(volume=100,low=80.))
    pos=r.store.get_open_positions()[0]
    assert pos.exit_intent is not None, pos

def test_split_replay_is_idempotent(tmp_path):
    r=runner(tmp_path); reserve(r); r.run_post_close('2024-05-15',bars())
    r.apply_corporate_action('CDSL','SPLIT',2.,'2024-05-16')
    qty=r.store.get_open_positions()[0].residual_qty
    r.apply_corporate_action('CDSL','SPLIT',2.,'2024-05-16')
    assert r.store.get_open_positions()[0].residual_qty == qty

def test_journal_projects_assigned_event_sequence(tmp_path):
    r=runner(tmp_path); reserve(r)
    result=r.run_post_close('2024-05-15',bars())
    with open(result['projections']['journal'],newline='',encoding='utf-8') as f:
        seq=[int(x['event_seq']) for x in csv.DictReader(f)]
    assert seq==list(range(1,len(seq)+1)), seq

def test_realized_net_pnl_reconciles_closed_cash(tmp_path):
    r=runner(tmp_path); reserve(r); r.run_post_close('2024-05-15',bars())
    result=r.run_post_close('2024-05-16',bars(low=80.))
    assert not r.store.get_open_positions()
    e=result['equity']
    assert e['realized_net_pnl_cumulative_rs'] == round(e['cash_ledger_rs']-r.config.initial_cash_rs,2), e
