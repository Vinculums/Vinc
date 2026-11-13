# -*- coding: utf-8 -*-
"""그래프 쓰기: 도메인 개명, 엣지·노드 삭제와 툼스톤, 개념 병합, 소유자 전체 삭제, 그리고 소유자별 수.

모두 비가역이거나 소유자를 가로지르는 표면이라 한곳에 둔다. 사이드카에서는 잡 큐가 이 함수들을
하나씩 돌리고(단일 writer), 소유자 삭제와 소유자별 수는 허브의 계정 서명 경로만 부른다.

`api/query.py` 가 여기 이름을 모두 다시 내보내므로 `q.X` 와 `from api.query import X` 는 그대로다.
두 이름은 같은 객체다. 여기 함수가 부르는 이름(`open_store`·`load_config` 등)은 이 모듈에서 찾으므로,
쓰기 안의 호출을 바꾸려면 `api.query` 가 아니라 이 모듈의 이름을 패치한다.

이 모듈은 `api.query` 를 import 하지 않는다. 스키마에서 온 관계·seed 표는 `api/query_base.py` 에서
읽는다(`tests/core/test_graph_edit.py` 가 둘을 본다).
"""
from __future__ import annotations

import json

from engines import domain_hierarchy
from store import pkns
from store.backend import open_store
from store.config import load_config
from store.embedded_schema import wiki_pairs_open

from . import graphmd
from .query_base import (
    SYNC_EDGE_RELS,
    WIKI_EDGE_RELS,
    _CC_RELS,
    _FRAGMENT_EDGE_TO_REL,
    _OWNER_SCOPED_LABELS,
    _SEED_TABLES,
)


# ── 삭제 / 병합 (T3c C3 — 오염 정리 쓰기 표면) ────────────────────────────────
# 실사용 사고 ③: 쓰기 표면이 MERGE 단독이라 중복·빈 껍데기 노드(concept:ipaas 등)가
# 생겨도 되돌릴 수단이 없어 오염이 영구화됐다. delete/merge 는 그 정리 수단이다 —
# 둘 다 **비가역**. 사이드카에서는 잡 큐(run_sync)로 직렬화된다(단일 writer, R4).

# 툼스톤이 읽는 라벨 → PK. `_OWNER_SCOPED_LABELS` 의 라벨마다 있어야 한다: 그 집합을
# 넓히고 여기를 안 넓히면 삭제가 KeyError 로 죽는다.
_NODE_PK = {"Concept": "concept_id", "Episode": "episode_id", "Record": "record_id",
            "Bridge": "bridge_id", "Entity": "entity_id", "Decision": "decision_id"}


def rename_domain(*, owner: str, old: str, new: str, store=None, cfg=None) -> dict:
    """도메인 일괄 변경/재배치(S2) — **owner 스코프 문자열 편집**.

    ⚠ 전역 `:Domain` 노드는 **건드리지 않는다**. Domain 은 `{name}` 뿐인 무소유
    구조 노드라 모든 사용자가 공유한다 — 지우거나 이름을 바꾸면 남의 그래프가
    깨진다. 실제 도메인 귀속은 Concept/Episode/Record 의 `domain` **문자열 필드**
    이고, 그게 `/domains` 집계의 출처다. 그래서 여기서는 내 노드의 그 필드만 바꾼다.

    - ``new`` 가 빈 문자열이면 **미분류로 내림**(= 사용자 체감의 "도메인 삭제":
      도메인이 목록에서 사라지고 노드는 남는다. 노드까지 지우려면 delete_node).
    - ``owner`` 필수 — 없으면 전 사용자 문서를 갈아버리므로 fail-closed 로 거부.
    - 변경된 노드마다 revstamp(updated_at/rev/sync_seq)를 찍어 다기기 동기화로
      전파한다(pin/tag 와 동형 — 안 찍으면 다른 기기가 바꾸기 전 도메인을 유지한다).
    """
    base: dict = {"kind": "domain.rename", "executed": False,
                  "old": old, "new": new, "moved": 0}
    if not owner:
        base["error"] = "owner required — 무스코프 도메인 변경은 전 사용자에 영향(SEC-1)"
        return base
    if not old:
        base["error"] = "old domain required"
        return base
    if old == new:
        base.update(executed=True, note="no-op (same domain)")
        return base
    cfg = cfg or load_config()
    owns = store is None
    if owns:
        store = open_store(cfg)
    if store is None:
        base["error"] = "no reachable store"
        return base
    from . import revstamp
    try:
        moved = 0
        per_label: dict[str, int] = {}
        # ⚠ 리터럴 표를 두지 말 것. v12 가 Decision 을 늘렸을 때 여기만 빠져서, 도메인을
        # 개명하면 결정은 `domain` 열도 IN_DOMAIN 엣지도 바꾸기 전 도메인에 남았다(v13 이
        # 백필 표는 고쳤지만 이 표는 못 봤다). DOMAIN_LABELS 가 `domain` 열을 가진
        # 라벨의 정본이므로 그것을 돈다 — 다음 의미층은 저절로 따라온다.
        for label, pk in domain_hierarchy.DOMAIN_LABELS.items():
            rows = store.run(
                f"MATCH (n:{label}) WHERE n.owner_id = $o AND n.domain = $old "
                f"RETURN n.{pk} AS id, n.rev AS rev", {"o": owner, "old": old}) or []
            for r in rows:
                st = revstamp.make_stamp(store, {"rev": r.get("rev"), "owner": owner})
                store.run(
                    f"MATCH (n:{label}) WHERE n.{pk} = $id AND n.owner_id = $o "
                    "SET n.domain = $new, n.updated_at = $ua, n.rev = $rev, n.sync_seq = $seq",
                    # 위 조회가 돌려준 id 는 읽기 훅이 되돌린 **논리** 슬러그다 —
                    # 다시 바인딩할 때 합성해야 한다(PK 네임스페이싱).
                    {"id": pkns.key(owner, r["id"]), "o": owner, "new": new,
                     "ua": st["updated_at"], "rev": st["rev"],
                     "seq": st["sync_seq"]})
                # 계층(E-1, v11)도 따라와야 한다. 안 옮기면 노드의 `domain` 열은 새
                # 도메인인데 IN_DOMAIN 은 바꾸기 전 도메인을 가리켜, 열 기준 집계(/domains)와
                # 그래프 기준 조회가 서로 다른 답을 준다. `new` 가 비면 엣지만 지운다
                # (= 미분류로 내림, 위 계약과 동형).
                domain_hierarchy.link_node(store, label, pkns.key(owner, r["id"]), new)
                moved += 1
            if rows:
                per_label[label] = len(rows)
        base.update(executed=True, moved=moved, by_label=per_label)
        if moved == 0:
            base["note"] = f"no nodes in domain {old!r} for this owner"
        return base
    finally:
        if owns:
            store.close()


