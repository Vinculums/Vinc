"""도구 참조문 — 문서 사이트(`/docs/mcp/tools/`)가 도구별 자리에 그리는 전체 계약의 커밋된 사본.

호스트에 내보내는 설명은 짧은 요약과 Docs 링크뿐이다(`tools.served_description`). 줄인 만큼의
계약은 사라지지 않고 여기로 온다: 요약 · Docs 주소 · 전체 계약(`reference`) · 인자(긴 설명 포함).
사이트 CI 는 이 파일을 읽어 페이지를 그리고, tests/mcp/test_tool_reference.py 가 레지스트리와
이 파일이 같은지 본다.

    python -X utf8 -m vinc_mcp.tool_reference          # 다시 쓴다
    python -X utf8 -m vinc_mcp.tool_reference --check  # 어긋나면 1

근거: [[decision:mcp-tool-descriptions-are-short-summaries-with-a-docs-link]]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import tools

FILE = Path(__file__).with_name("tool_reference.json")


def serialized() -> str:
    return json.dumps({"version": 1, "tools": tools.reference_export(hub=True)},
                      ensure_ascii=False, indent=1) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    expected = serialized()
    if args.check:
        if not FILE.exists() or FILE.read_text(encoding="utf-8") != expected:
            print("tool reference drift: run python -m vinc_mcp.tool_reference", file=sys.stderr)
            return 1
        return 0
    FILE.write_text(expected, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
