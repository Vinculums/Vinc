"""수정 후보의 계보와 개발 분할을 검사한다. 라벨이나 모델 점수를 만들지 않는다."""
from __future__ import annotations

import argparse
import copy
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path

from . import data_contracts as D, pilot_review as P, pilot_verify as V
from .pins import PREDICATES, QUESTIONS, render_state


def write(root, name, value):
    target = D.safe_file(root, name)
    target.parent.mkdir(parents=True, exist_ok=True)
    D.require(not target.exists(), f"artifact already exists: {name}")
    target.write_bytes(D.encoded(value))


def inventory(root):
    return {p.relative_to(root).as_posix(): D.digest(p.read_bytes())
            for p in sorted(root.rglob("*")) if p.is_file() and p != root / "manifest.json"}


def make_pair(dataset, allocation, recipe, contrast, authored, parent=None):
    a_name, b_name = {"support": ("evidence", "claim"),
                      "restatement_or_topical": ("claim", "paraphrase"),
                      "scope_time_condition": ("evidence", "scope_change"),
                      "reverse_or_conflict": ("claim", "counterclaim")}[contrast]
    langs = allocation["language_cell"].split("_")
    pid = dataset + ":" + D.digest((allocation["source_id"] + ":" + contrast + ":revision2").encode())[:16]
    pair = {"pair_id": pid, "family_id": allocation["family_id"], "parents": [parent] if parent else [],
            "context": recipe["context"][langs[0]], "context_evidence": recipe["span_ids"],
            "domain": recipe["category"], "mechanism": contrast, "contrast": contrast,
            "claim_time": recipe["revision"], "authored_at": authored,
            "review_required": True, "author_kind": "ai", "author": "OpenAI Codex assistant",
            "exact_served_model": None}
    for side, field, lang in (("a", a_name, langs[0]), ("b", b_name, langs[1])):
        pair[side] = {"endpoint_id": pid + ":" + side, "revision": "2" if parent else "1",
                      "text": recipe[field][lang], "language": lang, "mode": "authored",
                      "evidence": recipe["span_ids"]}
    pair["render_sha256"] = D.digest(render_state(pair["a"]["text"], pair["b"]["text"], pair["context"]).encode())
    return pair


def add_recipes(bundle, frozen, recipes, root, authored, parents=()):
    sources = {s["source_id"]: s for s in bundle["sources"]}
    allocation = {a["source_id"]: a for a in frozen["allocation"]}
    old = {(p["family_id"], p["contrast"]): p["pair_id"] for p in parents}
    replacements = []
    D.require(len({r["source_id"] for r in recipes}) == len(recipes), "duplicate recipe source")
    for recipe in recipes:
        fields = {"source_id", "category", "revision", "span_ids", "anchors", "evidence", "claim", "paraphrase", "scope_change", "counterclaim", "context", "author_kind", "author", "exact_served_model", "counterclaims_are_authored_not_observed"}
        D.require(set(recipe) == fields | ({"issue_id"} if parents else set()), "unexpected authoring recipe fields")
        source = sources[recipe["source_id"]]
        D.require(recipe["category"] == source["category"] and recipe["revision"] == source["revision"], "recipe source metadata mismatch")
        raw = D.safe_file(root, source["snapshot_path"]).read_bytes().decode("utf-8")
        D.require(recipe["author_kind"] == "ai" and recipe["exact_served_model"] is None, "candidate provenance required")
        D.require(set(recipe["span_ids"]) == {a["span_id"] for a in recipe["anchors"]}, "recipe span list mismatch")
        for anchor in recipe["anchors"]:
            text = anchor["text"]
            D.require(text and raw.count(text) == 1, "citation must identify one exact source span")
            start = raw.index(text)
            bundle["spans"].append({"span_id": anchor["span_id"], "source_id": source["source_id"],
                                    "source_sha256": source["sha256"], "start": start, "end": start + len(text),
                                    "excerpt_sha256": D.digest(text.encode())})
        for contrast in sorted(P.CONTRASTS):
            a = allocation[source["source_id"]]
            parent = old.get((a["family_id"], contrast))
            D.require(not parents or parent, "revision must name an original contrast")
            pair = make_pair(bundle["dataset_id"], a, recipe, contrast, authored, parent)
            bundle["pairs"].append(pair)
            if parent:
                replacements.append({"old_pair_id": parent, "new_pair_id": pair["pair_id"],
                                     "issue_id": recipe["issue_id"], "new_pair_sha256": D.fingerprint(pair)})
    return replacements