def _write_tombstone(store, node_id: str, label: str, owner: str | None) -> None:
    """삭제 표식(S4) — 삭제 전파용. 하드삭제(DETACH DELETE)만으론 수신측이 삭제를
    영영 모른다. 삭제 직전 노드의 rev/updated_at 을 보존하고 새 deleted_at(HLC)·
    sync_seq 를 찍어 `_Tombstone` 에 남긴다. owner 스코프 라벨(=_TRACKED_LABELS 5종)
    만 — 그 외는 동기화 대상이 아니라 전파할 곳이 없다. 지연 import(순환 회피)."""
    if label not in _OWNER_SCOPED_LABELS:
        return
    from . import revstamp
    pk = _NODE_PK[label]
    rows = store.run(f"MATCH (n:{label}) WHERE n.{pk} = $id "
                     "RETURN n.rev AS rev, n.updated_at AS ua, n.owner_id AS owner",
                     {"id": pkns.key(owner, node_id)})
    if not rows:
        return
    own = owner or rows[0].get("owner") or "user:local"
    # PK 네임스페이싱 — 툼스톤 PK 도 노드 id 라 같이 합성한다(감사 M3: 안 하면 두
    # 계정이 같은 슬러그를 지울 때 **툼스톤 행 하나를 공유**해 진 쪽의 삭제가 전파를
    # 멈춘다). owner 는 위에서 노드에서 승계한 값이라 무스코프 삭제에서도 맞는다.
    store.run("MERGE (t:_Tombstone {node_id: $id}) SET t.label = $l, t.owner_id = $o, "
              "t.deleted_at = $da, t.rev = $rev, t.sync_seq = $seq, t.origin = 'local'",
              {"id": pkns.key(own, node_id), "l": label, "o": own,
               "da": revstamp.hlc_now(store), "rev": int(rows[0].get("rev") or 0),
               "seq": revstamp.next_seq(store, own)})


def sync_edge_spec(rel: str):
    """Record 삭제는 저작 타입도 보존해 다른 타입의 관계를 지우지 않는다."""
    carrier, subtype = rel, None
    if rel.startswith("ATTACHES_TO:"):
        carrier, subtype = rel.split(":", 1)
        if subtype not in {"relates_to", "prerequisite_of", "derives_from", "inherits_from"}:
            return None
    pairs = SYNC_EDGE_RELS.get(carrier)
    return (carrier, subtype, pairs) if pairs else None


def _write_edge_tombstone(store, src: str, rel: str, dst: str, owner: str | None) -> None:
    """엣지 삭제 표식(S4 확장) — 엣지는 타임스탬프가 없어 노드보다 단순하다:
    (src, rel, dst) 키와 삭제 시각(HLC)·sync_seq 만 기록. owner 는 src 노드의
    소유를 승계(무스코프 삭제 시)한다 — 노드 툼스톤과 같은 규칙."""
    from . import revstamp
    own = owner
    if not own:
        # 끝점 후보를 훑어 **id 가 실제로 있는 라벨**에서 소유를 읽는다. 라벨을 하나로
        # 가정하면 결정 끝점에서 소유가 'user:local' 로 떨어져 남의 그래프에 툼스톤이
        # 앉는다.
        for sl, sp, _dl, _dp in sync_edge_spec(rel)[2]:
            rows = store.run(f"MATCH (n:{sl}) WHERE n.{sp} = $id RETURN n.owner_id AS o",
                             {"id": pkns.key(owner, src)})
            if rows:
                own = rows[0].get("o")
                break
        own = own or "user:local"
    store.run("MERGE (t:_EdgeTombstone {ekey: $k}) SET t.src = $s, t.rel = $r, "
              "t.dst = $d, t.owner_id = $o, t.deleted_at = $da, t.sync_seq = $seq, "
              "t.origin = 'local'",
              # `ekey` 는 논리 `src|REL|dst` 를 짓고 한 번만 합성한다(pkns.ekey).
              # ⚠ `src`/`dst` **열**은 논리로 남긴다 — 이 행은 동기화 와이어로 그대로
              # 나가고, 와이어에는 U+001F 가 있어서는 안 된다(계약 테스트가 고정한다).
              {"k": pkns.ekey(own, src, rel, dst), "s": src, "r": rel, "d": dst, "o": own,
               "da": revstamp.hlc_now(store), "seq": revstamp.next_seq(store, own)})


