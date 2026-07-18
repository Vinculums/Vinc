# -*- coding: utf-8 -*-
"""Embedded (LadybugDB/Kùzu) 스키마 렌더러 — 설계 §5 (schema-free LPG → 테이블 선행).

Neo4j 는 SET 이 임의 속성을 만들 수 있으나 임베디드 백엔드는 **열을 먼저 선언**해야
한다(§5-2). 각 엔진의 실제 MERGE/SET 전수 조사에서 도출한 열 명세가 여기 SSoT 다
(투기적 열 금지 — 엔진이 쓰는 것만).

**범위: 메모리 데이터플레인만**(발주자 결정). weg 플랫폼 PM 정책 그래프
(graph_to_neo4j: Policy/Screen/Reference/Gate/Procedure/Memory + 동적 관계)는 Vinc
범위 밖 — 미선언. Chunk 의 HAS_CHUNK 도 Policy/Reference(PM) 대상이라 제외(Chunk 는
벡터/FTS 검색용 독립 노드).

노드 6엔진: recall(Episode)·concepts(Concept)·bridges(Bridge·Domain)·
skill_tracker(SkillExecution)·learner(LearnerPattern)·embed(Chunk).
"""
from __future__ import annotations

# 노드 테이블 열 명세 (label → {pk, cols:[(col,type)]}).
# type: STRING · INT64 · DOUBLE · BOOL · STRING[] · FLOAT[{dim}]. 날짜는 STRING.
_NODE_TABLES: dict[str, dict] = {
    # recall_ingest.merge_episode + preference(pin/tag/touch) — §5-2 전체
    "Episode": {"pk": "episode_id", "cols": [
        ("episode_id", "STRING"), ("title", "STRING"), ("date", "STRING"),
        ("owner_id", "STRING"), ("tenant_id", "STRING"), ("domain", "STRING"),
        ("workspace", "STRING"), ("mode", "STRING"), ("summary", "STRING"),
        # completed_items·patterns 는 항목 **목록**(STRING[]). 원본 completed:BOOL 은
        # 소스·소비처 부재한 감사 오류로 판정, completed_items 로 개명·재타입(§5-2).
        ("completed_items", "STRING[]"), ("patterns", "STRING[]"), ("source_file", "STRING"),
        ("pinned", "BOOL"), ("tags", "STRING[]"),
        ("access_count", "INT64"), ("last_access", "STRING"), ("props", "STRING")]},
    # concepts_ingest (+ props: MCP fragment 자유 속성 — JSON 직렬화 문자열, D11 §5)
    "Concept": {"pk": "concept_id", "cols": [
        ("concept_id", "STRING"), ("stem", "STRING"), ("owner_id", "STRING"),
        ("domain", "STRING"), ("topic", "STRING"), ("title", "STRING"),
        ("one_liner", "STRING"), ("status", "STRING"), ("created", "STRING"),
        ("tags", "STRING[]"), ("source_file", "STRING"), ("props", "STRING")]},
    # bridges_ingest (+ owner_id v2 — F5: 무소유 구조 노드였던 Bridge 를 소유
    # 스코프에 편입. '' = v2 마이그레이션 이전 레거시 행(신뢰할 소유 원천 부재 —
    # 호스티드는 다중 테넌트 혼재 — 라 백필하지 않는다; 로컬은 bridges_ingest
    # 재실행이 자연 치유). owner_scoped_labels() 파생으로 purge 자동 커버.)
    "Bridge": {"pk": "bridge_id", "cols": [
        ("bridge_id", "STRING"), ("owner_id", "STRING"), ("title", "STRING"),
        ("status", "STRING"), ("created", "STRING"), ("source_domain", "STRING"),
        ("target_domain", "STRING"), ("source_file", "STRING"), ("props", "STRING")]},
    "Domain": {"pk": "name", "cols": [("name", "STRING")]},
    # MCP fragment 의 record 노드(D11 §5 — 적재 단위 일반 레코드). 엔진 소비처 없음,
    # 쓰기 경로는 api/fragment.merge_fragment 단일 지점.
    "Record": {"pk": "record_id", "cols": [
        ("record_id", "STRING"), ("title", "STRING"), ("owner_id", "STRING"),
        ("tenant_id", "STRING"), ("domain", "STRING"), ("tags", "STRING[]"),
        ("created", "STRING"), ("props", "STRING")]},
    # skill_tracker
    "SkillExecution": {"pk": "exec_id", "cols": [
        ("exec_id", "STRING"), ("skill", "STRING"), ("workspace", "STRING"),
        ("mode", "STRING"), ("outcome", "STRING"), ("duration_min", "DOUBLE"),
        ("date", "STRING"), ("timestamp", "STRING")]},
    # learner_ingest
    "LearnerPattern": {"pk": "pattern_id", "cols": [
        ("pattern_id", "STRING"), ("owner_id", "STRING"), ("code", "STRING"),
        ("name", "STRING"), ("description", "STRING"), ("domain", "STRING"),
        # observations 는 관측 **횟수(count)** 다 — learner 파서가 표의 n 셀을
        # int 로 뽑고 activate 가 `n={}` 로 표시(§5-2 감사정정: 리스트 아님).
        ("observations", "INT64")]},
    # embed_pipeline — 벡터 차원은 provision 시 주입({dim}). HAS_CHUNK(→PM) 제외.
    "Chunk": {"pk": "chunk_id", "cols": [
        ("chunk_id", "STRING"), ("source_file", "STRING"), ("doc_id", "STRING"),
        ("section_title", "STRING"), ("chunk_index", "INT64"), ("layer", "STRING"),
        ("char_count", "INT64"), ("token_count", "INT64"), ("text", "STRING"),
        ("embedding", "FLOAT[{dim}]")]},
    # 범용 캐리어 — **사용자 저작 타입**의 노드 인스턴스 집(D11-R2 · engines/
    # fragment_ingest). 레거시 4타입(concept/domain/bridge/episode)은 전용 테이블
    # 유지; 어휘 밖 신규 타입은 여기로 exact-id MERGE 된다. `type` 은 theme 어휘가
    # SSoT(여기 저장은 값). `theme_id` 는 노드 소유 = 주제 귀속(THEME-DEEP §6)을
    # 스키마에 각인. (구 D11 §5 의 Record/props 경로는 공존 — api/fragment.py.)
    "Entity": {"pk": "entity_id", "cols": [
        ("entity_id", "STRING"), ("type", "STRING"), ("title", "STRING"),
        ("owner_id", "STRING"), ("theme_id", "STRING"), ("tags", "STRING[]")]},
}

