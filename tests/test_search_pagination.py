# -*- coding: utf-8 -*-
"""GM-14 — vinc_search 커서 페이지네이션 + 요약·상세 분리.

실측 사고: `limit=100` 질의가 MCP 봉투에서 `Result too large: 184,136 characters`
로 **전량 거부**됐다. 어시스턴트는 재시도 근거가 없어 루프에 빠진다. 이 파일이
고정하는 계약:

  1. rows 는 안정 정렬(점수 내림차순 + id 오름차순) 풀의 페이지다 — 페이지 사이에
     결과가 흔들리지 않고, 순회에 누락·중복이 없다.
  2. 무효·실효 커서는 **조용히 첫 페이지로 폴백하지 않고** 명시적 오류를 낸다.
  3. 기본 응답은 발췌(snippet)다 — 전문은 vinc_doc_get(doc_id) 으로 유도한다.
  4. MCP 봉투는 초과 결과를 거부하는 대신 꼬리 행을 덜어 잘린 페이지로 돌려준다
     (등재된 도구만 — vinc_search·vinc_brief·vinc_graph; 나머지는 기존 거부 유지).
  5. 커서 코덱은 api.query 와 vinc_mcp.tools 에 미러된 두 사본이다 — 왕복 호환이
     깨지면 shrinker 가 만든 커서를 사이드카가 못 읽는다(교차 계약 테스트).
"""
from __future__ import annotations

import json
import unittest

from api import query as q
from vinc_mcp import tools as mcp_tools


def _mk_rows(n: int, *, doc: str = "docA", text: str = "본문 ") -> list[dict]:
    # 점수 내림차순이 곧 id 오름차순이 되도록 id 를 0패딩 — 안정 정렬 검증이 쉬워진다.
    return [{"id": f"{doc}#{i:05d}", "text": f"{text}{i} " + ("x" * 300),
             "score": float(n - i)} for i in range(n)]


class _Store:
    """q.search 가 만지는 최소면 — fulltext_search + run(노드 폴백·그래프 암은 빈 결과)."""
    driver_name = "fake"

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.seen_k: list[int] = []

    def fulltext_search(self, text, k, filters=None):
        self.seen_k.append(int(k))
        return [dict(r) for r in self.rows[:int(k)]]

    def run(self, cypher, params=None):
        return []

    def close(self):
        pass


def _search(store, **kw):
    kw.setdefault("top_k", 20)
    return q.search("질의어", store=store, **kw)


class PaginationWalkTests(unittest.TestCase):
    def test_페이지_순회에_누락과_중복이_없다(self):
        store = _Store(_mk_rows(50))
        seen: list[str] = []
        cursor = None
        pages = 0
        while True:
            res = _search(store, cursor=cursor)
            self.assertNotIn("error", res, f"페이지 {pages} 가 오류를 냈다: {res.get('error')}")
            self.assertEqual(res["total_count"], 50)
            seen += [r["id"] for r in res["rows"]]
            pages += 1
            if not res["has_more"]:
                self.assertIsNone(res["next_cursor"])
                break
            self.assertTrue(res["next_cursor"])
            cursor = res["next_cursor"]
        self.assertEqual(pages, 3)                      # 20 + 20 + 10
        self.assertEqual(len(seen), 50, "순회 총량이 풀과 다르다")
        self.assertEqual(len(set(seen)), 50, "페이지 사이에 중복이 있다")
        self.assertEqual(seen, sorted(seen), "안정 정렬(점수=id 역순 구성)이 깨졌다")

    def test_같은_페이지를_다시_읽으면_같은_행이_나온다(self):
        store = _Store(_mk_rows(50))
        first = _search(store)
        again = _search(store)
        self.assertEqual([r["id"] for r in first["rows"]],
                         [r["id"] for r in again["rows"]])

    def test_풀은_상한까지만_가져온다(self):
        store = _Store(_mk_rows(5))
        _search(store)
        self.assertEqual(store.seen_k, [q.SEARCH_POOL_CAP],
                         "후보 풀이 SEARCH_POOL_CAP 로 요청되지 않았다")


class SummaryDetailTests(unittest.TestCase):
    def test_기본_행은_발췌다_전문이_아니라(self):
        store = _Store(_mk_rows(3))
        rows = _search(store)["rows"]
        for r in rows:
            self.assertNotIn("text", r, "summary 행에 청크 전문이 실렸다")
            self.assertLessEqual(len(r["snippet"]), 240)
            self.assertTrue(r["truncated"], "300자 본문인데 truncated 가 아니다")
            self.assertEqual(r["doc_id"], "docA", "vinc_doc_get 유도용 doc_id 가 없다")

    def test_detail_full_은_전문을_실는다(self):
        store = _Store(_mk_rows(3))
        rows = _search(store, detail="full")["rows"]
        for r in rows:
            self.assertIn("text", r)
            self.assertNotIn("snippet", r)


