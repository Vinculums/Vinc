# -*- coding: utf-8 -*-
"""구조 진단과 커뮤니티: 주제 뿌리·연결 성분·주장 진단(`vinc_diagnose`)과 Leiden 커뮤니티 읽기.

`domain_root_diagnosis` 는 주제 트리의 뿌리를 센다. `component_diagnosis` 는 의미층을 잇는 관계(`ALGO_REL_TABLES`)로
본 약연결 성분과 고아를, `claim_diagnosis` 는 같은 파일을 두고 어긋난 기록 짝과 닻 없는 기록, 잰 뒤에 경로가 바뀐
주장(`_stale_claims`)을 읽는다. `communities`·`community_context` 는 같은 투영(`_algo_graph`) 위에서 `api/graph_algo.py`
의 Leiden 으로 군집을 나누고, 한 노드가 속한 군집의 대표를 `node_read` 의 `node_context` 로 읽는다. 투영을 소유자로
거르는 `_owner_concept_ids`, 두 스페이스의 같은 id 를 갈라 내는 `_ambiguous_algo_ids`, 구성원 상한 `_clamp_limit` 은
진단과 커뮤니티가 같이 쓴다.

`api/query.py` 가 여기 이름을 모두 다시 내보내므로 `q.component_diagnosis`·`q.X` 와 `from api.query import X` 는
그대로다. 두 이름은 같은 객체다. 여기 함수가 부르는 이름(`_run_algo`·`_algo_graph`·`open_store`·`node_context` 등)은
이 모듈에서 찾으므로, 그 호출을 바꾸려면 `api.query` 가 아니라 이 모듈의 이름을 패치한다.

스토어를 안 받으면 `_store_scope`(`api/query_base.py`)가 열고 닫는다. 열 수 없을 때의 규칙: 읽기는 `executed:False`
에 `note` 만 싣는다.

이 모듈은 `api.query` 를 import 하지 않는다(`tests/core/test_diagnose_module.py` 가 본다).
"""
from __future__ import annotations

import datetime as _dt
import re

from store import failure_codes as fc, pkns
from store.backend import open_store
from store.config import load_config
from store.querybase import _classify_chunk_error, _props_to_obj, mirror_id

from .graph_layout import _domain_path
from .node_read import node_context
from .node_schema import normalize_subject_path, normalize_time
from .query_base import _store_scope, domains


# ── 구조 진단 (E-G3) — 약연결 성분 · Leiden 커뮤니티 · 조회 전용 ─────────────────
# 계산은 `api/graph_algo.py`(순수 파이썬)가 한다. 2026-09-28 까지는 엔진 algo 확장에
# 맡겼는데, ladybug 0.20 확장이 번들되지 않은 공유 라이브러리(networkit·arrow)를 물어
# 허브에서 세 진단이 전부 꺼졌다. 옮기면서 Louvain 을 Leiden 으로 바꿨다.
# 근거: [[vinc.ops.ladybug-upgrade-review-2026-09-24 §2.3]] (원인), 문헌 조사 §10 레버 1·2
# 투영은 여기서 소유자로 거른다(엔진 투영은 전체 표만 됐다). 강등 계약은 그대로다:
# 스토어를 못 읽으면 executed=False + note 이고 크래시하지 않는다.

def _owner_concept_ids(store, owner: str | None) -> set[str] | None:
    """owner 의 **의미층** 노드 id 집합(스코프 교차용). owner=None(로컬)이면 None(전체).

    v12 — Decision 을 함께 센다. 알고리즘 프로젝션이 의미층 관계 4종의 끝점을 전부
    투영하므로(store.embedded_backend._algo_projection) 결과 행에 결정이 섞여 오는데,
    이 집합이 개념만 담으면 그 행들이 **말없이** 스코프 밖으로 떨어진다 — 결정은
    컴포넌트에도 고아 목록에도 영영 안 나오고, 그 사실을 알릴 자리도 없다."""
    if not owner:
        return None
    rows = store.run("MATCH (c:Concept) WHERE c.owner_id = $owner "
                     "RETURN c.concept_id AS id", {"owner": owner})
    ids = {r["id"] for r in rows if r.get("id")}
    drows = store.run("MATCH (d:Decision) WHERE d.owner_id = $owner "
                      "RETURN d.decision_id AS id", {"owner": owner})
    return ids | {r["id"] for r in drows if r.get("id")}


def _topic_description_mirrors(store, owner: str | None) -> set[str]:
    """토픽 설명 문서의 미러 id(논리 id). 설명은 일부러 잇지 않고 미러의 domain 으로
    토픽에 속하므로(`api/topic_description.py`) 성분 진단은 이것을 고아로 세지 않는다.

    `_DocMeta.doc_id` 에는 소유자 접두가 없고 진단 행의 id 도 논리 id 라서, 접두 없이
    `concept:doc.` 만 붙여 맞춘다. 네임스페이싱을 켜도 꺼도 같은 집합이 나온다."""
    from api import topic_description
    params: dict = {"origin": topic_description.ORIGIN}
    where = ""
    if owner:
        where = "AND d.owner_id = $owner "
        params["owner"] = owner
    rows = store.run("MATCH (d:_DocMeta) WHERE d.origin = $origin " + where
                     + "RETURN d.doc_id AS doc_id", params) or []
    return {mirror_id(str(r["doc_id"])) for r in rows if r.get("doc_id")}


