import copy
from pathlib import Path

import pytest
import yaml

from scripts.verify_current_hub import ready, render, step_env, target_env, target_hub_app, verification_steps, REQUIRED


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


def _repo_copy(tmp_path, setting):
    root=Path(__file__).resolve().parents[2]
    (tmp_path/".github/workflows").mkdir(parents=True)
    (tmp_path/".github/workflows/hub-deploy.yml").write_text((root/".github/workflows/hub-deploy.yml").read_text(encoding="utf8"),encoding="utf8")
    (tmp_path/".github/container-apps-target.env").write_text(setting,encoding="utf8")
    return tmp_path


def test_hub_app_follows_the_target_setting_through_the_deploy_table(tmp_path):
    """HUB_APP 을 넘겨받지 않으면 대상 설정 한 줄을 hub-deploy 의 표로 푼다. 전환 뒤에 옛 허브를 검증하지 않는다."""
    assert target_hub_app() in ("vinc-hub","vinc-hub-vnet")
    assert target_hub_app(_repo_copy(tmp_path/"old","# 주석\nCAE=vinc-cae\n"))=="vinc-hub"
    assert target_hub_app(_repo_copy(tmp_path/"new","# 주석\nCAE=vinc-cae-vnet\n"))=="vinc-hub-vnet"


@pytest.mark.parametrize("setting",["CAE=other\n","# 비었다\n","CAE=vinc-cae\nCAE=vinc-cae-vnet\n"])
def test_unknown_or_ambiguous_target_is_refused(tmp_path,setting):
    with pytest.raises(ValueError):
        target_hub_app(_repo_copy(tmp_path,setting))
