# -*- coding: utf-8 -*-
"""`validity_*` 셈(`Validity` 누산기)의 계약.

검색 응답의 `validity_filtered` 는 여러 단계가 쓴다: 덮어쓰기(`=`), 더하기(`+=`), 비었을 때만 쓰기
(`setdefault`)가 섞여 있어 단계의 순서가 곧 값이다. 이 파일은 그 쓰기 자리를 하나도 빼지 않고
실제 단계(`search` 의 제목 팔, 청크 풀, 페이지, 의미 팔 뒤 갱신, 시간 마감)에 태워 응답의
`validity_*` 값과 키 순서를 아래 `expected` 가 적은 쓰기 순서와 대조한다. `expected` 는 누산기를 쓰지
않고 쓰기 문장을 순서대로 dict 에 적용한다.

협력 함수(`_node_fallback_search`, `temporal.finish_nodes` 등)만 바꾸고 나머지는 실제 코드가 돈다.
스토어는 가짜라 엔진 없이 돈다.
"""
from __future__ import annotations

import ast
import itertools
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from vinc_rag import retrieval as R

VALIDITY_KEYS = ("validity_filtered", "doc_validity_filtered", "validity_best_dropped",
                 "validity_note", "validity_best_dropped_distance")
HID_CHUNKS_NOTE = "hid chunks of documents whose validity ended; pass historical=true to include them"
LATE_NOTE = "some claims are outside the selected time scope; use historical mode to include them"
BEST = (3, 2)


class FakeStore:
    scoped_chunk_search = True
    fts_index_remnant = False
    vector_index_remnant = False

    def run(self, query, params=None):
        return [{"c": 3}]


def row(rid: str, score: float) -> dict:
    return {"id": rid, "score": score, "text": f"text of {rid}"}


RUNS = {
    "general": {},
    "as_of": {"as_of": "2026-09"},
    "historical": {"mode": "historical"},
    "current": {"mode": "current"},
}
# 쓰기 자리마다 켜고 끄는 손잡이. 값 하나가 한 자리의 한 가지 모양이다.
#   t1   제목 팔(current)에서 가린 노드 수
#   dnc  제목 팔이 기간 밖이라 지운 노드 수
#   c1   청크 풀(current)에서 은퇴한 문서의 청크 수, "raise" 면 문서 구간을 못 읽는다
#   c2   청크 풀(current)에서 계보로 지운 행 수, "raise" 면 계보를 못 읽는다
#   p1   페이지에서 은퇴한 문서의 수
#   s5   의미 팔이 더 지운 노드 수, "2+score" 는 지운 행의 점수도 바꾼다(비었을 때만 쓰는 자리를 가른다)
#   dist 의미 팔이 지운 행 중 가장 가까운 거리
#   late 시간 마감이 가린 노드 수
KNOBS = {
    "general": dict(t1=(0,), dnc=(0, 3), c1=(0,), c2=(0,), p1=(0, 1), s5=(0, 2, "2+score"), dist=(None, 0.12345678), late=(0, 1)),
    "as_of": dict(t1=(0,), dnc=(0, 3), c1=(0,), c2=(0,), p1=(0, 1), s5=(0, 2, "2+score"), dist=(None, 0.12345678), late=(0, 1)),
    # 기간 없는 historical 은 문서 필터를 건너뛴다: p1 이 켜져도 쓰기 자리가 열리지 않아야 한다.
    "historical": dict(t1=(0,), dnc=(0, 3), c1=(0,), c2=(0,), p1=(0, 1), s5=(0, 2, "2+score"), dist=(None, 0.12345678), late=(0, 1)),
    # current 도 페이지 필터를 건너뛴다.
    "current": dict(t1=(0, 2), dnc=(0, 3), c1=(0, 2, "raise"), c2=(0, 1, "raise"), p1=(0, 1), s5=(0, 2, "2+score"),
                    dist=(None, 0.12345678), late=(0, 1)),
}


def scenarios():
    for run, knobs in KNOBS.items():
        names = list(knobs)
        for values in itertools.product(*(knobs[n] for n in names)):
            yield run, dict(zip(names, values))


