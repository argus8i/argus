"""
research/studies/prereg_io.py
=============================
Load pre-registration YAML files and verify their locks (plan P3.7 HoldoutGuard, P7.3).

PyYAML is not a project dependency (the approved research dependency is pyarrow only), so this module
parses the strict YAML subset the pre-registration files use:
- block mappings by indentation (spaces only), `key: value`, `key:` + nested block, `key: >` folded text;
- block sequences of scalars or flow collections (`- item`);
- flow sequences `[a, b]` and flow mappings `{k: v}`, nested;
- scalars: null / ~, true / false, integers, floats, single- or double-quoted strings, plain strings;
- `#` comments (outside quotes, preceded by whitespace or at line start).
Anything else raises PreregFormatError instead of guessing. When PyYAML is importable, the tests check
that both parsers return identical objects for every committed pre-registration.

Lock file (`<id>.lock`, JSON): {"id", "yaml_sha256", "commit", "locked_at"}. The hash is taken over
the YAML text with line endings normalised to LF, so a Windows checkout with autocrlf hashes the same.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PREREG_DIR = Path(__file__).resolve().parent / "prereg"


class PreregFormatError(ValueError):
    pass


# ---------------------------------------------------------------- scalars and flow collections
_INT = re.compile(r"^[-+]?\d+$")
_FLOAT = re.compile(r"^[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?$")


def _scalar(tok: str) -> Any:
    t = tok.strip()
    if t in ("", "~", "null", "Null", "NULL"):
        return None
    if t in ("true", "True", "TRUE"):
        return True
    if t in ("false", "False", "FALSE"):
        return False
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        body = t[1:-1]
        return body.replace("''", "'") if t[0] == "'" else bytes(body, "utf-8").decode("unicode_escape")
    if _INT.match(t):
        return int(t)
    if _FLOAT.match(t):
        return float(t)
    return t


class _Flow:
    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0

    def ws(self) -> None:
        while self.i < len(self.s) and self.s[self.i] in " \t":
            self.i += 1

    def parse(self) -> Any:
        self.ws()
        c = self.s[self.i] if self.i < len(self.s) else ""
        if c == "[":
            return self.seq()
        if c == "{":
            return self.mapping()
        return self.atom(stops=",]}")

    def atom(self, stops: str) -> Any:
        self.ws()
        if self.i < len(self.s) and self.s[self.i] in "\"'":
            q = self.s[self.i]
            j = self.i + 1
            while j < len(self.s):
                if self.s[j] == q and not (q == "'" and j + 1 < len(self.s) and self.s[j + 1] == "'"):
                    break
                j += 2 if (q == "'" and self.s[j] == "'") else 1
            if j >= len(self.s):
                raise PreregFormatError(f"unterminated quote in {self.s!r}")
            tok = self.s[self.i:j + 1]
            self.i = j + 1
            return _scalar(tok)
        j = self.i
        while j < len(self.s) and self.s[j] not in stops:
            j += 1
        tok = self.s[self.i:j]
        self.i = j
        return _scalar(tok)

    def seq(self) -> List[Any]:
        self.i += 1
        out: List[Any] = []
        while True:
            self.ws()
            if self.i >= len(self.s):
                raise PreregFormatError(f"unterminated [ in {self.s!r}")
            if self.s[self.i] == "]":
                self.i += 1
                return out
            out.append(self.parse())
            self.ws()
            if self.i < len(self.s) and self.s[self.i] == ",":
                self.i += 1

    def mapping(self) -> Dict[str, Any]:
        self.i += 1
        out: Dict[str, Any] = {}
        while True:
            self.ws()
            if self.i >= len(self.s):
                raise PreregFormatError(f"unterminated {{ in {self.s!r}")
            if self.s[self.i] == "}":
                self.i += 1
                return out
            key = self.atom(stops=":")
            if self.i >= len(self.s) or self.s[self.i] != ":":
                raise PreregFormatError(f"flow mapping key without ':' in {self.s!r}")
            self.i += 1
            self.ws()
            c = self.s[self.i] if self.i < len(self.s) else ""
            out[str(key)] = self.seq() if c == "[" else self.mapping() if c == "{" else self.atom(stops=",}")
            self.ws()
            if self.i < len(self.s) and self.s[self.i] == ",":
                self.i += 1


def _value(text: str) -> Any:
    t = text.strip()
    if t.startswith("[") or t.startswith("{"):
        f = _Flow(t)
        v = f.parse()
        f.ws()
        if f.i != len(f.s):
            raise PreregFormatError(f"trailing text after flow collection: {t!r}")
        return v
    return _scalar(t)


# ---------------------------------------------------------------- block structure
def _strip_comment(line: str) -> str:
    q = ""
    for k, ch in enumerate(line):
        if q:
            if ch == q:
                q = ""
            continue
        if ch in "\"'":
            q = ch
        elif ch == "#" and (k == 0 or line[k - 1] in " \t"):
            return line[:k].rstrip()
    return line.rstrip()


def _lines(text: str) -> List[Tuple[int, str, str]]:
    """(indent, content, raw) for significant lines; raw kept for folded scalars."""
    out = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise PreregFormatError("tabs are not allowed for indentation")
        content = _strip_comment(raw)
        if content.strip():
            out.append((len(raw) - len(raw.lstrip(" ")), content.strip(), raw))
    return out


def _split_key(content: str) -> Tuple[str, str]:
    m = re.match(r'^("[^"]*"|\'[^\']*\'|[^:#\[\]{}]+?):(\s+|$)(.*)$', content)
    if not m:
        raise PreregFormatError(f"expected 'key: value', got {content!r}")
    return str(_scalar(m.group(1))), m.group(3)


def _block(lines: List[Tuple[int, str, str]], i: int, indent: int) -> Tuple[Any, int]:
    if i >= len(lines):
        return None, i
    if lines[i][1].startswith("- ") or lines[i][1] == "-":
        out_l: List[Any] = []
        while i < len(lines) and lines[i][0] == indent and (lines[i][1].startswith("- ") or lines[i][1] == "-"):
            item = lines[i][1][1:].strip()
            if not item or re.match(r"^[^\[{\"'][^:]*:\s", item + " ") and not item.startswith("{"):
                raise PreregFormatError(f"only scalar or flow items are supported in sequences: {lines[i][1]!r}")
            out_l.append(_value(item))
            i += 1
        return out_l, i
    out: Dict[str, Any] = {}
    while i < len(lines) and lines[i][0] == indent:
        key, rest = _split_key(lines[i][1])
        if key in out:
            raise PreregFormatError(f"duplicate key {key!r}")
        i += 1
        if rest.strip() in (">", ">-", "|", "|-"):
            parts = []
            while i < len(lines) and lines[i][0] > indent:
                parts.append(lines[i][2].strip())
                i += 1
            joiner = " " if rest.strip().startswith(">") else "\n"
            out[key] = joiner.join(parts) + ("" if rest.strip().endswith("-") else "\n")
        elif rest.strip() == "":
            if i < len(lines) and lines[i][0] > indent:
                out[key], i = _block(lines, i, lines[i][0])
            elif i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
                out[key], i = _block(lines, i, indent)
            else:
                out[key] = None
        else:
            out[key] = _value(rest)
    if i < len(lines) and lines[i][0] > indent:
        raise PreregFormatError(f"unexpected indentation at {lines[i][1]!r}")
    return out, i


def loads(text: str) -> Any:
    lines = _lines(text)
    if not lines:
        return None
    value, i = _block(lines, 0, lines[0][0])
    if i != len(lines):
        raise PreregFormatError(f"could not parse from line {lines[i][1]!r}")
    return value


def load(path: Path | str) -> Dict[str, Any]:
    data = loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise PreregFormatError(f"{path}: top level must be a mapping")
    return data


# ---------------------------------------------------------------- hashing and locks
def normalised_sha256(path: Path | str) -> str:
    """SHA-256 over the raw bytes with CRLF -> LF. Done on bytes, not via read_text, so the result does not
    depend on Python's universal-newline translation (which would also rewrite lone CRs)."""
    data = Path(path).read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class LockStatus:
    valid: bool
    reason: str
    lock: Optional[Dict[str, Any]] = None


