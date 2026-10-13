"""테스트 판정과 인물은 가상이다. 실제 사람 검토 결과로 보관하지 않는다."""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from rag_eval.laya import data_contracts as D, human_review as H
from rag_eval.laya.pins import PREDICATES

PILOT = Path(__file__).resolve().parents[2] / "rag_eval/evalsets/laya-contrast-pilot-v1"


def initial():
    bundle, queue, _ = H.load(PILOT, PILOT / "bundle.json")
    return bundle, queue


def submission(bundle, queue, stage="author", actor="fixture:person-one", label=1):
    session = H.session(bundle, queue, H.read(PILOT / "review/instructions.json"), stage)
    case = session["cases"][0]
    return {"schema": H.SUBMISSION_SCHEMA, "bundle_sha256": session["bundle_sha256"],
            "session_sha256": D.fingerprint(session), "stage": stage, "actor": actor,
            "human_attestation": True, "entries": [{"pair_id": case["pair_id"],
                "pair_sha256": case["pair_sha256"], "reviews": case["reviews"], "status": "submitted",
                "at": datetime.now(timezone.utc).isoformat(), "labels": dict.fromkeys(PREDICATES, label),
                "rationale": dict.fromkeys(PREDICATES, "가상 검증 입력이며 실제 판정이 아님."), "evidence_strength": "direct"}]}


def ingest(tmp_path, bundle_path, payload, name):
    path = tmp_path / f"{name}-input.json"
    path.write_bytes(D.encoded(payload))
    out = tmp_path / name
    receipt = H.ingest(PILOT, bundle_path, path, out)
    return out / "bundle.json", receipt


def test_initial_session_is_blind_empty_and_offline(tmp_path):
    bundle, queue = initial()
    data = H.session(bundle, queue, H.read(PILOT / "review/instructions.json"), "author")
    assert len(data["cases"]) == 63
    for case in data["cases"]:
        assert not ({"labels", "rationale", "contrast", "mechanism", "prior_judgments"} & case.keys())
    receipt = H.prepare(PILOT, PILOT / "bundle.json", "author", tmp_path / "page")
    assert receipt["declared_human_annotation_events"] == 0
    assert receipt["state_counts"] == {"unannotated": 63}
    page = (tmp_path / "page/review.html").read_text(encoding="utf-8")
    assert "__SESSION_JSON__" not in page
    assert "default-src 'none'" in page and "fetch(" not in page
    assert 'value="1" selected' not in page
    with pytest.raises(FileExistsError):
        H.prepare(PILOT, PILOT / "bundle.json", "author", tmp_path / "page")


def test_author_review_disagreement_adjudication_and_history(tmp_path):
    bundle, queue = initial()
    first, receipt = ingest(tmp_path, PILOT / "bundle.json", submission(bundle, queue), "v1")
    assert receipt["state_counts"] == {"independent_review_pending": 1, "unannotated": 62}
    assert receipt["exportable_pairs"] == 0
    b1, q1, _ = H.load(PILOT, first)
    blind = H.session(b1, q1, H.read(PILOT / "review/instructions.json"), "review")
    assert "prior_judgments" not in blind["cases"][0]
    assert "labels" not in blind["cases"][0]
    second, receipt = ingest(tmp_path, first, submission(b1, q1, "review", "fixture:person-two", 0), "v2")
    assert receipt["state_counts"]["adjudication_pending"] == 1
    b2, q2, _ = H.load(PILOT, second)
    adjustment = H.session(b2, q2, H.read(PILOT / "review/instructions.json"), "adjudication")
    assert len(adjustment["cases"][0]["prior_judgments"]) == 2
    third, receipt = ingest(tmp_path, second, submission(b2, q2, "adjudication", "fixture:person-three", 0), "v3")
    b3, _, chain = H.load(PILOT, third)
    assert len(chain) == 3 and b3["annotations"][:2] == b2["annotations"]
    assert receipt["state_counts"]["resolved"] == 1
    assert receipt["exportable_pairs"] == 0
    assert not receipt["actor_identity_authenticated"]
    assert H.read(PILOT / "bundle.json")["annotations"] == []


def test_agreeing_review_resolves_but_unknown_stays_unresolved(tmp_path):
    bundle, queue = initial()
    first, _ = ingest(tmp_path, PILOT / "bundle.json", submission(bundle, queue, label=None), "null1")
    b, q, _ = H.load(PILOT, first)
    second, receipt = ingest(tmp_path, first, submission(b, q, "review", "fixture:person-two", None), "null2")
    assert receipt["state_counts"]["adjudication_pending"] == 1
    b, q, _ = H.load(PILOT, second)
    _, receipt = ingest(tmp_path, second, submission(b, q, "adjudication", "fixture:person-three", None), "null3")
    assert receipt["state_counts"]["unresolved"] == 1


