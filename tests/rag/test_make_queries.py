# -*- coding: utf-8 -*-
"""scripts/make_queries.py 리팩터 회귀 — collect/select 로 쪼갠 뒤에도 커밋된
fixtures/evalset.json 을 그대로 재생산하는가(vinc.plan.reinforcement-points 우선순위
4: make_queries_from_store.py 가 select() 를 공유하기 전에, 파일트리 경로가 안
바뀌었다는 것부터 못박는다)."""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import unittest

_SRC = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "make_queries.py"
_FIXTURE = pathlib.Path(__file__).resolve().parents[2] / "rag_eval" / "fixtures" / "evalset.json"


def _load():
    spec = importlib.util.spec_from_file_location("_vinc_make_queries", _SRC)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


#: 커밋된 평가셋은 **두 코퍼스가 전부 있는 기계**에서 만들어졌다. `build()` 는 없는
#: 코퍼스를 조용히 건너뛰므로, 하나라도 빠지면 재생산본이 더 적어져 바이트 대조가
#: 실패한다. 여태 이 가드가 fixture 존재를 봤는데 fixture 는 커밋돼 있어 **언제나
#: 참**이었다 — 자기 문구는 "코퍼스가 없으면 건너뛴다"인데 코퍼스를 안 보고 있었다.
#: 로컬(폐기된 Vinc-docs 체크아웃이 남아 있는 기계)에서는 통과하고 CI 에서만 터졌다.
def _corpora_present() -> bool:
    # 루트는 모듈 것을 그대로 쓴다 — 여기서 재계산하면 VINC_EVAL_ROOT 오버라이드
    # (워크트리 실행)와 어긋나서, 가드는 skip 인데 build() 는 도는 식의 불일치가 난다.
    m = _load()
    return all((m._ROOT / rel).exists() for _, rel in m._CORPORA)


@unittest.skipUnless(_FIXTURE.exists() and _corpora_present(),
                     "코퍼스(Vinc/starter-packs, Vinc-docs)가 로컬에 없다")
class RegenerationTests(unittest.TestCase):
    def test_리팩터_후에도_커밋된_평가셋과_바이트단위로_같다(self):
        m = _load()
        regen = m.build(30)
        committed = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(regen, committed)


class SelectPureTests(unittest.TestCase):
    """select() 자체는 후보 출처(파일·스토어)와 무관해야 한다 — 최소 계약만."""

    def setUp(self):
        self.m = _load()

    def test_모호한_제목은_제외되고_문서당_상한이_지켜진다(self):
        cands = [
            {"query": "겹치는 제목", "doc_id": "a", "lang": "ko", "corpus": "x", "source": "a"},
            {"query": "겹치는 제목", "doc_id": "b", "lang": "ko", "corpus": "x", "source": "b"},
            {"query": "고유1", "doc_id": "a", "lang": "ko", "corpus": "x", "source": "a"},
            {"query": "고유2", "doc_id": "a", "lang": "ko", "corpus": "x", "source": "a"},
            {"query": "고유3", "doc_id": "a", "lang": "ko", "corpus": "x", "source": "a"},
        ]
        out = self.m.select(cands, per_lang=30, corpus_meta=[])
        picked = {q["query"] for q in out["queries"]}
        self.assertNotIn("겹치는 제목", picked)          # 두 문서에 나온 제목은 모호
        self.assertEqual(len(picked & {"고유1", "고유2", "고유3"}), 2)  # 문서당 상한 2

    def test_generated_by_를_덮어쓸_수_있다(self):
        out = self.m.select([], per_lang=30, corpus_meta=[], generated_by="test-caller")
        self.assertEqual(out["generated_by"], "test-caller")


if __name__ == "__main__":
    unittest.main()
