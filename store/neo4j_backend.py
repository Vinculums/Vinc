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
열기 전에 거절한다. 쓰기 임대는 아래 `LEASE_LABEL` 이 진다. 스냅샷은 다음 단위다.
"""
from __future__ import annotations

import os
import re
import socket
import sys
import threading
import uuid
from contextlib import contextmanager

from . import pkns
from .embedded_backend import _filter_clause, _Result
from .write_batch import Batch, BatchSlot, flush
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


#: 쓰기 임대. 데이터베이스 하나에 Vinc 쓰기 프로세스는 하나다(계획 B4). 임대 노드 하나가
#: 누가 쥐었는지와 만료 시각(ms)을 든다. 먼저 노드에 쓰기 잠금을 건 뒤(`_touch`) 판정하므로
#: 두 프로세스가 동시에 집어도 하나만 이긴다(Neo4j 는 잠금을 쥔 뒤의 읽기가 최신 커밋을 본다).
LEASE_LABEL = "_VincWriterLease"
_LEASE_ACQUIRE = (
    f"MERGE (l:{LEASE_LABEL} {{key: 'writer'}}) "
    "SET l._touch = coalesce(l._touch, 0) + 1 "
    "WITH l, timestamp() AS now "
    "WITH l, now, (l.holder IS NULL OR l.holder = $h OR coalesce(l.expires, 0) < now "
    "              OR coalesce(l.holder = $prev, false)) AS ok "
    "SET l.holder = CASE WHEN ok THEN $h ELSE l.holder END, "
    "    l.expires = CASE WHEN ok THEN now + $ttl ELSE l.expires END "
    "RETURN ok, l.holder AS holder, l.expires AS expires")
#: 설치 단위 보유자 id 모양: `<설치 id>@<호스트>:<pid>`. 같은 설치·같은 호스트의 죽은 pid 가 쥔 임대는
#: 만료를 기다리지 않고 되찾는다(`try_acquire`). 다른 설치나 다른 호스트의 임대는 만료까지 기다린다.
_HOLDER = re.compile(r"^(?P<inst>[0-9a-f]{32})@(?P<host>[^:]+):(?P<pid>\d+)$")


def installation_holder(installation: str, *, host: str | None = None, pid: int | None = None) -> str:
    return f"{installation}@{host or socket.gethostname()}:{pid or os.getpid()}"


_LEASE_RELEASE = (f"MATCH (l:{LEASE_LABEL} {{key: 'writer'}}) WHERE l.holder = $h "
                  "SET l.expires = 0")


def _safe_error(exc: Exception) -> str:
    """드라이버 오류의 사람용 한 줄. 드라이버는 비밀번호를 메시지에 싣지 않지만, 혹시 실어도 잘라 낸다."""
    text = " ".join(str(getattr(exc, "message", None) or exc).split())
    return re.sub(r"(?i)(password|credentials?)\s*[=:]\s*\S+", r"\1=<hidden>", text)[:300]


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


class StoreReadOnly(RuntimeError):
    """읽기 전용으로 연 프로세스의 쓰기. 서버의 AccessMode 오류 대신 왜 읽기 전용인지 말한다."""


class Neo4jBackend:
    """Neo4j 어댑터. 읽기와 쓰기가 같은 드라이버를 쓴다(엔진이 동시 트랜잭션을 받는다)."""

    driver_name = "neo4j"
    #: 청크 팔이 owner 필터를 적용한다(임베디드와 같은 계약).
    scoped_chunk_search = True
    #: 마지막으로 못 연 사유(사람에게 보여도 되는 문장, 비밀번호 없음). `/health` 의 open_error 로 나간다.
    last_open_error: str = ""

    @classmethod
    def open(cls, cfg) -> "Neo4jBackend | None":
        """설정(`store.config.database_config`)으로 연다. 못 열면 None 이고 사유는 `last_open_error`.

        드라이버가 없거나, 닿지 않거나, 비밀번호가 틀렸거나, 하한보다 낮은 판이면 여기서 멈춘다.
        폴더 스토어로 물러서지 않는다(빈 그래프가 데이터 유실로 읽힌다)."""
        db = cfg.database or {}
        try:
            import neo4j  # noqa: F401 — 드라이버가 없으면 사유를 말하고 멈춘다
        except ImportError:
            cls.last_open_error = "the neo4j driver is not installed in this build"
            return None
        if not cfg.database_secret:
            cls.last_open_error = ("no password for the database: the app passes it from the "
                                   "keyring when it starts the sidecar")
            return None
        try:
            store = cls(db["uri"], user=db["user"], password=cfg.database_secret,
                        database=db["database"], holder=cfg.lease_holder)
        except Exception as exc:   # noqa: BLE001 — 사유를 한 줄로 남긴다
            cls.last_open_error = f"{type(exc).__name__}: {_safe_error(exc)}"
            print(f"[neo4j] store open failed: {cls.last_open_error}", file=sys.stderr, flush=True)
            return None
        cls.last_open_error = ""
        if store.read_only:
            print(f"[neo4j] opened read-only: {store.read_only_reason}", file=sys.stderr, flush=True)
        return store

    def __init__(self, uri: str, *, user: str, password: str, database: str = "neo4j",
                 writer: bool = True, lease_ttl_s: float = 60.0, heartbeat: bool = True,
                 holder: str | None = None):
        """``writer=True`` 면 쓰기 임대를 집는다. 남이 쥐고 있으면 **읽기 전용**으로 열린다
        (`read_only`·`read_only_reason`). 읽기 전용 세션의 쓰기는 Neo4j 서버가 거절한다
        (`Neo.ClientError.Statement.AccessMode`) — 문장을 들여다보고 막는 것이 아니다."""
        from neo4j import GraphDatabase, NotificationMinimumSeverity
        self.uri, self.database = uri, database
        self.holder = holder or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._ttl_ms = int(lease_ttl_s * 1000)
        self.read_only, self.read_only_reason = not writer, "" if writer else "opened read-only"
        self._stop = threading.Event()
        self._beat: threading.Thread | None = None
        #: 스레드마다 열린 쓰기 묶음과 그 트랜잭션(:meth:`write_batch`). 다른 스레드의 읽기는 끼지 않는다.
        self._batches = BatchSlot()
        self._batch_tx = threading.local()
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
        if writer:
            self.try_acquire()
            if not self.read_only and heartbeat:
                self._beat = threading.Thread(target=self._heartbeat, name="vinc-neo4j-lease",
                                              daemon=True)
                self._beat.start()

    # ── 쓰기 임대 ─────────────────────────────────────────────────────────
    def _lease(self, query: str, prev: str | None = None) -> dict | None:
        # 임대 자체는 늘 쓰기 세션이다. `execute_write` 는 일시 오류(동시에 처음 노드를 만들 때의
        # 교착 감지 같은 TransientError)를 드라이버가 다시 시도한다 — 그냥 `run` 이면 진 쪽이 오류로 끝난다.
        def work(tx):
            rec = tx.run(query, {"h": self.holder, "ttl": self._ttl_ms, "prev": prev}).single()
            return rec.data() if rec else None
        with self._driver.session(database=self.database) as s:
            return s.execute_write(work)

    def try_acquire(self) -> bool:
        """임대를 집거나 늘린다. 집으면 쓰기, 못 집으면 읽기 전용으로 바꾸고 False."""
        row = self._lease(_LEASE_ACQUIRE) or {}
        stale = self._own_dead_holder(row.get("holder"))
        if not row.get("ok") and stale:
            # 같은 설치의 죽은 프로세스가 남긴 임대다. 읽은 보유자가 그대로일 때만 바꾼다(그 사이 남이
            # 집었으면 조건이 거짓이라 아무것도 안 바뀐다).
            row = self._lease(_LEASE_ACQUIRE, prev=stale) or {}
        if row.get("ok"):
            self.read_only, self.read_only_reason = False, ""
            return True
        self.read_only = True
        self.read_only_reason = f"another Vinc process holds the writer lease ({row.get('holder')})"
        return False

    def _own_dead_holder(self, holder) -> str | None:
        """임대를 쥔 것이 이 설치·이 호스트의 **죽은** 프로세스면 그 보유자 id, 아니면 None."""
        mine, theirs = _HOLDER.match(self.holder), _HOLDER.match(holder or "")
        if not mine or not theirs or holder == self.holder:
            return None
        if (theirs["inst"], theirs["host"]) != (mine["inst"], mine["host"]):
            return None
        from .procutil import pid_alive
        return None if pid_alive(int(theirs["pid"])) else holder

    def _heartbeat(self) -> None:
        while not self._stop.wait(max(1.0, self._ttl_ms / 3000)):
            try:
                if not self.try_acquire():
                    print(f"[neo4j] {self.read_only_reason}; this process is read-only now",
                          file=sys.stderr, flush=True)
                    return
            except Exception as exc:       # noqa: BLE001 — 끊김은 다음 박동이 다시 본다
                print(f"[neo4j] lease renewal failed: {type(exc).__name__}: {exc}",
                      file=sys.stderr, flush=True)

    def writer_state(self) -> dict:
        return {"writer": not self.read_only, "holder": self.holder,
                "reason": self.read_only_reason}

    # ── 질의 ──────────────────────────────────────────────────────────────
    def current_batch(self) -> Batch | None:
        return self._batches.get()

    @contextmanager
    def write_batch(self):
        """트랜잭션 하나를 연다(임베디드의 :meth:`EmbeddedBackend.write_batch` 와 같은 계약). 이 스레드의
        `run` 은 묶음이 끝날 때까지 그 트랜잭션으로 간다. 안의 문장이 실패하면 Neo4j 가 트랜잭션을
        끝내므로 묶음을 깨진 것으로 적고, 끝에서 되돌린다."""
        outer = self._batches.get()
        if outer is not None:
            yield outer
            return
        from neo4j import READ_ACCESS, WRITE_ACCESS
        mode = READ_ACCESS if self.read_only else WRITE_ACCESS
        batch = Batch()
        with self._driver.session(database=self.database, default_access_mode=mode) as s:
            tx = s.begin_transaction()
            self._batch_tx.tx = tx
            self._batches.set(batch)
            committed = False
            try:
                yield batch
                if batch.broken is None:
                    flush(self, batch, lambda q, p: self.run(q, p))
                    tx.commit()
                    committed = True
            finally:
                self._batches.set(None)
                self._batch_tx.tx = None
                if not committed:
                    try:
                        tx.rollback()
                    except Exception:   # noqa: BLE001 — 이미 끝난 트랜잭션이면 되돌릴 것이 없다
                        pass
                tx.close()

    def run(self, query: str, params: dict | None = None, *, conn=None,
            raw: bool = False) -> list[dict]:
        batch = self._batches.get()
        if batch is not None:
            batch.check()
            batch.statements += 1
            try:
                return _rows(self._batch_tx.tx.run(to_neo4j_cypher(query), params or {}), raw=raw)
            except Exception as exc:
                batch.fail(exc)
                raise
        from neo4j import READ_ACCESS, WRITE_ACCESS
        mode = READ_ACCESS if self.read_only else WRITE_ACCESS
        try:
            with self._driver.session(database=self.database, default_access_mode=mode) as s:
                result = s.run(to_neo4j_cypher(query), params or {})
                return _rows(result, raw=raw)
        except Exception as exc:
            # 서버의 "Writing in read access mode not allowed" 는 사람에게 이유를 말하지 않는다.
            # 이 프로세스가 왜 읽기 전용인지(누가 쓰기 임대를 쥐었는지)를 말하는 오류로 바꾼다.
            if self.read_only and "AccessMode" in str(getattr(exc, "code", "") or exc):
                raise StoreReadOnly(
                    f"this Vinc process opened the database read-only: {self.read_only_reason}. "
                    "Close the other Vinc that writes to it, or wait for its lease to run out") from exc
            raise

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
        stmts.append(f"CREATE CONSTRAINT vinc_writer_lease_key IF NOT EXISTS "
                     f"FOR (l:{LEASE_LABEL}) REQUIRE l.key IS UNIQUE")
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
                           use_index: bool = False, kinds=None) -> list[dict]:
        params: dict = {"emb": list(embedding), "k": int(top_k) * (_OVERSAMPLE if owner or kinds else 1)}
        where = ""
        if owner:
            where, params["owner"] = "WHERE v.owner_id = $owner ", owner
        if kinds:
            where += ("AND " if where else "WHERE ") + "v.kind IN $kinds "
            params["kinds"] = list(kinds)
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
        self._stop.set()
        if self._beat is not None:
            self._beat.join(timeout=5)
        if not self.read_only:
            try:
                self._lease(_LEASE_RELEASE)   # 다음 프로세스가 만료를 기다리지 않게
            except Exception:                 # noqa: BLE001 — 닫기는 막지 않는다
                pass
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


# ── 연결 확인(저장 위치를 고르기 전에) ─────────────────────────────────────────
#: 그래프 노드로 세는 표(청크·벡터 미러는 문서와 노드의 부속이라 세지 않는다).
_COUNTED_LABELS = ("Episode", "Concept", "Decision", "Bridge", "Domain", "Record",
                   "SkillExecution", "LearnerPattern", "Entity")
#: 이 왕복 시간(ms, 세 번의 중앙값)을 넘으면 원격 지연을 위험으로 알린다. 검색 한 번이 수십 번 왕복한다.
REMOTE_LATENCY_MS = 30.0
#: 막는 위험. 나머지(평문 연결·원격 지연·남의 데이터)는 사람이 확인하면 받는다.
BLOCKING_RISKS = ("version_below_floor", "read_only_user")


def vinc_labels() -> set[str]:
    return set(node_table_specs()) | set(meta_table_specs()) | {LEASE_LABEL}


def inspect_database(uri: str, *, user: str, password: str, database: str,
                     timeout_s: float = 8.0) -> dict:
    """연결해 보고 **아무것도 남기지 않는다**(쓰기 권한은 되돌리는 트랜잭션으로 본다). 임대를 집지 않는다.

    `ok` 가 False 면 이 위치를 받을 수 없고 `problem` 이 이유다: unreachable · auth_failed ·
    database_not_found · driver_missing · error. `risks` 는 받을 수 있지만 사람에게 말할 것이다.
    비밀번호는 결과·로그 어디에도 싣지 않는다."""
    import time
    from urllib.parse import urlsplit
    out: dict = {"ok": False, "problem": None, "detail": None, "reachable": False, "auth_ok": False,
                 "server_version": None, "edition": None, "version_supported": None,
                 "write_permission": None, "latency_ms": None,
                 "vinc_schema_version": None, "vinc_counts": {"nodes": 0, "docs": 0},
                 "foreign_labels": {"count": 0, "names": [], "nodes": 0},
                 "writer_lease": None, "risks": []}
    try:
        from neo4j import GraphDatabase, NotificationMinimumSeverity
        from neo4j.exceptions import AuthError, ClientError, Forbidden, ServiceUnavailable
    except ImportError:
        out["problem"] = "driver_missing"
        return out
    # 평문은 이 기계 밖으로 나갈 때만 위험이다. 같은 노트북의 Neo4j 에 bolt:// 로 붙는 것까지
    # 확인을 받으면 사람은 늘 "그래도 쓰기"를 누르게 되고, 경고가 뜻을 잃는다.
    from .location import is_local_uri
    if urlsplit(uri).scheme not in ("neo4j+s", "neo4j+ssc", "bolt+s", "bolt+ssc") and not is_local_uri(uri):
        out["risks"].append("unencrypted_transport")
    driver = GraphDatabase.driver(uri, auth=(user, password), connection_timeout=timeout_s,
                                  connection_acquisition_timeout=timeout_s,
                                  notifications_min_severity=NotificationMinimumSeverity.OFF)
    try:
        try:
            driver.verify_connectivity()
        except AuthError as exc:
            out.update(reachable=True, problem="auth_failed", detail=_safe_error(exc))
            return out
        except (ServiceUnavailable, OSError) as exc:
            out.update(problem="unreachable", detail=_safe_error(exc))
            return out
        out.update(reachable=True, auth_ok=True)
        info = driver.get_server_info()
        out["server_version"] = (info.agent or "").split("/", 1)[-1] or None
        out["version_supported"] = _version_tuple(out["server_version"] or "") >= VERSION_FLOOR
        if not out["version_supported"]:
            out["risks"].append("version_below_floor")
        with driver.session(database=database) as s:
            try:
                rows = s.run("CALL dbms.components() YIELD edition RETURN edition").data()
                out["edition"] = rows[0]["edition"] if rows else None
            except ClientError as exc:
                if "DatabaseNotFound" in (exc.code or ""):
                    out.update(problem="database_not_found", detail=_safe_error(exc))
                    return out
                raise
            rtts = []
            for _ in range(3):
                t0 = time.perf_counter()
                s.run("RETURN 1").consume()
                rtts.append((time.perf_counter() - t0) * 1000)
            out["latency_ms"] = round(sorted(rtts)[1], 1)
            if out["latency_ms"] > REMOTE_LATENCY_MS:
                out["risks"].append("remote_latency")
            vinc = vinc_labels()
            labels = [r["label"] for r in s.run("CALL db.labels() YIELD label RETURN label").data()]
            present = [lb for lb in labels if lb in vinc]
            foreign = sorted(lb for lb in labels if lb not in vinc)
            if present:
                row = s.run("MATCH (m:_SchemaMeta {key: 'schema_version'}) RETURN m.value AS v").single()
                out["vinc_schema_version"] = int(row["v"]) if row and str(row["v"]).isdigit() else None
                out["vinc_counts"]["nodes"] = s.run(
                    "MATCH (n) WHERE any(l IN labels(n) WHERE l IN $ls) RETURN count(n) AS c",
                    {"ls": list(_COUNTED_LABELS)}).single()["c"]
                out["vinc_counts"]["docs"] = s.run("MATCH (d:_DocMeta) RETURN count(d) AS c").single()["c"]
                lease = s.run(f"MATCH (l:{LEASE_LABEL} {{key: 'writer'}}) "
                              "RETURN l.holder AS holder, l.expires > timestamp() AS active").single()
                if lease is not None:
                    out["writer_lease"] = {"holder": lease["holder"], "active": bool(lease["active"])}
            if foreign:
                n = s.run("MATCH (n) WHERE none(l IN labels(n) WHERE l IN $ls) RETURN count(n) AS c",
                          {"ls": sorted(vinc)}).single()["c"]
                out["foreign_labels"] = {"count": len(foreign), "names": foreign[:20], "nodes": n}
                if n:
                    out["risks"].append("foreign_data_present")
            # 쓰기 권한: 되돌리는 트랜잭션 안에서 만들어 본다. 커밋하지 않으므로 남는 것이 없다.
            tx = s.begin_transaction()
            try:
                tx.run("CREATE (p:_VincProbe) DELETE p").consume()
                out["write_permission"] = True
            except (Forbidden, ClientError) as exc:
                if "Forbidden" in type(exc).__name__ or "Security" in (exc.code or "") \
                        or "AccessMode" in (exc.code or ""):
                    out["write_permission"] = False
                    out["risks"].append("read_only_user")
                else:
                    raise
            finally:
                tx.rollback()
        out["ok"] = True
        return out
    except Exception as exc:   # noqa: BLE001 — 확인은 사유를 돌려주지 예외로 죽지 않는다
        out.update(problem=out["problem"] or "error", detail=f"{type(exc).__name__}: {_safe_error(exc)}")
        return out
    finally:
        driver.close()
