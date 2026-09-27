"""
research/trust/constitution.py
==============================
The Constitution (CONSTITUTION.md at the repository root) is in force only with Yashu's SIGNED approval.

  governance/owner_allowed_signers     Yashu's public signing key (registered once; never silently replaced)
  governance/signatures/<sha>.sig      his signature over one exact text (OpenSSH `ssh-keygen -Y sign`)
  governance/approvals.jsonl           hash-chained log: CONSTITUTION_SIGNATURE rows (pin the key's fingerprint at the
                                       first signature) and CONSTITUTION_CLAIM rows (an agent recording what Yashu
                                       said; a claim is NEVER an approval)

  check()   PASS        the text equals a text Yashu signed, the signature verifies, the key is the pinned key
            UNREVIEWED  never signed: a DRAFT, or APPROVAL CLAIMED (not verified)
            FAIL        changed since his last signed approval, a signature that does not verify, the owner key
                        changed, the ledger edited, or another working copy differs

Only Yashu signs, in his own terminal, with a passphrase he never types into any agent chat:
    python -m research.trust.constitution keygen     # once: creates his key OUTSIDE the repository, asks a passphrase
    python -m research.trust.constitution sign       # each approval: signs the current text, asks the passphrase
Anyone:
    python -m research.trust.constitution status
    python -m research.trust.constitution claim --quote "..." --where "..." --recorded-by claude

Honest limit: agents run commands on this machine, so nothing local is unbreakable. An agent that knows neither the
passphrase nor the key cannot make a valid signature; replacing the key is detected (pinned fingerprint), and the
daily report prints the fingerprint so Yashu can recognise it.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

IST = timezone(timedelta(hours=5, minutes=30))
FILE = "CONSTITUTION.md"
GOV = Path("governance")
LEDGER = GOV / "approvals.jsonl"
SIGNERS = GOV / "owner_allowed_signers"
NAMESPACE = "swing-trades-constitution"
IDENTITY = "yashu"
RECORDERS = ("claude", "codex", "antigravity", "yashu")
DEFAULT_KEY = Path.home() / ".swing_trades_owner" / "owner_ed25519"


class ApprovalError(ValueError):
    """A claim without Yashu's words or place, or an attempt to replace the registered owner key."""


class LedgerBroken(RuntimeError):
    """The approval ledger's hash chain does not verify (an old record was edited or removed)."""


# ---------------------------------------------------------------------------------------------- text and ledger
def _text(root: Path) -> Optional[bytes]:
    p = Path(root) / FILE
    return p.read_bytes().replace(b"\r\n", b"\n") if p.exists() else None


def constitution_sha(root: Path) -> Optional[str]:
    t = _text(root)
    return hashlib.sha256(t).hexdigest() if t is not None else None


