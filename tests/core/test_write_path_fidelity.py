"""저작 쓰기 경로가 보낸 값을 지키는지 실엔진에서 검증한다(VIN-587).

각 테스트는 감사(2026-10-16)가 재현한 결함 하나를 고정한다. 스토어는 모듈마다 하나이고
`VINC_PK_NAMESPACED` 0·1 두 판으로 돈다. id 는 테스트마다 새로 지어 서로 섞이지 않게 한다.
"""
from __future__ import annotations

import os
from itertools import count

import pytest

from api import docstore, docsync, query
from api.fragment import merge_fragment
from store import pkns
from store.backend import open_store
from store.config import load_config
from store.theme_store import ThemeStore

OWNER = "user:write-fidelity"
SEQ = count()


@pytest.fixture(scope="module", params=["0", "1"])
def graph(request, tmp_path_factory):
    keys = ("VINC_PK_NAMESPACED", "MEMORY_STORE_BACKEND")
    old = {k: os.environ.get(k) for k in keys}
    os.environ.update(VINC_PK_NAMESPACED=request.param, MEMORY_STORE_BACKEND="embedded")
    cfg = load_config({"data_dir": str(tmp_path_factory.mktemp("write-fidelity") / "db")})
    store = open_store(cfg)
    try:
        store.provision(dim=cfg.embedding_dim)
        yield store
    finally:
        store.close()
        for k, value in old.items():
            if value is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = value


def uid(kind: str) -> str:
    return f"{kind}:wf-{next(SEQ)}"


def write(graph, nodes, edges=(), **kw):
    body = {"owner": OWNER, "nodes": list(nodes), "edges": list(edges)}
    body.update({k: kw.pop(k) for k in ("theme_id",) if k in kw})
    return merge_fragment(body, store=graph, read_back=True, **kw)


def node(graph, nid):
    return query.node(nid, owner=OWNER, store=graph)["node"]


def row(graph, label, pk, nid, cols):
    proj = ", ".join(f"n.{c} AS {c}" for c in cols)
    rows = graph.run(f"MATCH (n:{label}) WHERE n.{pk} = $id RETURN {proj}",
                     {"id": pkns.key(OWNER, nid)})
    assert rows, nid
    return rows[0]


def concept(nid=None, **kw):
    return {"id": nid or uid("concept"), "type": "concept", "title": "Fixture concept",
            "domain": "vinc/test", **kw}


# ── F1: Bridge 에 tags 가 오면 노드와 엣지가 떨어졌다 ────────────────────────────

def test_bridge_with_tags_is_stored_with_its_edges_and_a_warning(graph):
    c, b = concept(), uid("bridge")
    res = write(graph, [c, {"id": b, "type": "bridge", "title": "Tagged bridge",
                            "tags": ["x"], "props": {"k": 1}}],
                [{"from": b, "type": "connects_via", "to": c["id"], "role": "input"}])
    assert res["merged"]["nodes"]["created"] == 2 and res["merged"]["edges"]["created"] == 1, res
    assert not [w for w in res["warnings"] if "write failed" in w or "read-back" in w], res
    assert any(w.startswith("node.tags not persisted on Bridge") for w in res["warnings"])
    stored = {n["id"]: n for n in res["stored"]}
    assert stored[b]["title"] == "Tagged bridge" and "tags" not in stored[b]
    links = graph.run("MATCH (a:Bridge)-[r:CONNECTS]->(c:Concept) WHERE a.bridge_id = $b "
                      "RETURN r.role AS role", {"b": pkns.key(OWNER, b)})
    assert [r["role"] for r in links] == ["input"]


# ── F1b: 같은 쌍의 다른 타입 부착이 한 엣지로 접혔다 ────────────────────────────

def _attachments(graph, rid):
    rows = graph.run("MATCH (a:Record)-[r:ATTACHES_TO]->(b:Concept) WHERE a.record_id = $r "
                     "RETURN r.type AS t, r.evidence AS ev, r.confidence AS conf",
                     {"r": pkns.key(OWNER, rid)})
    return {r["t"]: (r["ev"], r["conf"]) for r in rows}


