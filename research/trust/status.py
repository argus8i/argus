"""
research/trust/status.py
========================
T1: one command, one truth (trust plan v2, P1).

    python -m research.trust.status            # prints the report, writes <outputs>/trust/status_latest.json

Reads only machine records, never prose (notes, PLAN_STATUS.md, messages, register 'notes' fields, commit messages):
  strategies  the decision register is REBUILT from the append-only decision records (research/decision/records) and must match;
              every lock verifies; every holdout_done marker is backed by a register entry citing its lock.
  snapshots   every inventory snapshot is fully re-verified (snapshot.verify) against its pinned content hash.
  datasets    every inventory dataset is present (coverage audits arrive in P2).
  trials      the trials registry has its schema, unique ids and R-basis labels.
  code        each checkout's governed commits after its baseline are covered by exact APPROVED review records (T7);
              dirty governed files are listed.
  paper gate  no ALLOW_LIVE = True anywhere in antigravity/; the research ShadowRunner refuses live (AGENTS.md Rule 1).
  isolation   no Track 2 strategy id in the Track 1 log (AGENTS.md Rule 11).

Exit codes (the highest class present wins): 4 internal error (a checker failed; never green), 1 integrity failure,
2 unreviewed code, 3 missing or stale input, 0 all PASS. status.json is written in every case and carries the
checker's own code identity.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

IST = timezone(timedelta(hours=5, minutes=30))
EXIT_OK, EXIT_INTEGRITY, EXIT_UNREVIEWED, EXIT_MISSING, EXIT_INTERNAL = 0, 1, 2, 3, 4
STATUS_EXIT = {"PASS": EXIT_OK, "FAIL": EXIT_INTEGRITY, "UNREVIEWED": EXIT_UNREVIEWED, "MISSING": EXIT_MISSING,
               "STALE": EXIT_MISSING, "ERROR": EXIT_INTERNAL}
PRIORITY = [EXIT_INTERNAL, EXIT_INTEGRITY, EXIT_UNREVIEWED, EXIT_MISSING]
TRUST_DIR = Path(__file__).resolve().parent
INVENTORY = TRUST_DIR / "inventory.json"
PASSED_STATES = {"SHADOW", "PROMOTE_TO_PAPER"}


def load_inventory(path: Path = INVENTORY) -> Dict[str, Any]:
    inv = json.loads(Path(path).read_text(encoding="utf-8"))
    if inv.get("schema_version") != 1:
        raise ValueError(f"unknown inventory schema_version {inv.get('schema_version')!r}")
    return inv


def checker_identity() -> str:
    h = hashlib.sha256()
    for p in sorted(TRUST_DIR.glob("*.py")) + [INVENTORY]:
        h.update(p.name.encode())
        h.update(p.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()


# ---------------------------------------------------------------------------------------------- pure checks
def reconstruct_register(register: Mapping[str, Any], records: Sequence[Mapping[str, Any]],
                         legacy: Sequence[str]) -> List[str]:
    """Replay the decision records (oldest first) and compare with the decision register. Legacy strategies start
    UNVERIFIED; any other strategy must enter through a record from None."""
    problems: List[str] = []
    state: Dict[str, Optional[str]] = {s: "UNVERIFIED" for s in legacy}
    # Oldest first. promotion.py can write a registration and its first transition in the same second, so on a
    # timestamp tie the record that creates a strategy (from None) comes first; nothing else is reordered.
    for r in sorted(records, key=lambda r: (str(r.get("at")), r.get("from") is not None, str(r.get("action")))):
        sid, frm, to = r.get("strategy_id"), r.get("from"), r.get("to")
        cur = state.get(sid)
        if frm != cur:
            problems.append(f"{sid}: record {r.get('action')} at {r.get('at')} moves from {frm!r}, "
                            f"but the replayed state is {cur!r}")
        state[sid] = to
    reg = register.get("strategies", {})
    for sid in sorted(set(reg) | set(state)):
        if sid not in reg:
            problems.append(f"{sid}: in the decision records but not in the register")
        elif state.get(sid) is None:
            problems.append(f"{sid}: in the register without any decision record (not a legacy strategy)")
        elif reg[sid].get("status") != state[sid]:
            problems.append(f"{sid}: the register says {reg[sid].get('status')!r}, the records say {state[sid]!r}")
    return problems


_LIVE = re.compile(r"\bALLOW_LIVE\b\s*(?::\s*bool)?\s*=\s*True\b")


def check_paper_gate(root: Path) -> List[str]:
    problems = []
    base = Path(root) / "antigravity"
    for p in sorted(base.rglob("*.py")) if base.exists() else []:
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if _LIVE.search(line.split("#", 1)[0]):
                problems.append(f"{p.relative_to(root).as_posix()}:{n}: ALLOW_LIVE = True")
    return problems


# ---------------------------------------------------------------------------------------------- context checks
def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True,
                          encoding="utf-8", errors="replace").stdout.strip()


def _root(ctx: Mapping[str, Any], name: str) -> Path:
    return Path(ctx["roots"][name])


def check_strategies(ctx: Mapping[str, Any]) -> Tuple[str, List[str]]:
    from research.studies import prereg_io

    from research.decision import promotion

    reg = promotion.load_register(Path(ctx["register_path"]))            # read only, through the one writer
    records = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(ctx["records_dir"]).glob("*.json"))]
    problems = reconstruct_register(reg, records, ctx["inventory"]["legacy_strategies"])
    prereg = Path(ctx["prereg_dir"])
    cited = {v.get("prereg_sha256") for v in reg["strategies"].values()
             if v.get("status") in PASSED_STATES | {"REJECTED"} and v.get("evidence") == "E1_HOLDOUT"}
    for lock in sorted(prereg.glob("*.lock")):
        spec = lock.with_suffix(".yaml")
        st = prereg_io.verify_lock(spec)
        if not st.valid:
            problems.append(f"{lock.name}: lock does not verify ({st.reason})")
            continue
        if lock.with_suffix(".holdout_done").exists() and st.lock["yaml_sha256"] not in cited:
            problems.append(f"{lock.with_suffix('.holdout_done').name}: no register entry cites lock "
                            f"{st.lock['yaml_sha256'][:12]}")
    locked = {json.loads(p.read_text(encoding="utf-8"))["yaml_sha256"] for p in prereg.glob("*.lock")}
    for sid, v in reg["strategies"].items():
        if v.get("evidence") == "E1_HOLDOUT" and v.get("prereg_sha256") not in locked:
            problems.append(f"{sid}: E1_HOLDOUT evidence cites a pre-registration with no lock file")
    counts: Dict[str, int] = {}
    for v in reg["strategies"].values():
        counts[v.get("status")] = counts.get(v.get("status"), 0) + 1
    ctx["summary"]["strategies"] = counts
    ctx["summary"]["strategies_passed"] = sum(n for s, n in counts.items() if s in PASSED_STATES)
    return ("FAIL" if problems else "PASS"), problems


def check_snapshots(ctx: Mapping[str, Any]) -> Tuple[str, List[str]]:
    from research.data import snapshot

    problems = []
    for s in ctx["inventory"]["snapshots"]:
        path = Path(ctx["snapshots_root"]) / s["name"]
        if not path.exists():
            problems.append(f"{s['name']}: missing (pinned evidence can no longer be re-checked)")
            continue
        try:
            info = snapshot.verify(path)
        except Exception as exc:                            # tampered or unsealed is an integrity failure, not a crash
            problems.append(f"{s['name']}: {type(exc).__name__}: {exc}")
            continue
        if info["content_sha256"] != s["content_sha256"]:
            problems.append(f"{s['name']}: content {info['content_sha256'][:12]} is not the pinned {s['content_sha256'][:12]}")
    return ("FAIL" if problems else "PASS"), problems


def check_datasets(ctx: Mapping[str, Any]) -> Tuple[str, List[str]]:
    missing = [f"{d['id']}: {d['path']} not found" for d in ctx["inventory"]["datasets"]
               if not (Path(ctx["history"]) / d["path"]).exists()]
    return ("MISSING" if missing else "PASS"), missing + ["coverage audits: not yet built (trust plan P2)"]


def check_trials(ctx: Mapping[str, Any]) -> Tuple[str, List[str]]:
    header = ["trial_id", "date_run", "strategy_id", "variant", "data_span", "sample_id", "n_trades", "mean_net_r",
              "r_basis", "sr_per_trade", "source", "agent", "notes"]
    with open(ctx["trials_path"], encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    problems = []
    if not rows or list(rows[0].keys()) != header:
        problems.append("trials registry header is not the schema")
    ids = [r["trial_id"] for r in rows]
    if len(set(ids)) != len(ids):
        problems.append("duplicate trial ids")
    for r in rows:
        if not (r["r_basis"] in ("trigger", "stop_limit") or
                (r["r_basis"] == "none" and r["mean_net_r"] == "" and r["notes"].startswith("E0_EXPLORATORY"))):
            problems.append(f"{r['trial_id']}: r_basis {r['r_basis']!r} breaks the schema")
    ctx["summary"]["trials"] = len(rows)
    return ("FAIL" if problems else "PASS"), problems


def check_code(ctx: Mapping[str, Any]) -> Tuple[str, List[str]]:
    from research.trust import review_check, schemas

    inv = ctx["inventory"]
    scope, excl = inv["governed_scope"], inv["excluded_scope"]
    ledger = _root(ctx, inv["review_ledger"]["root"]) / inv["review_ledger"]["path"]
    try:
        reviews = schemas.read_ledger(ledger)
    except schemas.LedgerError as exc:
        return "FAIL", [f"review ledger: {exc}"]
    details, worst = [f"review ledger: {len(reviews)} record(s) at {inv['review_ledger']['path']}"], "PASS"
    for co in inv["checkouts"]:
        repo = _root(ctx, co["root"])
        branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        head = _git(repo, "rev-parse", "HEAD")
        dirty = [ln[3:] for ln in _git(repo, "status", "--porcelain").splitlines()
                 if ln.strip() and review_check.governed(ln[3:].strip().strip('"'), scope, excl)]
        unrev = review_check.unreviewed_commits(repo, co["baseline"], "HEAD", reviews, scope, excl)
        legacy = len(_git(repo, "rev-list", f"main..{co['baseline']}").split()) if co["expected_branch"] != "main" else 0
        line = (f"{co['name']} ({co['role']}): branch {branch} (expected {co['expected_branch']}), HEAD {head[:10]}, "
                f"unreviewed governed commits {len(unrev)}, dirty governed files {len(dirty)}, "
                f"legacy prose-reviewed commits {legacy}")
        details.append(line)
        details += [f"  UNREVIEWED {u['commit'][:10]} {u['subject'][:70]}" for u in unrev[:15]]
        details += [f"  DIRTY {d}" for d in dirty[:15]]
        if unrev or dirty or branch != co["expected_branch"]:
            worst = "UNREVIEWED"
    return worst, details


def check_paper(ctx: Mapping[str, Any]) -> Tuple[str, List[str]]:
    problems = check_paper_gate(_root(ctx, "main_checkout"))
    try:
        from research.shadow.runner import RunnerConfig

        if RunnerConfig().allow_live is not False:
            problems.append("research ShadowRunner: allow_live defaults to something other than False")
    except Exception as exc:
        problems.append(f"research ShadowRunner: cannot confirm the live refusal ({type(exc).__name__}: {exc})")
    return ("FAIL" if problems else "PASS"), problems or ["no ALLOW_LIVE = True; ShadowRunner refuses live"]


def check_paper_desks(ctx: Mapping[str, Any]) -> Tuple[str, List[str]]:
    """Every registered paper strategy obeys the framework rules (research/framework/RULES.md): locked, committed,
    unchanged pre-registration; Track 2 journal location; intact journals; and no broker code in the framework or
    in any registered plug-in."""
    import inspect

    from research.framework import rules
    from research.framework.strategy import registered

    strategies = ctx.get("paper_strategies")
    strategies = registered() if strategies is None else strategies
    check_git = ctx.get("paper_check_git", True)
    problems: List[str] = []
    for s in strategies:
        problems += rules.strategy_problems(s, check_git=check_git)
    fw = Path(rules.__file__).parent
    files = sorted(fw.glob("*.py")) + sorted({Path(inspect.getfile(type(s))) for s in strategies})
    problems += [f"{h['file']}:{h['line']}: broker-like code: {h['text']}" for h in rules.paper_only_scan(files)]
    ok = [f"{len(strategies)} paper strategies obey the framework rules; {len(files)} files free of broker code"]
    return ("FAIL" if problems else "PASS"), problems or ok


def check_isolation(ctx: Mapping[str, Any]) -> Tuple[str, List[str]]:
    inv = ctx["inventory"]
    log = _root(ctx, "main_checkout") / inv["track1_log"]
    if not log.exists():
        return "PASS", [f"{inv['track1_log']} absent"]
    text = log.read_text(encoding="utf-8", errors="replace")
    hits = [s for s in inv["track2_ids_forbidden_in_track1_log"] if s in text]
    return ("FAIL" if hits else "PASS"), [f"Track 2 ids in the Track 1 log: {hits}"] if hits else []


CHECKS: List[Tuple[str, Callable[[Dict[str, Any]], Tuple[str, List[str]]]]] = [
    ("strategies", check_strategies), ("snapshots", check_snapshots), ("datasets", check_datasets),
    ("trials", check_trials), ("code_review", check_code), ("paper_only_gate", check_paper),
    ("track_isolation", check_isolation), ("paper_desks", check_paper_desks),
]


# ---------------------------------------------------------------------------------------------- run
def build_context() -> Dict[str, Any]:
    from research.data import paths, snapshot
    from research.decision import promotion
    from research.studies import prereg_io

    main = paths.main_checkout()
    history = main / "shared" / "track2_liquid" / "history"
    return {"inventory": load_inventory(), "roots": {"main_checkout": str(main), "repo_root": str(paths.repo_root())},
            "history": str(history), "snapshots_root": str(snapshot.snapshots_root(history)),
            "register_path": str(promotion.REGISTER_PATH), "records_dir": str(promotion.RECORDS_DIR),
            "prereg_dir": str(prereg_io.PREREG_DIR),
            "trials_path": str(paths.repo_root() / "research" / "evidence" / "trials_registry.csv")}


def run(ctx: Optional[Dict[str, Any]] = None, out_dir: Optional[Path] = None) -> Tuple[Dict[str, Any], int]:
    started = datetime.now(IST)
    results, codes = [], set()
    try:
        ctx = build_context() if ctx is None else dict(ctx)
    except Exception as exc:
        ctx = {}
        results.append({"check": "context", "status": "ERROR", "details": [f"{type(exc).__name__}: {exc}"]})
        codes.add(EXIT_INTERNAL)
    ctx.setdefault("summary", {})
    if not results:
        for name, fn in CHECKS:
            try:
                status, details = fn(ctx)
                if status not in STATUS_EXIT:
                    raise ValueError(f"checker returned unknown status {status!r}")
            except Exception as exc:
                status = "ERROR"
                details = [f"{type(exc).__name__}: {exc}", traceback.format_exc(limit=3)]
            results.append({"check": name, "status": status, "details": list(details)})
            codes.add(STATUS_EXIT[status])
    try:
        ident = checker_identity()
    except Exception as exc:                  # CODEX-TRUST-P1-001 #3: an identity failure is an ERROR check too
        ident = f"UNKNOWN ({type(exc).__name__}: {exc})"
        results.append({"check": "checker_identity", "status": "ERROR", "details": [ident]})
        codes.add(EXIT_INTERNAL)
    code = next((c for c in PRIORITY if c in codes), EXIT_OK)     # decided once, after every check
    s = ctx.get("summary", {})
    verdict = (f"strategies passed: {s.get('strategies_passed', 'UNKNOWN')}; "
               f"live trading: prohibited (AGENTS.md Rule 1); exit {code}")
    report = {"generated_at": started.isoformat(timespec="seconds"), "checker_sha256": ident, "exit_code": code,
              "verdict": verdict, "summary": s, "checks": results}
    if out_dir is None:
        from research.data import paths

        out_dir = paths.outputs_dir() / "trust"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    body = json.dumps(report, indent=1, default=str)
    (out_dir / "status_latest.json").write_text(body, encoding="utf-8")
    (out_dir / f"status_{started:%Y%m%d_%H%M%S}.json").write_text(body, encoding="utf-8")
    return report, code


def render(report: Mapping[str, Any]) -> str:
    lines = [f"TRACK 2 STATUS  {report['generated_at']}  checker {report['checker_sha256'][:12]}",
             f"VERDICT: {report['verdict']}", ""]
    for c in report["checks"]:
        lines.append(f"[{c['status']:<10}] {c['check']}")
        lines += [f"             {d}" for d in c["details"] if d and not d.startswith("Traceback")]
    if "strategies" in report.get("summary", {}):
        lines += ["", "strategies by status: " + ", ".join(f"{k} {v}" for k, v in sorted(report["summary"]["strategies"].items()))]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")      # commit subjects carry the rupee sign
        report, code = run()
        print(render(report))
        return code
    except Exception as exc:                                             # a crash is an internal error, never a verdict
        print(f"STATUS CHECKER CRASHED: {type(exc).__name__}: {exc}")
        return EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
