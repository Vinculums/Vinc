"""Record props에 저장하는 실행 패키지의 저작 계약."""
from __future__ import annotations

from . import query

SCHEMA = "vinc.package.v1"
MAX_CLAIMS = 100
MAX_EVIDENCE = 256


def validate_nodes(nodes: list[dict], *, store, owner: str) -> str | None:
    """공개 저작 전에 검증한다. 근거의 내용이나 소유자는 오류에 싣지 않는다."""
    checked: set[str] = set()
    for node in nodes:
        props = node.get("props")
        if not isinstance(props, dict) or "package_schema" not in props:
            continue
        if node.get("type") != "record" or not str(node.get("id") or "").startswith("record:"):
            return "package requires a record carrier and record: id"
        if props["package_schema"] != SCHEMA:
            return "unsupported package_schema"
        for field in ("question", "conclusion", "produced_by", "run_id"):
            value = props.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > 16384:
                return f"package {field} must be a nonempty string of at most 16384 characters"
        claims = props.get("claims")
        if not isinstance(claims, list) or not 1 <= len(claims) <= MAX_CLAIMS:
            return "package claims must contain 1 to 100 claims"
        for claim in claims:
            if not isinstance(claim, dict) or set(claim) != {"claim", "evidence_ids"}:
                return "each package claim requires exactly claim and evidence_ids"
            text = claim["claim"]
            if not isinstance(text, str) or not text.strip() or len(text) > 16384:
                return "package claim must be a nonempty string of at most 16384 characters"
            ids = claim["evidence_ids"]
            if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_EVIDENCE:
                return "package evidence_ids must contain 1 to 256 exact node ids"
            for nid in ids:
                if (not isinstance(nid, str) or not nid.strip() or nid != nid.strip()
                        or len(nid) > 2048 or "\x1f" in nid or nid == node.get("id")):
                    return "package evidence id is invalid or self-referential"
                if nid in checked:
                    continue
                if len(checked) >= MAX_EVIDENCE:
                    return "package batch exceeds 256 distinct evidence ids"
                found = query.node(nid, owner=owner, store=store)
                if not found.get("executed") or not found.get("found"):
                    return "package evidence is unavailable in the selected space"
                checked.add(nid)
    return None
