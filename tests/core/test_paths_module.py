# -*- coding: utf-8 -*-
"""`api.paths` 는 `api.query` 가 같은 객체로 다시 내보내는 최단 경로 읽기다(홉 최단·confidence 가중 최단·끝점 거절).

잠그는 것:
- `api.paths` 의 최상위 이름은 전부 `api.query` 에 **같은 객체**로 있다. 그래야 `q.shortest_path`·`q.WEIGHT_HOPS`
  같은 호출부(사이드카 경로 라우트)와 `from api.query import ...` 가 그대로 돌고, `api.query` 에 남은 잇기 제안
  (`suggest_ties`)이 같은 `_NON_CONCEPT_PREFIXES` 를 읽는다.
- `api.paths` 는 `api.query` 를 import 하지 않는다(함수 안의 지연 import 포함).
- 바닥·계획·셰이핑에서 가져온 이름은 제 모듈의 것과 같은 객체다(`execute`·`near_nodes`·`_seed_table`·`_edge_cost` 는
  `query_base`, `seed_lookup_plan`·`shape_graph`·`graph_rows_for_ids_plan` 은 `graph_read`).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from api import graph_read  # noqa: E402
from api import paths  # noqa: E402
from api import query  # noqa: E402
from api import query_base  # noqa: E402

_SOURCE = ROOT / "api" / "paths.py"


def _tree() -> ast.Module:
    return ast.parse(_SOURCE.read_text(encoding="utf-8"))


def _defined() -> list[str]:
    """paths 가 최상위에서 정의하는 이름(가져온 이름은 뺀다)."""
    out = []
    for stmt in _tree().body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(stmt.name)
        elif isinstance(stmt, ast.Assign):
            out.extend(n.id for t in stmt.targets for n in ast.walk(t) if isinstance(n, ast.Name))
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            out.append(stmt.target.id)
    return out


def test_every_paths_name_is_reexported_as_the_same_object():
    names = _defined()
    assert "shortest_path" in names and "weighted_paths" in names and "_NON_CONCEPT_PREFIXES" in names, names
    missing = [n for n in names if not hasattr(query, n)]
    other = [n for n in names if hasattr(query, n) and getattr(query, n) is not getattr(paths, n)]
    assert not missing, f"api.query 가 다시 내보내지 않는 paths 이름: {missing}"
    assert not other, f"api.query 의 같은 이름이 paths 와 다른 객체다: {other}"


def test_paths_does_not_import_api_query():
    found = []
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name == "api.query"]
        elif isinstance(node, ast.ImportFrom):
            mod = "." * node.level + (node.module or "")
            names = {a.name for a in node.names}
            if mod in ("api.query", ".query") or (mod in ("api", ".") and "query" in names):
                found.append(mod)
    assert not found, f"paths 가 api.query 를 import 한다: {found}"


_HOMES = {"QueryPlan": query_base, "execute": query_base, "near_nodes": query_base,
          "_seed_table": query_base, "_edge_cost": query_base, "seed_lookup_plan": graph_read,
          "shape_graph": graph_read, "graph_rows_for_ids_plan": graph_read}


def test_imported_names_are_their_home_modules_objects():
    for name, home in _HOMES.items():
        assert getattr(paths, name) is getattr(home, name), name
        assert getattr(query, name) is getattr(home, name), name
