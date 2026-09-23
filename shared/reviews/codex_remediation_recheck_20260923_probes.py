"""Read-only source audit; all simulated runtime writes use TemporaryDirectory.

Run from repository root with .venv/Scripts/python.exe. No broker connections.
Outputs observations, not a claimed production approval.
"""
import json
import logging
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from antigravity.daemons.hybrid_execution_oms import HybridExecutionOMS
from antigravity.models.execution_policy import ExecutionIntent, ExecutionMode, ExecutionEnvironment, PolicyConfig
from antigravity.models.caliber_performance_analytics import CaliberPerformanceAnalytics
from antigravity.models.track2_paper_execution import BracketOrderManager
from antigravity.models.track2_portfolio_risk_governor import PortfolioRiskGovernor
from antigravity.daemons import dhan_feed_bridge as feed
from antigravity.daemons import track2_daily_paper_desk as desk
from antigravity.daemons import track2_premarket_screener as screen
from antigravity.models.session_manifest import IST

logging.disable(logging.CRITICAL)


def candidate(symbol='CDSL', shares=100, entry=100, stop=99):
    return dict(symbol=symbol, shares=shares, entry_price=entry, stop_loss=stop)


def show(name, value):
    print(json.dumps({'probe': name, 'observed': value}, default=str))


