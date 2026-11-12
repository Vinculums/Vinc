"""MCP tools/call 은 search·brief·node·neighbors 결과를 줄여서 싣는다. 핸들러와 REST v1 은 그대로다.

줄이는 것: 순위 진단(rows 의 score, node_hits 의 rrf_score 는 남는다), 시간 블록의 차 있지 않은 칸과
날짜에서 되찾을 수 있는 칸, brief 의 평평한 시간 사본과 관계의 빈 칸, 노드 팔이 건강할 때의 진단,
vinc_node 의 빈 칸과 노드에 이미 있는 시간 값, neighbors 의 빈 칸·rev·되풀이한 도메인, search 의
id 와 같은 node hit cite_id 와 한 응답 안에서 되풀이한 문서 제목. search·neighbors 에서 120자를
넘는 제목은 "…" 로 자른다. vinc_brief 의 기본 본문은 main 과 바이트까지 같다.
`raw:true`(또는 "true")면 그대로 나간다. 합성 데이터만 쓴다.
"""
import copy
import json
import re

import pytest

from vinc_mcp import rpc, tools
from tests.mcp.test_rest_v1 import Server, TEAM


def _block(**over):
    t = {"created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-02-01T00:00:00Z",
         "content_time": None, "valid_from": None, "valid_until": None, "superseded_by": None,
         "validity": "unknown", "interval_known": False, "interval_invalid": False,
         "holds_in_scope": None, "supersession": [], "supersedes": [], "claim_status": "unscoped",
         "conflicts": []}
    t.update(over)
    return t


UPD = {"updated_at": "2026-02-01T00:00:00Z"}

#: (블록에 얹는 값, 줄인 뒤 남아야 하는 블록). updated_at 은 늘 남는다.
SLIM_CASES = [
    ({"valid_from": "2026-01", "validity": "current", "interval_known": True},
     {"valid_from": "2026-01", **UPD}),
    ({"valid_until": "2099", "validity": "current", "interval_known": True},
     {"valid_until": "2099", **UPD}),
    ({"valid_until": "2020", "validity": "historical", "interval_known": True},
     {"valid_until": "2020", "validity": "historical", **UPD}),
    ({"valid_from": "2099", "validity": "future", "interval_known": True},
     {"valid_from": "2099", "validity": "future", **UPD}),
    ({"valid_from": "nope", "interval_invalid": True},
     {"valid_from": "nope", "interval_invalid": True, **UPD}),
    ({"content_time": "1905"}, {"content_time": "1905", **UPD}),
    ({"superseded_by": "record:new"}, {"superseded_by": "record:new", **UPD}),
    ({"supersession": [{"by": "record:new"}]}, {"supersession": [{"by": "record:new"}], **UPD}),
    ({"supersedes": ["record:old"]}, {"supersedes": ["record:old"], **UPD}),
    ({"conflicts": [{"id": "record:other"}]}, {"conflicts": [{"id": "record:other"}], **UPD}),
    ({"claim_status": "date_unknown"}, {"claim_status": "date_unknown", **UPD}),
    ({"claim_status": "conflict"}, {"claim_status": "conflict", **UPD}),
    ({"holds_in_scope": True}, {"holds_in_scope": True, **UPD}),
    ({"holds_in_scope": False}, {"holds_in_scope": False, **UPD}),
    ({"claim_key": "x"}, {"claim_key": "x", **UPD}),
    # mode=current 의 행: 평가한 claim_status 와 대체 관계는 남는다.
    ({"valid_from": "2026-01", "validity": "current", "interval_known": True,
      "holds_in_scope": True, "claim_status": "candidate",
      "supersession": [{"by": "record:new", "evidence": True}]},
     {"valid_from": "2026-01", "holds_in_scope": True, "claim_status": "candidate",
      "supersession": [{"by": "record:new", "evidence": True}], **UPD}),
]

#: 되찾는 규칙이 돌려줘야 하는 칸(이것 말고는 시각 둘과 평가 범위뿐이다).
EXPANDED = ("content_time", "valid_from", "valid_until", "superseded_by", "validity",
            "interval_known", "interval_invalid", "holds_in_scope", "supersession",
            "supersedes", "claim_status", "conflicts")


def _search_result(rows=2, temporal=None):
    hit = {"id": "concept:a", "title": "A", "kind": "concept", "domain": "vinc/x",
           "matched_in": "title", "hit_tokens": 2, "title_tokens": 1, "token_total": 2,
           "rrf_score": 0.03, "arms": ["semantic", "title"], "distance": 0.5,
           "temporal": temporal or _block(), "prod_rank": 2, "colbert": 0.52,
           "sem_lead": 0.1, "sem_decisive": True, "cite_id": "concept:a"}
    return {"kind": "search", "executed": True,
            "rows": [{"id": f"doc.a#{i:05d}", "score": 4.1, "rrf_score": 0.08,
                      "arms": ["keyword", "semantic"], "snippet": "Marker " * 30, "truncated": True,
                      "doc_id": "doc.a", "temporal": copy.deepcopy(temporal or _block()),
                      "doc_title": "Doc A", "cite_id": "concept:doc.doc.a"} for i in range(rows)],
            "node_hits": [hit], "arms": "fts+vector", "total_count": rows, "has_more": False,
            "next_cursor": None, "validity_filtered": 3, "validity_best_dropped": [2, 1],
            "validity_best_dropped_distance": 0.4, "validity_note": tools.HIDDEN_NOTE,
            "node_window": {"pagination": "first_page_only", "returned": 1,
                            "lexical_candidates_capped": False},
            "total_count_capped": False, "page": {"offset": 0, "limit": rows, "fingerprint": "abc"},
            "node_semantic": {"state": "complete", "stale_candidates": 0, "missing_candidates": 0},
            "node_rescore": {"state": "applied", "n": 20}}


def _flat(t):
    return {k: t[k] for k in ("created_at", "updated_at", "content_time", "valid_from",
                              "valid_until", "superseded_by", "validity")}


def _choose_brief():
    cands = []
    for i in range(3):
        t = _block()
        cands.append({"id": f"concept:c{i}", "cite_id": f"concept:c{i}", "title": f"C{i}",
                      "domain": "vinc/x", "kind": "concept", "matched_in": "title",
                      "hit_tokens": 1, "title_tokens": 1, "temporal": t, **_flat(t)})
    return {"kind": "brief", "executed": True, "query": "what is c", "status": "choose",
            "candidates": cands, "grounds": [], "arms": "fts+vector",
            "validity_filtered": 1, "validity_note": tools.HIDDEN_NOTE, "validity_best_dropped": [1, 0],
            "resolution": {"rule": "v3", "p": 0.1},
            "candidate_material": {"candidate": "concept:c0", "one_liner": "C0 is c.",
                                   "body_src": "one_liner", "excerpts": []}}


def _link_item(i):
    t = _block()
    return {"id": f"concept:m{i}", "title": f"M{i}", "domain": "vinc/x", "type": "ABOUT",
            "dir": "out", "evidence": False, "confidence": None, "note": "",
            "evidence_text": "" if i else "M0 is about N.", "valid_from": "", "valid_until": "",
            "kind": "concept", "temporal": t, **_flat(t)}


def _resolved_brief():
    t = _block()
    node = {"id": "concept:n", "cite_id": "concept:n", "title": "N", "domain": "vinc/x",
            "kind": "concept", "valid_from": "", "valid_until": "", "validity": "unknown",
            "superseded_by": "", "temporal": t, "created_at": t["created_at"],
            "updated_at": t["updated_at"], "content_time": None,
            "validity_basis": "legacy_visibility", "claim_validity": "unknown"}
    ex_t = _block(valid_from="2026-01", validity="current", interval_known=True)
    return {"kind": "brief", "executed": True, "status": "resolved", "node": node,
            "one_liner": "N is n.", "excerpts": [{"doc_id": "d", "text": "body",
                                                  "temporal": ex_t, **_flat(ex_t)}],
            "links": {"degree": 2, "out": 2, "in": 0, "executed": True, "offset": 0,
                      "truncated": False, "items": [_link_item(0), _link_item(1)]},
            "grounds": ["concept:n", "d"], "cite_grounds": ["concept:n", "d"],
            "validity_note": tools.HIDDEN_NOTE, "validity_best_dropped": [0, 0]}


def _neighbors(node="concept:v", domain="vinc/x"):
    return {"kind": "neighbors", "executed": True, "found": True, "node": node,
            "neighbors": [{"rel": "about", "dir": "out", "id": f"concept:n{i}", "title": f"N{i}",
                           "domain": domain, "one_liner": "" if i else "has one",
                           "rev": 3, "updated_at": "2026-01-01" if i else "",
                           "valid_from": "", "valid_until": None} for i in range(3)],
            "by_rel": {"about": 3}, "count": 3, "neighbors_truncated": False, "error": None}