def delete_edge(src: str, edge_type: str, dst: str, *, owner: str | None = None,
                cfg=None, store=None) -> dict:
    """엣지 1개를 삭제하고 **엣지 툼스톤**을 남긴다(S4 확장 — 삭제 전파). 대상은
    Record→Concept|Decision 부착은 원 타입으로 고르거나 ATTACHES_TO로 고른다.
    원 타입 선택의 툼스톤도 타입을 보존한다. CONNECTS·TYPED는 범위 밖이다.

    ``edge_type`` 은 fragment 타입명(relates_to·about…)과 rel 명(RELATED_TO…) 둘 다
    허용. RELATED_TO 는 의미상 무방향 — **양방향 물리 엣지를 모두** 지운다.
    owner(SEC-3): 주어지면 양 끝점을 owner_id 로 스코프 — 남의 엣지는 못 지운다."""
    cfg = cfg or load_config()
    owns = store is None
    if owns:
        store = open_store(cfg)
    rel = _FRAGMENT_EDGE_TO_REL.get((edge_type or "").strip().lower(),
                                    (edge_type or "").strip().upper())
    subtype = None
    wiki = wiki_pairs_open(owner)
    # 커뮤니티 위키는 기록 출발 관계도 rel 표에 쓴다(fragment `_merge_edge`).
    if not wiki and src.startswith("record:") and (edge_type or "").strip().lower() in {
            "relates_to", "prerequisite_of", "derives_from", "inherits_from"}:
        subtype, rel = edge_type.strip().lower(), "ATTACHES_TO"
    base = {"kind": "delete_edge", "executed": False, "found": False,
            "src": src, "rel": rel, "dst": dst}
    if store is None:
        base["note"] = base["error"] = "no reachable store"
        return base
    if rel not in SYNC_EDGE_RELS:
        base["executed"] = True
        base["note"] = (f"unsupported edge type {edge_type!r} — deletable: "
                        f"{sorted(_FRAGMENT_EDGE_TO_REL)} (sync-covered rels only)")
        return base
    scope_a = scope_b = ""
    params: dict = {"s": pkns.key(owner, src), "d": pkns.key(owner, dst)}
    qualifier = " AND r.type = $subtype" if subtype is not None else ""
    if subtype is not None:
        params["subtype"] = subtype
    if owner:
        scope_a, scope_b = " AND a.owner_id = $owner", " AND b.owner_id = $owner"
        params["owner"] = owner
    try:
        pairs = [(src, dst)]
        if rel == "RELATED_TO":   # 무방향 의미 — 역방향 물리 엣지도 대상
            pairs.append((dst, src))
        deleted = 0
        # 끝점 라벨 조합 전수 — 안 맞는 조합은 MATCH 가 비어 no-op 이다.
        for sl, sp, dl, dp in (WIKI_EDGE_RELS if wiki else SYNC_EDGE_RELS)[rel]:
            for s_, d_ in pairs:
                p = {**params, "s": pkns.key(owner, s_), "d": pkns.key(owner, d_)}
                cnt = store.run(
                    f"MATCH (a:{sl})-[r:{rel}]->(b:{dl}) "
                    f"WHERE a.{sp} = $s AND b.{dp} = $d{scope_a}{scope_b}{qualifier} "
                    "RETURN count(r) AS c", p)
                if cnt and int(cnt[0].get("c") or 0):
                    store.run(
                        f"MATCH (a:{sl})-[r:{rel}]->(b:{dl}) "
                        f"WHERE a.{sp} = $s AND b.{dp} = $d{scope_a}{scope_b}{qualifier} DELETE r", p)
                    deleted += int(cnt[0].get("c") or 0)
        base["executed"] = True
        if not deleted:
            base["note"] = "edge not found"
            return base
        base["found"] = True
        base["deleted"] = {"edges": deleted}
        # 툼스톤은 정준 방향(요청 방향) 1개 — apply 측이 RELATED_TO 를 양방향 삭제한다.
        marker = rel + ":" + subtype if subtype is not None else rel
        _write_edge_tombstone(store, src, marker, dst, owner)
    except Exception as exc:
        base["error"] = base["note"] = f"{type(exc).__name__}: {exc}"
    finally:
        if owns:
            store.close()
    return base


def _drop_node_shadows(store, node_id: str, owner: str | None, physical_id: str) -> None:
    """노드 하나를 지울 때 함께 가야 하는 그림자 둘 — 검색 미러(NodeVec) 행과 3-way
    기준(_SyncBase) 행. 둘 다 노드 표 밖이라 DETACH DELETE 가 못 미치고, 미러 행을 두면
    다음 기동의 전체 재구축까지 의미 팔에 남아 지운 노드가 검색 후보로 계속 나온다.
    실패는 삼키지 않고 호출자의 예외 경로로 올린다(근거는 `_purge_labels` 의 인용)."""
    from . import node_mirror
    store.run("MATCH (v:NodeVec) WHERE v.vec_id = $vid DELETE v",
              {"vid": node_mirror.vec_id(owner or "", node_id)})
    store.run("MATCH (b:_SyncBase) WHERE b.node_id = $id DELETE b", {"id": physical_id})


def _delete_preview_edges(store, node_id: str, label: str, pk: str,
                          owner: str | None) -> list[dict]:
    from store import embedded_schema as es
    nodes = es.node_table_specs()
    seen, edges = set(), []
    for spec in es.rel_table_specs():
        for direction in ("out", "in"):
            own_end, other_end = (("from", "to") if direction == "out" else ("to", "from"))
            if spec[own_end] != label:
                continue
            other_label = spec[other_end]
            other_pk = nodes[other_label]["pk"]
            pattern = (f"(n:{label})-[r:{spec['label']}]->(o:{other_label})" if direction == "out" else
                       f"(o:{other_label})-[r:{spec['label']}]->(n:{label})")
            cols = nodes[other_label]["cols"]
            has_owner = "owner_id" in cols
            visible = "o.owner_id = $owner" if owner and has_owner else "true"
            subtype = "r.type" if spec["label"] in {"ATTACHES_TO", "TYPED"} else "''"
            params = {"id": pkns.key(owner, node_id), "owner": owner or ""}
            scope = " AND n.owner_id = $owner" if owner else ""
            rows = store.run(f"MATCH {pattern} WHERE n.{pk} = $id{scope} "
                             f"RETURN id(r) AS ek, o.{other_pk} AS other, {visible} AS visible, "
                             f"{subtype} AS subtype", params)
            for row in rows:
                key = json.dumps(row["ek"], sort_keys=True)
                if key in seen:
                    continue
                seen.add(key)
                other = pkns.strip(str(row["other"])) if row.get("visible") else None
                edge = {"from": node_id if direction == "out" else other,
                        "to": other if direction == "out" else node_id,
                        "type": spec["label"], "dir": direction}
                if row.get("subtype"):
                    edge["subtype"] = row["subtype"]
                if other is None:
                    edge["endpoint_unavailable"] = True
                edges.append(edge)
    return sorted(edges, key=lambda e: json.dumps(e, sort_keys=True))


