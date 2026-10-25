# -*- coding: utf-8 -*-
"""실행 기록(`episode:run-*`)을 훑는다. 모델도 상태도 없다.

`cited` 를 주면 그 주장(Record·Decision·문서)을 `cited_ids` 에 담은 실행만 돌려주고, 주장의
지금 유효성(은퇴했는가·후속이 누구인가)을 함께 싣는다. 은퇴한 주장을 인용한 실행이 곧 다시
확인할 실행이다. 같은 변경이 이벤트(`run.citation_superseded`)로도 나가며, 이 목록은 그 이벤트를
놓쳤거나 늦게 구독한 쪽이 직접 묻는 문이다.
근거: [[vinc.plan.mcp-events-and-webhooks]]
"""
from __future__ import annotations

from store import pkns

from . import events, query as q

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
#: 문서 인용은 두 별칭 중 하나로 적힌다(이벤트 계산과 같은 규칙).
_DOC_ALIASES = ("doc:", "concept:doc.")


def clamp_limit(value) -> int:
    if value in (None, ""):
        return DEFAULT_LIMIT
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ValueError("invalid_limit: expected an integer") from None
    if not 1 <= n <= MAX_LIMIT:
        raise ValueError(f"invalid_limit: expected 1..{MAX_LIMIT}")
    return n


def parse_cited(value) -> str | None:
    """인용을 찾을 주장 id. 빈 값은 None, 타입 접두가 없으면 거절한다."""
    text = str(value or "").strip()
    if not text:
        return None
    if ":" not in text:
        raise ValueError("invalid_cited: expected a graph id with a type prefix, "
                         "such as record:x, decision:x or doc:x")
    return text


def _aliases(claim: str) -> tuple[str, ...]:
    """`doc:x` 와 `concept:doc.x` 는 같은 문서다. 그 밖의 id 는 하나뿐이다."""
    for prefix in _DOC_ALIASES:
        if claim.startswith(prefix):
            doc = claim[len(prefix):]
            return tuple(p + doc for p in _DOC_ALIASES)
    return (claim,)


def _doc_id(claim: str) -> str | None:
    for prefix in _DOC_ALIASES:
        if claim.startswith(prefix):
            return claim[len(prefix):]
    return None


def claim_status(store, owner: str, claim: str) -> dict:
    """주장 하나의 지금 상태: `{id, found, retired, valid_until, superseded_by}`."""
    out = {"id": claim, "found": False, "retired": False, "valid_until": "", "superseded_by": ""}
    doc = _doc_id(claim)
    if doc is not None:
        rows = events._run(q.QueryPlan(
            "runs_claim_doc", "MATCH (d:_DocMeta) WHERE d.doc_id = $id AND d.owner_id = $owner "
            "RETURN d.valid_until AS vu, d.superseded_by AS sb",
            {"id": doc, "owner": owner}), store)
        if rows:
            out.update(found=True, valid_until=str(rows[0].get("vu") or ""),
                       superseded_by=str(rows[0].get("sb") or ""))
    elif claim.startswith(("record:", "decision:")):
        label, pk = (("Record", "record_id") if claim.startswith("record:")
                     else ("Decision", "decision_id"))
        cols = "x.props AS props" + (", x.valid_until AS vu" if label == "Record" else "")
        rows = events._run(q.QueryPlan(
            f"runs_claim_{label}", f"MATCH (x:{label}) WHERE x.{pk} = $id AND x.owner_id = $owner "
            f"RETURN {cols}", {"id": pkns.key(owner, claim), "owner": owner}), store)
        if rows:
            props = events._props(rows[0].get("props"))
            vu = str(rows[0].get("vu") or props.get("valid_until") or "")
            sb = str(props.get("superseded_by")
                     or events._superseded_by(store, owner, label, pk, [claim]).get(claim) or "")
            out.update(found=True, valid_until=vu, superseded_by=sb)
    out["retired"] = bool(out["valid_until"] or out["superseded_by"])
    return out


def _episodes(store, owner: str) -> list[dict]:
    return events._run(q.QueryPlan(
        "runs_list", "MATCH (e:Episode) WHERE e.owner_id = $owner AND "
        "starts_with(e.episode_id, $prefix) RETURN e.episode_id AS id, e.title AS title, "
        "e.date AS date, e.props AS props",
        {"owner": owner, "prefix": pkns.key(owner, events.RUN_PREFIX)}), store)


def list_runs(store, *, owner: str, cited: str | None = None, role: str | None = None,
              run: str | None = None, limit: int = DEFAULT_LIMIT) -> dict:
    """실행 기록 목록, 최근 것부터. `cited` 는 그 주장을 인용한 실행만 남긴다.

    `run`(episode id 또는 호스트의 run id)을 주면 그 실행 하나를 `props` 통째로 돌려준다.
    열려 있는 실행의 상태(`state`·`pending`·`step`)를 프로토콜이 읽는 문이다."""
    base = {"kind": "runs", "executed": False}
    if not owner:
        return {**base, "error": "owner is required"}
    want_role = events.role_id(role)
    wanted = set(_aliases(cited)) if cited else set()
    try:
        rows = _episodes(store, owner)
        claim = claim_status(store, owner, cited) if cited else None
    except events._StoreFailed as exc:
        return {**base, "error": str(exc)}
    runs = []
    for row in rows:
        props = events._props(row.get("props"))
        run_role = events.role_id(props.get("role")) or ""
        if want_role and run_role != want_role:
            continue
        ids = [c for c in props.get("cited_ids") or [] if isinstance(c, str)]
        matched = [c for c in dict.fromkeys(ids) if c in wanted]
        if cited and not matched:
            continue
        if run and run not in (pkns.strip(row["id"]), str(props.get("run_id") or "")):
            continue
        item = {"id": pkns.strip(row["id"]), "run_id": str(props.get("run_id") or ""),
                "role": run_role, "outcome": str(props.get("outcome") or ""),
                "title": events._title(row.get("title")), "date": str(row.get("date") or ""),
                "host": str(props.get("host") or ""), "cited_ids": ids}
        if run:
            item["props"] = props
        if cited:
            item["matched"] = matched
        runs.append(item)
    runs.sort(key=lambda r: (r["date"], r["id"]), reverse=True)
    out = {"kind": "runs", "executed": True, "runs": runs[:limit], "total": len(runs),
           "truncated": len(runs) > limit}
    if claim is not None:
        out["claim"] = claim
    return out
