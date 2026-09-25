"""
research/data/indices.py
========================
Explicit index registry (plan D5). A series is an index only if it is listed here or carries the
"IDX:" prefix. The old substring test ("NIFTY" in name) treated INDIA VIX as a stock and would treat an
ETF such as NIFTYBEES as an index.

Names follow the NSE index list in Dhan's scrip master (plan section 2.2). Security IDs are deliberately
not stored here: read them from shared/track2_liquid/dhan_scrip_master.csv at run time.
"""
from __future__ import annotations

from typing import Dict, Optional

# canonical name -> aliases seen in data files and the scrip master
_CANONICAL: Dict[str, tuple] = {
    "IDX:NIFTY50": ("NIFTY50", "NIFTY 50", "NIFTY"),
    "IDX:INDIAVIX": ("INDIA VIX", "INDIAVIX"),
    "IDX:BANKNIFTY": ("BANKNIFTY", "NIFTY BANK"),
    "IDX:FINNIFTY": ("FINNIFTY", "NIFTY FIN SERVICE"),
    "IDX:MIDCPNIFTY": ("MIDCPNIFTY", "NIFTY MID SELECT"),
    "IDX:NIFTYAUTO": ("NIFTY AUTO",),
    "IDX:NIFTYPVTBANK": ("NIFTY PVT BANK",),
    "IDX:NIFTYFMCG": ("NIFTY FMCG",),
    "IDX:NIFTYIT": ("NIFTYIT", "NIFTY IT"),
    "IDX:NIFTYMEDIA": ("NIFTY MEDIA",),
    "IDX:NIFTYMETAL": ("NIFTY METAL",),
    "IDX:NIFTYPHARMA": ("NIFTY PHARMA",),
    "IDX:NIFTYPSUBANK": ("NIFTY PSU BANK",),
    "IDX:NIFTYREALTY": ("NIFTY REALTY",),
    "IDX:NIFTYPSE": ("NIFTYPSE", "NIFTY PSE"),
    "IDX:NIFTYENERGY": ("NIFTY ENERGY",),
    "IDX:NIFTYINFRA": ("NIFTYINFRA", "NIFTY INFRA"),
    "IDX:NIFTYCPSE": ("NIFTYCPSE", "NIFTY CPSE"),
    "IDX:NIFTYHEALTHCARE": ("NIFTY HEALTHCARE",),
    "IDX:NIFTYCONSRDURBL": ("NIFTY CONSR DURBL",),
    "IDX:NIFTYOILGAS": ("NIFTY OIL AND GAS",),
    "IDX:NIFTYINDDEFENCE": ("NIFTY IND DEFENCE",),
}
_ALIAS: Dict[str, str] = {}
for _canon, _aliases in _CANONICAL.items():
    _ALIAS[_canon] = _canon
    for _a in _aliases:
        _ALIAS[_a.upper()] = _canon


def canonical_index(name: str) -> Optional[str]:
    """Canonical IDX: name for a known index (or any IDX:-prefixed name); None for tradable symbols."""
    key = name.strip().upper()
    if key in _ALIAS:
        return _ALIAS[key]
    if key.startswith("IDX:"):
        return key
    return None


def is_index(name: str) -> bool:
    return canonical_index(name) is not None


def market_index_names(name: str = "IDX:NIFTY50") -> tuple:
    """All spellings that denote the same index, canonical first."""
    canon = canonical_index(name) or name
    return (canon,) + _CANONICAL.get(canon, ())
