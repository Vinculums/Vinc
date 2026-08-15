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
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vinc_mcp.bridge import Bridge                   # noqa: E402
from vinc_mcp.rpc import _tool_specs                  # noqa: E402
from vinc_mcp.tools import TOOLS                     # noqa: E402

#: 호스트가 tools/list 로 **실제로 받는** 스펙. 한때 rpc 에 description 을
#: 통째로 갈아치우는 override 계층이 있어서 tools.py 를 고쳐도 안 닿았다 —
#: 설명 계약은 반드시 이 경로로 검사한다.
SERVED = {t['name']: t for t in _tool_specs()}

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
    """모델이 size ≠ len(members) 를 버그로 읽지 않으려면 설명에 적혀 있어야 한다."""
    desc = SERVED["vinc_diagnose"]["description"]
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


# ── 3. F12 — 설명은 선택을 가능하게 한다 ─────────────────────────────────────
#
# 길이는 대리 지표일 뿐이지만, 71자짜리 한 줄("Subgraph around a seed node")로는
# 모델이 vinc_graph 와 vinc_neighbors 중 무엇을 부를지 못 고른다. 실제로 못 골랐다.
THIN_BEFORE = ("vinc_graph", "vinc_suggest_ties", "vinc_domains", "vinc_recall", "vinc_decide")


@pytest.mark.parametrize("name", THIN_BEFORE)
def test_previously_thin_descriptions_now_carry_guidance(name):
    desc = SERVED[name]["description"]
    assert len(desc) >= 250, f"{name}: 설명이 {len(desc)}자 — 선택 근거가 없다"
    assert "Returns" in desc, f"{name}: 무엇이 돌아오는지 안 적혀 있다"


@pytest.mark.parametrize("name", THIN_BEFORE)
def test_read_only_tools_say_so(name):
    """쓰기 도구와 헷갈리면 모델이 조회에도 확인을 구하거나, 반대로 겁 없이 쓴다."""
    assert "Read-only" in SERVED[name]["description"]


def test_every_tool_has_a_description():
    missing = [n for n, t in SERVED.items() if not (t.get("description") or "").strip()]
    assert not missing, f"설명 없는 도구: {missing}"


def test_served_descriptions_are_exactly_the_canonical_ones():
    """tools.py 가 정본이라면, tools/list 가 내는 설명은 그것과 **글자 그대로** 같아야
    한다. rpc 의 override 계층이 recall·domains 를 조용히 갈아치우고 있었다 — 그래서
    tools.py 를 고쳐도 호스트는 옛 문구를 봤다."""
    canon = {t["name"]: t["description"] for t in TOOLS}
    drift = [n for n, d in canon.items() if SERVED[n]["description"] != d]
    assert not drift, f"tools.py 와 tools/list 설명이 다른 도구: {drift}"


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
    """미러를 만들어 두고 설명에 안 쓰면 아무것도 달라지지 않는다. 호스트가 실제로
    받는 문자열(SERVED)에 그 문장이 들어 있는지 본다."""
    from vinc_mcp.tools import _ALLOWED_PAIRS_HINT
    assert _ALLOWED_PAIRS_HINT in SERVED[name]["description"], \
        f"{name}: 허용 쌍이 호스트가 받는 설명에 없다"


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
    ingest_desc = SERVED["vinc_ingest"]["description"]
    for t in ("record", "concept", "domain", "bridge", "episode", "decision"):
        assert t in node_type_desc, f"노드 type 설명에 {t} 가 없다"
        assert t in ingest_desc, f"vinc_ingest 설명의 라우팅 목록에 {t} 가 없다"


def test_docstring_tool_count_matches_the_list():
    """숫자를 손으로 쓰면 어긋난다 — F10-b 로 merge_reject 가 늘었을 때 독스트링은
    19 인 채로 남았고, "아래 TOOLS 가 정본"이라는 방어 문장을 단 채로 틀렸다."""
    import re
    from vinc_mcp import tools as _t
    m = re.search(r"\*\*(\d+)종\*\*", _t.__doc__ or "")
    assert m, "독스트링에서 도구 수를 못 찾았다"
    assert int(m.group(1)) == len(TOOLS) == len(SERVED), (
        f"독스트링 {m.group(1)}종 vs TOOLS {len(TOOLS)}개 vs 서빙 {len(SERVED)}개")
