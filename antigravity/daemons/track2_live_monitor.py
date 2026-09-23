"""Read-only local control room for the Track 2 market field test."""

from __future__ import annotations

import argparse
import json
import socket
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from antigravity.models.session_manifest import IST


REPO_ROOT = Path(__file__).resolve().parents[2]
TRACK2_ROOT = REPO_ROOT / "shared" / "track2_liquid"
EXPECTED_SYMBOLS = {"CDSL", "ANGELONE", "SUZLON", "INOXWIND", "IREDA", "RVNL", "COCHINSHIP", "BDL"}


def _read_object(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if path.is_symlink() or not path.is_file():
        return None, "File not created yet"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"Unreadable JSON: {type(exc).__name__}"
    if not isinstance(value, dict):
        return None, "JSON root is not an object"
    return value, None


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(IST)
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=IST) if parsed.tzinfo is None else parsed.astimezone(IST)
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        except ValueError:
            return None


def _age_seconds(timestamp: datetime | None, now: datetime) -> float | None:
    return round((now - timestamp).total_seconds(), 1) if timestamp else None


def _freshness(age: float | None, good: float, warn: float) -> str:
    if age is None or age < -2 or age > warn:
        return "ERROR"
    return "OK" if age <= good else "WARN"


def _stage(name: str, state: str, detail: str, updated: str = "—") -> dict[str, str]:
    return {"name": name, "state": state, "detail": detail, "updated": updated}


def _chrome_available() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 9444), timeout=0.2):
            return True
    except OSError:
        return False


def _latest_bar(record: Any) -> tuple[str, Any, int]:
    if not isinstance(record, dict) or not isinstance(record.get("bars"), list):
        return "—", None, 0
    bars = record["bars"]
    if not bars or not isinstance(bars[-1], dict):
        return "No bar yet", None, len(bars)
    return str(bars[-1].get("timestamp", "—")), bars[-1].get("close"), len(bars)


