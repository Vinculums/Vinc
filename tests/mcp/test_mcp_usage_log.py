# -*- coding: utf-8 -*-
"""허브 액세스 로그가 **누가 불렀는지**를 남기는가 (계정별 사용량 귀속).

왜 이 테스트가 있나. 2026-09-02 실측: 7일치 허브 http 로그 612줄이 **전부**
400·401·404·405·502 였고 2xx 는 한 줄도 없었다. 성공한 요청이 실사용의 전부인데
그것이 로그에 없으니, "이 계정이 얼마나 쓰고 있나"에 답할 수 있는 사람이 아무도
없었다 — 쿼터 카운터는 현재 창만 들고 아무도 읽지 않는다.
근거: [[record:per-account-usage-exists-only-as-the-quota-counter-and-nothing-reads-it]]

여기서 지키는 것 셋. 전부 조용히 깨지는 종류다:
  ① 인증된 성공은 owner 태그와 함께 남는다 (안 남기면 귀속이 다시 0 이 된다)
  ② 익명 성공은 여전히 안 남는다 (/health·discovery 가 하루 1,728회다 — 프로브 4종
     × 3지역 × 5분. 남기면 로그가 프로브 로그가 되고 태그는 신호를 잃는다)
  ③ 태그는 요청 하나를 넘기지 않는다 (keep-alive 가 한 연결로 여러 요청을 처리하므로,
     안 비우면 인증 없는 다음 요청이 앞 요청의 owner 를 달고 남는다 — 그 줄은
     그럴듯해 보이므로 잘못된 집계가 조용히 만들어진다)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import unittest                                      # noqa: E402

from vinc_mcp import http_server as hs               # noqa: E402


class _Handler:
    """`log_request`/`log_message` 만 떼어 쓴다 — 소켓 없이 분기만 본다."""

    log_request = hs._Handler.log_request
    requestline = 'POST /mcp HTTP/1.1'

    def __init__(self, tag=None):
        self._owner_tag = tag
        self.lines: list[str] = []

    def log_message(self, fmt, *args):
        self.lines.append(fmt % args)


class OwnerTagTests(unittest.TestCase):
    def test_같은_owner_는_같은_태그를_준다(self):
        a = hs._owner_tag("user:019fae48-cc62-7984-b2b0-1698f4de55d4")
        self.assertEqual(a, hs._owner_tag("user:019fae48-cc62-7984-b2b0-1698f4de55d4"))
        self.assertEqual(len(a), 12)
        # 원문이 로그로 새면 로그 한 벌이 계정 명부가 된다.
        self.assertNotIn("019fae48", a)
        self.assertIsNone(hs._owner_tag(None))
        self.assertIsNone(hs._owner_tag(""))


class UsageLogTests(unittest.TestCase):
    def test_인증된_성공은_owner_와_함께_남는다(self):
        h = _Handler(tag="abc123def456")
        h.log_request(200, "-")
        self.assertEqual(len(h.lines), 1, "성공이 안 남으면 귀속이 다시 0 이다")
        self.assertIn("owner=abc123def456", h.lines[0])
        self.assertIn("200", h.lines[0])

    def test_익명_성공은_안_남는다(self):
        h = _Handler(tag=None)
        h.log_request(200, "-")
        self.assertEqual(h.lines, [], "/health 가 하루 1,728회다 — 남기면 로그가 프로브 로그다")

    def test_실패는_owner_를_몰라도_남는다(self):
        h = _Handler(tag=None)
        h.log_request(401, "-")
        self.assertEqual(len(h.lines), 1)
        self.assertNotIn("owner=", h.lines[0])

    def test_태그는_요청_하나를_넘기지_않는다(self):
        h = _Handler(tag="abc123def456")
        h.log_request(200, "-")            # 인증된 요청
        h.log_request(200, "-")            # 같은 연결의 다음 요청, 인증 없음
        self.assertEqual(len(h.lines), 1,
                         "앞 요청의 owner 가 남으면 그럴듯한 거짓 집계가 만들어진다")

    def test_쿼리스트링은_잘라낸다(self):
        h = _Handler(tag="abc123def456")
        h.requestline = "GET /authorize?code=SECRET&state=x HTTP/1.1"
        h.log_request(200, "-")
        self.assertNotIn("SECRET", h.lines[0])


if __name__ == "__main__":
    unittest.main()
