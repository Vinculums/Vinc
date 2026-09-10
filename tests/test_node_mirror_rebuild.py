# -*- coding: utf-8 -*-
"""노드 미러 전량 재구축 — 배포 경계에서 한 번 도는 것.

증분 삽입은 기각됐다. "없던 벡터가 생기는" HNSW 삽입만 프로세스를 죽이므로(exit 139)
미러는 **전량 재구축 아니면 아무것도 아니다**. 그 비용을 질의 경로가 아니라 릴리스에
물리기로 했고, 그래서 컨테이너 기동이 방아쇠다.
근거: [[decision:the-node-mirror-is-rebuilt-whole-or-not-at-all]]

**이 파일이 지키는 것 넷**: 지우는 범위가 실린 소유자에 갇힌다(SEC-1) · 인코더가 없으면
조용히 넘어가지 않는다 · 반만 받은 벡터로 미러를 갈아 끼우지 않는다 · 두 벌이 겹치지
않는다.
"""
from __future__ import annotations

import os
import tempfile
import unittest

from api import node_mirror
from api import server as sv
from api.server import VincApp

try:
    import ladybug                          # noqa: F401
    _DRIVER = True
except Exception:                           # pragma: no cover
    _DRIVER = None

DIM = 4


def _vec(*xs):
    v = list(xs) + [0.0] * (DIM - len(xs))
    n = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / n for x in v]


class Text(unittest.TestCase):
    def test_the_title_comes_first(self):
        """제목이 이 노드의 이름이다. 부가 텍스트가 앞서면 라벨이 묻힌다."""
        self.assertEqual(node_mirror.mirror_text("Alpha", "a one liner"),
                         "Alpha. a one liner")

    def test_a_bare_title_keeps_no_trailing_dot(self):
        self.assertEqual(node_mirror.mirror_text("Alpha", ""), "Alpha")

    def test_the_extra_is_clipped(self):
        out = node_mirror.mirror_text("A", "x" * (node_mirror.EXTRA_CHARS + 50))
        self.assertEqual(len(out), node_mirror.EXTRA_CHARS + 3)   # "A. " + 잘린 부가


class _FakeStore:
    """미러 연산만 받아 적는 스토어. 드라이버 없이 핸들러 흐름을 그대로 태운다."""

    driver_name = "fake"
    scoped_chunk_search = True

    def __init__(self, nodes):
        self._nodes = nodes          # [{id, title, owner, extra, label}]
        self.mirror: list[dict] = []
        self.dropped = 0
        self.created = 0

    # 리더 뷰는 자기 자신이다 — 이 더블에는 쓰기/읽기 커넥션 구분이 없다.
    def reader_view(self):
        return self

    def run(self, query, params=None):
        params = params or {}
        if query.startswith("MATCH (c:"):
            label = query.split("MATCH (c:", 1)[1].split(")", 1)[0]
            owner = params.get("o")
            return [{"id": n["id"], "title": n["title"], "owner": n["owner"],
                     "extra": n.get("extra") or ""}
                    for n in self._nodes
                    if n["label"] == label and (owner is None or n["owner"] == owner)]
        if query.startswith("MATCH (n:NodeVec)"):
            keep = set(params.get("o") or [])
            self.mirror = [m for m in self.mirror if m["owner_id"] not in keep]
            return []
        if query.startswith("UNWIND $rows"):
            self.mirror += [{"node_id": r["id"], "owner_id": r["owner"],
                             "title": r["title"], "model": params["m"],
                             "embedding": r["emb"]} for r in params["rows"]]
            return []
        return []

    def drop_node_vector_index(self):
        self.dropped += 1

    def create_node_vector_index(self):
        self.created += 1

    def close(self):
        pass


def _nodes():
    return [{"label": "Concept", "id": "concept:a", "title": "Alpha",
             "owner": "user:mine", "extra": "one liner"},
            {"label": "Record", "id": "record:b", "title": "Bravo",
             "owner": "user:mine"},
            {"label": "Concept", "id": "concept:c", "title": "Charlie",
             "owner": "user:theirs"}]


