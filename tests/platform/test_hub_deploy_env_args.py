"""Exercise the deployed shell's Azure argument list without cloud access."""
from pathlib import Path
import itertools
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/hub-deploy.yml"
RETIRED_ENV = ("VINC_EMBED_REMOTE", "VINC_EMBED_ENCODE_TOKEN", "VINC_LAYOUT_REMOTE", "VINC_LAYOUT_TOKEN")
RETIRED_SECRETS = ("layout-token", "encode-token")
CLEANUP_STEP = "허브가 쓰지 않는 시크릿을 지운다 (env 참조를 뺀 갱신 뒤)"


def bash_path():
    bash = shutil.which("bash")
    if Path("D:/Git/bin/bash.exe").is_file():
        bash = "D:/Git/bin/bash.exe"
    return bash


def deploy_shell():
    """배포 스텝의 az containerapp update 앞까지(RMARGS 와 ENVVARS 조립)와 그 명령."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    start = re.search(r"^          RMARGS=\(\)$", workflow, re.M).start()
    update = re.search(r"^          if ! (az containerapp update .+); then$", workflow[start:], re.M)
    assert update is not None
    return textwrap.dedent(workflow[start:start + update.start()]), update.group(1)


class HubDeployEnvironmentArgs(unittest.TestCase):
    def setUp(self):
        self.bash = bash_path()
        self.assertIsNotNone(self.bash, "Bash is required to verify the deployment shell")

    def run_deploy(self, *, quota="", paused="off", have=""):
        assembly, command = deploy_shell()
        shell = assembly.replace("${{ vars.VINC_QUOTA_EXEMPT_OWNERS }}", quota)
        shell = shell.replace("${{ vars.VINC_EMBED_APPLY_PAUSED || 'off' }}", paused)
        shell = shell.replace("${{ vars.VINC_BURST_PER_MINUTE || '60' }}", "60")
        shell = shell.replace("${{ vars.VINC_EVENTS_WEBHOOK || 'off' }}", "off")
        shell = shell.replace("${{ vars.VINC_KEEP_VECTOR_INDEX || 'on' }}", "on")
        shell = shell.replace("${{ vars.VINC_NODE_COLBERT || 'off' }}", "off")
        shell = shell.replace("${{ vars.VINC_BRIEF_RULE || 'production' }}", "production")
        # `show` 는 허브에 지금 있는 env 이름을 돌려주고, 그 밖의 호출은 인자를 한 줄씩 찍는다.
        script = ('set -euo pipefail\n'
                  'az() { if [ "$2" = show ]; then printf "%s\\n" $HAVE; else printf "%s\\n" "$@"; fi; }\n'
                  'APP="--name vinc-hub -g vincsRG"\nIMAGE=test-image\n'
                  + shell + "\n" + command)
        result = subprocess.run([self.bash, "-c", script], capture_output=True, text=True,
                                encoding="utf-8", timeout=10, check=True, env={"HAVE": have, "PATH": "/usr/bin:/bin"})
        return result.stdout.splitlines()

    def test_optional_settings_share_one_environment_argument(self):
        for quota, paused in itertools.product(["", "owner:test"], ["off", "on"]):
            with self.subTest(quota=quota, paused=paused):
                args = self.run_deploy(quota=quota, paused=paused)
                self.assertEqual(args.count("--set-env-vars"), 1,
                                 "Repeated Azure options silently discard earlier settings")
                environment = args[args.index("--set-env-vars") + 1:args.index("--output")]
                expected = [f"VINC_EMBED_APPLY_PAUSED={paused}", "VINC_BURST_PER_MINUTE=60",
                            "VINC_TOPIC_CONVERSION=1", "VINC_EVENTS_WEBHOOK=off",
                            "VINC_KEEP_VECTOR_INDEX=on", "VINC_NODE_COLBERT=off",
                            "VINC_BRIEF_RULE=production"]
                if quota:
                    expected.insert(0, f"VINC_QUOTA_EXEMPT_OWNERS={quota}")
                self.assertEqual(environment, expected)

    def test_the_hub_no_longer_sets_the_layout_service_env(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("LAYOUT_PRESENT", workflow)
        self.assertNotIn("VINC_LAYOUT_REMOTE=", workflow)
        self.assertNotIn("secretref:layout-token", workflow)
        self.assertNotIn("layout_app.py", workflow)
        args = self.run_deploy(have=" ".join(RETIRED_ENV))
        environment = args[args.index("--set-env-vars") + 1:args.index("--output")]
        self.assertEqual([a for a in environment if a.startswith(("VINC_LAYOUT", "VINC_EMBED_REMOTE", "VINC_EMBED_ENCODE"))], [])

    def test_retired_remote_env_is_removed_only_when_present(self):
        cases = [("", []),
                 ("VINC_BRIEF_RULE VINC_QUOTA", []),
                 ("VINC_LAYOUT_REMOTE VINC_LAYOUT_TOKEN VINC_BRIEF_RULE", ["VINC_LAYOUT_REMOTE", "VINC_LAYOUT_TOKEN"]),
                 ("VINC_LAYOUT_TOKEN", ["VINC_LAYOUT_TOKEN"]),
                 (" ".join(RETIRED_ENV), list(RETIRED_ENV)),
                 # 이름이 많아 grep 이 첫 줄에서 끝나도 지울 이름을 놓치지 않는다(pipefail 과 SIGPIPE).
                 (" ".join(("VINC_EMBED_REMOTE",) + tuple(f"VINC_OTHER_{i}" for i in range(5000))),
                  ["VINC_EMBED_REMOTE"])]
        for have, removed in cases:
            with self.subTest(have=have):
                args = self.run_deploy(have=have)
                if not removed:
                    self.assertNotIn("--remove-env-vars", args)
                    continue
                self.assertEqual(args.count("--remove-env-vars"), 1)
                start = args.index("--remove-env-vars") + 1
                self.assertEqual(args[start:start + len(removed)], removed)
                self.assertEqual(args[start + len(removed)], "--set-env-vars",
                                 "removals must end before the set list starts")


class HubSecretCleanup(unittest.TestCase):
    """env 참조를 뺀 갱신 뒤에 쓰지 않는 시크릿을 지우는 스텝."""

    def setUp(self):
        self.bash = bash_path()
        self.assertIsNotNone(self.bash, "Bash is required to verify the deployment shell")
        self.steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["build-and-deploy"]["steps"]

    def run_cleanup(self, *, secrets, refs="", before="rev-1", after="rev-1"):
        step = next(s for s in self.steps if s.get("name") == CLEANUP_STEP)
        with tempfile.TemporaryDirectory() as work:
            script = (
                'az() {\n'
                '  echo "$*" >> "$WORK/calls"\n'
                '  case "$*" in\n'
                '    *"secret list"*) printf "%s\\n" $SECRETS ;;\n'
                '    *"secret remove"*) : > "$WORK/removed" ;;\n'
                '    *secretRef*) printf "%s\\n" $REFS ;;\n'
                '    *latestRevisionName*) if [ -e "$WORK/removed" ]; then echo "$AFTER"; else echo "$BEFORE"; fi ;;\n'
                '    *) echo "unexpected az call: $*" >&2; return 9 ;;\n'
                '  esac\n'
                '}\n' + step["run"])
            result = subprocess.run(
                [self.bash, "-c", script], capture_output=True, text=True, encoding="utf-8", timeout=10,
                env={"WORK": work, "SECRETS": secrets, "REFS": refs, "BEFORE": before, "AFTER": after,
                     "HUB_APP": "vinc-hub", "PATH": "/usr/bin:/bin"})
            calls_file = Path(work) / "calls"
            calls = calls_file.read_text(encoding="utf-8").splitlines() if calls_file.exists() else []
        return result, calls

    @staticmethod
    def removals(calls):
        return [c for c in calls if "secret remove" in c]

    def test_both_retired_secrets_go_in_one_call(self):
        result, calls = self.run_cleanup(secrets="acr-password encode-token layout-token mcp-token")
        self.assertEqual(result.returncode, 0, result.stderr)
        removal = self.removals(calls)
        self.assertEqual(len(removal), 1)
        self.assertIn("--name vinc-hub -g vincsRG", removal[0], "the app comes from the job's HUB_APP")
        self.assertTrue(removal[0].endswith("--secret-names layout-token encode-token --output none"), removal[0])
        self.assertNotIn("--show-values", " ".join(calls), "secret values must never be read")

    def test_absent_secrets_are_skipped(self):
        for secrets, expected in [("acr-password mcp-token", None), ("", None),
                                  ("encode-token mcp-token", "encode-token"), ("layout-token", "layout-token")]:
            with self.subTest(secrets=secrets):
                result, calls = self.run_cleanup(secrets=secrets)
                self.assertEqual(result.returncode, 0, result.stderr)
                removal = self.removals(calls)
                if expected is None:
                    self.assertEqual(removal, [])
                else:
                    self.assertEqual(len(removal), 1)
                    self.assertTrue(removal[0].endswith(f"--secret-names {expected} --output none"), removal[0])

    def test_a_secret_the_env_still_references_is_never_removed(self):
        for refs in ("layout-token", "mcp-token layout-token", "encode-token"):
            with self.subTest(refs=refs):
                result, calls = self.run_cleanup(secrets="encode-token layout-token", refs=refs)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.removals(calls), [])

    def test_an_unexpected_new_revision_fails_the_step(self):
        result, calls = self.run_cleanup(secrets="layout-token", before="rev-1", after="rev-2")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.removals(calls)), 1)

    def test_it_runs_after_the_update_and_before_the_drift_gate(self):
        names = [s.get("name") for s in self.steps]
        deploy = names.index("Deploy to Container Apps")
        drift = names.index("App config drift gate (구조 기준선 대조)")
        cleanup = names.index(CLEANUP_STEP)
        self.assertLess(deploy, cleanup, "Azure refuses to remove a secret the env still references")
        self.assertLess(cleanup, drift, "the drift gate must see the cleaned hub")
        self.assertIn("--remove-env-vars", self.steps[deploy]["run"])
        self.assertTrue(self.steps[cleanup].get("continue-on-error"))
        for name in RETIRED_SECRETS:
            self.assertIn(name, self.steps[cleanup]["run"])

    def test_the_hub_baseline_lists_none_of_the_retired_names(self):
        import json
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        from hub_target import target_hub_app
        baseline = json.loads((ROOT / "scripts/app-config-baseline.json").read_text(encoding="utf-8"))
        # 기준선의 허브 항목은 대상 설정이 고른 앱 이름으로 적혀 있다(지금은 vinc-hub-vnet).
        hub = baseline[target_hub_app(ROOT)]
        for name in RETIRED_ENV:
            self.assertNotIn(name, hub["env_names"])
        for name in RETIRED_SECRETS:
            self.assertNotIn(name, hub["secret_names"])
        # 배치 앱은 단계 2 에서 지울 때까지 기준선에 남는다.
        self.assertIn("VINC_LAYOUT_TOKEN", baseline["vinc-layout"]["env_names"])
        self.assertIn("layout-token", baseline["vinc-layout"]["secret_names"])


if __name__ == "__main__":
    unittest.main()
