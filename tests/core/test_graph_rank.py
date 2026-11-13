# -*- coding: utf-8 -*-
"""`api.graph_rank` 은 `api.query` 가 같은 객체로 다시 내보내는 지도 순위(ppr·최근성·상금 선택)다.

잠그는 것:
- `api.graph_rank` 의 최상위 이름은 전부 `api.query` 에 **같은 객체**로 있다. 그래야 `q.attach_ppr` 같은
  호출부와 `from api.query import ...` 가 그대로 돌고, `q.PRIZE_REACH_CAP` 을 고치는 테스트가 `graph()` 가
  읽는 상한을 고친다.
- `api.graph_rank` 은 `api.query` 를 import 하지 않는다(함수 안의 지연 import 포함).
- 계획·실행(`QueryPlan`·`execute`)과 엣지 통행 비용(`_edge_cost`)은 `api.query_base` 의 것과 같은 객체다.
  상금 선택의 비용과 경로 탐색의 비용이 한 벌이어야 둘이 같은 그래프를 말한다.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from api import graph_rank  # noqa: E402
from api import query  # noqa: E402
from api import query_base  # noqa: E402

_SOURCE = ROOT / "api" / "graph_rank.py"


def _tree() -> ast.Module:
    return ast.parse(_SOURCE.read_text(encoding="utf-8"))


def _defined() -> list[str]:
    """graph_rank 이 최상위에서 정의하는 이름(가져온 이름은 뺀다)."""
    out = []
    for stmt in _tree().body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(stmt.name)
        elif isinstance(stmt, ast.Assign):
            out.extend(n.id for t in stmt.targets for n in ast.walk(t) if isinstance(n, ast.Name))
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            out.append(stmt.target.id)
    return out


def test_every_graph_rank_name_is_reexported_as_the_same_object():
    names = _defined()
    assert "attach_ppr" in names and "PRIZE_REACH_CAP" in names and "_prize_pick" in names, names
    missing = [n for n in names if not hasattr(query, n)]
    other = [n for n in names if hasattr(query, n) and getattr(query, n) is not getattr(graph_rank, n)]
    assert not missing, f"api.query 가 다시 내보내지 않는 graph_rank 이름: {missing}"
    assert not other, f"api.query 의 같은 이름이 graph_rank 과 다른 객체다: {other}"


def test_graph_rank_does_not_import_api_query():
    found = []
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name == "api.query"]
        elif isinstance(node, ast.ImportFrom):
            mod = "." * node.level + (node.module or "")
            names = {a.name for a in node.names}
            if mod in ("api.query", ".query") or (mod in ("api", ".") and "query" in names):
                found.append(mod)
    assert not found, f"graph_rank 이 api.query 를 import 한다: {found}"


def test_plan_execute_and_edge_cost_are_the_query_base_objects():
    for name in ("QueryPlan", "execute", "_edge_cost"):
        assert getattr(graph_rank, name) is getattr(query_base, name), name
        assert getattr(query, name) is getattr(query_base, name), name
