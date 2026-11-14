# -*- coding: utf-8 -*-
"""`api.query` 를 나눈 모듈들이 같이 읽는 바닥: 스키마에서 온 관계·seed 표, 정확한 노드 읽기,
주제 수, 스토어 수명(`_store_scope`), 작은 보조 함수.

`api/query.py` 가 여기 이름을 모두 다시 내보내므로 `q.X` 와 `from api.query import X` 는 그대로다.
두 이름은 같은 객체다: `q._SEED_TABLES` 를 고치면 이 모듈이 읽는 표가 바뀐다. 함수는 다르다:
`api.query` 의 이름을 패치해도 이 모듈 안의 호출은 그 패치를 보지 않는다. 여기 정의된 함수가
부르는 것을 바꾸려면 이 모듈의 이름을 패치한다.

이 모듈은 `api.query` 를 import 하지 않는다. 노드 종류·엣지 타입의 정본은 `api/node_schema.py`
이고, 같은 값인 표는 거기서 읽는다(`tests/core/test_query_base.py` 가 대조한다).
"""
from __future__ import annotations

from contextlib import contextmanager

from store import pkns
from store.config import load_config
from store.querybase import (
    QueryPlan,
    _props_to_obj,
    domains as _domain_rows,
    execute,
    validity_state,
)
from vinc_rag.retrieval import _node_fallback_search

from .node_schema import (
    CC_EDGES,
    EC_EDGES,
    NODE_MAP,
    OWNER_SCOPED_LABELS,
    SUPERSEDES_EDGE,
    SUPERSEDES_REL,
)


# ── 스키마에서 온 표 ─────────────────────────────────────────────────────────

#: 의미층 라벨 → PK. 관측·부착 플랜이 "상대편으로 올 수 있는 것"을 여기서 편다.
#: (store.embedded_schema._SEMANTIC_LABELS 와 같은 집합 — 그쪽은 스키마, 여기는 조회.)
_SEMANTIC_PK: dict[str, str] = {"Concept": "concept_id", "Decision": "decision_id"}
_SEMANTIC_LABELS = frozenset(_SEMANTIC_PK)

#: seed id 접두사 → (라벨, PK 열). 전용 테이블을 가진 종류(`NODE_MAP`)에서 Domain 을 빼고
#: 개방층 Entity 를 더한다. 표에 없는 접두사는 `_seed_table` 이 Entity 로 보내므로, 전용
#: 테이블을 가진 종류가 여기서 빠지면 시드·삭제·이웃 조회가 조용히 빈 결과를 낸다.
#: `domain:` 은 표에 없다: 무소유 구조라 정확한 읽기는 `node_plan` 이 따로 받고, 삭제는
#: 모르는 접두사로 거절한다.
_SEED_TABLES: dict[str, tuple[str, str]] = {
    **{prefix: table for prefix, table in NODE_MAP.items() if prefix != "domain"},
    "entity": ("Entity", "entity_id"),
}

#: owner_id 열을 가진 라벨. 삭제는 이 라벨만 owner 가드로 지우고 밖이면 "refused" 로
#: 거절하며, 노드 툼스톤도 이 라벨에만 남긴다. `node_schema.OWNER_SCOPED_LABELS` 와 같은 객체다.
_OWNER_SCOPED_LABELS = OWNER_SCOPED_LABELS

#: 의미층 관계 rel 다섯. `node_schema.CC_EDGES` 의 값을 그 순서대로 쓴다.
_CC_RELS = tuple(CC_EDGES.values())

#: fragment 엣지 타입명 → rel. 의미층 다섯과 about, supersedes 를 받는다. 읽는 곳은 `.get` 과
#: 정렬한 목록뿐이라 키 순서는 뜻이 없다.
_FRAGMENT_EDGE_TO_REL = {**CC_EDGES, **EC_EDGES, SUPERSEDES_EDGE: SUPERSEDES_REL}

