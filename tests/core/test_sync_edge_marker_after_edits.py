"""엣지 삭제 표식은 이 묶음이 쓰기 전의 끝점 스탬프로 견준다.

지운 기기가 지운 뒤 두 끝을 고치면, 그 편집이 표식과 같은 묶음에 실려 와도 엣지를 다시 만든 것이 아니다.
허브와 기기 둘(실제 동기화 클라이언트와 sync.run)로, 지운 엣지가 어느 스토어에서도 돌아오지 않는지 본다.
"""
import contextlib, json
import pytest
from api import docstore as ds, docsync, revstamp, sync, fragment as frag, query as q
from store import pkns
from store.backend import open_store
from store.config import load_config
from vinc_mcp import syncclient

A = "user:marker-dev"
T0 = 1_800_000_000_000
SECOND = 1000; MINUTE = 60 * SECOND; HOUR = 60 * MINUTE; DAY = 24 * HOUR


class Clocks:
    def __init__(self):
        self.hub, self.offset, self.side = T0, 0, "device"
    def __call__(self):
        return self.hub + (0 if self.side == "hub" else self.offset)
    @contextlib.contextmanager
    def at(self, off):
        b, self.offset = self.offset, off
        try:
            yield
        finally:
            self.offset = b
    @contextlib.contextmanager
    def at_hub(self):
        b, self.side = self.side, "hub"
        try:
            yield
        finally:
            self.side = b


