"""개인 그래프 전수 후보를 동결한다. 허브에는 읽기 요청만 보낸다.

근거: [[vinc.plan.laya-test-method-2026-10-07 §6]]
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from itertools import combinations
import json
import os
from pathlib import Path
import random
import time
import urllib.error
import urllib.parse
import urllib.request

from .candidates import ID_RE, KINDS, MIN_TEXT, node_text
from .pins import SEED, detect_lang


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


class ReadHub:
    """성공한 읽기만 캐시한다. 오류·잘린 목록을 빈 결과로 바꾸지 않는다."""
    def __init__(self, base, token, cache):
        self.base, self.token, self.cache = base.rstrip("/"), token, cache
        cache.mkdir(parents=True, exist_ok=True)
        self.calls = 0

    def request(self, path, body=None):
        if not (path.startswith("/v1/") and (body is None or path == "/v1/search")):
            raise ValueError("read-only routes required")
        key = digest([self.base, path, body])
        file = self.cache / (key + ".json")
        if file.exists():
            cached = json.loads(file.read_text(encoding="utf-8"))["response"]
            if cached.get("http_status") != 404 or cached.get("confirmed_not_found"):
                return cached
        for attempt in range(6):
            try:
                req = urllib.request.Request(self.base + path,
                    data=json.dumps(body).encode() if body is not None else None,
                    headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=60) as response:
                    result = json.load(response)
                if result.get("error") or result.get("executed") is False:
                    raise RuntimeError(str(result.get("error") or result.get("note")))
                write_json(file, {"path": path, "body": body, "read_at": time.time(), "response": result})
                self.calls += 1
                return result
            except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
                if isinstance(exc, urllib.error.HTTPError) and exc.code == 404 and path.startswith("/v1/nodes/"):
                    if attempt < 2:
                        time.sleep(1 + attempt)
                        continue
                    result = {"found": False, "http_status": 404, "confirmed_not_found": True}
                    write_json(file, {"path": path, "body": body, "read_at": time.time(), "response": result})
                    return result
                if isinstance(exc, urllib.error.HTTPError) and exc.code in (400, 401, 403):
                    raise RuntimeError(f"read refused: {path}: HTTP {exc.code}") from exc
                if attempt == 5:
                    raise RuntimeError(f"read failed: {path}: {type(exc).__name__}") from exc
                time.sleep(min(10, 1 + attempt * 2))

    def node(self, nid):
        return self.request("/v1/nodes/" + urllib.parse.quote(nid, safe=":"))

    def neighbors(self, nid, *, allow_missing=False):
        result = self.request("/v1/nodes/" + urllib.parse.quote(nid, safe=":") + "/neighbors?limit=500")
        if allow_missing and result.get("http_status") == 404:
            return []
        rows = result.get("neighbors", [])
        if "count" not in result or result.get("neighbors_truncated") or len(rows) != result["count"]:
            raise ValueError(f"incomplete neighbors: {nid}")
        return rows


def census(hub):
    domains = hub.request("/v1/domains")["rows"]
    members, checks = {}, []
    for domain in sorted(domains, key=lambda x: x["domain"]):
        expected = sum(domain.get(k, 0) for k in ("concepts", "records", "decisions"))
        if not expected:
            continue
        if domain["domain"] == "(none)":
            raise ValueError("candidate nodes without a domain cannot be enumerated by this API")
        rows = [r for r in hub.neighbors("domain:" + domain["domain"])
                if r["rel"] == "IN_DOMAIN" and r["id"].split(":")[0] in KINDS]
        checks.append({"domain": domain["domain"], "expected": expected, "observed": len(rows)})
        members.update((r["id"], r) for r in rows)
    # domain 열과 IN_DOMAIN 엣지가 어긋난 문서 미러는 문서 목록으로 회수한다.
    path = "/v1/documents?limit=100&detail=summary"
    while path:
        page = hub.request(path)
        for doc in page["docs"]:
            nid = "concept:doc." + doc["doc_id"]
            if nid not in members:
                result = hub.node(nid)
                if result.get("found") and result.get("node"):
                    members[nid] = result["node"]
        path = ("/v1/documents?limit=100&detail=summary&cursor=" + urllib.parse.quote(page["next_cursor"], safe="")) if page.get("has_more") else None
    components = hub.request("/v1/diagnose?kind=components&member_limit=1000")
    for nid in sorted({n for c in components["components"] for n in c["members"]} - members.keys()):
        result = hub.node(nid)
        if result.get("found") and result.get("node"):
            members[nid] = result["node"]
    # 연결된 Record/Decision도 도메인 엣지가 빠질 수 있다. 알려진 개념 고리로 대조한다.
    for i, nid in enumerate(sorted(n for n in members if n.startswith("concept:"))):
        for row in hub.neighbors(nid, allow_missing=True):
            if row["id"].split(":")[0] in KINDS and row["id"] not in members:
                members[row["id"]] = row
        if (i + 1) % 100 == 0:
            print(f"census reconciliation concepts={i+1} nodes={len(members)}", flush=True)
    for check in checks:
        check["reconciled"] = sum(n.get("domain") == check["domain"] for n in members.values())
        if check["reconciled"] != check["expected"]:
            raise ValueError(f"domain census incomplete after document reconciliation: {check}")
    return domains, members, checks


def collect(hub, out):
    domains, members, checks = census(hub)
    print(f"census: {len(members)} candidate-kind nodes in {len(checks)} domains", flush=True)
    nodes, excluded, pairs, diagnostics = {}, [], {}, []
    for i, nid in enumerate(sorted(members)):
        response = hub.node(nid)
        node = response.get("node")
        if not node or not response.get("found"):
            excluded.append({"id": nid, "reason": "exact_node_read_unavailable", "http_status": response.get("http_status")})
            continue
        title = node.get("title") or ""
        minimum = MIN_TEXT // 2 if detect_lang(title) == "ko" else MIN_TEXT
        if len(title.strip()) < minimum:
            excluded.append({"id": nid, "reason": "short_title"})
            continue
        if type(node.get("rev")) is not int:
            raise ValueError(f"missing revision: {nid}")
        nodes[nid] = {"id": nid, "title": title, "text": node_text(node), "rev": node["rev"],
                      "kind": nid.split(":")[0], "domain": node.get("domain") or ""}
        if (i + 1) % 100 == 0:
            print(f"nodes {i+1}/{len(members)} calls={hub.calls}", flush=True)
    write_json(out / "nodes.json", list(nodes.values()))

    def add(a, b, source, context=None, context_id=None, **extra):
        if a == b or a not in nodes or b not in nodes:
            return
        a, b = sorted((a, b))
        key = (a, b)
        if key not in pairs:
            pairs[key] = {"pair_id": "fill-" + digest(key)[:20], "a": nodes[a], "b": nodes[b],
                          "lang": detect_lang(nodes[a]["text"], nodes[b]["text"]),
                          "sources": [], "context": None, "context_ids": [], "provenance": []}
        row = pairs[key]
        if source not in row["sources"]:
            row["sources"].append(source)
        if context and not row["context"]:
            row["context"] = context[:400]
            if context_id:
                row["context_ids"].append(context_id)
        row["provenance"].append({"generator": source, **extra})

    for i, (nid, node) in enumerate(sorted(nodes.items())):
        encoded = urllib.parse.quote(nid, safe=":")
        if node["kind"] == "concept":
            ties = hub.request(f"/v1/nodes/{encoded}/tie-suggestions?limit=5")
            for row in ties.get("suggestions", []):
                add(nid, row["id"], "ties", score=row.get("score"), formula=ties.get("formula"))
        res = hub.request("/v1/search", {"query": node["title"][:200], "limit": 6})
        diagnostics.append({"node": nid, "arms": res.get("arms"), "vector_note": res.get("vector_note")})
        hits = [r["id"] for r in res.get("node_hits", []) if r["id"] != nid and r["id"] in nodes][:5]
        for other in hits:
            add(nid, other, "search", seed=nid, arms=res.get("arms"))
        if len(hits) >= 2:
            add(hits[0], hits[1], "search", seed=nid, note="co-hit", arms=res.get("arms"))
        for row in res.get("rows", [])[:6]:
            snippet = row.get("snippet") or ""
            ids = sorted({x for x in ID_RE.findall(snippet) if x in nodes
                          and not snippet.rstrip("…. ").endswith(x)})[:6]
            for a, b in combinations(ids, 2):
                add(a, b, "comention", snippet, row.get("id"), method="snippet")
        # 개념마다 한 번씩만 shared-anchor 근사를 계산한다.
        if node["kind"] == "concept":
            about = sorted(r["id"] for r in hub.neighbors(nid)
                           if r["rel"] == "ABOUT" and r["id"] in nodes)[:6]
            for a, b in combinations(about, 2):
                add(a, b, "comention", node["text"], nid, method="shared-anchor")
        if (i + 1) % 25 == 0:
            print(f"generators {i+1}/{len(nodes)} pairs={len(pairs)} calls={hub.calls}", flush=True)
    merge = hub.request("/v1/merge-candidates?min_score=0.6&limit=100")
    if len(merge["candidates"]) != merge["total"]:
        raise ValueError("merge candidates exceed API limit; incomplete census refused")
    for row in merge["candidates"]:
        add(row["a_id"], row["b_id"], "merge", score=row["score"], method="merge-candidates")
    for nid in sorted(nodes):
        if nid.startswith("concept:doc.vinc.book.") and nid.endswith(".en"):
            add(nid[:-3], nid, "merge", method="book-mirror")
    rng = random.Random(SEED)
    ids, added = sorted(nodes), 0
    for _ in range(20000):
        if added == 200:
            break
        a, b = rng.sample(ids, 2)
        if nodes[a]["domain"].split("/")[:2] != nodes[b]["domain"].split("/")[:2]:
            key = tuple(sorted((a, b)))
            if key not in pairs:
                add(a, b, "random")
                added += 1
    if added != 200:
        raise ValueError("cannot construct the registered 200 random control pairs")
    rows = [pairs[k] for k in sorted(pairs)]
    with (out / "candidates.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    write_json(out / "search-diagnostics.json", diagnostics)
    summary = {"domains": domains, "census_checks": checks, "census_nodes": len(members),
               "eligible_nodes": len(nodes), "excluded_nodes": excluded, "pairs": len(rows),
               "by_generator": dict(Counter(s for r in rows for s in r["sources"])),
               "by_kind_pair": dict(Counter("+".join(sorted((r['a']['kind'], r['b']['kind']))) for r in rows)),
               "search_degraded": sum(bool(r["vector_note"]) for r in diagnostics), "merge": merge,
               "seed": SEED, "hub": hub.base, "new_read_calls": hub.calls,
               "candidates_sha256": hashlib.sha256((out / "candidates.jsonl").read_bytes()).hexdigest()}
    write_json(out / "candidates.summary.json", summary)
    print(json.dumps({k:v for k,v in summary.items() if k not in ("domains", "census_checks", "merge", "excluded_nodes")}), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--hub", default="https://mcp.vincs.io")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    hub = ReadHub(args.hub, os.environ["VINC_RO"], args.out / "read-cache")
    me = hub.request("/v1/me")
    if me.get("scope") != "personal":
        raise ValueError("VIN-410 requires the personal graph")
    collect(hub, args.out)


if __name__ == "__main__":
    main()
