# -*- coding: utf-8 -*-
"""`fragment._merge_node`·`_merge_edge` 의 현재 동작을 표로 고정한다(특성 테스트).

입력은 아래 `CASES` 의 한 줄이고, 결과는 공개 입구 `fragment.merge_fragment` 를 실 엔진
스토어에 한 번 돌려 얻는다. 기록하는 것은 셋이다: 응답(경고를 뺀 나머지), 경고 목록(순서
그대로), 호출 뒤 스토어의 노드·엣지·엣지 삭제 표식 전부. 기대값은 데이터 파일
`tests/fixtures/fragment_merge_characterization.json` 에 있다. `plain` 은 PK 네임스페이싱을
끈 모드, `namespaced` 는 켠 모드에서 **결과가 다른 케이스만** 담는다.

이 표가 바뀌면 그것은 쓰기 경로의 동작 변경이다. 리팩터는 표를 바꾸지 않아야 하고, 의도한
변경이면 바뀐 케이스를 PR 본문에 적는다. 다시 기록하는 법(끈 모드를 먼저):

    VINC_PK_NAMESPACED=0 python tests/core/test_fragment_merge_characterization.py --record
    VINC_PK_NAMESPACED=1 python tests/core/test_fragment_merge_characterization.py --record

정규화: HLC 시각은 `<hlc>`, 오늘 날짜는 `<today>`, 120자를 넘는 문자열은 `<str N>`, 12개를
넘는 목록은 `<list N>` 로 적는다. 긴 고정 문구 넷(허용 쌍 안내·엣지 경고 이유 셋)과 충돌
안내는 토큰으로 줄이고, 그 원문은 `test_고정_문구` 가 따로 붙잡는다. `updated_at`·`sync_seq`
는 적지 않는다: 스탬프가 찍혔는지는 `rev` 가 말한다.
"""
from __future__ import annotations

import copy
import functools
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from api import fragment as frag                       # noqa: E402
from api import mutation_contract                      # noqa: E402
from engines import domain_hierarchy                   # noqa: E402
from store import pkns                                 # noqa: E402
from store import querybase as _qb                     # noqa: E402
from store.embedded_schema import node_table_specs     # noqa: E402

GOLDEN = _ROOT / "tests" / "fixtures" / "fragment_merge_characterization.json"

A = "user:alice"
B = "user:bob"
WIKI = "community:00000000-0000-4000-8000-000000000001"


# ── 입력 표 ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Case:
    """케이스 하나. ``seed`` 는 앞서 넣을 fragment(또는 (fragment, kwargs)) 들이고 그 경고는
    기록하지 않는다. ``raw`` 는 공개 입구로 만들 수 없는 행을 직접 심는 함수다. ``themes`` 는
    본 호출에만 주제 어휘 스토어를 준다(시드는 주제 없이 들어간다)."""
    name: str
    call: dict
    seed: tuple = ()
    raw: tuple = ()
    kwargs: dict = field(default_factory=dict)
    themes: bool = False
    fault: str | None = None


def node(kind, nid, **fields):
    return {"type": kind, "id": nid, **fields}


def C(nid="concept:a", **f):
    return node("concept", nid, **f)


def R(nid="record:a", **f):
    return node("record", nid, **f)


def D(nid="decision:a", **f):
    return node("decision", nid, **f)


def EP(nid="episode:a", **f):
    return node("episode", nid, **f)


def BR(nid="bridge:a", **f):
    return node("bridge", nid, **f)


def W(nid="widget:a", **f):
    return node("widget", nid, **f)


def E(src, dst, etype, **f):
    return {"from": src, "to": dst, "type": etype, **f}


def F(nodes=(), edges=(), owner=A, **extra):
    """fragment 하나. ``owner=None`` 이면 owner 키를 싣지 않는다(로컬 경로)."""
    out = {"nodes": list(nodes), "edges": list(edges), **extra}
    if owner is not None:
        out["owner"] = owner
    return out


def legacy_row(label, pk, nid, **cols):
    """owner 가 빈 옛 행을 논리 키로 직접 심는다(공개 입구는 owner 를 늘 찍는다)."""
    def _run(store):
        sets = ", ".join(f"n.{k} = ${k}" for k in cols)
        store.run(f"CREATE (n:{label} {{{pk}: $id}})" + (f" SET {sets}" if sets else ""),
                  {"id": nid, **cols})
    return _run


def tombstone(owner, src, rel, dst):
    """엣지 삭제 표식 하나를 심는다. 키는 쓰기 경로와 같은 `pkns.ekey` 로 짓는다."""
    def _run(store):
        store.run("CREATE (t:_EdgeTombstone {ekey: $k, src: $s, rel: $r, dst: $d, owner_id: $o, "
                  "deleted_at: '2025-01-01T00:00:00Z'})",
                  {"k": pkns.ekey(owner, src, rel, dst), "s": src, "r": rel, "d": dst, "o": owner})
    return _run


_ROLE_A, _ROLE_B = "concept:agent-maker-x", "concept:agent-checker-x"
_ROLES = F([C(_ROLE_A, title="agent role: maker"), C(_ROLE_B, title="agent role: checker")])
_TWO_C = F([C("concept:a", title="A"), C("concept:b", title="B")])
_FULL_C = C("concept:a", title="A", domain="vinc/x", tags=["t"], props={"alpha": 1, "beta": 2},
            one_liner="line", content_time="2024")
_MANY = [C(f"concept:m{i}", title=f"M{i}") for i in range(513)]

