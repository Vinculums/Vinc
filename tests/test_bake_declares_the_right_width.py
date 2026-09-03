# -*- coding: utf-8 -*-
"""Dockerfile 이 선언한 임베딩 폭이 정본 표와 같은가.

굽기는 이제 구운 모델을 실제로 한 번 돌려 출력 폭을 재고 `VINC_EMBED_DIM` 과
대조한다(`packaging/bake_embed_model.py::verify_embedding_width`). 그 검사는 **빌드
안에서 진짜 아티팩트로** 돌기 때문에 강하지만, 여기서는 못 돌린다 — 이미지가 있어야
하고 onnxruntime 이 있어야 한다.

그래서 이 테스트는 다른 것을 지킨다: **선언이 서로 어긋나는 것.** 모델을 bge-m3 로
바꾸면서 폭을 768 로 두면 빌드가 설 텐데, 그 실패는 ACR 빌드까지 가야 보인다. 선언
둘만 대조하면 그 왕복 전에 PR 에서 걸린다. 그리고 이 검사는 **어디서나 돈다** —
ML 의존성이 필요 없다.

⚠ 폭을 여기 상수로 적지 않는다. `store/config.py::EMBEDDING_DIM_BY_MODEL` 이 정본이고,
그것을 복사하는 순간 복사본이 드리프트한다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from store.config import EMBEDDING_DIM_BY_MODEL   # noqa: E402

DOCKERFILE = _ROOT / "Dockerfile.hub"


def _env(name: str) -> str:
    """`ENV NAME=value` 한 줄을 읽는다. 없으면 테스트를 실패시킨다 — 못 찾은 것을
    '검사할 것 없음'으로 강등하면 이 테스트가 조용히 아무것도 안 본다."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    m = re.search(rf"^ENV\s+{re.escape(name)}=(.+)$", text, re.MULTILINE)
    assert m, f"Dockerfile.hub 에 ENV {name} 이 없다"
    return m.group(1).strip()


def test_선언한_폭이_정본_표와_같다():
    model = _env("VINC_EMBED_MODEL")
    declared = int(_env("VINC_EMBED_DIM"))
    assert model in EMBEDDING_DIM_BY_MODEL, (
        f"{model!r} 이 EMBEDDING_DIM_BY_MODEL 에 없다 — 표에 먼저 넣을 것"
    )
    assert declared == EMBEDDING_DIM_BY_MODEL[model], (
        f"Dockerfile 이 {model} 을 {declared} 차원으로 선언했는데 정본 표는 "
        f"{EMBEDDING_DIM_BY_MODEL[model]} 이다. 모델을 바꿀 때 폭을 같이 안 바꾼 것이다"
    )


def test_아티팩트_경로가_모델과_같은_계열이다():
    """세 줄이 함께 움직이는지 본다. 경로 규약의 정본 대조는
    `tests/test_embed_worker.py` 가 하고, 여기서는 **모델을 바꾸며 경로만 두고 간**
    가장 흔한 실수를 잡는다."""
    model = _env("VINC_EMBED_MODEL")
    path = _env("VINC_EMBED_MODEL_PATH")
    stem = Path(path).name
    family = model.split("/")[-1].replace("multilingual-", "")
    head = family.split("-")[0]                     # e5 / bge
    assert head in stem, (
        f"모델은 {model} 인데 아티팩트 경로는 {stem} 다 — 모델만 바꾸고 경로를 두면 "
        f"런타임이 옛 아티팩트를 집는다"
    )


def test_심링크_금지가_huggingface_import_보다_앞에_선다():
    """`HF_HUB_DISABLE_SYMLINKS` 는 huggingface_hub 이 **import 될 때** 읽힌다.
    import 뒤로 밀리면 조용히 아무 효과가 없고, 2GB 넘는 모델의 굽기가 다시
    'should be stored in …, but it is a symbolic link' 로 죽는다(실측 2026-09-03).

    순서가 계약이므로 순서를 고정한다 — 주석만으로는 다음 사람이 import 를 정리하다
    위로 올릴 수 있다."""
    src = (_ROOT / "packaging" / "bake_embed_model.py").read_text(encoding="utf-8")
    env_at = src.find("HF_HUB_DISABLE_SYMLINKS")
    imp_at = src.find("import huggingface_hub")
    assert env_at != -1, "HF_HUB_DISABLE_SYMLINKS 설정이 사라졌다"
    assert imp_at != -1, "huggingface_hub import 를 못 찾았다 — 이 테스트가 낡았다"
    assert env_at < imp_at, (
        "HF_HUB_DISABLE_SYMLINKS 를 huggingface_hub import **뒤**에 세우고 있다 — "
        "그 시점에는 이미 읽힌 뒤라 효과가 없다"
    )