#: 알고리즘 결과가 **두 스페이스의 노드를 같은 id 로** 물고 온 것들.
#:
#: 🔴 owner 스코프를 논리 id 문자열 집합과의 교차로 걸기 때문에 생긴다
#: (`_owner_concept_ids`). 한 소유자 안에서 id 는 기본키라 유일하니 교차는 안전해
#: 보이는데, 같은 슬러그를 가진 남의 노드가 문자열이 같다는 이유로 통과한다. 새
#: 스페이스는 전부 같은 기본 역할 넷으로 시작하므로 드문 일이 아니다.
#:
#: 어느 쪽이 내 것인지는 알 방법이 없다 — 알고리즘 행에 소유자가 없고, owner 만
#: 투영하는 길은 엔진이 막는다(cypher 투영 그래프는 WCC 를 Binder 가 거절한다).
#: 그래서 **고르지 않고 갈라 낸다.** 세 진단이 같은 함수를 쓴다.
#: 근거: [[record:one-logical-id-in-two-spaces-reads-as-two-orphans-2026-09-10]]
def _ambiguous_algo_ids(rows, scope: set[str] | None) -> list[str]:
    """한 id 가 서로 다른 그룹 값으로 두 번 이상 나오면 그것은 두 스페이스의 노드다."""
    seen: dict = {}
    for r in rows or []:
        cid = r.get("id")
        if not cid or (scope is not None and cid not in scope):
            continue
        seen.setdefault(cid, set()).add(r.get("value"))
    return sorted(cid for cid, vals in seen.items() if len(vals) > 1)


#: 성분 진단이 걷는 관계 — 의미층을 **실제로 잇는** 관계 전부. 엔진 시절의
#: `EmbeddedBackend._ALGO_REL_TABLES` 와 같은 목록이고 같은 이유다: ABOUT·ATTACHES_TO 를
#: 빼면 결정·사건·기록이 거짓 고아로 나온다(2026-08-09, 결정 67건 중 43건).
ALGO_REL_TABLES = ("RELATED_TO", "PREREQUISITE_OF", "DERIVES_FROM",
                   "INHERITS_FROM", "ABOUT", "ATTACHES_TO")
from store.embedded_schema import _LOUVAIN_PROJECTED_LABELS as _LOUVAIN_LABELS  # noqa: E402


def _algo_graph(store, owner: str | None, rel_tables: tuple,
                labels: tuple | None = None) -> tuple[list[str], list[tuple]]:
    """진단용 투영 — 관계 표의 끝점 라벨에 속한 노드 전부와 그 관계들. 소유자가 있으면
    **양 끝이 모두 그 소유자**인 것만 담는다. 라벨·PK·끝점 쌍은 스키마에서 파생한다
    (손으로 적은 목록이 낡아 진단을 통째로 죽인 일이 있다 — `_algo_projection` 주석).

    id 는 논리 id 로 돌아온다(네임스페이싱 스토어의 물리 키는 조회 계층이 벗긴다)."""
    from store import embedded_schema as _es
    pks = {lbl: spec["pk"] for lbl, spec in _es.node_table_specs().items()}
    want = set(rel_tables)
    # ``labels`` 를 주면 양 끝이 그 표인 쌍만 읽는다. 군집은 v23 에 넓어진 RELATED_TO 중
    # 개념끼리만 본다(`_LOUVAIN_PROJECTED_LABELS`).
    pairs = [(sp["label"], sp["from"], sp["to"]) for sp in _es.rel_table_specs()
             if sp["label"] in want and sp["from"] in pks and sp["to"] in pks
             and (labels is None or (sp["from"] in labels and sp["to"] in labels))]
    labels: list[str] = []
    for _rel, a, b in pairs:
        for lbl in (a, b):
            if lbl not in labels:
                labels.append(lbl)
    params = {"o": owner} if owner else None
    nodes: list[str] = []
    for lbl in labels:
        where = "WHERE n.owner_id = $o " if owner else ""
        for r in store.run(f"MATCH (n:{lbl}) {where}RETURN n.{pks[lbl]} AS id", params) or []:
            if r.get("id"):
                nodes.append(pkns.strip(r["id"]))
    edges: list[tuple] = []
    for rel, a, b in pairs:
        where = "WHERE x.owner_id = $o AND y.owner_id = $o " if owner else ""
        for r in store.run(f"MATCH (x:{a})-[:{rel}]->(y:{b}) {where}"
                           f"RETURN x.{pks[a]} AS a, y.{pks[b]} AS b", params) or []:
            if r.get("a") and r.get("b"):
                edges.append((pkns.strip(r["a"]), pkns.strip(r["b"])))
    return nodes, edges


def _run_algo(store, algo: str, *, rel_tables: tuple | None = None,
              owner: str | None = None, graph_out: dict | None = None,
              labels: tuple | None = None):
    """진단 알고리즘을 돌린다 — (rows, None) 또는 (None, note). rows 는 엔진 시절과 같은
    `[{id, value}]` 모양이라 소비자 셋이 그대로 읽는다.

    ``algo``: ``weakly_connected_components`` 또는 ``leiden``. ``rel_tables`` 미지정이면
    :data:`ALGO_REL_TABLES`. 커뮤니티는 호출부가 RELATED_TO 만 넘긴다 — 개념 사이의
    느슨한 연관이 군집의 뼈대이고, 방향 관계는 도메인 축과 따로 가는 '실측 군집'에
    섞이지 않게 둔다(엔진 시절의 선택을 그대로 잇는다)."""
    from . import graph_algo as ga
    fns = {"weakly_connected_components": ga.weakly_connected_components,
           "leiden": ga.leiden}
    fn = fns.get(algo)
    if fn is None:
        return None, f"algo_unsupported: {algo!r} (지원: {sorted(fns)})"
    try:
        nodes, edges = _algo_graph(store, owner, tuple(rel_tables or ALGO_REL_TABLES), labels)
        assign = fn(nodes, edges)
        if graph_out is not None:
            # 품질 지표가 같은 투영을 다시 읽지 않게 넘겨준다.
            graph_out.update(nodes=nodes, edges=edges, assign=assign)
    except Exception as exc:
        return None, f"{_classify_chunk_error(exc)}: {type(exc).__name__}: {exc}"
    return [{"id": n, "value": v} for n, v in assign.items()], None


