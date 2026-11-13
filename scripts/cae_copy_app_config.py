"""한 컨테이너 앱의 시크릿과 env 를 다른 앱으로 옮긴다. 값은 화면, 로그, 명령 인자 어디에도 나오지 않는다.

    python scripts/cae_copy_app_config.py --from-app vinc-account --to-app vinc-account-vnet
    python scripts/cae_copy_app_config.py --from-app vinc-account --to-app vinc-account-vnet --write \\
        --override VINC_MCP_INTERNAL_URL=https://<새 허브 주소>

기본은 dry-run 이다. 이름과 종류(값 또는 secretref)만 적고 아무것도 쓰지 않는다.
--write 는 소유자가 승인한 전환 단계에서만 돈다. 순서:
  1. 원본의 시크릿 값과 env 값을 메모리로 읽는다(출력하지 않는다).
  2. env 값이 원본 환경의 기본 도메인을 가리키면 --override 나 --keep-old-domain 없이는 멈춘다.
  3. 값을 0600 파일로 쓰고 az 에 `이름=@파일` 로 넘긴다. azure-cli 는 인자를 첫 `=` 에서 나눠
     뒤쪽이 `@경로` 면 그 파일 내용으로 바꾼다(azure.cli.core.commands._expand_file_prefixed_files).
     파일을 못 읽으면 `@경로` 글자 그대로 넘어가므로 호출 전에 파일 크기를 값 길이와 대조한다.
  4. 대상에서 다시 읽어 이름마다 same 또는 DIFFERENT 만 적는다. 하나라도 다르면 1 로 끝난다.
레지스트리는 쓰지 않는다(대상 앱을 만들 때 함께 정한다). server 와 username 이 같은지만 본다.

종료 코드: 0 정상, 1 되읽기 불일치, 2 거절(아무것도 쓰지 않음), 3 Azure 읽기 실패.
표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Callable

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]

# Container Apps 기본 도메인을 가리키는 호스트. 원본 환경의 것인지는 따로 대조한다.
_ACA_HOST = re.compile(r"[a-z0-9-]+\.[a-z0-9-]+\.[a-z0-9-]+\.azurecontainerapps\.io", re.I)


class Refused(Exception):
    """쓰기 전에 멈춘다. 메시지에는 이름만 담는다."""


class AzFailed(Exception):
    """Azure 호출 실패. 메시지에는 값이 들어가지 않게 만든다."""


def default_runner(argv: list[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(argv, capture_output=True, text=True)


def _redacted(stderr: str) -> str:
    # 시크릿을 다루는 호출의 오류는 첫 줄만, `=` 뒤를 지워서 낸다.
    line = (stderr or "").strip().splitlines()[:1]
    return re.sub(r"=\S*", "=<가림>", line[0]) if line else ""


def az(run: Runner, argv: list[str], *, carries_values: bool) -> str:
    r = run(["az", *argv])
    if r.returncode != 0:
        what = " ".join(argv[:3])
        if carries_values:
            raise AzFailed(f"az {what} 실패(rc={r.returncode}): {_redacted(r.stderr)}")
        raise AzFailed(f"az {what} 실패(rc={r.returncode}): {(r.stderr or '').strip()}")
    return r.stdout


@dataclass
class App:
    name: str
    env_id: str
    env: list[dict]          # [{"name", "value"} 또는 {"name", "secretRef"}]
    registries: list[dict]   # [{"server", "username", "passwordSecretRef"}]
    containers: int
    secret_names: list[str] = field(default_factory=list)


def read_app(run: Runner, name: str, rg: str) -> App:
    doc = json.loads(az(run, ["containerapp", "show", "-n", name, "-g", rg, "-o", "json"],
                        carries_values=False))
    props = doc.get("properties") or {}
    containers = (props.get("template") or {}).get("containers") or []
    config = props.get("configuration") or {}
    return App(name=name, env_id=props.get("managedEnvironmentId") or "",
               env=list((containers[0].get("env") if containers else None) or []),
               registries=list(config.get("registries") or []), containers=len(containers))


def read_secret_names(run: Runner, name: str, rg: str) -> list[str]:
    out = az(run, ["containerapp", "secret", "list", "-n", name, "-g", rg, "--query", "[].name", "-o", "json"],
             carries_values=False)
    return list(json.loads(out) or [])


def read_secret_values(run: Runner, name: str, rg: str) -> dict[str, dict]:
    # 값은 이 함수의 반환값으로만 산다. 출력하지 않는다.
    out = az(run, ["containerapp", "secret", "list", "-n", name, "-g", rg, "--show-values", "-o", "json"],
             carries_values=True)
    return {s["name"]: s for s in json.loads(out) or []}


def env_domain(run: Runner, env_id: str) -> str:
    if not env_id:
        return ""
    return az(run, ["containerapp", "env", "show", "--ids", env_id, "--query", "properties.defaultDomain", "-o", "tsv"],
              carries_values=False).strip()


def domain_hits(app: App, domain: str) -> tuple[list[str], list[str]]:
    """(원본 환경 도메인을 가리키는 env 이름, 다른 Container Apps 도메인을 가리키는 env 이름)."""
    source, other = [], []
    for e in app.env:
        value = e.get("value")
        if not isinstance(value, str):
            continue
        if domain and domain.lower() in value.lower():
            source.append(e["name"])
        elif _ACA_HOST.search(value):
            other.append(e["name"])
    return source, other


def _kind(e: dict) -> str:
    return f"secretref:{e['secretRef']}" if e.get("secretRef") else "value"


def _same_registries(a: list[dict], b: list[dict]) -> bool:
    key = lambda rs: sorted((r.get("server") or "", r.get("username") or "") for r in rs)  # noqa: E731
    return key(a) == key(b)


def dry_run(run: Runner, src: str, dst: str, rg: str, out=None) -> int:
    out = out or sys.stdout
    app = read_app(run, src, rg)
    names = read_secret_names(run, src, rg)
    domain = env_domain(run, app.env_id)
    hits, others = domain_hits(app, domain)
    p = lambda *a: print(*a, file=out)  # noqa: E731
    p(f"원본 앱: {src} (환경 기본 도메인: {domain or '읽지 못함'})")
    p(f"시크릿 {len(names)}개(이름만):")
    for n in sorted(names):
        p(f"  {n}")
    p(f"env {len(app.env)}개(이름과 종류만):")
    for e in app.env:
        mark = ""
        if e["name"] in hits:
            mark = "  <- 원본 환경 도메인을 가리킨다. --write 에는 --override 나 --keep-old-domain 이 필요하다"
        elif e["name"] in others:
            mark = "  <- 다른 Container Apps 도메인을 가리킨다. 확인할 것"
        p(f"  {e['name']}  {_kind(e)}{mark}")
    p("레지스트리:")
    for r in app.registries:
        p(f"  {r.get('server')}  username {r.get('username')}  passwordSecretRef {r.get('passwordSecretRef') or '-'}"
          f"  identity {r.get('identity') or '-'}")
    try:
        target = read_app(run, dst, rg)
    except AzFailed:
        p(f"대상 앱: {dst} 은 아직 없다(만든 뒤 --write 를 돌린다)")
        return 0
    target_secrets = read_secret_names(run, dst, rg)
    p(f"대상 앱: {dst} (환경 {target.env_id.rsplit('/', 1)[-1] or '?'})")
    p("  대상에 이미 있는 시크릿: " + (", ".join(sorted(set(names) & set(target_secrets))) or "없음"))
    p("  대상에 이미 있는 env: " + (", ".join(sorted({e['name'] for e in app.env} & {e['name'] for e in target.env}))
                                 or "없음"))
    p("  레지스트리(server, username): " + ("같다" if _same_registries(app.registries, target.registries) else "다르다"))
    return 0


def _parse_overrides(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        name, sep, value = item.partition("=")
        if not sep or not name:
            raise Refused(f"--override 는 NAME=VALUE 형식이다: {name or '(빈 이름)'}")
        out[name] = value
    return out


def _check_passable(kind: str, name: str, value: str) -> None:
    # az 가 파일에서 읽을 때 끝 줄바꿈과 BOM 을 지우고, `secretref:` 와 Key Vault 참조 문법을 따로 읽는다.
    if value.endswith(("\n", "\r")) or value.startswith("﻿"):
        raise Refused(f"{kind} {name}: 값의 끝 줄바꿈이나 BOM 은 파일로 넘기면 바뀐다")
    if kind == "env" and "secretref:" in value:
        raise Refused(f"env {name}: 값 안의 'secretref:' 를 az 가 참조로 읽는다")
    if kind == "secret" and value.startswith(("keyvaultref:", "identityref:")):
        raise Refused(f"secret {name}: 값이 Key Vault 참조 문법과 겹친다")


def _write_file(directory: str, index: int, value: str) -> str:
    path = os.path.join(directory, f"v{index}")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        f.write(value)
    if os.path.getsize(path) != len(value.encode("utf-8")):
        raise Refused("값 파일의 크기가 값 길이와 다르다")
    return path


def write(run: Runner, src: str, dst: str, rg: str, overrides: dict[str, str], keep_old: set[str],
          out=None) -> int:
    out = out or sys.stdout
    p = lambda *a: print(*a, file=out)  # noqa: E731
    app = read_app(run, src, rg)
    domain = env_domain(run, app.env_id)
    hits, _ = domain_hits(app, domain)
    unknown = sorted(set(overrides) - {e["name"] for e in app.env})
    if unknown:
        raise Refused("--override 이름이 원본 env 에 없다: " + ", ".join(unknown))
    unresolved = sorted(set(hits) - set(overrides) - keep_old)
    if unresolved:
        raise Refused("원본 환경 도메인을 가리키는 env 가 있다(--override 나 --keep-old-domain 으로 정할 것): "
                      + ", ".join(unresolved))
    try:
        target = read_app(run, dst, rg)
    except AzFailed:
        raise Refused(f"대상 앱 {dst} 이 없다. 먼저 만든다") from None
    if app.containers != 1 or target.containers != 1:
        raise Refused("원본과 대상은 컨테이너가 하나씩이어야 한다")

    secrets = read_secret_values(run, src, rg)
    for name, s in secrets.items():
        if "value" not in s or s.get("keyVaultUrl"):
            raise Refused(f"secret {name}: 값이 아닌 Key Vault 참조라 옮길 수 없다")
        _check_passable("secret", name, s["value"])
    plan_env: list[tuple[str, str, str]] = []   # (이름, 종류, 값 또는 참조)
    for e in app.env:
        if e.get("secretRef"):
            plan_env.append((e["name"], "ref", e["secretRef"]))
            continue
        value = overrides.get(e["name"], e.get("value") or "")
        _check_passable("env", e["name"], value)
        plan_env.append((e["name"], "value", value))

    private = tempfile.mkdtemp(prefix="cae-copy-")
    os.chmod(private, 0o700)
    try:
        args: list[str] = []
        for i, (name, s) in enumerate(sorted(secrets.items())):
            args.append(f"{name}=@{_write_file(private, i, s['value'])}")
        if args:
            az(run, ["containerapp", "secret", "set", "-n", dst, "-g", rg, "--secrets", *args, "--output", "none"],
               carries_values=True)
            p(f"시크릿 {len(args)}개를 썼다")
        env_args: list[str] = []
        for i, (name, kind, v) in enumerate(plan_env):
            env_args.append(f"{name}=secretref:{v}" if kind == "ref"
                            else f"{name}=@{_write_file(private, 1000 + i, v)}")
        if env_args:
            az(run, ["containerapp", "update", "-n", dst, "-g", rg, "--set-env-vars", *env_args, "--output", "none"],
               carries_values=True)
            p(f"env {len(env_args)}개를 썼다")
    finally:
        shutil.rmtree(private, ignore_errors=True)

    # 되읽기. 값은 비교에만 쓰고 결과는 이름과 same/DIFFERENT 로만 낸다.
    got_secrets = read_secret_values(run, dst, rg)
    got = read_app(run, dst, rg)
    got_env = {e["name"]: e for e in got.env}
    bad = 0
    for name, s in sorted(secrets.items()):
        ok = got_secrets.get(name, {}).get("value") == s["value"]
        bad += not ok
        p(f"  secret {name}: {'same' if ok else 'DIFFERENT'}")
    for name, kind, v in plan_env:
        e = got_env.get(name) or {}
        ok = (e.get("secretRef") == v) if kind == "ref" else (not e.get("secretRef") and (e.get("value") or "") == v)
        bad += not ok
        p(f"  env {name}: {'same' if ok else 'DIFFERENT'}")
    p("레지스트리(server, username): " + ("같다" if _same_registries(app.registries, got.registries)
                                       else "다르다(대상을 만들 때 정한 값을 확인할 것)"))
    return 1 if bad else 0


def main(argv: list[str] | None = None, run: Runner = default_runner) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from-app", required=True)
    ap.add_argument("--to-app", required=True)
    ap.add_argument("-g", "--resource-group", default="vincsRG")
    ap.add_argument("--write", action="store_true", help="쓴다. 없으면 이름만 적는 dry-run")
    ap.add_argument("--override", action="append", default=[], metavar="NAME=VALUE",
                    help="대상에 다른 값으로 넣을 env(값이 아닌 주소 같은 것만)")
    ap.add_argument("--keep-old-domain", action="append", default=[], metavar="NAME",
                    help="원본 환경 도메인을 가리키는 값을 그대로 옮길 env")
    a = ap.parse_args(argv)
    if a.from_app == a.to_app:
        print("원본과 대상이 같다", file=sys.stderr)
        return 2
    try:
        if not a.write:
            return dry_run(run, a.from_app, a.to_app, a.resource_group)
        return write(run, a.from_app, a.to_app, a.resource_group, _parse_overrides(a.override),
                     set(a.keep_old_domain))
    except Refused as e:
        print(f"거절: {e}", file=sys.stderr)
        return 2
    except AzFailed as e:
        print(str(e), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