def build_monitor_state(root: Path = TRACK2_ROOT, *, now: datetime | None = None) -> dict[str, Any]:
    current = (now or datetime.now(IST)).astimezone(IST)
    today = current.date().isoformat()
    depth, depth_error = _read_object(root / "live_depth_track2.json")
    candles, candle_error = _read_object(root / "live_candles_track2.json")
    history, history_error = _read_object(root / "historical_candles_track2.json")
    desk, desk_error = _read_object(root / "paper_desk_status.json")
    surveillance, surveillance_error = _read_object(
        root / "paper_surveillance" / f"nse_surveillance_snapshot_{today}.json"
    )

    dhan_hb, _ = _read_object(root / "dhan_feed_heartbeat.json")
    dhan_active = False
    dhan_detail = ""
    if dhan_hb and isinstance(dhan_hb, dict):
        hb_status = dhan_hb.get("status")
        up_time = _parse_timestamp(dhan_hb.get("updated_at"))
        if up_time and (datetime.now(timezone.utc) - up_time).total_seconds() <= 30.0:
            if hb_status in ("LIVE_STREAMING", "CONNECTED"):
                dhan_active = True
                ticks = dhan_hb.get("ticks_received", 0)
                lat = dhan_hb.get("latency_ms")
                lat_str = f"; latency {lat}ms" if lat is not None else ""
                dhan_detail = f"DhanHQ WebSocket v2 streaming ({ticks} ticks{lat_str})"

    chrome = _chrome_available() if root == TRACK2_ROOT else False
    if dhan_active:
        feed_state = "OK"
        feed_desc = dhan_detail
    elif chrome:
        feed_state = "OK"
        feed_desc = "Chrome debugging port 9444 is reachable"
    else:
        feed_state = "ERROR"
        feed_desc = "Neither DhanHQ WebSocket feed nor Kite Chrome (9444) reachable"

    stages: list[dict[str, str]] = []
    stages.append(_stage("Data feed session", feed_state, feed_desc))

    depth_time = _parse_timestamp((depth or {}).get("local_write_time") or (depth or {}).get("timestamp"))
    depth_age = _age_seconds(depth_time, current)
    depth_state = "ERROR" if depth_error else _freshness(depth_age, 5, 30)
    matches = (depth or {}).get("track2_matches", [])
    stages.append(_stage("Quotes & depth", depth_state,
                         depth_error or f"{len(matches)} Track 2 symbols; age {depth_age}s",
                         depth_time.isoformat() if depth_time else "—"))

    candle_time = _parse_timestamp((candles or {}).get("local_write_time"))
    candle_age = _age_seconds(candle_time, current)
    candle_session = (candles or {}).get("session_date")
    candle_state = "ERROR" if candle_error or candle_session != today else _freshness(candle_age, 45, 100)
    candle_symbols = (candles or {}).get("symbols", {})
    stages.append(_stage("Current 15-minute bars", candle_state,
                         candle_error or (f"Session {candle_session}; {len(candle_symbols)} instruments; age {candle_age}s"),
                         candle_time.isoformat() if candle_time else "—"))

    history_time = _parse_timestamp((history or {}).get("local_write_time"))
    history_age = _age_seconds(history_time, current)
    history_state = "ERROR" if history_error or (history or {}).get("end_date") != today else _freshness(history_age, 360, 600)
    stages.append(_stage("Historical baselines", history_state,
                         history_error or f"{(history or {}).get('start_date', '—')} to {(history or {}).get('end_date', '—')}; age {history_age}s",
                         history_time.isoformat() if history_time else "—"))

    fetched = _parse_timestamp((surveillance or {}).get("fetched_at"))
    eligible = (desk or {}).get("official_nse_eligible_symbols", [])
    preflight_state = "ERROR" if surveillance_error else ("OK" if fetched and fetched.date() == current.date() else "WARN")
    stages.append(_stage("NSE F&O / ASM / GSM", preflight_state,
                         surveillance_error or f"Snapshot present; {len(eligible)} desk-verified eligible symbols",
                         fetched.isoformat() if fetched else "—"))

    desk_time = _parse_timestamp((desk or {}).get("generated_at"))
    desk_age = _age_seconds(desk_time, current)
    desk_ready = (desk or {}).get("feed_ready") is True
    desk_state = "ERROR" if desk_error or not desk_ready else _freshness(desk_age, 15, 45)
    desk_label = str((desk or {}).get("state", "NOT_STARTED"))
    desk_reason = str((desk or {}).get("reason", ""))
    stages.append(_stage("Decision engine", desk_state,
                         desk_error or f"{desk_label}" + (f": {desk_reason}" if desk_reason else ""),
                         desk_time.isoformat() if desk_time else "—"))

    field_dir = root / "field_tests" / today
    candidates = sorted(field_dir.glob("candidate_*.json")) if field_dir.is_dir() else []
    event_count = 0
    recent_events: list[dict[str, Any]] = []
    event_path = field_dir / "events.jsonl"
    if event_path.is_file():
        for line in event_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                event_count += 1
                summary = {
                    "at": event.get("generated_at") or event.get("recorded_at") or "—",
                    "state": event.get("state") or event.get("record_type") or "—",
                    "reason": event.get("reason") or "",
                    "repeat": 1,
                }
                if (recent_events
                        and recent_events[-1]["state"] == summary["state"]
                        and recent_events[-1]["reason"] == summary["reason"]):
                    recent_events[-1]["at"] = summary["at"]
                    recent_events[-1]["repeat"] += 1
                else:
                    recent_events.append(summary)
    stages.append(_stage("Evidence recorder", "OK" if event_count else "WARN",
                         f"{event_count} cycles captured; {len(candidates)} candidates; zero broker orders"))

    decisions_by_symbol: dict[str, Mapping[str, Any]] = {}
    for decision in (desk or {}).get("decisions", []):
        if isinstance(decision, dict) and decision.get("symbol"):
            decisions_by_symbol[str(decision["symbol"])] = decision
    quotes: dict[str, Mapping[str, Any]] = {}
    for item in (depth or {}).get("watchlist", []):
        if isinstance(item, dict) and item.get("symbol"):
            quotes[str(item["symbol"])] = item

    symbols = []
    for symbol in sorted(EXPECTED_SYMBOLS):
        bar_time, close, bar_count = _latest_bar(candle_symbols.get(symbol))
        decision = decisions_by_symbol.get(symbol, {})
        evaluation = decision.get("evaluation", {}) if isinstance(decision.get("evaluation"), dict) else {}
        symbols.append({
            "symbol": symbol,
            "ltp": quotes.get(symbol, {}).get("ltp"),
            "change": quotes.get(symbol, {}).get("change_pct", "—"),
            "bars": bar_count,
            "latest_bar": bar_time,
            "close": close,
            "decision": decision.get("decision", "WAITING"),
            "volume_ratio": evaluation.get("volume_ratio"),
            "candidate": (field_dir / f"candidate_{symbol}.json").is_file(),
        })

    states = [stage["state"] for stage in stages]
    overall = "ERROR" if "ERROR" in states else ("WARN" if "WARN" in states else "OK")
    return {
        "generated_at": current.isoformat(),
        "market_phase": (
            "PRE-MARKET" if current.strftime("%H:%M") < "09:15" else
            "MARKET OPEN" if current.strftime("%H:%M") <= "15:30" else "MARKET CLOSED"
        ),
        "overall": overall,
        "stages": stages,
        "symbols": symbols,
        "eligible": eligible,
        "desk_state": desk_label,
        "desk_reason": desk_reason,
        "event_count": event_count,
        "candidate_count": len(candidates),
        "recent_events": recent_events[-20:][::-1],
    }


HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TRACK2 // LIVE OPS</title><style>
:root{--bg:#090b0a;--text:#d8ded9;--dim:#707a73;--line:#263029;--ok:#75d18b;--warn:#d5ad58;--err:#e06c75;--focus:#a4d4ad}
*{box-sizing:border-box}html{background:var(--bg)}body{margin:0;background:var(--bg);color:var(--text);font:13px/1.45 "Cascadia Mono","JetBrains Mono",Consolas,"Courier New",monospace;font-variant-numeric:tabular-nums}
.terminal{width:100%;min-height:100dvh;padding:14px 18px}.line{white-space:pre-wrap;overflow-wrap:anywhere}.prompt,.OK{color:var(--ok)}.WARN{color:var(--warn)}.ERROR{color:var(--err)}.dim{color:var(--dim)}
.top{display:flex;justify-content:space-between;gap:16px}.rule{height:1px;background:var(--line);margin:8px 0}.section{margin-top:14px}.title{color:var(--focus);font-weight:700;margin-bottom:4px}.summary{display:flex;gap:24px;flex-wrap:wrap}.summary b{font-weight:700;color:var(--text)}
.checks{display:grid;grid-template-columns:minmax(170px,220px) 72px 1fr minmax(180px,260px);column-gap:12px}.check{display:contents}.check>span{padding:3px 0;border-bottom:1px dotted #1c231e}.state{font-weight:700}.updated{color:var(--dim);text-align:right}
.scroll{overflow:auto;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}table{width:100%;border-collapse:collapse;min-width:660px}th,td{text-align:left;padding:4px 8px 4px 0;white-space:nowrap;border-bottom:1px dotted #1c231e}th{color:var(--dim);font-weight:400}td:first-child{color:var(--focus);font-weight:700}.yes{color:var(--ok)}.no{color:var(--dim)}
.event{display:grid;grid-template-columns:minmax(180px,230px) minmax(130px,190px) 1fr;gap:12px;padding:3px 0;border-bottom:1px dotted #1c231e}.reason{color:var(--err);overflow-wrap:anywhere}.empty{color:var(--dim);padding:4px 0}.footer{margin-top:16px;color:var(--dim)}
@media(max-width:760px){.terminal{padding:10px}.top{display:block}.checks{grid-template-columns:125px 60px 1fr}.updated{display:none}.event{grid-template-columns:1fr}.event .dim{display:none}}
</style></head><body><main class="terminal" aria-live="polite">
<div class="top"><div><span class="prompt">yashu@track2</span>:<span class="dim">~/paper-desk</span>$ ./status --watch</div><div id="clock" class="dim">--:--:-- IST</div></div>
<div class="line dim">TRACK 2 / LIQUID MOMENTUM / READ-ONLY FIELD TEST</div><div class="rule"></div>
<div class="summary line"><span>STATE <b id="overall" class="WARN">LOADING</b></span><span>SESSION <b id="phase">—</b></span><span>ELIGIBLE <b id="eligible">0</b>/8</span><span>CYCLES <b id="cycles">0</b></span><span>CANDIDATES <b id="candidates">0</b></span><span>ORDERS <b>0 [LOCKED]</b></span></div>

<section class="section"><div class="title">[SYSTEM CHECKS]</div><div class="checks" id="pipeline"><span class="dim">waiting for status...</span></div></section>

<section class="section"><div class="title">[SYMBOL TAPE]</div><div class="scroll"><table><thead><tr><th>SYMBOL</th><th>LTP</th><th>CHANGE%</th><th>BARS</th><th>LAST BAR</th><th>ENGINE STATE</th><th>VOL x</th><th>CANDIDATE</th></tr></thead><tbody id="symbols"></tbody></table></div></section>

<section class="section"><div class="title">[EVENT LOG / NEWEST FIRST]</div><div id="events"><div class="empty">waiting for events...</div></div></section>
<div class="footer line">poll=2s | mode=paper-only | broker_orders=disabled | a candidate is not a fill</div></main>
<script>
const esc=v=>String(v??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const stageName=n=>({'Kite window':'KITE_DEBUG','Quotes & depth':'QUOTE_DEPTH','Current 15-minute bars':'CANDLES_15M','Historical baselines':'HISTORY_BASE','NSE F&O / ASM / GSM':'NSE_PREFLIGHT','Decision engine':'DECISION_ENGINE','Evidence recorder':'EVIDENCE_LOG'}[n]||n);
const shortTime=v=>{if(!v||v==='—')return '—';const m=String(v).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);return m?`${m[2]}-${m[3]} ${m[4]}:${m[5]}`:v};
async function refresh(){try{const r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw new Error('HTTP '+r.status);const d=await r.json();
overall.textContent=d.overall;overall.className=d.overall;clock.textContent=new Date(d.generated_at).toLocaleTimeString('en-IN',{hour12:false,timeZone:'Asia/Kolkata'})+' IST';phase.textContent=d.market_phase;eligible.textContent=d.eligible.length;cycles.textContent=d.event_count;candidates.textContent=d.candidate_count;
pipeline.innerHTML=d.stages.map((s,i)=>`<div class="check"><span>${String(i+1).padStart(2,'0')} ${esc(stageName(s.name))}</span><span class="state ${esc(s.state)}">[${esc(s.state)}]</span><span>${esc(s.detail)}</span><span class="updated">${esc(shortTime(s.updated))}</span></div>`).join('');
symbols.innerHTML=d.symbols.map(s=>`<tr><td>${esc(s.symbol)}</td><td>${esc(s.ltp)}</td><td>${esc(s.change)}</td><td>${esc(s.bars)}</td><td>${esc(shortTime(s.latest_bar))}</td><td>${esc(s.decision)}</td><td>${esc(s.volume_ratio)}</td><td class="${s.candidate?'yes':'no'}">${s.candidate?'YES':'—'}</td></tr>`).join('');
events.innerHTML=d.recent_events.length?d.recent_events.map(e=>`<div class="event"><span class="dim">${esc(shortTime(e.at))}${e.repeat>1?'  x'+esc(e.repeat):''}</span><b>${esc(e.state)}</b><span class="reason">${esc(e.reason||'—')}</span></div>`).join(''):'<div class="empty">-- no captured events --</div>';
}catch(e){overall.textContent='MONITOR_ERROR';overall.className='ERROR';events.innerHTML=`<div class="reason">${esc(e.message)}</div>`}}
refresh();setInterval(refresh,2000);
</script></body></html>"""


class MonitorHandler(BaseHTTPRequestHandler):
    root = TRACK2_ROOT

    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", HTML.encode())
        elif route == "/api/status":
            raw = json.dumps(build_monitor_state(self.root), allow_nan=False).encode()
            self._send(200, "application/json; charset=utf-8", raw)
        else:
            self._send(404, "text/plain; charset=utf-8", b"Not found")

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Track 2 read-only live control room")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost"} or not 1024 <= args.port <= 65535:
        raise ValueError("monitor must use a local host and non-privileged port")
    server = ThreadingHTTPServer((args.host, args.port), MonitorHandler)
    url = f"http://127.0.0.1:{args.port}/"
    if args.open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    print(f"Track 2 Control Room: {url}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
