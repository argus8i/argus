"""P7.4 holdout runner: refusals and the pre-registered decision rule (the full run is exercised for real)."""
from __future__ import annotations

import json

import pytest

from research.studies import prereg_io
from research.studies import run_holdout as rh

SPEC = prereg_io.load(prereg_io.PREREG_DIR / "resid_rev_v1.yaml")


@pytest.mark.parametrize("t,mean,ok", [(2.0, 0.01, True), (2.5, 0.10, True), (1.99, 0.10, False),
                                       (3.0, 0.0, False), (3.0, -0.01, False), (None, 0.1, False),
                                       (float("nan"), 0.1, False)])
def test_primary_test_is_the_preregistered_rule(t, mean, ok):
    passed, reasons = rh.primary_test({"t_cluster": t, "mean_net_r": mean, "signals": 100, "signal_days": 80}, SPEC)
    assert passed is ok and ("PASS" if ok else "FAIL") in reasons[0]


def test_nothing_runs_without_a_valid_lock(tmp_path):
    y = tmp_path / "resid_rev_v1.yaml"
    y.write_bytes((prereg_io.PREREG_DIR / "resid_rev_v1.yaml").read_bytes())
    with pytest.raises(rh.HoldoutRefused, match="lock does not verify"):
        rh.preflight(y)


def test_lock_is_refused_for_a_draft(tmp_path):
    y = tmp_path / "resid_rev_v1.yaml"
    y.write_bytes((prereg_io.PREREG_DIR / "resid_rev_v1.yaml").read_bytes().replace(b"status: LOCKED", b"status: DRAFT"))
    with pytest.raises(rh.HoldoutRefused, match="not LOCKED"):
        rh.write_lock(y)


def test_snapshot_comparison_ignores_only_reference_tables(tmp_path):
    def seal(p, files):
        p.mkdir()
        (p / "SNAPSHOT.json").write_text(json.dumps({"files": {k: {"sha256": v, "bytes": 1} for k, v in files.items()}}))
    a, b = tmp_path / "a", tmp_path / "b"
    seal(a, {"bars_15m/X.parquet": "1", "reference/universe_daily.parquet": "u1", "events/fo_ban.parquet": "e"})
    seal(b, {"bars_15m/X.parquet": "1", "reference/universe_daily.parquet": "u2", "events/fo_ban.parquet": "e"})
    assert rh._snapshot_files(a) == rh._snapshot_files(b)
    c = tmp_path / "c"
    seal(c, {"bars_15m/X.parquet": "2", "reference/universe_daily.parquet": "u1", "events/fo_ban.parquet": "e"})
    assert rh._snapshot_files(a) != rh._snapshot_files(c)


def test_code_state_identifies_code_by_blob_hashes():
    st = rh.code_state()
    assert len(st["identity"]) == 64 and st["files"] > 50 and len(st["head"]) == 40
    assert rh.code_state()["identity"] == st["identity"]          # stable
