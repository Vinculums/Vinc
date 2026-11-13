"""허브 앱 이름은 대상 설정 한 줄을 hub-deploy 의 표로 푼 값 하나다.

지키는 것:
  - 표는 hub-deploy 의 "배포 대상" 스텝(id: target)에만 있고, 운영 워크플로·스크립트는 그 표를 읽는다.
  - 텍스트로 읽은 표가 YAML 로 읽은 스텝 본문의 표와 같다(러너 python3 에는 PyYAML 이 없다).
  - CAE 줄이 하나가 아니거나 표에 없는 값이면 멈춘다.
  - hub-deploy·hub-image-public 밖의 워크플로가 허브 앱 이름이나 허브 앱의 고정 주소를 직접 적지 않는다.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.hub_target import hub_table, target_hub_app

ROOT = Path(__file__).resolve().parents[2]


def _copy(tmp_path: Path, setting: str, deploy: str | None = None) -> Path:
    (tmp_path / ".github/workflows").mkdir(parents=True)
    text = deploy if deploy is not None else (ROOT / ".github/workflows/hub-deploy.yml").read_text(encoding="utf-8")
    (tmp_path / ".github/workflows/hub-deploy.yml").write_text(text, encoding="utf-8")
    (tmp_path / ".github/container-apps-target.env").write_text(setting, encoding="utf-8")
    return tmp_path


def test_the_text_table_is_the_deploy_step_table():
    document = yaml.safe_load((ROOT / ".github/workflows/hub-deploy.yml").read_text(encoding="utf-8"))
    script = next(step["run"] for step in document["jobs"]["plan"]["steps"] if step.get("id") == "target")
    expected = dict(re.findall(r"(?m)^\s*([a-z0-9-]+)\) app=([a-z0-9-]+) ;;$", script))
    assert expected and hub_table() == expected


def test_the_setting_resolves_through_the_table(tmp_path):
    assert target_hub_app() in hub_table().values()
    assert target_hub_app(_copy(tmp_path / "old", "# 주석\nCAE=vinc-cae\n")) == "vinc-hub"
    assert target_hub_app(_copy(tmp_path / "new", "# 주석\nCAE=vinc-cae-vnet\n")) == "vinc-hub-vnet"


@pytest.mark.parametrize("setting", ["CAE=other\n", "# 비었다\n", "CAE=vinc-cae\nCAE=vinc-cae-vnet\n"])
def test_unknown_or_ambiguous_setting_is_refused(tmp_path, setting):
    with pytest.raises(ValueError):
        target_hub_app(_copy(tmp_path, setting))


def test_a_deploy_file_without_one_target_step_is_refused(tmp_path):
    step = "      - id: target\n        run: |\n          case x in\n            vinc-cae) app=vinc-hub ;;\n          esac\n"
    with pytest.raises(ValueError):
        hub_table(_copy(tmp_path / "two", "CAE=vinc-cae\n", "jobs:\n  plan:\n    steps:\n" + step + step))
    with pytest.raises(ValueError):
        hub_table(_copy(tmp_path / "none", "CAE=vinc-cae\n", "jobs:\n  plan:\n    steps:\n      - id: target\n        run: echo\n"))


def test_the_table_ends_with_its_step(tmp_path):
    deploy = ("jobs:\n  plan:\n    steps:\n      - id: target\n        run: |\n          case \"$cae\" in\n"
              "            vinc-cae) app=vinc-hub ;;\n          esac\n      # 다음 스텝\n      - id: pick\n"
              "        run: |\n            other) app=not-the-hub ;;\n")
    assert hub_table(_copy(tmp_path, "CAE=vinc-cae\n", deploy)) == {"vinc-cae": "vinc-hub"}


def test_the_cli_runs_on_the_standard_library_alone():
    out = subprocess.run([sys.executable, "-I", "-S", str(ROOT / "scripts/hub_target.py")],
                         capture_output=True, text=True, check=True).stdout
    assert out == f"HUB_APP={target_hub_app()}\n"


# 허브 앱 이름(-n/--name, APP:, 앱 목록, 선택지)이나 허브 앱의 고정 주소. 컨테이너 이름과 이미지 저장소(vinc-hub:)는
# 새 앱에서도 vinc-hub 이므로 여기 걸리지 않는다.
_APP_NAME = re.compile(
    r"(?:-n|--name)[\s\"',]+vinc-hub(?![-.\w])"
    r"|APP:\s*\"?(?:--name\s+)?vinc-hub(?![-.\w])"
    r"|for app in [^\n]*\bvinc-hub(?![-.\w])"
    r"|options:\s*\[[^\]\n]*vinc-hub(?![-.\w])"
    r"|containerApps/vinc-hub(?![-.\w])"
    r"|APPS\s*=\s*\([^\n]*\"vinc-hub\""
    r"|vinc-hub\.[a-z0-9-]+\.koreacentral\.azurecontainerapps\.io")
_OWNERS = {"hub-deploy.yml", "hub-image-public.yml"}


def test_no_workflow_or_script_names_the_hub_app_itself():
    files = [p for p in sorted((ROOT / ".github/workflows").glob("*.yml")) if p.name not in _OWNERS]
    files += [ROOT / "scripts/gate_app_config.py", ROOT / "scripts/hub_capacity_trial.py"]
    hits = [f"{p.relative_to(ROOT)}:{n}" for p in files
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if _APP_NAME.search(line) and not line.lstrip().startswith("#")]
    assert hits == []
