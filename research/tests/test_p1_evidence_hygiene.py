"""P1 evidence hygiene: register single writer, registry schema, facts file present."""
import csv
import json
import re
from pathlib import Path

RESEARCH = Path(__file__).resolve().parents[1]
REGISTER = RESEARCH / "decision" / "register.json"
ALLOWED_WRITER = RESEARCH / "decision" / "promotion.py"


def test_every_register_status_is_backed_by_a_decision_record():
    """The register was seeded all UNVERIFIED (plan P1.4); any other status must have been written by
    promotion.py together with a DecisionRecord naming that strategy and transition."""
    reg = json.loads(REGISTER.read_text(encoding="utf-8"))
    assert reg["strategies"]
    records = [json.loads(p.read_text(encoding="utf-8")) for p in (RESEARCH / "decision" / "records").glob("*.json")]
    for name, v in reg["strategies"].items():
        if v["status"] == "UNVERIFIED":
            assert v["evidence"] == "none", name
        else:
            assert any(r["strategy_id"] == name and r.get("to") == v["status"] for r in records), name


def test_only_promotion_py_references_the_register_outside_tests():
    offenders = []
    for py in RESEARCH.rglob("*.py"):
        if "tests" in py.relative_to(RESEARCH).parts or py.resolve() == ALLOWED_WRITER.resolve():
            continue
        text = py.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"register\.json", text):
            offenders.append(str(py.relative_to(RESEARCH)))
    assert offenders == [], f"only research/decision/promotion.py may touch register.json: {offenders}"


def test_trials_registry_schema_and_seed():
    rows = list(csv.DictReader((RESEARCH / "evidence" / "trials_registry.csv").open(encoding="utf-8")))
    assert list(rows[0].keys()) == ["trial_id", "date_run", "strategy_id", "variant", "data_span", "sample_id",
                                    "n_trades", "mean_net_r", "r_basis", "sr_per_trade", "source", "agent", "notes"]
    assert len(rows) >= 21 and len({r["trial_id"] for r in rows}) == len(rows)
    assert all(r["r_basis"] in ("trigger", "stop_limit") for r in rows)


def test_measured_facts_file_exists():
    assert (RESEARCH / "evidence" / "MEASURED_FACTS.md").read_text(encoding="utf-8").count("| F") >= 10
