#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ONNX 문장 임베더 — **모델 계열별 규약**을 한자리에 담는다(모델 비교용).

`vinc_rag/embed/worker.py` 의 임베더는 프로덕션용이라 모델 하나를 상주시키지만,
모델 비교(bge vs e5, fp32 vs int8)는 **두 모델을 같은 코드로** 재야 한다. 그래서 계열을
인자로 받는 판을 따로 둔다.

🔴 **규약 셋 중 하나만 어긋나도 에러 없이 벡터가 틀린다**([[vinc.plan.embedding-pipeline]]
부록 B). 그래서 여기 표로 박아 둔다:

    | | bge-base-en-v1.5 | multilingual-e5-base/large | bge-m3 (dense) |
    |---|---|---|---|
    | 풀링 | `hidden[:,0]` (CLS) | **mean** — `(h*mask).sum(1)/mask.sum(1)` | `hidden[:,0]` (CLS) |
    | 접두 | query 만 지시문 | **양쪽** `"query: "` / `"passage: "` | **없음** (양쪽 다) |
    | 정규화 | L2 | L2 | L2 |
    | 입력 | input_ids·attention_mask·token_type_ids | input_ids·attention_mask (**둘뿐**) | input_ids·attention_mask |
    | 패딩 id | 0 = `[PAD]` | 0 은 `<s>` 다 — `<pad>` 는 **1** | XLM-R 계열 — `<pad>` 는 **1** |

⚠ **접두 열의 정본은 이 파일이 아니라 `vinc_rag/embed/worker.py::prefix` 다.** 여기 표는
읽으라고 있는 것이고, `prefix()` 는 그 프로덕션 함수를 그대로 불러 쓴다 — 규약을 두 곳에
적었다가 갈라진 적이 있어서(이 파일에는 bge-m3 분기가 있었고 프로덕션에는 없었다) 값을
유도하는 쪽으로 바꿨다. 풀링·패딩은 아직 여기 구현이 따로 있고 `--selfcheck` 가 대조한다.
⚠ bge-m3 는 이름이 bge 라도 **v1.5 의 쿼리 지시문을 붙이면 안 된다** — 공식 사용법이
무지시(instruction-free)다. 프로덕션 `prefix()` 의 m3 분기가 v1.5 분기보다 먼저 서는 이유다.
그리고 공개 ONNX 내보내기 중에는 풀링·정규화를 그래프 안에 이미 구운 판이 있어서,
출력이 [batch, dim] 2차원이면 풀링을 **다시 하지 않는다**(`_batch` 의 ndim 가드).

두 풀링의 코사인은 같은 입력에도 0.87~0.91 에 불과하다. ONNX 전환 통과 기준이 ≥0.9999
였던 것을 생각하면 완전히 다른 벡터다 — 즉 풀링을 틀리면 "조금 나쁜" 게 아니라 딴것이다.

**드리프트 방지**: `--selfcheck` 는 이 구현과 `vinc_rag/embed/worker.py` 의 프로덕션
구현이 같은 벡터를 내는지 대조한다(코사인 ≥0.9999). 둘이 갈라진 채 비교 실험을 돌리면
"모델 차이"라고 읽은 것이 사실은 "구현 차이"가 된다.

사용:
    python -m vinc_rag.embed.encoder --model intfloat/multilingual-e5-base --selfcheck
