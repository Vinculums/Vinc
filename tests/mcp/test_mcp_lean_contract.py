"""MCP 기본 본문의 계약을 바깥에서 고정한다: 남아야 하는 칸, 칸의 순서, 실제 꼴 전체의 같음.

`test_mcp_lean_reads.py` 가 규칙 하나하나를 보고, 여기서는 줄인 결과를 줄이는 코드와 따로 쓴 작은
함수(`_expected`)로 다시 만들어 본문 문자열째 대조한다. 실제 꼴은 고정 평가 스토어의 핸들러 결과를
줄여 담은 `fixtures/lean_real/*.json` 이다.
"""
import copy
import json
from pathlib import Path

import pytest

from vinc_mcp import rpc, tools

REAL = Path(__file__).parent / "fixtures" / "lean_real"


def _text(o) -> str:
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))


# ── 따로 쓴 기대값: 명세의 규칙을 줄이는 코드와 상관없이 다시 적는다 ───────────────────

FIXED = "hid nodes whose claim is no longer current; pass historical=true to include them"
SEARCH_TOP = ("node_rescore", "validity_best_dropped", "validity_best_dropped_distance", "page")
SEARCH_ROW = ("rrf_score", "arms")
SEARCH_HIT = ("matched_in", "hit_tokens", "title_tokens", "token_total", "arms", "distance",
              "prod_rank", "colbert", "sem_lead", "sem_decisive")
BRIEF_TOP = ("resolution", "validity_best_dropped")
BRIEF_CAND = ("matched_in", "hit_tokens", "title_tokens")
FLAT = ("created_at", "updated_at", "content_time", "valid_from", "valid_until", "superseded_by",
        "validity")


def _blank(v):
    return v is None or v == "" or v == [] or v == {}


def _block(t, *, keep_known_validity=False):
    dates = bool(t.get("valid_from") or t.get("valid_until"))
    bad = t.get("interval_invalid") is True
    derived = "unknown" if bad or not dates else "current"
    out = {}
    for k, v in t.items():
        if k in ("created_at", "claim_assessment_scope"):
            continue
        if k in ("content_time", "valid_from", "valid_until", "superseded_by", "holds_in_scope",
                 "updated_at") and v in (None, ""):
            continue
        if k in ("supersession", "supersedes", "conflicts") and v == []:
            continue
        if k == "interval_invalid" and v is False:
            continue
        if k == "claim_status" and v in ("unscoped", "unassessed"):
            continue
        if k == "interval_known" and v == (dates and not bad):
            continue
        if k == "validity" and v == derived and not (keep_known_validity and v != "unknown"):
            continue
        out[k] = v
    return out


def _walk_blocks(o, flat):
    if isinstance(o, dict):
        t = o.get("temporal")
        if isinstance(t, dict):
            if flat:
                for k in FLAT:
                    if k in o and k in t and (o[k] or None) == (t[k] or None) \
                            and not (k == "validity" and "claim_validity" in o):
                        del o[k]
            slim = _block(t)
            if slim:
                o["temporal"] = slim
            else:
                del o["temporal"]
        for k, v in o.items():
            if k != "props":
                _walk_blocks(v, flat)
    elif isinstance(o, list):
        for v in o:
            _walk_blocks(v, flat)


def _links(links):
    if not isinstance(links, dict):
        return
    for it in links.get("items") or []:
        for k in [k for k, v in it.items() if v is None or v is False or v == ""]:
            del it[k]
    for k, d in (("executed", True), ("truncated", False)):
        if links.get(k) is d:
            del links[k]
    if type(links.get("offset")) is int and links["offset"] == 0:
        del links["offset"]


