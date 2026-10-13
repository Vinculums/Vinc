from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from store.embedded_schema import (
    SCHEMA_VERSION, meta_table_specs, node_table_specs, render_embedded_ddl,
)
from store.topic_transfer import (
    TransferRefused, _apply_rehearsal, _restore_rehearsal, fingerprint, plan_transfer,
    read_snapshot, transformed_snapshot,
)

SOURCE = "user:synthetic-person"
TARGET = "team:00000000-0000-4000-8000-000000000001"
TOPIC = "vinc/project"
STAMP = "2026-10-13T10:00:00.000Z"
SEP = "\x1f"


def seed():
    specs = {**node_table_specs(), **meta_table_specs()}
    result = {"schema_version": SCHEMA_VERSION,
              "nodes": {k: [] for k in node_table_specs()},
              "meta": {k: [] for k in meta_table_specs()}, "edges": []}

    def add(table, key, **fields):
        row = {c: None for c in specs[table]["cols"]}
        row.update({specs[table]["pk"]: key}, **fields)
        result["meta" if table.startswith("_") else "nodes"][table].append(row)
        return row

    for label, nid, topic in (("Concept", "concept:a", TOPIC),
                              ("Concept", "concept:b", TOPIC + "/child"),
                              ("Concept", "concept:doc.doc1", TOPIC + "/child"),
                              ("Record", "record:evidence", TOPIC),
                              ("Decision", "decision:d", TOPIC),
                              ("Episode", "episode:e", TOPIC),
                              ("Concept", "concept:other", "vinc/projectile")):
        add(label, SOURCE + SEP + nid, owner_id=SOURCE, domain=topic, title=nid,
            tags=["synthetic"], props=json.dumps({"history": [{"at": "2020-01-01", "value": "kept"}]}),
            rev=7, sync_seq=40, updated_at="2020-01-01T00:00:00.000Z")
    add("Bridge", SOURCE + SEP + "bridge:inside", owner_id=SOURCE,
        source_domain=TOPIC, target_domain=TOPIC + "/child", rev=3, sync_seq=40)
    add("Domain", TOPIC)
    add("Domain", TOPIC + "/child")
    add("_SyncMeta", "seq:" + SOURCE, value="80")
    add("_SyncMeta", "topic-transfer:synthetic", value="tests-only")
    add("_SyncBase", SOURCE + SEP + "concept:a", label="Concept", fields='{"title":"concept:a"}',
        base_rev=7, base_updated_at="2020-01-01T00:00:00.000Z")
    body = "Synthetic body \u001f with " + "[[" + "concept:a" + "]] and complete offsets."
    add("_DocMeta", "doc1", owner_id=SOURCE, title="Synthetic document", origin="synthetic",
        content_hash=hashlib.sha256(body.encode()).hexdigest(), chunk_count=1,
        sync_seq=41, needs_embed=0, updated_at="2020-01-01T00:00:00.000Z",
        created_at="2019-01-01T00:00:00.000Z", content_time="1905")
    add("Chunk", "doc1#000", doc_id="doc1", owner_id=SOURCE, text=body,
        chunk_index=0, char_start=0, char_end=len(body), char_count=len(body),
        embedding=[1.0, 0.0, -1.0, 0.25], lex="synthetic body", source_file="synthetic")
    add("ChunkVec", "doc1#000#000", chunk_id="doc1#000", doc_id="doc1", owner_id=SOURCE,
        seq=0, embedding=[0.25, 0.5, 0.75, -1.0], encoder_runtime="synthetic-runtime")
    add("NodeVec", SOURCE + "#concept:a", node_id="concept:a", kind="concept", owner_id=SOURCE,
        embedding=[1.0, -0.5, 0.25, 0.0], text="synthetic", model="synthetic-model",
        encoder_runtime="synthetic-runtime")

    def edge(label, sl, src, dl, dst, **props):
        from store.embedded_schema import rel_table_specs
        spec = next(s for s in rel_table_specs() if (s["label"], s["from"], s["to"]) == (label, sl, dl))
        fields = {p: None for p in spec["props"]}
        fields.update(props)
        result["edges"].append({"label": label, "from_label": sl, "to_label": dl,
                                "from": src, "to": dst, "props": fields})

    a, b = SOURCE + SEP + "concept:a", SOURCE + SEP + "concept:b"
    edge("DERIVES_FROM", "Concept", a, "Concept", b, evidence="[[doc1]]", confidence=0.75,
         created="2010-01-01")
    edge("DERIVES_FROM", "Concept", b, "Concept", a, evidence="reverse", confidence=0.5,
         created="2011-01-01")
    # 속성이 같은 병렬 관계의 개수도 보존한다.
    result["edges"].append(copy.deepcopy(result["edges"][0]))
    edge("ATTACHES_TO", "Record", SOURCE + SEP + "record:evidence", "Decision", SOURCE + SEP + "decision:d",
         type="supports", evidence="[[doc1]]", confidence=0.9, created="2012-01-01")
    edge("ABOUT", "Episode", SOURCE + SEP + "episode:e", "Concept", a, evidence="[[doc1]]", confidence=0.8)
    edge("MENTIONS", "Chunk", "doc1#000", "Concept", a, method="title")
    edge("IN_DOMAIN", "Concept", a, "Domain", TOPIC)
    edge("IN_DOMAIN", "Concept", b, "Domain", TOPIC + "/child")
    edge("CROSSES", "Bridge", SOURCE + SEP + "bridge:inside", "Domain", TOPIC)
    edge("PARENT_OF", "Domain", TOPIC, "Domain", TOPIC + "/child")
    return result


