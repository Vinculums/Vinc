"""Record에 보관하는 belief 저작 계약과 엣지 미러의 읽기 주석. 스키마를 늘리지 않는다.

여기는 검증(`validate`·`prepare`)과 읽기(`load`·`current`·`annotate`)다. 판정을 쓰는 일(이전 구간을
닫고 엣지에 확률을 미러하기)은 `api.belief_write` 가 한다.

근거: [[vinc.plan.probabilistic-knowledge-graph-2026-10-07 §3]]
"""
from __future__ import annotations

from datetime import date
import json
import math

from store import pkns

from . import belief_calibrations

SCHEMA = "vinc.belief.v1"
# 술어는 props에 남고 물리 엣지만 기존 관계를 쓴다.
RELATIONS = {"supports": "RELATED_TO", "contradicts": "RELATED_TO",
             "same_as": "RELATED_TO", "relates_to": "RELATED_TO",
             "prerequisite_of": "PREREQUISITE_OF", "derives_from": "DERIVES_FROM",
             "inherits_from": "INHERITS_FROM"}
ENDS = {"concept": ("Concept", "concept_id"), "decision": ("Decision", "decision_id")}
CALIBRATION_FIELDS = ("calibration_id", "judged_by", "predicate", "language", "decision")


class InvalidBelief(ValueError):
    pass


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and value == value.strip() and "\x1f" not in value


def _probability(value):
    return type(value) in (int, float) and 0 <= value <= 1 and math.isfinite(value)


def _day(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def admitted_index(entries):
    """승인된 보정을 id 로 묶는다. 형식이 틀린 목록은 조용히 줄이지 않고 터뜨린다."""
    index = {}
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != set(CALIBRATION_FIELDS)
                or not all(_text(entry[k]) for k in CALIBRATION_FIELDS)
                or entry["predicate"] not in RELATIONS or not entry["decision"].startswith("decision:")
                or entry["calibration_id"] in index):
            raise ValueError("admitted calibrations need unique ids, a known predicate and a decision id")
        index[entry["calibration_id"]] = entry
    return index


ADMITTED = admitted_index(belief_calibrations.ADMITTED)


def validate(node):
    """자유 props의 부가 키는 보존하고 예약 계약만 검사한다."""
    nid, props = node.get("id"), node.get("props")
    if node.get("type") != "record" or not _text(nid) or not nid.startswith("record:belief-"):
        raise InvalidBelief("belief requires a record carrier and record:belief- id")
    if not isinstance(props, dict) or props.get("belief_schema") != SCHEMA:
        raise InvalidBelief("belief_schema must be vinc.belief.v1")
    subject = props.get("subject")
    if (not isinstance(subject, dict) or set(subject) != {"from", "predicate", "to"}
            or not all(_text(v) for v in subject.values())
            or subject["predicate"] not in RELATIONS):
        raise InvalidBelief("belief subject requires from, a supported edge predicate, and to")
    kinds = [subject[e].split(":", 1)[0] for e in ("from", "to")]
    if any(k not in ENDS for k in kinds) or (RELATIONS[subject["predicate"]] == "RELATED_TO"
                                           and kinds != ["concept", "concept"]):
        raise InvalidBelief("belief subject has no supported physical relation between these endpoints")
    if not _probability(props.get("p")):
        raise InvalidBelief("belief p must be a finite number in [0, 1]")
    for key in ("calibration_id", "judged_by", "independence_group"):
        if not _text(props.get(key)):
            raise InvalidBelief(f"belief {key} must be a nonempty string")
    ids = props.get("evidence_ids")
    if not isinstance(ids, list) or not ids or not all(_text(v) for v in ids):
        raise InvalidBelief("belief evidence_ids must contain exact node or chunk ids")
    revs = props.get("rev_seen")
    if (not isinstance(revs, dict) or set(revs) != {subject["from"], subject["to"]}
            or any(type(v) is not int or v < 0 for v in revs.values())):
        raise InvalidBelief("belief rev_seen requires nonnegative integer revisions for both endpoint ids")
    if props.get("grade") not in ("model", "rule", "user-confirmed"):
        raise InvalidBelief("belief grade must be model, rule, or user-confirmed")
    if props["grade"] == "model":
        # 승인되지 않은 보정의 모델 점수는 확률이 아니다. 읽기도 이 검사를 거치므로 그런 행은
        # 확률 근거로 읽히지 않는다. 채점 모델과 술어까지 승인 항목과 같아야 한다.
        entry = ADMITTED.get(props["calibration_id"])
        if (not entry or entry["judged_by"] != props["judged_by"]
                or entry["predicate"] != subject["predicate"]):
            raise InvalidBelief("a model belief requires an admitted calibration for its judge and predicate")
    distribution = props.get("distribution")
    if distribution is not None:
        if (not isinstance(distribution, list) or not distribution
                or any(not isinstance(v, dict) or set(v) != {"option", "p"}
                       or not _text(v["option"]) or not _probability(v["p"]) for v in distribution)
                or len({v["option"] for v in distribution}) != len(distribution)
                or not math.isclose(sum(v["p"] for v in distribution), 1.0, abs_tol=1e-9)):
            raise InvalidBelief("belief distribution must have unique options and probabilities summing to one")
    start, end = node.get("valid_from"), node.get("valid_until", "")
    if not _day(start) or (end != "" and (not _day(end) or end < start)):
        raise InvalidBelief("belief validity requires YYYY-MM-DD and an ordered interval")


