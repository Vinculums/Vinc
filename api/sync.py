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
from . import query as q

# 청크 상한 — 원격 `MAX_BODY`(4MB, vinc_mcp/http_server.py)보다 넉넉히 아래.
# 초과 시 서버가 빈 바디로 400 을 내므로 조용히 실패한다 → 여유를 크게 둔다.
CHUNK_BYTES = 3 * 1024 * 1024

_SEL_KEY = "selected_domains"


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
    props = {}
    if r.get("topic"):
        props["topic"] = r["topic"]
    if r.get("one_liner"):
        props["one_liner"] = r["one_liner"]
    if r.get("props"):
        try:
            props.update(json.loads(r["props"]))
        except (TypeError, ValueError):
            pass
    n = {"id": r["id"], "type": "concept", "title": r.get("title") or r["id"],
         "domain": r.get("domain") or "", "tags": list(r.get("tags") or []),
         "_rev": int(r.get("rev") or 0), "_updated_at": r.get("updated_at") or "",
         "_seq": int(r.get("sync_seq") or 0)}
    if props:
        n["props"] = props
    return n


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

    nxt = max([since] + [n["_seq"] for n in nodes]) if nodes else since
    base.update({"executed": True, "nodes": nodes, "edges": edges, "next_cursor": nxt,
                 "counts": {"concepts": len(c_rows), "episodes": len(e_rows),
                            "edges": len(edges)}})
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
    if not nodes:
        return [{"nodes": [], "edges": []}] if not edges else []

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
    return chunks


# ── apply ─────────────────────────────────────────────────────────────────────
def _strip_meta(node: dict) -> dict:
    return {k: v for k, v in node.items() if not k.startswith("_")}


def _is_stale(cur_row: dict | None, node: dict) -> bool:
    """저장본이 인바운드보다 **새롭거나 같으면** True → 적용 안 함.

    두 일을 동시에 한다:
      - **LWW**: 늦게 도착한 옛 변경이 최신을 덮지 못하게(updated_at 우선, 동률 시 rev).
      - **에코 차단**: 같은 버전이 되돌아오면(updated_at·rev 동일) 무연산 → 무한
        왕복이 끊긴다. `>` 가 아니라 `>=` 인 이유가 이것이다.
    필드단위 3-way 병합은 **클라이언트**가 push 전에 끝낸다(S3) — 허브는 LWW 스토어."""
    if not cur_row:
        return False
    cu, nu = cur_row.get("updated_at") or "", node.get("_updated_at") or ""
    if cu != nu:
        return cu > nu                    # HLC ISO 는 고정폭 → 사전순 = 시간순
    return int(cur_row.get("rev") or 0) >= int(node.get("_rev") or 0)