def _expected(tool, raw):
    """명세의 꼴: 원래 결과에서 빠지는 칸만 지운 사본(순서는 원래대로)과 omitted 한 줄."""
    o = copy.deepcopy(raw)
    if tool == "vinc_neighbors":
        dom = o["node"][len("domain:"):] if str(o.get("node", "")).startswith("domain:") else None
        for k in [k for k, v in o.items() if v is None or v == ""]:
            del o[k]
        for r in o.get("neighbors") or []:
            for k in [k for k, v in r.items() if v is None or v == "" or k == "rev"]:
                del r[k]
            if dom is not None and r.get("domain") == dom:
                del r["domain"]
        return o
    if tool == "vinc_node":
        n = o.get("node")
        if isinstance(n, dict):
            t = n.get("temporal")
            if isinstance(t, dict):
                slim = _block(t, keep_known_validity=True)
                for k in ("valid_from", "valid_until", "content_time", "superseded_by", "updated_at"):
                    if k in slim and (n.get(k) or None) == slim[k]:
                        del slim[k]
                if slim:
                    n["temporal"] = slim
                else:
                    del n["temporal"]
            for k in [k for k, v in n.items() if k in ("sync_seq", "owner_id", "tenant_id")
                      or (k != "props" and _blank(v))]:
                del n[k]
        note = tools.OMITTED_NODE_NOTE
    elif tool == "vinc_search":
        for k in SEARCH_TOP:
            o.pop(k, None)
        if o.get("validity_note") == FIXED:
            del o["validity_note"]
        if o.get("total_count_capped") is False:
            del o["total_count_capped"]
        ns, nw = o.get("node_semantic"), o.get("node_window")
        if (isinstance(ns, dict) and ns.get("state") == "complete" and not ns.get("error")
                and not ns.get("stale_candidates") and not ns.get("missing_candidates")
                and not o.get("node_error") and not (nw or {}).get("lexical_candidates_capped")):
            o.pop("node_semantic")
            o.pop("node_window", None)
        for r in o.get("rows") or []:
            for k in SEARCH_ROW:
                r.pop(k, None)
        for r in o.get("node_hits") or []:
            for k in SEARCH_HIT:
                r.pop(k, None)
        _walk_blocks(o, flat=False)
        note = tools.OMITTED_NOTE
    else:
        for k in BRIEF_TOP:
            o.pop(k, None)
        if o.get("validity_note") == FIXED:
            del o["validity_note"]
        if "cite_grounds" in o and o["cite_grounds"] == o.get("grounds"):
            del o["cite_grounds"]
        for c in o.get("candidates") or []:
            for k in BRIEF_CAND:
                c.pop(k, None)
        _links(o.get("links"))
        _links((o.get("candidate_material") or {}).get("links"))
        _walk_blocks(o, flat=True)
        note = tools.OMITTED_NOTE
    o.pop("page", None)
    if o != raw:
        o["omitted"] = note
    return o


REAL_CASES = [("vinc_search", "search.json"), ("vinc_brief", "brief_choose.json"),
              ("vinc_brief", "brief_resolved.json"), ("vinc_node", "node.json"),
              ("vinc_neighbors", "neighbors.json"), ("vinc_neighbors", "neighbors_domain.json")]


@pytest.mark.parametrize("tool,name", REAL_CASES)
def test_a_real_result_loses_exactly_the_dropped_keys_in_the_handler_order(tool, name):
    raw = json.loads((REAL / name).read_text(encoding="utf-8"))
    got = rpc._mcp_text(tool, {}, raw)
    assert got == _text(_expected(tool, raw))
    # 기대값이 공허하지 않다: 무언가는 빠졌고, 줄인 본문이 원래보다 짧다.
    assert len(got) < len(_text(raw))


def test_the_fixed_note_in_the_real_shapes_is_the_one_the_handler_wrote():
    assert tools.HIDDEN_NOTE == FIXED
    for name in ("search.json", "brief_choose.json", "brief_resolved.json"):
        assert json.loads((REAL / name).read_text(encoding="utf-8"))["validity_note"] == FIXED


# ── 순서: props 와 결과 전체의 칸 순서는 핸들러 그대로다 ─────────────────────────────

def test_props_keep_their_stored_order_and_text():
    props = {"zeta": "last letter first", "alpha": {"b": 2, "a": 1}, "mid": [3, 1, 2],
             "empty": "", "none": None, "long": "x" * 900}
    raw = {"kind": "node", "executed": True, "found": True,
           "node": {"id": "record:r", "title": "R", "props": props, "owner_id": "user:u",
                    "rev": 1, "kind": "record"}}
    text = rpc._mcp_text("vinc_node", {}, raw)
    assert '"props":' + _text(props) in text
    assert list(json.loads(text)["node"]["props"]) == list(props)


# ── 남아야 하는 칸: 명세의 목록과 핸들러가 싣는 신호 ────────────────────────────────

