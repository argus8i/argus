"""User-authorized project autonomy with a verified checkpoint before dispatch.

Backups provide recovery, not an OS security boundary. Runtime dependencies and
caches are rebuildable and excluded; project files (including ignored files)
and .git are included. Never automatically restores or deletes checkpoints.
"""
import hashlib
import json
import os
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKUPS = ROOT.parent / "swing-trades-checkpoints"
EXCLUDED_DIRS = {".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache"}


def create_checkpoint(root=None, destination=None):
    root = Path(root or ROOT).resolve()
    destination = Path(destination or BACKUPS).resolve()
    if destination == root or root in destination.parents:
        raise ValueError("Checkpoint destination must be outside the workspace")
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    pending = destination / (stamp + ".partial")
    final = destination / (stamp + ".zip")
    manifest = {"root": str(root), "created_utc": stamp, "excluded": [], "files": {}}
    with zipfile.ZipFile(pending, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for base, dirs, files in os.walk(root, followlinks=False):
            for name in list(dirs):
                p = Path(base) / name
                if name in EXCLUDED_DIRS:
                    manifest["excluded"].append(str(p.relative_to(root)))
                    dirs.remove(name)
                elif p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()):
                    raise RuntimeError(f"Checkpoint requires explicit handling of directory link: {p}")
            for name in files:
                p = Path(base) / name
                if p.is_symlink():
                    raise RuntimeError(f"Checkpoint requires explicit handling of file link: {p}")
                rel = p.relative_to(root).as_posix()
                before = p.stat()
                digest = hashlib.sha256()
                with p.open("rb") as source, archive.open("workspace/" + rel, "w", force_zip64=True) as target:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
                        target.write(chunk)
                after = p.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise RuntimeError(f"File changed during checkpoint; retry when idle: {rel}")
                manifest["files"][rel] = digest.hexdigest()
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
    with zipfile.ZipFile(pending) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Checkpoint CRC verification failed")
    pending.rename(final)
    return final


def prepare_dispatch(agent):
    """Failure to create/verify a backup prevents the child from starting."""
    if agent not in {"CLAUDE", "CODEX", "ANTIGRAVITY"}:
        raise ValueError("Unknown agent")
    checkpoint = create_checkpoint()
    print(f"[CHECKPOINT] {checkpoint}", flush=True)
    if agent == "CODEX":
        return ["--sandbox", "workspace-write"]
    if agent == "ANTIGRAVITY":
        return ["--project", "3ccee98c-0ec8-497b-a076-f86d4ef452ae", "--sandbox"]
    return []


TASK_BOUNDARIES = f"""
The authoritative workspace is {ROOT}. Resolve ALL task-relative paths against
that absolute directory. Set every command working directory there explicitly.
The CLI scratch directory is NOT the project; never write task artifacts there.
User-authorized project access: inspect, edit, build and test this workspace.
Preserve unrelated work. Never delete the repository, .git, or external checkpoints;
never use git reset --hard or git clean to discard user work. No live broker orders,
broker login, credential extraction, or alteration of the paper-only trading gate.
Report changed files and actual checks. These are task instructions, not an OS sandbox.
"""
