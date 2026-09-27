"""
research/data/archive_audit.py
==============================
Claude's machine audit of the NSE archive download (data program JOBs 1-3, Antigravity). Read-only: it never
changes a downloaded file or the manifest. Framework rule F14: no dataset feeds a strategy before this says PASS.

    python -m research.data.archive_audit            # prints the verdict, writes research/outputs/audit/...json

For every SAVED manifest line:
  FILE_MISSING, SHA_MISMATCH       the file on disk is not the file the manifest recorded
  UNREADABLE, EMPTY                not a readable bhavcopy / delivery file, or no rows
  DATE_MISMATCH                    the date inside the file is not the manifest's trade_date
  CONFLICTING_SAVES                one dataset and date saved twice with different bytes
Across lines:
  DATASET_MISMATCH                 a date SAVED in one dataset but 404 in another (a holiday is 404 in all)
  MISSING_SESSION                  CM files do not chain (each day's PREVCLOSE must equal the prior file's CLOSE):
                                   a session is missing; the candidates are the days in between that were not
                                   proven holidays (typically a weekend Muhurat or Budget session)
  CHAIN_UNKNOWN                    the chain could not be checked
  PACING                           two request starts (requested_at) under 4 s apart (data program rule 4)
  BLOCKED                          an HTTP 403/429 or STOPPED line (rule 6)
  OVER_CAP                         more than 1,000 requests on one calendar day (rule 5)
Verdict PASS only with no problem at all.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from research.framework.market import MarketFiles, parse_bhavcopy, unpack

DATASETS = ("cm_bhavcopy", "fo_bhavcopy", "mto")
MIN_GAP_S = 4.0
DAILY_CAP = 1000


EXPECTED = (date(2005, 1, 1), date(2021, 9, 30))          # the data program's JOBs 1-3 range
CHAIN_COLUMNS = ("TckrSymb", "SctySrs", "ClsPric", "PrvsClsgPric")


class _ArchiveCM(MarketFiles):
    """MarketFiles over the archive's CM zips, located through the manifest. Reads only the four columns the chain
    needs, from bytes whose SHA-256 is re-checked at the read (nothing is cached across runs)."""

    def __init__(self, history: Path, cm_files: Dict[date, Path], sha: Dict[Path, str]) -> None:
        super().__init__(history)
        self._files, self._sha = cm_files, sha
        self._sessions = sorted(cm_files)

    def cm_path(self, d: date) -> Path:
        return self._files.get(d, self.h / "__absent__" / d.isoformat())

    def _read(self, path: Path):
        raw = Path(path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != self._sha.get(Path(path)):
            raise ValueError(f"{path} changed during the audit")
        return parse_bhavcopy(raw, str(path), columns=CHAIN_COLUMNS)


def _mto_date(content: bytes) -> Optional[str]:
    for ln in content.decode("utf-8", errors="replace").splitlines()[:4]:
        f = [x.strip() for x in ln.split(",")]
        if len(f) >= 3 and f[0] == "10" and f[1].upper() == "MTO":
            return datetime.strptime(f[2], "%d%m%Y").date().isoformat()
    return None


# Every column a strategy or the desk reads (UDiFF names; legacy files are mapped). A file without one of them is
# not usable market data, whatever its date says (CODEX-FRAMEWORK-002).
REQUIRED = {"cm_bhavcopy": ("TckrSymb", "SctySrs", "OpnPric", "HghPric", "LwPric", "ClsPric", "PrvsClsgPric",
                            "TtlTrfVal", "TradDt"),
            "fo_bhavcopy": ("TckrSymb", "FinInstrmTp", "XpryDt", "ClsPric", "TradDt")}
MTO_MIN_FIELDS = 7


def _check_bytes(raw: bytes, dataset: str, source: str) -> Dict[str, Any]:
    """{'dates': set of ISO dates inside, 'rows': n} or {'error': ...}, from exactly the bytes that were hashed.
    Reads only the required columns, and refuses a file that lacks any of them."""
    try:
        if dataset == "mto":
            content = unpack(raw, source)
            d = _mto_date(content)
            rows = sum(1 for ln in content.decode("utf-8", errors="replace").splitlines()
                       if ln.startswith("20,") and len(ln.split(",")) >= MTO_MIN_FIELDS)
            return {"dates": {d} if d else set(), "rows": rows}
        need = REQUIRED[dataset]
        df = parse_bhavcopy(raw, source, columns=need)
        missing = [c for c in need if c not in df.columns]
        if missing:
            return {"error": f"MISSING_COLUMNS {missing}"}
        return {"dates": set(df["TradDt"]), "rows": int(len(df))}
    except Exception as exc:                          # BadZipFile, UnknownFormat, parse errors: all UNREADABLE
        return {"error": f"{type(exc).__name__}: {exc}"}


def audit(history: Path, cache: Optional[Path] = None, expected: tuple = EXPECTED) -> Dict[str, Any]:
    """Verdict: FAIL on any problem (including no files at all); PASS only when clean AND every weekday of `expected`
    is accounted for in the CM manifest (SAVED or 404); PASS_PARTIAL when clean but not complete.
    `cache` is accepted for old callers and ignored: every run re-reads every file (CODEX-FRAMEWORK-001 A5: a cache in
    a writable folder could certify bytes it never checked)."""
    h = Path(history)
    m = h / "raw" / "nse_archive" / "manifest.jsonl"
    problems: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []
    if not m.exists():
        return {"verdict": "FAIL", "problems": [{"kind": "NO_MANIFEST", "path": str(m)}], "files_checked": 0,
                "sessions": 0, "holidays": []}
    for i, ln in enumerate(m.read_text(encoding="utf-8").splitlines(), 1):
        if ln.strip():
            try:
                rows.append(json.loads(ln))
            except ValueError:
                problems.append({"kind": "MANIFEST_UNREADABLE", "line": i})

    # ---- files
    saved = [r for r in rows if r.get("outcome") == "SAVED"]
    by_key: Dict[Any, set] = defaultdict(set)
    for r in saved:
        by_key[(r.get("dataset"), r.get("trade_date"))].add(r.get("sha256"))
    for (ds, td), shas in by_key.items():
        if len(shas) > 1:
            problems.append({"kind": "CONFLICTING_SAVES", "dataset": ds, "trade_date": td, "sha256": sorted(shas)})
    checked, cm_files, cm_sha, sha_by_path = set(), {}, {}, {}
    for r in saved:
        key = (r.get("saved_path"), r.get("sha256"))
        if key in checked:
            continue
        checked.add(key)
        ds, td = r.get("dataset"), r.get("trade_date")
        p = h / str(r.get("saved_path") or "")
        base = {"dataset": ds, "trade_date": td, "saved_path": r.get("saved_path")}
        if not r.get("saved_path") or not p.is_file():
            problems.append({"kind": "FILE_MISSING", **base})
            continue
        raw = p.read_bytes()
        if hashlib.sha256(raw).hexdigest() != r.get("sha256"):
            problems.append({"kind": "SHA_MISMATCH", **base})
            continue
        res = _check_bytes(raw, ds, str(p))
        if "error" in res:
            problems.append({"kind": "UNREADABLE", **base, "error": res["error"]})
        elif res["rows"] == 0:
            problems.append({"kind": "EMPTY", **base})
        elif res["dates"] != {td}:
            problems.append({"kind": "DATE_MISMATCH", **base, "inside": sorted(res["dates"])[:3]})
        elif ds == "cm_bhavcopy":
            cm_files[date.fromisoformat(td)] = p
            cm_sha[date.fromisoformat(td)] = str(r.get("sha256"))
            sha_by_path[p] = str(r.get("sha256"))
    if not checked:
        problems.append({"kind": "NO_FILES", "detail": "no SAVED file in the manifest: nothing to certify"})

    # ---- coverage across datasets
    outcome: Dict[str, Dict[str, str]] = defaultdict(dict)
    for r in rows:
        o, ds, td = r.get("outcome"), r.get("dataset"), r.get("trade_date")
        if ds in DATASETS and td and o in ("SAVED", "MISSING_404") and outcome[td].get(ds) != "SAVED":
            outcome[td][ds] = o
    holidays = []
    for td, per in sorted(outcome.items()):
        vals = set(per.values())
        if vals == {"MISSING_404"} and set(per) == set(DATASETS):
            holidays.append(td)
        elif "SAVED" in vals and "MISSING_404" in vals:
            problems.append({"kind": "DATASET_MISMATCH", "trade_date": td, "outcomes": per})

    # ---- missing sessions (the chain)
    # A gap with a weekday nobody has requested yet is "not yet downloaded", not a missing session.
    arch = _ArchiveCM(h, cm_files, sha_by_path)
    ss = arch.sessions()
    hol = {date.fromisoformat(x) for x in holidays}
    attempted = {date.fromisoformat(td) for td in outcome if len(td) == 10}
    not_yet: List[List[str]] = []
    for a, b in zip(ss[:-1], ss[1:]):
        between = [a + timedelta(days=k) for k in range(1, (b - a).days)]
        if any(d.weekday() < 5 and d not in attempted for d in between):
            not_yet.append([a.isoformat(), b.isoformat()])
            continue
        c = arch.chain(a, b)
        if c["ok"] is True:
            continue
        # A day that was 404 in every dataset looked like a holiday; a broken chain across it says it traded (the
        # archive lacks its files), so it stays a candidate and is named separately.
        item = {"between": [a.isoformat(), b.isoformat()], "matched": c["matched"], "compared": c["compared"],
                "candidates": [d.isoformat() for d in between][:10],
                "all_404_but_traded": [d.isoformat() for d in between if d in hol]}
        problems.append({"kind": "MISSING_SESSION" if c["ok"] is False else "CHAIN_UNKNOWN", **item})

    # ---- pacing, blocks, caps
    starts = sorted(datetime.fromisoformat(r["requested_at"]) for r in rows if r.get("requested_at"))
    short = [(a, b) for a, b in zip(starts[:-1], starts[1:]) if (b - a).total_seconds() < MIN_GAP_S]
    if short:
        problems.append({"kind": "PACING", "count": len(short), "first": short[0][1].isoformat()})
    blocked = [r for r in rows if str(r.get("outcome", "")).startswith("STOPPED") or r.get("http_status") in (403, 429)]
    if blocked:
        problems.append({"kind": "BLOCKED", "count": len(blocked),
                         "first": blocked[0].get("requested_at") or blocked[0].get("fetched_at")})
    per_day = Counter(str(r.get("requested_at") or r.get("fetched_at") or "")[:10] for r in rows
                      if r.get("outcome") != "SKIPPED_ALREADY_SAVED")
    for d, n in sorted(per_day.items()):
        if d and n > DAILY_CAP:
            problems.append({"kind": "OVER_CAP", "day": d, "requests": n})

    coverage: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for (ds, td) in by_key:
        coverage[str(td)[:4]][ds] += 1
    # ---- completeness (CODEX-FRAMEWORK-001 A1): PASS means complete, never "clean so far"
    # a weekday is accounted for only when ALL THREE datasets have an outcome for it (CODEX-FRAMEWORK-002)
    cm_done = {date.fromisoformat(td) for td, per in outcome.items() if len(td) == 10 and set(DATASETS) <= set(per)}
    lo, hi = expected
    want = [lo + timedelta(days=k) for k in range((hi - lo).days + 1)]
    want = [d for d in want if d.weekday() < 5]
    missing = [d for d in want if d not in cm_done]
    complete = not missing and not not_yet
    verdict = "FAIL" if problems else ("PASS" if complete else "PASS_PARTIAL")
    return {"verdict": verdict, "problems": problems, "files_checked": len(checked),
            "coverage": {"expected": [lo.isoformat(), hi.isoformat()], "weekdays_expected": len(want),
                         "weekdays_accounted": len(want) - len(missing),
                         "first_unaccounted": missing[0].isoformat() if missing else None, "complete": complete},
            "sessions": len(ss), "holidays": holidays, "not_yet_downloaded": not_yet, "outcomes": dict(Counter(r.get("outcome") for r in rows)),
            "first_trade_date": ss[0].isoformat() if ss else None, "last_trade_date": ss[-1].isoformat() if ss else None,
            "saved_by_year": {y: dict(v) for y, v in sorted(coverage.items())}, "requests_per_day": dict(per_day)}


def main(argv: Optional[List[str]] = None) -> int:
    from research.data import paths

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Audit the NSE archive download (read-only)")
    ap.add_argument("--history", default=None)
    args = ap.parse_args(argv)
    h = Path(args.history) if args.history else paths.history_dir()
    out = paths.ensure(paths.outputs_dir() / "audit")
    rep = audit(h)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (out / f"archive_audit_{stamp}.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    cov = rep.get("coverage", {})
    print(f"VERDICT {rep['verdict']}: {rep['files_checked']} files, {rep['sessions']} CM sessions "
          f"{rep.get('first_trade_date')}..{rep.get('last_trade_date')}, holidays {len(rep['holidays'])}, "
          f"outcomes {rep.get('outcomes')}")
    if cov:
        print(f"COVERAGE {cov['weekdays_accounted']}/{cov['weekdays_expected']} weekdays of {cov['expected'][0]}.."
              f"{cov['expected'][1]} accounted; first unaccounted {cov['first_unaccounted']}; "
              f"{len(rep.get('not_yet_downloaded', []))} not-yet-downloaded gaps"
              + (" (PASS_PARTIAL = clean so far, NOT complete: not yet usable for a full-period study)"
                 if rep["verdict"] == "PASS_PARTIAL" else ""))
    for k, n in Counter(p["kind"] for p in rep["problems"]).items():
        print(f"  {k}: {n}")
    for p in rep["problems"][:20]:
        print("   ", json.dumps(p, default=str))
    print(f"report: {out / f'archive_audit_{stamp}.json'}")
    return 0 if rep["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