CASES: list[Case] = [
    # 형태 검사: 이 노드 하나만 건너뛰고 나머지는 계속한다
    Case("node.no_type", F([{"id": "concept:a", "title": "A"}])),
    Case("node.blank_type_and_id", F([{"id": "  ", "type": " "}])),
    Case("node.no_id", F([{"type": "concept", "title": "A"}])),
    Case("node.sep_in_title", F([C(title="a\x1fb")])),
    Case("node.sep_in_props", F([R(title="R", props={"k": ["x\x1f"]})])),
    Case("node.prefix_mismatch", F([EP("concept:a", title="A")])),
    Case("node.prefix_mismatch_open_type", F([W("record:a", title="A")])),
    Case("node.entity_without_title", F([W()])),
    Case("node.entity_blank_title", F([W(title="  ")])),
    Case("node.entity_title_not_a_string", F([W(title=7)])),
    Case("node.title_not_a_string", F([C(title=12)])),
    Case("node.title_at_cap", F([C(title="t" * 16_384)])),
    Case("node.title_over_cap", F([C(title="t" * 16_385)])),
    Case("node.tags_at_cap", F([C(title="A", tags=[f"t{i}" for i in range(256)])])),
    Case("node.tags_over_count_cap", F([C(title="A", tags=[f"t{i}" for i in range(257)])])),
    Case("node.tag_over_length_cap", F([C(title="A", tags=["x" * 2049])])),
    Case("node.tags_string_split", F([C(title="A", tags="ab")])),
    Case("node.tags_not_iterable", F([C(title="A", tags=5)])),
    Case("node.props_over_cap", F([C(title="A", props={"blob": "x" * (512 * 1024)})])),
    Case("node.props_not_serialisable", F([C(title="A", props={"s": {1, 2}})])),
    Case("node.domain_not_a_string", F([C(title="A", domain=5)])),
    Case("node.duplicate_in_payload", F([C(title="A1"), C(title="A2")])),
    Case("node.one_bad_among_good", F([C(title="A"), W("widget:x"), R(title="R")])),

    # 개념: 제목·도메인·태그·props 는 실으면 쓰고 안 실으면 보존한다
    Case("concept.new_minimal", F([C(title="A")])),
    Case("concept.new_without_title", F([C()])),
    Case("concept.new_empty_title", F([C(title="")])),
    Case("concept.new_full", F([_FULL_C])),
    Case("concept.matched_fields_absent_kept", F([C()]), seed=(F([_FULL_C]),)),
    Case("concept.matched_identical_resend", F([_FULL_C]), seed=(F([_FULL_C]),)),
    Case("concept.matched_title_changed", F([C(title="A2")]), seed=(F([_FULL_C]),)),
    Case("concept.matched_empty_title_becomes_id", F([C(title="")]), seed=(F([_FULL_C]),)),
    Case("concept.domain_normalised", F([C(title="A", domain=" vinc // eng / ")])),
    Case("concept.domain_moved", F([C(domain="vinc/y")]), seed=(F([_FULL_C]),)),
    Case("concept.domain_cleared", F([C(domain="")]), seed=(F([_FULL_C]),)),
    Case("concept.tags_cleared", F([C(tags=[])]), seed=(F([_FULL_C]),)),
    Case("concept.tags_null_kept", F([C(tags=None)]), seed=(F([_FULL_C]),)),
    Case("concept.props_whole_replacement", F([C(props={"gamma": 3})]), seed=(F([_FULL_C]),)),
    Case("concept.props_empty_dict", F([C(props={})]), seed=(F([_FULL_C]),)),
    Case("concept.props_not_a_dict", F([C(title="A", props=["x", 1])])),
    Case("concept.unkept_fields", F([C(title="A", valid_from="2025", valid_until="2026",
                                       date="2025-01-01", summary="s")])),
    Case("concept.claim_fields_warned", F([C(title="A", props={
        "subject_path": "vinc:a.py", "measured_at": "2025", "measured_ref": "x"})])),

    # props_patch·expected_rev·create_only 는 쓰기 전에 판정한다(mutation_contract)
    Case("concept.props_patch_merges", F([C(props_patch={"beta": None, "gamma": {"x": 1}},
                                            expected_rev=1)]), seed=(F([_FULL_C]),)),
    Case("concept.props_patch_without_expected_rev",
         F([C(props_patch={"gamma": 3})]), seed=(F([_FULL_C]),)),
    Case("concept.props_patch_with_props",
         F([C(props={"a": 1}, props_patch={"b": 2}, expected_rev=1)]), seed=(F([_FULL_C]),)),
    Case("concept.props_patch_not_a_dict",
         F([C(props_patch=["x"], expected_rev=1)]), seed=(F([_FULL_C]),)),
    Case("concept.props_patch_missing_node", F([C(props_patch={"a": 1})])),
    Case("concept.expected_rev_matches", F([C(title="A2", expected_rev=1)]), seed=(F([_FULL_C]),)),
    Case("concept.expected_rev_stale", F([C(title="A2", expected_rev=5)]), seed=(F([_FULL_C]),)),
    Case("concept.expected_rev_missing_node", F([C(title="A", expected_rev=0)])),
    Case("concept.expected_rev_negative", F([C(title="A", expected_rev=-1)])),
    Case("concept.expected_rev_not_int", F([C(title="A", expected_rev="1")])),
    Case("concept.expected_rev_bool", F([C(title="A", expected_rev=True)])),
    Case("concept.create_only_new", F([C(title="A", create_only=True)])),
    Case("concept.create_only_existing", F([C(title="A2", create_only=True)]),
         seed=(F([_FULL_C]),)),
    Case("concept.create_only_fragment_existing", F([C(title="A2")], create_only=True),
         seed=(F([_FULL_C]),)),
    Case("concept.create_only_fragment_not_bool", F([C(title="A")], create_only="yes")),
    Case("concept.create_only_node_not_bool", F([C(title="A", create_only=1)])),
    Case("concept.create_only_other_owner", F([C(title="A", create_only=True)]),
         seed=(F([C(title="B's")], owner=B),)),
    Case("guard.duplicate_target", F([C(title="A", create_only=True), C(title="A2")])),
    Case("guard.non_dict_node", F(["x", C(title="A", create_only=True)])),
    Case("guard.id_not_a_string", F([{"id": 5, "type": "concept"}, C(title="A", create_only=True)])),
    Case("guard.prefix_differs", F([R("concept:a", title="A", create_only=True)])),
    Case("guard.sep_in_id", F([C("concept:a\x1fb", title="A", create_only=True)])),
    Case("guard.domain_kind", F([node("domain", "domain:x", create_only=True)])),
    Case("guard.no_type", F([{"id": "concept:a", "title": "A", "create_only": True}])),
    Case("guard.too_many_nodes", F(_MANY[:-1] + [C("concept:last", title="L", create_only=True)])),
    Case("guard.local_owner_create_only", F([C(title="A", create_only=True)], owner=None)),
    Case("guard.plain_node_beside_guarded", F([C(title="A", create_only=True), R(title="R")])),

    # one_liner: top-level 이 이기고, 키가 아예 없을 때만 props.one_liner·note 를 올린다
    Case("concept.one_liner_top_level", F([C(title="A", one_liner="short")])),
    Case("concept.one_liner_from_props", F([C(title="A", props={"one_liner": " from props "})])),
    Case("concept.one_liner_from_note", F([C(title="A", props={"note": "a note"})])),
    Case("concept.one_liner_props_blank", F([C(title="A", props={"one_liner": "  ", "note": "n"})])),
    Case("concept.one_liner_props_not_string", F([C(title="A", props={"one_liner": 5})])),
    Case("concept.one_liner_explicit_null", F([C(title="A", one_liner=None,
                                                 props={"one_liner": "x"})])),
    Case("concept.one_liner_promote_into_empty", F([C(props={"one_liner": "x"})]),
         seed=(F([C(title="A")]),)),
    Case("concept.one_liner_promote_same", F([C(props={"one_liner": "line"})]),
         seed=(F([_FULL_C]),)),
    Case("concept.one_liner_promote_conflict", F([C(props={"one_liner": "other"})]),
         seed=(F([_FULL_C]),)),
    Case("concept.one_liner_top_level_empty_clears", F([C(one_liner="")]), seed=(F([_FULL_C]),)),

    # content_time: 형식 위반은 쓰지 않고 경고, 빈 문자열은 지우기
    Case("concept.content_time_valid", F([C(title="A", content_time="2025-03")])),
    Case("concept.content_time_trimmed", F([C(title="A", content_time=" 2025 ")])),
    Case("concept.content_time_int", F([C(title="A", content_time=2025)])),
    Case("concept.content_time_invalid_kept", F([C(content_time="2025/03")]),
         seed=(F([_FULL_C]),)),
    Case("concept.content_time_empty_clears", F([C(content_time="")]), seed=(F([_FULL_C]),)),
    Case("concept.content_time_from_props", F([C(title="A", props={"content_time": "2025-01-02"})])),
    Case("concept.content_time_props_invalid", F([C(title="A", props={"content_time": "Jan 2025"})])),
    Case("concept.content_time_props_blank", F([C(title="A", props={"content_time": " "})])),
    Case("concept.content_time_props_not_string", F([C(title="A", props={"content_time": 2025})])),
    Case("concept.content_time_null_blocks_props",
         F([C(title="A", content_time=None, props={"content_time": "2025"})])),
    Case("concept.content_time_props_conflict_kept", F([C(props={"content_time": "2025"})]),
         seed=(F([_FULL_C]),)),
    Case("concept.content_time_props_into_empty", F([C(props={"content_time": "2025"})]),
         seed=(F([C(title="A")]),)),
    Case("record.content_time_valid", F([R(title="R", content_time="2025-02")])),

    # 출생(created): 신규만 쓴다. ISO 가 아니면 버리고 지금을 찍는다
    Case("concept.created_date", F([C(title="A", created="2025-01-02")])),
    Case("concept.created_timestamp", F([C(title="A", created="2025-01-02T03:04:05+09:00")])),
    Case("concept.created_invalid", F([C(title="A", created="yesterday")])),
    Case("concept.created_impossible_date", F([C(title="A", created="2025-02-30")])),
    Case("concept.created_matched_ignored", F([C(created="2020-01-01")]),
         seed=(F([C(title="A", created="2025-01-02")]),)),
    Case("decision.created_date", F([D(title="D", created="2025-01-02")])),
    Case("bridge.created_date", F([BR(title="B", created="2025-01-02")])),
    Case("episode.created_ignored", F([EP(title="E", created="2025-01-02")])),

    # 역할 계약: 역할 개념이면 계약과 어긋난 곳을 말한다(쓰기는 한다)
    Case("concept.role_new_without_contract", F([C("concept:agent-x", title="x")])),
    Case("concept.role_roster_id", F([C("concept:agent-orchestration", title="roster")])),
    Case("concept.role_matched_title_only", F([C("concept:agent-x", title="y")]),
         seed=(F([C("concept:agent-x", title="x")]),)),

    # 소유: 명시 owner 가 남의(또는 빈 owner) 행에 쓰면 건너뛰고 id_conflicts 에 싣는다
    Case("owner.local_new", F([C(title="A")], owner=None)),
    Case("owner.local_matched_not_restamped", F([C(title="A2")], owner=None),
         seed=(F([C(title="A")], owner=None),)),
    Case("owner.explicit_same_matched", F([C(title="A2")]), seed=(F([_FULL_C]),)),
    Case("owner.explicit_other_conflict", F([C(title="mine")]),
         seed=(F([C(title="theirs")], owner=B),)),
    Case("owner.explicit_legacy_empty_owner", F([C(title="mine")]),
         raw=(legacy_row("Concept", "concept_id", "concept:a", title="legacy", owner_id=""),)),
    Case("owner.local_over_other_owner", F([C(title="local")], owner=None),
         seed=(F([C(title="theirs", tags=["t"])], owner=B),)),
    Case("owner.conflict_every_scoped_kind",
         F([R(title="r"), D(title="d"), EP(title="e"), BR(title="b"), W(title="w")]),
         seed=(F([R(title="R"), D(title="D"), EP(title="E"), BR(title="B"), W(title="W")],
                 owner=B),)),
    Case("owner.domain_is_shared", F([node("domain", "vinc/eng")]),
         seed=(F([node("domain", "vinc/eng")], owner=B),)),
    Case("owner.conflict_takes_its_edges", F([C(title="A"), C("concept:b", title="B")],
                                             [E("concept:a", "concept:b", "relates_to")]),
         seed=(F([C(title="theirs")], owner=B),)),

    # 기록: 제목은 id 로 메우지 않는다. 유효구간은 키가 있을 때만 쓴다
    Case("record.new", F([R(title="R", domain="vinc/x", tags=["t"], props={"k": 1})])),
    Case("record.new_without_title", F([R()])),
    Case("record.new_empty_title", F([R(title="")])),
    Case("record.new_blank_title", F([R(title="  ")])),
    Case("record.matched_title_absent_kept", F([R(tags=["u"])]), seed=(F([R(title="R")]),)),
    Case("record.matched_empty_title_blanks", F([R(title="")]), seed=(F([R(title="R")]),)),
    Case("record.unkept_one_liner", F([R(title="R", one_liner="x", date="2025", summary="s")])),
    Case("record.validity_valid", F([R(title="R", valid_from="2025-01", valid_until="2025-06")])),
    Case("record.validity_invalid_kept", F([R(valid_from="01/2025")]),
         seed=(F([R(title="R", valid_from="2025-01")]),)),
    Case("record.validity_empty_clears", F([R(valid_until="")]),
         seed=(F([R(title="R", valid_from="2025-01", valid_until="2025-06")]),)),
    Case("record.validity_inverted", F([R(title="R", valid_from="2025-06", valid_until="2025-01")])),
    Case("record.validity_inverted_against_stored", F([R(valid_until="2025-01")]),
         seed=(F([R(title="R", valid_from="2025-06")]),)),
    Case("record.validity_partial_close", F([R(valid_until="2025-06")]),
         seed=(F([R(title="R", valid_from="2025-01")]),)),
    Case("record.subject_path_normalised", F([R(title="R", props={"subject_path": "Vinc:.\\api\\x.py"})])),
    Case("record.subject_path_bare", F([R(title="R", props={"subject_path": "./api/x.py"})])),
    Case("record.subject_path_first_repo", F([R(title="R", props={"subject_path": "vinc:a.py"})])),
    Case("record.subject_path_new_repo", F([R("record:b", title="R", props={"subject_path": "vinx:b.py"})]),
         seed=(F([R(title="R", props={"subject_path": "vinc:a.py"})]),)),
    Case("record.measured_at_invalid", F([R(title="R", props={"measured_at": "last week"})])),
    Case("record.measured_at_valid", F([R(title="R", props={"measured_at": "2025-01-02",
                                                           "measured_ref": "abc"})])),

    # 결정: 유효구간은 props 에 산다. 저작 경로의 새 결정은 오늘로 시작을 채운다
    Case("decision.new", F([D(title="D", props={"valid_from": "2025-01"})])),
    Case("decision.new_without_title", F([D()])),
    Case("decision.interval_trimmed", F([D(title="D", props={"valid_from": " 2025-01 "})])),
    Case("decision.interval_malformed_new", F([D(title="D", props={"valid_from": "Jan", "k": 1})])),
    Case("decision.interval_malformed_kept_stored", F([D(props={"valid_from": "Jan"})]),
         seed=(F([D(title="D", props={"valid_from": "2024"})]),)),
    Case("decision.interval_empty_is_a_clear", F([D(props={"valid_from": "2024", "valid_until": ""})]),
         seed=(F([D(title="D", props={"valid_from": "2024", "valid_until": "2025"})]),)),
    Case("decision.interval_inverted", F([D(title="D", props={"valid_from": "2025",
                                                             "valid_until": "2024"})])),
    Case("decision.props_not_a_dict", F([D(title="D", props=["x"])])),
    Case("decision.top_level_interval_dropped", F([D(title="D", valid_from="2025", valid_until="2026",
                                                     content_time="2025")])),
    Case("decision.subject_path_bare", F([D(title="D", props={"subject_path": "api/x.py"})])),
    Case("decision.subject_path_list", F([D(title="D", props={"subject_path": ["vinc:a.py", "b.py", 3]})])),
    Case("decision.claim_field_warned", F([D(title="D", props={"measured_at": "2025"})])),
    Case("decision.with_about", F([D(title="D"), C(title="A")],
                                  [E("decision:a", "concept:a", "about")])),
    Case("decision.authored_new_fills_start", F([D(title="D", props={"k": 1})]),
         kwargs={"authored": True}),
    Case("decision.authored_new_without_props", F([D(title="D")]), kwargs={"authored": True}),
    Case("decision.authored_new_end_only", F([D(title="D", props={"valid_until": "2025"})]),
         kwargs={"authored": True}),
    Case("decision.authored_new_start_given", F([D(title="D", props={"valid_from": "2024"})]),
         kwargs={"authored": True}),
    Case("decision.authored_new_props_not_dict", F([D(title="D", props=["x"])]),
         kwargs={"authored": True}),
    Case("decision.authored_matched", F([D(title="D2")]), seed=(F([D(title="D")]),),
         kwargs={"authored": True}),
    Case("decision.authored_wiki_owner", F([D(title="D")], owner=WIKI), kwargs={"authored": True}),

    # 사건: date·summary 는 top-level 이 이기고, 키가 없을 때만 props 문자열을 올린다
    Case("episode.new", F([EP(title="E", date="2025-01-02", summary="s", domain="vinc/x")])),
    Case("episode.new_without_title", F([EP()])),
    Case("episode.new_empty_title", F([EP(title="")])),
    Case("episode.date_trimmed", F([EP(title="E", date=" 2025-01 ")])),
    Case("episode.date_invalid", F([EP(title="E", date="Jan 2")])),
    Case("episode.date_empty", F([EP(title="E", date="")])),
    Case("episode.date_invalid_kept_stored", F([EP(date="Jan 2")]),
         seed=(F([EP(title="E", date="2025-01-02")]),)),
    Case("episode.from_props", F([EP(title="E", props={"date": "2025-01-02", "summary": " s "})])),
    Case("episode.props_date_invalid", F([EP(title="E", props={"date": "soon"})])),
    Case("episode.props_not_strings", F([EP(title="E", props={"date": 20250102,
                                                             "summary": {"x": 1}})])),
    Case("episode.props_blank", F([EP(title="E", props={"date": " ", "summary": ""})])),
    Case("episode.top_level_wins", F([EP(title="E", date="2025-01-01", summary="top",
                                         props={"date": "2024-01-01", "summary": "props"})])),
    Case("episode.props_promote_conflict", F([EP(props={"date": "2025-02-02", "summary": "new"})]),
         seed=(F([EP(title="E", date="2025-01-01", summary="old")]),)),
    Case("episode.props_promote_into_empty", F([EP(props={"date": "2025-02-02"})]),
         seed=(F([EP(title="E")]),)),
    Case("episode.summary_explicit_empty", F([EP(summary="")]),
         seed=(F([EP(title="E", summary="s")]),)),
    Case("episode.unkept", F([EP(title="E", one_liner="x", content_time="2025", valid_until="2026")])),
    Case("episode.authored_new_fills_date", F([EP(title="E")]), kwargs={"authored": True}),
    Case("episode.authored_new_empty_date", F([EP(title="E", date="")]), kwargs={"authored": True}),
    Case("episode.authored_new_invalid_date", F([EP(title="E", date="soon")]),
         kwargs={"authored": True}),
    Case("episode.authored_new_date_given", F([EP(title="E", date="2024-05-06")]),
         kwargs={"authored": True}),
    Case("episode.authored_matched", F([EP(summary="s")]), seed=(F([EP(title="E")]),),
         kwargs={"authored": True}),
    Case("episode.authored_wiki_owner", F([EP(title="E")], owner=WIKI), kwargs={"authored": True}),

    # 다리: domain 열이 없고 tags 열도 없다(경고하고 나머지는 쓴다)
    Case("bridge.new", F([BR(title="B", props={"k": 1})])),
    Case("bridge.new_without_title", F([BR()])),
    Case("bridge.domain_warned_still_merged", F([BR(title="B", domain="vinc/x"), C(title="A")],
                                                [E("bridge:a", "concept:a", "connects_via")])),
    Case("bridge.tags_dropped", F([BR(title="B", tags=["a"])])),
    Case("bridge.matched_title_absent_kept", F([BR(props={"k": 2})]), seed=(F([BR(title="B")]),)),

    # 도메인: 열이 name 하나뿐이고 소유를 담지 않는다
    Case("domain.new", F([node("domain", "vinc/eng")])),
    Case("domain.prefixed_id", F([node("domain", "domain:vinc/eng")])),
    Case("domain.fields_dropped", F([node("domain", "vinc/eng", title="T", tags=["t"],
                                          props={"k": 1}, domain="vinc", one_liner="x")])),
    Case("domain.matched", F([node("domain", "vinc/eng")]), seed=(F([node("domain", "vinc/eng")]),)),

    # 개방층(Entity): 열은 type·title·tags·theme_id·owner_id 뿐이다
    Case("entity.new", F([W(title=" W ")])),
    Case("entity.new_with_tags", F([W(title="W", tags=["a", 1])])),
    Case("entity.tags_absent_kept", F([W(title="W2")]), seed=(F([W(title="W", tags=["a"])]),)),
    Case("entity.tags_cleared", F([W(title="W", tags=[])]), seed=(F([W(title="W", tags=["a"])]),)),
    Case("entity.domain_and_props_warned", F([W(title="W", domain="vinc/x", props={"k": 1})])),
    Case("entity.empty_props_not_warned", F([W(title="W", props={})])),
    Case("entity.unkept_contract_fields", F([W(title="W", one_liner="x", content_time="2025",
                                               date="2025", summary="s")])),
    Case("entity.id_without_prefix", F([node("widget", "plain", title="P")])),
    Case("entity.type_changes_on_resend", F([node("gadget", "widget:a", title="W")]),
         seed=(F([W(title="W")]),)),
    Case("entity.local_matched_not_restamped", F([W(title="W2")], owner=None),
         seed=(F([W(title="W")], owner=None),)),
    Case("entity.local_over_other_owner", F([W(title="local")], owner=None),
         seed=(F([W(title="theirs")], owner=B),)),
    Case("entity.themes_explicit_new", F([W(title="W")], theme_id="t1"), themes=True),
    Case("entity.themes_active_default", F([W(title="W")]), themes=True),
    Case("entity.themes_explicit_changes_matched", F([W(title="W")], theme_id="t2"), themes=True,
         seed=(F([W(title="W")]),)),
    Case("entity.themes_active_keeps_matched", F([W(title="W")]), themes=True,
         seed=(F([W(title="W")]),)),

    # 고장 주입: 노드 쓰기 실패와 도메인 고리 실패는 그 노드에서 멈춘다
    Case("fault.node_write_fails", F([C(title="A"), R(title="R")],
                                     [E("record:a", "concept:a", "relates_to")]),
         fault="node_write:Concept"),
    Case("fault.domain_link_fails", F([C(title="A", domain="vinc/x")]), fault="domain_link"),
    Case("fault.topic_guard_on", F([C(title="A")]), fault="topic_guard"),
    Case("fault.endpoint_probe_fails", F([], [E("concept:a", "concept:b", "relates_to")]),
         seed=(_TWO_C,), fault="resolve_probe"),

    # 엣지: 형태·끝점 해석
    Case("edge.sep", F([C(title="A"), C("concept:b", title="B")],
                       [E("concept:a", "concept:b", "relates\x1fto")])),
    Case("edge.endpoint_undeclared", F([C(title="A")], [E("concept:a", "concept:missing", "relates_to")])),
    Case("edge.endpoint_both_undeclared", F([], [E("concept:x", "concept:y", "relates_to")])),
    Case("edge.endpoint_skipped", F([C(title="A"), W("widget:b")],
                                    [E("concept:a", "widget:b", "relates_to")])),
    Case("edge.endpoint_skipped_and_undeclared", F([W("widget:b")],
                                                   [E("widget:b", "concept:nope", "relates_to")])),
    Case("edge.endpoint_from_store", F([], [E("concept:a", "concept:b", "relates_to")]),
         seed=(_TWO_C,)),
    Case("edge.endpoint_without_prefix", F([], [E("plain", "concept:b", "prerequisite_of")]),
         seed=(F([C("plain", title="P"), C("concept:b", title="B")]),)),
    Case("edge.endpoint_domain_in_payload", F([C(title="A"), node("domain", "domain:x")],
                                              [E("concept:a", "domain:x", "relates_to")])),
    Case("edge.endpoint_domain_from_store", F([C(title="A")], [E("concept:a", "domain:x", "relates_to")]),
         seed=(F([node("domain", "domain:x")]),)),
    Case("edge.endpoint_other_owner_explicit", F([], [E("concept:a", "concept:b", "relates_to")]),
         seed=(F(_TWO_C["nodes"], owner=B),)),
    Case("edge.endpoint_other_owner_local", F([], [E("concept:a", "concept:b", "relates_to")], owner=None),
         seed=(F(_TWO_C["nodes"], owner=B),)),
    Case("edge.endpoint_missing_from", F([C("concept:b", title="B")],
                                         [{"to": "concept:b", "type": "relates_to"}])),
    Case("edge.rollup_samples", F([C(title="A")], [E("concept:a", f"concept:n{i}", "relates_to")
                                                   for i in range(10)])),
    Case("edge.rollup_mixed_reasons", F([C(title="A"), C("concept:b", title="B"), W("widget:s")],
                                        [E("concept:a", "concept:gone", "relates_to"),
                                         E("concept:a", "concept:b", "likes"),
                                         E("concept:a", "widget:s", "relates_to"),
                                         E("concept:b", "concept:a", "loves")])),
    Case("edge.unsupported_type", F(_TWO_C["nodes"], [E("concept:a", "concept:b", "likes")])),
    Case("edge.no_type_between_concepts", F(_TWO_C["nodes"], [E("concept:a", "concept:b", "")])),
    Case("edge.duplicate_in_payload", F(_TWO_C["nodes"], [E("concept:a", "concept:b", "relates_to"),
                                                          E("concept:a", "concept:b", "relates_to")])),
    Case("edge.created_from_payload", F(_TWO_C["nodes"], [E("concept:a", "concept:b", "relates_to",
                                                            created="2025-01-01")])),
    Case("edge.created_not_overwritten", F([], [E("concept:a", "concept:b", "relates_to",
                                                  created="2020-01-01")]),
         seed=(F(_TWO_C["nodes"], [E("concept:a", "concept:b", "relates_to", created="2025-01-01")]),)),

    # 개방층 엣지: 양 끝이 Entity 면 타입과 무관하게 TYPED{type}
    Case("edge.typed", F([W(title="W"), W("widget:b", title="W2")], [E("widget:a", "widget:b", "uses")])),
    Case("edge.typed_known_name", F([W(title="W"), W("widget:b", title="W2")],
                                    [E("widget:a", "widget:b", "relates_to")])),
    Case("edge.typed_two_types", F([W(title="W"), W("widget:b", title="W2")],
                                   [E("widget:a", "widget:b", "uses"), E("widget:a", "widget:b", "owns")])),
    Case("edge.typed_without_type", F([W(title="W"), W("widget:b", title="W2")],
                                      [E("widget:a", "widget:b", "")])),
    Case("edge.typed_evidence_dropped", F([W(title="W"), W("widget:b", title="W2")],
                                          [E("widget:a", "widget:b", "uses", evidence="e",
                                             confidence=0.5)])),
    Case("edge.typed_from_store", F([], [E("widget:a", "widget:b", "uses")]),
         seed=(F([W(title="W"), W("widget:b", title="W2")]),)),
    Case("edge.typed_themes_proposed", F([W(title="W"), W("widget:b", title="W2")],
                                         [E("widget:a", "widget:b", "uses")]), themes=True),
    Case("edge.typed_entity_to_concept_refused", F([W(title="W"), C(title="A")],
                                                   [E("widget:a", "concept:a", "relates_to")])),

    # 반려 경로(returns_to): 서로 다른 역할 개념 사이, 근거 필수
    Case("edge.returns_to", F([], [E(_ROLE_A, _ROLE_B, "returns_to", evidence="checker rejected")]),
         seed=(_ROLES,)),
    Case("edge.returns_to_same_role", F([], [E(_ROLE_A, _ROLE_A, "returns_to", evidence="e")]),
         seed=(_ROLES,)),
    Case("edge.returns_to_not_roles", F(_TWO_C["nodes"], [E("concept:a", "concept:b", "returns_to",
                                                            evidence="e")])),
    Case("edge.returns_to_without_evidence", F([], [E(_ROLE_A, _ROLE_B, "returns_to")]), seed=(_ROLES,)),
    Case("edge.returns_to_blank_evidence", F([], [E(_ROLE_A, _ROLE_B, "returns_to", evidence=" ")]),
         seed=(_ROLES,)),
    Case("edge.returns_to_decision_end", F([D(title="D")], [E(_ROLE_A, "decision:a", "returns_to",
                                                              evidence="e")]), seed=(_ROLES,)),
    Case("edge.returns_to_roster", F([C("concept:agent-orchestration", title="roster")],
                                     [E(_ROLE_A, "concept:agent-orchestration", "returns_to",
                                        evidence="e")]), seed=(_ROLES,)),

    # 다리 엣지(connects_via): bridge → concept|decision, role 은 매칭 키
    Case("edge.connects_via", F([BR(title="B"), C(title="A")], [E("bridge:a", "concept:a", "connects_via")])),
    Case("edge.connects_via_roles", F([BR(title="B"), C(title="A")],
                                      [E("bridge:a", "concept:a", "connects_via", role="input"),
                                       E("bridge:a", "concept:a", "connects_via", role="output"),
                                       E("bridge:a", "concept:a", "connects_via", role=" input ")])),
    Case("edge.connects_via_to_decision", F([BR(title="B"), D(title="D")],
                                            [E("bridge:a", "decision:a", "connects_via")])),
    Case("edge.connects_via_evidence_dropped", F([BR(title="B"), C(title="A")],
                                                 [E("bridge:a", "concept:a", "connects_via",
                                                    evidence="e")])),
    Case("edge.connects_via_wrong_pair", F(_TWO_C["nodes"], [E("concept:a", "concept:b", "connects_via")])),

    # 대체(supersedes): 새 것 → 옛 것, 같은 종류, 근거 필수. 구간은 경고만 한다
    Case("edge.supersedes_records_open_old",
         F([R("record:new", title="N", valid_from="2025-06"), R("record:old", title="O")],
           [E("record:new", "record:old", "supersedes", evidence="re-measured")])),
    Case("edge.supersedes_records_aligned",
         F([R("record:new", title="N", valid_from="2025-06"),
            R("record:old", title="O", valid_until="2025-06")],
           [E("record:new", "record:old", "supersedes", evidence="e")])),
    Case("edge.supersedes_records_misaligned",
         F([R("record:new", title="N", valid_from="2025-06"),
            R("record:old", title="O", valid_until="2025-05")],
           [E("record:new", "record:old", "supersedes", evidence="e")])),
    Case("edge.supersedes_new_without_start",
         F([R("record:new", title="N"), R("record:old", title="O", valid_until="2025-05")],
           [E("record:new", "record:old", "supersedes", evidence="e")])),
    Case("edge.supersedes_decisions_open_old",
         F([D("decision:new", title="N", props={"valid_from": "2025"}), D("decision:old", title="O")],
           [E("decision:new", "decision:old", "supersedes", evidence="e")])),
    Case("edge.supersedes_doc_concepts",
         F([C("concept:doc.new", title="N"), C("concept:doc.old", title="O")],
           [E("concept:doc.new", "concept:doc.old", "supersedes", evidence="e")])),
    Case("edge.supersedes_plain_concepts_refused",
         F(_TWO_C["nodes"], [E("concept:a", "concept:b", "supersedes", evidence="e")])),
    Case("edge.supersedes_mixed_kinds_refused",
         F([R(title="R"), D(title="D")], [E("record:a", "decision:a", "supersedes", evidence="e")])),
    Case("edge.supersedes_self_refused", F([R(title="R")], [E("record:a", "record:a", "supersedes",
                                                              evidence="e")])),
    Case("edge.supersedes_without_evidence_refused",
         F([R("record:new", title="N"), R("record:old", title="O")],
           [E("record:new", "record:old", "supersedes")])),
    Case("edge.supersedes_episodes_refused",
         F([EP(title="E"), EP("episode:b", title="E2")],
           [E("episode:a", "episode:b", "supersedes", evidence="e")])),
    Case("edge.supersedes_resend_same",
         F([], [E("record:new", "record:old", "supersedes", evidence="e")]),
         seed=(F([R("record:new", title="N"), R("record:old", title="O", valid_until="2025")],
                 [E("record:new", "record:old", "supersedes", evidence="e")]),)),
    Case("edge.supersedes_resend_new_evidence",
         F([], [E("record:new", "record:old", "supersedes", evidence="e2")]),
         seed=(F([R("record:new", title="N"), R("record:old", title="O", valid_until="2025")],
                 [E("record:new", "record:old", "supersedes", evidence="e")]),)),

    # about: episode|decision → concept|decision(커뮤니티 위키는 기록·사건도 대상)
    Case("edge.about_episode_concept", F([EP(title="E"), C(title="A")], [E("episode:a", "concept:a", "about")])),
    Case("edge.about_decision_decision", F([D(title="D"), D("decision:b", title="D2")],
                                           [E("decision:a", "decision:b", "about")])),
    Case("edge.about_to_record_refused", F([EP(title="E"), R(title="R")], [E("episode:a", "record:a", "about")])),
    Case("edge.about_from_concept_refused", F(_TWO_C["nodes"], [E("concept:a", "concept:b", "about")])),
    Case("edge.about_wiki_to_record", F([EP(title="E"), R(title="R")], [E("episode:a", "record:a", "about")],
                                        owner=WIKI)),
    Case("edge.about_evidence_and_confidence", F([EP(title="E"), C(title="A")],
                                                 [E("episode:a", "concept:a", "about", evidence="e",
                                                    confidence="0.75")])),
    Case("edge.about_confidence_not_a_number", F([EP(title="E"), C(title="A")],
                                                 [E("episode:a", "concept:a", "about", confidence="high")])),
    Case("edge.about_resend_same", F([], [E("episode:a", "concept:a", "about", evidence="e")]),
         seed=(F([EP(title="E"), C(title="A")], [E("episode:a", "concept:a", "about", evidence="e")]),)),

    # 의미층 사이(CC): relates_to 는 개념끼리만이고 무방향으로 dedupe 한다
    Case("edge.relates_to", F(_TWO_C["nodes"], [E("concept:a", "concept:b", "relates_to")])),
    Case("edge.relates_to_evidence", F(_TWO_C["nodes"], [E("concept:a", "concept:b", "relates_to",
                                                           evidence="e", confidence=0.4)])),
    Case("edge.relates_to_reverse_duplicate", F([], [E("concept:b", "concept:a", "relates_to")]),
         seed=(F(_TWO_C["nodes"], [E("concept:a", "concept:b", "relates_to")]),)),
    Case("edge.relates_to_reverse_carries_evidence",
         F([], [E("concept:b", "concept:a", "relates_to", evidence="new", confidence=0.9)]),
         seed=(F(_TWO_C["nodes"], [E("concept:a", "concept:b", "relates_to", evidence="old")]),)),
    Case("edge.relates_to_reverse_same_evidence",
         F([], [E("concept:b", "concept:a", "relates_to", evidence="old")]),
         seed=(F(_TWO_C["nodes"], [E("concept:a", "concept:b", "relates_to", evidence="old")]),)),
    Case("edge.relates_to_decision_refused", F([C(title="A"), D(title="D")],
                                               [E("concept:a", "decision:a", "relates_to")])),
    Case("edge.relates_to_wiki_record_episode", F([R(title="R"), EP(title="E")],
                                                  [E("record:a", "episode:a", "relates_to")], owner=WIKI)),
    Case("edge.relates_to_wiki_decision", F([C(title="A"), D(title="D")],
                                            [E("concept:a", "decision:a", "relates_to")], owner=WIKI)),
    Case("edge.prerequisite_decision_concept", F([D(title="D"), C(title="A")],
                                                 [E("decision:a", "concept:a", "prerequisite_of")])),
    Case("edge.derives_from_decisions", F([D(title="D"), D("decision:b", title="D2")],
                                          [E("decision:a", "decision:b", "derives_from")])),
    Case("edge.inherits_from_confidence", F(_TWO_C["nodes"], [E("concept:a", "concept:b", "inherits_from",
                                                                confidence=1)])),
    Case("edge.cc_resend_same", F([], [E("concept:a", "concept:b", "derives_from", evidence="e")]),
         seed=(F(_TWO_C["nodes"], [E("concept:a", "concept:b", "derives_from", evidence="e")]),)),
    Case("edge.cc_resend_changed_confidence", F([], [E("concept:a", "concept:b", "derives_from",
                                                       confidence=0.2)]),
         seed=(F(_TWO_C["nodes"], [E("concept:a", "concept:b", "derives_from", confidence=0.1)]),)),
    Case("edge.cc_resend_first_confidence", F([], [E("concept:a", "concept:b", "derives_from",
                                                     confidence=0.2)]),
         seed=(F(_TWO_C["nodes"], [E("concept:a", "concept:b", "derives_from")]),)),

    # 기록 출발: ATTACHES_TO{type} 로 원 타입을 보존한다
    Case("edge.record_to_concept", F([R(title="R"), C(title="A")],
                                     [E("record:a", "concept:a", "relates_to", evidence="e")])),
    Case("edge.record_to_decision", F([R(title="R"), D(title="D")],
                                      [E("record:a", "decision:a", "derives_from", confidence=0.5)])),
    Case("edge.record_two_types_same_pair", F([R(title="R"), C(title="A")],
                                              [E("record:a", "concept:a", "relates_to"),
                                               E("record:a", "concept:a", "derives_from")])),
    Case("edge.record_about_refused", F([R(title="R"), C(title="A")], [E("record:a", "concept:a", "about")])),
    Case("edge.record_to_record_refused", F([R(title="R"), R("record:b", title="R2")],
                                            [E("record:a", "record:b", "relates_to")])),
    Case("edge.bridge_pair_refused", F([BR(title="B"), C(title="A")], [E("bridge:a", "concept:a", "relates_to")])),
    Case("edge.episode_concept_relates_refused", F([EP(title="E"), C(title="A")],
                                                   [E("episode:a", "concept:a", "relates_to")])),
    Case("edge.concept_to_record_refused", F([C(title="A"), R(title="R")],
                                             [E("concept:a", "record:a", "derives_from")])),

    # 끝점 스탬프와 삭제 표식
    Case("edge.stamp_edges_off_cc", F(_TWO_C["nodes"], [E("concept:a", "concept:b", "relates_to")]),
         kwargs={"stamp_edges": False}),
    Case("edge.stamp_edges_off_record", F([R(title="R"), C(title="A")],
                                          [E("record:a", "concept:a", "relates_to")]),
         kwargs={"stamp_edges": False}),
    Case("edge.tombstone_dropped_cc", F(_TWO_C["nodes"], [E("concept:a", "concept:b", "relates_to")]),
         raw=(tombstone(A, "concept:a", "RELATED_TO", "concept:b"),)),
    Case("edge.tombstone_dropped_record", F([R(title="R"), C(title="A")],
                                            [E("record:a", "concept:a", "relates_to")]),
         raw=(tombstone(A, "record:a", "ATTACHES_TO:relates_to", "concept:a"),)),
    Case("edge.tombstone_kept_when_stamp_off", F(_TWO_C["nodes"], [E("concept:a", "concept:b", "relates_to")]),
         raw=(tombstone(A, "concept:a", "RELATED_TO", "concept:b"),), kwargs={"stamp_edges": False}),
    Case("edge.tombstone_other_owner_kept", F(_TWO_C["nodes"], [E("concept:a", "concept:b", "relates_to")]),
         raw=(tombstone(B, "concept:a", "RELATED_TO", "concept:b"),)),
    Case("edge.tombstone_local_any_owner", F(_TWO_C["nodes"], [E("concept:a", "concept:b", "relates_to")],
                                             owner=None),
         raw=(tombstone(B, "concept:a", "RELATED_TO", "concept:b"),)),
]


