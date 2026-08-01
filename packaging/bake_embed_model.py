r"""빌드 타임 모델 굽기 — e5 fp32 를 받아 reduce_range int8 로 양자화해 이미지에 싣는다.

**왜 별도 파일인가**(이 파일이 존재하는 이유 — 인라인으로 되돌리지 말 것):
전에는 이 내용이 `Dockerfile.hub` 안의 heredoc(`python - <<'PY'`)이었다. 두 가지가
연달아 터졌다.

1. ACR Tasks 는 빌드 전에 Dockerfile 을 자체 파서로 훑는 "dependency scanning" 을
   도는데, 그 파서가 heredoc 본문을 구분하지 못해 `from X import Y` 를 대소문자
   무시한 `FROM` 지시자로 읽고 **빌드를 3초 만에 죽였다**(run dek, 2026-08-01).
2. 그것을 피해도 heredoc 자체가 안 먹었다. ACR 의 클래식 빌더에서
   `RUN a && python - <<'PY' && b` 는 줄 이음(`\`)으로 한 줄이 되고, 셸이 heredoc
   본문을 못 받아 `python -` 이 **빈 stdin 으로 즉시 종료**한다. 그래서 빌드는
   "성공"하는데 모델은 안 구워졌다 — 로그에 pip install 뒤 곧바로 pip uninstall 만
   찍히고, 이미지가 120.8MB → 126MB 로 5MB 만 늘었다(정상이면 약 400MB)(run dem).

**②가 진짜 위험했던 이유**: 빌드가 초록이라 아무도 몰랐고, 런타임에 워커가
`VINC_EMBED_MODEL_PATH` 가 없는 것을 보고 fp32(1,058MB)를 받아 1024MiB 컨테이너에
올리려다 **OOM 으로 MCP 까지 통째로 죽는다**(embed_worker.py `_model_path`).

그래서 ① heredoc 을 쓰지 않고 ② 아래에서 결과를 **검증하고 실패시킨다**.
조용히 건너뛰는 실패는 다시는 만들지 않는다.
"""

import os
import shutil
import sys

import huggingface_hub as hf
import onnxruntime.quantization as ortq

OUT = "/srv/vinc/models/e5-int8-rr.onnx"
TOK = "/srv/vinc/models/tokenizer.json"
MODEL = os.environ.get("VINC_EMBED_MODEL", "intfloat/multilingual-e5-base")

# 공식 int8(`model_qint8_avx512_vnni.onnx`)은 U8S8+per_channel+reduce_range=False 라
# non-VNNI CPU 에서 포화가 나 **에러 없이 품질만** 떨어진다(실측 한국어 nDCG
# 0.670 → 0.472). reduce_range=True 는 가중치를 7비트로 낮춰 포화를 산술적으로
# 불가능하게 만든다 — 그래서 공식본을 쓰지 않고 직접 굽는다.
fp32 = hf.hf_hub_download(MODEL, "onnx/model.onnx")
ortq.quantize_dynamic(
    fp32,
    OUT,
    weight_type=ortq.QuantType.QInt8,
    per_channel=True,
    reduce_range=True,
    extra_options={"MatMulConstBOnly": True},
)

# 토크나이저(17MB)도 함께 굽는다 — 런타임 네트워크 의존을 남기지 않는다.
shutil.copy(hf.hf_hub_download(MODEL, "tokenizer.json"), TOK)

# 결과 검증 — 조용히 건너뛴 실패를 빌드에서 잡는다(위 §② 재발 방지).
# 실측 279MB. 200MB 미만이면 무언가 잘못된 것이므로 빌드를 세운다.
size = os.path.getsize(OUT)
if size < 200 * 1024 * 1024:
    sys.exit(f"FATAL: 구워진 모델이 너무 작다({size}B) — 양자화가 제대로 안 됐다")
if not os.path.getsize(TOK):
    sys.exit("FATAL: 토크나이저가 비어 있다")

print(f"baked: model={size}B tokenizer={os.path.getsize(TOK)}B", flush=True)
