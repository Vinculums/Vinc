from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from . import pkns
from .embedded_schema import (
    SCHEMA_VERSION, meta_table_specs, node_table_specs, rel_table_specs,
)
from .querybase import is_conventions_doc_id


class TransferRefused(ValueError):
    pass


MAX_INVENTORY_ROWS = 5000
MAX_INVENTORY_BYTES = 32 * 1024 * 1024


def _canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def fingerprint(snapshot: dict) -> str:
    manifest = getattr(snapshot["edges"], "manifest", None)
    if manifest is not None:
        if manifest.snapshot is not snapshot:
            raise TransferRefused("invalid_manifest_snapshot")
        return manifest.fingerprint()
    # 순서만 다른 행과 병렬 관계의 개수를 모두 보존한다.
    normalized = copy.deepcopy(snapshot)
    for group in ("nodes", "meta"):
        for rows in normalized[group].values():
            rows.sort(key=_canonical)
    normalized["edges"].sort(key=_canonical)
    return hashlib.sha256(_canonical(normalized).encode()).hexdigest()


def check_inventory_budget(snapshot, *, extra_rows=0, extra_bytes=0):
    count = len(snapshot["edges"]) + sum(len(rows) for group in ("nodes", "meta")
                                          for rows in snapshot[group].values())
    if count + extra_rows > MAX_INVENTORY_ROWS or len(_canonical(snapshot).encode()) + extra_bytes > MAX_INVENTORY_BYTES:
        raise TransferRefused("conversion_inventory_limit")


def verify_store_shape(store, *, vocabulary_owner: str | None = None) -> None:
    """Validate sidecar and every physical table before any scoped inventory."""
    path = getattr(store, "path", None) or getattr(getattr(store, "_cfg", None), "data_dir", None)
    if not path:
        raise TransferRefused("external_store_state_unchecked")
    # DB 밖 소유자 어휘를 이전하지 못하므로, 파일이 있으면 내용과 무관하게 거절한다.
    sidecar = Path(str(path) + ".themes.json")
    if sidecar.exists():
        if vocabulary_owner is None:
            raise TransferRefused("owner_vocabulary_sidecar_not_supported")
        from .theme_store import owner_theme_id
        try:
            vocabulary = json.loads(sidecar.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            raise TransferRefused("owner_vocabulary_sidecar_not_supported") from None
        # A file shared with other tenants is not itself a source dependency.
        # Unknown shapes and any vocabulary belonging to this source fail closed.
        if not isinstance(vocabulary, dict) or not isinstance(vocabulary.get("themes"), dict) \
                or owner_theme_id(vocabulary_owner) in vocabulary["themes"]:
            raise TransferRefused("owner_vocabulary_sidecar_not_supported")
    specs = {**node_table_specs(), **meta_table_specs()}
    tables = store.run("CALL show_tables() RETURN *", raw=True)
    have = {r["name"] for r in tables}
    expected = set(specs) | {r["label"] for r in rel_table_specs()}
    if have != expected:
        raise TransferRefused("unsupported_schema_tables")
    for label, spec in specs.items():
        cols = {r["name"] for r in store.run(f"CALL table_info('{label}') RETURN *", raw=True)}
        if cols != set(spec["cols"]):
            raise TransferRefused("unsupported_schema_columns:" + label)
    for spec in rel_table_specs():
        label = spec["label"]
        cols = {r["name"] for r in store.run(f"CALL table_info('{label}') RETURN *", raw=True)}
        if cols != set(spec["props"]):
            raise TransferRefused("unsupported_schema_columns:" + label)


def read_snapshot(store, *, vocabulary_owner: str | None = None,
                  max_rows: int = MAX_INVENTORY_ROWS, max_bytes: int = MAX_INVENTORY_BYTES) -> dict:
    """Strict, read-only canonical inventory for synthetic rehearsal."""
    verify_store_shape(store, vocabulary_owner=vocabulary_owner)
    specs = {**node_table_specs(), **meta_table_specs()}
    result = {"schema_version": SCHEMA_VERSION, "nodes": {}, "meta": {}, "edges": []}
    remaining_rows, remaining_bytes = max_rows, max_bytes
    def bounded(q):
        nonlocal remaining_rows, remaining_bytes
        rows = store.run(q + f" LIMIT {remaining_rows + 1}", raw=True)
        remaining_rows -= len(rows)
        remaining_bytes -= len(_canonical(rows).encode())
        if remaining_rows < 0 or remaining_bytes < 0:
            raise TransferRefused("conversion_inventory_limit")
        return rows
    for label, spec in specs.items():
        projection = ", ".join(f"n.{c} AS {c}" for c in spec["cols"])
        group = "meta" if label.startswith("_") else "nodes"
        result[group][label] = bounded(f"MATCH (n:{label}) RETURN {projection}")
    for spec in rel_table_specs():
        label, src, dst = spec["label"], spec["from"], spec["to"]
        fields = [f"a.{specs[src]['pk']} AS src", f"b.{specs[dst]['pk']} AS dst"]
        fields += [f"r.{c} AS {c}" for c in spec["props"]]
        rows = bounded(f"MATCH (a:{src})-[r:{label}]->(b:{dst}) RETURN {', '.join(fields)}")
        for row in rows:
            result["edges"].append({"label": label, "from_label": src, "to_label": dst,
                                    "from": row.pop("src"), "to": row.pop("dst"), "props": row})
    return result


@dataclass(frozen=True)
class Plan:
    source: str
    target: str
    topic: str
    before_hash: str
    selected: tuple[tuple[str, str], ...]
    documents: tuple[str, ...]
    blockers: tuple[str, ...]
    conventions_policy: str = "retain"
    # 막지 않고 원문 그대로 옮기며 건수만 공개하는 참조(토픽 밖 id·외부 http(s) 첨부).
    preserved: tuple[str, ...] = ()

    @property
    def ready(self):
        return not self.blockers


_KINDS = ("Concept", "Record", "Decision", "Episode", "LearnerPattern")
_REFERENCE = re.compile(r"\[\[([^\]\s|]+)(?:[^\]]*)\]\]")
_ATTACHMENTS = {"attachment", "attachments", "file", "files", "blob", "blobs", "asset", "assets"}
_EXTERNAL_RESOURCE = re.compile(r"^(?:[A-Za-z]:[\\/]|(?:file|blob|s3|azure|gs|az|https?):|[/\\])", re.I)
_MANAGED_RESOURCE = re.compile(r"^(?:blob|s3|azure|gs|az):", re.I)
_IMAGE_REFERENCE = re.compile(r"!\[[^\]]*\]\([^)]*\)|<img\b[^>]*>", re.I)
_IMAGE_SOURCE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]*)|<img\b[^>]*?\bsrc\s*=\s*[\"']?([^\"'\s>]*)", re.I)
_WEB_URL = re.compile(r"^https?://[^\s/]+\S*$", re.I)
_HISTORY = ("_Tombstone", "_EdgeTombstone", "_DocTombstone")