# ── 실행과 기록 ────────────────────────────────────────────────────────────────

_PKS = {label: spec["pk"] for label, spec in node_table_specs().items()}
_HLC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")
#: 엔진 내부 열(`_ID`·`_SRC`·`_DST`·`_LABEL`)은 밑줄로 시작하므로 따로 거른다.
_DROP_COLS = {"updated_at", "sync_seq"}

#: 경고 안의 긴 고정 문구를 토큰으로 줄인다. 이유 문구가 허용 쌍 안내를 품으므로 긴 것부터 바꾼다.
_TOKENS = sorted(
    [(text, f"<reason:{reason}>") for reason, text in frag._EDGE_WARN_REASONS.items()]
    + [(frag._ALLOWED_PAIRS_HINT, "<ALLOWED_PAIRS_HINT>"),
       (mutation_contract.WRITE_CONFLICT_HINT, "<WRITE_CONFLICT_HINT>")],
    key=lambda pair: -len(pair[0]))


def _text(s: str) -> str:
    for long, token in _TOKENS:
        s = s.replace(long, token)
    return _HLC.sub("<hlc>", s).replace(_qb._today(), "<today>")


def _value(v):
    if isinstance(v, str):
        if _HLC.fullmatch(v):
            return "<hlc>"
        if v == _qb._today():
            return "<today>"
        s = _text(v)
        return f"<str {len(v)}>" if len(s) > 120 else s
    if isinstance(v, list):
        return f"<list {len(v)}>" if len(v) > 12 else [_value(x) for x in v]
    if isinstance(v, dict):
        return {k: _value(x) for k, x in v.items()}
    return v


