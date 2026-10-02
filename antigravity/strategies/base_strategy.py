"""
antigravity/strategies/base_strategy.py
=======================================
Base classes and standard event contracts for quantitative swing alpha strategies.
Part of Project Swing Trades (ARGUS 8i Track 2 Liquid Desk).

Mathematical Guardrails & Invariants:
1. Fail-closed on missing, NaN, infinite, or corrupt bar data.
2. Inviolable AGENTS.md Rule 2 Price Floor: Any candidate under Rs 10.00 is immediately rejected.
3. Pre-registered configuration binding: loads and cryptographically validates against SPEC_MANIFEST.sha256.
4. Immutable SignalEvent contract: produces frozen dataclass events with immutable MappingProxy calculation traces.
5. Strict Stop and Target Ordering: Stop Loss < Reference Entry < Target Price (with finite positive values).
6. Strict T_PLUS_1 execution timing: entry_session must be strictly after session_date with valid ISO dates.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timezone, timedelta
import hashlib
import math
import numbers
from pathlib import Path
import re
import types
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union
import yaml


IST = timezone(timedelta(hours=5, minutes=30))
_ISO_DATE_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _deep_freeze(obj: Any) -> Any:
    """
    Recursively freezes mappings, sequences, and sets into deeply immutable types.
    Ensures leaves and mapping keys are strictly immutable:
    - Mapping -> types.MappingProxyType with validated/converted immutable scalar keys
    - list/tuple -> tuple with deeply frozen elements
    - set/frozenset -> frozenset with deeply frozen elements
    - bytearray -> bytes (isolated immutable copy)
    - int, float, str, bool, bytes, date, datetime -> exact primitive conversion to strip subclass mutability
    - None -> None
    - Any other type -> raises TypeError to prevent aliased mutable leakage.
    """
    if isinstance(obj, Mapping):
        frozen_dict = {}
        for k, v in obj.items():
            frozen_k = _deep_freeze(k)
            if type(frozen_k) not in (str, int, float, bool, bytes, date, datetime) and frozen_k is not None:
                raise TypeError(
                    f"Mapping key in trace must be an immutable scalar primitive, got {type(frozen_k).__name__}"
                )
            frozen_dict[frozen_k] = _deep_freeze(v)
        return types.MappingProxyType(frozen_dict)
    elif isinstance(obj, bytearray):
        return bytes(obj)
    elif isinstance(obj, (list, tuple)):
        return tuple(_deep_freeze(v) for v in obj)
    elif isinstance(obj, (set, frozenset)):
        return frozenset(_deep_freeze(v) for v in obj)
    elif isinstance(obj, bool):  # bool is a subclass of int, check bool before int
        return bool(obj)
    elif type(obj) is int:
        return obj
    elif isinstance(obj, int):
        val = int.__int__(obj)
        if type(val) is not int or val is obj:
            val = int(str(obj))
        return val
    elif type(obj) is float:
        return obj
    elif isinstance(obj, float):
        val = float.__float__(obj)
        if type(val) is not float or val is obj:
            val = float(str(obj))
        return val
    elif type(obj) is str:
        return obj
    elif isinstance(obj, str):
        # Subclass of str: extract pure built-in str copy preserving exact unicode code points
        return str.encode(obj, "utf-8").decode("utf-8")
    elif type(obj) is bytes:
        return obj
    elif isinstance(obj, bytes):
        val = bytes.__bytes__(obj)
        if type(val) is not bytes or val is obj:
            val = bytes(list(obj))
        return val
    elif isinstance(obj, datetime):  # datetime is a subclass of date, check datetime before date
        tz = None
        if obj.tzinfo is not None:
            offset = obj.utcoffset()
            if offset is not None:
                raw_tz_name = obj.tzname()
                if raw_tz_name is not None:
                    # Normalize timezone name to exact built-in str to detach mutable subclasses
                    if type(raw_tz_name) is str:
                        clean_name = raw_tz_name
                    else:
                        clean_name = str.encode(raw_tz_name, "utf-8").decode("utf-8")
                else:
                    clean_name = None
                tz = timezone(offset, name=clean_name) if clean_name else timezone(offset)
        return datetime(
            obj.year, obj.month, obj.day,
            obj.hour, obj.minute, obj.second, obj.microsecond,
            tzinfo=tz,
            fold=getattr(obj, "fold", 0)
        )
    elif isinstance(obj, date):
        return date(obj.year, obj.month, obj.day)
    elif obj is None:
        return None
    else:
        raise TypeError(
            f"Unsupported mutable or unverified leaf type in trace: {type(obj).__name__}. "
            "Traces must consist strictly of immutable scalar primitives, dates, bytes, and frozen containers."
        )


def _deep_unfreeze(obj: Any) -> Any:
    """Recursively converts mapping proxies, tuples, and frozensets back into standard python types."""
    if isinstance(obj, (Mapping, types.MappingProxyType)):
        return {k: _deep_unfreeze(v) for k, v in obj.items()}
    elif isinstance(obj, tuple):
        return [_deep_unfreeze(v) for v in obj]
    elif isinstance(obj, frozenset):
        return list(_deep_unfreeze(v) for v in obj)
    return obj


@dataclass(frozen=True)
class SignalEvent:
    """
    Standard immutable signal event emitted by Track 2 quantitative alpha strategies.
    Strictly validated upon creation.
    """
    strategy_id: str
    symbol: str
    session_date: str          # Date of signal generation (T)
    entry_session: str         # Intended execution session (T+1, must be > session_date)
    signal_type: str = "BUY"
    order_type: str = "BUY_STOP"
    reference_price: float = 0.0
    stop_loss_price: float = 0.0
    target_price: float = 0.0
    priority_score: float = 0.0
    trace: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_id, str) or not self.strategy_id.strip():
            raise ValueError("strategy_id must be a non-empty string")
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must be a non-empty string")

        # Strict Date Validation
        for date_name, date_val in [("session_date", self.session_date), ("entry_session", self.entry_session)]:
            if not isinstance(date_val, str) or not _ISO_DATE_REGEX.match(date_val):
                raise ValueError(f"{date_name} must be a valid ISO date string (YYYY-MM-DD), got {date_val!r}")
            try:
                datetime.strptime(date_val, "%Y-%m-%d")
            except ValueError as e:
                raise ValueError(f"{date_name} is not a valid calendar date: {e}") from e

        # Rule 4 & Causality Invariant: Entry session cannot precede signal session
        if self.entry_session < self.session_date:
            raise ValueError(
                f"entry_session ({self.entry_session}) cannot precede session_date ({self.session_date})"
            )

        # Finite Numeric Validation
        for name, val in [
            ("reference_price", self.reference_price),
            ("stop_loss_price", self.stop_loss_price),
            ("target_price", self.target_price),
            ("priority_score", self.priority_score)
        ]:
            if not isinstance(val, (int, float)) or isinstance(val, bool) or not math.isfinite(val):
                raise ValueError(f"{name} must be a finite float, got {val!r}")

        # Rule 2: Absolute Rs 10.00 Price Floor
        if self.reference_price < 10.00:
            raise ValueError(f"reference_price {self.reference_price} violates Rule 2 floor (Rs 10.00)")

        # Structural price ordering
        if self.stop_loss_price <= 0.0:
            raise ValueError(f"stop_loss_price must be positive, got {self.stop_loss_price}")
        if self.stop_loss_price >= self.reference_price:
            raise ValueError(
                f"stop_loss_price ({self.stop_loss_price}) must be strictly less than "
                f"reference_price ({self.reference_price})"
            )
        if self.target_price <= self.reference_price:
            raise ValueError(
                f"target_price ({self.target_price}) must be strictly greater than "
                f"reference_price ({self.reference_price})"
            )

        # Mandatory Immutable Trace Contract
        if not isinstance(self.trace, Mapping) or not self.trace:
            raise ValueError("trace must be a non-empty mapping containing calculation proof")

        # Recursively freeze mapping so nested dicts/lists are immutable
        object.__setattr__(self, "trace", _deep_freeze(self.trace))

        if not self.created_at:
            object.__setattr__(self, "created_at", datetime.now(IST).isoformat(timespec="seconds"))

    @property
    def risk_per_share(self) -> float:
        return round(self.reference_price - self.stop_loss_price, 4)

    @property
    def target_gain_per_share(self) -> float:
        return round(self.target_price - self.reference_price, 4)

    @property
    def reward_risk_ratio(self) -> float:
        risk = self.risk_per_share
        if risk <= 0:
            return 0.0
        return round(self.target_gain_per_share / risk, 4)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "session_date": self.session_date,
            "entry_session": self.entry_session,
            "signal_type": self.signal_type,
            "order_type": self.order_type,
            "reference_price": self.reference_price,
            "stop_loss_price": self.stop_loss_price,
            "target_price": self.target_price,
            "risk_per_share": self.risk_per_share,
            "priority_score": self.priority_score,
            "reward_risk_ratio": self.reward_risk_ratio,
            "trace": _deep_unfreeze(self.trace),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ExitSignalEvent:
    """
    Standard immutable exit signal event emitted when a swing position triggers an exit condition.
    """
    strategy_id: str
    symbol: str
    session_date: str
    position_id: str
    reason: str                # TARGET_HIT, STOP_LOSS, TIME_STOP, TRAILING_STOP, SURVEILLANCE_PREEMPTION
    exit_price: float
    shares_to_exit: int
    trace: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_id, str) or not self.strategy_id.strip():
            raise ValueError("strategy_id must be a non-empty string")
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        if not isinstance(self.session_date, str) or not _ISO_DATE_REGEX.match(self.session_date):
            raise ValueError(f"session_date must be a valid ISO date string, got {self.session_date!r}")
        try:
            datetime.strptime(self.session_date, "%Y-%m-%d")
        except ValueError as e:
            raise ValueError(f"session_date is not a valid calendar date: {e}") from e

        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be a non-empty string")
        if not isinstance(self.exit_price, (int, float)) or isinstance(self.exit_price, bool) or not math.isfinite(self.exit_price) or self.exit_price <= 0:
            raise ValueError(f"exit_price must be a positive finite float, got {self.exit_price!r}")
        if not isinstance(self.shares_to_exit, int) or isinstance(self.shares_to_exit, bool) or self.shares_to_exit <= 0:
            raise ValueError(f"shares_to_exit must be a positive integer, got {self.shares_to_exit!r}")

        # Mandatory Non-Empty Trace Mapping
        if not isinstance(self.trace, Mapping) or not self.trace:
            raise ValueError("trace must be a non-empty mapping containing calculation proof")

        # Recursively freeze mapping so nested dicts/lists are immutable
        object.__setattr__(self, "trace", _deep_freeze(self.trace))

        if not self.created_at:
            object.__setattr__(self, "created_at", datetime.now(IST).isoformat(timespec="seconds"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "session_date": self.session_date,
            "position_id": self.position_id,
            "reason": self.reason,
            "exit_price": self.exit_price,
            "shares_to_exit": self.shares_to_exit,
            "trace": _deep_unfreeze(self.trace),
            "created_at": self.created_at,
        }


class BaseSwingStrategy(ABC):
    """
    Abstract Base Class for all Project ARGUS Track 2 Quantitative Alpha Strategies.
    """

    def __init__(
        self,
        spec_path: Optional[Union[str, Path]] = None,
        config: Optional[Dict[str, Any]] = None,
        allow_unreviewed_overrides: bool = False
    ) -> None:
        self.allow_unreviewed_overrides = allow_unreviewed_overrides
        self.config: Dict[str, Any] = {}
        if spec_path is not None:
            self.config = self.load_spec(spec_path)
        elif config is not None:
            if not allow_unreviewed_overrides:
                raise ValueError(
                    "Config-only instantiation without pre-registered spec requires explicit "
                    "allow_unreviewed_overrides=True (Rule 8 v2)"
                )
        if config is not None:
            if spec_path is not None and not allow_unreviewed_overrides:
                raise ValueError(
                    "Unreviewed configuration overrides over pre-registered spec are strictly forbidden (Rule 8 v2)"
                )
            self.config.update(config)
        self._validate_config()

    @staticmethod
    def validate_historical_bars(bars: Sequence[Mapping[str, Any]], session_date: str) -> bool:
        """
        Strict historical bar validation for quantitative swing strategies (Rule 4 Invariant).
        Requirements:
        1. Every bar must have a non-empty, valid ISO calendar date string ('session_date' or 'day').
        2. Bar dates must be strictly increasing and unique (no duplicates, no backwards jumps).
        3. No bar date can be after session_date (no lookahead / future bars).
        4. The final bar's date MUST strictly equal session_date (point-in-time fresh data).
        Returns True if and only if all conditions pass.
        """
        if not bars:
            return False

        prev_dt = None
        for b in bars:
            if not isinstance(b, Mapping):
                return False
            raw_date = b.get("session_date") or b.get("day")
            if not isinstance(raw_date, str) or not _ISO_DATE_REGEX.match(raw_date):
                return False
            try:
                cur_dt = datetime.strptime(raw_date, "%Y-%m-%d")
            except ValueError:
                return False

            # No future bar relative to session_date
            if raw_date > session_date:
                return False

            # Strictly increasing unique dates (reject duplicates and reversals)
            if prev_dt is not None and cur_dt <= prev_dt:
                return False
            prev_dt = cur_dt

        # Final bar date must strictly equal session_date
        last_date = bars[-1].get("session_date") or bars[-1].get("day")
        if last_date != session_date:
            return False

        return True

    @staticmethod
    def validate_timing_context(session_date: str, context: Optional[Mapping[str, Any]]) -> Optional[str]:
        """
        Validates timing context fail-closed for Rule 4 T+1 discrete execution.
        Returns next_session string if valid, or None if invalid.
        """
        if not isinstance(session_date, str) or not _ISO_DATE_REGEX.match(session_date):
            return None
        try:
            cur_dt = datetime.strptime(session_date, "%Y-%m-%d")
        except ValueError:
            return None

        if not context or not isinstance(context, Mapping):
            return None

        next_session = context.get("next_session")
        if not isinstance(next_session, str) or not _ISO_DATE_REGEX.match(next_session):
            return None
        try:
            next_dt = datetime.strptime(next_session, "%Y-%m-%d")
        except ValueError:
            return None

        # Must be strictly in the future (T+1 entry)
        if next_dt <= cur_dt:
            return None

        # Calendar gap cap: max 10 days (reject far-future like 2099-01-01)
        if (next_dt - cur_dt).days > 10:
            return None

        return next_session

    @property
    @abstractmethod
    def strategy_id(self) -> str:
        """Unique identifier of the strategy (e.g. DELIVERY_ACCUMULATION)."""
        pass

    @abstractmethod
    def generate_signals(
        self,
        session_date: str,
        market_data: Mapping[str, Any],
        context: Optional[Dict[str, Any]] = None
    ) -> List[SignalEvent]:
        """
        Generates candidate entry signals for session_date.
        Strictly point-in-time: uses data up to session_date without lookahead.
        """
        pass

    @abstractmethod
    def evaluate_exits(
        self,
        open_positions: Sequence[Mapping[str, Any]],
        current_bars: Mapping[str, Any],
        session_date: str
    ) -> List[ExitSignalEvent]:
        """
        Evaluates active swing positions against target, stop, trailing stop, and time stop rules.
        """
        pass

    def load_spec(self, spec_path: Union[str, Path], verify_manifest: bool = True) -> Dict[str, Any]:
        """
        Loads and parses a YAML strategy specification, strictly verifying its SHA-256 against SPEC_MANIFEST.sha256.
        Cryptographic manifest verification is mandatory and cannot be bypassed.
        """
        p = Path(spec_path).resolve()
        if not p.is_file():
            raise FileNotFoundError(f"Strategy specification file not found: {p}")

        manifest_p = p.parent / "SPEC_MANIFEST.sha256"
        if not manifest_p.is_file():
            raise FileNotFoundError(
                f"Cryptographic manifest SPEC_MANIFEST.sha256 not found in {p.parent}. "
                "Fail-closed invariant requires locked manifest verification for all specifications (Rule 8 v2)."
            )
        manifest_text = manifest_p.read_text(encoding="utf-8")
        raw_bytes = p.read_bytes().replace(b"\r\n", b"\n")
        calc_sha = hashlib.sha256(raw_bytes).hexdigest()
        matched = False
        for line in manifest_text.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[1] == p.name:
                expected_sha = parts[0].lower()
                if calc_sha.lower() != expected_sha:
                    raise ValueError(
                        f"Specification {p.name} hash {calc_sha} does not match locked manifest {expected_sha}"
                    )
                matched = True
                break
        if not matched:
            raise ValueError(f"Specification {p.name} not found in manifest {manifest_p.name}")

        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ValueError(f"Specification must be a mapping, got {type(data)}")
        return data

    def _validate_config(self) -> None:
        """Validates base parameters, schema, track, and strategy identity binding."""
        if not self.config:
            raise ValueError("Specification configuration cannot be empty")

        # 1. Strategy Identity Validation (Codex Round 4 Finding 2)
        strat_name = self.config.get("strategy_name")
        strat_id = self.config.get("strategy_id")
        if not strat_name and not strat_id:
            raise ValueError(
                f"Specification must declare 'strategy_name' or 'strategy_id' matching '{self.strategy_id}'"
            )
        if strat_name is not None and strat_name != self.strategy_id:
            raise ValueError(
                f"Specification declared strategy_name '{strat_name}' does not match "
                f"strategy '{self.strategy_id}'"
            )
        if strat_id is not None and strat_id != self.strategy_id:
            raise ValueError(
                f"Specification declared strategy_id '{strat_id}' does not match "
                f"strategy '{self.strategy_id}'"
            )

        # 2. Track Isolation (Rule 11) - Fail-Closed, explicit approved track required
        declared_track = self.config.get("track")
        if declared_track not in ("TRACK_2", "TRACK_2_LIQUID"):
            raise ValueError(
                f"Strategy specification belongs to unapproved track '{declared_track}', "
                "expected TRACK_2 / TRACK_2_LIQUID (Rule 11 invariant)"
            )

        # 3. Required sections schema validation
        for section in ["eligibility", "setup_rules", "entry_rules", "risk_and_exits"]:
            if section in self.config and not isinstance(self.config[section], Mapping):
                raise ValueError(f"Specification section '{section}' must be a mapping")

    # =========================================================================
    # Pure Mathematical Indicator Utilities (Robust & Fail-Closed)
    # =========================================================================

    @staticmethod
    def calculate_true_range(high: float, low: float, prev_close: float) -> float:
        """Calculates True Range for a single bar."""
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in (high, low, prev_close)):
            raise ValueError("All inputs to calculate_true_range must be finite numbers")
        return max(high - low, abs(high - prev_close), abs(low - prev_close))

    @staticmethod
    def calculate_atr(
        highs: Sequence[float],
        lows: Sequence[float],
        closes: Sequence[float],
        period: int = 14
    ) -> Optional[float]:
        """
        Calculates Average True Range over `period` bars using simple rolling average of True Range.
        Returns None if insufficient bars or non-finite data (fail-closed).
        """
        n = len(closes)
        if n <= period or len(highs) != n or len(lows) != n or period < 1:
            return None

        tr_values: List[float] = []
        for i in range(1, n):
            h, l, pc = highs[i], lows[i], closes[i - 1]
            if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in (h, l, pc)):
                return None
            tr_values.append(max(h - l, abs(h - pc), abs(l - pc)))

        if len(tr_values) < period:
            return None

        recent_trs = tr_values[-period:]
        atr = sum(recent_trs) / period
        return round(atr, 4) if atr > 0 else None

    @staticmethod
    def calculate_sma(values: Sequence[float], period: int) -> Optional[float]:
        """Calculates Simple Moving Average over `period`. Returns None if insufficient data."""
        if len(values) < period or period < 1:
            return None
        recent = values[-period:]
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in recent):
            return None
        return round(sum(recent) / period, 4)

    @staticmethod
    def calculate_ema(prices: Sequence[float], period: int) -> Optional[float]:
        """
        Calculates Exponential Moving Average over `period`.
        Initializes with SMA of first `period` bars. Returns None if insufficient data.
        """
        if len(prices) < period or period < 1:
            return None
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in prices):
            return None

        alpha = 2.0 / (period + 1.0)
        ema = sum(prices[:period]) / period
        for price in prices[period:]:
            ema = alpha * price + (1.0 - alpha) * ema
        return round(ema, 4)

    @staticmethod
    def calculate_rsi(closes: Sequence[float], period: int = 14) -> Optional[float]:
        """
        Calculates Relative Strength Index (RSI) over `period` bars.
        Returns None if insufficient data or non-finite values.
        """
        if len(closes) <= period or period < 1:
            return None
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in closes):
            return None

        gains: List[float] = []
        losses: List[float] = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            if diff > 0:
                gains.append(diff)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(abs(diff))

        if len(gains) < period:
            return None

        recent_gains = gains[-period:]
        recent_losses = losses[-period:]
        avg_gain = sum(recent_gains) / period
        avg_loss = sum(recent_losses) / period

        if avg_loss == 0.0:
            return 100.0
        if avg_gain == 0.0:
            return 0.0

        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return round(rsi, 2)
