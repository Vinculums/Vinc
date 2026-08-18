# -*- coding: utf-8 -*-
"""Tests for the store layer — embedded config, schema DDL, provision dry-run."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from store import config as cfgmod  # noqa: E402
from store import embedded_schema as eschema  # noqa: E402
from store import provision  # noqa: E402


class ConfigTests(unittest.TestCase):
    def test_default_backend_is_embedded(self):
        self.assertEqual(cfgmod.StoreConfig().backend, "embedded")
        self.assertEqual(cfgmod.load_config().backend, "embedded")

    def test_default_embedding_is_local_en_768(self):
        # 영어 우선·오프라인 기본(§8, D10) — API 키 불요(F5)
        self.assertEqual(cfgmod.DEFAULT_EMBEDDING_MODEL, "local-en")
        self.assertEqual(cfgmod.StoreConfig().embedding_dim, 768)

    def test_embedding_dim_by_model(self):
        self.assertEqual(cfgmod.StoreConfig(embedding_model="local-en-large").embedding_dim, 1024)
        self.assertEqual(cfgmod.StoreConfig(embedding_model="anthropic").embedding_dim, 1024)
        self.assertEqual(cfgmod.StoreConfig(embedding_model="local-en").embedding_dim, 768)

    def test_overrides_win_over_env(self):
        old = os.environ.get("MEMORY_DATA_DIR")
        os.environ["MEMORY_DATA_DIR"] = "/tmp/env-dir"
        try:
            c = cfgmod.load_config({"data_dir": "/tmp/override-dir"})
            self.assertEqual(c.data_dir, "/tmp/override-dir")
        finally:
            if old is None:
                os.environ.pop("MEMORY_DATA_DIR", None)
            else:
                os.environ["MEMORY_DATA_DIR"] = old

    def test_backend_env_override(self):
        old = os.environ.get("MEMORY_STORE_BACKEND")
        os.environ["MEMORY_STORE_BACKEND"] = "embedded"
        try:
            self.assertEqual(cfgmod.load_config().backend, "embedded")
        finally:
            if old is None:
                os.environ.pop("MEMORY_STORE_BACKEND", None)
            else:
                os.environ["MEMORY_STORE_BACKEND"] = old


class EmbeddedSchemaTests(unittest.TestCase):
    def test_node_tables_cover_engine_keys(self):
        ddl = eschema.render_embedded_ddl(dim=768)
        joined = "\n".join(ddl)
        for label in ("Episode", "Concept", "Bridge", "SkillExecution",
                      "Chunk", "LearnerPattern"):
            self.assertIn(f"CREATE NODE TABLE {label}", joined)

    def test_episode_declares_scope_and_preference_columns(self):
        ddl = "\n".join(eschema.render_embedded_ddl(dim=768))
        ep = [s for s in eschema.render_embedded_ddl(dim=768) if "TABLE Episode" in s][0]
        for col in ("episode_id", "owner_id", "tenant_id", "domain",
                    "pinned", "tags", "access_count"):
            self.assertIn(col, ep)
        self.assertIn("PRIMARY KEY(episode_id)", ep)

    def test_chunk_embedding_dim_substituted(self):
        chunk = [s for s in eschema.render_embedded_ddl(dim=768) if "TABLE Chunk" in s][0]
        self.assertIn("FLOAT[768]", chunk)
        self.assertNotIn("{dim}", chunk)

    def test_no_unsubstituted_placeholders(self):
        for s in eschema.render_embedded_ddl(dim=1024):
            self.assertNotIn("{dim}", s)


class ProvisionTests(unittest.TestCase):
    def test_dry_run_returns_ddl_statements(self):
        c = cfgmod.StoreConfig(embedding_model="local-en")
        res = provision.provision(c, dry_run=True)
        self.assertTrue(res.dry_run)
        self.assertEqual(len(res.statements), len(eschema.render_embedded_ddl(dim=768)))
        self.assertEqual(res.applied, 0)
        # 노드 테이블 선행 후 관계 테이블(FROM/TO 노드 존재 전제)
        self.assertTrue(all(s.startswith(("CREATE NODE TABLE", "CREATE REL TABLE"))
                            for s in res.statements))

    def test_relation_tables_declared(self):
        ddl = "\n".join(eschema.render_embedded_ddl(dim=768))
        # concepts(Concept↔Concept 3종) · bridges(CROSSES/CONNECTS)
        # E-G6 — CC 4종 + ABOUT 에 evidence/confidence 선택 열 부여(관계 출처 추적).
        # v12 — 의미층(Concept|Decision)이 서로 바꿔 설 수 있어 쌍이 4개로 편다.
        self.assertIn("CREATE REL TABLE PREREQUISITE_OF(FROM Concept TO Concept, "
                      "FROM Concept TO Decision, FROM Decision TO Concept, "
                      "FROM Decision TO Decision, evidence STRING, confidence DOUBLE, "
                      "created STRING)", ddl)   # v14 — 엣지 출생
        # …단 RELATED_TO 는 예외로 개념↔개념 — Louvain 이 단일 노드 테이블만 받는다.
        self.assertIn("CREATE REL TABLE RELATED_TO(FROM Concept TO Concept, "
                      "evidence STRING, confidence DOUBLE, created STRING)", ddl)
        self.assertIn("CREATE REL TABLE CROSSES(FROM Bridge TO Domain)", ddl)
        # v14 — 출생은 **저작된 관계에만**. CROSSES(브릿지 정의의 구조적
        # 일부)·IN_DOMAIN(domain 열 파생)·MENTIONS(문서 재링크 파생)는 안 받는다:
        # 파생물의 출생은 "마지막으로 다시 만든 때"라 뜻이 없다.
        for derived in ("CROSSES", "IN_DOMAIN", "MENTIONS", "PARENT_OF"):
            line = next(x for x in ddl.splitlines()
                        if x.startswith(f"CREATE REL TABLE {derived}("))
            self.assertNotIn("created STRING", line, derived)
        self.assertIn("CONNECTS(FROM Bridge TO Concept, FROM Bridge TO Decision, "
                      "role STRING, created STRING)", ddl)
        # F4 — episodic↔semantic 다리(+ E-G6 증거·신뢰도). v12 로 출발점에 Decision 추가.
        self.assertIn("CREATE REL TABLE ABOUT(FROM Episode TO Concept, "
                      "FROM Episode TO Decision, FROM Decision TO Concept, "
                      "FROM Decision TO Decision, evidence STRING, confidence DOUBLE, "
                      "created STRING)", ddl)
        self.assertIn("CREATE NODE TABLE Decision(decision_id STRING", ddl)
        # S4 확장 — 엣지 삭제 표식(메타, 부팅 시 멱등 생성)
        self.assertIn("CREATE NODE TABLE _EdgeTombstone(ekey STRING", ddl)
        # v11 도메인 계층(E-1). IN_DOMAIN 은 **한 테이블에 세 쌍** — 라벨을 셋으로
        # 쪼개지 않는다(ladybug 0.18.0 다중 FROM/TO 지원, 2026-08-09 실엔진 확인).
        self.assertIn("CREATE REL TABLE PARENT_OF(FROM Domain TO Domain)", ddl)
        self.assertIn("CREATE REL TABLE IN_DOMAIN(FROM Concept TO Domain, "
                      "FROM Record TO Domain, FROM Episode TO Domain, "
                      "FROM Decision TO Domain)", ddl)

    def test_multi_pair_rel_specs_expand_per_pair(self):
        """다중 쌍 테이블은 이관 명세에서 **쌍마다** 한 항목으로 펴진다.

        label 이 유일 키였다면 dict 로 접히며 두 쌍이 조용히 사라진다 — 논리 이관
        (store/migrate.py)이 그 라벨의 엣지를 0건 MATCH 로 유실하는 형태다."""
        specs = eschema.rel_table_specs()
        pairs = {(s["from"], s["to"]) for s in specs if s["label"] == "IN_DOMAIN"}
        self.assertEqual(pairs, {("Concept", "Domain"), ("Record", "Domain"),
                                 ("Episode", "Domain"), ("Decision", "Domain")})
        # 단일 쌍 라벨은 그대로 한 항목
        self.assertEqual(1, len([s for s in specs if s["label"] == "CROSSES"]))

    def test_louvain_projected_rel_stays_on_one_node_table(self):
        """`communities()` 가 투영하는 rel 은 **노드 테이블 하나**에만 걸쳐야 한다.

        엔진이 그렇게 요구한다(실측 2026-08-09):
            Runtime exception: Louvain only supports operations on one node table.
        그리고 `project_graph` 는 투영한 rel 의 끝점 테이블을 전부 투영하라고 하므로,
        이 rel 에 끝점 라벨을 하나만 더 붙여도 **커뮤니티 기능이 통째로 죽는다**.
        v12 작업 중 실제로 그렇게 죽였다가 되돌렸다 — 그래서 여기 가드를 둔다.

        우회로는 없다(전부 실측 기각): 필터 dict 형식도 끝점 요구를 그대로 받고,
        `project_graph_cypher` 로 만든 그래프는 WCC 조차 Binder 로 거절한다."""
        rel = eschema._LOUVAIN_PROJECTED_REL
        labels = {lbl for s in eschema.rel_table_specs() if s["label"] == rel
                  for lbl in (s["from"], s["to"])}
        self.assertEqual(labels, {"Concept"},
                         f"{rel} 이 노드 테이블 {sorted(labels)} 에 걸쳐 있다 — "
                         "Louvain 이 죽으므로 communities 투영을 함께 고쳐야 한다")

    def test_bridge_owner_column_and_v2_migration(self):
        # F5 — Bridge 소유 스탬프: 신규 설치는 base DDL, 구 설치는 v2 ALTER(백필 '').
        ddl = "\n".join(eschema.render_embedded_ddl(dim=768))
        bridge_ddl = next(s for s in ddl.splitlines() if s.startswith("CREATE NODE TABLE Bridge"))
        self.assertIn("owner_id STRING", bridge_ddl)
        self.assertEqual(eschema.SCHEMA_VERSION, 14)   # v14 — 엣지 출생
        # v10 은 ALTER 가 아니라 **신규 메타 테이블**이라 base DDL 이 만든다.
        self.assertIn("CREATE NODE TABLE _MergeRejection(pair_key STRING", ddl)
        # v11 도 같은 형태 — 신규 rel 테이블이라 ALTER 스텝이 없다.
        # v12 는 **다르다**: 기존 rel 테이블에 끝점 쌍을 여는 것이라 ALTER 가 필요하다.
        # 부팅 DDL 의 CREATE 는 구 설치에서 'already exists' 로 흡수돼 쌍을 안 열어 준다
        # — 그래서 여기가 비면 구 스토어에서 결정 엣지가 전부 Binder 로 죽는다.
        # ⚠ `migration_statements(n)` 은 n **이후 전부**를 낸다. 한 버전의 몫만 보려면
        # 차집합을 써야 한다 — 그냥 (11)을 검사하면 뒤에 붙는 버전이 생길 때마다
        # 이 단언이 깨진다(v14 에서 실제로 깨졌다).
        def only(v):
            later = set(eschema.migration_statements(v))
            return [s for s in eschema.migration_statements(v - 1) if s not in later]

        v12 = only(12)
        self.assertIn("ALTER TABLE ABOUT ADD FROM Decision TO Concept", v12)
        self.assertIn("ALTER TABLE MENTIONS ADD FROM Chunk TO Decision", v12)
        self.assertIn("ALTER TABLE IN_DOMAIN ADD FROM Decision TO Domain", v12)
        self.assertTrue(all("Decision" in s for s in v12),
                        "v12 스텝에 결정과 무관한 문이 섞였다")
        # v13 은 DDL 스텝이 없다 — 쌍은 v12 가 이미 열었고, 이 버전이 하는 일은
        # DOMAIN_HIERARCHY_VERSION 을 넘겨 **계층 백필을 다시 돌리는 것**이다.
        # 그래서 그 상수가 SCHEMA_VERSION 을 따라와야 의미가 있다(안 따라오면
        # 결정이 `domain` 열만 갖고 엣지를 못 가진 채 남는다 — 실제로 그랬다).
        self.assertEqual([], only(13))
        # v14 — 엣지 출생. 저작된 관계 전부에 붙고 파생 관계에는 안 붙는다.
        v14 = only(14)
        self.assertTrue(v14 and all(s.startswith("ALTER TABLE ")
                                    and s.endswith("ADD created STRING DEFAULT ''")
                                    for s in v14), v14)
        self.assertEqual({s.split()[2] for s in v14}, set(eschema.EDGE_BIRTH_RELS))
        # ⚠ 여기 `==` 였는데 그건 v13 에 특유한 사정이었다(그 버전이 하는 일이
        # 백필 재실행 자체였다). 계층과 무관한 스키마 변경이 오면 둘은 **갈리는 것이
        # 맞다** — 같게 묶으면 모든 업그레이드가 계층 백필을 다시 돌린다.
        # 진짜 불변식은 "계층 파생 규칙을 고쳤으면 이 상수를 올린다"이고, 그것은
        # 상수 비교로 못 잡는다. 여기서는 **뒤처지지 않는지**만 본다.
        self.assertLessEqual(eschema.DOMAIN_HIERARCHY_VERSION, eschema.SCHEMA_VERSION)
        self.assertIn("ALTER TABLE Bridge ADD owner_id STRING DEFAULT ''",
                      eschema.migration_statements(1))
        # 0→7 은 v1~v7 전 스텝 포함(…·Chunk SSoT·doc sync·needs_embed·시간축)
        v0 = eschema.migration_statements(0)
        self.assertIn("ALTER TABLE Bridge ADD owner_id STRING DEFAULT ''", v0)
        self.assertTrue(any("ADD updated_at" in s for s in v0))
        self.assertTrue(any("ADD evidence STRING" in s for s in v0))     # v3
        self.assertTrue(any("ADD confidence DOUBLE" in s for s in v0))   # v3
        self.assertIn("ALTER TABLE Chunk ADD owner_id STRING DEFAULT ''", v0)   # v4
        self.assertIn("ALTER TABLE Chunk ADD char_start INT64 DEFAULT -1", v0)  # v4
        self.assertIn("ALTER TABLE _DocMeta ADD sync_seq INT64 DEFAULT 0", v0)  # v5
        self.assertIn("ALTER TABLE _DocMeta ADD needs_embed INT64 DEFAULT 0", v0)  # v6
        self.assertIn("ALTER TABLE _DocMeta ADD created_at STRING DEFAULT ''", v0)  # v7
        self.assertIn("ALTER TABLE Concept ADD content_time STRING DEFAULT ''", v0)  # v7
        # v3→7 = Chunk SSoT(v4) + _DocMeta sync_seq(v5)·needs_embed(v6)·시간축(v7)
        v3 = eschema.migration_statements(3)
        self.assertTrue(any("Chunk" in s for s in v3))
        self.assertTrue(any("_DocMeta" in s for s in v3))
        v5 = eschema.migration_statements(5)
        self.assertTrue(any("needs_embed" in s for s in v5))
        # v7 은 시간축 네 열만(구 행 백필 없음 — 없는 시간을 지어내지 않는다).
        # v7 단독 스텝만 세려면 그 다음 버전을 빼야 한다 — 그냥 len(v6) 을 박으면
        # 버전을 올릴 때마다(v12 가 19개를 더했다) 무관하게 깨진다.
        v7_only = [s for s in eschema.migration_statements(6)
                   if s not in set(eschema.migration_statements(7))]
        self.assertEqual(len(v7_only), 4)
        self.assertTrue(all("DEFAULT ''" in s for s in v7_only))
        # v8·v9 는 **DDL 이 없다**. v8 은 신규 테이블(매 부팅 멱등 DDL 이 만든다)이고
        # v9 는 데이터 스텝(Episode props.date → 열)이라 ALTER 로 표현되지 않는다 —
        # 그래서 여기가 빈 것이 곧 "v9 에 할 일이 없다"는 뜻은 아니다. 그 스텝의
        # 게이트는 EPISODE_TIME_BACKFILL_VERSION 이고 계약은 tests/test_episode_date.py.
        # (v6 스텝과 같은 이유로 **그 구간만** 뺀다 — 뒤 버전이 늘어도 안 깨지게.)
        self.assertEqual([s for s in eschema.migration_statements(7)
                          if s not in set(eschema.migration_statements(9))], [])
        self.assertEqual(eschema.EPISODE_TIME_BACKFILL_VERSION, 9)

    def test_docmeta_table_and_chunk_ssot_columns(self):
        # D45 — Chunk=SSoT: 신규 설치 DDL 에 소유·오프셋 열과 _DocMeta(본문 없는
        # 문서 정체성 레지스트리)가 포함되고, Chunk 가 purge 대상에 편입된다.
        ddl = "\n".join(eschema.render_embedded_ddl(dim=768))
        chunk_ddl = next(s for s in ddl.splitlines() if s.startswith("CREATE NODE TABLE Chunk"))
        for col in ("owner_id STRING", "char_start INT64", "char_end INT64"):
            self.assertIn(col, chunk_ddl)
        self.assertIn("CREATE NODE TABLE _DocMeta(doc_id STRING", ddl)
        self.assertIn("content_hash STRING", ddl)
        self.assertIn("Chunk", eschema.owner_scoped_labels())

    def test_plan_matches_dim(self):
        c = cfgmod.StoreConfig(embedding_model="local-en-large")  # 1024
        stmts = provision.plan(c)
        self.assertTrue(any("FLOAT[1024]" in s for s in stmts))


if __name__ == "__main__":
    unittest.main()
