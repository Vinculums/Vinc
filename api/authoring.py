"""Bounded authoring diagnostics; never change claims, ranking or replication."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
import json
import re

from store import canon, pkns
from store.search_temporal import instant

MAX_CLAIMS = 16
MAX_CANDIDATES = 128
MAX_ADVISORIES = 32
MAX_ADVISORY_CHARS = 6000
MAX_VALUE_CHARS = 4096
FIELD_COVERAGE = {
    "concept": {"keyword": ["title", "one_liner", "props", "id"],
                "semantic": ["title", "one_liner"]},
    "record": {"keyword": ["title", "props", "id"], "semantic": ["title", "props body[:300]"]},
    "decision": {"keyword": ["title", "props", "id"], "semantic": ["title", "props body[:300]"]},
    "episode": {"keyword": ["title", "id"], "semantic": ["title", "summary[:300]"]},
}


def node_retrieval():
    return {"fields": {kind: {arm: list(fields) for arm, fields in arms.items()}
                       for kind, arms in FIELD_COVERAGE.items()}, "node_vectors": "not_checked",
            "node_refresh": "release_boundary", "query_index": "not_checked"}


def document_retrieval(needs_embed):
    return {"keyword_text": "stored", "document_vectors":
            "pending" if needs_embed else "not_checked" if needs_embed is None else "ready",
            "query_index": "not_checked"}


def _canonical(value):
    encoded = canon.canonical_json(value)
    if len(encoded) > MAX_VALUE_CHARS:
        raise ValueError("claim value exceeds review budget")
    return encoded


def _identity(node):
    props = node.get("props")
    if not isinstance(props, dict) or "value" not in props or "scope" not in props:
        return None
    if not all(isinstance(props.get(k), str) and props[k].strip()
               for k in ("entity", "predicate")):
        return None
    if props["scope"] is None:
        return None
    return (_canonical(props["entity"]), _canonical(props["predicate"]),
            _canonical(props["scope"]))


def _value(props):
    value = props["value"]
    if props.get("value_type") == "number":
        if type(value) not in (int, float):
            raise ValueError("unsupported number")
        _canonical(value)
        return ("number", Decimal(str(value)))
    # 복잡한 버전 표기를 추측하지 않는다. 명시된 숫자 릴리스만 정규화한다.
    if props.get("value_type") == "version":
        if not isinstance(value, str) or len(value) > 128 or not re.fullmatch(
                r"[0-9]+(?:\.[0-9]+){1,3}", value):
            raise ValueError("unsupported version")
        parts = [int(v) for v in value.split(".")]
        while len(parts) > 1 and parts[-1] == 0:
            parts.pop()
        return ("version", tuple(parts))
    return ("json", _canonical(value))


def _interval(node):
    props = node.get("props") or {}
    dates = props if node.get("kind") == "decision" else node
    start, end = dates.get("valid_from") or "", dates.get("valid_until") or ""
    parsed = instant(start), instant(end)
    invalid = any(value and parsed[i] is None for i, value in enumerate((start, end)))
    invalid |= bool(parsed[0] and parsed[1] and parsed[0] >= parsed[1])
    return start, end, *parsed, invalid


def compare(new, old, *, now=None):
    """Return a review candidate, never a truth or automatic retirement verdict."""
    if new.get("kind") != old.get("kind") or new.get("id") == old.get("id"):
        return None
    try:
        key = _identity(new)
        if not key or key != _identity(old):
            return None
        if new["props"].get("value_type") != old["props"].get("value_type"):
            return None
        if _value(new["props"]) == _value(old["props"]):
            return None
        nf, nu, ns, ne, ni = _interval(new)
        of, ou, os, oe, oi = _interval(old)
    except (ValueError, TypeError, OverflowError):
        return None
    now = now or dt.datetime.now(dt.timezone.utc)
    if ni or oi or (ns and ns > now) or (ne and ne <= now) or (oe and oe <= now):
        return None
    if ns and os and ns <= os:
        return None
    base = {"node_id": new["id"], "previous_id": old["id"],
            "automatic": False}
    if not nf or not of or not all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v)
                                   for v in (nf, of)):
        return {**base, "code": "claim_dates_need_review",
                "message": "Comparable values differ but dates are missing or imprecise; "
                           "supply dates before deciding whether one replaces the other."}
    # 과거 시점에 이미 닫힌 구간은 겹치는 주장도, 대체 후보도 아니다.
    if oe and ns >= oe:
        return None
    return {**base, "code": "claim_replacement_review",
            "suggested_valid_until": nf,
            "message": "Comparable claim values differ with overlapping validity. Review "
                       "whether to set the older valid_until and add an evidenced "
                       "new-to-old supersedes edge; nothing was retired automatically."}


def review(store, stored, *, owner, now=None, compare_claims=False):
    """Only use persisted authored nodes; bounded, scoped, and best-effort."""
    claims = []
    incomplete = False
    for node in stored:
        if node.get("kind") not in ("record", "decision"):
            continue
        try:
            if _identity(node):
                _value(node["props"])
                claims.append(node)
        except (ValueError, TypeError, OverflowError):
            incomplete = True
    if not claims and not incomplete:
        return None
    result = {"advisories": [], "advisory_review": {
        "state": "not_requested" if not compare_claims else
                 "limited" if incomplete or len(claims) > MAX_CLAIMS else "complete",
        "claims_reviewed": 0, "candidate_limit": MAX_CANDIDATES, "candidate_scope": "per_kind"}}
    now = now or dt.datetime.now(dt.timezone.utc)
    seen = set()
    used_chars = 0

    def limited():
        if result["advisory_review"]["state"] != "unavailable":
            result["advisory_review"]["state"] = "limited"

    def append(advisory):
        nonlocal used_chars
        size = len(json.dumps(advisory, ensure_ascii=False))
        if len(result["advisories"]) >= MAX_ADVISORIES or used_chars + size > MAX_ADVISORY_CHARS:
            limited()
            return False
        used_chars += size
        result["advisories"].append(advisory)
        return True

    if not compare_claims:
        for new in claims[:MAX_CLAIMS]:
            result["advisory_review"]["claims_reviewed"] += 1
            nf, _, _, _, invalid = _interval(new)
            if not nf or invalid:
                append({"code": "claim_valid_from_missing" if not nf else "claim_interval_invalid",
                        "node_id": new["id"], "automatic": False,
                        "message": "Supply valid_from and a valid interval; recording time is not validity."})
        if incomplete or len(claims) > MAX_CLAIMS:
            result["advisory_review"]["date_review_limited"] = True
        return result

    candidates = {}
    for kind in sorted({node["kind"] for node in claims[:MAX_CLAIMS]}):
        group = [node for node in claims[:MAX_CLAIMS] if node["kind"] == kind]
        label, pk = ("Record", "record_id") if kind == "record" else ("Decision", "decision_id")
        params = {"owner": owner}
        keys = sorted({(_canonical(node["props"]["entity"]), _canonical(node["props"]["predicate"]))
                       for node in group})
        clauses = []
        for i, (entity, predicate) in enumerate(keys):
            params.update({f"e{i}": entity, f"p{i}": predicate,
                           f"ea{i}": json.dumps(json.loads(entity), ensure_ascii=True),
                           f"pa{i}": json.dumps(json.loads(predicate), ensure_ascii=True)})
            clauses.append(f"((n.props CONTAINS $e{i} OR n.props CONTAINS $ea{i}) "
                           f"AND (n.props CONTAINS $p{i} OR n.props CONTAINS $pa{i}))")
        try:
            # 라벨마다 한 번만 조회한다. 잘린 창은 완료라고 답하지 않는다.
            # 근거: [[vinc.plan.authoring-contract-and-write-lifecycle-2026-10-13]]
            rows = store.run(
                f"MATCH (n:{label}) WHERE n.owner_id = $owner AND (" + " OR ".join(clauses) + ") "
                f"RETURN n.{pk} AS id, n.props AS props, "
                + ("n.valid_from AS valid_from, n.valid_until AS valid_until " if label == "Record"
                   else "'' AS valid_from, '' AS valid_until ")
                + f"LIMIT {MAX_CANDIDATES + 1}", params)
            if len(rows) > MAX_CANDIDATES:
                limited()
                continue
            candidates[kind] = [{**row, "id": pkns.strip(row["id"]), "kind": kind,
                                 "props": json.loads(row["props"] or "{}")}
                                for row in sorted(rows, key=lambda row: row["id"])]
        except Exception:
            # 검토 실패는 이미 끝난 저장을 실패로 바꾸거나 민감한 DB 오류를 내보내지 않는다.
            result["advisory_review"]["state"] = "unavailable"
    for new in claims[:MAX_CLAIMS]:
        result["advisory_review"]["claims_reviewed"] += 1
        nf, _, _, _, invalid = _interval(new)
        if not nf or invalid:
            append({
                "code": "claim_valid_from_missing" if not nf else "claim_interval_invalid",
                "node_id": new["id"], "automatic": False,
                "message": "Supply a valid_from and a valid interval for this structured "
                           "claim; recording time alone does not establish when it holds."})
        for old in candidates.get(new["kind"], []):
            advisory = compare(new, old, now=now)
            pair = (new["id"], old["id"])
            if advisory and pair not in seen:
                if not append(advisory):
                    break
                seen.add(pair)
    return result
