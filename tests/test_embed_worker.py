# -*- coding: utf-8 -*-
"""허브 임베딩 워커의 **오케스트레이션** 검증 — packaging/embed_worker.py.

왜 여기까지 테스트하나: 이 워커는 Azure 이관 때 **통째로 사라졌는데도 아무도 몰랐다**
(에러 없이 벡터만 안 채워짐). 되살릴 때 최소한 "돌긴 도는지"가 아니라 "어떻게
틀어지는지"를 못 박아 둔다. 임베딩 수치 자체는 여기서 안 본다 — 그건 배포 전에
폐기 컨테이너에서 sentence-transformers 와 코사인 대조로 검증했다(전 케이스
≥0.9999, 커밋 메시지에 재현 절차).

onnxruntime·numpy 없이 돈다: 워커의 모듈 레벨 import 는 stdlib 뿐이고 무거운
의존은 `_Embedder._load()` 안에 있다. 그래서 CI 가 임베딩 스택을 안 깔아도
이 파일은 **skip 되지 않는다**(조용한 skip 이 이 리포의 상습 함정이다).
"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

_SRC = pathlib.Path(__file__).resolve().parents[1] / "packaging" / "embed_worker.py"


def _load_module():
    """패키지가 아닌 스크립트를 경로로 적재한다(packaging/ 은 import 대상이 아니다)."""
    spec = importlib.util.spec_from_file_location("_vinc_embed_worker", _SRC)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class _FakeSidecar:
    """`_call` 대역. pending 을 큐에서 하나씩 내주고 apply 를 기록한다."""

    def __init__(self, rounds: list[list[dict]], fail_pending: bool = False):
        self.rounds = list(rounds)
        self.applied: list[dict] = []
        self.fail_pending = fail_pending

    def __call__(self, path: str, body: dict | None = None):
        if body is None:                                  # GET /embed/pending
            if self.fail_pending:
                raise OSError("사이드카 기동 전")
            chunks = self.rounds.pop(0) if self.rounds else []
            return {"kind": "embed.pending", "chunks": chunks}
        self.applied.append(body)                         # POST /embed/apply
        return {"embedded": len(body.get("embeddings") or {}), "cleared": True}


class EmbedWorkerOrchestrationTest(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()

    def _run(self, sidecar, encode=None, argv=("--once",)):
        self.mod._call = sidecar
        if encode is None:
            def encode(texts):                            # 결정론 더미 벡터
                return [[float(len(t)), 0.0] for t in texts]
        self.mod._Embedder.encode = lambda _self, texts: encode(texts)
        import sys
        old = sys.argv
        sys.argv = ["embed_worker.py", *argv]
        try:
            return self.mod.main()
        finally:
            sys.argv = old

    def test_pending_이_비면_모델을_올리지_않고_종료한다(self):
        """idle 라운드가 싸야 5분 간격이 성립한다 — 지연 로드가 깨지면 매 라운드
        440MB 모델을 올려 0.5 vCPU 를 배경 작업이 다 먹는다."""
        loaded = []
        self.mod._Embedder._load = lambda _self: loaded.append(1)
        sidecar = _FakeSidecar([[]])
        self.assertEqual(self._run(sidecar), 0)
        self.assertEqual(loaded, [], "일감이 없는데 모델을 올렸다")
        self.assertEqual(sidecar.applied, [])

    def test_문서_단위로_묶어_apply_한다(self):
        """apply 는 doc 단위여야 needs_embed 가 깔끔히 내려간다(청크별로 쪼개면
        큰 문서가 영영 안 끝난다 — docstore.pending_embed_chunks 주석의 계약)."""
        sidecar = _FakeSidecar([[
            {"doc_id": "d1", "chunk_id": "c1", "text": "aa"},
            {"doc_id": "d1", "chunk_id": "c2", "text": "bbb"},
            {"doc_id": "d2", "chunk_id": "c3", "text": "c"},
        ]])
        self.assertEqual(self._run(sidecar), 0)
        self.assertEqual(len(sidecar.applied), 2, "doc 두 개인데 apply 가 2회가 아니다")
        by_doc = {a["doc_id"]: a["embeddings"] for a in sidecar.applied}
        self.assertEqual(set(by_doc["d1"]), {"c1", "c2"})
        self.assertEqual(set(by_doc["d2"]), {"c3"})
        # chunk_id ↔ 벡터 짝이 어긋나면 검색이 조용히 엉킨다 — 길이로 확인.
        self.assertEqual(by_doc["d1"]["c1"][0], 2.0)
        self.assertEqual(by_doc["d1"]["c2"][0], 3.0)

    def test_pending_조회_실패는_예외를_밖으로_던지지_않는다(self):
        """사이드카 재시작 중에 워커가 죽으면 안 된다 — 엔트리포인트가 워커
        사망으로 컨테이너를 내리지는 않지만, 그래도 스스로 물러났다 와야 한다."""
        sidecar = _FakeSidecar([], fail_pending=True)
        self.assertEqual(self._run(sidecar), 1)           # --once 면 1 로 종료
        self.assertEqual(sidecar.applied, [])

    def test_침묵이_진단_가능해야_한다__생존_신호(self):
        """이 워커는 idle 이면 아무것도 안 찍는다 — 그래서 **죽음과 정상 대기가
        구분되지 않는다.** 원래 워커가 이관 때 사라졌는데 아무도 몰랐던 이유가 정확히
        이것이다. 첫 조회 성공은 무조건 찍고, 그 뒤 idle 은 N 라운드마다 한 줄 남긴다."""
        import contextlib
        import io
        import os

        class _Stop(Exception):
            pass

        rounds = {"n": 0}

        def fake_sleep(_s):
            rounds["n"] += 1
            if rounds["n"] >= 5:
                raise _Stop()

        self.mod.time.sleep = fake_sleep
        old = os.environ.get("VINC_EMBED_HEARTBEAT_ROUNDS")
        os.environ["VINC_EMBED_HEARTBEAT_ROUNDS"] = "2"
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), self.assertRaises(_Stop):
                self._run(_FakeSidecar([]), argv=("--interval", "0"))
        finally:
            if old is None:
                os.environ.pop("VINC_EMBED_HEARTBEAT_ROUNDS", None)
            else:
                os.environ["VINC_EMBED_HEARTBEAT_ROUNDS"] = old

        out = buf.getvalue()
        self.assertIn("loopback ok", out, "첫 조회 성공을 안 찍으면 기동 증명이 없다")
        self.assertIn("alive", out, "idle 하트비트가 없으면 침묵이 진단 불가다")

    def test_한_문서가_막혀도_나머지는_진행한다(self):
        """한 문서의 인코딩 실패가 라운드 전체를 버리면, 그 문서가 pending 선두에
        있는 동안 뒤의 전부가 영구히 굶는다(head-of-line blocking)."""
        def encode(texts):
            if any(t == "boom" for t in texts):
                raise RuntimeError("인코딩 실패")
            return [[1.0] for _ in texts]

        sidecar = _FakeSidecar([[
            {"doc_id": "bad", "chunk_id": "b1", "text": "boom"},
            {"doc_id": "good", "chunk_id": "g1", "text": "ok"},
        ]])
        self.assertEqual(self._run(sidecar, encode=encode), 0)
        self.assertEqual([a["doc_id"] for a in sidecar.applied], ["good"])


if __name__ == "__main__":
    unittest.main()
