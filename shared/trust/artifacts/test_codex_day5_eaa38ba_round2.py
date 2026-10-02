import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('previous_probes', Path(__file__).with_name('test_codex_day5_48cb886_review.py'))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

def test_no_caller_name_eligibility_bypass(tmp_path):
    def test_two_stale_runners_share_slot_gate():
        r = p.runner(tmp_path)
        return r.run_pre_open('2024-05-15', candidate_signals=[p.signal()])
    assert not test_two_stale_runners_share_slot_gate()['approved_reservations']

def test_fixed_0845_cutoff_cannot_be_overridden(tmp_path):
    r = p.runner(tmp_path)
    result = r.run_pre_open('2024-05-15', decision_time='16:30:00', candidate_signals=[p.signal(created_at='2024-05-15T16:00:00+05:30')], surveillance_snapshot={'fetched_at':'2024-05-15T16:00:00+05:30','gsm':[]}, fno_underlyings={'CDSL'})
    assert not result['approved_reservations']

def test_unverified_manifest_does_not_commit_equity(tmp_path):
    r = p.runner(tmp_path)
    r.run_post_close('2024-05-15', {}, bhavcopy_manifest={'status':'NORMAL','session_date':'1999-01-01'})
    assert r.store.get_latest_equity() is None

def test_crash_after_fill_event_before_economics_is_atomic(tmp_path, monkeypatch):
    r = p.runner(tmp_path)
    p.reserve(r)
    def crash(*args, **kwargs):
        raise RuntimeError('CRASH_BEFORE_TRANSITION')
    monkeypatch.setattr(r.store, 'commit_execution_transition', crash)
    with pytest.raises(RuntimeError):
        r.run_post_close('2024-05-15', p.bars())
    restored = p.PaperDeskRunner(r.config)
    assert restored.governor.cash_rs == r.config.initial_cash_rs
    assert not restored.store.get_open_positions()

def test_crash_before_exit_event_preserves_open_economics(tmp_path, monkeypatch):
    r = p.runner(tmp_path)
    p.reserve(r)
    r.run_post_close('2024-05-15', p.bars())
    old_cash = r.governor.cash_rs
    old_qty = r.store.get_open_positions()[0].residual_qty
    original = r.store.append_event
    def crash(event):
        if event.fill_qty_delta and event.side == 'SELL':
            raise RuntimeError('CRASH_EXIT')
        return original(event)
    monkeypatch.setattr(r.store, 'append_event', crash)
    with pytest.raises(RuntimeError):
        r.run_post_close('2024-05-16', p.bars(low=80.))
    restored = p.PaperDeskRunner(r.config)
    assert restored.governor.cash_rs == old_cash
    assert restored.store.get_open_positions()[0].residual_qty == old_qty