# 동기화 대상 엣지 rel → **끝점 (src 라벨, src PK, dst 라벨, dst PK) 후보들**. 엣지
# 삭제 표면(delete_edge)과 sync 의 엣지 툼스톤 적용이 공유하는 단일 정의. fragment
# 타입명(relates_to·about…)도 여기로 정규화된다.
#
# ⚠ **손으로 적은 표를 두지 말 것.** 의미층이 늘면 rel 하나가 끝점 조합 여럿을 갖게
# 되고, 표가 낡으면 엣지 삭제가 **오류 없이 0건 매치**로 끝난다(툼스톤은 남고 엣지는
# 그대로 = 기기마다 다른 그래프). 스키마에서 파생한다
# ([[record:rel-tables-take-multiple-endpoint-pairs]]).
def _derive_sync_edge_rels(*, wiki: bool = False) -> dict[str, tuple[tuple[str, str, str, str], ...]]:
    from store import embedded_schema as _es
    pks = {lbl: spec["pk"] for lbl, spec in _es.node_table_specs().items()}
    out: dict[str, list[tuple[str, str, str, str]]] = {}
    for spec in _es.rel_table_specs():
        rel, src, dst = spec["label"], spec["from"], spec["to"]
        if rel not in _SYNCED_EDGE_RELS or src not in pks or dst not in pks:
            continue
        # v23 의 위키 쌍은 커뮤니티 그래프에만 선다. 동기화와 개인·팀 조회는 그 쌍을 훑지 않는다.
        if not wiki and _es.wiki_only_pair(rel, src, dst):
            continue
        out.setdefault(rel, []).append((src, pks[src], dst, pks[dst]))
    return {k: tuple(v) for k, v in out.items()}


_SYNCED_EDGE_RELS = _CC_RELS + ("ABOUT", "ATTACHES_TO", "SUPERSEDES")
SYNC_EDGE_RELS: dict[str, tuple[tuple[str, str, str, str], ...]] = _derive_sync_edge_rels()
#: 커뮤니티 그래프가 읽고 지우는 쌍 — 위 표에 v23 위키 쌍을 더한 것.
WIKI_EDGE_RELS: dict[str, tuple[tuple[str, str, str, str], ...]] = _derive_sync_edge_rels(wiki=True)


def _graph_read_pairs() -> dict[tuple[str, str, str, str], tuple[str, ...]]:
    """graph/node/edge context의 끝점 계약. 표면별 분류와 whitelist는 별도로 유지한다.

    sync 계약의 스키마 파생 쌍 중 의미 관계·SUPERSEDES와 ABOUT만 읽는다. RETURNS_TO는 기존
    Concept 관계이고, ATTACHES_TO/CONNECTS는 계속 부착 카드 경로가 담당한다.
    """
    groups: dict[tuple[str, str, str, str], list[str]] = {}
    for rel in _CC_RELS + ("ABOUT", "SUPERSEDES"):
        for pair in SYNC_EDGE_RELS[rel]:
            # 기록끼리의 SUPERSEDES 도 읽는다. 쓰기는 같은 종끼리만 받으므로(fragment)
            # 의미층 밖에서는 이 한 쌍만 실재한다. 빠지면 vinc_graph·vinc_brief 가
            # vinc_neighbors 보다 관계를 적게 센다(감사 F2).
            if ((pair[0] in _SEMANTIC_LABELS and pair[2] in _SEMANTIC_LABELS)
                    or rel == "ABOUT"
                    or (rel == "SUPERSEDES" and pair[0] == pair[2] == "Record")):
                groups.setdefault(pair, []).append(rel)
    return {pair: tuple(rels) for pair, rels in groups.items()}


_GRAPH_READ_PAIRS = _graph_read_pairs()


def _wiki_read_pairs() -> dict[tuple[str, str, str, str], tuple[str, ...]]:
    """커뮤니티 그래프만 더 읽는 v23 쌍 — 위키 관계 네 종과 ABOUT 중 위 계약 밖의 것."""
    groups: dict[tuple[str, str, str, str], list[str]] = {}
    for rel in ("RELATED_TO", "PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM", "ABOUT"):
        for pair in WIKI_EDGE_RELS[rel]:
            if pair not in SYNC_EDGE_RELS[rel]:
                groups.setdefault(pair, []).append(rel)
    return {pair: tuple(rels) for pair, rels in groups.items()}


_WIKI_READ_PAIRS = _wiki_read_pairs()
_CONCEPT_READ_PATTERN = "|".join(_GRAPH_READ_PAIRS[("Concept", "concept_id", "Concept", "concept_id")])