# ── vinc_sync 추적열 (S0) ─────────────────────────────────────────────────────
# 동기화 대상 콘텐츠 라벨에 공통 부여. 마이그레이션 ALTER 도 이 명세에서 생성(DRY).
#   updated_at: HLC 문자열(스토어 내 단조) — 같은 필드 충돌의 타이브레이크.
#   rev       : 인과적 백업 순서(updated_at 동률 시).
#   sync_seq  : owner별 단조 카운터 — pull delta(since) 커서.
# default 는 ALTER 백필용(Kùzu 0.11.3 ADD+DEFAULT 로 기존 행 채움, 실측 확인).
_TRACK_COLS: list[tuple[str, str, str]] = [   # (col, type, default-literal)
    ("updated_at", "STRING", "''"),
    ("rev", "INT64", "0"),
    ("sync_seq", "INT64", "0"),
]
# owner 스코프 + 사용자 저작 콘텐츠(=동기화 단위). Domain 은 bare 구조 노드(필드 없음),
# Chunk/SkillExecution/LearnerPattern 은 동기화 제외(문서임베딩·로그·파생) → 추적열 없음.
_TRACKED_LABELS: tuple[str, ...] = ("Concept", "Episode", "Record", "Bridge", "Entity")

for _lbl in _TRACKED_LABELS:
    _NODE_TABLES[_lbl]["cols"] = _NODE_TABLES[_lbl]["cols"] + [(c, t) for c, t, _ in _TRACK_COLS]

# ── 동기화 메타 테이블 (S0, `_`-접두 = 데이터플레인과 구분) ──────────────────────
# _SchemaMeta : schema_version 등 스키마 메타(로컬·원격 공통).
# _SyncMeta   : 커서·선택도메인·owner 매핑·sync_running·last_hlc (대부분 로컬 전용).
# _SyncBase   : 3-way 병합 기준본 — 노드당 마지막 동기화 시 필드 스냅샷(로컬 전용).
# _Tombstone  : 삭제 표식 — 하드삭제로 못 하는 삭제 전파용.
_META_TABLES: dict[str, dict] = {
    "_SchemaMeta": {"pk": "key", "cols": [("key", "STRING"), ("value", "STRING")]},
    "_SyncMeta": {"pk": "key", "cols": [("key", "STRING"), ("value", "STRING")]},
    "_SyncBase": {"pk": "node_id", "cols": [
        ("node_id", "STRING"), ("label", "STRING"), ("fields", "STRING"),
        ("base_rev", "INT64"), ("base_updated_at", "STRING")]},
    "_Tombstone": {"pk": "node_id", "cols": [
        ("node_id", "STRING"), ("label", "STRING"), ("owner_id", "STRING"),
        ("deleted_at", "STRING"), ("rev", "INT64"), ("sync_seq", "INT64"),
        ("origin", "STRING")]},
}

# owner_id 열을 가진 노드 라벨 — 소유 스코프 삭제/purge(계정 삭제 캐스케이드)의 SSoT.
# _NODE_TABLES 에서 파생하므로 새 라벨에 owner_id 가 붙으면 자동 포함된다(v2 에서
# Bridge 소유 스탬프가 들어와 purge 가 즉시 커버 — 파생 설계의 실증). Domain/Chunk/
# SkillExecution 은 owner_id 가 없어(구조·파생 노드) 여기 안 든다 — 소유로 못 가른다.
def owner_scoped_labels() -> list[str]:
    """owner_id 열을 선언한 노드 라벨(선언 순서). 소유 스코프 purge 의 대상 집합."""
    return [lbl for lbl, spec in _NODE_TABLES.items()
            if any(col == "owner_id" for col, _ in spec["cols"])]


