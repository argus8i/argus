"""
EXPIRY_RELIEF_LONG v1 simulator, the event-study holdout runner's pass rule and lock refusals, and
promotion.register_candidate (26 Sep 2026).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research.backtest.bars import tick_size
from research.decision import promotion
from research.studies import event_holdout as eh
from research.studies import expiry_relief as er
from research.studies.run_holdout import HoldoutRefused
from research.studies.strategy_lab import Panel

T = tick_size(100.0)


class FakePanel:
    """One stock, sessions 0..n-1, OHLC arrays of shape (n, 1)."""

    def __init__(self, bars):
        a = np.array(bars, dtype=float)
        self.O, self.H, self.L, self.C = (a[:, k:k + 1] for k in range(4))
        self.j = {"AAA": 0}
        self.days = [f"2023-03-{d + 1:02d}" for d in range(len(bars))]


def _sig(entry=1, exit_=5):
    return pd.DataFrame({"symbol": ["AAA"], "expiry": ["2023-03-01"], "signal": [entry - 1], "entry": [entry],
                         "exit": [exit_], "r20": [-8.0]})


FLAT = [100, 100.5, 99.5, 100]


def test_gap_below_stop_fills_at_that_open():
    P = FakePanel([FLAT, FLAT, [95, 95.5, 94.5, 95]] + [FLAT] * 3)
    r = er.simulate_long(P, _sig()).iloc[0]
    assert r.exit_reason == "STOP_GAP"
    assert r.net_r < -1.0                          # 5% gap through a 3% stop costs more than 1R


def test_touch_on_the_entry_day_fills_at_the_stop():
    P = FakePanel([FLAT, [100, 100.5, 96.0, 99], FLAT, FLAT, FLAT, FLAT])
    r = er.simulate_long(P, _sig()).iloc[0]
    assert r.exit_reason == "STOP"
    assert r.stop_px == pytest.approx(round(math.floor((100 + T) * 0.97 / T + 1e-9) * T, 2))


def test_stop_counts_first_when_a_session_touches_both():
    P = FakePanel([FLAT, FLAT, [100, 106, 96.0, 100], FLAT, FLAT, FLAT])
    r = er.simulate_long(P, _sig()).iloc[0]
    assert r.exit_reason == "STOP" and not r.half_target


def test_half_at_target_then_time_exit():
    P = FakePanel([FLAT, FLAT, [101, 105.0, 100.5, 104], FLAT, FLAT, [103, 103.5, 102.5, 103]])
    r = er.simulate_long(P, _sig()).iloc[0]
    assert r.half_target and r.exit_reason == "TIME"
    assert r.net_r > 0


def test_gap_above_target_fills_at_the_open():
    P = FakePanel([FLAT, FLAT, [107, 108, 106.5, 107], FLAT, FLAT, FLAT])
    r = er.simulate_long(P, _sig()).iloc[0]
    assert r.half_target
    assert r.net_pnl_rs > 0


def test_entry_day_target_half_quantity_fees_and_slot_cap():
    P = FakePanel([FLAT, [100, 105.0, 99.9, 104], FLAT, FLAT, FLAT, [100, 100.5, 99.5, 100]])
    r = er.simulate_long(P, _sig()).iloc[0]
    assert r.half_target                                        # the entry-day high reached +1.5R
    entry = 100 + T
    assert r.qty == int(38000 // (round(100 * 1.001, 2) + T))     # the Rs 38,000 cap binds before Rs 1,500 risk
    assert r.notional_rs <= 38000
    assert r.risk_rs == pytest.approx(r.qty * (entry - r.stop_px))
    assert 0 < r.fee_r < 0.3                                    # CNC fees on the buy and both sell legs
    assert r.net_r == pytest.approx(r.gross_r - r.fee_r - r.slip_r, abs=1e-9)


def _draft(tmp: Path, name: str, status: str, pass_rule: str) -> Path:
    p = tmp / f"{name}.yaml"
    p.write_text(f"id: {name.upper()}\nstatus: {status}\npass_rule: {pass_rule}\n"
                 f"data:\n  holdout: [\"2024-10-01\", \"2026-07-31\"]\n", encoding="utf-8")
    return p


def test_panel_refuses_the_holdout_without_a_valid_lock(tmp_path):
    with pytest.raises(ValueError):
        Panel(Path("does-not-matter"), end="2025-01-31")
    spec = _draft(tmp_path, "expiry_relief_long_v1", "DRAFT", "x")
    with pytest.raises(ValueError):
        Panel(Path("does-not-matter"), end="2025-01-31", unlock_spec=spec)       # no lock file


def test_lock_refuses_a_draft_and_a_foreign_pass_rule(tmp_path, monkeypatch):
    rule = eh.STUDIES["ban_entry_short_v1"]["pass_rule"]
    p = _draft(tmp_path, "ban_entry_short_v1", "DRAFT", rule)
    monkeypatch.setattr(eh, "spec_path", lambda s: p)
    with pytest.raises(HoldoutRefused, match="not LOCKED"):
        eh.write_lock("ban_entry_short_v1")
    p = _draft(tmp_path, "ban_entry_short_v1", "LOCKED", "primary_test")
    with pytest.raises(HoldoutRefused, match="pass_rule"):
        eh.write_lock("ban_entry_short_v1")


def _res(t, m, s2, alpha=None):
    r = {"primary": {"summary": {"t_cluster": t, "mean_net_r": m}}, "slippage_2_ticks": {"summary": {"mean_net_r": s2}}}
    if alpha is not None:
        r["primary"]["drift_alpha"] = {"alpha_net": alpha}
    return r


def test_pass_rule_ban_entry():
    assert eh.decide("ban_entry_short_v1", _res(2.5, 0.1, 0.05), 2.0)[0] is True
    assert eh.decide("ban_entry_short_v1", _res(1.9, 0.1, 0.05), 2.0)[0] is False
    assert eh.decide("ban_entry_short_v1", _res(2.5, 0.1, -0.01), 2.0)[0] is False
    assert eh.decide("ban_entry_short_v1", _res(None, 0.1, 0.05), 2.0)[0] is False
    assert eh.decide("ban_entry_short_v1", _res(float("nan"), 0.1, 0.05), 2.0)[0] is False


def test_pass_rule_expiry_needs_the_beta_gate():
    assert eh.decide("expiry_relief_long_v1", _res(3.0, 0.4, 0.3, alpha=0.5), 2.0)[0] is True
    assert eh.decide("expiry_relief_long_v1", _res(3.0, 0.4, 0.3, alpha=-0.1), 2.0)[0] is False
    assert eh.decide("expiry_relief_long_v1", _res(3.0, 0.4, 0.3, alpha=None), 2.0)[0] is False


def test_unknown_study_refused():
    with pytest.raises(HoldoutRefused):
        eh.preflight("nope_v1")


def test_register_candidate(tmp_path):
    reg = tmp_path / "register.json"
    reg.write_text(json.dumps({"version": 1, "strategies": {"OLD": {"status": "REJECTED"}}}), encoding="utf-8")
    spec = _draft(tmp_path, "expiry_relief_long_v1", "DRAFT", "x")
    rec = promotion.register_candidate("EXPIRY_RELIEF_LONG", spec, "test", register_path=reg,
                                       records_dir=tmp_path / "records")
    assert rec["to"] == "UNVERIFIED"
    assert json.loads(reg.read_text(encoding="utf-8"))["strategies"]["EXPIRY_RELIEF_LONG"]["status"] == "UNVERIFIED"
    with pytest.raises(promotion.TransitionError):
        promotion.register_candidate("EXPIRY_RELIEF_LONG", spec, "again", register_path=reg,
                                     records_dir=tmp_path / "records")
    with pytest.raises(promotion.TransitionError):
        promotion.register_candidate("BAN_ENTRY_SHORT", spec, "wrong id", register_path=reg,
                                     records_dir=tmp_path / "records")