def plan(snapshot=None):
    return plan_transfer(snapshot or seed(), source=SOURCE, target=TARGET, topic=TOPIC)


@pytest.mark.parametrize("field,value", [
    ("props", '{"nested":{"nodeId":"concept:a"}}'),
    ("title", "See [[doc1]]"),
])
def test_incoming_logical_references_block_without_expanding_scope(field, value):
    data = seed()
    other = next(r for r in data["nodes"]["Concept"] if r["concept_id"].endswith("concept:other"))
    other[field] = value
    before = fingerprint(data)
    result = plan(data)
    assert any(b.startswith("incoming_reference:") for b in result.blockers)
    assert ("Concept", other["concept_id"]) not in result.selected
    assert fingerprint(data) == before


def test_incoming_document_successor_blocks():
    data = seed()
    meta = copy.deepcopy(data["meta"]["_DocMeta"][0])
    meta.update(doc_id="outside", superseded_by="doc1")
    data["meta"]["_DocMeta"].append(meta)
    assert any(b.startswith("incoming_reference:") for b in plan(data).blockers)


def test_outgoing_wiki_reference_in_native_title_blocks():
    data = seed()
    data["nodes"]["Concept"][0]["title"] = "See [[" + "concept:other" + "]]"
    assert "external_reference:concept:other" in plan(data).blockers


def test_wiki_aliases_retain_the_target_identity():
    data = seed()
    data["nodes"]["Concept"][0]["title"] = "[[concept:b|Inside]]"
    assert plan(data).ready
    data["nodes"]["Concept"][0]["title"] = "[[concept:other|Outside]]"
    assert "external_reference:concept:other" in plan(data).blockers


def test_other_owner_logical_alias_is_not_a_source_dependency():
    data = seed()
    other = copy.deepcopy(data["nodes"]["Concept"][-1])
    other.update(concept_id="user:another" + SEP + "concept:other", owner_id="user:another",
                 title="An alias to my own [[" + "concept:a" + "]]")
    data["nodes"]["Concept"].append(other)
    assert plan(data).ready


class RawStore:
    def __init__(self, path):
        from store.embedded_backend import _import_driver
        driver, _ = _import_driver()
        self.path = str(path)
        # Conversion verifies every table while a write transaction pins pages.
        # Keep the synthetic pool large enough for that engine overhead; the
        # independent 5,000-row / 32-MiB conversion budget remains unchanged.
        self.db = driver.Database(str(path), buffer_pool_size=256 * 1024 * 1024, max_num_threads=2)
        self.conn = driver.Connection(self.db)

    def run(self, q, params=None, *, raw=True):
        result = self.conn.execute(q, params) if params else self.conn.execute(q)
        rows = []
        for res in result if isinstance(result, list) else [result]:
            cols = res.get_column_names()
            while res.has_next():
                rows.append(dict(zip(cols, res.get_next())))
        return rows

    def close(self):
        self.conn.close()
        self.db.close()

    def reopen(self):
        replacement = RawStore(self.path)
        self.db, self.conn = replacement.db, replacement.conn