def selection(previous, current, replacements):
    pairs = {p["pair_id"]: p for p in current["pairs"]}
    remap = {r["old_pair_id"]: r["new_pair_id"] for r in replacements}
    selected = [remap.get(p["pair_id"], p["pair_id"]) for p in previous["pairs"]]
    return {"schema": "vinc.laya.candidate-selection.v1", "previous_bundle_sha256": D.fingerprint(previous),
            "history_bundle_sha256": D.fingerprint(current), "replacements": replacements,
            "active_pair_hashes": {pid: D.fingerprint(pairs[pid]) for pid in selected},
            "superseded_pair_hashes": {pid: D.fingerprint(pairs[pid]) for pid in remap},
            "label_status": "unlabeled", "training_authorized": False}


def validate_selection(previous, current, chosen):
    D.validate_append_only(previous, current)
    D.require(chosen["schema"] == "vinc.laya.candidate-selection.v1", "unknown selection")
    expected = selection(previous, current, chosen["replacements"])
    D.require(chosen == expected, "selection or candidate hash mismatch")
    additions = current["pairs"][len(previous["pairs"]):]
    D.require(len(additions) == 12 and len(chosen["replacements"]) == 12, "revision must append exactly twelve candidates")
    pairs = {p["pair_id"]: p for p in current["pairs"]}
    old_ids = {p["pair_id"] for p in previous["pairs"]}
    D.require(set(chosen["superseded_pair_hashes"]) <= old_ids, "only original candidates may be superseded")
    D.require({p["pair_id"] for p in additions} == {r["new_pair_id"] for r in chosen["replacements"]}, "replacement coverage mismatch")
    D.require(len(chosen["active_pair_hashes"]) == 240, "active selection must preserve 240 candidates")
    for item in chosen["replacements"]:
        old, new = pairs[item["old_pair_id"]], pairs[item["new_pair_id"]]
        D.require(new["parents"] == [old["pair_id"]] and new["contrast"] == old["contrast"], "replacement lineage mismatch")
        D.require(item["issue_id"] == "evidence-issue:" + old["family_id"].split("-")[-1], "replacement issue mismatch")
    D.require({p["family_id"] for p in additions} == {"family:laya-p1-" + k for k in ("spec09", "spec10", "spec16")}, "revision is outside the audited families")
    rows = [pairs[pid] for pid in chosen["active_pair_hashes"]]
    check_allocation(rows, 60, 60)
    return rows


def check_allocation(rows, families, cell_count):
    grouped = defaultdict(list)
    for pair in sorted(rows, key=lambda p: p["pair_id"]):
        grouped[pair["family_id"]].append(pair)
    D.require(len(grouped) == families, "family allocation mismatch")
    D.require(Counter(p["a"]["language"] + "_" + p["b"]["language"] for p in rows) == dict.fromkeys(P.CELLS, cell_count), "language allocation mismatch")
    for variants in grouped.values():
        D.require(len(variants) == 4 and {p["contrast"] for p in variants} == P.CONTRASTS, "family contrast allocation mismatch")