def _canon(rec: Dict[str, Any]) -> bytes:
    body = {k: v for k, v in rec.items() if k != "record_sha256"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def read_ledger(root: Path) -> List[Dict[str, Any]]:
    p = Path(root) / LEDGER
    if not p.exists():
        return []
    rows, prev = [], ""
    for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if not ln.strip():
            continue
        r = json.loads(ln)
        if r.get("prev_record_sha256") != prev or hashlib.sha256(_canon(r)).hexdigest() != r.get("record_sha256"):
            raise LedgerBroken(f"approval ledger broken at line {i} of {p}")
        prev = r["record_sha256"]
        rows.append(r)
    return rows


def _append(root: Path, rec: Dict[str, Any]) -> Dict[str, Any]:
    rows = read_ledger(root)
    rec = {**rec, "recorded_at": datetime.now(IST).isoformat(timespec="seconds"),
           "prev_record_sha256": rows[-1]["record_sha256"] if rows else ""}
    rec["record_sha256"] = hashlib.sha256(_canon(rec)).hexdigest()
    p = Path(root) / LEDGER
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")
    return rec


def record_claim(root: Path, *, quote: str, where: str, recorded_by: str) -> Dict[str, Any]:
    """An agent records what Yashu said about the CURRENT text. A claim never puts a text in force."""
    if not str(quote).strip():
        raise ApprovalError("Yashu's own words are required (quote)")
    if not str(where).strip():
        raise ApprovalError("where and when he said it is required (where)")
    if recorded_by not in RECORDERS:
        raise ApprovalError(f"recorded_by must be one of {RECORDERS}")
    sha = constitution_sha(root)
    if sha is None:
        raise ApprovalError(f"{FILE} does not exist")
    return _append(root, {"kind": "CONSTITUTION_CLAIM", "sha256": sha, "quote": str(quote).strip(),
                          "where": str(where).strip(), "recorded_by": recorded_by})


# ---------------------------------------------------------------------------------------------- key and signatures
def _ssh_keygen() -> str:
    exe = shutil.which("ssh-keygen")
    if not exe:
        raise RuntimeError("OpenSSH ssh-keygen is not available")
    return exe


def owner_fingerprint(root: Path) -> Optional[str]:
    p = Path(root) / SIGNERS
    if not p.exists():
        return None
    parts = p.read_text(encoding="utf-8").split()
    blob = next((x for x in parts if x.startswith("AAAA")), None)
    if blob is None:
        return None
    return "SHA256:" + base64.b64encode(hashlib.sha256(base64.b64decode(blob)).digest()).decode().rstrip("=")


def register_owner_key(root: Path, public_key: Path) -> str:
    """Register Yashu's public key once. Replacing it is refused (and would be detected by the pinned fingerprint)."""
    p = Path(root) / SIGNERS
    if p.exists():
        raise ApprovalError(f"{SIGNERS} already exists; the owner key is never replaced silently")
    kind, blob = Path(public_key).read_text(encoding="utf-8").split()[:2]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f'{IDENTITY} namespaces="{NAMESPACE}" {kind} {blob}\n', encoding="utf-8")
    return owner_fingerprint(root) or ""


def signature_path(root: Path, sha: str) -> Path:
    return Path(root) / GOV / "signatures" / f"{sha}.sig"


def sign(root: Path, private_key: Path) -> Dict[str, Any]:
    """Yashu signs the CURRENT text. ssh-keygen asks for the key's passphrase in his terminal."""
    text, sha = _text(root), constitution_sha(root)
    if text is None or sha is None:
        raise ApprovalError(f"{FILE} does not exist")
    fp = owner_fingerprint(root)
    if fp is None:
        raise ApprovalError(f"no owner key registered ({SIGNERS}); run keygen first")
    with tempfile.TemporaryDirectory() as td:
        msg = Path(td) / "constitution.txt"
        msg.write_bytes(text)
        subprocess.run([_ssh_keygen(), "-Y", "sign", "-f", str(private_key), "-n", NAMESPACE, str(msg)], check=True)
        out = signature_path(root, sha)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes((Path(td) / "constitution.txt.sig").read_bytes())
    return _append(root, {"kind": "CONSTITUTION_SIGNATURE", "sha256": sha, "fingerprint": fp,
                          "signature_sha256": hashlib.sha256(out.read_bytes()).hexdigest()})


def verify(root: Path, sha: str) -> bool:
    text, sig, signers = _text(root), signature_path(root, sha), Path(root) / SIGNERS
    if text is None or not sig.exists() or not signers.exists() or hashlib.sha256(text).hexdigest() != sha:
        return False
    r = subprocess.run([_ssh_keygen(), "-Y", "verify", "-f", str(signers), "-I", IDENTITY, "-n", NAMESPACE,
                        "-s", str(sig)], input=text, capture_output=True)
    return r.returncode == 0


