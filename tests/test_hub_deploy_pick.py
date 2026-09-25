# -*- coding: utf-8 -*-
"""hub-deploy 의 `plan` 이 무엇을 내보내고 무엇을 안 내보내는가.

판정은 `scripts/hub_deploy_pick.py` 의 `pick()` 하나에 있다. 바깥(API·git·az)은 전부
인자라서 여기서 가짜 이력을 꽂아 돌린다. 그리고 워크플로가 그 판정만 믿는지 —
`workflow_run` 런에서 `github.sha` 는 내보낼 커밋이 아니다 — 를 파일에서 확인한다.

`unittest.TestCase` 인 이유: 모듈 수준 `def test_*` 는 verify.py(unittest 로더)에 안 보인다
(tests/test_ci_suite_is_unconditional.py 머리말).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))

import hub_deploy_pick as hp  # noqa: E402

_WF = _ROOT / ".github" / "workflows" / "hub-deploy.yml"
_CI = _ROOT / ".github" / "workflows" / "ci.yml"


def _sha(n: int) -> str:
    return f"{n:040x}"


class _History:
    """first-parent 가 일직선인 main. c[0] 이 가장 옛것이다."""

    def __init__(self, n: int, files: dict[int, list[str]], status: dict[int, str]):
        self.c = [_sha(i + 1) for i in range(n)]
        self.files = {self.c[i]: f for i, f in files.items()}
        self.status = {self.c[i]: s for i, s in status.items()}
        self.asked: list[str] = []

    def newest_first(self) -> list[str]:
        return list(reversed(self.c))

    def ci_status(self, sha: str) -> str:
        self.asked.append(sha)
        return self.status.get(sha, "success")

    def is_commit(self, sha: str) -> bool:
        return sha in self.c

    def is_ancestor(self, a: str, b: str) -> bool:
        return self.c.index(a) <= self.c.index(b)

    def changed(self, a: str, b: str) -> list[str]:
        i, j = sorted((self.c.index(a), self.c.index(b)))
        out: list[str] = []
        for sha in self.c[i + 1:j + 1]:
            out += self.files.get(sha, [])
        return out

    def parent_of(self, sha: str) -> str:
        i = self.c.index(sha)
        return self.c[i - 1] if i else ""

    def run(self, *, live: int | None, trigger: int, conclusion: str = "success") -> hp.Plan:
        return hp.pick(
            event="workflow_run", trigger_sha=self.c[trigger], trigger_conclusion=conclusion,
            dispatch_sha="", live=self.c[live] if live is not None else "",
            main_commits=self.newest_first(), ci_status=self.ci_status,
            is_commit=self.is_commit, is_ancestor=self.is_ancestor, changed=self.changed,
            parent_of=self.parent_of)


class HubPaths(unittest.TestCase):
    def test_디렉터리는_접두로_파일은_정확히(self):
        yes = ["api/server.py", "vinc_mcp/http_server.py", "store/x/y.py", "engines/a.py",
               "packaging/lbug_extensions/fts.so", "Dockerfile.hub", "pyproject.toml",
               "packaging/docker-entrypoint.sh", "scripts/check_hub_env.py",
               ".github/workflows/hub-deploy.yml"]
        no = ["apix/a.py", "docs/pyproject.toml", "packaging/other.py", "app/src/api/x.ts",
              "tests/test_api.py", ".github/workflows/ci.yml", "Dockerfile.hub.bak"]
        for p in yes:
            self.assertTrue(hp.touches_hub([p]), p)
        for p in no:
            self.assertFalse(hp.touches_hub([p]), p)


class Pick(unittest.TestCase):
    def test_초록이고_허브를_건드렸으면_내보낸다(self):
        h = _History(3, {2: ["api/server.py"]}, {})
        plan = h.run(live=1, trigger=2)
        self.assertEqual((plan.deploy, plan.sha, plan.withheld), (True, h.c[2], ""))

    def test_늦게_끝난_옛_커밋이_새_커밋을_되돌리지_않는다(self):
        # c3 가 먼저 초록이 됐고 c2 의 CI 가 늦게 끝나 이 런을 불렀다. 내보내는 것은 c3 다.
        h = _History(4, {2: ["api/a.py"], 3: ["store/b.py"]}, {})
        plan = h.run(live=1, trigger=2)
        self.assertEqual((plan.deploy, plan.sha), (True, h.c[3]))

    def test_새_커밋이_아직_돌면_그_앞의_초록을_내보낸다(self):
        h = _History(4, {2: ["api/a.py"], 3: ["api/b.py"]}, {3: "pending"})
        plan = h.run(live=1, trigger=2)
        self.assertEqual((plan.deploy, plan.sha), (True, h.c[2]))

    def test_허브_경로가_안_바뀌었으면_안_내보낸다(self):
        h = _History(3, {2: ["app/src/main.ts", "tests/test_x.py"]}, {})
        plan = h.run(live=1, trigger=2)
        self.assertFalse(plan.deploy)
        self.assertEqual(plan.sha, "")

    def test_떠_있는_것이_main_머리면_아무것도_묻지_않는다(self):
        h = _History(3, {2: ["api/a.py"]}, {})
        plan = h.run(live=2, trigger=2)
        self.assertFalse(plan.deploy)
        self.assertEqual(h.asked, [])

    def test_떠_있는_것에서_멈춘다_그_아래는_묻지_않는다(self):
        h = _History(6, {5: ["api/a.py"]}, {})
        h.run(live=3, trigger=5)
        self.assertEqual(h.asked, [h.c[5]])

    def test_떠_있는_것보다_새_초록이_없으면_안_내보낸다(self):
        h = _History(4, {2: ["api/a.py"], 3: ["api/b.py"]}, {2: "failure", 3: "failure"})
        plan = h.run(live=1, trigger=3, conclusion="failure")
        self.assertFalse(plan.deploy)

    def test_빨간_CI_가_허브_커밋이면_보류로_알린다(self):
        h = _History(3, {2: ["vinc_mcp/x.py"]}, {2: "failure"})
        plan = h.run(live=1, trigger=2, conclusion="failure")
        self.assertEqual((plan.deploy, plan.withheld), (False, h.c[2]))

    def test_빨간_CI_가_허브를_안_건드렸으면_알리지_않는다(self):
        # 앞 커밋의 허브 변경이 아직 안 나갔어도 이 커밋 자신이 아니면 조용하다 —
        # 고칠 때까지 main push 마다 이슈가 새로 열리지 않게.
        h = _History(4, {2: ["api/a.py"], 3: ["docs/x.md"]}, {2: "failure", 3: "failure"})
        plan = h.run(live=1, trigger=3, conclusion="failure")
        self.assertEqual(plan.withheld, "")

    def test_보류와_앞의_초록_배포는_함께_간다(self):
        h = _History(4, {2: ["api/a.py"], 3: ["api/b.py"]}, {3: "failure"})
        plan = h.run(live=1, trigger=3, conclusion="failure")
        self.assertEqual((plan.deploy, plan.sha, plan.withheld), (True, h.c[2], h.c[3]))

    def test_러스트만_빨간_트리거는_보류가_아니고_그대로_내보낸다(self):
        # 2026-09-25 e9be2fa: 런은 failure 인데 core 잡은 초록(윈도우 cargo test 의 메모리 부족).
        h = _History(3, {2: [".github/workflows/hub-deploy.yml"]}, {2: "success"})
        plan = h.run(live=1, trigger=2, conclusion="failure")
        self.assertEqual((plan.deploy, plan.sha, plan.withheld), (True, h.c[2], ""))

    def test_취소된_CI_도_보류로_센다(self):
        h = _History(3, {2: ["api/a.py"]}, {2: "failure"})
        plan = h.run(live=1, trigger=2, conclusion="cancelled")
        self.assertEqual(plan.withheld, h.c[2])

    def test_태그를_못_읽으면_가장_새_초록을_내보낸다(self):
        h = _History(3, {}, {})
        plan = h.run(live=None, trigger=2)
        self.assertEqual((plan.deploy, plan.sha), (True, h.c[2]))

    def test_창_밖의_떠_있는_커밋은_diff_로_판정한다(self):
        # 떠 있는 c1 이 훑은 창(최근 2개) 밖이다. 창 전체를 보고 c1 과 diff 한다.
        h = _History(5, {2: ["api/a.py"]}, {})
        plan = hp.pick(
            event="workflow_run", trigger_sha=h.c[4], trigger_conclusion="success",
            dispatch_sha="", live=h.c[1], main_commits=h.newest_first()[:2],
            ci_status=h.ci_status, is_commit=h.is_commit, is_ancestor=h.is_ancestor,
            changed=h.changed, parent_of=h.parent_of)
        self.assertEqual((plan.deploy, plan.sha), (True, h.c[4]))

    def test_떠_있는_것이_더_새것이면_되돌리지_않는다(self):
        # 손으로 더 새 커밋을 올린 뒤(창 밖) 옛 머리의 런이 왔다.
        h = _History(5, {3: ["api/a.py"]}, {})
        plan = hp.pick(
            event="workflow_run", trigger_sha=h.c[2], trigger_conclusion="success",
            dispatch_sha="", live=h.c[4], main_commits=list(reversed(h.c[:3])),
            ci_status=h.ci_status, is_commit=h.is_commit, is_ancestor=h.is_ancestor,
            changed=h.changed, parent_of=h.parent_of)
        self.assertFalse(plan.deploy)

    def _dispatch(self, h: _History, sha: str) -> hp.Plan:
        return hp.pick(
            event="workflow_dispatch", trigger_sha="", trigger_conclusion="",
            dispatch_sha=sha, live="", main_commits=[], ci_status=h.ci_status,
            is_commit=h.is_commit, is_ancestor=h.is_ancestor, changed=h.changed,
            parent_of=h.parent_of)

    def test_손으로_부르면_경로와_무관하게_그_커밋을_내보낸다(self):
        h = _History(2, {1: ["docs/x.md"]}, {})
        plan = self._dispatch(h, h.c[1])
        self.assertEqual((plan.deploy, plan.sha), (True, h.c[1]))

    def test_손으로_불러도_CI_가_초록이_아니면_죽는다(self):
        h = _History(2, {}, {1: "failure"})
        with self.assertRaises(hp.Unanswered):
            self._dispatch(h, h.c[1])

    def test_모르는_이벤트는_죽는다(self):
        h = _History(2, {}, {})
        with self.assertRaises(hp.Unanswered):
            hp.pick(event="push", trigger_sha="", trigger_conclusion="", dispatch_sha=h.c[1],
                    live="", main_commits=[], ci_status=h.ci_status, is_commit=h.is_commit,
                    is_ancestor=h.is_ancestor, changed=h.changed, parent_of=h.parent_of)


def _job(name: str, conclusion: str | None = "success", status: str = "completed") -> dict:
    return {"name": name, "status": status, "conclusion": conclusion}


_CORE = "core (498 tests + forbidden-terms gate)"
_PKNS = "core (VINC_PK_NAMESPACED=on)"


class Classify(unittest.TestCase):
    """한 커밋의 CI 판정은 core 잡 둘만 본다."""

    def run_of(self, *jobs, status="completed", id_=1):
        return {"id": id_, "status": status, "jobs": list(jobs)}

    def test_core_둘이_초록이면_러스트가_빨개도_초록(self):
        runs = [self.run_of(_job(_CORE), _job(_PKNS), _job("rust (cargo check) (win)", "failure"),
                            _job("app (typecheck)", "failure"))]
        self.assertEqual(hp.classify(runs), "success")

    def test_core_하나라도_빨가면_실패(self):
        runs = [self.run_of(_job(_CORE), _job(_PKNS, "failure"))]
        self.assertEqual(hp.classify(runs), "failure")

    def test_core_가_아직_돌면_대기(self):
        runs = [self.run_of(_job(_CORE), _job(_PKNS, None, "in_progress"), status="in_progress")]
        self.assertEqual(hp.classify(runs), "pending")

    def test_잡이_아직_안_생긴_런은_대기(self):
        self.assertEqual(hp.classify([self.run_of(status="queued")]), "pending")

    def test_런이_없으면_none(self):
        self.assertEqual(hp.classify([]), "none")

    def test_잡_0개로_끝난_런은_실패(self):
        # 워크플로 검증 실패는 잡을 하나도 안 만들고 끝난다.
        self.assertEqual(hp.classify([self.run_of()]), "failure")

    def test_core_잡을_못_찾으면_판정하지_않고_죽는다(self):
        runs = [self.run_of(_job("tests (renamed)"), _job("app (typecheck)"))]
        with self.assertRaises(hp.Unanswered):
            hp.classify(runs)

    def test_런_하나라도_core_가_초록이면_초록(self):
        runs = [self.run_of(_job(_CORE, "failure"), _job(_PKNS), id_=1),
                self.run_of(_job(_CORE), _job(_PKNS), id_=2)]
        self.assertEqual(hp.classify(runs), "success")

    def test_ci_yml_의_core_잡_이름이_접두와_맞다(self):
        ci = yaml.safe_load(_CI.read_text(encoding="utf-8"))
        names = [j.get("name", k) for k, j in ci["jobs"].items()]
        gate = [n for n in names if n.startswith(hp.GATE_JOB_PREFIX)]
        self.assertEqual(sorted(gate), sorted([_CORE, _PKNS]),
                         "core 잡 이름이 바뀌면 GATE_JOB_PREFIX 와 이 테스트를 같이 고칠 것")


class WorkflowShape(unittest.TestCase):
    """워크플로가 picker 의 답만 쓰는가."""

    @classmethod
    def setUpClass(cls):
        cls.doc = yaml.safe_load(_WF.read_text(encoding="utf-8"))
        cls.ci = yaml.safe_load(_CI.read_text(encoding="utf-8"))

    def test_main_의_ci_가_끝나면_돈다(self):
        on = self.doc[True] if True in self.doc else self.doc["on"]  # PyYAML 은 `on` 을 True 로 읽는다
        self.assertNotIn("push", on, "push 로 돌면 CI 를 기다리지 않는다")
        wr = on["workflow_run"]
        self.assertEqual(wr["workflows"], [self.ci["name"]])
        self.assertEqual(wr["types"], ["completed"])
        self.assertEqual(wr["branches"], ["main"])
        self.assertIn("workflow_dispatch", on)

    def test_github_sha_는_dispatch_입력_한_자리뿐이다(self):
        hits = []
        for name, job in self.doc["jobs"].items():
            for k, v in (job.get("env") or {}).items():
                if "github.sha" in str(v):
                    hits.append((name, "env", k))
            for i, step in enumerate(job.get("steps") or []):
                for key in ("run", "with", "env"):
                    blob = step.get(key)
                    if blob is None:
                        continue
                    items = blob.items() if isinstance(blob, dict) else [("", blob)]
                    for k, v in items:
                        # 주석 줄은 빼고 본다.
                        text = "\n".join(line for line in str(v).splitlines()
                                         if not line.lstrip().startswith("#"))
                        if "github.sha" in text:
                            hits.append((name, i, key, k))
        allowed = {("plan", 3, "env", "DISPATCH_SHA")}
        # report 의 SHA 는 plan 이 죽었을 때의 마지막 대체값이다(`||` 사슬의 끝).
        allowed.add(("report", 0, "env", "SHA"))
        self.assertEqual(set(hits) - allowed, set())

    def test_배포는_plan_이_고른_커밋을_받는다(self):
        job = self.doc["jobs"]["build-and-deploy"]
        self.assertEqual(job["needs"], "plan")
        self.assertIn("needs.plan.outputs.deploy == 'true'", job["if"])
        self.assertEqual(job["env"]["DEPLOY_SHA"], "${{ needs.plan.outputs.sha }}")
        checkout = job["steps"][0]
        self.assertTrue(checkout["uses"].startswith("actions/checkout@"))
        self.assertEqual(checkout["with"]["ref"], "${{ needs.plan.outputs.sha }}")

    def test_배포_워크플로가_스위트를_다시_돌지_않는다(self):
        for name, job in self.doc["jobs"].items():
            for step in job.get("steps") or []:
                run = step.get("run") or ""
                self.assertNotIn("verify.py", run, name)
                self.assertNotRegex(run, r"(?m)^\s*python3? -m pytest", name)

    def test_보류는_초록_런에서도_이슈를_연다(self):
        report = self.doc["jobs"]["report"]
        self.assertIn("failure()", report["if"])
        self.assertIn("needs.plan.outputs.withheld", report["if"])


if __name__ == "__main__":
    unittest.main()
