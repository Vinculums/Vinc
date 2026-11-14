# -*- coding: utf-8 -*-
"""결정 읽기: 경로를 덮는 결정(`decide_paths`)과 주제 둘레의 결정 지원(`decide`), 그 상충 검사.

`decide_paths`(`vinc_decide_paths`)는 바뀐 파일 경로마다 그 경로를 주장한 기록과 결정을 찾는다. 주장의 닻이 경로를
덮는지(`_path_covers`)와 물러난 주장인지(`_retired_claim`)는 `diagnose` 의 주장 진단과 같은 함수로 본다. 닻에서 한 홉
닿은 결정은 `_gate_related_decisions` 가, 항목 순서는 `_GATE_MATCH_ORDER` 가 정한다. `decide`(`vinc_decide`)는
주제(`decide_topic_resolve_plan`)를 시드로 `graph_read` 의 계획과 셰이핑으로 둘레 그래프를 읽고, 지금 선 결정
(`_current_decisions`)과 상충(`_decide_conflicts`·`_flow_conflicts`, 순환은 `_strongly_connected`·`_shortest_cycle`)을 붙인다.

`api/query.py` 가 여기 이름을 모두 다시 내보내므로 `q.decide`·`q.X` 와 `from api.query import X` 는 그대로다. 두 이름은
같은 객체다. 여기 함수가 부르는 이름(`execute`·`_order_graph_result`·`shape_graph`·`_retired_claim` 등)은 이 모듈에서
찾으므로, 그 호출을 바꾸려면 `api.query` 가 아니라 이 모듈의 이름을 패치한다.

스토어를 안 받으면 `_store_scope`(`api/query_base.py`)가 열고 닫는다. 열 수 없을 때의 규칙: 읽기는 `executed:False`
에 `note` 만 싣는다.

이 모듈은 `api.query` 를 import 하지 않는다(`tests/core/test_decide_read_module.py` 가 본다).
"""
from __future__ import annotations

import datetime as _dt
import itertools

from store import pkns
from store.backend import open_store
from store.config import load_config
from store.querybase import QueryPlan, _props_to_obj, execute

from .diagnose import MEMBER_LIMIT_DEFAULT, MEMBER_LIMIT_MAX, _clamp_limit, _path_covers, _retired_claim
from .graph_rank import attach_ppr
from .graph_read import (
    _GRAPH_READ_PLANS,
    _order_graph_result,
    decision_seed_rows_plan,
    graph_rows_for_ids_plan,
    seed_lookup_plan,
    shape_graph,
    subgraph_ids_plan,
)
from .node_schema import normalize_subject_path, split_subject
from .query_base import (
    _DIRECTED_RELS,
    _GRAPH_READ_PAIRS,
    _REVERSED_RELS,
    _SEMANTIC_PK,
    _store_scope,
    near_nodes,
)


#: 결정 문이 한 번에 받는 경로 수. 경로 목록은 사이드카 GET 질의 문자열에 JSON 으로
#: 실리므로 요청 줄 상한(64KB) 안에 머물 만큼만 받는다. 더 큰 diff 는 부르는 쪽이 나눠 보낸다
#: (`vinc gate check` 가 그렇게 한다).
DECIDE_PATHS_MAX = 200
#: 같은 경로를 덮는 항목의 순서. 직접 닻이 먼저, 그 닻에서 한 홉 닿은 결정이 나중이다.
_GATE_MATCH_ORDER = {"exact": 0, "directory": 1, "related": 2}


def _gate_subjects(props: dict) -> list[str]:
    """노드가 가리키는 경로들(정규화). Record 는 쓰기 시점에 이미 접혀 있지만, Decision 은
    쓰기 경로가 접지 않으므로(`fragment._claim_props`) 여기서 같은 함수로 접는다. 결정은
    여러 경로를 다스릴 수 있어 목록도 받는다."""
    raw = props.get("subject_path")
    values = raw if isinstance(raw, list) else [raw]
    return [s for s in dict.fromkeys(normalize_subject_path(v) for v in values
                                     if isinstance(v, str)) if s]


def _gate_validity(valid_from: str, today: str) -> str:
    return "not_yet" if valid_from and valid_from[:10] > today else "current"


