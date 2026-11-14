# -*- coding: utf-8 -*-
"""변경 기록에서 Vinc 만 볼 수 있는 이벤트를 계산한다. 모델도 상태도 없다.

소유자마다 카운터가 둘이다: 노드 변경의 `seq:<owner>` 와 문서 변경의 `seq:doc:<owner>`.
커서는 그 둘을 `"<노드>.<문서>"` 로 묶은 불투명 문자열이고, 빈 커서는 "지금부터",
`"0"` 은 처음부터다. 한 행은 마지막 `sync_seq` 에서 한 번만 보이므로 두 번 바뀐 것은
최신 상태 하나로 합쳐진다. 이벤트 id 는 결정론이라 피드와 웹훅이 같은 id 를 낸다.

계약(이벤트 종류·식별·커서·페이지): [[vinc.plan.mcp-events-and-webhooks]]
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re

from store import pkns, run_state
from store.querybase import mirror_id

from . import query as q, readkit, role_contract

#: 내는 이벤트. 순서가 `events/list` 의 순서다.
KINDS = ("role.reading_changed", "run.citation_superseded", "role.lesson_added",
         "run.waiting", "run.answered")
#: 이름만 잡아 둔 이벤트. 지금은 없다(`run.answered` 는 실행 프로토콜이 생기며 KINDS 로 올랐다).
RESERVED: tuple[str, ...] = ()
#: 구독 기록은 역할에 붙지만 교훈이 아니다. 세면 구독이 제 구독자를 깨운다.
SUBSCRIPTION_PREFIX = "record:event-subscription-"
#: 평가 카드도 역할에 붙는 Record 지만 교훈이 아니라 측정 결과다(`api/eval_cards.py`).
EVAL_CARD_PREFIX = "record:eval-card-"
#: 실행 기록 Episode 의 id 접두(GRAPH.md "Agent runs").
RUN_PREFIX = "episode:run-"
DEFAULT_LIMIT = 100
MAX_LIMIT = 500
TITLE_CAP = readkit.TITLE_CAP

_CURSOR = re.compile(r"(\d{1,18})\.(\d{1,18})")
_NEEDS = {"role.reading_changed": {"docs"}, "run.citation_superseded": {"nodes", "docs"},
          "role.lesson_added": {"nodes"}, "run.waiting": {"nodes"}, "run.answered": {"nodes"}}
#: Decision 에서 Concept 로 가는 관계 중 역할에 "붙었다"고 읽는 것. 스키마에서 파생한다.
_DECISION_TO_ROLE = tuple(sorted(
    rel for rel, pairs in q.SYNC_EDGE_RELS.items()
    if ("Decision", "decision_id", "Concept", "concept_id") in pairs
    and rel not in ("SUPERSEDES", "ATTACHES_TO")))


def parse_cursor(value) -> tuple[int, int] | None:
    """`None` 은 지금부터. `"0"` 은 처음부터. 그 밖은 `"<n>.<d>"` 만 받는다."""
    if value is None or value == "":
        return None
    text = str(value).strip()
    if text == "0":
        return 0, 0
    found = _CURSOR.fullmatch(text)
    if not found:
        raise ValueError("invalid_cursor: expected the cursor a previous call returned, or 0")
    return int(found.group(1)), int(found.group(2))


def format_cursor(pos: tuple[int, int]) -> str:
    return f"{pos[0]}.{pos[1]}"


def parse_kinds(value) -> tuple[str, ...]:
    """쉼표 목록이나 리스트. 비면 전부. 예약 이름과 모르는 이름은 거절한다."""
    if value in (None, "", []):
        return KINDS
    items = value.split(",") if isinstance(value, str) else list(value)
    out = []
    for item in items:
        name = str(item).strip()
        if name not in KINDS:
            raise ValueError(f"invalid_kind: {name!r} is not one of {', '.join(KINDS)}")
        if name not in out:
            out.append(name)
    return tuple(out)


def clamp_limit(value) -> int:
    return readkit.clamp_limit(value, default=DEFAULT_LIMIT, maximum=MAX_LIMIT)


def role_id(value) -> str | None:
    """`agent-dev-core` 와 `concept:agent-dev-core` 를 같은 id 로. 빈 값은 None."""
    text = str(value or "").strip()
    if not text:
        return None
    return text if text.startswith("concept:") else "concept:" + text


def event_id(owner: str, name: str, *identity) -> str:
    """소유자·이름·정체에서 만든 id. 소유자는 해시 안에만 있다."""
    raw = json.dumps([owner, name, *identity], ensure_ascii=False, separators=(",", ":"))
    return "evt_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# 읽기 도구는 `api/readkit.py` 에 있다. 아래 사설 이름은 같은 객체를 가리키며 이 모듈 안의 호출과
# 바깥에서 사설 이름으로 부르는 곳이 그대로 돈다.
_props = readkit.props
_title = readkit.title
_run = readkit.run_plan
_StoreFailed = readkit.StoreFailed
_superseded_by = readkit.superseded_by


def head(store, owner: str) -> tuple[int, int]:
    """두 카운터의 지금 값. 한 번도 안 쓴 카운터는 0."""
    keys = {f"seq:{owner}": 0, f"seq:doc:{owner}": 1}
    rows = _run(q.QueryPlan("events_head", "MATCH (m:_SyncMeta) WHERE m.key IN $keys "
                            "RETURN m.key AS k, m.value AS v", {"keys": list(keys)}), store)
    pos = [0, 0]
    for row in rows:
        try:
            pos[keys[row["k"]]] = int(row.get("v") or 0)
        except (KeyError, TypeError, ValueError):
            continue
    return pos[0], pos[1]


def _changed(store, label: str, columns: str, owner: str, since: int, limit: int,
             where: str = "", params: dict | None = None) -> list[dict]:
    var = "x"
    return _run(q.QueryPlan(
        f"events_{label}",
        f"MATCH ({var}:{label}) WHERE {var}.owner_id = $owner AND {var}.sync_seq > $since{where} "
        f"RETURN {columns}, {var}.sync_seq AS seq ORDER BY {var}.sync_seq LIMIT $n",
        {"owner": owner, "since": since, "n": limit + 1, **(params or {})}), store)


def _page_end(sources: list[list[dict]], top: int, limit: int) -> tuple[int, bool]:
    """한 카운터의 이번 페이지 끝. 한 출처라도 꽉 찼으면 그 출처의 마지막 seq 에서 끊는다."""
    end, more = top, False
    for rows in sources:
        if len(rows) > limit:
            more = True
            end = min(end, int(rows[limit - 1]["seq"]))
    return end, more


def _roles(store, owner: str) -> dict[str, dict]:
    rows = _run(q.QueryPlan(
        "events_roles", "MATCH (n:Concept) WHERE n.owner_id = $owner AND "
        "n.concept_id STARTS WITH $prefix RETURN n.concept_id AS id, n.domain AS domain, "
        "n.props AS props", {"owner": owner, "prefix": pkns.key(owner, role_contract.ID_PREFIX)}),
        store)
    out = {}
    for row in rows:
        nid = pkns.strip(row["id"])
        props = _props(row.get("props"))
        if role_contract.is_role(nid, row.get("domain"), props):
            reads = props.get("reads")
            out[nid] = {"reads": [r for r in reads if isinstance(r, str)]
                        if isinstance(reads, list) else []}
    return out


def _attached(store, owner: str, label: str, pk: str, rels: str, ids: list[str]) -> list[tuple]:
    if not ids:
        return []
    rows = _run(q.QueryPlan(
        f"events_attached_{label}",
        f"MATCH (a:{label})-[r:{rels}]->(b:Concept) WHERE a.{pk} IN $ids "
        "AND a.owner_id = $owner AND b.owner_id = $owner AND b.concept_id STARTS WITH $prefix "
        f"RETURN a.{pk} AS id, b.concept_id AS role",
        {"ids": pkns.keys(owner, ids), "owner": owner,
         "prefix": pkns.key(owner, role_contract.ID_PREFIX)}), store)
    return [(pkns.strip(r["id"]), pkns.strip(r["role"])) for r in rows]


def _runs(store, owner: str) -> list[tuple[str, dict]]:
    rows = _run(q.QueryPlan(
        "events_runs", "MATCH (e:Episode) WHERE e.owner_id = $owner AND "
        "e.episode_id STARTS WITH $prefix RETURN e.episode_id AS id, e.props AS props",
        {"owner": owner, "prefix": pkns.key(owner, RUN_PREFIX)}), store)
    return [(pkns.strip(r["id"]), run_state.readable(r.get("props"))) for r in rows]


def scan(store, *, owner: str, since: tuple[int, int] | None, limit: int = DEFAULT_LIMIT,
         role: str | None = None, kinds: tuple[str, ...] = KINDS) -> dict:
    """`since` 뒤의 변경에서 이벤트 한 페이지. 결과의 `cursor` 는 다음 호출의 `since` 다.

    각 이벤트에는 `_pos`(`["n"|"d", seq]`)가 실린다. 웹훅의 안전 워터마크 계산용이고
    공개 표면은 걷어 낸다."""
    base = {"kind": "events", "executed": False}
    if not owner:
        return {**base, "error": "owner is required"}
    try:
        return _scan(store, owner=owner, since=since, limit=limit, role=role_id(role),
                     kinds=kinds or KINDS)
    except _StoreFailed as exc:
        return {**base, "error": str(exc)}


def _scan(store, *, owner, since, limit, role, kinds) -> dict:
    top = head(store, owner)
    out = {"kind": "events", "executed": True, "events": [], "has_more": False,
           "truncated": False, "head": format_cursor(top)}
    if since is None:
        return {**out, "cursor": format_cursor(top)}
    if since[0] > top[0] or since[1] > top[1]:
        # 스토어가 되감겼거나 다른 스페이스의 커서다. 지금으로 다시 맞추고 그 사실을 말한다.
        return {**out, "cursor": format_cursor(top), "truncated": True}
    need = set().union(*(_NEEDS[k] for k in kinds))
    recs = decs = docs = tombs = runs = []
    if {"run.waiting", "run.answered"} & set(kinds):
        # 실행 기록 Episode 만 본다: 다른 Episode 가 페이지를 먹지 않게 접두를 질의에 건다.
        runs = _changed(store, "Episode", "x.episode_id AS id, x.title AS title, x.props AS props, "
                        "x.updated_at AS ts", owner, since[0], limit,
                        " AND x.episode_id STARTS WITH $run_prefix",
                        {"run_prefix": pkns.key(owner, RUN_PREFIX)})
    if {"role.lesson_added", "run.citation_superseded"} & set(kinds):
        recs = _changed(store, "Record", "x.record_id AS id, x.title AS title, x.props AS props, "
                        "x.valid_until AS valid_until, x.updated_at AS ts", owner, since[0], limit)
        decs = _changed(store, "Decision", "x.decision_id AS id, x.title AS title, "
                        "x.props AS props, x.updated_at AS ts", owner, since[0], limit)
    if "docs" in need:
        docs = _changed(store, "_DocMeta", "x.doc_id AS id, x.title AS title, "
                        "x.content_hash AS hash, x.valid_until AS valid_until, "
                        "x.superseded_by AS superseded_by, x.updated_at AS ts",
                        owner, since[1], limit)
        tombs = _changed(store, "_DocTombstone", "x.doc_id AS id, x.deleted_at AS ts",
                         owner, since[1], limit)
    n_end, n_more = _page_end([recs, decs, runs], top[0], limit) if "nodes" in need else (
        top[0], False)
    d_end, d_more = _page_end([docs, tombs], top[1], limit) if "docs" in need else (top[1], False)
    recs = [r for r in recs if int(r["seq"]) <= n_end]
    decs = [r for r in decs if int(r["seq"]) <= n_end]
    runs = [r for r in runs if int(r["seq"]) <= n_end]
    docs = [r for r in docs if int(r["seq"]) <= d_end]
    tombs = [r for r in tombs if int(r["seq"]) <= d_end]
    for rows in (recs, decs):
        for r in rows:
            r["id"] = pkns.strip(r["id"])
    found: dict[str, dict] = {}

    def emit(name, identity, data, row, counter):
        if role and data.get("role") != role:
            return
        eid = event_id(owner, name, *identity)
        found.setdefault(eid, {"eventId": eid, "name": name,
                               "timestamp": str(row.get("ts") or "") or _now(), "data": data,
                               "_pos": [counter, int(row["seq"])]})

    roles = _roles(store, owner) if (docs or tombs or recs or decs) and (
        "role.reading_changed" in kinds or "role.lesson_added" in kinds) else {}
    if "role.reading_changed" in kinds and roles:
        readers: dict[str, list[str]] = {}
        for rid, info in roles.items():
            for doc in info["reads"]:
                readers.setdefault(doc, []).append(rid)
        for r in docs:
            vu, sb = str(r.get("valid_until") or ""), str(r.get("superseded_by") or "")
            change = "retired" if (vu or sb) else "updated"
            for rid in sorted(readers.get(r["id"], ())):
                emit("role.reading_changed", (rid, r["id"], change, r.get("hash") or "", vu, sb),
                     {"role": rid, "doc_id": r["id"], "title": _title(r.get("title")),
                      "change": change, "content_hash": r.get("hash") or "",
                      "valid_until": vu, "superseded_by": sb}, r, "d")
        for r in tombs:
            for rid in sorted(readers.get(r["id"], ())):
                emit("role.reading_changed", (rid, r["id"], "deleted", str(r.get("ts") or "")),
                     {"role": rid, "doc_id": r["id"], "title": "", "change": "deleted",
                      "content_hash": "", "valid_until": "", "superseded_by": ""}, r, "d")
    if "role.lesson_added" in kinds and roles:
        lessons = [r for r in recs
                   if not r["id"].startswith((SUBSCRIPTION_PREFIX, EVAL_CARD_PREFIX))]
        by_id = {r["id"]: r for r in lessons} | {r["id"]: r for r in decs}
        pairs = (_attached(store, owner, "Record", "record_id", "ATTACHES_TO",
                           [r["id"] for r in lessons])
                 + _attached(store, owner, "Decision", "decision_id", "|".join(_DECISION_TO_ROLE),
                             [r["id"] for r in decs]))
        for lid, rid in sorted(set(pairs)):
            if rid not in roles or lid not in by_id:
                continue
            kind = "record" if lid.startswith("record:") else "decision"
            emit("role.lesson_added", (rid, lid),
                 {"role": rid, "lesson": lid, "lesson_kind": kind,
                  "title": _title(by_id[lid].get("title"))}, by_id[lid], "n")
    if "run.citation_superseded" in kinds:
        _citations(store, owner, recs, decs, docs, emit)
    if "run.waiting" in kinds:
        _waiting(runs, owner, emit)
    if "run.answered" in kinds:
        _answered(runs, owner, emit)
    events = sorted(found.values(), key=lambda e: (e["timestamp"], e["_pos"][0], e["_pos"][1],
                                                   e["eventId"]))
    return {**out, "events": events, "cursor": format_cursor((n_end, d_end)),
            "has_more": n_more or d_more}


def _citations(store, owner, recs, decs, docs, emit) -> None:
    """은퇴한 주장 → 그것을 인용한 실행 기록. 은퇴 = valid_until·superseded_by·들어오는 대체."""
    retired: dict[str, tuple[dict, str, str, str]] = {}
    sup_r = _superseded_by(store, owner, "Record", "record_id", [r["id"] for r in recs])
    for r in recs:
        props = _props(r.get("props"))
        vu = str(r.get("valid_until") or "")
        sb = str(props.get("superseded_by") or sup_r.get(r["id"]) or "")
        if vu or sb:
            retired[r["id"]] = (r, vu, sb, "n")
    sup_d = _superseded_by(store, owner, "Decision", "decision_id", [r["id"] for r in decs])
    for r in decs:
        props = _props(r.get("props"))
        vu = str(props.get("valid_until") or "")
        sb = str(props.get("superseded_by") or sup_d.get(r["id"]) or "")
        if vu or sb:
            retired[r["id"]] = (r, vu, sb, "n")
    for r in docs:
        vu, sb = str(r.get("valid_until") or ""), str(r.get("superseded_by") or "")
        if vu or sb:
            for alias in (mirror_id(r["id"]), "doc:" + r["id"]):
                retired[alias] = (r, vu, sb, "d")
    if not retired:
        return
    for run_id, props in _runs(store, owner):
        cited = props.get("cited_ids")
        if not isinstance(cited, list):
            continue
        run_role = role_id(props.get("role")) or ""
        for claim in dict.fromkeys(c for c in cited if isinstance(c, str)):
            hit = retired.get(claim)
            if hit is None:
                continue
            row, vu, sb, counter = hit
            emit("run.citation_superseded", (run_id, claim, vu, sb),
                 {"run": run_id, "role": run_role, "claim": claim,
                  "valid_until": vu, "superseded_by": sb}, row, counter)


def _waiting(runs: list[dict], owner: str, emit) -> None:
    """실행이 사람 앞에서 대기에 들어선 순간. 알림(푸시)이 읽으므로 행동 설명·질문·작업은 싣지 않고
    id·스페이스·제목·만료만 싣는다. id 는 (실행, 질문 번호, 물은 때)에 묶인다.

    역할이 없는 대기(null 이거나 키가 없다)도 승인함에 보이므로 나간다. 역할은 빈 문자열이고, 제목은
    저장된 제목 대신 episode id 로 짓는다(`run_protocol._title`): 그런 기록의 제목은 보낸 쪽의 글이다."""
    from vinc_mcp import run_protocol as rp

    for row in runs:
        props = run_state.readable(row.get("props"))
        pending = props.get("pending") if isinstance(props.get("pending"), dict) else {}
        at, role = str(pending.get("asked_at") or ""), role_id(props.get("role"))
        if props.get("state") != "waiting" or not at:
            continue
        run_id, ask = pkns.strip(row["id"]), props["asks"]
        title = _title(row.get("title") if role else rp._title({**props, "run_id": ""}, run_id))
        emit("run.waiting", (run_id, ask, at),
             {"run": run_id, "role": role or "", "space": owner, "title": title,
              "ask": ask, "asked_at": at, "expires_at": str(pending.get("expires_at") or "")},
             {**row, "ts": at}, "n")


def _answered(runs: list[dict], owner: str, emit) -> None:
    """사람이 대기 중인 실행에 답한 순간. 답은 실행 기록 props 의 `answer`·`answered_at` 에 남는다.
    같은 실행의 이후 단계가 기록을 고쳐도 id 는 (실행, 답한 때, 답)에 묶여 그대로다.
    `via` 는 답이 온 길이다(person 은 서명된 세션, agent 는 도구 호출). 글 답(reply)의 본문은 싣지
    않는다."""
    for row in runs:
        props = run_state.readable(row.get("props"))
        answer, at = props.get("answer"), str(props.get("answered_at") or "")
        role = role_id(props.get("role"))
        if answer not in ("approve", "decline", "reply") or not at or not role:
            continue
        run_id = pkns.strip(row["id"])
        actions = list(props.get("answered_actions") or [])
        emit("run.answered", (run_id, at, answer),
             {"run": run_id, "role": role, "answer": answer, "answered_at": at,
              "actions": actions, "via": str(props.get("answer_via") or "agent"),
              "space": owner, "title": _title(row.get("title"))}, {**row, "ts": at}, "n")


def public(result: dict) -> dict:
    """공개 표면(도구·REST)이 내는 모양. 내부 위치(`_pos`)를 걷어 낸다."""
    if not isinstance(result, dict) or not isinstance(result.get("events"), list):
        return result
    return {**result, "events": [{k: v for k, v in e.items() if k != "_pos"}
                                 for e in result["events"]]}
