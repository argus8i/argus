"""
research/data/sector_map.py
===========================
Symbol -> factor index for RESID_REV's residual (plan P3.6).

No point-in-time constituent lists are available, and current constituent lists would be look-ahead.
The plan prefers a static industry classification when one exists, so this module maps Antigravity's
static classification (shared/track2_liquid/sector_mapping.json, `symbol_to_sector`, 28 industries) to
the index series we actually have bars for, through the fixed table SECTOR_TO_INDEX below.

The table is an ASSUMPTION, stated here and in every output row (source column):
- an industry maps to a sectoral index only where the index is defined on that industry;
- everything else falls back to NIFTY 50 (FALLBACK_NIFTY50).
Known limits (reported, not fixed):
- Index weights are not available, so the plan's "use NIFTY 50 if the stock is >10% of its index" rule
  cannot be applied (weight_if_known is empty). Heavyweights (for example the largest private banks in
  BANKNIFTY) are partly regressed on themselves, which shrinks their residuals: fewer signals, not false ones.
- The classification's own provenance is Antigravity's file; it was not independently checked.
- At run time, a session with no bars for the mapped index uses NIFTY 50 (features.calibration records it).

CLI:
    python -m research.data.sector_map [--mapping shared/track2_liquid/sector_mapping.json]
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

from research.data import paths

NIFTY = "IDX:NIFTY50"

SECTOR_TO_INDEX: Dict[str, str] = {
    "BANKING_PRIVATE": "IDX:BANKNIFTY",
    "BANKING_PSU": "IDX:NIFTYPSUBANK",
    "NBFC_LENDING": "IDX:FINNIFTY",
    "CAPITAL_MARKETS_FINTECH": "IDX:FINNIFTY",
    "INSURANCE": "IDX:FINNIFTY",
    "PSU_POWER_FINANCE": "IDX:FINNIFTY",
    "PSU_RENEWABLE_FINANCE": "IDX:FINNIFTY",
    "IT_SOFTWARE": "IDX:NIFTYIT",
    "AUTOMOBILES": "IDX:NIFTYAUTO",
    "AUTO_ANCILLARIES": "IDX:NIFTYAUTO",
    "METALS_MINING": "IDX:NIFTYMETAL",
    "PHARMA_HEALTHCARE": "IDX:NIFTYPHARMA",
    "FMCG_CONSUMER_STAPLES": "IDX:NIFTYFMCG",
    "OIL_GAS_PETROLEUM": "IDX:NIFTYENERGY",
    "POWER_ENERGY": "IDX:NIFTYENERGY",
    "GREEN_ENERGY_POWER": "IDX:NIFTYENERGY",
    "REAL_ESTATE": "IDX:NIFTYREALTY",
    "INFRASTRUCTURE_CONSTRUCTION": "IDX:NIFTYINFRA",
    "CEMENT_BUILDING_MATERIALS": "IDX:NIFTYINFRA",
    "TELECOMMUNICATIONS": "IDX:NIFTYINFRA",
    "PSU_RAILWAYS_INFRA": "IDX:NIFTYINFRA",
}


def build_rows(symbol_to_sector: Dict[str, str], as_of: str) -> List[Dict[str, str]]:
    rows = []
    for sym in sorted(symbol_to_sector):
        sector = symbol_to_sector[sym]
        idx = SECTOR_TO_INDEX.get(sector)
        rows.append({"symbol": sym, "factor_index": idx or NIFTY,
                     "source": f"STATIC_CLASSIFICATION:{sector}" if idx else f"FALLBACK_NIFTY50:{sector}",
                     "as_of": as_of, "weight_if_known": ""})
    return rows


def load_sector_map(path: Optional[Path | str] = None) -> Dict[str, str]:
    """symbol -> factor index, from sector_map.csv."""
    path = Path(path) if path else paths.reference_dir() / "sector_map.csv"
    with open(path, newline="", encoding="utf-8") as fh:
        return {r["symbol"]: r["factor_index"] for r in csv.DictReader(fh)}


def load_industries(mapping_json: Path | str) -> Dict[str, str]:
    """symbol -> industry label (used for the engine's max-2-per-sector cap)."""
    return dict(json.loads(Path(mapping_json).read_text(encoding="utf-8")).get("symbol_to_sector") or {})


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build sector_map.csv (plan P3.6)")
    ap.add_argument("--mapping", default=str(paths.shared_input("sector_mapping.json")))
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    raw = json.loads(Path(args.mapping).read_text(encoding="utf-8"))
    as_of = str((raw.get("_metadata") or {}).get("as_of_date") or date.today().isoformat())
    rows = build_rows(raw.get("symbol_to_sector") or {}, as_of)
    out = Path(args.out) if args.out else paths.ensure(paths.reference_dir()) / "sector_map.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["symbol", "factor_index", "source", "as_of", "weight_if_known"])
        w.writeheader()
        w.writerows(rows)
    from collections import Counter

    print(json.dumps({"rows": len(rows), "by_index": dict(Counter(r["factor_index"] for r in rows)),
                      "out": str(out)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
