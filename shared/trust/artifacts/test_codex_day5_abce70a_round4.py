import importlib.util
from pathlib import Path
import json
import csv
import hashlib
import pytest
spec=importlib.util.spec_from_file_location('p',Path(__file__).with_name('test_codex_day5_48cb886_review.py'))
p=importlib.util.module_from_spec(spec); spec.loader.exec_module(p)

@pytest.mark.parametrize('extra',[{'source_sha256':None},{'source_sha256':'0'*64},{'source_file':'does-not-exist.csv'}])
def test_unverified_provenance_cannot_seal_equity(tmp_path,extra):
    r=p.runner(tmp_path)
    r.run_post_close('2024-05-15',{},bhavcopy_manifest={'status':'NORMAL','session_date':'2024-05-15',**extra})
    assert r.store.get_latest_equity() is None

def test_pending_retry_still_requires_verified_data(tmp_path):
    r=p.runner(tmp_path)
    r.run_post_close('2024-05-15',{},bhavcopy_manifest=None)
    r.run_post_close('2024-05-15',{},bhavcopy_manifest={'status':'NORMAL','session_date':'2024-05-15'})
    assert r.store.get_latest_equity() is None

@pytest.mark.parametrize('payload',[{'gsm':[]},{'asm_long_term':[],'asm_short_term':[],'gsm':'INFY'}])
def test_surveillance_requires_complete_typed_lists(tmp_path,payload):
    from scripts.ingest_daily_regulatory_data import parse_surveillance_source
    f=tmp_path/'surv.json'; f.write_text(json.dumps(payload))
    with pytest.raises((ValueError,KeyError,TypeError)):
        parse_surveillance_source(f)

def test_missing_signal_input_is_an_error(tmp_path):
    from scripts.generate_candidate_signals import generate_candidate_signals
    with pytest.raises((FileNotFoundError,ValueError)):
        generate_candidate_signals('2024-05-15',tmp_path/'signals',str(tmp_path/'missing.json'))

def test_bhavcopy_requires_session_and_ohlcv_schema(tmp_path):
    from scripts.ingest_daily_bhavcopy import ingest_daily_bhavcopy
    f=tmp_path/'raw.csv'; f.write_text('SYMBOL,DATE\nINFY,2024-05-14\n')
    with pytest.raises((ValueError,KeyError)):
        ingest_daily_bhavcopy('2024-05-15',tmp_path/'out',str(f))

def test_health_detects_changed_csv_values_even_if_manifest_resealed(tmp_path):
    from scripts.verify_desk_health import verify_desk_health
    r=p.runner(tmp_path)
    r.run_post_close('2024-05-15',p.bars(),bhavcopy_manifest={'status':'NORMAL','session_date':'2024-05-15'})
    d=r.config.projections_dir
    f=d/'daily_portfolio_equity.csv'
    with f.open(newline='',encoding='utf-8') as handle:
        reader=csv.DictReader(handle); fields=reader.fieldnames; rows=list(reader)
    rows[0]['cash_ledger_rs']='999999999'
    with f.open('w',newline='',encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    mf=d/'generation_manifest.json'; manifest=json.loads(mf.read_text())
    for item in manifest['files'].values():
        if item['name']==f.name: item['sha256']=hashlib.sha256(f.read_bytes()).hexdigest()
    mf.write_text(json.dumps(manifest))
    with pytest.raises(AssertionError):
        verify_desk_health(r.config.db_path,'2024-05-15',d)