def rejection_disposition(row, source, logical, endpoint_state):
    """완전한 소유자 범위 조회로 부재가 증명된 기각 이력만 개인 공간에 둔다."""
    a, b = row.get("a_id"), row.get("b_id")
    if (not isinstance(a, str) or not isinstance(b, str) or not a or not b or a == b
            or not a.startswith("concept:") or not b.startswith("concept:")
            or a != pkns.strip(a) or b != pkns.strip(b) or "|" in a or "|" in b
            or row.get("pair_key") != source + "|" + "|".join(sorted((a, b)))):
        return "unsupported_merge_rejection_key"
    states = {endpoint_state(ref) for ref in (a, b)}
    # 접미(끝점 상태·행 해시)는 리허설 리포트가 원본 id 없이 행 수와 원인을 세게 한다.
    unknown = "merge_rejection_topic_membership_unknown:" + "+".join(sorted(states)) + ":" + row_digest(row["pair_key"])
    if any(state.startswith("ambiguous") for state in states):
        return unknown
    if states == {"selected"}:
        return "move"
    if "missing" in states and states <= {"selected", "missing"}:
        return "retain"
    if "selected" in states:
        return "merge_rejection_history_not_supported"
    if "missing" in states:
        return unknown
    return "outside"


def row_digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def endpoint_ambiguity(matches):
    if len(matches) != 1:
        return "ambiguous_multiple"
    return "ambiguous_kind" if matches[0][0] != "Concept" else "ambiguous_owner"


def retained_edge_deletion(label, row, source):
    """선택 노드 사이 엣지 삭제 표식은 개인 소유 그대로 남기고 팀으로 복사하지 않는다.

    소유자 합성 키일 때만이다. 레거시 키는 팀이 같은 엣지를 지울 때 같은 행을 덮는다.
    근거: [[decision:topic-transfer-edge-history-and-foreign-rejection-endpoints-2026-10-16]]
    """
    return label == "_EdgeTombstone" and str(row.get("ekey") or "").startswith(source + pkns.SEP)


def tombstone_blocker(label, refs):
    return f"existing_tombstone_history_not_supported:{label}:{row_digest(chr(31).join(sorted(refs)))}"


def rejection_endpoint_state(snapshot, source, logical, ref):
    # 합성 키 없는 레거시 행은 이 소유자 것일 때만 끝점 후보다. 같은 id 의 남의 행은 무관하다.
    # 근거: [[decision:topic-transfer-edge-history-and-foreign-rejection-endpoints-2026-10-16]]
    matches = [(label, row) for label in _KINDS + ("Bridge",)
               for row in snapshot["nodes"][label]
               if row[node_table_specs()[label]["pk"]] == source + pkns.SEP + ref
               or (row[node_table_specs()[label]["pk"]] == ref and row.get("owner_id") == source)]
    if not matches:
        return "missing"
    if (len(matches) != 1 or matches[0][0] != "Concept"
            or matches[0][1].get("owner_id") != source):
        return endpoint_ambiguity(matches)
    return "selected" if ref in logical else "outside"


def deletion_references(label, row, source):
    fields = {"_Tombstone": ("node_id",), "_EdgeTombstone": ("src", "dst"),
              "_DocTombstone": ("doc_id",)}[label]
    refs = set()
    for field in fields:
        value = row.get(field)
        if not isinstance(value, str) or not value or (pkns.SEP in value and not value.startswith(source + pkns.SEP)):
            raise TransferRefused("unsupported_tombstone_identity")
        refs.add(pkns.strip(value))
    return refs


