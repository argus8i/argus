"""Codex adapted fixture copy: original assertions unchanged. Only root and fresh synthetic ATR evidence added.
Original author probes remain unchanged in shared/reviews; see implementation report for exact fixture delta.
"""
"""Claude reviewer probes: Yashu's Adjusted A1 on every Track 2 paper execution path (1 Oct 2026).

Rule 8 v2 gate 1 (test-first): these are failing-first REVIEW tests. They never edit production code and never
place an order (PAPER ONLY, AGENTS.md Rule 1). Every probe writes only under pytest's tmp_path, except that the
terminal-server probes import antigravity/daemons/track2_terminal_server.py, whose class attribute builds a
TerminalStateHandler at import time (it opens <root>/shared/track2_liquid/capacity_ledger.db). Those probes are
therefore SKIPPED when the review root is the live ops checkout; run them against a scratch worktree.

Adjusted A1 (Yashu, 25 Sep 2026): at most 3 slots; Rs 38,000 per slot at the WORST admissible entry; Rs 1,14,000
aggregate exposure including pending reservations AND open positions; Rs 1,500 planned risk per trade. The
Rs 12,000 figure is a -10% scenario budget, not a maximum possible loss; nothing here tests or claims a maximum loss.

Code under review is imported from ARGUS_A1_REVIEW_ROOT (default: the live ops checkout). Usage:
    set ARGUS_A1_REVIEW_ROOT=<detached scratch worktree of the commit under review>
    python -m pytest shared/reviews/test_claude_adjusted_a1_probes_2026_10_01.py -p no:cacheprovider \
        --import-mode=importlib --basetemp <scratch dir> -rA
Probes whose module is absent at the root (research/decision exists only on research branches) are skipped.

Families: sim = simultaneous signals, qty = caller-supplied quantities, zero = zero affordable shares,
part = partial fills, cxl = cancellation races, rest = restart recovery, cfg = limits pinned / fail-closed defaults.
'ledger' = research/execution_realism/capacity.py; 'rd' = research/decision (research branches only).
A failing probe is a defect; a passing probe is evidence that the limit holds on that path (for that input only).
"""

import importlib
import io
import json
import os
import sys
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path

import pytest

LIVE_OPS = Path(r"C:\Users\yashw\swing trades").resolve()
ROOT = Path(os.environ.get("ARGUS_A1_REVIEW_ROOT", str(Path(__file__).resolve().parents[1]))).resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MAX_SLOTS = 3
SLOT_CAP_RS = 38_000.0
AGG_CAP_RS = 114_000.0
RISK_RS = 1_500.0
EPS = 1e-6


# ------------------------------------------------------------------------------------------------ helpers
def _mod(name: str):
    """Import `name` and prove it came from ROOT (namespace packages can merge several checkouts)."""
    if not (ROOT / Path(*name.split('.'))).with_suffix('.py').exists() and not (ROOT / Path(*name.split('.')) / '__init__.py').exists():
        pytest.skip(f"{name} not present at review root {ROOT}")
    try:
        m = importlib.import_module(name)
    except ImportError as exc:
        pytest.skip(f"{name} not present at review root {ROOT}: {exc}")
    f = Path(getattr(m, "__file__", "") or "").resolve()
    assert str(f).lower().startswith(str(ROOT).lower()), f"{name} imported from {f}, not from review root {ROOT}"
    return m


def _oms_mods():
    oms = _mod("antigravity.daemons.hybrid_execution_oms")
    pol = _mod("antigravity.models.execution_policy")
    return oms, pol


def _make_oms(d: Path, mode: str = "CO_PILOT", corpus: float = 250_000.0, use_file_config: bool = False):
    oms, pol = _oms_mods()
    d.mkdir(parents=True, exist_ok=True)
    cfg = None if use_file_config else pol.PolicyConfig(mode=pol.ExecutionMode(mode), expiry_seconds=600.0)
    return oms.HybridExecutionOMS(config=cfg, corpus_rs=corpus, output_dir=d)


def _cand(sym: str, sector: str, entry: float = 1000.0, stop: float = 960.0, strategy: str = "ORB", **kw):
    c = {"symbol": sym, "entry_price": entry, "stop_loss": stop, "sector": sector,
         "var_elm_rate": 0.2, "strategy": strategy, "atr14": 50.0,
         "atr_timestamp": datetime.now(timezone.utc).isoformat()}
    c.update(kw)
    return c


def _depth(d: Path, prices: dict) -> None:
    (d / "live_depth_track2.json").write_text(
        json.dumps({"watchlist": [{"symbol": s, "ltp": p} for s, p in prices.items()]}), encoding="utf-8")


def _orders(d: Path) -> list:
    p = d / "paper_orders.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(json.loads(line))
    return out


def _live_orders_on_disk(d: Path) -> dict:
    last = {}
    for r in _orders(d):
        if r.get("order_id"):
            last[r["order_id"]] = r
    return {k: v for k, v in last.items() if v.get("status") in ("OPEN", "RUNNING", "QUEUED", "PARTIAL")}