def belief_subject(node):
    s = node["props"]["subject"]
    return s["from"], s["predicate"], s["to"]


def edge_key(src, rel, dst):
    if rel == "RELATED_TO":
        src, dst = sorted((src, dst))
    return src, rel, dst


def belief_key(node):
    src, pred, dst = belief_subject(node)
    return edge_key(src, RELATIONS[pred], dst)


def load(store, owner, *, attached=False):
    """소유자 안의 예약 Record만 읽는다. 복원 경로의 불완전한 행은 확률로 읽지 않는다."""
    rows = store.run("MATCH (n:Record) WHERE n.owner_id = $owner "
                     "AND n.record_id STARTS WITH $prefix "
                     "RETURN n.record_id AS id, n.props AS props, n.valid_from AS valid_from, "
                     "n.valid_until AS valid_until, n.created AS created",
                     {"owner": owner, "prefix": pkns.key(owner, "record:belief-")})
    out = []
    for row in rows:
        row = {**row, "id": pkns.strip(row["id"]), "type": "record",
               "valid_until": row.get("valid_until") or ""}
        try:
            row["props"] = json.loads(row["props"] or "null")
            validate(row)
        except (InvalidBelief, TypeError, ValueError):
            continue
        out.append(row)
    if attached and out:
        arms = [f"MATCH (r:Record)-[:ATTACHES_TO]->(n:{label}) "
                "WHERE r.owner_id = $owner AND n.owner_id = $owner AND r.record_id IN $ids "
                f"RETURN r.record_id AS id, n.{pk} AS endpoint" for label, pk in ENDS.values()]
        links = store.run(" UNION ALL ".join(arms),
                          {"owner": owner, "ids": [pkns.key(owner, n["id"]) for n in out]})
        endpoints = {}
        for link in links:
            endpoints.setdefault(pkns.strip(link["id"]), set()).add(pkns.strip(link["endpoint"]))
        out = [r for r in out if {belief_subject(r)[0], belief_subject(r)[2]} <= endpoints.get(r["id"], set())]
    return out


def current(rows, today=None):
    today = today or date.today().isoformat()
    active = [r for r in rows if r["valid_from"] <= today
              and (not r["valid_until"] or today < r["valid_until"])]
    return sorted(active, key=lambda r: (r["valid_from"], r.get("created") or "", r["id"]))


