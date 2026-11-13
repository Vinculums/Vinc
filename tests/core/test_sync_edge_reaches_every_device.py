"""한 기기가 더한 엣지가 허브를 거쳐 다른 기기에 닿는다. 양 끝 노드의 필드가 그대로여도 그렇다.

허브 하나와 기기 둘. 기기는 실제 동기화 클라이언트(`vinc_mcp/syncclient`)와 `sync.run` 으로 보내고, 허브의 네 끝점은
세 번째 스토어 위에서 사이드카 라우트가 부르는 함수(`apply_upload`·`export_delta`+`with_clock`·`import_upload`·
`export_docs`+`with_clock`)로 답한다. 허브는 키로 소유자를 고른다(개인 그래프와 팀 공간 하나).

- 받은 묶음이 엣지를 새로 만들거나 그 근거·신뢰도를 바꾸면, 받은 스토어는 그 양 끝에 새 순번을 매긴다. 그래서 허브의
  다음 델타가 양 끝과 엣지를 함께 싣는다(델타의 엣지는 양 끝이 같은 델타에 있을 때만 실린다). 허브에서 그 엣지를 받은
  기기도 같은 규칙으로 한 번 되올리고, 허브에는 이미 있어 거기서 멈춘다.
- 그 양 끝은 묶음이 실어 온 스탬프가 더 늦으면 그 스탬프를 받는다. 엣지를 만든 기기가 엣지를 만들며 찍은 스탬프다.
  그래서 그 엣지의 삭제를 이미 아는 기기도 그 삭제 뒤에 다시 만든 엣지를 받는다. 받지 않은 노드(시계가 상한 밖)의
  스탬프는 받지 않는다.
- 같은 엣지를 다시 보내는 것만으로는 순번이 바뀌지 않는다. 그래서 엣지는 한 번 건너간 뒤 동기화마다 오가지 않는다.
"""
from __future__ import annotations

import contextlib
import json

import pytest

from api import docsync, revstamp, sync
from api import fragment as frag
from api import query as q
from store import pkns
from store.backend import open_store
from store.config import load_config
from store.embedded_backend import import_driver
from vinc_mcp import syncclient

pytestmark = pytest.mark.skipif(import_driver()[0] is None, reason="임베디드 드라이버(ladybug) 미설치")

A = "user:edge-dev"
TEAM = "team:01a0edge-0000-7000-8000-00000000e0e0"
KEYS = {"vinc_sk_personal": A, "vinc_sk_team": TEAM}
DOMAIN = "edge"
T0 = 1_800_000_000_000
SECOND = 1000
MINUTE = 60 * SECOND
HOUR = 60 * MINUTE
SKEW = "clock_skew"

#: 엣지 종류별 (끝점 노드 둘, 그 사이 엣지). 세 종류가 fragment 의 세 갈래(개념 사이·ABOUT·기록 부착)를 지난다.
KINDS = {
    "relates_to": ("concept:s", "concept:d", "relates_to"),
    "about": ("decision:x", "concept:d", "about"),
    "record": ("record:r", "concept:d", "relates_to"),
}
_TYPES = {"concept": "concept", "decision": "decision", "record": "record"}


class _Clocks:
    """허브의 벽시계와 기기의 어긋남. 허브 끝점 안에서는 허브 시각, 밖에서는 기기 시각이다."""

    def __init__(self):
        self.hub = T0
        self.offset = 0
        self.side = "device"

    def __call__(self) -> int:
        return self.hub + (0 if self.side == "hub" else self.offset)

    @contextlib.contextmanager
    def at_hub(self):
        before, self.side = self.side, "hub"
        try:
            yield
        finally:
            self.side = before

    @contextlib.contextmanager
    def at(self, offset):
        before, self.offset = self.offset, offset
        try:
            yield
        finally:
            self.offset = before


def _load(path: str, body: dict, out: dict) -> dict:
    """요청 하나가 실어 나른 노드·엣지·삭제 표식 수(올림은 up, 내림은 down)."""
    if path == "/sync/push":
        b = body.get("bundle") or {}
        return {"nodes_up": len(b.get("nodes") or []), "edges_up": len(b.get("edges") or []),
                "markers_up": len(b.get("tombstones") or []) + len(b.get("edge_tombstones") or [])}
    if path == "/sync/pull":
        return {"nodes_down": len(out.get("nodes") or []), "edges_down": len(out.get("edges") or []),
                "markers_down": len(out.get("tombstones") or []) + len(out.get("edge_tombstones") or [])}
    return {}