def decide_paths(paths, *, owner: str | None = None, cfg=None, store=None,
                 limit: int | None = None) -> dict:
    """결정 문(decision gate): 바뀐 경로마다 그 경로를 다스리는 **현재** 기록과 결정.

    모델 없이 결정론으로 돈다. 가부를 가리지 않는다: "이 경로에는 이 주장이 걸려 있다"만 내고,
    diff 가 그것을 어기는지는 읽는 사람이 연다(`claim_diagnosis` 와 같은 태도).
    근거: [[vinc.plan.agent-harness-work-order-2026-10-25 §2]] · [[vinc.ops.graph-claim-decay]] §7.3.

    맞춤은 `claim_diagnosis`·`_stale_claims` 와 같은 부품을 쓴다. 두 번째 맞춤기를 두지 않는다:
    - 경로는 `node_schema.normalize_subject_path` 로 접고(`repo:path`), 덮음은 `_path_covers` 다.
      같은 경로면 ``exact``, 노드의 경로가 바뀐 경로의 상위 디렉터리면 ``directory``.
    - 은퇴는 `_retired_claim` 이다. Record 는 열, Decision 은 `props.valid_until` 로 본다.
    - `props.claim_kind == "result"` 인 기록은 한 시점의 실행 결과라 다스리지 않는다(센다).

    직접 닿은 기록·결정에서 **한 홉** 닿은 현재 결정도 싣는다(``related``, `via` 는 그 닻).
    길은 Record→Decision `ATTACHES_TO` 와 결정↔결정 관계(`_GRAPH_READ_PAIRS`)다. 개념을
    건너는 두 홉은 따르지 않는다: 한 개념에 걸린 결정이 모두 딸려 와 목록이 소음이 된다.

    리포가 없는 경로는 리포가 있는 주장과 맞지 않는다(동명 파일 충돌을 막는 R4 의 이유 그대로).
    그런 입력은 `unqualified_paths` 로 밝힌다.

    반환 {executed, verdict: flag|pass, paths:[{path, governed_by:[...], count, truncated}],
    path_count, flagged_paths, governing_count, retired_excluded, results_excluded,
    unqualified_paths, scanned:{records, decisions}}."""
    base = {"kind": "decide_paths", "executed": False, "verdict": "pass", "paths": [],
            "path_count": 0, "flagged_paths": 0, "governing_count": 0,
            "retired_excluded": 0, "results_excluded": 0, "unqualified_paths": []}
    if (not isinstance(paths, list) or not paths
            or any(not isinstance(p, str) or not p.strip() for p in paths)):
        base["error"] = "invalid paths: send a nonempty list of repo:path strings"
        return base
    touched = list(dict.fromkeys(normalize_subject_path(p) for p in paths))
    if len(touched) > DECIDE_PATHS_MAX:
        base["error"] = (f"invalid paths: at most {DECIDE_PATHS_MAX} paths per call; "
                         "split the list")
        return base
    cfg = cfg or load_config()
    with _store_scope(store, cfg, open_store) as store:
        if store is None:
            base["note"] = "no reachable store"
            return base
        today = _dt.date.today().isoformat()
        params = {"owner": owner} if owner else {}
        anchors: list[dict] = []
        retired = results = 0
        record_rows = store.run(
            "MATCH (r:Record) " + ("WHERE r.owner_id = $owner " if owner else "") +
            "RETURN r.record_id AS id, r.title AS title, r.props AS props, "
            "r.valid_from AS valid_from, r.valid_until AS valid_until", params) or []
        for r in record_rows:
            props = _props_to_obj(r.get("props"))
            subjects = _gate_subjects(props)
            if not subjects:
                continue
            if _retired_claim(r, today):
                retired += 1
                continue
            if props.get("claim_kind") == "result":
                results += 1
                continue
            rid = pkns.strip(r.get("id") or "")
            anchors.append({"id": rid, "kind": "record", "title": r.get("title") or rid,
                            "subjects": subjects,
                            "valid_from": str(r.get("valid_from") or ""),
                            "valid_until": str(r.get("valid_until") or ""),
                            "measured_at": str(props.get("measured_at") or ""),
                            "measured_ref": str(props.get("measured_ref") or "")})
        decision_rows = store.run(
            "MATCH (d:Decision) " + ("WHERE d.owner_id = $owner " if owner else "") +
            "RETURN d.decision_id AS id, d.title AS title, d.props AS props", params) or []
        for d in decision_rows:
            props = _props_to_obj(d.get("props"))
            subjects = _gate_subjects(props)
            if not subjects:
                continue
            if _retired_claim({"valid_until": props.get("valid_until")}, today):
                retired += 1
                continue
            did = pkns.strip(d.get("id") or "")
            anchors.append({"id": did, "kind": "decision", "title": d.get("title") or did,
                            "subjects": subjects,
                            "valid_from": str(props.get("valid_from") or ""),
                            "valid_until": str(props.get("valid_until") or "")})

        def item(node: dict, match: str, **extra) -> dict:
            out = {"id": node["id"], "kind": node["kind"], "title": node["title"],
                   "match": match, **extra,
                   "valid_from": node["valid_from"], "valid_until": node["valid_until"],
                   "validity": _gate_validity(node["valid_from"], today)}
            for k in ("measured_at", "measured_ref"):
                if node.get(k):
                    out[k] = node[k]
            return out

        hits: dict[str, dict[str, dict]] = {p: {} for p in touched}
        for p in touched:
            for a in anchors:
                subject = next((s for s in a["subjects"] if _path_covers(s, p)), None)
                if subject is not None:
                    hits[p][a["id"]] = item(a, "exact" if subject == p else "directory",
                                            subject_path=subject)
        related, related_note = _gate_related_decisions(
            store, sorted({i for h in hits.values() for i in h}), owner=owner, today=today)
        for p in touched:
            # 직접 닻을 가까운 순(exact → directory, 같으면 id)으로 훑어 via 를 정한다.
            # 목록을 먼저 굳히므로 여기서 더한 related 는 다시 닻이 되지 않는다(한 홉).
            for via in sorted(hits[p], key=lambda i: (_GATE_MATCH_ORDER[hits[p][i]["match"]], i)):
                for dec in related.get(via, []):
                    if dec["id"] not in hits[p]:
                        hits[p][dec["id"]] = item(dec, "related", via=via,
                                                  relation=dec["relation"])
        lim = _clamp_limit(limit, MEMBER_LIMIT_DEFAULT, MEMBER_LIMIT_MAX)
        out_paths, governing = [], set()
        for p in touched:
            # 순서: 맞춤의 가까움 → 결정 먼저 → 최근 측정 먼저 → id. 정렬 세 번(안정 정렬).
            rows = sorted(hits[p].values(), key=lambda x: x["id"])
            rows.sort(key=lambda x: x.get("measured_at", ""), reverse=True)
            rows.sort(key=lambda x: (_GATE_MATCH_ORDER[x["match"]], x["kind"] != "decision"))
            governing.update(x["id"] for x in rows)
            out_paths.append({"path": p, "governed_by": rows[:lim], "count": len(rows),
                              "truncated": len(rows) > lim})
        flagged = sum(1 for x in out_paths if x["count"])
        base.update(executed=True, verdict="flag" if flagged else "pass", paths=out_paths,
                    path_count=len(touched), flagged_paths=flagged,
                    governing_count=len(governing), retired_excluded=retired,
                    results_excluded=results,
                    unqualified_paths=[p for p in touched if not split_subject(p)[0]],
                    scanned={"records": len(record_rows), "decisions": len(decision_rows)})
        if related_note:
            base["related_note"] = related_note
        if base["unqualified_paths"]:
            base["unqualified_note"] = ("these paths name no repository, so they only match "
                                        "claims that name none either; send repo:path")
    return base