# ── 자르면 잘렸다고 말한다 (F6) ──────────────────────────────────────────────
# 실측(2026-08-08): `vinc_diagnose(components)` 가 `size:173` 인 컴포넌트의 members 를
# **50개만 싣고 아무 표시도 안 했다.** 무음 절단은 이 리포가 반복해 물린 형태다 —
# 받는 쪽은 그게 전부인 줄 알고, 그 위에서 내린 판단이 조용히 틀린다.
#
# 원칙: 자른 자리마다 ① 상한을 **호출자가 정할 수 있게** 하고 ② 잘렸다는 사실과
# ③ 전체 개수를 함께 싣는다. `size` 처럼 전체 수가 이미 있으면 그것으로 갈음한다.
MEMBER_LIMIT_DEFAULT = 50
MEMBER_LIMIT_MAX = 1000


def _clamp_limit(val, default: int, cap: int) -> int:
    """호출자가 준 상한을 안전 범위로 접는다. 0·음수·비정수는 기본값."""
    try:
        n = int(val)
    except (TypeError, ValueError):
        return default
    return max(1, min(n, cap)) if n > 0 else default


def domain_root_diagnosis(*, owner: str, cfg=None, store=None,
                          member_limit: int | None = None) -> dict:
    """Read-only steward census of topic roots; multiple roots are not an error.

    Count the four authored kinds that carry a topic, not derived Domain nodes
    or Bridges. Run only on request, never on the ingest or layout path.
    """
    if not owner:
        return {"kind": "domain_root_diagnosis", "executed": False,
                "note": "an owner is required", fc.KEY: fc.OWNER_REQUIRED}
    census = domains(owner=owner, cfg=cfg, store=store)
    if not census.get("executed"):
        return dict(census, kind="domain_root_diagnosis")
    counts: dict[str, int] = {}
    assigned, unassigned = [], 0
    for row in census.get("rows", []):
        path = _domain_path(row["domain"]) if row["domain"] != "(none)" else ()
        if not path:
            unassigned += row["total"]
            continue
        root = path[0]
        counts[root] = counts.get(root, 0) + row["total"]
        assigned.append(dict(row, root=root))
    total = sum(counts.values())
    roots = [{"root": root, "nodes": n, "share": n / total if total else 0}
             for root, n in sorted(counts.items(), key=lambda x: (-x[1], x[0]))]
    # A tie is not broken alphabetically into a fabricated dominant root.
    largest = roots[0]["root"] if roots and (len(roots) == 1 or
              roots[0]["nodes"] > roots[1]["nodes"]) else None
    outside = [r for r in assigned if largest and r["root"] != largest]
    outside.sort(key=lambda r: (-r["total"], r["domain"]))
    limit = _clamp_limit(member_limit, MEMBER_LIMIT_DEFAULT, MEMBER_LIMIT_MAX)
    return {"kind": "domain_root_diagnosis", "executed": True,
            "counted_kinds": ["concept", "decision", "record", "episode"],
            "roots": roots, "largest_root": largest, "assigned_nodes": total,
            "unassigned_nodes": unassigned,
            "outside_largest_root": outside[:limit],
            "outside_domain_count": len(outside),
            "outside_node_count": sum(r["total"] for r in outside),
            "members_truncated": len(outside) > limit,
            "note": "Review unexpected topic roots; multiple roots can be intentional. "
                    "This census does not reject writes or change the layout."}


