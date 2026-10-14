"""고정 버전과 실제 단일 writer의 가져오기·충돌·프롬프트 경계를 검증한다."""
import base64
import copy
import hashlib
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from api import community_agents as ca, fragment, packfile, query, roles as role_read
from api.server import VincApp
from api.cli_tools import _InProcessBridge
from vinc_mcp import roles
from test_decision_type import _fresh_store, _restore

OWNER = "user:0191f0aa-1111-7abc-8def-0123456789ab"
OTHER = "user:0191f0aa-2222-7abc-8def-0123456789ab"


def authored():
    snapshot = {"role": {"id": "concept:agent-fixture", "name": "agent-fixture", "label": "Fixture tutor",
        "domain": "agent", "tags": ["agent", "execute", "role"], "props": {
        "mission": "Explain a public fixture.", "tier": "execute", "rung": "fast", "never": ["Execute code"],
        "person_only": [{"action": "activate", "reason": "A person chooses adoption."}], "repos": ["Vinc"],
        "tools": ["Search"], "verify": "Inspect the entire result."}}, "prompt": "Full authored text\n``` nested ```\n끝",
        "tools": ["Search"], "references": [{"kind": "page", "community": str(uuid4()), "page": "fixture",
            "url": "/community/synthetic/p/fixture", "title": "Public fixture"}],
        "license": "CC-BY-SA-4.0"}
    share = str(uuid4())
    source = {"url": f"https://vincs.io/community/a/{share}/v/1", "share_id": share, "version": 1,
              "hash": ca.digest(snapshot), "author": {"handle": "fixture", "deleted": False}}
    return snapshot, source


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on")
    monkeypatch.setenv("VINC_DURABLE_ACK", "off")
    monkeypatch.setenv("VINC_TOPIC_CONVERSION", "0")
    monkeypatch.setenv("VINC_STORE_SNAPSHOT", "")
    store, keep = _fresh_store()
    app = VincApp(store=store)
    try:
        yield store, app
    finally:
        app.jobs.shutdown()
        store.close()
        _restore(keep)


def save(app, snapshot, source, **kwargs):
    return app.import_community_agent({"owner": OWNER, "snapshot": snapshot, "source": source,
        "mutation_id": str(uuid4()), **kwargs})[1]


@pytest.mark.parametrize("format", ["claude", "gpt", "skill", "vincpack"])
def test_exports_preserve_full_contract_and_are_byte_deterministic(format):
    snapshot, source = authored()
    first = roles.render_community_export(snapshot, source, format)
    reverse = copy.deepcopy(snapshot)
    reverse["role"]["props"] = dict(reversed(list(reverse["role"]["props"].items())))
    again = roles.render_community_export(reverse, source, format)
    assert first == again
    content = base64.b64decode(first["content"]) if first["encoding"] == "base64" else first["content"].encode()
    assert first["content_hash"] == hashlib.sha256(content).hexdigest()
    if format == "vincpack":
        pack = packfile.from_bytes(content)
        props = pack["fragment"]["nodes"][0]["props"]
        assert props["shared_snapshot"] == snapshot
        assert props["community_source"] == source
        assert props["status"] == "proposed" and props["active"] is False
    else:
        assert roles.render_shell(snapshot["role"]) in first["content"]
        assert snapshot["prompt"] in first["content"]
        assert ca.canonical(snapshot) in first["content"]
        assert source["url"] in first["content"]


@pytest.mark.parametrize("bad", [1.0, float("nan"), 2**53, "\ud800"])
def test_cross_language_canonical_rejects_ambiguous_values(bad):
    with pytest.raises(ValueError):
        ca.canonical({"field": bad})


@pytest.mark.parametrize("kind", ["page", "thread", "pack"])
def test_public_reference_exact_targets_and_no_private_url(kind):
    snapshot, source = authored()
    ref = {"kind": kind, "community": str(uuid4()), "title": "Public title"}
    if kind in {"page", "thread"}:
        ref.update(page="fixture", url="/community/synthetic/p/fixture")
    if kind in {"thread", "pack"}:
        ref["id"] = str(uuid4())
        ref["url"] = ref["url"] + "/talk/" + ref["id"] if kind == "thread" else "/community/packs/" + ref["id"]
    snapshot["references"] = [ref]
    source["hash"] = ca.digest(snapshot)
    ca.validate(snapshot, source)
    ref["url"] = "https://private.example/" + ref["url"]
    source["hash"] = ca.digest(snapshot)
    with pytest.raises(ValueError, match="reference_url"):
        ca.validate(snapshot, source)


