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
    # NIFTY MIDCAP 50 is a different index from MIDCPNIFTY (Midcap Select). Yahoo ^NSEMDCP50 is this one.
    "IDX:NIFTYMIDCAP50": ("NIFTY MIDCAP 50", "NIFTYMIDCAP50"),
    "IDX:NIFTYAUTO": ("NIFTY AUTO", "NIFTYAUTO", "CNXAUTO"),
    "IDX:NIFTYPVTBANK": ("NIFTY PVT BANK",),
    "IDX:NIFTYFMCG": ("NIFTY FMCG", "NIFTYFMCG", "CNXFMCG"),
    "IDX:NIFTYIT": ("NIFTYIT", "NIFTY IT", "CNXIT"),
    "IDX:NIFTYMEDIA": ("NIFTY MEDIA", "NIFTYMEDIA", "CNXMEDIA"),
    "IDX:NIFTYMETAL": ("NIFTY METAL", "NIFTYMETAL", "CNXMETAL"),
    "IDX:NIFTYPHARMA": ("NIFTY PHARMA", "NIFTYPHARMA", "CNXPHARMA"),
    "IDX:NIFTYPSUBANK": ("NIFTY PSU BANK", "NIFTYPSUBANK", "CNXPSUBANK"),
    "IDX:NIFTYREALTY": ("NIFTY REALTY", "NIFTYREALTY", "CNXREALTY"),
    "IDX:NIFTYPSE": ("NIFTYPSE", "NIFTY PSE"),
    "IDX:NIFTYENERGY": ("NIFTY ENERGY", "NIFTYENERGY", "CNXENERGY"),
    "IDX:NIFTYINFRA": ("NIFTYINFRA", "NIFTY INFRA", "CNXINFRA"),
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


# Yahoo Finance chart tickers (Antigravity's free fetcher, commit a8264e9) -> canonical index.
# Keyed by the source ticker, not by the name the fetcher wrote: that fetcher stored ^NSEMDCP50
# (NIFTY MIDCAP 50) under the name MIDCPNIFTY (Midcap Select), which is a different index.
YAHOO_TICKER_TO_CANONICAL: Dict[str, str] = {
    "^NSEI": "IDX:NIFTY50", "^INDIAVIX": "IDX:INDIAVIX", "^NSEBANK": "IDX:BANKNIFTY",
    "NIFTY_FIN_SERVICE.NS": "IDX:FINNIFTY", "^NSEMDCP50": "IDX:NIFTYMIDCAP50",
    "^CNXAUTO": "IDX:NIFTYAUTO", "^CNXENERGY": "IDX:NIFTYENERGY", "^CNXFMCG": "IDX:NIFTYFMCG",
    "^CNXIT": "IDX:NIFTYIT", "^CNXMEDIA": "IDX:NIFTYMEDIA", "^CNXMETAL": "IDX:NIFTYMETAL",
    "^CNXPHARMA": "IDX:NIFTYPHARMA", "^CNXPSUBANK": "IDX:NIFTYPSUBANK", "^CNXREALTY": "IDX:NIFTYREALTY",
    "^CNXINFRA": "IDX:NIFTYINFRA",
}


def market_index_names(name: str = "IDX:NIFTY50") -> tuple:
    """All spellings that denote the same index, canonical first."""
    canon = canonical_index(name) or name
    return (canon,) + _CANONICAL.get(canon, ())
