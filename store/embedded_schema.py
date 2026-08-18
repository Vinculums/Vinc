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
        ("tags", "STRING[]"), ("source_file", "STRING"), ("props", "STRING"),
        # v7(D72) — 내용 시점. `created` 는 "이 노드가 언제 생겼나"이고 이것은
        # "이 노드가 말하는 일이 언제 있었나"다. 둘은 다른 축이라 겹쳐 쓰지 않는다.
        ("content_time", "STRING")]},
    # v12 — **결정(Decision)**. 개념은 지금도 참인 것을, 결정은 그때 고른 것을 말한다.
    # 열은 라이브 실측으로 고른 것만 둔다(투기적 열 금지) — 왜 이 여섯인지는
    # [[record:decision-columns-measured-from-live-rows]], 의미층 규칙은
    # [[vinc.spec.dataplane §2.4]].
    "Decision": {"pk": "decision_id", "cols": [
        ("decision_id", "STRING"), ("title", "STRING"), ("owner_id", "STRING"),
        ("domain", "STRING"), ("tags", "STRING[]"), ("created", "STRING"),
        ("props", "STRING")]},
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
        ("created", "STRING"), ("props", "STRING"),
        ("content_time", "STRING")]},   # v7(D72) — Concept 과 같은 축
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
    # embed_pipeline + api/docstore — 벡터 차원은 provision 시 주입({dim}).
    # v4(D45): Chunk 가 원문의 **정본**이 되며 얻은 두 열 —
    #   owner_id            : 소유 스코프(격리 + 계정삭제 purge). '' = 레거시, 백필 안 함
    #   char_start/char_end : 문자 오프셋(반개구간). **무손실 복원**의 근거. -1 = 레거시
    # source_file 은 조인 키가 아니라 출처 표시다 — 문서 정체성의 정본은 `doc_id`.
    # 계약: [[vinc.canon.chunk-ssot]] · 열의 계보는 [[vinc.spec.dataplane §7]].
    "Chunk": {"pk": "chunk_id", "cols": [
        ("chunk_id", "STRING"), ("source_file", "STRING"), ("doc_id", "STRING"),
        ("section_title", "STRING"), ("chunk_index", "INT64"), ("layer", "STRING"),
        ("char_count", "INT64"), ("token_count", "INT64"), ("text", "STRING"),
        ("embedding", "FLOAT[{dim}]"),
        ("owner_id", "STRING"), ("char_start", "INT64"), ("char_end", "INT64")]},
    # v8 — **부모-자식 벡터**: 청크 하나에 벡터 여러 개. 저장 단위(청크)와 모델 입력
    # 상한(512토큰)이 달라서, 청크는 그대로 두고 벡터만 쪼갠다. `Chunk.embedding` 은
    # v8 이후 **읽지 않는다**(열은 남긴다 — 드롭이 위험하다). 벡터 인덱스는 이 테이블,
    # FTS 는 Chunk.text 그대로.
    # 왜 재청킹도 평균도 아닌지, 실측 수치: [[vinc.spec.dataplane §2]].
    "ChunkVec": {"pk": "vec_id", "cols": [
        ("vec_id", "STRING"),            # f"{chunk_id}#{seq:03d}" — 위치 안정
        ("chunk_id", "STRING"), ("doc_id", "STRING"), ("owner_id", "STRING"),
        ("seq", "INT64"), ("embedding", "FLOAT[{dim}]")]},
    # 범용 캐리어 — **사용자 저작 타입**의 노드 인스턴스 집(D11-R2). 어휘 밖 타입은
    # 여기로 exact-id MERGE 된다. `type` 은 값일 뿐이고 어휘의 정본은 theme 파일이다
    # ([[vinc.canon.vocabulary-model]] · [[vinc.spec.dataplane §2.3]]).
    "Entity": {"pk": "entity_id", "cols": [
        ("entity_id", "STRING"), ("type", "STRING"), ("title", "STRING"),
        ("owner_id", "STRING"), ("theme_id", "STRING"), ("tags", "STRING[]")]},
}