def vnote_for(run: str) -> str:
    """제목 팔이 쓰는 알림 문장. 기간이 있으면(current 는 오늘) 그 기간을 말한다."""
    _, scope, info = R.temporal.mode_scope(RUNS[run].get("mode"), False, RUNS[run].get("as_of"), "q")
    if scope is not None:
        return (f"hid nodes whose claim did not hold during {info['as_of']}; "
                "drop as_of for today's view")
    return "hid nodes whose claim is no longer current; pass historical=true to include them"


def expected(run: str, k: dict) -> dict:
    """원본의 쓰기 문장을 실행 순서대로 dict 에 적용한 결과(키 순서가 처음 들어간 순서다)."""
    out: dict = {}
    current = run == "current"
    cur_hidden = k["t1"] if current else 0
    # 제목 팔: current 의 가린 노드(=), 이어 기간 밖으로 지운 노드(=)
    if current and cur_hidden:
        out["validity_filtered"] = cur_hidden
    if k["dnc"]:
        out["validity_filtered"] = k["dnc"] + cur_hidden
        out["validity_best_dropped"] = list(BEST)
        out["validity_note"] = vnote_for(run)
    # 청크 풀(current): 문서 구간을 읽으면 가린 청크를 더하고, 계보를 읽으면 지운 행을 더한다
    if current and k["c1"] != "raise":
        out["validity_filtered"] = int(out.get("validity_filtered") or 0) + k["c1"]
        out["doc_validity_filtered"] = k["c1"]
        if k["c2"] != "raise":
            out["validity_filtered"] += k["c2"]
            out["doc_validity_filtered"] += k["c2"]
    # 페이지: current 와 기간 없는 historical 은 건너뛴다
    if run in ("general", "as_of") and k["p1"]:
        out["validity_filtered"] = int(out.get("validity_filtered") or 0) + 1
        out["doc_validity_filtered"] = 1
        out.setdefault("validity_note", HID_CHUNKS_NOTE)
    # 의미 팔 뒤: 지운 수가 있으면 처음부터 다시 센다(=), 점수와 알림은 비었을 때만
    dropped_after = k["dnc"] + (2 if k["s5"] else 0)
    if dropped_after:
        out["validity_filtered"] = dropped_after + int(out.get("doc_validity_filtered") or 0) + cur_hidden
        out.setdefault("validity_best_dropped", list((7, 7) if k["s5"] == "2+score" else BEST if k["dnc"] else (0, 0)))
        out.setdefault("validity_note", vnote_for(run))
    if k["dist"] is not None and k["s5"]:
        out["validity_best_dropped_distance"] = round(k["dist"], 6)
    # 시간 마감: 가린 노드를 더하고(+=), 셈이 있으면 알림을 비었을 때만 쓴다
    if k["late"]:
        out["validity_filtered"] = int(out.get("validity_filtered") or 0) + k["late"]
    if out.get("validity_filtered"):
        out.setdefault("validity_note", LATE_NOTE)
    return out


