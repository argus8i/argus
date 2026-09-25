"""
research/backtest/tear_sheet.py
===============================
Self-contained, air-gapped HTML tear sheet renderer with embedded inline SVG charts.
Zero external network calls (no external scripts, styles, or CDNs).
Pure static SVG and CSS.
"""
from __future__ import annotations

import html
import math
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


def _svg_equity_curve(daily_returns: Sequence[float], width: int = 600, height: int = 200) -> str:
    """Generates an inline SVG cumulative equity curve."""
    if not daily_returns:
        return f'<svg width="{width}" height="{height}"><text x="20" y="30" fill="#999">No data</text></svg>'

    cum = [1.0]
    for r in daily_returns:
        cum.append(cum[-1] * (1.0 + r))

    min_val, max_val = min(cum), max(cum)
    val_range = max_val - min_val if max_val > min_val else 1.0

    margin = 30
    w = width - 2 * margin
    h = height - 2 * margin

    points = []
    for i, v in enumerate(cum):
        x = margin + (i / (len(cum) - 1)) * w
        y = margin + (1.0 - (v - min_val) / val_range) * h
        points.append(f"{x:.1f},{y:.1f}")

    poly = " ".join(points)
    return (
        f'<svg width="{width}" height="{height}" style="background:#111;border-radius:4px;">'
        f'<text x="{margin}" y="20" fill="#bbb" font-family="monospace" font-size="12">Cumulative Equity Curve</text>'
        f'<polyline fill="none" stroke="#00e676" stroke-width="2" points="{poly}" />'
        f'</svg>'
    )


def _svg_drawdown_curve(daily_returns: Sequence[float], width: int = 600, height: int = 150) -> str:
    """Generates an inline SVG drawdown chart."""
    if not daily_returns:
        return f'<svg width="{width}" height="{height}"><text x="20" y="30" fill="#999">No data</text></svg>'

    cum = [1.0]
    for r in daily_returns:
        cum.append(cum[-1] * (1.0 + r))

    peaks = [cum[0]]
    for v in cum[1:]:
        peaks.append(max(peaks[-1], v))
    dd = [(v - p) / p for v, p in zip(cum, peaks)]

    min_dd = min(dd) if dd else 0.0
    dd_range = abs(min_dd) if abs(min_dd) > 1e-6 else 1.0

    margin = 30
    w = width - 2 * margin
    h = height - 2 * margin

    points = []
    for i, v in enumerate(dd):
        x = margin + (i / (len(dd) - 1)) * w
        y = margin + (abs(v) / dd_range) * h
        points.append(f"{x:.1f},{y:.1f}")

    poly = " ".join(points)
    return (
        f'<svg width="{width}" height="{height}" style="background:#111;border-radius:4px;">'
        f'<text x="{margin}" y="20" fill="#bbb" font-family="monospace" font-size="12">Drawdown Profile (Underwater)</text>'
        f'<polyline fill="none" stroke="#ff5252" stroke-width="2" points="{poly}" />'
        f'</svg>'
    )


def _svg_pnl_bars(daily_returns: Sequence[float], width: int = 600, height: int = 150) -> str:
    """Generates an inline SVG bar chart of daily returns."""
    if not daily_returns:
        return f'<svg width="{width}" height="{height}"><text x="20" y="30" fill="#999">No data</text></svg>'

    max_abs = max([abs(r) for r in daily_returns] + [1e-6])
    margin = 30
    w = width - 2 * margin
    h = height - 2 * margin
    mid_y = margin + h / 2.0

    bar_width = max(1.0, w / len(daily_returns) - 2)
    bars_svg = []

    for i, r in enumerate(daily_returns):
        x = margin + (i / len(daily_returns)) * w
        bar_h = (abs(r) / max_abs) * (h / 2.0)
        if r >= 0:
            y = mid_y - bar_h
            color = "#00e676"
        else:
            y = mid_y
            color = "#ff5252"
        bars_svg.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{bar_h:.1f}" fill="{color}" />')

    return (
        f'<svg width="{width}" height="{height}" style="background:#111;border-radius:4px;">'
        f'<text x="{margin}" y="20" fill="#bbb" font-family="monospace" font-size="12">Daily Returns</text>'
        f'<line x1="{margin}" y1="{mid_y:.1f}" x2="{width - margin}" y2="{mid_y:.1f}" stroke="#444" stroke-width="1" />'
        + "".join(bars_svg)
        + f'</svg>'
    )


def render_tear_sheet(
    out_path: Path | str,
    title: str,
    portfolio: Mapping[str, Any],
    strategies: Mapping[str, Any],
    notes: Sequence[str] = (),
    gate: Optional[Mapping[str, Any]] = None,
    extra_tables: Optional[Mapping[str, Any]] = None,
) -> Path:
    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    daily_returns = list(portfolio.get("daily_returns", []))

    svg_eq = _svg_equity_curve(daily_returns)
    svg_dd = _svg_drawdown_curve(daily_returns)
    svg_pnl = _svg_pnl_bars(daily_returns)

    # Safe HTML escaping
    esc_title = html.escape(str(title))
    esc_notes = [html.escape(str(n)) for n in notes]

    # Gate status
    gate_status = "UNKNOWN"
    gate_color = "#999"
    gate_reasons = []
    if gate is not None:
        passed = gate.get("passed", False)
        gate_status = "PASSED" if passed else "FAILED"
        gate_color = "#00e676" if passed else "#ff5252"
        gate_reasons = [html.escape(str(r)) for r in gate.get("reasons", [])]

    # Strategies table
    strat_rows = []
    for s_name, s_data in strategies.items():
        summary = s_data.get("summary", {})
        n = summary.get("n", 0)
        mean = summary.get("mean", 0.0)
        strat_rows.append(
            f"<tr><td>{html.escape(s_name)}</td><td>{n}</td><td>{mean:.3f}</td></tr>"
        )
    strat_table_html = "".join(strat_rows)

    doc = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{esc_title}</title>
<style>
body {{ font-family: monospace; background: #0a0a0a; color: #ddd; margin: 20px; }}
h1, h2, h3 {{ color: #eee; }}
.card {{ background: #141414; padding: 15px; border-radius: 6px; margin-bottom: 20px; border: 1px solid #222; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 10px; }}
th, td {{ border: 1px solid #333; padding: 8px; text-align: left; }}
th {{ background: #222; }}
.gate {{ font-size: 16px; font-weight: bold; color: {gate_color}; }}
</style>
</head>
<body>
<h1>{esc_title}</h1>

<div class="card">
  <h2>Track 2 Gate Status: <span class="gate">{gate_status}</span></h2>
  {"<ul>" + "".join(f"<li>{r}</li>" for r in gate_reasons) + "</ul>" if gate_reasons else "<p>No gate violations.</p>"}
</div>

<div class="card">
  <h2>Performance Visualization</h2>
  <div style="display:flex; flex-direction:column; gap:15px;">
    {svg_eq}
    {svg_dd}
    {svg_pnl}
  </div>
</div>

<div class="card">
  <h2>Strategy Breakdown</h2>
  <table>
    <thead><tr><th>Strategy</th><th>Trades (N)</th><th>Mean Net R</th></tr></thead>
    <tbody>{strat_table_html}</tbody>
  </table>
</div>

<div class="card">
  <h2>Audit Notes</h2>
  <ul>{"".join(f"<li>{n}</li>" for n in esc_notes)}</ul>
</div>

</body>
</html>
"""
    out_file.write_text(doc, encoding="utf-8")
    return out_file
