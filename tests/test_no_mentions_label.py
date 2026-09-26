# -*- coding: utf-8 -*-
"""화면 문구는 "mentions" 를 빌리지 않는다(2026-09-26 발주자 판정).

옵시디언의 "Linked mentions · Unlinked mentions" 는 **이 노트를 언급한 다른 노트**(들어오는
방향)다. Vinc 리더 머리의 칩 줄은 **이 문서가 걸린 개념**(나가는 방향)이라, 같은 말을 쓰면
옵시디언을 쓰던 사람일수록 반대로 읽는다. 라벨은 "Concepts in this document" / "이 문서의
개념" 이다. 데이터 모델의 `MENTIONS` 엣지는 그대로다 — 지식그래프의 표준 용어이고 스키마·
팩·MCP 도구 설명이 그 이름에 묶여 있다(보는 이는 어시스턴트뿐).
근거: [[decision:the-reader-says-concepts-in-this-document-not-mentions-2026-09-26]]"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

COPY_TS = (Path(__file__).resolve().parents[1] / "app" / "src" / "copy.ts").read_text(encoding="utf-8")


def _strings(src: str) -> list[str]:
    """copy.ts 의 문자열 리터럴(따옴표·백틱). 주석은 빼고 본다."""
    body = re.sub(r"^\s*//.*$", "", src, flags=re.M)
    return re.findall(r'"([^"\\\n]*)"|`([^`]*)`', body)


class NoMentionsLabelTests(unittest.TestCase):
    def test_화면_문구에_mention_이_없다(self):
        for pair in _strings(COPY_TS):
            text = (pair[0] or pair[1]).lower()
            for bad in ("mention", "멘션"):
                self.assertNotIn(bad, text, text)

    def test_리더_라벨은_이_문서의_개념이다(self):
        self.assertIn('readerConcepts: "Concepts in this document",', COPY_TS)
        self.assertIn('readerConcepts: "이 문서의 개념",', COPY_TS)
        self.assertNotIn("readerMentions", COPY_TS)


if __name__ == "__main__":
    unittest.main()
