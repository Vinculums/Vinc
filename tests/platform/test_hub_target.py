"""허브 앱과 계정 앱 이름은 대상 설정 한 줄을 hub-deploy 의 표 한 줄로 푼 값이다.

지키는 것:
  - 표는 hub-deploy 의 "배포 대상" 스텝(id: target)에만 있고, 운영 워크플로·스크립트는 그 표를 읽는다.
  - 텍스트로 읽은 표가 YAML 로 읽은 스텝 본문의 표와 같다(러너 python3 에는 PyYAML 이 없다).
  - CAE 줄이 하나가 아니거나 표에 없는 값이면 멈춘다. 계정 앱이 빠진 표의 줄도 멈춘다.
  - hub-deploy·hub-image-public 밖의 워크플로가 표에 있는 앱 이름이나 허브 앱의 고정 주소를 직접 적지 않는다.
    컨테이너 이름과 이미지 저장소 이름(vinc-hub, vinc-account)은 앱 이름과 따로라 걸리지 않는다.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.hub_target import hub_table, target_account_app, target_hub_app, target_table

ROOT = Path(__file__).resolve().parents[2]


def _copy(tmp_path: Path, setting: str, deploy: str | None = None) -> Path:
    (tmp_path / ".github/workflows").mkdir(parents=True)
    text = deploy if deploy is not None else (ROOT / ".github/workflows/hub-deploy.yml").read_text(encoding="utf-8")
    (tmp_path / ".github/workflows/hub-deploy.yml").write_text(text, encoding="utf-8")
    (tmp_path / ".github/container-apps-target.env").write_text(setting, encoding="utf-8")
    return tmp_path


def _step(rows: str) -> str:
    return f"      - id: target\n        run: |\n          case x in\n{rows}          esac\n"


def test_the_text_table_is_the_deploy_step_table():
    document = yaml.safe_load((ROOT / ".github/workflows/hub-deploy.yml").read_text(encoding="utf-8"))
    script = next(step["run"] for step in document["jobs"]["plan"]["steps"] if step.get("id") == "target")
    rows = re.findall(r"(?m)^\s*([a-z0-9-]+)\) app=([a-z0-9-]+) account=([a-z0-9-]+) ;;$", script)
    expected = {cae: {"hub": hub, "account": account} for cae, hub, account in rows}
    assert expected and target_table() == expected
    assert hub_table() == {cae: apps["hub"] for cae, apps in expected.items()}


def test_the_setting_resolves_through_the_table(tmp_path):
    assert target_hub_app() in hub_table().values()
    assert target_account_app() in {apps["account"] for apps in target_table().values()}
    old = _copy(tmp_path / "old", "# 주석\nCAE=vinc-cae\n")
    new = _copy(tmp_path / "new", "# 주석\nCAE=vinc-cae-vnet\n")
    assert (target_hub_app(old), target_account_app(old)) == ("vinc-hub", "vinc-account")
    assert (target_hub_app(new), target_account_app(new)) == ("vinc-hub-vnet", "vinc-account-vnet")


@pytest.mark.parametrize("setting", ["CAE=other\n", "# 비었다\n", "CAE=vinc-cae\nCAE=vinc-cae-vnet\n"])
@pytest.mark.parametrize("resolve", [target_hub_app, target_account_app])
def test_unknown_or_ambiguous_setting_is_refused(tmp_path, setting, resolve):
    with pytest.raises(ValueError):
        resolve(_copy(tmp_path, setting))


def test_a_deploy_file_without_one_target_step_is_refused(tmp_path):
    step = _step("            vinc-cae) app=vinc-hub account=vinc-account ;;\n")
    with pytest.raises(ValueError):
        hub_table(_copy(tmp_path / "two", "CAE=vinc-cae\n", "jobs:\n  plan:\n    steps:\n" + step + step))
    with pytest.raises(ValueError):
        hub_table(_copy(tmp_path / "none", "CAE=vinc-cae\n", "jobs:\n  plan:\n    steps:\n      - id: target\n        run: echo\n"))


def test_a_row_without_the_account_app_is_refused(tmp_path):
    """계정 열이 빠진 줄을 건너뛰면 그 환경이 '표에 없음'이 되거나, 다른 줄만 남은 표로 엉뚱한 앱을 푼다."""
    step = _step("            vinc-cae) app=vinc-hub account=vinc-account ;;\n"
                 "            vinc-cae-vnet) app=vinc-hub-vnet ;;\n")
    root = _copy(tmp_path, "CAE=vinc-cae\n", "jobs:\n  plan:\n    steps:\n" + step)
    with pytest.raises(ValueError):
        target_table(root)
    with pytest.raises(ValueError):
        target_account_app(root)


def test_the_table_ends_with_its_step(tmp_path):
    deploy = ("jobs:\n  plan:\n    steps:\n" + _step("            vinc-cae) app=vinc-hub account=vinc-account ;;\n")
              + "      # 다음 스텝\n      - id: pick\n"
              "        run: |\n            other) app=not-the-hub account=not-the-account ;;\n")
    assert target_table(_copy(tmp_path, "CAE=vinc-cae\n", deploy)) == {
        "vinc-cae": {"hub": "vinc-hub", "account": "vinc-account"}}


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-I", "-S", str(ROOT / "scripts/hub_target.py"), *args],
                          capture_output=True, text=True)


def test_the_cli_runs_on_the_standard_library_alone():
    """워크플로는 출력 한 줄을 그대로 "$GITHUB_ENV" 에 붙이거나 `${line#HUB_APP=}` 로 자른다. 한 번에 한 줄이다."""
    assert _cli().stdout == f"HUB_APP={target_hub_app()}\n"
    assert _cli("hub").stdout == f"HUB_APP={target_hub_app()}\n"
    assert _cli("account").stdout == f"ACCOUNT_APP={target_account_app()}\n"
    for wrong in (("layout",), ("hub", "account")):
        out = _cli(*wrong)
        assert out.returncode == 2 and out.stdout == ""


# 표에 있는 앱의 이름을 적는 꼴. 이름 뒤에 -·.·:·글자가 이어지면 다른 것(다른 앱, 주소, 이미지 태그)이다.
_END = r"(?![-.:\w])"


def _app_name_pattern(names: set[str]) -> re.Pattern[str]:
    name = "(?:" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + ")"
    return re.compile(
        # -n NAME, --name NAME, --name=NAME, ["-n", "NAME"]
        rf"(?<![\w-])(?:-n|--name)[\s\"',=]+{name}{_END}"
        # 앱을 담는 변수·키에 넣기(APP=, HUB_APP:, echo "ACCOUNT_APP=...", target=, "app": ...)와
        # 그런 변수에 견주기(inputs.app == '...', [ "$APP" = ... ])
        rf"|(?<![\w-])(?i:[a-z_]*app|target)[\"'}}]{{0,2}}\s*(?:[:=]|[!=]==?)\s*[\"']?(?:--name[\s=]+)?{name}{_END}"
        rf"|\bcase\s+\S*(?i:app)\S*\s+in\b[^\n]*?(?<![\w-]){name}{_END}"
        rf"|\bfor app in [^\n]*(?<![\w-]){name}{_END}"
        rf"|options:\s*\[[^\]\n]*(?<![\w-]){name}{_END}"
        rf"|containerApps/{name}{_END}"
        rf"|\bAPPS\s*=\s*[(\[][^\n]*[\"']{name}[\"']"
        rf"|(?<![\w-]){name}\.[a-z0-9-]+\.[a-z0-9-]+\.azurecontainerapps\.io")


_TABLE_APPS = {app for apps in target_table().values() for app in apps.values()}
_APP_NAME = _app_name_pattern(_TABLE_APPS)
# 컨테이너 이름과 이미지 저장소 이름. 새 환경의 앱에서도 이 이름 그대로다.
_CONTAINERS = ("vinc-hub", "vinc-account")


def test_the_pattern_covers_every_app_of_the_table():
    assert _TABLE_APPS == {"vinc-hub", "vinc-hub-vnet", "vinc-account", "vinc-account-vnet"}


_NAMES_THE_APP = [
    "az containerapp show -n {app} -g vincsRG",
    'az containerapp show -n "{app}" -g vincsRG',
    "az containerapp show --name {app} -g vincsRG",
    "az containerapp show --name={app} -g vincsRG",
    'APP = ["-n", "{app}", "-g", "vincsRG"]',
    'APP: "--name {app} -g vincsRG"',
    "APP: {app}",
    "APP={app}",
    'echo "HUB_APP={app}" >> "$GITHUB_ENV"',
    'echo "ACCOUNT_APP={app}" >> "$GITHUB_ENV"',
    "account) target={app} ;;",
    'hub) target="{app}" ;;',
    "if: inputs.app == '{app}'",
    "group: ${{{{ inputs.app == '{app}' && 'hub-deploy' || 'azure-ops-account' }}}}",
    'if [ "$APP" = {app} ]; then',
    'if [ "${{APP}}" != "{app}" ]; then',
    'case "$APP" in vinc-layout|{app}) ;; *) exit 1 ;; esac',
    "for app in {app} vinc-layout; do",
    'options: ["hub", "{app}"]',
    'APPS = (HUB_APP, "{app}", "vinc-layout")',
    "/subscriptions/s/resourceGroups/vincsRG/providers/Microsoft.App/containerApps/{app}",
    "curl -sf https://{app}.grayplant-559d42e3.koreacentral.azurecontainerapps.io/mcp",
    'run(app="{app}")',
    '{{"app": "{app}"}}',
]


@pytest.mark.parametrize("app", sorted(_TABLE_APPS))
@pytest.mark.parametrize("line", _NAMES_THE_APP)
def test_these_lines_name_an_app_of_the_table(line, app):
    assert _APP_NAME.search(line.format(app=app)), line.format(app=app)


_NAMES_A_CONTAINER_OR_AN_IMAGE = [
    "hub = next(c for c in containers if c['name'] == '{name}')",
    "az containerapp show -n \"$HUB_APP\" -g vincsRG --query \"properties.template.containers[?name=='{name}'].image\"",
    'az containerapp update -n "$HUB_APP" -g vincsRG --container-name {name} --image "$img"',
    'az containerapp logs show -n "$ACCOUNT_APP" -g vincsRG --container {name}',
    '"containers": ["{name}"]',
    "name: {name}",
    "assert hub['image'] == 'vincacr.azurecr.io/{name}:' + sha",
    'img="vincacr.azurecr.io/{name}:$IMAGE_INPUT"',
    'case "$img" in vincacr.azurecr.io/{name}:*) ;; *) exit 1 ;; esac',
    "az acr repository show-tags -n vincacr --repository {name}",
    "docker build -t {name}:latest .",
    "IMAGE={name}:$SHA",
    "APP_IMAGE=vincacr.azurecr.io/{name}:$SHA",
    "--image {name}:latest",
    "APP: {name}-rh-${{{{ github.run_id }}}}",
    "az containerapp show -n {name}-rh-1 -g vincsRG",
]


@pytest.mark.parametrize("name", _CONTAINERS)
@pytest.mark.parametrize("line", _NAMES_A_CONTAINER_OR_AN_IMAGE)
def test_container_names_and_image_repositories_are_not_app_names(line, name):
    assert not _APP_NAME.search(line.format(name=name)), line.format(name=name)


_OWNERS = {"hub-deploy.yml", "hub-image-public.yml"}


def test_no_workflow_or_script_names_an_app_of_the_table():
    files = [p for p in sorted((ROOT / ".github/workflows").glob("*.yml")) if p.name not in _OWNERS]
    files += [ROOT / "scripts/gate_app_config.py", ROOT / "scripts/hub_capacity_trial.py"]
    hits = [f"{p.relative_to(ROOT)}:{n}" for p in files
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if _APP_NAME.search(line) and not line.lstrip().startswith("#")]
    assert hits == []