#: 방향이 곧 의미인 엣지. 배치(:func:`layout_positions`)와 상충 판정(:func:`_flow_conflicts`)이
#: 이 한 벌을 같이 읽는다. RELATED_TO 는 무방향이라(역방향이 같은 간선으로 접힌다)
#: 여기 없다 — 넣으면 정상 관계가 전부 모순으로 보고된다.
_DIRECTED_RELS = frozenset({"PREREQUISITE_OF", "DERIVES_FROM", "INHERITS_FROM"})
# ⚠ 배치가 뒤집어 읽는 관계. `DERIVES_FROM(a→b)` 은 "a 가 b 에서 나왔다"이므로 **b 가
# 상류**다. "A inherits from B" 도 같은 모양으로 **B 가 위**다. 좌측 문서 트리(`_nav_depth`)도
# 두 관계를 뒤집어 적재하므로, 둘 중 하나라도 빠지면 같은 데이터를 트리는 "원본이 위",
# rank 배치는 "파생물이 왼쪽"으로 반대로 읽는다.
#
# ⚠ 이 표를 고치면 **기존 지도의 뜻이 바뀐다**(상속을 가진 그래프의 x 축 순서).
# 그래서 앱의 인과 원뿔(`app/src/spotlight.ts` `UPSTREAM_REVERSED`)과 **같은 값**을
# 유지해야 한다 — 한쪽만 옮기면 하이라이트가 배치를 반박한다.
# 근거: [[record:inherits-from-now-reverses-with-derives-from]]
_REVERSED_RELS = ("DERIVES_FROM", "INHERITS_FROM")


# ── 작은 보조 함수 ───────────────────────────────────────────────────────────

#: `domain`(단수, 하위호환)과 `domains`(복수)를 하나의 목록으로 접는다. 빈 문자열과
#: 중복은 떨어뜨리되 **순서는 유지**한다 — 응답이 스코프를 에코할 때 흔들리지 않게.
def normalize_domains(domain: str | None = None,
                      domains: list[str] | None = None) -> list[str]:
    raw = list(domains or ([] if domain is None else [domain]))
    out: list[str] = []
    for d in raw:
        d = (d or "").strip()
        if d and d not in out:
            out.append(d)
    return out


#: 추적열 투영 한 자리(W3): 읽기 표면이 `rev`·`updated_at` 을 함께 낸다. 쓰기가 버전을
#: 손에 쥐려면 읽기가 먼저 그것을 줘야 한다. 쓰기 쪽 대조는 **선택**이다: `expected_rev` 를
#: 실은 쓰기만 이 값과 맞춰 보고 어긋나면 바꾸지 않고 거절한다(`api/mutation_contract.py`).
#: 근거: [[vinc.plan.mcp-agent-surface §6]] · 쓰기 쪽 조건은
#: [[vinc.design.mutation-lifecycle-mcp-api-cli-2026-10-13]] · 세 열의 의미는 [[vinc.spec.dataplane §6.1]].
#:
#: ⚠ 이 두 열은 `store/embedded_schema.py` 의 `_TRACK_COLS` 가 붙는 라벨에만 있다
#: (Concept·Episode·Record·Bridge·Entity·Decision). **Domain 에는 없다** — 도메인
#: 좌표 투영에 옮겨 붙이면 Binder 가 죽고 그 조회 전체가 함께 죽는다(이 파일이
#: pinned·content_time·one_liner 에서 이미 세 번 겪은 자리).
#:
#: 열 목록을 여기 한 벌만 두는 이유: 두 벌이 되는 순간 "저장이 안 됐다"와 "투영이
#: 빠뜨렸다"가 구분되지 않는다(`_KEPT_FIELDS` 전례).
def _track_cols(alias: str, prefix: str = "") -> str:
    return (f"coalesce({alias}.rev, 0) AS {prefix}rev, "
            f"coalesce({alias}.updated_at, '') AS {prefix}updated_at")


def _stamp_validity(base: dict, valid_from, valid_until) -> None:
    """노드 **자신**의 유효구간과 파생 상태를 상세에 싣는다. 부착 목록(links.items)만
    구간을 갖고 노드가 안 가지면, id 로 직접 연 historical 기록이 현재형 사실로
    읽힌다(라이브 재현: `record:fact-engines-orphans`). `superseded_by` 는 props 의
    임시 인코딩(§8 항목 4)이라 같은 자리에서 꺼내 준다 — 후속으로 건너갈 유일한 길."""
    base["valid_from"] = str(valid_from or "")
    base["valid_until"] = str(valid_until or "")
    base["validity"] = validity_state(base["valid_from"], base["valid_until"])
    props = base.get("props") if isinstance(base.get("props"), dict) else {}
    base["superseded_by"] = str(props.get("superseded_by") or "")


