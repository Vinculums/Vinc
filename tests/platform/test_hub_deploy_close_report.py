# -*- coding: utf-8 -*-
"""hub-deploy 의 `close-report` 잡이 닫는 것과 안 닫는 것.

`report` 잡은 배포가 실패하면 이슈를 연다. `close-report` 는 뒤의 배포가 성공하면 그
이슈를 닫는다. 이 파일은 워크플로에서 **실제로 도는 스크립트**를 꺼내 가짜 GitHub 앞에서
돌린다 — 판정이 YAML 안에 인라인으로 있어서, 꺼내 돌리지 않으면 러너에서 처음 돈다.

고정하는 것:
  · 닫을 것이 없으면 쓰기가 0건이다(no-op).
  · 이번 커밋이 실패 커밋을 포함할 때만 닫는다. 옛 런을 재실행한 성공은 뒤 커밋의
    실패를 닫지 않는다 — 그 실패는 아직 참이다.
  · 어떤 실패도 예외로 새지 않는다. 배포는 이미 나갔다.
  · 잡의 모양(러너·권한·조건)이 `report` 와 짝을 이룬다.

`unittest.TestCase` 인 이유는 `verify.py`(unittest 로더)에도 보이게 하려는 것이다
(tests/platform/test_ci_suite_is_unconditional.py 의 머리말과 같은 이유).
"""
from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_WF = _ROOT / ".github" / "workflows" / "hub-deploy.yml"

API = "https://api.github.test"
REPO = "Vinculums/Vinc"
SHA = "44e91625c9010ec99a781996c9e62449cd118a82"
RUN_URL = "https://github.test/Vinculums/Vinc/actions/runs/1"
ENV = {"GH_TOKEN": "t", "REPO": REPO, "API": API, "RUN_URL": RUN_URL,
       "WHY": "push", "SHA": SHA}


def _jobs() -> dict:
    return yaml.safe_load(_WF.read_text(encoding="utf-8"))["jobs"]


def _script() -> str:
    """`close-report` 의 run 본문에서 python3 히어독만 꺼낸다."""
    run = _jobs()["close-report"]["steps"][0]["run"]
    head = "<<'PYEOF'\n"
    start = run.index(head) + len(head)
    return run[start:run.index("\nPYEOF", start)]


def _issue(n: int, title: str, pr: bool = False) -> dict:
    d = {"number": n, "title": title}
    if pr:
        d["pull_request"] = {"url": "x"}
    return d


class _GitHub:
    """curl 자리에 앉는 가짜. 부른 것을 (메서드, 경로, 본문) 으로 남긴다."""

    def __init__(self, pages, compare=None, list_code="200",
                 comment_code="201", close_code="200", boom=False):
        self.pages = pages
        self.compare = compare or {}
        self.list_code, self.comment_code, self.close_code = list_code, comment_code, close_code
        self.boom = boom
        self.calls: list[tuple[str, str, object]] = []

    def run(self, cmd, input=None, capture_output=False):
        if self.boom:
            raise OSError("curl 이 없다")
        method = cmd[cmd.index("-X") + 1]
        path = cmd[-1][len(API):]
        body = json.loads(input) if input else None
        self.calls.append((method, path, body))
        code, payload = self._route(method, path)
        out = (json.dumps(payload) if payload is not None else "") + "\n" + code
        return SimpleNamespace(stdout=out.encode("utf-8"), returncode=0)

    def _route(self, method, path):
        if method == "GET" and path.startswith(f"/repos/{REPO}/issues?"):
            page = int(path.rsplit("page=", 1)[1])
            items = self.pages[page - 1] if page <= len(self.pages) else []
            return self.list_code, (items if self.list_code == "200" else {"message": "x"})
        if method == "GET" and path.startswith(f"/repos/{REPO}/compare/"):
            failed = path.split("/compare/", 1)[1].split("...", 1)[0]
            return "200", {"status": self.compare.get(failed, "ahead")}
        if method == "POST" and path.endswith("/comments"):
            return self.comment_code, {}
        if method == "PATCH":
            return self.close_code, {}
        raise AssertionError(f"예상 못 한 호출: {method} {path}")

    def writes(self):
        return [c for c in self.calls if c[0] in ("POST", "PATCH")]


def _run(gh: _GitHub) -> str:
    buf = io.StringIO()
    with mock.patch.dict(os.environ, ENV), mock.patch("subprocess.run", gh.run), \
            redirect_stdout(buf):
        exec(compile(_script(), "close-report", "exec"), {"__name__": "__main__"})
    return buf.getvalue()


