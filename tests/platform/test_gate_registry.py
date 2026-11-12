# -*- coding: utf-8 -*-
"""게이트 레지스트리(scripts/gates.py)가 리포와 워크플로와 맞는가.

목록이 여러 곳에 손으로 적히면 한 곳에만 더한 게이트가 조용히 안 돈다. 그래서 레지스트리를
하나로 두고 여기서 양쪽을 대조한다: 리포의 `scripts/gate_*.py` 는 전부 레지스트리에 있고,
레지스트리가 부르는 파일은 전부 있으며, 워크플로가 게이트를 손으로 부르는 자리는 레지스트리가
적은 자리와 정확히 같다. `unittest.TestCase` 인 이유는 verify.py(unittest 로더)에도 보이게.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts import gates as G  # noqa: E402

WORKFLOWS = Path(ROOT) / ".github" / "workflows"


def _runs() -> dict[str, list[str]]:
    """'<워크플로>:<잡>' → 그 잡의 `run:` 본문들."""
    import yaml
    out: dict[str, list[str]] = {}
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(wf.read_text(encoding="utf-8")) or {}
        for job_id, job in (doc.get("jobs") or {}).items():
            out[f"{wf.name}:{job_id}"] = [s.get("run") or "" for s in job.get("steps") or []]
    return out


# 임시 트리에만 있는 가짜 게이트 이름은 경로를 이어 붙여 만든다: 스크립트 경로를 글자 그대로 적으면
# 인용된 스크립트가 실재하는지 보는 테스트가 없는 파일로 읽는다.
_S = "scripts"

class RegistryMatchesTheRepo(unittest.TestCase):
    def test_every_gate_script_is_listed_and_every_command_file_exists(self):
        self.assertEqual(G.audit(), [])

    def test_audit_reports_an_unlisted_gate_and_a_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts" / "design").mkdir(parents=True)
            for name in (f"{_S}/gate_a.py", f"{_S}/gate_b.py", f"{_S}/design/gate_d.py"):
                (root / name).write_text("", encoding="utf-8")
            gates = (G.Gate("a", G.py(f"{_S}/gate_a.py")),
                     G.Gate("c", G.py("-X", "utf8", f"{_S}/gate_c.py")),
                     G.Gate("a", G.py(f"{_S}/design/gate_d.py")))
            problems = "\n".join(G.audit(root, gates))
        self.assertIn(f"{_S}/gate_b.py exists but no gate", problems)
        self.assertIn(f"gate c: {_S}/gate_c.py is named in its command but does not exist", problems)
        self.assertIn("duplicate gate id: a", problems)
        self.assertNotIn("gate_a.py exists", problems)
        self.assertNotIn("gate_d.py exists", problems)


class RegistryMatchesTheWorkflows(unittest.TestCase):
    def test_hand_written_steps_are_exactly_where_the_registry_says(self):
        runs = _runs()
        for g in G.GATES:
            m = G.marker(g)
            found = {where for where, bodies in runs.items() if any(m in b for b in bodies)}
            self.assertEqual(found, set(g.steps),
                             f"{g.id}: workflows run `{m}` in {sorted(found)}, "
                             f"the registry says {sorted(g.steps)}")

    def test_each_registry_job_calls_the_registry(self):
        runs = _runs()
        for job in sorted({g.ci for g in G.GATES if g.ci}):
            bodies = runs.get(f"ci.yml:{job}", [])
            self.assertTrue(any(f"scripts/gates.py --ci {job}" in b for b in bodies),
                            f"ci.yml job {job} does not run scripts/gates.py --ci {job}")
        self.assertTrue(any("scripts/gates.py --ci core" in b for b in runs["verify-hosted.yml:core"]))

    def test_every_ci_job_named_in_a_workflow_has_gates(self):
        import re
        jobs = {g.ci for g in G.GATES if g.ci}
        for where, bodies in _runs().items():
            for b in bodies:
                for name in re.findall(r"scripts/gates\.py --ci ([\w-]+)", b):
                    self.assertIn(name, jobs, f"{where} runs --ci {name}, which has no gates")


def _ctx(ci: bool, base: str = "", head: str = "") -> G.Ctx:
    return G.Ctx(ci=ci, network=ci, base=base, head=head, title="")


class RunnerBehaviour(unittest.TestCase):
    def test_pull_request_only_gate_skips_in_ci_without_a_range(self):
        g = G.Gate("x", G.py("-c", "pass"), needs=("range",))
        status, why, _ = G.run_gate(g, _ctx(ci=True), stream=False)
        self.assertEqual((status, why), ("SKIP", "pull request only: BASE and HEAD are unset"))

    def test_missing_tool_fails_in_ci_and_skips_locally(self):
        g = G.Gate("x", G.py("-c", "pass"), needs=("module:no_such_module_for_gate_registry",))
        self.assertEqual(G.run_gate(g, _ctx(ci=True), stream=False)[0], "FAIL")
        self.assertEqual(G.run_gate(g, _ctx(ci=False), stream=False)[0], "SKIP")

    def test_network_gate_skips_locally_unless_asked(self):
        g = G.Gate("x", G.py("-c", "pass"), needs=("network",))
        status, why, _ = G.run_gate(g, _ctx(ci=False), stream=False)
        self.assertEqual(status, "SKIP")
        self.assertIn("--network", why)

    def test_range_placeholders_are_filled(self):
        g = G.Gate("x", G.py("-c", "import sys; sys.exit(0 if sys.argv[1:] == ['b', 'h'] else 1)",
                             "{BASE}", "{HEAD}"), needs=("range",))
        self.assertEqual(G.run_gate(g, _ctx(ci=True, base="b", head="h"), stream=False)[0], "PASS")

    def test_later_commands_do_not_run_after_a_failure(self):
        g = G.Gate("x", (("python", "-c", "raise SystemExit(3)"),
                         ("python", "-c", "print('second')")))
        status, why, out = G.run_gate(g, _ctx(ci=False), stream=False)
        self.assertEqual((status, why), ("FAIL", "exit 3"))
        self.assertNotIn("second", out)

    def _main(self, gates, argv):
        with mock.patch.object(G, "GATES", gates), \
             mock.patch.object(G, "BY_ID", {g.id: g for g in gates}), \
             mock.patch.dict(os.environ, {"BASE": "", "HEAD": "", "GITHUB_STEP_SUMMARY": ""}):
            return G.main(argv)

    def test_advisory_failure_does_not_block_and_blocking_failure_does(self):
        bad = ("python", "-c", "raise SystemExit(1)")
        ok = ("python", "-c", "pass")
        advisory = (G.Gate("a", (bad,), ci="j", advisory=True), G.Gate("b", (ok,), ci="j"))
        blocking = (G.Gate("a", (ok,), ci="j"), G.Gate("b", (bad,), ci="j"))
        self.assertEqual(self._main(advisory, ["--ci", "j"]), 0)
        self.assertEqual(self._main(blocking, ["--ci", "j"]), 1)

    def test_unknown_ci_job_and_unknown_id_are_usage_errors(self):
        gates = (G.Gate("a", G.py("-c", "pass"), ci="j"),)
        self.assertEqual(self._main(gates, ["--ci", "typo"]), 2)
        self.assertEqual(self._main(gates, ["nope"]), 2)

    def test_relevance_follows_paths_the_gate_script_and_the_registry(self):
        escape = G.BY_ID["escape"]
        self.assertTrue(G.relevant(escape, ["app/src/surfaces/viewGraph.ts"]))
        self.assertTrue(G.relevant(escape, ["scripts/gate_escape.py"]))
        self.assertTrue(G.relevant(escape, ["scripts/gates.py"]))
        self.assertFalse(G.relevant(escape, ["store/graph.py"]))
        self.assertTrue(G.relevant(G.BY_ID["forbidden"], ["store/graph.py"]))


if __name__ == "__main__":
    unittest.main()