def _bridge_owner_pred(alias: str) -> str:
    """Bridge 소유 술어(F5, **과도기**) — ``(owner OR '')``. '' = v2 마이그레이션이
    백필한 소유 없는 행: 신뢰할 소유 원천이 없어(호스티드 다중 테넌트 혼재)
    실소유 백필이 불가하고, 엄격 ``= $owner`` 는 기존 로컬 사용자의 L0 결합을 즉시
    소거하는 회귀다. 이 술어는 오늘의 완전 무스코프(전 테넌트 전량 노출)를 "내 것 +
    마이그레이션 이전분"으로 좁히는 단조 개선이며, 신규 쓰기는 전부 스탬프되므로
    (fragment·bridges_ingest) 새 데이터는 누출 불가. 로컬 백필(bridges_ingest
    재실행)이 보편화되면 OR 절만 삭제해 엄격 전환한다 — 이 함수가 유일 정의 지점."""
    return f"({alias}.owner_id = $owner OR {alias}.owner_id = '')"


def _edge_cost(edge: dict) -> float:
    """엣지 통행 비용 = 1 - confidence(범위 밖·비수치·미기입은 0.0 신뢰도로 강등).
    순수 함수 — :func:`weighted_paths` 의 유일한 비용 정의 지점."""
    try:
        c = float(edge.get("confidence") or 0.0)
    except (TypeError, ValueError):
        c = 0.0
    return 1.0 - min(1.0, max(0.0, c))


# 시간축(D72). `recorded` = 기록 시각(현행 ts), `content` = 문서 내용이 가리키는 시점.
BASIS_RECORDED = "recorded"
BASIS_CONTENT = "content"


# ── 스토어 수명 ──────────────────────────────────────────────────────────────

@contextmanager
def _store_scope(store, cfg, opener):
    """스토어를 받았으면 그대로 넘기고, 못 받았으면 ``opener(cfg or load_config())`` 로 열어
    넘긴다. 블록이 끝나면(예외 포함) **자기가 연 스토어만** 닫는다. 받은 스토어는 열지도
    닫지도 않는다: 수명은 호출부 소유다.

    열 수 없으면(``opener`` 가 None) None 을 그대로 넘기고 아무것도 닫지 않는다. 그때 무엇을
    답할지는 이 함수가 정하지 않고 호출부가 정한다. 읽기는 ``executed:False`` 와 ``note``
    로 강등해 답하고, 쓰기는 ``note`` 와 ``error`` 를 같이 싣는다(``rename_domain`` 만
    ``error`` 하나). 이 함수는 그 모양을 건드리지 않는다.

    ``opener`` 는 호출 모듈이 자기 이름공간의 ``open_store`` 를 넘긴다. 이 모듈의 이름을
    직접 부르면 모듈별 ``open_store`` 패치(시험이 모듈마다 갈아 끼운다)가 닿지 않는다."""
    owns = store is None
    if owns:
        store = opener(cfg or load_config())
    try:
        yield store
    finally:
        if owns and store is not None:
            store.close()


# ── seed 표와 정확한 노드 읽기 ───────────────────────────────────────────────

def _seed_table(seed: str) -> tuple[str, str]:
    """seed id → (라벨, PK 열). 접두사 없음(콜론 부재) = Concept(하위호환).
    **미지 접두사 = Entity**(F1) — 개방층 노드 id 는 사용자 저작 타입이 접두사라
    (`theory:duality` 등) 열거 불가능하다; 전용 테이블의 접두사는 전부 위 표에 있으므로
    표 밖 접두사는 개방층으로 라우팅하는 것이 정확하다(Concept 로 보내면
    theory:* 시드를 '없음'으로 답한다)."""
    prefix = seed.split(":", 1)[0] if ":" in seed else ""
    if not prefix:
        return ("Concept", "concept_id")
    return _SEED_TABLES.get(prefix, ("Entity", "entity_id"))


