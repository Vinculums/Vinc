# -*- coding: utf-8 -*-
"""vinc_sync — 로컬↔원격 양방향 동기화의 데이터플레인(S1: export/apply).

왜 share pack 을 그대로 못 쓰나: `build_share_pack` 은 **전량 스냅샷**이고 delta
(since) 개념이 없다. 동기화는 "지난 번 이후 바뀐 것"만 날라야 한다 — S0 가 넣은
`sync_seq`(owner별 단조 카운터)가 그 커서다.

**오프라인 우선 약속의 각인**: export 는 `_SyncMeta.selected_domains` 에 든 도메인만
훑는다. 안 고른 도메인은 쿼리 자체가 닿지 않으므로 **기기를 떠날 수 없다**. 선택이
비어 있으면 아무것도 내보내지 않는다(명시적 옵트인).

**최초 동기화**: S0 마이그레이션은 기존 노드를 `sync_seq=0` 으로 백필한다. 커서도 0
이라 `sync_seq > 0` 은 그것들을 놓친다 — 그래서 `since=0`(=한 번도 push 안 함)이면
**선택 도메인 전량**을 내보낸다(delta 아님). 그 다음부터 delta.

적용(apply)은 `fragment.merge_fragment` 를 재사용하되 **rev 가드**를 건다: 인바운드
rev 가 저장본보다 낮으면 무시(늦게 도착한 옛 변경이 최신을 덮지 않게). 원격 스탬프
(updated_at/rev)는 **그대로 보존**한다 — 재스탬프하면 pull 이 곧 새 로컬 변경으로
보여 무한 왕복이 된다.
"""
from __future__ import annotations

import json

from . import fragment as frag
from . import revstamp
from . import syncmerge
from . import query as q

# 청크 상한 — 원격 `MAX_BODY`(4MB, vinc_mcp/http_server.py)보다 넉넉히 아래.
# 초과 시 서버가 빈 바디로 400 을 내므로 조용히 실패한다 → 여유를 크게 둔다.
CHUNK_BYTES = 3 * 1024 * 1024

_SEL_KEY = "selected_domains"

# ── 동기화 필드(=merge_fragment 가 실제로 왕복시키는 것만, S3) ─────────────────
# 급소: 3-way 병합은 로컬·원격·base 를 **같은 표현**으로 비교해야 한다. fragment 쓰기
# 경로(_merge_node)가 쓰는 필드만 왕복 가능하다 — Concept 의 topic/one_liner/status/
# stem, Episode 의 pinned 은 fragment 가 안 써서(엔진·preference 소관) 동기화하면
# 유실된다. 그래서 sync 는 왕복 가능한 필드만 나른다(그 외는 기기 로컬로 남는다).
SYNC_FIELDS: dict[str, tuple[str, ...]] = {
    "Concept": ("title", "domain", "tags", "props"),
    "Episode": ("title", "date", "domain", "summary", "tags"),
}
# fragment 노드 타입 → 라벨(export/apply 대상). Bridge/Entity 는 fragment 왕복 결함이
# 있어(구조 필드 미기록) 현재 sync 범위 밖 — Concept/Episode 만.
_SYNC_TYPES = {"concept": "Concept", "episode": "Episode"}


def _snapshot(label: str, node: dict) -> dict:
    """노드(번들 노드 또는 로컬 조회 dict)에서 sync 필드만 **raw** 로 뽑되 표현을
    통일한다: props 는 항상 dict(로컬 DB 는 JSON 문자열이라 파싱), tags 는 항상 list.
    로컬·원격·base 가 같은 표현이라야 3-way 비교·쓰기·저장이 일관된다. 값 비교는
    syncmerge._norm(dict→정렬 JSON, list→tuple)이 담당한다."""
    snap: dict = {}
    for f in SYNC_FIELDS.get(label, ()):
        v = node.get(f)
        if f == "props":
            if isinstance(v, str):
                try:
                    v = json.loads(v) if v else {}
                except (TypeError, ValueError):
                    v = {}
            elif v is None:
                v = {}
        elif f == "tags" and v is None:
            v = []
        snap[f] = v
    return snap


