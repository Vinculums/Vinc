# -*- coding: utf-8 -*-
"""Embedded (LadybugDB/Kùzu) 스키마 렌더러 — 설계 §5 (schema-free LPG → 테이블 선행).

Neo4j 는 SET 이 임의 속성을 만들 수 있으나 임베디드 백엔드는 **열을 먼저 선언**해야
한다(§5-2). 각 엔진의 SET 절에서 도출한 열 명세가 여기 SSoT 다. 노드 테이블은
`store/schema.py` 의 제약 11건(§5-1)과 PK 가 일치한다.

⚠ 현 단계(M2 e2e): **노드 테이블** 렌더 — Episode 는 §5-2 전체 열 완비(recall e2e).
   관계 테이블(§5-3)·나머지 노드 전체 열 감사는 P1 후속(각 엔진 SET 전수 조사).
"""
from __future__ import annotations

# 노드 테이블 열 명세 (label → [(col, type), ...], 마지막에 PK 지정).
# type: STRING · INT64 · DOUBLE · BOOL · DATE · TIMESTAMP · STRING[] · FLOAT[{dim}]
_NODE_TABLES: dict[str, dict] = {
    # Episode — recall_to_neo4j SET + preference(pin/tag/touch) + §5-2 전체
    "Episode": {
        "pk": "episode_id",
        "cols": [
            ("episode_id", "STRING"), ("title", "STRING"), ("date", "STRING"),
            ("owner_id", "STRING"), ("tenant_id", "STRING"), ("domain", "STRING"),
            ("workspace", "STRING"), ("mode", "STRING"), ("summary", "STRING"),
            ("completed", "BOOL"), ("patterns", "STRING[]"), ("source_file", "STRING"),
            ("pinned", "BOOL"), ("tags", "STRING[]"),
            ("access_count", "INT64"), ("last_access", "STRING"),
        ],
    },
    "Concept": {"pk": "concept_id", "cols": [
        ("concept_id", "STRING"), ("title", "STRING"), ("owner_id", "STRING"),
        ("domain", "STRING"), ("summary", "STRING")]},
    "LearnerPattern": {"pk": "pattern_id", "cols": [
        ("pattern_id", "STRING"), ("code", "STRING"), ("name", "STRING"),
        ("description", "STRING"), ("domain", "STRING"),
        ("observations", "STRING[]"), ("owner_id", "STRING")]},
    "SkillExecution": {"pk": "exec_id", "cols": [
        ("exec_id", "STRING"), ("skill", "STRING"), ("mode", "STRING"),
        ("outcome", "STRING"), ("duration_min", "DOUBLE"), ("date", "STRING")]},
    "Bridge": {"pk": "bridge_id", "cols": [
        ("bridge_id", "STRING"), ("title", "STRING"), ("owner_id", "STRING")]},
    "Domain": {"pk": "name", "cols": [("name", "STRING")]},
    # Chunk — embed_pipeline. 벡터 차원은 provision 시 주입({dim}).
    "Chunk": {"pk": "id", "cols": [
        ("id", "STRING"), ("text", "STRING"), ("embedding", "FLOAT[{dim}]"),
        ("source", "STRING")]},
    # PM 계 노드(graph_to_neo4j)
    "Policy": {"pk": "id", "cols": [("id", "STRING"), ("title", "STRING"), ("node_type", "STRING")]},
    "Screen": {"pk": "id", "cols": [("id", "STRING"), ("title", "STRING"), ("node_type", "STRING")]},
    "Reference": {"pk": "id", "cols": [("id", "STRING"), ("title", "STRING"), ("node_type", "STRING")]},
    "Gate": {"pk": "id", "cols": [("id", "STRING"), ("title", "STRING"), ("node_type", "STRING")]},
}


def render_node_tables(*, dim: int) -> list[str]:
    out: list[str] = []
    for label, spec in _NODE_TABLES.items():
        cols = ", ".join(f"{c} {t.replace('{dim}', str(dim))}" for c, t in spec["cols"])
        out.append(f"CREATE NODE TABLE {label}({cols}, PRIMARY KEY({spec['pk']}))")
    return out


def render_embedded_ddl(*, dim: int) -> list[str]:
    """임베디드 백엔드 스키마 DDL(멱등 — provision 이 'already exists' 무시).

    현 단계: 노드 테이블. 관계 테이블(§5-3)은 P1 후속."""
    return render_node_tables(dim=dim)
