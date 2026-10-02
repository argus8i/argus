"""Round 6 independent continuation of R5 provenance and reconciliation gates."""
import csv
import hashlib
import importlib.util
from pathlib import Path
import pytest
spec = importlib.util.spec_from_file_location('r6_helpers', Path(__file__).with_name('test_codex_day5_cd0b2bf_round5.py'))
q = importlib.util.module_from_spec(spec)
spec.loader.exec_module(q)
p = q.p

@pytest.mark.parametrize('case', ['missing_source', 'header_only', 'wrong_session', 'different_close', 'negative_volume', 'inconsistent_bounds'])
def test_eod_requires_validated_source_bound_to_consumed_bars(tmp_path, case):
    r = p.runner(tmp_path)
    manifest = dict(status='NORMAL', session_date='2024-05-15')
    bars = p.bars()
    if case != 'missing_source':
        f = tmp_path/'source.csv'
        f.write_text('SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,VOLUME,DATE\n')
        if case != 'header_only':
            date = '2024-05-14' if case == 'wrong_session' else '2024-05-15'
            close = '104' if case == 'different_close' else '103'
            with f.open('a') as h:
                h.write(f'CDSL,EQ,100,105,98,{close},50000,{date}\n')
        manifest.update(source_file=str(f), source_sha256=hashlib.sha256(f.read_bytes()).hexdigest())
    if case == 'negative_volume': bars = p.bars(volume=-1)
    if case == 'inconsistent_bounds': bars = p.bars(close=999)
    r.run_post_close('2024-05-15', bars, bhavcopy_manifest=manifest)
    assert r.store.get_latest_equity() is None, 'invalid/unbound market evidence sealed EOD equity'

@pytest.mark.parametrize('name,column,value', [
    ('canonical_paper_journal.csv', 'symbol', 'FORGED'),
    ('daily_portfolio_equity.csv', 'pending_exit_count', '99'),
    ('daily_portfolio_equity.csv', 'generation_id', 'FORGED'),
])
def test_health_requires_complete_authoritative_projection(tmp_path, name, column, value):
    from scripts.verify_desk_health import verify_desk_health
    r = p.runner(tmp_path)
    p.reserve(r)
    r.run_post_close('2024-05-15', p.bars(), bhavcopy_manifest=dict(status='NORMAL', session_date='2024-05-15'))
    q.tamper_projection(r, name, column, value)
    with pytest.raises((AssertionError, ValueError)):
        verify_desk_health(r.config.db_path, '2024-05-15', r.config.projections_dir)