def retained_history_counts(snapshot, plan):
    logical = {pkns.strip(key) for _, key in plan.selected} | set(plan.documents)
    counts = dict.fromkeys(_HISTORY, 0)
    for label in _HISTORY:
        for row in snapshot["meta"][label]:
            if row.get("owner_id") != plan.source:
                continue
            try:
                refs = deletion_references(label, row, plan.source)
            except TransferRefused:
                continue
            if not refs & logical or retained_edge_deletion(label, row, plan.source):
                counts[label] += 1
    retained = sum(row.get("owner_id") == plan.source and rejection_disposition(
        row, plan.source, logical,
        lambda ref: rejection_endpoint_state(snapshot, plan.source, logical, ref)) == "retain"
        for row in snapshot["meta"]["_MergeRejection"])
    if retained:
        counts["_MergeRejection"] = retained
    return counts


def external_images(text):
    """이미지 참조가 있으면 (전부 외부 http(s)인가, 개수)를, 없으면 None 을 돌려준다.

    관리형·로컬·상대·data: 주소나 src 없는 태그가 하나라도 있으면 외부가 아니다.
    근거: [[decision:topic-transfer-plain-references-and-external-images-2026-10-16]]
    """
    found = _IMAGE_REFERENCE.findall(text)
    if not found:
        return None
    sources = []
    for ref in found:
        match = _IMAGE_SOURCE.match(ref)
        sources.append((match.group(1) or match.group(2) or "") if match else "")
    return all(_WEB_URL.match(src) for src in sources), len(found)


def _web_only(value):
    """첨부 키 아래 값이 전부 외부 http(s) 주소 문자열이면 참이다."""
    if isinstance(value, dict):
        return bool(value) and all(_web_only(v) for v in value.values())
    if isinstance(value, list):
        return bool(value) and all(_web_only(v) for v in value)
    return isinstance(value, str) and bool(_WEB_URL.match(value))


def attachment_kind(value):
    """첨부 키 값의 진단 분류. 리허설은 이 종류만 세고 값은 내보내지 않는다."""
    leaves = []
    def walk(v):
        if isinstance(v, dict):
            for item in v.values():
                walk(item)
        elif isinstance(v, list):
            for item in v:
                walk(item)
        else:
            leaves.append(v)
    walk(value)
    if leaves and all(isinstance(v, str) and v and not _MANAGED_RESOURCE.match(v)
                      and not v.lower().startswith("data:") for v in leaves):
        return "attachment_key_path"
    return "attachment_key_other"


def preserved_reference_counts(plan):
    """동의 화면에 보일 건수. 식별자는 내보내지 않는다(남의 id 일 수 있다)."""
    counts = {"external_references": 0, "external_attachments": 0}
    for item in plan.preserved:
        code = item.split(":", 1)[0]
        if code == "external_reference":
            counts["external_references"] += 1
        elif code == "external_attachment":
            counts["external_attachments"] += 1
    return counts


def referenced_ids(value):
    """Logical references covered by the lossless transfer boundary check."""
    if isinstance(value, dict):
        for key, val in value.items():
            compact = re.sub(r"[^a-z0-9]", "", key.lower())
            # 빈 후속 문서·참조 값에는 옮길 대상이 없다. 실제 id는 그대로 검사한다.
            if compact in ("docid", "supersededby", "nodeid", "ref") and isinstance(val, str) and val:
                yield pkns.strip(val)
            yield from referenced_ids(val)
    elif isinstance(value, list):
        for val in value:
            yield from referenced_ids(val)
    elif isinstance(value, str):
        yield from _REFERENCE.findall(value)


def _document_rows(rows, doc, *, ordered=False):
    if hasattr(rows, "where"):
        return rows.where(doc_id=doc, order_by="chunk_index" if ordered else "pk")
    matches = (r for r in rows if r["doc_id"] == doc)
    return sorted(matches, key=lambda r: r["chunk_index"]) if ordered else matches


def _row_by_key(rows, pk, key):
    if hasattr(rows, "get"):
        return rows.get(key)
    return next((r for r in rows if r[pk] == key), None)


def _validate_snapshot(snapshot: dict) -> set[str]:
    expected = {"schema_version", "nodes", "meta", "edges"}
    if set(snapshot) != expected:
        raise TransferRefused("unsupported_snapshot_fields:" + ",".join(sorted(set(snapshot) ^ expected)))
    specs = {**node_table_specs(), **meta_table_specs()}
    if set(snapshot["nodes"]) != set(node_table_specs()) or set(snapshot["meta"]) != set(meta_table_specs()):
        raise TransferRefused("incomplete_inventory")
    blocks = set()
    for label, rows in {**snapshot["nodes"], **snapshot["meta"]}.items():
        if not isinstance(rows, Sequence):
            raise TransferRefused("invalid_inventory_rows:" + label)
        wanted = set(specs[label]["cols"])
        for row in rows:
            if not isinstance(row, dict) or wanted - set(row):
                raise TransferRefused("incomplete_columns:" + label)
            if set(row) - wanted:
                blocks.add("incomplete_columns:" + label)
            if not isinstance(row[specs[label]["pk"]], str):
                raise TransferRefused("invalid_primary_key:" + label)
    relations = {(r["label"], r["from"], r["to"]): set(r["props"]) for r in rel_table_specs()}
    if not isinstance(snapshot["edges"], Sequence):
        raise TransferRefused("invalid_relationship_inventory")
    for edge in snapshot["edges"]:
        if not isinstance(edge, dict) or set(edge) != {"label", "from_label", "to_label", "from", "to", "props"}:
            raise TransferRefused("unsupported_relationship_fields")
        key = (edge["label"], edge["from_label"], edge["to_label"])
        if key not in relations or not isinstance(edge["props"], dict):
            raise TransferRefused("unsupported_relationship_schema")
        if set(edge["props"]) != relations[key]:
            blocks.add("unsupported_relationship_columns:" + edge["label"])
    return blocks


