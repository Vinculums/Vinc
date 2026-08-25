# -*- coding: utf-8 -*-
"""첫 문이 **어느 그래프인지**도 말한다 — 개인인가 팀인가.

정본은 [[decision:identity-rides-on-the-first-door-not-a-whoami]]. 그 결정은 신원을
whoami 뒤에 두지 않고 **안 물어도 오는 자리**에 뒀다(`vinc_conventions` + 쓰기 둘).
불러야 하는 도구는 자기가 하는 줄 모르는 실수를 못 잡기 때문이다.

그런데 실린 것은 `owner` 문자열뿐이었고, 그것이 개인 그래프인지 팀 그래프인지는
**접두를 아는 사람만** 읽을 수 있었다. `scope` 는 그 한 걸음을 없앤다. 도구를 하나
더 만들지 않는 이유는 결정 그대로다 — 22개 설명은 요청마다 무는 비용이다.

여기서 고정하는 것 셋:
  ① 세 값이 실제로 갈린다(personal · team · unscoped).
  ② 팀 접두 사본이 MCP 층의 정본과 어긋나지 않는다. 어긋나면 **팀 그래프가
     personal 로 보고되고**, 그건 조용한 오답이라 최악이다.
  ③ 도구 설명이 그 필드들의 존재를 말한다. 값이 선에 있는데 계약이 침묵하면
     아무도 안 읽는다(R2 가 한 층 아래에서 겪은 그것)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import server as sv  # noqa: E402


class OwnerScopeTests(unittest.TestCase):
    def test_세_값이_갈린다(self):
        self.assertEqual(sv._owner_scope("user:019fae48-cc62-7984-b2b0-1698f4de55d4"),
                         "personal")
        self.assertEqual(sv._owner_scope("team:0198c0de-0000-7000-8000-000000000000"),
                         "team")
        for empty in (None, ""):
            self.assertEqual(sv._owner_scope(empty), "unscoped")

    def test_owner_없음과_개인을_구분한다(self):
        """빈 문자열을 돌려주면 호출부가 '모른다'와 '개인이다'를 못 가른다.
        로컬 단일 사용자 스토어는 소유자가 아예 없는 것이지 개인 그래프가 아니다."""
        self.assertNotEqual(sv._owner_scope(None), sv._owner_scope("user:x"))


class TeamPrefixMirrorTests(unittest.TestCase):
    """접두가 두 층에 있다 — 사이드카(사본)와 MCP 허브(정본). 어긋나면 팀 그래프가
    personal 로 보고되고 아무도 오류를 못 본다. 역할표를 두 곳에 두고 테스트로
    대조하는 `record:fact-ap1-role-table-mirror` 와 같은 처방이다."""

    def test_사이드카_접두가_허브_정규식과_같은_것을_가리킨다(self):
        from vinc_mcp.http_server import _TEAM_OWNER_RE
        sample = "team:0198c0de-1111-7222-8333-444455556666"
        self.assertTrue(_TEAM_OWNER_RE.fullmatch(sample), "표본이 정본을 통과해야 한다")
        self.assertTrue(sample.startswith(sv._TEAM_OWNER_PREFIX))
        self.assertEqual(sv._owner_scope(sample), "team")

    def test_개인_owner_는_허브_정규식도_거부한다(self):
        from vinc_mcp.http_server import _TEAM_OWNER_RE
        personal = "user:0198c0de-1111-7222-8333-444455556666"
        self.assertIsNone(_TEAM_OWNER_RE.fullmatch(personal))
        self.assertEqual(sv._owner_scope(personal), "personal")


class ConventionsContractTests(unittest.TestCase):
    def test_설명이_돌려주는_필드를_말한다(self):
        """이 검사가 있는 이유: 결정은 신원을 이 응답에 실었고 값은 실제로 왔는데,
        설명에 owner 를 담은 문장은 둘 다 소유격('the owner's GRAPH.md')이라
        **응답이 그것을 알려 준다는 말이 0건**이었다(2026-08-25 실측)."""
        from vinc_mcp.tools import TOOLS
        d = next(t for t in TOOLS if t["name"] == "vinc_conventions")["description"]
        for field in ("`owner`", "`scope`", "`target`"):
            self.assertIn(field, d, field)

    def test_설명이_팀은_여기서_못_닿는다고_말한다(self):
        """발주자가 실제로 물은 것이 이것이다. owner 를 봐도 '팀 그래프가 있고
        여기서는 주소가 안 잡힌다'는 안 나온다 — 신원이 아니라 도달 가능성이다."""
        from vinc_mcp.tools import TOOLS
        d = next(t for t in TOOLS if t["name"] == "vinc_conventions")["description"]
        self.assertIn("team", d)
        self.assertIn("web app", d)

    def test_whoami_도구는_여전히_없다(self):
        """결정이 기각한 것을 슬쩍 되살리지 않았는지 — 도구 수는 22 그대로다."""
        from vinc_mcp.tools import TOOLS
        self.assertEqual([t for t in TOOLS if "whoami" in t["name"]], [])
        self.assertEqual(len(TOOLS), 22)


_DRIVER = None
try:
    from store.embedded_backend import _import_driver
    _DRIVER, _ = _import_driver()
except Exception:
    pass


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class ConventionsResponseE2E(unittest.TestCase):
    """단위가 아니라 **실경로**로 본다. 반환 지점이 둘이라(저장본 · 기본 템플릿)
    한쪽만 고치고 통과하는 일이 실제로 가능하고, 이 소유자가 타는 것은 저장본 쪽이다.
    값이 함수에 있는 것과 응답에 실리는 것은 다른 사실이다 — R2 가 가르친 것."""

    def setUp(self):
        import os
        import tempfile
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_DATA_DIR": os.path.join(tempfile.mkdtemp(), "store")}.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        from store.config import load_config
        from store.backend import open_store
        self.store = open_store(load_config())
        self.store.provision(dim=4)
        self.app = sv.VincApp(store=self.store)

    def tearDown(self):
        import os
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _scope(self, owner):
        code, res = self.app.read_conventions({"owner": owner} if owner else {})
        self.assertEqual(code, 200, res)
        return res["source"], res.get("scope"), res.get("owner")

    def test_기본_템플릿_분기가_scope_를_싣는다(self):
        self.assertEqual(self._scope("user:a"), ("default", "personal", "user:a"))
        self.assertEqual(self._scope("team:0198c0de-1111-7222-8333-444455556666")[1],
                         "team")
        self.assertEqual(self._scope(None), ("default", "unscoped", ""))

    def test_저장본_분기도_scope_를_싣는다(self):
        """반환 지점 둘 중 이쪽이 실제 소유자가 타는 길이다."""
        from api import docstore, graphmd
        body = "\n".join(["# GRAPH.md", "", "## Node kinds",
                          "- Concept: one idea.", ""])
        docstore.put_doc(self.store, text=body, doc_id=graphmd.CONVENTIONS_DOC_ID,
                         owner="user:a", title="GRAPH.md")
        source, scope, owner = self._scope("user:a")
        self.assertEqual((source, scope, owner), ("doc", "personal", "user:a"))

    def test_메서드가_클래스에_붙어_있다(self):
        """헬퍼를 클래스 본문 안에 들여쓰기 없이 끼우면 그 아래 메서드가 전부
        모듈 함수가 된다. 문법은 맞아서 import 도 되고 단위 검사도 통과한다 —
        2026-08-25 에 실제로 그렇게 깨뜨렸다."""
        for name in ("read_conventions", "read_nav_tree", "read_diagnose"):
            self.assertTrue(hasattr(sv.VincApp, name), name)


if __name__ == "__main__":
    unittest.main()
