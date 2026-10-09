"""채움 비율의 분모·기권·출력 계약과 재개 시 입력 불변을 검사한다."""
from copy import deepcopy
import json
import math

import pytest

from rag_eval.laya.fill_candidates import ReadHub, census
from rag_eval.laya.fill_curve import (PREDICATES, belief_fragment, completed, decision,
                                      freeze, summarize)


def pair(pid="one", kind="concept"):
    return {"pair_id": pid, "a": {"id": "concept:a", "rev": 3, "domain": "vinc/ai/eval", "text": "A"},
            "b": {"id": kind + ":b", "rev": 4, "domain": "vinc/ai/eval", "text": "B"},
            "lang": "en", "sources": ["search", "ties"], "context_ids": []}


def calibration(th=.8):
    return {(pred,lang): {"temperature": 1, "calibration_id": pred+lang, "min_confidence": th}
            for pred in PREDICATES for lang in ("ko","en")}


def directions(p=.1, reverse=.1, pid="one"):
    return {direction: {"pair_id": pid, "direction": direction, "seconds": .2,
                        "truncated": False, "logits": {pred: [math.log(1-prob), math.log(prob)] for pred in PREDICATES}}
            for direction,prob in (("ab",p), ("ba",reverse))}


def test_negative_judgment_is_coverage_and_keeps_both_direction_estimates():
    p = pair()
    result = decision(p, "same_as", directions(.1,.2), calibration())
    assert result["accepted"] and not result["positive"]
    fragment = belief_fragment(p,"same_as",result,run_id="test",model_sha="abc",day="2026-10-09")
    props = fragment["nodes"][-1]["props"]
    assert props["p"] == pytest.approx(.1)
    assert props["directional_probabilities"] == pytest.approx({"ab":.1,"ba":.2})
    assert sum(v["p"] for v in props["distribution"]) == pytest.approx(1)
    assert props["evidence_ids"] == ["concept:a", "concept:b"]
    assert props["rev_seen"] == {"concept:a":3,"concept:b":4}
    assert len(fragment["edges"]) == 2


@pytest.mark.parametrize("kind", ["record", "decision"])
def test_unsupported_carriers_are_accounted_without_model_results(kind):
    assert decision(pair(kind=kind),"same_as",{},calibration())["reasons"] == ["unsupported_carrier"]


@pytest.mark.parametrize("a,b,threshold,reason", [(.55,.55,.8,"below_threshold"),
    (.95,.2,.8,"asymmetry"), (.9,.9,None,"missing_threshold")])
def test_each_frozen_gate_abstains(a,b,threshold,reason):
    result = decision(pair(),"contradicts",directions(a,b),calibration(threshold))
    assert not result["accepted"] and reason in result["reasons"]


def test_reverse_truncation_abstains_even_when_ab_confident():
    dirs = directions()
    dirs["ba"]["truncated"] = True
    assert decision(pair(),"same_as",dirs,calibration())["reasons"] == ["truncated"]


def test_missing_direction_is_an_incomplete_run_not_a_model_abstention():
    with pytest.raises(ValueError,match="incomplete measurement"):
        decision(pair(),"same_as",{"ab":directions()["ab"]},calibration())


def test_mixed_language_uses_frozen_english_temperature():
    p, cal = pair(), calibration()
    p["lang"] = "mixed"
    cal[("same_as","ko")]["temperature"] = 5
    assert decision(p,"same_as",directions(.9,.9),cal)["p"] == pytest.approx(.9)


def test_summary_separates_supported_fill_from_all_candidates(tmp_path):
    pairs = [pair(), pair("unsupported", "record")]
    raw = list(directions().values())
    summary = summarize(pairs,raw,calibration(),tmp_path,run_id="test",model_sha="abc",day="2026-10-09")
    all_rows = [r for r in summary["table"] if r["dimension"] == "all"]
    assert len(all_rows) == 2
    for row in all_rows:
        assert row["candidates"] == 2 and row["supported"] == 1
        assert row["accepted"] == 1 and row["abstained"] == 1
        assert row["fill_rate"] == row["abstention_rate"] == .5
        assert row["supported_fill_rate"] == 1
        assert row["positive"] == 0 and row["negative"] == 1
    assert summary["generator_membership_is_overlapping"]
    assert len((tmp_path/"beliefs.jsonl").read_text().splitlines()) == 2
    assert len((tmp_path/"abstained.jsonl").read_text().splitlines()) == 2


@pytest.mark.parametrize("bad", ["missing", "duplicate", "extra"])
def test_summary_refuses_missing_repeated_or_unknown_measurements(tmp_path,bad):
    rows = list(directions().values())
    if bad == "missing": rows.pop()
    if bad == "duplicate": rows.append(deepcopy(rows[0]))
    if bad == "extra": rows += list(directions(pid="other").values())
    with pytest.raises(ValueError):
        summarize([pair()],rows,calibration(),tmp_path,run_id="test",model_sha="abc",day="2026-10-09")


def test_manifest_prevents_resuming_with_another_checkpoint_or_candidate_set(tmp_path):
    path = tmp_path/"frozen.json"
    freeze(path,{"model":"one","data":"one"})
    freeze(path,{"model":"one","data":"one"})
    for value in ({"model":"two","data":"one"},{"model":"one","data":"two"}):
        with pytest.raises(ValueError,match="manifest differs"):
            freeze(path,value)


def test_completed_directions_are_recovered_and_partial_lines_are_refused(tmp_path):
    path = tmp_path/"raw.jsonl"
    row = directions()["ab"]
    path.write_text(json.dumps(row)+"\n",encoding="utf-8")
    assert completed(path,{"one"})[1] == {("one","ab")}
    path.write_text(json.dumps(row)+"\n{" ,encoding="utf-8")
    with pytest.raises(ValueError): completed(path,{"one"})


def test_neighbor_completeness_does_not_depend_on_a_truncation_flag(tmp_path):
    hub = ReadHub("https://example.invalid","unused",tmp_path)
    hub.request = lambda path: {"count":2,"neighbors":[{"id":"concept:a"}]}
    with pytest.raises(ValueError,match="incomplete neighbors"):
        hub.neighbors("domain:vinc/ai")


def test_read_cache_never_accepts_a_mutating_post(tmp_path):
    hub = ReadHub("https://example.invalid","unused",tmp_path)
    with pytest.raises(ValueError,match="read-only"):
        hub.request("/v1/fragments",{"nodes":[]})


def test_census_refuses_an_unexplained_missing_node():
    class Hub:
        def request(self,path):
            if path == "/v1/domains":
                return {"rows":[{"domain":"vinc/ai","concepts":2}]}
            if path.startswith("/v1/diagnose"):
                return {"components": []}
            return {"docs":[],"has_more":False}
        def neighbors(self,nid,**kwargs):
            return [{"id":"concept:a","domain":"vinc/ai","rel":"IN_DOMAIN"}] if nid.startswith("domain:") else []
    with pytest.raises(ValueError,match="census incomplete"):
        census(Hub())