def _gate_related_decisions(store, ids: list[str], *, owner: str | None,
                            today: str) -> tuple[dict[str, list[dict]], str]:
    """닻 id → 한 홉 닿은 현재 결정들. 결과는 (표, 실패 문장). 한 팔이 실패해도 직접 닻은
    그대로 낸다: 이웃 결정은 증강이고, 잃은 것은 문장으로 말한다."""
    out: dict[str, list[dict]] = {}
    if not ids:
        return out, ""
    rels = "|".join(_GRAPH_READ_PAIRS.get(
        ("Decision", "decision_id", "Decision", "decision_id"), ()))
    own = " AND s.owner_id = $owner AND d.owner_id = $owner" if owner else ""
    ret = ("RETURN s.{spk} AS via, d.decision_id AS id, d.title AS title, d.props AS props, "
           "{rel} AS relation")
    arms = [("MATCH (s:Record)-[e:ATTACHES_TO]->(d:Decision) "
             f"WHERE s.record_id IN $ids{own} "
             + ret.format(spk="record_id", rel="e.type"))]
    if rels:
        arms.append(f"MATCH (s:Decision)-[e:{rels}]->(d:Decision) "
                    f"WHERE s.decision_id IN $ids{own} "
                    + ret.format(spk="decision_id", rel="type(e)"))
        arms.append(f"MATCH (d:Decision)-[e:{rels}]->(s:Decision) "
                    f"WHERE s.decision_id IN $ids{own} "
                    + ret.format(spk="decision_id", rel="type(e)"))
    params: dict = {"ids": pkns.keys(owner, ids)}
    if owner:
        params["owner"] = owner
    failed = 0
    for cypher in arms:
        try:
            rows = store.run(cypher, params) or []
        except Exception:      # noqa: BLE001 — 증강 팔 하나의 실패가 문 전체를 죽이지 않는다
            failed += 1
            continue
        for row in rows:
            props = _props_to_obj(row.get("props"))
            if _retired_claim({"valid_until": props.get("valid_until")}, today):
                continue
            did = pkns.strip(row.get("id") or "")
            via = pkns.strip(row.get("via") or "")
            if not did or did == via:
                continue
            out.setdefault(via, []).append({
                "id": did, "kind": "decision", "title": row.get("title") or did,
                "relation": str(row.get("relation") or "").lower(),
                "valid_from": str(props.get("valid_from") or ""),
                "valid_until": str(props.get("valid_until") or "")})
    for via, decs in out.items():
        seen: dict[str, dict] = {}
        for dec in sorted(decs, key=lambda x: (x["id"], x["relation"])):
            seen.setdefault(dec["id"], dec)
        out[via] = list(seen.values())
    note = (f"{failed} of {len(arms)} relation lookups failed; decisions one hop from the "
            "matched claims may be missing" if failed else "")
    return out, note


