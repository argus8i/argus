"""
vigil_watchdog.py - VIGIL 7-Stage Real-Time Telemetry & Health Watchdog
======================================================================
Part of Project Swing Trades // ARGUS 8i.

Mandate:
  1. Real-time health monitoring of the 7 execution telemetry stages:
     - Stage 1: Kite Chrome debugging session (port 9444 reachability).
     - Stage 2: Quotes & depth telemetry (live_depth_track2.json freshness).
     - Stage 3: Current 15-minute bar completeness (live_candles_track2.json).
     - Stage 4: Historical baseline coverage (historical_candles_track2.json).
     - Stage 5: NSE F&O / ASM / GSM / MWPL Ban surveillance pre-emption.
     - Stage 6: Decision engine readiness (paper_desk_status.json).
     - Stage 7: Evidence ledger & audit stream integrity (events.jsonl).
  2. Fail-closed alerts for stale data feeds or lost connectivity.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional
from datetime import datetime

from antigravity.daemons.track2_live_monitor import (
    TRACK2_ROOT,
    build_monitor_state,
    main as run_vigil_main,
)

# Canonical VIGIL Aliases
build_vigil_state = build_monitor_state

__all__ = [
    "TRACK2_ROOT",
    "build_monitor_state",
    "build_vigil_state",
    "run_vigil_main",
]
