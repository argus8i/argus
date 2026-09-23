"""Fetch and seal the official NSE circulars required by Track 2."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable


CIRCULARS = {
    "NSE/FAOP/62241": "https://nsearchives.nseindia.com/content/circulars/FAOP62241.pdf",
    "NSE/FAOP/63405": "https://nsearchives.nseindia.com/content/circulars/FAOP63405.pdf",
}
MAX_PDF_BYTES = 10 * 1024 * 1024
MIN_PDF_BYTES = 1_000


def _atomic_create(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.is_file() and not path.is_symlink() and path.read_bytes() == payload:
            return
        raise FileExistsError(f"existing policy artifact differs: {path.name}")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        os.link(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def _fetch(url: str) -> tuple[int, bytes, str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/pdf"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.status, response.read(MAX_PDF_BYTES + 1), response.headers.get("content-type", ""), response.geturl()


def build_policy_package(
    output_dir: Path,
    *,
    fetcher: Callable[[str], tuple[int, bytes, str, str]] = _fetch,
) -> Path:
    output_dir = output_dir.resolve()
    entries = []
    for identifier, url in CIRCULARS.items():
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "nsearchives.nseindia.com":
            raise ValueError("circular URL is not an approved official NSE archive URL")
        status, payload, content_type, final_url = fetcher(url)
        if status != 200 or final_url != url:
            raise ValueError("official circular fetch status or final URL is invalid")
        if "pdf" not in content_type.lower() or not payload.startswith(b"%PDF-"):
            raise ValueError("official circular response is not a PDF")
        if len(payload) < MIN_PDF_BYTES or len(payload) > MAX_PDF_BYTES:
            raise ValueError("official circular PDF size is invalid")
        filename = Path(parsed.path).name
        _atomic_create(output_dir / filename, payload)
        entries.append({
            "id": identifier,
            "url": url,
            "path": filename,
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
    policy = {
        "policy_id": "NSE_DYNAMIC_OPERATING_RANGE_V2",
        "effective_from": "2024-08-19",
        "market": "NSE_CASH",
        "applies_to": "ACTIVE_FNO_UNDERLYINGS",
        "circulars": entries,
    }
    policy_path = output_dir / "band_policy.json"
    _atomic_create(policy_path, json.dumps(policy, indent=2, sort_keys=True).encode("utf-8"))
    return policy_path


def main() -> int:
    path = build_policy_package(
        Path(__file__).resolve().parents[2] / "shared" / "track2_liquid" / "band_policy"
    )
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