def _row(raw: dict) -> dict:
    out = {}
    for k, v in raw.items():
        if v is None or k in _DROP_COLS or k.startswith("_"):
            continue
        if k == "props" and isinstance(v, str):
            try:
                v = json.loads(v)
            except ValueError:
                pass
        out[k] = _value(v)
    return out


def _ident(raw: dict) -> str:
    return str(raw.get(_PKS.get(raw.get("_LABEL"), ""), "?"))


def snapshot(store) -> dict:
    """스토어 전체의 노드·엣지·엣지 삭제 표식. 값은 읽기 훅이 논리 id 로 돌려준 그대로다."""
    nodes: dict[str, dict] = {}
    for label, pk in _PKS.items():
        rows = store.run(f"MATCH (n:{label}) RETURN n") or []
        if rows:
            nodes[label] = {str(r["n"][pk]): _row(r["n"]) for r in rows}
    edges = sorted(
        ([r["r"]["_LABEL"], _ident(r["a"]), _ident(r["b"]), _row(r["r"])]
         for r in store.run("MATCH (a)-[r]->(b) RETURN a, r, b") or []),
        key=lambda e: json.dumps(e, sort_keys=True))
    stones = sorted(f"{r['k']} @{r['o']}" for r in store.run(
        "MATCH (t:_EdgeTombstone) RETURN t.ekey AS k, t.owner_id AS o") or [])
    out = {"nodes": nodes, "edges": edges, "tombstones": stones}
    return {k: v for k, v in out.items() if v}


