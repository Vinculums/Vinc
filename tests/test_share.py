# -*- coding: utf-8 -*-
"""Share pack(D19) 계약 테스트 — 스코프·스크럽·버전·owner 재스탬핑.

대부분은 무 DB(FakeStore)다. 맨 아래 `PackTimeContract` 만 실엔진인데, 시간 계약은
스텁으로 안 보이기 때문이다 — "기존 노드를 안 덮는다"는 **이미 있는 행**이 있어야
검사되고, 문서 왕복은 재청킹까지 돌아야 드러난다.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store import pkns  # noqa: E402

from api import share  # noqa: E402
from store.embedded_backend import _import_driver  # noqa: E402

_DRIVER, _NAME = _import_driver()


class _FakeStore:
    def __init__(self, responses: dict | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.responses = responses or {}

    def run(self, cypher: str, params: dict | None = None):
        self.calls.append((cypher, dict(params or {})))
        # 가장 구체적인(긴) 키 우선 — 짧은 키의 우연 매칭 방지
        for key in sorted(self.responses, key=len, reverse=True):
            if key in cypher:
                return self.responses[key]
        return []

    def close(self):
        pass


_CONCEPTS = [
    {"id": "concept:a", "title": "A", "domain": "math", "tags": ["core"],
     "topic": "analysis", "one_liner": "one", "props": None},
    {"id": "concept:b", "title": "B", "domain": "math", "tags": ["misc"],
     "topic": None, "one_liner": None, "props": None},
]
_EPISODES = [
    {"id": "ep:1", "title": "E1", "domain": "math", "date": "2026-07-01",
     "summary": "s", "tags": ["core"]},
]
_EDGE_ROWS = [
    {"id": "concept:a", "label": "A", "domain": "math",
     "rel": "RELATED_TO", "target": "concept:b", "target_domain": "math"},
    # 스코프 밖으로 나가는 엣지 — 팩에서 잘려야 함
    {"id": "concept:b", "label": "B", "domain": "math",
     "rel": "RELATED_TO", "target": "concept:out", "target_domain": "art"},
]


class BuildPackTests(unittest.TestCase):
    def _store(self):
        return _FakeStore({
            "one_liner": _CONCEPTS,
            "e.summary AS summary": _EPISODES,
            "OPTIONAL MATCH": _EDGE_ROWS,
        })

    def test_domain_scope_pack_shape(self):
        pack = share.build_share_pack(owner="user:alice", domain="math",
                                      generated_at="2026-07-10T00:00:00Z", store=self._store())
        self.assertTrue(pack["executed"])
        self.assertEqual(pack["vinc_pack"], 1)
        self.assertEqual(pack["manifest"]["counts"],
                         {"concepts": 2, "records": 1, "edges": 1, "documents": 0})   # 경계 밖 엣지 절단; 원문 없음
        types = {n["type"] for n in pack["fragment"]["nodes"]}
        self.assertEqual(types, {"concept", "episode"})
        self.assertEqual(pack["fragment"]["edges"],
                         [{"from": "concept:a", "to": "concept:b", "type": "relates_to"}])

    def test_no_preference_signals_in_pack(self):
        pack = share.build_share_pack(owner="user:alice", domain="math",
                                      generated_at="x", store=self._store())
        blob = str(pack)
        for banned in ("pinned", "access_count", "last_access"):
            self.assertNotIn(banned, blob)
        # 투영 자체가 preference 열을 선택하지 않는다
        store = self._store()
        share.build_share_pack(owner="user:alice", domain="math", generated_at="x", store=store)
        for cy, _ in store.calls:
            for banned in ("pinned", "access_count", "last_access"):
                self.assertNotIn(banned, cy)

    def test_tag_scope_filters_overlap(self):
        pack = share.build_share_pack(owner="user:alice", domain="math", tags=["core"],
                                      generated_at="x", store=self._store())
        ids = {n["id"] for n in pack["fragment"]["nodes"]}
        self.assertEqual(ids, {"concept:a", "ep:1"})    # 'misc'만 가진 b 제외

    def test_seed_scope_uses_subgraph_and_skips_records(self):
        store = _FakeStore({
            "DISTINCT m.concept_id": [{"id": "concept:b"}],
            "one_liner": _CONCEPTS,
            "OPTIONAL MATCH": _EDGE_ROWS,
        })
        pack = share.build_share_pack(owner="user:alice", seed="concept:a", depth=1,
                                      generated_at="x", store=store)
        self.assertEqual(pack["manifest"]["scope"]["seed"], "concept:a")
        self.assertEqual(pack["manifest"]["counts"]["records"], 0)   # ego 스코프 = 개념 중심
        # G1 — ego 순회는 도달집합 의미론(SHORTEST). 구 TRAIL 에서 교체됐다.
        self.assertTrue(any("SHORTEST" in cy for cy, _ in store.calls))

    def test_without_store_degrades(self):
        pack = share.build_share_pack(owner="user:alice", domain="math", store=None)
        if not pack.get("executed"):
            self.assertIn("note", pack)


class ImportPackTests(unittest.TestCase):
    def test_version_check(self):
        res = share.import_share_pack({"vinc_pack": 99}, owner="user:bob", store=_FakeStore())
        self.assertFalse(res["executed"])
        self.assertIn("unsupported pack version", res["error"])

    def test_import_stamps_recipient_owner(self):
        store = _FakeStore()
        pack = {"vinc_pack": 1, "manifest": {"created": "x"},
                "fragment": {"nodes": [{"id": "concept:a", "type": "concept"},
                                       {"id": "ep:1", "type": "episode",
                                        "date": "2026-07-01", "summary": "s"}],
                             "edges": []}}
        res = share.import_share_pack(pack, owner="user:bob", store=store)
        self.assertTrue(res["executed"])
        self.assertEqual(res["merged"]["nodes"]["created"], 2)
        # 노드 MERGE 만 — S0 스탬핑이 `_SyncMeta`(HLC·seq 카운터)에도 MERGE 하므로
        # 그건 제외한다(owner 를 실어야 할 대상이 아니다).
        merge_params = [p for cy, p in store.calls
                        if cy.startswith("MERGE") and "_SyncMeta" not in cy]
        self.assertTrue(all(p.get("owner_id") == "user:bob" for p in merge_params))
        # Episode 콘텐츠 필드 사상
        # PK 네임스페이싱이 켜지면 `$id` 바인딩은 물리 키다(사용자 대면 id 는 그대로).
        ep = [p for p in merge_params
              if p.get("id") == pkns.key("user:bob", "ep:1")][0]
        self.assertEqual(ep.get("date"), "2026-07-01")
        self.assertEqual(ep.get("summary"), "s")
        self.assertEqual(res["manifest"], {"created": "x"})


if __name__ == "__main__":
    unittest.main()


class IdsScopeAndInboxTests(unittest.TestCase):
    def test_ids_scope_packs_exact_set(self):
        store = _FakeStore({"one_liner": _CONCEPTS, "OPTIONAL MATCH": _EDGE_ROWS})
        pack = share.build_share_pack(owner="user:alice", ids=["concept:a", "concept:b"],
                                      generated_at="x", store=store)
        self.assertEqual(pack["manifest"]["scope"]["ids"], 2)
        self.assertEqual(pack["manifest"]["counts"]["records"], 0)  # ids 스코프 = 개념 중심
        cy = [c for c, p in store.calls if "IN $ids" in c]
        self.assertTrue(cy)                                          # 명시 집합 질의

    def test_scan_inbox_imports_and_marks_done(self):
        import json as _json, tempfile, os
        from pathlib import Path as _P
        store = _FakeStore()
        with tempfile.TemporaryDirectory() as d:
            good = {"vinc_pack": 1, "fragment": {"nodes": [{"id": "c1", "type": "concept"}], "edges": []}}
            _P(d, "a.json").write_text(_json.dumps(good))
            _P(d, "bad.json").write_text("{not json")
            res = share.scan_inbox(d, owner="user:me", store=store)
            self.assertTrue(res["executed"])
            self.assertEqual(len(res["imported"]), 1)
            self.assertEqual(len(res["errors"]), 1)
            self.assertTrue(os.path.exists(os.path.join(d, "a.json.done")))
            merge_params = [p for c, p in store.calls
                            if c.startswith("MERGE") and "_SyncMeta" not in c]
            self.assertTrue(all(p.get("owner_id") == "user:me" for p in merge_params))


class PromoteCandidatesTests(unittest.TestCase):
    def test_plan_targets_unpinned_with_signal_threshold(self):
        from api import query as q
        plan = q.promote_candidates_plan(owner="u", min_access=5)
        self.assertIn("coalesce(e.pinned, false) = false", plan.cypher)
        self.assertIn("$min_access", plan.cypher)
        self.assertNotIn("SET ", plan.cypher)   # 조회 전용 — 단일 쓰기 표면 유지


# ── 실엔진: 자기완결 팩(원문 포함, D45) 라운드트립 ────────────────────────────
try:
    from store.embedded_backend import _import_driver as _imp_drv
    _SHARE_DRIVER = _imp_drv()[0]
except Exception:
    _SHARE_DRIVER = None


@unittest.skipIf(_SHARE_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class PackDocumentsE2E(unittest.TestCase):
    """팩에 개념 참조 원문을 싣고(build) 수신 owner 로 복원(import)하는 전 경로 —
    무손실·owner 격리(SEC-1)·교차 doc_id 충돌 회피·멱등. FakeStore 로는 못 잡는
    put_doc/get_doc/MENTIONS/merge 상호작용을 실엔진으로 못 박는다."""
    TEXT = ("Photosynthesis is the process by which plants convert light into "
            "chemical energy. Cellular respiration then releases that energy.")

    def _store(self):
        import tempfile, os
        from store.config import load_config
        from store.backend import open_store
        st = open_store(load_config({"data_dir": os.path.join(tempfile.mkdtemp(), "db")}))
        st.provision(dim=4)
        self.addCleanup(st.close)
        return st

    def _seed(self, st, owner):
        from api import fragment as frag, docstore as ds
        frag.merge_fragment({"owner": owner, "edges": [], "nodes": [
            {"id": "concept:photosynthesis", "type": "concept", "title": "Photosynthesis"},
            {"id": "concept:cellular-respiration", "type": "concept",
             "title": "Cellular respiration"}]}, store=st)
        put = ds.put_doc(st, owner=owner, text=self.TEXT, title="Bio", origin="bio.md")
        self.assertTrue(put.get("executed"))
        return put["doc_id"]

    def test_build_attaches_referenced_documents(self):
        st = self._store()
        doc_a = self._seed(st, "user:alice")
        pack = share.build_share_pack(owner="user:alice", store=st)
        self.assertEqual(pack["manifest"]["counts"]["documents"], 1)
        docs = pack["documents"]
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["text"], self.TEXT)   # 무손실
        self.assertEqual(docs[0]["doc_id"], doc_a)

    def test_import_reconstructs_under_receiver_owner(self):
        from api import docstore as ds
        src = self._store()
        doc_a = self._seed(src, "user:alice")
        pack = share.build_share_pack(owner="user:alice", store=src)

        dst = self._store()   # 수신자 스토어(로컬/청정 호스티드)
        imp = share.import_share_pack(pack, owner="user:bob", store=dst)
        self.assertEqual(imp["documents"]["applied"], 1)
        b_docs = ds.list_docs(dst, owner="user:bob")["docs"]
        self.assertEqual(len(b_docs), 1)
        doc_b = b_docs[0]["doc_id"]
        self.assertNotEqual(doc_b, doc_a)             # 수신 owner 로 새 id 파생
        got = ds.get_doc(dst, doc_b, owner="user:bob")
        self.assertTrue(got["executed"] and got["verified"])
        self.assertEqual(got["text"], self.TEXT)      # 무손실 복원
        owners = {r["o"] for r in dst.run(
            "MATCH (c:Chunk) WHERE c.doc_id=$d RETURN DISTINCT c.owner_id AS o",
            {"d": doc_b})}
        self.assertEqual(owners, {"user:bob"})        # SEC-1 스탬프
        # 재임포트 멱등(내용 동일 → skip, 중복 문서 0)
        imp2 = share.import_share_pack(pack, owner="user:bob", store=dst)
        self.assertEqual(imp2["documents"], {"applied": 0, "skipped": 1, "total": 1})
        self.assertEqual(len(ds.list_docs(dst, owner="user:bob")["docs"]), 1)

    def test_shared_store_owner_isolation(self):
        # 호스티드 단일 스토어에 B 로 임포트 → B 문서 새 id·owner, A 문서 불변, 교차차단.
        from api import docstore as ds
        st = self._store()
        doc_a = self._seed(st, "user:alice")
        pack = share.build_share_pack(owner="user:alice", store=st)
        imp = share.import_share_pack(pack, owner="user:bob", store=st)
        self.assertEqual(imp["documents"]["applied"], 1)
        doc_b = ds.list_docs(st, owner="user:bob")["docs"][0]["doc_id"]
        self.assertNotEqual(doc_b, doc_a)
        a_owners = {r["o"] for r in st.run(
            "MATCH (c:Chunk) WHERE c.doc_id=$d RETURN DISTINCT c.owner_id AS o",
            {"d": doc_a})}
        self.assertEqual(a_owners, {"user:alice"})    # A 원문 불변
        cross = ds.get_doc(st, doc_a, owner="user:bob")
        self.assertFalse(cross.get("executed"))
        self.assertIn("mismatch", cross.get("error") or "")


@unittest.skipIf(_SHARE_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class DomainSubtreeScopeE2E(unittest.TestCase):
    """주제 스코프는 **하위 트리**다 — 부모 주제를 공유해도 팩이 비지 않는다.

    정확일치 시절엔 부모 주제로 공유하면 개념 0건 → `_docs_for_concepts` 가 문서까지
    0으로 만들어 **빈 팩이 오류 없이** 나갔다. 슬래시가 요점이라 형제 주제
    (`vinc/designsystem`)는 들어오면 안 된다. FakeStore 는 WHERE 를 안 보므로 실엔진이다.
    """
    TEXT = ("Design tokens carry the value layer of the system. "
            "The Contrast floor is fixed by those tokens.")

    def _store(self):
        import tempfile, os
        from store.config import load_config
        from store.backend import open_store
        st = open_store(load_config({"data_dir": os.path.join(tempfile.mkdtemp(), "db")}))
        st.provision(dim=4)
        self.addCleanup(st.close)
        return st

    def _seed(self, st, owner="user:alice", pre=""):
        """⚠ 노드 id 는 **전역 네임스페이스**라 두 owner 가 같은 슬러그를 못 쓴다
        (남의 id 는 경고와 함께 skip 된다). 그래서 owner 마다 `pre` 로 가른다."""
        from api import fragment as frag, docstore as ds
        res = frag.merge_fragment({"owner": owner, "edges": [], "nodes": [
            {"id": f"concept:{pre}design-tokens", "type": "concept", "title": "Design tokens",
             "domain": "vinc/design/tokens"},
            {"id": f"concept:{pre}contrast-floor", "type": "concept", "title": "Contrast floor",
             "domain": "vinc/design/tokens"},
            {"id": f"ep:{pre}token-review", "type": "episode", "title": "Token review",
             "domain": "vinc/design/tokens"},
            # 형제 주제 — 접두에 슬래시를 안 붙이면 이것까지 딸려 온다.
            {"id": f"concept:{pre}designsystem-note", "type": "concept",
             "title": "Designsystem note", "domain": "vinc/designsystem"},
        ]}, store=st)
        self.assertEqual(res.get("warnings") or [], [])
        put = ds.put_doc(st, owner=owner, text=self.TEXT, title="Tokens", origin="tokens.md")
        self.assertTrue(put.get("executed"))
        return put["doc_id"]

    def test_parent_topic_pulls_child_concepts_and_documents(self):
        st = self._store()
        doc = self._seed(st)
        pack = share.build_share_pack(owner="user:alice", domain="vinc/design", store=st)
        counts = pack["manifest"]["counts"]
        self.assertEqual(counts["concepts"], 2)
        self.assertEqual(counts["records"], 1)          # Episode 도 같은 술어를 쓴다
        self.assertEqual(counts["documents"], 1)        # 개념이 와야 문서가 따라온다
        self.assertEqual([d["doc_id"] for d in pack["documents"]], [doc])
        self.assertEqual(len(pack["fragment"]["nodes"]), counts["concepts"] + counts["records"])

    def test_sibling_topic_is_not_swept_in(self):
        st = self._store()
        self._seed(st)
        pack = share.build_share_pack(owner="user:alice", domain="vinc/design", store=st)
        ids = {n["id"] for n in pack["fragment"]["nodes"]}
        self.assertEqual(ids, {"concept:design-tokens", "concept:contrast-floor",
                               "ep:token-review"})
        self.assertNotIn("concept:designsystem-note", ids)   # 접두는 `d + "/"` 다

    def test_subtree_scope_still_stops_at_the_owner(self):
        """SEC-1 — 접두일치가 소유자를 넘지 않는다.

        무는 자리는 **팩에 실린 노드 id 집합**이다. 문서 쪽으로는 이 누출이 안 보인다:
        `_docs_for_concepts` 가 Chunk 에 자기 owner 술어를 또 걸고(`api/share.py`),
        `make_doc_id` 는 owner 를 해시에 넣어(`api/docstore.py`) 두 owner 의 doc_id 가
        애초에 겹칠 수 없다. 실측 — `_concepts_plan` 의 `c.owner_id = $owner` 를 지우면
        개념이 2→4 로 새는데 `counts["documents"]` 는 1 그대로였다.
        """
        st = self._store()
        self._seed(st, owner="user:alice")
        b_doc = self._seed(st, owner="user:bob", pre="b-")
        pack = share.build_share_pack(owner="user:bob", domain="vinc/design", store=st)
        ids = {n["id"] for n in pack["fragment"]["nodes"]}
        # 개념(`_concepts_plan`)과 Episode(`_episodes_plan`) 둘 다 — 어느 쪽 owner
        # 술어를 지워도 alice 의 id 가 여기 섞여 이 한 줄이 깨진다.
        self.assertEqual(ids, {"concept:b-design-tokens", "concept:b-contrast-floor",
                               "ep:b-token-review"})
        self.assertEqual([d["doc_id"] for d in pack["documents"]], [b_doc])


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class PackTimeContract(unittest.TestCase):
    """팩이 나르는 시간의 계약(발주자 결정 2026-08-17).

    `created` 의 뜻은 **"이 노드가 어느 공간에서든 처음 기록된 때"**다. 그래서 신규
    노드는 원본 값을 받고(밴딩 증상 해결이 그대로 유지된다), **기존 노드는 불가침**이다
    — 팩은 미신뢰 외부 입력이라 동기화의 min 과 달리 쓰기 자체를 안 한다.
    그리고 공간을 건너도 참인 `content_time` 은 여태 안 실렸다(실측). 이제 싣는다.
    """

    OWNER_A, OWNER_B = "user:author", "user:reader"

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_EMBEDDING_MODEL": "local-en",
                     "MEMORY_DATA_DIR": os.path.join(self._tmp, "store")}.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        from store.config import load_config
        from store.backend import open_store
        cfg = load_config()
        self.store = open_store(cfg)
        self.store.provision(dim=cfg.embedding_dim)

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _row(self, cid, owner=None):
        # 스토어 직접 조회 — 쓰기는 fragment/share 를 거쳤으니 물리 키로 물어야 한다.
        rows = self.store.run("MATCH (c:Concept) WHERE c.concept_id = $i "
                              "RETURN c.created AS created, c.content_time AS ct, "
                              "c.owner_id AS o",
                              {"i": pkns.key(owner or self.OWNER_B, cid)})
        return rows[0] if rows else {}

    def test_pack_carries_both_clocks_and_import_lands_them(self):
        from api import fragment as frag
        from api import share
        frag.merge_fragment({"owner": self.OWNER_A, "nodes": [
            {"id": "concept:relativity", "type": "concept", "title": "Relativity",
             "domain": "phys", "content_time": "1905",
             "created": "2019-03-03T00:00:00.000Z"}]}, store=self.store)

        pack = share.build_share_pack(owner=self.OWNER_A, domain="phys", store=self.store)
        node = pack["fragment"]["nodes"][0]
        self.assertEqual(node["created"], "2019-03-03T00:00:00.000Z")
        self.assertEqual(node["content_time"], "1905", "세계 시간이 팩을 못 건넜다")

        # 받는 쪽에는 아직 없는 상태를 만든다. DETACH 인 이유: 도메인 계층(IN_DOMAIN)이
        # 이미 붙어 있어 그냥 DELETE 하면 엔진이 거절한다.
        self.store.run("MATCH (c:Concept) WHERE c.concept_id=$i DETACH DELETE c",
                       {"i": pkns.key(self.OWNER_A, "concept:relativity")})
        share.import_share_pack(pack, owner=self.OWNER_B, store=self.store)
        got = self._row("concept:relativity")
        self.assertEqual(got["created"], "2019-03-03T00:00:00.000Z",
                         "신규는 원본 기록 시각을 받아야 한다(밴딩 방지)")
        self.assertEqual(got["ct"], "1905")

    def test_reimport_never_moves_an_existing_birth(self):
        """여태의 결함: 재-MERGE 가 무조건 덮어, 내가 쓴 개념의 생일까지 남의 값이 됐다."""
        from api import fragment as frag
        from api import share
        frag.merge_fragment({"owner": self.OWNER_B, "nodes": [
            {"id": "concept:x", "type": "concept", "title": "내가 쓴 것", "domain": "phys"}]},
            store=self.store)
        mine = self._row("concept:x")["created"]
        self.assertTrue(mine)

        pack = {"vinc_pack": share.PACK_VERSION, "kind": "share",
                "fragment": {"nodes": [
                    {"id": "concept:x", "type": "concept", "title": "남의 것",
                     "domain": "phys", "created": "1999-01-01T00:00:00.000Z"}],
                    "edges": []}}
        share.import_share_pack(pack, owner=self.OWNER_B, store=self.store)
        self.assertEqual(self._row("concept:x")["created"], mine,
                         "팩이 기존 노드의 출생을 덮었다")

    def test_pack_documents_carry_content_time_but_are_reborn_locally(self):
        """문서는 수신측에서 doc_id 가 새로 파생되므로 **다른 객체**다 — 출생은 지금이
        맞고, 지시체의 시간(content_time)만 건너온다."""
        from api import docstore as ds
        from api import share
        text = "---\ndomain: physics\n---\n# 노트\n\n## relativity\n\n광속 불변.\n"
        did = ds.put_doc(self.store, owner=self.OWNER_A, text=text, title="물리",
                         content_time="1905")["doc_id"]
        ds.extract_doc(self.store, did, owner=self.OWNER_A, apply=True)
        pack = share.build_share_pack(owner=self.OWNER_A, domain="", store=self.store)
        docs = pack.get("documents") or []
        self.assertTrue(docs, "자기완결 팩이 원문을 안 실었다")
        self.assertEqual(docs[0]["content_time"], "1905")

        share.import_share_pack(pack, owner=self.OWNER_B, store=self.store)
        rows = self.store.run("MATCH (d:_DocMeta) WHERE d.owner_id = $o "
                              "RETURN d.content_time AS ct, d.created_at AS born",
                              {"o": self.OWNER_B})
        self.assertTrue(rows, "수신측에 문서가 없다")
        self.assertEqual(rows[0]["ct"], "1905")
        self.assertTrue(rows[0]["born"], "수신 문서는 자기 공간의 출생을 가져야 한다")
