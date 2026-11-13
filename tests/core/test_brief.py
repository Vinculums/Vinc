# -*- coding: utf-8 -*-
"""W1 `vinc_brief` — 질문 하나로 답변 재료를 모으는 결정론 조인 읽기.

  1. **순수 층**(드라이버 불요): 해소 규칙(엄격한 승리만 확정·비기면 되묻기),
     본문 고르기 폴백(one_liner → props → 발췌), 근거 id 수집, 그리고 축소기가
     **발췌를 먼저, 관계를 나중에** 덜어내는지.
  2. **실엔진 층**(ladybug 미설치 시 skip): 개념·부착 레코드·근거 있는 관계를 심고
     한 번의 호출로 본문·관계·근거가 함께 오는지. 아래 파생 클래스가 같은 검사를
     PK 네임스페이싱을 켠 채 한 번 더 돈다.

근거: [[vinc.plan.mcp-agent-surface §4]]
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import fragment as frag  # noqa: E402
from api import query as q  # noqa: E402
from store.embedded_backend import import_driver  # noqa: E402
from vinc_mcp import tools as mcp_tools  # noqa: E402

_DRIVER, _NAME = import_driver()


# ── 1. 순수 층 ────────────────────────────────────────────────────────────────

class ResolveTests(unittest.TestCase):
    """해소는 이 표면에서 판단이 들어가는 유일한 자리다 — 규칙이 전부 명시적이어야 한다."""

    def test_빈_결과는_none(self):
        self.assertEqual(q._brief_resolve("x", []), ("none", []))

    def test_제목이_질문과_같으면_그것이다(self):
        hits = [{"id": "concept:b", "title": "beta", "hit_tokens": 9, "title_tokens": 9},
                {"id": "concept:a", "title": "Alpha Core", "hit_tokens": 1,
                 "title_tokens": 1}]
        status, picked = q._brief_resolve("  alpha core  ", hits)
        # 이름을 댄 경우다 — 점수가 낮아도 제목 완전일치가 이긴다.
        self.assertEqual((status, picked[0]["id"]), ("resolved", "concept:a"))

    def test_토큰이_맞은_단독_후보는_확정이다(self):
        hits = [{"id": "concept:a", "title": "alpha", "hit_tokens": 1, "title_tokens": 1}]
        status, picked = q._brief_resolve("무엇이든", hits)
        self.assertEqual((status, picked[0]["id"]), ("resolved", "concept:a"))

    def test_토큰이_하나도_안_맞은_단독_후보는_되묻는다(self):
        """⚠ 뒤집힌 검사다. 전에는 후보가 하나면 hit_tokens 0 이어도 확정이었다 — 의미·
        그래프 팔만 올린 노드가 풀에 혼자 남으면 "이것을 물었다"로 답했다. 판정(RV-10):
        그 한 후보를 싣고 되묻는다. 근거: [[vinc.plan.rag-code-review-fixes-2026-09-29 §7]]"""
        hits = [{"id": "concept:a", "title": "alpha", "hit_tokens": 0, "title_tokens": 0,
                 "arms": ["semantic"]}]
        status, picked = q._brief_resolve("무엇이든", hits)
        self.assertEqual((status, [h["id"] for h in picked]), ("choose", ["concept:a"]))

    def test_같은_제목이_둘이면_되묻는다(self):
        """이름을 댔는데 그 이름이 둘이다(doc 미러 제목 충돌). 앞의 것으로 확정하면
        어느 쪽이 앞에 왔느냐가 답을 정한다."""
        hits = [{"id": "concept:doc.alpha", "title": "Alpha", "hit_tokens": 1,
                 "title_tokens": 1},
                {"id": "concept:alpha", "title": "alpha", "hit_tokens": 1,
                 "title_tokens": 1},
                {"id": "concept:beta", "title": "alpha beta", "hit_tokens": 1,
                 "title_tokens": 1}]
        status, picked = q._brief_resolve("alpha", hits)
        self.assertEqual(status, "choose")
        self.assertEqual([h["id"] for h in picked], ["concept:doc.alpha", "concept:alpha"])

    def test_동점이면_추측하지_않고_되묻는다(self):
        """이 갈래가 없던 시절 이 표면은 후보를 id 알파벳순으로 집어 질문과 무관한
        답을 냈다. 비겼다는 것은 모른다는 뜻이지 1번을 고르라는 뜻이 아니다."""
        hits = [{"id": "concept:a", "title": "alpha core", "hit_tokens": 1,
                 "title_tokens": 1},
                {"id": "concept:b", "title": "alpha edge", "hit_tokens": 1,
                 "title_tokens": 1}]
        status, picked = q._brief_resolve("alpha", hits)
        self.assertEqual(status, "choose")
        self.assertEqual([h["id"] for h in picked], ["concept:a", "concept:b"])

    def test_토큰_하나_차이로는_확신하지_않는다(self):
        """⚠ 이 검사는 뒤집힌 것이다. 전에는 토큰 하나만 더 맞히면 확정이었고, 그것이
        사실상 문턱이 없다는 뜻이었다 — 실측(2026-09-09, 정답 66문항): 답 가능 60건 중
        46건을 확신하고 **그중 36건이 틀렸다**. 문턱을 둘로 올리니 확신 15 · 오답 7 이다.
        되돌리려면 `VINC_BRIEF_STRICT=off`(:func:`api.query._brief_strict_on`)."""
        hits = [{"id": "concept:a", "title": "alpha core", "hit_tokens": 1,
                 "title_tokens": 1},
                {"id": "concept:b", "title": "alpha edge core", "hit_tokens": 2,
                 "title_tokens": 2}]
        self.assertEqual(q._brief_resolve("어느 alpha core 인가", hits)[0], "choose")

    def test_두_토큰_차로_이기면_확정이다(self):
        hits = [{"id": "concept:a", "title": "alpha core", "hit_tokens": 1,
                 "title_tokens": 1},
                {"id": "concept:b", "title": "alpha edge core deep", "hit_tokens": 3,
                 "title_tokens": 3}]
        status, picked = q._brief_resolve("어느 alpha core 인가", hits)
        self.assertEqual((status, picked[0]["id"]), ("resolved", "concept:b"))

    def test_여러_팔이_함께_올린_후보가_먼저다(self):
        """어휘와 의미가 같은 노드를 함께 가리켰다는 것은, 토큰 하나를 더 맞힌 것보다
        강한 증거다. `arms` 가 없는 호출부는 전부 0 이라 순서가 안 바뀐다."""
        hits = [{"id": "concept:a", "title": "가", "hit_tokens": 2, "title_tokens": 2,
                 "arms": ["title"]},
                {"id": "concept:b", "title": "나", "hit_tokens": 2, "title_tokens": 2,
                 "arms": ["semantic", "title"]}]
        self.assertEqual(q._brief_resolve("아무 질문", hits)[1][0]["id"], "concept:b")

    def test_비상구를_열면_옛_규칙으로_돌아간다(self):
        hits = [{"id": "concept:a", "title": "alpha core", "hit_tokens": 1,
                 "title_tokens": 1},
                {"id": "concept:b", "title": "alpha edge core", "hit_tokens": 2,
                 "title_tokens": 2}]
        os.environ["VINC_BRIEF_STRICT"] = "off"
        try:
            status, picked = q._brief_resolve("어느 alpha core 인가", hits)
            self.assertEqual((status, picked[0]["id"]), ("resolved", "concept:b"))
        finally:
            os.environ.pop("VINC_BRIEF_STRICT", None)

    def test_그래프_팔만_올라오면_확정하지_않는다(self):
        """청크 인접은 "관련 있다"는 뜻이지 "이것을 물었다"는 뜻이 아니다."""
        hits = [{"id": "concept:a", "title": "가", "hit_tokens": 0, "title_tokens": 0},
                {"id": "concept:b", "title": "나", "hit_tokens": 0, "title_tokens": 0}]
        self.assertEqual(q._brief_resolve("아무 질문", hits)[0], "choose")

    def test_되묻는_후보는_다섯까지다(self):
        hits = [{"id": f"concept:{i}", "title": "t", "hit_tokens": 0, "title_tokens": 0}
                for i in range(9)]
        with mock.patch.dict(os.environ):
            os.environ.pop("VINC_BRIEF_EXPOSE", None)
            self.assertEqual(len(q._brief_resolve("q", hits)[1]), q._BRIEF_CANDIDATES)
        self.assertEqual(q._BRIEF_CANDIDATES, 5)


class ExposeFlagTests(unittest.TestCase):
    """X1 의 노출 손잡이. 기본은 다섯이고, 잘못된 값은 기본으로 읽는다."""

    HITS = [{"id": f"concept:{i}", "title": "t", "hit_tokens": 0, "title_tokens": 0}
            for i in range(9)]

    def _n(self, value):
        with mock.patch.dict(os.environ):
            os.environ.pop("VINC_BRIEF_EXPOSE", None)
            if value is not None:
                os.environ["VINC_BRIEF_EXPOSE"] = value
            return len(q._brief_resolve("q", self.HITS)[1])

    def test_기본은_다섯이고_바꾸면_그만큼_싣는다(self):
        self.assertEqual(self._n(None), 5)
        self.assertEqual(self._n("3"), 3)          # 예전 기본으로 되돌리는 손잡이

    def test_잘못된_값은_기본으로_읽는다(self):
        for bad in ("", "abc", "0", "-2"):
            self.assertEqual(self._n(bad), 5 if bad in ("", "abc") else 1, bad)


class BodyTests(unittest.TestCase):
    """본문은 **인용**이지 생성이 아니다 — 어디서 왔는지를 늘 함께 낸다."""

    def test_one_liner_가_있으면_그것이다(self):
        b = q._brief_body({"one_liner": " 한 줄 ", "props": {"note": "무시"}})
        self.assertEqual((b["text"], b["src"]), ("한 줄", "one_liner"))

    def test_비면_props_로_내려간다(self):
        b = q._brief_body({"one_liner": "  ", "props": {"note": "카드 본문"}})
        self.assertEqual((b["text"], b["src"]), ("카드 본문", "props"))

    def test_이름없는_props_는_가장_긴_문장을_쓴다(self):
        b = q._brief_body({"props": {"zz": "짧다", "aa": "이것이 충분히 긴 문장이다."}})
        self.assertEqual((b["text"], b["src"]), ("이것이 충분히 긴 문장이다.", "props"))

    def test_이름없는_props_는_12자를_넘어야_본문이다(self):
        """12자 이하 값은 문장이 아니라 꼬리표로 보고 발췌로 내려간다(ask.ts 와 같은 값)."""
        ex = [{"excerpt": "발췌 문장.", "doc_title": "문서"}]
        b = q._brief_body({"props": {"zz": "가" * 12}, "excerpts": ex})
        self.assertEqual(b["src"], "doc")
        b = q._brief_body({"props": {"zz": "가" * 13}, "excerpts": ex})
        self.assertEqual((b["text"], b["src"]), ("가" * 13, "props"))

    def test_같은_길이면_키_이름으로_갈라_결정론이다(self):
        """ask.ts 는 객체 순서에 기댄다. 같은 질문이 늘 같은 답을 내야 하므로
        여기서는 키 이름으로 가른다 — 이 갈래가 이식의 유일한 의도적 차이다."""
        props = {"zz": "길이가 똑같은 문장 하나요", "aa": "길이가 똑같은 문장 하나요"}
        self.assertEqual(q._brief_body({"props": props})["text"],
                         q._brief_body({"props": dict(reversed(list(props.items())))})["text"])

    def test_마지막_폴백은_발췌이고_출처를_단다(self):
        b = q._brief_body({"props": {}, "excerpts": [
            {"excerpt": "  정본에서 인용한 문장.  ", "doc_title": "문서", "section": "§2"}]})
        self.assertEqual((b["text"], b["src"], b["cite"]),
                         ("정본에서 인용한 문장.", "doc", "문서 · §2"))

    def test_재료가_없으면_비었다고_말한다(self):
        b = q._brief_body({"props": {}, "excerpts": []})
        self.assertEqual((b["text"], b["src"]), ("", "none"))


class GroundsTests(unittest.TestCase):
    def test_노드_문서_관계_id_를_모아_정렬한다(self):
        g = q._brief_grounds("concept:a",
                             [{"doc_id": "d2"}, {"doc_id": "d1"}, {"doc_id": ""}],
                             [{"id": "record:r"}, {"id": ""}])
        self.assertEqual(g, ["concept:a", "d1", "d2", "record:r"])


class ShrinkTests(unittest.TestCase):
    """예산 초과는 정책으로 강등한다 — 발췌를 먼저 덜고 관계는 마지막까지 남긴다."""

    def _fat(self):
        return {"kind": "brief", "status": "resolved",
                "excerpts": [{"doc_id": f"d{i}", "excerpt": "가" * 400} for i in range(20)],
                "links": {"items": [{"id": f"concept:{i}", "title": "나" * 100}
                                    for i in range(20)]},
                "grounds": [f"d{i}" for i in range(20)]}

    def test_발췌를_먼저_덜고_관계는_남긴다(self):
        out = mcp_tools._shrink_brief_result(self._fat(), 6000)
        self.assertIsNotNone(out)
        self.assertLess(len(out["excerpts"]), 20, "발췌가 하나도 안 줄었다")
        # 예산에 들어가는 순간 멈추므로 관계는 손대지 않은 채로 남아야 한다.
        self.assertEqual(len(out["links"]["items"]), 20, "관계가 발췌보다 먼저 잘렸다")
        self.assertIn("shrunk to fit", out["note"])

    def test_발췌를_다_덜어도_모자라면_그때_관계를_던다(self):
        out = mcp_tools._shrink_brief_result(self._fat(), 1500)
        self.assertIsNotNone(out)
        self.assertEqual(out["excerpts"], [])
        self.assertLess(len(out["links"]["items"]), 20)

    def test_근거_id_는_잘려도_남는다(self):
        """발췌를 덜어도 grounds 의 doc_id 로 vinc_doc_get 이 되찾는다 — 그래서
        덜어내는 것이 안전하다. 이 목록까지 잘리면 길이 끊긴다."""
        out = mcp_tools._shrink_brief_result(self._fat(), 6000)
        self.assertEqual(len(out["grounds"]), 20)

    def test_한_줄도_안_들어가면_거부한다(self):
        self.assertIsNone(mcp_tools._shrink_brief_result(self._fat(), 10))

    def test_등재되어_있다(self):
        self.assertIs(mcp_tools.SHRINKERS["vinc_brief"],
                      mcp_tools._shrink_brief_result)


# ── 2. 실엔진 층 ──────────────────────────────────────────────────────────────

@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class QueryIdTests(unittest.TestCase):
    """질문 속 id 탐지 — 순수 함수. 이유는 `_BRIEF_ID_RE` 주석에 있다."""

    def test_한국어_슬러그를_통째로_잡고_ASCII_접두를_뒤에_둔다(self):
        self.assertEqual(q._brief_query_ids("concept:graph-md는-설명서가-아니다 는 뭔가"),
                         ["concept:graph-md는-설명서가-아니다", "concept:graph-md"])

    def test_한글로_시작하는_슬러그도_잡는다(self):
        self.assertEqual(q._brief_query_ids("concept:사과-나무 는?"), ["concept:사과-나무"])

    def test_끝의_마침표는_id_에_붙지_않는다(self):
        self.assertEqual(q._brief_query_ids("See concept:사과-나무."), ["concept:사과-나무"])
        self.assertEqual(q._brief_query_ids("See concept:alpha-core."), ["concept:alpha-core"])

    def test_조사가_붙은_ASCII_id_는_접두를_되물림_후보로_둔다(self):
        self.assertEqual(q._brief_query_ids("concept:alpha-core는 무엇인가"),
                         ["concept:alpha-core는", "concept:alpha-core"])


class BriefE2E(unittest.TestCase):
    OWNER = "user:a"

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
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "concept:alpha-core", "type": "concept", "title": "alpha core",
             "domain": "eng", "one_liner": "The core of alpha."},
            {"id": "concept:alpha-edge", "type": "concept", "title": "alpha edge",
             "domain": "eng"},
            {"id": "record:note-card", "type": "record", "title": "A note",
             "props": {"note": "카드 본문은 props.note 에 있다."}},
        ], "edges": [
            {"from": "concept:alpha-edge", "to": "concept:alpha-core",
             "type": "relates_to", "evidence": "둘은 같은 계약을 공유한다.",
             "confidence": 0.5},
            {"from": "record:note-card", "to": "concept:alpha-core",
             "type": "relates_to", "evidence": "측정으로 확인했다."},
        ]}, store=self.store)

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _brief(self, query, **kw):
        return q.brief(query, owner=self.OWNER, store=self.store, **kw)

    def test_한_번의_호출로_본문과_관계와_근거가_함께_온다(self):
        out = self._brief("alpha core")
        self.assertEqual(out["kind"], "brief")
        self.assertEqual(out["status"], "resolved")
        self.assertEqual(out["node"]["id"], "concept:alpha-core")
        # 본문은 생성이 아니라 그 노드의 one_liner 다.
        self.assertEqual(out["one_liner"], "The core of alpha.")
        self.assertEqual(out["body_src"], "one_liner")
        ids = {i["id"] for i in out["links"]["items"]}
        self.assertIn("concept:alpha-edge", ids)
        self.assertIn("record:note-card", ids)
        # 모든 주장이 딛고 선 id — 노드 자신과 관계 상대가 전부 들어 있다.
        self.assertIn("concept:alpha-core", out["grounds"])
        self.assertIn("record:note-card", out["grounds"])

    def test_links_는_연결수를_직접_싣고_neighbors_와_같다(self):
        """`degree` 는 개념·결정층(out+in)만 세고 `attached` 가 그 나머지인데, 둘이
        한 dict 에 나란히 나가면서 어느 쪽도 그 사실을 말하지 않았다. 실측
        2026-09-14: concept:node-resolution-accuracy 가 degree 9 · attached 50 으로
        답하는 동안 vinc_neighbors 는 count 59 를 냈고(85% 과소), 그 수를 합산한
        연결성 결론이 나갔다가 철회됐다. 그래서 `total` 을 직접 싣는다 — 받는 쪽이
        더해서 복원할 것을 남겨 두지 않는다. 두 표면이 같은 노드를 두고 다른 수를
        말하는 것이 이 결함의 실체이므로 여기서 **두 수를 직접 맞댄다**.
        근거: [[episode:2026-09-14-brief-carries-the-connectivity-instead-of-leaving-it-to-be-summed]]"""
        links = self._brief("alpha core")["links"]
        # 부착이 실제로 있어야 검사가 공회전하지 않는다(record:note-card 한 건).
        self.assertEqual(links["attached"], 1, "부착이 안 심겼으면 이 비교는 무의미하다")
        self.assertEqual(links["degree"], links["out"] + links["in"])
        self.assertEqual(links["total"], links["degree"] + links["attached"])
        self.assertGreater(links["total"], links["degree"],
                           "degree 가 연결수와 같아 보이면 이 검사가 결함을 못 잡는다")
        n = q.neighbors("concept:alpha-core", owner=self.OWNER, store=self.store)
        self.assertEqual(links["total"], n["count"],
                         "brief 와 neighbors 가 같은 노드를 두고 다른 수를 말한다")

    def test_근거_있는_관계가_앞에_선다(self):
        items = self._brief("alpha core")["links"]["items"]
        self.assertEqual(items[0]["id"], "concept:alpha-edge")
        self.assertTrue(items[0]["evidence"])

    def test_부착_레코드의_카드_본문이_실린다(self):
        items = self._brief("alpha core")["links"]["items"]
        note = {i["id"]: i["note"] for i in items}["record:note-card"]
        self.assertEqual(note, "카드 본문은 props.note 에 있다.")

    def test_비기면_되묻고_노드를_고르지_않는다(self):
        # ⚠ 레코드 id 를 `record:alpha-...` 로 두면 안 된다 — 노드 검색은 **id 도**
        # 매치 필드라(matched_in="id") 그 레코드까지 후보로 올라와 이 검사가
        # 재는 것이 "두 개념의 동점"이 아니게 된다.
        out = self._brief("alpha")
        self.assertEqual(out["status"], "choose")
        self.assertEqual({c["id"] for c in out["candidates"]},
                         {"concept:alpha-core", "concept:alpha-edge"})
        self.assertNotIn("node", out)

    def test_같은_제목이_둘이면_이름을_대도_되묻는다(self):
        """RV-10. doc 미러가 주제 개념의 제목을 글자 그대로 물려받으면 같은 제목이 둘이
        된다. 전에는 id 순서로 앞선 쪽을 확정했다. 둘 다 후보 맨 앞에 서야 한다."""
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "concept:doc.alpha-core", "type": "concept", "title": "alpha core",
             "domain": "eng"}]}, store=self.store)
        out = self._brief("alpha core")
        self.assertEqual(out["status"], "choose", out.get("node"))
        self.assertNotIn("node", out)
        self.assertEqual({c["id"] for c in out["candidates"][:2]},
                         {"concept:alpha-core", "concept:doc.alpha-core"})
        # 나머지 풀도 뒤에 실린다 — 이름이 둘이라고 목록이 둘로 줄지 않는다.
        self.assertIn("concept:alpha-edge", [c["id"] for c in out["candidates"]])

    def test_맞는_것이_없으면_없다고_말한다(self):
        out = self._brief("존재하지않는질문어휘")
        self.assertEqual(out["status"], "none")
        self.assertEqual(out["candidates"], [])

    def test_관계는_근거_문장까지_실어_인용할_수_있다(self):
        """W1 의 exit test — 에이전트는 "근거가 있다"가 아니라 **그 문장**을 인용한다.
        불리언만으로는 답에 각주를 달 수 없다."""
        items = {i["id"]: i for i in self._brief("alpha core")["links"]["items"]}
        self.assertEqual(items["concept:alpha-edge"]["evidence_text"],
                         "둘은 같은 계약을 공유한다.")

    def test_화면_경로의_페이로드는_그대로다(self):
        """근거 문장은 요청할 때만 실린다 — 기본 node_context 는 키 자체가 없어야
        하고, 그래야 앱이 받는 응답이 이 변경으로 커지지 않는다."""
        ctx = q.node_context(owner=self.OWNER, node="concept:alpha-core",
                             store=self.store)
        self.assertTrue(ctx["links"]["items"], "관계가 없으면 이 검사는 공허하다")
        for it in ctx["links"]["items"]:
            self.assertNotIn("evidence_text", it)

    def test_남의_그래프는_보이지_않는다(self):
        """SEC-1 — owner 격리는 조인 읽기에서도 성립해야 한다."""
        out = q.brief("alpha core", owner="user:b", store=self.store)
        self.assertEqual(out["status"], "none")


# ── 3. Record 해소와 폴스루 — 2026-08-30 재측정에서 나온 결함 ────────────────

@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class BriefRecordE2E(unittest.TestCase):
    """조인 읽기가 **Record 를 못 열던** 결함의 회귀 검사.

    node_context 가 id 접두사로 라벨을 꺼낸 뒤 의미층(Concept·Decision)이 아니면
    Concept 으로 접어 버려서, `record:` id 는 Concept 조회 0행 → Episode 탐침 0행 →
    "unknown node" 로 답했다. brief 는 그것을 status="none" 으로 옮겨 담았는데 계약상
    none 은 "아무것도 안 맞았다"라, **맞았는데 못 읽었다**가 안 맞은 것처럼 보고됐다.
    2026-08-30 재측정에서 Record 표적 5건이 전부 그렇게 실패했다(Concept 6/6 ·
    Decision 2/2 는 정상). 근거:
    [[record:brief-returns-none-when-its-winner-is-a-record-2026-08-30]]

    아래 두 시드는 그 재측정의 태스크 0·A 를 그대로 옮긴 것이다 — 이 그래프의 사실형
    제목은 대부분 Record 에 실리므로, 가장 싸게 얻을 수 있는 답을 정확히 못 얻고
    있었다."""

    OWNER = "user:a"
    NOTE = "6 MCP 호출 중 유용은 3, 낭비가 3이었다."

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
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "concept:call-efficiency", "type": "concept",
             "title": "call efficiency", "domain": "mcp",
             "one_liner": "호출 수로 재는 효율."},
            # 태스크 0 — 물은 것은 이 기록의 **본문**이고, 본문은 props 에 있다.
            {"id": "record:agent-test-card", "type": "record",
             "title": "agent test card", "domain": "mcp",
             "props": {"note": BriefRecordE2E.NOTE}},
            # 태스크 A — 제목 자체가 사실이라 본문 없이 제목만으로 답이 된다.
            {"id": "record:cap-outweighs-depth", "type": "record", "domain": "ai",
             "title": "the 24-candidate cap costs 3.2x what the depth cutoff does"},
        ], "edges": [
            {"from": "record:agent-test-card", "to": "concept:call-efficiency",
             "type": "relates_to"},
        ]}, store=self.store)

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _brief(self, query, **kw):
        return q.brief(query, owner=self.OWNER, store=self.store, **kw)

    def test_기록이_해소된다(self):
        out = self._brief("agent test card")
        self.assertEqual(out["status"], "resolved", out.get("note"))
        self.assertEqual(out["node"]["id"], "record:agent-test-card")
        self.assertEqual(out["node"]["kind"], "record")

    def test_기록의_카드_본문이_답으로_온다(self):
        """태스크 0 — Record 에는 one_liner 열이 없다. props 폴백이 안 걸리면
        해소는 되고 본문만 빈, 더 알아채기 어려운 실패가 된다."""
        out = self._brief("agent test card")
        self.assertEqual(out["one_liner"], self.NOTE)
        self.assertEqual(out["body_src"], "props")

    def test_제목만으로_답이_되는_기록도_한_번에_온다(self):
        """태스크 A — 사실형 제목은 이 그래프에서 가장 싼 답이다."""
        out = self._brief("24-candidate cap costs what the depth cutoff does")
        self.assertEqual(out["status"], "resolved", out.get("note"))
        self.assertEqual(out["node"]["id"], "record:cap-outweighs-depth")
        self.assertIn("3.2x", out["node"]["title"])

    def test_기록도_자기_부착을_보여_준다(self):
        """vinc_neighbors 는 관계를 양쪽 끝에서 보여 준다고 계약에 적어 두었다.
        여기서 links 를 비워 두면 부착이 있는 기록이 '연결 0'으로 답해 두 도구가
        같은 그래프를 두고 어긋난다."""
        out = self._brief("agent test card")
        self.assertEqual([i["id"] for i in out["links"]["items"]],
                         ["concept:call-efficiency"])
        self.assertEqual(out["links"]["degree"], 1)
        self.assertIn("concept:call-efficiency", out["grounds"])

    def test_기록은_근거_없음이_사실이다(self):
        """근거가 저작되지 않은 부착은 False 로 답한다 — 결측이 아니라 사실이므로
        with_evidence 를 켜도 빈 문장이 온다. (v16 부터 ATTACHES_TO 에 열이 있어
        저작하면 실린다 — 그 경로는 test_fragment_edge_tenancy 가 지킨다.)"""
        it = self._brief("agent test card")["links"]["items"][0]
        self.assertFalse(it["evidence"])
        self.assertEqual(it["evidence_text"], "")

    def test_안_열리는_1위는_건너뛰고_그_사실을_밝힌다(self):
        """폴스루. 조용히 다음 후보를 답하면 이 표면은 '질문에 맞는 노드'가 아니라
        '열리기만 하는 노드'를 답하게 되므로, 건너뛴 것을 반드시 보고한다.

        지금은 node_hits 가 Concept·Record·Decision 만 싣고 셋 다 열리므로 이 갈래는
        경합(검색과 상세 사이의 삭제·소유권 변화)에서만 열린다. 상세를 갈아 끼워
        그 경합을 결정론으로 재현한다."""
        real, seen = q.node_context, []

        def flaky(*, node, **kw):
            seen.append(node)
            if node == "record:agent-test-card":
                return {"executed": False, "note": "unknown node: " + node}
            return real(node=node, **kw)

        q.node_context = flaky
        try:
            out = self._brief("agent test card call")   # 여유 2 (3 대 1)
        finally:
            q.node_context = real
        self.assertEqual(out["status"], "resolved", out.get("note"))
        self.assertNotEqual(out["node"]["id"], "record:agent-test-card")
        self.assertEqual([f["id"] for f in out["fell_through"]],
                         ["record:agent-test-card"])
        self.assertIn("unknown node", out["fell_through"][0]["note"])

    def test_제목_언어가_다르면_id_로_맞아도_진다(self):
        """⚠ 특성화 검사 — 바람직한 동작을 고정하는 것이 **아니라**, 남아 있는 결함을
        고정한다. 2026-08-30 재측정의 태스크 C 다.

        원인은 해소 규칙의 동점 처리다: hit_tokens 가 비기면 title_tokens 로 가르는데,
        질의와 제목의 언어가 다른 노드는 id 로 정확히 맞아도 title_tokens 가 0 이라
        구조적으로 진다.

        ⚠ **엄격 판정(2026-09-09)은 이 갈래를 못 닫는다.** 전체로는 확신 오답을 36 에서
        7 로 줄였는데 여기는 남는다 — hit 이 2 대 2 로 비겨도 title 이 2 대 0 이라
        **제목 토큰만으로 여유 2 가 채워지기 때문**이다.

        닫는 길("맞은 토큰이 비기면 제목 토큰만으로는 확신하지 않는다")은 **재 보고
        기각했다**(2026-09-09, 같은 정답층 탐침): 확신 오답이 5 에서 0 으로 가는 대신
        확신이 13건에서 2건으로 무너지고 맞는 확신 여섯이 함께 사라진다. 이 갈래 하나를
        닫자고 확신을 통째로 포기할 수는 없다. 그래서 수를 남긴다.
        근거: [[record:title-language-gap-survives-the-vector-arm-2026-08-30]]"""
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "concept:silent-truncation", "type": "concept",
             "title": "무음 절단", "domain": "mcp"},
            {"id": "record:embed-dim-split", "type": "record", "domain": "eng",
             "title": "the embedding dimension split is silent and connected to "
                      "the namespace change"},
        ]}, store=self.store)
        hits = {h["id"]: h for h in q.search(
            "What is silent truncation connected to?",
            owner=self.OWNER, store=self.store)["node_hits"]}
        # 두 후보가 맞은 토큰 수는 같다 — 갈리는 것은 제목 토큰뿐이다.
        self.assertEqual(hits["concept:silent-truncation"]["hit_tokens"],
                         hits["record:embed-dim-split"]["hit_tokens"])
        self.assertEqual(hits["concept:silent-truncation"]["title_tokens"], 0)
        self.assertEqual(hits["concept:silent-truncation"]["matched_in"], "id")

        out = self._brief("What is silent truncation connected to?")
        # 물은 것은 concept:silent-truncation 인데 기록이 이긴다. 조용하다 —
        # fell_through 도 비어 있다(1위가 열렸으니 폴스루가 열릴 일이 없다).
        self.assertEqual(out["status"], "resolved")
        self.assertEqual(out["node"]["id"], "record:embed-dim-split")
        self.assertEqual(out.get("fell_through", []), [])

    def test_질문이_id_를_담고_있으면_랭킹을_거치지_않고_그_노드다(self):
        """재측정 태스크 C 의 **실측 질의**는 id 를 그대로 담고 있었고
        ("What is concept:silent-truncation connected to?"), 그런데도 긴 제목의
        기록이 랭킹을 이겨 조용한 오답이 났다. 체인 arm v1.1 규칙(묻는 쪽이 이미
        준 id 는 검색하지 않는다)의 내재화가 이 모양을 닫는다. 랭킹 자체는
        그대로다 — id 없는 평문 질의는 위 특성화 검사가 지키는 열린 결정."""
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "concept:silent-truncation", "type": "concept",
             "title": "무음 절단", "domain": "mcp"},
            {"id": "record:embed-dim-split", "type": "record", "domain": "eng",
             "title": "the embedding dimension split is silent and connected to "
                      "the namespace change"},
        ]}, store=self.store)
        out = self._brief("What is concept:silent-truncation connected to?")
        self.assertEqual(out["status"], "resolved", out.get("note"))
        self.assertEqual(out["node"]["id"], "concept:silent-truncation")
        self.assertEqual(out.get("resolved_via"), "id")
        self.assertEqual(out["node"]["kind"], "concept")
        # 직참조도 근거는 같은 꼴로 실린다.
        self.assertIn("concept:silent-truncation", out["grounds"])

    def test_질문의_id_가_안_열리면_없다고_답하고_랭킹으로_가지_않는다(self):
        """준 id 가 없는 노드면 status 는 none 이고 unreadable 이 그 id 를 싣는다.
        나머지 낱말이 다른 노드(record:agent-test-card)에 맞더라도 랭킹 승자를
        대신 내지 않는다 — 전에는 resolved 로 그 노드를 냈다(감사 §5 결함 11)."""
        out = self._brief("concept:no-such-node agent test card")
        self.assertEqual(out["status"], "none", out.get("node"))
        self.assertNotIn("node", out)
        self.assertNotIn("resolved_via", out)
        self.assertNotIn("fell_through", out)
        self.assertEqual(out["candidates"], [])
        unread = out.get("unreadable", [])
        self.assertEqual([u["id"] for u in unread], ["concept:no-such-node"])
        self.assertIn("unknown node", unread[0]["note"])
        self.assertIn("unknown node", out["note"])

    def test_id_만_담은_질문도_없으면_none(self):
        """감사 재현 인자 그대로: 질문 전체가 없는 id 하나."""
        out = self._brief("concept:does-not-exist-xyz")
        self.assertEqual(out["status"], "none")
        self.assertEqual([u["id"] for u in out["unreadable"]],
                         ["concept:does-not-exist-xyz"])

    def test_한국어_슬러그_id_는_잘리지_않고_그_노드를_연다(self):
        """ASCII 로 조인 패턴은 첫 한글에서 id 를 잘랐고, 잘린 접두가 실재하면
        **다른 노드**를 직참조로 확정했다 — fell_through 도 빈 채로."""
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "concept:graph-md", "type": "concept", "title": "graph md",
             "domain": "eng"},
            {"id": "concept:graph-md는-설명서가-아니다", "type": "concept",
             "title": "GRAPH.md는 설명서가 아니다", "domain": "eng"},
        ]}, store=self.store)
        out = self._brief("concept:graph-md는-설명서가-아니다 에 대해 알려줘")
        self.assertEqual(out["status"], "resolved", out.get("note"))
        self.assertEqual(out["node"]["id"], "concept:graph-md는-설명서가-아니다")
        self.assertEqual(out.get("resolved_via"), "id")
        self.assertEqual(out.get("fell_through", []), [])

    def test_조사가_붙은_id_는_접두로_열고_그_대체를_밝힌다(self):
        """한국어 문장은 조사를 id 에 붙여 쓴다. 긴 쪽이 안 열리면 ASCII 접두를
        열되, 조용히 갈아타지 않고 fell_through 에 싣는다."""
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "concept:graph-md", "type": "concept", "title": "graph md",
             "domain": "eng"},
        ]}, store=self.store)
        out = self._brief("concept:graph-md는 무엇인가")
        self.assertEqual(out["status"], "resolved", out.get("note"))
        self.assertEqual(out["node"]["id"], "concept:graph-md")
        self.assertEqual(out.get("resolved_via"), "id")
        self.assertEqual([f["id"] for f in out.get("fell_through", [])],
                         ["concept:graph-md는"])

    def test_아무것도_못_읽으면_읽지_못했다고_말한다(self):
        """전부 실패하면 status 는 none 으로 남지만, 무엇을 못 읽었는지는 남긴다 —
        '안 맞았다'와 '맞았는데 못 읽었다'는 다른 사건이고 호출부의 다음 수가 다르다."""
        real = q.node_context
        q.node_context = lambda *, node, **kw: {"executed": False,
                                                "note": "unknown node: " + node}
        try:
            out = self._brief("agent test card call")   # 여유 2 (3 대 1)
        finally:
            q.node_context = real
        self.assertEqual(out["status"], "none")
        self.assertTrue(out["unreadable"])
        self.assertIn("unknown node", out["note"])


#: 아래 파생 클래스가 달고 다니는 설명.
_NS_DOC = """같은 검사 전부를 **PK 네임스페이싱을 켠 채** 다시 돈다.

