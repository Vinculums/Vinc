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
from store.embedded_schema import SCHEMA_VERSION

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

    def __init__(self, nodes, index_works=True):
        self._nodes = nodes          # [{id, title, owner, extra, label}]
        self.mirror: list[dict] = []
        self.dropped = 0
        self.created = 0
        self.reader_open = 0         # 지금 열려 있는 리더 뷰 수
        self._index_works = index_works

    def reader_view(self):
        """읽기 전용 뷰. **닫는 것까지 센다** — 인코딩 몇 분 동안 열어 두면 그 커넥션이
        뒤이은 인덱스 DDL(=쓰기)을 막는다."""
        self.reader_open += 1
        return _FakeReader(self)

    def node_vector_search(self, embedding, top_k, *, owner=None, use_index=False):
        if not self._index_works:
            return []            # 인덱스가 없을 때 엔진이 하는 그대로(예외 아님)
        return [{"id": m["node_id"]} for m in self.mirror
                if owner is None or m["owner_id"] == owner][:top_k]

    def run(self, query, params=None):
        params = params or {}
        if query.startswith("MATCH (c:"):
            label = query.split("MATCH (c:", 1)[1].split(")", 1)[0]
            owner = params.get("o")
            return [{"id": n["id"], "title": n["title"], "owner": n["owner"],
                     "extra": n.get("extra") or ""}
                    for n in self._nodes
                    if n["label"] == label and (owner is None or n["owner"] == owner)]
        if query.startswith("MATCH (n:NodeVec)") and "RETURN n.embedding" in query:
            return [{"e": m["embedding"]} for m in self.mirror][:1]
        if query.startswith("MATCH (n:NodeVec)") and "RETURN n.vec_id" in query:
            o = params.get("o")
            return [{"v": m["vec_id"], "t": m.get("text", ""), "m": m["model"],
                     "r": m.get("runtime", "")}
                    for m in self.mirror if o is None or m["owner_id"] == o]
        if query.startswith("MATCH (n:NodeVec)") and "n.vec_id IN $v" in query:
            gone = set(params["v"])
            self.mirror = [m for m in self.mirror if m["vec_id"] not in gone]
            return []
        if query.startswith("MATCH (n:NodeVec)"):
            keep = set(params.get("o") or [])
            self.mirror = [m for m in self.mirror if m["owner_id"] not in keep]
            return []
        if query.startswith("UNWIND $rows"):
            for r in params["rows"]:
                # 진짜 엔진이 하는 그대로: 같은 기본키가 두 번 오면 죽는다.
                if any(m["vec_id"] == r["vid"] for m in self.mirror):
                    raise RuntimeError(
                        f"Found duplicated primary key value {r['vid']}")
                self.mirror.append({"vec_id": r["vid"], "node_id": r["id"],
                                    "owner_id": r["owner"], "title": r["title"],
                                    "text": r["text"], "runtime": params["runtime"],
                                    "model": params["m"], "embedding": r["emb"]})
            return []
        return []

    def drop_node_vector_index(self):
        self.dropped += 1

    def create_node_vector_index(self) -> str:
        self.created += 1
        return "" if self._index_works else "RuntimeError: cannot start a write txn"

    def close(self):
        pass


