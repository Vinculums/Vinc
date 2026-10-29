# -*- coding: utf-8 -*-
"""pull_tokens.py — 그래프의 토큰 노드를 `tokens.json` 스냅샷으로 내려받는다.

값의 정본은 그래프다. 토큰 하나가 `concept:token.*` 노드 하나이고(`vinc/design/tokens`),
뿌리 `concept:design-tokens` 가 묶음 순서·메타·묶음 메모·데스크톱 테넌트를 갖는다.
CI 는 그래프에 닿지 않으므로 이 스크립트를 로컬에서 돌려 스냅샷을 다시 쓰고, 생성물
(`sync_out.py --apply`)과 **같은 커밋에** 넣는다. `graph-refs.json` 과 같은 방식이다.

    VINC_REMOTE_MCP_TOKEN=... python -X utf8 scripts/design/pull_tokens.py          # 다시 쓴다
    VINC_REMOTE_MCP_TOKEN=... python -X utf8 scripts/design/pull_tokens.py --check  # 같은지만 본다

값을 바꾸는 길은 그래프 쪽이다: 그 노드에 `props_patch` 와 `expected_rev` 로 쓴다(props 는
통째로 바뀌므로 부분 전송은 안 된다). 스냅샷을 손으로 고치면 다음 pull 이 되돌린다.
근거: [[decision:design-token-values-live-in-the-graph-and-the-hub-directory-is-gone-2026-10-29]]
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SNAPSHOT = HERE / "tokens.json"

DOMAIN = "domain:vinc/design/tokens"
ROOT_NODE = "concept:design-tokens"
PREFIX = "concept:token."
TRIES = 3

#: 묶음마다 노드 props 에서 스냅샷으로 옮길 칸. 나머지(group·key·order)는 자리를 정한다.
SCALAR_GROUPS = {"radius", "space", "fs", "lh", "font", "shadow", "shadowDark", "motion"}
NOTE_GROUPS = {"viewScene", "topicJewels", "motion"}


class PullError(SystemExit):
    pass


def _call_with_retry(call, name: str, args: dict, sleep=time.sleep) -> dict:
    res: dict = {}
    for _ in range(TRIES):
        res = call(name, args)
        wait = res.get("retry_after_s") if isinstance(res, dict) else None
        if isinstance(res, dict) and res.get("executed") is not False and not res.get("isError"):
            return res
        if isinstance(wait, (int, float)) and wait > 0:
            sleep(min(float(wait), 5.0))
    raise PullError(f"[pull_tokens] {name} {args} 실패: {res.get('error') if isinstance(res, dict) else res}")


def _node(call, node_id: str) -> dict:
    res = _call_with_retry(call, "vinc_node", {"node_id": node_id})
    if not res.get("found"):
        raise PullError(f"[pull_tokens] 노드가 없다: {node_id}")
    return res["node"]


def token_ids(call) -> list[str]:
    res = _call_with_retry(call, "vinc_neighbors", {"node": DOMAIN, "limit": 500})
    if res.get("neighbors_truncated"):
        raise PullError("[pull_tokens] 주제 목록이 잘렸다(500 초과): 토큰을 다 볼 수 없다")
    return sorted(n["id"] for n in res.get("neighbors", []) if n.get("id", "").startswith(PREFIX))


def assemble(root: dict, nodes: list[dict]) -> dict:
    """뿌리와 토큰 노드들 → 스냅샷(`build_tokens.py` 가 읽는 모양). 순서는 props.order."""
    rp = root.get("props") or {}
    groups = rp.get("groups") or []
    notes = rp.get("notes") or {}
    by_group: dict[str, list[dict]] = {g: [] for g in groups}
    seen: dict[tuple, str] = {}
    for n in nodes:
        p = n.get("props") or {}
        g, k = p.get("group"), p.get("key")
        if g not in by_group:
            raise PullError(f"[pull_tokens] {n['id']}: 묶음 {g!r} 이 뿌리의 groups 에 없다")
        if (g, k) in seen:
            raise PullError(f"[pull_tokens] {n['id']}: {g}.{k} 가 {seen[(g, k)]} 와 겹친다")
        seen[(g, k)] = n["id"]
        by_group[g].append(p)
    for g, items in by_group.items():
        orders = [p.get("order") for p in items]
        if len(set(orders)) != len(orders) or any(not isinstance(o, int) for o in orders):
            raise PullError(f"[pull_tokens] 묶음 {g} 의 order 가 정수가 아니거나 겹친다: {orders}")
        items.sort(key=lambda p: p["order"])

    out: dict = {"$meta": rp.get("meta") or {}}
    for g in groups:
        items = by_group[g]
        block: dict = {"note": notes[g]} if g in NOTE_GROUPS and g in notes else {}
        if g == "primitive":
            block.update({p["key"]: {"$value": p["value"], "$type": p["type"],
                                     "$description": p["description"]} for p in items})
        elif g == "semantic":
            block = {"light": {p["key"]: p["light"] for p in items},
                     "dark": {p["key"]: p["dark"] for p in items}}
        elif g == "viewScene":
            block["node"] = {p["key"]: {k: p[k] for k in ("fill", "stroke", "glowOpacity") if k in p}
                             for p in items}
        elif g == "topicJewels":
            if len(items) != 1:
                raise PullError(f"[pull_tokens] topicJewels 노드는 하나여야 한다({len(items)})")
            block.update({"dark": items[0]["dark"], "light": items[0]["light"]})
        elif g in SCALAR_GROUPS:
            block.update({p["key"]: p["value"] for p in items})
        else:
            raise PullError(f"[pull_tokens] 모르는 묶음: {g}")
        out[g] = block
    if rp.get("tenants"):
        out["tenants"] = rp["tenants"]
    _check_refs(out)
    return out


def _check_refs(tokens: dict) -> None:
    """의미 토큰과 씬 단의 `{이름}` 이 기본 토큰을 한 단계로 가리키는지."""
    prims = set(tokens.get("primitive", {}))
    refs = [(f"semantic.{m}.{k}", v) for m in ("light", "dark")
            for k, v in tokens.get("semantic", {}).get(m, {}).items()]
    refs += [(f"viewScene.{t}.{f}", s[f]) for t, s in tokens.get("viewScene", {}).get("node", {}).items()
             for f in ("fill", "stroke") if f in s]
    bad = [(where, v) for where, v in refs if v.startswith("{") and v[1:-1] not in prims]
    if bad:
        raise PullError("[pull_tokens] 없는 기본 토큰을 가리킨다: "
                        + ", ".join(f"{w}={v}" for w, v in bad))


def render(tokens: dict) -> str:
    return json.dumps(tokens, ensure_ascii=False, indent=2) + "\n"


def pull(call) -> str:
    root = _node(call, ROOT_NODE)
    nodes = [_node(call, i) for i in token_ids(call)]
    return render(assemble(root, nodes))


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not os.environ.get("VINC_REMOTE_MCP_TOKEN"):
        print("[pull_tokens] VINC_REMOTE_MCP_TOKEN 이 없다: 그래프를 읽으려면 키가 필요하다",
              file=sys.stderr)
        return 2
    sys.path.insert(0, str(ROOT))
    from vinc_mcp import remote  # noqa: E402 — 키가 있을 때만 네트워크 모듈을 싣는다
    text = pull(remote.call)
    current = SNAPSHOT.read_text(encoding="utf-8") if SNAPSHOT.exists() else ""
    if "--check" in argv:
        if text == current:
            print("[pull_tokens] 스냅샷이 그래프와 같다")
            return 0
        print("[pull_tokens] 스냅샷이 그래프와 다르다: pull_tokens.py 로 다시 쓰고 "
              "sync_out.py --apply 와 같은 커밋에 넣을 것")
        return 1
    if text == current:
        print("[pull_tokens] 바뀐 것 없음")
        return 0
    SNAPSHOT.write_text(text, encoding="utf-8", newline="\n")
    print(f"[pull_tokens] {SNAPSHOT.relative_to(ROOT)} 를 다시 썼다 — 이제 "
          f"scripts/design/sync_out.py --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
