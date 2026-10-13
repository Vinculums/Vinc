"""출처와 검토 이력을 검증한 쌍만 학습 형식으로 내보낸다. 모델은 호출하지 않는다."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from .build_train import rows_for_pair
from .pins import PREDICATES, questions_hash, render_state

SCHEMA = "vinc.laya.dataset.v1"
ROLES = {"train", "dev", "calibration_fit", "calibration_risk", "sealed_test", "diagnostic"}


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def digest(value):
    return hashlib.sha256(value).hexdigest()


def fingerprint(value):
    return digest(encoded(value))


def normalized(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def qualified(value):
    return nonempty(value) and bool(re.fullmatch(r"[^\s:]+:[^\s]+", value))


def stamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "timestamp must include timezone")
    return result


def index(rows, key):
    result = {}
    for row in rows:
        name = row.get(key)
        require(qualified(name), f"qualified {key} required")
        require(name not in result, f"duplicate {key}: {name}")
        result[name] = row
    return result


def safe_file(root, name):
    require(nonempty(name) and not Path(name).is_absolute(), "relative snapshot path required")
    result = (root / name).resolve()
    require(result.is_relative_to(root.resolve()), "snapshot escapes bundle")
    return result


def labels_valid(labels):
    return (isinstance(labels, dict) and set(labels) == set(PREDICATES)
            and all(v is None or (type(v) is int and v in (0, 1)) for v in labels.values()))


def lexical(text):
    return set(re.findall(r"[a-z0-9]+|[가-힣]{1,2}", normalized(text)))


def validate_append_only(previous, current):
    """이전 동결본을 제시한 수정은 기존 쌍과 이력을 덮어쓰지 못한다."""
    require(previous["dataset_id"] == current["dataset_id"], "dataset history identity mismatch")
    require(current.get("previous_bundle_sha256") == fingerprint(previous), "previous bundle hash mismatch")
    for field in ("annotations", "exposures", "pairs", "spans", "assignments"):
        require(current[field][:len(previous[field])] == previous[field], f"history must append, not replace: {field}")
    require(len(current["sources"]) >= len(previous["sources"]), "source history cannot shrink")
    for old, new in zip(previous["sources"], current["sources"]):
        require({k: v for k, v in old.items() if k != "eligibility"} == {k: v for k, v in new.items() if k != "eligibility"}, "source payload changed without a new identity")
    # 권한 상태는 새 동결본에서 바뀔 수 있다. 이전 동결본과 결정 인용이 변경 이력이다.


def validate(bundle, root):
    """구조 오류는 거절하고 미승인 자료는 검사 가능한 제외 사유로 돌려준다."""
    require(bundle.get("schema") == SCHEMA, "unsupported dataset schema")
    require(qualified(bundle.get("dataset_id")), "qualified dataset_id required")
    require(bundle.get("questions_hash") == questions_hash(), "question contract mismatch")
    require(bundle.get("render_version") == "laya-state-v1", "render version mismatch")
    sources = index(bundle["sources"], "source_id")
    spans = index(bundle["spans"], "span_id")
    pairs = index(bundle["pairs"], "pair_id")
    assignments = index(bundle["assignments"], "source_id")
    events = index(bundle["annotations"], "event_id")
    registry = bundle["exposure_registry"]
    registry_bytes = safe_file(root, registry["path"]).read_bytes()
    require(digest(registry_bytes) == registry["sha256"], "exposure registry hash mismatch")
    historical = json.loads(registry_bytes)
    require(historical.get("schema") == "vinc.laya.exposures.v1" and historical.get("events"), "nonempty exposure registry required")
    exposures = index(historical["events"] + bundle["exposures"], "event_id")
    require(sources and pairs, "empty dataset")
    require(set(assignments) == set(sources), "every source needs exactly one split assignment")
    contents, groups = {}, defaultdict(set)
    for sid, source in sources.items():
        for field in ("owner_scope", "origin_uri", "revision", "license_id", "redaction_version"):
            require(nonempty(source.get(field)), f"source missing {field}: {sid}")
        require(source.get("origin_kind") in {"public", "owner_opt_in", "synthetic", "observed"}, "origin kind required")
        require(qualified(source.get("document_id")), "qualified document_id required")
        stamp(source["captured_at"])
        data = safe_file(root, source["snapshot_path"]).read_bytes()
        require(digest(data) == source["sha256"], f"snapshot hash mismatch: {sid}")
        contents[sid] = data.decode("utf-8")
        for permission in ("training", "export"):
            grant = source["eligibility"][permission]
            require(grant.get("status") in {"approved", "pending", "denied"}, "invalid eligibility")
            if grant["status"] == "approved":
                require(nonempty(grant.get("decision_ref")) and nonempty(grant.get("approved_by")), "approval needs decision and actor")
                require(source["license_id"].casefold() not in {"unknown", "unassigned", "pending"}, "approved source needs a resolved license declaration")
        assignment = assignments[sid]
        require(assignment["role"] in ROLES, "invalid split role")
        stamp(assignment["assigned_at"])
        require(stamp(source["captured_at"]) <= stamp(assignment["assigned_at"]), "source snapshot must precede split assignment")
        require(assignment.get("groups") and all(qualified(x) for x in assignment["groups"]), "lineage groups required")
        for group in ["source:" + sid, "document:" + source["document_id"], "snapshot:" + source["sha256"], *assignment["groups"]]:
            groups[group].add(assignment["role"])
    require(all(len(v) == 1 for v in groups.values()), "source/document/lineage group crosses split roles")
    for sid, span in spans.items():
        require(span["source_id"] in sources, f"span source missing: {sid}")
        source = sources[span["source_id"]]
        require(span["source_sha256"] == source["sha256"], "span source revision mismatch")
        text = contents[span["source_id"]]
        start, end = span["start"], span["end"]
        require(type(start) is int and type(end) is int and 0 <= start < end <= len(text), "invalid Unicode codepoint span")
        require(digest(text[start:end].encode("utf-8")) == span["excerpt_sha256"], "excerpt hash mismatch")
    pair_sources, pair_roles = {}, {}
    families, texts, endpoints = defaultdict(set), defaultdict(set), defaultdict(set)
    for pid, pair in pairs.items():
        require(pid.startswith(bundle["dataset_id"] + ":"), "pair outside dataset namespace")
        require(qualified(pair["family_id"]), "qualified family_id required")
        for field in ("domain", "mechanism", "claim_time"):
            require(nonempty(pair.get(field)), f"pair missing {field}")
        authored = stamp(pair["authored_at"])
        refs = []
        for side in ("a", "b"):
            part = pair[side]
            require(nonempty(part.get("text")) and part.get("language") in {"en", "ko"}, "endpoint text and language required")
            require(qualified(part.get("endpoint_id")) and nonempty(part.get("revision")), "endpoint identity/revision required")
            require(part.get("mode") in {"quotation", "authored"}, "endpoint origin required")
            require(part.get("evidence") and all(x in spans for x in part["evidence"]), "endpoint evidence missing")
            refs.extend(part["evidence"])
            if part["mode"] == "quotation":
                require(len(part["evidence"]) == 1, "quotation must name one exact span")
                span = spans[part["evidence"][0]]
                require(part["text"] == contents[span["source_id"]][span["start"]:span["end"]], "quotation differs from source")
        context = pair.get("context")
        require(context is None or (isinstance(context, str) and len(context.strip()) <= 400), "context would be truncated")
        require(not context or pair.get("context_evidence"), "context evidence missing")
        require(all(x in spans for x in pair.get("context_evidence", [])), "context span missing")
        refs.extend(pair.get("context_evidence", []))
        sids = {spans[r]["source_id"] for r in refs}
        roles = {assignments[s]["role"] for s in sids}
        require(len(roles) == 1, "pair sources cross split roles")
        role = next(iter(roles))
        require(all(stamp(assignments[s]["assigned_at"]) <= authored for s in sids), "split assigned after authoring")
        require(pair["render_sha256"] == digest(render_state(pair["a"]["text"], pair["b"]["text"], context).encode()), "render fingerprint mismatch")
        pair_sources[pid], pair_roles[pid] = sids, role
        families[pair["family_id"]].add(role)
        for side in ("a", "b"):
            texts[normalized(pair[side]["text"])].add(role)
            endpoints[pair[side]["endpoint_id"]].add(role)
        if context:
            texts[normalized(context)].add(role)
        for parent in pair.get("parents", []):
            require(parent in pairs and pairs[parent]["family_id"] == pair["family_id"] and parent != pid, "variant must stay in its existing family")
            require(stamp(pairs[parent]["authored_at"]) < authored, "variant parent must precede child")
    require(all(len(v) == 1 for v in families.values()), "family crosses split roles")
    require(all(len(v) == 1 for v in endpoints.values()), "endpoint crosses split roles")
    require(all(len(v) == 1 for v in texts.values()), "normalized text crosses split roles")

    # 현재 쌍의 해시를 담은 이력만 유효하다. 자기 검토는 독립 검토로 세지 않는다.
    history = defaultdict(list)
    for eid, event in events.items():
        pid = event["pair_id"]
        require(pid in pairs and event["pair_sha256"] == fingerprint(pairs[pid]), "annotation targets stale or absent pair")
        require(labels_valid(event.get("labels")), "labels must be three integer 0/1 or null values")
        require(nonempty(event.get("actor")) and event.get("actor_kind") in {"ai", "human", "rule"}, "annotation identity required")
        require(event.get("stage") in {"author", "review", "adjudication"}, "invalid annotation stage")
        require(event.get("rationale") and all(nonempty(event["rationale"].get(p)) for p in PREDICATES), "rationale required for each predicate")
        require(event.get("evidence_strength") in {"direct", "inference", "ambiguous"}, "evidence strength required")
        require(stamp(event["at"]) >= stamp(pairs[pid]["authored_at"]), "annotation precedes pair")
        require(event.get("rubric_id") == bundle["rubric"]["id"], "annotation rubric mismatch")
        history[pid].append(event)
    opened_pairs = set()
    for event in exposures.values():
        require(event.get("kind") in {"measurement_opened", "development_used", "training_used"}, "invalid exposure kind")
        require(nonempty(event.get("reference")), "exposure reference required")
        stamp(event["at"])
        require(any(event.get(k) for k in ("source_hashes", "family_ids", "text_hashes", "source_ids", "document_ids")), "empty exposure event")
        # 이미 다른 용도로 열어 본 자료는 봉인 측정으로 되돌릴 수 없다.
        for pid, pair in pairs.items():
            hits = (bool({sources[s]["sha256"] for s in pair_sources[pid]} & set(event.get("source_hashes", [])))
                    or pair["family_id"] in event.get("family_ids", [])
                    or any(digest(normalized(t).encode()) in event.get("text_hashes", []) for t in [pair["a"]["text"], pair["b"]["text"], pair.get("context") or ""])
                    or bool((pair_sources[pid] | {pair[k]["endpoint_id"] for k in ("a", "b")}) & set(event.get("source_ids", [])))
                    or bool({sources[s]["document_id"] for s in pair_sources[pid]} & set(event.get("document_ids", []))))
            require(not (hits and pair_roles[pid] == "sealed_test"), "exposed data cannot be sealed test")
            if hits and event["kind"] == "measurement_opened":
                opened_pairs.add(pid)
    for pid, pair in pairs.items():
        require(not (pid in opened_pairs and pair_roles[pid] != "diagnostic"), "opened measurement is diagnostic only")

    # 근접 후보는 사람이 확인한다. 낮은 어휘 일치율은 언어 간 독립성의 보증이 아니다.
    near = []
    entries = [(pid, lexical(p[k]["text"])) for pid, p in pairs.items() for k in ("a", "b")]
    for i, (pid, a) in enumerate(entries):
        for qid, b in entries[i + 1:]:
            if pair_roles[pid] != pair_roles[qid] and a and b and len(a & b) / len(a | b) >= 0.8:
                near.append(sorted((pid, qid)))
    near = sorted({tuple(x) for x in near})
    reviews = {}
    for item in bundle.get("near_duplicate_reviews", []):
        key = tuple(sorted(item["pair_ids"]))
        require(key in near and key not in reviews, "unknown or duplicate near match review")
        require(item.get("actor_kind") == "human" and nonempty(item.get("actor")) and nonempty(item.get("rationale")), "near match needs human review")
        require(item.get("resolution") == "distinct_evidence", "linked near matches must share split")
        require(item["pair_hashes"] == {p: fingerprint(pairs[p]) for p in key}, "stale near match review")
        require(all(stamp(item["at"]) >= stamp(pairs[p]["authored_at"]) for p in key), "near review precedes pair")
        reviews[key] = item
    unresolved_near = [list(x) for x in near if x not in reviews]

    accepted, excluded = {}, {}
    rubric = bundle["rubric"]
    require(rubric.get("status") in {"proposed", "approved"} and qualified(rubric.get("id")), "invalid rubric")
    if rubric["status"] == "approved":
        require(nonempty(rubric.get("decision_ref")) and nonempty(rubric.get("approved_by")), "rubric approval evidence required")
    for pid, pair in pairs.items():
        reason = None
        if pair_roles[pid] != "train":
            reason = "not_training_split"
        elif rubric["status"] != "approved":
            reason = "rubric_unapproved"
        elif any(sources[s]["eligibility"][g]["status"] != "approved" for s in pair_sources[pid] for g in ("training", "export")):
            reason = "source_unapproved"
        hs = sorted(history[pid], key=lambda e: (stamp(e["at"]), e["event_id"]))
        authors = [e for e in hs if e["stage"] == "author"]
        require(len(authors) <= 1, "multiple authors require a new pair revision")
        require(not hs or (authors and hs[0] == authors[0]), "author annotation must precede review")
        final = None
        previous = []
        for event in hs:
            if event["stage"] != "author":
                require(set(event.get("reviews", [])) == {e["event_id"] for e in previous}, "review must reference all prior annotations")
                require(authors and event["actor"] != authors[0]["actor"], "self review is not independent")
                if event["actor_kind"] == "human":
                    agree = all(e["labels"] == event["labels"] for e in previous)
                    if agree or event["stage"] == "adjudication":
                        final = event
                    else:
                        final = None
                else:
                    final = None
            previous.append(event)
        if reason is None:
            if final is None:
                reason = "human_review_pending"
            elif any(v is None for v in final["labels"].values()):
                reason = "unknown_labels"
            elif final["evidence_strength"] == "ambiguous":
                reason = "ambiguous_evidence"
        if reason:
            excluded[pid] = reason
        else:
            accepted[pid] = final
    result = {"schema": SCHEMA, "dataset_id": bundle["dataset_id"], "bundle_sha256": fingerprint(bundle),
              "pairs": len(pairs), "sources": len(sources), "families": len(families),
              "language_cells": dict(Counter(p["a"]["language"] + "_" + p["b"]["language"] for p in pairs.values())),
              "roles": dict(Counter(pair_roles.values())), "exportable_pairs": len(accepted),
              "excluded": excluded, "unresolved_near_duplicates": unresolved_near,
              "lexical_screen_proves_cross_language_independence": False,
              "model_calls": 0, "source_permissions_are_declared_not_authenticated": True}
    result["ready_for_export"] = bool(accepted) and not unresolved_near
    return result, accepted


def export_training(bundle, root, out):
    report, accepted = validate(bundle, root)
    require(not report["unresolved_near_duplicates"], "unreviewed cross split near duplicates")
    require(accepted, "no reviewed eligible training pairs")
    rows = []
    for pair in bundle["pairs"]:
        event = accepted.get(pair["pair_id"])
        if event is None:
            continue
        a, b = pair["a"], pair["b"]
        lang = a["language"] if a["language"] == b["language"] else "mixed"
        for row in rows_for_pair(a["text"], b["text"], pair.get("context"), event["labels"], bundle["dataset_id"], lang):
            row["meta"].update(pair_id=pair["pair_id"], family_id=pair["family_id"], annotation_id=event["event_id"], pair_sha256=fingerprint(pair))
            rows.append(row)
    data = b"".join(json.dumps(r, ensure_ascii=False, sort_keys=True, allow_nan=False).encode() + b"\n" for r in rows)
    # 기존 결과를 덮어쓰지 않는다. 완료 manifest가 없으면 미완료 내보내기다.
    out.mkdir(parents=True, exist_ok=False)
    (out / "training.jsonl").write_bytes(data)
    report.update(training_rows=len(rows), training_items=sum(len(r["gold"]) for r in rows), training_sha256=digest(data))
    (out / "manifest.json").write_bytes(encoded(report))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--out", type=Path, help="explicitly export reviewed training rows to a new directory")
    parser.add_argument("--previous", type=Path, help="previous frozen bundle when appending review events")
    args = parser.parse_args(argv)
    try:
        bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
        require(bool(args.previous) == bool(bundle.get("previous_bundle_sha256")), "previous bundle and its hash must be supplied together")
        if args.previous:
            validate_append_only(json.loads(args.previous.read_text(encoding="utf-8")), bundle)
        report = export_training(bundle, args.bundle.parent, args.out) if args.out else validate(bundle, args.bundle.parent)[0]
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f"refused: {exc}\n")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
