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


# ── --forward: Grok Bot 루틴처럼 확인 절차 없이 POST 만 받는 곳으로 보낸다 ─────────

def test_forward_url_must_be_https_or_loopback():
    assert cli_events.check_forward_url("https://api2.cursor.sh/automations/webhook/r1") is None
    assert cli_events.check_forward_url("http://127.0.0.1:8080/hook") is None
    assert cli_events.check_forward_url("http://localhost/hook") is None
    assert "https" in cli_events.check_forward_url("http://example.com/hook")
    assert "https" in cli_events.check_forward_url("ftp://example.com/hook")
    assert "absolute" in cli_events.check_forward_url("/relative")
    assert "credentials" in cli_events.check_forward_url("https://u:p@example.com/hook")


def test_forward_posts_each_event_then_moves_the_cursor(tmp_path, monkeypatch):
    monkeypatch.setenv(cli_events.FORWARD_TOKEN_ENV, "crsr_test")
    pages = iter([{"executed": True, "events": [{"eventId": "evt_1"}, {"eventId": "evt_2"}],
                   "cursor": "2.2", "has_more": False}])
    args = _args(cursor_file=str(tmp_path / "cursor"), forward="https://hooks.example/r")
    posted = []

    def sleep(_seconds):
        raise KeyboardInterrupt

    out = io.StringIO()
    assert cli_events.tail(args, lambda: (next(pages), None), lambda r: 0, sleep=sleep, out=out,
                           post=lambda url, ev, tok: posted.append((url, ev["eventId"], tok)) or 200) == 0
    assert posted == [("https://hooks.example/r", "evt_1", "crsr_test"),
                      ("https://hooks.example/r", "evt_2", "crsr_test")]
    assert (tmp_path / "cursor").read_text(encoding="utf-8").strip() == "2.2"
    assert len(out.getvalue().splitlines()) == 2


def test_forward_failure_stops_before_the_cursor_moves(tmp_path, capsys):
    (tmp_path / "cursor").write_text("1.1\n", encoding="utf-8")
    args = _args(cursor_file=str(tmp_path / "cursor"), forward="https://hooks.example/r")
    page = {"executed": True, "events": [{"eventId": "evt_1"}, {"eventId": "evt_2"}],
            "cursor": "2.2", "has_more": False}
    statuses = iter([200, 401])
    assert cli_events.tail(args, lambda: (page, None), lambda r: 0, sleep=lambda s: None,
                           post=lambda *a: next(statuses)) == 1
    assert (tmp_path / "cursor").read_text(encoding="utf-8").strip() == "1.1"
    err = capsys.readouterr().err
    assert "forward_failed" in err and "evt_2" in err and "401" in err


def test_forward_once_reports_the_count_and_refuses_a_bad_url(tmp_path, capsys):
    args = _args(once=True, cursor_file=str(tmp_path / "cursor"), forward="https://hooks.example/r")
    seen = []
    page = {"executed": True, "events": [{"eventId": "evt_9"}], "cursor": "3.3"}
    assert cli_events.tail(args, lambda: (page, None), lambda r: seen.append(r) or 0,
                           post=lambda *a: 202) == 0
    assert seen[0]["forwarded"] == 1
    assert (tmp_path / "cursor").read_text(encoding="utf-8").strip() == "3.3"
    bad = _args(once=True, forward="http://example.com/hook")
    assert cli_events.tail(bad, lambda: (_ for _ in ()).throw(AssertionError("no call")),
                           lambda r: 0) == 2
    assert "invalid_forward_url" in capsys.readouterr().err


def test_post_event_sends_json_with_the_token_and_does_not_follow_redirects(monkeypatch):
    import http.server
    import threading

    got = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            got.append((self.path, dict(self.headers), json.loads(body)))
            if self.path == "/moved":
                self.send_response(307)
                self.send_header("Location", "/elsewhere")
            else:
                self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        event = {"eventId": "evt_7", "name": "role.lesson_added", "data": {"role": "agent-x"}}
        assert cli_events.post_event(base + "/hook", event, "crsr_abc") == 200
        assert cli_events.post_event(base + "/moved", event, None) == 307
    finally:
        server.shutdown()
    path, headers, body = got[0]
    assert path == "/hook" and body == event
    assert headers["Authorization"] == "Bearer crsr_abc"
    assert headers["Idempotency-Key"] == "evt_7"
    assert [g[0] for g in got] == ["/hook", "/moved"]          # /elsewhere 로 가지 않았다
    assert "Authorization" not in got[1][1]
    assert cli_events.post_event("http://127.0.0.1:1/none", event, None) == 0
    assert cli_events.post_event("http://127.0.0.1:1/none", event, "bad\r\ntoken") == 0


def test_post_event_to_loopback_ignores_the_environment_proxy(monkeypatch):
    """루프백으로 보낼 때 HTTP_PROXY 를 타면 토큰이 평문으로 프록시 호스트에 간다."""
    import urllib.request
    seen = []

    real = urllib.request.build_opener

    def build(*handlers):
        seen.append([type(h) for h in handlers])
        return real(*handlers)

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setattr(urllib.request, "build_opener", build)
    assert cli_events.post_event("http://127.0.0.1:1/none", {"eventId": "e"}, "crsr_x") == 0
    assert urllib.request.ProxyHandler in seen[0]
    cli_events.post_event("https://hooks.invalid/r", {"eventId": "e"}, None)
    assert urllib.request.ProxyHandler not in seen[1]


def test_main_routes_forward_to_the_poster(monkeypatch, capsys):
    monkeypatch.setattr(cli_tools, "run", lambda args: ({
        "kind": "events", "executed": True, "events": [{"eventId": "evt_5"}], "cursor": "4.4",
        "has_more": False}, None))
    posted = []
    monkeypatch.setattr(cli_events, "post_event", lambda url, ev, tok: posted.append(url) or 200)
    code = cli._main(["events", "tail", "--once", "--forward", "https://h.example/r",
                      "--target", "local", "--json"])
    assert code == 0 and posted == ["https://h.example/r"]
    assert json.loads(capsys.readouterr().out)["forwarded"] == 1
