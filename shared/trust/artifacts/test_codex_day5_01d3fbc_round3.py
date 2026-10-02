import importlib.util
from pathlib import Path
import json
import pytest
spec=importlib.util.spec_from_file_location('p',Path(__file__).with_name('test_codex_day5_48cb886_review.py'))
p=importlib.util.module_from_spec(spec); spec.loader.exec_module(p)

def test_same_day_exit_crash_cannot_credit_cash_with_open_inventory(tmp_path,monkeypatch):
    r=p.runner(tmp_path); p.reserve(r)
    original=r.store.append_event
    def crash(event):
        if event.event_type=='POSITION_CLOSED' and event.side=='SELL':
            raise RuntimeError('CRASH_AFTER_SAMEDAY_SELL')
        return original(event)
    monkeypatch.setattr(r.store,'append_event',crash)
    with pytest.raises(RuntimeError,match='CRASH_AFTER_SAMEDAY_SELL'):
        r.run_post_close('2024-05-15',p.bars(low=80.))
    restored=p.PaperDeskRunner(r.config)
    with restored.store._get_connection() as conn:
        sold=conn.execute("SELECT COALESCE(SUM(fill_qty_delta),0) FROM ledger_events WHERE side='SELL'").fetchone()[0]
    assert sold==0 or not restored.store.get_open_positions(), 'sell cash credited while sold inventory remains OPEN'

def test_invalid_surveillance_schema_is_rejected(tmp_path):
    from scripts.ingest_daily_regulatory_data import ingest_daily_regulatory_data
    surv=tmp_path/'surv.json'; surv.write_text('{}')
    fno=tmp_path/'fno.json'; fno.write_text('["CDSL"]')
    with pytest.raises((ValueError,KeyError)):
        ingest_daily_regulatory_data('2024-05-15',tmp_path/'surveillance',tmp_path/'fno',str(surv),str(fno))

def test_matching_labels_alone_do_not_verify_manifest(tmp_path):
    r=p.runner(tmp_path)
    r.run_post_close('2024-05-15',{},bhavcopy_manifest={'status':'NORMAL','session_date':'2024-05-15'})
    assert r.store.get_latest_equity() is None, 'manifest without source/hash accepted as verified'

def test_missing_manifest_session_can_be_retried_with_verified_data(tmp_path):
    r=p.runner(tmp_path)
    r.run_post_close('2024-05-15',{},bhavcopy_manifest=None)
    r.run_post_close('2024-05-15',{},bhavcopy_manifest={'status':'NORMAL','session_date':'2024-05-15'})
    assert r.store.get_latest_equity() is not None, 'missing-data run irreversibly seals session'

def test_wrapped_append_event_does_not_escape_atomic_transaction(tmp_path,monkeypatch):
    r=p.runner(tmp_path); p.reserve(r)
    original=r.store.append_event
    def wrapper(event):
        return original(event)
    monkeypatch.setattr(r.store,'append_event',wrapper)
    with r.store._get_connection() as conn:
        conn.execute("CREATE TRIGGER fail_position BEFORE INSERT ON positions BEGIN SELECT RAISE(ABORT, 'INJECTED_POSITION_FAILURE'); END;")
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError,match='INJECTED_POSITION_FAILURE'):
        r.run_post_close('2024-05-15',p.bars())
    restored=p.PaperDeskRunner(r.config)
    assert restored.governor.cash_rs==r.config.initial_cash_rs, 'wrapped event committed outside economics transaction'