def _intent_ids_on_disk(d: Path) -> set:
    data = json.loads((d / "execution_intents.json").read_text(encoding="utf-8"))
    return {i["intent_id"] for i in data.get("intents", [])}


LIVE_INTENT_STATES = ("PENDING_APPROVAL", "PRE_ARMED", "APPROVED", "ROUTED", "FILLED")


def _accepted(results) -> list:
    """Admissions that hold capacity. submit_candidate returns the intent even when AUTONOMOUS routing then
    aborted it (status REJECTED), so the status is checked, not just the return value."""
    return [r for r in results if r[0] is not None and getattr(r[0].status, "value", r[0].status) in LIVE_INTENT_STATES]


SYMS = [("AAA", "S_A"), ("BBB", "S_B"), ("CCC", "S_C"), ("DDD", "S_D"), ("EEE", "S_E"), ("FFF", "S_F"),
        ("GGG", "S_G"), ("HHH", "S_H")]
STRATS = ["ORB", "VWAP_RECLAIM", "PEAD_v2", "EXPIRY_RELIEF_v2", "RECOIL", "COMPASS", "TRAPDOOR", "LAST_LIGHT"]


# ---- terminal server (import has a side effect: skip on the live checkout) ----------------------------
def _terminal(tmp_path: Path, monkeypatch):
    if ROOT == LIVE_OPS:
        pytest.skip("terminal-server import builds a TerminalStateHandler at import time and opens "
                    "<root>/shared/track2_liquid/capacity_ledger.db; run against a scratch worktree")
    ts = _mod("antigravity.daemons.track2_terminal_server")
    d = tmp_path / "terminal"
    d.mkdir()
    for name in ("PAPER_ORDERS_PATH", "EVENTS_LOG_PATH", "LIVE_DEPTH_PATH", "LIVE_ORB_PATH", "TRADE_LOG_MD_PATH"):
        monkeypatch.setattr(ts, name, d / Path(getattr(ts, name)).name)
    monkeypatch.setattr(ts, "ROTATIONS_LOG_PATH", d / "rotations.jsonl")
    monkeypatch.setattr(ts, "DYNAMIC_UNIVERSE_PATH", d / "dynamic_universe.json")
    monkeypatch.setattr(ts, "TRACK2_ROOT", d)
    sh = ts.TerminalStateHandler(corpus_rs=250_000.0, output_dir=d)
    monkeypatch.setattr(sh, "get_performance", lambda: {})          # display-only metric, not an admission input
    return ts, sh, d


def _enter(ts, sh, body: dict) -> dict:
    h = ts.TerminalHTTPRequestHandler.__new__(ts.TerminalHTTPRequestHandler)
    h.state_handler = sh
    raw = json.dumps(body).encode()
    h.headers = {"Content-Length": str(len(raw))}
    h.rfile = io.BytesIO(raw)
    out: dict = {}

    def capture(data, status_code=HTTPStatus.OK):
        out["data"], out["code"] = data, int(status_code)

    h._serve_json = capture
    h._handle_enter()
    return out


def _open_brackets(d: Path) -> set:
    return {r["order_id"] for r in _orders(d) if r.get("status") == "OPEN"}


# ---- research capacity ledger ---------------------------------------------------------------------------
A1_LEDGER_KW = dict(capital_rs=250_000.0, cash_buffer_rs=136_000.0, slots=3, risk_per_trade_rs=1_500.0)
SHA = "a" * 64
T0 = 1_000_000.0


def _ledger(path: Path):
    cap = _mod("research.execution_realism.capacity")
    lg = cap.ReservationLedger(str(path), cap.CapacityConfig(**A1_LEDGER_KW))
    gate = lg.open_session("2026-10-01", SHA, T0)
    return cap, lg, gate


def _lc(cap, iid, sym, sector, qty=37, limit=1000.0, stop_limit=960.0):
    return cap.Candidate(iid, sym, sector, qty, limit, stop_limit, T0 + 600.0, margin_rate=0.2,
                         features={"spread_atr": 0.1, "speed_atr": 0.1, "rel_strength": 1.0})


def _route_and_fill(cap, lg, iid, fill_qty=None, px=1000.0):
    S = cap.ResState
    lg.transition(iid, S.APPROVED, expected_version=lg.row(iid)["version"], now=T0 + 1)
    lg.transition(iid, S.ROUTED, expected_version=lg.row(iid)["version"], now=T0 + 2,
                  evidence={"order_id": f"PAPER-{iid}"})
    if fill_qty:
        lg.record_entry_fill(iid, fill_qty, px, now=T0 + 3, evidence={"fill_evidence": "E3_TEST_FIXTURE"})


# ================================================================================================ guard
def test_a1_00_imports_resolve_under_review_root():
    for name in ("antigravity.models.execution_policy", "antigravity.models.track2_portfolio_risk_governor",
                 "antigravity.daemons.hybrid_execution_oms", "antigravity.models.track2_multi_strategy_engine",
                 "research.execution_realism.capacity"):
        _mod(name)


