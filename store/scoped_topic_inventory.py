"""Bounded, lossless topic inventory for the account-owned conversion protocol.

Only the prospective mutation closure is retained. Other source rows and
relations are inspected in bounded pages for references into that closure.
Unknown physical schema and incomplete scans fail before any graph mutation.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from . import pkns
from .embedded_schema import SCHEMA_VERSION, meta_table_specs, node_table_specs, rel_table_specs
from .topic_transfer import (
    MAX_INVENTORY_BYTES, MAX_INVENTORY_ROWS, TransferRefused, _KINDS,
    _canonical, check_inventory_budget, referenced_ids, verify_store_shape,
)

PAGE_ROWS = 128
PAGE_BYTES = 4 * 1024 * 1024
BATCH_KEYS = 32
SCAN_ROWS = 100_000
SCAN_SECONDS = 120


@dataclass(frozen=True)
class ScopedInventory:
    snapshot: dict
    blockers: tuple[str, ...]
    inspected_rows: int
    retained_rows: int
    retained_bytes: int


class _Reader:
    def __init__(self, store):
        self.store = store
        self.started = time.monotonic()
        self.inspected = 0

    def page(self, query, params=None):
        rows = self.store.run(query, params or None, raw=True)
        self.inspected += len(rows)
        if (len(rows) > PAGE_ROWS or len(_canonical(rows).encode()) > PAGE_BYTES
                or self.inspected > SCAN_ROWS or time.monotonic() - self.started > SCAN_SECONDS):
            raise TransferRefused("conversion_inventory_limit")
        return rows

    def scan(self, head, projection, order, params=None):
        # Most relationship tables have no rows. Avoid allocating a sorter for
        # a result that fits in one page; only paged scans need stable order.
        first = self.page(f"{head} RETURN {projection} LIMIT {PAGE_ROWS}", params)
        if len(first) < PAGE_ROWS:
            yield from first
            return
        offset = 0
        while True:
            rows = self.page(f"{head} RETURN {projection} ORDER BY {order} "
                             f"SKIP {offset} LIMIT {PAGE_ROWS}", params)
            yield from rows
            if len(rows) < PAGE_ROWS:
                return
            offset += len(rows)

    def one(self, query, params=None):
        rows = self.page(query + " LIMIT 2", params)
        if len(rows) > 1:
            raise TransferRefused("duplicate_primary_key")
        return rows[0] if rows else None

    def exists(self, query, params=None):
        return bool(self.page(query + " LIMIT 1", params))


def _node_projection(label, *, omit_embedding=False):
    specs = {**node_table_specs(), **meta_table_specs()}
    return ", ".join(f"n.{c} AS {c}" for c in specs[label]["cols"]
                     if not (omit_embedding and c == "embedding"))


def _batches(values):
    values = sorted(set(values))
    for offset in range(0, len(values), BATCH_KEYS):
        yield values[offset:offset + BATCH_KEYS]


def read_scoped_inventory(store, *, source: str, target: str, topic: str,
                          after: bool = False) -> ScopedInventory:
    """Read one owner/topic closure and prove its external dependencies boundedly.

    ``after`` selects the committed target closure for exact write verification
    and recovery. The ordinary mode selects the personal source before reserve.
    """
    verify_store_shape(store, vocabulary_owner=source)
    specs = {**node_table_specs(), **meta_table_specs()}
    reader = _Reader(store)
    owner = target if after else source
    result = {"schema_version": SCHEMA_VERSION,
              "nodes": {label: [] for label in node_table_specs()},
              "meta": {label: [] for label in meta_table_specs()}, "edges": []}
    blockers = set()
    retained = set()
    retained_bytes = 0

    def keep(label, row):
        nonlocal retained_bytes
        if row is None:
            raise TransferRefused("incomplete_inventory")
        pk = specs[label]["pk"]
        identity = (label, row[pk])
        if identity in retained:
            return
        retained.add(identity)
        result["meta" if label.startswith("_") else "nodes"][label].append(row)
        retained_bytes += len(_canonical(row).encode())
        if len(retained) + len(result["edges"]) > MAX_INVENTORY_ROWS or retained_bytes > MAX_INVENTORY_BYTES:
            raise TransferRefused("conversion_inventory_limit")

    def exact(label, key):
        pk = specs[label]["pk"]
        return reader.one(f"MATCH (n:{label}) WHERE n.{pk}=$key RETURN {_node_projection(label)}",
                          {"key": key})

    def matching(label, keys, *, ids_only=False):
        pk = specs[label]["pk"]
        projection = f"n.{pk} AS id" if ids_only else _node_projection(label)
        for batch in _batches(keys):
            yield from reader.page(f"MATCH (n:{label}) WHERE n.{pk} IN $keys "
                                   f"RETURN {projection} ORDER BY n.{pk}", {"keys": batch})

    def within(value):
        return isinstance(value, str) and (value == topic or value.startswith(topic + "/"))

    # Topic selection uses only coordinates, then fetches complete selected rows.
    for label in _KINDS + ("Bridge",):
        pk = specs[label]["pk"]
        candidates = []
        coordinate = (f"n.{pk} AS id, n.source_domain AS source_domain, "
                      "n.target_domain AS target_domain" if label == "Bridge" else
                      f"n.{pk} AS id, n.domain AS domain")
        for row in reader.scan(f"MATCH (n:{label}) WHERE n.owner_id=$owner",
                               coordinate, f"n.{pk}", {"owner": owner}):
            if label == "Bridge":
                left, right = within(row["source_domain"]), within(row["target_domain"])
                chosen = left and right
                if left != right and not after:
                    blockers.add("bridge_crosses_topic:" + row["id"])
            else:
                chosen = within(row["domain"])
            if chosen:
                candidates.append(row["id"])
                if len(candidates) > MAX_INVENTORY_ROWS:
                    raise TransferRefused("conversion_inventory_limit")
        found = 0
        for row in matching(label, candidates):
            keep(label, row)
            found += 1
        if found != len(candidates):
            raise TransferRefused("incomplete_inventory")

    selected = set(retained)
    docs = {pkns.strip(nid)[len("concept:doc."):] for label, nid in selected
            if label == "Concept" and pkns.strip(nid).startswith("concept:doc.")}
    logical = {pkns.strip(nid) for _, nid in selected}

    # Document identities are global, so inspect every row joined by doc_id,
    # including another owner's malformed or shared chunk/vector.
    for row in matching("_DocMeta", docs):
        keep("_DocMeta", row)
    for label in ("Chunk", "ChunkVec"):
        pk = specs[label]["pk"]
        for batch in _batches(docs):
            for row in reader.scan(f"MATCH (n:{label}) WHERE n.doc_id IN $docs",
                                   _node_projection(label), f"n.{pk}", {"docs": batch}):
                keep(label, row)
    for row in matching("_DocTombstone", docs):
        keep("_DocTombstone", row)

    node_vectors = []
    for row in reader.scan("MATCH (n:NodeVec) WHERE n.owner_id=$owner",
                           "n.vec_id AS id, n.node_id AS node_id", "n.vec_id", {"owner": owner}):
        if row["node_id"] in logical:
            node_vectors.append(row["id"])
            if len(node_vectors) > MAX_INVENTORY_ROWS:
                raise TransferRefused("conversion_inventory_limit")
    found = 0
    for row in matching("NodeVec", node_vectors):
        keep("NodeVec", row)
        found += 1
    if found != len(node_vectors):
        raise TransferRefused("incomplete_inventory")
    for row in matching("_SyncBase", (owner + pkns.SEP + logical_id for logical_id in logical)):
        keep("_SyncBase", row)

    # Sequence counters and movement markers are the only sync metadata that
    # the transform reads or writes. Protocol journals are handled separately.
    for key in ("seq:" + source, "seq:" + target):
        row = exact("_SyncMeta", key)
        if row is not None:
            keep("_SyncMeta", row)
    marker_keys = set()
    for label, key in tuple(retained):
        if label in ("Domain", "_SyncMeta", "_DocTombstone", "_Tombstone"):
            continue
        if after:
            if label == "NodeVec":
                old = source + "#" + next(r["node_id"] for r in result["nodes"][label]
                                          if r["vec_id"] == key)
            elif key.startswith(target + pkns.SEP):
                old = source + pkns.SEP + pkns.strip(key)
            else:
                old = key
        else:
            old = key
        marker_keys.add("topic-transfer:moved:" + source + ":" + label + ":" + pkns.strip(old))
    for row in matching("_SyncMeta", marker_keys):
        keep("_SyncMeta", row)

    if after:
        tombstone_keys = (source + pkns.SEP + pkns.strip(row[specs[label]["pk"]])
                          for label in _KINDS + ("Bridge",) for row in result["nodes"][label])
        for row in matching("_Tombstone", tombstone_keys):
            keep("_Tombstone", row)

    # Read all relations with an endpoint owned by this closure's owner.
    # Edges not touching selected nodes remain outside the snapshot, but their
    # logical evidence is checked for incoming links below.
    boundary_ids = set()
    for spec in rel_table_specs():
        label, src, dst = spec["label"], spec["from"], spec["to"]
        owners = [f"{alias}.owner_id=$owner" for alias, kind in (("a", src), ("b", dst))
                  if "owner_id" in specs[kind]["cols"]]
        if not owners:
            continue
        src_pk, dst_pk = specs[src]["pk"], specs[dst]["pk"]
        columns = [f"a.{src_pk} AS src", f"b.{dst_pk} AS dst"]
        columns += [f"r.{c} AS {c}" for c in spec["props"]]
        order = ", ".join([f"a.{src_pk}", f"b.{dst_pk}"] + [f"r.{c}" for c in spec["props"]])
        head = f"MATCH (a:{src})-[r:{label}]->(b:{dst}) WHERE {' OR '.join(owners)}"
        for row in reader.scan(head, ", ".join(columns), order, {"owner": owner}):
            a, b = (src, row.pop("src")), (dst, row.pop("dst"))
            if a in selected or b in selected or a in retained or b in retained:
                result["edges"].append({"label": label, "from_label": src, "to_label": dst,
                                        "from": a[1], "to": b[1], "props": row})
                retained_bytes += len(_canonical(result["edges"][-1]).encode())
                if len(retained) + len(result["edges"]) > MAX_INVENTORY_ROWS or retained_bytes > MAX_INVENTORY_BYTES:
                    raise TransferRefused("conversion_inventory_limit")
                for endpoint in (a, b):
                    if endpoint[0] == "Domain":
                        boundary_ids.add(endpoint[1])
            elif not after and set(referenced_ids(row)) & (logical | docs):
                blockers.add("incoming_reference:relationship")
    for name in sorted(boundary_ids):
        row = exact("Domain", name)
        if row is None:
            raise TransferRefused("incomplete_inventory")
        keep("Domain", row)

    if not after:
        # A namespaced destination may still be occupied by malformed foreign
        # data. MERGE would overwrite it, so reject every mapped PK collision.
        destinations = {}
        tombstone_keys = set()
        vector_nodes = {r["vec_id"]: r["node_id"] for r in result["nodes"]["NodeVec"]}
        for label, old in tuple(retained):
            if label in ("Domain", "_SyncMeta", "_DocTombstone", "_Tombstone"):
                continue
            if label == "NodeVec":
                new = target + "#" + vector_nodes[old]
            elif old.startswith(source + pkns.SEP):
                new = target + pkns.SEP + pkns.strip(old)
            else:
                new = old
            if new != old:
                destinations.setdefault(label, set()).add(new)
            if label in _KINDS + ("Bridge",):
                tombstone_keys.add(old)
        for label, keys in destinations.items():
            if any(matching(label, keys, ids_only=True)):
                blockers.add("key_collision:" + label)
        if any(matching("_Tombstone", tombstone_keys, ids_only=True)):
            blockers.add("existing_tombstone_history_not_supported")

        # Scan the source's other topics without retaining them. Opaque props
        # remain blockers because a hidden reference cannot be classified.
        for label in _KINDS + ("Bridge", "Chunk", "_DocMeta"):
            pk = specs[label]["pk"]
            for row in reader.scan(f"MATCH (n:{label}) WHERE n.owner_id=$source",
                                   _node_projection(label, omit_embedding=True), f"n.{pk}",
                                   {"source": source}):
                key = row[pk]
                if (label, key) in retained:
                    continue
                if row.get("props"):
                    try:
                        row["props"] = json.loads(row["props"])
                    except (TypeError, ValueError):
                        blockers.add("opaque_props:" + key)
                if set(referenced_ids(row)) & (logical | docs):
                    blockers.add("incoming_reference:" + key)

        for label in ("Entity",) + tuple(l for l in node_table_specs() if l not in ("Entity",) and
                                           "owner_id" in specs[l]["cols"]):
            if reader.exists(f"MATCH (n:{label}) WHERE n.owner_id=$target "
                             f"RETURN n.{specs[label]['pk']} AS id", {"target": target}):
                blockers.add("target_not_empty")
        if reader.exists("MATCH (n:Entity) WHERE n.owner_id=$source RETURN n.entity_id AS id",
                         {"source": source}):
            blockers.add("entity_topic_membership_not_supported")
        if reader.exists("MATCH (n:_SyncMeta) WHERE n.key CONTAINS $target RETURN n.key AS id",
                         {"target": target}):
            blockers.add("target_has_metadata")
        if reader.exists("MATCH (n:_SyncBase) WHERE n.node_id STARTS WITH $prefix "
                         "RETURN n.node_id AS id", {"prefix": target + pkns.SEP}):
            blockers.add("target_has_metadata")
        for label in ("_Tombstone", "_EdgeTombstone", "_DocTombstone"):
            if reader.exists(f"MATCH (n:{label}) WHERE n.owner_id=$source "
                             f"RETURN n.{specs[label]['pk']} AS id", {"source": source}):
                blockers.add("unclassified_tombstone_topic_history:" + label)
        for row in reader.scan("MATCH (n:_MergeRejection) WHERE n.owner_id=$source",
                               "n.pair_key AS id, n.a_id AS a_id, n.b_id AS b_id",
                               "n.pair_key", {"source": source}):
            refs = {pkns.strip(row["a_id"] or ""), pkns.strip(row["b_id"] or "")}
            if refs & logical:
                blockers.add("merge_rejection_history_not_supported")
            else:
                for ref in refs:
                    if not any((existing := exact(label, key)) is not None
                               and existing["owner_id"] == source
                               for label in _KINDS + ("Bridge",)
                               for key in (source + pkns.SEP + ref, ref)):
                        blockers.add("merge_rejection_topic_membership_unknown")
                        break
    else:
        # No new target-owned topic or metadata can appear while the durable
        # source/target fences are pending. Detect it before releasing them.
        for label, spec in {**node_table_specs(), **meta_table_specs()}.items():
            if "owner_id" not in spec["cols"]:
                continue
            for row in reader.scan(f"MATCH (n:{label}) WHERE n.owner_id=$target",
                                   f"n.{spec['pk']} AS id", f"n.{spec['pk']}", {"target": target}):
                if (label, row["id"]) not in retained:
                    raise TransferRefused("committed_payload_changed")
        for row in reader.scan("MATCH (n:_SyncMeta) WHERE n.key CONTAINS $target",
                               "n.key AS id", "n.key", {"target": target}):
            if ("_SyncMeta", row["id"]) not in retained:
                raise TransferRefused("committed_payload_changed")
        for row in reader.scan("MATCH (n:_SyncBase) WHERE n.node_id STARTS WITH $prefix",
                               "n.node_id AS id", "n.node_id", {"prefix": target + pkns.SEP}):
            if ("_SyncBase", row["id"]) not in retained:
                raise TransferRefused("committed_payload_changed")

    check_inventory_budget(result)
    count = len(result["edges"]) + sum(len(rows) for group in ("nodes", "meta")
                                       for rows in result[group].values())
    return ScopedInventory(result, tuple(sorted(blockers)), reader.inspected,
                           count, len(_canonical(result).encode()))
