# -*- coding: utf-8 -*-
"""edverify(벤더 Ed25519) 단위 + 크로스서비스 인터롭 회귀.

핵심: 아래 NODE_* 벡터는 계정측 node:crypto(edsign.ts 와 동일 포맷)가 만든
실제 산출이다. 파이썬이 이를 검증하지 못하면 계정↔MCP 신뢰가 깨진 것 —
CI 가 즉시 잡는다. (역방향은 account/test/edsign.test.ts 의 PY_* 벡터.)
"""
import base64
import time

from vinc_mcp import edverify as ev

# 계정측(Node) 산출 — seed[i]=(i*7+3)&0xff, payload={typ:aa,iss:account,sub:u_abc,plan:pro,...}
NODE_PUB_B64 = "dVxMuSVsp83ErP3Gz+7ahJAX5bn5UU6ZGRvWfgsNQnY="
NODE_ASSERTION = (
    "eyJ0eXAiOiJhYSIsImlzcyI6ImFjY291bnQiLCJzdWIiOiJ1X2FiYyIsInBsYW4iOiJwcm8iLCJpYXQiOjEwMDAsImV4cCI6OTk5OTk5OTk5OX0"
    ".8KrFuLuDewpM5agjD9waTNt4HebU8llzWcKFzIbgeJG0TxcaQmYUjhKA3BRBW1b_EhAhF0jp4DW-KXZsnKs3CQ"
)


def test_verifies_node_produced_assertion():
    pub = base64.b64decode(NODE_PUB_B64)
    payload = ev.verify_assertion(pub, NODE_ASSERTION)
    assert payload is not None
    assert payload["typ"] == "aa" and payload["iss"] == "account"
    assert payload["sub"] == "u_abc" and payload["plan"] == "pro"


def test_wrong_key_rejects_node_assertion():
    other = ev.secret_to_public(b"\x01" * 32)
    assert ev.verify_assertion(other, NODE_ASSERTION) is None


def test_tampered_token_rejected():
    pub = base64.b64decode(NODE_PUB_B64)
    part, sig = NODE_ASSERTION.split(".")
    flipped = sig[:-2] + ("aa" if sig[-2:] != "aa" else "bb")
    assert ev.verify_assertion(pub, part + "." + flipped) is None
    # payload 변조(권한 상승 시도)도 서명 불일치로 거부
    import json
    evil = ev.b64u_encode(json.dumps({"sub": "u_admin", "plan": "pro"}).encode())
    assert ev.verify_assertion(pub, evil + "." + sig) is None


def test_roundtrip_and_seed_to_public_deterministic():
    seed = bytes((i * 3 + 1) & 0xFF for i in range(32))
    tok = ev.sign_assertion(seed, {"typ": "cra", "req": "r1", "exp": int(time.time()) + 60})
    pub = ev.secret_to_public(seed)
    assert ev.secret_to_public(seed) == pub  # 결정적
    payload = ev.verify_assertion(pub, tok)
    assert payload and payload["req"] == "r1" and "iat" in payload


def test_exp_and_max_age_enforced():
    seed = bytes(range(32))
    pub = ev.secret_to_public(seed)
    expired = ev.sign_assertion(seed, {"exp": 1000})
    assert ev.verify_assertion(pub, expired, now=2000) is None
    assert ev.verify_assertion(pub, expired, now=500) is not None
    # max_age: iat 오래되면 exp 없어도 거부
    old = ev.sign_assertion(seed, {"iat": 100})
    assert ev.verify_assertion(pub, old, now=100 + 999, max_age=120) is None
    assert ev.verify_assertion(pub, old, now=100 + 60, max_age=120) is not None


def test_malformed_tokens_return_none():
    pub = ev.secret_to_public(bytes(range(32)))
    for bad in ["", "no-dot", "a.b.c", "!!.!!", "x." + "A" * 10]:
        assert ev.verify_assertion(pub, bad) is None