def _record_node(**over):
    """GET /node 이 기록 하나에 돌려주는 꼴(저장 칸 + 계산한 시간 블록)."""
    node = {"id": "record:r", "stem": None, "owner_id": "user:synthetic", "tenant_id": "tenant:local",
            "domain": "vinc/x", "topic": None, "title": "R holds", "one_liner": "",
            "status": None, "created": "2026-01-01T00:00:00Z", "tags": [], "source_file": None,
            "props": {"statement": "R holds.", "note": "", "empty": None},
            "content_time": "", "valid_from": "2026-01-05", "valid_until": "",
            "updated_at": "2026-02-01T00:00:00Z", "rev": 4, "sync_seq": 99, "kind": "record",
            "temporal": _block(valid_from="2026-01-05", validity="current", interval_known=True,
                               claim_status="unassessed",
                               claim_assessment_scope="stored_fields")}
    node.update(over)
    return {"kind": "node", "executed": True, "found": True, "node": node}


# ── 단위: 무엇을 빼고 무엇을 남기나 ─────────────────────────────────────────────

def test_undated_block_keeps_updated_at_and_scores_and_envelope_say_only_what_is_set():
    src = _search_result()
    keep = copy.deepcopy(src)
    out = tools.lean_result("vinc_search", {"query": "x"}, src)
    assert src == keep, "핸들러 결과를 고치면 안 된다"
    for row in out["rows"]:
        assert row["temporal"] == UPD
        assert row["score"] == 4.1 and "rrf_score" not in row and "arms" not in row
        # 행의 cite_id 는 남는다(행의 id 는 청크 id 라 인용할 수 없다).
        assert row["truncated"] is True and row["snippet"] and row["cite_id"]
        assert row["doc_id"] == "doc.a"
    # 같은 문서의 제목은 첫 행에만 실린다.
    assert [r.get("doc_title") for r in out["rows"]] == ["Doc A", None]
    hit = out["node_hits"][0]
    assert hit["rrf_score"] == 0.03 and hit["temporal"] == UPD and "cite_id" not in hit
    assert (hit["id"], hit["title"], hit["kind"], hit["domain"]) == ("concept:a", "A", "concept",
                                                                     "vinc/x")
    for k in ("matched_in", "hit_tokens", "title_tokens", "token_total", "arms", "distance",
              "prod_rank", "colbert", "sem_lead", "sem_decisive"):
        assert k not in hit, k
    # 고정 문장, 축소기 좌표, 건강한 노드 팔의 진단, 기본값인 상한 표시는 빠진다.
    for k in ("node_rescore", "validity_best_dropped", "validity_best_dropped_distance",
              "validity_note", "page", "node_window", "node_semantic", "total_count_capped"):
        assert k not in out, k
    for k in ("kind", "executed", "total_count", "arms", "has_more", "next_cursor",
              "validity_filtered"):
        assert out[k] == src[k], k
    assert out["omitted"] == tools.OMITTED_SEARCH_NOTE


def test_a_default_block_without_updated_at_goes_away():
    out = tools.lean_result("vinc_search", {}, _search_result(temporal=_block(updated_at=None)))
    assert all("temporal" not in r for r in out["rows"])


@pytest.mark.parametrize("over,slim", SLIM_CASES)
def test_a_block_keeps_exactly_what_is_set_and_the_rule_gives_the_rest_back(over, slim):
    src = _search_result(temporal=_block(**over))
    out = tools.lean_result("vinc_search", {"mode": "current"}, src)
    full = src["rows"][0]["temporal"]
    for entry in out["rows"] + out["node_hits"]:
        assert entry["temporal"] == slim
        back = tools.temporal_expand(entry["temporal"])
        assert {k: back[k] for k in EXPANDED} == {k: full[k] for k in EXPANDED}


def test_a_capped_or_degraded_node_arm_keeps_both_diagnostics_whole():
    for change in ({"node_window": {"lexical_candidates_capped": True}},
                   {"node_semantic": {"state": "partial", "stale_candidates": 2}},
                   {"node_semantic": {"missing_candidates": 1}},
                   {"node_semantic": {"state": "degraded", "error": "node_vector_query_failed"}},
                   {"node_semantic": {"state": "disabled"}},
                   {"node_error": "RuntimeError: x"}):
        src = _search_result()
        for k, v in change.items():
            src[k] = {**src[k], **v} if isinstance(src.get(k), dict) else v
        out = tools.lean_result("vinc_search", {}, src)
        assert out["node_semantic"] == src["node_semantic"], change
        assert out["node_window"] == src["node_window"], change
    no_semantic = _search_result()
    no_semantic.pop("node_semantic")
    assert tools.lean_result("vinc_search", {}, no_semantic)["node_window"] == \
        no_semantic["node_window"]
    capped = _search_result()
    capped["total_count_capped"] = True
    assert tools.lean_result("vinc_search", {}, capped)["total_count_capped"] is True


def test_raw_returns_the_result_untouched():
    for raw in (True, "true"):
        for name, src in (("vinc_search", _search_result()), ("vinc_brief", _choose_brief()),
                          ("vinc_neighbors", _neighbors()), ("vinc_node", _record_node())):
            assert tools.lean_result(name, {"raw": raw}, src) is src
    for raw in (False, "false", None):
        assert tools.lean_result("vinc_node", {"raw": raw}, _record_node()) != _record_node()
    other = {"kind": "graph", "temporal": _block()}
    assert tools.lean_result("vinc_graph", {}, other) is other


def test_choose_brief_drops_flat_copies_ranking_and_fixed_notes():
    src = _choose_brief()
    out = tools.lean_result("vinc_brief", {}, src)
    for k in ("resolution", "validity_note", "validity_best_dropped"):
        assert k not in out, k
    assert out["status"] == "choose" and out["grounds"] == [] and out["validity_filtered"] == 1
    assert [c["id"] for c in out["candidates"]] == [c["id"] for c in src["candidates"]]
    for c in out["candidates"]:
        assert set(c) == {"id", "cite_id", "title", "domain", "kind", "temporal"}, c
        assert c["temporal"] == UPD
    assert out["candidate_material"] == src["candidate_material"]
    assert out["omitted"] == tools.OMITTED_NOTE
    assert len(json.dumps(out)) < len(json.dumps(src)) / 2


def test_choose_brief_under_current_keeps_the_conflict():
    src = _choose_brief()
    src["candidates"][0]["temporal"]["claim_status"] = "conflict"
    src["candidates"][0]["temporal"]["conflicts"] = [{"id": "concept:c1"}]
    out = tools.lean_result("vinc_brief", {"mode": "current"}, src)
    got = out["candidates"][0]["temporal"]
    assert got == {"claim_status": "conflict", "conflicts": [{"id": "concept:c1"}], **UPD}
    full = src["candidates"][0]["temporal"]
    assert {k: tools.temporal_expand(got)[k] for k in EXPANDED} == {k: full[k] for k in EXPANDED}
    # 블록과 같은 평평한 사본만 빠진다.
    assert "validity" not in out["candidates"][0]


def test_resolved_brief_keeps_node_validity_the_dated_excerpt_and_the_evidence():
    src = _resolved_brief()
    out = tools.lean_result("vinc_brief", {}, src)
    node = out["node"]
    assert node["validity"] == "unknown" and node["claim_validity"] == "unknown"
    assert node["title"] == "N" and node["cite_id"] == "concept:n" and node["kind"] == "concept"
    assert node["temporal"] == UPD
    for k in ("valid_from", "valid_until", "superseded_by", "created_at", "updated_at", "content_time"):
        assert k not in node, k
    ex = out["excerpts"][0]
    assert ex["temporal"] == {"valid_from": "2026-01", **UPD} and not set(_flat(src["excerpts"][0])) & set(ex)
    assert ex["text"] == "body" and ex["doc_id"] == "d"
    links = out["links"]
    assert links == {"degree": 2, "out": 2, "in": 0, "items": links["items"]}
    first, second = links["items"]
    assert first == {"id": "concept:m0", "title": "M0", "domain": "vinc/x", "type": "ABOUT",
                     "dir": "out", "evidence_text": "M0 is about N.", "kind": "concept",
                     "temporal": UPD}
    assert "evidence_text" not in second and "evidence" not in second
    assert out["grounds"] == ["concept:n", "d"] and "cite_grounds" not in out
    assert "validity_note" not in out and "validity_best_dropped" not in out


def test_brief_keeps_what_differs():
    src = _resolved_brief()
    src["node"]["validity"] = "current"
    src["cite_grounds"] = ["concept:n"]
    src["links"].update(executed=False, offset=50, truncated=True)
    src["links"]["items"][0].update(confidence=0.0, evidence=True)
    out = tools.lean_result("vinc_brief", {}, src)
    assert out["node"]["validity"] == "current"
    assert out["cite_grounds"] == ["concept:n"]
    assert (out["links"]["executed"], out["links"]["offset"], out["links"]["truncated"]) == \
        (False, 50, True)
    assert out["links"]["items"][0]["confidence"] == 0.0
    assert out["links"]["items"][0]["evidence"] is True