# ── vinc_sync 추적열 (S0) ─────────────────────────────────────────────────────
# 동기화 대상 콘텐츠 라벨에 공통 부여. 마이그레이션 ALTER 도 이 명세에서 생성(DRY).
#   updated_at: HLC 문자열(스토어 내 단조) — 같은 필드 충돌의 타이브레이크.
#   rev       : 인과적 백업 순서(updated_at 동률 시).
#   sync_seq  : owner별 단조 카운터 — pull delta(since) 커서.
# default 는 ALTER 백필용. 셋이 어떻게 병합에 쓰이는지는 [[vinc.spec.dataplane §6.1]].
_TRACK_COLS: list[tuple[str, str, str]] = [   # (col, type, default-literal)
    ("updated_at", "STRING", "''"),
    ("rev", "INT64", "0"),
    ("sync_seq", "INT64", "0"),
]
# owner 스코프 + 사용자 저작 콘텐츠(=동기화 단위). Domain 은 bare 구조 노드,
# Chunk/SkillExecution/LearnerPattern 은 추적열 없음. **Chunk 가 정본 텍스트인데도
# 여기 없는 것은 누락이 아니다** — 문서는 doc 단위 자기 채널로 동기화한다
# ([[vinc.spec.dataplane §2.1]] · [[vinc.canon.chunk-ssot]]).
_TRACKED_LABELS: tuple[str, ...] = ("Concept", "Episode", "Record", "Bridge", "Entity",
                                    "Decision")   # v12

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
    # 엣지 삭제 표식 — 엣지는 타임스탬프가 없어 하드삭제만으론 수신측에서 부활한다.
    # ekey = "src|REL|dst", 동기화 rel(CC 4종+ABOUT)만 의미가 있다
    # ([[vinc.spec.dataplane §6.1]]).
    "_EdgeTombstone": {"pk": "ekey", "cols": [
        ("ekey", "STRING"), ("src", "STRING"), ("rel", "STRING"), ("dst", "STRING"),
        ("owner_id", "STRING"), ("deleted_at", "STRING"), ("sync_seq", "INT64"),
        ("origin", "STRING")]},
    # 문서 정체성 레지스트리(D45). **본문은 없다** — 정본 텍스트는 Chunk.text(오프셋
    # 연결)뿐이고 여기는 doc 단위 정체성·변경감지 메타다([[vinc.canon.chunk-ssot]]):
    #   doc_id       : 내재 식별자(경로 아님) — MENTIONS·증분·묶음의 조인 키.
    #   content_hash : 정본 텍스트 sha256 — 변경감지(무변경 재적재 스킵)·복원 검증.
    #   origin       : 출처 표시(구 source_file 역할) — 조인에 쓰지 않는 참고 정보.
    "_DocMeta": {"pk": "doc_id", "cols": [
        ("doc_id", "STRING"), ("owner_id", "STRING"), ("title", "STRING"),
        ("origin", "STRING"), ("content_hash", "STRING"), ("chunk_count", "INT64"),
        # updated_at = 단조 HLC, sync_seq = owner별 delta 커서. **문서는 doc 단위로만
        # 동기화된다**(텍스트만 전송, embedding 은 수신측 재생성 —
        # [[vinc.spec.dataplane §6.2]]). needs_embed=1 이면 임베딩 없이 적재된 문서고
        # (FTS 즉시·벡터 지연) 백그라운드 재임베딩 잡이 채운다.
        ("updated_at", "STRING"), ("sync_seq", "INT64"), ("needs_embed", "INT64"),
        # v7(D72) — 시간축 이원화. `updated_at` 하나뿐이던 시절엔 문서를 고칠 때마다
        # "언제 만든 글인가"가 지워졌다.
        #   created_at   : **최초 1회만** 기록하고 편집이 보존한다(HLC).
        #   content_time : 내용이 가리키는 시점. `YYYY[-MM[-DD]]` 접두 통과분만 저장 —
        #                  비교·버킷이 문자열 사전식이라 형식이 곧 계약이다.
        # 둘 다 '' 가 합법이다(레거시·미입력) — 없으면 없다고 말한다
        # ([[vinc.spec.dataplane §7]]).
        ("created_at", "STRING"), ("content_time", "STRING")]},
    # 문서 삭제 표식 — 하드삭제만으론 상대 사본이 다음 delta 에 실려 부활한다
    # (엣지 툼스톤과 같은 병 · [[vinc.spec.dataplane §6.2]]).
    "_DocTombstone": {"pk": "doc_id", "cols": [
        ("doc_id", "STRING"), ("owner_id", "STRING"), ("deleted_at", "STRING"),
        ("sync_seq", "INT64")]},
    # v10(F10-b) — 병합 후보 **기각 기억**. 기각은 사용자 지식이 아니라 제안 엔진에
    # 대한 판정이라 그래프가 아니라 메타 테이블에 둔다(툼스톤 3종과 같은 신분).
    #   pair_key : f"{owner}|{a}|{b}" — a,b 는 **정렬**돼 (a,b)·(b,a) 가 한 행이 된다.
    #              owner 를 키에 넣지 않으면 남의 MERGE 가 이 행의 owner_id 를 덮는다.
    #   note     : 왜 기각했는지(선택).
    # ⚠ 메타 테이블은 논리 이관·동기화 대상이 아니다 — 기각은 기기 로컬로 남고 스토어
    # 이관 시 유실된다(자문 성격이라 허용). 근거: [[vinc.spec.dataplane §2.5]].
    "_MergeRejection": {"pk": "pair_key", "cols": [
        ("pair_key", "STRING"), ("owner_id", "STRING"),
        ("a_id", "STRING"), ("b_id", "STRING"),
        ("rejected_at", "STRING"), ("note", "STRING")]},
}