def blind_queue(bundle, rows, root):
    spans, sources = ({s[k]: s for s in bundle[field]} for field, k in (("spans", "span_id"), ("sources", "source_id")))
    queue = []
    for pair in sorted(rows, key=lambda p: p["pair_id"]):
        refs = set(pair.get("context_evidence", [])) | {r for side in ("a", "b") for r in pair[side]["evidence"]}
        evidence = []
        for ref in sorted(refs):
            span = spans[ref]; source = sources[span["source_id"]]
            evidence.append({"span_id": ref, "start": span["start"], "end": span["end"],
                             "snapshot_path": source["snapshot_path"], "excerpt_sha256": span["excerpt_sha256"],
                             "origin_uri": source["origin_uri"], "source_sha256": source["sha256"]})
        queue.append({"pair_id": pair["pair_id"], "pair_sha256": D.fingerprint(pair),
                      "a": pair["a"]["text"], "b": pair["b"]["text"], "context": pair.get("context"),
                      "evidence": evidence, "rubric_id": bundle["rubric"]["id"], "questions_hash": bundle["questions_hash"]})
    return queue


def hydrated_queue(bundle, rows, root):
    """검토할 때만 불변 참조의 정확한 원문을 로컬 출력에 붙인다."""
    queue = blind_queue(bundle, rows, root)
    for item in queue:
        for evidence in item["evidence"]:
            data = D.safe_file(root, evidence["snapshot_path"]).read_bytes()
            D.require(D.digest(data) == evidence["source_sha256"], "review source hash mismatch")
            text = data.decode("utf-8")[evidence["start"]:evidence["end"]]
            D.require(D.digest(text.encode()) == evidence["excerpt_sha256"], "review excerpt hash mismatch")
            evidence["text"] = text
    return queue


def literal_receipts(bundle, recipes, root):
    sources = {s["source_id"]: s for s in bundle["sources"]}
    receipts = []
    for recipe in recipes:
        source = sources[recipe["source_id"]]
        if source["category"] == "technical_specification":
            continue  # 해시와 인용 범위를 검사한다. 규격의 의미를 라벨로 판정하지 않는다.
        raw = V.read(root, source["snapshot_path"])
        if source["category"] == "dated_engineering_measurement":
            D.require(raw["origin"] == "locally_observed" and raw["returncode"] == 0 and raw["stderr"] == "", "successful observation required")
            D.require(D.stamp(raw["started_at"]) <= D.stamp(raw["completed_at"]) <= D.stamp(source["captured_at"]), "observation timestamp mismatch")
            lines = raw["stdout"].splitlines()
            D.require(len(lines) == 2 and json.loads(lines[0]) == raw["rows"] and lines[1] == raw["sqlite_version"], "observation output mismatch")
            key, output = raw["case_id"], lines[0]
            expected = {"en": f"Isolated SQLite case {key} exited 0 and printed rows {output}.",
                        "ko": f"격리된 SQLite 시험 {key}의 종료 코드는 0이며 출력된 행은 {output}이다."}
            D.require(all(raw["query"] in recipe["context"][lang] and raw["sqlite_version"] in recipe["context"][lang] for lang in ("en", "ko")), "observation SQL or version missing from context")
            receipt = {"kind": "saved_sqlite_observation", "stdout_sha256": D.digest(raw["stdout"].encode()), "replayed": False}
        else:
            props = raw["properties"]; sid = props["stationIdentifier"]
            D.require(source["origin_uri"] == "https://api.weather.gov/stations/" + sid and source["revision"] == "snapshot:" + source["sha256"], "station source identity mismatch")
            fields = [("timeZone", "timeZone", props["timeZone"], "", "", "/properties/timeZone"),
                      ("elevation", "고도", props["elevation"]["value"], " m", "m", "/properties/elevation/value"),
                      ("county zone", "county 구역 식별자", props["county"].rsplit("/", 1)[1], "", "", "/properties/county"),
                      ("provider", "provider", props["provider"], "", "", "/properties/provider")]
            matches = [(en, ko, v, en_unit, ko_unit, pointer) for en, ko, v, en_unit, ko_unit, pointer in fields
                       if recipe["evidence"]["en"] == f"The frozen NWS metadata for {sid} lists {en} {v}{en_unit}."]
            D.require(len(matches) == 1, "station evidence differs from frozen field")
            en, ko, value, _, unit, pointer = matches[0]
            if en == "elevation":
                D.require(props["elevation"]["unitCode"] == "wmoUnit:m", "station elevation unit mismatch")
            particle = "은" if en == "timeZone" else "는"
            expected = {"en": recipe["evidence"]["en"], "ko": f"고정한 NWS {sid} 메타데이터의 {ko}{particle} {value}{unit}다."}
            receipt = {"kind": "station_metadata_literal", "station_id": sid, "field_pointer": pointer,
                       "independent_geographic_verification": False}
        D.require(recipe["evidence"] == expected, "evidence differs from literal source")
        receipts.append({"source_id": source["source_id"], "source_sha256": source["sha256"],
                         "language_literals_checked": 2, **receipt})
    return receipts


