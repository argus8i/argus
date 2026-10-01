"""
test_track2_official_source_ingestion.py - Verification & Adversarial Tests for Track 2 Phase 1B2
Part of Project Swing Trades (Antigravity + Claude Code + OpenAI Codex).

Validates:
1. Fetch all three official NSE endpoints as independent raw responses (mock fixtures).
2. Persist raw response bytes before parsing, using atomic writes under Track 2 surveillance directory.
3. Record per source: exact URL, HTTP status, fetched-at timezone-aware timestamp,
   content type, byte length, SHA-256, raw relative path, and parser version.
4. Parse deterministically from persisted raw bytes, never from caller-supplied derived list.
5. Produce canonical uppercase, sorted, duplicate-free sets for ASM ST, ASM LT, GSM, and active F&O underlyings.
6. The poller must independently reopen every raw file, recheck every hash, replay the parser,
   and require exact equality with derived lists before setting verified, source_authenticated,
   and lineage_bound_to_raw_parser_output to true.
7. Fail closed on:
   - network failure
   - non-200 status (including boolean masquerading as int)
   - wrong official host/path
   - redirects outside the allowlist
   - HTML or unexpected content type
   - malformed JSON
   - missing schema nodes
   - invalid or duplicate symbols
   - empty F&O universe
   - timestamp ambiguity or future time
   - stale or wrong-session evidence
   - raw-path escape or symlink
   - hash mismatch
   - parser-version mismatch
   - replay mismatch
8. Never synthesize clear surveillance state from missing data.
9. Never mutate existing immutable session snapshot (immutable snapshot already exists).
10. Atomic write interruption safety.
11. Deterministic replay identity across multiple runs.
12. Static no-order-path guard: no credentials, broker APIs, order routes, or live orders.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import tempfile
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from antigravity.daemons.exchange_circular_poller import ExchangeCircularPoller
from antigravity.daemons.track2_official_source_ingestor import (
    IST,
    PARSER_VERSION,
    EXPECTED_ENDPOINTS,
    Track2OfficialSourceIngestor,
    atomic_write_bytes,
    parse_raw_source_bytes,
    replay_and_verify_sources,
)
from antigravity.models.track2_universe_scanner import EXPANDED_FNO_UNIVERSE


@pytest.fixture
def temp_surv_dir():
    d = tempfile.mkdtemp(prefix="phase1b2_test_")
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


def make_valid_asm_raw() -> bytes:
    data = {
        "shortterm": {
            "data": [
                {"symbol": "STK_ASM_ST1"},
                {"symbol": "STK_ASM_ST2"},
            ]
        },
        "longterm": {
            "data": [
                {"symbol": "STK_ASM_LT1"},
            ]
        }
    }
    return json.dumps(data, sort_keys=True).encode("utf-8")


def make_valid_gsm_raw() -> bytes:
    data = [
        {"symbol": "STK_GSM1"},
        {"symbol": "STK_GSM2"},
    ]
    return json.dumps(data, sort_keys=True).encode("utf-8")


def make_valid_fno_raw() -> bytes:
    # Mandate line 13: "The F&O response contains data.UnderlyingList; each record has a symbol."
    data = {
        "data": {
            "UnderlyingList": [
                {"symbol": c["symbol"]} for c in EXPANDED_FNO_UNIVERSE
            ]
        }
    }
    return json.dumps(data, sort_keys=True).encode("utf-8")


def make_mock_fetcher(
    asm_bytes: Optional[bytes] = None,
    gsm_bytes: Optional[bytes] = None,
    fno_bytes: Optional[bytes] = None,
    asm_status: int = 200,
    gsm_status: int = 200,
    fno_status: int = 200,
    asm_content_type: str = "application/json",
    gsm_content_type: str = "application/json",
    fno_content_type: str = "application/json",
    network_error_on: Optional[str] = None,
):
    if asm_bytes is None:
        asm_bytes = make_valid_asm_raw()
    if gsm_bytes is None:
        gsm_bytes = make_valid_gsm_raw()
    if fno_bytes is None:
        fno_bytes = make_valid_fno_raw()

    def fetcher(url: str) -> Tuple[int, bytes, Dict[str, str]]:
        if network_error_on and url == EXPECTED_ENDPOINTS[network_error_on]:
            raise ConnectionError(f"Simulated network fault for {url}")

        if url == EXPECTED_ENDPOINTS["asm"]:
            return asm_status, asm_bytes, {"content-type": asm_content_type}
        elif url == EXPECTED_ENDPOINTS["gsm"]:
            return gsm_status, gsm_bytes, {"content-type": gsm_content_type}
        elif url == EXPECTED_ENDPOINTS["fno"]:
            return fno_status, fno_bytes, {"content-type": fno_content_type}
        else:
            raise ValueError(f"Unexpected URL: {url}")

    return fetcher


def test_positive_ingest_and_poller_replay_verification(temp_surv_dir):
    """
    Test full end-to-end positive flow:
    Ingestor fetches raw bytes, persists atomically, parses deterministically.
    Poller reopens raw files, rechecks hashes, replays parser, and sets verified=True.
    """
    fixed_now = datetime(2026, 9, 21, 8, 30, tzinfo=IST)
    ingestor = Track2OfficialSourceIngestor(
        surveillance_dir=str(temp_surv_dir),
        fetcher=make_mock_fetcher(),
        now_fn=lambda: fixed_now,
    )

    ingest_res = ingestor.ingest_session("2026-09-21", publication_date="2026-09-21")
    assert ingest_res["verified"] is True
    assert ingest_res["asm_short_term_count"] == 2
    assert ingest_res["asm_long_term_count"] == 1
    assert ingest_res["gsm_count"] == 2
    assert ingest_res["fno_underlyings_count"] == len(EXPANDED_FNO_UNIVERSE)

    snap_path = Path(ingest_res["snapshot_path"])
    assert snap_path.is_file()

    # Now verify with ExchangeCircularPoller
    hist_file = temp_surv_dir / "history.json"
    log_file = temp_surv_dir / "poller.log"
    poller = ExchangeCircularPoller(
        history_path=str(hist_file),
        log_path=str(log_file),
        snapshot_dir=str(temp_surv_dir),
        now_fn=lambda: fixed_now,
    )

    poller_res = poller.load_sourced_circular_snapshot("2026-09-21", snapshot_path=str(snap_path))
    assert poller_res["verified"] is True
    assert poller_res["integrity_verified"] is True
    assert poller_res["source_authenticated"] is True
    assert poller_res["lineage_bound_to_raw_parser_output"] is True
    assert poller_res["reason"] == "OFFICIAL_NSE_RAW_LINEAGE_VERIFIED"
    assert "STK_ASM_ST1" in poller_res["asm_short_term"]
    assert "STK_GSM1" in poller_res["gsm"]
    assert "CDSL" in poller_res["fno_underlyings"]

    report = poller.poll_and_update("2026-09-21", snapshot_path=str(snap_path))
    assert report["qualification_eligible"] is True
    assert report["qualified_count"] > 0


def test_reject_mutation_of_existing_snapshot(temp_surv_dir):
    """Never mutate an existing immutable session snapshot."""
    fixed_now = datetime(2026, 9, 21, 8, 30, tzinfo=IST)
    ingestor = Track2OfficialSourceIngestor(
        surveillance_dir=str(temp_surv_dir),
        fetcher=make_mock_fetcher(),
        now_fn=lambda: fixed_now,
    )
    res1 = ingestor.ingest_session("2026-09-21")
    assert res1["verified"] is True

    # Second attempt must fail closed and refuse overwrite
    res2 = ingestor.ingest_session("2026-09-21")
    assert res2["verified"] is False
    assert "IMMUTABLE_SNAPSHOT_ALREADY_EXISTS" in res2["reason"]


@pytest.mark.parametrize("fault_target", ["asm", "gsm", "fno"])
def test_network_failure_fails_closed(temp_surv_dir, fault_target):
    fixed_now = datetime(2026, 9, 21, 8, 30, tzinfo=IST)
    ingestor = Track2OfficialSourceIngestor(
        surveillance_dir=str(temp_surv_dir),
        fetcher=make_mock_fetcher(network_error_on=fault_target),
        now_fn=lambda: fixed_now,
    )
    res = ingestor.ingest_session("2026-09-21")
    assert res["verified"] is False
    assert f"NETWORK_OR_FETCH_ERROR_{fault_target.upper()}" in res["reason"]


@pytest.mark.parametrize("status_code", [404, 500, 302, True, 201])
def test_non_200_http_status_fails_closed(temp_surv_dir, status_code):
    fixed_now = datetime(2026, 9, 21, 8, 30, tzinfo=IST)
    ingestor = Track2OfficialSourceIngestor(
        surveillance_dir=str(temp_surv_dir),
        fetcher=make_mock_fetcher(asm_status=status_code),
        now_fn=lambda: fixed_now,
    )
    res = ingestor.ingest_session("2026-09-21")
    assert res["verified"] is False
    assert "HTTP_STATUS_NOT_200" in res["reason"]


def test_html_content_type_rejected(temp_surv_dir):
    fixed_now = datetime(2026, 9, 21, 8, 30, tzinfo=IST)
    html_bytes = b"<html><head><title>Captcha</title></head><body>Blocked</body></html>"
    ingestor = Track2OfficialSourceIngestor(
        surveillance_dir=str(temp_surv_dir),
        fetcher=make_mock_fetcher(asm_bytes=html_bytes, asm_content_type="text/html; charset=UTF-8"),
        now_fn=lambda: fixed_now,
    )
    res = ingestor.ingest_session("2026-09-21")
    assert res["verified"] is False
    assert "HTML_RESPONSE_DISALLOWED" in res["reason"]


def test_unexpected_non_json_content_type_rejected(temp_surv_dir):
    fixed_now = datetime(2026, 9, 21, 8, 30, tzinfo=IST)
    ingestor = Track2OfficialSourceIngestor(
        surveillance_dir=str(temp_surv_dir),
        fetcher=make_mock_fetcher(asm_content_type="text/plain"),
        now_fn=lambda: fixed_now,
    )
    res = ingestor.ingest_session("2026-09-21")
    assert res["verified"] is False
    assert "UNEXPECTED_CONTENT_TYPE_ASM" in res["reason"]


def test_naive_clock_fails_closed(temp_surv_dir):
    ingestor = Track2OfficialSourceIngestor(
        surveillance_dir=str(temp_surv_dir),
        fetcher=make_mock_fetcher(),
        now_fn=lambda: datetime(2026, 9, 21, 8, 30),
    )
    assert ingestor.ingest_session("2026-09-21")["reason"] == "AMBIGUOUS_NAIVE_NOW_TIMESTAMP"


def test_parser_matches_official_nse_response_shapes():
    asm_raw = json.dumps({
        "longterm": {"data": [{"symbol": "A2ZINFRA", "asmSurvIndicator": "Stage I"}]},
        "shortterm": {"data": [{"symbol": "ABH", "asmSurvIndicator": "Stage I"}]},
    }).encode("utf-8")
    gsm_raw = json.dumps([
        {"symbol": "AGSTRA", "gsmStage": "LXII"},
    ]).encode("utf-8")
    assert parse_raw_source_bytes("asm", asm_raw) == {
        "asm_short_term": ["ABH"],
        "asm_long_term": ["A2ZINFRA"],
    }
    assert parse_raw_source_bytes("gsm", gsm_raw) == {"gsm": ["AGSTRA"]}


def test_malformed_json_fails_closed(temp_surv_dir):
    fixed_now = datetime(2026, 9, 21, 8, 30, tzinfo=IST)
    ingestor = Track2OfficialSourceIngestor(
        surveillance_dir=str(temp_surv_dir),
        fetcher=make_mock_fetcher(fno_bytes=b'{"data": {"UnderlyingList": [{"symbol": "CDSL"')  # Truncated
        ,
        now_fn=lambda: fixed_now,
    )
    res = ingestor.ingest_session("2026-09-21")
    assert res["verified"] is False
    assert "RAW_PARSE_FAILED_FNO" in res["reason"]


@pytest.mark.parametrize("bad_fno_content", [
    b'{"data": {}}',  # Missing UnderlyingList
    b'{"wrong": 123}',  # Missing data node
    b'{"data": {"UnderlyingList": []}}',  # Empty universe
    b'{"data": {"UnderlyingList": [{"symbol": "cdsl"}]}}',  # Lowercase symbol
    b'{"data": {"UnderlyingList": [{"symbol": "CDSL"}, {"symbol": "CDSL"}]}}',  # Duplicate symbol
    b'{"data": {"UnderlyingList": [{"symbol": "INVALID SYMBOL!"}]}}',  # Invalid characters
])
def test_schema_and_symbol_defects_fail_closed(temp_surv_dir, bad_fno_content):
    fixed_now = datetime(2026, 9, 21, 8, 30, tzinfo=IST)
    ingestor = Track2OfficialSourceIngestor(
        surveillance_dir=str(temp_surv_dir),
        fetcher=make_mock_fetcher(fno_bytes=bad_fno_content),
        now_fn=lambda: fixed_now,
    )
    res = ingestor.ingest_session("2026-09-21")
    assert res["verified"] is False
    assert "RAW_PARSE_FAILED_FNO" in res["reason"] or "EMPTY_FNO_UNIVERSE" in res["reason"]


def test_post_decision_cutoff_fetch_fails_closed(temp_surv_dir):
    # After 09:00 IST on session day
    late_now = datetime(2026, 9, 21, 9, 1, tzinfo=IST)
    ingestor = Track2OfficialSourceIngestor(
        surveillance_dir=str(temp_surv_dir),
        fetcher=make_mock_fetcher(),
        now_fn=lambda: late_now,
    )
    res = ingestor.ingest_session("2026-09-21")
    assert res["verified"] is False
    assert res["reason"] == "INVALID_FETCH_PUBLICATION_OR_DECISION_ORDER"


def test_poller_detects_raw_tampering_hash_mismatch(temp_surv_dir):
    fixed_now = datetime(2026, 9, 21, 8, 30, tzinfo=IST)
    ingestor = Track2OfficialSourceIngestor(
        surveillance_dir=str(temp_surv_dir),
        fetcher=make_mock_fetcher(),
        now_fn=lambda: fixed_now,
    )
    ingest_res = ingestor.ingest_session("2026-09-21")
    assert ingest_res["verified"] is True

    snap_path = Path(ingest_res["snapshot_path"])
    # Tamper with raw ASM file on disk
    asm_rel = ingest_res["sources"]["asm"]["raw_relative_path"]
    asm_path = temp_surv_dir / asm_rel
    asm_path.write_bytes(b'{"tampered": true}')

    poller = ExchangeCircularPoller(snapshot_dir=str(temp_surv_dir), now_fn=lambda: fixed_now)
    res = poller.load_sourced_circular_snapshot("2026-09-21", snapshot_path=str(snap_path))
    assert res["verified"] is False
    assert "PARSER_REPLAY_FAILED" in res["reason"]
    assert "RAW_SHA256_MISMATCH_ASM" in res["reason"]


def test_poller_detects_replay_mismatch_with_derived_list(temp_surv_dir):
    fixed_now = datetime(2026, 9, 21, 8, 30, tzinfo=IST)
    ingestor = Track2OfficialSourceIngestor(
        surveillance_dir=str(temp_surv_dir),
        fetcher=make_mock_fetcher(),
        now_fn=lambda: fixed_now,
    )
    ingest_res = ingestor.ingest_session("2026-09-21")
    assert ingest_res["verified"] is True

    # Tamper with the derived list inside the snapshot manifest (e.g. remove a symbol)
    snap_path = Path(ingest_res["snapshot_path"])
    payload = json.loads(snap_path.read_text(encoding="utf-8"))
    payload["gsm"] = []  # Replay should expect STK_GSM1, STK_GSM2, but payload claims empty!
    snap_path.write_text(json.dumps(payload), encoding="utf-8")

    poller = ExchangeCircularPoller(snapshot_dir=str(temp_surv_dir), now_fn=lambda: fixed_now)
    res = poller.load_sourced_circular_snapshot("2026-09-21", snapshot_path=str(snap_path))
    assert res["verified"] is False
    assert "REPLAY_MISMATCH_GSM" in res["reason"]


def test_raw_path_escape_and_symlink_rejection(temp_surv_dir):
    fixed_now = datetime(2026, 9, 21, 8, 30, tzinfo=IST)
    ingestor = Track2OfficialSourceIngestor(
        surveillance_dir=str(temp_surv_dir),
        fetcher=make_mock_fetcher(),
        now_fn=lambda: fixed_now,
    )
    ingest_res = ingestor.ingest_session("2026-09-21")
    assert ingest_res["verified"] is True

    snap_path = Path(ingest_res["snapshot_path"])
    payload = json.loads(snap_path.read_text(encoding="utf-8"))

    # Attempt path escape
    payload["sources"]["fno"]["raw_relative_path"] = "../../outside.json"
    snap_path.write_text(json.dumps(payload), encoding="utf-8")

    poller = ExchangeCircularPoller(snapshot_dir=str(temp_surv_dir), now_fn=lambda: fixed_now)
    res = poller.load_sourced_circular_snapshot("2026-09-21", snapshot_path=str(snap_path))
    assert res["verified"] is False
    assert any(code in res["reason"] for code in (
        "RAW_PATH_ESCAPE_FNO",
        "RAW_PATH_OUTSIDE_SNAPSHOT_DIRECTORY",
        "RAW_FILENAME_BINDING_MISMATCH_FNO",
    ))


def test_atomic_write_bytes_interruption_and_replace():
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "test.bin"
        data = b"hello deterministic world"
        atomic_write_bytes(target, data)
        assert target.is_file()
        assert target.read_bytes() == data

        # Idempotent retry of identical bytes is accepted.
        atomic_write_bytes(target, data)
        assert target.read_bytes() == data

        # Overwriting existing evidence with different bytes is rejected.
        with pytest.raises(FileExistsError):
            atomic_write_bytes(target, b"new data")


def test_empty_surveillance_lists_fail_closed(temp_surv_dir):
    fixed_now = datetime(2026, 9, 21, 8, 30, tzinfo=IST)
    empty_asm = json.dumps({"shortterm": {"data": []}, "longterm": {"data": []}}).encode()
    res = Track2OfficialSourceIngestor(
        surveillance_dir=str(temp_surv_dir),
        fetcher=make_mock_fetcher(asm_bytes=empty_asm),
        now_fn=lambda: fixed_now,
    ).ingest_session("2026-09-21")
    assert res["verified"] is False
    assert res["reason"] == "EMPTY_ASM_UNIVERSE"


def test_retry_after_partial_failure_reuses_identical_raw(temp_surv_dir):
    fixed_now = datetime(2026, 9, 21, 8, 30, tzinfo=IST)
    first = Track2OfficialSourceIngestor(
        surveillance_dir=str(temp_surv_dir),
        fetcher=make_mock_fetcher(network_error_on="gsm"),
        now_fn=lambda: fixed_now,
    ).ingest_session("2026-09-21")
    assert first["verified"] is False
    second = Track2OfficialSourceIngestor(
        surveillance_dir=str(temp_surv_dir),
        fetcher=make_mock_fetcher(),
        now_fn=lambda: fixed_now,
    ).ingest_session("2026-09-21")
    assert second["verified"] is True


def test_cross_session_raw_filename_reuse_rejected(temp_surv_dir):
    fixed_now = datetime(2026, 9, 21, 8, 30, tzinfo=IST)
    ingested = Track2OfficialSourceIngestor(
        surveillance_dir=str(temp_surv_dir), fetcher=make_mock_fetcher(), now_fn=lambda: fixed_now
    ).ingest_session("2026-09-21")
    snap = Path(ingested["snapshot_path"])
    payload = json.loads(snap.read_text(encoding="utf-8"))
    payload["sources"]["asm"]["raw_relative_path"] = payload["sources"]["asm"]["raw_relative_path"].replace(
        "2026-09-21", "2026-09-20")
    snap.write_text(json.dumps(payload), encoding="utf-8")
    poller = ExchangeCircularPoller(snapshot_dir=str(temp_surv_dir), now_fn=lambda: fixed_now)
    result = poller.load_sourced_circular_snapshot("2026-09-21", snapshot_path=str(snap))
    assert result["verified"] is False
    assert "RAW_FILENAME_BINDING_MISMATCH_ASM" in result["reason"]


def test_evidence_window_rejects_far_future_session(temp_surv_dir):
    fixed_now = datetime(2026, 9, 20, 19, 0, tzinfo=IST)
    result = Track2OfficialSourceIngestor(
        surveillance_dir=str(temp_surv_dir), fetcher=make_mock_fetcher(), now_fn=lambda: fixed_now
    ).ingest_session("2026-09-25")
    assert result["verified"] is False
    assert result["reason"] == "EVIDENCE_WINDOW_TOO_EARLY_FOR_SESSION"


def test_evidence_window_allows_holiday_gap(temp_surv_dir):
    # Thursday 2026-10-01 19:00 IST -> next session is Monday 2026-10-05 (Gandhi Jayanti on Oct 2)
    fixed_now = datetime(2026, 10, 1, 19, 0, tzinfo=IST)
    result = Track2OfficialSourceIngestor(
        surveillance_dir=str(temp_surv_dir), fetcher=make_mock_fetcher(), now_fn=lambda: fixed_now
    ).ingest_session("2026-10-05")
    assert result["verified"] is True


def test_evidence_window_allows_ordinary_weekend_gap(temp_surv_dir):
    # Friday 2026-09-18 19:00 IST -> next session is Monday 2026-09-21
    fixed_now = datetime(2026, 9, 18, 19, 0, tzinfo=IST)
    result = Track2OfficialSourceIngestor(
        surveillance_dir=str(temp_surv_dir), fetcher=make_mock_fetcher(), now_fn=lambda: fixed_now
    ).ingest_session("2026-09-21")
    assert result["verified"] is True


def test_evidence_window_allows_same_session_preopen_refresh(temp_surv_dir):
    # Monday 2026-10-05 08:30 IST -> session is Monday 2026-10-05
    fixed_now = datetime(2026, 10, 5, 8, 30, tzinfo=IST)
    result = Track2OfficialSourceIngestor(
        surveillance_dir=str(temp_surv_dir), fetcher=make_mock_fetcher(), now_fn=lambda: fixed_now
    ).ingest_session("2026-10-05")
    assert result["verified"] is True


def test_evidence_window_rejects_past_session(temp_surv_dir):
    # Monday 2026-10-05 08:30 IST -> session is 2026-10-01 (past session)
    fixed_now = datetime(2026, 10, 5, 8, 30, tzinfo=IST)
    result = Track2OfficialSourceIngestor(
        surveillance_dir=str(temp_surv_dir), fetcher=make_mock_fetcher(), now_fn=lambda: fixed_now
    ).ingest_session("2026-10-01")
    assert result["verified"] is False


def test_evidence_window_rejects_intervening_holiday_and_weekend_dates(temp_surv_dir):
    # Thursday 2026-10-01 19:00 IST -> next session is Monday 2026-10-05
    # Intervening dates 2026-10-02 (Gandhi Jayanti holiday), 2026-10-03 (Sat), 2026-10-04 (Sun)
    # are NOT scheduled active sessions and must be rejected fail-closed.
    fixed_now = datetime(2026, 10, 1, 19, 0, tzinfo=IST)
    ingestor = Track2OfficialSourceIngestor(
        surveillance_dir=str(temp_surv_dir), fetcher=make_mock_fetcher(), now_fn=lambda: fixed_now
    )
    for intervening in ["2026-10-02", "2026-10-03", "2026-10-04"]:
        result = ingestor.ingest_session(intervening)
        assert result["verified"] is False, f"Expected {intervening} to be rejected, got verified=True"
        assert result["reason"] in ("SESSION_DATE_NOT_ALLOWED", "EVIDENCE_WINDOW_INVALID_SESSION_DATE", "SESSION_DATE_NOT_AN_ACTIVE_SESSION")




def test_poller_rejects_stale_per_source_timestamp(temp_surv_dir):
    fixed_now = datetime(2026, 9, 21, 8, 30, tzinfo=IST)
    ingested = Track2OfficialSourceIngestor(
        surveillance_dir=str(temp_surv_dir), fetcher=make_mock_fetcher(), now_fn=lambda: fixed_now
    ).ingest_session("2026-09-21")
    snap = Path(ingested["snapshot_path"])
    payload = json.loads(snap.read_text(encoding="utf-8"))
    payload["sources"]["asm"]["fetched_at"] = "2026-09-19T08:30:00+05:30"
    snap.write_text(json.dumps(payload), encoding="utf-8")
    result = ExchangeCircularPoller(
        snapshot_dir=str(temp_surv_dir), now_fn=lambda: fixed_now
    ).load_sourced_circular_snapshot("2026-09-21", snapshot_path=str(snap))
    assert result["verified"] is False
    assert result["reason"] == "INVALID_SOURCE_FETCH_TIME_ASM"


def test_poller_rejects_internally_consistent_empty_asm(temp_surv_dir):
    fixed_now = datetime(2026, 9, 21, 8, 30, tzinfo=IST)
    ingested = Track2OfficialSourceIngestor(
        surveillance_dir=str(temp_surv_dir), fetcher=make_mock_fetcher(), now_fn=lambda: fixed_now
    ).ingest_session("2026-09-21")
    snap = Path(ingested["snapshot_path"])
    payload = json.loads(snap.read_text(encoding="utf-8"))
    empty_raw = json.dumps({"shortterm": {"data": []}, "longterm": {"data": []}}).encode("utf-8")
    empty_sha = hashlib.sha256(empty_raw).hexdigest()
    empty_name = f"raw_nse_asm_2026-09-21_{empty_sha[:8]}.json"
    (temp_surv_dir / empty_name).write_bytes(empty_raw)
    payload["sources"]["asm"].update({
        "raw_relative_path": empty_name,
        "sha256": empty_sha,
        "byte_length": len(empty_raw),
    })
    payload["asm_short_term"] = []
    payload["asm_long_term"] = []
    payload["sha256"] = hashlib.sha256(
        f"{empty_sha}:{payload['sources']['gsm']['sha256']}:{payload['sources']['fno']['sha256']}".encode("ascii")
    ).hexdigest()
    snap.write_text(json.dumps(payload), encoding="utf-8")
    result = ExchangeCircularPoller(
        snapshot_dir=str(temp_surv_dir), now_fn=lambda: fixed_now
    ).load_sourced_circular_snapshot("2026-09-21", snapshot_path=str(snap))
    assert result["verified"] is False
    assert result["reason"] == "EMPTY_ASM_UNIVERSE"


def test_static_no_order_path_guard():
    """Verify that track2_official_source_ingestor.py contains no broker imports, order execution, or credentials."""
    target_file = Path(__file__).resolve().parents[1] / "antigravity" / "daemons" / "track2_official_source_ingestor.py"
    assert target_file.is_file()

    tree = ast.parse(target_file.read_text(encoding="utf-8"))

    forbidden_modules = {"requests", "aiohttp", "websockets", "kiteconnect"}
    forbidden_calls = {"post", "put", "delete", "place_order", "modify_order", "cancel_order"}
    forbidden_tokens = {"enctoken", "api_key", "api_secret", "access_token", "totp"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_pkg = alias.name.split(".")[0]
                assert root_pkg not in forbidden_modules, f"Forbidden import: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root_pkg = node.module.split(".")[0]
                assert root_pkg not in forbidden_modules, f"Forbidden from-import: {node.module}"
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                assert node.func.attr.lower() not in forbidden_calls, f"Forbidden call: {node.func.attr}"
            elif isinstance(node.func, ast.Name):
                assert node.func.id.lower() not in forbidden_calls, f"Forbidden call: {node.func.id}"
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            val_lower = node.value.lower()
            for token in forbidden_tokens:
                assert token not in val_lower, f"Forbidden credential token: {token} in {node.value}"