def delete_node(node_id: str, *, owner: str | None = None, cfg=None, store=None,
                dry_run: bool = False, stray_type: str | None = None) -> dict:
    """노드 1개를 부착 엣지와 함께 **비가역** 삭제(T3c). id 접두사로 테이블을 판별
    (:data:`_SEED_TABLES` 재사용) — 미지 접두사(Entity 등)는 표를 특정할 수 없어
    ``found:False`` 로 안전 거부한다. 존재 확인 → 부착 엣지 수(양방향) 카운트 →
    ``DETACH DELETE``(Kùzu 0.11.3 이 지원). ``owner`` 를 주면 owner_id 열이 있는
    라벨(Concept/Episode/Record)에 한해 소유 일치 시에만 삭제(스코프 1차 §1 가드).

    반환: 삭제 시 ``{executed:True, found:True, deleted:{node:1, edges:n}}``,
    미존재·미지 접두사 시 ``{executed:True, found:False}``.

    ``stray_type`` 은 접두사와 **다른** 타입의 테이블에 같은 id 로 들어간 사본 행만
    지운다. 접두사 테이블의 행은 살아 있는 노드라 이 경로로는 못 지운다. 묘비·벡터·
    동기화 base 는 키가 id 하나라 살아 있는 행의 것이므로 쓰지도 지우지도 않는다."""
    cfg = cfg or load_config()
    owns = store is None
    if owns:
        store = open_store(cfg)
    base = {"kind": "delete", "executed": False, "found": False, "node": node_id}
    if type(dry_run) is not bool:
        base["error"] = "dry_run must be a boolean"
        if owns and store is not None:
            store.close()
        return base
    if dry_run:
        base["dry_run"] = True
    if store is None:
        base["note"] = base["error"] = "no reachable store"
        return base
    # E-1 — 도메인 좌표는 **삭제할 수 있는 것이 아니다.** `Domain` 은 무소유 전역
    # 구조라 지우면 그 이름을 쓰는 남의 노드가 좌표를 잃는다. 사용자가 원하는 것은
    # "내 것들에서 이 도메인을 떼는 것"이고, 그건 이미 rename_domain(new='') 이다.
    # 일반 "unknown id prefix" 로 흘리면 그 안내가 안 나간다.
    _dom = domain_hierarchy.parse_id(node_id)
    if _dom:
        base["executed"] = True
        base["note"] = (f"refused: domains are a shared coordinate, not a deletable "
                        f"node ({node_id}). To clear it from your own nodes use "
                        f"domain rename with an empty target (old={_dom!r}, new='').")
        if owns:
            store.close()
        return base
    if pkns.SEP in node_id:
        # 사용자 대면 id 에는 물리 키의 구분자(U+001F)가 없다. `pkns.key` 는 그런 id 를 물리 키로 보고
        # 그대로 돌려주므로, 거르지 않으면 실행 검사를 지나지 않은 채 저장된 행에 닿는다.
        base["executed"] = True
        base["note"] = (f"refused: the id contains U+001F (reserved for PK namespacing), which no "
                        f"node id has. Nothing was deleted ({node_id!r}).")
        if owns:
            store.close()
        return base
    prefix = node_id.split(":", 1)[0] if ":" in node_id else ""
    label_pk = _SEED_TABLES.get(prefix)
    stray = None
    if stray_type is not None:
        stray = _SEED_TABLES.get(stray_type) if isinstance(stray_type, str) else None
        if stray is None or label_pk is None or stray == label_pk:
            base["executed"] = True
            base["note"] = (f"refused: stray_type must name a type other than the id prefix "
                            f"({node_id}). Without it the prefix picks the table.")
            if owns:
                store.close()
            return base
        label_pk = stray
    try:
        if label_pk is None:
            base["executed"] = True
            base["note"] = f"unknown id prefix — cannot resolve table for {node_id!r}"
            return base
        label, pk = label_pk
        scope = ""
        # PK 네임스페이싱 — 이 dict 는 존재확인·차수·DETACH DELETE 세 곳이 나눠 쓴다.
        params: dict = {"id": pkns.key(owner, node_id)}
        if owner and label in _OWNER_SCOPED_LABELS:
            scope = " AND n.owner_id = $owner"
            params["owner"] = owner
        elif owner:
            # SEC-3: owner 강제 경로인데 라벨(Bridge 등)이 owner_id 를 안 가지면 스코프
            # 불가 → 무스코프 삭제로 교차테넌트가 되므로 **거부**한다(fail-closed).
            # 로컬 무스코프(owner=None)는 이 분기를 타지 않아 현행 유지.
            base["executed"] = True
            base["note"] = f"refused: {label} is not owner-scoped ({node_id})"
            return base
        exists = store.run(
            f"MATCH (n:{label}) WHERE n.{pk} = $id{scope} RETURN count(n) AS c", params)
        base["executed"] = True
        if not (exists and exists[0].get("c")):
            base["note"] = f"node not found: {node_id}"
            return base
        base["found"] = True
        from store import run_state   # 국소 사용 — 고정한 api.query 이름 목록에 더하지 않는다
        if stray is None and label == "Episode" and run_state.run_id(node_id) == node_id:
            # 끝나지 않은 실행(일하는 중이거나 사람을 기다린다)은 지우지 않는다: 지우면 질문이 답 없이 승인함에서
            # 사라지거나 사람의 답의 기록이 지워진다. 끝난 실행과 만료된 질문은 소유자가 지운다. 같은 쓰기 잡 안에서 읽는다.
            held = store.run(f"MATCH (n:{label}) WHERE n.{pk} = $id{scope} RETURN n.props AS props",
                             params)
            if held and run_state.unfinished(held[0].get("props")):
                base.update(executed=False, error=run_state.CODE,
                            note=run_state.delete_refusal(node_id))
                return base
        deg = store.run(
            f"MATCH (n:{label})-[r]-() WHERE n.{pk} = $id{scope} RETURN count(DISTINCT r) AS c", params)
        edges = int(deg[0].get("c")) if deg else 0
        if dry_run:
            preview = _delete_preview_edges(store, node_id, label, pk, owner)
            if len(preview) != edges:
                base.update(executed=False, error="delete preview changed during read; try again")
                return base
            # 봉투 상한을 넘는 목록을 일부만 보여 주지 않는다. 실제 삭제는 하지 않는다.
            if len(json.dumps(preview, ensure_ascii=False).encode("utf-8")) > 48000:
                base.update(executed=False, error="delete preview exceeds response budget",
                            edge_count=edges, preview_complete=False)
                return base
            base["preview"] = {"node": 1, "edges": preview, "edge_count": edges,
                               "complete": True}
            return base
        if stray is None:
            _write_tombstone(store, node_id, label, owner)   # 삭제 전파용 표식(S4)
        store.run(f"MATCH (n:{label}) WHERE n.{pk} = $id{scope} DETACH DELETE n", params)
        if stray is None:
            _drop_node_shadows(store, node_id, owner, params["id"])
        else:
            base["stray_type"] = stray_type
        base["deleted"] = {"node": 1, "edges": edges}
    except Exception as exc:
        base["error"] = base["note"] = f"{type(exc).__name__}: {exc}"
    finally:
        if owns:
            store.close()
    return base


