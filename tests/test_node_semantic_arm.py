# -*- coding: utf-8 -*-
"""노드 층 의미 팔 — 어휘가 하나도 안 겹쳐도 닿는 유일한 팔.

**왜 있는가.** 노드 층에는 색인이 없었다. 제목 검색은 CONTAINS 전수 스캔이고 점수는 맞은
토큰을 세는 것이라, 질문과 노드가 낱말을 안 나누면 그 노드는 어떤 팔로도 후보가 못 된다.
실측(정답 66문항): 답 가능 60건 중 39건이 그 상태였고, 제목을 임베딩하니 22건이 상위 5에
돌아왔다.

**이 파일이 지키는 것 셋**: 미러가 owner 를 새지 않는다 · 팔이 없거나 비어도 검색이 안
죽는다 · 기본이 꺼져 있다. 마지막 것이 중요하다 — 이 세션에 후보 집합을 두 번 넓혔고
두 번 다 정직성 지표가 따라 움직였다.

근거: [[record:the-node-semantic-arm-lifts-candidacy-not-answers-2026-09-09]]
"""
from __future__ import annotations

import os
import tempfile
import unittest

from api import fragment as frag
from api import query as q

try:
    import ladybug                          # noqa: F401
    _DRIVER = True
except Exception:                           # pragma: no cover
    _DRIVER = None

DIM = 4


def _vec(*xs):
    """길이 DIM 의 단위 벡터를 손으로 만든다 — 모델을 부르지 않는다."""
    v = list(xs) + [0.0] * (DIM - len(xs))
    n = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / n for x in v]


class FlagDefault(unittest.TestCase):
    """기본이 꺼져 있는가. 스토어 없이 도는 순수 판정이라 여기서 못 박는다."""

    def setUp(self):
        self._prev = os.environ.pop("VINC_NODE_SEMANTIC_ARM", None)

    def tearDown(self):
        os.environ.pop("VINC_NODE_SEMANTIC_ARM", None)
        if self._prev is not None:
            os.environ["VINC_NODE_SEMANTIC_ARM"] = self._prev

    def test_the_arm_is_on_unless_switched_off(self):
        """2026-09-10 에 뒤집혔다 — 팔 자신의 1-2위 격차를 순서에 넣자 노출 R@1 이
        0.2833 → 0.4833 이 됐다. 미러가 비어 있어도 이 팔은 빈 목록이지 예외가
        아니라서(`store.node_vector_search`) 켜 두는 것이 안전하다."""
        self.assertTrue(q._node_semantic_arm_on())

    def test_the_usual_truthy_spellings_keep_it_on(self):
        for v in ("on", "1", "true", "yes", "ON", " on "):
            os.environ["VINC_NODE_SEMANTIC_ARM"] = v
            self.assertTrue(q._node_semantic_arm_on(), v)

    def test_the_off_spellings_are_the_escape_hatch(self):
        """`bool("false")` 가 True 인 것이 이 자리의 함정이다. 빈 문자열은 미지정과
        같으므로 **끄지 않는다** — 이 뒤집힘에서 갈리는 자리다."""
        for v in ("off", "false", "0", "no", " OFF "):
            os.environ["VINC_NODE_SEMANTIC_ARM"] = v
            self.assertFalse(q._node_semantic_arm_on(), v)
        os.environ["VINC_NODE_SEMANTIC_ARM"] = ""
        self.assertTrue(q._node_semantic_arm_on())


