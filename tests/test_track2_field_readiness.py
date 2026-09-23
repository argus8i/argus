import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from antigravity.daemons import track2_daily_paper_desk as desk
from antigravity.daemons.track2_candle_collector import parse_kite_candles
from antigravity.models.session_manifest import IST

DAY = '2026-09-22'
NOW = datetime(2026, 9, 22, 9, 45, 20, tzinfo=IST)


def bars():
    return [dict(timestamp=f'{DAY}T09:15:00+05:30', open=100, high=102, low=99, close=101, volume=1000000),
            dict(timestamp=f'{DAY}T09:30:00+05:30', open=101, high=103, low=100, close=103, volume=5000000)]


def record(requested='09:45:01'):
    return dict(bars=bars(), requested_at=f'{DAY}T{requested}+05:30', raw_sha256='a'*64)


def history(volume=1000000):
    start = datetime(2026, 8, 1, 9, 15, tzinfo=IST)
    return [dict(timestamp=(start+timedelta(days=day, minutes=15*i)).isoformat(),
                 open=100, high=104, low=98, close=102, volume=volume)
            for day in range(20) for i in range(25)]


def daily_history(volume=25_000_000):
    start = datetime(2026, 8, 1, 0, 0, tzinfo=IST)
    return [dict(timestamp=(start+timedelta(days=day)).isoformat(),
                 open=100, high=104, low=98, close=102, volume=volume)
            for day in range(20)]


def payload():
    return dict(session_date=DAY, local_write_time=f'{DAY} 09:45:05', data_valid=True,
                credential_serialized=False, symbols={s:record() for s in desk.CANDIDATES | {'NIFTY50'}})


def evaluate(data=None, hist=None, daily=None, now=NOW):
    return desk.evaluate_feed(data or payload(), historical={s:history() if hist is None else hist for s in desk.CANDIDATES},
         historical_daily={s:daily_history() if daily is None else daily for s in desk.CANDIDATES},
         order_rules=desk.ORDER_RULES, nifty_bars=bars(), eligible_symbols=desk.CANDIDATES, now=now)


def test_partial_candle_never_matures_without_new_request(tmp_path):
    data=payload()
    for item in data['symbols'].values():
        item['requested_at']=f'{DAY}T09:44:50+05:30'
    path=tmp_path/'feed.json'
    path.write_text(json.dumps(data))
    result=desk.load_current_candles(path, now=NOW)
    assert all(len(item['bars']) == 1 for item in result['symbols'].values())


def test_completed_candle_can_create_correctly_timed_candidate():
    result=evaluate()
    assert len(result['paper_signals'])==8
    signal=result['paper_signals'][0]
    assert signal['signal_timestamp']==NOW.isoformat()
    assert signal['bar_start_timestamp']==f'{DAY}T09:30:00+05:30'
    assert signal['bar_close_timestamp']==f'{DAY}T09:45:00+05:30'
    assert signal['fill_claimed'] is False


def test_insufficient_dtv_rejects_signal():
    result=evaluate(daily=daily_history(volume=1000))
    assert result['paper_signals']==[]
    assert {item['decision'] for item in result['decisions']}=={'DTV_BELOW_30_CR'}


def test_incomplete_history_cannot_be_daily_baseline():
    with pytest.raises(ValueError, match='same-bucket'):
        desk.baseline_from_history(history()[::25], daily_bars=daily_history(),
                                   decision_timestamp=f'{DAY}T09:30:00+05:30')


def test_twenty_complete_sessions_include_zero_volume_buckets():
    rows=history()
    for row in rows[:25]: row['volume']=0
    assert desk.baseline_from_history(rows, daily_bars=daily_history(),
        decision_timestamp=f'{DAY}T09:30:00+05:30')['historical_bucket_volume_median']==1000000


def test_stale_nifty_and_out_of_window_are_blocked():
    with pytest.raises(ValueError, match='Nifty.*stale'):
        evaluate(now=NOW+timedelta(minutes=20))
    assert evaluate(now=NOW+timedelta(minutes=10))['paper_signals']==[]
    assert evaluate(now=NOW.replace(hour=16))['state']=='OUTSIDE_SIGNAL_WINDOW'


def test_missing_surveillance_never_produces_candidates():
    result=desk.evaluate_feed(payload(), historical={s:history() for s in desk.CANDIDATES},
          historical_daily={s:daily_history() for s in desk.CANDIDATES},
          order_rules=desk.ORDER_RULES,nifty_bars=bars(),now=NOW)
    assert result['paper_signals']==[]


