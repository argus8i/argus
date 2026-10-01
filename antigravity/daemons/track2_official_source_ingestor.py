"""
track2_official_source_ingestor.py - Official NSE Raw Ingestion & Surveillance Ingestor
Part of Project Swing Trades (Track 2 Phase 1B2).

Mandate:
- Implement frozen mandate in shared/reviews/track2_phase1b2_source_ingestion_mandate_20260920.md exactly.
- Fetch all three official NSE endpoints as independent raw responses:
    1. ASM: https://www.nseindia.com/api/reportASM
    2. GSM: https://www.nseindia.com/api/reportGSM
    3. F&O: https://www.nseindia.com/api/underlying-information
- Persist raw response bytes before parsing, using atomic writes under the Track 2 surveillance directory.
- Record per source: exact URL, HTTP status, fetched-at timezone-aware timestamp, content type, byte length, SHA-256, raw relative path, and parser version.
- Parse deterministically from persisted raw bytes, never from caller-supplied derived symbol list.
- Produce canonical uppercase, sorted, duplicate-free sets for ASM short-term, ASM long-term, GSM, and active F&O underlyings.
- Fail closed on network failure, non-200 status, wrong official host/path, redirects outside allowlist, HTML/unexpected content type, malformed JSON, missing schema nodes, invalid/duplicate symbols, empty F&O universe, timestamp ambiguity/future time, stale or wrong-session evidence, raw-path escape/symlink, hash mismatch, parser-version mismatch, or replay mismatch.
- Paper observation only; no live orders, broker APIs, or credentials.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.parse
import time as time_mod
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

# Ensure repository root is on sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Timezone and Exchange constants
IST = timezone(timedelta(hours=5, minutes=30), name="IST")
DECISION_CUTOFF_TIME = time(9, 0)
PARSER_VERSION = "2.0.0"
MIN_INTERVAL_SECONDS = 4.0

OFFICIAL_NSE_HOSTS = {
    "www.nseindia.com",
    "nseindia.com",
}

EXPECTED_ENDPOINTS = {
    "asm": "https://www.nseindia.com/api/reportASM",
    "gsm": "https://www.nseindia.com/api/reportGSM",
    "fno": "https://www.nseindia.com/api/underlying-information",
}

DEFAULT_SURVEILLANCE_DIR = os.path.join(REPO_ROOT, "shared", "track2_liquid", "surveillance")
SYMBOL_REGEX = re.compile(r"^[A-Z0-9&._-]+$")
PHASE1B2_IMPLEMENTATION_COMPLETE = True
MAX_RAW_RESPONSE_BYTES = 10 * 1024 * 1024
MAX_EVIDENCE_AGE_DAYS = 1


def parse_aware_timestamp(ts_val: Any) -> Optional[datetime]:
    """
    Parses timestamp into an aware datetime object.
    Supports explicit ISO offsets (+05:30, Z) or the literal ' IST' suffix.
    Naive timestamps and date-only values are strictly rejected.
    """
    if not isinstance(ts_val, str) or not ts_val.strip():
        return None

    s = ts_val.strip()
    if s.endswith(" IST"):
        s_clean = s[:-4].strip()
        try:
            dt = datetime.strptime(s_clean, "%Y-%m-%d %H:%M:%S")
            return dt.replace(tzinfo=IST)
        except ValueError:
            pass

    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return None
        return dt.astimezone(IST)
    except Exception:
        pass

    return None


def atomic_write_bytes(target_path: Path, data: bytes) -> None:
    """
    Atomically writes raw bytes to target_path using a temporary file in the same directory.
    Flushes and syncs before replacing. Overwrite of existing files is rejected.
    """
    target_path = Path(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        if target_path.is_file() and target_path.read_bytes() == data:
            return
        raise FileExistsError(f"Target file already exists with different bytes: {target_path}")

    temp_fd, temp_name = tempfile.mkstemp(
        prefix=f".{target_path.name}.tmp_",
        dir=str(target_path.parent)
    )
    try:
        with os.fdopen(temp_fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        # A hard-link publishes the fully synced temporary inode only if the
        # destination does not already exist. Unlike os.replace, it cannot
        # overwrite a snapshot created by a concurrent ingestor.
        try:
            os.link(temp_name, target_path)
        except FileExistsError:
            if target_path.is_file() and target_path.read_bytes() == data:
                return
            raise
        finally:
            if os.path.exists(temp_name):
                os.remove(temp_name)
    except Exception:
        if os.path.exists(temp_name):
            try:
                os.remove(temp_name)
            except OSError:
                pass
        raise


def parse_raw_source_bytes(
    source_key: str,
    raw_bytes: bytes,
    parser_version: str = PARSER_VERSION,
) -> Dict[str, Any]:
    """
    Deterministically parses raw response bytes into canonical sorted, duplicate-free sets.
    Fails closed on malformed JSON, unexpected types, missing nodes, invalid symbols,
    duplicate symbols in raw nodes, or empty F&O underlyings.
    """
    if parser_version != PARSER_VERSION:
        raise ValueError(f"UNSUPPORTED_PARSER_VERSION: {parser_version}")

    try:
        text = raw_bytes.decode("utf-8")
        data = json.loads(text)
    except UnicodeDecodeError as e:
        raise ValueError(f"RAW_BYTES_NOT_UTF8: {e}") from e
    except json.JSONDecodeError as e:
        raise ValueError(f"MALFORMED_JSON: {e}") from e

    def _extract_symbol_list(items: Any, list_name: str) -> List[str]:
        if not isinstance(items, list):
            raise ValueError(f"NODE_NOT_LIST_{list_name}")
        extracted: List[str] = []
        seen: Set[str] = set()
        for idx, item in enumerate(items):
            if isinstance(item, dict):
                # May have 'symbol' or 'Symbol'
                sym = item.get("symbol") or item.get("Symbol")
            elif isinstance(item, str):
                sym = item
            else:
                raise ValueError(f"INVALID_ITEM_TYPE_IN_{list_name}_{type(item).__name__}")

            if not isinstance(sym, str) or not sym.strip():
                raise ValueError(f"EMPTY_OR_NON_STRING_SYMBOL_IN_{list_name}")
            cleaned = sym.strip()
            if cleaned != cleaned.upper() or not SYMBOL_REGEX.fullmatch(cleaned):
                raise ValueError(f"INVALID_SYMBOL_FORMAT_{cleaned}_IN_{list_name}")
            if cleaned in seen:
                raise ValueError(f"DUPLICATE_SYMBOL_{cleaned}_IN_{list_name}")
            seen.add(cleaned)
            extracted.append(cleaned)
        return sorted(extracted)

    if source_key == "asm":
        # Official /api/reportASM schema: shortterm.data and longterm.data.
        if not isinstance(data, dict):
            raise ValueError("ASM_ROOT_NODE_NOT_DICT")
        short_node = data.get("shortterm")
        long_node = data.get("longterm")
        if not isinstance(short_node, dict) or not isinstance(long_node, dict):
            raise ValueError("ASM_MISSING_SHORTTERM_OR_LONGTERM_OBJECT")
        if "data" not in short_node or "data" not in long_node:
            raise ValueError("ASM_MISSING_SHORTTERM_OR_LONGTERM_DATA")
        st_symbols = _extract_symbol_list(short_node["data"], "ASM_SHORT_TERM")
        lt_symbols = _extract_symbol_list(long_node["data"], "ASM_LONG_TERM")
        return {
            "asm_short_term": st_symbols,
            "asm_long_term": lt_symbols,
        }

    elif source_key == "gsm":
        # Official /api/reportGSM schema is a top-level JSON array.
        if not isinstance(data, list):
            raise ValueError("GSM_ROOT_NODE_NOT_LIST")
        gsm_symbols = _extract_symbol_list(data, "GSM")
        return {
            "gsm": gsm_symbols,
        }

    elif source_key == "fno":
        # Mandate line 13: "The F&O response contains data.UnderlyingList; each record has a symbol."
        if not isinstance(data, dict) or "data" not in data or not isinstance(data["data"], dict):
            raise ValueError("FNO_MISSING_DATA_OBJECT")
        d_obj = data["data"]
        if "UnderlyingList" not in d_obj:
            raise ValueError("FNO_MISSING_UNDERLYING_LIST")
        fno_symbols = _extract_symbol_list(d_obj["UnderlyingList"], "FNO_UNDERLYINGS")
        if not fno_symbols:
            raise ValueError("EMPTY_FNO_UNIVERSE")
        return {
            "fno_underlyings": fno_symbols,
        }

    else:
        raise ValueError(f"UNKNOWN_SOURCE_KEY: {source_key}")


def replay_and_verify_sources(
    sources_meta: Mapping[str, Any],
    base_dir: str | Path,
    expected_lists: Mapping[str, Sequence[str]],
    parser_version: str = PARSER_VERSION,
    expected_session_date: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Independently reopens every raw file, verifies hash against declared metadata,
    re-runs the parser, and checks exact equality against expected derived lists.
    Fails closed on any discrepancy or path escape.
    """
    base_dir = Path(base_dir).resolve()
    for key in ("asm", "gsm", "fno"):
        if key not in sources_meta:
            return False, f"MISSING_SOURCE_META_{key.upper()}"
        meta = sources_meta[key]
        if not isinstance(meta, dict):
            return False, f"INVALID_SOURCE_META_TYPE_{key.upper()}"

        raw_rel = meta.get("raw_relative_path")
        declared_sha = meta.get("sha256")
        meta_parser_ver = meta.get("parser_version")

        if not isinstance(raw_rel, str) or not raw_rel.strip():
            return False, f"RAW_PATH_INVALID_{key.upper()}"
        if not isinstance(declared_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", declared_sha.strip().lower()):
            return False, f"SHA256_INVALID_{key.upper()}"
        if meta_parser_ver != parser_version:
            return False, f"PARSER_VERSION_MISMATCH_{key.upper()}"
        if expected_session_date is not None:
            expected_name = f"raw_nse_{key}_{expected_session_date}_{declared_sha[:8]}.json"
            if Path(raw_rel).name != expected_name or raw_rel != Path(raw_rel).name:
                return False, f"RAW_FILENAME_BINDING_MISMATCH_{key.upper()}"

        target_path = (base_dir / raw_rel).resolve()
        # Security: check escape and symlinks
        try:
            target_path.relative_to(base_dir)
        except ValueError:
            return False, f"RAW_PATH_ESCAPE_{key.upper()}"
        if target_path.is_symlink() or (base_dir / raw_rel).is_symlink():
            return False, f"RAW_PATH_SYMLINK_{key.upper()}"
        if not target_path.is_file():
            return False, f"RAW_FILE_NOT_FOUND_{key.upper()}"

        raw_bytes = target_path.read_bytes()
        computed_sha = hashlib.sha256(raw_bytes).hexdigest().lower()
        if computed_sha != declared_sha.strip().lower():
            return False, f"RAW_SHA256_MISMATCH_{key.upper()}"

        if meta.get("byte_length") != len(raw_bytes):
            return False, f"BYTE_LENGTH_MISMATCH_{key.upper()}"

        # Replay parser
        try:
            parsed = parse_raw_source_bytes(key, raw_bytes, parser_version=parser_version)
        except Exception as e:
            return False, f"PARSER_REPLAY_ERROR_{key.upper()}_{e}"

        # Compare with expected lists
        if key == "asm":
            if parsed.get("asm_short_term") != list(expected_lists.get("asm_short_term", [])):
                return False, "REPLAY_MISMATCH_ASM_SHORT_TERM"
            if parsed.get("asm_long_term") != list(expected_lists.get("asm_long_term", [])):
                return False, "REPLAY_MISMATCH_ASM_LONG_TERM"
        elif key == "gsm":
            if parsed.get("gsm") != list(expected_lists.get("gsm", [])):
                return False, "REPLAY_MISMATCH_GSM"
        elif key == "fno":
            if parsed.get("fno_underlyings") != list(expected_lists.get("fno_underlyings", [])):
                return False, "REPLAY_MISMATCH_FNO_UNDERLYINGS"

    return True, None


class Track2OfficialSourceIngestor:
    """
    Ingests official NSE raw surveillance and F&O data.
    Ensures fail-closed network validation, atomic persistence of raw bytes,
    deterministic parsing, and creation of immutable snapshot manifests.
    """

    def __init__(
        self,
        surveillance_dir: str = DEFAULT_SURVEILLANCE_DIR,
        fetcher: Optional[Callable[[str], Tuple[int, bytes, Dict[str, str]]]] = None,
        now_fn: Optional[Callable[[], datetime]] = None,
        pacing_seconds: float = MIN_INTERVAL_SECONDS,
        sleep_fn: Optional[Callable[[float], None]] = None,
    ):
        self.surveillance_dir = Path(surveillance_dir).resolve()
        self.fetcher = fetcher or self._default_network_fetcher
        self.now_fn = now_fn or (lambda: datetime.now(IST))
        if pacing_seconds < MIN_INTERVAL_SECONDS:
            raise ValueError(f"pacing_seconds ({pacing_seconds}) must be >= {MIN_INTERVAL_SECONDS}")
        self.pacing_seconds = pacing_seconds
        self.sleep_fn = sleep_fn or time_mod.sleep

    @staticmethod
    def _validate_url(url: str, expected_url: str) -> Optional[str]:
        if url != expected_url:
            return f"URL_MISMATCH_EXPECTED_{expected_url}"
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https":
            return "URL_NOT_HTTPS"
        host = (parsed.hostname or "").lower()
        if host not in OFFICIAL_NSE_HOSTS or parsed.username or parsed.password:
            return f"UNAUTHORIZED_HOST_{parsed.netloc}"
        return None

    @classmethod
    def _default_network_fetcher(cls, url: str) -> Tuple[int, bytes, Dict[str, str]]:
        """
        Default HTTP GET client using standard library urllib.
        Strictly enforces HTTPS, no credentials, redirect allowlist on official NSE hosts.
        """
        import urllib.request

        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https":
            raise ValueError(f"Insecure scheme: {parsed.scheme}")
        if (parsed.hostname or "").lower() not in OFFICIAL_NSE_HOSTS:
            raise ValueError(f"Disallowed host: {parsed.netloc}")

        class StrictRedirectHandler(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                new_parsed = urllib.parse.urlparse(newurl)
                if new_parsed.scheme != "https":
                    raise ValueError(f"Redirect to non-https: {newurl}")
                if (new_parsed.hostname or "").lower() not in OFFICIAL_NSE_HOSTS:
                    raise ValueError(f"Redirect to disallowed host: {new_parsed.netloc}")
                return super().redirect_request(req, fp, code, msg, headers, newurl)

        opener = urllib.request.build_opener(StrictRedirectHandler)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        with opener.open(req, timeout=15) as resp:
            if resp.geturl() != url:
                raise ValueError(f"Unexpected final URL: {resp.geturl()}")
            status = resp.status
            raw_bytes = resp.read(MAX_RAW_RESPONSE_BYTES + 1)
            if len(raw_bytes) > MAX_RAW_RESPONSE_BYTES:
                raise ValueError("Response exceeds maximum allowed size")
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return status, raw_bytes, headers

    def ingest_session(
        self,
        session_date: str,
        publication_date: Optional[str] = None,
        snapshot_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Performs full ingestion of all three endpoints for session_date.
        Persists raw bytes atomically, parses them, produces the snapshot payload,
        and saves the snapshot JSON atomically.
        Fails closed on any error.
        """
        now = self.now_fn()
        if not isinstance(now, datetime) or now.tzinfo is None:
            return {"verified": False, "reason": "AMBIGUOUS_NAIVE_NOW_TIMESTAMP"}
        now = now.astimezone(IST)

        try:
            session_day = date.fromisoformat(session_date)
        except ValueError:
            return {"verified": False, "reason": "INVALID_SESSION_DATE_FORMAT"}

        if publication_date is None:
            # These endpoints are state snapshots, so provenance begins on fetch date.
            publication_date = now.date().isoformat()
        try:
            pub_day = date.fromisoformat(publication_date)
        except ValueError:
            return {"verified": False, "reason": "INVALID_PUBLICATION_DATE_FORMAT"}

        if pub_day > now.date() or session_day < pub_day:
            return {"verified": False, "reason": "FUTURE_OR_INVERTED_PUBLICATION_EFFECTIVE_DATE"}
        if session_day < now.date():
            return {"verified": False, "reason": "SESSION_DATE_IN_PAST"}
        from scripts.daily_pipeline import calculate_next_session_date
        next_session = calculate_next_session_date(now.date())
        if session_day > next_session:
            return {"verified": False, "reason": "EVIDENCE_WINDOW_TOO_EARLY_FOR_SESSION"}
        if session_day not in {now.date(), next_session}:
            return {"verified": False, "reason": "SESSION_DATE_NOT_AN_ACTIVE_SESSION"}

        # Decision cutoff at 09:00 IST on session day
        decision_cutoff = datetime.combine(session_day, DECISION_CUTOFF_TIME, tzinfo=IST)
        if now.date() < pub_day or now >= decision_cutoff:
            return {"verified": False, "reason": "INVALID_FETCH_PUBLICATION_OR_DECISION_ORDER"}

        self.surveillance_dir.mkdir(parents=True, exist_ok=True)
        snapshot_filename = f"nse_surveillance_snapshot_{session_date}.json"
        snapshot_path = self.surveillance_dir / snapshot_filename

        # Mandate line 46-47: "Never mutate an existing immutable session snapshot."
        if snapshot_path.exists():
            return {
                "verified": False,
                "reason": f"IMMUTABLE_SNAPSHOT_ALREADY_EXISTS: {snapshot_path.name}",
                "snapshot_path": str(snapshot_path),
            }

        if snapshot_id is None:
            snapshot_id = f"NSE_SURV_{session_date.replace('-', '')}_INGESTED"

        if not re.fullmatch(r"[A-Z0-9][A-Z0-9_.-]{5,127}", snapshot_id):
            return {"verified": False, "reason": "INVALID_SNAPSHOT_ID"}

        sources_meta: Dict[str, Any] = {}
        parsed_results: Dict[str, Any] = {
            "asm_short_term": [],
            "asm_long_term": [],
            "gsm": [],
            "fno_underlyings": [],
        }

        # Step 1: Fetch and atomically persist all 3 raw sources
        for idx, key in enumerate(("asm", "gsm", "fno")):
            if idx > 0 and self.pacing_seconds > 0:
                self.sleep_fn(self.pacing_seconds)
            target_url = EXPECTED_ENDPOINTS[key]
            err = self._validate_url(target_url, EXPECTED_ENDPOINTS[key])
            if err:
                return {"verified": False, "reason": f"URL_VALIDATION_FAILED_{key.upper()}_{err}"}

            try:
                status, raw_bytes, headers = self.fetcher(target_url)
            except Exception as e:
                return {"verified": False, "reason": f"NETWORK_OR_FETCH_ERROR_{key.upper()}_{e}"}

            fetch_time = self.now_fn()
            if not isinstance(fetch_time, datetime) or fetch_time.tzinfo is None:
                return {"verified": False, "reason": f"AMBIGUOUS_NAIVE_FETCH_TIMESTAMP_{key.upper()}"}
            fetch_time = fetch_time.astimezone(IST)
            if (fetch_time - now).total_seconds() > 60.0:
                return {"verified": False, "reason": f"FETCHED_AT_IN_FUTURE_{key.upper()}"}
            if fetch_time >= decision_cutoff:
                return {"verified": False, "reason": f"FETCH_COMPLETED_AFTER_DECISION_CUTOFF_{key.upper()}"}

            if type(status) is bool or not isinstance(status, int) or status != 200:
                return {"verified": False, "reason": f"HTTP_STATUS_NOT_200_{key.upper()}_{status}"}

            content_type = headers.get("content-type", "")
            if not isinstance(content_type, str):
                return {"verified": False, "reason": f"INVALID_CONTENT_TYPE_TYPE_{key.upper()}"}
            # Disallow HTML or unexpected content types
            if "text/html" in content_type.lower() or "<html" in raw_bytes[:100].decode("utf-8", errors="ignore").lower():
                return {"verified": False, "reason": f"HTML_RESPONSE_DISALLOWED_{key.upper()}"}
            media_type = content_type.split(";", 1)[0].strip().lower()
            if media_type not in {"application/json", "text/json"}:
                return {"verified": False, "reason": f"UNEXPECTED_CONTENT_TYPE_{key.upper()}_{media_type}"}

            if not raw_bytes:
                return {"verified": False, "reason": f"EMPTY_RAW_BYTES_{key.upper()}"}
            if len(raw_bytes) > MAX_RAW_RESPONSE_BYTES:
                return {"verified": False, "reason": f"RAW_RESPONSE_TOO_LARGE_{key.upper()}"}

            raw_sha = hashlib.sha256(raw_bytes).hexdigest().lower()
            raw_filename = f"raw_nse_{key}_{session_date}_{raw_sha[:8]}.json"
            raw_target_path = self.surveillance_dir / raw_filename

            # Atomic persist raw bytes
            try:
                atomic_write_bytes(raw_target_path, raw_bytes)
            except Exception as e:
                return {"verified": False, "reason": f"RAW_PERSIST_FAILED_{key.upper()}_{e}"}

            # Step 2: Parse deterministically from raw bytes
            try:
                parsed = parse_raw_source_bytes(key, raw_bytes, parser_version=PARSER_VERSION)
            except Exception as e:
                return {"verified": False, "reason": f"RAW_PARSE_FAILED_{key.upper()}_{e}"}

            if key == "asm":
                parsed_results["asm_short_term"] = parsed["asm_short_term"]
                parsed_results["asm_long_term"] = parsed["asm_long_term"]
            elif key == "gsm":
                parsed_results["gsm"] = parsed["gsm"]
            elif key == "fno":
                parsed_results["fno_underlyings"] = parsed["fno_underlyings"]

            sources_meta[key] = {
                "source_url": target_url,
                "http_status": status,
                "fetched_at": fetch_time.isoformat(timespec="seconds"),
                "content_type": content_type,
                "byte_length": len(raw_bytes),
                "sha256": raw_sha,
                "raw_relative_path": raw_filename,
                "parser_version": PARSER_VERSION,
            }

        # Step 3: Verify all lists are non-empty for F&O, uppercase, sorted, no duplicates
        if not parsed_results["fno_underlyings"]:
            return {"verified": False, "reason": "EMPTY_FNO_UNIVERSE"}
        if not parsed_results["asm_short_term"] and not parsed_results["asm_long_term"]:
            return {"verified": False, "reason": "EMPTY_ASM_UNIVERSE"}
        if not parsed_results["gsm"]:
            return {"verified": False, "reason": "EMPTY_GSM_UNIVERSE"}

        # Combine raw SHA256s deterministically for top-level backward compatibility
        composite_sha = hashlib.sha256(
            f"{sources_meta['asm']['sha256']}:{sources_meta['gsm']['sha256']}:{sources_meta['fno']['sha256']}".encode("ascii")
        ).hexdigest().lower()

        snapshot_payload = {
            "snapshot_id": snapshot_id,
            "publication_date": publication_date,
            "effective_session_date": session_date,
            "fetched_at": now.isoformat(timespec="seconds"),
            "source_url": EXPECTED_ENDPOINTS["fno"],  # Canonical top-level reference
            "http_status": 200,
            "raw_path": sources_meta["fno"]["raw_relative_path"],
            "raw_sha256": sources_meta["fno"]["sha256"],
            "parser_version": PARSER_VERSION,
            "parse_status": "SUCCESS",
            "sha256": composite_sha,
            "sources": sources_meta,
            "asm_short_term": parsed_results["asm_short_term"],
            "asm_long_term": parsed_results["asm_long_term"],
            "gsm": parsed_results["gsm"],
            "fno_underlyings": parsed_results["fno_underlyings"],
        }

        # Step 4: Write snapshot artifact atomically
        snapshot_bytes = json.dumps(snapshot_payload, indent=2, sort_keys=True).encode("utf-8")
        try:
            atomic_write_bytes(snapshot_path, snapshot_bytes)
        except Exception as e:
            return {"verified": False, "reason": f"SNAPSHOT_PERSIST_FAILED_{e}"}

        return {
            "verified": True,
            "snapshot_path": str(snapshot_path),
            "snapshot_id": snapshot_id,
            "effective_session_date": session_date,
            "publication_date": publication_date,
            "sources": sources_meta,
            "asm_short_term_count": len(parsed_results["asm_short_term"]),
            "asm_long_term_count": len(parsed_results["asm_long_term"]),
            "gsm_count": len(parsed_results["gsm"]),
            "fno_underlyings_count": len(parsed_results["fno_underlyings"]),
        }
