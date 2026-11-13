# -*- coding: utf-8 -*-
"""`api.neighbors` 는 `api.query` 가 같은 객체로 다시 내보내는 한 홉 관측이다(1-hop 이웃·저장된 엣지).

잠그는 것:
- `api.neighbors` 의 최상위 이름은 전부 `api.query` 에 **같은 객체**로 있다. 그래야 `q.neighbors`·`q.node_edges`
  같은 호출부(사이드카 라우트, MCP 도구, 역할 읽기)와 `from api.query import ...` 가 그대로 돌고, `api.query` 에 남은
  노드 맥락(`_domain_context`·`_entity_context`)이 같은 함수를 읽는다.
- `api.neighbors` 는 `api.query` 를 import 하지 않는다(함수 안의 지연 import 포함).
- 바닥·존재 조회·저장소에서 가져온 이름은 제 모듈의 것과 같은 객체다(`execute`·`near_nodes`·`_seed_table` 은
  `query_base`, `seed_lookup_plan` 은 `graph_read`, `open_store` 는 `store.backend`).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from api import graph_read  # noqa: E402
from api import neighbors  # noqa: E402
from api import query  # noqa: E402
from api import query_base  # noqa: E402
from store import backend  # noqa: E402

_SOURCE = ROOT / "api" / "neighbors.py"


def _tree() -> ast.Module:
    return ast.parse(_SOURCE.read_text(encoding="utf-8"))


def _defined() -> list[str]:
    """neighbors 가 최상위에서 정의하는 이름(가져온 이름은 뺀다)."""
    out = []
    for stmt in _tree().body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(stmt.name)
        elif isinstance(stmt, ast.Assign):
            out.extend(n.id for t in stmt.targets for n in ast.walk(t) if isinstance(n, ast.Name))
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            out.append(stmt.target.id)
    return out


def test_every_neighbors_name_is_reexported_as_the_same_object():
    names = _defined()
    assert "neighbors" in names and "node_edges" in names and "_domain_neighbors" in names, names
    missing = [n for n in names if not hasattr(query, n)]
    other = [n for n in names if hasattr(query, n) and getattr(query, n) is not getattr(neighbors, n)]
    assert not missing, f"api.query 가 다시 내보내지 않는 neighbors 이름: {missing}"
    assert not other, f"api.query 의 같은 이름이 neighbors 와 다른 객체다: {other}"


def test_neighbors_does_not_import_api_query():
    found = []
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name == "api.query"]
        elif isinstance(node, ast.ImportFrom):
            mod = "." * node.level + (node.module or "")
            names = {a.name for a in node.names}
            if mod in ("api.query", ".query") or (mod in ("api", ".") and "query" in names):
                found.append(mod)
    assert not found, f"neighbors 가 api.query 를 import 한다: {found}"


_HOMES = {"QueryPlan": query_base, "execute": query_base, "near_nodes": query_base,
          "_seed_table": query_base, "_track_cols": query_base, "_bridge_owner_pred": query_base,
          "_FRAGMENT_EDGE_TO_REL": query_base, "seed_lookup_plan": graph_read, "open_store": backend}


def test_imported_names_are_their_home_modules_objects():
    for name, home in _HOMES.items():
        assert getattr(neighbors, name) is getattr(home, name), name
        assert getattr(query, name) is getattr(home, name), name
