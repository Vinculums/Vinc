# -*- coding: utf-8 -*-
"""`api.graph_edit` 은 `api.query` 가 같은 객체로 다시 내보내는 그래프 쓰기 표면이다.

잠그는 것:
- `api.graph_edit` 의 최상위 이름은 전부 `api.query` 에 **같은 객체**로 있다. 그래야 `q.delete_node` 같은
  호출부(사이드카 라우트, MCP 도구, 동기화)와 `from api.query import ...` 가 그대로 돈다.
- `api.graph_edit` 은 `api.query` 를 import 하지 않는다(함수 안의 지연 import 포함). 쓰기가 읽기 모듈에
  기대면 둘이 서로를 import 하는 고리가 다시 생긴다.
- 쓰기가 읽는 스키마 표(seed 표, 소유 라벨, 관계 표)는 `api.query_base` 의 것과 같은 객체다. 테스트가
  `q._SEED_TABLES` 를 고치면 삭제가 읽는 표가 바뀌어야 한다.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from api import graph_edit  # noqa: E402
from api import query  # noqa: E402
from api import query_base  # noqa: E402

_SOURCE = ROOT / "api" / "graph_edit.py"
_SCHEMA_TABLES = ("SYNC_EDGE_RELS", "WIKI_EDGE_RELS", "_CC_RELS", "_FRAGMENT_EDGE_TO_REL",
                  "_OWNER_SCOPED_LABELS", "_SEED_TABLES")


def _tree() -> ast.Module:
    return ast.parse(_SOURCE.read_text(encoding="utf-8"))


def _defined() -> list[str]:
    """graph_edit 이 최상위에서 정의하는 이름(가져온 이름은 뺀다)."""
    out = []
    for stmt in _tree().body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(stmt.name)
        elif isinstance(stmt, ast.Assign):
            out.extend(n.id for t in stmt.targets for n in ast.walk(t) if isinstance(n, ast.Name))
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            out.append(stmt.target.id)
    return out


def test_every_graph_edit_name_is_reexported_as_the_same_object():
    names = _defined()
    assert "delete_node" in names and "_NODE_PK" in names and "owner_stats" in names, names
    missing = [n for n in names if not hasattr(query, n)]
    other = [n for n in names if hasattr(query, n) and getattr(query, n) is not getattr(graph_edit, n)]
    assert not missing, f"api.query 가 다시 내보내지 않는 graph_edit 이름: {missing}"
    assert not other, f"api.query 의 같은 이름이 graph_edit 과 다른 객체다: {other}"


def test_graph_edit_does_not_import_api_query():
    found = []
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name == "api.query"]
        elif isinstance(node, ast.ImportFrom):
            mod = "." * node.level + (node.module or "")
            names = {a.name for a in node.names}
            if mod in ("api.query", ".query") or (mod in ("api", ".") and "query" in names):
                found.append(mod)
    assert not found, f"graph_edit 이 api.query 를 import 한다: {found}"


def test_schema_tables_are_the_query_base_objects():
    for name in _SCHEMA_TABLES:
        assert getattr(graph_edit, name) is getattr(query_base, name), name
        assert getattr(query, name) is getattr(query_base, name), name