@pytest.mark.parametrize("mutation", ["actor", "attestation", "pair", "bundle", "session", "duplicate", "future", "bool", "reason", "reviews", "empty"])
def test_bad_submissions_fail_before_output(tmp_path, mutation):
    bundle, queue = initial()
    payload = submission(bundle, queue)
    entry = payload["entries"][0]
    if mutation == "actor": payload["actor"] = " "
    if mutation == "attestation": payload["human_attestation"] = False
    if mutation == "pair": entry["pair_sha256"] = "0" * 64
    if mutation == "bundle": payload["bundle_sha256"] = "0" * 64
    if mutation == "session": payload["session_sha256"] = "0" * 64
    if mutation == "duplicate": payload["entries"].append(copy.deepcopy(entry))
    if mutation == "future": entry["at"] = "2099-01-01T00:00:00Z"
    if mutation == "bool": entry["labels"]["supports"] = True
    if mutation == "reason": entry["rationale"]["supports"] = ""
    if mutation == "reviews": entry["reviews"] = ["fixture:invented"]
    if mutation == "empty": entry["status"] = "draft"
    with pytest.raises(ValueError):
        ingest(tmp_path, PILOT / "bundle.json", payload, "bad")
    assert not (tmp_path / "bad").exists()


def test_self_review_and_reimport_stale_session_refused(tmp_path):
    bundle, queue = initial()
    payload = submission(bundle, queue)
    first, _ = ingest(tmp_path, PILOT / "bundle.json", payload, "v1")
    with pytest.raises(ValueError, match="stale"):
        ingest(tmp_path, first, payload, "repeat")
    b, q, _ = H.load(PILOT, first)
    with pytest.raises(ValueError, match="self review"):
        ingest(tmp_path, first, submission(b, q, "review"), "self")


def test_rewritten_history_refused_even_with_refreshed_manifest(tmp_path):
    bundle, queue = initial()
    first, _ = ingest(tmp_path, PILOT / "bundle.json", submission(bundle, queue), "v1")
    manifest_path = first.parent / "manifest.json"
    manifest = H.read(manifest_path)
    history_path = first.parent / "history" / (D.fingerprint(bundle) + ".json")
    history = H.read(history_path)
    history["annotations"] = [{"event_id": "fixture:rewritten"}]
    history_path.write_bytes(D.encoded(history))
    manifest["files"][history_path.relative_to(first.parent).as_posix()] = D.digest(history_path.read_bytes())
    manifest_path.write_bytes(D.encoded(manifest))
    with pytest.raises(ValueError, match="history snapshot hash"):
        H.load(PILOT, first)


def test_changed_source_and_unfinished_version_refused(tmp_path):
    bundle, queue = initial()
    first, _ = ingest(tmp_path, PILOT / "bundle.json", submission(bundle, queue), "v1")
    source = D.safe_file(first.parent, bundle["sources"][0]["snapshot_path"])
    source.write_bytes(source.read_bytes() + b"changed")
    manifest_path = first.parent / "manifest.json"
    manifest = H.read(manifest_path)
    manifest["files"][source.relative_to(first.parent).as_posix()] = D.digest(source.read_bytes())
    manifest_path.write_bytes(D.encoded(manifest))
    with pytest.raises(ValueError, match="snapshot hash mismatch"):
        H.load(PILOT, first)
    manifest_path.unlink()
    with pytest.raises(FileNotFoundError):
        H.load(PILOT, first)


def test_draft_is_not_annotation_and_agreeing_review_resolves(tmp_path):
    bundle, queue = initial()
    payload = submission(bundle, queue)
    draft = copy.deepcopy(payload["entries"][0])
    draft.update(pair_id=queue[1]["pair_id"], pair_sha256=queue[1]["pair_sha256"], status="draft")
    payload["entries"].append(draft)
    first, receipt = ingest(tmp_path, PILOT / "bundle.json", payload, "v1")
    assert receipt["added_annotations"] == 1
    b, q, _ = H.load(PILOT, first)
    with pytest.raises(ValueError, match="previous version"):
        H.prepare(PILOT, first, "review", first.parent / "inside")
    _, receipt = ingest(tmp_path, first, submission(b, q, "review", "fixture:person-two"), "v2")
    assert receipt["state_counts"]["resolved"] == 1
    assert receipt["ever_disagreed_cases"] == 0
    assert receipt["exportable_pairs"] == 0


def test_outputs_cannot_enter_frozen_pilot():
    with pytest.raises(ValueError, match="frozen pilot"):
        H.prepare(PILOT, PILOT / "bundle.json", "author", PILOT / "forbidden-output")


def test_script_closing_source_text_is_escaped(tmp_path, monkeypatch):
    bundle, queue = initial()
    queue[0]["a"] = "</script><img src=x onerror=alert(1)>"
    monkeypatch.setattr(H, "load", lambda *_: (bundle, queue, {}))
    H.prepare(PILOT, PILOT / "bundle.json", "author", tmp_path / "safe")
    page = (tmp_path / "safe/review.html").read_text(encoding="utf-8")
    assert "</script><img" not in page
    assert "\\u003c/script>" in page
