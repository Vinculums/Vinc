"""동결 파일럿에서 사람 검토 화면을 만들고 실제 입력을 새 판정 버전으로 수집한다."""
from __future__ import annotations

import argparse
import copy
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import data_contracts as D, pilot_verify
from .pins import PREDICATES

SESSION_SCHEMA = "vinc.laya.human-session.v1"
SUBMISSION_SCHEMA = "vinc.laya.human-submission.v1"
STAGES = ("author", "review", "adjudication")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def histories(bundle):
    result = defaultdict(list)
    for event in bundle["annotations"]:
        result[event["pair_id"]].append(event)
    for rows in result.values():
        rows.sort(key=lambda e: (D.stamp(e["at"]), e["event_id"]))
    return result


def fixed_payload(bundle):
    return {k: v for k, v in bundle.items() if k not in {"annotations", "previous_bundle_sha256", "label_status"}}


def load(pilot, bundle_path):
    """이전 버전 해시 사슬을 원래 무라벨 파일럿까지 대조한다."""
    pilot, bundle_path = Path(pilot), Path(bundle_path)
    pilot_verify.verify(pilot)
    base, current = read(pilot / "bundle.json"), read(bundle_path)
    frozen_hash = D.fingerprint(base)
    if D.fingerprint(current) != frozen_hash:
        manifest = read(bundle_path.parent / "manifest.json")
        actual = {p.relative_to(bundle_path.parent).as_posix() for p in bundle_path.parent.rglob("*") if p.is_file() and p.name != "manifest.json"}
        D.require(actual == set(manifest["files"]), "annotation version inventory changed")
        for name, sha in manifest["files"].items():
            D.require(D.digest(D.safe_file(bundle_path.parent, name).read_bytes()) == sha, "annotation version file hash mismatch")
        D.require(manifest["bundle_sha256"] == D.fingerprint(current), "annotation version bundle hash mismatch")
    cursor, snapshots = current, {}
    while D.fingerprint(cursor) != frozen_hash:
        D.require(fixed_payload(cursor) == fixed_payload(base), "frozen pilot payload changed")
        previous_hash = cursor.get("previous_bundle_sha256")
        D.require(isinstance(previous_hash, str) and len(previous_hash) == 64 and all(c in "0123456789abcdef" for c in previous_hash), "history hash required")
        D.require(previous_hash not in snapshots, "cyclic bundle history")
        previous = read(D.safe_file(bundle_path.parent, f"history/{previous_hash}.json"))
        D.require(D.fingerprint(previous) == previous_hash, "history snapshot hash mismatch")
        D.validate_append_only(previous, cursor)
        D.validate(cursor, bundle_path.parent)
        snapshots[previous_hash] = previous
        cursor = previous
    D.validate(current, bundle_path.parent)
    queue = [json.loads(line) for line in (pilot / "review/blind-review.jsonl").read_text(encoding="utf-8").splitlines()]
    D.require(all(e["actor_kind"] == "human" and e["pair_id"] in {q["pair_id"] for q in queue} for e in current["annotations"]), "only queue human annotations belong in this handoff")
    return current, queue, snapshots


def needs_adjudication(rows):
    return (bool(rows) and rows[-1]["stage"] == "review"
            and (any(e["labels"] != rows[-1]["labels"] for e in rows[:-1])
                 or any(v is None for v in rows[-1]["labels"].values())
                 or rows[-1]["evidence_strength"] == "ambiguous"))


def session(bundle, queue, instructions, stage):
    D.require(stage in STAGES, "invalid session stage")
    history, cases = histories(bundle), []
    for case in queue:
        rows = history[case["pair_id"]]
        eligible = ((stage == "author" and not rows)
                    or (stage == "review" and len(rows) == 1 and rows[0]["stage"] == "author")
                    or (stage == "adjudication" and needs_adjudication(rows)))
        if not eligible:
            continue
        # 독립 검토에는 이전 라벨·근거를 보여 주지 않는다. 조정 때만 쟁점을 보여 준다.
        item = {**copy.deepcopy(case), "reviews": [e["event_id"] for e in rows],
                "author_actor": rows[0]["actor"] if rows else None}
        if stage == "adjudication":
            item["prior_judgments"] = copy.deepcopy(rows)
        cases.append(item)
    return {"schema": SESSION_SCHEMA, "dataset_id": bundle["dataset_id"],
            "bundle_sha256": D.fingerprint(bundle), "queue_sha256": D.fingerprint(queue),
            "stage": stage, "rubric_id": bundle["rubric"]["id"],
            "instructions": instructions, "cases": cases}