def _install_fault(mp: pytest.MonkeyPatch, store, fault: str | None) -> None:
    if fault is None:
        return
    if fault == "topic_guard":
        mp.setattr(store, "_topic_conversion_guard", True, raising=False)
        return
    if fault == "domain_link":
        def _boom(*_a, **_k):
            raise RuntimeError("injected link failure")
        mp.setattr(domain_hierarchy, "link_node", _boom)
        return
    real = store.run
    if fault.startswith("node_write:"):
        head = f"MERGE (n:{fault.split(':', 1)[1]} "

        def _run(cypher, params=None):
            if cypher.startswith(head):
                raise RuntimeError("injected write failure")
            return real(cypher, params)
    elif fault == "resolve_probe":
        def _run(cypher, params=None):
            if cypher.startswith("MATCH (n:Concept) WHERE n.concept_id = $id") \
                    and cypher.endswith("RETURN count(n) AS c"):
                raise RuntimeError("injected probe failure")
            return real(cypher, params)
    else:
        raise AssertionError(f"unknown fault {fault}")
    mp.setattr(store, "run", _run)


def run_case(case: Case, store) -> dict:
    """케이스 하나를 돌려 기록형을 돌려준다. 시드의 결과는 기록하지 않되 실패하면 멈춘다."""
    themes = None
    if case.themes:
        from store.theme_store import ThemeStore
        themes = ThemeStore(None)
    with pytest.MonkeyPatch.context() as mp:
        # 리포 이름 캐시는 스토어 객체 id 로 잡혀 앞 케이스의 값이 남는다. 시드가 채운 값도
        # 호출 결과를 가리므로(빈 집합이 몇 분 남는다) 시드 앞뒤로 비운다.
        mp.setattr(frag, "_REPO_CACHE", {})
        for seed in case.seed:
            fragment, kw = seed if isinstance(seed, tuple) else (seed, {})
            res = frag.merge_fragment(copy.deepcopy(fragment), store=store, **kw)
            assert res.get("executed") is True, (case.name, res)
        for plant in case.raw:
            plant(store)
        mp.setattr(frag, "_REPO_CACHE", {})
        _install_fault(mp, store, case.fault)
        try:
            res = frag.merge_fragment(copy.deepcopy(case.call), store=store, themes=themes,
                                      **case.kwargs)
        except Exception as exc:          # 입구 밖으로 나가는 예외도 동작이다
            res = {"raises": type(exc).__name__}
    warnings = res.pop("warnings", None)
    out = {"result": _value(res)}
    if warnings:
        out["warnings"] = [_text(w) for w in warnings]
    out.update(snapshot(store))
    return out


