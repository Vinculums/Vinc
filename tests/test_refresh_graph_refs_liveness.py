# -*- coding: utf-8 -*-
"""라이브 인용 대조 — **"없다"와 "못 물어봤다"를 가르는가.**

이 판정은 hub-deploy 안에서 돌고 실패하면 배포가 죽는다. 2026-09-09 에 타임아웃 한 번이
"죽은 인용 1건"으로 찍혀 배포를 죽였고, 그 노드는 라이브 그래프에 멀쩡히 있었다. 로그에는
"코드가 가리키는 id 가 라이브 그래프에 없다"고 적히고 바로 아랫줄에 `remote unreachable:
URLError: timed out` 이 있었다.

같은 부류가 한 번 더 있었다(응답 상한을 넘긴 허브 노드가 `isError` 로 돌아와 "없다"로
찍힌 것). 그때는 질문을 바꿔 증상만 닫았다. 이 파일이 부류를 닫는다.
"""
from __future__ import annotations

import unittest
from unittest import mock

from scripts import refresh_graph_refs as R

TIMEOUT = {"error": "remote unreachable: URLError: <urlopen error timed out>",
           "isError": True}
ABSENT = {"found": False}
PRESENT = {"found": True, "neighbors": []}


class VerifyNodes(unittest.TestCase):
    def test_a_server_that_answers_no_is_an_absence(self):
        with mock.patch.object(R.remote, "call", return_value=ABSENT):
            alive, unreachable = R.verify_nodes(["record:x"])
        self.assertEqual((alive, unreachable), ([], []))

    def test_a_transport_failure_is_not_an_absence(self):
        """여기가 배포를 죽였던 자리다. 못 물어본 것은 없는 것이 아니다."""
        with mock.patch.object(R.remote, "call", return_value=TIMEOUT):
            alive, unreachable = R.verify_nodes(["record:x"])
        self.assertEqual(alive, [])
        self.assertEqual(unreachable, ["record:x"])

    def test_it_retries_a_transport_failure_before_giving_up(self):
        """타임아웃은 일시적이라 한 번 더 물어본다 — 그러고도 안 되면 포기한다."""
        calls = []

        def flaky(*a, **kw):
            calls.append(1)
            return TIMEOUT if len(calls) < 2 else PRESENT

        with mock.patch.object(R.remote, "call", side_effect=flaky):
            alive, unreachable = R.verify_nodes(["record:x"])
        self.assertEqual((alive, unreachable), (["record:x"], []))
        self.assertEqual(len(calls), 2)


class CheckMode(unittest.TestCase):
    """`--check` 는 배포 안에서 돈다 — 못 물어봤으면 **건너뛴다**(0)."""

    def _check(self, call_result, hits):
        with mock.patch.dict("os.environ", {"VINC_REMOTE_MCP_TOKEN": "t"}), \
             mock.patch.object(R, "scan_repo", return_value=hits), \
             mock.patch.object(R, "fetch_docs", return_value=[]), \
             mock.patch.object(R.remote, "call", return_value=call_result):
            return R.check()

    def test_unreachable_does_not_fail_the_deploy(self):
        self.assertEqual(self._check(TIMEOUT, [("a.py", 1, "record:x")]), 0)

    def test_a_real_absence_still_fails(self):
        """건너뛰기가 진짜 드리프트까지 덮으면 이 게이트는 있으나 마나다."""
        self.assertEqual(self._check(ABSENT, [("a.py", 1, "record:x")]), 1)

    def test_all_present_passes(self):
        self.assertEqual(self._check(PRESENT, [("a.py", 1, "record:x")]), 0)


class RefreshMode(unittest.TestCase):
    """쓰기 경로는 반대다 — 구멍 난 대조표를 쓰느니 **멈춘다**.

    빠뜨린 노드를 쓰면 그 구멍이 커밋되고, 오프라인 게이트가 나중에 그 인용을 죽었다고
    잡는다. 사람이 로컬에서 돌리는 명령이라 시끄럽게 멈추는 쪽이 맞다."""

    def test_it_refuses_to_write_a_manifest_with_holes(self):
        with mock.patch.object(R, "scan_repo",
                               return_value=[("a.py", 1, "record:x")]), \
             mock.patch.object(R, "fetch_docs", return_value=[]), \
             mock.patch.object(R.remote, "call", return_value=TIMEOUT), \
             mock.patch("io.open", side_effect=AssertionError("매니페스트를 썼다")), \
             mock.patch.object(R.sys, "argv", ["refresh_graph_refs.py"]):
            self.assertEqual(R.main(), 1)


if __name__ == "__main__":
    unittest.main()