class _FakeReader:
    """`_ReaderView` 대역 — 읽기만 넘기고, 닫혔는지 기억한다."""

    def __init__(self, backend):
        self._b = backend
        self.closed = False

    def run(self, query, params=None):
        return self._b.run(query, params)

    def node_vector_search(self, embedding, top_k, *, owner=None, use_index=False):
        return self._b.node_vector_search(embedding, top_k, owner=owner, use_index=use_index)

    def close(self):
        self.closed = True
        self._b.reader_open -= 1


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
        rows, _ = node_mirror.collect(_FakeStore(_nodes()), "user:mine")
        self.assertEqual(sorted(r["id"] for r in rows), ["concept:a", "record:b"])

    def test_no_owner_means_every_owner(self):
        """배포 경계의 재구축은 테넌트를 안 가린다 — 한 벌의 인덱스이기 때문이다."""
        rows, _ = node_mirror.collect(_FakeStore(_nodes()), None)
        self.assertEqual(len(rows), 3)

    def test_every_row_carries_its_owner(self):
        """지우고 넣는 쪽이 스코프를 다시 지어내지 않게 한다."""
        rows, _ = node_mirror.collect(_FakeStore(_nodes()), None)
        self.assertEqual({r["owner"] for r in rows}, {"user:mine", "user:theirs"})

    def test_the_same_id_in_two_spaces_is_not_a_collision(self):
        """🔴 **이것이 v18 의 이유다.** 새 스페이스는 전부 같은 네 역할로 시작하므로
        `concept:agent-steward` 같은 id 가 소유자 수만큼 존재한다. 그것은 겹침이 아니라
        멀티테넌시다 — 둘 다 미러에 들어가야 각자의 스페이스에서 찾힌다.
        2026-09-10 에 이것을 그래프 결함으로 읽고 한쪽을 버렸다."""
        both = _nodes() + [{"label": "Concept", "id": "concept:a", "title": "Alpha",
                            "owner": "user:theirs", "extra": "one liner"}]
        rows, collisions = node_mirror.collect(_FakeStore(both), None)
        self.assertEqual(collisions, [])
        self.assertEqual(sorted(r["owner"] for r in rows if r["id"] == "concept:a"),
                         ["user:mine", "user:theirs"])

    def test_one_id_living_in_two_tables_inside_one_space_is_a_collision(self):
        """진짜 겹침은 **한 소유자 안**에서 난다. 각 표는 자기 pk 로 유일하지만 표
        사이에는 아무 제약이 없어, 한 논리 id 가 Concept 이면서 Decision 일 수 있다.
        그 쌍이 하나만 있어도 삽입이 통째로 죽으므로 걸러 내되 말한다."""
        dupe = _nodes() + [{"label": "Decision", "id": "concept:a", "title": "Alpha dup",
                            "owner": "user:mine"}]
        rows, collisions = node_mirror.collect(_FakeStore(dupe), None)
        self.assertEqual(len(rows), 3, "겹친 것 하나가 나머지를 데려가면 안 된다")
        self.assertEqual(collisions, [{"id": "concept:a", "owner": "user:mine",
                                       "kept": "concept", "dropped": "decision"}])

    def test_a_clean_graph_reports_no_collision(self):
        self.assertEqual(node_mirror.collect(_FakeStore(_nodes()), None)[1], [])

    def test_the_search_surface_is_the_title_plus_the_one_liner(self):
        rows, _ = node_mirror.collect(_FakeStore(_nodes()), "user:mine")
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
        s.mirror = [{"vec_id": "user:theirs#old", "node_id": "old",
                     "owner_id": "user:theirs", "title": "x",
                     "model": "e5", "embedding": _vec(1, 0)}]
        node_mirror.replace(s, self.rows("user:mine"), "e5")
        self.assertEqual({m["owner_id"] for m in s.mirror},
                         {"user:mine", "user:theirs"})

    def test_the_old_rows_of_that_owner_are_gone(self):
        s = _FakeStore([])
        s.mirror = [{"vec_id": "user:mine#stale", "node_id": "stale",
                     "owner_id": "user:mine", "title": "x",
                     "model": "e5", "embedding": _vec(1, 0)}]
        node_mirror.replace(s, self.rows("user:mine"), "e5")
        self.assertNotIn("stale", [m["node_id"] for m in s.mirror])

    def test_it_reports_when_the_index_did_not_come_back(self):
        """🔴 인덱스 생성은 예외를 **삼킨다**(멱등이라 그래야 한다). 삼킨 결과를 확인
        하지 않으면 행은 들어가고 인덱스만 없는 미러가 남는데, 그 상태에서 의미 팔은
        예외 없이 빈 목록만 낸다 — 밖에서 "안 켠 것"과 구분되지 않는다."""
        s = _FakeStore([], index_works=False)
        self.assertFalse(node_mirror.replace(s, self.rows("user:mine"), "e5")["index_ok"])

    def test_it_also_carries_the_reason_it_was_swallowed(self):
        """`index_ok` 는 **무엇이** 틀렸는지만 말한다. 사유가 없으면 "이미 있음"과
        "쓰기 트랜잭션을 못 잡았음"이 같은 침묵이 되고, 그 둘을 못 가르면 원인을
        배포 여러 번에 걸쳐 못 짚는다 — 2026-09-10 에 실제로 그랬다."""
        s = _FakeStore([], index_works=False)
        res = node_mirror.replace(s, self.rows("user:mine"), "e5")
        self.assertIn("write txn", res["index_error"])

    def test_a_healthy_rebuild_says_the_index_answers(self):
        s = _FakeStore([])
        self.assertTrue(node_mirror.replace(s, self.rows("user:mine"), "e5")["index_ok"])

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
            # 인코딩이 도는 동안 리더가 열려 있으면 안 된다 — 아래 시험이 이 값을 본다.
            self.calls.append((url, tuple(texts), kind, self.store.reader_open))
            sv._ENCODER_RESPONSE_RUNTIME.set("test-runtime")
            sv._ENCODER_RESPONSE_INSTANCE.set("test-instance")
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

    def test_local_encoder_restart_aborts_before_mirror_swap(self):
        saved_batch = sv._MIRROR_BATCH
        sv._MIRROR_BATCH = 1
        calls = 0

        def restarting_post(url, texts, kind, timeout):
            nonlocal calls
            calls += 1
            sv._ENCODER_RESPONSE_RUNTIME.set("same-runtime")
            sv._ENCODER_RESPONSE_INSTANCE.set("before" if calls == 1 else "after")
            return [_vec(1, 0)] * len(texts)

        sv._encode_post = restarting_post
        try:
            code, res = self.app.write_node_mirror_rebuild({})
        finally:
            sv._MIRROR_BATCH = saved_batch
        self.assertEqual(code, 502)
        self.assertIn("instance changed", res["error"])
        self.assertEqual(self.store.mirror, [])

    def test_an_owner_can_be_named(self):
        code, res = self.app.write_node_mirror_rebuild({"owner": "user:mine"})
        self.assertEqual((code, res["rows"]), (200, 2))

    def test_the_passages_are_encoded_as_passages(self):
        """질의 접두와 문단 접두가 다르다 — 섞으면 에러 없이 벡터만 틀린다."""
        self.app.write_node_mirror_rebuild({})
        self.assertTrue(self.calls)
        self.assertEqual({c[2] for c in self.calls}, {"passage"})

    def test_the_reader_is_closed_before_the_encoding_starts(self):
        """🔴 인코딩은 몇 분이다. 그동안 리더를 붙들면 그 커넥션이 읽기 트랜잭션을 쥐고,
        뒤이은 교체의 `CREATE_VECTOR_INDEX`(=DDL=쓰기)가 거절된다. 그 자리는 예외를
        삼키므로 **행은 들어가고 인덱스만 없는** 미러가 남는다 — 2026-09-10 라이브에서
        정확히 그랬고, 밖에서는 팔을 안 켠 것과 구분되지 않았다."""
        self.app.write_node_mirror_rebuild({})
        self.assertTrue(self.calls)
        self.assertEqual([c[3] for c in self.calls], [0] * len(self.calls),
                         "인코딩 중에 리더 뷰가 열려 있었다")
        self.assertEqual(self.store.reader_open, 0, "리더 뷰가 새어 나갔다")

    def test_the_result_names_the_colliding_ids(self):
        """걸러 낸 것을 조용히 버리면 그래프의 진짜 결함이 미러 뒤에 숨는다."""
        self.app = VincApp(store=_FakeStore(_nodes() + [
            {"label": "Decision", "id": "concept:a", "title": "dup",
             "owner": "user:mine"}]))
        code, res = self.app.write_node_mirror_rebuild({})
        self.assertEqual((code, res["rows"]), (200, 3))
        self.assertEqual(res["id_collision_count"], 1)
        self.assertEqual(res["id_collisions"][0]["id"], "concept:a")

    def test_a_multi_space_graph_rebuilds_whole(self):
        """스페이스마다 같은 역할 id 가 있는 그래프가 **끝까지** 들어가는가. 이것이
        라이브에서 재구축을 네 번 죽인 모양이고, 키를 바꾼 이유다."""
        self.app = VincApp(store=_FakeStore(_nodes() + [
            {"label": "Concept", "id": "concept:a", "title": "Alpha",
             "owner": "user:theirs", "extra": "one liner"}]))
        code, res = self.app.write_node_mirror_rebuild({})
        self.assertEqual((code, res["rows"]), (200, 4))
        self.assertEqual(res["id_collision_count"], 0)

    def test_the_result_says_whether_the_index_answers(self):
        """조용한 강등을 말하게 하는 자리 — 이 값이 배포 로그까지 그대로 간다."""
        code, res = self.app.write_node_mirror_rebuild({})
        self.assertEqual(code, 200)
        self.assertTrue(res["index_ok"])
        self.app = VincApp(store=_FakeStore(_nodes(), index_works=False))
        self.assertFalse(self.app.write_node_mirror_rebuild({})[1]["index_ok"])

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


