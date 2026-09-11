#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ONNX 크로스인코더 리랭커 · RRF 뒤에 붙는 2단계(정밀도)의 **측정용** 구현.

`scripts/encoder.py` 가 임베딩 모델 비교용이듯 이 파일은 리랭커 비교용이다. 프로덕션
경로(`api/query.py::search`)에는 리랭커가 **없다**. 이 파일은 그것을 붙일지 정하는
재료(품질·지연·메모리)를 만드는 계기이지 제품 코드가 아니다. 측정과 근거는 그래프의
`vinc.research.reranking-after-fusion-2026-09-03` (이중대괄호 인용을 안 쓴 이유는
`packaging/embed_worker.py::prefix` 주석과 같다: 매니페스트 갱신에 원격 그래프 키가 필요하다).

**규약 표.** 후보 셋은 전부 XLM-R 계열 `XLMRobertaForSequenceClassification`
(num_labels=1) 이라 같은 코드로 돈다. 임베딩 표(`encoder.py`)와 달리 풀링·접두가 없고
대신 **쌍 인코딩**이 있다:

    | | bge-reranker-v2-m3 | jina-reranker-v2-base-multilingual | mmarco-mMiniLMv2-L12-H384-v1 |
    |---|---|---|---|
    | 몸통 | XLM-R large (24층·1024) | XLM-R base (12층·768) | MiniLMv2 (12층·384) |
    | 파라미터 | 568M | 278M | 118M (그중 96M 이 어휘 행렬) |
    | 입력 | `<s> q </s></s> p </s>` (tokenizer.json 후처리기) | 같음 | 같음 |
    | 텐서 | input_ids · attention_mask (**token_type_ids 없음**) | 같음 | 같음 |
    | 출력 | logits[batch,1] · 클수록 관련 | 같음 | 같음 |
    | 패딩 id | 1 (`<pad>` · 0 은 `<s>`) | 1 | 1 |
    | 상한 | 8192 | 1024 | 512 |
    | 학습 언어 | 다국어(한국어 포함) | 다국어(100+) | mMARCO 14개 언어 · **한국어 없음** |

점수는 **로짓 그대로** 쓴다. 순위에는 단조 변환이 무의미하고 시그모이드는 표시용이다
(bge 의 `normalize=True`, jina 의 후처리가 그것이다). 로짓 눈금은 모델마다 다르므로
**모델 간 점수 비교 금지**: 같은 모델 안의 순서만 뜻이 있다.

⚠ `max_tokens` 기본 512 는 **측정 조건**이다. 청크 정본이 2000자라 한국어 청크는 512
토큰을 넘는다(`packaging/embed_worker.py::_split_for_embed` 가 벡터를 조각내는 이유).
그래서 창 둘을 잰다. `first` 는 앞 512 토큰만 보고(쌍 1개), `max` 는 워커와 같은
조각내기로 나눠 조각별 점수의 최대를 쓴다(쌍 약 2개). 창은 품질과 비용을 반대로 움직인다.

사용:
    python scripts/reranker.py --model BAAI/bge-reranker-v2-m3=.cache/rerank/v2-m3-int8-rr.onnx \\
        "what is a panda?" "The giant panda is a bear species endemic to China." "Paris is in France."
    python scripts/reranker.py --model cross-encoder/mmarco-mMiniLMv2-L12-H384-v1 --rss-probe
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: 워커가 벡터를 조각내는 상한(`packaging/embed_worker.py::MAX_CONTENT` 기본 500).
#: `max` 창은 이 값으로 조각내야 "벡터가 본 것과 같은 조각"을 리랭커가 본다.
PIECE_TOKENS = int(os.environ.get("VINC_EMBED_CONTENT_TOKENS", "500"))