class Collect(unittest.TestCase):
    def test_an_owner_scope_is_actually_applied(self):
        """미러는 노드의 사본이다. 스코프가 새면 남의 제목이 후보로 올라온다(SEC-1)."""
        rows = node_mirror.collect(_FakeStore(_nodes()), "user:mine")
        self.assertEqual(sorted(r["id"] for r in rows), ["concept:a", "record:b"])

    def test_no_owner_means_every_owner(self):
        """배포 경계의 재구축은 테넌트를 안 가린다 — 한 벌의 인덱스이기 때문이다."""
        rows = node_mirror.collect(_FakeStore(_nodes()), None)
        self.assertEqual(len(rows), 3)

    def test_every_row_carries_its_owner(self):
        """지우고 넣는 쪽이 스코프를 다시 지어내지 않게 한다."""
        rows = node_mirror.collect(_FakeStore(_nodes()), None)
        self.assertEqual({r["owner"] for r in rows}, {"user:mine", "user:theirs"})

    def test_the_search_surface_is_the_title_plus_the_one_liner(self):
        rows = node_mirror.collect(_FakeStore(_nodes()), "user:mine")
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(by_id["concept:a"]["text"], "Alpha. one liner")
        self.assertEqual(by_id["concept:a"]["title"], "Alpha")
        self.assertEqual(by_id["record:b"]["text"], "Bravo")


class Replace(unittest.TestCase):
    def rows(self, *owners):
        return [{"id": f"n{i}", "kind": "concept", "owner": o, "title": f"t{i}",
                 "text": f"t{i}", "emb": _vec(1, 0)} for i, o in enumerate(owners)]

    def test_the_index_is_dropped_before_and_built_after(self):
        """🔴 그 순서가 안전장치다 — 인덱스가 선 채로 삽입하면 세그폴트가 난다."""
        s = _FakeStore([])
        node_mirror.replace(s, self.rows("user:mine"), "e5")
        self.assertEqual((s.dropped, s.created), (1, 1))

    def test_it_only_clears_the_owners_it_is_replacing(self):
        """한 소유자에게 전량 재구축을 돌려도 다른 소유자의 미러는 살아남는다."""
        s = _FakeStore([])
        s.mirror = [{"node_id": "old", "owner_id": "user:theirs", "title": "x",
                     "model": "e5", "embedding": _vec(1, 0)}]
        node_mirror.replace(s, self.rows("user:mine"), "e5")
        self.assertEqual({m["owner_id"] for m in s.mirror},
                         {"user:mine", "user:theirs"})

    def test_the_old_rows_of_that_owner_are_gone(self):
        s = _FakeStore([])
        s.mirror = [{"node_id": "stale", "owner_id": "user:mine", "title": "x",
                     "model": "e5", "embedding": _vec(1, 0)}]
        node_mirror.replace(s, self.rows("user:mine"), "e5")
        self.assertNotIn("stale", [m["node_id"] for m in s.mirror])

    def test_a_long_list_still_lands_whole(self):
        """한 문장에 다 싣지 않고 쪼개므로, 쪼개는 자리에서 행이 새지 않는지 본다."""
        s = _FakeStore([])
        rows = self.rows(*(["user:mine"] * (node_mirror.INSERT_BATCH * 2 + 7)))
        node_mirror.replace(s, rows, "e5")
        self.assertEqual(len(s.mirror), len(rows))


