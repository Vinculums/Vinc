"""판정과 인물은 가상 검증 입력이다. 실제 사람 검토로 집계하지 않는다."""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from rag_eval.laya import cohort_review as G, candidate_context as C, candidate_revision as R, data_contracts as D, human_review as H
from rag_eval.laya.pins import QUESTIONS


@pytest.fixture
def cohorts(tmp_path, monkeypatch):
    candidates, prepared = tmp_path / "candidates", tmp_path / "prepared"
    prepared.mkdir()
    rubric = {"id": "rubric:fixture", "status": "proposed"}
    instructions = {"questions": QUESTIONS}
    original = candidates / "laya-contrast-pilot-v1"
    R.write(original, "bundle.json", {"rubric": rubric})
    groups = {}
    for cohort in G.COHORTS:
        source_dir = original if cohort == "pilot" else candidates / "laya-contrast-dev-v1"
        raw = f"Saved fictional source for {cohort}."
        path = source_dir / "sources" / (cohort + ".txt")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw, encoding="utf-8")
        span_id, source_id = "span:" + cohort, "source:" + cohort
        pairs = []
        for i in range(2):
            pair = {"pair_id": f"pair:{cohort}:{i}", "family_id": f"family:{cohort}:{i}", "parents": [],
                    "authored_at": "2026-10-11T12:00:00Z", "context": "Fictional test only.",
                    "context_evidence": [span_id], "a": {"text": f"Observation {cohort} {i}.", "language": "en", "evidence": [span_id]},
                    "b": {"text": f"Claim {cohort} {i}.", "language": "en", "evidence": [span_id]}}
            if cohort == "native":
                pair.update(source="scifact", source_context_sha256=D.digest(raw.encode()), locator={"doc_id": str(i)},
                            evidence=[{"source_projection": "fixture", "projection_sha256": D.digest(raw.encode()),
                                       "text": raw, "start": 0, "end": len(raw), "excerpt_sha256": D.digest(raw.encode())}])
            pairs.append(pair)
        source = {"dataset_id": "dataset:" + cohort, "rubric": rubric, "questions_hash": C.questions_hash(), "training_authorized": False, "annotations": [],
                  "pairs": pairs, "sources": [{"source_id": source_id, "origin_uri": "fixture:" + cohort,
                             "sha256": D.digest(raw.encode()), "snapshot_path": "sources/" + cohort + ".txt"}],
                  "spans": [{"span_id": span_id, "source_id": source_id, "start": 0, "end": len(raw),
                             "excerpt_sha256": D.digest(raw.encode())}]}
        if cohort == "pilot":
            directory = candidates / "laya-contrast-revision-v2"
            R.write(directory, "bundle.json", source)
            R.write(directory, "selection.json", {"active_pair_hashes": {p["pair_id"]: D.fingerprint(p) for p in pairs}})
            queue = R.hydrated_queue(source, pairs, original)
        elif cohort == "development":
            R.write(source_dir, "bundle.json", source)
            queue = R.hydrated_queue(source, pairs, source_dir)
        else:
            native = copy.deepcopy(source)
            native.pop("rubric")
            R.write(prepared, "native-bundle.json", native)
            queue = C.native_queue(source, rubric["id"])
        C.review_session(source, queue, instructions, prepared / cohort)
        groups[cohort] = {"cases": 2, "session_sha256": D.fingerprint(read_json(prepared / cohort / "session.json"))}
    receipt = {"schema": C.SCHEMA, "files": R.inventory(prepared), "review_groups": groups}
    R.write(prepared, "manifest.json", receipt)
    R.write(candidates / "laya-candidate-context-v1", "preparation-receipt.json", receipt)
    # 후보 자체의 검증은 별도 테스트에서 다룬다. 여기서는 이력과 제출 행을 검증한다.
    monkeypatch.setattr(C, "verify", lambda _: {"verified": True})
    return prepared, candidates


def read_json(path):
    return json.loads(Path(path).read_bytes())


def payload(prepared, candidates, cohort="pilot", version=None, stage="author", actor="fixture:alice", value=1):
    ledger, queue, initial, _ = G.load(prepared, cohort, candidates, version)
    data = G.session(ledger, queue, initial, stage)
    case = data["cases"][0]
    return {"schema": H.SUBMISSION_SCHEMA, "bundle_sha256": data["bundle_sha256"], "session_sha256": D.fingerprint(data),
            "stage": stage, "actor": actor, "human_attestation": True, "entries": [{"pair_id": case["pair_id"],
            "pair_sha256": case["pair_sha256"], "reviews": case["reviews"], "status": "submitted",
            "at": datetime.now(timezone.utc).isoformat(), "labels": dict.fromkeys(C.PREDICATES, value),
            "rationale": dict.fromkeys(C.PREDICATES, "가상 검증 판정이며 실제 입력이 아님."), "evidence_strength": "direct"}]}


