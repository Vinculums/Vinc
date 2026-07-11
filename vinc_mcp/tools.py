# -*- coding: utf-8 -*-
"""MCP tool 계약(D11 §4) — 이름·스키마·사이드카 매핑의 단일 출처.

읽기 5(search·recall·graph·decide·domains) + 보조 1(suggest_ties) + 쓰기 1
(ingest — 단일 write 표면). 핸들러는 계약 검증 후 브리지 호출뿐(스켈레톤 §4).
owner 미지정 시 브리지 기본 owner(user:local)를 주입한다 — MCP 호스트는
로컬 소유자 개념을 모른다.
"""
from __future__ import annotations

from .bridge import Bridge

_FRAGMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "nodes": {
            "type": "array",
            "description": "Nodes to MERGE by exact id (reuse ids found via vinc_search/vinc_recall first — read-before-write).",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Canonical MERGE key, type-prefixed (e.g. concept:margiela)."},
                    "type": {"type": "string", "enum": ["record", "concept", "domain", "bridge", "episode"]},
                    "title": {"type": "string"},
                    "domain": {"type": "string", "description": "Free-form domain string (no fixed list)."},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "props": {"type": "object", "description": "Free-form extension properties."},
                },
                "required": ["id", "type"],
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                    "type": {"type": "string",
                             "enum": ["relates_to", "prerequisite_of", "derives_from",
                                      "inherits_from", "connects_via"]},
                },
                "required": ["from", "to", "type"],
            },
        },
        "owner": {"type": "string", "description": "Owner scope to stamp (defaults to the local owner)."},
    },
    "required": ["nodes"],
}


def _owner(args: dict, bridge: Bridge) -> str:
    return args.get("owner") or bridge.owner


def _t_search(args, bridge):
    return bridge.request("GET", "/search", params={
        "query": args["query"], "top_k": args.get("limit", 20)})


def _t_recall(args, bridge):
    return bridge.request("GET", "/recall", params={
        "owner": _owner(args, bridge), "domain": args.get("domain"),
        "tags": ",".join(args["tags"]) if args.get("tags") else None,
        "limit": args.get("limit", 50)})


def _t_graph(args, bridge):
    return bridge.request("GET", "/graph", params={
        "owner": _owner(args, bridge), "seed": args["seed_id"],
        "depth": args.get("depth", 2)})


def _t_decide(args, bridge):
    return bridge.request("GET", "/decide", params={
        "topic": args["topic"], "depth": args.get("depth", 2)})


def _t_domains(args, bridge):
    return bridge.request("GET", "/domains", params={"owner": _owner(args, bridge)})


def _t_suggest(args, bridge):
    return bridge.request("GET", "/suggest-ties", params={
        "owner": _owner(args, bridge), "node": args["node"],
        "limit": args.get("limit", 5)})


def _t_ingest(args, bridge):
    fragment = {"nodes": args.get("nodes") or [], "edges": args.get("edges") or [],
                "owner": _owner(args, bridge)}
    return bridge.request("POST", "/ingest/fragment", body=fragment)


TOOLS: list[dict] = [
    {
        "name": "vinc_search",
        "description": "Hybrid full-text + vector search over the local Vinc store. "
                       "Call this before vinc_ingest to find existing node ids to reuse.",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 20},
        }, "required": ["query"]},
        "run": _t_search,
    },
    {
        "name": "vinc_recall",
        "description": "List recent episodes in an owner scope, optionally narrowed by domain or tags.",
        "inputSchema": {"type": "object", "properties": {
            "owner": {"type": "string"},
            "domain": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "integer", "default": 50},
        }}, "run": _t_recall,
    },
    {
        "name": "vinc_graph",
        "description": "Subgraph around a seed node (seed + depth traversal) — nodes and edges.",
        "inputSchema": {"type": "object", "properties": {
            "seed_id": {"type": "string"},
            "depth": {"type": "integer", "default": 2, "minimum": 1, "maximum": 4},
            "owner": {"type": "string"},
        }, "required": ["seed_id"]}, "run": _t_graph,
    },
    {
        "name": "vinc_decide",
        "description": "Deterministic decision-support traversal for a topic (nodes, edges, conflicts).",
        "inputSchema": {"type": "object", "properties": {
            "topic": {"type": "string"},
            "depth": {"type": "integer", "default": 2},
        }, "required": ["topic"]}, "run": _t_decide,
    },
    {
        "name": "vinc_domains",
        "description": "List the owner's domains discovered from data (open set, not a fixed list).",
        "inputSchema": {"type": "object", "properties": {
            "owner": {"type": "string"},
        }}, "run": _t_domains,
    },
    {
        "name": "vinc_suggest_ties",
        "description": "Suggest link candidates for a node by tag/domain proximity (read-only).",
        "inputSchema": {"type": "object", "properties": {
            "node": {"type": "string"},
            "limit": {"type": "integer", "default": 5},
            "owner": {"type": "string"},
        }, "required": ["node"]}, "run": _t_suggest,
    },
    {
        "name": "vinc_ingest",
        "description": "MERGE an authored graph fragment (nodes + edges) into the store by exact id. "
                       "The only write surface. Reuse existing ids (read-before-write); unknown "
                       "type values are skipped and reported in warnings.",
        "inputSchema": _FRAGMENT_SCHEMA,
        "run": _t_ingest,
    },
]

_BY_NAME = {t["name"]: t for t in TOOLS}


def specs() -> list[dict]:
    return [{"name": t["name"], "description": t["description"],
             "inputSchema": t["inputSchema"]} for t in TOOLS]


def call(name: str, args: dict | None, bridge: Bridge) -> dict:
    tool = _BY_NAME.get(name)
    if tool is None:
        raise KeyError(f"unknown tool: {name}")
    args = args or {}
    for req in tool["inputSchema"].get("required", []):
        if args.get(req) in (None, ""):
            raise ValueError(f"missing required argument: {req}")
    return tool["run"](args, bridge)