def _derived_topic_documents(snapshot, owner, within):
    """미러가 없는 문서도 기존 문서 목록의 주제 규칙으로 선택한다."""
    from .document_topic import ranked_topics
    concepts = {row["concept_id"]: row for row in snapshot["nodes"]["Concept"]
                if row["owner_id"] == owner}
    authored = {pkns.strip(key)[len("concept:doc."):]: row["domain"].strip()
                for key, row in concepts.items()
                if pkns.strip(key).startswith("concept:doc.") and row.get("domain")}
    owned = {row["doc_id"] for row in snapshot["meta"]["_DocMeta"] if row["owner_id"] == owner}
    chunks = {row["chunk_id"]: row["doc_id"] for row in snapshot["nodes"]["Chunk"]
              if row["owner_id"] in (owner, "") and row["doc_id"] in owned}
    counts = {}
    for edge in snapshot["edges"]:
        if (edge["label"], edge["from_label"], edge["to_label"]) != ("MENTIONS", "Chunk", "Concept"):
            continue
        doc = chunks.get(edge["from"])
        concept = concepts.get(edge["to"])
        domain = (concept.get("domain") or "").strip() if concept else ""
        if doc and domain:
            tally = counts.setdefault(doc, {})
            tally[domain] = tally.get(domain, 0) + 1
    return {doc for doc, tally in counts.items()
            if (topics := ranked_topics(tally, authored.get(doc, ""))) and within(topics[0])}


def _derived_topic_bridges(snapshot, owner, within):
    """좌표가 없는 소유 브리지는 모든 연결 끝점이 같은 선택 범위에 있어야 한다."""
    # 근거: [[record:bridge-layout-clump-2026-08-22]]
    endpoints = {(label, row[pk]): row for label, pk in (("Concept", "concept_id"),
                                                       ("Decision", "decision_id"))
                 for row in snapshot["nodes"][label]}
    candidates, refused = set(), set()
    for edge in snapshot["edges"]:
        if edge["label"] != "CONNECTS" or edge["from_label"] != "Bridge":
            continue
        key = edge["from"]
        candidates.add(key)
        endpoint = endpoints.get((edge["to_label"], edge["to"]))
        if endpoint is None or endpoint["owner_id"] != owner or not within(endpoint.get("domain")):
            refused.add(key)
    return candidates - refused


