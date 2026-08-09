# -*- coding: utf-8 -*-
"""도메인 계층(E-1) — 스키마 v11 을 **실 엔진**으로 검증.

도메인은 여태 노드의 `domain` **문자열 열**로만 있었다. `vinc/design` 의 `/` 가
계층을 뜻했지만 그건 이름 규약이라 그래프로 못 따라갔다 — `Domain` 테이블은 고아
3행뿐이고 엣지가 0건이었다(2026-08-09 실측).

v11 이 두 관계를 놓는다:
  `PARENT_OF`(Domain→Domain) · `IN_DOMAIN`(Concept|Record|Episode→Domain)

여기서 재는 것은 **DDL 문자열이 아니라 엔진의 실제 거동**이다(렌더 검사는
tests/test_store.py 소관). 특히 세 가지:

  ① 한 rel 테이블이 다중 FROM/TO 쌍을 실제로 받나 — 구 주석은 "임베디드 rel
     테이블은 FROM/TO 고정"이라 적고 있었는데 그건 kuzu 0.11.3 시절이다.
  ② 재기동이 멱등한가 — 'already exists' 흡수에 기대므로 두 번째 부팅이 죽으면
     모든 기존 설치가 못 뜬다.
  ③ 계층이 **조회에 쓸모가 있나** — 테이블이 있는 것과 `vinc` 아래 전부를 한 번에
     끌어오는 것은 다르다. 후자가 이 기능의 존재 이유다.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engines import domain_hierarchy as dh              # noqa: E402
from store import embedded_schema as eschema           # noqa: E402
from store.backend import open_store                    # noqa: E402
from store.config import load_config                    # noqa: E402
from store.embedded_backend import _import_driver       # noqa: E402

_DRIVER, _NAME = _import_driver()


class ParseTests(unittest.TestCase):
    """이름 → 사슬. 엔진 없이 도는 순수 함수라 드라이버 skip 밖에 둔다."""

    def test_chain_is_prefixes_root_first(self):
        self.assertEqual(["vinc", "vinc/design", "vinc/design/tokens"],
                         dh.parse_chain("vinc/design/tokens"))
        self.assertEqual(["vinc"], dh.parse_chain("vinc"))
        self.assertEqual([], dh.parse_chain(""))
        self.assertEqual([], dh.parse_chain(None))

    def test_normalize_folds_empty_segments_not_case(self):
        self.assertEqual("vinc/design", dh.normalize(" vinc // design "))
        self.assertEqual("vinc/design", dh.normalize("/vinc/design/"))
        # 대소문자는 접지 않는다 — 접으면 `domain` 열과 Domain 노드 이름이 갈린다.
        self.assertEqual("Vinc/Design", dh.normalize("Vinc/Design"))


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class DomainHierarchySchemaTests(unittest.TestCase):
    def setUp(self):
        self._cfg = load_config({"data_dir": tempfile.mkdtemp() + "/db"})
        self._store = None

    def tearDown(self):
        if self._store is not None:
            self._store.close()

    def _open(self):
        """같은 데이터 디렉토리를 다시 연다(재기동 시뮬레이션)."""
        if self._store is not None:
            self._store.close()
        self._store = open_store(self._cfg)
        return self._store

    def _seed(self, st):
        st.run("MERGE (:Domain {name:'vinc'})")
        st.run("MERGE (:Domain {name:'vinc/design'})")
        st.run("MATCH (p:Domain {name:'vinc'}), (c:Domain {name:'vinc/design'}) "
               "MERGE (p)-[:PARENT_OF]->(c)")
        st.run("MERGE (:Concept {concept_id:'c1', domain:'vinc/design'})")
        st.run("MERGE (:Record {record_id:'r1', domain:'vinc/design'})")
        st.run("MERGE (:Episode {episode_id:'e1', domain:'vinc'})")
        for lbl, key, val, dom in (("Concept", "concept_id", "c1", "vinc/design"),
                                   ("Record", "record_id", "r1", "vinc/design"),
                                   ("Episode", "episode_id", "e1", "vinc")):
            st.run(f"MATCH (n:{lbl} {{{key}:$k}}), (d:Domain {{name:$d}}) "
                   "MERGE (n)-[:IN_DOMAIN]->(d)", {"k": val, "d": dom})

    def test_new_install_converges_to_v11(self):
        st = self._open()
        st.provision(dim=self._cfg.embedding_dim)
        rows = st.run("MATCH (m:_SchemaMeta) WHERE m.key='schema_version' "
                      "RETURN m.value AS v")
        self.assertEqual(rows[0]["v"], str(eschema.SCHEMA_VERSION))
        tabs = {r["name"] for r in st.run("CALL SHOW_TABLES() RETURN name")}
        self.assertIn("PARENT_OF", tabs)
        self.assertIn("IN_DOMAIN", tabs)

    def test_reboot_is_idempotent(self):
        """두 번째 부팅이 죽으면 **모든 기존 설치가 못 뜬다** — 신규 rel 테이블은
        멱등 DDL('already exists' 흡수)에만 기대므로 여기서 확인한다."""
        st = self._open()
        st.provision(dim=self._cfg.embedding_dim)
        st = self._open()
        st.provision(dim=self._cfg.embedding_dim)   # 예외 없이 통과해야 한다

    def test_all_three_pairs_accept_edges(self):
        """IN_DOMAIN 은 한 테이블에 세 쌍이다. 쌍 하나라도 거부되면 그 층의 노드가
        계층에서 통째로 빠진다(테이블은 있는데 조회가 비는 무음 고장)."""
        st = self._open()
        st.provision(dim=self._cfg.embedding_dim)
        self._seed(st)
        for lbl in ("Concept", "Record", "Episode"):
            rows = st.run(f"MATCH (n:{lbl})-[:IN_DOMAIN]->(d:Domain) "
                          "RETURN d.name AS d")
            self.assertEqual(1, len(rows), f"{lbl} 의 IN_DOMAIN 이 안 걸렸다")

    def test_transitive_query_collects_subtree(self):
        """계층의 **존재 이유** — 'vinc 아래 전부'를 한 질의로 끌어오나.
        (`vinc/design` 의 노드가 `vinc` 조회에 딸려 와야 한다.)"""
        st = self._open()
        st.provision(dim=self._cfg.embedding_dim)
        self._seed(st)
        rows = st.run("MATCH (root:Domain {name:'vinc'})-[:PARENT_OF*0..3]->(d:Domain)"
                      "<-[:IN_DOMAIN]-(n) RETURN count(n) AS n")
        self.assertEqual(3, rows[0]["n"])
        # 대조: 하위 도메인만 보면 2 — 전이가 실제로 부모까지 올라간 것이지
        # 우연히 전부 한 도메인에 몰려 3이 나온 게 아니다.
        rows = st.run("MATCH (:Domain {name:'vinc/design'})<-[:IN_DOMAIN]-(n) "
                      "RETURN count(n) AS n")
        self.assertEqual(2, rows[0]["n"])

    def test_upgrade_from_v10_keeps_data(self):
        """구 설치(v10)가 올라올 때 계층 테이블만 붙고 기존 데이터는 그대로인가."""
        st = self._open()
        st.provision(dim=self._cfg.embedding_dim)
        st.run("MERGE (:Concept {concept_id:'keep', title:'Keep'})")
        st.run("MATCH (m:_SchemaMeta) WHERE m.key='schema_version' SET m.value='10'")
        st = self._open()
        st.provision(dim=self._cfg.embedding_dim)
        rows = st.run("MATCH (m:_SchemaMeta) WHERE m.key='schema_version' "
                      "RETURN m.value AS v")
        self.assertEqual(rows[0]["v"], str(eschema.SCHEMA_VERSION))
        self.assertEqual(1, len(st.run("MATCH (c:Concept {concept_id:'keep'}) RETURN 1")))

    def test_logical_migration_preserves_every_pair(self):
        """논리 이관(store/migrate.py)이 세 쌍을 다 나르나.

        `rel_table_specs()` 를 label 로 접으면 마지막 쌍만 남아 Concept·Record 의
        IN_DOMAIN 이 **오류 없이** 사라진다. 그 형태를 여기서 막는다."""
        from store import migrate
        st = self._open()
        st.provision(dim=self._cfg.embedding_dim)
        self._seed(st)
        dump = migrate.export_store(st)
        st.close()
        self._store = None
        self.assertEqual(3, len([e for e in dump["edges"] if e["label"] == "IN_DOMAIN"]))

        cfg2 = load_config({"data_dir": tempfile.mkdtemp() + "/db2"})
        dst = open_store(cfg2)
        try:
            migrate.import_store(dst, dump, dim=cfg2.embedding_dim)
            for lbl in ("Concept", "Record", "Episode"):
                rows = dst.run(f"MATCH (n:{lbl})-[:IN_DOMAIN]->(:Domain) RETURN count(n) AS n")
                self.assertEqual(1, rows[0]["n"], f"{lbl} 의 IN_DOMAIN 이 이관에서 유실됐다")
            rows = dst.run("MATCH (:Domain)-[:PARENT_OF]->(:Domain) RETURN count(*) AS n")
            self.assertEqual(1, rows[0]["n"])
        finally:
            dst.close()


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class WritePathTests(unittest.TestCase):
    """쓰기 경로가 계층을 **자동으로** 유지하는가 — 저작 없이."""

    def setUp(self):
        self._cfg = load_config({"data_dir": tempfile.mkdtemp() + "/db"})
        self._store = open_store(self._cfg)
        self._store.provision(dim=self._cfg.embedding_dim)

    def tearDown(self):
        self._store.close()

    def _ingest(self, nodes, owner="u1"):
        from api.fragment import merge_fragment
        return merge_fragment({"nodes": nodes, "edges": [], "owner": owner},
                              store=self._store)

    def _domain_of(self, node_id):
        rows = self._store.run(
            "MATCH (n:Concept {concept_id:$id})-[:IN_DOMAIN]->(d:Domain) "
            "RETURN d.name AS d", {"id": node_id})
        return [r["d"] for r in rows]

    def test_ingest_derives_hierarchy_without_authoring_edges(self):
        """엣지를 하나도 안 실었는데 계층이 생겨야 한다 — 이게 이 기능의 계약이다."""
        self._ingest([{"type": "concept", "id": "c:a", "title": "A",
                       "domain": "vinc/design"}])
        self.assertEqual(["vinc/design"], self._domain_of("c:a"))
        chain = self._store.run("MATCH (p:Domain)-[:PARENT_OF]->(c:Domain) "
                                "RETURN p.name AS p, c.name AS c")
        self.assertEqual([("vinc", "vinc/design")], [(r["p"], r["c"]) for r in chain])

    def test_moving_a_node_does_not_leave_it_in_both_domains(self):
        """도메인을 옮기면 옛 엣지가 사라져야 한다. 안 지우면 노드가 양쪽에 뜬다 —
        오류 없이 틀린 답이 나오는 형태다."""
        self._ingest([{"type": "concept", "id": "c:a", "title": "A",
                       "domain": "vinc/design"}])
        self._ingest([{"type": "concept", "id": "c:a", "title": "A",
                       "domain": "vinc/infra"}])
        self.assertEqual(["vinc/infra"], self._domain_of("c:a"))

    def test_partial_resend_without_domain_keeps_the_edge(self):
        """도메인을 안 실은 부분 재전송은 계층을 **건드리지 않는다**.

        `domain` 열은 미지정 시 보존되므로(title 과 달리 가드가 있다), 엣지만 지우면
        열과 그래프가 갈린다."""
        self._ingest([{"type": "concept", "id": "c:a", "title": "A",
                       "domain": "vinc/design"}])
        self._ingest([{"type": "concept", "id": "c:a", "title": "A rewritten"}])
        self.assertEqual(["vinc/design"], self._domain_of("c:a"))

    def test_normalized_value_lands_in_both_column_and_node(self):
        """열과 Domain 노드 이름이 같아야 한다 — 다르면 IN_DOMAIN 이 그 열의 표현이
        아니게 되고, 열 기준 집계(/domains)와 그래프 조회가 갈린다."""
        self._ingest([{"type": "concept", "id": "c:a", "title": "A",
                       "domain": " vinc // design "}])
        col = self._store.run("MATCH (n:Concept {concept_id:'c:a'}) "
                              "RETURN n.domain AS d")[0]["d"]
        self.assertEqual("vinc/design", col)
        self.assertEqual([col], self._domain_of("c:a"))

    def test_domain_rename_moves_the_edge_too(self):
        """`/domains` 이름변경이 열만 바꾸고 엣지를 두고 가면 안 된다."""
        from api.query import rename_domain
        self._ingest([{"type": "concept", "id": "c:a", "title": "A",
                       "domain": "vinc/design"}])
        res = rename_domain(old="vinc/design", new="vinc/brand", owner="u1",
                            store=self._store)
        self.assertTrue(res["executed"])
        self.assertEqual(1, res["moved"])
        self.assertEqual(["vinc/brand"], self._domain_of("c:a"))

    def test_domain_rename_to_empty_clears_the_edge(self):
        """빈 문자열 = 미분류로 내림. 엣지도 같이 사라져야 한다."""
        from api.query import rename_domain
        self._ingest([{"type": "concept", "id": "c:a", "title": "A",
                       "domain": "vinc/design"}])
        rename_domain(old="vinc/design", new="", owner="u1", store=self._store)
        self.assertEqual([], self._domain_of("c:a"))

    def test_subtree_query_is_owner_scoped(self):
        """⚠ 도메인 노드는 **전역 공유**다. 자손을 소유로 안 거르면 남이 만든 하위
        도메인의 노드가 딸려 온다 — 계층을 사용자에게 여는 순간 유출이 된다."""
        self._ingest([{"type": "concept", "id": "c:mine", "title": "Mine",
                       "domain": "vinc/design"}], owner="u1")
        self._ingest([{"type": "concept", "id": "c:theirs", "title": "Theirs",
                       "domain": "vinc/secret"}], owner="u2")
        rows = self._store.run(dh.subtree_scoped_cypher("Concept"),
                               {"domain": "vinc", "owner": "u1"})
        self.assertEqual(["c:mine"], [r["id"] for r in rows])
        # 대조: 소유 술어 없이 같은 경로를 타면 둘 다 나온다 — 위 결과가 우연이
        # 아니라 술어가 실제로 거른 것임을 보인다.
        rows = self._store.run(
            "MATCH (:Domain {name:'vinc'})-[:PARENT_OF*0..8]->(d:Domain)"
            "<-[:IN_DOMAIN]-(n:Concept) RETURN n.concept_id AS id")
        self.assertEqual({"c:mine", "c:theirs"}, {r["id"] for r in rows})


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class DomainAddressTests(unittest.TestCase):
    """`domain:` 이 **주소**로 잡히는가 — 조회는 열리고 삭제는 막혀야 한다."""

    def setUp(self):
        self._cfg = load_config({"data_dir": tempfile.mkdtemp() + "/db"})
        self._store = open_store(self._cfg)
        self._store.provision(dim=self._cfg.embedding_dim)
        from api.fragment import merge_fragment
        merge_fragment({"owner": "u1", "edges": [], "nodes": [
            {"type": "concept", "id": "c:tok", "title": "Tokens",
             "domain": "vinc/design/tokens"},
            {"type": "record", "id": "r:d", "title": "Rec", "domain": "vinc/design"},
            {"type": "episode", "id": "e:i", "title": "Ep", "domain": "vinc/infra"},
        ]}, store=self._store)
        merge_fragment({"owner": "u2", "edges": [], "nodes": [
            {"type": "concept", "id": "c:x", "title": "Secret",
             "domain": "vinc/secret"},
        ]}, store=self._store)

    def tearDown(self):
        self._store.close()

    def _n(self, node, owner="u1"):
        from api.query import neighbors
        return neighbors(node, owner=owner, store=self._store)

    def test_walking_down_from_a_domain(self):
        res = self._n("domain:vinc")
        self.assertTrue(res["executed"])
        kids = [r["id"] for r in res["neighbors"] if r["rel"] == "PARENT_OF"]
        # design 은 손자(tokens)에만 노드가 있어도 보여야 하고, infra 는 직속이라 보인다.
        self.assertEqual(["domain:vinc/design", "domain:vinc/infra"], kids)
        # 남의 하위 도메인은 안 보인다 — 이게 fail 하면 그 자체로 유출이다.
        self.assertNotIn("domain:vinc/secret", kids)

    def test_members_are_the_nodes_directly_in_that_domain(self):
        res = self._n("domain:vinc/design")
        got = {(r["rel"], r["id"]) for r in res["neighbors"]}
        self.assertIn(("IN_DOMAIN", "r:d"), got)              # 직속 Record
        self.assertIn(("PARENT_OF", "domain:vinc/design/tokens"), got)
        self.assertNotIn(("IN_DOMAIN", "c:tok"), got)         # 손자는 직속이 아니다

    def test_owner_is_required(self):
        """무스코프 실행은 거부한다 — 자식 목록이 곧 남의 도메인 이름이다."""
        from api.query import neighbors
        res = neighbors("domain:vinc", owner=None, store=self._store)
        self.assertFalse(res.get("found"))
        self.assertIn("owner required", res.get("error", ""))
        self.assertEqual([], res["neighbors"])

    def test_unknown_domain_says_so_instead_of_looking_empty(self):
        res = self._n("domain:nope")
        self.assertTrue(res["executed"])
        self.assertFalse(res["found"])
        self.assertIn("domain", res.get("hint", "").lower())

    def test_delete_refuses_and_points_at_rename(self):
        from api.query import delete_node
        res = delete_node("domain:vinc/design", owner="u1", store=self._store)
        self.assertTrue(res["executed"])
        self.assertFalse(res["found"])
        self.assertIn("rename", res["note"])
        # 좌표가 실제로 남아 있어야 한다(거부가 말뿐이 아니라는 대조).
        self.assertEqual(1, len(self._store.run(
            "MATCH (d:Domain {name:'vinc/design'}) RETURN 1")))


@unittest.skipIf(_DRIVER is None, "임베디드 드라이버(ladybug) 미설치 — e2e skip")
class BackfillTests(unittest.TestCase):
    """v11 이행 — 열만 있고 엣지가 없는 구 노드를 채운다."""

    def setUp(self):
        self._cfg = load_config({"data_dir": tempfile.mkdtemp() + "/db"})
        self._store = open_store(self._cfg)
        self._store.provision(dim=self._cfg.embedding_dim)
        # v11 **이전** 상태를 만든다: 열만 쓰고 엣지는 안 만든다(쓰기 경로 우회).
        for lbl, pk, i in (("Concept", "concept_id", "c1"),
                           ("Record", "record_id", "r1"),
                           ("Episode", "episode_id", "e1"),
                           ("Decision", "decision_id", "dec1")):   # v13
            self._store.run(f"MERGE (n:{lbl} {{{pk}: $i}}) "
                            "SET n.domain = 'design', n.owner_id = 'u1'", {"i": i})
        self._store.run("MERGE (n:Concept {concept_id:'c2'}) "
                        "SET n.domain = '', n.owner_id = 'u1'")

    def tearDown(self):
        self._store.close()

    def _edges(self):
        return int(self._store.run(
            "MATCH ()-[r:IN_DOMAIN]->() RETURN count(r) AS c")[0]["c"])

    def test_backfill_links_existing_nodes(self):
        self.assertEqual(0, self._edges())
        res = dh.backfill(self._store)
        self.assertEqual(4, res["linked"])
        self.assertEqual(4, self._edges())
        self.assertEqual(0, res["renamed"])
        # 빈 도메인은 세지 않는다 — 미분류는 좌표가 없는 것이 맞다.
        self.assertEqual(4, res["scanned"])

    def test_every_semantic_layer_is_in_the_hierarchy(self):
        """의미층이 하나 늘 때 이 표에 안 넣으면 그 층은 `domain` **열**만 갖고
        **엣지**를 못 가진다 — `vinc_domains`(열 집계)와 `vinc_neighbors`(그래프
        조회)가 서로 다른 답을 주는 상태다. v12 의 Decision 이 실제로 그렇게 배포됐다.

        스키마 쪽 쌍(IN_DOMAIN 의 FROM 목록)과 이 표가 어긋나지 않는지 대조한다."""
        from store import embedded_schema as es
        pairs = {s["from"] for s in es.rel_table_specs() if s["label"] == "IN_DOMAIN"}
        self.assertEqual(pairs, set(dh.DOMAIN_LABELS),
                         "IN_DOMAIN 의 끝점과 백필 라벨 표가 어긋났다")

    def test_backfill_is_idempotent(self):
        dh.backfill(self._store)
        dh.backfill(self._store)
        self.assertEqual(4, self._edges())

    def test_rename_moves_column_and_edge_together(self):
        res = dh.backfill(self._store, rename={"design": "vinc/design"})
        self.assertEqual(4, res["renamed"])
        col = {r["d"] for r in self._store.run(
            "MATCH (n:Concept {concept_id:'c1'}) RETURN n.domain AS d")}
        self.assertEqual({"vinc/design"}, col)
        rows = self._store.run(dh.subtree_scoped_cypher("Concept"),
                               {"domain": "vinc", "owner": "u1"})
        self.assertEqual(["c1"], [r["id"] for r in rows])

    def test_boot_migration_runs_the_backfill(self):
        """부팅 데이터 스텝(v11)이 실제로 돈다 — 여기가 라이브의 유일한 실행 경로다.
        사이드카가 락을 쥐고 있어 CLI 로는 못 열기 때문에, 이게 안 돌면 기존 그래프는
        영영 엣지가 없다."""
        # 방금 setUp 이 스키마를 v11 로 올려놨으므로 되돌려 구 설치를 만든다.
        self._store.run("MATCH (m:_SchemaMeta) WHERE m.key='schema_version' "
                        "SET m.value = '10'")
        self._store.run("MATCH ()-[r:IN_DOMAIN]->() DELETE r")
        self.assertEqual(0, self._edges())
        res = self._store.migrate(dim=self._cfg.embedding_dim)
        self.assertEqual(4, res["domain_links_backfilled"])
        self.assertEqual(4, self._edges())
        # 두 번째 기동은 다시 돌지 않는다(버전이 기록됐다).
        res2 = self._store.migrate(dim=self._cfg.embedding_dim)
        self.assertEqual(0, res2["domain_links_backfilled"])

    def test_about_only_node_is_not_reported_as_orphan(self):
        """`ABOUT` 하나로 이어진 결정을 고아라 부르면 안 된다.

        진단의 성분 분석이 걷는 관계 목록에서 `ABOUT` 이 빠져 있어, v12 이관 직후
        결정 67건 중 43건이 고아로 보고됐다 — 전부 거짓이었다(`decision:d68` 은
        ABOUT 이웃을 갖고도 고아로 떴다, 2026-08-09 라이브 실측). 층이 늘 때마다
        **투영에는 들어오는데 그 층의 엣지는 안 걷는** 비대칭이 재발할 수 있다."""
        st = self._store
        st.run("MERGE (:Concept {concept_id:'c-target', domain:'d', owner_id:'u1'})")
        st.run("MERGE (:Decision {decision_id:'dec-linked', domain:'d', owner_id:'u1'})")
        st.run("MATCH (a:Decision {decision_id:'dec-linked'}), "
               "(b:Concept {concept_id:'c-target'}) MERGE (a)-[:ABOUT]->(b)")
        # graph_algo 는 {id, value} 로 돌려준다(value = 성분 번호).
        groups: dict = {}
        for r in st.graph_algo("weakly_connected_components") or []:
            if r.get("id"):
                groups.setdefault(r.get("value"), []).append(r["id"])
        # ⚠ 먼저 **분석이 실제로 돌았는지** 확인한다. 이 두 줄이 없으면 알고리즘이
        # 빈 결과를 줘도 아래 assertNotIn 이 그냥 통과해 테스트가 공허해진다
        # (처음 쓴 형태가 그랬고, 수정을 되돌려도 초록이었다).
        seen = {n for m in groups.values() for n in m}
        self.assertIn("dec-linked", seen, "결정이 투영에 아예 안 들어왔다")
        self.assertIn("c-target", seen)
        # 그리고 **같은 성분**이어야 한다 — ABOUT 을 안 걷으면 각자 1인 성분이 된다.
        gid = next(g for g, m in groups.items() if "dec-linked" in m)
        self.assertIn("c-target", groups[gid],
                      "ABOUT 로 이어진 결정이 개념과 다른 성분에 있다(고아 오판)")

    def test_backfill_does_not_restamp(self):
        """366 노드에 스탬프를 찍으면 모든 기기가 전량을 다시 받는다. 이행은 내용
        변경이 아니라 표현 보정이므로 rev 를 건드리지 않는다."""
        before = self._store.run(
            "MATCH (n:Concept {concept_id:'c1'}) RETURN n.rev AS rev")[0]["rev"]
        dh.backfill(self._store, rename={"design": "vinc/design"})
        after = self._store.run(
            "MATCH (n:Concept {concept_id:'c1'}) RETURN n.rev AS rev")[0]["rev"]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
