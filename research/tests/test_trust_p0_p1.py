"""
Trust system P0/P1 (research/notes/trust_system_plan.md v2): review-record schema and hash-chained ledger, exact-commit
review coverage (T7), register reconstruction from decision records, paper-only gate, and fail-closed exit classes of
the status command (T1). Written before the implementation (Rule 8 v2 test-first gate).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from research.trust import review_check as rc
from research.trust import schemas
from research.trust import status as st

SCOPE = ["**/*.py", "**/*.yaml"]
EXCL = ["**/*.md"]


# ---------------------------------------------------------------------------------------------- schema + ledger
def _review(**kw):
    rec = {"schema_version": 1, "review_id": "R1", "reviewer": "codex", "author": "claude",
           "reviewed_commit": "a" * 40, "parent_commit": "b" * 40, "tree_sha": "c" * 40, "patch_sha256": "d" * 64,
           "scope": SCOPE, "verdict": "APPROVED", "reviewed_at": "2026-09-26T18:00:00+05:30",
           "tests": [{"command": "pytest", "exit_code": 0, "artifact": "x.log", "output_sha256": "e" * 64}],
           "failing_first_tests": ["t1"], "findings": []}
    rec.update(kw)
    return rec


def test_review_schema_accepts_a_complete_record():
    schemas.validate_review(_review())


@pytest.mark.parametrize("bad", [
    {"schema_version": 2}, {"verdict": "LGTM"}, {"reviewed_commit": "abc"}, {"reviewer": ""},
    {"tests": []}, {"patch_sha256": "zz"}, {"reviewer": "codex", "author": "codex"},
])
def test_review_schema_rejects_incomplete_or_self_review(bad):
    with pytest.raises(schemas.SchemaError):
        schemas.validate_review(_review(**bad))


def test_review_schema_rejects_missing_fields():
    rec = _review()
    del rec["tree_sha"]
    with pytest.raises(schemas.SchemaError):
        schemas.validate_review(rec)


def test_ledger_is_hash_chained_and_tamper_evident(tmp_path):
    led = tmp_path / "reviews.jsonl"
    schemas.append_ledger(led, _review(review_id="R1"))
    schemas.append_ledger(led, _review(review_id="R2"))
    assert [r["review_id"] for r in schemas.read_ledger(led)] == ["R1", "R2"]
    lines = led.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["verdict"] = "REJECTED"                                    # edit an old record in place
    led.write_text(json.dumps(first) + "\n" + lines[1] + "\n", encoding="utf-8")
    with pytest.raises(schemas.LedgerError):
        schemas.read_ledger(led)


def test_ledger_rejects_a_deleted_record(tmp_path):
    led = tmp_path / "reviews.jsonl"
    for i in range(3):
        schemas.append_ledger(led, _review(review_id=f"R{i}"))
    lines = led.read_text(encoding="utf-8").splitlines()
    led.write_text(lines[0] + "\n" + lines[2] + "\n", encoding="utf-8")
    with pytest.raises(schemas.LedgerError):
        schemas.read_ledger(led)


def test_missing_ledger_reads_as_empty_but_malformed_raises(tmp_path):
    assert schemas.read_ledger(tmp_path / "none.jsonl") == []
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{not json\n", encoding="utf-8")
    with pytest.raises(schemas.LedgerError):
        schemas.read_ledger(bad)


# ---------------------------------------------------------------------------------------------- T7 review coverage
def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    _git(r, "config", "core.autocrlf", "false")
    (r / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(r, "add", ".")
    _git(r, "commit", "-q", "-m", "base")
    return r


def _commit(r: Path, name: str, text: str, msg: str) -> str:
    (r / name).write_text(text, encoding="utf-8")
    _git(r, "add", ".")
    _git(r, "commit", "-q", "-m", msg)
    return _git(r, "rev-parse", "HEAD")


def _approve(r: Path, parent: str, commit: str, **kw) -> dict:
    d = rc.describe(r, parent, commit, SCOPE, EXCL)
    return _review(reviewed_commit=commit, parent_commit=parent, tree_sha=d["tree_sha"],
                   patch_sha256=d["patch_sha256"], **kw)


def test_governed_scope_matching():
    assert rc.governed("research/x.py", SCOPE, EXCL)
    assert rc.governed("a.py", SCOPE, EXCL)
    assert not rc.governed("research/notes/x.md", SCOPE, EXCL)


def test_exact_review_covers_and_later_commit_is_unreviewed(repo):
    base = _git(repo, "rev-parse", "HEAD")
    c1 = _commit(repo, "b.py", "y = 2\n", "c1")
    assert [u["commit"] for u in rc.unreviewed_commits(repo, base, "HEAD", [], SCOPE, EXCL)] == [c1]
    reviews = [_approve(repo, base, c1)]
    assert rc.unreviewed_commits(repo, base, "HEAD", reviews, SCOPE, EXCL) == []
    c2 = _commit(repo, "b.py", "y = 3\n", "c2")
    assert [u["commit"] for u in rc.unreviewed_commits(repo, base, "HEAD", reviews, SCOPE, EXCL)] == [c2]


def test_notes_only_commit_needs_no_review(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "n.md", "notes\n", "notes")
    assert rc.unreviewed_commits(repo, base, "HEAD", [], SCOPE, EXCL) == []


def test_review_with_wrong_patch_hash_or_self_review_or_rejection_does_not_cover(repo):
    base = _git(repo, "rev-parse", "HEAD")
    c1 = _commit(repo, "b.py", "y = 2\n", "c1")
    good = _approve(repo, base, c1)
    for bad in (dict(good, patch_sha256="0" * 64), dict(good, author="codex"), dict(good, verdict="REJECTED")):
        assert [u["commit"] for u in rc.unreviewed_commits(repo, base, "HEAD", [bad], SCOPE, EXCL)] == [c1]


def test_range_review_covers_every_commit_in_it(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "b.py", "y = 2\n", "c1")
    c2 = _commit(repo, "c.py", "z = 2\n", "c2")
    assert rc.unreviewed_commits(repo, base, "HEAD", [_approve(repo, base, c2)], SCOPE, EXCL) == []


# ---------------------------------------------------------------------------------------------- T1 checks
LEGACY = ["OLD"]


def _rec(sid, frm, to, at):
    return {"strategy_id": sid, "from": frm, "to": to, "at": at, "action": f"X_{to}"}


def test_register_reconstruction_matches():
    reg = {"strategies": {"OLD": {"status": "KILLED_ON_DESIGN"}, "NEW": {"status": "REJECTED"}}}
    recs = [_rec("OLD", "UNVERIFIED", "KILLED_ON_DESIGN", "2026-09-26T01:00:00"),
            _rec("NEW", None, "UNVERIFIED", "2026-09-26T02:00:00"),
            _rec("NEW", "UNVERIFIED", "REJECTED", "2026-09-26T03:00:00")]
    assert st.reconstruct_register(reg, recs, LEGACY) == []


def test_registration_and_transition_in_the_same_second_replay_in_causal_order():
    reg = {"strategies": {"NEW": {"status": "REJECTED"}}}
    recs = [_rec("NEW", "UNVERIFIED", "REJECTED", "2026-09-26T13:45:14+05:30"),       # sorts first by action name
            _rec("NEW", None, "UNVERIFIED", "2026-09-26T13:45:14+05:30")]
    recs[0]["action"], recs[1]["action"] = "HOLDOUT_REJECTED", "REGISTER_CANDIDATE"
    assert st.reconstruct_register(reg, recs, []) == []


@pytest.mark.parametrize("reg,recs", [
    ({"strategies": {"OLD": {"status": "SHADOW"}}}, []),                                           # edited register
    ({"strategies": {"OLD": {"status": "UNVERIFIED"}, "GHOST": {"status": "UNVERIFIED"}}}, []),    # appeared unrecorded
    ({"strategies": {"OLD": {"status": "UNVERIFIED"}}}, [_rec("X", None, "UNVERIFIED", "t")]),     # record, no register
    ({"strategies": {"OLD": {"status": "REJECTED"}}}, [_rec("OLD", "SHADOW", "REJECTED", "t")]),  # broken chain
])
def test_register_reconstruction_detects_tampering(reg, recs):
    assert st.reconstruct_register(reg, recs, LEGACY)


def test_paper_gate_detects_allow_live_true(tmp_path):
    (tmp_path / "antigravity" / "models").mkdir(parents=True)
    f = tmp_path / "antigravity" / "models" / "engine.py"
    f.write_text("ALLOW_LIVE: bool = False\n", encoding="utf-8")
    assert st.check_paper_gate(tmp_path) == []
    f.write_text("ALLOW_LIVE: bool = True\n", encoding="utf-8")
    assert st.check_paper_gate(tmp_path)


def test_internal_error_is_never_green(tmp_path, monkeypatch):
    def boom(ctx):
        raise RuntimeError("checker bug")

    monkeypatch.setattr(st, "CHECKS", [("boom", boom)])
    report, code = st.run(ctx={}, out_dir=tmp_path)
    assert code == st.EXIT_INTERNAL
    assert report["checks"][0]["status"] == "ERROR"
    assert json.loads((tmp_path / "status_latest.json").read_text(encoding="utf-8"))["exit_code"] == st.EXIT_INTERNAL


def test_exit_class_priority(tmp_path, monkeypatch):
    res = {"a": ("MISSING", []), "b": ("UNREVIEWED", []), "c": ("FAIL", [])}
    monkeypatch.setattr(st, "CHECKS", [(k, (lambda v: lambda ctx: v)(v)) for k, v in res.items()])
    _, code = st.run(ctx={}, out_dir=tmp_path)
    assert code == st.EXIT_INTEGRITY
    monkeypatch.setattr(st, "CHECKS", [("a", lambda ctx: ("MISSING", [])), ("b", lambda ctx: ("UNREVIEWED", []))])
    assert st.run(ctx={}, out_dir=tmp_path)[1] == st.EXIT_UNREVIEWED
    monkeypatch.setattr(st, "CHECKS", [("a", lambda ctx: ("MISSING", ["x"]))])
    assert st.run(ctx={}, out_dir=tmp_path)[1] == st.EXIT_MISSING
    monkeypatch.setattr(st, "CHECKS", [("a", lambda ctx: ("PASS", []))])
    assert st.run(ctx={}, out_dir=tmp_path)[1] == 0


def test_unknown_check_status_is_an_internal_error(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "CHECKS", [("a", lambda ctx: ("GREEN", []))])
    assert st.run(ctx={}, out_dir=tmp_path)[1] == st.EXIT_INTERNAL
