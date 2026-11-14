# -*- coding: utf-8 -*-
"""더 쪼갤 수 없어 허브의 본문 상한을 넘는 청크는 413 으로 보내지 않고 이름으로 알린다.

엣지의 끝점 둘이 각각 상한 가까이 크면 그 엣지의 넘침 청크는 둘을 함께 싣고, 끝점 노드는 서로 다른 청크에 있어 합칠 수도
없다. 그 청크는 어떻게 보내도 허브가 413 으로 거절하고, 커서가 그대로라 모든 동기화가 같은 청크에서 멈춘다.
`test_sync_chunk_every_part.py` 와 같은 스택(사이드카 ← 브리지 ← 공개 허브, 기기 스토어 하나)이다.

- 나머지 청크는 올라가고, 동기화는 끝까지 가고, 커서는 움직인다. 결과 `held` 와 `warnings` 가 엣지와 크기를 이름으로 댄다.
- 그 청크가 실은 노드는 새 순번을 받아 다음 동기화에 다시 실린다. 노드가 작아지면 엣지가 올라간다.
- 올라간 노드의 base 는 움직이고, 어느 청크에도 못 실린 노드의 base 는 그대로다.
- 모드 전환의 전량 올리기는 보내지 못한 청크를 `missing` 으로 센다.
"""
from __future__ import annotations

import pytest

from api import sync
from api import fragment as frag
from store.embedded_backend import import_driver
from tests.mcp.test_sync_chunk_every_part import (  # noqa: F401  (모듈 픽스처와 헬퍼)
    OWNER, export, has_base, hub_edge_pairs, hub_nodes, push, put_nodes, shrink, sync_cursor, wire)
from tests.mcp.test_sync_over_limit import DOMAIN, KEY, STAMP, World, world  # noqa: F401
from vinc_mcp import http_server

pytestmark = pytest.mark.skipif(import_driver()[0] is None, reason="임베디드 드라이버(ladybug) 미설치")

BIG, SMALL = "concept:big-a", "concept:big-b"
EDGE = f"{BIG} -relates_to-> {SMALL}"


def join(world) -> None:
    res = frag.merge_fragment({"owner": OWNER, "nodes": [], "edges": [
        {"from": BIG, "to": SMALL, "type": "relates_to"}]}, store=world.dev)
    assert res["executed"], res


def two_big_ends(world, monkeypatch) -> None:
    """상한 6,000 바이트에서 각각 5,000 바이트쯤인 노드 둘과 그 사이 엣지."""
    shrink(monkeypatch, 6000)
    put_nodes(world, [BIG, SMALL], pad=2300)
    join(world)
    chunks = sync.chunk_bundle(export(world, 0))
    assert len(chunks) == 3 and max(wire(c) for c in chunks[:2]) <= http_server.MAX_BODY < wire(chunks[2])


def test_an_edge_chunk_the_hub_cannot_take_is_named_and_the_sync_goes_on(world, monkeypatch):
    two_big_ends(world, monkeypatch)
    put_nodes(world, ["concept:small"])
    world.reached.clear()
    out = push(world)                                    # 413 으로 멈추지 않는다
    assert len(out["held"]) == 1 and out["held"][0]["edges"] == [EDGE], out
    assert out["held"][0]["bytes"] > http_server.MAX_BODY == out["held"][0]["limit"]
    assert any(EDGE in w and "stays queued" in w for w in out["warnings"])
    assert hub_nodes(world) == {BIG, SMALL, "concept:small"} and hub_edge_pairs(world) == set()
    assert out["remote"]["chunks"] == 3          # 노드 청크 셋. 엣지 청크는 보내지 않았다
    assert sync_cursor(world) == out["cursor"] > 0
    assert has_base(world, BIG) and has_base(world, SMALL) and has_base(world, "concept:small")


def test_the_held_ends_come_again_and_the_edge_lands_once_they_fit(world, monkeypatch):
    two_big_ends(world, monkeypatch)
    first = push(world)
    cursor = sync_cursor(world)
    again = export(world)
    assert {BIG, SMALL} <= {n["id"] for n in again["nodes"]}, "보내지 않은 청크의 노드가 다음 델타에 없다"
    assert again["next_cursor"] > cursor and len(first["held"]) == 1

    second = push(world)                                 # 아직 크다: 같은 이름으로 다시 알리고 멈추지 않는다
    assert len(second["held"]) == 1 and hub_edge_pairs(world) == set()

    put_nodes(world, [BIG, SMALL], pad=100)              # 작아지면 엣지가 올라간다
    third = push(world)
    assert "held" not in third, third
    assert hub_edge_pairs(world) == {frozenset((BIG, SMALL))}


def test_a_node_that_reached_no_chunk_keeps_its_base(world, monkeypatch):
    """한 노드의 청크가 상한을 넘으면(청크 하나가 노드 하나뿐) 그 노드는 허브에 없고 base 도 옮기지 않는다."""
    shrink(monkeypatch, 3000)
    put_nodes(world, ["concept:huge"], pad=4000)
    put_nodes(world, ["concept:fine"])
    out = push(world)
    assert [h["nodes"] for h in out["held"]] == [["concept:huge"]]
    assert hub_nodes(world) == {"concept:fine"}
    assert has_base(world, "concept:fine") and not has_base(world, "concept:huge")
    assert "concept:huge" in {n["id"] for n in export(world)["nodes"]}, "다음 델타에 다시 실려야 한다"


def test_push_everything_counts_a_held_chunk_as_missing(world, monkeypatch):
    two_big_ends(world, monkeypatch)
    rep = sync.push_everything(world.dev, world.dev_cfg, owners=[OWNER], remote=world.remote)
    assert len(rep["held"]) == 1 and rep["held"][0]["edges"] == [EDGE]
    assert rep["missing"] >= 1 and any(EDGE in w for w in rep["warnings"])
    assert hub_nodes(world) == {BIG, SMALL}
