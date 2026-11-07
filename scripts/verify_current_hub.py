"""배포된 리비전을 바꾸지 않고 기존 배포 검증 단계만 실행한다."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess

import yaml

REQUIRED = (
    "허브가 이 커밋의 이미지로 서 있는가",
    "Verify health (MCP tools/list)",
    "Verify temporal search through the existing read only account key",
    "Verify connector surface (OAuth discovery)",
    "저장 배치가 계정 경로에서 ready이고 그래프 표식과 맞는가",
    "App config drift gate (구조 기준선 대조)",
    "Node mirror rebuild result (재구축이 실제로 돌았는가)",
    "Graph citation liveness (라이브 인용 대조)",
)
SECRETS = {"VINC_MCP_TOKEN", "VINC_GRAPH_READONLY_KEY"}


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
    result = subprocess.run(["az", "containerapp", "show", "-n", "vinc-hub", "-g", "vincsRG", "-o", "json"],
                            check=True, capture_output=True, text=True)
    app = json.loads(result.stdout)
    if not ready(app, expected):
        raise ValueError("current revision is not the expected ready image")
    revision = app["properties"]["latestRevisionName"]
    state = subprocess.run(["az", "containerapp", "revision", "show", "-n", "vinc-hub", "-g", "vincsRG",
                            "--revision", revision, "--query", "properties.runningState", "-o", "tsv"],
                           check=True, capture_output=True, text=True).stdout.strip()
    if state not in ("Running", "RunningAtMaxScale"):
        raise ValueError("current revision is not running")
    source = subprocess.run(["git", "show", args.sha + ":.github/workflows/hub-deploy.yml"],
                            check=True, capture_output=True, text=True).stdout
    document = yaml.safe_load(source)
    environment = {**os.environ, "DEPLOY_SHA": args.sha}
    warnings = []
    for step in verification_steps(document):
        print("Verifying: " + step["name"], flush=True)
        selected = dict(environment)
        for name, value in step.get("env", {}).items():
            match = re.fullmatch(r"\$\{\{\s*secrets\.([A-Z_]+)\s*\}\}", str(value))
            if not match or match[1] not in SECRETS:
                raise ValueError("unsupported verification environment")
            selected[name] = environment.get(match[1], "")
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
