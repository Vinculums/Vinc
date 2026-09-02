# -*- coding: utf-8 -*-
"""어서션이 본문을 묶고 한 번만 쓰이는가(감사 2026-08-18 M2).

어서션은 owner 와 op 를 묶지만 **인자는 서명되지 않은 본문에서 읽힌다.** 그래서 잡은
어서션을 유효 창(120초) 안에 **다른 인자로** 재생할 수 있었다. `/internal/*` 은 Caddy 가
사라진 뒤 서명 하나만 남은 문이라 그 창이 그대로 공격면이다.

2026-09-02: 두 검사가 **필수로 올라갔다.** 처음 들어올 때는 계정이 아직 필드를 안 보낼
수 있어 "없으면 통과"였고 이 파일이 그 관대함을 고정하고 있었는데, 계정이 두 주 넘게
보내고 있고 이 경로를 부르는 다른 소비자가 없어 전제가 만료됐다. 그 두 테스트는
지우지 않고 **거부를 고정하는 쪽으로 바꿔** 사유를 그 자리에 남겼다 — 지워 버리면 왜
관대했었는지가 사라지고, 다음 사람이 같은 걱정을 처음부터 다시 한다."""
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


def test_absent_digest_is_now_refused():
    """이 테스트는 원래 반대를 고정하고 있었다(`..._passes_for_deployment_order`).

    그때의 이유는 옳았다: 계정이 아직 `bh` 를 안 보낼 수 있고, 받는 쪽이 먼저 조이면
    배포 순서가 어긋나는 순간 웹 Vinc 가 통째로 죽는다. **그 전제가 만료됐다.**

      · 계정은 `bc22584`(2026-08-20)부터 네 경로 모두에서 `bh`·`jti` 를 서명한다.
        그 뒤 account-deploy 가 여러 번 성공했다 — 2주 넘게 라이브다.
      · `/internal/*` 세 경로를 부르는 것은 **계정과 이 테스트뿐**이다(전 리포 grep).

    전제가 만료된 뒤에도 통과시키는 것은 느슨한 것이 맞다. `/internal/*` 은 공개 도달
    가능하고 남은 관문이 서명 하나뿐이라(M2), 지문 없는 어서션을 받아 준다는 것은
    잡은 어서션을 유효 창 안에 다른 인자로 재생할 여지를 남기는 것이다."""
    assert MCPHTTPApp._body_matches({}, b'{"anything":1}') is False


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


def test_absent_nonce_is_now_refused():
    """`bh` 와 같은 이유·같은 근거(위 테스트 주석). 여기 남겨 두면 재생 방어가 사실상
    선택이 된다 — `jti` 를 뺀 어서션이 몇 번이고 통과하기 때문이다."""
    app = _App()
    assert app._claim_jti({}) is False
    assert app._claim_jti({}) is False


def test_expired_entries_are_pruned():
    """창이 120초뿐이라 표가 자라면 안 된다."""
    app = _App()
    app._seen_jti = {"old": 0.0}          # 이미 만료된 항목
    assert app._claim_jti({"jti": "new"})
    assert "old" not in app._seen_jti, "만료 항목이 남았다"