#: `rrf_fuse` 의 k(60). 게이트 규칙 `top-agree` 가 "두 팔이 다 1위로 놓았다"를 점수로
#: 알아보는 데 쓴다: 그때 rrf_score = 2/(k+1) 이다.
#: 이 파일이 아는 융합 k 의 **역사적** 기본값. `gate()` 는 이제 이 값을 안 쓴다 —
#: 부르는 쪽이 자기 풀의 k 를 말한다. 남겨 두는 것은 옛 결과 JSON 을 읽을 때 필요해서다.
RRF_K = 60


def parse_model_spec(spec: str) -> tuple[str, str | None]:
    """`이름=경로` 를 (이름, 경로) 로. 경로가 없으면 허브의 `onnx/model.onnx` 를 받는다."""
    name, _, path = spec.partition("=")
    return name.strip(), (path.strip() or None)


# ── 순수 함수 (모델 없이 도는 것들 · 테스트 대상) ─────────────────────────────

def rerank_rows(rows: list[dict], scores: list[float], *, top_n: int | None = None,
                score_key: str = "rerank_score") -> list[dict]:
    """점수를 행에 붙이고 내림차순으로 다시 세운다. **안정 정렬**이라 동점은 들어온
    순서(= RRF 순위)를 지킨다. 원래 순위는 `prior_rank`(1부터) 로 남겨 무엇이 얼마나
    움직였는지 볼 수 있게 한다. 입력 행은 복사해 돌려준다(호출부의 리스트 불변)."""
    if len(rows) != len(scores):
        raise ValueError(f"rows {len(rows)} != scores {len(scores)}")
    out = []
    for i, (r, s) in enumerate(zip(rows, scores)):
        row = dict(r)
        row[score_key] = float(s)
        row["prior_rank"] = i + 1
        out.append(row)
    out.sort(key=lambda r: -r[score_key])          # sorted 는 안정 정렬이다
    return out[:top_n] if top_n else out


def maxp(piece_scores: list[list[float]]) -> list[float]:
    """조각별 점수 → 청크 점수(최대). 조각이 없는 청크는 -inf 다: "본 것이 없다"를
    "관련 없다"와 같게 보지 않는다(정렬에서 맨 뒤로 간다)."""
    return [max(ps) if ps else -math.inf for ps in piece_scores]


def summarize_runs(runs: list[float]) -> dict:
    """반복 측정 → 중앙값 + 흩어진 정도. **중앙값을 쓰는 이유**는 공유 vCPU 의 잡음이
    한쪽으로만 튀기 때문이다(느려지는 쪽으로). 평균은 그 꼬리를 그대로 먹는다.

    `spread_pct` 는 (최대-최소)/중앙값 이다. 이 값이 크면 그 런의 쌍당 비용으로 예산을
    짜면 안 된다 — 숨길 것이 아니라 그 자체가 결과다."""
    if not runs:
        return {"reps": 0, "ms_per_long_pair": None, "spread_pct": None}
    o = sorted(runs)
    mid = len(o) // 2
    med = o[mid] if len(o) % 2 else (o[mid - 1] + o[mid]) / 2
    return {"reps": len(o),
            "ms_per_long_pair": round(med, 1),
            "ms_per_long_pair_runs": [round(x, 1) for x in runs],
            "ms_per_long_pair_min": round(o[0], 1),
            "ms_per_long_pair_max": round(o[-1], 1),
            "spread_pct": round((o[-1] - o[0]) / med * 100, 1) if med else None}


def pick_piece(pieces: list[str], seq: int | None) -> str:
    """자식 하나만 고른다. seq 가 없거나 범위를 벗어나면 첫 조각(= `first` 창과 같은 자리).

    벡터 팔은 **이미** 부모당 최선 자식을 골라 놓고 접기에서 그 신원을 버린다
    (`store/embedded_backend.py::vector_search` 의 RETURN 에 seq 가 없다). 그것을
    되찾아 그 조각만 채점하면 쌍 수가 후보 수와 같아진다 — `max` 창의 2.16배가 1배다.
    되찾지 못한 후보(FTS 단독으로 들어온 행 등)는 `first` 와 같은 자리를 본다:
    "모른다"를 "관련 없다"로 바꾸지 않는다."""
    if not pieces:
        raise ValueError("pieces 가 비었다")
    if seq is None or seq < 0 or seq >= len(pieces):
        return pieces[0]
    return pieces[seq]