def decide_topic_resolve_plan(topic: str, *, owner: str | None = None,
                              label: str = "Concept") -> QueryPlan:
    """decide 토픽 → seed id 해석(A1.9 A5). id 정확일치 또는 title 대소문자 무시
    일치. decide_plan 의 `title=$topic`(완전일치) 대비 title 은 관대.

    ``label`` 은 의미층 라벨(:data:`_SEMANTIC_PK`)이다. 기본 Concept 일 때 플랜
    문자열은 종전과 같다. :func:`decide` 가 Concept 다음에 Decision 으로 다시 묻는다.

    ⚠ owner(SEC-1): 소유자를 주면 그 스코프 안에서만 해석한다. 없으면 남의 개념을
    시드로 잡아 서브그래프 전체를 되돌려줬다. OR 조건은 반드시 괄호로 묶는다 —
    안 그러면 owner AND 가 뒤 절에만 붙어 필터가 무력화된다."""
    # `$topic` 은 title 비교에 그대로 쓰이므로 논리로 남기고, id 열에는 물리 키를
    # 따로 바인딩한다(decide_plan 과 같은 이유).
    params: dict = {"topic": topic, "tkey": pkns.key(owner, topic)}
    pk = _SEMANTIC_PK[label]
    where = (f"(c.{pk} = $tkey "
             "OR lower(coalesce(c.title,'')) = lower($topic))")
    if owner:
        where += " AND c.owner_id = $owner"
        params["owner"] = owner
    cypher = f"MATCH (c:{label}) WHERE {where} RETURN c.{pk} AS id LIMIT 1"
    return QueryPlan("decide_resolve", cypher, params,
                     note=f"resolve topic → {label.lower()} seed")


