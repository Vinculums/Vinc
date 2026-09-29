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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from evalkit import run_nodes, runtime, transports  # noqa: E402

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
    return subprocess.CompletedProcess([], code, stdout=text, stderr=err)


class CliTransportTests(unittest.TestCase):
    def setUp(self):
        self.cli = transports.Cli("x.db", "tenant:local")

    def test_status_none_은_답이다(self):
        with mock.patch.object(subprocess, "run", return_value=_done(1, {"status": "none"})):
            self.assertEqual(self.cli.brief("q")["status"], "none")

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