def status(bundle, queue):
    history, counts, cells = histories(bundle), Counter(), defaultdict(Counter)
    pairs = {p["pair_id"]: p for p in bundle["pairs"]}
    for case in queue:
        rows = history[case["pair_id"]]
        if not rows:
            state = "unannotated"
        elif len(rows) == 1:
            state = "independent_review_pending"
        elif needs_adjudication(rows):
            state = "adjudication_pending"
        elif any(v is None for v in rows[-1]["labels"].values()) or rows[-1]["evidence_strength"] == "ambiguous":
            state = "unresolved"
        else:
            state = "resolved"
        counts[state] += 1
        pair = pairs[case["pair_id"]]
        cells[pair["a"]["language"] + "_" + pair["b"]["language"]][state] += 1
    return {"queue_cases": len(queue), "state_counts": dict(counts),
            "language_state_counts": {k: dict(v) for k, v in cells.items()},
            "ever_disagreed_cases": sum(len({D.fingerprint(e["labels"]) for e in history[c["pair_id"]]}) > 1 for c in queue),
            "declared_human_annotation_events": sum(e["actor_kind"] == "human" for e in bundle["annotations"]),
            "actor_identity_authenticated": False, "model_calls": 0}


def prepare(pilot, bundle_path, stage, out):
    bundle, queue, _ = load(pilot, bundle_path)
    data = session(bundle, queue, read(Path(pilot) / "review/instructions.json"), stage)
    D.require(data["cases"], "no eligible cases for this stage")
    template = Path(__file__).with_name("human_review.html").read_text(encoding="utf-8")
    # 인용된 원문의 </script>도 텍스트로만 남긴다.
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    out = Path(out)
    D.require(not out.resolve().is_relative_to(Path(pilot).resolve()), "output cannot modify frozen pilot")
    D.require(not out.resolve().is_relative_to(Path(bundle_path).parent.resolve()), "output cannot modify previous version")
    out.mkdir(parents=True, exist_ok=False)
    page = template.replace("__SESSION_JSON__", payload).replace("__SESSION_HASH__", D.fingerprint(data))
    (out / "review.html").write_bytes(page.encode("utf-8"))
    (out / "session.json").write_bytes(D.encoded(data))
    receipt = {**status(bundle, queue), "stage": stage, "session_cases": len(data["cases"]),
               "session_sha256": D.fingerprint(data), "html_sha256": D.digest(page.encode("utf-8"))}
    (out / "manifest.json").write_bytes(D.encoded(receipt))
    return receipt


def collect(bundle, data, submission, now):
    """사람이 확정한 행만 받는다. 빈 행과 미정 판정은 구별한다."""
    D.require(submission.get("schema") == SUBMISSION_SCHEMA, "unsupported submission schema")
    for field in ("bundle_sha256", "stage"):
        D.require(submission.get(field) == data[field], f"stale submission {field}")
    D.require(submission.get("session_sha256") == D.fingerprint(data), "session hash mismatch")
    D.require(submission.get("human_attestation") is True, "actual human attestation required")
    actor = submission.get("actor")
    D.require(D.nonempty(actor) and actor == actor.strip(), "actual actor identity required")
    D.require(isinstance(submission.get("entries"), list), "entries required")
    cases = {c["pair_id"]: c for c in data["cases"]}
    added, seen = [], set()
    for entry in submission["entries"]:
        pid = entry.get("pair_id")
        D.require(pid in cases and pid not in seen, "unknown or duplicate submission pair")
        seen.add(pid)
        case = cases[pid]
        D.require(entry.get("pair_sha256") == case["pair_sha256"], "stale submitted pair")
        D.require(entry.get("status") in {"draft", "submitted"}, "invalid entry status")
        if entry["status"] == "draft":
            continue
        D.require(actor != case["author_actor"], "self review is not independent")
        D.require(entry.get("reviews") == case["reviews"], "submitted review history changed")
        D.require(D.labels_valid(entry.get("labels")), "all three labels required")
        at = D.stamp(entry["at"])
        D.require(at <= now, "future annotation timestamp")
        prior = histories(bundle)[pid]
        D.require(not prior or at > D.stamp(prior[-1]["at"]), "annotation must follow prior history")
        event = {"pair_id": pid, "pair_sha256": case["pair_sha256"], "labels": entry["labels"],
                 "rationale": entry.get("rationale"), "evidence_strength": entry.get("evidence_strength"),
                 "actor": actor, "actor_kind": "human", "stage": data["stage"], "at": entry["at"],
                 "rubric_id": data["rubric_id"], "reviews": case["reviews"]}
        event["event_id"] = "annotation:" + D.fingerprint(event)
        added.append(event)
    D.require(added, "no submitted human annotations")
    return added


