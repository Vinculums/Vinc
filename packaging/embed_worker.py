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

두 역할 (2026-08-07 — 허브 1Gi 에서 ONNX 적재가 OOM/137 로 죽어 모델을 분리했다):

  서버 `vinc-encode`  모델을 든다. 2Gi · minReplicas=0(쓸 때만 깨어난다)
      VINC_EMBED_BIND=0.0.0.0 VINC_EMBED_PORT=8791 \
      VINC_EMBED_ENCODE_TOKEN=<토큰> python embed_worker.py --serve-only

  클라이언트 `vinc-hub`  모델을 **안 든다**. 배치 루프만 돌고 인코딩은 위임한다
      VINC_EMBED_REMOTE=https://vinc-encode…/ VINC_EMBED_ENCODE_TOKEN=<토큰> \
      python embed_worker.py --interval 300

사이드카의 질의 임베딩(`VINC_EMBED_ENCODE`)도 같은 서버를 가리킨다. 그쪽 상한은 **둘**
이다(`api/server.py` `_QVEC_TIMEOUT`·`_QVEC_COLD_TIMEOUT`, 기본 1.5초·30초). 인코더가 바쁠
뿐인 초과는 짧은 쪽에서 끊어 FTS 로 강등하지만(사유는 응답 메타에 남는다), 레플리카 0에서
깨는 중인 콜드스타트는 긴 쪽으로 한 번 기다려 깨운다. 그러니 **첫 질의는 강등되지 않고
30초 가까이 걸릴 수 있다** — 30초짜리 검색을 보면 여기부터 볼 것.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
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

# 🔴 이 파일은 `python packaging/embed_worker.py` 로 돈다(docker-entrypoint.sh). 그러면
# `sys.path[0]` 은 **`packaging/`** 이고 리포 루트는 들어가지 않는다 — CWD 가 경로에
# 붙는 것은 `-m` 과 대화형뿐이다(사이드카는 `python -m api.cli` 라 우연히 성립한다).
# 그 결과 `_split_for_embed` 의 `from api.docstore import chunk_doc` 가 조용히 실패해
# **부모-자식 조각내기(v8)가 통째로 죽는다**: 청크가 통으로 들어가 앞 512토큰만 담기고
# 나머지는 버려진다(실측 유실 한국어 42.6% · 영어 12.3%). 에러도 안 난다 — 그 임포트는
# `except Exception: return [text]` 로 강등되게 설계돼 있기 때문이다(데스크탑 폴백).
# 테스트(`tests/test_embed_worker.py`)는 리포 루트에서 돌아 `api` 가 잡히므로 이 결함을
# 재현하지 못한다. 컨테이너에서만 나는 무음 고장이라 여기서 경로를 명시적으로 세운다.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from api.encoder_runtime import fingerprint as _encoder_fingerprint  # noqa: E402
ENCODER_RUNTIME = _encoder_fingerprint()
ENCODER_BUILD = os.environ.get("VINC_ENCODER_BUILD", "").strip()
ENCODER_BUILD_REQUIRED = os.environ.get("VINC_ENCODER_BUILD_REQUIRED") == "on"

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

