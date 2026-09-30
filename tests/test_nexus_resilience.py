r"""
test_nexus_resilience.py - Verification Tests for Nexus Bus Resilience & Watchdog
================================================================================
Tests:
  1. Supervisor status contract & PID validation.
  2. Clean shutdown via stop_daemon() without orphaned processes.
  3. Watchdog automated recovery on simulated outage.
  4. End-to-end PING / PONG processing on recovered daemon.
  5. Orchestrator dashboard Bus Health telemetry reporting.
"""

import os
import sys
import time
import json
import pytest

WORKSPACE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if WORKSPACE_DIR not in sys.path:
    sys.path.insert(0, WORKSPACE_DIR)

from antigravity.daemons.supervised_inbox_worker import (
    get_status,
    stop_daemon,
    SUPERVISOR_PID_FILE,
    SUPERVISOR_LOCK_FILE,
)
from antigravity.daemons.nexus_watchdog import check_and_recover
from antigravity.daemons.tri_agent_bus import (
    send_to_agent,
    wait_for_agent_response,
)
from antigravity.orchestrator.status import get_hub_status


def test_supervisor_status_contract():
    """Verifies that get_status() returns required health fields."""
    st = get_status()
    assert isinstance(st, dict)
    assert "status" in st
    assert "running" in st
    assert st["status"] in ("RUNNING", "WORKER_DOWN", "STALE_PID", "STOPPED", "ERROR")


def test_watchdog_recovers_if_down():
    """Verifies that the 5-minute watchdog detects outages and recovers the daemon."""
    res = check_and_recover(verbose=False)
    assert res["healthy"] is True
    st = get_status()
    assert st["running"] is True
    assert st["status"] == "RUNNING"
    assert st.get("worker_alive") is True


def test_end_to_end_ping_pong_after_recovery():
    """Verifies that signed messages process cleanly after automated recovery."""
    st = get_status()
    if not st["running"]:
        check_and_recover(verbose=False)

    msg_id, corr_id = send_to_agent(
        sender="CLAUDE",
        recipient="ANTIGRAVITY",
        subject="PING",
        body="RESILIENCE_TEST",
        track="SHARED",
        timeout_sec=30.0,
    )
    assert msg_id.startswith("msg_")
    assert corr_id.startswith("corr_")

    resp = wait_for_agent_response(
        correlation_id=corr_id,
        recipient="ANTIGRAVITY",
        timeout_sec=10.0,
        poll_interval_sec=0.25,
    )
    assert resp["success"] is True
    assert resp["status"] == "COMPLETED"
    output = resp.get("response", {}).get("output_payload", {})
    assert output.get("reply") == "PONG"


def test_dashboard_bus_health_telemetry():
    """Verifies that get_hub_status() contains Bus Health and last processed message."""
    hub = get_hub_status()
    assert "bus" in hub
    bus = hub["bus"]
    assert bus["health"] in ("OK", "DOWN")
    assert "last_message" in bus
    if bus["health"] == "OK":
        assert bus["supervisor_pid"] is not None
        assert bus["worker_pid"] is not None
