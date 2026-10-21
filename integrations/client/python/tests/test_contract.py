"""Every route and field the client sends exists in the v1 OpenAPI file.

Runs only inside the Vinc repository, where ``vinc_mcp/openapi_v1.json`` sits
next to this package. A v1 change that would break the client fails here, in
the same pull request.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from vinc_client.client import _Base


def _openapi() -> dict | None:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "vinc_mcp" / "openapi_v1.json"
        if candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8"))
    return None


SPEC = _openapi()
pytestmark = pytest.mark.skipif(SPEC is None, reason="not inside the Vinc repository")

CALLS = [
    _Base._whoami(None),
    _Base._brief("q", 3, True, None),
    _Base._node("concept:x", None),
    _Base._document("vinc.spec.fixture", None),
    _Base._search("q", 5, "c", "summary", True, None),
    _Base._recall("vinc/design", ["a"], 5, "c", None),
    _Base._ingest([{}], [{}], None, True, "op"),
    _Base._operation_status("op", None),
    _Base._edges("concept:x", None, "relates_to", "out", 5, None),
    _Base._edges(None, ["concept:x"], "relates_to", "out", 5, None),
    _Base._role("agent-dev-core", None, "a" * 64),
    _Base._role_check("agent-dev-core", "text", [{"action": "a", "reason": "r"}], True, None),
    _Base._delete("concept:x", True, "d", "op", None),
    _Base._delete_edge("concept:x", "concept:y", "relates_to", True, "d", "op", None),
    _Base._merge_nodes("concept:x", "concept:y", True, "d", "op", None),
    _Base._doc_delete("vinc.spec.fixture", True, "d", "op", None),
]


def _operation(method: str, path: str) -> dict:
    for template, operations in SPEC["paths"].items():
        pattern = "^" + re.sub(r"\{[a-z_]+\}", "[^/]+", template) + "$"
        if re.match(pattern, path) and method.lower() in operations:
            return operations[method.lower()]
    raise AssertionError(f"{method} {path} is not in openapi_v1.json")


@pytest.mark.parametrize("call", CALLS, ids=lambda c: f"{c.method} {c.path}")
def test_route_and_fields_exist(call):
    operation = _operation(call.method, call.path)
    query = {p["name"] for p in operation.get("parameters", []) if p["in"] == "query"}
    assert "space" in query
    assert set(k for k, v in call.params.items() if v is not None) <= query
    if call.body is not None:
        schema = operation["requestBody"]["content"]["application/json"]["schema"]
        assert set(call.body) <= set(schema.get("properties") or {})
