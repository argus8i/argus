import hashlib
import json
from datetime import date, datetime

import pytest

from execution_realism.surveillance import (IST, SourceRecord, SurveillanceAbort, Tri, TradingCalendar,
                                            build_snapshot, canonical_sha256, premarket_gate, verdict)

TD = date(2026, 9, 24)
NOW = datetime(2026, 9, 24, 8, 45, tzinfo=IST)
CAL = TradingCalendar(frozenset({date(2026, 1, 26), date(2026, 10, 2)}), frozenset({2026}), "c" * 64)
FNO = [f"FNOSTOCK{i:03d}" for i in range(200)]
URLS = {"NSE_ASM": "https://www.nseindia.com/api/reportASM", "NSE_GSM": "https://www.nseindia.com/api/reportGSM",
        "NSE_FNO": "https://www.nseindia.com/api/underlying-information",
        "NSE_FO_BAN": "https://nsearchives.nseindia.com/content/fo/fo_secban.csv",
        "INSTRUMENT_MASTER": "https://api.kite.trade/instruments"}
CTYPE = {"NSE_ASM": "application/json; charset=utf-8", "NSE_GSM": "application/json", "NSE_FNO": "application/json",
         "NSE_FO_BAN": "text/csv", "INSTRUMENT_MASTER": "text/csv"}


def raw_bodies():
    asm = {"shortterm": {"data": [{"symbol": f"ST{i}", "asmSurvIndicator": "I"} for i in range(60)]},
           "longterm": {"data": [{"symbol": f"LT{i}", "asmSurvIndicator": "I"} for i in range(120)]
                        + [{"symbol": "FNOSTOCK009", "asmSurvIndicator": "I"}]}}
    gsm = [{"symbol": f"GSMCO{i}", "gsmStage": "0"} for i in range(70)]
    fno = {"data": {"UnderlyingList": [{"symbol": s, "underlying": s.title(), "serialNumber": i}
                                       for i, s in enumerate(FNO)]}}
    ban = "Securities in Ban For Trade Date 24-SEP-2026:\n1,FNOSTOCK005\n"
    return {"NSE_ASM": json.dumps(asm).encode(), "NSE_GSM": json.dumps(gsm).encode(),
            "NSE_FNO": json.dumps(fno).encode(), "NSE_FO_BAN": ban.encode(), "INSTRUMENT_MASTER": b"x" * 150_000}


def rec(name, raw, **over):
    kw = dict(name=name, url=URLS[name], http_status=200, content_type=CTYPE[name], byte_length=len(raw),
              sha256=hashlib.sha256(raw).hexdigest(), fetched_at=datetime(2026, 9, 24, 8, 30, tzinfo=IST),
              parser_version="3.0.0")
    kw.update(over)
    return SourceRecord(**kw)


def sources(**patch):
    bodies = raw_bodies()
    out = {n: (rec(n, b), b) for n, b in bodies.items()}
    out.update(patch)
    return out


def master():
    rows = [{"symbol": s, "series": "BE" if s == "FNOSTOCK007" else "EQ", "tick": "0.05"} for s in FNO]
    rows += [{"symbol": f"OTHER{i}", "series": "EQ", "tick": "0.01"} for i in range(1000)]
    return rows


def build(**kw):
    kw.setdefault("history_counts", None)
    return build_snapshot(kw.pop("srcs", None) or sources(), master(), trading_date=TD, calendar=CAL, now=NOW, **kw)


def test_happy_path_and_verdicts():
    snap = build()
    assert snap["validation"]["status"] == "PASS" and snap["sha256"] == canonical_sha256(snap)
    assert verdict("FNOSTOCK001", snap).eligible
    ban, t2t, ltasm = verdict("FNOSTOCK005", snap), verdict("FNOSTOCK007", snap), verdict("FNOSTOCK009", snap)
    assert not ban.eligible and "fo_ban=TRUE" in ban.reasons
    assert not t2t.eligible and "t2t=TRUE" in t2t.reasons
    assert not ltasm.eligible and "asm_lt=TRUE" in ltasm.reasons       # LT-ASM applies to F&O stocks
    assert not verdict("NOTFNO", snap).eligible


def test_unknown_list_is_not_clearance():
    snap = build()
    del snap["lists"]["gsm"]
    v = verdict("FNOSTOCK001", snap)
    assert not v.eligible and dict(v.flags)["gsm"] is Tri.UNKNOWN