def submit(tmp_path, cohorts, data, name, cohort="pilot", version=None):
    prepared, candidates = cohorts
    path = tmp_path / (name + "-submission.json")
    path.write_bytes(D.encoded(data))
    out = tmp_path / name
    result = G.ingest(prepared, cohort, path, out, candidates, version)
    return out, result


@pytest.mark.parametrize("cohort", G.COHORTS)
def test_first_saved_download_remains_compatible_and_blank(cohorts, tmp_path, cohort):
    prepared, candidates = cohorts
    page = tmp_path / (cohort + "-page")
    receipt = G.prepare(prepared, cohort, "author", page, candidates)
    assert (page / "session.json").read_bytes() == (prepared / cohort / "session.json").read_bytes()
    assert receipt["state_counts"] == {"unannotated": 2} and receipt["exportable_pairs"] == 0
    first, result = submit(tmp_path, cohorts, payload(prepared, candidates, cohort), cohort + "-v1", cohort)
    assert result["summary"]["state_counts"] == {"independent_review_pending": 1, "unannotated": 1}
    assert G.load(prepared, cohort, candidates, first)[0]["annotations"][0]["actor_kind"] == "human"
    assert result["summary"]["exportable_pairs"] == 0 and not result["summary"]["actor_identity_authenticated"]


@pytest.mark.parametrize("cohort", G.COHORTS)
def test_blind_review_disagreement_adjudication_and_raw_history(cohorts, tmp_path, cohort):
    prepared, candidates = cohorts
    one, _ = submit(tmp_path, cohorts, payload(prepared, candidates, cohort), "v1", cohort)
    ledger, queue, initial, _ = G.load(prepared, cohort, candidates, one)
    review = G.session(ledger, queue, initial, "review")
    assert "prior_judgments" not in review["cases"][0] and "labels" not in review["cases"][0]
    assert G.prepare(prepared, cohort, "review", tmp_path / "review", candidates, one)["session_cases"] == 1
    two, result = submit(tmp_path, cohorts, payload(prepared, candidates, cohort, version=one, stage="review", actor="fixture:bob", value=0), "v2", cohort, version=one)
    assert result["summary"]["state_counts"]["adjudication_pending"] == 1
    ledger, queue, initial, _ = G.load(prepared, cohort, candidates, two)
    assert len(G.session(ledger, queue, initial, "adjudication")["cases"][0]["prior_judgments"]) == 2
    assert G.prepare(prepared, cohort, "adjudication", tmp_path / "dispute", candidates, two)["session_cases"] == 1
    three, result = submit(tmp_path, cohorts, payload(prepared, candidates, cohort, version=two, stage="adjudication", actor="fixture:bob", value=0), "v3", cohort, version=two)
    assert result["summary"]["state_counts"]["resolved"] == 1
    ledger, _, _, history = G.load(prepared, cohort, candidates, three)
    assert len(history) == 3 and len(ledger["annotations"]) == 3
    assert len(list((three / "submissions").glob("*.json"))) == 3
    assert result["summary"]["exportable_pairs"] == 0


def test_unknown_draft_and_partial_batches_do_not_become_false_or_gold(cohorts, tmp_path):
    prepared, candidates = cohorts
    data = payload(prepared, candidates, value=None)
    draft = copy.deepcopy(data["entries"][0])
    draft.update(pair_id="pair:pilot:1", pair_sha256=G.load(prepared, "pilot", candidates)[1][1]["pair_sha256"], status="draft")
    data["entries"].append(draft)
    one, result = submit(tmp_path, cohorts, data, "unknown1")
    assert result["added_annotations"] == 1
    two, _ = submit(tmp_path, cohorts, payload(prepared, candidates, version=one, stage="review", actor="fixture:bob", value=None), "unknown2", version=one)
    three, result = submit(tmp_path, cohorts, payload(prepared, candidates, version=two, stage="adjudication", actor="fixture:bob", value=None), "unknown3", version=two)
    assert result["summary"]["state_counts"]["unresolved"] == 1
    assert all(v is None for v in G.load(prepared, "pilot", candidates, three)[0]["annotations"][-1]["labels"].values())


@pytest.mark.parametrize("mutation", ["attestation", "actor", "bool", "reason", "strength", "pair", "session", "bundle", "duplicate", "history", "future", "before_candidate", "draft_only"])
def test_bad_submissions_refused_before_creating_output(cohorts, tmp_path, mutation):
    prepared, candidates = cohorts
    data = payload(prepared, candidates)
    entry = data["entries"][0]
    if mutation == "attestation": data["human_attestation"] = False
    if mutation == "actor": data["actor"] = " "
    if mutation == "bool": entry["labels"]["supports"] = True
    if mutation == "reason": entry["rationale"]["supports"] = ""
    if mutation == "strength": entry["evidence_strength"] = "invented"
    if mutation == "pair": entry["pair_sha256"] = "0" * 64
    if mutation == "session": data["session_sha256"] = "0" * 64
    if mutation == "bundle": data["bundle_sha256"] = "0" * 64
    if mutation == "duplicate": data["entries"].append(copy.deepcopy(entry))
    if mutation == "history": entry["reviews"] = ["annotation:fake"]
    if mutation == "future": entry["at"] = "2099-01-01T00:00:00Z"
    if mutation == "before_candidate": entry["at"] = "2000-01-01T00:00:00Z"
    if mutation == "draft_only": entry["status"] = "draft"
    with pytest.raises(ValueError):
        submit(tmp_path, cohorts, data, "bad")
    assert not (tmp_path / "bad").exists()


