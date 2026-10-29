# -*- coding: utf-8 -*-
"""seed_tokens.py — 커밋된 스냅샷에서 그래프의 토큰 노드를 다시 쓴다(복구용).

`pull_tokens.py` 의 역방향이다. 평소에는 쓸 일이 없다: 값은 그래프에서 바꾸고 pull 한다.
그래프가 되감겨 토큰 노드를 잃었을 때, 마지막으로 커밋된 `tokens.json` 이 그 값을 갖고
있으므로 여기서 노드·엣지·뿌리를 그대로 되살린다. 기본은 계획만 찍는다(쓰지 않는다).

    python -X utf8 scripts/design/seed_tokens.py                 # 계획(JSON)만
    VINC_REMOTE_MCP_TOKEN=... python -X utf8 scripts/design/seed_tokens.py --apply

`plan()` 이 만든 것을 `pull_tokens.assemble()` 에 넣으면 스냅샷이 그대로 나온다
(`tests/design/test_design_tokens.py` 가 그 왕복을 지킨다).
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import uuid

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import build_tokens  # noqa: E402

DOMAIN = "vinc/design/tokens"
ROOT_NODE = "concept:design-tokens"
GROUP_SLUG = {"primitive": "primitive", "semantic": "semantic", "radius": "radius", "space": "space",
              "fs": "fs", "lh": "lh", "font": "font", "shadow": "shadow", "shadowDark": "shadow-dark",
              "motion": "motion", "viewScene": "view-scene"}
CSS_PREFIX = {"radius": "radius", "space": "space", "fs": "fs", "lh": "lh", "font": "font",
              "shadow": "shadow", "shadowDark": "shadow", "motion": None}


def node_id(group: str, key: str) -> str:
    if group == "topicJewels":
        return "concept:token.topic-jewels"
    return f"concept:token.{GROUP_SLUG[group]}.{re.sub(r'[^a-z0-9]+', '-', key.lower()).strip('-')}"


def _node(group: str, key: str, order: int, title: str, props: dict, one_liner: str | None = None) -> dict:
    n = {"id": node_id(group, key), "type": "concept", "domain": DOMAIN, "title": title,
         "tags": ["design-token", "topic-jewels" if group == "topicJewels" else GROUP_SLUG[group]],
         "props": {"group": group, "key": key, "order": order, **props}}
    if one_liner:
        n["one_liner"] = one_liner
    return n


def plan(tokens: dict) -> dict:
    """스냅샷 → {nodes, edges, root_props}."""
    nodes, edges = [], []
    prim = lambda ref: node_id("primitive", ref[1:-1])  # noqa: E731
    groups = [g for g in tokens if g not in ("$meta", "tenants")]
    for g in groups:
        block = tokens[g]
        if g == "primitive":
            for i, (k, v) in enumerate(block.items()):
                nodes.append(_node(g, k, i, k, {"value": v["$value"], "type": v["$type"],
                                                "description": v["$description"]}, v["$description"]))
        elif g == "semantic":
            for i, k in enumerate(block["light"]):
                lt, dk = block["light"][k], block["dark"][k]
                nodes.append(_node(g, k, i, f"--{k}", {"light": lt, "dark": dk}))
                edges += [{"from": node_id(g, k), "to": prim(r), "type": "derives_from"}
                          for r in dict.fromkeys([lt, dk]) if r.startswith("{")]
        elif g == "viewScene":
            for i, (k, spec) in enumerate(block["node"].items()):
                nodes.append(_node(g, k, i, f"View 3D mark, {k} tier", dict(spec)))
                edges += [{"from": node_id(g, k), "to": prim(spec[f]), "type": "derives_from"}
                          for f in ("fill", "stroke") if f in spec and spec[f].startswith("{")]
        elif g == "topicJewels":
            nodes.append(_node(g, "topicJewels", 0, "Topic jewel colours",
                               {"dark": block["dark"], "light": block["light"]},
                               "The five jewel colours topics take in order in the View, the left tree "
                               "and the node card, one set per mode."))
        else:
            items = [(k, v) for k, v in block.items() if k != "note"]
            for i, (k, v) in enumerate(items):
                p = CSS_PREFIX[g]
                css = f"--{k}" if p is None else (f"--{p}" if k == "DEFAULT" else f"--{p}-{k}")
                nodes.append(_node(g, k, i, css + (" (dark)" if g == "shadowDark" else ""), {"value": v}))
    notes = {g: tokens[g]["note"] for g in groups if isinstance(tokens[g], dict) and "note" in tokens[g]}
    root_props = {"groups": groups, "id_prefix": "concept:token.", "meta": tokens.get("$meta", {}),
                  "notes": notes, "snapshot": "Vinc:scripts/design/tokens.json"}
    if tokens.get("tenants"):
        root_props["tenants"] = tokens["tenants"]
    return {"nodes": nodes, "edges": edges, "root_props": root_props}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    p = plan(build_tokens.load())
    if "--apply" not in argv:
        print(json.dumps(p, ensure_ascii=False, indent=2))
        print(f"# 노드 {len(p['nodes'])} · 엣지 {len(p['edges'])} — 쓰려면 --apply", file=sys.stderr)
        return 0
    if not os.environ.get("VINC_REMOTE_MCP_TOKEN"):
        print("[seed_tokens] VINC_REMOTE_MCP_TOKEN 이 없다", file=sys.stderr)
        return 2
    sys.path.insert(0, str(ROOT))
    from vinc_mcp import remote  # noqa: E402
    batches = [p["nodes"][i:i + 30] for i in range(0, len(p["nodes"]), 30)]
    for nodes in batches:
        res = remote.call("vinc_ingest", {"nodes": nodes, "operation_id": str(uuid.uuid4())})
        if res.get("isError") or res.get("warnings"):
            print(f"[seed_tokens] 노드 쓰기 실패 또는 경고: {res.get('error') or res.get('warnings')}",
                  file=sys.stderr)
            return 1
    res = remote.call("vinc_ingest", {"nodes": [], "edges": p["edges"], "operation_id": str(uuid.uuid4())})
    if res.get("isError") or res.get("warnings"):
        print(f"[seed_tokens] 엣지 쓰기 실패 또는 경고: {res.get('error') or res.get('warnings')}",
              file=sys.stderr)
        return 1
    # 뿌리의 다른 칸은 남기고 토큰 칸만 바꾼다: 현재 rev 를 읽어 props_patch 로 보낸다.
    cur = remote.call("vinc_node", {"node_id": ROOT_NODE})
    rev = (cur.get("node") or {}).get("rev") if cur.get("found") else None
    root = {"id": ROOT_NODE, "type": "concept", "domain": DOMAIN}
    root.update({"expected_rev": rev, "props_patch": p["root_props"]} if rev is not None
                else {"title": "The design tokens", "props": p["root_props"]})
    res = remote.call("vinc_ingest", {"nodes": [root], "operation_id": str(uuid.uuid4())})
    if res.get("isError") or res.get("warnings"):
        print(f"[seed_tokens] 뿌리 쓰기 실패: {res.get('error') or res.get('warnings')}", file=sys.stderr)
        return 1
    print(f"[seed_tokens] 노드 {len(p['nodes'])} · 엣지 {len(p['edges'])} · 뿌리 1 을 썼다. "
          f"pull_tokens.py --check 로 확인할 것")
    return 0


if __name__ == "__main__":
    sys.exit(main())