def run_search(run: str, k: dict) -> dict:
    current = run == "current"
    gone = 2 if (current and k["c1"] == 2) else (1 if not current else 0)

    def node_fallback(store, query, **kw):
        stats = kw["stats"]
        stats["dropped_ids"] = {f"concept:d{i}" for i in range(k["dnc"])}
        stats["dropped_not_current"] = k["dnc"]
        stats["best_dropped_score"] = BEST if k["dnc"] else (0, 0)
        return []

    def finish_nodes(store, hits, *a, stats=None, window=None, **kw):
        n = k["t1"] if window == 0 else k["late"]
        if stats is not None and n:
            stats.setdefault("current_hidden", set()).update(f"concept:h{i}" for i in range(n))

    def semantic(*a, _vstats, **kw):
        if k["s5"]:
            _vstats["dropped_ids"] = set(_vstats.get("dropped_ids") or ()) | {f"concept:s{i}" for i in range(2)}
            _vstats["dropped_not_current"] = len(_vstats["dropped_ids"])
            if k["s5"] == "2+score":
                _vstats["best_dropped_score"] = (7, 7)
            if k["dist"] is not None:
                _vstats["best_dropped_distance"] = k["dist"]
        return [], None, None, {}

    def stamp(r, meta=None, **kw):
        r["temporal"] = {"validity": "historical" if r["id"].startswith("gone") else "current"}

    def doc_sources(*a, **kw):
        if k["c1"] == "raise":
            raise RuntimeError("meta")
        return {}

    def finish_docs(store, rows, *a, **kw):
        if k["c2"] == "raise":
            raise RuntimeError("lineage")
        if kw.get("current"):
            del rows[:k["c2"]]

    pool = [row(f"gone#{i:05d}", 0.9 - i / 100) for i in range(gone)] + [row("live#00001", 0.5), row("live#00002", 0.4)]
    titles = {"gone": ("2000", "2001")} if k["p1"] else {}
    with ExitStack() as st:
        for target, name, kw in (
                (R, "_node_fallback_search", dict(side_effect=node_fallback)),
                (R.temporal, "finish_nodes", dict(side_effect=finish_nodes)),
                (R, "_chunk_arms", dict(return_value=(pool, "", ["keyword"], False, False))),
                (R.temporal, "doc_sources", dict(side_effect=doc_sources)),
                (R.temporal, "stamp", dict(side_effect=stamp)),
                (R.temporal, "finish_docs", dict(side_effect=finish_docs)),
                (R.temporal, "current_order", {}),
                (R, "_attach_doc_titles", dict(return_value=titles)),
                (R, "_node_semantic_arm", dict(side_effect=semantic)),
                (R, "_fuse_graph_arm", dict(return_value={}))):
            st.enter_context(mock.patch.object(target, name, **kw))
        return R.search("q", store=FakeStore(), owner="u", top_k=5, **RUNS[run])


class EveryWriteSiteInEveryOrder(unittest.TestCase):
    """쓰기 자리 여섯 곳을 켜고 끄는 모든 조합에서 응답의 `validity_*` 가 원본의 쓰기 순서가 주는 값과
    키 순서를 그대로 갖는다."""

    def test_the_response_carries_what_the_write_order_gives(self):
        n = 0
        for run, k in scenarios():
            with self.subTest(run=run, **k):
                res = run_search(run, k)
                want = expected(run, k)
                got = {key: res[key] for key in VALIDITY_KEYS if key in res}
                self.assertEqual(got, want)
                self.assertEqual([key for key in res if key in VALIDITY_KEYS], list(want))
                n += 1
        self.assertGreater(n, 1000)

    def test_the_table_reaches_every_site_and_every_ordering_that_matters(self):
        """표가 헛돌지 않는다: 자리마다 켜진 조합이 있고, 마지막에 쓴 값이 앞 값을 덮는 조합과
        앞 값에 더하는 조합이 모두 있다."""
        seen = {"vf_set_over_title": 0, "vf_add_after_set": 0, "doc_add": 0, "note_kept": 0, "note_default": 0,
                "distance": 0, "page_add": 0, "late_add": 0, "score_kept": 0}
        for run, k in scenarios():
            want = expected(run, k)
            seen["vf_set_over_title"] += bool(k["dnc"] and k["s5"])
            seen["score_kept"] += bool(k["dnc"] and k["s5"] == "2+score")
            seen["vf_add_after_set"] += bool(run == "current" and k["c1"] == 2 and k["dnc"])
            seen["doc_add"] += bool(run == "current" and k["c1"] == 2 and k["c2"] == 1)
            seen["note_kept"] += bool(want.get("validity_note") == vnote_for(run) and k["p1"] and k["dnc"]
                                      and run in ("general", "as_of"))
            seen["note_default"] += bool(want.get("validity_note") in (HID_CHUNKS_NOTE, LATE_NOTE))
            seen["distance"] += "validity_best_dropped_distance" in want
            seen["page_add"] += bool(run in ("general", "as_of") and k["p1"] and k["dnc"])
            seen["late_add"] += bool(k["late"] and k["dnc"])
        for name, count in seen.items():
            self.assertGreater(count, 0, name)

    def test_a_search_with_nothing_hidden_carries_no_validity_key(self):
        for run in ("general", "as_of", "historical"):
            res = run_search(run, dict(t1=0, dnc=0, c1=0, c2=0, p1=0, s5=0, dist=None, late=0))
            self.assertEqual([key for key in res if key in VALIDITY_KEYS], [], run)

    def test_current_always_carries_the_chunk_counts_even_at_zero(self):
        res = run_search("current", dict(t1=0, dnc=0, c1=0, c2=0, p1=0, s5=0, dist=None, late=0))
        self.assertEqual((res["validity_filtered"], res["doc_validity_filtered"]), (0, 0))
        self.assertNotIn("validity_note", res)


