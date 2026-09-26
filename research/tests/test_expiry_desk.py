"""
Paper desk for EXPIRY_RELIEF_LONG v2 (research/shadow/expiry_desk.py). Written before the implementation.
Synthetic NSE UDiFF daily files in a temporary history folder: expiry detection, the split-safe 20-session return,
eligibility (price floor, turnover, ban list, fail-closed on a missing ban list), prospective vs late journaling,
reconciliation with a gap stop, a time exit, and a corporate action inside the hold.
"""
from __future__ import annotations

import gzip
import io
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from research.shadow import expiry_desk as ed

IST = timezone(timedelta(hours=5, minutes=30))
COLS = ["TradDt", "BizDt", "Sgmt", "Src", "FinInstrmTp", "FinInstrmId", "ISIN", "TckrSymb", "SctySrs", "XpryDt",
        "FininstrmActlXpryDt", "StrkPric", "OptnTp", "FinInstrmNm", "OpnPric", "HghPric", "LwPric", "ClsPric",
        "LastPric", "PrvsClsgPric", "UndrlygPric", "SttlmPric", "OpnIntrst", "ChngInOpnIntrst", "TtlTradgVol",
        "TtlTrfVal", "TtlNbOfTxsExctd", "SsnId", "NewBrdLotQty", "Rmks", "Rsvd1", "Rsvd2", "Rsvd3", "Rsvd4"]


def _write(path: Path, rows: list) -> None:
    df = pd.DataFrame([{c: r.get(c, "") for c in COLS} for r in rows], columns=COLS)
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    path.write_bytes(gzip.compress(buf.getvalue().encode("utf-8")))


def _weekdays(start: date, n: int) -> list:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _cm_row(d, sym, o, h, lo, c, pc, turnover=5e8, series="EQ"):
    return {"TradDt": d.isoformat(), "Sgmt": "CM", "FinInstrmTp": "STK", "TckrSymb": sym, "SctySrs": series,
            "OpnPric": o, "HghPric": h, "LwPric": lo, "ClsPric": c, "PrvsClsgPric": pc, "TtlTrfVal": turnover}


def _fo_rows(d, syms, expiry):
    return [{"TradDt": d.isoformat(), "Sgmt": "FO", "FinInstrmTp": "STF", "TckrSymb": s, "XpryDt": expiry.isoformat()}
            for s in syms]


@pytest.fixture()
def hist(tmp_path):
    """21 sessions up to and including the expiry (index 20). FALL drops 1% a session (-18% over 20), FLAT does not
    move, CHEAP falls but trades below Rs 10, THIN falls on small turnover, BANNED falls but is in the next
    session's ban list, SPLIT halves on a split with PrvsClsgPric adjusted (so it did not really fall)."""
    days = _weekdays(date(2026, 8, 3), 26)
    expiry = days[20]
    h = tmp_path / "history"
    px = {"FALL": 100.0, "CHEAP": 8.0, "THIN": 100.0, "BANNED": 100.0}
    for i, d in enumerate(days[:21]):
        rows = []
        for s in list(px):
            prev = px[s]
            px[s] = prev * 0.99
            rows.append(_cm_row(d, s, prev, prev, px[s], px[s], prev, turnover=5e8 if s != "THIN" else 1e7))
        rows.append(_cm_row(d, "FLAT", 50, 50, 50, 50, 50))
        split_close = 200.0 if i < 10 else 100.0
        rows.append(_cm_row(d, "SPLIT", split_close, split_close, split_close, split_close,
                            100.0 if i == 10 else split_close))
        _write(h / "bhavcopy" / "raw" / "cm" / str(d.year) / f"{d.isoformat()}.csv.gz", rows)
        _write(h / "bhavcopy" / "raw" / "fo" / str(d.year) / f"{d.isoformat()}.csv.gz",
               _fo_rows(d, ["FALL", "FLAT", "CHEAP", "THIN", "BANNED", "SPLIT"], expiry))
    return {"history": h, "days": days, "expiry": expiry}


def _ban(h: Path, d: date, syms: list) -> None:
    body = "Securities in Ban For Trade Date " + d.strftime("%d-%b-%Y").upper() + ":\n" + \
           "".join(f"{i + 1},{s}\n" for i, s in enumerate(syms))
    p = h / "raw" / "nse" / "fo_ban" / f"{d.isoformat()}_{d.isoformat()}.csv.gz"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(gzip.compress(body.encode("utf-8")))


def test_read_udiff_and_monthly_expiry(hist):
    fo = ed.read_udiff(hist["history"] / "bhavcopy" / "raw" / "fo" / "2026" / f"{hist['expiry'].isoformat()}.csv.gz")
    assert ed.is_monthly_expiry(fo, hist["expiry"]) is True
    assert ed.is_monthly_expiry(fo, hist["expiry"] - timedelta(days=1)) is False


def test_plan_selects_only_eligible_twenty_session_losers(hist):
    h, e = hist["history"], hist["expiry"]
    entry = hist["days"][21]
    _ban(h, entry, ["BANNED"])
    p = ed.build_plan(h, e, entry)
    assert p["status"] == "OK"
    assert [s["symbol"] for s in p["signals"]] == ["FALL"]
    reasons = p["excluded"]
    assert reasons["CHEAP"] == "PRICE_BELOW_10" and reasons["THIN"] == "TURNOVER_BELOW_30CR"
    assert reasons["BANNED"] == "FO_BAN_ON_ENTRY"
    assert "SPLIT" not in [s["symbol"] for s in p["signals"]]            # a split is not a crash
    assert p["signals"][0]["r20_pct"] == pytest.approx((0.99 ** 20 - 1) * 100, abs=1e-6)


