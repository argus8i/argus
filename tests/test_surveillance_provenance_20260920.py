"""
test_surveillance_provenance_20260920.py - Provenance & Authenticity Tests for Exchange Circular Poller
Part of Project Swing Trades (Antigravity + Claude Code + OpenAI Codex).

Validates strict provenance requirements for exchange circular ingestion:
1. Official NSE/BSE HTTPS URL enforcement (reject non-HTTPS, reject unofficial domains).
2. HTTP 200 response requirement (reject 404, 500, non-int, bools).
3. Raw evidence verification: raw_path and raw_sha256 verified against raw bytes on disk.
4. Parser version verification.
5. Exact effective session date matching (and Friday-for-Monday exchange calendar logic).
6. Timezone-aware timestamp parsing and non-future timestamps.
7. Explicit ASM/GSM/FNO list schema validation (lists of strings, no booleans).
8. Legacy self-hashed summary without raw evidence is rejected.
9. Isolated tempfile test environments leaving zero production modifications.
"""

import hashlib
import json
import os
import shutil
import tempfile
import pytest
from datetime import datetime, timezone, timedelta

from antigravity.daemons.exchange_circular_poller import ExchangeCircularPoller
from antigravity.models.track2_universe_scanner import EXPANDED_FNO_UNIVERSE


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp(prefix="surv_prov_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def create_mock_raw_circular(dir_path: str, filename: str = "surv_bulletin_20260918.csv", content: str = "SYMBOL,SERIES,ASM,GSM\nCDSL,EQ,0,0\nANGELONE,EQ,0,0\n") -> tuple[str, str]:
    raw_path = os.path.join(dir_path, filename)
    raw_bytes = content.encode("utf-8")
    with open(raw_path, "wb") as f:
        f.write(raw_bytes)
    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    return raw_path, sha256


def create_valid_snapshot_payload(raw_filename: str, raw_sha256: str) -> dict:
    return {
        "snapshot_id": "NSE_SURV_20260918_VALID",
        "publication_date": "2026-09-18",
        "effective_session_date": "2026-09-21",
        "fetched_at": "2026-09-18 19:15:00 IST",
        "source_url": "https://nsearchives.nseindia.com/content/circulars/surv_bulletin_20260918.csv",
        "http_status": 200,
        "raw_path": raw_filename,
        "raw_sha256": raw_sha256,
        "parser_version": "2.0.0",
        "parse_status": "SUCCESS",
        "asm_short_term": [],
        "asm_long_term": [],
        "gsm": [],
        "fno_underlyings": [c["symbol"] for c in EXPANDED_FNO_UNIVERSE]
    }


def test_raw_integrity_without_parser_lineage_fails_closed(temp_dir):
    """Raw integrity is useful evidence, but not source or parsed-list authenticity."""
    raw_path, raw_sha = create_mock_raw_circular(temp_dir, "bulletin.csv")
    snap_file = os.path.join(temp_dir, "snapshot_valid.json")
    hist_file = os.path.join(temp_dir, "history.json")
    log_file = os.path.join(temp_dir, "poller.log")

    payload = create_valid_snapshot_payload(os.path.basename(raw_path), raw_sha)
    with open(snap_file, "w", encoding="utf-8") as f:
        json.dump(payload, f)

    poller = ExchangeCircularPoller(history_path=hist_file, log_path=log_file, snapshot_dir=temp_dir)
    res = poller.load_sourced_circular_snapshot("2026-09-21", snapshot_path=snap_file)

    assert res["verified"] is False
    assert res["integrity_verified"] is True
    assert res["source_authenticated"] is False
    assert res["lineage_bound_to_raw_parser_output"] is False
    assert res["parser_version"] == "2.0.0"
    assert res["raw_sha256"] == raw_sha
    assert res["http_status"] == 200
    assert len(res["fno_underlyings"]) == len(EXPANDED_FNO_UNIVERSE)


def test_legacy_self_hashed_summary_without_raw_evidence_rejected(temp_dir):
    """Verifies that legacy snapshot without raw_path / raw_sha256 is strictly rejected."""
    snap_file = os.path.join(temp_dir, "legacy_snapshot.json")
    hist_file = os.path.join(temp_dir, "history.json")
    log_file = os.path.join(temp_dir, "poller.log")

    legacy_payload = {
        "snapshot_id": "LEGACY_SNAPSHOT_001",
        "publication_date": "2026-09-18",
        "effective_session_date": "2026-09-21",
        "fetched_at": "2026-09-18 19:15:00",
        "source_url": "https://nsearchives.nseindia.com/content/circulars/surv.csv",
        "parse_status": "SUCCESS",
        "asm_short_term": [],
        "asm_long_term": [],
        "gsm": [],
        "fno_underlyings": ["CDSL"],
        "sha256": "abcdef1234567890"  # Self-hashed without raw evidence
    }
    with open(snap_file, "w", encoding="utf-8") as f:
        json.dump(legacy_payload, f)

    poller = ExchangeCircularPoller(history_path=hist_file, log_path=log_file, snapshot_dir=temp_dir)
    res = poller.load_sourced_circular_snapshot("2026-09-21", snapshot_path=snap_file)

    assert res["verified"] is False
    assert res["reason"] == "LEGACY_SELF_HASHED_WITHOUT_RAW_EVIDENCE_REJECTED"


def test_reject_raw_bytes_sha256_mismatch(temp_dir):
    """Verifies that if raw circular bytes do not match declared raw_sha256, verification fails."""
    raw_path, raw_sha = create_mock_raw_circular(temp_dir, "bulletin.csv", content="Original content")
    snap_file = os.path.join(temp_dir, "snapshot.json")
    hist_file = os.path.join(temp_dir, "history.json")
    log_file = os.path.join(temp_dir, "poller.log")

    # Pass corrupt sha
    payload = create_valid_snapshot_payload(os.path.basename(raw_path), "0000000000000000000000000000000000000000000000000000000000000000")
    with open(snap_file, "w", encoding="utf-8") as f:
        json.dump(payload, f)

    poller = ExchangeCircularPoller(history_path=hist_file, log_path=log_file, snapshot_dir=temp_dir)
    res = poller.load_sourced_circular_snapshot("2026-09-21", snapshot_path=snap_file)

    assert res["verified"] is False
    assert "RAW_EVIDENCE_VERIFICATION_FAILED" in res["reason"]
    assert "RAW_SHA256_MISMATCH" in res["reason"]


def test_reject_missing_raw_file(temp_dir):
    """Verifies that if the raw evidence file does not exist, verification fails."""
    snap_file = os.path.join(temp_dir, "snapshot.json")
    hist_file = os.path.join(temp_dir, "history.json")
    log_file = os.path.join(temp_dir, "poller.log")

    payload = create_valid_snapshot_payload("non_existent_file.csv", "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890")
    with open(snap_file, "w", encoding="utf-8") as f:
        json.dump(payload, f)

    poller = ExchangeCircularPoller(history_path=hist_file, log_path=log_file, snapshot_dir=temp_dir)
    res = poller.load_sourced_circular_snapshot("2026-09-21", snapshot_path=snap_file)

    assert res["verified"] is False
    assert "RAW_FILE_NOT_FOUND" in res["reason"]


def test_reject_non_https_and_unofficial_url(temp_dir):
    """Verifies that HTTP (non-HTTPS) and unofficial hosts are rejected."""
    raw_path, raw_sha = create_mock_raw_circular(temp_dir, "bulletin.csv")
    snap_file = os.path.join(temp_dir, "snapshot.json")
    hist_file = os.path.join(temp_dir, "history.json")
    log_file = os.path.join(temp_dir, "poller.log")

    poller = ExchangeCircularPoller(history_path=hist_file, log_path=log_file, snapshot_dir=temp_dir)

    # 1. Non-HTTPS
    p1 = create_valid_snapshot_payload(os.path.basename(raw_path), raw_sha)
    p1["source_url"] = "http://nsearchives.nseindia.com/content/circulars/bulletin.csv"
    with open(snap_file, "w", encoding="utf-8") as f:
        json.dump(p1, f)
    res1 = poller.load_sourced_circular_snapshot("2026-09-21", snapshot_path=snap_file)
    assert res1["verified"] is False
    assert res1["reason"] == "SOURCE_URL_NOT_HTTPS"

    # 2. Unofficial domain
    p2 = create_valid_snapshot_payload(os.path.basename(raw_path), raw_sha)
    p2["source_url"] = "https://example.com/circulars/bulletin.csv"
    with open(snap_file, "w", encoding="utf-8") as f:
        json.dump(p2, f)
    res2 = poller.load_sourced_circular_snapshot("2026-09-21", snapshot_path=snap_file)
    assert res2["verified"] is False
    assert "SOURCE_URL_NOT_OFFICIAL_EXCHANGE" in res2["reason"]


def test_reject_invalid_http_status(temp_dir):
    """Verifies that http_status other than 200 (or non-int) is rejected."""
    raw_path, raw_sha = create_mock_raw_circular(temp_dir, "bulletin.csv")
    snap_file = os.path.join(temp_dir, "snapshot.json")
    hist_file = os.path.join(temp_dir, "history.json")
    log_file = os.path.join(temp_dir, "poller.log")

    poller = ExchangeCircularPoller(history_path=hist_file, log_path=log_file, snapshot_dir=temp_dir)

    # 404
    p = create_valid_snapshot_payload(os.path.basename(raw_path), raw_sha)
    p["http_status"] = 404
    with open(snap_file, "w", encoding="utf-8") as f:
        json.dump(p, f)
    res = poller.load_sourced_circular_snapshot("2026-09-21", snapshot_path=snap_file)
    assert res["verified"] is False
    assert "INVALID_HTTP_STATUS_404" in res["reason"]

    # True (boolean masquerading as int 1)
    p["http_status"] = True
    with open(snap_file, "w", encoding="utf-8") as f:
        json.dump(p, f)
    res_bool = poller.load_sourced_circular_snapshot("2026-09-21", snapshot_path=snap_file)
    assert res_bool["verified"] is False
    assert "INVALID_HTTP_STATUS_True" in res_bool["reason"]


def test_reject_future_timestamp(temp_dir):
    """Verifies that timestamps in the future beyond allowable skew are rejected."""
    raw_path, raw_sha = create_mock_raw_circular(temp_dir, "bulletin.csv")
    snap_file = os.path.join(temp_dir, "snapshot.json")
    hist_file = os.path.join(temp_dir, "history.json")
    log_file = os.path.join(temp_dir, "poller.log")

    poller = ExchangeCircularPoller(history_path=hist_file, log_path=log_file, snapshot_dir=temp_dir)

    p = create_valid_snapshot_payload(os.path.basename(raw_path), raw_sha)
    future_time = datetime.now(timezone(timedelta(hours=5, minutes=30))) + timedelta(days=2)
    p["fetched_at"] = future_time.strftime("%Y-%m-%d %H:%M:%S IST")
    with open(snap_file, "w", encoding="utf-8") as f:
        json.dump(p, f)

    res = poller.load_sourced_circular_snapshot("2026-09-21", snapshot_path=snap_file)
    assert res["verified"] is False
    assert "FETCHED_AT_IN_FUTURE" in res["reason"]


def test_reject_invalid_list_types(temp_dir):
    """Verifies that non-list or list containing booleans/non-strings is rejected."""
    raw_path, raw_sha = create_mock_raw_circular(temp_dir, "bulletin.csv")
    snap_file = os.path.join(temp_dir, "snapshot.json")
    hist_file = os.path.join(temp_dir, "history.json")
    log_file = os.path.join(temp_dir, "poller.log")

    poller = ExchangeCircularPoller(history_path=hist_file, log_path=log_file, snapshot_dir=temp_dir)

    # 1. asm_short_term is a dict instead of list
    p1 = create_valid_snapshot_payload(os.path.basename(raw_path), raw_sha)
    p1["asm_short_term"] = {"CDSL": 1}
    with open(snap_file, "w", encoding="utf-8") as f:
        json.dump(p1, f)
    res1 = poller.load_sourced_circular_snapshot("2026-09-21", snapshot_path=snap_file)
    assert res1["verified"] is False
    assert "SCHEMA_DEFECT_INVALID_TYPE_ASM_SHORT_TERM" in res1["reason"]

    # 2. fno_underlyings contains a boolean
    p2 = create_valid_snapshot_payload(os.path.basename(raw_path), raw_sha)
    p2["fno_underlyings"] = ["CDSL", True]
    with open(snap_file, "w", encoding="utf-8") as f:
        json.dump(p2, f)
    res2 = poller.load_sourced_circular_snapshot("2026-09-21", snapshot_path=snap_file)
    assert res2["verified"] is False
    assert "SCHEMA_DEFECT_ELEMENT_NOT_STRING_FNO_UNDERLYINGS" in res2["reason"]


def test_poll_and_update_disqualifies_all_on_missing_raw_evidence(temp_dir):
    """Verifies that poll_and_update fails closed and disqualifies all candidates if raw evidence is missing."""
    hist_file = os.path.join(temp_dir, "history.json")
    log_file = os.path.join(temp_dir, "poller.log")
    fake_snap = os.path.join(temp_dir, "missing_snapshot.json")

    poller = ExchangeCircularPoller(history_path=hist_file, log_path=log_file, snapshot_dir=temp_dir)
    report = poller.poll_and_update("2026-09-21", snapshot_path=fake_snap)

    assert report["qualified_count"] == 0
    assert report["disqualified_count"] == len(EXPANDED_FNO_UNIVERSE)
    assert all(d["status"] == "DISQUALIFIED_UNKNOWN" for d in report["disqualified"])


def test_friday_effective_date_cannot_substitute_for_monday(temp_dir):
    raw_path, raw_sha = create_mock_raw_circular(temp_dir, "bulletin.csv")
    snap_file = os.path.join(temp_dir, "snapshot.json")
    payload = create_valid_snapshot_payload(os.path.basename(raw_path), raw_sha)
    payload["effective_session_date"] = "2026-09-18"
    with open(snap_file, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    poller = ExchangeCircularPoller(snapshot_dir=temp_dir,
                                    history_path=os.path.join(temp_dir, "h.json"),
                                    log_path=os.path.join(temp_dir, "l.log"))
    result = poller.load_sourced_circular_snapshot("2026-09-21", snap_file)
    assert result["verified"] is False
    assert "SESSION_DATE_MISMATCH" in result["reason"]


def test_raw_path_cannot_escape_snapshot_directory(temp_dir):
    snapshot_dir = os.path.join(temp_dir, "snapshots")
    os.makedirs(snapshot_dir)
    outside_path, outside_sha = create_mock_raw_circular(temp_dir, "outside.csv")
    snap_file = os.path.join(snapshot_dir, "snapshot.json")
    payload = create_valid_snapshot_payload("../outside.csv", outside_sha)
    with open(snap_file, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    poller = ExchangeCircularPoller(snapshot_dir=snapshot_dir,
                                    history_path=os.path.join(temp_dir, "h.json"),
                                    log_path=os.path.join(temp_dir, "l.log"))
    result = poller.load_sourced_circular_snapshot("2026-09-21", snap_file)
    assert result["verified"] is False
    assert "RAW_PATH_OUTSIDE_SNAPSHOT_DIRECTORY" in result["reason"]


@pytest.mark.parametrize("fetched_at", ["2026-09-18 19:15:00", "2026-09-21"])
def test_naive_or_date_only_timestamp_rejected(temp_dir, fetched_at):
    raw_path, raw_sha = create_mock_raw_circular(temp_dir, "bulletin.csv")
    snap_file = os.path.join(temp_dir, "snapshot.json")
    payload = create_valid_snapshot_payload(os.path.basename(raw_path), raw_sha)
    payload["fetched_at"] = fetched_at
    with open(snap_file, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    poller = ExchangeCircularPoller(snapshot_dir=temp_dir,
                                    history_path=os.path.join(temp_dir, "h.json"),
                                    log_path=os.path.join(temp_dir, "l.log"))
    result = poller.load_sourced_circular_snapshot("2026-09-21", snap_file)
    assert result["reason"] == "INVALID_FETCHED_AT_TIMESTAMP"


def test_post_decision_evidence_rejected(temp_dir):
    raw_path, raw_sha = create_mock_raw_circular(temp_dir, "bulletin.csv")
    snap_file = os.path.join(temp_dir, "snapshot.json")
    payload = create_valid_snapshot_payload(os.path.basename(raw_path), raw_sha)
    payload["effective_session_date"] = "2026-09-19"
    payload["fetched_at"] = "2026-09-19T09:15:00+05:30"
    with open(snap_file, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    poller = ExchangeCircularPoller(snapshot_dir=temp_dir,
                                    history_path=os.path.join(temp_dir, "h.json"),
                                    log_path=os.path.join(temp_dir, "l.log"))
    result = poller.load_sourced_circular_snapshot("2026-09-19", snap_file)
    assert result["reason"] == "INVALID_FETCH_PUBLICATION_OR_DECISION_ORDER"


def test_unsupported_parser_and_duplicate_symbols_rejected(temp_dir):
    raw_path, raw_sha = create_mock_raw_circular(temp_dir, "bulletin.csv")
    snap_file = os.path.join(temp_dir, "snapshot.json")
    poller = ExchangeCircularPoller(snapshot_dir=temp_dir,
                                    history_path=os.path.join(temp_dir, "h.json"),
                                    log_path=os.path.join(temp_dir, "l.log"))
    payload = create_valid_snapshot_payload(os.path.basename(raw_path), raw_sha)
    payload["parser_version"] = "999.0"
    with open(snap_file, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    assert "INVALID_PARSER_VERSION" in poller.load_sourced_circular_snapshot(
        "2026-09-21", snap_file)["reason"]
    payload = create_valid_snapshot_payload(os.path.basename(raw_path), raw_sha)
    payload["fno_underlyings"] = ["CDSL", "CDSL"]
    with open(snap_file, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    assert "DUPLICATE_FNO_UNDERLYINGS" in poller.load_sourced_circular_snapshot(
        "2026-09-21", snap_file)["reason"]
