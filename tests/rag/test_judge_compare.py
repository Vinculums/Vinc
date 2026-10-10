"""Offline contract and journal tests. Responses are synthetic, never model evidence."""
import copy
import json
import hashlib
from pathlib import Path

import pytest

from rag_eval import judge_compare as J
from rag_eval.laya.pins import QUESTIONS

ROOT = Path(__file__).resolve().parents[2]


def pair():
    return {"pair_id": "synthetic", "lang": "mixed", "a": {"text": "first", "id": "secret-a"},
            "b": {"text": "second", "id": "secret-b"}, "context": None,
            "labels": {"supports": 1, "contradicts": 0, "same_as": 0}, "rationale": "SECRET"}


def receipt(provider, request, model, p=0.75):
    predicates = request["predicates"]
    answers = ({pred: {"type": "noul", "noul": p} for pred in predicates} if provider == "jev"
               else [{"type": "predicate", "name": pred, "probability": p} for pred in predicates])
    return {"request_id": request["request_id"], "body_sha256": request["body_sha256"], "attempt": 1,
            "started_at": "2026-10-11T01:00:00Z", "completed_at": "2026-10-11T01:00:01Z",
            "elapsed_s": 1, "response": {"model": model, "answers": answers,
                                         "usage": {"input_tokens": 10, "output_tokens": 2}}}


@pytest.mark.parametrize("provider,model", [("jev", "jev-1.13.0"), ("openai_decisions", "gpt-6-luna")])
def test_label_free_payload_preserves_criteria_and_directions(provider, model):
    requests = J.requests_for(provider, model, "a", [pair()])
    assert [r["predicates"] for r in requests] == [list(QUESTIONS), ["contradicts", "same_as"]]
    for request in requests:
        text = json.dumps(request["body"])
        assert all(secret not in text for secret in ("SECRET", "secret-a", "secret-b", "synthetic", "labels"))
        for pred in request["predicates"]:
            q = (request["body"]["questions"][pred] if provider == "jev" else
                 next(q for q in request["body"]["questions"] if q["name"] == pred))
            if provider == "jev":
                assert q == QUESTIONS[pred]
            else:
                assert q["instructions"] == (QUESTIONS[pred]["instructions"] + "\nTrue: "
                                             + QUESTIONS[pred]["criteria"]["true"] + "\nFalse: "
                                             + QUESTIONS[pred]["criteria"]["false"])


@pytest.mark.parametrize("provider,model", [("jev", "jev-1.13.0"), ("openai_decisions", "gpt-6-luna")])
def test_normalize_and_usage_are_per_request(provider, model):
    requests = J.requests_for(provider, model, "a", [pair()])
    receipts = [receipt(provider, r, model) for r in requests]
    rows, usage = J.normalize(provider, model, requests, receipts, [pair()])
    assert len(rows) == 5 and usage["input_tokens"] == 20 and usage["requests"] == 2
    assert rows[0]["label"] == 1 and rows[0]["p_raw"] == 0.75 and rows[0]["p_cal"] is None
    assert rows[0]["logits_kind"] == "probability_pseudo_logits"


@pytest.mark.parametrize("value", [True, False, -0.1, 1.1, float("nan"), float("inf"), "0.5", None])
def test_invalid_probabilities_stop_import(value):
    req = J.requests_for("jev", "jev-1.13.0", "a", [pair()])[0]
    rec = receipt("jev", req, "jev-1.13.0", value)
    with pytest.raises(ValueError):
        J.probabilities("jev", rec["response"], req["predicates"], "jev-1.13.0")