def run():
    with tempfile.TemporaryDirectory(prefix='argus-recheck-') as td:
        root = Path(td)
        oms = HybridExecutionOMS(config=PolicyConfig(mode=ExecutionMode.AUTONOMOUS), output_dir=root)
        intent, msg = oms.submit_candidate(candidate())
        show('missing_feed_route', [intent.status.value, oms.active_orders[0]['status']])
        show('repeat_route', oms.route_order(intent)['reason'])
        restored = HybridExecutionOMS(config=PolicyConfig(), output_dir=root)
        show('restart_orders', len(restored.active_orders))
        original = oms.orders_path.read_text()
        oms.orders_path.write_text('# canonical ledger header\n'+original, encoding='utf-8')
        restored_with_header = HybridExecutionOMS(config=PolicyConfig(), output_dir=root)
        show('restart_orders_with_canonical_header', len(restored_with_header.active_orders))
        oms.orders_path.write_text(original, encoding='utf-8')
        try:
            oms.submit_candidate(candidate('ANGELONE', shares=100000))
        except Exception as exc:
            show('oversized_rejection', f'{type(exc).__name__}: {exc}')
        oms.emergency_flatten_all()
        last = json.loads(oms.orders_path.read_text().splitlines()[-1])
        show('kill_queued_order', {k: last[k] for k in ('action', 'status', 'exit_price')})
        m = CaliberPerformanceAnalytics(orders_log_path=root/'missing').load_from_orders_log()
        show('missing_analytics', [m.total_trades, m.net_pnl_rs, m.rule_1_gate.completed_sessions])
        fake = {'order_id':'same', 'status':'QUEUED', 'net_pnl_rs':100}
        path = root/'bad-pnl.jsonl'
        path.write_text(json.dumps(fake)+'\n'+json.dumps(fake)+'\n', encoding='utf-8')
        m = CaliberPerformanceAnalytics(orders_log_path=path).load_from_orders_log()
        show('queued_duplicate_pnl', [m.total_trades, m.net_pnl_rs])
        fresh = root/'pending'; fresh.mkdir()
        oms = HybridExecutionOMS(config=PolicyConfig(), output_dir=fresh)
        approved = []
        for symbol in ('CDSL','SUZLON','RVNL','BDL'):
            i, _ = oms.submit_candidate(candidate(symbol, shares=500, stop=97))
            approved.append(i)
        for i in approved:
            oms.approve_intent(i.intent_id, request_id=i.symbol, current_ltp=100)
        show('pending_reservations', {'orders':len(oms.active_orders), 'risk':sum(o['risk_rs'] for o in oms.active_orders), 'notional':sum(o['notional'] for o in oms.active_orders)})
        probe = ExecutionIntent.create_from_candidate(candidate('INOXWIND'), expiry_seconds=-1)
        probe.mark_expired()
        show('direct_expired_route', oms.route_order(probe, current_ltp=100)['status'])
        armedroot=root/'armed'; armedroot.mkdir()
        armedoms=HybridExecutionOMS(config=PolicyConfig(),output_dir=armedroot)
        i,_=armedoms.submit_candidate(candidate())
        i.arm(); i.expires_at=(datetime.now(timezone.utc)-timedelta(seconds=60)).isoformat()
        armedoms._save_intents()
        show('prearmed_sweeper', [armedoms.sweep_expired_intents(), i.status.value, i.is_expired()])

        # Fake credentials and a patched scrip-master loader ensure no network/auth.
        with patch.object(feed.DhanScripMaster, 'load_index', return_value=None):
            bridge=feed.DhanFeedBridge({'client_id':'TEST','access_token':'TEST'}, output_dir=root, symbols=['CDSL'])
        bridge.scrip_master.id_to_sym={'1':'CDSL'}
        bridge.handle_message(None, {'security_id':1,'LTP':100,'volume':1000,'open':99})
        bridge.handle_message(None, {'security_id':1,'LTP':101,'volume':1100,'open':99})
        show('two_cumulative_ticks_bar_volume', bridge.intraday_bars['CDSL'][-1]['volume'])
        bridge.last_tick_time=datetime.now(timezone.utc)-timedelta(hours=1)
        bridge.handle_message(None, {'security_id':'UNKNOWN'})
        bridge.write_live_depth()
        show('unknown_packet_refreshes_stale_quote', json.loads(bridge.live_depth_path.read_text())['data_valid'])
        bridge.write_live_candles()
        with patch.object(desk,'get_candidate_universe',return_value={'CDSL'}):
            try:
                desk.load_current_candles(bridge.live_candles_path)
                show('dhan_to_desk', 'ACCEPTED')
            except Exception as exc:
                show('dhan_to_desk', f'{type(exc).__name__}: {exc}')
        # Normalize universe only to expose the independent missing record timestamp.
        with patch.object(desk,'get_candidate_universe',return_value={'CDSL'}):
            doc=json.loads(bridge.live_candles_path.read_text())
            doc['symbols']['NIFTY50']=doc['symbols']['CDSL']
            bridge.live_candles_path.write_text(json.dumps(doc),encoding='utf-8')
            try:
                desk.load_current_candles(bridge.live_candles_path)
            except Exception as exc:
                show('dhan_timestamp_contract', f'{type(exc).__name__}: {exc}')
        with patch.object(screen,'TRACK2_DIR',root):
            screener=screen.PremarketScreener()
            surveillance=root/'paper_surveillance'; surveillance.mkdir()
            (surveillance/'nse_surveillance_snapshot_2000-01-01.json').write_text('{}',encoding='utf-8')
            show('empty_old_surveillance', sorted(screener.load_surveillance_sets()))
            c=screener.build_candidate('CDSL',set())
            show('known_symbol_still_synthetic', [c.open_price,c.prev_close,c.pre_open_vol,c.return_20d_pct])
            ranked=screener.scanner.scan_and_rank([screener.build_candidate(s,set()) for s in screen.SCRIP_METRIC_PRIORS])
            frozen=screener.scanner.freeze_universe(ranked,output_path=str(root/'universe.json'))
            show('regenerated_quarantine', {'hash': 'universe_sha256' in frozen, 'qualification_eligible':frozen.get('qualification_eligible'), 'universe_status':frozen.get('universe_status')})

    b=BracketOrderManager.create_bracket('probe','CDSL',1000,980,10)
    b=BracketOrderManager.update_bracket_quote(b,1032,tick_high=1035)
    show('quote_only_realized_target', [b.is_t1_target_filled,b.realized_pnl_gross,b.total_friction_cost])
    b=BracketOrderManager.update_bracket_quote(b,998,tick_low=995)
    show('completed_roundtrip_costs', [b.total_friction_cost,b.cost_breakdown['total_cost']])
    governor=PortfolioRiskGovernor.calibrate_for_corpus(250000)
    for rate in (None,float('nan'),'INVALID',-1):
        result=governor.assess_candidate('CDSL',100,99,100,[],var_elm_rate=rate)
        show('invalid_margin_rate', [str(rate),result.is_approved])
    try:
        PolicyConfig(environment=ExecutionEnvironment.LIVE_BROKER,enforce_rule1_lock=False).validate_for_execution()
        show('live_flag_bypass','ACCEPTED')
    except Exception as exc:
        show('live_flag_bypass',type(exc).__name__)


if __name__ == '__main__':
    run()