# owner_id 열을 가진 노드 라벨 — 소유 스코프 삭제/purge 의 SSoT. **손으로 적은 목록이
# 아니라 _NODE_TABLES 파생이다**: 새 라벨에 owner_id 가 붙으면 purge 가 자동으로
# 커버한다(v2 Bridge · v4 Chunk 가 그 실증). Domain/SkillExecution 은 owner_id 가
# 없어 소유로 못 가른다. 근거: [[vinc.spec.dataplane §2.1]] · 격리 규칙은 §5.
def owner_scoped_labels() -> list[str]:
    """owner_id 열을 선언한 노드 라벨(선언 순서). 소유 스코프 purge 의 대상 집합."""
    return [lbl for lbl, spec in _NODE_TABLES.items()
            if any(col == "owner_id" for col, _ in spec["cols"])]


# E-G6 증거·신뢰도 열을 얻는 rel 라벨(마이그레이션 ALTER 대상 — DRY).
_EVIDENCE_RELS: tuple[str, ...] = ("PREREQUISITE_OF", "DERIVES_FROM", "RELATED_TO",
                                   "INHERITS_FROM", "ABOUT")

# 현재 스키마 목표 버전. 올릴 때마다 migration_statements 에 스텝 추가.
SCHEMA_VERSION = 14

#: v9 — **데이터 마이그레이션**(DDL 아님). Episode 의 `props.date`/`props.summary` →
#: 동명 열 백필. 구현은 `store.embedded_backend.backfill_episode_time_fields`(JSON
#: 파싱이 필요해 Cypher 로는 못 쓴다). 상수가 스키마 버전 **옆에** 있는 이유는
#: [[vinc.spec.dataplane §7]] — 떨어져 있으면 다음 bump 에서 조용히 빠진다.
EPISODE_TIME_BACKFILL_VERSION = 9

