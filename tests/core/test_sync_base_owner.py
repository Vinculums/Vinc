# -*- coding: utf-8 -*-
"""`push_local` 는 합의된 값(base)을 병합이 읽는 키로 쓴다.

`apply_delta` 는 base 를 소유자 키(`pkns.key(owner, id)`)로 읽는데 `push_local` 은 소유자 없이 써서, PK 네임스페이싱이 켜진
스토어(`VINC_PK_NAMESPACED=1`)에서는 push 가 쓴 base 를 병합이 못 찾고 빈 조상으로 떨어졌다. 빈 조상에서는 모든 필드가 양쪽 변경이라,
push 뒤 pull 앞에 고친 로컬 필드가 더 늦게 고쳐진 원격 노드에 진다. 꺼진 스토어(로컬 기기)에서는 두 키가 같은 문자열이라 어긋나지 않는다.

- push 가 쓴 base 는 병합이 읽는 키에 있다. 꺼진 스토어에서는 그 키가 id 그대로이고, 켜진 스토어에서는 소유자를 안은 키 하나다.
- push 뒤 pull 앞에 고친 로컬 필드는 원격이 다른 필드를 고쳐 와도 남는다(3-way).
- 소유자 없이 쓰인 옛 행은 병합의 조상으로 읽지 않는다. 읽으면 슬러그가 같은 다른 소유자의 조상이 내 병합에 든다.
"""
from __future__ import annotations

import json

import pytest

from api import sync
from store import pkns
from store.embedded_backend import import_driver
from tests.core.test_sync_edge_reaches_every_device import (  # noqa: F401  (모듈 픽스처와 헬퍼)
    A, DOMAIN, SECOND, _run, _write, net)

pytestmark = pytest.mark.skipif(import_driver()[0] is None, reason="임베디드 드라이버(ladybug) 미설치")

NID = "concept:m"


def remote():
    return sync.RemoteHTTP("vinc_sk_personal", "http://hub.test")


def node(title: str, **extra) -> dict:
    return {"id": NID, "type": "concept", "title": title, "domain": DOMAIN, **extra}


def props_of(st) -> dict:
    rows = st.run("MATCH (c:Concept) WHERE c.concept_id = $id AND c.owner_id = $o RETURN c.title AS t, c.props AS p",
                  {"id": pkns.key(A, NID), "o": A})
    raw = rows[0]["p"]
    return {"title": rows[0]["t"], "props": json.loads(raw) if isinstance(raw, str) and raw else (raw or {})}


def rows_at(st, key: str) -> int:
    """이 물리 키의 base 행 수. 스토어는 읽어 돌려주는 값에서 소유자 접두를 떼므로 키를 바인딩해서 센다."""
    return int(st.run("MATCH (b:_SyncBase) WHERE b.node_id = $k RETURN count(b) AS c", {"k": key})[0]["c"])


def test_push_writes_the_base_under_the_key_the_merge_reads(net):
    _write(net.dev, [node("t0")])
    net.clocks.hub += SECOND
    out = sync.push_local(net.dev, None, owner=A, remote=remote())
    assert out["sent"] == 1
    assert rows_at(net.dev, pkns.key(A, NID)) == 1
    base = sync.get_base(net.dev, NID, A)
    assert base is not None and base["fields"]["title"] == "t0"
    if pkns.enabled():
        assert pkns.key(A, NID) != NID, "켜진 스토어의 키는 소유자를 안아야 한다"
        assert rows_at(net.dev, NID) == 0, "소유자 없는 행이 같이 생겼다"
    else:
        assert pkns.key(A, NID) == NID, "꺼진 스토어에서는 두 키가 같은 문자열이다"


def test_a_pull_after_a_push_merges_three_way_with_the_pushed_base(net):
    """기기가 제목을 고쳐 올리고, 그 뒤 pull 전에 props 를 고친다. 그 사이 다른 기기가 허브의 제목을 더 늦게 고친다.
    올린 값이 조상이면 제목은 원격이, props 는 로컬이 이긴다. 빈 조상이면 props 도 더 늦은 원격 노드에 져서 로컬 편집이 사라진다."""
    _write(net.hub, [node("t0", props={"a": 1})])
    _run(net, net.dev)
    _run(net, net.dev2)
    assert props_of(net.dev) == {"title": "t0", "props": {"a": 1}}

    net.clocks.hub += 2 * SECOND
    _write(net.dev, [node("dev-title")])
    net.clocks.hub += SECOND
    sync.push_local(net.dev, None, owner=A, remote=remote())
    assert sync.get_base(net.dev, NID, A)["fields"]["title"] == "dev-title"

    net.clocks.hub += SECOND
    _write(net.dev, [node("dev-title", props={"a": 2})])           # push 뒤 pull 앞의 로컬 편집
    net.clocks.hub += 5 * SECOND
    _run(net, net.dev2)                                            # 허브의 제목을 이 기기보다 늦게 고친다
    _write(net.dev2, [node("hub-title")])
    _run(net, net.dev2)

    net.clocks.hub += SECOND
    sync.pull_apply(net.dev, None, owner=A, remote=remote())
    assert props_of(net.dev) == {"title": "hub-title", "props": {"a": 2}}


@pytest.mark.skipif(not pkns.enabled(), reason="키가 갈리는 것은 PK 네임스페이싱이 켜진 스토어뿐이다")
def test_a_base_row_without_the_owner_is_not_read_as_an_ancestor(net):
    """수정 전에 쓰인 소유자 없는 행. 폴백으로 읽으면 슬러그가 같은 다른 소유자의 행이 내 조상이 된다(감사 M3).
    소유자 키로 옮기는 일은 `scripts/migrate_pk_namespacing.py` 가 한다."""
    sync.set_base(net.dev, NID, "Concept", {"title": "someone else's"}, 1, "2026-11-14T00:00:00.000Z")
    assert rows_at(net.dev, NID) == 1 and rows_at(net.dev, pkns.key(A, NID)) == 0
    assert sync.get_base(net.dev, NID, A) is None
    assert sync.get_base(net.dev, NID, "") is not None
