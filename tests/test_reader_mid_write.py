# -*- coding: utf-8 -*-
"""쓰기 창에 걸린 문서를 앱의 두 리더가 어떻게 다루는가(P17b).

P17(#241)이 `doc_get` 에 가드를 넣어 반쪽 문서를 `executed:false` 로 거절하게 했다.
그런데 앱에서는 그 거절이 **다른 모든 실패와 같은 문구**로 떨어지고 있었다. 그 상태가
"빈 문서"로 읽히는 것이 이 결함의 전부다: 문서 열은 상시 편집이고 저장은 2초 디바운스라,
빈 편집기에 키 하나가 들어가면 2초 뒤 그 반쪽이 정본이 되고 이전 본을 들고 있는 것은
아무것도 없다.

여기서 지키는 것은 그래서 셋이다 — 순서가 곧 중요도다:
  ① 그 상태에서 **편집기를 세우지 않는다**(분기가 마운트 앞에서 돌아간다)
  ② 그 상태와 "정말 빈 문서"가 **다른 문구**로 갈린다
  ③ 재시도는 **사람이 누를 때만** 일어난다(크래시 잔해면 자동 루프는 영원히 돈다)

판별 열은 `chunk_count` 다. 서버가 그 열을 안 실으면 위 셋이 조용히 전부 죽으므로
그 계약도 함께 잰다. 서버 쪽 거동 자체는 `test_docstore.py` 가 실제로 돌려서 잰다.

앱 쪽에는 JS 테스트 러너가 없다(`app/package.json` 에 `test` 없음) — `test_lnb_tint.py`
와 같은 자리에 두는 이유가 그것이다."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app" / "src"
API = (SRC / "api.ts").read_text(encoding="utf-8")
ED = (SRC / "doc-editor.ts").read_text(encoding="utf-8")
VIEW = (SRC / "surfaces" / "view.ts").read_text(encoding="utf-8")
COPY = (SRC / "copy.ts").read_text(encoding="utf-8")
DOCSTORE = (ROOT / "api" / "docstore.py").read_text(encoding="utf-8")


def at(src: str, needle: str) -> int:
    """`str.index` 의 ValueError 대신 무엇이 없어졌는지 말하는 실패를 낸다."""
    i = src.find(needle)
    assert i >= 0, f"소스에서 사라졌다: {needle}"
    return i


class MidWriteDiscriminantTests(unittest.TestCase):
    def test_서버가_판별_열을_계속_싣는다(self):
        """`chunk_count` 는 이 갈래에만 실리는 표식이고, 앱의 분기 전체가 그것에 달렸다.
        서버가 이 열을 떼면 앱은 조용히 옛 문구로 돌아간다(타입체크도 게이트도 안 운다)."""
        self.assertIn('base["chunks"], base["chunk_count"] = len(rows), expected', DOCSTORE)

    def test_판별은_error_가_아니라_청크_수로_한다(self):
        """`error` 는 모르는 id·소유자 불일치에도 실린다 — 그것으로 가르면 오타 하나가
        "쓰는 중"으로 보인다."""
        m = re.search(r"export const docMidWrite = .*?;\n", API, re.S)
        self.assertIsNotNone(m, "api.ts 가 판별기를 안 내보낸다")
        body = m.group(0)
        self.assertIn("d.executed === false", body)
        self.assertIn("d.chunk_count !== undefined", body)
        self.assertNotIn("error", body)

    def test_일시와_영구를_못_가른다는_사실이_적혀_있다(self):
        """크래시가 남긴 무청크 상태는 며칠 간다. "잠시 뒤"만 말하면 그때 거짓말이 되고,
        응답에는 둘을 가를 열이 없다. 그 이유를 코드가 들고 있어야 다음 사람이 이
        문구를 "쓸데없이 길다"며 반으로 줄이지 않는다."""
        head = API[:API.index("export const docMidWrite")]
        self.assertIn("못 가른다", head[-1200:])


class ReadersDoNotOpenAnEditorTests(unittest.TestCase):
    """① 이 패키지의 전부 — 그 상태에서 편집을 켜지 않는다."""

    def test_편집기_리더는_마운트_앞에서_돌아간다(self):
        branch = at(ED, "docMidWrite(doc)")
        mount = at(ED, 'host.classList.add("doc-render")')
        self.assertLess(branch, mount, "분기가 편집기 마운트 뒤에 있으면 소용이 없다")
        self.assertRegex(ED[branch:mount], r"\n\s*return \{",
                         "분기와 마운트 사이에 반환이 없다 — 편집기가 그대로 선다")

    def test_뷰_리더도_마운트_앞에서_돌아간다(self):
        branch = at(VIEW, "docMidWrite(doc)")
        mount = at(VIEW, "await mountDocEditor(")
        self.assertLess(branch, mount)
        self.assertRegex(VIEW[branch:mount], r"\n\s*return;")

    def test_돌아간_뒤_넘기는_손잡이는_스스로_편집을_못_켠다(self):
        """호출부가 `setEditable(true)` 를 눌러도 켜질 편집기가 없어야 한다. 재시도가
        성공한 뒤에만(=`again` 이 채워진 뒤에만) 위임된다."""
        stub = ED[at(ED, "docMidWrite(doc)"):at(ED, 'host.classList.add("doc-render")')]
        self.assertIn("setEditable: (on) => again?.setEditable(on)", stub)
        self.assertIn("let again: DocEditorHandle | null = null;", stub)


class RetryIsManualTests(unittest.TestCase):
    """③ 자동 재시도 금지 — 크래시 잔해에서는 영원히 돈다."""

    def test_두_리더_모두_사람의_클릭에만_다시_읽는다(self):
        for name, src, btn in (("doc-editor", ED, "ed-retry"), ("view", VIEW, "rdr-retry")):
            with self.subTest(name):
                self.assertIn(f'id="{btn}"', src, "재시도 버튼이 없다")
                after = src[at(src, f'id="{btn}"'):]
                self.assertIn(f'querySelector("#{btn}")', after)
                self.assertIn('addEventListener("click"', after[:600])
                self.assertNotRegex(after[:600], r"set(Timeout|Interval)")


class CopyTests(unittest.TestCase):
    """② 화면에서 갈린다 — 그리고 두 덱 다."""

    def test_두_덱_모두_새_문구를_갖는다(self):
        for key in ("readerMidWrite", "readerRetry"):
            with self.subTest(key):
                self.assertEqual(COPY.count(f"{key}: "), 2, "EN·KO 한 벌씩이어야 한다")

    def test_빈_문서_문구와_다른_말을_한다(self):
        """같은 문자열을 재사용하면 화면에서 안 갈린다 — 이 패키지가 고친 것이 그것이다."""
        mid = re.findall(r'readerMidWrite: "([^"]+)"', COPY)
        fail = re.findall(r'readerFail: "([^"]+)"', COPY)
        self.assertEqual((len(mid), len(fail)), (2, 2))
        self.assertEqual(set(mid) & set(fail), set())

    def test_화면_문구에_em_dash_가_없다(self):
        for s in re.findall(r'reader(?:MidWrite|Retry): "([^"]+)"', COPY):
            self.assertNotIn("—", s)


if __name__ == "__main__":
    unittest.main()
