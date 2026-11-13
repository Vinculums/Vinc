# -*- coding: utf-8 -*-
"""`api.docreader`·`api.docextract` 가 리더 도우미와 개념 추출의 정본이고, `api.docstore` 의
이름은 같은 객체를 가리키는가.

잠그는 것:
- `docstore.X` 로 부르는 이름(서버·평가 스크립트·테스트가 그렇게 부른다)이 새 모듈의 이름과
  **같은 객체**다. 사본이 생기면 한쪽만 고친 규칙이 조용히 갈라진다.
- 두 모듈은 `api.docstore` 를 최상위에서 import 하지 않는다. docstore 가 두 모듈을 최상위에서
  다시 내보내므로, 거꾸로도 최상위에서 읽으면 어느 쪽을 먼저 import 하느냐에 따라 깨진다.
- docstore 쪽 이름은 호출 때 읽힌다: `docstore.get_doc` 을 바꿔 끼우면 옮긴 함수에도 닿는다.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from api import docextract  # noqa: E402
from api import docreader  # noqa: E402
from api import docstore  # noqa: E402

#: 모듈 → docstore 가 다시 내보내는 이름.
MOVED = {
    docreader: ("_HL_TOKEN_RE", "highlight_ranges", "_editor_offsets", "_concept_terms",
                "doc_highlights", "_MENTION_TARGETS", "_heading_level", "doc_sections",
                "doc_context"),
    docextract: ("_SLUG_RE", "_slug", "_ATX_RE", "_UNDERLINE_RE", "_FENCE_RE", "_LIST_RE",
                 "_MAX_HEADING_LEN", "_headings", "_title_shaped", "_HEAD_NUM_RE", "_MD_LINK_RE",
                 "_MD_EMPH_RE", "_MD_PREFIX_RE", "_SENT_END_RE", "_ONE_LINER_MAX", "_strip_md",
                 "_clean_heading", "_STRUCT_SLUGS", "_CLAUSE_OPENERS", "_MAX_TITLE_WORDS",
                 "_concept_shaped", "_one_liner", "extract_concepts", "_FM_DOMAIN_RE",
                 "_front_domain", "_doc_topic", "extract_doc", "_extract_body_doc"),
}


def _top_level_defs(path: Path) -> set[str]:
    """파일 최상위에서 정의(def·대입)된 이름. import 로 들어온 이름은 뺀다."""
    out: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


class ReexportIdentityTest(unittest.TestCase):

    def test_docstore_names_are_the_module_objects(self):
        for mod, names in MOVED.items():
            for name in names:
                with self.subTest(module=mod.__name__, name=name):
                    self.assertIs(getattr(docstore, name), getattr(mod, name))

    def test_each_name_is_defined_in_its_module_not_in_docstore(self):
        store_defs = _top_level_defs(ROOT / "api" / "docstore.py")
        for mod, names in MOVED.items():
            defs = _top_level_defs(Path(mod.__file__))
            for name in names:
                with self.subTest(module=mod.__name__, name=name):
                    self.assertIn(name, defs)
                    self.assertNotIn(name, store_defs)

    def test_reader_parses_headings_with_the_extraction_parser(self):
        # 목차와 개념 후보가 같은 파서를 쓴다 — 사본이면 둘이 갈린다.
        self.assertIs(docreader._headings, docextract._headings)


class ImportDirectionTest(unittest.TestCase):

    def test_new_modules_do_not_import_docstore_at_top_level(self):
        for mod in MOVED:
            tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.ImportFrom):
                    target = ("." * node.level) + (node.module or "")
                    names = {a.name for a in node.names}
                    with self.subTest(module=mod.__name__, line=node.lineno):
                        self.assertNotIn(target, {".docstore", "api.docstore"})
                        self.assertFalse(target in {".", "api"} and "docstore" in names)
                elif isinstance(node, ast.Import):
                    with self.subTest(module=mod.__name__, line=node.lineno):
                        self.assertNotIn("api.docstore", {a.name for a in node.names})


class CallTimeLookupTest(unittest.TestCase):

    def test_patched_get_doc_reaches_the_reader(self):
        original = docstore.get_doc
        docstore.get_doc = lambda *a, **k: {"executed": False, "error": "patched"}
        try:
            ctx = docstore.doc_context(None, "d1", owner="tenant:x")
            hl = docstore.doc_highlights(None, "d1", "concept:a", owner="tenant:x")
        finally:
            docstore.get_doc = original
        self.assertEqual((ctx["executed"], ctx["error"]), (False, "patched"))
        self.assertEqual((hl["executed"], hl["error"]), (False, "patched"))

    def test_patched_meta_reaches_extraction(self):
        original = docstore._meta
        docstore._meta = lambda *a, **k: None
        try:
            got = docstore.extract_doc(None, "d1", owner="tenant:x")
        finally:
            docstore._meta = original
        self.assertEqual((got["executed"], got["error"]), (False, "unknown doc_id"))


if __name__ == "__main__":
    unittest.main()
