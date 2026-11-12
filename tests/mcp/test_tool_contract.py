# -*- coding: utf-8 -*-
"""MCP 도구 계약 — 호스트 모델이 **읽을 수 있는** 형태로 적혀 있는가.

도구 스키마와 설명은 주석이 아니라 계약이다. 모델은 이 문자열만 보고 도구를 고르고
인자를 채운다. 그래서 설명에만 적힌 범위(예전 vinc_path 의 "Max path length (1–8)")는
계약이 아니었다 — 검증은 서버에서만 일어나고 모델은 8을 넘겨 실패하고서야 알았다.

여기서 지키는 것 셋:

1. **F8** 수치 인자는 전부 `minimum`·`maximum` 을 스키마로 선언한다. 기본값은 그 범위
   안에 있어야 한다(호스트가 기본값을 그대로 넣었다가 거절당하는 일이 없도록).
2. **F6** 결과를 자르는 도구는 상한을 인자로 열고, 잘렸다는 사실을 반환에 싣는다.
   `vinc_diagnose` 는 `member_limit` 을 받아 사이드카까지 전달해야 한다.
3. **F12** 설명은 "무엇을 하나"로 끝나면 안 된다 — 언제 부르나, 무엇이 돌아오나,
   무엇이 아닌가(가까운 다른 도구와의 경계)까지 적힌다.

설명은 세 층에 나뉜다: 내보내는 짧은 요약(고르는 데 필요한 것과 지우면 안 되는 약속),
응답이 싣는 다음 할 일(`next_step`·`hint`·`answer_note`), 내보내지 않는 참조문(`reference`, 문서
사이트가 그린다). 문구 검사는 그 문구가 실제로 실리는 층에 건다.
근거: [[decision:mcp-tool-descriptions-are-short-summaries-with-a-docs-link]]
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vinc_mcp.bridge import Bridge                   # noqa: E402
from vinc_mcp.rpc import _tool_specs                  # noqa: E402
from vinc_mcp.tools import TOOLS                     # noqa: E402

#: 호스트가 tools/list 로 **실제로 받는** 스펙. 한때 rpc 에 description 을
#: 통째로 갈아치우는 override 계층이 있어서 tools.py 를 고쳐도 안 닿았다 —
#: 설명 계약은 반드시 이 경로로 검사한다.
SERVED = {t['name']: t for t in _tool_specs()}
#: 허브 프로파일. 계정 전용 커뮤니티 도구 둘은 여기서만 나간다(로컬 목록에는 없다).
HUB = {t['name']: t for t in _tool_specs(hub=True)}
#: 내보내지 않는 전체 계약(참조문). 문서 사이트가 도구별 자리에 그린다.
from vinc_mcp.tools import HOST_TOOLS                # noqa: E402
REF = {t["name"]: t["reference"] for t in TOOLS + HOST_TOOLS}

NUMERIC = ("integer", "number")


def _params(tool):
    return (tool.get("inputSchema") or {}).get("properties") or {}


def _numeric_params():
    for t in TOOLS:
        for name, spec in _params(t).items():
            if spec.get("type") in NUMERIC:
                yield t["name"], name, spec


# ── 1. F8 — 수치 인자의 범위는 스키마에 있다 ─────────────────────────────────
@pytest.mark.parametrize("tool,param,spec", list(_numeric_params()),
                         ids=lambda v: v if isinstance(v, str) else "")
def test_numeric_params_declare_bounds(tool, param, spec):
    assert spec.get("minimum") is not None, f"{tool}.{param}: minimum 없음"
    assert spec.get("maximum") is not None, f"{tool}.{param}: maximum 없음"
    assert spec["minimum"] <= spec["maximum"], f"{tool}.{param}: min>max"


@pytest.mark.parametrize("tool,param,spec", list(_numeric_params()),
                         ids=lambda v: v if isinstance(v, str) else "")
def test_numeric_defaults_are_inside_their_own_bounds(tool, param, spec):
    d = spec.get("default")
    if d is None or spec.get("minimum") is None or spec.get("maximum") is None:
        return                      # 범위 결손은 앞 테스트가 보고한다
    assert spec["minimum"] <= d <= spec["maximum"], \
        f"{tool}.{param}: 기본 {d} 가 {spec['minimum']}~{spec['maximum']} 밖"


def test_bounds_are_not_merely_prose():
    """범위가 설명 문장에만 있고 스키마에 없으면 계약이 아니다(vinc_path 실사고)."""
    for tool, param, spec in _numeric_params():
        desc = spec.get("description") or ""
        if "–" in desc or "~" in desc:
            assert spec.get("maximum") is not None, \
                f"{tool}.{param}: 설명엔 범위가 있는데 스키마엔 없다"


# ── 2. F6 — 자르는 도구는 상한을 열고 잘림을 말한다 ──────────────────────────
def test_diagnose_exposes_member_limit():
    spec = _params([t for t in TOOLS if t["name"] == "vinc_diagnose"][0])
    assert "member_limit" in spec, "vinc_diagnose 가 상한을 인자로 안 연다"
    assert spec["member_limit"]["type"] == "integer"


def test_diagnose_documents_the_truncation_contract():
    """모델이 size ≠ len(members) 를 버그로 읽지 않으려면 계약에 적혀 있어야 한다. 응답은
    잘렸을 때 members_truncated:true 를 싣고, 그 뜻은 참조문이 말한다."""
    desc = REF["vinc_diagnose"]
    assert "members_truncated" in desc
    assert "member_limit" in desc


class _RecordingBridge(Bridge):
    """사이드카로 나가는 params 를 그대로 잡아 둔다."""

    def __init__(self):
        super().__init__(base="http://127.0.0.1:1", token="t", owner="user:local")
        self.seen = None

    def request(self, method, path, params=None, body=None, timeout=60):
        self.seen = {"method": method, "path": path, "params": params or {}}
        return {"executed": True}


def _run(name, args):
    bridge = _RecordingBridge()
    [t for t in TOOLS if t["name"] == name][0]["run"](args, bridge)
    return bridge.seen


def test_member_limit_reaches_the_sidecar():
    seen = _run("vinc_diagnose", {"kind": "communities", "member_limit": 500})
    assert seen["params"].get("member_limit") == 500


def test_member_limit_is_omitted_when_unset():
    """미지정이면 서버 기본(50)이 이긴다 — 도구가 제 값을 지어내 덮지 않는다."""
    seen = _run("vinc_diagnose", {})
    assert "member_limit" not in seen["params"]


def test_historical_reaches_the_sidecar_and_is_silent_when_unset():
    """유효구간 스위치. 응답의 validity_note 가 "pass historical=true" 라고 안내하는데
    도구가 그 인자를 떨어뜨리면 MCP 에이전트는 따를 길이 없다(라이브 재현 2026-09-11).
    미지정이면 보내지 않는다 — 기본(current 만)은 사이드카가 정한다."""
    for name in ("vinc_search", "vinc_brief"):
        seen = _run(name, {"query": "q", "historical": True})
        assert seen["params"].get("historical") == "true", name
        seen = _run(name, {"query": "q"})
        assert not seen["params"].get("historical"), name


# ── 3. F12 — 설명은 선택을 가능하게 한다 ─────────────────────────────────────
#
# 길이는 대리 지표일 뿐이지만, 71자짜리 한 줄("Subgraph around a seed node")로는
# 모델이 vinc_graph 와 vinc_neighbors 중 무엇을 부를지 못 고른다. 실제로 못 골랐다.
THIN_BEFORE = ("vinc_graph", "vinc_suggest_ties", "vinc_domains", "vinc_recall", "vinc_decide")


@pytest.mark.parametrize("name", THIN_BEFORE)
def test_previously_thin_descriptions_now_carry_guidance(name):
    """요약은 짧아도 전체 계약(무엇이 돌아오나)은 참조문에 남아 문서 사이트로 간다."""
    desc = REF[name]
    assert len(desc) >= 250, f"{name}: 참조문이 {len(desc)}자 — 계약이 없다"
    assert "Returns" in desc, f"{name}: 무엇이 돌아오는지 안 적혀 있다"


@pytest.mark.parametrize("name", THIN_BEFORE)
def test_read_only_tools_say_so(name):
    """쓰기 도구와 헷갈리면 모델이 조회에도 확인을 구하거나, 반대로 겁 없이 쓴다. 조회라는
    사실은 호스트가 받는 주석(readOnlyHint)이 싣는다."""
    assert SERVED[name]["annotations"].get("readOnlyHint") is True


def test_every_tool_has_a_description():
    missing = [n for n, t in SERVED.items() if not (t.get("description") or "").strip()]
    assert not missing, f"설명 없는 도구: {missing}"


def test_served_descriptions_are_exactly_the_canonical_ones():
    """tools.py 가 정본이라면, tools/list 가 내는 설명은 그것과 **글자 그대로** 같아야
    한다. rpc 의 override 계층이 recall·domains 를 조용히 갈아치우고 있었다 — 그래서
    tools.py 를 고쳐도 호스트는 옛 문구를 봤다."""
    from vinc_mcp.tools import served_description
    names = {t["name"] for t in TOOLS}
    assert names <= set(HUB), "허브 목록에 빠진 도구가 있다"
    drift = [n for n, t in {**HUB, **SERVED}.items() if t["description"] != served_description(n)]
    assert not drift, f"tools.py 와 tools/list 설명이 다른 도구: {drift}"


def test_summaries_are_short_and_link_their_own_anchor():
    """내보내는 설명은 요약 한 문단(SUMMARY_MAX 이하)과 그 도구의 Docs 링크다. 링크가 다른 도구의
    자리를 가리키면 사람이 엉뚱한 계약을 읽는다."""
    from vinc_mcp.tools import DOCS_BASE, SUMMARY_MAX, _SUMMARY
    for name, spec in HUB.items():
        summary, _, link = spec["description"].partition("\n\n")
        assert summary == _SUMMARY[name]
        assert len(summary) <= SUMMARY_MAX, f"{name}: 요약 {len(summary)}자"
        assert link == "Docs: " + DOCS_BASE + "#" + name, f"{name}: {link!r}"


def test_every_tool_keeps_its_full_contract_as_a_reference():
    """요약으로 줄인 만큼의 계약이 사라지지 않았는가. 참조문은 요약보다 짧을 수 없다."""
    from vinc_mcp.tools import _SUMMARY
    for name in HUB:
        assert REF[name].strip(), f"{name}: 참조문이 없다"
        assert len(REF[name]) >= len(_SUMMARY[name]), f"{name}: 참조문이 요약보다 짧다"


def test_long_parameter_text_is_not_served():
    """공유 인자의 긴 설명(`x-reference`)은 참조문에만 남고 tools/list 에는 실리지 않는다."""
    import json
    assert "x-reference" not in json.dumps(list(HUB.values()))
    assert "x-reference" not in json.dumps(list(SERVED.values()))


# ── 4. 계약이 서버 **동작**과 일치하는가 (교차 계약) ─────────────────────────
# 위 1~3 은 설명이 **존재하고 형식이 맞는지**만 잰다. 그것으로는 v12(Decision)
# 스키마 확장 때 네 자리가 "concept→concept" 인 채 남은 것을 못 잡았다: 문장은
# 멀쩡히 있었고 길이도 넉넉했으며 Returns 도 적혀 있었는데, **틀렸을 뿐이다.**
#
# test_decision_type.py 가 남긴 교훈("함수 하나를 고쳤는지가 아니라 사용자가 쓰는
# 표면에서 결정이 실제로 나오는지가 계약이다")은 엔진 계층에만 제도화돼 있었다 —
# 그 파일은 api.query 를 직접 부르고 vinc_mcp 를 한 번도 임포트하지 않는다.
# 여기가 그 교훈을 **모델이 읽는 계층**에 적용하는 자리다.

def test_allowed_pairs_mirror_matches_the_server(monkeypatch=None):
    """`vinc_mcp` 는 `api` 를 임포트하지 않으므로(독립 브리지 계약) 허용 쌍 문장이
    두 벌 있다. 두 사본이 갈리면 어시스턴트는 서버가 거부할 엣지를 쓰고 warnings +
    partial:true 로 조용히 반쯤 성공한다 — 계약이 대안을 말하지 않으니 스스로 고칠
    근거도 없다. 커서 코덱 미러(test_search_pagination.py)와 같은 방식으로 고정한다."""
    from api.fragment import _ALLOWED_PAIRS_HINT as server_hint
    from vinc_mcp.tools import _ALLOWED_PAIRS_HINT as mcp_hint
    assert mcp_hint == server_hint, (
        "허용 쌍 문장이 갈렸다 — api/fragment.py 가 정본이다.\n"
        f"  server: {server_hint!r}\n  mcp   : {mcp_hint!r}")


@pytest.mark.parametrize("name", ["vinc_ingest"])
def test_edge_contract_reaches_the_host(name):
    """미러를 만들어 두고 계약에 안 쓰면 아무것도 달라지지 않는다. 허용 쌍 전문은 참조문에,
    호스트가 받는 쪽에는 엣지 `type` 인자 설명(아래 검사)과 거절 때 warnings 의 같은 문장이 있다."""
    from vinc_mcp.tools import _ALLOWED_PAIRS_HINT
    assert _ALLOWED_PAIRS_HINT in REF[name], f"{name}: 허용 쌍이 참조문에 없다"


@pytest.mark.parametrize("name,field", [("vinc_ingest", "type")])
def test_fragment_edge_schema_admits_decision(name, field):
    """`_FRAGMENT_SCHEMA` 의 엣지 `type` 설명이 결정 끝점을 말하는가. 이 자리가
    v12 이후 가장 오래 틀려 있었다."""
    edges = (SERVED[name]["inputSchema"]["properties"]["edges"]["items"]["properties"])
    assert "decision" in edges[field]["description"], \
        "엣지 type 설명이 아직 concept→concept 만 말한다"


def test_node_type_routing_lists_every_dedicated_table():
    """전용 테이블로 가는 타입 목록이 서버의 실제 라우팅과 같은가. 한때 `:38` 은
    여섯을 세고 `vinc_ingest` 설명은 다섯을 세어 **같은 파일 안에서 모순**이었다."""
    from vinc_mcp.tools import _FRAGMENT_SCHEMA
    node_type_desc = (_FRAGMENT_SCHEMA["properties"]["nodes"]["items"]
                      ["properties"]["type"]["description"])
    ingest_desc = REF["vinc_ingest"]
    for t in ("record", "concept", "domain", "bridge", "episode", "decision"):
        assert t in node_type_desc, f"노드 type 설명에 {t} 가 없다"
        assert t in ingest_desc, f"vinc_ingest 참조문의 라우팅 목록에 {t} 가 없다"


def test_docstring_tool_count_matches_the_list():
    """숫자를 손으로 쓰면 어긋난다 — F10-b 로 merge_reject 가 늘었을 때 독스트링은
    19 인 채로 남았고, "아래 TOOLS 가 정본"이라는 방어 문장을 단 채로 틀렸다."""
    import re
    from vinc_mcp import tools as _t
    m = re.search(r"\*\*(\d+)종\*\*", _t.__doc__ or "")
    assert m, "독스트링에서 도구 수를 못 찾았다"
    assert int(m.group(1)) == len(TOOLS), f"독스트링 {m.group(1)}종 vs TOOLS {len(TOOLS)}개"
    # 로컬 목록은 계정 전용 도구 둘을 뺀 나머지, 허브 목록은 레지스트리 전부를 낸다.
    assert len(SERVED) == len(TOOLS) - len(_t._ACCOUNT_ONLY_TOOLS), (
        f"로컬 서빙 {len(SERVED)}개 vs TOOLS {len(TOOLS)}개")
    assert {t["name"] for t in TOOLS} <= set(HUB)


# ── 정체 각인(2026-08-18) — "어느 그래프에 썼는가"가 응답에 있는가 ──────────────
# 이것이 있는 이유는 실제 사고다: 대량 적재에서 owner 를 빠뜨려 전량이 `user:local`
# 로 들어갔는데, **그 tenant 안에서는 읽기도 성공해서** 검증이 통과했다(2026-08-07).
# 별도 `whoami` 를 만들지 않은 판단도 같은 자리에서 나왔다 — 부를 생각을 못 한 사람
# 에게 물어봐야 아는 도구는 소용이 없으므로, 정체를 **첫 문과 쓰기 응답**에 실었다.
def test_writes_and_conventions_carry_the_target_back():
    """`call()` 이 정체 도구 셋에 `target` 을 얹는가. 얹지 않으면 원격에 쓴 것과
    로컬에 쓴 것이 응답만 봐서 구분되지 않는다."""
    from vinc_mcp import tools as T

    seen: dict = {}

    class _FakeBridge:
        owner = "user:local"

        def request(self, *a, **k):
            seen["called"] = True
            return {"executed": True, "owner": "user:from-server"}

    for name in ("vinc_conventions", "vinc_ingest", "vinc_doc_put"):
        assert name in T._IDENTITY_TOOLS, f"{name} 이 정체 도구 목록에서 빠졌다"

    out = T.call("vinc_conventions", {}, _FakeBridge())
    assert out["target"] == "local"
    # **서버가 실은 owner 를 덮지 않는다.** 클라이언트가 아는 값으로 되울리면
    # 호스티드에서 거짓말이 된다(키가 owner 를 고정하고 인자는 무시되는 자리다).
    assert out["owner"] == "user:from-server"


def test_a_server_that_is_the_remote_does_not_answer_the_target_question():
    """허브 프로파일(`hub=True`)에서는 `target` 을 **안 찍는다**.

    라이브에서 물린 자리다(2026-08-17 배포 직후): 허브도 같은 깔때기를 도는데 거기서
    target 은 언제나 "local" 이라, **원격으로 간 호출이 `"local"` 을 받아 왔다.**
    그 값을 "remote" 로 덮는 것은 새 클라이언트뿐이고 데스크탑 업데이트는 사용자
    클릭이라, 구 클라이언트에게는 계속 거짓이었다. 계정 그래프에 썼는지 확인하려고
    이 필드를 본 세션이 반대 답을 듣고 로컬에 다시 쓸 수 있다 — 이 에코가 막으려던
    바로 그 고장이다. 호출자의 local/remote 개념은 호출자만 아니까 안 답하는 것이 맞다."""
    from vinc_mcp import tools as T

    class _FakeBridge:
        owner = "user:local"

        def request(self, *a, **k):
            return {"executed": True, "owner": "user:hosted"}

    out = T.call("vinc_ingest", {"nodes": []}, _FakeBridge(), hub=True)
    assert "target" not in out, "자기가 곧 원격인 서버가 target 을 답했다"
    assert out["owner"] == "user:hosted"       # owner 는 그대로 실린다
    # 로컬 프로파일에서는 계속 찍는다(회귀 가드)
    assert T.call("vinc_ingest", {"nodes": []}, _FakeBridge())["target"] == "local"


# ── v0.8.4 axis2 item9 — vinc_recall 의 예약 열 넷 ────────────────────────────
# tenant/workspace/access_count/last_access 는 recall_plan 투영에 늘 실리지만
# vinc_ingest 로는 절대 채워지지 않는다(각각 learn-log 배치 가져오기와 preference
# touch 신호 전용). "값이 항상 비었다" 는 드롭 사유가 아니라 문서화 사유다 —
# 드롭하면 그 두 실제 쓰기 경로를 쓰는 소유자의 데이터가 응답에서 사라진다.
# 정본은 [[vinc.canon.mcp-contract §4]].
def test_recall_row_shape_names_every_projected_field():
    """`recall_plan` 이 실제로 내는 열 이름 전부가 vinc_recall 설명에 있어야 한다 —
    설명이 사실보다 적게 말하면 호스트는 응답에 낯선 필드가 섞였다고 오인한다."""
    import re

    from api.query import recall_plan

    fields = set(re.findall(r"AS (\w+)", recall_plan().cypher))
    assert fields, "recall_plan 투영에서 필드를 못 뽑았다 — 정규식이 어긋났다"
    desc = REF["vinc_recall"]
    missing = [f for f in fields if f not in desc]
    assert not missing, f"vinc_recall 참조문에 없는 투영 필드: {missing}"


def test_recall_reserved_columns_documented_as_not_vinc_ingest_settable():
    """tenant/workspace/access_count/last_access 가 vinc_ingest 로는 안 채워진다는
    사실이 계약에 있어야 한다 — 없으면 값이 비는 것을 버그로 신고한다."""
    desc = REF["vinc_recall"]
    for field in ("tenant", "workspace", "access_count", "last_access"):
        assert field in desc, f"vinc_recall 설명에 예약 필드 '{field}' 가 없다"
    assert "vinc_ingest" in desc, "예약 필드가 vinc_ingest 로 안 채워진다는 설명이 없다"


def test_identity_stamp_stays_off_the_read_tools():
    """조회 스무 개에 같은 두 줄이 붙으면 그건 잡음이고, 잡음은 읽히지 않는다."""
    from vinc_mcp import tools as T

    class _FakeBridge:
        owner = "user:local"

        def request(self, *a, **k):
            return {"executed": True, "rows": []}

    out = T.call("vinc_search", {"query": "x"}, _FakeBridge())
    assert "target" not in out


# ── 되돌릴 수 없는 도구는 되돌릴 수 있는 앞단계를 가리킨다 (2026-09-01) ───────
# 정본: `vinc.canon.tool-response-disclosure` 의 "Destructive". 규칙 넷 중 마지막이
# **사람이 고칠 수 있는 것은 이름으로 부른다**인데, 파괴적인 도구에서 그것은 "지우기
# 전에 볼 수 있는 것"과 "대신 할 수 있는 되돌릴 수 있는 일"이다.
#
# 목록을 새로 만들지 않고 **주석표에서 유도한다**(`destructiveHint`) — 파괴적 도구가
# 늘면 이 검사가 저절로 그것을 덮는다. 세 번째 목록을 만들면 그 목록이 낡는다.
def _destructive() -> list[str]:
    from vinc_mcp import tools as _t
    return sorted(n for n, a in _t._ANNOTATIONS.items() if a.get("destructiveHint"))


def test_the_annotation_table_still_marks_destructive_tools():
    """유도의 전제 — 표가 비면 아래 검사가 **공허하게** 통과한다."""
    names = _destructive()
    assert len(names) >= 3, f"파괴적 도구가 {names} 뿐이다 — 주석표를 확인할 것"
    for n in names:
        assert n in SERVED, n


def test_every_destructive_tool_names_a_cheaper_move_first():
    """지우기 전에 볼 도구나, 되돌릴 수 있는 대안을 **이름으로** 부르는가.

    ⚠ "irreversible 이라고 적혀 있는가"로 쓰면 공허하다 — 셋 다 이미 그렇게 적혀
    있었고, 그러면서도 `vinc_merge_nodes` 는 옆에 있는 되돌릴 수 있는 절반
    (`vinc_merge_reject`)을 한 번도 안 가리켰다(실측 2026-09-01). 경고는 무엇을
    하라고 말하지 않는다."""
    others = {t["name"] for t in TOOLS}
    for name in _destructive():
        # 호스트가 받는 요약에는 더 싼 앞단계(dry_run 미리보기)가 반드시 있다.
        assert "dry_run" in SERVED[name]["description"], f"{name}: 요약이 미리보기를 안 말한다"
        named = {o for o in others if o != name and o in REF[name]}
        assert named, f"{name}: 더 싼 앞단계도 되돌릴 수 있는 대안도 안 가리킨다"


def test_the_irreversible_merge_points_at_the_reversible_half():
    """이 짝은 특히 못 박는다 — 병합 도구 넷 중 셋은 서로를 가리키는데 **되돌릴 수
    없는 하나만** 고아였다. 가장 위험한 자리가 가장 안 연결돼 있었다. 요약에서 지우면 안 된다."""
    assert "vinc_merge_reject" in SERVED["vinc_merge_nodes"]["description"]
    assert "reversible" in REF["vinc_merge_nodes"].lower()


def test_delete_says_to_look_before_not_after():
    """`deleted.edges` 는 사후 집계다. 그 값만 있고 사전 확인 경로가 없으면, 무엇이
    함께 사라지는지는 사라진 뒤에야 알 수 있다. 사전 확인은 미리보기가 한다."""
    assert "dry_run:true first" in SERVED["vinc_delete"]["description"]
    d = REF["vinc_delete"]
    assert "vinc_neighbors" in d, "지우기 전에 볼 도구를 안 가리킨다"
    assert "after the fact" in d, "사후 집계라는 것을 안 말한다"
# ── 실패를 이름으로 부르는가 (2026-08-31) ────────────────────────────────────
# 정본: [[decision:hide-implementation-preserve-evidence-surface-failure]] ·
# [[concept:tool-response-disclosure]]. 규칙은 "구현은 숨기고, 근거는 남기고,
# **상태 변화와 무결성 실패는 올린다**"다.
#
# 이 검사가 있는 이유: 두 필드가 이름만 설명에 있고 **실패했을 때 무슨 뜻인지**가
# 없었다. 그러면 에이전트는 값을 보고도 조용히 넘긴다.
#   · `vector_note` 는 "쓰기 직후 재빌드"만 적혀 있었다. 그런데 라이브에서 이 값이
#     `TimeoutError` 로 온다 — 의미 검색이 실패한 것인데 설명대로 읽으면 정상이다.
#   · `verified` 는 플래그 이름만 있었다. false 면 본문이 잘렸을 수 있는데, 잘린
#     본문도 산문으로 읽혀서 화면만 봐서는 아무도 모른다.
#
# 둘 다 **평소에는 조용하고 나빠졌을 때만 값이 생기는** 필드다. 그래서 "내부"로
# 분류되기 쉽고, 분류되는 순간 사용자는 열화를 영영 못 본다.
def test_degraded_states_are_named_not_just_listed():
    search = REF["vinc_search"]
    assert "vector_note" in search
    # 실패 원인을 예시로 들어야 "재빌드가 아닌 값"을 알아본다.
    assert "timeout" in search.lower(), "vector_note 의 실패 갈래가 참조문에 없다"
    assert "text matching alone" in search, "열화 상태의 결과가 무엇인지 안 말한다"

    doc_get = REF["vinc_doc_get"]
    assert "verified" in doc_get
    assert "FALSE" in doc_get or "false" in doc_get, "verified 가 거짓일 때를 안 말한다"
    assert "incomplete" in doc_get, "무결성 실패의 결과(본문이 잘림)를 안 말한다"


def test_the_reference_says_what_the_default_shape_cuts_and_what_to_cite():
    """기본 본문은 search·neighbors 의 긴 제목만 자르고 search node hit 의 id 와 같은 cite_id 만 뺀다.
    그 규칙은 두 도구의 `raw` 참조문만 말하고, brief·vinc_node 의 `raw` 참조문은 공용 문장 그대로다."""
    from vinc_mcp.tools import TOOLS, _RAW_PROP, _RAW_PROP_TITLES
    assert "a node hit without one: cite its `id`" in REF["vinc_search"]
    assert "cite `cite_id`." in REF["vinc_brief"] and "where it is missing" not in REF["vinc_brief"]
    raw = _RAW_PROP_TITLES["x-reference"]
    for phrase in ("cut to at most 120", "never cut", "first row of each doc_id",
                   "node hit's cite_id when it equals its id",
                   "neighbors result with a cut title carries a short `omitted`"):
        assert phrase in raw and phrase not in _RAW_PROP["x-reference"], phrase
    for gone in ("concept:doc.<doc_id>", "section", "one_liner", "body_cite", "widened", "quote source"):
        assert gone not in raw, gone
    props = {t["name"]: t["inputSchema"]["properties"]["raw"] for t in TOOLS
             if "raw" in t.get("inputSchema", {}).get("properties", {})}
    assert props["vinc_search"] is _RAW_PROP_TITLES and props["vinc_neighbors"] is _RAW_PROP_TITLES
    assert props["vinc_brief"] is _RAW_PROP and props["vinc_node"] is _RAW_PROP


class _AnswerBridge:
    owner = "user:local"

    def __init__(self, out):
        self.out = out

    def request(self, *a, **k):
        return dict(self.out)


def test_the_silent_failure_tools_tell_the_reader_to_speak():
    """상태가 나빠졌을 때만 값이 생기는 필드는 **말하라고** 전해야 한다. 그 지시는 이제
    설명이 아니라 그 상태를 담은 응답(`answer_note`)이 싣는다.

    ⚠ 이 검사를 "필드 이름이 있는가"로 쓰면 공허하다 — 여섯 필드 전부 이름은 이미
    있었고, 그래서 통과하면서도 둘이 실패를 안 말하고 있었다(실측 2026-08-31)."""
    from vinc_mcp import tools as T
    failed = {"executed": True, "rows": [], "arms": "fts", "vector_note": "TimeoutError: timed out"}
    for name in ("vinc_search", "vinc_brief"):
        out = T.call(name, {"query": "q"}, _AnswerBridge(failed))
        assert "say so" in out.get("answer_note", "").lower(), f"{name}: 말하라는 지시가 없다"
    # 호스트 별칭 search 는 결과를 다시 짜므로 지시를 그대로 옮겨 실어야 한다(호스트가 가장 자주 부른다).
    out = T.call("search", {"query": "q"}, _AnswerBridge(failed), hub=True)
    assert "say so" in out.get("answer_note", "").lower(), "search: 말하라는 지시가 없다"
    out = T.call("vinc_doc_get", {"doc_id": "d"}, _AnswerBridge({"executed": True, "verified": False,
                                                               "text": "abc"}))
    assert "say so" in out.get("answer_note", "").lower(), "vinc_doc_get: 말하라는 지시가 없다"


def test_benign_and_clean_results_carry_no_answer_note():
    """재빌드 창과 인코더 판 경고는 의미 팔이 돈 것이다. 정상 결과에 지시를 붙이면 잡음이 된다."""
    from vinc_mcp import tools as T
    for out in ({"executed": True, "rows": []},
                {"executed": True, "rows": [], "vector_note": "rebuilding", "vector_rebuilding": True},
                {"executed": True, "rows": [], "vector_note": "runtime", "encoder_runtime_warning": True}):
        assert "answer_note" not in T.call("vinc_search", {"query": "q"}, _AnswerBridge(out))
    ok = T.call("vinc_doc_get", {"doc_id": "d"}, _AnswerBridge({"executed": True, "verified": True}))
    assert "answer_note" not in ok


# ── 스키마가 받는 조건 필드를 설명이 이름으로 부르는가 (VIN-602) ──────────────
# dry_run·preview_digest·expected_content_hash 는 TOOLS 정의 뒤에 스키마로 주입된다.
# 그래서 스키마는 늘었는데 호스트가 읽는 설명은 되돌릴 수 없는 호출만 말하던 때가
# 있었다(삭제·병합 셋과 vinc_doc_put). 스키마에서 파생해 검사하므로 새 도구가
# 주입 목록에 들어오면 설명도 함께 고쳐야 한다.

def test_preview_tools_describe_the_preview_then_apply_flow():
    """요약은 순서(dry_run 먼저, 그다음 preview_digest)를 말하고, 응답이 다음 할 일을 말하고,
    참조문이 전체 계약을 싣는다."""
    from api.mutation_preview import HINTS, NEXT_STEP
    tools = [n for n, t in SERVED.items() if "preview_digest" in _params(t)]
    assert set(tools) >= {"vinc_delete", "vinc_delete_edge", "vinc_merge_nodes", "vinc_doc_delete"}
    for name in tools:
        for word in ("dry_run", "preview_digest"):
            assert word in SERVED[name]["description"], f"{name}: 요약이 {word} 를 말하지 않는다"
        for word in ("dry_run", "preview_digest", "operation_id", "preview_conflict",
                     "preview:{", "Applied, returns"):
            assert word in REF[name], f"{name}: 참조문이 {word} 를 말하지 않는다"
    assert "preview_digest" in NEXT_STEP and "without dry_run" in NEXT_STEP
    assert "dry_run:true" in HINTS["preview_conflict"]


def test_conditional_document_write_names_its_conditions():
    from api.mutation_contract import WRITE_CONFLICT_HINT
    tools = [n for n, t in SERVED.items() if "expected_content_hash" in _params(t)]
    assert "vinc_doc_put" in tools
    for name in tools:
        assert "expected_content_hash" in SERVED[name]["description"], name
        for word in ("expected_content_hash", "create_only", "operation_id",
                     "write_conflict", "current_content_hash"):
            assert word in REF[name], f"{name}: 참조문이 {word} 를 말하지 않는다"
    assert "Read it again" in WRITE_CONFLICT_HINT


# VIN-611: 주입 필드는 미리보기 넷과 vinc_doc_put 만이 아니다. operation_id 는 쓰기 여덟에,
# create_only 는 vinc_ingest 에도 실린다. 주입된 필드마다 그 이름을 설명이 부르는지 본다.
_INJECTED_FIELDS = ("operation_id", "create_only", "preview_digest", "dry_run",
                    "expected_content_hash")


@pytest.mark.parametrize("field", _INJECTED_FIELDS)
def test_every_injected_field_is_named_in_the_description(field):
    """주입된 필드는 호스트가 받는 스키마에 자기 설명을 갖고, 참조문이 그 이름을 부른다."""
    tools = [n for n, t in SERVED.items() if field in _params(t)]
    assert tools, f"{field} 를 받는 도구가 없다"
    for name in tools:
        assert _params(SERVED[name])[field].get("description"), f"{name}.{field}: 인자 설명이 없다"
        assert field in REF[name], f"{name}: 참조문이 {field} 를 말하지 않는다"


def test_operation_id_writes_say_how_to_retry():
    tools = [n for n, t in SERVED.items() if "operation_id" in _params(t)]
    assert set(tools) >= {"vinc_ingest", "vinc_delete", "vinc_delete_edge", "vinc_merge_nodes",
                          "vinc_merge_reject", "vinc_doc_put", "vinc_doc_delete",
                          "vinc_doc_extract", "vinc_operation_status"}
    for name in set(tools) - {"vinc_operation_status"}:
        assert "vinc_operation_status" in _params(SERVED[name])["operation_id"]["description"], name
        for word in ("vinc_operation_status", "replayed:true"):
            assert word in REF[name], f"{name}: 참조문이 {word} 를 말하지 않는다"


def test_conditional_ingest_names_its_conditions():
    assert "props_patch" in SERVED["vinc_ingest"]["description"]
    d = REF["vinc_ingest"]
    for word in ("create_only", "expected_rev", "props_patch", "write_conflict", "current_rev"):
        assert word in d, f"vinc_ingest: 참조문이 {word} 를 말하지 않는다"


def test_ingest_summary_keeps_the_read_before_write_covenant():
    """계약 §3: 약한 호스트에게는 설명이 유일한 통로다. 요약에서 지우면 중복 노드가 쌓인다."""
    d = SERVED["vinc_ingest"]["description"]
    assert "vinc_search" in d and "vinc_conventions" in d and "warnings" in d


def test_community_read_names_the_pack_version_not_a_release():
    """출하 번호로 적으면 다음 릴리스가 나가는 순간 거짓이 된다. 팩 판으로 적는다."""
    import re
    d = REF["vinc_community_read"]
    assert "pack_version 2" in d
    for text in (d, HUB["vinc_community_read"]["description"]):
        assert not re.search(r"\b0\.\d+\.\d+\b", text), "설명이 데스크톱 출하 번호를 단정한다"


# ── VIN-589: 핸들러가 이미 지키는 한계를 스키마도 말한다 ─────────────────────────
def test_delete_edge_type_enum_is_the_handler_list():
    """`vinc_delete_edge.type` 의 enum 은 핸들러가 받는 목록이다: fragment 타입명
    (`api/query.py` `_FRAGMENT_EDGE_TO_REL`)과 물리 부착 ATTACHES_TO. 목록이 갈리면 MCP 가
    핸들러가 받는 값을 거절하거나 못 받는 값을 광고한다."""
    from api import query
    spec = SERVED["vinc_delete_edge"]["inputSchema"]["properties"]["type"]
    expected = (set(query._FRAGMENT_EDGE_TO_REL) | set(query._FRAGMENT_EDGE_TO_REL.values())
                | {"ATTACHES_TO"})
    assert set(spec["enum"]) == expected
    for value in spec["enum"]:
        rel = query._FRAGMENT_EDGE_TO_REL.get(value.lower(), value.upper())
        assert rel in query.SYNC_EDGE_RELS, f"{value} 는 핸들러가 지울 수 없는 타입이다"


def test_delete_edge_refuses_a_type_outside_the_enum_before_the_bridge():
    from vinc_mcp import tools as T

    class _Bridge:
        owner = "user:test"

        def request(self, *a, **k):
            raise AssertionError("거절은 사이드카에 닿기 전이어야 한다")

    with pytest.raises(T.InvalidArgument) as err:
        T.call("vinc_delete_edge", {"from_id": "concept:a", "to_id": "concept:b",
                                    "type": "connects_via"}, _Bridge())
    assert "ATTACHES_TO" in err.value.text


def test_delete_edge_accepts_the_physical_name_vinc_edges_returns():
    """vinc_edges 는 물리 관계명(RELATED_TO)을 돌려준다. 그 값을 그대로 넘기는 호스트가
    MCP 단에서 거절되면 안 된다 — 핸들러는 원래 둘 다 받는다."""
    from vinc_mcp import tools as T
    seen = []

    class _Bridge:
        owner = "user:test"

        def request(self, *a, **k):
            seen.append((a, k))
            return {"kind": "delete_edge", "executed": True, "found": False}

    T.call("vinc_delete_edge", {"from_id": "concept:a", "to_id": "concept:b",
                                "type": "RELATED_TO"}, _Bridge())
    assert seen, "물리 관계명이 사이드카에 닿기 전에 거절됐다"


def test_role_check_output_limit_matches_the_handler():
    from vinc_mcp import tools as T
    spec = SERVED["vinc_role_check"]["inputSchema"]["properties"]["output"]
    assert spec["maxLength"] == 131072
    out = T._t_role_check({"role": "agent-dev-core", "output": "x" * (spec["maxLength"] + 1)},
                          bridge=None)
    assert out["error"] == "output_required"
