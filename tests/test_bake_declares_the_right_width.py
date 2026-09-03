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
