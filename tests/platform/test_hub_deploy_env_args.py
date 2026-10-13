"""Exercise the deployed shell's Azure argument list without cloud access."""
from pathlib import Path
import itertools
import re
import shutil
import subprocess
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]


class HubDeployEnvironmentArgs(unittest.TestCase):
    def test_optional_settings_share_one_environment_argument(self):
        bash = shutil.which("bash")
        if Path("D:/Git/bin/bash.exe").is_file():
            bash = "D:/Git/bin/bash.exe"
        self.assertIsNotNone(bash, "Bash is required to verify the deployment shell")
        workflow = (ROOT / ".github/workflows/hub-deploy.yml").read_text(encoding="utf-8")
        start = re.search(r"^          ENV(?:ARGS|VARS)=\(\)$", workflow, re.M).start()
        update = re.search(r"^          if ! (az containerapp update .+); then$", workflow[start:], re.M)
        self.assertIsNotNone(update)
        assembly = textwrap.dedent(workflow[start:start + update.start()])
        command = update.group(1)
        for quota, paused, layout in itertools.product(["", "owner:test"], ["off", "on"], ["0", "1"]):
            with self.subTest(quota=quota, paused=paused, layout=layout):
                shell = assembly.replace("${{ vars.VINC_QUOTA_EXEMPT_OWNERS }}", quota)
                shell = shell.replace("${{ vars.VINC_EMBED_APPLY_PAUSED || 'off' }}", paused)
                script = ('set -euo pipefail\n'
                          'az() { printf "%s\\n" "$@"; }\n'
                          'APP="--name vinc-hub -g vincsRG"\nIMAGE=test-image\n'
                          f'LAYOUT_PRESENT={layout}\nLAYOUT_REMOTE=https://layout.test\n'
                          + shell + "\n" + command)
                result = subprocess.run([bash, "-c", script], capture_output=True,
                                        text=True, encoding="utf-8", timeout=10, check=True)
                args = result.stdout.splitlines()
                self.assertEqual(args.count("--set-env-vars"), 1,
                                 "Repeated Azure options silently discard earlier settings")
                environment = args[args.index("--set-env-vars") + 1:args.index("--output")]
                expected = [f"VINC_EMBED_APPLY_PAUSED={paused}", "VINC_TOPIC_CONVERSION=1"]
                if quota:
                    expected.insert(0, f"VINC_QUOTA_EXEMPT_OWNERS={quota}")
                if layout == "1":
                    expected += ["VINC_LAYOUT_REMOTE=https://layout.test", "VINC_LAYOUT_TOKEN=secretref:layout-token"]
                self.assertEqual(environment, expected)


if __name__ == "__main__":
    unittest.main()
