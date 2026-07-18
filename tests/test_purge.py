# -*- coding: utf-8 -*-
"""계정 삭제 캐스케이드(§5.9) — owner 전체 purge 의 계약 3층.

  1. **스키마 층**: purge 대상 라벨이 owner_id 를 가진 라벨 전부(+툼스톤)인가
     — 스키마 SSoT 에서 파생하므로 새 소유 라벨이 자동 커버되는가.
  2. **엔진 층(순수)**: purge_owner 가 owner 를 술어로 박아 라벨별 DETACH DELETE 를
     내는가 / **빈 owner 를 거부**하는가(전삭제 사고 방지). 실엔진 2-소유자 왕복은
     test_embedded_backend.EmbeddedE2E(리눅스 CI) 가 담당 — Windows 임베디드는 못 돈다.
  3. **전송/게이트 층**: MCP /internal/purge-owner 가 **계정 서명** 어서션만 통과시켜
     서명된 `sub` 로만 owner 를 유도하는가(위조·만료·typ 재사용 거부, 스코프 위조 불가).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import query as q                     # noqa: E402
from vinc_mcp import edverify as ev            # noqa: E402
from vinc_mcp.bridge import Bridge             # noqa: E402
from vinc_mcp.http_server import MCPHTTPApp    # noqa: E402

ACCT_SEED = bytes((i * 9 + 4) & 0xFF for i in range(32))


# ── 1) 스키마 층: purge 대상이 소유 라벨 전부인가 ─────────────────────────────
def test_owner_scoped_labels_are_exactly_owner_id_bearing():
    from store.embedded_schema import owner_scoped_labels, _NODE_TABLES

    osl = owner_scoped_labels()
    # Bridge 는 v2(F5)에서 owner_id 를 얻어 편입 — 파생 설계의 실증(스키마에 열을
    # 붙이자 purge 가 자동 커버).
    for lbl in ("Concept", "Episode", "Record", "Entity", "LearnerPattern", "Bridge"):
        assert lbl in osl, f"{lbl} 은 owner_id 를 가지는데 purge 대상에서 빠짐"
    # 구조·파생 노드(owner_id 없음)는 소유로 못 가르므로 대상 아님
    for lbl in ("Domain", "Chunk", "SkillExecution"):
        assert lbl not in osl
    # 파생 계약: 목록의 모든 라벨이 실제 owner_id 열을 가진다(SSoT 교차확인)
    for lbl in osl:
        assert any(c == "owner_id" for c, _ in _NODE_TABLES[lbl]["cols"]), lbl


def test_purge_labels_cover_owner_scoped_plus_tombstone():
    from store.embedded_schema import owner_scoped_labels

    targets = q._purge_labels()
    assert set(owner_scoped_labels()).issubset(set(targets))
    assert "_Tombstone" in targets   # 소유 툼스톤도 함께 정리(owner_id 보유)


# ── 2) 엔진 층: 술어·거부(순수, 어디서든 실행) ────────────────────────────────
class _CapStore:
    """run 을 가로채 (cypher, params) 를 기록. count 질의는 지정 건수를 돌려준다."""

    def __init__(self, counts: dict[str, int]):
        self.counts = counts
        self.calls: list[tuple[str, dict | None]] = []

    def run(self, cypher: str, params: dict | None = None):
        self.calls.append((cypher, params))
        if "count(n)" in cypher:
            label = cypher.split("(n:", 1)[1].split(")", 1)[0]
            return [{"c": self.counts.get(label, 0)}]
        return []


def test_purge_owner_scopes_owner_and_deletes_only_present():
    store = _CapStore({"Concept": 2})            # Concept 만 존재, 나머지 0
    out = q.purge_owner("user:alice", store=store)

    assert out["executed"]
    # 모든 대상 라벨에 owner 술어가 박힌 count 질의가 나갔는가
    for lbl in q._purge_labels():
        assert any(f"MATCH (n:{lbl})" in cy and "n.owner_id = $o" in cy and "count(n)" in cy
                   for cy, _ in store.calls), lbl
    # DETACH DELETE 는 건수>0 인 라벨에만
    deletes = [cy for cy, _ in store.calls if "DETACH DELETE" in cy]
    assert any("(n:Concept)" in cy for cy in deletes)
    assert not any("(n:Episode)" in cy for cy in deletes)   # 0건 → 삭제 질의 없음
    # owner 는 항상 파라미터로만(문자열 인터폴레이션 금지)
    assert all(p == {"o": "user:alice"} for _, p in store.calls)
    assert out["deleted"]["Concept"] == 2 and out["deleted"].get("Episode") == 0
    assert out["total"] == 2


def test_purge_owner_refuses_empty_owner():
    """빈/None owner 는 스토어를 건드리지도 않고 거부(빈 술어 전삭제 사고 방지)."""
    for bad in ("", None):
        store = _CapStore({})
        out = q.purge_owner(bad, store=store)          # type: ignore[arg-type]
        assert not out["executed"] and "without an owner" in out["error"]
        assert store.calls == [], "빈 owner 로는 스토어에 닿으면 안 된다"


# ── 3) 게이트 층: MCP /internal/purge-owner 는 계정 서명만 통과 ───────────────
class _PurgeCapBridge(Bridge):
    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:local")
        self.calls: list[tuple] = []

    def request(self, method, path, params=None, body=None, timeout=60):
        self.calls.append((method, path, body))
        return {"executed": True, "deleted": {"Concept": 3}, "total": 3}


def _purge_assertion(sub="u-1", seed=ACCT_SEED, **over):
    now = int(time.time())
    body = {"typ": "purge", "iss": "account", "aud": "mcp",
            "sub": sub, "iat": now, "exp": now + 120}
    body.update(over)
    return ev.sign_assertion(seed, body)


@pytest.fixture
def app():
    # account_public 을 핀하면 purge 활성. static_token 은 생성자 인증 요건 충족용.
    return MCPHTTPApp(_PurgeCapBridge(), static_token="t",
                      account_public=ev.secret_to_public(ACCT_SEED))


def test_purge_request_proxies_signed_owner(app):
    out = app.purge_owner_request(_purge_assertion(sub="u-1"))
    assert out["executed"]
    method, path, body = app.bridge.calls[0]
    assert (method, path) == ("POST", "/sync/purge-owner")
    assert body == {"owner": "user:u-1"}          # 서명된 sub 에서만 유도


def test_purge_request_rejects_forged_expired_and_typ_reuse(app):
    with pytest.raises(PermissionError):          # 공격자 키 서명
        app.purge_owner_request(_purge_assertion(seed=bytes(32)))
    with pytest.raises(PermissionError):          # 만료
        app.purge_owner_request(_purge_assertion(iat=1, exp=2))
    with pytest.raises(PermissionError):          # typ 위조(AA 를 purge 자리에)
        app.purge_owner_request(_purge_assertion(typ="aa"))
    with pytest.raises(PermissionError):          # iss 위조(우리 자신을 사칭)
        app.purge_owner_request(_purge_assertion(iss="mcp"))
    with pytest.raises(ValueError):               # sub 없음
        app.purge_owner_request(_purge_assertion(sub=""))
    assert app.bridge.calls == [], "거부된 요청은 사이드카로 나가면 안 된다"


def test_purge_disabled_without_account_public():
    app = MCPHTTPApp(_PurgeCapBridge(), static_token="t")   # account_public 미핀
    assert not app.purge_enabled()
    with pytest.raises(PermissionError):
        app.purge_owner_request(_purge_assertion())
