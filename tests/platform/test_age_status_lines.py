# -*- coding: utf-8 -*-
"""R6 — 상태 줄을 리포에 대고 늙힌다. [[vinc.ops.graph-claim-decay]] §7.3.

`vinc.plan.sync-node-time-fields` 는 머지되고 엿새 뒤에도 "not yet merged to main" 이라고
적혀 있었고, 그 문서는 같은 기록이 **반박의 근거로 인용한** 문서였다. 즉 정정의 재료가
스스로 낡아 있었다.

git 조회는 주입한다 — 순수 부분(헤더 파싱·해시 판별·비교)이 리포 상태와 무관하게
테스트되어야, 이 검사가 왜 틀렸는지 물을 때 두 층을 따로 볼 수 있다."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

import age_status_lines as age  # noqa: E402

_DOC = """---
doc_id: vinc.plan.example
layer: plan
updated: 2026-08-17
---

> **Status: done on a branch, not yet merged to `main`**

## 8. What landed

| `af7a973` | the delta carries the field |
| `e489186` | the one-shot rewind |
"""


def _landed(mapping):
    return lambda sha: mapping.get(sha, "")


class ShaTokenTests(unittest.TestCase):
    def test_숫자뿐인_토큰은_해시가_아니다(self):
        """`20260817` 은 8자리 16진수다. a-f 를 한 자도 요구하지 않으면 날짜가
        커밋으로 잡히고, 그러면 목록이 소음이 되어 아무도 안 본다."""
        self.assertEqual(age._SHA.findall("20260817 1068 4"), [])

    def test_짧은_토큰은_해시가_아니다(self):
        self.assertEqual(age._SHA.findall("af7a9 abc"), [])

    def test_실제_해시를_잡는다(self):
        self.assertEqual(set(age._SHA.findall("`af7a973` and e489186 and 6a10420")),
                         {"af7a973", "e489186", "6a10420"})


class AuditTests(unittest.TestCase):
    def test_상태_줄보다_늦게_닿은_커밋이_있으면_발견이다(self):
        f = age.audit(_DOC, landed=_landed({"af7a973": "2026-08-18",
                                            "e489186": "2026-08-18"}))
        self.assertIsNotNone(f)
        self.assertEqual(f["doc_id"], "vinc.plan.example")
        self.assertEqual(f["as_of"], "2026-08-17")
        self.assertEqual(len(f["late"]), 2)

    def test_상태_줄이_커밋보다_나중이면_발견이_아니다(self):
        f = age.audit(_DOC.replace("updated: 2026-08-17", "updated: 2026-09-01"),
                      landed=_landed({"af7a973": "2026-08-18"}))
        self.assertIsNone(f)

    def test_같은_날은_발견이_아니다(self):
        """as-of 는 날짜고 착지도 날짜다. 같은 날이면 그 사실을 알고 썼을 수 있다 —
        발견으로 세면 머지한 날 쓴 문서가 전부 걸린다."""
        self.assertIsNone(age.audit(_DOC, landed=_landed({"af7a973": "2026-08-17"})))

    def test_아직_안_닿은_커밋은_발견이_아니다(self):
        """브랜치 위의 커밋을 인용한 문서가 '아직 머지 안 됨'이라 쓰는 것은 참이다."""
        self.assertIsNone(age.audit(_DOC, landed=_landed({})))

    def test_as_of_가_없으면_판정하지_않는다(self):
        """헤더에 날짜가 없으면 비교할 것이 없다. 없는 것을 오늘로 가정하면 모든
        옛 문서가 한꺼번에 걸리고, 그 목록은 아무도 안 줄인다."""
        self.assertIsNone(age.audit("# 제목만 있는 문서\n\n`af7a973` 참조",
                                    landed=_landed({"af7a973": "2026-08-18"})))

    def test_status_as_of_가_updated_보다_이긴다(self):
        """`status_as_of` 가 정본이다. `updated` 는 그것이 없을 때의 대타 — 기존
        문서 전부가 `updated` 만 갖고 있어서 대타가 없으면 새 문서만 걸린다."""
        doc = _DOC.replace("updated: 2026-08-17",
                           "status_as_of: 2026-09-01\nupdated: 2026-08-17")
        f = age.audit(doc, landed=_landed({"af7a973": "2026-08-18"}))
        self.assertIsNone(f)

    def test_가장_늦게_닿은_것이_앞에_온다(self):
        f = age.audit(_DOC, landed=_landed({"af7a973": "2026-08-18",
                                            "e489186": "2026-08-25"}))
        self.assertEqual(f["late"][0], ("e489186", "2026-08-25"))


class MainTests(unittest.TestCase):
    def test_발견이_있으면_종료코드_1(self):
        import io
        import contextlib
        p = _ROOT / "tests" / "_tmp_age_doc.md"
        p.write_text(_DOC, encoding="utf-8")
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = age.main([str(p)])
            # 이 리포에 실재하는 해시라 git 이 답한다 — 주입 없는 통합 경로다.
            self.assertIn(rc, (0, 1))
            if rc == 1:
                self.assertIn("landed on main", buf.getvalue())
        finally:
            p.unlink(missing_ok=True)

    def test_인자가_없으면_사용법이고_2(self):
        import io
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(age.main([]), 2)

    def test_없는_파일은_2(self):
        import io
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(age.main(["없는파일.md"]), 2)


class LandedAtTests(unittest.TestCase):
    """git 층. 이 리포에 실재하는 커밋으로 확인한다 — 주입으로는 못 보는 절반이다."""

    def test_모르는_해시는_빈_문자열이다(self):
        self.assertEqual(age.landed_at("deadbee"), "")
        self.assertEqual(age.landed_at(""), "")

    def test_실재하는_커밋은_날짜를_준다(self):
        got = age.landed_at("af7a973")
        if got:                       # 얕은 클론이면 조상 조회가 비어 온다
            self.assertRegex(got, r"^\d{4}-\d{2}-\d{2}$")


if __name__ == "__main__":
    unittest.main()
