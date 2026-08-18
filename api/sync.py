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
# `content_time`(D72)은 **저작된 값**이라 여기 든다 — 사용자가 고칠 수 있으므로 두 기기가
# 정당하게 다를 수 있고, 그러면 노드 타이브레이크가 판단할 일이다(제목·도메인과 같다).
# 열이 있는 라벨에만 넣는다: Concept 뿐이다(Decision·Episode 에는 열이 없다 — 2026-08-17
# 실엔진 확인. 넣으면 `_local_snapshot` 의 SELECT 가 Binder 로 죽는다).
# ⚠ `created`(출생)는 **여기 넣지 않는다.** 편집이 아니라 3-way 병합의 대상이 아니고,
# 규칙이 다르다(아래 `_reconcile_birth` — 가장 이른 값이 이긴다).
SYNC_FIELDS: dict[str, tuple[str, ...]] = {
    "Concept": ("title", "domain", "tags", "props", "content_time"),
    "Episode": ("title", "date", "domain", "summary", "tags"),
    # v12 — 결정. **빼면 안 된다**: 결정은 여태 Concept 이었고 그래서 동기화되고
    # 있었다. 라벨만 옮기고 여기 안 넣으면 기기 간에 결정이 조용히 안 넘어간다
    # (오류도 경고도 없이 — 다른 기기에서 결정이 통째로 사라진 것처럼 보인다).
    "Decision": ("title", "domain", "tags", "props"),
}
# fragment 노드 타입 → 라벨(export/apply 대상). Bridge/Entity 제외는 이제 왕복 결함이
# 아니라 **스코프 결정**(F1 포팅으로 Entity 필드는 왕복 가능; Bridge 구조 필드
# source/target_domain 은 여전히 fragment 미기록) — 편입은 후속 과제.
_SYNC_TYPES = {"concept": "Concept", "episode": "Episode", "decision": "Decision"}
#: 노드 툼스톤 전파 대상 라벨 — 동기화 라벨과 같아야 한다(따로 적으면 어긋난다).
_SYNC_LABELS = tuple(_SYNC_TYPES.values())
#: 라벨 → PK 열. fragment 의 타입 표에서 파생한다(중복 정의 회피).
_SYNC_PK = {lbl: frag._NODE_MAP[t][1] for t, lbl in _SYNC_TYPES.items()}


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


def put_edge_tombstone(store, src: str, rel: str, dst: str, owner: str,
                       deleted_at: str) -> None:
    """수신한 엣지 삭제를 재전파용으로 남긴다(노드 put_tombstone 동형 — deleted_at
    원본 보존, sync_seq 는 수신측 자기 번호)."""
    store.run("MERGE (t:_EdgeTombstone {ekey: $k}) SET t.src = $s, t.rel = $r, "
              "t.dst = $d, t.owner_id = $o, t.deleted_at = $da, t.sync_seq = $seq, "
              "t.origin = 'remote'",
              {"k": f"{src}|{rel}|{dst}", "s": src, "r": rel, "d": dst, "o": owner,
               "da": deleted_at, "seq": revstamp.next_seq(store, owner)})


def _edge_tombstones_delta_plan(*, owner: str, since: int) -> q.QueryPlan:
    """owner 의 엣지 툼스톤 중 sync_seq > since — 동기화 rel(CC 4종+ABOUT)만."""
    rels = sorted(q.SYNC_EDGE_RELS)
    where = ["t.owner_id = $owner", f"t.rel IN {json.dumps(rels)}"]
    params: dict = {"owner": owner}
    if since > 0:
        where.append("t.sync_seq > $since")
        params["since"] = since
    cypher = (
        f"MATCH (t:_EdgeTombstone) WHERE {' AND '.join(where)} "
        "RETURN t.src AS src, t.rel AS rel, t.dst AS dst, "
        "t.deleted_at AS deleted_at, t.sync_seq AS sync_seq ORDER BY t.sync_seq"
    )
    return q.QueryPlan("sync_edge_tombstones", cypher, params,
                       note="edge deletion markers for delta (S4 확장)")


