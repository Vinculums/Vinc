"""가상 후보로 출처 경계, 미확정 배제와 통제 비교의 항목 보존을 검증한다."""
import copy
from collections import Counter
from datetime import datetime, timezone

import pytest

from rag_eval.laya import curation as C, ai_candidates as A
from rag_eval.laya.pins import QUESTIONS


def row(name, cohort="pilot", y=1):
    return {"pair_id":name,"pair_sha256":name+"-hash","evidence_sha256":name+"-evidence",
            "family_id":"family:"+name,"cohort":cohort,"cohort_role":"dev" if cohort=="development" else "train-candidate",
            "a":"first assertion "+name,"b":"second assertion "+name,"context":"fictional",
            "evidence":[{"origin_uri":"fixture:"+name,"source_sha256":name+"-source"}],
            "rubric_id":"rubric:fixture","label_tier":A.TIER,"labels":dict.fromkeys(C.PREDICATES,y),
            "language_cell":"en_en","hard_target_candidate":True,"evidence_strength":"direct",
            "training_authorized":False,"human_review":False,"independent_review":False,
            "self_review_event_id":"event:"+name}


def sources(rows):
    return [{"origin_uri":e["origin_uri"],"source_sha256":e["source_sha256"],
             "training_execution_authorized":False,"production_authorized":False,"prior_training_status":"pending",
             "scope":"local_development_preparation","rubric_id":r["rubric_id"],"basis":"Fictional permission fixture",
             "rights_ref":"fixture:rights","selected_for_preparation":True} for r in rows for e in r["evidence"]]


def note(r):
    return {"pair_id":r["pair_id"],"pair_sha256":r["pair_sha256"],"evidence_sha256":r["evidence_sha256"],
            "reviews":r["self_review_event_id"],"actor":"codex:oc-20261015-vin511-1:primary",
            "independent":False,"action":"retain_exclusion","labels":copy.deepcopy(r["labels"]),
            "reason":"A narrower observation cannot establish the all-system target.","rule":"scope",
            "at":datetime.now(timezone.utc).isoformat()}


def test_unknown_remains_excluded_and_development_role_preserved():
    rows=[row("train"),row("unknown"),row("dev","development")]
    rows[1]["labels"]["supports"]=None;rows[1]["hard_target_candidate"]=False
    before=copy.deepcopy(rows)
    C.validate_dispositions(rows,[note(rows[1])])
    train,dev,excluded,audit=C.select(rows,sources(rows))
    assert [r["pair_id"] for r in train]==["train"]
    assert [r["pair_id"] for r in dev]==["dev"] and excluded==["unknown"]
    assert rows==before and not any(audit["exact_overlaps"].values())
    assert not audit["semantic_cross_language_independence_proven"]


@pytest.mark.parametrize("kind",["family_id","endpoint","origin_uri","source_sha256"])
def test_cross_role_leakage_refused(kind):
    rows=[row("train"),row("dev","development")]
    if kind=="endpoint":rows[1]["b"]="  FIRST ASSERTION train "
    elif kind in ("origin_uri","source_sha256"):rows[1]["evidence"][0][kind]=rows[0]["evidence"][0][kind]
    else:rows[1][kind]=rows[0][kind]
    with pytest.raises(ValueError):C.select(rows,sources(rows))


@pytest.mark.parametrize("field,value",[("training_authorized",True),("human_review",True),("independent_review",True),
    ("cohort_role","dev"),("label_tier","human-gold"),("cohort","invented")])
def test_candidate_authority_refused(field,value):
    r=row("train");r[field]=value
    with pytest.raises(ValueError):C.select([r],sources([r]))


@pytest.mark.parametrize("field,value",[("source_sha256","changed"),("training_execution_authorized",True),
    ("production_authorized",True),("prior_training_status","approved"),("rubric_id","changed"),("scope","production")])
def test_source_authority_and_bindings_refused(field,value):
    r=row("train");s=sources([r]);s[0][field]=value
    with pytest.raises(ValueError):C.select([r],s)


@pytest.mark.parametrize("change",["missing","duplicate","label","reference","actor","future","hash"])
def test_unknown_disposition_cannot_coerce_label_or_fabricate_review(change):
    r=row("unknown");r["labels"]["supports"]=None;r["hard_target_candidate"]=False
    notes=[note(r)]
    if change=="missing":notes=[]
    elif change=="duplicate":notes*=2
    elif change=="label":notes[0]["labels"]["supports"]=0
    elif change=="reference":notes[0]["reviews"]="unrelated"
    elif change=="actor":notes[0]["independent"]=True
    elif change=="future":notes[0]["at"]="2099-01-01T00:00:00Z"
    else:notes[0]["evidence_sha256"]="changed"
    with pytest.raises(ValueError):C.validate_dispositions([r],notes)


def test_false_source_selection_excludes_otherwise_defined_row():
    r=row("train");s=sources([r]);s[0]["selected_for_preparation"]=False
    train,dev,excluded,_=C.select([r],s)
    assert train==dev==[] and excluded==["train"]


def test_hard_target_flag_cannot_hide_null_or_bool():
    for value in (None,True):
        r=row("train");r["labels"]["supports"]=value
        with pytest.raises(ValueError):C.select([r],sources([r]))


def test_matched_comparison_replaces_only_supports_with_equal_class_budget():
    core=[{"state":"fictional state "+str(y),"questions":QUESTIONS,
           "gold":{p:{"probabilities":{"false":float(1-y),"true":float(y)}} for p in C.PREDICATES},
           "meta":{"pair_id":"core:"+str(y),"direction":"ab"}} for y in (0,1)]
    candidates=[row("new-negative",y=0),row("new-positive",y=1)]
    first=C.comparison_draws(core,candidates)
    assert first==C.comparison_draws(core,list(reversed(candidates)))
    assert first["replacement_items"]==252 and first["unchanged_items"]==2760
    assert Counter(r["target"] for r in first["replacements"])=={0:126,1:126}
    base,_=C.I.balanced_rows(core,[],"core",20261007)
    for item in first["replacements"]:
        assert list(base[item["position"]]["gold"])==["supports"]
        assert base[item["position"]]["gold"]["supports"]["probabilities"]["true"]==item["target"]
        assert item["direction"]=="AB" and not item["training_authorized"]


def test_draft_input_keeps_unreplaced_rows_and_has_no_supports_ba():
    core=[{"state":"fictional state "+str(y),"questions":QUESTIONS,
           "gold":{p:{"probabilities":{"false":float(1-y),"true":float(y)}} for p in C.PREDICATES},
           "meta":{"pair_id":"core:"+str(y),"direction":"ab"}} for y in (0,1)]
    train=[row("positive"),row("negative",y=0)]
    dev=[row("holdout","development")]
    result=C.draft_inputs(core,train,dev)
    changed=[(a,b) for a,b in zip(result["control"],result["intervention"]) if a!=b]
    assert len(changed)==252
    assert all(list(b["questions"])==["supports"] for a,b in changed)
    assert sum(len(r["questions"]) for r in result["development"])==5
    assert "supports" not in result["development"][1]["questions"]
    assert not result["training_execution_authorized"]
    dev[0]["context"]="x"*401
    with pytest.raises(ValueError,match="truncated"):C.draft_inputs(core,train,dev)


def test_preparation_registry_is_rejected_by_existing_training_runner():
    from rag_eval.laya.development import validate_registration
    with pytest.raises(ValueError,match="registration"):
        validate_registration({"schema":C.SCHEMA,"training_execution_authorized":False})
