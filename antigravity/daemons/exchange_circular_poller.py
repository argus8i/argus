"""
exchange_circular_poller.py - Sourced Exchange Circular Ingestion & Surveillance Auditor
Part of Project Swing Trades (Antigravity + Claude Code + OpenAI Codex).

Quantitative & Compliance Mandate:
- Closes Claude's Q16 condition from shared/04_OPEN_QUESTIONS.md:
    "ASM/GSM screening and daily F&O-membership verification remain mandatory for Track 2."
- Replaces fabricated surveillance with sourced, SHA-256 verified exchange circular artifacts.
- Enforces exchange trading session calendars (handling weekend/holiday notice transitions).
- Fails closed if the circular artifact is missing, unverified, corrupted, or stale.
- Enforces strict provenance requirements: official NSE/BSE HTTPS URL, HTTP 200, raw bytes sha256,
  parser version, strict type validation for ASM/GSM/FNO lists, timezone-aware non-future timestamps.
- Legacy self-hashed summaries without raw evidence are strictly rejected.
"""

import hashlib
import json
import math
import os
import re
import sys
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Any, Tuple

# Ensure repository root is on sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from antigravity.models.track2_surveillance_monitor import Track2SurveillanceMonitor, Track2SurveillanceState
from antigravity.models.track2_universe_scanner import EXPANDED_FNO_UNIVERSE

SURVEILLANCE_HISTORY_PATH = os.path.join(REPO_ROOT, "antigravity", "logs", "track2_surveillance_history.json")
CIRCULAR_LOG_PATH = os.path.join(REPO_ROOT, "antigravity", "logs", "circular_poller.log")
DEFAULT_SNAPSHOT_DIR = os.path.join(REPO_ROOT, "shared", "track2_liquid", "surveillance")

PARSER_VERSION = "2.0.0"

OFFICIAL_EXCHANGE_HOSTS = {
    "nsearchives.nseindia.com",
    "www.nseindia.com",
    "nseindia.com",
    "www.bseindia.com",
    "bseindia.com",
}


def parse_timestamp_tz(ts_val: Any) -> Optional[datetime]:
    """
    Parses timestamp string into an aware datetime object.
    Supports explicit ISO offsets or the literal IST suffix. Naive timestamps
    and date-only values are rejected because their ordering is ambiguous.
    """
    if not isinstance(ts_val, str) or not ts_val.strip():
        return None

    s = ts_val.strip()
    ist_tz = timezone(timedelta(hours=5, minutes=30))

    if s.endswith(" IST"):
        s_clean = s[:-4].strip()
        try:
            dt = datetime.strptime(s_clean, "%Y-%m-%d %H:%M:%S")
            return dt.replace(tzinfo=ist_tz)
        except ValueError:
            pass

    # Try ISO formats
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return None
        return dt.astimezone(ist_tz)
    except Exception:
        pass

    return None