def test_import_receipt_provenance_and_all_role_entrypoints_exclude_proposed(engine):
    store, app = engine
    snapshot, source = authored()
    op = str(uuid4())
    body = {"owner": OWNER, "snapshot": snapshot, "source": source, "mutation_id": op}
    result = app.import_community_agent(body)[1]
    assert result["executed"] and result["receipt"]["state"] == "committed", result
    node = query.node(result["node_id"], owner=OWNER, store=store)["node"]
    assert node["props"]["shared_snapshot"] == snapshot
    assert node["props"]["community_source"] == source
    assert role_read.read(result["node_id"], owner=OWNER, store=store)["found"] is False
    bridge = _InProcessBridge(app)
    bridge.owner = OWNER
    assert roles.list_prompts(bridge)["prompts"] == []
    with pytest.raises(ValueError, match="unknown prompt"):
        roles.get_prompt("agent-fixture", {}, bridge)
    assert app.import_community_agent(body)[1]["replayed"] is True
    assert query.node(result["node_id"], owner=OWNER, store=store)["node"]["rev"] == node["rev"]
    assert ca.import_result(store, owner=OTHER, mutation_id=op)["state"] == "unknown"
    changed = copy.deepcopy(body)
    changed["snapshot"]["prompt"] += " changed"
    changed["source"]["hash"] = ca.digest(changed["snapshot"])
    assert app.import_community_agent(changed)[1]["error"] == "operation_id_conflict"
    fragment.merge_fragment({"owner": OWNER, "nodes": [{"id": result["node_id"], "type": "concept",
        "props": {**node["props"], "active": True}}]}, store=store)
    assert role_read.read(result["node_id"], owner=OWNER, store=store)["found"] is False
    fragment.merge_fragment({"owner": OWNER, "nodes": [{"id": result["node_id"], "type": "concept",
        "props": {**node["props"], "active": True, "status": "authored"}}]}, store=store)
    assert role_read.read(result["node_id"], owner=OWNER, store=store)["found"] is True
    activated = roles.get_prompt("agent-fixture", {}, bridge)
    assert ca.canonical({"snapshot": snapshot, "source": source}) in activated["messages"][0]["content"]["text"]


def test_default_collisions_including_hash_prefix_preserve_existing_roles(engine):
    store, app = engine
    snapshot, source = authored()
    rid = snapshot["role"]["id"]
    occupied = [rid, rid + "-" + source["hash"][:6]]
    fragment.merge_fragment({"owner": OWNER, "nodes": [{"id": i, "type": "concept", "title": "Keep me",
        "props": {"private": i}} for i in occupied]}, store=store)
    result = save(app, snapshot, source)
    assert result["executed"] and result["node_id"] == rid + "-" + source["hash"][:12], result
    for ident in occupied:
        assert query.node(ident, owner=OWNER, store=store)["node"]["title"] == "Keep me"
    other = app.import_community_agent({"owner": OTHER, "snapshot": snapshot, "source": source,
        "mutation_id": str(uuid4())})[1]
    assert other["executed"] and other["node_id"] == rid


def test_replace_exact_target_changed_deleted_and_two_editors(engine):
    store, app = engine
    snapshot, source = authored()
    rid = save(app, snapshot, source)["node_id"]
    expected = ca.preview(store, owner=OWNER, snapshot=snapshot, source=source)["target"]
    fragment.merge_fragment({"owner": OWNER, "nodes": [{"id": rid, "type": "concept", "title": "Human edit"}]}, store=store)
    result = save(app, snapshot, source, mode="replace", expected_target=expected)
    assert result["error"] == "agent_target_changed" and result["rolled_back"] is True
    assert query.node(rid, owner=OWNER, store=store)["node"]["title"] == "Human edit"
    current = ca.preview(store, owner=OWNER, snapshot=snapshot, source=source)["target"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: save(app, snapshot, source, mode="replace", expected_target=current), range(2)))
    assert sum(r["executed"] for r in results) == 1, results
    before_delete = ca.preview(store, owner=OWNER, snapshot=snapshot, source=source)["target"]
    query.delete_node(rid, owner=OWNER, store=store)
    assert save(app, snapshot, source, mode="replace", expected_target=before_delete)["error"] == "agent_target_changed"


