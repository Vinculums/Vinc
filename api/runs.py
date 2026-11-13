# -*- coding: utf-8 -*-
"""실행 기록(`episode:run-*`)을 훑는다. 모델도 상태도 없다.

`cited` 를 주면 그 주장(Record·Decision·문서)을 `cited_ids` 에 담은 실행만 돌려주고, 주장의
지금 유효성(은퇴했는가·후속이 누구인가)을 함께 싣는다. 은퇴한 주장을 인용한 실행이 곧 다시
확인할 실행이다. 같은 변경이 이벤트(`run.citation_superseded`)로도 나가며, 이 목록은 그 이벤트를
놓쳤거나 늦게 구독한 쪽이 직접 묻는 문이다.
근거: [[vinc.plan.mcp-events-and-webhooks]]
"""
from __future__ import annotations

from store import pkns, run_state

from . import events, query as q, readkit

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
#: 문서 인용은 두 별칭 중 하나로 적힌다(이벤트 계산과 같은 규칙).
_DOC_ALIASES = ("doc:", "concept:doc.")


def clamp_limit(value) -> int:
    return readkit.clamp_limit(value, default=DEFAULT_LIMIT, maximum=MAX_LIMIT)


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
        rows = readkit.run_plan(q.QueryPlan(
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
        rows = readkit.run_plan(q.QueryPlan(
            f"runs_claim_{label}", f"MATCH (x:{label}) WHERE x.{pk} = $id AND x.owner_id = $owner "
            f"RETURN {cols}", {"id": pkns.key(owner, claim), "owner": owner}), store)
        if rows:
            props = readkit.props(rows[0].get("props"))
            vu = str(rows[0].get("vu") or props.get("valid_until") or "")
            sb = str(props.get("superseded_by")
                     or readkit.superseded_by(store, owner, label, pk, [claim]).get(claim) or "")
            out.update(found=True, valid_until=vu, superseded_by=sb)
    out["retired"] = bool(out["valid_until"] or out["superseded_by"])
    return out


def _episodes(store, owner: str) -> list[dict]:
    return readkit.run_plan(q.QueryPlan(
        "runs_list", "MATCH (e:Episode) WHERE e.owner_id = $owner AND "
        "e.episode_id STARTS WITH $prefix RETURN e.episode_id AS id, e.title AS title, "
        "e.date AS date, e.props AS props",
        {"owner": owner, "prefix": pkns.key(owner, events.RUN_PREFIX)}), store)


def parse_flagged(value) -> bool:
    """`flagged` 는 참·거짓만 받는다. 비면 거짓."""
    if value in (None, "", False, "false", "0"):
        return False
    if value in (True, "true", "1"):
        return True
    raise ValueError("invalid_flagged: expected true or false")


def list_runs(store, *, owner: str, cited: str | None = None, role: str | None = None,
              run: str | None = None, flagged: bool = False, limit: int = DEFAULT_LIMIT,
              stored: bool = False, now=None) -> dict:
    """실행 기록 목록, 최근 것부터. `cited` 는 그 주장을 인용한 실행만 남긴다.

    `run`(episode id 또는 호스트의 run id)을 주면 그 실행 하나를 `props` 통째로 돌려준다.
    열려 있는 실행의 상태(`state`·`pending`·`step`)를 프로토콜이 읽는 문이다.

    요청이 만료된 대기는 막힌 채 끝난 것으로 읽는다(`run_protocol.as_read`). 계산만 하고 쓰지
    않는다. `stored` 는 저장된 행 그대로다: 프로토콜의 제출·답만 쓰고, 그래야 `_expire` 가 행을 닫는다."""
    from vinc_mcp import run_protocol as rp

    base = {"kind": "runs", "executed": False}
    if not owner:
        return {**base, "error": "owner is required"}
    want_role = events.role_id(role)
    wanted = set(_aliases(cited)) if cited else set()
    try:
        rows = _episodes(store, owner)
        claim = claim_status(store, owner, cited) if cited else None
    except readkit.StoreFailed as exc:
        return {**base, "error": str(exc)}
    runs = []
    for row in rows:
        # 잘못된 모양의 기록 하나가 목록 전체를 멈추지 않게 읽는 한 벌(`store/run_state.py`)을 지난다.
        props = run_state.readable(row.get("props"))
        title = readkit.title(row.get("title"))
        if not stored:
            read = rp.as_read(props, now)
            if read is not props:
                # 저장된 제목은 아직 waiting 이다. 읽는 쪽에는 끝난 실행의 제목을 보인다.
                if read.get("role") and read.get("run_id"):
                    title = rp._title(read, pkns.strip(row["id"]))
                props = read
            elif props.get("verdict") and props.get("role") and props.get("run_id"):
                # 결론은 보호되지 않아 프로토콜 밖에서도 바뀐다. 목록의 제목은 저장된 결론에서 다시 짓는다.
                title = rp._title(props, pkns.strip(row["id"]))
        run_role = events.role_id(props.get("role")) or ""
        if want_role and run_role != want_role:
            continue
        ids = list(props.get("cited_ids") or [])
        matched = [c for c in dict.fromkeys(ids) if c in wanted]
        if cited and not matched:
            continue
        if run and run not in (pkns.strip(row["id"]), str(props.get("run_id") or "")):
            continue
        # 능력 감사 표지(vinc_mcp/run_protocol.py). 역할이 허락하지 않은 능력을 쓴 실행이다.
        flags = list(props.get("capability_flags") or [])
        if flagged and not flags:
            continue
        item = {"id": pkns.strip(row["id"]), "run_id": str(props.get("run_id") or ""),
                "role": run_role, "outcome": str(props.get("outcome") or ""),
                "end_reason": str(props.get("end_reason") or ""),
                "title": title, "date": str(row.get("date") or ""),
                "host": str(props.get("host") or ""), "cited_ids": ids, "capability_flags": flags}
        if props.get("verdict"):
            # 일이 내린 결론(`run_state.readable` 이 아는 낱말만 남긴다). 결론을 보고하지 않은 실행의
            # 항목은 이 키가 없어 그대로다.
            item["verdict"] = props["verdict"]
            if props.get("verdict_note"):
                item["verdict_note"] = props["verdict_note"]
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


#: 만료된 요청을 목록에 남겨 두는 시간(초). 사람이 "만료됨"을 볼 수 있을 만큼만 둔다.
EXPIRED_SHOWN_S = 86400


def waiting_runs(store, *, owner: str, limit: int = DEFAULT_LIMIT, now=None) -> dict:
    """사람의 답을 기다리는 실행과 그 대기 하나씩, 만료가 가까운 것부터.

    실행 기록 Episode 만 읽는다(따로 둔 저장소가 없다). 항목마다 사람이 보고 답할 것 전부를
    싣는다: 질문, 행동마다 호스트가 적은 설명, 종류(person_only·ask_first), 물은 때와 만료.
    만료된 대기는 `expired: true` 로 하루 더 보이고 그 뒤에는 빠진다. 계정의 세션 프록시가 이
    목록을 사람에게 보여 준다(`vinc_mcp/http_server.py` 의 `runs-waiting`)."""
    import datetime

    from vinc_mcp import run_protocol as rp

    base = {"kind": "runs.waiting", "executed": False}
    if not owner:
        return {**base, "error": "owner is required"}
    try:
        rows = _episodes(store, owner)
    except readkit.StoreFailed as exc:
        return {**base, "error": str(exc)}
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    shown_after = moment - datetime.timedelta(seconds=EXPIRED_SHOWN_S)
    items = []
    for row in rows:
        # 대기 중인 실행은 늘 dict 인 대기와 1 이상의 질문 번호로 읽힌다(`run_state.readable`). 값이
        # 잘못된 기록도 목록에 남아 사람이 보고 닫을 수 있고, 다른 기록의 목록을 막지 않는다.
        props = run_state.readable(row.get("props"))
        pending = props.get("pending")
        if props.get("state") != "waiting":
            continue
        view = rp.decision(pending)
        if rp.expired(pending, shown_after):
            continue
        items.append({
            "id": pkns.strip(row["id"]), "run_id": str(props.get("run_id") or ""),
            "role": events.role_id(props.get("role")) or "", "space": owner,
            "title": readkit.title(row.get("title")), "task": str(props.get("task") or ""),
            "subject": str(props.get("subject") or ""), "host": str(props.get("host") or ""),
            "step": props.get("step", 0), "ask": props["asks"],
            "started_at": str(props.get("started_at") or ""),
            "asked_at": view["asked_at"], "expires_at": view["expires_at"],
            "expired": rp.expired(pending, moment), "decision": view})
    items.sort(key=lambda r: (r["expired"], r["expires_at"], r["id"]))
    return {"kind": "runs.waiting", "executed": True, "runs": items[:limit], "total": len(items),
            "truncated": len(items) > limit}
