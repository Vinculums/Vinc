# -*- coding: utf-8 -*-
"""`_search` 의 네 단계(`_title_arm`, `_chunk_stage`, `_paginate`, `_fuse_graph_arm`)의 계약.

단계는 응답 dict 를 직접 고치지 않고 키 patch 로 돌려준다. 부르는 쪽(`_search`)이 부른 바로 뒤에
`base.update` 로 얹는다. 응답 전체는 `test_search_golden.py` 가 고정한다. 거기에 없는 자리를 이
파일이 지킨다: 단계가 `base` 를 읽기만 하는 것, patch 키의 순서, 인덱스 지연 빌드와 실패 표식,
낡은 커서의 답, 같은 키를 여러 단계가 쓸 때 부른 순서가 곧 마지막에 쓴 값이라는 것.
스토어는 가짜라 엔진 없이 돈다.
"""
from __future__ import annotations

import copy
import unittest
from unittest import mock

from vinc_rag import retrieval as R


class FakeStore:
    """청크 팔에 필요한 표지와 연산만 가진다. `chunks` 는 `_chunk_count` 가 읽는 행 수."""

    def __init__(self, *, chunks: int = 3, builder=None, **flags):
        self.chunks = chunks
        self.scoped_chunk_search = flags.pop("scoped_chunk_search", True)
        self.fts_index_remnant = flags.pop("fts_index_remnant", False)
        self.vector_index_remnant = flags.pop("vector_index_remnant", False)
        if builder is not None:
            self.ensure_search_indexes = builder

    def run(self, query, params=None):
        return [{"c": self.chunks}]


class Cfg:
    embedding_dim = 4


INDEX_MISSING = RuntimeError("Table Chunk doesn't have an index with name chunk_ft")


def row(rid: str, score: float) -> dict:
    return {"id": rid, "score": score, "text": f"text of {rid}"}


def chunk_stage(store, seed=None, **over):
    """`seed` 는 앞 단계(제목 팔)가 이미 쌓아 둔 `validity_*` 로, 누산기를 그 값으로 시작시킨다."""
    args = dict(filters=None, owner=None, kinds=None, top_k=5, cfg=Cfg(), _vstats={}, known_at=None,
                mode=None, _until=None, _scope=None, _validity=R.Validity(seed))
    args.update(over)
    return R._chunk_stage(store, "q", args.pop("query_embedding", None), **args)


def arms_ok(pool=None, vnote="", ran=("keyword",), capped=False, gated=False):
    return mock.patch.object(R, "_chunk_arms", return_value=(
        list(pool if pool is not None else [row("d#00001", 0.5)]), vnote, list(ran), capped, gated))


class TitleArm(unittest.TestCase):
    def arm(self, **over):
        args = dict(continuation=False, owner="u", mode=None, historical=True, known_at=None, _wide=False,
                    _nk=5, _kw={}, _scope=None, _until=None, _vstats={}, _vnote="note",
                    _validity=R.Validity())
        args.update(over)
        return R._title_arm(object(), "q", **args)

    def test_a_continuation_page_only_empties_node_hits(self):
        with mock.patch.object(R, "_node_fallback_search") as search:
            hits, patch = self.arm(continuation=True)
        search.assert_not_called()
        self.assertEqual((hits, patch), ([], {"node_hits": []}))

    def test_a_failure_empties_the_hits_and_leaves_node_error(self):
        with mock.patch.object(R, "_node_fallback_search", side_effect=RuntimeError("boom")):
            hits, patch = self.arm()
        self.assertEqual(hits, [])
        self.assertEqual(list(patch), ["node_error", "node_hits"])
        self.assertEqual(patch["node_error"], "RuntimeError: boom")

    def test_the_later_validity_filtered_wins_inside_the_arm(self):
        stats = {"current_hidden": {"a", "b"}, "dropped_not_current": 3, "best_dropped_score": (0.5, 1)}
        with mock.patch.object(R, "_node_fallback_search", return_value=[{"id": "concept:x"}]), \
                mock.patch.object(R.temporal, "finish_nodes"):
            hits, patch = self.arm(mode="current", _vstats=stats)
        self.assertEqual(patch["validity_filtered"], 3 + 2)
        self.assertEqual(list(patch), ["validity_filtered", "validity_best_dropped", "validity_note", "node_hits"])
        self.assertEqual(patch["validity_best_dropped"], [0.5, 1])
        self.assertEqual(patch["validity_note"], "note")
        self.assertIs(patch["node_hits"], hits)

    def test_known_at_in_current_mode_counts_what_it_hid(self):
        stats = {"current_hidden": {"a"}, "known_hidden": {"x", "y"}, "known_unknown": {"z"}}
        with mock.patch.object(R, "_node_fallback_search", return_value=[]), \
                mock.patch.object(R.temporal, "finish_nodes"):
            _, patch = self.arm(mode="current", known_at="2026-01", _vstats=stats)
        self.assertEqual(list(patch), ["validity_filtered", "known_at_filtered", "known_at_unknown", "node_hits"])
        self.assertEqual((patch["validity_filtered"], patch["known_at_filtered"], patch["known_at_unknown"]), (1, 2, 1))


