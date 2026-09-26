"""
Regression tests for research/studies/event_holdout.py (Codex pre-lock review, 26 Sep 2026, items 2, 3, 6):
the runner refuses a wrong or tampered snapshot, a YAML changed after its lock, dirty code, a second run, and a
YAML whose parameters differ from what the runner applies; undefined statistics (fewer than 2 clusters) fail.
Tiny sealed snapshots are built in a temporary folder with research.data.snapshot.seal.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from research.data import snapshot
from research.studies import ban_entry as be
from research.studies import event_holdout as eh
from research.studies import expiry_relief as er
from research.studies import prereg_io
from research.studies.run_holdout import HoldoutRefused

REAL = prereg_io.PREREG_DIR / "ban_entry_short_v1.yaml"
DESIGN_PIN = ("p7_design_20260926", "72a444d7aec15d979b54a36e54afb8108df4b1cf1a0105ef76cb63a7c320059d")
HOLD_PIN = ("p7_holdout_20260926", "6040e0cf40909afefcf03da1fed00a10478ab98f0769ff07c95d08de9b9ece11")


def _snap(root: Path, name: str, daily: bytes, universe: bytes) -> str:
    p = root / name
    (p / "daily").mkdir(parents=True)
    (p / "reference").mkdir()
    (p / "daily" / "AAA.parquet").write_bytes(daily)
    (p / "reference" / "universe_daily.parquet").write_bytes(universe)
    return snapshot.seal(p)["content_sha256"]


def _relock(spec: Path) -> None:
    lock = {"id": "BAN_ENTRY_SHORT_v1", "yaml_sha256": prereg_io.normalised_sha256(spec), "commit": "a" * 40,
            "locked_at": "2026-09-26T14:00:00+05:30"}
    spec.with_suffix(".lock").write_text(json.dumps(lock), encoding="utf-8")


@pytest.fixture()
def world(tmp_path, monkeypatch):
    root = tmp_path / "_snapshots"
    d_sha = _snap(root, "d_snap", b"daily-bytes", b"universe-design")
    h_sha = _snap(root, "h_snap", b"daily-bytes", b"universe-holdout")
    text = REAL.read_text(encoding="utf-8").replace("status: DRAFT", "status: LOCKED")
    text = text.replace(DESIGN_PIN[0], "d_snap").replace(DESIGN_PIN[1], d_sha)
    text = text.replace(HOLD_PIN[0], "h_snap").replace(HOLD_PIN[1], h_sha)
    spec = tmp_path / "ban_entry_short_v1.yaml"
    spec.write_text(text, encoding="utf-8")
    _relock(spec)
    monkeypatch.setattr(eh, "spec_path", lambda s: spec)
    monkeypatch.setattr(eh, "code_state", lambda: {"identity": "c" * 64, "dirty": [], "head": "b" * 40, "files": 1})
    return {"root": root, "spec": spec, "tmp": tmp_path}


def test_preflight_accepts_the_pinned_snapshots(world):
    pre = eh.preflight("ban_entry_short_v1", snapshots=world["root"])
    assert pre["holdout_snapshot"]["path"].name == "h_snap"


def test_a_yaml_edited_after_its_lock_is_refused(world):
    _snap(world["root"], "h_other", b"daily-bytes", b"universe-other")
    text = world["spec"].read_text(encoding="utf-8")
    world["spec"].write_text(text.replace("name: h_snap", "name: h_other"), encoding="utf-8")
    with pytest.raises(HoldoutRefused, match="lock does not verify"):
        eh.preflight("ban_entry_short_v1", snapshots=world["root"])


def test_a_snapshot_whose_hash_is_not_the_pinned_one_is_refused(world):
    _snap(world["root"], "h_other", b"daily-bytes", b"universe-other")
    text = world["spec"].read_text(encoding="utf-8")
    world["spec"].write_text(text.replace("name: h_snap", "name: h_other"), encoding="utf-8")   # pin keeps h_snap's hash
    _relock(world["spec"])
    with pytest.raises(HoldoutRefused, match="is not the pinned"):
        eh.preflight("ban_entry_short_v1", snapshots=world["root"])


def test_a_tampered_snapshot_is_refused(world):
    f = world["root"] / "h_snap" / "reference" / "universe_daily.parquet"
    f.chmod(0o666)
    f.write_bytes(b"tampered")
    with pytest.raises(snapshot.SnapshotTamperedError):
        eh.preflight("ban_entry_short_v1", snapshots=world["root"])


def test_holdout_data_that_differ_from_the_design_are_refused(world):
    root2 = world["tmp"] / "_snapshots2"
    d_sha = _snap(root2, "d_snap", b"daily-bytes", b"u")
    h_sha = _snap(root2, "h_snap", b"DIFFERENT-daily", b"u2")
    spec = prereg_io.load(world["spec"])
    text = world["spec"].read_text(encoding="utf-8")
    text = text.replace(spec["data"]["snapshot"]["content_sha256"], d_sha)
    text = text.replace(spec["data"]["holdout_snapshot"]["content_sha256"], h_sha)
    world["spec"].write_text(text, encoding="utf-8")
    _relock(world["spec"])
    with pytest.raises(HoldoutRefused, match="differ from the design"):
        eh.preflight("ban_entry_short_v1", snapshots=root2)


def test_dirty_code_is_refused(world, monkeypatch):
    monkeypatch.setattr(eh, "code_state", lambda: {"identity": "c" * 64, "dirty": ["?? research/x.py"],
                                                   "head": "b" * 40, "files": 1})
    with pytest.raises(HoldoutRefused, match="uncommitted research code"):
        eh.preflight("ban_entry_short_v1", snapshots=world["root"])


def test_a_second_run_after_done_is_refused(world):
    world["spec"].with_suffix(".holdout_done").write_text("{}", encoding="utf-8")
    with pytest.raises(HoldoutRefused, match="holdout_done"):
        eh.preflight("ban_entry_short_v1", snapshots=world["root"])


def test_started_marker_binds_lock_code_and_snapshot(world):
    pre = eh.preflight("ban_entry_short_v1", snapshots=world["root"])
    eh._started("ban_entry_short_v1", pre)
    eh._started("ban_entry_short_v1", pre)                                     # same state: fine
    with pytest.raises(HoldoutRefused, match="different lock/code/snapshot"):
        eh._started("ban_entry_short_v1", dict(pre, code_identity="d" * 64))


def test_a_yaml_that_differs_from_the_runner_is_refused(world):
    text = world["spec"].read_text(encoding="utf-8").replace("sl_trigger_pct_above_entry_ref: 3.0",
                                                             "sl_trigger_pct_above_entry_ref: 2.0")
    world["spec"].write_text(text, encoding="utf-8")
    _relock(world["spec"])
    assert eh.spec_mismatches("ban_entry_short_v1", prereg_io.load(world["spec"]))
    with pytest.raises(HoldoutRefused, match="does not match the runner"):
        eh.preflight("ban_entry_short_v1", snapshots=world["root"])


def test_the_real_yamls_match_the_runner():
    for study in eh.STUDIES:
        assert eh.spec_mismatches(study, prereg_io.load(prereg_io.PREREG_DIR / f"{study}.yaml")) == []


def _ban_row(day: str, r: float) -> dict:
    return {"symbol": "AAA", "day": day, "disposition": "FILLED", "net_r": r, "gross_r": r, "fee_r": 0.0,
            "slip_r": 0.0, "net_pnl_rs": r * 1000, "exit_reason": "TIME_1505", "band_touch": False,
            "pre_entry_stop_touch": False}


def test_fewer_than_two_clusters_fails_the_primary():
    s = be.summarise([_ban_row("2025-01-02", 0.5), _ban_row("2025-01-02", 0.7), _ban_row("2025-01-02", 0.6)])
    assert s["t_cluster"] is None
    res = {"primary": {"summary": s}, "slippage_2_ticks": {"summary": {"mean_net_r": 0.5}}}
    assert eh.decide("ban_entry_short_v1", res, 2.0)[0] is False
    sim = pd.DataFrame({"expiry": ["2025-01-30"] * 3, "disposition": ["FILLED"] * 3, "net_r": [0.5, 0.6, 0.7],
                        "gross_r": 0.6, "fee_r": 0.0, "slip_r": 0.0, "net_pnl_rs": 600.0, "exit_reason": "TIME"})
    assert er.summarise(sim)["t_cluster"] is None
