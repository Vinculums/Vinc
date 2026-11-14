# -*- coding: utf-8 -*-
"""`api.ties` 는 `api.query` 가 같은 객체로 다시 내보내는 잇기 제안이다(`suggest_ties` 와 그 산식·계획).

잠그는 것:
- `api.ties` 의 최상위 이름은 전부 `api.query` 에 **같은 객체**로 있다. 그래야 `q.suggest_ties` 를 부르는 사이드카
  경로와 MCP 도구, `q.bridge_neighbors_plan` 을 읽는 테스트, `from api.query import ...` 가 그대로 돈다.
- `api.ties` 는 `api.query` 를 import 하지 않는다(함수 안의 지연 import 포함).
- 가져온 이름은 제 것과 같은 객체다(`graph_plan` 은 `graph_read`, `_NON_CONCEPT_PREFIXES` 는 `paths`,
  `_bridge_owner_pred`·`near_nodes` 는 `query_base`, `execute`·`QueryPlan` 은 `store.querybase`, `pkns` 는 `store.pkns`).
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

from api import graph_read  # noqa: E402
from api import paths  # noqa: E402
from api import query  # noqa: E402
from api import query_base  # noqa: E402
from api import ties  # noqa: E402
from store import pkns, querybase  # noqa: E402

_SOURCE = ROOT / "api" / "ties.py"


def _tree() -> ast.Module:
    return ast.parse(_SOURCE.read_text(encoding="utf-8"))


def _defined() -> list[str]:
    """ties 가 최상위에서 정의하는 이름(가져온 이름은 뺀다)."""
    out = []
    for stmt in _tree().body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(stmt.name)
        elif isinstance(stmt, ast.Assign):
            out.extend(n.id for t in stmt.targets for n in ast.walk(t) if isinstance(n, ast.Name))
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            out.append(stmt.target.id)
    return out


def test_every_ties_name_is_reexported_as_the_same_object():
    names = _defined()
    assert {"suggest_ties", "TIE_SCORE_FORMULA", "_tag_weights", "bridge_neighbors_plan", "concept_meta_plan",
            "_TIE_TAG_MAX"} <= set(names), names
    missing = [n for n in names if not hasattr(query, n)]
    other = [n for n in names if hasattr(query, n) and getattr(query, n) is not getattr(ties, n)]
    assert not missing, f"api.query 가 다시 내보내지 않는 ties 이름: {missing}"
    assert not other, f"api.query 의 같은 이름이 ties 와 다른 객체다: {other}"


def test_ties_does_not_import_api_query():
    found = []
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name == "api.query"]
        elif isinstance(node, ast.ImportFrom):
            mod = "." * node.level + (node.module or "")
            names = {a.name for a in node.names}
            if mod in ("api.query", ".query") or (mod in ("api", ".") and "query" in names):
                found.append(mod)
    assert not found, f"ties 가 api.query 를 import 한다: {found}"


def test_imported_names_are_their_home_modules_objects():
    assert ties.pkns is pkns and query.pkns is pkns
    for name, home in (("execute", querybase), ("QueryPlan", querybase), ("graph_plan", graph_read),
                       ("_NON_CONCEPT_PREFIXES", paths), ("_bridge_owner_pred", query_base),
                       ("near_nodes", query_base)):
        assert getattr(ties, name) is getattr(home, name), name
        assert getattr(query, name) is getattr(home, name), name


def _codes(code):
    yield code
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            yield from _codes(const)


def test_every_global_name_the_functions_read_is_bound():
    """옮긴 함수가 전역에서 찾는 이름은 이 모듈에 묶여 있어야 한다(빠진 import 는 부를 때에야 NameError 로 난다)."""
    unbound = set()
    for obj in vars(ties).values():
        if isinstance(obj, types.FunctionType) and obj.__module__ == ties.__name__:
            for code in _codes(obj.__code__):
                for ins in dis.get_instructions(code):
                    if ins.opname == "LOAD_GLOBAL" and ins.argval not in vars(ties) \
                            and not hasattr(builtins, ins.argval):
                        unbound.add((obj.__name__, ins.argval))
    assert not unbound, f"ties 함수가 찾지 못하는 전역 이름: {sorted(unbound)}"
