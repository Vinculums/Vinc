"""토픽 이전 목록의 정본을 로컬 디스크에 제한된 크기로 보관한다."""
# 근거: [[vinc.spec.dataplane]] §7.
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections.abc import Sequence
from pathlib import Path

from .embedded_schema import SCHEMA_VERSION, meta_table_specs, node_table_specs
from .topic_transfer import TransferRefused, _canonical


class ManifestLimit(TransferRefused):
    def __init__(self, reason, **details):
        super().__init__("conversion_inventory_limit")
        self.reason = reason
        self.details = details


class Rows(Sequence):
    """한 테이블의 행을 전체 적재 없이 순서대로 읽는다."""

    def __init__(self, manifest, label):
        self.manifest, self.label = manifest, label

    def __len__(self):
        return self.manifest.db.execute(
            "SELECT count(*) FROM payload WHERE label=?", (self.label,)).fetchone()[0]

    def __iter__(self):
        return self.where()

    def __getitem__(self, index):
        self.manifest.check_time()
        if isinstance(index, slice):
            raise TypeError("manifest rows must be streamed")
        if index < 0:
            index += len(self)
        row = self.manifest.db.execute(
            "SELECT body FROM payload WHERE label=? ORDER BY pk LIMIT 1 OFFSET ?",
            (self.label, index)).fetchone()
        if row is None:
            raise IndexError(index)
        return json.loads(row[0])

    def where(self, *, order_by="pk", **fields):
        clauses, params = ["label=?"], [self.label]
        for field, value in fields.items():
            if field not in ("doc_id", "owner_id", "pk"):
                raise ValueError("unindexed manifest field")
            clauses.append(field + "=?")
            params.append(value)
        if order_by not in ("pk", "chunk_index"):
            raise ValueError("unsupported manifest order")
        order = "pk" if order_by == "pk" else "CAST(json_extract(body,'$.chunk_index') AS INTEGER),pk"
        cursor = self.manifest.db.execute(
            "SELECT body FROM payload WHERE " + " AND ".join(clauses) + " ORDER BY " + order, params)
        try:
            for row in cursor:
                self.manifest.check_time()
                yield json.loads(row[0])
        finally:
            cursor.close()

    def append(self, row):
        self.manifest.add(self.label, row)

    def get(self, key):
        self.manifest.check_time()
        row = self.manifest.db.execute(
            "SELECT body FROM payload WHERE label=? AND pk=?", (self.label, key)).fetchone()
        return json.loads(row[0]) if row else None


class Edges(Sequence):
    def __init__(self, manifest):
        self.manifest = manifest

    def __len__(self):
        return self.manifest.db.execute("SELECT count(*) FROM edges").fetchone()[0]

    def __iter__(self):
        cursor = self.manifest.db.execute("SELECT body FROM edges ORDER BY ordinal")
        try:
            for row in cursor:
                self.manifest.check_time()
                yield json.loads(row[0])
        finally:
            cursor.close()

    def __getitem__(self, index):
        if index < 0:
            index += len(self)
        row = self.manifest.db.execute(
            "SELECT body FROM edges ORDER BY ordinal LIMIT 1 OFFSET ?", (index,)).fetchone()
        if row is None:
            raise IndexError(index)
        return json.loads(row[0])

    def append(self, row):
        self.manifest.add_edge(row)