# ── 인코딩을 다른 프로세스에 맡기는 모드 ────────────────────────────────────
# 2026-08-07: 허브(1Gi)에서 ONNX 세션 적재가 rss 646MB 에 닿아 커널이 워커를
# **SIGKILL(137)** 했다. 3회 재기동 전부 같은 지점 — 코드가 아니라 자원 문제다.
# 모델을 별도 컨테이너(`vinc-encode`, 2Gi · minReplicas=0)로 옮기고, 허브에 남는
# 워커는 **모델을 안 올리는 얇은 클라이언트**가 된다. 그러면 허브 메모리는 그대로고
# 인코더는 쓸 때만 깨어난다.
#
# 이 파일 하나가 두 역할을 다 한다:
#   서버(vinc-encode) : VINC_EMBED_BIND=0.0.0.0 · --serve-only  → 모델을 든다
#   클라이언트(허브)   : VINC_EMBED_REMOTE=https://…            → 모델을 안 든다
# 규약이 한 파일에 있어야 접두·풀링·양자화 세 가지가 갈라지지 않는다(파일 상단 ⚠).
REMOTE_ENCODE = os.environ.get("VINC_EMBED_REMOTE", "").strip().rstrip("/")
# 인코더가 루프백을 벗어나면 인증이 필요하다. 루프백 시절엔 없었다.
ENCODE_TOKEN = os.environ.get("VINC_EMBED_ENCODE_TOKEN", "").strip()
ENCODE_BIND = os.environ.get("VINC_EMBED_BIND", "127.0.0.1").strip() or "127.0.0.1"
# 원격 인코딩 타임아웃. 배경 배치라 넉넉해야 한다 — 콜드스타트(0→1 리비전)가
# 여기 포함된다. 질의 경로의 두 상한(`VINC_EMBED_ENCODE_TIMEOUT` ·
# `VINC_EMBED_ENCODE_COLD_TIMEOUT`)과는 별개다.
REMOTE_TIMEOUT = float(os.environ.get("VINC_EMBED_REMOTE_TIMEOUT", "120"))
# 원격 인코딩 한 요청의 조각 수 상한. 1코어 bge-m3 기준 초당 1개 안팎이라 16 이면
# 요청 하나가 `REMOTE_TIMEOUT` 에 한참 못 미친다(`_RemoteEncoder.encode`).
REMOTE_BATCH = max(1, int(os.environ.get("VINC_EMBED_REMOTE_BATCH", "16")))
# 같은 문서가 이만큼 **연달아** 실패하면 `QUARANTINE_S` 동안 건너뛴다. 매 라운드
# 다시 시도하면 그 문서 하나가 인코더를 붙잡고, 그동안 검색의 질의 임베딩이 시간
# 초과로 떨어진다(2026-09-22 부터 사흘간 76회 실패한 문서가 실제로 그랬다).
QUARANTINE_AFTER = max(1, int(os.environ.get("VINC_EMBED_QUARANTINE_AFTER", "3")))
QUARANTINE_S = float(os.environ.get("VINC_EMBED_QUARANTINE_S", "21600"))


def _apply_paused() -> bool:
    """`VINC_EMBED_APPLY_PAUSED` 가 켜졌는가. 사이드카 `api/server.py` 의
    `embed_apply_paused` 와 같은 판정이다(이 스크립트는 api 를 import 하지 않는다)."""
    return os.environ.get("VINC_EMBED_APPLY_PAUSED", "").strip().lower() in (
        "1", "true", "yes", "on")