class ChunkStage(unittest.TestCase):
    def test_the_arm_result_is_a_patch_and_the_tally_is_left_alone_outside_current(self):
        acc = R.Validity({"validity_filtered": 4})
        with arms_ok([row("d#00001", 0.5), row("d#00003", 0.9), row("d#00002", 0.9)]):
            pool, withheld, hidden, patch = chunk_stage(FakeStore(), _validity=acc)
        self.assertEqual([r["id"] for r in pool], ["d#00002", "d#00003", "d#00001"])
        self.assertEqual((withheld, hidden), (False, 0))
        self.assertEqual(list(patch), ["node_window", "executed", "arms", "total_count"])
        self.assertEqual((patch["arms"], patch["total_count"]), ("fts", 3))
        self.assertEqual(acc.get("validity_filtered"), 4)

    def test_owner_scope_the_store_cannot_apply_fails_closed(self):
        with arms_ok() as arms:
            pool, _, _, patch = chunk_stage(FakeStore(scoped_chunk_search=False), owner="u",
                                            query_embedding=[1.0])
        arms.assert_not_called()
        self.assertEqual(pool, [])
        self.assertEqual(list(patch), ["node_window", "error", "note", "arms", "vector_note", "total_count"])
        self.assertTrue(patch["error"].startswith("owner_scope_unsupported: "))
        self.assertEqual((patch["note"], patch["vector_note"], patch["arms"]),
                         (patch["error"], patch["error"], "none"))

    def test_a_failure_that_is_not_a_missing_index_is_the_error(self):
        with mock.patch.object(R, "_chunk_arms", side_effect=RuntimeError("boom")):
            _, _, _, patch = chunk_stage(FakeStore())
        self.assertEqual(patch["error"], "error: RuntimeError: boom")
        self.assertEqual(patch["note"], patch["error"])
        self.assertNotIn("executed", patch)

    def test_a_missing_index_over_zero_chunks_answers_with_node_hits_only(self):
        with mock.patch.object(R, "_chunk_arms", side_effect=INDEX_MISSING):
            _, _, _, patch = chunk_stage(FakeStore(chunks=0, builder=mock.Mock()))
        self.assertTrue(patch["executed"])
        self.assertEqual(patch["note"], "no chunks indexed — node_hits only")
        self.assertNotIn("error", patch)

    def test_a_remnant_is_not_rebuilt(self):
        builder = mock.Mock()
        with mock.patch.object(R, "_chunk_arms", side_effect=INDEX_MISSING):
            _, _, _, patch = chunk_stage(FakeStore(fts_index_remnant=True, builder=builder))
        builder.assert_not_called()
        self.assertTrue(patch["error"].startswith("fts_index_remnant: "))
        self.assertEqual(patch["note"], patch["error"])

    def test_no_builder_says_index_missing(self):
        with mock.patch.object(R, "_chunk_arms", side_effect=INDEX_MISSING):
            _, _, _, patch = chunk_stage(FakeStore())
        self.assertTrue(patch["error"].startswith("index_missing: RuntimeError: "))

    def test_the_index_is_built_once_and_the_search_retried(self):
        builder = mock.Mock()
        calls = [INDEX_MISSING, ([row("d#00001", 0.5)], "", ["keyword"], False, False)]
        with mock.patch.object(R, "_chunk_arms", side_effect=calls):
            pool, _, _, patch = chunk_stage(FakeStore(builder=builder))
        builder.assert_called_once_with(dim=None)
        self.assertEqual(len(pool), 1)
        self.assertEqual(list(patch)[:3], ["node_window", "executed", "note"])
        self.assertEqual(patch["note"], "search index built on demand")
        self.assertEqual(patch["arms"], "fts")

    def test_a_retry_that_fails_is_index_build_failed(self):
        with mock.patch.object(R, "_chunk_arms", side_effect=[INDEX_MISSING, RuntimeError("again")]):
            _, _, _, patch = chunk_stage(FakeStore(builder=mock.Mock()))
        self.assertEqual(patch["error"], "index_build_failed: RuntimeError: again")
        self.assertEqual(patch["note"], patch["error"])
        self.assertEqual(patch["arms"], "none")

    def test_the_vector_rebuilding_window_is_marked_and_scheduled(self):
        with arms_ok(vnote=R._VECTOR_REBUILDING, ran=("keyword",)), \
                mock.patch.object(R, "_schedule_vector_index") as schedule:
            store = FakeStore()
            _, _, _, patch = chunk_stage(store, query_embedding=[1.0])
        schedule.assert_called_once_with(store, 4)
        self.assertEqual(list(patch), ["node_window", "executed", "arms", "vector_note", "vector_rebuilding",
                                       "total_count"])
        self.assertTrue(patch["vector_rebuilding"])

    def test_a_remnant_vector_index_is_a_failure_not_a_window(self):
        with arms_ok(vnote=R._VECTOR_REBUILDING), mock.patch.object(R, "_schedule_vector_index") as schedule:
            _, _, _, patch = chunk_stage(FakeStore(vector_index_remnant=True))
        schedule.assert_not_called()
        self.assertEqual(patch["vector_note"], R._VECTOR_REMNANT)
        self.assertNotIn("vector_rebuilding", patch)

    def test_current_adds_to_what_the_title_arm_counted(self):
        acc = R.Validity({"validity_filtered": 3})

        def stamp(r, meta, scope=None):
            r["temporal"] = {"validity": "historical" if r["id"].startswith("old") else "current"}
        pool_in = [row("old#00001", 0.9), row("new#00001", 0.8)]
        with arms_ok(pool_in), \
                mock.patch.object(R.temporal, "doc_sources", return_value={}), \
                mock.patch.object(R.temporal, "stamp", side_effect=stamp), \
                mock.patch.object(R.temporal, "finish_docs"), mock.patch.object(R.temporal, "current_order"):
            pool, withheld, _, patch = chunk_stage(FakeStore(), mode="current", _validity=acc)
        self.assertEqual([r["id"] for r in pool], ["new#00001"])
        self.assertFalse(withheld)
        self.assertEqual((patch["validity_filtered"], patch["doc_validity_filtered"]), (4, 1))
        self.assertEqual((acc.get("validity_filtered"), acc.get("doc_validity_filtered")), (4, 1))
        self.assertEqual(list(patch["_current_doc_temporal"]), ["new"])
        self.assertEqual(patch["total_count"], 1)

    def test_current_fails_closed_when_document_meta_cannot_be_read(self):
        with arms_ok(), mock.patch.object(R.temporal, "doc_sources", side_effect=RuntimeError("meta")):
            pool, withheld, _, patch = chunk_stage(FakeStore(), {"validity_filtered": 3}, mode="current")
        self.assertEqual((pool, withheld), ([], True))
        self.assertTrue(patch["partial"])
        self.assertEqual(patch["temporal_error"], "RuntimeError: meta")
        self.assertNotIn("validity_filtered", patch)
        self.assertEqual(patch["total_count"], 0)

    def test_known_at_fails_closed_when_the_pool_cannot_be_checked(self):
        with arms_ok(), mock.patch.object(R, "_known_at_pool", side_effect=RuntimeError("clock")):
            pool, withheld, hidden, patch = chunk_stage(FakeStore(), known_at="2026-01", _until="x")
        self.assertEqual((pool, withheld, hidden), ([], True, 0))
        self.assertEqual(patch["temporal_error"], "RuntimeError: clock")

    def test_a_pool_at_the_cap_says_total_count_is_at_least(self):
        pool = [row(f"d#{i:05d}", 1.0) for i in range(R.SEARCH_POOL_CAP)]
        with arms_ok(pool):
            _, _, _, patch = chunk_stage(FakeStore())
        self.assertEqual(patch["total_count"], R.SEARCH_POOL_CAP)
        self.assertTrue(patch["total_count_capped"])


