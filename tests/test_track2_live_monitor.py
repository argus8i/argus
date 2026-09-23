import json
from datetime import datetime

from antigravity.daemons.track2_live_monitor import build_monitor_state
from antigravity.models.session_manifest import IST


NOW = datetime(2026, 9, 22, 9, 45, 10, tzinfo=IST)
SYMBOLS = {"CDSL", "ANGELONE", "SUZLON", "INOXWIND", "IREDA", "RVNL", "COCHINSHIP", "BDL"}


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_dashboard_reports_pipeline_symbols_and_errors(tmp_path):
    watchlist = [{"symbol": symbol, "ltp": 100, "change_pct": "1%"} for symbol in SYMBOLS]
    _write(tmp_path / "live_depth_track2.json", {
        "local_write_time": "2026-09-22 09:45:09", "track2_matches": sorted(SYMBOLS),
        "watchlist": watchlist,
    })
    records = {
        symbol: {"bars": [{"timestamp": "2026-09-22T09:30:00+05:30", "close": 101}]}
        for symbol in SYMBOLS | {"NIFTY50"}
    }
    _write(tmp_path / "live_candles_track2.json", {
        "local_write_time": "2026-09-22 09:45:05", "session_date": "2026-09-22",
        "symbols": records,
    })
    _write(tmp_path / "historical_candles_track2.json", {
        "local_write_time": "2026-09-22 09:44:00", "start_date": "2026-08-01",
        "end_date": "2026-09-22", "symbols": records,
    })
    _write(tmp_path / "paper_surveillance" / "nse_surveillance_snapshot_2026-09-22.json", {
        "fetched_at": "2026-09-22T08:45:00+05:30",
    })
    _write(tmp_path / "paper_desk_status.json", {
        "generated_at": "2026-09-22T09:45:08+05:30", "feed_ready": True,
        "state": "NO_SIGNAL", "official_nse_eligible_symbols": sorted(SYMBOLS),
        "decisions": [{"symbol": "CDSL", "decision": "WAIT_IN_RANGE",
                       "evaluation": {"volume_ratio": 1.2}}],
    })
    events = tmp_path / "field_tests" / "2026-09-22" / "events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_text(json.dumps({"generated_at": NOW.isoformat(), "state": "NO_SIGNAL"}) + "\n")
    _write(events.parent / "candidate_CDSL.json", {"record_type": "SIGNAL_CANDIDATE"})

    state = build_monitor_state(tmp_path, now=NOW)
    assert state["market_phase"] == "MARKET OPEN"
    assert state["event_count"] == 1
    assert state["candidate_count"] == 1
    assert len(state["symbols"]) == 8
    assert next(row for row in state["symbols"] if row["symbol"] == "CDSL")["decision"] == "WAIT_IN_RANGE"
    assert next(stage for stage in state["stages"] if stage["name"] == "Decision engine")["state"] == "OK"


def test_dashboard_makes_stale_or_missing_inputs_visible(tmp_path):
    _write(tmp_path / "live_candles_track2.json", {
        "local_write_time": "2026-09-21 15:30:00", "session_date": "2026-09-21", "symbols": {},
    })
    _write(tmp_path / "paper_desk_status.json", {
        "generated_at": "2026-09-22T09:45:08+05:30", "feed_ready": False,
        "state": "WAITING_FOR_VALID_INPUT", "reason": "current candles belong to another session",
    })
    state = build_monitor_state(tmp_path, now=NOW)
    assert state["overall"] == "ERROR"
    assert state["desk_reason"] == "current candles belong to another session"
    assert any(stage["state"] == "ERROR" for stage in state["stages"])
