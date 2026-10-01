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

# ⚠ `huggingface_hub` 과 `onnxruntime.quantization` 은 여기서 import 하지 않는다 —
# **굽는 분기 안에서만** 든다. 검증 전용으로 도는 이미지 빌드에는 `onnx` 가 없고
# (굽지 않으므로 설치할 이유가 없다), 여기서 올리면 그 실행이 ImportError 로 죽는다.
# 위의 `HF_HUB_DISABLE_SYMLINKS` 는 그 지연 import 보다도 앞이므로 계약이 더 확실해진다.

# 🔴 **런타임이 찾는 바로 그 경로에 굽는다.** 굽는 이름과 찾는 이름을 각자 적으면
# 갈라지고, 갈라진 순간 런타임은 "구운 게 없다"고 판정해 조용히 fp32(e5 1.06GB ·
# bge-m3 2.27GB)를 내려받다가 OOM 으로 컨테이너(=MCP 포함)를 죽인다. 그래서 둘 다
# `VINC_EMBED_MODEL_PATH` 하나를 본다 — Dockerfile.hub 의 ENV 가 유일한 정본이고,
# 그 값이 모델과 맞는지는 `packaging/embed_worker.py::artifact_name` 규약을 대조하는
# 테스트(tests/rag/test_embed_worker.py)가 CI 에서 지킨다.
OUT = os.environ.get("VINC_EMBED_MODEL_PATH", "/srv/vinc/models/e5-int8-rr.onnx")
TOK = os.path.join(os.path.dirname(OUT), "tokenizer.json")
MODEL = os.environ.get("VINC_EMBED_MODEL", "intfloat/multilingual-e5-base")
#: 이 모델이 내야 하는 벡터 폭. `Dockerfile.hub` 의 ENV 가 정본이고, 그 값이
#: `store/config.py::EMBEDDING_DIM_BY_MODEL` 과 어긋나지 않는지는 CI 테스트가 본다
#: (tests/rag/test_bake_declares_the_right_width.py). 여기서 표를 복사하지 않는 이유는
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
# ── 굽기는 조건부, 검증은 무조건 ────────────────────────────────────────────
#
# 이 파일은 **두 자리에서** 돌고, 그 둘이 하는 일이 다르다:
#
#   ① 러너(hub-deploy 의 ubuntu-latest) — 아티팩트가 없으므로 **굽는다.**
#   ② 이미지 빌드(`RUN`) — `COPY` 로 이미 와 있으므로 **검증만 한다.**
#
# 굽기가 러너로 나간 이유는 메모리다. ACR 빌드 에이전트는 fp32 2.27GB 를 **다 받고
# 나서** `quantize_dynamic` 에서 OOM 으로 죽는다(실측 2026-09-03, exit 137,
# bake-probe run 33716383547). Basic 티어라 에이전트에 메모리를 더 줄 수 없다
# (전용 에이전트 풀은 Premium 기능).
#
# 🔴 **그래도 검증은 이미지 안에 남는다.** 그것이 이 구조의 요점이다. `COPY` 는
# `RUN` 처럼 빨개지지 못하고, 잘린 파일은 조용히 성공한다(이 리포 실화:
# `scripts/quantize_e5.py` 가 278,686,411 → 29,360,128 무음 절단을 기록한다).
# 그리고 그 결과는 런타임에서 빌드 실패가 아니라 **영구 stall** 로 나온다 —
# `_load()` 가 문서별 `except` 안에서 지연 호출돼 로드 예외가 문서당 stderr 한 줄로
# 삼켜지고 영원히 재시도된다. `/health` 는 200 을 유지한다.
#
# 아래 `verify_embedding_width` 는 **시스템 전체에서 아티팩트를 실제로 열어 돌려
# 보는 유일한 자리**다. 런타임이 하는 것은 basename 문자열 비교 하나뿐이고 그 양쪽이
# 둘 다 Dockerfile ENV 라 파일을 한 바이트도 안 읽는다(`VINC_EMBED_DIM` 은 런타임에서
# 아예 안 읽힌다). 그래서 이 검사를 이미지 밖으로 내보내면 **검증한 바이트와 실은
# 바이트가 갈린다.** 여기 남겨 둔 것이 그 성질을 되사는 값 전부다.
os.makedirs(os.path.dirname(OUT), exist_ok=True)

if os.path.exists(OUT) and os.path.exists(TOK):
    print(f"skip-bake: 이미 있는 아티팩트를 검증만 한다 ({OUT})", flush=True)
else:
    # 무거운 import 는 이 분기 안에서만 한다 — 이미지에는 `onnx` 가 없다(굽지 않으므로
    # 설치할 이유가 없어졌다). 위로 올리면 검증 전용 실행이 ImportError 로 죽는다.
    import huggingface_hub as hf   # noqa: E402
    import onnxruntime.quantization as ortq   # noqa: E402

    # 공식 int8(`model_qint8_avx512_vnni.onnx`)은 U8S8+per_channel+reduce_range=False 라
    # non-VNNI CPU 에서 포화가 나 **에러 없이 품질만** 떨어진다(실측 한국어 nDCG
    # 0.670 → 0.472). reduce_range=True 는 가중치를 7비트로 낮춰 포화를 산술적으로
    # 불가능하게 만든다 — 그래서 공식본을 쓰지 않고 직접 굽는다.
    fp32 = hf.hf_hub_download(MODEL, "onnx/model.onnx")
    src_bytes = os.path.getsize(fp32)
    # 2GB 를 넘는 fp32(e5-large·bge-m3)는 가중치가 `model.onnx_data` 등 **별도 파일**로
    # 나온다. 그래프 파일만 받으면 quantize 가 로드에서 터진다 — 같은 스냅샷 디렉터리에
    # 나란히 받아야 onnx 가 상대경로로 찾는다. 대안 .onnx(공식 int8·O4)과 설정류는 제외.
    # (같은 규칙이 `scripts/quantize_e5.py` 에도 있다. 그쪽은 실험용 로컬 빌드다.)
    for _f in hf.list_repo_files(MODEL):
        if (_f.startswith("onnx/") and _f != "onnx/model.onnx"
                and not _f.endswith((".onnx", ".json", ".model"))):
            src_bytes += os.path.getsize(hf.hf_hub_download(MODEL, _f))
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

    # 크기 하한을 **원본에서 유도한다.** 예전에는 200MB 리터럴이었는데 그것은 e5
    # int8-rr 의 실측 279MB 에 맞춘 값이라, 훨씬 큰 모델에서는 잘린 파일도 넘어갔다 —
    # 채택하려는 쪽에서 더 약했다. int8 은 fp32 의 1/4 근처이고(e5 실측 26%),
    # 0.15 는 그 아래로 넉넉히 둔 여유다. 선언이 아니라 유도라 모델이 바뀌어도 따라온다.
    floor = int(src_bytes * 0.15)
    got = os.path.getsize(OUT)
    if got < floor:
        sys.exit(f"FATAL: 구워진 모델이 너무 작다({got}B < {floor}B, 원본 {src_bytes}B) "
                 f"— 양자화가 제대로 안 됐다")

# ── 여기부터는 어느 자리에서 돌든 똑같이 한다 ────────────────────────────────
size = os.path.getsize(OUT)
if not os.path.getsize(TOK):
    sys.exit("FATAL: 토크나이저가 비어 있다")

width = verify_embedding_width(OUT, TOK, DIM)
print(f"baked: model={size}B tokenizer={os.path.getsize(TOK)}B width={width}", flush=True)