class Paginate(unittest.TestCase):
    POOL = [row(f"doc#{i:05d}", 1.0 - i / 10) for i in range(5)]

    def page(self, base=None, titles=None, **over):
        """`base` 가 이미 실은 `validity_*` 는 앞 단계가 쌓아 둔 것이라 누산기도 그 값으로 시작한다."""
        args = dict(cursor=None, off=0, want_eid=None, want_arms=None, qh="qh", top_k=2, detail="summary",
                    owner="u", mode=None, historical=False, _scope=None, _withheld=False,
                    base=base if base is not None else {"arms": "fts"})
        args.setdefault("_validity", R.Validity(args["base"]))
        args.update(over)
        pool = args.pop("pool", self.POOL)
        with mock.patch.object(R, "_attach_doc_titles", return_value=titles or {}):
            return R._paginate(object(), "q", pool, **args)

    def test_the_first_page_is_a_patch_with_a_cursor_for_the_next(self):
        base = {"arms": "fts", "rows": []}
        before = copy.deepcopy(base)
        stale, patch = self.page(base)
        self.assertFalse(stale)
        self.assertEqual(list(patch), ["has_more", "next_cursor", "rows", "page"])
        self.assertEqual([r["id"] for r in patch["rows"]], ["doc#00000", "doc#00001"])
        self.assertTrue(patch["has_more"])
        self.assertEqual(R.cursor_decode(patch["next_cursor"]),
                         {"v": 1, "off": 2, "qh": "qh", "eid": "doc#00002", "tc": 5, "arms": "fts"})
        self.assertEqual(patch["page"], {"offset": 0, "limit": 2, "fingerprint": "qh"})
        self.assertEqual(base, before)

    def test_the_last_page_has_no_cursor(self):
        stale, patch = self.page(off=4, cursor="c", want_arms="fts", want_eid="doc#00004")
        self.assertFalse(stale)
        self.assertFalse(patch["has_more"])
        self.assertNotIn("next_cursor", patch)
        self.assertEqual([r["id"] for r in patch["rows"]], ["doc#00004"])

    def test_detail_full_keeps_the_text(self):
        _, patch = self.page(detail="full")
        self.assertEqual(patch["rows"][0]["text"], "text of doc#00000")
        self.assertEqual(patch["rows"][0]["doc_id"], "doc")

    def test_a_cursor_for_other_arms_is_stale_and_says_so_in_the_patch(self):
        base = {"arms": "fts+vector", "executed": True, "note": "earlier"}
        stale, patch = self.page(base, cursor="c", want_arms="fts")
        self.assertTrue(stale)
        self.assertEqual(list(patch), ["note", "error", "executed"])
        self.assertTrue(patch["error"].startswith("stale_cursor: "))
        self.assertIs(patch["executed"], False)
        self.assertEqual(base["executed"], True)
        base.update(patch)
        self.assertEqual(list(base), ["arms", "executed", "note", "error"])

    def test_an_offset_past_the_pool_and_a_moved_eid_are_stale(self):
        self.assertTrue(self.page(cursor="c", off=9)[0])
        self.assertTrue(self.page(cursor="c", off=1, want_eid="doc#00009")[0])
        self.assertFalse(self.page(cursor="c", off=1, want_eid="doc#00001")[0])

    def test_an_earlier_error_or_a_withheld_pool_is_never_stale(self):
        self.assertFalse(self.page({"arms": "none", "error": "boom"}, cursor="c", off=9)[0])
        self.assertFalse(self.page(cursor="c", off=9, _withheld=True)[0])

    def test_rows_of_a_retired_document_are_hidden_and_counted_on_top_of_base(self):
        pool = [row("gone#00001", 0.9), row("live#00001", 0.8)]
        base = {"arms": "fts", "validity_filtered": 4}
        stale, patch = self.page(base, pool=pool, titles={"gone": ("2000", "2001")})
        self.assertFalse(stale)
        self.assertEqual([r["id"] for r in patch["rows"]], ["live#00001"])
        self.assertEqual((patch["validity_filtered"], patch["doc_validity_filtered"]), (5, 1))
        self.assertTrue(patch["validity_note"].startswith("hid chunks of documents"))
        self.assertEqual(patch["page"]["row_offsets"], {"live#00001": 1})
        self.assertEqual(base, {"arms": "fts", "validity_filtered": 4})

    def test_a_validity_note_an_earlier_stage_wrote_is_kept(self):
        pool = [row("gone#00001", 0.9), row("live#00001", 0.8)]
        _, patch = self.page({"arms": "fts", "validity_note": "earlier"}, pool=pool,
                             titles={"gone": ("2000", "2001")})
        self.assertNotIn("validity_note", patch)

    def test_unreadable_document_meta_empties_the_page_but_not_in_current_mode(self):
        with mock.patch.object(R, "_attach_doc_titles", side_effect=RuntimeError("meta")):
            _, patch = R._paginate(object(), "q", self.POOL, cursor=None, off=0, want_eid=None, want_arms=None,
                                   qh="qh", top_k=2, detail="summary", owner="u", mode=None, historical=False,
                                   _scope=None, _withheld=False, base={"arms": "fts"},
                                   _validity=R.Validity())
            _, cur = R._paginate(object(), "q", self.POOL, cursor=None, off=0, want_eid=None, want_arms=None,
                                 qh="qh", top_k=2, detail="summary", owner="u", mode="current", historical=False,
                                 _scope=None, _withheld=False, base={"arms": "fts"},
                                 _validity=R.Validity())
        self.assertEqual((patch["rows"], patch["partial"], patch["temporal_error"]),
                         ([], True, "RuntimeError: meta"))
        self.assertEqual(len(cur["rows"]), 2)
        self.assertTrue(cur["partial"])