class RebuildRoute(unittest.TestCase):
    """`POST /node-mirror/rebuild` — 엔트리포인트가 배포마다 한 번 부르는 자리."""

    def setUp(self):
        self.store = _FakeStore(_nodes())
        self.app = VincApp(store=self.store)
        self._prev = os.environ.get("VINC_EMBED_ENCODE")
        os.environ["VINC_EMBED_ENCODE"] = "http://encoder.invalid"
        self._real_post = sv._encode_post
        self.calls: list[tuple] = []

        def fake_post(url, texts, kind, timeout):
            self.calls.append((url, tuple(texts), kind))
            return [_vec(1, 0)] * len(texts)
        sv._encode_post = fake_post

    def tearDown(self):
        sv._encode_post = self._real_post
        os.environ.pop("VINC_EMBED_ENCODE", None)
        if self._prev is not None:
            os.environ["VINC_EMBED_ENCODE"] = self._prev

    def test_it_fills_every_owner_and_builds_the_index(self):
        code, res = self.app.write_node_mirror_rebuild({})
        self.assertEqual(code, 200)
        self.assertEqual(res["rows"], 3)
        self.assertEqual(sorted(res["owners"]), ["user:mine", "user:theirs"])
        self.assertEqual((self.store.dropped, self.store.created), (1, 1))

    def test_an_owner_can_be_named(self):
        code, res = self.app.write_node_mirror_rebuild({"owner": "user:mine"})
        self.assertEqual((code, res["rows"]), (200, 2))

    def test_the_passages_are_encoded_as_passages(self):
        """질의 접두와 문단 접두가 다르다 — 섞으면 에러 없이 벡터만 틀린다."""
        self.app.write_node_mirror_rebuild({})
        self.assertTrue(self.calls)
        self.assertEqual({c[2] for c in self.calls}, {"passage"})

    def test_no_encoder_is_a_refusal_not_a_silent_skip(self):
        """데스크탑에는 인코더가 없다. 조용히 0건 성공으로 넘어가면 미러가 빈 채로
        남았다는 것을 아무도 모른다 — 이 리포가 반복해 겪은 무음 고장의 모양이다."""
        os.environ.pop("VINC_EMBED_ENCODE", None)
        code, res = self.app.write_node_mirror_rebuild({})
        self.assertEqual(code, 503)
        self.assertFalse(res["executed"])

    def test_a_short_encoder_reply_aborts_before_touching_the_mirror(self):
        """반만 받은 벡터로 갈아 끼우면 나머지 노드가 조용히 사라진다."""
        sv._encode_post = lambda url, texts, kind, timeout: [_vec(1, 0)]
        code, res = self.app.write_node_mirror_rebuild({})
        self.assertEqual(code, 502)
        self.assertEqual(self.store.mirror, [])
        self.assertEqual(self.store.created, 0, "인덱스를 다시 만들면 반쪽이 굳는다")

    def test_an_empty_graph_is_not_an_error(self):
        self.app = VincApp(store=_FakeStore([]))
        code, res = self.app.write_node_mirror_rebuild({})
        self.assertEqual((code, res["rows"]), (200, 0))

    def test_two_rebuilds_do_not_overlap(self):
        """하나가 인덱스를 드롭한 사이 다른 하나가 만들면 반쪽 미러가 남는다."""
        self.app._mirror_lock.acquire()
        try:
            code, res = self.app.write_node_mirror_rebuild({})
        finally:
            self.app._mirror_lock.release()
        self.assertEqual(code, 409)

    def test_the_lock_is_released_when_it_fails(self):
        """실패가 다음 배포의 재구축까지 막으면 미러가 영원히 안 낫는다."""
        sv._encode_post = lambda url, texts, kind, timeout: []
        self.app.write_node_mirror_rebuild({})
        self.assertTrue(self.app._mirror_lock.acquire(blocking=False))
        self.app._mirror_lock.release()

    def test_the_route_is_registered(self):
        """엔트리포인트가 이 경로를 친다. 표에서 빠지면 배포는 멀쩡히 초록인데 미러만
        영원히 비고, 그 사실은 아무 데도 안 나온다."""
        h = sv._Handler.__new__(sv._Handler)
        h.server = type("S", (), {"app": self.app})()
        self.assertEqual(h._route_post("/node-mirror/rebuild", {})[0], 200)
        self.assertEqual(h._route_post("/node-mirror/nope", {})[0], 404)


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class RealStore(unittest.TestCase):
    """진짜 엔진 위에서: 드롭 → 삽입 → 생성이 실제로 찾을 수 있는 미러를 남기는가."""

    OWNER = "user:mirror-e2e"

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_DATA_DIR": os.path.join(self._tmp, "store")}.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        from api import fragment as frag
        from store.backend import open_store
        from store.config import load_config
        self.store = open_store(load_config())
        self.store.provision(dim=DIM)
        frag.merge_fragment({"owner": self.OWNER, "nodes": [
            {"id": "concept:alpha", "type": "concept", "title": "Alpha",
             "one_liner": "the first letter"},
            {"id": "record:bravo", "type": "record", "title": "Bravo"},
        ]}, store=self.store)

    def tearDown(self):
        self.store.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_a_rebuild_leaves_a_mirror_you_can_search(self):
        rows = node_mirror.collect(self.store, self.OWNER)
        self.assertEqual(sorted(r["id"] for r in rows),
                         ["concept:alpha", "record:bravo"])
        for i, r in enumerate(rows):
            r["emb"] = _vec(1, 0) if r["id"] == "concept:alpha" else _vec(0, 1)
        node_mirror.replace(self.store, rows, "test")
        hits = self.store.node_vector_search(_vec(1, 0), 5, owner=self.OWNER)
        self.assertEqual(hits[0]["id"], "concept:alpha")
        self.assertEqual(hits[0]["title"], "Alpha")

    def test_running_it_twice_does_not_double_the_rows(self):
        """전량 재구축은 배포마다 돈다 — 멱등이 아니면 미러가 배포 수만큼 부푼다."""
        rows = node_mirror.collect(self.store, self.OWNER)
        for r in rows:
            r["emb"] = _vec(1, 0)
        node_mirror.replace(self.store, rows, "test")
        node_mirror.replace(self.store, rows, "test")
        n = (self.store.run("MATCH (n:NodeVec) WHERE n.owner_id = $o "
                            "RETURN count(n) AS n", {"o": self.OWNER}) or [{}])[0]["n"]
        self.assertEqual(n, len(rows))


if __name__ == "__main__":
    unittest.main()
