"""문서 제목을 바꾸면 미러(`concept:doc.<id>`)의 제목도 따라간다 — 물려받은 제목일 때만.

앱에서 새 문서를 만들면 첫 저장이 주제와 함께 미러를 만들고, 미러는 그때 문서 제목을
물려받는다. 그 뒤 제목을 고치면 미러는 옛 제목을 그대로 들고 있어서 트리와 View 가 옛
이름을 보였다(PR Vinculums/Vinc#479 의 남은 일). 한편 미러 제목을 사람이 따로 다듬었다면
그 값은 덮으면 안 된다(`_set_authored_topic` 의 규칙). 두 규칙이 "미러가 아직 옛 문서
제목을 들고 있는가" 한 조건으로 함께 선다 — 여기서 그 둘을 다 고정한다.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import docstore  # noqa: E402
from store import pkns  # noqa: E402

_DRIVER = None
try:
    from store.embedded_backend import import_driver
    _DRIVER, _ = import_driver()
except Exception:  # pragma: no cover - 드라이버 없음
    _DRIVER = None


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class MirrorTitleFollowsDocTitle(unittest.TestCase):
    OWNER = "user:a"
    TOPIC = "vinc/product/timeline"

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_DATA_DIR": os.path.join(self._tmp, "store")}.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        from store.config import load_config
        from store.backend import open_store
        self.store = open_store(load_config())
        self.store.provision(dim=4)

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _put(self, doc_id, title, text="첫 줄", topic=None):
        return docstore.put_doc(self.store, owner=self.OWNER, text=text, doc_id=doc_id,
                                title=title, topic=topic)

    def _mirror_title(self, doc_id):
        mid = pkns.key(self.OWNER, "concept:doc." + doc_id)
        rows = self.store.run("MATCH (n:Concept) WHERE n.concept_id = $id "
                              "RETURN n.title AS title", {"id": mid}) or []
        return rows[0]["title"] if rows else None

    def test_retitle_moves_an_inherited_mirror_title(self):
        self._put("release-checklist", "Release checklist", topic=self.TOPIC)
        self.assertEqual(self._mirror_title("release-checklist"), "Release checklist")
        r = self._put("release-checklist", "Release checklist for 0.9.6")
        self.assertTrue(r["executed"])
        self.assertEqual(self._mirror_title("release-checklist"), "Release checklist for 0.9.6")

    def test_body_only_resend_keeps_the_title(self):
        self._put("d1", "Title one", topic=self.TOPIC)
        docstore.put_doc(self.store, owner=self.OWNER, text="바뀐 본문", doc_id="d1")
        self.assertEqual(self._mirror_title("d1"), "Title one")

    def test_a_hand_edited_mirror_title_is_not_overwritten(self):
        self._put("d2", "Draft name", topic=self.TOPIC)
        mid = pkns.key(self.OWNER, "concept:doc.d2")
        self.store.run("MATCH (n:Concept) WHERE n.concept_id = $id SET n.title = $t",
                       {"id": mid, "t": "Curated name"})
        self._put("d2", "Second draft name")
        self.assertEqual(self._mirror_title("d2"), "Curated name")

    def test_no_mirror_means_no_mirror_is_made(self):
        # 주제를 적은 적 없는 문서 — 제목만 바꿔도 미러를 새로 만들지 않는다.
        self._put("d3", "No topic yet")
        self._put("d3", "Still no topic")
        self.assertIsNone(self._mirror_title("d3"))

    def test_mirror_inherited_the_doc_id_when_there_was_no_title(self):
        self._put("d4", "", topic=self.TOPIC)
        self.assertEqual(self._mirror_title("d4"), "d4")
        self._put("d4", "Named at last")
        self.assertEqual(self._mirror_title("d4"), "Named at last")


if __name__ == "__main__":
    unittest.main()