def gate(rows: list[dict], policy: str, *, k: int,
         score_key: str = "rrf_score") -> bool:
    """이 융합 풀에 리랭커를 **돌릴지**를 정한다(True = 돌린다).

    `always`      언제나.
    `top-agree`   두 팔이 같은 청크를 1위로 놓았으면 **건너뛴다**(rrf_score = 2/(k+1)).
                  "FTS 와 벡터가 동의한다"는 이 융합에서 얻을 수 있는 가장 강한 신호다.

    🔴 **`k` 는 이 풀이 실제로 융합된 값이어야 하고, 그래서 기본값이 없다.** 여기 상수를
    기본값으로 두었더니 프로덕션 k 가 60에서 20으로 바뀐 뒤(Vinc#270) 이 게이트가
    **75건 중 0건** 발화했다 — k=20 융합의 1위 점수는 최소 1/21 = 0.0476 인데 임계값은
    2/61 = 0.0328 이라 비교가 구조적으로 성립하지 않는다. 그런데 조용했다: `gated` 열이
    `rrf` 와 똑같이 나올 뿐이라 "게이트가 잘 걸러낸다"로 읽힌다. 부르는 쪽이 k 를 말하게
    하면 두 상수가 갈리는 순간이 호출부에서 보인다.
    근거: [[record:the-top-agree-gate-never-fired-after-the-k-change-2026-09-09]]
    `margin:x`    1위와 2위의 상대 간격 (s1-s2)/s1 이 x 미만일 때만 돌린다. 1위가
                  멀찍이 앞서면 리랭커가 바꿀 것이 적다는 가정이다.
    행이 둘 미만이면 돌릴 이유가 없다(순서가 없다)."""
    if len(rows) < 2:
        return False
    if policy == "always":
        return True
    s1 = float(rows[0].get(score_key) or 0.0)
    s2 = float(rows[1].get(score_key) or 0.0)
    if policy == "top-agree":
        both_first = 2.0 / (k + 1)
        return s1 < both_first - 1e-12
    if policy.startswith("margin:"):
        x = float(policy.split(":", 1)[1])
        if s1 <= 0:
            return True
        return (s1 - s2) / s1 < x
    raise ValueError(f"unknown gate policy {policy!r}")


# ── 모델 ─────────────────────────────────────────────────────────────────────

