# -*- coding: utf-8 -*-
"""Share pack — 한 계정이 로컬에 적재한 데이터를 **다른 사람에게 전달**하기 위한
패키징(내보내기/들여오기). 계약 정본: Vinc-docs `vinc.c.sharepack`(DEC D19).

설계 결정 3가지:
1. **포맷 = MCP fragment 동형**(D11 §5). 팩의 본문은 `{nodes, edges}` fragment 라서
   들여오기가 `api/fragment.merge_fragment`(id 정확 매칭 MERGE·멱등) 재사용 하나로
   끝난다 — 별도 import 기계 없음.
2. **커스텀 스코프**. 도메인 전체가 아니라 "이 영역까지만"을 지원한다:
   ``domain``(데이터셋 구획) · ``tags``(교집합) · ``seed``+``depth``(Atlas L2 와 동일한
   ego 서브그래프 — 그래프에서 고른 영역 그대로 패키징).
3. **preference 신호 미포함**. pinned/access_count/last_access 는 원 계정의 개인
   신호라 공유 팩에 싣지 않는다(투영에서 제외 — 스크럽 단계 불요). 수신자는
   자신의 owner 로 재스탬핑해 들여온다(fragment.owner 명시 → §1 스코프 불변 유지).

과금·계정 연동 없음(F1) — 팩은 로컬 파일이고 전달은 사용자의 채널(드라이브·메신저)로.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from . import fragment as frag
from . import query as q

PACK_VERSION = 1

# rel 라벨 → fragment 엣지 타입(D11 §5) — fragment._CC_EDGES 의 역방향.
_REL_TO_FRAGMENT = {rel: ft for ft, rel in frag._CC_EDGES.items()}


def _concepts_plan(*, owner: str, domain: str | None = None,
                   ids: list[str] | None = None) -> q.QueryPlan:
    """공유 투영 — preference 신호 없는 콘텐츠 필드만.

    ⚠ owner 는 **모든 스코프에서 무조건** 건다(SEC-2). 예전엔 ids 분기가 owner 를
    받고도 쓰지 않아, 남의 concept_id 를 알면 그 콘텐츠가 팩에 실렸다 — SEC-1 과
    같은 결함(인자만 받고 술어에 안 씀). 호스티드에서 이 경로가 export 로 쓰이므로
    필터는 협상 대상이 아니다."""
    where, params = ["c.owner_id = $owner"], {"owner": owner}
    if ids is not None:
        where.append("c.concept_id IN $ids")
        params["ids"] = ids
    elif domain:
        where.append("c.domain = $domain")
        params["domain"] = domain
    cypher = (
        f"MATCH (c:Concept) WHERE {' AND '.join(where)} "
        "RETURN c.concept_id AS id, c.title AS title, c.domain AS domain, "
        "c.tags AS tags, c.topic AS topic, c.one_liner AS one_liner, c.props AS props"
    )
    return q.QueryPlan("share_concepts", cypher, params, note="share projection (no preference)")


def _episodes_plan(*, owner: str, domain: str | None = None) -> q.QueryPlan:
    where = ["e.owner_id = $owner"]
    params: dict = {"owner": owner}
    if domain:
        where.append("e.domain = $domain")
        params["domain"] = domain
    cypher = (
        f"MATCH (e:Episode) WHERE {' AND '.join(where)} "
        "RETURN e.episode_id AS id, e.title AS title, e.domain AS domain, "
        "e.date AS date, e.summary AS summary, e.tags AS tags"
    )
    return q.QueryPlan("share_episodes", cypher, params, note="share projection (no preference)")


def _tag_filter(rows: list[dict], tags: list[str] | None) -> list[dict]:
    if not tags:
        return rows
    want = set(tags)
    return [r for r in rows if want & set(r.get("tags") or [])]


def build_share_pack(*, owner: str, domain: str | None = None,
                     tags: list[str] | None = None, seed: str | None = None,
                     ids: list[str] | None = None,
                     depth: int = 2, include_records: bool = True,
                     generated_at: str | None = None,
                     cfg=None, store=None) -> dict:
    """스코프된 공유 팩을 만든다. 반환 dict 를 그대로 JSON 파일로 저장해 전달한다."""
    # 1) 스코프 결정 — ids(명시 집합: lasso 영역 선택, Phase B) > seed(ego) > domain.
    if ids is not None:
        c_res = q.execute(_concepts_plan(owner=owner, ids=sorted(ids)), cfg, store=store)
    elif seed:
        # SEC-2: 순회도 owner 스코프 — 안 그러면 남의 시드에서 남의 서브그래프를 뽑는다.
        ids_res = q.execute(q.subgraph_ids_plan(seed, depth=depth, owner=owner),
                            cfg, store=store)
        if not ids_res.get("executed"):
            return {"executed": False, "note": ids_res.get("note", "no reachable store")}
        ids = sorted({seed, *(r["id"] for r in ids_res.get("rows", []) if r.get("id"))})
        c_res = q.execute(_concepts_plan(owner=owner, ids=ids), cfg, store=store)
    else:
        c_res = q.execute(_concepts_plan(owner=owner, domain=domain), cfg, store=store)
    if not c_res.get("executed"):
        return {"executed": False, "note": c_res.get("note", "no reachable store")}
    concepts = _tag_filter(c_res.get("rows", []), tags)
    id_set = {c["id"] for c in concepts}

    # 2) 스코프 내 엣지 — Atlas 와 동일 플랜 재사용, 경계 밖은 잘라낸다.
    edges: list[dict] = []
    if id_set:
        e_res = q.execute(q.graph_rows_for_ids_plan(sorted(id_set), owner=owner),
                          cfg, store=store)
        seen = set()
        for r in e_res.get("rows", []):
            ftype = _REL_TO_FRAGMENT.get(r.get("rel") or "")
            tgt = r.get("target")
            if not ftype or not tgt or tgt not in id_set or r["id"] not in id_set:
                continue
            key = (r["id"], ftype, tgt)
            if key in seen:
                continue
            seen.add(key)
            edges.append({"from": r["id"], "to": tgt, "type": ftype})

    # 3) 레코드(Episode) — 도메인/태그 스코프에서만(ids/ego 스코프는 개념 중심).
    episodes: list[dict] = []
    if include_records and not seed and ids is None:
        ep_res = q.execute(_episodes_plan(owner=owner, domain=domain), cfg, store=store)
        episodes = _tag_filter(ep_res.get("rows", []), tags)

    # 4) fragment 노드로 사상.
    nodes: list[dict] = []
    for c in concepts:
        n = {"id": c["id"], "type": "concept", "title": c.get("title") or c["id"],
             "domain": c.get("domain") or "", "tags": c.get("tags") or []}
        props = {}
        if c.get("topic"):
            props["topic"] = c["topic"]
        if c.get("one_liner"):
            props["one_liner"] = c["one_liner"]
        if c.get("props"):
            try:
                props.update(json.loads(c["props"]))
            except (TypeError, ValueError):
                pass
        if props:
            n["props"] = props
        nodes.append(n)
    for e in episodes:
        nodes.append({"id": e["id"], "type": "episode", "title": e.get("title") or e["id"],
                      "domain": e.get("domain") or "", "tags": e.get("tags") or [],
                      "date": e.get("date"), "summary": e.get("summary")})

    created = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "vinc_pack": PACK_VERSION,
        "kind": "share",
        "manifest": {
            "created": created,
            "scope": {"domain": domain, "tags": tags or [],
                      "seed": seed, "depth": depth if seed else None,
                      "ids": len(ids) if ids is not None else None},
            "counts": {"concepts": len(concepts), "records": len(episodes),
                       "edges": len(edges)},
            "note": "Preference signals are not included — they stay with the origin account.",
        },
        "fragment": {"nodes": nodes, "edges": edges},
        "executed": True,
    }


def import_share_pack(pack: dict, *, owner: str, cfg=None, store=None) -> dict:
    """공유 팩 들여오기 — 수신자 ``owner`` 로 재스탬핑해 fragment MERGE(멱등)."""
    if not isinstance(pack, dict) or pack.get("vinc_pack") != PACK_VERSION:
        return {"executed": False,
                "error": f"unsupported pack version: {pack.get('vinc_pack')!r} "
                         f"(expected {PACK_VERSION})"}
    fragment = pack.get("fragment") or {}
    owns = store is None
    if owns:
        from store.backend import open_store
        from store.config import load_config
        store = open_store(cfg or load_config())
    if store is None:
        return {"executed": False, "error": "no reachable store"}
    try:
        res = frag.merge_fragment({**fragment, "owner": owner}, store=store)
    finally:
        if owns:
            store.close()
    res["manifest"] = pack.get("manifest")
    return res


def scan_inbox(path: str, *, owner: str, store) -> dict:
    """Pull inbox(Phase E2) — 디렉토리의 `*.json`/`*.vincpack` 공유 팩을 일괄
    수입한다(잡 큐에서 실행 — 긴 작업). 파일 단위 결과·오류를 모아 반환하며,
    성공 파일은 `.done` 접미로 옮겨 재수입을 막는다(멱등 이중 안전)."""
    from pathlib import Path as _P
    root = _P(path)
    results, errors = [], []
    if not root.is_dir():
        return {"executed": False, "error": f"inbox not a directory: {path}"}
    for f in sorted(root.glob("*")):
        if f.suffix.lower() not in (".json", ".vincpack") or f.name.endswith(".done"):
            continue
        try:
            pack = json.loads(f.read_text(encoding="utf-8"))
            res = import_share_pack(pack, owner=owner, store=store)
            if res.get("executed"):
                f.rename(f.with_name(f.name + ".done"))
                results.append({"file": f.name, "merged": res["merged"]})
            else:
                errors.append({"file": f.name, "error": res.get("error", "?")})
        except (ValueError, OSError) as exc:
            errors.append({"file": f.name, "error": f"{type(exc).__name__}: {exc}"})
    return {"executed": True, "imported": results, "errors": errors}
