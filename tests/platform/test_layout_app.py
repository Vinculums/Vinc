"""생성 권한·정해진 앱 구조·재시도·시크릿 경계. Azure 쓰기는 가짜 호출로만 검사한다."""
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import layout_app as la


def layout():
    return {"properties": {
        "environmentId": "/subscriptions/test/resourceGroups/vincsRG/providers/Microsoft.App/managedEnvironments/vinc-cae",
        "configuration": {"ingress": {"external": False, "targetPort": 8080, "allowInsecure": False,
                                       "fqdn": "vinc-layout.internal.test.japaneast.azurecontainerapps.io"},
                          "secrets": [{"name": "layout-token"}]},
        "template": {"containers": [{"name": "vinc-layout", "resources": {"cpu": 1, "memory": "2Gi"},
                                     "env": [{"name": "VINC_LAYOUT_TOKEN", "secretRef": "layout-token"}]}],
                     "scale": {"minReplicas": 0, "maxReplicas": 1}},
    }}


AUTH = {"GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_TRIGGERING_ACTOR": "operator", "PROD_OPERATORS": "operator", "LAYOUT_CONFIRM": "vinc-layout"}
IMAGE = "vincacr.azurecr.io/vinc-layout:" + "a" * 40


class Azure:
    def __init__(self, present=False, hub_token=None, layout_token="synthetic-layout-token"):
        self.apps = {"vinc-hub": {"properties": {"configuration": {"secrets": []}}}}
        self.tokens, self.calls = {}, []
        if hub_token:
            self.put_token("vinc-hub", hub_token)
        if present:
            self.apps[la.APP] = layout()
            self.tokens[la.APP] = layout_token
        self.fail_bind_once = False

    def put_token(self, name, token):
        self.tokens[name] = token
        self.apps[name]["properties"]["configuration"]["secrets"] = [{"name": la.TOKEN_SECRET}]

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        op = args[:2]
        name = args[args.index("-n") + 1] if "-n" in args else None
        if op == ("containerapp", "show"):
            return copy.deepcopy(self.apps.get(name))
        if op == ("acr", "credential"):
            return {"username": "vincacr", "password": "synthetic-registry-password"}
        if op == ("containerapp", "create"):
            self.apps[la.APP] = layout()
            self.tokens[la.APP] = args[args.index("--secrets") + 1].split("=", 1)[1]
            return {}
        if args[:3] == ("containerapp", "secret", "list"):
            return self.tokens.get(name)
        if args[:3] == ("containerapp", "secret", "set"):
            if self.fail_bind_once:
                self.fail_bind_once = False
                raise la.DeploymentError("synthetic bind failure")
            self.put_token(name, args[args.index("--secrets") + 1].split("=", 1)[1])
            return {}
        raise AssertionError("unexpected Azure operation")

    @property
    def writes(self):
        return [c for c in self.calls if c[:2] == ("containerapp", "create")
                or c[:3] == ("containerapp", "secret", "set")]