def component_diagnosis(*, owner: str | None = None, cfg=None, store=None,
                        member_limit: int | None = None) -> dict:
    """E-G3 WCC 구조 진단 — 연결 컴포넌트 수·크기 분포 + 고아(단일 노드) 개념 목록.
    "연결 안 된 섬 n개"(상태 화면)와 suggest_ties 후보 소스의 근거. 조회 전용.

    반환 {executed, components:[{group, size, members, members_truncated}],
    orphans:[id…], component_count, orphan_count, ambiguous, ambiguous_count,
    topic_descriptions_excluded}. 스토어를 못 읽으면 executed=False + note.

    ``ambiguous`` — **두 스페이스에 같은 슬러그로 존재해 어느 쪽이 내 것인지 못 가리는
    id**. 컴포넌트에도 고아에도 안 넣는다. 왜 생기는지는 아래 계산 자리의 주석에 있다.

    ``topic_descriptions_excluded`` — 컴포넌트와 고아에서 뺀 토픽 설명 미러의 수. 설명
    문서는 일부러 잇지 않고 미러의 domain 으로 토픽에 속하므로 이을 대상이 아니다. 그래서
    고아 목록에는 실제로 이어야 할 것만 남는다(`_topic_description_mirrors`).

    ``member_limit`` — 컴포넌트당 실을 구성원 수(기본 50, 상한 1000). 여태 50 으로
    **말없이** 잘라서, `size:173` 인 컴포넌트도 50개만 보이고 그게 전부인 줄 알기
    쉬웠다(F6). 이제 자르면 `members_truncated:true` 로 말한다 — 전체 수는 `size` 가
    이미 나른다."""
    cfg = cfg or load_config()
    with _store_scope(store, cfg, open_store) as store:
        base = {"kind": "component_diagnosis", "executed": False,
                "components": [], "orphans": [], "component_count": 0, "orphan_count": 0}
        if store is None:
            base["note"] = "no reachable store"
            base[fc.KEY] = fc.NO_REACHABLE_STORE
            return base
        rows, note = _run_algo(store, "weakly_connected_components", owner=owner)
        if rows is None:
            base["note"] = note
            return base
        scope = _owner_concept_ids(store, owner)
        # 2026-09-10 실측: `concept:agent-steward` 가 크기 1 컴포넌트로 **네 번** 나왔고
        # 그 수가 그 슬러그를 가진 스페이스 수와 맞았다. 사유는 `_ambiguous_algo_ids`.
        ambiguous = _ambiguous_algo_ids(rows, scope)
        drop = set(ambiguous)
        mirrors = _topic_description_mirrors(store, owner)
        excluded: set[str] = set()
        groups: dict = {}
        # 성분의 실제 노드 수를 종류 불문으로 센다. 개념만 세면 레코드·사건·결정에만
        # (ATTACHES_TO·ABOUT 으로) 붙은 개념이 저 혼자인 것처럼 보여 거짓 고아가 된다 —
        # 그 개념을 고아로 지우면 쓰이고 있는 노드를 지운다. 미러는 일부러 안 잇는 토픽
        # 설명 노드라 크기에서 뺀다. 근거: [[vinc.manual.core]]
        full_size: dict = {}
        for r in rows:
            cid, grp = r.get("id"), r.get("value")
            if not cid or cid in drop or grp is None:
                continue
            if cid not in mirrors:
                full_size[grp] = full_size.get(grp, 0) + 1   # 개념·레코드·사건·결정 전부
            if scope is not None and cid not in scope:
                continue                   # 비-개념은 크기엔 세고 개념 목록엔 안 넣는다
            if cid in mirrors:
                excluded.add(cid)          # 토픽 설명은 빼고 수만 밝힌다
                continue
            groups.setdefault(grp, []).append(cid)
        mlim = _clamp_limit(member_limit, MEMBER_LIMIT_DEFAULT, MEMBER_LIMIT_MAX)
        comps = [{"group": g, "size": len(m), "members": sorted(m)[:mlim],
                  "members_truncated": len(m) > mlim}
                 for g, m in groups.items()]
        comps.sort(key=lambda c: (-c["size"], str(c["group"])))   # 큰 컴포넌트 우선
        # 고아 = 성분에 저 혼자뿐인 개념. 개념 목록상 홀로여도(len==1) 성분에 레코드·
        # 사건·결정이 함께 있으면(full_size>1) 고아가 아니다 — 들어오는 엣지도 연결이다.
        orphans = sorted(m[0] for g, m in groups.items()
                         if len(m) == 1 and full_size.get(g, 1) == 1)
        base.update(executed=True, components=comps, orphans=orphans,
                    component_count=len(comps), orphan_count=len(orphans),
                    ambiguous=ambiguous, ambiguous_count=len(ambiguous),
                    topic_descriptions_excluded=len(excluded))
        if ambiguous:
            base["ambiguous_note"] = (
                "these ids exist in more than one space, so this diagnosis cannot tell "
                "which node is yours and leaves them out of components and orphans")
    return base


#: 경로처럼 생긴 토큰 — 슬래시가 하나 이상 있고 짧은 확장자로 끝난다. `and/or` 나
#: 날짜를 잡지 않고 `api/sync.py` · `app/src/surfaces/view3d.ts` 를 잡는 최소 모양이다.
#: URL 은 아래에서 먼저 지우고 찾는다(`https://vincs.io/docs/x.html` 이 경로로 보인다).
_PATH_TOKEN = re.compile(r"(?<![\w/.-])(?:[\w.-]+/)+[\w.-]+\.[A-Za-z]{1,5}(?![\w/])")
_URLISH = re.compile(r"\w+://\S+")
#: 주제가 될 수 없는 경로 — 가상환경·의존성·빌드 산출물. 실측(2026-08-25): 닻 없는
#: 45건 중 하나가 `./.venv/Scripts/python.exe` 였는데 그건 **명령**이지 주장의 대상이
#: 아니다. 이 목록은 줄이라고 있는 것이라, 줄일 수 없는 항목이 남으면 아무도 안 줄인다.
_NOT_A_SUBJECT = re.compile(
    r"(^|/)(\.venv|venv|node_modules|dist|build|__pycache__|\.git|target)(/|$)")
#: 주장 진단이 훑는 텍스트 필드(props 안). 노트 이름이 두 벌 쓰인다(note·ko).
_CLAIM_TEXT_KEYS = ("note", "ko", "state", "summary")


def _path_hint(props: dict, title: str) -> str:
    """이 기록이 코드를 두고 말하고 있나 — 그렇다면 처음 나온 경로 토큰."""
    parts = [title] + [str(props.get(k) or "") for k in _CLAIM_TEXT_KEYS]
    text = _URLISH.sub(" ", "\n".join(parts))
    # 첫 매치가 아니라 **주제가 될 수 있는 첫 매치**다. 노트가 명령줄과 파일을 함께
    # 담는 일이 흔해서, 먼저 나온 쪽이 `.venv` 이면 진짜 주제를 놓친다.
    for m in _PATH_TOKEN.finditer(text):
        if not _NOT_A_SUBJECT.search(m.group(0)):
            return m.group(0)
    return ""