# ── 3-way base(_SyncBase, 로컬 전용) ──────────────────────────────────────────
def get_base(store, node_id: str) -> dict | None:
    rows = store.run("MATCH (b:_SyncBase) WHERE b.node_id = $id "
                     "RETURN b.fields AS f, b.base_rev AS rev, b.base_updated_at AS ua",
                     {"id": node_id})
    if not rows:
        return None
    return {"fields": syncmerge.parse_base(rows[0].get("f")),
            "rev": int(rows[0].get("rev") or 0), "ua": rows[0].get("ua") or ""}


def set_base(store, node_id: str, label: str, fields: dict, rev: int, ua: str) -> None:
    store.run("MERGE (b:_SyncBase {node_id: $id}) "
              "SET b.label = $l, b.fields = $f, b.base_rev = $rev, b.base_updated_at = $ua",
              {"id": node_id, "l": label, "f": syncmerge.dump_base(fields),
               "rev": int(rev or 0), "ua": ua or ""})


def del_base(store, node_id: str) -> None:
    store.run("MATCH (b:_SyncBase) WHERE b.node_id = $id DELETE b", {"id": node_id})


# ── tombstone(삭제 전파, S4) ──────────────────────────────────────────────────
def get_tombstone(store, node_id: str) -> dict | None:
    rows = store.run("MATCH (t:_Tombstone) WHERE t.node_id = $id "
                     "RETURN t.deleted_at AS da, t.rev AS rev", {"id": node_id})
    if not rows:
        return None
    return {"deleted_at": rows[0].get("da") or "", "rev": int(rows[0].get("rev") or 0)}


def put_tombstone(store, node_id: str, label: str, owner: str, deleted_at: str,
                  rev: int) -> None:
    """수신한 삭제를 재전파용으로 남긴다. deleted_at 은 **원래 삭제 시각 보존**(의미),
    sync_seq 는 수신측 자기 번호(다른 기기의 pull 커서용) — 노드 스탬프와 같은 규칙."""
    store.run("MERGE (t:_Tombstone {node_id: $id}) SET t.label = $l, t.owner_id = $o, "
              "t.deleted_at = $da, t.rev = $rev, t.sync_seq = $seq, t.origin = 'remote'",
              {"id": node_id, "l": label, "o": owner, "da": deleted_at,
               "rev": int(rev), "seq": revstamp.next_seq(store, owner)})


def del_tombstone(store, node_id: str) -> None:
    store.run("MATCH (t:_Tombstone) WHERE t.node_id = $id DELETE t", {"id": node_id})


def _tombstones_delta_plan(*, owner: str, since: int) -> q.QueryPlan:
    """owner 의 tombstone 중 sync_seq > since(최초 sync 면 since=0 전량). 동기화 라벨만
    (Concept/Episode) — 그 외 라벨 삭제는 전파할 곳이 없다."""
    where = ["t.owner_id = $owner", "t.label IN ['Concept', 'Episode']"]
    params: dict = {"owner": owner}
    if since > 0:
        where.append("t.sync_seq > $since")
        params["since"] = since
    cypher = (f"MATCH (t:_Tombstone) WHERE {' AND '.join(where)} "
              "RETURN t.node_id AS node_id, t.label AS label, t.deleted_at AS deleted_at, "
              "t.rev AS rev, t.sync_seq AS sync_seq ORDER BY t.sync_seq")
    return q.QueryPlan("sync_tombstones", cypher, params, note="owner-scoped delete markers")