def _call(path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        SIDECAR + path, data=data,
        method="POST" if body is not None else "GET",
        headers={"Authorization": f"Bearer {TOKEN}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.load(resp)


def _mb(path: str) -> int:
    """파일 크기(MB). 진단 로그 전용이라 실패해도 0 을 돌려주고 넘어간다."""
    try:
        return os.path.getsize(path) >> 20
    except Exception:
        return 0


def _rss_mb() -> int:
    """이 프로세스의 RSS(MB) — 리눅스 전용, 실패하면 0.

    OOM 이 의심되는 자리마다 이 값을 함께 찍는다. cgroup 킬은 흔적을 안 남기므로
    **죽기 직전에 찍어 둔 숫자**가 유일한 증거가 된다. psutil 을 새로 싣지 않으려고
    `/proc` 을 직접 읽는다(이 워커는 의존성 하나가 곧 이미지 크기다)."""
    try:
        with open("/proc/self/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 0


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


#: 모델 → 구워 둔 int8-rr 아티팩트 **파일명**. 빌드(`packaging/bake_embed_model.py`)와
#: 런타임(:func:`_resolve_model`)이 같은 이름을 봐야 한다 — 갈라지면 런타임이 "구운 게
#: 없다"고 판정하고 조용히 fp32(e5 1.06GB · bge-m3 2.27GB)를 내려받는다. 그게 1024MiB
#: 컨테이너에서는 OOM 이고, OOM 은 사이드카·MCP 까지 함께 죽인다.
_ARTIFACT_NAMES = {
    # 이 이름은 **바꾸지 않는다** — 이미 구워진 이미지의 VINC_EMBED_MODEL_PATH 와
    # 영속 볼륨의 캐시가 이 파일명을 쓴다. 규칙에서 유도하면 다른 이름이 나온다.
    "intfloat/multilingual-e5-base": "e5-int8-rr.onnx",
}


def _no_artifact_msg(model: str, want: str, cache: str, exc: BaseException) -> str:
    """아티팩트를 못 얻었을 때의 **처방**. 사유만 적으면 운영자가 다음에 뭘 할지 모른다."""
    return (f"[embed] {model} 의 int8-rr 아티팩트({want})를 얻지 못했다: "
            f"{type(exc).__name__}: {exc}\n"
            f"  · 이미지에 굽기: Dockerfile.hub 의 VINC_EMBED_MODEL/VINC_EMBED_MODEL_PATH\n"
            f"  · 미리 굽기: python scripts/quantize_e5.py --model {model} "
            f"--out {os.path.join(cache, want)}\n"
            f"  · 의도적으로 fp32 로 갈 때만: VINC_EMBED_QUANTIZE=off "
            f"(허브 형상에서는 OOM 이다)")


def artifact_name(model: str) -> str:
    """이 모델의 int8-rr 아티팩트 파일명(빌드·런타임 공용 규약).

    캐시 디렉터리 하나가 모델 여러 벌을 담을 수 있어야 한다. 이름이 모델과 무관하면
    (옛 `e5-int8-rr.onnx` 고정) 모델을 바꿔도 **옛 모델의 파일을 그대로 집는다** —
    차원이 우연히 같으면 아무 오류 없이 딴 모델로 색인이 만들어진다."""
    return _ARTIFACT_NAMES.get(model) or f"{model.split('/')[-1].lower()}-int8-rr.onnx"


def _resolve_model(model: str, cache: str) -> str:
    """모델 ONNX 경로 — 필요하면 **직접 양자화해서** 캐시에 둔다.

    🔴 **무음 강등 금지(2026-08-30).** 이 함수는 세 갈래로 조용히 틀릴 수 있었다:
      ① 첫 분기가 `if not _is_e5(model) …: return fp32` 라 **e5 가 아니면 항상 fp32**
         였다. bge-m3 를 채택해도 워커는 2.27GB fp32 를 받는다.
      ② 구운 아티팩트 이름이 모델과 무관해서, 다른 모델을 굽고 env 만 바꾸면 **옛
         모델의 파일**을 집는다(차원이 같으면 영원히 안 들킨다).
      ③ 양자화가 실패하면 fp32 를 돌려줬다. "느리지만 정확하다"는 말은 메모리가
         넉넉할 때만 참이고, 허브에서는 그 경로가 곧 OOM 이다.
    지금은 셋 다 **예외로 끝난다**. 조용히 무거운 것을 받느니 크게 실패한다.
    의도적인 fp32 는 `VINC_EMBED_QUANTIZE=off` 로만 — 그때는 로그를 남긴다.

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
    넉넉하다는 전제다. 그마저 안 되면 **실패한다**(위 ③).
    조용히 공식 int8 로 갈아타지도 않는다(그쪽이 품질을 깎는다).

    ⚠ 2GB 를 넘는 fp32(e5-large·bge-m3)는 가중치가 `model.onnx_data` 별도 파일이라
    여기의 단일 파일 다운로드로는 양자화가 성립하지 않는다. 그 모델들은 미리 구워서
    (`scripts/quantize_e5.py --model …` 또는 이미지 빌드) 경로로 주는 것이 유일한 길이고,
    아래 실패 메시지가 그 처방을 말한다."""
    want = artifact_name(model)
    baked = os.environ.get("VINC_EMBED_MODEL_PATH", "").strip()
    if baked:
        # **이름이 곧 계약이다.** 구운 것이 다른 모델의 아티팩트면 여기서 끊는다 —
        # 그대로 쓰면 차원이 우연히 같은 조합에서 영원히 안 들키는 무음 고장이 된다.
        if os.path.basename(baked) != want:
            raise RuntimeError(
                f"VINC_EMBED_MODEL_PATH={baked} 는 다른 모델의 아티팩트다 — "
                f"VINC_EMBED_MODEL={model} 에는 {want} 가 필요하다. 이미지를 그 모델로 "
                f"다시 굽거나(Dockerfile.hub · bake_embed_model.py) 경로를 맞출 것")
        if os.path.exists(baked):
            return baked

    from huggingface_hub import hf_hub_download
    if os.environ.get("VINC_EMBED_QUANTIZE", "on") != "on":
        # 의도된 fp32(개발·대조 실험). 조용하지 않게 남긴다 — 이 줄이 없으면
        # "왜 갑자기 메모리를 이만큼 쓰나"의 답이 로그 어디에도 없다.
        print(f"[embed] VINC_EMBED_QUANTIZE=off — fp32 로 진행({model}). "
              "허브 형상(1024MiB)에서는 OOM 이다", file=sys.stderr, flush=True)
        return hf_hub_download(model, "onnx/model.onnx", cache_dir=cache)

    made = os.path.join(cache, want)
    if os.path.exists(made):
        return made
    fp32 = hf_hub_download(model, "onnx/model.onnx", cache_dir=cache)
    try:
        from onnxruntime.quantization import QuantType, quantize_dynamic
    except Exception as exc:
        raise RuntimeError(_no_artifact_msg(model, want, cache, exc)) from exc
    tmp = made + ".part"
    print("[embed] int8 양자화(reduce_range=True) 중 — 메모리 1.5GB+ 필요", flush=True)
    try:
        quantize_dynamic(fp32, tmp, weight_type=QuantType.QInt8, per_channel=True,
                         reduce_range=True, extra_options={"MatMulConstBOnly": True})
    except (MemoryError, Exception) as exc:     # noqa: B014 — MemoryError 를 눈에 띄게
        try:
            os.remove(tmp)
        except Exception:
            pass
        raise RuntimeError(_no_artifact_msg(model, want, cache, exc)) from exc
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

        # 🔴 여기서부터 세션 생성까지가 이 프로세스의 **메모리 고점**이다. 죽는다면
        # 여기서 죽는다 — 그리고 cgroup OOM 은 SIGKILL 이라 파이썬이 한 줄도 못 남긴다.
        # 그래서 단계마다 미리 찍는다. 마지막으로 찍힌 줄이 곧 사인(死因)이 된다.
        # (2026-08-07 진단: 허브 워커가 `loopback ok` 직후 사라졌는데 예외 로그도
        #  양자화 로그도 없어, 죽은 자리가 이 구간이라는 것까지밖에 좁히지 못했다.)
        print(f"[embed] loading tokenizer … {tok_path} ({_mb(tok_path)}MB) "
              f"rss={_rss_mb()}MB", flush=True)
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
        # 🔴 **메모리 고점을 깎는 두 스위치.** 이 컨테이너는 1GiB 를 사이드카·MCP 와
        # 나눠 쓰고(실측 정상 상태 136MB) 가중치만 279MB 다. ORT 기본값은 둘 다 속도를
        # 위해 메모리를 선점한다:
        #   · CPU 메모리 아레나는 요청보다 크게(kNextPowerOfTwo) 잡고 **돌려주지 않는다**.
        #   · mem_pattern 은 첫 실행의 텐서 배치를 미리 통째로 예약한다.
        # 배치 8 · 스레드 1 인 이 워커는 둘 다 이득이 거의 없는데 고점만 키운다. 고점이
        # 한도를 넘으면 cgroup OOM 킬러가 **가장 큰 프로세스인 이 워커를 조용히** 죽인다
        # (SIGKILL — 파이썬 예외도 로그도 없다. 컨테이너는 멀쩡하고 /health 는 200 이다).
        opts.enable_cpu_mem_arena = False
        opts.enable_mem_pattern = False
        print(f"[embed] loading onnx session … {onnx_path} ({_mb(onnx_path)}MB) "
              f"rss={_rss_mb()}MB", flush=True)
        self._sess = ort.InferenceSession(
            onnx_path, sess_options=opts, providers=["CPUExecutionProvider"])
        self._names = [i.name for i in self._sess.get_inputs()]
        print(f"[embed] model ready ({MODEL}, onnx, cache={CACHE}, "
              f"rss={_rss_mb()}MB)", flush=True)

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


class _RemoteEncoder:
    """`_Embedder` 의 자리를 대신하는 얇은 클라이언트 — 모델을 **안 든다**.

    허브에 남는 워커가 이것을 쓴다. 계약은 `_Embedder.encode` 와 같다(문자열 목록 →
    벡터 목록). 다른 점은 계산을 `vinc-encode` 컨테이너에 맡긴다는 것뿐이다.

    ⚠ **접두를 여기서 붙이지 않는다.** 배치 루프가 `_prefix(p, "passage")` 를 이미
    거친 문자열을 넘긴다(main()). 서버가 `kind` 를 보고 또 붙이면 `passage: passage: …`
    가 되어 **에러 없이 벡터만 틀린다**(접두 유무의 코사인이 0.95~0.97 이라 눈에 안
    띈다). 그래서 `raw:true` 로 보내고 서버는 그 경우 접두를 건드리지 않는다.

    ⚠ 재시도를 넣지 않는다. 실패한 문서는 `needs_embed` 가 안 지워져 **다음 라운드에
    그대로 다시 잡힌다** — 여기서 또 시도하면 콜드스타트 대기가 겹쳐 라운드가 늘어질
    뿐이다(F2 에서 브리지 재시도를 줄인 것과 같은 판단).
    """

    def __init__(self, url: str) -> None:
        self._url = url.rstrip("/") + "/encode"
        self.runtime = ""
        self.instance = ""

    def encode(self, texts: list[str]) -> list[list[float]]:
        """``REMOTE_BATCH`` 조각씩 나눠 보낸다. 한 요청에 문서 전체를 실으면 큰 문서는
        인코더(1코어) 계산이 `REMOTE_TIMEOUT` 을 넘겨 **매 라운드 같은 자리에서 실패**하고,
        클라이언트가 끊은 뒤에도 인코더는 끝까지 계산하느라 그동안 질의 임베딩이 밀린다.
        나눠 보내면 요청마다 한도 안에 끝나고, 문서는 한 번에 다 적용된다(적용 계약 불변).
        근거: [[episode:2026-09-24-hub-sidecar-segfault-loop]]"""
        self.runtime = ""
        self.instance = ""
        out: list[list[float]] = []
        for i in range(0, len(texts), REMOTE_BATCH):
            out.extend(self._encode_once(texts[i:i + REMOTE_BATCH]))
        return out

    def _encode_once(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        body = json.dumps({"texts": texts, "raw": True}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if ENCODE_TOKEN:
            headers["Authorization"] = f"Bearer {ENCODE_TOKEN}"
        req = urllib.request.Request(self._url, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=REMOTE_TIMEOUT) as resp:
            out = json.load(resp) or {}
        actual_runtime = out.get("encoder_runtime")
        if (not actual_runtime or
                (ENCODER_RUNTIME and actual_runtime != ENCODER_RUNTIME) or
                (ENCODER_BUILD_REQUIRED and not ENCODER_RUNTIME) or
                (self.runtime and actual_runtime != self.runtime)):
            raise RuntimeError(
                f"remote encoder runtime differs from this writer: "
                f"remote={actual_runtime or 'unknown'}; local={ENCODER_RUNTIME}")
        actual_build = out.get("encoder_build")
        if (ENCODER_BUILD_REQUIRED or ENCODER_BUILD or actual_build) and (
                not ENCODER_BUILD or actual_build != ENCODER_BUILD):
            raise RuntimeError(
                f"remote encoder build differs from this writer: "
                f"remote={actual_build or 'unknown'}; local={ENCODER_BUILD or 'unknown'}")
        self.runtime = actual_runtime
        actual_instance = str(out.get("encoder_instance") or "")
        if not ENCODER_BUILD and (
                not actual_instance or
                (self.instance and actual_instance != self.instance)):
            raise RuntimeError("remote encoder instance changed or unknown")
        self.instance = actual_instance
        vecs = out.get("vectors")
        if not isinstance(vecs, list) or len(vecs) != len(texts):
            # 개수가 어긋나면 아래 zip 이 **조용히 잘라** 일부 청크가 벡터 없이
            # 성공 처리된다. 조각↔벡터 정렬이 이 파이프라인의 유일한 암묵 계약이라
            # 여기서 끊는다.
            raise RuntimeError(
                f"encode 응답이 입력과 어긋난다: 입력 {len(texts)} · 응답 "
                f"{len(vecs) if isinstance(vecs, list) else type(vecs).__name__}"
                + (f" · error={out.get('error')}" if out.get("error") else ""))
        return vecs


def serve_encode(emb: "_Embedder", port: int) -> int:
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

    instance_id = secrets.token_hex(16)

    class _H(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass                      # 요청당 한 줄은 5분 루프 로그를 덮는다

        def _reply(self, code: int, payload: dict) -> None:
            out = _json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def _authed(self) -> bool:
            """토큰이 설정돼 있을 때만 검사한다 — 루프백 배치(허브 안)는 무토큰이다.

            ⚠ 바인드가 루프백을 벗어났는데 토큰이 없으면 **인증 없는 인코더**가 된다.
            그 조합은 `main()` 이 기동 시점에 거부한다(여기서 막으면 이미 늦다)."""
            if not ENCODE_TOKEN:
                return True
            got = (self.headers.get("Authorization") or "").strip()
            return got == f"Bearer {ENCODE_TOKEN}"

        def do_GET(self):
            # 컨테이너 플랫폼의 프로브용. **모델을 올리지 않는다** — 프로브가 모델
            # 적재를 유발하면 minReplicas=0 의 의미가 없어지고(깨어날 때마다 646MB)
            # 프로브 타임아웃에도 걸린다. "프로세스가 살아 있다"만 답한다.
            if self.path.rstrip("/") in ("/health", ""):
                self._reply(200, {"status": "ok", "model": MODEL,
                                  "loaded": getattr(emb, "_sess", None) is not None,
                                  "encoder_runtime": ENCODER_RUNTIME,
                                  "encoder_build": ENCODER_BUILD,
                                  "encoder_instance": instance_id})
                return
            self.send_error(404, "no such route")

        def do_POST(self):
            if self.path.rstrip("/") != "/encode":
                self.send_error(404, "no such route")
                return
            if not self._authed():
                self._reply(401, {"error": "unauthorized"})
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = _json.loads(self.rfile.read(n) or b"{}")
                texts = [str(t) for t in (body.get("texts") or [])]
                if not texts:
                    raise ValueError("texts required")
                kind = str(body.get("kind") or "passage")
                # `raw` = 호출부가 접두를 **이미** 붙였다(배치 루프가 그렇다).
                # 여기서 또 붙이면 `passage: passage: …` 가 되어 에러 없이 벡터만
                # 틀린다 — 접두 유무의 코사인이 0.95~0.97 이라 눈에 안 띈다.
                raw = bool(body.get("raw"))
                with lock:
                    vecs = emb.encode(texts if raw
                                      else [_prefix(t, kind) for t in texts])
                # `model` 은 질의 경로가 저장 벡터의 모델과 대조한다 — 폭이 같은 다른
                # 모델은 캐스팅 오류 없이 엉뚱한 이웃만 낸다(RV-06).
                out = _json.dumps({"vectors": vecs, "model": MODEL,
                                   "encoder_runtime": ENCODER_RUNTIME,
                                   "encoder_build": ENCODER_BUILD,
                                   "encoder_instance": instance_id}).encode("utf-8")
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

    srv = ThreadingHTTPServer((ENCODE_BIND, port), _H)
    bound = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True, name="vinc-embed-encode")
    t.start()
    print(f"[embed] encode service on {ENCODE_BIND}:{bound}"
          f"{' (token 인증)' if ENCODE_TOKEN else ''}", flush=True)
    # 실제로 바인드된 포트를 돌려준다 — port=0 이면 OS 가 고른다. 호출부가 미리
    # 빈 포트를 찾아 두고(bind→close) 다시 바인드하는 방식은 그 사이에 다른
    # 프로세스가 채 갈 수 있어 **산발적으로만** 실패한다(실측: 전체 스위트 1회 실패).
    return bound


def prefix(text: str, kind: str, model: str) -> str:
    """모델 계열별 비대칭 검색 접두 — **이 리포에서 접두 규약의 정본이다.**

    bge-*-v1.5 는 **쿼리에만** 지시 접두를 붙인다(저자 안내). 저장된 passage 벡터는
    접두 없이 만들어졌으므로 여기서 passage 에 무언가를 붙이면 기존 벡터와 공간이
    어긋난다 — 붙이지 않는 게 호환의 조건이다.
    e5 계열은 규약이 `query:` / `passage:` **양쪽**이고, 그때는 저장 벡터도 전량
    재생성해야 한다(모델 교체 자체가 이미 전량 재임베딩을 요구한다).
    bge-m3 는 이름에 bge 가 들어 있어도 **양쪽 다 무접두**다(공식 사용법이 무지시).
    그래서 m3 분기가 v1.5 분기보다 **먼저** 선다 — 순서를 바꾸면 m3 질의에 v1.5
    지시문이 붙어 정답 벡터와 코사인 0.79~0.82 로 어긋난다(W16 실측).

    ⚠ `model` 을 인자로 받는 이유: `scripts/encoder.py` 가 이 함수를 그대로 가져다
    쓴다(같은 규약이 두 곳에 적히면 갈라진다 — 실제로 갈라졌다). 그쪽은 모델을
    바꿔 가며 비교해야 해서 모듈 전역 MODEL 로는 안 된다.
    근거: [[record:embed-prefix-missing-m3-branch-2026-08-30]].
    """
    m = model.lower()
    if "bge-m3" in m:
        return text                      # m3 는 질의·문서 양쪽 다 맨몸이다
    if "e5" in m:
        # e5 는 **양쪽 모두** 필수다(모델카드 FAQ: 빠뜨리면 성능이 떨어진다).
        # 형태는 콜론 뒤 공백 하나 — "query: " 3토큰 · "passage: " 2토큰(실측).
        return f"{'query' if kind == 'query' else 'passage'}: {text}"
    if kind != "query":
        return text                      # bge-v1.5 passage 는 맨몸이다
    return f"{QUERY_PREFIX}{text}" if QUERY_PREFIX else text


def _prefix(text: str, kind: str) -> str:
    """이 프로세스가 든 모델(MODEL)에 규약을 적용한다."""
    return prefix(text, kind, MODEL)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="pending 을 한 번 비우고 종료")
    ap.add_argument("--interval", type=float, default=300.0,
                    help="상주 모드에서 pending 이 빌 때 대기 초")
    ap.add_argument("--encode-port", type=int, default=int(os.environ.get("VINC_EMBED_PORT", "0")),
                    help="쿼리 임베딩 루프백 서비스 포트(0=끔)")
    ap.add_argument("--serve-only", action="store_true",
                    help="인코딩 서비스만 띄우고 배치 루프는 돌지 않는다(vinc-encode 역할)")
    args = ap.parse_args()

    # 인코딩 주체를 여기서 한 번만 고른다. 원격이면 이 프로세스는 **모델을 안 든다**
    # — 허브에서 OOM 을 낸 646MB 가 통째로 사라진다.
    if REMOTE_ENCODE:
        if args.serve_only:
            print("[embed] --serve-only 와 VINC_EMBED_REMOTE 는 함께 쓸 수 없다 "
                  "— 서버가 자기 자신에게 위임하게 된다", file=sys.stderr, flush=True)
            return 2
        emb = _RemoteEncoder(REMOTE_ENCODE)
        print(f"[embed] 원격 인코더 사용 · {REMOTE_ENCODE} "
              f"(이 프로세스는 모델을 올리지 않는다)", flush=True)
    else:
        emb = _Embedder()

    # ⚠ 루프백 밖으로 여는데 토큰이 없으면 **인증 없는 인코더**다. 기동 시점에 막는다
    # — 요청이 올 때 막으면 그 사이에 이미 열려 있었다.
    if args.encode_port and ENCODE_BIND not in ("127.0.0.1", "localhost", "::1") \
            and not ENCODE_TOKEN:
        print(f"[embed] VINC_EMBED_BIND={ENCODE_BIND} 인데 VINC_EMBED_ENCODE_TOKEN 이 "
              "없다 — 인증 없는 인코더는 띄우지 않는다", file=sys.stderr, flush=True)
        return 2

    if args.serve_only:
        if not args.encode_port:
            print("[embed] --serve-only 에는 --encode-port(또는 VINC_EMBED_PORT)가 "
                  "필요하다", file=sys.stderr, flush=True)
            return 2
        serve_encode(emb, args.encode_port)
        print("[embed] serve-only — 배치 루프 없이 인코딩 요청만 받는다", flush=True)
        # 모델은 **첫 요청에서** 올라간다(지연 로드). 그래야 scale-to-zero 로 깨어난
        # 리비전이 프로브만 받고 도로 자는 동안 646MB 를 올리지 않는다.
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0
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
    # ⚠ `idle` 만으로는 "바쁘게 실패 중"을 못 잡는다. pending 이 있으면 idle 은
    # 매 라운드 0 으로 돌아가므로, 문서가 매번 실패해도(고장난 원격 인코더·손상된
    # 청크 등) 조용히 영원히 재시도만 하고 이 루프는 아무 요약도 안 남긴다.
    # 라운드마다 **하나라도 성공했는지**로 별도 카운터를 둔다. 이 값은 이
    # 프로세스가 사는 동안만 유효하다(재기동을 넘어서는 판정은 store 에 영속되는
    # `docstore.embed_model_state` 의 `last_applied_at`/`age_s` 가 한다: 거기가
    # "떴다 죽는 프로세스" 함정에서 자유로운 자리다). 정체를 봐도 여기서 워커를
    # 죽이거나 재기동하지 않는다. 소리만 낸다
    # ([[record:worker-heartbeat-separates-backlog-from-death]]).
    stall_rounds = 0
    paused_logged_at = None
    # 문서별 연속 실패 수와, 쉬게 한 문서가 다시 시도될 시각(monotonic). 이 프로세스가
    # 사는 동안만 유효하다 — 재기동하면 다시 시도하는 것이 맞다(이미지가 바뀌었을 수 있다).
    failures: dict[str, int] = {}
    benched: dict[str, float] = {}
    while True:
        # 운영자 스위치(`VINC_EMBED_APPLY_PAUSED`): 인코딩도 적용도 안 하고 백로그를
        # 그대로 둔다. 기동 때 한 번, 그 뒤로는 한 시간에 한 줄만 찍는다.
        # 근거: [[record:hub-segfault-loop-on-embedding-apply-2026-09-24]]
        if _apply_paused():
            now = time.monotonic()
            if paused_logged_at is None or now - paused_logged_at >= 3600:
                print("[embed] paused by VINC_EMBED_APPLY_PAUSED", flush=True)
                paused_logged_at = now
            if args.once:
                return 0
            time.sleep(args.interval)
            continue
        try:
            # 어떤 모델을 들었는지 **매 라운드 알려 준다**. 사이드카가 기록된 모델과
            # 비교해 다르면 전량 재임베딩을 건다(멱등 — 바뀐 첫 라운드만 일한다).
            # 모델을 바꿔도 needs_embed 가 저절로 켜지지 않아서 필요한 장치다.
            q = urllib.parse.quote(MODEL, safe="")
            now = time.monotonic()
            for d in [d for d, until in benched.items() if until <= now]:
                del benched[d]
            # 쉬는 문서도 pending 에는 그대로 나온다. 그만큼 더 받아야 나머지가 안 굶는다.
            lim = DOC_BATCH + len(benched)
            pending = _call(f"/embed/pending?limit={lim}&model={q}").get("chunks", [])
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

        progressed = False
        for doc_id, chunks in by_doc.items():
            if doc_id in benched:
                continue
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
                res = _call("/embed/apply", {"doc_id": doc_id, "embeddings": out,
                                             "encoder_runtime": (getattr(emb, "runtime", "")
                                                                 or ENCODER_RUNTIME)})
                print(f"[embed] doc={doc_id} chunks={res.get('embedded')} "
                      f"cleared={res.get('cleared')}", flush=True)
                failures.pop(doc_id, None)
                progressed = True
            except Exception as e:
                # 한 문서가 막혀도 나머지는 진행한다. 다음 라운드에 다시 잡힌다
                # (needs_embed 가 안 지워졌으므로 pending 에 계속 남는다).
                print(f"[embed] doc={doc_id} 실패: {e}", file=sys.stderr, flush=True)
                n = failures.get(doc_id, 0) + 1
                failures[doc_id] = n
                if n >= QUARANTINE_AFTER:
                    # 쉬게 한다. 지우지 않는다 — needs_embed 는 남고, 쉬는 시간이 지나면
                    # 다시 시도한다. 그동안 인코더는 다른 문서와 질의에 쓰인다.
                    benched[doc_id] = time.monotonic() + QUARANTINE_S
                    failures.pop(doc_id, None)
                    print(f"[embed] doc={doc_id} {n}회 연속 실패 — "
                          f"{QUARANTINE_S / 3600:g}시간 쉬었다가 다시 시도한다",
                          file=sys.stderr, flush=True)

        if progressed:
            stall_rounds = 0
        else:
            stall_rounds += 1
            if heartbeat_rounds > 0 and stall_rounds % heartbeat_rounds == 0:
                print(f"[embed] stall 경고 · {stall_rounds} 라운드 연속 적용 실패 "
                      f"(pending={len(pending)}, doc={len(by_doc)}건). 워커는 살아 "
                      "있지만 앞으로 못 간다", file=sys.stderr, flush=True)

        if args.once and len(by_doc) < DOC_BATCH:
            return 0

        if not progressed:
            # 무진행 라운드는 물러났다 온다. 백오프 없이 돌면 같은 실패를 전속력으로
            # 재시도하며 사이드카를 두들기고 stderr 를 쏟는다(정체인데 뜨거운 루프 —
            # 실제로 스톨 테스트가 이 자리 없이는 무한 루프로 MemoryError 났다).
            # 진행이 있으면 안 쉰다: 백로그는 전속력으로 빼는 게 맞다.
            time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