class Reranker:
    """크로스인코더 하나를 올려 (질의, 본문) 쌍에 점수를 준다. 지연 로드.

    스레드 기본 1 = 프로덕션 조건(`packaging/embed_worker.py` 와 같은 이유: 허브는 vCPU
    하나를 나눠 쓴다). `VINC_EVAL_THREADS` 로 올릴 수 있지만 **지연은 1 로 잰 것만
    프로덕션과 비교 가능**하고 순위 지표는 스레드 수와 무관하다."""

    def __init__(self, model: str, onnx_path: str | None = None, *,
                 cache: str | None = None, max_tokens: int = 512, batch: int = 8,
                 threads: int | None = None, piece_tokens: int = PIECE_TOKENS) -> None:
        self.model = model
        self.onnx_path = onnx_path
        self.cache = cache or os.environ.get("VINC_RERANK_CACHE") or ".cache/rerank"
        self.max_tokens = max_tokens
        self.batch = batch
        self.threads = threads
        self.piece_tokens = piece_tokens
        self.load_s: float | None = None
        self.pairs_scored = 0
        self._sess = None
        self._tok = None
        self._counter = None
        self._names: list[str] = []

    # ── 로드 ──
    def _load(self) -> None:
        if self._sess is not None:
            return
        import onnxruntime as ort
        from tokenizers import Tokenizer

        t0 = time.perf_counter()
        os.makedirs(self.cache, exist_ok=True)
        path = self.onnx_path
        if not path:
            from huggingface_hub import hf_hub_download
            path = hf_hub_download(self.model, "onnx/model.onnx", cache_dir=self.cache)
        # 구운 아티팩트 **옆의** 토크나이저를 먼저 본다(`encoder.py` 와 같은 규칙).
        # 허브에서 새로 받으면 구운 쌍이 아닌 토크나이저로 재게 되고 그 차이는 순위만 바꾼다.
        baked_tok = os.path.join(os.path.dirname(path), "tokenizer.json")
        if os.path.exists(baked_tok):
            tok_path = baked_tok
        else:
            from huggingface_hub import hf_hub_download
            tok_path = hf_hub_download(self.model, "tokenizer.json", cache_dir=self.cache)

        tok = Tokenizer.from_file(tok_path)
        # 질의는 통째로 두고 **본문만** 자른다. 기본 전략(longest_first)은 긴 본문과
        # 짧은 질의에서 결국 같은 결과지만, 질의가 길면 질의를 갉아먹는다: 질의는 리랭커가
        # 판단하는 기준이라 잘리면 안 된다.
        tok.enable_truncation(self.max_tokens, strategy="only_second")
        pad = tok.token_to_id("<pad>")
        if pad is None:
            pad = tok.token_to_id("[PAD]")
        if pad is None:
            tok.enable_padding()
        else:
            tok.enable_padding(pad_id=pad, pad_token=tok.id_to_token(pad))
        self._tok = tok
        # 조각내기용은 **자르지 않는** 별도 인스턴스여야 한다(`embed_worker` 와 같은 함정:
        # truncation 이 켜진 인스턴스로 세면 길이가 항상 ≤상한으로 보인다).
        self._counter = Tokenizer.from_file(tok_path)

        opts = ort.SessionOptions()
        threads = (self.threads if self.threads
                   else int(os.environ.get("VINC_EVAL_THREADS", "1") or "1"))
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        self._sess = ort.InferenceSession(path, sess_options=opts,
                                          providers=["CPUExecutionProvider"])
        self._names = [i.name for i in self._sess.get_inputs()]
        self.load_s = time.perf_counter() - t0

    def warm(self) -> float:
        """로드 + 첫 호출. 첫 호출에는 커널 준비가 실린다(임베더 실측 621ms 대 8ms 와
        같은 종류). 이것을 표본에 넣으면 첫 질의 하나가 p95 를 혼자 정한다."""
        t0 = time.perf_counter()
        self.score("warmup", ["warmup"])
        return time.perf_counter() - t0

    # ── 점수 ──
    def score(self, query: str, passages: list[str]) -> list[float]:
        """(query, passage) 쌍마다 로짓 하나. 배치 안의 패딩은 attention_mask 가 가리므로
        배치 구성이 점수를 바꾸지 않는다(단 int8 에서는 미세하게 흔들릴 수 있다)."""
        self._load()
        import numpy as np

        out: list[float] = []
        for i in range(0, len(passages), self.batch):
            enc = self._tok.encode_batch([(query, p) for p in passages[i:i + self.batch]])
            feed = {"input_ids": np.array([e.ids for e in enc], dtype=np.int64),
                    "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int64),
                    "token_type_ids": np.array([e.type_ids for e in enc], dtype=np.int64)}
            feed = {k: v for k, v in feed.items() if k in self._names}
            logits = self._sess.run(None, feed)[0]
            out.extend(float(x) for x in np.asarray(logits).reshape(-1))
        self.pairs_scored += len(passages)
        return out

    def count_tokens(self, text: str) -> int:
        self._load()
        return len(self._counter.encode(text).ids)

    def split(self, text: str) -> list[str]:
        """워커와 같은 조각내기(`api.docstore.chunk_doc`, 토큰 기준·무손실 경계)."""
        from api.docstore import chunk_doc
        parts = chunk_doc(text, max_tokens=self.piece_tokens, count=self.count_tokens)
        return [p["text"] for p in parts if p["text"].strip()] or [text]

    def score_texts(self, query: str, texts: list[str], *, window: str = "first",
                    seqs: list[int | None] | None = None) -> tuple[list[float], int]:
        """청크 텍스트 목록 → 청크 점수. 반환 (점수, 실제로 잰 쌍 수).

        `first`  청크 앞 max_tokens 만 본다(쌍 = 청크 수).
        `max`    조각별로 재서 최대(쌍 = 조각 수). 벡터가 본 조각과 같은 조각이다.
        `child`  벡터가 이긴 조각 **하나만** 잰다(쌍 = 청크 수). `seqs` 가 후보마다 그
                 조각 번호를 준다 — 없으면 그 후보는 `first` 와 같은 자리를 본다."""
        if window == "first":
            return self.score(query, texts), len(texts)
        if window == "child":
            sq = seqs if seqs is not None else [None] * len(texts)
            if len(sq) != len(texts):
                raise ValueError(f"seqs {len(sq)} != texts {len(texts)}")
            chosen = [pick_piece(self.split(t), n) for t, n in zip(texts, sq)]
            return self.score(query, chosen), len(chosen)
        if window != "max":
            raise ValueError(f"unknown window {window!r}")
        pieces: list[str] = []
        owner: list[int] = []
        for i, t in enumerate(texts):
            for p in self.split(t):
                pieces.append(p)
                owner.append(i)
        flat = self.score(query, pieces)
        per: list[list[float]] = [[] for _ in texts]
        for o, s in zip(owner, flat):
            per[o].append(s)
        return maxp(per), len(pieces)


