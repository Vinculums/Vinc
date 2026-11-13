# -*- coding: utf-8 -*-
"""`api.query_base` 는 `api.query` 가 같은 객체로 다시 내보내는 바닥이고, 스키마 표는 `api.node_schema` 에서 온다.

잠그는 것:
- `api.query_base` 의 최상위 이름은 전부 `api.query` 에 **같은 객체**로 있다. 그래야 `q.X` 호출부가 그대로
  돌고, `q._SEED_TABLES` 를 고치는 테스트가 이 모듈이 읽는 표를 고친다.
- `api.query_base` 는 `api.query` 를 import 하지 않는다. 나뉜 모듈이 이것을 먼저 읽는다.
- seed 표·의미층 rel·fragment 엣지 표는 `api.node_schema` 와 같은 값이다. seed 표는 Domain 을 빼고
  개방층 Entity 를 더한 것이고, Domain 이 들어오면 삭제가 `domain:` id 를 Domain 표에서 지운다.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from api import node_schema  # noqa: E402
from api import query  # noqa: E402
from api import query_base  # noqa: E402

_SOURCE = ROOT / "api" / "query_base.py"


def _defined() -> list[str]:
    """query_base 가 최상위에서 정의하는 이름(가져온 이름은 뺀다)."""
    out = []
    for stmt in ast.parse(_SOURCE.read_text(encoding="utf-8")).body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(stmt.name)
        elif isinstance(stmt, ast.Assign):
            out.extend(t.id for t in stmt.targets if isinstance(t, ast.Name))
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            out.append(stmt.target.id)
    return out


def test_every_query_base_name_is_reexported_as_the_same_object():
    names = _defined()
    assert "node" in names and "_SEED_TABLES" in names, names
    missing = [n for n in names if not hasattr(query, n)]
    other = [n for n in names if hasattr(query, n) and getattr(query, n) is not getattr(query_base, n)]
    assert not missing, f"api.query 가 다시 내보내지 않는 query_base 이름: {missing}"
    assert not other, f"api.query 의 같은 이름이 query_base 와 다른 객체다: {other}"


def test_query_base_does_not_import_api_query():
    found = []
    for node in ast.walk(ast.parse(_SOURCE.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name == "api.query"]
        elif isinstance(node, ast.ImportFrom):
            mod = "." * node.level + (node.module or "")
            names = {a.name for a in node.names}
            if mod in ("api.query", ".query") or (mod in ("api", ".") and "query" in names):
                found.append(mod)
    assert not found, f"query_base 가 api.query 를 import 한다: {found}"


def test_seed_tables_are_the_schema_tables_without_domain_plus_entity():
    seed = query_base._SEED_TABLES
    assert "domain" not in seed
    assert seed["entity"] == ("Entity", "entity_id")
    assert {k: v for k, v in seed.items() if k != "entity"} == \
        {k: v for k, v in node_schema.NODE_MAP.items() if k != "domain"}


def test_semantic_rels_follow_the_schema_edge_table_in_order():
    assert query_base._CC_RELS == tuple(node_schema.CC_EDGES.values())


def test_fragment_edge_types_map_to_the_schema_rels():
    expected = {**node_schema.CC_EDGES, **node_schema.EC_EDGES,
                node_schema.SUPERSEDES_EDGE: node_schema.SUPERSEDES_REL}
    assert query_base._FRAGMENT_EDGE_TO_REL == expected
