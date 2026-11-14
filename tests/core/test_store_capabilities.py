# -*- coding: utf-8 -*-
"""스토어의 선택 능력 선언(`store.backend.capabilities`·`operation`·`flag`) — 엔진도 서버도 없이 돈다.

잠그는 것:
- 백엔드마다 선언한 `capabilities` 가 그 클래스에 실제로 있는 선택 연산과 같다. 메서드를 더하고
  선언을 잊거나, 선언만 남기고 메서드를 지우면 여기서 떨어진다(호출부의 답이 선언을 따르므로).
- Neo4j 읽기 뷰는 따로 선언하지 않고 어댑터의 선언을 빌린다.
- `operation`·`flag` 은 모르는 이름을 던진다. 선언이 없는 객체(테스트 가짜·세션 래퍼)는 속성으로 답한다.
- api·store 의 호출부(어댑터 파일 밖)는 선택 연산·상태 표지를 getattr/hasattr 로 더듬지 않는다.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from store import backend as B  # noqa: E402
from store import embedded_backend as eb  # noqa: E402
from store import neo4j_backend as nb  # noqa: E402


def _present(cls) -> frozenset[str]:
    return frozenset(n for n in B.OPTIONAL_OPERATIONS if getattr(cls, n, None) is not None)


@pytest.mark.parametrize("cls", [eb.EmbeddedBackend, eb._ReaderView, nb.Neo4jBackend],
                         ids=["embedded", "embedded-reader", "neo4j"])
def test_선언은_실제_메서드와_같다(cls):
    declared = cls.__dict__["capabilities"]
    assert isinstance(declared, frozenset)
    assert declared <= B.OPTIONAL_OPERATIONS, sorted(declared - B.OPTIONAL_OPERATIONS)
    assert declared == _present(cls), {"선언만": sorted(declared - _present(cls)),
                                       "메서드만": sorted(_present(cls) - declared)}


def test_두_백엔드의_선언():
    assert eb.EmbeddedBackend.capabilities == B.OPTIONAL_OPERATIONS
    assert eb._ReaderView.capabilities == frozenset({"ensure_search_indexes"})
    assert nb.Neo4jBackend.capabilities == frozenset({
        "current_batch", "write_batch", "drop_vector_index", "drop_search_indexes",
        "ensure_search_indexes", "table_columns", "snapshot"})


def test_Neo4j_읽기_뷰는_어댑터의_선언을_빌린다():
    assert "capabilities" not in nb._ReaderView.__dict__
    backend = object.__new__(nb.Neo4jBackend)
    view = nb._ReaderView(backend)
    assert B.capabilities(view) is nb.Neo4jBackend.capabilities
    assert B.operation(view, "table_columns").__func__ is nb.Neo4jBackend.table_columns
    assert B.operation(view, "heal_fts_index") is None


def test_모르는_이름은_던진다():
    with pytest.raises(KeyError, match="unknown optional store operation"):
        B.operation(object(), "drop_vector_indx")
    with pytest.raises(KeyError, match="unknown store state flag"):
        B.flag(object(), "fts_index_remant")


def test_선언에_없는_연산은_속성이_있어도_없다():
    class Declared:
        capabilities = frozenset({"snapshot"})

        def snapshot(self, dest_dir):
            return dest_dir

        def table_columns(self, table):
            return []

    s = Declared()
    assert B.operation(s, "snapshot").__func__ is Declared.snapshot
    assert B.operation(s, "table_columns") is None
    assert B.capabilities(s) == frozenset({"snapshot"})


def test_선언했는데_속성이_없으면_없다():
    class Declared:
        capabilities = frozenset({"snapshot"})

    assert B.operation(Declared(), "snapshot") is None


def test_선언이_없으면_속성으로_답한다():
    class Fake:
        def current_batch(self):
            return None

        in_manual_transaction = None

    s = Fake()
    assert B.operation(s, "current_batch").__func__ is Fake.current_batch
    assert B.operation(s, "in_manual_transaction") is None
    assert B.operation(s, "write_batch") is None
    assert B.capabilities(s) == frozenset({"current_batch"})
    assert B.operation(None, "snapshot") is None


def test_가짜_객체는_getattr_과_같은_답이다():
    m = mock.MagicMock()
    assert B.operation(m, "snapshot") is m.snapshot
    assert B.flag(m, "read_only") is m.read_only


def test_상태_표지는_없으면_False_이고_값을_그대로_준다():
    class S:
        read_only = "yes"
        scoped_chunk_search = True

    assert B.flag(S(), "read_only") == "yes"        # `is True` 비교는 호출부가 한다
    assert B.flag(S(), "scoped_chunk_search") is True
    assert B.flag(S(), "fts_index_remnant") is False
    assert B.flag(None, "vector_index_remnant") is False


def test_어휘는_서로_겹치지_않는다():
    assert not (B.OPTIONAL_OPERATIONS & B.STATE_FLAGS)


# ── 호출부가 속성을 직접 더듬지 않는다 ─────────────────────────────────────────

#: 선택 능력을 자기 안에서 읽는 어댑터 파일. 호출부가 아니다.
_ADAPTERS = {"store/embedded_backend.py", "store/neo4j_backend.py", "store/backend.py"}


def test_api_store_호출부는_getattr_로_능력을_묻지_않는다():
    names = "|".join(sorted(B.OPTIONAL_OPERATIONS | B.STATE_FLAGS))
    probe = re.compile(r"\b(?:getattr|hasattr)\([^,()]+,\s*['\"](?:%s)['\"]" % names)
    files = subprocess.check_output(["git", "ls-files", "api", "store"], cwd=ROOT, text=True).split()
    hits = []
    for f in files:
        if not f.endswith(".py") or f in _ADAPTERS:
            continue
        for i, line in enumerate((ROOT / f).read_text(encoding="utf-8").splitlines(), 1):
            if probe.search(line):
                hits.append(f"{f}:{i}: {line.strip()}")
    assert hits == [], "store.backend.operation / flag 으로 묻는다:\n" + "\n".join(hits)