#: v11 — **데이터 마이그레이션**(DDL 아님). `domain` 열 → Domain 노드·PARENT_OF·
#: IN_DOMAIN 파생. 구현은 `engines.domain_hierarchy.backfill`. 부팅에서 도는 이유는
#: v9 와 같다(사이드카가 DB 락을 쥐고 있어 별도 프로세스로는 스토어를 못 연다).
#: 멱등이라 중간에 죽어도 다음 기동이 마무리한다.
#: ⚠ **의미층이 늘 때마다 이 상수를 올린다.** v12 가 Decision 을 늘렸는데 백필의 라벨
#: 표에 안 들어가 결정 67건이 엣지 없이 배포됐고, 그래서 v13 이 존재한다. 재실행은
#: 0.1초대다(5000노드 0.14s). 계보: [[vinc.spec.dataplane §7]].
DOMAIN_HIERARCHY_VERSION = 13


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
    if from_version < 3:   # v3 — E-G6 관계 증거·신뢰도(선택 속성). rel 테이블 ALTER
        # ADD+DEFAULT(실측: kuzu 0.11.3 rel 테이블 지원). 신규 설치는 render 가 이미
        # 열 포함 rel 테이블을 만들어 no-op 흡수; 구 설치는 CC 4종+ABOUT 에 백필.
        for rel in _EVIDENCE_RELS:
            stmts.append(f"ALTER TABLE {rel} ADD evidence STRING DEFAULT ''")
            stmts.append(f"ALTER TABLE {rel} ADD confidence DOUBLE DEFAULT 0.0")
    if from_version < 4:   # v4 — Chunk=SSoT(D45): 소유 스코프 + 무손실 오프셋.
        # owner_id '' = 레거시 무소유(Bridge v2 전례 — 파일 파생물이라 소유 원천
        # 부재, 백필 안 함). char_start/end -1 = 오프셋 미기록(docstore 재적재가
        # 자연 치유). _DocMeta 는 신규 테이블 — base DDL 멱등 재실행이 생성.
        stmts.append("ALTER TABLE Chunk ADD owner_id STRING DEFAULT ''")
        stmts.append("ALTER TABLE Chunk ADD char_start INT64 DEFAULT -1")
        stmts.append("ALTER TABLE Chunk ADD char_end INT64 DEFAULT -1")
    if from_version < 5:   # v5 — 문서 단위 동기화(P3, D45): _DocMeta 에 delta 커서.
        # _DocTombstone 은 신규 테이블 — base DDL 멱등 재실행이 생성(ALTER 불요).
        stmts.append("ALTER TABLE _DocMeta ADD sync_seq INT64 DEFAULT 0")
    if from_version < 6:   # v6 — 앱 내 편집 재임베딩 표식(D45-E 후속3).
        stmts.append("ALTER TABLE _DocMeta ADD needs_embed INT64 DEFAULT 0")
    if from_version < 7:   # v7 — 시간축 이원화(D72). 기록 시각과 내용 시점을 가른다.
        # '' = 모름. 구 행에 오늘을 찍지 않는다 — 없는 시간을 지어내면 타임라인이
        # "전부 마이그레이션한 날"에 몰린다(팩 재-MERGE 가 냈던 병과 같은 뿌리).
        stmts.append("ALTER TABLE Concept ADD content_time STRING DEFAULT ''")
        stmts.append("ALTER TABLE Record ADD content_time STRING DEFAULT ''")
        stmts.append("ALTER TABLE _DocMeta ADD created_at STRING DEFAULT ''")
        stmts.append("ALTER TABLE _DocMeta ADD content_time STRING DEFAULT ''")
    # v8 — 부모-자식 벡터(ChunkVec). **ALTER 스텝이 없다**: 신규 노드 테이블은 매 부팅
    # 멱등 DDL(render_node_tables)이 만든다. 기존 Chunk.embedding 은 건드리지 않는다 —
    # 드롭이 위험하고, 남아도 읽지 않으면 해롭지 않다(ChunkVec 이 채워지기 전까지는
    # 벡터 검색이 빌 뿐이고, 그 기간은 어차피 전량 재임베딩 구간이다).
    # v9 — Episode props.date 승격(F9). **ALTER 스텝이 없다**: 열(date/summary)은 v0
    # 부터 있었고 비어 있었을 뿐이라 스키마가 아니라 **데이터**를 고쳐야 한다.
    # 그 스텝은 JSON 파싱이 필요해 Cypher 문자열로 표현할 수 없다 →
    # `embedded_backend.backfill_episode_time_fields`(EPISODE_TIME_BACKFILL_VERSION).
    # v10 — 병합 후보 기각 기억(_MergeRejection, F10-b). **ALTER 스텝이 없다**: 신규
    # 메타 테이블은 매 부팅 멱등 DDL(render_meta_tables)이 만든다(_DocTombstone·
    # ChunkVec 전례). 백필할 기존 데이터도 없다 — 기각 이력은 이 버전부터 쌓인다.
    # v11 — 도메인 계층(E-1: PARENT_OF · IN_DOMAIN). **ALTER 스텝이 없다**: 신규 rel
    # 테이블은 매 부팅 멱등 DDL(render_rel_tables)이 만든다. 다만 **백필은 필요하다** —
    # 기존 노드의 `domain` 문자열에서 엣지를 만드는 것은 DDL 이 아니라 데이터 스텝이라
    # 여기 못 쓴다(v9 Episode 백필과 같은 이유). 그 스텝은 쓰기 경로가 자연 치유하거나
    # 일괄 백필로 돈다 — 엣지가 없는 동안은 계층 조회가 빌 뿐 기존 경로는 멀쩡하다.
    if from_version < 12:  # v12 — 결정(Decision) 타입.
        # 노드 테이블은 매 부팅 멱등 DDL 이 만들지만 **관계는 다르다**: 기존 rel
        # 테이블에 끝점 쌍을 여는 것은 CREATE 가 아니라 ALTER 이고, 신규 설치용
        # CREATE 는 구 설치에서 'already exists' 로 흡수돼 쌍을 안 열어 준다.
        # 목록은 _REL_TABLES 파생 — 손으로 적으면 다음 쌍에서 빠진다.
        # 근거: [[record:rel-tables-take-multiple-endpoint-pairs]].
        for lbl, src, dst, _p in _REL_TABLES:
            srcs = (src,) if isinstance(src, str) else tuple(src)
            dsts = (dst,) if isinstance(dst, str) else tuple(dst)
            for s in srcs:
                for d in dsts:
                    if "Decision" in (s, d):
                        stmts.append(f"ALTER TABLE {lbl} ADD FROM {s} TO {d}")
    # v13 — 결정을 주제 계층에 넣는다. **DDL 스텝이 없다**: 하는 일은
    # DOMAIN_HIERARCHY_VERSION 을 넘겨 백필을 다시 돌리는 것뿐이다
    # ([[vinc.spec.dataplane §7]] — 데이터 스텝에 자기 버전 상수가 필요한 이유).
    if from_version < 14:  # v14 — 엣지 출생(`_EDGE_BIRTH_COL` 주석 참조).
        # 기존 행은 DEFAULT '' 로 채워진다 = 모름. **백필 없음**이 요지다.
        for rel in EDGE_BIRTH_RELS:
            stmts.append(f"ALTER TABLE {rel} ADD created STRING DEFAULT ''")
    return stmts

