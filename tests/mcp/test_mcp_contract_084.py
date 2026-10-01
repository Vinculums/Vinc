# -*- coding: utf-8 -*-
"""v0.8.4 축2 — 2026-08-22 도구 실행 평가가 찾아낸 계약 결함 다섯(항목 4~8).

각 항목은 **두 자리를 함께** 잰다: 서버가 실제로 그렇게 동작하는가, 그리고 호스트가
받는 도구 설명이 그것을 말하는가. 둘 중 하나만 재면 여기 있는 결함들이 다시 난다 —
전부 "설명은 멀쩡한데 동작이 다르다" 또는 그 반대였고, 어느 쪽도 오류를 내지 않았다.

  4. `vinc_neighbors` 가 ATTACHES_TO·ABOUT 을 한쪽에서만 폈다. Record·Episode 시드는
     관계가 있는데도 이웃 0개를 냈고, 그 응답은 "관계 없음"과 구분되지 않았다.
  5. `vinc_conventions` 의 생성 구역이 저장 시점에 굳어 도메인 목록이 낡았다.
  6. `vinc_doc_extract` 후보가 절 번호·마크다운을 그대로 물고 나오고, one_liner 가
     줄바꿈에서 잘려 반 문장이었다.
  7. `vinc_decide` 의 conflicts 가 크로스도메인 하나뿐이라, 진짜 상충(같은 주제의
     결정 둘·역방향 유향 엣지·제목 중복)은 한 번도 보고되지 않았다.
  8. 문서를 쓰면 벡터 인덱스가 드롭되는데, 그 뒤 첫 검색이 재빌드를 **요청 스레드에서**
     기다렸다.
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import docstore as ds           # noqa: E402
from api import fragment as frag         # noqa: E402
from api import graphmd                  # noqa: E402
from api import query as q               # noqa: E402
from api.server import VincApp           # noqa: E402
from vinc_mcp.rpc import _tool_specs     # noqa: E402

#: 호스트가 tools/list 로 **실제로 받는** 설명(test_tool_contract 와 같은 경로).
SERVED = {t["name"]: t for t in _tool_specs()}

OWNER = "user:w2"


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
    return store, cfg, keep


def _restore(keep):
    for k, v in keep.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# ── 항목 4 — 부착 축은 양쪽에서 보인다 ────────────────────────────────────────
class NeighborsBothEnds(unittest.TestCase):
    def setUp(self):
        self.store, self.cfg, self._keep = _fresh_store()
        res = frag.merge_fragment({
            "owner": OWNER,
            "nodes": [
                {"id": "concept:view", "type": "concept", "title": "View",
                 "domain": "vinc/design"},
                {"id": "concept:billing", "type": "concept", "title": "Billing",
                 "domain": "vinc/product"},
                {"id": "record:memo", "type": "record", "title": "Memo",
                 "domain": "vinc/design"},
                {"id": "episode:s1", "type": "episode", "title": "Session 1",
                 "domain": "vinc/design", "date": "2026-08-22"},
                # Bridge 는 domain 을 fragment 로 못 받는다(source/target_domain 이 따로).
                {"id": "bridge:b1", "type": "bridge", "title": "Bridge one"},
            ],
            "edges": [
                {"from": "record:memo", "to": "concept:view", "type": "relates_to"},
                {"from": "episode:s1", "to": "concept:view", "type": "about"},
                {"from": "bridge:b1", "to": "concept:view", "type": "connects_via"},
                {"from": "bridge:b1", "to": "concept:billing", "type": "connects_via"},
            ],
        }, store=self.store)
        self.assertFalse(res.get("warnings"), res.get("warnings"))

    def tearDown(self):
        self.store.close()
        _restore(self._keep)

    def _n(self, node):
        return q.neighbors(node, owner=OWNER, cfg=self.cfg, store=self.store)

    def test_개념_시드는_종전대로_부착을_본다(self):
        """회귀 가드 — 역방향을 붙이면서 정방향을 잃으면 F14 가 되돌아간다."""
        got = self._n("concept:view")
        self.assertTrue(got["found"], got)
        self.assertEqual(got["by_rel"].get("ATTACHES_TO"), 1, got)
        self.assertEqual(got["by_rel"].get("ABOUT"), 1, got)
        self.assertEqual(got["by_rel"].get("CONNECTS"), 1, got)

    def test_기록_시드가_붙어_있는_개념을_돌려준다(self):
        got = self._n("record:memo")
        self.assertTrue(got["found"], got)
        self.assertEqual([(n["rel"], n["id"]) for n in got["neighbors"]],
                         [("ATTACHES_TO", "concept:view")], got)
        self.assertEqual(got["neighbors"][0]["title"], "View",
                         "제목이 안 실리면 상류는 id 만 보고 다음 홉을 고른다")

    def test_사건_시드가_ABOUT_대상을_돌려준다(self):
        got = self._n("episode:s1")
        self.assertEqual([(n["rel"], n["id"]) for n in got["neighbors"]],
                         [("ABOUT", "concept:view")], got)

    def test_다리_시드가_잇는_양쪽을_돌려준다(self):
        got = self._n("bridge:b1")
        self.assertEqual(sorted(n["id"] for n in got["neighbors"]),
                         ["concept:billing", "concept:view"], got)

    def test_관계가_정말_없을_때만_빈다(self):
        """빈 응답의 뜻이 하나여야 한다 — 여태 '반대편에서 물었다'도 여기로 왔다."""
        frag.merge_fragment({"owner": OWNER,
                             "nodes": [{"id": "record:lonely", "type": "record",
                                        "title": "Lonely"}]}, store=self.store)
        got = self._n("record:lonely")
        self.assertEqual(got["count"], 0, got)
        self.assertTrue(got["found"], "있는 노드를 없다고 하면 안 된다")

    def test_남의_부착은_스코프_밖이다(self):
        frag.merge_fragment({
            "owner": "user:other",
            "nodes": [{"id": "record:theirs", "type": "record", "title": "Theirs"},
                      {"id": "concept:theirs", "type": "concept", "title": "Theirs"}],
            "edges": [{"from": "record:theirs", "to": "concept:theirs",
                       "type": "relates_to"}],
        }, store=self.store)
        self.assertEqual(self._n("record:theirs")["count"], 0,
                         "역방향 플랜이 owner 스코프를 안 걸었다(SEC-1)")

    def test_설명이_양방향을_말한다(self):
        desc = SERVED["vinc_neighbors"]["description"]
        self.assertIn("BOTH ends", desc)
        for word in ("record", "episode", "bridge"):
            self.assertIn(word, desc, f"{word} 시드가 된다는 말이 없다")


# ── 항목 5 — 관례의 생성 구역은 매 호출에 다시 계산된다 ───────────────────────
class ConventionsAreRecomputed(unittest.TestCase):
    def setUp(self):
        self.store, self.cfg, self._keep = _fresh_store()
        self.app = VincApp(self.cfg, store=self.store)
        frag.merge_fragment({
            "owner": OWNER,
            "nodes": [{"id": "concept:a", "type": "concept", "title": "A",
                       "domain": "design"}],
        }, store=self.store)
        built = graphmd.build(self.store, owner=OWNER, themes=self.app.themes,
                              day="2026-01-01")
        self.assertIn("design", built["text"])
        ds.put_doc(self.store, owner=OWNER, text=built["text"],
                   doc_id=graphmd.conventions_doc_id(OWNER), title="GRAPH.md",
                   origin=graphmd.CONVENTIONS_ORIGIN)

    def tearDown(self):
        self.app.jobs.shutdown(drain=False)
        self.store.close()
        _restore(self._keep)

    def _read(self):
        code, res = self.app.read_conventions({"owner": OWNER})
        self.assertEqual(code, 200, res)
        return res

    def test_저장_뒤에_생긴_도메인이_보인다(self):
        before = self._read()
        self.assertNotIn("infra", before["text"])
        frag.merge_fragment({
            "owner": OWNER,
            "nodes": [{"id": "concept:b", "type": "concept", "title": "B",
                       "domain": "infra"}],
        }, store=self.store)
        after = self._read()
        self.assertEqual(after["source"], "doc")
        self.assertTrue(after["generated_recomputed"], after)
        self.assertIn("infra", after["text"],
                      "생성 구역이 저장 시점에 굳어 있다 — 낡은 도메인 목록으로 쓰게 된다")

    def test_저장된_built_날짜는_라벨로_남는다(self):
        """날짜를 새로 찍으면 응답이 저장본에 없는 날을 말하게 된다."""
        self.assertIn("built 2026-01-01", self._read()["text"])

    def test_산문은_그대로다(self):
        got = self._read()["text"]
        self.assertIn("Do not ratify your own proposal", got)

    def test_마커가_깨진_문서는_손대지_않는다(self):
        ds.put_doc(self.store, owner=OWNER, text="# GRAPH.md\n\n손으로 쓴 것\n",
                   doc_id=graphmd.conventions_doc_id(OWNER), title="GRAPH.md",
                   origin=graphmd.CONVENTIONS_ORIGIN)
        got = self._read()
        self.assertFalse(got["generated_recomputed"])
        self.assertIn("손으로 쓴 것", got["text"])

    def test_설명이_재계산을_말한다(self):
        desc = SERVED["vinc_conventions"]["description"]
        self.assertIn("recomputed", desc)
        self.assertIn("every call", desc)


# ── 항목 6 — 후보는 읽을 수 있는 모양으로 나온다 ──────────────────────────────
class ExtractCandidates(unittest.TestCase):
    def test_절_번호와_글머리를_벗긴다(self):
        text = ("# Doc\n\n## 1. The decisions\nFirst.\n\n"
                "## 3.1 Two id formulas\nSecond.\n\n"
                # ⚠ "Overview" 였다 — 구조어 필터(_concept_shaped)가 이제 걸러 낸다.
                # 이 테스트의 관심은 글머리(`A)`) 벗기기이므로 비구조어로 바꾼다.
                "## A) Key invariants\nThird.\n")
        got = ds.extract_concepts(text)
        self.assertEqual([c["title"] for c in got],
                         ["The decisions", "Two id formulas", "Key invariants"])
        self.assertEqual([c["slug"] for c in got],
                         ["the-decisions", "two-id-formulas", "key-invariants"],
                         "slug 은 정리된 제목에서 나온다 — 절 번호는 문서 안에서만 뜻이 있다")

    def test_숫자로_시작하는_진짜_제목은_지킨다(self):
        """`12 factors` 의 12 는 절 번호가 아니다 — 끊어 주는 부호가 없다."""
        got = ds.extract_concepts("# Doc\n\n## 12 factors of X\nBody.\n")
        self.assertEqual([c["title"] for c in got], ["12 factors of X"])

    def test_인라인_마크를_푼다(self):
        text = ("# Doc\n\n## The **chunk** is `canon`\n"
                "See [the spec](http://x) and [[vinc.canon.chunk-ssot]].\n")
        got = ds.extract_concepts(text)
        self.assertEqual(got[0]["title"], "The chunk is canon")
        self.assertEqual(got[0]["one_liner"],
                         "See the spec and vinc.canon.chunk-ssot.")

    def test_one_liner_가_문장_끝까지_간다(self):
        text = ("# Doc\n\n## Identity\n"
                "A document joins on its id,\nnot on a path.\nAnother sentence.\n")
        self.assertEqual(ds.extract_concepts(text)[0]["one_liner"],
                         "A document joins on its id, not on a path.")

    def test_이미_문장으로_끝난_줄은_그대로다(self):
        text = "# Doc\n\n## Identity\nOne. Two.\nNext line.\n"
        self.assertEqual(ds.extract_concepts(text)[0]["one_liner"], "One. Two.")

    def test_설명_없는_헤딩은_후보가_아니다(self):
        text = "# Doc\n\n## Empty\n\n## Full\nHas a body.\n"
        got = ds.extract_concepts(text)
        self.assertEqual([c["title"] for c in got], ["Full"])
        self.assertTrue(all(c["one_liner"] for c in got))

    def test_인용부호로_시작하는_본문도_읽는다(self):
        text = "# Doc\n\n## Canon\n> The chunk is the source of truth.\n"
        self.assertEqual(ds.extract_concepts(text)[0]["one_liner"],
                         "The chunk is the source of truth.")

    def test_결정론이다(self):
        text = ("# Doc\n\n## 1. One\nBody one,\nwrapped.\n\n## 2. Two\nBody two.\n")
        self.assertEqual(ds.extract_concepts(text), ds.extract_concepts(text))

    def test_설명이_그_규칙을_말한다(self):
        desc = SERVED["vinc_doc_extract"]["description"]
        self.assertIn("section number", desc)
        self.assertIn("first sentence", desc)
        self.assertIn("one_liner", desc)


# ── 항목 7 — 상충은 세 가지, 크로스도메인은 상충이 아니다 ─────────────────────
class DecideConflicts(unittest.TestCase):
    def setUp(self):
        self.store, self.cfg, self._keep = _fresh_store()
        res = frag.merge_fragment({
            "owner": OWNER,
            "nodes": [
                {"id": "concept:cache", "type": "concept", "title": "Cache",
                 "domain": "vinc/engineering"},
                {"id": "concept:billing", "type": "concept", "title": "Billing",
                 "domain": "vinc/product"},
                {"id": "decision:keep", "type": "decision", "title": "Drop the cache",
                 "domain": "vinc/engineering"},
                {"id": "decision:drop", "type": "decision", "title": "Drop the cache",
                 "domain": "vinc/engineering"},
            ],
            "edges": [
                {"from": "decision:keep", "to": "concept:cache", "type": "about"},
                {"from": "decision:drop", "to": "concept:cache", "type": "about"},
                # 도메인을 가로지르지만 상충은 아니다
                {"from": "concept:cache", "to": "concept:billing", "type": "relates_to"},
                # 방향이 반대인 유향 엣지 한 쌍
                {"from": "concept:cache", "to": "concept:billing",
                 "type": "prerequisite_of"},
                {"from": "concept:billing", "to": "concept:cache",
                 "type": "prerequisite_of"},
            ],
        }, store=self.store)
        self.assertFalse(res.get("warnings"), res.get("warnings"))

    def tearDown(self):
        self.store.close()
        _restore(self._keep)

    def _decide(self):
        got = q.decide("concept:cache", depth=2, owner=OWNER, cfg=self.cfg,
                       store=self.store)
        self.assertTrue(got["found"], got)
        return got

    def test_같은_주제의_결정_둘을_상충으로_낸다(self):
        got = self._decide()
        same = [c for c in got["conflicts"]
                if c["reason"] == "decisions_on_same_subject"]
        self.assertEqual(len(same), 1, got["conflicts"])
        self.assertEqual({same[0]["source"], same[0]["target"]},
                         {"decision:drop", "decision:keep"})
        self.assertEqual(same[0]["subject"], "concept:cache")

    def test_역방향_유향_엣지를_상충으로_낸다(self):
        opp = [c for c in self._decide()["conflicts"]
               if c["reason"] == "opposite_direction"]
        self.assertEqual(len(opp), 1, opp)
        self.assertEqual(opp[0]["type"], "PREREQUISITE_OF")
        self.assertEqual(opp[0]["reverse_type"], "PREREQUISITE_OF")

    def test_제목이_같은_결정을_상충으로_낸다(self):
        dup = [c for c in self._decide()["conflicts"]
               if c["reason"] == "duplicate_decision_title"]
        self.assertEqual(len(dup), 1, dup)
        self.assertEqual(dup[0]["title"], "Drop the cache")

    def test_크로스도메인은_제_필드로_나간다(self):
        got = self._decide()
        self.assertNotIn("cross_domain", [c["reason"] for c in got["conflicts"]])
        self.assertTrue(got["cross_domain"], got)
        pairs = {(e["source"], e["target"]) for e in got["cross_domain"]}
        self.assertIn(("concept:cache", "concept:billing"), pairs)

    def test_무방향_관계는_모순이_아니다(self):
        """RELATED_TO 는 역방향이 같은 간선으로 접힌다 — 넣으면 정상 관계가 전부 상충이 된다."""
        conflicts, cut = q._decide_conflicts(
            [{"source": "a", "target": "b", "type": "RELATED_TO"},
             {"source": "b", "target": "a", "type": "RELATED_TO"}], [])
        self.assertEqual(conflicts, [])
        self.assertFalse(cut)

    def test_상한을_넘으면_잘렸다고_말한다(self):
        decs = [{"id": f"decision:d{i}", "title": f"T{i}", "subject": "concept:x"}
                for i in range(20)]
        conflicts, cut = q._decide_conflicts([], decs)
        self.assertEqual(len(conflicts), q._CONFLICT_CAP)
        self.assertTrue(cut)

    def test_설명이_세_상충과_크로스도메인_필드를_말한다(self):
        desc = SERVED["vinc_decide"]["description"]
        for token in ("decisions_on_same_subject", "opposite_direction",
                      "duplicate_decision_title", "cross_domain"):
            self.assertIn(token, desc)


# ── 결정도 토픽이다 — 해석이 Concept 만 물어 결정 id 가 제 자신을 후보로 냈다 ────────
class DecideOnDecision(unittest.TestCase):
    """2026-09-29 실측: `decision:...` 을 주면 found:false 에 candidates 첫 줄이 **그 id**
    였고, 전체 제목도 못 찾았다. 해석 플랜이 `(c:Concept)` 만 물었기 때문이다."""

    def setUp(self):
        self.store, self.cfg, self._keep = _fresh_store()
        res = frag.merge_fragment({
            "owner": OWNER,
            "nodes": [
                {"id": "concept:cache", "type": "concept", "title": "Cache",
                 "domain": "vinc/engineering"},
                {"id": "concept:billing", "type": "concept", "title": "Billing",
                 "domain": "vinc/product"},
                {"id": "decision:keep", "type": "decision", "title": "Keep the cache",
                 "domain": "vinc/engineering"},
                {"id": "decision:drop", "type": "decision", "title": "Drop the cache",
                 "domain": "vinc/engineering"},
                {"id": "decision:lone", "type": "decision", "title": "A lone call",
                 "domain": "vinc/engineering"},
            ],
            "edges": [
                {"from": "decision:keep", "to": "concept:cache", "type": "about"},
                {"from": "decision:drop", "to": "concept:cache", "type": "about"},
                {"from": "decision:keep", "to": "concept:billing",
                 "type": "prerequisite_of"},
            ],
        }, store=self.store)
        # 경고는 하나뿐이어야 한다 — 일부러 ABOUT 없이 둔 decision:lone.
        warns = res.get("warnings") or []
        self.assertEqual(len(warns), 1, warns)
        self.assertIn("decision:lone", warns[0])

    def tearDown(self):
        self.store.close()
        _restore(self._keep)

    def _decide(self, topic, depth=2):
        return q.decide(topic, depth=depth, owner=OWNER, cfg=self.cfg, store=self.store)

    def test_결정_id_로_찾고_제_엣지를_싣는다(self):
        got = self._decide("decision:keep")
        self.assertTrue(got["found"], got)
        kinds = {n["id"]: n["kind"] for n in got["nodes"]}
        self.assertEqual(kinds.get("decision:keep"), "decision", got["nodes"])
        self.assertIn("concept:cache", kinds)
        edges = {(e["source"], e["type"], e["target"]) for e in got["edges"]}
        self.assertIn(("decision:keep", "ABOUT", "concept:cache"), edges)
        self.assertIn(("decision:keep", "PREREQUISITE_OF", "concept:billing"), edges)
        # 같은 주제를 두고 내려진 다른 결정이 상충으로 올라온다 — 이 도구의 요점.
        same = [c for c in got["conflicts"] if c["reason"] == "decisions_on_same_subject"]
        self.assertEqual(len(same), 1, got["conflicts"])

    def test_결정_제목으로도_찾는다(self):
        got = self._decide("keep the CACHE")
        self.assertTrue(got["found"], got)
        self.assertIn("decision:keep", {n["id"] for n in got["nodes"]})

    def test_엣지_없는_결정도_노드로_선다(self):
        # depth 1 — 2 부터는 무라벨 순회가 Domain 노드를 지나 같은 주제의 개념에 닿는다
        # (개념 시드와 같은 공유 의미론, `subgraph_hops_plan` 주석).
        got = self._decide("decision:lone", depth=1)
        self.assertTrue(got["found"], got)
        self.assertEqual([(n["id"], n["label"]) for n in got["nodes"]],
                         [("decision:lone", "A lone call")])


# ── 항목 8 — 쓰기 직후 검색은 FTS 로 답하고 재빌드를 기다리지 않는다 ──────────
class _IndexMissing(RuntimeError):
    """실측 문구 그대로 — `_classify_chunk_error` 가 이 모양으로 분류한다."""

    def __init__(self):
        super().__init__("Table ChunkVec doesn't have an index with name chunk_vec")


class _StoreWithNoVectorIndex:
    scoped_chunk_search = True

    def __init__(self):
        self.scheduled: list = []
        self.built_inline: list = []

    def run(self, *_a, **_k):
        return [{"c": 1}]

    def fulltext_search(self, _q, top_k, _filters=None):
        return [{"id": "doc-1#00000", "text": "keyword hit", "score": 1.0}]

    def vector_search(self, *_a, **_k):
        raise _IndexMissing()

    def schedule_search_indexes(self, *, dim=None):
        self.scheduled.append(dim)

    def ensure_search_indexes(self, *, dim=None):
        self.built_inline.append(dim)
        return {}

    def close(self):
        pass


class SearchFallsBackWithoutWaiting(unittest.TestCase):
    def setUp(self):
        self.store = _StoreWithNoVectorIndex()
        from store.config import load_config
        self.cfg = load_config()

    def _search(self):
        return q.search("anything", query_embedding=[0.1] * 4, top_k=5,
                        cfg=self.cfg, store=self.store)

    def test_키워드_결과를_버리지_않는다(self):
        got = self._search()
        self.assertTrue(got["executed"], got)
        self.assertEqual([r["id"] for r in got["rows"]], ["doc-1#00000"], got)

    def test_강등을_말한다(self):
        got = self._search()
        self.assertTrue(got.get("vector_note"), "조용히 강등됐다 — 사유가 없다")
        self.assertEqual(got["arms"], "fts",
                         "벡터 팔이 안 돌았는데 응답은 돌았다고 말한다")

    def test_재빌드는_예약이지_대기가_아니다(self):
        self._search()
        self.assertEqual(len(self.store.scheduled), 1, self.store.scheduled)
        self.assertFalse(self.store.built_inline,
                         "요청 스레드에서 HNSW 를 만들었다 — 이게 타임아웃의 원인이었다")

    def test_예약_창구가_없으면_그_자리에서_만든다(self):
        """CLI·테스트가 직접 연 백엔드에는 기다릴 요청 스레드가 없다."""
        self.store.schedule_search_indexes = None   # 예약 창구 없는 스토어
        self._search()
        self.assertEqual(self.store.built_inline, [self.cfg.embedding_dim])

    def test_예약_실패는_검색_실패가_아니다(self):
        def boom(**_k):
            raise RuntimeError("job queue is gone")
        self.store.schedule_search_indexes = boom
        self.assertTrue(self._search()["executed"])

    def test_리더_뷰는_타임아웃_0_으로_예약한다(self):
        from store.embedded_backend import _ReaderView
        seen: list = []
        rv = _ReaderView.__new__(_ReaderView)
        rv._b = types.SimpleNamespace(ensure_search_indexes=lambda **_k: {})
        rv._conn = None
        rv._writer = lambda label, fn, timeout: seen.append((label, timeout))
        rv.schedule_search_indexes(dim=4)
        self.assertEqual(seen, [("search.index", 0.0)])

    def test_쓰기_지점은_타임아웃_0_을_예약으로_읽는다(self):
        class _Jobs:
            def __init__(self):
                self.submitted, self.waited = [], []

            def submit(self, label, _fn):
                self.submitted.append(label)
                return types.SimpleNamespace(to_dict=lambda: {"status": "queued"})

            def run_sync(self, label, fn, timeout=None):
                self.waited.append((label, timeout))
                return fn()

        app = types.SimpleNamespace(jobs=_Jobs())
        VincApp._at_write_point(app, "search.index", lambda: {}, 0.0)
        self.assertEqual(app.jobs.submitted, ["search.index"])
        self.assertFalse(app.jobs.waited)
        VincApp._at_write_point(app, "graph.algo", lambda: {}, 60.0)
        self.assertEqual(app.jobs.waited, [("graph.algo", 60.0)])

    def test_벡터_아닌_실패는_사유로_말하고_키워드_결과는_지킨다(self):
        """인덱스 부재만 **양성** 강등이다. 그 밖의 실패도 삼키지 않고 `vector_note` 로
        말하되, 이미 구한 키워드 결과는 버리지 않는다(RV-06: 벡터 팔 하나의 고장이 검색
        전체의 `error` 가 되면 REST 는 500 을 낸다)."""
        def other(*_a, **_k):
            raise RuntimeError("connection reset")
        self.store.vector_search = other
        got = self._search()
        self.assertFalse(got.get("error"), got)
        self.assertEqual([r["id"] for r in got["rows"]], ["doc-1#00000"], got)
        self.assertIn("connection reset", str(got.get("vector_note") or ""), got)
        self.assertNotIn("vector_rebuilding", got, "진짜 실패를 재빌드 창으로 그렸다")
        self.assertFalse(self.store.scheduled, "실패를 재빌드로 착각해 예약했다")

    def test_설명이_그_계약을_말한다(self):
        desc = SERVED["vinc_search"]["description"]
        self.assertIn("vector_note", desc)
        self.assertIn("never waits", desc)


if __name__ == "__main__":
    unittest.main()
