# -*- coding: utf-8 -*-
"""LongMemEval 하네스의 **숫자가 맞는가** — scripts/benchmark/longmemeval.py.

왜 테스트하나: 이 하네스가 내는 가격표로 "전체 벤치마크에 돈을 쓸 것인가"를 사람이
결정한다(0.9.0 결정 게이트 3). 층화·채점·가격 산술이 틀리면 그 결정이 통째로 틀리는데,
셋 다 **그럴듯한 숫자**를 내놓기 때문에 눈으로는 안 잡힌다. 손계산과 대조해 못박는다.

스토어도 데이터셋도 네트워크도 없이 도는 순수 함수만 본다 — 265MB 데이터셋이 없는
CI 에서 **skip 되면 안 된다**(조용한 skip 이 이 리포의 상습 함정이다).
"""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

_SRC = (pathlib.Path(__file__).resolve().parents[1]
        / "scripts" / "benchmark" / "longmemeval.py")


def _load():
    spec = importlib.util.spec_from_file_location("_vinc_lme", _SRC)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


M = _load()


class Bucket(unittest.TestCase):
    def test_single_session_variants_fold_together(self):
        for raw in ("single-session-user", "single-session-assistant",
                    "single-session-preference"):
            self.assertEqual(M.bucket("q1", raw), "single-session")

    def test_abstention_wins_over_raw_type(self):
        # 기권 문항은 원시 유형이 무엇이든 기권이다. `_abs` 접미가 정본.
        self.assertEqual(M.bucket("q1_abs", "multi-session"), "abstention")

    def test_unknown_type_passes_through_instead_of_being_swallowed(self):
        # 조용히 'other' 로 삼키면 데이터셋이 바뀐 것을 아무도 모른다.
        self.assertEqual(M.bucket("q1", "brand-new-type"), "brand-new-type")


class Stratify(unittest.TestCase):
    def _rows(self):
        rows = []
        for i in range(60):
            rows.append({"question_id": f"a{i}", "question_type": "single-session-user"})
        for i in range(30):
            rows.append({"question_id": f"b{i}", "question_type": "multi-session"})
        for i in range(10):
            rows.append({"question_id": f"c{i}", "question_type": "temporal-reasoning"})
        return rows

    def test_exact_size_and_determinism(self):
        a = M.stratify(self._rows(), 20, 0)
        b = M.stratify(self._rows(), 20, 0)
        self.assertEqual(len(a), 20)
        self.assertEqual([r["question_id"] for r in a],
                         [r["question_id"] for r in b])

    def test_every_type_is_represented(self):
        # 앞에서 20개를 자르면 한 유형으로 쏠린다. 층화가 그걸 막는지 본다.
        got = {M.bucket(r["question_id"], r["question_type"])
               for r in M.stratify(self._rows(), 20, 0)}
        self.assertEqual(got, {"single-session", "multi-session", "temporal-reasoning"})

    def test_no_duplicates_when_rounding_needs_topping_up(self):
        picked = M.stratify(self._rows(), 47, 3)
        ids = [r["question_id"] for r in picked]
        self.assertEqual(len(ids), len(set(ids)))

    def test_asking_for_more_than_exists_returns_everything_once(self):
        rows = self._rows()
        picked = M.stratify(rows, 500, 1)
        self.assertEqual(len(picked), len(rows))


class Score(unittest.TestCase):
    def test_recall_and_mrr_against_hand_computed_values(self):
        rows = [
            {"question_id": "q1", "type": "multi-session", "gold": 1, "rank": 1},
            {"question_id": "q2", "type": "multi-session", "gold": 1, "rank": 4},
            {"question_id": "q3", "type": "multi-session", "gold": 1, "rank": 0},
            {"question_id": "q4", "type": "multi-session", "gold": 2, "rank": 2},
        ]
        got = M.score(rows, 10)["overall"]
        self.assertEqual(got["gradable"], 4)
        self.assertEqual(got["recall@1"], 0.25)          # q1 만
        self.assertEqual(got["recall@10"], 0.75)         # q3 빼고
        # (1/1 + 1/4 + 0 + 1/2) / 4 = 0.4375 → 0.438
        self.assertEqual(got["mrr"], 0.438)

    def test_abstention_is_not_graded_by_retrieval(self):
        # 정답 세션이 없는 문항을 recall 0 으로 세면 기준선이 거짓으로 낮아진다.
        rows = [
            {"question_id": "q1", "type": "multi-session", "gold": 1, "rank": 1},
            {"question_id": "q2_abs", "type": "abstention", "gold": 0, "rank": 0},
        ]
        s = M.score(rows, 10)
        self.assertEqual(s["overall"]["gradable"], 1)
        self.assertEqual(s["overall"]["recall@1"], 1.0)
        self.assertEqual(s["by_type"]["abstention"]["gradable"], 0)
        self.assertIn("note", s["by_type"]["abstention"])