class ProvisionTests(unittest.TestCase):
    def test_authorisation_is_checked_before_any_azure_call(self):
        for key, wrong in [("GITHUB_REF", "refs/heads/branch"), ("GITHUB_EVENT_NAME", "push"),
                           ("GITHUB_TRIGGERING_ACTOR", "outsider"), ("LAYOUT_CONFIRM", "")]:
            fake = Azure()
            with self.assertRaises(la.DeploymentError):
                la.provision(IMAGE, call=fake, environ={**AUTH, key: wrong})
            self.assertEqual(fake.calls, [])

    def test_new_app_is_internal_bounded_and_shares_only_a_secret(self):
        fake = Azure()
        result = la.provision(IMAGE, call=fake, environ=AUTH, new_token=lambda n: "synthetic-new-token")
        self.assertTrue(result["created"])
        self.assertFalse(result["hub_env_changed"])
        self.assertEqual(fake.tokens[la.APP], fake.tokens["vinc-hub"])
        create = next(c for c in fake.calls if c[:2] == ("containerapp", "create"))
        for flag, value in [("--ingress", "internal"), ("--target-port", "8080"), ("--cpu", "1"),
                            ("--memory", "2Gi"), ("--min-replicas", "0"), ("--max-replicas", "1")]:
            self.assertEqual(create[create.index(flag) + 1], value)
        self.assertNotIn("synthetic-new-token", json.dumps(result))
        self.assertNotIn("synthetic-registry-password", json.dumps(result))
        self.assertFalse(any(c[:2] == ("containerapp", "update") for c in fake.calls))

    def test_an_existing_hub_token_is_reused_without_rotation(self):
        fake = Azure(hub_token="synthetic-held-token")
        la.provision(IMAGE, call=fake, environ=AUTH, new_token=lambda n: self.fail("must reuse"))
        self.assertEqual(fake.tokens[la.APP], "synthetic-held-token")
        self.assertEqual(len(fake.writes), 1)

    def test_a_repeated_provision_is_a_noop(self):
        fake = Azure(present=True, hub_token="synthetic-layout-token")
        self.assertFalse(la.provision(IMAGE, call=fake, environ=AUTH)["created"])
        self.assertEqual(fake.writes, [])

    def test_retry_after_binding_failure_preserves_the_created_app_token(self):
        fake = Azure()
        fake.fail_bind_once = True
        with self.assertRaises(la.DeploymentError):
            la.provision(IMAGE, call=fake, environ=AUTH, new_token=lambda n: "synthetic-first-token")
        result = la.provision(IMAGE, call=fake, environ=AUTH, new_token=lambda n: self.fail("must not rotate"))
        self.assertFalse(result["created"])
        self.assertEqual(fake.tokens["vinc-hub"], "synthetic-first-token")
        self.assertEqual(len([c for c in fake.calls if c[:2] == ("containerapp", "create")]), 1)

    def test_a_conflicting_token_is_never_overwritten(self):
        fake = Azure(present=True, hub_token="synthetic-other-token")
        with self.assertRaises(la.DeploymentError) as caught:
            la.provision(IMAGE, call=fake, environ=AUTH)
        self.assertNotIn("synthetic-other-token", str(caught.exception))
        self.assertEqual(fake.writes, [])

    def test_mutable_image_is_rejected(self):
        fake = Azure()
        with self.assertRaises(la.DeploymentError):
            la.provision("vincacr.azurecr.io/vinc-layout:latest", call=fake, environ=AUTH)
        self.assertEqual(fake.calls, [])

    def test_invalid_existing_shapes_are_not_silently_adopted(self):
        for mutate in [lambda a: a["properties"]["configuration"]["ingress"].update(external=True),
                       lambda a: a["properties"]["template"]["scale"].update(maxReplicas=2),
                       lambda a: a["properties"]["template"].update(volumes=[{"name": "db"}]),
                       lambda a: a["properties"].update(environmentId="/managedEnvironments/other")]:
            fake = Azure(present=True)
            mutate(fake.apps[la.APP])
            with self.assertRaises(la.DeploymentError):
                la.provision(IMAGE, call=fake, environ=AUTH)
            self.assertEqual(fake.writes, [])


class ConnectionTests(unittest.TestCase):
    def test_absence_keeps_hub_off_without_secrets_or_writes(self):
        fake = Azure()
        self.assertEqual(la.hub_env(call=fake), {"LAYOUT_PRESENT": "0"})
        self.assertTrue(all(c[:2] == ("containerapp", "show") for c in fake.calls))

    def test_present_app_yields_only_url_and_presence(self):
        fake = Azure(present=True, hub_token="synthetic-layout-token")
        result = la.hub_env(call=fake)
        self.assertEqual(result["LAYOUT_PRESENT"], "1")
        self.assertTrue(result["LAYOUT_REMOTE"].startswith("https://vinc-layout.internal."))
        self.assertNotIn("synthetic-layout-token", json.dumps(result))
        self.assertTrue(all(c[:2] == ("containerapp", "show") for c in fake.calls))

    def test_missing_hub_secret_blocks_before_hub_stop(self):
        with self.assertRaises(la.DeploymentError):
            la.hub_env(call=Azure(present=True))

    def test_az_failure_never_discloses_secret_arguments(self):
        from subprocess import CompletedProcess
        with patch.object(la.shutil, "which", return_value="az"), patch.object(la.subprocess, "run",
                return_value=CompletedProcess([], 1, "", "synthetic-secret in upstream error")):
            with self.assertRaises(la.DeploymentError) as caught:
                la.az("containerapp", "secret", "set", "--secrets", "token=synthetic-secret")
        self.assertNotIn("synthetic-secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
