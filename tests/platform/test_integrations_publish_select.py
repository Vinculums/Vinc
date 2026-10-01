"""Tag and source validation for the integration publish workflow."""
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.integrations_publish_select import PACKAGE_DIRS, select_package


class SelectPackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name, path in PACKAGE_DIRS.items():
            project = self.root / path / "pyproject.toml"
            project.parent.mkdir(parents=True)
            project.write_text(f'[project]\nname = "{name}"\nversion = "0.1.0"\n',
                               encoding="utf-8")

    def test_each_tag_selects_only_its_own_package(self):
        for name, path in PACKAGE_DIRS.items():
            with self.subTest(name=name):
                self.assertEqual(
                    select_package("push", f"refs/tags/integrations-{name}-v0.1.0",
                                   "", self.root), (name, path, "0.1.0"))

    def test_wrong_version_and_unrelated_tag_are_rejected(self):
        for ref in ("refs/tags/integrations-vinc-client-v0.2.0",
                    "refs/tags/integrations-vinc-client-v0.1.0-extra",
                    "refs/tags/v0.1.0", "refs/heads/main"):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                select_package("push", ref, "", self.root)

    def test_dispatch_is_testpypi_only_from_main(self):
        self.assertEqual(select_package("workflow_dispatch", "refs/heads/main",
                                        "vinc-langgraph", self.root),
                         ("vinc-langgraph", PACKAGE_DIRS["vinc-langgraph"], "0.1.0"))
        for ref, name in (("refs/heads/feature", "vinc-client"),
                          ("refs/heads/main", "unknown")):
            with self.subTest(ref=ref, name=name), self.assertRaises(ValueError):
                select_package("workflow_dispatch", ref, name, self.root)

    def test_source_name_and_presence_are_checked(self):
        path = self.root / PACKAGE_DIRS["vinc-client"] / "pyproject.toml"
        path.write_text('[project]\nname="other"\nversion="0.1.0"\n', encoding="utf-8")
        with self.assertRaises(ValueError):
            select_package("push", "refs/tags/integrations-vinc-client-v0.1.0", "",
                           self.root)
        path.unlink()
        with self.assertRaises(ValueError):
            select_package("push", "refs/tags/integrations-vinc-client-v0.1.0", "",
                           self.root)


class WorkflowBoundaryTests(unittest.TestCase):
    def test_only_package_tags_publish_automatically_with_oidc(self):
        path = Path(__file__).resolve().parents[2] / ".github/workflows/integrations-publish.yml"
        source = path.read_text(encoding="utf-8")
        workflow = yaml.load(source, Loader=yaml.BaseLoader)
        self.assertEqual(set(workflow["on"]["push"]), {"tags"})
        self.assertEqual(set(workflow["on"]["push"]["tags"]),
                         {f"integrations-{name}-v*" for name in PACKAGE_DIRS})
        self.assertIn("workflow_dispatch", workflow["on"])
        job = workflow["jobs"]["publish"]
        self.assertEqual(job["permissions"], {"contents": "read", "id-token": "write"})
        self.assertIn("testpypi", job["environment"]["name"])
        self.assertIn("pypi", job["environment"]["name"])
        self.assertNotIn("secrets.", source)


if __name__ == "__main__":
    unittest.main()
