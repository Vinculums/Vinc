import copy
from pathlib import Path

import pytest
import yaml

from scripts.verify_current_hub import ready, render, step_env, target_env, verification_steps, REQUIRED


def test_ready_requires_exact_image_and_ready_latest_revision():
    app={"properties":{"template":{"containers":[{"image":"expected"}]},
                       "provisioningState":"Succeeded","latestRevisionName":"new","latestReadyRevisionName":"new"}}
    assert ready(app,"expected")
    for field,value in [("provisioningState","Updating"),("latestReadyRevisionName","old"),("latestRevisionName",None)]:
        changed=copy.deepcopy(app);changed["properties"][field]=value
        assert not ready(changed,"expected")
    assert not ready(app,"other-image")


def test_reuses_every_canonical_verification_without_deploy_or_prune():
    source=Path(__file__).resolve().parents[2]/".github/workflows/hub-deploy.yml"
    steps=verification_steps(yaml.safe_load(source.read_text(encoding="utf8")))
    assert [s["name"] for s in steps]==list(REQUIRED)
    for step in steps:
        script=render(step["run"])
        assert "az containerapp update" not in script
        assert "revision deactivate" not in script
        assert "acr repository delete" not in script
    missing=yaml.safe_load(source.read_text(encoding="utf8"))
    missing["jobs"]["build-and-deploy"]["steps"]=[s for s in missing["jobs"]["build-and-deploy"]["steps"] if s.get("name")!=REQUIRED[0]]
    with pytest.raises(ValueError,match="missing"):
        verification_steps(missing)


def test_credentials_are_environment_references_never_embedded_values():
    assert render('Bearer ${{ secrets.VINC_MCP_TOKEN }}')=='Bearer $VINC_MCP_TOKEN'
    with pytest.raises(ValueError):
        render('${{ secrets.UNREVIEWED_KEY }}')
    with pytest.raises(ValueError):
        render('${{ vars.UNKNOWN }}')


def test_every_canonical_step_environment_resolves():
    """시간 검색 단계가 env 로 리포 변수 VINC_GRAPH_SPACE 를 읽어서, 비밀만 받던 해석이 그 단계에서 멈췄다."""
    source=Path(__file__).resolve().parents[2]/".github/workflows/hub-deploy.yml"
    environment={"VINC_MCP_TOKEN":"t","VINC_GRAPH_READONLY_KEY":"k","VINC_GRAPH_SPACE":"s"}
    for step in verification_steps(yaml.safe_load(source.read_text(encoding="utf8"))):
        selected=step_env(step,environment)
        for name,value in (step.get("env") or {}).items():
            assert selected[name] in environment.values(),(step["name"],name)


def test_unreviewed_variables_and_expressions_are_refused():
    with pytest.raises(ValueError):
        step_env({"env":{"X":"${{ vars.UNKNOWN }}"}},{})
    with pytest.raises(ValueError):
        step_env({"env":{"X":"${{ github.token }}"}},{})
    with pytest.raises(ValueError):
        step_env({"env":{"X":"literal"}},{})
    assert step_env({"env":{"SPACE":"${{ vars.VINC_GRAPH_SPACE }}"}},{"VINC_GRAPH_SPACE":"abc"})["SPACE"]=="abc"


def test_replayed_steps_get_the_app_name_and_address_of_the_same_app():
    """배포 잡의 검증 단계는 HUB_APP·HUB_URL 을 읽는다. 다시 돌릴 때도 같은 앱의 실물에서 채운다."""
    app={"properties":{"configuration":{"ingress":{"fqdn":"vinc-hub.example.azurecontainerapps.io"}}}}
    assert target_env(app,"vinc-hub")=={"HUB_APP":"vinc-hub","HUB_URL":"https://vinc-hub.example.azurecontainerapps.io"}
    with pytest.raises(ValueError):
        target_env({"properties":{}},"vinc-hub")