def joint_bundle(history, development, history_root, dev_root):
    D.require(history_root.parent.resolve() == dev_root.parent.resolve(), "datasets must have a common artifact root")
    joint = copy.deepcopy(history)
    joint["dataset_id"] = "joint:laya-candidate-development-v1"
    joint.pop("previous_bundle_sha256", None)
    for field in ("sources", "assignments", "spans", "pairs", "exposures"):
        joint[field] += copy.deepcopy(development[field])
    for source in joint["sources"]:
        origin = history_root if source["source_id"] in {s["source_id"] for s in history["sources"]} else dev_root
        source["snapshot_path"] = origin.name + "/" + source["snapshot_path"]
    ids = {p["pair_id"]: joint["dataset_id"] + ":" + D.digest(p["pair_id"].encode())[:24] for p in joint["pairs"]}
    for pair in joint["pairs"]:
        pair["pair_id"] = ids[pair["pair_id"]]
        pair["parents"] = [ids[p] for p in pair.get("parents", [])]
    joint["exposure_registry"]["path"] = history_root.name + "/" + joint["exposure_registry"]["path"]
    return joint


def development_exposure(bundle, at):
    return {"event_id": "exposure:laya-development-candidate-authoring-2026-10-11", "kind": "development_used",
            "at": at, "reference": "decision:prepare-revised-laya-candidates-and-source-separated-development-data-2026-10-11",
            "scope": "development candidate authoring and source inspection; no model execution",
            "source_ids": sorted(s["source_id"] for s in bundle["sources"]),
            "source_hashes": sorted(s["sha256"] for s in bundle["sources"]),
            "document_ids": sorted(s["document_id"] for s in bundle["sources"]),
            "family_ids": sorted({p["family_id"] for p in bundle["pairs"]})}


def review_files(bundle, rows, root, source_root):
    queue = blind_queue(bundle, rows, source_root)
    write(root, "review/blind-review.json", queue)
    write(root, "review/human-input.json", [{"pair_id": q["pair_id"], "pair_sha256": q["pair_sha256"],
          "actor": None, "actor_kind": "human", "stage": "author", "at": None,
          "rubric_id": bundle["rubric"]["id"], "status": "unfilled", "evidence_strength": None,
          "labels": dict.fromkeys(PREDICATES), "rationale": dict.fromkeys(PREDICATES)} for q in queue])
    write(root, "review/manifest.json", {"bundle_sha256": D.fingerprint(bundle), "selected": len(queue),
          "queue_sha256": D.fingerprint(queue), "questions": QUESTIONS, "questions_hash": bundle["questions_hash"],
          "human_reviews_completed": 0, "annotation_events": 0, "training_exportable_pairs": 0,
          "existing_vin443_session_rebased": False, "automatic_ingestion_supported": False, "model_calls": 0})


