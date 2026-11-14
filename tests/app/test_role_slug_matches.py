# -*- coding: utf-8 -*-
"""새 Agent 양식의 이름 규칙(`role-composer.ts` 의 `ROLE_SLUG`)이 서버의 역할 이름 규칙과 같은 답을 내는가.

양식은 `ROLE_SLUG` 로 이름을 미리 거르고, 서버(`api/role_contract.py` 의 `_SLUG`)는 같은 이름을 역할로 알아보거나
경고한다. 정규식 글자가 같아도 두 언어가 다르게 읽는 곳이 있어(파이썬의 `$` 는 끝의 줄바꿈 앞에서도 맞는다)
글자 대조(`test_role_composer`)만으로는 모자라다. 그래서 같은 표본을 양쪽의 실제 정규식에 먹인다:
자바스크립트 쪽은 노드로 소스에서 뽑은 정규식을 돌리고, 파이썬 쪽은 서버가 쓰는 두 길(`is_role` 의 `fullmatch` 와
`check` 의 `match`)을 모두 부른다. `check` 는 id 의 앞뒤 공백을 떼고 맞춰 보므로 끝의 줄바꿈이 `$` 로 새지 않는다.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import unittest

from _src import ROOT, read

sys.path.insert(0, str(ROOT))
from api import role_contract as rc  # noqa: E402

#: 이름으로 받아야 하는 것과 거절해야 하는 것. 접두(`concept:agent-`) 뒤의 조각이다.
VALID = ["a", "0", "dev-core", "maker", "a1-2b", "a-b-c", "x" * 80, "9-lives"]
INVALID = [
    "", "A", "Dev-core", "-a", "a-", "a--b", "a b", "a_b", "a.b", "a/b", "a:b",
    "한글", "é", "ａ", "a\u0000", "a\tb", "a\nb", " a",
]

NODE = shutil.which("node")


def ts_pattern() -> tuple[str, str]:
    """`role-composer.ts` 의 `export const ROLE_SLUG = /…/flags;` 에서 (본문, 플래그)."""
    found = re.search(r"export const ROLE_SLUG = /(.+)/(\w*);", read("role-composer"))
    if found is None:
        raise AssertionError("export const ROLE_SLUG = /…/; not found in role-composer.ts")
    return found.group(1), found.group(2)


def js_answers(samples: list[str]) -> list[bool]:
    """노드가 소스의 정규식으로 `test` 한 결과. 표본은 JSON 으로 넘긴다(제어 문자도 그대로 간다)."""
    source, flags = ts_pattern()
    program = ("const [source, flags, samples] = JSON.parse(process.argv[1]);"
               "const re = new RegExp(source, flags);"
               "console.log(JSON.stringify(samples.map((s) => re.test(s))));")
    done = subprocess.run([NODE, "-e", program, json.dumps([source, flags, samples])],
                          capture_output=True, text=True, timeout=20)
    if done.returncode != 0:
        raise AssertionError(done.stderr)
    return json.loads(done.stdout)


def server_full(slug: str) -> bool:
    """역할로 알아보는 길(`is_role`)이 이름 조각에 거는 검사."""
    return rc._SLUG.fullmatch(slug) is not None


def server_warns(slug: str) -> bool:
    """역할 노드를 쓸 때 `check` 가 이름에 대해 경고하는가."""
    node = {"id": rc.ID_PREFIX + slug, "domain": rc.DOMAIN, "props": {}}
    return any("use lowercase letters, digits and hyphens" in w for w in rc.check(node, created=True))


@unittest.skipUnless(NODE, "Node.js is required to run the form's regular expression")
class RoleSlugMatchesTests(unittest.TestCase):
    def test_the_form_accepts_the_names_the_server_accepts(self) -> None:
        got = js_answers(VALID)
        for slug, form in zip(VALID, got):
            with self.subTest(slug=slug[:30]):
                self.assertTrue(form, "the form refuses a name the server accepts")
                self.assertTrue(server_full(slug))
                self.assertFalse(server_warns(slug))

    def test_the_form_refuses_the_names_the_server_refuses(self) -> None:
        got = js_answers(INVALID)
        for slug, form in zip(INVALID, got):
            with self.subTest(slug=slug):
                self.assertFalse(form, "the form accepts a name the server refuses")
                self.assertFalse(server_full(slug))
                self.assertTrue(server_warns(slug))


if __name__ == "__main__":
    unittest.main()
