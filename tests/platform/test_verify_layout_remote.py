import copy
import sys
import unittest
from pathlib import Path

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

    def test_off_and_failed_stop_without_retry(self):
        for state in ["off", "failed", "unknown"]:
            calls = []
            with self.assertRaises(vr.VerificationError):
                vr.verify("synthetic-read-key", get=lambda *a: (calls.append(a) or {"state": state}))
            self.assertEqual(len(calls), 1)

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

    def test_auth_and_contract_errors_are_not_retried(self):
        for error in ("account read returned HTTP 401", "account read was not executed",
                      "account reply was not JSON"):
            def get(*args):
                raise vr.VerificationError(error)
            with self.assertRaisesRegex(vr.VerificationError, error):
                vr.verify("synthetic", get=get, sleep=lambda _: self.fail("unexpected retry"))


if __name__ == "__main__":
    unittest.main()