def merge_nodes(keep_id: str, drop_id: str, *, owner: str | None = None,
                cfg=None, store=None) -> dict:
    """두 Concept 를 **비가역** 병합(T3c) — ``drop`` 의 모든 연결을 ``keep`` 으로
    재지향한 뒤 ``drop`` 을 삭제한다. v0.1.7 범위는 **Concept↔Concept 전용**(실사용
    정리 대상이 concept 중복 껍데기) — 그 외 타입 쌍은 ``executed:True``+``error`` 로
    거부한다. drop 의 title/props 는 버린다(keep 우선).

    재지향 대상: CC 4종(:data:`_CC_RELS`)의 outgoing·incoming 각각, ATTACHES_TO
    (Record→drop) incoming, CONNECTS(Bridge→drop) incoming. keep→keep 자기루프가 될
    엣지(상대가 keep/drop)·keep 에 이미 있는 동일 엣지는 스킵한다(중복 방지;
    RELATED_TO 는 무방향이라 역방향 존재도 중복으로 간주 — fragment.py 정책 일치).

    반환: ``{executed, found, repointed, skipped, dropped}``. keep/drop 미존재 시
    ``found:False``."""
    cfg = cfg or load_config()
    owns = store is None
    if owns:
        store = open_store(cfg)
    base = {"kind": "merge", "executed": False, "keep": keep_id, "drop": drop_id}
    try:
        if store is None:
            base["note"] = base["error"] = "no reachable store"
            return base
        kp = keep_id.split(":", 1)[0] if ":" in keep_id else ""
        dp = drop_id.split(":", 1)[0] if ":" in drop_id else ""
        if kp != "concept" or dp != "concept":
            base["executed"] = True
            base["error"] = ("merge_nodes v0.1.7 supports concept↔concept only "
                             f"(got keep={kp or '?'}, drop={dp or '?'})")
            return base
        if keep_id == drop_id:
            base["executed"] = True
            base["error"] = "keep and drop are the same node"
            return base

        # SEC-3: owner 가 주어지면 모든 Concept/Record MATCH 를 소유자로 스코프한다.
        # keep·drop 중 하나라도 호출자 소유가 아니면 _exists 게이트에서 found:False 로
        # 즉시 중단(어떤 쓰기도 하기 전) → 남의 concept 병합·재지향·삭제 불가. 재지향·
        # 최종삭제도 소유 노드에만 닿는다. owner=None(로컬 단일유저)이면 현행(무스코프).
        osc = bool(owner)
        oscope = (lambda var: f" AND {var}.owner_id = $owner") if osc else (lambda var: "")
        # F5 — Bridge 는 과도기 술어(소유 없는 '' 행 포함, _bridge_owner_pred 와 동일 정책).
        bscope = (lambda var: f" AND ({var}.owner_id = $owner OR {var}.owner_id = '')"
                  ) if osc else (lambda var: "")
        op = {"owner": owner} if osc else {}   # 파라미터 스프레드(무스코프면 빈 dict)
        # PK 네임스페이싱 — 이 함수의 모든 `$id`/`$s`/`$d`/`$r`/`$k`/`$b` 는 노드 PK
        # 바인딩이다. 반대로 조회가 돌려주는 `other` 는 읽기 훅이 되돌린 **논리**
        # 슬러그이므로, `keep_id`/`drop_id` 와의 비교는 논리끼리 그대로 맞는다 —
        # 합성은 **다시 바인딩할 때만** 한다. 근거: [[vinc.plan.node-pk-namespacing]] §7
        def _k(v: str) -> str:
            return pkns.key(owner, v)

        def _exists(cid: str) -> bool:
            r = store.run(
                f"MATCH (c:Concept) WHERE c.concept_id = $id{oscope('c')} "
                "RETURN count(c) AS c", {"id": _k(cid), **op})
            return bool(r and r[0].get("c"))

        base["executed"] = True
        if not (_exists(keep_id) and _exists(drop_id)):
            base["found"] = False
            base["note"] = "keep or drop not found"
            return base
        base["found"] = True

        repointed = skipped = 0

        # 반려 관계는 역할 권한 계약이다. 일반 중복 정리로 끝점과 근거를
        # 조용히 바꾸지 않는다. 구체적인 관계 변경을 먼저 검토해야 한다.
        from .role_contract import is_role_id
        if is_role_id(keep_id) or is_role_id(drop_id):
            role_returns = store.run(
                "MATCH (a:Concept)-[r:RETURNS_TO]->(b:Concept) "
                "WHERE (a.concept_id IN $ids OR b.concept_id IN $ids)"
                f"{oscope('a')}{oscope('b')} RETURN count(r) AS c",
                {"ids": [_k(keep_id), _k(drop_id)], **op})
            if role_returns and role_returns[0].get("c"):
                base["error"] = "role return paths require explicit review before merging nodes"
                return base

        def _cc_exists(rel: str, s: str, d: str) -> bool:
            r = store.run(
                f"MATCH (a:Concept)-[:{rel}]->(b:Concept) "
                f"WHERE a.concept_id = $s AND b.concept_id = $d{oscope('a')}{oscope('b')} "
                "RETURN count(*) AS c",
                {"s": _k(s), "d": _k(d), **op})
            return bool(r and r[0].get("c"))

        def _others(rel: str, incoming: bool) -> set:
            # drop 과 이웃 x 를 모두 owner 로 스코프 — 남의 노드로 향한 엣지는 재지향 대상 아님.
            if incoming:
                cy = (f"MATCH (x:Concept)-[:{rel}]->(d:Concept) "
                      f"WHERE d.concept_id = $id{oscope('d')}{oscope('x')} "
                      "RETURN DISTINCT x.concept_id AS other")
            else:
                cy = (f"MATCH (d:Concept)-[:{rel}]->(x:Concept) "
                      f"WHERE d.concept_id = $id{oscope('d')}{oscope('x')} "
                      "RETURN DISTINCT x.concept_id AS other")
            return {row["other"] for row in store.run(cy, {"id": _k(drop_id), **op})
                    if row.get("other")}

        for rel in _CC_RELS:
            for incoming in (False, True):
                for other in _others(rel, incoming):
                    if other in (keep_id, drop_id):      # keep 자기루프·drop 내부 엣지 → 스킵
                        skipped += 1
                        continue
                    # incoming: (other)-[rel]->(keep) / outgoing: (keep)-[rel]->(other)
                    s, d = (other, keep_id) if incoming else (keep_id, other)
                    dup = _cc_exists(rel, s, d)
                    if not dup and rel == "RELATED_TO":
                        dup = _cc_exists(rel, d, s)       # 무방향 — 역방향도 중복
                    if dup:
                        skipped += 1
                        continue
                    store.run(
                        "MATCH (a:Concept),(b:Concept) "
                        f"WHERE a.concept_id = $s AND b.concept_id = $d{oscope('a')}{oscope('b')} "
                        f"MERGE (a)-[:{rel}]->(b)", {"s": _k(s), "d": _k(d), **op})
                    repointed += 1

        # ATTACHES_TO incoming: (rec:Record)-[a]->(drop) → (rec)-[a]->(keep), type 보존.
        # SEC-3: Record·drop·keep 모두 owner 스코프 — 남의 Record 를 내 keep 으로 못 옮긴다.
        # 한 기록이 한 대상에 타입별로 따로 붙는다(쓰기 경로가 type 을 MERGE 패턴에 넣는다).
        # 그래서 중복 검사와 MERGE 모두 (기록, type) 쌍으로 한다. type 을 빼면 keep 에 다른
        # 타입 부착이 하나라도 있을 때 drop 의 부착이 버려지거나 그 엣지 위에 덮어써진다.
        for row in store.run(
                "MATCH (r:Record)-[a:ATTACHES_TO]->(d:Concept) "
                f"WHERE d.concept_id = $id{oscope('d')}{oscope('r')} "
                "RETURN DISTINCT r.record_id AS other, a.type AS type",
                {"id": _k(drop_id), **op}):
            other = row.get("other")
            if not other:
                continue
            etype = row.get("type")
            same = "e.type = $t" if etype is not None else "e.type IS NULL"
            tp = {"t": etype} if etype is not None else {}
            ex = store.run(
                "MATCH (r:Record)-[e:ATTACHES_TO]->(k:Concept) "
                f"WHERE r.record_id = $r AND k.concept_id = $k AND {same}"
                f"{oscope('r')}{oscope('k')} RETURN count(*) AS c",
                {"r": _k(other), "k": _k(keep_id), **tp, **op})
            if ex and ex[0].get("c"):
                skipped += 1
                continue
            if etype is not None:
                store.run(
                    "MATCH (r:Record),(k:Concept) "
                    f"WHERE r.record_id = $r AND k.concept_id = $k{oscope('r')}{oscope('k')} "
                    "MERGE (r)-[e:ATTACHES_TO {type: $t}]->(k)",
                    {"r": _k(other), "k": _k(keep_id), "t": etype, **op})
            else:
                # 타입 없는 부착은 MERGE 하면 같은 쌍의 타입 있는 부착에 맞물려 사라진다.
                # 위 검사가 타입 없는 것이 keep 에 없음을 확인했으므로 새로 만든다.
                store.run(
                    "MATCH (r:Record),(k:Concept) "
                    f"WHERE r.record_id = $r AND k.concept_id = $k{oscope('r')}{oscope('k')} "
                    "CREATE (r)-[e:ATTACHES_TO]->(k)",
                    {"r": _k(other), "k": _k(keep_id), **op})
            repointed += 1

        # CONNECTS incoming: (br:Bridge)-[c]->(drop) → (br)-[c]->(keep), role 보존.
        # SEC-3: Concept 끝점(drop·keep)은 엄격 owner 스코프, Bridge 는 v2(F5)에서
        # owner_id 를 얻어 과도기 술어(bscope — 소유 없는 '' 행 포함)로 스코프한다.
        for row in store.run(
                "MATCH (b:Bridge)-[c:CONNECTS]->(d:Concept) "
                f"WHERE d.concept_id = $id{oscope('d')}{bscope('b')} "
                "RETURN DISTINCT b.bridge_id AS other, c.role AS role",
                {"id": _k(drop_id), **op}):
            other = row.get("other")
            if not other:
                continue
            ex = store.run(
                "MATCH (b:Bridge)-[:CONNECTS]->(k:Concept) "
                f"WHERE b.bridge_id = $b AND k.concept_id = $k{oscope('k')}{bscope('b')} "
                "RETURN count(*) AS c",
                {"b": _k(other), "k": _k(keep_id), **op})
            if ex and ex[0].get("c"):
                skipped += 1
                continue
            if row.get("role") is not None:
                store.run(
                    "MATCH (b:Bridge),(k:Concept) "
                    f"WHERE b.bridge_id = $b AND k.concept_id = $k{oscope('k')}{bscope('b')} "
                    "MERGE (b)-[e:CONNECTS]->(k) SET e.role = $role",
                    {"b": _k(other), "k": _k(keep_id), "role": row["role"], **op})
            else:
                store.run(
                    "MATCH (b:Bridge),(k:Concept) "
                    f"WHERE b.bridge_id = $b AND k.concept_id = $k{oscope('k')}{bscope('b')} "
                    "MERGE (b)-[e:CONNECTS]->(k)",
                    {"b": _k(other), "k": _k(keep_id), **op})
            repointed += 1

        # drop 삭제(부착 엣지 포함) — title/props 버림(keep 우선).
        # SEC-3: 최종 파괴 연산도 owner 스코프(벨트&서스펜더 — _exists 게이트로 이미 소유
        # 확인됐지만, 파괴 연산은 이중 가드). owner 불일치면 아무것도 안 지운다.
        _write_tombstone(store, drop_id, "Concept", owner)   # drop 도 전파(S4)
        store.run(f"MATCH (c:Concept) WHERE c.concept_id = $id{oscope('c')} DETACH DELETE c",
                  {"id": _k(drop_id), **op})
        _drop_node_shadows(store, drop_id, owner, _k(drop_id))
        base["repointed"] = repointed
        base["skipped"] = skipped
        base["dropped"] = drop_id
    except Exception as exc:
        base["error"] = base["note"] = f"{type(exc).__name__}: {exc}"
    finally:
        if owns:
            store.close()
    return base