#: 명세 9장 "Must still be present by default" 와, 핸들러(vinc_rag.retrieval.search·api.query.brief)가
#: 봉투에 싣는 신호 중 빼는 표에 없는 것. 값은 기본이 아닌 것으로 채운다.
SEARCH_ENVELOPE = ("kind", "executed", "arms", "total_count", "has_more", "next_cursor",
                   "validity_filtered", "error", "vector_note", "answer_note", "node_error",
                   "known_at_filtered", "known_at_unknown", "time_scope", "mode",
                   "claim_assessment_scope", "doc_validity_filtered", "encoder_runtime_warning",
                   "vector_rebuilding", "temporal_error", "node_domain_error", "node_hits_omitted",
                   "keyword_gated", "relevance_policy", "note", "partial", "total_count_capped")
SEARCH_ROW_KEEP = ("id", "score", "snippet", "doc_id", "cite_id", "truncated", "doc_title",
                   "cite_unavailable")
SEARCH_HIT_KEEP = ("id", "title", "kind", "domain", "rrf_score", "cite_id")
BRIEF_ENVELOPE = ("kind", "executed", "query", "status", "one_liner", "body_src", "body_cite",
                  "grounds", "resolved_via", "unreadable", "fell_through", "partial", "partial_note",
                  "search_error", "shrunk", "section", "note", "arms", "vector_note",
                  "vector_rebuilding", "encoder_runtime_warning", "validity_filtered",
                  "known_at_filtered", "known_at_unknown", "time_scope", "mode",
                  "claim_assessment_scope", "temporal_error", "node_error", "kinds_filtered",
                  "ambiguous", "ambiguous_note", "answerability_note", "related_note",
                  "unqualified_note", "known_at_node_note", "citation_error", "doc_validity_error",
                  "excerpt_error")
BRIEF_NODE_KEEP = ("id", "cite_id", "title", "kind", "domain", "validity", "claim_validity")
QUOTE_KEEP = ("text", "cite_id", "doc_id", "source", "section")
EXCERPT_KEEP = ("doc_id", "doc_title", "cite_id", "excerpt", "section", "method")
LINK_KEEP = ("id", "title", "type", "dir", "evidence_text", "cite_id", "kind", "domain",
             "confidence")
CAND_KEEP = ("id", "cite_id", "title", "domain", "kind", "quotes")
MATERIAL_KEEP = ("candidate", "cite_id", "one_liner", "body_src", "body_cite", "excerpts",
                 "validity_note")
NODE_KEEP = ("id", "title", "domain", "tags", "props", "rev", "created", "updated_at",
             "valid_from", "valid_until", "content_time", "one_liner", "kind", "superseded_by")
NODE_ENVELOPE = ("kind", "executed", "found")
NEIGHBOR_ENVELOPE = ("kind", "executed", "found", "node", "by_rel", "count", "neighbors_truncated")
NEIGHBOR_ROW = ("rel", "dir", "id", "title", "one_liner", "updated_at")
LONG = "Measured 2026-10-07 on the owner's laptop: " + "one epoch takes nine minutes " * 10 + "end"


def _signal(k):
    return {"validity_filtered": 0, "known_at_filtered": 0, "doc_validity_filtered": 2,
            "total_count_capped": True, "partial": True, "fell_through": True,
            "encoder_runtime_warning": True, "vector_rebuilding": True, "keyword_gated": True,
            "ambiguous": True, "time_scope": {"as_of": "2026", "from": "2026-01-01"},
            "unreadable": ["doc.x"], "grounds": ["concept:n", "doc.x"],
            "shrunk": {"excerpts_dropped": 1}, "section": {"doc_id": "doc.x", "text": "body"},
            "kinds_filtered": 3, "node_hits_omitted": 1, "known_at_unknown": 1,
            "total_count": 7, "has_more": True, "executed": True}.get(k, f"{k} value")


def _quote(i):
    return {"doc_id": f"doc.q{i}", "source": LONG, "section": "2.1", "cite_id": f"concept:doc.q{i}",
            "text": f"Quote {i} sentence, kept word for word. " * 4}


