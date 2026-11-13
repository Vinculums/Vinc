# -*- coding: utf-8 -*-
"""질의 임베딩의 콜드 재시도 상태 기계를 HTTP 없이 고정한다.

짧은 상한이 시간 초과일 때 긴 상한으로 한 번 더 가는 자격(`_claim_cold_retry`)은 조건이
셋이다: 웜 창 안에 성공이 없다, 직전 긴 시도가 건 쿨다운이 지났다, 긴 상한을 문 스레드가
지금 없다. 그 상태를 바꾸는 자리는 둘이다: 성공 기록(`_encode_query`)과 긴 시도 실패의
쿨다운(`_query_embedding`). 여기서는 창구(`urllib.request.urlopen`)와 단조시계를 가짜로
바꿔 각 전이를 소켓·잠 없이 결정론으로 돌린다. 상한 값은 읽기만 한다.

창구 대역으로 HTTP 서버를 띄우는 시나리오 시험은 `test_server.py` 의
`QueryEmbeddingColdStartTests` 에 있다.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import server as srv  # noqa: E402
from api.auth import SessionAuth  # noqa: E402
from api.server import VincApp  # noqa: E402

_URL = "http://encoder.invalid"


class _Encoder:
    """`urllib.request.urlopen` 자리에 들어가는 창구 대역.

    ``script`` 는 호출마다 하나씩 꺼내 쓰는 결과다: ``"ok"`` 는 벡터 한 개를 답하고,
    예외 인스턴스는 그대로 던진다. 각 호출이 받은 상한(`timeout`)을 ``timeouts`` 에 남긴다."""

    def __init__(self, *script):
        self.script = list(script)
        self.timeouts: list[float] = []
        self.lock = threading.Lock()

    def reply(self) -> bytes:
        # 런타임·빌드 대조(`_encode_call`)를 통과하는 답. 값은 사이드카가 지금 아는 것을 읽는다.
        return json.dumps({"vectors": [[1.0, 0.0]],
                           "encoder_runtime": srv._ENCODER_RUNTIME or "test-runtime",
                           "encoder_build": srv._ENCODER_BUILD}).encode("utf-8")

    def next_outcome(self, timeout):
        with self.lock:
            self.timeouts.append(timeout)
            return self.script.pop(0)

    def __call__(self, req, timeout=None):
        outcome = self.next_outcome(timeout)
        if isinstance(outcome, BaseException):
            raise outcome
        return io.BytesIO(self.reply())


def _timed_out():
    return TimeoutError("timed out")


class ColdRetryStateMachineTests(unittest.TestCase):

    def setUp(self):
        self.now = 1000.0
        env = mock.patch.dict(os.environ, {"VINC_EMBED_ENCODE": _URL})
        env.start()
        self.addCleanup(env.stop)
        # 재채점 스위치가 켜지면 ColBERT 코드북을 올리러 간다. 이 시험은 dense 경로만 본다.
        os.environ.pop("VINC_NODE_COLBERT", None)
        self.app = VincApp(store=None, auth=SessionAuth("cold-retry-test"))
        self.addCleanup(self.app.jobs.shutdown, drain=True)
        clock = mock.patch("time.monotonic", lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.short, self.cold = srv._QVEC_TIMEOUT, srv._QVEC_COLD_TIMEOUT
        self.assertLess(self.short, self.cold, "두 상한이 같으면 아래 단언이 가르지 못한다")

    def ask(self, encoder, text="양자역학"):
        """창구를 ``encoder`` 로 바꾸고 질의 하나를 임베딩한다. (결과, stderr) 를 돌려준다."""
        err = io.StringIO()
        with mock.patch("urllib.request.urlopen", encoder), contextlib.redirect_stderr(err):
            out = self.app._query_embedding(text)
        return out, err.getvalue()

    def test_a_first_timeout_earns_one_long_retry_and_logs_the_wait(self):
        enc = _Encoder(_timed_out(), "ok")
        (vec, note), err = self.ask(enc)
        self.assertEqual((vec, note), ([1.0, 0.0], ""))
        self.assertEqual(enc.timeouts, [self.short, self.cold])
        self.assertIn("콜드 재시도 성공", err, "성공한 콜드 재시도의 기다림이 로그에 안 남았다")

    def test_a_recent_success_reads_the_next_timeout_as_busy(self):
        enc = _Encoder("ok", _timed_out())
        self.assertEqual(self.ask(enc)[0], ([1.0, 0.0], ""))
        self.now += srv._QVEC_WARM_WINDOW - 1.0
        (vec, note), err = self.ask(enc)
        self.assertIsNone(vec)
        self.assertTrue(note.startswith("TimeoutError"), note)
        self.assertEqual(enc.timeouts, [self.short, self.short], "웜 창 안에서 긴 상한을 물었다")
        self.assertEqual(err, "")

    def test_the_warm_window_closes(self):
        self.assertEqual(self.ask(_Encoder("ok"))[0], ([1.0, 0.0], ""))
        self.now += srv._QVEC_WARM_WINDOW + 1.0
        enc = _Encoder(_timed_out(), "ok")
        self.assertEqual(self.ask(enc)[0], ([1.0, 0.0], ""))
        self.assertEqual(enc.timeouts, [self.short, self.cold])

    def test_a_failed_long_try_sets_a_cooldown_that_then_expires(self):
        enc = _Encoder(_timed_out(), _timed_out())
        vec, note = self.ask(enc)[0]
        self.assertIsNone(vec)
        self.assertTrue(note.startswith("TimeoutError"), note)
        self.assertEqual(enc.timeouts, [self.short, self.cold])

        self.now += srv._QVEC_COLD_COOLDOWN - 1.0
        enc = _Encoder(_timed_out())
        self.assertIsNone(self.ask(enc)[0][0])
        self.assertEqual(enc.timeouts, [self.short], "쿨다운 안에서 또 긴 상한을 물었다")

        self.now += 2.0
        enc = _Encoder(_timed_out(), "ok")
        self.assertEqual(self.ask(enc)[0], ([1.0, 0.0], ""))
        self.assertEqual(enc.timeouts, [self.short, self.cold], "쿨다운이 지났는데 자격이 안 돌아왔다")

    def test_the_claim_is_held_by_one_caller_until_the_long_try_ends(self):
        self.assertTrue(self.app._claim_cold_retry())
        self.assertFalse(self.app._claim_cold_retry(), "긴 상한을 문 호출이 있는데 또 자격을 줬다")

    def test_a_second_query_does_not_wait_while_the_first_holds_the_long_bound(self):
        entered, release = threading.Event(), threading.Event()
        cold = self.cold

        class _Blocking(_Encoder):
            def __call__(self, req, timeout=None):
                if timeout == cold:
                    self.next_outcome(timeout)
                    entered.set()
                    release.wait(5)
                    return io.BytesIO(self.reply())
                return super().__call__(req, timeout)

        enc = _Blocking(_timed_out(), "ok", _timed_out())
        first: list = []
        with mock.patch("urllib.request.urlopen", enc), \
                contextlib.redirect_stderr(io.StringIO()):
            t = threading.Thread(target=lambda: first.append(self.app._query_embedding("q1")))
            t.start()
            self.assertTrue(entered.wait(5), "첫 질의가 긴 상한에 들어가지 않았다")
            vec, note = self.app._query_embedding("q2")
            release.set()
            t.join(5)
        self.assertIsNone(vec, "긴 상한을 문 스레드가 있는데 두 번째 질의도 기다렸다")
        self.assertTrue(note.startswith("TimeoutError"), note)
        self.assertEqual(first, [([1.0, 0.0], "")])
        self.assertEqual(enc.timeouts, [self.short, cold, self.short])
        # 첫 질의의 성공이 웜 창을 열었으니 창을 지나서 본다. 남은 거절 사유는 '문 스레드' 표식뿐이다.
        self.now += srv._QVEC_WARM_WINDOW + 1.0
        self.assertTrue(self.app._claim_cold_retry(), "긴 시도가 끝났는데 자격이 안 풀렸다")

    def test_only_a_timeout_earns_the_long_retry(self):
        refused = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        enc = _Encoder(refused)
        vec, note = self.ask(enc)[0]
        self.assertIsNone(vec)
        self.assertTrue(note.startswith("URLError"), note)
        self.assertEqual(enc.timeouts, [self.short], "거절은 빨리 오는 답인데 긴 상한으로 또 갔다")

        connect_timeout = urllib.error.URLError(TimeoutError("timed out"))
        enc = _Encoder(connect_timeout, "ok")
        self.assertEqual(self.ask(enc)[0], ([1.0, 0.0], ""))
        self.assertEqual(enc.timeouts, [self.short, self.cold],
                         "연결 단계의 시간 초과도 콜드 재시도 자격이다")


if __name__ == "__main__":
    unittest.main()