class ApiWritersKeepTheirEffect(unittest.TestCase):
    """`api` 는 검색 응답의 `validity_filtered` 에 다시 더한다. 누산기가 만든 응답을 그대로 받는다."""

    def test_the_unstarted_episodes_add_to_the_counter_the_search_wrote(self):
        from api.node_read import _drop_unstarted_episodes
        k = dict(t1=0, dnc=3, c1=0, c2=0, p1=1, s5=2, dist=0.5, late=1)
        res = run_search("as_of", k)
        before = (res["validity_filtered"], list(res["validity_best_dropped"]), res["validity_note"])
        res["node_hits"] = [
            {"id": "episode:late", "kind": "episode", "hit_tokens": 9, "title_tokens": 1,
             "temporal": {"content_time": "2027-01-01"}},
            {"id": "concept:kept", "kind": "concept", "temporal": {"content_time": ""}},
        ]
        _drop_unstarted_episodes(res, "q", as_of="2026-09", mode=None, historical=False)
        self.assertEqual([h["id"] for h in res["node_hits"]], ["concept:kept"])
        self.assertEqual(res["validity_filtered"], before[0] + 1)
        self.assertEqual(res["validity_best_dropped"], [max(BEST[0], 9), max(BEST[1], 1)])
        self.assertEqual(res["validity_note"], before[2])
        self.assertIsInstance(res["validity_filtered"], int)
        self.assertIsInstance(res["validity_best_dropped"], list)


class ValidityPrimitives(unittest.TestCase):
    def test_set_overwrites_add_accumulates_default_fills_only_a_gap(self):
        v = R.Validity()
        v.add("validity_filtered", 2)
        v.set("validity_filtered", 5)
        v.add("validity_filtered", 1)
        v.default("validity_note", "first")
        v.default("validity_note", "second")
        self.assertEqual((v.get("validity_filtered"), v.get("validity_note")), (6, "first"))
        self.assertIsNone(v.get("validity_best_dropped"))

    def test_add_starts_from_zero_and_makes_an_integer(self):
        v = R.Validity({"validity_filtered": None})
        v.add("validity_filtered", 0)
        self.assertEqual(v.get("validity_filtered"), 0)
        self.assertIs(type(v.get("validity_filtered")), int)

    def test_flush_writes_only_what_changed_since_the_last_flush_in_the_order_it_changed(self):
        v = R.Validity({"validity_filtered": 4, "unrelated": 1})
        sink: dict = {}
        v.flush(sink)
        self.assertEqual(sink, {})
        v.default("validity_note", "n")
        v.set("validity_filtered", 7)
        v.set("validity_note", "m")
        v.flush(sink)
        self.assertEqual(list(sink.items()), [("validity_note", "m"), ("validity_filtered", 7)])
        sink.clear()
        v.flush(sink)
        self.assertEqual(sink, {})

    def test_a_flushed_key_that_is_already_in_the_response_keeps_its_place(self):
        v = R.Validity()
        res = {"a": 1, "validity_filtered": 1, "b": 2}
        v.set("validity_filtered", 9)
        v.flush(res)
        self.assertEqual(list(res), ["a", "validity_filtered", "b"])
        self.assertEqual(res["validity_filtered"], 9)

    def test_the_seed_is_not_a_change_and_ignores_other_keys(self):
        v = R.Validity({"validity_note": "x", "node_hits": []})
        sink: dict = {}
        v.flush(sink)
        self.assertEqual((sink, v.get("validity_note"), v.get("node_hits")), ({}, "x", None))


class OnlyTheAccumulatorWritesValidityKeys(unittest.TestCase):
    def test_no_statement_in_retrieval_writes_a_validity_key_by_name(self):
        tree = ast.parse(Path(R.__file__).read_text(encoding="utf-8"))
        names = set(VALIDITY_KEYS)
        found = []
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            for t in targets:
                if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) and t.slice.value in names:
                    found.append(node.lineno)
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "setdefault"
                    and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value in names):
                found.append(node.lineno)
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
