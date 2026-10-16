"""VIN-183의 비과금 후보·원문 근거·명시 승인 계약. GLiNER 품질 평가는 아니다."""
import json

import pytest

from api import docstore as ds
from engines import body_candidates as bc, mentions_link as ml
from store import embedded_schema as schema

TEXT = "---\ndomain: physics\n---\n# Notes\n\n😀 양자 얽힘과 quantum memory를 본문에서 설명한다.\n"
OWNER = "user:body-eval"
DOC = "body-candidate-contract"


def candidate(text=TEXT, title="양자 얽힘", needle="양자 얽힘"):
    start = text.index(needle)
    return {"source": "body", "doc_id": DOC, "source_hash": bc.source_hash(text),
            "title": title, "spans": [{"char_start": start, "char_end": start + len(needle),
                                        "text": needle}],
            "extractor": {"name": "fixed-fixture", "version": "1", "runtime": "no-model"}}


def test_source_provenance_unicode_and_deterministic_id():
    raw = candidate()
    first = bc.prepare_candidates(TEXT, DOC, [raw])[0]
    assert first == bc.prepare_candidates(TEXT, DOC, [raw])[0]
    assert first["offset_unit"] == "unicode_codepoint"
    assert first["provenance_verified"] == "source-span-only"
    assert not first["approved"]
    changed_title = {**first, "title": "Edited title"}
    assert bc.prepare_candidates(TEXT, DOC, [changed_title])[0]["candidate_id"] == first["candidate_id"]
    assert raw == candidate()  # caller payload는 변경하지 않는다.


@pytest.mark.parametrize("mutation", [
    {"doc_id": "foreign"}, {"source_hash": "old"}, {"candidate_id": "body:forged"},
    {"extractor": {}}, {"score": float("nan")}, {"score": True}, {"spans": []},
    {"spans": [{"char_start": True, "char_end": 5, "text": "fake"}]},
    {"spans": [{"char_start": 1, "char_end": 9999, "text": "fake"}]},
    {"spans": [{"char_start": 0, "char_end": 3, "text": "fake"}]},
])
def test_invalid_or_stale_source_is_rejected(mutation):
    with pytest.raises(ValueError):
        bc.prepare_candidates(TEXT, DOC, [{**candidate(), **mutation}])


def test_no_implicit_approval_and_no_duplicate_candidate():
    raw = candidate()
    with pytest.raises(ValueError, match="explicit user approval"):
        bc.prepare_candidates(TEXT, DOC, [raw], require_approval=True)
    with pytest.raises(ValueError, match="duplicate"):
        bc.prepare_candidates(TEXT, DOC, [raw, raw])
    with pytest.raises(ValueError):
        bc.prepare_candidates(TEXT, DOC, [raw] * 25)


@pytest.fixture
def store(tmp_path, monkeypatch):
    from store.backend import open_store
    from store.config import load_config
    monkeypatch.setenv("MEMORY_DATA_DIR", str(tmp_path / "db"))
    monkeypatch.setenv("MEMORY_STORE_BACKEND", "embedded")
    backend = open_store(load_config())
    assert backend is not None
    backend.provision(dim=4)
    ds.put_doc(backend, doc_id=DOC, owner=OWNER, text=TEXT, title="Notes")
    yield backend
    backend.close()


def preview(store, raws=None):
    result = ds.extract_doc(store, DOC, owner=OWNER, candidates=raws or [candidate()])
    assert result["executed"], result
    return result


def count_nodes(store):
    return store.run("MATCH (n:Concept) RETURN count(n) AS n")[0]["n"]


def apply(store, rows, ids):
    return ds.extract_doc(store, DOC, owner=OWNER, candidates=rows, apply=True,
                          approved_candidate_ids=ids)


def body_links(store):
    return store.run("MATCH (ch:Chunk)-[m:MENTIONS]->(n:Concept) WHERE ch.doc_id=$doc "
                     "AND m.method='body' RETURN n.title AS title, m.char_start AS cs, "
                     "m.char_end AS ce, m.source_hash AS hash, m.body_provenance AS proof",
                     {"doc": DOC})


def test_preview_has_evidence_without_any_writes_and_default_apply_refuses(store):
    before = count_nodes(store)
    result = preview(store)
    assert count_nodes(store) == before
    assert result["candidates"][0]["spans"][0]["text"] == "양자 얽힘"
    assert not schema.body_mentions_ready(store)
    assert not apply(store, result["candidates"], [result["candidates"][0]["candidate_id"]])["executed"]
    assert count_nodes(store) == before
    with pytest.raises(ValueError, match="explicit approval"):
        schema.migrate_body_mentions(store)
    assert not schema.body_mentions_ready(store)