def plan_transfer(snapshot: dict, *, source: str, target: str, topic: str,
                  conventions_policy: str = "retain") -> Plan:
    """Select one authored topic tree. Dependencies outside it are blockers, not additions."""
    blocks = _validate_snapshot(snapshot)
    # 토픽 밖 id 참조와 외부 이미지는 막지 않고 원문 그대로 옮긴 뒤 건수만 공개한다.
    # 근거: [[decision:topic-transfer-plain-references-and-external-images-2026-10-16]]
    preserved = set()
    if conventions_policy not in ("retain", "move_original"):
        raise TransferRefused("invalid_conventions_policy")
    if not source.startswith("user:") or pkns.SEP in source or not source[5:]:
        raise TransferRefused("personal_source_required")
    try:
        if not target.startswith("team:") or str(UUID(target[5:])) != target[5:]:
            raise ValueError
    except ValueError:
        raise TransferRefused("new_team_owner_required") from None
    if not topic or topic != topic.strip("/") or any(p in ("", ".", "..") for p in topic.split("/")):
        raise TransferRefused("explicit_topic_required")
    if snapshot.get("schema_version") != SCHEMA_VERSION:
        raise TransferRefused("unsupported_schema_version")
    specs = {**node_table_specs(), **meta_table_specs()}
    if set(snapshot["nodes"]) != set(node_table_specs()) or set(snapshot["meta"]) != set(meta_table_specs()):
        raise TransferRefused("incomplete_inventory")
    selected = set()
    within = lambda d: isinstance(d, str) and (d == topic or d.startswith(topic + "/"))
    derived_bridges = _derived_topic_bridges(snapshot, source, within)
    allrows = {**snapshot["nodes"], **snapshot["meta"]}
    for label, rows in allrows.items():
        pk = specs[label]["pk"]
        ids = [r[pk] for r in rows]
        if len(set(ids)) != len(ids):
            blocks.add("duplicate_primary_key:" + label)
        for row in rows:
            if set(row) != set(specs[label]["cols"]):
                blocks.add("incomplete_columns:" + label)
            if row.get("owner_id") == target:
                blocks.add("target_not_empty")
            if row.get("owner_id") != source:
                continue
            if label in _KINDS and within(row.get("domain")):
                selected.add((label, row[pk]))
            if label == "Bridge" and (within(row.get("source_domain")) or within(row.get("target_domain"))):
                if within(row.get("source_domain")) and within(row.get("target_domain")):
                    selected.add((label, row[pk]))
                else:
                    blocks.add("bridge_crosses_topic:" + row[pk])
            elif (label == "Bridge" and not row.get("source_domain") and not row.get("target_domain")
                  and row[pk] in derived_bridges):
                selected.add((label, row[pk]))
    if not selected:
        blocks.add("empty_topic")
    # owner별 관리 데이터만 있는 대상도 빈 팀으로 취급하지 않는다.
    for row in snapshot["meta"]["_SyncMeta"]:
        if target in row["key"]:
            blocks.add("target_has_metadata")
    if any(r["owner_id"] == source for r in snapshot["nodes"]["Entity"]):
        blocks.add("entity_topic_membership_not_supported")
    docs = {pkns.strip(nid)[len("concept:doc."):] for label, nid in selected
            if label == "Concept" and pkns.strip(nid).startswith("concept:doc.")}
    docs.update(_derived_topic_documents(snapshot, source, within))
    metas = {r["doc_id"]: r for r in snapshot["meta"]["_DocMeta"]}
    for doc in docs:
        if any(r["doc_id"] == doc for r in snapshot["meta"]["_DocTombstone"]):
            # 메타데이터 없는 삭제 이력을 살아 있는 문서와의 충돌로 보고하지 않는다.
            code = "document_tombstone_conflicts_with_live_doc" if doc in metas else "existing_tombstone_history_not_supported"
            blocks.add(code + ":" + doc)
        if doc not in metas or metas[doc].get("owner_id") != source:
            blocks.add("document_owner_missing:" + doc)
            continue
        if (is_conventions_doc_id(doc) or metas[doc].get("origin") == "vinc:conventions") and conventions_policy != "move_original":
            blocks.add("owner_conventions_require_separate_decision:" + doc)
        successor = metas[doc].get("superseded_by")
        if successor and successor not in docs:
            blocks.add("external_document_successor:" + successor)
        chunks = _document_rows(snapshot["nodes"]["Chunk"], doc, ordered=True)
        digest = hashlib.sha256()
        chunk_keys = set()
        count = 0
        offset = 0
        for i, chunk in enumerate(chunks):
            count += 1
            chunk_keys.add(chunk["chunk_id"])
            digest.update((chunk["text"] or "").encode())
            if (chunk["owner_id"] != source or chunk["chunk_index"] != i
                    or chunk["char_start"] != offset or chunk["char_end"] != offset + len(chunk["text"] or "")):
                blocks.add("document_chunks_not_intact:" + doc)
            offset = chunk["char_end"]
        if count != metas[doc]["chunk_count"] or digest.hexdigest() != metas[doc]["content_hash"]:
            blocks.add("document_integrity_failed:" + doc)
        for label in ("Chunk", "ChunkVec"):
            for row in _document_rows(snapshot["nodes"][label], doc):
                if row["doc_id"] == doc:
                    if row["owner_id"] != source:
                        blocks.add("shared_document_owner:" + doc)
                    selected.add((label, row[specs[label]["pk"]]))
        selected.add(("_DocMeta", doc))
        for vector in _document_rows(snapshot["nodes"]["ChunkVec"], doc):
            if vector["doc_id"] == doc and vector["chunk_id"] not in chunk_keys:
                blocks.add("chunk_vector_join_mismatch:" + doc)
    logical = {pkns.strip(nid) for _, nid in selected}
    # Logical links have no relationship row. Moving their target must not
    # silently leave a different topic's text or structured references broken.
    for label, rows in allrows.items():
        if label not in _KINDS + ("Bridge", "Chunk", "_DocMeta"):
            continue
        for row in rows:
            if row.get("owner_id") != source:
                continue
            key = row[specs[label]["pk"]]
            props = row.get("props")
            fields = dict(row)
            if props:
                try:
                    fields["props"] = json.loads(props)
                except (TypeError, ValueError):
                    # Unclassified properties outside the scope can hide links.
                    blocks.add("opaque_props:" + key)
            refs = set(referenced_ids(fields))
            if (label, key) in selected:
                for ref in refs - (logical | docs):
                    preserved.add("external_reference:" + ref)
            elif refs & (logical | docs):
                blocks.add("incoming_reference:" + key)
    for edge in snapshot["edges"]:
        a, b = (edge["from_label"], edge["from"]), (edge["to_label"], edge["to"])
        if a not in selected and b not in selected:
            owned = any(r.get("owner_id") == source and r[specs[label]["pk"]] == key
                        for label, key in (a, b) for r in allrows[label])
            if owned and set(referenced_ids(edge["props"])) & (logical | docs):
                blocks.add("incoming_reference:relationship")
    for row in snapshot["nodes"]["NodeVec"]:
        if row["owner_id"] == source and row["node_id"] in logical:
            selected.add(("NodeVec", row["vec_id"]))
            if row["vec_id"] != source + "#" + row["node_id"]:
                blocks.add("unsupported_node_vector_key")
    for edge in snapshot["edges"]:
        a, b = (edge["from_label"], edge["from"]), (edge["to_label"], edge["to"])
        sa, sb = a in selected, b in selected
        if sa != sb:
            other = b if sa else a
            # Domain은 소유자 없는 구조 어휘다. 같은 토픽의 구조 관계만 유지한다.
            if other[0] != "Domain" or not within(other[1]) or edge["label"] not in ("IN_DOMAIN", "CROSSES"):
                blocks.add("boundary_relation:" + edge["label"] + ":" + pkns.strip(other[1]))
        if sa or sb:
            for ref in _REFERENCE.findall(str(edge["props"].get("evidence") or "")):
                if ref not in logical and ref not in docs:
                    preserved.add("external_reference:" + ref)
    for label, nid in tuple(selected):
        row = _row_by_key(allrows[label], specs[label]["pk"], nid)
        if "theme_id" in row and row["theme_id"]:
            blocks.add("owner_vocabulary_not_supported:" + nid)
        source_file = str(row.get("source_file") or "")
        # Chunk와 파생 Concept의 source_file은 출처 표시다. 경로 원문도 함께 보존한다.
        # 근거: [[vinc.canon.chunk-ssot §1]]
        if label not in ("Chunk", "Concept") and (_EXTERNAL_RESOURCE.match(source_file) or "/" in source_file or "\\" in source_file):
            blocks.add("external_source_file_not_supported:" + nid)
        if "props" in row and row["props"]:
            try:
                props = json.loads(row["props"])
            except (TypeError, ValueError):
                blocks.add("opaque_props:" + nid)
                props = {}
            def inspect(value):
                if isinstance(value, dict):
                    for key, val in value.items():
                        compact = re.sub(r"[^a-z0-9]", "", key.lower())
                        if (compact in _ATTACHMENTS or compact in (
                                "attachmenturl", "attachmentpath", "asseturl", "bloburi",
                                "filepath", "filepaths", "fileurl")) and val:
                            # 외부 http(s) 주소만 텍스트로 옮긴다. 관리형·로컬 첨부는 막는다.
                            if _web_only(val):
                                preserved.add("external_attachment:" + nid)
                            else:
                                blocks.add("external_attachments_not_supported:" + attachment_kind(val) + ":" + nid)
                        if compact in ("vocabulary", "themeid") and val:
                            blocks.add("owner_vocabulary_not_supported:" + nid)
                        if compact in ("graphmd", "conventions") and val:
                            blocks.add("owner_conventions_require_separate_decision:" + nid)
                        if compact in ("docid", "supersededby", "nodeid", "ref") and val and isinstance(val, str):
                            if val not in logical and val not in docs:
                                preserved.add("external_reference:" + val)
                        inspect(val)
                elif isinstance(value, list):
                    for val in value:
                        inspect(val)
                elif isinstance(value, str):
                    # 일반 URL·경로 문자열은 키 이름과 무관하게 원문 보존한다. 관리형 자원만 막는다.
                    if _MANAGED_RESOURCE.match(value):
                        blocks.add("external_resource_reference_not_supported:" + nid)
                    image = external_images(value)
                    if image:
                        if image[0]:
                            preserved.add("external_attachment:" + nid)
                        else:
                            blocks.add("external_attachments_not_supported:props_image:" + nid)
                    for ref in _REFERENCE.findall(value):
                        if ref not in logical and ref not in docs:
                            preserved.add("external_reference:" + ref)
            inspect(props)
        if label == "Chunk":
            image = external_images(row["text"] or "")
            if image:
                if image[0]:
                    preserved.add("external_attachment:" + nid)
                else:
                    blocks.add("external_attachments_not_supported:chunk_image:" + nid)
            for ref in _REFERENCE.findall(row["text"] or ""):
                if ref not in logical and ref not in docs:
                    preserved.add("external_reference:" + ref)
    for row in snapshot["meta"]["_SyncBase"]:
        if row["node_id"] == source + pkns.SEP + pkns.strip(row["node_id"]) and pkns.strip(row["node_id"]) in logical:
            selected.add(("_SyncBase", row["node_id"]))
    # 이력은 추측해서 버리거나 다른 토픽과 함께 가져오지 않는다.
    for row in snapshot["meta"]["_MergeRejection"]:
        if row["owner_id"] == source:
            refs = {pkns.strip(row["a_id"] or ""), pkns.strip(row["b_id"] or "")}
            disposition = rejection_disposition(row, source, logical,
                lambda ref: rejection_endpoint_state(snapshot, source, logical, ref))
            if disposition == "move":
                selected.add(("_MergeRejection", row["pair_key"]))
                for ref in set(referenced_ids(row["note"])) - (logical | docs):
                    preserved.add("external_reference:" + ref)
            elif disposition not in ("retain", "outside"):
                blocks.add(disposition)
            if disposition != "move" and set(referenced_ids(row["note"])) & (logical | docs):
                blocks.add("incoming_reference:merge_rejection")
    for label in _HISTORY:
        for row in snapshot["meta"][label]:
            if row.get("owner_id") == source:
                try:
                    refs = deletion_references(label, row, source)
                except TransferRefused as exc:
                    blocks.add(str(exc))
                    continue
                if refs & (logical | docs) and not retained_edge_deletion(label, row, source):
                    blocks.add(tombstone_blocker(label, refs & (logical | docs)))
    for label, nid in selected:
        if label in _KINDS + ("Bridge",) and not nid.startswith(source + pkns.SEP):
            blocks.add("namespaced_node_key_required:" + nid)
    conventions = [row for row in snapshot["meta"]["_DocMeta"]
                   if row["owner_id"] == source and (is_conventions_doc_id(row["doc_id"])
                                                     or row.get("origin") == "vinc:conventions")]
    if conventions_policy == "move_original" and any(row["doc_id"] in docs for row in conventions):
        if len(conventions) != 1 or not is_conventions_doc_id(conventions[0]["doc_id"]):
            blocks.add("ambiguous_owner_conventions")
    return Plan(source, target, topic, fingerprint(snapshot), tuple(sorted(selected)), tuple(sorted(docs)), tuple(sorted(blocks)), conventions_policy,
                tuple(sorted(preserved)))


