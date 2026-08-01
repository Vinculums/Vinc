#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""허브 자체호스팅 임베딩 워커 — `needs_embed` 문서의 청크를 **로컬 모델**로 임베딩해
사이드카에 돌려준다. 원문은 사이드카 **루프백**으로만 오가고 서버 밖으로 안 나간다
(no cloud/telemetry 약속 — 외부 임베딩 API 미사용).

배경: 사이드카 바이너리는 임베딩 스택을 번들에서 제외한다(packaging/sidecar.spec —
2.7GB 회피). 그래서 앱 편집·팩 임포트·파일 드롭으로 들어온 문서는 `needs_embed=1` 로
FTS 만 되고 벡터가 빈다. 이 워커가 허브에서만 그 벡터를 채운다. 데스크탑은 FTS 유지.

**torch 가 아니라 ONNX 를 쓴다(2026-07-30 전환).** 가비아 시절엔 systemd timer 가
별도 venv 의 sentence-transformers 를 돌렸다. Azure 로 오면서 그 timer 가 사라졌고
(허브 컨테이너에 embed 가 아예 없었다 — 조용한 기능 회귀), 되살릴 때 torch 를 다시
싣는 대신 ONNX 로 갈아탔다. 근거 셋:
  · 허브 실측 사용량이 1024MiB 중 **115MiB** 라 여유는 있지만, torch(~800MiB RSS)를
    올리면 한도에 붙어 OOM 이 컨테이너 전체(=MCP 포함)를 죽인다.
  · 자원을 1.0vCPU/2GiB 로 올리면 상시 과금이 배증한다(월 $10~15 추가 추정).
    ONNX 는 ~660MiB 로 끝나 **증설 자체가 불필요**하다.
  · 모델 가중치는 공식 리포 것을 쓴다 — 서드파티 재배포본이 아니다.

**모델은 multilingual-e5-base 다(2026-08-01 전환).** 발주자가 영어와 한국어를 함께
쓰기로 정했고, 영어 전용 bge 는 한글 음절 토큰이 vocab 에 **0개**라(자모로 분해) 한국어
검색이 무너져 있었다. 한영 혼합 코퍼스 실측(질의 60): 한국어 nDCG 0.347 → 0.670 으로
**거의 두 배**, 영어는 0.669 → 0.636 으로 5% 손실. 차원은 768 그대로라 재프로비전 불요.

⚠ 계열이 바뀌면 **규약도 바뀐다. 셋 중 하나만 어긋나도 에러 없이 벡터가 틀린다**:
    풀링   bge=CLS · e5=**mean**(attention_mask 가중)   — 두 풀링의 코사인 0.87~0.91
    접두   bge=query 만 · e5=**양쪽** query:/passage:   — 있고 없고의 코사인 0.95~0.97
    양자화 공식 int8 은 non-VNNI 에서 포화 → **직접 reduce_range 로 만든다**
그래서 이 파일의 규약은 `_pool`·`_prefix`·`_resolve_model` **세 곳에만** 산다.

⚠ **벡터 공간 동일성**: 모델이 바뀌면 옛 벡터와 새 벡터는 아예 다른 공간이라 코사인
대조가 성립하지 않는다. 대신 ① 같은 모델의 두 구현(참조 vs 이 코드)을 대조해 ≥0.9999 를
확인하고 ② 색인 혼재는 `switch_embed_model` 이 **옛 벡터를 전부 지워서** 막는다.

파이프라인:
    조각내기(≤500토큰, 청크는 그대로) → 접두 → tokenize(truncate 512) → onnx
    → 계열별 풀링 → L2 정규화

경로(전부 사이드카 루프백):
  GET  /embed/pending?limit=N&model=<id> → {chunks:[{doc_id, chunk_id, text}]}
       (model 을 실어 보내면 사이드카가 전환을 감지해 전량 재임베딩을 건다)
  POST /embed/apply {doc_id, embeddings:{chunk_id:[[...],[...]]}} → 저장
       (v8 — 청크당 **벡터 목록**. 전 청크 완료 시 needs_embed=0)

  VINC_SIDECAR=http://127.0.0.1:8787 VINC_SIDECAR_TOKEN=<토큰> \
    python embed_worker.py [--once] [--interval 300]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request

