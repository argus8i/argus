"""Phase 1D non-counting live-market rehearsal runner.

This runner reads the existing Track 2 browser snapshot and official NSE public
sources. It has no broker order methods and always freezes REHEARSAL mode before
collecting market observations.
"""

from __future__ import annotations

import argparse
import json
import time as wall_time
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from antigravity.daemons.track2_official_source_ingestor import Track2OfficialSourceIngestor
from antigravity.daemons.track2_session_coordinator import Track2PaperSessionCoordinator
from antigravity.daemons.track2_verdict_aggregator import build_gate_report, write_derived_report
from antigravity.models.session_manifest import IST


REPO_ROOT = Path(__file__).resolve().parents[2]
TRACK2_ROOT = REPO_ROOT / "shared" / "track2_liquid"
REHEARSAL_ROOT = TRACK2_ROOT / "rehearsals"
REHEARSAL_SURVEILLANCE = TRACK2_ROOT / "rehearsal_surveillance"
LIVE_SNAPSHOT = TRACK2_ROOT / "live_depth_track2.json"
REHEARSAL_STATUS = TRACK2_ROOT / "track2_rehearsal_status.json"
CAPTURE_START = time(9, 15)
SESSION_CLOSE = time(15, 30)
MAX_SNAPSHOT_AGE_SECONDS = 5.0


def load_live_snapshot(
    path: Path,
    *,
    now: datetime | None = None,
    expected_symbols: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Read one fresh, visible, valid Track 2 browser snapshot."""
    current = (now or datetime.now(IST)).astimezone(IST)
    if path.is_symlink() or not path.is_file():
        raise ValueError("live Track 2 snapshot is missing or unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("live Track 2 snapshot is invalid JSON") from exc
    if not isinstance(value, dict) or value.get("data_valid") is not True:
        raise ValueError("live Track 2 snapshot is not data-valid")
    if value.get("is_tab_hidden") is True or value.get("visibility_state") == "hidden":
        raise ValueError("live Track 2 tab is hidden")
    try:
        written_at = datetime.strptime(
            str(value.get("local_write_time")), "%Y-%m-%d %H:%M:%S",
        ).replace(tzinfo=IST)
    except ValueError as exc:
        raise ValueError("live Track 2 snapshot write time is invalid") from exc
    age = (current - written_at).total_seconds()
    if age < -1.0 or age > MAX_SNAPSHOT_AGE_SECONDS:
        raise ValueError("live Track 2 snapshot is stale or future-dated")
    watchlist = value.get("watchlist")
    if not isinstance(watchlist, list) or len(watchlist) < 4:
        raise ValueError("live Track 2 snapshot contains fewer than four instruments")
    if expected_symbols is not None:
        observed = {
            str(item.get("symbol", "")).strip().upper()
            for item in watchlist
            if isinstance(item, dict)
        }
        missing = set(expected_symbols).difference(observed)
        if missing:
            raise ValueError(f"live Track 2 snapshot is missing frozen symbols: {sorted(missing)}")
    return value


def _load_config(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("rehearsal configuration is missing or unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("rehearsal configuration is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("rehearsal configuration must be an object")
    candidates = value.get("candidates")
    engine_config = value.get("engine_config")
    if not isinstance(candidates, list) or len(candidates) < 4:
        raise ValueError("rehearsal configuration requires at least four candidates")
    if not isinstance(engine_config, dict):
        raise ValueError("rehearsal engine_config must be an object")
    return value


def wait_for_live_feed(*, timeout_seconds: int) -> None:
    deadline = min(
        datetime.now(IST) + timedelta(seconds=timeout_seconds),
        datetime.combine(datetime.now(IST).date(), time(8, 59, 45), tzinfo=IST),
    )
    last_error = "feed not checked"
    while datetime.now(IST) <= deadline:
        try:
            load_live_snapshot(LIVE_SNAPSHOT)
            return
        except ValueError as exc:
            last_error = str(exc)
            wall_time.sleep(1.0)
    raise RuntimeError(f"live feed was not ready before pre-open deadline: {last_error}")


def _coordinator(config: Mapping[str, Any], band_policy_path: Path) -> Track2PaperSessionCoordinator:
    return Track2PaperSessionCoordinator(
        sessions_root=REHEARSAL_ROOT,
        surveillance_dir=REHEARSAL_SURVEILLANCE,
        band_policy_path=band_policy_path,
        candidates=tuple(config["candidates"]),
        config=config["engine_config"],
        ingestor=Track2OfficialSourceIngestor(
            surveillance_dir=str(REHEARSAL_SURVEILLANCE),
        ),
        feed_health_check=lambda: bool(load_live_snapshot(LIVE_SNAPSHOT)),
        qualification_mode="REHEARSAL",
    )


def run_rehearsal(
    *,
    config_path: Path,
    band_policy_path: Path,
    resume: bool,
    wait_feed_seconds: int = 0,
) -> int:
    config = _load_config(config_path)
    if wait_feed_seconds > 0:
        wait_for_live_feed(timeout_seconds=wait_feed_seconds)
    coordinator = _coordinator(config, band_policy_path)
    session_date = datetime.now(IST).date().isoformat()
    result = coordinator.resume(session_date) if resume else coordinator.prepare(session_date)
    if result.state.value != "RECORDING":
        raise RuntimeError(f"rehearsal could not start: {result.reason}")

    try:
        while datetime.now(IST).time() < CAPTURE_START:
            wall_time.sleep(1.0)
        while datetime.now(IST).time() <= SESSION_CLOSE:
            snapshot = load_live_snapshot(
                LIVE_SNAPSHOT, expected_symbols=result.eligible_symbols,
            )
            if coordinator.record_snapshot(snapshot) is not True:
                raise RuntimeError("rehearsal recorder rejected the live snapshot")
            wall_time.sleep(1.0)
        verdict = coordinator.finalize()
    except KeyboardInterrupt:
        if coordinator.state.value == "RECORDING":
            coordinator.suspend_for_restart()
        return 130
    except (OSError, RuntimeError, ValueError):
        if coordinator.state.value == "RECORDING":
            coordinator.suspend_for_restart()
        raise

    report = build_gate_report(
        REHEARSAL_ROOT, expected_qualification_mode="REHEARSAL",
    )
    write_derived_report(report, REHEARSAL_STATUS, sessions_root=REHEARSAL_ROOT)
    if verdict.status.value != "REHEARSAL" or not report.trusted:
        return 2
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a non-counting Track 2 rehearsal")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--band-policy", required=True, type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--wait-feed-seconds", type=int, default=0)
    arguments = parser.parse_args(argv)
    return run_rehearsal(
        config_path=arguments.config,
        band_policy_path=arguments.band_policy,
        resume=arguments.resume,
        wait_feed_seconds=arguments.wait_feed_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