def gc_tombstones(store, *, keep_days: int = 90, now_ms: int | None = None) -> int:
    """안전 지평 지난 tombstone 청소. 보수적 90일 — 그 안에 모든 기기가 pull 했다고
    가정. 문자열 비교(HLC ISO 고정폭)라 임계 ISO 를 만들어 그 이하를 지운다."""
    import datetime
    ms = int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000) \
        if now_ms is None else now_ms
    cutoff = revstamp._fmt(ms - keep_days * 86400 * 1000)
    cur = store.run("MATCH (t:_Tombstone) WHERE t.deleted_at < $c AND t.deleted_at <> '' "
                    "RETURN count(t) AS c", {"c": cutoff})
    n = int(cur[0].get("c") or 0) if cur else 0
    if n:
        store.run("MATCH (t:_Tombstone) WHERE t.deleted_at < $c AND t.deleted_at <> '' "
                  "DELETE t", {"c": cutoff})
    return n


# ── 선택 도메인 (오프라인 우선 옵트인) ────────────────────────────────────────
def get_selected_domains(store) -> list[str]:
    raw = revstamp._meta_get(store, _SEL_KEY, "")
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return [str(x) for x in v] if isinstance(v, list) else []
    except (TypeError, ValueError):
        return []


def set_selected_domains(store, domains: list[str]) -> list[str]:
    clean = sorted({str(d) for d in (domains or []) if str(d).strip()})
    revstamp._meta_set(store, _SEL_KEY, json.dumps(clean, ensure_ascii=False))
    return clean


# ── 커서 ──────────────────────────────────────────────────────────────────────
def get_cursor(store, key: str) -> int:
    try:
        return int(revstamp._meta_get(store, f"cursor:{key}", "0"))
    except (TypeError, ValueError):
        return 0


def set_cursor(store, key: str, value: int) -> None:
    revstamp._meta_set(store, f"cursor:{key}", int(value))


# ── export ────────────────────────────────────────────────────────────────────
def _concepts_delta_plan(*, owner: str, domains: list[str], since: int) -> q.QueryPlan:
    """선택 도메인 안에서 `sync_seq > since` 인 Concept. since=0 이면 전량(최초 push)."""
    where = ["c.owner_id = $owner", "c.domain IN $domains"]
    params: dict = {"owner": owner, "domains": domains}
    if since > 0:
        where.append("c.sync_seq > $since")
        params["since"] = since
    cypher = (
        f"MATCH (c:Concept) WHERE {' AND '.join(where)} "
        "RETURN c.concept_id AS id, c.title AS title, c.domain AS domain, "
        "c.tags AS tags, c.topic AS topic, c.one_liner AS one_liner, "
        "c.props AS props, c.updated_at AS updated_at, c.rev AS rev, "
        "c.sync_seq AS sync_seq ORDER BY c.sync_seq"
    )
    return q.QueryPlan("sync_concepts", cypher, params, note="owner+domain scoped delta")


def _episodes_delta_plan(*, owner: str, domains: list[str], since: int) -> q.QueryPlan:
    where = ["e.owner_id = $owner", "e.domain IN $domains"]
    params: dict = {"owner": owner, "domains": domains}
    if since > 0:
        where.append("e.sync_seq > $since")
        params["since"] = since
    cypher = (
        f"MATCH (e:Episode) WHERE {' AND '.join(where)} "
        "RETURN e.episode_id AS id, e.title AS title, e.domain AS domain, "
        "e.date AS date, e.summary AS summary, e.tags AS tags, e.pinned AS pinned, "
        "e.updated_at AS updated_at, e.rev AS rev, e.sync_seq AS sync_seq "
        "ORDER BY e.sync_seq"
    )
    return q.QueryPlan("sync_episodes", cypher, params, note="owner+domain scoped delta")


