"""격리된 실제 저장소와 직접 주입한 4차원 벡터로 쓰기 이후 검색을 검증한다."""
import pytest

from api import docstore, fragment, node_mirror, query
from store import pkns
from store.backend import open_store
from store.config import load_config

OWNER, OTHER = "user:search-lifecycle", "user:other-space"
VECTOR = [1.0, 0.0, 0.0, 0.0]


@pytest.fixture(params=["0", "1"])
def db(tmp_path, monkeypatch, request):
    monkeypatch.setenv("VINC_PK_NAMESPACED", request.param)
    monkeypatch.setenv("VINC_KEEP_VECTOR_INDEX", "off")
    monkeypatch.setenv("VINC_NODE_SEMANTIC_ARM", "on")
    cfg = load_config({"data_dir": str(tmp_path / "db"), "embedding_dim": 4,
                       "embedding_model": "local-en", "buffer_pool_mb": 128})
    store = open_store(cfg)
    assert store is not None
    store.provision(dim=4)
    yield store, cfg
    store.close()


def ingest(store, ident, title, summary="", owner=OWNER):
    result = fragment.merge_fragment({"owner": owner, "nodes": [{"id": ident,
        "type": "episode", "title": title, "summary": summary, "date": "2026-10-14",
        "domain": "vinc/engineering/search"}]}, store=store)
    assert result["executed"] and not result.get("partial"), result


def fill_mirror(store):
    rows, conflicts = node_mirror.collect(store)
    assert not conflicts
    for row in rows:
        row["emb"] = VECTOR
    result = node_mirror.replace(store, rows, "synthetic-fixed-vector")
    assert result["index_ok"], result


@pytest.mark.parametrize("title,new_title", [
    ("Publish moonprobe one", "Publish orbitprobe two"),
    ("달탐사 출판 완료", "궤도탐사 출판 완료"),
])
def test_episode_create_update_delete_and_candidate_freshness(db, title, new_title):
    store, cfg = db
    ident = "episode:synthetic-release"
    ingest(store, ident, title, "Initial summary")
    # 300개의 다른 사건 뒤에도 새 제목은 즉시 어휘 검색으로 찾는다.
    rows = [{"id": pkns.key(OWNER, f"episode:filler{i}"), "title": f"Filler {i}"}
            for i in range(301)]
    store.run("UNWIND $rows AS r CREATE (:Episode {episode_id:r.id, title:r.title, "
              "owner_id:$owner, date:'2026-10-14'})", {"rows": rows, "owner": OWNER})
    first = query.search(title, store=store, cfg=cfg, owner=OWNER)
    assert first["executed"] and ident in {h["id"] for h in first["node_hits"]}
    unindexed = query.search(title, store=store, cfg=cfg, owner=OWNER, query_embedding=VECTOR)
    assert unindexed["node_semantic"]["state"] == "empty_mirror"
    assert unindexed["node_semantic"]["stored_vectors"] == 0
    assert unindexed["node_semantic"]["freshness"] == "not_checked"
    fill_mirror(store)
    ready = query.search(title, store=store, cfg=cfg, owner=OWNER, query_embedding=VECTOR, top_k=400)
    hit = next(h for h in ready["node_hits"] if h["id"] == ident)
    assert "semantic" in hit["arms"]
    assert ready["node_semantic"]["freshness"] == "checked_candidates"
    ingest(store, ident, new_title, "Updated summary")
    stale = query.search(title, store=store, cfg=cfg, owner=OWNER, query_embedding=VECTOR, top_k=400)
    assert stale["node_semantic"]["stale_candidates"] == 1
    assert all(h["id"] != ident or "semantic" not in h["arms"] for h in stale["node_hits"])
    assert any(h["id"] == ident for h in query.search(new_title, store=store, cfg=cfg,
                                                     owner=OWNER)["node_hits"])
    fill_mirror(store)
    refreshed = query.search(new_title, store=store, cfg=cfg, owner=OWNER,
                              query_embedding=VECTOR, top_k=400)
    assert refreshed["node_semantic"]["stale_candidates"] == 0
    assert query.delete_node(ident, store=store, owner=OWNER)["deleted"]["node"] == 1
    deleted = query.search(new_title, store=store, cfg=cfg, owner=OWNER,
                            query_embedding=VECTOR, top_k=400)
    assert all(h["id"] != ident for h in deleted["node_hits"])
    assert query.search(new_title, store=store, cfg=cfg, owner=OTHER,
                        query_embedding=VECTOR)["node_hits"] == []


