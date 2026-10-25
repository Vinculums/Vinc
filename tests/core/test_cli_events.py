"""`vinc events tail`: 한 줄에 이벤트 하나, 커서 이어 읽기, 커서 파일, `--once`.

근거: [[vinc.plan.mcp-events-and-webhooks]]
"""
from __future__ import annotations

import io
import json
from types import SimpleNamespace

from api import cli, cli_events, cli_tools


def _args(**kw):
    base = {"cmd": "events", "eventscmd": "tail", "since": None, "role": None, "kind": None,
            "limit": None, "interval": 30.0, "once": False, "cursor_file": None, "owner": None}
    return SimpleNamespace(**{**base, **kw})


def test_tool_request_maps_the_flags():
    name, data, guard = cli_tools._tool_request(_args(since="3.4", role="agent-x",
                                                      kind=["role.lesson_added"], limit=7))
    assert (name, guard) == ("vinc_events", None)
    assert data == {"since": "3.4", "role": "agent-x", "kinds": ["role.lesson_added"], "limit": 7}
    assert cli_tools._tool_request(_args())[1] == {}


def test_tail_prints_one_line_per_event_and_follows_the_cursor(tmp_path):
    pages = iter([
        {"executed": True, "events": [], "cursor": "5.5", "has_more": False},
        {"executed": True, "events": [{"eventId": "evt_1", "name": "role.lesson_added"},
                                      {"eventId": "evt_2", "name": "role.lesson_added"}],
         "cursor": "6.5", "has_more": True},
        {"executed": True, "events": [{"eventId": "evt_3", "name": "role.lesson_added"}],
         "cursor": "7.5", "has_more": False},
    ])
    args = _args(cursor_file=str(tmp_path / "cursor"))
    seen, slept = [], []

    def call():
        seen.append(args.since)
        return next(pages), None

    def sleep(seconds):
        slept.append(seconds)
        if len(slept) == 2:
            raise KeyboardInterrupt

    out = io.StringIO()
    assert cli_events.tail(args, call, lambda r: 0, sleep=sleep, out=out) == 0
    assert seen == [None, "5.5", "6.5"]                  # 지금부터 → 받은 커서로 이어 읽는다
    assert [json.loads(line)["eventId"] for line in out.getvalue().splitlines()] == [
        "evt_1", "evt_2", "evt_3"]
    assert slept == [30.0, 30.0]                         # has_more 이면 쉬지 않는다
    assert (tmp_path / "cursor").read_text(encoding="utf-8").strip() == "7.5"
    again = _args(cursor_file=str(tmp_path / "cursor"), once=True)
    got = []
    assert cli_events.tail(again, lambda: (got.append(again.since) or
                                           {"executed": True, "events": [], "cursor": "8.5"}, None),
                           lambda r: 0) == 0
    assert got == ["7.5"] and (tmp_path / "cursor").read_text(encoding="utf-8").strip() == "8.5"


def test_tail_stops_on_a_failed_page_and_keeps_the_interval_floor(capsys):
    args = _args(interval=0.1)
    calls = iter([({"executed": True, "events": [], "cursor": "1.1"}, None),
                  ({"executed": False, "error": "invalid_cursor"}, None)])
    slept = []
    assert cli_events.tail(args, lambda: next(calls), lambda r: 0, sleep=slept.append) == 1
    assert slept == [cli_events.MIN_INTERVAL]
    assert "invalid_cursor" in capsys.readouterr().err


def test_main_routes_events_tail_once_to_the_tool(monkeypatch, capsys):
    calls = []

    def run(args):
        calls.append((args.cmd, args.eventscmd, args.since, args.kind))
        return {"kind": "events", "executed": True, "events": [], "cursor": "2.3",
                "has_more": False}, None
    monkeypatch.setattr(cli_tools, "run", run)
    code = cli._main(["events", "tail", "--once", "--since", "0", "--kind", "role.lesson_added",
                      "--target", "local", "--json"])
    assert code == 0
    assert calls == [("events", "tail", "0", ["role.lesson_added"])]
    assert json.loads(capsys.readouterr().out)["cursor"] == "2.3"