# ================================================================================================ sim
def test_a1_sim01_oms_four_strategies_one_process(tmp_path):
    """Four simultaneous signals from four strategies through one OMS: at most 3 admitted, <= Rs 1,14,000 at limit.
    Low-risk trades (Rs 470 each) so that the slot gate, not the derived Rs 4,500 aggregate-risk gate, binds."""
    o = _make_oms(tmp_path / "d")
    res = [o.submit_candidate(_cand(s, sec, entry=800.0, stop=790.0, strategy=STRATS[i]))
           for i, (s, sec) in enumerate(SYMS[:4])]
    ok = [r[0] for r in _accepted(res)]
    assert 1 <= len(ok) <= MAX_SLOTS
    assert sum(i.shares * i.limit_price for i in ok) <= AGG_CAP_RS + EPS
    assert any("MAX_CONCURRENT_POSITIONS" in r[1] for r in res if r[0] is None), [r[1] for r in res]


def test_a1_sim02_oms_threaded_submissions(tmp_path):
    """Eight threads in ONE process submit at once: the in-process RLock must hold the 3-slot cap."""
    o = _make_oms(tmp_path / "d")
    results, barrier = [], threading.Barrier(8, timeout=20)

    def go(i):
        s, sec = SYMS[i]
        barrier.wait()
        results.append(o.submit_candidate(_cand(s, sec, strategy=STRATS[i])))

    ts = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert len(results) == 8
    assert 1 <= len(_accepted(results)) <= MAX_SLOTS


def test_a1_sim03_oms_two_processes_share_one_book(tmp_path):
    """Terminal server, Telegram bot and OMS supervisor each construct their own HybridExecutionOMS on the SAME
    shared/track2_liquid files. Two such instances (started together, as daemons are) must not admit 3 each."""
    d = tmp_path / "shared_track2"
    a = _make_oms(d, mode="AUTONOMOUS")
    b = _make_oms(d, mode="AUTONOMOUS")
    _depth(d, {s: 1000.0 for s, _ in SYMS})
    for i, (s, sec) in enumerate(SYMS[:3]):
        a.submit_candidate(_cand(s, sec, strategy=STRATS[i]))
    for i, (s, sec) in enumerate(SYMS[3:6]):
        b.submit_candidate(_cand(s, sec, strategy=STRATS[i + 3]))
    live = _live_orders_on_disk(d)
    assert len(live) <= MAX_SLOTS, f"{len(live)} live paper orders on disk: {sorted(v['symbol'] for v in live.values())}"
    assert sum(float(v["shares"]) * float(v["limit_price"]) for v in live.values()) <= AGG_CAP_RS + EPS


def test_a1_sim04_oms_two_processes_lose_no_admitted_intent(tmp_path):
    """Every intent an OMS admitted (and therefore reserved capacity for) must survive on disk; otherwise a
    restart restores fewer reservations than exist and the next admission double-allocates."""
    d = tmp_path / "shared_track2"
    a = _make_oms(d)
    b = _make_oms(d)
    admitted = []
    for i, (s, sec) in enumerate(SYMS[:2]):
        admitted.append(a.submit_candidate(_cand(s, sec, strategy=STRATS[i]))[0])
    admitted.append(b.submit_candidate(_cand(*SYMS[2], strategy=STRATS[2]))[0])
    assert all(x is not None for x in admitted)        # 3 admissions in total: within the 3-slot cap
    missing = {x.intent_id for x in admitted} - _intent_ids_on_disk(d)
    assert not missing, f"admitted intents missing from execution_intents.json: {sorted(missing)}"


def test_a1_sim05_terminal_enter_counts_oms_pending_intents(tmp_path, monkeypatch):
    """The terminal's /api/action/enter and the OMS it hosts are two books. With 3 OMS reservations pending,
    a terminal entry must be refused."""
    ts, sh, d = _terminal(tmp_path, monkeypatch)
    for i, (s, sec) in enumerate([("CDSL", "CAPITAL_MARKETS_FINTECH"), ("SUZLON", "GREEN_ENERGY_POWER"),
                                  ("RVNL", "PSU_RAILWAYS_INFRA")]):
        r = sh.oms.submit_candidate(_cand(s, sec, strategy=STRATS[i]))
        assert r[0] is not None, r[1]
    out = _enter(ts, sh, {"symbol": "DIXON", "entry_price": 1000.0, "stop_price": 980.0, "quantity": 30,
                          "var_elm_rate": 0.2})
    assert out.get("data", {}).get("status") != "APPROVED", (
        "terminal opened a 4th position while 3 OMS reservations were pending")