# 관계 테이블 (label, FROM, TO, [(prop,type)]). 엔진 MERGE 실측:
#   concepts: (Concept)-[:PREREQUISITE_OF|DERIVES_FROM|RELATED_TO]->(Concept)
#   bridges:  (Bridge)-[:CROSSES]->(Domain) · (Bridge)-[:CONNECTS {role}]->(Concept)
# E-G6 — 관계 증거·신뢰도(선택 속성). CC 4종 + ABOUT 에 부여해 관계 출처 추적을
# 노드(source_file)에서 엣지 수준으로 내린다. 상류가 안 보내면 기본값('', 0.0)이라
# 저작 부담을 강제하지 않는다. v3 에서 ALTER ADD([[vinc.spec.dataplane §7]]).
_EDGE_EVIDENCE_COLS: list[tuple[str, str]] = [("evidence", "STRING"), ("confidence", "DOUBLE")]

# v14 — **엣지 출생**(`created`). **저작된 관계에만** 붙인다: CROSSES·IN_DOMAIN·
# MENTIONS 는 파생물이라 출생이 "마지막으로 다시 만든 때"가 되어 뜻이 없다.
# 쓰기는 `ON CREATE SET`(재-MERGE 에서 안 덮는다) 이라 존재 확인 조회가 없고, 구
# 엣지는 **백필하지 않는다**('' = 모름. v7 과 같은 규율).
# 왜 이 열이 필요한지: [[record:edge-birth-v14]] · [[vinc.research.fourth-dimension]].
_EDGE_BIRTH_COL: tuple[str, str] = ("created", "STRING")
#: 출생 열을 갖는 관계 — DDL·마이그레이션·쓰기 경로가 **이 하나**를 읽는다.
EDGE_BIRTH_RELS: tuple[str, ...] = (
    "PREREQUISITE_OF", "DERIVES_FROM", "RELATED_TO", "INHERITS_FROM",
    "CONNECTS", "ATTACHES_TO", "ABOUT", "TYPED",
)

# v12 — **결정은 개념과 같은 층이다.** 개념이 끝점일 수 있는 자리마다 결정도 끝점일 수
# 있다. 특례를 만들면 미선언 쌍에서 경고가 아니라 **엔진 예외로 죽으므로** 사용자가
# 실행해 보고서야 알게 된다 — 어휘를 균일하게 두는 편이 싸다
# ([[vinc.spec.dataplane §2.4]]).
_SEMANTIC_LABELS: tuple[str, ...] = ("Concept", "Decision")

