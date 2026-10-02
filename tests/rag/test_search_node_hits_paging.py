# -*- coding: utf-8 -*-
"""P8 — vinc_search 의 node_hits 페이징 · total_count 상한 표식 · 행의 문서 제목.

감사 [[vinc.audit.personal-graph-and-mcp-2026-09-24]] §5 의 세 결함을 고정한다.

  #17 `query=design limit=5` 를 커서로 세 번 넘기면 같은 node_hits 가 페이지마다
      되풀이됐다. 이제 node_hits 는 첫 페이지에만 있고, 이어 읽기 페이지는 빈
      목록과 `node_hits_omitted` 를 싣는다. **첫 페이지는 그대로다** — brief(top_k 5)
      와 Ask·검색창(top_k 20)은 커서를 안 쓴다.
  #18 total_count 가 거의 늘 200·199 였다(풀 상한). 닿았을 때 `total_count_capped`.
  #19 허브 `search` 가 발췌 첫 줄을 제목으로 썼다 — 행이 doc_id 만 들고 있어서다.
      이제 요약 행에 `doc_title` 이 실린다(소유자 스코프 `_DocMeta` 조인).
근거: [[vinc.plan.mcp-read-tool-fixes-2026-09-24]] §3 S11·S12
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import docstore as ds          # noqa: E402
from api import query as q              # noqa: E402
from vinc_rag import retrieval         # noqa: E402  (search 가 부르는 이름은 여기 산다)

_NODE = {"id": "concept:design-hub", "title": "Design hub", "kind": "concept",
         "domain": "vinc/design", "matched_in": "title", "hit_tokens": 1,
         "title_tokens": 1, "token_total": 1}


def _mk_rows(n: int, doc: str = "docA") -> list[dict]:
    return [{"id": f"{doc}#{i:05d}", "text": f"본문 {i}", "score": float(n - i)}
            for i in range(n)]


class _Store:
    """q.search 가 만지는 최소면. `_DocMeta` 조회만 흉내 내고 그 호출을 적어 둔다."""
    driver_name = "fake"
    scoped_chunk_search = True     # owner 필터를 적용한다고 신고(청크 팔 fail-closed 통과)

    def __init__(self, rows, titles=None):
        self.rows = rows
        self.titles = titles or {}
        self.meta_calls: list[tuple[str, dict]] = []

    def fulltext_search(self, text, k, filters=None):
        return [dict(r) for r in self.rows[:int(k)]]

    def run(self, cypher, params=None):
        if "_DocMeta" in cypher:
            self.meta_calls.append((cypher, dict(params or {})))
            return [{"id": i, "t": self.titles[i]}
                    for i in (params or {}).get("ids", []) if i in self.titles]
        return []

    def close(self):
        pass


class NodeHitsPagingTests(unittest.TestCase):
    def setUp(self):
        self._orig = retrieval._node_fallback_search
        self.calls = 0

        def _fake(store, query, **kw):
            self.calls += 1
            return [dict(_NODE)]
        retrieval._node_fallback_search = _fake

    def tearDown(self):
        retrieval._node_fallback_search = self._orig

    def test_node_hits_는_첫_페이지에만_있다(self):
        store = _Store(_mk_rows(15))
        first = q.search("design", store=store, top_k=5)
        self.assertEqual([h["id"] for h in first["node_hits"]], ["concept:design-hub"])
        self.assertNotIn("node_hits_omitted", first, "첫 페이지 모양이 바뀌었다")
        pages, cur = [first], first["next_cursor"]
        while cur:
            res = q.search("design", store=store, top_k=5, cursor=cur)
            self.assertNotIn("error", res)
            pages.append(res)
            cur = res["next_cursor"]
        self.assertEqual(len(pages), 3)                 # 감사 재현: 세 페이지
        for p in pages[1:]:
            self.assertEqual(p["node_hits"], [], "이어 읽기 페이지가 node_hits 를 되풀이했다")
            self.assertEqual(p["node_hits_omitted"], "first_page_only")
        self.assertEqual(self.calls, 1, "이어 읽기 페이지가 노드 팔을 다시 돌렸다")

    def test_첫_페이지의_키_집합은_그대로다(self):
        # brief·Ask 가 보는 첫 페이지에 새 키가 붙지 않는다(풀 상한 미도달 · 문서
        # 메타 없음). 여기 목록이 늘면 소비처 셋을 함께 확인할 것.
        res = q.search("design", store=_Store(_mk_rows(3)), top_k=5)
        self.assertEqual(set(res), {"kind", "executed", "rows", "node_hits", "arms",
                                    "total_count", "has_more", "next_cursor", "page"})


class CappedTotalTests(unittest.TestCase):
    def test_풀_상한에_닿으면_표식이_선다(self):
        res = q.search("x", store=_Store(_mk_rows(q.SEARCH_POOL_CAP + 50)), top_k=5)
        self.assertEqual(res["total_count"], q.SEARCH_POOL_CAP)
        self.assertIs(res["total_count_capped"], True)

    def test_상한_아래면_표식이_없다(self):
        res = q.search("x", store=_Store(_mk_rows(q.SEARCH_POOL_CAP - 1)), top_k=5)
        self.assertEqual(res["total_count"], q.SEARCH_POOL_CAP - 1)
        self.assertNotIn("total_count_capped", res)


class DocTitleTests(unittest.TestCase):
    def test_요약_행에_문서_제목이_실린다(self):
        store = _Store(_mk_rows(3), titles={"docA": "MCP contract"})
        rows = q.search("x", store=store, top_k=5, owner="user:a")["rows"]
        self.assertTrue(rows)
        self.assertTrue(all(r["doc_title"] == "MCP contract" for r in rows))
        self.assertEqual(len(store.meta_calls), 1, "페이지당 조회는 한 번이어야 한다")
        cypher, params = store.meta_calls[0]
        self.assertIn("owner_id = $owner", cypher, "남의 문서 제목이 샐 수 있다")
        self.assertEqual(params["owner"], "user:a")
        self.assertEqual(params["ids"], ["docA"])

    def test_제목이_없으면_키를_달지_않는다(self):
        rows = q.search("x", store=_Store(_mk_rows(2)), top_k=5)["rows"]
        self.assertTrue(all("doc_title" not in r for r in rows))

    def test_full_행에는_달지_않는다(self):
        store = _Store(_mk_rows(2), titles={"docA": "T"})
        q.search("x", store=store, top_k=5, detail="full")
        self.assertEqual(store.meta_calls, [])


class DocTitleEndToEnd(unittest.TestCase):
    """실엔진 — 본문 첫 줄이 코드 펜스여도 행의 제목은 문서 메타의 제목이다."""

    def setUp(self):
        keys = ("MEMORY_STORE_BACKEND", "MEMORY_DATA_DIR", "MEMORY_EMBEDDING_MODEL")
        self._keep = {k: os.environ.get(k) for k in keys}
        os.environ["MEMORY_STORE_BACKEND"] = "embedded"
        os.environ["MEMORY_DATA_DIR"] = os.path.join(tempfile.mkdtemp(), "store")
        os.environ["MEMORY_EMBEDDING_MODEL"] = "local-en"
        from store.backend import open_store
        from store.config import load_config
        cfg = load_config()
        self.store = open_store(cfg)
        self.store.provision(dim=cfg.embedding_dim)

    def tearDown(self):
        self.store.close()
        for k, v in self._keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_검색_행의_제목은_문서_제목이다(self):
        ds.put_doc(self.store, owner="user:a", doc_id="vinc.canon.probe",
                   title="The MCP contract probe",
                   text="```mermaid\nflowchart TB\n```\n\nThe contract body text.")
        rows = q.search("contract body", owner="user:a", store=self.store, top_k=5)["rows"]
        self.assertTrue(rows, "색인된 청크를 못 찾았다")
        self.assertEqual({r["doc_title"] for r in rows}, {"The MCP contract probe"})


if __name__ == "__main__":
    unittest.main()