def claim_diagnosis(*, owner: str | None = None, cfg=None, store=None,
                    limit: int | None = None) -> dict:
    """R5 — **주장 진단**: 같은 파일을 두고 어긋난 기록 짝, 그리고 코드를 말하면서
    닻이 없는 기록. [[vinc.ops.graph-claim-decay]] §7.3.

    §6.1 이 "조인 키는 만들었는데 조인을 돌리는 것이 없다"고 지목한 그 러너다. R4 가
    `props.subject_path` 를 쓰기 시점에 정규화해 두었으므로 여기서는 문자열 동치로
    묶으면 된다 — 그 정규화가 없으면 이 함수는 **건강해 보이면서 0건**을 낸다.

    반환 {executed, conflicts:[{subject_path, newer:{…}, older:{…}}], conflict_count,
    unanchored:[{id,title,path_hint}], unanchored_count, unanchored_truncated,
    anchored_count, results_excluded, scanned}.

    **어느 쪽이 옳은지 판정하지 않는다.** 두 측정이 다른 날짜에 나왔다는 사실만
    내놓는다. 판정은 경로를 열어 보는 사람의 몫이고, 그 열어 보기가 이 도구가
    대신할 수 없는 잔여다(§7.5).

    짝짓기는 **가장 최근 것 대 나머지**다(n² 가 아니다). 물어야 할 질문이 "이 옛
    주장이 아직 서 있나"이지 "옛 주장 둘이 서로 맞나"가 아니기 때문이다.

    짝과 닻 없는 목록은 **현재 기록**만 본다. `valid_until` 이 오늘이거나 그 전인 기록은
    은퇴했거나 대체된 것이라 다시 잴 일도 닻을 달 일도 없다(`_retired_claim`, `stale` 과
    같은 기준). `anchored_count` 와 `scanned` 는 은퇴한 기록도 센다.

    `props.claim_kind` 가 ``"result"`` 인 기록은 한 시점의 실행 결과다. 같은 파일의 새
    측정과 다투지 않으므로 짝에서 통째로 빠진다(최신 쪽으로도 나머지 쪽으로도). 그렇게
    뺀 현재 기록의 수는 `results_excluded` 로 밝히고, `anchored_count` 와 `scanned` 는
    그대로 센다. 다른 값은 모르는 값으로 보고 보통 주장처럼 다룬다.

    기존 세 kind 가 그래프 **구조**(연결성·군집)를 읽는 데 반해 이것은 props **내용**을
    읽는다. 같은 문이지만 이웃한 코드가 아니라서 `component_diagnosis` 를 넓히지 않고
    따로 둔다."""
    cfg = cfg or load_config()
    with _store_scope(store, cfg, open_store) as store:
        base = {"kind": "claim_diagnosis", "executed": False, "conflicts": [],
                "stale": [], "stale_count": 0,
                "conflict_count": 0, "unanchored": [], "unanchored_count": 0,
                "unanchored_truncated": False, "anchored_count": 0,
                "results_excluded": 0, "scanned": 0}
        if store is None:
            base["note"] = "no reachable store"
            base[fc.KEY] = fc.NO_REACHABLE_STORE
            return base
        where = "WHERE r.owner_id = $owner " if owner else ""
        rows = store.run(
            f"MATCH (r:Record) {where}"
            "RETURN r.record_id AS id, r.title AS title, r.props AS props, "
            "r.valid_until AS valid_until",
            {"owner": owner} if owner else {})
        lim = _clamp_limit(limit, MEMBER_LIMIT_DEFAULT, MEMBER_LIMIT_MAX)
        stale = _stale_claims(store, rows, owner=owner)
        today = _dt.date.today().isoformat()
        by_path: dict[str, list[dict]] = {}
        unanchored: list[dict] = []
        anchored = 0
        results = 0
        for r in rows:
            props = _props_to_obj(r.get("props"))
            rid = pkns.strip(r.get("id") or "")
            title = r.get("title") or rid
            retired = _retired_claim(r, today)
            sp = str(props.get("subject_path") or "").strip()
            if sp:
                anchored += 1
                if retired:
                    continue               # 은퇴한 주장은 짝을 짓지 않는다
                if props.get("claim_kind") == "result":
                    results += 1           # 시점 결과는 새 측정과 짝을 짓지 않는다
                    continue
                by_path.setdefault(sp, []).append({
                    "id": rid, "title": title,
                    "measured_at": str(props.get("measured_at") or ""),
                    "measured_ref": str(props.get("measured_ref") or "")})
                continue
            if retired:
                continue                   # 은퇴한 주장에는 닻을 달 일이 없다
            hint = _path_hint(props, title)
            if hint:
                unanchored.append({"id": rid, "title": title, "path_hint": hint})
        conflicts = []
        for sp, items in by_path.items():
            if len(items) < 2:
                continue
            # 빈 measured_at 은 정렬에서 맨 뒤다 — 시간을 못 대는 주장이 "가장 최근"
            # 자리를 차지하면 짝의 방향이 뒤집힌다.
            items.sort(key=lambda i: (i["measured_at"] or "", i["id"]), reverse=True)
            newest = items[0]
            for other in items[1:]:
                if other["measured_at"] == newest["measured_at"]:
                    continue          # 같은 날의 두 측정은 어긋남이 아니다
                conflicts.append({"subject_path": sp, "newer": newest, "older": other})
        conflicts.sort(key=lambda c: (c["newer"]["measured_at"], c["subject_path"]),
                       reverse=True)
        unanchored.sort(key=lambda u: (u["path_hint"], u["id"]))
        base.update(executed=True,
                    conflicts=conflicts[:lim], conflict_count=len(conflicts),
                    unanchored=unanchored[:lim], unanchored_count=len(unanchored),
                    unanchored_truncated=len(unanchored) > lim,
                    anchored_count=anchored, results_excluded=results,
                    stale=stale[:lim], stale_count=len(stale),
                    stale_truncated=len(stale) > lim,
                    scanned=len(rows))
    return base


_SHA = re.compile(r"[0-9a-f]{7,40}")


def _path_covers(subject: str, changed: str) -> bool:
    """주장의 경로가 바뀐 파일을 덮는가. 같은 파일이거나 그 디렉터리 아래다."""
    return changed == subject or changed.startswith(subject.rstrip("/") + "/")


