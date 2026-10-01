# -*- coding: utf-8 -*-
"""테마 어휘의 소유자 스코프(감사 2026-08-18 M1).

테마 파일에는 소유 열이 없다. 강제 owner 경로에서 클라이언트가 theme_id 를 고를 수
있으면 **남의 어휘에 자기 타입이 proposed 로 꽂힌다.** 웹 평면은 이미 그 값을 벗기고
있었는데, 인증된 MCP 경로가 그 자리를 안 지나서 그대로 통과했다.

`tools.call` 을 통과시킨다 — 고친 자리가 거기다. `merge_fragment` 만 직접 부르면
정작 뚫려 있던 경로를 안 지나므로 이 결함을 재현하지 못한다."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from store.theme_store import owner_theme_id   # noqa: E402
from vinc_mcp import tools                     # noqa: E402
from vinc_mcp.bridge import Bridge             # noqa: E402

A = "user:aaa"
B = "user:bbb"


class _StubBridge(Bridge):
    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:local")
        self.body = None

    def request(self, method, path, params=None, body=None, timeout=60):
        self.body = body
        return {"executed": True, "merged": {"nodes": {"created": 1, "matched": 0}}}


def _ingest(args, **kw):
    b = _StubBridge()
    tools.call("vinc_ingest", dict(args), b, **kw)
    return b.body


def test_client_theme_is_replaced_by_the_owners_own():
    sent = _ingest({"nodes": [{"id": "gadget:x", "type": "gadget", "title": "t"}],
                    "theme_id": "someone-elses"}, forced_owner=A)
    assert sent["theme_id"] == owner_theme_id(A)
    assert sent["theme_id"] != "someone-elses", "남의 테마가 그대로 실려 갔다"
    assert sent["owner"] == A


def test_two_tenants_get_different_themes():
    """theme_id 를 안 실어도 예전에는 활성 테마 하나에 함께 고였다."""
    a = _ingest({"nodes": [{"id": "n:1", "type": "alpha", "title": "t"}]}, forced_owner=A)
    b = _ingest({"nodes": [{"id": "n:1", "type": "beta", "title": "t"}]}, forced_owner=B)
    assert a["theme_id"] != b["theme_id"], "두 테넌트가 한 어휘에 고인다"


def test_local_path_keeps_the_clients_theme():
    """forced_owner 없음(로컬 stdio) — 단일 사용자라 자기가 고른 테마가 맞다.
    여기까지 바꾸면 기존 기기의 어휘가 통째로 안 보이게 된다."""
    sent = _ingest({"nodes": [{"id": "n:1", "type": "widget", "title": "t"}],
                    "theme_id": "mine"})
    assert sent["theme_id"] == "mine"


def test_owner_theme_id_is_deterministic_and_separates():
    assert owner_theme_id(A) == owner_theme_id(A)
    assert owner_theme_id(A) != owner_theme_id(B)
    assert A not in owner_theme_id(A), "계정 식별자가 테마 이름으로 눕는다"
