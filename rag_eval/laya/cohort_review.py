"""세 후보 묶음의 실제 판정 입력을 추가 전용 이력으로 수집한다. 학습 승인은 분리한다."""
from __future__ import annotations

import argparse
import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import candidate_context as C, candidate_revision as R, data_contracts as D, human_review as H, pilot_verify as V

SCHEMA = "vinc.laya.cohort-judgments.v1"
COHORTS = ("pilot", "development", "native")
DATA = Path(__file__).resolve().parents[1] / "evalsets"


def frozen(prepared, cohort, candidates):
    D.require(cohort in COHORTS, "unknown review cohort")
    C.verify(prepared)
    registry = V.read(candidates / "laya-candidate-context-v1", "preparation-receipt.json")
    receipt = V.read(prepared, "manifest.json")
    D.require(all(receipt.get(k) == v for k, v in registry.items()
                  if k not in {"replay_byte_identical", "native_parent_manifest_sha256", "native_parent_files_unchanged",
                               "observed_truth_or_predicate_labels_verified"}), "prepared cohort differs from registered receipt")
    initial = V.read(prepared / cohort, "session.json")
    if cohort == "pilot":
        source = V.read(candidates / "laya-contrast-revision-v2", "bundle.json")
        chosen = V.read(candidates / "laya-contrast-revision-v2", "selection.json")
        selected = [p for p in source["pairs"] if p["pair_id"] in chosen["active_pair_hashes"]]
        queue = R.hydrated_queue(source, selected, candidates / "laya-contrast-pilot-v1")
    elif cohort == "development":
        source = V.read(candidates / "laya-contrast-dev-v1", "bundle.json")
        selected = source["pairs"]
        queue = R.hydrated_queue(source, selected, candidates / "laya-contrast-dev-v1")
    else:
        source = V.read(prepared, "native-bundle.json")
        source["rubric"] = V.read(candidates / "laya-contrast-pilot-v1", "bundle.json")["rubric"]
        selected = source["pairs"]
        queue = C.native_queue(source, source["rubric"]["id"])
    D.require(not source["annotations"] and source["training_authorized"] is False, "frozen candidate admission changed")
    D.require(D.fingerprint(source) == initial["bundle_sha256"], "candidate source bundle changed")
    D.require(H.session(source, queue, initial["instructions"], "author") == initial,
              "frozen session differs from candidate evidence")
    base = {"schema": SCHEMA, "cohort": cohort, "cohort_role": "dev" if cohort == "development" else "train-candidate",
            "prepared_manifest_sha256": D.fingerprint(receipt), "original_session_sha256": D.fingerprint(initial),
            "dataset_id": initial["dataset_id"], "rubric": copy.deepcopy(source["rubric"]),
            "pairs": copy.deepcopy(selected), "annotations": [], "batches": [], "training_authorized": False}
    return base, queue, initial


def fixed(ledger):
    return {k: v for k, v in ledger.items() if k not in {"annotations", "batches", "previous_ledger_sha256"}}


def session(ledger, queue, initial, stage):
    # VIN-476에서 이미 저장한 첫 입력의 세션 해시를 그대로 받는다.
    if not ledger["annotations"] and stage == "author":
        return copy.deepcopy(initial)
    return H.session(ledger, queue, initial["instructions"], stage)


def validate_events(base, current, now):
    D.require(fixed(current) == fixed(base) and current["schema"] == SCHEMA and current["training_authorized"] is False,
              "cohort payload, role or training authority changed")
    pairs = {p["pair_id"]: p for p in base["pairs"]}
    seen, histories = set(), {}
    fields = {"event_id", "pair_id", "pair_sha256", "labels", "rationale", "evidence_strength", "actor",
              "actor_kind", "stage", "at", "rubric_id", "reviews"}
    for event in current["annotations"]:
        D.require(set(event) == fields and event["event_id"] not in seen, "duplicate or malformed annotation")
        seen.add(event["event_id"])
        D.require(event["event_id"] == "annotation:" + D.fingerprint({k: v for k, v in event.items() if k != "event_id"}),
                  "annotation event hash mismatch")
        pid = event["pair_id"]
        D.require(pid in pairs and event["pair_sha256"] == D.fingerprint(pairs[pid]), "stale or cross-cohort annotation")
        D.require(D.labels_valid(event["labels"]), "three integer labels or null required")
        D.require(isinstance(event["rationale"], dict) and set(event["rationale"]) == set(C.PREDICATES)
                  and all(D.nonempty(v) for v in event["rationale"].values()), "rationale required for each predicate")
        D.require(event["evidence_strength"] in {"direct", "inference", "ambiguous"}, "evidence strength required")
        D.require(event["actor_kind"] == "human" and D.nonempty(event["actor"])
                  and event["actor"] == event["actor"].strip(), "declared human identity required")
        D.require(event["rubric_id"] == base["rubric"]["id"], "annotation rubric changed")
        at = D.stamp(event["at"])
        D.require(D.stamp(pairs[pid]["authored_at"]) <= at <= now, "annotation timestamp outside candidate/import scope")
        prior = histories.setdefault(pid, [])
        expected_stage = "author" if not prior else "review" if len(prior) == 1 else "adjudication" if H.needs_adjudication(prior) else None
        D.require(event["stage"] == expected_stage and event["reviews"] == [e["event_id"] for e in prior],
                  "annotation stage or complete prior references changed")
        if prior:
            D.require(D.normalized(event["actor"]) != D.normalized(prior[0]["actor"]), "self review is not independent")
            D.require(at > D.stamp(prior[-1]["at"]), "annotation must follow prior history")
        prior.append(event)