def test_a1_sim06_terminal_enter_concurrent_requests(tmp_path, monkeypatch):
    """ThreadingHTTPServer serves /api/action/enter concurrently with no admission lock. Four simultaneous entries
    on an empty book (the barrier only forces an interleaving the unlocked code already permits)."""
    ts, sh, d = _terminal(tmp_path, monkeypatch)
    real, barrier = sh.get_state, threading.Barrier(4, timeout=30)

    def gated():
        s = real()
        barrier.wait()
        return s

    monkeypatch.setattr(sh, "get_state", gated)
    outs, errors = [], []

    def go(sym):
        try:
            outs.append(_enter(ts, sh, {"symbol": sym, "entry_price": 1000.0, "stop_price": 980.0,
                                        "quantity": 30, "var_elm_rate": 0.2}))
        except Exception as exc:                                         # surfaced, never swallowed
            errors.append(repr(exc))

    th = [threading.Thread(target=go, args=(s,)) for s in ["CDSL", "SUZLON", "RVNL", "DIXON"]]
    [t.start() for t in th]
    [t.join(60) for t in th]
    assert not errors and len(outs) == 4, errors
    assert len(_open_brackets(d)) <= MAX_SLOTS, f"{len(_open_brackets(d))} OPEN paper brackets written"


def test_a1_sim07_ledger_batch_counts_open_and_pending(tmp_path):
    """Reference ledger: 1 open position + 1 pending reservation + 3 simultaneous strategies -> exactly 1 more."""
    cap, lg, gate = _ledger(tmp_path / "l.db")
    assert lg.admit(_lc(cap, "OPEN1", "AAA", "S_A"), now=T0, session_gate=gate).ok
    _route_and_fill(cap, lg, "OPEN1", fill_qty=37)
    assert lg.admit(_lc(cap, "PEND1", "BBB", "S_B"), now=T0, session_gate=gate).ok
    batch = [_lc(cap, f"N{i}", s, sec) for i, (s, sec) in enumerate(SYMS[2:5])]
    out = lg.admit_batch(batch, now=T0 + 5, session_gate=gate, batch_id="sim07")
    u = lg.usage()
    assert sum(a.ok for a in out) == 1
    assert u.slots <= MAX_SLOTS and u.notional_rs <= AGG_CAP_RS + EPS and u.risk_rs <= 3 * RISK_RS + EPS


def test_a1_sim08_rd_allocate_counts_open_and_pending():
    al = _mod("research.decision.allocator")
    from datetime import date
    cands = [al.Candidate(strategy_id=st, symbol=s, side="BUY", session=date(2026, 10, 1), priority=1,
                          notional=38_000.0, cluster=s, sector=s) for st, s in
             [("ORB", "CCC"), ("PEAD_v2", "DDD"), ("EXPIRY_RELIEF_v2", "EEE")]]
    r = al.allocate(cands, free_cash=1e6, open_positions=[al.Position("AAA", 38_000.0, "AAA", "AAA")],
                    pending_orders=[al.Position("BBB", 38_000.0, "BBB", "BBB")])
    assert len(r.allocated) == 1


# ================================================================================================ qty
@pytest.mark.parametrize("slip_bps", [15.0, 100.0])
def test_a1_qty01_intent_caller_shares_at_worst_entry(slip_bps):
    """38 caller-supplied shares at a Rs 1,000 trigger are Rs 38,000 at the trigger but more at the limit collar
    (the worst admissible entry the order is allowed to pay)."""
    _, pol = _oms_mods()
    it = pol.ExecutionIntent.create_from_candidate(
        _cand("AAA", "S_A", entry=1000.0, stop=980.0, shares=38, max_slippage_bps=slip_bps))
    assert it is None or it.shares * it.limit_price <= SLOT_CAP_RS + EPS, (
        f"{it.shares} x limit {it.limit_price} = {it.shares * it.limit_price:.2f} > 38,000")


def test_a1_qty02_intent_auto_size_at_worst_entry():
    _, pol = _oms_mods()
    it = pol.ExecutionIntent.create_from_candidate(_cand("AAA", "S_A", entry=1000.0, stop=990.0))
    assert it is None or it.shares * it.limit_price <= SLOT_CAP_RS + EPS, (
        f"{it.shares} x limit {it.limit_price} = {it.shares * it.limit_price:.2f} > 38,000")


def test_a1_qty03_intent_ignores_caller_slot_cap_override():
    """A caller field must not raise the owner-approved slot cap."""
    _, pol = _oms_mods()
    it = pol.ExecutionIntent.create_from_candidate(
        _cand("AAA", "S_A", entry=100.0, stop=99.0, shares=500, max_slot_notional_rs=1e9))
    assert it is None or it.shares * it.limit_price <= SLOT_CAP_RS + EPS, f"notional {it.notional_rs}"


def test_a1_qty04_oms_rejects_caller_slot_cap_override(tmp_path):
    o = _make_oms(tmp_path / "d")
    it, msg = o.submit_candidate(_cand("AAA", "S_A", entry=100.0, stop=99.0, shares=500, max_slot_notional_rs=1e9))
    assert it is None or it.shares * it.limit_price <= SLOT_CAP_RS + EPS, msg


def test_a1_qty05_oms_admitted_quantity_at_worst_entry(tmp_path):
    """End to end through OMS + governor: the admitted reservation at its limit price must be <= Rs 38,000."""
    o = _make_oms(tmp_path / "d")
    it, msg = o.submit_candidate(_cand("AAA", "S_A", entry=1000.0, stop=980.0, shares=38))
    assert it is None or it.shares * it.limit_price <= SLOT_CAP_RS + EPS, (
        f"admitted {it.shares} x limit {it.limit_price} = {it.shares * it.limit_price:.2f}")


