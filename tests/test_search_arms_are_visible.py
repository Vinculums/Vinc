# -*- coding: utf-8 -*-
"""벡터(의미) 팔이 빠진 검색을 **화면이 말하는가**.

이 리포에서 가장 여러 번 날짜가 찍힌 결함이다: 쿼리 임베딩 창구가 답을 못 주면
사이드카는 FTS 만으로 답하는데, 앱은 그 사실을 받아 놓고 **버렸다.** 사용자에게는
완전해 보이는 반쪽 결과가 남는다.

여기서 잠그는 것 넷:
  1. `api.search` 를 부르는 표면 셋(search · ask · topbar)이 전부 알림을 소비한다.
     하나라도 빠지면 같은 고장이 도구에 따라 보이거나 안 보인다.
  2. **그 판정이 실제로 마크업이 된다.** 이것이 급소다 — 처음 판(2026-09-07)은 1번만
     잠갔고, 그래서 세 표면의 **그리는 줄을 전부 지워도**(호출은 남긴 채) 초록이었다.
     화면에는 알림이 한 글자도 안 나오는데. 그리는 자리를 표면마다 한 곳으로 모아 두고
     그 조각을 여기서 잠근다.
  3. 판단이 `arms` 문자열이 아니다. `arms === "fts"` 는 임베딩 창구가 없는 데스크탑의
     **영구 정상 상태**라, 그것으로 띄우면 상시 경보가 되고 상시 경보는 안 읽힌다.
  4. 두 덱(EN · KO)에 문구가 다 있다. 한국어가 기본 화면이라 EN 만 넣으면 침묵한다.

⚠ 앱에는 JS 테스트 러너가 없다(`app/package.json` 에 `test` 없음). 소스를 읽어
   대조하는 것이 이 리포의 관례다(`tests/test_share_progress_is_honest.py` 와 같은 자리).
⚠ `unittest.TestCase` 로 둘 것 — CI 의 무조건 잡은 `unittest.discover` 다.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "app" / "src"
#: `api.search` 를 부르는 표면 전부(2026-09-07 실측). 늘어나면 여기도 늘린다.
#: 값은 (알림을 만드는 호출, **그 판정이 마크업이 되는 유일한 자리**).
#: 둘째 것을 문자 그대로 잡는 것이 일부러다. 이 조각이 사라졌으면 화면은 침묵한다 —
#: 이름을 바꾸느라 빨개졌다면 여기를 새 자리로 다시 겨누라는 뜻이지, 지우라는 뜻이 아니다.
SURFACES = {
    "surfaces/search.ts": ("armsNoticeBadge", "armsNoticeBadge(res) + html"),
    "ask.ts": ("armsNotice", "${esc(t.note)}"),
    "topbar.ts": ("armsNotice", "${notice.text}"),
    # 만들기 흐름의 노드 찾기(`[[` 목록 · 컴포저 연결 대상 · 잇기 띠)가 한 자리에서 부른다.
    "authoring.ts": ("armsNotice", '<p class="cm-assist-note">${esc(note)}</p>'),
}
#: 판정이 호출부에서 그리는 자리까지 건너는 다리. 끊어져도 화면은 똑같이 침묵한다.
#: (ask 는 답을 턴 로그에 실어 두었다가 그 턴을 다시 그린다 — 한 홉이 더 있다.)
BRIDGES = {
    "ask.ts": ("lastNote = armsNotice(res)?.text", "note: lastNote"),
    # `[[` 목록은 편집기 쪽 부품이 그린다 — 판정이 거기까지 건너가야 한다.
    "cm-assist.ts": ("this.note = got.note", "escHtml(this.note)"),
}


def _read(rel: str) -> str:
    return (SRC / rel).read_text(encoding="utf-8")


class ArmsNoticeReachesEverySurfaceTests(unittest.TestCase):

    def test_검색을_부르는_표면이_전부_알림을_소비한다(self):
        callers = sorted(p.relative_to(SRC).as_posix()
                         for p in SRC.rglob("*.ts") if "api.search(" in _read(
                             p.relative_to(SRC).as_posix()))
        self.assertEqual(callers, sorted(SURFACES),
                         "api.search 호출부가 바뀌었다 — 새 표면도 알림을 달 것")
        for rel, (fn, _) in SURFACES.items():
            self.assertIn(f"{fn}(", _read(rel), f"{rel} 이 벡터 팔 소실을 화면에 안 낸다")

    def test_판정이_실제로_마크업에_들어간다(self):
        """부르기만 하고 안 그리면 화면은 침묵한다. 호출만 잠근 판이 놓친 자리다."""
        for rel, (_, frag) in SURFACES.items():
            self.assertIn(frag, _read(rel),
                          f"{rel}: 알림을 부르기만 하고 그리지 않는다 ({frag})")
        for rel, bridges in BRIDGES.items():
            src = _read(rel)
            for b in bridges:
                self.assertIn(b, src, f"{rel}: 판정이 그리는 자리까지 못 간다 ({b})")

    def test_판단은_구조화_필드로만_한다(self):
        src = _read("components.ts")
        body = src[src.index("export function armsNotice("):]
        body = body[:body.index("\n}")]
        for field in ("embed_migration", "vector_rebuilding", "vector_note",
                      "encoder_runtime_warning"):
            self.assertIn(field, body, f"{field} 을 안 본다")
        self.assertNotRegex(body, r"\.\s*arms",
                            "arms 문자열로 판단하면 데스크탑에서 상시 경보가 된다")
        self.assertNotIn("rebuilt", body, "영문 사유 문장을 파싱하고 있다")

    def test_두_덱에_문구가_다_있다(self):
        src = _read("copy.ts")
        for key in ("rebuilding", "degraded", "runtimeMismatch"):
            self.assertEqual(len(re.findall(rf"^\s*{key}:", src, re.M)), 2,
                             f"{key} 가 EN·KO 두 덱에 다 있지 않다")
        # 화면 텍스트에 em 대시 금지(D38) — 새로 더한 두 줄만 본다.
        for m in re.finditer(r"^\s*(?:rebuilding|degraded|runtimeMismatch): (.+)$", src, re.M):
            self.assertNotIn("\u2014", m.group(1), m.group(1))
