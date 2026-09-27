"""
research/trust/constitution.py: the Constitution is in force only with Yashu's SIGNED approval.
A quote recorded by an agent is only CLAIMED (Codex / Antigravity / ChatGPT, 27 Sep 2026). Written before the
implementation; signatures use OpenSSH's `ssh-keygen -Y sign/verify` with a throwaway key in the tests.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from research.trust import constitution as con

TEXT = "# Constitution\n\nArticle 1. Make money, protect capital.\n"
pytestmark = pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="OpenSSH ssh-keygen not available")


def _root(tmp_path: Path, text: str = TEXT) -> Path:
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    (root / "CONSTITUTION.md").write_text(text, encoding="utf-8")
    return root


def _key(tmp_path: Path, name: str = "owner") -> Path:
    k = tmp_path / "keys" / name
    k.parent.mkdir(exist_ok=True)
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(k)], check=True)
    return k


def _claim(root: Path, **over) -> dict:
    fields = dict(quote="I approve the constitution", where="Claude Code chat, 27 Sep 2026 18:10 IST",
                  recorded_by="claude")
    fields.update(over)
    return con.record_claim(root, **fields)


def test_without_anything_the_constitution_is_a_draft(tmp_path):
    st, details = con.check(_root(tmp_path))
    assert st == "UNREVIEWED" and "DRAFT" in details[0]


def test_an_agent_recorded_quote_is_only_a_claim(tmp_path):
    root = _root(tmp_path)
    rec = _claim(root)
    assert rec["kind"] == "CONSTITUTION_CLAIM" and rec["sha256"] == con.constitution_sha(root)
    st, details = con.check(root)
    assert st == "UNREVIEWED" and "CLAIMED" in details[0] and "not verified" in details[0]


def test_a_claim_needs_his_words_the_place_and_a_real_recorder(tmp_path):
    root = _root(tmp_path)
    for bad in (dict(quote=""), dict(where=""), dict(recorded_by="someone")):
        with pytest.raises(con.ApprovalError):
            _claim(root, **bad)


def test_his_signature_puts_the_text_in_force(tmp_path):
    root = _root(tmp_path)
    key = _key(tmp_path)
    con.register_owner_key(root, key.with_suffix(".pub"))
    con.sign(root, key)
    st, details = con.check(root)
    assert st == "PASS" and "VERIFIED" in details[0] and con.owner_fingerprint(root) in details[0]


def test_a_change_after_his_signature_fails_until_he_signs_again(tmp_path):
    root = _root(tmp_path)
    key = _key(tmp_path)
    con.register_owner_key(root, key.with_suffix(".pub"))
    con.sign(root, key)
    (root / "CONSTITUTION.md").write_text(TEXT + "Article 9. Agents may trade real money.\n", encoding="utf-8")
    _claim(root, quote="(an agent says Yashu approved article 9)")
    st, details = con.check(root)
    assert st == "FAIL" and "since Yashu's last signed approval" in details[0]
    con.sign(root, key)
    assert con.check(root)[0] == "PASS"


def test_a_forged_signature_fails(tmp_path):
    root = _root(tmp_path)
    key = _key(tmp_path)
    con.register_owner_key(root, key.with_suffix(".pub"))
    con.sign(root, key)
    sig = con.signature_path(root, con.constitution_sha(root))
    sig.write_text(sig.read_text(encoding="utf-8").replace("A", "B", 3), encoding="utf-8")
    assert con.check(root)[0] == "FAIL"


def test_a_swapped_owner_key_fails_even_with_a_valid_signature_from_the_new_key(tmp_path):
    root = _root(tmp_path)
    owner, intruder = _key(tmp_path, "owner"), _key(tmp_path, "intruder")
    con.register_owner_key(root, owner.with_suffix(".pub"))
    con.sign(root, owner)
    (root / "governance" / "owner_allowed_signers").unlink()
    con.register_owner_key(root, intruder.with_suffix(".pub"))
    (root / "CONSTITUTION.md").write_text(TEXT + "Article 9.\n", encoding="utf-8")
    con.sign(root, intruder)
    st, details = con.check(root)
    assert st == "FAIL" and "owner key changed" in " ".join(details)


def test_the_owner_key_is_never_silently_replaced(tmp_path):
    root = _root(tmp_path)
    con.register_owner_key(root, _key(tmp_path, "owner").with_suffix(".pub"))
    with pytest.raises(con.ApprovalError):
        con.register_owner_key(root, _key(tmp_path, "other").with_suffix(".pub"))


def test_a_tampered_ledger_fails(tmp_path):
    root = _root(tmp_path)
    _claim(root)
    led = root / "governance" / "approvals.jsonl"
    row = json.loads(led.read_text(encoding="utf-8").splitlines()[0])
    row["quote"] = "edited later"
    led.write_text(json.dumps(row) + "\n", encoding="utf-8")
    st, details = con.check(root)
    assert st == "FAIL" and "ledger" in details[0].lower()


def test_line_endings_do_not_count_as_a_change(tmp_path):
    root = _root(tmp_path)
    key = _key(tmp_path)
    con.register_owner_key(root, key.with_suffix(".pub"))
    con.sign(root, key)
    (root / "CONSTITUTION.md").write_bytes(TEXT.replace("\n", "\r\n").encode("utf-8"))
    assert con.check(root)[0] == "PASS"


def test_another_working_copy_that_differs_fails_once_in_force(tmp_path):
    root = _root(tmp_path)
    key = _key(tmp_path)
    con.register_owner_key(root, key.with_suffix(".pub"))
    con.sign(root, key)
    other = tmp_path / "other"
    other.mkdir()
    (other / "CONSTITUTION.md").write_text(TEXT + "extra\n", encoding="utf-8")
    st, details = con.check(root, other_roots=[other])
    assert st == "FAIL" and any("differs" in d for d in details)


def test_the_constitution_check_is_part_of_the_trust_status():
    from research.trust import status

    assert "constitution" in [name for name, _ in status.CHECKS]
