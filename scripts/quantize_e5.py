#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""e5 fp32 → **reduce_range int8** 자체 양자화. 공식 int8 을 쓰지 않는 이유가 전부다.

multilingual-e5-base 의 공식 int8 은 `model_qint8_avx512_vnni.onnx` **한 종뿐**이고
U8S8 + per_channel + `reduce_range=False` 조합이다. ORT 문서가 non-VNNI CPU 에서
VPMADDUBSW 의 int16 누산 포화를 경고하는 바로 그 설정이라, VNNI 가 없으면 **에러 없이
품질만** 떨어진다. 실측(AVX2 기기, 한영 60질의 · nDCG@10):

    | | 영어 | 한국어 | 전체 |
    |---|---:|---:|---:|
    | e5 fp32 | 0.636 | 0.670 | 0.658 |
    | e5 int8 공식(rr=False) | 0.574 | **0.472** | 0.508 |
    | e5 int8 자체(rr=True) | 0.556 | **0.669** | 0.630 |

한국어 손실 0.198 이 전부 포화 탓이었고 `reduce_range=True`(가중치 7비트 → 포화가
산술적으로 불가)로 되돌아온다. VNNI 가 있어도 reduce_range 는 무해하므로 **CPU 를 확인할
필요조차 없다** — 어느 쪽이든 자체 빌드가 정답이다.

🔴 **런타임 양자화는 못 한다.** e5 의 임베딩 행렬 하나가 192M float(732MB)이고 원본까지
메모리에 올라가 피크가 1.5GB 를 넘는다. 1024MiB 컨테이너에서는 불가능하다. 그래서
프로덕션은 빌드 타임에 굽고(`packaging/bake_embed_model.py` · `Dockerfile.hub`) 런타임은
그 파일을 쓴다. 이 스크립트는 **실험용 로컬 빌드**다 — 굽는 쪽 정본은 그 파일이다.

모델 평가 스파이크(W10)부터는 e5 계열 밖도 굽는다 — `--model` 로 e5-large·bge-m3 를
받으면 같은 rr=True 레시피로 양자화한다. 2GB 를 넘는 fp32(e5-large·bge-m3)는 ONNX 가
가중치를 `model.onnx_data` **별도 파일**로 내보내므로, 그래프 파일만 받으면 로드가
터진다 — `onnx/` 밑의 동반 파일(외부 데이터·Constant 블롭)까지 함께 받는다.

사용:
    python scripts/quantize_e5.py --out .cache/embed/e5-int8-rr.onnx
    python scripts/quantize_e5.py --model BAAI/bge-m3 --out .cache/embed/m3-int8-rr.onnx
"""
from __future__ import annotations

import argparse
import os
import sys

#: 실측 크기(바이트). 다운로드가 조용히 잘려도 그대로 재면 엉뚱한 수치가 나온다 —
#: 이 세션에서 실제로 한 파일이 278,686,411 → 29,360,128 로 잘려 있었다.
#: e5 계열의 정본 크기다. 더 작은 모델(리랭커 118M 은 int8 이 ~120MB)에는 --min-size 로
#: 그 모델의 정본을 준다. 상수로 두면 작은 모델은 양자화에 성공하고도 FATAL 로 죽는다.
_EXPECT_INT8_MIN = 200 * 1024 * 1024


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="intfloat/multilingual-e5-base")
    ap.add_argument("--out", default=".cache/embed/e5-int8-rr.onnx")
    ap.add_argument("--cache", default=os.environ.get("VINC_EMBED_CACHE", ".cache/embed"))
    ap.add_argument("--keep-fp32", action="store_true",
                    help="fp32 원본(약 1GB)을 지우지 않는다(fp32 대조 실험용)")
    ap.add_argument("--min-size", type=int, default=_EXPECT_INT8_MIN,
                    help="이 크기 미만이면 양자화가 안 된 것으로 본다(모델별 정본 크기)")
    args = ap.parse_args()

    from huggingface_hub import hf_hub_download, list_repo_files
    from onnxruntime.quantization import QuantType, quantize_dynamic

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    if os.path.exists(args.out) and os.path.getsize(args.out) >= args.min_size:
        print(f"이미 있다 — {args.out} ({os.path.getsize(args.out)}B)")
        return 0

    print(f"fp32 내려받는 중 ({args.model})...", flush=True)
    fp32 = hf_hub_download(args.model, "onnx/model.onnx", cache_dir=args.cache)
    # 2GB 초과 모델은 가중치가 그래프 밖 별도 파일이다(e5-large·bge-m3 의
    # model.onnx_data, m3 의 Constant_* 블롭). 같은 스냅샷 디렉터리에 나란히 받아야
    # onnx.load 가 상대경로로 찾는다. 대안 .onnx(공식 int8·O4)과 토크나이저류는 제외.
    for f in list_repo_files(args.model):
        if (f.startswith("onnx/") and f != "onnx/model.onnx"
                and not f.endswith((".onnx", ".json", ".model"))):
            hf_hub_download(args.model, f, cache_dir=args.cache)

    tmp = args.out + ".part"
    print("int8 양자화(reduce_range=True) 중 — 메모리 1.5GB+ 필요, 약 31초", flush=True)
    quantize_dynamic(fp32, tmp, weight_type=QuantType.QInt8, per_channel=True,
                     reduce_range=True, extra_options={"MatMulConstBOnly": True})
    os.replace(tmp, args.out)             # 원자적 — 중간에 죽어도 반쪽이 안 남는다

    size = os.path.getsize(args.out)
    if size < args.min_size:
        print(f"FATAL: 결과가 너무 작다({size}B) — 양자화가 제대로 안 됐다", file=sys.stderr)
        return 1
    if not args.keep_fp32:
        # 외부 데이터 동반 파일(model.onnx_data 등)이 그래프보다 크다 — 같이 지운다.
        d = os.path.dirname(fp32)
        for name in ("model.onnx", "model.onnx_data"):
            try:
                os.remove(os.path.join(d, name))
            except OSError:
                pass
    print(f"완료 → {args.out} ({size}B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