def peak_rss_mb() -> float:
    """이 프로세스의 피크 RSS(MB). 리눅스는 ru_maxrss 가 KB, macOS 는 바이트다.
    못 재면 -1.0 (0.0 은 "안 썼다"로 읽히므로 절대 내지 않는다)."""
    try:
        import resource
        v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if v <= 0:
            return -1.0
        return round(v / 1e6 if sys.platform == "darwin" else v / 1e3, 1)
    except Exception:
        return -1.0


def rss_probe(model: str, onnx: str | None, *, max_tokens: int, pairs: int = 16,
              reps: int = 3, batch: int = 8) -> dict:
    """모델 하나만 든 프로세스의 로드 시간·첫 호출·피크 RSS·쌍당 비용. **한 프로세스에
    모델 하나**여야 한다: 피크 카운터는 내려가지 않아서 두 모델을 한 프로세스에서 재면
    둘째 값이 첫째에 오염된다(성능 정본 §4 와 같은 규칙).

    🔴 **쌍당 비용은 한 번 재면 안 된다.** 전에는 16쌍을 한 번 재서 그 값을 점추정으로
    냈는데, 같은 러너 타입(AMD EPYC 7763)에서 두 번 돌린 결과가 jina 385.2ms 와
    693.5ms 로 **80% 갈렸다**(2026-09-04 실측, run 33841962408 대 33842493227).
    공유 vCPU 에서는 그 정도가 정상 잡음이고, 표본 하나를 그대로 내놓으면 그 잡음이
    "하드웨어가 다르다"는 결론으로 읽힌다 — 실제로 그렇게 한 번 오독했다.
    그래서 `reps` 번 재고 **중앙값**을 내며 최소·최대를 함께 싣는다. 스프레드가 크면
    그 수치로 예산을 짜면 안 된다는 뜻이고, 그건 숨길 것이 아니라 결과다.

    피크 RSS 는 반대로 안정적이었다(같은 두 런에서 1% 안). 반복해도 피크는 최대치이므로
    의미가 그대로다.

    ``batch`` 는 **한 프로세스에 하나만** 재야 한다. 피크 카운터는 안 내려가므로 배치 8을
    잰 뒤 같은 프로세스에서 1을 재면 8의 값을 다시 읽는다. 배치별로 프로세스를 새로 띄울
    것 — 워크플로가 도커를 두 번 도는 이유가 이것이다.
    근거: [[record:encoder-resident-set-leaves-1-1-gib-for-a-reranker-2026-09-08]]"""
    before = peak_rss_mb()
    rr = Reranker(model, onnx, max_tokens=max_tokens, batch=batch)
    t0 = time.perf_counter()
    rr._load()
    load_s = time.perf_counter() - t0
    t1 = time.perf_counter()
    rr.score("warmup query", ["warmup passage"])
    first_call_s = time.perf_counter() - t1
    # 상한 길이 쌍으로 아레나를 실제 크기까지 키운다. 짧은 쌍만 재면 피크가 작게 나온다.
    long = ("허브는 단일 라이터라 리비전 롤링 전환이 안 된다. " * 200).strip()
    runs: list[float] = []
    for _ in range(max(1, reps)):
        t2 = time.perf_counter()
        rr.score("무엇이 문제인가", [long] * pairs)
        runs.append((time.perf_counter() - t2) * 1000 / pairs)
    return {"model": model, "onnx": onnx, "max_tokens": max_tokens, "batch": batch,
            "load_s": round(load_s, 3), "first_call_s": round(first_call_s, 3),
            "cold_total_s": round(load_s + first_call_s, 3),
            "long_pairs": pairs,
            "long_pairs_s": round(sum(runs) * pairs / 1000, 3),
            **summarize_runs(runs),
            "threads": int(os.environ.get("VINC_EVAL_THREADS", "1") or "1"),
            "artifact_mb": round(os.path.getsize(onnx) / 1e6, 1) if onnx and os.path.exists(onnx) else None,
            "peak_rss_mb_before": before, "peak_rss_mb": peak_rss_mb()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="BAAI/bge-reranker-v2-m3",
                    help="이름 또는 이름=ONNX경로")
    ap.add_argument("--max-tokens", type=int, default=512)
    ap.add_argument("--window", choices=("first", "max", "child"), default="first")
    ap.add_argument("--reps", type=int, default=3,
                    help="--rss-probe 의 쌍 측정 반복 횟수. 1 이면 스프레드를 못 본다")
    ap.add_argument("--batch", type=int, default=8,
                    help="추론 배치. 상주 크기의 절반 이상이 아레나라 이 값이 그것을 정한다 "
                         "— 배치별로 프로세스를 새로 띄울 것(피크는 안 내려간다)")
    ap.add_argument("--rss-probe", action="store_true",
                    help="이 모델만 든 프로세스의 로드·첫 호출·피크 RSS 를 JSON 으로")
    ap.add_argument("text", nargs="*", help="첫 인자가 질의, 나머지가 본문")
    args = ap.parse_args()
    name, path = parse_model_spec(args.model)
    if args.rss_probe:
        import json
        print(json.dumps(rss_probe(name, path, max_tokens=args.max_tokens,
                                   reps=args.reps, batch=args.batch),
                         ensure_ascii=False))
        return 0
    if len(args.text) < 2:
        ap.error("질의 하나와 본문 하나 이상을 주거나 --rss-probe 를 쓸 것")
    rr = Reranker(name, path, max_tokens=args.max_tokens, batch=args.batch)
    scores, pairs = rr.score_texts(args.text[0], args.text[1:], window=args.window)
    print(f"{name} · pairs={pairs} · load={rr.load_s:.2f}s")
    for row in rerank_rows([{"text": t} for t in args.text[1:]], scores):
        print(f"{row['rerank_score']:+8.3f}  (prior {row['prior_rank']})  {row['text'][:70]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
