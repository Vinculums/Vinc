# -*- coding: utf-8 -*-
"""공유 시트의 **문서 수**는 장식이 아니라 계측기다.

팩의 `documents` 는 고르는 것이 아니라 파생된다 — `api/share.py` `_docs_for_concepts`
가 담긴 개념을 언급하는 문서만 역추적해 싣는다. 그래서 스코프가 어긋나면 개념도
문서도 0인 **빈 팩**이 아무 오류 없이 저장되고, 여태 그 사실이 처음 보이는 자리는
**받는 쪽**의 가져오기 시트였다. 보내는 사람이 저장 전에 알아챌 수 있는 자리는
공유 시트의 이 한 줄뿐이다.

⚠ 이 줄은 **두 층에 걸쳐 있다** — 서버가 `manifest.counts.documents` 를 세고 앱이
그 키를 읽는다. 한쪽만 이름이 바뀌면 화면은 오류 없이 `0` 을 찍는다(= 빈 팩과
구분 불가). 그래서 양쪽을 여기서 대조한다. 앱에는 JS 테스트 러너가 없다
(`app/package.json` 에 `test` 없음) — `tests/test_view_url.py` 와 같은 자리다.

⚠ `unittest.TestCase` 로 둘 것. CI 의 무조건 잡은 `unittest.discover` 라 모듈 수준
`def test_*` 를 아예 수집하지 못한다.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _body(src: str, head: str) -> str:
    """`head` 로 시작하는 최상위 함수의 본문만 잘라 낸다 — 열 0 의 `}` 가 끝이다."""
    body = src[src.index(head):]
    return body[:body.index("\n}")]


class ShareSheetSpecTests(unittest.TestCase):
    def test_공유_시트가_문서_수를_보여준다(self):
        src = (ROOT / "app" / "src" / "sheets.ts").read_text(encoding="utf-8")
        spec = _body(src, "function specRows")
        self.assertIn("specDocs", spec, "공유 시트 사양 표에 문서 줄이 없다")
        self.assertIn("c.documents", spec, "문서 줄이 매니페스트 카운트를 안 읽는다")
        # 헬퍼가 **있는 것**과 **불리는 것**은 다르다 — 위 둘은 헬퍼 본문만 보므로
        # 호출부를 지워도 그대로 통과한다(변이로 실측). 그러면 문서 줄을 포함한 사양 표
        # 전체가 공유 시트에서 사라져도 가드는 초록이다 — 막으려던 바로 그것이다.
        share = _body(src, "export async function openShareSheet")
        self.assertIn("specRows(c, scopeLabel)", share, "공유 시트가 사양 표를 안 그린다")

    def test_서버가_같은_이름으로_문서를_센다(self):
        """앱이 읽는 키를 빌더가 실제로 넣는가 — 이름이 갈리면 화면은 조용히 0 이다."""
        src = (ROOT / "api" / "share.py").read_text(encoding="utf-8")
        self.assertIn('"documents": len(documents)', src)


if __name__ == "__main__":
    unittest.main()
