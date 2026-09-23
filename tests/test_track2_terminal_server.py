"""
test_track2_terminal_server.py - Unit Tests for Track 2 Institutional Terminal Server
=====================================================================================
Part of Project Swing Trades (Antigravity + Claude Code + OpenAI Codex).
"""

import threading
import time
import urllib.request
import json
import tempfile
from pathlib import Path
import pytest
from antigravity.daemons.track2_terminal_server import (
    TerminalStateHandler,
    run_terminal_server,
)


@pytest.fixture(autouse=True)
def clean_paper_orders_and_state(monkeypatch):
    """Isolate tests from shared paper orders file without relying on pytest tmp_path."""
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        temp_orders = td_path / "test_paper_orders.jsonl"
        temp_events = td_path / "test_events.jsonl"
        monkeypatch.setattr("antigravity.daemons.track2_terminal_server.PAPER_ORDERS_PATH", temp_orders)
        monkeypatch.setattr("antigravity.daemons.track2_terminal_server.EVENTS_LOG_PATH", temp_events)
        monkeypatch.setattr("antigravity.daemons.hybrid_execution_oms.INTENTS_PATH", td_path / "execution_intents.json")
        monkeypatch.setattr("antigravity.daemons.hybrid_execution_oms.CONFIG_PATH", td_path / "execution_config.json")
        monkeypatch.setattr("antigravity.daemons.hybrid_execution_oms.SHARED_TRACK2_DIR", td_path)
        monkeypatch.setattr("antigravity.daemons.track2_terminal_server.TerminalHTTPRequestHandler.state_handler", TerminalStateHandler(corpus_rs=250000.0, output_dir=td_path))
        yield


def test_terminal_state_handler_calibration():
    # Test state handler calibrated for Rs 2,50,000
    handler = TerminalStateHandler(corpus_rs=250000.0)
    state = handler.get_state()

    assert state["status"] == "OK"
    assert "timestamp" in state
    assert state["nifty"]["regime"] in ("BULLISH_EXPANSION", "NEUTRAL_SELECTIVE")
    assert state["risk"]["corpus_rs"] == 250000.0
    assert state["risk"]["max_single_trade_risk_rs"] == 1500.0
    assert state["risk"]["max_aggregate_risk_rs"] == 4500.0
    assert state["risk"]["max_notional_rs"] == 200000.0
    assert state["risk"]["cash_buffer_rs"] == 50000.0
    assert state["risk"]["max_positions"] == 3

    # Verify Radar items
    assert len(state["radar"]) == 8
    first = state["radar"][0]
    assert "symbol" in first
    assert "sector" in first
    assert first["vol_mult"] > 0
    assert first["ltp"] > 0

    # Verify Brackets (empty when no active orders)
    assert len(state["brackets"]) == 0

    # Verify brackets populated when active orders exist in paper_orders.jsonl
    from antigravity.daemons.track2_terminal_server import PAPER_ORDERS_PATH
    with open(PAPER_ORDERS_PATH, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "order_id": "ORD_001",
            "symbol": "CDSL",
            "shares": 50,
            "entry_price": 1400.0,
            "status": "OPEN",
        }) + "\n")
    state_with_orders = handler.get_state()
    assert len(state_with_orders["brackets"]) == 1
    assert state_with_orders["brackets"][0]["order_id"] == "ORD_001"
    assert state_with_orders["brackets"][0]["symbol"] == "CDSL"


    # Verify Sector Heatmap
    assert len(state["sectors"]) == 4

    # Verify VIGIL Watchdog integration
    assert "vigil" in state
    assert "is_paused" in state