def decide(topic: str, *, depth: int = 2, owner: str | None = None,
           cfg=None, store=None) -> dict:
    """결정 지원(A1.9 A5 정규화) — MCP-INTERFACE §4 계약 `{nodes, edges, conflicts}`.

    **왜 decide_plan 을 직접 실행하지 않나**: decide_plan 의 경로 순회
    `(n)-[* TRAIL]-(m)` 는 각 행이 시드 n 전체를 재직렬화(49컬럼 합집합, 대부분 null →
    토큰 폭발, 노션 §3.7)하고 중간 노드의 문자열 id 를 돌려주지 않아 깨끗한 그래프
    재구성이 불가하다. 그래서 **검증된 하위 planner 를 조합**한다: 토픽→seed 해석 →
    `subgraph_ids_plan`(TRAIL 순회) → `graph_rows_for_ids_plan` → `shape_graph`
    (dedup·props/tags 포함). blowup 을 없애고 label=title·props 를 얻는다.
    (decide_plan 은 `/plan` 표시·테스트용으로 유지 — 순회 의도의 표현.)

    ``conflicts`` 는 :func:`_decide_conflicts` 가 구조만으로 판별한다. 크로스도메인은
    **별도 필드**(`cross_domain`)로 나갔다 — 여태 그것이 conflicts 자리를 독점해서,
    이 도구가 "상충"이라고 부르는 것은 언제나 도메인이 다른 연결뿐이었다."""
    base = {"kind": "decide", "executed": False, "topic": topic, "depth": depth,
            "found": False, "nodes": [], "edges": [], "conflicts": [],
            "cross_domain": []}
    # SEC-1: 토픽 해석·순회·행조회 전부 owner 스코프. 없으면 남의 개념을 시드로
    # 잡아 서브그래프가 통째로 노출됐다(vinc_decide 는 owner 를 보내지도 않았다).
    res = execute(decide_topic_resolve_plan(topic, owner=owner), cfg, store=store)
    if not res.get("executed"):
        base["note"] = res.get("note", "")
        return base
    base["executed"] = True
    seed_label = "Concept"
    seed = next((r["id"] for r in res.get("rows", []) if r.get("id")), None)
    if not seed:
        # 결정도 토픽이다(v12 부터 제 테이블). Concept 만 물어서 결정 id 가 자기 자신을
        # 첫 후보로 내밀며 "없다"고 답했다(2026-09-29 실측). 개념이 먼저다 — 제목이
        # 겹치면 종전대로 개념이 시드다.
        dres = execute(decide_topic_resolve_plan(topic, owner=owner, label="Decision"),
                       cfg, store=store)
        seed = next((r["id"] for r in dres.get("rows", []) if r.get("id")), None)
        seed_label = "Decision"
    if not seed:
        # F7 — `topic` 은 **정확 일치**다(concept_id · decision_id · title). 부분 일치가
        # 아니라서 `view` 는 안 맞고 `concept:view` 나 정확한 제목이라야 맞는다.
        # 그 계약이 어디에도 안 적혀 있었고, 실패 응답도 그것을 말하지 않았다.
        cands = near_nodes(store, topic, owner=owner)
        base["note"] = f"topic not found: {topic}"
        base["error"] = f"topic not found: {topic}"
        base["hint"] = ("`topic` matches exactly on a concept or decision id "
                        "(e.g. `concept:view`), or a full concept or decision title "
                        "— not a substring. "
                        + ("Did you mean: " + ", ".join(c["id"] for c in cands) + "? "
                           if cands else "")
                        + "Use vinc_search to find the id first.")
        if cands:
            base["candidates"] = cands
        return base
    base["found"] = True
    ids_res = execute(subgraph_ids_plan(seed, depth=depth, owner=owner,
                                        seed_label=seed_label), cfg, store=store)
    ids = sorted({seed, *(r["id"] for r in ids_res.get("rows", []) if r.get("id"))})
    rows_res = execute(graph_rows_for_ids_plan(ids, owner=owner), cfg, store=store)
    rows = list(rows_res.get("rows", []))
    keep = set(ids)   # ego 경계(depth+1 유출 제거) — graph() 와 동일 정책
    if seed_label == "Decision":
        # graph_rows 는 Concept 전용이라 결정 시드는 제 행이 없다. 존재 조회로 노드를
        # 먼저 세우고(엣지 없는 결정도 nodes 에 선다) 시드의 ABOUT·의미층 엣지를 싣는다.
        # 다른 결정은 keep 밖이라 아래 ego 경계에서 걸러진다(nodes 는 개념 + 토픽).
        sr = execute(seed_lookup_plan(seed, owner=owner), cfg, store=store)
        rows += [{**r, "label": r.get("title"), "kind": "decision"}
                 for r in sr.get("rows", [])]
        for plan in (decision_seed_rows_plan(
                         seed, owner=owner,
                         rels="ABOUT|PREREQUISITE_OF|DERIVES_FROM|INHERITS_FROM"),
                     decision_seed_rows_plan(seed, owner=owner, outgoing=False)):
            rows += execute(plan, cfg, store=store).get("rows", [])
    elif owner:
        # 개념 시드도 부착 축을 합류시킨다. graph() 의 접착제 후조회와 같은 플랜·같은
        # ids 모드다. 없으면 graph_rows 의 개념↔개념 엣지만 나와, 개념에 ATTACHES_TO 로
        # 붙은 레코드와 ABOUT 으로 걸린 결정이 nodes·edges 에서 통째로 빠진다(결정은
        # conflicts 로만 비치고, decide 의 "무엇이 걸려 있나"가 빈 답이 된다). 축 플랜은
        # owner 필수라(소유자 격리) owner 가 있을 때만 돈다 — 무소유 로컬 모드는 개념
        # 축만 싣는다. 같은 물리 엣지는 한 플랜에서만 오므로(graph_rows=개념↔개념, 축
        # 플랜=ABOUT·ATTACHES_TO·CONNECTS·나머지) 이중 계상이 없다.
        # 근거: [[vinc.manual.core]]
        for plan_fn in _GRAPH_READ_PLANS:
            ax = execute(plan_fn(owner=owner, ids=ids), cfg, store=store)
            if not ax.get("executed"):
                continue
            for r in ax.get("rows", []):
                rows.append(r)
                if r.get("id"):
                    keep.add(r["id"])
                if r.get("target"):
                    keep.add(r["target"])
    shaped = shape_graph(rows)
    shaped["edges"] = [e for e in shaped["edges"] if e["source"] in keep and e["target"] in keep]
    shaped["nodes"] = [n for n in shaped["nodes"] if n["id"] in keep]
    attach_ppr(shaped, [seed])   # E-G2 — 토픽(시드) 관점 개인화 랭킹
    _order_graph_result(shaped)
    dec_res = execute(decisions_about_plan(ids, owner=owner), cfg, store=store)
    decisions = list(dec_res.get("rows", [])) if dec_res.get("executed") else []
    decisions, retired = _current_decisions(decisions, _dt.date.today().isoformat())
    conflicts, cut = _decide_conflicts(shaped["edges"], decisions)
    out = {**base, "nodes": shaped["nodes"], "edges": shaped["edges"],
           "conflicts": conflicts,
           # 크로스도메인은 상충이 아니라 **연결의 성질**이다. 자기 필드로 나간다.
           "cross_domain": [{"source": e["source"], "target": e["target"],
                             "type": e["type"]}
                            for e in shaped["edges"] if e.get("cross_domain")]}
    if cut:
        out["conflicts_truncated"] = True
    if retired:
        # 뺀 것을 말한다(F6). 받는 쪽이 "결정이 하나뿐"으로 읽지 않게.
        out["retired_decisions_excluded"] = retired
    return out