def _tombstones_delta_plan(*, owner: str, since: int) -> q.QueryPlan:
    """owner 의 tombstone 중 sync_seq > since(최초 sync 면 since=0 전량). 동기화 라벨만
    (_SYNC_LABELS) — 그 외 라벨 삭제는 전파할 곳이 없다."""
    where = ["t.owner_id = $owner", "t.label IN $labels"]
    params: dict = {"owner": owner, "labels": list(_SYNC_LABELS)}
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
    n = 0
    for label in ("_Tombstone", "_EdgeTombstone"):   # 엣지 표식도 같은 지평(S4 확장)
        cur = store.run(f"MATCH (t:{label}) WHERE t.deleted_at < $c AND t.deleted_at <> '' "
                        "RETURN count(t) AS c", {"c": cutoff})
        k = int(cur[0].get("c") or 0) if cur else 0
        if k:
            store.run(f"MATCH (t:{label}) WHERE t.deleted_at < $c AND t.deleted_at <> '' "
                      "DELETE t", {"c": cutoff})
        n += k
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
        # D72 두 시간열. 안 실으면 수신 기기가 자기 시각으로 출생을 새로 찍어 같은
        # 노드가 기기마다 다른 생일을 갖는다(2026-08-17 두 스토어 실측: 1초 차이).
        # 문서 경로(api/docsync)는 이미 같은 이유로 둘을 싣고 있었다 — 노드만 빠져 있었다.
        "c.created AS created, c.content_time AS content_time, "
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


def _decisions_delta_plan(*, owner: str, domains: list[str], since: int) -> q.QueryPlan:
    """선택 도메인 안에서 `sync_seq > since` 인 Decision(v12). Concept 플랜과 동형."""
    where = ["d.owner_id = $owner", "d.domain IN $domains"]
    params: dict = {"owner": owner, "domains": domains}
    if since > 0:
        where.append("d.sync_seq > $since")
        params["since"] = since
    cypher = (
        f"MATCH (d:Decision) WHERE {' AND '.join(where)} "
        "RETURN d.decision_id AS id, d.title AS title, d.domain AS domain, "
        "d.tags AS tags, d.props AS props, d.updated_at AS updated_at, "
        # `created` 만 — Decision 에는 `content_time` 열이 **없다**(v12 가 라이브 68건
        # 실측으로 빈 열을 안 만들었다; 2026-08-17 재확인). 실으면 Binder 로 죽는다.
        "d.created AS created, "
        "d.rev AS rev, d.sync_seq AS sync_seq ORDER BY d.sync_seq"
    )
    return q.QueryPlan("sync_decisions", cypher, params, note="owner+domain scoped delta")


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
            # 내용 시점은 **본문**으로(3-way 병합 대상), 출생은 **스탬프**로(_ 접두 —
            # 병합하지 않고 `_reconcile_birth` 가 따로 처리한다).
            "content_time": r.get("content_time") or "",
            "_rev": int(r.get("rev") or 0), "_updated_at": r.get("updated_at") or "",
            "_created": r.get("created") or "",
            "_seq": int(r.get("sync_seq") or 0)}