def _node_from_concept(r: dict) -> dict:
    # sync 는 **raw props** 만 나른다(topic/one_liner 는 fragment 왕복 불가라 폴딩 안 함).
    props = {}
    if r.get("props"):
        try:
            props = json.loads(r["props"]) or {}
        except (TypeError, ValueError):
            props = {}
    return {"id": r["id"], "type": "concept", "title": r.get("title") or r["id"],
            "domain": r.get("domain") or "", "tags": list(r.get("tags") or []),
            "props": props,
            "_rev": int(r.get("rev") or 0), "_updated_at": r.get("updated_at") or "",
            "_seq": int(r.get("sync_seq") or 0)}


def _node_from_episode(r: dict) -> dict:
    return {"id": r["id"], "type": "episode", "title": r.get("title") or r["id"],
            "domain": r.get("domain") or "", "tags": list(r.get("tags") or []),
            "date": r.get("date"), "summary": r.get("summary"),
            "_rev": int(r.get("rev") or 0), "_updated_at": r.get("updated_at") or "",
            "_seq": int(r.get("sync_seq") or 0)}


def export_delta(*, owner: str, since: int = 0, domains: list[str] | None = None,
                 cfg=None, store=None) -> dict:
    """owner + 선택 도메인 스코프의 delta 번들.

    반환 `{executed, since, next_cursor, full, nodes, edges, counts}`. `full=True` 는
    최초 동기화(전량)를 뜻한다. 노드에는 `_rev/_updated_at/_seq` 가 실려 수신측이
    LWW 가드와 커서를 판단한다."""
    sel = domains if domains is not None else get_selected_domains(store)
    base = {"executed": False, "since": since, "next_cursor": since,
            "full": since <= 0, "nodes": [], "edges": [], "counts": {}}
    if not sel:
        # 선택 도메인 없음 = 아무것도 내보내지 않는다(오프라인 우선 기본값).
        base["executed"] = True
        base["note"] = "no domains selected — nothing leaves the device"
        return base

    c_res = q.execute(_concepts_delta_plan(owner=owner, domains=sel, since=since),
                      cfg, store=store)
    if not c_res.get("executed"):
        base["note"] = c_res.get("note", "no reachable store")
        return base
    e_res = q.execute(_episodes_delta_plan(owner=owner, domains=sel, since=since),
                      cfg, store=store)

    c_rows = c_res.get("rows", [])
    e_rows = e_res.get("rows", []) if e_res.get("executed") else []
    nodes = [_node_from_concept(r) for r in c_rows] + [_node_from_episode(r) for r in e_rows]

    # 엣지: **양끝이 이번 번들 안에 있는 것만**. fragment 는 같은 payload 의 노드로만
    # (from,to) 타입을 해석하므로(fragment.py `_node_types`), 끝점이 빠진 엣지는
    # 수신측에서 조용히 skip 된다 — 애초에 싣지 않는다.
    edges: list[dict] = []
    ids = {n["id"] for n in nodes}
    if ids:
        rel_res = q.execute(q.graph_rows_for_ids_plan(sorted(ids), owner=owner),
                            cfg, store=store)
        seen = set()
        for r in rel_res.get("rows", []):
            ftype = _REL_TO_FRAGMENT.get(r.get("rel") or "")
            tgt = r.get("target")
            if not ftype or not tgt or tgt not in ids or r["id"] not in ids:
                continue
            key = (r["id"], ftype, tgt)
            if key in seen:
                continue
            seen.add(key)
            edges.append({"from": r["id"], "to": tgt, "type": ftype})

    # 삭제 표식(S4) — 노드와 같은 커서 공간(sync_seq). 도메인 스코프는 없다(삭제된
    # 노드의 도메인을 알 수 없으니 owner 전체). 수신측이 부활 가드로 안전 적용.
    t_res = q.execute(_tombstones_delta_plan(owner=owner, since=since), cfg, store=store)
    tombstones = [{"node_id": r["node_id"], "label": r["label"],
                   "deleted_at": r.get("deleted_at") or "", "rev": int(r.get("rev") or 0),
                   "_seq": int(r.get("sync_seq") or 0)}
                  for r in (t_res.get("rows", []) if t_res.get("executed") else [])]

    seqs = [n["_seq"] for n in nodes] + [t["_seq"] for t in tombstones]
    nxt = max([since] + seqs) if seqs else since
    base.update({"executed": True, "nodes": nodes, "edges": edges,
                 "tombstones": tombstones, "next_cursor": nxt,
                 "counts": {"concepts": len(c_rows), "episodes": len(e_rows),
                            "edges": len(edges), "tombstones": len(tombstones)}})
    return base