def test_neighbors_drop_empty_keys_and_rev():
    src = _neighbors()
    out = tools.lean_result("vinc_neighbors", {}, src)
    assert "error" not in out and out["neighbors_truncated"] is False and out["count"] == 3
    assert out["by_rel"] == {"about": 3} and "omitted" not in out
    first, second = out["neighbors"][0], out["neighbors"][1]
    assert first == {"rel": "about", "dir": "out", "id": "concept:n0", "title": "N0",
                     "domain": "vinc/x", "one_liner": "has one"}
    assert second["updated_at"] == "2026-01-01" and "one_liner" not in second
    assert [r["id"] for r in out["neighbors"]] == [r["id"] for r in src["neighbors"]]


def test_a_domain_listing_does_not_repeat_its_domain():
    out = tools.lean_result("vinc_neighbors", {}, _neighbors(node="domain:vinc/x"))
    assert all("domain" not in r for r in out["neighbors"])
    other = _neighbors(node="domain:vinc/x", domain="vinc/x/child")
    assert all(r["domain"] == "vinc/x/child"
               for r in tools.lean_result("vinc_neighbors", {}, other)["neighbors"])
    # 개념의 이웃은 도메인을 그대로 싣는다.
    assert all(r["domain"] == "vinc/x"
               for r in tools.lean_result("vinc_neighbors", {}, _neighbors())["neighbors"])


def test_node_keeps_props_whole_and_leaves_out_empty_and_duplicated_fields():
    src = _record_node()
    keep = copy.deepcopy(src)
    out = tools.lean_result("vinc_node", {"node_id": "record:r"}, src)
    assert src == keep
    assert out["kind"] == "node" and out["executed"] is True and out["found"] is True
    node = out["node"]
    assert node == {"id": "record:r", "domain": "vinc/x", "title": "R holds",
                    "created": "2026-01-01T00:00:00Z", "props": src["node"]["props"],
                    "valid_from": "2026-01-05", "updated_at": "2026-02-01T00:00:00Z",
                    "rev": 4, "kind": "record", "temporal": {"validity": "current"}}
    assert node["props"] == {"statement": "R holds.", "note": "", "empty": None}
    assert out["omitted"] == tools.OMITTED_NODE_NOTE
    # 블록이 다 빠져도 노드의 날짜와 규칙이 블록을 되돌린다.
    t = {k: node[k] for k in ("valid_from", "valid_until", "content_time") if node.get(k)}
    back = tools.temporal_expand({**t, **node.get("temporal", {})}, claim_status="unassessed")
    full = src["node"]["temporal"]
    assert {k: back[k] for k in EXPANDED} == {k: full[k] for k in EXPANDED}


def test_node_keeps_what_the_stored_fields_do_not_hold():
    # 결정의 구간은 props 에 있고 블록만 그것을 평평하게 보여 준다: 블록에 남는다.
    src = _record_node(id="decision:d", kind="decision", valid_from="", tags=["a"],
                       props={"valid_from": "2020", "valid_until": "2021", "why": "x" * 900},
                       temporal=_block(valid_from="2020", valid_until="2021", validity="historical",
                                       interval_known=True, claim_status="unassessed",
                                       claim_assessment_scope="stored_fields"))
    node = tools.lean_result("vinc_node", {}, src)["node"]
    assert node["temporal"] == {"valid_from": "2020", "valid_until": "2021", "validity": "historical"}
    assert node["tags"] == ["a"] and node["props"] == src["node"]["props"]
    missing = {"kind": "node", "executed": True, "found": False, "node": None}
    assert tools.lean_result("vinc_node", {}, missing) == missing


def test_the_citation_manifest_reads_the_same_validity_over_mcp():
    """`scripts/refresh_graph_refs.py` 는 MCP 기본 본문으로 vinc_node 를 읽어 validity 와 후속을 적는다."""
    from api import refs
    for over in ({}, {"valid_from": "", "temporal": _block()},
                 {"valid_until": "2020", "superseded_by": "record:new",
                  "temporal": _block(valid_from="2026-01-05", valid_until="2020",
                                     validity="historical", superseded_by="record:new")},
                 {"temporal": _block(valid_from="2099", validity="future")}):
        src = _record_node(**over)
        lean = json.loads(rpc._mcp_text("vinc_node", {}, src))
        assert refs._validity_of(lean) == refs._validity_of(src), over


def test_props_are_never_walked():
    src = _record_node(props={"temporal": _block(), "note": "", "rows": [{"temporal": _block()}]})
    assert tools.lean_result("vinc_node", {}, src)["node"]["props"] == src["node"]["props"]
    brief = _resolved_brief()
    brief["node"]["props"] = {"temporal": _block()}
    assert tools.lean_result("vinc_brief", {}, brief)["node"]["props"] == {"temporal": _block()}


def test_the_note_states_the_validity_rule_the_slim_block_relies_on():
    """`omitted` 의 validity 문장을 그대로 따라 읽으면 모든 구간 꼴에서 저장 쪽 validity 가 나온다."""
    from store import search_temporal
    clause = "No validity: unknown if interval_invalid or no valid_from/until, else current."
    for note in (tools.OMITTED_NOTE, tools.OMITTED_SEARCH_NOTE):
        assert clause in note and "raw:true" in note
    # search 의 줄은 잘린 제목과 node hit 의 빠진 cite_id 를 읽는 법도 말한다. brief 의 줄은 말하지 않는다.
    assert "…=cut title" in tools.OMITTED_SEARCH_NOTE and "No cite_id: id." in tools.OMITTED_SEARCH_NOTE
    assert "…" not in tools.OMITTED_NOTE and "cite_id" not in tools.OMITTED_NOTE

    def read_as_the_note_says(t):
        if "validity" in t:
            return t["validity"]
        if t.get("interval_invalid") or not (t.get("valid_from") or t.get("valid_until")):
            return "unknown"
        return "current"
    for row in ({}, {"valid_from": "2026-01-01"}, {"valid_until": "2026-12-31"},
                {"valid_from": "2025-01-01", "valid_until": "2026-01-01"}, {"valid_from": "2027-01-01"},
                {"valid_from": "2026-13-45"}, {"valid_from": "2026-05-01", "valid_until": "2026-04-01"},
                {"content_time": "1905"}):
        for scope in (None, ("2026-01-01", "2026-12-31")):
            meta = search_temporal.metadata(row, now="2026-06-15T00:00:00Z", scope=scope)
            assert read_as_the_note_says(tools._slim_temporal(meta)) == meta["validity"], row
    # vinc_node 는 알려진 validity 를 남기므로 짧은 문장이다.
    assert "raw:true" in tools.OMITTED_NODE_NOTE and len(tools.OMITTED_NODE_NOTE) <= 70


#: search·neighbors 기본 본문이 자를 수 있는 제목 칸. 다른 칸의 값은 바뀌면 안 된다.
TITLE_KEYS = ("title", "doc_title")


def _is_cut(raw, lean):
    """lean 이 raw 를 규칙대로 자른 것인가: 120자를 넘던 값이 120자 이하로 "…" 에 끝나고 앞부분이 같다."""
    return (isinstance(raw, str) and isinstance(lean, str) and len(raw) > 120
            and len(lean) <= 120 and lean.endswith("…") and raw.startswith(lean[:-1]))


def _dropped(raw, lean, where=""):
    """raw 에 있고 lean 에 없는 칸을 (자리, 키, 값)으로 모은다. 남은 칸의 값이 바뀌었으면 그것도 낸다.
    제목 칸만은 규칙대로 잘렸을 수 있다(`_is_cut`)."""
    if isinstance(raw, dict) and isinstance(lean, dict):
        for k, v in raw.items():
            if k not in lean:
                yield where, k, v
            elif k == "props":
                assert lean[k] == v, where
            elif k == "temporal" or isinstance(v, (dict, list)):
                yield from _dropped(v, lean[k], f"{where}/{k}")
            elif k in TITLE_KEYS and lean[k] != v:
                assert _is_cut(v, lean[k]), (where, k, v, lean[k])
            else:
                assert lean[k] == v, (where, k, v, lean[k])
    elif isinstance(raw, list) and isinstance(lean, list):
        assert len(raw) == len(lean), where
        for i, (a, b) in enumerate(zip(raw, lean)):
            yield from _dropped(a, b, f"{where}[{i}]")
    else:
        assert raw == lean, where


