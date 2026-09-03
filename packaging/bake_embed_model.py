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

# ⚠ **이 줄은 `huggingface_hub` import 보다 앞에 있어야 한다.** 그 패키지는 이 값을
# `constants` 모듈이 **import 되는 시점에** 한 번 읽는다(`_is_true(os.environ.get(...))`).
# 뒤로 옮기면 조용히 아무 효과가 없다.
#
# 왜 필요한가(실측 2026-09-03, bake-probe run 33714831308, ACR 52초):
# hf 캐시는 `blobs/` 의 실파일을 `snapshots/` 에서 **심링크**로 가리킨다. 2GB 를 넘는
# 모델은 가중치가 `model.onnx_data` 별도 파일로 오는데, onnx 의 외부 데이터 로더는
# 심링크를 **거부한다**(경로 traversal 방어):
#
#   ValidationError: Data of TensorProto (…word_embeddings.weight) should be stored in
#   …/onnx/model.onnx_data, but it is a symbolic link.
#
# 즉 동반 파일을 올바른 자리에 받아 놓고도 로드에서 막힌다. 이 값이 켜지면 hf 가
# 심링크 대신 **복사**한다. e5(단일 파일, 외부 데이터 없음)에는 영향이 없다 —
# 그쪽은 그래프 파일을 직접 열므로 이 검사를 아예 안 탄다.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

import huggingface_hub as hf  # noqa: E402  (위 환경변수가 먼저 서야 한다)
import onnxruntime.quantization as ortq  # noqa: E402

# 🔴 **런타임이 찾는 바로 그 경로에 굽는다.** 굽는 이름과 찾는 이름을 각자 적으면
# 갈라지고, 갈라진 순간 런타임은 "구운 게 없다"고 판정해 조용히 fp32(e5 1.06GB ·
# bge-m3 2.27GB)를 내려받다가 OOM 으로 컨테이너(=MCP 포함)를 죽인다. 그래서 둘 다
# `VINC_EMBED_MODEL_PATH` 하나를 본다 — Dockerfile.hub 의 ENV 가 유일한 정본이고,
# 그 값이 모델과 맞는지는 `packaging/embed_worker.py::artifact_name` 규약을 대조하는
# 테스트(tests/test_embed_worker.py)가 CI 에서 지킨다.
OUT = os.environ.get("VINC_EMBED_MODEL_PATH", "/srv/vinc/models/e5-int8-rr.onnx")
TOK = os.path.join(os.path.dirname(OUT), "tokenizer.json")
MODEL = os.environ.get("VINC_EMBED_MODEL", "intfloat/multilingual-e5-base")
#: 이 모델이 내야 하는 벡터 폭. `Dockerfile.hub` 의 ENV 가 정본이고, 그 값이
#: `store/config.py::EMBEDDING_DIM_BY_MODEL` 과 어긋나지 않는지는 CI 테스트가 본다
#: (tests/test_bake_declares_the_right_width.py). 여기서 표를 복사하지 않는 이유는
#: 복사본이 드리프트하기 때문이고, ENV 로 받는 이유는 이 파일 하나만 이미지로
#: 복사되어 `store.config` 를 import 할 수 없기 때문이다.
DIM = int(os.environ.get("VINC_EMBED_DIM", "768"))