def test_old_batch_cross_cohort_and_self_review_are_refused(cohorts, tmp_path):
    prepared, candidates = cohorts
    data = payload(prepared, candidates)
    one, _ = submit(tmp_path, cohorts, data, "first")
    with pytest.raises(ValueError): submit(tmp_path, cohorts, data, "old", version=one)
    with pytest.raises(ValueError): submit(tmp_path, cohorts, data, "cross", cohort="development")
    for actor in ("fixture:alice", "FIXTURE:ALICE"):
        with pytest.raises(ValueError, match="self review"):
            submit(tmp_path, cohorts, payload(prepared, candidates, version=one, stage="review", actor=actor), "self", version=one)
    with pytest.raises(ValueError): G.load(prepared, "native", candidates, one)


def test_hash_mutation_and_training_promotion_cannot_rewrite_history(cohorts, tmp_path):
    prepared, candidates = cohorts
    one, _ = submit(tmp_path, cohorts, payload(prepared, candidates), "saved")
    ledger = read_json(one / "ledger.json")
    ledger["training_authorized"] = True
    (one / "ledger.json").write_bytes(D.encoded(ledger))
    manifest = read_json(one / "manifest.json")
    manifest.update(files=R.inventory(one), ledger_sha256=D.fingerprint(ledger))
    (one / "manifest.json").write_bytes(D.encoded(manifest))
    with pytest.raises(ValueError, match="authority changed"):
        G.load(prepared, "pilot", candidates, one)


def test_changed_evidence_and_unsafe_output_refused(cohorts, tmp_path):
    prepared, candidates = cohorts
    source = candidates / "laya-contrast-pilot-v1/sources/pilot.txt"
    source.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="source hash"):
        G.load(prepared, "pilot", candidates)
    with pytest.raises(ValueError): G.new_output(prepared / "bad", prepared, candidates)
    with pytest.raises(ValueError): G.new_output(candidates / "bad", prepared, candidates)


def test_partial_saved_batch_requires_an_explicit_current_session(cohorts, tmp_path):
    prepared, candidates = cohorts
    old = payload(prepared, candidates)
    first, _ = submit(tmp_path, cohorts, old, "partial")
    ledger, queue, initial, _ = G.load(prepared, "pilot", candidates)
    second = G.session(ledger, queue, initial, "author")["cases"][1]
    old["entries"][0].update(pair_id=second["pair_id"], pair_sha256=second["pair_sha256"])
    with pytest.raises(ValueError, match="stale submission bundle"):
        submit(tmp_path, cohorts, old, "stale-other-row", version=first)
    updated, result = submit(tmp_path, cohorts, payload(prepared, candidates, version=first), "fresh", version=first)
    assert result["summary"]["state_counts"] == {"independent_review_pending": 2}
    assert len(G.load(prepared, "pilot", candidates, updated)[0]["annotations"]) == 2


@pytest.mark.parametrize("mutation", ["event", "raw", "history", "missing", "schema"])
def test_rehashed_version_still_replays_exact_submitted_rows(cohorts, tmp_path, mutation):
    prepared, candidates = cohorts
    one, _ = submit(tmp_path, cohorts, payload(prepared, candidates), "receipt")
    ledger = read_json(one / "ledger.json")
    if mutation == "event":
        event = ledger["annotations"][0]
        event["labels"]["supports"] = 0
        event["event_id"] = "annotation:" + D.fingerprint({k: v for k, v in event.items() if k != "event_id"})
        ledger["batches"][0]["event_ids"] = [event["event_id"]]
    elif mutation == "raw":
        submission = next((one / "submissions").glob("*.json"))
        data = read_json(submission)
        data["entries"][0]["labels"]["supports"] = 0
        submission.write_bytes(D.encoded(data))
    elif mutation == "history":
        snapshot = next((one / "history").glob("*.json"))
        data = read_json(snapshot)
        data["pairs"][0]["context"] = "A replacement context."
        snapshot.write_bytes(D.encoded(data))
    elif mutation == "missing":
        next((one / "sessions").glob("*.json")).unlink()
    (one / "ledger.json").write_bytes(D.encoded(ledger))
    manifest = read_json(one / "manifest.json")
    manifest.update(files=R.inventory(one), ledger_sha256=D.fingerprint(ledger), summary=G.summary(ledger, G.load(prepared, "pilot", candidates)[1]))
    if mutation == "schema": manifest["schema"] = "unsupported"
    (one / "manifest.json").write_bytes(D.encoded(manifest))
    with pytest.raises((ValueError, FileNotFoundError)):
        G.load(prepared, "pilot", candidates, one)