# ── owner 전체 purge (계정 삭제 캐스케이드, B2 §5.9) ──────────────────────────
# 계정(Vinc-homepage)이 하드삭제될 때 호스티드 그래프에 남는 회원 지식을 함께 지운다.
# 계정 서비스는 B2 경계상 MCP DB 에 직접 못 닿으므로, 계정이 Ed25519 로 서명한 삭제
# 요청을 MCP `/internal/purge-owner` 가 검증해(account 공개키) 사이드카의 이 함수로
# 프록시한다. owner_id 가 있는 라벨(스키마 파생 — Concept/Episode/Record/Entity/
# LearnerPattern)의 소유 노드를 부착 엣지와 함께 DETACH DELETE 한다. 멱등.
def _purge_labels() -> list[str]:
    """owner 스코프 삭제 대상 라벨 — 스키마 SSoT + 소유 메타. 지연 임포트(백엔드
    중립 유지). owner_scoped_labels 는 owner_id 열 보유 라벨만 돌려주므로 여기 전부
    ``n.owner_id = $o`` 필터가 성립한다(v4 부터 Chunk 포함 — D45 Chunk=SSoT).
    `_Tombstone`·`_DocMeta`(메타)도 owner_id 를 가져 포함 — 정본 텍스트(Chunk)가
    지워질 때 문서 정체성 레지스트리도 함께 지운다. `_MergeRejection`(F10-b)도
    회원의 노드 id 쌍을 담으므로 계정 삭제에 함께 지워져야 한다."""
    from store.embedded_schema import owner_scoped_labels
    # `_EdgeTombstone` 도 owner_id 를 가지지만 메타 표라 파생 목록 밖이다. 여기서 빼면 지운
    # 관계의 양 끝 id 가 계정 삭제 뒤에도 90일 GC 까지 남는다.
    # 근거: [[vinc.research.privacy-compliance-gap-2026-09-26 §6]]
    return owner_scoped_labels() + ["_Tombstone", "_EdgeTombstone", "_DocMeta",
                                    "_DocTombstone", "_MergeRejection"]