def test_two_attachment_types_to_one_target_stay_two_edges(graph):
    c, rid = concept(), uid("record")
    rec = {"id": rid, "type": "record", "title": "Fixture record", "domain": "vinc/test"}
    write(graph, [c, rec], [
        {"from": rid, "type": "derives_from", "to": c["id"], "evidence": "ev der", "confidence": 0.6},
        {"from": rid, "type": "relates_to", "to": c["id"], "evidence": "ev rel", "confidence": 0.4}])
    assert _attachments(graph, rid) == {"derives_from": ("ev der", 0.6),
                                        "relates_to": ("ev rel", 0.4)}
    # 같은 타입 재전송은 그 엣지만 고친다.
    write(graph, [c, rec], [{"from": rid, "type": "derives_from", "to": c["id"],
                             "evidence": "ev der 2"}])
    assert _attachments(graph, rid) == {"derives_from": ("ev der 2", 0.6),
                                        "relates_to": ("ev rel", 0.4)}
    # 타입을 지정한 삭제는 다른 타입의 부착을 지우지 않는다.
    out = query.delete_edge(rid, "derives_from", c["id"], owner=OWNER, store=graph)
    assert out["found"] and out["deleted"] == {"edges": 1}, out
    assert _attachments(graph, rid) == {"relates_to": ("ev rel", 0.4)}


# ── F5: 문서 content_time ────────────────────────────────────────────────────

TEXT = "# Fixture doc\n\nBody line about fixtures.\n"


def _doc(graph, doc_id):
    return docstore.get_doc(graph, doc_id, owner=OWNER)


def test_doc_content_time_only_edit_is_applied_without_rechunking(graph):
    doc_id = f"wf-doc-{next(SEQ)}"
    assert docstore.put_doc(graph, owner=OWNER, doc_id=doc_id, text=TEXT, title="Doc",
                            content_time="2026-01")["executed"]
    res = docstore.put_doc(graph, owner=OWNER, doc_id=doc_id, text=TEXT, content_time="2025-05")
    assert res["executed"] and not res["skipped"] and res["replaced"] == 0, res
    assert _doc(graph, doc_id)["content_time"] == "2025-05"
    res = docstore.put_doc(graph, owner=OWNER, doc_id=doc_id, text=TEXT,
                           valid_until="2026-11", content_time="2025-06")
    got = _doc(graph, doc_id)
    assert (got["content_time"], got["valid_until"]) == ("2025-06", "2026-11"), res
    # 같은 값 재전송은 여전히 스킵이다.
    assert docstore.put_doc(graph, owner=OWNER, doc_id=doc_id, text=TEXT,
                            content_time="2025-06")["skipped"]


@pytest.mark.parametrize("same_text", [True, False])
def test_doc_invalid_content_time_warns_and_keeps_the_stored_value(graph, same_text):
    doc_id = f"wf-doc-{next(SEQ)}"
    docstore.put_doc(graph, owner=OWNER, doc_id=doc_id, text=TEXT, content_time="2026-01")
    res = docstore.put_doc(graph, owner=OWNER, doc_id=doc_id,
                           text=TEXT if same_text else TEXT + "more\n", content_time="2026/01")
    assert res["executed"], res
    assert any("content_time '2026/01'" in w for w in res["warnings"]), res
    assert _doc(graph, doc_id)["content_time"] == "2026-01"
    # 빈 문자열은 여전히 명시적 지우기다.
    docstore.put_doc(graph, owner=OWNER, doc_id=doc_id, text=TEXT, content_time="")
    assert _doc(graph, doc_id)["content_time"] == ""


def test_docsync_applies_a_remote_content_time_only_edit(graph):
    doc_id = f"wf-doc-{next(SEQ)}"
    docstore.put_doc(graph, owner=OWNER, doc_id=doc_id, text=TEXT, content_time="2026-01")
    local = _doc(graph, doc_id)
    remote = {"doc_id": doc_id, "text": TEXT, "title": local["title"],
              "content_hash": local["content_hash"], "content_time": "1990-01",
              "updated_at": "2099-01-01T00:00:00.000Z"}   # 유효구간 키가 없는 구 송신자
    out = docsync.import_docs(graph, owner=OWNER, payload={"docs": [remote]})
    assert out["updated"] == 1, out
    assert _doc(graph, doc_id)["content_time"] == "1990-01"


# ── F6: props 승격은 신규이거나 열이 빌 때만 ─────────────────────────────────