def _mode() -> str:
    return "namespaced" if pkns.enabled() else "plain"


def _open_store(data_dir: str):
    from store.backend import open_store
    from store.config import load_config
    cfg = load_config({"data_dir": data_dir})
    st = open_store(cfg)
    st.provision(dim=cfg.embedding_dim)
    return st


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE_BACKEND", "embedded")
    monkeypatch.setenv("MEMORY_EMBEDDING_MODEL", "local-en")
    st = _open_store(str(tmp_path / "db"))
    try:
        yield st
    finally:
        st.close()


@functools.lru_cache(maxsize=1)
def _golden_cached() -> str:
    return GOLDEN.read_text(encoding="utf-8")


def _golden() -> dict:
    return json.loads(_golden_cached())


def _expected(golden: dict, name: str) -> dict:
    if _mode() == "namespaced" and name in golden["namespaced"]:
        return golden["namespaced"][name]
    return golden["plain"][name]


# ── 테스트 ────────────────────────────────────────────────────────────────────

def test_케이스_이름이_겹치지_않고_기록과_맞는다():
    names = [c.name for c in CASES]
    assert len(names) == len(set(names))
    golden = _golden()
    assert sorted(golden["plain"]) == sorted(names)
    assert set(golden["namespaced"]) <= set(names)


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_동작이_기록과_같다(case, store):
    assert run_case(case, store) == _expected(_golden(), case.name)


