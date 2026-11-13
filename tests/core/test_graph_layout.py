# -*- coding: utf-8 -*-
"""`api.graph_layout` 은 `api.query` 가 같은 객체로 다시 내보내는 지도 배치 기하다.

잠그는 것:
- `api.graph_layout` 의 최상위 이름은 전부 `api.query` 에 **같은 객체**로 있다. 그래야 `q.layout_positions`
  같은 호출부와 `from api.query import ...` 가 그대로 돈다.
- `api.graph_layout` 은 `api.query` 를 import 하지 않고, 스토어·검색 모듈도 읽지 않는다. 배치는 받은 노드와
  엣지만으로 좌표를 정하는 순수 함수다.
- 방향 관계 표는 `api.query_base` 의 것과 같은 객체다. 배치와 상충 판정이 한 벌을 같이 읽어야 둘이 같은
  방향을 말한다.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from api import graph_layout  # noqa: E402
from api import query  # noqa: E402
from api import query_base  # noqa: E402

_SOURCE = ROOT / "api" / "graph_layout.py"


def _tree() -> ast.Module:
    return ast.parse(_SOURCE.read_text(encoding="utf-8"))


def _defined() -> list[str]:
    """graph_layout 이 최상위에서 정의하는 이름(가져온 이름은 뺀다)."""
    out = []
    for stmt in _tree().body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(stmt.name)
        elif isinstance(stmt, ast.Assign):
            out.extend(n.id for t in stmt.targets for n in ast.walk(t) if isinstance(n, ast.Name))
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            out.append(stmt.target.id)
    return out


def _imported_modules() -> list[str]:
    """graph_layout 이 어디서든(함수 안 포함) import 하는 모듈 이름. 상대 import 는 점을 붙인다."""
    found = []
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append("." * node.level + (node.module or ""))
    return found


def test_every_graph_layout_name_is_reexported_as_the_same_object():
    names = _defined()
    assert "layout_positions" in names and "_CONE_MAX" in names, names
    missing = [n for n in names if not hasattr(query, n)]
    other = [n for n in names if hasattr(query, n) and getattr(query, n) is not getattr(graph_layout, n)]
    assert not missing, f"api.query 가 다시 내보내지 않는 graph_layout 이름: {missing}"
    assert not other, f"api.query 의 같은 이름이 graph_layout 과 다른 객체다: {other}"


def test_graph_layout_reads_no_query_store_or_search_module():
    mods = _imported_modules()
    bad = [m for m in mods
           if m in ("api.query", ".query") or m.split(".")[0] in ("store", "vinc_rag", "engines")]
    assert not bad, f"graph_layout 이 읽으면 안 되는 모듈을 import 한다: {bad}"
    assert set(mods) <= {"__future__", ".query_base", "math"}, mods


def test_direction_tables_are_the_query_base_objects():
    for name in ("_DIRECTED_RELS", "_REVERSED_RELS"):
        assert getattr(graph_layout, name) is getattr(query_base, name), name
        assert getattr(query, name) is getattr(query_base, name), name
