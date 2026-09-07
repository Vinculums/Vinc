# -*- coding: utf-8 -*-
"""벡터(의미) 팔이 빠진 검색을 **화면이 말하는가**.

이 리포에서 가장 여러 번 날짜가 찍힌 결함이다: 쿼리 임베딩 창구가 답을 못 주면
사이드카는 FTS 만으로 답하는데, 앱은 그 사실을 받아 놓고 **버렸다.** 사용자에게는
완전해 보이는 반쪽 결과가 남는다.

여기서 잠그는 것 셋:
  1. `api.search` 를 부르는 표면 셋(search · ask · topbar)이 전부 알림을 소비한다.
     하나라도 빠지면 같은 고장이 도구에 따라 보이거나 안 보인다.
  2. 판단이 `arms` 문자열이 아니다. `arms === "fts"` 는 임베딩 창구가 없는 데스크탑의
     **영구 정상 상태**라, 그것으로 띄우면 상시 경보가 되고 상시 경보는 안 읽힌다.
  3. 두 덱(EN · KO)에 문구가 다 있다. 한국어가 기본 화면이라 EN 만 넣으면 침묵한다.

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
SURFACES = {
    "surfaces/search.ts": "armsNoticeBadge",
    "ask.ts": "armsNotice",
    "topbar.ts": "armsNotice",
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
        for rel, fn in SURFACES.items():
            self.assertIn(f"{fn}(", _read(rel), f"{rel} 이 벡터 팔 소실을 화면에 안 낸다")

    def test_판단은_구조화_필드로만_한다(self):
        src = _read("components.ts")
        body = src[src.index("export function armsNotice("):]
        body = body[:body.index("\n}")]
        for field in ("embed_migration", "vector_rebuilding", "vector_note"):
            self.assertIn(field, body, f"{field} 을 안 본다")
        self.assertNotRegex(body, r"\.\s*arms",
                            "arms 문자열로 판단하면 데스크탑에서 상시 경보가 된다")
        self.assertNotIn("rebuilt", body, "영문 사유 문장을 파싱하고 있다")

    def test_두_덱에_문구가_다_있다(self):
        src = _read("copy.ts")
        for key in ("rebuilding", "degraded"):
            self.assertEqual(len(re.findall(rf"^\s*{key}:", src, re.M)), 2,
                             f"{key} 가 EN·KO 두 덱에 다 있지 않다")
        # 화면 텍스트에 em 대시 금지(D38) — 새로 더한 두 줄만 본다.
        for m in re.finditer(r"^\s*(?:rebuilding|degraded): (.+)$", src, re.M):
            self.assertNotIn("\u2014", m.group(1), m.group(1))