def insert(store, data):
    specs = {**node_table_specs(), **meta_table_specs()}
    for group in ("nodes", "meta"):
        for label, rows in data[group].items():
            for row in rows:
                fields = ", ".join(f"{c}:${c}" for c in row)
                store.run(f"CREATE (n:{label} {{{fields}}})", row)
    for e in data["edges"]:
        fields = ", ".join(f"{c}:${c}" for c in e["props"])
        store.run(f"MATCH (a:{e['from_label']}), (b:{e['to_label']}) "
                  f"WHERE a.{specs[e['from_label']]['pk']}=$src AND b.{specs[e['to_label']]['pk']}=$dst "
                  f"CREATE (a)-[:{e['label']}" + (" {" + fields + "}" if fields else "") + "]->(b)",
                  {"src": e["from"], "dst": e["to"], **e["props"]})


@pytest.fixture
def native(tmp_path, request):
    from store.embedded_backend import _import_driver
    driver, _ = _import_driver()
    if driver is None:
        pytest.skip("native graph driver not installed")
    store = RawStore(tmp_path / "synthetic.db")
    for ddl in render_embedded_ddl(dim=4):
        store.run(ddl)
    data = seed()
    if getattr(request, "param", None) == "physical":
        data["nodes"]["Chunk"][0]["chunk_id"] = SOURCE + SEP + "doc1#000"
        data["nodes"]["ChunkVec"][0]["vec_id"] = SOURCE + SEP + "doc1#000#000"
        data["nodes"]["ChunkVec"][0]["chunk_id"] = SOURCE + SEP + "doc1#000"
        next(e for e in data["edges"] if e["label"] == "MENTIONS")["from"] = SOURCE + SEP + "doc1#000"
    insert(store, data)
    try:
        yield store
    finally:
        store.close()


def test_subtree_only_and_all_payload_fields_preserved():
    before = seed()
    p = plan(before)
    assert p.ready, p.blockers
    assert ("Concept", SOURCE + SEP + "concept:other") not in p.selected
    after = transformed_snapshot(before, p, stamp=STAMP)
    for label in ("Chunk", "ChunkVec", "NodeVec"):
        left, right = before["nodes"][label][0], after["nodes"][label][0]
        for field in left:
            if field not in ("owner_id", "vec_id"):
                assert right[field] == left[field]
    assert after["edges"][0]["props"] == before["edges"][0]["props"]
    assert len(after["edges"]) == len(before["edges"])
    assert after["nodes"]["Concept"][3] == before["nodes"]["Concept"][3]
    doc = after["meta"]["_DocMeta"][0]
    assert doc["doc_id"] == "doc1" and doc["needs_embed"] == 0 and doc["content_time"] == "1905"
    assert doc["created_at"] == before["meta"]["_DocMeta"][0]["created_at"]
    assert after["meta"]["_SyncBase"][0]["fields"] == before["meta"]["_SyncBase"][0]["fields"]
    assert after["meta"]["_SyncBase"][0]["node_id"] == TARGET + SEP + "concept:a"
    assert int(next(r["value"] for r in after["meta"]["_SyncMeta"] if r["key"] == "seq:" + SOURCE)) > 80
    assert int(next(r["value"] for r in after["meta"]["_SyncMeta"] if r["key"] == "seq:" + TARGET)) > 0
    assert fingerprint(before) == fingerprint(seed())


@pytest.mark.parametrize("problem", ["outgoing", "incoming", "shared_doc", "foreign_doc", "bridge",
                                    "integrity", "attachments", "reference", "rejection", "tombstone",
                                    "nonempty", "metadata", "extra_column", "missing_table", "conventions"])
