"""동결 후보의 출처 대조 결과를 남긴다. 술어 라벨이나 학습 승인을 만들지 않는다."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import data_contracts as D, pilot_verify as V

SCHEMA = "vinc.laya.candidate-audit.v1"


def fact_receipt(raw, source, recipe):
    """저장된 단일 사건의 증거 문장만 대조한다. 대조 문장의 정답은 추론하지 않는다."""
    D.require(raw.get("type") == "FeatureCollection" and len(raw["features"]) == 1, "one catalog event required")
    event = raw["features"][0]
    props, coords = event["properties"], event["geometry"]["coordinates"]
    D.require(str(props["updated"]) == source["revision"], "catalog revision mismatch")
    D.require(source["document_id"] == "document:laya-p1-" + event["id"], "catalog event identity mismatch")
    values = [("magnitude", "규모", format(props["mag"], "g"), "/properties/mag", "", ""),
              ("depth", "깊이", format(coords[2], "g"), "/geometry/coordinates/2", " km", "km"),
              ("latitude", "위도", format(coords[1], "g"), "/geometry/coordinates/1", " degrees", "도"),
              ("longitude", "경도", format(coords[0], "g"), "/geometry/coordinates/0", " degrees", "도"),
              ("UTC date", "UTC 날짜", datetime.fromtimestamp(props["time"] / 1000, timezone.utc).date().isoformat(), "/properties/time", "", "")]
    matches = [(en, ko, value, pointer, unit_ko) for en, ko, value, pointer, unit_en, unit_ko in values
               if recipe["evidence"]["en"] == f"The frozen USGS entry {event['id']} reports {en} {value}{unit_en}."]
    D.require(len(matches) == 1, "catalog evidence differs from frozen value")
    en, ko, value, pointer, unit_ko = matches[0]
    expected_ko = f"고정한 USGS 항목 {event['id']}에 기록된 값은 {ko} {value}{unit_ko}다."
    D.require(recipe["evidence"]["ko"] == expected_ko, "Korean catalog evidence differs from frozen value")
    D.require(all(event["id"] in recipe["context"][lang] and source["revision"] in recipe["context"][lang] for lang in ("en", "ko")), "catalog context identity mismatch")
    return {"kind": "catalog_literal", "field_pointer": "/features/0" + pointer,
            "event_id": event["id"], "value_text": value, "language_literals_checked": 2,
            "independent_geophysical_verification": False}


def observation_receipt(raw, recipe):
    """저장된 출력의 인용과 실행 범위만 확인한다. 스냅샷의 코드를 실행하지 않는다."""
    D.require(raw["origin"] == "locally_observed" and type(raw["returncode"]) is int and raw["returncode"] == 0 and raw["stderr"] == "", "successful recorded observation required")
    D.require(D.stamp(raw["started_at"]) <= D.stamp(raw["completed_at"]), "observation time order mismatch")
    stdout = raw["stdout"].strip()
    case = raw["case_id"]
    D.require(recipe["evidence"] == {"en": f"Local case {case} exited with code 0 and printed {stdout!r}.",
                                    "ko": f"로컬 시험 {case}의 종료 코드는 0이었고 출력은 {stdout!r}였다."}, "observation evidence differs from saved stdout")
    D.require(all(raw["code"] in recipe["context"][lang] for lang in ("en", "ko")), "observation input missing from context")
    return {"kind": "saved_observation_literal", "case_id": case, "stdout_sha256": D.digest(raw["stdout"].encode()),
            "code_sha256": D.digest(raw["code"].encode()), "executable_sha256": raw["executable_sha256"],
            "language_literals_checked": 2, "replayed": False}


def recipe_endpoints(recipe, pair):
    # 작성 의도는 텍스트 계보를 찾는 데만 쓴다. 술어의 정답으로 내보내지 않는다.
    fields = {"support": ("evidence", "claim"), "restatement_or_topical": ("claim", "paraphrase"),
              "scope_time_condition": ("evidence", "scope_change"), "reverse_or_conflict": ("claim", "counterclaim")}
    a, b = fields[pair["contrast"]]
    D.require(pair["a"]["text"] == recipe[a][pair["a"]["language"]] and pair["b"]["text"] == recipe[b][pair["b"]["language"]], "pair differs from authoring recipe")


def issue_receipts(issues, bundle, contents):
    D.require(set(issues) == {"schema", "bundle_sha256", "issues"}, "unexpected evidence issue fields")
    D.require(issues.get("schema") == "vinc.laya.evidence-issues.v1" and issues["bundle_sha256"] == D.fingerprint(bundle), "issue input hash mismatch")
    sources = {s["source_id"]: s for s in bundle["sources"]}
    seen, rows = set(), []
    for issue in issues["issues"]:
        D.require(set(issue) == {"issue_id", "source_id", "source_sha256", "family_id", "kind", "status", "origin", "source_anchors", "repair_code", "canonical_doc"}, "unexpected evidence issue fields")
        D.require(issue["issue_id"] not in seen, "duplicate evidence issue")
        seen.add(issue["issue_id"])
        source = sources[issue["source_id"]]
        D.require(issue["source_sha256"] == source["sha256"], "issue source hash mismatch")
        D.require(issue["status"] == "proposed" and issue["origin"] == "assistant_source_inspection", "issue is not an admission decision")
        anchors = []
        for text in issue["source_anchors"]:
            full = contents[source["source_id"]]
            D.require(text and full.count(text) == 1, "issue anchor missing or ambiguous")
            start = full.index(text)
            anchors.append({"start": start, "end": start + len(text), "excerpt_sha256": D.digest(text.encode())})
        D.require(anchors, "issue needs source anchors")
        affected = sorted(p["pair_id"] for p in bundle["pairs"] if p["family_id"] == issue["family_id"])
        D.require(affected and all(source["source_id"] in {next(s["source_id"] for s in bundle["spans"] if s["span_id"] == r) for side in ("a", "b") for r in p[side]["evidence"]} for p in bundle["pairs"] if p["pair_id"] in affected), "issue family source mismatch")
        rows.append({**issue, "anchors": anchors, "affected_pair_ids": affected})
    return rows


def exposure_matches(pair, source, events):
    """동결 원장의 알려진 식별자·정규화 텍스트 일치를 기록한다. 미등록 이력은 모른다."""
    text_hashes = {D.digest(D.normalized(t).encode()) for t in (pair["a"]["text"], pair["b"]["text"], pair.get("context") or "")}
    ids = {source["source_id"], pair["a"]["endpoint_id"], pair["b"]["endpoint_id"]}
    hits = []
    for event in events:
        if (source["sha256"] in event.get("source_hashes", []) or pair["family_id"] in event.get("family_ids", [])
                or text_hashes & set(event.get("text_hashes", [])) or ids & set(event.get("source_ids", []))
                or source["document_id"] in event.get("document_ids", [])):
            hits.append({"event_id": event["event_id"], "kind": event["kind"]})
    return sorted(hits, key=lambda h: h["event_id"])


def audit(root, issues):
    root = Path(root).resolve()
    verified = V.verify(root)
    bundle, recipes = V.read(root, "bundle.json"), V.read(root, "authoring-recipes.json")
    sources = {s["source_id"]: s for s in bundle["sources"]}
    D.require(len(recipes) == len(sources) and {r["source_id"] for r in recipes} == set(sources), "one authoring recipe per source required")
    recipes = {r["source_id"]: r for r in recipes}
    spans = {s["span_id"]: s for s in bundle["spans"]}
    assignments = {s["source_id"]: s for s in bundle["assignments"]}
    contents = {sid: D.safe_file(root, s["snapshot_path"]).read_bytes().decode("utf-8") for sid, s in sources.items()}
    inspected = issue_receipts(issues, bundle, contents)
    events = V.read(root, bundle["exposure_registry"]["path"])["events"] + bundle["exposures"]
    source_rows = []
    for sid, source in sorted(sources.items()):
        text, recipe = contents[sid], recipes[sid]
        if source["category"] == "general_factual_evidence":
            receipt = fact_receipt(json.loads(text), source, recipe)
        elif source["category"] == "dated_engineering_measurement":
            receipt = observation_receipt(json.loads(text), recipe)
        else:
            D.require(source["category"] == "technical_specification", "unsupported source category")
            excerpt = "\n".join(text[s["start"]:s["end"]] for s in spans.values() if s["source_id"] == sid)
            # 수치 누락은 의미 오류의 증명이 아니다. 증거 문장에 덧붙은 수치를 확인할 단서다.
            missing = sorted(set(re.findall(r"\d+(?:\.\d+)*", recipe["evidence"]["en"])) - set(re.findall(r"\d+(?:\.\d+)*", excerpt)))
            receipt = {"kind": "normative_text_inventory", "evidence_numbers_absent_from_cited_spans": missing,
                       "normative_tokens": sorted(set(re.findall(r"\b(?:MUST|SHOULD|MAY)(?: NOT)?\b", excerpt))),
                       "semantic_grounding_verified": False}
        source_rows.append({"source_id": sid, "source_sha256": source["sha256"], "category": source["category"], "receipt": receipt})
    receipts = {s["source_id"]: s["receipt"] for s in source_rows}
    queue = {json.loads(line)["pair_id"] for line in (root / "review/blind-review.jsonl").read_text(encoding="utf-8").splitlines()}
    rows = []
    for pair in bundle["pairs"]:
        refs = sorted({r for side in ("a", "b") for r in pair[side]["evidence"]} | set(pair.get("context_evidence", [])))
        sids = sorted({spans[r]["source_id"] for r in refs})
        D.require(len(sids) == 1, "pilot pair needs one source")
        sid = sids[0]
        recipe_endpoints(recipes[sid], pair)
        rows.append({"pair_id": pair["pair_id"], "pair_sha256": D.fingerprint(pair), "family_id": pair["family_id"],
                     "source_id": sid, "source_sha256": sources[sid]["sha256"], "role": assignments[sid]["role"],
                     "language_cell": pair["a"]["language"] + "_" + pair["b"]["language"],
                     "span_receipts": [{"span_id": r, "start": spans[r]["start"], "end": spans[r]["end"], "excerpt_sha256": spans[r]["excerpt_sha256"]} for r in refs],
                     "checks": {"frozen_source_and_spans": True, "recipe_join": True, "registered_split_and_exposure": True},
                     "known_exposure_matches": exposure_matches(pair, sources[sid], events),
                     "receipt_kind": receipts[sid]["kind"], "author_review_required": bool(pair.get("review_required")),
                     "selected_in_original_blind_queue": pair["pair_id"] in queue,
                     "inspection_issue_ids": [i["issue_id"] for i in inspected if pair["pair_id"] in i["affected_pair_ids"]]})
    report = V.read(root, "validation.json")
    return {"schema": SCHEMA, "dataset_id": bundle["dataset_id"], "bundle_sha256": D.fingerprint(bundle),
            "manifest_sha256": verified["manifest_sha256"], "source_freeze_sha256": bundle["source_freeze_sha256"],
            "exposure_registry_sha256": bundle["exposure_registry"]["sha256"], "issue_spec_sha256": D.fingerprint(issues),
            "sources": source_rows, "pairs": rows, "issues": inspected,
            "summary": {"pairs_checked": len(rows), "sources_checked": len(source_rows), "roles": report["roles"],
                        "language_cells": report["language_cells"], "receipt_kinds": dict(Counter(s["receipt"]["kind"] for s in source_rows)),
                        "literal_evidence_language_checks": sum(s["receipt"].get("language_literals_checked", 0) for s in source_rows),
                        "author_review_required_pairs": sum(r["author_review_required"] for r in rows),
                        "source_inspection_issues": len(inspected), "source_inspection_pairs": sum(bool(r["inspection_issue_ids"]) for r in rows),
                        "source_inspection_pairs_in_original_queue": sum(bool(r["inspection_issue_ids"]) and r["selected_in_original_blind_queue"] for r in rows),
                        "numeric_reference_gap_sources": sum(bool(s["receipt"].get("evidence_numbers_absent_from_cited_spans")) for s in source_rows),
                        "known_exposure_events_checked": len(events), "pairs_with_known_exposure_matches": sum(bool(r["known_exposure_matches"]) for r in rows),
                        "source_training_permissions_pending": sum(s["eligibility"]["training"]["status"] == "pending" for s in sources.values()),
                        "original_review_queue_size": len(queue), "cross_role_comparisons_applicable": len(report["roles"]) > 1,
                        "historical_exposure_registry_is_exhaustive": False, "human_reviews_completed": 0,
                        "training_exportable_pairs": 0, "predicate_labels_produced": 0, "model_calls": 0, "observation_replays": 0}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--issues", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        root, out = args.directory.resolve(), args.out.resolve()
        D.require(not out.is_relative_to(root), "audit output cannot mutate frozen input")
        result = audit(root, json.loads(args.issues.read_text(encoding="utf-8")))
        with out.open("xb") as stream:
            stream.write(D.encoded(result))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f"refused: {exc}\n")
    print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    main()
