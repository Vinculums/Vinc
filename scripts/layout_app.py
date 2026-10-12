"""vinc-layout 생성과 허브 연결 준비. Azure 쓰기는 main의 수동 배포에서만 한다.

토큰과 레지스트리 암호는 프로세스 메모리와 Azure 시크릿에만 둔다. 허브 env는
여기서 바꾸지 않는다. hub-env가 내보낸 주소를 hub-deploy의 단일 writer 순서로 넣는다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
from pathlib import Path

RG, APP, ENVIRONMENT = "vincsRG", "vinc-layout", "vinc-cae"
TOKEN_SECRET = "layout-token"


class DeploymentError(RuntimeError):
    pass


def az(*args: str, missing_ok: bool = False):
    executable = shutil.which("az")
    if not executable:
        raise DeploymentError("az CLI is unavailable")
    result = subprocess.run([executable, *args, "-o", "json"], capture_output=True,
                            text=True, encoding="utf-8")
    if result.returncode:
        if missing_ok and re.search(r"\bResourceNotFound\b|\bContainerAppNotFound\b", result.stderr, re.I):
            return None
        # 시크릿을 실은 argv나 Azure의 응답을 예외에 넣지 않는다.
        raise DeploymentError(f"az {' '.join(args[:2])} failed (exit {result.returncode})")
    return json.loads(result.stdout or "null")


def show(name: str, call=az):
    return call("containerapp", "show", "-n", name, "-g", RG, missing_ok=True)


def layout_remote(app: dict) -> str:
    properties = app.get("properties") or {}
    configuration, template = properties.get("configuration") or {}, properties.get("template") or {}
    ingress = configuration.get("ingress") or {}
    containers = template.get("containers") or []
    scale = template.get("scale") or {}
    if (ingress.get("external") is not False or ingress.get("targetPort") != 8080
            or ingress.get("allowInsecure") is not False or len(containers) != 1
            or scale.get("minReplicas") != 0 or scale.get("maxReplicas") != 1
            or containers[0].get("resources", {}).get("cpu") != 1
            or containers[0].get("resources", {}).get("memory") != "2Gi"
            or template.get("volumes") or containers[0].get("volumeMounts")):
        raise DeploymentError("vinc-layout differs from internal 8080, 0..1 replicas, 1 CPU/2Gi, no volumes")
    environment = properties.get("environmentId") or properties.get("managedEnvironmentId") or ""
    if not environment.lower().endswith("/managedenvironments/" + ENVIRONMENT):
        raise DeploymentError("vinc-layout is outside vinc-cae")
    env = {e["name"]: e for e in containers[0].get("env") or []}
    if env.get("VINC_LAYOUT_TOKEN", {}).get("secretRef") != TOKEN_SECRET:
        raise DeploymentError("vinc-layout must reference its layout-token secret")
    fqdn = ingress.get("fqdn") or ""
    if not re.fullmatch(r"vinc-layout\.internal\.[a-z0-9.-]+\.azurecontainerapps\.io", fqdn):
        raise DeploymentError("vinc-layout has no valid internal ingress FQDN")
    return "https://" + fqdn


def authorised(environ: dict[str, str]) -> None:
    operators = {s.strip() for s in environ.get("PROD_OPERATORS", "").split(",") if s.strip()}
    if (environ.get("GITHUB_REF") != "refs/heads/main"
            or environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or environ.get("GITHUB_TRIGGERING_ACTOR") not in operators
            or environ.get("LAYOUT_CONFIRM") != APP):
        raise DeploymentError("provisioning requires a PROD_OPERATORS main dispatch confirming vinc-layout")


def secret_value(name: str, call=az) -> str | None:
    return call("containerapp", "secret", "list", "-n", name, "-g", RG,
                "--query", f"[?name=='{TOKEN_SECRET}'].value | [0]")


def provision(image: str, *, call=az, environ=None, new_token=secrets.token_hex) -> dict:
    authorised(os.environ if environ is None else environ)
    if not re.fullmatch(r"vincacr\.azurecr\.io/vinc-layout:[0-9a-f]{40}", image):
        raise DeploymentError("provisioning requires the immutable commit-tagged layout image")
    app, hub = show(APP, call), show("vinc-hub", call)
    created = app is None
    if not hub:
        raise DeploymentError("vinc-hub is absent")
    hub_token = secret_value("vinc-hub", call)
    if app:
        remote = layout_remote(app)
        token = secret_value(APP, call)
        if not token:
            raise DeploymentError("vinc-layout has no token value; credential repair is required")
        if hub_token and hub_token != token:
            raise DeploymentError("layout and hub token values differ; no credential was overwritten")
    else:
        token = hub_token or new_token(32)
        credentials = call("acr", "credential", "show", "-n", "vincacr",
                           "--query", "{username:username,password:passwords[0].value}")
        if not credentials or not credentials.get("password") or not credentials.get("username"):
            raise DeploymentError("existing ACR pull credentials are unavailable")
        call("containerapp", "create", "-n", APP, "-g", RG,
             "--environment", ENVIRONMENT, "--image", image,
             "--registry-server", "vincacr.azurecr.io", "--registry-username", credentials["username"],
             "--registry-password", credentials["password"], "--ingress", "internal", "--target-port", "8080",
             "--transport", "http", "--cpu", "1", "--memory", "2Gi", "--min-replicas", "0", "--max-replicas", "1",
             "--scale-rule-name", "http", "--scale-rule-type", "http", "--scale-rule-http-concurrency", "1",
             "--secrets", f"{TOKEN_SECRET}={token}", "--env-vars", f"VINC_LAYOUT_TOKEN=secretref:{TOKEN_SECRET}")
        app = show(APP, call)
        if not app:
            raise DeploymentError("vinc-layout was not present after creation")
        remote = layout_remote(app)
    if not hub_token:
        # 시크릿만 추가한다. env·이미지·스케일은 다음 hub-deploy가 바꾼다.
        call("containerapp", "secret", "set", "-n", "vinc-hub", "-g", RG,
             "--secrets", f"{TOKEN_SECRET}={token}")
    return {"app": APP, "remote": remote, "created": created,
            "hub_env_changed": False}


def hub_env(*, call=az) -> dict[str, str]:
    app = show(APP, call)
    if not app:
        return {"LAYOUT_PRESENT": "0"}
    remote = layout_remote(app)
    hub = show("vinc-hub", call)
    names = {s.get("name") for s in (hub or {}).get("properties", {}).get("configuration", {}).get("secrets", [])}
    if TOKEN_SECRET not in names:
        raise DeploymentError("vinc-hub has no layout-token secret; complete the provision dispatch first")
    return {"LAYOUT_PRESENT": "1", "LAYOUT_REMOTE": remote}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "provision", "hub-env"))
    parser.add_argument("--image")
    parser.add_argument("--out")
    args = parser.parse_args()
    try:
        if args.mode == "provision":
            result = provision(args.image or "")
        elif args.mode == "hub-env":
            result = hub_env()
            if args.out:
                with Path(args.out).open("a", encoding="utf-8") as f:
                    f.write("".join(f"{k}={v}\n" for k, v in result.items()))
        else:
            app = show(APP)
            result = {"app": APP, "present": bool(app), "resource_group": RG, "environment": ENVIRONMENT,
                      "cpu": 1, "memory": "2Gi", "min_replicas": 0, "max_replicas": 1,
                      "ingress": "internal", "port": 8080,
                      "remote": layout_remote(app) if app else None, "writes": False}
        print(json.dumps(result, sort_keys=True))
        return 0
    except DeploymentError as exc:
        print(f"layout deployment stopped: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
