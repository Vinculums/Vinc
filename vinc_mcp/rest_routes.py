"""Public v1 route registry. The MCP tool schemas remain the argument canon."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re
from urllib.parse import unquote


@lru_cache(maxsize=None)
def _pattern(path: str) -> re.Pattern:
    # 인그레스가 %2F를 먼저 풀어도 노드 주소의 슬래시는 그대로 노드 id에 속한다.
    # 도구 접미사는 find_route가 긴 고정 경로부터 골라 주소 조회에 삼키지 않는다.
    def capture(match):
        field = match.group(1)
        value = ".+?" if field == "id" and path.startswith("/v1/nodes/") else "[^/]+"
        return f"(?P<{field}>{value})"
    return re.compile(re.sub(r"\{([a-z_]+)\}", capture, path))


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    tool: str
    query: tuple[str, ...] = ()
    body: tuple[str, ...] = ()
    path_args: tuple[tuple[str, str], ...] = ()
    success: int = 200

    def match(self, method: str, path: str) -> dict[str, str] | None:
        if method != self.method:
            return None
        found = _pattern(self.path).fullmatch(path)
        return {key: unquote(value) for key, value in found.groupdict().items()} if found else None


READ_ROUTES = (
    Route("GET", "/v1/me", "vinc_whoami"),
    Route("GET", "/v1/conventions", "vinc_conventions", query=("lang",)),
    Route("GET", "/v1/domains", "vinc_domains"),
    Route("POST", "/v1/search", "vinc_search",
          body=("query", "limit", "cursor", "detail", "historical", "as_of")),
    Route("POST", "/v1/brief", "vinc_brief",
          body=("query", "max_excerpts", "historical", "as_of")),
    Route("GET", "/v1/nodes/{id}", "vinc_node", path_args=(("id", "node_id"),)),
    Route("GET", "/v1/nodes/{id}/neighbors", "vinc_neighbors",
          query=("limit",), path_args=(("id", "node"),)),
    Route("GET", "/v1/nodes/{id}/graph", "vinc_graph",
          query=("depth", "select"), path_args=(("id", "seed_id"),)),
    Route("POST", "/v1/graph", "vinc_graph", body=("url", "depth", "select")),
    Route("GET", "/v1/nodes/{id}/tie-suggestions", "vinc_suggest_ties",
          query=("limit",), path_args=(("id", "node"),)),
    Route("GET", "/v1/path", "vinc_path",
          query=("a", "b", "max_depth", "weight")),
    Route("GET", "/v1/episodes", "vinc_recall",
          query=("domain", "tags", "limit", "cursor")),
    Route("GET", "/v1/decide", "vinc_decide", query=("topic", "depth")),
    Route("GET", "/v1/diagnose", "vinc_diagnose",
          query=("kind", "node", "member_limit")),
    Route("GET", "/v1/merge-candidates", "vinc_merge_candidates",
          query=("limit", "include_structural", "min_score")),
    Route("GET", "/v1/merge-rejections", "vinc_merge_rejections"),
    Route("GET", "/v1/documents", "vinc_doc_list",
          query=("limit", "cursor", "detail")),
    Route("GET", "/v1/documents/{doc_id}", "vinc_doc_get",
          path_args=(("doc_id", "doc_id"),)),
)

# A2 adds editor/admin routes here. Search/fetch belong to a host directory,
# while every other public MCP tool must have a route.
WRITE_ROUTES = (
    Route("POST", "/v1/merge-rejections", "vinc_merge_reject",
          body=("a", "b", "note", "undo")),
    Route("POST", "/v1/fragments", "vinc_ingest", body=("nodes", "edges")),
    Route("POST", "/v1/nodes/merge", "vinc_merge_nodes",
          body=("keep_id", "drop_id")),
    Route("DELETE", "/v1/nodes/{id}", "vinc_delete",
          path_args=(("id", "node_id"),)),
    Route("DELETE", "/v1/edges", "vinc_delete_edge",
          query=("from_id", "to_id", "type")),
    Route("PUT", "/v1/documents/{doc_id}", "vinc_doc_put",
          body=("text", "title", "origin", "topic"),
          path_args=(("doc_id", "doc_id"),)),
    Route("POST", "/v1/documents", "vinc_doc_put",
          body=("text", "title", "origin", "topic"), success=201),
    Route("DELETE", "/v1/documents/{doc_id}", "vinc_doc_delete",
          path_args=(("doc_id", "doc_id"),)),
    Route("POST", "/v1/documents/{doc_id}/extract", "vinc_doc_extract",
          body=("apply", "candidates"), path_args=(("doc_id", "doc_id"),)),
)
ROUTES = READ_ROUTES + WRITE_ROUTES
HOST_EXCLUSIONS = frozenset({"search", "fetch"})


def find_route(method: str, path: str) -> tuple[Route, dict[str, str]] | None:
    # /nodes/{id}/neighbors 같은 고정 접미사는 /nodes/{id}보다 구체적이다.
    for route in sorted(ROUTES, key=lambda r: len(re.sub(r"\{[^}]+\}", "", r.path)),
                        reverse=True):
        fields = route.match(method, path)
        if fields is not None:
            return route, fields
    return None


def allowed_methods(path: str) -> set[str]:
    return {route.method for route in ROUTES if route.match(route.method, path) is not None}
