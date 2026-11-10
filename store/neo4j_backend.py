# -*- coding: utf-8 -*-
"""Neo4j 어댑터 — 사용자가 연결한 Neo4j 데이터베이스를 Vinc 스토어로 쓴다(`GraphStore` 구현).

계획 `vinc.plan.bring-your-own-database-2026-11-08` §2 B4. 이 파일만 neo4j 드라이버(Apache-2.0)를
import 한다. 질의는 호출부가 표준 openCypher·GQL 로 쓰므로(B2) 그대로 보낸다. 이 어댑터가 하는 일:

- **행 모양을 임베디드와 같게**: 노드는 `{_ID, _LABEL, 속성…}`, 관계는 `{_ID, _LABEL, _SRC, _DST,
  속성…}`, 경로는 `{_NODES, _RELS}`. 호출부는 엔진을 모른다.
- **PK 분해 훅**: 물리 키(owner U+001F id)를 논리 id 로 되돌리는 자리가 임베디드와 같은 규칙이다.
- **스키마 준비**: 표마다 키 고유 제약, 청크 전문 색인(`lex` 위, 공백 분석기 — 토큰화는 Vinc 가
  `store.lexical` 로 이미 했다), 청크 조각·노드 미러 벡터 색인(코사인).
- **알림 경고는 끈다**: 아직 노드가 없는 속성을 이름으로 부르면 Neo4j 는 경고를 내는데, 그건
  오류가 아니다(임베디드는 스키마가 먼저라 같은 질의가 조용하다).

지원 하한은 Neo4j 2026.01 이다(`decision:byo-database-neo4j-floor-2026-01-2026-11-08`). 그보다 낮으면
열기 전에 거절한다. 쓰기 임대(Vinc 쓰기 프로세스 하나)와 스냅샷은 다음 단위다.
"""
from __future__ import annotations

import re
from contextlib import contextmanager

from . import pkns
from .embedded_backend import _filter_clause, _Result
from .embedded_schema import meta_table_specs, node_table_specs

#: 지원 하한(연.월). 그 아래 판은 열지 않는다.
VERSION_FLOOR = (2026, 1)

_FTS_INDEX = "vinc_chunk_lex"
_VEC_INDEX = "vinc_chunkvec_embedding"
_NODE_VEC_INDEX = "vinc_nodevec_embedding"
#: Lucene 질의 문법에서 뜻을 갖는 글자. 사용자 낱말은 낱말로만 찾게 하나씩 이스케이프한다.
_LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/&|])')
_CHILD_FANOUT = 4
_OVERSAMPLE = 5


class Neo4jVersionError(RuntimeError):
    """연결한 서버가 지원 하한보다 낮다."""


