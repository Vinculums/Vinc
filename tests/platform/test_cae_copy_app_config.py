# -*- coding: utf-8 -*-
"""앱 설정 복사 스크립트: 값이 화면과 명령 인자에 나오지 않고, 되읽기로 판정하는가.

가짜 az 가 앱 둘의 상태를 들고 있고, `이름=@파일` 인자는 진짜 az 처럼 파일 내용으로 바꿔 적용한다.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import subprocess
import unittest

from scripts import cae_copy_app_config as cp

OLD = "grayplant-test.koreacentral.azurecontainerapps.io"
NEW = "lemon-test.koreacentral.azurecontainerapps.io"
SECRET = "s3cr3t-value-=,x"
PLAIN = "plain-value-with=equals"
HUB = f"https://vinc-hub.{OLD}"


def _app(env_name: str, env: list[dict], registries: list[dict]) -> dict:
    return {"properties": {
        "managedEnvironmentId": f"/subscriptions/x/resourceGroups/vincsRG/providers/Microsoft.App/managedEnvironments/{env_name}",
        "configuration": {"registries": registries},
        "template": {"containers": [{"name": "c", "env": env}]}}}


class FakeAz:
    def __init__(self, *, target: bool = True, corrupt: str = ""):
        reg = [{"server": "vincacr.azurecr.io", "username": "vincacr", "passwordSecretRef": "acr-password"}]
        self.apps = {"src": _app("vinc-cae", [
            {"name": "DB_HOST", "value": PLAIN},
            {"name": "DB_PASSWORD", "secretRef": "db-password"},
            {"name": "VINC_MCP_INTERNAL_URL", "value": HUB},
        ], reg)}
        self.secrets = {"src": {"db-password": SECRET, "acr-password": "acr-" + SECRET}}
        if target:
            self.apps["dst"] = _app("vinc-cae-vnet", [], copy.deepcopy(reg))
            self.secrets["dst"] = {"acr-password": "acr-" + SECRET}
        self.domains = {"vinc-cae": OLD, "vinc-cae-vnet": NEW}
        self.corrupt = corrupt
        self.calls: list[list[str]] = []

    @staticmethod
    def _load(arg: str) -> tuple[str, str]:
        name, _, rest = arg.partition("=")
        if rest.startswith("@"):
            with open(rest[1:], encoding="utf-8") as f:
                rest = f.read()
        return name, rest

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(list(argv))
        a = argv[1:]
        ok = lambda out: subprocess.CompletedProcess(argv, 0, out, "")  # noqa: E731
        if a[:2] == ["containerapp", "env"]:
            env_id = a[a.index("--ids") + 1]
            return ok(self.domains[env_id.rsplit("/", 1)[-1]] + "\n")
        name = a[a.index("-n") + 1]
        if name not in self.apps:
            return subprocess.CompletedProcess(argv, 3, "", f"ResourceNotFound: {name}")
        if a[:2] == ["containerapp", "show"]:
            return ok(json.dumps(self.apps[name]))
        if a[:3] == ["containerapp", "secret", "list"]:
            if "--show-values" in a:
                return ok(json.dumps([{"name": k, "value": v} for k, v in self.secrets[name].items()]))
            return ok(json.dumps(list(self.secrets[name])))
        if a[:3] == ["containerapp", "secret", "set"]:
            i = a.index("--secrets") + 1
            for arg in a[i:a.index("--output")]:
                k, v = self._load(arg)
                self.secrets[name][k] = v + ("x" if k == self.corrupt else "")
            return ok("")
        if a[:2] == ["containerapp", "update"]:
            i = a.index("--set-env-vars") + 1
            env = self.apps[name]["properties"]["template"]["containers"][0]["env"]
            for arg in a[i:a.index("--output")]:
                k, v = self._load(arg)
                env[:] = [e for e in env if e["name"] != k]
                env.append({"name": k, "secretRef": v[len("secretref:"):]} if v.startswith("secretref:")
                           else {"name": k, "value": v})
            return ok("")
        raise AssertionError(f"unexpected az call: {argv}")


def _run(fake: FakeAz, *args: str) -> tuple[int, str]:
    buf_out, buf_err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
        rc = cp.main(["--from-app", "src", "--to-app", "dst", *args], run=fake)
    return rc, buf_out.getvalue() + buf_err.getvalue()


class CopyAppConfig(unittest.TestCase):
    def assert_no_values(self, text: str) -> None:
        for v in (SECRET, PLAIN, HUB, "acr-" + SECRET):
            self.assertNotIn(v, text)

    def test_dry_run_prints_names_only_and_flags_the_old_domain(self):
        fake = FakeAz()
        rc, text = _run(fake)
        self.assertEqual(rc, 0)
        self.assert_no_values(text)
        self.assertIn("db-password", text)
        self.assertIn("DB_PASSWORD  secretref:db-password", text)
        self.assertRegex(text, r"VINC_MCP_INTERNAL_URL  value  <- 원본 환경 도메인")
        self.assertNotIn("--show-values", " ".join(" ".join(c) for c in fake.calls))
        self.assertFalse([c for c in fake.calls if c[1:4] == ["containerapp", "secret", "set"]
                          or c[1:3] == ["containerapp", "update"]])

    def test_dry_run_says_when_the_target_does_not_exist(self):
        rc, text = _run(FakeAz(target=False))
        self.assertEqual(rc, 0)
        self.assertIn("아직 없다", text)

    def test_write_refuses_an_unresolved_old_domain_value_and_writes_nothing(self):
        fake = FakeAz()
        rc, text = _run(fake, "--write")
        self.assertEqual(rc, 2)
        self.assertIn("VINC_MCP_INTERNAL_URL", text)
        self.assertFalse([c for c in fake.calls if "set" in c or "update" in c])

    def test_write_refuses_when_the_target_is_missing(self):
        rc, _ = _run(FakeAz(target=False), "--write", "--keep-old-domain", "VINC_MCP_INTERNAL_URL")
        self.assertEqual(rc, 2)

    def test_write_applies_the_override_and_passes_no_value_on_a_command_line(self):
        fake = FakeAz()
        new_url = f"https://vinc-hub-vnet.{NEW}"
        rc, text = _run(fake, "--write", "--override", f"VINC_MCP_INTERNAL_URL={new_url}")
        self.assertEqual(rc, 0, text)
        self.assert_no_values(text)
        self.assertNotIn(new_url, text)
        argv = "\n".join(" ".join(c) for c in fake.calls)
        for v in (SECRET, PLAIN, "acr-" + SECRET, new_url):
            self.assertNotIn(v, argv)
        env = {e["name"]: e for e in fake.apps["dst"]["properties"]["template"]["containers"][0]["env"]}
        self.assertEqual(env["VINC_MCP_INTERNAL_URL"]["value"], new_url)
        self.assertEqual(env["DB_HOST"]["value"], PLAIN)
        self.assertEqual(env["DB_PASSWORD"]["secretRef"], "db-password")
        self.assertEqual(fake.secrets["dst"]["db-password"], SECRET)
        self.assertIn("secret db-password: same", text)
        self.assertNotIn("DIFFERENT", text)

    def test_a_readback_difference_exits_one(self):
        fake = FakeAz(corrupt="db-password")
        rc, text = _run(fake, "--write", "--keep-old-domain", "VINC_MCP_INTERNAL_URL")
        self.assertEqual(rc, 1)
        self.assertIn("secret db-password: DIFFERENT", text)
        self.assert_no_values(text)

    def test_values_az_would_change_are_refused_before_writing(self):
        fake = FakeAz()
        fake.secrets["src"]["db-password"] = SECRET + "\n"
        rc, _ = _run(fake, "--write", "--keep-old-domain", "VINC_MCP_INTERNAL_URL")
        self.assertEqual(rc, 2)
        self.assertFalse([c for c in fake.calls if "set" in c or "update" in c])

    def test_secret_command_errors_are_redacted(self):
        self.assertEqual(cp._redacted("bad secret db-password=hunter2 rest\nsecond"),
                         "bad secret db-password=<가림> rest")


if __name__ == "__main__":
    unittest.main()
