"""
antigravity/strategies
======================
Quantitative alpha swing strategies for Project ARGUS Track 2 Liquid Desk.
All strategies strictly conform to:
- AGENTS.md (Rules 1, 2, 4, 8, 9, 11)
- Adjusted A1 Risk Budget (Rs 1,500 trade risk, Rs 38,000 slot cap)
- Fail-closed validation and immutable SignalEvent records.
"""

from .base_strategy import BaseSwingStrategy, SignalEvent, ExitSignalEvent
from .delivery_accumulation import DeliveryAccumulationStrategy
from .high52_momentum import High52MomentumStrategy
from .expiry_relief import ExpiryReliefStrategy

__all__ = [
    "BaseSwingStrategy",
    "SignalEvent",
    "ExitSignalEvent",
    "DeliveryAccumulationStrategy",
    "High52MomentumStrategy",
    "ExpiryReliefStrategy",
]