def _rich_results():
    """빠질 수 있는 칸을 다 담은 결과들: 날짜 있는·없는 블록, mode=current 블록, 관계·재료가 있는 brief."""
    cur = _block(valid_from="2026-01", validity="current", interval_known=True, holds_in_scope=True,
                 claim_status="candidate", supersedes=["record:old"])
    search = _search_result(rows=2, temporal=_block(valid_from="2026-01", validity="current",
                                                    interval_known=True))
    search["node_hits"].append({**copy.deepcopy(search["node_hits"][0]), "id": "record:c",
                                "temporal": cur, "cite_id": "record:c"})
    search["page"]["row_offsets"] = {"doc.a#00000": 0}
    search["node_hits"][0]["title"] = LONG
    search["rows"][0]["doc_title"] = search["rows"][1]["doc_title"] = LONG
    choose = _choose_brief()
    choose["candidate_material"]["excerpts"] = [{"doc_id": "d", "excerpt": "e", "temporal": _block(),
                                                 **_flat(_block())}]
    choose["candidate_material"]["links"] = {"executed": True, "offset": 0, "truncated": False,
                                             "items": [_link_item(1)]}
    yield "vinc_search", search
    yield "vinc_brief", choose
    yield "vinc_brief", _resolved_brief()
    yield "vinc_node", _record_node(temporal=_block(valid_from="2026-01-05", validity="current",
                                                     interval_known=True, claim_status="unassessed",
                                                     claim_assessment_scope="stored_fields"))


#: "default" 로 빠지는 칸이 가질 수 있는 값. 다른 값이 빠지면 그것은 기본값이 아니다.
DEFAULTS = {"interval_invalid": (False,), "claim_status": ("unscoped", "unassessed"),
            "validity": ("unknown",), "validity_note": (tools.HIDDEN_NOTE,),
            "claim_assessment_scope": ("stored_fields", "retrieved_candidates"),
            "total_count_capped": (False,), "executed": (True,), "offset": (0,),
            "truncated": (False,), "evidence": (False,)}


def _at(obj, where):
    for part in [p for p in where.replace("[", "/[").split("/") if p]:
        obj = obj[int(part[1:-1])] if part.startswith("[") else obj[part]
    return obj


#: `tools.OMITTED_KINDS` 의 손 사본. 빼는 칸을 늘리거나 줄이거나 종류를 바꾸면 이 사본도 사람이 고친다.
DROP_TABLE = (
    ("vinc_search", "", "node_rescore", "diagnostic"),
    ("vinc_search", "", "validity_best_dropped", "diagnostic"),
    ("vinc_search", "", "validity_best_dropped_distance", "diagnostic"),
    ("vinc_search", "", "node_window", "diagnostic"),
    ("vinc_search", "", "node_semantic", "diagnostic"),
    ("vinc_search", "", "validity_note", "default"),
    ("vinc_search", "", "page", "diagnostic"),
    ("vinc_search", "", "total_count_capped", "default"),
    ("vinc_search", "/rows[]", "rrf_score", "diagnostic"),
    ("vinc_search", "/rows[]", "arms", "diagnostic"),
    ("vinc_search", "/rows[]", "doc_title", "duplicate"),
    ("vinc_search", "/node_hits[]", "cite_id", "derived"),
    ("vinc_search", "/node_hits[]", "matched_in", "diagnostic"),
    ("vinc_search", "/node_hits[]", "hit_tokens", "diagnostic"),
    ("vinc_search", "/node_hits[]", "title_tokens", "diagnostic"),
    ("vinc_search", "/node_hits[]", "token_total", "diagnostic"),
    ("vinc_search", "/node_hits[]", "arms", "diagnostic"),
    ("vinc_search", "/node_hits[]", "distance", "diagnostic"),
    ("vinc_search", "/node_hits[]", "prod_rank", "diagnostic"),
    ("vinc_search", "/node_hits[]", "colbert", "diagnostic"),
    ("vinc_search", "/node_hits[]", "sem_lead", "diagnostic"),
    ("vinc_search", "/node_hits[]", "sem_decisive", "diagnostic"),
    ("vinc_brief", "", "resolution", "diagnostic"),
    ("vinc_brief", "", "validity_best_dropped", "diagnostic"),
    ("vinc_brief", "", "validity_note", "fixed notes"),
    ("vinc_brief", "", "cite_grounds", "duplicate"),
    ("vinc_brief", "/candidates[]", "matched_in", "diagnostic"),
    ("vinc_brief", "/candidates[]", "hit_tokens", "diagnostic"),
    ("vinc_brief", "/candidates[]", "title_tokens", "diagnostic"),
    ("vinc_brief", "*links", "executed", "default"),
    ("vinc_brief", "*links", "offset", "default"),
    ("vinc_brief", "*links", "truncated", "default"),
    ("vinc_brief", "*links/items[]", "evidence", "default"),
    ("vinc_node", "/node", "sync_seq", "internal"),
    ("vinc_node", "/node", "owner_id", "internal"),
    ("vinc_node", "/node", "tenant_id", "internal"),
    ("vinc_node", "/node/temporal", "created_at", "repeated"),
    ("vinc_node", "/node/temporal", "updated_at", "repeated"),
    ("vinc_node", "/node/temporal", "valid_from", "repeated"),
    ("vinc_node", "/node/temporal", "valid_until", "repeated"),
    ("vinc_node", "/node/temporal", "content_time", "repeated"),
    ("vinc_node", "/node/temporal", "superseded_by", "repeated"),
    ("vinc_node", "/node/temporal", "interval_known", "repeated"),
    ("vinc_node", "/node/temporal", "validity", "unset"),
    ("vinc_node", "/node/temporal", "claim_status", "unset"),
    ("vinc_node", "/node/temporal", "claim_assessment_scope", "unset"),
    ("vinc_node", "/node/temporal", "interval_invalid", "unset"),
    ("vinc_neighbors", "/neighbors[]", "rev", "internal"),
    ("vinc_neighbors", "/neighbors[]", "domain", "repeated"),
    ("*", "*/temporal", "created_at", "created_at"),
    ("*", "*/temporal", "claim_assessment_scope", "default"),
    ("*", "*/temporal", "interval_invalid", "default"),
    ("*", "*/temporal", "claim_status", "default"),
    ("*", "*/temporal", "validity", "derived"),
    ("*", "*/temporal", "interval_known", "derived"),
    ("vinc_brief", "*", "created_at", "created_at"),
    ("vinc_brief", "*", "updated_at", "duplicate"),
    ("vinc_brief", "*", "content_time", "duplicate"),
    ("vinc_brief", "*", "valid_from", "duplicate"),
    ("vinc_brief", "*", "valid_until", "duplicate"),
    ("vinc_brief", "*", "superseded_by", "duplicate"),
    ("vinc_brief", "*", "validity", "derived"),
)


def _still_readable(raw, lean, where, key, value):
    """"duplicate" 로 빠진 값을 응답의 다른 자리에서 되읽을 수 있는가."""
    here = _at(lean, where)
    if value in ((here.get("temporal") or {}).get(key), lean.get("grounds")):
        return True
    if key == "doc_title":
        # 같은 doc_id 의 앞선 행이 (잘렸을 수 있는) 그 제목을 싣는다.
        i = int(where.rsplit("[", 1)[1][:-1])
        doc = raw["rows"][i]["doc_id"]
        return any(r.get("doc_id") == doc and r.get("doc_title") in (value, tools.cut_title(value))
                   for r in lean["rows"][:i])
    return False


def _kind(tool, where, key):
    import fnmatch
    for t, pat, k, kind in tools.OMITTED_KINDS:
        if k == key and t in (tool, "*") and fnmatch.fnmatchcase(where, pat):
            return kind
    raise KeyError((tool, where, key))


def test_the_drop_table_is_the_one_a_person_wrote():
    assert tools.OMITTED_KINDS == DROP_TABLE
    assert len(set((t, w, k) for t, w, k, _ in DROP_TABLE)) == len(DROP_TABLE)


def test_every_lean_drop_key_is_in_the_drop_table():
    import fnmatch
    for tool, drops in tools.LEAN_DROP.items():
        for where, keys in drops.items():
            at = "" if where == "" else f"/{where}[]"
            for k in keys:
                assert any(t == tool and kk == k and fnmatch.fnmatchcase(at, w)
                           for t, w, kk, _ in DROP_TABLE), (tool, at, k)


def test_every_key_left_out_falls_under_a_kind_the_note_names():
    notes = {"vinc_search": tools.OMITTED_SEARCH_NOTE.lower(), "vinc_brief": tools.OMITTED_NOTE.lower(),
             "vinc_node": tools.OMITTED_NODE_NOTE.lower()}
    for tool, _w, _k, kind in DROP_TABLE:
        for t in ((tool,) if tool != "*" else ("vinc_search", "vinc_brief")):
            if t in notes:
                assert kind in notes[t], (t, kind)
    seen = set()
    for name, raw in list(_rich_results()) + [("vinc_neighbors", _neighbors(node="domain:vinc/x"))]:
        lean = tools.lean_result(name, {}, raw)
        for where, key, value in _dropped(raw, lean):
            at = re.sub(r"\[\d+\]", "[]", where)
            if value is None or value in ("", [], {}):
                kind = "empty"
                assert name == "vinc_neighbors" or (
                    ("unset" if name == "vinc_node" else "empty") in notes[name]), (name, at, key)
            else:
                kind = _kind(name, at, key)
                if name in notes:
                    assert kind in notes[name], (name, at, key, value)
            if kind in ("default", "unset") and value not in (None, "", [], {}):
                assert any(value is d or (value == d and type(value) is type(d))
                           for d in DEFAULTS[key]), (where, key, value)
            if kind == "duplicate":
                # 같은 값이 남은 자리에서 한 번은 읽힌다(같은 객체의 블록이거나 봉투의 grounds).
                assert _still_readable(raw, lean, where, key, value), (where, key, value)
            seen.add(kind)
    kinds = seen
    assert kinds >= {kind for _t, _w, _k, kind in DROP_TABLE}, \
        {kind for _t, _w, _k, kind in DROP_TABLE} - kinds


