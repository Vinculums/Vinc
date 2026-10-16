# -*- coding: utf-8 -*-
"""호스트가 받는 도구 문장에 대시와 내부 표기가 없다(VIN-589).

모델은 도구 설명과 인자 설명만 보고 도구를 고른다. 그 문장에 em-dash(U+2014)·en-dash
(U+2013)가 103개 있었고, 내부 코드(E-G5·D45-V·DEC·Chunk=SSoT)와 경고 기호·한국어 예시가
섞여 있었다. 화면 문구 규칙([[vinc.manual.app]] §3)과 같은 이유로 대시를 걷어 냈고, 다시
들어오지 않게 여기서 막는다.

검사 대상은 **호스트가 실제로 받는 목록**이다(`tools.specs`). 프로파일 둘을 모두 본다:
허브(호스티드·REST)와 로컬(stdio·CLI). 설명은 스키마 안쪽까지
(중첩 items·properties, outputSchema) 전부 걷는다. 코드 주석은 대상이 아니다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vinc_mcp import tools  # noqa: E402

PROFILES = {
    "hosted": lambda: tools.specs(hub=True),
    "local": lambda: tools.specs(),
}

DASHES = ("—", "–")
#: 호스트에 나가면 안 되는 내부 표기. 식별자(ATTACHES_TO 등)는 대상이 아니다.
INTERNAL = re.compile(r"\bE-G\d+\b|\bD45(?:-V)?\b|\bDEC\b|Chunk=SSoT|Search-on-Graph")


def _texts(spec: dict):
    """(위치, 문장) 쌍. 도구 설명과 스키마 안의 모든 description."""
    yield f"{spec['name']}:description", spec["description"]

    def walk(node, path):
        if isinstance(node, dict):
            if isinstance(node.get("description"), str):
                yield f"{spec['name']}:{path or '$'}", node["description"]
            for key, value in node.items():
                if key != "description":
                    yield from walk(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for i, value in enumerate(node):
                yield from walk(value, f"{path}[{i}]")

    yield from walk(spec.get("inputSchema") or {}, "inputSchema")
    yield from walk(spec.get("outputSchema") or {}, "outputSchema")


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_served_text_has_no_em_or_en_dash(profile):
    bad = [(where, ch) for spec in PROFILES[profile]()
           for where, text in _texts(spec) for ch in DASHES if ch in text]
    assert not bad, f"{profile}: 대시가 남았다 {bad[:10]}"


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_served_text_has_no_internal_codes_warning_sign_or_korean(profile):
    bad = []
    for spec in PROFILES[profile]():
        for where, text in _texts(spec):
            if "⚠" in text:
                bad.append((where, "⚠"))
            if re.search(r"[가-힣]", text):
                bad.append((where, "korean"))
            bad += [(where, m.group(0)) for m in INTERNAL.finditer(text)]
    assert not bad, f"{profile}: 내부 표기가 호스트 문장에 남았다 {bad[:10]}"


def test_the_walk_reaches_nested_parameter_text():
    """검사기가 중첩 스키마를 실제로 걷는가. 안 걸으면 위 두 검사는 빈 통과다."""
    ingest = next(s for s in tools.specs(hub=True) if s["name"] == "vinc_ingest")
    places = {where for where, _ in _texts(ingest)}
    assert "vinc_ingest:inputSchema.properties.nodes.items.properties.valid_until" in places
    assert "vinc_ingest:inputSchema.properties.edges.items.properties.evidence" in places
