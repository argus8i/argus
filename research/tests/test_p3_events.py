"""
P3.4 NSE events tests. Parsers run on responses recorded from NSE on 25 Sep 2026 (trimmed fixtures);
the client and fetch loop run on a fake session and a fake clock, so no test touches the network.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("pyarrow")

from research.data import nse_events as ne
from research.data.universe_build import eligibility

IST = timezone(timedelta(hours=5, minutes=30))
FIX = Path(__file__).resolve().parent / "fixtures"


def _fx(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ parsers on recorded responses
def test_announcements_keyed_by_dissemination_time():
    rows, issues = ne.parse_announcements(_fx("nse_announcements_20260924.json"))
    assert rows and not issues
    first = rows[0]
    assert first["symbol"] == "MCL" and first["ts_field"] == "exchdisstime"
    assert first["disseminated_at"] == "2026-09-24T23:58:30+05:30"        # exchdisstime, not an_dt (23:58:29)


def test_announcement_without_dissemination_time_falls_back_to_filing_time_and_says_so():
    raw = [{"symbol": "abc", "an_dt": "24-Sep-2026 10:00:01", "exchdisstime": None}, {"symbol": "", "an_dt": "x"}]
    rows, issues = ne.parse_announcements(raw)
    assert rows == [{"symbol": "ABC", "disseminated_at": "2026-09-24T10:00:01+05:30", "ts_field": "an_dt",
                     "seq_id": "", "desc": ""}]
    assert issues == {"DISSEMINATION_TIME_MISSING_USED_AN_DT": 1, "ANNOUNCEMENT_UNPARSEABLE": 1}


def test_board_meetings_keyed_by_intimation_not_meeting_date():
    rows, issues = ne.parse_board_meetings(_fx("nse_board_meetings_20260924.json"))
    assert rows and not issues
    r = rows[0]
    assert r["symbol"] == "AIFL" and r["meeting_date"] == "2026-09-24"
    assert r["intimated_at"] == "2026-09-21T17:34:13+05:30"


def test_corporate_actions_without_broadcast_date_use_the_labelled_assumption():
    rows, issues = ne.parse_corporate_actions(_fx("nse_corporate_actions_20260924.json"))
    assert rows and issues["BROADCAST_DATE_MISSING_ASSUMED"] == len(rows)
    r = next(x for x in rows if x["symbol"] == "ENGINERSIN")
    assert r["ex_date"] == "2026-09-24" and r["announced_basis"] == "ASSUMED_BEFORE_EX_DATE"
    assert r["announced_at"] == "2026-09-24T00:00:00+05:30"
    with_bc, _ = ne.parse_corporate_actions([{"symbol": "X", "exDate": "01-Oct-2026",
                                              "caBroadcastDate": "20-Sep-2026 18:00:00"}])
    assert with_bc[0]["announced_basis"] == "BROADCAST" and with_bc[0]["announced_at"].startswith("2026-09-20T18:00")


def test_ban_csv():
    d, syms = ne.parse_ban_csv((FIX / "nse_fo_secban_24092026.csv").read_text(encoding="utf-8"))
    assert d == date(2026, 9, 24) and syms == ["KAYNES", "LICHSGFIN", "MANAPPURAM", "SAIL"]
    assert ne.parse_ban_csv("Securities in Ban For Trade Date 01-OCT-2026:\n") == (date(2026, 10, 1), [])


# ------------------------------------------------------------------ windows and hashing
def test_windows_and_request_hash_ignore_headers():
    assert ne.windows("fo_ban", date(2026, 9, 25), date(2026, 9, 29)) == [
        (date(2026, 9, 25),) * 2, (date(2026, 9, 28),) * 2, (date(2026, 9, 29),) * 2]
    w = ne.windows("board_meetings", date(2026, 9, 1), date(2026, 9, 20))
    assert w[0] == (date(2026, 9, 1), date(2026, 9, 7)) and w[-1] == (date(2026, 9, 15), date(2026, 9, 20))
    assert len(ne.windows("announcements", date(2026, 9, 1), date(2026, 9, 30))) == 30
    url, params = ne.request_for("announcements", date(2026, 9, 24), date(2026, 9, 24))
    assert params == {"index": "equities", "from_date": "24-09-2026", "to_date": "24-09-2026"}
    assert ne.request_hash(url, params) == ne.request_hash(url, dict(reversed(list(params.items()))))


# ------------------------------------------------------------------ client (fake session and clock)
class _Resp:
    def __init__(self, code, body=b""):
        self.status_code, self.content = code, body


class _Session:
    def __init__(self, script):
        self.headers, self.calls, self.script = {}, [], list(script)

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        if url == ne.BASE + "/":
            return _Resp(200, b"<html>")
        return self.script.pop(0) if self.script else _Resp(200, b"[]")


class _Clock:
    def __init__(self):
        self.t, self.slept = 0.0, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def _client(script, **kw):
    clk = _Clock()
    return ne.NseClient(session=_Session(script), sleep=clk.sleep, clock=clk.now, **kw), clk


def test_client_paces_at_two_seconds_and_refuses_faster():
    c, clk = _client([_Resp(200, b"[]")] * 3)
    for _ in range(3):
        c.get(ne.BASE + "/api/corporate-announcements", {"a": "1"})
    assert c.requests_made == 4                              # warm-up + 3
    assert all(s == pytest.approx(2.0) for s in clk.slept) and len(clk.slept) == 3
    assert "track2-research" in c.s.headers["User-Agent"]
    with pytest.raises(ValueError):
        ne.NseClient(session=_Session([]), min_interval=1.0)


def test_client_stops_on_repeated_401_403():
    c, _ = _client([_Resp(403)] * 5)
    with pytest.raises(ne.NseBlocked):
        c.get(ne.BASE + "/api/corporate-announcements")


def test_client_backs_off_on_429_then_succeeds():
    c, clk = _client([_Resp(429), _Resp(503), _Resp(200, b"[]")])
    assert c.get(ne.ARCHIVE + "/x.csv") == (200, b"[]")
    assert max(clk.slept) >= 4.0


class _Resetting(_Session):
    """Raises ConnectionResetError for the first `n` API calls (what NSE did on 25 Sep after 119 requests)."""

    def __init__(self, n, script=()):
        super().__init__(script)
        self.n = n

    def get(self, url, params=None, timeout=None):
        if url != ne.BASE + "/" and self.n > 0:
            self.n -= 1
            raise ConnectionResetError(10054, "forcibly closed by the remote host")
        return super().get(url, params, timeout)


def test_connection_reset_cools_down_and_retries():
    clk = _Clock()
    c = ne.NseClient(session=_Resetting(1, [_Resp(200, b"[]")]), sleep=clk.sleep, clock=clk.now)
    assert c.get(ne.BASE + "/api/corporate-announcements") == (200, b"[]")
    assert 30.0 in clk.slept                                   # cool-down, not the 2 s pace


def test_repeated_connection_resets_stop_the_run():
    clk = _Clock()
    c = ne.NseClient(session=_Resetting(10), sleep=clk.sleep, clock=clk.now)
    with pytest.raises(ne.NseBlocked, match="transport"):
        c.get(ne.BASE + "/api/corporate-announcements")


# ------------------------------------------------------------------ fetch, resume, build, load
def test_fetch_resume_build_and_point_in_time_tables(tmp_path):
    ann = json.dumps(_fx("nse_announcements_20260924.json")).encode()
    board = json.dumps(_fx("nse_board_meetings_20260924.json")).encode()
    ca = json.dumps(_fx("nse_corporate_actions_20260924.json")).encode()
    ban = (FIX / "nse_fo_secban_24092026.csv").read_bytes()
    day = date(2026, 9, 24)
    c, _ = _client([_Resp(200, ann), _Resp(200, board), _Resp(200, ca), _Resp(200, ban)])
    stats = ne.fetch(list(ne.ALL_KINDS), day, day, client=c, root=tmp_path, log=lambda s: None)
    assert all(v == {"status_200": 1} for v in stats.values())
    man = [json.loads(x) for x in (tmp_path / "raw" / "nse" / "manifest.jsonl").read_text().splitlines()]
    assert len(man) == 4 and all(len(m["request_sha256"]) == 64 and "headers" not in m for m in man)
    # resume: nothing is fetched again
    c2, _ = _client([])
    again = ne.fetch(list(ne.ALL_KINDS), day, day, client=c2, root=tmp_path, log=lambda s: None)
    assert all(v == {"skipped": 1} for v in again.values()) and c2.requests_made == 0

    cov = ne.build(root=tmp_path)
    assert cov["announcements"]["days"] == 1 and cov["announcements"]["n_gaps"] == 0
    ev = ne.load_table_events(root=tmp_path)
    assert ev.coverage == "TABLE"
    start = datetime(2026, 9, 24, 0, 0, 1, tzinfo=IST)
    assert ev.news_since("MCL", start, datetime(2026, 9, 24, 23, 59, tzinfo=IST)) is True    # 23:58:30
    assert ev.news_since("MCL", start, datetime(2026, 9, 24, 23, 58, tzinfo=IST)) is True    # 23:57:03 too
    assert ev.news_since("MCL", start, datetime(2026, 9, 24, 23, 0, tzinfo=IST)) is False
    assert ev.scheduled_event("AIFL", day, datetime(2026, 9, 24, 10, 0, tzinfo=IST)) is True
    assert ev.scheduled_event("AIFL", day, datetime(2026, 9, 21, 17, 0, tzinfo=IST)) is None   # outside cover
    assert ev.ex_date("ENGINERSIN", day, datetime(2026, 9, 24, 9, 30, tzinfo=IST)) is True
    assert ev.news_since("MCL", start, datetime(2026, 9, 25, 10, 0, tzinfo=IST)) is None       # beyond cover
    banned, known = ne.load_fo_ban(root=tmp_path)
    assert banned["SAIL"] == {day} and known == {day}


def test_any_gap_makes_events_unknown(tmp_path):
    body = b"[]"
    script = [_Resp(200, body), _Resp(500), _Resp(500), _Resp(500), _Resp(500), _Resp(200, body)]
    c, _ = _client(script)
    ne.fetch(["announcements"], date(2026, 9, 22), date(2026, 9, 24), client=c, root=tmp_path, log=lambda s: None)
    cov = ne.build(root=tmp_path)
    assert cov["announcements"]["n_gaps"] == 1 and cov["announcements"]["failed_windows"] == ["2026-09-23_2026-09-23"]
    assert ne.load_table_events(root=tmp_path).covered is None        # fail closed


def test_max_requests_stops_cleanly(tmp_path):
    c, _ = _client([_Resp(200, b"[]")] * 10)
    stats = ne.fetch(["announcements"], date(2026, 9, 1), date(2026, 9, 10), client=c, root=tmp_path,
                     max_requests=4, log=lambda s: None)
    assert sum(stats["announcements"].values()) == 3                   # warm-up counts as a request


# ------------------------------------------------------------------ universe: unknown ban day is ineligible
def test_missing_ban_file_is_never_read_as_no_ban():
    days = [date(2026, 8, 1) + timedelta(days=k) for k in range(25)]
    daily = [(d, 100.0, 4_000_000) for d in days]
    rows = eligibility(daily, [days[21], days[22], days[23]], member=True, banned={days[22]},
                       ban_known={days[21], days[22]})
    assert [r["reason"] for r in rows] == ["ELIGIBLE", "FO_BAN", "FO_BAN_UNKNOWN"]
    assert eligibility(daily, [days[23]], member=True)[0]["reason"] == "ELIGIBLE"   # no history: table-level flag