def test_only_selected_ids_persist_and_mentions_hold_provenance(store):
    assert schema.migrate_body_mentions(store, approved=True)["changed"]
    assert not schema.migrate_body_mentions(store, approved=True)["changed"]
    rows = preview(store, [candidate(), candidate(title="quantum memory", needle="quantum memory")])["candidates"]
    before = count_nodes(store)
    chosen_id = rows[0]["candidate_id"]
    for ids in [None, [], ["body:forged"], [chosen_id, chosen_id]]:
        assert not apply(store, rows, ids)["executed"]
        assert count_nodes(store) == before
    applied = apply(store, rows, [chosen_id])
    assert applied["executed"], applied
    assert applied["applied"] == {"created": 1, "matched": 0}
    assert count_nodes(store) == before + 1
    links = body_links(store)
    assert len(links) == 1 and links[0]["title"] == "양자 얽힘"
    assert TEXT[links[0]["cs"]:links[0]["ce"]] == "양자 얽힘"
    proof = json.loads(links[0]["proof"])[0]
    assert proof["candidate_id"] == chosen_id and proof["approved"] is True
    assert proof["verification"] == "source-span-only"
    assert links[0]["hash"] == bc.source_hash(TEXT)
    # 기본 재링크는 승인 스팬을 지우거나 method를 덮어쓰지 않는다.
    ml.relink_all(store, owner=OWNER)
    assert body_links(store) == links
    again = apply(store, rows, [chosen_id])
    assert again["applied"] == {"created": 0, "matched": 1}
    assert body_links(store) == links


def test_stale_foreign_and_unverified_documents_never_apply(store):
    schema.migrate_body_mentions(store, approved=True)
    rows = preview(store)["candidates"]
    before = count_nodes(store)
    foreign = ds.extract_doc(store, DOC, owner="user:other", apply=True,
                             candidates=rows, approved_candidate_ids=[rows[0]["candidate_id"]])
    assert foreign["error"] == "unknown doc_id"
    ds.put_doc(store, owner=OWNER, doc_id=DOC, text=TEXT + "Updated.\n")
    assert "stale" in apply(store, rows, [rows[0]["candidate_id"]])["error"]
    assert count_nodes(store) == before
    store.run("MATCH (ch:Chunk) WHERE ch.doc_id=$doc SET ch.char_start=-1", {"doc": DOC})
    assert "verified canonical" in ds.extract_doc(store, DOC, owner=OWNER,
                                                  candidates=rows)["error"]
    assert count_nodes(store) == before


@pytest.mark.parametrize("failure_point", ["mentions", "domain"])
def test_mid_apply_failure_rolls_back_all_nodes_edges_and_stamps(store, monkeypatch, failure_point):
    schema.migrate_body_mentions(store, approved=True)
    rows = preview(store, [candidate(), candidate(title="quantum memory", needle="quantum memory")])["candidates"]
    before = count_nodes(store)
    stamps = store.run("MATCH (m:_SyncMeta) RETURN m.key AS key, m.value AS value ORDER BY key")
    domains = store.run("MATCH (d:Domain) RETURN d.name AS name ORDER BY name")
    original = store.run
    def fail(query, params=None, **kwargs):
        if ((failure_point == "mentions" and "m.body_provenance=$proof" in query)
                or (failure_point == "domain" and "MERGE (n)-[:IN_DOMAIN]->(d)" in query)):
            raise RuntimeError("injected link failure")
        return original(query, params, **kwargs)
    monkeypatch.setattr(store, "run", fail)
    result = apply(store, rows, [r["candidate_id"] for r in rows])
    assert not result["executed"] and result["error"] == "body candidate apply failed"
    assert count_nodes(store) == before
    assert body_links(store) == []
    assert store.run("MATCH (m:_SyncMeta) RETURN m.key AS key, m.value AS value ORDER BY key") == stamps
    assert store.run("MATCH (d:Domain) RETURN d.name AS name ORDER BY name") == domains


def test_unselected_invalid_candidate_rejects_whole_batch_before_writes(store):
    schema.migrate_body_mentions(store, approved=True)
    rows = preview(store)["candidates"]
    bad = candidate(title="quantum memory", needle="quantum memory")
    bad["spans"][0]["text"] = "fabricated"
    before = count_nodes(store)
    assert not apply(store, rows + [bad], [rows[0]["candidate_id"]])["executed"]
    assert count_nodes(store) == before