def test_node_drops_only_unset_internal_and_repeated_keys():
    raw = next(r for n, r in _rich_results() if n == "vinc_node")
    lean = tools.lean_result("vinc_node", {}, raw)
    for where, key, value in _dropped(raw, lean):
        unset = value is None or value in ("", [], {}) or (key, value) in (
            ("claim_status", "unassessed"), ("interval_invalid", False),
            ("claim_assessment_scope", "stored_fields"), ("validity", "unknown"))
        internal = key in tools._NODE_DROP
        repeated = (where == "/node/temporal" and (raw["node"].get(key) == value
                    or key == "created_at" and raw["node"].get("created") == value
                    or key == "interval_known"))
        assert unset or internal or repeated, (where, key, value)


LONG = "Measured 2026-10-07 on the owner's laptop: " + "one epoch takes nine minutes " * 10 + "end"


def _words(n):
    """정확히 n 자인 영어 제목(단어 사이 빈칸)."""
    text = ("word " * n)[:n]
    return text[:-1] + "x" if text.endswith(" ") else text


def test_a_title_of_120_stays_and_121_is_cut_at_a_word_boundary():
    at, over = _words(120), _words(121)
    assert len(at) == 120 and len(over) == 121
    assert tools.cut_title(at) == at
    cut = tools.cut_title(over)
    assert _is_cut(over, cut) and len(cut) <= 120
    # 단어 경계: "…" 앞은 단어 하나가 통째로 끝난 자리다.
    assert over[len(cut) - 1] == " " and not cut[:-1].endswith(" ")
    assert tools.cut_title(LONG) != LONG and _is_cut(LONG, tools.cut_title(LONG))
    for v in (None, 3, ""):
        assert tools.cut_title(v) == v


def test_a_title_without_a_late_word_boundary_is_cut_hard_at_119():
    # 빈칸 없는 긴 낱말, 그리고 빈칸이 남길 몫의 앞 절반에만 있는 제목.
    assert tools.cut_title("x" * 300) == "x" * 119 + "…"
    early = "a " + "x" * 200
    assert tools.cut_title(early) == early[:119] + "…"


def test_a_title_that_would_cut_to_nothing_is_cut_hard_and_never_to_an_ellipsis_alone():
    # 경계에서 자르면 문장부호만 남는 제목: 글자 자리에서 잘라 낱말이 남는다.
    dots = "." * 70 + " " + "x" * 100
    assert tools.cut_title(dots) == dots[:119] + "…"
    # 빈칸·문장부호뿐인 제목도 "…" 하나가 되지 않는다(제목 앞부분 119자가 남는다).
    for text in (" " * 200, "!" * 200, ", " * 100):
        cut = tools.cut_title(text)
        assert cut == text[:119] + "…" and len(cut) == 120, repr(text[:4])


def test_long_titles_are_cut_in_neighbors_and_search_only():
    nb = _neighbors()
    nb["neighbors"][0]["title"] = LONG
    nb["neighbors"][1]["title"] = _words(120)
    out = tools.lean_result("vinc_neighbors", {}, nb)
    assert out["neighbors"][0]["title"] == tools.cut_title(LONG)
    assert out["neighbors"][1]["title"] == _words(120)
    search = _search_result()
    search["rows"][0]["doc_title"] = LONG
    search["node_hits"][0]["title"] = LONG
    out = tools.lean_result("vinc_search", {}, search)
    assert out["rows"][0]["doc_title"] == tools.cut_title(LONG)
    assert out["node_hits"][0]["title"] == tools.cut_title(LONG)
    # brief 와 vinc_node 의 제목은 어느 자리든 자르지 않는다(아래 brief 테스트가 더 넓게 본다).
    choose = _choose_brief()
    choose["candidates"][0]["title"] = LONG
    out = tools.lean_result("vinc_brief", {}, choose)
    assert out["candidates"][0]["title"] == LONG
    node = _record_node(title=LONG)
    assert tools.lean_result("vinc_node", {}, node)["node"]["title"] == LONG


def test_raw_returns_the_whole_titles_and_ids():
    search = _search_result()
    search["node_hits"][0]["title"] = LONG
    search["rows"][0]["doc_title"] = LONG
    nb = _neighbors()
    nb["neighbors"][0]["title"] = LONG
    for name, src in (("vinc_search", search), ("vinc_neighbors", nb)):
        out = tools.lean_result(name, {"raw": True}, src)
        assert out is src
    assert search["node_hits"][0]["title"] == LONG and search["rows"][1]["doc_title"] == "Doc A"
    assert search["node_hits"][0]["cite_id"] == "concept:a"