def decisions_about_plan(ids: list[str], *, owner: str | None = None) -> QueryPlan:
    """id 집합의 개념에 ABOUT 으로 걸린 결정 — decide 의 상충 판정이 쓰는 추가 조회.

    ⚠ :func:`graph_rows_for_ids_plan` 은 Concept 전용이라 **결정은 decide 의
    서브그래프에 한 번도 실린 적이 없다.** 그래서 "이미 내려진 결정과 부딪히는가"를
    도구 설명이 약속하고도 구조적으로 답할 수 없었다. 결정을 nodes 에 합류시키지
    않고 여기서 따로 읽는 이유는, decide 의 nodes/edges 계약(Concept 서브그래프)을
    바꾸지 않고 상충만 채우기 위해서다."""
    params: dict = {"ids": pkns.keys(owner, ids)}
    where = "c.concept_id IN $ids"
    if owner:
        where += " AND c.owner_id = $owner AND d.owner_id = $owner"
        params["owner"] = owner
    # props 는 은퇴 여부를 보려고 싣는다. Decision 은 유효구간 열이 없고 `props.valid_until` 에 둔다.
    cypher = (f"MATCH (d:Decision)-[:ABOUT]->(c:Concept) WHERE {where} "
              "RETURN d.decision_id AS id, d.title AS title, c.concept_id AS subject, "
              "d.props AS props")
    return QueryPlan("decisions_about", cypher, params,
                     note="decisions attached to the id set")


