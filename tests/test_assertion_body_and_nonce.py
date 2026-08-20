# -*- coding: utf-8 -*-
"""어서션이 본문을 묶고 한 번만 쓰이는가(감사 2026-08-18 M2).

어서션은 owner 와 op 를 묶지만 **인자는 서명되지 않은 본문에서 읽힌다.** 그래서 잡은
어서션을 유효 창(120초) 안에 **다른 인자로** 재생할 수 있었다. `/internal/*` 은 Caddy 가
사라진 뒤 서명 하나만 남은 문이라 그 창이 그대로 공격면이다."""
from __future__ import annotations

import base64
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vinc_mcp.http_server import MCPHTTPApp   # noqa: E402


def _bh(raw: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()


class _App(MCPHTTPApp):
    def __init__(self):          # 검사 두 개만 쓰므로 서버를 세우지 않는다
        self._seen_jti = {}


def test_matching_body_passes():
    raw = b'{"query":"mine"}'
    assert MCPHTTPApp._body_matches({"bh": _bh(raw)}, raw)


def test_a_swapped_body_is_refused():
    """이것이 M2 그 자체다 — 같은 어서션에 다른 인자."""
    signed = b'{"query":"mine"}'
    replayed = b'{"query":"everything"}'
    assert MCPHTTPApp._body_matches({"bh": _bh(signed)}, replayed) is False


def test_absent_digest_passes_for_deployment_order():
    """계정이 아직 안 보낼 수 있다. 여기서 막으면 순서가 어긋나는 순간 웹이 죽는다.
    느슨하지 않은 이유: 서명을 위조할 수 없어 공격자는 `bh` 를 **떼어낼 수 없다**."""
    assert MCPHTTPApp._body_matches({}, b'{"anything":1}')


def test_empty_body_is_hashed_not_skipped():
    assert MCPHTTPApp._body_matches({"bh": _bh(b"")}, b"")
    assert MCPHTTPApp._body_matches({"bh": _bh(b"")}, b"{}") is False


def test_an_assertion_is_usable_once():
    app = _App()
    p = {"jti": "abc-123"}
    assert app._claim_jti(p) is True
    assert app._claim_jti(p) is False, "같은 어서션이 두 번 통과했다"


def test_different_assertions_do_not_collide():
    app = _App()
    assert app._claim_jti({"jti": "one"})
    assert app._claim_jti({"jti": "two"})


def test_absent_nonce_passes_for_deployment_order():
    app = _App()
    assert app._claim_jti({}) is True
    assert app._claim_jti({}) is True


def test_expired_entries_are_pruned():
    """창이 120초뿐이라 표가 자라면 안 된다."""
    app = _App()
    app._seen_jti = {"old": 0.0}          # 이미 만료된 항목
    assert app._claim_jti({"jti": "new"})
    assert "old" not in app._seen_jti, "만료 항목이 남았다"