def test_props_resend_does_not_rewrite_authored_columns(graph):
    c = concept(one_liner="Authored liner", content_time="2025-04-05")
    write(graph, [c])
    res = write(graph, [{"id": c["id"], "type": "concept",
                         "props": {"note": "a note", "content_time": "1999"}}])
    got = node(graph, c["id"])
    assert (got["one_liner"], got["content_time"]) == ("Authored liner", "2025-04-05")
    assert got["props"] == {"note": "a note", "content_time": "1999"}
    assert any(w.startswith("props.note not promoted") for w in res["warnings"]), res
    assert any(w.startswith("props.content_time not promoted") for w in res["warnings"]), res

    eid = uid("episode")
    write(graph, [{"id": eid, "type": "episode", "title": "E", "domain": "vinc/test",
                   "date": "2026-03-04", "summary": "Authored summary"}])
    res = write(graph, [{"id": eid, "type": "episode",
                         "props": {"date": "2001-01-01", "summary": "stale"}}])
    got = row(graph, "Episode", "episode_id", eid, ("date", "summary"))
    assert (got["date"], got["summary"]) == ("2026-03-04", "Authored summary")
    assert any(w.startswith("props.date not promoted") for w in res["warnings"]), res


def test_props_still_promote_into_new_or_empty_columns(graph):
    c = concept()
    write(graph, [c])
    write(graph, [{"id": c["id"], "type": "concept", "props": {"note": "late note"}}])
    assert node(graph, c["id"])["one_liner"] == "late note"
    eid = uid("episode")
    write(graph, [{"id": eid, "type": "episode", "title": "E",
                   "props": {"date": "2026-07-01", "summary": "s"}}])
    got = row(graph, "Episode", "episode_id", eid, ("date", "summary"))
    assert (got["date"], got["summary"]) == ("2026-07-01", "s")


# ── F7: 결정 유효구간 형식 ───────────────────────────────────────────────────

def test_decision_malformed_interval_is_not_stored(graph):
    c, did = concept(), uid("decision")
    res = write(graph, [c, {"id": did, "type": "decision", "title": "Unpadded",
                            "props": {"valid_from": "2025-1-5", "valid_until": "2026-12",
                                      "why": "w"}}],
                [{"from": did, "type": "about", "to": c["id"]}])
    assert any("props.valid_from '2025-1-5'" in w for w in res["warnings"]), res
    assert node(graph, did)["props"] == {"valid_until": "2026-12", "why": "w"}
    # 기존 값이 있으면 틀린 재전송이 그것을 지우지 않는다.
    res = write(graph, [{"id": did, "type": "decision",
                         "props": {"valid_until": "2026-9-1", "why": "w2"}}])
    assert any("props.valid_until '2026-9-1'" in w for w in res["warnings"]), res
    assert node(graph, did)["props"] == {"valid_until": "2026-12", "why": "w2"}


def test_decision_inverted_interval_warns_and_valid_retirement_retires(graph):
    c, did = concept(), uid("decision")
    res = write(graph, [c, {"id": did, "type": "decision", "title": "Inverted",
                            "props": {"valid_from": "2026-05", "valid_until": "2026-01"}}],
                [{"from": did, "type": "about", "to": c["id"]}])
    assert any(w.startswith("validity interval inverted") for w in res["warnings"]), res
    write(graph, [{"id": did, "type": "decision", "props": {"valid_from": "2019-01"}}])
    rev = node(graph, did)["rev"]
    res = write(graph, [{"id": did, "type": "decision", "expected_rev": rev,
                         "props_patch": {"valid_until": "2020-01"}}])
    assert not [w for w in res["warnings"] if "valid_until" in w], res
    got = node(graph, did)
    assert got["props"]["valid_until"] == "2020-01"
    assert got["temporal"]["validity"] == "historical", got["temporal"]


# ── F9: Episode 날짜와 상류 created ─────────────────────────────────────────

def test_episode_date_is_validated_like_content_time(graph):
    eid = uid("episode")
    res = write(graph, [{"id": eid, "type": "episode", "title": "E", "date": "2026-3-4"}])
    assert any("date '2026-3-4'" in w for w in res["warnings"]), res
    assert not row(graph, "Episode", "episode_id", eid, ("date",))["date"]
    write(graph, [{"id": eid, "type": "episode", "date": "2026-03-04"}])
    res = write(graph, [{"id": eid, "type": "episode", "date": "March 5"}])
    assert any("date 'March 5'" in w for w in res["warnings"]), res
    assert row(graph, "Episode", "episode_id", eid, ("date",))["date"] == "2026-03-04"