def test_굽기가_폭_검사를_실제로_부른다():
    """검사를 만들어 두고 부르지 않는 것이 오늘의 주제였다. 호출을 고정한다."""
    src = (_ROOT / "packaging" / "bake_embed_model.py").read_text(encoding="utf-8")
    assert "def verify_embedding_width" in src
    assert re.search(r"^width = verify_embedding_width\(", src, re.MULTILINE), (
        "verify_embedding_width 가 정의만 되어 있고 굽기 흐름에서 안 불린다"
    )


@pytest.mark.parametrize("name", ["VINC_EMBED_MODEL", "VINC_EMBED_MODEL_PATH",
                                  "VINC_EMBED_DIM"])
def test_세_줄이_모두_있다(name):
    assert _env(name)


# ── 굽기는 러너, 검증은 이미지 (2026-09-03) ──────────────────────────────────
#
# `quantize_dynamic` 만 ACR 밖으로 나갔다. 그때 잃기 쉬운 성질이 **"검증한 바이트 =
# 실은 바이트"** 다 — 검증을 이미지 밖으로 같이 내보내면 조용히 사라지고, 아무 테스트도
# 안 떨어진다. 그래서 여기서 고정한다.

def test_이미지가_COPY_한_바이트를_스스로_검증한다():
    """`COPY` 는 `RUN` 처럼 빨개지지 못하고 잘린 파일은 조용히 성공한다. 그 결과는
    런타임에서 빌드 실패가 아니라 영구 stall 이다(로드 예외가 문서별 except 에 삼켜지고
    /health 는 200 을 유지한다). 그래서 이미지 안에서 한 번 열어 봐야 한다."""
    df = DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY models/" in df, "아티팩트를 컨텍스트에서 안 가져온다"
    assert re.search(r"^RUN python /tmp/bake_embed_model\.py", df, re.MULTILINE), (
        "COPY 만 하고 검증 RUN 이 없다 — 실은 바이트를 아무도 안 연다"
    )


def test_이미지_빌드가_양자화_의존성을_다시_들이지_않는다():
    """`onnx` 를 이미지에서 뺀 것이 이 변경의 덤이다. 다시 들어오면 굽기가 이미지로
    돌아왔다는 뜻이고, 그러면 bge-m3 에서 다시 OOM 이다."""
    df = DOCKERFILE.read_text(encoding="utf-8")
    bake_layer = df[df.index("COPY models/"):]
    assert "pip install" not in bake_layer, (
        f"굽기 레이어에 pip install 이 돌아왔다:\n{bake_layer[:400]}"
    )


def test_무거운_import_가_굽는_분기_안에_있다():
    """`huggingface_hub` 과 `onnxruntime.quantization` 이 모듈 최상단에 있으면 검증
    전용 실행(이미지 빌드)이 ImportError 로 죽는다 — 거기엔 `onnx` 가 없다."""
    src = (_ROOT / "packaging" / "bake_embed_model.py").read_text(encoding="utf-8")
    for mod in ("import huggingface_hub", "import onnxruntime.quantization"):
        for line in src.splitlines():
            if line.strip().startswith(mod):
                assert line.startswith(" "), (
                    f"{mod!r} 이 최상단에 있다 — 굽는 분기 안으로 들여쓸 것"
                )


def test_models_가_빌드_컨텍스트에서_제외되지_않는다():
    """`.dockerignore` 에 넣으면 `COPY models/` 가 죽는다. 여기 없는 것이 의도라는
    사실이 어딘가에 적혀 있어야 다음 사람이 '누락'으로 보고 더하지 않는다."""
    di = (_ROOT / ".dockerignore").read_text(encoding="utf-8")
    ignored = [ln.strip() for ln in di.splitlines()
               if ln.strip() and not ln.strip().startswith("#")]
    assert not any(ln.rstrip("/") == "models" for ln in ignored), (
        ".dockerignore 가 models/ 를 뺀다 — COPY models/ 가 죽는다"
    )
    assert "models/" in di, "제외하지 않는다는 것이 의도임을 적어 둘 것"