class Price(unittest.TestCase):
    def _report(self):
        return {
            "config": {"chars_per_token": 4.0, "tokens_exact": False},
            "ingest": {"seconds": 100.0, "docs": 18, "per_question": [
                {"question_id": "q1", "sessions": 10, "chars": 400, "tokens": 100},
                {"question_id": "q2", "sessions": 10, "chars": 400, "tokens": 100},
            ]},
            "retrieve": {"seconds": 10.0, "context_chars": 800},
        }

    def test_scale_and_batch_discount(self):
        p = M.price(self._report(), model="claude-opus-5", total_questions=500,
                    rate_in=None, rate_out=None)
        self.assertEqual(p["scale"], 250.0)              # 500 / 2
        self.assertEqual(p["rates_usd_per_mtok"], {"in": 5.0, "out": 25.0})
        for arm in p["arms"].values():
            self.assertAlmostEqual(arm["full_usd_batch"], arm["full_usd"] * 0.5, places=1)

    def test_host_only_control_input_equals_the_whole_haystack(self):
        # 통제군은 메모리가 없으니 건초더미를 통째로 먹는다. 이 팔이 비용을 지배한다는
        # 것이 가격표의 핵심 소견이라 산술이 틀리면 안 된다.
        p = M.price(self._report(), model="claude-opus-5", total_questions=500,
                    rate_in=None, rate_out=None)
        self.assertEqual(p["arms"]["host_only_control"]["sample_in_tokens"], 200)
        self.assertEqual(p["measured"]["haystack_tokens_sample"], 200)

    def test_call_counts_scale_with_sessions_not_questions(self):
        # 추출은 **세션마다** 한 번이다. 문항 수로 세면 2만5천 호출이 500 으로 보여서
        # 요율한도 문제가 통째로 안 보인다.
        p = M.price(self._report(), model="claude-opus-5", total_questions=500,
                    rate_in=None, rate_out=None)
        self.assertEqual(p["arms"]["vinc_extract_ingest"]["sample_calls"], 18)  # docs(비어있지 않은 것)
        self.assertEqual(p["arms"]["vinc_extract_ingest"]["full_calls"], 4500)
        self.assertEqual(p["arms"]["host_only_control"]["sample_calls"], 2)      # 문항당 1

    def test_rate_override_beats_the_table(self):
        p = M.price(self._report(), model="claude-opus-5", total_questions=500,
                    rate_in=1.0, rate_out=2.0)
        self.assertEqual(p["rates_usd_per_mtok"], {"in": 1.0, "out": 2.0})

    def test_unknown_model_refuses_instead_of_guessing_a_rate(self):
        with self.assertRaises(SystemExit):
            M.price(self._report(), model="some-new-model", total_questions=500,
                    rate_in=None, rate_out=None)