# ---------------------------------------------------------------------------------------------- check
def check(root: Path, other_roots: Sequence[Path] = ()) -> Tuple[str, List[str]]:
    sha = constitution_sha(root)
    if sha is None:
        return "MISSING", [f"{FILE} does not exist in {root}"]
    try:
        rows = read_ledger(root)
    except (LedgerBroken, ValueError) as exc:
        return "FAIL", [f"approval ledger: {exc}"]
    sigs = [r for r in rows if r.get("kind") == "CONSTITUTION_SIGNATURE"]
    claims = [r for r in rows if r.get("kind") == "CONSTITUTION_CLAIM" and r.get("sha256") == sha]
    if not sigs:                                    # never in force: a draft, perhaps with a claim
        if claims:
            c = claims[-1]
            return "UNREVIEWED", [f"APPROVAL CLAIMED by {c['recorded_by']} ({c['where']}: \"{c['quote']}\"), not "
                                  f"verified by Yashu's signature; {FILE} ({sha[:12]}) is not in force"]
        return "UNREVIEWED", [f"DRAFT: {FILE} ({sha[:12]}) has never been signed by Yashu; it is not in force"]
    fails: List[str] = []
    pinned, fp = sigs[0].get("fingerprint"), owner_fingerprint(root)
    signed_now = signature_path(root, sha).exists()
    if not signed_now:
        fails.append(f"{FILE} changed since Yashu's last signed approval (signed {sigs[-1]['sha256'][:12]}, "
                     f"now {sha[:12]})")
    elif not verify(root, sha):
        fails.append(f"the signature for {sha[:12]} does not verify with the registered owner key")
    if fp != pinned:
        fails.append(f"owner key changed since the first signed approval (pinned {pinned}, now {fp})")
    for other in other_roots:
        o = constitution_sha(other)
        if o is None:
            fails.append(f"{FILE} is missing in the other working copy {other}")
        elif o != sha:
            fails.append(f"{FILE} in {other} differs from {root} ({o[:12]} vs {sha[:12]})")
    if fails:
        return "FAIL", fails
    return "PASS", [f"VERIFIED: {FILE} ({sha[:12]}) is signed by Yashu's key {fp}"]


# ---------------------------------------------------------------------------------------------- cli
def main(argv: Optional[List[str]] = None) -> int:
    from research.data import paths

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Constitution: status, claims, and Yashu's signature")
    ap.add_argument("cmd", choices=["status", "claim", "keygen", "sign"])
    ap.add_argument("--quote", default="")
    ap.add_argument("--where", default="")
    ap.add_argument("--recorded-by", default="")
    ap.add_argument("--key", default=str(DEFAULT_KEY))
    a = ap.parse_args(argv)
    root = paths.repo_root()
    try:
        if a.cmd == "claim":
            print(json.dumps(record_claim(root, quote=a.quote, where=a.where, recorded_by=a.recorded_by), indent=1,
                             ensure_ascii=False))
            print("Recorded as a CLAIM. It does not put the text in force; only Yashu's signature does.")
            return 0
        if a.cmd == "keygen":
            key = Path(a.key)
            if not key.exists():
                key.parent.mkdir(parents=True, exist_ok=True)
                print("Choose a passphrase only you know. Never type it into any agent chat.")
                subprocess.run([_ssh_keygen(), "-t", "ed25519", "-C", "yashu-constitution", "-f", str(key)],
                               check=True)
            print("Owner key fingerprint (write it down):", register_owner_key(root, key.with_suffix(".pub")))
            return 0
        if a.cmd == "sign":
            rec = sign(root, Path(a.key))
            print(f"Signed {rec['sha256'][:12]} with {rec['fingerprint']}")
            return 0
    except (ApprovalError, LedgerBroken, subprocess.CalledProcessError, RuntimeError) as exc:
        print(f"REFUSED: {exc}")
        return 2
    others = [p for p in {paths.main_checkout()} if Path(p).resolve() != Path(root).resolve()]
    st, details = check(root, others)
    print(st)
    for d in details:
        print(" ", d)
    return {"PASS": 0, "UNREVIEWED": 2, "MISSING": 3, "FAIL": 1}[st]


if __name__ == "__main__":
    raise SystemExit(main())