class RebuildReuse(unittest.TestCase):
    """바뀐 노드만 인코딩한다 — 판을 증명할 런타임·빌드·모델 라벨이 다 있을 때만.

    2026-10-02 허브: 전량 인코딩이 25분을 넘기는 동안 질의 임베딩이 같은 인코더 뒤에 줄을
    섰고, 교체 단계에서 사이드카가 OOM 으로 죽었다. 배포마다 바뀌는 노드는 몇십 개다."""

    def setUp(self):
        self.nodes = _nodes()
        self.store = _FakeStore(self.nodes)
        self.app = VincApp(store=self.store)
        self._saved = (sv._encode_post, sv._ENCODER_RUNTIME, sv._ENCODER_BUILD,
                       sv.docstore.embed_model_state, os.environ.get("VINC_EMBED_ENCODE"))
        self._saved_model = os.environ.pop("VINC_EMBED_MODEL", None)
        os.environ["VINC_EMBED_ENCODE"] = "http://encoder.invalid"
        sv._ENCODER_RUNTIME, sv._ENCODER_BUILD = "rt", "b1"
        sv.docstore.embed_model_state = lambda _s: {"stored": "bge"}
        self.encoded: list[str] = []

        def fake_post(url, texts, kind, timeout):
            self.encoded.extend(texts)
            sv._ENCODER_RESPONSE_RUNTIME.set(sv._ENCODER_RUNTIME)
            sv._ENCODER_RESPONSE_INSTANCE.set("i")
            return [_vec(1, 0)] * len(texts)
        sv._encode_post = fake_post

    def tearDown(self):
        (sv._encode_post, sv._ENCODER_RUNTIME, sv._ENCODER_BUILD,
         sv.docstore.embed_model_state, prev) = self._saved
        os.environ.pop("VINC_EMBED_ENCODE", None)
        if prev is not None:
            os.environ["VINC_EMBED_ENCODE"] = prev
        os.environ.pop("VINC_EMBED_MODEL", None)
        if self._saved_model is not None:
            os.environ["VINC_EMBED_MODEL"] = self._saved_model

    def rebuild(self):
        self.encoded.clear()
        code, res = self.app.write_node_mirror_rebuild({})
        self.assertEqual(code, 200)
        return res

    def test_a_second_rebuild_with_nothing_changed_touches_nothing(self):
        self.assertEqual(self.rebuild()["encoded"], 3)
        res = self.rebuild()
        self.assertTrue(res["unchanged"])
        self.assertEqual(self.encoded, [])
        self.assertEqual((self.store.dropped, self.store.created), (1, 1),
                         "아무것도 안 바뀌었는데 색인을 떨궜다")

    def test_a_renamed_node_is_the_only_one_encoded(self):
        self.rebuild()
        self.nodes[1]["title"] = "Bravo two"
        res = self.rebuild()
        self.assertEqual((res["encoded"], res["removed"]), (1, 1))
        self.assertEqual(self.encoded, ["Bravo two"])
        self.assertEqual(sorted(m["text"] for m in self.store.mirror),
                         ["Alpha. one liner", "Bravo two", "Charlie"])
        self.assertEqual((self.store.dropped, self.store.created), (2, 2))

    def test_a_deleted_node_leaves_the_mirror(self):
        self.rebuild()
        del self.nodes[1]
        res = self.rebuild()
        self.assertEqual((res["encoded"], res["removed"]), (0, 1))
        self.assertEqual(sorted(m["node_id"] for m in self.store.mirror),
                         ["concept:a", "concept:c"])
        self.assertTrue(res["index_ok"])

    def test_a_new_runtime_encodes_everything_again(self):
        self.rebuild()
        sv._ENCODER_RUNTIME = "rt2"
        self.assertEqual(self.rebuild()["encoded"], 3)
        self.assertEqual({m["runtime"] for m in self.store.mirror}, {"rt2"})

    def test_the_encoders_model_labels_the_mirror_while_the_stored_key_is_still_the_previous_one(self):
        """모델을 바꾼 배포에서 재구축은 워커의 첫 폴(스토어 키 교체)보다 먼저 돌 수 있다.
        그때 스토어 키로 라벨을 달면 이전 모델의 벡터가 전부 "안 바뀜"으로 남는다."""
        self.rebuild()
        os.environ["VINC_EMBED_MODEL"] = "bge-ko"
        res = self.rebuild()
        self.assertEqual(res["encoded"], 3)
        self.assertEqual({m["model"] for m in self.store.mirror}, {"bge-ko"})
        self.assertTrue(self.rebuild()["unchanged"])

    def test_without_a_build_id_it_rebuilds_whole_as_before(self):
        self.rebuild()
        sv._ENCODER_BUILD = ""
        self.assertEqual(self.rebuild()["encoded"], 3)


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
        rows, _ = node_mirror.collect(self.store, self.OWNER)
        self.assertEqual(sorted(r["id"] for r in rows),
                         ["concept:alpha", "record:bravo"])
        for i, r in enumerate(rows):
            r["emb"] = _vec(1, 0) if r["id"] == "concept:alpha" else _vec(0, 1)
        node_mirror.replace(self.store, rows, "test")
        hits = self.store.node_vector_search(_vec(1, 0), 5, owner=self.OWNER)
        self.assertEqual(hits[0]["id"], "concept:alpha")
        self.assertEqual(hits[0]["title"], "Alpha")

    def test_an_incremental_swap_keeps_unchanged_rows_and_the_index(self):
        """`plan` → `replace(stale=…)` 이 진짜 엔진에서 맞게 도는가: 바뀐 행만 갈리고,
        안 바뀐 행은 벡터째 남고, 색인이 다시 선다. 벡터는 float32 배열로 들고 간다."""
        from array import array
        rows, _ = node_mirror.collect(self.store, self.OWNER)
        for r in rows:
            r["emb"] = array("f", _vec(1, 0) if r["id"] == "concept:alpha" else _vec(0, 1))
        node_mirror.replace(self.store, rows, "test", "rt")
        have = node_mirror.existing(self.store, self.OWNER)
        self.assertEqual(len(have), 2)
        self.assertEqual(node_mirror.plan(rows, have, "test", "rt"), ([], []))
        self.assertTrue(node_mirror.index_answers(self.store))

        bravo = next(r for r in rows if r["id"] == "record:bravo")
        bravo["text"] = "Bravo renamed"
        todo, stale = node_mirror.plan(rows, have, "test", "rt")
        self.assertEqual([r["id"] for r in todo], ["record:bravo"])
        res = node_mirror.replace(self.store, todo, "test", "rt", stale=stale)
        self.assertTrue(res["index_ok"])
        texts = sorted(t for t, _m, _r in node_mirror.existing(self.store, self.OWNER).values())
        self.assertEqual(texts, ["Alpha. the first letter", "Bravo renamed"])
        hits = self.store.node_vector_search(_vec(1, 0), 1, owner=self.OWNER)
        self.assertEqual(hits[0]["id"], "concept:alpha", "안 바뀐 행의 벡터가 사라졌다")

    def test_runtime_stamp_detects_legacy_and_mixed_vectors(self):
        from api import query as q
        rows, _ = node_mirror.collect(self.store, self.OWNER)
        for row in rows:
            row["emb"] = _vec(1, 0)
        node_mirror.replace(self.store, rows, "test", "ort=1.30.0;tok=0.23.2;np=2.5.3")
        self.assertEqual(q._encoder_runtime_warning(
            self.store, self.OWNER, "ort=1.30.0;tok=0.23.2;np=2.5.3"), "")
        other = {**rows[0], "id": "concept:other", "owner": "user:other"}
        node_mirror.replace(self.store, [other], "test", "ort=1.29.0;tok=0.23.2;np=2.5.3")
        self.assertEqual(q._encoder_runtime_warning(
            self.store, self.OWNER, "ort=1.30.0;tok=0.23.2;np=2.5.3"), "")
        self.assertIn("mismatch", q._encoder_runtime_warning(
            self.store, self.OWNER, "ort=1.29.0;tok=0.23.2;np=2.5.3"))
        self.store.run("MATCH (n:NodeVec) WHERE n.node_id = 'record:bravo' "
                       "SET n.encoder_runtime = 'ort=1.29.0;tok=0.23.2;np=2.5.3'")
        self.assertIn("mismatch", q._encoder_runtime_warning(
            self.store, self.OWNER, "ort=1.30.0;tok=0.23.2;np=2.5.3"))
        self.store.run("MATCH (n:NodeVec) WHERE n.node_id = 'record:bravo' "
                       "SET n.encoder_runtime = ''")
        self.assertIn("unknown encoder runtime", q._encoder_runtime_warning(
            self.store, self.OWNER, "ort=1.30.0;tok=0.23.2;np=2.5.3"))

    def test_two_spaces_can_hold_the_same_node_id(self):
        """🔴 v18 이 사는 자리. 진짜 엔진 위에서 같은 논리 id 를 두 소유자로 넣는다 —
        v17 의 키(`node_id`)에서는 여기서 `Found duplicated primary key value` 로
        죽었고, 그것이 라이브 재구축을 네 번 무너뜨린 모양 그대로다."""
        rows = [{"id": "concept:agent-steward", "kind": "concept", "owner": o,
                 "title": "Agent role: steward", "text": "Agent role: steward",
                 "emb": _vec(1, 0)} for o in (self.OWNER, "team:other")]
        res = node_mirror.replace(self.store, rows, "test")
        self.assertEqual(res["rows"], 2)
        n = (self.store.run("MATCH (n:NodeVec) RETURN count(n) AS n") or [{}])[0]["n"]
        self.assertEqual(n, 2)
        # 그리고 조회는 여전히 자기 스페이스 것만 본다(SEC-1).
        hits = self.store.node_vector_search(_vec(1, 0), 5, owner="team:other")
        self.assertEqual([h["id"] for h in hits], ["concept:agent-steward"])

    def test_an_old_store_is_rekeyed_on_boot(self):
        """v17 로 만들어진 스토어가 부팅 한 번으로 새 키를 갖는가. 허브가 다음 기동에
        실제로 밟는 경로이고, 기본키는 ALTER 로 못 고쳐서 **테이블을 버리는** 유일한
        마이그레이션이다."""
        self.store.run("MERGE (m:_SchemaMeta {key: 'schema_version'}) SET m.value = '17'")
        self.store.migrate(dim=DIM)          # 부팅이 하는 그대로
        rows = [{"id": "concept:same", "kind": "concept", "owner": o, "title": "t",
                 "text": "t", "emb": _vec(1, 0)} for o in ("user:a", "user:b")]
        node_mirror.replace(self.store, rows, "test")   # 옛 키였다면 여기서 죽는다
        v = (self.store.run("MATCH (m:_SchemaMeta) WHERE m.key = 'schema_version' "
                            "RETURN m.value AS v") or [{}])[0]["v"]
        # 지금 버전까지 간다(버전 리터럴 고정은 test_store·test_embedded_backend 몫이다).
        self.assertEqual(v, str(SCHEMA_VERSION))

    def test_a_failed_rekey_does_not_record_the_new_version(self):
        """🔴 드롭이 실패하면 그 예외는 삼켜지고 base DDL 의 "이미 있다"도 삼켜진다.
        거기서 버전만 올리면 **옛 모양 그대로인 테이블에 새 버전이 찍혀** 다시는 고칠
        기회가 없다 — 재구축은 계속 죽고 이유는 안 보인다. 확인이 실패하면 버전을
        올리지 않아야 다음 기동이 다시 시도한다."""
        self.store.run("MERGE (m:_SchemaMeta {key: 'schema_version'}) SET m.value = '17'")
        real = self.store._node_vec_rekeyed
        self.store._node_vec_rekeyed = lambda: False
        try:
            self.store.migrate(dim=DIM)          # 예외 없이 끝나야 한다
        finally:
            self.store._node_vec_rekeyed = real
        v = (self.store.run("MATCH (m:_SchemaMeta) WHERE m.key = 'schema_version' "
                            "RETURN m.value AS v") or [{}])[0]["v"]
        self.assertEqual(v, "17", "고치지 못했는데 고쳤다고 기록하면 안 된다")
        self.store.migrate(dim=DIM)              # 다음 기동
        v = (self.store.run("MATCH (m:_SchemaMeta) WHERE m.key = 'schema_version' "
                            "RETURN m.value AS v") or [{}])[0]["v"]
        self.assertEqual(v, str(SCHEMA_VERSION), "다음 기동이 다시 시도해서 끝내야 한다")

    def test_running_it_twice_does_not_double_the_rows(self):
        """전량 재구축은 배포마다 돈다 — 멱등이 아니면 미러가 배포 수만큼 부푼다."""
        rows, _ = node_mirror.collect(self.store, self.OWNER)
        for r in rows:
            r["emb"] = _vec(1, 0)
        node_mirror.replace(self.store, rows, "test")
        node_mirror.replace(self.store, rows, "test")
        n = (self.store.run("MATCH (n:NodeVec) WHERE n.owner_id = $o "
                            "RETURN count(n) AS n", {"o": self.OWNER}) or [{}])[0]["n"]
        self.assertEqual(n, len(rows))


if __name__ == "__main__":
    unittest.main()
