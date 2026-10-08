# -*- coding: utf-8 -*-
"""노트북이나 폐기된 Cloud 풀로 CI가 되돌아가는 것을 막는다."""
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
HOSTED = {"ubuntu-latest", "ubuntu-22.04", "windows-latest", "macos-latest", "macos-15"}


class HostedRunnerContract(unittest.TestCase):
    def test_every_workflow_job_uses_a_hosted_runner(self):
        jobs_seen = 0
        for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
            workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
            for name, job in workflow.get("jobs", {}).items():
                jobs_seen += 1
                runner = job.get("runs-on")
                if runner == "${{ matrix.os }}":
                    matrix = job["strategy"]["matrix"]
                    labels = matrix.get("os", []) + [row["os"] for row in matrix.get("include", [])]
                    self.assertTrue(labels, f"{path.name}:{name}: empty runner matrix")
                else:
                    labels = [runner]
                for label in labels:
                    self.assertIsInstance(label, str, f"{path.name}:{name}: indirect runner labels")
                    self.assertIn(label, HOSTED, f"{path.name}:{name}: non-hosted runner")
        self.assertGreater(jobs_seen, 0)

    def test_linux_release_preserves_the_public_runtime_floor(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))
        build = workflow["jobs"]["build"]
        linux = next(row for row in build["strategy"]["matrix"]["include"]
                     if row["triple"] == "x86_64-unknown-linux-gnu")
        self.assertEqual(linux["os"], "ubuntu-22.04")
        self.assertNotIn("container", build)
        commands = "\n".join(step.get("run", "") for step in build["steps"])
        self.assertIn("getconf GNU_LIBC_VERSION", commands)
        self.assertIn('"glibc 2.35"', commands)
        self.assertIn("libwebkit2gtk-4.1-dev", commands)
        self.assertNotIn("image", workflow["jobs"])


if __name__ == "__main__":
    unittest.main()
