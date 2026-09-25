"""
research/studies/signal_sim.py
==============================
The single per-signal simulation (plan D15). The engine's allocated trades and every counterfactual
use this one function, so all emitted signals share identical fill rules, including the entry clamp.

Side-aware throughout (plan D1): sg = +1 for BUY, -1 for SELL.

Entry (plan D19)
  next_open    : fill at the next bar's open + sg*k_entry ticks. Skipped (MISSED_CLAMP) if
                 sg*(open - entry_ref) > clamp_pct*entry_ref. A zero-volume bar is NO_LIQUIDITY.
  signal_close : fill at the signal bar's close (audit regression mode; no clamp).
Stops (plan D3)
  The stop is a trigger. With stop_limit_offset_pct set it is an SL-limit order whose limit is
  stop*(1 - sg*offset), rounded away from the entry:
    - intrabar trigger: fill at trigger - sg*k ticks, never worse than the limit;
    - bar opens beyond the trigger but not beyond the limit: fill at open - sg*k ticks, never worse
      than the limit;
    - bar opens beyond the limit: the SL-limit cannot fill (GAP_THROUGH_LIMIT); the desk escalates to
      a marketable exit, modelled at open - sg*k ticks and counted separately.
  With stop_limit_offset_pct = None the stop behaves as SL-M (legacy): gaps fill at open - sg*k ticks.
  The stop is checked before targets inside a bar (a bar touching both is a stop).
Targets
  Resting limits fill only on a trade-through: price strictly beyond the target (plan rule 1.2.3).
  Zero-quantity tranches are skipped and the remainder goes to the last tranche (plan D14).
  With two or more targets, the stop moves to the ACTUAL entry fill after the first target fills
  (plan D18); if the same bar also reaches that breakeven stop, the remainder exits there (plan D4).
Time and policy
  max_bars: exit at the close of the N-th bar held - sg*k_exit ticks.
  Policy: exit at the open of the first bar that contains or follows the bounded-exit time (15:05),
  - sg*k_exit ticks. If the data ends first, the broker's 15:10 RMS square-off is modelled at the last
  close with its Rs 20 + GST fee.
R accounting (plan D3)
  risk_rs = qty * |entry_ref - stop| (r_basis "trigger") or qty * |entry_ref - stop_limit| ("stop_limit").
  gross_r uses slippage-free reference prices; slip_r and fee_r are separate; net_r = gross - fee - slip.
  When the planned quantity is 0, prices are simulated per share, fees at one share, fee_estimated = True
  (plan D9). R is NaN, never 0.0, when it is undefined.
Evidence: every outcome here is bar-modelled (E1 for allocated trades, E1_CF for counterfactuals) and is
never admissible to the locked gate.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional, Sequence, Tuple

from research.backtest.bars import IST, Bar, round_to_tick, tick_size
from research.backtest.cost_model import DhanFeeEngine, OrderSide, ProductType
from research.backtest.policy import SessionPolicy

RMS_FEE_RS = round(DhanFeeEngine.AUTO_SQUAREOFF_BASE * (1.0 + DhanFeeEngine.GST_RATE), 2)


@dataclass(frozen=True)
class SimConfig:
    entry_mode: str = "next_open"
    entry_slippage_ticks: int = 1
    clamp_pct: float = 0.0010
    stop_slippage_ticks: int = 1
    exit_slippage_ticks: int = 1
    stop_limit_offset_pct: Optional[float] = None
    r_basis: str = "trigger"
    cost_mode: str = "dhan"
    flat_cost_pct: float = 0.00106
    product: ProductType = ProductType.MIS
    policy: SessionPolicy = field(default_factory=SessionPolicy)

    def __post_init__(self) -> None:
        if self.entry_mode not in ("next_open", "signal_close"):
            raise ValueError("entry_mode must be next_open or signal_close")
        if self.cost_mode not in ("dhan", "flat_pct_of_entry_notional"):
            raise ValueError("cost_mode must be dhan or flat_pct_of_entry_notional")
        if self.r_basis not in ("trigger", "stop_limit"):
            raise ValueError("r_basis must be trigger or stop_limit")
        if self.r_basis == "stop_limit" and self.stop_limit_offset_pct is None:
            raise ValueError("r_basis stop_limit needs stop_limit_offset_pct")
        if min(self.entry_slippage_ticks, self.stop_slippage_ticks, self.exit_slippage_ticks) < 0:
            raise ValueError("slippage ticks must be non-negative")


@dataclass(frozen=True)
class ExitFill:
    reason: str
    price: float
    qty: int
    time: datetime
    ideal_price: float
    method: str


@dataclass
class SimResult:
    disposition: str                      # FILLED, MISSED_CLAMP, NO_NEXT_BAR, NO_LIQUIDITY, INVALID_RISK
    side: str
    qty: int = 0
    fee_estimated: bool = False
    entry_time: Optional[datetime] = None
    entry_price: float = math.nan
    entry_ideal: float = math.nan
    stop_trigger: float = math.nan
    stop_limit: Optional[float] = None
    be_reference: Optional[str] = None
    exits: List[ExitFill] = field(default_factory=list)
    exit_reason: str = ""
    gap_through_limit: bool = False
    gross_pnl_rs: float = 0.0
    gross_pnl_ideal_rs: float = 0.0
    fees_rs: float = 0.0
    rms_fee_rs: float = 0.0
    slippage_rs: float = 0.0
    net_pnl_rs: float = 0.0
    risk_rs: float = math.nan
    r_basis: str = "trigger"
    gross_r: float = math.nan
    fee_r: float = math.nan
    slip_r: float = math.nan
    net_r: float = math.nan
    holding_bars: int = 0
    release_time: Optional[datetime] = None

    @property
    def filled(self) -> bool:
        return self.disposition == "FILLED"


def side_sign(side: str) -> int:
    s = side.upper()
    if s == "BUY":
        return 1
    if s == "SELL":
        return -1
    raise ValueError(f"side must be BUY or SELL, got {side!r}")


def _worse_entry(px: float, sg: int) -> float:
    return round_to_tick(px, "up" if sg > 0 else "down")


def _worse_exit(px: float, sg: int) -> float:
    return round_to_tick(px, "down" if sg > 0 else "up")


def plan_tranches(qty: int, targets: Sequence[Tuple[float, float]]) -> List[List]:
    """[target_number, price, qty]; zero-quantity tranches skipped, remainder on the last (plan D14)."""
    out, used = [], 0
    for n, (price, frac) in enumerate(targets, start=1):
        q = qty - used if n == len(targets) else int(math.floor(qty * frac + 1e-9))
        used += q
        out.append([n, float(price), q])
    return [t for t in out if t[2] > 0]


def size_qty(entry_ref: float, stop: float, side: str, risk_budget_rs: float, slot_cap_rs: float,
             stop_limit_offset_pct: Optional[float] = None) -> int:
    """floor(min(risk budget / per-share risk, slot cap / entry_ref)); per-share risk to the SL-limit when
    one is modelled (plan A.10), otherwise to the trigger. 0 when the geometry is invalid."""
    sg = side_sign(side)
    if not (entry_ref > 0 and math.isfinite(stop)) or sg * (entry_ref - stop) <= 0:
        return 0
    risk_px = stop if stop_limit_offset_pct is None else _worse_exit(stop * (1 - sg * stop_limit_offset_pct), sg)
    risk = abs(entry_ref - risk_px)
    return max(0, int(min(risk_budget_rs // risk, slot_cap_rs // entry_ref)))


def simulate_signal(bars: Sequence[Bar], signal_index: int, side: str, entry_ref: float, stop: float,
                    targets: Sequence[Tuple[float, float]], max_bars: Optional[int], qty: int, cfg: SimConfig,
                    session_day: Optional[date] = None) -> SimResult:
    sg = side_sign(side)
    if not (math.isfinite(entry_ref) and math.isfinite(stop)) or sg * (entry_ref - stop) <= 0:
        return SimResult("INVALID_RISK", side, qty=qty)
    fee_estimated = qty <= 0
    q = qty if qty > 0 else 1

    if cfg.entry_mode == "signal_close":
        sb = bars[signal_index]
        entry_ideal = sb.close
        entry = _worse_entry(sb.close + sg * cfg.entry_slippage_ticks * tick_size(sb.close), sg)
        entry_time, start = sb.end, signal_index + 1
    else:
        if signal_index + 1 >= len(bars):
            return SimResult("NO_NEXT_BAR", side, qty=qty)
        nb = bars[signal_index + 1]
        if nb.volume <= 0:
            return SimResult("NO_LIQUIDITY", side, qty=qty)
        if sg * (nb.open - entry_ref) > cfg.clamp_pct * entry_ref + 1e-9:
            return SimResult("MISSED_CLAMP", side, qty=qty)
        entry_ideal = nb.open
        entry = _worse_entry(nb.open + sg * cfg.entry_slippage_ticks * tick_size(nb.open), sg)
        entry_time, start = nb.start, signal_index + 1

    offset = cfg.stop_limit_offset_pct
    stop_limit = None if offset is None else _worse_exit(stop * (1 - sg * offset), sg)
    risk_ps = abs(entry_ref - stop) if cfg.r_basis == "trigger" else abs(entry_ref - stop_limit)

    tranches = plan_tranches(q, targets)
    multi = len(targets) > 1
    untargeted = 0 if tranches else q
    cur_stop, cur_limit = stop, stop_limit
    be_moved, be_ref = False, None
    exits: List[ExitFill] = []
    gap_limit = False
    k_stop, k_exit = cfg.stop_slippage_ticks, cfg.exit_slippage_ticks

    def remaining() -> int:
        return untargeted + sum(t[2] for t in tranches)

    def close_all(px: float, ideal: float, reason: str, method: str, when: datetime) -> None:
        nonlocal untargeted
        rem = remaining()
        if rem > 0:
            exits.append(ExitFill(reason, px, rem, when, ideal, method))
        tranches.clear()
        untargeted = 0

    def stop_fill(trigger_px: float) -> float:
        px = _worse_exit(trigger_px - sg * k_stop * tick_size(trigger_px), sg)
        if cur_limit is not None and sg * (px - cur_limit) < 0:
            px = cur_limit
        return px

    exit_idx: Optional[int] = None
    release: Optional[datetime] = None
    bounded = cfg.policy.bounded_exit
    for j in range(start, len(bars)):
        b = bars[j]
        b_start, b_end = b.start.astimezone(IST).time(), b.end.astimezone(IST).time()
        if b_start >= bounded or b_start <= bounded < b_end:
            close_all(_worse_exit(b.open - sg * k_exit * tick_size(b.open), sg), b.open, "POLICY_EXIT",
                      "BAR_OPEN_AT_POLICY_EXIT", b.start)
            exit_idx, release = j, b.end
            break
        entry_bar = cfg.entry_mode == "next_open" and j == start
        stop_reason = "BREAKEVEN_STOP" if be_moved else "STOP"
        if not entry_bar and sg * (b.open - cur_stop) <= 0:
            if cur_limit is not None and sg * (b.open - cur_limit) < 0:
                close_all(_worse_exit(b.open - sg * k_stop * tick_size(b.open), sg), b.open, "GAP_THROUGH_LIMIT",
                          "ESCALATED_MARKETABLE_AT_OPEN", b.start)
                gap_limit = True
            else:
                close_all(stop_fill(b.open), b.open, stop_reason, "GAP_STOP_AT_OPEN", b.start)
            exit_idx, release = j, b.end
            break
        adverse, favour = (b.low, b.high) if sg > 0 else (b.high, b.low)
        if sg * (adverse - cur_stop) <= 0:
            close_all(stop_fill(cur_stop), cur_stop, stop_reason, "STOP_TRIGGERED_IN_BAR", b.start)
            exit_idx, release = j, b.end
            break
        while tranches:
            n, tp, tq = tranches[0]
            if not sg * (favour - tp) > 0:
                break
            exits.append(ExitFill(f"TARGET_{n}", tp, tq, b.start, tp, "LIMIT_TRADE_THROUGH"))
            tranches.pop(0)
            if multi and not be_moved and remaining() > 0:
                be_moved, be_ref = True, "entry_fill"
                cur_stop = entry
                cur_limit = None if offset is None else _worse_exit(entry * (1 - sg * offset), sg)
                if sg * (adverse - cur_stop) <= 0:
                    close_all(stop_fill(cur_stop), cur_stop, "BREAKEVEN_STOP", "STOP_SAME_BAR_AFTER_TARGET",
                              b.start)
                    break
        if remaining() == 0:
            exit_idx, release = j, b.end
            break
        if max_bars is not None and j - start + 1 >= max_bars:
            close_all(_worse_exit(b.close - sg * k_exit * tick_size(b.close), sg), b.close, "TIME_STOP",
                      "BAR_CLOSE", b.end)
            exit_idx, release = j, b.end
            break
    if exit_idx is None:
        last = bars[-1]
        when = datetime.combine(session_day or last.session_date, cfg.policy.broker_rms, IST)
        close_all(_worse_exit(last.close - sg * k_exit * tick_size(last.close), sg), last.close, "RMS_SQUAREOFF",
                  "RMS_SQUAREOFF_MODELLED", when)
        exit_idx, release = len(bars) - 1, when

    entry_side, exit_side = (OrderSide.BUY, OrderSide.SELL) if sg > 0 else (OrderSide.SELL, OrderSide.BUY)
    rms_fee = RMS_FEE_RS * sum(1 for e in exits if e.reason == "RMS_SQUAREOFF")
    if cfg.cost_mode == "dhan":
        fees = DhanFeeEngine.calculate_order(entry_side, [(entry, q)], cfg.product).total_charges
        for e in exits:
            fees += DhanFeeEngine.calculate_order(exit_side, [(e.price, e.qty)], cfg.product).total_charges
    else:
        fees = cfg.flat_cost_pct * entry * q
    gross = sg * sum((e.price - entry) * e.qty for e in exits)
    gross_ideal = sg * sum((e.ideal_price - entry_ideal) * e.qty for e in exits)
    risk_rs = q * risk_ps
    net = gross - fees - rms_fee
    res = SimResult("FILLED", side.upper(), qty=qty, fee_estimated=fee_estimated, entry_time=entry_time,
                    entry_price=entry, entry_ideal=entry_ideal, stop_trigger=stop, stop_limit=stop_limit,
                    be_reference=be_ref, exits=exits, exit_reason=exits[-1].reason, gap_through_limit=gap_limit,
                    gross_pnl_rs=round(gross, 2), gross_pnl_ideal_rs=round(gross_ideal, 2), fees_rs=round(fees, 2),
                    rms_fee_rs=round(rms_fee, 2), slippage_rs=round(gross_ideal - gross, 2),
                    net_pnl_rs=round(net, 2), risk_rs=round(risk_rs, 4), r_basis=cfg.r_basis,
                    holding_bars=exit_idx - start + 1, release_time=release)
    if risk_rs > 0:
        res.gross_r = gross_ideal / risk_rs
        res.slip_r = (gross_ideal - gross) / risk_rs
        res.fee_r = (fees + rms_fee) / risk_rs
        res.net_r = net / risk_rs
    return res
