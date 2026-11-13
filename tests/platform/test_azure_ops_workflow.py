# -*- coding: utf-8 -*-
"""azure-ops 가 허용한 동작만 하는가 — 주석이 아니라 검사로.

`azure-ops.yml` 은 main 의 OIDC 쓰기 신원을 쥔다. 그 신원은 리소스 그룹의 Contributor 라서,
워크플로가 할 수 있는 일은 파일에 적힌 명령이 전부다. 누가 편의로 `--set-env-vars` 나 배포
동사를 한 줄 더하면 이 워크플로는 hub-deploy 의 직렬화와 리포 변수 정본을 비켜 가는 물건이
된다. 그래서 허용 목록과 금지 동사를 여기서 고정한다.

주석은 검사 대상이 아니다. YAML 을 파싱해 실행되는 것(`run` 본문과 `uses`)만 본다.
근거: [[vinc.ops.azure-identity-split-2026-08-30]] · 결정 decision:azure-writes-go-through-main-oidc-workflows-and-no-agent-holds-owner-2026-11-07
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
WF = _ROOT / ".github" / "workflows" / "azure-ops.yml"

#: 실행 본문에 있어서는 안 되는 것들과 그 이유.
FORBIDDEN = [
    ("--set-env-vars", "환경변수는 배포 워크플로가 리포 변수에서 싣는다"),
    ("--remove-env-vars", "같은 이유"),
    ("--replace-env-vars", "같은 이유"),
    ("--image", "이미지를 바꾸면 그것이 배포다"),
    ("containerapp update", "앱 정의를 바꾸는 동사"),
    ("containerapp create", "앱을 만드는 동사"),
    ("containerapp delete", "앱을 지우는 동사"),
    ("revision activate", "리비전 전환은 배포 경로의 일이다"),
    ("revision deactivate", "같은 이유"),
    ("role assignment", "역할 부여는 오너만 한다"),
    ("secret set", "비밀은 이 워크플로가 만지지 않는다"),
    ("keys list", "스토리지 키는 이 워크플로에 필요 없다"),
]


def _doc() -> dict:
    # PyYAML 은 `on` 을 True 로 읽는다.
    return yaml.safe_load(WF.read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    jobs = _doc().get("jobs") or {}
    assert jobs, "azure-ops.yml 에 잡이 없다"
    out = [s for job in jobs.values() for s in (job.get("steps") or [])]
    assert out, "검사할 스텝이 없다. 파싱이 틀렸을 가능성이 높다"
    return out


def _runs() -> list[str]:
    return [s["run"] for s in _steps() if "run" in s]


def test_dispatch_only_with_the_fixed_choices():
    doc = _doc()
    on = doc.get("on", doc.get(True))
    assert set(on) == {"workflow_dispatch"}, "손으로만 돈다. push·schedule·workflow_run 은 없다"
    inputs = on["workflow_dispatch"]["inputs"]
    assert inputs["app"]["type"] == "choice"
    assert sorted(inputs["app"]["options"]) == ["account", "hub"]
    assert inputs["action"]["type"] == "choice"
    assert inputs["action"]["options"] == ["restart"]


def test_the_operator_gate_comes_first():
    first = _steps()[0]
    assert "PROD_OPERATORS" in first.get("if", ""), "첫 스텝이 운영 담당 가드여야 한다"
    assert "exit 1" in first.get("run", "")


def test_the_hub_shares_the_deploy_line():
    group = _doc()["concurrency"]["group"]
    assert "'hub-deploy'" in group and "inputs.app == 'hub'" in group
    assert _doc()["concurrency"]["cancel-in-progress"] is False


def test_no_forbidden_verb_runs():
    text = "\n".join(_runs())
    for verb, why in FORBIDDEN:
        assert verb not in text, f"{verb!r} 가 실행 본문에 있다: {why}"


def test_the_only_azure_write_is_a_revision_restart():
    writes = []
    for body in _runs():
        for line in body.splitlines():
            m = re.search(r"\baz\s+([a-z][a-z-]*(?:\s+[a-z][a-z-]*){0,3})", line)
            if m and not m.group(1).startswith(("containerapp show", "account")):
                writes.append(m.group(1))
    assert writes == ["containerapp revision restart"], writes


def test_inputs_never_reach_a_shell_body_directly():
    for body in _runs():
        assert "${{ inputs." not in body, "입력은 env 로만 넘긴다(셸 주입 방지)"


def test_every_input_is_checked_by_the_guard():
    guard = next(s for s in _steps() if s.get("name") == "Guard")
    assert set(guard["env"]) >= {"APP", "ACTION", "CONFIRM", "REASON"}
    body = guard["run"]
    assert '"$CONFIRM" != "$target"' in body
    assert 'hub) target="$HUB_APP" ;;' in body and "restart)" in body
    assert 'echo "TARGET_APP=$target" >> "$GITHUB_ENV"' in body


def test_the_hub_is_the_app_the_target_setting_names():
    """허브는 대상 설정을 hub-deploy 의 표로 푼 앱이다. 앱 이름이나 주소를 여기 적으면 대상 설정이 바뀌어도 그 앱이 재시작된다."""
    steps = _steps()
    resolve = next(i for i, s in enumerate(steps) if s.get("run") == 'python3 scripts/hub_target.py >> "$GITHUB_ENV"')
    guard = next(i for i, s in enumerate(steps) if s.get("name") == "Guard")
    assert resolve < guard
    text = "\n".join(_runs())
    assert not re.search(r"(-n|--name)\s+\"?vinc-hub\b", text)
    assert "azurecontainerapps.io" not in text, "허브 주소는 재시작한 앱의 인그레스에서 읽는다"
    restart = next(s for s in steps if s.get("name") == "Restart the latest revision")
    assert '--name "$TARGET_APP"' in restart["run"] and "env" not in restart


def test_the_azure_profile_is_per_run_and_removed():
    steps = _steps()
    names = [s.get("name", "") for s in steps]
    assert names.index("Azure profile for this run only") < next(
        i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith("azure/login"))
    last = steps[-1]
    assert last.get("if") == "always()" and 'rm -rf "$AZURE_CONFIG_DIR"' in last["run"]