def test_preflight_refuses_without_mutation(problem):
    data = seed()
    if problem in ("outgoing", "incoming"):
        edge = copy.deepcopy(data["edges"][0])
        edge["to" if problem == "outgoing" else "from"] = SOURCE + SEP + "concept:other"
        data["edges"].append(edge)
    elif problem == "shared_doc":
        edge = copy.deepcopy(next(e for e in data["edges"] if e["label"] == "MENTIONS"))
        edge["to"] = SOURCE + SEP + "concept:other"
        data["edges"].append(edge)
    elif problem == "foreign_doc":
        data["meta"]["_DocMeta"][0]["owner_id"] = "user:another"
    elif problem == "bridge":
        data["nodes"]["Bridge"][0]["target_domain"] = "vinc/other"
    elif problem == "integrity":
        data["nodes"]["Chunk"][0]["text"] += "broken"
    elif problem == "attachments":
        data["nodes"]["Record"][0]["props"] = '{"attachments":["private/blob"]}'
    elif problem == "reference":
        data["nodes"]["Decision"][0]["props"] = '{"superseded_by":"decision:outside"}'
    elif problem == "rejection":
        specs = meta_table_specs()["_MergeRejection"]
        row = {c: None for c in specs["cols"]}
        row.update(pair_key="synthetic", owner_id=SOURCE, a_id="concept:a", b_id="concept:other")
        data["meta"]["_MergeRejection"].append(row)
    elif problem == "tombstone":
        row = {c: None for c in meta_table_specs()["_Tombstone"]["cols"]}
        row.update(node_id=SOURCE + SEP + "concept:a", owner_id=SOURCE, label="Concept")
        data["meta"]["_Tombstone"].append(row)
    elif problem == "nonempty":
        data["nodes"]["Concept"][-1]["owner_id"] = TARGET
    elif problem == "metadata":
        data["meta"]["_SyncMeta"].append({"key": "seq:" + TARGET, "value": "0"})
    elif problem == "extra_column":
        data["nodes"]["Concept"][0]["unrecognized"] = "must not lose"
    elif problem == "missing_table":
        del data["meta"]["_DocTombstone"]
        with pytest.raises(TransferRefused):
            plan(data)
        return
    elif problem == "conventions":
        data["meta"]["_DocMeta"][0]["origin"] = "vinc:conventions"
    before = fingerprint(data)
    p = plan(data)
    assert not p.ready, problem
    with pytest.raises(TransferRefused):
        transformed_snapshot(data, p, stamp=STAMP)
    assert fingerprint(data) == before


def test_stale_plan_and_namespace_refused():
    data = seed()
    p = plan(data)
    data["nodes"]["Record"][0]["title"] = "changed"
    with pytest.raises(TransferRefused, match="plan_stale"):
        transformed_snapshot(data, p, stamp=STAMP)
    with pytest.raises(TransferRefused):
        plan_transfer(seed(), source=SOURCE, target=SOURCE, topic=TOPIC)
    data = seed()
    data["nodes"]["Decision"][0]["decision_id"] = "decision:d"
    assert any(b.startswith("namespaced_node_key_required") for b in plan(data).blockers)


def test_native_commit_preserves_exact_payload_and_idempotency(native):
    before = read_snapshot(native)
    p = plan(before)
    assert p.ready, p.blockers
    expected = transformed_snapshot(before, p, stamp=STAMP)
    assert _apply_rehearsal(native, p, run_id="synthetic-1", stamp=STAMP) == "committed"
    actual = read_snapshot(native)
    actual["meta"]["_SyncMeta"] = [r for r in actual["meta"]["_SyncMeta"] if not r["key"].startswith("topic-transfer:run:")]
    assert fingerprint(actual) == fingerprint(expected)
    assert _apply_rehearsal(native, p, run_id="synthetic-1", stamp=STAMP) == "already_committed"


@pytest.mark.parametrize("phase", ["nodes", "edges", "delete", "journal"])
def test_native_failure_rolls_back_source_and_all_metadata(native, phase):
    before = read_snapshot(native)
    p = plan(before)
    with pytest.raises(RuntimeError, match="injected"):
        _apply_rehearsal(native, p, run_id="synthetic-failure", stamp=STAMP, fail_at=phase)
    assert fingerprint(read_snapshot(native)) == fingerprint(before)
    assert _apply_rehearsal(native, p, run_id="synthetic-failure", stamp=STAMP) == "committed"


