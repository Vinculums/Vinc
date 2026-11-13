# -*- coding: utf-8 -*-
"""`api.graph_read` 는 `api.query` 가 같은 객체로 다시 내보내는 지도 읽기다(L1·L2 그래프, 미리보기, 결합, 원장 계획).

잠그는 것:
- `api.graph_read` 의 최상위 이름은 전부 `api.query` 에 **같은 객체**로 있다. 그래야 `q.graph` 같은 호출부(사이드카
  라우트, MCP 도구, 공유 팩)와 `from api.query import ...` 가 그대로 돌고, `api.query` 에 남은 `decide()`·원장 분류가
  같은 계획 함수를 읽는다.
- `api.graph_read` 는 `api.query` 를 import 하지 않는다(함수 안의 지연 import 포함).
- 바닥·좌표·순위에서 가져온 이름은 제 모듈의 것과 같은 객체다(`execute`·`near_nodes` 는 `query_base`,
  `layout_positions` 는 `graph_layout`, `attach_ppr`·`_prize_pick`·`PRIZE_REACH_CAP` 은 `graph_rank`).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from api import graph_layout  # noqa: E402
from api import graph_rank  # noqa: E402
from api import graph_read  # noqa: E402
from api import query  # noqa: E402
from api import query_base  # noqa: E402

_SOURCE = ROOT / "api" / "graph_read.py"


def _tree() -> ast.Module:
    return ast.parse(_SOURCE.read_text(encoding="utf-8"))


def _defined() -> list[str]:
    """graph_read 가 최상위에서 정의하는 이름(가져온 이름은 뺀다)."""
    out = []
    for stmt in _tree().body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(stmt.name)
        elif isinstance(stmt, ast.Assign):
            out.extend(n.id for t in stmt.targets for n in ast.walk(t) if isinstance(n, ast.Name))
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            out.append(stmt.target.id)
    return out


def test_every_graph_read_name_is_reexported_as_the_same_object():
    names = _defined()
    assert "graph" in names and "_GRAPH_READ_PLANS" in names and "_mark_topic_descriptions" in names, names
    missing = [n for n in names if not hasattr(query, n)]
    other = [n for n in names if hasattr(query, n) and getattr(query, n) is not getattr(graph_read, n)]
    assert not missing, f"api.query 가 다시 내보내지 않는 graph_read 이름: {missing}"
    assert not other, f"api.query 의 같은 이름이 graph_read 와 다른 객체다: {other}"


def test_graph_read_does_not_import_api_query():
    found = []
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name == "api.query"]
        elif isinstance(node, ast.ImportFrom):
            mod = "." * node.level + (node.module or "")
            names = {a.name for a in node.names}
            if mod in ("api.query", ".query") or (mod in ("api", ".") and "query" in names):
                found.append(mod)
    assert not found, f"graph_read 가 api.query 를 import 한다: {found}"


_HOMES = {"QueryPlan": query_base, "execute": query_base, "near_nodes": query_base,
          "_seed_table": query_base, "_topic_description_mirrors_by_domain": query_base,
          "layout_positions": graph_layout, "_domain_path": graph_layout,
          "attach_ppr": graph_rank, "_prize_pick": graph_rank, "PRIZE_REACH_CAP": graph_rank}


def test_imported_names_are_their_home_modules_objects():
    for name, home in _HOMES.items():
        assert getattr(graph_read, name) is getattr(home, name), name
        assert getattr(query, name) is getattr(home, name), name