def test_preview_and_render_are_pure_and_invalid_input_does_not_write(engine):
    store, app = engine
    snapshot, source = authored()
    assert app.preview_community_agent({"owner": OWNER, "snapshot": snapshot, "source": source})[1]["target"] is None
    assert app.render_community_agent({"snapshot": snapshot, "source": source, "format": "claude"})[1]["executed"]
    assert not query.node(snapshot["role"]["id"], owner=OWNER, store=store)["found"]
    bad = copy.deepcopy(snapshot)
    bad["role"]["props"]["owner"] = "foreign"
    with pytest.raises(ValueError, match="invalid_agent_props"):
        save(app, bad, source)
    with pytest.raises(ValueError, match="requires_target_snapshot"):
        save(app, snapshot, source, mode="replace")


@pytest.mark.parametrize("namespaced", [False, True])
def test_downloaded_pack_uses_safe_import_and_replays_without_duplicate(engine, monkeypatch, namespaced):
    from api import share
    store, app = engine
    monkeypatch.setenv("VINC_PK_NAMESPACED", "on" if namespaced else "off")
    snapshot, source = authored()
    file = roles.render_community_export(snapshot, source, "vincpack")
    pack = packfile.from_bytes(base64.b64decode(file["content"]))
    owner = OWNER if namespaced else "user:local"
    original = snapshot["role"]["id"]
    fragment.merge_fragment({"owner": OTHER, "nodes": [{"id": original, "type": "concept", "title": "Foreign preserved"}]}, store=store)
    # 파일의 활성/소유 상태는 권한이 아니다. 고정된 계약으로 제안 역할을 다시 만든다.
    pack["fragment"]["nodes"][0]["props"].update(active=True, status="authored", owner=OTHER)
    first = app.jobs.run_sync("ingest:pack", lambda: share.import_share_pack(pack, owner=owner, store=store))
    assert first["executed"] and first["status"] == "proposed", first
    assert first["node_id"] == original if namespaced else first["node_id"] != original
    assert query.node(original, owner=OTHER, store=store)["node"]["title"] == "Foreign preserved"
    imported = query.node(first["node_id"], owner=owner, store=store)["node"]
    assert imported["props"]["active"] is False and "owner" not in imported["props"]
    again = app.jobs.run_sync("ingest:pack", lambda: share.import_share_pack(pack, owner=owner, store=store))
    assert again["replayed"] and again["node_id"] == first["node_id"]
    bad = copy.deepcopy(pack)
    bad["fragment"]["nodes"].append({"id": "concept:injected", "type": "concept"})
    assert share.import_share_pack(bad, owner=owner, store=store)["error"] == "invalid_community_agent_pack"
    assert not query.node("concept:injected", owner=owner, store=store)["found"]


def test_published_import_generation_restores_role_and_receipt(engine, tmp_path):
    from store import persistence, durable_generation
    from store.backend import open_store
    from store.config import load_config
    store, app = engine
    app._durable = persistence.DurableAck(lambda: store, app.cfg.data_dir, str(tmp_path / "snapshot"))
    app.jobs._on_before_done = app._ack_before_done
    snapshot, source = authored()
    result = save(app, snapshot, source)
    assert result["durability"] == "published_generation" and result["durable_sequence"] > 0, result
    # 원래 ACK가 유실되고 사본을 편집한 뒤에도 Take의 영속 증거는 남는다.
    app.jobs.run_sync("ingest:fragment", lambda: fragment.merge_fragment({"owner": OWNER, "nodes": [
        {"id": result["node_id"], "type": "concept", "title": "Recipient edited title"}]}, store=store))
    destination = str(tmp_path / "restored" / "graph")
    assert durable_generation.restore(app._durable.root, destination).startswith("restored:")
    restored = open_store(load_config({"data_dir": destination}))
    try:
        node = query.node(result["node_id"], owner=OWNER, store=restored)["node"]
        assert node["props"]["shared_snapshot"] == snapshot and node["props"]["community_source"] == source
        status = ca.import_result(restored, owner=OWNER, mutation_id=result["operation_id"], durability_root=app._durable.root)
        assert status["state"] == "committed" and status["result"]["source_hash"] == source["hash"]
        assert status["durability"] == "published_generation" and status["durable_sequence"] > 0
        assert node["title"] == "Recipient edited title"
        from api import operation_receipts
        assert operation_receipts.status(restored, owner=OWNER, operation_id=result["operation_id"])["durability"] == "not_checked"
        assert ca.import_result(restored, owner=OTHER, mutation_id=result["operation_id"])["state"] == "unknown"
    finally:
        restored.close()