# share.py 의 역매핑을 재사용(중복 정의 회피).
_REL_TO_FRAGMENT = {rel: ft for ft, rel in frag._CC_EDGES.items()}


# ── 청킹 ──────────────────────────────────────────────────────────────────────
def chunk_bundle(bundle: dict, *, limit: int = CHUNK_BYTES) -> list[dict]:
    """번들을 전송 가능한 크기로 쪼갠다. **각 청크는 자기완결** — 엣지는 양끝 노드가
    같은 청크에 있을 때만 싣는다(수신측 fragment 가 payload 내 노드로만 타입 해석).
    끝점이 갈라진 엣지는 마지막 청크에 양끝 노드를 다시 실어 함께 보낸다."""
    nodes = bundle.get("nodes") or []
    edges = bundle.get("edges") or []
    tombs = bundle.get("tombstones") or []
    if not nodes:
        # 노드가 없어도 엣지·tombstone 은 실어 보낸다(삭제-only sync).
        if not edges and not tombs:
            return []
        return [{"nodes": [], "edges": edges, "tombstones": tombs}]

    by_id = {n["id"]: n for n in nodes}
    chunks: list[dict] = []
    cur: list[dict] = []
    size = 0
    for n in nodes:
        b = len(json.dumps(n, ensure_ascii=False).encode("utf-8"))
        if cur and size + b > limit:
            chunks.append({"nodes": cur, "edges": []})
            cur, size = [], 0
        cur.append(n)
        size += b
    if cur:
        chunks.append({"nodes": cur, "edges": []})

    # 엣지를 양끝이 함께 든 청크에 배치; 갈라진 엣지는 별도 청크(양끝 노드 동봉).
    placed = set()
    for ch in chunks:
        have = {n["id"] for n in ch["nodes"]}
        for e in edges:
            k = (e["from"], e["type"], e["to"])
            if k not in placed and e["from"] in have and e["to"] in have:
                ch["edges"].append(e)
                placed.add(k)
    spill = [e for e in edges if (e["from"], e["type"], e["to"]) not in placed]
    if spill:
        need = {e["from"] for e in spill} | {e["to"] for e in spill}
        chunks.append({"nodes": [by_id[i] for i in sorted(need) if i in by_id],
                       "edges": spill})
    # tombstone 은 작아 첫 청크에 전부 싣는다(한 번만 적용되게).
    if tombs:
        chunks[0]["tombstones"] = tombs
    return chunks


# ── apply (3-way 병합, S3) ────────────────────────────────────────────────────
def _local_snapshot(store, label: str, pk: str, nid: str) -> dict | None:
    """로컬 노드의 sync 필드 + rev/updated_at. 없으면 None."""
    cols = ", ".join(f"x.{c} AS {c}" for c in SYNC_FIELDS[label])
    rows = store.run(f"MATCH (x:{label}) WHERE x.{pk} = $id "
                     f"RETURN {cols}, x.rev AS rev, x.updated_at AS updated_at", {"id": nid})
    if not rows:
        return None
    r = rows[0]
    snap = _snapshot(label, r)
    snap["_rev"] = int(r.get("rev") or 0)
    snap["_ua"] = r.get("updated_at") or ""
    return snap


def _merged_fragment_node(label: str, nid: str, ntype: str, merged: dict) -> dict:
    """병합 결과(snapshot)를 merge_fragment 가 쓸 수 있는 노드로. props=dict, tags=list."""
    node = {"id": nid, "type": ntype}
    node.update(merged)   # title/domain/tags/props(+date/summary) — 이미 raw 표현
    return node