def test_terminal_server_http_endpoints():
    port = 8799
    server = run_terminal_server(host="127.0.0.1", port=port)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.1)

    try:
        # 1. Test GET /
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as resp:
            assert resp.status == 200
            html = resp.read().decode("utf-8")
            assert "TRACK 2" in html
            assert "PROJECT SWING TRADES" in html
            assert "ARGUS 8i" in html
            assert "NIGHTWATCH" in html

        # 2. Test GET /api/state
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state") as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "OK"
            assert "radar" in data
            assert "risk" in data
            assert "is_paused" in data

        # 3. Test GET /api/audit
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/audit") as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "OK"
            assert len(data["events"]) > 0

        # 4. Test POST /api/action/enter - Approved
        valid_payload = json.dumps({
            "symbol": "RVNL",
            "entry_price": 200.0,
            "stop_price": 190.0,
            "quantity": 150,  # 1500 Rs risk
        }).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/action/enter",
            data=valid_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            res = json.loads(resp.read().decode("utf-8"))
            assert res["status"] == "APPROVED"
            assert "RVNL" in res["message"]

        # 5. Test POST /api/action/enter - Rejected (Risk Exceeded)
        excess_payload = json.dumps({
            "symbol": "BDL",
            "entry_price": 1000.0,
            "stop_price": 950.0,
            "quantity": 40,  # 2000 Rs risk > 1500 Rs limit
        }).encode("utf-8")
        req_excess = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/action/enter",
            data=excess_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req_excess)
            pytest.fail("Expected HTTP 403 Forbidden for excessive risk")
        except urllib.error.HTTPError as e:
            assert e.code == 403
            err_data = json.loads(e.read().decode("utf-8"))
            assert "FAIL-CLOSED" in err_data["error"]

        # 6. Test POST /api/action/pause - Toggle to PAUSED
        req_pause = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/action/pause",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_pause) as resp:
            assert resp.status == 200
            pause_res = json.loads(resp.read().decode("utf-8"))
            assert pause_res["status"] == "OK"
            assert pause_res["is_paused"] is True

        # 7. Test POST /api/action/enter - Rejected because system is PAUSED
        req_enter_paused = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/action/enter",
            data=valid_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req_enter_paused)
            pytest.fail("Expected HTTP 403 Forbidden when system is paused")
        except urllib.error.HTTPError as e:
            assert e.code == 403
            err_data = json.loads(e.read().decode("utf-8"))
            assert "PAUSED" in err_data["error"]

        # 8. Test POST /api/action/pause - Resume
        with urllib.request.urlopen(req_pause) as resp:
            assert resp.status == 200
            resume_res = json.loads(resp.read().decode("utf-8"))
            assert resume_res["is_paused"] is False

        # 9. Test POST /api/action/squareoff - Emergency Flatten
        req_flatten = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/action/squareoff",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_flatten) as resp:
            assert resp.status == 200
            flatten_res = json.loads(resp.read().decode("utf-8"))
            assert flatten_res["status"] == "TRIGGERED"
            assert flatten_res["squared_off_count"] >= 0

    finally:
        server.shutdown()
        server.server_close()


def test_terminal_ui_sensory_and_ergonomic_elements():
    """Verify that all Phase 2 (Patch A) W19-W28 controls and canonical names are present in index.html."""
    from antigravity.daemons.track2_terminal_server import INDEX_HTML_PATH
    assert INDEX_HTML_PATH.exists()
    html = INDEX_HTML_PATH.read_text(encoding="utf-8")

    # W19: Audio Sound Alerts
    assert "sound-toggle-btn" in html
    assert "playBreakoutSound" in html
    assert "playFillSound" in html
    assert "playAlertSound" in html

    # W20: Keyboard Shortcuts & Hotkeys Modal
    assert "hotkeys-modal" in html
    assert "hotkeys-btn" in html
    assert "highlightCandidate" in html
    assert "row-highlight" in html

    # W21: Plain-English Tooltips
    assert "title=" in html
    assert "15M VOL MULT" in html
    assert "E3 Fills" in html

    # W22: One-Click CSV Exports
    assert "export-radar-csv-btn" in html
    assert "export-brackets-csv-btn" in html
    assert "export-audit-csv-btn" in html
    assert "downloadCSV" in html

    # W23: Colorblind Accessibility Mode
    assert "colorblind-toggle-btn" in html
    assert "colorblind-mode" in html

    # W24: Browser Tab Title Alert & Favicon
    assert "dynamic-favicon" in html
    assert "flashTabAlert" in html

    # W25: Theme Switcher (OLED vs Navy)
    assert "theme-toggle-btn" in html
    assert "theme-oled" in html

    # W26: Millisecond Timestamp Precision
    assert "ms-toggle-btn" in html
    assert "applyMsPrecision" in html

    # W27: Sticky Table Headers
    assert "sticky top-0" in html
    assert "sticky-header" in html

    # W28: Live Ping / Heartbeat Indicator
    assert "ping-badge" in html

    # Canonical Platform Nomenclature
    assert "ARGUS 8i" in html
    assert "NIGHTWATCH" in html
    assert "CALIBER" in html
    assert "BASTION" in html
    assert "SENTINEL" in html
    assert "VECTOR" in html
    assert "SPLITLOCK" in html
    assert "BLACKBOX" in html
    assert "VIGIL" in html

    # Phase 3 (Patch B): W4, W5, W10 Elements
    assert "depth-modal" in html
    assert "tca-modal" in html
    assert "openDepthModal" in html
    assert "closeDepthModal" in html
    assert "openTcaModal" in html
    assert "closeTcaModal" in html
    assert "SLIP:" in html
    assert "bps" in html
    assert "[ℹ TCA]" in html
    assert "📊 DEPTH" in html


