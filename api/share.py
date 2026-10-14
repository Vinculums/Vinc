# -*- coding: utf-8 -*-
"""Share pack — 한 계정이 로컬에 적재한 데이터를 **다른 사람에게 전달**하기 위한
패키징(내보내기/들여오기). 계약 정본: [[vinc.canon.share-pack]](DEC D19).

설계 결정 3가지:
1. **포맷 = MCP fragment 동형**(D11 §5). 팩의 본문은 `{nodes, edges}` fragment 라서
   들여오기가 `api/fragment.merge_fragment`(id 정확 매칭 MERGE·멱등) 재사용 하나로
   끝난다 — 별도 import 기계 없음.
2. **커스텀 스코프**. 도메인 전체가 아니라 "이 영역까지만"을 지원한다:
   ``domain``(데이터셋 구획) · ``tags``(교집합) · ``seed``+``depth``(ego 서브그래프).
   구조 노드를 경유하되 팩에는 Concept/Episode/Record/Decision만 싣는다.
3. **preference 신호 미포함**. pinned/access_count/last_access 는 원 계정의 개인
   신호라 공유 팩에 싣지 않는다(투영에서 제외 — 스크럽 단계 불요). 수신자는
   자신의 owner 로 재스탬핑해 들여온다(fragment.owner 명시 → §1 스코프 불변 유지).

과금·계정 연동 없음(F1) — 팩은 로컬 파일이고 전달은 사용자의 채널(드라이브·메신저)로.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from . import fragment as frag
from . import packfile
from . import query as q
from store import pkns
from store.embedded_schema import _NODE_TABLES, _REL_TABLES
from . import docstore as ds

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
        # PK 네임스페이싱 — 노드 PK 바인딩은 물리 키다. 근거: [[vinc.plan.node-pk-namespacing]] §7
        params["ids"] = pkns.keys(owner, ids)
    elif domain:
        # 주제 스코프는 **하위 트리**다 — 정확일치면 부모 주제를 공유할 때 개념이 0건
        # 나오고, 그러면 `_docs_for_concepts` 가 문서까지 0으로 만들어 **빈 팩이 오류
        # 없이 나간다**. 판정은 `query.domain_subtree_where` 한 벌만 쓴다(같은 화면의
        # 렌즈 소속도 그 규칙이다: app/src/surfaces/view.ts `inLens`).
        # 근거: [[record:query-scope-ignored-the-topic-tree-it-was-standing-on]]
        where.append(q.domain_subtree_where(["c.domain"], [domain], params))
    cypher = (
        f"MATCH (c:Concept) WHERE {' AND '.join(where)} "
        "RETURN c.concept_id AS id, c.title AS title, c.domain AS domain, "
        "c.tags AS tags, c.topic AS topic, c.one_liner AS one_liner, c.props AS props, "
        # created 를 싣는다 — 안 실으면 받는 쪽에서 팩 개념이 전부 임포트 시각으로
        # 몰려 타임라인이 밴드 하나가 된다. 가산 필드라 구 임포터는 무시한다
        # ([[record:sync-drops-node-created-pack-keeps-it]]).
        "c.created AS created, "
        # content_time 은 **더 실려야 할 쪽**이다 — created 는 공간의 시계지만 이것은
        # 지시체에 속해(1905 는 어느 그래프에서든 1905다) 공간을 건너도 참이다
        # ([[vinc.plan.sync-node-time-fields]]).
        "c.content_time AS content_time"
    )
    return q.QueryPlan("share_concepts", cypher, params, note="share projection (no preference)")


def _episodes_plan(*, owner: str, domain: str | None = None,
                   ids: list[str] | None = None) -> q.QueryPlan:
    where = ["e.owner_id = $owner"]
    params: dict = {"owner": owner}
    if ids is not None:
        where.append("e.episode_id IN $ids")
        params["ids"] = pkns.keys(owner, ids)
    elif domain:
        # 개념과 **같은 술어**를 쓴다 — 한쪽만 하위 트리로 두면 부모 주제를 공유할 때
        # Episode 만 빠져 팩이 반쪽이 된다.
        where.append(q.domain_subtree_where(["e.domain"], [domain], params))
    cypher = (
        f"MATCH (e:Episode) WHERE {' AND '.join(where)} "
        "RETURN e.episode_id AS id, e.title AS title, e.domain AS domain, "
        "e.date AS date, e.summary AS summary, e.tags AS tags, e.props AS props"
    )
    return q.QueryPlan("share_episodes", cypher, params, note="share projection (no preference)")


def _content_plan(kind: str, *, owner: str, domain: str | None = None,
                  ids: list[str] | None = None) -> q.QueryPlan:
    label, pk = frag._NODE_MAP[kind]
    where, params = ["n.owner_id = $owner"], {"owner": owner}
    if ids is not None:
        where.append(f"n.{pk} IN $ids")
        params["ids"] = pkns.keys(owner, ids)
    elif domain:
        where.append(q.domain_subtree_where(["n.domain"], [domain], params))
    fields = ["title", "domain", "tags", "props", "created"]
    if kind == "record":
        fields += ["content_time", "valid_from", "valid_until"]
    return q.QueryPlan(
        f"share_{kind}s", f"MATCH (n:{label}) WHERE {' AND '.join(where)} "
        f"RETURN n.{pk} AS id, " + ", ".join(f"n.{f} AS {f}" for f in fields),
        params, note="share projection (no preference)")


def _share_edges_plan(*, owner: str, ids: list[str]) -> q.QueryPlan:
    """네 종류 사이 저작 관계만, 양 끝 owner를 매 홉에서 검사한다.

    구조 경유는 _share_reach_plan에서 다루고, 여기서는 내보낼 관계만 고른다.
    근거: [[vinc.canon.mcp-contract §5.2]].
    """
    pairs = []
    for source in ("concept", "decision"):
        for target in ("concept", "decision"):
            for relation in frag._CC_EDGES.values():
                if relation not in ("RELATED_TO", "RETURNS_TO") or source == target == "concept":
                    pairs.append((source, target, relation))
    pairs += [(s, t, "ABOUT") for s in ("episode", "decision")
              for t in ("concept", "decision")]
    pairs += [("record", t, "ATTACHES_TO") for t in ("concept", "decision")]
    arms = []
    for source, target, relation in pairs:
        sl, sp = frag._NODE_MAP[source]
        tl, tp = frag._NODE_MAP[target]
        role = "r.type" if relation == "ATTACHES_TO" else "''"
        arms.append(
            f"MATCH (a:{sl})-[r:{relation}]->(b:{tl}) "
            f"WHERE a.owner_id = $owner AND b.owner_id = $owner "
            f"AND a.{sp} IN $ids AND b.{tp} IN $ids "
            f"RETURN a.{sp} AS id, b.{tp} AS target, '{relation}' AS rel, "
            f"{role} AS role, r.evidence AS evidence, r.confidence AS confidence, "
            "r.created AS edge_created")
    return q.QueryPlan("share_edges", " UNION ALL ".join(arms),
                       {"owner": owner, "ids": pkns.keys(owner, ids)})


def _share_reach_plan(*, owner: str, frontier: dict[str, set[str]]) -> q.QueryPlan:
    """기존 ego의 구조 경유를 유지하되 매 홉의 소유자를 검사한다.

    Bridge/Chunk/Domain은 순회용이며 팩의 노드가 아니다. 라벨마다 PK와 소유 열이
    다르므로 스키마에서 승인된 쌍을 파생한다. 근거: [[vinc.spec.dataplane §2]].
    """
    pairs = set()
    for relation, sources, targets, _ in _REL_TABLES:
        sources = (sources,) if isinstance(sources, str) else sources
        targets = (targets,) if isinstance(targets, str) else targets
        for source in sources:
            for target in targets:
                pairs.add((source, target, relation))
                pairs.add((target, source, relation))
    arms, params = [], {"owner": owner}
    for source, target, relation in sorted(pairs):
        if not frontier.get(source):
            continue
        sp, tp = _NODE_TABLES[source]["pk"], _NODE_TABLES[target]["pk"]
        param = f"ids_{source}"
        values = sorted(frontier[source])
        params[param] = values if source in ("Chunk", "Domain") else pkns.keys(owner, values)
        where = [f"a.{sp} IN ${param}"]
        for alias, label in (("a", source), ("b", target)):
            if label == "Bridge":
                where.append(q._bridge_owner_pred(alias))
            elif label == "Chunk":
                # v4 이전 청크는 owner_id 가 ''(레거시 무소유)다. Bridge 와 같은 과도기
                # 술어라야 구 ego 가 걷던 청크 경로가 끊기지 않는다. 남의 청크는 여전히 막힌다.
                where.append(f"({alias}.owner_id = $owner OR {alias}.owner_id = '')")
            elif any(col == "owner_id" for col, _ in _NODE_TABLES[label]["cols"]):
                where.append(f"{alias}.owner_id = $owner")
        arms.append(f"MATCH (a:{source})-[:{relation}]-(b:{target}) "
                    f"WHERE {' AND '.join(where)} RETURN '{target}' AS kind, b.{tp} AS id")
    if not arms:
        return q.QueryPlan("share_reach", "RETURN '' AS kind, '' AS id LIMIT 0", {})
    return q.QueryPlan("share_reach", " UNION ALL ".join(arms), params)


def _tag_filter(rows: list[dict], tags: list[str] | None) -> list[dict]:
    if not tags:
        return rows
    want = set(tags)
    return [r for r in rows if want & set(r.get("tags") or [])]


def _read_props(row: dict, warnings: list[str]) -> dict:
    """저장된 props JSON 을 dict 로 푼다. 객체가 아니면 props 만 빼고 경고한다.

    행 하나가 팩 전체를 실패시키지 않는다. props 를 안 실으면 수신측은 자기 값을
    보존하므로(키 부재 = 미지정) 빼는 쪽이 지우는 쪽보다 안전하다."""
    raw = row.get("props")
    if not raw:
        return {}
    try:
        props = json.loads(raw)
    except (TypeError, ValueError):
        props = None
    if not isinstance(props, dict):
        warnings.append(f"stored props of {row['id']} are not a JSON object; "
                        "exported the node without them")
        return {}
    return props


def _docs_for_concepts(store, *, owner: str, concept_ids: list[str],
                       decision_ids: list[str] | None = None, cfg=None,
                       refs: dict[str, list[str]] | None = None,
                       include_all: bool = False) -> list[dict]:
    """팩의 개념이 가리키는 원문 문서를 export 형태로 모은다 — 자기완결 공유 팩
    (개념 + 그 소스 원문). 길이 **둘**이다: `(Chunk)-[:MENTIONS]->(Concept)` 역추적과
    문서 미러(`concept:doc.<doc_id>`) id 풀기. Chunk=SSoT(D45) 이후에만 존재;
    어느 길로도 안 걸리면(팩·수기 개념) 빈 목록이다.

    ``refs`` 에 dict 를 넘기면 실린 문서마다 **그 문서를 끌어온 노드 id**(팩 fragment 의
    id 그대로)를 채운다. 공유 시트가 행을 끈 뒤 원문을 같은 기준으로 다시 고르는 데 쓴다.
    근거: [[decision:share-documents-follow-the-nodes-that-carry-them]]

    ``include_all`` 은 노드 선택이 없는 전체 공유에만 쓴다. 노드가 가리키지
    않는 문서도 원문 목록에서 읽고, 그 문서의 빈 refs 는 독립 선택 행을 뜻한다.

    payload 형태는 docsync.export_docs 와 동형({doc_id,title,origin,content_hash,
    updated_at,text}) — 수신측이 import_docs 로 재청킹한다(embedding·오프셋 미전송,
    수신 기기 재생성). SEC-1: owner 스코프 청크만 역추적한다.

    ``store`` 미주입이면 **여기서 연다**(`query.execute`·`import_share_pack` 과 같은
    규약). 예전엔 `store is None` 을 "개념이 없다"와 한 줄로 묶어 빈 목록을 냈고,
    그래서 store 를 안 넘기는 호출자(`api/cli.py` 의 `share`)가 오류도 로그도 없이
    `documents: 0` 인 팩을 **성공으로** 받아 갔다. 같은 스코프를 store 주입으로 부르면
    `documents: 1` 이 나오던 자리다(실측 2026-09-07). 개념은 `query.execute` 가 스스로
    열어 제대로 나왔으므로, 팩은 "개념은 있는데 원문만 없는" 반쪽으로 저장됐다."""
    if not include_all and not concept_ids and not decision_ids:
        return []
    owns = store is None
    if owns:
        from store.backend import open_store
        from store.config import load_config
        store = open_store(cfg or load_config())
        if store is None:
            return []          # 정말로 도달 불가 — 개념 조회도 같은 답을 냈을 자리다
    try:
        # 끌어온 노드는 **팩 fragment 의 id** 로 적는다 — 스토어가 돌려주는 물리 키를
        # 넘겨받은 id 로 되돌린다(네임스페이싱이 꺼져 있으면 둘은 같다).
        as_given = {pkns.key(owner, i): i for i in (*concept_ids, *(decision_ids or []))}
        by_doc: dict[str, set[str]] = {}
        # 전체 공유는 주제·미러가 없는 문서도 읽는다. 목록은 owner 로 좁히며,
        # GRAPH.md 같은 관례 문서는 기존 문서 목록 규칙대로 자료에서 제외한다.
        if include_all:
            for doc in ds.list_docs(store, owner=owner, detail="summary")["docs"]:
                by_doc[doc["doc_id"]] = set()

        def _carried(rows_) -> None:
            for r in rows_ or []:
                did, node = r.get("doc_id"), r.get("node")
                if not did:
                    continue
                held = by_doc.setdefault(did, set())
                if node:
                    held.add(as_given.get(node) or node)

        rows = store.run(
            "MATCH (c:Chunk)-[:MENTIONS]->(co:Concept) "
            "WHERE co.concept_id IN $ids AND c.owner_id = $o "
            "RETURN DISTINCT c.doc_id AS doc_id, co.concept_id AS node",
            {"ids": pkns.keys(owner, list(concept_ids)), "o": owner})
        _carried(rows)
        if decision_ids:
            rows = store.run(
                "MATCH (c:Chunk)-[:MENTIONS]->(d:Decision) "
                "WHERE d.decision_id IN $ids AND d.owner_id = $o AND c.owner_id = $o "
                "RETURN DISTINCT c.doc_id AS doc_id, d.decision_id AS node",
                {"ids": pkns.keys(owner, decision_ids), "o": owner})
            _carried(rows)
        # MENTIONS 만으로는 부족하다 — 그 엣지는 **본문이 개념 이름을 담을 때** 생기므로,
        # 주제를 손으로 적기만 한 문서는 자기 미러(`concept:doc.<doc_id>`)가 팩에 실려도
        # 따라오지 않는다. 받는 쪽은 가리키는 문서가 없는 개념을 오류 없이 얻는다.
        # 그래서 미러 id 에서 접두를 벗겨 문서 집합에 더한다(MENTIONS 와 합집합 — 중복 없음).
        # 접두 상수는 docstore 의 것을 그대로 쓴다(사본을 세 벌로 늘리지 않는다).
        # `pkns.strip` 은 SEP 없으면 항등이라 네임스페이싱 on/off 양쪽에서 같은 코드가 돈다.
        for cid in concept_ids:
            logical = pkns.strip(cid)
            if logical.startswith(ds._DOC_MIRROR_PREFIX):
                did = logical[len(ds._DOC_MIRROR_PREFIX):]
                if did:
                    by_doc.setdefault(did, set()).add(cid)
        docs: list[dict] = []
        for did in sorted(by_doc):
            # SEC-1 — 미러에서 푼 doc_id 도 owner 를 건다. `get_doc` 이 owner 불일치와
            # 부재(오타 미러·이미 지운 문서)를 둘 다 executed=False 로 답하므로, 이 한
            # 줄이 "남의 문서 유출"과 "없는 doc_id 를 실어 임포트를 깨는 것"을 함께 막는다.
            got = ds.get_doc(store, did, owner=owner)
            if not got.get("executed"):
                if include_all:
                    raise ValueError("Could not read every document. Try sharing again.")
                continue
            if include_all and not got.get("verified"):
                raise ValueError("Could not verify every document. Try sharing again.")
            docs.append({
                "doc_id": did, "title": got.get("title") or "",
                "origin": got.get("origin") or "", "content_hash": got.get("content_hash"),
                "updated_at": got.get("updated_at") or "", "text": got.get("text") or "",
                # **내용 시점만** 싣는다. `created_at` 은 안 싣는다 — 임포트가 doc_id 를 수신
                # owner 로 새로 파생하므로 받는 쪽에서 그것은 다른 문서이고, 이 공간에 생긴
                # 때는 정말로 지금이다(노드는 id 를 물려받아 같은 객체라 규칙이 갈린다).
                "content_time": (ds._meta(store, did) or {}).get("content_time") or "",
            })
            if refs is not None:
                refs[did] = sorted(by_doc[did])
        return docs
    finally:
        if owns:
            store.close()


def build_share_pack(*, owner: str, domain: str | None = None,
                     tags: list[str] | None = None, seed: str | None = None,
                     ids: list[str] | None = None,
                     depth: int = 2, include_records: bool = True,
                     generated_at: str | None = None,
                     cfg=None, store=None, doc_refs: bool = False) -> dict:
    """네 종류의 콘텐츠를 선택한다: ids > seed > domain, 이후 태그 교집합.

    ego는 구조 노드를 포함해 무방향 1~4홉 순회하며 출력 관계 방향은 보존한다.
    include_records=False는 기존 CLI 계약대로 Concept만 포함한다.

    ``doc_refs=True`` 면 팩에 ``doc_refs``(doc_id → 그 원문을 끌어온 노드 id 목록)를
    더한다. 팩 파일의 일부가 아니라 **공유 시트만 쓰는 값**이다: 시트가 행을 끈 뒤 원문을 다시
    고르고 저장·전송 전에 뗀다. 그래서 CLI 처럼 팩을 그대로 파일로 쓰는 호출자는 안 켠다.
    """
    # 1) 스코프 결정 — ids(명시 집합: lasso 영역 선택, Phase B) > seed(ego) > domain.
    if ids is not None:
        c_res = q.execute(_concepts_plan(owner=owner, ids=sorted(ids)), cfg, store=store)
    elif seed:
        reached = {label: {seed} for label in ("Concept", "Episode", "Record", "Decision")}
        frontier = reached
        for _ in range(max(1, min(int(depth), 4))):
            ids_res = q.execute(_share_reach_plan(owner=owner, frontier=frontier),
                                cfg, store=store)
            if not ids_res.get("executed"):
                return {"executed": False, "note": ids_res.get("note", "no reachable store")}
            frontier = {}
            for row in ids_res.get("rows", []):
                label, nid = row["kind"], row["id"]
                if nid not in reached.setdefault(label, set()):
                    frontier.setdefault(label, set()).add(nid)
                    reached[label].add(nid)
            if not frontier:
                break
        ids = sorted({nid for label in ("Concept", "Episode", "Record", "Decision")
                      for nid in reached.get(label, set())})
        c_res = q.execute(_concepts_plan(owner=owner, ids=ids), cfg, store=store)
    else:
        c_res = q.execute(_concepts_plan(owner=owner, domain=domain), cfg, store=store)
    if not c_res.get("executed"):
        return {"executed": False, "note": c_res.get("note", "no reachable store")}
    concepts = _tag_filter(c_res.get("rows", []), tags)
    concept_ids = {c["id"] for c in concepts}
    # 못 읽는 값(노드의 props · 엣지의 타입)은 팩을 실패시키지 않는다. 그 값만 빠지고
    # (타입 없는 엣지는 엣지째), 무엇이 빠졌는지는 manifest 의 warnings 에 남는다.
    warnings: list[str] = []
    selected = {}
    for kind in ("decision", "record", "episode"):
        if not include_records:
            selected[kind] = []
            continue
        plan = (_episodes_plan(owner=owner, domain=domain, ids=ids) if kind == "episode"
                else _content_plan(kind, owner=owner, domain=domain, ids=ids))
        result = q.execute(plan, cfg, store=store)
        if not result.get("executed"):
            return {"executed": False, "note": result.get("note", "no reachable store")}
        selected[kind] = _tag_filter(result.get("rows", []), tags)
    id_set = concept_ids | {r["id"] for rows in selected.values() for r in rows}

    # 2) 스코프 내 저작 엣지 — 네 종류의 관계를 읽고 경계 밖은 잘라낸다.
    edges: list[dict] = []
    if id_set:
        e_res = q.execute(_share_edges_plan(ids=sorted(id_set), owner=owner),
                          cfg, store=store)
        if not e_res.get("executed"):
            return {"executed": False, "note": e_res.get("note", "no reachable store")}
        seen = set()
        for r in e_res.get("rows", []):
            relation = r.get("rel") or ""
            ftype = (r.get("role") if relation == "ATTACHES_TO" else
                     "about" if relation == "ABOUT" else _REL_TO_FRAGMENT.get(relation))
            tgt = r.get("target")
            if not tgt or tgt not in id_set or r["id"] not in id_set:
                continue
            if (not ftype or (relation == "ATTACHES_TO" and ftype not in frag._CC_EDGES)):
                # 타입 없는 ATTACHES_TO 는 병합 경로(query.py)가 실제로 쓴다. 그 엣지
                # 하나 때문에 팩 전체를 거절하지 않고, 빼고 말한다.
                warnings.append(f"{relation} {r['id']} -> {tgt} has no exportable "
                                f"type ({ftype!r}); left out of the pack")
                continue
            key = (r["id"], ftype, tgt)
            if key in seen:
                continue
            seen.add(key)
            e = {"from": r["id"], "to": tgt, "type": ftype}
            # v14 — 엣지 출생도 함께. `created` 의 뜻이 "어느 공간에서든 처음
            # 이어진 때"라 공간을 건너도 참이고, 받는 쪽에서 신규일 때만 쓰인다
            # (ON CREATE SET — 기존 엣지는 안 덮는다).
            if r.get("edge_created"):
                e["created"] = r["edge_created"]
            # 값이 있을 때만 싣는다. ALTER 로 열을 얻은 스토어는 빈 엣지도 ''·0.0 을
            # 돌려주고, 그것을 실으면 수신측의 실제 근거·신뢰도를 덮어 지운다.
            for field in ("evidence", "confidence"):
                if r.get(field) or (relation == "RETURNS_TO" and r.get(field) is not None):
                    e[field] = r[field]
            edges.append(e)

    # 4) fragment 노드로 사상.
    nodes: list[dict] = []
    for c in concepts:
        n = {"id": c["id"], "type": "concept", "title": c.get("title") or c["id"],
             "domain": c.get("domain") or "", "tags": c.get("tags") or []}
        if c.get("created"):
            n["created"] = c["created"]
        if c.get("content_time"):
            n["content_time"] = c["content_time"]
        props = {}
        if c.get("topic"):
            props["topic"] = c["topic"]
        if c.get("one_liner"):
            props["one_liner"] = c["one_liner"]
        props.update(_read_props(c, warnings))
        if props:
            n["props"] = props
        nodes.append(n)
    for kind, rows in selected.items():
        for row in rows:
            node = {"id": row["id"], "type": kind, "title": row.get("title") or "",
                    "domain": row.get("domain") or "", "tags": row.get("tags") or []}
            # 개념 분기와 같은 규칙: 값이 있을 때만 싣는다. 구 스토어의 열 기본값 ''
            # 는 "모름"인데, 실으면 임포트가 명시적 지우기로 읽어 수신측 값을 지운다.
            for field in ("created", "content_time", "valid_from", "valid_until", "date", "summary"):
                if row.get(field):
                    node[field] = row[field]
            props = _read_props(row, warnings)
            if props:
                node["props"] = props
            nodes.append(node)

    # 자기완결(D45) — 팩 개념이 참조하는 원문 문서를 함께 싣는다(Chunk=SSoT 이후).
    # vinc_pack 버전은 그대로 두고 **선택 필드**로 더한다 — 구버전 앱은 이 필드를
    # 무시하고 개념만 임포트(하위호환), 신버전은 원문까지 복원한다.
    refs: dict[str, list[str]] | None = {} if doc_refs else None
    # 노드 선택·주제·태그 필터가 없는 전체 공유에서만 문서 목록까지 합친다.
    # 읽기가 불완전하면 성공한 빈/부분 팩 대신 재시도 가능한 실패를 답한다.
    try:
        documents = _docs_for_concepts(
            store, owner=owner, concept_ids=sorted(concept_ids),
            decision_ids=sorted(r["id"] for r in selected["decision"]), cfg=cfg, refs=refs,
            include_all=ids is None and not (seed or domain or tags) and include_records)
    except ValueError as exc:
        return {"executed": False, "note": str(exc)}

    created = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    pack = {
        "vinc_pack": PACK_VERSION,
        "kind": "share",
        "manifest": {
            "created": created,
            "scope": {"domain": domain, "tags": tags or [],
                      "seed": seed, "depth": depth if seed else None,
                      "ids": len(ids) if ids is not None else None},
            "counts": {"concepts": len(concepts),
                       "records": len(selected["episode"]) + len(selected["record"]),
                       "decisions": len(selected["decision"]),
                       "edges": len(edges), "documents": len(documents)},
            "note": "Preference signals are not included — they stay with the origin account.",
        },
        "fragment": {"nodes": nodes, "edges": edges},
        "executed": True,
    }
    if warnings:
        pack["manifest"]["warnings"] = warnings
    if documents:
        pack["documents"] = documents
        if refs is not None:
            pack["doc_refs"] = refs
    return pack


def import_share_pack(pack: dict, *, owner: str, cfg=None, store=None,
                      max_domains: int | None = None) -> dict:
    """공유 팩 들여오기 — 수신자 ``owner`` 로 재스탬핑해 fragment MERGE(멱등).

    자기완결 팩(선택 ``documents``, D45)은 개념 MERGE **다음에** 원문을 임포트한다 —
    수신 owner 로 재청킹하고, MENTIONS 재링크가 방금 들어온 개념에 붙게 하기 위한
    순서다. 구버전 팩엔 ``documents`` 가 없어 이 단계가 스킵된다(하위호환).

    ``max_domains``(선택, AP-1 §3.3): 무료 등급 주제 상한. 팩은 새 주제가 한 번에
    여럿 들어오는 경로라 상한을 태운다 — 안 태우면 그대로 우회로가 된다. 상한에
    걸리면 **문서 임포트 이전에** 멈춘다(개념 없이 원문만 남는 반쪽 상태 방지)."""
    from vinc_mcp import agent_export_contract
    marked_agent = isinstance(pack, dict) and pack.get("kind") == agent_export_contract.AGENT_PACK_KIND
    if marked_agent and (type(pack.get("vinc_pack")) is not int
                         or pack["vinc_pack"] != agent_export_contract.AGENT_PACK_VERSION):
        return {"executed": False, "error": "unsupported_community_agent_pack_version"}
    if not isinstance(pack, dict) or (not marked_agent and pack.get("vinc_pack") != PACK_VERSION):
        return {"executed": False,
                "error": f"unsupported pack version: {(pack.get('vinc_pack') if isinstance(pack, dict) else None)!r} "
                         f"(expected {PACK_VERSION})"}
    fragment = pack.get("fragment") or {}
    documents = pack.get("documents") or []
    owns = store is None
    if owns:
        from store.backend import open_store
        from store.config import load_config
        store = open_store(cfg or load_config())
    if store is None:
        return {"executed": False, "error": "no reachable store"}
    try:
        if marked_agent:
            from . import community_agents
            return community_agents.import_file(pack, owner=owner, store=store, max_domains=max_domains)
        res = frag.merge_fragment({**fragment, "owner": owner}, store=store,
                                  max_domains=max_domains)
        if res.get("error") == "domain_limit_reached":
            return res            # 개념이 거부됐으면 원문도 넣지 않는다
        if documents:
            # 원문 복원 — put_doc 으로 수신 owner 에 재청킹한다. **doc_id 는 재사용하지
            # 않고 새로 파생한다**(doc_id=None): 팩은 교차-owner 공유라, 호스티드 단일
            # 스토어에서 원저작자의 doc_id 를 그대로 쓰면 그의 _DocMeta(owner 불일치)에
            # 부딪혀 거부된다. put_doc 이 (수신owner,content_hash,title)로 파생 → 충돌
            # 없음 + 내용 동일 재임포트는 멱등(content_hash 매치 시 skip). MENTIONS 는
            # 방금 merge 된 개념에 재링크(순서: fragment 먼저 → 여기). embedding 은
            # 미전송·수신 재생성(스택 없으면 FTS 지연 — 기존 규율과 동일).
            dapplied = dskipped = 0
            for d in documents:
                r = ds.put_doc(store, owner=owner, text=d.get("text") or "",
                               doc_id=None, title=d.get("title") or "",
                               origin=d.get("origin") or "",
                               # 내용 시점은 지시체의 것이라 공간을 건너도 참이다.
                               # created_at 은 넘기지 않는다 — 위 export 주석 참조.
                               content_time=d.get("content_time") or None)
                if r.get("executed") and r.get("skipped"):
                    dskipped += 1
                elif r.get("executed"):
                    dapplied += 1
            res["documents"] = {"applied": dapplied, "skipped": dskipped,
                                 "total": len(documents)}
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
            # D55 — zip 컨테이너·구 JSON 양쪽을 받는다(첫 바이트로 판별).
            # 텍스트가 아니라 바이트로 연다: zip 을 텍스트로 읽으면 깨진다.
            pack = packfile.read_file(f)
            res = import_share_pack(pack, owner=owner, store=store)
            if res.get("executed"):
                f.rename(f.with_name(f.name + ".done"))
                if res.get("kind") == "community-agent-import":
                    results.append({"file": f.name, **{key: res[key] for key in (
                        "kind", "node_id", "owner", "rev", "status", "source_hash", "version", "mode",
                        "operation_id", "receipt", "replayed") if key in res}})
                else:
                    results.append({"file": f.name, "merged": res["merged"]})
            else:
                errors.append({"file": f.name, "error": res.get("error", "?")})
        except (ValueError, OSError) as exc:
            errors.append({"file": f.name, "error": f"{type(exc).__name__}: {exc}"})
    return {"executed": True, "imported": results, "errors": errors}
