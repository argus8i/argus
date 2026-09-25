"""
research/shadow/feed.py
=======================
Bar feeds for the shadow runner (plan P8.2; Yashu's mandate of 26 Sep 2026). Every feed produces the live
file format that run_day.load_live validates (session_date, interval, data_valid, symbols -> {source_url,
bars}); the source_url host decides the provenance class, so a feed can never smuggle in a forbidden source.

SnapshotReplayFeed  a past session from the (sealed) history, point-in-time: live_day(day, now) keeps only
                    bars that ended by now - 60 s (run_day.load_live), and daily bars are read strictly before
                    the session by LiveDayStore. Used for replay tests.
UpstoxIntradayFeed  the live replacement for the 9-symbol kite.zerodha.com/oms bridge (plan rule 1.2.11):
                    polls the Upstox V2 intraday candle API (1-minute candles, no login) for every symbol and
                    resamples them with upstox_history.resample_15m, the rule that built the history, so live
                    and historical bars are constructed identically. The file is written atomically to
                    shared/track2_liquid/live_candles_track2_upstox.json (it never overwrites the bridge file).
                    Pacing: one request per `min_interval` seconds (default 1.0); a poll of ~190 instruments
                    therefore takes ~3 minutes. ASSUMPTION to verify against Upstox's published limits before
                    lowering it (floor 0.25 s). Three consecutive refusals stop the poll (UpstoxBlocked).
DhanIntradayFeed    refuses: Dhan intraday charts need the paid Dhan Data API, which Yashu declined (25 Sep).
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from research.data import paths
from research.shadow import run_day

IST = timezone(timedelta(hours=5, minutes=30))
REPLAY_SOURCE = "https://api.upstox.com/v2/historical-candle"
INTRADAY_URL = "https://api.upstox.com/v2/historical-candle/intraday/{key}/1minute"
LIVE_FILE_UPSTOX = "live_candles_track2_upstox.json"
MIN_LIVE_INTERVAL_S = 0.25


class SnapshotReplayFeed:
    def __init__(self, store: Any, source_url: str = REPLAY_SOURCE) -> None:
        self.store, self.source_url = store, source_url

    def live_file(self, day: date, symbols: Optional[Sequence[str]] = None) -> bytes:
        syms = list(symbols) if symbols is not None else [s for s in self.store.symbols if day in self.store.sessions(s)]
        return run_day.live_file_from_history(self.store, day, syms, self.source_url)

    def live_day(self, day: date, now: datetime, symbols: Optional[Sequence[str]] = None) -> run_day.LiveDay:
        return run_day.load_live(self.live_file(day, symbols), day, now)


def keys_from_manifest(path: Optional[Path] = None) -> Dict[str, str]:
    """symbol -> Upstox instrument key, from the history's raw/upstox manifest (the keys the history used)."""
    p = Path(path or paths.history_dir() / "raw" / "upstox" / "manifest.jsonl")
    out: Dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            out.setdefault(str(rec["symbol"]), str(rec["instrument_key"]))
    return out


class UpstoxIntradayFeed:
    def __init__(self, keys: Mapping[str, str], client: Any = None, min_interval: float = 1.0,
                 out_path: Optional[Path] = None) -> None:
        from research.data.upstox_history import UpstoxClient

        if min_interval < MIN_LIVE_INTERVAL_S:
            raise ValueError(f"min_interval below {MIN_LIVE_INTERVAL_S}s")
        if client is None:
            client = UpstoxClient(min_interval=max(1.0, min_interval))
            client.min_interval = min_interval          # live polling floor (see module docstring)
        self.keys, self.client = dict(keys), client
        self.out_path = Path(out_path or paths.shared_input(LIVE_FILE_UPSTOX))

    def poll(self, day: date) -> Dict[str, Any]:
        """Fetch every symbol once and write the live file atomically. Returns a summary."""
        from research.data.upstox_history import resample_15m

        doc: Dict[str, Any] = {"session_date": day.isoformat(), "interval": "15minute", "data_valid": True,
                               "credential_serialized": False, "writer": "research/shadow/feed.py UpstoxIntradayFeed",
                               "symbols": {}}
        errors: Counter = Counter()
        for sym, key in sorted(self.keys.items()):
            url = INTRADAY_URL.format(key=key)
            requested_at = datetime.now(IST).isoformat()
            code, body = self.client.get(url)
            if code != 200:
                errors[f"HTTP_{code}"] += 1
                continue
            try:
                candles = json.loads(body)["data"]["candles"]
            except (ValueError, KeyError, TypeError):
                errors["BAD_BODY"] += 1
                continue
            bars, stats = resample_15m(candles)
            bars = [b for b in bars if b["timestamp"][:10] == day.isoformat()]
            doc["symbols"][sym] = {"source_url": url, "instrument_key": key, "requested_at": requested_at,
                                   "raw_sha256": hashlib.sha256(body).hexdigest(), "bars": bars,
                                   "minute_stats": dict(stats)}
        doc["local_write_time"] = datetime.now(IST).isoformat()
        tmp = self.out_path.with_suffix(".json.tmp")
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        os.replace(tmp, self.out_path)                   # readers never see a half-written file
        return {"symbols": len(doc["symbols"]), "errors": dict(errors), "path": str(self.out_path),
                "requests": getattr(self.client, "requests_made", None)}


class DhanIntradayFeed:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("Dhan intraday charts need the paid Dhan Data API, which Yashu declined on "
                                  "25 Sep 2026; use UpstoxIntradayFeed")
