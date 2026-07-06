# -*- coding: utf-8 -*-
"""MCP 5 tool 핸들러 — 계약 검증 → bridge 호출 → 감사 로그 ([[vinc.mcp.skeleton]] §4·§6).

비즈니스 로직 0: 의미 판단·구조화·dedup 은 전부 상류(Claude)와 하위(엔진)에 있다.
이 파일은 [[vinc.mcp#§5]] 형태 검증(필수 키·enum) + 사이드카 HTTP 호출 + 로그뿐.

**읽기 4종의 실제 파라미터**는 [[vinc.mcp.skeleton]] §4 원안(`limit`/`tag`/`seed`)과
다르다 — `api/server.py` 가 실제로 노출하는 이름(`top_k`/`tags`/owner 스코프)에
맞췄다(문서 델타는 스켈레톤 §4 갱신 참조). `vinc_graph` 는 D11 이 요구하는
seed+depth 서브그래프 순회가 `api/query.py` 에 아직 없어(기존 `/graph` 는 owner
전체 그래프) **미구현으로 명시 응답**한다 — owner=seed_id 로 갈음하는 오매핑을
피한다(후속: 쿼리 계층에 순회 플랜 추가).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from . import audit
from .bridge import SidecarBridge

_VALID_NODE_TYPES = {"record", "concept", "domain", "bridge", "episode"}
_VALID_EDGE_TYPES = {"relates_to", "prerequisite_of", "derives_from", "inherits_from", "connects_via"}


def validate_fragment(fragment: dict) -> tuple[dict, list[str]]:
    """[[vinc.mcp#§5]] 형태 검증 — enum 밖·필수 키 누락 항목은 스킵 + warnings.
    (사이드카가 실제 스키마 대비로 한 번 더 걸러 자체 warnings 를 얹는다 —
    engines/fragment_ingest.py.) 반환: (정제된 fragment, 이 단계 warnings)."""
    warnings: list[str] = []
    nodes_in = fragment.get("nodes") or []
    edges_in = fragment.get("edges") or []
    ok_ids: set[str] = set()

    nodes_out = []
    for n in nodes_in:
        nid, ntype, title = n.get("id"), n.get("type"), n.get("title")
        if not nid or not isinstance(nid, str):
            warnings.append(f"node missing/invalid id: {n!r}")
            continue
        if ntype not in _VALID_NODE_TYPES:
            warnings.append(f"node.type out of enum: {ntype!r} (id={nid})")
            continue
        if not title or not isinstance(title, str):
            warnings.append(f"node.title required: id={nid}")
            continue
        ok_ids.add(nid)
        nodes_out.append(n)

    edges_out = []
    for e in edges_in:
        efrom, eto, etype = e.get("from"), e.get("to"), e.get("type")
        if etype not in _VALID_EDGE_TYPES:
            warnings.append(f"edge.type out of enum: {etype!r}")
            continue
        if efrom not in ok_ids or eto not in ok_ids:
            warnings.append(f"edge endpoint not in valid node set: {efrom!r} -> {eto!r}")
            continue
        edges_out.append(e)

    out = {"nodes": nodes_out, "edges": edges_out}
    if fragment.get("owner"):
        out["owner"] = fragment["owner"]
    return out, warnings


class VincTools:
    """`bridge`(SidecarBridge)를 주입받는 5 tool 표면. 도구별 호출을 §6 로그로 남긴다."""

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

    def vinc_ingest(self, fragment: dict) -> dict:
        validated, pre_warnings = validate_fragment(fragment)

        def _do():
            res = self._bridge.post("/ingest", validated)
            res["warnings"] = pre_warnings + list(res.get("warnings") or [])
            return res

        return self._call("vinc_ingest", fragment, _do)