class TopicManifest:
    """임시 payload는 로컬에 두고 발행할 목록에는 식별자와 해시만 담는다.

    호출자는 쓰기 가능한 SMB 대신 컨테이너 로컬 저장소를 사용한다.
    이후 배치가 읽을 payload의 정본은 원래 그래프다.
    """

    def __init__(self, path, *, max_rows=250_000, max_bytes=1024 * 1024 * 1024, deadline=None):
        self.deadline = deadline
        self.path = Path(path)
        # 기존 작업이나 다른 실행의 파일을 덮어쓰지 않는다.
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(self.path)
        self.db.executescript("""
            PRAGMA cache_size=-2048;
            PRAGMA mmap_size=0;
            PRAGMA temp_store=FILE;
            CREATE TABLE payload(label TEXT NOT NULL, pk TEXT NOT NULL,
                doc_id TEXT, owner_id TEXT, body TEXT NOT NULL, digest TEXT NOT NULL,
                PRIMARY KEY(label,pk));
            CREATE INDEX document_rows ON payload(label,doc_id,pk);
            CREATE INDEX owned_rows ON payload(label,owner_id,pk);
            CREATE TABLE edges(ordinal INTEGER PRIMARY KEY, body TEXT NOT NULL, digest TEXT NOT NULL);
            CREATE INDEX edge_bodies ON edges(body);
        """)
        self.max_rows, self.max_bytes = max_rows, max_bytes
        self.retained_rows = self.payload_bytes = 0
        self.snapshot = {"schema_version": SCHEMA_VERSION,
                         "nodes": {label: Rows(self, label) for label in node_table_specs()},
                         "meta": {label: Rows(self, label) for label in meta_table_specs()},
                         "edges": Edges(self)}

    def check_time(self):
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise ManifestLimit("processing_seconds")

    def _check(self, size):
        self.check_time()
        if self.retained_rows + 1 > self.max_rows:
            raise ManifestLimit("retained_rows", observed=self.retained_rows + 1, limit=self.max_rows)
        if self.payload_bytes + size > self.max_bytes:
            raise ManifestLimit("retained_bytes", observed=self.payload_bytes + size, limit=self.max_bytes)

    def add(self, label, row):
        specs = {**node_table_specs(), **meta_table_specs()}
        body = _canonical(row)
        size = len(body.encode())
        self._check(size)
        try:
            self.db.execute("INSERT INTO payload VALUES(?,?,?,?,?,?)",
                            (label, row[specs[label]["pk"]], row.get("doc_id"), row.get("owner_id"),
                             body, hashlib.sha256(body.encode()).hexdigest()))
        except sqlite3.IntegrityError:
            raise TransferRefused("duplicate_primary_key:" + label) from None
        self.retained_rows += 1
        self.payload_bytes += size

    def add_edge(self, row):
        body = _canonical(row)
        size = len(body.encode())
        self._check(size)
        self.db.execute("INSERT INTO edges(body,digest) VALUES(?,?)",
                        (body, hashlib.sha256(body.encode()).hexdigest()))
        self.retained_rows += 1
        self.payload_bytes += size

    def canonical_parts(self):
        # 기존 fingerprint의 정렬, 빈 테이블, 구분자를 스트림으로 재현한다.
        yield '{"edges":['
        for index, (body,) in enumerate(self.db.execute("SELECT body FROM edges ORDER BY body")):
            self.check_time()
            if index:
                yield ","
            yield body
        yield ']'
        for group in ("meta", "nodes"):
            yield ',"' + group + '":{'
            for index, label in enumerate(sorted(self.snapshot[group])):
                if index:
                    yield ","
                yield _canonical(label) + ":["
                for n, (body,) in enumerate(self.db.execute(
                        "SELECT body FROM payload WHERE label=? ORDER BY body", (label,))):
                    self.check_time()
                    if n:
                        yield ","
                    yield body
                yield "]"
            yield "}"
        yield ',"schema_version":' + str(SCHEMA_VERSION) + "}"

    def fingerprint(self):
        digest = hashlib.sha256()
        for part in self.canonical_parts():
            digest.update(part.encode())
        return digest.hexdigest()

    def serialized_bytes(self):
        return sum(len(part.encode()) for part in self.canonical_parts())

    def seal(self, destination, *, binding):
        """본문, 속성, 벡터 없이 변경 불가능한 목록을 발행한다."""
        destination = Path(destination)
        digest = hashlib.sha256()
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as output:
            def emit(row):
                data = (_canonical(row) + "\n").encode()
                output.write(data)
                digest.update(data)
            emit({"protocol": 3, "schema_version": SCHEMA_VERSION, "binding": binding,
                  "rows": self.retained_rows, "payload_bytes": self.payload_bytes,
                  "snapshot_hash": self.fingerprint()})
            for label, pk, row_hash in self.db.execute(
                    "SELECT label,pk,digest FROM payload ORDER BY label,pk"):
                emit({"kind": "node", "label": label, "key": pk, "digest": row_hash})
            for ordinal, row_hash in self.db.execute("SELECT ordinal,digest FROM edges ORDER BY ordinal"):
                emit({"kind": "edge", "ordinal": ordinal, "digest": row_hash})
            output.flush()
            os.fsync(output.fileno())
        return {"sha256": digest.hexdigest(), "rows": self.retained_rows,
                "bytes": destination.stat().st_size}

    @staticmethod
    def verify_seal(path, expected, *, binding):
        digest = hashlib.sha256()
        count = 0
        header = None
        try:
            with Path(path).open("rb") as source:
                while line := source.readline(16 * 1024 + 1):
                    if len(line) > 16 * 1024 or not line.endswith(b"\n"):
                        raise ValueError("manifest line exceeds bound")
                    digest.update(line)
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError("invalid manifest row")
                    if count == 0:
                        header = row
                    count += 1
        except (OSError, ValueError):
            raise TransferRefused("conversion_manifest_invalid") from None
        if (header is None or digest.hexdigest() != expected or header.get("binding") != binding
                or header.get("protocol") != 3 or header.get("schema_version") != SCHEMA_VERSION
                or type(header.get("rows")) is not int or header["rows"] < 0
                or count != header["rows"] + 1):
            raise TransferRefused("conversion_manifest_invalid")
        return header

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