def build(original, revision, development, revision_recipes, dev_recipes, authored, freeze_commit):
    V.verify(original)
    D.require(not revision.exists(), "revision destination already exists")
    revision.mkdir(parents=True)
    previous = V.read(original, "bundle.json")
    history = copy.deepcopy(previous)
    # 기존 원문은 --original에서 그대로 읽는다. 복제하거나 경로 payload를 바꾸지 않는다.
    history["previous_bundle_sha256"] = D.fingerprint(previous)
    replacements = add_recipes(history, V.read(original, "source-freeze.json"), revision_recipes, original, authored, previous["pairs"])
    chosen = selection(previous, history, replacements)
    active = validate_selection(previous, history, chosen)
    frozen = V.read(development, "source-freeze.json")
    D.require(D.stamp(authored) > D.stamp(frozen["created_at"]), "authoring must follow development freeze")
    D.require(len(freeze_commit) == 40 and all(c in "0123456789abcdef" for c in freeze_commit), "freeze commit pin required")
    dev = {k: copy.deepcopy(previous[k]) for k in ("schema", "questions_hash", "render_version", "rubric", "label_status", "training_authorized")}
    dev.update(dataset_id="development:laya-contrast-v1", sources=frozen["sources"], assignments=frozen["assignments"],
               pairs=[], spans=[], annotations=[], exposures=[], exposure_registry=previous["exposure_registry"],
               source_freeze_sha256=D.fingerprint(frozen), source_freeze_commit=freeze_commit,
               source_freeze_precedes_authoring=True)
    shutil.copyfile(original / "historical-exposure.json", development / "historical-exposure.json")
    D.require({r["source_id"] for r in dev_recipes} == {s["source_id"] for s in dev["sources"]}, "development recipe coverage mismatch")
    add_recipes(dev, frozen, dev_recipes, development, authored)
    dev["exposures"] = [development_exposure(dev, authored)]
    check_allocation(dev["pairs"], 24, 24)
    write(development, "evidence-checks.json", literal_receipts(dev, dev_recipes, development))
    for bundle, root, source_root, recipes, rows in ((history, revision, original, revision_recipes, active), (dev, development, development, dev_recipes, dev["pairs"])):
        report, accepted = D.validate(bundle, source_root)
        D.require(not accepted and not report["unresolved_near_duplicates"], "unlabeled cohort must not export or contain unresolved near matches")
        write(root, "bundle.json", bundle); write(root, "validation.json", report)
        write(root, "authoring-recipes.json", recipes)
        review_files(bundle, rows, root, source_root)
    write(revision, "selection.json", chosen)
    report, _ = D.validate(joint_bundle(history, dev, original, development), original.parent)
    D.require(not report["unresolved_near_duplicates"], "unresolved training/development near matches")
    write(revision, "joint-validation.json", report)
    for root in (revision, development):
        write(root, "manifest.json", {"schema": "vinc.laya.artifacts.v1", "files": inventory(root)})
    return verify(original, revision, development)


