# -*- coding: utf-8 -*-
"""시간 가지 배치를 배치 컨테이너에 맡기고 들고 있는 것(`api.layout_remote`).

틀리면 조용하다: 표식을 안 보면 쓰기 뒤에도 옛 배치가 나가고, 관심을 안 보면 아무도 안
보는 공간을 위해 컨테이너를 깨우며, 페이로드에 제목·props 가 섞이면 밖으로 새어 나간다.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import changes, layout_remote, mirror_provenance  # noqa: E402
from api.auth import SessionAuth  # noqa: E402
from api.server import VincApp  # noqa: E402

GRAPH = {"executed": True,
         "nodes": [{"id": "concept:a", "label": "Secret title", "kind": "concept", "domain": "vinc/x",
                    "ts": "2026-10-01", "content_ts": "2026-09", "props": {"secret": 1}},
                   {"id": "concept:b", "label": "B", "kind": "concept", "domain": "vinc/x"}],
         "edges": [{"source": "concept:a", "target": "concept:b", "type": "derives_from", "evidence": "quote"}]}


def _answer(payload):
    return {"answer": {"nodes": [[n["id"], 1, 2, 3, 4, 5] for n in payload["nodes"]], "topics": [],
                       "edges": [], "topicEdges": []}, "stats": {"ms": 1}}


def _body(result):
    code, body = result
    return code, json.loads(bytes(body).decode("utf-8")) if isinstance(body, bytes) else body


class RootsUnitTests(unittest.TestCase):
    def setUp(self):
        self.marker = changes.ChangeMarker()
        self.posted = []
        self.version = "v1"        # 배치 컨테이너가 답과 /health 에 싣는 코드 판

        def post(payload):
            self.posted.append(payload)
            return {**_answer(payload), "version": self.version}
        self.post = post
        self.roots = self.make()

    def make(self, **kwargs):
        roots = layout_remote.RootsLayouts(remote="http://layout", token="t", read_graph=lambda o: GRAPH,
                                           marker=self.marker.read, post=self.post, debounce=0,
                                           health=lambda: {"ok": True, "version": self.version}, **kwargs)
        self.addCleanup(roots.close)
        return roots

    def wait_runs(self, n):
        deadline = time.monotonic() + 5
        while self.roots.runs < n and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertGreaterEqual(self.roots.runs, n)

    def test_the_first_ask_is_pending_and_the_answer_is_tied_to_the_marker(self):
        code, body = _body(self.roots.get("user:a", "recorded"))
        self.assertEqual((code, body["state"]), (200, "pending"))
        self.wait_runs(1)
        code, body = _body(self.roots.get("user:a", "recorded"))
        self.assertEqual(body["state"], "ready")
        self.assertEqual(body["marker"], self.marker.read("user:a"))
        self.assertEqual(body["answer"]["nodes"][0][0], "concept:a")
        # 같은 표식이면 다시 맡기지 않는다
        self.assertEqual(self.roots.run_once("user:a", "recorded"), "same")
        self.assertEqual(len(self.posted), 1)

    def test_only_ids_kinds_topics_dates_and_relation_ends_leave_the_hub(self):
        self.roots.run_once("user:a", "content")
        sent = self.posted[0]
        self.assertNotIn("user:a", json.dumps(sent))
        self.assertEqual(sent["basis"], "content")
        self.assertEqual(sent["nodes"][0], {"id": "concept:a", "kind": "concept", "domain": "vinc/x",
                                            "ts": "2026-10-01", "content_ts": "2026-09"})
        self.assertEqual(sent["edges"], [{"source": "concept:a", "target": "concept:b", "type": "derives_from"}])
        self.assertNotIn("Secret", json.dumps(sent))

    def test_a_write_redoes_a_watched_space_and_leaves_others_alone(self):
        self.roots.run_once("user:a", "recorded")
        self.roots.get("user:a", "recorded")             # 화면이 보고 있다
        self.marker.bump("user:a")
        self.roots.note_write("user:a")
        self.wait_runs(2)
        self.assertEqual(_body(self.roots.get("user:a", "recorded"))[1]["state"], "ready")
        self.marker.bump("user:b")
        self.roots.note_write("user:b")                  # 아무도 안 보는 공간
        time.sleep(0.1)
        self.assertEqual(len(self.posted), 2)

    def test_a_write_after_the_answer_makes_it_stale_until_redone(self):
        self.roots.run_once("user:a", "recorded")
        self.marker.bump("user:a")
        with mock.patch.object(self.roots, "_schedule"):
            code, body = _body(self.roots.get("user:a", "recorded"))
        self.assertEqual(body["state"], "stale")
        self.assertNotEqual(body["marker"], self.marker.read("user:a"))

    def test_a_failure_is_reported_and_not_retried_for_the_same_marker(self):
        self.roots._post = mock.Mock(side_effect=OSError("down"))
        self.assertEqual(self.roots.run_once("user:a", "recorded"), "failed")
        with mock.patch.object(self.roots, "_schedule") as schedule:
            self.assertEqual(_body(self.roots.get("user:a", "recorded"))[1]["state"], "failed")
            schedule.assert_not_called()
            self.marker.bump("user:a")
            self.assertEqual(_body(self.roots.get("user:a", "recorded"))[1]["state"], "pending")
            schedule.assert_called_once()

    def test_bad_basis_and_off(self):
        self.assertEqual(self.roots.get("user:a", "other")[0], 400)
        off = layout_remote.RootsLayouts(remote="", token="", read_graph=lambda o: GRAPH, marker=self.marker.read)
        self.assertEqual(off.get("user:a", "recorded"), (200, {"executed": True, "state": "off"}))
        off.note_write("user:a")
        self.assertIsNone(off._thread)

    def test_held_answers_are_bounded(self):
        self.roots.max_bytes = 250
        for owner in ("user:a", "user:b", "user:c", "user:d"):
            self.roots.run_once(owner, "recorded")
        self.assertLessEqual(self.roots._bytes, 250)
        self.assertIn(("user:d", "recorded"), self.roots._items)
        self.assertNotIn(("user:a", "recorded"), self.roots._items)

    def test_a_token_is_required_to_switch_it_on(self):
        with mock.patch.dict("os.environ", {"VINC_LAYOUT_REMOTE": "http://layout", "VINC_LAYOUT_TOKEN": ""}):
            self.assertFalse(layout_remote.from_env(read_graph=lambda o: None, marker=lambda o: "m").enabled)
        with mock.patch.dict("os.environ", {"VINC_LAYOUT_REMOTE": "http://layout", "VINC_LAYOUT_TOKEN": "x"}):
            self.assertTrue(layout_remote.from_env(read_graph=lambda o: None, marker=lambda o: "m").enabled)

    # ── VIN-744: 배치 코드 판·따라 묻기·재시작 ────────────────────────────
    def test_a_layout_deploy_makes_the_held_answer_stale_without_any_write(self):
        self.roots.run_once("user:a", "recorded")
        body = _body(self.roots.get("user:a", "recorded"))[1]
        self.assertEqual((body["state"], body["version"]), ("ready", "v1"))
        self.version = "v2"                       # layout-deploy: 쓰기는 없다
        self.roots._probed = float("-inf")         # 다음 물음에서 판을 다시 본다
        with mock.patch.object(self.roots, "_schedule") as schedule:
            body = _body(self.roots.get("user:a", "recorded"))[1]
        self.assertEqual(body["state"], "stale")
        self.assertIn("answer", body)              # 낡은 답도 싣는다: 화면은 먼저 그리고 갈아 끼운다
        schedule.assert_called_once()
        self.assertEqual(self.roots.run_once("user:a", "recorded"), "ready")
        body = _body(self.roots.get("user:a", "recorded"))[1]
        self.assertEqual((body["state"], body["version"]), ("ready", "v2"))

    def test_the_version_is_probed_at_most_once_per_interval_and_a_dead_probe_keeps_serving(self):
        calls = []
        roots = self.make(probe_s=60.0)
        roots._health = lambda: calls.append(1) or {"version": "v1"}
        roots.run_once("user:a", "recorded")
        for _ in range(3):
            roots.get("user:a", "recorded")
        self.assertEqual(len(calls), 1)
        roots._probed = float("-inf")
        roots._health = mock.Mock(side_effect=OSError("down"))
        self.assertEqual(_body(roots.get("user:a", "recorded"))[1]["state"], "ready")

    def test_have_returns_the_state_without_the_body_when_nothing_changed(self):
        self.roots.run_once("user:a", "recorded")
        full = _body(self.roots.get("user:a", "recorded"))[1]
        code, short = self.roots.get("user:a", "recorded", have=full["digest"])
        self.assertEqual((code, short["state"], short["digest"]), (200, "ready", full["digest"]))
        self.assertNotIn("answer", short)
        self.assertIn("answer", _body(self.roots.get("user:a", "recorded", have="other"))[1])

    def test_a_restart_serves_the_saved_answer_as_stale_and_redoes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = self.make(store_dir=tmp)
            first.run_once("user:a", "recorded")
            digest = _body(first.get("user:a", "recorded"))[1]["digest"]
            files = os.listdir(tmp)
            self.assertEqual(len(files), 1)
            self.assertNotIn("user", files[0])     # 파일 이름에 소유자가 드러나지 않는다
            first.close()
            # 재시작: 표식 세대가 새로 뽑힌다(`api.changes`)
            self.marker = changes.ChangeMarker()
            second = self.make(store_dir=tmp)
            second._marker = self.marker.read
            with mock.patch.object(second, "_schedule") as schedule:
                body = _body(second.get("user:a", "recorded"))[1]
            self.assertEqual((body["state"], body["digest"]), ("stale", digest))
            self.assertEqual(body["answer"]["nodes"][0][0], "concept:a")
            schedule.assert_called_once()
            self.assertEqual(second.run_once("user:a", "recorded"), "ready")
            self.assertEqual(_body(second.get("user:a", "recorded"))[1]["state"], "ready")

    def test_saves_are_throttled_and_the_rest_is_written_at_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = [1000.0]
            roots = self.make(store_dir=tmp, save_every=300.0, clock=lambda: now[0])
            roots.run_once("user:a", "recorded")
            path = os.path.join(tmp, os.listdir(tmp)[0])
            self.assertIn('"v1"', open(path, encoding="utf-8").readline())
            self.version = "v2"
            now[0] += 100                          # 판 확인 주기(60초)는 지났고 저장 주기는 아직이다
            roots._check_version()
            roots.run_once("user:a", "recorded")   # 판이 바뀌어 새 답. 주기 안이라 아직 안 쓴다
            self.assertIn('"v1"', open(path, encoding="utf-8").readline())
            roots.close()
            self.assertIn('"v2"', open(path, encoding="utf-8").readline())
            self.assertEqual(len(os.listdir(tmp)), 1)   # 임시 파일이 남지 않는다

    def test_a_broken_saved_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots = self.make(store_dir=tmp)
            with open(roots._file(("user:a", "recorded")), "w", encoding="utf-8") as fh:
                fh.write('{"v":1,"basis":"recorded"}\n{"nodes": [')
            with mock.patch.object(roots, "_schedule"):
                self.assertEqual(_body(roots.get("user:a", "recorded"))[1]["state"], "pending")

    def test_the_saved_answers_live_beside_the_persistent_snapshot(self):
        with mock.patch.dict("os.environ", {"VINC_STORE_SNAPSHOT": "/data/db", "VINC_LAYOUT_STORE": ""}):
            self.assertEqual(layout_remote.store_dir_from_env(), "/data/db.layouts")
        with mock.patch.dict("os.environ", {"VINC_STORE_SNAPSHOT": "", "VINC_LAYOUT_STORE": ""}):
            self.assertIsNone(layout_remote.store_dir_from_env())
        with mock.patch.dict("os.environ", {"VINC_STORE_SNAPSHOT": "/data/db", "VINC_LAYOUT_STORE": "/x"}):
            self.assertEqual(layout_remote.store_dir_from_env(), "/x")


class _Store:
    driver_name = "fake"

    def reader_view(self):
        return self

    def run(self, cypher, params=None):
        return []

    def close(self):
        pass


class ServerTests(unittest.TestCase):
    def test_the_sidecar_lays_out_the_same_population_the_view_reads(self):
        seen = []

        def fake(**kwargs):
            seen.append(kwargs)
            return GRAPH
        with mock.patch.object(mirror_provenance, "graph_with_mirror_review", fake), \
                mock.patch.dict("os.environ", {"VINC_LAYOUT_REMOTE": "http://layout", "VINC_LAYOUT_TOKEN": "x"}):
            app = VincApp(store=_Store(), auth=SessionAuth("t"), quiet_boot=True)
            self.addCleanup(app.close)
            posted = threading.Event()
            app.roots._post = lambda payload: (posted.set(), _answer(payload))[1]
            app.roots.debounce = 0
            app.roots._health = lambda: {}
            self.assertEqual(_body(app.read_graph_roots({"owner": "user:a"}))[1]["state"], "pending")
            self.assertTrue(posted.wait(5))
            deadline = time.monotonic() + 5
            while app.roots.runs < 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            code, body = _body(app.read_graph_roots({"owner": "user:a", "basis": "recorded"}))
        self.assertEqual(body["state"], "ready")
        self.assertEqual(seen[0]["owner"], "user:a")
        self.assertEqual(seen[0]["layout"], "hub")
        self.assertIsNone(seen[0]["domains"])
        self.assertEqual(seen[0]["cone"], 1.0)

    def test_the_route_is_open_to_the_web_proxy(self):
        from vinc_mcp import http_server as hs
        self.assertEqual(hs._PROXY_OPS.get("graph-roots"), ("GET", "/graph/roots"))
        self.assertIn("graph-roots", hs.team_role_tiers()["viewer"])


if __name__ == "__main__":
    unittest.main()
