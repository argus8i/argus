"""
research/decision/promotion.py
==============================
The ONLY writer of research/decision/register.json (plan P6.6; enforced by
research/tests/test_p1_evidence_hygiene.py).

State machine
    UNVERIFIED --(lock-verified holdout record: pass)--> SHADOW
    UNVERIFIED --(lock-verified holdout record: fail)--> REJECTED                  terminal for this version
    UNVERIFIED --(sealed design record: mean <= 0 and t <= -2)--> KILLED_ON_DESIGN   terminal (Yashu, 26 Sep)
    SHADOW     --(first look with n >= n_pre/2 and mean + 1.2816*SE < 0)--> KILLED   futility; terminal
    SHADOW     --(n reaches n_pre: ONE terminal test)--> PROMOTE_TO_PAPER if evaluate_gate passes and
                                                         LB95 > 0, else KILLED

Evidence rules
- Holdout transitions use the E1 study record, and only when its pre-registration lock verifies. This is
  the only place E1 evidence can change a status, and it can never promote.
- evaluate_gate, LB95 and futility use E2/E3 ledger rows only.
- Futility is a SINGLE look: the first evaluation at which n >= n_pre/2 (and n < n_pre). Its outcome is
  recorded so it is never repeated. There are no repeated looks after n_pre: a strategy that fails the
  terminal test is KILLED; a new idea needs a new version, a new pre-registration and new data. (Repeated
  weekly re-tests at n = 111, 121, ..., 311 would inflate false promotions from 2.3% to about 7.6%.)
- n_pre = 111 (Yashu, decision 3).

Every transition, and every evaluation that does not transition, writes a DecisionRecord JSON under
research/decision/records/: inputs, ledger hash, gate output, LB95, PSR, DSR, the status and reasons.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

import numpy as np

from research.backtest.metrics import deflated_sharpe, evaluate_gate
from research.decision.stats import Z_FUTILITY, clustered_se, lb95
from research.studies import prereg_io

IST = timezone(timedelta(hours=5, minutes=30))
REGISTER_PATH = Path(__file__).with_name("register.json")
RECORDS_DIR = Path(__file__).with_name("records")

UNVERIFIED, SHADOW, REJECTED, KILLED, PROMOTE = "UNVERIFIED", "SHADOW", "REJECTED", "KILLED", "PROMOTE_TO_PAPER"
KILLED_ON_DESIGN = "KILLED_ON_DESIGN"
TERMINAL = frozenset({REJECTED, KILLED, PROMOTE, KILLED_ON_DESIGN})
KILL_T = -2.0                 # a design record must be at least this clearly negative to kill (Yashu, 26 Sep)
ADMISSIBLE = ("E2", "E3")
N_PRE = 111


class TransitionError(ValueError):
    """The requested transition is not allowed from the current state, or its evidence does not verify."""


# ------------------------------------------------------------------------------------------ pure decision
@dataclass
class ShadowDecision:
    state: str
    futility_checked: bool
    action: str                          # NONE | FUTILITY_PASSED | KILLED_FUTILITY | TERMINAL_PROMOTE | TERMINAL_KILL
    reasons: List[str] = field(default_factory=list)
    n: int = 0
    n_sessions: int = 0
    mean_net_r: float = math.nan
    se: float = math.nan
    lb95: float = math.nan
    gate: Optional[Dict[str, Any]] = None


def decide_shadow(state: str, futility_checked: bool, rows: Sequence[Mapping[str, Any]], sessions_observed: int,
                  n_pre: int = N_PRE) -> ShadowDecision:
    """Pure SHADOW-state decision on ledger rows (filtered here to E2/E3). No I/O."""
    if state != SHADOW:
        raise TransitionError(f"shadow evaluation needs state SHADOW, got {state}")
    good = [r for r in rows if r.get("evidence_class") in ADMISSIBLE and r.get("net_r") is not None
            and math.isfinite(float(r["net_r"]))]
    n = len(good)
    net = [float(r["net_r"]) for r in good]
    sess = [r["session"] for r in good]
    mean = float(np.mean(net)) if net else math.nan
    se = clustered_se(net, sess)
    d = ShadowDecision(SHADOW, futility_checked, "NONE", n=n, n_sessions=len(set(sess)), mean_net_r=mean, se=se,
                       lb95=lb95(mean, se))
    if n >= n_pre:
        trades = [{"session": r["session"], "net_r": float(r["net_r"]), "evidence_class": r["evidence_class"]}
                  for r in good]
        d.gate = evaluate_gate(trades, sessions_observed)
        lb_ok = math.isfinite(d.lb95) and d.lb95 > 0
        ok = bool(d.gate["passed"]) and lb_ok
        d.state, d.action = (PROMOTE, "TERMINAL_PROMOTE") if ok else (KILLED, "TERMINAL_KILL")
        d.reasons = [] if ok else list(d.gate["reasons"]) + ([] if lb_ok else [f"LB95 {d.lb95:.4f} <= 0"])
        return d
    if n * 2 >= n_pre and not futility_checked:
        d.futility_checked = True
        upper = mean + Z_FUTILITY * se if math.isfinite(se) else math.nan
        if math.isfinite(upper) and upper < 0:
            d.state, d.action = KILLED, "KILLED_FUTILITY"
            d.reasons = [f"futility: mean {mean:.4f} + 1.2816 * SE {se:.4f} = {upper:.4f} < 0 at n = {n}"]
        else:
            d.action = "FUTILITY_PASSED"
    return d


# ------------------------------------------------------------------------------------------ register I/O
def load_register(path: Path = REGISTER_PATH) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_register(reg: Dict[str, Any], path: Path, now: datetime, who: str) -> None:
    reg["updated_by"] = f"research/decision/promotion.py ({who})"
    reg["updated_at"] = now.isoformat(timespec="seconds")
    tmp = Path(path).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)                                  # atomic on the same volume


def _write_record(record: Dict[str, Any], records_dir: Path) -> Path:
    records_dir.mkdir(parents=True, exist_ok=True)
    stamp = record["at"].replace(":", "").replace("-", "")[:15]
    p = records_dir / f"{record['strategy_id']}_{stamp}_{record['action']}.json"
    p.write_text(json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8")
    return p


def _rows_hash(rows: Sequence[Mapping[str, Any]]) -> str:
    h = hashlib.sha256()
    for r in sorted(rows, key=lambda r: (str(r.get("decision_ts")), str(r.get("signal_id")))):
        h.update(json.dumps(dict(r), sort_keys=True, default=str).encode())
    return h.hexdigest()


def _clean(x: Any) -> Any:
    return None if isinstance(x, float) and not math.isfinite(x) else x


# ------------------------------------------------------------------------------------------ transitions
def apply_holdout(strategy_id: str, record: Mapping[str, Any], prereg_yaml: Path | str, *,
                  register_path: Path = REGISTER_PATH, records_dir: Path = RECORDS_DIR,
                  now: Optional[datetime] = None) -> Dict[str, Any]:
    """UNVERIFIED -> SHADOW (pass) or REJECTED (fail), from the one holdout study record.
    record: {"strategy_id", "prereg_sha256", "evidence_class": "E1", "passed": bool, ...} written by
    run_holdout.py (P7.4). Refused unless the pre-registration lock verifies and the record cites it."""
    now = now or datetime.now(IST)
    reg = load_register(register_path)
    st = reg["strategies"].get(strategy_id)
    if st is None:
        raise TransitionError(f"{strategy_id} is not in the register")
    if st["status"] != UNVERIFIED:
        raise TransitionError(f"{strategy_id}: holdout transitions start from UNVERIFIED, not {st['status']}")
    lock = prereg_io.verify_lock(prereg_yaml)
    if not lock.valid:
        raise TransitionError(f"{strategy_id}: pre-registration lock does not verify ({lock.reason})")
    if record.get("strategy_id") != strategy_id or record.get("prereg_sha256") != lock.lock.get("yaml_sha256"):
        raise TransitionError(f"{strategy_id}: holdout record does not cite the locked pre-registration")
    if record.get("evidence_class") != "E1" or not isinstance(record.get("passed"), bool):
        raise TransitionError(f"{strategy_id}: holdout record must be E1 with a boolean 'passed'")
    new = SHADOW if record["passed"] else REJECTED
    rec = {"strategy_id": strategy_id, "at": now.isoformat(timespec="seconds"), "action": f"HOLDOUT_{new}",
           "from": UNVERIFIED, "to": new, "reasons": list(record.get("reasons") or []),
           "inputs": {"holdout_record_sha256": hashlib.sha256(json.dumps(dict(record), sort_keys=True,
                                                                         default=str).encode()).hexdigest(),
                      "prereg": str(prereg_yaml), "lock": lock.lock},
           "evidence": "E1 holdout study (can never promote)"}
    st.update({"status": new, "evidence": "E1_HOLDOUT", "prereg_sha256": lock.lock.get("yaml_sha256")})
    _write_register(reg, register_path, now, f"holdout {strategy_id}")
    rec["record_file"] = str(_write_record(rec, records_dir))
    return rec


def kill_on_design(strategy_id: str, record: Mapping[str, Any], *, register_path: Path = REGISTER_PATH,
                   records_dir: Path = RECORDS_DIR, now: Optional[datetime] = None) -> Dict[str, Any]:
    """UNVERIFIED -> KILLED_ON_DESIGN (terminal), on a design-set record that is clearly negative: mean net R
    <= 0 AND day-clustered t <= KILL_T, computed on a sealed snapshot. Design evidence (E1_CF) can never
    promote; it can only stop a strategy from taking slots (Yashu, 26 Sep 2026: do not curve-fit a strategy
    that loses on the design set). A new idea needs a new version and a new pre-registration."""
    now = now or datetime.now(IST)
    reg = load_register(register_path)
    st = reg["strategies"].get(strategy_id)
    if st is None:
        raise TransitionError(f"{strategy_id} is not in the register")
    if st["status"] != UNVERIFIED:
        raise TransitionError(f"{strategy_id}: a design kill starts from UNVERIFIED, not {st['status']}")
    if record.get("strategy_id") != strategy_id or not record.get("snapshot_sha256"):
        raise TransitionError(f"{strategy_id}: design record must cite the strategy and its sealed snapshot")
    mean, t = record.get("mean_net_r"), record.get("t_cluster")
    if not (isinstance(mean, (int, float)) and isinstance(t, (int, float)) and math.isfinite(mean)
            and math.isfinite(t) and mean <= 0 and t <= KILL_T):
        raise TransitionError(f"{strategy_id}: design record is not clearly negative (mean {mean}, t {t}; "
                              f"needs mean <= 0 and t <= {KILL_T})")
    rec = {"strategy_id": strategy_id, "at": now.isoformat(timespec="seconds"), "action": KILLED_ON_DESIGN,
           "from": UNVERIFIED, "to": KILLED_ON_DESIGN,
           "reasons": [f"design mean net R {mean:+.4f} (t {t:.2f}, n {record.get('n')}) on "
                       f"{record.get('window')}; expected_net_r <= 0, so EXPLOIT allocates it no slots"],
           "inputs": {k: record.get(k) for k in ("window", "snapshot_sha256", "n", "mean_net_r", "t_cluster",
                                                  "mean_gross_r", "mean_fee_r", "mean_slip_r", "result_file",
                                                  "evidence_class")},
           "evidence": "E1_CF design-set record (can never promote)"}
    st.update({"status": KILLED_ON_DESIGN, "evidence": "E1_CF_DESIGN", "expected_net_r": float(mean)})
    _write_register(reg, register_path, now, f"{KILLED_ON_DESIGN} {strategy_id}")
    rec["record_file"] = str(_write_record(rec, records_dir))
    return rec


def evaluate_shadow(strategy_id: str, ledger_rows: Sequence[Mapping[str, Any]], sessions_observed: int, *,
                    n_pre: int = N_PRE, n_trials: int = 1, trials_sr_variance: float = 0.0,
                    register_path: Path = REGISTER_PATH, records_dir: Path = RECORDS_DIR,
                    now: Optional[datetime] = None) -> Dict[str, Any]:
    """Evaluate a SHADOW strategy on its ledger rows (all classes may be passed; only E2/E3 count).
    Writes a DecisionRecord every time, and the register when the status or the futility flag changes."""
    now = now or datetime.now(IST)
    reg = load_register(register_path)
    st = reg["strategies"].get(strategy_id)
    if st is None:
        raise TransitionError(f"{strategy_id} is not in the register")
    if st["status"] in TERMINAL:
        raise TransitionError(f"{strategy_id} is {st['status']} (terminal): no further looks")
    d = decide_shadow(st["status"], bool(st.get("futility_checked")), ledger_rows, sessions_observed, n_pre)
    good = [r for r in ledger_rows if r.get("evidence_class") in ADMISSIBLE]
    net = [float(r["net_r"]) for r in good if r.get("net_r") is not None]
    rec = {"strategy_id": strategy_id, "at": now.isoformat(timespec="seconds"), "action": d.action,
           "from": st["status"], "to": d.state, "reasons": d.reasons,
           "inputs": {"n_admissible": d.n, "n_sessions": d.n_sessions, "sessions_observed": sessions_observed,
                      "n_pre": n_pre, "n_trials": n_trials, "trials_sr_variance": trials_sr_variance},
           "ledger_hash": _rows_hash(good), "gate": d.gate, "mean_net_r": _clean(d.mean_net_r),
           "se_cluster": _clean(d.se), "lb95": _clean(d.lb95),
           "psr": _clean(deflated_sharpe(net, 1, 0.0)) if len(net) >= 3 else None,
           "dsr": _clean(deflated_sharpe(net, n_trials, trials_sr_variance)) if len(net) >= 3 else None,
           "evidence": "E2/E3 ledger rows only"}
    changed = d.state != st["status"] or d.futility_checked != bool(st.get("futility_checked"))
    if changed:
        st["status"] = d.state
        st["futility_checked"] = d.futility_checked
        if d.state in TERMINAL:
            st["evidence"] = "E2_E3_SHADOW"
        _write_register(reg, register_path, now, f"{d.action} {strategy_id}")
    rec["record_file"] = str(_write_record(rec, records_dir))
    return rec
