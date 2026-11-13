# -*- coding: utf-8 -*-
"""사이드카가 500 으로 답한 요청은 컨테이너 로그에 한 줄을 남겨야 한다.

허브 앞에서 보이는 것은 게이트웨이 오류뿐이라, 사이드카 쪽에 줄이 없으면 원인을 되짚을
길이 없다. 성공과 4xx 는 남기지 않는다. 줄에는 메서드, 쿼리를 뗀 경로, 상태, 걸린 시간만
싣는다: 쿼리스트링·헤더·본문에는 owner 와 토큰이 실릴 수 있다."""
from __future__ import annotations

import re
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import server as sidecar  # noqa: E402
from api.auth import SessionAuth  # noqa: E402


class _Store:
    def run(self, *a, **k):
        return []

    def close(self):
        pass


TOKEN = "sidecar-secret-token-marker"


def _serve(app):
    httpd = sidecar.make_server(app)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, t


def _get(httpd, path, token=TOKEN):
    req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}{path}",
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def _stop(app, httpd, t):
    httpd.shutdown()
    httpd.server_close()
    t.join(timeout=5)
    app.jobs.shutdown(drain=True)


def test_a_500_from_the_sidecar_leaves_a_line_on_stderr(capfd):
    app = sidecar.VincApp(store=_Store(), auth=SessionAuth(TOKEN), quiet_boot=True)

    def boom(_p):
        raise RuntimeError("probe-failure-marker")

    app.read_search = boom          # 라우트 표는 요청마다 인스턴스 속성을 읽는다
    httpd, t = _serve(app)
    try:
        status = _get(httpd, "/search?query=query-marker&owner=user:owner-marker")
    finally:
        _stop(app, httpd, t)
    assert status == 500
    err = capfd.readouterr().err
    assert "/search" in err and "500" in err, f"사이드카가 500 을 아무 데도 남기지 않았다: {err!r}"
    lines = [ln for ln in err.splitlines() if "/search" in ln]
    assert len(lines) == 1, err
    assert re.fullmatch(r'\[sidecar\] "GET /search" 500 \d+ms', lines[0]), lines[0]
    # 쿼리스트링·토큰·응답 본문(예외 문장)은 줄에 싣지 않는다.
    for secret in ("query-marker", "owner-marker", TOKEN, "probe-failure-marker"):
        assert secret not in err, (secret, err)


def test_successes_and_client_errors_leave_no_line(capfd):
    app = sidecar.VincApp(store=_Store(), auth=SessionAuth(TOKEN), quiet_boot=True)
    app.read_search = lambda _p: (200, {"ok": True})
    httpd, t = _serve(app)
    try:
        codes = (_get(httpd, "/search?query=x"), _get(httpd, "/no-such-route"),
                 _get(httpd, "/search", token="wrong"))
    finally:
        _stop(app, httpd, t)
    assert codes == (200, 404, 401)
    assert "[sidecar]" not in capfd.readouterr().err


def _read_line(line: str) -> tuple[str, int]:
    """로그를 읽는 쪽이 하듯 줄을 푼다: 따옴표 칸은 역슬래시 이스케이프(\\" 와 \\\\)만 허용한다.
    (풀어 낸 "메서드 경로", 상태) 를 돌려주고, 칸이 그 꼴로 닫히지 않으면 실패한다."""
    m = re.fullmatch(r'\[sidecar\] "((?:[^"\\]|\\["\\])*)" (\d{3}) (?:\d+ms|-)', line)
    assert m, f"따옴표 칸이 모호하다: {line!r}"
    return re.sub(r'\\(["\\])', r"\1", m.group(1)), int(m.group(2))


def test_a_quote_or_backslash_in_the_path_keeps_the_line_parseable(capfd):
    app = sidecar.VincApp(store=_Store(), auth=SessionAuth(TOKEN), quiet_boot=True)

    def boom(*_a, **_k):
        raise RuntimeError("probe-failure-marker")

    app.get_job = boom
    # 마지막 경로는 정확히 200자이고 200자째가 역슬래시다: 자르기 전에 이스케이프하면 반쪽만 남는다.
    paths = ['/jobs/a"b', "/jobs/a\\", '/jobs/a\\"b"c', "/jobs/" + "a" * 193 + "\\"]
    assert len(paths[-1]) == 200
    httpd, t = _serve(app)
    try:
        codes = [_get(httpd, p + "?owner=user:owner-marker") for p in paths]
    finally:
        _stop(app, httpd, t)
    assert codes == [500] * len(paths), codes
    err = capfd.readouterr().err
    lines = [ln for ln in err.splitlines() if ln.startswith("[sidecar]")]
    assert [_read_line(ln) for ln in lines] == [(f"GET {p}", 500) for p in paths], lines
    for secret in ("owner-marker", TOKEN, "probe-failure-marker"):
        assert secret not in err, (secret, err)