def test_stale_history_rejected(tmp_path):
    start='2026-08-01'
    symbols={}
    for index,symbol in enumerate(sorted(desk.CANDIDATES|{'NIFTY50'}),start=1):
        item=record('09:30:01')
        item.update(instrument_token=index,raw_sha256='a'*64,daily_raw_sha256='b'*64,
            source_url=f'https://kite.zerodha.com/oms/instruments/historical/{index}/15minute?from={start}&to={DAY}',
            daily_source_url=f'https://kite.zerodha.com/oms/instruments/historical/{index}/day?from={start}&to={DAY}')
        symbols[symbol]=item
    data=dict(start_date=start,end_date=DAY,interval='15minute',data_valid=True,
              credential_serialized=False,symbols=symbols)
    path=tmp_path/'history.json'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='stale'):
        desk.load_historical_candles(path,session_date=DAY,now=NOW)


@pytest.mark.parametrize('data',[[],None,42,{'data_valid':True}])
def test_malformed_history_is_handled_input_error(tmp_path,data):
    path=tmp_path/'history.json'
    path.write_text(json.dumps(data))
    with pytest.raises(desk.INPUT_ERRORS):
        desk.load_historical_candles(path,session_date=DAY,now=NOW)


@pytest.mark.parametrize('value',[True,1.9,float('inf'),-1])
def test_invalid_volume_is_rejected(value):
    raw=json.dumps({'data':{'candles':[[f'{DAY}T09:15:00+05:30',100,102,99,101,value]]}}).encode()
    with pytest.raises(ValueError): parse_kite_candles(raw,session_date=DAY)


def test_restart_reuses_verified_surveillance(tmp_path):
    meta={k:dict(source_url=url,http_status=200,fetched_at=f'{DAY}T08:45:00+05:30') for k,url in desk.EXPECTED_ENDPOINTS.items()}
    snapshot=dict(effective_session_date=DAY,parse_status='SUCCESS',fetched_at=f'{DAY}T08:45:00+05:30',
                  sources=meta,fno_underlyings=sorted(desk.CANDIDATES),asm_short_term=[],asm_long_term=[],gsm=[])
    (tmp_path/f'nse_surveillance_snapshot_{DAY}.json').write_text(json.dumps(snapshot))
    with patch.object(desk,'replay_and_verify_sources',return_value=(True,None)),patch.object(desk,'Track2OfficialSourceIngestor') as ingest:
        assert desk.fetch_eligible_symbols(session_date=DAY,surveillance_dir=tmp_path,now=NOW)==desk.CANDIDATES
        ingest.assert_not_called()
    with patch.object(desk,'replay_and_verify_sources',return_value=(False,'HASH_MISMATCH')):
        with pytest.raises(ValueError,match='HASH_MISMATCH'):
            desk.fetch_eligible_symbols(session_date=DAY,surveillance_dir=tmp_path,now=NOW)


def test_fresh_surveillance_ingest_uses_post_fetch_validation_time(tmp_path, monkeypatch):
    before = datetime(2026, 9, 22, 8, 46, 3, tzinfo=IST)
    after = before + timedelta(seconds=2)

    class Clock(datetime):
        calls = 0

        @classmethod
        def now(cls, tz=None):
            cls.calls += 1
            return before if cls.calls == 1 else after

    class Ingestor:
        def __init__(self, surveillance_dir):
            self.root = Path(surveillance_dir)

        def ingest_session(self, session_date):
            fetched = (before + timedelta(seconds=1)).isoformat()
            sources = {
                key: dict(source_url=url, http_status=200, fetched_at=fetched)
                for key, url in desk.EXPECTED_ENDPOINTS.items()
            }
            snapshot = dict(
                effective_session_date=session_date, parse_status='SUCCESS',
                fetched_at=fetched, sources=sources,
                fno_underlyings=sorted(desk.CANDIDATES),
                asm_short_term=[], asm_long_term=[], gsm=[],
            )
            (self.root / f'nse_surveillance_snapshot_{session_date}.json').write_text(json.dumps(snapshot))
            return {'verified': True}

    monkeypatch.setattr(desk, 'datetime', Clock)
    monkeypatch.setattr(desk, 'Track2OfficialSourceIngestor', Ingestor)
    monkeypatch.setattr(desk, 'replay_and_verify_sources', lambda *args, **kwargs: (True, None))
    assert desk.fetch_eligible_symbols(session_date=DAY, surveillance_dir=tmp_path) == desk.CANDIDATES


