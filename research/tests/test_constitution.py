"""
research/trust/constitution.py: the Constitution can change only with Yashu's recorded approval. Written before the
implementation.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.trust import constitution as con

TEXT = "# Constitution\n\nArticle 1. Make money, protect capital.\n"


def _root(tmp_path: Path, text: str = TEXT) -> Path:
    (tmp_path / "CONSTITUTION.md").write_text(text, encoding="utf-8")
    return tmp_path


def _approve(root: Path, **over) -> dict:
    fields = dict(quote="I approve the constitution", where="Claude Code chat, 27 Sep 2026 18:10 IST",
                  recorded_by="claude")
    fields.update(over)
    return con.record_approval(root, **fields)


def test_without_an_approval_the_constitution_is_a_draft(tmp_path):
    st, details = con.check(_root(tmp_path))
    assert st == "UNREVIEWED" and "DRAFT" in details[0]


def test_an_approved_unchanged_constitution_passes(tmp_path):
    root = _root(tmp_path)
    rec = _approve(root)
    assert rec["sha256"] == con.constitution_sha(root) and rec["approved_by"] == "Yashu"
    st, details = con.check(root)
    assert st == "PASS", details


def test_a_change_after_approval_fails_until_yashu_approves_again(tmp_path):
    root = _root(tmp_path)
    _approve(root)
    (root / "CONSTITUTION.md").write_text(TEXT + "Article 9. Agents may trade real money.\n", encoding="utf-8")
    st, details = con.check(root)
    assert st == "FAIL" and "without Yashu's approval" in details[0]
    _approve(root, quote="I approve article 9")
    assert con.check(root)[0] == "PASS"


def test_an_approval_needs_his_words_the_place_and_a_real_recorder(tmp_path):
    root = _root(tmp_path)
    for bad in (dict(quote=""), dict(where=""), dict(recorded_by="someone")):
        with pytest.raises(con.ApprovalError):
            _approve(root, **bad)


def test_a_tampered_approval_ledger_fails(tmp_path):
    root = _root(tmp_path)
    _approve(root)
    led = root / "governance" / "approvals.jsonl"
    row = json.loads(led.read_text(encoding="utf-8").splitlines()[0])
    row["quote"] = "edited later"
    led.write_text(json.dumps(row) + "\n", encoding="utf-8")
    st, details = con.check(root)
    assert st == "FAIL" and "ledger" in details[0].lower()


def test_line_endings_do_not_count_as_a_change(tmp_path):
    root = _root(tmp_path)
    _approve(root)
    (root / "CONSTITUTION.md").write_bytes(TEXT.replace("\n", "\r\n").encode("utf-8"))
    assert con.check(root)[0] == "PASS"


def test_two_working_copies_that_differ_fail(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    _root(a), _root(b, TEXT + "extra\n")
    _approve(a)
    st, details = con.check(a, other_roots=[b])
    assert st == "FAIL" and any("differs" in d for d in details)
    missing = tmp_path / "c"
    missing.mkdir()
    assert con.check(a, other_roots=[missing])[0] == "FAIL"


def test_the_constitution_check_is_part_of_the_trust_status():
    from research.trust import status

    assert "constitution" in [name for name, _ in status.CHECKS]
