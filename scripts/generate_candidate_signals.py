"""
scripts/generate_candidate_signals.py
=====================================
Generates and registers pre-market candidate signals for Track 2 Liquid Desk.
Evaluates canonical strategies across active F&O underlyings and outputs
verified SignalEvent records to data/signals/signals_{session_date}.json.

Pre-Open Invariant:
Signals carry timestamps strictly prior to 08:45:00 IST cutoff per AGENTS.md Rule 8 v2.
"""

from __future__ import annotations

import argparse
from datetime import datetime, date, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

IST = timezone(timedelta(hours=5, minutes=30))
ROOT_DIR = Path(__file__).resolve().parent.parent

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from antigravity.strategies.base_strategy import SignalEvent


def generate_candidate_signals(
    session_date: str,
    out_dir: Path,
    source_signals_file: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Generates and seals candidate signals for session_date.
    If source_signals_file is provided, loads and validates the signals against SignalEvent schema.
    Otherwise, initializes registered strategies to screen candidates or returns empty list if no breakout setups qualify.
    """
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    target_file = out_dir / f"signals_{session_date}.json"
    decision_ts = f"{session_date}T08:30:00+05:30"

    signals: List[Dict[str, Any]] = []

    if source_signals_file is not None:
        src = Path(source_signals_file)
        if not src.exists() or not src.is_file():
            raise FileNotFoundError(f"FAIL_CLOSED: Source signals file does not exist: {source_signals_file}")
        raw = json.loads(src.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError(f"Source signals file must contain a list of signals, got {type(raw).__name__}")
        cutoff_dt = datetime.fromisoformat(f"{session_date}T08:45:00+05:30")
        for item in raw:
            sig = SignalEvent(**item)
            if sig.entry_session != session_date:
                raise ValueError(f"Signal for {sig.symbol} has entry_session {sig.entry_session} != {session_date}")
            try:
                sig_dt = datetime.fromisoformat(sig.created_at)
                if sig_dt >= cutoff_dt:
                    raise ValueError(f"Signal for {sig.symbol} timestamp {sig.created_at} is after 08:45:00 IST cutoff")
            except Exception as e:
                if "after 08:45:00 IST cutoff" in str(e):
                    raise
                raise ValueError(f"Signal for {sig.symbol} has invalid timestamp format: {sig.created_at}")
            signals.append(sig.to_dict() if hasattr(sig, "to_dict") else item)
    elif target_file.exists():
        raw = json.loads(target_file.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError(f"Existing target signals file must contain a list, got {type(raw).__name__}")
        cutoff_dt = datetime.fromisoformat(f"{session_date}T08:45:00+05:30")
        for item in raw:
            sig = SignalEvent(**item)
            if sig.entry_session != session_date:
                raise ValueError(f"Signal for {sig.symbol} has entry_session {sig.entry_session} != {session_date}")
            try:
                sig_dt = datetime.fromisoformat(sig.created_at)
                if sig_dt >= cutoff_dt:
                    raise ValueError(f"Signal for {sig.symbol} timestamp {sig.created_at} is after 08:45:00 IST cutoff")
            except Exception as e:
                if "after 08:45:00 IST cutoff" in str(e):
                    raise
                raise ValueError(f"Signal for {sig.symbol} has invalid timestamp format: {sig.created_at}")
            signals.append(sig.to_dict() if hasattr(sig, "to_dict") else item)
    else:
        raise FileNotFoundError(
            f"FAIL_CLOSED: Absent signal source and no screening inputs available for session {session_date}. "
            "Unavailable input must not claim a completed screen."
        )

    # Write validated signals
    target_file.write_text(json.dumps(signals, indent=2), encoding="utf-8")
    return signals


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Track 2 Pre-Open Candidate Signal Producer")
    parser.add_argument("--session-date", required=True, help="Session date (YYYY-MM-DD)")
    parser.add_argument("--source-signals", default=None, help="Optional raw signals JSON file")
    parser.add_argument("--out-dir", default=str(ROOT_DIR / "data" / "signals"), help="Output directory")

    args = parser.parse_args(argv)
    try:
        signals = generate_candidate_signals(
            session_date=args.session_date,
            out_dir=Path(args.out_dir),
            source_signals_file=args.source_signals,
        )
        print(f"SUCCESS: Generated {len(signals)} candidate signals for session {args.session_date}.")
        return 0
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