def verify(original, revision, development):
    V.verify(original)
    previous = V.read(original, "bundle.json")
    history, dev = V.read(revision, "bundle.json"), V.read(development, "bundle.json")
    active = validate_selection(previous, history, V.read(revision, "selection.json"))
    # 새 텍스트와 근거도 저장된 작성 레시피로 재구성한다. 해시 목록만 믿지 않는다.
    reproduced = copy.deepcopy(previous)
    reproduced["previous_bundle_sha256"] = D.fingerprint(previous)
    revision_recipes = V.read(revision, "authoring-recipes.json")
    additions = history["pairs"][len(previous["pairs"]):]
    D.require(len({p["authored_at"] for p in additions}) == 1, "revision authoring timestamp mismatch")
    add_recipes(reproduced, V.read(original, "source-freeze.json"), revision_recipes, original, additions[0]["authored_at"], previous["pairs"])
    D.require(reproduced == history, "revision differs from authoring recipes")
    reproduced_dev = copy.deepcopy(dev)
    reproduced_dev["pairs"], reproduced_dev["spans"] = [], []
    D.require(len({p["authored_at"] for p in dev["pairs"]}) == 1, "development authoring timestamp mismatch")
    recipes = V.read(development, "authoring-recipes.json")
    D.require({r["source_id"] for r in recipes} == {s["source_id"] for s in dev["sources"]}, "development recipe coverage mismatch")
    add_recipes(reproduced_dev, V.read(development, "source-freeze.json"), recipes, development, dev["pairs"][0]["authored_at"])
    D.require(reproduced_dev == dev, "development differs from authoring recipes")
    D.require(dev["exposures"] == [development_exposure(dev, dev["pairs"][0]["authored_at"])], "development exposure registry changed")
    D.require(not ({s["origin_uri"] for s in history["sources"]} & {s["origin_uri"] for s in dev["sources"]}), "origin URI crosses training/development roles")
    receipts = literal_receipts(dev, recipes, development)
    D.require(receipts == V.read(development, "evidence-checks.json"), "literal receipt mismatch")
    for bundle, root, rows in ((history, revision, active), (dev, development, dev["pairs"])):
        source_root = original if root == revision else root
        D.require(V.read(root, "manifest.json")["files"] == inventory(root), "artifact inventory/hash mismatch")
        D.require(not bundle["annotations"] and bundle["label_status"] == "unlabeled" and bundle["training_authorized"] is False, "candidate cohort contains labels or authorization")
        D.require(all(s["eligibility"]["training"]["status"] == "pending" for s in bundle["sources"]), "source training permission changed")
        frozen = V.read(source_root, "source-freeze.json")
        D.require(bundle["source_freeze_sha256"] == D.fingerprint(frozen), "source freeze hash mismatch")
        D.require(bundle["sources"] == frozen["sources"] and bundle["assignments"] == frozen["assignments"], "split changed after freeze")
        for license_record in frozen["licenses"].values():
            D.require(D.digest(D.safe_file(source_root, license_record["path"]).read_bytes()) == license_record["sha256"], "license snapshot changed")
        D.require(all(D.stamp(p["authored_at"]) > D.stamp(frozen["created_at"]) for p in bundle["pairs"]), "authoring precedes source freeze")
        allocation = {a["family_id"]: a for a in frozen["allocation"]}
        span_sources = {s["span_id"]: s["source_id"] for s in bundle["spans"]}
        for pair in bundle["pairs"]:
            a = allocation[pair["family_id"]]
            D.require(pair["a"]["language"] + "_" + pair["b"]["language"] == a["language_cell"], "language differs from frozen allocation")
            D.require(all(span_sources[r] == a["source_id"] for r in pair["context_evidence"] + [r for side in ("a", "b") for r in pair[side]["evidence"]]), "candidate differs from frozen source family")
        report, accepted = D.validate(bundle, source_root)
        D.require(not accepted and report == V.read(root, "validation.json"), "validation receipt mismatch")
        queue = blind_queue(bundle, rows, source_root)
        D.require(queue == V.read(root, "review/blind-review.json"), "blind queue mismatch")
        receipt = V.read(root, "review/manifest.json")
        D.require(receipt["bundle_sha256"] == D.fingerprint(bundle) and receipt["queue_sha256"] == D.fingerprint(queue) and receipt["questions"] == QUESTIONS and receipt["questions_hash"] == bundle["questions_hash"] and receipt["selected"] == len(rows), "review receipt mismatch")
        D.require(all(receipt[k] == 0 for k in ("human_reviews_completed", "annotation_events", "training_exportable_pairs", "model_calls")) and receipt["existing_vin443_session_rebased"] is False and receipt["automatic_ingestion_supported"] is False, "review authority changed")
        forms = V.read(root, "review/human-input.json")
        D.require([(f["pair_id"], f["pair_sha256"]) for f in forms] == [(q["pair_id"], q["pair_sha256"]) for q in queue], "blank form target mismatch")
        D.require(all(f == {"pair_id": q["pair_id"], "pair_sha256": q["pair_sha256"], "actor": None,
                           "actor_kind": "human", "stage": "author", "at": None, "rubric_id": bundle["rubric"]["id"],
                           "status": "unfilled", "evidence_strength": None, "labels": dict.fromkeys(PREDICATES),
                           "rationale": dict.fromkeys(PREDICATES)} for f, q in zip(forms, queue)), "human form contains invented annotations")
    check_allocation(dev["pairs"], 24, 24)
    frozen_dev = V.read(development, "source-freeze.json")
    D.require(D.fingerprint(V.read(development, "source-plan.json")) == frozen_dev["source_plan_sha256"], "source plan hash mismatch")
    D.require(len(dev["source_freeze_commit"]) == 40 and all(c in "0123456789abcdef" for c in dev["source_freeze_commit"]), "development freeze commit pin required")
    for category in {s["category"] for s in dev["sources"]}:
        D.require(Counter(a["language_cell"] for a in frozen_dev["allocation"] if next(s["category"] for s in dev["sources"] if s["source_id"] == a["source_id"]) == category) == dict.fromkeys(P.CELLS, 2), "category language allocation changed")
    D.require(Counter(s["category"] for s in dev["sources"]) == dict.fromkeys(("technical_specification", "dated_engineering_measurement", "general_factual_evidence"), 8), "development category allocation changed")
    D.require({a["role"] for a in history["assignments"]} == {"train"} and {a["role"] for a in dev["assignments"]} == {"dev"}, "candidate roles changed")
    joint, accepted = D.validate(joint_bundle(history, dev, original, development), original.parent)
    D.require(not accepted and not joint["unresolved_near_duplicates"] and joint == V.read(revision, "joint-validation.json"), "joint leakage validation mismatch")
    return {"verified": True, "original_pairs": 240, "history_pairs": len(history["pairs"]), "active_candidates": len(active),
            "revised_candidates": 12, "development_pairs": len(dev["pairs"]), "development_families": 24,
            "development_language_cells": V.read(development, "validation.json")["language_cells"],
            "development_literal_language_checks": sum(r["language_literals_checked"] for r in receipts),
            "joint_pairs_checked": joint["pairs"], "cross_role_near_matches": 0,
            "human_annotations": 0, "training_exportable_pairs": 0, "model_calls": 0,
            "lexical_screen_proves_cross_language_independence": False,
            "historical_exposure_registry_is_exhaustive": False,
            "revision_manifest_sha256": D.digest((revision / "manifest.json").read_bytes()),
            "development_manifest_sha256": D.digest((development / "manifest.json").read_bytes())}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--revision", type=Path, required=True)
    parser.add_argument("--development", type=Path, required=True)
    parser.add_argument("--review-out", type=Path, help="materialize exact evidence into a new local directory outside this repository")
    parser.add_argument("--author", action="store_true", help="append candidates from explicit recipes to a new revision and a frozen development directory")
    parser.add_argument("--revision-recipes", type=Path)
    parser.add_argument("--development-recipes", type=Path)
    parser.add_argument("--authored-at")
    parser.add_argument("--freeze-commit")
    args = parser.parse_args(argv)
    try:
        author_args = (args.revision_recipes, args.development_recipes, args.authored_at, args.freeze_commit)
        D.require(all(author_args) if args.author else not any(author_args), "author mode needs both recipes, timestamp and freeze commit; verify mode accepts none")
        if args.author:
            result = build(args.original, args.revision, args.development,
                           json.loads(args.revision_recipes.read_text(encoding="utf-8")),
                           json.loads(args.development_recipes.read_text(encoding="utf-8")),
                           args.authored_at, args.freeze_commit)
        else:
            result = verify(args.original, args.revision, args.development)
        if args.review_out:
            D.require(not args.review_out.resolve().is_relative_to(args.original.resolve().parents[2]), "review output must be outside repository")
            args.review_out.mkdir(parents=True, exist_ok=False)
            previous, history = V.read(args.original, "bundle.json"), V.read(args.revision, "bundle.json")
            active = validate_selection(previous, history, V.read(args.revision, "selection.json"))
            write(args.review_out, "revision-review.json", hydrated_queue(history, active, args.original))
            dev = V.read(args.development, "bundle.json")
            write(args.review_out, "development-review.json", hydrated_queue(dev, dev["pairs"], args.development))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f"refused: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
