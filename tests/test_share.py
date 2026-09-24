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
            "UNION ALL": _EDGE_ROWS,
        })

    def test_domain_scope_pack_shape(self):
        pack = share.build_share_pack(owner="user:alice", domain="math",
                                      generated_at="2026-07-10T00:00:00Z", store=self._store())
        self.assertTrue(pack["executed"])
        self.assertEqual(pack["vinc_pack"], 1)
        self.assertEqual(pack["manifest"]["counts"],
                         {"concepts": 2, "records": 1, "decisions": 0, "edges": 1, "documents": 0})   # 경계 밖 엣지 절단; 원문 없음
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
        store = _FakeStore({"one_liner": _CONCEPTS, "UNION ALL": _EDGE_ROWS})
        pack = share.build_share_pack(owner="user:alice", ids=["concept:a", "concept:b"],
                                      generated_at="x", store=store)
        self.assertEqual(pack["manifest"]["scope"]["ids"], 2)
        self.assertEqual(pack["manifest"]["counts"]["records"], 0)  # 이 fixture에는 Episode/Record 행이 없다
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
        # 남의 문서는 없는 문서와 같게 답한다 — 후보에도 남의 id 가 새지 않는다.
        self.assertEqual(cross.get("error"), "unknown doc_id")
        self.assertNotIn(doc_a, cross.get("candidates") or [])


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


