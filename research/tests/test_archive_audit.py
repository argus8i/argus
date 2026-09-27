"""
research/data/archive_audit.py: the machine audit of the NSE 2005-2021 archive download (data program JOBs 1-3).
Written before the implementation. A synthetic archive with legacy-format zips, MTO files and a manifest.
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from research.data import archive_audit as aa

SYMS = [f"S{k:02d}" for k in range(25)]


def _px(d: date, k: int) -> float:
    return 100.0 + k + (d.toordinal() % 50)


def _cm_text(d: date, prev: date) -> str:
    rows = ["SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,"]
    ts = d.strftime("%d-%b-%Y").upper()
    for k, s in enumerate(SYMS):
        rows.append(f"{s},EQ,{_px(prev, k)},{_px(d, k) + 1},{_px(prev, k) - 1},{_px(d, k)},{_px(d, k)},{_px(prev, k)},"
                    f"1000,100000,{ts},")
    return "\n".join(rows) + "\n"


def _fo_text(d: date) -> str:
    ts = d.strftime("%d-%b-%Y").upper()
    return ("INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,VAL_INLAKH,"
            f"OPEN_INT,CHG_IN_OI,TIMESTAMP,\nFUTSTK,S00,27-Jan-2005,0,XX,1,1,1,1,1,1,1,1,1,{ts},\n")


def _mto_text(d: date) -> str:
    return ("Security Wise Delivery Position - Compulsory Rolling Settlement\n"
            f"10,MTO,{d.strftime('%d%m%Y')},1,1\n"
            f"Trade Date <{d.strftime('%d-%b-%Y').upper()}>,Settlement Type <N>\n"
            "Record Type,Sr No,Name of Security,Quantity Traded,Deliverable Quantity,%\n20,1,S00,EQ,10,5,50.00\n")


class Arch:
    def __init__(self, h: Path) -> None:
        self.h, self.rows = h, []
        self.t = 0

    def save(self, dataset: str, d: date, body: bytes, name: str, outcome: str = "SAVED", sha: str = "") -> None:
        rel = f"raw/nse_archive/{dataset}/{d.year}/{name}"
        p = self.h / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
        self.t += 4
        self.rows.append({"job": "JOB1", "dataset": dataset, "trade_date": d.isoformat(), "url": "u", "attempt": 1,
                          "http_status": 200, "outcome": outcome, "bytes": len(body),
                          "sha256": sha or hashlib.sha256(body).hexdigest(), "saved_path": rel,
                          "requested_at": f"2026-09-26T19:{self.t // 60:02d}:{self.t % 60:02d}+05:30",
                          "fetched_at": f"2026-09-26T19:{self.t // 60:02d}:{self.t % 60:02d}+05:30",
                          "code_commit": "c", "requests_today": len(self.rows) + 1})

    def missing(self, dataset: str, d: date) -> None:
        self.t += 4
        self.rows.append({"dataset": dataset, "trade_date": d.isoformat(), "outcome": "MISSING_404", "http_status": 404,
                          "bytes": 0, "sha256": "", "saved_path": "",
                          "requested_at": f"2026-09-26T19:{self.t // 60:02d}:{self.t % 60:02d}+05:30"})

    def day(self, d: date, prev: date) -> None:
        tag = d.strftime("%d%b%Y").upper()
        for ds, name, text in (("cm_bhavcopy", f"cm{tag}bhav.csv.zip", _cm_text(d, prev)),
                               ("fo_bhavcopy", f"fo{tag}bhav.csv.zip", _fo_text(d))):
            buf = Path(self.h / "tmp.zip")
            buf.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr(name[:-4], text)
            self.save(ds, d, buf.read_bytes(), name)
            buf.unlink()
        self.save("mto", d, _mto_text(d).encode(), f"MTO_{d.strftime('%d%m%Y')}.DAT")

    def write(self) -> Path:
        m = self.h / "raw" / "nse_archive" / "manifest.jsonl"
        m.write_text("\n".join(json.dumps(r) for r in self.rows) + "\n", encoding="utf-8")
        return m


def _weekdays(start: date, n: int) -> list:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


@pytest.fixture()
def arch(tmp_path):
    a = Arch(tmp_path / "history")
    days = _weekdays(date(2005, 1, 3), 6)
    prev = days[0] - timedelta(days=3)
    for d in days:
        a.day(d, prev)
        prev = d
    return a, days


def test_a_clean_archive_passes_only_when_complete(arch):
    a, days = arch
    a.write()
    rep = aa.audit(a.h, expected=(days[0], days[-1]))
    assert rep["verdict"] == "PASS", rep["problems"]
    assert rep["files_checked"] == 18 and rep["sessions"] == 6 and rep["coverage"]["complete"] is True
    part = aa.audit(a.h)                                   # against the full 2005-2021 range: clean, not complete
    assert part["verdict"] == "PASS_PARTIAL" and part["coverage"]["first_unaccounted"] == "2005-01-11"


def test_sha_mismatch_and_missing_file_fail(arch):
    a, days = arch
    a.rows[0]["sha256"] = "0" * 64
    (a.h / a.rows[1]["saved_path"]).unlink()
    a.write()
    kinds = {p["kind"] for p in aa.audit(a.h)["problems"]}
    assert {"SHA_MISMATCH", "FILE_MISSING"} <= kinds


def test_wrong_date_inside_a_file_fails(arch):
    a, days = arch
    wrong = days[1]
    a.save("mto", days[5] + timedelta(days=7), _mto_text(wrong).encode(), "MTO_x.DAT")
    a.write()
    probs = [p for p in aa.audit(a.h)["problems"] if p["kind"] == "DATE_MISMATCH"]
    assert probs and probs[0]["dataset"] == "mto"


def test_a_date_saved_in_one_dataset_but_404_in_another_fails(arch):
    a, days = arch
    extra = days[-1] + timedelta(days=1)
    tag = extra.strftime("%d%b%Y").upper()
    buf = a.h / "x.zip"
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"cm{tag}bhav.csv", _cm_text(extra, days[-1]))
    a.save("cm_bhavcopy", extra, buf.read_bytes(), f"cm{tag}bhav.csv.zip")
    a.missing("fo_bhavcopy", extra)
    a.write()
    assert any(p["kind"] == "DATASET_MISMATCH" and p["trade_date"] == extra.isoformat()
               for p in aa.audit(a.h)["problems"])


def test_a_missing_session_is_found_by_the_chain_and_names_the_candidate_weekend(tmp_path):
    a = Arch(tmp_path / "history")
    fri, sat, mon = date(2005, 1, 7), date(2005, 1, 8), date(2005, 1, 10)
    thu = date(2005, 1, 6)
    a.day(thu, date(2005, 1, 5))
    a.day(fri, thu)
    a.day(mon, sat)                          # Monday's previous close is Saturday's: a special session is missing
    a.write()
    rep = aa.audit(a.h)
    gap = [p for p in rep["problems"] if p["kind"] == "MISSING_SESSION"]
    assert gap and gap[0]["between"] == [fri.isoformat(), mon.isoformat()]
    assert gap[0]["candidates"] == [sat.isoformat(), (sat + timedelta(days=1)).isoformat()]


def test_all_three_404_is_a_holiday_not_a_problem(arch):
    a, days = arch
    hol = days[-1] + timedelta(days=1)
    for ds in ("cm_bhavcopy", "fo_bhavcopy", "mto"):
        a.missing(ds, hol)
    a.write()
    rep = aa.audit(a.h, expected=(days[0], hol))
    assert rep["verdict"] == "PASS" and rep["holidays"] == [hol.isoformat()]


def test_blocks_and_fast_requests_are_reported(arch):
    a, _ = arch
    a.rows[3]["requested_at"] = a.rows[2]["requested_at"]
    a.rows.append({"dataset": "cm_bhavcopy", "trade_date": "2005-02-01", "outcome": "STOPPED_403", "http_status": 403,
                   "requested_at": "2026-09-26T20:00:00+05:30"})
    a.write()
    kinds = {p["kind"] for p in aa.audit(a.h)["problems"]}
    assert {"PACING", "BLOCKED"} <= kinds


def test_same_file_saved_twice_with_different_bytes_is_flagged(arch):
    a, days = arch
    r = dict(a.rows[2])
    r["sha256"] = "f" * 64
    a.rows.append(r)
    a.write()
    assert any(p["kind"] in ("CONFLICTING_SAVES", "SHA_MISMATCH") for p in aa.audit(a.h)["problems"])


def test_a_gap_not_yet_downloaded_is_not_a_missing_session(arch):
    a, days = arch
    far = date(2010, 1, 4)                        # a spot check years ahead of the forward download
    a.day(far, far - timedelta(days=3))
    a.write()
    rep = aa.audit(a.h, expected=(days[0], far))
    assert rep["verdict"] == "PASS_PARTIAL", rep["problems"]            # clean, but years are not downloaded yet
    assert rep["problems"] == [] and rep["coverage"]["complete"] is False
    assert rep["not_yet_downloaded"] == [[days[-1].isoformat(), far.isoformat()]]


def test_a_404_day_that_breaks_the_chain_is_named_as_traded(tmp_path):
    a = Arch(tmp_path / "history")
    mon, tue, wed = date(2005, 1, 3), date(2005, 1, 4), date(2005, 1, 5)
    a.day(mon, mon - timedelta(days=3))
    for ds in ("cm_bhavcopy", "fo_bhavcopy", "mto"):
        a.missing(ds, tue)
    a.day(wed, tue)                                  # Wednesday's previous close is Tuesday's: Tuesday traded
    a.write()
    gap = [p for p in aa.audit(a.h)["problems"] if p["kind"] == "MISSING_SESSION"]
    assert gap and gap[0]["all_404_but_traded"] == [tue.isoformat()]


def test_every_run_rereads_every_file_and_a_changed_file_fails(arch, tmp_path):
    a, days = arch
    a.write()
    span = (days[0], days[-1])
    assert aa.audit(a.h, expected=span)["verdict"] == "PASS"
    p = a.h / a.rows[0]["saved_path"]
    p.write_bytes(p.read_bytes() + b"x")                            # changed after a PASS
    assert "SHA_MISMATCH" in {x["kind"] for x in aa.audit(a.h, expected=span)["problems"]}


def test_a_non_bhavcopy_file_with_the_right_hash_is_unreadable(tmp_path):
    raw = b"A,B\n1,2\n"
    rel = "raw/nse_archive/cm_bhavcopy/2005/x.csv"
    (tmp_path / rel).parent.mkdir(parents=True)
    (tmp_path / rel).write_bytes(raw)
    m = tmp_path / "raw" / "nse_archive" / "manifest.jsonl"
    m.write_text(json.dumps({"dataset": "cm_bhavcopy", "trade_date": "2005-01-03", "outcome": "SAVED",
                             "saved_path": rel, "sha256": hashlib.sha256(raw).hexdigest()}) + "\n", encoding="utf-8")
    rep = aa.audit(tmp_path)
    assert rep["verdict"] == "FAIL" and rep["problems"][0]["kind"] == "UNREADABLE"