def _node_from_decision(r: dict) -> dict:
    props = {}
    if r.get("props"):
        try:
            props = json.loads(r["props"]) or {}
        except (TypeError, ValueError):
            props = {}
    return {"id": r["id"], "type": "decision", "title": r.get("title") or r["id"],
            "domain": r.get("domain") or "", "tags": list(r.get("tags") or []),
            "props": props,
            "_rev": int(r.get("rev") or 0), "_updated_at": r.get("updated_at") or "",
            "_created": r.get("created") or "",
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
    d_res = q.execute(_decisions_delta_plan(owner=owner, domains=sel, since=since),
                      cfg, store=store)

    c_rows = c_res.get("rows", [])
    e_rows = e_res.get("rows", []) if e_res.get("executed") else []
    d_rows = d_res.get("rows", []) if d_res.get("executed") else []
    nodes = ([_node_from_concept(r) for r in c_rows]
             + [_node_from_episode(r) for r in e_rows]
             + [_node_from_decision(r) for r in d_rows])

    # 엣지: **양끝이 이번 번들 안에 있는 것만**. fragment 는 같은 payload 의 노드로만
    # (from,to) 타입을 해석하므로(fragment.py `_node_types`), 끝점이 빠진 엣지는
    # 수신측에서 조용히 skip 된다 — 애초에 싣지 않는다.
    edges: list[dict] = []
    ids = {n["id"] for n in nodes}
    if ids:
        rel_res = q.execute(q.graph_rows_for_ids_plan(sorted(ids), owner=owner),
                            cfg, store=store)
        # graph_rows_for_ids_plan 은 Concept→Concept 만 본다 → 나머지 조합은 전용
        # 플랜으로 별도 조회(F4 의 Episode ABOUT, v12 의 결정 엣지). apply 측은
        # 무변경 — merge_fragment 가 타입에서 라우팅한다.
        extra_rows: list[dict] = []
        for pair in _EXTRA_EDGE_PAIRS:
            res = q.execute(_extra_edges_plan(sorted(ids), owner=owner, pair=pair),
                            cfg, store=store)
            if res.get("executed"):
                extra_rows.extend(res.get("rows", []))
        seen = set()
        for r in [*rel_res.get("rows", []), *extra_rows]:
            ftype = _REL_TO_FRAGMENT.get(r.get("rel") or "")
            tgt = r.get("target")
            if not ftype or not tgt or tgt not in ids or r["id"] not in ids:
                continue
            key = (r["id"], ftype, tgt)
            if key in seen:
                continue
            seen.add(key)
            e = {"from": r["id"], "to": tgt, "type": ftype}
            if r.get("edge_created"):
                e["created"] = r["edge_created"]
            edges.append(e)

    # 삭제 표식(S4) — 노드와 같은 커서 공간(sync_seq). 도메인 스코프는 없다(삭제된
    # 노드의 도메인을 알 수 없으니 owner 전체). 수신측이 부활 가드로 안전 적용.
    t_res = q.execute(_tombstones_delta_plan(owner=owner, since=since), cfg, store=store)
    tombstones = [{"node_id": r["node_id"], "label": r["label"],
                   "deleted_at": r.get("deleted_at") or "", "rev": int(r.get("rev") or 0),
                   "_seq": int(r.get("sync_seq") or 0)}
                  for r in (t_res.get("rows", []) if t_res.get("executed") else [])]

    # 엣지 삭제 표식(S4 확장) — 노드 툼스톤과 같은 커서 공간(sync_seq).
    et_res = q.execute(_edge_tombstones_delta_plan(owner=owner, since=since),
                       cfg, store=store)
    edge_tombstones = [{"src": r["src"], "rel": r["rel"], "dst": r["dst"],
                        "deleted_at": r.get("deleted_at") or "",
                        "_seq": int(r.get("sync_seq") or 0)}
                       for r in (et_res.get("rows", []) if et_res.get("executed") else [])]

    seqs = ([n["_seq"] for n in nodes] + [t["_seq"] for t in tombstones]
            + [t["_seq"] for t in edge_tombstones])
    nxt = max([since] + seqs) if seqs else since
    base.update({"executed": True, "nodes": nodes, "edges": edges,
                 "tombstones": tombstones, "edge_tombstones": edge_tombstones,
                 "next_cursor": nxt,
                 "counts": {"concepts": len(c_rows), "episodes": len(e_rows),
                            "edges": len(edges), "tombstones": len(tombstones),
                            "edge_tombstones": len(edge_tombstones)}})
    return base


# share.py 의 역매핑을 재사용(중복 정의 회피). + F4: ABOUT(Episode→Concept) 편입 —
# _EC_EDGES 는 _CC_EDGES 와 분리 유지(여기 역맵이 Concept 엣지 export 전용이라는
# 전제를 오염시키지 않기 위함), 합류는 이 지점에서만.
_REL_TO_FRAGMENT = {rel: ft for ft, rel in frag._CC_EDGES.items()}
_REL_TO_FRAGMENT.update({rel: ft for ft, rel in frag._EC_EDGES.items()})


#: `graph_rows_for_ids_plan` 이 **못 보는** 끝점 조합 — 그 플랜은 `(c:Concept)-[r]->
#: (d:Concept)` 라 소스가 Concept 이 아니거나 도착이 Concept 이 아니면 안 걸린다.
#: 여기 빠진 조합의 엣지는 번들에 안 실리고, **오류 없이** 기기 간에 사라진다
#: (F4 가 Episode→Concept 에서 겪은 결함이고, v12 의 결정 엣지가 같은 자리다).
_EXTRA_EDGE_PAIRS: tuple[tuple[str, str, str, str], ...] = (
    ("Episode", "episode_id", "Concept", "concept_id"),      # F4
    ("Episode", "episode_id", "Decision", "decision_id"),    # v12
    ("Decision", "decision_id", "Concept", "concept_id"),
    ("Decision", "decision_id", "Decision", "decision_id"),
    ("Concept", "concept_id", "Decision", "decision_id"),
)


def _extra_edges_plan(ids: list[str], *, owner: str, pair: tuple) -> "q.QueryPlan":
    """번들 내 비-(Concept→Concept) 엣지. 양끝 모두 번들 id 집합 + owner 스코프.

    rel 라벨을 좁히지 않고 `label(r)` 로 뽑는다 — 동기화 대상 여부는 호출부의
    `_REL_TO_FRAGMENT` 가 가린다(IN_DOMAIN 처럼 매핑 없는 것은 거기서 떨어진다).
    삭제 전파는 엣지 툼스톤(_EdgeTombstone, S4 확장)이 담당."""
    sl, sp, dl, dp = pair
    cypher = (
        f"MATCH (a:{sl})-[r]->(b:{dl}) "
        f"WHERE a.{sp} IN $ids AND b.{dp} IN $ids "
        "AND a.owner_id = $owner AND b.owner_id = $owner "
        f"RETURN a.{sp} AS id, label(r) AS rel, b.{dp} AS target, "
        # v14 — 엣지 출생도 나른다. 안 실으면 수신측에서 그 엣지는 신규라
        # **그 기기 시각으로 다시 태어난다** — 노드에서 방금 고친 것과 같은 병이다.
        "r.created AS edge_created"
    )
    return q.QueryPlan(f"sync_edges_{sl}_{dl}", cypher, {"ids": ids, "owner": owner},
                       note=f"{sl}->{dl} edges for sync bundle")


# ── 청킹 ──────────────────────────────────────────────────────────────────────
def chunk_bundle(bundle: dict, *, limit: int = CHUNK_BYTES) -> list[dict]:
    """번들을 전송 가능한 크기로 쪼갠다. **각 청크는 자기완결** — 엣지는 양끝 노드가
    같은 청크에 있을 때만 싣는다(수신측 fragment 가 payload 내 노드로만 타입 해석).
    끝점이 갈라진 엣지는 마지막 청크에 양끝 노드를 다시 실어 함께 보낸다."""
    nodes = bundle.get("nodes") or []
    edges = bundle.get("edges") or []
    tombs = bundle.get("tombstones") or []
    etombs = bundle.get("edge_tombstones") or []
    if not nodes:
        # 노드가 없어도 엣지·tombstone 은 실어 보낸다(삭제-only sync).
        if not edges and not tombs and not etombs:
            return []
        return [{"nodes": [], "edges": edges, "tombstones": tombs,
                 "edge_tombstones": etombs}]

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
    # tombstone(노드·엣지)은 작아 첫 청크에 전부 싣는다(한 번만 적용되게).
    if tombs:
        chunks[0]["tombstones"] = tombs
    if etombs:
        chunks[0]["edge_tombstones"] = etombs
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


#: 출생(`created`) 열을 가진 동기화 라벨. Episode 에는 그 열이 **없다**(2026-08-17
#: 실엔진 확인) — 넣으면 아래 문장이 Binder 로 죽는다.
_BIRTH_LABELS = ("Concept", "Decision")


def _reconcile_birth(store, label: str, pk: str, nid: str, born: str, owner: str) -> None:
    """노드 출생 시각을 **가장 이른 값으로** 수렴시킨다.

    왜 3-way 병합이 아닌가: 출생은 편집이 아니라서 "충돌"이 뜻을 갖지 않는다. min 은
    교환·결합·멱등이라 어느 순서로 동기화하든 모든 기기가 같은 값에 닿고 타이브레이크가
    필요 없다. 뜻도 정직하다 — 어느 기기든 이 노드를 **처음 본 시각**이 그 노드가 이
    공간에 들어온 때의 최선 추정이다. 빈 값은 이기지 못하므로, 출생을 모르는 기기는
    들은 값을 받아들인다.

    **모든 인바운드 노드에** 부른다(쓰기가 일어난 것만이 아니라). 병합 결과가 로컬과
    같으면(`skipped_stale`) 쓰기 자체가 없는데, 그때 건너뛰면 이미 갈라진 출생이 영영
    만나지 못한다.

    ⚠ `IS NULL` 은 방어적 군더더기가 아니다: 문서 추출 경로가 추적열을 안 찍던 시절의
    행이 NULL 을 갖고 있다(af7a973 이전). owner 술어도 필수다 — merge_fragment 의
    SEC-3 가드가 교차테넌트 노드를 건너뛰어도 여기서 id 로만 MATCH 하면 남의 행을
    고친다(risk-audit 2026-08-05 §3.2, 위 스탬프 루프와 같은 이유)."""
    if not born or label not in _BIRTH_LABELS:
        return
    store.run(
        f"MATCH (x:{label}) WHERE x.{pk} = $id AND x.owner_id = $owner "
        "AND (x.created IS NULL OR x.created = '' OR x.created > $born) "
        "SET x.created = $born",
        {"id": nid, "owner": owner, "born": born})


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
    births: list[tuple] = []         # (label, pk, nid, remote_created) — 병합과 무관
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

        # 삭제가 이긴 노드는 위에서 이미 continue 했다 — 지운 노드의 열을 되살리지
        # 않으려고 여기서 모은다(부활한 경우는 살아났으므로 포함이 맞다).
        births.append((label, pk, nid, n.get("_created") or ""))

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
        # stamp_edges=False — 원격발 엣지를 끝점 스탬프하면 다음 push 로 무변경 노드가
        # 에코된다(엣지 전파 스탬프는 **원 저작 기기**에서만, fragment 주석 참조).
        res = frag.merge_fragment(
            {"owner": owner, "nodes": [w["node"] for w in writes],
             "edges": bundle.get("edges") or []}, store=store, stamp_edges=False)
        out["executed"] = bool(res.get("executed"))
        out["edges"] = (res.get("merged") or {}).get("edges", {}).get("created", 0)
        out["warnings"] += res.get("warnings") or []
    else:
        out["executed"] = True

    # merge_fragment 가 방금 로컬 스탬프를 찍었으므로, 결정된 스탬프로 덮어쓴다.
    # owner 술어 필수(risk-audit 2026-08-05 §3.2): merge_fragment 의 SEC-3 가드가
    # 교차테넌트 노드를 건너뛰어도 이 루프가 owner 없이 id 로만 MATCH 하면 그 노드의
    # rev/sync_seq 를 그대로 찍어 버려 피해자의 3-way 병합 기준을 조용히 오염시킨다.
    for w in writes:
        node = w["node"]
        label, pk = frag._NODE_MAP[node["type"]]
        store.run(f"MATCH (x:{label}) WHERE x.{pk} = $id AND x.owner_id = $owner "
                  "SET x.updated_at = $ua, x.rev = $rev, x.sync_seq = $seq",
                  {"id": node["id"], "owner": owner, "ua": w["ua"], "rev": int(w["rev"]),
                   "seq": revstamp.next_seq(store, owner)})
    # 출생 화해는 **스탬프 루프 뒤**다: 신규 노드는 방금 merge_fragment 가 수신측
    # 시각으로 `created` 를 찍었으므로(fragment `_merge_node`), 그 위에 min 을 얹어야
    # 원본의 출생이 남는다.
    for label, pk, nid, born in births:
        _reconcile_birth(store, label, pk, nid, born, owner)
    for nid, label, fields, rev, ua in base_updates:
        set_base(store, nid, label, fields, rev, ua)

    # 삭제 전파(S4): 인바운드 tombstone 을 적용한다. 단 **노드가 삭제보다 나중에
    # 편집됐으면**(updated_at > deleted_at) 삭제를 무시한다(다른 기기의 재편집이 이김).
    # 적용 시 자기 tombstone 을 남겨(put_tombstone) 다른 기기로 재전파한다.
    # owner 술어 필수(risk-audit §3.2): 없으면 유효한 멤버 키 하나로 남의 concept 을
    # 지울 수 있다. deleted_at 은 clamp — 인바운드 값이라 공격자가 미래로 조작해
    # 어떤 재편집도 이기게 만들 수 있으니 서버 클록을 넘지 못하게 한다.
    for t in bundle.get("tombstones") or []:
        tid, tlabel = t.get("node_id"), t.get("label")
        t_da = t.get("deleted_at") or ""
        if t_da:
            t_da = min(t_da, revstamp.hlc_now(store))
        # 라벨→PK 를 손으로 적지 않는다 — 동기화 라벨이 늘 때 여기가 빠지면 그
        # 라벨의 삭제가 조용히 전파되지 않는다(v12 Decision 이 그 자리였다).
        pk = _SYNC_PK.get(tlabel or "")
        if not tid or not pk:
            continue
        cur = store.run(f"MATCH (x:{tlabel}) WHERE x.{pk} = $id AND x.owner_id = $owner "
                        "RETURN x.updated_at AS ua", {"id": tid, "owner": owner})
        if cur and (cur[0].get("ua") or "") > t_da:
            continue                          # 삭제 이후 재편집됨 → 삭제 무시
        if cur:
            store.run(f"MATCH (x:{tlabel}) WHERE x.{pk} = $id AND x.owner_id = $owner "
                      "DETACH DELETE x", {"id": tid, "owner": owner})
            out["deleted"] += 1
        del_base(store, tid)
        put_tombstone(store, tid, tlabel, owner, t_da, int(t.get("rev") or 0))
        revstamp.observe_hlc(store, t_da)

    # 엣지 삭제 전파(S4 확장): 엣지는 타임스탬프가 없어 노드식 재편집 가드가 불가 —
    # 삭제를 무조건 적용한다. '재생성이 삭제를 이긴다'는 별도 경로로 성립:
    # 재생성 기기가 끝점을 스탬프(fragment._stamp_edge_endpoints — 로컬 엣지 툼스톤
    # 상쇄 포함)해 다음 delta 에 엣지가 실려 되돌아온다.
    # owner 술어 필수(risk-audit §3.2): 양 끝 모두 owner 스코프 — 안 그러면 남의
    # 관계를 지울 수 있다. _extra_edges_plan 과 같은 술어 패턴.
    for t in bundle.get("edge_tombstones") or []:
        src, rel, dst = t.get("src"), t.get("rel"), t.get("dst")
        ep = q.SYNC_EDGE_RELS.get(rel or "")
        if not ep or not src or not dst:
            continue
        pairs = [(src, dst)]
        if rel == "RELATED_TO":   # 무방향 의미 — 물리 역방향도 함께(delete_edge 동형)
            pairs.append((dst, src))
        # 끝점 라벨 조합 전수(v12 — rel 하나에 조합이 여럿). delete_edge 와 동형.
        for sl, sp, dl, dp in ep:
            for s_, d_ in pairs:
                cnt = store.run(f"MATCH (a:{sl})-[r:{rel}]->(b:{dl}) "
                                f"WHERE a.{sp} = $s AND b.{dp} = $d "
                                "AND a.owner_id = $owner AND b.owner_id = $owner "
                                "RETURN count(r) AS c",
                                {"s": s_, "d": d_, "owner": owner})
                if cnt and int(cnt[0].get("c") or 0):
                    store.run(f"MATCH (a:{sl})-[r:{rel}]->(b:{dl}) "
                              f"WHERE a.{sp} = $s AND b.{dp} = $d "
                              "AND a.owner_id = $owner AND b.owner_id = $owner DELETE r",
                              {"s": s_, "d": d_, "owner": owner})
                    out["edges_deleted"] = out.get("edges_deleted", 0) + 1
        put_edge_tombstone(store, src, rel, dst, owner, t.get("deleted_at") or "")
        revstamp.observe_hlc(store, t.get("deleted_at") or "")

    out["applied"] = len(writes)
    return out


# ── 오케스트레이터 (S2) ───────────────────────────────────────────────────────
# 로컬 사이드카가 원격(mcp.vincs.io)에 대해 pull/push 를 조율한다. 원격 호출은
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
    """syncclient 를 감싼 원격 어댑터(프로덕션). 회원 키·기본 mcp.vincs.io."""

    def __init__(self, token: str, base: str | None = None):
        self.token = token
        self.base = base or "https://mcp.vincs.io"

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


#: 일회성 전량 왕복 표식. 다음에 또 한 번이 필요하면 **키 이름을 올린다**(v2) —
#: 이력이 남고, 이미 돈 기기가 다시 돌지 않는다.
_ONESHOT_KEY = "oneshot:birth-resync-v1"


def ensure_birth_resync(store) -> bool:
    """이 스토어에서 **딱 한 번**, 커서 둘을 0 으로 되돌린다. 되돌렸으면 True.

    왜 필요한가: `_reconcile_birth` 는 새 발산을 막을 뿐, **이미 갈라진 행은 못 고친다**
    — delta 는 커서 이후 변경분만 나르는데 그 노드들은 안 변했다. 그래서 한 번은
    전량을 오가게 해야 한다.

    왜 커서 리셋인가(전용 '출생 교환' 프로토콜 대신): 전량 왕복은 **최초 동기화가
    이미 하는 일**이라 코드도 배선도 새로 필요 없다. 값이 실제로 바뀌는 노드가 없으면
    apply 는 3-way 에서 `skipped_stale` 로 흘러가고 쓰기는 `_reconcile_birth` 뿐이다.

    왜 **둘 다** 0 인가: 수렴이 양방향이라야 한다. pull 만 0 이면 이 기기가 허브의
    이른 출생을 받아들이지만, 이 기기가 가진 더 이른 출생은 허브로 안 간다(push 는
    안 변한 노드를 안 싣는다). `run` 이 pull → push 순이므로 한 세션에 둘 다 돈다:
    허브 것을 받아 min 을 취하고, 그 결과를 도로 올린다.

    표식은 **세션이 끝난 뒤** 찍는다(`run`). 중간에 죽으면 커서가 0 인 채 남아 다음
    세션이 그대로 다시 한다 — 멱등이라 재실행이 해롭지 않다."""
    if revstamp._meta_get(store, _ONESHOT_KEY, "") == "done":
        return False
    set_cursor(store, "push", 0)
    set_cursor(store, "pull", 0)
    return True


def run(store, cfg, *, owner: str, remote) -> dict:
    """한 동기화 세션: **pull 먼저**(원격 최신을 받아 병합 후 push — lost-update 창
    최소, 플랜 §6-1) → push. 잡 워커에서 돌아 로컬 쓰기가 단일 writer 로 직렬화된다."""
    resynced = ensure_birth_resync(store)
    pu = pull_apply(store, cfg, owner=owner, remote=remote)
    ph = push_local(store, cfg, owner=owner, remote=remote)
    if resynced:
        # 여기까지 왔으면 전량이 양방향으로 한 번 오갔다 — 다시 할 이유가 없다.
        revstamp._meta_set(store, _ONESHOT_KEY, "done")
    return {"executed": True, "pull": pu, "push": ph, "birth_resync": resynced}