def _current_decisions(rows: list[dict], today: str) -> tuple[list[dict], int]:
    """은퇴한 결정(`props.valid_until` 이 오늘이나 그 전)을 상충 검사 입력에서 뺀다.

    시간 정책은 대체된 결정을 지우지 말고 남기라고 한다. 그런데 남긴 결정이 검사에
    그대로 들어가면 대체할 때마다 대체된 결정과 새 결정이 `decisions_on_same_subject` 로
    영원히 보고된다 — 정책을 지킬수록 신호가 소음이 된다. 기준은 `decide_paths` 와 같은
    :func:`_retired_claim` 하나다. 반환 ``(현재 결정, 뺀 수)``."""
    keep: list[dict] = []
    retired = 0
    for r in rows:
        props = _props_to_obj(r.get("props")) or {}
        until = props.get("valid_until") if isinstance(props, dict) else None
        if _retired_claim({"valid_until": until}, today):
            retired += 1
            continue
        keep.append(r)
    return keep, retired


#: 한 응답이 싣는 상충 상한. 넘치면 `conflicts_truncated` 로 말한다(F6 — 잘린 것을
#: 말하지 않으면 받는 쪽은 "이게 전부"로 읽는다).
_CONFLICT_CAP = 50


def _decide_conflicts(edges: list[dict],
                      decisions: list[dict]) -> tuple[list[dict], bool]:
    """구조만으로 판별되는 상충(순수 함수 — 모델 판단 없음, 편들지 않는다).

      ① ``decisions_on_same_subject`` — 같은 개념을 두고 내려진 결정이 둘 이상.
         결정을 하나 더 쓰기 전에 읽어야 하는 바로 그것이다.
      ② ``opposite_direction`` — 한 쌍 사이에 **흐름**이 양쪽으로 다 있다.
         방향이 의미인 관계에서 이건 둘 중 하나가 틀렸다는 뜻이다.
      ④ ``cycle`` — 셋 이상의 개념이 흐름으로 고리를 이룬다(강연결요소).
      ③ ``duplicate_decision_title`` — 제목이 같은 결정 둘. 같은 것을 두 번
         결정했거나, 하나가 다른 하나를 대체하는데 그렇게 적히지 않았다.

    **흐름**은 저장 방향이 아니다. `DERIVES_FROM`·`INHERITS_FROM` 은 흐름과 반대로
    저장되므로(:data:`_REVERSED_RELS`, 레이아웃·좌측 트리와 같은 규약) 뒤집어 읽는다.
    저장 방향 그대로 비교하면 "A 가 B 의 선행, B 는 A 에서 파생"(일치)을 상충으로
    내고 "A 가 B 의 선행, A 는 B 에서 파생"(모순)은 놓친다. ②의 ``type`` 은 흐름
    source→target 을 실은 관계, ``reverse_type`` 은 그 반대 흐름을 실은 관계다.
    자기 고리(A→A)는 내지 않는다.

    싣는 순서는 구조 상충(②·④) → ③ → ① 이다. ①은 결정 수의 제곱으로 늘어
    상한(:data:`_CONFLICT_CAP`)을 혼자 채우므로, 드물고 확실한 상충이 그 뒤에서
    잘리지 않게 맨 뒤에 둔다.

    반환 ``(conflicts, truncated)``. 순서는 결정론이다 — 같은 그래프를 두 번 물으면
    같은 순서로 답해야 한다."""
    structural = _flow_conflicts(edges)
    by_title_out: list[dict] = []
    same_subject: list[dict] = []

    # ① 같은 주제에 걸린 결정들.
    by_subject: dict[str, set[str]] = {}
    titles: dict[str, str] = {}
    for r in decisions:
        did, subj = str(r.get("id") or ""), str(r.get("subject") or "")
        if not did or not subj:
            continue
        by_subject.setdefault(subj, set()).add(did)
        titles[did] = str(r.get("title") or "")
    for subj, dec_ids in sorted(by_subject.items()):
        for a, b in itertools.combinations(sorted(dec_ids), 2):
            same_subject.append({"source": a, "target": b, "type": "ABOUT",
                                 "reason": "decisions_on_same_subject", "subject": subj})

    # ③ 제목이 같은 결정. 공백·대소문자만 정규화한다(그 이상은 판단이다).
    by_title: dict[str, set[str]] = {}
    for did, title in titles.items():
        key = " ".join(title.split()).casefold()
        if key:
            by_title.setdefault(key, set()).add(did)
    for key, dec_ids in sorted(by_title.items()):
        if len(dec_ids) < 2:
            continue
        for a, b in itertools.combinations(sorted(dec_ids), 2):
            by_title_out.append({"source": a, "target": b, "type": "",
                                 "reason": "duplicate_decision_title", "title": titles[a]})

    out = structural + by_title_out + same_subject
    return out[:_CONFLICT_CAP], len(out) > _CONFLICT_CAP


