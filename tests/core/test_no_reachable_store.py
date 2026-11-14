"""스토어를 열 수 없을 때 읽기·쓰기 표면이 무엇을 답하는지, 그리고 스토어를 누가 닫는지.

`api/` 의 읽기·쓰기 모듈은 `store` 를 안 받으면 스스로 열고(`open_store`), 열었으면 끝에서 닫는다.
열 수 없으면(None) 표면마다 다르게 답한다. 아래 표가 그 답을 고정한다.

- 읽기: 상태 `executed:False` 와 `note` 만 싣는다(저하된 읽기).
- 쓰기: `note` 와 `error` 를 같이 싣는다. `rename_domain` 만 `error` 하나다.
- 표의 모든 행이 이 두 규칙을 따른다. 스토어를 못 열면 어느 표면도 예외를 내지 않고 답한다.
  `merge_nodes` 는 쓰기라 `note` 와 `error` 를, 원장 두 읽기 꼬리(`_ledger_by_concept`,
  `ledger_drilldown`)는 `note` 만 싣는다.

두 번째 시험은 수명을 고정한다: 주입된 스토어는 닫지 않고, 모듈이 연 스토어는 정확히 한 번 닫는다.
키 모드(`VINC_PK_NAMESPACED`)와 상관없이 같은 답이어야 한다.
"""
from __future__ import annotations

from contextlib import ExitStack
from unittest import mock

import pytest

from api import decide_read, diagnose, graph_edit, merge_candidates, nav, neighbors, node_read
from store import querybase
from store.config import load_config
from vinc_rag import retrieval

OWNER = "user:no-store"
MSG = "no reachable store"

#: `open_store` 이름을 제 모듈 안에 들고 있는 모듈. 시험이 이 이름을 바꿔 끼운다.
_OPENERS = (graph_edit, neighbors, node_read, nav, diagnose, merge_candidates, decide_read,
            querybase, retrieval)

# 답의 모양. (이름, 있어야 하는 키와 값, 없어야 하는 키)
NOTE = ("note", {"executed": False, "note": MSG}, ("error",))
NOTE_ERROR = ("note+error", {"executed": False, "note": MSG, "error": MSG}, ())
ERROR = ("error", {"executed": False, "error": MSG}, ("note",))


def _ledger_tail(**kw):
    by_id = {"concept:a": {"domain": "d", "ts": "", "episodic": True, "record": False,
                           "degree": 0, "gateway": False}}
    with mock.patch.object(nav, "_ledger_classify", return_value=(by_id, "domain", None)):
        return nav.ledger_drilldown(owner=OWNER, domain="d", mark="episodic", **kw)


def _search_with_rows(**kw):
    with mock.patch.object(node_read._retrieval, "search",
                           return_value={"rows": [{"doc_id": "d1"}], "node_hits": []}):
        return node_read.search("q", owner=OWNER, **kw)


