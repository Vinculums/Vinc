import copy
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import verify_layout_remote as vr


GRAPH = {"executed": True, "marker": "r1", "nodes": [{"id": "a"}, {"id": "b"}],
         "edges": [{"source": "a", "target": "b", "type": "x"}] * 2}
ROOTS = {"executed": True, "state": "ready", "marker": "r1", "answer": {
    "nodes": [["a", 1, 2, 3, 4, 5], ["b", 6, 7, 8, 9, 10]],
    "edges": [["a\x00b\x00x\x000", None], ["a\x00b\x00x\x001", None]],
}}


class VerificationTests(unittest.TestCase):
    def test_ready_requires_node_and_numbered_relation_coverage(self):
        self.assertEqual(vr.coverage(ROOTS, GRAPH), {"state": "ready", "marker_matches": True, "nodes": 2, "edges": 2})
        for change in [lambda r: r["answer"]["nodes"].pop(), lambda r: r["answer"]["edges"].pop(),
                       lambda r: r["answer"]["nodes"][0].__setitem__(1, float("nan"))]:
            roots = copy.deepcopy(ROOTS)
            change(roots)
            with self.assertRaises(vr.VerificationError):
                vr.coverage(roots, GRAPH)

    def test_marker_mismatch_retries_instead_of_claiming_ready(self):
        roots = copy.deepcopy(ROOTS)
        roots["marker"] = "r0"
        calls = []
        answers = [roots, GRAPH, ROOTS, GRAPH]
        result = vr.verify("synthetic-read-key", get=lambda *a: (calls.append(a[0]) or answers.pop(0)), sleep=lambda _: None)
        self.assertEqual(result["nodes"], 2)
        self.assertEqual(len(calls), 4)

    def test_pending_and_stale_are_followed_to_ready(self):
        answers = [{"state": "pending"}, {"state": "stale"}, ROOTS, GRAPH]
        self.assertEqual(vr.verify("synthetic-read-key", get=lambda *a: answers.pop(0), sleep=lambda _: None)["state"], "ready")

    def test_off_and_unknown_stop_without_retry(self):
        for state in ["off", "unknown"]:
            calls = []
            with self.assertRaises(vr.VerificationError):
                vr.verify("synthetic-read-key", get=lambda *a: (calls.append(a) or {"state": state}))
            self.assertEqual(len(calls), 1)

    def test_failed_is_waited_out_like_pending_and_bounded(self):
        # 앱은 실패한 계산을 재시도 창이 지난 뒤 다시 한다. 첫 읽기의 failed 는 끝이 아니라 기다림이다.
        answers = [{"state": "failed"}, {"state": "failed"}, {"state": "pending"}, ROOTS, GRAPH]
        self.assertEqual(vr.verify("synthetic-read-key", get=lambda *a: answers.pop(0), sleep=lambda _: None)["state"], "ready")
        now = [0.0]
        with self.assertRaises(vr.VerificationError) as cm:
            vr.verify("synthetic", get=lambda *a: {"state": "failed"}, clock=lambda: now[0],
                      sleep=lambda seconds: now.__setitem__(0, now[0] + seconds), wait_s=25)
        self.assertIn("did not become ready", str(cm.exception))

    def test_wait_is_bounded(self):
        now = [0]
        def sleep(n):
            now[0] += n
        with self.assertRaises(vr.VerificationError):
            vr.verify("synthetic-read-key", get=lambda *a: {"state": "pending"},
                      clock=lambda: now[0], sleep=sleep, wait_s=25)
        self.assertEqual(now[0], 25)

    def test_no_credential_is_required_to_be_pasted_into_the_source(self):
        with self.assertRaises(vr.VerificationError):
            vr.verify("")

    def test_transport_recovery_requires_full_roots_and_graph_coverage(self):
        for error in ("account read returned HTTP 502", "account read could not complete"):
            for failed_call in (1, 2):
                now, calls = [0], [0]
                def get(path, *args):
                    calls[0] += 1
                    if calls[0] == failed_call:
                        raise vr.VerificationError(error)
                    return ROOTS if "roots" in path else GRAPH
                result = vr.verify("synthetic", get=get, clock=lambda: now[0],
                                   sleep=lambda seconds: now.__setitem__(0, now[0] + seconds))
                self.assertEqual(result, {"state": "ready", "marker_matches": True,
                                          "nodes": 2, "edges": 2, "transport_retries": 1})

    def test_persistent_transport_failure_is_bounded(self):
        now = [0]
        def get(*args):
            raise vr.VerificationError("account read could not complete")
        with self.assertRaisesRegex(vr.VerificationError, "deadline"):
            vr.verify("synthetic", get=get, clock=lambda: now[0],
                      sleep=lambda seconds: now.__setitem__(0, now[0] + seconds), wait_s=25)
        self.assertEqual(now[0], 25)

    def test_a_busy_store_is_waited_out_and_reported(self):
        """새 리비전이 막 떴을 때의 `store_busy` 는 기한 안에서 다시 묻는다(run 39906310900)."""
        for busy_call in (1, 2):
            now, calls = [0], [0]
            def get(path, *args):
                calls[0] += 1
                if calls[0] == busy_call:
                    raise vr.StoreBusy("account read was not executed (/graph/roots: store_busy)")
                return ROOTS if "roots" in path else GRAPH
            result = vr.verify("synthetic", get=get, clock=lambda: now[0],
                               sleep=lambda seconds: now.__setitem__(0, now[0] + seconds))
            self.assertEqual(result, {"state": "ready", "marker_matches": True,
                                      "nodes": 2, "edges": 2, "busy_retries": 1})

    def test_a_store_that_stays_busy_is_bounded(self):
        now = [0]
        def get(*args):
            raise vr.StoreBusy("account read was not executed (/graph/roots: store_busy)")
        with self.assertRaisesRegex(vr.VerificationError, "deadline"):
            vr.verify("synthetic", get=get, clock=lambda: now[0],
                      sleep=lambda seconds: now.__setitem__(0, now[0] + seconds), wait_s=25)
        self.assertEqual(now[0], 25)

    def test_auth_and_contract_errors_are_not_retried(self):
        for error in ("account read returned HTTP 401", "account read was not executed",
                      "account reply was not JSON"):
            def get(*args):
                raise vr.VerificationError(error)
            with self.assertRaisesRegex(vr.VerificationError, error):
                vr.verify("synthetic", get=get, sleep=lambda _: self.fail("unexpected retry"))


    def test_unexecuted_reply_names_the_read_and_the_hub_reason(self):
        replies = [{"executed": False, "error": "store_busy"}, {"executed": False}, ["not", "a", "dict"]]
        expected = ["/graph/roots: store_busy", "/graph/roots: no error field",
                    "/graph/roots: no error field"]
        for reply, text in zip(replies, expected):
            class Response:
                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

                def read(self, _limit, body=json.dumps(reply).encode()):
                    return body
            with mock.patch.object(vr, "urlopen", lambda *a, **k: Response()):
                with self.assertRaisesRegex(vr.VerificationError, "account read was not executed") as caught:
                    vr.read("/graph/roots?basis=recorded", "synthetic", 1)
            self.assertIn(text, str(caught.exception))
            # 다시 물어도 되는 것은 store_busy 하나다. 사유가 없거나 다른 거절은 그대로 멈춘다.
            self.assertEqual(isinstance(caught.exception, vr.StoreBusy), text.endswith("store_busy"))


if __name__ == "__main__":
    unittest.main()
