# -*- coding: utf-8 -*-
"""로컬 소유자 하나로 접기(VIN-131) — `user:local` → `tenant:local`.

재는 것:
  1. **실 엔진**: MCP 가 옛 기본값으로 쓴 노드·엣지·문서·툼스톤·기각·시퀀스가 접힌 뒤
     `tenant:local` 로 전부 보이고, `user:local` 에는 아무것도 안 남으며, 두 번째 접기는
     아무것도 안 한다. 스텁 초록은 소유 격리의 증거가 아니다(SEC-1 교훈).
  2. **허브 방어**: 네임스페이싱 스토어에서는 쓰기를 하나도 내지 않는다.
  3. **대상 목록**: 스키마 파생 소유 라벨 전부를 덮는다(NodeVec 만 뺀다). 새 소유 라벨이
     생기면 자동으로 따라온다.
  4. **기본값 한 벌**: 로컬 MCP 브리지·fragment·profile.yml 의 기본 소유자가 같다.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api import owner_fold as of          # noqa: E402
from store import pkns                    # noqa: E402
from store.embedded_backend import _import_driver  # noqa: E402

_DRIVER, _NAME = _import_driver()
L, T = of.LEGACY, of.CANON


def _fresh_store():
    keys = ("MEMORY_STORE_BACKEND", "MEMORY_DATA_DIR", "MEMORY_EMBEDDING_MODEL")
    keep = {k: os.environ.get(k) for k in keys}
    os.environ["MEMORY_STORE_BACKEND"] = "embedded"
    os.environ["MEMORY_DATA_DIR"] = os.path.join(tempfile.mkdtemp(), "store")
    os.environ["MEMORY_EMBEDDING_MODEL"] = "local-en"
    from store.backend import open_store
    from store.config import load_config
    cfg = load_config()
    store = open_store(cfg)
    store.provision(dim=cfg.embedding_dim)
    return store, keep


def _restore(keep):
    for k, v in keep.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치")
@unittest.skipIf(pkns.enabled(), "네임스페이싱 스토어는 접지 않는다(허브) — 아래 거절 테스트가 본다")
class FoldOnEngine(unittest.TestCase):

    def setUp(self):
        from api import docstore
        from api import fragment as frag
        from api import query as q
        self.q = q
        self.store, self._keep = _fresh_store()
        s = self.store
        # 앱이 쓴 것(이미 tenant:local) 하나와 MCP 가 옛 기본값으로 쓴 것들.
        frag.merge_fragment({"owner": T, "nodes": [
            {"id": "concept:by-app", "type": "concept", "title": "By app", "domain": "vinc/x"}],
            "edges": []}, store=s)
        frag.merge_fragment({"owner": L, "nodes": [
            {"id": "concept:a", "type": "concept", "title": "A", "domain": "vinc/x"},
            {"id": "concept:b", "type": "concept", "title": "B", "domain": "vinc/x"},
            {"id": "concept:gone", "type": "concept", "title": "Gone", "domain": "vinc/x"},
            {"id": "record:r", "type": "record", "title": "R", "domain": "vinc/x"}],
            "edges": [{"from": "concept:a", "to": "concept:b", "type": "relates_to"},
                      {"from": "record:r", "to": "concept:a", "type": "derives_from",
                       "evidence": "e"}]}, store=s)
        docstore.put_doc(s, owner=L, doc_id="doc.mcp", title="MCP doc",
                         text="written through the local assistant")
        q.reject_merge_pair(owner=L, a_id="concept:b", b_id="concept:a", store=s)
        q.delete_node("concept:gone", owner=L, store=s)

    def tearDown(self):
        self.store.close()
        _restore(self._keep)

    def _count(self, label, owner):
        rows = self.store.run(f"MATCH (n:{label}) WHERE n.owner_id = $o RETURN count(n) AS c",
                              {"o": owner})
        return int(rows[0]["c"])

    def test_접으면_앱의_소유자로_전부_보인다(self):
        from api import docstore
        res = of.fold_legacy_owner(self.store)
        self.assertTrue(res["executed"])
        moved = res["moved"]
        self.assertEqual(moved.get("Concept"), 2)          # a, b (gone 은 지워졌다)
        self.assertEqual(moved.get("Record"), 1)
        self.assertEqual(moved.get("_DocMeta"), 1)
        self.assertGreaterEqual(moved.get("Chunk", 0), 1)
        self.assertEqual(moved.get("_Tombstone"), 1)
        self.assertEqual(moved.get("_MergeRejection"), 1)
        self.assertEqual(moved.get("seq"), 1)

        for label in of._labels() + ["_MergeRejection"]:
            self.assertEqual(self._count(label, L), 0, label)
        self.assertEqual(self._count("Concept", T), 3)     # by-app + a + b

        # 엣지는 소유가 없다 — 노드가 그대로라 그대로다.
        nb = self.q.neighbors("concept:a", owner=T, store=self.store)
        ids = sorted(n.get("id") for n in (nb.get("neighbors") or nb.get("rows") or []))
        self.assertEqual(ids, ["concept:b", "record:r"])
        doc = docstore.get_doc(self.store, "doc.mcp", owner=T)
        self.assertTrue(doc.get("verified"))
        self.assertEqual(doc.get("text"), "written through the local assistant")
        rej = self.q.list_merge_rejections(owner=T, store=self.store)
        self.assertEqual(len(rej.get("rejections") or rej.get("rows") or []), 1)
        # 시퀀스는 둘 중 큰 값이 남고 옛 키는 사라진다.
        self.assertIsNone(of._meta_int(self.store, f"seq:{L}"))
        self.assertGreater(of._meta_int(self.store, f"seq:{T}") or 0, 0)

    def test_두번째_접기는_아무것도_안_한다(self):
        of.fold_legacy_owner(self.store)
        again = of.fold_legacy_owner(self.store)
        self.assertTrue(again["executed"])
        self.assertEqual(again["moved"], {})

    def test_같은_쌍의_기각이_이미_있으면_그것을_남긴다(self):
        self.q.reject_merge_pair(owner=T, a_id="concept:a", b_id="concept:b", note="mine",
                                 store=self.store)
        of.fold_legacy_owner(self.store)
        self.assertEqual(self._count("_MergeRejection", T), 1)
        self.assertEqual(self._count("_MergeRejection", L), 0)


class _NoWriteStore:
    def __init__(self):
        self.calls = []

    def run(self, cypher, params=None):
        self.calls.append(cypher)
        return [{"c": 5, "v": "3"}]


class NamespacedRefusalTest(unittest.TestCase):

    def test_네임스페이싱_스토어에서는_아무것도_안_쓴다(self):
        keep = os.environ.get("VINC_PK_NAMESPACED")
        os.environ["VINC_PK_NAMESPACED"] = "on"
        try:
            store = _NoWriteStore()
            res = of.fold_legacy_owner(store)
        finally:
            if keep is None:
                os.environ.pop("VINC_PK_NAMESPACED", None)
            else:
                os.environ["VINC_PK_NAMESPACED"] = keep
        self.assertFalse(res["executed"])
        self.assertEqual(res["skipped"], "namespaced store")
        self.assertEqual(store.calls, [])


class CoverageTest(unittest.TestCase):

    def test_소유_라벨_전부를_덮고_NodeVec_만_뺀다(self):
        from api import query as q
        from store.embedded_schema import owner_scoped_labels
        labels = set(of._labels())
        self.assertEqual(labels | {"NodeVec"}, set(owner_scoped_labels()) | set(of._META_LABELS))
        self.assertNotIn("NodeVec", labels)
        # purge 가 지우는 소유 메타와 같은 집합이다(`_MergeRejection` 은 키를 옮겨 따로 한다).
        self.assertEqual(set(q._purge_labels()) - {"NodeVec"},
                         labels | {"_MergeRejection"})


class OneOwnerTest(unittest.TestCase):

    def test_로컬_기본_소유자는_한_벌이다(self):
        from api import fragment
        from vinc_mcp import bridge
        self.assertEqual(bridge.DEFAULT_OWNER, of.CANON)
        self.assertEqual(fragment.DEFAULT_OWNER, of.CANON)
        profile = (ROOT / "profile.yml").read_text(encoding="utf-8")
        self.assertRegex(profile, r'default_owner:\s*"tenant:local"')

    def test_기기_사이드카만_접는다(self):
        """허브는 `--parent-pid` 를 안 넘긴다(docker-entrypoint.sh). 접기는 그 표식 뒤에만 선다."""
        src = (ROOT / "api/server.py").read_text(encoding="utf-8")
        self.assertRegex(src, r"if guard_wal:\s*\n\s*app\._fold_owner\(\)")
        # 재링크 잡보다 먼저 선다 — 뒤에 서면 전량 재링크를 기다리다 제한 시간을 넘긴다.
        self.assertLess(src.index("app._fold_owner()"), src.index("app._schedule_mentions_rule()"))
        # 앱이 모드를 정하려고 묻기 전에 끝나야 한다 — 비동기로 걸면 그 질문이 먼저 온다.
        body = src[src.index("def _fold_owner"):]
        body = body[: body.index("\n    def ", 10)]
        self.assertIn('run_sync("owner.fold"', body)
        self.assertNotIn("jobs.submit", body)


if __name__ == "__main__":
    unittest.main()