def test_고정_문구():
    """기록에서 토큰으로 줄인 문구의 원문. 클라이언트가 읽는 문장이라 여기서 바뀌어도 변경이다."""
    assert frag._ALLOWED_PAIRS_HINT == (
        "allowed pairs (concept and decision are interchangeable endpoints, "
        "except relates_to which stays concept→concept): "
        "supersedes = new→old of the same kind (record→record, decision→decision, "
        "concept:doc.*→concept:doc.*), with nonempty evidence; "
        "relates_to = concept→concept; "
        "prerequisite_of|derives_from|inherits_from = concept|decision → concept|decision; "
        "returns_to = distinct role concepts, with nonempty evidence; "
        "record → concept|decision for any of those four; "
        "connects_via = bridge → concept|decision; "
        "about = episode|decision → concept|decision; "
        "any-type = open-entity→open-entity (TYPED carrier)")
    assert frag._EDGE_WARN_REASONS == {
        "undeclared_endpoint":
            "endpoint not found — it is not in this payload's `nodes`, and no node with "
            "that id exists in the store under your ownership. Check the id (they are "
            "exact and type-prefixed), or author the node in this same call",
        "skipped_endpoint":
            "endpoint was declared but did not merge — it was skipped earlier in this same "
            "call, so its edges went down with it. Fix the node first: the reason is in the "
            "node warnings above (missing title, id owned by another tenant, and so on)",
        "unsupported_type": "unsupported edge type; " + frag._ALLOWED_PAIRS_HINT,
    }
    assert frag._EDGE_WARN_SAMPLE == 8
    assert mutation_contract.WRITE_CONFLICT_HINT == (
        "Nothing was changed: the stored version is not the one you read, or the id already "
        "exists. Read it again, merge your change, and resend with the current rev or hash.")