def test_korean_and_combining_titles_are_cut_by_characters():
    ko = "측정 결과: " + "한국어 제목은 글자 수로 자른다 " * 12
    cut = tools.cut_title(ko)
    assert len(ko) > 120 and _is_cut(ko, cut) and len(cut) <= 120
    assert cut[-2] != " " and len(cut.encode("utf-8")) > 120, "바이트가 아니라 글자로 센다"
    # 결합 부호(é = e + U+0301)와 조합형 한글(ᄒ+ᅡ+ᆫ) 가운데서는 자르지 않는다.
    for unit in ("e\u0301", "\u1112\u1161\u11ab", "\U0001F468\u200d\U0001F469"):
        text = unit * 200
        cut = tools.cut_title(text)
        assert len(cut) <= 120 and cut.endswith("…")
        body = cut[:-1]
        assert len(body) % len(unit) == 0 and body == unit * (len(body) // len(unit)), unit


def test_a_flag_is_not_split_and_a_skin_tone_keeps_its_base():
    flag, wave = "\U0001F1F0\U0001F1F7", "\U0001F44B\U0001F3FD"
    for unit in (flag, wave):
        body = tools.cut_title(unit * 100)[:-1]
        assert body == unit * 59, unit     # 119 자리는 짝의 가운데라 118 에서 자른다
    # 지역 표시 문자는 줄의 처음부터 둘씩 짝이다: 앞에 한 글자가 있으면 119 자리가 짝의 경계다.
    body = tools.cut_title("x" + flag * 100)[:-1]
    assert body == "x" + flag * 59 and len(body) == 119
    # 수식자 하나만 남거나 바탕 글자 없이 시작하는 일이 없다.
    for n in range(118, 125):
        body = tools.cut_title("y" * (n - 119) + wave * 100 if n > 119 else wave * 100)[:-1]
        assert not body.endswith("\U0001F44B") and body.count("\U0001F3FD") == body.count("\U0001F44B")


def test_neighbors_say_once_that_a_title_was_cut():
    nb = _neighbors()
    nb["neighbors"][0]["title"] = LONG
    nb["neighbors"][1]["title"] = LONG + " too"
    out = tools.lean_result("vinc_neighbors", {}, nb)
    assert out["omitted"] == tools.CUT_TITLE_NOTE and len(tools.CUT_TITLE_NOTE) < 60
    assert "vinc_node" in tools.CUT_TITLE_NOTE and "raw:true" in tools.CUT_TITLE_NOTE
    assert json.dumps(out, ensure_ascii=False).count(tools.CUT_TITLE_NOTE) == 1
    # 잘린 제목이 없으면 neighbors 는 여전히 `omitted` 를 싣지 않는다.
    assert "omitted" not in tools.lean_result("vinc_neighbors", {}, _neighbors())
    assert "omitted" not in tools.lean_result("vinc_neighbors", {"raw": True}, nb)


def test_doc_title_is_sent_once_per_document():
    src = _search_result(rows=3)
    other = copy.deepcopy(src["rows"][0])
    other.update(id="doc.b#00000", doc_id="doc.b", doc_title="Doc B", cite_id="concept:doc.doc.b")
    src["rows"].insert(1, other)
    out = tools.lean_result("vinc_search", {}, src)
    assert [(r["doc_id"], r.get("doc_title")) for r in out["rows"]] == [
        ("doc.a", "Doc A"), ("doc.b", "Doc B"), ("doc.a", None), ("doc.a", None)]
    # 행에 제목이 없던 문서는 그대로 없다.
    src["rows"][0].pop("doc_title")
    out = tools.lean_result("vinc_search", {}, src)
    assert out["rows"][2].get("doc_title") == "Doc A"


def test_search_leaves_out_only_a_node_hit_cite_id_equal_to_its_id():
    src = _search_result(rows=3)
    src["rows"][1]["cite_id"] = None
    src["rows"][1]["cite_unavailable"] = "document mirror missing or unreadable"
    src["rows"][2]["cite_id"] = "concept:doc.somewhere-else"
    src["node_hits"].append({**copy.deepcopy(src["node_hits"][0]), "id": "concept:b",
                             "cite_id": "concept:other"})
    src["node_hits"].append({**copy.deepcopy(src["node_hits"][0]), "id": "concept:c",
                             "cite_id": None, "cite_unavailable": "mirror missing"})
    out = tools.lean_result("vinc_search", {}, src)
    rows, hits = out["rows"], out["node_hits"]
    # 행의 cite_id 는 어느 꼴이든 그대로다(행의 id 는 청크 id 라 인용할 수 없다).
    assert [r["cite_id"] for r in rows] == [r["cite_id"] for r in src["rows"]]
    assert rows[0]["cite_id"] == "concept:doc.doc.a" and rows[1]["cite_unavailable"]
    assert "cite_id" not in hits[0] and hits[0]["id"] == src["node_hits"][0]["cite_id"]
    assert hits[1]["cite_id"] == "concept:other"
    assert hits[2]["cite_id"] is None and hits[2]["cite_unavailable"]


#: main 의 brief `omitted` 줄. brief 기본 본문은 main 과 바이트까지 같아야 하므로 상수를 읽지 않고
#: 글자로 박아 둔다(이 줄을 바꾸는 일은 brief 의 꼴을 바꾸는 일이라 따로 잰 뒤에 한다).
MAIN_BRIEF_NOTE = ("Omitted: empty, default, derived, duplicate, diagnostic keys, fixed notes, page, "
                   "created_at. No validity: unknown if interval_invalid or no valid_from/until, else "
                   "current. raw:true has all.")
T300 = (LONG * 3)[:300]
_TITLE_LIKE = ("title", "doc_title", "source", "body_cite")


def _quotes(n):
    return [{"doc_id": "d", "source": LONG, "section": "2.1", "cite_id": "concept:doc.d",
             "text": f"q{i}", "widened": i % 2 == 1} for i in range(n)]


def _brief_cases():
    """brief 의 여러 꼴: 재료가 있는 choose, 절이 있는 choose, 관계와 인용이 있는 resolved, none,
    모든 제목이 300자인 brief, 축소기가 발췌를 덜어낸 brief."""
    material = _choose_brief()
    material["candidate_material"].update(
        cite_id="concept:c0", one_liner="The text.", body_src="doc", body_cite=LONG + " · 2.1",
        excerpts=[{"doc_id": "d", "doc_title": LONG, "excerpt": "The text. ", "cite_id": "concept:doc.d",
                   "temporal": _block(), **_flat(_block())}],
        links={"executed": True, "offset": 0, "truncated": False,
               "items": [{**_link_item(1), "title": LONG}]})
    for c in material["candidates"]:
        c["quotes"] = _quotes(2)
    yield "choose with material", material
    section = _choose_brief()
    section["section"] = {"node_id": "concept:c1", "cite_id": "concept:c1", "doc_id": "d",
                          "title": LONG, "text": "section body"}
    section["grounds"] = ["concept:c1"]
    yield "choose with section", section
    resolved = _resolved_brief()
    resolved["links"]["items"][0]["title"] = LONG
    resolved["excerpts"][0].update(doc_title=LONG, cite_id="concept:doc.d")
    resolved["quotes"] = _quotes(3)
    yield "resolved with links and quotes", resolved
    yield "none", {"kind": "brief", "executed": True, "query": "nothing here", "status": "none",
                   "candidates": [], "grounds": [], "arms": "fts+vector", "validity_filtered": 2,
                   "validity_note": tools.HIDDEN_NOTE, "validity_best_dropped": [0, 0],
                   "resolution": {"rule": "v3", "p": 0.0}}
    everywhere = copy.deepcopy(material)
    everywhere["section"] = copy.deepcopy(section["section"])
    _walk_all(everywhere, lambda d, _w: d.update({k: T300 for k in ("title", "doc_title", "source")
                                                  if k in d}))
    yield "300-character titles everywhere", everywhere
    shrunk = _resolved_brief()
    shrunk["node"]["title"] = T300
    shrunk["quotes"] = _quotes(4)
    shrunk["excerpts"] = [{"doc_id": f"d{i}", "doc_title": T300, "cite_id": f"concept:doc.d{i}",
                           "text": "x" * 3000} for i in range(40)]
    yield "shrunk", shrunk


def _walk_all(node, fn, where=()):
    """모든 dict 에 `fn(dict, 자리)` 를 건다. 자리는 키와 번호의 튜플이다."""
    if isinstance(node, dict):
        fn(node, where)
        for k, v in node.items():
            _walk_all(v, fn, where + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_all(v, fn, where + (i,))


def _at_path(node, path):
    for step in path:
        try:
            node = node[step]
        except (KeyError, IndexError, TypeError):
            return None
    return node


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


@pytest.mark.parametrize("label,src", list(_brief_cases()), ids=lambda v: v if isinstance(v, str) else "")
def test_brief_default_text_is_as_on_main(label, src):
    """vinc_brief 의 기본 본문은 main 과 같다: 어느 제목도 "…" 로 자르지 않고, `widened` 는 값과 함께
    남고, `omitted` 줄은 main 의 글자 그대로다. 호스트가 받는 길(줄이기와 축소기)을 그대로 지난다."""
    text = rpc._mcp_text("vinc_brief", {}, src)
    reply = rpc._result_text(text, "vinc_brief", {}, src)
    assert not reply["isError"], reply
    out = json.loads(reply["content"][0]["text"])
    assert out["omitted"] == MAIN_BRIEF_NOTE == tools.OMITTED_NOTE
    assert ("shrunk" in out) == (label == "shrunk")
    before = set(_strings(src))
    assert all(t in before for t in _strings(out) if "\u2026" in t), label
    widened, titles = [], []
    _walk_all(src, lambda d, w: widened.append((w, d["widened"])) if "widened" in d else None)
    _walk_all(src, lambda d, w: titles.extend((w + (k,), d[k]) for k in _TITLE_LIKE if k in d))
    assert widened or label in ("choose with section", "none")
    for where, value in widened:
        got = _at_path(out, where)
        assert isinstance(got, dict) and got.get("widened") is value, (label, where)
    for where, value in titles:
        got = _at_path(out, where)
        # 축소기가 덜어낸 발췌만 없을 수 있다. 남은 제목은 받은 그대로다.
        assert got == value or (got is None and label == "shrunk" and where[0] == "excerpts"), \
            (label, where)
    assert "\u2026" not in json.dumps(out, ensure_ascii=False)


def test_failures_and_notes_in_the_envelope_survive():
    search = _search_result()
    extra = {"error": "index_missing", "vector_note": "meaning arm timed out", "answer_note": "a",
             "search_error": "s", "partial": True, "node_error": "RuntimeError: x"}
    search.update(extra)
    out = tools.lean_result("vinc_search", {}, search)
    assert {k: out[k] for k in extra} == extra
    brief = _choose_brief()
    extra = {"vector_note": "v", "search_error": "s", "partial": True, "unreadable": ["d"],
             "fell_through": True, "resolved_via": "id", "shrunk": {"excerpts_dropped": 1}}
    brief.update(extra)
    out = tools.lean_result("vinc_brief", {}, brief)
    assert {k: out[k] for k in extra} == extra


def test_an_untrimmed_result_carries_no_omitted_note():
    bare = {"kind": "search", "executed": True, "rows": [], "has_more": False, "next_cursor": None}
    assert tools.lean_result("vinc_search", {}, bare) == bare


def test_the_slim_rule_gives_back_every_store_interval():
    """`store.search_temporal.metadata` 의 모든 구간 꼴에서: 줄인 블록을 규칙대로 펼치면 validity·
    interval_known·claim_status 와 나머지 기본값이 그대로 돌아온다. 저장 쪽이 칸을 더하면 여기서 깨진다."""
    from store import search_temporal
    assert tools._TEMPORAL_FLAT == search_temporal.FLAT
    now = "2026-06-15T00:00:00Z"
    rows = {
        "none": {},
        "from only": {"valid_from": "2026-01-01"},
        "until only": {"valid_until": "2026-12-31"},
        "past": {"valid_from": "2025-01-01", "valid_until": "2026-01-01"},
        "future": {"valid_from": "2027-01-01"},
        "invalid": {"valid_from": "2026-13-45"},
        "reversed": {"valid_from": "2026-05-01", "valid_until": "2026-04-01"},
        "content time": {"content_time": "1905"},
        "superseded": {"superseded_by": "record:new", "valid_from": "2026-01-01"},
    }
    seen = set()
    for label, row in rows.items():
        for scope in (None, ("2026-01-01", "2026-12-31")):
            meta = search_temporal.metadata({**row, "updated_at": "2026-02-01T00:00:00Z",
                                             "created_at": "2026-01-01T00:00:00Z"},
                                            now=now, scope=scope)
            assert set(meta) <= set(EXPANDED) | {"created_at", "updated_at"}, label
            back = tools.temporal_expand(tools._slim_temporal(meta))
            assert {k: back[k] for k in EXPANDED} == {k: meta[k] for k in EXPANDED}, label
            seen.add(meta["validity"])
            # vinc_node 의 블록: 평가하지 않았다는 같은 뜻을 unassessed 로 적는다.
            node_meta = {**meta, "claim_status": "unassessed",
                         "claim_assessment_scope": "stored_fields"}
            back = tools.temporal_expand(tools._slim_temporal(node_meta), claim_status="unassessed")
            assert {k: back[k] for k in EXPANDED} == {k: node_meta[k] for k in EXPANDED}, label
    assert seen == {"current", "historical", "future", "unknown"}


# ── 전송: MCP 만 바뀌고 REST v1 은 같다 ─────────────────────────────────────────

def _mcp(server, name, args):
    rpc_msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": name, "arguments": {**args, "space": TEAM}}}
    status, _, body = server.request("POST", "/mcp", body=rpc_msg)
    assert status == 200 and not body["result"]["isError"], body
    return body["result"]["content"][0]["text"]


@pytest.mark.parametrize("name,method,path,args,result", [
    ("vinc_search", "POST", "/v1/search", {"query": "x"}, _search_result()),
    ("vinc_brief", "POST", "/v1/brief", {"query": "x"}, _choose_brief()),
    ("vinc_brief", "POST", "/v1/brief", {"query": "x"}, _resolved_brief()),
    ("vinc_node", "GET", "/v1/nodes/record:r", {"node_id": "record:r"}, _record_node()),
    ("vinc_neighbors", "GET", "/v1/nodes/concept:v/neighbors", {"node": "concept:v"}, _neighbors()),
])
def test_rest_v1_keeps_the_full_shape_while_mcp_trims(name, method, path, args, result):
    from vinc_mcp import roles
    server = Server(role="viewer", scope="read")
    try:
        server.bridge.override = copy.deepcopy(result)
        body = args if method == "POST" else None
        status, _, rest = server.request(method, f"{path}?space={TEAM}", body=body)
        assert status == 200 and rest == result
        text = _mcp(server, name, args)
        assert json.loads(text) == tools.lean_result(name, args, result)
        assert ", " not in text[:200] and '":' in text           # 빈칸 없는 JSON
        # raw 는 줄이기 전과 바이트까지 같은 본문이다(문자열 "true" 도 같다).
        for raw in (True, "true"):
            assert _mcp(server, name, {**args, "raw": raw}) == roles.serialize_result(result)
        # MCP 를 거친 뒤에도 REST 는 같은 꼴이다(핸들러 결과를 고치지 않는다).
        status, _, again = server.request(method, f"{path}?space={TEAM}", body=body)
        assert again == result
    finally:
        server.close()


@pytest.mark.parametrize("name,args,result", [
    ("vinc_search", {"query": "x"}, _search_result()),
    ("vinc_brief", {"query": "x"}, _resolved_brief()),
    ("vinc_node", {"node_id": "record:r"}, _record_node()),
    ("vinc_neighbors", {"node": "concept:v"}, _neighbors()),
])
def test_handler_output_is_unchanged(name, args, result):
    class Bridge:
        owner = "user:synthetic"
        def request(self, method, path, params=None, body=None, **_kw):
            assert "raw" not in (params or {}) and "raw" not in (body or {})
            return copy.deepcopy(result)
    assert tools.call(name, args, Bridge()) == result
    assert tools.call(name, {**args, "raw": True}, Bridge()) == result


def test_raw_is_one_short_boolean_on_the_four_tools():
    served = {s["name"]: s for s in tools.specs(hub=True)}
    props = [served[n]["inputSchema"]["properties"]["raw"]
             for n in ("vinc_search", "vinc_brief", "vinc_node", "vinc_neighbors")]
    for prop in props:
        assert prop == props[0]
        assert prop["type"] == "boolean" and "x-reference" not in prop
        assert len(prop["description"]) <= 140
    assert all("raw" not in s["inputSchema"]["properties"] for s in served.values()
               if s["name"] not in tools.LEAN_TOOLS)


def test_raw_as_a_string_passes_argument_checks():
    for name, args in (("vinc_search", {"query": "x"}), ("vinc_node", {"node_id": "record:r"})):
        tools.validate_args(name, {**args, "raw": "true"})


# ── 크기: 실제와 같은 꼴의 픽스처에서 기본 본문이 얼마나 줄었나 ─────────────────────

def _dated(i):
    if i % 3 == 0:
        return _block(updated_at=f"2026-0{1 + i % 9}-02T03:04:05.678Z")
    if i % 3 == 1:
        return _block(valid_from="2026-09-0%d" % (1 + i % 9), validity="current",
                      interval_known=True, updated_at="2026-10-14T16:24:27.973Z",
                      created_at="2026-10-06T17:42:28.155Z")
    return _block(content_time="2026-10-07", updated_at="2026-10-14T16:24:27.973Z",
                  created_at="2026-10-06T17:42:28.155Z")


#: 실제 그래프의 긴 제목 꼴(주장 문장인 제목, 200자). 제목을 자르는 몫이 크기 목표에 드러나게 한다.
T200 = ("Measured 2026-10-07 on the owner's laptop: one epoch of the Laya model takes nine minutes on "
        "CPU torch with sixteen gigabytes, and the loss it reports is a raw score rather than a "
        "probability, so the curve is not comparable")[:200]
assert len(T200) == 200


def _realistic_search():
    rows = [{"id": f"vinc.plan.some-plan-{i % 7}#{i:05d}", "score": 7.86 - i / 10,
             "rrf_score": 0.095 - i / 1000, "arms": ["keyword", "semantic"],
             "snippet": "…as untrusted data, not instructions, because record notes and props are "
                        "authored content and prompt injection through hydrated graph text is the "
                        "report's most concrete security point, confirmed by the house rule.",
             "truncated": True, "doc_id": f"vinc.plan.some-plan-{i % 7}", "temporal": _dated(i),
             "doc_title": T200,
             "cite_id": f"concept:doc.vinc.plan.some-plan-{i % 7}"} for i in range(20)]
    hits = [{"id": f"record:measured-something-about-the-graph-{i}-2026-09-05",
             "title": T200,
             "kind": "record", "domain": "vinc/agent/orchestration", "matched_in": "title",
             "hit_tokens": 6, "title_tokens": 2, "token_total": 8, "rrf_score": 0.0292,
             "arms": ["semantic", "title"], "distance": 0.52, "temporal": _dated(i + 1),
             "cite_id": f"record:measured-something-about-the-graph-{i}-2026-09-05"}
            for i in range(20)]
    out = _search_result()
    out.update(rows=rows, node_hits=hits, total_count=200, has_more=True,
               next_cursor="eyJ2IjoxLCJvZmYiOjIwLCJxaCI6IjBkOWQzNTk3MmIzMCJ9",
               total_count_capped=True,
               validity_note="hid nodes whose claim is no longer current; pass historical=true "
                             "to include them",
               node_window={"pagination": "first_page_only", "total_count_scope": "chunks",
                            "limit": 20, "lexical_candidates": 901,
                            "lexical_candidates_capped": False, "candidate_limit": 2000,
                            "returned": 20},
               page={"offset": 0, "limit": 20, "fingerprint": "0d9d35972b30"},
               node_semantic={"state": "complete", "freshness": "checked_candidates",
                              "refresh": "release_boundary", "scope": "returned_candidates",
                              "stale_candidates": 0, "missing_candidates": 0,
                              "check": "source_text"})
    return out


def _realistic_choose():
    cands = []
    for i in range(5):
        t = _dated(i + 1)
        cands.append({"id": f"record:laya-runs-locally-on-the-owners-laptop-{i}-2026-10-07",
                      "cite_id": f"record:laya-runs-locally-on-the-owners-laptop-{i}-2026-10-07",
                      "title": "Measured 2026-10-07 on the owner's laptop: one epoch takes nine "
                               "minutes on CPU torch and the loss is not a probability",
                      "domain": "vinc/ai/eval/laya/training", "kind": "record",
                      "matched_in": "semantic", "hit_tokens": 0, "title_tokens": 0,
                      "temporal": t, **_flat(t),
                      "quotes": [{"text": "One epoch takes nine minutes on the owner's laptop.",
                                  "source": "Laya training notes", "cite_id": "concept:doc.laya"}]})
    ex_t = _dated(0)
    return {"kind": "brief", "executed": True, "query": "how long does laya train",
            "status": "choose", "candidates": cands, "grounds": [], "arms": "fts+vector",
            "validity_filtered": 2,
            "validity_note": "hid nodes whose claim is no longer current; pass historical=true "
                             "to include them",
            "validity_best_dropped": [1, 0],
            "candidate_material": {
                "candidate": cands[0]["id"],
                "one_liner": "Laya trains locally on CPU in about nine minutes per epoch.",
                "body_src": "one_liner",
                "excerpts": [{"doc_id": "vinc.research.laya", "cite_id": "concept:doc.laya",
                              "excerpt": "The run used CPU torch with sixteen gigabytes. " * 3,
                              "temporal": ex_t, **_flat(ex_t)} for _ in range(2)]}}


#: 이 두 픽스처를 search 제목 단계 없이 줄인 기본 본문 바이트 수(choose 는 main 의 lean_result 로 잰 값).
#: 픽스처를 고치면 다시 잰다. search 상한은 지금 규칙으로 잰 비율(0.726)이다.
BEFORE = {"search": 23239, "choose": 4737}


@pytest.mark.parametrize("label,name,result,ceiling", [
    ("search", "vinc_search", _realistic_search(), 0.73),
    ("choose", "vinc_brief", _realistic_choose(), 0.75),
])
def test_the_default_text_is_smaller_by_the_package_target(label, name, result, ceiling):
    out = json.loads(rpc._mcp_text(name, {}, result))
    # 목표는 `omitted` 한 줄을 빼고 센다(명세의 목표가 그 줄을 셈에 넣지 않았다). 그 줄의 길이는 따로 묶는다.
    assert out.pop("omitted") == (tools.OMITTED_SEARCH_NOTE if label == "search" else tools.OMITTED_NOTE)
    size = len(json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode())
    assert size <= ceiling * BEFORE[label], (size, BEFORE[label])


def test_the_notes_stay_short():
    assert len(tools.OMITTED_NOTE) <= 190 and len(tools.OMITTED_SEARCH_NOTE) <= 190
    assert len(tools.OMITTED_NODE_NOTE) <= 70


# ── 축소기: 줄인 뒤에도 페이지가 이어진다 ──────────────────────────────────────

class _BigBridge:
    owner = "user:synthetic"

    def __init__(self, result):
        self.result = result

    def request(self, method, path, params=None, body=None, **_kw):
        return copy.deepcopy(self.result)


def test_shrink_still_pages_after_the_trim():
    big = _search_result(rows=100)
    for i, row in enumerate(big["rows"]):
        row["snippet"] = f"{i} " + "x" * 900
    big["total_count"] = 100
    big["page"]["row_offsets"] = {r["id"]: i for i, r in enumerate(big["rows"])}
    assert len(json.dumps(tools.lean_result("vinc_search", {}, big))) > rpc.MAX_RESULT_CHARS
    d = rpc.Dispatcher(_BigBridge(big))
    reply = d.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": "vinc_search", "arguments": {"query": "x"}}})
    result = reply["result"]
    assert not result["isError"], result
    text = result["content"][0]["text"]
    assert len(text) <= rpc.MAX_RESULT_CHARS and ", " not in text[:300]
    out = json.loads(text)
    assert "page" not in out and "row_offsets" not in text
    assert out["omitted"] == tools.OMITTED_SEARCH_NOTE
    kept = len(out["rows"])
    assert 0 < kept < 100 and out["has_more"] and out["next_cursor"]
    cursor = json.loads(__import__("base64").urlsafe_b64decode(out["next_cursor"] + "==="))
    assert cursor["off"] == kept and cursor["qh"] == "abc" and cursor["eid"] == big["rows"][kept]["id"]
    assert all(r["temporal"] == UPD for r in out["rows"])
    assert all("rrf_score" not in r for r in out["rows"])
    # 줄인 덕에 줄이지 않았을 때보다 행이 더 실린다.
    d_raw = rpc.Dispatcher(_BigBridge(big))
    raw = json.loads(d_raw.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "vinc_search", "arguments": {"query": "x", "raw": True}}})["result"]["content"][0]["text"])
    assert len(raw["rows"]) < kept and "rrf_score" in raw["rows"][0] and raw["page"]