class ExchangeCircularPoller:
    """
    Automates sourced circular ingestion and updates Track 2 surveillance state.
    Requires immutable, raw-evidence verified exchange snapshot artifacts.
    """

    def __init__(
        self,
        history_path: str = SURVEILLANCE_HISTORY_PATH,
        log_path: str = CIRCULAR_LOG_PATH,
        snapshot_dir: str = DEFAULT_SNAPSHOT_DIR,
        now_fn: Optional[Any] = None,
    ):
        self.history_path = history_path
        self.log_path = log_path
        self.snapshot_dir = snapshot_dir
        self.now_fn = now_fn or (lambda: datetime.now(timezone(timedelta(hours=5, minutes=30))))
        self.monitor = Track2SurveillanceMonitor(history_file=history_path)

    def log(self, msg: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}"
        print(line)
        try:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    @staticmethod
    def verify_raw_evidence(raw_path: str, expected_sha256: str, base_dir: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        """
        Verifies that raw_path exists and its raw bytes match expected_sha256 exactly.
        """
        if not raw_path or not isinstance(raw_path, str):
            return False, "RAW_PATH_MISSING_OR_INVALID"

        if not expected_sha256 or not isinstance(expected_sha256, str):
            return False, "RAW_SHA256_MISSING_OR_INVALID"

        resolved_path = raw_path
        if not os.path.isabs(resolved_path):
            if base_dir:
                resolved_path = os.path.join(base_dir, raw_path)
            else:
                resolved_path = os.path.join(REPO_ROOT, raw_path)

        resolved_path = os.path.realpath(resolved_path)
        if base_dir:
            allowed_root = os.path.realpath(base_dir)
            try:
                if os.path.commonpath([allowed_root, resolved_path]) != allowed_root:
                    return False, "RAW_PATH_OUTSIDE_SNAPSHOT_DIRECTORY"
            except ValueError:
                return False, "RAW_PATH_OUTSIDE_SNAPSHOT_DIRECTORY"
        if os.path.islink(resolved_path):
            return False, "RAW_PATH_SYMLINK_REJECTED"

        if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256.strip()):
            return False, "RAW_SHA256_NOT_64_HEX"

        if not os.path.exists(resolved_path) or os.path.isdir(resolved_path):
            return False, f"RAW_FILE_NOT_FOUND: {resolved_path}"

        h = hashlib.sha256()
        try:
            with open(resolved_path, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            computed_sha = h.hexdigest().lower()
            if computed_sha != expected_sha256.strip().lower():
                return False, f"RAW_SHA256_MISMATCH: computed {computed_sha} != declared {expected_sha256}"
            return True, None
        except Exception as e:
            return False, f"RAW_FILE_READ_ERROR: {e}"

    def load_sourced_circular_snapshot(
        self,
        target_session_date: str,
        snapshot_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Loads and validates an immutable daily exchange circular snapshot artifact.
        Enforces:
        1. Official NSE/BSE HTTPS source URL.
        2. HTTP 200 response verification.
        3. Raw evidence (raw_path and raw_sha256 matching raw bytes).
        4. Valid parser_version.
        5. Exact effective session date matching target date.
        6. Explicit ASM/GSM/FNO lists with correct types (list of non-empty strings, no bools).
        7. Timezone-aware timestamps not in the future.
        8. Rejection of legacy self-hashed summaries without raw evidence.
        Fails closed on any missing, unverified, corrupted, or stale artifact.
        """
        if not snapshot_path:
            snapshot_path = os.path.join(self.snapshot_dir, f"nse_surveillance_snapshot_{target_session_date}.json")

        if not os.path.exists(snapshot_path):
            self.log(f"FAIL-CLOSED: Surveillance snapshot not found at '{snapshot_path}'.")
            return {
                "verified": False,
                "reason": f"SURVEILLANCE_SNAPSHOT_MISSING: {snapshot_path}",
                "target_date": target_session_date
            }

        try:
            with open(snapshot_path, "r", encoding="utf-8") as f:
                artifact = json.load(f)
        except Exception as e:
            self.log(f"FAIL-CLOSED: Error reading surveillance snapshot: {e}")
            return {
                "verified": False,
                "reason": f"SURVEILLANCE_SNAPSHOT_PARSE_ERROR: {e}",
                "target_date": target_session_date
            }

        if not isinstance(artifact, dict):
            self.log("FAIL-CLOSED: Snapshot artifact is not a JSON dictionary.")
            return {
                "verified": False,
                "reason": "SCHEMA_DEFECT_NOT_A_DICT",
                "target_date": target_session_date
            }

        if not isinstance(artifact.get("snapshot_id"), str) or not re.fullmatch(
                r"[A-Z0-9][A-Z0-9_.-]{5,127}", artifact.get("snapshot_id", "")):
            return {"verified": False, "reason": "INVALID_SNAPSHOT_ID",
                    "target_date": target_session_date}

        # 1. Reject legacy self-hashed summary without raw evidence
        has_raw_evidence = ("raw_path" in artifact and artifact["raw_path"]) and ("raw_sha256" in artifact and artifact["raw_sha256"])
        if not has_raw_evidence:
            self.log("FAIL-CLOSED: Legacy self-hashed summary without raw evidence rejected.")
            return {
                "verified": False,
                "reason": "LEGACY_SELF_HASHED_WITHOUT_RAW_EVIDENCE_REJECTED",
                "target_date": target_session_date
            }

        # 2. Validate required schema fields
        required_keys = [
            "snapshot_id", "publication_date", "effective_session_date",
            "fetched_at", "parse_status", "source_url", "http_status",
            "raw_path", "raw_sha256", "parser_version",
            "asm_short_term", "asm_long_term", "gsm", "fno_underlyings"
        ]
        for k in required_keys:
            if k not in artifact or artifact[k] is None:
                self.log(f"FAIL-CLOSED: Missing required key '{k}' in snapshot artifact.")
                return {
                    "verified": False,
                    "reason": f"SCHEMA_DEFECT_MISSING_{k.upper()}",
                    "target_date": target_session_date
                }

        # 3. Validate source_url (Official NSE/BSE HTTPS URL)
        source_url = artifact.get("source_url")
        if not isinstance(source_url, str):
            self.log("FAIL-CLOSED: source_url is not a string.")
            return {
                "verified": False,
                "reason": "INVALID_SOURCE_URL_TYPE",
                "target_date": target_session_date
            }
        parsed_url = urllib.parse.urlparse(source_url)
        if parsed_url.scheme != "https":
            self.log(f"FAIL-CLOSED: source_url scheme is not https: '{source_url}'")
            return {
                "verified": False,
                "reason": "SOURCE_URL_NOT_HTTPS",
                "target_date": target_session_date
            }
        source_host = (parsed_url.hostname or "").lower()
        if (source_host not in OFFICIAL_EXCHANGE_HOSTS or parsed_url.username is not None
                or parsed_url.password is not None):
            self.log(f"FAIL-CLOSED: source_url host '{parsed_url.netloc}' is not an official NSE/BSE exchange domain.")
            return {
                "verified": False,
                "reason": f"SOURCE_URL_NOT_OFFICIAL_EXCHANGE: {parsed_url.netloc}",
                "target_date": target_session_date
            }

        # 4. Validate http_status (must be integer 200, reject bool)
        http_status = artifact.get("http_status")
        if type(http_status) is bool or not isinstance(http_status, int) or http_status != 200:
            self.log(f"FAIL-CLOSED: http_status is not HTTP 200: {http_status!r}")
            return {
                "verified": False,
                "reason": f"INVALID_HTTP_STATUS_{http_status}",
                "target_date": target_session_date
            }

        # 5. Validate parse_status and parser_version
        if artifact.get("parse_status") != "SUCCESS":
            self.log(f"FAIL-CLOSED: Artifact parse_status is not SUCCESS: {artifact.get('parse_status')}")
            return {
                "verified": False,
                "reason": f"PARSE_STATUS_FAILED_{artifact.get('parse_status')}",
                "target_date": target_session_date
            }

        parser_ver = artifact.get("parser_version")
        if parser_ver != PARSER_VERSION:
            self.log("FAIL-CLOSED: parser_version is missing or invalid.")
            return {
                "verified": False,
                "reason": f"INVALID_PARSER_VERSION_{parser_ver}",
                "target_date": target_session_date
            }

        # 6. Validate Raw Evidence: raw_path and raw_sha256 verified against raw bytes on disk
        snapshot_parent = os.path.dirname(os.path.abspath(snapshot_path))
        raw_ok, raw_err = self.verify_raw_evidence(
            artifact["raw_path"],
            artifact["raw_sha256"],
            base_dir=snapshot_parent
        )
        if not raw_ok:
            self.log(f"FAIL-CLOSED: Raw evidence verification failed: {raw_err}")
            return {
                "verified": False,
                "reason": f"RAW_EVIDENCE_VERIFICATION_FAILED: {raw_err}",
                "target_date": target_session_date
            }

        # 7. Validate ASM/GSM/FNO lists: must be explicit lists of strings (no bools)
        list_keys = ["asm_short_term", "asm_long_term", "gsm", "fno_underlyings"]
        for lk in list_keys:
            val = artifact.get(lk)
            if type(val) is not list:
                self.log(f"FAIL-CLOSED: Key '{lk}' must be a list, got {type(val).__name__}.")
                return {
                    "verified": False,
                    "reason": f"SCHEMA_DEFECT_INVALID_TYPE_{lk.upper()}",
                    "target_date": target_session_date
                }
            for item in val:
                if (type(item) is not str or not item.strip()
                        or item != item.strip().upper()
                        or not re.fullmatch(r"[A-Z0-9&._-]+", item)):
                    self.log(f"FAIL-CLOSED: Key '{lk}' contains non-string or empty element: {item!r}")
                    return {
                        "verified": False,
                        "reason": f"SCHEMA_DEFECT_ELEMENT_NOT_STRING_{lk.upper()}",
                        "target_date": target_session_date
                    }
            if len(set(val)) != len(val):
                return {
                    "verified": False,
                    "reason": f"SCHEMA_DEFECT_DUPLICATE_{lk.upper()}",
                    "target_date": target_session_date
                }

        # 8. Timezone and Non-Future Timestamp Verification
        fetched_at_str = artifact.get("fetched_at")
        fetched_dt = parse_timestamp_tz(fetched_at_str)
        if not fetched_dt:
            self.log(f"FAIL-CLOSED: fetched_at timestamp '{fetched_at_str}' cannot be parsed with timezone.")
            return {
                "verified": False,
                "reason": "INVALID_FETCHED_AT_TIMESTAMP",
                "target_date": target_session_date
            }

        # Check against now with 60s skew allowance
        ist_tz = timezone(timedelta(hours=5, minutes=30))
        now_dt = self.now_fn()
        if not isinstance(now_dt, datetime) or now_dt.tzinfo is None:
            return {"verified": False, "reason": "AMBIGUOUS_NAIVE_NOW_TIMESTAMP",
                    "target_date": target_session_date}
        now_dt = now_dt.astimezone(ist_tz)
        if (fetched_dt - now_dt).total_seconds() > 60.0:
            skew = (fetched_dt - now_dt).total_seconds()
            self.log(f"FAIL-CLOSED: fetched_at timestamp is in the future by {skew:.1f}s.")
            return {
                "verified": False,
                "reason": f"FETCHED_AT_IN_FUTURE_{skew:.1f}S",
                "target_date": target_session_date
            }

        # Publication/effective dates must be real ISO dates and cannot be future.
        try:
            publication_dt = datetime.strptime(artifact["publication_date"], "%Y-%m-%d").date()
            effective_dt = datetime.strptime(artifact["effective_session_date"], "%Y-%m-%d").date()
            target_dt = datetime.strptime(target_session_date, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return {"verified": False, "reason": "INVALID_PUBLICATION_OR_EFFECTIVE_DATE",
                    "target_date": target_session_date}
        if artifact["effective_session_date"] != target_session_date:
            return {
                "verified": False,
                "reason": f"SESSION_DATE_MISMATCH_EFFECTIVE_{artifact['effective_session_date']}",
                "target_date": target_session_date
            }
        if publication_dt > now_dt.date() or effective_dt > target_dt or publication_dt > effective_dt:
            return {"verified": False, "reason": "FUTURE_OR_INVERTED_PUBLICATION_EFFECTIVE_DATE",
                    "target_date": target_session_date}
        decision_cutoff = datetime.combine(effective_dt, datetime.min.time(), tzinfo=ist_tz).replace(
            hour=9, minute=0)
        if fetched_dt.date() < publication_dt or fetched_dt >= decision_cutoff:
            return {"verified": False, "reason": "INVALID_FETCH_PUBLICATION_OR_DECISION_ORDER",
                    "target_date": target_session_date}

        # 9. Effective Session Date Verification: exact Phase 1 contract.
        # 9. Multi-Source Replay & Lineage Verification (Phase 1B2 Mandate)
        # If 'sources' metadata is present, independently reopen every raw file,
        # verify hash, replay parser, and require exact equality with derived lists.
        sources_meta = artifact.get("sources")
        if isinstance(sources_meta, dict) and all(k in sources_meta for k in ("asm", "gsm", "fno")):
            if (effective_dt - fetched_dt.date()).days > 1:
                return {"verified": False, "reason": "EVIDENCE_WINDOW_TOO_EARLY_FOR_SESSION",
                        "target_date": target_session_date}
            from antigravity.daemons.track2_official_source_ingestor import (
                EXPECTED_ENDPOINTS,
                replay_and_verify_sources,
            )
            for source_key in ("asm", "gsm", "fno"):
                meta = sources_meta[source_key]
                if not isinstance(meta, dict):
                    return {"verified": False, "reason": f"INVALID_SOURCE_META_{source_key.upper()}",
                            "target_date": target_session_date}
                if meta.get("source_url") != EXPECTED_ENDPOINTS[source_key]:
                    return {"verified": False, "reason": f"SOURCE_URL_MISMATCH_{source_key.upper()}",
                            "target_date": target_session_date}
                source_status = meta.get("http_status")
                if type(source_status) is bool or not isinstance(source_status, int) or source_status != 200:
                    return {"verified": False, "reason": f"INVALID_SOURCE_HTTP_STATUS_{source_key.upper()}",
                            "target_date": target_session_date}
                source_type = meta.get("content_type")
                if not isinstance(source_type, str) or source_type.split(";", 1)[0].strip().lower() not in {
                        "application/json", "text/json"}:
                    return {"verified": False, "reason": f"INVALID_SOURCE_CONTENT_TYPE_{source_key.upper()}",
                            "target_date": target_session_date}
                source_fetched = parse_timestamp_tz(meta.get("fetched_at"))
                if (source_fetched is None or source_fetched.date() < publication_dt
                        or source_fetched >= decision_cutoff
                        or source_fetched < fetched_dt - timedelta(seconds=60)
                        or (effective_dt - source_fetched.date()).days > 1
                        or (source_fetched - now_dt).total_seconds() > 60.0):
                    return {"verified": False, "reason": f"INVALID_SOURCE_FETCH_TIME_{source_key.upper()}",
                            "target_date": target_session_date}
            expected_map = {
                "asm_short_term": artifact["asm_short_term"],
                "asm_long_term": artifact["asm_long_term"],
                "gsm": artifact["gsm"],
                "fno_underlyings": artifact["fno_underlyings"],
            }
            replay_ok, replay_err = replay_and_verify_sources(
                sources_meta,
                base_dir=snapshot_parent,
                expected_lists=expected_map,
                parser_version=artifact["parser_version"],
                expected_session_date=target_session_date,
            )
            if not replay_ok:
                self.log(f"FAIL-CLOSED: Parser replay verification failed: {replay_err}")
                return {
                    "verified": False,
                    "integrity_verified": True,
                    "source_authenticated": False,
                    "lineage_bound_to_raw_parser_output": False,
                    "reason": f"PARSER_REPLAY_FAILED: {replay_err}",
                    "target_date": target_session_date
                }
            if not artifact["asm_short_term"] and not artifact["asm_long_term"]:
                return {"verified": False, "reason": "EMPTY_ASM_UNIVERSE",
                        "target_date": target_session_date}
            if not artifact["gsm"]:
                return {"verified": False, "reason": "EMPTY_GSM_UNIVERSE",
                        "target_date": target_session_date}

            fno_meta = sources_meta["fno"]
            if (artifact["raw_path"] != fno_meta.get("raw_relative_path")
                    or artifact["raw_sha256"] != fno_meta.get("sha256")
                    or artifact["source_url"] != fno_meta.get("source_url")
                    or artifact["http_status"] != fno_meta.get("http_status")):
                return {"verified": False, "reason": "TOP_LEVEL_FNO_BINDING_MISMATCH",
                        "target_date": target_session_date}
            expected_composite = hashlib.sha256(
                f"{sources_meta['asm']['sha256']}:{sources_meta['gsm']['sha256']}:{sources_meta['fno']['sha256']}".encode("ascii")
            ).hexdigest().lower()
            if artifact.get("sha256") != expected_composite:
                return {"verified": False, "reason": "COMPOSITE_SOURCE_HASH_MISMATCH",
                        "target_date": target_session_date}

            # All sources verified against raw bytes and parser replay!
            return {
                "verified": True,
                "integrity_verified": True,
                "source_authenticated": True,
                "lineage_bound_to_raw_parser_output": True,
                "reason": "OFFICIAL_NSE_RAW_LINEAGE_VERIFIED",
                "snapshot_id": artifact["snapshot_id"],
                "fetched_at": artifact["fetched_at"],
                "publication_date": artifact["publication_date"],
                "effective_session_date": artifact["effective_session_date"],
                "source_url": artifact["source_url"],
                "http_status": artifact["http_status"],
                "raw_path": artifact["raw_path"],
                "raw_sha256": artifact["raw_sha256"],
                "parser_version": artifact["parser_version"],
                "sources": sources_meta,
                "asm_short_term": set(artifact["asm_short_term"]),
                "asm_long_term": set(artifact["asm_long_term"]),
                "gsm": set(artifact["gsm"]),
                "fno_underlyings": set(artifact["fno_underlyings"]),
                "target_date": target_session_date
            }

        # Single raw file or legacy artifact: integrity verified, but parser lineage unverified
        return {
            "verified": False,
            "integrity_verified": True,
            "source_authenticated": False,
            "lineage_bound_to_raw_parser_output": False,
            "reason": "SOURCE_AND_PARSED_LINEAGE_UNVERIFIED_PHASE1",
            "snapshot_id": artifact["snapshot_id"],
            "fetched_at": artifact["fetched_at"],
            "publication_date": artifact["publication_date"],
            "effective_session_date": artifact["effective_session_date"],
            "source_url": artifact["source_url"],
            "http_status": artifact["http_status"],
            "raw_path": artifact["raw_path"],
            "raw_sha256": artifact["raw_sha256"],
            "parser_version": artifact["parser_version"],
            "asm_short_term": set(artifact["asm_short_term"]),
            "asm_long_term": set(artifact["asm_long_term"]),
            "gsm": set(artifact["gsm"]),
            "fno_underlyings": set(artifact["fno_underlyings"]),
            "target_date": target_session_date
        }

    def poll_and_update(
        self,
        target_date_str: Optional[str] = None,
        snapshot_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Polls daily bulletins and updates surveillance history for all candidate underlyings.
        Fails closed on missing or unverified snapshots.
        """
        if not target_date_str:
            target_date_str = datetime.now().strftime("%Y-%m-%d")

        self.log(f"Starting Exchange Circular Audit for session: {target_date_str}")
        snapshot_res = self.load_sourced_circular_snapshot(target_date_str, snapshot_path=snapshot_path)

        audit_items = []

        if not snapshot_res.get("verified"):
            self.log(f"FAIL-CLOSED: Circular verification failed ({snapshot_res.get('reason')}). Disqualifying all scrips.")
            # Emit unverified checks (asm_stage=None, gsm_stage=None, checked_at=None)
            for candidate in EXPANDED_FNO_UNIVERSE:
                audit_items.append({
                    "symbol": candidate["symbol"],
                    "is_fno_underlying": False,
                    "asm_stage": None,
                    "gsm_stage": None,
                    "band_pct": candidate.get("band_pct", 0.0),
                    "checked_at": None
                })
        else:
            asm_set = snapshot_res["asm_short_term"].union(snapshot_res["asm_long_term"])
            gsm_set = snapshot_res["gsm"]
            fno_set = snapshot_res["fno_underlyings"]
            fetched_time = snapshot_res["fetched_at"]

            for candidate in EXPANDED_FNO_UNIVERSE:
                sym = candidate["symbol"]
                is_fno = sym in fno_set
                asm_stage = 1 if sym in asm_set else 0
                gsm_stage = 1 if sym in gsm_set else 0
                band_pct = 0.0 if is_fno else 10.0

                audit_items.append({
                    "symbol": sym,
                    "is_fno_underlying": is_fno,
                    "asm_stage": asm_stage,
                    "gsm_stage": gsm_stage,
                    "band_pct": band_pct,
                    "checked_at": fetched_time
                })

        report = self.monitor.run_daily_basket_audit(audit_items, target_date_str)
        report["qualification_eligible"] = bool(snapshot_res.get("verified"))
        report["provenance"] = {
            key: snapshot_res.get(key) for key in (
                "verified", "integrity_verified", "source_authenticated",
                "lineage_bound_to_raw_parser_output", "reason", "snapshot_id",
                "raw_sha256", "parser_version", "effective_session_date")
        }
        self.log(f"Circular Audit Complete: {report['qualified_count']}/{report['total_evaluated']} Qualified.")

        if report["disqualified_count"] > 0:
            for d in report["disqualified"]:
                self.log(f"  [DISQUALIFIED ALERT] {d['symbol']}: {d['status']} -> {d['reason']}")

        return report
