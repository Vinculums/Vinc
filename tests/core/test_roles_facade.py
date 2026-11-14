"""`vinc_mcp.roles` 는 roles_render · roles_contract · roles_mcp 를 다시 내보내는 문이다.

정의된 이름은 모두 `roles` 에서도 같은 객체로 읽히고, 테스트·다른 모듈이 고정한 비공개 이름도 남는다.
세 모듈의 import 는 한 방향이다(render ← contract ← mcp). 함수 안의 import 까지 세어도 고리가 없다.
"""
from __future__ import annotations

import ast
from pathlib import Path

from vinc_mcp import roles, roles_contract, roles_mcp, roles_render

ROOT = Path(__file__).resolve().parents[2]


def _defined(module) -> list[str]:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            out.append(node.name)
        elif isinstance(node, ast.Assign):
            out += [t.id for t in node.targets if isinstance(t, ast.Name)]
    return out


def test_every_name_the_three_modules_define_is_the_same_object_on_roles():
    for module in (roles_render, roles_contract, roles_mcp):
        names = _defined(module)
        assert names, module.__name__
        for name in names:
            assert getattr(roles, name) is getattr(module, name), (module.__name__, name)


def test_the_pinned_private_names_stay_on_roles():
    for name in ("_mcp_entries", "_focus_block", "_Fixed", "_gate_bullets", "_bullets", "_owner_of",
                 "serialize_result"):
        assert hasattr(roles, name), name


def _imports(module) -> set[str]:
    """모듈이 import 하는 `vinc_mcp` 형제 모듈. 함수 안의 import 도 센다."""
    siblings = {"roles", "roles_render", "roles_contract", "roles_mcp", "encoding"}
    found: set[str] = set()
    for node in ast.walk(ast.parse(Path(module.__file__).read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            found |= ({node.module} if node.module else {alias.name for alias in node.names}) & siblings
        elif isinstance(node, ast.Import):
            found |= {alias.name.removeprefix("vinc_mcp.") for alias in node.names} & siblings
    return found


def test_the_three_modules_import_in_one_direction_with_no_cycle_even_inside_functions():
    graph = {module.__name__.rsplit(".", 1)[1]: _imports(module) for module in (roles_render, roles_contract, roles_mcp)}
    assert graph["roles_render"] == set()
    assert graph["roles_contract"] <= {"encoding", "roles_render"}
    assert graph["roles_mcp"] <= {"encoding", "roles_render", "roles_contract"}

    def reaches(start: str, seen: frozenset = frozenset()) -> set[str]:
        out: set[str] = set()
        for nxt in graph.get(start, ()):
            if nxt not in seen:
                out |= {nxt} | reaches(nxt, seen | {start})
        return out
    assert not any(name in reaches(name) for name in graph)


def test_agents_sync_reads_the_renderer_module():
    text = (ROOT / "scripts" / "agents_sync.py").read_text(encoding="utf-8")
    assert "from vinc_mcp.roles_render import (" in text