def test_a_shrunk_page_keeps_the_doc_title_on_the_first_row_of_each_document():
    big = _search_result(rows=100)
    for i, row in enumerate(big["rows"]):
        doc = f"doc.{i % 7}"
        row.update(id=f"{doc}#{i:05d}", doc_id=doc, cite_id=f"concept:doc.{doc}",
                   doc_title=f"{doc} " + T200, snippet=f"{i} " + "x" * 900)
    big["node_hits"][0]["title"] = T200
    big["page"]["row_offsets"] = {r["id"]: i for i, r in enumerate(big["rows"])}
    assert len(json.dumps(tools.lean_result("vinc_search", {}, big))) > rpc.MAX_RESULT_CHARS
    reply = rpc.Dispatcher(_BigBridge(big)).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                    "params": {"name": "vinc_search",
                                                               "arguments": {"query": "x"}}})
    assert not reply["result"]["isError"], reply
    out = json.loads(reply["result"]["content"][0]["text"])
    kept = len(out["rows"])
    assert 7 < kept < 100 and out["has_more"] and out["next_cursor"]
    cursor = json.loads(__import__("base64").urlsafe_b64decode(out["next_cursor"] + "==="))
    assert cursor["off"] == kept and cursor["eid"] == big["rows"][kept]["id"]
    seen = set()
    for row in out["rows"]:
        first = row["doc_id"] not in seen
        seen.add(row["doc_id"])
        assert ("doc_title" in row) == first, row["id"]
        if first:
            assert row["doc_title"] == tools.cut_title(f'{row["doc_id"]} ' + T200)
        assert row["cite_id"] == f'concept:doc.{row["doc_id"]}'  # 행의 cite_id 는 남는다
    assert out["node_hits"][0]["title"] == tools.cut_title(T200)


