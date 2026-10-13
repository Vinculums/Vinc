"""Small authenticated reads; output contains counts and contract flags only."""
from __future__ import annotations

import json
import os
from urllib.parse import urlencode
import urllib.error
import urllib.request

from verify_layout_remote import BASE, NoRedirect, VerificationError

FIELDS = {"created_at", "updated_at", "content_time", "valid_from", "valid_until",
          "superseded_by", "validity", "supersession", "supersedes", "claim_status", "conflicts"}


def _json_read(request, timeout):
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            data = response.read(8 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        raise VerificationError(f"search read returned HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError):
        raise VerificationError("search read could not complete") from None
    if len(data) > 8 * 1024 * 1024:
        raise VerificationError("search reply exceeded the verification byte limit")
    try:
        return json.loads(data)
    except ValueError:
        raise VerificationError("search reply was not JSON") from None


def read(path, key, timeout):
    # Search keeps its executed flag; a successful empty search is still executed.
    return _json_read(urllib.request.Request(BASE + path, headers={
        "Authorization": "Bearer " + key, "Cache-Control": "no-store"}), timeout)


def read_mcp(options, key, timeout):
    request = urllib.request.Request("https://mcp.vincs.io/mcp", data=json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "vinc_search", "arguments": {"query": "vinc", "limit": 1, **options}}}).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                 "Accept": "application/json", "Cache-Control": "no-store"})
    envelope = _json_read(request, timeout)
    if not isinstance(envelope, dict):
        raise VerificationError("MCP search envelope was invalid")
    result = envelope.get("result") or {}
    if not isinstance(result, dict):
        raise VerificationError("MCP search result was invalid")
    if envelope.get("error") or result.get("isError"):
        raise VerificationError("MCP search read returned an error")
    for part in result.get("content") or []:
        if part.get("type") == "text":
            try:
                return json.loads(part["text"])
            except (KeyError, ValueError):
                break
    raise VerificationError("MCP search reply did not contain a result")


def verify(key: str, *, get=read, mcp_key=None, mcp_get=read_mcp) -> dict:
    if not key:
        raise VerificationError("VINC_GRAPH_READONLY_KEY is absent")
    checks = []
    transports = [("account", lambda opts: get("/search?" + urlencode({
        "query": "vinc", "top_k": 1, **opts}), key, 30))]
    if mcp_key:
        transports.append(("mcp", lambda opts: mcp_get(opts, mcp_key, 30)))
    for transport, fetch in transports:
        for options in ({}, {"mode": "general"}, {"mode": "current"},
                        {"mode": "historical", "detail": "full"},
                        {"mode": "historical", "as_of": "2020", "known_at": "2025"}):
            result = fetch(options)
            if isinstance(result, dict) and result.get("executed") is not True:
                # Only bounded stable codes, never error detail, owner, or returned content.
                known_codes = {"index_missing", "extension_unavailable", "index_build_failed",
                               "owner_scope_unsupported", "fts_index_remnant", "store_busy",
                               "store_unavailable", "no reachable store"}
                prefix = str(result.get("error") or "").split(":", 1)[0]
                code = prefix if prefix in known_codes else "unclassified"
                raise VerificationError(f"{transport} search was not executed; error_code={code}; "
                                        f"error_present={bool(result.get('error'))}; "
                                        f"node_error_present={bool(result.get('node_error'))}")
            if (not isinstance(result, dict) or result.get("kind") != "search"
                    or result.get("error") or result.get("node_error")
                    or not isinstance(result.get("rows"), list)
                    or not isinstance(result.get("node_hits"), list)):
                raise VerificationError(f"{transport} search did not return a valid search result")
            if options.get("mode") and result.get("mode") != options["mode"]:
                raise VerificationError("explicit temporal mode was not forwarded")
            items = result["rows"] + result["node_hits"]
            for row in items:
                temporal = row.get("temporal")
                if not isinstance(temporal, dict) or not FIELDS <= temporal.keys():
                    raise VerificationError("search temporal metadata is incomplete")
                if options.get("mode") == "current" and temporal["validity"] in ("future", "historical"):
                    raise VerificationError("current search returned outside current validity")
            if result.get("temporal_error"):
                raise VerificationError("temporal metadata read was partial")
            checks.append({"transport": transport, "mode": options.get("mode", "legacy"),
                           "items_checked": len(items), "time_scope_requested": "as_of" in options,
                           "semantic_degraded": bool(result.get("vector_note")), "contract": "pass"})
    if not any(c["items_checked"] for c in checks):
        raise VerificationError("no evidence metadata was available to verify")
    return {"search_temporal": "pass", "checks": checks, "content_logged": False}


if __name__ == "__main__":
    try:
        print(json.dumps(verify(os.environ.get("VINC_GRAPH_READONLY_KEY", ""),
                                mcp_key=os.environ.get("VINC_MCP_TOKEN")), sort_keys=True))
    except VerificationError as exc:
        raise SystemExit(str(exc))
