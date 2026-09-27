"""
research/data/snapshot.py
=========================
Frozen, hash-sealed copies of the shared history (the P7 input freeze).

Other agents write into shared/track2_liquid/history while studies run. A study that must be reproducible
(the P7.2 design runs, the P7.3 freeze, the P7.4 holdout run) reads a sealed snapshot instead:

    python -m research.data.snapshot create p7_design_20260926
    set TRACK2_HISTORY_DIR=<history>/_snapshots/p7_design_20260926
    python -m research.data.universe_build --pit-membership      # derived tables, built inside the copy
    python -m research.data.snapshot seal p7_design_20260926
    python -m research.data.snapshot verify p7_design_20260926

`create` copies the study inputs and fails if a source file changes while it is being copied (another
writer). `seal` hashes every file, writes SNAPSHOT.json and marks the files read-only. `verify` recomputes
every hash; any changed, missing or extra file fails. A sealed snapshot is never modified: new data means
a new snapshot with a new name.

Snapshots live under <history>/_snapshots/, which is gitignored with the history (plan rule 1.2.6).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from research.data import paths

IST = timezone(timedelta(hours=5, minutes=30))
SEAL = "SNAPSHOT.json"
SNAPSHOTS = "_snapshots"
COPY_DIRS = ("bars_15m", "daily", "reference", "events", "manifests")
COPY_FILES = (
    "bhavcopy/fno_point_in_time_2022_2026.parquet",
    "bhavcopy/historical_fno_upstox_resolution.json",
    "bhavcopy/cm_bhavcopy_2021_2026.parquet",
    "bhavcopy/fo_bhavcopy_underlyings_2021_2026.parquet",
    "raw/upstox/manifest.jsonl",
)
# read by universe_build from outside the history; hashed into the seal so a rebuild can be checked
EXTERNAL_INPUTS = ("fno_lot_sizes.json",)
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")


class SnapshotError(RuntimeError):
    pass


class SnapshotRaceError(SnapshotError):
    """A source file changed while it was being copied: another agent is writing. Retry when quiet."""


class SnapshotTamperedError(SnapshotError):
    """A sealed snapshot no longer matches its seal."""


def snapshots_root(history: Optional[Path] = None) -> Path:
    return Path(history or paths.history_dir()) / SNAPSHOTS


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _files(root: Path) -> List[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and p.name != SEAL)


def _rel(p: Path, root: Path) -> str:
    return p.relative_to(root).as_posix()


def _content_hash(files: Dict[str, Dict[str, Any]]) -> str:
    lines = "".join(f"{k}\t{v['sha256']}\t{v['bytes']}\n" for k, v in sorted(files.items()))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def _sources(history: Path) -> List[Tuple[Path, str]]:
    out: List[Tuple[Path, str]] = []
    for d in COPY_DIRS:
        base = history / d
        if not base.is_dir():
            raise SnapshotError(f"missing input folder {base}")
        out += [(p, _rel(p, history)) for p in _files(base)]
    for f in COPY_FILES:
        if not (history / f).is_file():
            raise SnapshotError(f"missing input file {history / f}")
        out.append((history / f, f))
    return out


def _make_writable(root: Path) -> None:
    for p in root.rglob("*"):
        if p.is_file():
            os.chmod(p, stat.S_IREAD | stat.S_IWRITE)


def create(name: str, history: Optional[Path] = None) -> Path:
    """Copy the study inputs into <history>/_snapshots/<name>. Each file is hashed before and after its
    copy and the copy is hashed too; any difference means a concurrent writer, and nothing is kept."""
    if not _NAME.match(name):
        raise SnapshotError(f"bad snapshot name {name!r}")
    history = Path(history or paths.history_dir())
    dest = snapshots_root(history) / name
    if dest.exists():
        raise SnapshotError(f"{dest} exists; a snapshot is never overwritten, use a new name")
    tmp = snapshots_root(history) / f".{name}.partial"
    if tmp.exists():                             # our own unfinished copy from an earlier attempt
        _make_writable(tmp)
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    raced: List[str] = []
    try:
        for src, rel in _sources(history):
            before = _sha256(src)
            out = tmp / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)
            if _sha256(out) != before or _sha256(src) != before:
                raced.append(rel)
        if raced:
            raise SnapshotRaceError(f"{len(raced)} files changed during the copy (e.g. {raced[:3]}); "
                                    "another writer is active, retry when the history folder is quiet")
        (tmp / "CREATED.json").write_text(json.dumps({
            "name": name, "source": str(history), "created": datetime.now(IST).isoformat(timespec="seconds"),
            "copied_dirs": list(COPY_DIRS), "copied_files": list(COPY_FILES)}, indent=1), encoding="utf-8")
        tmp.rename(dest)
    except BaseException:
        if tmp.exists():
            _make_writable(tmp)
            shutil.rmtree(tmp)
        raise
    return dest


def seal(path: Path) -> Dict[str, Any]:
    """Hash every file, write SNAPSHOT.json, make every file read-only. Sealing twice is refused."""
    path = Path(path)
    if (path / SEAL).exists():
        raise SnapshotError(f"{path} is already sealed")
    files = {_rel(p, path): {"bytes": p.stat().st_size, "sha256": _sha256(p)} for p in _files(path)}
    external = {}
    for n in EXTERNAL_INPUTS:
        p = paths.shared_input(n)
        external[n] = {"path": str(p), "sha256": _sha256(p) if p.is_file() else None}
    info = {"name": path.name, "sealed_at": datetime.now(IST).isoformat(timespec="seconds"),
            "n_files": len(files), "total_bytes": sum(v["bytes"] for v in files.values()),
            "content_sha256": _content_hash(files), "external_inputs": external, "files": files}
    (path / SEAL).write_text(json.dumps(info, indent=1), encoding="utf-8")
    for p in list(_files(path)) + [path / SEAL]:
        os.chmod(p, stat.S_IREAD)
    return info


def verify(path: Path) -> Dict[str, Any]:
    """Recompute every hash against the seal. Raises SnapshotTamperedError on any difference."""
    path = Path(path)
    if not (path / SEAL).is_file():
        raise SnapshotError(f"{path} is not sealed")
    info = json.loads((path / SEAL).read_text(encoding="utf-8"))
    sealed: Dict[str, Dict[str, Any]] = info["files"]
    now = {_rel(p, path): p for p in _files(path)}
    missing = sorted(set(sealed) - set(now))
    extra = sorted(set(now) - set(sealed))
    changed = sorted(k for k in set(sealed) & set(now)
                     if now[k].stat().st_size != sealed[k]["bytes"] or _sha256(now[k]) != sealed[k]["sha256"])
    if missing or extra or changed or _content_hash(sealed) != info["content_sha256"]:
        raise SnapshotTamperedError(f"{path.name}: changed={changed[:5]} missing={missing[:5]} extra={extra[:5]}")
    return {"name": info["name"], "content_sha256": info["content_sha256"], "n_files": info["n_files"],
            "sealed_at": info["sealed_at"]}


def describe(history: Optional[Path] = None) -> Dict[str, Any]:
    """What a study is reading: a verified sealed snapshot, or the live (mutable) history."""
    history = Path(history or paths.history_dir())
    if (history / SEAL).is_file():
        return {"kind": "SEALED_SNAPSHOT", **verify(history)}
    return {"kind": "LIVE_HISTORY_NOT_SNAPSHOT", "path": str(history)}


def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Frozen, hash-sealed history snapshots")
    ap.add_argument("cmd", choices=["create", "seal", "verify"])
    ap.add_argument("name")
    ap.add_argument("--history", default=None, help="source history (default: the shared history)")
    args = ap.parse_args(list(argv) if argv is not None else None)
    history = Path(args.history) if args.history else paths.history_dir()
    target = snapshots_root(history) / args.name
    if args.cmd == "create":
        print(f"created: {create(args.name, history)}")
    elif args.cmd == "seal":
        info = seal(target)
        print(json.dumps({k: v for k, v in info.items() if k != "files"}, indent=1))
    else:
        print(json.dumps(verify(target), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
