#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ONNX 문장 임베더 — **모델 계열별 규약**을 한자리에 담는다(모델 비교용).

`packaging/embed_worker.py` 의 임베더는 프로덕션용이라 모델 하나를 상주시키지만,
모델 비교(bge vs e5, fp32 vs int8)는 **두 모델을 같은 코드로** 재야 한다. 그래서 계열을
인자로 받는 판을 따로 둔다.

🔴 **규약 셋 중 하나만 어긋나도 에러 없이 벡터가 틀린다**([[vinc.plan.embedding-pipeline]]
부록 B). 그래서 여기 표로 박아 둔다:

    | | bge-base-en-v1.5 | multilingual-e5-base |
    |---|---|---|
    | 풀링 | `hidden[:,0]` (CLS) | **mean** — `(h*mask).sum(1)/mask.sum(1)` |
    | 접두 | query 만 지시문 | **양쪽** `"query: "` / `"passage: "` |
    | 정규화 | L2 | L2 |
    | 입력 | input_ids·attention_mask·token_type_ids | input_ids·attention_mask (**둘뿐**) |
    | 패딩 id | 0 = `[PAD]` | 0 은 `<s>` 다 — `<pad>` 는 **1** |

두 풀링의 코사인은 같은 입력에도 0.87~0.91 에 불과하다. ONNX 전환 통과 기준이 ≥0.9999
였던 것을 생각하면 완전히 다른 벡터다 — 즉 풀링을 틀리면 "조금 나쁜" 게 아니라 딴것이다.

**드리프트 방지**: `--selfcheck` 는 이 구현과 `packaging/embed_worker.py` 의 프로덕션
구현이 같은 벡터를 내는지 대조한다(코사인 ≥0.9999). 둘이 갈라진 채 비교 실험을 돌리면
"모델 차이"라고 읽은 것이 사실은 "구현 차이"가 된다.

사용:
    python scripts/encoder.py --model intfloat/multilingual-e5-base --selfcheck
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def is_e5(model: str) -> bool:
    return "e5" in model.lower()


def prefix(text: str, kind: str, model: str) -> str:
    """비대칭 검색 접두. e5 는 **양쪽 필수**, bge 는 query 만."""
    if is_e5(model):
        return f"{'query' if kind == 'query' else 'passage'}: {text}"
    if kind != "query":
        return text                       # bge passage 는 맨몸이다
    return ("Represent this sentence for searching relevant passages: " + text)


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
        tok_path = hf_hub_download(self.model, "tokenizer.json", cache_dir=self.cache)

        tok = Tokenizer.from_file(tok_path)
        tok.enable_truncation(self.max_tokens)
        # ⚠ 기본 pad_id 0 이 항상 패딩 토큰은 아니다 — e5(XLM-R)에서 0 은 `<s>` 이고
        # `<pad>` 는 1 이다. 마스크가 풀링을 방어하긴 하지만 틀린 입력은 틀린 입력이다.
        pad = tok.token_to_id("<pad>") or tok.token_to_id("[PAD]")
        if pad is None:
            tok.enable_padding()
        else:
            tok.enable_padding(pad_id=pad, pad_token=tok.id_to_token(pad))
        self._tok = tok

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        self._sess = ort.InferenceSession(path, sess_options=opts,
                                          providers=["CPUExecutionProvider"])
        self._names = [i.name for i in self._sess.get_inputs()]

    def encode(self, texts: list[str], kind: str = "passage") -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            out.extend(self._batch([prefix(t, kind, self.model)
                                    for t in texts[i:i + self.batch]]))
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
        if is_e5(self.model):
            m = mask[..., None].astype(hidden.dtype)
            pooled = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
        else:
            pooled = hidden[:, 0]
        norm = np.linalg.norm(pooled, axis=1, keepdims=True)
        norm[norm == 0] = 1.0
        return (pooled / norm).astype(float).tolist()


def selfcheck(model: str) -> int:
    """이 구현 대 `packaging/embed_worker.py` 프로덕션 구현. 코사인 ≥0.9999 를 요구한다."""
    import numpy as np

    os.environ.setdefault("VINC_EMBED_MODEL", model)
    from packaging import embed_worker as w                # noqa: PLC0415

    samples = ["Newtonian mechanics describes motion under forces.",
               "허브는 단일 라이터라 리비전 롤링 전환이 안 된다.",
               "mixed 문장 with both scripts 섞여 있다."]
    mine = np.array(Encoder(model).encode(samples, "passage"))
    theirs = np.array(w._Embedder().encode([w._prefix(s, "passage") for s in samples]))
    cos = (mine * theirs).sum(axis=1) / (
        np.linalg.norm(mine, axis=1) * np.linalg.norm(theirs, axis=1))
    worst = float(cos.min())
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
