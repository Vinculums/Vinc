"""노드 검색 미러의 텍스트와 현재 원문을 요청 소유자 안에서 대조한다."""
import json

from store import pkns

EXTRA_CHARS = 300
#: 종류별 부가 길이. 에피소드 요약은 질문이 묻는 세부가 300자 뒤에 있는 일이 잦아 600자까지 싣는다.
#: 근거: [[vinc.plan.episode-mirror-600-sealed11-2026-10-26]]
EXTRA_CHARS_BY_KIND = {"episode": 600}
#: (라벨, PK, 부가 텍스트 열). Record·Decision 은 본문 전용 열이 없어 `props` 에서 고른다
#: (:func:`extra_text`). 근거: [[vinc.spec.retrieval-knobs]] §2
SPEC = {
    "concept": ("Concept", "concept_id", "one_liner"),
    "record": ("Record", "record_id", "props"),
    "decision": ("Decision", "decision_id", "props"),
    "episode": ("Episode", "episode_id", "summary"),
}

#: props 본문 고르기는 brief 의 답 본문(`api.query._brief_body`)과 같은 순서다: 이 키들이
#: 먼저고, 없으면 가장 긴 문자열 값이다. 아래 키는 뜻이 아니라 주소·측정 표지라 고르지 않는다.
_BODY_KEYS = ("note", "summary", "one_liner", "description", "desc")
_NOT_BODY = frozenset({
    "subject_path", "measured_ref", "measured_at", "reviewed_at", "claim_kind", "run_dir",
    "source", "source_doc", "source_file", "policy_ref", "episode", "plan", "status",
    "authority", "decided_by", "url", "path", "commit", "pr", "issue",
    "valid_from", "valid_until"})
_MIN_BODY = 12


def props_body(props) -> str:
    """props(JSON 문자열 또는 dict)에서 본문 한 덩어리. 없으면 빈 문자열."""
    if isinstance(props, str):
        try:
            props = json.loads(props) if props.strip() else {}
        except ValueError:
            return ""
    if not isinstance(props, dict):
        return ""
    for k in _BODY_KEYS:
        v = props.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    rest = sorted(((k, v.strip()) for k, v in props.items()
                   if isinstance(v, str) and k not in _NOT_BODY and len(v.strip()) > _MIN_BODY),
                  key=lambda kv: (-len(kv[1]), kv[0]))
    return rest[0][1] if rest else ""


def extra_text(kind: str, raw) -> str:
    """SPEC 의 부가 열 값을 미러 텍스트의 부가 부분으로 바꾼다. 수집과 대조가 같은 함수를 써야
    미러 행이 낡은 것으로 잘못 읽히지 않는다."""
    if (SPEC.get(kind) or (None, None, None))[2] == "props":
        return props_body(raw)
    return str(raw or "")


def mirror_text(title: str, extra: str, kind: str = "") -> str:
    title = " ".join(str(title or "").split())
    extra = " ".join(str(extra or "").split())[:EXTRA_CHARS_BY_KIND.get(kind, EXTRA_CHARS)]
    return f"{title}. {extra}".strip(". ") if extra else title


def validate(run, hits: list[dict], *, owner: str | None) -> tuple[list[dict], dict]:
    state = {"freshness": "not_checked", "stale_candidates": 0, "missing_candidates": 0}
    # 소유자를 정하지 않은 읽기는 같은 논리 id의 다른 소유자를 임의로 택하지 않는다.
    if not owner:
        return hits, state
    sources = {}
    for kind, (label, pk, extra) in SPEC.items():
        ids = sorted({h["id"] for h in hits if h.get("kind") == kind and h.get("id")})
        if not ids:
            continue
        extra_col = f"n.{extra}" if extra else "''"
        rows = run(f"MATCH (n:{label}) WHERE n.{pk} IN $ids AND n.owner_id=$owner "
                   f"RETURN n.{pk} AS id, n.title AS title, {extra_col} AS extra",
                   {"ids": pkns.keys(owner, ids), "owner": owner}) or []
        for row in rows:
            sources[(kind, pkns.strip(row.get("id") or ""))] = row
    kept = []
    for hit in hits:
        source = sources.get((hit.get("kind"), hit.get("id")))
        if source is None:
            state["missing_candidates"] += 1
        elif hit.get("text") != mirror_text(source.get("title"),
                                                extra_text(hit.get("kind"), source.get("extra")),
                                                hit.get("kind") or ""):
            state["stale_candidates"] += 1
        else:
            kept.append({**hit, "title": source.get("title") or ""})
    state["freshness"] = "checked_candidates"
    state["check"] = "source_text"
    return kept, state