def node_plan(node_id: str, *, owner: str) -> QueryPlan:
    """정확한 id의 저장 필드만 읽는다. 이웃·발췌·배치는 조회하지 않는다."""
    from store.embedded_schema import node_table_specs

    if node_id.startswith("domain:"):
        name = node_id[len("domain:"):]
        # Domain은 무소유 구조다. 소유자가 쓰는 주제와 그 조상만 정확히 읽는다.
        # 근거: [[vinc.spec.dataplane §2.1]]
        branches = [
            f"MATCH (n:{label}) WHERE n.owner_id = $owner "
            "AND (n.domain = $name OR n.domain STARTS WITH $prefix) "
            "RETURN $name AS name"
            for label in ("Concept", "Record", "Episode", "Decision")
        ]
        return QueryPlan("node", " UNION ALL ".join(branches),
                         {"owner": owner, "name": name, "prefix": name + "/"})
    label, pk = _seed_table(node_id)
    # 열 목록은 스키마를 따른다. 종류에 없는 props나 유효구간을 만들어 내지 않는다.
    # 근거: [[vinc.spec.dataplane §2.2]]
    columns = node_table_specs()[label]["cols"]
    fields = ", ".join(f"n.{col} AS {'id' if col == pk else col}" for col in columns)
    if node_id.startswith("concept:doc."):
        from store.search_temporal import FIELDS
        dates = ", ".join(f"d.{key} AS _doc_{key}" for key in FIELDS)
        return QueryPlan("node", f"MATCH (n:{label}) WHERE n.{pk} = $node "
                         "AND n.owner_id = $owner OPTIONAL MATCH (d:_DocMeta) "
                         "WHERE d.doc_id = $doc AND d.owner_id = $owner "
                         f"RETURN {fields}, d.doc_id AS _doc_id, {dates}",
                         {"node": pkns.key(owner, node_id), "owner": owner,
                          "doc": node_id[len("concept:doc."):]})
    return QueryPlan("node", f"MATCH (n:{label}) WHERE n.{pk} = $node "
                     f"AND n.owner_id = $owner RETURN {fields}",
                     {"node": pkns.key(owner, node_id), "owner": owner})


def node(node_id: str, *, owner: str, cfg=None, store=None) -> dict:
    """한 노드를 저장된 모양으로 돌려준다. 없는 노드와 다른 소유자의 노드는 같다."""
    result = execute(node_plan(node_id, owner=owner), cfg, store=store)
    rows = result.pop("rows", [])
    if not result.get("executed"):
        return result
    result["found"] = bool(rows)
    result["node"] = None
    if rows:
        stored = dict(rows[0])
        doc_meta = None
        if "_doc_id" in stored:
            from store.search_temporal import FIELDS
            present = stored.pop("_doc_id") is not None
            values = {key: stored.pop("_doc_" + key, None) for key in FIELDS}
            if present:
                doc_meta = values
        if node_id.startswith("domain:"):
            stored = {"id": node_id, "kind": "domain", "name": stored["name"]}
        else:
            stored["kind"] = stored.get("type") or _seed_table(node_id)[0].lower()
        if "props" in stored:
            stored["props"] = _props_to_obj(stored["props"])
        from store.search_temporal import metadata
        # A domain is a namespace container, not dated evidence. Keep its exact read shape.
        # Other kinds retain their stored top-level columns; normalized dates are nested.
        if stored.get("kind") != "domain":
            stored["temporal"] = metadata(doc_meta if doc_meta is not None else stored)
            # Exact reads retain their single-query, neighbor-independent contract.
            # Context/brief/search assess lineage; this endpoint exposes stored dates only.
            stored["temporal"]["claim_status"] = "unassessed"
            stored["temporal"]["claim_assessment_scope"] = "stored_fields"
        result["node"] = stored
    return result


# ── 주제 수와 주제 설명 미러 ─────────────────────────────────────────────────

#: 문서 미러 개념의 id 접두사(`concept:doc.<doc_id>`). `api/docstore.py` 의 `_DOC_MIRROR_PREFIX`
#: 와 같은 문자열이어야 한다.
_DOC_CONCEPT_PREFIX = "concept:doc."


