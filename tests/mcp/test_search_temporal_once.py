"""vinc_search 요약 응답은 행과 노드 후보의 시간 칸을 `temporal{}` 에만 싣는다.

평평한 사본(created_at·updated_at·content_time·valid_from·valid_until·superseded_by·validity)은
detail=full 에서만 함께 나간다(폐기 예고). 어느 꼴이든 행과 노드 후보의 수와 순서는 같고,
brief 와 평가 하네스가 부르는 `q.search` 의 꼴은 그대로다. 합성 데이터만 쓴다.
"""
import copy

import pytest

from api import docstore, fragment, query
from api.cli_tools import _InProcessBridge
from api.server import VincApp
from store import search_temporal as temporal
from store.backend import open_store
from store.config import load_config
from vinc_mcp import tools

OWNER = "user:synthetic-temporal-once"
MODES = [{}, {"mode": "current"}, {"mode": "historical"}, {"as_of": "2026"},
         {"known_at": "2100"}, {"historical": True}]


def _seed(store):
    for suffix, extra in (("alpha", {"valid_from": "2026-01"}), ("beta", {})):
        docstore.put_doc(store, owner=OWNER, doc_id="synthetic." + suffix,
                         text=f"Marker synthetic passage about {suffix}. " * 20,
                         title="Marker " + suffix, **extra)
    res = fragment.merge_fragment({"owner": OWNER, "nodes": [
        {"id": "concept:marker-concept", "type": "concept", "title": "Marker concept",
         "domain": "vinc/synthetic", "content_time": "1905"},
        {"id": "record:marker-record", "type": "record", "title": "Marker record",
         "domain": "vinc/synthetic", "valid_from": "2026-01-01"},
        {"id": "decision:marker-decision", "type": "decision", "title": "Marker decision",
         "domain": "vinc/synthetic", "props": {"valid_from": "2026-02"}},
        {"id": "episode:marker-episode", "type": "episode", "title": "Marker episode",
         "domain": "vinc/synthetic", "date": "2026-10-01"},
    ], "edges": [{"from": "decision:marker-decision", "to": "concept:marker-concept",
                  "type": "about"}]}, store=store)
    assert res["executed"] and not res.get("warnings"), res


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    # 읽기만 하는 테스트들이라 스토어 하나를 모듈이 같이 쓴다.
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MEMORY_STORE_BACKEND", "embedded")
        mp.setenv("MEMORY_EMBEDDING_MODEL", "local-en")
        cfg = load_config({"data_dir": str(tmp_path_factory.mktemp("once") / "synthetic-db")})
        store = open_store(cfg)
        assert store is not None
        store.provision(dim=cfg.embedding_dim)
        _seed(store)
        app = VincApp(cfg, store=store)
        try:
            yield app
        finally:
            app.jobs.shutdown()
            store.close()


def _search(app, **args):
    bridge = _InProcessBridge(app)
    bridge.owner = OWNER
    return tools.call("vinc_search", {"query": "Marker", "limit": 20, **args}, bridge)


def _entries(res):
    return (res.get("rows") or []) + (res.get("node_hits") or [])


def _order(res):
    return ([r.get("id") for r in res.get("rows") or []],
            [h.get("id") for h in res.get("node_hits") or []])


@pytest.mark.parametrize("kw", MODES)
def test_summary_rows_and_node_hits_carry_the_temporal_fields_once(app, kw):
    res = _search(app, **kw)
    assert res["executed"], res
    assert res["rows"] and res["node_hits"], res
    for entry in _entries(res):
        assert isinstance(entry.get("temporal"), dict), entry
        assert set(temporal.FLAT) <= set(entry["temporal"]), entry
        assert not set(temporal.FLAT) & set(entry), entry


@pytest.mark.parametrize("kw", MODES)
def test_full_keeps_the_deprecated_copy_and_the_same_rows(app, kw):
    full = _search(app, detail="full", **kw)
    summary = _search(app, **kw)
    assert _order(full) == _order(summary)
    for entry in _entries(full):
        assert set(temporal.FLAT) <= set(entry), entry
        assert all(entry[k] == entry["temporal"][k] for k in temporal.FLAT), entry


@pytest.mark.parametrize("detail", ["summary", "full"])
def test_only_the_flat_copy_leaves_the_response(app, detail):
    """라우트가 하는 일은 사본을 걷는 것뿐이다. 순위·행·다른 키는 `q.search` 와 같다."""
    core = query.search("Marker", owner=OWNER, top_k=20, detail=detail,
                        store=app.store)
    sent = _search(app, **({"detail": "full"} if detail == "full" else {}))
    want = copy.deepcopy(core) if detail == "full" else temporal.drop_flat(copy.deepcopy(core))
    assert sent["rows"] == want["rows"]
    assert sent["node_hits"] == want["node_hits"]


def test_the_core_search_brief_reads_is_unchanged(app):
    core = query.search("Marker", owner=OWNER, top_k=20, store=app.store)
    for entry in _entries(core):
        assert set(temporal.FLAT) <= set(entry), entry


def test_drop_flat_keeps_the_only_copy():
    row = {"id": "r", "valid_from": "2026", "validity": "current"}
    hit = {"id": "h", "valid_from": "2026", "temporal": {"valid_from": "2026"}}
    out = temporal.drop_flat({"rows": [row], "node_hits": [hit, "not-a-row"]})
    assert out["rows"] == [{"id": "r", "valid_from": "2026", "validity": "current"}]
    assert out["node_hits"] == [{"id": "h", "temporal": {"valid_from": "2026"}}, "not-a-row"]