class FuseGraphArm(unittest.TestCase):
    ROWS = [{"id": "c#1", "relevance": {"level": "weak"}}, {"id": "c#2"}]

    def fuse(self, base=None, graph=None, title=None, vec=None, **over):
        args = dict(owner="u", kinds=None, known_at=None, mode=None, _nk=5, _until=None, _vstats={},
                    _chunk_known_hidden=0, _sem_lead=None, _sem_decisive=None)
        args.update(over)
        base = {"rows": self.ROWS} if base is None else base
        with mock.patch.object(R, "_graph_arm_search", return_value=graph if graph is not None else []) as arm:
            patch = R._fuse_graph_arm(object(), base, title if title is not None else [],
                                      vec if vec is not None else [], **args)
        return patch, arm

    def test_weak_chunks_do_not_seed_the_graph_arm_and_hits_carry_the_semantic_marks(self):
        title = [{"id": "concept:a", "title": "a"}]
        graph = [{"id": "concept:b", "title": "b"}]
        vec = [{"id": "concept:a", "distance": 0.1}, {"id": "concept:b", "distance": 0.4}]
        base = {"rows": self.ROWS}
        before = copy.deepcopy(base)
        patch, arm = self.fuse(base, graph, title, vec, _sem_lead=("concept:a", 0.3), _sem_decisive="concept:a")
        self.assertEqual(arm.call_args.args[1], ["c#2"])
        self.assertEqual(list(patch), ["node_hits"])
        by_id = {h["id"]: h for h in patch["node_hits"]}
        self.assertEqual(by_id["concept:a"]["distance"], 0.1)
        self.assertEqual(by_id["concept:a"]["sem_lead"], 0.3)
        self.assertTrue(by_id["concept:a"]["sem_decisive"])
        self.assertNotIn("sem_lead", by_id["concept:b"])
        self.assertEqual(base, before)

    def test_kinds_without_concept_skip_the_graph_arm(self):
        patch, arm = self.fuse(kinds=["record"], title=[{"id": "record:x"}])
        arm.assert_not_called()
        self.assertEqual([h["id"] for h in patch["node_hits"]], ["record:x"])

    def test_known_at_counts_what_the_filter_and_the_chunk_stage_hid(self):
        stats = {"known_hidden": {"a", "b"}}
        with mock.patch.object(R, "_known_at_nodes", return_value=[]):
            patch, _ = self.fuse(known_at="2026-01", _until="x", _vstats=stats, _chunk_known_hidden=3)
        self.assertEqual(list(patch), ["known_at_filtered", "node_hits"])
        self.assertEqual(patch["known_at_filtered"], 3 + 2)

    def test_a_failure_falls_back_to_the_title_hits_and_leaves_node_error(self):
        title = [{"id": "concept:a"}]
        with mock.patch.object(R, "_graph_arm_search", side_effect=RuntimeError("graph")):
            patch = R._fuse_graph_arm(object(), {"rows": self.ROWS}, title, [], owner="u", kinds=None,
                                      known_at=None, mode=None, _nk=5, _until=None, _vstats={},
                                      _chunk_known_hidden=0, _sem_lead=None, _sem_decisive=None)
        self.assertEqual(list(patch), ["node_error", "node_hits"])
        self.assertEqual(patch["node_error"], "RuntimeError: graph")
        self.assertEqual([h["id"] for h in patch["node_hits"]], ["concept:a"])

    def test_a_node_error_an_earlier_stage_wrote_is_not_overwritten(self):
        base = {"rows": self.ROWS, "node_error": "ValueError: title arm"}
        with mock.patch.object(R, "_graph_arm_search", side_effect=RuntimeError("graph")):
            patch = R._fuse_graph_arm(object(), base, [], [], owner="u", kinds=None, known_at=None, mode=None,
                                      _nk=5, _until=None, _vstats={}, _chunk_known_hidden=0, _sem_lead=None,
                                      _sem_decisive=None)
        self.assertNotIn("node_error", patch)
        self.assertEqual(base["node_error"], "ValueError: title arm")


