# -*- coding: utf-8 -*-
"""사이드카가 500 으로 답한 요청은 컨테이너 로그에 한 줄을 남겨야 한다.

허브 앞에서 보이는 것은 게이트웨이 오류뿐이라, 사이드카 쪽에 줄이 없으면 원인을 되짚을
길이 없다. 성공한 요청은 남기지 않아도 된다(허브와 같은 규칙: 오류만 남긴다)."""
from __future__ import annotations

import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import server as sidecar  # noqa: E402
from api.auth import SessionAuth  # noqa: E402


class _Store:
    def run(self, *a, **k):
        return []

    def close(self):
        pass


@pytest.mark.xfail(strict=True, reason="사이드카 `_Handler.log_message` 가 비어 있어 500 이 어디에도 남지 "
                   "않는다. 고치는 묶음이 이 표시를 걷는다")
def test_a_500_from_the_sidecar_leaves_a_line_on_stderr(capfd):
    app = sidecar.VincApp(store=_Store(), auth=SessionAuth("t"), quiet_boot=True)

    def boom(_p):
        raise RuntimeError("probe-failure-marker")

    app.read_search = boom          # 라우트 표는 요청마다 인스턴스 속성을 읽는다
    httpd = sidecar.make_server(app)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_address[1]}/search?query=x",
            headers={"Authorization": "Bearer t"})
        try:
            urllib.request.urlopen(req, timeout=10)
            status = 200
        except urllib.error.HTTPError as exc:
            status = exc.code
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=5)
        app.jobs.shutdown(drain=True)
    assert status == 500
    err = capfd.readouterr().err
    assert "/search" in err and "500" in err, f"사이드카가 500 을 아무 데도 남기지 않았다: {err!r}"
