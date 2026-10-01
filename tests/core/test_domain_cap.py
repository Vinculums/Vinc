# -*- coding: utf-8 -*-
"""P1 주제 개수 상한 — AP-1 §3.2·§3.3.

지키려는 것:

1. 막는 것은 **새 주제 생성뿐**이다. 기존 주제에 더 쓰는 것·주제 없는 노드는 통과 →
   상한을 이미 넘긴 사용자가 자동 유예된다(데이터 삭제·강제 이관 없음).
2. 거부는 **프래그먼트 통째로**. 새 주제 둘 중 하나만 받으면 재시도가 멱등하지 않다.
3. **기본값이 무제한**이라 동기화 복제·로컬은 코드 변경 없이 면제된다. 이게 뒤집히면
   (기본이 상한) 상한 초과 사용자의 복제가 조용히 깨진다.
4. 허브가 **등급→숫자**를 해석하고 사이드카는 숫자만 받는다. 자격은 클라이언트가
   보낼 수 없다(owner 와 같은 취급).
"""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest                                        # noqa: E402

from api import fragment as frag                     # noqa: E402
from api import query                                # noqa: E402
from api import server as srv                        # noqa: E402
from vinc_mcp import tools                           # noqa: E402
from vinc_mcp.bridge import Bridge                   # noqa: E402
from vinc_mcp.http_server import MCPHTTPApp          # noqa: E402


def _node(nid, domain=None, title=None):
    n = {"id": nid, "type": "concept", "title": title or nid}
    if domain is not None:
        n["domain"] = domain
    return n


@pytest.fixture
def existing(monkeypatch):
    """소유자가 이미 가진 주제 집합을 고정한다(스토어 없이 관문만 검증)."""
    names = {"work", "research", "home"}

    def fake(*, owner=None, cfg=None, store=None):
        return set(names)

    monkeypatch.setattr(query, "domain_names", fake)
    return names


# ── 1. 새 주제만 막는다 ──────────────────────────────────────────────────────
def test_new_topic_beyond_cap_is_refused(existing):
    denial = frag._domain_gate([_node("c:1", "sideproject")],
                               store=object(), owner="user:u1", max_domains=3)
    assert denial and denial["error"] == "domain_limit_reached"
    assert denial["limit"] == 3 and denial["current"] == 3
    assert denial["attempted"] == ["sideproject"]


def test_writing_into_an_existing_topic_always_passes(existing):
    assert frag._domain_gate([_node("c:1", "work")],
                             store=object(), owner="user:u1", max_domains=3) is None


def test_nodes_without_a_topic_always_pass(existing):
    assert frag._domain_gate([_node("c:1"), _node("c:2", "")],
                             store=object(), owner="user:u1", max_domains=1) is None


def test_grandfathered_owner_keeps_working_over_the_cap(existing):
    """이미 3개인데 상한이 2로 내려가도 기존 주제 쓰기는 계속돼야 한다."""
    assert frag._domain_gate([_node("c:1", "work")],
                             store=object(), owner="user:u1", max_domains=2) is None
    # 다만 새 주제는 막힌다
    assert frag._domain_gate([_node("c:1", "brand-new")],
                             store=object(), owner="user:u1", max_domains=2)


def test_room_below_the_cap_allows_a_new_topic(existing):
    assert frag._domain_gate([_node("c:1", "newone")],
                             store=object(), owner="user:u1", max_domains=4) is None


# ── 2. 원자성 ────────────────────────────────────────────────────────────────
def test_two_new_topics_are_rejected_together_not_partially(existing):
    denial = frag._domain_gate([_node("c:1", "alpha"), _node("c:2", "beta")],
                               store=object(), owner="user:u1", max_domains=4)
    assert denial, "3+2 > 4 이므로 통째로 거부돼야 한다"
    assert denial["attempted"] == ["alpha", "beta"]     # 둘 다 보고 — 부분 수용 없음


def test_store_unreachable_does_not_block_writes(monkeypatch):
    """판정 불가(스토어 미도달)를 거부로 바꾸면 장애가 전면 쓰기 중단이 된다."""
    monkeypatch.setattr(query, "domain_names", lambda **kw: None)
    assert frag._domain_gate([_node("c:1", "x")],
                             store=object(), owner="user:u1", max_domains=1) is None


# ── 3. 기본값 = 무제한(동기화 면제의 근거) ───────────────────────────────────
def test_merge_fragment_signature_defaults_to_unlimited():
    import inspect
    sig = inspect.signature(frag.merge_fragment)
    assert sig.parameters["max_domains"].default is None, \
        "기본값이 무제한이 아니면 sync·로컬 경로가 조용히 상한에 걸린다"


def test_sync_apply_delta_does_not_pass_a_cap():
    """동기화 적용 경로가 상한 인자를 넘기지 않는 것을 소스로 고정한다."""
    from api import sync
    src = Path(sync.__file__).read_text(encoding="utf-8")
    body = src[src.index("def apply_delta"):]
    assert "max_domains" not in body, \
        "sync 는 상한을 태우면 안 된다 — 유예된 사용자의 복제가 깨진다"