def mapped_rejection_key(key: str, source: str, target: str) -> str:
    """기각쌍의 논리 끝점은 보존하고 소유자 접두만 바꾼다."""
    prefix = source + "|"
    if not key.startswith(prefix):
        raise TransferRefused("unsupported_merge_rejection_key")
    return target + "|" + key[len(prefix):]


def transformed_snapshot(snapshot: dict, plan: Plan, *, stamp: str) -> dict:
    """Pure transformation used by the synthetic transaction rehearsal, with no embeddings."""
    if not plan.ready:
        raise TransferRefused(";".join(plan.blockers))
    if fingerprint(snapshot) != plan.before_hash:
        raise TransferRefused("plan_stale")
    out = copy.deepcopy(snapshot)
    specs = {**node_table_specs(), **meta_table_specs()}
    selected, mapping = set(plan.selected), {}
    for label, nid in selected:
        if label == "NodeVec":
            row = next(r for r in out["nodes"][label] if r["vec_id"] == nid)
            new = plan.target + "#" + row["node_id"]
            if nid != plan.source + "#" + row["node_id"]:
                raise TransferRefused("unsupported_node_vector_key")
        elif label == "_MergeRejection":
            new = mapped_rejection_key(nid, plan.source, plan.target)
        else:
            new = plan.target + pkns.SEP + pkns.strip(nid) if nid.startswith(plan.source + pkns.SEP) else nid
        mapping[(label, nid)] = new
    seqs = {r["key"]: int(r["value"]) for r in out["meta"]["_SyncMeta"] if r["key"].startswith("seq:")}
    def seq(owner):
        key = "seq:" + owner
        seqs[key] = seqs.get(key, 0) + 1
        return seqs[key]
    for group in ("nodes", "meta"):
        for label, rows in out[group].items():
            pk = specs[label]["pk"]
            for row in rows:
                old = row[pk]
                if (label, old) not in selected:
                    continue
                row[pk] = mapping[(label, old)]
                if "owner_id" in row:
                    row["owner_id"] = plan.target
                if label == "ChunkVec" and row["chunk_id"].startswith(plan.source + pkns.SEP):
                    row["chunk_id"] = plan.target + pkns.SEP + pkns.strip(row["chunk_id"])
                if "sync_seq" in row:
                    row["sync_seq"] = seq(plan.target)
                    row["updated_at"] = stamp
                    if "rev" in row:
                        row["rev"] = int(row["rev"] or 0) + 1
                if label in _KINDS + ("Bridge",):
                    out["meta"]["_Tombstone"].append({"node_id": old, "label": label,
                        "owner_id": plan.source, "deleted_at": stamp, "rev": row.get("rev") or 1,
                        "sync_seq": seq(plan.source), "origin": "topic-transfer"})
                if label == "_DocMeta":
                    out["meta"]["_DocTombstone"].append({"doc_id": old, "owner_id": plan.source,
                        "deleted_at": stamp, "sync_seq": seq(plan.source)})
                marker = "topic-transfer:moved:" + plan.source + ":" + label + ":" + pkns.strip(old)
                out["meta"]["_SyncMeta"].append({"key": marker, "value": plan.target})
    for edge in out["edges"]:
        edge["from"] = mapping.get((edge["from_label"], edge["from"]), edge["from"])
        edge["to"] = mapping.get((edge["to_label"], edge["to"]), edge["to"])
    for key, value in seqs.items():
        rows = [r for r in out["meta"]["_SyncMeta"] if r["key"] == key]
        if rows:
            rows[0]["value"] = str(value)
        else:
            out["meta"]["_SyncMeta"].append({"key": key, "value": str(value)})
    for label, rows in {**out["nodes"], **out["meta"]}.items():
        pk = specs[label]["pk"]
        if len({r[pk] for r in rows}) != len(rows):
            raise TransferRefused("key_collision:" + label)
    return out


