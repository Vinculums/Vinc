"""새 Agent 역할 양식(app/src/role-composer.ts)이 서버의 역할 계약과 같은 값을 쓰는가.

양식은 자리(tier)·모델 단계(rung)를 칩으로, 이름을 정규식으로 미리 거른다. 그 값이 서버
(`api/role_contract.py`)와 갈리면 양식이 받은 것을 서버가 경고하거나, 서버가 받을 것을 양식이
막는다. 둘 다 오류 없이 지나가므로 여기서 글자로 대조한다.
근거: [[decision:agent-roles-live-under-a-top-level-agent-topic-2026-10-02]]
"""
from __future__ import annotations

import re
import unittest

from _src import ROOT, read

SRC = read("role-composer")
LNB = read("lnb")

import sys  # noqa: E402
sys.path.insert(0, str(ROOT))
from api import role_contract as rc  # noqa: E402


def _ts_list(name: str) -> tuple[str, ...]:
    m = re.search(rf"export const {name} = \[([^\]]*)\] as const;", SRC)
    assert m, name
    return tuple(re.findall(r'"([a-z]+)"', m.group(1)))


class SameContractAsTheServer(unittest.TestCase):
    def test_tiers_and_rungs(self):
        self.assertEqual(_ts_list("TIERS"), rc.TIERS)
        self.assertEqual(_ts_list("RUNGS"), rc.RUNGS)

    def test_the_name_rule(self):
        m = re.search(r"export const ROLE_SLUG = /(.+)/;", SRC)
        self.assertEqual(m.group(1), rc._SLUG.pattern)

    def test_required_props_are_all_sent(self):
        for key in rc.REQUIRED:
            self.assertRegex(SRC, rf"\b{key}\b", key)

    def test_files_under_the_new_role_topic(self):
        self.assertIn("const topic = AGENT_TOPICS[0];", SRC)
        self.assertIn('export const AGENT_TOPICS = ["agent", "vinc/agent"] as const;', LNB)

    def test_the_agent_rail_plus_opens_it(self):
        self.assertIn('void import("./role-composer").then((m) => m.openRoleComposer());', LNB)


if __name__ == "__main__":
    unittest.main()
