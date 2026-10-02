"""Independent Round 5 acceptance probes. Review only."""
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('r5_helpers', Path(__file__).with_name('test_codex_day5_48cb886_review.py'))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

def test_absent_signal_source_must_not_claim_completed_screen(tmp_path):
    from scripts.generate_candidate_signals import generate_candidate_signals
    with pytest.raises((FileNotFoundError, ValueError)):
        generate_candidate_signals('2024-05-15', tmp_path/'signals')

@pytest.mark.parametrize('case', ['undated', 'nan', 'inconsistent', 'empty_series'])
def test_bhavcopy_rejects_unverifiable_or_invalid_rows(tmp_path, case):
    from scripts.ingest_daily_bhavcopy import ingest_daily_bhavcopy
    row = dict(SYMBOL='CDSL', SERIES='EQ', OPEN='100', HIGH='105', LOW='99', CLOSE='103', VOLUME='50000', DATE='2024-05-15')
    if case == 'undated': del row['DATE']
    if case == 'nan': row['CLOSE'] = 'nan'
    if case == 'inconsistent': row['CLOSE'] = '999'
    if case == 'empty_series': row['SERIES'] = ''
    f = tmp_path/'raw.csv'
    with f.open('w', newline='') as h:
        w = csv.DictWriter(h, fieldnames=list(row)); w.writeheader(); w.writerow(row)
    with pytest.raises((ValueError, KeyError)):
        ingest_daily_bhavcopy('2024-05-15', tmp_path/'out', str(f))

def test_arbitrary_existing_file_cannot_verify_empty_market_data(tmp_path):
    r = p.runner(tmp_path)
    f = tmp_path/'not_market_data.txt'; f.write_text('unrelated bytes')
    r.run_post_close('2024-05-15', {}, bhavcopy_manifest=dict(status='NORMAL', session_date='2024-05-15', source_file=str(f), source_sha256=hashlib.sha256(f.read_bytes()).hexdigest()))
    assert r.store.get_latest_equity() is None

def tamper_projection(r, name, column, value):
    d = r.config.projections_dir; f = d/name
    with f.open(newline='', encoding='utf-8') as h:
        reader = csv.DictReader(h); fields = reader.fieldnames; rows = list(reader)
    assert rows, 'probe requires nonempty projection'
    rows[0][column] = value
    with f.open('w', newline='', encoding='utf-8') as h:
        w = csv.DictWriter(h, fieldnames=fields); w.writeheader(); w.writerows(rows)
    mf = d/'generation_manifest.json'; m = json.loads(mf.read_text())
    for item in m['files'].values():
        if item['name'] == name: item['sha256'] = hashlib.sha256(f.read_bytes()).hexdigest()
    mf.write_text(json.dumps(m))

@pytest.mark.parametrize('name,column,value', [
    ('daily_portfolio_equity.csv', 'occupied_slots', '3'),
    ('daily_portfolio_equity.csv', 'cash_ledger_rs', 'nan'),
    ('canonical_paper_journal.csv', 'event_seq', '999999'),
])
def test_health_reconciles_authoritative_values(tmp_path, name, column, value):
    from scripts.verify_desk_health import verify_desk_health
    r = p.runner(tmp_path)
    p.reserve(r)
    r.run_post_close('2024-05-15', p.bars(), bhavcopy_manifest=dict(status='NORMAL', session_date='2024-05-15'))
    tamper_projection(r, name, column, value)
    with pytest.raises((AssertionError, ValueError)):
        verify_desk_health(r.config.db_path, '2024-05-15', r.config.projections_dir)
