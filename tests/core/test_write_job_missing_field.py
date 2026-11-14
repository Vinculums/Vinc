# -*- coding: utf-8 -*-
"""쓰기 잡이 읽는 필수 필드가 빠지면 400 이다(500 이 아니다).

잡 안에서 `_Missing` 이 나면 잡 오류로 감싸여 `500 {"error": "_Missing: 'id'"}` 로 나갔다. 필수
필드는 잡에 넣기 전에 확인하므로, 라우터의 늘 쓰는 거절(`400 {"error": "missing field: <키>"}`)이
나가고 잡은 큐에 들어가지 않는다. 미리보기(`dry_run: true`)와 형식이 틀린 `dry_run`·
`preview_digest` 는 지금처럼 그 앞에서 답한다.
"""
from __future__ import annotations

import contextlib
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.auth import SessionAuth  # noqa: E402
from api.server import VincApp, make_server  # noqa: E402

_TOKEN = "test-token-missing-field"
_OWNER = "user:missing-field"


class _EmptyStore:
    """무엇을 물어도 빈 행을 주는 스토어. 잡이 돌면 그 사실이 `runs` 에 남는다."""

    driver_name = "fake"

    def __init__(self):
        self.runs = 0

    def run(self, query, params=None):
        self.runs += 1
        return []

    def close(self):
        pass


@contextlib.contextmanager
def _server(app):
    httpd = make_server(app, host="127.0.0.1", port=0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        app.close()


def _post(base, path, body):
    r = urllib.request.Request(base + path, data=json.dumps(body).encode("utf-8"), method="POST")
    r.add_header("Authorization", f"Bearer {_TOKEN}")
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


@pytest.mark.parametrize("path,body,field", [
    ("/doc/delete", {}, "id"),
    ("/doc/delete", {"owner": _OWNER}, "id"),
    ("/doc/delete", {"owner": _OWNER, "dry_run": False}, "id"),
    ("/doc/extract", {"owner": _OWNER}, "id"),
    ("/doc/extract", {"owner": _OWNER, "apply": True}, "id"),
    ("/domain/rename", {"owner": _OWNER}, "old"),
    ("/domain/rename", {"owner": _OWNER, "new": "b"}, "old"),
])
def test_빠진_필수_필드는_400_이고_잡에_들어가지_않는다(path, body, field):
    store = _EmptyStore()
    app = VincApp(store=store, auth=SessionAuth(_TOKEN))
    with _server(app) as base:
        code, obj = _post(base, path, body)
    assert (code, obj) == (400, {"error": f"missing field: {field}"})
    assert store.runs == 0


@pytest.mark.parametrize("path", ["/doc/delete", "/doc/extract"])
@pytest.mark.parametrize("extra,error", [
    ({"dry_run": "yes"}, "invalid_dry_run"),
    ({"preview_digest": "short"}, "invalid_preview_digest"),
])
def test_형식이_틀린_미리보기_인자는_그_앞에서_답한다(path, extra, error):
    app = VincApp(store=_EmptyStore(), auth=SessionAuth(_TOKEN))
    with _server(app) as base:
        code, obj = _post(base, path, {"owner": _OWNER, **extra})
    assert code == 200 and obj == {"executed": False, "error": error}