def test_candidate_dedupe_and_end_of_day_summary(tmp_path):
    signals=evaluate()['paper_signals']
    assert desk.record_candidates(tmp_path,signals)==8
    assert desk.record_candidates(tmp_path,signals)==0
    desk.append_event(tmp_path,dict(input_sha256='a'*64,state='NO_SIGNAL'))
    desk.append_event(tmp_path,dict(reason='stale feed',state='WAITING_FOR_VALID_INPUT'))
    summary=desk.close_field_test(tmp_path,'FIELD_TEST_CLOSED')
    assert summary['candidate_count']==8
    assert summary['captured_cycles']==1
    assert summary['issues']==['stale feed']
    assert summary['counts_session_gate'] is False
    assert summary['confirmed_fills']==0


def test_two_writers_cannot_own_same_field_test(tmp_path):
    first=desk.SessionWriterLock(tmp_path/'writer.lock')
    second=desk.SessionWriterLock(tmp_path/'writer.lock')
    first.acquire()
    try:
        with pytest.raises(ValueError,match='another coordinator'):
            second.acquire()
    finally: first.release()


def test_runner_captures_candidates_and_closes_without_orders(tmp_path, monkeypatch):
    class Clock(datetime):
        current = NOW

        @classmethod
        def now(cls, tz=None):
            return cls.current

    hist = dict(symbols={s:dict(bars=history(), daily_bars=daily_history(),
        raw_sha256='c'*64, daily_raw_sha256='d'*64) for s in desk.CANDIDATES})
    hist['symbols']['NIFTY50']={'bars':bars(), 'daily_bars':daily_history(),
        'raw_sha256':'e'*64, 'daily_raw_sha256':'f'*64}
    monkeypatch.setattr(desk,'datetime',Clock)
    monkeypatch.setattr(desk,'FIELD_TEST_ROOT',tmp_path/'sessions')
    monkeypatch.setattr(desk,'STATUS_PATH',tmp_path/'status.json')
    monkeypatch.setattr(desk,'fetch_eligible_symbols',lambda **kwargs: desk.CANDIDATES)
    monkeypatch.setattr(desk,'load_current_candles',lambda path: payload())
    monkeypatch.setattr(desk,'load_historical_candles',lambda *args,**kwargs: hist)
    monkeypatch.setattr(desk.time,'sleep',lambda seconds: setattr(Clock,'current',NOW.replace(hour=15,minute=30)))
    assert desk.run_status_loop()==0
    summary=json.loads((tmp_path/'sessions'/DAY/'summary.json').read_text())
    assert summary['candidate_count']==8
    assert summary['captured_cycles']==1
    assert summary['confirmed_fills']==0
    assert summary['actual_order_sent'] is False
    assert len(list((tmp_path/'sessions'/DAY/'inputs').glob('*.json')))==2
    candidate=json.loads(next((tmp_path/'sessions'/DAY).glob('candidate_*.json')).read_text())
    assert candidate['instruction']['historical_15m_raw_sha256']=='c'*64
    assert candidate['instruction']['historical_daily_raw_sha256']=='d'*64


def test_runner_survives_bad_input_and_closes_with_reason(tmp_path,monkeypatch):
    class Clock(datetime):
        current=NOW

        @classmethod
        def now(cls,tz=None): return cls.current

    monkeypatch.setattr(desk,'datetime',Clock)
    monkeypatch.setattr(desk,'FIELD_TEST_ROOT',tmp_path/'sessions')
    monkeypatch.setattr(desk,'STATUS_PATH',tmp_path/'status.json')
    monkeypatch.setattr(desk,'fetch_eligible_symbols',lambda **kwargs: desk.CANDIDATES)
    def bad_feed(path): raise TypeError('malformed row')
    monkeypatch.setattr(desk,'load_current_candles',bad_feed)
    monkeypatch.setattr(desk.time,'sleep',lambda seconds: setattr(Clock,'current',NOW.replace(hour=15,minute=30)))
    assert desk.run_status_loop()==0
    summary=json.loads((tmp_path/'sessions'/DAY/'summary.json').read_text())
    assert summary['issues']==['TypeError: malformed row']
    assert summary['candidate_count']==0