def test_a1_qty06_intent_caller_shares_risk_cap():
    _, pol = _oms_mods()
    it = pol.ExecutionIntent.create_from_candidate(_cand("AAA", "S_A", entry=1000.0, stop=980.0, shares=100))
    assert it is None or it.shares * (it.entry_price - it.stop_loss) <= RISK_RS + EPS


def _sig(mse, sym, entry, stop, shares, notional, risk, strategy="VWAP_RECLAIM", sector="S1"):
    return mse.UnifiedTradeSignal(symbol=sym, strategy_type=strategy, conviction_score=0.9, entry_price=entry,
                                  stop_price=stop, target_tranche1=entry * 1.02, target_tranche2=entry * 1.04,
                                  shares=shares, notional_value_rs=notional, actual_risk_rs=risk, risk_pct=1.0,
                                  volume_multiple=2.0, sector=sector, details={}, is_shadow=False)


def test_a1_qty07_engine_recomputes_notional_and_risk():
    """rank_and_allocate must not trust a strategy's reported notional/risk (several adapters default it to 0.0)."""
    mse = _mod("antigravity.models.track2_multi_strategy_engine")
    sel = mse.MultiStrategyEngine().rank_and_allocate([_sig(mse, "AAA", 100.0, 97.0, 1000, 0.0, 0.0)])
    for s in sel:
        assert s.shares * s.entry_price <= SLOT_CAP_RS + EPS, f"{s.shares} x {s.entry_price}"
        assert s.shares * (s.entry_price - s.stop_price) <= RISK_RS + EPS


def test_a1_qty08_engine_enforces_per_trade_risk():
    mse = _mod("antigravity.models.track2_multi_strategy_engine")
    sel = mse.MultiStrategyEngine().rank_and_allocate([_sig(mse, "AAA", 38.0, 35.0, 1000, 38_000.0, 3_000.0)])
    for s in sel:
        assert s.shares * (s.entry_price - s.stop_price) <= RISK_RS + EPS, (
            f"selected {s.shares} shares with planned risk {s.shares * (s.entry_price - s.stop_price):.2f}")


def test_a1_qty09_terminal_enter_caller_quantity(tmp_path, monkeypatch):
    ts, sh, d = _terminal(tmp_path, monkeypatch)
    over_cap = _enter(ts, sh, {"symbol": "DIXON", "entry_price": 1000.0, "stop_price": 990.0, "quantity": 39,
                               "var_elm_rate": 0.2})
    over_risk = _enter(ts, sh, {"symbol": "CDSL", "entry_price": 1000.0, "stop_price": 950.0, "quantity": 31,
                                "var_elm_rate": 0.2})
    assert over_cap["data"]["status"] != "APPROVED" and over_risk["data"]["status"] != "APPROVED"
    assert "SLOT_CAP" in over_cap["data"].get("error", "") and "RISK" in over_risk["data"].get("error", ""), (
        over_cap, over_risk)                                             # refused for the A1 reason, not another gate


def test_a1_qty10_ledger_caller_quantity(tmp_path):
    cap, lg, gate = _ledger(tmp_path / "l.db")
    R = cap.RejectReason
    assert lg.admit(_lc(cap, "Q1", "AAA", "S_A", qty=39, limit=1000.0, stop_limit=999.0),
                    now=T0, session_gate=gate).reason is R.PER_SLOT_NOTIONAL
    assert lg.admit(_lc(cap, "Q2", "BBB", "S_B", qty=31, limit=1000.0, stop_limit=950.0),
                    now=T0, session_gate=gate).reason is R.PER_TRADE_RISK


def test_a1_qty11_orb_sizing_caps():
    """LiquidMomentumEngine.calculate_position_size (field-test desk and ORB alpha engine; limit = entry)."""
    lme = _mod("antigravity.models.liquid_momentum_screener").LiquidMomentumEngine
    z = lme.calculate_position_size(entry_price=1000.0, or_low=980.0, atr14=20.0, dtv_med20_cr=100.0,
                                    order_execution_type="SL_LIMIT")
    assert 0 < z.shares and z.shares * 1000.0 <= SLOT_CAP_RS + EPS and z.actual_risk_rs <= RISK_RS + EPS


def test_a1_qty12_rd_allocate_slot_cap():
    al = _mod("research.decision.allocator")
    from datetime import date
    c = al.Candidate(strategy_id="ORB", symbol="AAA", side="BUY", session=date(2026, 10, 1), priority=1,
                     notional=38_000.01, cluster="A", sector="A")
    r = al.allocate([c], free_cash=1e6)
    assert not r.allocated and r.dropped[0][1] == "SLOT_CAP"


