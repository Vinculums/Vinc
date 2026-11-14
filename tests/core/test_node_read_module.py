# -*- coding: utf-8 -*-
"""`api.node_read` 는 `api.query` 가 같은 객체로 다시 내보내는 노드 읽기다(검색 파사드·노드 상세·관계 목록).

잠그는 것:
- `api.node_read` 의 최상위 이름은 전부 `api.query` 에 **같은 객체**로 있다. 그래야 `q.search`·`q.node_context`
  같은 호출부(사이드카 검색·노드 라우트, MCP 도구, 브리프)와 `from api.query import ...` 가 그대로 돌고, `api.query`
  에 남은 브리프가 같은 `search`·`node_context` 를 읽는다.
- `api.node_read` 는 `api.query` 를 import 하지 않는다(함수 안의 지연 import 포함).
- 바닥·한 홉 관측·검색 코어에서 가져온 이름은 제 모듈의 것과 같은 객체다(`execute`·`node`·`node_plan`·`_seed_table` 은
  `query_base`, `_domain_neighbors`·`_typed_neighbors_cypher` 는 `neighbors`, `_retrieval` 은 `vinc_rag.retrieval`).
- 옮긴 함수가 전역에서 찾는 이름은 모두 이 모듈에 묶여 있다(빠진 import 는 부를 때에야 `NameError` 로 드러난다).
"""
from __future__ import annotations

import ast
import builtins
import dis
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from api import neighbors  # noqa: E402
from api import node_read  # noqa: E402
from api import query  # noqa: E402
from api import query_base  # noqa: E402
from vinc_rag import retrieval  # noqa: E402

_SOURCE = ROOT / "api" / "node_read.py"


def _tree() -> ast.Module:
    return ast.parse(_SOURCE.read_text(encoding="utf-8"))


def _defined() -> list[str]:
    """node_read 가 최상위에서 정의하는 이름(가져온 이름은 뺀다)."""
    out = []
    for stmt in _tree().body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(stmt.name)
        elif isinstance(stmt, ast.Assign):
            out.extend(n.id for t in stmt.targets for n in ast.walk(t) if isinstance(n, ast.Name))
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            out.append(stmt.target.id)
    return out


def test_every_node_read_name_is_reexported_as_the_same_object():
    names = _defined()
    assert "search" in names and "node_context" in names and "_node_links" in names, names
    missing = [n for n in names if not hasattr(query, n)]
    other = [n for n in names if hasattr(query, n) and getattr(query, n) is not getattr(node_read, n)]
    assert not missing, f"api.query 가 다시 내보내지 않는 node_read 이름: {missing}"
    assert not other, f"api.query 의 같은 이름이 node_read 와 다른 객체다: {other}"


def test_node_read_does_not_import_api_query():
    found = []
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name == "api.query"]
        elif isinstance(node, ast.ImportFrom):
            mod = "." * node.level + (node.module or "")
            names = {a.name for a in node.names}
            if mod in ("api.query", ".query") or (mod in ("api", ".") and "query" in names):
                found.append(mod)
    assert not found, f"node_read 가 api.query 를 import 한다: {found}"


_HOMES = {"execute": query_base, "node": query_base, "node_plan": query_base, "_seed_table": query_base,
          "_stamp_validity": query_base, "_track_cols": query_base, "_domain_neighbors": neighbors,
          "_typed_neighbors_cypher": neighbors, "_chunk_count": retrieval}


def test_imported_names_are_their_home_modules_objects():
    for name, home in _HOMES.items():
        assert getattr(node_read, name) is getattr(home, name), name
        assert getattr(query, name) is getattr(home, name), name


def test_search_core_is_the_retrieval_module():
    assert node_read._retrieval is retrieval
    assert query._retrieval is retrieval


def _codes(code):
    yield code
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            yield from _codes(const)


def test_every_global_name_the_functions_read_is_bound():
    """옮긴 함수가 전역에서 찾는 이름은 이 모듈에 묶여 있어야 한다(빠진 import 는 부를 때에야 NameError 로 난다)."""
    unbound = set()
    for obj in vars(node_read).values():
        if isinstance(obj, types.FunctionType) and obj.__module__ == node_read.__name__:
            for code in _codes(obj.__code__):
                for ins in dis.get_instructions(code):
                    if ins.opname == "LOAD_GLOBAL" and ins.argval not in vars(node_read) \
                            and not hasattr(builtins, ins.argval):
                        unbound.add((obj.__name__, ins.argval))
    assert not unbound, f"node_read 함수가 찾지 못하는 전역 이름: {sorted(unbound)}"