def summary(ledger, queue):
    return {**H.status(ledger, queue), "cohort": ledger["cohort"], "cohort_role": ledger["cohort_role"],
            "ledger_sha256": D.fingerprint(ledger), "imported_batches": len(ledger["batches"]),
            "automatic_ingestion_supported": True, "training_authorized": False, "exportable_pairs": 0,
            "source_admission_separate": True, "rubric_admission_separate": True}


def load(prepared, cohort, candidates=DATA, version=None):
    prepared, candidates = Path(prepared), Path(candidates)
    base, queue, initial = frozen(prepared, cohort, candidates)
    if version is None:
        return base, queue, initial, {}
    version = Path(version)
    manifest = V.read(version, "manifest.json")
    D.require(manifest["schema"] == SCHEMA, "unsupported judgment version schema")
    D.require(manifest["files"] == R.inventory(version), "judgment version inventory/hash changed")
    current = V.read(version, "ledger.json")
    D.require(manifest["ledger_sha256"] == D.fingerprint(current), "judgment ledger hash mismatch")
    cursor, snapshots, seen = current, {}, set()
    now = datetime.now(timezone.utc)
    while D.fingerprint(cursor) != D.fingerprint(base):
        validate_events(base, cursor, now)
        previous_hash = cursor.get("previous_ledger_sha256")
        D.require(isinstance(previous_hash, str) and re.fullmatch(r"[0-9a-f]{64}", previous_hash)
                  and previous_hash not in seen, "invalid or cyclic judgment history")
        seen.add(previous_hash)
        previous = V.read(version, "history/" + previous_hash + ".json")
        D.require(D.fingerprint(previous) == previous_hash, "history snapshot hash mismatch")
        D.require(cursor["annotations"][:len(previous["annotations"])] == previous["annotations"]
                  and len(cursor["annotations"]) > len(previous["annotations"]), "judgments must append, not replace")
        D.require(len(cursor["batches"]) == len(previous["batches"]) + 1
                  and cursor["batches"][:-1] == previous["batches"], "import history changed")
        batch = cursor["batches"][-1]
        D.require(set(batch) == {"previous_ledger_sha256", "session_sha256", "submission_sha256", "at", "event_ids"}
                  and batch["previous_ledger_sha256"] == previous_hash, "import binding changed")
        imported_at = D.stamp(batch["at"])
        D.require(imported_at <= now and (not previous["batches"] or imported_at > D.stamp(previous["batches"][-1]["at"])),
                  "import timestamp outside history")
        for field in ("session_sha256", "submission_sha256"):
            D.require(isinstance(batch[field], str) and re.fullmatch(r"[0-9a-f]{64}", batch[field]), "invalid import hash")
        data = V.read(version, "sessions/" + batch["session_sha256"] + ".json")
        raw = D.safe_file(version, "submissions/" + batch["submission_sha256"] + ".json").read_bytes()
        D.require(D.fingerprint(data) == batch["session_sha256"] and D.digest(raw) == batch["submission_sha256"],
                  "saved session or submission hash mismatch")
        D.require(data == session(previous, queue, initial, data["stage"]), "saved session differs from prior ledger")
        added = H.collect(previous, data, json.loads(raw), imported_at)
        D.require(cursor["annotations"] == previous["annotations"] + added
                  and batch["event_ids"] == [e["event_id"] for e in added], "annotations differ from actual submitted rows")
        snapshots[previous_hash] = previous
        cursor = previous
    D.require(manifest["summary"] == summary(current, queue), "judgment summary mismatch")
    return current, queue, initial, snapshots