def apply_delta(*, owner: str, bundle: dict, cfg=None, store=None) -> dict:
    """delta 번들을 이 스토어에 적용(LWW).

    스탬프 처리가 급소다:
      - `updated_at`/`rev` 는 **콘텐츠 버전 정체성** → 보낸 쪽 값을 그대로 보존한다.
        재스탬프하면 수신 즉시 '새 로컬 변경'이 되어 되돌아간다(무한 왕복).
      - `sync_seq` 는 **저장소별 전송 커서** → 보낸 쪽 번호는 이 스토어의 번호 공간에서
        의미가 없다. **받는 쪽이 자기 카운터로 새로 매긴다** — 그래야 이 스토어를
        pull 하는 다른 기기의 커서가 성립한다.
    merge_fragment 가 변경감지로 로컬 스탬프를 찍으므로, 적용 후 위 규칙대로 덮어쓴다."""
    out = {"executed": False, "applied": 0, "skipped_stale": 0, "edges": 0, "warnings": []}
    if store is None:
        out["note"] = "no reachable store"
        return out

    fresh: list[dict] = []
    for n in bundle.get("nodes") or []:
        nid, ntype = n.get("id"), n.get("type")
        label_pk = frag._NODE_MAP.get(ntype or "")
        if not nid or not label_pk:
            out["warnings"].append(f"unsupported node — skipped ({nid or '?'})")
            continue
        label, pk = label_pk
        cur = store.run(f"MATCH (x:{label}) WHERE x.{pk} = $id "
                        "RETURN x.rev AS rev, x.updated_at AS updated_at", {"id": nid})
        if _is_stale(cur[0] if cur else None, n):
            out["skipped_stale"] += 1
            continue
        fresh.append(n)

    if fresh or bundle.get("edges"):
        res = frag.merge_fragment(
            {"owner": owner, "nodes": [_strip_meta(n) for n in fresh],
             "edges": bundle.get("edges") or []}, store=store)
        out["executed"] = bool(res.get("executed"))
        out["edges"] = (res.get("merged") or {}).get("edges", {}).get("created", 0)
        out["warnings"] += res.get("warnings") or []
    else:
        out["executed"] = True

    for n in fresh:
        label, pk = frag._NODE_MAP[n["type"]]
        store.run(
            f"MATCH (x:{label}) WHERE x.{pk} = $id "
            "SET x.updated_at = $ua, x.rev = $rev, x.sync_seq = $seq",
            {"id": n["id"], "ua": n.get("_updated_at") or "",
             "rev": int(n.get("_rev") or 0),
             "seq": revstamp.next_seq(store, owner)})   # ← 수신측 자기 번호
        revstamp.observe_hlc(store, n.get("_updated_at") or "")
    out["applied"] = len(fresh)
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
    """로컬 delta 를 원격에 push. 커서는 **push 성공 후에만** 전진(중단 시 재전송)."""
    since = get_cursor(store, "push")
    bundle = export_delta(owner=owner, since=since, cfg=cfg, store=store)
    if not bundle.get("executed"):
        return {"phase": "push", "executed": False, "note": bundle.get("note")}
    nodes = bundle.get("nodes") or []
    if not nodes and not (bundle.get("edges") or []):
        return {"phase": "push", "executed": True, "sent": 0, "cursor": since}
    res = remote.push(chunk_bundle(bundle))
    set_cursor(store, "push", bundle["next_cursor"])
    return {"phase": "push", "executed": True, "sent": len(nodes),
            "remote": res, "cursor": bundle["next_cursor"]}


def pull_apply(store, cfg, *, owner: str, remote) -> dict:
    """원격 delta 를 페이지 단위로 받아 apply. 커서는 **apply·전진이 내구된 뒤에만**."""
    since = get_cursor(store, "pull")
    applied = skipped = pages = 0
    warnings: list[str] = []
    while pages < 1000:                       # 안전 상한(무한루프 방어)
        page = remote.pull(since)
        nodes = page.get("nodes") or []
        r = apply_delta(owner=owner, bundle=page, cfg=cfg, store=store)
        applied += r["applied"]
        skipped += r["skipped_stale"]
        warnings += r.get("warnings") or []
        pages += 1
        nxt = int(page.get("next_cursor", since))
        advanced = nxt > since
        if advanced:
            since = nxt
            set_cursor(store, "pull", since)   # 페이지별 내구 전진
        if not nodes or not advanced:
            break
    return {"phase": "pull", "applied": applied, "skipped_stale": skipped,
            "pages": pages, "cursor": since, "warnings": warnings}


def run(store, cfg, *, owner: str, remote) -> dict:
    """한 동기화 세션: **pull 먼저**(원격 최신을 받아 병합 후 push — lost-update 창
    최소, 플랜 §6-1) → push. 잡 워커에서 돌아 로컬 쓰기가 단일 writer 로 직렬화된다."""
    pu = pull_apply(store, cfg, owner=owner, remote=remote)
    ph = push_local(store, cfg, owner=owner, remote=remote)
    return {"executed": True, "pull": pu, "push": ph}