@pytest.mark.parametrize("text,new_text", [("moonfixture document", "orbitfixture document"),
                                            ("달표식 문서입니다", "궤도표식 문서입니다")])
def test_document_keyword_pending_ready_update_delete_and_unknown_dates(db, text, new_text):
    store, cfg = db
    ident = "synthetic-lifecycle-doc"
    result = docstore.put_doc(store, owner=OWNER, doc_id=ident, title="Fixture", text=text,
                             topic="vinc/engineering/search")
    assert result["executed"] and result["needs_embed"]
    unknown = docstore.get_doc(store, ident, owner=OWNER)
    assert unknown["validity_basis"] == "legacy_visibility"
    assert unknown["claim_validity"] == unknown["temporal"]["validity"] == "unknown"
    brief = query.brief("concept:doc." + ident, store=store, cfg=cfg, owner=OWNER)
    assert brief["node"]["validity_basis"] == "legacy_visibility"
    assert brief["node"]["claim_validity"] == brief["node"]["temporal"]["validity"] == "unknown"
    keyword = query.search(text, store=store, cfg=cfg, owner=OWNER)
    assert any(r["doc_id"] == ident for r in keyword["rows"]), keyword
    pending = [c for c in docstore.pending_embed_chunks(store) if c["doc_id"] == ident]
    assert pending
    applied = docstore.apply_embeddings(store, ident, {c["chunk_id"]: [VECTOR] for c in pending},
                                        content_hash=result["content_hash"])
    assert applied["executed"], applied
    assert not docstore.get_doc(store, ident, owner=OWNER)["needs_embed"]
    readiness = docstore.get_doc(store, ident, owner=OWNER)["retrieval"]
    assert readiness["document_vectors"] == "ready" and readiness["query_index"] == "not_checked"
    store.ensure_search_indexes(dim=4)
    semantic = query.search("unrelated paraphrase", store=store, cfg=cfg, owner=OWNER,
                             query_embedding=VECTOR)
    assert any(r["doc_id"] == ident and "semantic" in r.get("arms", [])
               for r in semantic["rows"]), repr(semantic)
    changed = docstore.put_doc(store, owner=OWNER, doc_id=ident, text=new_text)
    assert changed["needs_embed"]
    stale = docstore.apply_embeddings(store, ident, {c["chunk_id"]: [VECTOR] for c in pending},
                                      content_hash=result["content_hash"])
    assert not stale["executed"] and stale["error"] == "content_changed"
    assert query.node("concept:doc." + ident, store=store, owner=OWNER)["found"]
    assert docstore.delete_doc(store, ident, owner=OWNER)["executed"]
    assert not docstore.get_doc(store, ident, owner=OWNER)["executed"]
    assert query.node("concept:doc." + ident, store=store, owner=OWNER)["found"]
    after = query.search(new_text, store=store, cfg=cfg, owner=OWNER, query_embedding=VECTOR)
    assert all(r["doc_id"] != ident for r in after["rows"])


def test_owner_validation_never_uses_another_owners_same_logical_id(db):
    store, cfg = db
    ident = "episode:shared" if pkns.enabled() else "episode:other"
    ingest(store, "episode:shared", "Owner moonprobe")
    ingest(store, ident, "Other orbitprobe", owner=OTHER)
    fill_mirror(store)
    ingest(store, "episode:shared", "Owner updatedprobe")
    other = query.search("Other orbitprobe", store=store, cfg=cfg, owner=OTHER,
                         query_embedding=VECTOR)
    assert other["node_semantic"]["stale_candidates"] == 0
    assert [h["id"] for h in other["node_hits"]] == [ident]
    assert query.delete_node("episode:shared", store=store, owner=OWNER)["executed"]
    assert query.node(ident, store=store, owner=OTHER)["found"]
