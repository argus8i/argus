"""Adversarial checks for the non-counting Track 2 rehearsal runner."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from antigravity.daemons import track2_rehearsal_runner as runner
from antigravity.models.session_manifest import IST


NOW = datetime(2026, 9, 21, 9, 30, tzinfo=IST)


def _snapshot(**overrides):
    value = {
        "local_write_time": NOW.strftime("%Y-%m-%d %H:%M:%S"),
        "data_valid": True,
        "is_tab_hidden": False,
        "visibility_state": "visible",
        "watchlist": [{"symbol": symbol} for symbol in ("CDSL", "BDL", "IREDA", "SUZLON")],
    }
    value.update(overrides)
    return value


def _write(path: Path, value) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_live_snapshot_accepts_only_fresh_visible_valid_data(tmp_path):
    path = _write(tmp_path / "snapshot.json", _snapshot())
    assert len(runner.load_live_snapshot(
        path, now=NOW, expected_symbols=("CDSL", "BDL", "IREDA", "SUZLON"),
    )["watchlist"]) == 4


def test_live_snapshot_must_contain_frozen_universe(tmp_path):
    path = _write(tmp_path / "snapshot.json", _snapshot())
    with pytest.raises(ValueError, match="missing frozen symbols"):
        runner.load_live_snapshot(path, now=NOW, expected_symbols=("CDSL", "ANGELONE"))


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"data_valid": False}, "data-valid"),
        ({"is_tab_hidden": True}, "hidden"),
        ({"visibility_state": "hidden"}, "hidden"),
        ({"watchlist": [{"symbol": "CDSL"}]}, "fewer than four"),
        ({"local_write_time": (NOW - timedelta(seconds=6)).strftime("%Y-%m-%d %H:%M:%S")}, "stale"),
        ({"local_write_time": (NOW + timedelta(seconds=2)).strftime("%Y-%m-%d %H:%M:%S")}, "future-dated"),
    ],
)
def test_live_snapshot_fails_closed(tmp_path, overrides, message):
    path = _write(tmp_path / "snapshot.json", _snapshot(**overrides))
    with pytest.raises(ValueError, match=message):
        runner.load_live_snapshot(path, now=NOW)


def test_configuration_requires_four_candidates_and_engine_config(tmp_path):
    path = _write(tmp_path / "config.json", {"candidates": ["CDSL"], "engine_config": {}})
    with pytest.raises(ValueError, match="at least four"):
        runner._load_config(path)
    path = _write(tmp_path / "config.json", {"candidates": ["A", "B", "C", "D"]})
    with pytest.raises(ValueError, match="engine_config"):
        runner._load_config(path)


def test_rehearsal_paths_are_isolated_from_qualifying_evidence():
    assert runner.REHEARSAL_ROOT.name == "rehearsals"
    assert runner.REHEARSAL_SURVEILLANCE.name == "rehearsal_surveillance"
    assert runner.REHEARSAL_STATUS.parent == runner.TRACK2_ROOT
    assert runner.REHEARSAL_STATUS.parent != runner.REHEARSAL_ROOT


def test_runner_has_no_broker_order_capability():
    source = Path(runner.__file__).read_text(encoding="utf-8").lower()
    forbidden = ("place_order", "enctoken", "api.kite.trade", "requests.post", "kiteconnect")
    assert not any(token in source for token in forbidden)