@pytest.mark.parametrize("created,kept", [
    ("2025-01-02T03:04:05.000Z", True), ("2025-01-02", True),
    ("last tuesday", False), ("2025-02-30", False), ("2025", False)])
def test_client_created_is_accepted_only_as_iso(graph, created, kept):
    did = uid("decision")
    res = write(graph, [{"id": did, "type": "decision", "title": "Born", "created": created}])
    stored = row(graph, "Decision", "decision_id", did, ("created",))["created"]
    if kept:
        assert stored == created
        assert not [w for w in res["warnings"] if w.startswith("created ")], res
    else:
        assert stored != created and stored[:4].isdigit() and "T" in stored, stored
        assert any(w.startswith(f"created '{created}'") for w in res["warnings"]), res


# ── F16: Entity 의 tags·theme_id ─────────────────────────────────────────────

def test_entity_resend_keeps_tags_and_theme(graph):
    tid = uid("theory")
    themes = ThemeStore(None)
    write(graph, [{"id": tid, "type": "theory", "title": "T", "tags": ["a"]}],
          themes=themes, theme_id="fashion")
    cols = ("tags", "theme_id")
    assert row(graph, "Entity", "entity_id", tid, cols) == {"tags": ["a"], "theme_id": "fashion"}
    # themes 없는 호출부와 다른 활성 테마는 귀속을 덮지 않는다.
    write(graph, [{"id": tid, "type": "theory", "title": "T2"}])
    themes.create_theme("other")
    themes.activate_theme("other")
    write(graph, [{"id": tid, "type": "theory", "title": "T3"}], themes=themes)
    assert row(graph, "Entity", "entity_id", tid, cols) == {"tags": ["a"], "theme_id": "fashion"}
    # 직접 실은 theme_id 는 바꾼다.
    write(graph, [{"id": tid, "type": "theory", "title": "T4", "tags": []}],
          themes=themes, theme_id="other")
    assert row(graph, "Entity", "entity_id", tid, cols) == {"tags": [], "theme_id": "other"}


# ── F18: Bridge 출생 ─────────────────────────────────────────────────────────

def test_bridge_created_is_stamped(graph):
    b, born = uid("bridge"), uid("bridge")
    write(graph, [{"id": b, "type": "bridge", "title": "B"},
                  {"id": born, "type": "bridge", "title": "B2", "created": "2025-05-05"}])
    stamped = row(graph, "Bridge", "bridge_id", b, ("created",))["created"]
    assert stamped[:4].isdigit() and "T" in stamped, stamped
    assert row(graph, "Bridge", "bridge_id", born, ("created",))["created"] == "2025-05-05"
    # 재전송은 출생을 바꾸지 않는다.
    write(graph, [{"id": b, "type": "bridge", "title": "B again", "created": "2020-01-01"}])
    assert row(graph, "Bridge", "bridge_id", b, ("created",))["created"] == stamped


# ── F20: 역방향 relates_to 재전송 · Domain 경고 ──────────────────────────────

def test_reverse_relates_to_resend_updates_the_stored_edge(graph):
    a, b = concept(), concept()
    write(graph, [a, b], [{"from": a["id"], "type": "relates_to", "to": b["id"],
                           "evidence": "first", "confidence": 0.7}])
    res = write(graph, [a, b], [{"from": b["id"], "type": "relates_to", "to": a["id"],
                                 "evidence": "reverse", "confidence": 0.11}])
    assert res["merged"]["edges"] == {"created": 0, "skipped_duplicate": 1}, res
    rows = graph.run("MATCH (x:Concept)-[r:RELATED_TO]-(y:Concept) "
                     "WHERE x.concept_id = $a AND y.concept_id = $b "
                     "RETURN r.evidence AS ev, r.confidence AS conf",
                     {"a": pkns.key(OWNER, a["id"]), "b": pkns.key(OWNER, b["id"])})
    assert [(r["ev"], r["conf"]) for r in rows] == [("reverse", 0.11)]


def test_domain_props_warn_once(graph):
    res = write(graph, [{"id": "domain:vinc/test/wf", "type": "domain", "props": {"no": 1}}])
    assert len([w for w in res["warnings"] if "props" in w]) == 1, res