"""
from __future__ import annotations

import argparse
import functools
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def is_e5(model: str) -> bool:
    return "e5" in model.lower()


@functools.cache
def _load_production_worker():
    """`worker.py` 를 **파일 경로로** 연다(한 번만 — 접두는 청크마다 부른다).

    패키지로 import 하지 않고 따로 싣는 이유: 워커는 import 시점에 환경변수(모델 등)를
    읽는다. `--selfcheck` 가 모델을 바꿔 가며 새로 실어야 해서, 이미 올라온 모듈을
    재사용하면 옛 모델 값을 쓰게 된다. `tests/rag/test_embed_worker.py` 도 같은 방식이다.
    """
    import importlib.util

    src = Path(__file__).resolve().parent / "worker.py"
    spec = importlib.util.spec_from_file_location("_vinc_embed_worker_contract", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def prefix(text: str, kind: str, model: str) -> str:
    """비대칭 검색 접두 — **프로덕션 구현에서 그대로 유도한다.**

    정본은 `vinc_rag/embed/worker.py::prefix` 다. 여기서 규약을 다시 적지 않는 이유는
    전에 그렇게 해서 갈라졌기 때문이다: 이 파일에는 bge-m3 분기가 있었고 프로덕션에는
    없어서, 평가는 맞고 프로덕션만 틀린 상태가 남았다(W16 실측 코사인 0.79~0.82).
    """
    return _load_production_worker().prefix(text, kind, model)


class Encoder:
    """모델 하나를 올려 텍스트를 벡터로 만든다. 지연 로드."""

    def __init__(self, model: str, onnx_path: str | None = None,
                 cache: str | None = None, max_tokens: int = 512,
                 batch: int = 8) -> None:
        self.model = model
        self.onnx_path = onnx_path
        self.cache = cache or os.environ.get("VINC_EMBED_CACHE") or ".cache/embed"
        self.max_tokens = max_tokens
        self.batch = batch
        self._sess = None
        self._tok = None
        self._counter = None
        self._names: list[str] = []

    def _load(self) -> None:
        if self._sess is not None:
            return
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        os.makedirs(self.cache, exist_ok=True)
        path = self.onnx_path or hf_hub_download(
            self.model, "onnx/model.onnx", cache_dir=self.cache)
        # 구운 아티팩트 **옆의** 토크나이저를 먼저 본다 — 프로덕션(`embed_worker.py`
        # `_Embedder._load`)이 하는 그대로다. 허브에서 새로 받으면 구운 쌍이 아닌 다른
        # 토크나이저로 평가하게 되고, 그 차이는 예외 없이 순위만 바꾼다.
        baked_tok = os.path.join(os.path.dirname(path), "tokenizer.json")
        tok_path = (baked_tok if os.path.exists(baked_tok)
                    else hf_hub_download(self.model, "tokenizer.json",
                                         cache_dir=self.cache))

        tok = Tokenizer.from_file(tok_path)
        tok.enable_truncation(self.max_tokens)
        # 조각내기(`split_for_embed`)가 **진짜 토큰 수**를 세게 한다. truncation 이 켜진
        # 위 인스턴스로 세면 길이가 항상 ≤상한으로 보이므로(`embed_worker` 와 같은 함정)
        # 자르지 않는 별도 인스턴스를 든다.
        self._counter = Tokenizer.from_file(tok_path)
        # ⚠ 기본 pad_id 0 이 항상 패딩 토큰은 아니다 — e5(XLM-R)에서 0 은 `<s>` 이고
        # `<pad>` 는 1 이다. 마스크가 풀링을 방어하긴 하지만 틀린 입력은 틀린 입력이다.
        pad = tok.token_to_id("<pad>") or tok.token_to_id("[PAD]")
        if pad is None:
            tok.enable_padding()
        else:
            tok.enable_padding(pad_id=pad, pad_token=tok.id_to_token(pad))
        self._tok = tok

        opts = ort.SessionOptions()
        # 기본 1 = 프로덕션(`vinc_rag/embed/worker.py`) 동일 조건. 코퍼스 인코딩을
        # 빨리 돌리고 싶을 때만 VINC_EVAL_THREADS 를 올린다 — 순위 지표는 스레드 수와
        # 무관하지만 **지연 측정은 1 로 잰 것만 프로덕션과 비교 가능**하다.
        threads = int(os.environ.get("VINC_EVAL_THREADS", "1") or "1")
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        self._sess = ort.InferenceSession(path, sess_options=opts,
                                          providers=["CPUExecutionProvider"])
        self._names = [i.name for i in self._sess.get_inputs()]

    def count_tokens(self, text: str) -> int:
        self._load()
        return len(self._counter.encode(text).ids)

    def split_for_embed(self, text: str, max_tokens: int = 500) -> list[str]:
        """청크 → 임베딩 조각(워커 `vinc_rag/embed/worker.py::_split_for_embed` 와 같은
        규칙: `store.chunking.chunk_doc` 토큰 기준·무손실 경계, 저장 청크는 안 건드린다).

        리허설의 `reindex` 는 여태 청크를 통째로 한 벡터로 만들었다. 프로덕션 워커는
        ≤500 토큰 조각마다 벡터를 만들어 `ChunkVec` 자식으로 편다(라이브 허브 실측
        청크당 2.08개). 그 형상으로 스토어를 만들려면 이 함수가 필요하다."""
        from store.chunking import chunk_doc
        parts = chunk_doc(text, max_tokens=max_tokens, count=self.count_tokens)
        return [p["text"] for p in parts if p["text"].strip()] or [text]

    def encode(self, texts: list[str], kind: str = "passage") -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            out.extend(self._batch([prefix(t, kind, self.model)
                                    for t in texts[i:i + self.batch]]))
        return out

    def hidden(self, texts: list[str], kind: str = "passage") -> list:
        """텍스트마다 패딩을 뺀 `last_hidden_state`[n, dim] — ColBERT 토큰의 재료.
        워커 `_Embedder.encode_with_hidden` 과 같게 한 줄씩 돈다(int8 은 배치가 값을 바꾼다)."""
        self._load()
        import numpy as np

        out = []
        for t in texts:
            e = self._tok.encode_batch([prefix(t, kind, self.model)])[0]
            ids = np.array([e.ids], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": np.array([e.attention_mask], dtype=np.int64),
                    "token_type_ids": np.array([e.type_ids], dtype=np.int64)}
            feed = {k: v for k, v in feed.items() if k in self._names}
            out.append(self._sess.run(None, feed)[0][0][:int(sum(e.attention_mask))])
        return out

    def _batch(self, texts: list[str]) -> list[list[float]]:
        self._load()
        import numpy as np

        enc = self._tok.encode_batch(texts)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        feed = {"input_ids": np.array([e.ids for e in enc], dtype=np.int64),
                "attention_mask": mask,
                "token_type_ids": np.array([e.type_ids for e in enc], dtype=np.int64)}
        feed = {k: v for k, v in feed.items() if k in self._names}
        hidden = self._sess.run(None, feed)[0]
        if hidden.ndim == 2:
            # 공개 ONNX 중에는 풀링·정규화가 그래프에 이미 구워진 판이 있다
            # ([batch, dim] 출력). 그 위에 다시 풀링하면 dim 축을 시퀀스 축으로
            # 오독한다 — 그대로 쓰고 아래 L2 만 거친다(멱등).
            pooled = hidden
        elif is_e5(self.model):
            m = mask[..., None].astype(hidden.dtype)
            pooled = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
        else:
            pooled = hidden[:, 0]         # CLS — bge-v1.5 와 bge-m3 dense 공통
        norm = np.linalg.norm(pooled, axis=1, keepdims=True)
        norm[norm == 0] = 1.0
        return (pooled / norm).astype(float).tolist()


def selfcheck(model: str) -> int:
    """이 구현 대 `vinc_rag/embed/worker.py` 프로덕션 구현. 코사인 ≥0.9999 를 요구한다."""
    import numpy as np

    os.environ.setdefault("VINC_EMBED_MODEL", model)
    # 위 `_load_production_worker` 와 같은 이유로 새로 싣는다(이 모델 값으로).
    import importlib.util
    _p = Path(__file__).resolve().parent / "worker.py"
    _spec = importlib.util.spec_from_file_location("_vinc_embed_worker", _p)
    w = importlib.util.module_from_spec(_spec)
    assert _spec.loader is not None
    _spec.loader.exec_module(w)

    samples = ["Newtonian mechanics describes motion under forces.",
               "허브는 단일 라이터라 리비전 롤링 전환이 안 된다.",
               "mixed 문장 with both scripts 섞여 있다.",
               # 512토큰을 **넘겨** 절단 경로를 지나게 한다. 짧은 문장 셋만 있으면 두
               # 구현이 서로 다르게 자르거나 다르게 채워도 이 가드가 통과한다 — 실제
               # 코퍼스 청크는 2000자라 언제나 이쪽이다.
               ("허브는 단일 라이터라 리비전 롤링 전환이 안 된다. " * 120).strip(),
               # 거의 전부가 패딩인 입력. pad-id 오선택(e5 는 0 이 `<s>`)을 여기서 본다.
               "단어"]
    # ⚠ 프로덕션이 **실제 해석한 onnx 파일**을 이쪽에도 준다. 프로덕션 `_resolve_model`
    # 은 e5 를 int8-rr 로 구워 쓰므로, 이걸 빼면 fp32 대 int8 의 양자화 오차(실측 최악
    # 코사인 0.98486)가 구현 차이로 읽혀 **거짓 경보**가 된다 — W16 에서 실제로 물렸다.
    # 같은 파일이면 두 구현은 1.0 이 나온다(실측 2026-08-30).
    resolved = w._resolve_model(w.MODEL, w.CACHE)
    enc, emb = Encoder(model, resolved), w._Embedder()
    # 두 종류(passage · query)를 다 지난다. 접두가 갈리는 것은 query 쪽이고(e5 는
    # 특히), passage 만 보면 질의 경로의 드리프트를 통째로 못 본다.
    worst = 1.0
    for kind in ("passage", "query"):
        mine = np.array(enc.encode(samples, kind))
        theirs = np.array(emb.encode([w._prefix(t, kind) for t in samples]))
        cos = (mine * theirs).sum(axis=1) / (
            np.linalg.norm(mine, axis=1) * np.linalg.norm(theirs, axis=1))
        worst = min(worst, float(cos.min()))
    print(f"selfcheck({model}): 최악 코사인 {worst:.8f} (기준 ≥0.9999)")
    if worst < 0.9999:
        print("🔴 두 구현이 갈라졌다 — 이 상태의 모델 비교는 구현 차이를 모델 차이로 "
              "읽는다. 풀링·접두·패딩 셋을 대조할 것.", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="intfloat/multilingual-e5-base")
    ap.add_argument("--onnx", help="ONNX 파일 경로(미지정 시 허브에서 fp32 내려받음)")
    ap.add_argument("--selfcheck", action="store_true",
                    help="프로덕션 임베더와 벡터 일치 확인(≥0.9999)")
    ap.add_argument("text", nargs="*", help="인코딩해 볼 문장")
    args = ap.parse_args()

    if args.selfcheck:
        return selfcheck(args.model)
    if not args.text:
        ap.error("문장을 주거나 --selfcheck 를 쓸 것")
    vecs = Encoder(args.model, args.onnx).encode(list(args.text), "passage")
    for t, v in zip(args.text, vecs):
        print(f"{t[:48]!r:52} dim={len(v)} head={[round(x, 4) for x in v[:4]]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