def ingest(pilot, bundle_path, submission_path, out):
    bundle_path, out = Path(bundle_path), Path(out)
    bundle, queue, snapshots = load(pilot, bundle_path)
    submission_bytes = Path(submission_path).read_bytes()
    submission = json.loads(submission_bytes)
    data = session(bundle, queue, read(Path(pilot) / "review/instructions.json"), submission.get("stage"))
    added = collect(bundle, data, submission, datetime.now(timezone.utc))
    current = copy.deepcopy(bundle)
    current["annotations"].extend(added)
    current["previous_bundle_sha256"] = D.fingerprint(bundle)
    current["label_status"] = "human_annotations_pending_training_admission"
    D.validate_append_only(bundle, current)
    report, _ = D.validate(current, bundle_path.parent)
    # 전부 검사한 뒤 새 폴더에 쓴다. manifest가 없으면 미완료 버전이다.
    D.require(not out.resolve().is_relative_to(Path(pilot).resolve()), "output cannot modify frozen pilot")
    D.require(not out.resolve().is_relative_to(bundle_path.parent.resolve()), "output cannot modify previous version")
    out.mkdir(parents=True, exist_ok=False)
    paths = {s["snapshot_path"] for s in current["sources"]} | {current["exposure_registry"]["path"]}
    for name in paths:
        target = D.safe_file(out, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(D.safe_file(bundle_path.parent, name).read_bytes())
    snapshots[D.fingerprint(bundle)] = bundle
    (out / "history").mkdir()
    for sha, previous in snapshots.items():
        (out / "history" / f"{sha}.json").write_bytes(D.encoded(previous))
    (out / "bundle.json").write_bytes(D.encoded(current))
    (out / "submission.json").write_bytes(submission_bytes)
    D.require(D.validate(current, out)[0] == report, "copied source validation changed")
    (out / "validation.json").write_bytes(D.encoded(report))
    receipt = {**status(current, queue), "added_annotations": len(added), "bundle_sha256": D.fingerprint(current),
               "previous_bundle_sha256": D.fingerprint(bundle), "exportable_pairs": report["exportable_pairs"],
               "files": {p.relative_to(out).as_posix(): D.digest(p.read_bytes()) for p in out.rglob("*") if p.is_file()}}
    (out / "manifest.json").write_bytes(D.encoded(receipt))
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "ingest", "status"))
    parser.add_argument("--pilot", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--stage", choices=STAGES)
    parser.add_argument("--submission", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "prepare":
            D.require(args.stage and args.out and not args.submission, "prepare needs --stage and --out")
            result = prepare(args.pilot, args.bundle, args.stage, args.out)
        elif args.action == "ingest":
            D.require(args.submission and args.out and not args.stage, "ingest needs --submission and --out")
            result = ingest(args.pilot, args.bundle, args.submission, args.out)
        else:
            D.require(not (args.stage or args.submission or args.out), "status takes only --pilot and --bundle")
            bundle, queue, _ = load(args.pilot, args.bundle)
            report, _ = D.validate(bundle, args.bundle.parent)
            result = {**status(bundle, queue), "exportable_pairs": report["exportable_pairs"]}
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        parser.exit(2, f"refused: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
