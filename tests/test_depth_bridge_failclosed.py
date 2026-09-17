"""
tests/test_depth_bridge_failclosed.py
=====================================
Regression tests for the Kite CDP depth bridge fail-closed gate and CDP
request-id arithmetic.

Both defects pinned here were found by live reviewers (Claude and Codex)
against commit 0ca71f2, AFTER that commit had already passed AST parse,
node --check, a DOM-stub test and the full unit suite. Neither is reachable
by those checks, so they are pinned by source inspection and by simulating
the arithmetic.
"""

import inspect
import os
import re
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

BRIDGE_PATH = os.path.join(
    PROJECT_ROOT, "antigravity", "daemons", "kite_web_depth_bridge.py"
)


@pytest.fixture(scope="module")
def source():
    with open(BRIDGE_PATH, encoding="utf-8") as f:
        return f.read()


# --------------------------------------------------------------------------
# CDP request-id arithmetic
# --------------------------------------------------------------------------

def test_enforce_tab_focus_consumes_two_ids():
    """The helper's contract: it uses req_id AND req_id + 1."""
    from antigravity.daemons import kite_web_depth_bridge as bridge

    src = inspect.getsource(bridge.enforce_tab_focus)
    assert "req_id=req_id" in src
    assert "req_id + 1" in src, "helper must consume req_id + 1"


def test_caller_advances_msg_id_past_both_focus_ids(source):
    """Regression: the caller advanced by 1 while the helper used 2 ids.

    That made the next Runtime.evaluate reuse the focus-emulation id on every
    cycle, so a late focus reply was matched as the extraction response, val
    came back None, and the tick was silently dropped.
    """
    calls = re.findall(r"await enforce_tab_focus\(ws, msg_id\)", source)
    assert len(calls) == 2, "expected focus enforcement on connect and in-loop"

    for m in re.finditer(r"await enforce_tab_focus\(ws, msg_id\)\n(.*)\n", source):
        assert "msg_id += 1" in m.group(1), (
            "each enforce_tab_focus call must be followed by msg_id += 1 to "
            "account for the second id the helper consumes"
        )


def test_focus_and_extract_ids_never_collide():
    """Simulate the loop's id arithmetic; no id may be issued twice."""
    issued = []
    msg_id = 1000

    # connect
    msg_id += 1
    issued += [msg_id, msg_id + 1]   # enforce_tab_focus
    msg_id += 1

    for cycle in range(5):
        if cycle % 2 == 0:           # periodic focus enforcement
            msg_id += 1
            issued += [msg_id, msg_id + 1]
            msg_id += 1
        msg_id += 1                  # Runtime.evaluate
        issued.append(msg_id)

    assert len(issued) == len(set(issued)), f"duplicate CDP ids issued: {issued}"


# --------------------------------------------------------------------------
# Fail-closed gate
# --------------------------------------------------------------------------

def test_gate_runs_before_volume_audit(source):
    """Rule 7 volume expansion must not be computed from a stale DOM read."""
    gate = source.index("STALE_STATUSES = (")
    audit = source.index("vol_audit[sym] = compute_rule7_volume_expansion")
    assert gate < audit, "fail-closed gate must precede the Rule 7 volume audit"
    assert "if data_valid:" in source[gate:audit], "audit must be guarded by data_valid"


@pytest.mark.parametrize("local_var", [
    "depth = None",
    "stats = {}",
    "wl = []",
    "ltp_to_log = None",
    "vol_to_log = None",
    "best_bid = None",
    "best_ask = None",
    "total_b = None",
    "total_s = None",
])
def test_invalid_branch_nulls_every_local_feeding_the_tick_csv(source, local_var):
    """Nulling only val[...] left the CSV publishing stale quotes.

    best_bid/best_ask/spread and total_buy/total_sell are recomputed from the
    LOCAL depth and stats when append_tick_log runs, so live_depth.json said
    data_valid=false while live_depth_ticks.csv beside it recorded
    real-looking prices.
    """
    start = source.index("if not data_valid:")
    end = source.index('val["data_valid"] = data_valid')
    assert local_var in source[start:end], f"invalid branch must set {local_var}"


def test_invalid_branch_clears_watchlist(source):
    """multi_stock_radar.py reads watchlist LTPs and labels them KITE_LIVE
    without checking data_valid or is_stale, so a populated watchlist was the
    widest stale-price path out of the gate."""
    start = source.index("if not data_valid:")
    end = source.index('val["data_valid"] = data_valid')
    assert 'val["watchlist"] = []' in source[start:end]


def test_dialog_fallback_requires_a_depth_container(source):
    """Wrong attribution is worse than UNATTRIBUTED: an order/GTT/alert dialog
    must not be able to name the depth currently being captured."""
    assert "const hasDepth = dlg.querySelector(" in source
    assert "if (!hasDepth) continue;" in source