def _write_difference(store, before: dict, after: dict, *, fail_at=None, vocabulary_owner=None,
                      verify_snapshot=None):
    specs = {**node_table_specs(), **meta_table_specs()}
    # 전체 스토어를 지우지 않는다. 키가 바뀌는 선택 행만 새로 만들고 관계를 옮긴다.
    for group in ("nodes", "meta"):
        for label, rows in after[group].items():
            pk = specs[label]["pk"]
            old = {r[pk]: r for r in before[group][label]}
            for row in rows:
                if old.get(row[pk]) == row:
                    continue
                setters = ", ".join(f"n.{c}=${c}" for c in row if c != pk)
                store.run(f"MERGE (n:{label} {{{pk}:${pk}}})" + (" SET " + setters if setters else ""), row, raw=True)
    if fail_at == "nodes":
        raise RuntimeError("injected_after_nodes")
    old_edges = Counter(_canonical(e) for e in before["edges"])
    for edge in after["edges"]:
        encoded = _canonical(edge)
        if old_edges[encoded]:
            old_edges[encoded] -= 1
            continue
        sp, dp = specs[edge["from_label"]]["pk"], specs[edge["to_label"]]["pk"]
        props = ", ".join(f"{c}:${c}" for c in edge["props"])
        store.run(f"MATCH (a:{edge['from_label']}), (b:{edge['to_label']}) "
                  f"WHERE a.{sp}=$src AND b.{dp}=$dst CREATE (a)-[:{edge['label']}"
                  + (" {" + props + "}" if props else "") + "]->(b)",
                  {"src": edge["from"], "dst": edge["to"], **edge["props"]}, raw=True)
    if fail_at == "edges":
        raise RuntimeError("injected_after_edges")
    for group in ("nodes", "meta"):
        for label, rows in before[group].items():
            pk = specs[label]["pk"]
            keep = {r[pk] for r in after[group][label]}
            for row in rows:
                if row[pk] not in keep:
                    store.run(f"MATCH (n:{label}) WHERE n.{pk}=$key DETACH DELETE n", {"key": row[pk]}, raw=True)
    if fail_at == "delete":
        raise RuntimeError("injected_after_delete")
    observed = (verify_snapshot() if verify_snapshot is not None else
                read_snapshot(store, vocabulary_owner=vocabulary_owner))
    if fingerprint(observed) != fingerprint(after):
        raise TransferRefused("payload_verification_failed")


