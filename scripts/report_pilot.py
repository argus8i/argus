import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

base_dir = Path("shared/track2_liquid/history/raw/nse_archive")
manifest_file = base_dir / "manifest.jsonl"
records = [json.loads(line) for line in open(manifest_file, encoding="utf-8") if line.strip()]

print(f"Total manifest lines: {len(records)}")
counts = Counter(r["outcome"] for r in records)
print(f"Outcome counts: {dict(counts)}")
total_bytes = sum(r.get("bytes", 0) for r in records if r.get("outcome") == "SAVED")
print(f"Total SAVED bytes: {total_bytes:,} bytes ({total_bytes} bytes)\n")

# Jan 2010 Side-by-side Table
jan_lines = [r for r in records if r["trade_date"].startswith("2010-01")]
table = defaultdict(dict)
for r in jan_lines:
    d = r["trade_date"]
    table[d][r["dataset"]] = r

cov_cm = {r["date"]: r for r in csv.DictReader(open(base_dir / "coverage_cm_bhavcopy_2010.csv", encoding="utf-8"))}

print("### January 2010 Coverage Table (Side-by-Side)")
print("| Date | Weekday | CM Bhavcopy (JOB1) | F&O Bhavcopy (JOB2) | MTO Delivery (JOB3) | Status Notes |")
print("|:---|:---|:---|:---|:---|:---|")
for d, row in sorted(cov_cm.items()):
    w = row["weekday"]
    cm = table.get(d, {}).get("cm_bhavcopy", {})
    fo = table.get(d, {}).get("fo_bhavcopy", {})
    mto = table.get(d, {}).get("mto", {})

    cm_s = f"SAVED ({cm.get('bytes', 0):,} B)" if cm.get("outcome") == "SAVED" else cm.get("outcome", "N/A")
    fo_s = f"SAVED ({fo.get('bytes', 0):,} B)" if fo.get("outcome") == "SAVED" else fo.get("outcome", "N/A")
    mto_s = f"SAVED ({mto.get('bytes', 0):,} B)" if mto.get("outcome") == "SAVED" else mto.get("outcome", "N/A")

    note = "Trading Session"
    if cm.get("outcome") == "MISSING_404" and fo.get("outcome") == "MISSING_404" and mto.get("outcome") == "MISSING_404":
        if d == "2010-01-01":
            note = "Exchange Holiday (New Year's Day)"
        elif d == "2010-01-26":
            note = "Exchange Holiday (Republic Day)"
        else:
            note = "Consistent Exchange Holiday across all 3 datasets"

    print(f"| {d} | {w} | {cm_s} | {fo_s} | {mto_s} | {note} |")

print("\n### 9 Spot Checks Summary")
spot_dates = ["2005-06-01", "2016-01-04", "2021-01-04"]
for sd in spot_dates:
    for ds in ("cm_bhavcopy", "fo_bhavcopy", "mto"):
        r = [rec for rec in records if rec["trade_date"] == sd and rec["dataset"] == ds][0]
        print(f"- **{sd} | {ds} ({r['job']})**: HTTP {r['http_status']} {r['outcome']} | Size: {r['bytes']:,} bytes | SHA-256: `{r['sha256']}` | Path: `{r['saved_path']}`")