def test_native_target_provision_race_rejected(native):
    p = plan(read_snapshot(native))
    native.run("CREATE (c:Concept {concept_id:$id, owner_id:$owner, title:'agent pack'})",
               {"id": TARGET + SEP + "concept:agent", "owner": TARGET})
    before = fingerprint(read_snapshot(native))
    with pytest.raises(TransferRefused, match="plan_stale"):
        _apply_rehearsal(native, p, run_id="synthetic-race", stamp=STAMP)
    assert fingerprint(read_snapshot(native)) == before


@pytest.mark.parametrize("payload", [
    {"nodes": [{"id": "concept:a", "type": "concept", "title": "future offline edit",
                "_rev": 999, "_updated_at": "2099-01-01T00:00:00.000Z"}]},
    {"edges": [{"from": "concept:a", "to": "concept:b", "type": "derives_from"}]},
    {"tombstones": [{"node_id": "concept:a", "label": "Concept"}]},
])
def test_native_offline_node_reupload_is_refused_before_any_write(native, payload):
    from api.sync import apply_delta
    p = plan(read_snapshot(native))
    _apply_rehearsal(native, p, run_id="offline", stamp=STAMP)
    before = fingerprint(read_snapshot(native))
    res = apply_delta(owner=SOURCE, bundle=payload, store=native)
    assert res["executed"] is False and res["error"] == "ownership_moved"
    assert fingerprint(read_snapshot(native)) == before


@pytest.mark.parametrize("payload", [
    {"docs": [{"doc_id": "doc1", "text": "future edit", "updated_at": "2099-01-01T00:00:00.000Z"}]},
    {"tombstones": [{"doc_id": "doc1", "deleted_at": "2099-01-01T00:00:00.000Z"}]},
])
def test_native_offline_document_upload_and_delete_refused(native, payload):
    from api.docsync import import_docs
    p = plan(read_snapshot(native))
    _apply_rehearsal(native, p, run_id="offline-doc", stamp=STAMP)
    before = fingerprint(read_snapshot(native))
    res = import_docs(native, owner=SOURCE, payload=payload)
    assert res["executed"] is False and res["error"] == "ownership_moved"
    assert fingerprint(read_snapshot(native)) == before


def test_native_marker_does_not_grant_target_access_or_alias_other_owners(native):
    from store.ownership_moves import blocked_sync_references
    p = plan(read_snapshot(native))
    _apply_rehearsal(native, p, run_id="isolation", stamp=STAMP)
    payload = {"nodes": [{"id": "concept:a"}], "docs": [{"doc_id": "doc1"}]}
    assert blocked_sync_references(native, owner=SOURCE, payload=payload) == ["concept:a", "doc1"]
    assert blocked_sync_references(native, owner=TARGET, payload=payload) == []
    assert blocked_sync_references(native, owner="user:another", payload=payload) == []


def test_native_resume_verifies_committed_payload(native):
    p = plan(read_snapshot(native))
    _apply_rehearsal(native, p, run_id="verify-resume", stamp=STAMP)
    native.run("MATCH (c:Concept) WHERE c.owner_id=$owner SET c.title='corrupted'", {"owner": TARGET})
    with pytest.raises(TransferRefused, match="committed_payload_changed"):
        _apply_rehearsal(native, p, run_id="verify-resume", stamp=STAMP)


def test_native_compensation_restores_original_and_cannot_replay_cancelled_run(native):
    before = read_snapshot(native)
    p = plan(before)
    _apply_rehearsal(native, p, run_id="cancel", stamp=STAMP)
    assert _restore_rehearsal(native, before, p, run_id="cancel", stamp=STAMP) == "restored"
    current = read_snapshot(native)
    current["meta"]["_SyncMeta"] = [r for r in current["meta"]["_SyncMeta"] if r["key"] != "topic-transfer:run:cancel"]
    assert fingerprint(current) == fingerprint(before)
    assert _restore_rehearsal(native, before, p, run_id="cancel", stamp=STAMP) == "already_restored"
    with pytest.raises(TransferRefused, match="run_cancelled"):
        _apply_rehearsal(native, p, run_id="cancel", stamp=STAMP)


