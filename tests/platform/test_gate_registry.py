# -*- coding: utf-8 -*-
"""게이트 레지스트리(scripts/gates.py)가 리포와 워크플로와 맞는가.

목록이 여러 곳에 손으로 적히면 한 곳에만 더한 게이트가 조용히 안 돈다. 그래서 레지스트리를
하나로 두고 여기서 양쪽을 대조한다: 리포의 `scripts/gate_*.py` 는 전부 레지스트리에 있고,
레지스트리가 부르는 파일은 전부 있으며, 워크플로가 게이트를 손으로 부르는 자리는 레지스트리가
적은 자리와 정확히 같다. `unittest.TestCase` 인 이유는 verify.py(unittest 로더)에도 보이게.
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import shlex
import shutil
import sys
import tempfile
import time
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


# ── 레지스트리를 부르는 스텝은 건너뛰거나 무시될 수 없다 ───────────────────────

#: ci.yml 밖에서 레지스트리를 부르는 잡. ci.yml 의 잡은 레지스트리의 `ci=` 가 이름을 댄다.
HOSTED_REGISTRY_JOBS = ("verify-hosted.yml:core",)

# 첫 낱말이 파이썬인 줄만 호출로 친다: `echo "… scripts/gates.py --ci core …"` 같은 글은 호출이 아니다.
_CALL = re.compile(r"^\s*\S*python\S*\s.*\bscripts/gates\.py\b(?P<rest>.*)$")


def _registry_calls(run: str) -> list[str]:
    """`run:` 본문이 `scripts/gates.py --ci <잡>` 으로 부르는 잡 이름들."""
    jobs: list[str] = []
    for line in run.splitlines():
        m = _CALL.match(line)
        if m:
            jobs += re.findall(r"--ci[ =]([\w-]+)", m.group("rest"))
    return jobs


def registry_step_problems(workflows: Path, gates=G.GATES) -> list[str]:
    """레지스트리 스텝이 선택 사항이 되는 자리를 문장으로 돌려준다. 빈 목록이면 정합이다.

    - 레지스트리 스텝에 `if:` 나 `continue-on-error:` 가 있다: 건너뛰거나 무시되면 막는 게이트가
      레지스트리를 아무도 고치지 않은 채 선택 사항이 된다.
    - 그 스텝의 잡에 `continue-on-error:` 가 있거나, ci.yml 의 그 잡에 `if:` 가 있다(건너뛴 필수
      잡은 성공으로 읽힌다). verify-hosted 의 잡 `if:` 는 수동 실행의 선택 입력이라 둔다.
    - 레지스트리가 이름 댄 잡(과 HOSTED_REGISTRY_JOBS)마다 레지스트리 스텝이 정확히 하나가 아니다.
    - 스텝이 자기 잡이 아닌 `--ci` 를 부른다.
    """
    import yaml
    problems: list[str] = []
    count: dict[str, int] = {}
    for wf in sorted(workflows.glob("*.yml")):
        doc = yaml.safe_load(wf.read_text(encoding="utf-8")) or {}
        for job_id, job in (doc.get("jobs") or {}).items():
            where = f"{wf.name}:{job_id}"
            for step in job.get("steps") or []:
                called = _registry_calls(step.get("run") or "")
                if not called:
                    continue
                count[where] = count.get(where, 0) + 1
                name = step.get("name") or "(unnamed)"
                for key in ("if", "continue-on-error"):
                    if key in step:
                        problems.append(f"{where}: step {name!r} runs the registry "
                                        f"but carries {key}: {step[key]!r}")
                if called != [job_id]:
                    problems.append(f"{where}: step {name!r} runs --ci {' --ci '.join(called)}, "
                                    f"expected --ci {job_id}")
            if where in count:
                if "continue-on-error" in job:
                    problems.append(f"{where}: the job runs the registry but carries "
                                    f"continue-on-error: {job['continue-on-error']!r}")
                if wf.name == "ci.yml" and "if" in job:
                    problems.append(f"{where}: the job runs the registry but carries if: {job['if']!r}")
    named = {f"ci.yml:{g.ci}" for g in gates if g.ci} | set(HOSTED_REGISTRY_JOBS)
    for where in sorted(named | set(count)):
        if count.get(where, 0) != 1:
            problems.append(f"{where}: {count.get(where, 0)} registry step(s), expected exactly one")
    return problems


_CORE_STEP = "      - name: Gates (registry, core)\n        run: python -X utf8 scripts/gates.py --ci core\n"
_CHANGES_STEP = "      - name: Gates (registry, changes)\n"
_CORE_JOB = "  core:\n    name: core (498 tests + forbidden-terms gate)\n"


class RegistryStepsAreNotOptional(unittest.TestCase):
    def test_the_workflows_have_one_unconditional_registry_step_per_job(self):
        self.assertEqual(registry_step_problems(WORKFLOWS), [])

    def _problems_after(self, old: str, new: str) -> str:
        """워크플로 사본의 ci.yml 에서 old 를 new 로 바꾼 뒤의 문제들."""
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            for wf in WORKFLOWS.glob("*.yml"):
                shutil.copy(wf, scratch / wf.name)
            ci = scratch / "ci.yml"
            text = ci.read_text(encoding="utf-8")
            self.assertIn(old, text, "the fixture no longer matches ci.yml")
            ci.write_text(text.replace(old, new, 1), encoding="utf-8")
            return "\n".join(registry_step_problems(scratch))

    def test_an_if_on_a_registry_step_is_reported(self):
        problems = self._problems_after(_CORE_STEP, _CORE_STEP.replace(
            "        run:", "        if: github.event_name != 'merge_group'\n        run:"))
        self.assertIn("ci.yml:core: step 'Gates (registry, core)' runs the registry but carries if", problems)

    def test_continue_on_error_on_a_registry_step_is_reported(self):
        problems = self._problems_after(_CHANGES_STEP,
                                        _CHANGES_STEP + "        continue-on-error: true\n")
        self.assertIn("ci.yml:changes: step 'Gates (registry, changes)' runs the registry "
                      "but carries continue-on-error: True", problems)

    def test_a_second_registry_step_in_a_job_is_reported(self):
        problems = self._problems_after(_CORE_STEP, _CORE_STEP * 2)
        self.assertIn("ci.yml:core: 2 registry step(s), expected exactly one", problems)

    def test_a_condition_on_a_ci_job_that_runs_the_registry_is_reported(self):
        problems = self._problems_after(_CORE_JOB, _CORE_JOB + "    if: github.event_name == 'push'\n")
        self.assertIn("ci.yml:core: the job runs the registry but carries if", problems)

    def test_an_echoed_command_is_not_a_registry_call(self):
        self.assertEqual(_registry_calls('echo "| core (scripts/gates.py --ci core) |"'), [])
        self.assertEqual(_registry_calls("  python -X utf8 scripts/gates.py --ci changes"), ["changes"])


# ── 결정 문: 레지스트리 항목과 ci.yml 스텝의 명령 ─────────────────────────────

#: ci.yml 스텝에만 있는 선택지. 출력 형식과 스텝 요약 · 원본 결과 파일 · 실행 기록 자리이고,
#: 어느 경로를 고르고 어느 결정을 찾는지는 바꾸지 않는다.
_CI_ONLY_OPTIONS = ("--format", "--summary", "--trace", "--run-id", "--task-ref", "--host")


class DecisionGateMatchesItsStep(unittest.TestCase):
    def test_the_registry_runs_the_ci_step_command_without_its_ci_only_options(self):
        import yaml
        doc = yaml.safe_load((WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))
        step = next(s for s in doc["jobs"]["changes"]["steps"] if s.get("id") == "gate")
        run = step["run"].replace("\\\n", " ")
        line = next(ln for ln in run.splitlines() if "api.cli" in ln and "gate check" in ln)
        argv = shlex.split(line)
        argv = argv[:argv.index(">")] if ">" in argv else argv
        kept: list[str] = []
        it = iter(argv)
        for tok in it:
            if tok in _CI_ONLY_OPTIONS:
                next(it)
                continue
            kept.append("{BASE}" if tok == "origin/$BASE_REF" else tok)
        gate = G.BY_ID["decision-gate"]
        self.assertEqual(len(gate.cmd), 1)
        self.assertEqual(kept, list(gate.cmd[0]))


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

    def _main(self, gates, argv, actions: str = ""):
        # GITHUB_ACTIONS 는 늘 명시한다: CI 에서 이 테스트가 돌 때 가짜 게이트의 주석이 체크 화면에
        # 올라가면 안 된다.
        with mock.patch.object(G, "GATES", gates), \
             mock.patch.object(G, "BY_ID", {g.id: g for g in gates}), \
             mock.patch.dict(os.environ, {"BASE": "", "HEAD": "", "GITHUB_STEP_SUMMARY": "",
                                          "GITHUB_ACTIONS": actions}):
            return G.main(argv)

    def _main_output(self, gates, argv, actions: str = "") -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = self._main(gates, argv, actions)
        return code, buf.getvalue()

    def test_a_blocking_failure_in_actions_prints_one_escaped_annotation(self):
        noisy = ("python", "-c", "print('50% done :: see below'); print('second line');"
                                 " raise SystemExit(1)")
        ok = ("python", "-c", "pass")
        gates = (G.Gate("fx", (noisy,), ci="j"),
                 G.Gate("a:b,c", (("python", "-c", "raise SystemExit(4)"),), ci="j"),
                 G.Gate("adv", (noisy,), ci="j", advisory=True),
                 G.Gate("fine", (ok,), ci="j"))
        code, out = self._main_output(gates, ["--ci", "j"], actions="true")
        self.assertEqual(code, 1)
        errors = [ln for ln in out.splitlines() if ln.startswith("::error")]
        self.assertEqual(errors, ["::error title=gate fx::exit 1: 50%25 done :: see below",
                                  "::error title=gate a%3Ab%2Cc::exit 4"])
        # 같은 실행을 Actions 밖에서 돌리면 주석이 없다.
        code, out = self._main_output(gates, ["--ci", "j"])
        self.assertEqual(code, 1)
        self.assertNotIn("::error", out)

    def test_annotation_escapes_the_workflow_command_format(self):
        self.assertEqual(G.annotation("x,y:z", "100%\r\nnext :: line"),
                         "::error title=gate x%2Cy%3Az::100%25%0D%0Anext :: line")
        self.assertEqual(G.failure_line("exit 2", "\n\n  first\nsecond\n"), "exit 2: first")
        self.assertEqual(G.failure_line("exit 1", "── header\n[OK] a\n[DRIFT] b differs\n[FAIL] c\n"),
                         "exit 1: [DRIFT] b differs")
        self.assertEqual(G.failure_line("timed out after 3 s", ""), "timed out after 3 s")

    def test_a_timed_out_gate_blocks_unless_it_is_advisory(self):
        slow = (("python", "-c", "import time; time.sleep(60)"),)
        code, out = self._main_output((G.Gate("slow", slow, ci="j", timeout=1),),
                                      ["--ci", "j"], actions="true")
        self.assertEqual(code, 1)
        self.assertIn("::error title=gate slow::timed out after 1 s", out)
        code, out = self._main_output((G.Gate("slow", slow, ci="j", timeout=1, advisory=True),),
                                      ["--ci", "j"], actions="true")
        self.assertEqual(code, 0)
        self.assertIn("FAIL (advisory)  slow                 timed out after 1 s", out)

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

    @unittest.skipUnless(os.name == "posix", "프로세스 그룹은 POSIX 에서 본다")
    def test_a_gate_past_its_timeout_fails_and_its_grandchild_is_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = Path(tmp) / "grandchild.pid"
            code = ("import subprocess, sys, time; "
                    "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
                    f"open({str(pidfile)!r}, 'w').write(str(p.pid)); time.sleep(120)")
            g = G.Gate("slow", G.py("-c", code), timeout=1)
            started = time.monotonic()
            status, why, _ = G.run_gate(g, _ctx(ci=False), stream=False)
            elapsed = time.monotonic() - started
            pid = int(pidfile.read_text(encoding="utf-8"))
        self.assertEqual((status, why), ("FAIL", "timed out after 1 s"))
        self.assertLess(elapsed, 1 + G._TERM_GRACE + 2)
        self.assertFalse(_alive(pid), f"grandchild {pid} is still running")

    def test_the_default_timeout_applies_to_every_gate_without_one(self):
        self.assertEqual(G.Gate("x", G.py("-c", "pass")).timeout, G.DEFAULT_TIMEOUT)
        self.assertTrue(all(g.timeout >= G.DEFAULT_TIMEOUT for g in G.GATES))

    def test_relevance_follows_paths_the_gate_script_and_the_registry(self):
        escape = G.BY_ID["escape"]
        self.assertTrue(G.relevant(escape, ["app/src/surfaces/viewGraph.ts"]))
        self.assertTrue(G.relevant(escape, ["scripts/gate_escape.py"]))
        self.assertTrue(G.relevant(escape, ["scripts/gates.py"]))
        self.assertFalse(G.relevant(escape, ["store/graph.py"]))
        self.assertTrue(G.relevant(G.BY_ID["forbidden"], ["store/graph.py"]))


def _alive(pid: int, wait: float = 5.0) -> bool:
    """pid 가 wait 초 안에 사라지지 않고 살아 있는가. 거둬지기 전의 좀비는 죽은 것으로 친다."""
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        proc = Path(f"/proc/{pid}/stat")
        if Path("/proc").is_dir():
            try:
                if proc.read_text(encoding="utf-8").rsplit(")", 1)[1].split()[0] == "Z":
                    return False
            except OSError:
                return False
        time.sleep(0.1)
    return True


if __name__ == "__main__":
    unittest.main()
