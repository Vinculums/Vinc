# -*- coding: utf-8 -*-
"""두 어댑터가 같이 쓰는 조각(`store.adapter_common`) — 엔진도 서버도 없이 돈다.

잠그는 것:
- `fold_children` 은 두 어댑터가 각자 쓰던 부모 접기와 출력이 같다. 같은 입력(동점, 부모 id 가
  없는 행, top_k 경계)을 세 구현에 넣어 같은 행 객체가 같은 순서로 나오는지 표로 본다.
- 두 어댑터의 `vector_search` 가 실제로 그 접기를 거친다(질의 실행만 가짜로 바꾼다).
- 임베디드 쪽 사설 이름은 같은 객체를 가리키고, 과추출 배수는 두 어댑터가 같은 값이다.
- 스코프 필터 술어의 계약(모르는 키는 던진다, WHERE 가 이미 있는 자리는 AND).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from store import adapter_common as ac  # noqa: E402
from store import embedded_backend as eb  # noqa: E402
from store import neo4j_backend as nb  # noqa: E402


# ── 기준 구현: 공용 모듈로 옮기기 전 두 어댑터의 접기 루프를 글자 그대로 옮겨 적었다 ──

def _embedded_fold(rows, top_k):
    best: dict[str, dict] = {}
    for r in rows:                       # 이미 distance 오름차순 — 첫 등장이 최소
        best.setdefault(r.get("id"), r)
    out = list(best.values())[:int(top_k)]
    return out


def _neo4j_fold(rows, top_k):
    best: dict[str, dict] = {}
    for r in rows:
        best.setdefault(r.get("id"), r)
    return list(best.values())[:int(top_k)]


def _rows(*spec):
    """(id, distance) 쌍으로 행을 만든다. id 자리에 `...` 를 주면 `id` 키가 없는 행이다."""
    out = []
    for i, (cid, d) in enumerate(spec):
        r = {"text": f"t{i}", "distance": d}
        if cid is not ...:
            r["id"] = cid
        out.append(r)
    return out


_CASES = {
    "빈 입력": (_rows(), 3),
    "한 행": (_rows(("a", .1)), 1),
    "한 부모의 자식 여럿은 첫 행 하나": (_rows(("a", .1), ("a", .2), ("a", .3)), 5),
    "부모가 섞여도 첫 등장 순서": (_rows(("a", .1), ("b", .2), ("a", .25), ("c", .3), ("b", .4)), 3),
    "동점 거리는 들어온 순서": (_rows(("b", .2), ("a", .2), ("c", .2), ("a", .2)), 3),
    "동점이 top_k 경계에 걸린다": (_rows(("a", .1), ("b", .2), ("c", .2), ("d", .2)), 2),
    "id 키가 없는 행은 하나로 접힌다": (_rows((..., .1), ("a", .2), (..., .3)), 5),
    "id 가 None 인 행과 키가 없는 행은 같은 자리": (_rows(("a", .1), (None, .2), (..., .3), ("b", .4)), 5),
    "부모가 하나도 없다": (_rows((..., .1), (..., .2)), 2),
    "top_k 0": (_rows(("a", .1), ("b", .2)), 0),
    "top_k 가 부모 수와 같다": (_rows(("a", .1), ("b", .2), ("a", .3)), 2),
    "top_k 가 부모 수보다 하나 작다": (_rows(("a", .1), ("b", .2), ("c", .3)), 2),
    "top_k 가 부모 수보다 크다": (_rows(("a", .1), ("b", .2)), 10),
    "top_k 가 문자열": (_rows(("a", .1), ("b", .2), ("c", .3)), "2"),
    "top_k 가 실수면 내림": (_rows(("a", .1), ("b", .2), ("c", .3)), 2.9),
    "top_k 가 음수면 뒤에서 자른다": (_rows(("a", .1), ("b", .2), ("c", .3)), -1),
    "id 가 숫자와 문자열로 섞인다": (_rows((1, .1), ("1", .2), (1, .3)), 5),
}


@pytest.mark.parametrize("rows,top_k", list(_CASES.values()), ids=list(_CASES))
def test_세_구현의_접기가_같다(rows, top_k):
    want = _embedded_fold(rows, top_k)
    assert _neo4j_fold(rows, top_k) == want
    got = ac.fold_children(rows, top_k)
    assert got == want
    # 같은 행 객체를 그대로 돌려준다(복사·수정하지 않는다).
    assert [id(r) for r in got] == [id(r) for r in want]


@pytest.mark.parametrize("rows,top_k,exc", [
    (_rows(("a", .1)), None, TypeError),          # top_k 를 정수로 못 바꾼다
    ([{"id": ["a"], "distance": .1}], 1, TypeError),  # id 가 해시되지 않는다
])
def test_세_구현이_같은_예외를_낸다(rows, top_k, exc):
    for fold in (_embedded_fold, _neo4j_fold, ac.fold_children):
        with pytest.raises(exc):
            fold(rows, top_k)


def test_접기는_입력을_고치지_않는다():
    rows = _rows(("a", .1), ("a", .2), ("b", .3))
    before = [dict(r) for r in rows]
    ac.fold_children(rows, 2)
    assert rows == before


# ── 두 어댑터가 실제로 이 접기를 거친다 ─────────────────────────────────────────

_CHILD_ROWS = _rows(("a", .1), ("a", .15), ("b", .2), (..., .22), ("c", .3), ("b", .35), ("d", .4))


@pytest.mark.parametrize("top_k", [0, 1, 2, 3, 4, 5, 9])
def test_임베디드_벡터_검색이_접기를_거친다(monkeypatch, top_k):
    backend = object.__new__(eb.EmbeddedBackend)
    backend._conn = object()
    backend._load_ext = lambda *a, **kw: None
    seen = []

    def execute(conn, query, params):
        seen.append(dict(params))
        return list(_CHILD_ROWS)

    monkeypatch.setattr(eb, "_exec_to_dicts", execute)
    monkeypatch.setattr(eb, "keep_vector_index", lambda: False)
    got = backend.vector_search([1.0], top_k)
    assert got == _embedded_fold(_CHILD_ROWS, top_k)
    assert seen[0]["k"] == top_k * ac.CHILD_FANOUT


@pytest.mark.parametrize("top_k", [0, 1, 2, 3, 4, 5, 9])
def test_Neo4j_벡터_검색이_접기를_거친다(top_k):
    backend = object.__new__(nb.Neo4jBackend)
    seen = []

    def run(cypher, params=None):
        seen.append(dict(params or {}))
        return list(_CHILD_ROWS)

    backend.run = run
    got = backend.vector_search([1.0], top_k)
    assert got == _neo4j_fold(_CHILD_ROWS, top_k)
    assert seen[0]["k"] == top_k * ac.CHILD_FANOUT


def test_필터가_걸리면_두_어댑터가_같은_배수로_과추출한다(monkeypatch):
    filters = {"owner_id": "user:me"}
    emb_seen, neo_seen = [], []

    def execute(conn, query, params):
        emb_seen.append(dict(params))
        return list(_CHILD_ROWS)

    monkeypatch.setattr(eb, "_exec_to_dicts", execute)
    monkeypatch.setattr(eb, "keep_vector_index", lambda: False)
    e = object.__new__(eb.EmbeddedBackend)
    e._conn = object()
    e._load_ext = lambda *a, **kw: None
    e.vector_search([1.0], 2, filters)

    n = object.__new__(nb.Neo4jBackend)
    n.run = lambda cypher, params=None: neo_seen.append(dict(params or {})) or list(_CHILD_ROWS)
    n.vector_search([1.0], 2, filters)

    want = 2 * ac.OVERSAMPLE * ac.CHILD_FANOUT
    assert emb_seen[0]["k"] == neo_seen[0]["k"] == want
    assert emb_seen[0]["owner_id"] == neo_seen[0]["owner_id"] == "user:me"


# ── 이름과 값 ─────────────────────────────────────────────────────────────────

def test_임베디드_사설_이름은_같은_객체다():
    assert eb._filter_clause is ac.filter_clause
    assert eb._Result is ac.Result
    assert eb._CHUNK_FILTER_KEYS is ac.CHUNK_FILTER_KEYS
    assert eb.EmbeddedBackend._CHILD_FANOUT == ac.CHILD_FANOUT == 4
    assert eb.EmbeddedBackend._OVERSAMPLE == ac.OVERSAMPLE == 5


def test_Neo4j_어댑터는_임베디드_어댑터를_부르지_않는다():
    src = Path(nb.__file__).read_text(encoding="utf-8")
    assert "embedded_backend" not in src
    assert nb.filter_clause is ac.filter_clause
    assert nb.Result is ac.Result
    assert nb.fold_children is ac.fold_children


def test_결과_묶음은_neo4j_Result_모양이다():
    r = ac.Result([{"x": 1}, {"x": 2}])
    assert list(r) == [{"x": 1}, {"x": 2}]
    assert r.single() == {"x": 1}
    assert r.data() == [{"x": 1}, {"x": 2}]
    assert ac.Result([]).single() is None


# ── 스코프 필터 술어 ──────────────────────────────────────────────────────────

def test_필터가_없으면_빈_술어다():
    params: dict = {}
    assert ac.filter_clause("p", None, params) == ""
    assert ac.filter_clause("p", {}, params) == ""
    assert params == {}


def test_필터_술어는_열_순서이고_None_은_건너뛴다():
    params: dict = {}
    got = ac.filter_clause("p", {"doc_id": "d1", "layer": None, "owner_id": "u"}, params)
    assert got == "WHERE p.owner_id = $owner_id AND p.doc_id = $doc_id "
    assert params == {"owner_id": "u", "doc_id": "d1"}


def test_WHERE_가_이미_있는_자리는_AND_로_붙는다():
    params: dict = {}
    assert ac.filter_clause("p", {"layer": "L"}, params, keyword="AND") == "AND p.layer = $layer "


def test_모르는_필터_키는_던진다():
    with pytest.raises(ValueError, match="unsupported chunk search filter: zzz"):
        ac.filter_clause("p", {"owner_id": "u", "zzz": 1}, {})