@pytest.fixture()
def net(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE_BACKEND", "embedded")
    monkeypatch.setenv("MEMORY_EMBEDDING_MODEL", "local-en")
    clocks = _Clocks()
    monkeypatch.setattr(revstamp, "_wall_ms", clocks)
    opened = []
    traffic: list[dict] = []
    replies: list[dict] = []

    def make(name):
        cfg = load_config({"data_dir": str(tmp_path / name)})
        st = open_store(cfg)
        st.provision(dim=cfg.embedding_dim)
        opened.append(st)
        return st

    hub, dev, dev2 = make("hub"), make("device"), make("device2")

    def post(base, path, token, payload, *, timeout):
        owner = KEYS[token]
        body = json.loads(json.dumps(payload))
        with clocks.at_hub():
            if path == "/sync/push":
                out = sync.apply_upload(owner=owner, bundle=body["bundle"], store=hub)
                replies.append(out)
            elif path == "/sync/pull":
                out = revstamp.with_clock(sync.export_delta(
                    owner=owner, since=int(body["since"]), domains=[DOMAIN], store=hub))
            elif path == "/sync/docs-push":
                out = docsync.import_upload(hub, owner=owner, payload=body["payload"])
            elif path == "/sync/docs-pull":
                out = revstamp.with_clock(docsync.export_docs(hub, owner=owner, since=int(body["since"])))
            else:
                raise AssertionError(path)
        out = json.loads(json.dumps(out, default=str))
        traffic.append(_load(path, body, out))
        return out

    monkeypatch.setattr(syncclient, "_post", post)
    for st in (dev, dev2):
        sync.set_selected_domains(st, [DOMAIN])
    ns = type("Net", (), {})()
    ns.hub, ns.dev, ns.dev2, ns.clocks, ns.traffic, ns.replies = hub, dev, dev2, clocks, traffic, replies
    ns.make = make
    try:
        yield ns
    finally:
        for st in opened:
            st.close()


def _run(net, st, *, owner=A, offset=0, skew_ok=False) -> dict:
    """기기 한 대의 동기화 한 번. 허브 시계를 1초 민다. 그 동기화가 나른 수를 돌려준다."""
    net.clocks.hub += SECOND
    token = next(k for k, v in KEYS.items() if v == owner)
    start = len(net.traffic)
    with net.clocks.at(offset):
        out = sync.run(st, None, owner=owner, remote=sync.RemoteHTTP(token, "http://hub.test"))
    assert out["executed"], out
    if not skew_ok:
        assert SKEW not in out["push"], out
    total: dict = {}
    for row in net.traffic[start:]:
        for k, v in row.items():
            total[k] = total.get(k, 0) + v
    return total


def _carried(load: dict) -> tuple[int, int, int, int]:
    """(올린 노드, 올린 엣지, 내려온 노드, 내려온 엣지)."""
    return (load.get("nodes_up", 0), load.get("edges_up", 0),
            load.get("nodes_down", 0), load.get("edges_down", 0))


def _node(nid, title=None):
    return {"id": nid, "type": _TYPES[nid.split(":", 1)[0]], "title": title or nid, "domain": DOMAIN}


def _write(st, nodes=(), edges=(), owner=A):
    res = frag.merge_fragment({"owner": owner, "nodes": list(nodes), "edges": list(edges)}, store=st)
    assert res["executed"], res
    return res


def _edge(kind, **extra):
    src, dst, etype = KINDS[kind]
    return {"from": src, "to": dst, "type": etype, **extra}


def _count(st, kind="relates_to", owner=A, ends=None) -> int:
    """그 종류의 엣지 수(개념 사이는 방향 없이 센다). `ends` 는 개념 사이 엣지의 다른 두 끝이다."""
    src, dst, _ = KINDS[kind]
    if ends:
        src, dst = ends
    p = {"s": pkns.key(owner, src), "d": pkns.key(owner, dst), "o": owner}
    if kind == "relates_to":
        cy = ("MATCH (a:Concept)-[r:RELATED_TO]-(b:Concept) WHERE a.concept_id = $s AND b.concept_id = $d "
              "AND a.owner_id = $o RETURN count(r) AS c")
    elif kind == "about":
        cy = ("MATCH (a:Decision)-[r:ABOUT]->(b:Concept) WHERE a.decision_id = $s AND b.concept_id = $d "
              "AND a.owner_id = $o RETURN count(r) AS c")
    else:
        cy = ("MATCH (a:Record)-[r:ATTACHES_TO]->(b:Concept) WHERE a.record_id = $s AND b.concept_id = $d "
              "AND r.type = 'relates_to' AND a.owner_id = $o RETURN count(r) AS c")
    rows = st.run(cy, p)
    return int(rows[0]["c"]) if rows else 0


def _counts(net, kind="relates_to", owner=A) -> list[int]:
    return [_count(st, kind, owner) for st in (net.hub, net.dev, net.dev2)]


def _evidence(st, owner=A):
    rows = st.run("MATCH (a:Concept)-[r:RELATED_TO]-(b:Concept) WHERE a.concept_id = $s AND b.concept_id = $d "
                  "RETURN r.evidence AS ev", {"s": pkns.key(owner, "concept:s"), "d": pkns.key(owner, "concept:d")})
    return rows[0]["ev"] if rows else None


def _row(st, nid, owner=A):
    label, pk = frag._NODE_MAP[_TYPES[nid.split(":", 1)[0]]]
    rows = st.run(f"MATCH (x:{label}) WHERE x.{pk} = $id AND x.owner_id = $o "
                  "RETURN x.title AS title, x.updated_at AS ua, x.rev AS rev",
                  {"id": pkns.key(owner, nid), "o": owner})
    return rows[0] if rows else None


def _seed(net, owner=A, edges=()):
    """허브에 끝점 노드를 만들고 두 기기가 한 번씩 받는다. 엣지는 `edges` 에 든 것만 만든다."""
    with net.clocks.at_hub():
        _write(net.hub, [_node(n) for n in ("concept:s", "concept:d", "decision:x", "record:r")],
               edges, owner=owner)
    for st in (net.dev, net.dev2):
        _run(net, st, owner=owner)


# ── 끝점이 그대로인 엣지 추가 ──────────────────────────────────────────────────
@pytest.mark.parametrize("kind", list(KINDS))
def test_an_edge_added_between_unchanged_nodes_reaches_the_other_device(net, kind):
    _seed(net)
    _write(net.dev, edges=[_edge(kind)])
    sent = _carried(_run(net, net.dev))
    got = _carried(_run(net, net.dev2))
    assert _counts(net, kind) == [1, 1, 1]
    # 더한 기기는 양 끝과 엣지를 올리고, 받는 기기는 그것을 받아 한 번 되올린다.
    assert (sent, got) == ((2, 1, 0, 0), (2, 1, 2, 1))

    # 그 뒤로는 허브가 더한 기기에 한 번 되돌려 주는 것 말고는 오가는 것이 없다.
    loads = [_carried(_run(net, st)) for st in (net.dev, net.dev2, net.dev, net.dev2)]
    assert loads == [(0, 0, 2, 1), (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0)]
    assert _counts(net, kind) == [1, 1, 1]


def test_the_hub_takes_the_stamps_the_adding_device_gave_both_ends(net):
    _seed(net)
    net.clocks.hub += 10 * SECOND
    _write(net.dev, edges=[_edge("relates_to")])
    stamped = {n: _row(net.dev, n) for n in ("concept:s", "concept:d")}
    _run(net, net.dev)
    for n, got in stamped.items():
        held = _row(net.hub, n)
        assert (held["ua"], held["rev"]) == (got["ua"], got["rev"]), n


# ── 다른 기기가 한쪽 끝을 고치는 동안 ──────────────────────────────────────────
@pytest.mark.parametrize("edit_first", [False, True], ids=["edge-first", "edit-first"])
def test_an_edge_added_while_the_other_device_edits_one_end(net, edit_first):
    _seed(net)
    _write(net.dev, edges=[_edge("relates_to")])
    _write(net.dev2, nodes=[_node("concept:s", "s edited on dev2")])
    order = (net.dev2, net.dev, net.dev2, net.dev) if edit_first else (net.dev, net.dev2, net.dev, net.dev2)
    for st in order:
        _run(net, st)
    assert _counts(net) == [1, 1, 1]
    assert [(_row(st, "concept:s") or {}).get("title") for st in (net.hub, net.dev, net.dev2)] \
        == ["s edited on dev2"] * 3


# ── 더한 뒤 다른 기기가 받기 전에 지움 ─────────────────────────────────────────
def test_an_edge_added_and_deleted_before_the_other_device_syncs_is_nowhere(net):
    _seed(net)
    _write(net.dev, edges=[_edge("relates_to")])
    net.clocks.hub += SECOND
    assert q.delete_edge("concept:s", "relates_to", "concept:d", owner=A, store=net.dev).get("executed")
    loads = [_run(net, st) for st in (net.dev, net.dev2, net.dev, net.dev2, net.dev, net.dev2)]
    assert _counts(net) == [0, 0, 0]
    assert sum(ld.get("edges_up", 0) + ld.get("edges_down", 0) for ld in loads) == 0
    # 삭제 표식은 한 번 건너가고 멈춘다.
    assert [ld.get("markers_up", 0) + ld.get("markers_down", 0) for ld in loads[-2:]] == [0, 0]


def test_a_deletion_and_new_edges_from_one_device_both_reach_the_other(net):
    """한 기기가 s-d 를 지우고 s-x-d 로 바꾼 뒤 한 번에 동기화한다. 지운 엣지는 어디에도 없고 새 엣지는 어디에나 있다."""
    _seed(net, edges=[_edge("relates_to")])
    net.clocks.hub += SECOND
    assert q.delete_edge("concept:s", "relates_to", "concept:d", owner=A, store=net.dev).get("executed")
    net.clocks.hub += SECOND
    _write(net.dev, [_node("concept:x", "x between s and d")], [
        {"from": "concept:s", "to": "concept:x", "type": "relates_to"},
        {"from": "concept:x", "to": "concept:d", "type": "relates_to"}])
    for st in (net.dev, net.dev2, net.dev, net.dev2):
        _run(net, st)
    stores = (net.hub, net.dev, net.dev2)
    assert [_count(st) for st in stores] == [0, 0, 0]
    assert [_count(st, ends=("concept:s", "concept:x")) for st in stores] == [1, 1, 1]
    assert [_count(st, ends=("concept:x", "concept:d")) for st in stores] == [1, 1, 1]


def test_an_edge_added_on_a_device_and_later_deleted_there_is_deleted_everywhere(net):
    _seed(net)
    _write(net.dev, edges=[_edge("relates_to")])
    for st in (net.dev, net.dev2):
        _run(net, st)
    assert _counts(net) == [1, 1, 1]
    net.clocks.hub += SECOND
    assert q.delete_edge("concept:s", "relates_to", "concept:d", owner=A, store=net.dev).get("executed")
    for st in (net.dev, net.dev2, net.dev, net.dev2):
        _run(net, st)
    assert _counts(net) == [0, 0, 0]


# ── 같은 엣지를 두 기기가 더함 ─────────────────────────────────────────────────
@pytest.mark.parametrize("reverse", [False, True], ids=["same-direction", "other-direction"])
def test_the_same_edge_added_on_both_devices_is_one_edge_everywhere(net, reverse):
    _seed(net)
    _write(net.dev, edges=[_edge("relates_to")])
    other = {"from": "concept:d", "to": "concept:s", "type": "relates_to"} if reverse else _edge("relates_to")
    _write(net.dev2, edges=[other])
    loads = [_carried(_run(net, st)) for st in (net.dev, net.dev2, net.dev, net.dev2, net.dev, net.dev2)]
    assert _counts(net) == [1, 1, 1]
    assert loads[-2:] == [(0, 0, 0, 0), (0, 0, 0, 0)]


# ── 그 엣지의 삭제를 이미 아는 기기 ────────────────────────────────────────────
def test_an_edge_made_again_after_its_deletion_reaches_a_device_that_holds_the_deletion(net):
    _seed(net, edges=[_edge("relates_to")])
    assert _counts(net) == [1, 1, 1]
    net.clocks.hub += SECOND
    assert q.delete_edge("concept:s", "relates_to", "concept:d", owner=A, store=net.dev).get("executed")
    for st in (net.dev, net.dev2, net.dev):
        _run(net, st)
    assert _counts(net) == [0, 0, 0]
    assert net.dev2.run("MATCH (t:_EdgeTombstone) RETURN count(t) AS c")[0]["c"] == 1

    net.clocks.hub += SECOND
    _write(net.dev, edges=[_edge("relates_to")])
    for st in (net.dev, net.dev2, net.dev, net.dev2):
        _run(net, st)
    assert _counts(net) == [1, 1, 1]
    # 다시 만든 엣지가 이긴 표식은 어느 스토어에도 남지 않는다.
    assert [st.run("MATCH (t:_EdgeTombstone) RETURN count(t) AS c")[0]["c"]
            for st in (net.hub, net.dev, net.dev2)] == [0, 0, 0]


# ── 팀 공간 ───────────────────────────────────────────────────────────────────
def test_an_edge_in_a_team_space_reaches_the_other_device_and_stays_in_the_team(net):
    """두 기기는 팀 공간을 동기화하고, 셋째 기기는 같은 허브에서 개인 그래프를 동기화한다(기기 스토어는 소유자마다 따로)."""
    mine = net.make("device-personal")
    sync.set_selected_domains(mine, [DOMAIN])
    with net.clocks.at_hub():
        _write(net.hub, [_node(n) for n in ("concept:p1", "concept:p2")], owner=A)
    _run(net, mine, owner=A)
    _seed(net, owner=TEAM)
    _write(net.dev, edges=[_edge("relates_to")], owner=TEAM)
    _run(net, net.dev, owner=TEAM)
    _run(net, net.dev2, owner=TEAM)
    _run(net, mine, owner=A)
    assert _counts(net, owner=TEAM) == [1, 1, 1]
    assert _row(mine, "concept:p1") is not None
    for st in (net.hub, mine):
        rows = st.run("MATCH (a:Concept)-[r:RELATED_TO]-(b:Concept) WHERE a.owner_id = $o RETURN count(r) AS c",
                      {"o": A})
        assert rows[0]["c"] == 0
    assert mine.run("MATCH (c:Concept) WHERE c.owner_id = $o RETURN count(c) AS c", {"o": TEAM})[0]["c"] == 0


# ── 시계가 어긋난 기기 ────────────────────────────────────────────────────────
@pytest.mark.parametrize("offset", [-2 * HOUR, 4 * MINUTE], ids=["2h-slow", "4min-fast"])
def test_an_edge_from_a_skewed_device_reaches_the_other_and_a_later_deletion_follows(net, offset):
    _seed(net)
    _run(net, net.dev, offset=offset)
    with net.clocks.at(offset):
        _write(net.dev, edges=[_edge("relates_to")])
    _run(net, net.dev, offset=offset)
    _run(net, net.dev2)
    assert _counts(net) == [1, 1, 1]
    # 시계가 맞는 기기가 그 뒤에 지운 것은 어느 스토어에도 남지 않는다.
    net.clocks.hub += 10 * SECOND
    assert q.delete_edge("concept:s", "relates_to", "concept:d", owner=A, store=net.dev2).get("executed")
    for st, off in ((net.dev2, 0), (net.dev, offset), (net.dev2, 0), (net.dev, offset)):
        _run(net, st, offset=off)
    assert _counts(net) == [0, 0, 0]


def test_the_hub_does_not_take_end_stamps_beyond_its_bound(net):
    _seed(net)
    held = {n: _row(net.hub, n)["ua"] for n in ("concept:s", "concept:d")}
    with net.clocks.at(10 * MINUTE):
        _write(net.dev, edges=[_edge("relates_to")])
    _run(net, net.dev, offset=10 * MINUTE, skew_ok=True)
    assert all(SKEW in r for r in net.replies[-1:]), net.replies[-1]
    for n, ua in held.items():
        assert _row(net.hub, n)["ua"] == ua, n
    _run(net, net.dev2)
    assert _count(net.dev2) == 1


# ── 근거만 바뀐 엣지 ──────────────────────────────────────────────────────────
def test_new_evidence_on_an_existing_edge_reaches_the_other_device(net):
    _seed(net, edges=[_edge("relates_to", evidence="first reading")])
    assert [_evidence(st) for st in (net.hub, net.dev, net.dev2)] == ["first reading"] * 3
    _write(net.dev, edges=[_edge("relates_to", evidence="second reading")])
    for st in (net.dev, net.dev2):
        _run(net, st)
    assert [_evidence(st) for st in (net.hub, net.dev, net.dev2)] == ["second reading"] * 3