# ── 다시 기록하기 ──────────────────────────────────────────────────────────────

def _dump(golden: dict) -> str:
    """케이스마다 몇 줄로 적는다. 행·엣지 하나는 한 줄이라 차이가 케이스 단위로 읽힌다."""
    def one(v):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)

    lines = ["{"]
    sections = sorted(golden.items())
    for si, (section, cases) in enumerate(sections):
        if not isinstance(cases, dict):
            lines.append(f" {one(section)}: {one(cases)}" + ("," if si < len(sections) - 1 else ""))
            continue
        lines.append(f" {one(section)}: {{")
        items = sorted(cases.items())
        for ci, (name, out) in enumerate(items):
            lines.append(f"  {one(name)}: {{")
            keys = sorted(out)
            for ki, key in enumerate(keys):
                val = out[key]
                tail = "," if ki < len(keys) - 1 else ""
                if key == "nodes":
                    lines.append(f"   {one(key)}: {{")
                    labels = sorted(val)
                    for li, label in enumerate(labels):
                        lines.append(f"    {one(label)}: {{")
                        rows = sorted(val[label].items())
                        for ri, (nid, row) in enumerate(rows):
                            lines.append(f"     {one(nid)}: {one(row)}"
                                         + ("," if ri < len(rows) - 1 else ""))
                        lines.append("    }" + ("," if li < len(labels) - 1 else ""))
                    lines.append("   }" + tail)
                elif isinstance(val, list):
                    lines.append(f"   {one(key)}: [")
                    for vi, item in enumerate(val):
                        lines.append(f"    {one(item)}" + ("," if vi < len(val) - 1 else ""))
                    lines.append("   ]" + tail)
                else:
                    lines.append(f"   {one(key)}: {one(val)}{tail}")
            lines.append("  }" + ("," if ci < len(items) - 1 else ""))
        lines.append(" }" + ("," if si < len(sections) - 1 else ""))
    lines.append("}")
    return "\n".join(lines) + "\n"


def _record() -> None:
    os.environ.setdefault("MEMORY_STORE_BACKEND", "embedded")
    os.environ.setdefault("MEMORY_EMBEDDING_MODEL", "local-en")
    got = {}
    with tempfile.TemporaryDirectory() as tmp:
        for i, case in enumerate(CASES):
            st = _open_store(os.path.join(tmp, f"db{i}"))
            try:
                got[case.name] = run_case(case, st)
            finally:
                st.close()
    golden = _golden() if GOLDEN.exists() else {"plain": {}, "namespaced": {}}
    golden["_about"] = ("tests/core/test_fragment_merge_characterization.py 의 기록. plain 은 "
                        "PK 네임스페이싱을 끈 모드, namespaced 는 켠 모드에서 결과가 다른 케이스만.")
    if _mode() == "plain":
        golden["plain"] = got
        golden["namespaced"] = {k: v for k, v in golden.get("namespaced", {}).items() if k in got}
    else:
        golden["namespaced"] = {k: v for k, v in got.items() if golden["plain"].get(k) != v}
    GOLDEN.write_text(_dump(golden), encoding="utf-8")
    print(f"{_mode()}: {len(got)} cases, namespaced differs in {len(golden['namespaced'])}")


if __name__ == "__main__":
    if sys.argv[1:] != ["--record"]:
        sys.exit("usage: test_fragment_merge_characterization.py --record")
    _record()