def _flow_conflicts(edges: list[dict]) -> list[dict]:
    """유향 관계를 흐름 방향으로 맞춘 뒤 ②(양방향 쌍)와 ④(셋 이상의 고리)를 낸다.

    순수 함수다. 순서는 정렬로 고정한다: ②는 (source, target) 순, ④는 고리 구성원의
    최소 id 순."""
    # 흐름 (u→v) 마다 그 흐름을 실은 저장 관계 이름들.
    flow: dict[tuple[str, str], set[str]] = {}
    for e in edges:
        rel = str(e.get("type") or "")
        s, t = str(e.get("source") or ""), str(e.get("target") or "")
        if rel not in _DIRECTED_RELS or not s or not t or s == t:
            continue
        u, v = (t, s) if rel in _REVERSED_RELS else (s, t)
        flow.setdefault((u, v), set()).add(rel)

    out: list[dict] = []
    # ② 쌍당 한 번(s < t).
    for (s, t), fwd in sorted(flow.items()):
        back = flow.get((t, s))
        if back and s < t:
            out.append({"source": s, "target": t, "type": sorted(fwd)[0],
                        "reason": "opposite_direction",
                        "reverse_type": sorted(back)[0]})

    # ④ 크기 3 이상의 강연결요소. 2짜리는 ②가 이미 말했다.
    succ: dict[str, list[str]] = {}
    for (u, v) in flow:
        succ.setdefault(u, []).append(v)
        succ.setdefault(v, [])
    for k in succ:
        succ[k].sort()
    for comp in _strongly_connected(succ):
        if len(comp) < 3:
            continue
        members = sorted(comp)
        path = _shortest_cycle(members[0], succ, set(members))
        out.append({"source": path[0], "target": path[1],
                    "type": sorted(flow[(path[0], path[1])])[0],
                    "reason": "cycle", "nodes": path, "members": members})
    return out


def _strongly_connected(succ: dict[str, list[str]]) -> list[list[str]]:
    """반복형 Tarjan. 서브그래프가 depth 4 까지 커질 수 있어 재귀 한도를 피한다.
    노드는 정렬 순으로 방문해 결과가 결정론이다."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    comps: list[list[str]] = []
    counter = 0
    for root in sorted(succ):
        if root in index:
            continue
        work: list[tuple[str, int]] = [(root, 0)]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, i = work[-1]
            nbrs = succ.get(node, [])
            if i < len(nbrs):
                work[-1] = (node, i + 1)
                nxt = nbrs[i]
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, 0))
                elif nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                comp: list[str] = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                comps.append(comp)
    comps.sort(key=lambda c: min(c))
    return comps


def _shortest_cycle(start: str, succ: dict[str, list[str]], within: set[str]) -> list[str]:
    """`start` 에서 나가 `start` 로 돌아오는 가장 짧은 고리(구성원 안에서만, BFS).
    반환은 `start` 로 시작하고 되돌아오는 마지막 `start` 는 뺀 노드 열이다.
    강연결요소 안이라 고리는 반드시 있다."""
    prev: dict[str, str] = {}
    queue = [start]
    seen = {start}
    head = 0
    while head < len(queue):
        node = queue[head]
        head += 1
        for nxt in succ.get(node, []):
            if nxt not in within:
                continue
            if nxt == start:
                path = [node]
                while path[-1] != start:
                    path.append(prev[path[-1]])
                return list(reversed(path))
            if nxt not in seen:
                seen.add(nxt)
                prev[nxt] = node
                queue.append(nxt)
    return [start]   # 도달 불가(강연결요소면 일어나지 않는다)