조인 읽기는 search 가 준 id 를 node_context 에 다시 넣는다. 그 사이에서 물리 키와
논리 id 를 헷갈리면 상세가 통째로 빈 채 status 만 resolved 로 남는다 — 기본 스위트는
플래그가 꺼져 있어 초록으로 통과한다. 그래서 검사를 새로 쓰지 않고 있는 검사를 두
모드로 돌린다."""


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class BriefListResolvedE2E(unittest.TestCase):
    """꺼져 있으면 응답이 바이트까지 그대로이고, 켜면 확정에 순위 목록만 더한다.
    status 와 node 는 그대로다 — 목록이 답을 바꾸지 않는다(X1)."""

    OWNER = BriefE2E.OWNER
    setUp, tearDown = BriefE2E.setUp, BriefE2E.tearDown
    FLAG = "VINC_BRIEF_LIST_RESOLVED"

    def _brief(self, query, flag=None):
        with mock.patch.dict(os.environ):
            os.environ.pop(self.FLAG, None)
            if flag is not None:
                os.environ[self.FLAG] = flag
            return q.brief(query, owner=self.OWNER, store=self.store)

    def test_꺼져_있으면_응답이_그대로다(self):
        for query in ("alpha core", "alpha", "concept:alpha-core"):
            self._brief(query)                       # 지연 색인 빌드를 먼저 치른다
            base = json.dumps(self._brief(query), ensure_ascii=False)
            for off in ("", "off", "0"):
                self.assertEqual(json.dumps(self._brief(query, off), ensure_ascii=False),
                                 base, (query, off))

    def test_켜면_확정은_그대로이고_목록이_곁에_실린다(self):
        self._brief("alpha core")
        off = self._brief("alpha core")
        on = self._brief("alpha core", "on")
        self.assertEqual((off["status"], on["status"]), ("resolved", "resolved"))
        self.assertEqual(on["node"], off["node"])
        self.assertEqual(off["candidates"], [])
        self.assertEqual(on["candidates"][0]["id"], on["node"]["id"])


def _namespaced(base):
    """base 의 검사 전부를 플래그 켠 채 도는 클래스로 만든다."""

    class _Ns(base):
        def setUp(self):
            self._pk_prev = os.environ.get("VINC_PK_NAMESPACED")
            os.environ["VINC_PK_NAMESPACED"] = "on"
            super().setUp()

        def tearDown(self):
            try:
                super().tearDown()
            finally:
                if self._pk_prev is None:
                    os.environ.pop("VINC_PK_NAMESPACED", None)
                else:
                    os.environ["VINC_PK_NAMESPACED"] = self._pk_prev

    _Ns.__name__ = _Ns.__qualname__ = base.__name__ + "UnderNamespacing"
    _Ns.__doc__ = _NS_DOC
    return _Ns


BriefUnderNamespacing = _namespaced(BriefE2E)


if __name__ == "__main__":
    unittest.main()

BriefRecordUnderNamespacing = _namespaced(BriefRecordE2E)