@pytest.mark.parametrize("change", ["missing", "extra", "duplicate", "refusal", "model", "usage"])
def test_openai_malformed_answers_are_not_silently_dropped(change):
    req = J.requests_for("openai_decisions", "gpt-6-luna", "a", [pair()])[0]
    response = receipt("openai_decisions", req, "gpt-6-luna")["response"]
    if change == "missing":
        response["answers"].pop()
    elif change == "extra":
        response["answers"].append({"name": "other", "type": "predicate", "probability": 0.5})
    elif change == "duplicate":
        response["answers"].append(copy.deepcopy(response["answers"][0]))
    elif change == "refusal":
        response["answers"][0]["type"] = "refusal"
    elif change == "model":
        response["model"] = "changed"
    else:
        response["usage"]["input_tokens"] = True
    with pytest.raises(ValueError):
        J.probabilities("openai_decisions", response, req["predicates"], "gpt-6-luna")


@pytest.mark.parametrize("change", ["missing", "duplicate", "hash", "retry", "time", "elapsed", "freeze"])
def test_request_receipts_are_complete_and_after_freeze(change):
    requests = J.requests_for("jev", "jev-1.13.0", "a", [pair()])
    receipts = [receipt("jev", r, "jev-1.13.0") for r in requests]
    frozen_at = None
    if change == "missing":
        receipts.pop()
    elif change == "duplicate":
        receipts.append(receipts[0])
    elif change == "hash":
        receipts[0]["body_sha256"] = "wrong"
    elif change == "retry":
        receipts[0]["attempt"] = 2
    elif change == "time":
        receipts[0]["completed_at"] = "2026-10-10T00:00:00Z"
    elif change == "elapsed":
        receipts[0]["elapsed_s"] = 5
    else:
        frozen_at = "2026-10-11T01:00:00Z"
    with pytest.raises(ValueError):
        J.normalize("jev", "jev-1.13.0", requests, receipts, [pair()], frozen_at=frozen_at)


@pytest.mark.parametrize("p", [0.0, 1.0])
def test_probability_endpoints_keep_original_and_finite_pseudo_logits(p):
    requests = J.requests_for("jev", "jev-1.13.0", "a", [pair()])
    rows, _ = J.normalize("jev", "jev-1.13.0", requests,
                          [receipt("jev", r, "jev-1.13.0", p) for r in requests], [pair()])
    assert rows[0]["p_provider"] == p and rows[0]["probability_clipped"]
    assert all(J.number(x, -100, 0) == x for x in rows[0]["logits"])


def test_frozen_corpus_preparation_and_mutation_guards(tmp_path):
    contract = json.loads(J.CONTRACT.read_text(encoding="utf-8"))
    sets = J.check_contract(ROOT, contract)
    assert {name: len(pairs) for name, pairs in sets.items()} == {"calib": 120, "a": 173, "b": 150, "c": 140, "d": 45}
    assert sum(2 * len(pairs) for pairs in sets.values()) == 1256
    assert sum(5 * len(pairs) for pairs in sets.values()) == 3140
    for provider in contract["providers"]:
        directory = tmp_path / provider
        assert J.main(["prepare", "--root", str(ROOT), "--directory", str(directory), "--provider", provider]) == 0
        assert len(J.read_jsonl(directory / "requests-calib.jsonl")) == 240
        assert not (directory / "requests-a.jsonl").exists()
        with pytest.raises(FileExistsError):
            J.main(["prepare", "--root", str(ROOT), "--directory", str(directory), "--provider", provider])
        with pytest.raises(ValueError, match="six"):
            J.main(["release-eval", "--root", str(ROOT), "--directory", str(directory)])
    changed = copy.deepcopy(contract)
    changed["sets"]["a"]["split_sha256"] = "changed"
    with pytest.raises(ValueError, match="hash"):
        J.check_contract(ROOT, changed)
    changed = copy.deepcopy(contract)
    changed["gates"]["coverage_min"] = 0.4
    with pytest.raises(ValueError, match="gate"):
        J.check_contract(ROOT, changed)