def test_lost_ack_reconciles_same_version_without_duplicate(engine):
    from api.jobs import JobError
    from store.persistence import DurableAckError
    store, app = engine
    snapshot, source = authored()
    body = {"owner": OWNER, "snapshot": snapshot, "source": source, "mutation_id": str(uuid4())}
    def lose_ack(job, result):
        raise DurableAckError("Synthetic lost publication acknowledgement")
    app.jobs._on_before_done = lose_ack
    with pytest.raises(JobError) as caught:
        app.import_community_agent(body)
    assert caught.value.write_may_have_applied
    status = ca.import_result(store, owner=OWNER, mutation_id=body["mutation_id"])
    assert status["state"] == "committed" and status["result"]["source_hash"] == source["hash"]
    app.jobs._on_before_done = None
    again = app.import_community_agent(body)[1]
    assert again["replayed"] and again["node_id"] == snapshot["role"]["id"]
    assert query.node(again["node_id"], owner=OWNER, store=store)["node"]["rev"] == status["result"]["rev"]


@pytest.mark.parametrize("failure", [1, 2])
def test_failed_generation_publication_is_unknown_and_retry_does_not_duplicate(engine, tmp_path, failure):
    from api.jobs import JobError
    from store import persistence
    store, app = engine
    actual = persistence.DurableAck(lambda: store, app.cfg.data_dir, str(tmp_path / "snap"))
    class Fault:
        sequence = 0
        calls = 0
        def __call__(self, job, result):
            self.calls += 1
            if self.calls == failure:
                raise persistence.DurableAckError("Synthetic publish fault")
            actual(job, result)
            self.sequence = actual.sequence
    app._durable = Fault()
    app.jobs._on_before_done = app._ack_before_done
    snapshot, source = authored()
    body = {"owner": OWNER, "snapshot": snapshot, "source": source, "mutation_id": str(uuid4())}
    with pytest.raises(JobError) as caught:
        app.import_community_agent(body)
    assert caught.value.write_may_have_applied
    uncertain = ca.import_result(store, owner=OWNER, mutation_id=body["mutation_id"], durability_root=actual.root)
    assert uncertain["state"] == "committed"
    assert uncertain["durability"] == "not_checked"
    app._durable = actual
    again = app.import_community_agent(body)[1]
    assert again["replayed"] and again["durability"] == "published_generation"
    recovered = ca.import_result(store, owner=OWNER, mutation_id=body["mutation_id"], durability_root=actual.root)
    assert recovered["durability"] == "published_generation"
    assert query.node(again["node_id"], owner=OWNER, store=store)["node"]["rev"] == uncertain["result"]["rev"]


def test_pack_inspection_uses_primary_reader_and_bounds_before_inflation():
    import io
    import zipfile
    ordinary = {"vinc_pack": 1, "kind": "share", "manifest": {}, "fragment": {"nodes": [], "edges": []}}
    raw = packfile.to_bytes(ordinary)
    assert ca.inspect_pack({"encoding": "base64", "content": base64.b64encode(raw).decode()})["pack"] == ordinary
    cases = [b"PK broken", b"{}"]
    for name, content in [("../../manifest.json", "{}"), ("documents/0001.json", "x" * (4 * 1024 * 1024 + 1))]:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", '{"vinc_pack":1}')
            z.writestr(name, content)
        cases.append(buf.getvalue())
    # 컨테이너 내용은 한도 안이어도 응답 봉투와 해시를 보태면 넘을 수 있다.
    from api.operation_receipts import canonical
    body = {"vinc_pack": 1, "kind": "share", "manifest": {"text": ""}}
    piece = canonical(ordinary["fragment"])
    body["manifest"]["text"] = "a" * (4 * 1024 * 1024 - 10 - len(canonical(body)) - len(piece))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", canonical(body))
        z.writestr("fragment.json", piece)
    cases.append(buf.getvalue())
    for bad in cases:
        with pytest.raises(ValueError):
            ca.inspect_pack({"encoding": "base64", "content": base64.b64encode(bad).decode()})
