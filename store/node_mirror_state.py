"""노드 검색 미러의 텍스트와 현재 원문을 요청 소유자 안에서 대조한다."""
from store import pkns

EXTRA_CHARS = 300
SPEC = {
    "concept": ("Concept", "concept_id", "one_liner"),
    "record": ("Record", "record_id", None),
    "decision": ("Decision", "decision_id", None),
    "episode": ("Episode", "episode_id", "summary"),
}


def mirror_text(title: str, extra: str) -> str:
    title = " ".join(str(title or "").split())
    extra = " ".join(str(extra or "").split())[:EXTRA_CHARS]
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
        elif hit.get("text") != mirror_text(source.get("title"), source.get("extra")):
            state["stale_candidates"] += 1
        else:
            kept.append({**hit, "title": source.get("title") or ""})
    state["freshness"] = "checked_candidates"
    state["check"] = "source_text"
    return kept, state