def _retired_claim(row: dict, today: str) -> bool:
    """유효구간이 오늘이나 그 전에 끝난 기록(은퇴한 주장). 대체된 주장도 대체당한
    기록의 `valid_until` 로 여기 온다. 대체 엣지는 그 값이 없으면 경고하고
    (`fragment._warn_supersede_interval`), 값이 없는 기록은 현재로 남는다.
    주장 진단의 목록들이 이 기준 하나를 쓴다."""
    until = str(row.get("valid_until") or "")
    return bool(until) and until <= today


def _stale_claims(store, record_rows: list[dict], *, owner: str | None) -> list[dict]:
    """측정 뒤에 경로가 바뀐 **현재** 주장. 변경 근거는 `props.files` 를 가진 Episode 다.

    판정하지 않는다. "이 주장을 잰 뒤에 그 파일이 바뀌었다"는 사실만 낸다. 같은 커밋
    (`measured_ref` 와 `head_sha` 가 접두로 겹침)에서 잰 것은 낡지 않았다. 측정과 같은 날의 변경은 `same_day` 로 표시해 낸다. 날짜가 없는 쪽은 비교하지
    않는다(없는 시간을 지어내지 않는다). 주장마다 가장 최근 변경 하나만 붙인다.

    `props.reviewed_at` 은 누군가 이 날짜까지의 변경을 살펴보고 주장이 아직 선다고 확인한
    날이다. 어떤 기록이든 `measured_at` 과 이 날짜 중 늦은 쪽 뒤의 변경만 센다. 확인일 당일의
    변경은 살펴본 것으로 친다. 날짜 검사는 `measured_at`·Episode `date` 와 같은
    `node_schema.normalize_time` 이고, 그 검사를 못 지난 값은 없는 것으로 본다. 확인일이
    `measured_at` 없는 기록을 대상으로 끌어들이지는 않는다.
    근거: [[vinc.plan.temporal-edges-point-in-time-document-validity-2026-09-28]] 를 잇는
    그래프 문서 vinc.plan.spec-drift-graphiti-0-9-8-2026-10-11 §3."""
    where = "WHERE e.owner_id = $owner " if owner else ""
    changes = []
    for e in store.run(f"MATCH (e:Episode) {where}"
                       "RETURN e.episode_id AS id, e.title AS title, e.date AS date, "
                       "e.props AS props", {"owner": owner} if owner else {}):
        props = _props_to_obj(e.get("props"))
        files = props.get("files")
        date = str(e.get("date") or props.get("date") or "")
        if not isinstance(files, list) or not files or not date:
            continue
        changes.append({"id": pkns.strip(e.get("id") or ""), "title": e.get("title") or "",
                        "date": date, "head_sha": str(props.get("head_sha") or ""),
                        "files": [normalize_subject_path(f) for f in files if isinstance(f, str)]})
    if not changes:
        return []
    today = _dt.date.today().isoformat()
    out = []
    for r in record_rows:
        if _retired_claim(r, today):
            continue                       # 은퇴한 주장은 낡을 수 없다
        props = _props_to_obj(r.get("props"))
        sp = str(props.get("subject_path") or "").strip()
        at = str(props.get("measured_at") or "")
        ref = str(props.get("measured_ref") or "")
        if not sp or not at:
            continue
        reviewed = normalize_time(props.get("reviewed_at"))
        # 같은 커밋인지는 접두로 본다 — 측정 기록은 짧은 sha 를, 변경 기록은 긴 sha 를
        # 흔히 싣는다. 날짜가 같으면 하루 안의 순서를 알 수 없으므로 감추지 않고
        # `same_day` 로 표시해 낸다(조용히 빠지면 같은 날 머지가 주장을 영영 못 건드린다).
        def same_commit(sha: str) -> bool:
            # 둘 다 커밋 해시(7~40자 16진)일 때만 접두로 맞춘다. 브랜치 이름 같은 다른
            # 참조는 글자 그대로 같아야 같다 — `main` 이 `main-fix` 의 접두가 되면 안 된다.
            if not (ref and sha):
                return False
            if _SHA.fullmatch(ref) and _SHA.fullmatch(sha):
                return sha.startswith(ref) or ref.startswith(sha)
            return sha == ref
        # 확인일까지의 변경은 이미 살펴본 것이라 세지 않는다(당일 포함).
        hits = [c for c in changes
                if c["date"] >= at and not (reviewed and c["date"] <= reviewed)
                and not same_commit(c["head_sha"])
                and any(_path_covers(sp, f) for f in c["files"])]
        if not hits:
            continue
        latest = max(hits, key=lambda c: (c["date"], c["id"]))
        rid = pkns.strip(r.get("id") or "")
        claim = {"id": rid, "title": r.get("title") or rid, "subject_path": sp,
                 "measured_at": at, "measured_ref": ref}
        if reviewed:
            claim["reviewed_at"] = reviewed
        out.append({"claim": claim,
                    "change": {k: latest[k] for k in ("id", "title", "date", "head_sha")},
                    "changes_since": len(hits),
                    "same_day": latest["date"] == at})
    out.sort(key=lambda x: (x["change"]["date"], x["claim"]["id"]), reverse=True)
    return out


