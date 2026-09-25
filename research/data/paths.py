"""
research/data/paths.py
======================
Single place that resolves every research data path (plan P3.1). All writers in research/data,
research/features and research/studies use these functions; nothing hard-codes a path.

Environment overrides:
    TRACK2_HISTORY_DIR     parquet history root (default: <repo>/shared/track2_liquid/history)
    TRACK2_MAIN_CHECKOUT   main checkout (credentials, shared inputs); default: this repo
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


def main_checkout() -> Path:
    """The main checkout. In a git worktree the shared inputs and credentials live there."""
    return _env_path("TRACK2_MAIN_CHECKOUT", REPO_ROOT)


def history_dir() -> Path:
    return _env_path("TRACK2_HISTORY_DIR", REPO_ROOT / "shared" / "track2_liquid" / "history")


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
