# -*- coding: utf-8 -*-
"""W3 — 읽기 표면이 `rev`/`updated_at` 을 낸다(투영만).

세 열(`updated_at`·`rev`·`sync_seq`)은 동기화 도입 때부터 모든 추적 라벨에 있었고
필드 단위 3-way 병합도 이미 그것으로 돈다. 없던 것은 **노출**이다: 어느 MCP 읽기
도구도 이 둘을 돌려주지 않아서, 클라이언트는 비교할 버전을 손에 쥘 방법 자체가
없었다. 그래서 "내가 읽은 그 판본이 맞나"를 물을 수 없었다.

여기서 **강제는 하지 않는다**(D4). `vinc_ingest` 에 `expected_rev` 를 붙이는 것은
첫 실측 쓰기 충돌이 방아쇠다. 계획 정본: [[vinc.plan.mcp-agent-surface §6]].

급소 둘, 둘 다 이 파일이 지킨다:

1. **UNION 팔의 모양.** `one_liner`(R2)는 Concept 에만 있어 다른 팔을 리터럴로
   맞춰야 했다. 이 둘은 추적 라벨 전부에 있어 그 맞춤이 필요 없지만, 그렇다고
   아무 라벨에나 되는 것은 아니다 — **Domain 에는 없다**. 부착 축(Record·Episode·
   Bridge)까지 죽지 않고 값을 내는지 본다.
2. **뒤늦게 도착한 값.** 축 플랜으로 먼저 도착한 노드가 버전 없이 굳으면 안 된다
   (`shape_graph` 의 ts·content_ts·one_liner 와 같은 규칙).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import fragment as frag  # noqa: E402
from api import query as q  # noqa: E402

_DRIVER = None
try:
    from store.embedded_backend import _import_driver
    _DRIVER, _ = _import_driver()
except Exception:
    pass


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class NodeVersionExposureE2E(unittest.TestCase):
    OWNER = "user:a"

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {
            "MEMORY_STORE_BACKEND": "embedded",
            "MEMORY_DATA_DIR": os.path.join(self._tmp, "store"),
        }.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        from store.config import load_config
        from store.backend import open_store
        self.store = open_store(load_config())
        self.store.provision(dim=4)

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _ingest(self, nodes, edges=None):
        return frag.merge_fragment(
            {"owner": self.OWNER, "nodes": nodes, "edges": edges or []},
            store=self.store)

    # ── node_context ────────────────────────────────────────────────────
    def test_node_context_가_버전을_돌려주고_쓰기마다_오른다(self):
        self._ingest([{"id": "concept:c1", "type": "concept", "title": "T",
                       "domain": "d", "one_liner": "한 줄."}])
        first = q.node_context(owner=self.OWNER, node="concept:c1", store=self.store)
        self.assertTrue(first["executed"], first)
        self.assertGreaterEqual(first["rev"], 1, first)
        self.assertTrue(first["updated_at"], first)

        self._ingest([{"id": "concept:c1", "type": "concept", "title": "T2",
                       "domain": "d", "one_liner": "고친 한 줄."}])
        second = q.node_context(owner=self.OWNER, node="concept:c1", store=self.store)
        # 이게 요점이다: 두 번 읽은 값이 다르다는 것을 클라이언트가 볼 수 있어야
        # 낙관적 동시성이 나중에 성립한다.
        self.assertGreater(second["rev"], first["rev"], (first, second))
        self.assertNotEqual(second["updated_at"], "")

    def test_없는_노드도_키는_있다(self):
        out = q.node_context(owner=self.OWNER, node="concept:nope", store=self.store)
        # 키 부재와 rev 0 은 다른 말이다. 못 읽었을 때도 모양은 균일하다.
        self.assertIn("rev", out)
        self.assertIn("updated_at", out)

    def test_에피소드_갈래도_버전을_싣는다(self):
        """Episode 는 node_context 의 별도 반환 갈래다 — 여기를 빠뜨리면 타임라인에서
        연 노드만 조용히 버전이 없다."""
        self._ingest([{"id": "episode:e1", "type": "episode", "title": "E",
                       "domain": "d", "date": "2026-08-29", "summary": "있었던 일."}])
        out = q.node_context(owner=self.OWNER, node="episode:e1", store=self.store)
        self.assertTrue(out["executed"] and out["node_kind"] == "episode", out)
        self.assertGreaterEqual(out["rev"], 1, out)
        self.assertTrue(out["updated_at"], out)

    # ── vinc_graph ──────────────────────────────────────────────────────
    def test_graph_노드가_버전을_싣는다(self):
        self._ingest([{"id": "concept:g1", "type": "concept", "title": "G",
                       "domain": "d"}])
        out = q.graph(owner=self.OWNER, seed="concept:g1", depth=1, store=self.store)
        self.assertTrue(out["executed"], out)
        node = next((n for n in out["nodes"] if n["id"] == "concept:g1"), None)
        self.assertIsNotNone(node, out.get("nodes"))
        self.assertGreaterEqual(node["rev"], 1, node)
        self.assertTrue(node["updated_at"], node)

    def test_축_플랜의_매개자도_버전을_싣는다(self):
        """Record·Decision 은 vinc_graph 의 노드로 나가는데 축 플랜에서만 도착한다.
        개념 팔만 투영하면 그쪽이 통째로 버전 없이 남는다."""
        self._ingest(
            [{"id": "concept:anchor", "type": "concept", "title": "A", "domain": "d"},
             {"id": "record:r1", "type": "record", "title": "R", "domain": "d"},
             {"id": "decision:d1", "type": "decision", "title": "D", "domain": "d"}],
            [{"from": "record:r1", "to": "concept:anchor", "type": "relates_to"},
             {"from": "decision:d1", "to": "concept:anchor", "type": "about"}])
        out = q.graph(owner=self.OWNER, seed="concept:anchor", depth=2, store=self.store)
        self.assertTrue(out["executed"], out)
        by_id = {n["id"]: n for n in out["nodes"]}
        for nid in ("record:r1", "decision:d1"):
            self.assertIn(nid, by_id, out.get("nodes"))
            self.assertGreaterEqual(by_id[nid]["rev"], 1, by_id[nid])
            self.assertTrue(by_id[nid]["updated_at"], by_id[nid])

    def test_shape_graph_는_빈_버전만_나중에_채운다(self):
        """축 플랜(투영 없음)으로 먼저 도착한 노드가 영구히 버전 0 으로 굳지 않게.
        ts·content_ts·one_liner 와 같은 규칙이고, 이미 채워진 값은 안 덮는다."""
        rows = [{"id": "concept:x", "label": "X", "domain": "d"},
                {"id": "concept:x", "label": "X", "domain": "d",
                 "rev": 7, "updated_at": "2026-08-29T00:00:00.000Z"},
                {"id": "concept:x", "label": "X", "domain": "d",
                 "rev": 9, "updated_at": "2026-08-30T00:00:00.000Z"}]
        node = q.shape_graph(rows)["nodes"][0]
        self.assertEqual(node["rev"], 7)
        self.assertEqual(node["updated_at"], "2026-08-29T00:00:00.000Z")

    # ── vinc_neighbors ──────────────────────────────────────────────────
    def test_이웃이_버전을_싣고_부착_축도_죽지_않는다(self):
        """부착 축은 별도 UNION 이다(Record·Episode·Bridge). 열 없는 라벨에 투영을
        옮겨 붙이면 Binder 가 죽고 이웃 조회 **전체**가 함께 죽는다 — 이 파일이
        pinned·content_time·one_liner 에서 이미 세 번 겪은 자리다."""
        self._ingest(
            [{"id": "concept:seed", "type": "concept", "title": "S", "domain": "d"},
             {"id": "concept:other", "type": "concept", "title": "O", "domain": "d"},
             {"id": "record:r2", "type": "record", "title": "R", "domain": "d"},
             {"id": "episode:e2", "type": "episode", "title": "E", "domain": "d",
              "date": "2026-08-29"}],
            [{"from": "concept:seed", "to": "concept:other", "type": "relates_to"},
             {"from": "record:r2", "to": "concept:seed", "type": "relates_to"},
             {"from": "episode:e2", "to": "concept:seed", "type": "about"}])
        out = q.neighbors(owner=self.OWNER, node="concept:seed", store=self.store)
        self.assertTrue(out["executed"] and out["neighbors"], out)
        got = {nb["id"]: nb for nb in out["neighbors"]}
        for nid in ("concept:other", "record:r2", "episode:e2"):
            self.assertIn(nid, got, out["neighbors"])
            self.assertGreaterEqual(got[nid]["rev"], 1, got[nid])
            self.assertTrue(got[nid]["updated_at"], got[nid])

    def test_부착_노드를_시드로_잡아도_버전이_온다(self):
        """역방향 UNION(`_attach_reverse_cypher`). 정방향만 고치면 Record 시드에서
        걸어 나온 개념만 버전이 없다."""
        self._ingest(
            [{"id": "concept:anchor2", "type": "concept", "title": "A", "domain": "d"},
             {"id": "record:r3", "type": "record", "title": "R", "domain": "d"}],
            [{"from": "record:r3", "to": "concept:anchor2", "type": "relates_to"}])
        out = q.neighbors(owner=self.OWNER, node="record:r3", store=self.store)
        self.assertTrue(out["executed"] and out["neighbors"], out)
        got = {nb["id"]: nb for nb in out["neighbors"]}
        self.assertIn("concept:anchor2", got, out["neighbors"])
        self.assertGreaterEqual(got["concept:anchor2"]["rev"], 1, got)


_NS_DOC = """같은 검사를 PK 네임스페이싱 **켠 채** 다시 돈다.

읽기 경로는 id 를 물리 키로 바인딩하고 돌아온 값을 벗긴다. 한 모드에서만 초록인
투영은 층을 헷갈린 것이고, 기본 스위트는 플래그가 꺼져 있어 그것을 못 잡는다."""


def _namespaced(base):
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


NodeVersionExposureUnderNamespacing = _namespaced(NodeVersionExposureE2E)


if __name__ == "__main__":
    unittest.main()