class CursorErrorTests(unittest.TestCase):
    def test_쓰레기_커서는_invalid_cursor_오류다(self):
        store = _Store(_mk_rows(10))
        res = _search(store, cursor="이건 커서가 아니다")
        self.assertTrue(str(res.get("error", "")).startswith("invalid_cursor"))
        self.assertEqual(res["rows"], [], "오류인데 행이 실렸다 — 조용한 폴백")
        self.assertFalse(res["executed"])

    def test_다른_질의의_커서는_invalid_cursor_오류다(self):
        store = _Store(_mk_rows(50))
        cur = _search(store)["next_cursor"]
        res = q.search("전혀 다른 질의", store=store, top_k=20, cursor=cur)
        self.assertTrue(str(res.get("error", "")).startswith("invalid_cursor"))

    def test_결과가_변하면_stale_cursor_오류다(self):
        store = _Store(_mk_rows(50))
        cur = _search(store)["next_cursor"]
        mutated = _Store(_mk_rows(50)[5:])              # 발급 후 앞 5행이 사라졌다
        res = _search(mutated, cursor=cur)
        self.assertTrue(str(res.get("error", "")).startswith("stale_cursor"))
        self.assertEqual(res["rows"], [])

    def test_풀_끝을_넘긴_커서도_stale_cursor_오류다(self):
        store = _Store(_mk_rows(50))
        cur = _search(store)["next_cursor"]
        res = _search(_Store(_mk_rows(3)), cursor=cur)
        self.assertTrue(str(res.get("error", "")).startswith("stale_cursor"))


class CodecCrossContractTests(unittest.TestCase):
    """api.query ↔ vinc_mcp.tools 커서 코덱 미러의 왕복 호환."""

    def test_tools_인코딩을_query_가_디코딩한다(self):
        payload = {"v": 1, "off": 7, "qh": "abc123def456", "eid": "docA#00007", "tc": 50}
        self.assertEqual(q.cursor_decode(mcp_tools._cursor_encode(payload)), payload)

    def test_query_인코딩을_query_가_디코딩한다(self):
        payload = {"v": 1, "off": 0, "qh": "0" * 12, "eid": "x", "tc": 1}
        self.assertEqual(q.cursor_decode(q.cursor_encode(payload)), payload)

    def test_버전이_다르면_invalid(self):
        with self.assertRaises(ValueError):
            q.cursor_decode(q.cursor_encode({"v": 2, "off": 0}))


class ShrinkerTests(unittest.TestCase):
    def _big_result(self, n=40, offset=0):
        return {"kind": "search", "executed": True,
                "rows": [{"id": f"d#{i + offset:05d}", "snippet": "y" * 200,
                          "truncated": True, "doc_id": "d", "score": float(n - i)}
                         for i in range(n)],
                "node_hits": [], "arms": "fts", "total_count": 200,
                "has_more": True, "next_cursor": "무관",
                "page": {"offset": offset, "limit": n, "fingerprint": "f" * 12}}

    def test_예산을_넘으면_꼬리를_덜고_커서를_다시_쓴다(self):
        out = self._big_result(n=40)
        slim = mcp_tools._shrink_search_result(out, 3_000)
        self.assertIsNotNone(slim)
        kept = len(slim["rows"])
        self.assertGreater(kept, 0)
        self.assertLess(kept, 40)
        self.assertTrue(slim["has_more"])
        self.assertLessEqual(len(json.dumps(slim, ensure_ascii=False)), 3_000)
        cur = q.cursor_decode(slim["next_cursor"])      # 사이드카가 읽을 수 있어야 한다
        self.assertEqual(cur["off"], kept)
        self.assertEqual(cur["eid"], f"d#{kept:05d}", "다음 페이지 기대 id 가 어긋난다")
        self.assertEqual(cur["qh"], "f" * 12)

    def test_page_좌표가_없으면_축소를_포기한다(self):
        out = self._big_result()
        del out["page"]
        self.assertIsNone(mcp_tools._shrink_search_result(out, 3_000))

    def test_행_하나도_안_들어가면_포기한다(self):
        self.assertIsNone(mcp_tools._shrink_search_result(self._big_result(n=2), 10))