def apply_delta(*, owner: str, bundle: dict, cfg=None, store=None) -> dict:
    """delta 번들을 **필드단위 3-way 병합**으로 적용(S3 — 발주자 핵심 요구).

    각 노드: base(마지막 동기화값) 대비 local·remote 를 필드별로 병합한다. 서로 다른
    필드를 고쳤으면 **둘 다 보존**, 같은 필드 충돌만 노드 타이브레이크(updated_at→rev→id)
    로 최신승. 병합 결과가 로컬과 달라졌으면 새 HLC 로 스탬프해 push 가 집어가게 하고,
    **base 는 원격값으로** 갱신한다 — 그래야 다음 push 가 로컬-only 필드만 감지한다
    (에코 완전 제거). 스탬프 규칙(§S2)은 그대로: sync_seq 는 수신측 자기 번호.
    """
    out = {"executed": False, "applied": 0, "skipped_stale": 0, "merged": 0,
           "edges": 0, "deleted": 0, "resurrected": 0, "warnings": []}
    if store is None:
        out["note"] = "no reachable store"
        return out

    writes: list[dict] = []          # merge_fragment 로 쓸 노드(스탬프 포함)
    base_updates: list[tuple] = []   # (nid, label, base_fields, base_rev, base_ua)
    for n in bundle.get("nodes") or []:
        nid, ntype = n.get("id"), n.get("type")
        label = _SYNC_TYPES.get(ntype or "")
        if not nid or not label:
            out["warnings"].append(f"unsupported node — skipped ({nid or '?'})")
            continue
        pk = frag._NODE_MAP[ntype][1]
        remote_snap = _snapshot(label, n)
        r_rev, r_ua = int(n.get("_rev") or 0), n.get("_updated_at") or ""

        # 부활 가드(S4): 로컬 tombstone 이 인바운드 편집보다 **새롭거나 같으면** 삭제가
        # 이긴다 → upsert 무시(지운 노드가 옛 편집으로 되살아나지 않게). 인바운드가 더
        # 최신이면(삭제 후 재생성) tombstone 을 걷고 적용한다.
        tomb = get_tombstone(store, nid)
        if tomb:
            if tomb["deleted_at"] >= r_ua:
                out["skipped_stale"] += 1
                continue
            del_tombstone(store, nid)
            out["resurrected"] += 1

        local = _local_snapshot(store, label, pk, nid)

        if local is None:                       # 신규 — 원격 그대로, 스탬프 보존
            writes.append({"node": _merged_fragment_node(label, nid, ntype, remote_snap),
                           "ua": r_ua, "rev": r_rev})
            base_updates.append((nid, label, remote_snap, r_rev, r_ua))
            continue

        l_rev, l_ua = local.pop("_rev"), local.pop("_ua")
        base = get_base(store, nid)
        # base 없음 = 공통 조상 없음(양쪽 독립 존재) → 빈 조상: 모든 필드가 '양쪽 변경'
        # 으로 취급돼 값이 다르면 필드별 LWW. dict(local) 로 두면 push 후·pull 전 로컬
        # 편집이 조상에 섞여 유실된다(그 편집이 원격과 충돌 시). 빈 조상이 안전측.
        base_fields = base["fields"] if base else {}
        remote_newer = syncmerge.node_remote_newer(l_rev, l_ua, r_rev, r_ua, nid)
        merged, conflicts = syncmerge.three_way(base_fields, local, remote_snap,
                                                remote_newer=remote_newer)

        if not syncmerge.maps_equal(merged, local):
            # 병합이 로컬을 바꿈 → 새 상태로 스탬프(신규 HLC·rev+1). push 가 집어간다.
            writes.append({"node": _merged_fragment_node(label, nid, ntype, merged),
                           "ua": revstamp.hlc_now(store),
                           "rev": max(l_rev, r_rev) + 1})
            out["merged"] += 1
        else:
            out["skipped_stale"] += 1           # 로컬에 반영할 변화 없음
        # base 는 항상 원격이 가진 값으로 — 다음 push 가 로컬-only 필드만 delta 로 본다.
        base_updates.append((nid, label, remote_snap, r_rev, r_ua))
        revstamp.observe_hlc(store, r_ua)

    if writes or bundle.get("edges"):
        res = frag.merge_fragment(
            {"owner": owner, "nodes": [w["node"] for w in writes],
             "edges": bundle.get("edges") or []}, store=store)
        out["executed"] = bool(res.get("executed"))
        out["edges"] = (res.get("merged") or {}).get("edges", {}).get("created", 0)
        out["warnings"] += res.get("warnings") or []
    else:
        out["executed"] = True

    # merge_fragment 가 방금 로컬 스탬프를 찍었으므로, 결정된 스탬프로 덮어쓴다.
    for w in writes:
        node = w["node"]
        label, pk = frag._NODE_MAP[node["type"]]
        store.run(f"MATCH (x:{label}) WHERE x.{pk} = $id "
                  "SET x.updated_at = $ua, x.rev = $rev, x.sync_seq = $seq",
                  {"id": node["id"], "ua": w["ua"], "rev": int(w["rev"]),
                   "seq": revstamp.next_seq(store, owner)})
    for nid, label, fields, rev, ua in base_updates:
        set_base(store, nid, label, fields, rev, ua)

    # 삭제 전파(S4): 인바운드 tombstone 을 적용한다. 단 **노드가 삭제보다 나중에
    # 편집됐으면**(updated_at > deleted_at) 삭제를 무시한다(다른 기기의 재편집이 이김).
    # 적용 시 자기 tombstone 을 남겨(put_tombstone) 다른 기기로 재전파한다.
    for t in bundle.get("tombstones") or []:
        tid, tlabel = t.get("node_id"), t.get("label")
        t_da = t.get("deleted_at") or ""
        pk = {"Concept": "concept_id", "Episode": "episode_id"}.get(tlabel or "")
        if not tid or not pk:
            continue
        cur = store.run(f"MATCH (x:{tlabel}) WHERE x.{pk} = $id RETURN x.updated_at AS ua",
                        {"id": tid})
        if cur and (cur[0].get("ua") or "") > t_da:
            continue                          # 삭제 이후 재편집됨 → 삭제 무시
        if cur:
            store.run(f"MATCH (x:{tlabel}) WHERE x.{pk} = $id DETACH DELETE x", {"id": tid})
            out["deleted"] += 1
        del_base(store, tid)
        put_tombstone(store, tid, tlabel, owner, t_da, int(t.get("rev") or 0))
        revstamp.observe_hlc(store, t_da)

    out["applied"] = len(writes)
    return out


