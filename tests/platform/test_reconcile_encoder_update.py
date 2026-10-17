import json
from types import SimpleNamespace

import pytest

from scripts.reconcile_encoder_update import update


def test_uncertain_update_is_observed_without_repeating_the_mutation():
    calls=[]
    app={"properties":{"template":{"containers":[{"image":"expected"}],"scale":{"minReplicas":1}},
                       "provisioningState":"Succeeded","latestRevisionName":"new","latestReadyRevisionName":"new"}}
    def run(command,**kwargs):
        calls.append(command)
        if "update" in command:return SimpleNamespace(returncode=1,stdout="",stderr="private diagnostic")
        if "revision" in command:return SimpleNamespace(returncode=0,stdout="Running\n")
        return SimpleNamespace(returncode=0,stdout=json.dumps(app))
    update("expected",run=run,sleep=lambda _:None)
    assert sum("update" in c for c in calls)==1


@pytest.mark.parametrize("state",["unknown","wrong_image","unready","zero_minimum"])
def test_failed_or_mismatched_observation_preserves_failure(state):
    app={"properties":{"template":{"containers":[{"image":"expected"}],"scale":{"minReplicas":1}},
                       "provisioningState":"Succeeded","latestRevisionName":"new","latestReadyRevisionName":"new"}}
    if state=="wrong_image":app["properties"]["template"]["containers"][0]["image"]="other"
    if state=="unready":app["properties"]["latestReadyRevisionName"]="old"
    if state=="zero_minimum":app["properties"]["template"]["scale"]["minReplicas"]=0
    calls=[]
    def run(command,**kwargs):
        calls.append(command)
        if "update" in command or state=="unknown":return SimpleNamespace(returncode=1,stdout="")
        return SimpleNamespace(returncode=0,stdout=json.dumps(app))
    with pytest.raises(RuntimeError,match="not verified"):
        update("expected",run=run,sleep=lambda _:None,attempts=2)
    assert sum("update" in c for c in calls)==1


def test_scale_to_zero_is_requested_and_verified_without_a_running_replica():
    calls=[]
    app={"properties":{"template":{"containers":[{"image":"expected"}],"scale":{"minReplicas":0}},
                       "provisioningState":"Succeeded","latestRevisionName":"new","latestReadyRevisionName":"new"}}
    def run(command,**kwargs):
        calls.append(command)
        if "update" in command:return SimpleNamespace(returncode=1,stdout="")
        if "revision" in command:raise AssertionError("a scaled-to-zero encoder has no running replica to check")
        return SimpleNamespace(returncode=0,stdout=json.dumps(app))
    update("expected",min_replicas=0,run=run,sleep=lambda _:None)
    update_call=next(c for c in calls if "update" in c)
    assert update_call[update_call.index("--min-replicas")+1]=="0"


def test_a_minimum_other_than_the_requested_one_is_not_verified():
    app={"properties":{"template":{"containers":[{"image":"expected"}],"scale":{"minReplicas":1}},
                       "provisioningState":"Succeeded","latestRevisionName":"new","latestReadyRevisionName":"new"}}
    def run(command,**kwargs):
        if "update" in command:return SimpleNamespace(returncode=1,stdout="")
        return SimpleNamespace(returncode=0,stdout=json.dumps(app))
    with pytest.raises(RuntimeError,match="not verified"):
        update("expected",min_replicas=0,run=run,sleep=lambda _:None,attempts=2)