def _version_tuple(text: str) -> tuple[int, int]:
    m = re.match(r"(\d+)\.(\d+)", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _value(v):
    """드라이버 값 → 임베디드와 같은 모양의 파이썬 값."""
    from neo4j.graph import Node, Path, Relationship
    if isinstance(v, Node):
        out = {"_ID": v.element_id, "_LABEL": next(iter(v.labels), None)}
        out.update({k: _value(x) for k, x in v.items()})
        return out
    if isinstance(v, Relationship):
        out = {"_ID": v.element_id, "_LABEL": v.type,
               "_SRC": v.start_node.element_id if v.start_node is not None else None,
               "_DST": v.end_node.element_id if v.end_node is not None else None}
        out.update({k: _value(x) for k, x in v.items()})
        return out
    if isinstance(v, Path):
        return {"_NODES": [_value(n) for n in v.nodes],
                "_RELS": [_value(r) for r in v.relationships]}
    if isinstance(v, list):
        return [_value(x) for x in v]
    if isinstance(v, dict):
        return {k: _value(x) for k, x in v.items()}
    return v


def _rows(result, *, raw: bool) -> list[dict]:
    out = [{k: _value(rec[k]) for k in rec.keys()} for rec in result]
    if pkns.enabled() and not raw:
        out = [pkns.strip_deep(row) for row in out]
    return out


#: 임베디드 철자가 남아 있어도 Neo4j 에서 같은 뜻이 되는 두 꼴만 여기서 바꾼다.
#: - `CAST(NULL AS 타입)`: ladybug 가 UNION ALL 열 타입을 맞추려고 쓰는 타입 붙은 NULL. Neo4j 는 `NULL`.
#: - `regexp_replace(식, $props_key, ' ', 'g')`: 노드 후보 질의가 props 의 키 자리를 지우는 식
#:   (`vinc_rag.retrieval._PROPS_KEY_PATTERN`). Neo4j 기본 Cypher 에는 정규식 치환이 없어 지우지
#:   않은 식으로 보낸다. 후보가 키 낱말로 조금 늘 수 있지만 점수는 파이썬이 키를 지운 값으로 다시
#:   매기므로(`_props_values_text`) 순위는 같다.
_TYPED_NULL = re.compile(r"\bCAST\(\s*NULL\s+AS\s+\w+\s*\)")
_PROPS_KEY_STRIP = re.compile(r"regexp_replace\((lower\(coalesce\([\w.]+,\s*''\)\)),\s*\$props_key,\s*' ',\s*'g'\)")


def to_neo4j_cypher(cypher: str) -> str:
    if "CAST(" in cypher:
        cypher = _TYPED_NULL.sub("NULL", cypher)
    if "regexp_replace(" in cypher:
        cypher = _PROPS_KEY_STRIP.sub(r"\1", cypher)
    return cypher


def lucene_terms(text: str) -> str:
    """공백으로 나뉜 낱말을 Lucene 의 OR 질의로. 특수 문자는 문자 그대로 찾게 이스케이프한다."""
    return " ".join(_LUCENE_SPECIAL.sub(r"\\\1", t) for t in text.split())


class _Session:
    """neo4j Session 위의 얇은 판 — `.run(cypher, **params)` 가 `_Result` 를 준다(임베디드와 같다)."""

    def __init__(self, backend):
        self._b = backend

    def run(self, cypher: str, **params):
        return _Result(self._b.run(cypher, params or None))

    def close(self):
        pass


class Neo4jBackend:
    """Neo4j 어댑터. 읽기와 쓰기가 같은 드라이버를 쓴다(엔진이 동시 트랜잭션을 받는다)."""

    driver_name = "neo4j"
    #: 청크 팔이 owner 필터를 적용한다(임베디드와 같은 계약).
    scoped_chunk_search = True

    def __init__(self, uri: str, *, user: str, password: str, database: str = "neo4j"):
        from neo4j import GraphDatabase, NotificationMinimumSeverity
        self.uri, self.database = uri, database
        self._driver = GraphDatabase.driver(
            uri, auth=(user, password),
            notifications_min_severity=NotificationMinimumSeverity.OFF)
        try:
            self._driver.verify_connectivity()
            info = self._driver.get_server_info()
            self.server_version = (info.agent or "").split("/", 1)[-1]
            if _version_tuple(self.server_version) < VERSION_FLOOR:
                raise Neo4jVersionError(
                    f"Neo4j {self.server_version} is below the supported floor "
                    f"{VERSION_FLOOR[0]}.{VERSION_FLOOR[1]:02d}")
        except Exception:
            self._driver.close()
            raise

    # ── 질의 ──────────────────────────────────────────────────────────────
    def run(self, query: str, params: dict | None = None, *, conn=None,
            raw: bool = False) -> list[dict]:
        with self._driver.session(database=self.database) as s:
            result = s.run(to_neo4j_cypher(query), params or {})
            return _rows(result, raw=raw)

    @contextmanager
    def session(self):
        yield _Session(self)

    def reader_view(self, *, writer=None):
        """읽기 뷰. Neo4j 는 읽기와 쓰기가 서로 막지 않으므로 같은 어댑터를 그대로 준다."""
        return _ReaderView(self)

    # ── 스키마 ────────────────────────────────────────────────────────────
    def provision(self, *, dim: int) -> list[str]:
        stmts = []
        for label, spec in {**node_table_specs(), **meta_table_specs()}.items():
            stmts.append(f"CREATE CONSTRAINT vinc_{label.lower()}_pk IF NOT EXISTS "
                         f"FOR (n:`{label}`) REQUIRE n.`{spec['pk']}` IS UNIQUE")
        stmts.append(f"CREATE FULLTEXT INDEX {_FTS_INDEX} IF NOT EXISTS FOR (c:Chunk) ON EACH [c.lex] "
                     "OPTIONS {indexConfig: {`fulltext.analyzer`: 'whitespace'}}")
        for name, label in ((_VEC_INDEX, "ChunkVec"), (_NODE_VEC_INDEX, "NodeVec")):
            stmts.append(f"CREATE VECTOR INDEX {name} IF NOT EXISTS FOR (v:{label}) ON v.embedding "
                         "OPTIONS {indexConfig: {`vector.dimensions`: %d, "
                         "`vector.similarity_function`: 'cosine'}}" % int(dim))
        for stmt in stmts:
            self.run(stmt)
        from .embedded_schema import SCHEMA_VERSION
        from .embedded_schema import LEX_INDEX_KEY
        self.run("MERGE (m:_SchemaMeta {key: 'schema_version'}) SET m.value = $v",
                 {"v": str(SCHEMA_VERSION)})
        self.run("MERGE (m:_SchemaMeta {key: $k}) SET m.value = 'ready'", {"k": LEX_INDEX_KEY})
        self.run("CALL db.awaitIndexes(60)")
        return stmts

    def lex_ready(self) -> bool:
        return True   # 전문 색인은 처음부터 `lex` 위에 선다(provision)

    def ensure_search_indexes(self, *, dim: int | None = None, conn=None) -> dict:
        return {}     # 색인은 provision 이 세우고 Neo4j 가 쓰기와 함께 유지한다

    def schedule_search_indexes(self, *, dim: int | None = None) -> None:
        return None

    # 색인이 열을 얼리지 않으므로(임베디드의 HNSW 와 다르다) 떼었다 붙일 일이 없다.
    def drop_vector_index(self) -> None:
        return None

    def drop_node_vector_index(self) -> None:
        return None

    def drop_search_indexes(self) -> None:
        return None

    # ── 검색 ──────────────────────────────────────────────────────────────
    def fulltext_search(self, text, top_k, filters=None, *, conn=None) -> list[dict]:
        from .lexical import lex_text
        q = lucene_terms(lex_text(text))
        if not q:
            return []
        k = int(top_k) * (_OVERSAMPLE if filters else 1)
        params: dict = {"q": q, "k": k}
        where = _filter_clause("node", filters, params)
        rows = self.run(
            f"CALL db.index.fulltext.queryNodes('{_FTS_INDEX}', $q) YIELD node, score "
            f"{where}"
            "RETURN node.chunk_id AS id, node.text AS text, node.lex AS lex, score "
            "ORDER BY score DESC LIMIT $k", params)
        return rows[:int(top_k)]

    def vector_search(self, embedding, top_k, filters=None, *, conn=None) -> list[dict]:
        """자식(ChunkVec)에서 찾아 부모(Chunk)로 접는다. 거리는 1 - 코사인(임베디드와 같은 척도)."""
        k = int(top_k) * (_OVERSAMPLE if filters else 1)
        params: dict = {"emb": list(embedding), "k": k * _CHILD_FANOUT}
        where = _filter_clause("p", filters, params)
        rows = self.run(
            f"CALL db.index.vector.queryNodes('{_VEC_INDEX}', $k, $emb) YIELD node AS v, score "
            "MATCH (p:Chunk {chunk_id: v.chunk_id}) "
            f"{where}"
            # Neo4j 의 코사인 점수는 (1 + cos) / 2 다. 거리 1 - cos 로 되돌린다.
            "RETURN p.chunk_id AS id, p.text AS text, 2 - 2 * score AS distance "
            "ORDER BY distance", params)
        best: dict[str, dict] = {}
        for r in rows:
            best.setdefault(r.get("id"), r)
        return list(best.values())[:int(top_k)]

    def node_vector_search(self, embedding, top_k, *, owner=None, conn=None,
                           use_index: bool = False) -> list[dict]:
        params: dict = {"emb": list(embedding), "k": int(top_k) * (_OVERSAMPLE if owner else 1)}
        where = ""
        if owner:
            where, params["owner"] = "WHERE v.owner_id = $owner ", owner
        rows = self.run(
            f"CALL db.index.vector.queryNodes('{_NODE_VEC_INDEX}', $k, $emb) YIELD node AS v, score "
            f"{where}"
            "RETURN v.node_id AS id, 2 - 2 * score AS distance ORDER BY distance", params)
        return rows[:int(top_k)]

    def chunk_vector_distances(self, chunk_ids, embedding, *, conn=None) -> dict[str, float]:
        rows = self.run(
            "MATCH (v:ChunkVec) WHERE v.chunk_id IN $ids "
            "RETURN v.chunk_id AS cid, min(1 - vector.similarity.cosine(v.embedding, $emb) * 2 + 1) AS d",
            {"ids": list(chunk_ids), "emb": list(embedding)})
        return {str(r["cid"]): float(r["d"]) for r in rows if r.get("d") is not None}

    # ── 카탈로그 ──────────────────────────────────────────────────────────
    def search_indexes(self) -> list[tuple[str, str]]:
        rows = self.run("SHOW INDEXES YIELD name, type, labelsOrTypes "
                        "WHERE type IN ['FULLTEXT', 'VECTOR'] RETURN name, labelsOrTypes")
        return [((r["labelsOrTypes"] or [""])[0], r["name"]) for r in rows]

    def table_names(self) -> set[str]:
        return {r["label"] for r in self.run("CALL db.labels() YIELD label RETURN label")}

    def table_columns(self, table: str) -> list[tuple[str, str]] | None:
        rows = self.run("CALL db.schema.nodeTypeProperties() YIELD nodeLabels, propertyName, "
                        "propertyTypes WHERE $t IN nodeLabels "
                        "RETURN propertyName AS name, propertyTypes AS types", {"t": table})
        if not rows:
            return None
        return [(r["name"], ",".join(r["types"] or [])) for r in rows if r["name"]]

    # ── 수명 ──────────────────────────────────────────────────────────────
    def snapshot(self, dest_dir: str) -> str:
        raise NotImplementedError("a Neo4j location is backed up with Neo4j's own tools")

    def close(self) -> None:
        self._driver.close()


class _ReaderView:
    """읽기 경로가 받는 뷰. 닫아도 드라이버는 그대로다(수명은 어댑터 소유)."""

    def __init__(self, backend: Neo4jBackend):
        self._b = backend

    def __getattr__(self, name):
        return getattr(self._b, name)

    def run(self, query: str, params: dict | None = None) -> list[dict]:
        return self._b.run(query, params)

    def close(self) -> None:
        pass
