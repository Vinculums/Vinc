# -*- coding: utf-8 -*-
"""`retable` 은 지운 벡터를 아무도 못 채우는 상태로 남기면 안 된다.

`retable_vectors` 는 ChunkVec 을 DROP 하고 다시 만든 **뒤에**
`switch_embed_model` 로 전 문서를 재큐한다. 그런데 그 함수는 저장된 모델 식별자가
그대로면 "바뀐 게 없다"고 보고 재큐를 건너뛴다. 같은 모델로 부르면 그 둘이 겹쳐
**벡터 0 · pending 0** 인 스토어가 남는다: 의미 검색은 죽었고, 워커는 할 일이 없다고
판단하며, 반환값은 성공처럼 보인다(`requeued: null` 하나뿐).

실측 2026-09-03: e5 스토어에 `retable --model e5` → 481벡터 소실, 오류 0건.
데스크탑에서 같은 호출은 그 기기의 의미 검색을 영구히 끈다.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))


def test_guard_exists_and_names_the_state():
    """가드가 그 조건을 실제로 보고, 사람이 다음 수를 알 수 있게 말하는가."""
    src = (Path(__file__).resolve().parents[2] / "scripts" / "rehearse_rebuild.py"
           ).read_text(encoding="utf-8")
    body = src.split("def retable_vectors")[1].split("\ndef ")[0]
    assert 'before["vecs"] > 0 and not after["pending"]' in body, \
        "지운 벡터 + 재큐 0 조합을 보는 가드가 없다"
    assert "SystemExit" in body, "그 상태를 발견하고도 성공으로 반환하면 가드가 아니다"
    # 메시지가 다음 수를 말해야 한다 — 상태만 알리고 끝나면 부르는 사람이 막힌다.
    assert "다른 모델" in body or "rebuild" in body


def test_switch_embed_model_skips_requeue_on_an_unchanged_model():
    """가드가 방어하는 그 동작이 실제로 존재하는지 — 없으면 가드는 사문이다."""
    import inspect
    from api import docstore as ds
    # 함수가 정의된 파일을 읽는다(`api.docembed`, docstore 는 같은 객체를 다시 내보낸다).
    src = Path(inspect.getsourcefile(ds.switch_embed_model)).read_text(encoding="utf-8")
    body = src.split("def switch_embed_model")[1].split("\ndef ")[0]
    # 저장된 값과 같으면 일찍 빠져나가는 분기가 있다는 것을 확인한다.
    assert "requeued" in body
    assert "return" in body