class EnvelopeIntegrationTests(unittest.TestCase):
    """rpc 봉투 — vinc_search 초과 결과는 잘린 페이지, 타 도구는 기존 거부."""

    def test_vinc_search_초과는_잘린_페이지로_돌아온다(self):
        from vinc_mcp.rpc import Dispatcher

        big = {"kind": "search", "executed": True,
               "rows": [{"id": f"d#{i:05d}", "snippet": "z" * 500, "truncated": True,
                         "doc_id": "d"} for i in range(200)],
               "node_hits": [], "arms": "fts", "total_count": 200,
               "has_more": False, "next_cursor": None,
               "page": {"offset": 0, "limit": 200, "fingerprint": "a" * 12}}

        class _Bridge:
            owner = "user:local"
            def request(self, method, path, params=None, body=None):
                return big

        # limit 은 스키마 상한(100) 안이어야 한다 — 200 은 이제 실행 전에 거절된다(D5).
        # 스텁이 limit 과 무관하게 200행을 주므로 축소기 경로는 그대로 선다.
        d = Dispatcher(_Bridge())
        res = d.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "vinc_search",
                                   "arguments": {"query": "x", "limit": 100}}})
        result = res["result"]
        self.assertFalse(result.get("isError"), "거부됐다 — shrinker 가 안 돌았다")
        text = result["content"][0]["text"]
        self.assertLessEqual(len(text), 72_000)
        out = json.loads(text)
        self.assertTrue(out["has_more"])
        self.assertTrue(out["next_cursor"])
        self.assertLess(len(out["rows"]), 200)

    def test_상한_밖_limit_은_자르지_않고_거절한다(self):
        """D5: limit=200 은 조용히 100 으로 잘리지 않는다 — 실행 전에 도구 결과로 거절."""
        from vinc_mcp.rpc import Dispatcher

        class _Bridge:
            owner = "user:local"
            def request(self, method, path, params=None, body=None):
                raise AssertionError("거절된 호출이 사이드카까지 갔다")

        res = Dispatcher(_Bridge()).handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "vinc_search", "arguments": {"query": "x", "limit": 200}}})
        self.assertTrue(res["result"]["isError"])
        self.assertEqual(res["result"]["_meta"]["vinc_invalid_argument"]["value"], 200)

    def test_비등재_도구_초과는_여전히_거부다(self):
        # tests/test_mcp_directory.py 의 vinc_graph 케이스가 정본 — 여기서는
        # 레지스트리에 **무엇이 있는지**를 고정한다(임의 확장 방지). 늘릴 때는
        # 이 줄을 함께 고쳐 "왜 그 도구는 잘려도 되는가"를 답해야 한다.
        #   vinc_search — 꼬리 행을 덜고 next_cursor 로 이어 읽는다.
        #   vinc_brief  — 발췌를 덜어도 grounds 에 doc_id 가 남아 되찾을 수 있다
        #                 ([[vinc.plan.mcp-agent-surface §4]]).
        #   vinc_graph  — 시드는 통째로 남기고 이웃의 props·one_liner, 엣지 evidence 를
        #                 던다. 이웃의 props 는 그 id 로 다시 부르면 되찾는다. 조밀한
        #                 노드의 depth 1 이 상한을 넘겨 시드 하나를 읽으러 온 --pull 이
        #                 죽었다(2026-09-05, Vinc#204 후속).
        self.assertEqual(set(mcp_tools.SHRINKERS), {"vinc_search", "vinc_brief", "vinc_graph"})


class ToolContractAdditionsTests(unittest.TestCase):
    """스키마·전달 계약 — cursor/detail 이 셋(스키마·핸들러·사이드카)에서 일치."""

    def test_스키마에_cursor_와_detail_이_있다(self):
        spec = next(t for t in mcp_tools.specs() if t["name"] == "vinc_search")
        props = spec["inputSchema"]["properties"]
        self.assertIn("cursor", props)
        self.assertEqual(props["detail"]["enum"], ["summary", "full"])

    def test_핸들러가_cursor_와_detail_을_전달한다(self):
        seen: dict = {}

        class _Bridge:
            owner = "user:local"
            def request(self, method, path, params=None, body=None):
                seen.update({"method": method, "path": path, "params": params})
                return {"ok": True}

        # 모양이 맞는 커서여야 한다: "CUR" 같은 쓰레기는 이제 MCP 층이 먼저 거절한다(D5).
        cur = mcp_tools._cursor_encode({"v": 1, "off": 20, "qh": "a" * 12})
        mcp_tools.call("vinc_search",
                       {"query": "x", "cursor": cur, "detail": "full"}, _Bridge())
        self.assertEqual(seen["path"], "/search")
        self.assertEqual(seen["params"]["cursor"], cur)
        self.assertEqual(seen["params"]["detail"], "full")


if __name__ == "__main__":
    unittest.main()