def test_every_must_stay_key_survives_unchanged():
    search = {k: _signal(k) for k in SEARCH_ENVELOPE}
    search.update(kind="search", validity_note="hid nodes whose claim did not hold during 2020; "
                  "drop as_of for today's view")
    search["rows"] = [{"id": "doc.a#00001", "score": 0.0, "snippet": LONG, "doc_id": "doc.a",
                       "cite_id": "concept:doc.doc.a", "truncated": True, "doc_title": LONG,
                       "cite_unavailable": "mirror missing", "rrf_score": 0.1}]
    search["node_hits"] = [{"id": "record:r", "title": LONG, "kind": "record", "domain": "vinc/x",
                            "rrf_score": 0.0, "cite_id": "record:r", "matched_in": "title"}]
    out = tools.lean_result("vinc_search", {}, search)
    for k in SEARCH_ENVELOPE + ("validity_note",):
        assert k in out and out[k] == search[k], k
    for k in SEARCH_ROW_KEEP:
        assert out["rows"][0][k] == search["rows"][0][k], k
    for k in SEARCH_HIT_KEEP:
        assert out["node_hits"][0][k] == search["node_hits"][0][k], k

    brief = {k: _signal(k) for k in BRIEF_ENVELOPE}
    brief.update(kind="brief", status="resolved",
                 validity_note="this claim is historical, not current; superseded by record:new")
    brief["node"] = {"id": "record:n", "cite_id": "record:n", "title": LONG, "kind": "record",
                     "domain": "vinc/x", "validity": "historical", "claim_validity": "historical"}
    brief["quotes"] = [_quote(0), _quote(1)]
    brief["excerpts"] = [{"doc_id": "doc.e", "doc_title": LONG, "cite_id": "concept:doc.doc.e",
                          "excerpt": "Excerpt text " * 30, "section": "3", "method": "chunk"}]
    brief["links"] = {"degree": 1, "items": [
        {"id": "concept:m", "title": LONG, "type": "ABOUT", "dir": "out", "evidence_text": "ev",
         "cite_id": "concept:m", "kind": "concept", "domain": "vinc/x", "confidence": 0.0}]}
    brief["candidates"] = [{"id": f"record:c{i}", "cite_id": f"record:c{i}", "title": LONG,
                            "domain": "vinc/x", "kind": "record", "quotes": [_quote(i)]}
                           for i in range(5)]
    brief["candidate_material"] = {"candidate": "record:c0", "cite_id": "record:c0",
                                   "one_liner": "C0.", "body_src": "one_liner", "body_cite": "x",
                                   "excerpts": copy.deepcopy(brief["excerpts"]),
                                   "validity_note": "the referenced node is not current"}
    out = tools.lean_result("vinc_brief", {}, brief)
    for k in BRIEF_ENVELOPE + ("validity_note",):
        assert k in out and out[k] == brief[k], k
    for k in BRIEF_NODE_KEEP:
        assert out["node"][k] == brief["node"][k], k
    for got, want in zip(out["quotes"], brief["quotes"]):
        assert {k: got[k] for k in QUOTE_KEEP} == {k: want[k] for k in QUOTE_KEEP}
    assert len(out["quotes"]) == 2
    for k in EXCERPT_KEEP:
        assert out["excerpts"][0][k] == brief["excerpts"][0][k], k
    for k in LINK_KEEP:
        assert out["links"]["items"][0][k] == brief["links"]["items"][0][k], k
    assert len(out["candidates"]) == 5
    for got, want in zip(out["candidates"], brief["candidates"]):
        assert {k: got[k] for k in CAND_KEEP} == {k: want[k] for k in CAND_KEEP}
    for k in MATERIAL_KEEP:
        assert out["candidate_material"][k] == brief["candidate_material"][k], k

    node = {"kind": "node", "executed": True, "found": True,
            "node": {"id": "record:n", "title": LONG, "domain": "vinc/x", "tags": ["a", "b"],
                     "props": {"b": "", "a": None, "c": "x" * 900}, "rev": 0,
                     "created": "2026-01-01", "updated_at": "2026-02-01", "valid_from": "2026-01-05",
                     "valid_until": "2026-12-31", "content_time": "2026-01-02",
                     "one_liner": "N.", "kind": "record", "superseded_by": "record:m"}}
    out = tools.lean_result("vinc_node", {}, node)
    for k in NODE_ENVELOPE:
        assert out[k] == node[k], k
    for k in NODE_KEEP:
        assert out["node"][k] == node["node"][k], k
    assert _text(out["node"]["props"]) == _text(node["node"]["props"])

    nb = {"kind": "neighbors", "executed": True, "found": True, "node": "concept:v",
          "by_rel": {"ABOUT": 1}, "count": 1, "neighbors_truncated": False,
          "neighbors": [{"rel": "ABOUT", "dir": "in", "id": "record:r", "title": LONG,
                         "one_liner": "R.", "updated_at": "2026-02-01", "domain": "vinc/x"}]}
    out = tools.lean_result("vinc_neighbors", {}, nb)
    for k in NEIGHBOR_ENVELOPE:
        assert out[k] == nb[k], k
    for k in NEIGHBOR_ROW + ("domain",):
        assert out["neighbors"][0][k] == nb["neighbors"][0][k], k