#: **RELATED_TO 만 예외로 개념↔개념에 남는다.** 엔진 제약이다 — `api.query.communities`
#: 가 이 rel 단일 투영으로 Louvain 을 돌리는데, Decision 을 붙이면 투영 노드 테이블이
#: 둘이 되어 **커뮤니티 기능이 통째로 죽는다**. 우회로는 없다(전부 실측으로 기각).
#: ⚠ 이 목록을 넓히려면 `communities` 의 투영도 같이 봐야 한다. 불변식은
#: tests/test_store.py 의 단일 노드테이블 가드가 지킨다.
#: 오류 문면·기각한 우회로·의미상으로도 맞는 이유: [[record:louvain-projects-one-node-table]].
_LOUVAIN_PROJECTED_REL = "RELATED_TO"

_REL_TABLES: list[tuple] = [
    ("PREREQUISITE_OF", _SEMANTIC_LABELS, _SEMANTIC_LABELS, list(_EDGE_EVIDENCE_COLS)),
    ("DERIVES_FROM", _SEMANTIC_LABELS, _SEMANTIC_LABELS, list(_EDGE_EVIDENCE_COLS)),
    ("RELATED_TO", "Concept", "Concept", list(_EDGE_EVIDENCE_COLS)),   # ↑ 예외 사유
    ("CROSSES", "Bridge", "Domain", []),
    ("CONNECTS", "Bridge", _SEMANTIC_LABELS, [("role", "STRING")]),
    # MCP fragment 엣지(D11 §5) 지원 —
    #   inherits_from(Concept→Concept)는 1급 관계 테이블.
    #   Record 참여 엣지는 ATTACHES_TO{type} 하나로 수용한다(type = 원 fragment
    #   엣지 타입 보존). ⚠ 이 통합의 근거였던 "rel 테이블은 FROM/TO 고정"은 **이제
    #   틀리다** — 그래도 되돌리지 않는다(type 열이 이미 보존한다):
    #   [[record:rel-tables-take-multiple-endpoint-pairs]].
    ("INHERITS_FROM", _SEMANTIC_LABELS, _SEMANTIC_LABELS, list(_EDGE_EVIDENCE_COLS)),
    ("ATTACHES_TO", "Record", _SEMANTIC_LABELS, [("type", "STRING")]),
    # F4 — episodic↔semantic 다리: "이 개념을 배운 에피소드". fragment 엣지 타입
    # `about`(api/fragment._EC_EDGES)이 유일 쓰기 경로. 신규 rel 테이블은 매 부팅
    # 멱등 DDL 재실행이 자동 생성하므로 마이그레이션 스텝 불요.
    # v12 — 출발점에 Decision 이 붙는다. ABOUT 은 "X 는 Y 에 관한 것"이고, 결정이
    # 어떤 개념을 두고 내려졌다는 말이 정확히 그 형태다(라이브 51건이 여태 RELATED_TO
    # 로 그 뜻을 흉내내고 있었다 — 이관이 그것을 ABOUT 으로 옮긴다).
    ("ABOUT", ("Episode", "Decision"), _SEMANTIC_LABELS, list(_EDGE_EVIDENCE_COLS)),
    # 범용 관계 캐리어 — 사용자 저작 엣지 타입의 집(Entity→Entity, D11-R2 §4).
    # exact (from, type, to) MERGE 로 rel-property `type` 이 매칭 키에 참여한다.
    ("TYPED", "Entity", "Entity", [("type", "STRING")]),
    # E-G1 — 그래프-검색 접속(이부 그래프). Chunk(벡터/FTS 검색 노드)를 그것이
    # 언급하는 Concept 에 잇는다. 생성은 LLM OpenIE 가 아니라 **결정론**(source_file
    # basename 일치 또는 title/stem 토큰 매칭 — engines/mentions_link.py). 이 다리로
    # 검색이 "질문과 유사한 청크"만이 아니라 "그 청크가 언급하는 개념의 이웃"까지
    # 후보로 올린다(리서치 ① 다중 홉 가교 해소, HippoRAG 이부 그래프의 결정론 번역).
    # method = 링크 근거('source'|'title'|'stem') — 재적재 시 드랍-리빌드 관리.
    ("MENTIONS", "Chunk", _SEMANTIC_LABELS, [("method", "STRING")]),
    # ── 도메인 계층 (v11, E-1) ────────────────────────────────────────────
    # 두 관계는 **파생물이지 저작물이 아니다.** 사용자는 `domain` 문자열만 적고 노드와
    # 엣지는 쓰기 경로가 그것에서 만든다 — 그래서 GRAPH.md 의 저작 엣지 여섯 종은
    # 그대로다. ⚠ `Domain` 은 **무소유 전역 구조**(열이 `name` 뿐)이고 소유는 엣지의
    # 출발점 노드가 진다. 그래서 소유자별 도메인 노드도, PK 이관도 필요 없다.
    # v11 이전의 상태(고아 3행·엣지 0건): [[record:domain-hierarchy-was-a-string-until-v11]].
    ("PARENT_OF", "Domain", "Domain", []),
    # 다중 FROM/TO 쌍을 **한 테이블**에 담는다 — 라벨을 넷으로 쪼갤 이유가 없다
    # ([[record:rel-tables-take-multiple-endpoint-pairs]]).
    ("IN_DOMAIN", ("Concept", "Record", "Episode", "Decision"), "Domain", []),
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
    """관계 테이블 DDL. ``src``·``dst`` 는 문자열 하나 또는 **여러 라벨의 튜플**이다.

    여러 라벨을 주면 한 테이블이 그 조합 전부를 받는다
    (``FROM A TO D, FROM B TO D``). ladybug 0.18.0 이 다중 쌍을 지원한다 —
    구 주석이 말하는 "FROM/TO 고정"은 kuzu 0.11.3 시절이다(2026-08-09 실엔진 확인).
    덕분에 `IN_DOMAIN` 을 라벨 셋으로 쪼개지 않는다."""
    out = []
    for label, src, dst, props in _REL_TABLES:
        # v14 — 출생 열은 표(EDGE_BIRTH_RELS)에서 파생한다. `_REL_TABLES` 의 props 에
        # 손으로 적으면 마이그레이션 ALTER 목록과 두 벌이 되고, 언젠가 갈린다.
        if label in EDGE_BIRTH_RELS:
            props = list(props) + [_EDGE_BIRTH_COL]
        cols = "".join(f", {p} {t}" for p, t in props)
        srcs = (src,) if isinstance(src, str) else tuple(src)
        dsts = (dst,) if isinstance(dst, str) else tuple(dst)
        pairs = ", ".join(f"FROM {s} TO {d}" for s in srcs for d in dsts)
        out.append(f"CREATE REL TABLE {label}({pairs}{cols})")
    return out


def node_table_specs() -> dict[str, dict]:
    """노드 테이블 명세(label → {pk, cols}) — 논리 이관(store/migrate.py)의 SSoT.
    데이터플레인만(meta `_`-테이블 제외). 스키마가 진실이라 이관이 열 추가에 자동 추종."""
    return {lbl: {"pk": spec["pk"], "cols": [c for c, _ in spec["cols"]]}
            for lbl, spec in _NODE_TABLES.items()}


def rel_table_specs() -> list[dict]:
    """관계 테이블 명세(label·from·to·props 열) — 논리 이관용.

    다중 FROM/TO 쌍 테이블(v11 `IN_DOMAIN`)은 **쌍마다 한 항목**으로 편다. 이관은
    끝점 라벨을 박아 MATCH 하므로 쌍이 곧 이관 단위다.

    ⚠ 그래서 `label` 은 더 이상 유일 키가 아니다. `{s["label"]: s for s in ...}` 로
    접으면 마지막 쌍만 남고 나머지가 **조용히 사라진다**(store/migrate.py 가 리스트로
    묶는 이유)."""
    out: list[dict] = []
    for lbl, src, dst, props in _REL_TABLES:
        srcs = (src,) if isinstance(src, str) else tuple(src)
        dsts = (dst,) if isinstance(dst, str) else tuple(dst)
        cols = [p for p, _ in props]
        for s in srcs:
            for d in dsts:
                out.append({"label": lbl, "from": s, "to": d, "props": list(cols)})
    return out


def render_embedded_ddl(*, dim: int) -> list[str]:
    """임베디드 백엔드 스키마 DDL(멱등 — migrate 가 'already exists' 무시).
    노드 테이블 선행 후 관계 테이블(FROM/TO 노드가 먼저 존재해야 함). 동기화 메타
    테이블은 관계 참조가 없어 노드 뒤·관계 앞 어디든 무방(노드 뒤에 붙임)."""
    return render_node_tables(dim=dim) + render_meta_tables() + render_rel_tables()