def _apply_rehearsal(store, plan: Plan, *, run_id: str, stamp: str, fail_at=None) -> str:
    """Private, unindexed synthetic executor. Not connected to REST, MCP or account.

    Production use requires the account reservation, write fences, all-writer move
    barriers, durable ACK, index/cache hooks and engine crash qualification.
    """
    _require_synthetic(store)
    if not run_id or len(run_id) > 120:
        raise TransferRefused("invalid_run_id")
    if any(r["index_name"] != "_PK" for r in store.run("CALL show_indexes() RETURN *", raw=True)):
        raise TransferRefused("indexed_store_not_supported_in_rehearsal")
    key = "topic-transfer:run:" + run_id
    args = _canonical([plan.source, plan.target, plan.topic, plan.before_hash])
    store.run("BEGIN TRANSACTION", raw=True)
    try:
        previous = store.run("MATCH (m:_SyncMeta) WHERE m.key=$key RETURN m.value AS value", {"key": key}, raw=True)
        if previous:
            journal = json.loads(previous[0]["value"])
            if journal["args"] != args:
                raise TransferRefused("run_id_reused")
            if journal.get("status") == "cancelled":
                raise TransferRefused("run_cancelled")
            current = read_snapshot(store)
            current["meta"]["_SyncMeta"] = [r for r in current["meta"]["_SyncMeta"] if r["key"] != key]
            if fingerprint(current) != journal["after_hash"]:
                raise TransferRefused("committed_payload_changed")
            store.run("COMMIT", raw=True)
            return "already_committed"
        before = read_snapshot(store)
        after = transformed_snapshot(before, plan, stamp=stamp)
        _write_difference(store, before, after, fail_at=fail_at)
        journal = _canonical({"args": args, "after_hash": fingerprint(after), "status": "committed"})
        store.run("CREATE (m:_SyncMeta {key:$key, value:$value})", {"key": key, "value": journal}, raw=True)
        if fail_at == "journal":
            raise RuntimeError("injected_before_commit")
        store.run("COMMIT", raw=True)
        return "committed"
    except BaseException:
        store.run("ROLLBACK", raw=True)
        raise


def _restore_rehearsal(store, before: dict, plan: Plan, *, run_id: str, stamp: str, fail_at=None):
    """Compensate an isolated synthetic commit before account activation.

    A changed store is refused. This is not a live sync recovery protocol: live
    recovery must publish new monotonic revisions under durable scope fences.
    """
    _require_synthetic(store)
    if any(r["index_name"] != "_PK" for r in store.run("CALL show_indexes() RETURN *", raw=True)):
        raise TransferRefused("indexed_store_not_supported_in_rehearsal")
    expected = transformed_snapshot(before, plan, stamp=stamp)
    key = "topic-transfer:run:" + run_id
    args = _canonical([plan.source, plan.target, plan.topic, plan.before_hash])
    store.run("BEGIN TRANSACTION", raw=True)
    try:
        current = read_snapshot(store)
        journals = [r for r in current["meta"]["_SyncMeta"] if r["key"] == key]
        if not journals or json.loads(journals[0]["value"])["args"] != args:
            raise TransferRefused("run_not_committed")
        without_journal = copy.deepcopy(current)
        without_journal["meta"]["_SyncMeta"] = [r for r in without_journal["meta"]["_SyncMeta"] if r["key"] != key]
        if json.loads(journals[0]["value"]).get("status") == "cancelled":
            if fingerprint(without_journal) != fingerprint(before):
                raise TransferRefused("restored_payload_changed")
            store.run("COMMIT", raw=True)
            return "already_restored"
        if fingerprint(without_journal) != fingerprint(expected):
            raise TransferRefused("compensation_source_changed")
        _write_difference(store, current, before, fail_at=fail_at)
        value = _canonical({"args": args, "status": "cancelled"})
        store.run("CREATE (m:_SyncMeta {key:$key, value:$value})", {"key": key, "value": value}, raw=True)
        store.run("COMMIT", raw=True)
        return "restored"
    except BaseException:
        store.run("ROLLBACK", raw=True)
        raise


def _require_synthetic(store):
    # 실험용 표식이 없는 일반 저장소에는 쓰기 트랜잭션을 열지 않는다.
    marker = store.run("MATCH (m:_SyncMeta) WHERE m.key='topic-transfer:synthetic' RETURN m.value AS value",
                       raw=True)
    if marker != [{"value": "tests-only"}]:
        raise TransferRefused("synthetic_store_required")