def test_codex_r11_empty_old_file_aborts(tmp_path):
    old = tmp_path / "nse_surveillance_snapshot_2000-01-01.json"
    old.write_text("{}")
    with pytest.raises(SurveillanceAbort, match="WRONG_SESSION_FILE"):
        premarket_gate(old, TD, NOW)
    today = tmp_path / "nse_surveillance_snapshot_2026-09-24.json"
    today.write_text("{}")
    with pytest.raises(SurveillanceAbort, match="EMPTY_PAYLOAD"):
        premarket_gate(today, TD, NOW)


def test_gate_accepts_valid_and_rejects_tampered(tmp_path):
    snap = build()
    p = tmp_path / "nse_surveillance_snapshot_2026-09-24.json"
    p.write_text(json.dumps(snap))
    assert premarket_gate(p, TD, NOW)["sha256"] == snap["sha256"]
    snap["lists"]["asm_lt"].remove("FNOSTOCK009")                        # quietly clear a stock
    p.write_text(json.dumps(snap))
    with pytest.raises(SurveillanceAbort, match="INTEGRITY"):
        premarket_gate(p, TD, NOW)


@pytest.mark.parametrize("over,code", [
    (dict(url="https://evil.example.com/api/reportASM"), "SOURCE_HOST"),
    (dict(http_status=500), "HTTP_STATUS"),
    (dict(content_type="text/html"), "CONTENT_TYPE"),
    (dict(sha256="0" * 64), "INTEGRITY"),
    (dict(fetched_at=datetime(2026, 9, 24, 8, 30)), "NAIVE_TIMESTAMP"),
    (dict(fetched_at=datetime(2026, 9, 23, 10, 0, tzinfo=IST)), "STALE_OR_FUTURE"),
    (dict(fetched_at=datetime(2026, 9, 24, 9, 30, tzinfo=IST)), "STALE_OR_FUTURE"),
])
def test_transport_failures_abort(over, code):
    b = raw_bodies()["NSE_ASM"]
    with pytest.raises(SurveillanceAbort, match=code):
        build(srcs=sources(NSE_ASM=(rec("NSE_ASM", b, **over), b)))


def test_empty_payload_and_missing_source_abort():
    with pytest.raises(SurveillanceAbort, match="EMPTY_PAYLOAD"):
        build(srcs=sources(NSE_GSM=(rec("NSE_GSM", b"[]"), b"[]")))
    s = sources()
    del s["NSE_FO_BAN"]
    with pytest.raises(SurveillanceAbort, match="MISSING_SOURCE"):
        build(srcs=s)


def test_schema_and_asof_failures_abort():
    bad = json.dumps({"shortterm": {"data": [{"symbol": f"S{i}"} for i in range(80)]}}).encode()
    with pytest.raises(SurveillanceAbort, match="SCHEMA"):
        build(srcs=sources(NSE_ASM=(rec("NSE_ASM", bad), bad)))
    dup = json.dumps([{"symbol": "DUP"}] * 60).encode()
    with pytest.raises(SurveillanceAbort, match="duplicate"):
        build(srcs=sources(NSE_GSM=(rec("NSE_GSM", dup), dup)))
    old_ban = b"Securities in Ban For Trade Date 23-SEP-2026:\nNIL\n"
    with pytest.raises(SurveillanceAbort, match="STALE_OR_FUTURE"):
        build(srcs=sources(NSE_FO_BAN=(rec("NSE_FO_BAN", old_ban), old_ban)))


def test_cardinality_and_overnight_churn_abort():
    with pytest.raises(SurveillanceAbort, match="CARDINALITY"):
        build(history_counts={"asm_lt": [300 + (i % 5) for i in range(30)]})
    with pytest.raises(SurveillanceAbort, match="CARDINALITY"):
        build(previous_lists={"fno_underlyings": [f"OLD{i}" for i in range(200)]})


def test_calendar_guards():
    with pytest.raises(SurveillanceAbort, match="CALENDAR_COVERAGE"):
        CAL.is_trading_day(date(2027, 1, 4))
    with pytest.raises(SurveillanceAbort, match="NOT_A_TRADING_DAY"):
        build_snapshot(sources(), master(), trading_date=date(2026, 9, 26), calendar=CAL, now=NOW)


def test_snapshot_conforms_to_published_json_schema():
    jsonschema = pytest.importorskip("jsonschema")
    from execution_realism.surveillance import SNAPSHOT_JSON_SCHEMA
    snap = build()
    jsonschema.validate(snap, SNAPSHOT_JSON_SCHEMA, format_checker=jsonschema.FormatChecker())
    bad = dict(snap, validation={"status": "FAIL", "warnings": []})
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, SNAPSHOT_JSON_SCHEMA)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({}, SNAPSHOT_JSON_SCHEMA)