def test_native_compensation_failure_keeps_recoverable_committed_state(native):
    before = read_snapshot(native)
    p = plan(before)
    _apply_rehearsal(native, p, run_id="compensation-failure", stamp=STAMP)
    committed = fingerprint(read_snapshot(native))
    with pytest.raises(RuntimeError, match="injected"):
        _restore_rehearsal(native, before, p, run_id="compensation-failure", stamp=STAMP, fail_at="delete")
    assert fingerprint(read_snapshot(native)) == committed
    assert _restore_rehearsal(native, before, p, run_id="compensation-failure", stamp=STAMP) == "restored"


def test_native_compensation_refuses_to_overwrite_subsequent_changes(native):
    before = read_snapshot(native)
    p = plan(before)
    _apply_rehearsal(native, p, run_id="changed", stamp=STAMP)
    native.run("MATCH (c:Concept) WHERE c.owner_id=$owner SET c.title='new edit'", {"owner": TARGET})
    changed = fingerprint(read_snapshot(native))
    with pytest.raises(TransferRefused, match="compensation_source_changed"):
        _restore_rehearsal(native, before, p, run_id="changed", stamp=STAMP)
    assert fingerprint(read_snapshot(native)) == changed


def test_native_unknown_columns_refused_instead_of_dropped(native):
    native.run("ALTER TABLE Concept ADD unrecognized STRING DEFAULT 'kept'")
    with pytest.raises(TransferRefused, match="unsupported_schema_columns"):
        read_snapshot(native)


def test_native_executor_refuses_a_store_without_synthetic_marker(native):
    p = plan(read_snapshot(native))
    native.run("MATCH (m:_SyncMeta) WHERE m.key='topic-transfer:synthetic' DELETE m")
    before = fingerprint(read_snapshot(native))
    with pytest.raises(TransferRefused, match="synthetic_store_required"):
        _apply_rehearsal(native, p, run_id="must-not-write", stamp=STAMP)
    assert fingerprint(read_snapshot(native)) == before


@pytest.mark.parametrize("native", ["physical"], indirect=True)
def test_native_rekey_physical_chunk_and_vectors(native):
    # 오래된 물리 청크 키 형식도 새 소유자의 물리 키로 함께 옮긴다.
    original = read_snapshot(native)
    p = plan(original)
    _apply_rehearsal(native, p, run_id="physical-chunks", stamp=STAMP)
    after = read_snapshot(native)
    assert after["nodes"]["Chunk"][0]["chunk_id"] == TARGET + SEP + "doc1#000"
    assert after["nodes"]["ChunkVec"][0]["chunk_id"] == TARGET + SEP + "doc1#000"
    assert after["nodes"]["ChunkVec"][0]["vec_id"] == TARGET + SEP + "doc1#000#000"


@pytest.mark.parametrize("mode", ["uncommitted", "committed"])
def test_native_process_interrupt_and_resume(native, mode):
    before = read_snapshot(native)
    p = plan(before)
    native.run("CHECKPOINT")
    native.close()
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "crash", mode, native.path],
                                cwd=ROOT, capture_output=True, timeout=45)
    finally:
        native.reopen()
    assert result.returncode == 87, (result.stdout, result.stderr)
    if mode == "uncommitted":
        assert fingerprint(read_snapshot(native)) == fingerprint(before)
        assert _apply_rehearsal(native, p, run_id="crash-test", stamp=STAMP) == "committed"
    else:
        assert _apply_rehearsal(native, p, run_id="crash-test", stamp=STAMP) == "already_committed"