@pytest.fixture()
def w(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE_BACKEND", "embedded")
    monkeypatch.setenv("MEMORY_EMBEDDING_MODEL", "local-en")
    clocks = Clocks()
    monkeypatch.setattr(revstamp, "_wall_ms", clocks)
    opened, traffic = [], []

    def make(name):
        cfg = load_config({"data_dir": str(tmp_path / name)})
        st = open_store(cfg); st.provision(dim=cfg.embedding_dim); opened.append(st)
        return st
    hub, dev, dev2, dev3 = make("hub"), make("dev"), make("dev2"), make("dev3")

    def post(base, path, token, payload, *, timeout):
        body = json.loads(json.dumps(payload))
        with clocks.at_hub():
            if path == "/sync/push":
                out = sync.apply_upload(owner=A, bundle=body["bundle"], store=hub)
                b = body.get("bundle") or {}
                m = (len(b.get("tombstones") or []) + len(b.get("edge_tombstones") or []), 0)
            elif path == "/sync/pull":
                out = revstamp.with_clock(sync.export_delta(owner=A, since=int(body["since"]),
                                                            domains=["marker"], store=hub))
                m = (0, len(out.get("tombstones") or []) + len(out.get("edge_tombstones") or []))
            elif path == "/sync/docs-push":
                out = docsync.import_upload(hub, owner=A, payload=body["payload"])
                m = (len((body.get("payload") or {}).get("tombstones") or []), 0)
            elif path == "/sync/docs-pull":
                out = revstamp.with_clock(docsync.export_docs(hub, owner=A, since=int(body["since"])))
                m = (0, len(out.get("tombstones") or []))
            else:
                raise AssertionError(path)
        traffic.append(m)
        return json.loads(json.dumps(out, default=str))
    monkeypatch.setattr(syncclient, "_post", post)
    for st in (dev, dev2, dev3):
        sync.set_selected_domains(st, ["marker"])
    ns = type("W", (), {})()
    ns.hub, ns.dev, ns.dev2, ns.dev3, ns.clocks, ns.traffic = hub, dev, dev2, dev3, clocks, traffic
    ns.remote = sync.RemoteHTTP("vinc_sk_test", "http://hub.test")
    ns.skews = []
    try:
        yield ns
    finally:
        for st in opened:
            st.close()


def run(w, st, off=0):
    w.clocks.hub += SECOND
    start = len(w.traffic)
    with w.clocks.at(off):
        out = sync.run(st, None, owner=A, remote=w.remote)
    assert out["executed"], out
    sk = (out.get("push") or {}).get("clock_skew") or []
    if sk:
        w.skews.append(sk)
    return (sum(t[0] for t in w.traffic[start:]), sum(t[1] for t in w.traffic[start:]))


def write(st, title, nid="concept:n"):
    assert frag.merge_fragment({"owner": A, "nodes": [{"id": nid, "type": "concept", "title": title,
                                                       "domain": "marker"}], "edges": []}, store=st)["executed"]


def row(st, nid="concept:n"):
    r = st.run("MATCH (c:Concept) WHERE c.concept_id = $id AND c.owner_id = $o RETURN c.title AS t, c.updated_at AS ua",
               {"id": pkns.key(A, nid), "o": A})
    return r[0] if r else None


def title(st, nid="concept:n"):
    return (row(st, nid) or {}).get("t")


def edges(st, s="concept:s", d="concept:d"):
    r = st.run("MATCH (a:Concept)-[r:RELATED_TO]-(b:Concept) WHERE a.concept_id = $s AND b.concept_id = $d "
               "RETURN count(r) AS c", {"s": pkns.key(A, s), "d": pkns.key(A, d)})
    return int(r[0]["c"]) if r else 0


def tomb(st, nid="concept:n"):
    t = sync.get_tombstone(st, nid, A)
    return (t or {}).get("deleted_at")


def etomb(st):
    return [r["da"] for r in st.run("MATCH (t:_EdgeTombstone) RETURN t.deleted_at AS da")]


def seed(w, extra_nodes=()):
    with w.clocks.at_hub():
        assert frag.merge_fragment({"owner": A, "nodes": [
            {"id": nid, "type": "concept", "title": nid, "domain": "marker"}
            for nid in ("concept:n", "concept:s", "concept:d", "concept:keep") + tuple(extra_nodes)],
            "edges": [{"from": "concept:s", "to": "concept:d", "type": "relates_to"}]}, store=w.hub)["executed"]


def rep(tag, **kw):
    print(f"\nPROBE {tag}: " + " ".join(f"{k}={v}" for k, v in kw.items()))




def test_a_deleter_that_edits_both_ends_before_syncing_keeps_the_edge_deleted(w):
    seed(w)
    run(w, w.dev); run(w, w.dev2)
    w.clocks.hub += SECOND
    assert q.delete_edge("concept:s", "relates_to", "concept:d", owner=A, store=w.dev).get("executed")
    w.clocks.hub += SECOND
    write(w.dev, "s edited after the edge deletion", "concept:s")
    write(w.dev, "d edited after the edge deletion", "concept:d")
    seq = []
    for st in (w.dev, w.dev2, w.dev, w.dev2, w.dev):
        run(w, st)
        seq.append([edges(x) for x in (w.hub, w.dev, w.dev2)])
    rep("E1", edges_hub_dev_dev2_each_sync=seq, edge_markers=[etomb(x) for x in (w.hub, w.dev, w.dev2)])
    assert [edges(x) for x in (w.hub, w.dev, w.dev2)] == [0, 0, 0]


def test_a_deleter_that_edits_both_ends_after_syncing_keeps_the_edge_deleted(w):
    """Control: the same edits after the deletion reached the hub."""
    seed(w)
    run(w, w.dev); run(w, w.dev2)
    w.clocks.hub += SECOND
    assert q.delete_edge("concept:s", "relates_to", "concept:d", owner=A, store=w.dev).get("executed")
    run(w, w.dev)
    write(w.dev, "s edited later", "concept:s")
    write(w.dev, "d edited later", "concept:d")
    for st in (w.dev, w.dev2, w.dev, w.dev2):
        run(w, st)
    rep("E3", edges_hub_dev_dev2=[edges(x) for x in (w.hub, w.dev, w.dev2)])
    assert [edges(x) for x in (w.hub, w.dev, w.dev2)] == [0, 0, 0]
