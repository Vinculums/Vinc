# -*- coding: utf-8 -*-
"""`/internal/*` 가 받는 계정 어서션 검사는 `account_assertion.verify` 한 곳에 있다.

경로마다의 답은 `test_internal_assertion_table.py` 가 고정한다. 여기서는 그 표가 기대는 두 가지를
본다: 검사의 차례(앞에서 거절되면 jti 를 태우지 않는다), 그리고 허브가 검사를 다시 경로 안에 따로
적지 않는다는 것.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from vinc_mcp import account_assertion as aa   # noqa: E402
from vinc_mcp import edverify as ev            # noqa: E402

SEED = bytes((i * 13 + 1) & 0xFF for i in range(32))
PUBLIC = ev.secret_to_public(SEED)
RAW = b'{"k":1}'


def _bh(raw: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()


def _sign(**over) -> str:
    now = int(time.time())
    body = {"typ": "purge", "iss": "account", "aud": "mcp", "iat": now, "exp": now + 120,
            "sub": "u1", "bh": _bh(RAW), "jti": "j-1", **over}
    return ev.sign_assertion(SEED, body)


def _verify(token: str, raw: bytes = RAW, *, seen: dict | None):
    once = None if seen is None else (lambda p: aa.claim_jti(seen, p))
    return aa.verify(PUBLIC, token, raw, ("purge",), refusal="refused", once=once)


def test_a_good_assertion_returns_its_payload_and_burns_its_jti():
    seen: dict = {}
    assert _verify(_sign(), seen=seen)["sub"] == "u1"
    assert "j-1" in seen
    with pytest.raises(PermissionError, match="already used"):
        _verify(_sign(), seen=seen)


@pytest.mark.parametrize("over, message", [
    ({"typ": "usage"}, "refused"),
    ({"iss": "mcp"}, "refused"),
    ({"aud": "account"}, "refused"),
    ({"iat": 0, "exp": 1}, "refused"),
    ({"bh": _bh(b"other")}, "signed digest"),
])
def test_an_earlier_refusal_does_not_burn_the_jti(over, message):
    seen: dict = {}
    with pytest.raises(PermissionError, match=message):
        _verify(_sign(**over), seen=seen)
    assert seen == {}
    assert _verify(_sign(), seen=seen)["sub"] == "u1"


def test_without_once_the_jti_is_not_consulted():
    """읽기 경로(`/internal/quota`)의 꼴: 같은 어서션이 두 번 통과하고, jti 가 없어도 통과한다."""
    token = _sign()
    assert _verify(token, seen=None) == _verify(token, seen=None)
    assert _verify(_sign(jti=None), seen=None)["sub"] == "u1"


def test_the_digest_is_still_required_without_once():
    with pytest.raises(PermissionError, match="signed digest"):
        _verify(_sign(bh=None), seen=None)


def test_the_hub_does_not_verify_account_assertions_inline():
    """허브 안에서 `edverify.verify_assertion` 을 직접 부르는 곳은 회원 연결 승인(aa) 하나뿐이다.
    `/internal/*` 의 검사를 경로 안에 다시 적으면 검사 하나가 한 경로에서만 빠질 수 있다."""
    tree = ast.parse((ROOT / "vinc_mcp" / "http_server.py").read_text(encoding="utf-8"))
    callers = set()
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            for node in ast.walk(fn):
                if isinstance(node, ast.Attribute) and node.attr == "verify_assertion":
                    callers.add(fn.name)
    assert callers == {"complete_member_connect"}