# ================================================================================================ zero
@pytest.mark.parametrize("kw", [dict(entry=39_000.0, stop=38_000.0), dict(entry=39_000.0, stop=38_990.0, shares=1),
                                dict(entry=5_000.0, stop=3_000.0)], ids=["price_above_slot", "caller_one_share",
                                                                         "risk_floor_zero"])
def test_a1_zero01_intent_unaffordable(kw):
    _, pol = _oms_mods()
    assert pol.ExecutionIntent.create_from_candidate(_cand("AAA", "S_A", **kw)) is None


def test_a1_zero02_engine_never_forces_one_share():
    mse = _mod("antigravity.models.track2_multi_strategy_engine")
    sel = mse.MultiStrategyEngine().rank_and_allocate([_sig(mse, "AAA", 40_000.0, 39_900.0, 1, 40_000.0, 100.0)])
    assert all(s.shares * s.entry_price <= SLOT_CAP_RS + EPS for s in sel), (
        f"selected {[(s.symbol, s.shares, s.notional_value_rs) for s in sel]}")


def test_a1_zero03_ledger_size_position_zero():
    cap = _mod("research.execution_realism.capacity")
    with pytest.raises(ValueError):
        cap.size_position(39_000.0, 38_000.0, 5.0, cap.CapacityConfig(**A1_LEDGER_KW))


def test_a1_zero03b_orb_sizing_zero():
    lme = _mod("antigravity.models.liquid_momentum_screener").LiquidMomentumEngine
    assert lme.calculate_position_size(entry_price=40_000.0, or_low=39_900.0, atr14=50.0, dtv_med20_cr=500.0,
                                       order_execution_type="SL_LIMIT").shares == 0


def test_a1_zero04_oms_unaffordable_creates_no_intent(tmp_path):
    d = tmp_path / "d"
    o = _make_oms(d)
    it, msg = o.submit_candidate(_cand("AAA", "S_A", entry=39_000.0, stop=38_000.0))
    assert it is None and not o.intents and not (d / "execution_intents.json").exists(), msg


def test_a1_zero05_rd_base_qty_zero():
    sz = _mod("research.decision.sizing")
    assert sz.base_qty(39_000.0, 38_000.0, 1e6) == 0


# ================================================================================================ partial fills
def test_a1_part01_ledger_partial_fill_reservation(tmp_path):
    """Filled inventory and the working remainder both stay reserved; only a cancel ack frees the remainder."""
    cap, lg, gate = _ledger(tmp_path / "l.db")
    S = cap.ResState
    assert lg.admit(_lc(cap, "P1", "AAA", "S_A"), now=T0, session_gate=gate).ok
    _route_and_fill(cap, lg, "P1", fill_qty=10)
    assert lg.row("P1")["state"] == S.PARTIAL.value and abs(lg.usage().notional_rs - 37_000.0) < EPS
    lg.transition("P1", S.CANCEL_REQUESTED, expected_version=lg.row("P1")["version"], now=T0 + 4)
    assert abs(lg.usage().notional_rs - 37_000.0) < EPS and lg.usage().slots == 1
    lg.transition("P1", S.OPEN_POSITION, expected_version=lg.row("P1")["version"], now=T0 + 5,
                  evidence={"cancel_ack": "ACK-TEST"})
    assert abs(lg.usage().notional_rs - 10_000.0) < EPS and lg.usage().slots == 1


def _route_one(d: Path, sym="AAA", sector="S_A"):
    o = _make_oms(d, mode="AUTONOMOUS")
    _depth(d, {s: 1000.0 for s, _ in SYMS})
    it, msg = o.submit_candidate(_cand(sym, sector))
    assert it is not None and it.order_id, msg
    rec = [r for r in _orders(d) if r.get("order_id") == it.order_id][-1]
    return o, it, rec