def verify_lock(yaml_path: Path | str, lock_path: Optional[Path | str] = None) -> LockStatus:
    """A lock is valid only if it exists, names the same id, carries a 40-hex commit, the YAML status is
    LOCKED, and the YAML text still hashes to the recorded value."""
    yaml_path = Path(yaml_path)
    lock_path = Path(lock_path) if lock_path else yaml_path.with_suffix(".lock")
    if not yaml_path.exists():
        return LockStatus(False, "PREREG_MISSING")
    if not lock_path.exists():
        return LockStatus(False, "LOCK_MISSING")
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        spec = load(yaml_path)
    except (ValueError, OSError) as exc:
        return LockStatus(False, f"UNREADABLE: {exc}")
    if lock.get("id") != spec.get("id"):
        return LockStatus(False, "ID_MISMATCH", lock)
    if not re.fullmatch(r"[0-9a-f]{40}", str(lock.get("commit", ""))):
        return LockStatus(False, "COMMIT_INVALID", lock)
    if spec.get("status") != "LOCKED":
        return LockStatus(False, "STATUS_NOT_LOCKED", lock)
    if lock.get("yaml_sha256") != normalised_sha256(yaml_path):
        return LockStatus(False, "HASH_MISMATCH", lock)
    return LockStatus(True, "OK", lock)