def _purge_sync_base(store, owner: str) -> int:
    """`_SyncBase` 는 owner 열이 없다. 키가 `pkns.key(owner, node_id)` 라 네임스페이싱이
    켜진 스토어(허브)에서는 접두로 가를 수 있고, 꺼진 스토어(로컬, 소유자 하나)에서는
    가를 수 없어 건드리지 않는다 — 거기서는 소유가 하나라 잔여가 남의 것일 수 없다.
    이 표의 행은 기기가 밀어 올린 노드의 필드 스냅샷이라, 계정 삭제 뒤에 남으면
    제목·태그·props 가 이름 없는 채로 살아남는다(갭 분석 §6)."""
    if not pkns.enabled():
        return 0
    p = owner + pkns.SEP
    cnt = store.run(
        "MATCH (b:_SyncBase) WHERE b.node_id STARTS WITH $p RETURN count(b) AS c", {"p": p})
    n = int(cnt[0].get("c")) if cnt else 0
    if n:
        store.run("MATCH (b:_SyncBase) WHERE b.node_id STARTS WITH $p DELETE b", {"p": p})
    return n


def _purge_sync_meta(store, owner: str) -> int:
    """`_SyncMeta` 의 소유자별 행(`seq:<owner>` 등)은 키 꼬리가 owner 다."""
    s = ":" + owner
    cnt = store.run(
        "MATCH (m:_SyncMeta) WHERE m.key ENDS WITH $s RETURN count(m) AS c", {"s": s})
    n = int(cnt[0].get("c")) if cnt else 0
    if n:
        store.run("MATCH (m:_SyncMeta) WHERE m.key ENDS WITH $s DELETE m", {"s": s})
    return n


