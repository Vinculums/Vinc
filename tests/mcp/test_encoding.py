"""결과 인코더는 `vinc_mcp.encoding` 한 벌이고, `roles.serialize_result` 는 같은 함수를 가리킨다."""
from __future__ import annotations

import ast
import json
from pathlib import Path

from vinc_mcp import encoding, roles


def test_roles_keeps_the_name_and_it_is_the_same_function():
    assert roles.serialize_result is encoding.serialize_result


def test_a_role_is_written_compact_and_anything_else_with_the_default_separators():
    role = {"kind": "role", "id": "concept:agent-x", "props": {"한": "글"}}
    wrapped = {"role": {"kind": "role", "id": "concept:agent-x"}, "executed": True}
    other = {"kind": "search", "rows": [{"t": "é"}]}
    assert encoding.serialize_result(role) == json.dumps(role, ensure_ascii=False, separators=(",", ":"))
    assert encoding.serialize_result(wrapped) == json.dumps(wrapped, ensure_ascii=False, separators=(",", ":"))
    assert encoding.serialize_result(other) == json.dumps(other, ensure_ascii=False)
    assert encoding.serialize_result([role]) == json.dumps([role], ensure_ascii=False)


def test_the_encoder_imports_no_other_vinc_mcp_module():
    source = (Path(__file__).resolve().parents[2] / "vinc_mcp" / "encoding.py").read_text(encoding="utf-8")
    imported = [node for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.ImportFrom) and (node.level or (node.module or "").startswith("vinc_mcp"))]
    assert imported == []