# ── validity_note: 고정 문장만 빠진다 ───────────────────────────────────────────

#: 핸들러가 `validity_note` 에 쓸 수 있는 문장 전부(vinc_rag.retrieval·api.query). 고정 문장 하나만 빠진다.
OTHER_NOTES = [
    "hid nodes whose claim did not hold during 2020; drop as_of for today's view",
    "hid chunks of documents whose validity ended; pass historical=true to include them",
    "some claims are outside the selected time scope; use historical mode to include them",
    "hid claims that are not current; use mode=historical to include them",
    "the current claim is uncertain; no current state was chosen",
    "current claim evidence is conflicting or unknown; no current state was chosen",
    "the referenced node is not current",
    "the referenced node did not hold during 2020",
    "the referenced node was recorded after 2020; the graph did not hold it then",
    "this claim is historical, not current; superseded by record:new",
    "a claim that is not current tied with the leading candidate, so this was not resolved; "
    "use mode=historical to see it",
    "a claim that did not hold during 2020 tied with the leading candidate, so this was not "
    "resolved; drop as_of to see it",
    "a claim that is no longer current tied with the leading candidate, so this was not "
    "resolved; pass historical=true to see it",
]


@pytest.mark.parametrize("note", OTHER_NOTES)
@pytest.mark.parametrize("tool", ["vinc_search", "vinc_brief"])
def test_a_validity_note_that_says_something_happened_stays(tool, note):
    raw = {"kind": tool[5:], "executed": True, "validity_filtered": 1, "validity_note": note,
           "resolution": {"rule": "v3"}, "node_rescore": {"n": 1}}
    assert tools.lean_result(tool, {}, raw)["validity_note"] == note


@pytest.mark.parametrize("tool", ["vinc_search", "vinc_brief"])
def test_the_fixed_hidden_note_goes(tool):
    raw = {"kind": tool[5:], "executed": True, "validity_filtered": 1,
           "validity_note": tools.HIDDEN_NOTE}
    out = tools.lean_result(tool, {}, raw)
    assert "validity_note" not in out and out["validity_filtered"] == 1


@pytest.fixture(scope="module")
def expired_app(tmp_path_factory):
    from api import fragment
    from api.server import VincApp
    from store.backend import open_store
    from store.config import load_config
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MEMORY_STORE_BACKEND", "embedded")
        mp.setenv("MEMORY_EMBEDDING_MODEL", "local-en")
        cfg = load_config({"data_dir": str(tmp_path_factory.mktemp("expired") / "db")})
        store = open_store(cfg)
        store.provision(dim=cfg.embedding_dim)
        res = fragment.merge_fragment({"owner": "user:synthetic-expired", "nodes": [
            {"id": "record:quartz-old", "type": "record", "title": "Quartz ledger old claim",
             "domain": "vinc/synthetic", "valid_from": "2019-01-01", "valid_until": "2020-01-01"},
            {"id": "record:quartz-new", "type": "record", "title": "Quartz ledger new claim",
             "domain": "vinc/synthetic", "valid_from": "2020-01-01"},
        ]}, store=store)
        assert res["executed"] and not res.get("warnings"), res
        app = VincApp(cfg, store=store)
        try:
            yield app
        finally:
            app.jobs.shutdown()
            store.close()


def test_the_mirrored_hidden_note_is_the_sentence_the_search_arm_writes(expired_app):
    from api.cli_tools import _InProcessBridge
    bridge = _InProcessBridge(expired_app)
    bridge.owner = "user:synthetic-expired"
    raw = tools.call("vinc_search", {"query": "Quartz ledger"}, bridge)
    assert raw.get("validity_filtered") and raw["validity_note"] == tools.HIDDEN_NOTE
    reply = rpc.Dispatcher(bridge).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                           "params": {"name": "vinc_search",
                                                      "arguments": {"query": "Quartz ledger"}}})
    out = json.loads(reply["result"]["content"][0]["text"])
    assert "validity_note" not in out and out["validity_filtered"] == raw["validity_filtered"]
    # 기간을 준 읽기의 문장은 무엇이 숨었는지 말하므로 남는다.
    raw = tools.call("vinc_search", {"query": "Quartz ledger", "as_of": "2030"}, bridge)
    if raw.get("validity_note"):
        assert raw["validity_note"] != tools.HIDDEN_NOTE
        assert tools.lean_result("vinc_search", {}, raw)["validity_note"] == raw["validity_note"]