class JobShape(unittest.TestCase):
    def test_성공에만_돌고_report_와_같은_러너와_권한이다(self):
        jobs = _jobs()
        job, report = jobs["close-report"], jobs["report"]
        self.assertEqual(job["needs"], report["needs"])
        self.assertEqual(job["if"], "success()")
        self.assertEqual(job["runs-on"], report["runs-on"])
        self.assertEqual(job["permissions"], report["permissions"])
        self.assertIn("timeout-minutes", job)
        self.assertIs(job["steps"][0].get("continue-on-error"), True)

    def test_report_의_제목_머리가_그대로다(self):
        """두 잡은 제목 머리로 이어진다. 한쪽만 바꾸면 이 잡은 아무것도 못 찾는다."""
        report_run = _jobs()["report"]["steps"][0]["run"]
        self.assertIn('f"hub-deploy failed ({e[\'WHY\']}, {sha[:7]})"', report_run)
        self.assertIn('PREFIX = "hub-deploy failed ("', _script())


class Closes(unittest.TestCase):
    def test_실패_커밋을_포함하면_댓글을_달고_닫는다(self):
        gh = _GitHub([[_issue(385, "hub-deploy failed (push, ee5b99d)")]])
        out = _run(gh)
        self.assertIn(("GET", f"/repos/{REPO}/compare/ee5b99d...{SHA}?per_page=1", None), gh.calls)
        (m1, p1, b1), (m2, p2, b2) = gh.writes()
        self.assertEqual((m1, p1), ("POST", f"/repos/{REPO}/issues/385/comments"))
        self.assertIn(RUN_URL, b1["body"])
        self.assertIn(SHA, b1["body"])
        self.assertEqual((m2, p2), ("PATCH", f"/repos/{REPO}/issues/385"))
        self.assertEqual(b2, {"state": "closed", "state_reason": "completed"})
        self.assertIn("1개를 닫았다", out)

    def test_같은_커밋의_재실행_성공도_닫는다(self):
        gh = _GitHub([[_issue(9, f"hub-deploy failed (push, {SHA[:7]})")]],
                     compare={SHA[:7]: "identical"})
        _run(gh)
        self.assertEqual([c[0] for c in gh.writes()], ["POST", "PATCH"])

    def test_첫_쪽_너머도_읽는다(self):
        page1 = [_issue(n, f"unrelated {n}") for n in range(1, 101)]
        page2 = [_issue(200, "hub-deploy failed (workflow_dispatch, abcdef1)")]
        gh = _GitHub([page1, page2])
        _run(gh)
        self.assertTrue(any("page=2" in c[1] for c in gh.calls))
        self.assertEqual(gh.writes()[-1][1], f"/repos/{REPO}/issues/200")


class LeavesOpen(unittest.TestCase):
    def test_닫을_것이_없으면_아무것도_쓰지_않는다(self):
        gh = _GitHub([[_issue(1, "something else"),
                       _issue(2, "hub-deploy failed (push, ee5b99d)", pr=True)]])
        out = _run(gh)
        self.assertEqual(gh.writes(), [])
        self.assertEqual(len(gh.calls), 1)
        self.assertIn("닫을 실패 이슈 없음", out)

    def test_열린_이슈가_아예_없어도_조용히_끝난다(self):
        gh = _GitHub([[]])
        out = _run(gh)
        self.assertEqual(gh.writes(), [])
        self.assertNotIn("::warning::", out)

    def test_이번_커밋이_더_옛것이면_닫지_않는다(self):
        """옛 런을 재실행한 성공. 허브는 뒤 커밋을 여전히 안 들고 있다."""
        gh = _GitHub([[_issue(7, "hub-deploy failed (push, 1234567)")]],
                     compare={"1234567": "behind"})
        out = _run(gh)
        self.assertEqual(gh.writes(), [])
        self.assertIn("열어 둔다", out)

    def test_제목에서_커밋을_못_읽으면_닫지_않는다(self):
        gh = _GitHub([[_issue(8, "hub-deploy failed (나중에 볼 것)")]])
        _run(gh)
        self.assertEqual(gh.writes(), [])
        self.assertFalse(any("/compare/" in c[1] for c in gh.calls))

    def test_댓글이_안_붙으면_닫지_않는다(self):
        gh = _GitHub([[_issue(5, "hub-deploy failed (push, ee5b99d)")]], comment_code="403")
        out = _run(gh)
        self.assertEqual([c[0] for c in gh.writes()], ["POST"])
        self.assertIn("::warning::", out)


class NeverFails(unittest.TestCase):
    def test_목록을_못_읽어도_경고로_끝난다(self):
        gh = _GitHub([[]], list_code="500")
        out = _run(gh)
        self.assertEqual(gh.writes(), [])
        self.assertIn("::warning::", out)

    def test_예상_못_한_오류도_새지_않는다(self):
        out = _run(_GitHub([[]], boom=True))
        self.assertIn("::warning::", out)


if __name__ == "__main__":
    unittest.main()