class FusionShape(unittest.TestCase):
    """융합이 세 팔을 받고, 팔이 비어도 하위호환을 지키는가."""

    def rows(self, *ids):
        return [{"id": i, "title": i, "hit_tokens": 1, "title_tokens": 1} for i in ids]

    def test_title_only_keeps_its_own_order(self):
        out = q._fuse_node_hits(self.rows("a", "b"), [], top_k=5)
        self.assertEqual([r["id"] for r in out], ["a", "b"])
        self.assertEqual(out[0]["arms"], ["title"])

    def test_the_semantic_arm_alone_still_answers(self):
        """제목 팔이 0건이어도 의미 팔만으로 후보가 선다 — 그것이 이 팔의 존재 이유다."""
        out = q._fuse_node_hits([], [], top_k=5, vec_hits=self.rows("z"))
        self.assertEqual([r["id"] for r in out], ["z"])
        self.assertEqual(out[0]["arms"], ["semantic"])

    def test_the_graph_arm_alone_still_answers(self):
        """제목 팔이 비고 그래프 팔만 찬 경우 — 융합 이전 동작이 이것이었다."""
        out = q._fuse_node_hits([], self.rows("g"), top_k=5)
        self.assertEqual([r["id"] for r in out], ["g"])
        self.assertEqual(out[0]["arms"], ["graph"])

    def test_no_arm_found_anything(self):
        self.assertEqual(q._fuse_node_hits([], [], top_k=5, vec_hits=[]), [])

    def test_membership_names_every_arm_that_found_it(self):
        out = q._fuse_node_hits(self.rows("a"), self.rows("a"), top_k=5,
                                vec_hits=self.rows("a"))
        self.assertEqual(out[0]["arms"], ["graph", "semantic", "title"])

    def test_a_semantically_decisive_row_is_ordered_first(self):
        """의미 팔이 **자기 기준으로 단호할 때만** 앞에 놓는다. 절대 거리는 못 쓴다 —
        완전 일치가 0.077 인데 아무 말이나 던져도 0.170 이라 크게 겹친다. 가르는 것은
        1위가 2위보다 뚜렷하게 가까운가이고, 그 격차는 맞혔을 때 0.0215 · 틀렸을 때
        0.0047 로 4.6배 갈린다(실측 2026-09-10)."""
        hits = [{"id": "record:noisy", "title": "노이즈", "hit_tokens": 1,
                 "title_tokens": 1, "arms": ["title"]},
                {"id": "concept:right", "title": "정답", "hit_tokens": 0,
                 "title_tokens": 0, "arms": ["semantic"], "sem_decisive": True}]
        self.assertEqual(q._brief_resolve("아무 질문", hits)[1][0]["id"], "concept:right")

    def test_decisiveness_reorders_but_never_confirms(self):
        """단호함은 **순서만** 바꾼다. 답 불가 21문항 중 9건도 0.010 격차를 통과하므로
        이것으로 확정을 주면 정직성이 무너진다."""
        hits = [{"id": "concept:right", "title": "정답", "hit_tokens": 0,
                 "title_tokens": 0, "arms": ["semantic"], "sem_decisive": True},
                {"id": "record:noisy", "title": "노이즈", "hit_tokens": 0,
                 "title_tokens": 0, "arms": ["title"]}]
        self.assertEqual(q._brief_resolve("아무 질문", hits)[0], "choose")

    def test_the_rank_flag_is_on_by_default(self):
        """팔과 한 몸이다 — 0.4833 은 둘 다 켠 값이고 팔만 켜면 0.2833 으로 돌아간다."""
        prev = os.environ.pop("VINC_NODE_SEM_RANK", None)
        try:
            self.assertTrue(q._node_sem_rank_on())
            os.environ["VINC_NODE_SEM_RANK"] = "off"
            self.assertFalse(q._node_sem_rank_on())
        finally:
            os.environ.pop("VINC_NODE_SEM_RANK", None)
            if prev is not None:
                os.environ["VINC_NODE_SEM_RANK"] = prev

    def test_the_gap_is_where_it_was_measured(self):
        """옮기려면 다시 재야 한다 — 0.025 에서는 노출 R@1 이 0.4833 에서 0.3167 로 내린다."""
        self.assertEqual(q._SEM_GAP, 0.010)

    def test_two_arms_fuse_rather_than_concatenate(self):
        out = q._fuse_node_hits(self.rows("a", "b"), [], top_k=5,
                                vec_hits=self.rows("b", "c"))
        ids = [r["id"] for r in out]
        self.assertIn("c", ids, "의미 팔만 아는 것이 빠졌다")
        self.assertEqual(ids[0], "b", "두 팔이 함께 올린 것이 1위여야 한다")


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class MirrorStore(unittest.TestCase):
    OWNER = "user:node-mirror"
    OTHER = "user:someone-else"

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_DATA_DIR": os.path.join(self._tmp, "store")}.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        from store.backend import open_store
        from store.config import load_config
        self.store = open_store(load_config())
        self.store.provision(dim=DIM)
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "concept:mine", "type": "concept", "title": "mine"},
        ]}, store=self.store)
        self.store.drop_node_vector_index()
        self.store.run(
            "UNWIND $rows AS r CREATE (:NodeVec {node_id: r.id, kind: 'concept', "
            "owner_id: r.o, title: r.t, text: r.x, model: 'test', embedding: r.emb})",
            # 제목과 찾는 표면을 **일부러 다르게** 둔다 — 라벨이 어느 쪽에서 오는지
            # 가르는 것이 아래 test_the_label_is_the_title 이다.
            {"rows": [{"id": "concept:mine", "o": self.OWNER, "t": "mine",
                       "x": "mine. a one liner that is not the title", "emb": _vec(1, 0)},
                      {"id": "concept:theirs", "o": self.OTHER, "t": "theirs",
                       "x": "theirs", "emb": _vec(1, 0)}]})
        self.store.create_node_vector_index()

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_the_mirror_never_leaks_another_owner(self):
        """미러는 노드의 사본이다. 스코프가 새면 남의 그래프 제목이 후보로 올라온다."""
        hits = self.store.node_vector_search(_vec(1, 0), 5, owner=self.OWNER)
        self.assertEqual([h["id"] for h in hits], ["concept:mine"])

    def test_it_finds_by_vector_not_by_shared_words(self):
        """제목과 한 글자도 안 겹치는 질의 벡터로도 닿는다 — 이 팔의 요점이다."""
        hits = self.store.node_vector_search(_vec(0.99, 0.01), 5, owner=self.OWNER)
        self.assertEqual(hits[0]["id"], "concept:mine")
        self.assertEqual(hits[0]["kind"], "concept")

    def test_a_missing_index_is_an_empty_list_not_a_crash(self):
        """이 팔은 증강이다. 없다고 검색이 죽으면 안 된다(그래프 팔과 같은 태도)."""
        self.store.drop_node_vector_index()
        self.assertEqual(self.store.node_vector_search(_vec(1, 0), 5,
                                                       owner=self.OWNER), [])

    def test_the_label_is_the_title_not_the_search_surface(self):
        """미러의 `text` 에는 한줄설명이 붙는다. 그것이 라벨 자리로 새면 후보 목록에
        "제목. 한줄설명…" 이 그대로 나간다(이 스토어 1365 중 225 행이 그 모양이다)."""
        os.environ["VINC_NODE_SEMANTIC_ARM"] = "on"
        try:
            res = q.search("nothing shares a word with this", owner=self.OWNER,
                           query_embedding=_vec(1, 0), top_k=5, store=self.store)
            row = next(h for h in res["node_hits"] if h["id"] == "concept:mine")
            self.assertEqual(row["title"], "mine")
        finally:
            os.environ.pop("VINC_NODE_SEMANTIC_ARM", None)

    def test_the_escape_hatch_actually_shuts_the_arm_off(self):
        """기본이 켜진 뒤로 이 시험의 값어치는 **끄는 길이 실재하는가**다."""
        prev = os.environ.get("VINC_NODE_SEMANTIC_ARM")
        os.environ["VINC_NODE_SEMANTIC_ARM"] = "off"
        try:
            res = q.search("nothing shares a word with this", owner=self.OWNER,
                           query_embedding=_vec(1, 0), top_k=5, store=self.store)
            arms = {a for h in (res.get("node_hits") or []) for a in h.get("arms", [])}
            self.assertNotIn("semantic", arms)
        finally:
            os.environ.pop("VINC_NODE_SEMANTIC_ARM", None)
            if prev is not None:
                os.environ["VINC_NODE_SEMANTIC_ARM"] = prev

    def test_search_uses_it_when_the_flag_is_on(self):
        os.environ["VINC_NODE_SEMANTIC_ARM"] = "on"
        try:
            res = q.search("nothing shares a word with this", owner=self.OWNER,
                           query_embedding=_vec(1, 0), top_k=5, store=self.store)
            ids = [h["id"] for h in (res.get("node_hits") or [])]
            self.assertIn("concept:mine", ids,
                          "어휘가 안 겹치는 질의가 의미 팔로도 못 닿았다")
        finally:
            os.environ.pop("VINC_NODE_SEMANTIC_ARM", None)


if __name__ == "__main__":
    unittest.main()
