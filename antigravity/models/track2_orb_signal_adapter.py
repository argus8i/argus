"""Convert sealed direct-Kite bars into rule-bound Track 2 paper instructions."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping

from antigravity.models.liquid_momentum_screener import LiquidMomentumEngine
from antigravity.models.market_regime_filter import MarketRegimeSnapshot
from antigravity.models.session_manifest import _canonical_json, _sha256_bytes


def evaluate_symbol(
    *,
    symbol: str,
    candle_record: Mapping[str, Any],
    baseline: Mapping[str, Any],
    regime: MarketRegimeSnapshot,
    order_rules: Mapping[str, Any],
) -> dict[str, Any]:
    bars = candle_record.get("bars")
    if not isinstance(bars, list) or len(bars) < 2:
        return {"symbol": symbol, "decision": "DATA_INVALID", "paper_instruction": None}
    opening = next((bar for bar in bars if str(bar.get("timestamp", ""))[11:16] == "09:15"), None)
    eligible = [bar for bar in bars if "09:30" <= str(bar.get("timestamp", ""))[11:16] <= "14:30"]
    if opening is None or not eligible:
        return {"symbol": symbol, "decision": "DATA_INVALID", "paper_instruction": None}
    latest = eligible[-1]
    required_baseline = ("historical_bucket_volume_median", "atr14_points", "dtv_med20_cr")
    if any(key not in baseline for key in required_baseline):
        return {"symbol": symbol, "decision": "DATA_INVALID", "paper_instruction": None}
    result = LiquidMomentumEngine.evaluate_15m_orb_breakout(
        symbol=symbol,
        current_price=latest["close"],
        or_high=opening["high"],
        or_low=opening["low"],
        bucket_volume=latest["volume"],
        historical_bucket_volume_median=baseline["historical_bucket_volume_median"],
        atr14_intraday=baseline["atr14_points"],
        min_volume_multiple=float(order_rules.get("min_volume_multiple", 2.5)),
        regime_snapshot=regime,
    )
    if result["signal"] != "RESEARCH_ORB_HYPOTHESIS":
        return {"symbol": symbol, "decision": result["signal"], "paper_instruction": None, "evaluation": result}
    sizing = LiquidMomentumEngine.calculate_position_size(
        entry_price=latest["close"], or_low=opening["low"],
        atr14=baseline["atr14_points"], dtv_med20_cr=baseline["dtv_med20_cr"],
        risk_budget_rs=float(order_rules.get("risk_rs", 1500)),
        order_execution_type="SL_LIMIT",
    )
    if sizing.shares <= 0:
        return {"symbol": symbol, "decision": "SIZING_REJECTED", "paper_instruction": None}
    instruction = {
        "symbol": symbol,
        "side": "BUY",
        "limit_price": round(float(latest["close"]), 2),
        "quantity": sizing.shares,
        "stop_price": sizing.stop_price,
        "stop_limit_price": sizing.limit_exit_price,
        "target_price": sizing.target_price,
        "signal_timestamp": latest["timestamp"],
        "source_raw_sha256": candle_record.get("raw_sha256"),
        "strategy_rules_sha256": _sha256_bytes(_canonical_json(dict(order_rules))),
        "evidence_class": "E1_BAR_POSSIBLE",
        "fill_claimed": False,
    }
    return {
        "symbol": symbol, "decision": "PAPER_SIGNAL", "paper_instruction": instruction,
        "evaluation": result, "sizing": asdict(sizing),
    }