def communities(*, owner: str | None = None, cfg=None, store=None,
                member_limit: int | None = None) -> dict:
    """E-G3 Leiden 커뮤니티 검출 — 개념 군집(도메인 축과 별개의 '실측 군집' 렌즈).
    각 커뮤니티에 지배 도메인·크기·대표(사전순 첫 id)를 붙여 **테마 proposed 제안**의
    근거로 쓴다(재가는 사용자 — T1/T3, 여기서는 쓰기 없음). 조회 전용.

    반환 {executed, communities:[{community, size, dominant_domain, members,
    members_truncated}], community_count}. ``member_limit`` 은 component_diagnosis
    와 같다(기본 50 · 상한 1000 · 자르면 말한다 — F6). 미배정(-1, 고립)은 별도 집계에서 제외. 스토어를 못 읽으면
    executed=False + note."""
    cfg = cfg or load_config()
    with _store_scope(store, cfg, open_store) as store:
        base = {"kind": "communities", "executed": False,
                "communities": [], "community_count": 0}
        if store is None:
            base["note"] = "no reachable store"
            base[fc.KEY] = fc.NO_REACHABLE_STORE
            return base
        # RELATED_TO 단일 투영(개념 관계의 주축: wikilink 추론이 전부 RELATED_TO 로
        # 강등돼 밀도 최고). 엔진 Louvain 이 단일 엣지 표만 받던 시절의 선택인데, 방향
        # 관계를 섞지 않는 '실측 군집'이라는 뜻이 있어 계산을 옮긴 뒤에도 잇는다.
        _g: dict = {}
        rows, note = _run_algo(store, "leiden", rel_tables=("RELATED_TO",), owner=owner,
                               labels=_LOUVAIN_LABELS,
                               graph_out=_g)
        if rows is None:
            base["note"] = note
            return base
        # 커뮤니티마다 품질(연결·밀도·전도율·모듈러리티 몫). 판정은 안 한다 — 숫자만 싣고
        # 어떤 군집을 믿을지는 읽는 쪽이 정한다. 문헌 조사 §9 "Community quality" 경고의 답.
        from . import graph_algo as _ga
        _quality = _ga.community_quality(_g.get("nodes") or [], _g.get("edges") or [],
                                         _g.get("assign") or {})
        scope = _owner_concept_ids(store, owner)
        # 컴포넌트 쪽과 같은 자리다 — 두 스페이스에 사는 슬러그는 커뮤니티 둘에 들어가
        # `size` 를 부풀린다. 고르지 않고 갈라 낸다(`_ambiguous_algo_ids`).
        ambiguous = _ambiguous_algo_ids(rows, scope)
        drop = set(ambiguous)
        # 도메인 부착(지배 도메인 산정) — owner 스코프 개념 도메인 맵.
        dwhere = "WHERE c.owner_id = $owner " if owner else ""
        dparams = {"owner": owner} if owner else None
        drows = store.run("MATCH (c:Concept) " + dwhere +
                          "RETURN c.concept_id AS id, c.domain AS domain", dparams)
        domain_of = {r["id"]: (r.get("domain") or "") for r in drows}
        groups: dict = {}
        for r in rows:
            cid, com = r.get("id"), r.get("value")
            if not cid or com == -1 or cid in drop \
                    or (scope is not None and cid not in scope):
                continue   # -1 = 미배정(고립) — 커뮤니티 아님
            groups.setdefault(com, []).append(cid)
        out = []
        for com, members in groups.items():
            dom_counts: dict = {}
            for cid in members:
                d = domain_of.get(cid, "")
                if d:
                    dom_counts[d] = dom_counts.get(d, 0) + 1
            dominant = max(dom_counts.items(), key=lambda kv: (kv[1], kv[0]))[0] \
                if dom_counts else ""
            _ml = _clamp_limit(member_limit, MEMBER_LIMIT_DEFAULT, MEMBER_LIMIT_MAX)
            out.append({"community": com, "size": len(members),
                        "dominant_domain": dominant,
                        "quality": {k: v for k, v in (_quality.get(com) or {}).items()
                                    if k != "size"},
                        "members": sorted(members)[:_ml],
                        "members_truncated": len(members) > _ml})
        out.sort(key=lambda c: (-c["size"], str(c["community"])))
        base.update(executed=True, communities=out, community_count=len(out),
                    algorithm="leiden", ambiguous=ambiguous, ambiguous_count=len(ambiguous))
        if ambiguous:
            base["ambiguous_note"] = (
                "these ids exist in more than one space, so this diagnosis cannot tell "
                "which node is yours and leaves them out of the communities")
    return base


#: community_context 의 대표(발췌 동봉) 상한 — 커뮤니티가 크면 전원에게 발췌를
#: 붙이는 비용이 MENTIONS 조회 N회로 뛴다. "대표"라는 말 자체가 전량이 아니라는
#: 뜻이라, 작게 못박는다(사용자가 조를 필요는 없다 — no-llm-chat 연구 §4처럼
#: '모델 없이' 답하는 broad 질문의 답은 소수의 근거면 된다).
COMMUNITY_REPRESENTATIVE_CAP = 5


