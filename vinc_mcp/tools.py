# -*- coding: utf-8 -*-
"""MCP tool 핸들러 — 계약 검증 → bridge 호출 → 감사 로그 ([[vinc.mcp.skeleton]] §4·§6).

비즈니스 로직 0: 의미 판단·구조화·dedup 은 전부 상류(Claude)와 하위(엔진)에 있다.
이 파일은 [[vinc.mcp#§5]] **형태 검증(필수 키만 — 타입 enum 아님)** + 사이드카 HTTP
호출 + 로그뿐. **타입은 전역 enum 으로 검증하지 않는다**(THESIS T3) — 타입 어휘는
주제별 사용자 저작물이라 어휘 조회·편입은 서버 측(themes.json 에 닿는 곳)에서만
일어난다(MCP 프로세스는 HTTP bridge 만 가짐). 여기서는 type 문자열을 통과시킨다.

**스토어별 단일 write**: 그래프 쓰기는 `vinc_ingest` 하나, 어휘 쓰기는 `vinc_types_*`
하나로 분리 — 두 write 가 서로 다른 저장소(그래프 vs 주제 메타)를 건드려 감사 경계가
또렷하다.

**읽기 4종의 실제 파라미터**는 [[vinc.mcp.skeleton]] §4 원안(`limit`/`tag`/`seed`)과
다르다 — `api/server.py` 가 실제로 노출하는 이름(`top_k`/`tags`/owner 스코프)에
맞췄다. `vinc_graph` 는 seed+depth 순회가 `api/query.py` 에 아직 없어 미구현 응답.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from . import audit
from .bridge import SidecarBridge


def validate_fragment_shape(fragment: dict) -> tuple[dict, list[str]]:
    """[[vinc.mcp#§5]] **형태** 검증 — id/title 필수, 엣지 endpoint 선언 확인.
    **타입 enum 검증 없음**: 어휘 밖 타입도 통과시킨다(서버가 어휘에 proposed 로
    편입 — engines/fragment_ingest.py). 반환: (정제된 fragment, 이 단계 warnings)."""
    warnings: list[str] = []
    nodes_in = fragment.get("nodes") or []
    edges_in = fragment.get("edges") or []
    ok_ids: set[str] = set()

    nodes_out = []
    for n in nodes_in:
        nid, title = n.get("id"), n.get("title")
        if not nid or not isinstance(nid, str):
            warnings.append(f"node missing/invalid id: {n!r}")
            continue
        if not title or not isinstance(title, str):
            warnings.append(f"node.title required: id={nid}")
            continue
        ok_ids.add(nid)
        nodes_out.append(n)

    edges_out = []
    for e in edges_in:
        efrom, eto = e.get("from"), e.get("to")
        if efrom not in ok_ids or eto not in ok_ids:
            warnings.append(f"edge endpoint not in valid node set: {efrom!r} -> {eto!r}")
            continue
        edges_out.append(e)

    out = {"nodes": nodes_out, "edges": edges_out}
    if fragment.get("owner"):
        out["owner"] = fragment["owner"]
    return out, warnings


class VincTools:
    """`bridge`(SidecarBridge)를 주입받는 tool 표면. 도구별 호출을 §6 로그로 남긴다."""

    def __init__(self, bridge: SidecarBridge, *, data_dir: str, session_id: str):
        self._bridge = bridge
        self._data_dir = data_dir
        self._session = session_id

    def _call(self, tool: str, args: dict, fn) -> dict:
        start = time.monotonic()
        result = fn()
        duration_ms = (time.monotonic() - start) * 1000
        audit.record(self._data_dir, tool=tool, source="mcp", session=self._session,
                     args=args, result=result, warnings=result.get("warnings"),
                     duration_ms=duration_ms, ts=datetime.now(timezone.utc).isoformat())
        return result

    def vinc_search(self, query: str, limit: int = 20, domain: str | None = None) -> dict:
        args = {"query": query, "limit": limit, "domain": domain}
        return self._call("vinc_search", args, lambda: self._bridge.get(
            "/search", {"query": query, "top_k": limit, "domain": domain}))

    def vinc_recall(self, domain: str | None = None, tag: str | None = None,
                    owner: str | None = None, limit: int = 50) -> dict:
        args = {"domain": domain, "tag": tag, "owner": owner, "limit": limit}
        return self._call("vinc_recall", args, lambda: self._bridge.get(
            "/recall", {"domain": domain, "tags": tag, "owner": owner, "limit": limit}))

    def vinc_graph(self, seed_id: str, depth: int = 2) -> dict:
        args = {"seed_id": seed_id, "depth": depth}
        return self._call("vinc_graph", args, lambda: {
            "nodes": [], "edges": [], "executed": False,
            "warnings": ["vinc_graph not yet backed by a seed-based traversal query "
                         "— api/query.py has no seed+depth subgraph plan (후속)"],
        })

    def vinc_decide(self, topic: str, depth: int = 2) -> dict:
        args = {"topic": topic, "depth": depth}
        return self._call("vinc_decide", args, lambda: self._bridge.get(
            "/decide", {"topic": topic, "depth": depth}))

    def vinc_ingest(self, fragment: dict, theme_id: str) -> dict:
        args = {"theme_id": theme_id, "fragment": fragment}
        # theme_id 필수(D11-R2 §3) — 없으면 bridge 호출 없이 즉시 reject(클라 가드).
        if not theme_id or not isinstance(theme_id, str):
            return self._call("vinc_ingest", args, lambda: {
                "executed": False, "rejected": True,
                "warnings": ["theme_id required — ingest rejected (client guard)"]})

        validated, pre_warnings = validate_fragment_shape(fragment)
        validated["theme_id"] = theme_id

        def _do():
            res = self._bridge.post("/ingest", validated)
            res["warnings"] = pre_warnings + list(res.get("warnings") or [])
            return res

        return self._call("vinc_ingest", args, _do)

    # ── 주제 CRUD (headless — GUI 이전 ingest 가 유효 대상 주제를 갖게) ──────────
    def vinc_themes_list(self) -> dict:
        return self._call("vinc_themes_list", {}, lambda: self._bridge.get("/themes", {}))

    def vinc_themes_create(self, theme_id: str, title: str = "",
                           guiding_questions: list[str] | None = None) -> dict:
        body = {"theme_id": theme_id, "title": title,
                "guiding_questions": list(guiding_questions or [])}
        return self._call("vinc_themes_create", body, lambda: self._bridge.post("/themes", body))

    def vinc_themes_activate(self, theme_id: str) -> dict:
        body = {"theme_id": theme_id}
        return self._call("vinc_themes_activate", body,
                          lambda: self._bridge.post("/themes/activate", body))

    # ── 타입 어휘 (읽기 1 / 쓰기 3 — 쓰기는 사용자 승인 전제, 호스트 규약) ────────
    def vinc_types_list(self, theme_id: str, status: str | None = None) -> dict:
        params = {"theme_id": theme_id, "status": status}
        return self._call("vinc_types_list", params, lambda: self._bridge.get("/types", params))

    def vinc_types_ratify(self, theme_id: str, name: str) -> dict:
        body = {"theme_id": theme_id, "name": name}
        return self._call("vinc_types_ratify", body, lambda: self._bridge.post("/types/ratify", body))

    def vinc_types_reject(self, theme_id: str, name: str) -> dict:
        body = {"theme_id": theme_id, "name": name}
        return self._call("vinc_types_reject", body, lambda: self._bridge.post("/types/reject", body))

    def vinc_types_rename(self, theme_id: str, from_name: str, to_name: str) -> dict:
        body = {"theme_id": theme_id, "from_name": from_name, "to_name": to_name}
        return self._call("vinc_types_rename", body, lambda: self._bridge.post("/types/rename", body))