# Windows 콘솔/파이프 기본 인코딩(cp949)에서 비ASCII 출력이 UnicodeEncodeError 로
# 죽는다 — 이 워커는 한국어 오류 메시지를 찍으므로 api/cli.py 와 같은 규율을 따른다.
# 컨테이너(UTF-8)에서는 무해하지만, 로컬 스모크·테스트에서 실제로 터져 잡았다.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass

SIDECAR = os.environ.get("VINC_SIDECAR", "http://127.0.0.1:8787").rstrip("/")
TOKEN = (os.environ.get("VINC_SIDECAR_TOKEN")
         or os.environ.get("VINC_TOKEN") or os.environ.get("TOKEN") or "")
MODEL = os.environ.get("VINC_EMBED_MODEL", "intfloat/multilingual-e5-base")
DOC_BATCH = int(os.environ.get("VINC_EMBED_DOC_BATCH", "16"))
# 모델 캐시는 **영속 볼륨**에 둔다(Azure Files /data). 이미지에 굽지 않는 이유:
# 440MB 가 이미지에 들어가면 허브 배포마다 그만큼 pull 이 늘고, 허브는 api/ 변경마다
# 배포된다 — 단일 writer 라 배포 시간이 곧 다운타임이다. 첫 기동에서 한 번 받고
# 재시작에는 재사용한다.
CACHE = os.environ.get("VINC_EMBED_CACHE", "/data/embed-model")
MAX_TOKENS = int(os.environ.get("VINC_EMBED_MAX_TOKENS", "512"))
# 조각 하나에 담을 **콘텐츠** 토큰 수(v8 부모-자식 — `_split_for_embed`).
# 상한 512 에서 빼야 할 것: 특수토큰 2개(`<s>`/`</s>`) + 접두("passage: " 2토큰 ·
# "query: " 3토큰, 실측). 발주자 결정 500 은 그 뒤 여유가 12 라 빠듯하지만, 진짜
# 토크나이저로 자를 때는 `chunk_doc` 의 수리 루프가 상한을 보장하고(실측 512 초과 0),
# 토크나이저 없는 경로는 추정기의 1.3배 여유가 흡수한다.
MAX_CONTENT = int(os.environ.get("VINC_EMBED_CONTENT_TOKENS", "500"))
# 한 번의 세션 실행에 넣을 청크 수 상한. **없으면 안 된다** — 자세한 근거는
# `_Embedder.encode` 주석. 8 인 이유: 실측상 청크는 대부분 512 토큰에 붙어 있어
# (중앙 469 · p90 512) 배치당 출력 텐서가 8×512×768×fp32 = 12MB 로 잡히고, 어텐션
# 중간 텐서(배치×헤드12×512²×fp32 ≈ 배치당 12.6MB)까지 더해도 100MB 대에 머문다.
# 허브는 1024MiB 를 사이드카·MCP 와 나눠 쓰고 가중치만 이미 ~440MB 다.
EMBED_BATCH = max(1, int(os.environ.get("VINC_EMBED_BATCH", "8")))
# bge-*-v1.5 의 쿼리 지시 접두(저장 passage 에는 붙이지 않는다 — `_prefix` 주석).
QUERY_PREFIX = os.environ.get(
    "VINC_EMBED_QUERY_PREFIX",
    "Represent this sentence for searching relevant passages: ")