def test_duplicate_jev_json_keys_stop_before_parsing(tmp_path):
    journal = tmp_path / "journal.jsonl"
    journal.write_text('{"response":{"answers":{"same_as":0.3,"same_as":0.8}}}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        J.read_journal(journal)


def test_temperature_mixed_routing_report_and_audit(tmp_path):
    from rag_eval.laya import report
    model = "jev-1.13.0"
    requests = J.requests_for("jev", model, "d", [pair()])
    calibration = {(p, lang): {"temperature": 2 if lang == "en" else 1,
                               "calibration_id": f"{p}:{lang}", "min_confidence": 0.55}
                   for p in QUESTIONS for lang in ("en", "ko")}
    rows, usage = J.normalize("jev", model, requests, [receipt("jev", r, model) for r in requests],
                              [pair()], calibration, "2026-10-10T23:00:00Z")
    assert rows[0]["calibration_id"] == "supports:en"
    assert rows[0]["p_cal"] == pytest.approx(0.6339745962)
    assert J.audit_counts(rows) == {"false": {"n": 1, "accepted": 1, "ab_p_ge_half": 1},
                                    "true": {"n": 0, "accepted": 0, "ab_p_ge_half": 0}}
    inp, pins, out = tmp_path / "fixture.jsonl", tmp_path / "pins.json", tmp_path / "report.md"
    provenance = {"provider": "synthetic_fixture", "model": model}
    J.write_rows(inp, rows, {"pins": provenance})
    J.write_json(pins, provenance)
    report.main(["--in", str(inp), "--out", str(out), "--provenance", str(pins)])
    result = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert result["pins"] == provenance and result["gate"] == {"auroc_margin": 0.05, "ece_max": 0.1, "coverage_min": 0.5}
    assert "laya==" not in json.dumps(result["pins"]) and result["rows"] == 5


def test_freeze_releases_eval_once_and_rejects_changed_calibration(tmp_path):
    directory = tmp_path / "prepared"
    J.main(["prepare", "--root", str(ROOT), "--directory", str(directory), "--provider", "jev"])
    requests = J.read_jsonl(directory / "requests-calib.jsonl")
    journal = tmp_path / "synthetic-journal.jsonl"
    J.write_rows(journal, [receipt("jev", r, "jev-1.13.0") for r in requests], {"synthetic_fixture": True})
    J.main(["import", "--root", str(ROOT), "--directory", str(directory), "--set", "calib", "--journal", str(journal)])
    rows = J.read_jsonl(directory / "calib.jsonl")
    pins = json.loads((directory / "provenance.json").read_text(encoding="utf-8"))
    (directory / "calibration").mkdir()
    for pred in QUESTIONS:
        for lang in ("en", "ko"):
            entry = {"predicate": pred, "lang": lang, "n": sum(r["predicate"] == pred and
                     ("en" if r["lang"] == "mixed" else r["lang"]) == lang for r in rows),
                     "seed": 20261007, "questions_hash": J.questions_hash(), "temperature": 1,
                     "min_confidence": 0.9, "source_input_sha256": J.file_hash(directory / "calib.jsonl"),
                     "min_bucket_n": 30}
            entry["calibration_id"] = hashlib.sha256(json.dumps(entry, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
            J.write_json(directory / "calibration" / f"calib-{pred}-{lang}.json", {"pins": pins, **entry})
    J.main(["release-eval", "--root", str(ROOT), "--directory", str(directory)])
    manifest, _ = J.prepared(directory, ROOT)
    J.verified_calibration(directory, manifest)
    assert {name: len(J.read_jsonl(directory / f"requests-{name}.jsonl")) for name in "abcd"} == {
        "a": 346, "b": 300, "c": 280, "d": 90}
    with pytest.raises(FileExistsError):
        J.main(["release-eval", "--root", str(ROOT), "--directory", str(directory)])
    path = directory / "calibration/calib-supports-ko.json"
    changed = json.loads(path.read_text(encoding="utf-8"))
    changed["temperature"] = 2
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        J.verified_calibration(directory, manifest)
