import json
from pathlib import Path

import pytest

from antigravity.daemons.track2_policy_ingestor import CIRCULARS, build_policy_package
from antigravity.daemons.track2_session_coordinator import validate_band_policy


PDF = b"%PDF-1.7\n" + (b"official circular fixture\n" * 60)


def _fetch(url):
    return 200, PDF + url.encode(), "application/pdf", url


def test_builds_policy_accepted_by_coordinator(tmp_path):
    path = build_policy_package(tmp_path, fetcher=_fetch)
    policy, digest = validate_band_policy(path)
    assert {item["id"] for item in policy["circulars"]} == set(CIRCULARS)
    assert len(digest) == 64


@pytest.mark.parametrize("status, body, content_type", [
    (500, PDF, "application/pdf"),
    (200, b"<html>blocked</html>" * 100, "text/html"),
    (200, b"%PDF-short", "application/pdf"),
])
def test_bad_responses_fail_closed(tmp_path, status, body, content_type):
    def bad(url):
        return status, body, content_type, url
    with pytest.raises(ValueError):
        build_policy_package(tmp_path, fetcher=bad)


def test_existing_changed_pdf_is_not_replaced(tmp_path):
    build_policy_package(tmp_path, fetcher=_fetch)
    first = next(iter(CIRCULARS.values()))
    (tmp_path / Path(first).name).write_bytes(b"changed")
    with pytest.raises(FileExistsError):
        build_policy_package(tmp_path, fetcher=_fetch)


def test_module_contains_no_broker_order_capability():
    import antigravity.daemons.track2_policy_ingestor as module
    source = Path(module.__file__).read_text(encoding="utf-8").lower()
    assert not any(token in source for token in ("place_order", "enctoken", "kiteconnect", "api.kite.trade"))