class ExtractedArmRefuses(unittest.TestCase):
    """`--arm extracted` 는 **키가 있어도** 거절해야 한다.

    실제로 있던 결함이다: `ingest()` 가 arm 으로 분기하지 않아서, 키가 있으면 문서 경로를
    돌려 놓고 리포트에 "extracted" 라고 적었다. 조용히 다른 것을 재는 고장이라 값이 가장
    비싸다. 키 유무로 갈리던 가드를 무조건 거절로 바꿨고, 이 테스트가 되돌아가는 것을 막는다."""

    def _args(self):
        import argparse
        return argparse.Namespace(arm="extracted", dataset="x", data="y", out="z",
                                  n=1, seed=0, top_k=10, chars_per_token=4.0,
                                  base_url="https://example.invalid", verbose=False,
                                  model="claude-opus-5", full=500,
                                  rate_in=None, rate_out=None)

    def _assert_env_untouched(self, before: dict) -> None:
        # 거절로 끝난 호출이 env 를 남기면 같은 프로세스의 이후 load_config() 전부가
        # 오염된다(실사고: ThemeStore 가 ./y.themes.json 을 공유해 먼 테마 테스트가
        # 깨졌다). 원거리 고장 대신 여기서 죽는다.
        import os
        for k, v in before.items():
            self.assertEqual(os.environ.get(k), v,
                             f"cmd_run 거절 경로가 {k} 를 남겼다 — env 설정은 거절 뒤여야 한다")

    def test_refuses_without_key(self):
        import os
        keep = os.environ.pop("ANTHROPIC_API_KEY", None)
        before = {k: os.environ.get(k) for k in ("MEMORY_DATA_DIR", "MEMORY_STORE_BACKEND")}
        try:
            with self.assertRaises(SystemExit):
                M.cmd_run(self._args())
            self._assert_env_untouched(before)
        finally:
            if keep is not None:
                os.environ["ANTHROPIC_API_KEY"] = keep

    def test_refuses_even_with_a_key(self):
        import os
        keep = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = "sk-test-not-real"
        before = {k: os.environ.get(k) for k in ("MEMORY_DATA_DIR", "MEMORY_STORE_BACKEND")}
        try:
            with self.assertRaises(SystemExit):
                M.cmd_run(self._args())
            self._assert_env_untouched(before)
        finally:
            if keep is None:
                os.environ.pop("ANTHROPIC_API_KEY", None)
            else:
                os.environ["ANTHROPIC_API_KEY"] = keep


class LlmTransport(unittest.TestCase):
    """LLM 경로는 이 세션에서 안 돌았다(키가 없다). 그렇다고 **미검증**으로 둘 수는
    없어서 전송만 가짜로 끼워 호출 모양과 파싱을 못박는다."""

    def test_llm_sends_system_and_parses_text_blocks(self):
        seen = {}

        def fake_post(url, body, api_key, timeout=60.0):
            seen.update(url=url, body=body, key=api_key)
            return {"content": [{"type": "thinking", "thinking": "..."},
                                {"type": "text", "text": "CORRECT"}],
                    "usage": {"input_tokens": 11, "output_tokens": 2}}

        orig, M._post = M._post, fake_post
        try:
            got = M.llm("sys", "usr", model="claude-opus-5", api_key="k",
                        base_url="https://example.invalid/", max_tokens=16)
        finally:
            M._post = orig
        self.assertEqual(got["text"], "CORRECT")          # thinking 블록은 안 섞인다
        self.assertEqual(got["usage"]["input_tokens"], 11)
        self.assertEqual(seen["url"], "https://example.invalid/v1/messages")
        self.assertEqual(seen["body"]["system"], "sys")
        self.assertEqual(seen["body"]["model"], "claude-opus-5")

    def test_token_counter_falls_back_to_estimate_when_the_api_fails(self):
        t = M.Tokens("claude-opus-5", 4.0, "k", "https://example.invalid")
        self.assertTrue(t.exact)

        def boom(*a, **k):
            raise RuntimeError("network down")

        orig, M._post = M._post, boom
        try:
            got = t(" " * 40)
        finally:
            M._post = orig
        # 실패를 조용히 0 으로 만들지 않고 추정으로 내려가며 그 사실을 남긴다.
        self.assertEqual(got, 10)
        self.assertFalse(t.exact)


class DocId(unittest.TestCase):
    def test_unsafe_characters_are_folded(self):
        self.assertEqual(M.doc_id_for("q/1 a", "s:2"), "lme.q-1-a.s-2")

    def test_distinct_sessions_stay_distinct(self):
        self.assertNotEqual(M.doc_id_for("q1", "s1"), M.doc_id_for("q1", "s2"))


if __name__ == "__main__":
    unittest.main()