def verify_embedding_width(model_path: str, tokenizer_path: str, expected: int) -> int:
    """구운 artifact 를 **실제로 열어 한 번 돌리고** 출력 폭을 잰다.

    크기 하한이 답할 수 없는 질문에 답한다: 이 파일이 로드되는가, 그리고 선언한 폭을
    내는가. 모델 교체에서 이것이 결정적인 이유는 차원이 함께 바뀌기 때문이다 —
    e5 는 768, bge-m3 는 1024(`store/config.py`). 폭이 어긋난 벡터를 스토어에 쓰면
    `ARRAY(768)` 열에서 캐스팅 오류가 나는데, 그때는 **이미 옛 벡터를 지운 뒤**다
    (`scripts/rehearse_rebuild.py` 머리말). 빌드에서 잡는 편이 훨씬 싸다.

    onnxruntime·tokenizers·numpy 는 런타임 레이어에 이미 있다(`Dockerfile.hub`) —
    이 검사를 위해 새로 까는 것이 없다.
    """
    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer

    enc = Tokenizer.from_file(tokenizer_path).encode("dimension probe")
    ids = np.array([enc.ids], dtype=np.int64)
    supply = {"input_ids": ids,
              "attention_mask": np.array([enc.attention_mask], dtype=np.int64),
              "token_type_ids": np.zeros_like(ids)}
    sess = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
    feed = {}
    for spec in sess.get_inputs():
        if spec.name not in supply:
            # 모르는 입력을 0 으로 채워 넘기지 않는다 — 그러면 폭은 나오는데 그 값이
            # 무엇인지 아무도 모른다. 답할 수 없으면 답하지 않는다.
            sys.exit(f"FATAL: 구운 모델이 모르는 입력을 요구한다: {spec.name!r}")
        feed[spec.name] = supply[spec.name]
    got = int(sess.run(None, feed)[0].shape[-1])
    if got != expected:
        sys.exit(
            f"FATAL: 구운 모델의 출력 폭이 {got} 인데 VINC_EMBED_DIM 은 {expected} 다. "
            f"모델({MODEL})과 선언 폭이 어긋났다 — Dockerfile.hub 의 "
            f"VINC_EMBED_MODEL·VINC_EMBED_DIM 를 함께 볼 것"
        )
    return got

# 공식 int8(`model_qint8_avx512_vnni.onnx`)은 U8S8+per_channel+reduce_range=False 라
# non-VNNI CPU 에서 포화가 나 **에러 없이 품질만** 떨어진다(실측 한국어 nDCG
# 0.670 → 0.472). reduce_range=True 는 가중치를 7비트로 낮춰 포화를 산술적으로
# 불가능하게 만든다 — 그래서 공식본을 쓰지 않고 직접 굽는다.
os.makedirs(os.path.dirname(OUT), exist_ok=True)
fp32 = hf.hf_hub_download(MODEL, "onnx/model.onnx")
# 2GB 를 넘는 fp32(e5-large·bge-m3)는 가중치가 `model.onnx_data` 등 **별도 파일**로
# 나온다. 그래프 파일만 받으면 quantize 가 로드에서 터진다 — 같은 스냅샷 디렉터리에
# 나란히 받아야 onnx 가 상대경로로 찾는다. 대안 .onnx(공식 int8·O4)과 설정류는 제외.
# (같은 규칙이 `scripts/quantize_e5.py` 에도 있다. 그쪽은 실험용 로컬 빌드다.)
for _f in hf.list_repo_files(MODEL):
    if (_f.startswith("onnx/") and _f != "onnx/model.onnx"
            and not _f.endswith((".onnx", ".json", ".model"))):
        hf.hf_hub_download(MODEL, _f)
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
#
# ⚠ **크기 하한만으로는 부족하다.** 이 문턱은 e5 int8-rr 의 실측 279MB 에 맞춘 값이라
# 그보다 훨씬 큰 모델(bge-m3)에서는 **잘린 파일도 넘어간다** — 채택하려는 쪽에서 더
# 약하다는 뜻이다. 그리고 크기는 "열리는가·폭이 맞는가" 를 아예 묻지 않는다.
# 이 파일이 존재하는 이유가 무음 굽기 실패였는데(위 §②), 그때의 처방은 **일어난
# 고장**을 잡았지 그 부류를 잡지 못했다. 아래 폭 검사가 그 자리를 메운다.
size = os.path.getsize(OUT)
if size < 200 * 1024 * 1024:
    sys.exit(f"FATAL: 구워진 모델이 너무 작다({size}B) — 양자화가 제대로 안 됐다")
if not os.path.getsize(TOK):
    sys.exit("FATAL: 토크나이저가 비어 있다")

width = verify_embedding_width(OUT, TOK, DIM)
print(f"baked: model={size}B tokenizer={os.path.getsize(TOK)}B width={width}", flush=True)