def prepare(nodes, edges, *, store, owner):
    """첫 쓰기 전에 거절한다. 같은 id 재시도는 닫힌 유효구간을 다시 열지 않는다."""
    from . import query
    candidates = [n for n in nodes if str(n.get("id") or "").startswith("record:belief-")
                  or isinstance(n.get("props"), dict) and "belief_schema" in n["props"]]
    if not candidates:
        return nodes, edges, []
    by_id = {n.get("id"): n for n in nodes}
    if len(by_id) != len(nodes):
        raise InvalidBelief("a belief fragment cannot repeat a node id")
    stored = load(store, owner)
    adjusted = {n.get("id"): dict(n) for n in nodes}
    added, subjects = [], set()
    for node in candidates:
        nid = node.get("id")
        if not _text(nid):
            raise InvalidBelief("belief id must be an exact record id")
        previous = query.node(nid, owner=owner, store=store)
        if not previous.get("executed"):
            raise InvalidBelief("belief record is unavailable in the selected space")
        old = previous.get("node")
        effective = {**(old or {}), **node}
        # null은 일반 저작 경로에서 '보존'이므로 검사에도 같은 의미를 준다.
        for key in ("props", "valid_from", "valid_until"):
            if node.get(key) is None and old:
                effective[key] = old.get(key)
        validate(effective)
        if old:
            if effective["props"] != old["props"] or effective["valid_from"] != old["valid_from"]:
                raise InvalidBelief("belief revisions require a new record id; props and valid_from are immutable")
            if effective.get("valid_until") != (old.get("valid_until") or ""):
                if effective.get("valid_until") != "":
                    raise InvalidBelief("belief validity is closed by a successor record")
                adjusted[nid].pop("valid_until", None)
            continue
        if effective.get("valid_until", ""):
            raise InvalidBelief("a new authored belief must have an open validity interval")
        if "created" in node:
            raise InvalidBelief("belief creation order is assigned by the server")
        if effective["valid_from"] > date.today().isoformat():
            raise InvalidBelief("a new authored belief cannot start in the future")
        subject = belief_subject(effective)
        if subject in subjects:
            raise InvalidBelief("send one new judgment per subject in a fragment")
        subjects.add(subject)
        prior = [r for r in stored if belief_subject(r) == subject]
        if any(r["valid_from"] > effective["valid_from"] for r in prior):
            raise InvalidBelief("a successor belief cannot precede its existing history")
        if any(r["props"]["grade"] == "user-confirmed" and not r["valid_until"] for r in prior) \
                and effective["props"]["grade"] != "user-confirmed":
            raise InvalidBelief("a model or rule cannot supersede a human confirmation")
        src, predicate, dst = subject
        for end in {src, dst}:
            endpoint = by_id.get(end)
            if not endpoint or endpoint.get("type") not in ENDS or not end.startswith(endpoint["type"] + ":"):
                raise InvalidBelief("include both Concept or Decision endpoints in the belief fragment")
            if not any(e.get("from") == nid and e.get("to") == end
                       and e.get("type") in ("relates_to", "prerequisite_of", "derives_from", "inherits_from")
                       for e in edges):
                raise InvalidBelief("include a Record attachment to each belief endpoint")
        if RELATIONS[predicate] == "RELATED_TO" and any(by_id[e]["type"] != "concept" for e in (src, dst)):
            raise InvalidBelief("this predicate requires Concept endpoints until its physical relation exists")
        added.append(effective)
    # 노드 스킵 뒤 belief만 남는 부분 성공을 막는다. 값/소유자 내용은 오류에 싣지 않는다.
    required = {n["id"] for n in candidates}
    for n in added:
        required.update((belief_subject(n)[0], belief_subject(n)[2]))
    for nid in required:
        n = adjusted[nid]
        typ = n.get("type")
        label, pk = ("Record", "record_id") if typ == "record" else ENDS.get(typ, (None, None))
        if not label or pkns.has_sep(n):
            raise InvalidBelief("invalid belief carrier or endpoint")
        if (isinstance(n.get("title"), str) and len(n["title"]) > 16384
                or isinstance(n.get("tags"), list) and (len(n["tags"]) > 256
                    or any(isinstance(t, str) and len(t) > 2048 for t in n["tags"]))
                or len(json.dumps(n.get("props"), ensure_ascii=False)) > 512 * 1024):
            raise InvalidBelief("belief carrier or endpoint exceeds authored field limits")
        foreign = store.run(f"MATCH (n:{label}) WHERE n.{pk} = $id "
                            "AND (n.owner_id IS NULL OR n.owner_id <> $owner) RETURN count(n) AS c",
                            {"id": pkns.key(owner, nid), "owner": owner})
        if foreign and foreign[0]["c"]:
            raise InvalidBelief("belief carrier or endpoint is unavailable in the selected space")
    # 완전 재전송은 노드 저장 뒤 중단된 엣지 쓰기도 회복해야 한다.
    complete = [n for n in candidates if isinstance(n.get("props"), dict)
                and all(any(e.get("from") == n["id"] and e.get("to") == end for e in edges)
                        for end in (belief_subject(n)[0], belief_subject(n)[2]))]
    extra = [{"from": belief_subject(n)[0], "to": belief_subject(n)[2],
              "type": "relates_to" if RELATIONS[belief_subject(n)[1]] == "RELATED_TO"
              else belief_subject(n)[1]} for n in complete]
    return list(adjusted.values()), [*edges, *extra], added


def annotate(edges, *, store, owner):
    """confidence 기본값과 판정 p=0을 구별한다. 확률 정본은 언제나 Record다."""
    if not any(e["type"] in RELATIONS.values() for e in edges):
        return
    latest = {belief_key(r): r for r in current(load(store, owner, attached=True))}
    for edge in edges:
        if edge["type"] not in set(RELATIONS.values()):
            continue
        belief = latest.get(edge_key(edge["from"], edge["type"], edge["to"]))
        edge["belief_status"] = "judged" if belief else "unjudged"
        if belief:
            edge.update(belief_id=belief["id"], p=belief["props"]["p"])
            edge["predicate"] = belief["props"]["subject"]["predicate"]