def test_optin_schema_rolls_back_and_missing_columns_do_not_fake_readiness(store, monkeypatch):
    original = store.run
    def fail(query, params=None, **kwargs):
        if query.startswith("ALTER TABLE MENTIONS ADD source_hash"):
            raise RuntimeError("injected schema failure")
        return original(query, params, **kwargs)
    monkeypatch.setattr(store, "run", fail)
    with pytest.raises(RuntimeError, match="injected schema"):
        schema.migrate_body_mentions(store, approved=True)
    assert not schema.body_mentions_ready(store)
    names = {r["name"] for r in store.run("CALL table_info('MENTIONS') RETURN *")}
    assert not any(name in names for name, _ in schema.BODY_MENTIONS_COLUMNS)
    store.run("MERGE (m:_SchemaMeta {key:$k}) SET m.value='1'", {"k": schema.BODY_MENTIONS_SCHEMA_KEY})
    assert not schema.body_mentions_ready(store)
    assert not apply(store, [candidate()], ["body:unknown"])["executed"]


def test_full_source_span_crosses_chunk_boundary_and_preserves_prior_approval(store):
    text = TEXT + "Long " * 20 + "quantum memory twice quantum memory."
    ds.put_doc(store, owner=OWNER, doc_id=DOC, text=text)
    schema.migrate_body_mentions(store, approved=True)
    raw = candidate(text, needle="quantum memory", title="Quantum memory")
    first_offset = raw["spans"][0]["char_start"]
    # 실제 적재 청크를 같은 원문에서 40문자씩 다시 나눈다. 경계 너머 스팬을 검증한다.
    ds.put_doc(store, owner=OWNER, doc_id=DOC, text=text + "\n", max_chars=40,
               boundaries=[first_offset + 5])
    text += "\n"
    raw = candidate(text, needle="quantum memory", title="Quantum memory")
    first = preview(store, [raw])["candidates"]
    assert apply(store, first, [first[0]["candidate_id"]])["executed"]
    assert len(body_links(store)) >= 2
    second_offset = text.rindex("quantum memory")
    second_raw = {**raw, "spans": [{"char_start": second_offset,
                                    "char_end": second_offset + len("quantum memory"),
                                    "text": "quantum memory"}]}
    second = preview(store, [second_raw])["candidates"]
    assert apply(store, second, [second[0]["candidate_id"]])["executed"]
    offsets = {s["char_start"] for row in body_links(store)
               for proof in json.loads(row["proof"]) for s in proof["spans"]}
    assert first_offset in offsets and second_offset in offsets


def test_http_handler_preserves_explicit_approval_and_authentication(store):
    from test_server import _app, _req, running_server
    schema.migrate_body_mentions(store, approved=True)
    with running_server(_app(store)) as base:
        body = {"owner": OWNER, "id": DOC, "candidates": [candidate()]}
        code, denied = _req(base, "/doc/extract", method="POST", body=body, token=None)
        assert code == 401
        code, result = _req(base, "/doc/extract", method="POST", body=body)
        assert code == 200 and result["executed"]
        body.update(apply=True, candidates=result["candidates"])
        code, denied = _req(base, "/doc/extract", method="POST", body=body)
        assert code == 200 and not denied["executed"]
        body["approved_candidate_ids"] = [result["candidates"][0]["candidate_id"]]
        code, applied = _req(base, "/doc/extract", method="POST", body=body)
        assert code == 200 and applied["executed"]
        assert applied["applied"] == {"created": 1, "matched": 0}


def test_body_match_preserves_authored_node_and_origin_only_change_preserves_spans(store):
    schema.migrate_body_mentions(store, approved=True)
    rows = preview(store)["candidates"]
    assert apply(store, rows, [rows[0]["candidate_id"]])["executed"]
    from store import pkns
    key = pkns.key(OWNER, "concept:양자-얽힘")
    store.run("MATCH (n:Concept) WHERE n.concept_id=$id SET n.title='Authored', "
              "n.one_liner='Authored summary', n.props='{\"user\":true}'", {"id": key})
    before = body_links(store)
    assert apply(store, rows, [rows[0]["candidate_id"]])["applied"]["matched"] == 1
    authored = store.run("MATCH (n:Concept) WHERE n.concept_id=$id RETURN n.title AS title, "
                         "n.one_liner AS ol, n.props AS props", {"id": key})[0]
    assert authored == {"title": "Authored", "ol": "Authored summary", "props": '{"user":true}'}
    assert body_links(store) == before
    ds.put_doc(store, owner=OWNER, doc_id=DOC, text=TEXT, origin="renamed/source.md")
    assert body_links(store) == before