def _topic_description_mirrors_by_domain(owner: str | None, cfg=None, store=None) -> dict[str, str] | None:
    """주제 설명 문서 미러의 논리 id → 그 미러가 선 주제. 설명은 주제(Domain)가 글을 못 담아 문서로 두고
    그 미러의 domain 으로 주제에 속한다(`api/topic_description.py`). 그래서 미러가 그 주제의 노드처럼
    읽힌다. 지도 표시·주제 수·주제 상세가 이 한 벌로 그것을 가른다. 읽기에 실패하면 None 이고, 부르는
    쪽은 수를 고치지 않고 그대로 둔다(빼다 만 수보다 낫다)."""
    if not owner:
        return {}
    from api import topic_description
    res = execute(QueryPlan("topic_description_mirrors",
        "MATCH (d:_DocMeta) WHERE d.origin = $origin AND d.owner_id = $owner "
        "RETURN d.doc_id AS doc_id", {"origin": topic_description.ORIGIN, "owner": owner},
        note="topic description mirrors"), cfg, store=store)
    if not res.get("executed"):
        return None
    ids = [_DOC_CONCEPT_PREFIX + str(r["doc_id"]) for r in res.get("rows", []) if r.get("doc_id")]
    if not ids:
        return {}
    got = execute(QueryPlan("topic_description_mirror_domains",
        "MATCH (c:Concept) WHERE c.owner_id = $owner AND c.concept_id IN $ids "
        "RETURN c.concept_id AS id, c.domain AS domain",
        {"owner": owner, "ids": pkns.keys(owner, ids)}, note="topic description mirror topics"),
        cfg, store=store)
    if not got.get("executed"):
        return None
    return {pkns.strip(str(r["id"])): str(r.get("domain") or "") for r in got.get("rows", []) if r.get("id")}


def domains(*, owner: str | None = None, cfg=None, store=None, debug: bool = False) -> dict:
    """주제별 노드 수(:func:`store.querybase.domains`)에서 주제 설명 미러를 뺀다. 설명은 그 주제의
    노드가 아니라 주제에 대한 글이다. 뺀 수는 숨기지 않고 행의 ``topic_descriptions`` 로 밝힌다.
    설명만 남은 주제도 행은 남긴다(total 0): 주제는 있고, 그 설명에 닿는 길이 여기다."""
    res = _domain_rows(owner=owner, cfg=cfg, store=store, debug=debug)
    if not res.get("executed") or not owner:
        return res
    mirrors = _topic_description_mirrors_by_domain(owner, cfg, store)
    if not mirrors:
        return res
    per: dict[str, int] = {}
    for d in mirrors.values():
        per[d or "(none)"] = per.get(d or "(none)", 0) + 1
    rows = []
    for r in res.get("rows", []):
        n = min(per.get(r["domain"], 0), r["concepts"])
        rows.append({**r, "concepts": r["concepts"] - n, "total": r["total"] - n,
                     "topic_descriptions": n} if n else r)
    rows.sort(key=lambda r: (-r["total"], r["domain"]))
    return {**res, "rows": rows}


# ── 실패 응답의 다음 행동 (F7) ───────────────────────────────────────────────
# 실패는 **무엇**만이 아니라 **다음**을 말해야 한다: `error`(무엇) + `hint`(다음) +
# `candidates`(어디로). 없는 노드 조회가 `{"executed": true}` 만 답하면 성공과
# 구분되지 않는다([[vinc.plan.mcp-hardening]]).

def near_nodes(store, needle: str, *, owner: str | None = None,
               limit: int = 3) -> list[dict]:
    """놓친 id/제목에 가까운 노드 후보 — 이미 있는 폴백 검색을 그대로 쓴다.

    새 색인을 만들지 않는다. `_node_fallback_search` 가 제목·요약·props 를 토큰 OR 로
    훑고 점수로 좁히는 일을 이미 한다. 접두사(`concept:`)는 떼고 넣는다 — 사용자가
    `concept:없는것` 을 줬을 때 접두사까지 검색어에 남으면 전부 똑같이 걸린다.
    은퇴한 문서의 미러는 검색과 같은 규칙(`doc_intervals`)으로 힌트에서도 뺀다."""
    probe = needle.split(":", 1)[1] if ":" in needle else needle
    if not probe.strip():
        return []
    try:
        rows = _node_fallback_search(store, probe, limit=max(1, limit), owner=owner,
                                     doc_intervals=True)
    except Exception:                                             # noqa: BLE001
        return []                      # 힌트를 만들다 본 응답을 죽이지 않는다
    return [{"id": r.get("id"), "title": r.get("title") or "",
             "domain": r.get("domain") or ""} for r in rows[:limit]]