def new_output(out, prepared, candidates, version=None, submission=None):
    out = Path(out)
    roots = [Path(prepared), Path(candidates), Path(__file__).resolve().parents[2]]
    if version is not None:
        roots.append(Path(version))
    D.require(not out.exists() and all(not out.resolve().is_relative_to(r.resolve()) for r in roots),
              "output must be new and outside frozen inputs/repository")
    D.require(submission is None or not Path(submission).resolve().is_relative_to(out.resolve()), "output contains input submission")
    return out


def prepare(prepared, cohort, stage, out, candidates=DATA, version=None):
    ledger, queue, initial, _ = load(prepared, cohort, candidates, version)
    data = session(ledger, queue, initial, stage)
    D.require(data["cases"], "no eligible cases for this review stage")
    out = new_output(out, prepared, candidates, version)
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    page = Path(H.__file__).with_name("human_review.html").read_text(encoding="utf-8")
    page = page.replace("__SESSION_JSON__", payload).replace("__SESSION_HASH__", D.fingerprint(data))
    out.mkdir(parents=True)
    (out / "session.json").write_bytes(D.encoded(data))
    (out / "review.html").write_text(page, encoding="utf-8")
    receipt = {**summary(ledger, queue), "stage": stage, "session_cases": len(data["cases"]),
               "session_sha256": D.fingerprint(data), "files": R.inventory(out)}
    R.write(out, "manifest.json", receipt)
    return receipt


def ingest(prepared, cohort, submission, out, candidates=DATA, version=None):
    base, queue, initial, snapshots = load(prepared, cohort, candidates, version)
    raw = Path(submission).read_bytes()
    supplied = json.loads(raw)
    data = session(base, queue, initial, supplied.get("stage"))
    now = datetime.now(timezone.utc)
    added = H.collect(base, data, supplied, now)
    current = copy.deepcopy(base)
    current["annotations"].extend(added)
    current["previous_ledger_sha256"] = D.fingerprint(base)
    current["batches"].append({"previous_ledger_sha256": D.fingerprint(base), "session_sha256": D.fingerprint(data),
                               "submission_sha256": D.digest(raw), "at": now.isoformat(),
                               "event_ids": [e["event_id"] for e in added]})
    frozen_base, _, _ = frozen(Path(prepared), cohort, Path(candidates))
    validate_events(frozen_base, current, now)
    out = new_output(out, prepared, candidates, version, submission)
    out.mkdir(parents=True)
    snapshots[D.fingerprint(base)] = base
    for sha, value in snapshots.items():
        R.write(out, "history/" + sha + ".json", value)
    if version is not None:
        for folder in ("sessions", "submissions"):
            for path in (Path(version) / folder).glob("*.json"):
                target = out / folder / path.name
                target.parent.mkdir(exist_ok=True)
                target.write_bytes(path.read_bytes())
    session_path = out / "sessions" / (D.fingerprint(data) + ".json")
    session_path.parent.mkdir(exist_ok=True)
    D.require(not session_path.exists(), "session already imported")
    session_path.write_bytes(D.encoded(data))
    submission_path = out / "submissions" / (D.digest(raw) + ".json")
    submission_path.parent.mkdir(exist_ok=True)
    D.require(not submission_path.exists(), "submission already imported")
    submission_path.write_bytes(raw)
    R.write(out, "ledger.json", current)
    receipt = {"schema": SCHEMA, "ledger_sha256": D.fingerprint(current), "summary": summary(current, queue),
               "added_annotations": len(added), "files": R.inventory(out)}
    R.write(out, "manifest.json", receipt)
    # 신원 인증은 하지 않는다. 읽기 확인은 제출 행과 모든 이전 이력을 재구성한다.
    load(prepared, cohort, candidates, out)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "ingest", "status"))
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--cohort", choices=COHORTS, required=True)
    parser.add_argument("--candidates", type=Path, default=DATA)
    parser.add_argument("--version", type=Path)
    parser.add_argument("--stage", choices=H.STAGES)
    parser.add_argument("--submission", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "prepare":
            D.require(args.stage and args.out and not args.submission, "prepare needs stage/output only")
            result = prepare(args.prepared, args.cohort, args.stage, args.out, args.candidates, args.version)
        elif args.action == "ingest":
            D.require(args.submission and args.out and not args.stage, "ingest needs actual submission/output only")
            result = ingest(args.prepared, args.cohort, args.submission, args.out, args.candidates, args.version)
        else:
            D.require(not (args.stage or args.submission or args.out), "status accepts no mutation arguments")
            ledger, queue, _, _ = load(args.prepared, args.cohort, args.candidates, args.version)
            result = summary(ledger, queue)
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        parser.exit(2, f"refused: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