# ── 오케스트레이터 (S2) ───────────────────────────────────────────────────────
# 로컬 사이드카가 원격(mcp.vincs.app)에 대해 pull/push 를 조율한다. 원격 호출은
# 주입(remote 객체)이라 인프로세스 스텁과 실 HTTP 양쪽으로 검증된다.
#
# 커서 2개(서로 다른 seq 공간):
#   cursor:push — **로컬** sync_seq. 이후 로컬 변경분만 push.
#   cursor:pull — **허브** sync_seq. 이후 허브 변경분만 pull.
#
# echo: pull 로 받은 노드는 로컬 seq 가 새로 매겨져 다음 push 에 실릴 수 있으나,
# updated_at/rev 를 보존하므로 허브 stale 가드가 되돌림을 막는다(무손상, 헛트래픽만).
# 그 헛트래픽은 S3 의 base(로컬≠base 일 때만 push)가 완전히 없앤다.


class RemoteHTTP:
    """syncclient 를 감싼 원격 어댑터(프로덕션). 회원 키·기본 mcp.vincs.app."""

    def __init__(self, token: str, base: str | None = None):
        self.token = token
        self.base = base or "https://mcp.vincs.app"

    def pull(self, since: int) -> dict:
        from vinc_mcp import syncclient
        return syncclient.pull(since=since, token=self.token, base=self.base)

    def push(self, chunks: list[dict]) -> dict:
        from vinc_mcp import syncclient
        return syncclient.push(chunks, token=self.token, base=self.base)


