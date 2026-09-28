from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest

RO_KEY = "vinc_ro_" + "a" * 32
SK_KEY = "vinc_sk_" + "b" * 32
TEAM = "01a0bc92-ce1d-7867-b970-72cffed2dd89"


class Recorder:
    """Records every request and answers from a handler keyed by (method, path)."""

    def __init__(self, routes: dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]]):
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        handler = self.routes.get((request.method, request.url.path))
        if handler is None:
            return httpx.Response(404, json={"error": {"code": "route_not_found",
                                                       "message": "No such v1 route."}})
        return handler(request)

    def body(self, index: int = -1) -> dict[str, Any]:
        return json.loads(self.requests[index].content or b"{}")


def ok(payload: dict[str, Any]) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _request: httpx.Response(200, json=payload)


def fail(status: int, code: str, message: str = "", *, details: dict | None = None,
         headers: dict | None = None) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _request: httpx.Response(
        status, headers=headers,
        json={"error": {"code": code, "message": message, "request_id": "req_test",
                        "details": details or {}}})


RESOLVED = {
    "status": "resolved",
    "node": {"id": "decision:tokens-are-oklch", "title": "Colour tokens are stored as OKLCH",
             "domain": "vinc/design/tokens", "validity": "current"},
    "one_liner": "Tokens keep OKLCH so contrast can be checked per theme.",
    "grounds": ["decision:tokens-are-oklch", "record:contrast-audit"],
    "excerpts": [{"doc_id": "vinc.design.tokens", "doc_title": "Design tokens",
                  "section": "Colour", "excerpt": "Every colour token is OKLCH."}],
    "links": {"items": [{"id": "record:contrast-audit", "title": "Contrast audit",
                         "type": "ATTACHES_TO", "dir": "att",
                         "evidence_text": "The audit measured every pair."}]},
}


@pytest.fixture
def env_clean(monkeypatch):
    monkeypatch.delenv("VINC_API_KEY", raising=False)
    monkeypatch.delenv("VINC_API_URL", raising=False)