# 현재 스키마 목표 버전. 올릴 때마다 migration_statements 에 스텝 추가.
SCHEMA_VERSION = 2


def migration_statements(from_version: int) -> list[str]:
    """`from_version`(현재 기록된 버전) 이후 SCHEMA_VERSION 까지 적용할 ALTER 문.

    각 문은 **멱등-흡수 전제**(migrate 가 'already' 계열 오류 무시): 신규 설치는
    render_embedded_ddl 이 이미 추적열 포함 테이블을 만들어 여기 ALTER 가 no-op 이고,
    구 설치는 기존 테이블에 열을 추가(ADD+DEFAULT 백필)한다. 둘 다 같은 최종형으로 수렴."""
    stmts: list[str] = []
    if from_version < 1:   # v1 — 추적열 도입
        for lbl in _TRACKED_LABELS:
            for col, typ, default in _TRACK_COLS:
                stmts.append(f"ALTER TABLE {lbl} ADD {col} {typ} DEFAULT {default}")
    if from_version < 2:   # v2 — Bridge 소유 스탬프 (F5). '' = 레거시 미소유:
        # 기존 행의 신뢰할 소유 원천이 없어(호스티드는 다중 테넌트 혼재) 백필하지
        # 않는다. 조회측은 과도기 술어(owner OR '')로 좁히고(query._bridge_owner_pred),
        # 로컬은 bridges_ingest 재실행으로 실소유 백필된다.
        stmts.append("ALTER TABLE Bridge ADD owner_id STRING DEFAULT ''")
    return stmts

# 관계 테이블 (label, FROM, TO, [(prop,type)]). 엔진 MERGE 실측:
#   concepts: (Concept)-[:PREREQUISITE_OF|DERIVES_FROM|RELATED_TO]->(Concept)
#   bridges:  (Bridge)-[:CROSSES]->(Domain) · (Bridge)-[:CONNECTS {role}]->(Concept)
_REL_TABLES: list[tuple] = [
    ("PREREQUISITE_OF", "Concept", "Concept", []),
    ("DERIVES_FROM", "Concept", "Concept", []),
    ("RELATED_TO", "Concept", "Concept", []),
    ("CROSSES", "Bridge", "Domain", []),
    ("CONNECTS", "Bridge", "Concept", [("role", "STRING")]),
    # MCP fragment 엣지(D11 §5) 지원 —
    #   inherits_from(Concept→Concept)는 1급 관계 테이블.
    #   Record 참여 엣지는 임베디드 rel 테이블이 FROM/TO 타입 고정이므로
    #   ATTACHES_TO{type} 하나로 수용한다(type = 원 fragment 엣지 타입 보존).
    ("INHERITS_FROM", "Concept", "Concept", []),
    ("ATTACHES_TO", "Record", "Concept", [("type", "STRING")]),
    # F4 — episodic↔semantic 다리: "이 개념을 배운 에피소드". fragment 엣지 타입
    # `about`(api/fragment._EC_EDGES)이 유일 쓰기 경로. 신규 rel 테이블은 매 부팅
    # 멱등 DDL 재실행이 자동 생성하므로 마이그레이션 스텝 불요.
    ("ABOUT", "Episode", "Concept", []),
    # 범용 관계 캐리어 — 사용자 저작 엣지 타입의 집(Entity→Entity, D11-R2 §4).
    # exact (from, type, to) MERGE 로 rel-property `type` 이 매칭 키에 참여한다.
    ("TYPED", "Entity", "Entity", [("type", "STRING")]),
]


def _render_node_tables(tables: dict, *, dim: int) -> list[str]:
    out = []
    for label, spec in tables.items():
        cols = ", ".join(f"{c} {t.replace('{dim}', str(dim))}" for c, t in spec["cols"])
        out.append(f"CREATE NODE TABLE {label}({cols}, PRIMARY KEY({spec['pk']}))")
    return out


def render_node_tables(*, dim: int) -> list[str]:
    return _render_node_tables(_NODE_TABLES, dim=dim)


def render_meta_tables() -> list[str]:
    """동기화 메타 테이블 DDL(S0). 데이터플레인과 독립(관계 참조 없음)."""
    return _render_node_tables(_META_TABLES, dim=0)


def render_rel_tables() -> list[str]:
    out = []
    for label, src, dst, props in _REL_TABLES:
        cols = "".join(f", {p} {t}" for p, t in props)
        out.append(f"CREATE REL TABLE {label}(FROM {src} TO {dst}{cols})")
    return out


def render_embedded_ddl(*, dim: int) -> list[str]:
    """임베디드 백엔드 스키마 DDL(멱등 — migrate 가 'already exists' 무시).
    노드 테이블 선행 후 관계 테이블(FROM/TO 노드가 먼저 존재해야 함). 동기화 메타
    테이블은 관계 참조가 없어 노드 뒤·관계 앞 어디든 무방(노드 뒤에 붙임)."""
    return render_node_tables(dim=dim) + render_meta_tables() + render_rel_tables()