def push_local(store, cfg, *, owner: str, remote) -> dict:
    """로컬 delta 를 원격에 push. 커서는 **push 성공 후에만** 전진(중단 시 재전송).

    push 성공 후 보낸 노드의 **base 를 로컬 현재값으로** 갱신한다(S3). 이유: base 는
    '마지막으로 원격과 합의한 값'이라야 3-way 가 이후 로컬 편집만 delta 로 잡는다.
    안 하면 로컬-only 편집이 매 sync 재전송되거나(에코), push 후·pull 전 편집이 조상에
    섞여 유실될 수 있다."""
    since = get_cursor(store, "push")
    bundle = export_delta(owner=owner, since=since, cfg=cfg, store=store)
    if not bundle.get("executed"):
        return {"phase": "push", "executed": False, "note": bundle.get("note")}
    nodes = bundle.get("nodes") or []
    tombs = bundle.get("tombstones") or []
    if not nodes and not (bundle.get("edges") or []) and not tombs:
        return {"phase": "push", "executed": True, "sent": 0, "deletes": 0, "cursor": since}
    res = remote.push(chunk_bundle(bundle))
    for n in nodes:                       # base := 방금 보낸(=원격과 합의된) 값
        label = _SYNC_TYPES.get(n.get("type") or "")
        if label:
            set_base(store, n["id"], label, _snapshot(label, n),
                     int(n.get("_rev") or 0), n.get("_updated_at") or "")
    set_cursor(store, "push", bundle["next_cursor"])
    return {"phase": "push", "executed": True, "sent": len(nodes), "deletes": len(tombs),
            "remote": res, "cursor": bundle["next_cursor"]}


def pull_apply(store, cfg, *, owner: str, remote) -> dict:
    """원격 delta 를 페이지 단위로 받아 apply. 커서는 **apply·전진이 내구된 뒤에만**."""
    since = get_cursor(store, "pull")
    applied = skipped = deleted = pages = 0
    warnings: list[str] = []
    while pages < 1000:                       # 안전 상한(무한루프 방어)
        page = remote.pull(since)
        nodes = page.get("nodes") or []
        tombs = page.get("tombstones") or []
        r = apply_delta(owner=owner, bundle=page, cfg=cfg, store=store)
        applied += r["applied"]
        skipped += r["skipped_stale"]
        deleted += r.get("deleted", 0)
        warnings += r.get("warnings") or []
        pages += 1
        nxt = int(page.get("next_cursor", since))
        advanced = nxt > since
        if advanced:
            since = nxt
            set_cursor(store, "pull", since)   # 페이지별 내구 전진
        if (not nodes and not tombs) or not advanced:
            break
    return {"phase": "pull", "applied": applied, "skipped_stale": skipped,
            "deleted": deleted, "pages": pages, "cursor": since, "warnings": warnings}


def run(store, cfg, *, owner: str, remote) -> dict:
    """한 동기화 세션: **pull 먼저**(원격 최신을 받아 병합 후 push — lost-update 창
    최소, 플랜 §6-1) → push. 잡 워커에서 돌아 로컬 쓰기가 단일 writer 로 직렬화된다."""
    pu = pull_apply(store, cfg, owner=owner, remote=remote)
    ph = push_local(store, cfg, owner=owner, remote=remote)
    return {"executed": True, "pull": pu, "push": ph}
