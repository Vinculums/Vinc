"""스펙 주장·불일치 후보·검증 기록의 저작 계약. 스키마를 늘리지 않고 Record props 를 검사한다.

세 스키마가 있다. ``vinc.specclaim.v1`` 은 스펙 문서의 검사 가능한 문장 하나,
``vinc.specdrift.v1`` 은 그 문장과 변경 근거 사이의 불일치 후보, ``vinc.specverify.v1`` 은
이름 붙은 검사가 한 커밋에서 그 문장을 확인한 기록이다. 최신 여부는 여기서 다루지 않는다.
그것은 `valid_from`/`valid_until` 에서 파생되고, ``review`` 는 그와 다른 축이다.

규칙 요지:
- 모델이 만든 후보(``generated_by: model``)는 ``verified`` 가 될 수 없다. 검증은 검사 기록이 한다.
- ``approved`` 에는 승인자와 승인 시각이 있어야 한다. 승인자는 사람이든 에이전트든 누구나다.
- 이 계약은 저작 경로(`authored`)에서만 돈다. 동기화·팩 복원은 막지 않는다.

근거: [[vinc.plan.temporal-edges-point-in-time-document-validity-2026-09-28]] 을 잇는
그래프 문서 vinc.plan.spec-drift-graphiti-0-9-8-2026-10-11 §2 와 그 판정
decision:spec-drift-rulings-2026-10-11 (둘 다 2026-10-11 작성, 인용 대조표 갱신 전).
"""
from __future__ import annotations

import re

CLAIM, DRIFT, VERIFY = "vinc.specclaim.v1", "vinc.specdrift.v1", "vinc.specverify.v1"
PREFIX = {CLAIM: "record:specclaim-", DRIFT: "record:specdrift-", VERIFY: "record:specverify-"}
REVIEW = ("candidate", "verified", "approved", "rejected")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_REPO_PATH = re.compile(r"^[A-Za-z0-9_.-]+:[^\s]+$")


class InvalidSpecRecord(ValueError):
    pass


def _text(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _need(props: dict, keys, schema: str) -> None:
    missing = [k for k in keys if not _text(props.get(k))]
    if missing:
        raise InvalidSpecRecord(f"{schema} requires {', '.join(missing)}")


def _day(props: dict, key: str, schema: str) -> None:
    if not _DAY.match(str(props.get(key) or "")):
        raise InvalidSpecRecord(f"{schema} {key} must be YYYY-MM-DD")


def _review(props: dict, schema: str, allowed=REVIEW) -> str:
    review = props.get("review")
    if review not in allowed:
        raise InvalidSpecRecord(f"{schema} review must be one of {', '.join(allowed)}")
    if review == "approved":
        _need(props, ("approved_by",), schema)
        _day(props, "approved_at", schema)
    return review


def validate(node: dict, *, existing: bool = False) -> str | None:
    """계약 대상이면 스키마 이름을, 아니면 None 을 돌려준다. 어기면 InvalidSpecRecord."""
    nid = str(node.get("id") or "")
    if "props" not in node and existing:
        # props 키가 없는 재전송은 "기존 props 보존"이다(엣지 끝점 선언·도메인만 바꾸기).
        # 면제는 **이미 저장된** 같은 소유자의 Record 에만 준다. 새 예약 id 는 아래에서
        # 거절된다(props 없이 새 기록을 만드는 길이 되면 안 된다). null·빈 props 는
        # 키가 있으므로 면제 대상이 아니다.
        return None
    props = node.get("props")
    schema = props.get("spec_schema") if isinstance(props, dict) else None
    if schema is None:
        if any(nid.startswith(p) for p in PREFIX.values()):
            raise InvalidSpecRecord(f"{nid} uses a reserved spec id prefix without props.spec_schema")
        return None
    if schema not in PREFIX:
        raise InvalidSpecRecord(f"unknown spec_schema {schema!r}; expected one of {', '.join(PREFIX)}")
    if node.get("type") != "record" or not nid.startswith(PREFIX[schema]):
        raise InvalidSpecRecord(f"{schema} requires a record carrier and a {PREFIX[schema]} id")
    if schema == CLAIM:
        _need(props, ("feature", "doc_id", "text", "subject_path", "measured_ref"), schema)
        if not str(props["feature"]).startswith(("concept:", "decision:")):
            raise InvalidSpecRecord(f"{schema} feature must be a concept: or decision: id")
        _day(props, "measured_at", schema)
        _review(props, schema)
    elif schema == DRIFT:
        _need(props, ("claim", "evidence", "observed", "generated_by"), schema)
        if props["generated_by"] not in ("rule", "model"):
            raise InvalidSpecRecord(f"{schema} generated_by must be rule or model")
        review = _review(props, schema, ("candidate", "approved", "rejected"))
        if review == "candidate" and props["generated_by"] == "model" and "p" in props:
            p = props["p"]
            if type(p) not in (int, float) or not 0 <= p <= 1:
                raise InvalidSpecRecord(f"{schema} p must be a number in [0, 1]")
    else:
        _need(props, ("claim", "check", "run_ref", "measured_ref", "result"), schema)
        if props["result"] not in ("pass", "fail"):
            raise InvalidSpecRecord(f"{schema} result must be pass or fail")
        _day(props, "measured_at", schema)
    if schema in (CLAIM, VERIFY) and _text(props.get("subject_path")) \
            and not _REPO_PATH.match(props["subject_path"].strip()):
        raise InvalidSpecRecord(f"{schema} subject_path must be repo:path")
    return schema


def _existing_records(store, ids: list[str], owner: str | None) -> set[str]:
    """선택된 소유자 범위에 이미 있는 Record id."""
    if store is None or not ids:
        return set()
    from store import pkns
    scope = " AND r.owner_id = $owner" if owner else ""
    rows = store.run(f"MATCH (r:Record) WHERE r.record_id IN $ids{scope} "
                     "RETURN r.record_id AS id",
                     {"ids": pkns.keys(owner, ids), "owner": owner or ""}) or []
    return {pkns.strip(r.get("id") or "") for r in rows}


def validate_nodes(nodes: list[dict], *, store=None, owner: str | None = None) -> str | None:
    """첫 위반의 사유. 없으면 None. props 없는 재전송 면제는 ``store`` 의 기존 Record 에만."""
    bare = [str(n.get("id") or "") for n in nodes
            if "props" not in n and n.get("type") == "record"
            and any(str(n.get("id") or "").startswith(p) for p in PREFIX.values())]
    existing = _existing_records(store, bare, owner)
    for node in nodes:
        try:
            validate(node, existing=str(node.get("id") or "") in existing
                     and node.get("type") == "record")
        except InvalidSpecRecord as exc:
            return f"{node.get('id') or '?'}: {exc}"
    return None