def test_cap_is_skipped_without_explicit_owner(monkeypatch):
    """owner 미지정(로컬 기본값)은 자격 개념이 없다 — 관문을 아예 타지 않는다."""
    calls = []
    monkeypatch.setattr(query, "domain_names",
                        lambda **kw: calls.append(1) or {"a", "b", "c"})
    out = frag.merge_fragment({"nodes": [_node("c:1", "zzz")]},
                              store=None, max_domains=1)
    assert out["executed"] is False           # store 없음이 먼저 걸린다
    assert calls == []


# ── 3b. 예약 주제는 상한 밖 ─────────────────────────────────────────────────
def test_reserved_agent_topic_is_never_counted(monkeypatch):
    """기본 Agent 팩은 모든 스페이스에 들어간다. 그것이 무료 주제 다섯 중 하나를 먹으면
    제품이 자기 기본값에 세금을 물리는 셈이다 — vinc/agent 는 양쪽 집합에서 빠진다."""
    monkeypatch.setattr(query, "domain_names",
                        lambda **kw: {"work", "research", "home", "vinc/agent"})
    # 예약 주제로의 적재는 상한이 꽉 차 있어도 통과한다
    assert frag._domain_gate([_node("c:1", "vinc/agent")],
                             store=object(), owner="user:u1", max_domains=3) is None
    assert frag._domain_gate([_node("c:1", "vinc/agent/roles")],
                             store=object(), owner="user:u1", max_domains=3) is None
    # 그리고 이미 있는 예약 주제는 current 에 세지 않는다(3, 4 가 아니라)
    denial = frag._domain_gate([_node("c:1", "brand-new")],
                               store=object(), owner="user:u1", max_domains=3)
    assert denial and denial["current"] == 3
    assert frag._domain_gate([_node("c:1", "brand-new")],
                             store=object(), owner="user:u1", max_domains=4) is None


def test_reserved_topic_matches_the_roster_domain():
    """`roles.DOMAIN` 과 `RESERVED_TOPICS` 가 갈리면 프롬프트 표면은 한 주제를 읽고
    상한은 다른 주제를 면제한다 — 두 상수를 한 테스트가 묶는다."""
    from engines import domain_hierarchy as dh
    from vinc_mcp import roles
    assert roles.DOMAIN in dh.RESERVED_TOPICS
    assert dh.is_reserved(roles.DOMAIN) and dh.is_reserved(roles.DOMAIN + "/roles")
    assert not dh.is_reserved("vinc/agentic") and not dh.is_reserved("vinc")
    assert not dh.is_reserved("") and not dh.is_reserved(None)


# ── 4. 허브가 등급을 숫자로 옮긴다 ───────────────────────────────────────────
class _StubBridge(Bridge):
    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:local")
        self.last = None

    def request(self, method, path, params=None, body=None, timeout=60):
        self.last = (method, path, params, body)
        return {"executed": True}


def _app(cap=5):
    return MCPHTTPApp(_StubBridge(), static_token="S", free_max_domains=cap)


def test_free_gets_the_number_and_paid_gets_nothing():
    app = _app(5)
    assert app.entitlements_for("free") == {"max_domains": 5}
    assert app.entitlements_for("team") == {}


def test_cap_disabled_when_unset():
    assert _app(None).entitlements_for("free") == {}


def test_client_cannot_forge_entitlements():
    """자격은 owner 와 같은 취급 — 클라이언트가 보낸 값은 지워진다."""
    b = _StubBridge()
    tools.call("vinc_ingest", {"nodes": [], "_entitlements": {"max_domains": 9999}},
               b, forced_owner="user:u1", entitlements={"max_domains": 5})
    assert b.last[3]["_entitlements"] == {"max_domains": 5}


def test_entitlements_absent_when_not_granted():
    b = _StubBridge()
    tools.call("vinc_ingest", {"nodes": [], "_entitlements": {"max_domains": 1}},
               b, forced_owner="user:u1", entitlements=None)
    assert "_entitlements" not in b.last[3]


def test_only_capped_tools_carry_entitlements():
    b = _StubBridge()
    tools.call("vinc_domains", {}, b, forced_owner="user:u1",
               entitlements={"max_domains": 5})
    assert "_entitlements" not in (b.last[2] or {})


# ── 5. 사이드카는 숫자만 읽고 본문에서 지운다 ────────────────────────────────
def test_sidecar_pops_the_entitlement_key():
    body = {"nodes": [], "_entitlements": {"max_domains": 5}}
    assert srv._pop_max_domains(body) == 5
    assert "_entitlements" not in body      # fragment 로 새어들어가지 않게 제거


@pytest.mark.parametrize("raw", [{}, {"_entitlements": {}}, {"_entitlements": "x"},
                                 {"_entitlements": {"max_domains": "5"}},
                                 {"_entitlements": {"max_domains": True}},
                                 {"_entitlements": {"max_domains": -1}}])
def test_malformed_entitlements_mean_unlimited(raw):
    assert srv._pop_max_domains(dict(raw)) is None
