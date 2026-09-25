"""Sealed history snapshots: copy, seal, verify, and refusal on a concurrent writer or any later change."""
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import pytest

from research.data import snapshot as snap


def _history(tmp_path: Path) -> Path:
    h = tmp_path / "history"
    for d in snap.COPY_DIRS:
        (h / d).mkdir(parents=True)
        (h / d / f"{d}.bin").write_bytes(d.encode() * 10)
    for f in snap.COPY_FILES:
        (h / f).parent.mkdir(parents=True, exist_ok=True)
        (h / f).write_bytes(f.encode())
    (h / "raw" / "upstox" / "big_raw_file.json.gz").write_bytes(b"not copied")
    return h


def _writable(p: Path) -> None:
    os.chmod(p, stat.S_IREAD | stat.S_IWRITE)


def test_create_seal_verify_round_trip(tmp_path):
    h = _history(tmp_path)
    dest = snap.create("t1", h)
    assert (dest / "bars_15m" / "bars_15m.bin").read_bytes() == (h / "bars_15m" / "bars_15m.bin").read_bytes()
    assert not (dest / "raw" / "upstox" / "big_raw_file.json.gz").exists()      # raw bodies stay behind
    info = snap.seal(dest)
    assert info["n_files"] == len(snap.COPY_DIRS) + len(snap.COPY_FILES) + 1    # + CREATED.json
    v = snap.verify(dest)
    assert v["content_sha256"] == info["content_sha256"]
    assert snap.describe(dest)["kind"] == "SEALED_SNAPSHOT"
    assert snap.describe(h)["kind"] == "LIVE_HISTORY_NOT_SNAPSHOT"
    assert not os.access(dest / "events" / "events.bin", os.W_OK)                # read-only after sealing


def test_changed_extra_and_missing_files_fail_verification(tmp_path):
    h = _history(tmp_path)
    dest = snap.create("t2", h)
    snap.seal(dest)
    f = dest / "daily" / "daily.bin"
    _writable(f)
    f.write_bytes(b"edited")
    with pytest.raises(snap.SnapshotTamperedError, match="changed"):
        snap.verify(dest)

    dest3 = snap.create("t3", h)
    snap.seal(dest3)
    (dest3 / "reference" / "new.parquet").write_bytes(b"x")
    with pytest.raises(snap.SnapshotTamperedError, match="extra"):
        snap.verify(dest3)


def test_concurrent_writer_during_copy_aborts_and_keeps_nothing(tmp_path, monkeypatch):
    h = _history(tmp_path)
    real_copy = shutil.copy2

    def copy_while_someone_writes(src, dst, *a, **k):
        out = real_copy(src, dst, *a, **k)
        if Path(src).name == "bars_15m.bin":
            Path(src).write_bytes(b"another agent rewrote this file")
        return out

    monkeypatch.setattr(snap.shutil, "copy2", copy_while_someone_writes)
    with pytest.raises(snap.SnapshotRaceError):
        snap.create("t4", h)
    assert not (snap.snapshots_root(h) / "t4").exists()
    assert not (snap.snapshots_root(h) / ".t4.partial").exists()


def test_never_overwrites_and_never_seals_twice(tmp_path):
    h = _history(tmp_path)
    dest = snap.create("t5", h)
    with pytest.raises(snap.SnapshotError, match="never overwritten"):
        snap.create("t5", h)
    snap.seal(dest)
    with pytest.raises(snap.SnapshotError, match="already sealed"):
        snap.seal(dest)
    with pytest.raises(snap.SnapshotError, match="bad snapshot name"):
        snap.create("../escape", h)


def test_missing_input_is_refused(tmp_path):
    h = _history(tmp_path)
    (h / snap.COPY_FILES[0]).unlink()
    with pytest.raises(snap.SnapshotError, match="missing input file"):
        snap.create("t6", h)
