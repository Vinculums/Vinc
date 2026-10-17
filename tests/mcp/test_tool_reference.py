# -*- coding: utf-8 -*-
"""도구 참조문(`vinc_mcp/tool_reference.json`)이 레지스트리와 같은가.

호스트에 내보내는 설명은 짧은 요약이고, 전체 계약은 이 파일을 거쳐 문서 사이트로 간다. 파일이
낡으면 사이트가 옛 계약을 보여 주고, 요약의 Docs 링크는 없는 자리를 가리킨다.
근거: [[decision:mcp-tool-descriptions-are-short-summaries-with-a-docs-link]]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vinc_mcp import tool_reference, tools  # noqa: E402


def test_committed_reference_matches_the_registry():
    assert tool_reference.FILE.read_text(encoding="utf-8") == tool_reference.serialized(), (
        "참조문이 낡았다: python -X utf8 -m vinc_mcp.tool_reference 로 다시 쓸 것")


def test_reference_covers_every_hosted_tool_with_its_own_anchor():
    data = json.loads(tool_reference.serialized())
    names = [row["name"] for row in data["tools"]]
    assert names == [spec["name"] for spec in tools.specs(hub=True)]
    for row in data["tools"]:
        assert row["docs_url"] == tools.DOCS_BASE + "#" + row["name"]
        assert row["reference"] and row["summary"]


def test_long_parameter_text_survives_in_the_reference():
    """공유 인자의 긴 설명은 내보내는 목록에서 빠지고 여기에 남는다."""
    data = json.loads(tool_reference.serialized())
    search = next(row for row in data["tools"] if row["name"] == "vinc_search")
    by_name = {p["name"]: p for p in search["params"]}
    assert "validity_filtered" in by_name["as_of"]["reference"]
    assert "community" in by_name["space"]["reference"]
    assert "owner" not in by_name, "허브는 owner 를 무시하므로 광고하지 않는다"
