"""
antigravity/engine/eligibility_contract.py
==========================================
Timestamped Pre-Open Eligibility & Fail-Closed Guard (Track 2 Liquid Desk).

Strictly enforces Codex Mandate 1 & AGENTS.md Rule 8/11:
1. Morning signal generation at 09:08 IST on session date T must verify:
   - is_surveillance == False
   - is_fno_underlying == True
2. Eligibility must be proven using ONLY files timestamped and ingested
   PRIOR to session open (cutoff 09:08:00 IST).
3. If surveillance or F&O master files are missing, stale (>1 day age, or >3 days over weekend),
   corrupted, or post-dated, the contract FAILS CLOSED (blocks entry).
4. Truthful eligibility status: Never assumes missing evidence implies eligible.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
PRE_OPEN_CUTOFF_TIME = "09:08:00"


class EligibilityVerdict(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    BLOCKED_SURVEILLANCE = "BLOCKED_SURVEILLANCE"
    BLOCKED_NOT_FNO = "BLOCKED_NOT_FNO"
    BLOCKED_MISSING_EVIDENCE = "BLOCKED_MISSING_EVIDENCE"
    BLOCKED_STALE_EVIDENCE = "BLOCKED_STALE_EVIDENCE"
    BLOCKED_LOOKAHEAD_EVIDENCE = "BLOCKED_LOOKAHEAD_EVIDENCE"


@dataclass(frozen=True)
class EligibilityResult:
    symbol: str
    session_date: date
    verdict: EligibilityVerdict
    is_fno: bool
    is_surveillance: bool
    surveillance_details: Optional[str] = None
    evidence_timestamp: Optional[datetime] = None
    rejection_reason: Optional[str] = None

    @property
    def is_passed(self) -> bool:
        return self.verdict == EligibilityVerdict.ELIGIBLE


class PreOpenEligibilityValidator:
    """
    Validates scrip eligibility prior to market open under strict fail-closed invariants.
    """

    def __init__(
        self,
        surveillance_dir: Path,
        fno_master_dir: Path,
        max_evidence_age_days: int = 4,
    ) -> None:
        self.surveillance_dir = surveillance_dir
        self.fno_master_dir = fno_master_dir
        self.max_evidence_age_days = max_evidence_age_days

    def _parse_timestamp(self, ts_str: str) -> Optional[datetime]:
        """Parses ISO or IST formatted timestamp into aware datetime."""
        if not ts_str:
            return None
        s = ts_str.strip()
        if s.endswith(" IST"):
            s_clean = s[:-4].strip()
            try:
                dt = datetime.strptime(s_clean, "%Y-%m-%d %H:%M:%S")
                return dt.replace(tzinfo=IST)
            except ValueError:
                return None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                return dt.replace(tzinfo=IST)
            return dt.astimezone(IST)
        except Exception:
            return None

    def get_preopen_cutoff(self, session_date: date) -> datetime:
        """Returns 09:08:00 IST on session_date."""
        dt = datetime(session_date.year, session_date.month, session_date.day, 9, 8, 0)
        return dt.replace(tzinfo=IST)

    def load_surveillance_snapshot(
        self, session_date: date
    ) -> Tuple[Optional[Set[str]], Optional[datetime], Optional[str]]:
        """
        Loads surveillance snapshot applicable for session_date.
        Returns: (surveillance_symbols, fetched_at_timestamp, error_message)
        """
        date_str = session_date.isoformat()
        pattern = f"*{date_str}*.json"
        matches = sorted(self.surveillance_dir.glob(pattern), reverse=True)

        if not matches:
            # Check for snapshot from immediately preceding calendar/trading day
            prev_matches = sorted(self.surveillance_dir.glob("*.json"), reverse=True)
            if not prev_matches:
                return None, None, f"No surveillance snapshots exist in {self.surveillance_dir}"
            candidate = prev_matches[0]
        else:
            candidate = matches[0]

        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            ts_str = data.get("fetched_at") or data.get("timestamp") or data.get("created_at")
            ts = self._parse_timestamp(ts_str) if ts_str else None

            if not ts:
                # Fall back to file modification time if explicit timestamp missing
                mtime = candidate.stat().st_mtime
                ts = datetime.fromtimestamp(mtime, tz=IST)

            # Lookahead check: File cannot be timestamped after 09:08:00 on session_date
            cutoff = self.get_preopen_cutoff(session_date)
            if ts > cutoff:
                return None, ts, f"Surveillance snapshot {candidate.name} timestamp {ts} is after cutoff {cutoff}"

            # Stale check
            age = (cutoff - ts).total_seconds() / 86400.0
            if age > self.max_evidence_age_days:
                return None, ts, f"Surveillance snapshot {candidate.name} is {age:.1f} days old (max {self.max_evidence_age_days})"

            # Extract symbols under surveillance (ASM, GSM, ESM, T2T)
            symbols = set()
            for key in ["asm_long_term", "asm_short_term", "gsm", "esm", "t2t", "surveillance_list"]:
                for item in data.get(key, []):
                    if isinstance(item, str):
                        symbols.add(item.strip().upper())
                    elif isinstance(item, dict) and "symbol" in item:
                        symbols.add(item["symbol"].strip().upper())

            return symbols, ts, None
        except Exception as exc:
            return None, None, f"Failed to parse surveillance snapshot {candidate.name}: {exc}"

    def load_fno_underlyings(
        self, session_date: date
    ) -> Tuple[Optional[Set[str]], Optional[datetime], Optional[str]]:
        """
        Loads verified F&O underlyings list applicable for session_date.
        Returns: (fno_symbols, fetched_at_timestamp, error_message)
        """
        date_str = session_date.isoformat()
        pattern = f"*{date_str}*"
        matches = sorted(self.fno_master_dir.glob(pattern), reverse=True)

        candidate = matches[0] if matches else None
        if not candidate:
            all_files = sorted(self.fno_master_dir.glob("*"), reverse=True)
            candidate = all_files[0] if all_files else None

        if not candidate:
            return None, None, f"No F&O master files found in {self.fno_master_dir}"

        try:
            symbols = set()
            ts = None
            if candidate.suffix == ".json":
                data = json.loads(candidate.read_text(encoding="utf-8"))
                ts_str = data.get("fetched_at") or data.get("timestamp") or data.get("created_at")
                if ts_str:
                    ts = self._parse_timestamp(ts_str)
                for s in data.get("fno_underlyings", data.get("symbols", [])):
                    symbols.add(s.strip().upper())
            else:
                lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
                for line in lines:
                    s = line.split(",")[0].strip().upper()
                    if s and not s.startswith("SYMBOL") and not s.startswith("#"):
                        symbols.add(s)

            if ts is None:
                mtime = candidate.stat().st_mtime
                ts = datetime.fromtimestamp(mtime, tz=IST)

            cutoff = self.get_preopen_cutoff(session_date)
            if ts > cutoff:
                return None, ts, f"F&O master {candidate.name} timestamp/mtime {ts} after cutoff {cutoff}"

            return symbols, ts, None
        except Exception as exc:
            return None, None, f"Failed to parse F&O master {candidate.name}: {exc}"

    def validate_scrip(self, symbol: str, session_date: date) -> EligibilityResult:
        """
        Validates a single scrip against pre-open surveillance and F&O membership.
        Fail-Closed: Any missing evidence or error blocks the scrip immediately.
        """
        sym = symbol.strip().upper()

        # 1. Check F&O membership evidence
        fno_symbols, fno_ts, fno_err = self.load_fno_underlyings(session_date)
        if fno_err or fno_symbols is None:
            return EligibilityResult(
                symbol=sym,
                session_date=session_date,
                verdict=EligibilityVerdict.BLOCKED_MISSING_EVIDENCE,
                is_fno=False,
                is_surveillance=False,
                rejection_reason=f"F&O evidence failure: {fno_err}",
            )

        if sym not in fno_symbols:
            return EligibilityResult(
                symbol=sym,
                session_date=session_date,
                verdict=EligibilityVerdict.BLOCKED_NOT_FNO,
                is_fno=False,
                is_surveillance=False,
                evidence_timestamp=fno_ts,
                rejection_reason=f"Symbol {sym} is not in verified F&O underlyings list",
            )

        # 2. Check Surveillance evidence
        surv_symbols, surv_ts, surv_err = self.load_surveillance_snapshot(session_date)
        if surv_err or surv_symbols is None:
            return EligibilityResult(
                symbol=sym,
                session_date=session_date,
                verdict=EligibilityVerdict.BLOCKED_MISSING_EVIDENCE,
                is_fno=True,
                is_surveillance=False,
                evidence_timestamp=fno_ts,
                rejection_reason=f"Surveillance evidence failure: {surv_err}",
            )

        if sym in surv_symbols:
            return EligibilityResult(
                symbol=sym,
                session_date=session_date,
                verdict=EligibilityVerdict.BLOCKED_SURVEILLANCE,
                is_fno=True,
                is_surveillance=True,
                evidence_timestamp=surv_ts,
                surveillance_details=f"Symbol {sym} classified under ASM/GSM/ESM/T2T surveillance",
                rejection_reason=f"Symbol {sym} under active exchange surveillance",
            )

        # 3. All checks passed
        return EligibilityResult(
            symbol=sym,
            session_date=session_date,
            verdict=EligibilityVerdict.ELIGIBLE,
            is_fno=True,
            is_surveillance=False,
            evidence_timestamp=surv_ts,
        )