# 표면 이름 -> (호출, 답의 모양, 스토어가 None 일 때 open_store 가 불리는 총 횟수)
SURFACES = {
    "graph_edit.rename_domain": (
        lambda **kw: graph_edit.rename_domain(owner=OWNER, old="a", new="b", **kw), ERROR, 1),
    "graph_edit.delete_edge": (
        lambda **kw: graph_edit.delete_edge("concept:a", "relates_to", "concept:b",
                                            owner=OWNER, **kw), NOTE_ERROR, 1),
    "graph_edit.delete_node": (
        lambda **kw: graph_edit.delete_node("concept:a", owner=OWNER, **kw), NOTE_ERROR, 1),
    "graph_edit.merge_nodes": (
        lambda **kw: graph_edit.merge_nodes("concept:a", "concept:b", owner=OWNER, **kw),
        NOTE_ERROR, 1),
    "graph_edit.purge_owner": (
        lambda **kw: graph_edit.purge_owner(OWNER, **kw), NOTE_ERROR, 1),
    "graph_edit.owner_stats": (
        lambda **kw: graph_edit.owner_stats(**kw), NOTE_ERROR, 1),
    "neighbors._domain_neighbors": (
        lambda **kw: neighbors._domain_neighbors("domain:x", owner=OWNER, limit=None, **kw),
        NOTE_ERROR, 1),
    "neighbors.node_edges": (
        lambda **kw: neighbors.node_edges(node="concept:a", owner=OWNER, **kw), NOTE, 1),
    "node_read.node_context": (
        lambda **kw: node_read.node_context(owner=OWNER, node="concept:a", **kw), NOTE, 2),
    "node_read._node_context": (
        lambda **kw: node_read._node_context(owner=OWNER, node="concept:a", **kw), NOTE, 1),
    "nav.edge_context": (
        lambda **kw: nav.edge_context(owner=OWNER, src="concept:a", dst="concept:b",
                                      etype="RELATED_TO", **kw), NOTE, 1),
    "nav.nav_tree": (lambda **kw: nav.nav_tree(owner=OWNER, **kw), NOTE, 1),
    "nav.docs_in_range": (
        lambda **kw: nav.docs_in_range(owner=OWNER, start="2026-01-01", end="2026-02-01", **kw),
        NOTE, 1),
    "nav._ledger_by_concept": (
        lambda cfg=None, store=None: nav._ledger_by_concept(
            {"c": {"domain": "d"}}, owner=OWNER, scope="all", cfg=cfg, store=store),
        NOTE, 1),
    "nav.ledger_drilldown": (_ledger_tail, NOTE, 1),
    "diagnose.component_diagnosis": (
        lambda **kw: diagnose.component_diagnosis(owner=OWNER, **kw), NOTE, 1),
    "diagnose.claim_diagnosis": (
        lambda **kw: diagnose.claim_diagnosis(owner=OWNER, **kw), NOTE, 1),
    "diagnose.communities": (lambda **kw: diagnose.communities(owner=OWNER, **kw), NOTE, 1),
    "diagnose.community_context": (
        lambda **kw: diagnose.community_context(owner=OWNER, node="concept:a", **kw), NOTE, 1),
    "merge_candidates.list_merge_rejections": (
        lambda **kw: merge_candidates.list_merge_rejections(owner=OWNER, **kw), NOTE, 1),
    "merge_candidates.reject_merge_pair": (
        lambda **kw: merge_candidates.reject_merge_pair(owner=OWNER, a_id="concept:a",
                                                        b_id="concept:b", **kw), NOTE_ERROR, 1),
    # 기각 조회와 계획 실행이 같은 연결을 보려고 스토어를 먼저 연다. 그 연결이 None 이면
    # `execute` 가 한 번 더 열어 본다.
    "merge_candidates.suggest_merge_candidates": (
        lambda **kw: merge_candidates.suggest_merge_candidates(owner=OWNER, **kw), NOTE, 2),
    "decide_read.decide_paths": (
        lambda **kw: decide_read.decide_paths(["repo:path"], owner=OWNER, **kw), NOTE, 1),
    # 인용 id 를 붙이는 자리는 열지 못해도 행을 지우지 않고 `cite_unavailable` 로 남긴다.
    "node_read.search": (_search_with_rows, ("cited", None, None), 1),
}


class _Store:
    """읽기 전부에 빈 행을 돌려주고 닫힌 횟수를 센다."""

    def __init__(self) -> None:
        self.closed = 0

    def run(self, *_args, **_kwargs):
        return []

    def close(self) -> None:
        self.closed += 1


def _patch_openers(stack: ExitStack, make):
    """모든 `open_store` 를 `make()` 로 바꾸고, 불린 횟수를 모듈 이름별로 모은다."""
    opened: list = []

    def opener(*_a, **_k):
        store = make()
        opened.append(store)
        return store

    for mod in _OPENERS:
        stack.enter_context(mock.patch.object(mod, "open_store", opener))
    return opened


@pytest.mark.parametrize("name", list(SURFACES))
def test_no_reachable_store_answers(name):
    call, shape, opens = SURFACES[name]
    with ExitStack() as stack:
        opened = _patch_openers(stack, lambda: None)
        got = call()
        if shape[0] == "cited":
            assert got["rows"] == [{"doc_id": "d1", "cite_id": None,
                                    "cite_unavailable": "document mirror missing or unreadable"}]
            assert "citation_error" not in got
        else:
            _, present, absent = shape
            for key, value in present.items():
                assert got.get(key) == value, (name, key, got)
            for key in absent:
                assert key not in got, (name, key, got)
    assert len(opened) == opens, (name, len(opened))


@pytest.mark.parametrize("name", list(SURFACES))
def test_injected_store_is_never_closed(name, tmp_path):
    call, _shape, _opens = SURFACES[name]
    cfg = load_config({"data_dir": str(tmp_path / "db")})
    store = _Store()
    with ExitStack() as stack:
        _patch_openers(stack, _Store)
        try:
            call(cfg=cfg, store=store)
        except Exception:
            pass          # 빈 스토어에서 본문이 어떻게 끝나든 수명만 본다
    assert store.closed == 0, name


@pytest.mark.parametrize("name", list(SURFACES))
def test_opened_store_is_closed_exactly_once(name, tmp_path):
    call, _shape, _opens = SURFACES[name]
    cfg = load_config({"data_dir": str(tmp_path / "db")})
    with ExitStack() as stack:
        opened = _patch_openers(stack, _Store)
        try:
            call(cfg=cfg)
        except Exception:
            pass
    assert opened, name
    assert [s.closed for s in opened] == [1] * len(opened), name
