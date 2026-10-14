import copy
from pathlib import Path

import pytest
import yaml

from scripts.verify_current_hub import ready, render, verification_steps, REQUIRED


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