def test_missing_ban_list_blocks_the_plan(hist):
    p = ed.build_plan(hist["history"], hist["expiry"], hist["days"][21])
    assert p["status"] == "BLOCKED" and "ban list" in p["reason"]


def test_not_an_expiry_is_refused(hist):
    p = ed.build_plan(hist["history"], hist["days"][19], hist["days"][20])
    assert p["status"] == "NOT_AN_EXPIRY"


def test_entry_session_is_the_next_weekday_confirmed_by_its_ban_list(hist):
    h, e = hist["history"], hist["expiry"]
    assert ed.entry_session(h, e) is None                         # no ban list published yet: unknown, fail closed
    _ban(h, hist["days"][21], [])
    assert ed.entry_session(h, e) == hist["days"][21]


def test_prospective_only_before_0900_on_the_entry_session(hist, tmp_path):
    h, e, entry = hist["history"], hist["expiry"], hist["days"][21]
    _ban(h, entry, [])
    j = tmp_path / "journal.jsonl"
    early = datetime.combine(e, datetime.min.time(), IST) + timedelta(hours=20)
    late = datetime.combine(entry, datetime.min.time(), IST) + timedelta(hours=9, minutes=1)
    assert ed.plan(h, e, j, now=early)["admissible"] is True
    assert ed.plan(h, e, j, now=late)["admissible"] is False
    rows = ed.Journal(j).verify()
    assert [r["kind"] for r in rows] == ["PLAN", "PLAN"]


def _hold(hist, fall_path, extra=None):
    """Write five hold sessions after the expiry for FALL with the given (o, h, l, c, prev_close) tuples."""
    h = hist["history"]
    days = hist["days"][21:26]
    for d, (o, hi, lo, c, pc) in zip(days, fall_path):
        _write(h / "bhavcopy" / "raw" / "cm" / str(d.year) / f"{d.isoformat()}.csv.gz",
               [_cm_row(d, "FALL", o, hi, lo, c, pc)] + (extra or []))
    return days


def test_reconcile_time_exit(hist, tmp_path):
    h, e = hist["history"], hist["expiry"]
    _ban(h, hist["days"][21], ["BANNED"])                        # only FALL is a signal
    j = tmp_path / "journal.jsonl"
    ed.plan(h, e, j, now=datetime.combine(e, datetime.min.time(), IST) + timedelta(hours=20))
    c0 = 100 * 0.99 ** 21
    _hold(hist, [(c0, c0 * 1.01, c0 * 0.99, c0 * 1.005, c0),
                 (c0 * 1.005, c0 * 1.02, c0 * 1.0, c0 * 1.01, c0 * 1.005)] +
          [(c0 * 1.01, c0 * 1.02, c0 * 1.0, c0 * 1.01, c0 * 1.01)] * 3)
    res = ed.reconcile(h, j, as_of=hist["days"][25])
    assert res["scored"] == 1
    r = [x for x in ed.Journal(j).verify() if x["kind"] == "RESULT"][0]
    assert r["symbol"] == "FALL" and r["exit_reason"] == "TIME" and r["evidence"] == "PROSPECTIVE"
    assert ed.reconcile(h, j, as_of=hist["days"][25])["scored"] == 0          # never scored twice


def test_reconcile_gap_stop(hist, tmp_path):
    h, e = hist["history"], hist["expiry"]
    _ban(h, hist["days"][21], ["BANNED"])                        # only FALL is a signal
    j = tmp_path / "journal.jsonl"
    ed.plan(h, e, j, now=datetime.combine(e, datetime.min.time(), IST) + timedelta(hours=20))
    c0 = 100 * 0.99 ** 21
    _hold(hist, [(c0, c0, c0 * 0.99, c0, c0), (c0 * 0.9, c0 * 0.9, c0 * 0.9, c0 * 0.9, c0)] +
          [(c0 * 0.9, c0 * 0.9, c0 * 0.9, c0 * 0.9, c0 * 0.9)] * 3)
    ed.reconcile(h, j, as_of=hist["days"][25])
    r = [x for x in ed.Journal(j).verify() if x["kind"] == "RESULT"][0]
    assert r["exit_reason"] == "STOP_GAP" and r["net_r"] < -1.0


def test_reconcile_voids_a_corporate_action_inside_the_hold(hist, tmp_path):
    h, e = hist["history"], hist["expiry"]
    _ban(h, hist["days"][21], ["BANNED"])                        # only FALL is a signal
    j = tmp_path / "journal.jsonl"
    ed.plan(h, e, j, now=datetime.combine(e, datetime.min.time(), IST) + timedelta(hours=20))
    c0 = 100 * 0.99 ** 21
    _hold(hist, [(c0, c0, c0, c0, c0), (c0 / 2, c0 / 2, c0 / 2, c0 / 2, c0 / 2)] + [(c0 / 2,) * 5] * 3)
    ed.reconcile(h, j, as_of=hist["days"][25])
    r = [x for x in ed.Journal(j).verify() if x["kind"] == "RESULT"][0]
    assert r["exit_reason"] == "VOID_CORPORATE_ACTION"


def test_reconcile_waits_for_five_sessions(hist, tmp_path):
    h, e = hist["history"], hist["expiry"]
    _ban(h, hist["days"][21], ["BANNED"])                        # only FALL is a signal
    j = tmp_path / "journal.jsonl"
    ed.plan(h, e, j, now=datetime.combine(e, datetime.min.time(), IST) + timedelta(hours=20))
    c0 = 100 * 0.99 ** 21
    _hold(hist, [(c0, c0, c0, c0, c0)] * 3)
    assert ed.reconcile(h, j, as_of=hist["days"][23])["scored"] == 0
