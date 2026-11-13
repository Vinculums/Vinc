"""배포된 리비전을 바꾸지 않고 기존 배포 검증 단계만 실행한다."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

import yaml

REQUIRED = (
    "허브가 이 커밋의 이미지로 서 있는가",
    "Verify health (MCP tools/list)",
    "Verify temporal search through the existing read only account key",
    "Verify connector surface (OAuth discovery)",
    "App config drift gate (구조 기준선 대조)",
    "Node mirror rebuild result (재구축이 실제로 돌았는가)",
    "Graph citation liveness (라이브 인용 대조)",
)
SECRETS = {"VINC_MCP_TOKEN", "VINC_GRAPH_READONLY_KEY"}
# 검증 단계가 env 로 읽는 리포 변수. 값은 비밀이 아니고, hub-verify-current 가 같은 이름의 env 로 넘긴다.
VARS = {"VINC_GRAPH_SPACE"}


def ready(app, expected):
    properties = app.get("properties", {})
    containers = properties.get("template", {}).get("containers", [])
    return bool(containers and containers[0].get("image") == expected
                and properties.get("provisioningState") == "Succeeded"
                and properties.get("latestRevisionName")
                and properties.get("latestReadyRevisionName") == properties["latestRevisionName"])


def verification_steps(document):
    steps = document["jobs"]["build-and-deploy"]["steps"]
    found = {step.get("name"): step for step in steps}
    missing = set(REQUIRED) - set(found)
    if missing:
        raise ValueError("verification stage missing")
    return [found[name] for name in REQUIRED]


def step_env(step, environment):
    """검증 단계의 env 를 이 실행의 값으로 푼다. 허용된 비밀(SECRETS)과 리포 변수(VARS) 참조만 받고, 그 밖의 식은 거절한다."""
    selected = dict(environment)
    for name, value in (step.get("env") or {}).items():
        match = re.fullmatch(r"\$\{\{\s*(secrets|vars)\.([A-Z_]+)\s*\}\}", str(value))
        if not match or match[2] not in (SECRETS if match[1] == "secrets" else VARS):
            raise ValueError("unsupported verification environment")
        selected[name] = environment.get(match[2], "")
    return selected


def target_env(app, hub_app):
    """배포 잡이 plan 에서 받는 HUB_APP·HUB_URL 을 같은 앱의 실물에서 채운다. 검증 단계가 그 둘을 읽는다."""
    fqdn = (app.get("properties", {}).get("configuration") or {}).get("ingress", {}).get("fqdn")
    if not fqdn:
        raise ValueError("hub ingress address is absent")
    return {"HUB_APP": hub_app, "HUB_URL": "https://" + fqdn}


def target_hub_app(root=Path(__file__).resolve().parents[1]):
    """대상 설정 한 줄(.github/container-apps-target.env 의 CAE)을 허브 앱 이름으로 푼다.

    환경에서 앱으로 가는 표는 hub-deploy 의 plan 잡 "배포 대상" 스텝(id: target) 하나에만 있으므로 그 스텝의
    case 줄을 읽는다. CAE 줄이 하나가 아니거나 표에 없는 값이면 멈춘다: 다른 앱을 검증하면 안 된다."""
    values = re.findall(r"(?m)^CAE=(\S+)$", (root / ".github/container-apps-target.env").read_text(encoding="utf-8"))
    if len(values) != 1:
        raise ValueError("the target setting must hold exactly one CAE line")
    document = yaml.safe_load((root / ".github/workflows/hub-deploy.yml").read_text(encoding="utf-8"))
    script = next(step["run"] for step in document["jobs"]["plan"]["steps"] if step.get("id") == "target")
    table = dict(re.findall(r"(?m)^\s*([a-z0-9-]+)\) app=([a-z0-9-]+) ;;$", script))
    if values[0] not in table:
        raise ValueError("unknown target environment")
    return table[values[0]]


def render(script):
    def replacement(match):
        name = match.group(1)
        if name not in SECRETS:
            raise ValueError("unsupported verification credential")
        return "$" + name
    rendered = re.sub(r"\$\{\{\s*secrets\.([A-Z_]+)\s*\}\}", replacement, script)
    if "${{" in rendered:
        raise ValueError("unresolved verification expression")
    return rendered


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.sha):
        raise ValueError("invalid deployment commit")
    subprocess.run(["git", "merge-base", "--is-ancestor", args.sha, "origin/main"], check=True)
    expected = "vincacr.azurecr.io/vinc-hub:" + args.sha
    # 앱 이름은 hub-deploy 의 "배포 대상" 과 같은 뜻이다. 넘겨받지 않으면 대상 설정에서 푼다.
    hub_app = os.environ.get("HUB_APP") or target_hub_app()
    result = subprocess.run(["az", "containerapp", "show", "-n", hub_app, "-g", "vincsRG", "-o", "json"],
                            check=True, capture_output=True, text=True)
    app = json.loads(result.stdout)
    if not ready(app, expected):
        raise ValueError("current revision is not the expected ready image")
    revision = app["properties"]["latestRevisionName"]
    state = subprocess.run(["az", "containerapp", "revision", "show", "-n", hub_app, "-g", "vincsRG",
                            "--revision", revision, "--query", "properties.runningState", "-o", "tsv"],
                           check=True, capture_output=True, text=True).stdout.strip()
    if state not in ("Running", "RunningAtMaxScale"):
        raise ValueError("current revision is not running")
    source = subprocess.run(["git", "show", args.sha + ":.github/workflows/hub-deploy.yml"],
                            check=True, capture_output=True, text=True).stdout
    document = yaml.safe_load(source)
    environment = {**os.environ, "DEPLOY_SHA": args.sha, **target_env(app, hub_app)}
    warnings = []
    for step in verification_steps(document):
        print("Verifying: " + step["name"], flush=True)
        selected = step_env(step, environment)
        completed = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", render(step["run"])],
                                   env=selected, timeout=900)
        if completed.returncode:
            if step.get("continue-on-error"):
                warnings.append(step["name"])
            else:
                raise ValueError("deployment verification failed: " + step["name"])
    print(json.dumps({"deployment_sha": args.sha, "verification": "passed", "warnings": warnings}))


if __name__ == "__main__":
    main()