class SearchAppliesEachPatchAtOnce(unittest.TestCase):
    """`_search` 는 단계를 부른 바로 뒤에 patch 를 얹는다. 같은 키(`validity_filtered`)를 여러 단계가
    쓰므로 이 순서가 곧 마지막에 쓴 값을 정한다. 값은 단계 사이를 누산기가 나른다."""

    def run_search(self, page_result=(False, {"rows": []})):
        seen = {}

        def title(*a, _validity, **k):
            seen["title"] = _validity.get("validity_filtered")
            patch = {}
            _validity.set("validity_filtered", 1)
            _validity.flush(patch)
            return [], dict(patch, node_hits=[])

        def chunk(*a, _validity, **k):
            seen["chunk"] = _validity.get("validity_filtered")
            patch = {}
            _validity.set("validity_filtered", 2)
            _validity.flush(patch)
            return [], False, 0, dict(patch, total_count=0)

        def paginate(*a, base, **k):
            seen["paginate"] = base.get("validity_filtered")
            return page_result

        def semantic(*a, base, **k):
            seen["semantic"] = base.get("validity_filtered")
            return [], None, None, {}

        def graph(store, base, *a, **k):
            seen["graph"] = base.get("validity_filtered")
            return {"node_hits": ["fused"]}

        with mock.patch.object(R, "_title_arm", side_effect=title), \
                mock.patch.object(R, "_chunk_stage", side_effect=chunk), \
                mock.patch.object(R, "_paginate", side_effect=paginate), \
                mock.patch.object(R, "_node_semantic_arm", side_effect=semantic), \
                mock.patch.object(R, "_fuse_graph_arm", side_effect=graph):
            res = R._search("q", store=object(), owner="u",
                            _mode_scope=R.temporal.mode_scope(None, False, None, "q"))
        return res, seen

    def test_every_stage_sees_the_patches_of_the_stages_before_it(self):
        res, seen = self.run_search()
        self.assertEqual(seen, {"title": None, "chunk": 1, "paginate": 2, "semantic": 2, "graph": 2})
        self.assertEqual(res["validity_filtered"], 2)
        self.assertEqual(res["node_hits"], ["fused"])

    def test_a_stale_cursor_ends_the_search_before_the_node_arms(self):
        res, seen = self.run_search((True, {"note": "stale", "error": "stale", "executed": False}))
        self.assertEqual(sorted(seen), ["chunk", "paginate", "title"])
        self.assertEqual((res["error"], res["executed"]), ("stale", False))
        self.assertNotIn("node_hits_omitted", res)


if __name__ == "__main__":
    unittest.main()