class SyntheticAccount:
    """SQLite test double only; not the production MySQL account repository."""
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS reservations (run TEXT PRIMARY KEY, actor TEXT, target TEXT UNIQUE, status TEXT)")
        self.db.commit()

    def reserve(self, run, actor, target):
        self.db.execute("INSERT OR IGNORE INTO reservations VALUES (?, ?, ?, 'prepared')", (run, actor, target))
        row = self.db.execute("SELECT actor, target FROM reservations WHERE run=?", (run,)).fetchone()
        if row != (actor, target):
            raise TransferRefused("account_reservation_mismatch")
        self.db.commit()

    def advance(self, run, actor, status):
        row = self.db.execute("SELECT actor, status FROM reservations WHERE run=?", (run,)).fetchone()
        if not row or row[0] != actor:
            raise TransferRefused("actor_not_owner")
        steps = ["prepared", "graph_committed", "verified", "active"]
        if steps.index(status) > steps.index(row[1]) + 1:
            raise TransferRefused("verification_required")
        if steps.index(status) > steps.index(row[1]):
            self.db.execute("UPDATE reservations SET status=? WHERE run=?", (status, run))
            self.db.commit()

    def visible(self, run):
        return self.db.execute("SELECT status FROM reservations WHERE run=?", (run,)).fetchone()[0] == "active"


def test_synthetic_account_prepare_verify_activate_and_resume(native, tmp_path):
    account = SyntheticAccount(tmp_path / "synthetic-account.sqlite")
    p = plan(read_snapshot(native))
    account.reserve("saga", SOURCE, TARGET)
    assert not account.visible("saga")
    with pytest.raises(TransferRefused, match="verification_required"):
        account.advance("saga", SOURCE, "active")
    with pytest.raises(TransferRefused, match="actor_not_owner"):
        account.advance("saga", "user:someone-else", "graph_committed")
    _apply_rehearsal(native, p, run_id="saga", stamp=STAMP)
    # 그래프 커밋 뒤 계정 단계 기록 전 중단을 재현한다.
    account.db.close()
    account = SyntheticAccount(tmp_path / "synthetic-account.sqlite")
    account.reserve("saga", SOURCE, TARGET)
    assert _apply_rehearsal(native, p, run_id="saga", stamp=STAMP) == "already_committed"
    assert not account.visible("saga")
    account.advance("saga", SOURCE, "graph_committed")
    # index/cache 검증 실패는 활성화하지 않고 재개할 상태로 남긴다.
    assert not account.visible("saga")
    account.advance("saga", SOURCE, "verified")
    account.advance("saga", SOURCE, "active")
    assert account.visible("saga")
    account.db.close()


def test_synthetic_saga_keeps_team_unavailable_until_verifier_passes(native, tmp_path):
    from api.topic_transfer_rehearsal import resume_rehearsal
    account = SyntheticAccount(tmp_path / "coordinator-account.sqlite")
    p = plan(read_snapshot(native))
    with pytest.raises(TransferRefused, match="actor_not_source_owner"):
        resume_rehearsal(native, account, p, actor="user:another", run_id="coordinator", stamp=STAMP,
                         verify=lambda: True)
    with pytest.raises(TransferRefused, match="activation_verification_failed"):
        resume_rehearsal(native, account, p, actor=SOURCE, run_id="coordinator", stamp=STAMP,
                         verify=lambda: False)
    assert not account.visible("coordinator")
    account.db.close()
    account = SyntheticAccount(tmp_path / "coordinator-account.sqlite")
    assert resume_rehearsal(native, account, p, actor=SOURCE, run_id="coordinator", stamp=STAMP,
                            verify=lambda: True) == "active"
    assert account.visible("coordinator")
    account.db.close()


if __name__ == "__main__":
    import os

    if len(sys.argv) != 4 or sys.argv[1] != "crash":
        raise SystemExit("Only the synthetic subprocess crash harness is supported.")
    crash_store = RawStore(sys.argv[3])
    crash_plan = plan(read_snapshot(crash_store))
    if sys.argv[2] == "uncommitted":
        original_run = crash_store.run

        def crash_before_commit(query, params=None, *, raw=True):
            if query == "COMMIT":
                os._exit(87)
            return original_run(query, params, raw=raw)

        crash_store.run = crash_before_commit
    _apply_rehearsal(crash_store, crash_plan, run_id="crash-test", stamp=STAMP)
    os._exit(87)