def _call(path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        SIDECAR + path, data=data,
        method="POST" if body is not None else "GET",
        headers={"Authorization": f"Bearer {TOKEN}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.load(resp)


def _is_e5(model: str = "") -> bool:
    return "e5" in (model or MODEL).lower()


def _split_for_embed(text: str) -> list[str]:
    """청크 → 임베딩 조각(≤`MAX_CONTENT` 토큰). **저장 청크는 건드리지 않는다.**

    청크는 정본(D45)이고 2000자 단위인데 모델 입력 상한은 512토큰이다. 한 벡터만 두면
    앞 512토큰만 담기고 나머지는 조용히 버려진다 — 실측 유실이 한국어 42.6% · 영어
    12.3% 다. 청크를 토큰 기준으로 다시 자르는 길도 있지만 정본을 전부 다시 쓰는 일이고
    개념 링크를 −2.5%/−10% 깎는다. 그래서 **벡터만** 쪼갠다(v8 부모-자식).

    ⚠ 조각을 만들어 **평균**하는 더 싼 안은 실측에서 기준선보다 나빴다(전체 nDCG 0.535
    vs 0.601). 2000자 안의 서로 다른 내용이 평균에서 뭉개진다 — 조각별로 따로 둬야 한다.

    자르는 일은 `api.docstore.chunk_doc` 이 이미 한다(무손실 경계 + 토큰 수리 루프).
    사이드카가 없는 환경을 위해 임포트 실패는 통째 반환으로 강등한다."""
    if not text:
        return [text]
    try:
        from api.docstore import chunk_doc
    except Exception:
        return [text]
    tok = getattr(_SPLIT, "tok", None)
    parts = chunk_doc(text, max_tokens=MAX_CONTENT, count=tok)
    return [p["text"] for p in parts] or [text]


class _SplitState:
    """조각내기용 토크나이저 핸들. 모델을 올릴 때 채워진다 — 없으면 `chunk_doc` 의
    문자 계열 추정기로 강등한다(데스크탑 경로와 같은 폴백)."""
    tok = None


_SPLIT = _SplitState()


def _pool(hidden, mask, np):
    """모델 계열에 맞는 풀링 — **여기가 틀리면 에러 없이 벡터가 통째로 어긋난다.**

    bge-*-v1.5 는 CLS(`hidden[:,0]`), e5 계열은 **mean** 이다. 근거는 각 모델 리포의
    `1_Pooling/config.json` — e5 는 `pooling_mode_cls_token=false`,
    `pooling_mode_mean_tokens=true` 다. 같은 입력에 대해 두 풀링의 코사인은 0.87~0.91 에
    불과하다(실측). ONNX 전환 때 통과 기준이 ≥0.9999 였던 걸 생각하면 완전히 다른 벡터다.

    mean 은 반드시 **attention_mask 로 가중**해야 한다. 패딩 위치를 평균에 넣으면 짧은
    문장일수록 벡터가 0 쪽으로 끌려가고, 그 정도가 **같은 배치에 무엇이 실렸는지**에 따라
    달라진다(패딩 길이가 배치 최장에 맞춰지므로). 즉 마스크를 빼먹으면 벡터가 배치 구성에
    의존하게 된다 — 재현 불가능한 색인이 된다."""
    if not _is_e5():
        return hidden[:, 0]
    m = mask[..., None].astype(hidden.dtype)
    return (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)


def _resolve_model(model: str, cache: str) -> str:
    """모델 ONNX 경로 — 필요하면 **직접 양자화해서** 캐시에 둔다.

    e5 의 공식 int8 은 `model_qint8_avx512_vnni.onnx` **한 종뿐**이고
    U8S8 + per_channel + `reduce_range=False` 조합이다. ORT 문서가 non-VNNI CPU 에서
    VPMADDUBSW 의 int16 누산 포화를 경고하는 바로 그 설정이라, VNNI 가 없으면 **에러 없이
    품질만** 떨어진다. 실측(AVX2 기기, 한영 60질의): 한국어 nDCG 0.670 → 0.472.

    같은 fp32 를 `reduce_range=True`(가중치 7비트 → 포화가 산술적으로 불가)로 직접
    양자화하면 한국어가 0.669 로 **fp32 와 사실상 동일**해진다. 그래서 공식본을 쓰지 않고
    여기서 만든다 — VNNI 가 있어도 reduce_range 는 무해하므로 CPU 를 확인할 필요조차 없다.

    🔴 **런타임 양자화는 하지 않는다 — 못 한다.** e5 의 임베딩 행렬 하나가 192M float
    (732MB)이고 원본 모델까지 메모리에 올라가 피크가 1.5GB 를 넘는다(실측: 컨테이너보다
    넉넉한 기기에서도 `Unable to allocate 732 MiB` 로 죽었다). 1024MiB 컨테이너에서는
    불가능하다. 그래서 **빌드 타임에 굽고**(`Dockerfile.hub`) 여기서는 그 파일을 쓴다.
    경로는 `VINC_EMBED_MODEL_PATH`.

    구워진 파일이 없으면(로컬 개발 등) 그 자리에서 만든다 — 개발 기기는 메모리가
    넉넉하다는 전제다. 그마저 안 되면 fp32 로 간다: 느리고 무겁지만 **정확하다**.
    조용히 공식 int8 로 갈아타지 않는다(그쪽이 품질을 깎는다)."""
    baked = os.environ.get("VINC_EMBED_MODEL_PATH", "").strip()
    if baked and os.path.exists(baked):
        return baked

    from huggingface_hub import hf_hub_download
    if not _is_e5(model) or os.environ.get("VINC_EMBED_QUANTIZE", "on") != "on":
        return hf_hub_download(model, "onnx/model.onnx", cache_dir=cache)

    made = os.path.join(cache, "e5-int8-rr.onnx")
    if os.path.exists(made):
        return made
    fp32 = hf_hub_download(model, "onnx/model.onnx", cache_dir=cache)
    try:
        from onnxruntime.quantization import QuantType, quantize_dynamic
    except Exception as exc:
        print(f"[embed] 양자화 불가({type(exc).__name__}) — fp32 로 진행", file=sys.stderr,
              flush=True)
        return fp32
    tmp = made + ".part"
    print("[embed] int8 양자화(reduce_range=True) 중 — 메모리 1.5GB+ 필요", flush=True)
    try:
        quantize_dynamic(fp32, tmp, weight_type=QuantType.QInt8, per_channel=True,
                         reduce_range=True, extra_options={"MatMulConstBOnly": True})
    except (MemoryError, Exception) as exc:     # noqa: B014 — MemoryError 를 눈에 띄게
        print(f"[embed] 양자화 실패({type(exc).__name__}) — fp32 로 진행. "
              f"컨테이너라면 이미지에 굽는 경로(VINC_EMBED_MODEL_PATH)를 쓸 것",
              file=sys.stderr, flush=True)
        try:
            os.remove(tmp)
        except Exception:
            pass
        return fp32
    os.replace(tmp, made)                       # 원자적 — 중간에 죽어도 반쪽이 안 남는다
    try:
        os.remove(fp32)                         # 1GB 를 영속 볼륨에 남길 이유가 없다
    except Exception:
        pass
    print(f"[embed] 양자화 완료 → {made}", flush=True)
    return made


class _Embedder:
    """ONNX 세션 + 토크나이저. 지연 로드 — 일감이 있을 때만 올린다(idle RSS 절약)."""

    def __init__(self) -> None:
        self._sess = None
        self._tok = None
        self._names: list[str] = []

    def _load(self) -> None:
        if self._sess is not None:
            return
        import numpy as np  # noqa: F401  (여기서 실패하면 아래 except 가 사유를 싣는다)
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        os.makedirs(CACHE, exist_ok=True)
        onnx_path = _resolve_model(MODEL, CACHE)
        # 토크나이저도 구워진 것이 있으면 그걸 쓴다(런타임 네트워크 의존 제거).
        baked_tok = os.path.join(os.path.dirname(onnx_path), "tokenizer.json")
        tok_path = (baked_tok if os.path.exists(baked_tok)
                    else hf_hub_download(MODEL, "tokenizer.json", cache_dir=CACHE))

        tok = Tokenizer.from_file(tok_path)
        tok.enable_truncation(MAX_TOKENS)
        # ⚠ `enable_padding()` 의 기본 pad_id 는 **0** 인데 그게 항상 패딩 토큰은 아니다.
        # e5(XLM-R)에서 0 은 `<pad>` 가 아니라 `<s>`(문장 시작)이고 `<pad>` 는 1 이며
        # `[PAD]` 는 vocab 에 아예 없다. attention_mask 가 패딩을 0 으로 표시하므로 풀링
        # 결과는 방어되지만, 문장 시작 토큰을 꼬리에 줄줄이 붙이는 건 그냥 틀린 입력이다.
        pad = tok.token_to_id("<pad>")
        if pad is None:
            pad = tok.token_to_id("[PAD]")
        if pad is None:
            tok.enable_padding()
        else:
            tok.enable_padding(pad_id=pad, pad_token=tok.id_to_token(pad))
        self._tok = tok
        # 조각내기(`_split_for_embed`)가 **진짜 토큰 수**를 쓰게 한다. 없으면 문자 계열
        # 추정기로 강등되는데, 추정기는 여유를 크게 잡아 조각이 불필요하게 짧아진다.
        # ⚠ truncation·padding 이 켜진 이 인스턴스를 그대로 쓰면 길이가 512 에서 잘려
        # "항상 ≤512" 로 보인다 — 세는 용도로는 **자르지 않는 별도 인스턴스**가 필요하다.
        counter = Tokenizer.from_file(tok_path)
        _SPLIT.tok = lambda s: len(counter.encode(s).ids)
        # 스레드 1개 — 허브는 0.5 vCPU 를 사이드카·MCP 와 나눠 쓴다. 배경 작업이
        # 전경 응답을 굶기지 않게 명시적으로 묶는다.
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        self._sess = ort.InferenceSession(
            onnx_path, sess_options=opts, providers=["CPUExecutionProvider"])
        self._names = [i.name for i in self._sess.get_inputs()]
        print(f"[embed] model ready ({MODEL}, onnx, cache={CACHE})", flush=True)

    def encode(self, texts: list[str]) -> list[list[float]]:
        """청크 목록을 임베딩한다 — **반드시 나눠서** 세션에 넣는다.

        여기가 나뉘어 있지 않으면 큰 문서 하나가 컨테이너를 죽인다. 호출부는 한
        문서의 **전 청크**를 통째로 넘기고(:func:`main`), 공급측 `/embed/pending` 의
        limit 은 청크가 아니라 **문서 수**다(`api.docstore.pending_embed_chunks`).
        즉 배치 크기 = 그 문서의 청크 수이고 아무도 그걸 제한하지 않았다.

        메모리는 배치에 선형으로 늘고 상한이 없었다. 출력 텐서 `last_hidden_state`
        하나만 해도 `배치 × 512 × 768 × fp32` 로 고정이라 ONNX 최적화로 줄일 수 없다:

            청크  23 (스타터팩 최대 문서)  →   34MB
            청크 100 (약 200KB 문서)       →  150MB
            청크1500 (웹 상한 4MB 문서)    → 2250MB   ← 한도 1024MiB 초과

        터지면 둘 중 하나인데 둘 다 나쁘다. 커널 OOM 이 컨테이너를 잡으면 **MCP 가
        같이 죽는다**(워커·사이드카·MCP 가 한 컨테이너다 — docker-entrypoint.sh).
        ONNX 를 고른 이유가 정확히 그 OOM 을 피하려던 것이었는데, 프레임워크만 바꾸고
        배치 축은 열어 둔 셈이었다. Python `MemoryError` 로 잡히면 그 문서는
        needs_embed=1 로 남아 **5분마다 영원히 재시도**하며 매번 같은 고점을 친다.

        나눠도 벡터는 사실상 달라지지 않는다. 각 청크의 출력은 attention_mask 때문에
        같은 배치의 다른 청크에 의존하지 않는다(패딩 위치는 소프트맥스에서 제외된다).
        fp32 에서는 배치 모양이 바뀌어도 코사인 1 - 1e-7 수준이라 순위를 바꿀 크기가
        아니다.

        ⚠ **단 int8 양자화 모델에서는 이 논증이 약해진다**(2026-08-01 실측). 동적
        양자화는 배치의 활성값 분포로 스케일을 잡으므로 같은 문장이라도 **어떤 문장과
        함께 실렸는지에 따라 벡터가 달라진다** — 단독 인코딩 대 동반 배치 코사인
        0.9912 로, fp32 의 1-1e-7 과는 자릿수가 다르다. 배치 크기를 바꾸면 저장 벡터가
        미묘하게 흔들린다는 뜻이라, int8 모델을 채택한다면 색인 일관성을 위해
        `EMBED_BATCH` 를 고정하고 재임베딩은 같은 값으로 돌려야 한다.

        길이순 정렬은 하지 않는다. 패딩 낭비를 줄이는 표준 기법이지만, 실측 분포가
        중앙 469 · p90 512 라 이미 대부분 상한에 붙어 있어 얻을 게 거의 없고, 순서를
        바꾸면 chunk_id 대응을 되돌리는 코드가 늘어 사고 여지만 커진다.
        """
        out: list[list[float]] = []
        for i in range(0, len(texts), EMBED_BATCH):
            out.extend(self._encode_batch(texts[i:i + EMBED_BATCH]))
        return out

    def _encode_batch(self, texts: list[str]) -> list[list[float]]:
        self._load()
        import numpy as np

        enc = self._tok.encode_batch(texts)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        feed = {
            "input_ids": np.array([e.ids for e in enc], dtype=np.int64),
            "attention_mask": mask,
            "token_type_ids": np.array([e.type_ids for e in enc], dtype=np.int64),
        }
        # e5(XLM-R)는 token_type_ids 를 받지 않는다(그래프 입력이 둘뿐 — 실측).
        # 세션이 받는 것만 남긴다.
        feed = {k: v for k, v in feed.items() if k in self._names}
        hidden = self._sess.run(None, feed)[0]
        pooled = _pool(hidden, mask, np)
        norm = np.linalg.norm(pooled, axis=1, keepdims=True)
        norm[norm == 0] = 1.0                                  # 빈 입력 방어
        return (pooled / norm).astype(float).tolist()


def serve_encode(emb: "_Embedder", port: int) -> None:
    """루프백 인코딩 서비스 — 사이드카가 **쿼리 임베딩**을 얻어 가는 창구.

    왜 여기냐: 임베딩 모델을 들고 있는 프로세스가 이 워커뿐이다. 사이드카(데스크탑·허브
    양쪽)에는 torch·sentence-transformers 가 없어서 `/doc/reembed` 조차 강등된다. 그래서
    여태 `query_embedding` 을 만들 주체가 **아무 데도 없었고**, 벡터는 write-only 였다
    (검색 호출부 둘 다 query_embedding 을 안 넘긴다).

    사이드카에 모델을 또 올리는 선택지도 있었지만 1024MiB 컨테이너에 440MB 를 **두 벌**
    올리게 된다. 여기서 열어 주면 한 벌로 끝난다.

    계약: `POST /encode {"texts": [...], "kind": "query"|"passage"}` → `{"vectors": [[...]]}`
    - **루프백 전용**(127.0.0.1 바인딩). 프록시·공개 노출 대상이 아니다 — `/embed/pending`
      과 같은 등급의 내부 표면이라 웹 3중 등재도 하지 않는다.
    - 인코딩은 `_lock` 으로 직렬화한다. 배경 pull 루프와 전경 쿼리가 같은 세션을 쓰는데,
      동시에 밀어 넣으면 메모리 고점이 두 배가 되고 그건 이 컨테이너에서 OOM 이다.
    - 이 서버가 죽어도 워커 본체는 살아야 한다(부가 기능이 본업을 죽이지 않는다).
    """
    import json as _json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    lock = threading.Lock()

    class _H(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass                      # 요청당 한 줄은 5분 루프 로그를 덮는다

        def do_POST(self):
            if self.path.rstrip("/") != "/encode":
                self.send_error(404, "no such route")
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = _json.loads(self.rfile.read(n) or b"{}")
                texts = [str(t) for t in (body.get("texts") or [])]
                if not texts:
                    raise ValueError("texts required")
                kind = str(body.get("kind") or "passage")
                with lock:
                    vecs = emb.encode([_prefix(t, kind) for t in texts])
                out = _json.dumps({"vectors": vecs}).encode("utf-8")
            except Exception as exc:
                # 사유를 삼키지 않는다 — 호출부가 로그에 남길 수 있게 본문으로 준다.
                out = _json.dumps({"error": f"{type(exc).__name__}: {exc}"}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    srv = ThreadingHTTPServer(("127.0.0.1", port), _H)
    t = threading.Thread(target=srv.serve_forever, daemon=True, name="vinc-embed-encode")
    t.start()
    print(f"[embed] encode service on 127.0.0.1:{port}", flush=True)


def _prefix(text: str, kind: str) -> str:
    """모델별 비대칭 검색 접두.

    bge-*-v1.5 는 **쿼리에만** 지시 접두를 붙인다(저자 안내). 저장된 passage 벡터는
    접두 없이 만들어졌으므로 여기서 passage 에 무언가를 붙이면 기존 벡터와 공간이
    어긋난다 — 붙이지 않는 게 호환의 조건이다.
    e5 계열로 갈아타면 규약이 `query:` / `passage:` **양쪽**으로 바뀌고, 그때는 저장
    벡터도 전량 재생성해야 한다(모델 교체 자체가 이미 전량 재임베딩을 요구한다).
    """
    if "e5" in MODEL.lower():
        # e5 는 **양쪽 모두** 필수다(모델카드 FAQ: 빠뜨리면 성능이 떨어진다).
        # 형태는 콜론 뒤 공백 하나 — "query: " 3토큰 · "passage: " 2토큰(실측).
        return f"{'query' if kind == 'query' else 'passage'}: {text}"
    if kind != "query":
        return text                      # bge passage 는 맨몸이다
    return f"{QUERY_PREFIX}{text}" if QUERY_PREFIX else text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="pending 을 한 번 비우고 종료")
    ap.add_argument("--interval", type=float, default=300.0,
                    help="상주 모드에서 pending 이 빌 때 대기 초")
    ap.add_argument("--encode-port", type=int, default=int(os.environ.get("VINC_EMBED_PORT", "0")),
                    help="쿼리 임베딩 루프백 서비스 포트(0=끔)")
    args = ap.parse_args()

    emb = _Embedder()
    # ⚠ 생존 신호가 없으면 **워커의 죽음과 정상 대기가 구분되지 않는다.** 원래 워커가
    # Azure 이관 때 사라졌는데 일주일 넘게 아무도 몰랐던 이유가 정확히 이것이다(조용한
    # happy path). 그래서 ① 첫 성공 조회를 무조건 한 번 찍어 루프백이 살아있음을
    # 증명하고 ② 그 뒤로는 idle 이 HEARTBEAT_ROUNDS 번 쌓일 때마다 한 줄 남긴다.
    # 5분 간격 × 12 = 약 1시간에 1줄 — 로그를 더럽히지 않으면서 침묵을 진단 가능하게.
    heartbeat_rounds = int(os.environ.get("VINC_EMBED_HEARTBEAT_ROUNDS", "12"))
    if args.encode_port:
        # 쿼리 임베딩 창구. 실패해도 본업(배경 임베딩)은 계속 돈다 — 부가 기능이
        # 본업을 죽이지 않는다는 이 워커의 규율 그대로다.
        try:
            serve_encode(emb, args.encode_port)
        except Exception as exc:
            print(f"[embed] encode service 기동 실패(무시): {exc}", file=sys.stderr, flush=True)
    proved = False
    idle = 0
    while True:
        try:
            # 어떤 모델을 들었는지 **매 라운드 알려 준다**. 사이드카가 기록된 모델과
            # 비교해 다르면 전량 재임베딩을 건다(멱등 — 바뀐 첫 라운드만 일한다).
            # 모델을 바꿔도 needs_embed 가 저절로 켜지지 않아서 필요한 장치다.
            q = urllib.parse.quote(MODEL, safe="")
            pending = _call(f"/embed/pending?limit={DOC_BATCH}&model={q}").get("chunks", [])
        except Exception as e:
            # 사이드카 기동 전/재시작 중일 수 있다. 워커가 죽으면 컨테이너의
            # 다른 프로세스(MCP)까지 영향받을 이유가 없으므로 물러났다 다시 온다.
            print(f"[embed] pending 조회 실패: {e}", file=sys.stderr, flush=True)
            if args.once:
                return 1
            time.sleep(args.interval)
            continue

        if not proved:
            proved = True
            print(f"[embed] loopback ok · pending={len(pending)} "
                  f"(interval={args.interval:g}s)", flush=True)

        if not pending:
            if args.once:
                return 0
            idle += 1
            if heartbeat_rounds > 0 and idle % heartbeat_rounds == 0:
                print(f"[embed] alive · idle {idle} rounds, pending=0", flush=True)
            time.sleep(args.interval)
            continue
        idle = 0

        by_doc: dict[str, list[dict]] = {}
        for c in pending:
            by_doc.setdefault(c["doc_id"], []).append(c)

        for doc_id, chunks in by_doc.items():
            try:
                # 청크 하나 → 벡터 **여럿**(v8 부모-자식). 저장 청크는 2000자인데 모델
                # 입력 상한은 512토큰이라 한 벡터로는 앞부분만 담긴다(실측 유실: 한국어
                # 42.6% · 영어 12.3%). 청크를 ≤MAX_CONTENT 토큰 조각으로 나눠 조각마다
                # 벡터를 만들고, 스토어가 ChunkVec 자식 행으로 편다. **청크 자체는 안
                # 건드린다** — 재청킹이 아니라서 MENTIONS·발췌·주제 유도가 그대로다.
                #
                # ⚠ **반드시 `_prefix` 를 거친다.** bge 에서는 passage 가 맨몸이라 안
                # 거쳐도 결과가 같지만, 그 "같음"에 기대면 모델을 바꾸는 순간 조용히
                # 틀린다 — e5 는 저장 쪽에도 "passage: " 가 **필수**다(모델카드). 접두는
                # 조각을 낸 **뒤에** 붙인다. 그래야 접두 토큰이 512 예산에 정확히 든다.
                per_chunk = [_split_for_embed(c["text"]) for c in chunks]
                flat = [_prefix(p, "passage") for parts in per_chunk for p in parts]
                vecs = emb.encode(flat)
                out, at = {}, 0
                for c, parts in zip(chunks, per_chunk):
                    out[c["chunk_id"]] = vecs[at:at + len(parts)]
                    at += len(parts)
                res = _call("/embed/apply", {"doc_id": doc_id, "embeddings": out})
                print(f"[embed] doc={doc_id} chunks={res.get('embedded')} "
                      f"cleared={res.get('cleared')}", flush=True)
            except Exception as e:
                # 한 문서가 막혀도 나머지는 진행한다. 다음 라운드에 다시 잡힌다
                # (needs_embed 가 안 지워졌으므로 pending 에 계속 남는다).
                print(f"[embed] doc={doc_id} 실패: {e}", file=sys.stderr, flush=True)

        if args.once and len(by_doc) < DOC_BATCH:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
