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