def _purge_owner_theme(cfg, owner: str) -> int:
    """어휘 파일(`<db>.themes.json`)의 소유자 주제(`owner-<sha256[:12]>`)를 지운다.
    파일이 없거나 주제가 없으면 0 이고 파일을 건드리지 않는다."""
    try:
        from store.theme_store import ThemeStore, owner_theme_id
        return 1 if ThemeStore(getattr(cfg, "data_dir", None)).delete_theme(
            owner_theme_id(owner)) else 0
    except Exception:
        return 0


def purge_owner(owner: str, *, cfg=None, store=None) -> dict:
    """``owner`` 소유의 **전 노드**를 부착 엣지와 함께 비가역 DETACH DELETE.

    계정 삭제 캐스케이드 전용(§5.9). 소유 스코프이므로 SEC-1/SEC-2 의 owner 격리가
    전제 — 소유가 안 갈리면 남의 노드가 지워질 수 있다. 라벨별 삭제 건수를 돌려줘
    호출자(계정)가 감사할 수 있게 한다. **owner 가 비었으면 거부**(빈 술어로 전삭제
    사고 방지). Domain 은 owner_id 가 없어 소유로 못 가르는 잔여 —
    [[vinc.spec.dataplane §5]] 의 알려진 한계(Bridge 는 v2, Chunk 는 v4 에서
    소유 스탬프를 얻어 편입 — '' 소유 행만 잔여)."""
    cfg = cfg or load_config()
    base = {"kind": "purge", "executed": False, "owner": owner}
    if not owner or not isinstance(owner, str):
        base["error"] = base["note"] = "refusing to purge without an owner"
        return base
    owns = store is None
    if owns:
        store = open_store(cfg)
    if store is None:
        base["note"] = base["error"] = "no reachable store"
        return base
    deleted: dict[str, int] = {}
    try:
        for label in _purge_labels():
            cnt = store.run(
                f"MATCH (n:{label}) WHERE n.owner_id = $o RETURN count(n) AS c", {"o": owner})
            n = int(cnt[0].get("c")) if cnt else 0
            if n:
                store.run(f"MATCH (n:{label}) WHERE n.owner_id = $o DETACH DELETE n", {"o": owner})
            deleted[label] = n
        # owner 열이 없어 위 루프가 못 미치는 잔여 셋 — 3-way 기준(_SyncBase)·소유자별
        # 메타(_SyncMeta)·어휘 파일의 소유자 주제. 갭 분석 §6 의 "지웠다 뒤에 남는 것".
        deleted["_SyncBase"] = _purge_sync_base(store, owner)
        deleted["_SyncMeta"] = _purge_sync_meta(store, owner)
        deleted["theme"] = _purge_owner_theme(cfg, owner)
        base["executed"] = True
        base["deleted"] = deleted
        base["total"] = sum(deleted.values())
    except Exception as exc:
        base["error"] = base["note"] = f"{type(exc).__name__}: {exc}"
    finally:
        if owns:
            store.close()
    return base


# ── 운영자 사용량 덤프의 그래프 쪽 절반(W1c) ─────────────────────────────────────
#: 소유자별로 세는 라벨과 응답 키. 앞의 넷은 `/domains` 가 세는 것과 같고 `nodes` 는
#: 그 넷의 합이다(운영자가 "노드 수"라고 부르는 값). Entity·Bridge 는 따로 싣는다.
_OWNER_STAT_LABELS = (("Concept", "concepts"), ("Record", "records"),
                      ("Episode", "episodes"), ("Decision", "decisions"),
                      ("Entity", "entities"), ("Bridge", "bridges"))
_OWNER_STAT_NODE_KEYS = ("concepts", "records", "episodes", "decisions")


def owner_stats(*, cfg=None, store=None) -> dict:
    """소유자 **전체**의 노드·문서 수 — `{owner: {nodes, concepts, …, docs}}`.

    ⚠ 이 모듈에서 owner 술어 없이 소유자를 가로지르는 조회는 이것뿐이다. 개수만
    돌려주고(제목·본문·도메인 없음) 부르는 곳도 하나다: 허브의 계정 서명 경로
    `/internal/usage`(`vinc_mcp.http_server.MCPHTTPApp.usage_request`). 웹 프록시
    허용목록(`_PROXY_OPS`)에 올리면 로그인한 누구나 남의 규모를 보게 된다.

    문서 수는 문서 목록과 같은 기준이다: 관례 문서(GRAPH.md)는 자료가 아니라 빼고 센다.
    owner 가 '' 인 행(소유 스탬프가 없는 행)도 그대로 싣는다. 버리면 합계가
    맞지 않고, 그 차이가 무엇인지 아무도 모르게 된다.
    근거: [[vinc.plan.backoffice-and-stats-2026-09-12]]"""
    cfg = cfg or load_config()
    base: dict = {"kind": "owner.stats", "executed": False}
    owns = store is None
    if owns:
        store = open_store(cfg)
    if store is None:
        base["note"] = base["error"] = "no reachable store"
        return base

    def slot(owner: str) -> dict:
        return owners.setdefault(owner, {**{k: 0 for _l, k in _OWNER_STAT_LABELS},
                                         "docs": 0})

    owners: dict[str, dict] = {}
    try:
        for label, key in _OWNER_STAT_LABELS:
            for row in store.run(f"MATCH (n:{label}) RETURN n.owner_id AS owner, "
                                 "count(*) AS c") or []:
                slot(row.get("owner") or "")[key] += int(row.get("c") or 0)
        for row in store.run("MATCH (d:_DocMeta) RETURN d.owner_id AS owner, "
                             "d.doc_id AS doc_id") or []:
            if not graphmd.is_conventions_doc_id(row.get("doc_id") or ""):
                slot(row.get("owner") or "")["docs"] += 1
        for s in owners.values():
            s["nodes"] = sum(s[k] for k in _OWNER_STAT_NODE_KEYS)
        base["executed"] = True
        base["owners"] = owners
    except Exception as exc:
        base["error"] = base["note"] = f"{type(exc).__name__}: {exc}"
    finally:
        if owns:
            store.close()
    return base
