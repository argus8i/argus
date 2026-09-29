import hashlib
import json
import zipfile
import tempfile
from pathlib import Path

import pytest
from antigravity.daemons import agent_access


@pytest.fixture
def tmp_path():
    with tempfile.TemporaryDirectory(prefix="agent_access_test_") as directory:
        yield Path(directory)


def test_checkpoint_preserves_dirty_untracked_ignored_and_git(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    for name in ["dirty.py", "untracked.txt", ".env", ".git/HEAD"]:
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"existing contents")
    env = root / ".venv"
    env.mkdir()
    (env / "rebuildable").write_text("excluded")
    out = agent_access.create_checkpoint(root, tmp_path / "backups")
    with zipfile.ZipFile(out) as z:
        manifest = json.loads(z.read("manifest.json"))
        for name in ["dirty.py", "untracked.txt", ".env", ".git/HEAD"]:
            assert z.read("workspace/" + name) == b"existing contents"
            assert manifest["files"][name] == hashlib.sha256(b"existing contents").hexdigest()
        assert ".venv" in manifest["excluded"]
    assert (root / "dirty.py").read_bytes() == b"existing contents"


def test_backup_inside_workspace_rejected(tmp_path):
    with pytest.raises(ValueError):
        agent_access.create_checkpoint(tmp_path, tmp_path / "backups")


def test_failed_checkpoint_prevents_permission_flags(monkeypatch):
    def fail():
        raise OSError("disk full")
    monkeypatch.setattr(agent_access, "create_checkpoint", fail)
    with pytest.raises(OSError, match="disk full"):
        agent_access.prepare_dispatch("ANTIGRAVITY")


def test_checkpoint_excludes_track2_history_but_keeps_other_history(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    # Track 2 heavy history path
    t2_history = root / "shared" / "track2_liquid" / "history"
    t2_history.mkdir(parents=True, exist_ok=True)
    (t2_history / "massive_bhavcopy.csv").write_bytes(b"huge binary archive data")

    # Generic history folder (e.g. documentation or audit history)
    doc_history = root / "docs" / "history"
    doc_history.mkdir(parents=True, exist_ok=True)
    (doc_history / "changelog.txt").write_bytes(b"historical documentation")

    out = agent_access.create_checkpoint(root, tmp_path / "backups")
    with zipfile.ZipFile(out) as z:
        manifest = json.loads(z.read("manifest.json"))
        # shared/track2_liquid/history must be excluded
        assert "shared/track2_liquid/history" in manifest["excluded"]
        assert "workspace/shared/track2_liquid/history/massive_bhavcopy.csv" not in z.namelist()
        # docs/history must NOT be excluded; it must be backed up
        assert "shared/track2_liquid/history" in manifest["excluded"]
        assert "docs/history" not in manifest["excluded"]
        assert "workspace/docs/history/changelog.txt" in z.namelist()
        assert z.read("workspace/docs/history/changelog.txt") == b"historical documentation"