def test_microstructure_and_tca_models():
    """Verify mathematical correctness of W4 shortfall, W5 depth & queue rank, and W10 cost decomposition."""
    from antigravity.models.track2_paper_execution import (
        calculate_implementation_shortfall,
        generate_depth_ladder,
        calculate_queue_rank,
        calculate_transaction_costs,
        aggregate_transaction_costs,
    )

    # 1. W4: Implementation Shortfall & Slippage vs Arrival Price
    shortfall_buy = calculate_implementation_shortfall(
        signal_price=100.0,
        limit_price=100.05,
        fill_price=100.10,
        side="BUY",
    )
    assert shortfall_buy["slippage_bps"] == 10.0  # +10 bps adverse slippage
    assert shortfall_buy["spread_cost_bps"] == 5.0
    assert shortfall_buy["delay_impact_bps"] == 5.0

    shortfall_sell = calculate_implementation_shortfall(
        signal_price=100.0,
        limit_price=99.95,
        fill_price=99.90,
        side="SELL",
    )
    assert shortfall_sell["slippage_bps"] == 10.0  # +10 bps adverse slippage
    assert shortfall_sell["spread_cost_bps"] == 5.0
    assert shortfall_sell["delay_impact_bps"] == 5.0

    # 2. W5: 5-Level Depth Ladder & Queue Rank
    depth = generate_depth_ladder(symbol="CDSL", ltp=1400.0, spread_ticks=1, depth_levels=5)
    assert depth["symbol"] == "CDSL"
    assert depth["ltp"] == 1400.0
    assert len(depth["bids"]) == 5
    assert len(depth["asks"]) == 5
    assert depth["spread_rs"] == 0.05
    assert depth["spread_bps"] > 0
    assert depth["total_bid_qty"] > 0
    assert depth["total_ask_qty"] > 0

    queue = calculate_queue_rank(order_qty=50, order_price=depth["bids"][0]["price"], side="BUY", depth=depth)
    assert queue["vol_ahead"] >= 0
    assert queue["queue_ratio"] >= 0
    assert queue["required_turnover_for_fill"] == queue["vol_ahead"] + 50

    # 3. W10: Explicit Transaction Cost Decomposition
    costs_buy = calculate_transaction_costs(price=1400.0, quantity=50, side="BUY", is_intraday=True)
    assert costs_buy["turnover"] == 70000.0
    assert costs_buy["brokerage"] == 20.0  # capped at 20
    assert costs_buy["stt"] == 0.0  # zero on intraday buy
    assert costs_buy["exchange_charges"] > 0
    assert costs_buy["gst"] > 0
    assert costs_buy["sebi_charges"] > 0
    assert costs_buy["stamp_duty"] > 0
    assert costs_buy["total_cost"] > 0

    costs_sell = calculate_transaction_costs(price=1445.0, quantity=25, side="SELL", is_intraday=True)
    assert costs_sell["stt"] > 0  # 0.025% on intraday sell

    agg = aggregate_transaction_costs([costs_buy, costs_sell])
    assert agg["turnover"] == 106125.0
    assert agg["total_cost"] == round(costs_buy["total_cost"] + costs_sell["total_cost"], 2)


def test_depth_api_endpoint():
    """Verify that GET /api/depth returns valid 5-level market depth and queue rank."""
    port = 8798
    server = run_terminal_server(host="127.0.0.1", port=port)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.1)

    try:
        # 1. Test GET /api/depth?symbol=CDSL
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/depth?symbol=CDSL") as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "OK"
            assert data["symbol"] == "CDSL"
            assert "depth" in data
            assert len(data["depth"]["bids"]) == 5
            assert len(data["depth"]["asks"]) == 5
            assert "queue" in data
            assert data["queue"]["required_turnover_for_fill"] > 0

        # 2. Test GET /api/depth (all books)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/depth") as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "OK"
            assert "depth_books" in data
            assert "CDSL" in data["depth_books"]
    finally:
        server.shutdown()
        server.server_close()


def test_terminal_oms_copilot_endpoints():
    """Verify that /api/state includes oms summary and /api/action/set_mode works."""
    port = 8799
    server = run_terminal_server(host="127.0.0.1", port=port)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.1)

    try:
        # 1. Test GET /api/state contains oms key
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state") as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert "oms" in data
            assert "mode" in data["oms"]
            assert "pending_co_pilot_count" in data["oms"]

        # 2. Test POST /api/action/set_mode
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/action/set_mode",
            data=json.dumps({"mode": "AUTONOMOUS"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            res = json.loads(resp.read().decode("utf-8"))
            assert res["status"] == "OK"
            assert res["mode"] == "AUTONOMOUS"

        # Verify state updated
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state") as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert data["oms"]["mode"] == "AUTONOMOUS"
    finally:
        server.shutdown()
        server.server_close()



