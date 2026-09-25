"""
research/derivatives/vix_regime.py
==================================
India VIX regime filter with hysteresis.

Tiers (mandate thresholds 11.5 / 16.5 / 22.0): LOW < 11.5 <= NORMAL <= 16.5 < ELEVATED <= 22.0 < CRISIS.
A tier change needs the VIX to clear the boundary by `hysteresis` points, so a reading hovering on a
boundary does not flip sizing back and forth. A missing or stale reading gives UNKNOWN with a zero
multiplier (fail-closed).

The sizing multipliers are ASSUMED placeholders. Nothing in this repository calibrates them, and the
mandate's supporting claims (for example "false breakout rate > 65% below VIX 11.5") are unsourced.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Dict, Optional, Tuple


class VixTier(IntEnum):
    UNKNOWN = -1
    LOW = 0
    NORMAL = 1
    ELEVATED = 2
    CRISIS = 3


DEFAULT_MULTIPLIERS: Dict[VixTier, float] = {VixTier.UNKNOWN: 0.0, VixTier.LOW: 0.5, VixTier.NORMAL: 1.0,
                                             VixTier.ELEVATED: 0.5, VixTier.CRISIS: 0.0}


@dataclass(frozen=True)
class VixState:
    tier: VixTier
    multiplier: float
    vix: Optional[float]
    as_of: Optional[datetime]
    changed: bool


class VixRegimeFilter:
    def __init__(self, thresholds: Tuple[float, float, float] = (11.5, 16.5, 22.0), hysteresis: float = 0.5,
                 max_staleness: timedelta = timedelta(minutes=30),
                 multipliers: Optional[Dict[VixTier, float]] = None):
        if not thresholds[0] < thresholds[1] < thresholds[2] or hysteresis < 0:
            raise ValueError("thresholds must increase and hysteresis must be non-negative")
        self.thresholds = thresholds
        self.hysteresis = hysteresis
        self.max_staleness = max_staleness
        self.multipliers = dict(multipliers or DEFAULT_MULTIPLIERS)
        self._tier = VixTier.UNKNOWN
        self._vix: Optional[float] = None
        self._as_of: Optional[datetime] = None

    def _raw(self, v: float) -> VixTier:
        a, b, c = self.thresholds
        if v < a:
            return VixTier.LOW
        if v <= b:
            return VixTier.NORMAL
        if v <= c:
            return VixTier.ELEVATED
        return VixTier.CRISIS

    def _state(self, changed: bool) -> VixState:
        return VixState(self._tier, self.multipliers[self._tier], self._vix, self._as_of, changed)

    def update(self, vix: Optional[float], as_of: datetime) -> VixState:
        before = self._tier
        if vix is None or isinstance(vix, bool) or not math.isfinite(vix) or vix <= 0:
            self._tier, self._vix, self._as_of = VixTier.UNKNOWN, None, as_of
            return self._state(before != self._tier)
        self._vix, self._as_of = float(vix), as_of
        raw = self._raw(vix)
        if self._tier == VixTier.UNKNOWN:
            self._tier = raw
        elif raw > self._tier:
            # climb to the highest tier whose lower boundary is cleared by the buffer
            target = self._tier
            for k in range(self._tier + 1, raw + 1):
                if vix > self.thresholds[k - 1] + self.hysteresis:
                    target = VixTier(k)
            self._tier = target
        elif raw < self._tier:
            target = self._tier
            for k in range(self._tier - 1, raw - 1, -1):
                if vix < self.thresholds[k] - self.hysteresis:
                    target = VixTier(k)
            self._tier = target
        return self._state(before != self._tier)

    def state_at(self, now: datetime) -> VixState:
        if self._as_of is None or now - self._as_of > self.max_staleness:
            return VixState(VixTier.UNKNOWN, self.multipliers[VixTier.UNKNOWN], self._vix, self._as_of, False)
        return self._state(False)
