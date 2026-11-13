# -*- coding: utf-8 -*-
"""노드에 딸린 **문서 전부**는 발췌 캡과 다른 계약이다.

발췌(`excerpts`)는 청크를 `limit` 개로 자른다. 그러니 그 목록에 보이는 문서는 "이
노드에 딸린 문서"가 아니라 "상위 몇 개 청크가 우연히 속한 문서"다. 소스 선택기가
그것을 전체 목록으로 읽으면 사용자는 **있는 줄도 모르는 문서**를 고를 수 없다.

실측 2026-09-20, 이 파일의 픽스처와 같은 모양: 개념 하나를 문서 여섯이 언급하는데
기본 발췌 넷이 가리키는 문서는 **넷뿐**이었고 나머지 둘은 응답 어디에도 없었다.

근거: [[vinc.spec.desktop-content-first-ia-0-9-4 §4]] — 연결 문서 열거는 상한 걸린
발췌와 별개 계약이고, 총수와 완전한 소스 신분을 내야 한다.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import docstore as ds  # noqa: E402
from api import query as q  # noqa: E402
from store import pkns  # noqa: E402

_DRIVER = None
try:
    from store.embedded_backend import import_driver
    _DRIVER, _ = import_driver()
except Exception:
    pass


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class NodeSourcesE2E(unittest.TestCase):
    OWNER = "user:a"
    # ⚠ 제목은 2글자 이상이어야 언급 링커가 문다(engines.mentions_link._tokens).
    TITLE = "옵티미스틱락"

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {
            "MEMORY_STORE_BACKEND": "embedded",
            "MEMORY_DATA_DIR": os.path.join(self._tmp, "store"),
        }.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        from store.config import load_config
        from store.backend import open_store
        self.store = open_store(load_config())
        self.store.provision(dim=4)
        self.store.run(
            "MERGE (n:Concept {concept_id:$id}) SET n.title=$t, n.domain='d', "
            "n.owner_id=$o",
            {"id": pkns.key(self.OWNER, "concept:lock"), "t": self.TITLE,
             "o": self.OWNER})

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _ctx(self, **kw):
        res = q.node_context(owner=self.OWNER, node="concept:lock", store=self.store,
                             **kw)
        self.assertTrue(res["executed"], res.get("note"))
        return res

    def test_the_source_list_is_not_what_the_excerpt_cap_happened_to_show(self):
        # 긴 문서 하나 + 짧은 문서 다섯 = 여섯. 기본 발췌는 넷이라 어느 둘은
        # 발췌에 못 든다. 그래도 소스 목록에는 여섯이 다 있어야 한다.
        ds.put_doc(self.store, owner=self.OWNER, title="긴 문서",
                   text="\n\n".join(f"{i}절. {self.TITLE} 을 다룬다." for i in range(8)))
        for i in range(5):
            ds.put_doc(self.store, owner=self.OWNER, title=f"메모{i}",
                       text=f"{self.TITLE} 에 관한 {i}번 메모.")
        res = self._ctx()
        seen_in_excerpts = {e["doc_title"] for e in res["excerpts"]}
        self.assertLess(len(seen_in_excerpts), 6,
                        "픽스처가 발췌 캡을 못 넘겼다 — 이 테스트가 무의미해진다")
        src = res["sources"]
        self.assertEqual(src["total"], 6)
        self.assertEqual({s["title"] for s in src["items"]},
                         {"긴 문서"} | {f"메모{i}" for i in range(5)})
        self.assertFalse(src["truncated"])

    def test_sources_are_ordered_deterministically(self):
        for name in ("다", "가", "나"):
            ds.put_doc(self.store, owner=self.OWNER, title=name,
                       text=f"{self.TITLE} 을 {name} 에서 말한다.")
        items = self._ctx()["sources"]["items"]
        self.assertEqual([s["title"] for s in items], sorted(s["title"] for s in items))

    def test_a_node_with_no_documents_says_zero_rather_than_nothing(self):
        # 키가 없는 것과 0 은 다른 말이다 — 받는 쪽이 "못 읽었다"와 "없다"를 갈라야 한다.
        src = self._ctx()["sources"]
        self.assertEqual(src, {"total": 0, "items": [], "truncated": False, "offset": 0})

    def test_the_excerpt_limit_does_not_change_the_source_total(self):
        # 발췌를 더 달라고 해도 소스 총수는 그대로여야 한다(두 계약이 독립이라는 뜻).
        for i in range(6):
            ds.put_doc(self.store, owner=self.OWNER, title=f"문서{i}",
                       text=f"{self.TITLE} 에 관한 {i}번 글.")
        self.assertEqual(self._ctx(limit=1)["sources"]["total"],
                         self._ctx(limit=12)["sources"]["total"])

    def _source(self, index, owner=None, meta_owner=None):
        own = owner or self.OWNER
        did = f"{own}:source:{index:03}"
        cid = did + "#00000"
        self.store.run(
            "CREATE (c:Chunk {chunk_id:$cid, doc_id:$did, text:$text, "
            "owner_id:$own, chunk_index:0})",
            {"cid": cid, "did": did, "text": self.TITLE, "own": own})
        self.store.run(
            "CREATE (d:_DocMeta {doc_id:$did, title:$title, owner_id:$own})",
            {"did": did, "title": f"Source {index // 2:03}",
             "own": own if meta_owner is None else meta_owner})
        self.store.run(
            "MATCH (c:Chunk {chunk_id:$cid}), (n:Concept {concept_id:$nid}) "
            "CREATE (c)-[:MENTIONS {method:'title'}]->(n)",
            {"cid": cid, "nid": pkns.key(self.OWNER, "concept:lock")})
        return did

    def test_source_continuation_reaches_every_document_without_duplicates(self):
        expected = [self._source(i) for i in reversed(range(103))]
        self._source(999, owner="user:b")
        seen, offset = [], 0
        for size in (50, 50, 3):
            result = self._ctx(sources_offset=offset, limit=1)
            page = result["sources"]
            self.assertEqual(page["total"], 103)
            self.assertEqual(page["offset"], offset)
            self.assertEqual(len(page["items"]), size)
            self.assertLessEqual(len(result["excerpts"]), 1)
            seen.extend(page["items"])
            if page["truncated"]:
                self.assertGreater(page["next_offset"], offset)
                offset = page["next_offset"]
            else:
                self.assertNotIn("next_offset", page)
        self.assertEqual([i["doc_id"] for i in seen], sorted(expected))
        self.assertEqual(len({i["doc_id"] for i in seen}), 103)
        self.assertEqual(seen, sorted(seen, key=lambda i: (i["title"], i["doc_id"])))
        self.assertEqual(self._ctx(sources_offset=103)["sources"]["items"], [])
        self.assertEqual(self._ctx(sources_offset=-5)["sources"]["offset"], 0)
        denied = q.node_context(owner="user:b", node="concept:lock",
                                sources_offset=50, store=self.store)
        missing = q.node_context(owner="user:b", node="concept:missing",
                                 sources_offset=50, store=self.store)
        self.assertEqual(denied["sources"], missing["sources"])
        self.assertEqual(denied["sources"]["total"], 0)

    def test_source_continuation_boundary_51(self):
        for i in range(51):
            self._source(i)
        first = self._ctx()["sources"]
        self.assertEqual((first["total"], len(first["items"]), first["next_offset"]),
                         (51, 50, 50))
        last = self._ctx(sources_offset=first["next_offset"])["sources"]
        self.assertEqual(len(last["items"]), 1)
        self.assertFalse(last["truncated"])
        self.assertNotIn("next_offset", last)

    def test_shared_document_id_cannot_replace_another_owners_source(self):
        first = ds.put_doc(self.store, owner=self.OWNER, doc_id="source:shared",
                           title="Owned source", text=self.TITLE)
        self.assertTrue(first["executed"], first)
        rejected = ds.put_doc(self.store, owner="user:b", doc_id="source:shared",
                              title="Foreign secret", text=self.TITLE)
        self.assertFalse(rejected["executed"], rejected)
        self.assertIn("doc_id unavailable", rejected["error"])
        self.assertEqual(self._ctx()["sources"]["items"][0]["title"], "Owned source")
        denied = q.node_context(owner="user:b", node="concept:lock", store=self.store)
        self.assertEqual(denied["sources"]["items"], [])
        self.assertNotIn("Foreign secret", str(self._ctx()))

    def test_a_source_title_comes_only_from_my_own_or_ownerless_metadata(self):
        # VIN-233 — 청크는 내 것인데 같은 doc_id 의 `_DocMeta` 는 남의 것인 상태(메타 없이
        # 남은 꼬리 청크 위에 남이 같은 id 로 쓴 경우, RV-12). `_DocMeta.doc_id` 는 소유자로
        # 네임스페이싱되지 않으므로 제목 조인에 owner 가 없으면 남의 제목이 내 목록에 뜬다.
        foreign = self._source(0, meta_owner="user:b")    # 제목 "Source 000"
        legacy = self._source(2, meta_owner="")           # 제목 "Source 001"
        res = self._ctx()
        self.assertNotIn("Source 000", str(res))
        self.assertEqual({s["doc_id"]: s["title"] for s in res["sources"]["items"]},
                         {foreign: foreign, legacy: "Source 001"})
        self.assertEqual({e["doc_id"]: e["doc_title"] for e in res["excerpts"]},
                         {foreign: foreign, legacy: "Source 001"})

    def test_decision_sources_use_the_same_continuation_contract(self):
        self.store.run(
            "CREATE (n:Decision {decision_id:$id, title:'Choice', owner_id:$owner})",
            {"id": pkns.key(self.OWNER, "decision:choice"), "owner": self.OWNER})
        for i in range(51):
            did = self._source(i)
            self.store.run(
                "MATCH (c:Chunk {chunk_id:$cid}), (n:Decision {decision_id:$nid}) "
                "CREATE (c)-[:MENTIONS {method:'title'}]->(n)",
                {"cid": did + "#00000", "nid": pkns.key(self.OWNER, "decision:choice")})
        result = q.node_context(owner=self.OWNER, node="decision:choice",
                                sources_offset=50, store=self.store)
        self.assertTrue(result["executed"], result)
        self.assertEqual(result["sources"]["total"], 51)
        self.assertEqual(len(result["sources"]["items"]), 1)
        self.assertFalse(result["sources"]["truncated"])

    def test_non_mention_carriers_have_no_invented_sources(self):
        for label, pk, node in (("Record", "record_id", "record:plain"),
                                ("Episode", "episode_id", "episode:plain")):
            self.store.run(
                f"CREATE (n:{label} {{{pk}:$id, title:'Plain', owner_id:$owner}})",
                {"id": pkns.key(self.OWNER, node), "owner": self.OWNER})
            result = q.node_context(owner=self.OWNER, node=node, sources_offset=50,
                                    store=self.store)
            self.assertTrue(result["executed"], result)
            self.assertEqual(result["sources"],
                             {"total": 0, "items": [], "offset": 50, "truncated": False})

    def test_http_adapter_forwards_source_and_relation_offsets_separately(self):
        from test_server import _EmptyStore, _app, _req, running_server
        from unittest.mock import patch
        with patch("api.server.q.node_context", return_value={"executed": True}) as context:
            with running_server(_app(_EmptyStore())) as base:
                code, result = _req(base, "/node-context?owner=user:a&node=concept:lock"
                                    "&sources_offset=50&links_offset=24&limit=1")
        self.assertEqual(code, 200)
        self.assertTrue(result["executed"])
        self.assertEqual(context.call_args.kwargs["sources_offset"], 50)
        self.assertEqual(context.call_args.kwargs["links_offset"], 24)
        self.assertEqual(context.call_args.kwargs["limit"], 1)


if __name__ == "__main__":
    unittest.main()