def _append(d: Path, rec: dict) -> None:
    with (d / "paper_orders.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def test_a1_part02_oms_restart_after_kill_keeps_partial_inventory(tmp_path):
    """PARTIAL order (20 of 37 filled) -> kill switch with no live feed -> restart. The 20 held shares must
    still occupy their slot after the restart."""
    d = tmp_path / "d"
    _, it, rec = _route_one(d)
    _append(d, {**rec, "status": "PARTIAL", "filled_shares": 20})        # a fill writer's PARTIAL record
    b = _make_oms(d, mode="AUTONOMOUS")
    (d / "live_depth_track2.json").unlink()                              # feed unavailable at the kill switch
    b.emergency_flatten_all(reason="PROBE_KILL")
    c = _make_oms(d, mode="AUTONOMOUS")                                  # restart
    _depth(d, {s: 1000.0 for s, _ in SYMS})
    res = [c.submit_candidate(_cand(s, sec)) for s, sec in SYMS[1:4]]
    assert len(_accepted(res)) <= MAX_SLOTS - 1, (
        f"after restart {len(_accepted(res))} new admissions; the 20 held {it.symbol} shares lost their slot")


def test_a1_part03_oms_exit_instruction_keeps_inventory_until_exit_fill(tmp_path):
    """An exit instruction is not an exit (AGENTS.md Rule 4, Track 2): a kill-switch square-off priced at a
    sampled LTP, with no exit-fill evidence, must not free the filled position's slot."""
    d = tmp_path / "d"
    _, it, rec = _route_one(d)
    _append(d, {**rec, "status": "OPEN", "filled_shares": rec["shares"]})
    b = _make_oms(d, mode="AUTONOMOUS")
    _depth(d, {s: 1005.0 if s == "AAA" else 1000.0 for s, _ in SYMS})
    b.emergency_flatten_all(reason="PROBE_KILL")
    _depth(d, {s: 1000.0 for s, _ in SYMS})
    res = [b.submit_candidate(_cand(s, sec)) for s, sec in SYMS[1:4]]
    assert len(_accepted(res)) <= MAX_SLOTS - 1, (
        f"{len(_accepted(res))} new admissions while the {it.symbol} exit has no exit-fill evidence")


def test_a1_part04_rd_exposure_book_partial_then_cancel():
    al = _mod("research.decision.allocator")
    b = al.ExposureBook()
    b.reserve("o1", "AAA", "BUY", 38, 1000.0)
    b.fill("o1", 10, 1000.0)
    b.cancel("o1")
    assert b.slots_used() == 1 and abs(b.exposure() - 10_000.0) < EPS


# ================================================================================================ cancellation
def test_a1_cxl01_ledger_cancel_request_holds_and_late_fill_kept(tmp_path):
    cap, lg, gate = _ledger(tmp_path / "l.db")
    S = cap.ResState
    assert lg.admit(_lc(cap, "C1", "AAA", "S_A"), now=T0, session_gate=gate).ok
    _route_and_fill(cap, lg, "C1")
    lg.transition("C1", S.CANCEL_REQUESTED, expected_version=lg.row("C1")["version"], now=T0 + 3)
    assert abs(lg.usage().notional_rs - 37_000.0) < EPS                 # request does not release
    lg.record_entry_fill("C1", 5, 1000.0, now=T0 + 4, evidence={"fill_evidence": "LATE_FILL"})
    with pytest.raises(cap.EvidenceRequired):                            # fills exist: cannot confirm a full cancel
        lg.transition("C1", S.CANCELLED_CONFIRMED, expected_version=lg.row("C1")["version"], now=T0 + 5,
                      evidence={"cancel_ack": "ACK", "filled_qty_at_ack": 5})
    lg.transition("C1", S.OPEN_POSITION, expected_version=lg.row("C1")["version"], now=T0 + 6,
                  evidence={"cancel_ack": "ACK"})
    assert abs(lg.usage().notional_rs - 5_000.0) < EPS and lg.usage().slots == 1


def test_a1_cxl02_oms_cancel_request_holds_capacity(tmp_path):
    """Three routed, unfilled orders; the kill switch requests their cancellation. Until a cancel confirmation
    (paper-sim or broker ack) exists, a fill can still arrive, so no new reservation may take those slots.
    (When an explicit confirmation API is added, extend this probe to confirm and then expect release.)"""
    d = tmp_path / "d"
    o = _make_oms(d, mode="AUTONOMOUS")
    _depth(d, {s: 1000.0 for s, _ in SYMS})
    for s, sec in SYMS[:3]:
        assert o.submit_candidate(_cand(s, sec))[0] is not None
    o.emergency_flatten_all(reason="PROBE_CANCEL_REQUEST")
    res = [o.submit_candidate(_cand(s, sec)) for s, sec in SYMS[3:6]]
    assert not _accepted(res), f"{len(_accepted(res))} admissions before any cancel confirmation"


def test_a1_cxl03_rd_exposure_book_fill_after_cancel():
    """A fill that races a cancel must be recordable and keep its capacity."""
    al = _mod("research.decision.allocator")
    b = al.ExposureBook()
    b.reserve("o1", "AAA", "BUY", 38, 1000.0)
    b.cancel("o1")
    try:
        b.fill("o1", 5, 1000.0)
    except ValueError as exc:
        pytest.fail(f"late fill after cancel request refused: {exc}")
    assert b.slots_used() == 1 and abs(b.exposure() - 5_000.0) < EPS


# ================================================================================================ restart
def test_a1_rest01_oms_restart_restores_pending_and_open(tmp_path):
    d = tmp_path / "d"
    a = _make_oms(d)
    _depth(d, {s: 1000.0 for s, _ in SYMS})
    ids = [a.submit_candidate(_cand(s, sec))[0].intent_id for s, sec in SYMS[:3]]
    for iid in ids[:2]:                  # approver as the terminal server passes it (track2_terminal_server.py:795)
        assert a.approve_intent(iid, approver="TERMINAL_OPERATOR", current_ltp=1000.0)["status"] == "SUCCESS"
    b = _make_oms(d)                                                     # restart
    act, pen = b._get_active_and_pending_exposures()
    assert len(act) == 2 and len(pen) == 1
    assert b.submit_candidate(_cand(*SYMS[3]))[0] is None


@pytest.mark.parametrize("what", ["intents_file", "orders_tail"])
def test_a1_rest02_oms_restart_fails_closed_on_corrupt_state(tmp_path, what):
    d = tmp_path / "d"
    a = _make_oms(d, mode="AUTONOMOUS" if what == "orders_tail" else "CO_PILOT")
    _depth(d, {s: 1000.0 for s, _ in SYMS})
    for s, sec in SYMS[:3]:
        assert a.submit_candidate(_cand(s, sec))[0] is not None
    if what == "intents_file":
        p = d / "execution_intents.json"
        p.write_text(p.read_text(encoding="utf-8")[:40], encoding="utf-8")
    else:                                                                # torn final append (crash mid-write)
        p = d / "paper_orders.jsonl"
        raw = p.read_text(encoding="utf-8").rstrip("\n")
        p.write_text(raw[: len(raw) - 25], encoding="utf-8")
    try:
        b = _make_oms(d, mode="AUTONOMOUS" if what == "orders_tail" else "CO_PILOT")
    except Exception:
        return                                                           # refusing to start is fail-closed
    it, msg = b.submit_candidate(_cand(*SYMS[3]))
    assert it is None, f"unreadable durable state was treated as an empty book: {msg}"


def test_a1_rest03_oms_counts_terminal_bracket_notional(tmp_path):
    """/api/action/enter writes OPEN brackets without a 'notional' key; the OMS hydrates them at start-up."""
    d = tmp_path / "d"
    d.mkdir()
    _append(d, {"order_id": "BRK_DIXON_1", "symbol": "DIXON", "shares": 38, "entry_price": 1000.0,
                "risk_rs": 760.0, "status": "OPEN"})
    o = _make_oms(d)
    act, _ = o._get_active_and_pending_exposures()
    assert len(act) == 1 and abs(act[0]["notional_rs"] - 38_000.0) < EPS, f"exposure seen by the governor: {act}"


def test_a1_rest04_ledger_restart_restores_and_requires_reconcile(tmp_path):
    cap, lg, gate = _ledger(tmp_path / "l.db")
    assert lg.admit(_lc(cap, "R1", "AAA", "S_A"), now=T0, session_gate=gate).ok
    _route_and_fill(cap, lg, "R1", fill_qty=10)
    assert lg.admit(_lc(cap, "R2", "BBB", "S_B"), now=T0, session_gate=gate).ok
    before = lg.usage()
    lg2 = cap.ReservationLedger(str(tmp_path / "l.db"), cap.CapacityConfig(**A1_LEDGER_KW))
    assert lg2.usage() == before
    assert lg2.claim_oms("OMS-B", T0 + 100) is True
    a = lg2.admit(_lc(cap, "R3", "CCC", "S_C"), now=T0 + 101, session_gate=gate)
    assert not a.ok and a.reason is cap.RejectReason.RECONCILIATION_REQUIRED


# ================================================================================================ cfg / defaults
def test_a1_cfg01_oms_config_file_cannot_widen_slots(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "execution_config.json").write_text(json.dumps({
        "mode": "CO_PILOT", "expiry_seconds": 600, "max_open_positions": 4, "cash_buffer_rs": 98_000.0}),
        encoding="utf-8")
    o = _make_oms(d, use_file_config=True)
    res = [o.submit_candidate(_cand(s, sec)) for s, sec in SYMS[:4]]
    ok = [r[0] for r in _accepted(res)]
    assert len(ok) <= MAX_SLOTS and sum(i.shares * i.limit_price for i in ok) <= AGG_CAP_RS + EPS, (
        f"{len(ok)} admitted, {sum(i.shares * i.limit_price for i in ok):.2f} reserved")


def test_a1_cfg02_oms_corpus_cannot_widen_slot_cap(tmp_path):
    o = _make_oms(tmp_path / "d", corpus=400_000.0)
    it, msg = o.submit_candidate(_cand("AAA", "S_A", entry=100.0, stop=99.0, shares=500, max_slot_notional_rs=1e9))
    assert it is None or it.shares * it.limit_price <= SLOT_CAP_RS + EPS, (
        f"admitted {it.shares} x {it.limit_price} = {it.shares * it.limit_price:.2f}")


def test_a1_cfg03_governor_default_is_a1():
    g = _mod("antigravity.models.track2_portfolio_risk_governor").PortfolioRiskGovernor()
    r = g.assess_candidate("CDSL", 100.0, 99.0, 500, [], var_elm_rate=0.2)
    assert not r.is_approved, "default governor approved a Rs 50,000 single position"


def test_a1_cfg04_capacity_config_default_is_a1():
    """Same check as CODEX test_reference_ledger_config_matches_adjusted_a1 (30 Sep)."""
    c = _mod("research.execution_realism.capacity").CapacityConfig()
    assert (c.slots, c.risk_per_trade_rs) == (3, 1500.0)
    assert abs(c.slot_notional_rs - SLOT_CAP_RS) < EPS and abs(c.deployable_rs - AGG_CAP_RS) < EPS, (
        f"slot {c.slot_notional_rs:.2f}, deployable {c.deployable_rs:.2f}")
