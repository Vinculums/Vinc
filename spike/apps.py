# -*- coding: utf-8 -*-
"""W11 스파이크: MCP Apps(SEP-1865, 확장 id `io.modelcontextprotocol/ui`) 최소 구현.

**병합되지 않는다.** 판정 재료 두 가지만 만든다:
  (1) 이 손수 만든 stdlib 서버가 계약을 답할 수 있는가 · 비용은 얼마인가
  (2) 호스트가 실제로 렌더하고 왕복하는가

문자열은 스펙 실측치다(2026-08-29 확인, ext-apps `specification/draft/apps.mdx`):
  · mimeType 은 정확히 `text/html;profile=mcp-app` — `text/html+skybridge` 는
    OpenAI 레거시라 표준 호스트가 못 알아본다.
  · 결합은 **도구 정의**의 `_meta.ui.resourceUri` 하나다. 도구 **결과**에는
    결합 정보가 없다(결과는 데이터만 싣는다).
  · 서버가 선언할 확장 capability 는 **없다**. 확장은 클라이언트가 initialize 에서
    선언하고, 서버는 평범한 `resources` 만 있으면 된다.
"""
from __future__ import annotations

import os

UI_URI = "ui://vinc/neighbors"
UI_MIME = "text/html;profile=mcp-app"
UI_TOOL = "vinc_neighbors"

# 확장 id — 클라이언트가 initialize.capabilities.extensions 에 싣는 키.
UI_EXT = "io.modelcontextprotocol/ui"

_HTML_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "vinc_mcp", "ui", "neighbors.html")


def ui_html() -> str:
    with open(_HTML_PATH, encoding="utf-8") as f:
        return f.read()


def resource_descriptor() -> dict:
    return {"uri": UI_URI, "name": "Vinc neighbors", "mimeType": UI_MIME,
            "description": "Neighbor ring for one node, with click-to-call."}


def tool_meta() -> dict:
    # 표준 키 + 폐기예정 평면 키를 **둘 다** 싣는다 — 현장의 호스트 일부가
    # 평면 키만 읽는다(공식 SDK 의 registerAppTool 도 둘 다 낸다).
    return {"ui": {"resourceUri": UI_URI, "visibility": ["model", "app"]},
            "ui/resourceUri": UI_URI}


def install(dispatcher) -> None:
    """Dispatcher 에 resources 면을 **런타임에** 덧댄다 — 원본 파일을 안 고친다.

    비용 추정치가 '원본을 얼마나 고쳐야 하는가'인데 원본을 고치면 그 측정이
    오염된다. 여기 얹힌 코드량이 곧 그 추정치다.
    """
    inner = dispatcher._dispatch
    seen: dict = {}

    def patched(method, params, **kw):
        if method == "resources/list":
            return {"resources": [resource_descriptor()]}
        if method == "resources/read":
            uri = params.get("uri")
            if uri != UI_URI:
                raise ValueError(f"unknown resource: {uri}")
            return {"contents": [{"uri": UI_URI, "mimeType": UI_MIME,
                                  "text": ui_html()}]}
        out = inner(method, params, **kw)
        if method == "initialize":
            out["capabilities"]["resources"] = {"listChanged": False}
            # 클라이언트가 확장을 선언했는지 기록해 둔다(스펙: 서버는 SHOULD 확인).
            seen["ext"] = ((params.get("capabilities") or {})
                           .get("extensions") or {}).get(UI_EXT)
            seen["client"] = params.get("clientInfo")
        elif method == "tools/list":
            for t in out["tools"]:
                if t["name"] == UI_TOOL:
                    t["_meta"] = tool_meta()
        return out

    dispatcher._dispatch = patched
    dispatcher.w11_seen = seen
