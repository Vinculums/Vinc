# -*- coding: utf-8 -*-
"""`api.node_schema` 가 스키마 상수의 정본이고, 옛 사설 이름은 같은 객체를 가리키는가.

잠그는 것:
- `api.fragment` 의 사설 이름(`_NODE_MAP` …)과 `api.docstore._norm_content_time`,
  `api.query`(`api.query_base`)의 `_OWNER_SCOPED_LABELS` 가 `api.node_schema` 의 공개
  이름과 **같은 객체**다. 사본이 생기면 한쪽만 고친 값이 조용히 갈라진다.
- `api.node_schema` 는 잎이다: 표준 라이브러리 밖의 모듈을 import 하지 않는다.
- 아직 자기 사본을 가진 모듈(`api.sync`)의 값이 공개 값과 같다. 그 모듈이 공개 이름으로
  옮겨 가면 이 비교는 같은 객체 검사로 바꾼다. `api.query_base` 가 스키마에서 만든 표는
  `tests/core/test_query_base.py` 가 대조한다.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from api import docstore  # noqa: E402
from api import fragment  # noqa: E402
from api import node_schema  # noqa: E402
from api import query  # noqa: E402
from api import query_base  # noqa: E402
from api import sync  # noqa: E402

#: (사설 이름을 가진 모듈, 사설 이름, 공개 이름).
ALIASES = (
    (fragment, "_NODE_MAP", "NODE_MAP"),
    (fragment, "_CC_EDGES", "CC_EDGES"),
    (fragment, "_EC_EDGES", "EC_EDGES"),
    (fragment, "_SUPERSEDES_EDGE", "SUPERSEDES_EDGE"),
    (fragment, "_SUPERSEDES_REL", "SUPERSEDES_REL"),
    (fragment, "_SUPERSEDES_KINDS", "SUPERSEDES_KINDS"),
    (fragment, "_OWNER_SCOPED_LABELS", "OWNER_SCOPED_LABELS"),
    (fragment, "_KEPT_FIELDS", "KEPT_FIELDS"),
    (fragment, "_CONTRACT_FIELDS", "CONTRACT_FIELDS"),
    (fragment, "_INTERVAL_FIELDS", "INTERVAL_FIELDS"),
    (docstore, "_norm_content_time", "normalize_time"),
    (query_base, "_OWNER_SCOPED_LABELS", "OWNER_SCOPED_LABELS"),
    (query, "_OWNER_SCOPED_LABELS", "OWNER_SCOPED_LABELS"),
)


class AliasesAreTheSameObjects(unittest.TestCase):

    def test_every_private_name_is_the_public_object(self):
        for module, private, public in ALIASES:
            with self.subTest(name=f"{module.__name__}.{private}"):
                self.assertIs(getattr(module, private), getattr(node_schema, public),
                              f"{module.__name__}.{private} 가 node_schema.{public} 와 "
                              "다른 객체다")

    def test_rel_to_fragment_inverts_both_edge_tables(self):
        # 의미층 엣지와 about 의 역방향이다. 대체와 Record 엣지는 전용 경로로 나른다.
        expected = {rel: ft for ft, rel in node_schema.CC_EDGES.items()}
        expected.update({rel: ft for ft, rel in node_schema.EC_EDGES.items()})
        self.assertEqual(node_schema.REL_TO_FRAGMENT, expected)
        self.assertNotIn(node_schema.SUPERSEDES_REL, node_schema.REL_TO_FRAGMENT)
        self.assertNotIn("ATTACHES_TO", node_schema.REL_TO_FRAGMENT)


class CopiesStillAgree(unittest.TestCase):

    def test_sync_rel_to_fragment_matches(self):
        self.assertEqual(sync._REL_TO_FRAGMENT, node_schema.REL_TO_FRAGMENT,
                         "sync._REL_TO_FRAGMENT 와 node_schema.REL_TO_FRAGMENT 가 다르다")


class NodeSchemaIsALeaf(unittest.TestCase):

    def test_imports_only_the_standard_library(self):
        tree = ast.parse((ROOT / "api" / "node_schema.py").read_text(encoding="utf-8"))
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                found.append("." * node.level + (node.module or ""))
            elif isinstance(node, ast.Import):
                found.extend(alias.name for alias in node.names)
        outside = [name for name in found
                   if name.startswith(".") or name.split(".")[0] not in sys.stdlib_module_names]
        self.assertFalse(outside, f"node_schema 가 표준 라이브러리 밖을 import 한다: {outside}")


if __name__ == "__main__":
    unittest.main()