@unittest.skipIf(_SHARE_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class DocMirrorPullsItsDocumentE2E(unittest.TestCase):
    """문서 미러(`concept:doc.<doc_id>`)가 팩에 실리면 **그 문서도** 실려야 한다.

    미러는 Concept 이라 주제 스코프에 걸린다. 그런데 `_docs_for_concepts` 의
    `(Chunk)-[:MENTIONS]->(Concept)` 역추적은 **본문이 개념 이름을 담을 때만** 문서를
    찾으므로, 주제를 손으로 적기만 한 문서는 따라오지 않았다 — 받는 쪽은 "가리키는
    개념은 있는데 그 문서가 없는" 반쪽 팩을 오류 없이 얻는다.

    P11 이 넣은 `DomainSubtreeScopeE2E` 는 픽스처 본문이 개념 제목을 그대로 담고 있어
    이 경우를 재지 못한다. 그래서 본문에 어떤 개념 제목도 없는 문서로 잰다.
    """
    # 어떤 개념 제목도 담지 않는다 — MENTIONS 가 0건이어야 이 경우가 재현된다.
    TEXT = "회의는 화요일 오전에 한다. 참석자는 셋이고 장소는 아직 안 정했다.\n"

    def _store(self):
        import tempfile, os
        from store.config import load_config
        from store.backend import open_store
        st = open_store(load_config({"data_dir": os.path.join(tempfile.mkdtemp(), "db")}))
        st.provision(dim=4)
        self.addCleanup(st.close)
        return st

    def _seed(self, st, owner="user:alice"):
        """주제만 적힌 문서 하나 — `topic` 인자가 미러를 만든다(`_set_authored_topic`)."""
        from api import docstore as ds
        put = ds.put_doc(st, owner=owner, text=self.TEXT, title="회의 노트",
                         origin="meeting.md", topic="vinc/design/tokens")
        self.assertTrue(put.get("executed"))
        return put["doc_id"]

    def test_authored_topic_document_rides_along_with_its_mirror(self):
        st = self._store()
        doc = self._seed(st)
        pack = share.build_share_pack(owner="user:alice", domain="vinc/design", store=st)
        # 미러가 노드로 실렸는지 먼저 확인한다 — 아니면 이 테스트는 다른 것을 재고 있다.
        self.assertIn("concept:doc." + doc, {n["id"] for n in pack["fragment"]["nodes"]})
        self.assertEqual([d["doc_id"] for d in pack.get("documents") or []], [doc])
        self.assertEqual(pack["manifest"]["counts"]["documents"], 1)

    def test_roundtrip_applies_every_document_the_manifest_counted(self):
        st = self._store()
        self._seed(st)
        pack = share.build_share_pack(owner="user:alice", domain="vinc/design", store=st)
        counted = pack["manifest"]["counts"]["documents"]
        self.assertTrue(counted, "팩이 문서를 안 실었다 — 왕복을 잴 것이 없다")
        res = share.import_share_pack(pack, owner="user:bob", store=st)
        self.assertEqual(res["documents"]["applied"], counted,
                         "공유 시트가 보여준 수와 실제로 들어간 수가 다르다")
        # 재임포트는 멱등 — content_hash 매치로 전부 skip 이다.
        again = share.import_share_pack(pack, owner="user:bob", store=st)
        self.assertEqual(again["documents"]["applied"], 0)
        self.assertEqual(again["documents"]["skipped"], counted)

    def test_mirror_never_reaches_across_owners(self):
        """SEC-1 — 미러 id 에서 푼 doc_id 도 **자기 owner 스코프 안에서만** 조회한다."""
        st = self._store()
        a_doc = self._seed(st, owner="user:alice")
        self.assertEqual(
            share._docs_for_concepts(st, owner="user:bob",
                                     concept_ids=["concept:doc." + a_doc]), [])

    def test_a_mirror_whose_document_is_gone_ships_no_document(self):
        """④ 부재(존재하지 않는 doc_id) 쪽을 잠근다.

        P11b 는 `if not got.get("executed"): continue` 한 줄이 **둘**을 막는다고 적었다
        — 남의 문서 유출과, 없는 doc_id 를 실어 임포트를 깨는 것. 그런데 변이 실측에서
        그 줄을 지웠을 때 빨개지는 것은 교차-owner 테스트 **하나뿐**이었다. 절반이
        무보호였다.

        부재를 만드는 길은 지어낼 필요가 없다: `delete_doc` 은 청크·메타·툼스톤만
        건드리고 **미러 Concept 을 안 지운다**. 그래서 지운 문서의 미러가 그래프에
        남고 그 주제의 팩마다 실린다(⑥과 같은 뿌리의 결손, 판정은 커밋 메시지에).
        여기서 재는 것은 그 상태에서 팩이 **빈 껍데기 문서를 만들지 않는다**는 것이다.
        """
        from api import docstore as ds
        st = self._store()
        doc = self._seed(st)
        self.assertTrue(ds.delete_doc(st, doc, owner="user:alice")["executed"])
        mirror = "concept:doc." + doc
        pack = share.build_share_pack(owner="user:alice", domain="vinc/design", store=st)
        # 전제 확인 — 미러가 실제로 팩에 남아 있어야 이 테스트가 그 경우를 잰다.
        self.assertIn(mirror, {n["id"] for n in pack["fragment"]["nodes"]})
        self.assertEqual(pack.get("documents") or [], [],
                         "지워진 문서의 doc_id 가 팩에 실렸다 — 임포트가 빈 문서를 만든다")
        self.assertEqual(pack["manifest"]["counts"]["documents"], 0)
        self.assertEqual(
            share._docs_for_concepts(st, owner="user:alice", concept_ids=[mirror]), [])

    def test_mentions_lookup_does_not_even_ask_for_another_owners_document(self):
        """⑤ MENTIONS 술어의 owner 절을 **테스트로** 만든다.

        `_docs_for_concepts` 독스트링은 "SEC-1: owner 스코프 청크만 역추적한다"고
        단언하는데, `c.owner_id = $o` 를 지워도 share 테스트 22건이 전부 초록이었다
        — 뒤의 `get_doc(..., owner=owner)` 가 두 번째 방어선이라 실제 누출은 없지만,
        **첫 번째 벽은 아무도 안 지키고 있었다**. 그래서 그 벽이 하는 일을 직접 잰다:
        남의 개념에서 나온 doc_id 는 `get_doc` 까지 **가지도 않는다**.

        ⚠ PK 네임스페이싱이 켜진 잡에서는 `$ids` 가 이미 bob 으로 합성돼 alice 의
        키와 안 맞으므로, 이 테스트는 그 모드에서 변이를 못 잡는다(그쪽은 pkns 가
        같은 일을 하는 세 번째 벽이다). 잡는 자리는 꺼진 모드(`core`)다.
        """
        from api import docstore as ds
        st = self._store()
        # 본문이 개념 제목을 담아 MENTIONS 가 실제로 생기는 픽스처를 쓴다.
        a_concept = "concept:design-tokens"
        from api import fragment as frag
        self.assertEqual(frag.merge_fragment({"owner": "user:alice", "edges": [], "nodes": [
            {"id": a_concept, "type": "concept", "title": "Design tokens",
             "domain": "vinc/design/tokens"}]}, store=st).get("warnings") or [], [])
        a_doc = ds.put_doc(st, owner="user:alice", origin="tokens.md", title="Tokens",
                           text="Design tokens carry the value layer.")["doc_id"]
        self.assertTrue(st.run(
            "MATCH (c:Chunk)-[:MENTIONS]->(co:Concept) WHERE c.doc_id = $d "
            "RETURN c.doc_id AS d", {"d": a_doc}), "픽스처에 MENTIONS 가 없다")

        asked: list[str] = []
        real = ds.get_doc

        def _spy(store, doc_id, **kw):
            asked.append(doc_id)
            return real(store, doc_id, **kw)

        ds.get_doc = _spy
        self.addCleanup(setattr, ds, "get_doc", real)
        self.assertEqual(
            share._docs_for_concepts(st, owner="user:bob", concept_ids=[a_concept]), [])
        self.assertNotIn(a_doc, asked,
                         "남의 문서 id 가 역추적에서 나왔다 — MENTIONS 의 owner 절이 죽었다")

    def test_roundtrip_leaves_the_mirror_pointing_at_a_document_that_is_not_here(self):
        """⑥ **아직 못 고친 결손을 고정한다** — 지금 테스트가 개수만 재서 이걸 통과시킨다.

        수신측이 받는 미러는 발신자의 doc_id 를 그대로 안고 오는데
        (`concept:doc.<발신 doc_id>`), 임포트는 문서의 doc_id 를 수신 owner 로 **새로
        판다**(`put_doc(doc_id=None)` — 교차-owner 충돌 회피). 게다가 `import_share_pack`
        은 `topic=` 을 안 넘기므로 새 문서에는 자기 미러가 생기지 않는다. 결과: 받은
        미러는 이 공간에 없는 문서를 가리키고, 적힌 주제도 새 문서에 안 붙는다.

        고치려면 팩 포맷(미러 → doc_id 재사상)이나 id 정책 판정이 필요해 P11c 범위
        밖이다. 여기 못박아 두는 이유는 **고쳐졌을 때 이 테스트가 빨개져서** 그 사실이
        보이게 하려는 것이다. 빨개지면 지우지 말고 기대값을 뒤집을 것.
        """
        from api import docstore as ds
        src = self._store()
        doc = self._seed(src)
        pack = share.build_share_pack(owner="user:alice", domain="vinc/design", store=src)
        dst = self._store()
        res = share.import_share_pack(pack, owner="user:bob", store=dst)
        self.assertEqual(res["documents"]["applied"], 1)

        got = [r["d"] for r in dst.run(
            "MATCH (d:_DocMeta) WHERE d.owner_id = $o RETURN d.doc_id AS d",
            {"o": "user:bob"}) or []]
        self.assertEqual(len(got), 1)
        bob_doc = got[0]
        self.assertNotEqual(bob_doc, doc, "doc_id 가 그대로면 이 결손은 사라진 것이다")
        # 받은 미러는 발신자의 doc_id 를 가리키고, 그 문서는 여기 없다.
        mirrors = {pkns.strip(r["c"]) for r in dst.run(
            "MATCH (c:Concept) RETURN c.concept_id AS c") or []}
        self.assertIn("concept:doc." + doc, mirrors)
        self.assertNotIn("concept:doc." + bob_doc, mirrors,
                         "새 문서에 미러가 생겼다면 재사상이 구현된 것이다")
        self.assertFalse(ds.get_doc(dst, doc, owner="user:bob").get("executed"))


@unittest.skipIf(_SHARE_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class ShareWithoutAnInjectedStoreE2E(unittest.TestCase):
    """② store 를 안 넘긴 호출이 문서를 **조용히** 0으로 만들면 안 된다.

    `api/cli.py` 의 `share` 는 `store=`/`cfg=` 없이 `build_share_pack` 을 부른다.
    개념은 `query.execute` 가 스스로 스토어를 열어 제대로 나오는데, `_docs_for_concepts`
    는 `store is None` 을 "개념이 없다"와 한 줄로 묶어 빈 목록을 냈다. 오류도 로그도
    없고 매니페스트는 `documents: 0` 을 성공으로 보고했다.

    실측(2026-09-07, 같은 스토어·같은 스코프):
      store 주입 → {'concepts': 1, ..., 'documents': 1}
      store 미주입 → {'concepts': 1, ..., 'documents': 0}

    그래서 **두 호출을 나란히 놓고** 잰다 — 한쪽만 재면 "문서가 없는 스코프"와
    구분이 안 된다."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._env = {}
        for k, v in {"MEMORY_STORE_BACKEND": "embedded",
                     "MEMORY_DATA_DIR": os.path.join(self._tmp, "store")}.items():
            self._env[k] = os.environ.get(k)
            os.environ[k] = v

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_documents_do_not_vanish_when_the_caller_forgets_the_store(self):
        from api import docstore as ds
        from store.backend import open_store
        from store.config import load_config
        cfg = load_config()
        st = open_store(cfg)
        st.provision(dim=4)
        doc = ds.put_doc(st, owner="user:alice", origin="meeting.md", title="회의 노트",
                         text="회의는 화요일 오전에 한다.\n",
                         topic="vinc/design/tokens")["doc_id"]
        injected = share.build_share_pack(owner="user:alice", domain="vinc/design", store=st)
        st.close()          # CLI 는 자기 프로세스에서 열므로 여기서 놓아 준다(단일 writer)

        forgot = share.build_share_pack(owner="user:alice", domain="vinc/design")
        self.assertEqual(injected["manifest"]["counts"]["documents"], 1)
        self.assertEqual(forgot["manifest"]["counts"], injected["manifest"]["counts"],
                         "store 를 안 넘긴 호출이 다른 수치를 냈다")
        self.assertEqual([d["doc_id"] for d in forgot.get("documents") or []], [doc])


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


@unittest.skipIf(_DRIVER is None, "embedded driver unavailable")
class MixedKindShareTests(unittest.TestCase):
    def setUp(self):
        from store.backend import open_store
        from store.config import load_config
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st = open_store(load_config({"data_dir": str(Path(self.tmp.name) / "db")}))
        self.st.provision(dim=4)
        self.addCleanup(self.st.close)
        self.nodes = [
            {"id": "concept:scope", "type": "concept", "title": "Scope", "domain": "vinc/share/child"},
            {"id": "decision:scope", "type": "decision", "title": "Choice", "domain": "vinc/share/child",
             "tags": ["picked"], "created": "2020-01-01T00:00:00.000Z",
             "props": {"decided_at": "2020-01", "valid_until": "2027", "nested": {"ok": True}}},
            {"id": "record:scope", "type": "record", "title": "Measurement", "domain": "vinc/share/child",
             "tags": ["picked"], "created": "2020-02-01T00:00:00.000Z", "content_time": "2019",
             "valid_from": "2020", "valid_until": "2028", "props": {"value": 0}},
            {"id": "episode:scope", "type": "episode", "title": "Run", "domain": "vinc/share/child",
             "date": "2020-03-01", "summary": "A result", "props": {"outcome": "passed"}},
            {"id": "concept:far", "type": "concept", "title": "Far", "domain": "vinc/other"},
            {"id": "record:sibling", "type": "record", "title": "Sibling", "domain": "vinc/shared"},
        ]
        self.edges = [
            {"from": "decision:scope", "to": "concept:scope", "type": "about"},
            {"from": "record:scope", "to": "decision:scope", "type": "derives_from"},
            {"from": "episode:scope", "to": "decision:scope", "type": "about"},
            {"from": "decision:scope", "to": "concept:far", "type": "prerequisite_of"},
        ]
        for edge in self.edges:
            # 0.0 은 열 기본값("모름")과 같아 싣지 않는 값이다. 왕복을 보려면 실제 값을 쓴다.
            edge.update(evidence="Measured support", confidence=0.8, created="2020-04-01T00:00:00.000Z")
        result = share.frag.merge_fragment({"owner": "user:alice", "nodes": self.nodes,
                                           "edges": self.edges}, store=self.st)
        self.assertFalse(result.get("warnings"), result)

    def pack(self, **scope):
        result = share.build_share_pack(owner="user:alice", store=self.st, **scope)
        self.assertTrue(result.get("executed"), result)
        return result

    def test_domain_roundtrip_preserves_kinds_fields_and_directed_metadata(self):
        pack = self.pack(domain="vinc/share")
        self.assertEqual({n["type"] for n in pack["fragment"]["nodes"]},
                         {"concept", "decision", "record", "episode"})
        self.assertCountEqual(pack["fragment"]["edges"], self.edges[:3])
        from store.backend import open_store
        from store.config import load_config
        dst = open_store(load_config({"data_dir": str(Path(self.tmp.name) / "dst")}))
        self.addCleanup(dst.close)
        dst.provision(dim=4)
        imported = share.import_share_pack(pack, owner="team:reader", store=dst)
        self.assertFalse(imported.get("warnings"), imported)
        again = share.build_share_pack(owner="team:reader", domain="vinc/share", store=dst)
        first_nodes = {n["id"]: n for n in pack["fragment"]["nodes"]}
        self.assertEqual({n["id"]: n for n in again["fragment"]["nodes"]}, first_nodes)
        self.assertCountEqual(again["fragment"]["edges"], pack["fragment"]["edges"])
        repeat = share.import_share_pack(pack, owner="team:reader", store=dst)
        self.assertEqual(repeat["merged"]["nodes"]["created"], 0)

    def test_exact_ids_tags_and_empty_scope(self):
        pack = self.pack(ids=["record:scope", "decision:scope"], domain="vinc/other")
        self.assertEqual({n["id"] for n in pack["fragment"]["nodes"]},
                         {"record:scope", "decision:scope"})
        self.assertEqual(pack["fragment"]["edges"], [self.edges[1]])
        filtered = self.pack(domain="vinc/share", tags=["picked"])
        self.assertEqual(filtered["fragment"], pack["fragment"])
        self.assertEqual(self.pack(ids=[])["fragment"], {"nodes": [], "edges": []})
        self.assertEqual(self.pack(domain="missing")["fragment"], {"nodes": [], "edges": []})

    def test_record_and_decision_seeds_have_exact_hop_boundaries(self):
        one = self.pack(seed="record:scope", depth=1)
        self.assertEqual({n["id"] for n in one["fragment"]["nodes"]},
                         {"record:scope", "decision:scope"})
        two = self.pack(seed="record:scope", depth=2)
        self.assertEqual({n["id"] for n in two["fragment"]["nodes"]},
                         {"record:scope", "decision:scope", "concept:scope", "concept:far", "episode:scope"})
        self.assertEqual(len(self.pack(seed="decision:scope", depth=1)["fragment"]["nodes"]), 5)

    def test_no_records_keeps_cli_concepts_only_contract(self):
        self.assertEqual([n["type"] for n in self.pack(domain="vinc/share", include_records=False)
                          ["fragment"]["nodes"]], ["concept"])

    def test_bridge_siblings_keep_the_existing_concept_ego_reach(self):
        result = share.frag.merge_fragment({"owner": "user:alice", "nodes": [
            {"id": "concept:bridge-a", "type": "concept", "title": "A", "domain": "vinc/left"},
            {"id": "concept:bridge-b", "type": "concept", "title": "B", "domain": "vinc/right"},
            {"id": "bridge:ab", "type": "bridge", "title": "Connection"}], "edges": [
                {"from": "bridge:ab", "to": "concept:bridge-a", "type": "connects_via"},
                {"from": "bridge:ab", "to": "concept:bridge-b", "type": "connects_via"}]}, store=self.st)
        self.assertFalse(result.get("warnings"), result)
        baseline = share.q.execute(share.q.subgraph_ids_plan("concept:bridge-a", depth=2,
                                                             owner="user:alice"), store=self.st)
        expected = {"concept:bridge-a"} | {r["id"] for r in baseline["rows"]}
        self.assertEqual(expected, {"concept:bridge-a", "concept:bridge-b"})
        for include_records in (False, True):
            pack = self.pack(seed="concept:bridge-a", depth=2, include_records=include_records)
            self.assertEqual({n["id"] for n in pack["fragment"]["nodes"] if n["type"] == "concept"}, expected)
            self.assertNotIn("bridge:ab", str(pack["fragment"]))
        self.assertEqual({n["id"] for n in self.pack(seed="concept:bridge-a", depth=1)["fragment"]["nodes"]},
                         {"concept:bridge-a"})
        # 오염된 교차-owner 중간 노드를 통해 도달 범위를 넓혀서는 안 된다.
        self.st.run("MATCH (b:Bridge) WHERE b.bridge_id=$id SET b.owner_id='team:other'",
                    {"id": pkns.key("user:alice", "bridge:ab")})
        self.assertEqual({n["id"] for n in self.pack(seed="concept:bridge-a", depth=2)["fragment"]["nodes"]},
                         {"concept:bridge-a"})

    def test_other_owner_and_space_never_export_even_via_corrupt_cross_owner_edge(self):
        share.frag.merge_fragment({"owner": "team:other", "nodes": [
            {"id": "decision:secret", "type": "decision", "title": "Secret", "domain": "vinc/share"}],
            "edges": []}, store=self.st)
        self.st.run("MATCH (a:Record), (b:Decision) WHERE a.record_id=$a AND b.decision_id=$b "
                    "MERGE (a)-[r:ATTACHES_TO]->(b) SET r.type='derives_from'",
                    {"a": pkns.key("user:alice", "record:scope"),
                     "b": pkns.key("team:other", "decision:secret")})
        for scope in ({"domain": "vinc/share"}, {"ids": ["decision:secret"]},
                      {"seed": "decision:secret", "depth": 4}, {"seed": "record:scope", "depth": 4}):
            pack = self.pack(**scope)
            self.assertNotIn("Secret", str(pack))
            self.assertNotIn("decision:secret", str(pack["fragment"]))

    def test_shared_domain_and_chunk_transit_match_existing_concept_reach(self):
        from api import docstore as ds
        extra = [
            {"id": "concept:domain-a", "type": "concept", "title": "Domain A", "domain": "vinc/domain-transit"},
            {"id": "concept:domain-b", "type": "concept", "title": "Domain B", "domain": "vinc/domain-transit"},
            {"id": "concept:chunk-a", "type": "concept", "title": "Chunk A", "domain": "vinc/chunk-left"},
            {"id": "concept:chunk-b", "type": "concept", "title": "Chunk B", "domain": "vinc/chunk-right"},
        ]
        share.frag.merge_fragment({"owner": "user:alice", "nodes": extra, "edges": []}, store=self.st)
        doc = ds.put_doc(self.st, owner="user:alice", title="Carrier", text="Evidence.")["doc_id"]
        for nid in ("concept:chunk-a", "concept:chunk-b"):
            self.st.run("MATCH (c:Chunk), (n:Concept) WHERE c.doc_id=$doc AND n.concept_id=$id "
                        "MERGE (c)-[:MENTIONS]->(n)", {"doc": doc, "id": pkns.key("user:alice", nid)})
        for prefix in ("domain", "chunk"):
            seed = f"concept:{prefix}-a"
            before = share.q.execute(share.q.subgraph_ids_plan(seed, depth=2, owner="user:alice"), store=self.st)
            self.assertTrue(before["executed"], before)
            expected = {seed} | {r["id"] for r in before["rows"]}
            self.assertEqual(expected, {seed, f"concept:{prefix}-b"})
            after = self.pack(seed=seed, depth=2)
            self.assertEqual({n["id"] for n in after["fragment"]["nodes"]}, expected)

    def test_reach_frontier_handles_empty_and_namespaced_entity_keys(self):
        empty = share.q.execute(share._share_reach_plan(owner="user:alice", frontier={}), store=self.st)
        self.assertTrue(empty["executed"], empty)
        self.assertEqual(empty["rows"], [])
        share.frag.merge_fragment({"owner": "user:alice", "nodes": [
            {"id": "item:a", "type": "item", "title": "A"},
            {"id": "item:b", "type": "item", "title": "B"}], "edges": [
                {"from": "item:a", "to": "item:b", "type": "follows"}]}, store=self.st)
        out = share.q.execute(share._share_reach_plan(owner="user:alice", frontier={"Entity": {"item:a"}}), store=self.st)
        self.assertTrue(out["executed"], out)
        self.assertEqual(out["rows"], [{"kind": "Entity", "id": "item:b"}])
        self.assertEqual(self.pack(ids=["item:a"])["fragment"], {"nodes": [], "edges": []})

    def test_failed_kind_query_cannot_report_successful_partial_pack(self):
        from unittest.mock import patch
        original = share.q.execute
        def failing(plan, *args, **kwargs):
            if plan.kind == "share_records":
                return {"executed": False, "note": "unavailable record table"}
            return original(plan, *args, **kwargs)
        with patch.object(share.q, "execute", side_effect=failing):
            result = share.build_share_pack(owner="user:alice", store=self.st)
        self.assertFalse(result["executed"])
        self.assertNotIn("fragment", result)

    def test_colliding_logical_ids_stay_scoped_when_namespaced(self):
        if not pkns.enabled():
            return  # 동일 테스트를 core-pkns에서도 실행한다.
        other = [{**n, "title": "Other owner"} for n in self.nodes]
        share.frag.merge_fragment({"owner": "team:other", "nodes": other, "edges": self.edges}, store=self.st)
        for scope in ({"domain": "vinc/share"}, {"ids": ["record:scope", "decision:scope"]},
                      {"seed": "record:scope", "depth": 4}):
            pack = self.pack(**scope)
            self.assertNotIn("Other owner", str(pack))
            self.assertNotIn(pkns.SEP, str(pack))

    def test_decision_mentions_carry_owned_documents_only(self):
        from api import docstore as ds
        own = ds.put_doc(self.st, owner="user:alice", title="Evidence", text="Supporting text.")["doc_id"]
        other = ds.put_doc(self.st, owner="team:other", title="Private", text="Private text.")["doc_id"]
        for doc in (own, other):
            self.st.run("MATCH (c:Chunk), (d:Decision) WHERE c.doc_id=$doc AND d.decision_id=$id "
                        "MERGE (c)-[:MENTIONS]->(d)",
                        {"doc": doc, "id": pkns.key("user:alice", "decision:scope")})
        pack = self.pack(ids=["decision:scope"])
        self.assertEqual([d["doc_id"] for d in pack["documents"]], [own])
        self.assertNotIn("Private text", str(pack))

    def test_import_keeps_recipient_values_the_sender_does_not_have(self):
        # 열을 ALTER 로 얻은 스토어는 빈 칸을 ''·0.0 으로 돌려준다. 그 모습을 재현한다.
        rid = pkns.key("user:alice", "record:scope")
        self.st.run("MATCH (r:Record) WHERE r.record_id=$id "
                    "SET r.content_time='', r.valid_from='', r.valid_until=''", {"id": rid})
        self.st.run("MATCH (r:Record)-[a:ATTACHES_TO]->(d:Decision) WHERE r.record_id=$id "
                    "SET a.evidence='', a.confidence=0.0", {"id": rid})
        pack = self.pack(ids=["record:scope", "decision:scope"])
        record = next(n for n in pack["fragment"]["nodes"] if n["id"] == "record:scope")
        for field in ("content_time", "valid_from", "valid_until"):
            self.assertNotIn(field, record)
        self.assertEqual(pack["fragment"]["edges"],
                         [{"from": "record:scope", "to": "decision:scope",
                           "type": "derives_from", "created": "2020-04-01T00:00:00.000Z"}])
        from store.backend import open_store
        from store.config import load_config
        dst = open_store(load_config({"data_dir": str(Path(self.tmp.name) / "dst")}))
        self.addCleanup(dst.close)
        dst.provision(dim=4)
        seeded = share.frag.merge_fragment({"owner": "team:reader", "nodes": [
            {"id": "decision:scope", "type": "decision", "title": "Choice"},
            {"id": "record:scope", "type": "record", "title": "Measurement",
             "content_time": "2020", "valid_until": "2026-09"}], "edges": [
            {"from": "record:scope", "to": "decision:scope", "type": "derives_from",
             "evidence": "team note", "confidence": 0.9}]}, store=dst)
        self.assertEqual(seeded["merged"]["edges"]["created"], 1, seeded)
        imported = share.import_share_pack(pack, owner="team:reader", store=dst)
        self.assertTrue(imported.get("executed"), imported)
        mine = pkns.key("team:reader", "record:scope")
        row = dst.run("MATCH (r:Record) WHERE r.record_id=$id "
                      "RETURN r.content_time AS ct, r.valid_until AS vu", {"id": mine})[0]
        self.assertEqual((row["ct"], row["vu"]), ("2020", "2026-09"))
        edge = dst.run("MATCH (r:Record)-[a:ATTACHES_TO]->(d:Decision) WHERE r.record_id=$id "
                       "RETURN a.evidence AS ev, a.confidence AS conf", {"id": mine})[0]
        self.assertEqual((edge["ev"], edge["conf"]), ("team note", 0.9))

    def test_bad_stored_props_leave_only_the_props_behind_and_are_reported(self):
        # 한 행이 팩 전체를 실패시키지 않는다. 노드는 싣고 못 읽는 props 만 뺀다.
        self.st.run("MATCH (r:Record) WHERE r.record_id=$id SET r.props=$props",
                    {"id": pkns.key("user:alice", "record:scope"), "props": "[1, 2]"})
        self.st.run("MATCH (c:Concept) WHERE c.concept_id=$id SET c.props=$props",
                    {"id": pkns.key("user:alice", "concept:scope"), "props": "not json"})
        pack = self.pack(domain="vinc/share")
        nodes = {n["id"]: n for n in pack["fragment"]["nodes"]}
        self.assertEqual(nodes["record:scope"]["valid_until"], "2028")
        self.assertNotIn("props", nodes["record:scope"])
        self.assertNotIn("props", nodes["concept:scope"])
        self.assertEqual(nodes["decision:scope"]["props"]["decided_at"], "2020-01")
        warned = pack["manifest"]["warnings"]
        self.assertEqual(len(warned), 2, warned)
        self.assertIn("record:scope", " ".join(warned))
        self.assertIn("concept:scope", " ".join(warned))

    def test_untyped_and_unknown_attachments_are_left_out_and_reported(self):
        rid = pkns.key("user:alice", "record:scope")
        # 병합 경로(api/query.py)는 타입 없는 ATTACHES_TO 를 실제로 쓴다.
        self.st.run("MATCH (r:Record), (c:Concept) WHERE r.record_id=$r AND c.concept_id=$c "
                    "MERGE (r)-[:ATTACHES_TO]->(c)",
                    {"r": rid, "c": pkns.key("user:alice", "concept:scope")})
        self.st.run("MATCH (r:Record)-[a:ATTACHES_TO]->(d:Decision) "
                    "WHERE r.record_id=$id SET a.type='unknown'", {"id": rid})
        pack = self.pack(domain="vinc/share")
        self.assertCountEqual(pack["fragment"]["edges"], [self.edges[0], self.edges[2]])
        warned = pack["manifest"]["warnings"]
        self.assertEqual(len(warned), 2, warned)
        self.assertTrue(all(w.startswith("ATTACHES_TO record:scope -> ") for w in warned), warned)

    def test_legacy_unowned_chunk_still_carries_the_ego_path(self):
        from api import docstore as ds
        share.frag.merge_fragment({"owner": "user:alice", "nodes": [
            {"id": "concept:legacy-a", "type": "concept", "title": "Legacy A", "domain": "vinc/legacy-left"},
            {"id": "concept:legacy-b", "type": "concept", "title": "Legacy B", "domain": "vinc/legacy-right"}],
            "edges": []}, store=self.st)
        doc = ds.put_doc(self.st, owner="user:alice", title="Legacy", text="Old text.")["doc_id"]
        for nid in ("concept:legacy-a", "concept:legacy-b"):
            self.st.run("MATCH (c:Chunk), (n:Concept) WHERE c.doc_id=$doc AND n.concept_id=$id "
                        "MERGE (c)-[:MENTIONS]->(n)", {"doc": doc, "id": pkns.key("user:alice", nid)})
        # v4 이전 청크는 owner_id 가 '' 다.
        self.st.run("MATCH (c:Chunk) WHERE c.doc_id=$doc SET c.owner_id=''", {"doc": doc})
        reached = {n["id"] for n in self.pack(seed="concept:legacy-a", depth=2)["fragment"]["nodes"]}
        self.assertEqual(reached, {"concept:legacy-a", "concept:legacy-b"})
        # 남의 청크는 여전히 경로가 되지 못한다.
        self.st.run("MATCH (c:Chunk) WHERE c.doc_id=$doc SET c.owner_id='team:other'", {"doc": doc})
        reached = {n["id"] for n in self.pack(seed="concept:legacy-a", depth=2)["fragment"]["nodes"]}
        self.assertEqual(reached, {"concept:legacy-a"})

    def test_ego_never_walks_through_another_owners_nodes(self):
        # 오염된 교차 소유 엣지로 alice 의 먼 노드에 닿는 경로를 만든다.
        # 매 홉의 소유자 검사가 빠지면 hop-b·hop-c 가 팩에 들어온다.
        share.frag.merge_fragment({"owner": "user:alice", "nodes": [
            {"id": f"concept:hop-{x}", "type": "concept", "title": f"Hop {x}",
             "domain": f"vinc/hop-{x}"} for x in "abc"], "edges": []}, store=self.st)
        share.frag.merge_fragment({"owner": "team:other", "nodes": [
            {"id": "concept:hop-x", "type": "concept", "title": "Other X", "domain": "vinc/hop-x"},
            {"id": "record:hop-r", "type": "record", "title": "Other R", "domain": "vinc/hop-r"}],
            "edges": []}, store=self.st)
        a, b, c = (pkns.key("user:alice", f"concept:hop-{x}") for x in "abc")
        x = pkns.key("team:other", "concept:hop-x")
        r = pkns.key("team:other", "record:hop-r")
        link = ("MATCH (s:Concept), (t:Concept) WHERE s.concept_id=$s AND t.concept_id=$t "
                "MERGE (s)-[:RELATED_TO]->(t)")
        self.st.run(link, {"s": a, "t": x})
        self.st.run(link, {"s": x, "t": b})
        attach = ("MATCH (s:Record), (t:Concept) WHERE s.record_id=$s AND t.concept_id=$t "
                  "MERGE (s)-[e:ATTACHES_TO]->(t) SET e.type='derives_from'")
        self.st.run(attach, {"s": r, "t": a})
        self.st.run(attach, {"s": r, "t": c})
        pack = self.pack(seed="concept:hop-a", depth=2)
        self.assertEqual({n["id"] for n in pack["fragment"]["nodes"]}, {"concept:hop-a"})
        self.assertEqual(pack["fragment"]["edges"], [])
        self.assertNotIn("Other", str(pack))
