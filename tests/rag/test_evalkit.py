# -*- coding: utf-8 -*-
"""evalkit(RAG eval v2 F1) — 거부 규칙과 전송 봉투를 잠근다. 스토어·네트워크 없이 돈다.

지키는 것: 런타임이 다르면 거부한다 · 락에 판이 없으면 같다고 가정하지 않는다 · CLI 의
`status: none` 은 답이고 다른 실패는 던진다 · v1 의 404(못 읽은 노드)는 인프로세스와 같은
모양의 답이다 · 경로 대조는 채점 필드 전부를 본다.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from rag_eval.evalkit import funnel, grading, run_nodes, runtime, transports  # noqa: E402

RT = {"onnxruntime": "1.30.0", "tokenizers": "0.23.2", "numpy": "2.5.3"}


class RuntimeTests(unittest.TestCase):
    def test_같은_판이면_불일치가_없다(self):
        self.assertEqual(runtime.runtime_mismatch({"encoder_runtime": RT},
                                                  {"encoder_runtime": RT}), [])

    def test_onnxruntime_만_달라도_잡는다(self):
        got = runtime.runtime_mismatch({"encoder_runtime": RT},
                                       {"encoder_runtime": {**RT, "onnxruntime": "1.29.0"}})
        self.assertEqual(len(got), 1)
        self.assertIn("onnxruntime", got[0])

    def test_락에_판이_없으면_불일치로_센다(self):
        self.assertEqual(len(runtime.runtime_mismatch({}, {"encoder_runtime": RT})), 3)

    def test_락의_두_핀이_모두_런타임을_적는다(self):
        stores = json.loads(runtime.LOCK.read_text(encoding="utf-8"))["stores"]
        for key in ("m3-2026-09-13", "m3-ort130-2026-09-28"):
            with self.subTest(key=key):
                self.assertTrue(all(stores[key]["encoder_runtime"].get(p)
                                    for p in runtime.RUNTIME_PACKAGES))


def _done(code: int, out: dict | str, err: str = "") -> subprocess.CompletedProcess:
    text = out if isinstance(out, str) else json.dumps(out)
    if code == 1:
        return subprocess.CompletedProcess([], code, stdout="", stderr=text)
    return subprocess.CompletedProcess([], code, stdout=text, stderr=err)


class CliTransportTests(unittest.TestCase):
    def setUp(self):
        self.cli = transports.Cli("x.db", "tenant:local")

    def test_status_none_은_답이다(self):
        with mock.patch.object(subprocess, "run", return_value=_done(1, {"status": "none"})):
            self.assertEqual(self.cli.brief("q")["status"], "none")

    def test_none도_실패_표지가_있으면_거절한다(self):
        markers = [{"error": {"code": code}} for code in
                   ("usage", "not_authenticated", "upstream_unavailable", "quota_exceeded")]
        markers += [{"executed": False}, {"isError": True}]
        for code in (0, 1):
            for marker in markers:
                with self.subTest(code=code, marker=marker), mock.patch.object(
                        subprocess, "run", return_value=_done(code, {"status": "none", **marker})):
                    with self.assertRaises(transports.TransportError):
                        self.cli.brief("q")

    def test_none_예외는_brief에만_적용한다(self):
        with mock.patch.object(subprocess, "run", return_value=_done(1, {"status": "none"})):
            with self.assertRaises(transports.TransportError):
                self.cli.search("q", 5)

    def test_실패_stdout은_공백까지_비어야_한다(self):
        for stdout in ('{"status":"resolved"}', " ", "\n"):
            done = subprocess.CompletedProcess([], 1, stdout=stdout, stderr='{"status":"none"}')
            with self.subTest(stdout=stdout), mock.patch.object(subprocess, "run", return_value=done):
                with self.assertRaises(transports.TransportError):
                    self.cli.brief("q")

    def test_빈_JSON과_객체가_아닌_JSON은_거절한다(self):
        for code in (0, 1):
            for text in ("", "{}", "[]", "null", "{broken"):
                with self.subTest(code=code, text=text), mock.patch.object(
                        subprocess, "run", return_value=_done(code, text)):
                    with self.assertRaises(transports.TransportError):
                        self.cli.brief("q")

    def test_도구_실패는_던진다(self):
        with mock.patch.object(subprocess, "run",
                               return_value=_done(1, {"executed": False, "error": "x"})):
            with self.assertRaises(transports.TransportError):
                self.cli.search("q", 5)

    def test_사용법_오류는_던진다(self):
        with mock.patch.object(subprocess, "run", return_value=_done(2, "", "usage")):
            with self.assertRaises(transports.TransportError):
                self.cli.brief("q")

    def test_JSON_이_아닌_출력은_던진다(self):
        with mock.patch.object(subprocess, "run", return_value=_done(0, "not json")):
            with self.assertRaises(transports.TransportError):
                self.cli.brief("q")

    def test_기동_진단_뒤의_none_JSON도_답이다(self):
        text = 'vinc: boot diagnostics {}\n' + json.dumps({"status": "none"})
        with mock.patch.object(subprocess, "run", return_value=_done(1, text)):
            self.assertEqual(self.cli.brief("q")["status"], "none")

    def test_실패_JSON_뒤의_잡음은_거절한다(self):
        text = json.dumps({"status": "none"}) + '\nnot a JSON result'
        with mock.patch.object(subprocess, "run", return_value=_done(1, text)):
            with self.assertRaises(transports.TransportError):
                self.cli.brief("q")

    def test_성공은_stderr_진단과_무관하게_stdout을_읽는다(self):
        with mock.patch.object(subprocess, "run", return_value=_done(
                0, {"status": "resolved"}, json.dumps({"status": "none"}))):
            self.assertEqual(self.cli.brief("q")["status"], "resolved")

    def test_실패는_stdout의_정상처럼_보이는_답을_무시한다(self):
        done = subprocess.CompletedProcess([], 1, stdout=json.dumps({"status": "none"}),
                                           stderr=json.dumps({"executed": False, "error": "x"}))
        with mock.patch.object(subprocess, "run", return_value=done):
            with self.assertRaises(transports.TransportError):
                self.cli.brief("q")

    def test_실패_stderr가_JSON이_아니면_stdout으로_대체하지_않는다(self):
        done = subprocess.CompletedProcess([], 1, stdout=json.dumps({"status": "none"}),
                                           stderr="not json")
        with mock.patch.object(subprocess, "run", return_value=done):
            with self.assertRaisesRegex(transports.TransportError, "stderr"):
                self.cli.brief("q")

    def test_스토어_파일을_MEMORY_DATA_DIR_로_넘긴다(self):
        with mock.patch.object(subprocess, "run", return_value=_done(0, {"status": "choose"})) as run:
            self.cli.brief("q")
        self.assertEqual(run.call_args.kwargs["env"]["MEMORY_DATA_DIR"], "x.db")


class V1TransportTests(unittest.TestCase):
    def _v1(self, exc):
        v = transports.V1("http://127.0.0.1:1", "vinc_sk_x")
        v.rc = mock.Mock(RemoteFailure=type(exc), call=mock.Mock(side_effect=exc))
        return v

    def test_못_읽은_노드의_404_는_none_답이다(self):
        from api.remote_client import RemoteFailure
        exc = RemoteFailure({"error": {"code": "not_found", "message": "m"}}, 1)
        got = self._v1(exc).brief("concept:x")
        self.assertEqual((got["status"], got["unreadable"]), ("none", True))

    def test_다른_실패는_던진다(self):
        from api.remote_client import RemoteFailure
        exc = RemoteFailure({"error": {"code": "upstream_unavailable", "message": "m"}}, 5)
        with self.assertRaises(transports.TransportError):
            self._v1(exc).search("q", 5)


class ParityTests(unittest.TestCase):
    def test_채점_필드가_하나라도_다르면_잡는다(self):
        a = {"status": "choose", "node": "", "rank": 1, "union_rank": 2,
             "unreadable": False, "fell_through": False}
        runs = {"inproc": {"per_query": [a]},
                "cli": {"per_query": [{**a, "union_rank": 3}]}}
        diffs = run_nodes.parity(runs)
        self.assertEqual(diffs, [{"row": 0, "pair": "inproc/cli",
                                  "diff": {"union_rank": (2, 3)}}])

    def test_같으면_빈_목록이다(self):
        a = {k: None for k in run_nodes.PARITY_KEYS}
        self.assertEqual(run_nodes.parity({"a": {"per_query": [a]},
                                           "b": {"per_query": [dict(a)]}}), [])

    def test_의미_팔이_빠진_응답을_강등으로_센다(self):
        self.assertTrue(run_nodes._degraded({"arms": "fts"}))
        self.assertTrue(run_nodes._degraded({"arms": "fts+vector", "vector_note": "x"}))
        self.assertEqual(run_nodes._degraded({"arms": "fts+vector"}), "")

    def test_재채점을_켰는데_안_돈_응답을_강등으로_센다(self):
        ok = {"arms": "fts+vector", "node_rescore": {"state": "applied", "n": 20}}
        self.assertEqual(run_nodes._degraded(ok), "")
        self.assertIn("tokens missing", run_nodes._degraded(
            {"arms": "fts+vector", "node_rescore": {"state": "skipped", "reason": "tokens missing"}}))

    def test_재채점_전송은_질의_임베딩이_있을_때만_토큰을_싣는다(self):
        calls = []

        class Q:
            marker = 1

            def search(self, query, **kw):
                calls.append(("search", kw.get("query_colbert")))

            def brief(self, query, **kw):
                calls.append(("brief", kw.get("query_colbert")))

        q = transports._WithColbert(Q(), lambda text: "tok:" + text)
        q.search("a", query_embedding=[0.1])
        q.search("b", query_embedding=None)
        q.brief("c", query_embedding=[0.1])
        q.brief("d", query_embedding=[0.1], query_colbert="given")
        self.assertEqual(calls, [("search", "tok:a"), ("search", None), ("brief", "tok:c"), ("brief", "given")])
        self.assertEqual(q.marker, 1)

    def test_스탬프_없는_벡터_경고만으로는_강등이_아니다(self):
        from api.query import UNSTAMPED_RUNTIME_NOTE as u
        self.assertEqual(run_nodes._degraded({"arms": "fts+vector", "vector_note": u}), "")
        # 다른 사유가 같이 붙으면(검색이 이어 붙이는 모양 그대로) 그 사유로 강등이다.
        self.assertEqual(run_nodes._degraded({"arms": "fts+vector", "vector_note": f"x; {u}"}), "x")
        self.assertTrue(run_nodes._degraded({"arms": "fts", "vector_note": u}))

    def test_CLI_경로는_로컬_대상으로_못_박는다(self):
        from rag_eval.evalkit.transports import Cli
        self.assertEqual(Cli("d", "o").env["VINC_TARGET"], "local")
        self.assertEqual(Cli("d", "o", env={"VINC_TARGET": "remote"}).env["VINC_TARGET"], "remote")


def _row(status="choose", rank=0, union_rank=0, answerable=True, wrong=0.0, query="q"):
    return {"query": query, "answerable": answerable, "status": status, "rank": rank,
            "union_rank": union_rank, "wrong_node": wrong, "gold_kind": "concept", "axis": "en",
            "unanswerable_kind": "" if answerable else "no-topic",
            "false_resolved": 1.0 if (not answerable and status == "resolved") else 0.0,
            "hit": {k: 1.0 if 0 < rank <= k else 0.0 for k in (1, 3, 10)}}


class FunnelTests(unittest.TestCase):
    def test_처음_떨어진_게이트_하나를_적는다(self):
        self.assertEqual(funnel.first_fail(_row(rank=0, union_rank=0)), "union")
        # 노출은 brief 가 보여 준 목록에 있느냐다(순위 > 0). 합집합에만 있으면 노출에서 떨어진다.
        self.assertEqual(funnel.first_fail(_row(rank=0, union_rank=4)), "exposed")
        self.assertEqual(funnel.first_fail(_row(rank=4, union_rank=4)), "first")
        self.assertEqual(funnel.first_fail(_row(rank=2, union_rank=1)), "first")
        self.assertEqual(funnel.first_fail(_row(rank=1, union_rank=1)), "committed")
        self.assertEqual(funnel.first_fail(_row("resolved", rank=1, union_rank=1)), "pass")
        # 틀린 노드로 확정하면 정답이 가려진다 — 노출에서 떨어진다.
        self.assertEqual(funnel.first_fail(_row("resolved", rank=0, union_rank=2, wrong=1.0)),
                         "exposed")

    def test_JSON_에서_읽은_문자열_키도_같게_읽는다(self):
        r = _row(rank=1, union_rank=1)
        r["hit"] = {str(k): v for k, v in r["hit"].items()}
        self.assertEqual(funnel.first_fail(r), "committed")

    def test_nDCG_의_IDCG_는_정답_목록에서_구한다(self):
        self.assertAlmostEqual(funnel.ndcg_at(2), 1 / __import__("math").log2(3))
        self.assertEqual(funnel.ndcg_at(11), 0.0)
        # 정답이 둘인데 하나만 1위로 받았으면 1 이 아니다.
        self.assertLess(funnel.ndcg_at(1, n_relevant=2), 1.0)

    def test_McNemar_는_뒤집힘만_센다(self):
        self.assertEqual(funnel.mcnemar_p(0, 0), 1.0)
        self.assertEqual(funnel.mcnemar_p(0, 6), 0.0312)

    def test_같은_실행끼리는_차이가_0_이다(self):
        rows = [_row("resolved", 1, 1, query="a"), _row(rank=2, union_rank=2, query="b"),
                _row("resolved", answerable=False, query="c")]
        got = funnel.paired(rows, rows)
        self.assertEqual((got["exposed_r1"]["delta"], got["exposed_r1"]["mcnemar_p"]), (0.0, 1.0))
        self.assertEqual(got["false_resolved"]["n"], 1)

    def test_문항_목록이_다르면_짝짓지_않는다(self):
        with self.assertRaises(ValueError):
            funnel.paired([_row(query="a")], [_row(query="b")])

    def test_확정_점은_곡선_위에_있다(self):
        rows = [_row("resolved", 1, 1), _row("resolved", 0, 1, wrong=1.0),
                _row(rank=1, union_rank=1), _row("none")]
        rc = funnel.risk_coverage(rows)
        self.assertEqual(rc["operating_point"], {"coverage": 0.5, "risk": 0.5})
        self.assertEqual(rc["max_coverage"], 0.75)      # none 은 기권이라 덮지 않는다

    def test_채점이_합집합의_팔과_첫_노드를_싣는다(self):
        hits = [{"id": "concept:b", "arms": ["title"], "rrf_score": 0.03},
                {"id": "concept:a", "arms": ["title", "semantic"], "rrf_score": 0.02}]
        res = {"status": "choose", "candidates": [{"id": "concept:b"}, {"id": "concept:a"}]}
        got = grading.grade_one(res, {"query": "q", "gold_node_id": "concept:a"}, hits)
        self.assertEqual((got["union_arms"], got["pred"], got["pred_rrf"]),
                         (["semantic", "title"], "concept:b", 0.03))


class PromotionTests(unittest.TestCase):
    def _choose(self, lead, right):
        r = _row(rank=1 if right else 2, union_rank=1)
        r["margin"] = {"sem_lead": lead}
        return r

    def test_여백_순서대로_올리고_선을_넘지_않는_가장_큰_자름을_고른다(self):
        rows = [_row("resolved", 1, 1), _row("resolved", 0, 1, wrong=1.0),   # 오늘 위험 1/2
                self._choose(0.05, True), self._choose(0.03, True),
                self._choose(0.02, False), self._choose(None, True)]
        got = funnel.promotion(rows, "sem_lead", (0.025,))
        self.assertEqual(got["with_value"], 3)          # None 은 올리지 않는다
        # 셋째(오답)를 올려도 맞는 확정은 안 는다 — 같은 정답 수면 덜 올리는 쪽이다.
        self.assertEqual(got["operating_points"]["today"]["point"]["promoted"], 2)
        self.assertEqual(got["at_marks"]["0.025"]["promoted"], 2)
        self.assertEqual(got["auroc"], 1.0)

    def test_가르지_못하면_작동점이_없다(self):
        rows = [_row("resolved", 1, 1), _row("resolved", 0, 1, wrong=1.0),
                self._choose(0.01, False), self._choose(0.01, True)]
        got = funnel.promotion(rows, "sem_lead")
        self.assertIsNone(got["operating_points"]["lead_report_4_of_18"]["point"])
        self.assertEqual(got["auroc"], 0.5)             # 같은 값이면 반

    def test_여백은_보여_준_첫_노드의_의미_우위다(self):
        res = {"status": "choose", "candidates": [{"id": "a", "hit_tokens": 3, "title_tokens": 1},
                                                  {"id": "b", "hit_tokens": 1, "title_tokens": 1}]}
        sem = [{"id": "b", "distance": 0.10}, {"id": "a", "distance": 0.12}]
        got = grading.margins(res, "a", [{"id": "a", "arms": ["semantic", "title"]}], sem)
        self.assertEqual((got["tok_gap"], got["title_gap"]), (2, 0))
        self.assertEqual((got["sem_gap"], got["sem_lead"]), (0.02, -0.02))
        self.assertEqual(got["sem_dist_missing"], 1)


class LiveFingerprintTests(unittest.TestCase):
    def test_fingerprint_uses_verified_content_and_all_domain_counts(self):
        class _V1:
            def _call(self, tool, args):
                if tool == "vinc_doc_list":
                    return {"executed": True, "docs": [{"doc_id": "d1", "title": "A",
                                      "updated_at": "same", "chunks": 2}], "has_more": False}
                if tool == "vinc_doc_get":
                    return {"executed": True, "verified": True, "content_hash": "content-v2"}
                if tool == "vinc_domains":
                    return {"executed": True, "rows": [{"domain": "x", "episodes": 1, "concepts": 2,
                                      "records": 3, "decisions": 4, "total": 10}]}
                raise AssertionError(tool)

        got = run_nodes._live_snapshot(_V1())
        self.assertEqual(got["docs"], [("d1", "A", "same", "content-v2")])
        self.assertEqual(got["domains"], [("x", 1, 2, 3, 4, 10)])

    def test_changed_live_fingerprint_invalidates_the_run(self):
        with tempfile.TemporaryDirectory() as out, \
             mock.patch.dict("os.environ", {"VINC_API_KEY": "test-key"}), \
             mock.patch.object(run_nodes.runtime, "fingerprint", return_value={}), \
             mock.patch.object(run_nodes.grading, "load_gold", return_value=[]), \
             mock.patch.object(run_nodes, "V1"), \
             mock.patch.object(run_nodes, "_live_openapi_hash", return_value="schema-sha"), \
             mock.patch.object(run_nodes, "_live_snapshot",
                               side_effect=[{"docs": [("d1", "old")]},
                                            {"docs": [("d1", "new")]}]), \
             mock.patch.object(run_nodes, "gold_titles", return_value=({}, [])), \
             mock.patch.object(run_nodes, "run_one", return_value={}) as run, \
             mock.patch.object(run_nodes, "_finish", return_value=0) as finish:
            self.assertEqual(run_nodes.main(["--live", "https://example.test", "--out", out]), 1)
        run.assert_called_once()
        header = finish.call_args.args[1]
        self.assertFalse(header["live_stable"])
        self.assertNotEqual(header["live_fingerprint_before"], header["live_fingerprint_after"])


if __name__ == "__main__":
    unittest.main()
