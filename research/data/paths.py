"""
research/data/paths.py
======================
Single place that resolves every research data path (plan P3.1). All writers in research/data,
research/features and research/studies use these functions; nothing hard-codes a path.

Environment overrides:
    TRACK2_HISTORY_DIR     parquet history root (default: <main checkout>/shared/track2_liquid/history)
    TRACK2_MAIN_CHECKOUT   main checkout (credentials, shared inputs); default: this repo, or the main
                           checkout when this repo is a git worktree
    TRACK2_OUTPUTS_DIR     generated research outputs (default: <repo>/research/outputs)

Every default location is gitignored (plan rule 1.2.6: data is never committed).
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    return Path(raw) if raw else default


def repo_root() -> Path:
    return REPO_ROOT


def _detect_main_checkout(root: Path) -> Path:
    """In a git worktree, <root>/.git is a file 'gitdir: <main>/.git/worktrees/<name>'; return <main>.
    In the main checkout (or without git) return root itself."""
    marker = root / ".git"
    if marker.is_file():
        text = marker.read_text(encoding="utf-8", errors="replace").strip()
        if text.startswith("gitdir:"):
            gitdir = Path(text[len("gitdir:"):].strip())
            if gitdir.parent.name == "worktrees" and gitdir.parent.parent.name == ".git":
                return gitdir.parent.parent.parent
    return root


def main_checkout() -> Path:
    """The main checkout. In a git worktree the shared inputs and credentials live there."""
    return _env_path("TRACK2_MAIN_CHECKOUT", _detect_main_checkout(REPO_ROOT))


def history_dir() -> Path:
    """Plan rule 1.2.6: default <main checkout>/shared/track2_liquid/history, so every worktree shares one
    (gitignored) history."""
    return _env_path("TRACK2_HISTORY_DIR", main_checkout() / "shared" / "track2_liquid" / "history")


def bars_15m_dir() -> Path:
    return history_dir() / "bars_15m"


def daily_dir() -> Path:
    return history_dir() / "daily"


def reference_dir() -> Path:
    """Universe, sector map and calendar tables derived from the history."""
    return history_dir() / "reference"


def outputs_dir() -> Path:
    return _env_path("TRACK2_OUTPUTS_DIR", REPO_ROOT / "research" / "outputs")


def shared_input(name: str) -> Path:
    """A file under shared/track2_liquid of the main checkout (inputs other agents maintain)."""
    return main_checkout() / "shared" / "track2_liquid" / name


def ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
