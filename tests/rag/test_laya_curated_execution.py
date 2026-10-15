"""모의 실행으로 승인, 선택 및 실행 한도를 검증한다."""
import copy
import json
import subprocess
from types import SimpleNamespace

import pytest

from rag_eval.laya import curated_execution as E


def authority():
    return {"schema":E.SCHEMA,"decision":E.DECISION,"owner_instruction":"그럼 다음 단계 작업 진행",
            "training_execution_authorized":True,"registration_sha256":E.REG_SHA,
            "max_training_runs":2,"max_total_seconds":3600,"arms":list(E.ARMS),
            "paid_resources":False,"production_authorized":False,"manifest_sha256":"fixture"}


@pytest.mark.parametrize("key,value",[("training_execution_authorized",False),("decision","unapproved"),
    ("registration_sha256","changed"),("max_training_runs",3),("max_total_seconds",3601),
    ("arms",list(reversed(E.ARMS))),("paid_resources",True),("production_authorized",True)])
def test_execution_authority_cannot_expand(key,value):
    a=authority();a[key]=value
    with pytest.raises(ValueError):E.validate_authority(a)


def test_diagnostics_cannot_enter_primary_selector():
    rows=[{"questions":{"supports":{"instructions":"fixture"}},"gold":{"supports":{"probabilities":{"true":1}}},
           "meta":{"pair_id":"fixture","language":"ko_ko"}}]
    before=copy.deepcopy(rows)
    prefixed=E.prefix_diagnostics(rows,"exposed")
    assert "supports" not in prefixed[0]["questions"] and "exposed::supports" in prefixed[0]["gold"]
    assert rows==before and prefixed[0]["meta"]["language_cell"]=="ko_ko"
    history=[{"epoch":1,"development":{"supports":{"positive_precision":.5,"positive_recall":1.,"nll":.3},
               "exposed::supports":{"positive_precision":1.,"positive_recall":1.,"nll":.01}}},
             {"epoch":2,"development":{"supports":{"positive_precision":1.,"positive_recall":1.,"nll":.4},
               "exposed::supports":{"positive_precision":0.,"positive_recall":0.,"nll":8.}}}]
    assert E.I.select_checkpoint(history,E.I.SELECTION)["epoch"]==2


def test_metrics_confusion_and_language_are_from_actual_logits():
    records=[(0,[0.,3.],[0.,1.],2),(1,[0.,3.],[1.,0.],2),(2,[3.,0.],[0.,1.],2),(3,[3.,0.],[1.,0.],2)]
    metadata=[{"task":"supports","language_cell":"en_en"} for _ in records]
    result=E.enhanced_metrics(records,metadata)
    assert {k:result["supports"][k] for k in ("tp","fp","fn","tn","f1")}==dict(tp=1,fp=1,fn=1,tn=1,f1=.5)
    assert result["by_language"]["en_en"]["supports"]==result["supports"]
    with pytest.raises(ValueError):E.enhanced_metrics(records,metadata[:-1])


def outputs():
    return [{"meta":{"pair_id":str(i),"family_id":"family"+str(i//2),"task":"supports"},
             "logits":[0.,2.] if i%2 else [2.,0.],"target":[1.,0.] if i%2==0 else [0.,1.]} for i in range(8)]


def test_paired_family_bootstrap_has_zero_delta_for_same_outputs_and_refuses_mismatch():
    rows=outputs();result=E.paired_uncertainty(rows,rows)
    assert result["families"]==4 and result["f1_delta"]==0 and result["percentile_95"]==[0,0]
    changed=copy.deepcopy(rows);changed[0]["target"]=[0.,1.]
    with pytest.raises(ValueError):E.paired_uncertainty(rows,changed)


@pytest.mark.parametrize("failure",["nonzero","timeout"])
def test_failed_attempt_is_recorded_and_never_retried(tmp_path,monkeypatch,failure):
    calls=[]
    def run(command,**kwargs):
        calls.append((command,kwargs["timeout"]))
        assert command[3]=="rag_eval.laya.curated_execution"
        if failure=="timeout":raise subprocess.TimeoutExpired(command,kwargs["timeout"])
        return SimpleNamespace(returncode=7)
    monkeypatch.setattr(E.subprocess,"run",run)
    root=tmp_path/"attempt"
    with pytest.raises(ValueError,match="no retry"):
        E.supervise({"attempt_root":str(root)},authority())
    assert len(calls)==1 and calls[0][1]<=3600
    assert json.loads((root/"attempt-results.json").read_bytes())[0]["status"]==("failed" if failure=="nonzero" else "timeout")
    with pytest.raises(FileExistsError):E.supervise({"attempt_root":str(root)},authority())
    assert len(calls)==1


def test_two_successful_attempts_share_one_deadline(tmp_path,monkeypatch):
    remaining=[]
    monkeypatch.setattr(E.subprocess,"run",lambda cmd,**kw:(remaining.append(kw["timeout"]) or SimpleNamespace(returncode=0)))
    monkeypatch.setattr(E,"summarize",lambda root:{"paired_supports":{"fixture":True}})
    root=tmp_path/"attempt";E.supervise({"attempt_root":str(root)},authority())
    assert len(remaining)==2 and 0<remaining[1]<=remaining[0]<=3600
    assert len(json.loads((root/"attempt-results.json").read_bytes()))==2
