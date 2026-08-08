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
import os
import pathlib
import sys
import unittest


def _np_or_skip(case):
    """numpy 가 필요한 검사에만 붙인다. 이 파일 전체는 임베딩 스택 없이도 돌아야 하고
    (모듈 docstring), 그래서 skip 은 **이 한 함수에만** 국한한다 — 파일 단위 skip 은
    이 리포의 상습 함정이다."""
    try:
        import numpy as np
    except Exception:
        case.skipTest("numpy 없음 — 풀링 수치 검사만 건너뛴다")
    return np

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
        self._real_model = self.mod.MODEL

    def tearDown(self):
        self.mod.MODEL = self._real_model

    def _run(self, sidecar, encode=None, argv=("--once",), model=None):
        """워커 1회전. ``model`` 을 주면 그 계열로 고정한다.

        ⚠ 모델을 명시하지 않으면 **기본값에 묶인다**. 기본이 bge 에서 e5 로 바뀌자
        접두가 붙어(`"passage: "`) 더미 인코더의 길이 기반 단언이 통째로 깨졌다 —
        암묵적 의존이 드러난 것이다. 접두 규약이 결과에 영향을 주는 검사는 모델을
        명시할 것."""
        self.mod._call = sidecar
        if model is not None:
            self.mod.MODEL = model
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
        # bge 로 고정 — 이 검사는 doc 묶음이지 접두가 아니다(e5 는 별도 테스트).
        self.assertEqual(self._run(sidecar, model="BAAI/bge-base-en-v1.5"), 0)
        self.assertEqual(len(sidecar.applied), 2, "doc 두 개인데 apply 가 2회가 아니다")
        by_doc = {a["doc_id"]: a["embeddings"] for a in sidecar.applied}
        self.assertEqual(set(by_doc["d1"]), {"c1", "c2"})
        self.assertEqual(set(by_doc["d2"]), {"c3"})
        # v8 — 값은 **벡터 목록**이다(청크 하나에 조각 여럿). 짧은 텍스트는 조각이
        # 하나뿐이라 목록 길이 1. chunk_id ↔ 벡터 짝이 어긋나면 검색이 조용히 엉키므로
        # 더미 인코더가 넣은 길이값으로 대응을 확인한다.
        self.assertEqual(by_doc["d1"]["c1"], [[2.0, 0.0]])   # "aa"
        self.assertEqual(by_doc["d1"]["c2"], [[3.0, 0.0]])   # "bbb"
        self.assertEqual(by_doc["d2"]["c3"], [[1.0, 0.0]])   # "c"

    def test_긴_청크는_조각_여럿으로_임베딩된다(self):
        """v8 부모-자식 — 청크는 정본이라 안 자르고, **벡터만** 쪼갠다.

        청크는 2000자 단위인데 모델 입력 상한은 512토큰이라 한 벡터로는 앞부분만
        담긴다(실측 유실: 한국어 42.6% · 영어 12.3%). 조각을 내지 않으면 그 손실이
        그대로 남는다. 이 테스트는 조각내기가 사라지면 깨진다."""
        long_text = "문단 하나. " * 400 + "\n\n" + "문단 둘. " * 400
        sidecar = _FakeSidecar([[{"doc_id": "d1", "chunk_id": "c1", "text": long_text}]])
        self.assertEqual(self._run(sidecar), 0)
        vecs = sidecar.applied[0]["embeddings"]["c1"]
        self.assertGreater(len(vecs), 1,
                           f"긴 청크가 조각나지 않았다(벡터 {len(vecs)}개)")
        self.assertTrue(all(isinstance(v, list) for v in vecs))

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

        # ⚠ `self.mod.time` 은 이 모듈의 지역 별칭이 아니라 **전역 `time` 모듈**이다.
        # 여기서 sleep 을 바꾸면 프로세스 전체가 바뀌고, 복원하지 않으면 **이후 모든
        # 테스트**의 time.sleep 이 _Stop 을 던진다(rounds 가 이미 5 를 넘겨 즉시 발동).
        # 실제로 그렇게 새 나가 다른 파일의 동시성 테스트를 깨뜨렸다(2026-07-31).
        # sleep 을 쓰는 테스트가 드물어 오래 잠복했다 — 반드시 되돌린다.
        real_sleep = self.mod.time.sleep
        self.mod.time.sleep = fake_sleep
        old = os.environ.get("VINC_EMBED_HEARTBEAT_ROUNDS")
        os.environ["VINC_EMBED_HEARTBEAT_ROUNDS"] = "2"
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), self.assertRaises(_Stop):
                self._run(_FakeSidecar([]), argv=("--interval", "0"))
        finally:
            self.mod.time.sleep = real_sleep
            if old is None:
                os.environ.pop("VINC_EMBED_HEARTBEAT_ROUNDS", None)
            else:
                os.environ["VINC_EMBED_HEARTBEAT_ROUNDS"] = old

        out = buf.getvalue()
        self.assertIn("loopback ok", out, "첫 조회 성공을 안 찍으면 기동 증명이 없다")
        self.assertIn("alive", out, "idle 하트비트가 없으면 침묵이 진단 불가다")

    def test_세션에_들어가는_배치에_상한이_있어야_한다(self):
        """청크 수가 아무리 많아도 **한 번에** 세션에 들어가면 안 된다.

        예전엔 한 문서의 전 청크가 단일 ONNX run 이었고 상한이 없었다. 출력 텐서만
        `배치 × 512 × 768 × fp32` 라 1500청크 문서(웹 상한 4MB)면 2.25GB 로 컨테이너
        한도(1024MiB)를 넘긴다. 워커·사이드카·MCP 가 한 컨테이너라 그 OOM 은
        **MCP 를 같이 죽인다** — ONNX 를 고른 이유가 바로 그거였는데 배치 축이 열려
        있었다. 이 테스트는 그 상한이 사라지면 깨진다.

        ⚠ 여기서 실제 세션을 흉내 내지 않는다(numpy 가 필요해진다). 이 파일은
        임베딩 스택이 없는 CI 에서도 **skip 되지 않아야** 한다 — 조용한 skip 이 이
        리포의 상습 함정이라 모듈 docstring 이 명시적으로 금지한다. 그래서 검사 대상은
        수치가 아니라 **분할 경계**다: `encode` 가 `_encode_batch` 를 몇 개씩 부르는가.
        """
        seen: list[int] = []
        emb = self.mod._Embedder()
        emb._encode_batch = lambda texts: (seen.append(len(texts))
                                           or [[0.0, 1.0] for _ in texts])

        n_chunks = self.mod.EMBED_BATCH * 3 + 1
        vecs = emb.encode([f"청크 {i}" for i in range(n_chunks)])

        self.assertEqual(len(vecs), n_chunks, "청크 수와 벡터 수가 다르다 — 대응이 깨졌다")
        self.assertTrue(seen, "세션이 한 번도 실행되지 않았다 — 테스트가 낡았다")
        self.assertLessEqual(max(seen), self.mod.EMBED_BATCH,
                             f"배치 상한을 넘겨 세션에 넣었다: {max(seen)} > {self.mod.EMBED_BATCH}")
        self.assertEqual(sum(seen), n_chunks, "일부 청크가 누락되거나 중복 처리됐다")

    def test_인코딩_창구가_쿼리에만_접두를_붙인다(self):
        """`serve_encode` 계약 — 사이드카가 이 창구로 쿼리 임베딩을 얻어 간다.

        임베딩 모델을 든 프로세스는 이 워커뿐이라(사이드카엔 torch 가 없다) 여기가
        막히면 벡터 검색이 통째로 죽는다. 특히 **접두 규약**이 급소다: bge 는 쿼리에만
        지시 접두를 붙이고 passage 는 맨몸이다. passage 에도 붙이면 이미 저장된 벡터와
        공간이 어긋나 **에러 없이 검색만 나빠진다**.
        """
        import json
        import urllib.request

        got: list[str] = []
        self.mod.MODEL = "BAAI/bge-base-en-v1.5"   # 이 검사는 bge 규약이다(e5 는 별도)
        emb = self.mod._Embedder()
        emb.encode = lambda texts: (got.extend(texts) or [[0.0, 1.0] for _ in texts])

        port = 0
        # 실제 서버를 띄운다 — 계약(경로·본문 형태)까지 같이 검사하려면 대역이 아니라
        # 진짜 핸들러를 통과시켜야 한다.
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        self.mod.serve_encode(emb, port)

        def call(kind):
            body = json.dumps({"texts": ["양자역학"], "kind": kind}).encode()
            req = urllib.request.Request(f"http://127.0.0.1:{port}/encode", data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.load(r)

        out = call("query")
        self.assertEqual(out["vectors"], [[0.0, 1.0]], "벡터 형태가 계약과 다르다")
        self.assertTrue(got and got[0].startswith(self.mod.QUERY_PREFIX),
                        f"쿼리에 접두가 안 붙었다: {got!r}")

        got.clear()
        call("passage")
        self.assertEqual(got, ["양자역학"],
                         "passage 에 접두가 붙었다 — 저장 벡터와 공간이 어긋난다")

    def test_다국어_모델이면_저장_쪽에도_접두가_붙는다(self):
        """모델 계열이 바뀌면 접두 규약도 바뀐다 — 그리고 그 누락은 **에러를 안 낸다**.

        bge 는 쿼리에만 지시 접두를 붙이고 passage 는 맨몸이다. e5 는 모델카드가
        `"query: "` / `"passage: "` **양쪽 모두** 필수라고 못박는다(빠뜨리면 성능 저하).
        그래서 `VINC_EMBED_MODEL` 을 e5 로 바꾸는 순간 저장 벡터가 전부 규약 위반이
        되는데, 검색은 그냥 조금 나빠질 뿐 아무 신호도 없다.

        배경 임베딩 루프가 `_prefix` 를 우회하던 것이 실제로 그 지뢰였다(2026-08-01
        수정). 이 테스트는 우회가 되돌아오거나 e5 분기가 passage 를 빠뜨리면 깨진다.
        """
        real = self.mod.MODEL
        try:
            self.mod.MODEL = "intfloat/multilingual-e5-base"
            self.assertEqual(self.mod._prefix("본문", "passage"), "passage: 본문")
            self.assertEqual(self.mod._prefix("질의", "query"), "query: 질의")
            self.mod.MODEL = "BAAI/bge-base-en-v1.5"
            self.assertEqual(self.mod._prefix("본문", "passage"), "본문",
                             "bge passage 에 접두가 붙었다 — 저장 벡터 공간이 어긋난다")
            self.assertTrue(self.mod._prefix("질의", "query").startswith(self.mod.QUERY_PREFIX))
        finally:
            self.mod.MODEL = real

    def test_배경_임베딩이_접두_경로를_우회하지_않는다(self):
        """저장 벡터를 만드는 실제 루프가 `_prefix` 를 **거치는지** 본다.

        규약을 한 곳에 모아 놓아도 호출부가 우회하면 소용없다. 여기가 그 계약이다."""
        seen: list[str] = []

        def spy(texts):                       # `_run` 이 _Embedder.encode 로 꽂는다
            seen.extend(texts)
            return [[0.0, 1.0] for _ in texts]

        real = self.mod.MODEL
        try:
            self.mod.MODEL = "intfloat/multilingual-e5-base"
            sidecar = _FakeSidecar([[{"doc_id": "d1", "chunk_id": "c1", "text": "본문"}]])
            self._run(sidecar, encode=spy)
        finally:
            self.mod.MODEL = real
        self.assertTrue(seen, "인코딩이 호출되지 않았다 — 테스트가 낡았다")
        self.assertTrue(all(t.startswith("passage: ") for t in seen),
                        f"배경 루프가 접두를 건너뛰었다: {seen!r}")

    def test_풀링이_모델_계열을_따른다(self):
        """bge 는 CLS, e5 는 mean — **틀려도 에러가 안 난다**.

        각 모델 리포의 `1_Pooling/config.json` 이 정본이다(e5 는 cls=false·mean=true).
        같은 입력에 두 풀링의 코사인은 0.87~0.91 로, ONNX 전환 때 통과 기준이 ≥0.9999
        였던 걸 생각하면 완전히 다른 벡터다. 모델만 갈아끼우고 풀링을 그대로 두면
        저장 벡터 전량이 조용히 어긋난다."""
        np = _np_or_skip(self)
        hidden = np.array([[[1.0, 0.0], [3.0, 0.0], [9.0, 9.0]]])   # (1, 3, 2)
        mask = np.array([[1, 1, 0]])                                # 세 번째는 패딩

        real = self.mod.MODEL
        try:
            self.mod.MODEL = "BAAI/bge-base-en-v1.5"
            got = self.mod._pool(hidden, mask, np)
            self.assertEqual(got.tolist(), [[1.0, 0.0]], "bge 가 CLS 를 안 썼다")

            self.mod.MODEL = "intfloat/multilingual-e5-base"
            got = self.mod._pool(hidden, mask, np)
            # 패딩(9,9)이 빠진 (1+3)/2 = 2 여야 한다. 안 빠지면 (1+3+9)/3 = 4.33.
            self.assertEqual(got.tolist(), [[2.0, 0.0]],
                             "e5 가 mean 이 아니거나 패딩을 평균에 넣었다")
        finally:
            self.mod.MODEL = real

    def test_e5_는_공식_int8_을_쓰지_않는다(self):
        """공식 `model_qint8_avx512_vnni.onnx` 는 non-VNNI 에서 품질을 깎는다.

        U8S8+per_channel+reduce_range=False 조합이라 ORT 문서가 경고하는 포화 조건에
        정확히 해당하고, 실측으로 한국어 nDCG 가 0.670 → 0.472 로 떨어졌다. 같은 fp32 를
        reduce_range=True 로 직접 양자화하면 0.669 로 돌아온다. 그래서 공식 int8 파일은
        **받지 않는다** — 이 테스트는 누가 그걸 되돌리면 깨진다."""
        asked: list[str] = []

        def fake_dl(repo, path, **kw):
            asked.append(path)
            return f"/tmp/{path}"

        import types
        stub = types.ModuleType("huggingface_hub")
        stub.hf_hub_download = fake_dl
        old = sys.modules.get("huggingface_hub")
        sys.modules["huggingface_hub"] = stub
        real_env = os.environ.get("VINC_EMBED_QUANTIZE")
        os.environ["VINC_EMBED_QUANTIZE"] = "off"      # 양자화는 건너뛰고 경로 선택만 본다
        try:
            p = self.mod._resolve_model("intfloat/multilingual-e5-base", "/tmp/cache")
        finally:
            if old is None:
                sys.modules.pop("huggingface_hub", None)
            else:
                sys.modules["huggingface_hub"] = old
            if real_env is None:
                os.environ.pop("VINC_EMBED_QUANTIZE", None)
            else:
                os.environ["VINC_EMBED_QUANTIZE"] = real_env
        self.assertEqual(asked, ["onnx/model.onnx"],
                         f"공식 int8 을 받으려 했다: {asked}")
        self.assertTrue(p.endswith("model.onnx"))

    def test_구워진_모델이_있으면_그걸_쓴다__런타임_양자화_금지(self):
        """🔴 런타임 양자화는 **못 한다**(1024MiB 컨테이너에서 OOM).

        e5 의 임베딩 행렬 하나가 192M float(732MB)이고 원본 모델까지 메모리에 올라가
        피크가 1.5GB 를 넘는다 — 실측으로 컨테이너보다 넉넉한 기기에서도 죽었다.
        그래서 `Dockerfile.hub` 가 빌드 타임에 굽고 `VINC_EMBED_MODEL_PATH` 로 알린다.
        이 테스트는 그 경로를 무시하고 런타임에 받으려 들면 깨진다."""
        import tempfile
        import types
        called: list[str] = []
        stub = types.ModuleType("huggingface_hub")
        stub.hf_hub_download = lambda repo, path, **kw: called.append(path) or "/tmp/x"
        old = sys.modules.get("huggingface_hub")
        sys.modules["huggingface_hub"] = stub
        real = os.environ.get("VINC_EMBED_MODEL_PATH")
        with tempfile.NamedTemporaryFile(suffix=".onnx", delete=False) as f:
            baked = f.name
        os.environ["VINC_EMBED_MODEL_PATH"] = baked
        try:
            p = self.mod._resolve_model("intfloat/multilingual-e5-base", "/tmp/c")
        finally:
            if old is None:
                sys.modules.pop("huggingface_hub", None)
            else:
                sys.modules["huggingface_hub"] = old
            if real is None:
                os.environ.pop("VINC_EMBED_MODEL_PATH", None)
            else:
                os.environ["VINC_EMBED_MODEL_PATH"] = real
            os.unlink(baked)
        self.assertEqual(p, baked, "구워진 모델을 안 쓰고 다른 걸 골랐다")
        self.assertEqual(called, [], f"런타임에 모델을 받으려 했다: {called}")

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
        self.assertEqual(self._run(sidecar, encode=encode,
                                   model="BAAI/bge-base-en-v1.5"), 0)
        self.assertEqual([a["doc_id"] for a in sidecar.applied], ["good"])


class SplitEncoderTest(unittest.TestCase):
    """모델을 별도 컨테이너로 분리한 뒤의 계약 (2026-08-07).

    허브(1Gi)에서 ONNX 적재가 rss 646MB 에 닿아 커널이 워커를 **SIGKILL(137)** 했다.
    모델을 `vinc-encode`(2Gi · minReplicas=0)로 옮기고 허브에는 모델을 안 드는 얇은
    클라이언트만 남긴다. 그 분리가 만드는 새 실패 모드를 여기서 못 박는다 —
    **셋 다 에러를 안 내고 벡터만 틀리거나 표면을 열어 두는 종류다.**
    """

    def setUp(self):
        self.mod = _load_module()
        self._saved = {k: getattr(self.mod, k) for k in
                       ("MODEL", "REMOTE_ENCODE", "ENCODE_TOKEN", "ENCODE_BIND")}

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(self.mod, k, v)

    def _serve(self, encode):
        """진짜 핸들러를 통과시킨다 — 대역으로는 본문 계약이 안 잡힌다.

        ⚠ 포트를 미리 찾아 두지 않는다(bind→close→재bind 는 그 틈에 다른 프로세스가
        채 갈 수 있어 **산발 실패**를 만든다 — 실제로 한 번 물렸다). 0 을 주고 OS 가
        고른 포트를 돌려받는다."""
        emb = self.mod._Embedder()
        emb.encode = encode
        port = self.mod.serve_encode(emb, 0)
        return f"http://127.0.0.1:{port}"

    def test_원격_인코더가_접두를_두_번_붙이지_않는다(self):
        """급소. 배치 루프는 `_prefix` 를 **이미** 거친 문자열을 넘긴다(main).
        서버가 `kind` 를 보고 또 붙이면 `passage: passage: …` 가 되는데, 접두 유무의
        코사인이 0.95~0.97 이라 **에러 없이 검색만 나빠진다**. 그래서 클라이언트는
        `raw:true` 로 보내고 서버는 그 경우 손대지 않는다."""
        seen: list[str] = []
        base = self._serve(lambda texts: (seen.extend(texts)
                                          or [[float(len(t))] for t in texts]))
        self.mod.MODEL = "intfloat/multilingual-e5-base"      # 양쪽 접두 규약
        enc = self.mod._RemoteEncoder(base)
        out = enc.encode(["passage: 이미 붙었다"])
        self.assertEqual(seen, ["passage: 이미 붙었다"],
                         f"서버가 접두를 덧붙였다: {seen!r}")
        self.assertEqual(out, [[float(len("passage: 이미 붙었다"))]])

    def test_raw_가_없으면_서버가_접두를_붙인다(self):
        """`raw` 는 옵트인이다 — 사이드카의 질의 경로는 예전 계약 그대로 온다."""
        seen: list[str] = []
        base = self._serve(lambda texts: (seen.extend(texts) or [[1.0] for _ in texts]))
        self.mod.MODEL = "intfloat/multilingual-e5-base"
        import json
        import urllib.request
        body = json.dumps({"texts": ["질문"], "kind": "query"}).encode()
        req = urllib.request.Request(base + "/encode", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5):
            pass
        self.assertEqual(seen, ["query: 질문"], f"접두가 안 붙었다: {seen!r}")

    def test_벡터_개수가_어긋나면_던진다(self):
        """조각↔벡터 정렬이 이 파이프라인의 유일한 암묵 계약이다. 개수가 모자라면
        `zip` 이 **조용히 잘라** 일부 청크가 벡터 없이 성공 처리된다(needs_embed 가
        지워져 다시 잡히지도 않는다)."""
        base = self._serve(lambda texts: [[1.0]])            # 둘 받고 하나만 준다
        enc = self.mod._RemoteEncoder(base)
        with self.assertRaises(RuntimeError) as ctx:
            enc.encode(["a", "b"])
        self.assertIn("어긋난다", str(ctx.exception))

    def test_원격_모드에서는_모델을_올리지_않는다(self):
        """분리의 목적 자체 — 허브 프로세스가 ONNX 를 만지면 OOM 이 그대로 돌아온다."""
        loaded = []
        self.mod._Embedder._load = lambda _self: loaded.append(1)
        base = self._serve(lambda texts: [[float(len(t))] for t in texts])
        self.mod.REMOTE_ENCODE = base
        sidecar = _FakeSidecar([[{"doc_id": "d1", "chunk_id": "c1", "text": "본문"}]])
        self.mod._call = sidecar
        old = sys.argv
        sys.argv = ["embed_worker.py", "--once"]
        try:
            self.assertEqual(self.mod.main(), 0)
        finally:
            sys.argv = old
        self.assertEqual([a["doc_id"] for a in sidecar.applied], ["d1"])
        self.assertEqual(loaded, [], "원격 모드인데 로컬 모델을 적재했다")

    def test_루프백_밖인데_토큰이_없으면_기동을_거부한다(self):
        """인증 없는 인코더를 **기동 시점에** 막는다. 요청이 올 때 막으면 그 사이에
        이미 열려 있었다."""
        self.mod.ENCODE_BIND = "0.0.0.0"
        self.mod.ENCODE_TOKEN = ""
        old = sys.argv
        sys.argv = ["embed_worker.py", "--serve-only", "--encode-port", "0"]
        try:
            self.assertEqual(self.mod.main(), 2)
        finally:
            sys.argv = old

    def test_서버가_자기_자신에게_위임하지_않는다(self):
        """`--serve-only` + `VINC_EMBED_REMOTE` 는 인코더가 자기를 부르는 고리다."""
        self.mod.REMOTE_ENCODE = "http://elsewhere"
        old = sys.argv
        sys.argv = ["embed_worker.py", "--serve-only", "--encode-port", "9"]
        try:
            self.assertEqual(self.mod.main(), 2)
        finally:
            sys.argv = old

    def test_토큰이_있으면_인증을_요구한다(self):
        import json
        import urllib.error
        import urllib.request
        self.mod.ENCODE_TOKEN = "s3cret"
        base = self._serve(lambda texts: [[1.0] for _ in texts])
        body = json.dumps({"texts": ["x"]}).encode()
        req = urllib.request.Request(base + "/encode", data=body,
                                     headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 401)
        # 토큰을 실으면 통과한다 — 그리고 클라이언트가 그걸 자동으로 싣는다.
        self.assertEqual(self.mod._RemoteEncoder(base).encode(["x"]), [[1.0]])

    def test_health_는_모델을_올리지_않는다(self):
        """프로브가 모델 적재를 유발하면 minReplicas=0 의 의미가 사라진다 —
        깨어날 때마다 646MB 를 올리고 프로브 타임아웃에도 걸린다."""
        import json
        import urllib.request
        loaded = []
        self.mod._Embedder._load = lambda _self: loaded.append(1)
        base = self._serve(lambda texts: [[1.0] for _ in texts])
        with urllib.request.urlopen(base + "/health", timeout=5) as r:
            out = json.load(r)
        self.assertEqual(out["status"], "ok")
        self.assertFalse(out["loaded"])
        self.assertEqual(loaded, [], "/health 가 모델을 적재했다")


if __name__ == "__main__":
    unittest.main()
