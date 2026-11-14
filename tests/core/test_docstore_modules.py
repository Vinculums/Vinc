# -*- coding: utf-8 -*-
"""`api.docreader`·`api.docextract`·`api.docevidence`·`api.docembed` 가 리더 도우미·개념 추출·관계
근거 백필·임베딩 상태의 정본이고, `api.docstore` 의 이름은 같은 객체를 가리키는가.

잠그는 것:
- `docstore.X` 로 부르는 이름(서버·평가 스크립트·테스트가 그렇게 부른다)이 새 모듈의 이름과
  **같은 객체**다. 사본이 생기면 한쪽만 고친 규칙이 조용히 갈라진다.
- 네 모듈은 `api.docstore` 를 최상위에서 import 하지 않는다. docstore 가 네 모듈을 최상위에서
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

from api import docembed  # noqa: E402
from api import docevidence  # noqa: E402
from api import docextract  # noqa: E402
from api import docreader  # noqa: E402
from api import docstore  # noqa: E402
from engines import mentions_link as ml  # noqa: E402

#: 모듈 → docstore 가 다시 내보내는 이름.
MOVED = {
    docreader: ("_HL_TOKEN_RE", "highlight_ranges", "_editor_offsets", "_concept_terms",
                "doc_highlights", "_heading_level", "doc_sections", "doc_context"),
    docextract: ("_SLUG_RE", "_slug", "_ATX_RE", "_UNDERLINE_RE", "_FENCE_RE", "_LIST_RE",
                 "_MAX_HEADING_LEN", "_headings", "_title_shaped", "_HEAD_NUM_RE", "_MD_LINK_RE",
                 "_MD_EMPH_RE", "_MD_PREFIX_RE", "_SENT_END_RE", "_ONE_LINER_MAX", "_strip_md",
                 "_clean_heading", "_STRUCT_SLUGS", "_CLAUSE_OPENERS", "_concept_shaped",
                 "_one_liner", "extract_concepts", "_FM_DOMAIN_RE", "_front_domain", "_doc_topic",
                 "extract_doc", "_extract_body_doc"),
    docevidence: ("_EVIDENCE_MAX", "_sentences", "backfill_evidence"),
    docembed: ("reembed_doc", "pending_embed_chunks", "_fair_pending_docs", "_vec_width",
               "apply_embeddings", "_apply_embeddings", "_EMBED_MODEL_KEY", "stored_embed_model",
               "_EMBED_APPLIED_KEY", "_EMBED_MIGRATING_KEY", "_MIGRATION_GATE_MIN",
               "_MIGRATION_GATE_RATIO", "_EMBED_STALL_DEFAULT_S", "embed_model_state",
               "switch_embed_model", "stale_docs"),
}

#: docembed 가 호출 때 부르는 docstore 의 공개 도우미 → docstore 의 사설 별칭.
DOCSTORE_HELPERS = {"age_s": "_age_s", "doc_meta": "_meta",
                    "drop_vector_index_if_any": "_drop_vector_index_if_any",
                    "remnant_refusal": "_remnant_refusal"}


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

    def test_docstore_helpers_have_public_names_and_the_old_aliases(self):
        for public, private in DOCSTORE_HELPERS.items():
            with self.subTest(name=public):
                self.assertIs(getattr(docstore, public), getattr(docstore, private))

    def test_reader_parses_headings_with_the_extraction_parser(self):
        # 목차와 개념 후보가 같은 파서를 쓴다 — 사본이면 둘이 갈린다.
        self.assertIs(docreader._headings, docextract._headings)


class LinkerNamesTest(unittest.TestCase):
    """링크 대상 표와 제목 토큰 상한의 정본은 링커 하나다. 읽기 쪽에 사본이 있으면 의미층이 늘 때
    한쪽만 늘어 결정만 언급한 문서가 빈손이 된다."""

    def test_the_linker_keeps_its_private_names_as_aliases(self):
        self.assertIs(ml._LINK_TARGETS, ml.LINK_TARGETS)
        self.assertIs(ml._tokens, ml.tokens)
        self.assertIs(ml._fetch_concepts, ml.fetch_concepts)
        self.assertEqual(ml._MAX_TITLE_TOKENS, ml.MAX_TITLE_TOKENS)

    def test_the_readers_hold_the_linkers_objects_not_copies(self):
        self.assertIs(docreader.LINK_TARGETS, ml.LINK_TARGETS)
        self.assertIs(docreader.tokens, ml.tokens)
        self.assertEqual(docreader.MAX_TITLE_TOKENS, ml.MAX_TITLE_TOKENS)
        self.assertEqual(docextract.MAX_TITLE_TOKENS, ml.MAX_TITLE_TOKENS)

    def test_no_second_target_table_or_title_limit_is_defined_in_api(self):
        copies = {"_MENTION_TARGETS", "_MAX_TITLE_WORDS", "_MAX_TITLE_TOKENS", "_LINK_TARGETS"}
        for path in sorted((ROOT / "api").glob("doc*.py")):
            with self.subTest(module=path.name):
                self.assertFalse(_top_level_defs(path) & copies)
        for name in sorted(copies):
            with self.subTest(docstore=name):
                self.assertFalse(hasattr(docstore, name))


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

    def test_patched_doc_meta_reaches_the_embedding_group(self):
        # docembed 는 docstore 의 공개 도우미를 호출 때 읽는다. 바꿔 끼우면 재임베딩 큐잉에 닿는다.
        original = docstore.doc_meta
        docstore.doc_meta = lambda *a, **k: None
        try:
            got = docstore.reembed_doc(None, "d1", owner="tenant:x")
        finally:
            docstore.doc_meta = original
        self.assertEqual((got["executed"], got["error"]), (False, "unknown doc_id"))

    def test_patched_backfill_reaches_extraction(self):
        # 추출은 근거 백필을 호출 때 docstore 에서 읽는다(`test_pk_compose_write` 가 그렇게 바꿔 끼운다).
        import inspect
        src = inspect.getsource(docextract.extract_doc)
        self.assertIn("backfill_evidence", src)
        self.assertNotIn("backfill_evidence", {n for n in vars(docextract)})


if __name__ == "__main__":
    unittest.main()