def test_the_deploy_probe_reads_the_full_shape():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import verify_search_temporal_remote as probe
    sent = []

    def fake(request, timeout):
        sent.append(json.loads(request.data))
        return {"result": {"content": [{"type": "text", "text": "{}"}]}}
    old = probe._json_read
    probe._json_read = fake
    try:
        probe.read_mcp({"mode": "current"}, "synthetic", 30)
    finally:
        probe._json_read = old
    assert sent[0]["params"]["arguments"]["raw"] is True


# ── 실제 스토어: 모드마다 MCP 결과가 핸들러 결과를 같은 규칙으로 줄인다 ──────────────

from tests.mcp.test_search_temporal_once import MODES, OWNER, app  # noqa: E402,F401  (모듈 픽스처)


def _over_mcp(app, name, args):
    from api.cli_tools import _InProcessBridge
    bridge = _InProcessBridge(app)
    bridge.owner = OWNER
    reply = rpc.Dispatcher(bridge).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                           "params": {"name": name, "arguments": args}})
    assert not reply["result"]["isError"], reply
    return json.loads(reply["result"]["content"][0]["text"]), tools.call(name, args, bridge)


@pytest.mark.parametrize("kw", MODES)
def test_real_search_keeps_every_temporal_value_the_rule_cannot_give_back(app, kw):
    mcp, handler = _over_mcp(app, "vinc_search", {"query": "Marker", "limit": 20, **kw})
    entries = lambda r: (r.get("rows") or []) + (r.get("node_hits") or [])  # noqa: E731
    assert [e["id"] for e in entries(mcp)] == [e["id"] for e in entries(handler)]
    dated = 0
    for got, full in zip(entries(mcp), entries(handler)):
        t = full["temporal"]
        assert got.get("temporal", {}) == tools._slim_temporal(t)
        back = tools.temporal_expand(got.get("temporal", {}))
        assert {k: back[k] for k in EXPANDED} == {k: t[k] for k in EXPANDED}
        dated += bool(t["valid_from"] or t["valid_until"] or t["content_time"])
    assert dated, "합성 데이터에는 구간이나 내용 시점을 단 행이 있다"
    if kw.get("mode") == "current":
        # current 는 오늘의 범위를 준다: 행마다 그 범위에 드는지(holds_in_scope)가 남는다.
        assert all(e["temporal"].get("holds_in_scope") is not None for e in entries(mcp))


def test_real_brief_over_mcp(app):
    mcp, handler = _over_mcp(app, "vinc_brief", {"query": "Marker"})
    assert mcp["status"] == handler["status"]
    assert [c["id"] for c in mcp.get("candidates") or []] == \
        [c["id"] for c in handler.get("candidates") or []]
    assert len(json.dumps(mcp)) < len(json.dumps(handler))


def test_real_node_over_mcp(app):
    _, found = _over_mcp(app, "vinc_search", {"query": "Marker", "limit": 5})
    nid = next(h["id"] for h in found.get("node_hits") or [])
    mcp, handler = _over_mcp(app, "vinc_node", {"node_id": nid})
    assert mcp["found"] is True and mcp["node"]["id"] == nid
    assert mcp["node"]["props"] == handler["node"]["props"]
    assert mcp["node"].get("rev") == handler["node"].get("rev")
    assert "sync_seq" not in mcp["node"] and "owner_id" not in mcp["node"]