def community_context(*, owner: str | None = None, node: str, cfg=None, store=None,
                      member_limit: int | None = None) -> dict:
    """E-G3 Leiden 커뮤니티 → **하나의 시드가 속한 군집**의 답변 재료(unified-kg-rag
    비교 §4 — "wire Louvain into retrieval", 모델 없는 `global` 전략의 절반. 2026-09-28
    부터 Leiden 이다 — 끊긴 군집이 답변 재료로 나가지 않는다). 조회
    전용, 문장을 생성하지 않는다.

    `communities()`(전체 커뮤니티 목록, id만)와 달리 **시드 하나가 속한 커뮤니티 하나**
    를 골라 그 구성원에 title·one_liner·발췌를 붙인다 — "이 영역엔 뭐가 있나" 같은
    넓은 질문에 node_context 가 못 주는 답을 준다. 대표(``representatives``, 최대
    :data:`COMMUNITY_REPRESENTATIVE_CAP`)만 발췌를 동봉한다 — MENTIONS 언급 수가
    가장 많은 순(문서화가 가장 잘 된 노드가 그 영역을 대표한다는 결정론적 기준).
    나머지 구성원은 id·title만 ``members``에 실린다(``member_limit`` — component_
    diagnosis/communities 와 같은 규약, 잘리면 ``members_truncated``).

    반환 {executed, node, community, size, quality, dominant_domain,
    representatives:[{id, title, one_liner, excerpt}],
    members:[{id, title}], members_truncated}. 시드가 없거나 고립(-1)이면
    executed=False + note. 스토어를 못 읽을 때도 마찬가지."""
    cfg = cfg or load_config()
    with _store_scope(store, cfg, open_store) as store:
        base = {"kind": "community-context", "executed": False, "node": node,
                "community": None, "size": 0, "dominant_domain": "",
                "representatives": [], "members": [], "members_truncated": False}
        if store is None:
            base["note"] = "no reachable store"
            base[fc.KEY] = fc.NO_REACHABLE_STORE
            return base
        _g: dict = {}
        rows, note = _run_algo(store, "leiden", rel_tables=("RELATED_TO",), owner=owner,
                               labels=_LOUVAIN_LABELS,
                               graph_out=_g)
        if rows is None:
            base["note"] = note
            return base
        scope = _owner_concept_ids(store, owner)
        # 🔴 **여기가 셋 중 가장 조용한 자리였다.** `assign` 은 논리 id 로 키를 잡는
        # dict 라, 같은 슬러그가 두 스페이스에 있으면 **마지막 행이 이긴다** — 씨앗의
        # 커뮤니티로 남의 스페이스 값을 집어 그 위에서 대표와 발췌를 만들고, 아무 표시도
        # 없다. 컴포넌트 쪽은 중복이 눈에 보이기라도 했다.
        # 씨앗이 모호하면 **답하지 않는다**: 고르는 것은 추측이다.
        ambiguous = set(_ambiguous_algo_ids(rows, scope))
        if node in ambiguous:
            base["note"] = (f"{node} exists in more than one space, so this cannot tell "
                            "which node is yours")
            base["ambiguous"] = True
            return base
        assign = {r["id"]: r["value"] for r in rows
                  if r.get("id") and r["id"] not in ambiguous}
        com = assign.get(node)
        if com is None or com == -1 or (scope is not None and node not in scope):
            base["note"] = f"unknown or isolated node: {node}"
            return base
        members = [cid for cid, c in assign.items()
                  if c == com and (scope is None or cid in scope)]

        dwhere = "WHERE c.owner_id = $owner " if owner else ""
        dparams = {"owner": owner} if owner else None
        drows = store.run("MATCH (c:Concept) " + dwhere +
                          "RETURN c.concept_id AS id, c.title AS title, "
                          "c.domain AS domain, c.owner_id AS owner_id", dparams) or []
        title_of = {r["id"]: (r.get("title") or r["id"]) for r in drows}
        domain_of = {r["id"]: (r.get("domain") or "") for r in drows}
        # node_context 는 owner=None(비스코프)을 안 받는다 — 시드별 실제 owner_id 로
        # 부른다(unscoped 로컬 모드에서도 발췌가 나오게. 통상 단일 소유자 스토어라
        # 전원이 같은 값이지만, 명시 owner 가 있으면 그 값이 항상 이긴다).
        owner_of = {r["id"]: (r.get("owner_id") or "") for r in drows}
        dom_counts: dict = {}
        for cid in members:
            d = domain_of.get(cid, "")
            if d:
                dom_counts[d] = dom_counts.get(d, 0) + 1
        dominant = max(dom_counts.items(), key=lambda kv: (kv[1], kv[0]))[0] \
            if dom_counts else ""

        mrows = store.run(
            "MATCH (ch:Chunk)-[:MENTIONS]->(c:Concept) WHERE c.concept_id IN $ids "
            # 무스코프 로컬에서는 구성원마다 owner 가 다를 수 있어 **각자의 owner** 로
            # 합성한다(바로 위 node_context 호출이 쓰는 그 규칙과 같다).
            "RETURN c.concept_id AS id, count(ch) AS n",
            {"ids": [pkns.key(owner or owner_of.get(cid, ""), cid)
                     for cid in members]}) or []
        mentions = {r["id"]: int(r.get("n") or 0) for r in mrows}
        ranked = sorted(members, key=lambda cid: (-mentions.get(cid, 0), cid))

        reps = []
        for cid in ranked[:COMMUNITY_REPRESENTATIVE_CAP]:
            ctx = node_context(owner=owner or owner_of.get(cid, ""), node=cid,
                               cfg=cfg, store=store)
            excerpt = (ctx.get("excerpts") or [{}])[0].get("excerpt", "")
            reps.append({"id": cid, "title": title_of.get(cid, cid),
                        "one_liner": ctx.get("one_liner") or "", "excerpt": excerpt})

        mlim = _clamp_limit(member_limit, MEMBER_LIMIT_DEFAULT, MEMBER_LIMIT_MAX)
        rest = ranked[COMMUNITY_REPRESENTATIVE_CAP:]
        member_list = [{"id": cid, "title": title_of.get(cid, cid)}
                       for cid in sorted(rest)[:max(0, mlim - len(reps))]]
        # 답변 재료로 나가는 군집이라 품질을 함께 싣는다 — `communities()` 와 같은 숫자
        from . import graph_algo as _ga
        _quality = _ga.community_quality(_g.get("nodes") or [], _g.get("edges") or [],
                                         _g.get("assign") or {}).get(com) or {}
        base.update(executed=True, community=com, size=len(members), algorithm="leiden",
                    quality={k: v for k, v in _quality.items() if k != "size"},
                    dominant_domain=dominant, representatives=reps,
                    members=member_list,
                    members_truncated=len(members) > len(reps) + len(member_list))
    return base
